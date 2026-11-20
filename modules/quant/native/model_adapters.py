"""Load frozen native states once; infer with fitted Qlib/RL processing only."""
from __future__ import annotations

import copy
import json
import pickle
from pathlib import Path, PurePosixPath


def _tree_root(artifacts, prefix):
    selected = [(key.removeprefix(prefix + '/'), Path(value)) for key, value in artifacts.items() if key.startswith(prefix + '/')]
    if not selected:
        raise ValueError('The frozen model has no provider files for ' + prefix)
    roots = {path.parents[len(PurePosixPath(relative).parts) - 1] for relative, path in selected}
    if len(roots) != 1:
        raise ValueError('The frozen provider files do not belong to one retained directory.')
    return roots.pop()


def _settings(artifacts):
    from importlib.metadata import version
    settings = json.loads(Path(artifacts['adapter']).read_text(encoding='utf-8'))
    if settings['engineVersion'] != version('pyqlib'):
        raise ValueError('Use the exact frozen Qlib environment for model inference.')
    return settings


def load_qlib(artifacts):
    import qlib
    settings = _settings(artifacts)
    initialization = copy.deepcopy(settings['qlibInit'])
    providers = {frequency: str(_tree_root(artifacts, prefix)) for frequency, prefix in settings['providers'].items()}
    original = initialization['provider_uri']
    initialization['provider_uri'] = {frequency: providers[frequency] for frequency in original} if isinstance(original, dict) else providers['default']
    initialization.update(expression_cache=None, dataset_cache=None, kernels=1)
    qlib.init(**initialization)
    with Path(artifacts['params.pkl']).open('rb') as stream:
        model = pickle.load(stream)
    with Path(artifacts['dataset']).open('rb') as stream:
        dataset = pickle.load(stream)
    return {'model': model, 'dataset': dataset, 'settings': settings}


def predict_qlib(state, payload):
    """payload is Dataset.config keyword arguments; native pandas output uses split JSON."""
    from qlib.data.dataset.handler import DataHandlerLP
    if 'talyx' in state['settings']['task']:
        import pandas as pd
        frame = pd.DataFrame(**payload)
        frame = frame[state['settings']['task']['talyx']['libraryFeatures']]
        frame.columns = pd.MultiIndex.from_product([['feature'], frame.columns])
        handler = state['dataset'].handler
        for processor in [*handler.shared_processors, *handler.infer_processors]:
            frame = processor(frame)
        return state['model'].model.predict(frame['feature'].values).tolist()
    dataset = state['dataset']
    dataset.config(**payload)
    dataset.setup_data(handler_kwargs={'init_type': DataHandlerLP.IT_LS})
    value = state['model'].predict(dataset)
    return json.loads(value.to_json(orient='split', date_format='iso'))
