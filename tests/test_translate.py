from paperretex.translate import prose_spans, apply_translations, bilingual


def test_only_prose_changes():
    source = r"""\documentclass{article}
\newcommand{\hidden}{Do not translate}
\begin{document}
% keep comment
\vspace{2pt}{\bfseries Paper title}\par
\section{Our method}
We use $x_i=2$ and \emph{an example}.\label{sec:example}
\begin{equation}\text{fixed math}+x=1\end{equation}
\includegraphics[width=\linewidth]{assets/English-name.png}
\begin{tabular}{lc}Method & Value\\\end{tabular}
\end{document}"""
    spans = prose_spans(source)
    texts = [source[a:b] for a, b in spans]
    assert "Paper title" in texts and "Our method" in texts and "Method" in texts
    assert not any(
        "fixed math" in t
        or "English-name" in t
        or "sec:example" in t
        or "Do not translate" in t
        for t in texts
    )
    result = apply_translations(source, spans, ["译文 % & \\input{evil}"] * len(spans))
    assert r"\begin{equation}\text{fixed math}+x=1\end{equation}" in result
    assert r"\includegraphics[width=\linewidth]{assets/English-name.png}" in result
    assert r"\textbackslash{}input\{evil\}" in result
    assert r"\section{" in result


def test_bilingual_sync_and_float_conversion():
    source = r"""\documentclass[10pt,twocolumn]{article}
\usepackage[a4paper]{geometry}
\begin{document}
% p001-b0001 / heading
\twocolumn[{\textbf{Title}}]
% p001-b0002 / paragraph
First paragraph.
% p001-b0003 / image_crop
\begin{figure*}[tbp]\includegraphics{assets/image.png}\caption*{Caption}\end{figure*}
\end{document}"""
    output = bilingual(source, source.replace("First paragraph.", "第一段。"))
    assert output.count(r"\switchcolumn*") == 3
    assert r"\begin{figure" not in output and r"\twocolumn" not in output
    assert r"\captionof*{figure}{Caption}" in output
    assert "第一段。" in output and "First paragraph." in output
    numbered = source.replace("First paragraph.", r"\section{Method}")
    assert bilingual(numbered, numbered).count(r"\section{Method}") == 2


def test_resume_uses_validated_cache_and_preserves_input(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from paperretex.translate import translate_project

    source = tmp_path / "source"
    source.mkdir()
    tex = source / "main.tex"
    original = r"\documentclass{article}\begin{document}\section{Method}Text $x=1$.\end{document}"
    tex.write_text(original)
    calls = []

    def run(command, input, **kwargs):
        rows = json.loads(input.split("\n", 1)[1])
        calls.append(rows)
        path = command[command.index("--output-last-message") + 1]
        from pathlib import Path

        Path(path).write_text(
            json.dumps({
                "translations": [{"id": r["id"], "text": "译文"} for r in rows]
            })
        )
        return SimpleNamespace(returncode=0, stdout="test")

    def compile(project):
        (project / "main.pdf").write_bytes(b"fake PDF fixture")
        return {"status": "passed"}

    monkeypatch.setattr("paperretex.translate.subprocess.run", run)
    monkeypatch.setattr("paperretex.cli.compile_project", compile)
    out = tmp_path / "out"
    report = translate_project(tex, out)
    assert report["new_codex_calls"] == 1
    report = translate_project(tex, out)
    assert report["new_codex_calls"] == 0 and len(calls) == 1
    assert tex.read_text() == original
    assert (out / "translated.pdf").exists() and (out / "bilingual.pdf").exists()
    assert "$x=1$" in (out / "main.tex").read_text()


def test_paragraph_split_keeps_environments_and_groups():
    from paperretex.translate import split_paragraphs

    parts = split_paragraphs(
        "First.\n\n"
        + r"\begin{equation}"
        + "\nx=1\n\n"
        + r"\end{equation}"
        + "\n\nLast."
    )
    assert len(parts) == 3
    assert r"\begin{equation}" in parts[1] and r"\end{equation}" in parts[1]
