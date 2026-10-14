"""Import MinerU Content List V1, without a hard dependency on its runtime."""
import json
import shutil
from pathlib import Path
from .latex import safe_math
from .tables import table_tex

def import_content(path, out):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,list): raise ValueError('Expected MinerU Content List V1: a JSON array.')
    blocks, warnings = [], []
    for i,item in enumerate(data):
        if not isinstance(item,dict): raise ValueError('Every content-list entry must be an object.')
        page = int(item.get('page_idx',0)) + 1
        typ = item.get('type','text')
        if typ == 'discarded':
            text=item.get('text','').strip()
            bb=item.get('bbox',[0,0,0,0])
            if not (page==1 and bb[1]>650 and text and 'arXiv:' not in text and len(text)>6):continue
            typ='footnote'
        b = {'id':f'p{page:03d}-b{i+1:04d}','page':page,'review':True,'provenance':'mineru_content_list'}
        if item.get('bbox'):
            b['bbox']=item['bbox']
            b['wide']=(item['bbox'][2]-item['bbox'][0])>550
        if typ=='footnote':
            b.update(type='footnote',text=item.get('text',''))
        elif typ == 'text':
            b.update(type='heading' if item.get('text_level',0) else 'paragraph',text=item.get('text',''),level=min(3,max(1,int(item.get('text_level',1)))))
        elif typ == 'equation':
            value=item.get('text','').strip()
            try:b.update(type='equation',latex=safe_math(value))
            except ValueError as exc:
                b.update(type='unresolved_equation',text=value)
                warnings.append({'page':page,'message':str(exc)})
        elif typ in ('image','table'):
            relative = Path(item.get('img_path',''))
            source = (path.parent/relative).resolve()
            if relative.is_absolute() or path.parent not in source.parents:
                raise ValueError('Asset path must remain inside the content-list directory.')
            if not source.is_file(): raise ValueError('Missing asset: ' + str(relative))
            if source.suffix.lower() not in {'.png','.jpg','.jpeg','.pdf'}:
                raise ValueError('Unsupported asset format: '+source.suffix)
            asset = f"assets/{b['id']}{source.suffix.lower()}"
            shutil.copyfile(source,out/asset)
            captions = item.get(typ+'_caption',[])
            if isinstance(captions,str): captions = [captions]
            b.update(type=typ+'_crop',asset=asset,caption=' '.join(captions),width=.9)
            if typ == 'table':
                latex=table_tex(item.get('table_body',''))
                if latex:b.update(type='table',latex=latex)
                else:warnings.append({'page':page,'message':'Invalid or missing table structure: retained image fallback.'})
        else:
            warnings.append({'page':page,'message':'Unsupported content type preserved as text: '+str(typ)})
            b.update(type='paragraph',text=json.dumps(item,ensure_ascii=False))
        blocks.append(b)
    return blocks,{'backend':'mineru_content_list','quality':'model_output_needs_review','warnings':warnings,'limitations':['OCR equations need mathematical review.','Tables with invalid or missing recognition remain explicit image fallbacks.']}
