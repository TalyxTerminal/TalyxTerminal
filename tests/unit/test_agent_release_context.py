"""Regressions from the A12 conversation in the release trial."""
import asyncio
import re
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage
from talyx_sdk import ContractError

from tests.unit.test_agent_runtime import WORKSPACE, agent, cards, settled

# Original agreement and first successful summary excerpt from qa-C-phase3.json.
AGREEMENT = ("A12-约定 我们核算的是 QA3 Human Ridge 的第 1 次运行，项目代号“青檀-407”。起始现金 10003.25 USD，"
             "预留现金 37.75 USD，买入 17 股，整笔只扣固定手续费 2.40 USD。用户规则是按纽约交易日、用拆股调整口径，"
             "末行写 SEA-LIME-407。这些只属于当前会话，不写进记忆、文件或其他地方。")
SUMMARY = ("项目代号“青檀-407”；核算对象为 QA3 Human Ridge 第 1 次运行；起始现金 10003.25 USD，预留现金 37.75 USD，"
           "买入 17 股，整笔只扣固定手续费 2.40 USD；交易日按纽约交易日，价格用拆股调整口径；末行写 `SEA-LIME-407`。")


def test_same_turn_retry_reuses_completed_summary_and_keeps_the_trade(tmp_path):
    async def scenario():
        async with agent(tmp_path, [AIMessage(content="收到")]) as (service, _):
            conf = service.catalogue.model("model").model_dump()
            service.catalogue.put_model({**conf, "id":"summary"})
            service.configure_defaults({"summaryModelId":"summary", "autoCompact":False}, WORKSPACE)
            cid = service.store.create(WORKSPACE, model="model")["id"]
            service.store.update(cid, WORKSPACE, {"memoryEnabled":False})
            for prompt in (AGREEMENT, "第一批", "第二批", "第三批"):
                await service.send(cid, WORKSPACE, prompt, include_view=False)
                assert (await settled(service, cid))["turns"][-1]["state"] == "completed"

            original = service.model_runtime.model
            summaries = []

            @asynccontextmanager
            async def model(model_id, **opts):
                async with original(model_id, **opts) as lease:
                    if model_id == "summary":
                        async def invoke(messages):
                            summaries.append(messages)
                            return AIMessage(content=SUMMARY)
                        lease.invoke = invoke
                    yield lease
            service.model_runtime.model = model
            service.configure_defaults({"autoCompact":True, "compactAt":1}, WORKSPACE)
            service.catalogue.put_model({**conf, "contextWindow":512})
            turn = await service.send(cid, WORKSPACE, "先核对这一批，不要复述约定。", include_view=False)
            failed = await settled(service, cid)
            assert failed["turns"][-1]["state"] == "failed" and len(summaries) == 1
            assert "青檀-407" in str(summaries[0]) and "long-term memory" in summaries[0][0].text
            assert len(cards(failed, "compaction")) == 1
            service.catalogue.put_model(conf)
            service.configure_defaults({"compactAt":90, "autoCompact":False}, WORKSPACE)
            await service.card_decision("retry", {"conversationId":cid, "cardId":"error_" + turn["id"]}, WORKSPACE)
            recovered = await settled(service, cid)
            assert recovered["turns"][-1]["state"] == "completed"
            assert len(summaries) == 1 and len(cards(recovered, "compaction")) == 1
            service.configure_defaults({"autoCompact":False}, WORKSPACE)
            await service.send(cid, WORKSPACE, "执行价换为 252.40 USD。按原约定扣除预留，给出可用现金。", include_view=False)
            await settled(service, cid)
            messages = service.model_runtime.chat_model.requests[-1]
            summary_message, = [m for m in messages if SUMMARY in m.text]
            assert not any(AGREEMENT in m.text for m in messages)
            principal, reserve, shares, fee = re.search(
                r"起始现金 ([\d.]+) USD，预留现金 ([\d.]+) USD，买入 (\d+) 股，整笔只扣固定手续费 ([\d.]+) USD",
                summary_message.text).groups()
            assert Decimal(principal) - Decimal(reserve) - Decimal(shares) * Decimal("252.40") - Decimal(fee) == Decimal("5672.30")
    asyncio.run(scenario())


def test_explicit_output_limit_and_profile_reserve_share_the_configured_window(tmp_path):
    async def scenario():
        async with agent(tmp_path, [AIMessage(content="ok")]) as (service, _):
            c = service.catalogue
            conf = c.model("model").model_dump()
            c.update_profiles({"ollama":{"models":{"tool-model":{"name":"Flash", "limit":{"context":1000000,
                              "output":393216}}}}}, version="fixture", refreshed=1)
            assert c.put_model(conf)["maxOutputTokens"] == 8000
            assert c.put_model({**conf, "maxOutputTokens":4096})["maxOutputTokens"] == 4096
            for cap in (32000, 40000):
                with pytest.raises(ContractError, match=f"32000 tokens, requested output {cap}"):
                    c.put_model({**conf, "maxOutputTokens":cap})
            assert c.resolve("model")["maxOutputTokens"] == 4096
    asyncio.run(scenario())
