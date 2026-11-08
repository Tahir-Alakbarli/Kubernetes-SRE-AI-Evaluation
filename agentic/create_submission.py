#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT=Path(__file__).resolve().parent
SAFE_SOURCE=['README.md','suite.py','create_submission.py']
TRIAL_MAIN=['instruction.md','diagnosis.json','trial_metadata.json','grade.json','terminal-transcript.txt','notes.txt']
MAX=50*1024*1024


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--trials',type=Path,help='Parent directory containing finished model trial folders')
    p.add_argument('--video-url',default='',help='Public, accessible video URL; required for final submission')
    p.add_argument('--description',default='',help='Participant-authored summary of actual findings; required for final submission')
    p.add_argument('--final',action='store_true',help='Refuse to package missing real artifacts')
    p.add_argument('--output',type=Path,default=ROOT.parent/'micro1-hackathon-submission-draft.zip')
    a=p.parse_args()
    if a.final and (not a.video_url.startswith('https://') or not a.description.strip()):
        p.error('--final requires --video-url and --description')
    trials=[]
    if a.trials and a.trials.exists():
        trials=[x for x in sorted(a.trials.iterdir()) if x.is_dir() and (x/'grade.json').exists()]
    if a.final and not trials:
        p.error('Cannot finalize without scored model trials')
    if a.final and not (ROOT/'results/oracle-smoke-report.json').exists():
        p.error('Cannot finalize without actual live oracle smoke report')
    content=(ROOT/'README.md').read_text()
    content += '\n## Submission description and video\n\n'
    content += f'**Short description:** {a.description or "TO BE WRITTEN FROM MEASURED MODEL RESULTS"}\n\n'
    content += f'**Video walkthrough:** {a.video_url or "TO BE ADDED"}\n\n'
    content += '**Reminder:** review AI-assisted material and verify every benchmark result independently.\n'
    a.output=a.output.expanduser().resolve()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(a.output,'w',ZIP_DEFLATED) as z:
        z.writestr('README.md',content)
        for name in SAFE_SOURCE[1:]: z.write(ROOT/name,name)
        for subdir in ('prompts','manifests','tests','results'):
            folder=ROOT/subdir
            if folder.exists():
                for file in folder.rglob('*'):
                    if file.is_file() and file.suffix in ('.py','.yaml','.md','.json') and '__pycache__' not in file.parts and '.pytest_cache' not in file.parts:
                        z.write(file,Path(subdir)/file.relative_to(folder))
        for trial in trials:
            for name in TRIAL_MAIN:
                if (trial/name).exists(): z.write(trial/name,Path('trials')/trial.name/name)
            for file in (trial/'snapshots').glob('*.txt') if (trial/'snapshots').exists() else []:
                z.write(file,Path('trials')/trial.name/'snapshots'/file.name)
    if a.output.stat().st_size > MAX:
        a.output.unlink()
        raise SystemExit('ZIP exceeds 50 MB. Remove oversized transcripts or attachments and retry.')
    with ZipFile(a.output) as z:
        print('Created:',a.output)
        print('Entries:',len(z.namelist()),'Size:',round(a.output.stat().st_size/1024,1),'KB')
        print('Model trials included:',len(trials))
        if not a.final: print('DRAFT: Not a finished submission; add video, verified results and final human review.')

if __name__=='__main__': main()
