"""Standard 7, Agent's part: a copied workspace has its own copies of the conversations, with everything the model
remembers of them; the original's stay as they were."""

import asyncio

from langchain_core.messages import AIMessage

from tests.unit.test_agent_runtime import WORKSPACE, agent

COPY = "c0" * 16


async def finished(service, conversation, workspace):
    async with asyncio.timeout(30):
        while conversation in service.runners:
            await asyncio.sleep(.02)
    return service.store.read(conversation, workspace)


def texts(snapshot):
    return [card["text"] for card in snapshot["cards"] if card["type"] == "answer"]


def test_a_copied_conversation_continues_where_the_original_was(tmp_path):
    async def scenario():
        responses = [AIMessage(content="Noted: you hold BTC."), AIMessage(content="You hold BTC."),
                     AIMessage(content="Still BTC.")]
        async with agent(tmp_path, responses) as (service, _platform):
            original = service.store.create(WORKSPACE, model="model")["id"]
            service.attach(original, WORKSPACE, "note.txt", "text/plain", b"BTC 1")
            answer = f"Code and paths keep {original}."
            service.model_runtime.chat_model.responses[0] = AIMessage(content=answer)
            message = f"I hold BTC. Investigation of conversation {original}."
            await service.send(original, WORKSPACE, message, include_view=False)
            before = await finished(service, original, WORKSPACE)

            await service.copy_workspace(WORKSPACE, COPY)

            [listed] = service.store.list(COPY)["items"]
            copy = listed["id"]
            assert copy != original and listed["copiedFrom"] == original and listed["title"] == message[:70]
            await service.copy_workspace(COPY, "third-workspace")
            third = service.store.panel_conversation("third-workspace", copy)
            assert third and third != copy and service.store.panel_conversation("third-workspace", original) == third
            copied = service.store.read(copy, COPY)
            assert texts(copied) == texts(before) == [message, answer]
            assert {card["id"] for card in copied["cards"]}.isdisjoint(card["id"] for card in before["cards"])
            [attachment] = service.store.db.execute("SELECT id FROM attachments WHERE conversation=?", (copy,)).fetchall()
            assert (service.root / "attachments" / attachment[0]).read_bytes() == b"BTC 1"

            # The model in the copy is given what was said in the original.
            await service.send(copy, COPY, "What do I hold?", include_view=False)
            after = await finished(service, copy, COPY)
            assert texts(after)[-1] == "You hold BTC."
            asked = " ".join(str(message.content) for message in service.model_runtime.chat_model.requests[-1])
            assert "I hold BTC" in asked and "What do I hold?" in asked

            # The original is as it was, and goes on by itself.
            assert texts(service.store.read(original, WORKSPACE)) == texts(before)
            assert [item["id"] for item in service.store.list(WORKSPACE)["items"]] == [original]
            await service.send(original, WORKSPACE, "Anything else?", include_view=False)
            await finished(service, original, WORKSPACE)
            asked = " ".join(str(message.content) for message in service.model_runtime.chat_model.requests[-1])
            assert "What do I hold?" not in asked
            assert texts(service.store.read(copy, COPY)) == texts(after)
    asyncio.run(scenario())
