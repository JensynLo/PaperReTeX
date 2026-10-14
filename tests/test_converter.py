import json
from pathlib import Path
import fitz
import pytest
from paperretex.cli import main,parse_pages
from paperretex.latex import safe_math,escape,rich_text
from paperretex.importer import import_content
from paperretex.native import order_blocks


def test_escape_and_math():
    assert escape('a_b & 30%') == r'a\_b \& 30\%'
    assert safe_math(r'$$\frac{x_1}{y^2}$$') == r'\frac{x_1}{y^2}'
    assert rich_text('energy $E=mc^2$ & mass') == r'energy $E=mc^2$ \& mass'

@pytest.mark.parametrize('value',[r'\input{/etc/passwd}',r'\write18{touch x}',r'\csname input\endcsname{x}',r'\begin{document}x\end{document}','x^^41','{x','x}'])
def test_reject_unsafe_math(value):
    with pytest.raises(ValueError): safe_math(value)


def test_pages():
    assert parse_pages('3,1-2,2') == [0,1,2]


def test_column_order():
    blocks = [{'id':n,'bbox':r} for n,r in [('right',[320,120,550,200]),('title',[50,10,550,30]),('left',[50,100,270,300])]]
    assert [b['id'] for b in order_blocks(blocks,600)] == ['title','left','right']


def test_native_and_protect_existing(tmp_path):
    pdf = tmp_path/'paper.pdf'
    with fitz.open() as d:
        p=d.new_page();p.insert_text((50,70),'A scientific paper',fontsize=18)
        p.insert_text((50,110),'Some recoverable text with 30% & a formula reference.')
        d.save(pdf)
    out=tmp_path/'result'
    assert main(['convert','--mode','reflow',str(pdf),'-o',str(out)]) == 0
    assert '30\\%' in (out/'main.tex').read_text()
    report=json.loads((out/'report.json').read_text())
    assert report['quality']=='review_draft'
    assert main(['convert','--mode','reflow',str(pdf),'-o',str(out)]) == 2


def test_scan_fallback_and_bad_page(tmp_path):
    pdf=tmp_path/'blank.pdf'
    with fitz.open() as d:d.new_page();d.save(pdf)
    out=tmp_path/'result'
    assert main(['convert','--mode','reflow',str(pdf),'-o',str(out)]) == 0
    assert json.loads((out/'blocks.json').read_text())[0]['type']=='page_crop'
    assert main(['convert','--mode','reflow',str(pdf),'--pages','2','-o',str(tmp_path/'bad')]) == 2
    assert not (tmp_path/'bad').exists()


def test_importer_and_traversal(tmp_path):
    path=tmp_path/'list.json'; out=tmp_path/'out';out.mkdir();(out/'assets').mkdir()
    path.write_text(json.dumps([{'type':'text','text':'Title','text_level':1,'page_idx':0},{'type':'equation','text':r'$$x=\frac{a}{b}$$','page_idx':0}]))
    blocks,report=import_content(path,out)
    assert blocks[1]['latex']==r'x=\frac{a}{b}'
    assert blocks[0]['type']=='heading'
    path.write_text(json.dumps([{'type':'image','img_path':'../private.png'}]))
    with pytest.raises(ValueError,match='inside'):import_content(path,out)


def test_codex_two_round_budget(tmp_path, monkeypatch):
    from paperretex.assist import run_round
    (tmp_path/'calls.json').write_text(json.dumps([{'round':1},{'round':2}]))
    with pytest.raises(ValueError,match='budget exhausted'):
        run_round(tmp_path,'Do not call the model',[])


def test_layout_retypesets_text_and_keeps_positions(tmp_path):
    import shutil
    import numpy as np
    from paperretex.layout import reconstruct
    from paperretex.cli import compile_project
    sample=Path(__file__).resolve().parents[1]/'samples/2111.14517-EMS.pdf'
    if not sample.exists() or not shutil.which('xelatex'):
        pytest.skip('Local sample PDF and XeLaTeX required for layout integration check')
    report=reconstruct(sample,tmp_path,[2])
    assert report['counts']['editable_glyphs']>3000
    assert len(report['embedded_formulas'])>=7
    assert compile_project(tmp_path)['status']=='passed'
    with fitz.open(sample) as source,fitz.open(tmp_path/'main.pdf') as rebuilt,fitz.open(tmp_path/'background.pdf') as background:
        # The real text must come from TeX, not an opaque copy of the source page.
        bg_visible=sum(len(s['chars']) for s in background[0].get_texttrace() if s['type']==0)
        tex_visible=sum(len(s['chars']) for s in rebuilt[0].get_texttrace() if s['type']==0 and s['font'].startswith('f'))
        assert bg_visible<100
        assert tex_visible>3000
        a=source[2].get_pixmap(dpi=100);b=rebuilt[0].get_pixmap(dpi=100)
        assert (a.width,a.height)==(b.width,b.height)
        error=np.abs(np.frombuffer(a.samples,np.uint8).astype(float)-np.frombuffer(b.samples,np.uint8))
        assert error.mean()<1.0


def test_rotated_glyph_baselines(tmp_path):
    import shutil
    import numpy as np
    from paperretex.layout import reconstruct
    from paperretex.cli import compile_project
    sample=Path(__file__).resolve().parents[1]/'samples/2607.05568-Kobsik-Primitive-Shape-Abstraction.pdf'
    if not sample.exists() or not shutil.which('xelatex'):
        pytest.skip('Local sample PDF and XeLaTeX required for rotation integration check')
    reconstruct(sample,tmp_path,[18])
    assert compile_project(tmp_path)['status']=='passed'
    with fitz.open(sample) as source,fitz.open(tmp_path/'main.pdf') as rebuilt:
        a=source[18].get_pixmap(dpi=100);b=rebuilt[0].get_pixmap(dpi=100)
        error=np.abs(np.frombuffer(a.samples,np.uint8).astype(float)-np.frombuffer(b.samples,np.uint8))
        assert error.mean()<.3
