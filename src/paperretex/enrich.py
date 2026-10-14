"""Recover typography and printed equation labels from the input PDF only."""
import re
import statistics
from collections import Counter
import fitz


def enrich(blocks,pdf):
    with fitz.open(pdf) as doc:
        cache={}
        for b in blocks:
            pno=b['page']-1
            if pno<0 or pno>=len(doc) or not b.get('bbox'):continue
            page=doc[pno]
            if pno not in cache:
                data=page.get_text('dict',flags=(fitz.TEXTFLAGS_DICT|fitz.TEXT_IGNORE_ACTUALTEXT)&~fitz.TEXT_PRESERVE_IMAGES)
                cache[pno]=[line for block in data['blocks'] for line in block.get('lines',[])]
            lines=cache[pno]
            # Model exports normalize coordinates to [0,1000].
            box=fitz.Rect(b['bbox'][0]*page.rect.width/1000,b['bbox'][1]*page.rect.height/1000,b['bbox'][2]*page.rect.width/1000,b['bbox'][3]*page.rect.height/1000)
            spans=[s for l in lines for s in l['spans'] if box.contains(fitz.Point((s['bbox'][0]+s['bbox'][2])/2,(s['bbox'][1]+s['bbox'][3])/2))]
            if spans:
                sizes=Counter()
                for s in spans:sizes[round(s['size'],1)]+=len(s['text'])
                b['font_size']=sizes.most_common(1)[0][0]
                n=sum(len(s['text']) for s in spans)
                b['font_name']=Counter(s['font'] for s in spans).most_common(1)[0][0]
                if b['type'] in ('paragraph','heading') and n:
                    b['italic']=sum(len(s['text']) for s in spans if s['flags']&2)>.7*n
                    b['bold']=sum(len(s['text']) for s in spans if s['flags']&16)>.7*n
            if b['type']=='equation':
                candidates=[]
                for l in lines:
                    text=''.join(s['text'] for s in l['spans']).strip()
                    m=re.fullmatch(r'\(\s*(\d+[a-z]?)\s*\)',text)
                    if not m:continue
                    r=fitz.Rect(l['bbox']);dy=abs((r.y0+r.y1-box.y0-box.y1)/2)
                    if dy<max(12,box.height*.65) and r.x0>=box.x1-5 and r.x0-box.x1<page.rect.width*.3:
                        candidates.append((dy+abs(r.x0-box.x1)*.02,m[1],r.y0))
                if candidates:
                    b['number']=min(candidates)[1]
                    numbers=list(dict.fromkeys(c[1] for c in sorted(candidates,key=lambda c:c[2])))
                    rows=equation_rows(b['latex'])
                    if len(numbers)>1 and len(numbers)==len(rows):
                        b['equation_rows']=[{'latex':v,'number':n} for v,n in zip(rows,numbers)]
        # Title/author front matter remains full width, while the body flows in columns.
        if blocks and blocks[0]['type']=='heading':
            for b in blocks[1:]:
                if b['page']!=blocks[0]['page'] or b['type']!='paragraph':break
                b['role']='frontmatter'
                page=doc[b['page']-1];bb=b.get('bbox')
                if bb:
                    box=fitz.Rect(bb[0]*page.rect.width/1000,bb[1]*page.rect.height/1000,bb[2]*page.rect.width/1000,bb[3]*page.rect.height/1000)
                    selected=[l for l in cache[b['page']-1] if box.contains(fitz.Point((l['bbox'][0]+l['bbox'][2])/2,(l['bbox'][1]+l['bbox'][3])/2))]
                    rows=[]
                    for line in sorted(selected,key=lambda l:(l['bbox'][1],l['bbox'][0])):
                        cy=(line['bbox'][1]+line['bbox'][3])/2
                        group=next((r for r in rows if abs(r[0]-cy)<4),None)
                        if group is None:group=[cy,[]];rows.append(group)
                        group[1].append(line)
                    b['frontmatter_rows']=[[''.join(s['text'] for s in line['spans']) for line in sorted(row,key=lambda l:l['bbox'][0])] for _,row in rows]
                    b.pop('frontmatter_lines',None)
        # Wide teaser figures and wide abstracts precede the two-column body.
        index=1
        while index<len(blocks) and blocks[index].get('role')=='frontmatter':index+=1
        while index<len(blocks) and blocks[index]['page']==blocks[0]['page']:
            b=blocks[index]
            if b['type'] in ('image_crop','figure_crop') and b.get('wide'):
                b['role']='frontmatter_figure';index+=1;continue
            if b['type']=='heading' and b.get('text','').strip().lower()=='abstract' and index+1<len(blocks) and blocks[index+1].get('wide'):
                b['role']='frontmatter_abstract_heading';index+=1
                while index<len(blocks) and blocks[index]['page']==b['page'] and blocks[index]['type']=='paragraph':
                    blocks[index]['role']='frontmatter_abstract';index+=1
                continue
            break
    return blocks


def equation_rows(value):
    """Split only top-level rows of a recognized outer math array."""
    match=re.match(r'\s*\\begin\{array\}\s*\{[^{}]*\}(.*)\\end\{array\}\s*$',value,re.S)
    if not match:return []
    text=match[1];rows=[];depth=0;start=0;i=0
    while i<len(text):
        if text[i]=='\\':
            if text[i:i+2]=='\\\\' and depth==0:
                rows.append(text[start:i].strip());i+=2;start=i;continue
            if i+1<len(text) and text[i+1] in '{}\\':i+=2;continue
        if text[i]=='{':depth+=1
        elif text[i]=='}':depth-=1
        i+=1
    rows.append(text[start:].strip())
    return rows
