# Contributing to TalyxTerminal

Thanks for your interest. Bug reports, fixes, new indicators and new modules are all welcome.

## Before you start

- **Read [docs/architecture.md](docs/architecture.md).** Most review comments come back to it.
- **Small fixes:** open a pull request directly.
- **Larger changes** (a new module, a change to a public contract, anything that spans modules): open an issue first and describe what a user would do with it. That saves work on both sides.
- **Building a module of your own** does not need a change here at all. Modules install from their own folders. Start from Developer's guides in [modules/developer/public](modules/developer/public/README.md).

## The rules that matter most

- **Modules cooperate only through what they declare publicly.** A module may import another module only through its `public/` package, and only if its manifest declares the dependency. Official modules follow the same rule; there are no back doors.
- **Parts that serve every module work from declarations.** Agent, settings, notifications and the workspace never name a specific module in code.
- **Public contracts are generated, never hand-edited.** Declare actions, views and events in code with `talyx_sdk.declare`, then run `scripts/contracts/generate.py` to regenerate the contracts and clients under `public/`.
- **Frontends call other modules through the generated clients** (`createClient` from the module's `public/ts`), not through hand-written call names.
- **Make the smallest change that solves the problem completely.** Fix every entry point of a rule, or every caller of an interface you change, in the same change. Do not add fallbacks, compatibility branches or placeholders.
- **Let errors surface.** Catch exceptions only at real boundaries (user input, network, child processes, external files), and catch specific types.
- **Write comments and user-facing text in English.**

## Tests

- **Write tests for the rules that can go silently wrong:** instrument identity, price adjustment, units, money. Test the rule directly, without starting the whole platform.
- **Tests that start the platform** are only for cooperation between modules; keep them few.
- **Regression tests** go in `tests/`. Commands that measure performance or run real scenarios go in `scripts/`.

## Checks

Run the full set before opening a pull request (setup is in [docs/development.md](docs/development.md)):

```powershell
& .\.venv\Scripts\python.exe -B -m ruff check .
& .\.venv\Scripts\python.exe -B -m pytest tests -n 4 -m "not timing" --basetemp=output/pt
& .\.venv\Scripts\python.exe -B -m pytest tests -m timing --basetemp=output/pt
& .\.venv\Scripts\python.exe -B scripts/contracts/generate.py --check
npm --prefix web run build
npm --prefix web test
npm --prefix web run test:contracts
```

If your change affects what users see, also try it in the running app. Passing checks do not replace that.

## Pull requests

- **Keep one topic per pull request.**
- **Say in the description:**
  - what changed and why;
  - how you verified it;
  - anything you did not verify.
- **Screenshots help** for interface changes.

## License

TalyxTerminal is licensed under [Apache-2.0](LICENSE). By contributing, you agree that your contributions are licensed under the same terms.
