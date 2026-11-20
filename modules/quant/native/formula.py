"""One-line factor formulas: Qlib expressions with TALYX's cross-section operators.

Parsing and checking need only the standard library, so the Host checks a formula while it is typed (`check`) and the research
environment evaluates it (`evaluate`). Time-series and element operators are Qlib's own (`qlib.data.ops`), fed from tables in
memory rather than Qlib's data folder; the operators across names (CSRank, CSZScore, CSDemean, IndRank, Neutralize) are
TALYX's. A formula only looks back: a shift into later days is refused when the formula is read, so no evaluation can use
future data by construction.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------------------------------------------- declarations
# kind: "ts" over time (Qlib Rolling or Ref), "elem" element by element (Qlib), "cs" across names (TALYX), "series" other series.
# args: the operand kinds in order; "x" a series, "n" a whole number of bars, "q" a number, "s" text, "f" a classification field.
@dataclass(frozen=True)
class Operator:
    name: str
    group: str
    kind: str
    args: tuple
    help: str
    optional: int = 0

    @property
    def signature(self):
        names = {"x": "x", "n": "n", "q": "q", "s": '"…"', "f": "$industry"}
        parts, seen = [], {}
        for index, arg in enumerate(self.args):
            label = names[arg]
            if arg == "x":
                seen["x"] = seen.get("x", 0) + 1
                label = "x" if seen["x"] == 1 else "y" if seen["x"] == 2 else "z"
            parts.append(label if index < len(self.args) - self.optional else f"[{label}]")
        return f"{self.name}({', '.join(parts)})"


def _ops():
    ts, elem, cs, other = "Over time", "Math and logic", "Across names", "Other series"
    rolling = [("Mean", "average over the last n days"), ("Sum", "sum over the last n days"), ("Std", "standard deviation over the last n days"),
               ("Var", "variance over the last n days"), ("Skew", "skew over the last n days"), ("Kurt", "kurtosis over the last n days"),
               ("Max", "highest over the last n days"), ("Min", "lowest over the last n days"), ("Med", "median over the last n days"),
               ("Mad", "mean absolute deviation over the last n days"), ("Slope", "slope of a line through the last n days"),
               ("Rsquare", "R squared of that line"), ("Resi", "residual of that line"), ("Rank", "rank of today among the last n days, 0 to 1"),
               ("Count", "days with a value in the last n"), ("EMA", "exponential average with span n"), ("WMA", "linearly weighted average"),
               ("IdxMax", "days since the highest in the last n"), ("IdxMin", "days since the lowest in the last n"), ("Delta", "x minus x n days earlier")]
    items = [Operator("Ref", ts, "ts", ("x", "n"), "x n days earlier; n must be 1 or more")]
    items += [Operator(name, ts, "ts", ("x", "n"), text) for name, text in rolling]
    items += [Operator("Quantile", ts, "ts", ("x", "n", "q"), "the q quantile over the last n days"),
              Operator("Corr", ts, "ts", ("x", "x", "n"), "correlation of x and y over the last n days"),
              Operator("Cov", ts, "ts", ("x", "x", "n"), "covariance of x and y over the last n days")]
    items += [Operator("CSRank", cs, "cs", ("x",), "rank across names each day, 0 to 1"),
              Operator("CSZScore", cs, "cs", ("x",), "z-score across names each day"),
              Operator("CSDemean", cs, "cs", ("x",), "minus the mean across names each day"),
              Operator("IndRank", cs, "cs", ("x",), "rank inside each industry each day, 0 to 1"),
              Operator("Neutralize", cs, "cs", ("x", "f", "x"), "residual after industry dummies and log size each day", optional=2)]
    items += [Operator("Abs", elem, "elem", ("x",), "absolute value"), Operator("Log", elem, "elem", ("x",), "natural log"),
              Operator("Sign", elem, "elem", ("x",), "-1, 0 or 1"), Operator("Power", elem, "elem", ("x", "q"), "x to the power q"),
              Operator("If", elem, "elem", ("x", "x", "x"), "y where x holds, else z"), Operator("Greater", elem, "elem", ("x", "x"), "the larger of x and y"),
              Operator("Less", elem, "elem", ("x", "x"), "the smaller of x and y"), Operator("Gt", elem, "elem", ("x", "x"), "1 where x > y"),
              Operator("Ge", elem, "elem", ("x", "x"), "1 where x ≥ y"), Operator("Lt", elem, "elem", ("x", "x"), "1 where x < y"),
              Operator("Le", elem, "elem", ("x", "x"), "1 where x ≤ y"), Operator("Eq", elem, "elem", ("x", "x"), "1 where x = y"),
              Operator("Ne", elem, "elem", ("x", "x"), "1 where x ≠ y"), Operator("And", elem, "elem", ("x", "x"), "both hold"),
              Operator("Or", elem, "elem", ("x", "x"), "either holds"), Operator("Not", elem, "elem", ("x",), "does not hold")]
    items += [Operator("ChangeInstrument", other, "series", ("s", "x"), "x computed on another instrument of this data, such as an index or benchmark"),
              Operator("TResample", other, "series", ("x", "s"), "x on a slower calendar (1w, 1m), carried forward once each period has closed")]
    return {item.name: item for item in items}


OPERATORS = _ops()
OPERATOR_GROUPS = ("Over time", "Across names", "Math and logic", "Other series")
PRICE_FIELDS = {"$open": "Open", "$high": "High", "$low": "Low", "$close": "Close", "$volume": "Volume",
                "$vwap": "Average price, (H + L + C) / 3", "$amount": "Dollar volume, close × volume"}
CLASSIFICATION = {"$industry"}


def declared_operators():
    """The operators the formula box offers, as the contract describes them."""
    return [{"name": item.name, "group": item.group, "signature": item.signature, "help": item.help} for item in OPERATORS.values()]


# ---------------------------------------------------------------------------------------------------------------- reading
class FormulaError(ValueError):
    """A mistake at a place in the formula: `kind` is function, field, brackets, future or syntax; `at` and `length` are character offsets."""

    def __init__(self, kind, at, length, message, fix=None):
        super().__init__(message)
        self.kind, self.at, self.length, self.message, self.fix = kind, at, length, message, fix

    def to_dict(self):
        return {"kind": self.kind, "at": self.at, "length": self.length, "message": self.message, **({"fix": self.fix} if self.fix else {})}


@dataclass
class Node:
    kind: str  # num, str, field, call, neg, bin
    at: int
    length: int
    value: object = None
    args: list = field(default_factory=list)

    def text(self, source):
        return source[self.at:self.at + self.length]


TOKEN = re.compile(r"\s*(?:(?P<num>\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)|(?P<field>\$[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)?)|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
                   r"|(?P<str>\"[^\"]*\"|'[^']*')|(?P<op>>=|<=|==|!=|[-+*/()<>,&|]))")


def _tokens(source):
    tokens, index = [], 0
    while index < len(source):
        if source[index].isspace():
            index += 1
            continue
        match = TOKEN.match(source, index)
        if match is None or match.end() == index:
            raise FormulaError("syntax", index, 1, f"{source[index]!r} cannot be read here")
        kind = match.lastgroup
        start = match.start(kind)
        tokens.append((kind, match.group(kind), start))
        index = match.end()
    tokens.append(("end", "", len(source)))
    return tokens


def _distance(a, b):
    row = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        previous, row[0] = row[0], i
        for j in range(1, len(b) + 1):
            kept = row[j]
            row[j] = min(row[j] + 1, row[j - 1] + 1, previous + (a[i - 1] != b[j - 1]))
            previous = kept
    return row[-1]


def closest_operator(name):
    """The declared operator a misspelt name most likely means: one that starts the same, or at most two edits away and fewer edits than
    half the name ("Foo" is two edits from "Cov" too, and offering Cov for it is a guess, not a fix)."""
    lower = name.lower()
    for item in OPERATORS:
        if lower.startswith(item.lower()) or item.lower().startswith(lower):
            return item
    ranked = sorted(OPERATORS, key=lambda item: _distance(lower, item.lower()))
    edits = _distance(lower, ranked[0].lower()) if ranked else None
    return ranked[0] if edits is not None and edits <= 2 and edits < len(lower) / 2 else None


class _Parser:
    def __init__(self, source):
        self.source, self.tokens, self.position = source, _tokens(source), 0

    def peek(self):
        return self.tokens[self.position]

    def take(self):
        token = self.tokens[self.position]
        self.position += 1
        return token

    def expect(self, value):
        kind, text, at = self.take()
        if text != value:
            if value == ")" :
                raise FormulaError("brackets", at, 0 if kind == "end" else len(text), "A bracket is not closed" if kind == "end" else f"Expected ) before {text!r}")
            raise FormulaError("syntax", at, len(text) or 1, f"Expected {value!r}" + (f" before {text!r}" if text else " at the end"))

    def parse(self):
        node = self.expression()
        kind, text, at = self.peek()
        if kind != "end":
            if text == ")":
                raise FormulaError("brackets", at, 1, "This ) has no (")
            raise FormulaError("syntax", at, len(text), f"{text!r} cannot follow here; an operator such as + or * is missing")
        return node

    def binary(self, level):
        levels = [("|",), ("&",), (">", "<", ">=", "<=", "==", "!="), ("+", "-"), ("*", "/")]
        if level == len(levels):
            return self.unary()
        left = self.binary(level + 1)
        while self.peek()[0] == "op" and self.peek()[1] in levels[level]:
            _, operator, at = self.take()
            right = self.binary(level + 1)
            left = Node("bin", left.at, right.at + right.length - left.at, operator, [left, right])
        return left

    expression = lambda self: self.binary(0)  # noqa: E731

    def unary(self):
        kind, text, at = self.peek()
        if kind == "op" and text in "+-" and text:
            self.take()
            operand = self.unary()
            return operand if text == "+" else Node("neg", at, operand.at + operand.length - at, None, [operand])
        return self.atom()

    def atom(self):
        kind, text, at = self.take()
        if kind == "num":
            return Node("num", at, len(text), float(text))
        if kind == "str":
            return Node("str", at, len(text), text[1:-1])
        if kind == "field":
            return Node("field", at, len(text), text)
        if kind == "name":
            if self.peek()[1] != "(":
                raise FormulaError("syntax", at, len(text), f"{text} needs ( after it" if text in OPERATORS else f"Unknown name {text}; fields start with $")
            self.take()
            args = []
            if self.peek()[1] != ")":
                args.append(self.expression())
                while self.peek()[1] == ",":
                    self.take()
                    args.append(self.expression())
            closing = self.peek()
            self.expect(")")
            return Node("call", at, closing[2] + 1 - at, text, args)
        if text == "(":
            inner = self.expression()
            closing = self.peek()
            self.expect(")")
            return Node(inner.kind, at, closing[2] + 1 - at, inner.value, inner.args) if inner.kind in {"bin", "neg"} else inner
        if kind == "end":
            raise FormulaError("syntax", at, 0, "The formula ends where a value is needed")
        if text == ")":
            raise FormulaError("brackets", at, 1, "This ) has no (")
        raise FormulaError("syntax", at, len(text), f"{text!r} cannot start a value")


def _scan(source, fields):
    """The mistakes the box marks while typing, in the order the formula reads: an unknown function, an unknown or unavailable field, brackets."""
    for match in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", source):
        if match.group(1) not in OPERATORS:
            fix = closest_operator(match.group(1))
            raise FormulaError("function", match.start(1), len(match.group(1)), f"Unknown function {match.group(1)}", fix)
    for match in re.finditer(r"\$[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)?", source):
        declared = fields.get(match.group(0)) if fields is not None else {}
        if declared is None:
            known = [name for name, item in fields.items() if not item.get("unavailable")]
            near = min(known, key=lambda name: _distance(match.group(0).lower(), name.lower()), default=None)
            fix = near if near is not None and _distance(match.group(0).lower(), near.lower()) <= 2 else None
            raise FormulaError("field", match.start(), len(match.group(0)), f"Unknown field {match.group(0)}", fix)
        if declared.get("unavailable"):
            raise FormulaError("field", match.start(), len(match.group(0)), f"{match.group(0)} is not available: {declared['unavailable']}")
    depth = 0
    for index, character in enumerate(source):
        depth += character == "("
        if character == ")":
            depth -= 1
            if depth < 0:
                raise FormulaError("brackets", index, 1, "This ) has no (")
    if depth > 0:
        raise FormulaError("brackets", len(source), 0, "A bracket is not closed" if depth == 1 else f"{depth} brackets are not closed")


def _whole(node, source):
    if node.kind == "neg" and node.args[0].kind == "num":
        return -node.args[0].value
    return node.value if node.kind == "num" else None


def _check_call(node, source, ahead=False):
    operator = OPERATORS[node.value]
    low, high = len(operator.args) - operator.optional, len(operator.args)
    if not low <= len(node.args) <= high:
        count = f"{low}" if low == high else f"{low} to {high}"
        raise FormulaError("function", node.at, len(node.value), f"{node.value} takes {count} argument{'s' if high > 1 else ''}: {operator.signature}")
    for spec, arg in zip(operator.args, node.args, strict=False):
        if spec == "n":
            value = _whole(arg, source)
            if value is None or value != int(value):
                raise FormulaError("function", arg.at, arg.length, f"{node.value} needs a whole number of days here")
            if value < 0 and ahead and node.value == "Ref":
                continue  # a label may look ahead, with Ref only
            if value < 0 and ahead:
                raise FormulaError("future", node.at, node.length, "Only Ref can look ahead in a label")
            if value < 0:
                raise FormulaError("future", node.at, node.length, f"{node.value} with a negative shift reads later days")
            if value == 0 and node.value in {"Ref", "Delta"}:
                raise FormulaError("function", arg.at, arg.length, f"{node.value} needs a shift of 1 day or more")
            # Qlib reads a window of 0 as the whole history so far: Mean($close, 0) would be another factor than the one written.
            if value == 0 and node.value in _ROLLING_WINDOW:
                raise FormulaError("function", arg.at, arg.length, f"{node.value} needs a window of 1 day or more")
        elif spec == "q" and _whole(arg, source) is None:
            raise FormulaError("function", arg.at, arg.length, f"{node.value} needs a number here")
        elif spec == "s" and arg.kind != "str":
            raise FormulaError("function", arg.at, arg.length, f'{node.value} needs text in quotes here, such as "SPY"' if node.value == "ChangeInstrument" else f'{node.value} needs a period in quotes, such as "1w"')
        elif spec == "f" and not (arg.kind == "field" and arg.value in CLASSIFICATION) and not (node.value == "Neutralize" and len(node.args) == 2):
            raise FormulaError("function", arg.at, arg.length, f"{node.value} takes $industry here")
        elif spec == "x" and arg.kind == "str":
            raise FormulaError("function", arg.at, arg.length, f"{node.value} needs a series here, not text")
    if node.value == "TResample" and node.args[1].value not in RESAMPLE:
        raise FormulaError("function", node.args[1].at, node.args[1].length, "TResample takes \"1w\" or \"1m\"")


def _walk(node):
    yield node
    for arg in node.args:
        yield from _walk(arg)


def parse(source, fields=None, *, ahead=False):
    """The formula's tree, or FormulaError at the first mistake. `fields` maps each declared field to its declaration (`unavailable` gives why not).
    `ahead` is for a label: `Ref` may then take a negative shift (read a later bar); nothing else may, and a factor formula never passes it."""
    if not isinstance(source, str) or not source.strip():
        raise FormulaError("syntax", 0, 0, "Write a formula first")
    if len(source) > 4000:
        raise FormulaError("syntax", 4000, 0, "A formula is at most 4000 characters; longer ones belong in code")
    _scan(source, fields)
    tree = _Parser(source).parse()
    for node in _walk(tree):
        if node.kind == "call":
            _check_call(node, source, ahead)
    if all(node.kind not in {"field", "call"} for node in _walk(tree)):
        raise FormulaError("syntax", 0, len(source), "A formula needs a field such as $close")
    return tree


_ROLLING_WINDOW = {"Mean", "Sum", "Std", "Var", "Skew", "Kurt", "Max", "Min", "Med", "Mad", "Slope", "Rsquare", "Resi", "Rank", "Count", "EMA", "WMA", "IdxMax", "IdxMin",
                   "Quantile", "Corr", "Cov"}


def _reads(node, source):
    """Each field the subtree reads, with the offsets (from the bar the value is for) it reads: [(field, lowest, highest)]."""
    if node.kind == "field":
        return [(node.value, 0, 0)]
    if node.kind in {"num", "str"}:
        return []
    inner = [read for arg in node.args for read in _reads(arg, source)] if node.kind != "call" else None
    if node.kind != "call":
        return inner
    name, operator = node.value, OPERATORS[node.value]
    if name == "ChangeInstrument":
        return _reads(node.args[1], source)
    if name == "TResample":
        return [(field, float("-inf"), high) for field, _low, high in _reads(node.args[0], source)]
    series = [read for arg, spec in zip(node.args, operator.args, strict=False) if spec == "x" for read in _reads(arg, source)]
    if name == "Ref":
        shift = _whole(node.args[1], source)
        return [(field, low - shift, high - shift) for field, low, high in series]
    if name == "Delta":
        window = _whole(node.args[1], source)
        return [(field, low - window, high) for field, low, high in series]
    if name in _ROLLING_WINDOW:
        window = _whole(node.args[-1 if name not in {"Quantile"} else 1], source)
        return [(field, low - (window - 1), high) for field, low, high in series]
    return series


def _back(node, source):
    """How many bars before the first one a formula needs: Qlib's own left extension of the window (a rolling operator over a later-reading
    series still starts with part-filled windows, so its window counts)."""
    if node.kind in {"field", "num", "str"}:
        return 0
    if node.kind != "call":
        return max([_back(arg, source) for arg in node.args] + [0])
    name, operator = node.value, OPERATORS[node.value]
    if name == "ChangeInstrument":
        return _back(node.args[1], source)
    inner = max([_back(arg, source) for arg, spec in zip(node.args, operator.args, strict=False) if spec == "x"] + [0])
    if name == "Ref":
        shift = _whole(node.args[1], source)
        return max(inner + shift, inner)
    if name == "Delta":
        return inner + _whole(node.args[1], source)
    if name in _ROLLING_WINDOW:
        return inner + _whole(node.args[-1 if name != "Quantile" else 1], source) - 1
    return inner


def reach(tree, source=""):
    """What a label formula reads: `ahead` bars after its own, `back` bars before the first one (the warm-up it needs), and `start`, the earliest
    moment it reads that is not yet known at today's open (`$open` is the open, any other field the close). Raises when it reads no later bar."""
    reads = _reads(tree, source)
    ahead = int(max([high for _field, _low, high in reads] + [0]))
    if ahead <= 0:
        raise FormulaError("future", tree.at, tree.length, "A label must read a later bar, such as Ref($open, -6)")
    moments = []
    for read, low, high in reads:
        first = max(int(low) if low != float("-inf") else 0, 0)
        for bar in range(first, int(high) + 1):
            if bar >= 1 or read != "$open":
                moments.append((bar, 0 if read == "$open" else 1))
    bar, at = min(moments)
    return {"ahead": ahead, "back": int(_back(tree, source)), "start": {"bar": bar, "at": "open" if at == 0 else "close"}}


def check(source, fields=None):
    """None, or the first mistake as the contract's FormulaError."""
    try:
        parse(source, fields)
    except FormulaError as error:
        return error.to_dict()
    return None


def leaves(tree):
    """The fields a formula reads, in the order they first appear."""
    seen = []
    for node in _walk(tree):
        if node.kind == "field" and node.value not in seen:
            seen.append(node.value)
    return seen


def instruments(tree):
    """Other instruments a formula reads through ChangeInstrument."""
    return sorted({node.args[0].value for node in _walk(tree) if node.kind == "call" and node.value == "ChangeInstrument"})


def inputs(tree, source, limit=4):
    """What the values-on-a-day table shows beside each value: the fields read, then the operands of the outermost operation."""
    found = [name for name in leaves(tree) if name not in CLASSIFICATION]

    def bare(node):
        while node.kind == "neg":
            node = node.args[0]
        return node

    node = bare(tree)
    for arg in node.args if node.kind in {"bin", "call"} else []:
        arg = bare(arg)
        if arg.kind in {"num", "str", "field"}:
            continue
        text = _unwrap(arg.text(source).strip())
        if text not in found:
            found.append(text)
    return found[:limit]


def _unwrap(text):
    """The text without brackets around all of it."""
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        for index, character in enumerate(text):
            depth += (character == "(") - (character == ")")
            if depth == 0 and index < len(text) - 1:
                return text
        text = text[1:-1].strip()
    return text


# ---------------------------------------------------------------------------------------------------------------- to code
_PANDAS_FIELDS = {"$open": "data.open", "$high": "data.high", "$low": "data.low", "$close": "data.close", "$volume": "data.volume",
                  "$vwap": "data.vwap", "$amount": "data.amount", "$industry": "data.industry", "$size": "data.size", "$shares": "data.shares"}
_PYTHON = {"&": "&", "|": "|", "==": "==", "!=": "!=", ">": ">", "<": "<", ">=": ">=", "<=": "<=", "+": "+", "-": "-", "*": "*", "/": "/"}
_PRECEDENCE = {"|": 1, "&": 2, ">": 3, "<": 3, ">=": 3, "<=": 3, "==": 3, "!=": 3, "+": 4, "-": 4, "*": 5, "/": 5}


def _field_code(name):
    if name in _PANDAS_FIELDS:
        return _PANDAS_FIELDS[name]
    if name.startswith("$lib."):
        return f"data.library({name[5:]!r})"
    if name.startswith("$fact."):
        return f"data.fact({name[6:]!r})"
    return f"data.field({name[1:]!r})"


def _code(node, source, parent=0):
    if node.kind == "num":
        value = node.value
        return repr(int(value)) if value == int(value) and abs(value) < 1e15 else repr(value)
    if node.kind == "str":
        return repr(node.value)
    if node.kind == "field":
        return _field_code(node.value)
    if node.kind == "neg":
        return f"-{_code(node.args[0], source, 6)}"
    if node.kind == "bin":
        level = _PRECEDENCE[node.value]
        text = f"{_code(node.args[0], source, level)} {_PYTHON[node.value]} {_code(node.args[1], source, level + 1)}"
        if node.value in {">", "<", ">=", "<=", "==", "!="}:
            text = f"({text}).astype(float)"
        return f"({text})" if level < parent else text
    return f"ops.{node.value}({', '.join(_code(arg, source) for arg in node.args)})"


def to_code(source, name="Factor"):
    """The formula as a factor written in Python: the same values, through the same operators, in code that can grow."""
    tree = parse(source)
    used = leaves(tree)
    inputs_text = ", ".join(f"{field[1:]!r}: {_field_code(field)}" for field in used if field not in CLASSIFICATION)
    return (f'"""{name}, written from the formula\n\n    {source.strip()}\n"""\n'
            "import talyx_quant as q\nfrom talyx_quant import ops\n\n"
            "data = q.data()\n\n"
            f"factor = {_code(tree, source)}\n"
            f"q.report.factor(factor, inputs={{{inputs_text}}})\n")


# ---------------------------------------------------------------------------------------------------------------- evaluation
RESAMPLE = {"1w": "W", "1m": "M"}


class _Memory:
    """Qlib's feature provider replaced by tables in memory: each table is registered under a name, one column per instrument."""

    def __init__(self):
        self.tables = {}

    def feature(self, instrument, field, start_index, end_index, freq):
        import pandas as pd
        array, where = self.tables[field.lstrip("$")]
        values = array[start_index:end_index + 1, where[instrument]]
        return pd.Series(values, index=pd.RangeIndex(start_index, start_index + len(values)))


_memory = None


def _qlib():
    """Qlib's operators, with the leaf features read from memory. Qlib's data folder and `qlib.init` are not needed for this."""
    global _memory
    import qlib.data.base as base
    import qlib.data.data as data
    from qlib.data import ops
    if _memory is None:
        _memory = _Memory()
        data.FeatureD.register(_memory)
    return ops, base.Feature


class Context:
    """What a formula is evaluated on: wide tables (bar labels × instruments) by field name, and the industry of each instrument."""

    def __init__(self, tables, industry=None, extra=None):
        import pandas as pd
        self.tables, self.industry, self.extra = tables, industry, extra or {}
        first = next(iter(tables.values()))
        self.index, self.columns = first.index, list(first.columns)
        self.positions = pd.RangeIndex(len(self.index))
        self.counter = 0
        self.registered, self.kept = {}, []

    def frame(self, values):
        import pandas as pd
        return pd.DataFrame(values, index=self.index, columns=self.columns)

    def table(self, name):
        if name not in self.tables:
            raise FormulaError("field", 0, 0, f"{name} is not in this run's data")
        return self.tables[name]


def _register(context, frame):
    """A table as a Qlib feature; the same table keeps the same name within one context, so Qlib's cache serves a subexpression
    computed before (the values table's inputs reuse the formula's own work)."""
    known = context.registered.get(id(frame))
    if known is not None:
        return known
    context.counter += 1
    name = f"__t{id(context)}_{context.counter}"
    _memory.tables[name] = (frame.to_numpy(dtype=float), {column: index for index, column in enumerate(frame.columns)})
    context.registered[id(frame)] = name
    context.kept.append(frame)
    return name


def _cross(name, args, context):
    """TALYX's operators across names, on wide tables."""
    import numpy as np
    import pandas as pd
    x = args[0]
    if name == "CSRank":
        return x.rank(axis=1, pct=True)
    if name == "CSZScore":
        return x.sub(x.mean(axis=1), axis=0).div(x.std(axis=1, ddof=1), axis=0)
    if name == "CSDemean":
        return x.sub(x.mean(axis=1), axis=0)
    industry = context.industry
    if industry is None or industry.reindex(x.columns).isna().all():
        raise FormulaError("field", 0, 0, "$industry is not in this run's data")
    codes = industry.reindex(x.columns)
    if name == "IndRank":
        out = pd.DataFrame(np.nan, index=x.index, columns=x.columns)
        for _, members in codes.groupby(codes):
            cols = list(members.index)
            out[cols] = x[cols].rank(axis=1, pct=True)
        return out
    # Neutralize(x, [$industry], [size]): the residual of x on industry dummies and log size, each day (Frisch–Waugh, exact).
    groups = codes if len(args) >= 2 and isinstance(args[1], pd.Series) else None
    size = next((arg for arg in args[1:] if isinstance(arg, pd.DataFrame)), None)
    return neutralize(x, groups, None if size is None else np.log(size.where(size > 0)))


def neutralize(values, industry=None, size=None):
    """Each day, the residual of `values` after industry dummies (`industry`: instrument → label) and a numeric exposure (`size`, same shape)."""
    import numpy as np
    import pandas as pd
    y = values.to_numpy(dtype=float)
    ok = ~np.isnan(y)
    x = None
    if size is not None:
        x = size.reindex_like(values).to_numpy(dtype=float)
        ok &= ~np.isnan(x)
    if industry is not None:
        labels = industry.reindex(values.columns)
        ok &= labels.notna().to_numpy()[None, :]
        codes, _ = pd.factorize(labels.fillna("__none__"))
        onehot = np.zeros((len(values.columns), codes.max() + 1))
        onehot[np.arange(len(codes)), codes] = 1.0
        count = ok.astype(float) @ onehot
        def demean(array):
            filled = np.where(ok, array, 0.0)
            means = (filled @ onehot) / np.maximum(count, 1)
            return np.where(ok, array - means[:, codes], 0.0)
    else:
        def demean(array):
            filled = np.where(ok, array, 0.0)
            means = filled.sum(axis=1) / np.maximum(ok.sum(axis=1), 1)
            return np.where(ok, array - means[:, None], 0.0)
    out = demean(y)
    if x is not None:
        xd = demean(x)
        with np.errstate(invalid="ignore", divide="ignore"):
            beta = (xd * out).sum(axis=1) / (xd * xd).sum(axis=1)
        out = out - np.nan_to_num(beta)[:, None] * xd
    out[~ok] = np.nan
    return pd.DataFrame(out, index=values.index, columns=values.columns)


def _resample(frame, period):
    """Each instrument's last value of each calendar period, from the period's last bar until the next period's last bar.

    Qlib's own TResample gives every bar of a period the period's value, which reads later days; this one only looks back."""
    import numpy as np
    import pandas as pd
    keys = pd.DatetimeIndex(frame.index).to_period(RESAMPLE[period][0])
    filled = frame.groupby(keys).ffill()
    order = pd.Series(keys)
    closed = (order != order.shift(-1)).to_numpy()
    return filled.where(np.broadcast_to(closed[:, None], frame.shape)).ffill()


class _Builder:
    """The tree as Qlib expression objects; a node across names (or another series) is computed first and enters as a feature in memory."""

    def __init__(self, context, ops, feature, source=None, known=None):
        self.context, self.ops, self.feature = context, ops, feature
        # Subexpressions already computed as wide tables (the values table's inputs), by their text in `source`: the formula takes them as they are.
        self.source, self.known = source, known or {}

    def leaf(self, frame):
        return self.feature(_register(self.context, frame))

    def table_of(self, node):
        """A whole subtree as a wide table."""
        built = self.build(node)
        return self.load(built)

    def load(self, built):
        import pandas as pd
        if isinstance(built, (int, float)):
            return self.context.frame(float(built))
        if isinstance(built, pd.DataFrame):
            return built
        columns = {}
        for column in self.context.columns:
            series = built.load(column, 0, len(self.context.index) - 1, "day")
            columns[column] = series.reindex(self.context.positions).to_numpy(dtype=float)
        return self.context.frame(pd.DataFrame(columns).to_numpy())

    def build(self, node):
        ops = self.ops
        if node.kind == "num":
            return node.value
        if self.known and self.source is not None and node.kind not in {"field", "str"}:
            table = self.known.get(_unwrap(node.text(self.source).strip()))
            if table is not None:
                return self.leaf(table)
        if node.kind == "field":
            if node.value in CLASSIFICATION:
                raise FormulaError("function", node.at, node.length, f"{node.value} names a group; use it in Neutralize or IndRank")
            return self.leaf(self.context.table(node.value))
        if node.kind == "neg":
            inner = self.build(node.args[0])
            return -inner if isinstance(inner, (int, float)) else ops.Mul(inner, -1)
        if node.kind == "bin":
            left, right = self.build(node.args[0]), self.build(node.args[1])
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                return float(eval(f"{left} {node.value} {right}")) if node.value in "+-*/" else float(eval(f"{left} {node.value} {right}"))  # noqa: S307
            if isinstance(left, (int, float)):
                left = self.leaf(self.context.frame(float(left)))
            operator = {"+": ops.Add, "-": ops.Sub, "*": ops.Mul, "/": ops.Div, ">": ops.Gt, "<": ops.Lt, ">=": ops.Ge, "<=": ops.Le,
                        "==": ops.Eq, "!=": ops.Ne, "&": ops.And, "|": ops.Or}[node.value]
            return operator(left, right)
        operator = OPERATORS[node.value]
        if operator.kind == "cs":
            import pandas as pd
            args = []
            for arg in node.args[:len(operator.args)]:
                if arg.kind == "field" and arg.value in CLASSIFICATION:
                    if self.context.industry is None:
                        raise FormulaError("field", arg.at, arg.length, "$industry is not in this run's data")
                    args.append(self.context.industry)
                else:
                    args.append(self.table_of(arg))
            if node.value == "Neutralize" and len(args) == 2 and isinstance(args[1], pd.DataFrame):
                args = [args[0], None, args[1]]
                return self.leaf(neutralize(args[0], None, _log_size(args[2])))
            return self.leaf(_cross(node.value, [a for a in args if a is not None], self.context))
        if operator.kind == "series":
            if node.value == "TResample":
                return self.leaf(_resample(self.table_of(node.args[0]), node.args[1].value))
            target = node.args[0].value
            table = self.table_of(node.args[1])
            if target not in table.columns:
                raise FormulaError("field", node.args[0].at, node.args[0].length, f"{target} is not in this run's data; add it to the symbols")
            return self.leaf(self.context.frame(table[target].to_numpy()[:, None].repeat(len(table.columns), axis=1)))
        values = []
        for spec, arg in zip(operator.args, node.args, strict=False):
            if spec in {"n", "q"}:
                number = _whole(arg, None)
                values.append(int(number) if spec == "n" else float(number))
            else:
                built = self.build(arg)
                values.append(self.leaf(self.context.frame(float(built))) if isinstance(built, (int, float)) else built)
        return getattr(ops, node.value)(*values)


def _log_size(frame):
    import numpy as np
    return np.log(frame.where(frame > 0))


def warmed(values, tree, source, context):
    """The values with each name's warm-up left out: the bars the formula reads before a bar (`_back`) counted from the name's own first
    bar with every field it reads. A 60-day window has a value only once it holds 60 days of that name; Qlib would give part-filled ones."""
    import numpy as np
    back = int(_back(tree, source))
    fields = [field for field in leaves(tree) if field in context.tables]
    if back <= 0 or not fields:
        return values
    present = np.logical_and.reduce([np.isfinite(context.tables[field].to_numpy(dtype=float)) for field in fields])
    rows = len(present)
    first = np.where(present.any(axis=0), present.argmax(axis=0), rows)
    early = np.arange(rows)[:, None] < (first + back)[None, :]
    return values.mask(early)


def evaluate(source, context, parts=(), *, ahead=False):
    """The formula's values as a wide table (bar labels × instruments), infinite values missing; with `parts` (texts of subexpressions)
    also each of those, computed in the same pass so their shared work is done once. Returns the table, or (table, {part: table})."""
    import numpy as np
    ops, feature = _qlib()
    tree = parse(source, ahead=ahead)
    try:
        # The parts first, each once; the formula then reads them as computed instead of working them out again inside itself.
        found, known = {}, {}
        for part in parts:
            table = context.tables.get(part)
            if table is None:
                inner = _Builder(context, ops, feature, part, dict(known))  # a part inside another is reused too
                table = known[part] = inner.load(inner.build(parse(part)))
            found[part] = table.replace([np.inf, -np.inf], np.nan)
            if not ahead:  # each part as it is on its own, its warm-up left out (the formula reads it whole and leaves out its own)
                found[part] = warmed(found[part], parse(part), part, context)
        builder = _Builder(context, ops, feature, source, known)
        result = builder.load(builder.build(tree)).replace([np.inf, -np.inf], np.nan)
        if not ahead:
            result = warmed(result, tree, source, context)
    finally:
        _forget(context)
    return (result, found) if parts else result


def table(source, context):
    """Every input the values table shows (the fields read and the outer operands) as wide tables, by label."""
    tree = parse(source)
    out = {}
    for text in inputs(tree, source):
        out[text] = context.table(text) if text in context.tables else evaluate(text, context)
    return out


def _forget(context):
    """Drop this evaluation's tables and Qlib's cached results, so a long-lived process does not grow."""
    from qlib.data.cache import H
    prefix = f"__t{id(context)}_"
    for name in [name for name in _memory.tables if name.startswith(prefix)]:
        del _memory.tables[name]
    context.registered.clear()
    context.kept.clear()
    H["f"].clear()


def apply(name, *args, industry=None):
    """One operator on wide tables (dates × instruments), for factors written in code (`talyx_quant.ops`): the formula's own implementation.
    `industry` (instrument → label) is what IndRank ranks within when no industry is passed."""
    import pandas as pd
    operator = OPERATORS.get(name)
    if operator is None:
        raise AttributeError(name)
    tables = [arg for arg in args if isinstance(arg, pd.DataFrame)]
    if not tables:
        raise TypeError(f"ops.{name} needs a table of dates by instruments")
    base = tables[0]
    given = next((arg for arg in args[1:] if isinstance(arg, pd.Series)), None)
    context = Context({"$x": base}, given if given is not None else industry)
    ops, feature = _qlib()
    builder = _Builder(context, ops, feature)
    try:
        if operator.kind == "cs":
            if name == "Neutralize":
                size = next((arg for arg in args[1:] if isinstance(arg, pd.DataFrame)), None)
                return neutralize(args[0], given, None if size is None else _log_size(size.reindex_like(args[0])))
            return _cross(name, [args[0]], context)
        if name == "TResample":
            return _resample(args[0], args[1])
        if name == "ChangeInstrument":
            target, frame = args
            if target not in frame.columns:
                raise KeyError(f"{target} is not in this table")
            return context.frame(frame[target].to_numpy()[:, None].repeat(len(frame.columns), axis=1))
        if len(args) < len(operator.args) - operator.optional or len(args) > len(operator.args):
            raise TypeError(f"ops.{operator.signature}")
        values = []
        for spec, arg in zip(operator.args, args, strict=False):
            if spec == "n":
                if isinstance(arg, bool) or not isinstance(arg, int) or arg < (1 if name in {"Ref", "Delta"} else 0):
                    raise ValueError(f"ops.{name} needs a whole number of days" + (" of 1 or more" if name in {"Ref", "Delta"} else ""))
                values.append(arg)
            elif spec == "q":
                values.append(float(arg))
            elif isinstance(arg, pd.DataFrame):
                values.append(builder.leaf(arg.reindex(index=base.index, columns=base.columns)))
            else:
                values.append(builder.leaf(context.frame(float(arg))))
        result = builder.load(getattr(ops, name)(*values)).replace([float("inf"), -float("inf")], math.nan)
        if name in _ROLLING_WINDOW:
            # As in the formula (`warmed`): a window has a value only once it holds its bars of that name, counted from the name's first
            # bar with every table it reads, so a formula written as code gives the same values.
            import numpy as np
            window = args[-1 if name != "Quantile" else 1]
            present = np.logical_and.reduce([np.isfinite(table.reindex(index=base.index, columns=base.columns).to_numpy(dtype=float)) for table in tables])
            rows = len(present)
            first = np.where(present.any(axis=0), present.argmax(axis=0), rows)
            result = result.mask(np.arange(rows)[:, None] < (first + int(window) - 1)[None, :])
        return result
    finally:
        _forget(context)
