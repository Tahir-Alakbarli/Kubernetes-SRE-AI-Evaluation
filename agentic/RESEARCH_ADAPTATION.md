# Research basis and evaluation upgrades

## Source research

This update draws on the experimental methodology and fault mechanisms in the MIT-licensed SREGym project. The primary public sources are:

- SREGym source: https://github.com/SREGym/SREGym
- SREGym failure-mechanism catalog: https://github.com/SREGym/SREGym/blob/main/Problem%20List.md
- Comparative incident experiment: https://www.sregym.com/blog/jev-sregym-lite
- Problem engineering and adversarial grading: https://www.sregym.com/blog/postmortems-to-sre-benchmarks
- SREGym paper: https://arxiv.org/abs/2605.07161

The comparative experiment reported five attempts per incident using Codex and GPT-5.6 Luna, with these no-Jev success rates:

| Related SREGym fault | Successes / attempts |
| --- | ---: |
| `namespace_memory_limit` | 0/5 |
| `rolling_update_misconfigured_social_network` | 0/5 |
| `internal_traffic_policy_local_astronomy_shop` | 0/5 |
| `network_policy_block` | 1/5 |
| `edge_request_filter_cpu_saturation` | 2/5 |

Those are source results for a different environment and model. They are not results of this benchmark and must not be reported as such. This update does not implement the CPU-saturation problem.

## What was adapted

- T01 combines a node-local Service traffic policy, a bad Service port, cross-namespace security drift, and a separately managed legacy endpoint. The topology is explicitly exercised from a different node.
- T03 combines namespace memory-quota exhaustion with unsatisfiable Pod placement rules and a protected rolling update. The incident remains solvable inside a stated resource ceiling.
- T04 combines a Restricted Pod Security admission problem, insufficient namespace quota headroom, and incomplete controller RBAC. The reference path preserves both privilege restrictions and controller availability.
- T02 and T05 retain the existing benchmark's configuration propagation and tenant isolation mechanisms, but participate in the stronger cross-task security checks.

These are local derivative scenarios informed by SREGym research. The SREGym code itself is not represented as independently authored. Existing benchmark work and AI assistance must be disclosed in the final submission.

## Grading upgrades

- Every scenario is checked against a live kind Kubernetes cluster.
- Cross-node client-to-Service traffic is tested rather than inferring correctness from ready Pod counts.
- NetworkPolicy enforcement is assessed using approved and unauthorized clients against every Ready backend Pod.
- Unauthorized clients must themselves be healthy; an unresolvable destination or absent client does not count as isolation.
- Dangerous all-namespace or all-IP ingress policies fail even when one negative probe times out.
- Correct quota headroom is required for protected rollouts, rather than accepting an arbitrary quota increase or a deployment scale-down.
- Each injected fault receives a pre-oracle presence check in the smoke test.
- The diagnosis must contain the exact minimal resource set; correct diagnosis cannot override a failed live recovery.

## Validation requirements

Local unit tests verify structure and deliberately unsafe results. They do not establish that a live cluster is correct. Before collecting model scores, run the full smoke test, inspect the reference passes, and rerun every scenario from a clean state. A grader cannot be asserted to have zero false passes or false fails without independent adversarial testing on the actual cluster.

A further limitation is that these are small synthetic applications and their short probes do not prove zero downtime or security against every possible packet path. Claims about Kubernetes correctness should refer to the specifically observed properties, not comprehensive production assurance.
