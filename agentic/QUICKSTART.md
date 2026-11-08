# Five-task agentic eval — minimal runbook

Run from the ORIGINAL project root in WSL, with your existing dedicated kind cluster and Calico already Ready.

## 1. One-time validation

```bash
cd ~/Projects/micro1-hackathon/kubernetes-incident-eval
source ../.venv/bin/activate
python -m pytest agentic/tests -q
python agentic/suite.py preflight
python agentic/suite.py smoke
```

`smoke` verifies all five injected incidents and reference recoveries on the LIVE cluster. It sequentially resets benchmark namespaces. This is essential before using model runs as evidence. It creates `agentic/results/oracle-smoke-report.json`.

## 2. Blind run against any one task

```bash
python agentic/suite.py setup t01 --trial ~/micro1-model-trials/t01-gpt6-01
cd ~/micro1-model-trials/t01-gpt6-01
```

Then open a terminal-capable AI agent **from that directory** and provide it only the following opening instruction:

> Read instruction.md. Diagnose and repair the LIVE kind-micro1-eval cluster with kubectl. Comply with all production and security requirements. Write diagnosis.json. Do not access the benchmark project, hidden reference solutions, or grading tools.

Make the CLI agent ask before destructive commands; do not accept access to filesystem directories containing reference answers. Record its full transcript and exact model version.

After the agent finishes:

```bash
cd ~/Projects/micro1-hackathon/kubernetes-incident-eval
python agentic/suite.py grade t01 --trial ~/micro1-model-trials/t01-gpt6-01
```

Never run oracle during a blind attempt; it leaks the answer. Grade and save the outcome before resetting. Repeat with new trial names and other task IDs.

## 3. Optional terminal agents (choose when needed; not required for validation)

- Codex CLI, from the official OpenAI guide: `curl -fsSL https://chatgpt.com/codex/install.sh | sh` then `codex`. Sign in with ChatGPT instead of API billing.
- Gemini CLI, official Google guide: `npx @google/gemini-cli` with Node.js 20+. Sign in with Google. No global installation needed, though npx may download and cache the package. Actual CLI model availability may differ from Gemini website models.

The regular Gemini/ChatGPT **website** does not get direct access to your local `kubectl` session. A web-only response is not an agentic live-recovery trial.

## 4. Draft packaging

```bash
python agentic/create_submission.py --trials ~/micro1-model-trials --output ~/micro1-draft.zip
```

After real scored experiments, reviewed analysis, and an accessible video link, see `python agentic/create_submission.py --help` for `--final --video-url ... --description ...`. The packager excludes credentials by using a file allowlist; independently inspect every included transcript to ensure it contains no private data.

**Research integrity:** local smoke test = author implementation validation. Only independent blind AI sessions count as model results. Save exact settings, date, time, costs and failed transcripts. Do not claim a model limitation based on untested or revised output.
