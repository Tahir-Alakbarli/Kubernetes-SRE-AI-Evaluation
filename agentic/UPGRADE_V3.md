# Stricter research-based incidents

Install this overlay into the existing `kubernetes-incident-eval` project. It includes the earlier preflight fix, T04 injection fix, and blind-prompt upgrades. Do not rerun the old `upgrade_blind_v2.py` after applying this overlay.

Before restarting the disposable cluster, confirm that Docker Desktop and WSL are available. If the kind node containers were stopped with `docker stop`, restart only those three node containers.

```bash
cd ~/Projects/micro1-hackathon/kubernetes-incident-eval
docker start micro1-eval-control-plane micro1-eval-worker micro1-eval-worker2
kubectl get nodes
```

After the nodes and Calico become Ready:

```bash
source ../.venv/bin/activate
python -m pytest agentic/tests -q
python agentic/suite.py preflight
python agentic/suite.py smoke
```

The smoke command clears and recreates the named benchmark application namespaces inside the dedicated `kind-micro1-eval` cluster. It does not delete the kind cluster or Calico. Do not run it during a model attempt.

The `smoke` command must complete successfully before any blind model trial. Use a new trial folder for every model and repeat. Preserve the agent transcript, exact model/version, grade JSON, and failures. Do not treat the author reference smoke as model evidence.
