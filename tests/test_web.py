import json
from pathlib import Path
import sys
import threading
import time
import http.client

import pytest

from paperretex.web import (
    JobManager,
    clean_options,
    convert_args,
    make_server,
    read_progress,
    translate_args,
)

FAKE_CLI = r"""
import json, sys, time
from pathlib import Path
command = sys.argv[1]
if command == "convert":
    out = Path(sys.argv[sys.argv.index("-o") + 1])
    out.mkdir(exist_ok=True)
    for stage in ("babeldoc-layout", "formula-ocr", "codex-formula-review"):
        print("Hybrid: " + stage, file=sys.stderr, flush=True)
    print("Formula review: first-round batch 1/2 (36 expressions) completed in 1s",
          file=sys.stderr, flush=True)
    if "--pages" in sys.argv and sys.argv[sys.argv.index("--pages") + 1] == "9":
        print("paperretex: Invalid page selection.", file=sys.stderr)
        sys.exit(2)
    time.sleep(0.2)
    print("Hybrid: region-tex-and-assembly", file=sys.stderr, flush=True)
    (out / "main.pdf").write_bytes(b"%PDF-1.5 rebuilt")
    (out / "main.tex").write_text("main")
    (out / "main-flat.tex").write_text("flat")
    (out / "regions").mkdir(exist_ok=True)
    (out / "regions" / "p001-r0001.tex").write_text("x")
    (out / "report.json").write_text(json.dumps({
        "pages": [0], "unresolved_formula_ids": ["f00002"],
        "formula_review": {"reviewed": 3, "uncertain": 1, "new_calls": 2},
        "duration_seconds": 1.0}))
    sys.exit(1)  # unresolved formulas still produce a usable project
out = Path(sys.argv[sys.argv.index("-o") + 1])
out.mkdir(exist_ok=True)
print("Translating batch 1/1 (3 fragments)", file=sys.stderr, flush=True)
(out / "translated.pdf").write_bytes(b"%PDF-1.5 translated")
(out / "bilingual.pdf").write_bytes(b"%PDF-1.5 bilingual")
"""


def test_options_are_whitelisted():
    o = clean_options({"pages": "1-3, 5", "codex_effort": "high"})
    assert o["pages"] == "1-3,5" and o["codex_effort"] == "high"
    for bad in (
        {"pages": "1;rm"},
        {"llm_provider": "other"},
        {"llm_provider": "claude", "codex_effort": "high"},
        {"formula_batch_size": 100},
        {"target": "x" * 41},
        {"extra": "--evil"},
    ):
        with pytest.raises(ValueError):
            clean_options(bad)


def test_cli_arguments_follow_options():
    o = clean_options({"layout_review": "local", "pages": "2", "target": "日本語"})
    args = convert_args(o, resume=True)
    assert args[:5] == ["convert", "input.pdf", "-o", "convert", "--compile"]
    assert "--resume" in args and args[args.index("--layout-review") + 1] == "local"
    assert args[args.index("--pages") + 1] == "2"
    t = translate_args(o)
    assert t[1] == "convert/main-flat.tex" and t[t.index("--target") + 1] == "日本語"


def test_progress_lines_drive_stages():
    job = {"options": {"translate": True}, "stage": "layout"}
    read_progress("Hybrid: codex-formula-review\n", job)
    read_progress(
        "Formula review: first-round batch 3/6 (36 expressions) completed\n", job
    )
    assert job["stage"] == "review" and job["stage_fraction"] == 0.5
    read_progress("Hybrid: codex-formula-context-review\n", job)
    assert job["review_round"] == 2 and 0 < job["progress"] < 1


@pytest.fixture
def server(tmp_path):
    fake = tmp_path / "fake_cli.py"
    fake.write_text(FAKE_CLI)
    srv, manager = make_server(
        "127.0.0.1", 0, tmp_path / "web", command=[sys.executable, str(fake)]
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, manager
    srv.shutdown()
    srv.server_close()


def request(srv, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
    h = {"Host": host or "127.0.0.1:%d" % srv.server_port}
    h.update(headers or {})
    conn.request(method, path, body=body, headers=h)
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, data, res


def wait(srv, job_id, states=("done", "failed")):
    for _ in range(200):
        status, data, _ = request(srv, "GET", "/api/jobs/" + job_id)
        job = json.loads(data)
        if job["status"] in states:
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish: " + job["status"])


def test_upload_run_and_download(server):
    srv, _ = server
    status, page, _ = request(srv, "GET", "/")
    assert status == 200 and b"PaperReTeX" in page
    query = "/api/jobs?name=Attention.pdf&options=" + "%7B%7D"
    # Cross-site or rebinding requests are refused before anything runs.
    assert request(srv, "POST", query, b"%PDF-1.5")[0] == 403
    assert (
        request(srv, "GET", "/api/jobs", host="evil.example:%d" % srv.server_port)[0]
        == 403
    )
    head = {"X-PaperReTeX": "1", "Content-Type": "application/pdf"}
    assert request(srv, "POST", query, b"<html>", head)[0] == 400
    status, data, _ = request(srv, "POST", query, b"%PDF-1.5 input", head)
    assert status == 201
    job = wait(srv, json.loads(data)["id"])
    assert job["status"] == "done" and job["progress"] == 1
    assert set(job["outputs"]) == {
        "bilingual.pdf",
        "translated.pdf",
        "rebuilt.pdf",
        "latex-project.zip",
    }
    assert "unresolved" in job["warnings"][0]
    assert "$ paperretex convert input.pdf" in job["log"]
    status, data, res = request(
        srv, "GET", "/api/jobs/%s/files/bilingual.pdf?download=1" % job["id"]
    )
    assert status == 200 and data == b"%PDF-1.5 bilingual"
    assert res.getheader("Content-Disposition").startswith("attachment")
    assert 'filename="Attention-bilingual.pdf"' in res.getheader("Content-Disposition")
    assert (
        request(srv, "GET", "/api/jobs/%s/files/..%%2Fjob.json" % job["id"])[0] == 404
    )
    assert request(srv, "DELETE", "/api/jobs/" + job["id"], headers=head)[0] == 200
    assert request(srv, "GET", "/api/jobs/" + job["id"])[0] == 404


def test_failed_convert_reports_cli_error(server):
    srv, _ = server
    head = {"X-PaperReTeX": "1"}
    options = json.dumps({"pages": "9"}).replace('"', "%22").replace(" ", "")
    status, data, _ = request(
        srv, "POST", "/api/jobs?name=a.pdf&options=" + options, b"%PDF-1.5", head
    )
    job = wait(srv, json.loads(data)["id"])
    assert job["status"] == "failed" and "Invalid page selection" in job["error"]


def test_missing_xelatex_is_named_in_the_error(tmp_path):
    fake = tmp_path / "fake.py"
    fake.write_text(
        "import json, pathlib\n"
        "out = pathlib.Path('convert'); out.mkdir()\n"
        "(out / 'main-flat.tex').write_text('x')\n"
        "(out / 'report.json').write_text(json.dumps("
        "{'compile': {'status': 'unavailable'}}))\n"
    )
    manager = JobManager(tmp_path / "web", command=[sys.executable, str(fake)])
    job = manager.create("a.pdf", b"%PDF-1.5", {})
    for _ in range(100):
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert job["status"] == "failed" and "XeLaTeX was not found" in job["error"]


def test_restart_marks_unfinished_jobs_interrupted(tmp_path):
    folder = tmp_path / "jobs" / "j1"
    folder.mkdir(parents=True)
    (folder / "job.json").write_text(
        json.dumps({"id": "j1", "status": "running", "created": 1, "options": {}})
    )
    manager = JobManager(tmp_path, command=[sys.executable, "-c", "pass"])
    assert manager.get("j1")["status"] == "interrupted"
    assert json.loads((folder / "job.json").read_text())["status"] == "interrupted"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_cli_unwinds_on_sigterm_even_when_sigint_was_ignored(tmp_path):
    import signal
    import subprocess

    script = tmp_path / "child.py"
    script.write_text(
        "import sys, time\n"
        "from paperretex.cli import install_interrupts\n"
        "install_interrupts()\n"
        "print('ready', flush=True)\n"
        "try:\n"
        "    time.sleep(30)\n"
        "except KeyboardInterrupt:\n"
        "    print('cleanup', flush=True)\n"
    )
    src = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        text=True,
        env={"PYTHONPATH": src, "PATH": "/usr/bin:/bin"},
        # A background job of a non-interactive shell starts like this.
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
    )
    # Importing PyMuPDF may print a deprecation notice first.
    while proc.stdout.readline().strip() != "ready":
        pass
    proc.send_signal(signal.SIGTERM)
    assert proc.communicate(timeout=10)[0].strip() == "cleanup"
