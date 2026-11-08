#!/usr/bin/env bash
set -u

passed=0
failed=0

check() {
    if eval "$2" >/dev/null 2>&1; then
        echo "PASS: $1"
        ((passed+=1))
    else
        echo "FAIL: $1"
        ((failed+=1))
    fi
}

check "Payments API has 3 available replicas" \
  '[ "$(kubectl get deploy payments-api -n payments-prod -o jsonpath="{.status.availableReplicas}")" = "3" ]'

check "Checkout gateway has 2 available replicas" \
  '[ "$(kubectl get deploy checkout-gateway -n storefront-prod -o jsonpath="{.status.availableReplicas}")" = "2" ]'

check "Payments Service has ready endpoints" \
  '[ -n "$(kubectl get endpointslice -n payments-prod -l kubernetes.io/service-name=payments-api -o jsonpath="{.items[*].endpoints[?(@.conditions.ready==true)].addresses[*]}")" ]'

check "Checkout can reach Payments API" \
  'kubectl exec -n storefront-prod deploy/checkout-gateway -- curl -fsS --max-time 5 http://payments-api.payments-prod.svc.cluster.local:8443 | grep -q "payments-api healthy"'

check "Payments ingress policy exists" \
  'kubectl get networkpolicy payments-ingress -n payments-prod'

check "Checkout egress policy exists" \
  'kubectl get networkpolicy checkout-egress -n storefront-prod'

check "PodDisruptionBudget remains intact" \
  '[ "$(kubectl get pdb payments-api -n payments-prod -o jsonpath="{.spec.minAvailable}")" = "2" ]'

pod_ip=$(kubectl get pods -n payments-prod -l app=payments-api -o jsonpath='{.items[0].status.podIP}')
check "Untrusted namespace cannot directly access Payments Pods" \
  '[ -n "$pod_ip" ] && ! kubectl exec -n untrusted deploy/untrusted-client -- curl -fsS --max-time 3 "http://${pod_ip}:8080"'

echo
echo "RESULT: $passed passed, $failed failed"
[ "$failed" -eq 0 ]
