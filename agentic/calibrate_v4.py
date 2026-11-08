import argparse
import json
import shutil
from pathlib import Path
import tempfile

import suite


def mutate(task):
    if task in ('t01', 't05'):
        namespace = 'payments-prod'
        name = 'payments-ingress'
        suite.patch('networkpolicy', name, namespace, {
            'spec': {
                'ingress': [{'from': [{'namespaceSelector': {}}], 'ports': [{'protocol': 'TCP', 'port': 8080}]}]
            }
        })
    elif task == 't02':
        suite.k('rollout', 'pause', 'deployment/payments-api', '-n', 'payments-prod')
    elif task == 't03':
        suite.patch('service', 'settlement-worker', 'worker-prod', {'spec': {'ports': [{'name': 'http', 'port': 8080, 'targetPort': 9999, 'protocol': 'TCP'}]}})
    elif task == 't04':
        suite.patch('role', 'watch-configmaps', 'ops-prod', {'rules': [{'apiGroups': [''], 'resources': ['configmaps', 'secrets'], 'verbs': ['get', 'list', 'watch']}]})
    else:
        raise ValueError(task)


def calibrate(tasks, output):
    suite.allowed_context()
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='micro1-v4-calibration-') as tmp:
        for task in tasks:
            trial = Path(tmp) / task
            suite.prepare(task, trial)
            if not suite.fast_fault_present(task):
                raise RuntimeError(f'{task}: injected fault markers were not observed')
            suite.oracle(task)
            (trial / 'diagnosis.json').write_text(json.dumps({'root_causes': sorted(suite.EXPECTED_CAUSES[task]), 'explanation': 'Validated reference recovery against live Kubernetes constraints.'}))
            reference_ok = suite.grade(task, trial, rounds=1)
            reference_report = json.loads((trial / 'grade.json').read_text())
            shutil.copy2(trial / 'grade.json', output / f'{task}-reference.json')
            if not reference_ok:
                raise RuntimeError(f'{task}: correct reference solution incorrectly rejected')
            mutate(task)
            mutation_ok = suite.grade(task, trial, rounds=1)
            mutation_report = json.loads((trial / 'grade.json').read_text())
            shutil.copy2(trial / 'grade.json', output / f'{task}-mutation.json')
            if mutation_ok:
                raise RuntimeError(f'{task}: unsafe or incomplete recovery falsely accepted')
            results.append({
                'task': task,
                'reference_pass': reference_ok,
                'reference_score': reference_report['total'],
                'mutation_pass': mutation_ok,
                'mutation_score': mutation_report['total'],
                'failed_after_mutation': [c['name'] for c in mutation_report['checks'] if not c['passed']],
            })
    (output / 'calibration-summary.json').write_text(json.dumps(results, indent=2) + '\n')
    print(f'Calibration passed for {len(results)} tasks; saved to {output}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', choices=suite.SCENARIOS)
    parser.add_argument('--output', default=str(suite.ROOT / 'results' / 'v4-calibration'))
    args = parser.parse_args()
    calibrate([args.task] if args.task else list(suite.SCENARIOS), Path(args.output).expanduser().resolve())


if __name__ == '__main__':
    main()
