"""Estimate a conventional document layout from PDF text, without TeX sources."""
from collections import Counter
import statistics
import fitz


def infer_layout(pdf):
    sizes=Counter();fonts=Counter(); leading=Counter(); boxes=[]; widths=[]; heights=[]; tops=[]; bottoms=[]; two=0; total=0
    with fitz.open(pdf) as doc:
        for page in doc:
            widths.append(page.rect.width);heights.append(page.rect.height)
            lines=[]
            for b in page.get_text('dict',flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks']:
                previous_baseline=None
                for line in b.get('lines',[]):
                    spans=[s for s in line['spans'] if len(s['text'].strip())>3]
                    if not spans:continue
                    for s in spans:
                        sizes[round(s['size'],1)]+=len(s['text'])
                        fonts[s['font']]+=len(s['text'])
                    r=line['bbox']
                    if len(''.join(s['text'] for s in spans))>35:
                        baseline=spans[0].get('origin',(0,r[3]))[1]
                        if previous_baseline is not None:
                            delta=baseline-previous_baseline
                            if spans[0]['size']*.95<delta<spans[0]['size']*1.7:
                                leading[round(delta,1)]+=1
                        previous_baseline=baseline
                    if len(''.join(s['text'] for s in spans))>35 and 30<r[1]<page.rect.height-30:
                        lines.append(r);boxes.append(r)
            if lines:
                tops.append(min(r[1] for r in lines));bottoms.append(max(r[3] for r in lines))
            left=sum(r[2]<page.rect.width*.53 for r in lines)
            right=sum(r[0]>page.rect.width*.45 for r in lines)
            two+=left>8 and right>8;total+=1
    w=statistics.median(widths);h=statistics.median(heights)
    columns=2 if two>total*.4 else 1
    xs=sorted(r[0] for r in boxes); ends=sorted(r[2] for r in boxes)
    margin_left=xs[int(len(xs)*.05)] if xs else 54
    margin_right=w-ends[int(len(ends)*.95)] if ends else 54
    top=sorted(tops)[int(len(tops)*.2)] if tops else 54
    bottom=h-sorted(bottoms)[int(len(bottoms)*.8)] if bottoms else 54
    gap=18
    if columns==2:
        right=[r[0] for r in boxes if r[0]>w*.45]
        left=[r[2] for r in boxes if r[2]<w*.53]
        if right and left:gap=max(10,min(35,statistics.median(right)-max(left)))
    return dict(paper_width=round(w,2),paper_height=round(h,2),columns=columns,
                font_size=sizes.most_common(1)[0][0] if sizes else 10,
                line_height=leading.most_common(1)[0][0] if leading else (sizes.most_common(1)[0][0]*1.2 if sizes else 12),
                body_font=fonts.most_common(1)[0][0] if fonts else 'Times',
                left=round(max(25,margin_left),2),right=round(max(25,margin_right),2),
                top=round(max(25,min(top,100)),2),bottom=round(max(25,min(bottom,100)),2),column_gap=round(gap,2))
