"""Standard 11: the platform knows no module, exchange or business word; exchange limits count processes."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Platform code: what every module stands on, generated files included (they come from platform declarations).
PLATFORM = ["kernel", "host", "sdk", "web/src/kernel", "web/src/runtime", "web/src/api", "web/src/shell",
            "web/src/design", "contracts", "scripts/contracts", "scripts/checks"]
# The shared vocabulary is where instruments, bars, venues and prices are defined for every module: business words,
# venue names and the module names that are also finance words (market, trade, chart) are its subject; the other
# module names are not.
VOCABULARY = ["vocabulary"]
SUFFIXES = (".py", ".ts", ".tsx", ".json", ".mjs", ".js", ".css")
MODULES = sorted(path.name for path in (ROOT / "modules").iterdir() if (path / "talyx.module.json").exists())
# "gate" alone is also an ordinary technical word (a fault gate); the exchange is caught as gateio and by its hosts.
EXCHANGES = ["binance", "binanceusdm", "binancecoinm", "okx", "gateio", "kraken", "bybit", "bitget",
             "coinbase", "deribit", "htx", "kucoin", "mexc", "hyperliquid", "bitfinex", "cryptocom", "ccxt"]
BUSINESS = ["indicator", "strategy", "backtest", "watchlist", "candle", "ohlcv", "orderbook", "instrument", "exchange",
            "order", "account", "position", "alert", "symbol", "ticker", "portfolio", "trading", "broker", "trade"]
# Words that are also ordinary English, where the platform means the ordinary sense: an order is a sequence (sort
# order, draw order, ORDER BY), a position is a place (CSS, a label's height, a pointer), an alert is a notice (TxAlert,
# ARIA's alert role), an indicator is any small sign (a running indicator, a checkbox's), a chart is a graph (Quant's
# charts, a chart's key). Such a word is a violation only where it names the trade or the module: in the phrases below.
# Everything else about them is ordinary English and passes; every other word in the lists is caught wherever it is.
SENSES = {
    "order": re.compile(r"\b(open|limit|market|stop|pending|resting|bracket|filled|cancell?ed)[\s_-]*orders?\b|"
                        r"\b(place[sd]?|placing|cancel(s|led|ling)?|submit(s|ted)?|amend(s|ed)?)[\s_-]*"
                        r"(an?\s+|the\s+|all\s+)?orders?\b|"
                        r"\borders?[\s_-]*(book|id|type|side|intent|ticket|entry|form|history|status|qty|quantity|fills?)\b|"
                        r"\borders?\s+(placed|filled|cancell?ed|executed|sent)\b", re.IGNORECASE),
    "position": re.compile(r"\b(open|long|short|net|closed|flat|margin|perp|spot)[\s_-]*positions?\b|"
                           r"\bpositions?[\s_-]*(size|side|pnl|p&l|margin|entry|liq\w*)\b|"
                           r"\b(clos(e|es|ed|ing)|reduc(e|es|ed|ing)|hold(s|ing)?)[\s_-]*(an?\s+|the\s+|all\s+|its\s+|their\s+)?"
                           r"positions?\b", re.IGNORECASE),
    "alert": re.compile(r"\b(price|chart|indicator|level|crossing|triggered)[\s_-]*alerts?\b|"
                        r"\balerts?[\s_-]*(condition|trigger|log|level|price)s?\b", re.IGNORECASE),
    "indicator": re.compile(r"\b(technical|chart|trend|momentum|volume|custom)[\s_-]*indicators?\b|"
                            r"\bindicators?[\s_-]*(template|param\w*|pane|script|menu|list|value|values|setting\w*)\b|"
                            r"(?-i:\bIndicators\b)", re.IGNORECASE),
    # The module: as a path or a panel kind, and as a capitalised name in running text ("from Chart", "Chart's").
    "chart": re.compile(r"\bmodules?[/:.]chart\b|"
                        r"(?-i:\b(from|in|of|and|or|to|by|with|like|than|the) Chart\b)|"
                        r"(?-i:\bChart('s|\s*[:·×]|\s+(and|or|module|panel|tab|window)\b))", re.IGNORECASE),
}
# Everyday technical forms of words that are caught everywhere else: an HTTP user agent, JavaScript's well-known symbols.
TECHNICAL = re.compile(r"\buser[-_]agent|\bSymbol\.(iterator|asyncIterator|for|dispose|asyncDispose)\b", re.IGNORECASE)
# Names third-party libraries chose, which the platform's code cannot rename: Dockview's dndStrategy, the Instrument Sans
# typeface. Matched exactly as written, so the word itself is still caught anywhere else.
LIBRARY = re.compile(r"\bdndStrategy\b|\bInstrument Sans\b")
FINANCE = {"market", "trade", "chart"}
TOKEN = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")


def files(roots: list[str]) -> list[Path]:
    listed = subprocess.run(["git", "ls-files", *roots], cwd=ROOT, capture_output=True, text=True).stdout
    # Lock files are package hashes, not code.
    return [ROOT / line for line in listed.splitlines()
            if line.endswith(SUFFIXES) and ".test." not in line and not line.endswith("package-lock.json")]


def tokens(line: str) -> set[str]:
    """Every word in a line, also the ones inside identifiers (marketColors, market_data, OKX)."""
    words = {word.lower() for word in TOKEN.findall(LIBRARY.sub(" ", TECHNICAL.sub(" ", line)))}
    return words | {word[:-1] for word in words if word.endswith("s")}


def caught(line: str, words: set[str]) -> set[str]:
    """The words of `words` a line names: anywhere, or for the words in SENSES only in their trade or module sense."""
    return (tokens(line) & (words - SENSES.keys())) | {word for word in words & SENSES.keys() if SENSES[word].search(line)}


def violations(roots: list[str], words: list[str]) -> list[str]:
    wanted = set(words)
    found = []
    for path in files(roots):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            hits = caught(line, wanted)
            if hits:
                found.append(f"{path.relative_to(ROOT).as_posix()}:{number}: {sorted(hits)} {line.strip()[:100]}")
    return found


@pytest.mark.parametrize("roots,words", [(PLATFORM, MODULES), (VOCABULARY, sorted(set(MODULES) - FINANCE)),
                                         (PLATFORM, EXCHANGES), (PLATFORM, BUSINESS)],
                         ids=["modules", "modules in the vocabulary", "exchanges", "business"])
def test_platform_code_names_no_module_exchange_or_business(roots, words):
    found = violations(roots, words)
    assert not found, f"{len(found)} lines:\n" + "\n".join(found[:400])


ALL = set(MODULES) | set(EXCHANGES) | set(BUSINESS)


def test_ordinary_senses_pass():
    ordinary = {
        "order": ["in the same order", "sort order", "padded = txDrawOrder(traces);", "ORDER BY created", "order: -1;",
                  "--tx-chart-order-1", "every member's entries, in the members' order", "in working order"],
        "position": ["position: absolute;", "a context menu at the pointer position", 'sidePosition?: "start"',
                     "end labels (`positions` are pixel heights)", "background-position: 120% 0;",
                     'target.api.moveTo({ group: g, position: "right" })', "xaxis: { position: 0.5 }"],
        "alert": ['<TxAlert title="Saved workspaces are unavailable">', 'marker.setAttribute("role", "alert");',
                  "const alerts = useShellAlerts();", "settingsAlert: boolean;", ".tx-alert-actions { display: flex; }", "max-width: var(--tx-alert-line-width);",
                  '<TxSwitch label="Alert sounds" />'],
        "indicator": ["A small running indicator inside a table cell.", "<CheckboxPrimitive.Indicator />",
                      'dndTabIndicator: "line",', "No scroll indicator of any kind"],
        "chart": ["A chart's key as the draft draws it", "export interface TxChartKey", '<TxIcon name="chart" />',
                  '["Chart canvas", [["chart-grid", "Grid"]]]', 'icon = "chart-candlestick"', "This chart could not be drawn.",
                  '<TxIconButton variant="tool" label="Chart settings" icon="settings" />'],
    }
    passed = {word: [line for line in lines if word in caught(line, ALL)] for word, lines in ordinary.items()}
    assert passed == {word: [] for word in ordinary}


def test_the_trade_and_module_senses_are_caught():
    trade = {
        "order": ["No open orders", "placeOrder(intent)", "Orders placed from the panel show up here.", "order_id",
                  "cancel all orders", "a limit order", "<TxTh>Order type</TxTh>"],
        "position": ["6 open positions", "closePosition(id)", "position size", "net_position", "a short position"],
        "alert": ["price alerts", "alertCondition", "when the alert triggers", "an alert trigger"],
        "indicator": ["technical indicators", "indicator template", "<TxIcon name=\"function\" />Indicators</TxButton>",
                      "New indicator script…"],
        "chart": ["module:chart", "modules/chart/README.md", "Orders placed from Chart or Trade show up here.",
                  '"Chart ×4 · Agent"', "Chart: toolbar with the indicator menu open", "repaints the Chart when it shows",
                  "Chart's own setting"],
    }
    missed = {word: [line for line in lines if word not in caught(line, ALL)] for word, lines in trade.items()}
    assert missed == {word: [] for word in trade}


def test_every_other_word_is_caught_wherever_it_is():
    named = {"market": ["setMarketColors(value)", 'data-market-colors="green-up"', "Let's follow the market."],
             "trade": ["A trade", "trade.read_orders"], "agent": ["(agent-2 §1.14)", "AgentDemo"],
             "quant": ["Quant alone 14 MB"], "okx": ["OKX perpetual swaps"], "binanceusdm": ["binanceusdm"],
             "backtest": [".cm-backtest dl { margin: 0; }"], "strategy": ["strategy/carry.py"],
             "symbol": ["(a symbol, a timeframe menu)"], "trading": ["TradingView-style"], "candle": ["useCandles(96, 33)"]}
    missed = {word: [line for line in lines if word not in caught(line, ALL)] for word, lines in named.items()}
    assert missed == {word: [] for word in named}
    # Only the exact technical and library forms are let through.
    let_through = ["user-agent", "Symbol.iterator", 'dndStrategy="pointer"', "font-family: Instrument Sans, sans-serif"]
    assert [line for line in let_through if caught(line, ALL)] == []
    assert caught("dndStrategyName", ALL) == {"strategy"} and caught("an instrument", ALL) == {"instrument"}


def test_kernel_does_not_import_host():
    found = subprocess.run(["git", "grep", "-n", "-E", r"^\s*(from|import) host", "--", "kernel"], cwd=ROOT,
                           capture_output=True, text=True).stdout
    assert not found, found


def test_exchange_request_margin_counts_the_registered_processes(tmp_path):
    """Three processes on one limit domain together stay within the exchange's reported budget."""
    from talyx_sdk import rest_queue
    share = rest_queue.process_share
    assert share(tmp_path, "domain", "p1") == 1.0
    share(tmp_path, "domain", "p2")
    assert share(tmp_path, "domain", "p3") * 3 <= 1.0


def test_cooldowns_are_shared_across_processes_survive_restarts_and_notify_once(tmp_path):
    from talyx_sdk import rest_fuse
    first = rest_fuse.Cooldowns(tmp_path)
    assert first.open("domain", until=4_000_000_000, reason="429") is True     # notify
    second = rest_fuse.Cooldowns(tmp_path)                                    # another process, or after restart
    assert second.until("domain") == 4_000_000_000
    assert second.open("domain", until=4_000_000_000, reason="429") is False   # same cooldown: no second notice
