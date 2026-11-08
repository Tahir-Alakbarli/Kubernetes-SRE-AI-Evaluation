# Task 02 — Secure operator rollout with an authorization failure

You are the SRE for a fictional config-watcher operator. All manifests and evidence are **synthetic**. The snapshot in `workspace/` and `evidence/observations.txt` is complete for the task. The new controller release needs to observe ConfigMaps labeled `managed=true` in `ops-prod` through a Kubernetes API list/watch reflector and then serve a readiness endpoint. The container image named in the Deployment is illustrative; no image registry access is required for the offline challenge.

The namespace enforces Pod Security Admission at the `restricted` level. The rollout has stalled while the old revision is still available. An authorized canary diagnostic also shows that simply making the Pod admissible would not restore controller readiness.

## Required recovery

- Bring the new revision into compliance with the **restricted** Pod Security Admission profile without weakening the namespace label or switching off security controls. The `bootstrap` initContainer must still write to `/work/bootstrap.state` via the existing shared `emptyDir` volume and the controller must still access the same volume.
- Restore the controller's minimum namespace-scoped ConfigMap permissions for the required list/watch mechanism; preserve its `get` ability. No cluster-scoped role, resource/verb wildcard, secret access, or broader namespace permissions.
- Preserve the workload identity, replicas=3, the 0-unavailable rolling strategy, existing images, containers, env vars, health probe, resource requests/limits and RoleBinding. Do not change anything outside the affected deployment and Role.

Within `workspace/`, edit **only**:
- `deployment.yaml`
- `role.yaml`

Write `workspace/diagnosis.json` in this exact top-level shape:
```
{
  "root_causes": ["Kind/namespace/name", "Kind/namespace/name"],
  "explanation": "Why admission fails and why rollout readiness would still fail after admission is repaired.",
  "validation": "How to verify admission, readiness, least privilege, and zero-downtime rollout constraints."
}
```

List precisely the resources whose configurations must be repaired. The grader evaluates the security and RBAC properties of the final manifests, not a hard-coded text patch. Assume the existing recorded observations are trustworthy. During your attempt, read only the provided trial folder (its instruction, evidence and workspace). Edit only the permitted files within `workspace/`.
