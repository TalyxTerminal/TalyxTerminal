"""Domain contracts have one definition and no external dependencies."""

from __future__ import annotations

import importlib
import json
import pkgutil
from pathlib import Path


def test_contracts_are_the_single_definition_for_host() -> None:
    """Instrument names have a single definition in vocabulary.instruments."""
    import vocabulary.instruments as contract_market
    import vocabulary.instruments as host_market

    assert host_market.MarketInstrument is contract_market.MarketInstrument
    assert host_market.MarketSeries is contract_market.MarketSeries


def test_contracts_import_nothing_heavier_than_stdlib() -> None:
    """Contracts cannot import pandas, NumPy or the host."""
    import contracts

    banned = {"pandas", "numpy", "host", "modules"}
    for module_info in pkgutil.walk_packages(contracts.__path__, prefix="contracts."):
        before = set(sys_modules := __import__("sys").modules)
        importlib.import_module(module_info.name)
        loaded = set(sys_modules) - before
        assert not (loaded & banned), f"{module_info.name} pulled in {loaded & banned}"


def test_market_identity_id_minting_survives_the_move() -> None:
    from vocabulary.identity import instrument_id
    from vocabulary.instruments import MarketInstrument

    instrument = MarketInstrument(
        venueId="gate",
        symbol="BTC/USDT",
        displaySymbol="BTC/USDT",
        marketType="spot",
    )
    assert instrument.instrumentId == instrument_id(instrument.canonical_dict(with_id=False))
    assert instrument.assetClass == "unknown"


def test_precious_metal_identity_roundtrips_through_vocabulary():
    from vocabulary.instruments import MarketInstrument
    from vocabulary.python.types import Instrument

    instrument = MarketInstrument(
        venueId="gate", symbol="XAU_USDT", displaySymbol="XAU/USDT",
        marketType="swap", assetClass="precious_metal", contractOrientation="linear",
    )
    wire = instrument.canonical_dict()
    assert Instrument.model_validate(wire).model_dump(mode="json")["assetClass"] == "precious_metal"


def test_quant_declares_no_trading_account_or_lp_surface():
    root = Path(__file__).resolve().parents[2] / "modules/quant"
    public = json.loads((root / "public/contract.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "talyx.module.json").read_text(encoding="utf-8"))
    assert not (root / "backend/private-contract.json").exists() and "privateContracts" not in manifest
    assert not {"list_accounts", "read_account", "read_account_fills", "read_account_strategies", "read_lp"} & public["actions"].keys()
    assert not {"account_filled", "account_order_rejected", "strategy_state_changed"} & public["events"].keys()
    assert {name for name, stream in public["streams"].items() if stream.get("trigger")} == {"research_facts", "library_facts"}


def test_market_identity_delegates_to_vocabulary_without_decimal_or_time_loss():
    from vocabulary.identity import instrument_id, series_id
    from vocabulary.instruments import MarketInstrument, ohlcv_series
    instrument = MarketInstrument(venueId="okx", symbol="TEST/USD-OPTION", displaySymbol="Test option",
        marketType="option", assetClass="crypto", contractOrientation="inverse",
        contractSize="0.000000000000000000001", strike="100000.000000000001", expiry="1800000000000000001")
    wire = instrument.canonical_dict()
    assert wire["instrumentId"] == instrument_id(wire)
    assert wire["contractSize"] == "0.000000000000000000001"
    assert wire["expiry"] == "1800000000000000001"
    assert MarketInstrument(**{key: value for key, value in wire.items() if key not in {"instrumentId", "identityVersion"}}).instrumentId == instrument.instrumentId
    series = ohlcv_series(instrument, "1m")
    assert series.seriesId == series_id(series.to_dict())
