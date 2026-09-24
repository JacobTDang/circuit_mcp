"""What the app says when it cannot render, and how long it takes to quit.

Inside the packaged app there is no Node, no node_modules and no vendored
worker, so every visual route answers 502. The failure is honest but a button
that always errors is not: the surface has to say why before it is pressed.

The app SIGTERMs the server and SIGKILLs it ten seconds later. The web lifespan
stops UxPlay and then Showman on the way out, so whatever those two take
together has to fit inside that, or every quit looks like a crash.
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from circuit_mcp import paths
from circuit_mcp.showman import ShowmanManager

GRACE_PERIOD = 10.0
# Each stop terminates, waits, then kills. Two of them run in sequence on the way
# out, and uvicorn may already have spent three of the ten seconds closing
# connections before the lifespan runs at all.
STOP_BUDGET = 2.0


def built(root) -> None:
    """A renderer checkout that is installed and built, as `npm run build` leaves it."""
    from circuit_mcp.showman import WORKER_SCRIPT

    (root / WORKER_SCRIPT).parent.mkdir(parents=True)
    (root / "package.json").write_text("{}")
    (root / WORKER_SCRIPT).write_text("")


@pytest.fixture
def unbuilt(tmp_path) -> ShowmanManager:
    return ShowmanManager(tmp_path / "showman", port=32011, data_dir=tmp_path / "data")


def test_the_renderer_root_follows_its_own_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCUIT_MCP_SHOWMAN_ROOT", str(tmp_path / "renderer"))
    assert paths.showman_root() == (tmp_path / "renderer").resolve()
    assert ShowmanManager().root == (tmp_path / "renderer").resolve()


def test_a_checkout_still_uses_the_vendored_worker(monkeypatch):
    monkeypatch.delenv("CIRCUIT_MCP_SHOWMAN_ROOT", raising=False)
    assert paths.showman_root() == (paths.REPO_ROOT / "vendor" / "showman").resolve()


def test_status_says_in_one_sentence_why_it_cannot_render(unbuilt):
    status = unbuilt.status()
    assert status["ok"] is False
    assert "not installed" in status["unavailable"]
    assert status["root"] == str(unbuilt.root)


def test_status_says_when_node_itself_is_missing(tmp_path, monkeypatch):
    root = tmp_path / "showman"
    built(root)
    monkeypatch.setattr("circuit_mcp.showman.shutil.which", lambda _: None)
    manager = ShowmanManager(root, port=32012, data_dir=tmp_path / "data")
    assert "Node" in manager.status()["unavailable"]


def test_a_renderer_that_can_run_reports_no_reason(tmp_path, monkeypatch):
    root = tmp_path / "showman"
    built(root)
    monkeypatch.setattr("circuit_mcp.showman.shutil.which", lambda _: "/usr/bin/node")
    manager = ShowmanManager(root, port=32013, data_dir=tmp_path / "data")
    assert manager.status()["unavailable"] == ""


def deaf_process(tmp_path, name: str) -> subprocess.Popen:
    """A child that really does ignore SIGTERM by the time the test signals it.

    Without waiting for the ready file the child can still be starting Python
    when the signal arrives, die from the default handler, and let an unbounded
    stop look fast.
    """
    ready = tmp_path / f"{name}.ready"
    process = subprocess.Popen([sys.executable, "-c",
                                "import signal, sys, time, pathlib\n"
                                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                                "pathlib.Path(sys.argv[1]).write_text('x')\n"
                                "time.sleep(120)\n", str(ready)])
    deadline = time.monotonic() + 15
    while not ready.exists():
        assert time.monotonic() < deadline and process.poll() is None, "the deaf child never started"
        time.sleep(0.02)
    return process


def timed_stop(process: subprocess.Popen, stop) -> tuple[float, bool]:
    """Run one stop against a deaf child and read the child before cleaning up.

    Reading it afterwards would only prove this test's own kill worked.
    """
    try:
        started = time.monotonic()
        stop()
        return time.monotonic() - started, process.poll() is not None
    finally:
        process.kill()
        process.wait(timeout=10)


def test_stopping_a_worker_that_ignores_sigterm_is_bounded(tmp_path):
    deaf = deaf_process(tmp_path, "showman")
    manager = ShowmanManager(tmp_path, port=32014, data_dir=tmp_path / "data")
    manager.process = deaf
    elapsed, ended = timed_stop(deaf, manager.stop)
    assert elapsed < STOP_BUDGET, f"stop took {elapsed:.1f}s"
    assert ended, "the worker outlived the stop that was supposed to end it"


def test_stopping_a_receiver_that_ignores_sigterm_is_bounded(tmp_path):
    from circuit_mcp.ipad_capture import IPadCaptureService

    deaf = deaf_process(tmp_path, "uxplay")
    service = IPadCaptureService()
    service._process = deaf
    elapsed, ended = timed_stop(deaf, service.stop_airplay)
    assert elapsed < STOP_BUDGET, f"stop took {elapsed:.1f}s"
    assert ended, "the receiver outlived the stop that was supposed to end it"


def test_both_shutdown_stops_together_fit_inside_the_apps_grace_period(tmp_path, monkeypatch):
    """The lifespan runs them in sequence, so it is the sum the app has to survive.

    Both children ignore SIGTERM, which is the case that used to cost 6 s and 5 s
    -- eleven seconds against a ten second grace period, so every such quit ended
    in a SIGKILL the app reported as a crash. Three seconds of the period belong
    to uvicorn's own connection shutdown before the lifespan runs at all.
    """
    from fastapi.testclient import TestClient

    from circuit_mcp import ipad_capture, showman, web

    renderer, receiver = deaf_process(tmp_path, "render"), deaf_process(tmp_path, "receive")
    monkeypatch.setattr(showman.SHOWMAN, "process", renderer, raising=False)
    monkeypatch.setattr(ipad_capture.IPAD_CAPTURE, "_process", receiver, raising=False)
    try:
        started = time.monotonic()
        with TestClient(web.app):
            pass
        elapsed = time.monotonic() - started
        ended = renderer.poll() is not None and receiver.poll() is not None
    finally:
        for process in (renderer, receiver):
            process.kill()
            process.wait(timeout=10)
    assert elapsed < 2 * STOP_BUDGET, f"the two stops took {elapsed:.1f}s of a {GRACE_PERIOD:g}s quit"
    assert ended, "a child outlived the shutdown that was supposed to end it"


def test_the_desk_status_carries_the_reason_the_visual_surface_is_dead(monkeypatch):
    from fastapi.testclient import TestClient

    from circuit_mcp import showman, web

    monkeypatch.setattr(showman.SHOWMAN, "status",
                        lambda: {"ok": False, "unavailable": "Showman is not installed."})
    with TestClient(web.app, headers={"host": "localhost:2300"}) as client:
        body = client.get("/api/status").json()
    assert body["showman"]["unavailable"] == "Showman is not installed."
