"""The OCR worker's page action, with the recognizer replaced by a stub.

UniMERNet cannot run here, so ``_latex`` is stubbed; the tests cover decoding,
segmentation, cropping, ordering and the expression cap. PIL is the worker's
own dependency, so these tests skip where it is not installed.
"""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from circuit_mcp import ocr_worker

Image = pytest.importorskip("PIL.Image")


def png(array):
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def engine(monkeypatch):
    engine = ocr_worker._Engine("unimernet_test", "cpu")
    engine.device = "cpu"
    monkeypatch.setattr(engine, "load", lambda: None)
    seen = []

    def fake_latex(image):
        seen.append(image.size)
        return f"expr{len(seen)}", 0.01

    monkeypatch.setattr(engine, "_latex", fake_latex)
    engine.seen = seen
    return engine


def test_page_transcription_crops_each_expression_in_reading_order(engine):
    page = np.full((300, 400), 255, np.uint8)
    page[40:70, 50:150] = 0
    page[130:160, 60:200] = 0
    result = engine.transcribe_page(png(page))
    assert result["ok"] is True
    assert [e["latex"] for e in result["expressions"]] == ["expr1", "expr2"]
    assert [e["index"] for e in result["expressions"]] == [0, 1]
    first, second = (e["bbox"] for e in result["expressions"])
    assert first[1] < second[1]
    assert engine.seen[0] == (first[2] - first[0], first[3] - first[1])   # the crop, not the page
    assert result["expression_count"] == 2
    assert result["truncated"] is False
    assert (result["image_width"], result["image_height"]) == (400, 300)


def test_page_transcription_caps_the_expression_count(engine):
    page = np.full((2100, 200), 255, np.uint8)
    for i in range(70):
        page[i * 30 : i * 30 + 10, 20:120] = 0
    result = engine.transcribe_page(png(page))
    assert result["expression_count"] == 70
    assert len(result["expressions"]) == ocr_worker.MAX_PAGE_EXPRESSIONS
    assert result["truncated"] is True


def test_a_page_over_the_pixel_limit_is_refused_before_segmentation(engine, monkeypatch):
    segmented = []
    monkeypatch.setattr(ocr_worker.page_segment, "expression_boxes", lambda gray: segmented.append(gray) or [])
    monkeypatch.setattr(ocr_worker, "MAX_PAGE_PIXELS", 100)
    with pytest.raises(ValueError, match=r"30x20 .*100"):
        engine.transcribe_page(png(np.full((20, 30), 255, np.uint8)))
    assert segmented == []
    assert engine.seen == []


def test_an_undecodable_page_is_a_value_error(engine):
    with pytest.raises(ValueError, match="Could not decode"):
        engine.transcribe_page(b"\x89PNG\r\n\x1a\nnot really")


def test_the_worker_script_imports_page_segment_outside_the_package():
    worker = Path(ocr_worker.__file__)
    run = subprocess.run([sys.executable, str(worker)], capture_output=True, text=True, timeout=60)
    assert "usage: ocr_worker.py" in run.stderr
    assert "Error" not in run.stderr.replace("SystemExit", "")
