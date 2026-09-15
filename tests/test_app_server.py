"""The server entry point the macOS app runs: lock, free port, READY, graceful stop."""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

import pytest

from circuit_mcp import paths
from circuit_mcp.app_server import EXIT_LOCKED, LOCK_NAME, DataFolderLocked, acquire_data_lock

# The spawned servers run the web lifespan, whose shutdown stops Showman. Point
# them at a throwaway folder so they never touch a developer's .local/showman.
_SHOWMAN_DATA = tempfile.TemporaryDirectory(prefix="preppal-showman-")
ENV = {
    **os.environ,
    "PYTHONPATH": str(paths.REPO_ROOT / "src"),
    "CIRCUIT_MCP_SHOWMAN_DATA_DIR": _SHOWMAN_DATA.name,
}


def _launch(data_dir: Path) -> tuple[subprocess.Popen, "queue.Queue[str]"]:
    process = subprocess.Popen(
        [sys.executable, "-m", "circuit_mcp.app_server", "--data-dir", str(data_dir)],
        env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    lines: "queue.Queue[str]" = queue.Queue()

    def pump() -> None:
        for line in process.stdout:
            lines.put(line)
        lines.put("")

    threading.Thread(target=pump, daemon=True).start()
    return process, lines


def _protocol_line(lines: "queue.Queue[str]", timeout: float = 60.0) -> str:
    seen: list[str] = []
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
