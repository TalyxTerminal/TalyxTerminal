"""Apply the two approved presentation/calendar patches to fixed native wheels.

The Qlib wheel is built from the recorded v0.9.8 commit with the existing
Microsoft tools; this recipe reuses that wheel and retains upstream metadata.
"""
from __future__ import annotations

import argparse
import base64
import csv
import difflib
import hashlib
import io
import json
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "modules/quant/runtime"


def bundled_uri(path):
    """Repository-relative wheel reference; Quant resolves it against the checkout."""
    return "file:./" + quote(Path(path).resolve().relative_to(ROOT).as_posix(), safe="/")


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"The fixed upstream patch context differs: {old!r}")
    return source.replace(old, new)


def qlib_patch(source):
    source = replace_once(source, "def _calculate_report_data(df: pd.DataFrame) -> pd.DataFrame:", "def _calculate_report_data(df: pd.DataFrame, time_format='%Y-%m-%d') -> pd.DataFrame:")
    source = replace_once(source, 'df.index.strftime("%Y-%m-%d")', "df.index.strftime(time_format)")
    source = replace_once(source, "def _report_figure(df: pd.DataFrame) -> [list, tuple]:", "def _report_figure(df: pd.DataFrame, time_format='%Y-%m-%d') -> [list, tuple]:")
    source = replace_once(source, "report_df = _calculate_report_data(df)", "report_df = _calculate_report_data(df, time_format=time_format)")
    source = replace_once(source, "def report_graph(report_df: pd.DataFrame, show_notebook: bool = True) -> [list, tuple]:", "def report_graph(report_df: pd.DataFrame, show_notebook: bool = True, time_format='%Y-%m-%d') -> [list, tuple]:")
    return replace_once(source, "fig_list = _report_figure(report_df)", "fig_list = _report_figure(report_df, time_format=time_format)")


def alphalens_performance(source):
    source = replace_once(source, "def factor_alpha_beta(\n    factor_data,\n    returns=None,\n    demeaned=True,\n    group_adjust=False,\n    equal_weight=False,\n):", "def factor_alpha_beta(\n    factor_data,\n    returns=None,\n    demeaned=True,\n    group_adjust=False,\n    equal_weight=False,\n    annualization=252,\n):")
    return replace_once(source, 'pd.Timedelta("252Days") / pd.Timedelta(period)', 'pd.Timedelta(days=annualization) / pd.Timedelta(period)')


def alphalens_tears(source):
    source = replace_once(source, "def create_summary_tear_sheet(factor_data, long_short=True, group_neutral=False):", "def create_summary_tear_sheet(factor_data, long_short=True, group_neutral=False, annualization=252):")
    for name in ("returns", "full"):
        source = replace_once(source, f"def create_{name}_tear_sheet(\n    factor_data, long_short=True, group_neutral=False, by_group=False\n):", f"def create_{name}_tear_sheet(\n    factor_data, long_short=True, group_neutral=False, by_group=False, annualization=252\n):")
    source = replace_once(source, "factor_data, demeaned=long_short, group_adjust=group_neutral\n    )", "factor_data, demeaned=long_short, group_adjust=group_neutral, annualization=annualization\n    )")
    source = replace_once(source, "factor_data, factor_returns, long_short, group_neutral\n    )", "factor_data, factor_returns, long_short, group_neutral, annualization=annualization\n    )")
    return replace_once(source, "factor_data, long_short, group_neutral, by_group, set_context=False\n    )", "factor_data, long_short, group_neutral, by_group, annualization=annualization, set_context=False\n    )")


def patched_wheel(upstream, modifications):
    with zipfile.ZipFile(upstream) as archive:
        payload = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    patches = []
    for name, transform in modifications.items():
        original = payload[name].decode("utf-8").replace("\r\n", "\n")
        changed = transform(original)
        compile(changed, name, "exec")
        payload[name] = changed.encode()
        patches.extend(difflib.unified_diff(original.splitlines(True), changed.splitlines(True), fromfile="a/" + name, tofile="b/" + name))
    record = next(name for name in payload if name.endswith(".dist-info/RECORD"))
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name, content in sorted(payload.items()):
        if name != record:
            writer.writerow([name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("="), len(content)])
    writer.writerow([record, "", ""])
    payload[record] = output.getvalue().encode()
    parts = upstream.name.split("-")
    filename = "-".join([*parts[:2], "1talyx", *parts[2:]])
    target = RUNTIME / "wheels" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(payload.items()):
            entry = zipfile.ZipInfo(name, (2026, 10, 11, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, content)
    (RUNTIME / "patches").mkdir(exist_ok=True)
    (RUNTIME / "patches" / (parts[0] + ".patch")).write_text("".join(patches), encoding="utf-8")
    return {"path": "wheels/" + filename, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "upstreamSha256": hashlib.sha256(upstream.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", type=Path, help="Optional explicit import directory for new upstream materials")
    args = parser.parse_args()
    handoff = args.handoff.resolve() if args.handoff else RUNTIME
    upstream = RUNTIME / "upstream"
    upstream.mkdir(parents=True, exist_ok=True)
    qlib_wheel = next(upstream.glob("pyqlib*.whl"), None)
    if qlib_wheel is None:
        if not args.handoff:
            raise RuntimeError("Missing upstream Qlib wheel; rebuild with modules/quant/runtime/build_qlib_upstream.py and explicitly import its output with --handoff.")
        qlib_wheel = next((handoff / "wheels").glob("pyqlib*.whl"))
        shutil.copy2(qlib_wheel, upstream / qlib_wheel.name)
    alphalens_wheel = upstream / "alphalens_reloaded-0.5.1-py3-none-any.whl"
    if not alphalens_wheel.exists():
        metadata = json.load(urllib.request.urlopen("https://pypi.org/pypi/alphalens-reloaded/0.5.1/json"))
        artifact = next(item for item in metadata["urls"] if item["filename"] == alphalens_wheel.name)
        content = urllib.request.urlopen(artifact["url"]).read()
        assert hashlib.sha256(content).hexdigest() == "1455c630a8a5822bcbc605c08d6ddab37360f27a317c8b64e936be6bc1d50c66"
        alphalens_wheel.write_bytes(content)
    qlib = patched_wheel(upstream / qlib_wheel.name, {"qlib/contrib/report/analysis_position/report.py": qlib_patch})
    alphalens = patched_wheel(alphalens_wheel, {"alphalens/performance.py": alphalens_performance, "alphalens/tears.py": alphalens_tears})
    if not (upstream / "qlib-v0.9.8.tar.gz").exists():
        shutil.copy2(handoff / "qlib-v0.9.8.tar.gz", upstream / "qlib-v0.9.8.tar.gz")
    # The other engines' entries (skfolio, QuantLib, ORE) are kept as recorded.
    recorded = json.loads((RUNTIME / "identity.json").read_text())
    identity = {**recorded, "python": "3.12", "qlibCommit": "54355232463878d2eebb91fe0ee5fa7fa1f5976c", "qlib": qlib, "alphalens": alphalens, "source": json.loads((handoff / "qlib-build.json").read_text()) if (handoff / "qlib-build.json").is_file() else recorded["source"]}
    (RUNTIME / "identity.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
    requirements = (handoff / "research-dependencies.txt").read_text(encoding="utf-8") if (handoff / "research-dependencies.txt").is_file() else (RUNTIME / "research.pins.txt").read_text(encoding="utf-8")
    requirements = "\n".join(line for line in requirements.splitlines() if not line.startswith("pyqlib @"))
    requirements = "\n".join("alphalens-reloaded @ " + bundled_uri(RUNTIME / alphalens["path"]) if line.startswith("alphalens-reloaded @") else line for line in requirements.splitlines())
    requirements += "\npyqlib @ " + bundled_uri(RUNTIME / qlib["path"]) + "\n"
    pins = RUNTIME / "research.pins.txt"
    pins.write_text(requirements, encoding="utf-8")
    # Run from the repository root: file:./ references and the lock header stay relative.
    subprocess.run([shutil.which("uv"), "pip", "compile", pins.relative_to(ROOT).as_posix(), "--python-version", "3.12", "--python-platform", "windows", "--generate-hashes", "--no-deps", "--output-file", (RUNTIME / "research.lock").relative_to(ROOT).as_posix()], cwd=ROOT, check=True)
    print(json.dumps(identity))


if __name__ == "__main__":
    main()
