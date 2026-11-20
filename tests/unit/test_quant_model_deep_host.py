"""The Host side of neural networks: device detection from recorded output, which PyTorch build is offered, the algorithm table, the estimate."""
from __future__ import annotations

from types import SimpleNamespace

from modules.quant.backend import model_prep as P
from modules.quant.backend import packages
from modules.quant.native import model_algorithms as A
from modules.quant.native import model_check as C

GIB = 2**20
TABLE = {"one": "0, NVIDIA GeForce RTX 4090, 24564, 23000, 581.42, 8.9\n",
         "two": "0, NVIDIA GeForce RTX 4090, 24564, 23000, 581.42, 8.9\n1, NVIDIA GeForce RTX 3060, 12288, 11500, 581.42, 8.6\n",
         "old": "0, NVIDIA GeForce RTX 3060, 12288, 11500, 551.61, 8.6\n",
         "pascal": "0, NVIDIA GeForce GTX 1060, 6144, 5900, 581.42, 6.1\n"}
HEADER = {"one": "| NVIDIA-SMI 581.42   Driver Version: 581.42   CUDA Version: 13.2 |", "two": "| CUDA Version: 13.2 |", "old": "| CUDA Version: 12.4 |", "pascal": "| CUDA Version: 13.2 |"}
ARC = "Intel(R) Arc(TM) 140V GPU (16GB)\n"


def recorded(kind, intel=False):
    def run(command, **_):
        if command[0] == "nvidia-smi" and len(command) > 1 and kind != "none":
            out = TABLE[kind]
        elif command[0] == "nvidia-smi" and kind != "none":
            out = HEADER[kind]
        elif command[0] == "powershell":
            out = ARC if intel else "Microsoft Basic Display\n"
        else:
            raise FileNotFoundError(command[0])
        return SimpleNamespace(returncode=0, stdout=out)
    return run


def test_devices_list_what_the_machine_has():
    none = packages.detect_devices(recorded("none"))
    assert none["nvidia"] == [] and none["intel"] == []
    one = packages.detect_devices(recorded("one"))["nvidia"]
    assert [card["name"] for card in one] == ["NVIDIA GeForce RTX 4090"] and one[0]["cudaMax"] == "13.2" and round(one[0]["memoryTotal"] / 2**30) == 24
    assert len(packages.detect_devices(recorded("two"))["nvidia"]) == 2
    assert [card["name"] for card in packages.detect_devices(recorded("none", intel=True))["intel"]] == ["Intel(R) Arc(TM) 140V GPU (16GB)"]


def test_install_picks_the_cuda_lock_when_an_nvidia_card_is_found():
    offer = packages.deep_offer(packages.detect_devices(recorded("one")))
    assert offer["variant"] == "cuda" and offer["size"] == "about 3.4 GB" and "reason" not in offer


def test_an_old_driver_installs_the_cpu_lock_and_says_why():
    offer = packages.deep_offer(packages.detect_devices(recorded("old")))
    assert offer["variant"] == "cpu" and "supports CUDA 12.4; PyTorch 2.15 needs 13.2" in offer["reason"]
    pascal = packages.deep_offer(packages.detect_devices(recorded("pascal")))
    assert pascal["variant"] == "cpu" and "compute capability 6.1" in pascal["reason"]


def test_intel_graphics_alone_lets_the_user_choose_cpu_or_xpu():
    offer = packages.deep_offer(packages.detect_devices(recorded("none", intel=True)))
    assert offer["variant"] == "cpu" and offer["choices"] == ["cpu", "xpu"]
    assert "choices" not in packages.deep_offer(packages.detect_devices(recorded("none")))


def test_each_deep_lock_is_the_research_lock_plus_one_torch():
    research = packages.lock_blocks((packages.RUNTIME / "research.lock").read_text(encoding="utf-8"))
    for variant, extra in (("cpu", {"torch"}), ("cuda", {"torch"}), ("xpu", {"torch", "triton-xpu"})):
        text = (packages.RUNTIME / f"deep-{variant}.lock").read_text(encoding="utf-8")
        assert not [line for line in text.splitlines() if line.startswith("--") and "index" in line], variant
        blocks = packages.lock_blocks(text)
        added = set(blocks) - set(research)
        assert {"torch"} <= added and len(text) < 2**20
        assert all(blocks[name][0].split(" \\")[0] == research[name][0].split(" \\")[0] for name in set(research) & set(blocks) if "@ file" not in blocks[name][0])
        assert extra <= added, variant


def test_all_networks_are_listed_and_none_is_greyed():
    listed = [item for item in P.algorithms() if item["group"] == "neural"]
    assert {item["id"] for item in listed} == set(A.NEURAL_IDS)
    assert not any(item.get("unavailable") for item in listed)
    assert [item["id"] for item in listed if item["shape"] == "cross"] == ["mlp", "tabnet"]


def test_look_back_belongs_to_sequence_networks_only():
    keys = {item["id"]: {entry["key"] for entry in item["params"]} for item in P.algorithms() if item["group"] == "neural"}
    assert "lookback" in keys["lstm"] and "lookback" not in keys["mlp"] and "lookback" not in keys["tabnet"]
    assert "heads" in keys["transformer"] and "heads" not in keys["lstm"] and "pretrain" in keys["tabnet"] and "dropout" not in keys["mlp"]
    assert all({"epochs", "stop", "lr", "device"} <= value for value in keys.values())
    assert all({"batch", "optimizer"} <= value for name, value in keys.items() if name not in ("igmtf", "hist", "tra"))


def test_neural_params_reach_the_pipeline_by_short_names():
    params = {item["name"]: item["default"] for item in P.standard_declarations()["parameters"]}
    params["algorithm"] = "lstm"
    short = P.algorithm_params(params)
    assert short["epochs"] == 100 and short["lookback"] == 20 and short["optimizer"] == "adam"
    assert A.checked_params("lstm", {key: value for key, value in short.items() if key in {entry[0] for entry in A.BY_ID["lstm"]["params"]}})["batch"] == 800


def test_memory_estimate_grows_with_lookback():
    small = C.estimate(algorithm="lstm", params={"lookback": 5, "epochs": 20, "stop": 5}, rows=300_000, features=300, bars=2000, names=500, windows=10, threads=8)
    large = C.estimate(algorithm="lstm", params={"lookback": 60, "epochs": 20, "stop": 5}, rows=300_000, features=300, bars=2000, names=500, windows=10, threads=8)
    assert large["memory"] > 10 * small["memory"] * 0.5 and large["seconds"] > small["seconds"] * 5



def test_lstm_preset_creates_qlibs_benchmark_settings():
    from modules.quant.backend.object_params import check_values
    from modules.quant.backend.object_store import MODEL_PRESETS
    preset = MODEL_PRESETS["model:lstm-alpha360"]["params"]
    assert preset["algorithm"] == "lstm" and preset["inputs"][0]["ref"] == "alpha360" and preset["every_months"] == 6
    assert (preset["nn_width"], preset["nn_layers"], preset["nn_dropout"], preset["nn_epochs"], preset["nn_stop"], preset["nn_batch"], preset["nn_lr"]) == (64, 2, 0.0, 200, 20, 800, 0.001)
    declared = P.standard_declarations()["parameters"]
    check_values(declared, {**{item["name"]: item["default"] for item in declared}, **preset})


def test_epoch_figure_of_a_running_window():
    from modules.quant.backend.model_work_figure import epoch_figure
    figure = epoch_figure({"progress": {"curve": {"train": [0.9, 0.8, 0.7], "valid": [0.95, 0.9, 0.92]}, "bestEpoch": 2}})
    assert figure["layout"]["meta"]["key"] == "model.work.epochs"
    assert figure["layout"]["xaxis"]["dtick"] == 1, "epochs are whole numbers"
    roles = {trace["name"]: trace["meta"]["role"] for trace in figure["data"]}
    assert roles == {"Training loss": "benchmark", "Validation loss": "main"}
    assert figure["data"][1]["hovertemplate"] == "Validation loss %{y:.4f}<extra></extra>" and figure["data"][0]["hovertemplate"].startswith("Epoch %{x}<br>Training loss")
    assert figure["layout"]["shapes"][0]["x0"] == 2


def test_the_six_qlib_fit_networks_are_listed_with_their_own_settings():
    listed = {item["id"]: item for item in P.algorithms() if item["group"] == "neural"}
    assert {"igmtf", "add", "adarnn", "tra", "tcts", "hist"} <= set(listed)
    assert all(listed[key]["route"] == "qlib_fit" and listed[key]["shape"] == "sequence" for key in A.QLIB_FIT_IDS)
    assert listed["lstm"]["route"] == "epochs"
    keys = {key: {entry["key"] for entry in listed[key]["params"]} for key in A.QLIB_FIT_IDS}
    assert "gamma" in keys["add"] and "domains" in keys["adarnn"] and "states" in keys["tra"] and "steps" in keys["tcts"]
    assert "optimizer" not in keys["tra"] and "batch" not in keys["igmtf"] and "batch" not in keys["hist"]
    assert listed["tcts"]["needs"] == ["aux_labels"] and listed["hist"]["needs"] == ["base_run", "concepts"]


def test_early_stop_on_is_offered_only_by_add_and_adarnn():
    listed = {item["id"]: item for item in P.algorithms() if item["group"] == "neural"}
    assert [entry["id"] for entry in listed["add"]["earlyStopOn"]] == ["ic", "loss", "ric"]
    assert [entry["id"] for entry in listed["adarnn"]["earlyStopOn"]] == ["loss", "ic"]
    assert not [key for key, item in listed.items() if key not in ("add", "adarnn") and item.get("earlyStopOn")]
    assert "optimizers" not in listed["tra"] and [entry["id"] for entry in listed["mlp"]["optimizers"]][0] == "gd"
    assert [entry["id"] for entry in listed["lstm"]["optimizers"]] == ["adam", "gd"]


def test_only_the_mlp_trains_a_binary_target():
    for key in A.NEURAL_IDS:
        offered = [entry["id"] for entry in A.objectives_of(key)]
        assert offered == (["l2", "binary"] if key == "mlp" else ["l2"]), key


# ------------------------------------------------------------------------------------------------------ graphics memory, batch size, time
CARD3060 = {"index": 0, "name": "NVIDIA GeForce RTX 3060", "memoryTotal": 12288 * GIB, "memoryFree": 11500 * GIB}
CARD4090 = {"index": 1, "name": "NVIDIA GeForce RTX 4090", "memoryTotal": 24564 * GIB, "memoryFree": 23000 * GIB}


def _group(algorithm="lstm", **params):
    params = {"lookback": 60, "layers": 2, "width": 64, "batch": 4000, "device": "cuda:0", **params}
    return params, {"group": 1, **C.estimate(algorithm=algorithm, params=params, rows=362_000, features=300, bars=2000, names=500, windows=12, threads=8)}


def test_vram_blocks_with_both_numbers():
    params, estimate = _group(width=2048, layers=4)
    rows = C.neural_checks(estimates=[estimate], params=[params], cards={"cuda:0": CARD3060}, windows=[(1, 362_000)], concurrency=1)
    gpu = next(row for row in rows if row[0] == "GPU memory")
    assert gpu[3] == "block" and "RTX 3060 has 11.2 GB free" in gpu[1] and gpu[1].startswith("about ")
    assert gpu[5] == "Batch size 4000 × look back 60 × 300 features. Choose a smaller batch size, or the CPU."
    # a smaller batch or the CPU lifts it
    params, estimate = _group(width=2048, layers=4, batch=100)
    assert not [row for row in C.neural_checks(estimates=[estimate], params=[params], cards={"cuda:0": CARD3060}, windows=[(1, 362_000)], concurrency=1) if row[0] == "GPU memory"]
    cpu_params, cpu_estimate = _group(width=2048, layers=4, device="cpu")
    assert "vram" not in cpu_estimate
    assert not [row for row in C.neural_checks(estimates=[cpu_estimate], params=[cpu_params], cards={"cuda:0": CARD3060}, windows=[(1, 362_000)], concurrency=1) if row[0] == "GPU memory"]


def test_vram_estimate_grows_with_batch_and_heads():
    small, large = C.vram_of("lstm", {"layers": 2, "width": 512}, 300, 60, 800), C.vram_of("lstm", {"layers": 2, "width": 512}, 300, 60, 8000)
    assert large > 4 * small
    assert C.vram_of("transformer", {"layers": 2, "width": 64, "heads": 4}, 300, 60, 4000) > C.vram_of("transformer", {"layers": 2, "width": 64, "heads": 2}, 300, 60, 4000)


def test_batch_larger_than_a_window_is_blocked():
    params, estimate = _group(device="cpu", batch=8000)
    rows = C.neural_checks(estimates=[estimate], params=[params], cards={}, windows=[(1, 40_000), (2, 3650)], concurrency=1)
    batch = next(row for row in rows if row[0] == "Batch size")
    assert batch[3] == "block" and batch[1] == "8000; window 2 has 3,650 training rows" and "Batch size 8000; window 2 has 3,650 training rows" in batch[5]
    ok_params, ok_estimate = _group(device="cpu", batch=800)
    assert not [row for row in C.neural_checks(estimates=[ok_estimate], params=[ok_params], cards={}, windows=[(1, 40_000), (2, 3650)], concurrency=1) if row[0] == "Batch size"]


def test_default_batch_follows_the_card():
    params = {"layers": 2, "width": 64}
    assert C.default_batch(None, "lstm", params, 300, 60, 100_000) == 800
    big = C.default_batch(CARD4090, "lstm", params, 300, 60, 400_000)
    small_card = C.default_batch({"memoryFree": 1.5 * 2**30}, "lstm", params, 300, 60, 400_000)
    assert big == 8000 and 800 <= small_card < big
    assert C.default_batch(CARD4090, "lstm", params, 300, 60, 6000) == 800, "never more than a quarter of the smallest window's rows (1500)"
    assert C.default_batch(CARD4090, "lstm", params, 300, 60, 16000) == 4000 and C.default_batch(CARD4090, "lstm", params, 300, 60, 3000) == 800


def test_a_batch_over_a_day_says_how_long_and_how_many_at_a_time():
    params, estimate = _group(device="cpu")
    estimates = [{**estimate, "group": number, "seconds": 9 * 86400 * 2 / 6} for number in range(1, 7)]
    rows = C.neural_checks(estimates=estimates, params=[params] * 6, cards={}, windows=[(1, 362_000)], concurrency=2)
    time_row = next(row for row in rows if row[0] == "Time")
    assert time_row[1] == "about 9 days, 2 at a time on this machine" and time_row[3] == "ok", "not a block"
    assert C.duration(27 * 3600) == "1 day 3 hours" and C.duration(40 * 60) == "40 min" and C.duration(5400) == "1.5 hours"


def test_memory_block_detail_names_the_look_back_and_columns():
    _params, estimate = _group(device="cpu")
    assert estimate["look"] == 60 and estimate["features"] == 300 and estimate["rows"] == 362_000


def test_two_networks_share_a_card_only_when_both_fit():
    import asyncio

    from modules.quant.backend.run_queue import RunQueue
    GB = 2**30

    async def scenario():
        queue = RunQueue(memory=64 * GB, cpus=4, devices=[{**CARD4090, "index": 0}, {**CARD3060, "index": 1}])
        order = []
        hold = asyncio.Event()

        async def train(owner, card, vram, wait=True):
            async with queue.slot(owner=owner, label=owner, interactive=False, memory=GB, gpu={"index": card, "memory": vram * GB}):
                order.append(owner)
                if wait:
                    await hold.wait()

        # the 4090's budget is 90% of 24 GB = 21.6 GB: 10 + 10 fit together, a third does not; another card is a budget of its own
        tasks = [asyncio.create_task(train("a", 0, 10)), asyncio.create_task(train("b", 0, 10)), asyncio.create_task(train("c", 0, 10)), asyncio.create_task(train("d", 1, 9))]
        await asyncio.sleep(0.05)
        view = queue.view()
        assert order == ["a", "b", "d"] and [item["owner"] for item in view["waiting"]] == ["c"]
        assert view["waiting"][0]["waitsFor"] == "waits for RTX 4090 memory" and view["running"][0]["gpu"]["name"] == "RTX 4090"
        hold.set()
        await asyncio.gather(*tasks)
        assert order == ["a", "b", "d", "c"]
        # a job whose own estimate is over the budget still runs when it is alone on the card (the check blocked it before sending if it would not fit)
        alone = RunQueue(memory=64 * GB, cpus=2, devices=[CARD3060 | {"index": 0}])
        async with alone.slot(owner="big", label="big", interactive=False, memory=GB, gpu={"index": 0, "memory": 30 * GB}):
            assert alone.view()["running"][0]["owner"] == "big"
    asyncio.run(scenario())


def test_the_cpu_does_not_wait_for_a_card():
    import asyncio

    from modules.quant.backend.run_queue import RunQueue
    GB = 2**30

    async def scenario():
        queue = RunQueue(memory=64 * GB, cpus=4, devices=[{**CARD3060, "index": 0}])
        hold, order = asyncio.Event(), []

        async def train(owner, gpu):
            async with queue.slot(owner=owner, label=owner, interactive=False, memory=GB, gpu=gpu):
                order.append(owner)
                await hold.wait()
        tasks = [asyncio.create_task(train("gpu-1", {"index": 0, "memory": 9 * GB})), asyncio.create_task(train("gpu-2", {"index": 0, "memory": 9 * GB})),
                 asyncio.create_task(train("cpu", None))]
        await asyncio.sleep(0.05)
        assert order == ["gpu-1", "cpu"], "the second card job waits, the CPU job behind it does not"
        hold.set()
        await asyncio.gather(*tasks)
    asyncio.run(scenario())


def test_a_run_trains_on_the_card_its_settings_name():
    from modules.quant.backend.model_runs import ModelRuns
    record = {"settings": {"params": {"nn_device": "cuda:1", "nn_batch": 4000}}, "estimate": {"vram": 9 * 2**30}}
    assert ModelRuns.card_of(None, record) == {"index": 1, "memory": 9 * 2**30}
    assert ModelRuns.card_of(None, {**record, "overrides": {"nn_device": "cpu"}}) is None, "Resume on the CPU leaves the card's queue"
    assert ModelRuns.card_of(None, {"settings": {"params": {}}}) is None


def test_failure_carries_what_the_resume_buttons_need():
    from modules.quant.backend.model_runs import ModelRuns
    holder = SimpleNamespace(read=lambda _id: {"windows": [1, 2, 3, 4], "windowsTotal": 12})
    shown = ModelRuns.failure_of(holder, "x", {"window": 5, "title": "Ran out of GPU memory in window 5, epoch 23", "detail": "d", "kind": "gpu-memory", "epoch": 23, "saved": 22,
                                              "batch": 800, "tryBatch": 400, "stage": "compute"})
    assert (shown["kind"], shown["saved"], shown["tryBatch"], shown["windowsTotal"], shown["resumeFrom"]) == ("gpu-memory", 22, 400, 12, 5)



def test_hist_without_concept_data_is_blocked_before_sending():
    from modules.quant.native import model_data as D
    formula = D.quick_formula(5, "rank")
    rows = C.route_b_checks("hist", None, formula)
    assert [(row[0], row[3]) for row in rows] == [("Concepts", "block")] and rows[0][1] == "Market gives no concept-sector membership for these names"
    assert C.route_b_checks("hist", {"matrix_file": "m.npy"}, formula) == []
    assert C.route_b_checks("lstm", None, formula) == [], "only HIST reads concepts"


def test_tcts_needs_an_auxiliary_label():
    from modules.quant.native import model_data as D
    assert C.route_b_checks("tcts", None, D.quick_formula(5, "rank")) == [], "a plain return over some days gives its own longer ones"
    assert C.route_b_checks("tcts", None, D.quick_formula(1, "raw")) == []
    rows = C.route_b_checks("tcts", None, "CSRank($close / Ref($close, 3) - 1)")
    assert [(row[0], row[3], row[4]) for row in rows] == [("Labels", "block", "label")] and rows[0][1] == "TCTS needs at least one auxiliary label"
    assert C.route_b_checks("igmtf", None, "anything") == []


def test_start_from_lists_matching_base_runs():
    from modules.quant.backend.model_runs import ModelRuns
    rows = [{"id": "r14", "number": 14, "state": "completed", "settings": {"params": {"algorithm": "lstm", "inputs": [1], "nn_lookback": 20}}},
            {"id": "r13", "number": 13, "state": "failed", "settings": {"params": {"algorithm": "gru"}}},
            {"id": "r12", "number": 12, "state": "completed", "settings": {"params": {"algorithm": "lightgbm"}}}]
    listed = ModelRuns.base_runs(SimpleNamespace(of_object=lambda _id: rows), "obj")
    assert [(item["id"], item["label"]) for item in listed] == [("r14", "run #14 · LSTM")] and listed[0]["lookback"] == 20 and listed[0]["width"] == 64


def test_start_from_a_different_network_is_blocked_before_sending():
    same = {"inputs": [{"source": "builtin", "ref": "alpha158"}], "lookback": 20, "width": 64, "layers": 2}
    base = {"label": "run #14", **same}
    for algorithm in ("gats", "igmtf", "add", "hist"):
        assert C.start_from_row(algorithm, same, base) is None
        row = C.start_from_row(algorithm, {**same, "lookback": 30}, base)
        assert row[0] == "Start from a run with the same inputs and look back" and row[3] == "block" and row[1] == "run #14 has other look back"
    both = C.start_from_row("gats", {**same, "width": 32, "inputs": []}, base)
    assert both[1] == "run #14 has other inputs, width"
    assert C.start_from_row("lstm", {**same, "width": 32}, base) is None, "only the networks that start from a run"
    assert C.start_from_row("gats", same, None) is None, "nothing chosen"


def test_every_network_that_starts_from_a_run_declares_it():
    from modules.quant.native import model_algorithms as A
    for key in ("gats", "igmtf", "add", "hist"):
        assert "base_run" in A.BY_ID[key]["needs"] and any(entry[0] == "base" for entry in A.BY_ID[key]["params"])
    assert "base_run" not in (A.BY_ID["lstm"].get("needs") or [])


class _Values:
    def __init__(self, rows):
        self.store = SimpleNamespace(read_values=lambda version_id: rows)


def _row(day, name, field, value):
    return {"datetime": day, "instrument": name, "field": field, "value": value, "availableAt": day, "recordedAt": day, "mode": "x"}


def test_a_concept_table_is_names_by_concepts(tmp_path):
    import pandas as pd
    info = {"instruments": [{"instrumentId": "a", "column": "A.XNAS"}, {"instrumentId": "b", "column": "B.XNAS"}, {"instrumentId": "c", "column": "C.XNAS"}]}
    rows = [_row(1, "a", "chips", 1.0), _row(1, "a", "cloud", 0.0), _row(1, "b", "cloud", 1.0), _row(1, "b", "chips", 1.0), _row(1, "zzz", "chips", 1.0),
            _row(1, "c", "empty", 0.0), _row(2, "a", "chips", 0.0)]
    found = P.concept_table(_Values(rows), "v1", info, tmp_path / "c.parquet")
    table = pd.read_parquet(tmp_path / "c.parquet")
    assert found == {"path": str(tmp_path / "c.parquet"), "names": 1, "concepts": 2, "of": 3}
    assert list(table.columns) == ["chips", "cloud"] and set(table.index) == {"B.XNAS"}, "A left every concept on day 2 (the newest value stands); C and zzz were never in one"
    assert table.loc["B.XNAS", "chips"] == 1.0 and table.loc["B.XNAS", "cloud"] == 1.0


def test_hist_concept_table_without_these_names_is_blocked():
    formula = "x"
    assert C.route_b_checks("hist", {"names": 5, "concepts": 3}, formula) == []
    row = C.route_b_checks("hist", {"names": 0, "concepts": 0, "of": 5}, formula)[0]
    assert row[0] == "Concepts" and row[1] == "The table has none of these names" and row[3] == "block"
    assert C.route_b_checks("hist", {"names": 4, "concepts": 0}, formula)[0][1] == "The table has no concept with a member here"


def test_concepts_reads_the_chosen_table_by_the_panels_names(tmp_path):
    import pandas as pd

    from modules.quant.native.model_run import Training as Run
    pd.DataFrame({"chips": [1.0, 0.0], "cloud": [0.0, 1.0]}, index=["B.XNAS", "A.XNAS"]).to_parquet(tmp_path / "c.parquet")
    fake = SimpleNamespace(job={"concepts": {"path": str(tmp_path / "c.parquet")}}, panel=SimpleNamespace(columns=["A.XNAS", "B.XNAS", "C.XNAS"]))
    found = Run.concepts(fake, None)
    assert found["concepts"] == ["chips", "cloud"] and found["matrix"].shape == (3, 2)
    assert found["matrix"].tolist() == [[0.0, 1.0], [1.0, 0.0], [0.0, 0.0]], "a name the table lacks is in no concept"
    assert Run.concepts(SimpleNamespace(job={}, panel=None), None) is None


def test_auxiliary_labels_are_written_or_derived_and_the_gap_follows_the_farthest():
    from modules.quant.native import formula as F
    from modules.quant.native import model_data as D
    main = D.quick_formula(5, "rank")
    assert D.aux_formulas(main, None) is None, "only TCTS takes them"
    derived = D.aux_formulas(main, "")
    assert derived == [D.quick_formula(6, "raw").join(["CSRank(", ")"]), D.quick_formula(7, "raw").join(["CSRank(", ")"])]
    assert D.aux_formulas(main, "CSRank(Ref($open, -11) / Ref($open, -1) - 1)\n\n") == ["CSRank(Ref($open, -11) / Ref($open, -1) - 1)"]
    assert D.aux_formulas("CSRank($close / Ref($close, 3) - 1)", "") == [], "no plain return to derive from"
    ahead = [F.reach(F.parse(item, ahead=True), item)["ahead"] for item in D.aux_formulas(main, "Ref($open, -11) / Ref($open, -1) - 1")]
    assert ahead == [11] and F.reach(F.parse(main, ahead=True), main)["ahead"] == 6


def test_a_written_auxiliary_label_must_read_a_later_bar():
    main = "CSRank($close / Ref($close, 3) - 1)"
    assert C.route_b_checks("tcts", None, main, "Ref($open, -8) / Ref($open, -1) - 1") == []
    rows = C.route_b_checks("tcts", None, main, "$close / Ref($close, 3) - 1")
    assert [(row[0], row[3]) for row in rows] == [("Labels", "block")] and "later bar" in rows[0][1]


def test_check_gap_follows_the_farthest_auxiliary_label():
    from modules.quant.native import model_data as D
    source = open(C.__file__, encoding="utf-8").read()
    assert "self.gap_ahead" in source and "D.layout(panel.labels, settings[\"split\"], self.gap_ahead" in source
    assert D.aux_formulas(D.quick_formula(5, "rank"), "Ref($open, -9) / Ref($open, -1) - 1") == ["Ref($open, -9) / Ref($open, -1) - 1"]


def test_qlib_fit_networks_resume_from_the_window():
    from modules.quant.backend.model_runs import ModelRuns
    fake = SimpleNamespace(read=lambda _id: {"windows": [{"k": 1}, {"k": 2}, {"k": 3}, {"k": 4}], "windowsTotal": 9})
    shown = ModelRuns.failure_of(fake, "r1", {"window": 5, "title": "Window 5: boom", "message": "boom", "stage": "compute"})
    assert shown["resumeFrom"] == 5 and shown["checkpoint"] == 4 and "epoch" not in shown and "saved" not in shown
    first = ModelRuns.failure_of(SimpleNamespace(read=lambda _id: {"windows": []}), "r1", {"window": 1, "title": "Window 1: boom"})
    assert first["resumeFrom"] is None, "nothing finished: it starts again from the first window"
