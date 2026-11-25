"""Exchange rates for spending limits: the European Central Bank's daily reference rates, fetched once a day.

A limit counts the month's spending in every billing currency, converted into the limit's currency at the latest rates
fetched. Rates are daily figures; the last ones fetched are the ones in force until the next day's arrive."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from decimal import Decimal, InvalidOperation

import httpx2
from talyx_sdk.network import (
    NetworkConfigurationError,
    NetworkRouteChanged,
    await_network_route,
    system_http_proxy_url,
)

# Frankfurter's free, keyless mirror of the ECB reference rates: one record per currency, each per 1 EUR.
SOURCE = "https://api.frankfurter.dev/v2/providers/ECB/rates"
STALE_AFTER = 24 * 3600
RETRY_AFTER = 3600
log = logging.getLogger(__name__)


class ExchangeRates:
    def __init__(self, path):
        self.path = path
        # {"date": the ECB's publication day, "fetchedAt": epoch seconds, "perEuro": {currency: units per 1 EUR}}
        self.latest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def convert(self, amounts, currency):
        """Amounts in several currencies as one amount in `currency`, and the currencies without a rate (then None)."""
        rates = self.latest["perEuro"] if self.latest is not None else {}
        total, missing = Decimal(0), set()
        for unit, amount in amounts.items():
            if unit == currency or amount == 0:
                total += amount
                continue
            absent = {code for code in (unit, currency) if code not in rates}
            if absent:
                missing |= absent
                continue
            total += amount * Decimal(rates[currency]) / Decimal(rates[unit])
        return (None if missing else total), sorted(missing)

    def store(self, date, per_euro, fetched_at):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = {"date": date, "fetchedAt": fetched_at, "perEuro": per_euro}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value), encoding="utf-8")
        temporary.replace(self.path)
        self.latest = value

    async def refresh(self):
        proxy = system_http_proxy_url()

        async def fetch():
            async with httpx2.AsyncClient(proxy=proxy, trust_env=False, verify=ssl.create_default_context(), timeout=30) as client:
                response = await client.get(SOURCE)
                response.raise_for_status()
                return response.json()

        records = await await_network_route(fetch, proxy)
        per_euro = {"EUR": "1"}
        for record in records:
            rate = Decimal(str(record["rate"]))
            if record["base"] != "EUR" or not rate.is_finite() or rate <= 0:
                raise ValueError(f"Unexpected ECB rate record {record!r}")
            per_euro[record["quote"]] = str(rate)
        if len(per_euro) == 1:
            raise ValueError("The ECB rates response is empty")
        self.store(max(record["date"] for record in records), per_euro, time.time())

    async def keep_fresh(self):
        """Fetch when there are no rates or they are a day old; after a failure, try again in an hour."""
        while True:
            age = None if self.latest is None else time.time() - self.latest["fetchedAt"]
            wait = STALE_AFTER - age if age is not None and age < STALE_AFTER else 0
            if wait == 0:
                try:
                    await self.refresh()
                    wait = STALE_AFTER
                except (httpx2.HTTPError, NetworkRouteChanged, NetworkConfigurationError, ValueError, KeyError, TypeError,
                        InvalidOperation) as error:
                    # A download from the network: the rates fetched before stay in force.
                    log.warning("Exchange rate refresh failed: %r", error)
                    wait = RETRY_AFTER
            await asyncio.sleep(max(1.0, wait))
