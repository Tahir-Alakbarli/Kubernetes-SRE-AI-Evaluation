import importlib.util
import json
from pathlib import Path
import sys
import tokenize
import yaml

DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))
import run


def test_five_tasks_and_expected_sets():
    assert len(run.TASKS) == 5
    assert all(run.EXPECTED[k] for k in run.TASKS)
    assert len(run.EXPECTED['t01']) > len(run.suite.EXPECTED_CAUSES['t01'])
    assert len(run.EXPECTED['t02']) > len(run.suite.EXPECTED_CAUSES['t02'])
    assert len(run.EXPECTED['t03']) > len(run.suite.EXPECTED_CAUSES['t03'])


def test_reference_documentation_is_not_in_agent_workspace(tmp_path):
    p = run.create_trial(tmp_path / 'trial', 't01')
    assert (p / 'ticket.md').exists()
    assert (p / 'README.md').exists()
    assert (p / 'runbooks' / 'operational-policy.md').exists()
    assert not (p / 'run.py').exists()
    assert not (p / 'expected.json').exists()
    assert not (p / 'reference.yaml').exists()


def test_initial_ticket_is_short_and_does_not_reveal_faults():
    for tid in run.TASKS:
        text = (DIRECTORY / 'tickets' / f'{tid}.md').read_text()
        assert len(text.split()) < 130
        assert 'root cause:' not in text.lower()
        assert 'kubectl patch' not in text
        assert 'webhook' not in text.lower()
        assert 'mutatingadmissionpolicy' not in text.lower()
        assert 'maxUnavailable' not in text


def test_yaml_fixtures_parse():
    for path in (DIRECTORY / 'fixtures').glob('*.yaml'):
        items = list(yaml.safe_load_all(path.read_text()))
        assert items
        for item in items:
            assert item['kind'] and item['metadata']['name']


def test_admission_policy_is_scoped_and_bounded():
    docs = list(yaml.safe_load_all((DIRECTORY / 'fixtures' / 'mutation.yaml').read_text()))
    policy = docs[0]
    binding = docs[1]
    assert policy['kind'] == 'MutatingAdmissionPolicy'
    assert binding['kind'] == 'MutatingAdmissionPolicyBinding'
    assert policy['spec']['matchConstraints']['namespaceSelector']['matchLabels'] == {'eval-admission': 'active'}
    assert '16Mi' in policy['spec']['mutations'][0]['jsonPatch']['expression']
    assert binding['spec']['policyName'] == policy['metadata']['name']


def test_both_explanation_forms_accepted():
    roots = sorted(run.EXPECTED['t02'])
    one = run.diagnosis({'root_causes': roots, 'explanation': 'Evidence from events and ReplicaSets confirmed the causes. Changes restored both services and effective RBAC permissions. Verification was performed against the live cluster.'}, run.EXPECTED['t02'])
    other = run.diagnosis({'root_causes': roots, 'explanation': {'evidence': ['ReplicaSet status identified admission blocking and stale release configuration.'], 'changes': ['Patched authorized resources and completed the affected deployments.'], 'verification': ['Ready replicas and ServiceAccount access were checked using Kubernetes queries.']}}, run.EXPECTED['t02'])
    assert one['complete'] and other['complete']
    assert one['score'] == 30 and other['score'] == 30


def test_missing_explanation_cannot_earn_100():
    roots = sorted(run.EXPECTED['t03'])
    result = run.diagnosis({'root_causes': roots}, run.EXPECTED['t03'])
    assert not result['complete']
    assert result['score'] < 30


def test_missing_and_extra_causes_penalized():
    roots = sorted(run.EXPECTED['t01'])
    omitted = run.diagnosis({'root_causes': roots[:-1], 'explanation': 'A detailed, evidence-based explanation with repairs and validation.'}, run.EXPECTED['t01'])
    inserted = run.diagnosis({'root_causes': roots + ['Pod/kube-system/coredns'], 'explanation': 'A detailed, evidence-based explanation with repairs and validation.'}, run.EXPECTED['t01'])
    assert omitted['score'] < 30
    assert inserted['score'] < 30


def test_duplicate_causes_rejected():
    roots = sorted(run.EXPECTED['t04'])
    result = run.diagnosis({'root_causes': roots * 2, 'explanation': 'A detailed, evidence-based explanation with repairs and validation.'}, run.EXPECTED['t04'])
    assert not result['complete']


def test_memory_limit_accepts_valid_alternative_quantities():
    assert run.memory_mi('64Mi') == 64
    assert run.memory_mi('100Mi') == 100
    assert run.memory_mi('1Gi') == 1024
    assert run.memory_mi('invalid') == -1


def test_reference_uses_both_component_recoveries(monkeypatch):
    calls = []
    monkeypatch.setattr(run, 'guard', lambda: None)
    monkeypatch.setattr(run.suite, 'oracle', lambda name: calls.append(name))
    run.reference('t01')
    assert calls == ['t01', 't05']


def test_rollout_strategy_rejects_unsafe_values(monkeypatch):
    monkeypatch.setattr(run.suite, 'obj', lambda *args: {'spec': {'strategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxUnavailable': '100%', 'maxSurge': 0}}}})
    assert not run.rollout_strategy_safe()


def test_rollout_strategy_accepts_safe_values(monkeypatch):
    monkeypatch.setattr(run.suite, 'obj', lambda *args: {'spec': {'strategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxUnavailable': 0, 'maxSurge': 1}}}})
    assert run.rollout_strategy_safe()


def test_code_has_no_comments():
    code_files = [DIRECTORY / 'run.py', DIRECTORY / 'tests' / 'test_v6.py']
    for filename in code_files:
        with filename.open('rb') as source:
            assert all(token.type != tokenize.COMMENT for token in tokenize.tokenize(source.readline))


def test_release_consistency_hardening_checks_the_new_version(monkeypatch):
    calls = []
    monkeypatch.setattr(run.suite, 'task_checks', lambda task: [])
    monkeypatch.setattr(run.hardened_v4, 'checks', lambda suite, task, rounds: calls.append(task) or [])
    monkeypatch.setattr(run.hardened_v4, 'probe_operator', lambda *args: (True, {}))
    run.collect_checks('t02', destructive=False)
    assert calls == ['t02']
