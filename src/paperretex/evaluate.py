"""Reproducible raster comparison. Scores measure appearance, not semantic LaTeX."""

import json
import math
from pathlib import Path
import fitz
import numpy as np
from PIL import Image, ImageDraw


def evaluate(source, rebuilt, out, dpi=144):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    with fitz.open(source) as a, fitz.open(rebuilt) as b:
        if len(a) != len(b):
            report = {
                "passed": False,
                "reason": "page_count_mismatch",
                "source_pages": len(a),
                "rebuilt_pages": len(b),
            }
            (out / "evaluation.json").write_text(json.dumps(report, indent=2))
            return report
        for i in range(len(a)):
            pa = a[i].get_pixmap(dpi=dpi, alpha=False, colorspace=fitz.csRGB)
            pb = b[i].get_pixmap(dpi=dpi, alpha=False, colorspace=fitz.csRGB)
            if (pa.width, pa.height) != (pb.width, pb.height):
                rows.append({
                    "page": i + 1,
                    "passed": False,
                    "reason": "page_size_mismatch",
                })
                continue
            aa = np.frombuffer(pa.samples, dtype=np.uint8).reshape(
                pa.height, pa.width, 3
            )
            bb = np.frombuffer(pb.samples, dtype=np.uint8).reshape(
                pb.height, pb.width, 3
            )
            diff = np.abs(aa.astype(np.float32) - bb.astype(np.float32))
            ink = (aa.min(axis=2) < 245) | (bb.min(axis=2) < 245)
            mae = float(diff.mean())
            ink_mae = float(diff[ink].mean()) if ink.any() else 0
            mse = float((diff**2).mean())
            # Tolerant thresholds flag structural failures, not rasterizer antialiasing.
            passed = mae < 2.5 and ink_mae < 15
            row = {
                "page": i + 1,
                "mae_255": round(mae, 4),
                "ink_mae_255": round(ink_mae, 4),
                "psnr_db": round(20 * math.log10(255 / math.sqrt(mse)), 3)
                if mse
                else None,
                "changed_ink_fraction": round(
                    float((diff.max(axis=2)[ink] > 32).mean()), 5
                )
                if ink.any()
                else 0,
                "passed": passed,
            }
            rows.append(row)
            # Render a review sheet per page so model calls can be batched by difficulty.
            ims = [
                Image.fromarray(aa),
                Image.fromarray(bb),
                Image.fromarray(np.minimum(diff * 4, 255).astype(np.uint8)),
            ]
            for im in ims:
                im.thumbnail((600, 850))
            sheet = Image.new("RGB", (1800, max(x.height for x in ims) + 38), "#e9e9e9")
            draw = ImageDraw.Draw(sheet)
            for j, (label, im) in enumerate(
                zip(["Original", "Reconstructed", "Difference x4"], ims)
            ):
                sheet.paste(im, (600 * j, 38))
                draw.text((600 * j + 10, 10), f"{label} / page {i + 1}", fill="black")
            sheet.save(out / f"page-{i + 1:03d}.png")
    valid = [r for r in rows if "mae_255" in r]
    report = {
        "passed": all(r["passed"] for r in rows),
        "dpi": dpi,
        "pages": rows,
        "mean_page_mae_255": round(sum(r["mae_255"] for r in valid) / len(valid), 4)
        if valid
        else None,
        "worst_pages": sorted(valid, key=lambda r: r["ink_mae_255"], reverse=True)[:5],
        "thresholds": {"page_mae_255_below": 2.5, "ink_mae_255_below": 15},
        "scope": "Visual fidelity only. Does not measure semantic equation recovery or editability.",
    }
    (out / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
