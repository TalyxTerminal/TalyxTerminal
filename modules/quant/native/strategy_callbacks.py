"""Observe escaping authored Nautilus callbacks without changing their native dispatch."""
from __future__ import annotations

import functools
import importlib
import inspect
import json
import traceback
from contextlib import nullcontext
from pathlib import Path


class NativeCallbackObserver:
    def __init__(self, source, publisher):
        self.source = Path(source).resolve()
        self.publisher = publisher
        self.restores = []
        self.errors = []
        self.first_error = None
        self.published = False
        self.active_engine = None
        self.callback_depth = 0
        self.components = {}
        self.executed_components = {}

    def _patch(self, target, name, replacement):
        namespace = vars(target)
        existed = name in namespace
        original = namespace.get(name)
        setattr(target, name, replacement)
        self.restores.append((target, name, existed, original))

    def _authored(self, callback):
        try:
            filename = inspect.getsourcefile(inspect.unwrap(callback))
            return filename is not None and Path(filename).resolve().is_relative_to(self.source)
        except (TypeError, ValueError):
            return False

    def _record(self, engine, name, callback, error):
        if self.first_error is None:
            self.first_error = error
        # Keep the original exception and traceback. Report formatting must never
        # replace the exception which is returned unchanged to native dispatch.
        try:
            frames = traceback.extract_tb(error.__traceback__)
            frame = next((frame for frame in reversed(frames) if Path(frame.filename).resolve().is_relative_to(self.source)), None)
            row = {"callback": name, "exceptionType": type(error).__name__, "message": str(error),
                   "source": frame.filename if frame else inspect.getsourcefile(callback),
                   "line": frame.lineno if frame else None, "traceback": "".join(traceback.format_exception(error)),
                   "engineIdentity": id(engine), "nativeRunId": None}
        except Exception:
            row = {"callback": name, "exceptionType": type(error).__name__, "message": type(error).__name__,
                   "engineIdentity": id(engine), "nativeRunId": None}
        if len(self.errors) < 20:
            self.errors.append(row)

    def _callbacks(self, target):
        for name in dir(target):
            if not name.startswith("on_"):
                continue
            callback = getattr(target, name, None)
            if not callable(callback) or not self._authored(callback) or getattr(callback, "_talyx_callback_observer", None) is self:
                continue

            def wrap(callback, name):
                @functools.wraps(callback)
                def observed(*args, **kwargs):
                    if self.first_error is not None:
                        return None  # the code failed once: it is not called again, so the run holds what it had at that moment
                    self.callback_depth += 1
                    try:
                        import talyx_quant as q
                        component = args[0] if inspect.isclass(target) else target
                        decision = q._models.callback(component) if q._models is not None and isinstance(component, q.Strategy) else nullcontext()
                        with decision:
                            return callback(*args, **kwargs)
                    except BaseException as error:
                        if not hasattr(error, "_talyx_snapshot"):
                            try:  # where the run was the moment the author's code failed; the engine may go on to the end before the failure is reported
                                error._talyx_snapshot = {"simulatedAt": component.clock.timestamp_ns() // 1_000_000, "tradesBefore": len(component.cache.positions_closed()),
                                                         "equityBefore": float(component.equity())}
                            except Exception:
                                pass
                        if self.active_engine is not None and self.callback_depth == 1:
                            self._record(self.active_engine, name, callback, error)
                        raise
                    finally:
                        self.callback_depth -= 1
                observed._talyx_callback_observer = self
                return observed

            self._patch(target, name, wrap(callback, name))

    def install(self):
        from nautilus_trader.backtest import BacktestEngine
        for name in ("add_strategy", "add_actor", "add_exec_algorithm"):
            original = getattr(BacktestEngine, name)

            def wrap_add(original, kind):
                @functools.wraps(original)
                def add(engine, component, *args, **kwargs):
                    self._callbacks(component)
                    previous = self.active_engine; self.active_engine = engine
                    try:
                        result = original(engine, component, *args, **kwargs)
                        import talyx_quant as q
                        if q._models is not None and isinstance(component, q.Strategy):
                            q._models.bind_clock(component)
                        if kind == "add_strategy" and (self.publisher.invocation.get("entry", "").endswith(".py") or self.publisher.invocation.get("entry", "").startswith("module:")):
                            config = component.config
                            signature = inspect.signature(type(config))
                            values = {key: getattr(config, key) for key, parameter in signature.parameters.items()
                                      if key != "self" and parameter.kind not in {parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD}}
                            def class_path(cls):
                                module = cls.__module__
                                if module == "__main__":
                                    entry = self.publisher.invocation["entry"]
                                    module = entry.removeprefix("module:") if entry.startswith("module:") else Path(entry).with_suffix("").as_posix().replace("/", ".")
                                return module + ":" + cls.__name__
                            native = {"strategy_path": class_path(type(component)),
                                      "config_path": class_path(type(config)),
                                      "config": json.loads(json.dumps(values, default=str))}
                            self.components.setdefault(id(engine), []).append(native)
                        return result
                    finally:
                        self.active_engine = previous
                return add

            self._patch(BacktestEngine, name, wrap_add(original, name))
        for name, path_field in (("add_strategy_from_config", "strategy_path"), ("add_actor_from_config", "actor_path"), ("add_exec_algorithm_from_config", "exec_algorithm_path")):
            original = getattr(BacktestEngine, name)

            def wrap_config(original, path_field):
                @functools.wraps(original)
                def add(engine, config, *args, **kwargs):
                    module, separator, class_name = getattr(config, path_field).partition(":")
                    if separator:
                        component = getattr(importlib.import_module(module), class_name)
                        self._callbacks(component)
                    previous = self.active_engine; self.active_engine = engine
                    try:
                        result = original(engine, config, *args, **kwargs)
                        if path_field == "strategy_path":
                            self.components.setdefault(id(engine), []).append({"strategy_path": config.strategy_path,
                                "config_path": config.config_path, "config": json.loads(json.dumps(config.config, default=str))})
                        return result
                    finally:
                        self.active_engine = previous
                return add

            self._patch(BacktestEngine, name, wrap_config(original, path_field))
        original_add_data = BacktestEngine.add_data

        @functools.wraps(original_add_data)
        def add_data(engine, data, *args, **kwargs):
            from native.strategy_equity import record_marks
            record_marks(engine, data)
            return original_add_data(engine, data, *args, **kwargs)

        self._patch(BacktestEngine, "add_data", add_data)
        for name in ("run", "end", "reset", "dispose", "clear_strategies", "clear_actors"):
            original = getattr(BacktestEngine, name)

            def wrap_run(original, operation):
                @functools.wraps(original)
                def run(engine, *args, **kwargs):
                    from native.strategy_equity import attach, release
                    if operation == "run":
                        attach(engine)
                    previous = self.active_engine; self.active_engine = engine
                    try:
                        result = original(engine, *args, **kwargs)
                        if operation == "run":
                            self.executed_components[id(engine)] = list(self.components.get(id(engine), []))
                            self.publisher.index["deploymentComponents"] = [item for components in self.executed_components.values() for item in components]
                            self.publisher.write()
                        elif operation == "clear_strategies":
                            self.components[id(engine)] = []
                        elif operation == "clear_actors":
                            release(engine)
                        return result
                    finally:
                        if operation == "dispose":
                            release(engine)
                        self.active_engine = previous
                        affected = [row for row in self.errors if row["engineIdentity"] == id(engine) and row["nativeRunId"] is None]
                        if affected:
                            try:
                                run_id = str(engine.get_result().run_id)
                                for row in affected:
                                    row["nativeRunId"] = run_id
                            except Exception:
                                pass
                return run

            self._patch(BacktestEngine, name, wrap_run(original, name))

    def publish(self):
        if not self.errors or self.published:
            return
        self.published = True
        rows = [{key: value for key, value in row.items() if key != "engineIdentity"} for row in self.errors]
        for unit in self.publisher.index["units"]:
            run_id = str(unit.get("nativeStatistics", {}).get("run_id"))
            failures = [row for row in rows if row.get("nativeRunId") == run_id]
            if failures and unit.get("nativeKind") == "nautilus_trader.backtest.BacktestEngine":
                unit.update(state="partial", callbackErrors=failures, error=failures[0]["message"])
        path = self.publisher.root / "python/native-callback-failures.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=2), encoding="utf8")
        artifact = self.publisher.artifact(path, "Original Python callback failures", "file")
        self.publisher.section("native-callback-failures", "Original Python callback failures", [artifact])
        self.publisher.index["units"].append({"id": "native-callback-failures", "title": "Original Python callback failures", "state": "failed", "callbackErrors": rows})
        self.publisher.write()

    def check(self):
        try:
            self.publish()
        finally:
            if self.first_error is not None:
                raise self.first_error.with_traceback(self.first_error.__traceback__)

    def close(self):
        for target, name, existed, original in reversed(self.restores):
            if existed:
                setattr(target, name, original)
            else:
                delattr(target, name)
        self.restores.clear()
        self.publish()
