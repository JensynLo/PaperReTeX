"""Position-preserving reconstruction using original embedded font outlines.

Vector art stays in a PDF background. Supported visible text is hidden in that
background and re-typeset as positioned, editable LaTeX glyphs, not page images.
"""

import io
import math
import re
from collections import defaultdict
from pathlib import Path
import fitz
from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib import TTFont
from fontTools.t1Lib import T1Font
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.transformPen import TransformPen
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, NameObject, NumberObject


def convert_font(data, ext, target, usage):
    """Create a Unicode OpenType font; preserve each original glyph's outline."""
    mapping = {}
    if ext == "pfa":
        tmp = target.with_suffix(".pfa")
        tmp.write_bytes(data + b"\n" + b"0" * 512 + b"\ncleartomark\n")
        try:
            source = T1Font(str(tmp))
            glyphs = source.getGlyphSet()
            order = list(glyphs)
            # FreeType swaps glyph zero and .notdef when loading Type 1 fonts.
            idx = order.index(".notdef")
            order[0], order[idx] = order[idx], order[0]
            cs, metrics = {}, {}
            matrix = source["FontMatrix"]
            transform = tuple(x * 1000 for x in matrix)
            for name in order:
                rec = RecordingPen()
                glyphs[name].draw(rec)
                width = round(glyphs[name].width * transform[0])
                pen = T2CharStringPen(width, glyphs)
                rec.replay(TransformPen(pen, transform))
                cs[name] = pen.getCharString()
                metrics[name] = (width, 0)
            fb = FontBuilder(1000, isTTF=False)
            fb.setupGlyphOrder(order)
            fb.setupCFF(
                target.stem,
                {
                    "FullName": target.stem,
                    "FamilyName": target.stem,
                    "Weight": "Regular",
                },
                cs,
                {},
            )
            fb.setupHorizontalMetrics(metrics)
            fb.setupHorizontalHeader(ascent=1000, descent=-300)
            fb.setupNameTable({
                "familyName": target.stem,
                "styleName": "Regular",
                "uniqueFontIdentifier": target.stem,
                "fullName": target.stem,
                "psName": target.stem,
            })
            font = fb.font
        finally:
            tmp.unlink(missing_ok=True)
    elif ext in ("ttf", "otf"):
        font = TTFont(io.BytesIO(data))
        order = font.getGlyphOrder()
        fb = FontBuilder(font=font)
    else:
        raise ValueError("Unsupported embedded font format: " + ext)
    # Prefer semantic Unicode, use private-use codes only for ambiguous glyphs.
    cmap, used = {}, set()
    for gid, unicodes in sorted(usage.items()):
        if gid == -1:
            continue
        if not 0 <= gid < len(order):
            raise ValueError(f"Invalid glyph index {gid}/{len(order)}")
        candidates = [
            u
            for u in sorted(unicodes)
            if u >= 32 and u != 0xFFFD and u not in used and not 0xD800 <= u <= 0xDFFF
        ]
        ligatures = {
            "fi": 0xFB01,
            "fl": 0xFB02,
            "ff": 0xFB00,
            "ffi": 0xFB03,
            "ffl": 0xFB04,
        }
        preferred = ligatures.get(order[gid])
        code = (
            preferred
            if preferred is not None and preferred not in used
            else (candidates[0] if candidates else 0xF0000 + gid)
        )
        while code in used:
            code += 1
        cmap[code] = order[gid]
        mapping[gid] = code
        used.add(code)
    # Retain unused glyphs in the font; glyph identity, not shaping, controls layout.
    cmap.setdefault(32, "space" if "space" in order else order[1])
    fb.setupCharacterMap(cmap)
    if "OS/2" not in font:
        fb.setupOS2(
            sTypoAscender=1000, sTypoDescender=-300, usWinAscent=2000, usWinDescent=1000
        )
    elif font["OS/2"].version < 4:
        font["OS/2"].fsSelection &= 0x7F
    if "name" not in font:
        fb.setupNameTable({
            "familyName": target.stem,
            "styleName": "Regular",
            "uniqueFontIdentifier": target.stem,
            "fullName": target.stem,
            "psName": target.stem,
        })
    if "post" not in font:
        fb.setupPost()
    fb.save(str(target))
    return mapping


def make_background(pdf, dest, selected, supported):
    reader = PdfReader(str(pdf))
    writer = PdfWriter()
    for pno in selected:
        writer.add_page(reader.pages[pno])
    seen = set()
    hidden = 0

    def process(stream, resources):
        nonlocal hidden
        fonts = (
            resources.get("/Font", {}).get_object() if resources.get("/Font") else {}
        )
        cs = ContentStream(stream, writer)
        current, mode, stack = None, 0, []
        ops = []
        for args, op in cs.operations:
            if op == b"q":
                stack.append((current, mode))
            elif op == b"Q" and stack:
                current, mode = stack.pop()
            elif op == b"Tf":
                f = fonts.get(args[0])
                current = (
                    str(f.get_object().get("/BaseFont", "")).lstrip("/") if f else None
                )
            elif op == b"Tr":
                mode = int(args[0])
            if op in (b"Tj", b"TJ", b"'", b'"') and current in supported and mode < 3:
                ops.extend([
                    ([NumberObject(3)], b"Tr"),
                    (args, op),
                    ([NumberObject(mode)], b"Tr"),
                ])
                hidden += 1
            else:
                ops.append((args, op))
        cs.operations = ops
        xobjects = (
            resources.get("/XObject", {}).get_object()
            if resources.get("/XObject")
            else {}
        )
        for ref in xobjects.values():
            obj = ref.get_object()
            key = (getattr(ref, "idnum", None), id(obj))
            if key in seen or obj.get("/Subtype") != "/Form":
                continue
            seen.add(key)
            converted = process(obj, obj.get("/Resources", resources).get_object())
            obj._data = converted.get_data()
            obj.pop("/Filter", None)
            obj.pop("/DecodeParms", None)
        return cs

    for page in writer.pages:
        page.replace_contents(
            process(page.get_contents(), page["/Resources"].get_object())
        )
        # The reconstructed document carries no live source annotations/actions.
        page.pop("/Annots", None)
    writer.write(str(dest))
    return hidden


def split_baselines(spans):
    for span in spans:
        dx, dy = span["dir"]
        run = []
        offset = None
        for char in span["chars"]:
            x, y = char[2]
            normal = -x * dy + y * dx
            if offset is not None and abs(normal - offset) > 0.05:
                yield dict(span, chars=run)
                run = []
            run.append(char)
            offset = normal
        if run:
            yield dict(span, chars=run)


def tex_char(code):
    char = chr(code)
    if char.isascii() and char.isalnum():
        return char
    return '\\char"' + format(code, "X") + "{}"


def reconstruct(pdf, out, pages=None):
    (out / "fonts").mkdir()
    report = {
        "backend": "layout",
        "quality": "positioned_reconstruction",
        "warnings": [],
        "limitations": [
            "Positioned glyphs preserve layout but do not recover semantic equation/section environments.",
            "Unsupported fonts and text clipping stay in the original vector background.",
            "The background PDF retains hidden original text for text-matrix advancement; do not use it for redaction.",
        ],
    }
    pages_data, font_defs = [], {}
    embedded_formulas = []
    usage = defaultdict(lambda: defaultdict(set))
    with fitz.open(pdf) as doc:
        if doc.needs_pass:
            raise ValueError("Encrypted PDF: decrypt it before conversion.")
        selected = pages if pages is not None else list(range(len(doc)))
        if any(p < 0 or p >= len(doc) for p in selected):
            raise ValueError("Page selection is out of range.")
        for pno in selected:
            page = doc[pno]
            from .native import actual_text
            from .latex import safe_math

            for formula in actual_text(doc, page):
                try:
                    embedded_formulas.append({
                        "page": pno + 1,
                        "latex": safe_math(formula),
                    })
                except ValueError:
                    pass
            if page.rotation:
                raise ValueError(
                    "Rotated page metadata is not supported by layout mode yet; use reflow mode."
                )
            traces = page.get_texttrace()
            fonts = page.get_fonts()
            lookup = defaultdict(list)
            for f in fonts:
                key = f[3]
                if key not in font_defs:
                    font_defs[key] = {"xref": f[0], "ext": f[1], "base": key}
                lookup[key.split("+")[-1]].append(key)
            for s in traces:
                candidates = list(dict.fromkeys(lookup.get(s["font"], [])))
                s["font_key"] = candidates[0] if len(candidates) == 1 else None
                if s["font_key"] and s["type"] in (0, 1, 2):
                    for u, gid, _, _ in s["chars"]:
                        usage[s["font_key"]][gid].add(u)
            pages_data.append({
                "page": pno + 1,
                "width": page.rect.width,
                "height": page.rect.height,
                "spans": traces,
            })
        exported = {}
        for i, (key, u) in enumerate(usage.items()):
            f = font_defs[key]
            filename = f"f{i:03d}.otf"
            try:
                _, ext, _, data = doc.extract_font(f["xref"])
                if not data:
                    raise ValueError("Font is not embedded")
                mapping = convert_font(data, ext, out / "fonts" / filename, u)
                exported[key] = {
                    "command": f"pdfFont{chr(65 + i // 26)}{chr(65 + i % 26)}",
                    "file": filename,
                    "map": mapping,
                }
            except (ValueError, KeyError, AssertionError, RuntimeError) as exc:
                report["warnings"].append({
                    "font": key,
                    "message": str(exc) + "; preserved in vector background",
                })
    # Text used as clipping paths cannot safely be separated from its artwork.
    # MuPDF reports it as a clipping operation; conservatively retain its fonts.
    with fitz.open(pdf) as doc:
        for pno in selected:
            bboxlog = doc[pno].get_bboxlog()
            for s in pages_data[selected.index(pno)]["spans"]:
                if s["seqno"] < len(bboxlog) and bboxlog[s["seqno"]][0] == "clip-text":
                    exported.pop(s["font_key"], None)
    hidden = make_background(pdf, out / "background.pdf", selected, set(exported))
    tex = [
        r"""\documentclass{article}
\usepackage{fontspec,graphicx,xcolor,eso-pic}
\usepackage[margin=0pt]{geometry}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\setlength{\unitlength}{1bp}
% Text baselines and dimensions use PDF points (bp). Original outlines are embedded.
\newcommand{\G}[2]{\kern#1bp\relax\rlap{#2}}
"""
    ]
    for f in exported.values():
        tex.append(
            "\\newfontfamily\\"
            + f["command"]
            + "[Path=fonts/,RawFeature={-liga;-kern}]{"
            + f["file"]
            + "}\n"
        )
    tex.append("\\begin{document}\n")
    editable, fallback = 0, 0
    for page_index, page in enumerate(pages_data):
        w, h = page["width"], page["height"]
        tex.append(f"\\pdfpagewidth={w:.5f}bp\\pdfpageheight={h:.5f}bp\n")
        tex.append(f"\\paperwidth={w:.5f}bp\\paperheight={h:.5f}bp\n")
        tex.append(
            "\\AddToShipoutPictureBG*{\\AtPageLowerLeft{\\includegraphics[page="
            + str(page_index + 1)
            + f",width={w:.5f}bp,height={h:.5f}bp"
            + "]{background.pdf}}}\n"
        )
        tex.append(
            "\\AddToShipoutPictureFG*{\\AtPageUpperLeft{\\setlength{\\unitlength}{1bp}\n"
        )
        for s in split_baselines(page["spans"]):
            if s["type"] not in (0, 1, 2):
                continue
            f = exported.get(s["font_key"])
            if not f:
                fallback += len(s["chars"])
                continue
            if not s["chars"]:
                continue
            # Glyph positions follow the original text baseline, including rotation.
            dx, dy = s["dir"]
            angle = -math.degrees(math.atan2(dy, dx))
            ox, oy = s["chars"][0][2]
            color = s["color"]
            if len(color) == 1:
                color = (color[0],) * 3
            if len(color) == 4:
                c, m, y, k = color
                color = (1 - min(1, c + k), 1 - min(1, m + k), 1 - min(1, y + k))
            rgb = ",".join(f"{x:.5f}" for x in color)
            parts = []
            previous = 0
            for u, gid, (x, y), bbox in s["chars"]:
                if gid < 0:
                    continue
                pos = (x - ox) * dx + (y - oy) * dy
                parts.append(
                    f"\\G{{{pos - previous:.5f}}}{{{tex_char(f['map'][gid])}}}"
                )
                previous = pos
                editable += 1
            content = (
                "\\"
                + f["command"]
                + f"\\fontsize{{{s['size']:.5f}bp}}{{{s['size']:.5f}bp}}\\selectfont\\color[rgb]{{{rgb}}}"
                + "".join(parts)
            )
            if abs(angle) > 0.001:
                content = (
                    f"\\rotatebox[origin=Bl]{{{angle:.5f}}}{{\\makebox[0pt][l]{{\\smash{{"
                    + content
                    + "}}}"
                )
            preview = (
                ""
                .join(chr(c[0]) for c in s["chars"])
                .replace("\n", " ")
                .replace("\r", " ")
                .replace("^^", "^ ^")[:130]
            )
            tex.append("% " + preview + "\n")
            tex.append(f"\\put({ox:.5f},{-oy:.5f})" + "{" + content + "}\n")
        tex.append("}}\n\\null\\newpage\n")
    tex.append("\\end{document}\n")
    (out / "main.tex").write_text("".join(tex), encoding="utf-8")
    report["embedded_formulas"] = embedded_formulas
    report.update(
        counts={
            "pages": len(pages_data),
            "editable_glyphs": editable,
            "background_glyphs": fallback,
            "embedded_fonts": len(exported),
            "hidden_text_operations": hidden,
        },
        review_blocks=[],
    )
    # All original geometry and readable text remain available for tooling/editors.
    (out / "layout.json").write_text(
        __import__("json").dumps(pages_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report
