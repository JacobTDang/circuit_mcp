from __future__ import annotations

import subprocess
import concurrent.futures

import pytest

from circuit_mcp import ipad_capture

PNG = b"\x89PNG\r\n\x1a\nframe"


def test_airplay_frame_is_preferred_and_hashed(monkeypatch, tmp_path):
    service = ipad_capture.IPadCaptureService()
    service._client_connected = True
    service._stream_file = tmp_path / "frame.h264"
    service._stream_file.write_bytes(b"h264-stream")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        open(command[-1], "wb").write(PNG)
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(ipad_capture.subprocess, "run", run)
    result = service.capture("auto")
    assert result["source"] == "airplay"
    assert result["headless"] is True
    assert result["png"] == PNG
    assert len(result["sha256"]) == 64
    assert "-update" in calls[0]
    assert "-sseof" not in calls[0]


def test_airplay_frame_cache_shares_decode_between_fast_pollers(monkeypatch, tmp_path):
    service = ipad_capture.IPadCaptureService()
    service._client_connected = True
    service._stream_file = tmp_path / "frame.h264"
    service._stream_file.write_bytes(b"h264-stream")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        open(command[-1], "wb").write(PNG)
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(ipad_capture.subprocess, "run", run)
    first = service.capture("airplay")
    second = service.capture("airplay")
    assert first["png"] == second["png"] == PNG
    assert len(calls) == 1


def test_usb_is_the_automatic_fallback(monkeypatch, tmp_path):
    service = ipad_capture.IPadCaptureService()
    helper = tmp_path / "usb"
    helper.write_text("")
    monkeypatch.setattr(ipad_capture, "USB_CAPTURE", helper)
    def run(command, **kwargs):
        open(command[-1], "wb").write(PNG)
        return subprocess.CompletedProcess(command, 0, '{"ok":true}', "")
    monkeypatch.setattr(ipad_capture.subprocess, "run", run)
    assert service.capture()["source"] == "usb"


def test_source_validation_and_no_source_error(monkeypatch, tmp_path):
    service = ipad_capture.IPadCaptureService()
    monkeypatch.setattr(ipad_capture, "USB_CAPTURE", tmp_path / "missing")
    with pytest.raises(ipad_capture.IPadCaptureError, match="source must"):
        service.capture("sidecar")
    with pytest.raises(ipad_capture.IPadCaptureError, match="not built"):
        service.capture("auto")


def test_invalid_backend_bytes_are_rejected():
    with pytest.raises(ipad_capture.IPadCaptureError, match="non-PNG"):
        ipad_capture.IPadCaptureService._result(b"desktop", "usb", {})


def test_concurrent_status_polls_share_one_usb_discovery(monkeypatch, tmp_path):
    service = ipad_capture.IPadCaptureService()
    helper = tmp_path / "usb"
    helper.write_text("")
    monkeypatch.setattr(ipad_capture, "USB_CAPTURE", helper)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "[]", "")
    monkeypatch.setattr(ipad_capture.subprocess, "run", run)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: service.status(), range(100)))
    assert len(results) == 100
    assert len(calls) == 1


def test_forked_child_cannot_run_parent_receiver_cleanup(monkeypatch):
    service = ipad_capture.IPadCaptureService()
    calls = []
    monkeypatch.setattr(service, "stop_airplay", lambda: calls.append(True))
    monkeypatch.setattr(ipad_capture.os, "getpid", lambda: service._owner_pid + 1)
    service.close()
    assert calls == []
    monkeypatch.setattr(ipad_capture.os, "getpid", lambda: service._owner_pid)
    service.close()
    assert calls == [True]
