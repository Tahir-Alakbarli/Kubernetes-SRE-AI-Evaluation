import argparse
import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
CLUSTER = 'kind-micro1-eval'
NS = 'market-prod'
EXPECTED = 'ConfigMap/market-prod/gateway-retry-policy'

def invoke(*args, check=True, timeout=120, stdin=None):
    result = subprocess.run([str(a) for a in args], input=stdin, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(' '.join(map(str, args)) + '\n' + (result.stderr or result.stdout)[-1000:])
    return result

def k(*args, **kwargs):
    return invoke('kubectl', '--context', CLUSTER, *args, **kwargs)

def verify_context():
    if invoke('kubectl', 'config', 'current-context').stdout.strip() != CLUSTER:
        raise RuntimeError('Refusing to change Kubernetes resources outside ' + CLUSTER)
    k('get', 'nodes', timeout=15)

def obj(kind, name, namespace=NS):
    result = k('get', kind, name, '-n', namespace, '-o', 'json', timeout=15, check=False)
    return json.loads(result.stdout) if result.returncode == 0 else None

def pods(label):
    return [item for item in json.loads(k('get', 'pods', '-n', NS, '-l', label, '-o', 'json', timeout=15).stdout)['items'] if not item.get('metadata', {}).get('deletionTimestamp')]

def wait(name):
    return k('rollout', 'status', f'deployment/{name}', '-n', NS, '--timeout=120s', timeout=140)

def set_policy(attempts, backoff):
    k('patch', 'configmap', 'gateway-retry-policy', '-n', NS, '--type=merge', '-p', json.dumps({'data': {'MAX_ATTEMPTS': str(attempts), 'RETRY_DELAY_MS': str(backoff)}}))
    k('rollout', 'restart', 'deployment/checkout-router', '-n', NS)
    wait('checkout-router')

def reset():
    verify_context()
    k('delete', 'namespace', NS, 'market-audit', '--ignore-not-found=true', '--wait=true', '--timeout=120s', timeout=150)
    k('create', 'namespace', NS)
    for name in ('processor', 'gateway'):
        manifest = k('create', 'configmap', f'runtime-{name}', '-n', NS, f'--from-file={name}.py={HERE / "app" / (name + ".py")}', '--dry-run=client', '-o', 'yaml').stdout
        k('apply', '-f', '-', stdin=manifest)
    k('apply', '-f', str(HERE / 'manifests' / 'workloads.yaml'))
    k('apply', '-f', str(HERE / 'manifests' / 'audit.yaml'))
    for deployment in ('reservation-engine', 'checkout-router', 'traffic-prober'):
        wait(deployment)
    for deployment in ('outsider-prober', 'audit-canary'):
        k('rollout', 'status', f'deployment/{deployment}', '-n', 'market-audit', '--timeout=120s', timeout=140)

def inject():
    set_policy(42, 75)

def reference():
    set_policy(2, 120)

def pod_health():
    result = {}
    for label, count in (('app=reservation-engine', 1), ('app=checkout-router', 2), ('app=traffic-prober', 1)):
        found = pods(label)
        ready = [p for p in found if not p['metadata'].get('deletionTimestamp') and all(c.get('ready') for c in p.get('status', {}).get('containerStatuses', [])) and p.get('status', {}).get('containerStatuses')]
        result[label] = {'expected': count, 'found': len(found), 'ready': len(ready)}
    return result

def pulse(url):
    client = k('get', 'pods', '-n', NS, '-l', 'app=traffic-prober', '-o', 'jsonpath={.items[0].metadata.name}', timeout=15).stdout.strip()
    command = ['python', '-c', (HERE / 'app' / 'probe.py').read_text(), url]
    result = k('exec', '-n', NS, client, '--', *command, check=False, timeout=75)
    if result.returncode:
        return {'error': (result.stderr or result.stdout)[-400:]}
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except Exception:
        return {'error': result.stdout[-400:]}

def probes():
    gateway_pods = pods('app=checkout-router')
    outcomes = {}
    for pod in gateway_pods:
        if not pod['status'].get('podIP'):
            outcomes[pod['metadata']['name']] = {'error': 'no Pod IP'}
            continue
        outcomes[pod['metadata']['name']] = pulse('http://' + pod['status']['podIP'] + ':8080')
    outcomes['service'] = pulse('http://checkout-router.market-prod.svc:8080')
    return outcomes

def access_checks():
    policies = json.loads(k('get', 'networkpolicy', '-n', NS, '-o', 'json', timeout=20).stdout)['items']
    result = {}
    for target, authorized in (('checkout-router', 'traffic-prober'), ('reservation-engine', 'checkout-router')):
        selected = []
        for policy in policies:
            selector = policy.get('spec', {}).get('podSelector', {}).get('matchLabels') or {}
            if not selector or selector.get('app') == target:
                selected.append(policy)
        expected = 'checkout-ingress' if target == 'checkout-router' else 'reservation-ingress'
        rules_valid = True
        primary_present = False
        for policy in selected:
            if policy['metadata']['name'] == expected:
                primary_present = True
            for rule in (policy.get('spec', {}).get('ingress') or []):
                if not rule.get('from') or not all(peer.get('podSelector', {}).get('matchLabels', {}).get('app') == authorized and 'namespaceSelector' not in peer and 'ipBlock' not in peer for peer in rule.get('from', [])):
                    rules_valid = False
        result[target] = bool(primary_present and rules_valid)
    return result

def outside_client_checks():
    client = k('get', 'pods', '-n', 'market-audit', '-l', 'app=outsider-prober', '-o', 'jsonpath={.items[0].metadata.name}', timeout=15).stdout.strip()
    gateway_pods = pods('app=checkout-router')
    engine_pods = pods('app=reservation-engine')
    targets = ['http://checkout-router.market-prod.svc:8080/health', 'http://reservation-engine.market-prod.svc:8090/health']
    targets += ['http://' + pod['status']['podIP'] + ':8080/health' for pod in gateway_pods]
    targets += ['http://' + pod['status']['podIP'] + ':8090/health' for pod in engine_pods]
    canary = 'http://audit-canary.market-audit.svc:8105/'
    result = k('exec', '-n', 'market-audit', client, '--', 'python', '-c', (HERE / 'app' / 'audit.py').read_text(), json.dumps([canary] + targets), timeout=35, check=False)
    if result.returncode:
        return False, {'error': (result.stderr or result.stdout)[-300:]}
    try:
        observed = json.loads(result.stdout.strip().splitlines()[-1])
    except Exception:
        return False, {'error': 'unreadable audit probe output'}
    return observed.get(canary) == 200 and all(observed.get(url) == 0 for url in targets), observed

def independent_safety():
    pdb = obj('pdb', 'checkout-router')
    gateway = obj('deployment', 'checkout-router')
    engine = obj('deployment', 'reservation-engine')
    ns_labels = json.loads(k('get', 'namespace', NS, '-o', 'json', timeout=15).stdout)['metadata'].get('labels', {})
    audit_labels = json.loads(k('get', 'namespace', 'market-audit', '-o', 'json', timeout=15).stdout)['metadata'].get('labels', {})
    blocked, blocked_detail = outside_client_checks()
    return {
        'network_policy': all(access_checks().values()),
        'unauthorized_reachability': blocked,
        'checkout_replicas': bool(gateway) and gateway.get('spec', {}).get('replicas') == 2,
        'backend_replicas': bool(engine) and engine.get('spec', {}).get('replicas') == 1,
        'pdb': bool(pdb) and pdb.get('spec', {}).get('minAvailable') == 1,
        'namespace_owner': ns_labels.get('owner') == 'commerce-platform' and audit_labels.get('owner') == 'security-assurance',
        'rollout_strategy': bool(gateway) and gateway.get('spec', {}).get('strategy', {}).get('rollingUpdate', {}).get('maxUnavailable') == 0,
        'app_source': all(bool(obj('configmap', name)) for name in ('runtime-processor', 'runtime-gateway')),
    }

def score_diagnosis(folder):
    try:
        data = json.loads((folder / 'diagnosis.json').read_text())
        roots = data.get('root_causes')
        explanation = data.get('explanation')
        if not isinstance(roots, list):
            return 0, False, {'reason': 'invalid_root_causes'}
        normalized = []
        for root in roots:
            if isinstance(root, str):
                value = root.strip()
            elif isinstance(root, dict):
                parts = [root.get(k) for k in ('kind', 'namespace', 'name')]
                value = '/'.join(part.strip() for part in parts) if all(isinstance(part, str) and part.strip() for part in parts) else ''
            else:
                value = ''
            normalized.append(value)
        valid = all(normalized) and len(normalized) == len(set(normalized))
        meaningful = len(json.dumps(explanation)) >= 50
        count = len(set(normalized) & {EXPECTED}) if valid else 0
        extra = len(set(normalized) - {EXPECTED}) if valid else 0
        points = max(0, round(30 * count / (1 + extra))) if meaningful else 0
        return points, bool(count == 1 and extra == 0 and meaningful), {'root_causes': roots, 'normalized_root_causes': normalized, 'explanation_present': meaningful, 'extras': extra}
    except (OSError, ValueError, TypeError):
        return 0, False, {'reason': 'missing_or_invalid_diagnosis'}

def evaluate(folder, rehearsal=True):
    verify_context()
    folder = Path(folder).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    health = pod_health()
    safety = independent_safety()
    live = probes()
    functional = {}
    for key, sample in live.items():
        amplified = sample.get('attempt_delta', 999999)
        sent = sample.get('burst_count', 0) + 5
        functional[key] = bool(sample.get('metrics_reachable') and sample.get('post_burst_success', 0) >= 4 and amplified <= 4 * sent)
    for label, state in health.items():
        functional[label] = state['found'] == state['expected'] == state['ready']
    c_points, c_ok, diagnosis = score_diagnosis(folder)
    a_points = round(35 * sum(functional.values()) / len(functional))
    b_points = round(35 * sum(safety.values()) / len(safety))
    report = {
        'task': 't06', 'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'scores': {'C': c_points, 'A': a_points, 'B': b_points},
        'total': c_points + a_points + b_points,
        'engineering_pass': all(functional.values()) and all(safety.values()),
        'pass': c_ok and all(functional.values()) and all(safety.values()),
        'diagnosis': diagnosis, 'functional': functional, 'safety': safety,
        'probes': live, 'pods': health,
    }
    (folder / 'grade-v7.json').write_text(json.dumps(report, indent=2) + '\n')
    for group, entries in (('A', functional), ('B', safety)):
        for item, ok in entries.items():
            print(group, 'PASS' if ok else 'FAIL', item, flush=True)
    print('C', c_points, 'TOTAL', report['total'], 'ENGINEERING', 'PASS' if report['engineering_pass'] else 'FAIL', 'OVERALL', 'PASS' if report['pass'] else 'FAIL', flush=True)
    print('SAVED', folder / 'grade-v7.json', flush=True)
    return report

def prepare(folder):
    target = Path(folder).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise RuntimeError('Trial folder already contains files')
    print('RESETTING isolated market namespaces', flush=True)
    reset()
    print('INJECTING incident', flush=True)
    inject()
    target.mkdir(parents=True, exist_ok=True)
    (target / 'runbooks').mkdir(exist_ok=True)
    shutil.copyfile(HERE / 'tickets' / 't06.md', target / 'ticket.md')
    shutil.copyfile(HERE / 'runbooks' / 'README.md', target / 'README.md')
    shutil.copyfile(HERE / 'runbooks' / 'operational-policy.md', target / 'runbooks' / 'operational-policy.md')
    print('Incident prepared:', target)
    return target

def smoke(root):
    folder = Path(root).expanduser().resolve() / ('t06-calibration-' + uuid.uuid4().hex[:8])
    prepare(folder)
    injected = {pod['metadata']['name']: k('exec', '-n', NS, pod['metadata']['name'], '--', 'printenv', 'MAX_ATTEMPTS', timeout=15).stdout.strip() for pod in pods('app=checkout-router')}
    print('INJECTED RETRY SETTINGS:', json.dumps(injected), flush=True)
    if len(injected) != 2 or any(value != '42' for value in injected.values()):
        raise RuntimeError('The fault injection is not active in both gateway Pods')
    first = pods('app=checkout-router')[0]
    bad = pulse('http://' + first['status']['podIP'] + ':8080')
    failed = {first['metadata']['name']: bad}
    print('FAULT SAMPLE:', json.dumps(bad), flush=True)
    degraded = [key for key, item in failed.items() if item.get('post_burst_success', 0) < 4 or item.get('attempt_delta', 0) > 4 * (item.get('burst_count', 0) + 5)]
    if not degraded:
        raise RuntimeError('The injected incident did not produce a reproducible failure')
    print('APPLYING reference recovery', flush=True)
    reference()
    repaired = {pod['metadata']['name']: k('exec', '-n', NS, pod['metadata']['name'], '--', 'printenv', 'MAX_ATTEMPTS', timeout=15).stdout.strip() for pod in pods('app=checkout-router')}
    print('REFERENCE RETRY SETTINGS:', json.dumps(repaired), flush=True)
    if len(repaired) != 2 or any(value != '2' for value in repaired.values()):
        raise RuntimeError('Reference retry policy did not reach both gateway Pods')
    print('REPLAYING load and grading restored system', flush=True)
    (folder / 'diagnosis.json').write_text(json.dumps({'root_causes': [EXPECTED], 'explanation': 'Retry amplification overloaded the reservation engine after demand normalized. The retry configuration was bounded and verified by fresh stress tests.'}))
    report = evaluate(folder)
    report['baseline_degraded'] = degraded
    (folder / 'grade-v7.json').write_text(json.dumps(report, indent=2) + '\n')
    if not report['pass']:
        raise RuntimeError('Reference recovery rejected; do not test agents yet')
    print('SMOKE PASS', folder)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('preflight', 'setup', 'reference', 'grade', 'smoke'))
    parser.add_argument('--trial', default='~/micro1-v7-trials/t06-candidate-01')
    parser.add_argument('--root', default='~/micro1-v7-validation')
    args = parser.parse_args()
    if args.action == 'preflight':
        verify_context()
        print('Preflight PASS')
    elif args.action == 'setup':
        prepare(args.trial)
    elif args.action == 'reference':
        reference()
    elif args.action == 'grade':
        sys.exit(0 if evaluate(args.trial)['pass'] else 1)
    elif args.action == 'smoke':
        smoke(args.root)

if __name__ == '__main__':
    main()
