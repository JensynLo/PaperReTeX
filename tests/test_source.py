import io
import json
import tarfile
import fitz
import pytest
from paperretex.source import safe_extract, entrypoints, source_structure, detect_id
from paperretex.cli import main


def make_archive(name, content):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as t:
        member = tarfile.TarInfo(name)
        member.size = len(content)
        t.addfile(member, io.BytesIO(content))
    return stream.getvalue()


def test_source_archive_structure(tmp_path):
    data = make_archive(
        "paper.tex",
        rb"\documentclass{article}\begin{document}\section{Hello}\begin{equation}x=1\end{equation}\end{document}",
    )
    safe_extract(data, tmp_path)
    entries, engine, _ = entrypoints(tmp_path)
    assert entries == ["paper.tex"] and engine == "pdflatex"
    assert source_structure(tmp_path)["equation"] == 1
    assert source_structure(tmp_path)["section"] == 1


@pytest.mark.parametrize("name", ["../escaped.tex", "/absolute.tex"])
def test_source_archive_rejects_unsafe_paths(tmp_path, name):
    with pytest.raises(ValueError, match="Unsafe"):
        safe_extract(make_archive(name, b"x"), tmp_path)


def test_source_is_explicit_and_not_positioned(tmp_path):
    pdf = tmp_path / "paper.pdf"
    with fitz.open() as d:
        page = d.new_page()
        page.insert_text((50, 50), "arXiv:2509.24986v1")
        d.save(pdf)
    assert detect_id(pdf) == "2509.24986v1"
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(
        make_archive(
            "main.tex",
            rb"\documentclass{article}\begin{document}\section{Hello}\begin{equation}x=1\end{equation}\end{document}",
        )
    )
    out = tmp_path / "project"
    assert (
        main([
            "convert",
            "--mode",
            "source",
            str(pdf),
            "--source-archive",
            str(archive),
            "-o",
            str(out),
        ])
        == 0
    )
    tex = (out / "main.tex").read_text()
    assert r"\section{Hello}" in tex and r"\begin{equation}" in tex
    assert r"\put(" not in tex
    assert json.loads((out / "report.json").read_text())["backend"] == "arxiv_source"


def test_compile_semantic_references_to_convergence(tmp_path):
    import shutil
    from paperretex.source import compile_source

    if not shutil.which("pdflatex"):
        pytest.skip("pdfLaTeX required")
    (tmp_path / "paper.tex").write_text(r"""\documentclass{article}
\usepackage{amsmath}
\begin{document}
\section{Method}See Equation~\ref{eq:example}.
\begin{equation}\label{eq:example}x=1\end{equation}
\end{document}""")
    report = compile_source(
        tmp_path, {"engine": "pdflatex", "entrypoints": ["paper.tex"]}
    )
    assert report["status"] == "passed"
    with fitz.open(tmp_path / "main.pdf") as doc:
        text = "".join(p.get_text() for p in doc)
        assert "Equation 1" in text and "??" not in text
