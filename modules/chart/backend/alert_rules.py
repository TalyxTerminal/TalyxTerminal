"""An alert's conditions over one series, as TradingView judges them.

``Rule.observe`` takes one observation of every operand at a bar time: on the forming bar
(each tick Market sends, newest only) or when the bar closed. It says whether the alert
fires. Crossing compares with the previous observation, so the first one only sets the
baseline; ``once_per_bar_close`` looks at closed bars only, the other frequencies at every
observation. Only observations Chart counts as real time may fire (``live``); the others
only build the baseline, and a new window rebuilds it silently (``rebase``).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

CHANNEL_OPS = ("entering_channel", "exiting_channel", "inside_channel", "outside_channel")
MOVING_OPS = ("moving_up", "moving_down", "moving_up_percent", "moving_down_percent")
MINUTE_MS = 60_000


@dataclass
class Values:
    """One condition's operands at an observation (None where a value is absent, Pine na)."""
    left: float | None
    right: float | None = None
    right2: float | None = None


def inside(values: Values) -> bool | None:
    if values.left is None or values.right is None or values.right2 is None:
        return None
    low, high = sorted((values.right, values.right2))
    return low < values.left < high


def holds(op: str, now: Values, before: Values | None, past: float | None, amount: float | None) -> bool:
    """One condition at this observation; ``before`` is the previous observation, ``past`` the
    left value ``bars`` closed bars ago (moving ops)."""
    if op in MOVING_OPS:
        if now.left is None or past is None:
            return False
        if op == "moving_up":
            return now.left - past >= amount
        if op == "moving_down":
            return past - now.left >= amount
        if past == 0:
            return False
        change = (now.left - past) / abs(past) * 100
        return change >= amount if op == "moving_up_percent" else -change >= amount
    if op == "triggered":
        return bool(now.left)
    if op in CHANNEL_OPS:
        state = inside(now)
        if state is None:
            return False
        if op == "inside_channel":
            return state
        if op == "outside_channel":
            return not state
        earlier = inside(before) if before is not None else None
        if earlier is None:
            return False
        return (state and not earlier) if op == "entering_channel" else (earlier and not state)
    if now.left is None or now.right is None:
        return False
    if op == "greater":
        return now.left > now.right
    if op == "less":
        return now.left < now.right
    if before is None or before.left is None or before.right is None:
        return False  # crossing needs a baseline
    up = before.left <= before.right and now.left > now.right
    down = before.left >= before.right and now.left < now.right
    return up if op == "crossing_up" else down if op == "crossing_down" else up or down


@dataclass
class Rule:
    conditions: list[dict]          # Condition: op, amount, bars
    frequency: str
    fired_bar: int | None = None    # the bar it last fired on (kept across restarts)
    fired_at: int | None = None     # when it last fired (once per minute)
    before: list[Values] | None = None
    closed_before: list[Values] | None = None
    history: list[deque] = field(default_factory=list)  # closed left values per moving condition

    def __post_init__(self):
        self.history = [deque(maxlen=(item.get("bars") or 1) + 1) for item in self.conditions]

    def rebase(self, closed_lefts: list[list[float | None]], last_closed: list[Values] | None = None) -> None:
        """A new window: the first observation sets the baseline again, except on bar close,
        where the window's last closed bar is the baseline; moving conditions get their last
        closed values."""
        self.before, self.closed_before = None, last_closed
        for queue, lefts in zip(self.history, closed_lefts, strict=True):
            queue.clear()
            queue.extend(lefts[-queue.maxlen:])

    def observe(self, time: int, closed: bool, values: list[Values], live: bool, now_ms: int) -> bool:
        """Whether the alert fires at this observation (bar ``time``)."""
        if self.frequency == "once_per_bar_close":
            if not closed:
                return False
            before, self.closed_before = self.closed_before, values
        else:
            before, self.before = self.before, values
        pasts = []
        for index, (condition, queue) in enumerate(zip(self.conditions, self.history, strict=True)):
            bars = condition.get("bars") or 1
            if closed:
                queue.append(values[index].left)
                pasts.append(queue[-1 - bars] if len(queue) > bars else None)
            else:
                pasts.append(queue[-bars] if len(queue) >= bars else None)
        met = all(holds(condition["op"], values[index], before[index] if before else None, pasts[index],
                        condition.get("amount"))
                  for index, condition in enumerate(self.conditions))
        if not met or not live:
            return False
        if self.frequency in ("once_per_bar", "once_per_bar_close") and self.fired_bar == time:
            return False
        if self.frequency == "once_per_minute" and self.fired_at is not None and now_ms - self.fired_at < MINUTE_MS:
            return False
        self.fired_bar, self.fired_at = time, now_ms
        return True
