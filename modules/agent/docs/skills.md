# Writing an Agent skill

A skill is a set of instructions (and any files they need) that TALYX's chats load when a request
matches it, or when the user starts a message with `/<name>`. Only its name and description are in
every request; the rest is read when it is used.

## Where it goes

A skill is a folder holding `SKILL.md`, in one of two places:

- this workspace: `/skills/workspace/<name>/SKILL.md` in the chat's file tools (the `skills`
  folder inside the workspace folder);
- every workspace: `/skills/global/<name>/SKILL.md`.

Write it with the file tools (`write_file`). The chats see a new or changed skill from the next
message on, and Agent's Skills settings list it.

## SKILL.md

```markdown
---
name: earnings-review
description: Review a company's latest quarterly results against the prior year and consensus. Use when the user asks how a quarter went.
---

# Earnings review

1. Read the last eight quarters with market.read_research {data: "financials", symbols, periodKinds: ["FQ"]}.
2. ...
```

- `name`: lowercase letters, digits and single hyphens, at most 64 characters, the same as the
  folder's name.
- `description`: what it does and when to use it, at most 1,024 characters; this is what the AI
  matches requests against.
- The body: the steps, in the imperative, naming the TALYX actions to call. Other files in the
  folder (a script, a template, reference notes) are read when the body points to them by their
  path in the folder.

To run a bundled Python script, give `run_python` its full `/skills/workspace/…` or `/skills/global/…` path alone. The script runs in the workspace folder and can read its own data with `Path(__file__).with_name("data.csv")`.

The chat's file tool checks the saved SKILL.md with the skill loader. If the check reports a
problem, edit the saved file and check the result again.

## Instructions and knowledge

- The workspace's standing instructions are `/workspace/AGENTS.md` (plain Markdown, every chat in
  the workspace reads it); change it only when the user asks.
- Knowledge folders are folders the user binds in Agent's settings; chats read them as
  `/knowledge/<id>/`, and the first paragraph of a folder's own `AGENTS.md` describes it.
