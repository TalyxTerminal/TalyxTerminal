"""The shared figure template frames every Quant chart the drafts' way: value axis on the right, no Plotly legend, no axis titles, one read-out writing."""
from modules.quant.native import figures as F


def test_a_figure_has_the_value_axis_on_the_right_and_no_legend_or_axis_titles():
    made = F.figure("x.equity", [F.line(["2020-01-01"], [1.0], "main", "equity", "Strategy")], showlegend=True,
                    xaxis={"title": {"text": "Date"}, "type": "date"}, yaxis={"title": {"text": "Equity"}, "tickformat": ".0%"})
    layout = made["layout"]
    assert layout["showlegend"] is False
    assert layout["yaxis"] == {"side": "right", "tickformat": ".0%"}
    assert layout["xaxis"] == {"type": "date"}


def test_the_read_out_is_a_head_then_one_name_and_value_a_line():
    assert F.hover("%{x}", ("Return", "%{y:+.1%}"), ("Trades", "%{customdata}")) == "<b>%{x}</b><br>Return: %{y:+.1%}<br>Trades: %{customdata}<extra></extra>"
    assert F.hover(None, ("IC", "%{x:+.3f}")) == "IC: %{x:+.3f}<extra></extra>"


def test_the_equity_figure_offers_a_log_scale_and_drops_its_legend():
    made = {"data": [{"type": "scatter", "x": ["2020-01-01", "2020-01-02"], "y": [1.0, 1.2], "name": "Strategy"}],
            "layout": {"meta": {"key": "strategy.equity"}, "showlegend": True, "yaxis": {"title": {"text": "Equity"}}}}
    F.plain_axes(made)
    assert made["layout"]["meta"]["scales"] == ["linear", "log"]
    assert made["layout"]["showlegend"] is False
    assert "title" not in made["layout"]["yaxis"]
