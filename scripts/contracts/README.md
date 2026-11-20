# Contract generation

Run from the repository root:

```powershell
& .\.venv\Scripts\python.exe -B scripts/contracts/generate.py --check
```

From the declarations in code (the `Module` decorators and types), this generates, for the platform and every module with a version-5 manifest:

- `public/contract.json`;
- `public/ts/types.tsx` for frontends (the platform's goes into the SDK as `sdk/ui/src/platform.tsx`; modules reach it as `Platform` from `@talyx/sdk`);
- `public/python/client.py` for other modules' backends.

Without `--check`, the files are written. With `--check`, they are only compared, and the run fails if any is stale. To generate only some, name them: `generate.py platform market`. For a module outside `modules/`, use `--module-dir <folder>`. Generated files are never edited by hand: change the declarations and generate again.

See [docs/architecture.md](../../docs/architecture.md) for how contracts connect modules.
