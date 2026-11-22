"""Connect the framework's per-tool interrupts to the decisions made on TALYX approval cards."""

import json

from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import ToolMessage
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from .children import CHILD
from .permissions import proposal

FILE_EFFECTS = {"write_file": ["write"], "edit_file": ["write"], "delete": ["delete"], "run_python": ["code"],
                "bot_manage": ["write"], "bot_delete": ["delete"], "bot_read": ["read"]}


def field(data, path):
    for key in path.split("."):
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


class ReviewedCalls(HumanInTheLoopMiddleware):
    """Include the user's recorded decision in the result delivered to the model."""

    def __init__(self, store, turn_id, **options):
        super().__init__(**options)
        self.store, self.turn_id = store, turn_id

    async def awrap_tool_call(self, request, handler):
        result = await super().awrap_tool_call(request, handler)
        card = self.store.approval_for(self.turn_id, request.tool_call["id"])
        if card is None or not isinstance(result, ToolMessage):
            return result
        outcome = {"allowed": "The tool paused for approval. The user approved this call before it ran.",
                   "consumed": "The tool paused for approval. The user approved this call before it ran.",
                   "denied": "The tool paused for approval. The user declined this call; it did not run.",
                   "expired": "The tool waited for user approval. The request expired without execution.",
                   "interrupted": "The tool waited for user approval. The request was interrupted without execution."}
        receipt = {"whatHappened": outcome[card["state"]], "decision": card["decision"], "reason": card["reason"]}
        if isinstance(result.content, list):
            content = [{"type": "text", "text": json.dumps({"approval": receipt})}, *result.content]
        else:
            try:
                value = json.loads(result.content)
            except ValueError:
                value = result.content
            payload = {"result": value, "approval": receipt}
            if isinstance(value, dict) and "status" in value:
                # The graph uses the outer status to wait for an accepted background task.
                payload["status"] = value["status"]
            content = json.dumps(payload, ensure_ascii=False)
        return result.model_copy(update={"content": content})


class Reviews:
    def __init__(self, service, turn, catalog):
        self.service, self.turn, self.catalog = service, turn, catalog
        self.children = {}

    def operation(self, name, args):
        if name != "talyx_call_action":
            return name, args, FILE_EFFECTS.get(name, ["read"]), args.get("file_path") or args.get("path") or args.get("id")
        if not isinstance(args.get("name"), str) or not isinstance(args.get("input"), dict):
            raise ContractError("invalid_input", t("Give talyx_call_action an action name and an input object"))
        action, arguments = args["name"], args["input"]
        if action not in self.catalog:
            raise ContractError("unknown_capability", t("Read the catalog to find {action}", action=action))
        item = self.catalog[action]
        rule = item.get("effectBranches")
        if rule is None:
            return action, arguments, item["effects"], field(arguments, item["target"]) if item["target"] else None
        branch = rule["branches"].get(field(arguments, rule["path"]))
        if branch is None:
            raise ContractError("invalid_input", t("Choose a declared operation in {path}", path=rule['path']))
        target = field(arguments, branch["target"]) if branch.get("target") else None
        return action, arguments, branch["effects"], target

    def decision(self, name, args):
        action, _, effects, target = self.operation(name, args)
        return self.service.decision(self.turn, action, effects, target=target)

    def middleware(self):
        def when(request):
            call = request.tool_call
            if child := CHILD.get():
                self.children[call["id"]] = child["childRunId"]
            if self.service.store.approval_for(self.turn["id"], call["id"]) is not None:
                return True
            try:
                return self.decision(call["name"], call["args"]) == "ask"
            except ContractError:
                return False  # a malformed call never runs; the tool returns the error to the model to correct
        rule = {"when": when, "allowed_decisions": ["approve", "reject"]}
        return ReviewedCalls(self.service.store, self.turn["id"], interrupt_on={name: rule for name in ["talyx_call_action", *FILE_EFFECTS]},
                                       interrupt_mode="per_call")

    def sync(self, interrupts):
        store, turn = self.service.store, self.turn
        with store.transaction():
            mapping = {}
            for item in interrupts:
                value = item.value
                key = value["tool_call_id"]
                saved = store.approval_for(turn["id"], key)
                if saved is None:
                    action, args, effects, _ = self.operation(value["name"], value["args"])
                    saved = store.request_approval(turn["id"], key, key, action, args, proposal(effects),
                                                   child_run_id=self.children.get(key))
                mapping[item.id] = saved["id"]
            current = store.turn(turn["id"])
            store.save_turn({**current, "interrupts": mapping, "state": "waiting_approval" if mapping else "running"})
        return mapping

    @staticmethod
    def decisions(store, turn):
        result = {}
        for interrupt, identity in turn["interrupts"].items():
            card = store.approval(identity, turn["workspaceId"])
            if card["state"] == "allowed":
                result[interrupt] = {"type": "approve"}
            elif card["state"] in {"denied", "expired", "interrupted"}:
                result[interrupt] = {"type": "reject", "message": card["reason"] or "The user declined this action."}
        return result
