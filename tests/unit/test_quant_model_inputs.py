"""Models resolve by kind, bind immutable versions and reject training overlap."""
import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from talyx_sdk import ContractError

from modules.quant.backend import model_inputs


@pytest.mark.parametrize('source', ['q.model(name)', 'q.model("x", version=2)', 'q.model(1)'])
def test_model_declarations_need_one_literal_name(source):
    with pytest.raises(ContractError, match='literal'):
        model_inputs.declarations(source)


def test_binding_keeps_frozen_artifact_and_exact_training_boundary(tmp_path, monkeypatch):
    root = tmp_path / 'version'
    (root / 'report').mkdir(parents=True)
    (root / 'source').mkdir()
    artifact = root / 'report' / 'state.bin'
    artifact.write_bytes(b'fitted weights')
    checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
    spec = {'format': 'talyx.model.v1', 'trainingCutoff': 2000, 'loader': 'inference:load', 'predictor': 'inference:predict',
            'artifacts': [{'name': 'state', 'path': 'state.bin', 'sha256': checksum}]}
    meta = {'common': None, 'environment': {'preparationId': 'env'}, 'code': {'digest': 'code'}, 'lock': {'digest': 'lock'}}
    resolved = []

    def resolve(name, version=None, kind=None):
        resolved.append((name, kind))
        return {'versionId': 'v1'}

    library = SimpleNamespace(resolve=resolve,
        read_version=lambda _: {'versionId': 'v1', 'version': 1, 'state': 'fixed', 'kind': 'model', 'model': spec},
        version_directory=lambda _: root, store=SimpleNamespace(version=lambda _: {'meta': meta}))
    from tests.unit.quant_public import library_actions
    read = library_actions(monkeypatch, library=library)
    assert asyncio.run(read("read_library_model", {"name":"Forecast"}))["trainingCutoff"] == 2000
    resolved.clear()
    run = tmp_path / 'run'
    (run / 'source').mkdir(parents=True)
    (run / 'source' / 'strategy.py').write_text('first=q.model("Forecast")\nsecond=q.model("Forecast")')
    with pytest.raises(ContractError) as error:
        asyncio.run(model_inputs.prepare(library, run, 1999))
    assert error.value.code == 'model_training_overlap'
    assert error.value.details['trainingCutoff'] == 2000
    result = asyncio.run(model_inputs.prepare(library, run, 2000))
    assert len(result) == 1 and result[0]['versionId'] == 'v1'
    assert resolved == [('Forecast', 'model'), ('Forecast', 'model')]
    artifact.write_bytes(b'changed')
    with pytest.raises(ContractError, match='artifact changed'):
        asyncio.run(model_inputs.prepare(library, run, 2000, frozen=result))
