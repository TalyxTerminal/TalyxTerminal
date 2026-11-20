"""Per-runner run state for the approved PyneCore wheel.

``runner_context.py`` (next to this file) is added to the wheel; four hooks in
``pynecore/core/script_runner.py`` install a runner's own context whenever its
code executes, so many runners share one process.
"""
# ruff: noqa: E501 -- native source lines are preserved verbatim for wheel identity

import hashlib
from pathlib import Path

ORIGINAL_SCRIPT_RUNNER = "88e6c03f79daa154eacbc0dab8346f55fd2703670c3cd6544a26afea1d67d96a"
RUNNER_CONTEXT = Path(__file__).with_name("runner_context.py")

REPLACEMENTS = [
    # 1. the module
    (
        "LIVE_TRANSITION = OHLCV(timestamp=-1, open=-1, high=-1, low=-1, close=-1, volume=-1)\n",
        "from . import runner_context as _talyx_rc  # TALYX: per-runner run state\n\n"
        "LIVE_TRANSITION = OHLCV(timestamp=-1, open=-1, high=-1, low=-1, close=-1, volume=-1)\n",
    ),
    # 2. imports outside a runner run in the base context
    (
        "def import_script(script_path: Path, *, inputs: dict[str, Any] | None = None,\n",
        "def import_script(script_path: Path, *, inputs: dict[str, Any] | None = None,\n"
        "                  settings: dict[str, Any] | None = None,\n"
        "                  save_overrides: bool = False) -> ModuleType:\n"
        "    \"\"\"TALYX: :func:`_talyx_import_script` outside every runner's run state.\"\"\"\n"
        "    with _talyx_rc.outside():\n"
        "        return _talyx_import_script(script_path, inputs=inputs, settings=settings,\n"
        "                                    save_overrides=save_overrides)\n\n\n"
        "def _talyx_import_script(script_path: Path, *, inputs: dict[str, Any] | None = None,\n",
    ),
    # 3. a context per runner; the constructor already runs in it
    (
        "                 'broker_balance', '_sim_logged_open_ids', '_inputs', '_settings', '_live')\n",
        "                 'broker_balance', '_sim_logged_open_ids', '_inputs', '_settings', '_live',\n"
        "                 '_talyx_context')\n\n"
        "    def __init__(self, *args, **kwargs):\n"
        "        \"\"\"TALYX: :meth:`_talyx_init` in this runner's own run state.\"\"\"\n"
        "        self._talyx_context = _talyx_rc.Context()\n"
        "        _talyx_rc.acquire(self._talyx_context)\n"
        "        try:\n"
        "            self._talyx_init(*args, **kwargs)\n"
        "        finally:\n"
        "            _talyx_rc.release()\n",
    ),
    (
        "    def __init__(self, script_path: Path, ohlcv_iter: Iterable[OHLCV], syminfo: SymInfo, *,\n",
        "    def _talyx_init(self, script_path: Path, ohlcv_iter: Iterable[OHLCV], syminfo: SymInfo, *,\n",
    ),
    # 4. every step runs in the runner's context; the feed runs outside it
    (
        "        results = self._run_iter(on_progress, on_tick)\n"
        "        if not copy_results:\n"
        "            return results\n"
        "        return self._copy_results(results)\n",
        "        ctx = self._talyx_context\n"
        "        self.ohlcv_iter = _talyx_rc.feed(self.ohlcv_iter, ctx)\n"
        "        if self._magnifier_iter is not None:\n"
        "            self._magnifier_iter = _talyx_rc.feed(self._magnifier_iter, ctx)\n"
        "        results = self._run_iter(on_progress, on_tick)\n"
        "        if copy_results:\n"
        "            results = self._copy_results(results)\n"
        "        return _talyx_rc.guard(results, ctx)\n",
    ),
]


def patched_script_runner(raw: bytes) -> bytes:
    if hashlib.sha256(raw).hexdigest() != ORIGINAL_SCRIPT_RUNNER:
        raise ValueError("The upstream script_runner.py identity differs")
    source = raw.decode().replace("\r\n", "\n")
    for before, after in REPLACEMENTS:
        if source.count(before) != 1:
            raise ValueError("Isolation patch location differs: " + before.splitlines()[0])
        source = source.replace(before, after)
    return source.replace("\n", "\r\n").encode()


def runner_context() -> bytes:
    return RUNNER_CONTEXT.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n").encode()
