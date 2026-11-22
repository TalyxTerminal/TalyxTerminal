"""Durable request accounting with actual monthly and group charges."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.callbacks import AsyncCallbackHandler
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from .context import CURRENT_USAGE

UNLIMITED = "unlimited"  # A budget policy value, never a monetary amount.

@dataclass(frozen=True)
class Price:
    """USD (or the named currency) per million tokens and per named search unit."""

    input: str
    output: str
    version: str
    currency: str = "USD"
    cache_read: str | None = None
    cache_write: str | None = None
    cache_write_5m: str | None = None
    cache_write_1h: str | None = None
    search: dict[str, str] | None = None
    multiplier: str = "1"

    def __post_init__(self):
        values = [self.input, self.output, self.cache_read, self.cache_write, self.cache_write_5m,
                  self.cache_write_1h, self.multiplier]
        values.extend(self.search.values() if self.search is not None else [])
        if not self.currency or not self.version or any(value is not None and
                (not Decimal(value).is_finite() or Decimal(value) < 0) for value in values):
            raise ValueError("Prices require finite non-negative values, a currency and a version")

    def estimate(self, usage, search_units):
        incoming = usage["input_tokens"]
        outgoing = usage["output_tokens"]
        details = usage["input_token_details"] if "input_token_details" in usage else {}
        cached = details["cache_read"] if "cache_read" in details else 0
        written = details["cache_creation"] if "cache_creation" in details else 0
        unpriced = []
        dimensions = [("cache_read", cached, self.cache_read)]
        split = [(field, details[field], price) for field, price in (
            ("ephemeral_5m_input_tokens", self.cache_write_5m), ("ephemeral_1h_input_tokens", self.cache_write_1h)) if field in details]
        dimensions.extend(split)
        dimensions.append(("cache_creation", written, self.cache_write))
        amount = Decimal(self.input) * (incoming - cached - written - sum(tokens for _, tokens, _ in split)) + Decimal(self.output) * outgoing
        for field, tokens, price in dimensions:
            if tokens:
                if price is None:
                    unpriced.append(field)
                else:
                    amount += Decimal(price) * tokens
        amount /= 1_000_000
        for unit, count in search_units.items():
            if count == 0:
                continue
            if self.search is None or unit not in self.search:
                unpriced.append(unit)
            else:
                amount += Decimal(self.search[unit]) * count
        return amount * Decimal(self.multiplier), unpriced



class UsageLedger:
    def __init__(self, path, *, timezone_name="Asia/Singapore"):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS requests (
              id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, model_id TEXT NOT NULL,
              started REAL NOT NULL, finished REAL, state TEXT NOT NULL,
              usage TEXT, search_units TEXT NOT NULL, price TEXT, currency TEXT,
              amount TEXT, unpriced TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS budget_notifications (month TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS request_context (
              request_id TEXT PRIMARY KEY REFERENCES requests(id), workspace_id TEXT, conversation_id TEXT,
              group_id TEXT, member_id TEXT, work_run_id TEXT, child_run_id TEXT,
              unattended INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS group_budgets (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, monthly_limit TEXT NOT NULL,
              currency TEXT NOT NULL, version INTEGER NOT NULL, paused_month TEXT, reason TEXT);
        """)
        self.db.execute("UPDATE requests SET state='unknown',finished=? WHERE state='started'", (time.time(),))
        self.db.execute("INSERT OR IGNORE INTO settings VALUES (1,?)", (json.dumps({
            "timezone": timezone_name, "monthlyLimit": None, "currency": "USD"}),))
        self.db.commit()
        self.lock = threading.RLock()

    def configure_group(self, group_id, workspace_id, *, monthly_limit, expected_version=0, currency=None):
        try:
            amount = Decimal(str(monthly_limit)) if monthly_limit is not None else None
            if amount is not None and (not amount.is_finite() or amount < 0):
                raise ValueError("Invalid group limit")
        except (ValueError, TypeError, ArithmeticError) as error:
            raise ContractError("invalid_input", t("Group limit must be a finite non-negative amount")) from error
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM group_budgets WHERE id=?", (group_id,)).fetchone()
            if expected_version != (row["version"] if row else 0) or row and row["workspace_id"] != workspace_id:
                raise ContractError("conflict", t("Group budget version or workspace changed"))
            unit = row["currency"] if row else currency or self.settings()["currency"]
            self.db.execute("INSERT OR REPLACE INTO group_budgets VALUES (?,?,?,?,?,NULL,NULL)",
                (group_id, workspace_id, str(amount) if amount is not None else UNLIMITED, unit, expected_version + 1))
        return self.group_status(group_id)

    def delete_groups(self, workspace_id, group_id=None):
        """A deleted bot's budget, or every bot budget of a deleted workspace."""
        with self.lock, self.db:
            self.db.execute("DELETE FROM group_budgets WHERE workspace_id=? AND (? IS NULL OR id=?)", (workspace_id, group_id, group_id))

    def run_costs(self, group_id, month):
        """A bot's priced runs in a month: by work run, its tokens and known amounts in each billing currency, plus any unpriced usage."""
        runs, zone = {}, ZoneInfo(self.settings()["timezone"])
        year, number = map(int, month.split("-"))
        start = datetime(year, number, 1, tzinfo=zone).timestamp()
        end = datetime(year + number // 12, number % 12 + 1, 1, tzinfo=zone).timestamp()
        with self.lock:
            for row in self.db.execute("SELECT r.amount,r.currency,r.unpriced,r.usage,c.work_run_id FROM requests r JOIN request_context c "
                                       "ON r.id=c.request_id WHERE c.group_id=? AND r.state='final' AND r.started>=? AND r.started<?",
                                       (group_id, start, end)):
                usage = json.loads(row["usage"])
                tokens, amounts, unpriced = runs.get(row["work_run_id"], (0, {}, False))
                if row["amount"] is not None:
                    currency = row["currency"]
                    amounts[currency] = amounts.get(currency, Decimal(0)) + Decimal(row["amount"])
                unpriced = unpriced or row["amount"] is None or bool(json.loads(row["unpriced"]))
                runs[row["work_run_id"]] = (tokens + usage["input_tokens"] + usage["output_tokens"], amounts, unpriced)
        return runs

    def month(self, now):
        """The Usage month (in its time zone) a moment falls in."""
        return self._month(now)

    def _month(self, now):
        return datetime.fromtimestamp(now, ZoneInfo(self.settings()["timezone"])).strftime("%Y-%m")

    def _totals(self, month, currency, *, group_id=None):
        spent = Decimal(0)
        sql = "SELECT r.* FROM requests r LEFT JOIN request_context c ON r.id=c.request_id WHERE r.currency=?"
        args = [currency]
        if group_id is not None:
            sql += " AND c.group_id=?"
            args.append(group_id)
        for row in self.db.execute(sql, args):
            if self._month(row["started"]) != month:
                continue
            if row["state"] == "final" and row["amount"] is not None:
                spent += Decimal(row["amount"])
        return spent

    def group_status(self, group_id):
        with self.lock:
            row = self.db.execute("SELECT * FROM group_budgets WHERE id=?", (group_id,)).fetchone()
            if row is None:
                raise ContractError("target_gone", t("Group budget is not configured"))
            month = self._month(time.time())
            spent = self._totals(month, row["currency"], group_id=group_id)
            return {"groupId": group_id, "workspaceId": row["workspace_id"], "version": row["version"],
                    "month": month, "currency": row["currency"], "monthlyLimit": None if row["monthly_limit"] == UNLIMITED else row["monthly_limit"],
                    "spent": str(spent),
                    "paused": row["paused_month"] == month, "reason": row["reason"] if row["paused_month"] == month else None}

    def month_spent(self):
        """This month's priced spend in the Usage currency, the figure the overall limit is checked against."""
        with self.lock:
            return self._totals(self._month(time.time()), self.settings()["currency"])

    def claim_limit_notification(self):
        with self.lock, self.db:
            current = self.settings()
            if current["monthlyLimit"] is None:
                return False
            month = self._month(time.time())
            if self._totals(month, current["currency"]) < Decimal(current["monthlyLimit"]):
                return False
            return bool(self.db.execute("INSERT OR IGNORE INTO budget_notifications VALUES (?)", (month,)).rowcount)

    def settings(self):
        with self.lock:
            return json.loads(self.db.execute("SELECT value FROM settings WHERE id=1").fetchone()[0])

    def configure(self, *, timezone_name, monthly_limit, currency="USD"):
        try:
            ZoneInfo(timezone_name)
        except (TypeError, ZoneInfoNotFoundError) as error:
            raise ContractError("invalid_input", t("Invalid usage timezone"), {"field": "timezone"}) from error
        try:
            if monthly_limit is not None and (not Decimal(str(monthly_limit)).is_finite() or Decimal(str(monthly_limit)) < 0):
                raise ValueError("Monthly limit must be a finite non-negative amount")
        except (ValueError, TypeError, ArithmeticError) as error:
            raise ContractError("invalid_input", t("Invalid usage settings"), {"field": "monthlyLimit"}) from error
        with self.lock, self.db:
            self.db.execute("UPDATE settings SET value=? WHERE id=1", (json.dumps({
                "timezone": timezone_name, "monthlyLimit": str(monthly_limit) if monthly_limit is not None else None,
                "currency": currency}),))
        return self.settings()

    def start(self, request_id, connection_id, model_id, *, price: Price | None, search_units=None, context=None):
        """Check settled charges and record attribution before transport dispatch."""
        context = CURRENT_USAGE.get() if context is None else context
        units = search_units if search_units is not None else {}
        now = time.time()
        failure = None
        with self.lock, self.db:
            existing = self.db.execute("SELECT connection_id,model_id FROM requests WHERE id=?", (request_id,)).fetchone()
            if existing is not None:
                if (existing["connection_id"], existing["model_id"]) != (connection_id, model_id):
                    raise ContractError("conflict", t("Request identity belongs to another model"))
                return False
            settings = self.settings()
            month = self._month(now)
            priced = price is not None
            group = None
            if context.group_id is not None:
                group = self.db.execute("SELECT * FROM group_budgets WHERE id=?", (context.group_id,)).fetchone()
                if group is None or group["workspace_id"] != context.workspace_id:
                    raise ContractError("permission_denied", t("Group usage requires its configured workspace budget"))
            if priced and group is not None and group["paused_month"] == month:
                failure = ContractError("budget_exceeded", t("This group's monthly budget is paused"), self.group_status(context.group_id))
            limits = [(None, settings["monthlyLimit"])] if settings["monthlyLimit"] is not None else []
            if group is not None and group["monthly_limit"] != UNLIMITED:
                limits.append((context.group_id, group["monthly_limit"]))
            for group_id, limit in limits if priced else ():
                spent = self._totals(month, settings["currency"], group_id=group_id)
                if spent >= Decimal(limit):
                    failure = ContractError("budget_exceeded", t("Monthly budget cannot admit the next request"),
                        {"groupId": group_id, "month": month, "spent": str(spent), "monthlyLimit": limit})
                    break
            if failure is not None and failure.code == "budget_exceeded" and group is not None and group["paused_month"] != month:
                self.db.execute("UPDATE group_budgets SET paused_month=?,reason='budget_exceeded' WHERE id=?", (month, context.group_id))
            if failure is None:
                self.db.execute("INSERT INTO requests VALUES (?,?,?,?,NULL,'started',NULL,?,?,?,NULL,'[]')",
                    (request_id, connection_id, model_id, now, json.dumps(units), json.dumps(asdict(price)) if price else None,
                     price.currency if price else None))
                self.db.execute("INSERT INTO request_context VALUES (?,?,?,?,?,?,?,?)",
                    (request_id, context.workspace_id, context.conversation_id, context.group_id,
                     context.member_id, context.work_run_id, context.child_run_id, context.unattended))
        if failure is not None:
            raise failure
        return True

    def finish(self, request_id, usage, *, search_units=None):
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if row is None:
                raise ContractError("target_gone", t("Provider request has not been started"))
            if row["state"] in {"final", "rejected"}:
                return False
            units = search_units if search_units is not None else json.loads(row["search_units"])
            price = Price(**json.loads(row["price"])) if row["price"] is not None else None
            amount, unpriced = price.estimate(usage, units) if price is not None else (None, list(units))
            context = self.db.execute("SELECT group_id,unattended FROM request_context WHERE request_id=?", (request_id,)).fetchone()
            self.db.execute("UPDATE requests SET state=?,finished=?,usage=?,search_units=?,amount=?,unpriced=? WHERE id=?",
                ("final", time.time(), json.dumps(usage), json.dumps(units), str(amount) if amount is not None else None,
                 json.dumps(unpriced), request_id))
            if context is not None and context["group_id"] is not None:
                group_id = context["group_id"]
                group = self.group_status(group_id)
                settings = self.settings()
                spent = self._totals(group["month"], settings["currency"])
                exhausted = (group["monthlyLimit"] is not None and Decimal(group["spent"]) >= Decimal(group["monthlyLimit"])
                             or settings["monthlyLimit"] is not None and spent >= Decimal(settings["monthlyLimit"]))
                if exhausted and not group["paused"]:
                    self.db.execute("UPDATE group_budgets SET paused_month=?,reason=? WHERE id=?",
                                    (group["month"], "budget_exceeded", group_id))
        return True

    def unknown(self, request_id):
        with self.lock, self.db:
            self.db.execute("UPDATE requests SET state='unknown',finished=? WHERE id=? AND state='started'",
                            (time.time(), request_id))

    def reject(self, request_id):
        with self.lock, self.db:
            self.db.execute("UPDATE requests SET state='rejected',finished=?,amount='0',search_units='{}',unpriced='[]' WHERE id=? AND state='started'",
                            (time.time(), request_id))

    def summary(self, *, group_by="day", month=None, group_id=None, workspace_id=None, child_run_id=None):
        if group_by not in {"day", "model", "workspace", "owner"}:
            raise ContractError("invalid_input", t("Choose a known usage grouping"))
        with self.lock:
            settings = self.settings()
            zone = ZoneInfo(settings["timezone"])
            groups = {}
            # Requests outside any bot group (chats, automations) group under the empty key.
            query = ("SELECT r.*, c.group_id AS context_group, c.workspace_id, c.conversation_id "
                     "FROM requests r LEFT JOIN request_context c ON r.id=c.request_id WHERE 1=1")
            params = []
            for field, value in (("group_id", group_id), ("workspace_id", workspace_id), ("child_run_id", child_run_id)):
                if value is not None:
                    query += f" AND c.{field}=?"
                    params.append(value)
            for row in self.db.execute(query + " ORDER BY started", params):
                date = datetime.fromtimestamp(row["started"], zone)
                if month is not None and date.strftime("%Y-%m") != month:
                    continue
                key = {"model": row["model_id"], "workspace": row["workspace_id"], "owner": row["context_group"] or row["conversation_id"],
                       "day": date.strftime("%Y-%m-%d")}[group_by] or ""
                if key not in groups:
                    groups[key] = {"key": key, "inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0,
                        "reasoningTokens": 0, "requests": 0, "unconfirmedRequests": 0, "tokenOnlyRequests": 0, "unpricedRequests": 0,
                        "amounts": {}, "unpricedSearchUnits": {}, "unpricedPriceDimensions": []}
                group = groups[key]
                group["requests"] += 1
                if row["state"] == "rejected":
                    continue
                if row["state"] != "final":
                    group["unconfirmedRequests"] += 1
                    continue
                usage = json.loads(row["usage"])
                group["inputTokens"] += usage["input_tokens"]
                group["outputTokens"] += usage["output_tokens"]
                details = usage["input_token_details"] if "input_token_details" in usage else {}
                group["cacheReadTokens"] += details["cache_read"] if "cache_read" in details else 0
                details = usage["output_token_details"] if "output_token_details" in usage else {}
                group["reasoningTokens"] += details["reasoning"] if "reasoning" in details else 0
                if row["amount"] is None:
                    group["tokenOnlyRequests"] += 1
                else:
                    amounts = group["amounts"]
                    amounts[row["currency"]] = str(Decimal(amounts[row["currency"]] if row["currency"] in amounts else "0") + Decimal(row["amount"]))
                if row["amount"] is None or json.loads(row["unpriced"]):
                    group["unpricedRequests"] += 1
                units = json.loads(row["search_units"])
                for unit in json.loads(row["unpriced"]):
                    if unit in units:
                        total = group["unpricedSearchUnits"]
                        total[unit] = (total[unit] if unit in total else 0) + units[unit]
                    elif unit not in group["unpricedPriceDimensions"]:
                        group["unpricedPriceDimensions"].append(unit)
            return {"settings": settings, "groups": list(groups.values())}

    def close(self):
        self.db.close()


class UsageCallback(AsyncCallbackHandler):
    """Put on model callbacks and/or inherited RunnableConfig callbacks; run_id deduplicates."""

    raise_error = True

    def __init__(self, ledger, connection_id, model_id, *, price, search_units=None,
                 before_request=None, context=None, provider_id=None):
        self.ledger, self.connection_id, self.model_id = ledger, connection_id, model_id
        self.price = price
        self.search_units = search_units if search_units is not None else {}
        self.before_request = before_request
        self.context = CURRENT_USAGE.get() if context is None else context
        self.provider_id = provider_id
        self.active = set()

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        if self.before_request is not None:
            self.before_request()
        self.ledger.start(str(run_id), self.connection_id, self.model_id, price=self.price,
                          search_units=self.search_units, context=self.context)
        self.active.add(str(run_id))

    async def on_llm_end(self, response, *, run_id, **kwargs):
        messages = [generation.message for batch in response.generations for generation in batch]
        usage = next((message.usage_metadata for message in messages if message.usage_metadata is not None), None)
        if usage is None:
            self.ledger.unknown(str(run_id))
            self.active.discard(str(run_id))
            return
        units = {key: value for key, value in self.search_units.items() if key in {"google_grounding_query", "google_grounding_request"}}
        anthropic_count = None
        if response.llm_output is not None and "usage" in response.llm_output:
            raw_usage = response.llm_output["usage"]
            if "server_tool_use" in raw_usage and raw_usage["server_tool_use"] is not None:
                observed = raw_usage["server_tool_use"]
                if "web_search_requests" in observed:
                    anthropic_count = observed["web_search_requests"]
                    units["anthropic_web_search_request"] = anthropic_count
        for message in messages:
            metadata = message.response_metadata
            if "usage" in metadata and "server_tool_use" in metadata["usage"] and metadata["usage"]["server_tool_use"] is not None:
                observed = metadata["usage"]["server_tool_use"]
                if "web_search_requests" in observed:
                    anthropic_count = observed["web_search_requests"]
                    units["anthropic_web_search_request"] = anthropic_count
            if "google_grounding_query" in units and "grounding_metadata" in metadata:
                grounding = metadata["grounding_metadata"]
                if "web_search_queries" in grounding and grounding["web_search_queries"] is not None:
                    units["google_grounding_query"] = len(grounding["web_search_queries"])
            for block in message.content_blocks:
                if block["type"] == "server_tool_call" and block["name"] == "web_search":
                    provider = self.provider_id or metadata.get("model_provider")
                    action = block["args"].get("type") if "args" in block else None
                    if provider == "xai":
                        unit = "xai_web_search_unconfirmed_action"
                    elif provider == "openai":
                        unit = "openai_web_search_call" if action == "search" else "openai_web_search_unconfirmed_action" if action is None else None
                    else:
                        unit = "anthropic_web_search_request" if provider == "anthropic" and anthropic_count is None else None
                    if unit is not None:
                        units[unit] = (units[unit] if unit in units else 0) + 1
            if self.provider_id == "xai" and "rawUsage" in metadata and metadata["rawUsage"] is not None:
                raw = metadata["rawUsage"]
                if "server_side_tool_usage_details" in raw and "web_search_calls" in raw["server_side_tool_usage_details"]:
                    units.pop("xai_web_search_unconfirmed_action", None)
                    units["xai_web_search_call"] = raw["server_side_tool_usage_details"]["web_search_calls"]
                usage = {**usage, "provider_raw": raw}
        self.ledger.finish(str(run_id), usage, search_units=units)
        self.active.discard(str(run_id))

    async def on_llm_error(self, error, *, run_id, **kwargs):
        # Here, not at the top: the providers package imports usage.
        from ..providers.errors import request_rejected
        if request_rejected(error):
            self.ledger.reject(str(run_id))
        else:
            self.ledger.unknown(str(run_id))
        self.active.discard(str(run_id))

    def abandon(self):
        for request_id in self.active:
            self.ledger.unknown(request_id)
        self.active.clear()
