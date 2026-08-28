from pathlib import Path
import os
import signal
import subprocess
import sys
import tempfile
import time

from circuit_mcp.ocr_client import OCRWorker


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
