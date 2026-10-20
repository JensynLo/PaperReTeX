"""Local web app: drop a PDF, pick options, download translated/bilingual PDFs.

Jobs run the regular CLI (`convert`, then `translate`) as subprocesses, one at
a time, so a web job behaves exactly like the equivalent command line. Every
job lives in its own folder with a JSON state file and a log, so the list and
the results survive a restart; an interrupted job can be resumed.
"""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .config import cache_root
from .llm_cli import CODEX_EFFORTS

MAX_UPLOAD = 300 * 1024 * 1024
PAGE = Path(__file__).with_name("data") / "web.html"
# Mutating requests must carry this header. Browsers cannot add it to a
# cross-site form post without a CORS preflight, which this server never grants.
CSRF_HEADER = "X-PaperReTeX"
PAGES = re.compile(r"^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$")
STAGES = [
    "queued",
    "layout",
    "ocr",
    "review",
    "assemble",
    "translate",
    "done",
]
# Rough share of wall time per stage, for one progress bar across both commands.
WEIGHTS = {
    "layout": 0.15,
    "ocr": 0.1,
    "review": 0.45,
    "assemble": 0.05,
    "translate": 0.25,
}
OUTPUTS = {
    "bilingual.pdf": "translate/bilingual.pdf",
    "translated.pdf": "translate/translated.pdf",
    "rebuilt.pdf": "convert/main.pdf",
    "latex-project.zip": "latex-project.zip",
}
DEFAULTS = {
    "translate": True,
    "target": "简体中文",
    "llm_provider": "codex",
    "codex_effort": "",
    "pages": "",
    "layout_profile": "auto",
    "layout_review": "llm",
    "formula_batch_size": 36,
    "formula_review_rounds": 2,
    "formula_timeout": 600,
    "batch_chars": 18000,
    "translate_timeout": 900,
}


def clean_options(raw):
    """Whitelist every option; anything else is rejected, never passed through."""
    if not isinstance(raw, dict):
        raise ValueError("Options must be a JSON object.")
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ValueError("Unknown options: " + ", ".join(sorted(unknown)))
    o = dict(DEFAULTS, **raw)

    def integer(key, low, high):
        try:
            value = int(o[key])
        except (TypeError, ValueError):
            raise ValueError(key + " must be an integer.") from None
        if not low <= value <= high:
            raise ValueError("%s must be between %d and %d." % (key, low, high))
        return value

    if not isinstance(o["translate"], bool):
        raise ValueError("translate must be true or false.")
    if o["llm_provider"] not in ("codex", "claude"):
        raise ValueError("llm_provider must be codex or claude.")
    if o["codex_effort"] and (
        o["codex_effort"] not in CODEX_EFFORTS or o["llm_provider"] != "codex"
    ):
        raise ValueError("codex_effort needs a valid level and the Codex provider.")
    pages = str(o["pages"]).replace(" ", "")
    if pages and not PAGES.fullmatch(pages):
        raise ValueError("pages must look like 1-3,5.")
    if o["layout_profile"] not in ("auto", "single_column", "two_column"):
        raise ValueError("Invalid layout_profile.")
    if o["layout_review"] not in ("llm", "local"):
        raise ValueError("layout_review must be llm or local.")
    target = str(o["target"]).strip()
    if not target or len(target) > 40 or re.search(r"[\x00-\x1f\x7f]", target):
        raise ValueError("target must be a short language name.")
    return {
        "translate": o["translate"],
        "target": target,
        "llm_provider": o["llm_provider"],
        "codex_effort": o["codex_effort"] or "",
        "pages": pages,
        "layout_profile": o["layout_profile"],
        "layout_review": o["layout_review"],
        "formula_batch_size": integer("formula_batch_size", 1, 48),
        "formula_review_rounds": integer("formula_review_rounds", 1, 2),
        "formula_timeout": integer("formula_timeout", 60, 7200),
        "batch_chars": integer("batch_chars", 2000, 60000),
        "translate_timeout": integer("translate_timeout", 60, 7200),
    }


def convert_args(o, resume=False):
    args = [
        "convert",
        "input.pdf",
        "-o",
        "convert",
        "--compile",
        "--llm-provider",
        o["llm_provider"],
        "--layout-profile",
        o["layout_profile"],
        "--layout-review",
        "local" if o["layout_review"] == "local" else "codex",
        "--formula-batch-size",
        str(o["formula_batch_size"]),
        "--formula-review-rounds",
        str(o["formula_review_rounds"]),
        "--formula-timeout",
        str(o["formula_timeout"]),
    ]
    if o["pages"]:
        args += ["--pages", o["pages"]]
    if o["codex_effort"]:
        args += ["--codex-effort", o["codex_effort"]]
    if resume:
        args += ["--resume", "--retry-formulas"]
    return args


def translate_args(o):
    args = [
        "translate",
        "convert/main-flat.tex",
        "-o",
        "translate",
        "--target",
        o["target"],
        "--batch-chars",
        str(o["batch_chars"]),
        "--timeout",
        str(o["translate_timeout"]),
        "--llm-provider",
        o["llm_provider"],
    ]
    if o["codex_effort"]:
        args += ["--codex-effort", o["codex_effort"]]
    return args


def read_progress(line, job):
    """Map the CLI's stderr progress lines onto stages and a 0–1 fraction."""
    stage = None
    if line.startswith("Hybrid: "):
        name = line[8:].strip()
        if name == "babeldoc-layout" or name.startswith("region-format"):
            stage = "layout"
        elif name == "formula-ocr":
            stage = "ocr"
        elif name.endswith("formula-review"):
            stage = "review"
            job["review_round"] = 1
        elif name.endswith("formula-context-review"):
            stage = "review"
            job["review_round"] = 2
        elif name == "region-tex-and-assembly":
            stage = "assemble"
    match = re.search(r"batch (\d+)/(\d+)", line)
    if match and ("Formula review" in line or "Translating" in line):
        done, total = int(match[1]), int(match[2])
        if "completed" in line or "cached" in line or "Translating" in line:
            job["stage_fraction"] = (done - ("Translating" in line)) / total
        job["detail"] = line.strip()[:160]
    if stage and stage != job.get("stage"):
        job.update(stage=stage, stage_fraction=0, detail="")
    order = list(WEIGHTS)
    if job.get("stage") in order:
        index = order.index(job["stage"])
        total = sum(WEIGHTS.values()) if job["options"]["translate"] else 0.75
        base = sum(WEIGHTS[s] for s in order[:index])
        part = WEIGHTS[job["stage"]] * min(1, job.get("stage_fraction", 0))
        job["progress"] = round(min(0.99, (base + part) / total), 3)


class JobManager:
    def __init__(self, root, command=None, workers=1):
        self.root = Path(root)
        (self.root / "jobs").mkdir(parents=True, exist_ok=True)
        # The CLI module, run with this same interpreter unless a test overrides it.
        self.command = command or [sys.executable, "-m", "paperretex"]
        self.lock = threading.Lock()
        self.jobs = {}
        self.procs = {}
        self.queue = queue.Queue()
        for path in sorted((self.root / "jobs").glob("*/job.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if job.get("status") in ("queued", "running"):
                job.update(
                    status="interrupted", error="Server stopped during this job."
                )
                self._write(job)
            self.jobs[job["id"]] = job
        for _ in range(workers):
            threading.Thread(target=self._worker, daemon=True).start()

    def folder(self, job_id):
        return self.root / "jobs" / job_id

    def _write(self, job):
        path = self.folder(job["id"]) / "job.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def update(self, job, **values):
        with self.lock:
            job.update(values)
            self._write(job)

    def create(self, name, data, options):
        if not data.startswith(b"%PDF-"):
            raise ValueError("The upload is not a PDF.")
        options = clean_options(options)
        job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        folder = self.folder(job_id)
        folder.mkdir()
        (folder / "input.pdf").write_bytes(data)
        stem = Path(name).name or "paper.pdf"
        job = {
            "id": job_id,
            "name": re.sub(r"[\x00-\x1f\x7f]", "", stem)[:200],
            "size": len(data),
            "options": options,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "created": time.time(),
            "outputs": [],
            "warnings": [],
        }
        with self.lock:
            self.jobs[job_id] = job
            self._write(job)
        self.queue.put((job_id, False))
        return job

    def resume(self, job_id):
        job = self.get(job_id)
        if job["status"] not in ("failed", "interrupted", "cancelled"):
            raise ValueError("Only stopped jobs can be resumed.")
        self.update(job, status="queued", stage="queued", error=None, progress=0)
        self.queue.put((job_id, True))
        return job

    def cancel(self, job_id):
        job = self.get(job_id)
        if job["status"] == "queued":
            self.update(job, status="cancelled", error="Cancelled before start.")
        elif job["status"] == "running":
            self.update(job, cancel_requested=True)
            proc = self.procs.get(job_id)
            if proc:
                stop(proc)
        return job

    def delete(self, job_id):
        job = self.get(job_id)
        if job["status"] in ("queued", "running"):
            raise ValueError("Cancel the job before deleting it.")
        with self.lock:
            del self.jobs[job_id]
        shutil.rmtree(self.folder(job_id), ignore_errors=True)

    def get(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError(job_id)
            return self.jobs[job_id]

    def listing(self):
        with self.lock:
            return sorted(
                (dict(j) for j in self.jobs.values()),
                key=lambda j: j["created"],
                reverse=True,
            )

    def log_tail(self, job_id, lines=60):
        path = self.folder(job_id) / "run.log"
        if not path.exists():
            return ""
        with path.open("rb") as f:
            f.seek(max(0, path.stat().st_size - 24000))
            text = f.read().decode("utf-8", "replace")
        return "\n".join(text.splitlines()[-lines:])

    def _worker(self):
        while True:
            job_id, resume = self.queue.get()
            try:
                job = self.get(job_id)
            except KeyError:
                continue
            if job["status"] != "queued":
                continue
            try:
                self._run(job, resume)
            except Exception as exc:  # keep the worker alive for the next job
                self.update(job, status="failed", error=str(exc), finished=time.time())

    def _step(self, job, args, log):
        log.write("\n$ paperretex " + " ".join(args) + "\n")
        log.flush()
        proc = subprocess.Popen(
            self.command + args,
            cwd=self.folder(job["id"]),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=(os.name == "posix"),
            env=child_env(),
        )
        self.procs[job["id"]] = proc
        try:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                with self.lock:
                    read_progress(line, job)
            return proc.wait()
        finally:
            self.procs.pop(job["id"], None)
            self.update(job)

    def _run(self, job, resume):
        folder = self.folder(job["id"])
        options = job["options"]
        self.update(
            job,
            status="running",
            stage="layout",
            started=time.time(),
            finished=None,
            cancel_requested=False,
            warnings=[],
            outputs=[],
        )
        with (folder / "run.log").open("a", encoding="utf-8") as log:
            converted = folder / "convert"
            again = resume and converted.exists() and any(converted.iterdir())
            if converted.exists() and not again:
                shutil.rmtree(converted)
            code = self._step(job, convert_args(options, again), log)
            if job.get("cancel_requested"):
                return self.update(
                    job, status="cancelled", error="Cancelled.", finished=time.time()
                )
            report = read_json(converted / "report.json")
            built = (converted / "main.pdf").is_file() and (
                converted / "main-flat.tex"
            ).is_file()
            if code not in (0, 1) or not built:
                compiled = (report.get("compile") or {}).get("status")
                error = {
                    "unavailable": "XeLaTeX was not found on PATH. Install TeX Live "
                    "or MacTeX, restart the web app from that shell, then Resume.",
                    "failed": "XeLaTeX could not compile the generated project; "
                    "see convert/compile.log in the job folder.",
                }.get(compiled) or last_error(self.log_tail(job["id"]))
                return self.update(
                    job, status="failed", error=error, finished=time.time()
                )
            unresolved = len(report.get("unresolved_formula_ids", []))
            if unresolved:
                job["warnings"].append(
                    "%d formula(s) are still unresolved and show as [?]." % unresolved
                )
            package(converted, folder / "latex-project.zip")
            if options["translate"]:
                self.update(job, stage="translate", stage_fraction=0)
                code = self._step(job, translate_args(options), log)
                if job.get("cancel_requested"):
                    return self.update(
                        job,
                        status="cancelled",
                        error="Cancelled.",
                        finished=time.time(),
                    )
                if code:
                    return self.update(
                        job,
                        status="failed",
                        error=last_error(self.log_tail(job["id"])),
                        outputs=available(folder),
                        finished=time.time(),
                    )
                translation = folder / "translate" / "translation-report.json"
                kept = (
                    len(json.loads(translation.read_text()).get("untranslated", []))
                    if translation.exists()
                    else 0
                )
                if kept:
                    job["warnings"].append(
                        "%d fragment(s) were left untranslated; see "
                        "translate/translation-report.json." % kept
                    )
        self.update(
            job,
            status="done",
            stage="done",
            progress=1,
            detail="",
            outputs=available(folder),
            summary=summary(report),
            finished=time.time(),
        )


def child_env():
    """Let `python -m paperretex` work from a source checkout (python app.py)."""
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    package_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (package_root, env.get("PYTHONPATH")) if p
    )
    return env


def stop(proc):
    """Ask the CLI to stop; it unwinds and stops the LLM process trees it started."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=15)
    except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, OSError):
            pass


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def last_error(log):
    for line in reversed(log.splitlines()):
        if line.startswith("paperretex:") or "Error" in line:
            return line.strip()[:500]
    return "The command failed; see the log."


def available(folder):
    return [name for name, rel in OUTPUTS.items() if (folder / rel).is_file()]


def summary(report):
    review = report.get("formula_review") or {}
    return {
        "pages": len(report.get("pages") or []),
        "formulas_reviewed": review.get("reviewed"),
        "formulas_uncertain": review.get("uncertain"),
        "llm_calls": review.get("new_calls"),
        "seconds": report.get("duration_seconds"),
    }


def package(converted, dest):
    """The editable project only: sources, assets and the audit reports."""
    keep = ("main.tex", "main-flat.tex", "BUILD.txt", "report.json", "formulas.json")
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for name in keep:
            if (converted / name).is_file():
                z.write(converted / name, "latex-project/" + name)
        for sub in ("regions", "assets"):
            for path in sorted((converted / sub).rglob("*")):
                if path.is_file():
                    z.write(
                        path, "latex-project/" + path.relative_to(converted).as_posix()
                    )


def environment():
    from .diagnostics import doctor

    info = doctor()
    return {
        "codex": bool(info.get("codex")),
        "claude": bool(info.get("claude")),
        "xelatex": bool(info.get("xelatex")),
        "runtimes": bool(info.get("babeldoc_python") and info.get("ocr_python")),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "PaperReTeX"
    manager = None
    allowed_hosts = set()

    def log_message(self, fmt, *args):  # keep the console for job progress
        pass

    def send_json(self, value, status=HTTPStatus.OK):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def fail(self, status, message):
        self.send_json({"error": message}, status)

    def guard(self, mutating=False):
        host = (self.headers.get("Host") or "").lower()
        if host not in self.allowed_hosts:
            self.fail(HTTPStatus.FORBIDDEN, "Unexpected Host header.")
            return False
        if mutating and self.headers.get(CSRF_HEADER) != "1":
            self.fail(HTTPStatus.FORBIDDEN, "Missing request header.")
            return False
        return True

    def route(self):
        parts = [unquote(p) for p in urlparse(self.path).path.split("/") if p]
        return parts

    def do_GET(self):
        if not self.guard():
            return
        parts = self.route()
        if not parts:
            page = PAGE.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(page)
            return
        if parts == ["api", "config"]:
            return self.send_json({
                "defaults": DEFAULTS,
                "efforts": list(CODEX_EFFORTS),
                "environment": environment(),
                "max_upload": MAX_UPLOAD,
            })
        if parts == ["api", "jobs"]:
            return self.send_json({"jobs": self.manager.listing()})
        if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            try:
                job = dict(self.manager.get(parts[2]))
            except KeyError:
                return self.fail(HTTPStatus.NOT_FOUND, "No such job.")
            job["log"] = self.manager.log_tail(parts[2])
            return self.send_json(job)
        if len(parts) == 5 and parts[:2] == ["api", "jobs"] and parts[3] == "files":
            return self.send_file(parts[2], parts[4])
        self.fail(HTTPStatus.NOT_FOUND, "Not found.")

    def send_file(self, job_id, name):
        try:
            job = self.manager.get(job_id)
        except KeyError:
            return self.fail(HTTPStatus.NOT_FOUND, "No such job.")
        if name not in OUTPUTS:  # fixed names only; never a client-supplied path
            return self.fail(HTTPStatus.NOT_FOUND, "Unknown file.")
        path = self.manager.folder(job_id) / OUTPUTS[name]
        if not path.is_file():
            return self.fail(HTTPStatus.NOT_FOUND, "Not available yet.")
        stem = re.sub(r"[^\w.-]+", "_", Path(job["name"]).stem)[:80] or "paper"
        download = "download" in parse_qs(urlparse(self.path).query)
        filename = stem + "-" + name
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            "application/zip" if name.endswith(".zip") else "application/pdf",
        )
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header(
            "Content-Disposition",
            ("attachment" if download else "inline") + '; filename="%s"' % filename,
        )
        self.end_headers()
        with path.open("rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def do_POST(self):
        if not self.guard(mutating=True):
            return
        parts = self.route()
        try:
            if parts == ["api", "jobs"]:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= MAX_UPLOAD:
                    return self.fail(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Upload size not allowed."
                    )
                data = self.rfile.read(length)
                query = parse_qs(urlparse(self.path).query)
                options = json.loads(query.get("options", ["{}"])[0])
                name = query.get("name", ["paper.pdf"])[0]
                job = self.manager.create(name, data, options)
                return self.send_json(job, HTTPStatus.CREATED)
            if len(parts) == 4 and parts[:2] == ["api", "jobs"]:
                action = {"cancel": self.manager.cancel, "resume": self.manager.resume}
                if parts[3] in action:
                    return self.send_json(action[parts[3]](parts[2]))
        except KeyError:
            return self.fail(HTTPStatus.NOT_FOUND, "No such job.")
        except ValueError as exc:
            return self.fail(HTTPStatus.BAD_REQUEST, str(exc))
        self.fail(HTTPStatus.NOT_FOUND, "Not found.")

    def do_DELETE(self):
        if not self.guard(mutating=True):
            return
        parts = self.route()
        if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            try:
                self.manager.delete(parts[2])
            except KeyError:
                return self.fail(HTTPStatus.NOT_FOUND, "No such job.")
            except ValueError as exc:
                return self.fail(HTTPStatus.CONFLICT, str(exc))
            return self.send_json({"deleted": parts[2]})
        self.fail(HTTPStatus.NOT_FOUND, "Not found.")


def hosts_for(host, port):
    names = {host}
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if loopback:
        names |= {"localhost", "127.0.0.1", "[::1]"}
    if host in ("0.0.0.0", "::"):
        import socket

        names |= {"localhost", "127.0.0.1", socket.gethostname().lower()}
    return {n + ":" + str(port) for n in names} | names


def make_server(host, port, workdir, command=None, extra_hosts=()):
    manager = JobManager(workdir, command)
    handler = type(
        "PaperReTeXHandler",
        (Handler,),
        {
            "manager": manager,
            "allowed_hosts": {h.lower() for h in hosts_for(host, port)}
            | {h.lower() for h in extra_hosts},
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    # Port 0 picks a free port; allow the real one in Host headers.
    handler.allowed_hosts |= {h.lower() for h in hosts_for(host, server.server_port)}
    return server, manager


def serve(host="127.0.0.1", port=8765, workdir=None, allow_host=()):
    workdir = Path(workdir) if workdir else cache_root() / "web"
    try:
        server, _ = make_server(host, port, workdir, extra_hosts=allow_host)
    except OSError as exc:
        if exc.errno in (48, 98, 10048):  # address in use: macOS, Linux, Windows
            raise SystemExit(
                "Port %d is already in use; choose another with --port." % port
            ) from None
        raise
    shown = "localhost" if host in ("127.0.0.1", "::1", "localhost") else host
    print("PaperReTeX web app: http://%s:%d" % (shown, server.server_port), flush=True)
    print("Jobs and results: " + str(workdir.resolve()), flush=True)
    if host not in ("127.0.0.1", "::1", "localhost"):
        print(
            "Warning: listening beyond this computer. Anyone who can reach it can "
            "upload PDFs and spend your LLM CLI quota; there is no login.",
            file=sys.stderr,
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the PaperReTeX web app.")
    add_arguments(parser)
    args = parser.parse_args(argv)
    serve(args.host, args.port, args.workdir, args.allow_host)


def add_arguments(parser):
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--workdir",
        help="Folder for uploaded PDFs and results (default: ~/.cache/paperretex/web).",
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        help="Extra Host header to accept, e.g. a LAN name (repeatable).",
    )
