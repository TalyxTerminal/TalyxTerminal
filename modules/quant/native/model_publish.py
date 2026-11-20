"""Immutable callable models, with a UTC exclusive training boundary."""
from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath


def cutoff_ms(value):
    """Accept UTC milliseconds or an aware instant; round up to an exclusive millisecond."""
    if type(value) is int:
        result = value
    else:
        point = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(point, datetime) or point.tzinfo is None or point.utcoffset() is None:
            raise ValueError('training_cutoff needs UTC milliseconds or a timezone-aware instant.')
        elapsed = point.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
        result = elapsed.days * 86_400_000 + elapsed.seconds * 1000 + (elapsed.microseconds + 999) // 1000
    if result < 0:
        raise ValueError('training_cutoff must not precede the Unix epoch.')
    return result


def _relative(value):
    path = PurePosixPath(value)
    if not value or '\\' in value or ':' in value or path.is_absolute() or any(part in {'.', '..'} for part in value.split('/')):
        raise ValueError('A model artifact name must be a relative name inside its bundle.')
    return path


def _entry(value, publisher):
    roots = [Path(publisher.invocation[key]).resolve() for key in ('source', 'common') if publisher.invocation[key] is not None]
    if callable(value):
        if not inspect.isfunction(value) or value.__qualname__ != value.__name__:
            raise ValueError('Model loader and predictor must be top-level functions in frozen code.')
        file = Path(inspect.getsourcefile(value)).resolve()
        root = next((root for root in roots if file.is_relative_to(root)), None)
        if root is None:
            raise ValueError('Model entries must belong to the run source or frozen common code.')
        parts = file.relative_to(root).with_suffix('').parts
        module = '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)
        result = module + ':' + value.__name__
    elif isinstance(value, str):
        result = value
    else:
        raise TypeError('Model loader and predictor need a function or module:function entry.')
    module, separator, function = result.partition(':')
    if not separator or not function.isidentifier() or not module or not all(part.isidentifier() for part in module.split('.')):
        raise ValueError('A model entry must be module:function.')
    local = Path(*module.split('.'))
    if not any((root / local.with_suffix('.py')).is_file() or (root / local / '__init__.py').is_file() for root in roots):
        raise ValueError(f'The model entry {result} is not in the frozen source or common code.')
    return result


def _snapshot(publisher, name, artifacts, loader, predictor, training_cutoff, *, engine):
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError('Choose a model name of 1 to 120 characters.')
    boundary = cutoff_ms(training_cutoff)
    if not isinstance(artifacts, Mapping) or not artifacts:
        raise ValueError('A model needs a nonempty mapping of artifact names to files or directories.')
    root = publisher.root.resolve()
    destination = root / 'model-bundles' / hashlib.sha256(name.encode()).hexdigest()[:20]
    if destination.exists():
        raise ValueError(f'The run already froze a model named {name}.')
    captured = []
    with tempfile.TemporaryDirectory(prefix='.model-', dir=root) as temporary:
        stage = Path(temporary) / 'bundle'
        stage.mkdir()
        for alias, source in artifacts.items():
            _relative(alias)
            source = Path(source).expanduser().resolve(strict=True)
            files = sorted(source.rglob('*')) if source.is_dir() else [source]
            count = 0
            for file in files:
                if not file.is_file():
                    continue
                if not file.resolve().is_relative_to(source if source.is_dir() else source.parent):
                    raise ValueError('A model artifact symlink leaves its selected directory.')
                relative = alias + '/' + file.relative_to(source).as_posix() if source.is_dir() else alias
                target = stage / _relative(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise ValueError('Model artifact names overlap: ' + relative)
                shutil.copyfile(file, target)
                with target.open('rb') as stream:
                    checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
                with file.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
                        raise ValueError('A model artifact changed while being frozen: ' + str(file))
                captured.append({'name': relative, 'path': (destination / relative).relative_to(root).as_posix(), 'sha256': checksum})
                count += 1
            if not count:
                raise ValueError('A model artifact directory contains no files: ' + str(source))
        spec = {'format': 'talyx.model.v1', 'trainingCutoff': boundary, 'engine': engine,
                'loader': loader, 'predictor': predictor, 'artifacts': captured}
        manifest = stage / 'model-manifest.json'
        if manifest.exists():
            raise ValueError('model-manifest.json is reserved for the model bundle.')
        manifest.write_text(json.dumps(spec, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage.replace(destination)
    for item in captured:
        publisher.artifact(root / item['path'], f'{name} · {item["name"]}', 'native', sha256=item['sha256'])
    publisher.artifact(destination / 'model-manifest.json', name + ' · Frozen inference model', 'result', format=spec['format'])
    return spec


def publish(publisher, name, artifacts, loader, predictor, training_cutoff):
    """The implementation behind q.publish_model; the caller captures kind=model, model=spec."""
    return _snapshot(publisher, name, artifacts, _entry(loader, publisher), _entry(predictor, publisher), training_cutoff, engine='python')


def qlib_label_lookahead(task):
    """Use Qlib's expression semantics, including nested forward-looking operators."""
    from qlib.data.expression_parser import parse_expression
    handler = task['dataset']['kwargs']['handler']['kwargs']
    labels = handler['label']
    if isinstance(labels, str):
        labels = [labels]
    if labels and isinstance(labels[0], list):
        labels = labels[0]
    if not labels:
        raise ValueError('The trained Qlib task needs explicit label expressions to determine its cutoff.')
    return max(0, *(parse_expression(label).get_extended_window_size()[1] for label in labels))


def qlib_training_cutoff(task, calendar, available_at, *, label_lookahead):
    """Map inclusive train/validation/processor ends through the retained Market calendar.

    calendar is ordered Qlib bar labels; available_at contains their actual UTC close
    milliseconds in the same order. It must include future observations consumed by labels.
    """
    import pandas as pd
    dates = pd.DatetimeIndex(calendar)
    if not len(dates) or not dates.is_monotonic_increasing or dates.has_duplicates or len(dates) != len(available_at):
        raise ValueError('Model training needs one ordered, unique calendar and a close for every label.')
    if type(label_lookahead) is not int or label_lookahead < 0:
        raise ValueError('The label look-ahead must be a nonnegative number of calendar observations.')
    dataset = task['dataset']['kwargs']
    ends = [(dataset['segments'][key][1], label_lookahead) for key in ('train', 'valid') if key in dataset['segments']]
    def fitted(value):
        if isinstance(value, dict):
            if value.get('fit_end_time') is not None:
                ends.append((value['fit_end_time'], 0))
            for item in value.values():
                fitted(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                fitted(item)
    fitted(dataset['handler'])
    if not ends or any(value is None for value, _ in ends):
        raise ValueError('The trained Qlib task has no bounded training/selection interval.')
    consumed = []
    for value, ahead in ends:
        boundary = pd.Timestamp(value)
        if dates.tz is not None:
            boundary = boundary.tz_localize(dates.tz) if boundary.tzinfo is None else boundary.tz_convert(dates.tz)
        elif boundary.tzinfo is not None:
            boundary = boundary.tz_convert('UTC').tz_localize(None)
        day_end = isinstance(value, str) and len(value) == 10
        index = dates.searchsorted(boundary + pd.Timedelta(days=1), side='left') - 1 if day_end else dates.searchsorted(boundary, side='right') - 1
        index += ahead
        if not 0 <= index < len(dates):
            raise ValueError('The retained calendar does not cover the last observation consumed by model training.')
        consumed.append(cutoff_ms(available_at[index]))
    return max(consumed)


def archive_qlib(publisher, name, recorder, task, *, training_cutoff, providers, qlib_init):
    """Copy the actual fitted Recorder objects and complete fixed providers; never refit."""
    with tempfile.TemporaryDirectory(prefix='.qlib-model-', dir=publisher.root) as temporary:
        temporary = Path(temporary)
        artifacts = {key: Path(recorder.download_artifact(key, dst_path=str(temporary))) for key in ('params.pkl', 'dataset', 'task')}
        settings = {'task': task, 'qlibInit': qlib_init, 'providers': {frequency: 'provider/' + frequency for frequency in providers},
                    'engineVersion': _qlib_version()}
        settings_path = temporary / 'adapter.json'
        settings_path.write_text(json.dumps(settings, allow_nan=False), encoding='utf-8')
        artifacts['adapter'] = settings_path
        if 'talyx' in task:
            for key, value in task['dataset']['kwargs']['handler']['kwargs']['data_loader']['kwargs']['config'].items():
                artifacts['library-' + key] = Path(value)
        artifacts.update(('provider/' + frequency, directory) for frequency, directory in providers.items())
        return _snapshot(publisher, name, artifacts, 'native.model_adapters:load_qlib', 'native.model_adapters:predict_qlib', training_cutoff, engine='qlib')


def _qlib_version():
    from importlib.metadata import version
    return version('pyqlib')
