"""Watchlists: a workspace's lists in sections, every change on the workspace stream, and the
list of common symbols a new workspace starts with."""
import asyncio

import pytest
from talyx_sdk import ContractError

from modules.chart.backend.app import ChartApp
from modules.chart.backend.wire import WatchlistCreate, WatchlistId, WatchlistUpdate
from tests.unit.chart.fake_market import BTC, ETH, FakeMarket


async def next_item(stream):
    return await asyncio.wait_for(anext(stream), 5)


def section(section_id, *instruments, name=""):
    return {"id": section_id, "name": name, "members": [{"instrument": item} for item in instruments]}


def test_a_new_workspace_starts_with_common_symbols_once(tmp_path):
    async def main():
        app = ChartApp(FakeMarket(), tmp_path / "data", tmp_path / "indicators")
        stream = app.workspace_stream("w1")
        assert (await next_item(stream))["watchlists"] == []
        item = await next_item(stream)
        assert item["type"] == "watchlist"
        default = item["value"]
        # what Market resolves goes in (the stand-in knows OKX BTC and ETH only)
        assert [(part["name"], [member["instrument"]["instrumentId"] for member in part["members"]])
                for part in default["sections"]] == [("Crypto", [BTC["instrumentId"], ETH["instrumentId"]]),
                                                     ("Stocks", [])]
        again = app.workspace_stream("w1")
        assert (await next_item(again))["watchlists"] == [default]
        await app.delete_watchlist("w1", WatchlistId(watchlistId=default["id"]))
        assert await next_item(stream) == {"type": "watchlist_removed", "id": default["id"]}
        later = app.workspace_stream("w1")
        assert (await next_item(later))["watchlists"] == []
        assert app.watchlists.seeding == {} and app.watchlists.all("w1") == []  # deleting it does not bring it back
        await app.close()
    asyncio.run(main())


def test_watchlists_change_at_their_revision_and_go_out_on_the_stream(tmp_path):
    async def main():
        app = ChartApp(FakeMarket(), tmp_path / "data", tmp_path / "indicators")
        app.store.put_object("seeded", {"id": "w1"}, "w1")  # no default list in this test
        stream = app.workspace_stream("w1")
        await next_item(stream)
        created = await app.create_watchlist("w1", WatchlistCreate(name="Majors", sections=[section("a", BTC)]))
        assert created["revision"] == 1 and (await next_item(stream)) == {"type": "watchlist", "value": created}

        moved = await app.update_watchlist("w1", WatchlistUpdate(
            watchlistId=created["id"], revision=1, sections=[section("a", ETH, BTC, name="Top")]))
        assert moved["revision"] == 2 and moved["name"] == "Majors"
        assert [m["instrument"]["instrumentId"] for m in moved["sections"][0]["members"]] == \
            [ETH["instrumentId"], BTC["instrumentId"]]
        assert (await next_item(stream))["value"] == moved

        with pytest.raises(ContractError) as stale:
            await app.update_watchlist("w1", WatchlistUpdate(watchlistId=created["id"], revision=1, name="Old"))
        assert stale.value.code == "conflict" and stale.value.details == {"revision": 2}
        with pytest.raises(ContractError) as twice:
            await app.update_watchlist("w1", WatchlistUpdate(
                watchlistId=created["id"], revision=2, sections=[section("a", BTC), section("b", BTC)]))
        assert twice.value.code == "invalid_input"
        many = [{**BTC, "instrumentId": f"x{index}"} for index in range(201)]
        with pytest.raises(ContractError) as full:
            await app.create_watchlist("w1", WatchlistCreate(name="Big", sections=[section("a", *many)]))
        assert full.value.code == "invalid_input" and "200" in full.value.message

        # another workspace sees none of it
        with pytest.raises(ContractError) as elsewhere:
            await app.delete_watchlist("w2", WatchlistId(watchlistId=created["id"]))
        assert elsewhere.value.code == "not_found"
        assert app.snapshot("w1")["watchlists"] == [moved] and app.snapshot("w2")["watchlists"] == []
        await app.close()
    asyncio.run(main())
