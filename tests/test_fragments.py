import json
from pathlib import Path
from types import SimpleNamespace
import fitz
import pytest
from paperretex.fragments import (
    boundary_spaces,
    fragment_groups,
    merge_fragments,
    review_fragments,
)


def region(rid, text, box, page=1, kind="paragraph", size=10):
    return {
        "id": rid,
        "page": page,
        "type": kind,
        "text": text,
        "source_bbox": list(box),
        "bbox": [v * 2 for v in box],
        "segments": [{"text": text}],
        "formula_ids": [],
        "font_size": size,
        "font_name": "Times",
        "bold": False,
        "italic": False,
    }


def split_line():
    # "In summary, our contributions are as follows:" as BabelDOC returned it.
    pieces = [
        ("I", 321, 324),
        ("n summary, our con", 324, 403),
        ("t", 403, 406),
        ("r", 406, 409),
        ("ib", 409, 417),
        ("u", 417, 422),
        ("ti", 422, 427),
        ("ons are as", 428, 466),
        ("f", 469, 473),
        ("o", 473, 477),
        ("ll", 478, 483),
        ("ows:", 483, 501),
    ]
    rows = [
        region("p002-r%04d" % n, text, (x0, 388, x1, 394))
        for n, (text, x0, x1) in enumerate(pieces, 8)
    ]
    rows[1]["source_bbox"][1] = 384
    rows[9]["source_bbox"][1] = 393  # a short glyph box such as "o"
    return rows


def test_split_line_is_one_group_and_neighbours_stay_apart():
    rows = split_line()
    left = region("p002-r0001", "Left column text ends here.", (50, 386, 290, 396))
    below = region("p002-r0020", "• A novel 3D Gaussian ...", (309, 397, 545, 431))
    column = region("p002-r0021", "Right column.", (320, 440, 545, 450))
    beside = region("p002-r0022", "ok", (560, 440, 580, 450))  # beyond a column gap
    groups = fragment_groups([left] + rows + [below, column, beside])
    assert len(groups) == 1
    group = groups[0]
    assert group["regions"] == [r["id"] for r in rows]
    from paperretex.fragments import joined

    assert (
        joined(group["fragments"], group["spaces"])
        == "In summary, our contributions are as follows:"
    )


def test_reviewer_may_only_change_spaces():
    parts = ["con", "t", "r", "ib", "u", "ti", "ons are as", "f"]
    assert boundary_spaces(parts, "contributions are as f") == [
        False,
        False,
        False,
        False,
        False,
        False,
        True,
    ]
    assert boundary_spaces(parts, "contributions are a f") is None


def make_pdf(path):
    with fitz.open() as doc:
        page = doc.new_page(width=612, height=792)
        page.insert_text((321, 394), "In summary, our contributions are as follows:")
        doc.save(path)


def test_review_merges_caches_and_retries_failures(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf)
    rows = split_line()
    formulas = [{"id": "f00001", "region_id": rows[4]["id"]}]
    rows[4]["formula_ids"] = ["f00001"]
    groups = fragment_groups(rows)
    calls = []
    monkeypatch.setattr("paperretex.llm_cli.shutil.which", lambda x: "/fake/codex")

    def failing(command, **kwargs):
        calls.append(command)
        raise RuntimeError("network down")

    monkeypatch.setattr("paperretex.fragments.run_review", failing)
    with pytest.raises(RuntimeError, match="network down"):
        review_fragments(groups, rows, pdf, tmp_path)

    def run(command, input, **kwargs):
        calls.append(command)
        payload = json.loads(input[input.index("[") :])
        answer = {
            "groups": [
                {
                    "id": g["id"],
                    "merge": True,
                    "text": "In summary, our contributions are as follows:",
                    "notes": "",
                }
                for g in payload
            ]
        }
        Path(command[command.index("--output-last-message") + 1]).write_text(
            json.dumps(answer)
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("paperretex.fragments.run_review", run)
    verdicts, stats = review_fragments(groups, rows, pdf, tmp_path)
    assert stats["new_calls"] == 1 and len(calls) == 2
    assert (tmp_path / "fragment-review").exists()
    again, stats = review_fragments(groups, rows, pdf, tmp_path)
    assert stats["cached_batches"] == 1 and len(calls) == 2

    merged = merge_fragments(rows, formulas, groups, verdicts)
    assert merged == ["g001"] and len(rows) == 1
    line = rows[0]
    assert line["text"] == "In summary, our contributions are as follows:"
    assert "".join(s["text"] for s in line["segments"]) == line["text"]
    assert line["source_bbox"] == [321, 384, 501, 394]
    assert formulas[0]["region_id"] == line["id"]
    assert line["formula_ids"] == ["f00001"]


def test_rejected_merge_and_changed_glyphs(tmp_path):
    from paperretex.fragments import validate

    groups = fragment_groups(split_line())
    keep = validate(
        {"groups": [{"id": "g001", "merge": False, "text": "", "notes": "cells"}]},
        groups,
    )
    rows = split_line()
    assert merge_fragments(rows, [], groups, {"g001": keep[0]}) == []
    assert len(rows) == 12
    edited = validate(
        {
            "groups": [
                {
                    "id": "g001",
                    "merge": True,
                    "text": "In summary, our contribution is as follows:",
                    "notes": "",
                }
            ]
        },
        groups,
    )[0]
    assert "glyphs changed" in edited["notes"]
    assert edited["spaces"] == groups[0]["spaces"]
    with pytest.raises(ValueError):
        validate({"groups": []}, groups)
