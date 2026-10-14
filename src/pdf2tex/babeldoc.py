"""Normalize BabelDOC's layout IR into auditable regions and formula crops."""
from collections import Counter
import re
from pathlib import Path
import fitz


def rect(box, height):
    return fitz.Rect(box['x'], height-box['y2'], box['x2'], height-box['y'])


def characters(comp):
    if comp.get('pdf_character'):
        value = comp['pdf_character']
        return value if isinstance(value, list) else [value]
    for key in ('pdf_line', 'pdf_same_style_characters', 'pdf_formula'):
        if comp.get(key):
            return comp[key].get('pdf_character', [])
    return []


def style_of(chars, fonts, fallback=None):
    sizes = Counter(); names = Counter(); bold = italic = total = 0
    for char in chars:
        style = char.get('pdf_style') or fallback or {}
        font = fonts.get(style.get('font_id'), {})
        weight = max(1, len(char.get('char_unicode') or ''))
        sizes[style.get('font_size') or 10] += weight
        names[font.get('name') or 'unknown'] += weight
        bold += weight*bool(font.get('bold')); italic += weight*bool(font.get('italic')); total += weight
    return {'font_size': round(sizes.most_common(1)[0][0], 2) if sizes else (fallback or {}).get('font_size', 10),
            'font_name': names.most_common(1)[0][0] if names else 'unknown',
            'bold': bool(total and bold/total>.65), 'italic': bool(total and italic/total>.65)}


def text_of(chars):
    """Keep explicit spaces and infer line joins/gaps when PDF has no space glyph."""
    text = ''; previous = None
    for c in chars:
        value = c.get('char_unicode') or ''
        box = c.get('box')
        if previous and box and text and value and not text[-1].isspace() and not value[0].isspace():
            old = previous['box']; size = (c.get('pdf_style') or {}).get('font_size') or 10
            newline = abs(box['y']-old['y'])>size*.65 and box['x']<old['x2']
            gap = box['x']-old['x2']
            if newline:
                if text.endswith('-') and value[0].islower(): text = text[:-1]
                else: text += ' '
            elif gap>size*.18: text += ' '
        text += value
        if box: previous = c
    return text


def source_blue(chars):
    """Detect the blue glyphs commonly used for linked PDF citations."""
    blue = 0
    visible = 0
    for char in chars:
        value = (char.get('char_unicode') or '').strip()
        if not value:continue
        visible += len(value)
        state = ((char.get('pdf_style') or {}).get('graphic_state') or {}).get('passthrough_per_char_instruction') or ''
        matches = list(re.finditer(r'0\.210000\s+0\.490000\s+0\.740000\s+rg|0\s+g', state))
        if matches and '0.210000' in matches[-1][0]:blue += len(value)
    return visible>0 and blue/visible>.3


def normalize_ir(ir, pdf, root, selected=None):
    root = Path(root); (root/'formula-crops').mkdir(exist_ok=True); (root/'assets').mkdir(exist_ok=True)
    regions = []; formulas = []; warnings = []
    with fitz.open(pdf) as doc:
        for page_ir in ir['page']:
            index = page_ir['page_number']
            if selected is not None and index not in selected: continue
            page = doc[index]; height = page.rect.height; width = page.rect.width
            fonts = {f['font_id']: f for f in page_ir.get('pdf_font', [])}
            for obj in page_ir.get('pdf_xobject', []):
                for f in obj.get('pdf_font', []): fonts.setdefault(f['font_id'], f)
            math_family = 'computer_modern' if any(re.search(r'(?:\+|^)CM(?:MI|SY|EX)', f.get('name','')) for f in fonts.values()) else 'unknown'
            layouts = page_ir.get('page_layout', [])
            for ordinal, para in enumerate(page_ir.get('pdf_paragraph', []), 1):
                box = rect(para['box'], height)
                label = (para.get('layout_label') or 'text').lower()
                rid = 'p%03d-r%04d' % (index+1, ordinal)
                comps = para.get('pdf_paragraph_composition', [])
                allchars = [c for comp in comps for c in characters(comp)]
                typ = 'equation' if 'formula' in label else ('heading' if 'title' in label else 'paragraph')
                if label in ('footnote', 'footnotes'): typ = 'footnote'
                region = {'id': rid, 'page': index+1, 'type': typ, 'layout_label': label,
                          'bbox': [box.x0/width*1000, box.y0/height*1000, box.x1/width*1000, box.y1/height*1000],
                          'source_bbox': list(box), 'text': para.get('unicode') or text_of(allchars),
                          'segments': [], 'formula_ids': [], 'layout_id': para.get('layout_id'),
                          'first_line_indent': bool(para.get('first_line_indent')),
                          'math_font_family':math_family,
                          **style_of(allchars, fonts, para.get('pdf_style'))}
                if allchars and allchars[0].get('box'):
                    region['first_line_offset'] = max(0, allchars[0]['box']['x']-box.x0)
                previous_chars = []
                for comp in comps:
                    comp_chars = characters(comp)
                    previous_char = previous_chars[-1] if previous_chars else None
                    if previous_chars and comp_chars:
                        joined = text_of([previous_chars[-1], comp_chars[0]])
                        raw = (previous_chars[-1].get('char_unicode') or '')+(comp_chars[0].get('char_unicode') or '')
                        if len(joined)>len(raw): region['segments'].append({'text':' '})
                    if comp_chars: previous_chars = comp_chars
                    formula = comp.get('pdf_formula')
                    if formula:
                        fid = 'f%05d' % (len(formulas)+1)
                        fbox = rect(formula['box'], height)
                        for c in formula.get('pdf_character', []):
                            visual = (c.get('visual_bbox') or {}).get('box')
                            if visual: fbox |= rect(visual, height)
                        fbox = (fbox+(-1.5, -1.5, 1.5, 1.5)) & page.rect
                        if fbox.is_empty: raise ValueError('Empty formula crop: '+fid)
                        crop = 'formula-crops/'+fid+'.png'
                        page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=fbox, alpha=False).save(root/crop)
                        record = {'id': fid, 'region_id': rid, 'page': index+1,
                                  'kind': 'display' if typ=='equation' else 'inline',
                                  'corner_mark': bool(formula.get('is_corner_mark')),
                                  'bbox': list(fbox), 'crop': crop,
                                  'native_text': text_of(formula.get('pdf_character', [])),
                                  'source_blue':source_blue(formula.get('pdf_character', [])),
                                  'context': region['text'][:1200]}
                        if record['corner_mark'] and previous_char and comp_chars:
                            old = previous_char.get('box'); current = comp_chars[0].get('box')
                            if old and current:
                                delta = (current['y']+current['y2']-old['y']-old['y2'])/2
                                record['script_position'] = 'super' if delta>1 else ('sub' if delta< -1 else 'baseline')
                                previous_size = (previous_char.get('pdf_style') or {}).get('font_size') or 10
                                record['script_is_isolated'] = (0<=current['x']-old['x2']<previous_size*.6
                                    and all(((c.get('pdf_style') or {}).get('font_size') or previous_size)<previous_size*.8
                                            for c in comp_chars if (c.get('char_unicode') or '').strip()))
                        formulas.append(record); region['formula_ids'].append(fid)
                        region['segments'].append({'formula_id': fid})
                    else:
                        chars = characters(comp)
                        unicode_part = comp.get('pdf_same_style_unicode_characters') or {}
                        value = text_of(chars) if chars else unicode_part.get('unicode', '')
                        if value:
                            region['segments'].append({'text': value, **style_of(chars, fonts, para.get('pdf_style'))})
                if not region['segments'] and region['text']:
                    region['segments'] = [{'text': region['text']}]
                if typ=='equation' and not region['formula_ids']:
                    # Some display regions are kept as lines; OCR the complete region.
                    fid = 'f%05d' % (len(formulas)+1); crop = 'formula-crops/'+fid+'.png'
                    page.get_pixmap(matrix=fitz.Matrix(4,4), clip=(box+(-2,-2,2,2)) & page.rect).save(root/crop)
                    formulas.append({'id':fid, 'region_id':rid, 'page':index+1, 'kind':'display',
                                     'corner_mark':False, 'bbox':list(box), 'crop':crop,
                                     'native_text':region['text'], 'context':region['text']})
                    region.update(segments=[{'formula_id':fid}], formula_ids=[fid])
                regions.append(region)
            # BabelDOC deliberately leaves isolated display math outside paragraphs.
            # Recover those layout boxes as separate expressions, not PDF assets.
            for ordinal, layout in enumerate(layouts, 1):
                label = (layout.get('class_name') or '').lower()
                if label not in ('formula', 'isolate_formula', 'isolated_formula'): continue
                box = rect(layout['box'], height) & page.rect
                if box.is_empty: continue
                overlap = sum(abs(box & fitz.Rect(f['bbox'])) for f in formulas if f['page']==index+1)
                if overlap > abs(box)*.5: continue
                rid = 'p%03d-r%04d' % (index+1, len(page_ir.get('pdf_paragraph', []))+ordinal)
                fid = 'f%05d' % (len(formulas)+1)
                # The model has a separate equation-number class in some documents.
                for tag in layouts:
                    if tag.get('class_name')=='formula_caption':
                        number_box = rect(tag['box'], height)
                        if abs((number_box.y0+number_box.y1)-(box.y0+box.y1))<20 and 0<number_box.x0-box.x1<100:
                            box |= number_box
                cropbox = (box+(-2,-2,2,2)) & page.rect
                crop = 'formula-crops/'+fid+'.png'
                page.get_pixmap(matrix=fitz.Matrix(4,4), clip=cropbox, alpha=False).save(root/crop)
                native = page.get_textbox(cropbox)
                number = None
                for tag in layouts:
                    if tag.get('class_name')!='formula_caption': continue
                    nb = rect(tag['box'], height)
                    if (box.x0+box.width*.5 <= nb.x0 <= box.x1+100
                            and nb.y0<=box.y1+14 and nb.y1>=box.y0-5):
                        match = re.fullmatch(r'\s*\((\d+[a-z]?)\)\s*', page.get_textbox(nb+(-1,-1,1,1)))
                        if match: number = match[1]
                formulas.append({'id':fid, 'region_id':rid, 'page':index+1, 'kind':'display', 'corner_mark':False,
                                 'bbox':list(cropbox), 'crop':crop, 'native_text':native, 'context':native})
                regions.append({'id':rid, 'page':index+1, 'type':'equation', 'layout_label':label,
                                'number':number,
                                'math_font_family':math_family,
                                'source_bbox':list(box), 'text':native, 'segments':[{'formula_id':fid}],
                                'formula_ids':[fid], 'bbox':[box.x0/width*1000,box.y0/height*1000,box.x1/width*1000,box.y1/height*1000]})
            # Retain source vector graphics; only figures/tables become PDF assets.
            # No full-page or formula backgrounds are used for TeX reconstruction.
            for ordinal, layout in enumerate(layouts, 1):
                label = (layout.get('class_name') or '').lower()
                if label not in ('figure', 'table'): continue
                box = rect(layout['box'], height) & page.rect
                if box.is_empty: continue
                rid = 'p%03d-%s%03d' % (index+1, label, ordinal)
                dest = 'assets/'+rid+'.pdf'
                with fitz.open() as asset:
                    target = asset.new_page(width=box.width, height=box.height)
                    target.show_pdf_page(target.rect, doc, index, clip=box)
                    asset.save(root/dest, garbage=4, deflate=True)
                regions.append({'id':rid, 'page':index+1, 'type':'table_crop' if label=='table' else 'figure_crop',
                                'layout_label':label, 'asset':dest, 'source_bbox':list(box),
                                'bbox':[box.x0/width*1000, box.y0/height*1000, box.x1/width*1000, box.y1/height*1000],
                                'text':'', 'segments':[], 'formula_ids':[]})
                if label=='table': warnings.append(rid+': table preserved as vector PDF; cells are not editable TeX.')
    if not regions: raise ValueError('BabelDOC returned no usable regions.')
    return regions, formulas, warnings


def context_crops(pdf, formulas, root):
    """A second review can see the surrounding line and an explicit target box."""
    from PIL import Image, ImageDraw
    root = Path(root); folder = root/'formula-context'; folder.mkdir(exist_ok=True)
    with fitz.open(pdf) as doc:
        for f in formulas:
            page = doc[f['page']-1]; target = fitz.Rect(f['bbox'])
            clip = (target+(-24,-8,24,8)) & page.rect
            pix = page.get_pixmap(matrix=fitz.Matrix(4,4), clip=clip, alpha=False)
            image = Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
            ImageDraw.Draw(image).rectangle([(target.x0-clip.x0)*4,(target.y0-clip.y0)*4,
                                            (target.x1-clip.x0)*4,(target.y1-clip.y0)*4],outline='#d62728',width=2)
            name = 'formula-context/'+f['id']+'.png'; image.save(root/name)
            f['review_crop'] = name


def render_overlays(pdf, regions, formulas, root):
    """Make detection coverage inspectable without loading the large raw IR."""
    from PIL import Image, ImageDraw
    folder = Path(root)/'layout-overlays'; folder.mkdir(exist_ok=True)
    with fitz.open(pdf) as doc:
        for number in sorted({r['page'] for r in regions}):
            pix = doc[number-1].get_pixmap(matrix=fitz.Matrix(1.6,1.6), alpha=False)
            image = Image.frombytes('RGB', (pix.width,pix.height), pix.samples)
            draw = ImageDraw.Draw(image)
            for row in [r for r in regions if r['page']==number]:
                box = [v*1.6 for v in row['source_bbox']]
                draw.rectangle(box, outline='#007b47', width=2)
            for row in [f for f in formulas if f['page']==number]:
                box = [v*1.6 for v in row['bbox']]
                draw.rectangle(box, outline='#d62728', width=2)
                draw.text((box[0],max(0,box[1]-11)), row['id'], fill='#b00020')
            image.save(folder/('page-%03d.png' % number))
