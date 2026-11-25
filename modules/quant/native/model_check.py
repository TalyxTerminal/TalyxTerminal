"""The model's check before training, in seconds and without training: the time axis, the inputs table, the checks and the estimates.

It runs in the research process that keeps the data in memory (the factor engine's), on the same panel a run would use. Built-in sets
are computed on a sample of names only, enough for their coverage and for finding inputs that are nearly the same. Checks that fail
are listed and do not stop sending, except what would fail for certain: too little memory, an algorithm this environment cannot run,
data with too few names a day.
"""
from __future__ import annotations

import json
import math
import time
import warnings

import numpy as np
import pandas as pd

from . import i18n
from . import model_algorithms as A
from . import model_data as D
from . import model_figures as MF

GB = 1024 ** 3
SAMPLE_NAMES = 30
SAMPLE_DAYS = 40
ROWS_NEED = 200_000
NAMES_NEED = 150
COVERAGE_NEED = 0.8
DUPLICATE = 0.95
FACTOR = {"lightgbm": 3.0, "xgboost": 4.0, "catboost": 5.0, "linear": 2.5, "ridge": 2.5, "lasso": 2.5, "nnls": 2.5, "double_ensemble": 4.0}
SPEED = {"lightgbm": 2.5e-9, "xgboost": 4e-9, "catboost": 6e-9, "double_ensemble": 2.5e-9}
BUILTIN_FEATURES = {"alpha158": 157, "alpha360": 300, "styles": 7}
BASE = 0.7 * GB


def _day(label):
    moment = pd.Timestamp(int(label), unit="ms", tz="UTC")
    return i18n.t("{month} {day}, {year}", month=moment.strftime(i18n.t("%b")), day=moment.day, year=moment.year)


def _missing_part(daily, labels):
    """Where an input with low coverage is missing: the stretches of days with values for under half the names, else the share of names a day."""
    low = np.asarray(daily) < 0.5
    stretches, start = [], None
    for index, flag in enumerate(low):
        if flag and start is None:
            start = index
        if start is not None and (not flag or index == len(low) - 1):
            last = index if flag else index - 1
            if last - start >= 4:   # a stretch of a week or more, not a lone missing day
                stretches.append((start, last))
            start = None
    if stretches:
        shown = ", ".join(f"{_day(int(labels[a]))} – {_day(int(labels[b]))}" for a, b in stretches[:3]) + (f" and {len(stretches) - 3} more" if len(stretches) > 3 else "")
        return f"Missing for most names {shown}"
    return f"Values for {np.mean(daily) * 100:.0f}% of the names on an average day" if len(daily) else ""


TRAIN_CHOICES = (12, 24, 36, 60)   # the Training choices of the standard model


def _room_for_windows(labels, split, ahead, warm):
    """What gives a split with no window one: the shortest data start that fits, and the training lengths that fit the data as they are."""
    labels = np.asarray(labels, dtype="int64")
    train, valid = int(split["trainMonths"]), int(split["validMonths"])
    fits = [months for months in TRAIN_CHOICES if months < train and D.layout(labels, {**split, "trainMonths": months}, ahead, warmup=warm)]
    # the first test month needs warm-up, training and validation before it, and a label `ahead` bars after its first bar before the newest bar
    spacing = (labels[-1] - labels[0]) / (len(labels) - 1) if len(labels) > 1 else D.DAY   # calendar time a bar, weekends and holidays included
    last_test = D._month_start(int(labels[-1]) - int((ahead + 1) * spacing))
    start_by = D._months_before(last_test, train + valid) - int(warm * spacing)
    text = (f"The data ({_month(int(labels[0]))} – {_month(int(labels[-1]))}) are too short for {D.months_label(train)} of training, {D.months_label(valid)} of "
            f"validation and one test period after the warm-up. Start the data by {_day(start_by)}")
    if split.get("firstTest"):
        text += ", clear First test"
    if fits:
        text += f", or train {D.months_label(fits[-1])}"
    return text + "."


def _month(label):
    moment = pd.Timestamp(int(label), unit="ms", tz="UTC")
    return f"{moment.strftime('%b')} {moment.year}"


# Seconds an epoch takes for each row × look back day × input on 8 CPU threads (measured on LSTM, GRU and Transformer; the others take the middle).
SPEED_NN = {"lstm": 1.6e-7, "gru": 3.2e-7, "transformer": 1.0e-6, "localformer": 1.2e-6, "alstm": 3.0e-7, "mlp": 2e-8, "tabnet": 1e-7}


# How many times a layer's activations the graphics memory holds per input value (forward, backward and the optimizer's working copies); a Transformer's attention adds its heads.
ACT_HEADS = {"transformer", "localformer"}
CUDA_CONTEXT = 0.5 * GB


def vram_of(algorithm, params, features, look, batch=None):
    """Graphics memory (bytes) of one network on a card: its batch of inputs and activations, its parameters with their gradients and Adam's two
    copies (16 bytes each), and CUDA's own context. The data stay in memory; the card holds a batch."""
    batch = float(batch if batch is not None else params.get("batch", 800))
    layers, width = float(params.get("layers", 2)), float(params.get("width", 64))
    act = 2.0 * layers * width / max(float(features), 1.0)
    if algorithm in ACT_HEADS:
        act *= float(params.get("heads", 2))
    weights = layers * 8 * width * width + features * width * 4
    return float(batch * look * features * 4 * max(act, 1.0) + weights * 16 + CUDA_CONTEXT)


BATCH_STEPS = (800, 2000, 4000, 8000)


def default_batch(card, algorithm, params, features, look, min_rows):
    """The batch size a card starts with: CPU 800; on a card the biggest of 800, 2000, 4000 and 8000 whose graphics memory is under 60% of what the card
    has free, and no more than a quarter of the smallest window's training rows (Qlib drops a last batch that is not full)."""
    if not card:
        return 800
    free = float(card.get("memoryFree") or card.get("memoryTotal") or 0)
    fits = [step for step in BATCH_STEPS if vram_of(algorithm, params, features, look, step) <= 0.6 * free and (not min_rows or step <= min_rows / 4)]
    return max(fits) if fits else BATCH_STEPS[0]


def card_name(card):
    """A card's name as the check writes it: "NVIDIA GeForce RTX 3060" is "RTX 3060"."""
    return " ".join(part for part in str(card.get("name", "GPU")).split() if part.lower() not in ("nvidia", "geforce"))


def duration(seconds):
    """A time to wait, to the day: "9 days", "1 day 3 hours", "40 min"."""
    seconds = float(seconds)
    if seconds >= 86400:
        days, hours = int(seconds // 86400), int(round(seconds % 86400 / 3600))
        if hours == 24:
            days, hours = days + 1, 0
        return f"{days} day{'s' if days != 1 else ''}" + (f" {hours} hour{'s' if hours != 1 else ''}" if hours and days < 3 else "")
    if seconds >= 3600:
        hours = seconds / 3600
        return f"{hours:.0f} hours" if hours >= 10 else f"{hours:.1f} hours"
    return f"{max(1, round(seconds / 60))} min"


START_FROM = ("gats", "igmtf", "add", "hist")


def start_from_row(algorithm, own, base):
    """Starting from a run only works when the network inside is the same shape: the same inputs, look back, width and layers as the run it starts from."""
    if algorithm not in START_FROM or not base:
        return None
    differs = [name for name in ("inputs", "lookback", "width", "layers") if json.dumps(own.get(name), sort_keys=True) != json.dumps(base.get(name), sort_keys=True)]
    if not differs:
        return None
    names = {"inputs": "inputs", "lookback": "look back", "width": "width", "layers": "layers"}
    return ("Start from a run with the same inputs and look back", f"{base.get('label') or 'The run'} has other {', '.join(names[item] for item in differs)}", "the same as this model's",
            "block", "algorithm", "Choose a finished LSTM or GRU run with the same inputs, look back, width and layers, or none.")


def route_b_checks(algorithm, concepts, label_formula, aux=""):
    """What HIST and TCTS need before they can train, as `add` takes the rows: HIST a concept-sector table, TCTS a label it can derive auxiliary ones from."""
    rows = []
    if algorithm == "hist" and not concepts:
        rows.append(("Concepts", "Market gives no concept-sector membership for these names", "available", "block", "algorithm",
                     "Choose a concept table, or a data source that gives concept-sector membership; HIST cannot train without one."))
    elif algorithm == "hist" and isinstance(concepts, dict) and concepts.get("names") is not None and (not concepts["names"] or not concepts.get("concepts")):
        rows.append(("Concepts", "The table has none of these names" if not concepts["names"] else "The table has no concept with a member here", "names in a concept", "block", "algorithm",
                     "Choose a table of the names in this model's data by concepts: a value other than 0 means the name belongs to the concept."))
    if algorithm == "tcts":
        formulas = D.aux_formulas(label_formula, aux)
        if not formulas:
            rows.append(("Labels", "TCTS needs at least one auxiliary label", "at least one", "block", "label",
                         "TCTS trains on the label and some auxiliary ones; this label is not a plain return over some days, so none can be derived from it. Add one in the Label settings."))
        for item in formulas:
            try:
                from . import formula as F
                F.reach(F.parse(item, ahead=True), item)
            except ValueError as error:
                rows.append(("Labels", f"{item}: {error}", "a formula that reads a later bar", "block", "label", "An auxiliary label is a formula like the label: it must read a later bar, such as Ref($open, -8) / Ref($open, -1) - 1."))
    return rows


def neural_checks(*, estimates, params, cards, windows, concurrency):
    """The rows a neural network's runs add to the check, as `add` takes them: the graphics memory against the card's free memory, a batch bigger
    than the smallest window's training rows, and how long the runs take here. `params[i]` is group i's parameters, `cards` the NVIDIA cards by
    device name ("cuda:0"), `windows` (number, training rows) pairs."""
    rows = []
    over = [(item, cards[params[item["group"] - 1].get("device")]) for item in estimates
            if item.get("vram") and params[item["group"] - 1].get("device") in cards and item["vram"] > float(cards[params[item["group"] - 1]["device"]]["memoryFree"])]
    if over:
        item, card = max(over, key=lambda pair: pair[0]["vram"] - float(pair[1]["memoryFree"]))
        batch = int(params[item["group"] - 1].get("batch", 800))
        rows.append(("GPU memory", f"about {item['vram'] / GB:.1f} GB; {card_name(card)} has {float(card['memoryFree']) / GB:.1f} GB free", "fits", "block", "algorithm",
                     f"Batch size {batch} × look back {item['look']:.0f} × {item['features']} features. Choose a smaller batch size, or the CPU."))
    smallest = min(windows, key=lambda pair: pair[1]) if windows else None
    for item in estimates:
        batch = int(params[item["group"] - 1].get("batch") or 0)
        if smallest is not None and batch and batch > smallest[1]:
            rows.append(("Batch size", f"{batch}; window {smallest[0]} has {smallest[1]:,} training rows", f"≤ {smallest[1]:,}", "block", "algorithm",
                         f"Batch size {batch}; window {smallest[0]} has {smallest[1]:,} training rows. A window with fewer rows than one batch is not trained at all."))
            break
    concurrency = max(1, concurrency)
    total = sum(item["seconds"] for item in estimates) / concurrency
    shown = duration(total) + (f", {concurrency} at a time on this machine" if len(estimates) > 1 and concurrency > 1 else " on this machine")
    rows.append(("Time", f"about {shown}", "", "ok", "batch", None))
    return rows


def estimate(*, algorithm, params, rows, features, bars, names, windows, threads, calibration=None):
    """Peak memory (bytes) and time (seconds) of one training run: the data set in memory plus the largest window's training rows as the
    algorithm holds them; the time of each window from its rows, features and rounds, shared over the threads. `calibration` is this
    object's measured time against the estimate."""
    tensor = bars * names * features * 4 * 3
    if algorithm in A.NEURAL_IDS:
        look = float(params.get("lookback", 1)) if A.BY_ID[algorithm].get("shape") == "sequence" else 1.0
        memory = BASE + tensor + rows * look * features * 4 * 3 + 0.7 * GB
        epochs = min(float(params.get("epochs", 100)), 3 * float(params.get("stop", 20) or 20))
        per_window = SPEED_NN.get(algorithm, 3e-7) * rows * look * features * epochs * (8 / max(1, threads)) ** 0.8
        building = 2e-7 * bars * names * features
        return {"memory": float(memory), "seconds": float((building + windows * (per_window + 1.0)) * (calibration or 1.0)), "look": look, "features": features, "rows": rows,
                **({"vram": vram_of(algorithm, params, features, look)} if str(params.get("device") or "").startswith("cuda") else {})}
    train = rows * features * 4 * FACTOR.get(algorithm, 3.0) * 2
    memory = BASE + tensor + train
    if algorithm in SPEED:
        rounds = float(params.get("rounds", 1000))
        stop = float(params.get("stop", 0) or 0)
        effective = min(rounds, stop * 8) if stop else rounds
        if algorithm == "double_ensemble":
            effective = rounds * float(params.get("models", 6))
        per_window = SPEED[algorithm] * rows * features * effective / max(1, threads) ** 0.8
    else:
        per_window = 2e-10 * rows * features * features + 0.5
    building = 2e-7 * bars * names * features
    seconds = (building + windows * (per_window + 1.0)) * (calibration or 1.0)
    return {"memory": float(memory), "seconds": float(seconds)}


def _sample_columns(panel, count):
    step = max(1, len(panel.columns) // count)
    return panel.columns[::step][:count]


def _histogram(values, edges):
    values = values[np.isfinite(values)]
    if not len(values):
        return [0] * 40
    counts, _ = np.histogram(np.clip(values, edges[0], edges[1]), bins=40, range=edges)
    return counts.astype(int).tolist()


def _stats(values):
    values = values[np.isfinite(values)]
    if len(values) < 3:
        return {"mean": None, "std": None, "skew": None, "beyond": None}
    median = np.median(values)
    mad = np.median(np.abs(values - median)) * 1.4826 or 1.0
    std = values.std(ddof=1)
    skew = float(np.mean(((values - values.mean()) / std) ** 3)) if std > 0 else 0.0
    return {"mean": float(values.mean()), "std": float(std), "skew": skew, "beyond": float(np.mean(np.abs(values - median) > 3 * mad))}


class Check:
    def __init__(self, job, panel):
        self.job, self.panel = job, panel
        self.settings = job["settings"]
        self.label_formula = D.label_formula(self.settings["label"])
        try:
            from . import formula as F
            self.reach, self.label_error = F.reach(F.parse(self.label_formula, ahead=True), self.label_formula), None
        except Exception as error:  # noqa: BLE001 - a label that cannot be read is the check's answer, not a crash
            self.reach, self.label_error = None, error.to_dict() if hasattr(error, "to_dict") else {"kind": "syntax", "at": 0, "length": 0, "message": str(error)}
        self.days = (self.reach["ahead"] - self.reach["start"]["bar"]) if self.reach else 5
        self.ahead = self.reach["ahead"] if self.reach else 6
        self.gap_ahead = self.ahead
        for item in D.aux_formulas(self.label_formula, self.settings["label"].get("aux")) or []:
            try:  # a written auxiliary label that reads further sets the gap; one that cannot be read is a Labels check
                self.gap_ahead = max(self.gap_ahead, F.reach(F.parse(item, ahead=True), item)["ahead"])
            except ValueError:
                pass
        self.sample = None

    def tables(self):
        """Every input on the panel; built-in sets on a sample of names."""
        inputs = [D.Input(id=item["id"], name=item["name"], source=item["source"], ref=item.get("ref", ""), formula=item.get("formula"), path=item.get("path"), only=D.kept_columns((self.job.get("params") or {}).get("keep_columns")) if item["source"] == "builtin" else [])
                  for item in self.job["inputs"]]
        tables, failed = {}, []
        self.owner = {}  # which input each table came from
        sample = _sample_columns(self.panel, SAMPLE_NAMES)
        for item in inputs:
            try:
                found = D.input_tables(self.panel, [item], columns=sample if item.source == "builtin" else None, code=_no_code)
            except D.DataError as error:
                failed.append({"input": item, "message": error.failure["message"]})
                continue
            tables.update(found)
            self.owner.update({name: item.id for name in found})
        self.sample = [self.panel.columns.index(column) for column in sample]
        return inputs, tables, failed

    def run(self):
        started = time.perf_counter()
        panel, settings = self.panel, self.settings
        inputs, tables, failed = self.tables()
        rows = D.rows_of(panel, settings.get("leaveOut") or D.LEAVE_OUT_DEFAULT)
        warm = max([D.WARMUP.get(item.ref, 0) for item in inputs if item.source == "builtin"] + [self.reach["back"] if self.reach else 0])
        windows = D.layout(panel.labels, settings["split"], self.gap_ahead, warmup=warm)
        label = D.label_of(panel, D.split_form(self.label_formula)[0]) if self.reach else np.full(panel.shape, np.nan)
        checks, axis_inputs, input_rows = [], [], []
        names_a_day = rows[warm:].sum(axis=1) if len(rows) > warm else rows.sum(axis=1)
        valued_days = names_a_day[names_a_day > 0]
        low, high = (int(valued_days.min()), int(valued_days.max())) if len(valued_days) else (0, 0)

        def add(name, value, need, state, group, detail=None):
            checks.append({"name": name, "value": value, "need": need, "state": state, "group": group, **({"detail": detail} if detail else {})})

        if high < D.MIN_NAMES:
            shown = ", ".join(panel.columns[:3]) + ("…" if len(panel.columns) > 3 else "")
            add("Names a day", f"at most {high}", f"≥ {D.MIN_NAMES} to train", "block", "data",
                f"A model ranks names against each other each day, and these data have at most {high} {'name' if high == 1 else 'names'} a day ({shown}). "
                f"Choose at least {D.MIN_NAMES}: an index, a research list with more members or more symbols.")
        else:
            add("Names a day", f"{low} – {high}", f"≥ {NAMES_NEED}", "ok" if low >= NAMES_NEED else "watch", "data")
        # each input: where it starts, how much of the rows it covers, whether it is known in time
        research = slice(warm, None)
        for item in inputs:
            features = item.features or []
            entry = {"id": item.id, "name": item.name, "source": item.source, "features": len(features) or (len(item.only) or BUILTIN_FEATURES.get(item.ref, 1) if item.source == "builtin" else 1)}
            problem = next((found for found in failed if found["input"] is item), None)
            if problem:
                entry.update(flag="fix", note=problem["message"], coverage=0.0, start=None)
                input_rows.append(entry)
                continue
            cols = self.sample if item.source == "builtin" else slice(None)
            have, can = 0, 0
            first = None
            shares = []
            for name in features:
                values = tables[name][research][:, cols]
                mask = rows[research][:, cols]
                have += int(np.sum(np.isfinite(values) & mask))
                can += int(np.sum(mask))
                daily = (np.isfinite(values) & mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
                shares.append(daily)
                begun = np.nonzero(daily >= 0.5)[0]
                start = int(panel.labels[warm + begun[0]]) if len(begun) else None
                first = start if first is None or (start is not None and start > first) else first
            coverage = have / can if can else 0.0
            entry.update(coverage=coverage, start=first, note=item.note)
            late = item.ready in ("late", "next_close")
            if late:
                entry["flag"] = "fix"
                entry["note"] = item.note or "Known after the label starts"
            elif coverage < COVERAGE_NEED:
                entry["flag"] = "watch"
                entry["note"] = "; ".join(part for part in (item.note, _missing_part(np.mean(shares, axis=0) if shares else np.zeros(0), panel.labels[warm:])) if part)
            input_rows.append(entry)
            if first is not None and len(panel.labels) > warm and first > int(panel.labels[warm]) + 20 * D.DAY:
                axis_inputs.append({"name": item.name, "missingTo": first, "note": i18n.t("No values before {month}", month=_month(first))})
            if late:
                axis_inputs.append({"name": item.name, "peek": True, "note": entry["note"]})
            if not any(row["name"] == item.name for row in axis_inputs):
                axis_inputs.append({"name": item.name, "note": f"{entry.get('features', '')} features".strip() if entry.get("features") else item.name})
        if input_rows:
            under = [row for row in input_rows if row["coverage"] < COVERAGE_NEED and not row.get("flag") == "fix"]
            if under:
                shown = ", ".join(f"{row['name']} {row['coverage'] * 100:.0f}%" for row in under[:4]) + (f" and {len(under) - 4} more" if len(under) > 4 else "")
                add("Coverage", shown, f"≥ {COVERAGE_NEED * 100:.0f}%", "watch", "inputs", "; ".join(row["note"] for row in under if row.get("note")) or None)
            else:
                lowest = min(input_rows, key=lambda row: row["coverage"])
                add("Coverage", f"lowest {lowest['name']} {lowest['coverage'] * 100:.1f}%", f"≥ {COVERAGE_NEED * 100:.0f}%", "ok", "inputs")
            peeking = [row for row in input_rows if row.get("flag") == "fix" and row.get("coverage")]
            if peeking:
                add("Look-ahead", ", ".join(row["name"] for row in peeking), "known before the label", "fix", "inputs",
                    "; ".join(f"{row['name']}: {row['note']}" for row in peeking))
            else:
                add("Look-ahead", "none", "known before the label", "ok", "inputs", "Every input is known before the next open, where the label starts")
            for found in failed:
                add("Input", found["input"].name, "can be computed", "fix", "inputs", found["message"])
        else:
            add("Inputs", "none yet", "at least one", "notrun", "inputs")
        # nearly the same inputs, on a sample of days and names
        names = [name for name in tables if np.isfinite(tables[name][research]).any()]
        pairs = []
        if 1 < len(names) <= 600:
            days = np.unique(np.linspace(warm, len(panel.labels) - 1, SAMPLE_DAYS).astype(int))
            cols = self.sample
            stack = np.stack([tables[name][np.ix_(days, cols)] for name in names], axis=2)
            ranks = np.stack([D._rank(stack[:, :, f]) for f in range(stack.shape[2])], axis=2).reshape(-1, len(names))
            ok = np.isfinite(ranks).all(axis=1)
            if ok.sum() > 20:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    corr = np.corrcoef(ranks[ok].T)
                upper = np.triu(np.abs(np.nan_to_num(corr)) > DUPLICATE, 1)
                found = np.argwhere(upper)
                # Only pairs from two different inputs: a built-in set's own columns overlap by its design (Alpha158 has some fifty such pairs,
                # Alpha360 over a thousand), so counting them made this amber on every check and hid a real duplicate the user brought in.
                pairs = [(names[a], names[b], float(corr[a, b])) for a, b in found if self.owner[names[a]] != self.owner[names[b]]]
        if len(names) > 1:
            pairs.sort(key=lambda item: -abs(item[2]))
            add("Near duplicates", f"{len(pairs)} pairs above {DUPLICATE}" if pairs else "none", "none", "watch" if pairs else "ok", "inputs",
                f"{len(pairs)} pairs above {DUPLICATE}, e.g. {pairs[0][0]} and {pairs[0][1]}" if pairs else None)
        # the label
        research_label = np.where(rows, label, np.nan)[warm:len(panel.labels) - self.ahead]
        empty = int(np.sum((rows[warm:len(panel.labels) - self.ahead].sum(axis=1) > 0) & ~np.isfinite(research_label).any(axis=1))) if len(research_label) else 0
        wild = int(np.sum(np.abs(np.nan_to_num(research_label)) > 0.5))
        label_settings = settings["label"]
        distinct = None
        if not self.label_error and label_settings.get("objective") == "multiclass" and len(research_label):
            counts = [len(np.unique(day[np.isfinite(day)])) for day in research_label if np.isfinite(day).any()]
            distinct = int(min(counts)) if counts else 0
        if distinct is not None and distinct < int(label_settings.get("objectiveParam") or 5):
            need = int(label_settings.get("objectiveParam") or 5)
            add("Label", f"{distinct} distinct values a day; {need} groups need at least {need}", f"≥ {need}", "fix", "label", f"{distinct} distinct values a day; {need} groups need at least {need}")
            self.label_error = {"kind": "syntax", "at": 0, "length": 0, "message": i18n.t("{distinct} distinct values a day; {need} groups need at least {need}", distinct=distinct, need=need)}
        elif self.label_error:
            add("Label", self.label_error["message"], "reads a later bar", "fix", "label", self.label_error["message"])
        else:
            start = "the next open" if self.reach["start"] == {"bar": 1, "at": "open"} else "this close" if self.reach["start"] == {"bar": 0, "at": "close"} else \
                f"bar {self.reach['start']['bar']}'s {self.reach['start']['at']}"
            add("Label", "no empty day" if not empty else f"{empty} empty days", "no empty day", "ok" if not empty else "watch", "label",
                f"reads {self.ahead} bars ahead, from {start}; {wild} rows beyond ±50% before ranking")
        # Processing fitted on the training segment and the gaps of the label's length hold by construction (the axis draws the gaps): no row
        # pretends to check them.
        self.extra_block = False
        if label_settings.get("weight") == "formula":
            from . import formula as F
            source = (label_settings.get("weightFormula") or "").strip()
            problem = None
            if not source:
                problem = "write a formula"
            else:
                try:
                    F.parse(source)
                except F.FormulaError as error:
                    problem = "A weight reads only past and current bars" if error.kind == "future" else error.message
            if problem:
                add("Sample weight", problem, "a formula that reads only the past", "fix", "label", problem)
                self.extra_block = True
            else:
                try:
                    _weights, zero = D.weights_of(panel, label_settings, rows)
                except D.DataError as error:
                    add("Sample weight", error.failure["message"], "computable", "fix", "label", error.failure["message"])
                    self.extra_block = True
                else:
                    if zero > 0:
                        add("Sample weight", f"{zero * 100:.1f}% of rows weigh 0", "none", "watch", "label")
        # windows and their training rows
        drop = settings["processing"]["inputs"]["missing"] == "drop"
        complete = np.ones(panel.shape, dtype=bool)
        if drop:
            for name in tables:
                values = tables[name]
                if np.isfinite(values).any():
                    complete &= np.isfinite(values) if values.shape == panel.shape else complete
        counted = rows & np.isfinite(label) & complete
        axis_windows = []
        for window in windows:
            first, last = window.train
            count = int(counted[first:last + 1].sum())
            window.rows = count
        median_rows = float(np.median([w.rows for w in windows])) if windows else 0.0
        flagged = []
        for window in windows:
            spans = window.spans(panel.labels)
            entry = {"k": window.k, "train": spans["train"], "valid": spans["valid"], "test": spans["test"], "rows": window.rows}
            if median_rows and window.rows < 0.5 * median_rows:
                kept = window.rows / median_rows
                missing = next((row["name"] for row in axis_inputs if row.get("missingTo") and row["missingTo"] > spans["train"]["from"]), None)
                entry["flag"] = f"{kept * 100:.0f}% of the usual training rows" + (f": {missing} starts later and rows with a missing input are dropped" if missing and drop else "")
                flagged.append(window.k)
            axis_windows.append(entry)
        if windows:
            lows = [w.rows for w in windows]
            short = [w.k for w in windows if w.rows < ROWS_NEED]
            value = f"{_k(min(lows))} – {_k(max(lows))} a window"
            if short and len(short) < len(windows):
                value = f"windows {short[0]}–{short[-1]}: {_k(min(w.rows for w in windows if w.k in short))} – {_k(max(w.rows for w in windows if w.k in short))}"
            add("Training rows", value, f"≥ {_k(ROWS_NEED)}", "ok" if not short else "watch", "data")
        else:
            add("Windows", "none", "at least one", "block", "split", _room_for_windows(panel.labels, settings["split"], self.gap_ahead, warm))
        # the algorithm and the memory of each group
        groups = self.job.get("groups") or []
        features = sum(len(item.features) or (len(item.only) or BUILTIN_FEATURES.get(item.ref, 1) if item.source == "builtin" else 1) for item in inputs)
        algorithm = self.job.get("algorithm")
        if not algorithm:
            add("Algorithm", "not chosen", "chosen", "notrun", "algorithm")
        elif algorithm in A.NEURAL_IDS and self.job.get("deepInstalled") is False:
            add("Environment", "Deep learning environment not installed", "installed", "block", "algorithm", "Install the deep learning environment in the Algorithm settings.")
        for row in route_b_checks(algorithm, self.job.get("concepts"), D.label_formula(settings["label"]), settings["label"].get("aux") or ""):
            add(*row)
        row = start_from_row(algorithm, (self.job.get("startFrom") or {}).get("own") or {}, (self.job.get("startFrom") or {}).get("base"))
        if row:
            add(*row)
        # Runs wait in the queue for memory that is in use now; only more than runs on this computer ever get blocks (it refused against what
        # was free at that moment and never came back to it).
        budget = float(self.job.get("budget") or 0)
        rows_max = max([w.rows for w in windows] + [0])
        estimates = []
        cards = {f"cuda:{card['index']}": card for card in self.job.get("devices") or []}
        for index, group in enumerate(groups or [{"algorithm": algorithm, "params": self.job.get("params") or {}, "features": features}]):
            found = estimate(algorithm=group.get("algorithm") or algorithm or "lightgbm", params=group.get("params") or {}, rows=rows_max,
                             features=int(group.get("features") or features or 1), bars=len(panel.labels), names=len(panel.columns), windows=len(windows),
                             threads=int(self.job.get("threads") or 1), calibration=self.job.get("calibration"))
            estimates.append({"group": index + 1, **found})
        if not windows:
            estimates = []   # nothing would train: no time or memory to estimate
        if estimates and algorithm:
            worst = max(estimates, key=lambda item: item["memory"])
            if budget and worst["memory"] > budget:
                # An estimate is no reason to refuse: the run goes alone and, if the machine truly runs out, fails saying how much it used.
                label_group = f"group {worst['group']}" if len(estimates) > 1 else "this run"
                add("Memory", f"{label_group} needs about {worst['memory'] / GB:.1f} GB; runs get {budget / GB:.1f} GB", "fits", "watch", "batch")
            else:
                add("Memory", f"{worst['memory'] / GB:.1f} GB of the {budget / GB:.1f} GB runs get" if budget else f"{worst['memory'] / GB:.1f} GB", "fits", "ok", "batch")
        if algorithm in A.NEURAL_IDS and estimates:
            for row in neural_checks(estimates=estimates, params=[(group.get("params") or {}) for group in (groups or [{"params": self.job.get("params") or {}}])], cards=cards,
                                     windows=[(w.k, w.rows) for w in windows], concurrency=int(self.job.get("concurrency") or 1)):
                add(*row)
        batch_default = None
        if algorithm in A.NEURAL_IDS and estimates and windows:
            first = (groups[0].get("params") if groups else self.job.get("params")) or {}
            if first.get("device") in cards:
                batch_default = default_batch(cards[first["device"]], algorithm, first, estimates[0]["features"], estimates[0]["look"], min(w.rows for w in windows))
        engine = self.engine_check()
        if engine and not engine["ok"]:
            add("Engine parameters", engine["errors"][0]["message"], "valid", "fix", "algorithm", engine["errors"][0]["message"])
        order = {"block": 0, "fix": 1, "watch": 2, "notrun": 3, "ok": 4}
        checks.sort(key=lambda item: order[item["state"]])
        held = self.job.get("heldOut")
        end = int(held["to"]) if held else int(panel.labels[-1])
        axis = {"start": int(panel.labels[0]), "end": end, "warmUp": {"from": int(panel.labels[0]), "to": int(panel.labels[min(warm, len(panel.labels) - 1)])},
                "period": {"from": int(panel.labels[min(warm, len(panel.labels) - 1)]), "to": int(panel.labels[-1])},
                "heldOut": {"from": int(held["from"]), "to": int(held["to"])} if held else None, "inputs": axis_inputs,
                "windows": _shown(axis_windows, flagged), "count": len(axis_windows), "every": f"every {D.months_label(int(settings['split']['everyMonths']))}",
                "gap": self.gap_ahead - 1}
        return {"ok": True, "checks": checks, "axis": axis, "inputs": input_rows, "features": features, "estimates": estimates, "windows": len(windows),
                "rowsPerWindow": rows_max, "bars": len(panel.labels), "names": len(panel.columns), "seconds": round(time.perf_counter() - started, 2),
                "blocked": any(item["state"] == "block" for item in checks) or bool(self.label_error) or self.extra_block or bool(engine and not engine["ok"]),
                "label": {"reach": self.reach, "error": self.label_error}, "engineParams": engine, **({"batchDefault": batch_default} if batch_default else {})}

    def engine_check(self):
        """Engine parameters, checked against the library (a few tens of milliseconds): {ok, errors: [{key, message}]}, or None when none are written."""
        text = (self.job.get("engineText") or "").strip()
        algorithm = self.job.get("algorithm")
        if not text or not algorithm:
            return None
        try:
            A.engine_params(algorithm, text, declared=set(A.CONTROL_NAMES.get(algorithm, {}).values()))
        except ValueError as error:
            message = str(error)
            key = message.split(":")[0].split(" ")[0] if ":" in message or " is set" in message else ""
            return {"ok": False, "errors": [{"key": key, "message": message}]}
        return {"ok": True, "errors": []}

    def inspect(self, input_id, feature=None):
        """One input before and after processing, on the windows' training segments only: two histograms, the share missing each week, and
        a few statistics."""
        inputs, tables, failed = self.tables()
        item = next((entry for entry in inputs if entry.id == input_id), None)
        if item is None:
            return {"ok": False, "failure": {"stage": "inputs", "title": i18n.t("no such input"), "message": i18n.t("This input is not in the preparation.")}}
        if any(found["input"] is item for found in failed):
            return {"ok": False, "failure": {"stage": "inputs", "title": i18n.t("cannot be computed"), "message": next(found["message"] for found in failed if found["input"] is item)}}
        name = feature if feature in item.features else item.features[0]
        warm = max([D.WARMUP.get(entry.ref, 0) for entry in inputs if entry.source == "builtin"] + [0])
        windows = D.layout(self.panel.labels, self.settings["split"], self.ahead, warmup=warm)
        training = np.zeros(len(self.panel.labels), dtype=bool)
        for window in windows:
            training[window.train[0]:window.train[1] + 1] = True
        rows = D.rows_of(self.panel, self.settings.get("leaveOut") or D.LEAVE_OUT_DEFAULT)
        before = np.where(rows, tables[name], np.nan)
        after = D.process_day(before[:, :, None], self.panel, self.settings["processing"]["inputs"])[:, :, 0]
        if self.settings["processing"]["inputs"]["scale"] == "robust" or self.settings["processing"]["inputs"]["missing"] == "train_median":
            fitted = D.fit_window(before[training][rows[training]][:, None], self.settings["processing"]["inputs"])
            after = np.where(rows, D.apply_fitted(after.reshape(-1, 1), fitted, self.settings["processing"]["inputs"]).reshape(after.shape), np.nan)
        b, a = before[training][rows[training]], after[training][rows[training]]
        finite = b[np.isfinite(b)]
        edges = (float(np.nanpercentile(finite, 1)), float(np.nanpercentile(finite, 99))) if len(finite) else (0.0, 1.0)
        if edges[0] == edges[1]:
            edges = (edges[0] - 1, edges[1] + 1)
        weeks = pd.Series(training, index=self.panel.dates)
        share = pd.Series(np.where(training, (~np.isfinite(before) & rows).sum(axis=1) / np.maximum(rows.sum(axis=1), 1), np.nan), index=self.panel.dates)
        weekly = share[weeks].resample("W").mean().dropna()
        missing = [[int(stamp.value // 1_000_000), round(float(value), 4)] for stamp, value in weekly.items()]
        sb, sa = _stats(b), _stats(a)
        missing_share = float(np.mean(~np.isfinite(b))) if len(b) else None
        policy = self.settings["processing"]["inputs"]["missing"]

        def fmt(value, kind="number"):
            if value is None:
                return "—"
            return f"{value * 100:.1f}%" if kind == "share" else f"{value:+.3f}" if kind == "signed" else f"{value:.3f}"
        stats = [{"label": i18n.t("Mean"), "before": fmt(sb["mean"], "signed"), "after": fmt(sa["mean"], "signed")},
                 {"label": i18n.t("Std"), "before": fmt(sb["std"]), "after": fmt(sa["std"])},
                 {"label": i18n.t("Skew"), "before": fmt(sb["skew"], "signed"), "after": fmt(sa["skew"], "signed")},
                 {"label": i18n.t("Beyond 3 MAD"), "before": fmt(sb["beyond"], "share"), "after": fmt(sa["beyond"], "share")},
                 {"label": i18n.t("Missing"), "before": fmt(missing_share, "share"), "after": i18n.t("dropped") if policy == "drop" else i18n.t("left") if policy == "model" else i18n.t("filled")}]
        note = (f"Missing {missing_share * 100:.0f}% of the training rows" if missing_share else "No missing values on the training rows") + (
            "; rows with a missing input are dropped, so those days train on fewer names." if policy == "drop" and missing_share else ".")
        after_edges = (float(np.nanmin(a)), float(np.nanmax(a))) if np.isfinite(a).any() else (0.0, 1.0)
        before_counts = _histogram(b, edges)
        after_range = after_edges if after_edges[0] < after_edges[1] else (0.0, 1.0)
        after_counts = _histogram(a, after_range)
        return {"ok": True, "name": name, "input": item.name, "features": item.features[:200], "before": before_counts, "after": after_counts,
                "edges": list(edges), "afterEdges": list(after_edges), "missing": missing, "stats": stats, "note": note,
                "figures": {"model.inspect.distribution": _distribution_figure(before_counts, edges, after_counts, after_range),
                            "model.inspect.missing": _missing_figure(missing)}}


def _distribution_figure(before, edges, after, after_edges):
    """Before (thin grey) and after processing (body colour), 40 bins each; hovering a bin reads its range and the two row counts."""
    def centres(low, high):
        step = (high - low) / 40
        return [low + step * (index + .5) for index in range(40)], step

    def trace(counts, bounds, name, role):
        x, step = centres(*bounds)
        return {"type": "bar", "x": x, "y": counts, "width": step, "name": name, "meta": {"role": role, "key": name.lower()},
                "customdata": [[round(value - step / 2, 3), round(value + step / 2, 3)] for value in x],
                "hovertemplate": name + " %{customdata[0]:.2f} to %{customdata[1]:.2f}: %{y:,} rows<extra></extra>"}
    return MF.figure("model.inspect.distribution", [trace(before, edges, "Before", "benchmark"), trace(after, after_edges, "After", "main")],
                     barmode="overlay", bargap=0, hovermode="x unified")


def _missing_figure(missing):
    """The share of rows missing each week, on the training segments only."""
    days = [pd.Timestamp(stamp, unit="ms").strftime("%Y-%m-%d") for stamp, _ in missing]
    figure = MF.figure("model.inspect.missing", [{"type": "bar", "x": days, "y": [round(value * 100, 2) for _, value in missing], "name": "Missing", "meta": {"role": "benchmark", "key": "missing"},
                                                 "customdata": [pd.Timestamp(stamp, unit="ms").strftime("%b %d, %Y") for stamp, _ in missing],
                                                 "hovertemplate": "Week of %{customdata}: %{y:.1f}% missing<extra></extra>"}], yaxis={"ticksuffix": "%"})
    return figure


def _no_code(item):
    raise D.DataError("an input cannot be computed", f"{item.name} is written in code; the check before training does not run code. Its coverage shows after a run.",
                      input=item.id)


def _k(value):
    return f"{value / 1000:.0f}k" if value >= 10_000 else f"{value:,}"


def _shown(windows, flagged):
    """Many windows draw only the first few, the last and the flagged ones; the rest are counted between them."""
    if len(windows) <= 8:
        return windows
    keep = sorted({0, 1, len(windows) - 1, *[k - 1 for k in flagged]})
    return [windows[index] for index in keep]


def handle(op, job, load_panel):
    from .factor_resident import load_panel as load
    panel = (load_panel or load)(job["data"])
    held = job.get("heldOut")
    if held:
        panel = panel.truncate(int(held["from"]))
    check = Check(job, panel)
    try:
        if op == "model_inspect":
            return check.inspect(job["input"], job.get("feature"))
        return check.run()
    except D.DataError as error:
        return {"ok": False, "failure": error.failure}


def finite(value):
    return None if value is None or not math.isfinite(value) else float(value)
