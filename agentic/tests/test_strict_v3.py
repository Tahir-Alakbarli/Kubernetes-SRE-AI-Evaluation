import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
import yaml

ROOT=Path(__file__).parents[1]
SPEC=importlib.util.spec_from_file_location('strict_suite',ROOT/'suite.py')
SUITE=importlib.util.module_from_spec(SPEC)
sys.modules['strict_suite']=SUITE
SPEC.loader.exec_module(SUITE)


def test_source_based_incidents_change_quota_and_cross_node_traffic():
    source=(ROOT/'suite.py').read_text()
    assert "'internalTrafficPolicy':'Local'" in source
    assert "'limits.memory':'288Mi'" in source
    assert "'limits.memory':'192Mi'" in source
    assert 'Pod/risk-prod/risk-legacy-shadow' in source


def test_correct_resource_quota_present_in_baseline():
    files={'scheduling.yaml':('worker-prod','settlement-budget','384Mi'),
           'restricted-ops.yaml':('ops-prod','ops-budget','288Mi')}
    for filename,(namespace,name,limit) in files.items():
        items=list(yaml.safe_load_all((ROOT/'manifests'/filename).read_text()))
        quota=next(x for x in items if x.get('kind')=='ResourceQuota')
        assert quota['metadata']['namespace']==namespace
        assert quota['metadata']['name']==name
        assert quota['spec']['hard']['limits.memory']==limit


def test_backend_and_gateway_nodes_differ():
    items=list(yaml.safe_load_all((ROOT/'manifests'/'base.yaml').read_text()))
    by_name={x['metadata']['name']:x for x in items if x.get('kind')=='Deployment'}
    node=lambda n:by_name[n]['spec']['template']['spec']['nodeSelector']['kubernetes.io/hostname']
    assert node('risk-api')=='micro1-eval-worker'
    assert node('checkout-gateway')=='micro1-eval-worker2'


def test_unsafe_global_policy_is_rejected(monkeypatch):
    policies={'items':[{'spec':{'ingress':[{'from':[{'namespaceSelector':{}}]}]}}]}
    monkeypatch.setattr(SUITE,'k',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(policies)))
    assert not SUITE.safe_ingress_rules('payments-prod')


def test_unsafe_allow_all_ipblock_is_rejected(monkeypatch):
    policies={'items':[{'spec':{'ingress':[{'from':[{'ipBlock':{'cidr':'0.0.0.0/0'}}]}]}}]}
    monkeypatch.setattr(SUITE,'k',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(policies)))
    assert not SUITE.safe_ingress_rules('payments-prod')


def test_restricted_policy_is_accepted(monkeypatch):
    policies={'items':[{'spec':{'ingress':[{'from':[{'namespaceSelector':{'matchLabels':{'security-zone':'checkout-edge-v3'}},'podSelector':{'matchLabels':{'app':'checkout-gateway'}}}]}]}}]}
    monkeypatch.setattr(SUITE,'k',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(policies)))
    assert SUITE.safe_ingress_rules('payments-prod')


def test_unavailable_probe_client_never_passes_security(monkeypatch):
    monkeypatch.setattr(SUITE,'ready_ips',lambda *a:['10.0.0.4','10.0.0.5'])
    monkeypatch.setattr(SUITE,'curl',lambda *a,**kw:SimpleNamespace(returncode=0))
    monkeypatch.setattr(SUITE,'obj',lambda *a,**kw:{'status':{'availableReplicas':0}})
    assert not SUITE.pod_isolation('risk-prod','app=risk-api')


def test_all_untrusted_pod_addresses_are_tested(monkeypatch):
    seen=[]
    def fake_curl(ns,src,url,timeout=4):
        seen.append((ns,url))
        return SimpleNamespace(returncode=28 if ns!='storefront-prod' or src=='debug-client' else 0)
    monkeypatch.setattr(SUITE,'ready_ips',lambda *a:['10.0.0.4','10.0.0.5'])
    monkeypatch.setattr(SUITE,'curl',fake_curl)
    monkeypatch.setattr(SUITE,'obj',lambda *a,**kw:{'status':{'availableReplicas':1}})
    assert SUITE.pod_isolation('risk-prod','app=risk-api')
    assert ('untrusted','http://10.0.0.4:8080') in seen
    assert ('untrusted','http://10.0.0.5:8080') in seen


def test_negative_probe_must_fail_when_access_is_open(monkeypatch):
    monkeypatch.setattr(SUITE,'ready_ips',lambda *a:['10.0.0.4','10.0.0.5'])
    monkeypatch.setattr(SUITE,'curl',lambda *a,**kw:SimpleNamespace(returncode=0))
    monkeypatch.setattr(SUITE,'obj',lambda *a,**kw:{'status':{'availableReplicas':1}})
    assert not SUITE.pod_isolation('risk-prod','app=risk-api')


def test_transport_error_cannot_pass_as_security_denial(monkeypatch):
    def fake_curl(ns,src,url,timeout=4):
        return SimpleNamespace(returncode=6 if ns=='untrusted' else 0)
    monkeypatch.setattr(SUITE,'ready_ips',lambda *a:['10.0.0.4','10.0.0.5'])
    monkeypatch.setattr(SUITE,'curl',fake_curl)
    monkeypatch.setattr(SUITE,'obj',lambda *a,**kw:{'status':{'availableReplicas':1}})
    assert not SUITE.pod_isolation('risk-prod','app=risk-api')


def test_unmodified_task_prompt_cannot_name_quota_causes():
    for tid in ('t03','t04'):
        text=(ROOT/'prompts'/f'{tid}.md').read_text()
        for cause in SUITE.EXPECTED_CAUSES[tid]:
            assert cause not in text


def test_correct_diagnosis_cannot_override_a_failed_recovery(tmp_path,monkeypatch):
    tid='t03'
    (tmp_path/'diagnosis.json').write_text(json.dumps({'root_causes':sorted(SUITE.EXPECTED_CAUSES[tid])}))
    monkeypatch.setattr(SUITE,'allowed_context',lambda:None)
    monkeypatch.setattr(SUITE,'snapshot',lambda *a:None)
    monkeypatch.setattr(SUITE,'task_checks',lambda _: [SUITE.Check('A','network',False),SUITE.Check('B','safety',True)])
    monkeypatch.setattr(SUITE.hardened_v4,'checks',lambda *args,**kwargs: [])
    assert SUITE.grade(tid,tmp_path) is False
