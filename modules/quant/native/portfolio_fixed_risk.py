"""An explicitly dated saved estimate is a native prior, separate from CV fits."""
import numpy as np
import pandas as pd
from native.result_inputs import fixed_table
from native.strategy_options import native_ns
from skfolio.prior import BasePrior, EmpiricalPrior, ReturnDistribution
from sklearn.utils.validation import validate_data


class FixedResultPrior(BasePrior):
    def __init__(self, assets, covariance=None, mu=None):
        self.assets, self.covariance, self.mu = assets, covariance, mu

    def fit(self, X, y=None, **fit_params):
        if list(X.columns) != list(self.assets):
            raise ValueError("Fixed risk input must use its original exact asset order.")
        matrix = validate_data(self, X)
        empirical = EmpiricalPrior().fit(X)
        distribution = empirical.return_distribution_
        self.return_distribution_ = ReturnDistribution(
            mu=distribution.mu if self.mu is None else np.asarray(self.mu),
            covariance=distribution.covariance if self.covariance is None else np.asarray(self.covariance),
            returns=matrix)
        return self


def prior(invocation, config, frame):
    if invocation.get("operation") == "portfolio.validate" and any(role in config.get("resultMaterials", {}) for role in ("covariance", "alpha")):
        raise ValueError("This saved allocation used fixed fitted estimates. Choose its component definition for a new training-window validation.")
    selected = {item["role"]: item for item in invocation.get("materials", []) if item.get("role") in {"covariance", "alpha"} and item.get("sourceResult")}
    if not selected:
        return None
    if config.get("method", "manual") in {"manual", "alpha", "population", "factor"}:
        raise ValueError("Fixed covariance and expected-return estimates need a native allocation estimator with a prior input. Choose estimate and optimize, or remove the fixed estimate for historical manual-allocation risk.")
    if invocation.get("operation") == "portfolio.validate" or config.get("method") in {"validate", "online", "online_fit", "online_search"}:
        raise ValueError("Fixed fitted risk/alpha values are separate from time validation. Choose the component definition to fit it in each training window.")
    as_of = native_ns(config.get("riskInputAsOfNs"), "Risk input decision time")
    if as_of is None:
        raise ValueError("Set a decision time for the fixed risk or expected-return estimate.")
    covariance, mu = None, None
    for role, material in selected.items():
        manifest, table = fixed_table(material)
        cutoff = manifest.get("axes", {}).get("allocationEstimationEndNs")
        if cutoff is None or int(cutoff) >= as_of:
            raise ValueError("The saved risk estimate needs its original estimation cutoff strictly before the decision.")
        if manifest.get("units", {}).get("currency") != config.get("currency"):
            raise ValueError("Use the saved estimate's original return currency, or an explicit currency-converted input.")
        units = manifest.get("units", {})
        frequency = config.get("returnFrequency") or (pd.infer_freq(frame.index) if len(frame) >= 3 else None)
        if not frequency or units.get("observationFrequency") != frequency or units.get("returnHorizon") != config.get("returnHorizon", 1) or units.get("annualization") != config.get("annualization", 252):
            raise ValueError("Fixed risk and expected returns need the same original observation frequency, return horizon and annualization. Provide an explicit justified conversion before using a different period.")
        if "asset" not in table or table.asset.duplicated().any() or set(table.asset) != set(frame.columns):
            raise ValueError("The fixed estimate must cover exactly the receiving assets.")
        identities = manifest.get("axes", {}).get("assetIdentity", {})
        receiving_ids = config.get("assetIdentity", {})
        if any(not identities.get(asset) or receiving_ids.get(asset) != identities[asset] for asset in frame.columns):
            raise ValueError("The fixed estimate and receiving returns need the same explicit original asset identities.")
        table = table.set_index("asset").reindex(frame.columns)
        if role == "covariance":
            if manifest.get("units", {}).get("covariance") != "simple_return_squared":
                raise ValueError("Covariance needs its saved per-observation simple-return units.")
            covariance = table.loc[:, frame.columns].to_numpy(dtype=float)
            if not np.isfinite(covariance).all() or not np.allclose(covariance, covariance.T, atol=1e-12) or np.linalg.eigvalsh(covariance).min() < -1e-10:
                raise ValueError("The saved covariance must be finite, symmetric and positive semidefinite.")
        else:
            if manifest.get("units", {}).get("expected_returns") != "simple_return" or "expected_return" not in table:
                raise ValueError("Expected returns need their original return target and per-observation units; ranking scores cannot be used as returns.")
            mu = table.expected_return.to_numpy(dtype=float)
            if not np.isfinite(mu).all():
                raise ValueError("Expected returns must be finite for every receiving asset.")
    return FixedResultPrior(list(frame.columns), covariance, mu)
