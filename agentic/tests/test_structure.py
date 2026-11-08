import importlib.util
from pathlib import Path
import yaml

ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('agentic_suite',ROOT/'suite.py')
suite=importlib.util.module_from_spec(spec)
import sys
sys.modules['agentic_suite']=suite
spec.loader.exec_module(suite)


def test_five_distinct_tasks_have_prompts_and_causes():
    assert len(suite.SCENARIOS)==5
    assert all(suite.EXPECTED_CAUSES[t] for t in suite.SCENARIOS)
    for task in suite.SCENARIOS:
        text=(ROOT/'prompts'/f'{task}.md').read_text()
        assert 'diagnosis.json' in text
        assert 'kubectl' in text
        assert len(text)>500


def test_all_kubernetes_manifests_are_well_formed():
    for path in (ROOT/'manifests').glob('*.yaml'):
        records=list(yaml.safe_load_all(path.read_text()))
        assert records
        for doc in records:
            assert doc['apiVersion'] and doc['kind'] and doc['metadata']['name']
            if doc['kind']!='Namespace': assert doc['metadata']['namespace']


def test_guard_is_exact_context(monkeypatch):
    class P:
        stdout='production-context\\n'
    monkeypatch.setattr(suite,'k',lambda *a,**kw:P())
    import pytest
    with pytest.raises(SystemExit):suite.allowed_context()


def test_reference_causes_not_in_model_prompts_verbatim():
    for task in suite.SCENARIOS:
        text=(ROOT/'prompts'/f'{task}.md').read_text()
        assert not set(suite.EXPECTED_CAUSES[task]).issubset(set(text.split()))


def test_diagnosis_extra_resources_cannot_pass(tmp_path,monkeypatch):
    import json
    t='t03'
    (tmp_path/'diagnosis.json').write_text(json.dumps({'root_causes':list(suite.EXPECTED_CAUSES[t])+['Service/default/irrelevant']}))
    monkeypatch.setattr(suite,'allowed_context',lambda:None)
    monkeypatch.setattr(suite,'snapshot',lambda *args:None)
    monkeypatch.setattr(suite,'task_checks',lambda _: [suite.Check('A','test',True),suite.Check('B','test',True)])
    monkeypatch.setattr(suite.hardened_v4, 'checks', lambda *args, **kwargs: [])
    assert suite.grade(t,tmp_path) is False
    assert json.loads((tmp_path/'grade.json').read_text())['total'] < 100


def test_correct_diagnosis_requires_working_live_outcome(tmp_path,monkeypatch):
    import json
    t='t03'
    (tmp_path/'diagnosis.json').write_text(json.dumps({'root_causes':sorted(suite.EXPECTED_CAUSES[t])}))
    monkeypatch.setattr(suite,'allowed_context',lambda:None)
    monkeypatch.setattr(suite,'snapshot',lambda *args:None)
    monkeypatch.setattr(suite,'task_checks',lambda _: [suite.Check('A','broken',False),suite.Check('B','security intact',True)])
    monkeypatch.setattr(suite.hardened_v4, 'checks', lambda *args, **kwargs: [])
    assert suite.grade(t,tmp_path) is False
    assert json.loads((tmp_path/'grade.json').read_text())['scores']['C']==30
