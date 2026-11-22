"""Copy a LangGraph checkpoint and its ancestry into a separate conversation."""

from collections import defaultdict

from langchain_core.messages import ToolMessage
from talyx_sdk import ContractError
from talyx_sdk.i18n import t


def config(thread, namespace="", checkpoint=None):
    value = {"thread_id": thread, "checkpoint_ns": namespace}
    if checkpoint is not None:
        value["checkpoint_id"] = checkpoint
    return {"configurable": value}


def child_namespaces(value):
    if isinstance(value, ToolMessage) and isinstance(value.artifact, dict):
        if namespace := value.artifact.get("checkpoint_ns"):
            yield namespace
    elif isinstance(value, dict):
        for item in value.values():
            yield from child_namespaces(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from child_namespaces(item)


async def copy_thread(saver, source, destination, checkpoint=None):
    head = await saver.aget_tuple(config(source, checkpoint=checkpoint))
    if head is None:
        if checkpoint is not None:
            raise ContractError("not_found", t("This conversation checkpoint no longer exists"))
        return
    if await saver.aget_tuple(config(destination)) is not None:
        raise ContractError("conflict", t("The destination conversation already has a history"))
    pending = {"": head}
    copied = set()
    try:
        while pending:
            namespace, item = pending.popitem()
            copied.add(namespace)
            chain = []
            children = set()
            while item is not None:
                chain.append(item)
                children.update(child_namespaces(item.checkpoint["channel_values"]))
                children.update(child_namespaces(item.pending_writes))
                item = await saver.aget_tuple(item.parent_config) if item.parent_config else None
            parent = config(destination, namespace)
            for item in reversed(chain):
                parent = await saver.aput(parent, item.checkpoint, item.metadata, item.checkpoint["channel_versions"])
                writes = defaultdict(list)
                for task, channel, payload in item.pending_writes or []:
                    writes[task].append((channel, payload))
                for task, batch in writes.items():
                    await saver.aput_writes(parent, batch, task)
            for child in children - copied - pending.keys():
                # Each completed subagent task has its own namespace.
                child_head = await saver.aget_tuple(config(source, child))
                if child_head is None:
                    raise ContractError("not_found", t("A completed subagent's checkpoint is missing"))
                pending[child] = child_head
    except BaseException:
        await saver.adelete_thread(destination)
        raise
