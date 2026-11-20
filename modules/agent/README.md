# Agent

Agent brings conversations, research files and module objects into one TalyxTerminal workspace. In **Chats** you can ask questions, approve actions, view results and undo a turn's changes. In **Bots** you can set up duties, group chats, schedules and wake-ups from alerts.

## How a conversation runs

Deep Agents 0.7.39 runs the model loop, to-dos, file tools, skills, compaction and sub-agents. A LangGraph SQLite checkpointer stores messages, pending writes and subgraphs. You can fork from an answer or rewrite from one of your own messages. A fork copies the conversation up to that checkpoint. When rewriting, you can choose to undo business changes made after that point; remembered content is managed separately.

Model settings can specify an output limit. The automatic limit is the smaller of the model profile's limit and a quarter of the context window; chat, summaries and sub-tasks share this rule. The smallest limit among the window, the profile and a test request caps the output tokens actually sent. Completed compaction summaries and history archives are saved as separate checkpoints, so if the main call fails, Retry continues the same turn.

Each user message is one work process. The collapsed row shows the whole turn's duration and number of failures; expanding it shows intermediate text, thinking and tool steps in time order. The conversation is read, written and subscribed to through private routes; the right column shows sub-agents, background tasks, module results and changes. Result cards read the Summary declared by public actions and open through each module's openTarget.

The model gets the instrument, source, timeframe and openable objects from shared views. When it needs data, it follows each module's public catalogue, descriptions and examples. Without a panel, it can query an instrument the user names directly. Citations in answers are tied to web sources or module objects seen in this conversation.

## Permissions, files and usage

Read-only, automatic and full permission apply to the actual side effects of each action. Plan mode forbids changes. When approval is needed, each call gets its own card, and approval resumes the original graph. Approvals the user started directly stay valid; background approvals that time out are recorded as not done. Python runs as the current user; the interface explains its scope on hover.

Long-lived instructions come from global and workspace `AGENTS.md` files; memory is stored separately and injected into the system prompt. Skills and knowledge folders are loaded from the actual files; for skills with the same name, the workspace version wins. Workspace files, bound knowledge folders, selected attachments and temporary computation for this run are each accessed by path. File and module changes can be undone and redone per turn; undo covers later changes to the same object made after that turn. Bound folders belong to the user and are kept when a workspace is deleted.

Usage is recorded per model, conversation run and sub-agent. Cost uses the request's actual input, cached and output tokens and the model's prices. Once the monthly limit is reached, new paid requests are blocked. Background tasks continue under their original call id, and their results can be read once the same turn has received a final state.

A bot's single runs and its monthly cost by wake-up reason show each billing currency separately. Budget progress is computed in the budget's currency.

## Public actions

An external AI in the bound workspace uses `agent.chat` to manage, search and export conversations, read sub-tasks and permission records, and undo or redo by conversation and turn id.
`agent.bot` manages bots, groups, settings and schedules, and can read threads, send messages, test schedules and handle runs.
`agent.configure` reads and updates model connections, non-secret settings, instructions, the list of knowledge folders, memory and budgets. API keys are entered by the user on the Settings page.
`agent.search_web` searches current sources or reads an exact HTTP(S) page through the configured search source, recording its actual usage; errors never switch sources.

Each action's inputs and side effects are in the generated [public/contract.json](public/contract.json). Agent also defines the extension point `agent.open_file`, which opens a selected workspace file or attachment for the user.

## Bots and schedules

A bot has its own duty, model, permissions, budget and run history. Ordinary conversations can also carry schedules, listed under **Scheduled**. Module events come from publicly declared trigger sources and are read from a cursor. When busy, alerts of the same kind are merged; several missed schedules run only once, recording the earliest missed scheduled time. When nobody in a group has spoken for 200 consecutive turns, bots stop waking each other. Copying a workspace copies conversations and checkpoints; bots and schedules in the copy start paused.

## Code and tests

- The backend entry is `backend/entry.py`. The engine, approvals and checkpoints are in `engine.py`, `reviews.py` and `checkpoints.py`. Cross-module operations go through the platform catalogue and public actions; running Python uses the current run token.
- Chat, Bots and Settings are in `frontend/chat/`, `frontend/bot/` and `frontend/settings/`. Panels use real workspace data.
- Run the checks from the repository root as described in [docs/development.md](../../docs/development.md). Regression tests for conversations, permissions, workspace copying, background continuation and usage are in `tests/unit/test_agent_*`; tool declarations are checked by `tests/governance/`.

For how modules connect through public contracts, see [docs/architecture.md](../../docs/architecture.md).
