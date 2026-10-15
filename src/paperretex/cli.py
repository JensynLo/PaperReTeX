import argparse
import hashlib
import json
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from .latex import emit_document
from .native import extract
from .importer import import_content
from .layout import reconstruct
from .source import recover, compile_source


def parse_pages(value):
    pages = set()
    for item in value.split(","):
        if "-" in item:
            a, b = map(int, item.split("-", 1))
            if a < 1 or b < a:
                raise argparse.ArgumentTypeError(
                    "Pages must be ascending, starting at 1."
                )
            pages.update(range(a - 1, b))
        else:
            n = int(item)
            if n < 1:
                raise argparse.ArgumentTypeError("Pages start at 1.")
            pages.add(n - 1)
    return sorted(pages)


def compile_project(out, timeout=180):
    engine = shutil.which("xelatex")
    if not engine:
        return {
            "status": "unavailable",
            "message": "Install XeLaTeX (MacTeX or TeX Live).",
        }
    try:
        # Never let a failed rebuild expose a stale PDF as the current result.
        (out / "main.pdf").unlink(missing_ok=True)
        result = subprocess.run(
            [
                engine,
                "-no-shell-escape",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "main.tex",
            ],
            cwd=out,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            text=True,
        )
        (out / "compile.log").write_text(result.stdout, encoding="utf-8")
        missing = [
            line for line in result.stdout.splitlines() if "Missing character:" in line
        ]
        return {
            "status": ("passed" if not missing else "passed_with_missing_glyphs")
            if result.returncode == 0 and (out / "main.pdf").is_file()
            else "failed",
            "returncode": result.returncode,
            "missing_glyphs": missing,
            "layout_warnings": [
                line
                for line in result.stdout.splitlines()
                if "Overfull" in line or "Float too large" in line
            ],
            "log": "compile.log",
        }
    except subprocess.TimeoutExpired:
        return {"status": "failed", "message": f"XeLaTeX exceeded {timeout}s."}


def convert(args):
    if args.evaluate and not args.compile:
        raise ValueError("--evaluate requires --compile.")
    if args.mode == "hybrid":
        from .hybrid import convert_hybrid

        return convert_hybrid(args)
    if args.source_archive and args.mode != "source":
        raise ValueError(
            "--source-archive requires explicit --mode source; reverse conversion never reads original TeX."
        )
    pdf = Path(args.pdf).resolve()
    if not pdf.is_file():
        raise ValueError("Input PDF does not exist: " + str(pdf))
    out = Path(args.output).resolve() if args.output else Path("output") / pdf.stem
    out = out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(
            "Output directory is not empty; choose a new --output to protect edits."
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=".paperretex-", dir=out.parent) as tmp:
        stage = Path(tmp)
        (stage / "assets").mkdir()
        source_mode = args.mode == "source" and not args.content_list
        plan = None
        if args.mode == "semantic":
            from .preflight import assess_layout

            reviewer = args.llm_provider if args.layout_review != "local" else "local"
            plan = assess_layout(
                pdf,
                args.pages,
                reviewer,
                args.layout_profile,
                codex=args.llm_command or args.codex_command or args.llm_provider,
                timeout=args.layout_timeout,
                provider=args.llm_provider,
                codex_effort=args.codex_effort,
            )
            (stage / "layout-plan.json").write_text(
                json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                "Layout preflight: "
                + plan["family"]
                + "; reviewer="
                + plan["reviewer"]
                + (" (cached)" if plan["cache_hit"] else ""),
                file=sys.stderr,
            )
            if (
                plan["review"]
                and not plan["manual_override"]
                and (
                    plan["review"]["confidence"] < 0.75
                    or plan["review"]["family"] == "unknown"
                )
            ):
                raise ValueError(
                    "Uncertain layout: inspect the cached assessment, then choose --layout-profile single_column or two_column explicitly."
                )
            for warning in plan["warnings"]:
                print("Layout: " + warning, file=sys.stderr)
        if source_mode:
            if args.pages:
                raise ValueError(
                    "Source recovery uses the complete document; --pages is for layout/reflow modes."
                )
            report = recover(pdf, stage, args.source_archive)
            blocks = []
        elif args.mode == "semantic" and not args.content_list:
            from .ocr import recognize

            blocks, report = recognize(pdf, stage, args.pages, args.ocr_command)
        elif args.mode == "layout" and not args.content_list:
            report = reconstruct(pdf, stage, args.pages)
            blocks = []
        elif args.content_list:
            if args.pages:
                raise ValueError("--pages cannot be combined with --content-list.")
            blocks, report = import_content(args.content_list, stage)
        else:
            blocks, report = extract(pdf, stage, args.pages)
        if blocks:
            from .geometry import infer_layout

            report["layout"] = plan["geometry"] if plan else infer_layout(pdf)
            if plan:
                from .profiles import prepare_blocks

                blocks, ordering_warnings = prepare_blocks(blocks, plan)
                report["layout_preflight"] = {
                    k: v for k, v in plan.items() if k not in ("geometry", "pages")
                }
                report["layout_preflight"]["ordering_warnings"] = ordering_warnings
            if report.get("backend") in ("mineru_pipeline", "mineru_content_list"):
                from .enrich import enrich

                enrich(blocks, pdf)
            emit_document(blocks, stage / "main.tex", report["layout"])
        elif args.mode != "layout" and not source_mode:
            raise ValueError("No content was extracted.")
        recovered = report.get("embedded_formulas", [])
        if recovered:
            emit_document(
                [
                    dict(type="equation", id=f"equation-{i + 1}", **b)
                    for i, b in enumerate(recovered)
                ],
                stage / "recovered-equations.tex",
            )
        (stage / "blocks.json").write_text(
            json.dumps(blocks, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report.update(
            input_name=pdf.name,
            input_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
            counts=report.get("counts", dict(Counter(b["type"] for b in blocks))),
            review_blocks=report.get(
                "review_blocks", [b["id"] for b in blocks if b.get("review")]
            ),
            duration_seconds=round(time.monotonic() - start, 2),
            compile=(
                compile_source(stage, report) if source_mode else compile_project(stage)
            )
            if args.compile
            else {"status": "not_requested"},
        )
        (stage / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        build = (
            "\n".join(
                report["engine"]
                + " -no-shell-escape -interaction=nonstopmode -halt-on-error "
                + entry
                for entry in report["entrypoints"]
            )
            if source_mode
            else "xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error main.tex"
        )
        (stage / "BUILD.txt").write_text(
            build
            + "\n\nRead report.json for source provenance and compilation details.\n",
            encoding="utf-8",
        )
        if out.exists():
            out.rmdir()
        shutil.move(str(stage), str(out))
    if args.evaluate:
        from .quality import assess

        assess(pdf, out)
    print(
        json.dumps(
            {
                "output": str(out),
                "counts": report["counts"],
                "compile": report["compile"]["status"],
                "needs_review": len(report["review_blocks"]),
            },
            ensure_ascii=False,
        )
    )
    return 1 if args.compile and report["compile"]["status"] != "passed" else 0


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def install_interrupts():
    """Make SIGINT and SIGTERM unwind normally so LLM process trees are stopped.

    Shells start background jobs with SIGINT ignored, and Python keeps an
    inherited "ignore"; without this, stopping a web job would orphan Codex.
    """
    signal.signal(signal.SIGINT, signal.default_int_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _interrupt)


def main(argv=None):
    from .llm_cli import CODEX_EFFORTS

    install_interrupts()
    parser = argparse.ArgumentParser(
        description="Academic PDF → editable LaTeX draft, assets, and an audit report."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser(
        "convert", help="Reverse a PDF into structured LaTeX using local OCR."
    )
    p.add_argument("pdf")
    p.add_argument(
        "--mode",
        choices=["hybrid", "semantic", "layout", "reflow", "source"],
        default="hybrid",
        help="hybrid (default): BabelDOC layout + reviewed formula OCR; semantic: MinerU full-page OCR; layout: positioned glyphs; reflow: native draft; source: explicit source download.",
    )
    p.add_argument("-o", "--output")
    p.add_argument(
        "--source-archive",
        help="Use a local source archive instead of downloading it (explicit --mode source only).",
    )
    p.add_argument(
        "--ocr-command", help="Path to a compatible MinerU 2.7 pipeline executable."
    )
    p.add_argument(
        "--pages", type=parse_pages, help="1-based page selection, e.g. 1-3,5"
    )
    p.add_argument(
        "--evaluate",
        action="store_true",
        help="After compilation, write content coverage and page comparison sheets.",
    )
    p.add_argument(
        "--compile",
        action="store_true",
        help="Compile the exported project with XeLaTeX.",
    )
    p.add_argument(
        "--content-list",
        help="Import a MinerU Content List V1 JSON instead of native extraction.",
    )
    p.add_argument(
        "--layout-review",
        choices=["codex", "local"],
        default="codex",
        help="Use the selected CLI provider for layout review, or local geometry only.",
    )
    p.add_argument(
        "--layout-profile",
        choices=["auto", "single_column", "two_column"],
        default="auto",
    )
    p.add_argument("--llm-provider", choices=["codex", "claude"], default="codex")
    p.add_argument(
        "--codex-effort",
        choices=CODEX_EFFORTS,
        help="Codex reasoning effort for layout and formula review; defaults to Codex configuration.",
    )
    p.add_argument(
        "--llm-command", help="CLI executable path; defaults to the provider name."
    )
    p.add_argument("--codex-command", help="Deprecated alias for --llm-command.")
    p.add_argument("--layout-timeout", type=int, default=300)
    p.add_argument(
        "--babeldoc-python", help="Python in an isolated BabelDOC 0.6.4 runtime."
    )
    p.add_argument(
        "--ocr-python",
        help="Python in an isolated MinerU 2.7.1 runtime (formula OCR only).",
    )
    p.add_argument(
        "--formula-batch-size",
        type=int,
        default=36,
        help="Expressions per visual review call (1–48).",
    )
    p.add_argument("--formula-timeout", type=int, default=600)
    p.add_argument(
        "--formula-review-rounds",
        type=int,
        choices=[1, 2],
        default=2,
        help="At most two rounds; the second reviews only uncertain targets with surrounding context.",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Resume the same hybrid output, reusing completed stages and reviews.",
    )
    p.add_argument(
        "--retry-formulas",
        action="store_true",
        help="Explicitly retry failed formula-review batches when resuming.",
    )
    i = sub.add_parser(
        "inspect", help="Assess PDF layout before OCR; no OCR installation required."
    )
    i.add_argument("pdf")
    i.add_argument(
        "-o", "--output", required=True, help="Write the layout plan as JSON"
    )
    i.add_argument("--pages", type=parse_pages)
    i.add_argument("--layout-review", choices=["codex", "local"], default="codex")
    i.add_argument(
        "--layout-profile",
        choices=["auto", "single_column", "two_column"],
        default="auto",
    )
    i.add_argument("--llm-provider", choices=["codex", "claude"], default="codex")
    i.add_argument(
        "--codex-effort",
        choices=CODEX_EFFORTS,
        help="Codex reasoning effort; defaults to Codex configuration.",
    )
    i.add_argument("--llm-command")
    i.add_argument("--codex-command")
    i.add_argument("--timeout", type=int, default=300)
    i.add_argument(
        "--retry",
        action="store_true",
        help="Explicitly retry a failed preflight call; completed calls remain cached.",
    )
    e = sub.add_parser(
        "evaluate", help="Compare original and compiled PDFs page by page."
    )
    e.add_argument("source")
    e.add_argument("rebuilt")
    e.add_argument("-o", "--output", required=True)
    e.add_argument("--dpi", type=int, default=144)
    t = sub.add_parser(
        "translate",
        help="Translate generated TeX via a supported CLI and compile translated/bilingual PDFs.",
    )
    t.add_argument("tex", help="Generated main.tex or its project directory")
    t.add_argument("-o", "--output", required=True)
    t.add_argument("--target", default="简体中文")
    t.add_argument("--batch-chars", type=int, default=18000)
    t.add_argument("--llm-provider", choices=["codex", "claude"], default="codex")
    t.add_argument(
        "--codex-effort",
        choices=CODEX_EFFORTS,
        help="Codex reasoning effort; defaults to Codex configuration.",
    )
    t.add_argument("--llm-command")
    t.add_argument("--codex-command")
    t.add_argument("--timeout", type=int, default=900)
    sub.add_parser("doctor", help="Check runtime and compiler availability.")
    w = sub.add_parser(
        "serve",
        help="Run the local web app: drop a PDF, download translated/bilingual PDFs.",
    )
    from .web import add_arguments

    add_arguments(w)
    sub.add_parser(
        "install-runtimes",
        help="Install isolated BabelDOC and formula OCR environments.",
    )
    args = parser.parse_args(argv)
    try:
        if getattr(args, "codex_effort", None) and args.llm_provider != "codex":
            raise ValueError("--codex-effort requires --llm-provider codex.")
        if args.command == "serve":
            from .web import serve

            serve(args.host, args.port, args.workdir, args.allow_host)
            return 0
        if args.command == "install-runtimes":
            from .runtime_install import install_runtimes

            print(json.dumps({"runtime_cache": str(install_runtimes())}))
            return 0
        if args.command == "inspect":
            from .preflight import assess_layout

            reviewer = args.llm_provider if args.layout_review != "local" else "local"
            plan = assess_layout(
                args.pdf,
                args.pages,
                reviewer,
                args.layout_profile,
                codex=args.llm_command or args.codex_command or args.llm_provider,
                timeout=args.timeout,
                retry=args.retry,
                provider=args.llm_provider,
                codex_effort=args.codex_effort,
            )
            dest = Path(args.output)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(
                json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "output": str(dest.resolve()),
                        "family": plan["family"],
                        "needs_review": plan["needs_review"],
                        "cache_hit": plan["cache_hit"],
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "translate":
            from .translate import translate_project

            report = translate_project(
                args.tex,
                args.output,
                args.target,
                args.batch_chars,
                args.llm_command or args.codex_command or args.llm_provider,
                args.timeout,
                provider=args.llm_provider,
                codex_effort=args.codex_effort,
            )
            print(json.dumps(report, ensure_ascii=False))
            return 0
        if args.command == "evaluate":
            from .evaluate import evaluate

            report = evaluate(args.source, args.rebuilt, args.output, args.dpi)
            print(
                json.dumps(
                    {k: v for k, v in report.items() if k != "pages"},
                    ensure_ascii=False,
                )
            )
            return 0 if report["passed"] else 1
        if args.command == "doctor":
            from .diagnostics import doctor

            print(json.dumps(doctor(), indent=2))
            return 0
        return convert(args)
    except (ValueError, OSError, RuntimeError) as exc:
        print("paperretex: " + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        # Child LLM process trees were stopped while unwinding.
        print("paperretex: interrupted", file=sys.stderr)
        return 130
