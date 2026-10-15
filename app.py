"""Run the PaperReTeX web app: python app.py --port 8765

Equivalent to `paperretex serve`; works from a checkout without installing.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from paperretex.web import main  # noqa: E402

if __name__ == "__main__":
    main()
