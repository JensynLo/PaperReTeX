#!/usr/bin/env python3
"""PDF-only held-out fixtures. Never reads recovered source directories."""

import argparse
import html
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
import re
import fitz
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
SAMPLES = {
    "EMS": "2111.14517-EMS.pdf",
    "Kobsik": "2607.05568-Kobsik-Primitive-Shape-Abstraction.pdf",
    "Light-SQ": "2509.24986-Light-SQ.pdf",
    "ResFit": "2512.09201-ResFit-SuperFrusta.pdf",
}

from paperretex.quality import assess


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="output/reverse")
    p.add_argument("--only", choices=list(SAMPLES))
    a = p.parse_args()
    root = Path(a.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, file in SAMPLES.items():
        if a.only and a.only != name:
            continue
        dest = root / name
        source = ROOT / "samples" / file
        if not (dest / "report.json").exists():
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "paperretex",
                    "convert",
                    str(source),
                    "-o",
                    str(dest),
                    "--compile",
                ],
                cwd=ROOT,
            )
            if not (dest / "report.json").exists():
                rows.append(
                    dict(
                        name=name, error="conversion failed", returncode=proc.returncode
                    )
                )
                continue
        result = assess(source, dest)
        rows.append(dict(name=name, **result))
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
        (root / "summary.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2)
        )
    body = [
        '<!doctype html><meta charset="utf-8"><title>PDF-only reverse evaluation</title><style>body{max-width:1100px;margin:40px auto;font:16px/1.6 system-ui}img{width:100%}td,th{padding:10px;border-bottom:1px solid #ddd}</style><h1>仅输入 PDF 的逆向评测</h1><p>识别输入仅为 samples 中的 PDF；未读取原始 LaTeX 源码。以下是真实重建结果，不以编译通过代表识别准确或版式通过。</p>'
    ]
    review_root = ROOT / "output/codex-review"
    for round_number in (1, 2):
        review_path = review_root / f"round-{round_number}-retry-1-result.json"
        if review_path.exists():
            review = json.loads(review_path.read_text())
            body.append(
                f"<h2>Codex 第 {round_number} 轮评审</h2><p>{html.escape(review['summary'])}</p>"
            )
            for finding in review.get("findings", []):
                body.append(
                    "<details><summary>"
                    + html.escape(finding["document"] + " / " + str(finding["page"]))
                    + "</summary><p>"
                    + html.escape(finding["issue"])
                    + "</p><p>"
                    + html.escape(finding["suggested_fix"])
                    + "</p></details>"
                )
    body.append(
        "<p>报告针对当前生成结果。版式预检分数表示模板适配度，不代表复刻准确率；请结合各工程的 layout-plan.json、evaluation.json 和页面对照检查。公式仍需数学审阅，历史评审如存在仅针对当轮快照。</p>"
    )
    for row in rows:
        name = row["name"]
        body.append("<h2>" + html.escape(name) + "</h2>")
        if "error" in row:
            body.append("<p>Conversion failed</p>")
            continue
        body.append(
            f'<p>页数 {row["source_pages"]} → {row["output_pages"]}；编译 {row["compile"]["status"]}；词汇覆盖代理 {row["word_recall_proxy"]:.1%}；未解决块 {len(row["unresolved"])}。</p><p><a href="{name}/main.tex">LaTeX</a> · <a href="{name}/main.pdf">PDF</a> · <a href="{name}/evaluation.json">完整指标</a></p><pre>{html.escape(json.dumps(row["counts"], ensure_ascii=False))}</pre>'
        )
        for i in range(1, max(row["source_pages"], row["output_pages"]) + 1):
            body.append(
                f'<details><summary>第 {i} 页对照</summary><img loading="lazy" src="{name}/comparison/page-{i:03d}.png"></details>'
            )
    (root / "report.html").write_text("\n".join(body))


if __name__ == "__main__":
    main()
