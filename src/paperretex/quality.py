"""PDF-only reconstruction checks; never uses author sources as ground truth."""

import json
import re
from pathlib import Path
from collections import Counter
import fitz
from PIL import Image, ImageDraw


def assess(source, dest):
    source = Path(source)
    dest = Path(dest)
    report = json.loads((dest / "report.json").read_text())
    blocks = json.loads((dest / "blocks.json").read_text())

    def words(text):
        return Counter(re.findall(r"[a-z]{3,}", text.lower()))

    with fitz.open(source) as a:
        original = words(" ".join(p.get_text() for p in a))
        np = len(a)
        rebuilt = Counter()
        nq = 0
        if (dest / "main.pdf").exists():
            with fitz.open(dest / "main.pdf") as b:
                nq = len(b)
                rebuilt = words(" ".join(p.get_text() for p in b))
                review = dest / "comparison"
                review.mkdir(exist_ok=True)
                for stale in review.glob("page-*.png"):
                    stale.unlink()
                for i in range(max(np, nq)):
                    sheet = Image.new("RGB", (1200, 850), "#ddd")
                    draw = ImageDraw.Draw(sheet)
                    for j, doc in enumerate((a, b)):
                        draw.text(
                            (j * 600 + 10, 8),
                            ("Input PDF" if j == 0 else "Reconstructed")
                            + f" / page {i + 1}",
                            fill="black",
                        )
                        if i < len(doc):
                            pix = doc[i].get_pixmap(dpi=85, alpha=False)
                            im = Image.frombytes(
                                "RGB", (pix.width, pix.height), pix.samples
                            )
                            im.thumbnail((590, 810))
                            sheet.paste(im, (j * 600, 35))
                    sheet.save(review / f"page-{i + 1:03d}.png")
    counts = Counter(b["type"] for b in blocks)
    result = dict(
        input_policy=report.get("input_policy", "unknown"),
        source_lookup=report.get("source_lookup"),
        mathematical_accuracy="not_verified",
        layout_status="needs_visual_review",
        compile=report["compile"],
        source_pages=np,
        output_pages=nq,
        counts=dict(counts),
        word_recall_proxy=round(
            sum((original & rebuilt).values()) / max(1, sum(original.values())), 4
        ),
        unresolved=[
            b["id"]
            for b in blocks
            if b["type"]
            in (
                "equation_crop",
                "page_crop",
                "unresolved_equation",
                "table_crop",
                "unmapped_text_crop",
            )
        ],
        limitations=[
            "Word overlap is a coverage proxy, not mathematical accuracy.",
            "Page comparisons use page ordinal; flowing reconstruction may move content between pages.",
            "Semantic structure does not guarantee faithful spacing, numbering or OCR accuracy.",
        ],
    )
    placement = figure_placement(dest, blocks)
    result["figure_placement"] = placement
    if placement["status"] == "page_alignment_failed":
        result["layout_status"] = "page_alignment_failed"
    (dest / "evaluation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    return result


def figure_placement(dest, blocks):
    """Locate unchanged raster assets in the rebuilt PDF by decoded pixel digest.

    Matching source page ordinals is necessary, but not sufficient, for fidelity.
    Unmatched or repeated assets remain unverified, never counted as correct.
    """
    dest = Path(dest)
    if not (dest / "main.pdf").exists():
        return {"status": "unavailable", "figures": []}
    positions = {}
    with fitz.open(dest / "main.pdf") as doc:
        for page in doc:
            for item in page.get_image_info(hashes=True):
                positions.setdefault(item["digest"], set()).add(page.number + 1)
    rows = []
    for block in blocks:
        asset = block.get("asset")
        if not asset or block["type"] not in ("image_crop", "figure_crop"):
            continue
        row = {
            "block": block["id"],
            "source_page": block["page"],
            "output_pages": [],
            "status": "unverified",
        }
        path = dest / asset
        if path.is_file() and path.suffix.lower() in (".png", ".jpg", ".jpeg"):
            image = fitz.Pixmap(str(path))
            pages = sorted(positions.get(image.digest, set()))
            row["output_pages"] = pages
            if len(pages) == 1:
                row["status"] = "same_page" if pages[0] == block["page"] else "moved"
        rows.append(row)
    counts = Counter(r["status"] for r in rows)
    return {
        "status": "page_alignment_failed" if counts["moved"] else "needs_visual_review",
        "same_page": counts["same_page"],
        "moved": counts["moved"],
        "unverified": counts["unverified"],
        "figures": rows,
        "scope": "Exact raster-content matching; checks page placement only, not within-page position, scale, captions, or formulas.",
    }
