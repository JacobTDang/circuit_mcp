"""The server entry point the macOS app runs: lock, free port, READY, graceful stop."""
from __future__ import annotations

import json
import os
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import TextIO

import pytest

from circuit_mcp import app_server, paths
from circuit_mcp.app_server import EXIT_LOCKED, LOCK_NAME, DataFolderLocked, acquire_data_lock

# The spawned servers run the web lifespan, whose shutdown stops Showman. Point
# them at a throwaway folder so they never touch a developer's .local/showman.
_SHOWMAN_DATA = tempfile.TemporaryDirectory(prefix="preppal-showman-")
ENV = {
    **os.environ,
    "PYTHONPATH": str(paths.REPO_ROOT / "src"),
    "CIRCUIT_MCP_SHOWMAN_DATA_DIR": _SHOWMAN_DATA.name,
}


def _pump(stream: TextIO) -> "queue.Queue[str]":
    """Read a stream in the background so its pipe can never fill and block the server."""
    lines: "queue.Queue[str]" = queue.Queue()

    def drain() -> None:
        for line in stream:
            lines.put(line)
        lines.put("")

    threading.Thread(target=drain, daemon=True).start()
    return lines


def _launch(data_dir: Path, stderr: int = subprocess.STDOUT) -> tuple[subprocess.Popen, "queue.Queue[str]"]:
    """Spawn a server. Pass ``stderr=subprocess.PIPE`` to keep the streams apart, then pump stderr too."""
    process = subprocess.Popen(
        [sys.executable, "-m", "circuit_mcp.app_server", "--data-dir", str(data_dir)],
        env=ENV, stdout=subprocess.PIPE, stderr=stderr, text=True,
    )
    return process, _pump(process.stdout)


def _protocol_line(lines: "queue.Queue[str]", timeout: float = 60.0, seen: list[str] | None = None) -> str:
    seen = [] if seen is None else seen
    while True:
        try:
            line = lines.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError("no READY or LOCKED within %ss; output:\n%s" % (timeout, "".join(seen))) from None
        if line == "":
            raise AssertionError("server exited before READY or LOCKED; output:\n" + "".join(seen))
        seen.append(line)
        if line.startswith(("READY ", "LOCKED ")):
            return line.strip()


def _rest_of(lines: "queue.Queue[str]", timeout: float = 20.0) -> list[str]:
    """Everything still queued on a stream, up to the end-of-file marker its pump adds."""
    rest: list[str] = []
    while True:
        try:
            line = lines.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError("stream still open %ss after the server exited; read:\n%s" % (timeout, "".join(rest))) from None
        if line == "":
            return rest
        rest.append(line)


def _stop(process: subprocess.Popen) -> int:
    process.send_signal(signal.SIGTERM)
    try:
        return process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        _discard(process)
        raise


def _discard(process: subprocess.Popen) -> None:
    """Leave no live server behind: it leads its own process group, so nothing else reaps it."""
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def test_ready_reports_a_port_that_serves_the_status_endpoint(tmp_path):
    process, lines = _launch(tmp_path / "data")
    try:
        line = _protocol_line(lines)
        assert line.startswith("READY ")
        port = int(line.split()[1])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=30) as response:
            assert json.load(response)["ok"] is True
        assert os.getpgid(process.pid) == process.pid
        assert (tmp_path / "data" / LOCK_NAME).read_text() == str(process.pid)
    finally:
        assert _stop(process) == 0


def test_a_graceful_stop_releases_the_lock(tmp_path):
    process, lines = _launch(tmp_path)
    try:
        assert _protocol_line(lines).startswith("READY ")
        assert _stop(process) == 0
        acquire_data_lock(tmp_path).close()
    finally:
        _discard(process)


def test_a_second_server_on_a_locked_folder_says_locked_and_exits_3(tmp_path):
    held = acquire_data_lock(tmp_path)
    try:
        process, lines = _launch(tmp_path)
        try:
            assert _protocol_line(lines) == f"LOCKED {os.getpid()}"
            assert process.wait(timeout=30) == EXIT_LOCKED
        finally:
            _discard(process)
    finally:
        held.close()


def test_the_lock_names_its_holder_when_refused(tmp_path):
    held = acquire_data_lock(tmp_path)
    try:
        with pytest.raises(DataFolderLocked) as refused:
            acquire_data_lock(tmp_path)
        assert refused.value.holder == str(os.getpid())
        assert refused.value.path == tmp_path / LOCK_NAME
    finally:
        held.close()


def test_run_ui_refuses_a_locked_data_folder(tmp_path):
    held = acquire_data_lock(tmp_path)
    try:
        completed = subprocess.run(
            [sys.executable, str(paths.REPO_ROOT / "run_ui.py")],
            env={**ENV, "CIRCUIT_MCP_DATA_DIR": str(tmp_path)},
            capture_output=True, text=True, timeout=60,
        )
    finally:
        held.close()
    assert completed.returncode == EXIT_LOCKED
    assert "Another server is using" in completed.stderr


def test_stdout_carries_only_the_protocol_line_while_requests_are_served(tmp_path):
    """The app parses stdout, so served traffic must never add uvicorn access lines to it."""
    process, out = _launch(tmp_path, stderr=subprocess.PIPE)
    errors = _pump(process.stderr)
    stdout_lines: list[str] = []
    try:
        line = _protocol_line(out, seen=stdout_lines)
        assert line.startswith("READY ")
        port = int(line.split()[1])
        for _ in range(3):  # uvicorn logs one access line per request whenever access_log is on
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=30) as response:
                assert json.load(response)["ok"] is True
        assert _stop(process) == 0
    finally:
        _discard(process)
    stdout_lines.extend(_rest_of(out))
    assert stdout_lines == [f"READY {port}\n"]
    assert _rest_of(errors), "uvicorn's own logging still belongs on stderr"


# --- the port the browser remembers -------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_server_binds_the_port_the_browser_remembers(monkeypatch):
    """localStorage is keyed by origin, so a new port each launch is an empty desk each launch."""
    monkeypatch.setattr(app_server, "PREFERRED_PORT", _free_port())
    listener = app_server.open_listener()
    try:
        assert listener.getsockname()[1] == app_server.PREFERRED_PORT
    finally:
        listener.close()


def test_a_taken_port_falls_back_to_any_free_one(monkeypatch):
    """Something else holding the port must not stop the app from starting."""
    taken = _free_port()
    monkeypatch.setattr(app_server, "PREFERRED_PORT", taken)
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", taken))
    holder.listen(1)
    try:
        listener = app_server.open_listener()
        try:
            assert listener.getsockname()[1] not in (0, taken)
        finally:
            listener.close()
    finally:
        holder.close()


def test_the_listener_can_rebind_after_a_restart(monkeypatch):
    """The app restarts the server whenever settings are saved; TIME_WAIT must not force a fallback."""
    monkeypatch.setattr(app_server, "PREFERRED_PORT", _free_port())
    first = app_server.open_listener()
    first.listen(1)
    first.close()
    second = app_server.open_listener()
    try:
        assert second.getsockname()[1] == app_server.PREFERRED_PORT
    finally:
        second.close()
