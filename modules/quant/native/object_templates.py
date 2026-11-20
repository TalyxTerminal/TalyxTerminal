"""Starter code for each kind of Quant object: only the author's own logic, no data or venue setup."""

STRATEGY = '''"""A new strategy. It places no orders yet: write the trading logic in on_bar.

The data, venue, fees and account come from the strategy's data and run settings; the numbers declared with q.param show up as editable
fields beside the chart.
"""
import talyx_quant as q
from nautilus_trader.model import OrderSide


class MyStrategy(q.Strategy):
    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        # Decide on this bar's close; an order fills on the next bar. For example:
        # self.submit_order(self.order_factory.market(self.instrument_id, OrderSide.BUY, self.instrument.make_qty(0.1)))
        pass
'''

# One empty code cell; TALYX writes the kernel it runs in when the notebook is saved.
NOTEBOOK = """{
 "cells": [{"cell_type": "code", "execution_count": null, "id": "first", "metadata": {}, "outputs": [], "source": []}],
 "metadata": {"kernelspec": {"display_name": "Python 3 (TALYX research)", "language": "python", "name": "python3"}},
 "nbformat": 4,
 "nbformat_minor": 5
}
"""

SOURCES = {"strategy": ("strategy.py", STRATEGY)}

FACTOR = '''"""Momentum: the return over the last `window` bars, lagged one bar so it only uses bars that had closed.

A factor is a table of dates by instruments. q.report.factor turns it into the standard factor report
(information coefficient, group returns, turnover); when you like the result, publish it from the run to the library.
"""
import talyx_quant as q

window = q.param("window", 20, min=1, max=500, label="Window (bars)")
groups = q.param("groups", 5, min=2, max=20, label="Groups")

prices = q.data().close
factor = prices.pct_change(window, fill_method=None).shift(1)
q.report.factor(factor, groups=groups, settings={"neutralize": "none"})
'''

MODEL = '''"""Gradient boosting on selected library factors, retrained every `step` bars on everything before each test window.

q.qlib.library_lightgbm returns a plain Qlib task using only the chosen factor versions: edit it, or write your own model and task.
q.report.model turns the trained Recorder into the standard model report; publish the predictions and the model from the run.
"""
import talyx_quant as q

factor = q.library_param("factor", "", label="Training factor from the library")
rounds = q.param("rounds", 100, min=10, max=2000, label="Boosting rounds")
train_bars = q.param("train_bars", 250, min=20, label="First training window (bars)")
valid_bars = q.param("valid_bars", 60, min=5, label="Validation window (bars)")
step = q.param("step", 20, min=1, label="Retrain every (bars)")

q.qlib.init()
task = q.qlib.library_lightgbm([factor], rounds=rounds, train_bars=train_bars, valid_bars=valid_bars, step=step)
recorder = q.qlib.train_rolling(task, step=step)
q.report.model(recorder, task)
'''

ALLOCATION = '''"""Minimum-variance allocation, refit every `rebalance` bars on the last `lookback` bars.

skfolio walks forward through the returns: each rebalance uses only what came before it, and the newest (shorter) period is kept so today's weights exist today.
q.report.allocation turns the result into the standard allocation report; publish the weights from the run.
"""
from skfolio import RiskMeasure
from skfolio.model_selection import WalkForward, cross_val_predict
from skfolio.optimization import MeanRisk

import talyx_quant as q

lookback = q.param("lookback", 120, min=20, label="Fitting window (bars)")
rebalance = q.param("rebalance", 20, min=1, label="Rebalance every (bars)")

returns = q.data().close.pct_change(fill_method=None).dropna(how="all").fillna(0.0)
model = MeanRisk(risk_measure=RiskMeasure.VARIANCE, min_weights=0, max_weights=1, budget=1)
portfolio = cross_val_predict(model, returns, cv=WalkForward(train_size=lookback, test_size=rebalance, reduce_test=True),
                              portfolio_params={"annualization_factor": q.data().annualization})
q.report.allocation(portfolio)
'''

SOURCES.update(notebook=("notebook.ipynb", NOTEBOOK), factor=("factor.py", FACTOR), model=("model.py", MODEL), allocation=("allocation.py", ALLOCATION))
