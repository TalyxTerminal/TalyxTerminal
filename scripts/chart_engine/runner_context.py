"""
TALYX patch: per-runner isolation of PyneCore's process-wide run state.

PyneCore keeps the state of a run in module globals: ``pynecore.lib`` and its
submodules (prices, ``bar_index``, ``barstate``, ``syminfo``, plot data, drawing
registries, the scheduled-grid trackers, ...) and the root state vectors of
:mod:`~pynecore.core.instance_state`. A :class:`ScriptRunner` assumes it is the
only run in the process: starting a second one resets the first one's function
instances (``instance_state.reset()`` clears every root's children), and every
bar overwrites the module globals the first one reads.

This module gives every runner a context of its own and installs it whenever
that runner's code executes:

- one engine lock serializes runners (a runner holds it while it computes and
  drops it while it waits on its feed or hands a result to its consumer);
- a switch saves the module values of the context that ran last and installs
  the next one's. Scalars are rebound in the module dicts; containers that
  other objects alias (drawing registries, overload caches) are swapped in
  place, so their identity never changes;
- a new context starts from the values the modules had when this module first
  scanned them, before any run: the state of a fresh process.

Consecutive steps of the same runner do not switch at all.
"""
from __future__ import annotations

import gc
import importlib
import pkgutil
import sys
import threading
import types
from collections import OrderedDict, defaultdict, deque
from operator import itemgetter

__all__ = ['Context', 'acquire', 'release', 'inside', 'outside', 'guard', 'feed', 'BASE']

_MISSING = object()

# Run state outside the ``pynecore.lib`` package, by module: scalar keys.
# ``_root_vectors`` is the registry of the root state vectors; the drawing id
# counter numbers a run's drawings.
_EXTRA_KEYS = {
    'pynecore.core.instance_state': ('_root_vectors',),
    'pynecore.types.base': ('_vid_counter',),
}

_SKIP_TYPES = (types.ModuleType, types.FunctionType, types.BuiltinFunctionType, type,
               types.MethodType, staticmethod, classmethod, property)
_CONTAINER_TYPES = (list, dict, set, deque, bytearray, defaultdict, OrderedDict)


def _fresh(value):
    """Copy the container structure of ``value``; anything else is shared."""
    t = type(value)
    if t is list:
        return [_fresh(v) for v in value]
    if t is dict:
        return {k: _fresh(v) for k, v in value.items()}
    if t is set:
        return set(value)
    if t is deque:
        return deque((_fresh(v) for v in value), value.maxlen)
    if t is defaultdict:
        return defaultdict(value.default_factory, {k: _fresh(v) for k, v in value.items()})
    if t is OrderedDict:
        return OrderedDict((k, _fresh(v)) for k, v in value.items())
    if t is bytearray:
        return bytearray(value)
    return value


def _snap(c):
    t = type(c)
    if t is list or t is bytearray:
        return c[:]
    if t is deque:
        return list(c)
    return c.copy()  # dict, set, defaultdict, OrderedDict


def _put(c, snap):
    t = type(c)
    if t is list or t is bytearray:
        c[:] = snap
    elif t is deque:
        c.clear()
        c.extend(snap)
    else:
        c.clear()
        c.update(snap)


class _Target:
    __slots__ = ('dict', 'keys', 'skip', 'getter', 'size')

    def __init__(self, d: dict, keys: tuple[str, ...], skip: frozenset[str]):
        self.dict = d
        self.keys = keys
        self.skip = skip  # names of containers swapped in place, never rebound
        self.getter = (itemgetter(*keys) if len(keys) > 1 else (lambda m, k=keys[0]: (m[k],)) if keys
                       else (lambda m: ()))
        self.size = len(d)


# Layout, built at the first switch and extended when modules gain keys or a
# script registers a new overload cache.
_targets: list[_Target] = []
_pristine_values: list[tuple] = []
_contents: list = []            # containers swapped in place
_pristine_contents: list = []
_shared_caches: list | None = None  # instance_state._shared_caches (closure-held dicts)
_shared_seen = 0
_scanned = False
_grown = 0  # keys found after the scan (diagnostics)


class Context:
    """The run state of one runner (or of the code outside every runner)."""
    __slots__ = ('values', 'contents', 'missing', '__weakref__')

    def __init__(self):
        self.values: list | None = None     # per target: tuple of values
        self.contents: list | None = None   # per content container: snapshot
        self.missing: set[int] = set()      # targets holding _MISSING values


BASE = Context()
_current: Context = BASE
_lock = threading.RLock()
_stack: list[Context] = []


def _state_like(value) -> bool:
    return not isinstance(value, _SKIP_TYPES) and (isinstance(value, _CONTAINER_TYPES) or not callable(value))


def _scan() -> None:
    """Build the layout: every run-state key of ``pynecore.lib`` (all of its
    submodules are imported first, so the layout is complete) and the extras."""
    global _scanned, _shared_caches, _shared_seen
    import pynecore.lib as lib
    for info in pkgutil.walk_packages(lib.__path__, 'pynecore.lib.'):
        try:
            importlib.import_module(info.name)
        except Exception:  # an optional submodule that cannot load is not run state
            pass
    modules = [m for n, m in sorted(sys.modules.items())
               if m is not None and (n == 'pynecore.lib' or n.startswith('pynecore.lib.'))]
    candidates: list[tuple[dict, list[str]]] = []
    for m in modules:
        d = vars(m)
        keys = [k for k, v in d.items() if not (k.startswith('__') and k.endswith('__')) and _state_like(v)]
        candidates.append((d, keys))
    from . import instance_state
    from ..types import base
    for name, keys in _EXTRA_KEYS.items():
        candidates.append((vars(sys.modules[name]), list(keys)))
    _shared_caches = instance_state._shared_caches
    del base

    # A container is swapped in place when anything besides one module key
    # refers to it (another module's name, a tuple, a closure, an object):
    # rebinding the key would leave that reference on another context's object.
    module_dicts = {id(d) for d, _ in candidates}
    containers = {}
    names = defaultdict(int)
    for d, keys in candidates:
        for k in keys:
            v = d[k]
            if isinstance(v, _CONTAINER_TYPES):
                containers[id(v)] = v
                names[id(v)] += 1
    aliased = {i for i, n in names.items() if n > 1}
    objs = tuple(containers.values())
    for ref in gc.get_referrers(*objs):
        if isinstance(ref, types.FrameType) or ref is containers or ref is objs:
            continue
        if isinstance(ref, dict) and id(ref) in module_dicts:
            continue
        if type(ref) is tuple and len(ref) == len(objs) and all(a is b for a, b in zip(ref, objs)):
            continue  # the argument tuple of this call
        for item in (ref.values() if isinstance(ref, dict) else ref if isinstance(ref, (list, tuple)) else
                     (getattr(ref, 'cell_contents', None),) if isinstance(ref, types.CellType) else ()):
            if id(item) in containers:
                aliased.add(id(item))
    for i in aliased:
        _contents.append(containers[i])
        _pristine_contents.append(_fresh(_snap(containers[i])))
    for d, keys in candidates:
        skip = frozenset(k for k in keys if id(d[k]) in aliased)
        t = _Target(d, tuple(k for k in keys if k not in skip), skip)
        _targets.append(t)
        _pristine_values.append(tuple(_fresh(v) for v in t.getter(d)))
    _scanned = True
    _sync_shared()


def _sync_shared() -> None:
    """Swap the overload / method caches a script registered since the last look."""
    global _shared_seen
    while _shared_seen < len(_shared_caches):
        c = _shared_caches[_shared_seen]
        _shared_seen += 1
        _contents.append(c)
        _pristine_contents.append({})


def _grow(target_index: int) -> None:
    """A module gained keys (a runner set an attribute the module did not
    define): track them; every other context has them absent."""
    t = _targets[target_index]
    d = t.dict
    new = tuple(k for k, v in d.items() if k not in t.keys and k not in t.skip
                and not (k.startswith('__') and k.endswith('__')) and _state_like(v))
    t.size = len(d)
    if not new:
        return
    global _grown
    _grown += len(new)
    old_len = len(t.keys)
    _targets[target_index] = _Target(d, t.keys + new, t.skip)
    _pristine_values[target_index] = _pristine_values[target_index] + (_MISSING,) * len(new)
    for ctx in _contexts():
        if ctx is not _current and ctx.values is not None and len(ctx.values[target_index]) == old_len:
            ctx.values[target_index] = ctx.values[target_index] + (_MISSING,) * len(new)
            ctx.missing.add(target_index)


import weakref  # noqa: E402

_live = weakref.WeakSet()


def _contexts():
    yield BASE
    yield from list(_live)


def _save(ctx: Context) -> None:
    values = []
    missing = set()
    for i, t in enumerate(_targets):
        if len(t.dict) != t.size:
            _grow(i)
            t = _targets[i]
        try:
            values.append(t.getter(t.dict))
        except KeyError:
            d = t.dict
            values.append(tuple(d.get(k, _MISSING) for k in t.keys))
            missing.add(i)
    ctx.values = values
    ctx.missing = missing
    ctx.contents = [_snap(c) for c in _contents]


def _install(ctx: Context) -> None:
    if ctx.values is None:  # a new context: a fresh process's state
        ctx.values = [tuple(_fresh(v) for v in vals) for vals in _pristine_values]
        ctx.missing = {i for i, vals in enumerate(_pristine_values) if any(v is _MISSING for v in vals)}
        ctx.contents = [_fresh(s) for s in _pristine_contents]
    else:
        # Layout grown since this context was saved
        for i in range(len(ctx.values), len(_targets)):
            ctx.values.append(tuple(_fresh(v) for v in _pristine_values[i]))
        for i in range(len(ctx.contents), len(_contents)):
            ctx.contents.append(_fresh(_pristine_contents[i]))
    for i, (t, vals) in enumerate(zip(_targets, ctx.values)):
        d = t.dict
        if i in ctx.missing:
            for k, v in zip(t.keys, vals):
                if v is _MISSING:
                    d.pop(k, None)
                else:
                    d[k] = v
        else:
            d.update(zip(t.keys, vals))
        t.size = len(d)
    for c, s in zip(_contents, ctx.contents):
        _put(c, s)


def _switch(ctx: Context) -> None:
    global _current
    if not _scanned:
        _scan()
    elif _shared_seen != len(_shared_caches):
        _sync_shared()
    _save(_current)
    _install(ctx)
    _current = ctx


def acquire(ctx: Context) -> None:
    """Take the engine lock and install ``ctx`` (no switch if it ran last)."""
    _lock.acquire()
    try:
        _stack.append(_current)
        if ctx is not _current:
            if ctx is not BASE:
                _live.add(ctx)
            _switch(ctx)
    except BaseException:
        _stack.pop()
        _lock.release()
        raise


def release() -> None:
    """Drop one level of the engine lock; a nested entry gets its outer context back."""
    try:
        outer = _stack.pop()
        if _stack and outer is not _current:
            _switch(outer)
    finally:
        _lock.release()


class inside:
    """Read or change a runner's state from outside its steps (its last plot
    values, its drawings): ``with inside(runner._talyx_context): ...``."""

    def __init__(self, ctx: Context):
        self.ctx = ctx

    def __enter__(self):
        acquire(self.ctx)

    def __exit__(self, *exc):
        release()


class outside:
    """Run code that is not a runner's (an import) in the base context, unless
    this thread is already inside a runner."""

    def __enter__(self):
        self.entered = not _lock._is_owned()
        if self.entered:
            acquire(BASE)

    def __exit__(self, *exc):
        if self.entered:
            release()


def feed(iterable, ctx: Context):
    """The runner's view of its feed: the lock is dropped while the feed waits."""
    it = iter(iterable)
    while True:
        release()
        try:
            item = next(it)
        except StopIteration:
            return
        finally:
            acquire(ctx)
        yield item


def guard(gen, ctx: Context):
    """The consumer's view of a runner's results: each step runs in ``ctx``."""
    try:
        while True:
            acquire(ctx)
            try:
                item = next(gen)
            except StopIteration:
                return
            finally:
                release()
            yield item
    finally:
        acquire(ctx)
        try:
            gen.close()
        finally:
            release()
