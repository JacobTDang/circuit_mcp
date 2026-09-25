"""run_ui.py serves on the port it is given, answers /healthz, and stops promptly on SIGTERM.

That is the contract linkC relies on to open the command center in a project tab: linkC picks
the port, polls the health path, and sends SIGTERM when the tab closes.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import run_ui

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_port_comes_from_the_command_line():
    assert run_ui.parse_args(["--port", "4321"]).port == 4321
    assert run_ui.parse_args([]).port == 2300


def test_it_serves_the_given_port_and_stops_on_sigterm(tmp_path):
    port = _free_port()
    env = {**os.environ, "CIRCUIT_MCP_DATA_DIR": str(tmp_path / "command_center")}
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "run_ui.py"), "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                    assert response.status == 200
                    break
            except (urllib.error.URLError, ConnectionError):
                assert process.poll() is None, "the server exited before it answered"
                assert time.monotonic() < deadline, "the server never answered /healthz"
                time.sleep(0.25)
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
