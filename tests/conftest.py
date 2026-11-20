"""Shared test setup. Offline and deterministic: tests never touch the network or the operator's data root.

Nothing here imports the platform or a module. The offline guards are applied when the guarded module is first
imported (by whichever test needs it), so a test that never touches Market or Agent pays nothing for them.
"""

from __future__ import annotations

import importlib.abc
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest


def pytest_configure(config):
    # Anything that builds the app from defaults lands in a disposable root, never in data/store or user/.
    base = Path(config.option.basetemp) if config.option.basetemp else Path(tempfile.gettempdir()) / "talyx-tests"
    os.environ["TALYX_DATA_DIR"] = str(base.parent / f"{base.name}-default-root")
    sys.meta_path.insert(0, _AfterImport(_GUARDS))


class _AfterImport(importlib.abc.MetaPathFinder):
    """Runs a guard right after the named module has executed."""

    def __init__(self, guards):
        self.guards = guards

    def find_spec(self, name, path, target=None):
        guard = self.guards.get(name)
        if guard is None:
            return None
        sys.meta_path.remove(self)
        try:
            spec = importlib.util.find_spec(name)
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None:
            return spec
        execute = spec.loader.exec_module

        def exec_module(module):
            execute(module)
            guard(module)

        spec.loader.exec_module = exec_module
        return spec


def _offline_market(network):
    """Market never leaves this machine in tests: hosts other than 127.0.0.1 fail as unreachable.

    Tests that answer from recordings replace the egress or CCXT clients themselves; ``market_egress`` tests get the
    real egress back (``real_market_egress``) and drive it through a local proxy.
    """
    from urllib.parse import urlsplit

    real = {name: getattr(network.Egress, name) for name in ("request", "probe", "websocket")}
    real["ccxt_client"] = network.ccxt_client
    network._real_for_tests = real

    def remote(url):
        return urlsplit(str(url)).hostname not in {"127.0.0.1", "localhost", "::1"}

    async def offline_request(self, source_id, url, **options):
        if remote(url):
            raise network.MarketError("network_unreachable", "The network is off in tests", source_id=source_id)
        return await real["request"](self, source_id, url, **options)

    async def offline_probe(self, url):
        return False if remote(url) else await real["probe"](self, url)

    async def offline_websocket(self, source_id, url, **options):
        if remote(url):
            raise network.MarketError("network_unreachable", "The network is off in tests", source_id=source_id)
        return await real["websocket"](self, source_id, url, **options)

    def offline_client(exchange_id, **options):
        value = real["ccxt_client"](exchange_id, **options)

        async def fetch(url, *_args, **_kwargs):
            import ccxt
            raise ccxt.NetworkError(f"The network is off in tests ({url})")

        value.fetch = fetch
        return value

    network.Egress.request = offline_request
    network.Egress.probe = offline_probe
    network.Egress.websocket = offline_websocket
    network.ccxt_client = offline_client


def _offline_model_info(service):
    """Agent fetches models.dev on its own schedule; tests drive that loop explicitly."""

    async def offline(self):
        pass

    service.ProviderService.start = offline


def _no_recycle_bin(workspaces):
    """A deleted workspace's folder never lands in the operator's Recycle Bin: tests delete it outright."""
    import shutil

    workspaces.recycle_directory = shutil.rmtree


_GUARDS = {
    "host.workspaces": _no_recycle_bin,
    "modules.market.backend.network": _offline_market,
    "modules.agent.backend.providers.service": _offline_model_info,
}


@pytest.fixture(autouse=True)
def real_market_egress(request, monkeypatch):
    if request.node.get_closest_marker("market_egress") is None:
        return
    from modules.market.backend import network

    for name in ("request", "probe", "websocket"):
        monkeypatch.setattr(network.Egress, name, network._real_for_tests[name])
    monkeypatch.setattr(network, "ccxt_client", network._real_for_tests["ccxt_client"])
