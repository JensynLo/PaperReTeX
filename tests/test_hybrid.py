import copy
import json
from pathlib import Path
import fitz
import pytest
from PIL import Image
from pdf2tex.babeldoc import normalize_ir, text_of, source_blue
from pdf2tex.formula_review import validate_review, review_formulas
from pdf2tex.hybrid import apply_formulas, emit_regions, prepare_structure


def fixture_ir():
    def char(text, x, y=150):
        return {'char_unicode':text, 'box':{'x':x,'y':y,'x2':x+8,'y2':y+10},
                'pdf_style':{'font_id':'f1','font_size':10}}
    return {'page':[{'page_number':0, 'pdf_font':[{'font_id':'f1','name':'Times','bold':False,'italic':False}],
        'page_layout':[], 'pdf_paragraph':[{'box':{'x':30,'y':150,'x2':170,'y2':160},
            'layout_label':'plain text', 'unicode':'Let x denote a variable.',
            'pdf_paragraph_composition':[
                {'pdf_same_style_characters':{'pdf_character':[char('Let ',30)]}},
                {'pdf_formula':{'box':{'x':55,'y':150,'x2':65,'y2':160},'pdf_character':[char('x',55)]}},
                {'pdf_same_style_characters':{'pdf_character':[char(' denote a variable.',68)]}}]}]}]}


def test_babeldoc_coordinates_and_inline_formula(tmp_path):
    pdf=tmp_path/'input.pdf'
    with fitz.open() as doc:
        p=doc.new_page(width=200,height=200);p.insert_text((30,50),'Let x denote a variable.');doc.save(pdf)
    regions, formulas, warnings=normalize_ir(fixture_ir(),pdf,tmp_path)
    assert len(formulas)==1 and formulas[0]['kind']=='inline'
    assert formulas[0]['bbox']==[53.5,38.5,66.5,51.5]
    assert (tmp_path/formulas[0]['crop']).exists()
    assert regions[0]['formula_ids']==['f00001']
    apply_formulas(regions,formulas,{'f00001':{'id':'f00001','status':'corrected','latex':'x_i','notes':'subscript'}})
    assert regions[0]['tex']=='Let $x_i$ denote a variable.'
    assert formulas[0]['review']['status']=='corrected'


def test_isolated_formula_outside_paragraph_is_not_lost(tmp_path):
    pdf=tmp_path/'input.pdf'
    with fitz.open() as doc:
        p=doc.new_page(width=200,height=200);p.insert_text((30,90),'x = 1');doc.save(pdf)
    ir=fixture_ir()
    ir['page'][0]['page_layout']=[{'class_name':'isolate_formula',
                                  'box':{'x':30,'y':100,'x2':90,'y2':115}}]
    regions,formulas,_=normalize_ir(ir,pdf,tmp_path)
    assert [f['kind'] for f in formulas]==['inline','display']
    assert regions[-1]['type']=='equation'
    assert len({r['id'] for r in regions})==len(regions)


def test_review_cannot_omit_duplicate_or_inject():
    expected=[{'id':'a'},{'id':'b'}]
    row={'id':'a','status':'confirmed','latex':'x','notes':''}
    with pytest.raises(ValueError): validate_review({'formulas':[row]},expected)
    with pytest.raises(ValueError): validate_review({'formulas':[row,row]},expected)
    bad=dict(row,id='b',latex=r'\input{secrets}')
    with pytest.raises(ValueError): validate_review({'formulas':[row,bad]},expected)
    assert len(validate_review({'formulas':[row,dict(row,id='b',status='uncertain',latex='')]},expected))==2
    quarantined=validate_review({'formulas':[row,bad]},expected,quarantine=True)
    assert quarantined[1]['status']=='uncertain' and 'validation_error' in quarantined[1]


def test_uncertain_formula_is_visible_and_not_silently_accepted():
    regions=[{'id':'r','type':'paragraph','segments':[{'formula_id':'a'}]}]
    formulas=[{'id':'a','ocr_latex':'wrong'}]
    with pytest.raises(ValueError):apply_formulas(regions,formulas,{})
    apply_formulas(regions,formulas,{'a':{'id':'a','status':'uncertain','latex':'guess','notes':'blurred'}})
    assert 'guess' not in regions[0]['tex'] and 'wrong' not in regions[0]['tex']
    assert '[?]' in regions[0]['tex'] and regions[0]['review']
    assert regions[0]['unresolved_formulas']==['a']


def test_linked_citation_keeps_source_color_without_coloring_other_math():
    blue='0 g 0 G 0.210000 0.490000 0.740000 rg 0.210000 0.490000 0.740000 RG'
    black='0.210000 0.490000 0.740000 rg 0.210000 0.490000 0.740000 RG 0 g 0 G'
    glyph=lambda value,state:{'char_unicode':value,'pdf_style':{'graphic_state':{
        'passthrough_per_char_instruction':state}}}
    assert source_blue([glyph('[',black),glyph('12',blue),glyph(']',black)])
    assert not source_blue([glyph('x',black)])
    regions=[{'id':'r','type':'paragraph','segments':[{'formula_id':'a'},{'text':' and '},{'formula_id':'b'}]}]
    formulas=[{'id':'a','native_text':'[12]','source_blue':True},
              {'id':'b','native_text':'x','source_blue':True}]
    verdicts={'a':{'id':'a','status':'confirmed','latex':'[12]','notes':''},
              'b':{'id':'b','status':'confirmed','latex':'x','notes':''}}
    apply_formulas(regions,formulas,verdicts)
    assert r'{\color{sourceblue}$[12]$}' in regions[0]['tex']
    assert r'{\color{sourceblue}$x$}' not in regions[0]['tex']


def test_review_batches_cache_and_missing_ids_fail(tmp_path,monkeypatch):
    image=tmp_path/'crop.png';Image.new('RGB',(100,30),'white').save(image)
    formulas=[{'id':str(i),'page':1,'kind':'inline','crop':'crop.png','ocr_latex':'x'} for i in range(3)]
    calls=[]
    def run(command,**kwargs):
        prompt=kwargs['input'];payload=json.loads(prompt[prompt.index('[{'):])
        calls.append(payload)
        result=Path(command[command.index('--output-last-message')+1])
        result.write_text(json.dumps({'formulas':[{'id':r['id'],'status':'confirmed','latex':'x','notes':''} for r in payload]}))
        return type('Result',(),{'returncode':0})()
    monkeypatch.setattr('pdf2tex.formula_review.run_review',run)
    reviews,stats=review_formulas(formulas,tmp_path,batch_size=2)
    assert len(calls)==2 and stats['reviewed']==3
    reviews,stats=review_formulas(formulas,tmp_path,batch_size=2)
    assert len(calls)==2 and stats['cached_batches']==2
    formulas[0]['ocr_latex']='y'
    review_formulas(formulas,tmp_path,batch_size=2)
    assert len(calls)==3


def test_failed_review_recovers_only_complete_valid_result(tmp_path, monkeypatch):
    Image.new('RGB',(20,20),'white').save(tmp_path/'crop.png')
    formulas=[{'id':'a','crop':'crop.png','page':1,'kind':'inline'}]
    def run(command, **kwargs):
        result=Path(command[command.index('--output-last-message')+1])
        result.write_text(json.dumps({'formulas':[{'id':'a','status':'confirmed','latex':'x','notes':''}]}))
        return type('Result',(),{'returncode':0})()
    monkeypatch.setattr('pdf2tex.formula_review.run_review',run)
    review_formulas(formulas,tmp_path)
    ledger=next((tmp_path/'formula-review').glob('*/call.json'))
    state=json.loads(ledger.read_text());state.update(status='failed',error='old timeout')
    ledger.write_text(json.dumps(state))
    monkeypatch.setattr('pdf2tex.formula_review.run_review',lambda *a,**k:pytest.fail('Must reuse result'))
    _,stats=review_formulas(formulas,tmp_path)
    assert stats['cached_batches']==1 and json.loads(ledger.read_text())['result_recovered']
    state['status']='failed';ledger.write_text(json.dumps(state))
    (ledger.parent/'result.json').write_text('{"formulas": []}')
    with pytest.raises(ValueError,match='Incomplete'):
        review_formulas(formulas,tmp_path)


def test_timeout_stops_native_child_and_reports_progress(tmp_path, capsys):
    import os
    import subprocess
    import sys
    import time
    from pdf2tex.formula_review import run_review
    if os.name != 'posix':
        pytest.skip('POSIX process group test')
    # A launcher waiting for a child that would write after the deadline.
    marker=tmp_path/'escaped'
    child='import time,pathlib;time.sleep(1.2);pathlib.Path(%r).write_text("alive")' % str(marker)
    wrapper='import subprocess,sys;subprocess.run([sys.executable,"-c",%r])' % child
    started=time.monotonic()
    with (tmp_path/'codex.log').open('w') as log:
        with pytest.raises(subprocess.TimeoutExpired):
            run_review([sys.executable,'-c',wrapper],input='test',stdout=log,
                       cwd=tmp_path,timeout=0.3,progress_interval=0.1)
    assert time.monotonic()-started < 1.1
    time.sleep(1.3)
    assert not marker.exists()
    assert 'waiting' in capsys.readouterr().err


def test_review_runner_success(tmp_path):
    import sys
    from pdf2tex.formula_review import run_review
    with (tmp_path/'codex.log').open('w') as log:
        proc=run_review([sys.executable,'-c','import sys;print(sys.stdin.read())'],
                        input='payload',stdout=log,cwd=tmp_path,timeout=5)
    assert proc.returncode==0
    assert (tmp_path/'codex.log').read_text().strip()=='payload'


def test_timeout_retains_ledger_and_requires_explicit_retry(tmp_path, monkeypatch):
    import subprocess
    Image.new('RGB',(20,20),'white').save(tmp_path/'crop.png')
    formulas=[{'id':'a','crop':'crop.png','page':1,'kind':'inline'}]
    def timeout(command, **kwargs):
        kwargs['stdout'].write('ERROR: Reconnecting... waiting for network\n')
        raise subprocess.TimeoutExpired(command, 1)
    monkeypatch.setattr('pdf2tex.formula_review.run_review',timeout)
    with pytest.raises(RuntimeError,match='Network reconnection'):
        review_formulas(formulas,tmp_path,timeout=1)
    ledger=next((tmp_path/'formula-review').glob('*/call.json'))
    state=json.loads(ledger.read_text())
    assert state['status']=='failed' and state['attempts']==1
    assert state['elapsed_seconds']>=0 and state['finished_at']>=state['started_at']
    with pytest.raises(ValueError,match='Incomplete'):
        review_formulas(formulas,tmp_path)
    with pytest.raises(RuntimeError):
        review_formulas(formulas,tmp_path,timeout=1,retry=True)
    assert (ledger.parent/'codex-attempt-1.log').exists()
    assert json.loads(ledger.read_text())['attempts']==2


def test_region_files_assemble_normal_sections_and_equations(tmp_path):
    regions=[{'id':'p001-r0001','page':1,'type':'heading','text':'2 Method'},
             {'id':'p001-r0002','page':1,'type':'paragraph','text':'Let x be a variable.','tex':'Let $x$ be a variable.'},
             {'id':'p001-r0003','page':1,'type':'equation','latex':r'x=\frac12\tag{3}'}]
    emit_regions(regions,tmp_path,None)
    main=(tmp_path/'main.tex').read_text();flat=(tmp_path/'main-flat.tex').read_text()
    assert main.count(r'\input{regions/')==3
    assert r'\section{Method}' in (tmp_path/regions[0]['tex_file']).read_text()
    assert r'\begin{equation}' in flat and r'\tag{3}' in flat
    import re
    assembled=re.sub(r'\\input\{([^}]+)\}%\n',lambda m:(tmp_path/m[1]).read_text(),main)
    assert assembled==flat


def test_vector_assets_and_mislabeled_caption_keep_one_copy():
    def r(fid,typ,label,box,text='',asset=None):
        row={'id':fid,'page':1,'type':typ,'layout_label':label,'source_bbox':box,
             'bbox':[v*2 for v in box],'text':text,'column':'left','wide':False,
             'font_size':9,'bold':False,'format':{}}
        if asset:row['asset']=asset
        return row
    regions=[r('p001-table001','table_crop','table',[50,100,250,180],asset='assets/a.pdf'),
             r('p001-table002','table_crop','table',[51,101,250,180],asset='assets/b.pdf'),
             r('p001-r0001','paragraph','fallback_line',[70,120,120,130],'Method'),
             r('p001-r0002','paragraph','table_footnote',[50,185,250,195],'Table 1. Results'),
             r('p001-r0003','paragraph','table_caption',[50,205,250,218],'3.2. Experiments')]
    regions[-1]['bold']=True
    prepare_structure(regions,{})
    assert regions[1]['merged_into']==regions[0]['id']
    assert regions[2]['merged_into']==regions[0]['id']
    assert regions[3]['merged_into']==regions[0]['id']
    assert regions[0]['caption_regions']==[regions[3]]
    assert regions[4]['type']=='heading' and not regions[4].get('merged_into')


def test_column_frames_compile_across_region_inputs(tmp_path):
    import shutil
    import subprocess
    if not shutil.which('xelatex'):pytest.skip('XeLaTeX is unavailable')
    layout={'paper_width':400,'paper_height':600,'left':40,'right':40,'top':40,'bottom':40,
            'column_gap':18,'columns':1,'font_size':10,'line_height':12,
            'preserve_source_breaks':True,'source_column_frames':True}
    blocks=[{'id':'p001-r0001','page':1,'type':'heading','text':'1 Introduction',
             'source_bbox':[40,50,180,65],'column':'left','layout_columns':1},
            {'id':'p001-r0002','page':1,'type':'paragraph','text':'Hello world.',
             'tex':'Hello world.','source_bbox':[40,80,300,105],
             'column':'left','layout_columns':1},
            {'id':'p002-r0001','page':2,'type':'equation','latex':r'x=1\tag{1}',
             'source_bbox':[90,60,210,80],'column':'left','layout_columns':1}]
    emit_regions(blocks,tmp_path,layout)
    main=(tmp_path/'main.tex').read_text()
    assert (tmp_path/'main-flat.tex').read_text().count(r'\begin{sourceframe}')==2
    assert r'\section{Introduction}' in (tmp_path/blocks[0]['tex_file']).read_text()
    assert r'\begin{equation}' in (tmp_path/blocks[2]['tex_file']).read_text()
    proc=subprocess.run(['xelatex','-no-shell-escape','-interaction=nonstopmode','-halt-on-error','main.tex'],
                        cwd=tmp_path,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT,timeout=30)
    assert proc.returncode==0
    with fitz.open(tmp_path/'main.pdf') as pdf:assert len(pdf)==2


def test_default_routes_to_hybrid_without_source_lookup(monkeypatch,tmp_path):
    from pdf2tex.cli import main
    seen=[]
    monkeypatch.setattr('pdf2tex.hybrid.convert_hybrid',lambda args: seen.append(args.mode) or 0)
    monkeypatch.setattr('pdf2tex.cli.recover',lambda *a: pytest.fail('Must not fetch sources'))
    assert main(['convert','paper.pdf'])==0
    assert seen==['hybrid']


def test_hybrid_stage_order_resume_and_two_round_limit(tmp_path,monkeypatch):
    import hashlib
    import sys
    from pdf2tex.cli import main
    from pdf2tex.preflight import assess_layout
    pdf=tmp_path/'paper.pdf';out=tmp_path/'result';events=[]
    with fitz.open() as d:
        p=d.new_page(width=200,height=200);p.insert_text((30,50),'Let x denote a variable in this example.');d.save(pdf)
    monkeypatch.setattr('pdf2tex.hybrid.runtime',lambda *a:Path(sys.executable))
    def worker(python,module,args,log):
        events.append(module)
        if module=='babeldoc_worker':
            folder=Path(args[1]);(folder/'layout-ir.json').write_text(json.dumps(fixture_ir()))
            (folder/'provenance.json').write_text('{}')
        else:
            root=Path(args[0]);formulas=json.loads((root/'formulas.json').read_text())
            (root/'formula-ocr.json').write_text(json.dumps([{'id':f['id'],'ocr_latex':'x',
                'crop_sha256':hashlib.sha256((root/f['crop']).read_bytes()).hexdigest()} for f in formulas]))
    monkeypatch.setattr('pdf2tex.hybrid.worker',worker)
    def assess(*a,**kw):
        events.append('layout-assessment');return assess_layout(*a,**kw)
    monkeypatch.setattr('pdf2tex.hybrid.assess_layout',assess)
    def review(formulas,*args,**kwargs):
        events.append('context-review' if kwargs.get('context') else 'review')
        return ({f['id']:{'id':f['id'],'status':'uncertain','latex':'x','notes':'ambiguous'} for f in formulas},
                {'new_calls':1,'cached_batches':0,'reviewed':len(formulas),'uncertain':len(formulas)})
    monkeypatch.setattr('pdf2tex.hybrid.review_formulas',review)
    argv=['convert',str(pdf),'--layout-review','local','-o',str(out)]
    assert main(argv)==1
    assert events==['babeldoc_worker','layout-assessment','formula_ocr_worker','review','context-review']
    assert (out/'main.tex').exists()
    assert json.loads((out/'report.json').read_text())['formula_review']['rounds']==2
    assert '[?]' in (out/'main-flat.tex').read_text()
    assert '% Unresolved formulas: f00001' in (out/'main-flat.tex').read_text()
    state=json.loads((out/'pipeline.json').read_text())
    state.update(status='failed',error='previous timeout')
    (out/'pipeline.json').write_text(json.dumps(state))
    events.clear()
    assert main(argv+['--resume'])==1
    assert 'error' not in json.loads((out/'pipeline.json').read_text())
    assert events==['layout-assessment','review','context-review']
    assert main(argv+['--resume','--layout-profile','two_column'])==2
