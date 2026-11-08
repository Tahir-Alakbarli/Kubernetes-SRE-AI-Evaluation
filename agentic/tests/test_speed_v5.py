import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('speed_suite_v5', ROOT / 'suite.py')
SUITE = importlib.util.module_from_spec(SPEC)
sys.modules['speed_suite_v5'] = SUITE
SPEC.loader.exec_module(SUITE)
STRICT = SUITE.hardened_v4


def fake_pod(name, ip):
    return {'metadata': {'name': name}, 'status': {'phase': 'Running', 'podIP': ip, 'podIPs': [{'ip': ip}], 'conditions': [{'type': 'Ready', 'status': 'True'}]}}


def test_null_endpointslice_is_a_failed_check():
    mocked = SimpleNamespace(k=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps({'items': [{'endpoints': None}]}), stderr=''))
    good, details = STRICT.service_endpoints(mocked, 'payments-prod', 'payments-api', [fake_pod('pay-1', '10.0.0.5')])
    assert not good
    assert details['ready_count'] == 0


def test_empty_endpointslice_is_not_accepted():
    mocked = SimpleNamespace(k=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps({'items': []}), stderr=''))
    good, details = STRICT.service_endpoints(mocked, 'risk-prod', 'risk-api', [fake_pod('risk-1', '10.0.0.6')])
    assert not good
    assert details['observed'] == []


def test_probe_blocked_rejects_unexpected_exec_error(monkeypatch):
    def fake_ready(suite, ns, label, count):
        return [fake_pod('p' + str(i), '10.1.0.' + str(i)) for i in range(count)], {}
    monkeypatch.setattr(STRICT, 'ready_pods', fake_ready)
    monkeypatch.setattr(STRICT, 'exec_http', lambda *a, **kw: (124, '', 'kubectl exec timed out'))
    good, details = STRICT.probe_blocked(SimpleNamespace(), 1)
    assert not good
    assert details['exit'] == 124


def test_probe_backends_detects_single_failing_replica(monkeypatch):
    def fake_ready(suite, ns, label, count):
        prefix = '10.5.0.' if ns == 'risk-prod' else '10.4.0.' if ns == 'payments-prod' else '10.3.0.'
        return [fake_pod(ns + '-' + str(i), prefix + str(i)) for i in range(1, count + 1)], {}
    monkeypatch.setattr(STRICT, 'ready_pods', fake_ready)
    monkeypatch.setattr(STRICT, 'service_endpoints', lambda *a, **kw: (True, {}))
    def fake_http(suite, ns, pod, url, timeout=3):
        return (28, '', 'timeout') if url.endswith('10.5.0.2:8080') else (0, 'risk-api healthy' if 'risk' in url or '10.5.0.' in url else 'payments-api healthy', '')
    monkeypatch.setattr(STRICT, 'exec_http', fake_http)
    good, details = STRICT.probe_backends(SimpleNamespace(), 1, False)
    assert not good
    assert details['url'].endswith('10.5.0.2:8080')


def test_fast_fault_detection_requires_actual_injected_state(monkeypatch):
    objects = {
        ('service', 'payments-api', 'payments-prod'): {'spec': {'selector': {'release': 'canary'}}},
        ('service', 'risk-api', 'risk-prod'): {'spec': {'internalTrafficPolicy': 'Local', 'ports': [{'targetPort': 8181}]}},
        ('pod', 'risk-legacy-shadow', 'risk-prod'): {'kind': 'Pod'},
        ('deployment', 'payments-api', 'payments-prod'): {'spec': {'paused': True}},
        ('configmap', 'payments-release', 'payments-prod'): {'data': {'RELEASE_ID': 'v2-certified'}},
        ('configmap', 'risk-release', 'risk-prod'): {'data': {'RELEASE_ID': 'v2-certified'}},
        ('deployment', 'settlement-worker', 'worker-prod'): {'spec': {'template': {'spec': {'nodeSelector': {'eval-role': 'retired'}}}}},
        ('deployment', 'config-watcher', 'ops-prod'): {'spec': {'template': {'spec': {'initContainers': [{'securityContext': {'allowPrivilegeEscalation': True}}]}}}},
        ('networkpolicy', 'allow-lab-temporary', 'payments-prod'): {'kind': 'NetworkPolicy'},
        ('networkpolicy', 'risk-ingress', 'risk-prod'): {'spec': {'podSelector': {'matchLabels': {'role': 'old-backend'}}}},
    }
    monkeypatch.setattr(SUITE, 'obj', lambda kind, name, namespace=None: objects.get((kind, name, namespace), {}))
    monkeypatch.setattr(SUITE, 'quota_hard', lambda ns, name, value: value in ('288Mi', '192Mi'))
    assert all(SUITE.fast_fault_present(task) for task in SUITE.SCENARIOS)
    objects.pop(('networkpolicy', 'allow-lab-temporary', 'payments-prod'))
    assert not SUITE.fast_fault_present('t05')
