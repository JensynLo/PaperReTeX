#!/usr/bin/env python3
"""One batched review round. Re-run once with revised images for verification."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from paperretex.assist import run_round

p = argparse.ArgumentParser()
p.add_argument("--folder", required=True)
p.add_argument("--prompt", required=True)
p.add_argument("--images", nargs="+", required=True)
p.add_argument(
    "--retry-failed-startup",
    action="store_true",
    help="Retry one proven pre-model startup failure once, preserving the original ledger.",
)
p.add_argument("--timeout", type=int, default=180)
p.add_argument("--llm-provider", choices=["codex", "claude"], default="codex")
p.add_argument("--llm-command")
a = p.parse_args()
result = run_round(
    a.folder,
    Path(a.prompt).read_text(),
    a.images,
    a.timeout,
    a.retry_failed_startup,
    a.llm_provider,
    a.llm_command,
)
print(result)
raise SystemExit(0 if result["status"] == "completed" else 1)
