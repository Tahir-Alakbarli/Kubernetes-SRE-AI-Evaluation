
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from benchmark.kube import documents, identity, service_backends, target_port_resolves, allowed, index_by_name, select

TASKS = {'t01': 't01-checkout-routing', 't02': 't02-secure-operator'}


def check(out, name, condition, points=0, group='repair'):
    out['checks'].append({'name': name, 'passed': bool(condition), 'points': points, 'group': group})


def immutable_files(out, source, workspace, filenames):
    for name in filenames:
        check(out, f'immutable:{name}', (source / name).read_bytes() == (workspace / name).read_bytes(), 0, 'integrity')


def score_diagnosis(out, workspace, expected):
    diagnosis = json.loads((workspace / 'diagnosis.json').read_text())
    causes = diagnosis.get('root_causes')
    correct = isinstance(causes, list) and len(causes) == len(expected) and set(causes) == set(expected)
    check(out, 'precise_root_causes', correct, 30, 'diagnosis')
    check(out, 'explanation_present', len(str(diagnosis.get('explanation', '')).strip()) >= 60, 0, 'format')
    check(out, 'validation_present', len(str(diagnosis.get('validation', '')).strip()) >= 40, 0, 'format')


def t01(out, source, work):
    immutable_files(out, source, work, ['namespaces.yaml', 'pods.yaml'])
    namespaces = {n['metadata']['name']: n for n in documents(work / 'namespaces.yaml')}
    pods = documents(work / 'pods.yaml')
    services = documents(work / 'services.yaml')
    policies = documents(work / 'networkpolicies.yaml')
    check(out, 'only_payments_service', [identity(o) for o in services] == ['Service/payments-prod/payments-api'], 0, 'integrity')
    check(out, 'two_original_network_policies', set(map(identity, policies)) == {
        'NetworkPolicy/storefront-prod/checkout-egress', 'NetworkPolicy/payments-prod/payments-ingress'
    } and len(policies) == 2, 0, 'integrity')
    if not out['checks'][-1]['passed'] or not out['checks'][-2]['passed']:
        return
    orig_svc = documents(source / 'services.yaml')[0]
    sv = services[0]
    expected_base = copy.deepcopy(orig_svc)
    expected_base['spec'].pop('selector', None)
    actual_base = copy.deepcopy(sv)
    actual_base['spec'].pop('selector', None)
    check(out, 'service_contract_preserved', actual_base == expected_base, 10, 'completeness')
    backend = service_backends(sv, pods)
    backend_names = {x['metadata']['name'] for x in backend}
    check(out, 'two_intended_ready_backends', backend_names == {'payments-api-5c7-0','payments-api-5c7-1'}, 15, 'completeness')
    check(out, 'named_target_port_resolves', len(backend) == 2 and all(target_port_resolves(sv, p) for p in backend), 10, 'completeness')
    orig_policies = index_by_name(documents(source / 'networkpolicies.yaml'))
    actual_policies = index_by_name(policies)
    old_egress_scope = copy.deepcopy(orig_policies['checkout-egress'])
    old_egress_scope['spec'].pop('egress', None)
    new_egress_scope = copy.deepcopy(actual_policies['checkout-egress'])
    new_egress_scope['spec'].pop('egress', None)
    check(out, 'checkout_egress_scope_unchanged', old_egress_scope == new_egress_scope, 0, 'integrity')
    original_ingress = orig_policies['payments-ingress']
    edited_ingress = actual_policies['payments-ingress']
    orig_except_rules = copy.deepcopy(original_ingress)
    orig_except_rules['spec'].pop('ingress', None)
    current_except_rules = copy.deepcopy(edited_ingress)
    current_except_rules['spec'].pop('ingress', None)
    check(out, 'ingress_policy_scope_unchanged', orig_except_rules == current_except_rules, 0, 'integrity')
    pd = index_by_name(pods)
    source_gateway = pd['checkout-gateway-0']
    destination = pd['payments-api-5c7-0']
    flows = [
       ('gateway_api_access', source_gateway, destination, 8080, 'TCP', True, 15),
       ('debug_blocked', pd['debug-tool-0'], destination, 8080, 'TCP', False, 5),
       ('sandbox_blocked', pd['sandbox-curl-0'], destination, 8080, 'TCP', False, 5),
       ('audit_metrics_preserved', pd['audit-agent-0'], destination, 9090, 'TCP', True, 5),
       ('gateway_metrics_blocked', source_gateway, destination, 9090, 'TCP', False, 5),
       ('gateway_mysql_blocked', source_gateway, destination, 3306, 'TCP', False, 0),
       ('gateway_sandbox_egress_denied', source_gateway, pd['sandbox-curl-0'], 8080, 'TCP', False, 0),
       ('gateway_audit_egress_denied', source_gateway, pd['audit-agent-0'], 9090, 'TCP', False, 0),
       ('gateway_other_payments_pod_denied', source_gateway, pd['settlement-worker-0'], 8080, 'TCP', False, 0),
       ('gateway_dns_udp_preserved', source_gateway, pd['dns-0'], 53, 'UDP', True, 0),
       ('gateway_dns_tcp_preserved', source_gateway, pd['dns-0'], 53, 'TCP', True, 0),
    ]
    for name, src, dst, port, proto, expected, points in flows:
        check(out, name, allowed(src, dst, namespaces, policies, port, proto) == expected, points, 'recovery')
    score_diagnosis(out, work, {'Service/payments-prod/payments-api','NetworkPolicy/payments-prod/payments-ingress','NetworkPolicy/storefront-prod/checkout-egress'})


def restricted_ok(container, pod_security):
    c = container.get('securityContext') or {}
    uid = c.get('runAsUser', pod_security.get('runAsUser'))
    nonroot = c.get('runAsNonRoot', pod_security.get('runAsNonRoot'))
    seccomp = (c.get('seccompProfile') or pod_security.get('seccompProfile') or {}).get('type')
    cap = c.get('capabilities', {}).get('drop', [])
    add = c.get('capabilities', {}).get('add', [])
    return (nonroot is True and isinstance(uid, int) and uid > 0
            and c.get('allowPrivilegeEscalation') is False
            and 'ALL' in cap and not add and c.get('privileged', False) is not True
            and seccomp in ('RuntimeDefault', 'Localhost'))


def t02(out, source, work):
    immutable_files(out, source, work, ['namespace.yaml','access.yaml'])
    dep_list = documents(work / 'deployment.yaml')
    role_list = documents(work / 'role.yaml')
    check(out, 'deployment_and_role_identity', len(dep_list) == 1 and len(role_list) == 1 and
          identity(dep_list[0]) == 'Deployment/ops-prod/config-watcher' and
          identity(role_list[0]) == 'Role/ops-prod/configmap-observer', 0, 'integrity')
    if not out['checks'][-1]['passed']:
        return
    deployment = dep_list[0]
    role = role_list[0]
    before = documents(source / 'deployment.yaml')[0]
    def core_copy(obj):
        obj = copy.deepcopy(obj)
        pod = obj['spec']['template']['spec']
        pod.pop('securityContext', None)
        for container in pod['initContainers'] + pod['containers']:
            container.pop('securityContext', None)
        return obj
    check(out, 'workload_and_rollout_contract_unchanged', core_copy(before) == core_copy(deployment), 15, 'completeness')
    pod_spec = deployment['spec']['template']['spec']
    sec = pod_spec.get('securityContext') or {}
    initial = pod_spec['initContainers'][0]
    controller = pod_spec['containers'][0]
    check(out, 'both_containers_restricted_compliant', restricted_ok(initial, sec) and restricted_ok(controller, sec), 15, 'completeness')
    check(out, 'writable_shared_emptydir',
          sec.get('fsGroup') == 10001
          and any(v.get('name') == 'scratch' and 'emptyDir' in v for v in pod_spec.get('volumes', []))
          and any(v.get('name') == 'scratch' and v.get('mountPath') == '/work' for v in initial.get('volumeMounts', []))
          and any(v.get('name') == 'scratch' and v.get('mountPath') == '/work' for v in controller.get('volumeMounts', [])), 5, 'completeness')

    rules = role.get('rules', [])
    permissions = set()
    no_extra = True
    for r in rules:
        if r.get('apiGroups') != [''] or r.get('resources') != ['configmaps'] or r.get('resourceNames'):
            no_extra = False
        verbs = r.get('verbs', [])
        if not isinstance(verbs, list) or any(v not in ('get', 'list', 'watch') for v in verbs):
            no_extra = False
        if no_extra:
            permissions.update(verbs)
    check(out, 'rbac_minimal_namespaced_only', no_extra and permissions == {'get','list','watch'}, 25, 'recovery')
    initial_role = copy.deepcopy(documents(source / 'role.yaml')[0])
    initial_role.pop('rules', None)
    final_role = copy.deepcopy(role)
    final_role.pop('rules', None)
    check(out, 'role_identity_preserved', final_role == initial_role, 0, 'integrity')
    psa = documents(work / 'namespace.yaml')[0]['metadata']['labels']
    check(out, 'psa_restricted_enforced', psa.get('pod-security.kubernetes.io/enforce') == 'restricted', 5, 'recovery')
    check(out, 'no_secrets_permission', no_extra, 5, 'recovery')
    score_diagnosis(out, work, {'Deployment/ops-prod/config-watcher','Role/ops-prod/configmap-observer'})


def grade(task, workspace):
    if task not in TASKS:
        raise ValueError(f'Invalid task id: {task}')
    workspace = Path(workspace)
    source = ROOT / 'tasks' / TASKS[task] / 'initial'
    out = {'task': task, 'workspace': str(workspace), 'checks': [], 'score': 0, 'passed': False, 'error': None,
           'validation_level': 'offline-manifest-and-policy-semantics'}
    try:
        if task == 't01':
            t01(out, source, workspace)
        else:
            t02(out, source, workspace)
    except Exception as exc:
        out['error'] = f'{type(exc).__name__}: {exc}'
        out['checks'].append({'name':'parse_and_evaluate','passed':False,'points':0,'group':'integrity'})
    out['score'] = sum(c['points'] for c in out['checks'] if c['passed'])
    out['max_points'] = 100
    out['passed'] = bool(out['checks']) and all(c['passed'] for c in out['checks']) and out['error'] is None
    return out


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('task', choices=TASKS)
    p.add_argument('workspace')
    args = p.parse_args()
    print(json.dumps(grade(args.task,args.workspace),indent=2))
