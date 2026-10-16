import json
import time
from pathlib import Path
from types import SimpleNamespace
import fitz
import pytest
from paperretex.preflight import assess_layout, local_assessment, sample_pages
from paperretex.profiles import prepare_blocks
from paperretex.latex import emit_document


def make_pdf(path, columns=(1, 2)):
    with fitz.open() as doc:
        for col in columns:
            page = doc.new_page(width=600, height=800)
            for row in range(14):
                if col == 1:
                    page.insert_text(
                        (50, 90 + row * 20),
                        "A long line of ordinary scientific prose extending across the width of this page.",
                        fontsize=10,
                    )
                else:
                    for x in (45, 315):
                        page.insert_text(
                            (x, 90 + row * 20),
                            "Ordinary scientific prose fills this column here.",
                            fontsize=9,
                        )
        doc.save(path)


def test_local_profiles_detect_transitions(tmp_path):
    pdf = tmp_path / "mixed.pdf"
    make_pdf(pdf)
    plan = assess_layout(pdf, reviewer="local")
    assert plan["family"] == "mixed"
    assert [p["columns"] for p in plan["pages"]] == [1, 2]
    assert sample_pages(plan["pages"]) == [1, 2]


def test_codex_preflight_one_cached_call_and_manual_override(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf, (2, 2))
    calls = []
    monkeypatch.setattr("paperretex.preflight.shutil.which", lambda x: "/fake/codex")

    def run(command, **kw):
        calls.append(command)
        response = {
            "family": "two_column",
            "confidence": 0.95,
            "single_column_score": 10,
            "two_column_score": 95,
            "text_source": "native",
            "pages": [
                {
                    "page": i,
                    "body_columns": 2,
                    "confidence": 0.95,
                    "full_width_elements": [],
                    "notes": "",
                }
                for i in (1, 2)
            ],
            "risks": [],
            "summary": "双栏正文",
        }
        Path(command[command.index("--output-last-message") + 1]).write_text(
            json.dumps(response)
        )
        return SimpleNamespace(returncode=0, stdout="done")

    monkeypatch.setattr("paperretex.preflight.subprocess.run", run)
    first = assess_layout(pdf, cache_root=tmp_path / "cache")
    second = assess_layout(pdf, cache_root=tmp_path / "cache")
    assert len(calls) == 1 and not first["cache_hit"] and second["cache_hit"]
    assert first["geometry"]["columns"] == 2
    manual = assess_layout(pdf, profile="single_column", cache_root=tmp_path / "cache")
    assert manual["columns"] == 1 and all(p["columns"] == 1 for p in manual["pages"])
    assert len(calls) == 1
    stronger = assess_layout(pdf, cache_root=tmp_path / "cache", codex_effort="high")
    assert not stronger["cache_hit"] and len(calls) == 2
    assert calls[-1][calls[-1].index("-c") + 1] == 'model_reasoning_effort="high"'
    assert assess_layout(pdf, cache_root=tmp_path / "cache", codex_effort="high")[
        "cache_hit"
    ]
    assert len(calls) == 2


def valid_response(pages):
    return {
        "family": "single_column",
        "confidence": 0.95,
        "single_column_score": 95,
        "two_column_score": 5,
        "text_source": "native",
        "pages": [
            {
                "page": i,
                "body_columns": 1,
                "confidence": 0.95,
                "full_width_elements": [],
                "notes": "",
            }
            for i in pages
        ],
        "risks": [],
        "summary": "单栏正文",
    }


def test_failed_call_is_not_cached_and_retries(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf, (1,))
    calls = []
    replies = [
        SimpleNamespace(returncode=1, stdout="workspace routing discovery timed out"),
        SimpleNamespace(returncode=0, stdout="invalid"),
        SimpleNamespace(returncode=0, stdout="done"),
    ]
    monkeypatch.setattr("paperretex.preflight.shutil.which", lambda x: "/fake/codex")

    def run(command, **kwargs):
        calls.append(command)
        reply = replies[len(calls) - 1]
        body = json.dumps(valid_response([1])) if reply.stdout == "done" else "{}"
        Path(command[command.index("--output-last-message") + 1]).write_text(body)
        return reply

    monkeypatch.setattr("paperretex.preflight.subprocess.run", run)
    cache = tmp_path / "cache"
    for _ in range(2):
        with pytest.raises(RuntimeError, match="stopped before OCR"):
            assess_layout(pdf, cache_root=cache)
    plan = assess_layout(pdf, cache_root=cache)
    assert not plan["cache_hit"] and plan["review"]["family"] == "single_column"
    assert assess_layout(pdf, cache_root=cache)["cache_hit"]
    assert len(calls) == 3
    ledger = json.loads(next(cache.glob("*/call.json")).read_text())
    assert ledger["status"] == "completed" and ledger["attempts"] == 3


def test_interrupted_or_stale_call_retries_but_live_call_blocks(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf, (1,))
    cache = tmp_path / "cache"
    calls = []
    monkeypatch.setattr("paperretex.preflight.shutil.which", lambda x: "/fake/codex")

    def cancelled(command, **kwargs):
        calls.append(command)
        raise KeyboardInterrupt

    monkeypatch.setattr("paperretex.preflight.subprocess.run", cancelled)
    with pytest.raises(KeyboardInterrupt):
        assess_layout(pdf, cache_root=cache)
    ledger_path = next(cache.glob("*/call.json"))
    assert json.loads(ledger_path.read_text())["status"] == "interrupted"

    # A call that started moments ago may still be running in another process.
    live = {"status": "started", "started": time.time(), "timeout": 300}
    ledger_path.write_text(json.dumps(live))
    with pytest.raises(RuntimeError, match="may still be running"):
        assess_layout(pdf, cache_root=cache)
    assert len(calls) == 1

    def run(command, **kwargs):
        calls.append(command)
        Path(command[command.index("--output-last-message") + 1]).write_text(
            json.dumps(valid_response([1]))
        )
        return SimpleNamespace(returncode=0, stdout="done")

    monkeypatch.setattr("paperretex.preflight.subprocess.run", run)
    assert assess_layout(pdf, cache_root=cache, retry=True)["review"]
    assert len(calls) == 2

    # A process killed mid-call long ago leaves a stale "started" ledger.
    ledger_path.unlink()
    (ledger_path.parent / "result.json").unlink()
    stale = {"status": "started", "started": time.time() - 3600, "timeout": 300}
    ledger_path.write_text(json.dumps(stale))
    assert assess_layout(pdf, cache_root=cache)["review"]
    assert len(calls) == 3


def test_column_order_and_spanning_bands():
    plan = {
        "columns": 2,
        "geometry": {"paper_width": 600, "left": 45, "right": 45, "column_gap": 20},
        "pages": [{"page": 1, "columns": 2, "confidence": 0.95}],
    }

    def block(id, bb):
        return {"id": id, "page": 1, "type": "paragraph", "bbox": bb, "text": id}

    items = [
        block("right-top", [530, 150, 920, 250]),
        block("left-bottom", [70, 550, 470, 650]),
        block("title", [70, 50, 930, 100]),
        block("left-top", [70, 150, 470, 250]),
        block("figure", [70, 300, 930, 500]),
        block("right-bottom", [530, 550, 920, 650]),
    ]
    result, warnings = prepare_blocks(items, plan)
    assert [b["id"] for b in result] == [
        "title",
        "left-top",
        "right-top",
        "figure",
        "left-bottom",
        "right-bottom",
    ]
    assert result[3]["wide"] and not result[1]["wide"] and not warnings


def test_tex_keeps_semantic_structure_on_column_change(tmp_path):
    blocks = [
        {
            "id": "h",
            "page": 1,
            "type": "heading",
            "text": "1 Method",
            "layout_columns": 1,
        },
        {"id": "e", "page": 2, "type": "equation", "latex": "x=1", "layout_columns": 2},
    ]
    emit_document(blocks, tmp_path / "main.tex")
    source = (tmp_path / "main.tex").read_text()
    assert r"\section{Method}" in source and r"\begin{equation*}" in source
    assert "\\twocolumn\n" in source


def test_convert_preflight_runs_before_recognizer(tmp_path, monkeypatch):
    from paperretex.cli import main

    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf, (2,))
    events = []

    def assess(*a, **kwargs):
        events.append("preflight")
        return assess_layout(pdf, reviewer="local")

    def recognize(*a, **kwargs):
        events.append("ocr")
        return [{"id": "h", "page": 1, "type": "heading", "text": "1 Method"}], {
            "backend": "fixture"
        }

    monkeypatch.setattr("paperretex.preflight.assess_layout", assess)
    monkeypatch.setattr("paperretex.ocr.recognize", recognize)
    assert (
        main(["convert", "--mode", "semantic", str(pdf), "-o", str(tmp_path / "out")])
        == 0
    )
    assert events == ["preflight", "ocr"]
    assert (tmp_path / "out/layout-plan.json").exists()


def test_uncertain_visual_assessment_stops_before_ocr(tmp_path, monkeypatch):
    from paperretex.cli import main

    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf, (2,))
    plan = assess_layout(pdf, reviewer="local")
    plan["review"] = {"confidence": 0.4, "family": "unknown"}
    monkeypatch.setattr("paperretex.preflight.assess_layout", lambda *a, **kw: plan)

    def forbidden(*a, **kw):
        raise AssertionError("OCR must not run before layout is confirmed")

    monkeypatch.setattr("paperretex.ocr.recognize", forbidden)
    assert (
        main(["convert", "--mode", "semantic", str(pdf), "-o", str(tmp_path / "out")])
        == 2
    )
    assert not (tmp_path / "out").exists()


def test_figure_page_metric_flags_float_drift(tmp_path):
    from paperretex.quality import figure_placement
    from PIL import Image

    (tmp_path / "assets").mkdir()
    asset = tmp_path / "assets/figure.png"
    Image.new("RGB", (30, 20), (40, 100, 200)).save(asset)
    with fitz.open() as doc:
        doc.new_page()
        p = doc.new_page()
        p.insert_image(fitz.Rect(20, 20, 80, 60), filename=str(asset))
        doc.save(tmp_path / "main.pdf")
    blocks = [
        {"id": "figure", "page": 1, "type": "image_crop", "asset": "assets/figure.png"}
    ]
    result = figure_placement(tmp_path, blocks)
    assert result["status"] == "page_alignment_failed" and result["moved"] == 1
    assert result["figures"][0]["output_pages"] == [2]
