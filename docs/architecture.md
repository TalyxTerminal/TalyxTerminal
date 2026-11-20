# Architecture

This page explains how TalyxTerminal is put together: how the platform and the modules divide the work, how modules cooperate, and what the platform guarantees. Field-level details live in the declarations: each module's generated `public/contract.json`, the [manifest schema](../contracts/manifest_v5.schema.json) and the [platform contract](../host/public/contract.json).

## Principles

**A thin platform, strong modules.** The platform provides only general machinery: the registry, routing, the bridge to the interface, process supervision, workspaces and settings, and shared services for storage, secrets, notifications and tasks. Every piece of business logic belongs to a module.

**Modules cooperate only through what they publish.** Official modules have no back doors into each other, so whatever an official module can do with another, a third-party module can do too. Modularity means two things:

- **Working together.** Modules do their work inside one platform and one workflow. Putting several tools side by side is no different from opening several programs; the value is in modules working across each other.
- **Extending.** Anyone can write a module, financial or not, and it fits into the same workflow.

**Cross-cutting parts work from declarations.** The parts that serve every module (the Agent, the settings window, notifications, the platform itself) act only on what modules declare. They never name a module in code.

The frontend owns the interface, panels and local state; the backend owns data, computation, background work and long-held resources.

## Processes

- The platform runs as one process (the Host), and every module runs in its own Python process. Official and third-party modules run exactly the same way.
- Each module process sits in a Windows Job Object: when the module exits, everything it started exits with it. Child processes a module starts go through the SDK, each in its own Job, so stopping a run stops its whole process tree.
- A module that hangs or crashes affects only itself. The platform restarts it; after three quick exits in a row it is marked as failed to start, with the reason. Calls still waiting on it receive `provider_restarted` (outcome unknown, side effects possible); the platform reports this as it is and never retries. Streams it provided end with the same error, subscribers resubscribe from their cursor automatically, and panels reconnect.
- Processes start in parallel. Calls to a module that is not ready yet wait in a queue (up to 30 seconds and 1,000 calls), then are delivered or fail with `unavailable`.
- Changing a module's code reloads only that module: its environment is prepared from the manifest, its contract is regenerated with its own Python, and its process restarts. Other modules' calls and subscriptions are not interrupted.
- Every process keeps its event loop free and moves heavy work to threads or subprocesses. Performance targets once a workspace is open: event-loop lag p99 ≤ 20 ms and maximum ≤ 200 ms; opening a chart or changing its timeframe ≤ 0.5 s. `scripts/perf.py` measures these (see [development](development.md)).

## Layers

Upper layers may depend on lower ones, never the reverse.

| Layer | Contents | Location |
|---|---|---|
| 4 Application shell | Home window, workspace frame, top bar, settings window, desktop entry. Assembly only, no business flows | `web/src/shell/`, `host/desktop/` |
| 3 Modules | Official and third-party modules, in one format and one runtime | `modules/<id>/`, `user/modules/` |
| 2 Platform | Routing, Host services and module supervision; the frontend registry, bridge and runtime; the SDK for modules | `kernel/`, `host/`, `web/src/kernel/`, `web/src/runtime/`, `sdk/` |
| 1 Vocabulary | Shared data shapes: instruments, series, timeframes, bars, file references; the manifest schema. Shapes only, no fetching, computing or drawing | `vocabulary/`, `contracts/` |

Everything on screen outside the panels belongs to the shell; everything inside a panel belongs to its module. A module that wants to appear outside its panels does so through the platform's contribution points, such as a page in the settings window or a status item.

## Modules

```
modules/<id>/
  talyx.module.json   small hand-written manifest: identity, entry, dependencies, extra Python
                      packages, panels and views, contributions to other modules (provides)
  public/             generated from code and committed: contract.json, a TypeScript client and a
                      Python client for calls between modules; never edited by hand
  frontend/ backend/  private implementation, invisible to other modules
```

- **Code first.** Authors declare actions, streams, events, extension points and settings in Python with the decorators and pydantic types of `talyx_sdk.declare`. A function's docstring is the description people and AI read. `scripts/contracts/generate.py` generates `public/` from the code, and the checks fail if the generated files were edited. The platform reads the contract before running any code; a handler that does not match the contract fails activation.
- **Dependencies** come from each manifest (today Chart requires Market and Developer; Quant requires Market and optionally Developer). A module cannot be enabled while a required dependency is disabled, a module others require cannot be disabled, and cycles are not allowed. When an optional dependency is missing, only the part that uses it stops working, and the module says so.
- **Extenders depend on the owner of an extension point**, never the other way round. Developer knows nothing about indicators, for example; Chart provides the authoring support for them.
- **Python environments.** Modules use the bundled environment by default. A module that declares extra packages gets its own environment, built with uv. If it cannot be built, the module reports what is missing and does not start; there is no fallback to another environment.
- **Third-party modules** install from an archive or a folder and can be enabled, disabled, uninstalled and reloaded in the running app; modules written in Developer are installed in place. They get exactly the platform services the official modules get. A third-party module is code the user has chosen to enable and trusts fully: its separate process isolates crashes and makes cleanup reliable, but it is **not a security sandbox**.

## Dependency rules

1. Layers point one way: the vocabulary depends on nothing, the platform depends on no module, and modules do not depend on the shell.
2. A module may use only another module's `public/`, and only if that module is listed in its manifest's `requires`.
3. Everything public is declared once, in code; the contract and the clients on both ends are generated from it.
4. A module's frontend and its own backend talk over private routes (FastAPI, mounted at `/m/<id>/`, WebSockets included). The Host checks the interface session and forwards them unchanged; they are not part of the contract or the AI catalog.
5. Platform code (kernel, host, sdk and the platform part of web) contains no module names, exchange names or business vocabulary. Callers pass business information as generic parameters.

`scripts/checks/`, `tests/governance/` and the contract generation check enforce these rules.

## How modules cooperate

There are three channels:

- **Data**: read or subscribe to what another module publishes.
- **Capability**: call another module's public actions.
- **Interface**: contribute content to a place another module offers, or embed a view it publishes.

Openness guarantees three things, not replaceability:

- **Use.** A module names the public parts of another and uses them. Because official modules also go only through public contracts, anything official is available to third parties. If the named module is swapped for another, the part that named it stops working.
- **Add.** Owners open extension points and others plug into them, such as Market's data sources or Chart's indicators.
- **Cross-cutting by declaration.** The Agent, the settings window, notifications and the platform act only on declarations.

Things live in the module where they naturally belong, private by default, until the owner publishes them. There is no shared object store and no global, writable "current instrument".

| Kind | When to use it |
|---|---|
| Action | Anything you ask a module to do, called by people, AI, other modules and running code. Every action carries side-effect labels. It may be a long task: it returns a task id at once, and the platform tracks its status, progress and cancellation |
| Stream | A parameterised subscription, optionally resumable from a cursor. A stream may be marked as a trigger source, which consumers such as the Agent's schedules collect from all modules without naming any |
| Event | The owner announces something that has happened. Not stored, not replayed |
| Extension point | The owner defines an interface, others implement it, the owner calls it. For example `market.data_source` |
| Setting | Each has a type, a default, and whether it is global only; the platform stores it, resolves its two levels and draws it in the settings window |
| Panel, view (in the manifest) | A module's own panels, and views that can be embedded elsewhere. Embedding goes through the registry, never by importing another module's code |
| Contribution point (`provides` in the manifest) | A place an owner offers, which contributors fill by static declaration |

Every call goes through the Host: the interface over the bridge, external AI over MCP, modules over their own pipes, running code with a run token. Each frame is a small envelope plus body bytes; the Host reads only the envelope and forwards the body as it is. The receiving module's SDK parses input against the declaration and answers `invalid_input` when it does not match; the sending side's SDK checks output, rejecting wrong types, NaN or infinity, and integers beyond JavaScript's safe range as `invalid_output`. A frame is at most 16 MB; larger results are written by the provider as a file in its own directory and returned as a file reference (written once, uniquely named, with a sha256).

## What the platform always guarantees

- **Declarations before code.** What a module publishes is known without running it. A handler without a declaration, or a declaration without a handler, fails activation.
- **Identity is stamped once.** The Host stamps each call's origin at the entry: `module`, `workspaceId`, `origin` (user, ai, module or code) and, for AI, which client and conversation. Nobody downstream can change it.
- **One error format.** Every error states whether it executed (`execution`) and whether it may have had side effects (`effects`). Writes with an uncertain outcome are never retried automatically, and the interface never shows "not confirmed" as "not done".
- **Closing a workspace really stops it.** Modules register background work (asyncio tasks and process trees) under the workspace through the SDK. Before closing, the user sees what will stop; after confirming, the SDK cancels the tasks, ends the process trees and reports back. A module that cannot finish within 5 seconds is restarted. Closing completes only when everything has stopped.
- **The window reloads when the Host restarts.** Unsaved changes are kept in the page and restored as unsaved after the reload.
- **Panels open by target.** A module can register "panel type plus object id" as an open target. The platform finds the open panel showing that object, or opens a new one; the caller needs no dependency on the target module.
- **Frontend resources are scoped.** Handlers, subscriptions and views hang on a scope and are released together when a panel closes or a module is disabled.
- **Module health is shown by the platform.** Modules that fail or cannot start are listed under Installed in Community and flagged in the top bar; a panel that fails shows why.
- **The platform draws the settings window** from module declarations. Each setting shows whether it follows the global value or has its own value in this workspace, with "reset to global". Things that manage objects rather than single values (Market's sources and keys, the Agent's model connections) are pages a module contributes through `platform.settings_section`.
- **Exchange requests are queued, and rate limits are respected across processes.** The SDK provides generic queuing per quota domain and slows down as the exchange reports usage; each module that talks to exchanges supplies the quota tables. After a 429/418 or an explicit rate-limit response, the cool-down is shared by all processes and survives restarts. During it, requests fail at once; afterwards only new calls are accepted, old ones are not replayed.
- **Secrets are only available through the platform's secret service**, isolated per module. The frontend sees only whether a secret is configured, masked; secret values never appear in logs or errors. On Windows, keys are stored in the Credential Manager.
- **Disabling a module** is refused while another enabled module requires it. Before disabling, the module's frontend saves pending work; afterwards its panels stay in place marked as disabled, embedded views become placeholders, and its contributions are withdrawn.

## Workspaces

The platform owns a workspace's identity, layout and panel states. Modules store what belongs to a workspace themselves, keyed by workspace id; the platform does not interpret watchlists, alerts or other business data. A workspace is the lifetime boundary for background work: switching workspaces, hiding the window to the tray, reloading and disconnecting do not close it.

- **Settings have two levels.** One global set; each workspace records only the values that differ, and everything else follows the global value at once. Settings tied to the machine or to shared data are global only (start at login, data sources and their keys, notification channel accounts), as their owners declare.
- **Copying a workspace** asks every module to copy what belongs to it (Chart's charts and watchlists, the Agent's conversations). If a module with panels in the saved layout is not running, the copy is refused with the module's name, rather than producing an incomplete copy.
- **"Unsaved" means the user changed something.** State a module derives by itself (refreshed lists, re-bound references, browsing position such as the selected run or tab) is saved with the next save but never marks the workspace unsaved. Only changes to what the user made count: layout, panels, charts, settings, code. Leaving asks only when there really are unsaved changes.
- Each workspace has its own folder; only the user can bind it to an existing directory. One Host runs per data root. The tray, start at login and keep-awake belong to the desktop host; when the window is minimised or in the tray, the desktop host hides the page too, so its animations and timers stop.

Python environments live in a shared directory managed by the SDK's uv tools, with a reference for every holder; an environment with holders is never cleaned up. The owner of a run freezes the code, dependencies and materials it uses and holds the environment reference, so later edits affect only new runs.

## AI

AI is connected by declaration, like every cross-cutting part.

- **AI and people use the same actions.** What the AI can use is every public action, including the platform's own (opening and focusing panels, reading the workspace and each panel's current view, reading and writing settings). There is no separate AI list.
- **Whether to ask the user is the AI side's decision.** The platform publishes the catalog and records calls; it does not block actions. The built-in Agent follows the permission level the user picks; external AI clients use their own approvals.
- **Side-effect labels are honest.** Every action declares its side effects: `read`, `write`, `delete`, running code (`code`), sending outside (`external`), moving money (`funds`). An action that moves money indirectly is labelled `funds` too.
- **One catalog for internal and external AI.** The tool catalog is generated from declarations. The Agent calls through the platform's `ai` service; external AI clients connect over the Host's MCP endpoint, bound to a workspace, with side-effect labels translated into MCP tool annotations.
- **Modules declare their own guides.** `ai.guide` in `talyx.module.json` names a short guide and `ai.docs` registers topic documents inside the module. The Agent lists the topics after the guide; external clients get the same list from `platform.ai.catalog`, and both read pages with `platform.ai.guide {module, topic}`.
- **Call records are separate from business state.** The platform records calls made by AI and by running code, and module-to-module calls that change or delete things: start, end, origin and a result summary, kept for 30 days.
- **AI reads are bounded.** Every answer to an AI is at most 20,000 characters of compact JSON, images aside. Larger answers fail rather than being truncated; owners page long results and use file references for large content.
- **Running code can call TalyxTerminal.** A module starting a subprocess asks the platform for a run token, passed in the environment. The subprocess calls through the Host's local endpoint with the SDK's small client; its calls are recorded with origin `code`, the module and the workspace.
- **Modules report the current view** with `ctx.view.report`: a short context and the current object. The shell adds panel title, focus, visibility and time.

## Deliberately not built

- A shared object store, or a generic mechanism for several modules to edit the same object.
- FDC3-style intents with user-chosen handlers.
- Link groups that follow an instrument by colour.
- Cross-module transactions, automatic rollback, exactly-once delivery.
- A security sandbox and permission system for third-party modules.
- Compatibility layers for old manifest formats, module names or stored data.
