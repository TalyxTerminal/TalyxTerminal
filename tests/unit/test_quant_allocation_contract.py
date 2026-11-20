"""Every allocation action in the public contract has a handler and a description the AI reads, and the standard file's settings can all be shown."""
import json
from pathlib import Path
from types import SimpleNamespace

from modules.quant.backend.allocation_routes import handle_allocation_actions

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "modules" / "quant" / "public" / "contract.json").read_text(encoding="utf-8"))["actions"]
ACTIONS = ["trial_allocation","review_allocation_period","add_allocation_shock"]


def test_each_new_action_has_a_handler_and_an_ai_description():
    ctx = SimpleNamespace(handlers={})
    handle_allocation_actions(ctx, SimpleNamespace(allocation=SimpleNamespace()))
    assert set(ctx.handlers) == set(ACTIONS)
    for name in ACTIONS:
        assert CONTRACT[name]["description"] and CONTRACT[name]["examples"] and CONTRACT[name]["effects"]


def test_starting_a_run_returns_at_once_and_says_how_to_follow_it():
    """A run is Quant's own record, not a platform task: the action answers at once and its description names the
    read that follows it."""
    entry = CONTRACT["start_run"]
    assert entry["task"] is False and "write" in entry["effects"]
    assert "runId" in json.dumps(json.loads((Path(__file__).resolve().parents[2] / "modules/quant/public/contract.json").read_text(encoding="utf-8"))["$defs"]["RunReceipt"])
