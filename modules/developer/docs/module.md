# Writing a TALYX module

A module is a folder with a hand-written manifest (`talyx.module.json`), a Python backend that
declares what it offers, and a frontend entry. Official and installed modules are the same kind of
thing and run the same way, each in its own process.

## Start and edit

1. `developer.projects.create_module {name}` writes a working module from Developer's template (one
   action, `greet`, and a panel that calls it) into the user's modules folder and opens it as a
   project. Its `AGENTS.md` (`developer.projects.instructions {projectId}`) says what each file is.
2. Read and change its files through Developer's documents: `developer.projects.read_tree`,
   `developer.documents.open {source: {kind: "file", uri}}`, `documents.read`, `documents.edit`,
   `documents.save` (as in Developer's guide); a new file with `developer.documents.create`.
3. Ask the user to select the new project in Developer and click **Use in TALYX**. This installs
   and starts it for the first time. After saving a code change, click **Reload** to regenerate
   the contract and restart this module. **Enable** starts a previously disabled module. For an AI
   installation request, call platform.modules.install with the directory returned by projects.create_module.

## The SDK references

The same references Developer's Help lists, read a page at a time with
`developer.documents.read_source {source: {kind: "contribution", providerId: "developer:talyx-sdk",
sourceKey}}`:

| sourceKey | What it is |
|---|---|
| `declare` | Declaring a module in Python: `Module`, `module.action` (effects, examples, errors), streams, events, settings, extension points, `Model` |
| `process` | A module's process: `Context` (`ctx.call`, `ctx.platform`, storage, secrets) and `Call` (caller, workspace) |
| `typescript` | The frontend: `activate(ctx)`, panels, views, `ctx.actions.call`, the component library |
| `manifest` | The manifest's schema, `talyx.module.json` |
| `platform` | The platform's own actions (`platform.*`), as a contract |

## Rules a module keeps

If users write code that your module runs, declare its authoring reference in
`talyx.module.json` alongside its guide.

```json
{"ai":{"guide":"GUIDE.md","docs":[{"topic":"scripts","title":"Write and check a script","file":"docs/scripts.md"}]}}
```

The guide has YAML front matter with `name` and `description`, and up to 2,500 characters of
body text. Each topic is a separate Markdown file in the module. TALYX lists the topics in
`platform.ai.catalog`; `platform.ai.guide {module,topic}` reads their text. Include a working
example and the public action that saves and checks it.

- Everything another module, a script or AI uses is a declared action, stream or event in
  `backend/entry.py`: typed input and output (pydantic `Model`), a docstring people and AI read,
  and honest effects (`read`, `write`, `delete`, `code`, `external`, `funds`).
- It calls other modules only through their public actions, and only those named in `requires`.
- `public/` is generated; never edit it. `python.requirements` in the manifest gives the module its
  own environment.
