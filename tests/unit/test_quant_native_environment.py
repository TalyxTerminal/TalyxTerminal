"""Fixed native wheels keep their exact filenames when the repository relocates."""
import asyncio

from modules.quant.backend.native_environment import project_lock
from modules.quant.backend.project_paths import local_path


def test_relocated_local_version_wheel_is_a_real_file(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / ".python-version").write_text("3.13.13", encoding="utf8")
    runtime = tmp_path / "runtime"
    wheels = runtime / "wheels"
    wheels.mkdir(parents=True)
    filename = "nautilus_trader-2.0.0rc6+talyx.3-cp313-cp313-win_amd64.whl"
    wheel = wheels / filename
    wheel.write_bytes(b"isolated filename fixture")
    monkeypatch.setattr("modules.quant.backend.native_environment.RUNTIME", runtime)
    baseline = runtime / "strategy.lock"
    baseline.write_text("nautilus-trader @ file:///Z:/old/modules/quant/runtime/wheels/" + filename.replace("+", "%2B") + " --hash=sha256:test\n", encoding="utf8")
    lock, origin = asyncio.run(project_lock(None, {"engine": "nautilus", "workbench": "strategy"},
        {"snapshotPath": str(source)}, tmp_path, baseline))
    uri = lock.split(" @ ", 1)[1].split(" ", 1)[0]
    assert local_path(uri) == wheel.resolve()
    assert local_path(uri).read_bytes() == b"isolated filename fixture"
    assert origin["pythonVersion"] == "3.13.13"


def test_repository_relative_bundled_wheel_resolves_against_runtime(tmp_path):
    from modules.quant.backend.native_environment import bundled_wheels
    runtime = tmp_path / "checkout" / "modules/quant/runtime"
    wheel = runtime / "wheels/nautilus_trader-2.0.0rc6+talyx.17-cp313-cp313-win_amd64.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(b"fixed")
    lock = ("nautilus-trader @ file:./modules/quant/runtime/wheels/nautilus_trader-2.0.0rc6%2Btalyx.17-cp313-cp313-win_amd64.whl \\\n"
            "    --hash=sha256:test\nnumpy==2.4.6\n")
    resolved = bundled_wheels(lock, runtime)
    uri = resolved.split(" @ ", 1)[1].split(" ", 1)[0]
    assert local_path(uri) == wheel.resolve()
    assert resolved.endswith("--hash=sha256:test\nnumpy==2.4.6\n")
