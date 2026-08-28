"""macOS workspace capture, without taking real screenshots in unit tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from circuit_mcp import capture

PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"


def test_status_does_not_capture_or_claim_permission(monkeypatch):
    monkeypatch.setattr(capture.shutil, "which", lambda command: command)
    status = capture.capture_status()
    assert status == {
        "ok": True,
        "platform": "macos",
        "capture_command": capture.SCREEN_CAPTURE,
        "permission": "unknown_until_capture",
        "message": (
            "Screen capture is available. macOS may ask for Screen Recording "
            "permission on the first capture."
        ),
    }


def test_capture_returns_png_hash_and_display_metadata(monkeypatch):
    monkeypatch.setattr(capture.shutil, "which", lambda command: command)

    def completed(command, **kwargs):
        Path(command[-1]).write_bytes(PNG)
        assert command[:-1] == [
            capture.SCREEN_CAPTURE, "-x", "-t", "png", "-D", "2"
        ]
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(capture.subprocess, "run", completed)
    result = capture.capture_workspace(display=2, allow_full_display=True)

    assert result["ok"] is True
    assert result["png"] == PNG
    assert result["bytes"] == len(PNG)
    assert len(result["sha256"]) == 64
    assert result["selection"] == {"kind": "display", "display": 2}


def test_region_capture_uses_all_four_coordinates(monkeypatch):
    monkeypatch.setattr(capture.shutil, "which", lambda command: command)

    def completed(command, **kwargs):
        Path(command[-1]).write_bytes(PNG)
        assert "-R" in command
        assert "10,20,800,600" in command
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(capture.subprocess, "run", completed)
    result = capture.capture_workspace(x=10, y=20, width=800, height=600)
    assert result["selection"] == {
        "kind": "region", "x": 10, "y": 20, "width": 800, "height": 600
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {"display": 0},
        {},
        {"x": 1},
        {"x": -1, "y": 0, "width": 10, "height": 10},
        {"x": 0, "y": 0, "width": 0, "height": 10},
    ],
)
def test_invalid_capture_selection_is_refused(monkeypatch, arguments):
    monkeypatch.setattr(capture.shutil, "which", lambda command: command)
    with pytest.raises(capture.CaptureError):
        capture.capture_workspace(**arguments)


def test_permission_failure_is_actionable(monkeypatch):
    monkeypatch.setattr(capture.shutil, "which", lambda command: command)
    monkeypatch.setattr(
        capture.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "", "could not create image from display"
        ),
    )

    with pytest.raises(capture.CaptureError) as caught:
        capture.capture_workspace(allow_full_display=True)
    assert "Screen Recording" in str(caught.value)
    assert "System Settings" in str(caught.value)
