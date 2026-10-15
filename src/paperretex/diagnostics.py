"""Report installed CLI, compiler, and isolated model runtimes."""

import os
import shutil
import sys

import fitz
import pypdf

from .config import cache_root
from .hybrid import runtime


def doctor():
    runtimes = {}
    for name in ("babeldoc", "ocr"):
        try:
            runtimes[name + "_python"] = str(runtime(name))
        except ValueError:
            runtimes[name + "_python"] = None
    local_mineru = (
        cache_root()
        / "ocr-runtime"
        / ("Scripts/mineru.exe" if os.name == "nt" else "bin/mineru")
    )
    return {
        **runtimes,
        "python": sys.version.split()[0],
        "pymupdf": fitz.VersionBind,
        "pypdf": pypdf.__version__,
        "xelatex": shutil.which("xelatex"),
        "codex": shutil.which("codex"),
        "claude": shutil.which("claude"),
        "mineru": shutil.which("mineru")
        or (str(local_mineru) if local_mineru.is_file() else None),
    }
