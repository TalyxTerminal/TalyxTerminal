"""The Market panel's private route answers a Market failure as the panel reads it (its code and words, a 4xx where
the user can act), not as a server error: before, every MarketError of the route was a bare 500."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from modules.market.backend import entry
from modules.market.backend.common import MarketError


def test_a_market_failure_reaches_the_panel_with_its_code_and_message(monkeypatch):
    async def call(value, _caller):
        raise MarketError("not_found", "Nothing stored for this series")
    monkeypatch.setattr(entry, "market", SimpleNamespace(panel=SimpleNamespace(call=call)))
    client = TestClient(entry.routes(None), raise_server_exceptions=False)
    response = client.post("/panel", json={"op": "detail"})
    assert response.status_code == 404
    assert response.json()["code"] == "not_found" and response.json()["message"] == "Nothing stored for this series"
