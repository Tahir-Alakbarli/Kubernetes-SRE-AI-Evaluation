import json
import shutil
from pathlib import Path

import pytest
import yaml

from graders.grade import grade, ROOT, TASKS


def trial(tmp_path, task, reference=False):
    base = ROOT / 'tasks' / TASKS[task]
    shutil.copytree(base / 'initial', tmp_path / 'workspace')
    if reference:
        for file in (base / 'reference').iterdir():
            shutil.copy(file, tmp_path / 'workspace' / file.name)
    return tmp_path / 'workspace'


def replace_yaml(path, documents):
    path.write_text(yaml.safe_dump_all(documents, sort_keys=False), encoding='utf-8')


@pytest.mark.parametrize('task', ['t01','t02'])
def test_reference_passes(tmp_path,task):
    workspace = trial(tmp_path,task, True)
    report = grade(task, workspace)
    assert report['passed'], json.dumps(report,indent=2)
    assert report['score'] == 100


@pytest.mark.parametrize('task', ['t01','t02'])
def test_unrepaired_fails(tmp_path,task):
    workspace = trial(tmp_path,task)
    (workspace / 'diagnosis.json').write_text(json.dumps({'root_causes':[], 'explanation':'unknown','validation':'unknown'}))
    assert not grade(task,workspace)['passed']


def test_t01_only_service_fix_fails(tmp_path):
    w = trial(tmp_path,'t01', True)
    shutil.copy(ROOT/'tasks/t01-checkout-routing/initial/networkpolicies.yaml', w/'networkpolicies.yaml')
    r = grade('t01',w)
    assert not r['passed'] and not next(c for c in r['checks'] if c['name']=='gateway_api_access')['passed']


def test_t01_only_networkpolicy_fix_fails(tmp_path):
    w = trial(tmp_path,'t01', True)
    shutil.copy(ROOT/'tasks/t01-checkout-routing/initial/services.yaml', w/'services.yaml')
    r = grade('t01',w)
    assert not r['passed'] and not next(c for c in r['checks'] if c['name']=='two_intended_ready_backends')['passed']


def test_t01_overbroad_namespace_peer_fails(tmp_path):
    w = trial(tmp_path,'t01', True)
    docs = list(yaml.safe_load_all((w/'networkpolicies.yaml').read_text()))
    policy = docs[1]
    # Splitting a combined selector into separate list entries grants EVERY pod in the namespace.
    first = policy['spec']['ingress'][0]['from'][0]
    policy['spec']['ingress'][0]['from'] = [{'namespaceSelector':first['namespaceSelector']},
                                           {'podSelector':first['podSelector']}]
    replace_yaml(w/'networkpolicies.yaml', docs)
    r=grade('t01',w)
    assert not r['passed'] and not next(c for c in r['checks'] if c['name']=='debug_blocked')['passed']


def test_t01_remove_ingress_rules_fails(tmp_path):
    w = trial(tmp_path,'t01', True)
    docs = list(yaml.safe_load_all((w/'networkpolicies.yaml').read_text()))
    docs[1]['spec']['ingress']=[{}]
    replace_yaml(w/'networkpolicies.yaml',docs)
    assert not grade('t01',w)['passed']


def test_t01_immutable_namespace_change_fails(tmp_path):
    w = trial(tmp_path,'t01', True)
    (w/'namespaces.yaml').write_text((w/'namespaces.yaml').read_text().replace('checkout-edge-v2','checkout-edge-v1'))
    assert not grade('t01',w)['passed']


def test_t02_only_psa_fix_fails(tmp_path):
    w = trial(tmp_path,'t02', True)
    shutil.copy(ROOT/'tasks/t02-secure-operator/initial/role.yaml', w/'role.yaml')
    r=grade('t02',w)
    assert not r['passed'] and not next(c for c in r['checks'] if c['name']=='rbac_minimal_namespaced_only')['passed']


def test_t02_wildcard_rbac_fails(tmp_path):
    w = trial(tmp_path,'t02',True)
    docs=list(yaml.safe_load_all((w/'role.yaml').read_text()))
    docs[0]['rules'][0]['verbs'].append('*')
    replace_yaml(w/'role.yaml', docs)
    assert not grade('t02',w)['passed']


def test_t02_privileged_bootstrap_fails(tmp_path):
    w=trial(tmp_path,'t02',True)
    docs=list(yaml.safe_load_all((w/'deployment.yaml').read_text()))
    docs[0]['spec']['template']['spec']['initContainers'][0]['securityContext']['runAsUser']=0
    replace_yaml(w/'deployment.yaml',docs)
    assert not grade('t02',w)['passed']


def test_t02_availability_constraint_fails(tmp_path):
    w=trial(tmp_path,'t02',True)
    docs=list(yaml.safe_load_all((w/'deployment.yaml').read_text()))
    docs[0]['spec']['strategy']['rollingUpdate']['maxUnavailable']=1
    replace_yaml(w/'deployment.yaml',docs)
    assert not grade('t02',w)['passed']


def test_t01_broad_egress_fails(tmp_path):
    w = trial(tmp_path, 't01', True)
    docs = list(yaml.safe_load_all((w/'networkpolicies.yaml').read_text()))
    docs[0]['spec']['egress'] = [{}]
    replace_yaml(w/'networkpolicies.yaml', docs)
    r = grade('t01', w)
    assert not r['passed'] and not next(c for c in r['checks'] if c['name']=='gateway_sandbox_egress_denied')['passed']


def test_t01_only_ingress_and_service_fixed_fails(tmp_path):
    w = trial(tmp_path, 't01', True)
    docs = list(yaml.safe_load_all((w/'networkpolicies.yaml').read_text()))
    old = list(yaml.safe_load_all((ROOT/'tasks/t01-checkout-routing/initial/networkpolicies.yaml').read_text()))
    docs[0] = old[0]
    replace_yaml(w/'networkpolicies.yaml', docs)
    r = grade('t01', w)
    assert not r['passed'] and not next(c for c in r['checks'] if c['name']=='gateway_api_access')['passed']


def test_t01_alternative_valid_service_selector_passes(tmp_path):
    w = trial(tmp_path, 't01', True)
    docs = list(yaml.safe_load_all((w/'services.yaml').read_text()))
    docs[0]['spec']['selector'] = {'app': 'payments-api'}
    replace_yaml(w/'services.yaml', docs)
    r = grade('t01', w)
    assert r['passed'], r


def test_t01_alternative_valid_namespace_expression_passes(tmp_path):
    w = trial(tmp_path, 't01', True)
    docs = list(yaml.safe_load_all((w/'networkpolicies.yaml').read_text()))
    egress = docs[0]['spec']['egress'][0]['to'][0]['namespaceSelector']
    egress.clear()
    egress['matchExpressions'] = [{'key':'security-zone','operator':'In','values':['payments-core']}]
    ingress = docs[1]['spec']['ingress'][0]['from'][0]['namespaceSelector']
    ingress.clear()
    ingress['matchExpressions'] = [{'key':'security-zone','operator':'In','values':['checkout-edge-v2']}]
    replace_yaml(w/'networkpolicies.yaml', docs)
    r = grade('t01', w)
    assert r['passed'], r


def test_t02_alternative_valid_split_roles_passes(tmp_path):
    w = trial(tmp_path, 't02', True)
    docs = list(yaml.safe_load_all((w/'role.yaml').read_text()))
    docs[0]['rules'] = [{'apiGroups':[''], 'resources':['configmaps'], 'verbs':[v]}
                         for v in ('get','list','watch')]
    replace_yaml(w/'role.yaml', docs)
    r = grade('t02', w)
    assert r['passed'], r
