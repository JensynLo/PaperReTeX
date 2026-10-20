"""BabelDOC layout → formula OCR/review → region TeX → assembled document."""

import hashlib
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import statistics
import time
from .babeldoc import normalize_ir, render_overlays, context_crops
from .formula_review import review_formulas
from .fragments import fragment_groups, merge_fragments, review_fragments
from .latex import emit_document, escape, safe_math
from .preflight import assess_layout
from .profiles import prepare_blocks
from .config import cache_root

VERSION = "babeldoc-hybrid-v1"
MONOSPACE = re.compile(r"Mono|Courier|CMTT|SFTT|Consol|Menlo")
MARKER = re.compile(r"[*∗†‡§¶\d]{1,3}")


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def runtime(name, explicit=None):
    if explicit:
        path = Path(explicit).expanduser().resolve()
    else:
        cache = cache_root()
        path = (
            cache
            / (name + "-runtime")
            / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
    if not path.is_file():
        raise ValueError(
            "Missing "
            + name
            + " Python runtime: "
            + str(path)
            + ". Run paperretex install-runtimes or specify --"
            + name
            + "-python."
        )
    return path


def worker(python, module, arguments, logfile, timeout=1800):
    script = Path(__file__).with_name(module + ".py")
    with logfile.open("w") as log:
        try:
            result = subprocess.run(
                [str(python), str(script), *map(str, arguments)],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Worker timed out; see " + str(logfile)) from exc
    if result.returncode:
        tail = "\n".join(logfile.read_text(errors="replace").splitlines()[-12:])
        raise RuntimeError(module + " failed; see " + str(logfile) + "\n" + tail)


def apply_formulas(regions, formulas, verdicts):
    if set(verdicts) != {f["id"] for f in formulas if not f.get("skipped")}:
        raise ValueError("Assembly refused: formula review coverage is incomplete.")
    mapping = {f["id"]: f for f in formulas}
    for region in regions:
        text_parts = []
        tex_parts = []
        math_parts = []
        uncertain = []
        line_starts = []
        for segment in region["segments"]:
            if segment.get("line_break"):
                line_starts.append(len(tex_parts) + 1)
            fid = segment.get("formula_id")
            if fid and mapping[fid].get("skipped"):
                # Hidden regions are never emitted; keep their audit text only.
                text_parts.append(mapping[fid].get("native_text", ""))
                tex_parts.append(escape(mapping[fid].get("native_text", "")))
                continue
            if fid and verdicts[fid]["status"] == "text":
                mapping[fid]["review"] = verdicts[fid]
                text = mapping[fid].get("native_text", "")
                value = escape(text)
                size = mapping[fid].get("font_size")
                if MARKER.fullmatch(text.strip()) and (
                    mapping[fid].get("script_position") == "super"
                    or (size and size < 0.8 * (region.get("font_size") or 10))
                ):
                    # Footnote markers are set smaller and raised in the source.
                    value = r"\textsuperscript{" + escape(text.strip()) + "}"
                if mapping[fid].get("source_blue") and re.fullmatch(
                    r"\s*\[[0-9,\s–-]+\]\s*", text
                ):
                    value = r"{\color{sourceblue}" + value + "}"
                text_parts.append(text)
                tex_parts.append(value)
                continue
            if fid:
                row = verdicts[fid]
                if row["status"] == "uncertain":
                    uncertain.append(fid)
                    value = r"\text{[?]}"
                else:
                    value = safe_math(row["latex"])
                    position = (
                        mapping[fid].get("script_position")
                        if mapping[fid].get("script_is_isolated")
                        else None
                    )
                    if position in ("super", "sub") and not value.startswith((
                        "^",
                        "_",
                        "{}^",
                        "{}_",
                    )):
                        value = (
                            "{}"
                            + ("^" if position == "super" else "_")
                            + "{"
                            + value
                            + "}"
                        )
                mapping[fid]["review"] = row
                native = mapping[fid].get("native_text", "")
                # A crop may include a boundary comma already emitted by the
                # native text run. Keep the reviewed math unchanged, consume the
                # duplicated native punctuation exactly once.
                punctuation = value.lstrip()[:1]
                if (
                    punctuation in (",", ";", ":")
                    and text_parts
                    and text_parts[-1].endswith(punctuation)
                    and not native.lstrip().startswith(punctuation)
                ):
                    text_parts[-1] = text_parts[-1][:-1]
                    tex_parts[-1] = re.sub(
                        re.escape(punctuation) + r"(?=\}*$)", "", tex_parts[-1], count=1
                    )
                prefix = " " if native[:1].isspace() else ""
                suffix = " " if native[-1:].isspace() else ""
                text_parts.append(prefix + "$" + value + "$" + suffix)
                math_tex = "$" + value + "$"
                if (
                    row["status"] != "uncertain"
                    and mapping[fid].get("source_blue")
                    and re.fullmatch(r"\[[0-9,\s–-]+\]", value)
                ):
                    math_tex = r"{\color{sourceblue}" + math_tex + "}"
                tex_parts.append(prefix + math_tex + suffix)
                math_parts.append(value)
            else:
                text = segment.get("text", "")
                # Mirror of the leading-comma rule: a reviewed "512." followed by
                # the native "." would print the period twice.
                if (
                    math_parts
                    and tex_parts
                    and tex_parts[-1].rstrip().endswith("$")
                    and text[:1] in (".", ",", ";", ":")
                    and math_parts[-1].rstrip().endswith(text[:1])
                ):
                    text = text[1:]
                value = escape(text)
                if text.strip() and MONOSPACE.search(segment.get("font_name", "")):
                    value = r"\texttt{" + value + "}"
                if segment.get("italic"):
                    value = r"\textit{" + value + "}"
                if segment.get("bold"):
                    value = r"\textbf{" + value + "}"
                text_parts.append(text)
                tex_parts.append(value)
                if region["type"] == "equation" and text.strip():
                    number = re.fullmatch(r"\s*\((\d+[a-z]?)\)\s*", text)
                    math_parts.append(
                        r"\tag{" + number[1] + "}"
                        if number
                        else r"\text{" + escape(text) + "}"
                    )
        region["text"] = "".join(text_parts).strip()
        region["tex"] = "".join(tex_parts).strip()
        bounds = [0] + line_starts + [len(tex_parts)]
        region["tex_lines"] = [
            line
            for line in (
                "".join(tex_parts[a:b]).strip() for a, b in zip(bounds, bounds[1:])
            )
            if line
        ]
        region["review"] = bool(uncertain)
        region["unresolved_formulas"] = uncertain
        if region["type"] == "equation":
            region["latex"] = " ".join(math_parts)
        region["alignment"] = "justified" if region["type"] == "paragraph" else "left"
        region["format"] = {
            k: region.get(k)
            for k in (
                "layout_label",
                "font_size",
                "font_name",
                "bold",
                "italic",
                "alignment",
                "first_line_indent",
                "layout_columns",
                "column",
                "wide",
            )
        }
    return regions


def area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def overlap(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )


def hide_unemitted(regions, formulas):
    """Mark regions that never reach the TeX before any OCR or review is spent.

    Vector assets already contain their labels and table cells, and BabelDOC
    also exposes those glyphs as fallback paragraphs; page decorations are
    metadata. Their formulas are recorded as skipped instead of reviewed.
    """
    # BabelDOC flags some footnote markers as corner marks; others are plain
    # symbol formulas at the start of the note.
    marker = {
        f["id"]
        for f in formulas
        if f.get("corner_mark")
        or re.fullmatch(r"[*∗†‡§¶\d]{1,3}", f.get("native_text", "").strip())
    }
    for region in regions:
        label = region["layout_label"]
        if label in ("table_footnote", "figure_footnote") and re.match(
            r"^(?:Table|Figure|Fig\.)\s*\d+[.:]", region["text"]
        ):
            region["layout_label"] = (
                "table_caption" if label == "table_footnote" else "figure_caption"
            )
        # Layout models often label bottom-of-page footnotes as "abandon".
        first = next(
            (
                s
                for s in region.get("segments", [])
                if s.get("formula_id") or s.get("text", "").strip()
            ),
            {},
        )
        if (
            label == "abandon"
            and first.get("formula_id") in marker
            and len(region["text"]) > 12
        ):
            region.update(type="footnote", layout_label="footnote")
    # Largest first, so a panel inside a whole-figure detection is the duplicate.
    assets = sorted(
        (r for r in regions if r.get("asset")),
        key=lambda r: area(r["source_bbox"]),
        reverse=True,
    )
    kept = defaultdict(list)
    for region in assets:
        box = region["source_bbox"]
        for other in kept[region["page"]]:
            if (
                region["type"] == other["type"]
                and area(box)
                and (overlap(box, other["source_bbox"]) / area(box) > 0.9)
            ):
                region.update(
                    merged_into=other["id"], representation="duplicate-layout-detection"
                )
                break
        else:
            kept[region["page"]].append(region)
    for region in regions:
        label = region["layout_label"]
        if not region.get("asset") and "caption" not in label:
            box = region["source_bbox"]
            for asset in kept[region["page"]]:
                if area(box) and overlap(box, asset["source_bbox"]) / area(box) > 0.9:
                    region["merged_into"] = asset["id"]
                    region["representation"] = "text-in-vector-asset"
                    break
        # Marginal page numbers/headers are metadata, not body paragraphs.
        if label in (
            "page_number",
            "header",
            "footer",
            "page_header",
            "page_footer",
            "abandon",
        ) and not region.get("merged_into"):
            region["merged_into"] = "page-decoration"
    hidden = {r["id"]: r["merged_into"] for r in regions if r.get("merged_into")}
    for f in formulas:
        if f["region_id"] in hidden:
            f["skipped"] = "region-not-emitted: " + hidden[f["region_id"]]
        else:
            f.pop("skipped", None)
    return regions


def prepare_structure(regions, geometry):
    """Associate captions and frontmatter using layout labels and source geometry."""
    assets_by_page = defaultdict(list)
    for region in regions:
        if region.get("asset") and not region.get("merged_into"):
            assets_by_page[region["page"]].append(region)
    for region in regions:
        label = region["layout_label"]
        if (
            "caption" in label
            and region.get("bold")
            and len(region["text"]) < 160
            and re.match(r"^\d+\.\d+\.?\s+[A-Z]", region["text"])
        ):
            region.update(type="heading", layout_label="title")
            label = "title"
        if "caption" in label:
            candidates = [
                r
                for r in assets_by_page[region["page"]]
                if abs(
                    (r["bbox"][0] + r["bbox"][2])
                    - (region["bbox"][0] + region["bbox"][2])
                )
                < 200
            ]
            if candidates:
                target = min(
                    candidates,
                    key=lambda r: min(
                        abs(region["bbox"][1] - r["bbox"][3]),
                        abs(r["bbox"][1] - region["bbox"][3]),
                    ),
                )
                if (
                    min(
                        abs(region["bbox"][1] - target["bbox"][3]),
                        abs(target["bbox"][1] - region["bbox"][3]),
                    )
                    < 100
                ):
                    target["caption"] = (
                        target.get("caption", "") + " " + region["text"]
                    ).strip()
                    target.setdefault("caption_regions", []).append(region)
                    region["merged_into"] = target["id"]
    first = next((r for r in regions if not r.get("merged_into")), None)
    if first and first["type"] == "heading" and not re.match(r"^\d", first["text"]):
        for r in regions[regions.index(first) + 1 :]:
            if r.get("merged_into"):
                continue
            if r["page"] != first["page"]:
                break
            if r.get("asset") and r.get("wide"):
                r["role"] = "frontmatter_figure"
                continue
            if r["text"].lower().startswith("abstract") or len(r["text"]) > 650:
                break
            if (
                r["type"] == "heading"
                and len(r["text"]) < 120
                and not re.match(r"^\d", r["text"])
            ):
                # Layout models label bold author names as titles.
                r["type"] = "paragraph"
            if r["type"] != "paragraph":
                break
            r["role"] = "frontmatter"
            r["alignment"] = "center"
            r["format"]["alignment"] = "center"
    references = False
    for r in regions:
        if r.get("merged_into"):
            continue
        if r["type"] == "heading":
            references = r["text"].strip().lower() in ("references", "bibliography")
        elif references and r["type"] == "paragraph":
            r["role"] = "bibliography"
        elif (
            r["type"] == "paragraph"
            and r["text"].startswith("1. ")
            and re.search(r"\b2\.\s+", r["text"])
        ):
            r["role"] = "numbered_list"
    return regions


def infer_region_spacing(regions, geometry):
    """Estimate flow spacing from source blocks rather than article defaults."""
    above = []
    below = []
    indents = []
    after_heading = []
    geometry["preserve_source_breaks"] = True
    geometry["source_column_frames"] = True
    active = [
        r
        for r in regions
        if not r.get("merged_into") and not r.get("role", "").startswith("frontmatter")
    ]
    active_columns = defaultdict(list)
    text_columns = defaultdict(list)
    for region in active:
        active_columns[region["page"], region.get("column")].append(region)
    for region in regions:
        if region["type"] in ("paragraph", "heading"):
            text_columns[region["page"], region.get("column")].append(region)
    for r in active:
        same = active_columns[r["page"], r.get("column")]
        before = [
            v for v in same if v["source_bbox"][3] <= r["source_bbox"][1] and v is not r
        ]
        after = [
            v for v in same if v["source_bbox"][1] >= r["source_bbox"][3] and v is not r
        ]
        previous = max(before, key=lambda v: v["source_bbox"][3]) if before else None
        following = min(after, key=lambda v: v["source_bbox"][1]) if after else None
        if r["type"] == "heading":
            if previous and not previous.get("asset"):
                r["space_before"] = round(
                    max(0, min(40, r["source_bbox"][1] - previous["source_bbox"][3])), 2
                )
            if following and following["type"] == "paragraph":
                r["space_after"] = round(
                    max(0, min(40, following["source_bbox"][1] - r["source_bbox"][3])),
                    2,
                )
        if (
            r["type"] == "paragraph"
            and r.get("layout_label") == "plain text"
            and r.get("role") != "bibliography"
        ):
            if previous and previous["type"] == "paragraph":
                r["space_before"] = round(
                    max(
                        0, min(40, r["source_bbox"][1] - previous["source_bbox"][3] - 2)
                    ),
                    2,
                )
            column_left = (
                geometry["left"]
                + (
                    geometry["paper_width"]
                    - geometry["left"]
                    - geometry["right"]
                    + geometry["column_gap"]
                )
                / 2
                if r.get("column") == "right"
                else geometry["left"]
            )
            offset = r.get("first_line_offset", 0) + r["source_bbox"][0] - column_left
            if 6 < offset < 20:
                r.update(first_line_indent=True, first_line_offset=round(offset, 2))
    families = Counter(
        r.get("math_font_family", "unknown") for r in regions if r.get("formula_ids")
    )
    if families:
        geometry["math_font_family"] = families.most_common(1)[0][0]
    for i, r in enumerate(active):
        if (
            r["type"] == "paragraph"
            and r.get("first_line_indent")
            and 2 < r.get("first_line_offset", 0) < 30
        ):
            indents.append(r["first_line_offset"])
        if (
            r["type"] == "heading"
            and i + 1 < len(active)
            and active[i + 1]["type"] == "paragraph"
        ):
            after_heading.append(bool(active[i + 1].get("first_line_indent")))
        if r["type"] != "equation":
            continue
        top, bottom = r["source_bbox"][1], r["source_bbox"][3]
        same = text_columns[r["page"], r.get("column")]
        before = [v for v in same if v["source_bbox"][3] <= top]
        after = [v for v in same if v["source_bbox"][1] >= bottom]
        if before:
            gap = top - max(v["source_bbox"][3] for v in before)
            if 0 <= gap < 25:
                above.append(gap)
        if after:
            gap = min(v["source_bbox"][1] for v in after) - bottom
            if 0 <= gap < 25:
                below.append(gap)
    if above:
        geometry["display_above"] = round(statistics.median(above), 2)
    if below:
        geometry["display_below"] = round(statistics.median(below), 2)
    if indents:
        geometry["paragraph_indent"] = round(statistics.median(indents), 2)
    if after_heading:
        geometry["indent_after_heading"] = sum(after_heading) > len(after_heading) / 2
    # Native-text margin estimates can miss the descenders of the bottom display.
    # Enlarge the body frame only when an actual body region extends past it.
    body_bottom = [
        r["source_bbox"][3]
        for r in regions
        if not r.get("merged_into")
        and r["type"] in ("paragraph", "equation", "figure_crop", "table_crop")
    ]
    if body_bottom:
        geometry["bottom"] = round(
            min(
                geometry["bottom"],
                max(20, geometry["paper_height"] - max(body_bottom) - 2),
            ),
            2,
        )
    page_tops = {}
    for r in regions:
        if not r.get("merged_into"):
            page_tops[r["page"]] = min(
                page_tops.get(r["page"], float("inf")), r["source_bbox"][1]
            )
    if page_tops:
        geometry["top"] = round(
            max(20, min(geometry["top"], statistics.median(page_tops.values()))), 2
        )
    return geometry


def emit_regions(regions, root, geometry):
    """Reuse structured emitter, split at block boundaries, and assemble with inputs."""
    flat = root / "main-flat.tex"
    emit_document(regions, flat, geometry)
    text = flat.read_text()
    # Block IDs are locally generated ASCII identifiers, never PDF-provided paths.
    pattern = r"(?m)^% (p\d{3}-(?:r\d{4}|figure\d{3}|table\d{3})) / [^\n]+\n"
    matches = list(re.finditer(pattern, text))
    if not matches:
        raise ValueError("No region boundaries in generated TeX.")
    folder = root / "regions"
    folder.mkdir(exist_ok=True)
    main = text[: matches[0].start()]
    end = text.rfind(r"\end{document}")
    files = {}
    for i, match in enumerate(matches):
        finish = matches[i + 1].start() if i + 1 < len(matches) else end
        content = text[match.start() : finish]
        name = match[1] + ".tex"
        (folder / name).write_text(content, encoding="utf-8")
        files[match[1]] = "regions/" + name
        main += r"\input{regions/" + name + "}%\n"
    main += "\\end{document}\n"
    (root / "main.tex").write_text(main, encoding="utf-8")
    for region in regions:
        region["tex_file"] = files[region["id"]]


def convert_hybrid(args):
    import fitz
    from .cli import compile_project

    if args.content_list or args.source_archive or args.ocr_command:
        raise ValueError(
            "Hybrid uses BabelDOC layout and formula-only OCR; use --ocr-python for its runtime."
        )
    pdf = Path(args.pdf).resolve()
    if not pdf.is_file():
        raise ValueError("Input PDF does not exist: " + str(pdf))
    root = Path(args.output or ("output/" + pdf.stem)).resolve()
    with fitz.open(pdf) as doc:
        total_pages = len(doc)
        selected = args.pages if args.pages is not None else list(range(len(doc)))
        if not selected or min(selected) < 0 or max(selected) >= len(doc):
            raise ValueError("Invalid page selection.")
        for i in selected:
            if doc[i].rotation or doc[i].cropbox != doc[i].mediabox:
                raise ValueError(
                    "Hybrid currently requires unrotated pages with matching CropBox/MediaBox; normalize the PDF first."
                )
            if len(doc[i].get_text().strip()) < 20:
                raise ValueError(
                    "Page "
                    + str(i + 1)
                    + " has insufficient native text; scanned-page OCR is not yet supported by hybrid mode."
                )
    provider = args.llm_provider
    command = args.llm_command or args.codex_command or provider
    reviewer = provider if args.layout_review != "local" else "local"
    identity = {
        "version": VERSION,
        "input_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "pages": selected,
        "layout_profile": args.layout_profile,
        "layout_review": reviewer,
        "llm_provider": provider,
    }
    if args.codex_effort is not None:
        identity["codex_effort"] = args.codex_effort
    statepath = root / "pipeline.json"
    if root.exists() and any(root.iterdir()):
        if not args.resume or not statepath.exists():
            raise ValueError(
                "Nonempty output: use a new directory, or --resume for this hybrid run."
            )
        state = json.loads(statepath.read_text())
        if state["identity"] != identity:
            raise ValueError("Cannot resume: input, pages, or layout settings differ.")
    else:
        root.mkdir(parents=True, exist_ok=True)
        state = {"identity": identity, "status": "started"}
    start = time.monotonic()

    def stage(name):
        state.pop("error", None)
        state.update(status="running", stage=name)
        save(statepath, state)
        print("Hybrid: " + name, file=sys.stderr, flush=True)

    try:
        stage("babeldoc-layout")
        folder = root / "babeldoc"
        folder.mkdir(exist_ok=True)
        irpath = folder / "layout-ir.json"
        if not irpath.exists() or not (folder / "provenance.json").exists():
            worker(
                runtime("babeldoc", args.babeldoc_python),
                "babeldoc_worker",
                [pdf, folder, ",".join(str(i + 1) for i in selected)],
                folder / "extract.log",
            )
        stage("region-format-and-formula-crops")
        plan = assess_layout(
            pdf,
            selected,
            reviewer,
            args.layout_profile,
            codex=command,
            timeout=args.layout_timeout,
            provider=provider,
            codex_effort=args.codex_effort,
        )
        save(root / "layout-plan.json", plan)
        if (
            plan["review"]
            and not plan["manual_override"]
            and (
                plan["review"]["confidence"] < 0.75
                or plan["review"]["family"] == "unknown"
            )
        ):
            raise ValueError(
                "Uncertain page layout: inspect layout-plan.json, then rerun with an explicit --layout-profile in a new output."
            )
        regions, formulas, warnings = normalize_ir(
            json.loads(irpath.read_text()), pdf, root, selected
        )
        groups = fragment_groups(regions)
        fragment_review = {"groups": 0, "merged": 0}
        if groups:
            stage(provider + "-fragment-review")
            fragment_verdicts, fragment_review = review_fragments(
                groups,
                regions,
                pdf,
                root,
                command,
                args.formula_timeout,
                provider=provider,
                codex_effort=args.codex_effort,
            )
            merged = merge_fragments(regions, formulas, groups, fragment_verdicts)
            fragment_review["merged"] = len(merged)
            save(
                root / "fragment-verdicts.json",
                [dict(g, verdict=fragment_verdicts[g["id"]]) for g in groups],
            )
        regions, order_warnings = prepare_blocks(regions, plan)
        hide_unemitted(regions, formulas)
        active = [f for f in formulas if not f.get("skipped")]
        render_overlays(pdf, regions, formulas, root)
        save(root / "regions.json", regions)
        save(root / "formulas.json", formulas)
        stage("formula-ocr")
        ocrpath = root / "formula-ocr.json"
        old = (
            {r["id"]: r for r in json.loads(ocrpath.read_text())}
            if ocrpath.exists()
            else {}
        )
        hashes = {
            f["id"]: hashlib.sha256((root / f["crop"]).read_bytes()).hexdigest()
            for f in active
        }
        valid_cache = set(old) == set(hashes) and all(
            old[k].get("crop_sha256") == v for k, v in hashes.items()
        )
        if not active:
            save(ocrpath, [])
        elif not valid_cache or not ocrpath.exists():
            worker(
                runtime("ocr", args.ocr_python),
                "formula_ocr_worker",
                [root],
                root / "formula-ocr.log",
            )
        rows = json.loads(ocrpath.read_text())
        ocr = {r["id"]: r for r in rows}
        if len(rows) != len(active) or set(ocr) != {f["id"] for f in active}:
            raise ValueError("OCR result IDs do not match all formula crops.")
        for f in active:
            f.update(ocr[f["id"]])
        save(root / "formulas.json", formulas)
        stage(provider + "-formula-review")
        verdicts, review = review_formulas(
            active,
            root,
            command,
            args.formula_batch_size,
            args.formula_timeout,
            args.retry_formulas,
            provider=provider,
            codex_effort=args.codex_effort,
        )
        uncertain = [f for f in active if verdicts[f["id"]]["status"] == "uncertain"]
        review["rounds"] = 1
        if uncertain and args.formula_review_rounds == 2:
            stage(provider + "-formula-context-review")
            for f in uncertain:
                f["previous_review"] = verdicts[f["id"]]
            context_crops(pdf, uncertain, root)
            second, stats = review_formulas(
                uncertain,
                root,
                command,
                args.formula_batch_size,
                args.formula_timeout,
                args.retry_formulas,
                context=True,
                provider=provider,
                codex_effort=args.codex_effort,
            )
            for fid, row in second.items():
                row["first_review"] = verdicts[fid]
                row["round"] = 2
            verdicts.update(second)
            review.update(
                rounds=2,
                context_reviewed=len(second),
                new_calls=review["new_calls"] + stats["new_calls"],
                cached_batches=review["cached_batches"] + stats["cached_batches"],
                uncertain=sum(r["status"] == "uncertain" for r in verdicts.values()),
                text=sum(r["status"] == "text" for r in verdicts.values()),
            )
        save(root / "formula-verdicts.json", list(verdicts.values()))
        stage("region-tex-and-assembly")
        apply_formulas(regions, formulas, verdicts)
        prepare_structure(regions, plan["geometry"])
        infer_region_spacing(regions, plan["geometry"])
        save(root / "layout-plan.json", plan)
        emit_regions(regions, root, plan["geometry"])
        save(root / "regions.json", regions)
        save(root / "blocks.json", regions)
        save(root / "formulas.json", formulas)
        compiled = (
            compile_project(root) if args.compile else {"status": "not_requested"}
        )
        frame_data = None
        if compiled["status"] == "passed" and plan["geometry"].get(
            "source_column_frames"
        ):
            from .pageflow import frame_fit

            frame_data = frame_fit((root / "compile.log").read_text(errors="replace"))
        report = {
            "backend": "babeldoc_hybrid",
            "input_name": pdf.name,
            **identity,
            "layout": plan["geometry"],
            "formula_ocr_model": "UniMERNet-small (MinerU 2.7.1)",
            "formula_review": review,
            "fragment_review": fragment_review,
            "detected_formulas": len(formulas),
            "skipped_formulas": len(formulas) - len(active),
            "inline_formulas": sum(f["kind"] == "inline" for f in formulas),
            "display_formulas": sum(f["kind"] == "display" for f in formulas),
            "counts": dict(Counter(r["type"] for r in regions)),
            "review_blocks": [r["id"] for r in regions if r.get("review")],
            "warnings": warnings + order_warnings,
            "limitations": [
                "Review coverage is over detected formula regions; it is not a recall measurement.",
                "Tables and figure-internal labels remain vector assets; they are not editable TeX.",
                "Semantic TeX reflows; identical pagination is not guaranteed.",
            ],
            "compile": compiled,
            "frame_fit": frame_data,
            "unresolved_formula_ids": sorted(
                fid for fid, row in verdicts.items() if row["status"] == "uncertain"
            ),
            "duration_seconds": round(time.monotonic() - start, 2),
        }
        save(root / "report.json", report)
        (root / "BUILD.txt").write_text(
            "xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error main.tex\n"
        )
        if args.evaluate and compiled["status"].startswith("passed"):
            if len(selected) == total_pages:
                from .quality import assess
                from .evaluate import evaluate

                assessment = assess(pdf, root)
                pixels = evaluate(
                    pdf, root / "main.pdf", root / "pixel-comparison", dpi=100
                )
                if frame_data and frame_data["significant"]:
                    assessment["layout_status"] = "frame_overflow"
                    save(root / "evaluation.json", assessment)
                report["evaluation"] = {
                    "file": "evaluation.json",
                    "layout_status": assessment["layout_status"],
                    "pixel_file": "pixel-comparison/evaluation.json",
                    "visual_fidelity_passed": pixels["passed"],
                    "mean_page_mae_255": pixels["mean_page_mae_255"],
                }
            else:
                from .evaluate import evaluate

                selection = root / "source-selection.pdf"
                with fitz.open(pdf) as original, fitz.open() as subset:
                    for i in selected:
                        subset.insert_pdf(original, from_page=i, to_page=i)
                    subset.save(selection)
                assessment = evaluate(selection, root / "main.pdf", root / "comparison")
                report["evaluation"] = {
                    "file": "comparison/evaluation.json",
                    "visual_fidelity_passed": assessment["passed"],
                    "source_pages": len(selected),
                }
                with fitz.open(root / "main.pdf") as rebuilt:
                    report["evaluation"]["output_pages"] = len(rebuilt)
            save(root / "report.json", report)
        state.update(status="completed", stage="completed")
        save(statepath, state)
        print(
            json.dumps(
                {
                    "output": str(root),
                    "regions": len(regions),
                    "formulas": len(formulas),
                    "review": review,
                    "compile": compiled["status"],
                },
                ensure_ascii=False,
            )
        )
        return (
            1
            if review["uncertain"] or (args.compile and compiled["status"] != "passed")
            else 0
        )
    except Exception as exc:
        state.update(status="failed", error=str(exc))
        save(statepath, state)
        raise
