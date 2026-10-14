#!/usr/bin/env python3
"""Regenerate LaTeX from existing recognition; never rerun OCR or retrieve source."""
import sys,json
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pdf2tex.importer import import_content
from pdf2tex.enrich import enrich
from pdf2tex.geometry import infer_layout
from pdf2tex.latex import emit_document
from pdf2tex.cli import compile_project
out=Path(sys.argv[1]);pdf=Path(sys.argv[2])
raw=next((out/'recognition').rglob('*_content_list.json'))
blocks,report=import_content(raw,out)
from pdf2tex.repair import apply_saved
apply_saved(blocks,out)
plan_path=out/'layout-plan.json'
plan=json.loads(plan_path.read_text()) if plan_path.exists() else None
if plan:
    import hashlib
    if plan['input_sha256']!=hashlib.sha256(pdf.read_bytes()).hexdigest():raise ValueError('Saved layout plan belongs to a different PDF')
    from pdf2tex.profiles import prepare_blocks
    blocks,ordering_warnings=prepare_blocks(blocks,plan)
enrich(blocks,pdf)
old=json.loads((out/'report.json').read_text());old.update(report)
old.update(backend='mineru_pipeline',source_lookup=False,input_policy='pdf_only',layout=plan['geometry'] if plan else infer_layout(pdf),counts=dict(Counter(b['type'] for b in blocks)),review_blocks=[b['id'] for b in blocks if b.get('review')])
emit_document(blocks,out/'main.tex',old['layout']);old['compile']=compile_project(out)
(out/'blocks.json').write_text(json.dumps(blocks,ensure_ascii=False,indent=2))
(out/'report.json').write_text(json.dumps(old,ensure_ascii=False,indent=2))
print(json.dumps({'counts':old['counts'],'compile':old['compile']},ensure_ascii=False))
