"""macOS workspace capture, without taking real screenshots in unit tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from circuit_mcp import capture, screen_recording

PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"


@pytest.fixture(autouse=True)
def allowed(monkeypatch):
    """This machine's own Screen Recording decision is not what these test.

    Left real, every one of them would pass or fail by whether the developer had
    ticked a box in System Settings.
    """
    monkeypatch.setattr(screen_recording, "permission_state",
                        lambda: screen_recording.GRANTED)
    monkeypatch.setattr(screen_recording, "request_access",
                        lambda: screen_recording.GRANTED)


def test_status_reports_the_tool_and_the_permission_without_capturing(monkeypatch):
    monkeypatch.setattr(capture.shutil, "which", lambda command: command)
    assert capture.capture_status() == {
        "ok": True,
        "platform": "macos",
        "capture_command": capture.SCREEN_CAPTURE,
        "permission": "granted",
        "message": "Screen capture is available and Screen Recording is allowed.",
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


def refusing(monkeypatch, detail: str = "could not create image from display"):
    monkeypatch.setattr(capture.shutil, "which", lambda command: command)
    monkeypatch.setattr(
        capture.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", detail),
    )


def test_a_capture_refused_for_permission_is_actionable(monkeypatch):
    refusing(monkeypatch)
    monkeypatch.setattr(screen_recording, "permission_state",
                        lambda: screen_recording.DENIED)
    with pytest.raises(capture.CaptureError) as caught:
        capture.capture_workspace(allow_full_display=True)
    assert "Screen Recording" in str(caught.value)
    assert "System Settings" in str(caught.value)


def test_a_capture_refused_for_any_other_reason_says_that_reason(monkeypatch):
    """Blaming Screen Recording for a locked screen or a display that is not
    there sends the student to a settings pane that was never the problem."""
    refusing(monkeypatch, "invalid display specified")
    with pytest.raises(capture.CaptureError) as caught:
        capture.capture_workspace(display=9, allow_full_display=True)
    assert "invalid display specified" in str(caught.value)
    assert "System Settings" not in str(caught.value)
