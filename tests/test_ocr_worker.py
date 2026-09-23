"""The OCR worker's page action, with the recognizer replaced by a stub.

UniMERNet cannot run here, so ``_latex`` is stubbed; the tests cover decoding,
segmentation, cropping, ordering and the expression cap. PIL is the worker's
own dependency, so these tests skip where it is not installed.
"""
from __future__ import annotations

import io
import pickle
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_the_pixel_limit_is_checked_from_the_header_before_any_pixel_is_decoded(engine, monkeypatch):
    page = png(np.full((20, 30), 255, np.uint8))
    header_only = page[: page.index(b"IDAT") + 4]   # the pixel data is cut off
    with pytest.raises(ValueError, match="Could not decode"):
        engine.transcribe_page(header_only)
    monkeypatch.setattr(ocr_worker, "MAX_PAGE_PIXELS", 100)
    with pytest.raises(ValueError, match=r"30x20 .*100"):
        engine.transcribe_page(header_only)


def test_a_refused_page_does_not_load_the_model(engine, monkeypatch):
    def load():
        pytest.fail("the model was loaded for a page that is refused")

    monkeypatch.setattr(engine, "load", load)
    monkeypatch.setattr(ocr_worker, "MAX_PAGE_PIXELS", 100)
    with pytest.raises(ValueError, match=r"30x20 .*100"):
        engine.transcribe_page(png(np.full((20, 30), 255, np.uint8)))
    with pytest.raises(ValueError, match="Could not decode"):
        engine.transcribe_page(b"\x89PNG\r\n\x1a\nnot really")


def test_the_worker_loop_dispatches_a_page_request(monkeypatch):
    class StubEngine:
        def __init__(self, model_dir, device):
            pass

        def transcribe_page(self, png):
            return {"ok": True, "expressions": [], "page": png}

    request = io.BytesIO()
    ocr_worker._write_frame(request, {"action": "transcribe_page", "png": b"page"})
    request.seek(0)
    response = io.BytesIO()
    monkeypatch.setattr(ocr_worker, "_Engine", StubEngine)
    monkeypatch.setattr(ocr_worker.sys, "stdin", SimpleNamespace(buffer=request))
    monkeypatch.setattr(ocr_worker.sys, "stdout", SimpleNamespace(buffer=response))
    ocr_worker.serve("model", "cpu")
    framed = response.getvalue()
    header = ocr_worker.HEADER.size
    (size,) = ocr_worker.HEADER.unpack(framed[:header])
    assert len(framed) == header + size
    assert pickle.loads(framed[header:]) == {
        "ok": True, "expressions": [], "page": b"page"
    }


def test_an_undecodable_page_is_a_value_error(engine):
    with pytest.raises(ValueError, match="Could not decode"):
        engine.transcribe_page(b"\x89PNG\r\n\x1a\nnot really")


def test_the_worker_script_imports_page_segment_outside_the_package():
    worker = Path(ocr_worker.__file__)
    run = subprocess.run([sys.executable, str(worker)], capture_output=True, text=True, timeout=60)
    assert "usage: ocr_worker.py" in run.stderr
    assert "Error" not in run.stderr.replace("SystemExit", "")


def test_a_frame_is_painted_out_before_the_crop_is_recognized(monkeypatch):
    """The recognizer must not see the frame's edge; it reads it as ink."""
    engine = ocr_worker._Engine("unimernet_test", "cpu")
    engine.device = "cpu"
    monkeypatch.setattr(engine, "load", lambda: None)
    crops = []

    def fake_latex(image):
        crops.append(np.asarray(image.convert("L")))
        return "expr", 0.01

    monkeypatch.setattr(engine, "_latex", fake_latex)

    page = np.full((260, 400), 255, np.uint8)
    page[90:93, 30:260] = 0      # frame: top edge
    page[157:160, 30:260] = 0    # bottom edge
    page[90:160, 30:33] = 0      # left edge
    page[90:160, 257:260] = 0    # right edge
    page[97:153, 40:250] = 0     # the answer, close enough that the crop's padding reaches the frame

    result = engine.transcribe_page(png(page))

    assert result["expression_count"] == 1
    assert len(crops) == 1
    # The answer's ink survives; the frame's does not reach the recognizer.
    assert (crops[0] == 0).any()
    assert crops[0][:3, :].min() == 255 and crops[0][-3:, :].min() == 255


def test_a_page_is_opened_once_and_read_one_box_at_a_time(engine):
    """The worker holds the page so the client can take one box per request."""
    page = np.full((300, 400), 255, np.uint8)
    page[40:70, 50:150] = 0
    page[130:160, 60:200] = 0

    opened = engine.page_open(png(page))

    assert opened["ok"] is True
    assert opened["expression_count"] == 2
    assert opened["page_id"]
    assert (opened["image_width"], opened["image_height"]) == (400, 300)
    assert engine.seen == []  # opening transcribes nothing

    first = engine.page_expression(opened["page_id"], 0)
    second = engine.page_expression(opened["page_id"], 1)

    assert [first["latex"], second["latex"]] == ["expr1", "expr2"]
    assert first["bbox"][1] < second["bbox"][1]
    assert first["index"] == 0 and second["index"] == 1


def test_a_page_id_the_worker_no_longer_holds_is_refused(engine):
    page = np.full((300, 400), 255, np.uint8)
    page[40:70, 50:150] = 0
    opened = engine.page_open(png(page))
    engine.page_open(png(page))  # a second page replaces the first

    stale = engine.page_expression(opened["page_id"], 0)

    assert stale["ok"] is False
    assert stale["error"] == "page_expired"


def test_an_out_of_range_box_is_refused(engine):
    page = np.full((300, 400), 255, np.uint8)
    page[40:70, 50:150] = 0
    opened = engine.page_open(png(page))

    result = engine.page_expression(opened["page_id"], 5)

    assert result["ok"] is False
    assert result["error"] == "no_such_expression"
