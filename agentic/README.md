# micro1 Agentic Kubernetes SRE Evaluation — Local Live Cluster Suite

**Five candidate tasks, with live fault injection, reference repairs, post-incident scoring, and a clean reset for every trial.** This is a prototype; it is **not yet validated against live Kubernetes on the participant's machine or tested against frontier models**. The `smoke` command provides that essential reference verification. The original offline `tasks/`, `graders/`, and `run_eval.py` remain untouched.

## What changed

The earlier static-file t01 passed Gemini 3.1 Pro at 100/100. It does not demonstrate an AI limitation and is not included in this new 5-task candidate suite. The new tasks require the model to investigate and modify a running kind cluster with `kubectl`, not simply rewrite provided YAML.

| Task | Real engineering focus | C / A / B dimensions |
| --- | --- | --- |
| t01 | Cross-namespace migration, broken Service selection, dual ingress/egress policy drift and downstream Risk API routing | Identify all causes; restore both APIs; preserve isolation |
| t02 | ConfigMap desired-vs-running state, paused rollout, multiple replica consistency | Correct runtime state rather than ConfigMap alone; avoid downtime |
| t03 | Impossible node selection + required pod anti-affinity with 3 replicas on only 2 workers | Correct scheduling without moving to control plane or weakening PDB |
| t04 | Restricted admission, non-root init volume permissions, RBAC watch privileges | Fix rollout + least privilege without broad security exceptions |
| t05 | Additive allow policy silently defeating restrictive isolation + risk port mismatch | Recover service and close real bypass; avoid false security pass |

The selected limitations are: C = root-cause reasoning, A = completeness of repair, B = safe recovery. We do **not** claim any specific frontier model fails these until blinded runs demonstrate it.

## Prerequisites

- Existing kind cluster named `micro1-eval`; `kubectl config current-context` MUST be `kind-micro1-eval`.
- Three Ready nodes: `micro1-eval-control-plane`, `micro1-eval-worker`, `micro1-eval-worker2`.
- Calico CNI successfully enforcing NetworkPolicies. `kubectl get pods -n calico-system` should show ready controllers and nodes.
- Existing Python environment with `PyYAML` for `tests/test_structure.py`. The main `suite.py` uses Python standard library only.
- Images referenced in `agentic/manifests/` must be pullable: `hashicorp/http-echo:1.0.0`, `curlimages/curl:8.12.1`, `busybox:1.36`.
- A **dedicated disposable** kind cluster: `setup` and `smoke` DELETE and recreate ONLY the following namespaces: `storefront-prod`, `payments-prod`, `risk-prod`, `untrusted`, `worker-prod`, `ops-prod`. Never run on another context or a cluster containing valuable resources in these namespaces.
- Machine RAM: use sequential tasks, never load all tasks at the same time.

## One-time setup (using existing project)

Unzip the delivered overlay into your existing `~/Projects/micro1-hackathon/kubernetes-incident-eval` directory. It creates only `agentic/`.

```bash
cd ~/Projects/micro1-hackathon/kubernetes-incident-eval
source ../.venv/bin/activate  # if your virtual environment is in the parent directory
python agentic/suite.py preflight
python -m pytest agentic/tests -q
```

If the venv is in a different directory, activate that one; `suite.py` needs no extra pip packages. If you already have Python + PyYAML, you can run the structure tests with your existing environment.

### First validate ALL author reference solutions in one go

```bash
python agentic/suite.py smoke
```

This will reset the isolated cluster sequentially for each task, verify at least one pre-repair live check fails, apply the known-correct recovery, and require **100/100 on every task**. It writes `agentic/results/oracle-smoke-report.json` if successful. **It has not yet been executed in your WSL environment by the author.** Treat failures as benchmark bugs to fix, not AI failures. It can take several minutes due to image pulls and Kubernetes reconciliation.

### One independent model trial

Choose a fresh trial directory, outside the project tree, so the model sees no graders or reference files:

```bash
python agentic/suite.py setup t01 --trial ~/micro1-model-trials/t01-gemini-01
cd ~/micro1-model-trials/t01-gemini-01
```

The setup resets the dedicated Kubernetes cluster to a healthy baseline, verifies it, injects the chosen fault(s), and creates `instruction.md` and `diagnosis.json.example`. A terminal-capable model must inspect `instruction.md`, run `kubectl` to find/repair the problems, and write an actual `diagnosis.json` in this trial folder. Do not show the model the benchmark sources or correct answers, and do not run an author `oracle` command during a blind trial.

Use the same opening prompt for every model and trial:

> Read instruction.md. Use kubectl to diagnose and fully repair the LIVE kind-micro1-eval cluster. Follow every business, security, and availability requirement. Make changes directly in Kubernetes. Write diagnosis.json with the minimal root-causing resource IDs and your evidence. Do not read benchmark reference material or folders outside this trial directory.

After the model finishes, record its complete terminal transcript, model version, reasoning effort, time, and any prompts/approvals. **Then grade from a terminal outside the model session:**

```bash
cd ~/Projects/micro1-hackathon/kubernetes-incident-eval
python agentic/suite.py grade t01 --trial ~/micro1-model-trials/t01-gemini-01
```

Results are saved to `<trial>/grade.json`. Do **not** reset or apply an oracle repair until after grading and saving evidence. For independent repetitions use `setup` again with a new trial directory; each setup resets the cluster.

**Other tasks:** substitute `t02`, `t03`, `t04`, `t05`. `python agentic/suite.py list` shows the descriptions.

## Grading

- C / 30: minimum exact identified `root_causes` list as `Kind/namespace/name` or `Namespace/name`. Partial credit penalizes extras; full score requires the exact set.
- A / 35: observable service responses, all Pods/rollouts recovered, actual replica state and EndpointSlices (task-dependent).
- B / 35: network isolation from untrusted/debug Pods, absence of temporary allow policy, PodSecurity enforcement, RBAC least privilege, PDB and scheduling restrictions (task-dependent).
- **Full task pass:** exact root-cause identities AND every live check passes. Some checks are coarse composites by design and the raw report shows each top-level result.

Important limitations: The grader verifies a final live snapshot, not actual uninterrupted traffic throughout the repair; it is not a complete security audit. It cannot rule out every malicious workaround or confirm temporal properties such as zero downtime. References are separate from the blind prompt. Strengthen and independently audit the grader before asserting scientific-quality results.

## Results and submission

The participants' guide asks for ~5–10 clear tasks, reference solutions, multiple trials per frontier model, full outputs/transcripts, exact model IDs/settings, cost/runtime, and honest analysis. At least one frontier model and repeated trials are needed. Ideally use several different model providers with identical tasks. **Do not report fabricated model outcomes.**

For the HackerRank submission, include the task prompts, manifests, reference and grading implementation, validated oracle report, representative transcripts, measured model results, a complete README, and a video walkthrough link (≤5 minutes). Limit ZIP to 50 MB; never include `~/.kube/config`, API tokens, model login caches, personal data, or videos in ZIP.

## Provenance and research

New synthetic manifests, faults, prompt descriptions and validation scripts were drafted for this challenge and must be reviewed by the participant. The task themes are conceptually informed by ITBench-AA (https://artificialanalysis.ai/evaluations/itbench-aa), the 2026 CNCF Kubernetes source-repair experiment (https://www.cncf.io/blog/2026/05/08/benchmarking-ai-agent-retrieval-strategies-on-kubernetes-bug-fixes/), and the R2Act paper (https://arxiv.org/abs/2607.04623); the benchmark cases are not reproductions of those incidents. Disclose that AI tools assisted with scaffolding, manifests, prompts, code and grader; document your own checks, any corrections, live evidence and interpretation.

## Safe cleanup

`kind delete cluster --name micro1-eval` deletes only the disposable benchmark cluster. This is optional after submission and should only be run after all reports and transcripts are saved.
