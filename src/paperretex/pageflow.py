"""Page and column frames around ordinary, editable LaTeX content.

The PDF's regions determine the column bands. Individual glyphs are never
positioned and formulas remain normal equation environments inside each frame.
"""
from collections import defaultdict
import re


PREAMBLE = r'''
\newsavebox{\sourceframebox}
\newenvironment{sourceframe}[3]{%
  \def\sourceframeheight{#1}\def\sourceframewidth{#2}\def\sourceframelabel{#3}%
  \begin{lrbox}{\sourceframebox}\begin{minipage}[t]{#2bp}\sourcebodyfont\vspace{0pt}%
}{%
  \par\end{minipage}\end{lrbox}%
  \typeout{PAPERRETEX-FRAME \sourceframelabel\space natural=\the\dimexpr\ht\sourceframebox+\dp\sourceframebox\relax limit=\sourceframeheight bp}%
  \vtop to \sourceframeheight bp{\vskip0pt\hbox{%
    \ifdim\dimexpr\ht\sourceframebox+\dp\sourceframebox\relax>\sourceframeheight bp%
      \resizebox*{\sourceframewidth bp}{\sourceframeheight bp}{\usebox{\sourceframebox}}%
    \else\usebox{\sourceframebox}\fi}%
    \vfil}%
}
'''


def frame_fit(log):
    """Measure every fitted column; make substantial vertical scaling visible."""
    entries=[]
    for label,natural,limit in re.findall(
            r'PAPERRETEX-FRAME\s+(\S+)\s+natural=([\d.]+)ptlimit=([\d.]+)bp',log):
        ratio=float(natural)/(float(limit)*72.27/72) if float(limit)>0 else 0
        entries.append({'frame':label,'natural_to_limit':round(ratio,4)})
    return {'frames':len(entries),
            'scaled':sum(x['natural_to_limit']>1 for x in entries),
            'significant': [x for x in entries if x['natural_to_limit']>1.03],
            'max_natural_to_limit':max((x['natural_to_limit'] for x in entries),default=0)}


def _unwrap(value):
    value=re.sub(r'\\sourcepagebreak\{\d+\}\s*', '', value)
    value=value.replace('\\sourcecolumnbreak\n','')
    # These wrappers are generated locally, never read from the source PDF.
    value=re.sub(r'\\twocolumn\[\{(.*?)\}\]\n',r'\1\n',value,flags=re.S)
    value=value.replace(r'\begin{strip}','').replace(r'\end{strip}','')
    # The following band supplies the source gap outside the spanning frame.
    value=re.sub(r'\\vspace\{[\d.]+bp\}\s*$', '', value)
    return value


def frame_pages(tex, blocks, layout):
    """Reorganize emitted region fragments into fixed page/column bands."""
    marker=re.compile(r'(?m)^% (p\d{3}-(?:r\d{4}|figure\d{3}|table\d{3})) / [^\n]+\n')
    matches=list(marker.finditer(tex))
    if not matches:return tex
    end=tex.rfind(r'\end{document}')
    chunks={m[1]:_unwrap(tex[m.start():matches[i+1].start() if i+1<len(matches) else end])
            for i,m in enumerate(matches)}
    pre=tex[:matches[0].start()]
    pre=pre.replace(r'\documentclass[10pt,twocolumn]',r'\documentclass[10pt]')
    pre=pre.replace(r'\usepackage{cuted}','')
    pre=pre.replace(r'\begin{document}',PREAMBLE+'\n'+r'\begin{document}')
    body_font=(r'\newcommand{\sourcebodyfont}{\fontsize{'+str(layout['font_size'])+'}{'+
               str(layout.get('line_height',layout['font_size']*1.2))+r'}\selectfont}'+'\n')
    pre=pre.replace(r'\begin{document}',body_font+r'\begin{document}')
    # The frames already include the full source height. A normal TeX topskip
    # would add another text line and ship an empty page before every frame.
    pre+='\n\\topskip=0pt\\baselineskip=0pt\\lineskip=0pt\\lineskiplimit=0pt\n'
    grouped=defaultdict(list)
    for b in blocks:grouped[b['page']].append(b)
    width=layout['paper_width']-layout['left']-layout['right']
    bottom=layout['paper_height']-layout['bottom']
    out=[pre]
    used=set()

    def content(items, top):
        if not items:return ''
        items=sorted(items,key=lambda b:(b['source_bbox'][1],b['source_bbox'][0]))
        offset=max(0,items[0]['source_bbox'][1]-top)
        value=r'\vspace*{'+str(round(offset,2))+r'bp}'+'\n'
        for n,b in enumerate(items):
            chunk=chunks[b['id']]
            # Before-skip on the first heading is already represented by the
            # frame offset. LaTeX cannot discard it at a minipage's top.
            if n==0 and b['type']=='heading':
                chunk=re.sub(r'(\\titlespacing\*\{\\\w+\}\{0pt\})\{[^}]*\}',r'\1{0pt}',chunk)
            used.add(b['id']);value+=chunk
        return value

    def frame(height, frame_width, label, value):
        if height<=0:raise ValueError('Overlapping source column bands: '+label)
        return (r'\begin{sourceframe}{'+str(round(height,2))+'}{'+str(round(frame_width,2))+'}{'+label+'}%\n'+value+'\\end{sourceframe}%\n')

    for ordinal,(page,items) in enumerate(sorted(grouped.items()),1):
        if ordinal>1:out.append('\\clearpage\n')
        out.append('% Source PDF page '+str(page)+' / column frames\n')
        columns=items[0].get('layout_columns',layout['columns'])
        gap=layout['column_gap'] if columns==2 else 0
        column_width=(width-gap)/columns
        active=[b for b in items if not b.get('merged_into') and not b.get('role','').startswith('frontmatter')]
        spans=[b for b in active if columns==2 and b.get('wide')]
        ordinary=[b for b in active if b not in spans]
        top=layout['top'];band=0

        def columns_band(selected, start, finish):
            nonlocal band
            band+=1
            h=finish-start
            if columns==1:
                return '\\par\\noindent'+frame(h,width,'p%d-b%d-single'%(page,band),content(selected,start))+'\\par\n'
            left=[b for b in selected if b.get('column')!='right']
            right=[b for b in selected if b.get('column')=='right']
            return ('\\par\\noindent\\hbox to \\textwidth{%\n'+
                    frame(h,column_width,'p%d-b%d-left'%(page,band),content(left,start))+
                    '\\hfil%\n'+frame(h,column_width,'p%d-b%d-right'%(page,band),content(right,start))+'}\\par\n')

        for span in sorted(spans,key=lambda b:b['source_bbox'][1]):
            start=span['source_bbox'][1]
            before=[b for b in ordinary if (b['source_bbox'][1]+b['source_bbox'][3])/2<start]
            if before:
                out.append(columns_band(before,top,start))
                ordinary=[b for b in ordinary if b not in before]
                top=start
            header_bottom=max([span['source_bbox'][3]]+
                              [c['source_bbox'][3] for c in span.get('caption_regions',[])])
            if span is active[0] and span['type']=='heading':
                # The emitted title includes author rows and leading artwork.
                front=[b for b in items if b.get('role','').startswith('frontmatter')]
                header_bottom=max([header_bottom]+[b['source_bbox'][3] for b in front]+
                                  [c['source_bbox'][3] for b in front for c in b.get('caption_regions',[])])
            used.add(span['id'])
            out.append('\\par\\noindent'+frame(header_bottom-top,width,'p%d-header%d'%(page,band),chunks[span['id']])+'\\par\n')
            top=header_bottom
        if ordinary:out.append(columns_band(ordinary,top,bottom))
        # Keep every region's file and audit marker, including content represented
        # by vector assets or the title. These fragments contain only comments.
        for b in items:
            if b['id'] not in used:
                out.append(chunks[b['id']]);used.add(b['id'])
    out.append('\\end{document}\n')
    return ''.join(out)
