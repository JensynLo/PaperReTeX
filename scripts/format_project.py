#!/usr/bin/env python3
"""Format tracked project text with the same Ruff and Prettier as VS Code."""

import argparse
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def project_files():
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return sorted({Path(name.decode()) for name in result.stdout.split(b"\0") if name})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report files needing formatting without changing them",
    )
    args = parser.parse_args(argv)

    files = project_files()
    groups = {
        "Python (Ruff 0.17.0)": (
            ["uvx", "--from", "ruff==0.17.0", "ruff", "format"],
            ["--check"] if args.check else [],
            [p for p in files if p.suffix == ".py"],
        ),
        "Markdown/JSON (Prettier 3.7.4)": (
            ["npx", "--yes", "prettier@3.7.4"],
            ["--check"] if args.check else ["--write"],
            [p for p in files if p.suffix in (".md", ".json")],
        ),
        "TOML (Taplo 0.9.3)": (
            ["uvx", "--from", "taplo==0.9.3", "taplo", "format"],
            ["--check"] if args.check else [],
            [p for p in files if p.suffix == ".toml"],
        ),
        "Shell (shfmt)": (
            ["shfmt", "-i", "2", "-ci", "-d" if args.check else "-w"],
            [],
            [p for p in files if p.suffix == ".sh"],
        ),
    }
    missing = [
        command[0]
        for command, _, selected in groups.values()
        if selected and not shutil.which(command[0])
    ]
    if missing:
        raise SystemExit("Missing formatting tools: " + ", ".join(sorted(set(missing))))

    failed = False
    for label, (command, options, selected) in groups.items():
        if not selected:
            continue
        print(f"{label}: {len(selected)} files", flush=True)
        result = subprocess.run(
            command + options + [str(p) for p in selected], cwd=ROOT
        )
        failed |= result.returncode != 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
