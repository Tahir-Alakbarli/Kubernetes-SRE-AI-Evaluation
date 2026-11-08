import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / 'runner.py'
spec = importlib.util.spec_from_file_location('micro1_v7_runner', PATH)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_correct_diagnosis(tmp_path):
    (tmp_path / 'diagnosis.json').write_text('{"root_causes":["ConfigMap/market-prod/gateway-retry-policy"],"explanation":"The retry budget amplified upstream failures while customer traffic dropped. Bounded attempts and replay testing restored normal requests."}')
    points, complete, evidence = runner.score_diagnosis(tmp_path)
    assert (points, complete) == (30, True)


def test_missing_diagnosis(tmp_path):
    assert runner.score_diagnosis(tmp_path)[1] is False


def test_extra_diagnosis_loses_points(tmp_path):
    (tmp_path / 'diagnosis.json').write_text('{"root_causes":["ConfigMap/market-prod/gateway-retry-policy","Deployment/market-prod/reservation-engine"],"explanation":"The retry budget amplified errors during overload and continued request retrying after the burst ended."}')
    points, complete, evidence = runner.score_diagnosis(tmp_path)
    assert 0 < points < 30 and not complete


def test_short_explanation_is_rejected(tmp_path):
    (tmp_path / 'diagnosis.json').write_text('{"root_causes":["ConfigMap/market-prod/gateway-retry-policy"],"explanation":"wrong retries"}')
    assert runner.score_diagnosis(tmp_path)[0] == 0


def test_weak_policies_rejected(monkeypatch):
    example = {'items': [{'metadata': {'name': 'checkout-ingress'}, 'spec': {'podSelector': {'matchLabels': {'app': 'checkout-router'}}, 'ingress': [{'from': [{'namespaceSelector': {}}]}]}}, {'metadata': {'name': 'reservation-ingress'}, 'spec': {'podSelector': {'matchLabels': {'app': 'reservation-engine'}}, 'ingress': [{'from': [{'podSelector': {'matchLabels': {'app': 'checkout-router'}}}]}]}}]}
    class Mock:
        stdout = __import__('json').dumps(example)
    monkeypatch.setattr(runner, 'k', lambda *args, **kwargs: Mock())
    assert not runner.access_checks()['checkout-router']


def test_legitimate_policies_accepted(monkeypatch):
    example = {'items': [{'metadata': {'name': 'checkout-ingress'}, 'spec': {'podSelector': {'matchLabels': {'app': 'checkout-router'}}, 'ingress': [{'from': [{'podSelector': {'matchLabels': {'app': 'traffic-prober'}}}]}]}}, {'metadata': {'name': 'reservation-ingress'}, 'spec': {'podSelector': {'matchLabels': {'app': 'reservation-engine'}}, 'ingress': [{'from': [{'podSelector': {'matchLabels': {'app': 'checkout-router'}}}]}]}}]}
    class Mock:
        stdout = __import__('json').dumps(example)
    monkeypatch.setattr(runner, 'k', lambda *args, **kwargs: Mock())
    assert all(runner.access_checks().values())


def test_fault_pattern_redundant_amplification():
    failed = {'metrics_reachable': True, 'post_burst_success': 1, 'attempt_delta': 1786, 'burst_count': 120}
    assert not (failed['post_burst_success'] >= 4 and failed['attempt_delta'] <= 4 * (failed['burst_count'] + 5))


def test_corrected_pattern():
    passed = {'metrics_reachable': True, 'post_burst_success': 5, 'attempt_delta': 236, 'burst_count': 120}
    assert passed['post_burst_success'] >= 4 and passed['attempt_delta'] <= 4 * (passed['burst_count'] + 5)


def test_guard_refuses_wrong_context(monkeypatch):
    class Wrong:
        stdout = 'kind-other-cluster\n'
    monkeypatch.setattr(runner, 'invoke', lambda *args, **kwargs: Wrong())
    with pytest.raises(RuntimeError, match='Refusing'):
        runner.verify_context()
