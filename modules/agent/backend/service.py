"""Conversation lifecycle and the composition of Agent-owned backend capabilities."""

from __future__ import annotations

import asyncio
import base64
import contextvars
import hashlib
import json
import mimetypes
import re
import shutil
import time
from pathlib import Path
from uuid import uuid4

import aiosqlite
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import checkpoints
from .activities import Activities
from .bots.service import Bots
from .changes import Changes
from .children import ChildRuns
from .completions import Completions
from .engine import ConversationEngine
from .files import Files
from .history import History
from .instructions import InstructionService
from .knowledge import AgentKnowledge
from .memory import MemoryService
from .permissions import ConversationPermissions, proposal
from .profile import Profiles
from .providers import ProviderCatalog, ProviderRuntime, ProviderService
from .providers.errors import PROVIDER_ERRORS, provider_error
from .providers.thinking import level as thinking_level
from .references import References
from .result_files import sweep_results
from .reviews import Reviews
from .search import SearchService
from .skills import SkillsService
from .store import ConversationStore, encode
from .tools import observed_task
from .usage import UsageContext, UsageLedger
from .workspace_copy import copy_workspace

CLIENT = "TALYX Agent"
# How often a running long task of a tool call is read again.
TASK_POLL_SECONDS = 1.0



class AgentService:
    def __init__(self, ctx, directories, *, model_runtime=None):
        self.ctx = ctx
        self.root, self.user_root = Path(directories["dataDir"]), Path(directories["userDir"])
        self.store = ConversationStore(self.root, on_event=self._event)
        self.memory = MemoryService(self.store)
        self.permissions = ConversationPermissions(self.store)
        self.changes = Changes(self)
        self.history = History(self)
        self.files = Files(self)
        self.catalogue = ProviderCatalog(self.root / "models.sqlite")
        self.usage = UsageLedger(self.root / "usage.sqlite")
        self.model_runtime = model_runtime or ProviderRuntime(self.catalogue, self.usage, self.get_secret)
        self.providers = ProviderService(self.catalogue, self.model_runtime)
        self.search = SearchService(self.root / "search.json", self.search_secret, self.usage, catalog=self.catalogue)
        self.skills = SkillsService(self.user_root / "skills")
        self.knowledge = AgentKnowledge(self)
        self.references = References(self.store)
        self.runners, self.observers, self.steps = {}, {}, {}
        self.focused_views = set()
        self.stopping = set()
        self.output_reserves = {}
        self.checkpoint_connection = None
        self.engine = None
        self.bots = Bots(self)
        self.children = ChildRuns(self)
        self.profiles = Profiles(self)
        self.completions = Completions(self)
        self.activities = Activities(self)

    async def start(self):
        await asyncio.to_thread(sweep_results, self.root)
        self.checkpoint_connection = await aiosqlite.connect(self.root / "engine.sqlite")
        saver = AsyncSqliteSaver(self.checkpoint_connection)
        await saver.setup()
        self.engine = ConversationEngine(self, saver)
        await self.providers.start()
        self.rates_task = asyncio.create_task(self.usage.rates.keep_fresh(), context=contextvars.Context())
        for row in self.store.db.execute("SELECT value FROM turns WHERE state='waiting_business'").fetchall():
            await self.refresh_business(json.loads(row[0]))
        for row in self.store.db.execute("SELECT value FROM turns WHERE state IN ('waiting_approval','running')"):
            turn = json.loads(row[0])
            if Reviews.decisions(self.store, turn):
                self.schedule(turn["conversationId"], resume=True)

    def _event(self, event):
        if event["type"] == "turn" or event["type"] == "card" and event["payload"]["type"] == "approval":
            self.bots.worker.ensure(event["workspaceId"])
        self.bots.changed(event["workspaceId"])
        process = self.ctx.process
        payload = {"conversationId": event["conversationId"], "workspaceId": event["workspaceId"], "seq": event["seq"]}
        process.send({"t": "event", "cap": "agent.conversation_changed"},
                     process.codec(process.module.events["conversation_changed"].payload).check(payload))

    async def get_secret(self, reference):
        value = await self.ctx.platform.secret(reference)
        if value is None:
            raise ContractError("credential_missing", t("Configure this credential in Agent settings before using the connection"))
        return value

    async def search_secret(self, reference):
        return await self.ctx.platform.secret(reference)

    async def credential(self, reference):
        """Whether a key is stored, and its last characters for the settings page; the key stays here."""
        value = await self.ctx.platform.secret(reference) if reference else None
        return {"configured": False, "mask": None} if value is None else {"configured": True, "mask": "••••" + value[-4:]}

    async def catalog(self, index):
        """Read each enabled module's declarations from the paginated public catalog."""
        async def pages(key, first=None, **filters):
            items = []
            page = first or await self.ctx.call("platform.ai.catalog", filters)
            while True:
                items.extend(page[key])
                if page["nextCursor"] is None:
                    return items
                page = await self.ctx.call("platform.ai.catalog", {**filters, "cursor": page["nextCursor"]})

        modules = await pages("modules", index)
        batches = await asyncio.gather(*(pages("tools", module=m["module"]) for m in modules))
        return modules, [tool for batch in batches for tool in batch]

    async def ai_call(self, turn, action, arguments):
        """Run an action as this conversation's AI, in its workspace. The platform records the call."""
        return await self.ctx.call("platform.ai.call", {"workspaceId": turn["workspaceId"], "action": action,
                                                       "input": arguments, "client": CLIENT,
                                                       "conversation": turn["conversationId"]})

    def calling_turn(self, call):
        """The turn of one of this Agent's conversations that called an Agent action through the platform."""
        caller = call.caller
        if caller.module != self.ctx.module_id or not caller.ai or not caller.ai["conversation"]:
            return None
        return self.store.active(caller.ai["conversation"])

    async def current_view(self, workspace):
        """What the visible panels of the workspace's window report they show; None without a window."""
        try:
            panels = (await self.ctx.call("platform.panels.list", {}, workspace=workspace))["panels"]
        except ContractError as error:
            if error.code == "ui_required":
                return None
            raise
        shown = [panel for panel in panels if panel["visible"] and panel["view"] is not None
                 and not panel["type"].startswith(self.ctx.module_id + ".")]
        if not shown:
            return None
        return {"workspaceId": workspace, "summary": "; ".join(panel["view"]["summary"] for panel in shown),
                "refs": [panel["view"]["refs"] for panel in shown],
                "facts": [fact for panel in shown for fact in panel["view"].get("facts", [])],
                "openTargets": [target for panel in shown for target in panel["view"].get("openTargets", [])],
                "observedAt": time.time()}

    def decision(self, turn, action, effects, *, target=None):
        """Apply the conversation's permission to the selected operation."""
        value = proposal(effects)
        conversation = self.execution_conversation(turn)
        if not value["risks"]:
            return "run"
        if conversation.get("planMode"):
            return "refuse"
        if conversation["permission"] == "full" or self.permissions.allows(self.permission_owner(conversation), action, value):
            return "run"
        if conversation["permission"] == "read_only":
            return "ask"
        if "delete" in effects:
            return "run" if self.changes.created(conversation, action, target) else "ask"
        return "run"

    def model_result(self, response):
        """What the model reads of a tool call."""
        status = response.get("status")
        if status in {"running", "accepted"}:
            return {"status": "running", "task": response["task"], "result": response["result"],
                    "continuation": "The runtime will deliver completion automatically. Do not poll; answer any "
                                    "independent user question, then wait."}
        # A call that was denied, refused, failed or whose outcome is unknown says so itself.
        return response["result"] if "result" in response else response

    def execution_conversation(self, turn):
        conversation = self.store.conversation(turn["conversationId"], turn["workspaceId"])
        if "childRunId" in turn:
            conversation = {**conversation, "modelId": turn["modelId"], "thinking": turn["thinking"]}
        return conversation

    def configure_defaults(self, changes, workspace=None, *, kind="chat", reset=None):
        for key in ("modelId", "summaryModelId", "childModelId"):
            if key in changes and changes[key] is not None:
                self.catalogue.resolve(changes[key])
        return self.store.configure_defaults(changes, workspace, kind=kind, reset=reset)

    def thinking_choice(self, previous, changes):
        """An explicit level is remembered for its model; switching models without one takes that model's remembered level."""
        model_id = changes["modelId"] if "modelId" in changes else previous["modelId"]
        remembered = self.store.defaults()["modelThinking"]
        if "thinking" in changes and changes["thinking"] is not None:
            if model_id is None:
                raise ContractError("invalid_input", t("Choose a model before its thinking level"))
            level = thinking_level(self.catalogue.thinking(model_id), changes["thinking"])
            self.store.configure_defaults({"modelThinking": {**remembered, model_id: level}})
            return changes
        if "modelId" in changes and changes["modelId"] != previous["modelId"]:
            # A remembered level the model no longer offers (its model info changed) falls back to the model's default.
            levels = self.catalogue.thinking(model_id)["levels"] if model_id is not None else []
            known = model_id in remembered and remembered[model_id] in levels
            return {**changes, "thinking": {"effort": remembered[model_id]} if known else None}
        return changes

    def update_conversation(self, conversation_id, workspace, changes):
        with self.store.transaction():
            previous = self.store.conversation(conversation_id, workspace)
            changes = self.thinking_choice(previous, changes)
            result = self.store.update(conversation_id, workspace, changes)
            levels = {"read_only": 0, "auto": 1, "full": 2}
            if levels[result["permission"]] < levels[previous["permission"]]:
                self.permissions.clear(conversation_id)
        return result

    def permission_owner(self, conversation):
        bot = conversation.get("botId")
        if bot:
            return self.bots.line(conversation["workspaceId"], self.bots.entry(conversation["workspaceId"], bot, "bot"), bot)
        return conversation["id"]

    def usage_context(self, turn):
        bot = self.store.conversation(turn["conversationId"], turn["workspaceId"]).get("botId")
        return UsageContext(workspace_id=turn["workspaceId"], conversation_id=turn["conversationId"], work_run_id=turn["id"],
            group_id=bot,
            **{field: turn[key] for key, field in (("childRunId", "child_run_id"), ("unattended", "unattended")) if key in turn})

    async def instruction_service(self, turn):
        folder = await self.ctx.platform.workspace_folder(turn["workspaceId"])
        bot = self.bots.profile(self.execution_conversation(turn))
        libraries = {"/knowledge/" + f["id"] + "/": Path(f["path"]) for f in self.files.folders(turn["workspaceId"])}
        return InstructionService(self.user_root, folder, {path: root for path, root in libraries.items()
            if not bot or any((choice.rstrip("/") + "/").startswith(path) for choice in bot["knowledge"])})

    def context(self, turn, *, estimated, actual, window):
        if "childRunId" in turn:
            child = self.children.read(turn["childRunId"])
            self.children.save({**child, "context": {"estimatedInputTokens": estimated, "providerInputTokens": actual,
                "window": window, "outputReserve": self.output_reserves[turn["childRunId"]], "observedAt": time.time(), "isEstimate": actual is None}})
            return
        conversation = self.store.conversation(turn["conversationId"])
        previous = conversation["context"]
        context = {"estimatedInputTokens": estimated if estimated is not None else previous["estimatedInputTokens"] if previous else None,
                   "providerInputTokens": actual, "window": window, "outputReserve": self.output_reserves[turn["id"]],
                   "observedAt": time.time(), "isEstimate": actual is None}
        with self.store.transaction():
            value = self.store.save_conversation({**conversation, "context": context})
            self.store.event(conversation["id"], turn["id"], "conversation", value)

    def runtime_card(self, turn, value):
        if "childRunId" in turn:
            self.children.detail(turn, value)
        else:
            self.store.card(turn["conversationId"], turn["id"], value)

    def result_summary(self, turn, result, paths):
        for path in paths:
            found = [result]
            for part in path:
                if part == "*":
                    found = [item for items in found if isinstance(items, list) for item in items]
                else:
                    found = [value[part] for value in found if isinstance(value, dict) and part in value]
            for value in found:
                if isinstance(value, dict) and {"openTarget","keyNumbers","title","subtitle"} <= value.keys():
                    self.runtime_card(turn, {"id": "summary_" + self.store.tool_identity(turn["id"], value["openTarget"]),
                                             "type": "summary", **value})

    async def wake(self, conversation_id, workspace, text, *, cause, request_id):
        """Start one background run on its line through the same graph as a user turn."""
        conversation = self.store.conversation(conversation_id, workspace)
        if conversation["modelId"] is None:
            raise ContractError("model_missing", t("Choose a model for this run"))
        identity = self.store.tool_identity("wake", {"text": text, "cause": cause})
        with self.store.transaction():
            previous = self.store.sent_request(conversation_id, request_id, identity)
            if previous is not None:
                return previous
            turn = self.store.new_turn(conversation_id, workspace, text, wake=cause)
            self.store.remember_send(conversation_id, request_id, identity, turn["id"])
        self.schedule(conversation_id)
        return turn

    def queue(self, conversation_id, workspace, args):
        with self.store.transaction():
            conversation = self.store.conversation(conversation_id, workspace)
            queued = args.get("queued", [*conversation.get("queued", []), args.get("text")])
            if not isinstance(queued, list) or any(not isinstance(s, str) or not s.strip() for s in queued):
                raise ContractError("invalid_input", t("Queue a nonempty message"))
            conversation = self.store.save_conversation({**conversation, "queued": queued})
            self.store.event(conversation_id, None, "conversation", conversation)
        return queued

    async def export(self, conversation_id, workspace):
        conversation = self.store.conversation(conversation_id, workspace)
        rows = self.store.db.execute("SELECT value FROM cards WHERE conversation=? AND json_extract(value,'$.type')='answer' "
                                     "ORDER BY created,rowid", (conversation_id,))
        content = ["# " + conversation["title"]]
        for row in rows:
            message = json.loads(row[0])
            content.extend(["## " + message["role"].capitalize(), message["text"]])
        folder = Path(await self.ctx.platform.workspace_folder(workspace))
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", conversation["title"]).strip(" .")[:100] or "Conversation"
        def write():
            for n in range(1, 10000):
                dest = folder / ((name if n == 1 else f"{name} ({n})") + ".md")
                try:
                    with dest.open("x", encoding="utf-8") as out:
                        out.write("\n\n".join(content) + "\n")
                except FileExistsError:
                    continue
                return dest.name
            raise ContractError("already_exists", t("Choose a different name for this export"))
        return {"name": await asyncio.to_thread(write)}

    async def card_decision(self, operation, args, workspace):
        cid = args["conversationId"]
        self.store.conversation(cid, workspace)
        row = self.store.db.execute("SELECT value,turn_id FROM cards WHERE conversation=? AND id=?", (cid, args["cardId"])).fetchone()
        if row is None:
            raise ContractError("not_found", t("Choose a card in this conversation"))
        card, turn = json.loads(row["value"]), self.store.turn(row["turn_id"])
        if operation == "memory_decision" and card["type"] == "memory_saved":
            return self.knowledge.memory_decision(turn, card, redo=args["direction"] == "redo")
        if operation == "plan_decision" and card["type"] == "plan":
            if card["state"] != "waiting" or type(args.get("accepted")) is not bool:
                raise ContractError("conflict", t("This plan is no longer waiting for a decision"))
            reason = args.get("reason", "")
            if not isinstance(reason, str) or len(reason) > 100000:
                raise ContractError("invalid_input", t("Enter a shorter instruction for the revised plan"))
            with self.store.transaction():
                self.runtime_card(turn, {**card, "state": "accepted" if args["accepted"] else "skipped"})
                if args["accepted"]:
                    self.store.update(cid, workspace, {"planMode": False})
                if args["accepted"] or reason.strip():
                    # The plan is the model's own last answer in this chat; the user's message only says to go ahead.
                    instruction = "Implement the plan." if args["accepted"] else reason.strip()
                    pending = self.store.new_turn(cid, workspace, instruction)
                    self.record_user(pending)
            if args["accepted"] or reason.strip():
                self.schedule(cid)
            return None
        if operation == "retry" and card["type"] == "error":
            if turn["state"] not in {"failed", "budget_exceeded"} or self.store.active(cid):
                raise ContractError("conflict", t("This run cannot be retried now"))
            with self.store.transaction():
                self.store.save_turn({**turn, "state": "queued", "businessContinuation": True, "finishedAt": None, "error": None})
            await self.refresh_business(turn)
            self.schedule(cid)
            return None
        raise ContractError("invalid_input", t("This action does not apply to this card"))

    def admit(self, conversation_id, workspace, attachments=()):
        """The attachments a message to the conversation carries, once its model can take the message: it has a model,
        and the model reads the images among them."""
        conversation = self.store.conversation(conversation_id, workspace)
        if conversation["modelId"] is None:
            raise ContractError("model_missing", t("Add and select a tool-capable model in Agent settings before sending"))
        selected = [self.attachment(item, conversation_id) for item in attachments]
        model = self.catalogue.resolve(conversation["modelId"])
        for attachment in selected:
            if attachment["mime"].startswith("image/") and model["images"] is not True:
                raise ContractError("unsupported", t("This model cannot read image attachments; choose an image-capable model"))
        return selected

    async def send(self, conversation_id, workspace, text, *, include_view=True, attachments=(), supplemental=True):
        selected = self.admit(conversation_id, workspace, attachments)
        view = await self.current_view(workspace) if include_view else None
        return self.send_admitted(conversation_id, workspace, text, view, selected, supplemental=supplemental)

    def send_admitted(self, conversation_id, workspace, text, view, selected, *, supplemental=True):
        active = self.store.active(conversation_id)
        if active is not None and supplemental and active["state"] != "stopping":
            with self.store.transaction():
                if active["state"] == "waiting_approval":
                    # Typing while an approval waits answers it: the action is denied.
                    for row in self.store.db.execute("SELECT value FROM approvals WHERE turn_id=? AND state='waiting'", (active["id"],)).fetchall():
                        approval = json.loads(row[0])
                        self.store.decide(approval["id"], workspace, approval["version"], "deny", text)
                updated = self.store.supplement(active["id"], text, view=view, attachments=[item["id"] for item in selected])
                self.record_user({**active, **updated["inbox"][-1]})
            if active["state"] in {"waiting_business", "waiting_approval"}:
                if active["state"] == "waiting_business":
                    with self.store.transaction():
                        self.store.save_turn({**self.store.turn(active["id"]), "state": "queued", "businessContinuation": True})
                self.schedule(conversation_id, resume=active["state"] == "waiting_approval")
            return self.store.turn(active["id"])
        with self.store.transaction():
            turn = self.store.new_turn(conversation_id, workspace, text, view=view, attachments=[item["id"] for item in selected])
            self.record_user(turn)
        self.schedule(conversation_id)
        return turn

    def record_user(self, turn):
        """A message the user sent: `/compact` in it asks for compaction."""
        self.knowledge.queue_compact(turn, turn)

    @staticmethod
    def input_source(kind, identity, text):
        from .table_inputs import table_facts
        source = f"{kind}:{identity}"
        facts = table_facts(text)
        return ("Source for run_python stdin_source: " + source +
                ("\nExact CSV/TSV counts (header excluded): " + encode(facts) if facts else ""))

    async def input_messages(self, turn, conversation, model):
        checkpoint_id = await self.history.checkpoint(conversation["id"])
        with self.store.transaction():
            self.store.db.execute("UPDATE cards SET value=json_set(value,'$.checkpointId',?) WHERE id=? "
                                  "AND json_type(value,'$.checkpointId') IS NULL", (checkpoint_id, turn["messageId"]))
            self.store.db.execute("UPDATE events SET value=json_set(value,'$.payload.checkpointId',?) WHERE conversation=? "
                                  "AND json_extract(value,'$.payload.id')=? AND json_extract(value,'$.type')='card'",
                                  (checkpoint_id, conversation["id"], turn["messageId"]))
        messages = [HumanMessage(content=turn["text"], id=turn["messageId"])]
        if conversation.get("kind") == "group" and not turn["unattended"]:
            observed = self.bots.public_context(conversation, turn)
            if observed is not None:
                messages.insert(0, observed)
        blocks = []
        if "\n" in turn["text"] and (turn["wake"]["kind"] == "user" or "arrivalSequence" in turn):
            blocks.append({"type":"text", "text": self.input_source("message", turn["messageId"], turn["text"])})
        for attachment_id in turn["attachments"]:
            attachment = self.attachment(attachment_id, conversation["id"])
            if attachment["mime"].startswith("image/") and model["images"] is not True:
                raise ContractError("unsupported", t("The newly selected model cannot read this image attachment"))
            if attachment["mime"] == "application/pdf" and model["pdf"] is not True:
                blocks.append({"type": "text", "text": f"Attached PDF {attachment['name']}. Use read_pdf with "  # i18n: ignore (model input)
                               f"attachment_id={attachment_id} and a 1-based page number."})
                continue
            data = await asyncio.to_thread(self.attachment_bytes, attachment)
            if attachment["mime"].startswith("image/"):
                blocks.append({"type": "image", "base64": base64.b64encode(data).decode(), "mime_type": attachment["mime"]})
            elif attachment["mime"] == "application/pdf":
                blocks.append({"type": "file", "base64": base64.b64encode(data).decode(), "mime_type": "application/pdf", "filename": attachment["name"]})
            else:
                text = data.decode("utf-8-sig")
                blocks.append({"type": "text", "text": "Attachment " + attachment["name"] + ":\n" + text})
                blocks.append({"type": "text", "text": self.input_source("attachment", attachment_id, text)})
        if turn["view"] is not None:
            blocks.append({"type": "text", "text": "Current workspace view (observed when the user sent this message):\n" +
                  encode({key: value for key, value in turn['view'].items() if key != 'workspaceId'})})
        else:
            blocks.append({"type":"text", "text":"No panel was shared with this message. Use the named instrument or object directly."})  # i18n: ignore (model input)
        if blocks:
            messages.append(HumanMessage(content=blocks, id="materials_" + turn["messageId"]))
        return messages

    async def drain_messages(self, turn):
        conversation = self.execution_conversation(turn)
        model = self.catalogue.resolve(conversation["modelId"])
        groups = []
        for item in self.store.drain(turn["id"]):
            groups.append((item['arrivalSequence'], await self.input_messages({**turn, **item}, conversation, model)))
        groups.extend(self.completions.groups(turn))
        return [message for _arrival, messages in sorted(groups, key=lambda group: group[0]) for message in messages]

    async def reconcile_history(self, conversation):
        """Calls an earlier run left unfinished: a call that was on its way has an unknown outcome; a long task that
        still runs is followed again."""
        observations = []
        rows = self.store.db.execute("SELECT c.value,c.turn_id FROM calls c LEFT JOIN cards p ON p.id='task_'||c.id WHERE c.conversation=? AND "
            "(json_extract(c.value,'$.state')='started' OR json_extract(p.value,'$.state')='interrupted')", (conversation["id"],)).fetchall()
        for row in rows:
            call = json.loads(row[0])
            if call["state"] == "started":
                error = {"code": "unconfirmed", "message": t("TALYX stopped while this call ran; its effects are unconfirmed"),
                         "execution": "unknown", "effects": "may_have_occurred"}
                self.store.finish_call(call["callId"], error=error)
                observations.append({"callId": call["callId"], "action": call["action"], "status": "unknown", "error": error})
                continue
            try:
                task = await self.ctx.call("platform.tasks.get", {"taskId": call["response"]["taskId"]})
            except ContractError as error:
                observations.append({"callId": call["callId"], "action": call["action"], "status": "unknown", "error": error.to_dict()})
                continue
            observations.append(observed_task(task))
            await self.watch_business(self.store.turn(row["turn_id"]), call["callId"])
        return observations

    def schedule(self, conversation_id, *, resume=False):
        if conversation_id in self.runners:
            return
        conversation = self.store.conversation(conversation_id)
        task = self.background(conversation["workspaceId"], conversation["title"], self._run(conversation_id, resume=resume))
        self.runners[conversation_id] = task
        def finished(completed):
            self.runners.pop(conversation_id, None)
            if completed.cancelled():
                pending = self.store.active(conversation_id)
                if pending is not None and pending["state"] == "queued":
                    self.schedule(conversation_id)
                return
            error = completed.exception()
            if error is not None:
                turn = self.store.active(conversation_id)
                if turn is not None:
                    failure = {"code": "agent_internal_error", "message": t("Agent stopped after an internal failure; review the recorded calls before continuing"),
                               "details": {"type": type(error).__name__, "execution": "unknown", "effects": "may_have_occurred"}}
                    self.store.state(turn["id"], "failed", error=failure)
                    self.store.card(conversation_id, turn["id"], {"id": "error_" + turn["id"], "type": "error", **failure})
                asyncio.get_running_loop().call_exception_handler({"message": "Agent graph failed", "exception": error, "task": completed})  # i18n: ignore (log)
            pending = self.store.active(conversation_id)
            if pending is not None and pending["state"] == "queued":
                self.schedule(conversation_id)
            elif pending is not None and pending["interrupts"] and Reviews.decisions(self.store, pending):
                self.schedule(conversation_id, resume=True)
        task.add_done_callback(finished)

    def run_steps(self, turn):
        """The steps on a run's process card: those the running pass keeps, else read back from the card."""
        runtime = turn.get("childRunId", turn["id"])
        if runtime in self.steps:
            return self.steps[runtime]
        if "childRunId" in turn:
            row = self.store.db.execute("SELECT value FROM child_details WHERE child=? AND card_id=? ORDER BY seq DESC LIMIT 1",
                                        (runtime, "process_" + runtime)).fetchone()
        else:
            row = self.store.db.execute("SELECT value FROM cards WHERE id=?", ("process_" + runtime,)).fetchone()
        return {s["id"]: s for s in json.loads(row[0])["steps"]} if row else {}

    def background(self, workspace, title, coroutine):
        """Work of a conversation runs as AI work of its workspace: the platform lists it and stops it when the
        workspace closes or the user stops what AI is doing."""
        activity = self.ctx.activity(workspace, title, origin="ai")
        return activity.add_task(asyncio.get_running_loop().create_task(coroutine))

    async def _run(self, conversation_id, *, resume):
        while (turn := self.store.active(conversation_id)) is not None:
            if turn["state"] == "waiting_approval" and not (resume and Reviews.decisions(self.store, turn)):
                return
            self.store.state(turn["id"], "running")
            try:
                minutes = self.store.defaults(turn["workspaceId"], kind="bot")["botRunMinutes"] if turn["unattended"] else 0
                used_seconds = turn.get("activeSeconds", 0)
                started = time.monotonic()
                try:
                    async with asyncio.timeout(max(0, minutes * 60 - used_seconds) if minutes else None):
                        completed = await self.engine.run(turn, resume=resume)
                finally:
                    if turn["unattended"]:
                        with self.store.transaction():
                            current = self.store.turn(turn["id"])
                            self.store.save_turn({**current, "activeSeconds": used_seconds + time.monotonic() - started})
                if not completed:
                    return
                self.changes.card(turn)
                self.store.state(turn["id"], "completed")
                await self.notify_finished(turn)
                await self.refresh_business(turn)
                current = self.store.conversation(conversation_id)
                if current.get("queued"):
                    with self.store.transaction():
                        first, *remaining = current["queued"]
                        self.queue(conversation_id, turn["workspaceId"], {"queued": remaining})
                        queued = self.store.new_turn(conversation_id, turn["workspaceId"], first)
                        self.record_user(queued)
            except TimeoutError:
                error = {"code": "run_timeout", "message": t("This background run reached its working time limit.")}
                self.store.state(turn["id"], "failed", error=error)
                self.runtime_card(turn, {"id": "error_" + turn["id"], "type": "error", **error})
            except ContextOverflowError:
                failure = {"code": "context_window_exceeded", "message": t("The request and selected tools do not fit this model's context window. Reduce the input or choose a larger window.")}
                self.store.state(turn["id"], "failed", error=failure)
                self.store.card(conversation_id, turn["id"], {"id": "error_" + turn["id"], "type": "error", **failure})
            except asyncio.CancelledError:
                self.store.state(turn["id"], "cancelled" if turn["id"] in self.stopping else "interrupted")
                raise
            except ContractError as error:
                self.store.state(turn["id"], "budget_exceeded" if error.code == "budget_exceeded" else "failed", error=error.to_dict())
                self.store.card(conversation_id, turn["id"], {"id": "error_" + turn["id"], "type": "error", "code": error.code,
                    "message": error.message, "details": error.details})
                if error.code == "budget_exceeded" and self.usage.claim_limit_notification():
                    await self.ctx.call("platform.notifications.toast", {"title": t("Agent spending limit reached"),
                        "message": t("Priced model requests have reached this month's spending limit.")}, workspace=turn["workspaceId"])
            except PROVIDER_ERRORS as error:
                failure = provider_error(error)
                self.store.state(turn["id"], "failed", error=failure)
                self.store.card(conversation_id, turn["id"], {"id": "error_" + turn["id"], "type": "error", **failure})
            except GraphRecursionError:
                failure = {"code": "step_limit", "message": t("The agent reached its turn step limit; review its completed work before continuing")}
                self.store.state(turn["id"], "failed", error=failure)
                self.store.card(conversation_id, turn["id"], {"id": "error_" + turn["id"], "type": "error", **failure})
            finally:
                # What this pass of the run kept in memory; a later pass reads its steps back from the card.
                self.steps.pop(turn["id"], None)
                self.output_reserves.pop(turn["id"], None)
                self.stopping.discard(turn["id"])
            resume = False

    async def notify_finished(self, turn):
        owner = self.execution_conversation(turn)
        if owner.get("kind", "chat") != "chat":
            return
        policy = self.store.defaults(turn["workspaceId"])["chatNotification"]
        if policy == "never" or policy == "background" and self.focused_views:
            return
        await self.ctx.call("platform.notifications.toast", {"title":owner["title"],"message":t("Chat finished"),
            "navigation":{"workspaceId":turn["workspaceId"],"panel":"agent.chat","state":{"conversationId":owner["id"]}}},
            workspace=turn["workspaceId"])

    async def decide(self, approval_id, workspace, version, decision, reason=""):
        with self.store.transaction():
            approval = self.store.decide(approval_id, workspace, version, decision, reason)
            if decision == "allow_conversation":
                conversation = self.store.conversation(approval["conversationId"], workspace)
                self.permissions.remember({**approval, "conversationId": self.permission_owner(conversation)})
        self.schedule(approval["conversationId"], resume=True)
        return approval

    async def stop(self, conversation_id, workspace):
        self.store.conversation(conversation_id, workspace)
        turns = [json.loads(row[0]) for row in self.store.db.execute("SELECT value FROM turns WHERE conversation=? AND state IN ('queued','running','waiting_approval','waiting_business','stopping') ORDER BY created", (conversation_id,))]
        for turn in turns:
            self.stopping.add(turn["id"])
            self.store.state(turn["id"], "stopping")
        for row in self.store.db.execute("SELECT id FROM child_runs WHERE conversation=? AND json_extract(value,'$.state') NOT IN ('completed','failed','cancelled','interrupted')", (conversation_id,)).fetchall():
            await self.children.stop(row[0], workspace, conversation_id, resume_parent=False)
        pending = self.store.pending_business_turns(conversation_id)
        for turn in turns:
            self.store.invalidate_approvals(turn["id"], "This turn was stopped; its approval is no longer valid")
            if turn["id"] not in pending:
                pending.append(turn["id"])
        task = self.runners.get(conversation_id)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for turn in turns:
            self.store.state(turn["id"], "cancelled")
        for turn_id in pending:
            await self.stop_turn_business(self.store.turn(turn_id))
        return self.store.turn(pending[-1]) if pending else {"state": "idle"}

    async def stop_turn_business(self, turn):
        await self.activities.stop(turn)
        self.store.invalidate_approvals(turn["id"], "This turn's tasks were stopped; its approvals are no longer valid")
        pending = [self.observers.pop(call["callId"]) for call in self.store.calls(turn["id"]) if call["callId"] in self.observers]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for call in self.store.calls(turn["id"]):
            await self.cancel_business(turn, call)

    async def cancel_business(self, turn, call):
        """Cancel the long task a tool call started, if it still runs, and show how it ended."""
        if call["response"] is None or call["response"]["status"] != "accepted":
            return
        task = await self.ctx.call("platform.tasks.get", {"taskId": call["response"]["taskId"]})
        if task["state"] == "running":
            await self.ctx.call("platform.tasks.cancel", {"taskId": task["taskId"]})
        await self.watch_business(turn, call["callId"])

    async def refresh_business(self, turn):
        for call in self.store.calls(turn["id"]):
            if call["response"] is not None and call["response"]["status"] == "accepted":
                seen = self.store.db.execute("SELECT 1 FROM completion_inbox WHERE call_id=?", (call["callId"],)).fetchone()
                if seen is None:
                    await self.watch_business(turn, call["callId"])

    async def watch_business(self, turn, call_id):
        """Follow the long task a tool call started until it ends; the conversation continues with its result."""
        if call_id in self.observers:
            return
        original = next(item for item in self.store.calls(turn["id"]) if item["callId"] == call_id)
        turn = self.call_turn(turn, original)
        task_id = original["response"]["taskId"]

        async def project(observed):
            business = observed["business"]
            runtime_id = turn["childRunId"] if "childRunId" in turn else turn["id"]
            steps = self.run_steps(turn)
            failure = observed["result"].get("error") if isinstance(observed["result"], dict) else None
            step = next((item for item in steps.values() if item.get('callId') == call_id), None)
            if step is not None:
                step.update(state=business["status"], error=failure)
                if business["terminal"] and step["finishedAt"] is None:
                    step["finishedAt"] = observed["observedAt"]
                self.runtime_card(turn, {"id": "process_" + runtime_id, "type": "process_group",
                    "title": t("Working"), "steps": list(steps.values())})
            self.runtime_card(turn, {"id": "task_" + call_id, "type": "result" if business["terminal"] else "task",
                "callId": call_id, "state": business["status"], "action": business["action"], "op": None,
                "task": observed["task"],
                "title": step["title"] if step is not None else business["action"].split('.')[-1].replace('_', ' ').capitalize(),
                "result": observed["result"],
                "summary": observed["result"].get("summary") if isinstance(observed["result"], dict) else None,
                "error": failure, "openTarget": None})
            if business["terminal"]:
                profile = await self.profiles.read(turn)
                action = next((item for item in profile["tools"] if item["action"] == business["action"]), {})
                self.result_summary(turn, observed["result"], action.get("summaryPaths", []))
                self.completions.put(turn, call_id, observed)

        async def read_original():
            try:
                return observed_task(await self.ctx.call("platform.tasks.get", {"taskId": task_id}))
            except ContractError as error:
                if error.code != "not_found":
                    raise
                return {"business":{"action":original["action"],"status":"unknown","terminal":True},
                        "result":{"error":{"code":"result_unknown","message":t("The original task ended or expired while disconnected. Its result is unknown.")}},
                        "task":{"taskId":task_id,"progress":None},"observedAt":time.time()}

        initial = await read_original()
        await project(initial)
        if initial["business"]["terminal"]:
            return

        async def observe():
            while True:
                await asyncio.sleep(TASK_POLL_SECONDS)
                observed = await read_original()
                if observed["business"]["terminal"] or observed["task"] != initial["task"]:
                    await project(observed)
                if observed["business"]["terminal"]:
                    return
        task = self.background(turn["workspaceId"], self.store.conversation(turn["conversationId"])["title"], observe())
        self.observers[call_id] = task
        def finished(completed):
            if self.observers.get(call_id) is completed:
                self.observers.pop(call_id, None)
        task.add_done_callback(finished)

    def call_turn(self, turn, call):
        if call["childRunId"] is not None:
            child = self.children.read(call["childRunId"])
            turn = {**turn, "childRunId": child["id"], "modelId": child["modelId"], "thinking": child["thinking"]}
        return turn

    def attachment(self, attachment_id, conversation_id):
        row = self.store.db.execute("SELECT conversation,value FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if row is None or row["conversation"] != conversation_id:
            raise ContractError("target_gone", t("This attachment is unavailable in the conversation"))
        value = json.loads(row["value"])
        if value["state"] != "complete":
            raise ContractError("conflict", t("This attachment upload is not complete"))
        return value

    def attachment_bytes(self, attachment):
        data = (self.root / "attachments" / attachment["id"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != attachment["digest"]:
            raise ContractError("conflict", t("An immutable attachment changed on disk; attach it again"))
        return data

    def attach(self, conversation_id, workspace, name, mime, content, *, attachment_id=None):
        self.store.conversation(conversation_id, workspace)
        if len(content) > 10 * 1024 * 1024:
            raise ContractError("attachment_too_large", t("Each attachment may contain at most 10 MiB"))
        if mime not in {"image/png", "image/jpeg", "image/webp", "application/pdf", "text/plain", "text/markdown", "text/csv", "application/json"}:
            raise ContractError("unsupported", t("Attach a supported image, PDF or UTF-8 text file"))
        if mime not in {"image/png", "image/jpeg", "image/webp", "application/pdf"}:
            try:
                content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ContractError("invalid_input", t("Text attachments must use UTF-8")) from error
            if len(content) > 100000:
                raise ContractError("attachment_too_large", t("Text attachments may contain at most 100000 bytes"))
        if not name or len(name) > 200 or Path(name).name != name:
            raise ContractError("invalid_input", t("Use a file name without a directory path"))
        attachment_id = attachment_id or uuid4().hex
        directory = self.root / "attachments"
        directory.mkdir(exist_ok=True)
        (directory / attachment_id).write_bytes(content)
        value = {"id": attachment_id, "name": name, "mime": mime or mimetypes.guess_type(name)[0],
                 "size": len(content), "digest": hashlib.sha256(content).hexdigest(), "state": "complete"}
        with self.store.transaction():
            self.store.db.execute("INSERT OR REPLACE INTO attachments VALUES (?,?,?)", (attachment_id, conversation_id, encode(value)))
        return value

    def upload_start(self, conversation_id, workspace, name, mime, size):
        self.store.conversation(conversation_id, workspace)
        if type(size) is not int or not 0 <= size <= 10 * 1024 * 1024:
            raise ContractError("attachment_too_large", t("Each attachment may contain at most 10 MiB"))
        if not name or len(name) > 200 or Path(name).name != name:
            raise ContractError("invalid_input", t("Use a file name without a directory path"))
        attachment_id = uuid4().hex
        directory = self.root / "attachments"
        directory.mkdir(exist_ok=True)
        (directory / attachment_id).touch(exist_ok=False)
        value = {"id": attachment_id, "name": name, "mime": mime, "size": size, "state": "uploading", "received": 0}
        with self.store.transaction():
            self.store.db.execute("INSERT INTO attachments VALUES (?,?,?)", (attachment_id, conversation_id, encode(value)))
        return value

    def upload_chunk(self, conversation_id, workspace, attachment_id, offset, data):
        self.store.conversation(conversation_id, workspace)
        row = self.store.db.execute("SELECT conversation,value FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if row is None or row["conversation"] != conversation_id:
            raise ContractError("target_gone", t("This attachment upload is unavailable"))
        value = json.loads(row["value"])
        if value["state"] != "uploading" or value["received"] != offset:
            raise ContractError("conflict", t("The attachment upload offset changed"))
        try:
            content = base64.b64decode(data, validate=True)
        except ValueError as error:
            raise ContractError("invalid_input", t("Attachment chunks must be valid base64")) from error
        if len(content) > 256 * 1024 or offset + len(content) > value["size"]:
            raise ContractError("invalid_input", t("Upload at most 256 KiB per chunk within the declared file size"))
        with (self.root / "attachments" / attachment_id).open("r+b") as stream:
            stream.seek(0, 2)
            if stream.tell() != offset:
                raise ContractError("conflict", t("The partial upload changed on disk; attach the file again"))
            stream.write(content)
        value["received"] += len(content)
        with self.store.transaction():
            self.store.db.execute("UPDATE attachments SET value=? WHERE id=?", (encode(value), attachment_id))
        return value

    def upload_finish(self, conversation_id, workspace, attachment_id):
        self.store.conversation(conversation_id, workspace)
        row = self.store.db.execute("SELECT conversation,value FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if row is None or row["conversation"] != conversation_id:
            raise ContractError("target_gone", t("This attachment upload is unavailable"))
        value = json.loads(row["value"])
        if value["state"] == "complete":
            return value
        if value["received"] != value["size"]:
            raise ContractError("conflict", t("Upload all attachment bytes before finishing"))
        content = (self.root / "attachments" / attachment_id).read_bytes()
        if len(content) != value["size"]:
            raise ContractError("conflict", t("The partial upload changed on disk"))
        return self.attach(conversation_id, workspace, value["name"], value["mime"], content, attachment_id=attachment_id)

    def remove_attachment(self, conversation_id, workspace, attachment_id):
        self.store.conversation(conversation_id, workspace)
        row = self.store.db.execute("SELECT conversation FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if row is None or row[0] != conversation_id:
            raise ContractError("target_gone", t("This attachment is unavailable"))
        for row in self.store.db.execute("SELECT value FROM cards WHERE conversation=? AND json_extract(value,'$.type')='answer' AND json_extract(value,'$.role')='user'", (conversation_id,)):
            if attachment_id in json.loads(row[0])["attachments"]:
                raise ContractError("conflict", t("This immutable attachment belongs to a sent message"))
        (self.root / "attachments" / attachment_id).unlink(missing_ok=True)
        with self.store.transaction():
            self.store.db.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))

    async def copy_workspace(self, source, copy):
        """A copied workspace gets its own copies of the workspace's conversations and memories."""
        if source == copy or self.store.db.execute("SELECT 1 FROM conversations WHERE workspace=?", (copy,)).fetchone():
            raise ContractError("conflict", t("Choose a new workspace for the copy"))
        try:
            for original, copied in copy_workspace(self, source, copy).items():
                await checkpoints.copy_thread(self.engine.checkpointer, self.history.thread(original), copied)
                private = self.root / "files"
                if (private / original).is_dir():
                    await asyncio.to_thread(shutil.copytree, private / original, private / copied)
        except BaseException:
            await self.delete_workspace(copy, preview=False)
            raise

    async def delete(self, conversation_id, workspace):
        self.store.conversation(conversation_id, workspace)
        if self.store.active(conversation_id) is not None or self.store.pending_business_turns(conversation_id):
            raise ContractError("conflict", t("Stop the conversation before deleting it"))
        pending = [self.observers[call["callId"]] for row in self.store.db.execute("SELECT id FROM turns WHERE conversation=?", (conversation_id,))
                   for call in self.store.calls(row[0]) if call["callId"] in self.observers]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        rows = self.store.db.execute("SELECT id FROM attachments WHERE conversation=?", (conversation_id,)).fetchall()
        for row in rows:
            (self.root / "attachments" / row[0]).unlink(missing_ok=True)
        self.permissions.clear(conversation_id)
        threads = {conversation_id}
        threads.update(row[0] for row in self.store.db.execute("SELECT thread FROM graph_branches WHERE conversation=?", (conversation_id,)))
        for thread in threads:
            await self.engine.checkpointer.adelete_thread(thread)
        await self.children.delete(conversation_id)
        self.knowledge.release(workspace, conversation_id)
        root = (self.root / "files").resolve()
        folder = (root / conversation_id).resolve()
        if folder.parent == root and folder.is_dir():
            await asyncio.to_thread(shutil.rmtree, folder)
        with self.store.transaction():
            self.store.db.execute("DELETE FROM graph_branches WHERE conversation=?", (conversation_id,))
            self.store.db.execute("DELETE FROM bot_routines WHERE conversation=?", (conversation_id,))
            self.store.db.execute("DELETE FROM bot_wakes WHERE conversation=? AND bot IS NULL", (conversation_id,))
            self.store.db.execute("DELETE FROM file_changes WHERE call_id IN (SELECT call_id FROM turn_changes WHERE conversation=?)", (conversation_id,))
            self.changes.forget_images()
            self.store.db.execute("DELETE FROM turn_changes WHERE conversation=?", (conversation_id,))
            self.store.db.execute("DELETE FROM turn_activities WHERE turn_id IN (SELECT id FROM turns WHERE conversation=?)", (conversation_id,))
            self.store.db.execute("DELETE FROM agent_memory_changes WHERE conversation=?", (conversation_id,))
            self.store.db.execute('DELETE FROM completion_inbox WHERE conversation=?', (conversation_id,))
            self.store.delete(conversation_id, workspace)

    async def delete_workspace(self, workspace, *, preview):
        db = self.store.db
        conversations = [json.loads(row[0]) for row in db.execute("SELECT value FROM conversations WHERE workspace=?", (workspace,))]
        bots = await self.bots.delete_workspace(workspace, preview=preview)
        if preview:
            return [*bots, *("Chat · " + c["title"] for c in conversations if c.get("kind", "chat") == "chat"),
                    *("Memory · " + json.loads(row[0])["body"] for row in db.execute(
                        "SELECT value FROM agent_memories WHERE workspace=? AND status='active'", (workspace,)))]
        for c in conversations:
            if self.store.active(c["id"]):
                await self.stop(c["id"], workspace)
            await self.delete(c["id"], workspace)
        with self.store.transaction():
            db.execute("DELETE FROM agent_memory_search WHERE id IN (SELECT id FROM agent_memories WHERE workspace=?)", (workspace,))
            db.execute("DELETE FROM agent_memories WHERE workspace=?", (workspace,))
            db.execute("DELETE FROM knowledge_folders WHERE workspace=?", (workspace,))
            db.execute("DELETE FROM workspace_files WHERE workspace=?", (workspace,))
            self.changes.forget_images()
            db.execute("DELETE FROM scoped_defaults WHERE workspace=?", (workspace,))
        return []

