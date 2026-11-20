"""Publish skfolio's native hierarchy and verified continuation packages."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import pickle
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import cloudpickle
import numpy as np
import pandas as pd
from native.publish import safe as publisher_safe


def safe(value):
    if isinstance(value,np.ndarray):
        return safe(value.tolist())
    if isinstance(value,dict):
        return {str(key):safe(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)):
        return [safe(item) for item in value]
    return publisher_safe(value)


def online_capable(estimator):
    if estimator is None or not callable(getattr(estimator,"partial_fit",None)):
        return False
    for name in ("prior_estimator_","factor_prior_estimator_","mu_estimator_","covariance_estimator_","alpha_estimator_"):
        child = getattr(estimator,name,None)
        if child is not None and not online_capable(child):
            return False
    return True


def prior_component(estimator):
    final = estimator.steps[-1][1] if hasattr(estimator,"steps") else estimator
    return getattr(final,"prior_estimator_",final)


def factor_state(estimator,inputs=None):
    prior = prior_component(estimator)
    return getattr(prior,"factor_model_",None) or getattr(getattr(prior,"return_distribution_",None),"factor_model",None) or (inputs or {}).get("factorModel")


def save(publisher, estimator, portfolio, inputs, config, unit_id="portfolio"):
    from skfolio import FailedPortfolio, MultiPeriodPortfolio, Population, Portfolio
    from skfolio.prior import BasePrior
    root = publisher.root / "portfolio-state" / unit_id
    root.mkdir(parents=True, exist_ok=False)
    if isinstance(portfolio,Population):
        for i,member in enumerate(portfolio):
            if id(member) in getattr(publisher,"_portfolio_saved",{}):
                continue
            child_config = {key:value for key,value in config.items() if key != "estimator"}
            if config.get("estimator"):
                child_config["allocationConditions"]=config["estimator"]
            child_config.update(method="manual" if isinstance(member,Portfolio) else "native_path",portfolioParams=allocation_params(member),annualization=getattr(member,"annualization_factor",config.get("annualization",252)))
            if isinstance(member,Portfolio):
                if member.weights is not None:
                    child_config["weights"] = dict(zip(member.assets,member.weights.tolist(),strict=True))
                child_config["assets"] = list(member.assets)
                child_inputs = {"returns":pd.DataFrame(member.X,index=member.observations,columns=member.assets)}
                if factor_state(estimator,inputs) is not None:
                    child_inputs["factorModel"] = factor_state(estimator,inputs)
            else:
                child_inputs = inputs
            save(publisher,None,member,child_inputs,child_config,f"{unit_id}/candidate-{i+1}")
    files = []
    for role, value in (("estimator", estimator), ("native_portfolio", portfolio), ("inputs", inputs), ("component_definition", config)):
        if value is None:
            continue
        path = root / (role + ".pkl")
        path.write_bytes(cloudpickle.dumps(value, protocol=5))
        files.append({"name": path.relative_to(publisher.root).as_posix(), "digest": hashlib.sha256(path.read_bytes()).hexdigest(), "role": role})
        publisher.artifact(path, role.replace("_", " ").title(), "native")
    operations = ["portfolio.analyze"]
    if estimator is not None and hasattr(estimator, "predict") or isinstance(portfolio,Portfolio) and not isinstance(portfolio,FailedPortfolio):
        operations.append("portfolio.predict")
    final = estimator.steps[-1][1] if hasattr(estimator,"steps") else estimator
    batch_failure = publisher.invocation.get("operation","native.run") != "portfolio.update" and (getattr(final,"error_",None) is not None or bool(getattr(final,"fallback_chain_",None)))
    if hasattr(estimator,"predict") and online_capable(estimator) and not inputs.get("invalidOnlineState") and not batch_failure:
        operations.append("portfolio.update")
    component_validation = config.get("method") in {"manual","validate","online"} or hasattr(estimator,"predict")
    multiple_allocations = type(portfolio).__name__ == "Population"
    from native.portfolio_fixed_risk import FixedResultPrior
    fixed_risk = any(role in config.get("resultMaterials", {}) for role in ("covariance", "alpha")) or isinstance(prior_component(estimator), FixedResultPrior)
    if config.get("estimator") and not inputs.get("alphaEvaluation") and component_validation and not multiple_allocations and not fixed_risk:
        operations.append("portfolio.validate")
    frame = inputs.get("returns")
    analyses = ["alpha"] if inputs.get("alphaEvaluation") is not None else ["risk"]
    if portfolio is not None and type(portfolio).__name__ == "Portfolio":
        analyses.append("stress")
    factor_model = factor_state(estimator,inputs)
    if factor_model is not None and portfolio is not None:
        analyses.append("predicted")
        from skfolio.portfolio._portfolio import _select_realized_observation_window
        try:
            positions,_ = _select_realized_observation_window(portfolio.observations,attribution_model(portfolio,factor_model))
        except ValueError:
            positions = []
        if len(positions) > 1:
            analyses.extend(["realized","rolling"])
    table_roles = {"path":"portfolio_returns","composition":"target_weights","weights":"target_weights","contributions":"asset_risk_contributions","exposures":"factor_exposures","specific-returns":"specific_returns","factor-returns":"factor_returns","expected-returns":"expected_returns","covariance":"asset_covariance","alpha-decomposition":"alpha_decomposition"}
    for artifact in publisher.index["artifacts"]:
        if artifact.get("kind") == "table" and artifact["name"].startswith(unit_id+"/"):
            label = Path(artifact["name"]).stem
            if label in table_roles:
                path = publisher.root / artifact["name"]
                files.append({"name":artifact["name"],"digest":hashlib.sha256(path.read_bytes()).hexdigest(),"role":table_roles[label],"format":"parquet"})
    observations = getattr(portfolio,"observations",None)
    available = config.get("resultAvailableAtNs")
    evidence = config.get("resultAvailabilityEvidence")
    if available is not None:
        if not str(available).isdigit() or int(available) > 2**64 - 1 or not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("Declare the result's exact release instant and its source availability evidence.")
        if frame is not None and len(frame) and int(available) < int(pd.Timestamp(frame.index.max()).value):
            raise ValueError("This whole-sample result cannot be released before its last fitted source observation.")
    allocation_assets=list(portfolio.assets) if isinstance(portfolio,Portfolio) else list(dict.fromkeys(asset for period in portfolio for asset in period.assets)) if isinstance(portfolio,MultiPeriodPortfolio) else list(frame.columns) if frame is not None else []
    manifest = {"formatVersion": 1, "resultId": str(uuid4()), "engine": "skfolio", "engineVersion": importlib.metadata.version("skfolio"),
                "nativeKind": type(portfolio if portfolio is not None else estimator if estimator is not None else inputs.get("alphaEvaluation")).__name__, "files": files,
                "recipeRef": publisher.invocation.get("recipeRef"), "inputRefs": publisher.invocation.get("materials", []),
                "axes": {"assets": allocation_assets, "inputAssets":list(frame.columns) if frame is not None else [], "assetIdentity": config.get("assetIdentity", {}),
                         "estimationRange": {"start": safe(frame.index.min()), "end": safe(frame.index.max()), "observations": len(frame)} if frame is not None and len(frame) else None,
                         "evaluationRange": {"start":safe(min(observations)),"end":safe(max(observations)),"observations":len(observations)} if observations is not None and len(observations) else None,
                         "timezone":config.get("timezone"),"assetCurrencies":config.get("assetCurrencies",{}),
                         "availableAtNs": str(available) if available is not None else None,"availabilityEvidence": evidence,
                         "historyMeaning": config.get("historyMeaning", "Hypothetical fixed allocation")},
                "units": {"returns": "simple_return", "specific_returns": "simple_return", "factor_returns": "simple_return", "covariance": "simple_return_squared", "expected_returns": "simple_return", "weights": "fraction", "currency": config.get("currency"), "annualization": config.get("annualization", 252),
                          "observationFrequency": config.get("returnFrequency") or (pd.infer_freq(frame.index) if frame is not None and isinstance(frame.index, pd.DatetimeIndex) and len(frame) >= 3 else None), "returnHorizon": config.get("returnHorizon", 1)},
                "componentDefinition": config.get("estimator") if not fixed_risk else None,
                "componentRole": "prior" if isinstance(estimator, BasePrior) else "estimator",
                "availableOperations": operations, "analysisKinds":analyses}
    manifest["axes"].update(allocationMethod=config.get("method"), executionEligible=config.get("executionEligible"),
                            allocationEstimationEndNs=str(pd.Timestamp(frame.index.max()).value) if frame is not None and len(frame) and isinstance(frame.index,pd.DatetimeIndex) else None)
    if isinstance(portfolio,MultiPeriodPortfolio):
        manifest["axes"]["periodAssets"]=[list(period.assets) for period in portfolio]
    if isinstance(portfolio,Population):
        manifest["members"]=[{key:value for key,value in publisher._portfolio_saved[id(member)].items() if key in {"unitId","artifactName","digest","resultId","nativeKind"}} for member in portfolio]
    path = root / "manifest.json"
    manifest=safe(manifest)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf8")
    artifact = publisher.artifact(path, "Saved portfolio research", "result", digest=hashlib.sha256(path.read_bytes()).hexdigest(), availableOperations=operations, resultId=manifest["resultId"])
    indexed = {**manifest, "unitId": unit_id, "artifactName": artifact["name"], "digest": artifact["digest"]}
    publisher.index.setdefault("results", []).append(indexed)
    common = {"sourceAxes": manifest["axes"], "units": manifest["units"], "engine": manifest["engine"], "nativeKind": manifest["nativeKind"], "sourceRef": {"runId": publisher.invocation["runId"], "unitId": unit_id, "artifactName": artifact["name"], "digest": artifact["digest"]}}
    if isinstance(portfolio, Portfolio) and not isinstance(portfolio, FailedPortfolio):
        last_observation = frame.index.max() if frame is not None and len(frame) else None
        weights = pd.DataFrame({"instrument": portfolio.assets, "value": portfolio.weights, "datetime": last_observation})
        publisher.named_table(weights, unit_id + "/target_weights", "target_weights", **common)
    if isinstance(portfolio, (Portfolio, MultiPeriodPortfolio)) and not isinstance(portfolio, FailedPortfolio):
        series = pd.DataFrame({"datetime": portfolio.observations, "value": portfolio.cumulative_returns, "instrument": "portfolio"})
        publisher.named_table(series, unit_id + "/curve", "curve", **common)
    if portfolio is not None and not isinstance(portfolio,Population):
        if not hasattr(publisher,"_portfolio_saved"):
            publisher._portfolio_saved={}
        publisher._portfolio_saved[id(portfolio)]=indexed
    publisher.index["engineVersion"] = manifest["engineVersion"]
    if not any(unit["id"] == unit_id for unit in publisher.index["units"]):
        publisher.index["units"].append({"id":unit_id,"title":config.get("name",manifest["nativeKind"]),"state":"completed","nativeKind":manifest["nativeKind"]})
    publisher.write()
    return indexed


def load(invocation):
    from native.state_files import read_source
    manifest, files = read_source(invocation)
    if manifest.get("formatVersion") != 1 or manifest.get("engine") != "skfolio" or manifest.get("engineVersion") != importlib.metadata.version("skfolio"):
        raise ValueError("This portfolio needs its original skfolio version and supported state format.")
    values = {item["role"]: pickle.loads(files[item["name"]].read_bytes()) for item in manifest["files"] if item["role"] in {"estimator","native_portfolio","inputs","component_definition"}}
    return manifest, values


def publish(publisher, value, unit_id="portfolio", title="Portfolio analysis"):
    from skfolio import FailedPortfolio, MultiPeriodPortfolio, Population, Portfolio
    artifacts = []
    report_errors = []
    if isinstance(value, FailedPortfolio):
        message = getattr(value,"optimization_error",None) or "The native estimator returned a failed allocation."
        artifacts.append(publisher.table(pd.DataFrame([{"failure":message}]),unit_id+"/failure","Native allocation failure"))
        publisher.section(unit_id,title,artifacts)
        publisher.index["units"].append({"id":unit_id,"title":title,"state":"failed","nativeKind":"FailedPortfolio","error":str(message)})
        publisher.write()
        return
    if isinstance(value, Population):
        for i, member in enumerate(value):
            if id(member) not in getattr(publisher,"_portfolio_saved",{}):
                publish(publisher, member, f"{unit_id}/candidate-{i + 1}", member.name or f"Candidate {i + 1}")
        artifacts.append(publisher.table(value.summary(formatted=False), unit_id + "/comparison", "Native population comparison"))
        if len(value) > 1:
            from native.chart_publication import plotly_figures
            for name, method, chapter in [("comparison-cumulative", "plot_cumulative_returns", "performance"), ("comparison-drawdown", "plot_drawdowns", "risk"), ("comparison-composition", "plot_composition", "weights"), ("comparison-rolling", "plot_rolling_measure", "risk")]:
                try:
                    artifacts.extend(plotly_figures(publisher, [getattr(value, method)()], unit_id + "/" + name, name.replace("-", " ").title(), chapter=chapter, engine="skfolio"))
                except Exception as error:
                    report_errors.append({"title": method, "message": str(error)})
    elif isinstance(value, (Portfolio, MultiPeriodPortfolio)):
        metrics = {}
        for label, attribute in (("return", "cumulative_returns"), ("volatility", "annualized_standard_deviation"), ("turnover", "turnover"), ("sharpe", "sharpe_ratio"), ("drawdown", "max_drawdown")):
            try:
                raw = getattr(value, attribute)
                metrics[label] = float(raw[-1] if label == "return" else raw)
            except (AttributeError, TypeError, ValueError, IndexError):
                metrics[label] = None
        publisher.index.setdefault("metrics", {}).update(metrics)
        artifacts.append(publisher.table(value.summary(formatted=False), unit_id + "/summary", "Native risk and performance"))
        artifacts.append(publisher.table(pd.DataFrame({"return": value.returns, "cumulative_return": value.cumulative_returns, "drawdown": value.drawdowns}, index=pd.Index(value.observations, name="date")), unit_id + "/path", "Returns and drawdown"))
        artifacts.append(publisher.table(value.composition, unit_id + "/composition", "Target composition"))
        if isinstance(value, MultiPeriodPortfolio):
            for i, member in enumerate(value.portfolios):
                publish(publisher, member, f"{unit_id}/period-{i + 1}", f"Period {i + 1}")
        else:
            from skfolio.measures import RiskMeasure
            contributions = pd.DataFrame({"weight": value.weights, "ending_weight": value.ending_weights,
                                          "variance_contribution": value.contribution(RiskMeasure.VARIANCE),
                                          "volatility_contribution": value.contribution(RiskMeasure.STANDARD_DEVIATION)}, index=pd.Index(value.assets, name="asset"))
            artifacts.append(publisher.table(contributions, unit_id + "/contributions", "Asset risk contributions"))
        for label, method in (("cumulative", "plot_cumulative_returns"), ("drawdown", "plot_drawdowns"), ("composition", "plot_composition")):
            try:
                if label=="composition" and value.composition.shape[1]==1:
                    # Plotly Express 7 rejects skfolio's one-column wide-form call.
                    # Visualize the unchanged native composition table directly.
                    import plotly.graph_objects as go
                    composition=value.composition
                    figure=go.Figure(go.Bar(x=list(composition.index),y=composition.iloc[:,0].to_numpy(),name=str(composition.columns[0])))
                    figure.update_layout(xaxis_title="Asset",yaxis_title="Weight")
                else:
                    figure=getattr(value, method)()
                from native.chart_publication import plotly_figures
                artifacts.extend(plotly_figures(publisher, [figure], unit_id + "/" + label, label.replace("_", " ").title(),
                                                chapter={"cumulative":"performance", "drawdown":"risk", "composition":"weights"}[label], engine="skfolio"))
            except Exception as error:
                report_errors.append({"title":method,"message":str(error)})
        from native.chart_publication import plotly_figures
        from skfolio.measures import RiskMeasure
        for label, draw, chapter in [("risk-contribution", lambda: value.plot_contribution(RiskMeasure.STANDARD_DEVIATION), "risk"),
                                      ("rolling-sharpe", lambda: value.plot_rolling_measure(), "risk")]:
            try:
                artifacts.extend(plotly_figures(publisher, [draw()], unit_id + "/" + label, label.replace("-", " ").title(), chapter=chapter, engine="skfolio", order=10))
            except Exception as error:
                report_errors.append({"title": label, "message": str(error)})
    else:
        raise TypeError("Publish a native Portfolio, MultiPeriodPortfolio or Population.")
    publisher.section(unit_id, title, artifacts)
    publisher.index["units"].append({"id": unit_id, "title": title, "state": "completed", "nativeKind": type(value).__name__,"reportErrors":report_errors})
    publisher.write()


def publish_factor(publisher, estimator, unit_id="factor-risk", *, observations=None):
    distribution = estimator.return_distribution_
    names = list(getattr(estimator, "feature_names_in_", range(len(distribution.mu))))
    artifacts = [publisher.table(pd.DataFrame({"expected_return": distribution.mu}, index=pd.Index(names, name="asset")), unit_id + "/expected-returns", "Estimated expected returns"),
                 publisher.table(pd.DataFrame(distribution.covariance, index=pd.Index(names, name="asset"), columns=names), unit_id + "/covariance", "Estimated asset covariance")]
    sample_weight=getattr(distribution,"sample_weight",None)
    if sample_weight is not None:
        artifacts.append(publisher.table(pd.DataFrame({"native_probability":sample_weight},index=pd.RangeIndex(len(sample_weight),name="observation")),unit_id+"/probabilities","Native scenario probabilities"))
    entropy={key:getattr(estimator,key) for key in ("relative_entropy_","effective_number_of_scenarios_") if hasattr(estimator,key)}
    if entropy:
        artifacts.append(publisher.table(pd.DataFrame([entropy]),unit_id+"/entropy","Native entropy diagnostics"))
    model = getattr(estimator, "factor_model_", None)
    loading = getattr(getattr(estimator, "loading_matrix_estimator_", None), "loading_matrix_", None)
    if loading is not None:
        artifacts.append(publisher.table(pd.DataFrame(loading, index=pd.Index(names, name="asset")), unit_id + "/loadings", "Time-series factor loadings"))
    if model is not None:
        original_times = pd.DatetimeIndex(observations) if observations is not None else None
        for label, method in (("factor-returns", "factor_returns_df"), ("specific-returns", "idio_returns_df"), ("exposures", "exposures_df")):
            value = getattr(model, method)
            value = value() if callable(value) else value
            value = value.rename_axis(index="date")
            native_times = pd.DatetimeIndex(value.index)
            if original_times is not None and original_times.tz is not None:
                # fit_metadata stores the original aware return observation
                # instants as UTC-naive AssetPanel observations. Match every
                # native label back to that exact original axis before display.
                if native_times.tz is None:
                    utc_labels = original_times.tz_convert("UTC").tz_localize(None)
                    positions = utc_labels.get_indexer(native_times)
                    if (positions < 0).any():
                        raise ValueError("Native factor observations do not match the original UTC-normalized training clock.")
                    value.index = original_times.take(positions).rename("date")
                else:
                    if (original_times.get_indexer(native_times) < 0).any():
                        raise ValueError("Native factor observations differ from the original training instants.")
                    value.index = native_times.tz_convert(original_times.tz).rename("date")
            if label == "exposures":
                # Preserve the actual factor/asset identity rather than
                # flattening both names into a display column.
                value = value.stack(level="asset", future_stack=True).rename_axis(index=["date", "asset"])
            artifacts.append(publisher.table(value, unit_id + "/" + label, label.replace("-", " ").title(),
                observationTimezone=str(value.index.tz) if isinstance(value.index,pd.DatetimeIndex) else str(original_times.tz) if original_times is not None and original_times.tz is not None else None,
                observationClock="Exact original training instants; native UTC-naive AssetPanel labels matched before conversion" if original_times is not None and original_times.tz is not None else "Original native observation labels"))
        artifacts.append(publisher.table(model.summary(annualization_factor=publisher.index.get("effectiveConfig", {}).get("annualization", 252)), unit_id + "/diagnostics", "Factor model diagnostics"))
        alpha = getattr(getattr(estimator, "alpha_estimator_", None), "alpha_", None)
        decomposition = {"factor_spanned_expected_return": model.loading_matrix @ model.factor_mu}
        if model.idio_mu is not None:
            decomposition["orthogonal_expected_return"] = model.idio_mu
        if alpha is not None:
            decomposition["alpha_forecast"] = alpha
        artifacts.append(publisher.table(pd.DataFrame(decomposition, index=pd.Index(names, name="asset")), unit_id + "/alpha-decomposition", "Expected return and alpha decomposition"))
    publisher.section(unit_id, "Factor risk and alpha" if model is not None or loading is not None else "Return and covariance estimates", artifacts)
    publisher.write()


def attribution_model(portfolio,factor_model):
    portfolio_times = pd.DatetimeIndex(portfolio.observations)
    factor_times = pd.DatetimeIndex(factor_model.observations)
    if portfolio_times.tz is not None and factor_times.tz is None:
        # AssetPanel stores datetime64 instants without a timezone. Reattach the
        # known UTC interpretation on a copy for native label alignment.
        factor_model = replace(factor_model, observations=factor_times.tz_localize("UTC").tz_convert(portfolio_times.tz).to_numpy())
    return factor_model


def publish_attribution(publisher, portfolio, factor_model, mode, options=None):
    factor_model = attribution_model(portfolio,factor_model)
    allowed = {"predicted": "predicted_attribution", "realized": "realized_attribution", "rolling": "rolling_realized_attribution"}
    result = getattr(portfolio, allowed[mode])(factor_model=factor_model, **(options or {}))
    artifacts = [publisher.table(getattr(result, method)(), "attribution/" + label, label.title()) for label, method in (("summary", "summary_df"), ("factors", "factors_df"), ("families", "families_df"))]
    if mode in {"realized","rolling"}:
        from skfolio.portfolio._portfolio import _select_realized_observation_window
        positions,_ = _select_realized_observation_window(portfolio.observations,factor_model)
        artifacts.append(publisher.table(pd.DataFrame([{"portfolio_observations":len(portfolio.observations),"aligned_observations":len(positions),"start":portfolio.observations[positions[0]],"end":portfolio.observations[positions[-1]]}]),"attribution/coverage","Native overlapping attribution observations"))
    publisher.section("attribution", mode.title() + " factor attribution", artifacts)
    return result


def publish_alpha(publisher, evaluation,unit_id="alpha"):
    artifacts = []
    for method in ("ic_summary", "portfolio_summary", "quantile_summary", "calibration_summary", "coverage_summary", "holding_period_summary", "decay_summary"):
        artifacts.append(publisher.table(getattr(evaluation, method)(), unit_id+"/" + method, method.removesuffix("_summary").replace("_", " ").title()))
    from native.chart_publication import plotly_figures
    artifacts.extend(plotly_figures(publisher, [evaluation.plot_cumulative_ic(), evaluation.plot_cumulative_returns(), evaluation.plot_ic_decay()], unit_id+"/diagnostics", "Alpha forecast diagnostics", chapter="performance", engine="skfolio"))
    publisher.section(unit_id, "Native alpha forecast evaluation", artifacts)


def publish_estimator(publisher, estimator, frame, unit_id="estimate"):
    final = estimator.steps[-1][1] if hasattr(estimator,"steps") else estimator
    weights = getattr(final,"weights_",None)
    if weights is not None:
        weights = np.atleast_2d(weights)
        names = getattr(final,"feature_names_in_",frame.columns if weights.shape[1]==len(frame.columns) else range(weights.shape[1]))
        table = pd.DataFrame(weights.T,index=pd.Index(names,name="asset"))
        publisher.section(unit_id,"Native fitted allocation",[publisher.table(table,unit_id+"/weights","Fitted target weights")])
    alpha = getattr(final,"alpha_",None)
    if alpha is not None:
        names=getattr(final,"asset_names_",frame.columns)
        publisher.section(unit_id,"Native fitted alpha",[publisher.table(pd.DataFrame({"expected_return_forecast":alpha},index=pd.Index(names,name="asset")),unit_id+"/alpha","Latest fitted alpha forecast")])
    if hasattr(final,"return_distribution_"):
        publish_factor(publisher,final,unit_id+"/prior",observations=frame.index)
    elif hasattr(prior_component(estimator),"return_distribution_"):
        publish_factor(publisher,prior_component(estimator),unit_id+"/prior",observations=frame.index)


def allocation_params(portfolio):
    import inspect

    from skfolio import Portfolio
    return {name:getattr(portfolio,name) for name in inspect.signature(Portfolio).parameters if name not in {"X","weights","name","tag","kwargs"} and hasattr(portfolio,name)}
