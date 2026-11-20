# Developer

Developer serves module development in TalyxTerminal, and the development features other modules plug into it. It owns source projects, shared documents, file history and recovery of unsaved content. It provides light editing, writing a module and using it in the running application, and a way into debugging. Heavy editing, terminals, search and breakpoint debugging are handed to VS Code. Templates, checks, runs and application rules for business extensions belong to the module that owns them (Chart's indicators, Quant's code). Installing, enabling and reloading modules, and their Python environments, belong to the platform.

## Editing

- Opening Developer goes straight to the editor: the project file tree on the left, open-file tabs and a Monaco editor in the middle. The tree is read on demand; files added outside appear after **Refresh**.
- The entry at the top left switches projects and also offers **New TALYX module**, **Open folder** and removing a project. Locations are typed as paths; there is no folder picker.
- The editor has syntax highlighting, built-in TypeScript/JSON assistance, and line markers that embedding modules supply. Cross-file search and terminals are done in VS Code.
- **Python language service.** When a development support (such as Quant) declares the `language` capability in `developer.development_support` and answers a Python environment for a file, that file gets completion, hover, signature help, go to definition, rename and static errors. Developer does not know what the environment is for. The backend uses basedpyright, one process per interpreter, connected to the editor through the module's own private WebSocket. Only Python files currently shown in some view are opened in the service; about 3 seconds after all views close, the process is reclaimed. When packages are installed into or removed from the environment, a new process replaces the old one within about 15 seconds and the editor reconnects automatically. Notebook cell assistance goes through the `assist` mode of `developer.text_editor`, not through this service.
- **Open in VS Code** opens the project folder with the `code` command. Changes saved in VS Code come back to Developer within about a second.
- There are two ways to remove a project: **Remove from list** keeps the files; **Move to Recycle Bin** moves the whole folder to the Windows Recycle Bin, and if that fails the project stays registered rather than being deleted permanently. Unsaved files in the project must be dealt with first.

## Shared documents

- A document is a file on disk plus an edit buffer kept in the backend. Opening the same source gives the same document. Chart's and Quant's embedded editors and the full editor in the same window share one Monaco model, and typing reaches the backend buffer immediately.
- Unsaved buffers are written to disk and survive a restart; they can be opened or discarded from **Recovery**. Clean documents are not stored; the file is read again next time.
- The backend checks the size and modification time of open files every second. When a file changes outside: a clean buffer follows the disk; with unsaved input both are kept, marked **Changed on disk**, and the author compares and chooses **Keep mine** or the disk version. A file that fails to read (for example, locked by another program) affects only that file and resyncs automatically once unlocked.
- When another writer (the Quant parameter panel, checkpoint restore and so on) changes the backend buffer: if this window has no unsent input, the change is adopted; otherwise both are kept, marked **Changed elsewhere**, and compared and resolved in place, so nothing is overwritten.
- Ctrl+S saves the current file. Saving checks the disk version and reports a conflict instead of overwriting if the file changed meanwhile. Saving writes source only; it never runs code.
- The controlled text editor view `developer.text_editor` lets other modules embed an editing box not bound to a document: the module supplies the content and language, each change is sent back as `changed`, and the module keeps the content itself (for example, notebook cells). In `assist` mode, completion and hover are requested from the module.
- When closing a tab, an embedded editor or the Developer panel with unsaved files that no other view still shows, you are asked to save, discard or cancel.
- Saving a workspace creates checkpoints for open files; reopening the workspace restores from the checkpoints, and anything that differs from current input goes to Recovery for comparison. The public `documents.create_checkpoint` / `restore_checkpoint` let modules such as Quant save their state the same way.
- Recovery and `documents.list` without `rootUris` both find unsaved files and module-provided documents. `documents.read_versions` and `documents.resolve` compare and settle disk conflicts; `documents.read_checkpoint` reads back fixed content.
- **File history** records versions when external changes are read and before and after saves; `projects.file_history` lists and reads revisions and `projects.restore_revision` restores one. Comparison uses Monaco's diff view.
- The public actions (`projects.*`, `documents.*`) are the same for people, the Agent and other modules. Versioned writes return `conflict` when the version does not match. Whether to ask the user is decided by the Agent; Developer adds no gate of its own.

## Development supports

- A module can contribute development supports through `developer.development_support`. A support can list documentation (guides and examples shown in Developer's Help) and can provide the `language` capability (the Python a matching file is analysed against). Selecting supports and asking them is implemented once, in the backend; an error in one support affects only that support.
- Other modules' content can be opened and edited in Developer as a document source (`developer.document_source`).
- `help` lists the reference material of the SDK and of each module. `projects.module_status` reports the module a project corresponds to and its load state; `projects.remove` removes a project from the list or moves it to the Recycle Bin.
- The public interface and fields are defined by [public/contract.json](public/contract.json); integration steps are in [Integrating with Developer](public/README.md). Chart's indicator projects and Quant's Python code and research use this interface; Developer carries none of their business meaning.

## Writing a module

This follows how VS Code extensions are written and tried in a development host, except that no second application is started: the module runs directly in the TalyxTerminal you are using.

1. **New TALYX module** (or `projects.create_module` from the Agent) needs only a name. The module is created in the folder where TalyxTerminal keeps user modules, with one action, `greet`, and a panel that calls it. The frontend is plain JavaScript and needs no build step.
2. **Use in TALYX** enables it like an installed module: its panel appears in the add-panel list and its actions enter the Agent's tool catalogue.
3. After changing code, click **Reload**: files are saved first, the platform regenerates the contract and restarts only this module; other modules keep running. After disabling, click **Enable** again; **Uninstall** removes the module (an in-place module in the user modules folder is deleted together with its folder).
4. **Debug in VS Code** writes an attach configuration into the project's `.vscode/launch.json` and opens VS Code; starting it from Run and Debug attaches to the module's current Python process. After the module restarts, click **Debug in VS Code** again to update the PID in the configuration.

Archives or folders from others are installed under Community → Installed. Building installer packages, starting a separate application instance for trying a module, and online publishing are not supported.

For module structure and the SDK, see [docs/development.md](../../docs/development.md) and [docs/architecture.md](../../docs/architecture.md).
