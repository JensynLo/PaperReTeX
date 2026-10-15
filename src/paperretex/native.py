"""Conservative native reconstruction: semantic text plus explicit crop fallbacks."""

import re
import statistics
import unicodedata
from collections import Counter
import fitz
from pypdf.generic import ContentStream, DecodedStreamObject
from .latex import escape, safe_math

MATH_FONT = re.compile(
    r"^(CM(?:MI|SY|EX|R|BX|B|SS)|MS[AB]M|.*(?:Math|Symbol|math|symbol)|MTMI|MTEX|MTSY)"
)


def math_font(name):
    return bool(MATH_FONT.search(name.split("+")[-1]))


def text_of(block):
    return "\n".join(
        "".join(s["text"] for s in line["spans"]) for line in block.get("lines", [])
    )


def actual_text(doc, page):
    values = []
    seen = set()
    for xref in list(page.get_contents()) + [x[0] for x in page.get_xobjects()]:
        if xref in seen:
            continue
        seen.add(xref)
        data = doc.xref_stream(xref)
        if not data or b"ActualText" not in data:
            continue
        stream = DecodedStreamObject()
        stream.set_data(data)
        for operands, operator in ContentStream(stream, None).operations:
            if operator != b"BDC":
                continue
            for operand in operands:
                if isinstance(operand, dict) and "/ActualText" in operand:
                    value = str(operand["/ActualText"]).strip()
                    if "\\" in value and len(value) > 20:
                        values.append(value)
    return values


def inline_tex(block):
    """Recover font runs and obvious scripts; complex inline math needs review."""
    result = []
    has_math = False
    for line in block.get("lines", []):
        spans = [s for s in line["spans"] if s["text"].strip()]
        if not spans:
            continue
        base_size = max(s["size"] for s in spans)
        bases = [s["origin"][1] for s in spans if s["size"] > base_size * 0.85]
        baseline = statistics.median(bases)
        text = []
        for s in spans:
            value = escape(s["text"])
            if math_font(s["font"]):
                has_math = True
                # Preserve glyph meanings, but never pretend inferred scripts are certain.
                leading = " " if s["text"].startswith(" ") else ""
                trailing = " " if s["text"].endswith(" ") else ""
                plain = s["text"].strip().replace("−", "-")
                plain = re.sub(r"[\\{}$%&#_^]", "", plain)
                plain = "".join(c for c in plain if ord(c) >= 32)
                plain = (
                    plain
                    .replace("µ", r"\mu ")
                    .replace("ˆ", r"\hat{}")
                    .replace("˜", r"\tilde{}")
                    .replace("ℓ", r"\ell ")
                )
                if s["font"].startswith("MSBM"):
                    plain = r"\mathbb{" + plain + "}"
                elif s["font"].startswith("CMSY") and re.fullmatch(r"[A-Z]+", plain):
                    plain = r"\mathcal{" + plain + "}"
                elif s["font"].startswith(("CMMIB", "CMBX")):
                    plain = r"\symbf{" + plain + "}"
                if s["size"] < base_size * 0.85:
                    if s["origin"][1] < baseline - 0.8:
                        value = r"\ensuremath{{}^{\scriptscriptstyle " + plain + "}}"
                    elif s["origin"][1] > baseline + 0.8:
                        value = r"\ensuremath{{}_{\scriptscriptstyle " + plain + "}}"
                    else:
                        value = r"\ensuremath{" + plain + "}"
                else:
                    value = r"\ensuremath{" + plain + "}"
                value = leading + value + trailing
            elif s["flags"] & 16:
                value = r"\textbf{" + value + "}"
            elif s["flags"] & 2:
                value = r"\textit{" + value + "}"
            text.append(value)
        result.append("".join(text))
    # A literal line-end hyphen in PDF prose normally marks a split word.
    return re.sub(r"(?<=[a-z])-\n(?=[a-z])", "", "\n".join(result)).replace(
        "\n", " "
    ), has_math


def union(rects):
    r = fitz.Rect(rects[0])
    for item in rects[1:]:
        r |= fitz.Rect(item)
    return r


def area(r):
    return max(0, r.width) * max(0, r.height)


def overlap(a, b):
    a, b = fitz.Rect(a), fitz.Rect(b)
    return area(a & b) / max(1, area(a))


def order_blocks(blocks, width):
    """Full-width blocks divide a page into bands; read columns within each band."""
    mid = width / 2
    spanning = sorted(
        [b for b in blocks if b["bbox"][0] < mid - 30 and b["bbox"][2] > mid + 30],
        key=lambda b: b["bbox"][1],
    )
    others = [b for b in blocks if b not in spanning]
    ordered = []
    for s in spanning:
        band = [
            b
            for b in others
            if (b["bbox"][1] + b["bbox"][3]) / 2 < (s["bbox"][1] + s["bbox"][3]) / 2
        ]
        others = [b for b in others if b not in band]
        ordered.extend(
            sorted(
                band, key=lambda b: (b["bbox"][0] >= mid, b["bbox"][1], b["bbox"][0])
            )
        )
        ordered.append(s)
    ordered.extend(
        sorted(others, key=lambda b: (b["bbox"][0] >= mid, b["bbox"][1], b["bbox"][0]))
    )
    return ordered


def extract(pdf, out, pages=None):
    blocks, warnings, page_stats, embedded_formulas = [], [], [], []
    with fitz.open(pdf) as doc:
        if doc.needs_pass:
            raise ValueError("Encrypted PDF: decrypt it before conversion.")
        selected = pages if pages is not None else list(range(len(doc)))
        if any(p < 0 or p >= len(doc) for p in selected):
            raise ValueError("Page selection is out of range.")
        for pno in selected:
            page = doc[pno]
            data = page.get_text(
                "dict",
                flags=(fitz.TEXTFLAGS_DICT | fitz.TEXT_IGNORE_ACTUALTEXT)
                & ~fitz.TEXT_PRESERVE_IMAGES,
            )
            raw = [b for b in data["blocks"] if b["type"] == 0]
            chars = sum(len(text_of(b)) for b in raw)
            seq = []
            formulas = actual_text(doc, page)
            for formula in formulas:
                try:
                    embedded_formulas.append({
                        "page": pno + 1,
                        "latex": safe_math(formula),
                    })
                except ValueError as exc:
                    warnings.append({"page": pno + 1, "message": str(exc)})
            # Classify short equation-only blocks, then merge split fraction/script blocks.
            for i, b in enumerate(raw):
                b["_index"] = i
                spans = [s for l in b["lines"] for s in l["spans"]]
                n = sum(len(s["text"].strip()) for s in spans)
                nm = sum(len(s["text"].strip()) for s in spans if math_font(s["font"]))
                text = text_of(b)
                matched = next(
                    (
                        v
                        for v in formulas
                        if re.sub(r"\s+", "", v)[-80:] in re.sub(r"\s+", "", text)
                    ),
                    None,
                )
                b["_formula"] = matched
                b["_math"] = bool(matched) or (n > 0 and nm / n > 0.62)
            # Collect image/vector regions; filter tiny decorations and page-size borders.
            regions = [
                fitz.Rect(x["bbox"])
                for x in page.get_image_info()
                if area(fitz.Rect(x["bbox"])) > 700
            ]
            try:
                regions += [
                    r
                    for r in page.cluster_drawings()
                    if r.width > 30
                    and r.height > 24
                    and area(r) < area(page.rect) * 0.75
                ]
            except (AttributeError, RuntimeError):
                pass
            merged = []
            for region in regions:
                r = region & page.rect
                if r.is_empty:
                    continue
                changed = True
                while changed:
                    changed = False
                    for old in list(merged):
                        expanded = old + (-3, -3, 3, 3)
                        if expanded.intersects(r):
                            r |= old
                            merged.remove(old)
                            changed = True
                merged.append(r)
            assets = []
            for r in merged:
                # Avoid swallowing entire prose paragraphs behind background rectangles.
                if any(
                    overlap(b["bbox"], r) > 0.8
                    and len(text_of(b)) > 200
                    and not b["_math"]
                    for b in raw
                ):
                    continue
                assets.append(r)
            consumed = set()
            for r in assets:
                for b in raw:
                    if overlap(b["bbox"], r) > 0.85:
                        consumed.add(b["_index"])
                seq.append({"type": "figure_crop", "bbox": list(r), "review": True})
            ordered = order_blocks(
                [b for b in raw if b["_index"] not in consumed], page.rect.width
            )
            i = 0
            while i < len(ordered):
                b = ordered[i]
                text = text_of(b).strip()
                if not text:
                    i += 1
                    continue
                # Skip running page numbers and arXiv stamps, retain in source.json metadata.
                if (
                    re.fullmatch(r"\d+", text)
                    and (b["bbox"][1] > page.rect.height * 0.92 or b["bbox"][3] < 35)
                ) or text.startswith("arXiv:"):
                    i += 1
                    continue
                if (
                    any(unicodedata.category(c) == "Co" or c == "\ufffd" for c in text)
                    and not b["_formula"]
                ):
                    seq.append({
                        "type": "unmapped_text_crop",
                        "bbox": list(b["bbox"]),
                        "text": text,
                        "review": True,
                    })
                    i += 1
                    continue
                if b["_math"]:
                    group = [b]
                    i += 1
                    while i < len(ordered) and ordered[i]["_math"]:
                        nxt = ordered[i]
                        if (
                            nxt["bbox"][1] - max(x["bbox"][3] for x in group) > 12
                            or abs(nxt["bbox"][0] - b["bbox"][0])
                            > page.rect.width * 0.35
                        ):
                            break
                        # Distinct embedded formulae are separate equations.
                        if nxt["_formula"] and any(x["_formula"] for x in group):
                            break
                        group.append(nxt)
                        i += 1
                    r = union([x["bbox"] for x in group])
                    exact = next((x["_formula"] for x in group if x["_formula"]), None)
                    if exact:
                        try:
                            latex = safe_math(exact)
                            seq.append({
                                "type": "equation",
                                "bbox": list(r),
                                "latex": latex,
                                "provenance": "pdf_actualtext",
                                "review": False,
                            })
                            continue
                        except ValueError as exc:
                            warnings.append({"page": pno + 1, "message": str(exc)})
                    seq.append({
                        "type": "equation_crop",
                        "bbox": list(r),
                        "review": True,
                        "text": " ".join(text_of(x) for x in group),
                    })
                    continue
                spans = [s for l in b["lines"] for s in l["spans"]]
                heading = (
                    bool(re.match(r"^\d+(?:\.\d+)*\.?\s+[A-Z]", text))
                    and len(text) < 150
                    and any(
                        s["flags"] & 16
                        or re.search(r"Bold|Medi|LinBiolinum|Libertine.*B", s["font"])
                        for s in spans
                    )
                )
                heading = heading or text in {
                    "Abstract",
                    "References",
                    "Acknowledgments",
                    "Acknowledgements",
                }
                if heading:
                    level = (
                        min(
                            3,
                            len(
                                re
                                .match(r"^([\d.]+)", text)
                                .group(1)
                                .rstrip(".")
                                .split(".")
                            ),
                        )
                        if text[0].isdigit()
                        else 1
                    )
                    seq.append({
                        "type": "heading",
                        "level": level,
                        "text": text,
                        "bbox": b["bbox"],
                        "review": False,
                    })
                else:
                    tex, has_math = inline_tex(b)
                    seq.append({
                        "type": "paragraph",
                        "text": text,
                        "tex": tex,
                        "bbox": b["bbox"],
                        "review": has_math,
                    })
                i += 1
            if chars < 50:
                seq = [{"type": "page_crop", "bbox": list(page.rect), "review": True}]
                warnings.append({
                    "page": pno + 1,
                    "message": "No usable text layer; page preserved as image. OCR required.",
                })
            seq = order_blocks(seq, page.rect.width)
            for j, b in enumerate(seq):
                b.update(id=f"p{pno + 1:03d}-b{j + 1:03d}", page=pno + 1)
                if b["type"].endswith("_crop"):
                    r = (fitz.Rect(b["bbox"]) + (-1, -1, 1, 1)) & page.rect
                    name = f"assets/{b['id']}.png"
                    page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=r, alpha=False).save(
                        out / name
                    )
                    b.update(
                        asset=name,
                        width=round(
                            min(1, max(0.18, r.width / (page.rect.width - 90))), 3
                        ),
                    )
            blocks.extend(seq)
            page_stats.append({
                "page": pno + 1,
                "source_characters": chars,
                "blocks": len(seq),
                "embedded_formula_candidates": len(formulas),
            })
    return blocks, {
        "backend": "native",
        "quality": "review_draft",
        "pages": page_stats,
        "warnings": warnings,
        "embedded_formulas": embedded_formulas,
        "limitations": [
            "Native extraction does not OCR formulas or scans.",
            "Inline math, reading order, tables and figure grouping require review.",
            "Cropped equations are not editable LaTeX; consult blocks marked review=true.",
        ],
    }
