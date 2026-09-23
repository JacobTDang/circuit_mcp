from pathlib import Path
import os
import signal
import subprocess
import sys
import tempfile
import time

from circuit_mcp.ocr_client import MAX_IMAGE_BYTES, OCRWorker


def _configuration(tmp_path: Path):
    model = tmp_path / "model"
    model.mkdir(exist_ok=True)
    (model / "unimernet_test.pth").write_bytes(b"placeholder")
    (model / "config.json").write_text("{}")
    (model / "tokenizer.json").write_text("{}")
    import sys
    return Path(sys.executable), model, "cpu"


def test_status_protocol_reuses_one_persistent_worker(tmp_path, monkeypatch):
    worker = OCRWorker()
    monkeypatch.setattr(worker, "_configuration", lambda: _configuration(tmp_path))
    try:
        first = worker.call({"action": "status", "load_model": False})
        pid = worker.pid
        second = worker.call({"action": "status", "load_model": False})
        assert first["ok"] is second["ok"] is True
        assert first["loaded"] is second["loaded"] is False
        assert worker.pid == pid
    finally:
        worker.shutdown()
    assert worker.pid is None


def test_bad_png_is_refused_before_worker_start():
    worker = OCRWorker()
    result = worker.call({"action": "transcribe", "png": b"not png"})
    assert result["error"] == "bad_image"
    assert worker.pid is None


def _refuse_start(worker, monkeypatch):
    started = []

    def start():
        started.append(True)
        raise AssertionError("the worker must not be started")

    monkeypatch.setattr(worker, "_start", start)
    return started


def test_a_non_png_page_is_refused_before_worker_start(monkeypatch):
    worker = OCRWorker()
    started = _refuse_start(worker, monkeypatch)
    result = worker.call({"action": "transcribe_page", "png": b"GIF89a"})
    assert result["error"] == "bad_image"
    assert started == []
    assert worker.pid is None


def test_an_oversized_page_is_refused_before_worker_start(monkeypatch):
    worker = OCRWorker()
    started = _refuse_start(worker, monkeypatch)
    page = b"\x89PNG\r\n\x1a\n" + bytes(MAX_IMAGE_BYTES)
    result = worker.call({"action": "transcribe_page", "png": page})
    assert result["error"] == "image_too_large"
    assert started == []
    assert worker.pid is None


def test_dead_worker_is_restarted_and_request_retried(tmp_path, monkeypatch):
    worker = OCRWorker()
    monkeypatch.setattr(worker, "_configuration", lambda: _configuration(tmp_path))
    try:
        assert worker.call({"action": "status", "load_model": False})["ok"]
        first = worker.pid
        os.kill(first, signal.SIGKILL)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and worker._process.poll() is None:
            time.sleep(0.01)
        result = worker.call({"action": "status", "load_model": False})
        assert result["ok"] is True
        assert worker.pid not in (None, first)
    finally:
        worker.shutdown()


def test_missing_installation_is_actionable(tmp_path, monkeypatch):
    worker = OCRWorker()
    monkeypatch.setattr(
        worker,
        "_configuration",
        lambda: (tmp_path / "missing-python", tmp_path / "missing-model", "auto"),
    )
    result = worker.availability()
    assert result["ok"] is False
    assert "setup_ocr.sh" in result["message"]


def test_timeout_kills_a_wedged_worker(monkeypatch):
    worker = OCRWorker()

    def start():
        worker._stderr = tempfile.TemporaryFile()
        worker._process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=worker._stderr,
            start_new_session=True,
        )
        return worker._process

    monkeypatch.setattr(worker, "_start", start)
    result = worker.call({"action": "status", "load_model": False}, timeout=0.1)
    assert result["error"] == "ocr_timeout"
    assert worker.pid is None


# --- a page must not block every other OCR call ------------------------------

STUB = Path(__file__).parent / "fixtures" / "stub_ocr_worker.py"


def _stub_worker(monkeypatch, **environment):
    """An OCRWorker talking to the stub, over real pipes."""
    worker = OCRWorker()

    def start():
        worker._stderr = tempfile.TemporaryFile()
        worker._process = subprocess.Popen(
            [sys.executable, str(STUB)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=worker._stderr,
            bufsize=0, start_new_session=True,
            env={**os.environ, **{key: str(value) for key, value in environment.items()}},
        )
        return worker._process

    monkeypatch.setattr(worker, "_start", start)
    return worker


def test_an_image_call_during_a_page_waits_one_box(monkeypatch):
    """A page holds the worker for minutes; a single image must not wait for all of it."""
    import threading

    worker = _stub_worker(monkeypatch, STUB_BOXES=6, STUB_BOX_SECONDS=0.2, STUB_IMAGE_SECONDS=0.02)
    page_result = {}
    try:
        def read_page():
            page_result.update(worker.transcribe_page(b"\x89PNG\r\n\x1a\npage", timeout=30))

        reader = threading.Thread(target=read_page)
        reader.start()
        time.sleep(0.25)                      # the page is under way
        started = time.monotonic()
        single = worker.call({"action": "transcribe", "png": b"\x89PNG\r\n\x1a\nimage"})
        waited = time.monotonic() - started
        reader.join(timeout=30)
    finally:
        worker.shutdown()

    assert single["ok"] is True
    assert waited < 0.5, f"waited {waited:.2f}s -- that is the whole page, not one box"
    assert page_result["ok"] is True
    assert [e["latex"] for e in page_result["expressions"]] == [f"expr{n}" for n in range(1, 7)]


def test_a_page_the_worker_forgot_fails_loudly(monkeypatch):
    """A worker restarted mid-page must not return a half-read page as if it were whole."""
    worker = _stub_worker(monkeypatch, STUB_BOXES=4, STUB_BOX_SECONDS=0.0, STUB_FORGET_AFTER=2)
    try:
        result = worker.transcribe_page(b"\x89PNG\r\n\x1a\npage", timeout=30)
    finally:
        worker.shutdown()

    assert result["ok"] is False
    assert result["error"] == "page_expired"
    assert "2 of 4" in result["message"]


def test_a_page_is_refused_before_the_worker_starts_when_it_is_not_png(monkeypatch):
    worker = _stub_worker(monkeypatch, STUB_BOXES=2)
    result = worker.transcribe_page(b"not png", timeout=5)
    assert result["error"] == "bad_image"
    assert worker.pid is None
