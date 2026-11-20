# Dependency boundary check

Run from the repository root (TypeScript resolution uses `web/node_modules`):

```powershell
& .\.venv\Scripts\python.exe -B scripts/checks/check_boundaries.py --strict
```

The check verifies import direction in Python and TypeScript across four ownership layers: shared vocabulary (`contracts`, `vocabulary`), the platform, modules, and the window's assembly layer (`web/src/shell`, `main.tsx`). Modules may import only another module's public parts, and must declare that dependency in their manifest. The SDK does not import the host, the kernel or `web/src`. Required dependencies must not form a cycle. Any violation fails the check. `tests/governance/test_boundaries.py` runs the same check under pytest. Third-party `node_modules` are excluded on both sides.

See [docs/architecture.md](../../docs/architecture.md) for the boundaries this enforces and [docs/development.md](../../docs/development.md) for the full set of checks.
