"""Memories are written from their content alone, each undoable; a bot's own are seen by it only."""
import pytest
from talyx_sdk import ContractError

from modules.agent.backend.memory import MemoryService
from modules.agent.backend.store import ConversationStore


@pytest.fixture
def services(tmp_path):
    store = ConversationStore(tmp_path)
    conversation = store.create("workspace")["id"]
    yield store, conversation, MemoryService(store)
    store.close()


def test_memory_write_undo_delete_and_switch_off(services):
    store, conversation, memory = services
    saved = memory.write("workspace", conversation, "Use conservative costs", scope="workspace")
    about = memory.write("workspace", conversation, "Prefers short answers", scope="global", frequent=True)
    assert [item["body"] for item in memory.search("workspace", conversation, "conservative")] == ["Use conservative costs"]
    assert [item["body"] for item in memory.common("workspace", conversation)] == ["Prefers short answers"]
    memory.undo("workspace", conversation, saved["changeId"])
    assert memory.search("workspace", conversation, "conservative") == []
    memory.undo("workspace", conversation, saved["changeId"], redo=True)
    restored, = memory.search("workspace", conversation, "conservative")
    memory.edit("workspace", restored["id"], restored["revision"], restored["body"], delete=True)
    assert memory.search("workspace", conversation, "conservative") == []
    assert [item["body"] for item in memory.list("workspace", status="active")["items"]] == ["Prefers short answers"]
    assert memory.list("elsewhere")["items"] == [about["memory"]]
    with pytest.raises(ContractError, match="global or workspace"):
        memory.write("workspace", conversation, "A bot's own", scope="bot")
    store.update(conversation, "workspace", {"memoryEnabled": False})
    for call in (lambda: memory.search("workspace", conversation, "cost"), lambda: memory.common("workspace", conversation),
                 lambda: memory.write("workspace", conversation, "Another", scope="workspace")):
        with pytest.raises(ContractError, match="switched off"):
            call()


def test_a_bot_sees_its_own_memory_and_the_shared_ones_but_no_other_bot_does(services):
    store, chat, memory = services
    line = store.create("workspace", kind="bot", bot_id="b1")["id"]
    other = store.create("workspace", kind="bot", bot_id="b2")["id"]
    memory.write("workspace", chat, "Workspace convention", scope="workspace", frequent=True)
    memory.write("workspace", line, "Alerts fire at the 4h close", scope="bot", frequent=True)
    assert memory.write("workspace", line, "Bot default scope", scope="bot")["memory"]["botId"] == "b1"
    def seen(conversation):
        return sorted(item["body"] for item in memory.common("workspace", conversation))
    assert seen(line) == ["Alerts fire at the 4h close", "Workspace convention"]
    assert seen(other) == seen(chat) == ["Workspace convention"]
    assert memory.search("workspace", other, "4h close") == []
    assert [item["body"] for item in memory.list("workspace")["items"]] == ["Workspace convention"]
    assert sorted(item["body"] for item in memory.list("workspace", bot="b1")["items"]) == [
        "Alerts fire at the 4h close", "Bot default scope"]
    memory.forget_bot("b1")
    assert memory.list("workspace", bot="b1")["items"] == []

