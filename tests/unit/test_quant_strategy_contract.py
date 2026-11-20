"""The strategy actions are in Quant's public contract, AI and the page use the same ones, and the second layer holds nothing the engine's
simulation of latency, queue position or ticks would need (those are not done, so nothing in the contract names them)."""
import asyncio
import json
from pathlib import Path

import pytest

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "modules" / "quant" / "public" / "contract.json").read_text(encoding="utf-8"))
NEW = ["read_run","read_run_table","read_object","update_object"]


def schema(value):
    """``value`` with its ``$ref`` (and a nullable ``anyOf``) followed to the definition."""
    while True:
        if "$ref" in value:
            value = CONTRACT["$defs"][value["$ref"].rsplit("/", 1)[1]]
        elif "anyOf" in value and len([item for item in value["anyOf"] if item.get("type") != "null"]) == 1:
            value = next(item for item in value["anyOf"] if item.get("type") != "null")
        else:
            return value


@pytest.mark.parametrize("name", NEW)
def test_each_new_action_is_public_with_an_ai_description(name):
    action = CONTRACT["actions"][name]
    assert action["description"] and action["examples"] and action["effects"]
    assert schema(action["input"])["type"] == "object"


def test_read_actions_are_safe_and_the_switch_is_not():
    for name in NEW:
        assert (CONTRACT["actions"][name]["effects"] == ["read"]) == (name != "update_object")


def test_trades_page_is_capped_at_500():
    properties = schema(CONTRACT["actions"]["read_run_table"]["input"])["properties"]
    assert schema(properties["limit"])["maximum"] == 500 and {"runId","cursor","filter","table","start","end"} <= set(properties)


def test_start_run_only_takes_the_saved_object():
    properties = schema(CONTRACT["actions"]["start_run"]["input"])["properties"]
    assert set(properties) == {"objectId"}


def test_run_settings_have_no_latency_or_queue():
    text = json.dumps({name: CONTRACT["actions"][name] for name in NEW + ["start_run"]})
    for word in ("latency", "queuePosition", "queue_position", "liquidityConsumption", "dataType", "tickData", "orderBook"):
        assert word not in text, word
    from modules.quant.backend import strategy_settings
    value = strategy_settings.effective("perpetual", None, {})
    flat = json.dumps(value)
    assert all(word not in flat for word in ("latency", "queue", "dataType"))


def test_actions_route_to_handlers(tmp_path):
    from modules.quant.backend.object_routes import handle_actions
    from tests.fixtures.strategy.fakes import services

    async def scenario():
        ctx, records, shell = services(tmp_path)
        handle_actions(ctx, shell)
        records.close()
        missing = [name for name in NEW if name not in ctx.handlers]
        assert not missing, missing
    asyncio.run(scenario())


def test_no_search_action():
    """Grid sweeps only: nothing in the contract searches (random, Bayesian) for parameters, since trying many runs is what the deflated Sharpe discounts."""
    text = json.dumps(CONTRACT).lower()
    for word in ("optuna", "bayes", "random_search", "randomsearch", "auto_tune", "autotune"):
        assert word not in text
