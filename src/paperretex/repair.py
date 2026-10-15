"""Apply validated equation data from a bounded review; never execute model code."""

import json
from pathlib import Path
from .latex import safe_math


def apply_saved(blocks, out):
    path = Path(out) / "corrections.json"
    if not path.exists():
        return blocks
    corrections = json.loads(path.read_text())
    for b in blocks:
        if b["id"] in corrections:
            c = corrections[b["id"]]
            if b["type"] != "equation" or b["page"] != c["page"]:
                raise ValueError("Correction no longer matches its source block.")
            b.update(
                latex=safe_math(c["latex"]),
                correction_provenance="codex_visual_review",
                review=True,
            )
    return blocks


def save_review(review, root):
    review = Path(review)
    root = Path(root).resolve()
    applied = []
    for item in json.loads(review.read_text()).get("equations", []):
        if item.get("confidence", "").lower() != "high":
            continue
        name, separator, id = item["document"].partition(":")
        if not separator:
            continue
        out = (root / name).resolve()
        if out.parent != root:
            raise ValueError("Review document must be a direct child project.")
        blocks = json.loads((out / "blocks.json").read_text())
        matches = [
            b
            for b in blocks
            if b["id"] == id and b["page"] == item["page"] and b["type"] == "equation"
        ]
        if len(matches) != 1:
            raise ValueError("Review block not found uniquely: " + item["document"])
        latex = safe_math(item["latex"])
        path = out / "corrections.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        data[id] = {
            "page": item["page"],
            "latex": latex,
            "previous_latex": matches[0]["latex"],
            "review_file": str(review),
            "confidence": "high",
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        applied.append(item["document"])
    return applied
