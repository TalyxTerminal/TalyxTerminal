"""One thinking choice, {"effort": level}, derived from model info and sent as each provider's native parameters."""

from __future__ import annotations

import re

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

# Token budgets for models that only take a thinking budget, clamped into the model's own range.
BUDGETS = {"low": 1024, "medium": 8192, "high": 24576}
# Providers whose official API switches thinking with {"thinking": {"type": ...}} and grades it with reasoning_effort.
SWITCHED = {"deepseek", "moonshotai", "zhipuai"}


def anthropic_generation(name):
    """(major, minor) of a Claude model id; dated suffixes are not minor versions."""
    match = re.match(r"claude-(?:opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d{1,2})(?!\d))?", name)
    if match is None:
        legacy = re.match(r"claude-(\d+)", name)
        return (int(legacy.group(1)), 0) if legacy else (5, 0)
    return int(match.group(1)), int(match.group(2) or 0)


def anthropic_rules(name):
    """Whether thinking can be turned off and the level Anthropic uses when no thinking parameter is sent."""
    if anthropic_generation(name) < (5, 0):
        return True, "off"  # 4.x thinking is opt-in.
    for prefix, off, default in (("claude-opus-5-5", False, "medium"), ("claude-fable-5", False, "high"),
                                 ("claude-mythos-5", False, "high"), ("claude-haiku-5-5", True, "medium"),
                                 ("claude-sonnet-5-5", True, "high"), ("claude-sonnet-5", True, "high"),
                                 ("claude-opus-5", True, "high")):
        if name.startswith(prefix):
            return off, default
    return None, "high"


def provider_default(provider, name, levels):
    """The level each provider uses when nothing is sent, from its documentation (2026-10)."""
    if provider == "openai":
        if re.fullmatch(r"gpt-5\.[124](-mini|-nano)?(-\d{4}-\d{2}-\d{2})?", name):
            return "off"
        return "medium" if "medium" in levels else levels[-1]
    if provider == "anthropic":
        return anthropic_rules(name)[1]
    if provider == "google":
        if "flash-lite" in name and name.startswith("gemini-2.5") or levels == ["off", "on"]:
            return "off"
        return "high" if "high" in levels else levels[-1]
    if provider == "xai":
        return "low" if name.startswith("grok-4.3") else "high"
    if provider == "ollama":
        return "medium" if "medium" in levels else "on"
    if provider in {"moonshotai", "moonshotai-cn", "zhipuai", "zai"} and "max" in levels:
        return "max"
    if provider == "deepseek":
        return "high"
    for level in ("on", "medium", "high"):
        if level in levels:
            return level
    return levels[-1]


def plan(profile, *, protocol, provider, name, discovered):
    """Levels, default and the facts needed to send a level natively; levels are empty for models that can't think."""
    none = {"levels": [], "default": None, "toggle": False, "effort": False, "budgets": None}
    if protocol == "ollama":
        if discovered is not True:
            return none
        levels = ["low", "medium", "high"] if name.startswith("gpt-oss") else ["off", "on"]
        return {**none, "levels": levels, "default": provider_default("ollama", name, levels)}
    if discovered is False or not ("reasoning_output" in profile and profile["reasoning_output"]):
        return none
    if "reasoning_options" in profile:
        options = profile["reasoning_options"]
    elif "reasoning_effort_levels" in profile:
        options = [{"type": "effort", "values": profile["reasoning_effort_levels"]}]
    else:
        options = []
    toggle = any(option["type"] == "toggle" for option in options)
    effort = next((option["values"] for option in options if option["type"] == "effort"), [])
    budget = next((option for option in options if option["type"] == "budget_tokens"), None)
    levels = [value for value in effort if value != "none"]
    budgets = None
    if budget is not None and (not levels or protocol == "anthropic" and anthropic_generation(name) < (4, 6)):
        low, high = budget["min"] if "min" in budget else 0, budget["max"] if "max" in budget else None
        budgets = {level: max(low, tokens if high is None else min(high, tokens)) for level, tokens in BUDGETS.items()}
        levels = levels or list(budgets)
    off = toggle or "none" in effort or budget is not None and "min" in budget and budget["min"] == 0
    if protocol == "anthropic":
        allowed, _ = anthropic_rules(name)
        off = allowed if allowed is not None else toggle
    if not levels and toggle:
        levels = ["on"]
    if not levels:
        return none
    levels = ["off", *levels] if off else levels
    return {"levels": levels, "default": provider_default(provider, name, levels), "toggle": toggle,
            "effort": bool(effort), "budgets": budgets}


def schema(thinking):
    properties = {"effort": {"enum": thinking["levels"]}} if thinking["levels"] else {}
    return {"type": "object", "properties": properties, "additionalProperties": False}


def level(thinking, choice):
    """The chosen level, or None for the provider default; any other shape is rejected."""
    if choice is None:
        return None
    if not isinstance(choice, dict) or set(choice) != {"effort"} or choice["effort"] not in thinking["levels"]:
        raise ContractError("invalid_input", t("Choose one of this model's thinking levels"), {"levels": thinking["levels"]})
    return choice["effort"]


def native(connection, resolved, thinking, chosen):
    """Provider request parameters for a level, and extra output tokens a thinking budget needs."""
    if chosen is None:
        return {}, 0
    name = resolved["profileModel"]
    off = chosen == "off"
    if connection.protocol == "ollama":
        return {"reasoning": not off if chosen in {"off", "on"} else chosen}, 0
    if connection.protocol == "anthropic":
        generation = anthropic_generation(name)
        if off:
            if generation < (5, 0):
                return {}, 0
            return {"thinking": {"type": "between_tools" if name.startswith("claude-sonnet-5-5") else "disabled"}}, 0
        if thinking["budgets"] is not None:
            # Budgeted extended thinking (before 4.6); Opus 4.5 also takes effort beside its budget.
            budget = thinking["budgets"][chosen]
            effort = {"effort": chosen} if thinking["effort"] else {}
            return {"thinking": {"type": "enabled", "budget_tokens": budget}, **effort}, budget
        display = {"display": "summarized"} if generation >= (4, 7) else {}
        return {"thinking": {"type": "adaptive", **display}, "effort": chosen}, 0
    if connection.protocol == "google":
        if thinking["budgets"] is not None or thinking["levels"] == ["off", "on"]:
            budget = 0 if off else -1 if chosen == "on" else thinking["budgets"][chosen]
            return {"thinking_budget": budget, "include_thoughts": not off}, 0
        return {"thinking_level": chosen, "include_thoughts": True}, 0
    if connection.kind == "official" and connection.providerId in SWITCHED:
        if off:
            return {"extra_body": {"thinking": {"type": "disabled"}}}, 0
        switch = {"extra_body": {"thinking": {"type": "enabled"}}} if thinking["toggle"] else {}
        return {**switch, **({"reasoning_effort": chosen} if chosen != "on" else {})}, 0
    # OpenAI, xAI, LM Studio and relays: the protocol's own reasoning_effort ("none" turns it off).
    if chosen == "on":
        return {}, 0
    return {"reasoning_effort": "none" if off else chosen}, 0
