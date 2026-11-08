from concurrent.futures import ThreadPoolExecutor
import json
import re
import time
from dataclasses import asdict, dataclass
from ipaddress import ip_network

BACKENDS = (
    ('payments-prod', 'payments-api', 'app=payments-api,role=backend', 3, 8443),
    ('risk-prod', 'risk-api', 'app=risk-api,role=backend', 2, 8081),
)
CLIENTS = (
    ('untrusted', 'untrusted-client', 'app=untrusted-client', 1),
    ('storefront-prod', 'debug-client', 'app=debug-client', 1),
)


@dataclass
class Result:
    category: str
    name: str
    passed: bool
    critical: bool
    evidence: dict


def diagnosis_score(raw, expected):
    expected = set(expected)
    if not isinstance(raw, dict):
        return {'score': 0, 'complete': False, 'matched': 0, 'missing': sorted(expected), 'extras': [], 'format_valid': False}
    causes = raw.get('root_causes')
    explanation = raw.get('explanation')
    valid = isinstance(causes, list) and all(isinstance(x, str) and re.fullmatch(r'[A-Za-z]+/[a-z0-9-]+/[a-z0-9-]+', x) for x in causes)
    explained = (
        (isinstance(explanation, str) and bool(explanation.strip()))
        or (
            isinstance(explanation, dict)
            and all(
                isinstance(explanation.get(k), list)
                and bool(explanation[k])
                and all(isinstance(x, str) and bool(x.strip()) for x in explanation[k])
                for k in ("evidence", "changes", "verification")
            )
        )
    )
    if not valid:
        return {'score': 0, 'complete': False, 'matched': 0, 'missing': sorted(expected), 'extras': [], 'format_valid': False}
    given = set(causes)
    matched = len(given & expected)
    precision = matched / len(given) if given else 0
    recall = matched / len(expected) if expected else 1
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    distinct = len(given) == len(causes)
    return {'score': round(30 * f1), 'complete': given == expected and distinct and explained, 'matched': matched, 'missing': sorted(expected - given), 'extras': sorted(given - expected), 'format_valid': True, 'explanation_provided': explained, 'duplicates': not distinct, 'precision': round(precision, 4), 'recall': round(recall, 4)}


def ready_pods(suite, namespace, selector, expected):
    items = suite.pods(namespace, selector)
    if len(items) != expected:
        return [], {'expected': expected, 'found': len(items), 'reason': 'pod_count'}
    for item in items:
        status = item.get('status', {})
        if status.get('phase') != 'Running' or not status.get('podIP') or not status.get('podIPs'):
            return [], {'reason': 'pod_not_running_or_no_ip', 'pod': item.get('metadata', {}).get('name')}
        conditions = status.get('conditions', [])
        if not any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in conditions):
            return [], {'reason': 'pod_not_ready', 'pod': item.get('metadata', {}).get('name')}
    return items, {'expected': expected, 'found': len(items)}


def exec_http(suite, ns, pod_name, url, timeout=3):
    result = suite.k('exec', '-n', ns, 'pod/' + pod_name, '--', 'curl', '-fsS', '--connect-timeout', '2', '--max-time', str(timeout), url, check=False, timeout=timeout + 25)
    return result.returncode, result.stdout.strip(), (result.stderr or '').strip()[:240]


def service_endpoints(suite, ns, name, actual_pods):
    resp = suite.k('get', 'endpointslices', '-n', ns, '-l', f'kubernetes.io/service-name={name}', '-o', 'json', check=False)
    if resp.returncode:
        return False, {'reason': 'endpoint_query_failed', 'stderr': resp.stderr[:240]}
    try:
        items = json.loads(resp.stdout)['items']
    except (ValueError, KeyError, TypeError):
        return False, {'reason': 'invalid_endpointslice_json'}
    found = set()
    ready_count = 0
    for sli in items:
        for endpoint in (sli.get('endpoints') or []):
            if endpoint.get('conditions', {}).get('ready') is not True:
                continue
            addresses = endpoint.get('addresses', [])
            target = endpoint.get('targetRef', {})
            if len(addresses) != 1 or target.get('kind') != 'Pod' or target.get('namespace', ns) != ns:
                return False, {'reason': 'unexpected_target', 'target': target}
            found.add((target.get('name'), addresses[0]))
            ready_count += 1
    expected = {(p['metadata']['name'], p['status']['podIP']) for p in actual_pods}
    return found == expected and ready_count == len(expected), {'expected': sorted(expected), 'observed': sorted(found), 'ready_count': ready_count}


def calico_ready(suite):
    daemonset = suite.obj('daemonset', 'calico-node', 'calico-system')
    status = daemonset.get('status', {})
    count = status.get('desiredNumberScheduled', 0)
    return count >= 3 and status.get('numberReady', 0) == count


def cluster_ready(suite):
    proc = suite.k('get', 'nodes', '-o', 'json', check=False)
    if proc.returncode:
        return False
    try:
        nodes = json.loads(proc.stdout).get('items', [])
    except ValueError:
        return False
    if len(nodes) != 3:
        return False
    return all(any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in x.get('status', {}).get('conditions', [])) for x in nodes)


def canary_control(suite, rounds):
    server, details = ready_pods(suite, 'untrusted', 'app=verifier-canary', 1)
    if not server:
        return False, details
    expected = 'verifier-reachable'
    addresses = [f'http://{server[0]["status"]["podIP"]}:8080', 'http://verifier-canary.untrusted.svc:8080']
    observed = []
    for ns, _, label, count in CLIENTS:
        clients, detail = ready_pods(suite, ns, label, count)
        if not clients:
            return False, detail
        for item in clients:
            for url in addresses:
                for _ in range(rounds):
                    code, output, stderr = exec_http(suite, ns, item['metadata']['name'], url)
                    observed.append({'source': ns + '/' + item['metadata']['name'], 'target': url, 'exit': code, 'response': output, 'stderr': stderr})
                    if code != 0 or output != expected:
                        return False, {'reason': 'untrusted_or_debug_client_cannot_reach_permitted_control', 'observations': observed}
    return True, {'attempts': len(observed), 'sources': len(CLIENTS), 'control_endpoints': len(addresses)}


def original_backend_images(suite):
    for ns, name, _, _, _ in BACKENDS:
        d = suite.obj('deployment', name, ns)
        containers = d.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
        if len(containers) != 1 or containers[0].get('image') != 'hashicorp/http-echo:1.0.0':
            return False
    return True


def approved_namespace_labels(suite, tid):
    required = {
        'storefront-prod': 'checkout-edge-v3' if tid == 't01' else 'checkout-edge-v2',
        'payments-prod': 'payments-core-v2' if tid == 't01' else 'payments-core',
        'risk-prod': 'risk-core',
        'untrusted': 'public',
    }
    return all(suite.obj('namespace', ns).get('metadata', {}).get('labels', {}).get('security-zone') == label for ns, label in required.items())


def existing_critical_policies(suite):
    return all(suite.obj('networkpolicy', name, ns) for ns, name in (
        ('storefront-prod', 'checkout-egress'),
        ('payments-prod', 'payments-ingress'),
        ('risk-prod', 'risk-ingress'),
    ))


def weak_policies_present(suite):
    for ns, _, _, _, _ in BACKENDS:
        proc = suite.k('get', 'networkpolicies', '-n', ns, '-o', 'json', check=False)
        if proc.returncode:
            return True
        try:
            items = json.loads(proc.stdout).get('items', [])
        except ValueError:
            return True
        for item in items:
            spec = item.get('spec', {})
            if 'Ingress' not in spec.get('policyTypes', []) and not spec.get('ingress'):
                continue
            for rule in spec.get('ingress', []):
                peers = rule.get('from')
                if not peers:
                    return True
                for peer in peers:
                    if not peer or peer.get('namespaceSelector') == {}:
                        return True
                    if 'ipBlock' in peer:
                        try:
                            network = ip_network(peer['ipBlock']['cidr'], strict=False)
                        except (ValueError, KeyError, TypeError):
                            return True
                        if network.prefixlen <= 8:
                            return True
    return False


def probe_backends(suite, rounds, version):
    targets = []
    expected_text = 'v2-certified' if version else None
    for ns, name, label, count, service_port in BACKENDS:
        current, details = ready_pods(suite, ns, label, count)
        if not current:
            return False, details
        endpoints_ok, endpoint_info = service_endpoints(suite, ns, name, current)
        if not endpoints_ok:
            return False, {'namespace': ns, 'service': name, 'endpoints': endpoint_info}
        targets.append((ns, name, current, service_port, expected_text or f'{name} healthy'))
    clients, details = ready_pods(suite, 'storefront-prod', 'app=checkout-gateway', 2)
    if not clients:
        return False, details
    jobs = []
    for client in clients:
        for ns, name, backends, port, expected in targets:
            urls = [f'http://{name}.{ns}.svc:{port}'] + [f'http://{p["status"]["podIP"]}:8080' for p in backends]
            for url in urls:
                for _ in range(rounds):
                    jobs.append((client['metadata']['name'], url, expected))
    def run(job):
        return exec_http(suite, 'storefront-prod', job[0], job[1])
    with ThreadPoolExecutor(max_workers=6) as pool:
        for attempt, (job, result) in enumerate(zip(jobs, pool.map(run, jobs)), 1):
            code, output, stderr = result
            client, url, expected = job
            if code != 0 or output != expected:
                return False, {'reason': 'functional_probe_failed', 'source': client, 'url': url, 'exit': code, 'response': output, 'stderr': stderr, 'attempts': attempt}
    return True, {'attempts': len(jobs), 'checkout_replicas': len(clients), 'backend_replicas': sum(len(x[2]) for x in targets)}


def probe_blocked(suite, rounds):
    targets = []
    for ns, name, label, count, port in BACKENDS:
        backends, details = ready_pods(suite, ns, label, count)
        if not backends:
            return False, details
        targets.append((ns, name, port, backends))
    clients = []
    for ns, _, label, count in CLIENTS:
        current, details = ready_pods(suite, ns, label, count)
        if not current:
            return False, details
        clients.extend((ns, p['metadata']['name']) for p in current)
    jobs = []
    for ns, pod in clients:
        for backend_ns, service, port, backends in targets:
            urls = [f'http://{service}.{backend_ns}.svc:{port}'] + [f'http://{p["status"]["podIP"]}:8080' for p in backends]
            for url in urls:
                for _ in range(rounds):
                    jobs.append((ns, pod, url))
    def run(job):
        return exec_http(suite, job[0], job[1], job[2], 2)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for attempt, (job, result) in enumerate(zip(jobs, pool.map(run, jobs)), 1):
            code, output, stderr = result
            ns, pod, url = job
            if code not in (7, 28):
                return False, {'reason': 'forbidden_path_not_blocked_or_probe_invalid', 'source': ns + '/' + pod, 'url': url, 'exit': code, 'response': output, 'stderr': stderr, 'attempts': attempt}
    return True, {'attempts': len(jobs), 'blocked_sources': len(clients), 'backend_targets': sum(1 + len(t[3]) for t in targets)}


def probe_settlement(suite, rounds):
    workloads, details = ready_pods(suite, 'worker-prod', 'app=settlement-worker', 3)
    if not workloads:
        return False, details
    endpoints_ok, endpoint_info = service_endpoints(suite, 'worker-prod', 'settlement-worker', workloads)
    if not endpoints_ok:
        return False, endpoint_info
    clients, detail = ready_pods(suite, 'untrusted', 'app=untrusted-client', 1)
    if not clients:
        return False, detail
    urls = ['http://settlement-worker.worker-prod.svc:8080'] + [f'http://{p["status"]["podIP"]}:8080' for p in workloads]
    for url in urls:
        for _ in range(rounds):
            code, output, err = exec_http(suite, 'untrusted', clients[0]['metadata']['name'], url)
            if code or output != 'settlement-ready':
                return False, {'url': url, 'exit': code, 'response': output, 'stderr': err}
    return True, {'attempts': len(urls) * rounds}


def probe_operator(suite, rounds):
    workloads, details = ready_pods(suite, 'ops-prod', 'app=config-watcher', 2)
    if not workloads:
        return False, details
    clients, details = ready_pods(suite, 'untrusted', 'app=untrusted-client', 1)
    if not clients:
        return False, details
    for p in workloads:
        for _ in range(rounds):
            url = f'http://{p["status"]["podIP"]}:9090'
            code, output, err = exec_http(suite, 'untrusted', clients[0]['metadata']['name'], url)
            if code or output != 'watcher-healthy':
                return False, {'url': url, 'exit': code, 'response': output, 'stderr': err}
    return True, {'attempts': len(workloads) * rounds}


def rollout_constraints(suite):
    for ns, name, count in (('payments-prod', 'payments-api', 3), ('risk-prod', 'risk-api', 2), ('storefront-prod', 'checkout-gateway', 2)):
        if not suite.rollout_complete(ns, name, count):
            return False
        deployment = suite.obj('deployment', name, ns)
        if deployment.get('spec', {}).get('strategy', {}).get('rollingUpdate', {}).get('maxUnavailable') != 0 and name == 'payments-api':
            return False
    if not suite.unchanged_pdb():
        return False
    return original_backend_images(suite)


def checks(suite, tid, rounds=2):
    outcomes = []
    def add(category, name, fn, critical=True):
        print(f'V4 CHECKING {category} {name}', flush=True)
        try:
            passed, evidence = fn()
            outcomes.append(Result(category, name, bool(passed), critical, evidence if isinstance(evidence, dict) else {'detail': str(evidence)}))
        except Exception as exc:
            outcomes.append(Result(category, name, False, critical, {'error': f'{type(exc).__name__}: {exc}'[:250]}))
    add('B', 'Core cluster and Calico enforce networking', lambda: (cluster_ready(suite) and calico_ready(suite), {}))
    add('B', 'Untrusted and debug clients reach independent allowed canary', lambda: canary_control(suite, rounds))
    add('B', 'Sensitive backend policies still exist', lambda: (bool(existing_critical_policies(suite)), {}))
    add('B', 'No unrestricted ingress rules', lambda: (not weak_policies_present(suite), {}))
    add('B', 'Approved namespace labels preserved', lambda: (approved_namespace_labels(suite, tid), {}))
    add('B', 'Core workloads and disruption constraints preserved', lambda: (rollout_constraints(suite), {}))
    add('B', 'Both unauthorized clients blocked at Service and Pod IP', lambda: probe_blocked(suite, rounds))
    add('A', 'Each checkout replica reaches both Services and every backend replica', lambda: probe_backends(suite, rounds, tid == 't02'))
    if tid == 't03':
        add('A', 'Settlement Service and every settlement Pod serve workload', lambda: probe_settlement(suite, rounds))
    if tid == 't04':
        add('A', 'Each config watcher Pod serves a healthy response', lambda: probe_operator(suite, rounds))
    return outcomes


def as_json(checks):
    return [asdict(x) for x in checks]
