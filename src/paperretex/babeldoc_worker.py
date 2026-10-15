"""Pinned BabelDOC 0.6.4 layout-only subprocess adapter (Python >=3.12).

Stop immediately after StylesAndFormulas, before translation/typesetting. The
hook avoids reimplementing BabelDOC's PDF normalization and parser setup.
"""

import importlib.metadata
import json
import logging
from pathlib import Path
import sys


class LayoutComplete(Exception):
    pass


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    # Running this file directly must not import our sibling babeldoc.py as the
    # third-party BabelDOC package.
    sys.path = [
        p
        for p in sys.path
        if Path(p or ".").resolve() != Path(__file__).parent.resolve()
    ]
    version = importlib.metadata.version("BabelDOC")
    if version != "0.6.4":
        raise RuntimeError("Layout adapter requires BabelDOC==0.6.4; found " + version)
    from babeldoc.docvision.doclayout import OnnxModel
    from babeldoc.format.pdf import high_level
    from babeldoc.format.pdf.document_il.xml_converter import XMLConverter
    from babeldoc.format.pdf.translation_config import TranslationConfig
    from babeldoc.progress_monitor import ProgressMonitor

    pdf, output = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    pages = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
    output.mkdir(parents=True, exist_ok=True)
    high_level.init()
    config = TranslationConfig(
        translator=None,
        input_file=pdf,
        lang_in="en",
        lang_out="en",
        doc_layout_model=OnnxModel.from_pretrained(),
        pages=pages,
        working_dir=output / "work",
        output_dir=output,
        skip_translation=True,
        auto_extract_glossary=False,
        use_rich_pbar=False,
        only_include_translated_page=True,
        skip_clean=True,
        skip_scanned_detection=True,
    )
    original = high_level.StylesAndFormulas.process

    def export_and_stop(instance, document, *args, **kwargs):
        original(instance, document, *args, **kwargs)
        XMLConverter().write_json(document, output / "layout-ir.json")
        (output / "provenance.json").write_text(
            json.dumps(
                {
                    "engine": "BabelDOC",
                    "version": version,
                    "last_stage": "StylesAndFormulas",
                    "translation_called": False,
                    "typesetting_called": False,
                    "pages": pages,
                },
                indent=2,
            )
        )
        raise LayoutComplete()

    high_level.StylesAndFormulas.process = export_and_stop
    try:
        with ProgressMonitor(high_level.get_translation_stage(config)) as monitor:
            try:
                high_level._do_translate_single(monitor, config)
            except LayoutComplete:
                return
        raise RuntimeError(
            "BabelDOC did not reach the expected formula extraction stage."
        )
    finally:
        high_level.StylesAndFormulas.process = original


if __name__ == "__main__":
    main()
