"""Supervisor and framed IPC client for the persistent UniMERNet worker."""
from __future__ import annotations

import atexit
import os
import pickle
import selectors
import signal
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

HEADER = struct.Struct("!Q")
OCR_TIMEOUT_SECONDS = 120.0
MAX_IMAGE_BYTES = 25 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[2]


class OCRWorkerError(RuntimeError):
    """The OCR worker is unavailable or returned an invalid response."""


class OCRWorkerTimeout(OCRWorkerError):
    """The OCR worker exceeded its wall-clock budget."""


def _read_before(fd: int, count: int, deadline: float) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        while remaining:
            left = deadline - time.monotonic()
            if left <= 0 or not selector.select(max(0, left)):
                raise OCRWorkerTimeout("OCR worker timed out while reading a response.")
            chunk = os.read(fd, remaining)
            if not chunk:
                raise OCRWorkerError("OCR worker closed its response pipe.")
            chunks.append(chunk)
            remaining -= len(chunk)
    return b"".join(chunks)


def _write_before(fd: int, data: bytes, deadline: float) -> None:
    view = memoryview(data)
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_WRITE)
        while view:
            left = deadline - time.monotonic()
            if left <= 0 or not selector.select(max(0, left)):
                raise OCRWorkerTimeout("OCR worker timed out while receiving a request.")
            try:
                view = view[os.write(fd, view):]
            except BrokenPipeError as exc:
                raise OCRWorkerError("OCR worker closed its request pipe.") from exc


class OCRWorker:
    """One lazily started process that keeps UniMERNet resident on Metal."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._stderr = None

    @property
    def pid(self) -> int | None:
        process = self._process
        return None if process is None else process.pid

    def _configuration(self) -> tuple[Path, Path, str]:
        python = Path(
            os.environ.get("CIRCUIT_MCP_OCR_PYTHON", ROOT / ".venv-ocr.nosync/bin/python")
        ).expanduser()
        if not python.is_absolute():
            python = ROOT / python
        # Do not resolve this symlink: a venv's python commonly points at the
        # base interpreter, and resolving it discards the venv site-packages.
        python = python.absolute()
        model = Path(
            os.environ.get("CIRCUIT_MCP_OCR_MODEL", ROOT / "models/unimernet_small")
        ).expanduser().resolve()
        device = os.environ.get("CIRCUIT_MCP_OCR_DEVICE", "auto")
        return python, model, device

    def availability(self) -> dict[str, Any]:
        python, model, device = self._configuration()
        checkpoint = list(model.glob("unimernet_*.pth")) if model.is_dir() else []
        available = python.is_file() and len(checkpoint) == 1
        return {
            "ok": available,
            "backend": "unimernet",
            "python": str(python),
            "model_dir": str(model),
            "requested_device": device,
            "process_running": self.pid is not None,
            "message": (
                "UniMERNet worker files are available."
                if available
                else "OCR is not installed. Run scripts/setup_ocr.sh."
            ),
        }

    def _start(self) -> subprocess.Popen:
        available = self.availability()
        if not available["ok"]:
            raise OCRWorkerError(available["message"])
        python, model, device = self._configuration()
        worker = Path(__file__).with_name("ocr_worker.py")
        environment = dict(os.environ)
        environment.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")
        environment.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        self._stderr = tempfile.TemporaryFile()
        self._process = subprocess.Popen(
            [str(python), str(worker), str(model), device],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            bufsize=0,
            start_new_session=True,
            env=environment,
        )
        return self._process

    def _ensure(self) -> subprocess.Popen:
        if self._process is None or self._process.poll() is not None:
            self._discard()
            return self._start()
        return self._process

    def _stderr_tail(self) -> str:
        if self._stderr is None:
            return ""
        self._stderr.seek(0, os.SEEK_END)
        size = self._stderr.tell()
        self._stderr.seek(max(0, size - 1200))
        return self._stderr.read().decode("utf-8", "replace").strip()

    def _discard(self) -> None:
        process, self._process = self._process, None
        stderr, self._stderr = self._stderr, None
        if process is not None:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except OSError:
                    pass
            process.wait()
        if stderr is not None:
            stderr.close()

    def shutdown(self) -> None:
        with self._lock:
            self._discard()

    def call(self, request: dict[str, Any], timeout: float = OCR_TIMEOUT_SECONDS) -> dict:
        if request.get("action") == "transcribe":
            png = request.get("png")
            if not isinstance(png, bytes) or not png.startswith(b"\x89PNG\r\n\x1a\n"):
                return {"ok": False, "error": "bad_image", "message": "Input is not PNG data."}
            if len(png) > MAX_IMAGE_BYTES:
                return {
                    "ok": False,
                    "error": "image_too_large",
                    "message": f"Image is {len(png)} bytes; limit is {MAX_IMAGE_BYTES}.",
                }
        payload = pickle.dumps(request, protocol=pickle.HIGHEST_PROTOCOL)
        with self._lock:
            for attempt in (0, 1):
                deadline = time.monotonic() + timeout
                try:
                    process = self._ensure()
                    _write_before(
                        process.stdin.fileno(), HEADER.pack(len(payload)) + payload, deadline
                    )
                    (size,) = HEADER.unpack(
                        _read_before(process.stdout.fileno(), HEADER.size, deadline)
                    )
                    response = pickle.loads(
                        _read_before(process.stdout.fileno(), size, deadline)
                    )
                    if not isinstance(response, dict) or "ok" not in response:
                        raise OCRWorkerError("OCR worker returned a malformed response.")
                    return response
                except OCRWorkerTimeout as exc:
                    tail = self._stderr_tail()
                    self._discard()
                    return {
                        "ok": False,
                        "error": "ocr_timeout",
                        "message": f"{exc} Worker was killed. {tail}".strip(),
                    }
                except Exception as exc:
                    tail = self._stderr_tail()
                    self._discard()
                    if attempt == 0:
                        continue
                    return {
                        "ok": False,
                        "error": "ocr_worker_error",
                        "message": f"{type(exc).__name__}: {exc}. {tail}".strip(),
                    }
        return {"ok": False, "error": "ocr_worker_error", "message": "unreachable"}


OCR_WORKER = OCRWorker()
atexit.register(OCR_WORKER.shutdown)
