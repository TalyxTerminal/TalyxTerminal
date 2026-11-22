"""Market 2.0 trading terms and professional materials (build criterion 32), replaying recorded data."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import jsonschema
import numpy as np

from modules.market.backend.app import Market
from modules.market.backend.common import ns_of_date
from tests.fixtures.market.replay import ReplayEgress
from vocabulary.instruments import MarketInstrument, ohlcv_series

FIXTURES = Path(__file__).parents[1] / "fixtures" / "market"
PUBLIC = Path(__file__).parents[2] / "modules" / "market" / "public"
YAHOO = ("yfinance", "research")
NASDAQ = ("nasdaqtrader", "symbol-directory")
STOCKS = ["MSFT", "META", "V", "MA", "KO", "IBM"]


def run(coro):
    return asyncio.run(coro)


def stock_schema():
    contract = json.loads((PUBLIC / "contract.json").read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator({"$ref": "#/$defs/StockExecutionMaterial", "$defs": contract["$defs"]})


def test_stock_terms_freeze_raw_and_adjusted_prices_with_the_dividend_step(tmp_path):
    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress(YAHOO, NASDAQ))
        await market.start(load_catalogs=False)
        await market.instruments.load("nasdaqtrader", "US", "listings")
        try:
            records = [(await market.instruments.resolve({"text": code, "market": "US"}))["instrument"]
                       for code in STOCKS]
            series = [ohlcv_series(MarketInstrument.from_wire(r), "1d", adjustment="split_dividend").to_dict()
                      for r in records]
            static = await market.terms.read({"instruments": records})
            assert [item["stock"]["sessions"] for item in static["items"]] == [[]] * 6
            result = await market.terms.read({"instruments": records, "series": series,
                                              "start": str(ns_of_date("2024-01-02")),
                                              "end": str(ns_of_date("2025-01-01"))})
            assert result["asOf"].isdigit()
            validator = stock_schema()
            for code, item in zip(STOCKS, result["items"], strict=True):
                # Every one gets terms; none is refused.
                assert set(item["unavailable"]) == {"fees"}, (code, item["unavailable"])
                assert (item["quantityUnit"], item["minQuantity"], item["priceIncrement"]) == ("shares", "1", "0.01")
                stock = item["stock"]
                validator.validate(stock)
                assert len(stock["sessions"]) == 252, code
                summer = next(row for row in stock["sessions"] if row["sessionDate"] == "2024-06-03")
                assert summer["openAt"] == str(ns_of_date("2024-06-03") + (13 * 3600 + 30 * 60) * 10**9)
                # The first 2024 NYSE session opened at 09:30 EST (14:30 UTC).
                assert stock["sessions"][0]["openAt"] == str(ns_of_date("2024-01-02") + (14 * 3600 + 30 * 60) * 10**9)
                assert all(s["openAt"].isdigit() and s["closeAt"].isdigit() for s in stock["sessions"])
            msft = {s["sessionDate"]: s for s in result["items"][0]["stock"]["sessions"]}
            # The reference price is the raw (unadjusted) close of the day before the ex-date.
            assert msft["2024-08-14"]["rawOHLC"][3] == "416.86"
            dividend = next(a for a in result["items"][0]["stock"]["corporateActions"] if a["exDate"] == "2024-08-15")
            assert dividend["actionType"] == "dividend" and dividend["amount"] == 0.75
            assert dividend["sourceRecord"]["referenceClose"] == "416.86"
            assert dividend["sourceRecord"]["referenceDate"] == "2024-08-14"
            step = Decimal(1) - Decimal("0.75") / Decimal("416.86")
            assert Decimal(dividend["sourceRecord"]["dividendFactor"]) == step
            # The adjusted coordinate moves by exactly that step across the ex-date.
            ratio = Decimal(msft["2024-08-14"]["priceMap"]["scale"]) / Decimal(msft["2024-08-15"]["priceMap"]["scale"])
            assert abs(ratio - step) < Decimal("1e-12")
            for session in (msft["2024-08-14"], msft["2024-08-15"]):
                scale = Decimal(session["priceMap"]["scale"])
                for raw, adjusted in zip(session["rawOHLC"], session["adjustedOHLC"], strict=True):
                    assert abs(Decimal(raw) * scale - Decimal(adjusted)) <= Decimal("0.00005")
            # The coordinate after the last dividend in the window is not 1: later dividends are known.
            assert Decimal(msft["2024-11-20"]["priceMap"]["scale"]) < 1
        finally:
            await market.close()

    run(scenario())


def _instants_to_ns(value):
    if isinstance(value, dict):
        if value.get("precision") == "instant":
            return {**value, "value": str(int(value["value"]) * 1_000_000)}
        return {key: _instants_to_ns(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_instants_to_ns(item) for item in value]
    return value


def test_professional_list_and_read_match_the_materials_before_the_rename(tmp_path):
    before = json.loads((FIXTURES / "professional-before-rename.json").read_text(encoding="utf-8"))

    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress())
        store = market.professional.store
        for name in ("quotes", "terms"):
            given = before["inputs"][name]
            content = json.dumps(_instants_to_ns(json.loads(given["content"]))).encode()
            material, rows = store.prepare(content, given["file"], _instants_to_ns(given["descriptor"]))
            store.commit(material, rows, content)
        listed = await market.professional.list({})
        assert len(listed["materials"]) == len(before["list"]["materials"]) == 2
        old_by_id = {m["materialId"]: m for m in before["list"]["materials"]}
        for material in listed["materials"]:
            old = _instants_to_ns(old_by_id[material["materialId"]])
            assert material["acquiredAt"].isdigit() and len(material["acquiredAt"]) == 19
            # The file bytes changed with the time encoding, so their hashes and the version do too.
            same = {"version", "acquiredAt", "files"}
            assert {k: v for k, v in material.items() if k not in same} == {k: v for k, v in old.items() if k not in same}
            assert [{k: f[k] for k in ("role", "filename", "rowCount", "format")} for f in material["files"]] == \
                [{k: f[k] for k in ("role", "filename", "rowCount", "format")} for f in old["files"]]
            read = await market.professional.read({"materialId": material["materialId"],
                                                   "version": material["version"], "limit": 200})
            old_read = next(r for r in before["read"] if r["material"]["materialId"] == material["materialId"])
            assert read["rows"] == _instants_to_ns(old_read["rows"])
            assert read["coverage"] == _instants_to_ns(old_read["coverage"])
            assert (read["hasMore"], read["nextAfter"]) == (old_read["hasMore"], old_read["nextAfter"])
        quotes = next(m for m in listed["materials"] if m["kind"] == "quote_set")
        page = await market.professional.read({"materialId": quotes["materialId"], "version": quotes["version"],
                                               "limit": 1})
        assert page["rows"][0]["observedAt"] == {"precision": "instant", "value": "1451606400000000000"}
        second = await market.professional.read({"materialId": quotes["materialId"], "version": quotes["version"],
                                                 "limit": 1, "after": page["nextAfter"]})
        assert second["rows"][0]["quoteId"] == "usd-1"
        # The export hands a material version over whole: its record and every row.
        exported = await market.exports.export({"selections": [{"professionalMaterial": {
            "materialId": quotes["materialId"], "version": quotes["version"]}}],
            "destination": {"folder": str(tmp_path / "out")}})
        (file,) = exported["files"]
        assert file["content"] == f"professional:{quotes['materialId']}@{quotes['version']}" and file["rows"] == 3
        written = json.loads(Path(file["path"]).read_text(encoding="utf-8"))
        assert written["material"] == quotes and written["rows"] == (await market.professional.read(
            {"materialId": quotes["materialId"], "version": quotes["version"], "limit": 200}))["rows"]

    run(scenario())


def test_stock_price_steps_follow_the_price():
    """US Rule 612 and HKEX's spread table (phase 2, 2026-08-03); a price on a band's bound takes the lower band."""
    from modules.market.backend.terms import tick
    day = "2026-11-13"
    assert [str(tick("US", price, day)) for price in (0.85, 0.9999, 1.0, 245.5)] == ["0.0001", "0.0001", "0.01", "0.01"]
    assert str(tick("US", 245.5, "2028-03-01")) == "0.01"  # past the SEC relief: still a step, not a refusal
    hk = {0.25: "0.001", 0.255: "0.005", 8.40: "0.005", 10.0: "0.005", 10.02: "0.01", 20.0: "0.01", 49.98: "0.02",
          50.0: "0.02", 99.95: "0.05", 100.0: "0.05", 150.1: "0.1", 386.6: "0.2", 512.0: "0.5", 1500.0: "1"}
    assert {price: str(tick("HK", price, day)) for price in hk} == hk


def test_stock_price_increment_is_the_step_at_the_latest_close(tmp_path):
    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress(NASDAQ))
        await market.start(load_catalogs=False)
        await market.instruments.load("nasdaqtrader", "US", "listings")
        market.instruments.ingest([
            {"type": "listing", "market": "HK", "code": code, "name": name, "assetClass": asset, "currency": "HKD",
             "calendarId": "XHKG"}
            for code, name, asset in (("00700.HK", "TENCENT", "equity"), ("00005.HK", "HSBC", "equity"),
                                      ("01398.HK", "ICBC", "equity"), ("02800.HK", "TRACKER FUND", "fund"))], "test")
        market.terms.board_lots.write("HK", {"date": "2026-11-13", "listings": {}})   # the HKEX list read, with none of these lots
        try:
            def store(record, close):
                series = ohlcv_series(MarketInstrument.from_wire(record), "1d", adjustment="raw")
                key = market.data.plan(record, series, None).key
                day = ns_of_date("2026-11-13")
                market.store.write(key, {"time": np.array([day]), "close_time": np.array([day + 8 * 3600 * 10**9]),
                                         **{name: np.array([close]) for name in ("open", "high", "low", "close")},
                                         "volume": np.array([1000.0])},
                                   fetched=[(day, day + 86_400 * 10**9)], source="yfinance", revision=1)
            records = {}
            for code in ("AAPL", "00700.HK", "00005.HK", "01398.HK", "02800.HK"):
                records[code] = (await market.instruments.resolve({"text": code}))["instrument"]
            for code, close in (("AAPL", 0.85), ("00005.HK", 8.40), ("00700.HK", 512.0), ("02800.HK", 25.0)):
                store(records[code], close)
            items = (await market.terms.read({"instruments": list(records.values())}))["items"]
            got = {code: item["priceIncrement"] for code, item in zip(records, items, strict=True)}
            assert got == {"AAPL": "0.0001", "00700.HK": "0.5", "00005.HK": "0.005", "01398.HK": None,
                           "02800.HK": None}
            reasons = {code: item["unavailable"].get("priceIncrement") for code, item in zip(records, items, strict=True)}
            assert "no daily close is stored" in reasons["01398.HK"]
            assert "exchange traded products" in reasons["02800.HK"]
            assert reasons["AAPL"] is None and reasons["00700.HK"] is None
        finally:
            await market.close()

    run(scenario())


def test_cn_etf_tick_and_stamp_duty(tmp_path):
    market = Market(None, tmp_path, sources=[])
    for code, asset, tick, tax in [("510300.SH", "fund", "0.001", 0), ("600519.SH", "equity", "0.01", 500)]:
        id_, = market.identity.upsert_listings("CN", [{"code": code, "assetClass": asset}], source="fixture")
        terms = run(market.terms.read({"instruments": [market.identity.record(id_)]}))["items"][0]
        assert terms["priceIncrement"] == tick
        taxes = terms["stock"]["taxes"]
        assert sum(Decimal(row["sellRate"]) * 1_000_000 for row in taxes if row["effectiveUntil"] is None) == tax
