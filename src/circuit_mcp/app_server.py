"""Server entry point for the Andrew's PrepPal macOS app.

    python -m circuit_mcp.app_server --data-dir <folder>

Prints exactly one protocol line on stdout: ``READY <port>`` once the server is
listening, or ``LOCKED <pid>`` when another web server holds the folder (exit
status 3). Exit status 3 means a locked folder only when that ``LOCKED`` line
was printed: uvicorn exits 3 on a startup failure of its own, so a bare 3 with
no protocol line is the server failing to start. The process leads its own
process group so the app can stop it and every child it started together.
SIGTERM shuts down gracefully, which runs the web lifespan that stops UxPlay
and Showman.
"""
from __future__ import annotations

import argparse
import faulthandler
import fcntl
import os
import signal
import socket
import sys
from pathlib import Path
from typing import TextIO

LOCK_NAME = "server.lock"
EXIT_LOCKED = 3


class DataFolderLocked(RuntimeError):
    """Another web server already holds this data folder."""

    def __init__(self, path: Path, holder: str):
        super().__init__(f"Another server is using {path.parent} (process {holder}).")
        self.path = path
        self.holder = holder


def acquire_data_lock(data_dir: Path) -> TextIO:
    """Hold an exclusive lock on ``<data_dir>/server.lock`` while the returned file stays open."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / LOCK_NAME
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.seek(0)
        holder = handle.read().strip() or "unknown"
        handle.close()
        raise DataFolderLocked(path, holder) from None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


PREFERRED_PORT = 2300


def open_listener() -> socket.socket:
    """Bind the port the browser remembers, or any free one.

    The desk layout lives in the browser's localStorage, which is keyed by
    origin. Binding port 0 gave a different origin on every launch, so every
    launch opened an empty desk and the cards came back stacked at the default
    spot. One port keeps one origin.

    A port can be held by something else -- run_ui.py serves on this one too --
    and the app starting matters more than the layout, so that falls back to an
    ephemeral port rather than refusing to start. SO_REUSEADDR is what stops the
    restart-on-save path from falling back over its own socket in TIME_WAIT.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("127.0.0.1", PREFERRED_PORT))
    except OSError:
        listener.bind(("127.0.0.1", 0))
    return listener


def _serve(listener: socket.socket) -> None:
    import uvicorn

    from . import web  # imported only now: web.DATA reads CIRCUIT_MCP_DATA_DIR at import

    port = listener.getsockname()[1]

    class ReadyServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                print(f"READY {port}", flush=True)

        def handle_exit(self, sig, frame):
            """Exit 0 on a graceful stop instead of dying from the signal.

            The first signal also kills any tool call still running. The window
            that asked for it is closing, and a check_equivalence that has
            nineteen seconds of SymPy left would otherwise hold the quit for all
            of them -- the app's grace period runs out and SIGKILLs the server,
            so every quit looks like a failed stop.

            Anything else that stalls writes its stacks to the log seven seconds
            in, so the next one is diagnosable rather than merely slow.

            uvicorn records each signal it handles and, once it has restored the
            original handlers, re-raises them -- which would leave this process
            killed by SIGTERM rather than exiting 0. Not recording the signal
            here is what stops that re-raise. The app reads the exit status to
            tell a clean stop from a crash.
            """
            if not self.should_exit:
                from .server import _WORKER

                _WORKER.abort()
                faulthandler.dump_traceback_later(7, exit=False)
            if self.should_exit and sig == signal.SIGINT:
                self.force_exit = True
            self.should_exit = True

    # access_log=False keeps stdout carrying only the protocol lines: uvicorn's access
    # handler streams to stdout, while everything else it logs goes to stderr, which the
    # app captures into the same log file.
    # Without a bound, uvicorn waits for in-flight connections forever, and a
    # client that stopped reading a download holds the stop until the app's own
    # grace period runs out and SIGKILLs the server.
    ReadyServer(uvicorn.Config(web.app, lifespan="on", log_level="info", access_log=False,
                               timeout_graceful_shutdown=3)).run(sockets=[listener])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the command center for the macOS app.")
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    os.setpgrp()
    data_dir = args.data_dir.expanduser().resolve()
    os.environ["CIRCUIT_MCP_DATA_DIR"] = str(data_dir)
    try:
        lock = acquire_data_lock(data_dir)
    except DataFolderLocked as refused:
        print(f"LOCKED {refused.holder}", flush=True)
        print(refused, file=sys.stderr, flush=True)
        return EXIT_LOCKED
    try:
        listener = open_listener()
        _serve(listener)
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
