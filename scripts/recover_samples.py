#!/usr/bin/env python3
"""Recover and validate version-matched semantic source for the four fixtures."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from paperretex.evaluate import evaluate
p=argparse.ArgumentParser()
p.add_argument('--output',default='output/semantic-run')
a=p.parse_args();root=Path(a.output).resolve()
if root.exists() and any(root.iterdir()):raise SystemExit('Use a new output directory to protect edited source.')
root.mkdir(parents=True,exist_ok=True)
for name,prefix in [('EMS','2111.14517'),('Light-SQ','2509.24986'),('Kobsik','2607.05568'),('ResFit','2512.09201')]:
    pdf=next((ROOT/'samples').glob(prefix+'-*.pdf'));dest=root/name
    subprocess.run([sys.executable,'-m','paperretex','convert','--mode','source',str(pdf),'--mode','semantic','--compile','-o',str(dest)],cwd=ROOT,check=True)
    report=json.loads((dest/'report.json').read_text())
    visual=evaluate(pdf,dest/report['compile']['pdf'],root/'evaluation'/name)
    print(json.dumps({'name':name,'counts':report['counts'],'visual_pass':visual['passed'],'mean_page_mae_255':visual.get('mean_page_mae_255')},ensure_ascii=False),flush=True)
