"""Literal model dependencies and immutable, verified library model bindings."""
from __future__ import annotations

import ast
import asyncio
import hashlib
from pathlib import Path

from talyx_sdk import ContractError

from .work_records import digest


def declarations(source):
    names = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        model = isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name) and function.value.id in {'q', 'talyx_quant'} and function.attr == 'model'
        model = model or isinstance(function, ast.Name) and function.id == 'model'
        if not model:
            continue
        if len(node.args) != 1 or node.keywords or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str) or not node.args[0].value.strip():
            raise ContractError('invalid_input', f'line {node.lineno}: q.model needs one literal library model name.')
        names.add(node.args[0].value)
    return sorted(names)


def source_names(directory):
    names = set()
    for part in ('source', 'common'):
        root = Path(directory) / part
        for path in root.rglob('*.py'):
            names.update(declarations(path.read_text(encoding='utf-8')))
    return sorted(names)


def binding(library, name, *, version_id=None):
    version = library.read_version(library.resolve(name, kind='model')['versionId'] if version_id is None else version_id)
    if version['state'] == 'delisted' or version['kind'] != 'model':
        raise ContractError('not_found', f'{name} is not an available trained model.')
    spec = version['model']
    if spec is None or spec.get('format') != 'talyx.model.v1' or type(spec.get('trainingCutoff')) is not int:
        raise ContractError('model_not_callable', f"{name} v{version['version']} has no frozen inference entry or training cutoff.")
    directory = library.version_directory(version['versionId']).resolve()
    artifacts = []
    for item in spec['artifacts']:
        path = (directory / 'report' / item['path']).resolve()
        if not path.is_relative_to(directory / 'report'):
            raise ContractError('invalid_input', 'The model artifact escapes its frozen version.')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != item['sha256']:
            raise ContractError('conflict', f"The frozen model artifact changed: {item['name']}.")
        artifacts.append({'name': item['name'], 'path': str(path), 'sha256': actual})
    row = library.store.version(version['versionId'])
    meta = row['meta']
    model = {'source': str(directory / 'source'), 'common': str(directory / 'common') if meta['common'] is not None else None,
             'artifacts': artifacts, 'loader': spec['loader'], 'predictor': spec['predictor']}
    return {'name': name, 'versionId': version['versionId'], 'version': version['version'], 'trainingCutoff': spec['trainingCutoff'], 'model': model,
            'environment': meta['environment'], 'digest': digest({'code': meta['code']['digest'], 'common': meta['common'], 'model': spec, 'lock': meta['lock']})}


async def prepare(library, directory, requested_start, *, frozen=None):
    names = source_names(directory)
    by_name = None if frozen is None else {item['name']: item['versionId'] for item in frozen}
    result = []
    for name in names:
        item = await asyncio.to_thread(binding, library, name, version_id=None if by_name is None else by_name[name])
        if requested_start < item['trainingCutoff']:
            raise ContractError('model_training_overlap', f"{name} v{item['version']} was trained through {item['trainingCutoff']} UTC milliseconds; start the backtest at or after that cutoff.",
                {'name': name, 'versionId': item['versionId'], 'trainingCutoff': item['trainingCutoff'], 'requestedStart': requested_start})
        result.append(item)
    return result


def public_bindings(bindings):
    return [{key: item[key] for key in ('name', 'versionId', 'trainingCutoff')} for item in bindings]
