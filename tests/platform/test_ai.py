"""Standard 10: one AI catalog of every public action with its effects; MCP shows the same; answers over 20,000
characters are errors; old call records are deleted."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from talyx_sdk.errors import ContractError

from host.ai import annotations, tool_name, tool_schema
from host.records import CallLog
from tests.platform.harness import run, running


def test_the_catalog_is_every_public_action_with_its_effects_and_mcp_says_the_same(tmp_path):
    async def scenario():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with running(tmp_path) as platform:
            workspace = await platform.workspace()
            index = await platform.call("platform.ai.catalog")
            assert len(json.dumps(index, ensure_ascii=False)) <= 20_000
            catalog = [tool for item in index["modules"] for tool in await platform.ai_tools(item["module"])]
            detail = await platform.call("platform.ai.catalog", {"action": "platform.tasks.get"})
            assert "state" in {f["name"] for f in detail["outputFields"]}
            assert all("outputFields" not in tool for tool in catalog)
            names = {tool["name"] for tool in catalog}
            for owner, contract in platform.host.contracts():
                for name, action in contract["actions"].items():
                    assert (f"{owner}.{name}" in names) == ("callers" not in action or "ai" in action["callers"])
            assert {"echo.echo", "consumer.bench", "late.ping", "platform.settings.read"} <= names
            assert {f"platform.modules.{action}" for action in ("install", "enable", "reload", "disable", "uninstall")} <= names
            assert "platform.notifications.test" in names
            assert all(tool["effects"] for tool in catalog)
            assert all(tool["input"]["type"] == "object" for tool in catalog)
            url = f"http://127.0.0.1:{platform.port}/mcp/workspaces/{workspace}"
            async with streamablehttp_client(url) as (read, write, _), ClientSession(read, write) as session:
                await session.initialize()
                listed = {tool.name: tool for tool in (await session.list_tools()).tools}
                answer = await session.call_tool("echo_whoami", {})
                refused = await session.call_tool("platform_notifications_configure", {})
                assert refused.isError and "permission_denied" in refused.content[0].text
                assert "platform_notifications_configure" not in listed
            for tool in catalog:
                mcp_tool = listed[tool_name(tool["name"])]
                assert mcp_tool.annotations.model_dump(exclude_none=True) == {
                    key: value for key, value in annotations(tool["effects"]).items()}
                assert mcp_tool.meta["talyx/effects"] == tool["effects"]
            assert '"origin":"ai"' in answer.content[0].text
            assert (await platform.call("platform.ai.calls", {"workspaceId": workspace}))[-1]["capability"] == \
                "echo.whoami"

    run(scenario())


def test_open_panel_catalog_follows_installed_panel_declarations(tmp_path):
    from host.app import Host, HostConfig
    from host.catalog import Installed

    host = Host(HostConfig(data_dir=tmp_path / "data", user_dir=tmp_path / "user"))
    def read():
        return host.ai.detail("platform.panels.open")["description"]

    assert "sample.desk" not in read()
    directory = tmp_path / "sample"
    directory.mkdir()
    host.catalog.register(Installed("sample", directory,
        {"name": "Sample", "panels": {"desk": {"title": "Desk", "description": "Explore custom data."},
                                      "hidden": {"title": "Internal", "visibility": "private"}}},
        {"actions": {}}, "installed", "1"))
    host.modules_changed()
    assert 'Desk: {"type": "sample.desk"} — Explore custom data.' in read()
    assert "sample.hidden" not in read()
    host.catalog.uninstall("sample")
    host.modules_changed()
    assert "sample.desk" not in read()


def test_an_answer_over_twenty_thousand_characters_is_an_error_not_cut(tmp_path):
    async def scenario():
        async with running(tmp_path) as platform:
            workspace = await platform.workspace()
            caller = {"origin": "ai", "module": None, "workspaceId": workspace, "ai": {"client": "test"}}
            short = await platform.host.ai.invoke(caller, "echo.long_answer", {"rows": 19_000})
            assert len(short["text"]) == 19_000
            with pytest.raises(ContractError) as error:
                await platform.host.ai.invoke(caller, "echo_long_answer", {"rows": 25_000})
            assert error.value.code == "too_large" and error.value.execution == "failed"
            with pytest.raises(ContractError) as written:
                await platform.host.ai.invoke(caller, "echo.write_long_answer", {"rows": 25_000})
            assert written.value.execution == "unknown" and written.value.effects == "may_have_occurred"
            assert "25011 characters" in written.value.message and "before retrying" in written.value.message

    run(scenario())


def test_call_records_older_than_the_retention_are_deleted(tmp_path):
    log = CallLog(tmp_path / "calls", days=30)
    today = datetime.now(UTC)
    days = [(today - timedelta(days=age)).date().isoformat() for age in (31, 30, 1)]
    log._append([{"callId": day, "caller": {}, "startedAt": day + "T00:00:00Z"} for day in days])
    log.sweep(today)
    with closing(log._connect()) as db:
        assert [row[0] for row in db.execute("SELECT id FROM calls ORDER BY day")] == days[1:]


def test_effects_become_mcp_annotations():
    assert annotations(["read"]) == {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
                                     "openWorldHint": False}
    assert annotations(["write", "funds"])["destructiveHint"] is True
    assert annotations(["external"])["openWorldHint"] is True


def test_a_tool_takes_its_own_object_with_only_the_definitions_it_uses():
    definitions = {"Add": {"type": "object", "properties": {"symbol": {"$ref": "#/$defs/Symbol"}}},
                   "Symbol": {"type": "string"}, "Unused": {"type": "integer"}}
    assert tool_schema({"$ref": "#/$defs/Add", "title": "AddInput"}, definitions) == {
        "type": "object", "title": "AddInput", "properties": {"symbol": {"$ref": "#/$defs/Symbol"}},
        "$defs": {"Symbol": {"type": "string"}}}
    plain = {"type": "object", "properties": {"n": {"type": "integer"}}}
    assert tool_schema(plain, definitions) == plain
    union = tool_schema({"anyOf": [{"$ref": "#/$defs/Add"}, {"type": "object"}]}, definitions)
    assert union["type"] == "object" and set(union["$defs"]) == {"Add", "Symbol"}

def test_module_guide_body_limit_counts_characters(tmp_path):
    from talyx_sdk import ContractError

    from host.catalog import read_guide

    guide = tmp_path / "ABOUT.md"
    header = "---\nname: Example\ndescription: Example module\n---\n"
    guide.write_text(header + "字" * 2500, encoding="utf-8")
    assert len(read_guide(tmp_path, guide.name)["text"]) == 2500
    guide.write_text(header + "字" * 2501, encoding="utf-8")
    with pytest.raises(ContractError, match="2500"):
        read_guide(tmp_path, guide.name)


def test_enabling_module_rechecks_its_guide_before_changing_state(tmp_path):
    from tests.platform.harness import prepare

    async def scenario():
        root = prepare(tmp_path / "modules", ("echo",))
        manifest = root / "echo" / "talyx.module.json"
        declaration = json.loads(manifest.read_text(encoding="utf-8"))
        declaration["ai"] = {"guide":"ABOUT.md"}
        manifest.write_text(json.dumps(declaration), encoding="utf-8")
        guide = root / "echo" / "ABOUT.md"
        header = "---\nname: Echo\ndescription: Echo module\n---\n"
        guide.write_text(header + "short", encoding="utf-8")
        async with running(tmp_path, ("echo",), official_root=root) as platform:
            await platform.host.disable("echo")
            guide.write_text(header + "字" * 2501, encoding="utf-8")
            with pytest.raises(ContractError, match="2500"):
                await platform.host.enable("echo")
            assert not platform.host.catalog.get("echo").enabled
            guide.write_text(header + "字" * 2500, encoding="utf-8")
            await platform.host.enable("echo")
            assert platform.host.catalog.get("echo").enabled
    run(scenario())


def test_output_field_projection_resolves_units_and_limits_depth():
    from host.ai import output_fields

    definitions = {"Rate": {"type": "number", "x-unit": "percent"}}
    schema = {"properties": {"costs": {"type": "object", "properties": {
        "feePct": {"$ref": "#/$defs/Rate"}, "source": {"type": "object", "properties": {"internal": {"type": "string"}}}}}}}
    fields = {item["name"]: item for item in output_fields(schema, definitions)}
    assert fields["costs.feePct"] == {"name": "costs.feePct", "type": "number", "unit": "percent"}
    assert set(fields) == {"costs", "costs.feePct", "costs.source"}


def test_authoring_docs_are_listed_in_the_catalog_and_read_by_topic(tmp_path):
    from tests.platform.harness import prepare

    async def scenario():
        root = prepare(tmp_path / "modules", ("echo",))
        manifest = root / "echo" / "talyx.module.json"
        declaration = json.loads(manifest.read_text(encoding="utf-8"))
        declaration["ai"] = {"guide": "ABOUT.md", "docs": [{"topic": "plugins", "title": "Write a plugin",
                                                           "file": "docs/plugins.md"}]}
        manifest.write_text(json.dumps(declaration), encoding="utf-8")
        (root / "echo" / "ABOUT.md").write_text("---\nname: Echo\ndescription: Echo module\n---\nshort", encoding="utf-8")
        (root / "echo" / "docs").mkdir()
        (root / "echo" / "docs" / "plugins.md").write_text("# Plugins\nHow to write one.", encoding="utf-8")
        async with running(tmp_path, ("echo",), official_root=root) as platform:
            index = await platform.call("platform.ai.catalog")
            echo = next(item for item in index["modules"] if item["module"] == "echo")
            assert echo["docs"] == [{"topic": "plugins", "title": "Write a plugin"}]
            doc = await platform.call("platform.ai.guide", {"module": "echo", "topic": "plugins"})
            assert doc["text"] == "# Plugins\nHow to write one."
            with pytest.raises(ContractError, match="plugins"):
                await platform.call("platform.ai.guide", {"module": "echo", "topic": "missing"})
    run(scenario())


def test_a_doc_outside_the_module_or_named_twice_is_refused(tmp_path):
    from host.catalog import read_docs

    (tmp_path / "a.md").write_text("A", encoding="utf-8")
    assert read_docs(tmp_path, [{"topic": "a", "title": "A", "file": "a.md"}]) == {"a": {"title": "A", "text": "A"}}
    with pytest.raises(ContractError, match="twice"):
        read_docs(tmp_path, [{"topic": "a", "title": "A", "file": "a.md"}] * 2)
    with pytest.raises(ContractError):
        read_docs(tmp_path, [{"topic": "b", "title": "B", "file": "../b.md"}])


def test_installed_module_with_broken_guide_is_listed_and_can_be_retried(tmp_path):
    from tests.platform.harness import prepare

    async def scenario():
        root = prepare(tmp_path / "official", ("echo",))
        manifest = root / "echo" / "talyx.module.json"
        data = json.loads(manifest.read_bytes())
        data["ai"] = {"guide": "GUIDE.md"}
        manifest.write_text(json.dumps(data))
        guide = root / "echo" / "GUIDE.md"
        header = "---\nname: Example\ndescription: Test module\n---\n"
        guide.write_text(header + "x" * 2501)
        async with running(tmp_path, official_root=root, wait=()) as platform:
            row, = await platform.call("platform.modules.list")
            assert row["state"] == "failed" and "2501 characters" in row["problem"]
            assert "echo" not in platform.host.supervisor.processes
            guide.write_text(header + "Working")
            await platform.call("platform.modules.reload", {"id": row["id"]})
            await platform.ready("echo")
            row, = await platform.call("platform.modules.list")
            assert row["state"] == "ready" and row["problem"] is None
    run(scenario())


def test_ai_tests_a_configured_channel_and_reads_the_receipt(tmp_path):
    from unittest.mock import AsyncMock

    from tests.unit.test_notification_channels import configure

    async def scenario():
        async with running(tmp_path, test_modules=()) as platform:
            service = platform.host.notifications
            channel, = await configure(service, "telegram")
            sender = AsyncMock(return_value=(200, {"ok": True, "result": {"message_id": 9}}))
            service.sender = sender
            caller = {"origin": "ai", "module": None, "workspaceId": await platform.workspace(), "ai": {"client": "test"}}
            receipt = await platform.host.ai.invoke(caller, "platform.notifications.test", {"channelId": channel["id"]})
            sender.assert_awaited_once()
            assert receipt["deliveries"][0]["status"] == "sent"
    run(scenario())


@pytest.mark.parametrize("file", ["talyx.module.json", "public/contract.json"])
def test_unreadable_declarations_can_be_retried_without_restarting_other_modules(tmp_path, file):
    from tests.platform.harness import prepare

    async def scenario():
        root = prepare(tmp_path / "official", ("late", "echo"))
        path = root / "late" / file
        original = path.read_bytes()
        path.write_bytes(b"[]")
        async with running(tmp_path, official_root=root, wait=("echo",)) as platform:
            echo = platform.process("echo")
            row, = [item for item in await platform.call("platform.modules.list") if item["state"] == "failed"]
            assert "must be an object" in row["problem"]
            with pytest.raises(ContractError):
                await platform.call("platform.modules.reload", {"id": row["id"]})
            path.write_bytes(original)
            fresh = await platform.call("platform.modules.reload", {"id": row["id"]})
            await platform.ready("late")
            assert fresh["id"] == "late" and platform.process("echo") is echo
            assert not platform.host.catalog.skipped
    run(scenario())
