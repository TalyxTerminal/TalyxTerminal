# Writing factor, model and allocation code, and notebook cells

Research code imports `talyx_quant as q` and runs in the research environment: Python 3.12 with
pandas, numpy, scipy, statsmodels, scikit-learn, lightgbm, xgboost, catboost, pyqlib,
alphalens-reloaded, skfolio, cvxpy, arch, linearmodels, QuantLib, matplotlib, seaborn and plotly.
Extra packages are installed by the user (Quant's Packages page); `%pip` in a cell is refused.

## Save, check, run

- An object's code is one file: `factor.py`, `model.py` or `allocation.py`. Write it with
  `quant.create_object {kind, name, code, data}` or `quant.update_object {objectId, code}` (the
  whole file). A formula factor, or a model or allocation on TALYX's standard flow, becomes code
  when you give it code; `quant.write_as_code {objectId}` instead copies the standard flow (or
  converts the formula) for you to edit, and `read_object {part: "code"}` reads it.
- The answer's `codeErrors` is the check without running (syntax and the declared settings, with
  `file`, `line`, `message`): fix until empty, then `quant.start_run {objectId}`. A failure while
  running comes back with the line.
- A one-line factor needs no code: `formula` (`validate_factor_formula` checks it).

## Settings and data

- `q.param(name, default, min=, max=, step=, label=, unit=)`, `q.choice(name, default, options)`,
  `q.pick(name, kinds=("factor",))` (a library item): literal arguments only, read without running.
  `update_object {params}` sets them; `read_object {part: "options"}` lists them.
- `q.data()`: the object's data settings as a `ResearchData`: `.open`, `.high`, `.low`, `.close`,
  `.volume`, `.amount` are tables of dates by instruments (columns are the symbols); `.industry`,
  `.size`, `.shares`; `.annualization` is bars per year; `.bars` the long table.
- `q.data(["AAPL", "MSFT"], "1d", "2024-01-01", "2024-12-31", adjustment="split")`, or
  `q.data(index={"symbol": "^GSPC"}, timeframe="1d", start=...)` or `q.data(list="My list", ...)`:
  any bars, frozen through Market (an index's members only while they were members).
  `q.bars(symbol, timeframe, start, end)`: one symbol's OHLCV. Dates are inclusive.
- `q.ops`: the formula's operators on tables (`q.ops.Ref(x, 5)`, `Mean`, `Std`, `Rank`, `CSRank`,
  `Corr`, `Neutralize`, ...).
- `q.library.read(name, start=, end=)`: values of a library item, as of when they were known.

## A factor

```python
import talyx_quant as q

window = q.param("window", 20, min=1, max=500, label="Window (bars)")
groups = q.param("groups", 5, min=2, max=20, label="Groups")

prices = q.data().close
factor = prices.pct_change(window, fill_method=None).shift(1)  # only bars that had closed
q.report.factor(factor, groups=groups, settings={"neutralize": "none"})
```

`q.report.factor(table)` takes dates by instruments and makes the standard report (IC, group
returns, turnover) and the values a run publishes. Shift by one bar unless the value is known at
the close it is traded on.

## A model or an allocation

Start from `write_as_code`: the standard flow's own code, with the same settings, to edit.

- A model trains window by window: `data = q.model_data(inputs, processing=..., label=...,
  split=...)`, then `for window in data.windows(): q.record(window, q.fit("lightgbm", window,
  params))` (`fit` takes lightgbm, xgboost, catboost, linear, ridge, lasso, nnls,
  double_ensemble; `q.fit_network(NetClass, window, params)` your own torch network in the deep
  environment). Or train with Qlib (`q.qlib.init()`, `q.qlib.library_lightgbm([...])`,
  `q.qlib.train_rolling(task)`) and hand the recorder to `q.report.model(recorder, task)`.
- An allocation runs `q.allocate(settings)` (the standard flow) or `q.allocate(settings,
  estimator=MeanRisk(...))` (any skfolio optimization, fitted at every rebalance), or hands a
  walk-forward skfolio portfolio to `q.report.allocation(portfolio)`.

## Notebook cells

- A notebook is a Quant object of kind `notebook`; its cells (with ids) read with `read_object
  {part: "code"}`.
- `quant.write_cell {objectId, op: "add", source}` adds a code cell (`after: cellId`, `type:
  "markdown"`); `op: "replace"` with `cellId` and `source`; `op: "delete"`. It saves the notebook.
- `quant.run_cell {objectId, cellId}` runs a saved cell in the notebook's kernel and returns its
  outputs as text (a plot is named, not sent). Variables stay in the kernel between cells:
  `read_notebook_variables`.
- In cells: `q.data(...)`, `q.run(run_id)` (any run's frozen data and tables), `q.backtest(name,
  params)` (a strategy run tagged Notebook), `q.library.read(...)`.

## Research tools
- list_presets returns descriptions. list_common_code gives the shared project directory for developer.projects.open. Before changing source, check_parameters checks candidate declarations. read_object part dataDefaults previews account, venue and fees.
- estimate_sweep accepts the strategy axes, plus kind "walk" and the window lengths for walk-forward. select_sweep_cell saves a completed selection. apply_run applies its parameters. A failed factor data fetch can continue with resume_run. Read factor preparation with read_run part fetch.
- Model training supports promote_model_run, reset_model, read_run part valuesProgress and read_model_figures. Read allocation snapshots with read_run part code.
- read_library_material accepts a versionId and part report, source, file or table. Source without a name lists files. File returns a file reference. For a notebook bound to a published version, use create_object {kind:"notebook", fromVersion}.
- Read a complete notebook and its document version with read_object part notebook. Pass that version and the edited model to save_notebook. preview_notebook_extract shows what selected cells would contribute to create_object.
- control_notebook_kernel accepts status, interrupt, stop and restart. After switching environments, run_cell waits for the replacement. read_notebook_variables with name reads the variable as a table.
- read_environments part packages lists requirements, packages and the install job. manage_package_install starts a research installation or cancels the active research or deep installation.
