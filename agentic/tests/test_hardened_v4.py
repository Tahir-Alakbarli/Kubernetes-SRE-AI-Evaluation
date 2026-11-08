import json
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))
import hardened_v4 as strict


def pod(name, ip, labels=None):
    return {
        'metadata': {'name': name, 'labels': labels or {}},
        'status': {
            'phase': 'Running',
            'podIP': ip,
            'podIPs': [{'ip': ip}],
            'conditions': [{'type': 'Ready', 'status': 'True'}],
        },
    }


def response(code, payload=''):
    return SimpleNamespace(returncode=code, stdout=payload, stderr='')


def test_exact_diagnosis_full_credit():
    expected={'Service/a/svc', 'Deployment/a/api'}
    score=strict.diagnosis_score({'root_causes':sorted(expected),'explanation':'Service and deployment were both causally faulty.'},expected)
    assert score['complete'] and score['score']==30
    assert score['precision']==1 and score['recall']==1


def test_extra_cause_is_penalized():
    expected={'Service/a/svc','Deployment/a/api'}
    result=strict.diagnosis_score({'root_causes':sorted(expected | {'Pod/a/other'}),'explanation':'causes'},expected)
    assert result['score']<30 and not result['complete']
    assert result['extras']==['Pod/a/other']


def test_missing_cause_is_penalized():
    expected={'Service/a/svc','Deployment/a/api'}
    result=strict.diagnosis_score({'root_causes':['Service/a/svc'],'explanation':'causes'},expected)
    assert result['score']<30 and not result['complete']
    assert result['missing']==['Deployment/a/api']


def test_duplicates_do_not_pass():
    expected={'Service/a/svc'}
    result=strict.diagnosis_score({'root_causes':['Service/a/svc','Service/a/svc'],'explanation':'x'},expected)
    assert result['score']==30 and not result['complete']


def test_explanation_required_for_full_pass():
    expected={'Service/a/svc'}
    result=strict.diagnosis_score({'root_causes':['Service/a/svc']},expected)
    assert result['score']==30 and not result['complete']


def test_malformed_json_fields_fail_closed():
    assert strict.diagnosis_score({'root_causes':[{'name':'x'}]}, {'Pod/a/p'})['score']==0
    assert strict.diagnosis_score({'root_causes':'Pod/a/p'}, {'Pod/a/p'})['score']==0
    assert strict.diagnosis_score(None, {'Pod/a/p'})['score']==0


def test_ready_pods_reject_unready_and_missing():
    mocked=SimpleNamespace(pods=lambda *_:[pod('one','10.1.0.1')])
    found,_=strict.ready_pods(mocked,'test','app=x',2)
    assert not found
    unready=pod('one','10.1.0.1')
    unready['status']['conditions'][0]['status']='False'
    mocked.pods=lambda *_:[unready]
    found,_=strict.ready_pods(mocked,'test','app=x',1)
    assert not found


def test_endpoint_rejects_unmanaged_extra_pod():
    expected=[pod('api-1','10.2.0.2')]
    data={'items':[{'endpoints':[
        {'addresses':['10.2.0.2'],'conditions':{'ready':True},'targetRef':{'kind':'Pod','namespace':'a','name':'api-1'}},
        {'addresses':['10.2.0.3'],'conditions':{'ready':True},'targetRef':{'kind':'Pod','namespace':'a','name':'shadow'}},
    ]}]}
    mocked=SimpleNamespace(k=lambda *a,**kw:response(0,json.dumps(data)))
    good,_=strict.service_endpoints(mocked,'a','api',expected)
    assert not good


def test_endpoint_accepts_exact_pod_ownership():
    expected=[pod('api-1','10.2.0.2')]
    data={'items':[{'endpoints':[{'addresses':['10.2.0.2'],'conditions':{'ready':True},'targetRef':{'kind':'Pod','namespace':'a','name':'api-1'}}]}]}
    mocked=SimpleNamespace(k=lambda *a,**kw:response(0,json.dumps(data)))
    good,_=strict.service_endpoints(mocked,'a','api',expected)
    assert good


def test_canary_failure_cannot_count_as_security_success(monkeypatch):
    def fake_ready(suite,ns,label,count):
        return ([pod('canary','10.3.0.2')] if label=='app=verifier-canary' else [pod('client','10.3.0.3')]),{}
    monkeypatch.setattr(strict,'ready_pods',fake_ready)
    monkeypatch.setattr(strict,'exec_http',lambda *a,**kw:(28,'','timeout'))
    ok,details=strict.canary_control(SimpleNamespace(),2)
    assert not ok
    assert details['reason']=='untrusted_or_debug_client_cannot_reach_permitted_control'


def test_canary_requires_service_and_direct_ip(monkeypatch):
    observed=[]
    def fake_ready(suite,ns,label,count):
        return ([pod('canary','10.3.0.2')] if label=='app=verifier-canary' else [pod('client','10.3.0.3')]),{}
    def fake_http(suite,ns,podname,url,timeout=3):
        observed.append(url)
        return 0,'verifier-reachable',''
    monkeypatch.setattr(strict,'ready_pods',fake_ready)
    monkeypatch.setattr(strict,'exec_http',fake_http)
    ok,details=strict.canary_control(SimpleNamespace(),1)
    assert ok and details['attempts']==4
    assert any('10.3.0.2' in url for url in observed)
    assert any('verifier-canary.untrusted.svc' in url for url in observed)


def test_unauthorized_success_is_rejected(monkeypatch):
    def fake_ready(suite,ns,label,count):
        if ns=='payments-prod':
            return [pod(f'p{i}',f'10.4.0.{i}') for i in range(1,4)],{}
        if ns=='risk-prod':
            return [pod(f'r{i}',f'10.5.0.{i}') for i in range(1,3)],{}
        return [pod('client','10.6.0.1')],{}
    monkeypatch.setattr(strict,'ready_pods',fake_ready)
    monkeypatch.setattr(strict,'exec_http',lambda *a,**kw:(0,'payments-api healthy',''))
    ok,details=strict.probe_blocked(SimpleNamespace(),1)
    assert not ok
    assert details['reason']=='forbidden_path_not_blocked_or_probe_invalid'


def test_unauthorized_dns_failure_not_accepted(monkeypatch):
    def fake_ready(suite,ns,label,count):
        if ns=='payments-prod':
            return [pod(f'p{i}',f'10.4.0.{i}') for i in range(1,4)],{}
        if ns=='risk-prod':
            return [pod(f'r{i}',f'10.5.0.{i}') for i in range(1,3)],{}
        return [pod('client','10.6.0.1')],{}
    monkeypatch.setattr(strict,'ready_pods',fake_ready)
    monkeypatch.setattr(strict,'exec_http',lambda *a,**kw:(6,'','DNS lookup failed'))
    ok,_=strict.probe_blocked(SimpleNamespace(),1)
    assert not ok


def test_unauthorized_transport_denials_accepted(monkeypatch):
    def fake_ready(suite,ns,label,count):
        if ns=='payments-prod':
            return [pod(f'p{i}',f'10.4.0.{i}') for i in range(1,4)],{}
        if ns=='risk-prod':
            return [pod(f'r{i}',f'10.5.0.{i}') for i in range(1,3)],{}
        return [pod('client','10.6.0.1')],{}
    monkeypatch.setattr(strict,'ready_pods',fake_ready)
    monkeypatch.setattr(strict,'exec_http',lambda *a,**kw:(28,'','timeout'))
    ok,details=strict.probe_blocked(SimpleNamespace(),1)
    assert ok and details['attempts']==14


def test_allow_any_namespace_policy_rejected():
    payload={'items':[{'spec':{'policyTypes':['Ingress'],'ingress':[{'from':[{'namespaceSelector':{}}]}]}}]}
    mocked=SimpleNamespace(k=lambda *a,**kw:response(0,json.dumps(payload)))
    assert strict.weak_policies_present(mocked)


def test_denial_policy_not_confused_with_open_ingress():
    payload={'items':[{'spec':{'policyTypes':['Ingress'],'ingress':[]}}]}
    mocked=SimpleNamespace(k=lambda *a,**kw:response(0,json.dumps(payload)))
    assert not strict.weak_policies_present(mocked)


def test_read_only_cluster_checks_detect_missing_nodes():
    data={'items':[{'status':{'conditions':[{'type':'Ready','status':'True'}]}}]}
    mocked=SimpleNamespace(k=lambda *a,**kw:response(0,json.dumps(data)))
    assert not strict.cluster_ready(mocked)


def test_calico_daemonset_must_be_ready():
    mocked=SimpleNamespace(obj=lambda *a,**kw:{'status':{'desiredNumberScheduled':3,'numberReady':2}})
    assert not strict.calico_ready(mocked)
