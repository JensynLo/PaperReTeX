"""Conventional single/two-column policies shared by ordering and TeX emission."""
from collections import defaultdict

PROFILES = {
    'single_column': {'columns': 1, 'reading_order': 'top_to_bottom', 'wide_floats': False},
    'two_column': {'columns': 2, 'reading_order': 'left_then_right_between_spanning_blocks', 'wide_floats': True},
}


def prepare_blocks(blocks, plan):
    """Order bbox-equipped pages in column bands; keep uncertain pages unchanged."""
    pages = {p['page']: p for p in plan['pages']}
    grouped = defaultdict(list)
    for block in blocks:
        grouped[block['page']].append(block)
    result, warnings = [], []
    geometry = plan['geometry']
    width = geometry['paper_width']
    gutter = (geometry['left'] + (width-geometry['right'])) / 2 / width * 1000
    halfgap = geometry['column_gap'] / width * 500
    for page_number, items in sorted(grouped.items()):
        info = pages.get(page_number, {})
        columns = info.get('columns') or plan['columns']
        for b in items:
            b['layout_columns'] = columns
            bb = b.get('bbox')
            if bb:
                b['wide'] = columns == 2 and bb[0] < gutter-halfgap and bb[2] > gutter+halfgap
                b['column'] = 'span' if b['wide'] else ('right' if columns == 2 and (bb[0]+bb[2])/2 > gutter else 'left')
        if not all(b.get('bbox') for b in items) or (info.get('confidence', 0) < .7 and not plan.get('manual_override')):
            warnings.append({'page': page_number, 'reason': 'Kept recognizer order: missing boxes or uncertain page layout.'})
            result.extend(items)
            continue
        if columns == 1:
            result.extend(sorted(items, key=lambda b: (b['bbox'][1], b['bbox'][0])))
            continue
        spans = sorted([b for b in items if b['wide']], key=lambda b: b['bbox'][1])
        ordinary = [b for b in items if not b['wide']]
        ordered = []
        # A full-width figure/title separates reading-order bands; do not simply
        # read the whole left column before all spanning content.
        for barrier in spans:
            before = [b for b in ordinary if (b['bbox'][1]+b['bbox'][3])/2 < barrier['bbox'][1]]
            ordered.extend(sorted(before, key=lambda b: (b['column']=='right', b['bbox'][1])))
            ordinary = [b for b in ordinary if b not in before]
            ordered.append(barrier)
        ordered.extend(sorted(ordinary, key=lambda b: (b['column']=='right', b['bbox'][1])))
        result.extend(ordered)
    return result, warnings
