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
    helper.chmod(0o755)
    monkeypatch.setenv("CIRCUIT_MCP_USB_CAPTURE", str(helper))
    def run(command, **kwargs):
        open(command[-1], "wb").write(PNG)
        return subprocess.CompletedProcess(command, 0, '{"ok":true}', "")
    monkeypatch.setattr(ipad_capture.subprocess, "run", run)
    assert service.capture()["source"] == "usb"


def test_source_validation_and_no_source_error(monkeypatch, tmp_path):
    service = ipad_capture.IPadCaptureService()
    monkeypatch.delenv("CIRCUIT_MCP_USB_CAPTURE", raising=False)
    monkeypatch.setenv("CIRCUIT_MCP_RUNTIME_DIR", str(tmp_path / "missing"))
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
    helper.chmod(0o755)
    monkeypatch.setenv("CIRCUIT_MCP_USB_CAPTURE", str(helper))
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "[]", "")
    monkeypatch.setattr(ipad_capture.subprocess, "run", run)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: service.status(), range(100)))
    assert len(results) == 100
    assert len(calls) == 1


def _usb_status():
    """The status field, without waiting out the background USB refresh.

    Discovery is cached off-request. Seeding that cache from one synchronous
    discovery is what a later poll returns, which is the sentence the desk shows.
    """
    service = ipad_capture.IPadCaptureService()
    discovered = service._discover_usb_devices()
    service._usb_cached = discovered
    service._usb_cached_at = 1e18
    return service.status()["usb"]


def test_a_missing_checkout_helper_names_the_setup_script(tmp_path, monkeypatch):
    """A checkout builds the helper; "not built" with no way to build it is a dead end."""
    monkeypatch.delenv("CIRCUIT_MCP_USB_CAPTURE", raising=False)
    monkeypatch.setenv("CIRCUIT_MCP_RUNTIME_DIR", str(tmp_path / "runtime"))
    usb = _usb_status()
    assert usb["available"] is False
    assert "not built" in usb["error"]
    assert "setup_ipad_capture.sh" in usb["error"]
    assert "broken" not in usb["error"].lower()
    with pytest.raises(ipad_capture.IPadCaptureError, match="setup_ipad_capture.sh"):
        ipad_capture.IPadCaptureService().capture("usb")


def test_a_missing_helper_in_the_app_names_a_broken_bundle(tmp_path, monkeypatch):
    """The app is supposed to carry the helper. The setup script is a checkout step."""
    monkeypatch.delenv("CIRCUIT_MCP_USB_CAPTURE", raising=False)
    monkeypatch.setenv("CIRCUIT_MCP_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(
        ipad_capture, "__file__",
        str(tmp_path / "Circuit MCP.app/Contents/Resources/python/lib/circuit_mcp/ipad_capture.py"))
    usb = _usb_status()
    assert usb["available"] is False
    assert "bundle is broken" in usb["error"]
    assert "setup_ipad_capture.sh" not in usb["error"]
    with pytest.raises(ipad_capture.IPadCaptureError, match="bundle is broken"):
        ipad_capture.IPadCaptureService().capture("usb")


def test_a_bad_usb_variable_is_reported_and_not_replaced(tmp_path, monkeypatch):
    """Falling back to a checkout build would hide a bundle that named a broken helper."""
    built = tmp_path / "runtime" / "bin" / "ipad_usb_capture"
    built.parent.mkdir(parents=True)
    built.write_text("#!/bin/sh\n")
    built.chmod(0o755)
    monkeypatch.setenv("CIRCUIT_MCP_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("CIRCUIT_MCP_USB_CAPTURE", str(tmp_path / "absent"))
    usb = _usb_status()
    assert usb["available"] is False
    assert "CIRCUIT_MCP_USB_CAPTURE" in usb["error"]
    assert "not built" not in usb["error"]


def test_the_app_says_why_it_wants_the_camera():
    """The helper opens the iPad as a capture device, and macOS asks the app."""
    import plistlib
    from circuit_mcp import paths
    plist = plistlib.loads((paths.REPO_ROOT / "macos" / "Resources" / "Info.plist").read_bytes())
    reason = plist["NSCameraUsageDescription"]
    assert "iPad" in reason
    assert "USB" in reason


def test_the_app_build_compiles_and_signs_the_usb_helper():
    from circuit_mcp import paths
    script = (paths.REPO_ROOT / "macos" / "build_app.sh").read_text()
    assert "native/ipad_usb_capture.swift" in script
    assert "swiftc" in script
    all_line = next(line for line in script.splitlines() if line.strip().startswith("all)"))
    assert "usb" in all_line
    assert all_line.index("usb") < all_line.index("sign")
    sign = script.split("stage_sign()", 1)[1].split("stage_dmg()", 1)[0]
    assert "ipad_usb_capture" in sign


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
