"""A shared real SQLite ledger covers model calls, concurrent requests and restart."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from talyx_sdk import ContractError

from modules.agent.backend.usage import Price, UsageCallback, UsageContext, UsageLedger


def test_run_costs_keep_currencies_separate(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.sqlite")
    budget = ledger.configure_group("g", "w", monthly_limit=None)
    context = UsageContext(workspace_id="w", group_id="g", work_run_id="run")
    for currency, rate in [("USD", "1"), ("CNY", "7")]:
        ledger.start(currency, "c", "m", price=Price(rate, "0", "v", currency), context=context)
        ledger.finish(currency, {"input_tokens": 1_000_000, "output_tokens": 0})
    ledger.start("no-price", "c", "m", price=None, context=context)
    ledger.finish("no-price", {"input_tokens": 2, "output_tokens": 0})
    assert ledger.run_costs("g", budget["month"]) == {"run": (2_000_002, {"USD": Decimal(1), "CNY": Decimal(7)}, True)}
    ledger.configure(timezone_name="UTC", monthly_limit=None, currency="CNY")
    updated = ledger.configure_group("g", "w", monthly_limit="5", expected_version=budget["version"])
    # The limit stays in USD; without rates its spending is unknown, with them CNY counts converted (7 CNY = 1 USD).
    assert updated["currency"] == "USD" and updated["spent"] is None and updated["missingRates"] == ["CNY", "USD"]
    ledger.rates.store("2026-11-25", {"EUR": "1", "USD": "1", "CNY": "7"}, 0)
    assert ledger.group_status("g")["spent"] == "2"
    ledger.close()


def test_a_limit_counts_spending_in_every_currency_converted(tmp_path):
    """A bot limited at 10 USD whose replies are priced in CNY: 80 CNY at 7.2 CNY a dollar is 11.11 USD, so the next
    request is refused and the bot paused. The overall limit in CNY counts USD spending the same way."""
    ledger = UsageLedger(tmp_path / "usage.sqlite")
    ledger.rates.store("2026-11-25", {"EUR": "1", "USD": "1", "CNY": "7.2"}, 0)
    ledger.configure_group("g", "w", monthly_limit="10")
    ledger.configure(timezone_name="UTC", monthly_limit=None, currency="CNY")
    context = UsageContext(workspace_id="w", group_id="g", work_run_id="run")
    ledger.start("cny", "c", "m", price=Price("40", "0", "v", "CNY"), context=context)
    ledger.finish("cny", {"input_tokens": 1_000_000, "output_tokens": 0})   # 40 CNY: 5.56 USD
    assert ledger.start("next", "c", "m", price=Price("40", "0", "v", "CNY"), context=context)
    ledger.finish("next", {"input_tokens": 1_000_000, "output_tokens": 0})  # 80 CNY: 11.11 USD
    assert Decimal(ledger.group_status("g")["spent"]).quantize(Decimal("0.01")) == Decimal("11.11")
    with pytest.raises(ContractError) as refused:
        ledger.start("after", "c", "m", price=Price("1", "0", "v", "USD"), context=context)
    assert refused.value.code == "budget_exceeded" and ledger.group_status("g")["paused"]
    ledger.configure(timezone_name="UTC", monthly_limit="80", currency="CNY")
    with pytest.raises(ContractError) as overall:
        ledger.start("chat", "c", "m", price=Price("1", "0", "v", "USD"))
    assert overall.value.code == "budget_exceeded"
    ledger.close()


def test_a_limit_without_an_exchange_rate_refuses_rather_than_not_count(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.sqlite")
    ledger.configure_group("g", "w", monthly_limit="10")
    context = UsageContext(workspace_id="w", group_id="g", work_run_id="run")
    ledger.start("cny", "c", "m", price=Price("1", "0", "v", "CNY"), context=context)
    ledger.finish("cny", {"input_tokens": 1_000_000, "output_tokens": 0})
    with pytest.raises(ContractError) as refused:
        ledger.start("next", "c", "m", price=Price("1", "0", "v", "USD"), context=context)
    assert refused.value.code == "exchange_rate_missing" and refused.value.details["missingRates"] == ["CNY", "USD"]
    ledger.rates.store("2026-11-25", {"EUR": "1", "USD": "1", "CNY": "7"}, 0)
    assert ledger.start("next", "c", "m", price=Price("1", "0", "v", "USD"), context=context)
    ledger.close()


def test_price_cache_reasoning_search_and_unknown_prices(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.sqlite")
    price = Price(input="2", output="8", cache_read="0.2", cache_write="2.5", version="price-v1",
                  search={"openai_web_search_call": "0.01"})
    ledger.start("known", "connection", "model", price=price)
    usage = {"input_tokens": 1000, "output_tokens": 100,
             "input_token_details": {"cache_read": 600, "cache_creation": 100},
             "output_token_details": {"reasoning": 50}}
    ledger.finish("known", usage, search_units={"openai_web_search_call": 2, "unpriced_search": 1})
    ledger.start("no-price", "local", "unknown", price=None)
    ledger.finish("no-price", {"input_tokens": 2, "output_tokens": 3})
    summary = {group["key"]: group for group in ledger.summary(group_by="model")["groups"]}
    assert Decimal(summary["model"]["amounts"]["USD"]) == Decimal("0.02177")
    assert summary["model"]["reasoningTokens"] == 50
    assert summary["model"]["unpricedSearchUnits"] == {"unpriced_search": 1}
    assert summary["unknown"]["tokenOnlyRequests"] == 1 and summary["unknown"]["amounts"] == {}
    ledger.close()


def test_callbacks_cover_inherited_subcalls_and_refuse_before_model_dispatch(tmp_path):
    async def scenario():
        ledger = UsageLedger(tmp_path / "usage.sqlite")
        price = Price(input="1", output="1", version="test")
        callback = UsageCallback(ledger, "connection", "model", price=price)
        model = FakeMessagesListChatModel(responses=[AIMessage(content="ok", usage_metadata={
            "input_tokens": 12, "output_tokens": 4, "total_tokens": 16})], callbacks=[callback])
        # Inheriting the same handler through RunnableConfig doesn't double-count a request.
        await model.ainvoke([HumanMessage(content="one")], config={"callbacks": [callback]})
        await model.ainvoke([HumanMessage(content="subagent or summarizer")], config={"callbacks": [callback]})
        summary = ledger.summary(group_by="model")["groups"][0]
        assert summary["requests"] == 2 and summary["inputTokens"] == 24
        ledger.configure(timezone_name="Asia/Singapore", monthly_limit="0")
        with pytest.raises(ContractError) as rejected:
            await model.ainvoke([HumanMessage(content="must not run")])
        assert rejected.value.code == "budget_exceeded"
        assert ledger.summary(group_by="model")["groups"][0]["requests"] == 2
        ledger.close()
    asyncio.run(scenario())


def test_search_billing_uses_provider_dimensions_and_actions(tmp_path):
    async def scenario():
        ledger = UsageLedger(tmp_path / "usage.sqlite")
        callback = UsageCallback(ledger, "connection", "model", price=None)
        usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
        message = AIMessage(content=[{"type": "web_search_call", "id": action, "action": {"type": action}}
            for action in ("search", "open_page", "find_in_page")], usage_metadata=usage,
            response_metadata={"model_provider": "openai"})
        model = FakeMessagesListChatModel(responses=[message], callbacks=[callback])
        await model.ainvoke("search")
        row = ledger.db.execute("SELECT search_units FROM requests").fetchone()
        import json
        assert json.loads(row[0]) == {"openai_web_search_call": 1}
        ledger.close()
    asyncio.run(scenario())


def test_group_month_uses_usage_timezone_at_calendar_boundary(tmp_path, monkeypatch):
    import modules.agent.backend.usage.ledger as accounting
    clock = [datetime.fromisoformat("2026-10-31T15:59:00+00:00").timestamp()]
    monkeypatch.setattr(accounting.time, "time", lambda: clock[0])
    ledger = UsageLedger(tmp_path / "usage.sqlite", timezone_name="Asia/Singapore")
    ledger.configure_group("g", "w", monthly_limit="0.01")
    context = UsageContext(workspace_id="w", group_id="g", unattended=True)
    price = Price(input="1", output="1", version="v")
    ledger.start("oct", "c", "m", price=price, context=context)
    ledger.finish("oct", {"input_tokens": 10_000, "output_tokens": 0})
    assert ledger.group_status("g")["month"] == "2026-10" and ledger.group_status("g")["paused"]
    clock[0] += 60
    assert ledger.group_status("g")["month"] == "2026-11" and not ledger.group_status("g")["paused"]
    ledger.start("nov", "c", "m", price=price, context=context)
    assert ledger.group_status("g")["spent"] == "0"
    ledger.close()


def test_parallel_requests_settle_without_reserving_and_stop_next_dispatch(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.sqlite")
    ledger.configure_group("g", "w", monthly_limit="0.5")
    context = UsageContext(workspace_id="w", group_id="g", unattended=True)
    price = Price(input="1", output="1", version="v")
    with ThreadPoolExecutor(max_workers=4) as workers:
        assert all(workers.map(lambda i: ledger.start(str(i), "c", "m", price=price, context=context), range(4)))
    assert ledger.group_status("g")["spent"] == "0"
    ledger.finish("0", {"input_tokens": 600_000, "output_tokens": 0})   # over the limit: the bot is paused
    with pytest.raises(ContractError, match="monthly budget is paused"):
        ledger.start("next", "c", "m", price=price, context=context)
    for i in range(1, 4):
        ledger.finish(str(i), {"input_tokens": 100_000, "output_tokens": 0})
    assert ledger.group_status("g")["spent"] == "0.9" and ledger.group_status("g")["paused"]
    assert ledger.finish("0", {"input_tokens": 1, "output_tokens": 0}) is False
    ledger.close()


def test_unpriced_unattended_and_unknown_requests_do_not_block(tmp_path):
    path = tmp_path / "usage.sqlite"
    ledger = UsageLedger(path)
    ledger.configure_group("g", "w", monthly_limit="1")
    context = UsageContext(workspace_id="w", group_id="g", child_run_id="child", unattended=True)
    ledger.start("no-price", "c", "m", price=None, context=context)
    ledger.finish("no-price", {"input_tokens": 500, "output_tokens": 50})
    ledger.start("partial", "c", "m", price=Price(input="1", output="2", version="v"), context=context)
    ledger.finish("partial", {"input_tokens": 1000, "output_tokens": 100, "input_token_details": {"cache_read": 800}}, search_units={"missing": 2})
    ledger.start("interrupted", "c", "m", price=None, context=context)
    assert ledger.group_status("g")["spent"] == "0.0004"
    assert not ledger.group_status("g")["paused"]
    summary = ledger.summary()["groups"][0]
    assert summary["unpricedRequests"] == 2 and summary["inputTokens"] == 1500
    assert ledger.db.execute("SELECT child_run_id FROM request_context WHERE request_id='partial'").fetchone()[0] == "child"
    ledger.close()
    ledger = UsageLedger(path)
    assert ledger.db.execute("SELECT state FROM requests WHERE id='interrupted'").fetchone()[0] == "unknown"
    assert ledger.start("after-restart", "c", "m", price=None, context=context)
    ledger.close()


def test_global_limit_uses_settled_amount_for_groups_and_conversations(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.sqlite")
    ledger.configure(timezone_name="UTC", monthly_limit="0.1")
    ledger.configure_group("g", "w", monthly_limit=None)
    price = Price(input="1", output="1", version="v")
    ledger.start("ordinary", "c", "m", price=price)
    ledger.finish("ordinary", {"input_tokens": 100_000, "output_tokens": 0})
    context = UsageContext(workspace_id="w", group_id="g", unattended=True)
    assert ledger.start("unpriced", "c", "m", price=None, context=context)
    with pytest.raises(ContractError, match="Monthly budget"):
        ledger.start("group", "c", "m", price=price, context=context)
    assert ledger.group_status("g")["paused"]
    ledger.close()


def test_anthropic_cache_durations_are_disjoint_from_generic_cache_creation():
    # LangChain reports generic cache_creation=0 when it supplies duration-specific counts.
    usage = {"input_tokens": 1000, "output_tokens": 100,
             "input_token_details": {"cache_creation": 0, "ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": 0}}
    priced = Price(input="1", output="2", cache_write_5m="3", version="v")
    assert priced.estimate(usage, {"unused_search": 0}) == (Decimal("0.0016"), [])
    missing = Price(input="1", output="2", version="v")
    assert missing.estimate(usage, {}) == (Decimal("0.001"), ["ephemeral_5m_input_tokens"])


def test_dollar_cap_counts_inflight_replies_and_notifies_once_across_restart(tmp_path):
    path = tmp_path / "cap.sqlite"
    ledger = UsageLedger(path)
    ledger.configure(timezone_name="UTC", monthly_limit="1.00")
    price = Price(input="1", output="2", version="test")
    for identity in ("first","inflight"):
        ledger.start(identity, "c", "m", price=price)
    ledger.finish("first", {"input_tokens":800_000, "output_tokens":100_000})
    assert ledger.month_spent() == (Decimal("1.00"), [])
    with pytest.raises(ContractError, match="Monthly budget"):
        ledger.start("next", "c", "m", price=price)
    assert ledger.claim_limit_notification()
    ledger.finish("inflight", {"input_tokens":1000,"output_tokens":0})
    assert ledger.month_spent() == (Decimal("1.001"), [])
    ledger.close()
    ledger = UsageLedger(path)
    assert not ledger.claim_limit_notification()
    assert ledger.start("no_price", "c", "other", price=None)
    ledger.finish("no_price", {"input_tokens":10,"output_tokens":3})
    assert ledger.month_spent() == (Decimal("1.001"), [])
    ledger.close()

