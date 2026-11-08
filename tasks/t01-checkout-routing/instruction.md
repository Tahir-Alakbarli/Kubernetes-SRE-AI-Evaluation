# Task 01 — Checkout authorization outage across Service and policy boundaries

You are the on-call Kubernetes SRE for a fictional checkout application. All manifests and logs in this task are **synthetic**. The read-only evidence in `evidence/observations.txt` and the object snapshots in `workspace/` are the complete information you have. Treat them as accurate at the incident timestamp. The object snapshots include `status` only as captured diagnostic state; they are not intended to be applied with `kubectl`.

The storefront's `checkout-gateway` can resolve `payments-api.payments-prod.svc` but can no longer authorize requests. The payment API's two replicas are Running and Ready. Historical database alerts are present. Identify **every independently necessary configuration repair** across routing and both sides of network isolation and restore the authorized request path without creating a broader trust boundary.

## What you can edit

Within `workspace/`, edit **only**:
- `services.yaml`
- `networkpolicies.yaml`

You must preserve the Service name, port 8443, target container port named `http-api` and protocol TCP. Keep all existing workloads, DNS traffic and audit metrics access working. The gateway must not gain generic egress to unrelated Pods or namespaces. You **must not** change namespaces, Pod labels, Pods, images, security-zone assignments, or eliminate isolation policies. Do not add broad ingress/egress allow rules. The checkout gateway alone (not the debug Pod in its namespace) should reach the payments API on TCP/8080 through the Service on TCP/8443. Audit agents from `audit-prod` must retain access to TCP/9090; sandbox traffic and frontend debug traffic to the payments API must remain denied. Do not grant checkout callers access to TCP/9090 or other backend ports.

Write `workspace/diagnosis.json` with this structure:
```
{
  "root_causes": ["Kind/namespace/name", "Kind/namespace/name", "Kind/namespace/name"],
  "explanation": "A concise technical root-cause explanation, including how the two faults interact.",
  "validation": "How you would verify the repair without weakening security."
}
```

The `root_causes` array must contain precisely the faulty Kubernetes resources (do not list healthy symptomatic resources). Use `Service`, `NetworkPolicy`, or other real Kind values. Your solution is judged by the corrected configuration and actual traffic-policy semantics, **not** by matching a particular YAML formatting or repair method. Both repairs and the diagnosis are required. During your attempt, read only the provided trial folder (its instruction, evidence and workspace). Edit only the permitted files within `workspace/`.
