"""The research library consumes the public Market file/table shapes with inclusive dates and stable identities."""
import pandas as pd
import pytest

from modules.quant import talyx_quant as q


def file_source(tmp_path, monkeypatch, days):
    calls = []
    def channel(name,args,timeout=None):
        assert name == "market.read_data" and args["format"] == "file"
        calls.append(args)
        start = pd.Timestamp(args["start"],tz="UTC") if len(args["start"]) == 10 else pd.Timestamp(args["start"])
        end = pd.Timestamp(args["end"],tz="UTC") + pd.Timedelta(days=1)-pd.Timedelta(nanoseconds=1) if len(args["end"]) == 10 else pd.Timestamp(args["end"])
        rows = [[day.isoformat(),"100","102.25","99.75",str(100+i/4),"0.00000123"] for i,day in enumerate(days) if start <= day <= end]
        path = tmp_path/"bars.parquet"
        pd.DataFrame(rows,columns=["time","open","high","low","close","volume"]).to_parquet(path)
        return {"file":{"path":str(path)}}
    monkeypatch.setattr(q,"_channel",lambda:channel)
    return calls


def test_q_bars_inclusive_two_spy_sessions(tmp_path,monkeypatch):
    days = pd.date_range("2025-01-01","2025-01-06",tz="UTC")
    calls = file_source(tmp_path,monkeypatch,days)
    result = q.bars("SPY","1d","2025-01-02","2025-01-03")
    assert result.index.strftime("%Y-%m-%d").tolist() == ["2025-01-02","2025-01-03"]
    assert result.close.tolist() == [100.25,100.5]
    assert calls == [{"symbols":["SPY"],"timeframe":"1d","start":"2025-01-02","end":"2025-01-03","format":"file"}]


def test_q_bars_reads_all_250_spy_sessions_with_numeric_prices(tmp_path,monkeypatch):
    holidays = pd.to_datetime(["2023-01-02","2023-01-16","2023-02-20","2023-04-07","2023-05-29","2023-06-19",
                               "2023-07-04","2023-09-04","2023-11-23","2023-12-25"],utc=True)
    days = pd.bdate_range("2023-01-01","2023-12-31",tz="UTC").difference(holidays)
    calls = file_source(tmp_path,monkeypatch,days)
    result = q.bars("SPY","1d","2023-01-01","2023-12-31")
    assert len(result) == 250 and len(calls) == 1
    assert result.index.equals(pd.DatetimeIndex(days,name="time")) and result.close.iloc[-1] == 162.25


@pytest.mark.parametrize("timeframe",["1d","1m"])
def test_q_bars_preserves_inclusive_instants(tmp_path,monkeypatch,timeframe):
    calls = file_source(tmp_path,monkeypatch,pd.to_datetime(["2024-01-02T08:00:00+08:00"],utc=True))
    result = q.bars("okx:BTC/USDT",timeframe,"2024-01-02T00:00:00Z","2024-01-02T00:00:00Z")
    assert result.volume.tolist() == [.00000123] and pd.Timestamp(calls[0]["end"]) == result.index[0]


def test_q_bars_keeps_empty_market_read_numeric(tmp_path,monkeypatch):
    file_source(tmp_path,monkeypatch,[])
    answer = q.bars("SPY","1d","2023-01-01","2023-01-01")
    assert answer.empty and str(answer.index.tz) == "UTC" and pd.api.types.is_numeric_dtype(answer.close)


def test_q_bars_propagates_market_source_refusal(monkeypatch):
    def channel(*args,**kwargs):
        raise RuntimeError("forbidden: HTTP 451")
    monkeypatch.setattr(q,"_channel",lambda:channel)
    with pytest.raises(RuntimeError,match="HTTP 451"):
        q.bars("AAPL","1d","2023-01-01","2023-12-31")


def test_members_at_cutoff_remain_distinct_across_pages(monkeypatch):
    calls = []
    def channel(name,args,timeout=None):
        assert name == "market.read_research"
        calls.append(args)
        cursor = args.get("cursor")
        return {"columns":[{"name":k} for k in ("symbol","instrumentId","start","end")],
                "rows":[["US:XYZ" if not cursor else "XYZ","id-1" if not cursor else None,"2020-01-01",None]],
                "nextCursor":None if cursor else "2"}
    monkeypatch.setattr(q,"_channel",lambda:channel)
    found = q.index_members("^GSPC","2025-01-02",as_of="2025-01-03")
    assert found.instrumentId.iloc[0] == "id-1" and pd.isna(found.instrumentId.iloc[1]) and found.tradable.tolist() == [True,False]
    assert calls[0]["index"] == {"symbol":"^GSPC","at":"2025-01-02"} and calls[1]["asOf"] == "2025-01-03"
