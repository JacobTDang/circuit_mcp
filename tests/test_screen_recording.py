"""Screen Recording permission, asked of macOS rather than guessed.

In a terminal the permission is inherited from the terminal, so this never came
up. A packaged app is its own subject: the permission is asked for once against
its bundle identifier, and until it is granted every capture fails. The desk
should say that before a button is pressed, and the refusal should name System
Settings rather than repeat what screencapture printed.
"""
from __future__ import annotations

import subprocess

import pytest

from circuit_mcp import capture, screen_recording


@pytest.fixture(autouse=True)
def forget_the_prompt(monkeypatch):
    monkeypatch.setattr(screen_recording, "_ASKED", False, raising=False)


@pytest.fixture(autouse=True)
def capture_tool_exists(monkeypatch):
    """What these test is the permission, not whether this machine is a Mac.

    Left real, every one of them short-circuits on Linux at "there is no
    screencapture here" and asserts nothing about permission at all.
    """
    monkeypatch.setattr(capture.shutil, "which", lambda command: command)


class FakeCoreGraphics:
    """Stands in for the framework: the real answer depends on this machine's TCC."""

    def __init__(self, granted: bool, grants_on_request: bool = False):
        self.granted = granted
        self.grants_on_request = grants_on_request
        self.requests = 0

    def CGPreflightScreenCaptureAccess(self) -> bool:
        return self.granted

    def CGRequestScreenCaptureAccess(self) -> bool:
        self.requests += 1
        self.granted = self.granted or self.grants_on_request
        return self.granted


def test_the_state_comes_from_the_framework_not_from_a_guess(monkeypatch):
    monkeypatch.setattr(screen_recording, "_core_graphics", lambda: FakeCoreGraphics(True))
    assert screen_recording.permission_state() == "granted"
    monkeypatch.setattr(screen_recording, "_core_graphics", lambda: FakeCoreGraphics(False))
    assert screen_recording.permission_state() == "denied"


def test_a_machine_without_the_framework_says_so_instead_of_raising(monkeypatch):
    """Linux runs this suite, and the import must not be what decides that."""
    monkeypatch.setattr(screen_recording, "_core_graphics", lambda: None)
    assert screen_recording.permission_state() == "unavailable"


def test_asking_for_access_prompts_once_and_reports_what_came_back(monkeypatch):
    framework = FakeCoreGraphics(False, grants_on_request=True)
    monkeypatch.setattr(screen_recording, "_core_graphics", lambda: framework)
    assert screen_recording.request_access() == "granted"
    assert screen_recording.request_access() == "granted"
    assert framework.requests == 1, "the student should be asked once, not once per capture"


def test_the_workspace_status_reports_the_real_permission(monkeypatch):
    monkeypatch.setattr(screen_recording, "_core_graphics", lambda: FakeCoreGraphics(False))
    status = capture.capture_status()
    assert status["permission"] == "denied"
    assert "Screen Recording" in status["message"]


def test_a_denied_capture_names_system_settings_and_never_runs_the_tool(monkeypatch):
    monkeypatch.setattr(screen_recording, "_core_graphics", lambda: FakeCoreGraphics(False))

    def never(*_: object, **__: object):
        raise AssertionError("screencapture ran without permission")

    monkeypatch.setattr(capture.subprocess, "run", never)
    with pytest.raises(capture.CaptureError, match="System Settings"):
        capture.capture_workspace(x=0, y=0, width=100, height=100)


def test_a_denied_capture_asks_for_access_first_so_the_prompt_appears(monkeypatch):
    """The one-time prompt is the only way the packaged app can ever be granted."""
    framework = FakeCoreGraphics(False)
    monkeypatch.setattr(screen_recording, "_core_graphics", lambda: framework)
    with pytest.raises(capture.CaptureError):
        capture.capture_workspace(x=0, y=0, width=100, height=100)
    assert framework.requests == 1


def test_a_refusal_never_widens_the_capture_to_the_whole_display(monkeypatch):
    """The region is the privacy boundary and no failure path may trade it away."""
    monkeypatch.setattr(screen_recording, "_core_graphics", lambda: FakeCoreGraphics(False))
    commands = []
    monkeypatch.setattr(capture.subprocess, "run",
                        lambda command, **__: commands.append(command))
    with pytest.raises(capture.CaptureError):
        capture.capture_workspace(x=10, y=20, width=30, height=40)
    assert commands == []


def test_an_unknown_permission_still_lets_the_capture_be_attempted(monkeypatch):
    """Off macOS, or on a build without the symbol, the tool itself is the answer."""
    monkeypatch.setattr(screen_recording, "_core_graphics", lambda: None)
    ran = []

    def refuse(command, **_):
        ran.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="no display")

    monkeypatch.setattr(capture.subprocess, "run", refuse)
    with pytest.raises(capture.CaptureError, match="no display"):
        capture.capture_workspace(x=0, y=0, width=10, height=10)
    assert ran, "an unknown permission is not a refusal"
