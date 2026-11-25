# Development

How to run TalyxTerminal from source, where it keeps its files, and how to check a change. For how the platform is put together, read [architecture.md](architecture.md) first.

## Requirements

Windows x64 with:

- Git, [uv](https://docs.astral.sh/uv/) and Node.js LTS (24 is tested)
- Microsoft Edge WebView2 (present on current Windows; the installer adds it when missing)
- Optional: VS Code with the Python extension and `code` on PATH, for Developer's "Open in VS Code" and breakpoint debugging

Missing tools can be installed with winget:

```powershell
winget install --id Git.Git -e
winget install --id astral-sh.uv -e
winget install --id OpenJS.NodeJS.LTS -e
```

## Install from source

In a new PowerShell at the repository root:

```powershell
$env:UV_PROJECT_ENVIRONMENT = Join-Path $PWD '.venv'
uv sync --python 3.13 --managed-python --locked --compile-bytecode
npm.cmd --prefix web ci
```

Chart's PyneCore and Quant's Qlib wheels ship in the repository. To run Quant strategies, download the NautilusTrader `.whl` from the same release on the Releases page into `modules/quant/runtime/wheels/`. The expected file name and SHA-256 are in `modules/quant/runtime/strategy.identity.json`; the runtime files are described in [modules/quant/runtime](../modules/quant/runtime/README.md).

## Run

The desktop app starts the backend, the frontend and the window, with a tray icon:

```powershell
& .\.venv\Scripts\python.exe -B -m host.desktop
```

Add `--start-in-tray` to start with the window hidden.

For frontend work, run the backend and the Vite dev server separately and open `http://127.0.0.1:5173`:

```powershell
& .\.venv\Scripts\python.exe -B -m host
# in a second terminal
npm --prefix web run dev
```

In development, module frontends under `modules/` are compiled by the dev server, so TypeScript changes reload without a build. Public market data needs a network connection.

## Where files live

| What | Running from source | Installed app |
| --- | --- | --- |
| Data (workspaces, market data, logs) | `data/store` | `%LOCALAPPDATA%\TALYX\Data` |
| Your files | `user/` | inside the data folder |
| API keys | Windows Credential Manager | Windows Credential Manager |

Inside your files folder: user indicators in `module-files/chart/indicators/`, global Agent skills in `module-files/agent/skills/<name>/SKILL.md`, workspace folders in `workspaces/` (each with its own `skills/`), and third-party module code in `installed-modules/`. Module data stays in the data folder under `modules/<id>/` when the module is uninstalled.

Set `TALYX_DATA_DIR` to use another folder for both data and your files. Keys with the same name are kept separate per data folder. Uninstalling the app keeps your data.

## A disposable instance

To try a change without touching your everyday data, start an isolated instance with its own data folder, port and modules:

```powershell
& .\.venv\Scripts\python.exe -B -m scripts.dev_host --data-dir output/trial/dev
# in a second terminal
$env:TALYX_DEV_BACKEND = 'http://127.0.0.1:8011'
npm --prefix web run dev -- --port 5174 --strictPort
```

Open `http://127.0.0.1:5174/`. Use `--port` and `--ui-url` to run several instances side by side. To test only the backend and MCP, add `--workspace trial`: the backend opens that workspace and prints its MCP address, with no frontend needed. The data folder is not cleaned up for you; delete it when done.

## Connecting an external AI

Open a workspace and choose **Copy AI connection address** in the workspace menu on the top bar. This gives the workspace's MCP address (`/mcp/workspaces/{contextId}`). For Claude Code:

```powershell
claude mcp add --transport http talyx <address>
```

To inspect MCP calls by hand:

```powershell
& .\.venv\Scripts\python.exe -B -m scripts.dev_ai http://127.0.0.1:8011/mcp/workspaces/<contextId>
```

Then type one JSON request per line: `{"op":"list"}` lists the tools, `{"op":"list","full":true}` prints full declarations, `{"op":"call","name":"platform_read_panel","arguments":{"view":"list"}}` calls a tool, and `{"op":"resource","uri":"talyx://workspace/current-view"}` reads what is on screen.

## Checks

The full set, run from the repository root:

```powershell
& .\.venv\Scripts\python.exe -B -m ruff check .
& .\.venv\Scripts\python.exe -B -m pytest tests -n 4 -m "not timing" --basetemp=output/pt
& .\.venv\Scripts\python.exe -B -m pytest tests -m timing --basetemp=output/pt
& .\.venv\Scripts\python.exe -B scripts/contracts/generate.py --check
npm --prefix web run build
npm --prefix web test
npm --prefix web run test:contracts
& .\.venv\Scripts\python.exe -B scripts/checks/check_i18n.py --strict
```

- The backend suite takes about six minutes with `-n 4`; most of that is tests that start real processes (the multi-process platform, Agent, Developer).
- Tests marked `timing` assert durations, so they run on their own, serially; other processes competing for the CPU would make them fail spuriously.
- A single test that runs longer than 300 seconds stops and prints every thread's stack (the `timeout` setting in `pyproject.toml`).
- Keep `--basetemp` short; deep test paths can exceed the Windows 260-character path limit.
- `pytest tests` includes the dependency boundary check (`tests/governance/test_boundaries.py`); see [scripts/checks](../scripts/checks/README.md) to run it alone.
- Public contracts are generated from code; see [scripts/contracts](../scripts/contracts/README.md).
- Both `pytest` and `npm --prefix web test --` accept specific paths.

The native engines (Qlib, NautilusTrader, deep models) have separate smoke tests on small fixed inputs. They are not part of the everyday set:

```powershell
$env:TALYX_NATIVE_SMOKE = '1'
& .\.venv\Scripts\python.exe -B -m pytest tests/native --basetemp=output/pn
Remove-Item Env:TALYX_NATIVE_SMOKE
```

Passing checks do not replace using the change in the real app.

## Performance

`scripts/perf.py` measures startup, chart loading, interval switching, source-to-screen latency and per-process CPU and memory, against a data folder with a saved workspace. It starts its own backend, frontend and headless Chromium, and stops them when done:

```powershell
& .\.venv\Scripts\python.exe -B -m scripts.perf startup --data-dir output/trial/<name> --workspace <workspace> --out output/startup.txt --close-open
& .\.venv\Scripts\python.exe -B -m scripts.perf load --data-dir output/trial/<name> --workspace <workspace> --out output/load.txt --batch "update all" --seconds 60
```

Both commands change the data folder, so use a disposable one. `scripts/quant_scenario.py` times real Quant scenarios through MCP. Run either with `--help` for the options.

## Building the installer

After installing from source, put the matching NautilusTrader source archive (`*-source.tar.gz`) from the release in `modules/quant/runtime/upstream/`, then run:

```powershell
& .\.venv\Scripts\python.exe -B installer/build.py
```

The script builds from the current Git commit, verifies the native files, builds the frontend and downloads the packaging tools. The result is in `output/installer/`. A release uploads the installer together with the NautilusTrader wheel and its source archive.
