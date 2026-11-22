"""Market's declarations: what it offers, typed by ``models``. The contract is generated from this file."""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from talyx_sdk import ContractError
from talyx_sdk.declare import Module
from talyx_sdk.process import Call, Context

from . import calendars
from . import models as m
from . import public_inputs as inputs
from . import public_results as outputs
from .routing import in_worker

# One resolved spot listing and its hourly series, as returned by the instrument actions.
SPOT = {'identityVersion': 1,
 'venueId': 'okx',
 'symbol': 'BTC/USDT',
 'displaySymbol': 'BTC/USDT',
 'marketType': 'spot',
 'assetClass': 'crypto',
 'contractOrientation': 'none',
 'calendarId': '24_7',
 'adjustmentEligible': False,
 'base': 'BTC',
 'quote': 'USDT',
 'instrumentId': 'vi1_80daee1a0754faf74ee61fa41665cd4db2dd441bf2cebb0e126a579e72872724'}
HOURLY = {'identityVersion': 1,
 'instrumentId': 'vi1_80daee1a0754faf74ee61fa41665cd4db2dd441bf2cebb0e126a579e72872724',
 'capability': 'ohlcv',
 'timeframe': '1h',
 'priceBasis': 'last',
 'sessionScope': 'continuous',
 'adjustment': 'raw',
 'seriesId': 'vs1_92ca41ce5f46e4cd3c03f49ccc2d9ff076fa5fc70f0342cca6c3d57a9e8cd095'}

module = Module("market")
log = logging.getLogger("talyx.market")
market = None  # the running Market, set when the process starts


def _value(model) -> dict:
    """Market's code reads requests as the JSON it was sent."""
    return model.model_dump(mode="json", exclude_unset=True)


async def upgrade_member_dates(ctx):
    """Keep cached index data when moving to exchange-local member dates and separate weights."""
    from .sources.indices import localize_sp500_dates
    from .store import DocumentStore

    research = Path((await ctx.platform.storage())["dataDir"]) / "research"

    def upgrade():
        members = DocumentStore(research, "members")
        for index in members.ids():
            doc = members.read(index)
            if doc["source"] == "sp500_history":
                localize_sp500_dates(doc["rows"])
                members.write(index, doc)
            elif doc["source"] == "tushare":
                weights = research / "weights"
                weights.mkdir(exist_ok=True)
                (members.root / f"{index}.json").replace(weights / f"{index}.json")

    await asyncio.to_thread(upgrade)


module.data_format(2, {2: upgrade_member_dates})


@module.changes
class SourceChanges:
    @staticmethod
    async def list(call_ids, run_id):
        return market.registry.preferences.list(call_ids, run_id)

    @staticmethod
    async def revert(call_id, direction):
        await market.admin.revert(call_id, direction)


@module.on_delete_workspace
async def delete_workspace(workspace: str, *, preview: bool) -> list[str]:
    jobs = [job for job in market.jobs.jobs.values() if job.record["workspaceId"] == workspace]
    if preview:
        return [f"Market fetch history ({len(jobs)} jobs)"] if jobs else []
    await market.jobs.workspace_closed(workspace)
    for job in jobs:
        (market.jobs.root / (job.id + ".json")).unlink(missing_ok=True)
        market.jobs.jobs.pop(job.id)
    market.registry.preferences.forget(workspace)
    return []


@module.on_start
async def start(ctx: Context) -> None:
    global market
    # Importing CCXT and Market's own code and building the exchange calendars take most of a second each: a worker
    # thread does them, so Market's loop never does (the sources describe themselves with CCXT as Market starts); the
    # imports below then find them loaded. One thread: the work is Python holding the GIL, so a second thread would
    # not finish sooner, it would only keep the loop waiting longer.
    prepared = asyncio.ensure_future(asyncio.to_thread(_prepare))
    directories = await ctx.platform.storage()
    await prepared
    from talyx_sdk import rest_profiles

    from .app import Market
    from .exchange_limits import ExchangeProfiles
    from .sources import official

    rest_profiles.use(ExchangeProfiles())
    # Opening the stores reads the identity database, the sources' settings and every fetch job's record (hundreds
    # of kilobytes each): in a worker too.
    market = await asyncio.to_thread(Market, ctx, Path(directories["dataDir"]))
    for source in {id(entry.source): entry.source for entry in market.registry.entries.values()}.values():
        module.implement("market.data_source", source.id, **official.handlers(source))
    ctx.watch_extensions("data_source", market.extensions_changed)

    def job_started(job) -> None:
        # A download runs as its own task, so the fetch call that started it can return or be joined. Started by
        # a fetch call it is part of that call's entry; resumed when its workspace opens again it is listed alone.
        workspace = job.record["workspaceId"]
        if workspace:
            ctx.activity(workspace, module.actions["fetch"].title).add_task(job.task)

    market.jobs.on_started = job_started
    await market.start()
    changes = await open_workspaces(ctx, market.jobs)
    watcher = asyncio.create_task(follow_workspaces(market.jobs, changes))
    market.workspace_watch = changes, watcher
    watcher.add_done_callback(_workspace_watch_done)
    market.extensions_changed(await ctx.extensions("data_source"))


async def open_workspaces(ctx: Context, jobs):
    """Downloads belong to the workspace that started them: interrupted downloads continue when it opens again, and when
    Market (or the Host) restarts while it is open. The changes are subscribed before the open workspaces are listed,
    so one opening in between is not missed; they are returned for ``follow_workspaces``."""
    changes = await ctx.subscribe("platform.workspace.changes")
    jobs.open_workspaces = set()
    for workspace in await ctx.platform.open_workspaces():
        jobs.open_workspaces.add(workspace)
        jobs.workspace_opened(workspace)
    return changes


async def follow_workspaces(jobs, changes) -> None:
    try:
        async for change in changes:
            if change["kind"] == "opened":
                jobs.open_workspaces.add(change["workspaceId"])
                jobs.workspace_opened(change["workspaceId"])
            elif change["kind"] == "closed":
                jobs.open_workspaces.discard(change["workspaceId"])
    except ContractError as exc:
        # The Host ends platform streams before it stops the module processes.
        if (exc.code, exc.message) != ("unavailable", "TALYX is closing"):
            raise


def _workspace_watch_done(task: asyncio.Task) -> None:
    if not task.cancelled() and (error := task.exception()) is not None:
        log.error("Workspace subscription failed", exc_info=error)


def _prepare():
    import ccxt  # noqa: F401
    import ccxt.async_support  # noqa: F401
    import ccxt.pro  # noqa: F401
    for name in (".app", ".exchange_limits", ".sources.official"):
        importlib.import_module(name, __package__)
    calendars.prepare()


@module.private_routes
def routes(ctx: Context):
    """The Market panel's own requests (library, detail, sources, jobs); the panel shows the same numbers as
    ``read_data`` view=local."""
    import orjson
    from fastapi import FastAPI, Request, Response

    app = FastAPI()

    async def panel(request):
        answer = await market.panel.call(await request.json(), request.headers.get("x-talyx-workspace"))
        # The library is thousands of rows, asked for every 2 s while a fetch runs: written by orjson in a worker,
        # not by FastAPI's encoder on the event loop (50-100 ms a time).
        return Response(await in_worker(orjson.dumps, answer), media_type="application/json")

    # Registered with the annotation as an object: this file's postponed annotations would hide it from FastAPI.
    panel.__annotations__ = {"request": Request}
    app.post("/panel")(panel)

    async def failed(_request, error: ContractError):
        """A Market failure as the panel reads it (``panelCall``: code, message, execution, effects at the top)."""
        status = {"not_found": 404, "conflict": 409, "invalid_input": 400, "permission_denied": 403}.get(error.code, 500)
        return Response(orjson.dumps(error.to_dict()), status_code=status, media_type="application/json")
    app.add_exception_handler(ContractError, failed)
    return app


# ── instruments ──────────────────────────────────────────────────────────────

@module.action("instruments.search", effects=["read"], examples=[{'query': 'Palantir'}, {'query': 'BTC', 'venue': 'binance', 'marketType': 'swap'}, {'venue': 'okx', 'marketType': 'spot'}])
async def instruments_search(input: inputs.Search, call: Call) -> outputs.Search:
    """Find instruments by code, former code or name; each result's symbol can be passed to every Market action.
    With venue and no query: that exchange's instrument list. Use it when you have a company name or are unsure of
    a code; a known code (AAPL, 600519.SH, binance:BTC/USDT) goes straight to the read actions."""
    return await market.public.search(_value(input))


@module.action("instruments.get", effects=["read"], examples=[{'symbols': ['AAPL', 'DASH', '600519.SH']}, {'symbols': ['vi2_1793cd1caa114f60a8c44cebbf7d147f'], 'include': ['codes']}])
async def instruments_get(input: inputs.Get, call: Call) -> outputs.Matches:
    """Instrument records for up to 500 symbols or instrumentIds, one item each in input order: found, redirect
    (the ID was merged into another; previousId is the one given), ambiguous (with candidates; qualify the code,
    e.g. US:DASH) or not_found. at reads a code as it was on that date. include ["codes"] adds the code history.
    records is for modules moving saved instrument records between data directories."""
    return await market.public.get(_value(input))


@module.action("instruments.terms", effects=["read"], examples=[{'symbols': ['binance:BTC/USDT:USDT']}, {'symbols': ['AAPL'], 'start': '2025-01-02', 'end': '2025-01-31'}])
async def instruments_terms(input: inputs.Terms, call: Call) -> outputs.Terms:
    """Trading terms of each instrument: maker and taker fees, minimum quantity, quantity and price increments,
    contract multiplier; with start and end, a stock's execution material for those days (sessions, price limits,
    lot rules). series instead of symbols names exact Market series (for modules)."""
    return await market.public.terms(_value(input))


@module.action("read_series", effects=["read"], examples=[{"instrumentId": SPOT["instrumentId"], "series": HOURLY, "limit": 10}])
async def read_series(input: inputs.ReadSeries, call: Call) -> m.DataReadOutput:
    """For modules that move whole series (charts, frozen research data): one column-encoded page of one exact
    series, times in epoch nanoseconds, up to 10,000 rows, with its revision. Use read_data for anything else."""
    return await market.public.series(_value(input))


@module.action("read_coverage", effects=["read"], examples=[{'symbols': ['binance:BTC/USDT:USDT']}, {'data': 'members'}, {'market': 'US', 'query': 'AAPL'}])
async def read_coverage(input: inputs.Coverage, call: Call) -> outputs.MarketTable:
    """What exists for these symbols: per data kind and timeframe, what is stored locally, what the source offers,
    the first available date, gaps and why something cannot be fetched (refused, not covered, rate limited).
    data:"members" lists the indexes with member history. Without symbols: the local data library, filtered by
    group, market, venue, status or query."""
    return await market.public.coverage(_value(input))


@module.action("read_quotes", effects=["read"], examples=[{'symbols': ['AAPL', 'binance:BTC/USDT']}])
async def read_quotes(input: inputs.Quotes, call: Call) -> outputs.Quotes:
    """Current quotes of up to 100 instruments: price, bid, ask, mark and index where they exist, change and changePct
    (percent) against the basis (previous session close for stocks; basis rolling_24h or last for crypto), as-of
    time and whether delayed. For history use read_data."""
    return await market.public.quotes(_value(input))


@module.action("read_option_chain", effects=["read"], examples=[{'symbol': 'gate:BTC/USDT'}, {'symbol': 'gate:BTC/USDT', 'expiry': '2026-12-25'}])
async def read_option_chain(input: inputs.OptionChain, call: Call) -> outputs.OptionChain:
    """The listed options on an underlying with their quotes, a page at a time, and the available expiries; expiry
    keeps one expiry."""
    return await market.public.option_chain(_value(input))


@module.action("read_research", effects=["read"], examples=[
    {'data': 'actions', 'symbols': ['AAPL'], 'start': '2020-01-01', 'end': '2020-12-31'},
    {'data': 'members', 'index': {'symbol': '^GSPC', 'at': '2025-06-30'}},
    {'data': 'financials', 'symbols': ['MSFT'], 'periodKinds': ['FQ'], 'start': '2024-01-01'},
    {'data': 'classification', 'market': 'CN', 'at': '2025-06-30'}])
async def read_research(input: inputs.Research, call: Call) -> outputs.Research:
    """Reference data as one table: actions (splits with ratio, cash dividends with amount; exDate, recordDate and
    payDate are exchange-local dates), members of an index (code at the time, current symbol, name, start and end of
    each membership, both inclusive; index.at for one day's members, start/end for everyone who was in the index in
    that window; weight is percent when published), classification (sector, industry; with start/end or at, where
    a source keeps dated answers (A shares) each row runs from when it was known until it changed, read on the
    window's first day and each quarter end, so a change shows at most a quarter late; elsewhere the source's rows,
    start empty when the source gives none), shares,
    financials (source concept names in metrics; FY or FQ),
    market_cap, earnings_dates, factors. asOf hides what was published after that time. Pages by size: follow
    nextCursor."""
    return await market.public.research(_value(input))


@module.action("read_professional", effects=["read"], examples=[{}, {'materialId': '<materialId>', 'version': '<version>'}])
async def read_professional(input: inputs.Professional, call: Call) -> outputs.Materials | outputs.MaterialRows:
    """Professional materials the user imported or a paid source supplies (execution terms, quote files): without
    materialId, the list (filter by kind, sourceId, symbol, quoteType); with materialId and version, that version's
    rows a page at a time."""
    return await market.public.professional(_value(input))


@module.action("read_data", effects=["read"], examples=[
    {'symbols': ['NVDA'], 'timeframe': '1d', 'start': '2024-01-01', 'end': '2024-12-31'},
    {'symbols': ['JPM', 'BAC', 'binance:BTC/USDT'], 'timeframe': '1d', 'limit': 5},
    {'index': {'symbol': '^GSPC'}, 'timeframe': '1d', 'start': '2025-01-01', 'end': '2025-06-30', 'format': 'file'},
    {'symbols': ['okx:BTC/USDT:USDT'], 'data': 'funding_rate', 'start': '2025-10-01'}])
async def read_data(input: inputs.ReadData, call: Call) -> outputs.DataPage | outputs.MarketFile:
    """Candles (data "bars", the default) or a crypto series of one or many instruments, or of an index's members,
    as one long table with a symbol column, plus each symbol's meta: instrumentId, name, source, adjustment, units,
    timezone, the first and last dates actually returned, and gaps. start and end are both included; without start,
    each symbol's latest limit rows (default 24). Missing local data is filled from the source within the read.
    A page holds up to about 18,000 characters (a year of daily bars of one symbol); nextCursor continues, inside a
    long history too. An index's members as rows must fit one page (else too_large): for those, and for many symbols
    or long histories worked in Python, format "file" gives one Parquet file with symbol and instrumentId columns.
    With index and no index.at the file only contains in-index bars. Group by instrumentId and membershipPeriod
    to compute returns for each holding period, including re-entries. fill_too_large: run market.fetch first."""
    return await market.public.read(_value(input))


@module.action("calendar.sessions", effects=["read"], errors=['invalid_input', 'unsupported'],
               examples=[{'calendarId': 'XNYS', 'startDate': '2024-01-02', 'endDate': '2024-01-05'}])
async def calendar_sessions(input: inputs.Sessions, call: Call) -> outputs.Sessions:
    """Trading days of an exchange calendar (XNYS, XSHG, XHKG, 24_7 ...) between two local dates, with each session's
    open and close as ISO times."""
    return await market.public.sessions(_value(input))


@module.action("calendar.bar_times", effects=["read"], errors=['invalid_input', 'unsupported'],
               examples=[{'calendarId': '24_7', 'timeframe': '1h', 'sessionScope': 'continuous', 'start': '1704067200000000000', 'end': '1704153600000000000'}])
async def calendar_bar_times(input: m.CalendarBarTimesInput, call: Call) -> m.CalendarBarTimesOutput:
    """For modules aligning series: the open and close times, in epoch nanoseconds, of a calendar's bars of one
    timeframe between two instants (end included)."""
    value = _value(input)
    limit = value.get("limit", 250)
    return calendars.bar_times(value["calendarId"], value["timeframe"], value["sessionScope"], int(value["start"]),
                               int(value["end"]) + 1, int(value["after"]) if value.get("after") else None,
                               limit)


@module.action(effects=["read"], examples=[{"symbols": ["AAPL"], "data": [{"kind": "bars", "timeframe": "1d"}],
                                          "start": "2025-01-01"}])
async def estimate_fetch(input: inputs.Fetch, call: Call) -> outputs.FetchEstimate:
    """Estimate bytes for missing time-series rows and report free bytes on the Market data drive before fetch.
    Uses the same selection and space check as fetch, without starting a download."""
    return await market.public.estimate_fetch(_value(input), call)


@module.action(effects={"path": "operation.op", "branches": {
    "list": {"effects": ["read"]}, "cancel": {"effects": ["write"], "target": "operation.jobId"},
    "resume": {"effects": ["write", "external"], "target": "operation.jobId"},
}}, examples=[{"operation": {"op": "list"}}, {"operation": {"op": "resume", "jobId": "job_..."}}])
async def downloads(input: inputs.Downloads, call: Call) -> outputs.Downloads | outputs.DownloadState:
    """List downloads in this workspace, newest first, or cancel a download or resume an interrupted one by jobId.
    The list includes progress and items already stored. Follow nextCursor for older jobs."""
    return await market.public.downloads(_value(input), call.workspace_id)


@module.action(effects=["write", "external"], task=True, examples=[
    {"symbols": ["AAPL", "MSFT"], "data": [{"kind": "bars", "timeframe": "1d"}], "start": "2015-01-01"},
    {"index": {"symbol": "^GSPC"}, "data": [{"kind": "bars", "timeframe": "1d"}, {"kind": "actions"}], "start": "2018-01-01"},
    {"symbols": ["binance:BTC/USDT"], "data": [{"kind": "bars", "timeframe": "1m"}], "start": "2024-01-01"}])
async def fetch(input: inputs.Fetch, call: Call) -> m.FetchOutput:
    """Download what is missing locally for symbols or an index's members (every member for its time in the index;
    index.at for one day's members) over start..end, both included, as a long task: the result comes when it ends,
    with what was stored and what failed and why. Use it before reading many instruments or long histories
    (read_data says fill_too_large). A fetch started again after an interruption continues where it stopped."""
    return await market.public.fetch(_value(input), call)


@module.action(effects=["write"], task=True, examples=[
    {"symbols": ["AAPL", "MSFT"], "data": "bars", "timeframe": "1d", "start": "2020-01-01", "format": "csv"},
    {"index": {"symbol": "^GSPC"}, "data": "bars", "timeframe": "1d", "start": "2020-01-01", "destination": {"folder": "D:/research/sp500"}}])
async def export(input: inputs.Export, call: Call) -> m.ExportOutput:
    """Write the selected data to Parquet (default) or CSV files, filling what is missing first (a long task):
    candles or a crypto series for symbols or an index's members, or a research kind (data "actions", "members" ...).
    destination.folder accepts an absolute path or a folder relative to the calling workspace and returns absolute
    file paths. Omit destination to receive file references. Stocks are split-adjusted unless adjustment says otherwise."""
    return await market.public.export(_value(input), call)


@module.action(effects={"path": "operation.op", "branches": {
    "import": {"effects": ["write"]}, "delete": {"effects": ["delete"], "target": "operation.importId"},
}}, task=True, errors=['invalid_input', 'not_found'],
               examples=[{'operation': {'op': 'import', 'paths': ['data/my_prices.csv'], 'spec': {'kind': 'bars', 'market': 'US', 'codeColumn': 'ticker', 'timeframe': '1d', 'timezone': 'America/New_York'}}}])
async def write_data(input: m.WriteDataInput, call: Call) -> m.WriteDataOutput:
    """op=import reads CSV or Parquet files (named paths or a folder, absolute or workspace-relative) of price bars (a time or date column, open, high,
    low, close and optionally volume, one row per bar) into Market as an imported dataset; research data and
    professional materials are not imported. Codes come from a code column, the file name or spec.symbol and are
    matched by market, code and trading date. New stock codes become listings. Crypto requires an existing
    contract on the named exchange. Imported
    data never replaces a source. op=delete removes local data for the given symbols and kinds (or one imported
    dataset); it is downloaded again when needed. Example: {"operation":{"op":"import",
    "paths":["data/my_prices.csv"],"spec":{"kind":"bars","market":"US","codeColumn":"ticker","timeframe":"1d",
    "timezone":"America/New_York"}}}."""
    from .imports import read_import_file

    op = input.operation
    if isinstance(op, m.ImportFolder):
        folder = await market.folder(op.folder, call.workspace_id)
        return await market.write_data.import_folder(folder, _value(op).get("spec") or {})

    async def workspace_bytes(path: str) -> bytes:
        return await read_import_file(str(await market.folder(path, call.workspace_id)))

    return await market.write_data.run(_value(input), read=workspace_bytes)


@module.action(effects=["read"], examples=[{"folder": "imports"}])
async def scan_import(input: inputs.ScanImport, call: Call) -> outputs.MarketTable:
    """Preview CSV and Parquet files in a folder before write_data import. Use an absolute path or a path
    relative to the workspace. Each row identifies a file and detected instrument, row counts or a file error."""
    from .public_actions import table_page
    folder = await market.folder(input.folder, call.workspace_id)
    result = await market.write_data.scan_folder(folder, _value(input).get("spec") or {})
    return table_page(result["files"], _value(input))


@module.action(effects=["read"], errors=['invalid_input', 'not_found'],
               examples=[{'view': 'details', 'source': 'alpaca'}])
async def read_source(input: m.ReadSourceInput, call: Call) -> m.ReadSourceOutput:
    """view=list: data sources with state (working, not configured, rate limited until, failing and why,
    unsubscribed) and whether a key is configured (never the key), 40 per page. view=selection: which source
    Market uses for each market and data kind, and why. view=details: one source's coverage, non-secret settings
    with their schema, last test failures and network route. Example: {"view":"details","source":"alpaca"}."""
    return await market.admin.read(_value(input))


@module.action(effects={"path": "operation.op", "branches": {
    "enable": {"effects": ["write", "external"], "target": "operation.source"},
    "disable": {"effects": ["write", "external"], "target": "operation.source"},
    "configure": {"effects": ["write", "external"], "target": "operation.source"},
    "test": {"effects": ["external"]}, "test_all": {"effects": ["external"]},
    "reconnect": {"effects": ["write", "external"], "target": "operation.source"},
}}, task=True, errors=['invalid_input', 'not_found'],
               examples=[{'operation': {'op': 'disable', 'source': 'yfinance'}}])
async def write_source(input: m.WriteSourceInput, call: Call) -> m.WriteSourceOutput:
    """op=enable / op=disable turns one source on or off; stored data affected by the change is fetched again
    from the new best source in the background. op=configure sets non-secret settings declared by the source;
    keys are entered by the user in the Market panel. op=test makes real sample requests and reports the checks
    that failed with each source's latency; op=test_all tests every source with one sample request each.
    op=reconnect rebuilds a source's connections. Example: {"operation":{"op":"disable","source":"yfinance"}}."""
    return await market.admin.write(_value(input), call)


# ── streams ──────────────────────────────────────────────────────────────────

@module.stream()
async def session(params: m.SessionStreamParams, call: Call) -> AsyncIterator[m.SessionStreamItem]:
    """One series live: a replace with what is stored, then append (each closed bar), update (the forming bar)
    and status. Market recovers disconnects, sleep and source changes itself; consumers never reopen."""
    async for item in market.sessions.open(_value(params)):
        yield item


@module.stream()
async def quotes(params: m.QuotesStreamParams, call: Call) -> AsyncIterator[m.QuotesStreamItem]:
    """Quotes of a set of instruments: the first item carries all, then only those that changed, at most four
    items a second."""
    async for item in market.quotes.open(_value(params)):
        yield item


# ── the data source extension point ──────────────────────────────────────────

module.extension_point(
    "data_source",
    description="Supply market data to Market. Official free and paid sources and third-party modules implement the "
                "same interface. A source only answers requests: Market searches, plans, rate-limits by the declared "
                "concurrency, pages, fills gaps after live reconnects, aggregates periods, adjusts, stores and serves.",
    descriptor=m.SourceDescriptor,
    actions={
        "describe": (m.SourceDescribeInput, m.SourceDescribeOutput, "What the source gives for one source id."),
        "search": (m.SourceSearchInput, m.SourceSearchOutput, "Candidates for a query."),
        "catalog": (m.SourceCatalogInput, m.SourceCatalogOutput, "The source's instrument list, page by page."),
        "history": (m.SourceHistoryInput, m.SourceHistoryOutput, "One page of history."),
        "bulk": (m.SourceBulkInput, m.SourceBulkOutput, "A whole trading day of many instruments as one file."),
        "configure": (m.SourceConfigureInput, m.SourceConfigureOutput, "Store the source's settings."),
    },
    streams={"live": (m.SourceLiveParams, m.SourceLiveItem, "Live events of one target.")},
)
