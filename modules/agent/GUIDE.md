---
name: Agent
description: TALYX's own AI. Web search, and the files, skills, instructions and knowledge folders its chats use.
---

- Manage chats with agent.chat {operation:{op:"list"}}. Use read for cards and turns, fork at an itemId, export to workspace Markdown, and revert with conversationId, turnId and direction ("undo" or "redo"). child_read and child_stop take conversationId and childRunId.
- Use agent.bot {operation:{op:"list"}} for groups, bots, settings and run costs by currency. inspect takes an id for details, read takes a thread id. Create, patch, send and manage routines through the same action.
- Use agent.configure {operation:{op:"read"}} for models, connections and defaults. Use page or read_instructions for a settings scope, folders for knowledge bindings, memory_list for revisions, read_usage for costs. Credentials are entered by the user. An oversized result returns a file reference. Read that JSON snapshot at file.path. It is retained for at least 24 hours.
- Search the web or read one page: agent.search_web {mode:"search", query} or {mode:"page", url}.
- In TALYX's own chat the file tools see /workspace/ (the workspace folder), /knowledge/<id>/ (folders the user bound as knowledge) and the skills: /skills/workspace/ (this workspace's) and /skills/global/ (every workspace's). For a physical workspace path use platform.workspace.folder {workspaceId}.
- For a pasted or attached rectangular CSV/TSV, the model receives the exact record count and first-column endpoint counts. For other groupings use run_python with the supplied stdin_source to read the original input via sys.stdin. Supplying code alone runs a one-off calculation without saving a script.
- The workspace's instructions are /workspace/AGENTS.md; change them only when the user asks.
- A new skill: read the skills doc first.
- Show a file to the user: the Developer guide says how.
