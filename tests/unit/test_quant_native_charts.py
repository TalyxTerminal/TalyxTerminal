"""Run with the pinned research interpreter; verify actual serialized assets."""
import xml.etree.ElementTree as ET

import pytest

from modules.quant.native.publish import Publisher


def test_plotly_preserves_native_data_layout_and_metadata(tmp_path):
    go = pytest.importorskip("plotly.graph_objects")
    import plotly.io as pio
    figure = go.Figure(go.Scatter(x=[1, 2], y=[.1234567890123, -.2], line={"color": "#123456"}))
    figure.update_layout(annotations=[{"text": "Native annotation", "x": 1, "y": .1}], xaxis={"title": "Native axis"})
    before = pio.to_json(figure, validate=False)
    publisher = Publisher({"runId": "test", "output": str(tmp_path)})
    artifact = publisher.plotly(figure, "performance/equity", "Equity", chapter="performance", order=2, engine="nautilus", note="Read the native cumulative account path.")
    assert (tmp_path / artifact["name"]).read_text(encoding="utf8") == before
    assert pio.to_json(figure, validate=False) == before
    assert artifact == {"name": "performance/equity.plotly.json", "title": "Equity", "kind": "plotly", "chapter": "performance", "order": 2, "engine": "nautilus", "note": "Read the native cumulative account path."}
    assert not list(tmp_path.rglob("*.html"))


def test_svg_keeps_text_and_data_colors_with_transparent_background(tmp_path):
    pytest.importorskip("matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figure, axis = plt.subplots()
    axis.plot([1, 2], [3, 4], color="#123456", label="Native series")
    axis.legend(facecolor="white")
    axis.text(1, 3.5, "Native annotation", bbox={"facecolor": "white"})
    axis.set_title("Native title")
    axis.set_xlabel("Native axis")
    axis.grid(True)
    publisher = Publisher({"runId": "test", "output": str(tmp_path)})
    before = matplotlib.rcParams["svg.fonttype"]
    artifact = publisher.svg(figure, "residuals/acf", "ACF", chapter="residuals", order=1, engine="statsmodels")
    xml = (tmp_path / artifact["name"]).read_text(encoding="utf8")
    ET.fromstring(xml)
    assert "<text" in xml and "Native title" in xml
    assert "#123456" in xml and "#a1a1aa" in xml and "#2a2a2e" in xml
    elements = ET.fromstring(xml)
    ns = {"svg": "http://www.w3.org/2000/svg"}
    legend = elements.find(".//svg:g[@id='legend_1']", ns)
    assert legend is not None
    assert any("fill: #111114" in node.get("style", "") for node in legend.iter())
    dark_boxes = [node for node in elements.iter() if "fill: #111114" in node.get("style", "")]
    assert len(dark_boxes) == 2  # Legend frame and annotation box.
    assert all("stroke: #2a2a2e" in node.get("style", "") and "opacity: 0.9" in node.get("style", "") for node in dark_boxes)
    assert "fill: none" in xml
    assert axis.lines[0].get_color() == "#123456"
    assert artifact["kind"] == "svg" and "note" not in artifact
    assert matplotlib.rcParams["svg.fonttype"] == before
    plt.close(figure)


def test_native_plotly_sequence_is_one_file_per_figure_and_values_stay_numeric(tmp_path):
    go = pytest.importorskip("plotly.graph_objects")
    pytest.importorskip("pandas")
    import pandas as pd

    from modules.quant.native.chart_publication import plotly_figures
    publisher = Publisher({"runId": "test", "output": str(tmp_path)})
    figures = [go.Figure(go.Scatter(x=["2025-01-01"], y=[1e-14])), go.Figure(go.Bar(x=["A"], y=[.25]))]
    artifacts = plotly_figures(publisher, figures, "quality/native", "Native", chapter="quality", engine="qlib")
    assert [item["name"] for item in artifacts if item["kind"] == "plotly"] == ["quality/native.plotly.json", "quality/native-2.plotly.json"]
    values = pd.read_parquet(tmp_path / "quality/native-values.parquet")
    assert values.loc[values.field == "y", "value"].iloc[0] == 1e-14


def test_authored_native_figure_also_exports_its_numeric_values(tmp_path):
    go = pytest.importorskip("plotly.graph_objects")
    from modules.quant.native.python_projects import NativeCollector
    source = tmp_path / "source"
    source.mkdir()
    invocation = {"runId":"author", "source":str(source), "output":str(tmp_path / "result"), "workbench":"statistics"}
    publisher = Publisher(invocation)
    NativeCollector(invocation, publisher).figure(go.Figure(go.Scatter(x=[1, 2], y=[.1, .2])), "Author diagnostic")
    assert [item["kind"] for item in publisher.index["artifacts"]] == ["plotly", "table"]
    assert publisher.index["artifacts"][0]["chapter"] == "fit"


def test_svg_bar_and_heatmap_exports_actual_heights_and_cells(tmp_path):
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt
    import pandas as pd
    from native.chart_publication import svg_figure
    figure, axes = plt.subplots(1, 2)
    axes[0].bar([1, 2], [2., 5.])
    axes[1].pcolormesh([[.1, .2], [.3, .4]])
    publisher = Publisher({"runId":"bars", "output":str(tmp_path)})
    svg_figure(publisher, figure, "native", "Native values", chapter="returns", engine="alphalens")
    values = pd.read_parquet(tmp_path / "native-values.parquet")
    assert values.loc[values.component == "bar", "y"].tolist() == [2., 5.]
    assert values.loc[values.component == "mesh", "value"].tolist() == [.1, .2, .3, .4]
    plt.close(figure)
