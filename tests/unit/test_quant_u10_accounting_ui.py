"""Notebook outputs never depend on presentation errors."""
import json

from modules.quant.backend.notebook_file import new_cell, parse, serialize


def test_notebook_sources_use_lf_without_changing_output_text():
    model = {'cells': [new_cell('code', 'one\r\ntwo\rthree\n')], 'metadata': {}}
    model['cells'][0]['outputs'] = [{'output_type': 'stream', 'name': 'stdout', 'text': 'progress\rnext\r\n'}]
    saved = serialize(model)
    source = json.loads(saved)['cells'][0]['source']
    assert source == ['one\n', 'two\n', 'three\n']
    read = parse(saved)['cells'][0]
    assert read['source'] == 'one\ntwo\nthree\n'
    assert read['outputs'][0]['text'] == 'progress\rnext\r\n'


def test_running_a_cell_declares_that_it_runs_code():
    """An AI client decides from the declared effects (the platform has no read-only tier): running a cell says it runs code."""
    action = json.loads(open('modules/quant/public/contract.json', encoding='utf-8').read())['actions']['run_cell']
    assert 'code' in action['effects']
