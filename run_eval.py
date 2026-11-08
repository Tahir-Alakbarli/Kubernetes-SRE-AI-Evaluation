"""Local preparation, grading and reporting of manually or externally generated attempts.

No frontier model calls have been made by this scaffold; model outputs must be
recorded as separate trials and evaluated before reporting empirical findings.
"""
import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from graders.grade import grade, TASKS, ROOT


def prepare(task, name):
    if not name.replace('-', '').replace('_', '').isalnum():
        raise ValueError('trial name must use letters, digits, dash, or underscore')
    target = ROOT / 'runs' / f'{task}-{name}'
    if target.exists():
        raise FileExistsError(f'Existing trial: {target}')
    base = ROOT / 'tasks' / TASKS[task]
    (target / 'workspace').mkdir(parents=True)
    shutil.copytree(base / 'initial', target / 'workspace', dirs_exist_ok=True)
    shutil.copytree(base / 'evidence', target / 'evidence')
    shutil.copy(base / 'instruction.md', target / 'instruction.md')
    print(target)


def evaluate(task, trial_dir, model_name, output):
    trial_dir = Path(trial_dir).resolve()
    result = grade(task, trial_dir / 'workspace')
    result['model'] = model_name
    result['graded_at_utc'] = datetime.now(timezone.utc).isoformat()
    result['transcript_path'] = str(trial_dir / 'transcript.txt')
    destination = Path(output) if output else trial_dir / 'grade.json'
    destination.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'score': result['score'], 'passed': result['passed'],
                      'checks_passed':sum(x['passed'] for x in result['checks']),
                      'checks_total':len(result['checks']), 'report':str(destination),
                      'error':result['error']}, indent=2))


def main():
    p = argparse.ArgumentParser(description='Offline Kubernetes incident benchmark')
    sp = p.add_subparsers(dest='command', required=True)
    prep = sp.add_parser('prepare', help='Create isolated model-visible task workspace')
    prep.add_argument('task', choices=TASKS)
    prep.add_argument('--name', required=True)
    g = sp.add_parser('grade', help='Grade a modified attempt directory')
    g.add_argument('task', choices=TASKS)
    g.add_argument('trial_dir')
    g.add_argument('--model', default='unreported')
    g.add_argument('--output')
    args = p.parse_args()
    if args.command == 'prepare':
        prepare(args.task, args.name)
    elif args.command == 'grade':
        evaluate(args.task, args.trial_dir, args.model, args.output)


if __name__ == '__main__':
    main()
