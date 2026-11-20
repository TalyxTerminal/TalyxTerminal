# Integrating with Developer

This guide is for module authors who want to show their own documents in Developer, give Python files editor language support, embed Developer's editors, or work with shared documents from a backend. Product behaviour is described in [Developer](../README.md). Fields and versions are defined by [contract.json](contract.json) and the [generated TypeScript types](ts/types.tsx); backends can use the [generated Python client](python/client.py).

Developer offers three things to other modules:

- **Extension points** a module implements: `developer.document_source` (documents the module serves itself) and `developer.development_support` (documentation listed in Help, and the `language` capability for Python files).
- **Views** a module embeds: `developer.editor`, `developer.project_editor` and `developer.text_editor`.
- **Public actions** any module, the Agent or a script calls: `documents.*`, `projects.*` and `help`, plus the events `developer.document_changed` and `developer.projects_changed`.

## 1. Declare the dependency and the extensions

Declare Developer in `requires` (optional or required), and list what you provide under `provides` in your manifest. This is how Chart declares its indicator documentation:

```json
{
  "requires": {"market": {}, "developer": {}},
  "provides": {
    "developer.document_source": [{
      "id": "indicator-docs",
      "descriptor": {"name": "Chart indicator documentation", "readOnly": true, "watch": false}
    }],
    "developer.development_support": [{
      "id": "indicators",
      "descriptor": {
        "name": "Chart indicators",
        "capabilities": [],
        "appliesTo": {},
        "documentation": [
          {"name": "Writing Chart indicators",
           "source": {"kind": "contribution", "providerId": "chart:indicator-docs", "sourceKey": "guide"}}
        ]
      }
    }]
  }
}
```

The backend registers the implementations on its `Module` declaration:

```python
module.implement("developer.document_source", "indicator-docs", actions={"read": read, "write": write})
module.implement("developer.development_support", "indicators", actions={"invoke": invoke})
```

An extension's external identity is `<module id>:<extension id>` (for example `chart:indicator-docs`). A module that only edits ordinary files on disk needs no document source; a module that only uses the document services needs no extension at all.

Generated clients come from `scripts/contracts/generate.py` (see [scripts/contracts/README.md](../../../scripts/contracts/README.md)). Frontends import Developer's client from its generated types (`createClient` in `modules/developer/public/ts/types.tsx`); do not copy return types or write a client by hand.

## 2. Document sources

A document source serves documents that are not plain files, opened in Developer as `{kind: "contribution", providerId: "<module>:<id>", sourceKey, target?}`. The descriptor has `name`, `readOnly` (default false) and `watch` (default false).

- **`read`** receives `{sourceKey, target}` and returns `sourceKey`, `name`, `canonicalUri` (or null), `content`, the source's own `version`, `language`, `readOnly`, and `context`: `contextId`, `projectId` (or null), `rootUri` (or null) and `languageMaterials`.
- **`write`** receives `{sourceKey, target, content, expectedVersion}` and returns `{status, version, message}`, where `status` is `saved` or `conflict` and `message` may be null. A read-only source can refuse writes (Chart answers `permission_denied`).
- Developer does not poll contributed sources. Every `documents.open` reads the source again: a clean buffer follows the new version, and a buffer with unsaved input is marked as a conflict.
- `target` is a JSON identity defined by the owning module. Developer stores it and passes it back unchanged.
- The document `version` is the shared buffer's version; the source `version` is the owner's external version. They are checked separately when saving.

## 3. Development supports

The descriptor has `name`, `capabilities` (currently only `language`), `appliesTo` and `documentation`.

- **`documentation`** is a list of `{name, source}` entries, where `source` is a file source or a contribution source. They appear in Developer's Help.
- **`appliesTo`** selects the documents a support is for. Every listed criterion must match; empty lists match everything:
  - `sourceKinds`: `file`, `contribution`;
  - `filePatterns`: glob patterns on the file name (case-insensitive);
  - `providerIds`: contribution providers;
  - `projectKinds`: `local`, `reference`.
- **`invoke`** receives `{capability, command, target, document, project?}`. A support that lists `language` answers `capability: "language", command: "environment"` with:
  - `environment.python.interpreterPath` (and `environment.python.environmentPath` for a virtual environment): the Python the file is analysed against;
  - `languageMaterials`, a list of:
    - `{kind: "python-path", path}`: a folder imports resolve against, in addition to the environment's own packages (a `name.pyi` there is the type information for `name`);
    - `{kind: "python-path-scope", scope: "declared"}`: the listed folders are all the support adds; no folder is added automatically;
    - `{kind: "python-analysis", typeCheckingMode}`: one of `off`, `basic`, `standard` (default), `strict`, `recommended`, `all`.

    Other entries are ignored.

  Without an interpreter path, the file has no language features. When several supports apply, the first one that answers an interpreter is used. Files using the same interpreter share one basedpyright process, which is replaced after packages are installed or removed. A support that does not provide a capability should raise an error for it (Chart and Quant answer `unknown_capability`).

Quant is a working example: its `source_language` support answers the research or strategy environment and the `talyx_quant` path for Quant object folders and the common code folder.

## 4. Embedding editors

All three views are mounted with the SDK's `ModuleView`:

```tsx
import { ModuleView } from "@talyx/sdk";
import { createClient } from "../../developer/public/ts/types";

const developer = createClient(ctx);
const opened = await developer["documents.open"]({ source: { kind: "file", uri } });

<ModuleView id="developer.editor" context={ctx}
  props={{ documentId: opened.documentId, markers }} />
```

- **`developer.editor`**: one shared document, by `source` (opened for you) or `documentId`. Props: `source`, `documentId`, `readOnly`, `position`, `positionRequestId`, `markers` (line marks the embedding module supplies). Emits `changed`.
- **`developer.project_editor`**: a project's file tree and editor. Props: `projectId`, `rootUri`, `treeTitle`, `file`, `position`, `positionRequestId`, `markers`, `readOnly`, `tree`. Emits `changed` and `opened`.
- **`developer.text_editor`**: an editing box not bound to a document; the module keeps the content. Props: `value`, `language`, `readOnly`, `autoHeight`, `lineNumbers`, `keys`, `assist`, `answer`, `focusRequestId`. Emits `changed`, `focus`, `key`, `complete` and `inspect` (with `assist`, completion and hover are requested from the module).

Every editor of a document in the same window shares one Monaco model, so typing can move between an embedded editor and the full editor, and undo/redo is shared too. Do not create your own Monaco model or copy text to keep views in sync. Programmatic edits go through `documents.edit` with the version you read.

## 5. Shared documents from a backend

A document is a file on disk plus an edit buffer kept in Developer's backend. Unsaved buffers survive restarts. External changes on disk are detected within about a second: a clean buffer follows the disk; with unsaved input the document is marked as a conflict for the author to resolve. Reopening an editor or restoring a page never runs business commands.

Typical calls (Quant does this before a run):

```python
page = await ctx.call("developer.documents.list", {"rootUris": [root_uri]})
for document in page["documents"]:
    if document["dirty"]:
        await ctx.call("developer.documents.save",
                       {"documentId": document["documentId"], "expectedVersion": document["version"]})
```

- `documents.list({rootUris})` returns `documents` open or recovered under those folders, each with `documentId`, `uri`, `version`, `dirty` and `conflict`; `nextCursor` pages on. Without `rootUris`, it lists every unsaved document in Recovery, including module-provided sources. Use it to decide what to save before a run, instead of relying on documents your frontend happened to see.
- `documents.open` opens a file (its folder becomes a project unless it is inside one) or a module's document; every caller of the same source shares one buffer.
- `documents.read` reads whole lines a page at a time, with `version`, `dirty`, and `conflict` (with the saved text in `baseContent`).
- `documents.edit` changes the buffer at the version you read: line-range `edits` or a whole `content`. A stale version fails with `conflict`. Nothing is written to disk.
- `documents.save` writes the buffer to its source and returns `saved`, `clean` (nothing to write) or `conflict` (the file changed on disk since it was read; nothing is overwritten). Saving runs no code.
- `documents.create_checkpoint` / `restore_checkpoint` / `read_checkpoint` save and restore working state; `documents.read_versions` and `documents.resolve` compare and settle conflicts.
- `developer.document_changed` reports `opened`, `changed`, `saved` and `resolved`, with the document's version, `origin` (`user`, `ai`, `code`, a module id, `disk` or `source`) and `externalState` (`present` or `deleted`).


## 6. Debugging

**Debug in VS Code** writes an attach configuration named `TALYX: <name>` (type `debugpy`, request `attach`, with the process id) into the project's `.vscode/launch.json` and opens VS Code. Existing configurations with other names are kept. After the module restarts, run it again to update the process id.
