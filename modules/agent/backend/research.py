"""Run a workspace Python report and read pages from attached research documents."""

import asyncio
import json
import os
import sys
from pathlib import Path

from langchain_core.tools import StructuredTool
from pypdf import PdfReader
from talyx_sdk import ContractError, talyx_call
from talyx_sdk.i18n import t
from talyx_sdk.jobs import spawn

from .tools import TOOL_CALL


def research_tools(service, turn, filesystem):
    async def run_python(path: str | None = None, code: str | None = None, stdin_source: str | None = None) -> str:
        """Calculate derived values, including price differences and returns. Supply code alone for a one-off
        calculation without saving a script. To keep or rerun a script, give its workspace path as well or an existing
        path alone. Run a skill's bundled .py by passing its /skills/workspace/... or /skills/global/... path alone.
        Python runs in the workspace folder. A skill script can find its bundled files relative to __file__.
        For a pasted table or text attachment pass its supplied stdin_source. Read the exact original input from
        sys.stdin, including any preamble before the CSV header; do not transcribe the rows.
        Call TALYX in it: from talyx_call import call; call("module.action", {...}) returns the action's JSON. Ask
        for format "file" for bulk data. Read result["file"]["path"] with pandas.read_parquet and compute the answer
        in that same script. Reuse the returned path for further calculations. An action
        that runs a long task returns {taskId} here: start such work with talyx_call_action before the script, or
        poll platform.tasks.get. Print the values you will cite with their series, basis and units: about 8,000 characters each of stdout and stderr
        come back; the full output stays in outputFolder."""
        workspace = turn["workspaceId"]
        root = filesystem.mounts["/workspace/"].cwd
        script = None
        if path is None:
            if code is None:
                raise ContractError("invalid_input", t("Supply code or the path of an existing script"))
            # Keep user code off the Windows command line (length-limited and visible to process viewers).
            # One JSON line carries the code; the remaining bytes are the script's stdin.
            argv = [sys.executable, "-B", "-c", "import json, sys; exec(compile(json.loads(sys.stdin.buffer.readline()), '<calculation>', 'exec'))"]
        else:
            if not path.startswith("/skills/"):
                relative = Path(path.removeprefix("/workspace/"))
                script = (root / relative).resolve()
                if relative.anchor or not script.is_relative_to(root):
                    raise ContractError("invalid_input", t("Choose a Python script inside the workspace folder"))
                workspace_skills = filesystem.mounts["/skills/workspace/"].cwd
                if script.is_relative_to(workspace_skills):
                    path = "/skills/workspace/" + script.relative_to(workspace_skills).as_posix()
            if path.startswith("/skills/"):
                if code is not None:
                    raise ContractError("invalid_input", t("Run a skill script by path alone, without code"))
                script = filesystem.skill_path(path)
            if script.suffix.lower() != ".py":
                raise ContractError("invalid_input", t("Choose a .py script"))
            if code is None and not script.is_file():
                raise ContractError("not_found", t("No script at {path}", path=path))
            argv = [sys.executable, "-B", str(script)]
        stdin = None
        if stdin_source is not None:
            kind, _, identity = stdin_source.partition(":")
            if kind == "message":
                row = service.store.db.execute(
                    "SELECT json_extract(value,'$.text') FROM cards WHERE id=? AND conversation=? "
                    "AND json_extract(value,'$.role')='user'", (identity, turn["conversationId"])).fetchone()
                if row is None:
                    raise ContractError("not_found", t("Choose a user message from this conversation"))
                stdin = row[0].encode("utf-8")
            elif kind == "attachment":
                attachment = service.attachment(identity, turn["conversationId"])
                if not attachment["mime"].startswith("text/") and attachment["mime"] != "application/json":
                    raise ContractError("invalid_input", t("Choose a text attachment for Python input"))
                stdin = await asyncio.to_thread(service.attachment_bytes, attachment)
            else:
                raise ContractError("invalid_input", t("Use the message: or attachment: source supplied with the input"))
        if script is None:
            stdin = (json.dumps(code, ensure_ascii=False) + "\n").encode() + (stdin or b"")
        token = await service.ctx.run_token(workspace, conversation=turn["conversationId"])
        env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "USERPROFILE",
                                                 "LOCALAPPDATA", "APPDATA", "UV_CACHE_DIR", "npm_config_cache")
               if key in os.environ}
        env.update(TALYX_CALL_URL=token["url"], TALYX_RUN_TOKEN=token["token"],
                   PYTHONPATH=str(Path(talyx_call.__file__).parent), PYTHONIOENCODING="utf-8")
        # The complete output stays in the chat's private /.agent/ folder. Only a preview enters the model context.
        outputs = service.root / "files" / turn["conversationId"] / ".agent" / "runs" / token["runId"]
        try:
            outputs.mkdir(parents=True)
            # Files the script writes in the workspace folder are this turn's changes, like the file tools' writes.
            before = await service.changes.scan(workspace, root)
            try:
                if script is not None and code is not None:
                    script.parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(script.write_text, code, encoding="utf-8")
                with (outputs / "stdout.txt").open("wb") as out, (outputs / "stderr.txt").open("wb") as err:
                    process, job = await spawn(argv, env=env, cwd=str(root),
                                               stdin=asyncio.subprocess.DEVNULL if stdin is None else asyncio.subprocess.PIPE,
                                               stdout=out, stderr=err)
                    try:
                        await process.communicate(input=stdin)
                        status = process.returncode
                    finally:
                        job.terminate()
                        await job.ended()
                        job.close()
            finally:
                service.changes.record_run(turn, TOOL_CALL.get(), root, before, await service.changes.scan(workspace, root))
                await service.changes.collect_run(turn, token["runId"])
        finally:
            await service.ctx.call("platform.runs.end", {"runId": token["runId"]})
        def preview(name):
            with (outputs / name).open("r", encoding="utf-8", errors="replace") as stream:
                return stream.read(8000)
        report = {"exitCode": status, "stdout": preview("stdout.txt"), "stderr": preview("stderr.txt"),
                  "outputFolder": "/.agent/runs/" + token["runId"]}
        if status:
            raise ContractError("execution_failed", t("The research script exited with status {status}", status=status), report)
        return json.dumps(report, ensure_ascii=False)

    async def read_pdf(attachment_id: str, page: int) -> str:
        """Read one numbered text page of a PDF attached to this conversation, starting at page 1."""
        selected = service.attachment(attachment_id, turn["conversationId"])
        if selected["mime"] != "application/pdf":
            raise ContractError("invalid_input", t("Choose an attached PDF"))
        def read():
            reader = PdfReader(service.root / "attachments" / attachment_id)
            if not 1 <= page <= len(reader.pages):
                raise ContractError("invalid_input", t("Choose a page from 1 to {value}", value=len(reader.pages)))
            return json.dumps({"page": page, "pages": len(reader.pages), "text": reader.pages[page-1].extract_text()},
                              ensure_ascii=False)
        return await asyncio.to_thread(read)

    return [StructuredTool.from_function(coroutine=run_python), StructuredTool.from_function(coroutine=read_pdf)]


def memory_tools(service, turn):
    """Remember and look up memories: about the user, this workspace's and, in a bot's lines, the bot's own."""
    bot = service.execution_conversation(turn)["botId"]

    async def remember(body: str, scope: str = "bot" if bot else "workspace", frequent: bool = False) -> str:
        with service.store.transaction():
            result = service.memory.write(turn["workspaceId"], turn["conversationId"], body, scope=scope, frequent=frequent)
            service.runtime_card(turn, {"id": result["changeId"], "type": "memory_saved", **result})
            if bot:
                # The bot's run record counts it beside the run's changes, and undoing the run forgets it.
                service.changes.add(turn, "memory", {"callId": result["changeId"], "objectId": result["memory"]["id"],
                    "kind": "created", "label": body, "memory": True, "openTarget": None, "reversible": True})
        return json.dumps({"remembered": body}, ensure_ascii=False)

    async def search_memory(query: str) -> str:
        found = service.memory.search(turn["workspaceId"], turn["conversationId"], query)
        return json.dumps([{"scope": item["scope"], "body": item["body"]} for item in found], ensure_ascii=False)

    scopes = "global (about the user), workspace (this workspace's background, conventions and progress)" + (
        " or bot (your own experience in your duty, which other bots do not see)" if bot else "")
    return [StructuredTool.from_function(coroutine=remember, description=(
                "Remember one lasting thing for later conversations, in the user's wording. Scope: " + scopes + ". "
                "Set frequent for the few core items every request should carry; the rest are found with search_memory.")),
            StructuredTool.from_function(coroutine=search_memory, description=(
                "Find remembered entries containing these words, beyond the core ones already given."))]
