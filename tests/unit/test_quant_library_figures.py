"""The item page's three charts, drawn by the backend as Plotly: every trace says what it is (`meta.role`), every chart says which one it is
(`layout.meta.key`), what the mouse reads out matches the draft (title, rows, sign, decimals, unit once), and a chart that cannot be drawn says why."""
import re

import pytest

from tests.unit.quant_library_fakes import DAY, build, factor_spec, synthetic_after

ROLES = {"main", "benchmark", "compare", "earlier", "group", "updown", "attention", "watch", "detail"}
SIGNED = re.compile(r"%\{[^}]*:\+[^}]*\}")


def factor_item(world):
    from modules.quant.backend import library_after
    data = synthetic_after()
    figures, points = library_after.measure(data.values, data.prices, scheduled=data.prices.index[data.prices.index > data.published_through], holding=5, delay=0, annualization=252)
    spec = factor_spec("Momentum", "v1", 199, published=0.015)
    spec["checks"]["lsPerPeriod"] = 0.0004
    spec["checks"]["ls"] = 0.1
    created = world.store.commit_versions([spec])[0]
    world.store.append_values("v1", [], cursor=299 * DAY, after=figures, points=points)
    return created["itemId"], figures


def allocation_item(world):
    spec = factor_spec("Risk parity", "a1", 199, kind="allocation", published=0.10)
    spec["checks"]["riskShares"] = {"A": 0.40, "B": 0.35, "C": 0.25}
    created = world.store.commit_versions([spec])[0]
    points = [{"date": day * DAY, "value": 0.10 + 0.0004 * (day - 150), "settled": True, "after": day >= 200} for day in range(150, 300)]
    after = {"kind": "allocation", "value": 0.158, "published": 0.10, "settled": 100, "pending": 0, "multiple": 1.58, "drift": True,
             "worst": {"asset": "A", "delta": 0.15}, "shares": {"published": {"A": 0.40, "B": 0.35, "C": 0.25}, "now": {"A": 0.55, "B": 0.30, "C": 0.15}}}
    world.store.append_values("a1", [], cursor=299 * DAY, after=after, points=points)
    return created["itemId"]


def every_trace_has_a_role(figure, key):
    assert figure["layout"]["meta"]["key"] == key
    assert figure["data"] and all(trace.get("meta", {}).get("role") in ROLES for trace in figure["data"]), "every trace says what it is"
    for shape in figure["layout"].get("shapes", []):
        assert shape.get("meta", {}).get("role") in ROLES


def test_every_figure_has_role_and_key(tmp_path):
    world = build(tmp_path)
    item, _ = factor_item(world)
    figures = world.library.detail(item, metric="ic", span="all")["figures"]
    every_trace_has_a_role(figures["cumulative"], "library.cumulative")
    roles = {trace["meta"]["role"] for trace in figures["cumulative"]["data"]}
    assert {"main", "benchmark", "compare"} <= roles, "after publication, before publication (Whole history), the published level"
    shapes = {shape["meta"]["role"] for shape in figures["cumulative"]["layout"]["shapes"]}
    assert shapes == {"benchmark", "group"}, "the publication day, and the stretch not settled yet"
    assert any("5 pending" in (note.get("text") or "") for note in figures["cumulative"]["layout"].get("annotations", []))
    since = world.library.detail(item, metric="ic", span="since")["figures"]["cumulative"]
    assert {trace["meta"]["role"] for trace in since["data"]} == {"main", "compare", "group"}
    # The section's key reads as the draft's: settled, at the published level, pending (an empty box trace for the shaded stretch).
    assert [trace["name"] for trace in since["data"] if trace.get("hoverinfo") != "skip"] == ["settled", "at the published level", "pending"]
    assert next(trace for trace in since["data"] if trace["name"] == "pending")["x"] == []
    assert "volatility" not in figures and "riskShares" not in figures, "only the charts of this kind of item"
    other = allocation_item(world)
    drawn = world.library.detail(other)["figures"]
    every_trace_has_a_role(drawn["volatility"], "library.volatility")
    every_trace_has_a_role(drawn["riskShares"], "library.risk_shares")
    assert {trace["meta"]["role"] for trace in drawn["volatility"]["data"]} == {"main", "compare", "watch"}, "the 1.5× line is amber, as the draft's"
    assert [trace["name"] for trace in drawn["volatility"]["data"]] == ["63-day realised", "published", "1.5×"]
    assert [trace["name"] for trace in drawn["riskShares"]["data"] if trace.get("showlegend") is not False] == ["published", "now", "over 10 pt"], "the hidden change bar is not keyed"
    assert {"earlier", "main", "watch"} <= {trace["meta"]["role"] for trace in drawn["riskShares"]["data"]} and "cumulative" not in drawn


def test_a_figure_that_cannot_be_drawn_says_why(tmp_path):
    world = build(tmp_path)
    created = world.store.commit_versions([factor_spec("Fresh", "v1", 50)])[0]
    missing = world.library.detail(created["itemId"])["figures"]["cumulative"]
    assert isinstance(missing.get("missing"), str) and missing["missing"] and "data" not in missing
    bare = world.store.commit_versions([factor_spec("Plain", "a1", 50, kind="allocation")])[0]
    drawn = world.library.detail(bare["itemId"])["figures"]
    assert all(isinstance(drawn[key].get("missing"), str) and drawn[key]["missing"] for key in ("volatility", "riskShares"))


@pytest.mark.parametrize(("metric", "main", "level"), [("ic", r"%\{y:\+\.2f\}", (r"\+\.4f", "a day")), ("ls", r"%\{y:\+\.0%\}", (r"\+\.1%", "a year"))])
def test_every_figure_reads_out_like_the_draft(tmp_path, metric, main, level):
    world = build(tmp_path)
    item, _ = factor_item(world)
    cumulative = world.library.detail(item, metric=metric, span="all")["figures"]["cumulative"]
    assert "Before publication" in "".join(trace.get("hovertemplate", "") for trace in cumulative["data"] if trace["meta"]["role"] == "benchmark"), "only with the whole history"
    since = next(trace for trace in cumulative["data"] if trace["meta"]["role"] == "main")
    assert "Since publication" in since["hovertemplate"] and re.search(main, since["hovertemplate"]), "a signed cumulative figure, with the date as the title"
    assert cumulative["layout"]["hovermode"] == "x unified", "the date is the title of the read-out"
    ref = next(trace for trace in cumulative["data"] if "At the published level" in trace.get("hovertemplate", ""))
    assert re.search(r"%\{y:" + level[0] + r"\}", ref["hovertemplate"]) and ref["hovertemplate"].count(level[1]) == 1, "the unit is written once; the level is the hidden line's own y"
    drawn = next(trace for trace in cumulative["data"] if trace["meta"]["role"] == "compare" and trace.get("hoverinfo") == "skip")
    assert drawn["line"]["dash"] in ("dash", "dot", "dashdot")
    assert all("Since publication" not in trace.get("hovertemplate", "") for trace in cumulative["data"] if trace is not since)
    assert all(re.fullmatch(r"[^%]*%\{[xy]:[^}]+\}[^%]*<extra></extra>", trace["hovertemplate"]) for trace in cumulative["data"] if trace.get("hovertemplate")), "only x or y read out (the plot's read-out takes no customdata)"

    other = allocation_item(world)
    figures = world.library.detail(other)["figures"]
    volatility = "".join(trace.get("hovertemplate", "") for trace in figures["volatility"]["data"])
    assert all(name in volatility for name in ("Realised 63-day", "Published", "1.5×")) and "%{y:.1%}" in volatility and not SIGNED.search(volatility), "shares of a year, no sign"
    shares = "".join(trace.get("hovertemplate", "") for trace in figures["riskShares"]["data"])
    assert all(name in shares for name in ("Published", "Now", "Change")) and re.search(r"%\{x:\+\.1f\} pt", shares), "the change is signed, in pt"
    assert re.findall(r"Now[^<]*%\{[^}]*\}", shares) and not re.search(r"(Now|Published)[^<]*%\{[^}]*\+", shares)


def test_a_cumulative_chart_with_nothing_settled_says_how_many_periods_wait_and_for_what():
    from modules.quant.backend import library_figures
    version = {"publishedAt": 1_762_473_600_000, "horizon": {"holding": 5, "delay": 0}, "timeframe": "1d", "publication": {"kind": "factor"}}
    points = [{"date": day, "after": True, "settled": False} for day in range(3)]
    assert library_figures.cumulative(version, points, "ic", "since") == {"missing": "Nothing settled yet: 3 periods since Nov 7, 2025, all waiting for their 5-day return"}
    assert library_figures.cumulative(version, [], "ic", "since") == {"missing": "Nothing settled yet: no period since Nov 7, 2025"}
    # Counted from the last bar the publication held; with auto-update off since publication nothing comes after it (library.js).
    through = {**version, "publishedAt": 1_759_190_400_000}
    assert library_figures.cumulative(through, [], "ic", "since") == {"missing": "Nothing settled yet: no period since Sep 30, 2025"}
    assert library_figures.cumulative({**through, "auto": False}, [], "ic", "since") == {"missing": "No values after Sep 30, 2025: auto-update is off"}


def test_risk_shares_of_many_assets_draw_the_largest_with_the_change_at_each_bar():
    """Seventy assets drew seventy unreadable bars: the ten largest then or now and any one over 10 points, the largest first, the change written at the end
    of each bar of now, the shares on a bottom axis and the names on the left as in the draft."""
    from modules.quant.backend import library_figures
    published = {f"i{n}": 0.5 if n == 0 else 0.5 / 69 for n in range(70)}
    now = {**published, "i0": 0.38, "i69": published["i69"] + 0.12}
    instruments = [{"instrumentId": f"i{n}", "column": f"C{n}", "displaySymbol": f"S{n}"} for n in range(70)]
    figure = library_figures.risk_shares({"after": {"shares": {"published": published, "now": now}}, "info": {"instruments": instruments}})
    traces = {trace["name"]: trace for trace in figure["data"]}
    labels = traces["published"]["y"]
    assert labels[:2] == ["S0", "S69"] and len(labels) == 10 and figure["layout"]["meta"] == {"key": "library.risk_shares", "shown": 10, "of": 70}
    assert traces["over 10 pt"]["text"][0] == "−12.0" and traces["over 10 pt"]["text"][labels.index("S69")] == "+12.0"
    assert figure["layout"]["xaxis"]["side"] == "bottom" and figure["layout"]["yaxis"]["side"] == "left"
    assert traces["published"]["meta"]["role"] == "earlier", "the published bar is grey, as the draft's, not the light-blue benchmark"


def test_volatility_is_drawn_since_publication_and_a_few_days_tick_once_a_day():
    from modules.quant.backend import library_figures
    points = [{"date": day * DAY, "value": 0.1, "after": day >= 8} for day in range(10)]
    figure = library_figures.volatility({"published": {"value": 0.08}, "timeframe": "1d"}, points)
    assert len(figure["data"][0]["x"]) == 2 and figure["layout"]["xaxis"] == {"dtick": 86_400_000}
    assert "missing" in library_figures.volatility({"published": {"value": 0.08}, "timeframe": "1d"}, [{**point, "after": False} for point in points])
