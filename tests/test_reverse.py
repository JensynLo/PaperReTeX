import json
import fitz
from paperretex.cli import main
from paperretex.latex import emit_document
from paperretex.tables import table_tex


def test_semantic_never_recovers_source(tmp_path, monkeypatch):
    import paperretex.ocr
    import paperretex.cli

    def forbidden(*a, **kw):
        raise AssertionError("Original source must never be used")

    monkeypatch.setattr(paperretex.cli, "recover", forbidden)

    def recognize(pdf, out, pages, executable):
        return [
            {
                "id": "p1",
                "page": 1,
                "type": "heading",
                "text": "1 Introduction",
                "level": 1,
            },
            {"id": "p2", "page": 1, "type": "equation", "latex": r"x=\frac{1}{2}"},
        ], {"backend": "fixture_ocr", "source_lookup": False}

    monkeypatch.setattr(paperretex.ocr, "recognize", recognize)
    pdf = tmp_path / "unknown.pdf"
    with fitz.open() as d:
        p = d.new_page()
        p.insert_text((50, 60), "arXiv:2509.24986v1")
        d.save(pdf)
    out = tmp_path / "result"
    assert (
        main([
            "convert",
            "--mode",
            "semantic",
            str(pdf),
            "-o",
            str(out),
            "--layout-review",
            "local",
        ])
        == 0
    )
    tex = (out / "main.tex").read_text()
    assert r"\section{Introduction}" in tex
    assert r"\begin{equation*}" in tex
    assert r"\put(" not in tex
    assert json.loads((out / "report.json").read_text())["source_lookup"] is False


def test_tables_are_editable_and_escaped():
    result = table_tex(
        "<table><tr><td>method</td><td>30% &amp; 2</td></tr><tr><td>A</td><td>$x_1$</td></tr></table>"
    )
    assert r"\begin{tabular}" in result and r"30\% \& 2" in result and "$x_1$" in result
    assert r"\multicolumn{2}{l}{merged}" in table_tex(
        '<tr><td colspan="2">merged</td></tr>'
    )


def test_numbered_equation_and_normal_heading(tmp_path):
    out = tmp_path / "main.tex"
    emit_document(
        [
            {"id": "a", "page": 1, "type": "heading", "text": "2.1 Our Method"},
            {"id": "b", "page": 1, "type": "equation", "latex": r"E=mc^2 \tag{3}"},
        ],
        out,
    )
    text = out.read_text()
    assert r"\subsection{Our Method}" in text
    assert r"\begin{equation}" in text and r"\tag{3}" in text


def test_structured_output_compiles(tmp_path):
    import shutil
    import pytest
    from paperretex.cli import compile_project

    if not shutil.which("xelatex"):
        pytest.skip("XeLaTeX required")
    blocks = [
        {"id": "t", "page": 1, "type": "heading", "text": "A Reconstructed Paper"},
        {"id": "h", "page": 1, "type": "heading", "text": "1 Method"},
        {
            "id": "p",
            "page": 1,
            "type": "paragraph",
            "text": "We estimate $x_1$ using a fraction.",
        },
        {
            "id": "e",
            "page": 1,
            "type": "equation",
            "latex": r"x_1=\frac{a^2}{b}+\sum_{i=1}^{n} i\tag{1}",
        },
        {
            "id": "tb",
            "page": 1,
            "type": "table",
            "latex": table_tex(
                "<tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr>"
            ),
            "caption": "Table 1: Values.",
        },
    ]
    emit_document(blocks, tmp_path / "main.tex")
    assert compile_project(tmp_path)["status"] == "passed"
    with fitz.open(tmp_path / "main.pdf") as d:
        text = "".join(p.get_text() for p in d)
        assert "Method" in text and "Values" in text and "estimate" in text


def test_retry_preserves_two_round_ledger(tmp_path, monkeypatch):
    from paperretex.assist import run_round
    from types import SimpleNamespace
    import pytest

    ledger = [{"round": 1, "status": "failed"}, {"round": 2, "status": "completed"}]
    (tmp_path / "calls.json").write_text(json.dumps(ledger))
    (tmp_path / "round-1.log").write_text(
        "Error: failed to initialize in-process app-server client: Operation not permitted"
    )

    def fake(command, **kwargs):
        Path = __import__("pathlib").Path
        Path(command[command.index("--output-last-message") + 1]).write_text("{}")
        return SimpleNamespace(returncode=0, stdout="reviewed")

    monkeypatch.setattr("paperretex.assist.subprocess.run", fake)
    assert (
        run_round(tmp_path, "Review PDF-only output", [], retry_failed_startup=True)[
            "status"
        ]
        == "completed"
    )
    saved = json.loads((tmp_path / "calls.json").read_text())
    assert (
        len(saved) == 2
        and saved[0]["status"] == "failed"
        and saved[0]["retry"]["round"] == 1
    )
    assert (tmp_path / "round-1.log").read_text().startswith("Error:")
    with pytest.raises(ValueError):
        run_round(tmp_path, "no third round", [])
    with pytest.raises(ValueError):
        run_round(tmp_path, "no repeated retry", [], retry_failed_startup=True)


def test_literal_percent_in_math_is_not_comment():
    import pytest
    from paperretex.latex import safe_math

    assert safe_math(r"50\%") == r"50\%"
    with pytest.raises(ValueError):
        safe_math("50% hidden")
    with pytest.raises(ValueError):
        safe_math(r"50\\% hidden")


def test_common_relations_are_allowed():
    from paperretex.latex import safe_math

    for value in (r"N\ll M", r"a\gg b", r"x\leqslant y", r"A\subsetneq B", r"p\prec q"):
        assert safe_math(value) == value


def test_failed_compile_does_not_leave_stale_pdf(tmp_path):
    import shutil
    import pytest
    from paperretex.cli import compile_project

    if not shutil.which("xelatex"):
        pytest.skip("XeLaTeX required")
    (tmp_path / "main.pdf").write_bytes(b"stale output")
    (tmp_path / "main.tex").write_text(
        r"\documentclass{article}\begin{document}\unknowncommand\end{document}"
    )
    assert compile_project(tmp_path)["status"] == "failed"
    assert not (tmp_path / "main.pdf").exists()


def test_grouped_equations_keep_distinct_pdf_numbers(tmp_path):
    from paperretex.enrich import enrich

    pdf = tmp_path / "two-equations.pdf"
    with fitz.open() as d:
        p = d.new_page(width=600, height=800)
        p.insert_text((150, 100), "x=1")
        p.insert_text((300, 100), "(1)")
        p.insert_text((150, 130), "y=2")
        p.insert_text((300, 130), "(2)")
        d.save(pdf)
    blocks = [
        {
            "id": "eq",
            "page": 1,
            "type": "equation",
            "bbox": [200, 100, 450, 175],
            "latex": r"\begin{array}{r}{x=1}\\{y=2}\end{array}",
        }
    ]
    enrich(blocks, pdf)
    assert [r["number"] for r in blocks[0]["equation_rows"]] == ["1", "2"]
    emit_document(blocks, tmp_path / "main.tex")
    tex = (tmp_path / "main.tex").read_text()
    assert tex.count(r"\begin{equation}") == 2 and r"\tag{2}" in tex
