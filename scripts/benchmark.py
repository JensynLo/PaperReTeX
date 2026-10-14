#!/usr/bin/env python3
"""Run all four authorized fixtures and write an honest visual-fidelity report."""
import argparse
import html
import json
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from paperretex.evaluate import evaluate
SAMPLES={'EMS':'2111.14517-EMS.pdf','Kobsik':'2607.05568-Kobsik-Primitive-Shape-Abstraction.pdf','Light-SQ':'2509.24986-Light-SQ.pdf','ResFit':'2512.09201-ResFit-SuperFrusta.pdf'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',default='output/final');a=p.parse_args()
    root=Path(a.output).resolve();root.mkdir(parents=True,exist_ok=True)
    rows=[]
    for name,filename in SAMPLES.items():
        source=ROOT/'samples'/filename;dest=root/name
        run=subprocess.run([sys.executable,'-m','paperretex','convert','--mode','layout',str(source),'-o',str(dest),'--compile'],cwd=ROOT)
        if run.returncode: raise SystemExit('Conversion failed: '+name)
        result=evaluate(source,dest/'main.pdf',root/'evaluation'/name)
        conversion=json.loads((dest/'report.json').read_text());counts=conversion['counts']
        denominator=counts['editable_glyphs']+counts['background_glyphs']
        row={'name':name,'pages':counts['pages'],'compile':conversion['compile']['status'],
             'mean_page_mae_255':result['mean_page_mae_255'],
             'max_ink_mae_255':max(x['ink_mae_255'] for x in result['pages']),
             'visual_pass':result['passed'],'editable_glyph_fraction':round(counts['editable_glyphs']/max(1,denominator),5),
             'background_glyphs':counts['background_glyphs'],'embedded_formulas':len(conversion.get('embedded_formulas',[]))}
        rows.append(row);print(json.dumps(row),flush=True)
    summary={'documents':rows,'total_pages':sum(r['pages'] for r in rows),
             'meaning':'Visual similarity is NOT semantic LaTeX recovery accuracy. Text is positioned glyphs; artwork remains vector PDF.',
             'codex_review_log':'../codex-review/calls.json'}
    (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    lines=['<!doctype html><html lang="zh"><meta charset="utf-8"><title>PDF → LaTeX 评测</title><style>body{max-width:1100px;margin:40px auto;font:16px/1.6 system-ui;color:#19212a}table{border-collapse:collapse;width:100%}th,td{padding:10px;border-bottom:1px solid #ddd;text-align:left}img{width:100%}code{background:#eee;padding:2px 5px}a{color:#146b9b}</style><h1>PDF → LaTeX 版式重建评测</h1><p>58 页、144 DPI，原图与重建 PDF 逐页比较。像素误差越低越好（0–255）。文字按原始字形、坐标重排，图形保留为矢量背景。此评测不代表语义公式恢复准确率。</p><table><tr><th>文档</th><th>页数</th><th>编译</th><th>平均像素误差</th><th>LaTeX 字形覆盖</th><th>内嵌公式恢复</th></tr>']
    for r in rows:
        n=html.escape(r['name']);lines.append(f'<tr><td><a href="{n}/main.pdf">{n}</a> · <a href="{n}/main.tex">LaTeX</a></td><td>{r["pages"]}</td><td>{r["compile"]}</td><td>{r["mean_page_mae_255"]}</td><td>{r["editable_glyph_fraction"]:.2%}</td><td>{r["embedded_formulas"]}</td></tr>')
    lines.append('</table><p>“LaTeX 字形覆盖”是独立重新排出的可见字形比例，不表示恢复了原作者的宏、段落和公式结构。未支持的少数字体保留在背景中。默认阈值：每页平均误差 &lt; 2.5、非白像素区域平均误差 &lt; 15；这只是本工具的启发式视觉门槛。</p>')
    for r in rows:
        n=r['name'];evaluation=json.loads((root/'evaluation'/n/'evaluation.json').read_text());worst=evaluation['worst_pages'][0]['page']
        lines.append(f'<h2>{html.escape(n)}：差异最大页 {worst}</h2><p><a href="evaluation/{n}/evaluation.json">全部逐页指标</a> · <a href="{n}/report.json">转换报告</a></p><img src="evaluation/{n}/page-{worst:03d}.png" alt="原图、重建、差异对照">')
    lines.append('</html>');(root/'report.html').write_text('\n'.join(lines))
if __name__=='__main__':main()
