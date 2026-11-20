"""A failed preparation never sticks, its failure says the last line of its log, and the environment is the same wherever the checkout is."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from talyx_sdk import ContractError

from modules.quant.backend.native_environment import prepare_lock, stable_lock
from tests.unit.quant_runtime_fakes import adapt

ORIGIN = {"kind": "bundled-research", "pythonVersion": "3.12", "engine": "research"}


def lock_at(root):
    return f"pandas==2.0 --hash=sha256:aa\nalphalens-reloaded @ file:///{root}/modules/quant/runtime/wheels/alphalens-0.5.whl \\\n    --hash=sha256:bb\n"


class Platform:
    """The platform's environments as talyx_sdk.uv answers them: a new submission is queued, the same key returns the record
    there (`duplicate`), a failed one is kept until retried, and every record names its log (`stderrPath`)."""

    def __init__(self, logs, outcomes=()):
        self.records, self.calls, self.outcomes, self.logs = {}, [], list(outcomes), Path(logs)
        self.python_env = SimpleNamespace(submit=self.submit, get=self.get, retry=self.retry)

    def finish(self, record):
        """The preparation ends with the next outcome; a failed one leaves the line uv wrote last in its log."""
        record["state"] = self.outcomes.pop(0) if self.outcomes else "completed"
        if record["state"] == "failed":
            Path(record["stderrPath"]).write_text(f"Resolving\nerror: attempt {len(self.calls)} could not download pandas\n", encoding="utf-8")

    async def submit(self, args):
        self.calls.append(("submit", args["submission_key"], args["lock_digest"]))
        found = self.records.get(args["submission_key"])
        if found is not None:
            if found["lockDigest"] != args["lock_digest"]:
                raise ContractError("conflict", "submissionKey was already used for another environment", {"preparationId": found["preparationId"]})
            return {**found, "duplicate": True}
        identity = "pep_" + str(len(self.records))
        record = {"preparationId": identity, "state": "queued", "lockDigest": args["lock_digest"], "stderrPath": str(self.logs / f"{identity}.stderr.log")}
        self.records[args["submission_key"]] = record
        return dict(record)

    def record(self, identity):
        return next(item for item in self.records.values() if item["preparationId"] == identity)

    async def get(self, args):
        record = self.record(args["preparation_id"])
        if record["state"] == "queued":
            self.finish(record)
        return dict(record)

    async def retry(self, args):
        self.calls.append(("retry", args["preparation_id"]))
        record = self.record(args["preparation_id"])
        record["state"] = "queued"
        return dict(record)


def run(coroutine):
    return asyncio.run(coroutine)


def test_a_failed_earlier_preparation_is_tried_once_more_for_the_next_caller(tmp_path):
    platform = Platform(tmp_path, outcomes=["failed", "completed"])
    ctx = adapt(SimpleNamespace(platform=platform))
    with pytest.raises(ContractError, match="could not download pandas"):  # the first attempt fails as it is made (no earlier attempt to retry)
        run(prepare_lock(ctx, lock_at("/a"), ORIGIN))
    state = run(prepare_lock(ctx, lock_at("/a"), ORIGIN))
    assert state["state"] == "completed" and [call[0] for call in platform.calls].count("retry") == 1


def test_a_preparation_that_fails_again_is_reported_after_one_retry_with_the_last_line_of_its_log(tmp_path):
    platform = Platform(tmp_path, outcomes=["failed", "failed"])
    ctx = adapt(SimpleNamespace(platform=platform))
    with pytest.raises(ContractError):
        run(prepare_lock(ctx, lock_at("/a"), ORIGIN))
    with pytest.raises(ContractError) as failed:
        run(prepare_lock(ctx, lock_at("/a"), ORIGIN))
    assert failed.value.message == "The research environment could not be prepared: error: attempt 3 could not download pandas"
    assert failed.value.details["preparation"]["state"] == "failed"
    assert [call[0] for call in platform.calls] == ["submit", "submit", "retry"]  # the earlier failure is retried once, not again and again


def test_a_failure_with_no_log_says_the_preparation_stopped_without_saying_why(tmp_path):
    platform = Platform(tmp_path, outcomes=["failed"])
    platform.finish = lambda record: record.update(state="failed")  # nothing written to the log
    with pytest.raises(ContractError, match="the preparation stopped without saying why"):
        run(prepare_lock(adapt(SimpleNamespace(platform=platform)), lock_at("/a"), ORIGIN))


def test_the_same_environment_is_found_from_another_checkout(tmp_path):
    assert stable_lock(lock_at("/D:/old/wt-M")) == stable_lock(lock_at("/D:/new/wt-final3")) == stable_lock(lock_at("/D:/moved/TALYX"))
    platform = Platform(tmp_path)
    ctx = adapt(SimpleNamespace(platform=platform))
    first = run(prepare_lock(ctx, lock_at("/D:/old/wt-M"), ORIGIN))
    second = run(prepare_lock(ctx, lock_at("/D:/new/wt-final3"), ORIGIN))
    assert second["preparationId"] == first["preparationId"] and second["state"] == "completed"
    assert len({call[1] for call in platform.calls if call[0] == "submit"}) == 1 and len(platform.records) == 1
    third = Platform(tmp_path)
    third.records = dict(platform.records)
    again = run(prepare_lock(adapt(SimpleNamespace(platform=third)), lock_at("/elsewhere"), ORIGIN))
    assert again["preparationId"] == first["preparationId"]


def test_new_install_after_cancel_restarts_the_same_preparation(tmp_path):
    platform = Platform(tmp_path, outcomes=["cancelled", "completed"])
    ctx = adapt(SimpleNamespace(platform=platform))
    with pytest.raises(ContractError):
        run(prepare_lock(ctx, lock_at("/a"), ORIGIN))
    result = run(prepare_lock(ctx, lock_at("/a"), ORIGIN))
    assert result["state"] == "completed"
    assert [row[0] for row in platform.calls] == ["submit", "submit", "retry"]
