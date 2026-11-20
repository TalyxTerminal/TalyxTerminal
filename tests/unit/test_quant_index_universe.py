"""Historical selection retains member identity and discloses missing-price exclusions."""
import asyncio
from types import SimpleNamespace

import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend.index_universe import active, check_prices, freeze
from modules.quant.backend.object_store import check_data
from tests.unit.quant_runtime_fakes import adapt

SELECTION = {'instrument': {'instrumentId': 'index', 'displaySymbol': '^GSPC', 'symbol': '^GSPC', 'venueId': 'cboe'}}
BTC, ETH = {'instrumentId': 'BTC', 'displaySymbol': 'BTC'}, {'instrumentId': 'ETH', 'displaySymbol': 'ETH'}


def fixture(unmapped=False):
    async def read(args):
        assert args['asOf'] and args['fill'] == 'none'
        return {"columns":[{"name":c} for c in ("instrumentId","symbol","start","end","knownAt")],
                "rows":[["BTC","BTC","1970-01-01T00:00:00Z","1970-01-01T00:00:00.009Z","1970-01-01"],
                        [None if unmapped else "ETH","ETH","1970-01-01T00:00:00.010Z",None,"1970-01-01"]],
                "meta":[],"unmapped":int(unmapped),"nextCursor":None,"note":None}
    async def get(args):
        return {"items":[{"status":"found","symbol":item["displaySymbol"],"instrument":item} for item in (BTC,ETH) if item["instrumentId"] in args["symbols"]],"nextCursor":None}
    async def call(name,args):
        assert name == "market.fetch"
        assert args["symbols"] == ["index"] if args["data"][0]["kind"] == "members" else args["index"] == {"symbol":"index"}
        return {'taskId': 'members'}
    async def task(args):
        return {'state': 'completed', 'sequence': 1, 'result': {'failures': []}, 'error': None, 'events': []}
    return adapt(SimpleNamespace(require=lambda name: SimpleNamespace(read=read, get=get),
        actions=SimpleNamespace(call=call), platform=SimpleNamespace(tasks=SimpleNamespace(get=task))))


def test_union_preserves_removed_member_and_inclusive_boundary():
    result = asyncio.run(freeze(fixture(), SELECTION, 0, 20))
    assert result['instruments'] == [BTC, ETH]
    assert active(result, 'BTC', 9) and not active(result, 'BTC', 10)
    assert active(result, 'ETH', 10) and not active(result, 'ETH', 9)


def test_unmapped_historical_asset_is_reported_without_shrinking_the_universe():
    index = asyncio.run(freeze(fixture(True), SELECTION, 0, 20))
    assert index['intervals'][1]['members'][0]['assetId'] == 'unmapped:ETH:1970-01-01T00:00:00.010Z'
    assert index['exclusions'][0] == {'assetId': 'unmapped:ETH:1970-01-01T00:00:00.010Z', 'instrumentId': None, 'symbol': 'ETH', 'since': 10, 'until': 20,
        'kind': 'unmapped', 'reason': 'The free source has no prices for this past member'}
    assert not active(index, 'ETH', 15)


def test_data_settings_accept_one_fixed_index_selection():
    check_data({'index': SELECTION, 'timeframe': '1h', 'since': '2025-01-01', 'until': '2025-02-01'}, 'strategy')
    with pytest.raises(ContractError, match='one way'):
        check_data({'index': SELECTION, 'universe': 'code'}, 'factor')
    with pytest.raises(ContractError, match='instrument'):
        check_data({'index': {'materialId': 'm', 'poolId': 'p'}}, 'factor')


def test_q_data_masks_nonmembers_and_q_index_reads_the_frozen_material(monkeypatch):
    from modules.quant import talyx_quant as q
    index = asyncio.run(freeze(fixture(), SELECTION, 0, 20))
    monkeypatch.setattr(q, '_index_bound', False)
    monkeypatch.setattr(q._Run, 'invocation', {'research': {'info': {'index': index}}})
    assert q.index_members(SELECTION['instrument']['instrumentId'], '1970-01-01T00:00:00.009Z')['instrumentId'].tolist() == ['BTC']
    assert q.index_members(SELECTION['instrument']['instrumentId'], '1970-01-01T00:00:00.010Z')['instrumentId'].tolist() == ['ETH']
    with pytest.raises(ValueError, match='frozen'):
        q.index_members('different', '1970-01-01T00:00:00.010Z')
    data = q.ResearchData(pd.DataFrame({'column': ['BTC', 'ETH'], 'label': [0, 0], 'close': [100, 10], 'isMember': [True, False]}), {})
    assert data.close['BTC'].iloc[0] == 100 and pd.isna(data.close['ETH'].iloc[0])


def test_price_check_discloses_only_active_member_holes():
    index = asyncio.run(freeze(fixture(), SELECTION, 0, 20))
    series = {'timeframe': '1h', 'sessionScope': 'all'}
    btc = {'instrument': {**BTC, 'calendarId': '24_7'}, 'series': series}
    eth = {'instrument': {**ETH, 'calendarId': '24_7'}, 'series': series}
    calendar = [(0, 10), (10, 20)]
    check_prices(index, btc, pd.DataFrame({'time': [0]}), 30, calendar)
    check_prices(index, eth, pd.DataFrame({'time': [0]}), 30, calendar)
    assert len(index['exclusions']) == 1
    assert index['exclusions'][0]['assetId'] == 'ETH'
    assert index['exclusions'][0]['since'] == 10
    assert not active(index, 'ETH', 20)


def test_missing_price_recovers_at_next_available_session_close():
    index = asyncio.run(freeze(fixture(), SELECTION, 0, 25))
    selection = {'instrument': {**ETH, 'calendarId': '24_7'}, 'series': {'timeframe': '1h', 'sessionScope': 'all'}}
    check_prices(index, selection, pd.DataFrame({'time': [10, 20]}), 30, [(10, 12), (15, 17), (20, 22)])
    assert active(index, 'ETH', 12)
    assert not active(index, 'ETH', 17)
    assert active(index, 'ETH', 22)


def test_partial_replay_price_check_never_marks_earlier_unrequested_sessions_missing():
    index = asyncio.run(freeze(fixture(), SELECTION, 0, 25))
    selection = {'instrument': {**ETH, 'calendarId': '24_7'}, 'series': {'timeframe': '1h', 'sessionScope': 'all'}}
    check_prices(index, selection, pd.DataFrame({'time': [20]}), 22, [(10, 12), (15, 17), (20, 22)], since=20, until=22)
    assert index['exclusions'] == []


def test_report_publishes_excluded_members_and_periods(tmp_path):
    import json

    from modules.quant.native.publish import Publisher
    index = asyncio.run(freeze(fixture(True), SELECTION, 0, 20))
    publisher = Publisher({'output': str(tmp_path), 'runId': 'r', 'research': {'info': {'index': index}}})
    result = json.loads((tmp_path / 'result-index.json').read_text(encoding='utf8'))
    assert result['dataLimitations']['exclusions'] == index['exclusions']
    assert publisher.index['sections'][0]['id'] == 'index-data-limitations'
    assert pd.read_parquet(tmp_path / 'data/index-exclusions.parquet')['assetId'].tolist() == ['unmapped:ETH:1970-01-01T00:00:00.010Z']



def test_index_publication_cannot_reintroduce_an_unpriced_signal(monkeypatch):
    from modules.quant import talyx_quant as q
    bars = pd.DataFrame({'column':['GAP','GAP'],'label':[0,1],'close':[1.0,float('nan')],'isMember':[True,True]})
    monkeypatch.setattr(q._Run,'data',q.ResearchData(bars,{}))
    monkeypatch.setattr(q._Run,'invocation',{'research':{'info':{'index':{}}}})
    monkeypatch.setattr(q._Run,'mode','values')
    monkeypatch.setattr(q._Run,'publications',[])
    values = pd.DataFrame({'datetime':[0,1],'instrument':['GAP','GAP'],'field':['score','score'],'value':[1.0,99.0]})
    q._capture('prediction','prediction',values)
    assert q._Run.publications[0]['values']['datetime'].tolist() == [0]


def test_a_runs_record_keeps_counts_and_its_page_reads_the_lists_from_info_json(tmp_path):
    """The record of an index run keeps the index's counts, not its members or exclusions (megabytes for the S&P 500, parsed by every query
    over the runs); one run's page reads the names' sources and bars from info.json, each stock's engine evidence left out."""
    import json

    from modules.quant.backend.index_universe import summary
    from modules.quant.backend.object_data import public_summary
    index = asyncio.run(freeze(fixture(True), SELECTION, 0, 20))
    kept = summary(index)
    assert kept['memberCount'] == 2 and kept['mappedCount'] == 1 and kept['excludedCount'] == 1
    assert 'exclusions' not in kept and 'intervals' not in kept and 'instruments' not in kept
    info = {'instruments': [{'id': 'BTC.VENUE', 'symbol': 'BTC'}], 'sources': [{'nativeInstrument': 'BTC.VENUE', 'stock': {'commission': {}}}],
            'index': {**kept, 'exclusions': index['exclusions']}}
    (tmp_path / 'info.json').write_text(json.dumps(info), encoding='utf-8')
    record = {'index': kept, 'traded': 1, 'infoPath': str(tmp_path / 'info.json')}
    result = public_summary(record)
    assert result['sources'] == [{'nativeInstrument': 'BTC.VENUE'}] and result['instruments'] == info['instruments']
    assert result['index'] == kept and 'sources' not in record


def test_the_limitation_says_how_many_members_have_no_instrument_and_the_worst_year():
    from modules.quant.backend.index_universe import gap_text, gaps
    index = asyncio.run(freeze(fixture(unmapped=True), SELECTION, 0, 20))
    found = gaps(index, 3)
    assert found['unmapped'] == 1 and found['worst'] == 1 and found['withoutPrices'] == 3 and found['year'] == 1970
    text = gap_text(found)
    assert '1 members over the period have no Market instrument' in text and 'up to 1 a day in 1970' in text and '3 have no prices' in text
    assert gaps(asyncio.run(freeze(fixture(), SELECTION, 0, 20)), 0) is None


def test_benchmarks_buy_first_day_members_and_equal_weight_each_days_members(monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "modules" / "quant"))
    from native.strategy_report import Report

    days = pd.date_range("2024-01-01", periods=3, tz="UTC")
    closes = (days + pd.Timedelta(hours=21)).as_unit("ns").asi8
    ms = closes // 1_000_000
    def members(names):
        return [{"instrument": {"instrumentId": name}} for name in names]
    report = object.__new__(Report)
    report.display = {}
    report.invocation = {"index": {"nativeInstruments": {name: name for name in "ABC"}, "exclusions": [],
                                   "intervals": [{"since": int(ms[0]), "until": int(ms[2] - 1), "members": members("AB")},
                                                 {"since": int(ms[2]), "until": int(ms[2]), "members": members("ABC")}]}}
    report.frame = pd.DataFrame([{"ts_event": at, "instrument": name, "open": 100, "close": price}
                                 for name, prices in {"A": [100, 100, 100], "B": [100, 100, 100], "C": [100, 200, 400]}.items()
                                 for at, price in zip(closes, prices, strict=True)])
    hold, label = report.benchmark_series(days)
    assert "2 names" in label and list(hold) == [1, 1, 1]
    report.invocation["benchmark"] = {"kind": "equal"}
    equal, _ = report.benchmark_series(days)
    assert list(equal) == pytest.approx([1, 1, 4 / 3])
