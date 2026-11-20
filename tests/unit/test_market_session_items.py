"""Session items Market sends fit its own contract: a worker module (Trade) gets them validated.

Trades and order-book sessions built their first page's time range by hand without unit and
bounds; in-process consumers did not notice, Trade's worker refused every item ("Schema
validation failed") and showed no market price, so no order could be placed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator

from modules.market.backend.live_aux import AuxSession
from vocabulary.instruments import MarketInstrument, market_data_series

ROOT = Path(__file__).parents[2]


def test_a_trades_session_first_page_is_a_valid_session_item(tmp_path):
    instrument = MarketInstrument(venueId="gate", symbol="BTC/USDT:USDT", displaySymbol="BTC/USDT:USDT",
                                  marketType="swap", assetClass="crypto", base="BTC", quote="USDT", settle="USDT",
                                  contractOrientation="linear", contractSize="0.0001").wire_dict()
    series = market_data_series(MarketInstrument.from_wire(instrument), "trades")
    market = SimpleNamespace(source_ref=lambda source_id: {"sourceId": source_id, "name": "Gate"})
    session = AuxSession(SimpleNamespace(market=market), None, instrument, series, "production", 2, 0, "gate")
    session.trades = [{"time": "1794325062749000000", "price": "73313", "amount": "13", "side": "buy", "id": "1"},
                      {"time": "1794325062749000000", "price": "73313.1", "amount": "13", "side": "buy", "id": "2"}]
    page = asyncio.run(session.build_replace())

    # The generated contract carries every type the stream item uses.
    contract = json.loads((ROOT / "modules" / "market" / "public" / "contract.json").read_text(encoding="utf-8"))
    item_schema = {**contract["streams"]["session"]["item"], "$defs": contract["$defs"]}
    errors = list(Draft202012Validator(item_schema).iter_errors({"type": "replace", "page": page}))
    assert errors == []
    assert page["range"] == {"start": "1794325062749000000", "end": "1794325062749000001", "unit": "ns", "bounds": "[)"}


def test_a_read_finds_its_forming_bar_while_observation_sessions_run(tmp_path):
    """Order-book and trades sessions (Trade's) are keyed with their depth too; every data.read failed while one ran."""
    from modules.market.backend.app import Market
    from tests.fixtures.market.replay import ReplayEgress

    market = Market(None, tmp_path, egress=ReplayEgress(), sources=[])
    bars = SimpleNamespace(seriesId="vs1_bars")
    market.sessions.sessions[("vs1_book", "production", 20)] = SimpleNamespace(forming=None)
    market.sessions.sessions[("vs1_bars", "production")] = SimpleNamespace(forming={"time": "1"})
    assert market.sessions.forming(bars, "production") == {"time": "1"}
    assert market.sessions.forming(SimpleNamespace(seriesId="vs1_other"), "production") is None


def test_extension_bar_requires_boolean_closed():
    import pytest

    from modules.market.backend.common import MarketError
    from modules.market.backend.sources.official import from_wire_event

    event = {"type": "bar", "bar": {"time": "10"}, "closed": False}
    assert from_wire_event(event)["closed"] is False
    with pytest.raises(MarketError, match="boolean closed"):
        from_wire_event({**event, "closed": "false"})
