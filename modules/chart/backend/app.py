"""Chart's backend, composed: one object per workspace-independent service."""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import bars, exports, redirects, research
from .alerting import Alerts, Notifier
from .charts import Charts
from .feeds import Hub
from .flows import ChartFlow
from .marketdata import Market
from .pine.engine import PineEngine
from .pine.indicators import Catalog, Indicators, Pins
from .screens import Screens
from .store import Store
from .watchlists import Watchlists
from .wire import AlertDefaults
from .workspace import Workspaces


class ChartApp:
    def __init__(self, ctx, data: Path, user_indicators: Path):
        self.ctx = ctx
        self.defaults: dict[str, dict] = {}   # workspace → what its new alerts start with (Chart's settings)
        self.store = Store(data / "chart.db")
        self.export_folder = data / "exports"
        self.module_data = data.parent.resolve()
        self.market = Market(ctx)
        self.hub = Hub(self.market)
        self.engine = PineEngine(data / "logs")
        self.catalog = Catalog(self.engine, user_indicators)
        self.indicators = Indicators(self.engine, self.catalog, self.hub)
        self.workspaces = Workspaces(self.snapshot)
        self.charts = Charts(self.store, self.market, self.workspaces.publish)
        self.watchlists = Watchlists(self.store, self.market, self.workspaces.publish)
        pins = Pins(self.catalog, data / "pinned_code")
        self.screens = Screens(self.store, self.market, self.engine, pins, self.watchlists, self.workspaces.publish)
        self.alerts = Alerts(self.store, self.hub, self.indicators, pins,
                             self.watchlists, self.charts,
                             self.workspaces.publish, Notifier(ctx))
        self.flows: dict[tuple[str, str], list[ChartFlow]] = {}
        self.opening: dict[str, asyncio.Task] = {}
        self.platform_watches = []
        self.warming: asyncio.Task | None = None
        self.sent_health: dict | None = None
        self.engine.health_listeners.append(lambda _health: self.health_changed())
        self.store.failure_listeners.append(self.health_changed)

    def warm(self) -> None:
        async def warming():
            await self.engine.warm()
            await self.catalog.entries()
        self.warming = asyncio.ensure_future(warming())

    def snapshot(self, workspace: str) -> dict:
        return {"alerts": self.alerts.all(workspace), "watchlists": self.watchlists.all(workspace),
                "screens": self.screens.all(workspace), "templates": self.templates(),
                "alertDefaults": self.defaults.get(workspace, AlertDefaults().model_dump()), "health": self.health()}

    def health(self) -> dict:
        pine = self.engine.health()
        return {"pine": {"state": pine["state"], "reason": pine["reason"]},
                "alertStore": {"state": "failing" if self.store.failure else "ok", "reason": self.store.failure}}

    def health_changed(self) -> None:
        health = self.health()
        if health != self.sent_health:
            self.sent_health = health
            self.workspaces.publish_all({"type": "health", **health})

    def workspace_stream(self, workspace: str):
        self.watchlists.seed(workspace)
        return self.workspaces.subscribe(workspace)

    async def read_alert_defaults(self, workspace: str) -> dict:
        values = (await self.ctx.call("platform.settings.read", {"module": "chart", "keys": ["alertPopup", "alertSound"],
                                                                 "workspaceId": workspace}))["values"]
        self.defaults[workspace] = {"toast": values["alertPopup"]["value"], "sound": values["alertSound"]["value"]}
        return self.defaults[workspace]

    async def alert_defaults_changed(self, workspace: str) -> None:
        """A global change reaches every open workspace that follows it."""
        for each in list(self.defaults) if workspace == "global" else [workspace]:
            if each in self.defaults:
                self.workspaces.publish(each, {"type": "alert_defaults", "value": await self.read_alert_defaults(each)})

    def copy_workspace(self, source: str, copy: str) -> None:
        """The copy gets its own charts (as they are now, with their saved revisions) and watchlists. Its panels still
        name the original charts; ``/charts/adopt`` tells them which copies are theirs. The source may itself be a copy
        not saved since, whose panels still name its own original's charts: those names lead to the copy's charts too."""
        copies = {}
        for chart_id in self.store.charts(source):
            copies[chart_id] = copied = "chart-" + uuid.uuid4().hex[:12]
            self.store.copy_chart(chart_id, copied, copy)
            self.store.put_object("chart_copy", {"id": f"{copy}:{chart_id}", "chart": copied}, copy)
        for record in self.store.objects("chart_copy", source):
            named = record["id"].removeprefix(f"{source}:")
            if record["chart"] in copies and named not in copies:
                self.store.put_object("chart_copy", {"id": f"{copy}:{named}", "chart": copies[record["chart"]]}, copy)
        for watchlist in self.watchlists.all(source):
            self.store.put_object("watchlist", {"id": (copied := "wl-" + uuid.uuid4().hex[:12]),
                                                "value": {**watchlist, "id": copied}}, copy)
        self.store.put_object("seeded", {"id": copy}, copy)

    async def adopt(self, workspace, body):
        """Which charts of this workspace stand for the charts its panels name (a copied workspace's panels name the
        original's)."""
        mapping = {}
        for chart_id in body.chartIds:
            record = self.store.object("chart_copy", f"{workspace}:{chart_id}")
            mapping[chart_id] = record["chart"] if record else chart_id
        return {"charts": mapping}

    async def delete_workspace(self, workspace, preview):
        charts = self.store.charts(workspace)
        objects = [(kind, row["value"]) for kind in ("alert", "watchlist", "screen")
                   for row in self.store.objects(kind, workspace)]
        if preview:
            return [*(f"Chart · {self.store.chart(key)['document']['instrument']['displaySymbol']}" for key in charts),
                    *(f"{kind.capitalize()} · {value.get('name') or kind.capitalize()}" for kind, value in objects)]
        self.alerts.workspace_closed(workspace)
        stopping = []
        for pending in (self.opening.pop(workspace, None), self.watchlists.seeding.pop(workspace, None)):
            if pending:
                pending.cancel()
                stopping.append(pending)
        for kind, value in objects:
            key = value["id"]
            if kind == "screen" and (run := self.screens.runs.pop(key, None)):
                run.cancel()
                stopping.append(run)
            if kind == "alert":
                self.alerts.records.pop(key, None)
        if stopping:
            await asyncio.gather(*stopping, return_exceptions=True)
        self.alerts.loaded.discard(workspace)
        self.defaults.pop(workspace, None)
        for key in charts:
            self.charts.entries.pop(key, None)
        with self.store.transaction():
            for table in ("charts", "objects", "fired", "changes"):
                self.store.write(f"DELETE FROM {table} WHERE workspace=?", (workspace,))
        return []

    def flow(self, workspace: str, chart_id: str) -> ChartFlow:
        flows = self.flows.get((workspace, chart_id))
        if not flows:
            raise ContractError("unavailable", t("Open chart {chart_id} with platform.panels.open_target first", chart_id=chart_id))
        return flows[-1]

    async def chart_stream(self, workspace: str, chart_id: str):
        flow = ChartFlow(self.charts, self.hub, self.indicators, workspace, chart_id)
        flow.start()
        self.flows.setdefault((workspace, chart_id), []).append(flow)

        async def items():
            try:
                async for item in flow.out:
                    yield item
            finally:
                self.flows[(workspace, chart_id)].remove(flow)
                await flow.close()
        return items()

    # ── route handlers: (workspace, body model) → response ──

    async def open_chart(self, workspace, body):
        return self.charts.state(self.charts.entry(workspace, body.chartId))

    async def stored_layer(self, workspace, body):
        doc = self.charts.entry(workspace, body.chartId).document
        for layer in doc.layers:
            if layer.id == body.id:
                return layer.model_dump(mode="json", by_alias=True, exclude={"visible", "version", "symbol"})
        raise ContractError("not_found", t("No layer {id} on this chart", id=body.id))

    async def research_file(self, ref):
        return await asyncio.to_thread(research.load_reference, ref, self.module_data)

    async def file_layer(self, workspace, body):
        layer = await self.research_file(body)
        research.check_layer(layer)
        return layer.model_dump(mode="json", by_alias=True)

    async def layer_history(self, workspace, body):
        instrument, _ = await self.market.instrument(body.instrumentId)
        series = self.market.default_series(instrument, body.timeframe, adjustment=body.adjustment)
        columns = await research.history(self.market, instrument, series, research.milliseconds(body.window.start),
                                         research.milliseconds(body.window.to))
        return {"bars": columns, "zone": await self.market.timezone(instrument["calendarId"]),
                "priceFormat": bars.price_format(await self.market.price_increment(instrument),
                                                [str(value) for value in columns["close"][-500:]])}

    async def create_chart(self, workspace, body):
        return await self.charts.create(workspace, body.instrumentId, body.timeframe, body.importId, body.copyOf)

    async def commit(self, workspace, body):
        return await self.charts.commit(workspace, body.chartId, body.baseRevision, body.commands)

    async def undo(self, workspace, body):
        return self.charts.step(workspace, body.chartId, back=True)

    async def redo(self, workspace, body):
        return self.charts.step(workspace, body.chartId, back=False)

    async def save(self, workspace, body):
        self.charts.save(workspace, [item.model_dump() for item in body.charts])
        return {}

    async def backfill(self, workspace, body):
        self.charts.entry(workspace, body.chartId)
        return await self.flow(workspace, body.chartId).backfill(body.to, body.bars)

    async def set_view(self, workspace, body):
        self.charts.entry(workspace, body.chartId)
        if body.bars is not None and body.left is None and body.right is None:
            view = {"bars": body.bars}
        elif body.bars is None and body.left is not None and body.right is not None and body.left <= body.right:
            view = {"left": body.left, "right": body.right}
        else:
            raise ContractError("invalid_input", t("Give left and right (left first), or bars"))
        self.flow(workspace, body.chartId).set_view(view)
        return {}

    async def start_replay(self, workspace, body):
        self.charts.entry(workspace, body.chartId)
        await self.flow(workspace, body.chartId).start_replay(body.at)
        return {}

    async def step_replay(self, workspace, body):
        await self.flow(workspace, body.chartId).step_replay(body.bars)
        return {}

    async def stop_replay(self, workspace, body):
        await self.flow(workspace, body.chartId).stop_replay()
        return {}

    async def export_chart(self, workspace, body):
        self.charts.entry(workspace, body.chartId)
        return exports.export(self.flow(workspace, body.chartId), body.format)

    def templates(self) -> list[dict]:
        """Indicator, chart-settings and drawing templates: global, every workspace sees them."""
        return sorted((record["value"] for record in self.store.objects("template")), key=lambda item: item["name"])

    def template(self, template_id: str) -> dict:
        record = self.store.object("template", template_id)
        if record is None:
            raise ContractError("not_found", t("No template {template_id}", template_id=template_id))
        return record["value"]

    async def save_template(self, workspace, body):
        if body.templateId is not None and self.store.object("template", body.templateId) is None:
            raise ContractError("not_found", t("No template {templateId}", templateId=body.templateId))
        value = {"id": body.templateId or "tp-" + uuid.uuid4().hex[:12], "kind": body.kind, "name": body.name,
                 "content": body.content}
        self.store.put_object("template", {"id": value["id"], "value": value}, None)
        self.workspaces.publish_all({"type": "template", "value": value})
        return value

    async def delete_template(self, workspace, body):
        if self.store.object("template", body.templateId) is None:
            raise ContractError("not_found", t("No template {templateId}", templateId=body.templateId))
        self.store.delete_object("template", body.templateId)
        self.workspaces.publish_all({"type": "template_removed", "id": body.templateId})
        return {}

    async def create_alert(self, workspace, body):
        return self.alerts.create(workspace, body.spec.model_dump())

    async def update_alert(self, workspace, body):
        return self.alerts.update(workspace, body.alertId, body.revision, body.spec.model_dump())

    async def set_alert_state(self, workspace, body):
        return self.alerts.set_state(workspace, body.alertId, body.revision, body.state)

    async def delete_alert(self, workspace, body):
        self.alerts.delete(workspace, body.alertId)
        return {}

    async def alert_log(self, workspace, body):
        return self.alerts.log(workspace, body.alertId, body.before, body.limit)

    async def save_screen(self, workspace, body):
        return self.screens.save(workspace, body.screenId, body.revision, body.spec.model_dump())

    async def run_screen(self, workspace, body):
        return self.screens.run(workspace, body.screenId)

    async def cancel_screen(self, workspace, body):
        return self.screens.cancel(workspace, body.screenId)

    async def delete_screen(self, workspace, body):
        self.screens.delete(workspace, body.screenId)
        return {}

    async def create_watchlist(self, workspace, body):
        return self.watchlists.create(workspace, body.name, [item.model_dump() for item in body.sections])

    async def update_watchlist(self, workspace, body):
        sections = None if body.sections is None else [item.model_dump() for item in body.sections]
        return self.watchlists.update(workspace, body.watchlistId, body.revision, body.name, sections)

    async def delete_watchlist(self, workspace, body):
        self.watchlists.delete(workspace, body.watchlistId)
        return {}

    async def catalog_entries(self, workspace, body):
        return {"folder": str(self.catalog.user_dir.resolve()), "entries": await self.catalog.entries()}

    def handlers(self) -> dict:
        return {"/layers/history": self.layer_history, "/layers/stored": self.stored_layer, "/layers/file": self.file_layer,
                "/charts/open": self.open_chart, "/charts/adopt": self.adopt, "/charts/create": self.create_chart, "/charts/commit": self.commit,
                "/charts/undo": self.undo, "/charts/redo": self.redo, "/charts/save": self.save,
                "/charts/backfill": self.backfill, "/charts/view": self.set_view, "/charts/export": self.export_chart,
                "/replay/start": self.start_replay, "/replay/step": self.step_replay, "/replay/stop": self.stop_replay,
                "/indicators/catalog": self.catalog_entries,
                "/watchlists/create": self.create_watchlist,
                "/watchlists/update": self.update_watchlist, "/watchlists/delete": self.delete_watchlist,
                "/alerts/create": self.create_alert, "/alerts/update": self.update_alert,
                "/alerts/state": self.set_alert_state, "/alerts/delete": self.delete_alert, "/alerts/log": self.alert_log,
                "/screens/save": self.save_screen, "/screens/run": self.run_screen, "/screens/cancel": self.cancel_screen,
                "/screens/delete": self.delete_screen, "/templates/save": self.save_template,
                "/templates/delete": self.delete_template}

    def workspace_opened(self, workspace: str) -> None:
        """Alerts follow after merged instruments are replaced (alerts made meanwhile follow at once). Closing the
        workspace ends the opening; one that ends anyway after the close starts nothing."""
        self.alerts.open.add(workspace)

        async def opened():
            try:
                await self.read_alert_defaults(workspace)
                await redirects.apply(workspace, self)
                if self.opening.get(workspace) is task:
                    self.alerts.workspace_opened(workspace)
            finally:
                if self.opening.get(workspace) is task:
                    del self.opening[workspace]
        task = asyncio.ensure_future(opened())
        self.opening[workspace] = task

    def workspace_closed(self, workspace: str) -> None:
        opening = self.opening.pop(workspace, None)
        if opening is not None:
            opening.cancel()
        self.defaults.pop(workspace, None)
        self.alerts.workspace_closed(workspace)
        self.charts.workspace_closed(workspace)

    def watch_platform(self, changes, follow) -> None:
        task = asyncio.create_task(follow(changes), name=changes.name)
        self.platform_watches.append((changes, task))
        task.add_done_callback(self._watch_ended)

    @staticmethod
    def _watch_ended(task: asyncio.Task) -> None:
        if task.cancelled() or (error := task.exception()) is None:
            return
        if isinstance(error, ContractError) and (error.code, error.message) == ("unavailable", "TALYX is closing"):
            return
        logging.getLogger("talyx.chart").error("%s subscription failed", task.get_name(), exc_info=error)

    async def close(self) -> None:
        for _, task in self.platform_watches:
            task.cancel()
        for changes, task in self.platform_watches:
            await asyncio.gather(task, return_exceptions=True)  # the callback reports unexpected failures
            await changes.close()
        self.platform_watches.clear()
        for task in self.opening.values():
            task.cancel()
        if self.warming is not None:
            self.warming.cancel()
        await self.alerts.close()
        await self.screens.close()
        await self.watchlists.close()
        await self.hub.close()
        await self.engine.close()
        self.store.close()
