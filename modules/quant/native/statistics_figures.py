"""Native statsmodels diagnostic figures and native forecast intervals."""
from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd
from native.chart_publication import svg_figure


def capture(publisher, draw, name, title, chapter, order):
    before = set(plt.get_fignums())
    try:
        figure = draw()
        return svg_figure(publisher, figure, name, title, chapter=chapter, order=order, engine="statsmodels")
    except Exception as error:
        publisher.index.setdefault("nativeWarnings", []).append({"title": title, "message": str(error)})
        return []
    finally:
        for number in set(plt.get_fignums()) - before:
            plt.close(number)


def regression_figures(publisher, model, prefix="diagnostics"):
    from statsmodels.graphics.regressionplots import (
        influence_plot,
        plot_ccpr,
        plot_leverage_resid2,
        plot_partregress,
    )
    artifacts = []
    for order, (name, title, draw) in enumerate([
        ("influence-plot", "Native influence plot", lambda: influence_plot(model)),
        ("leverage-resid2", "Leverage and squared residuals", lambda: plot_leverage_resid2(model)),
    ], 1):
        artifacts.extend(capture(publisher, draw, f"{prefix}/{name}", title, "influence", order))
    for index, term in enumerate(model.model.exog_names):
        if term in {"const", "Intercept"}:
            continue
        # statsmodels 0.15's single-variable grid leaves title_kwargs unbound.
        # Its standalone native partial-regression function has no grid bug.
        def partial(index=index, term=term):
            matrix = model.model.exog
            others = [column for column in range(matrix.shape[1]) if column != index]
            return plot_partregress(pd.Series(model.model.endog, name=model.model.endog_names),
                                    pd.Series(matrix[:, index], name=term), matrix[:, others], obs_labels=False)
        artifacts.extend(capture(publisher, partial,
                                 f"{prefix}/partial-regression-{index}", "Partial regression · " + term, "influence", 10 + index))
        artifacts.extend(capture(publisher, lambda index=index: plot_ccpr(model, index),
                                 f"{prefix}/ccpr-{index}", "Component and component-plus-residual · " + term, "influence", 100 + index))
    return artifacts
