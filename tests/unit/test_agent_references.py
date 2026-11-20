"""Answer links bind only to web sources the conversation read (its searches, cited sources); any other link falls back to its label."""
import asyncio

from modules.agent.backend.references import References
from modules.agent.backend.store import ConversationStore


def test_answer_binds_bare_quoted_and_markdown_links_to_observed_panels(tmp_path):
    async def scenario():
        store = ConversationStore(tmp_path)
        chat = store.create("desk")["id"]
        turn = store.new_turn(chat, "desk", "Open the chart")
        target = {"module": "example", "panelType": "desk", "objectId": "chart:A/B"}
        store.card(chat, turn["id"], {"id": "chart", "type": "summary", "openTarget": target, "title": "SPY 1D"})
        store.call(turn["id"], "tool1", "call1", "example.create", {"symbol": "SPY", "timeframe": "1d"})
        store.finish_call("call1", result={"openTarget": target})
        store.call(turn["id"], "tool2", "call2", "platform.panels.open", {"type": "example.desk"})
        store.finish_call("call2", result={"panelId": "example-deadbeef"})
        answer = await References(store).answer(turn,
            "（talyx://example/desk/chart%3AA%2FB），`talyx://example/desk/chart:A%2FB` "
            "[Example](talyx://example/desk) `example-deadbeef`", [])
        assert [ref["kind"] for ref in answer["references"]] == ["object", "object", "panel", "panel"]
        assert answer["references"][0]["target"] == target
        assert answer["references"][0]["label"] == "SPY 1D"
        assert answer["references"][2]["target"] == {"type": "example.desk", "panelId": "example-deadbeef"}
        assert answer["text"].startswith("（[[ref:") and "]]），[[ref:" in answer["text"]
        assert "`" not in answer["text"] and "deadbeef" not in answer["text"]
        store.close()

    asyncio.run(scenario())


def test_natural_links_bind_only_observed_sources(tmp_path):
    async def scenario():
        store=ConversationStore(tmp_path)
        conversation=store.create('a')['id']
        turn=store.new_turn(conversation,'a','Cite the observed sources')
        references=References(store)
        store.call(turn['id'],'tool_1','call_1','agent.search_web',{'mode':'search'})
        store.finish_call('call_1',result={'sources':[{'url':'https://example.com/observed'}]},response={})
        store.call(turn['id'],'tool_2','call_2','agent.search_web',{'mode':'page','url':'https://example.com/failed'})
        store.finish_call('call_2',error={'code':'unavailable'})
        chart='talyx://chart/C1?from=2026-10-26T00:00:00Z&to=2026-10-26T12:00:00Z'
        answer=await references.answer(turn,'[Source](https://example.com/observed) [Invented](https://example.com/invented) '
            '[Failed](https://example.com/failed) [Cited](https://example.com/cited) [Chart]('+chart+')',[{'url':'https://example.com/cited'}])
        assert [ref['target']['url'] for ref in answer['references']]==['https://example.com/observed','https://example.com/cited']
        assert answer['text'].startswith('[[ref:') and ' Invented Failed [[ref:' in answer['text'] and answer['text'].endswith(' Chart')
        assert references.read(answer['references'][0]['id'],'a')['href']=='https://example.com/observed'
        target = {'module':'example','panelType':'research','objectId':'A/B'}
        store.card(conversation,turn['id'],{'id':'summary','type':'summary','openTarget':target})
        answer = await references.answer(turn,'[Research](talyx://example/research/A%2FB) [Other](talyx://example/research/B)',[])
        assert [ref['target'] for ref in answer['references']] == [target]
        assert answer['text'].endswith(' Other')
        other = store.new_turn(store.create('a')['id'],'a','Another chat')
        assert (await references.answer(other,'[Research](talyx://example/research/A%2FB) [Source](https://example.com/observed)',[]))['references'] == []
        store.close()
    asyncio.run(scenario())
