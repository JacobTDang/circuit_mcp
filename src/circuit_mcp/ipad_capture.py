"""Managed local iPadOS screen ingestion over AirPlay or USB-C."""
from __future__ import annotations

import hashlib
import atexit
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".local" / "runtime"
UXPLAY = RUNTIME / "uxplay" / "bin" / "uxplay"
WINDOW_INFO = RUNTIME / "bin" / "window_info"
USB_CAPTURE = RUNTIME / "bin" / "ipad_usb_capture"
SCREEN_CAPTURE = Path("/usr/sbin/screencapture")
FFMPEG = Path(shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg")
MAX_FRAME_BYTES = 25 * 1024 * 1024


class IPadCaptureError(RuntimeError):
    pass


class IPadCaptureService:
    def __init__(self) -> None:
        self._owner_pid = os.getpid()
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._pin: str | None = None
        self._started_at: float | None = None
        self._client_connected = False
        self._logs: deque[str] = deque(maxlen=80)
        self._usb_lock = threading.Lock()
        self._usb_cached: tuple[list[dict], str | None] = ([], None)
        self._usb_cached_at = 0.0
        self._usb_refreshing = False
        self._stream_dir = RUNTIME / "ipad"
        self._stream_base = self._stream_dir / "airplay-frame"
        self._stream_file = self._stream_dir / "airplay-frame.h264"
        self._identity_key = self._stream_dir / "receiver.pem"
        self._registration = self._stream_dir / "receiver.register"
        self._stream_last_size = 0
        self._stream_active_at = 0.0
        self._frame_lock = threading.Lock()
        self._frame_cached: dict | None = None
        self._frame_cached_at = 0.0

    def _clear_streams(self) -> None:
        for path in self._stream_dir.glob("airplay-frame*.h264"):
            path.unlink(missing_ok=True)

    def _latest_stream(self) -> Path | None:
        """Return the newest visible dump, including reconnect rotations."""
        candidates = [path for path in self._stream_dir.glob("airplay-frame*.h264")
                      if path.is_file()]
        if self._stream_file.is_file() and self._stream_file not in candidates:
            candidates.append(self._stream_file)
        try:
            return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None
        except OSError:
            return None

    def _read_logs(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            with self._lock:
                self._logs.append(line.rstrip()[:1000])
                lowered = line.lower()
                if "connection request from" in lowered:
                    self._client_connected = True
                elif "lost connection with client" in lowered or "connection closed on socket" in lowered:
                    self._client_connected = False

    def start_airplay(self) -> dict:
        with self._lock:
            if self._process and self._process.poll() is None:
                return self.status()
            if not UXPLAY.is_file():
                raise IPadCaptureError("UxPlay is not built; run scripts/setup_ipad_capture.sh")
            self._stream_dir.mkdir(parents=True, exist_ok=True)
            self._clear_streams()
            self._stream_last_size = 0
            self._stream_active_at = 0.0
            self._frame_cached = None
            self._pin = os.environ.get("CIRCUIT_MCP_AIRPLAY_PIN") or f"{secrets.randbelow(10000):04d}"
            preview_size = os.environ.get("CIRCUIT_MCP_AIRPLAY_SIZE", "800x600@30")
            if not re.fullmatch(r"[1-9]\d{2,3}x[1-9]\d{2,3}@[1-9]\d?", preview_size):
                raise IPadCaptureError("CIRCUIT_MCP_AIRPLAY_SIZE must look like 800x600@30")
            command = [str(UXPLAY), "-n", "EE2300 Capture", "-nh", "-pin", self._pin,
                       "-m", "02:ee:23:00:00:01", "-key", str(self._identity_key),
                       "-reg", str(self._registration), "-nohold",
                       "-s", preview_size,
                       "-vsync", "no", "-vs", "fakesink",
                       "-vdmp", "100000000", str(self._stream_base),
                       "-as", "0", "-reset", "0", "-nofreeze"]
            command.extend(["-d", "1"])
            environment = os.environ.copy()
            environment.setdefault("GST_DEBUG", "1")
            self._process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=environment,
            )
            self._started_at = time.time()
            self._client_connected = False
            self._logs.clear()
            threading.Thread(target=self._read_logs, args=(self._process,), daemon=True).start()
        time.sleep(0.25)
        if self._process.poll() is not None:
            raise IPadCaptureError("UxPlay exited during startup: " + " | ".join(self._logs)[-1000:])
        return self.status()

    def stop_airplay(self) -> dict:
        with self._lock:
            process = self._process
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            self._process = None
            self._pin = None
            self._started_at = None
            self._client_connected = False
            self._clear_streams()
            self._stream_last_size = 0
            self._stream_active_at = 0.0
            self._frame_cached = None
        return self.status()

    def close(self) -> None:
        """Clean up only in the process that owns the managed subprocess."""
        if os.getpid() == self._owner_pid:
            self.stop_airplay()

    def _usb_devices(self) -> tuple[list[dict], str | None]:
        with self._usb_lock:
            now = time.monotonic()
            if now - self._usb_cached_at < 5:
                return self._usb_cached
            result = self._discover_usb_devices()
            self._usb_cached = result
            self._usb_cached_at = time.monotonic()
            return result

    def _refresh_usb_async(self) -> tuple[list[dict], str | None]:
        """Return cached USB state immediately and refresh it off-request."""
        with self._usb_lock:
            cached = self._usb_cached
            stale = time.monotonic() - self._usb_cached_at >= 5
            if not stale or self._usb_refreshing:
                return cached
            self._usb_refreshing = True

        def refresh() -> None:
            result = self._discover_usb_devices()
            with self._usb_lock:
                self._usb_cached = result
                self._usb_cached_at = time.monotonic()
                self._usb_refreshing = False

        threading.Thread(target=refresh, daemon=True).start()
        return cached

    def _discover_usb_devices(self) -> tuple[list[dict], str | None]:
        if not USB_CAPTURE.is_file():
            return [], "USB helper is not built"
        try:
            result = subprocess.run([str(USB_CAPTURE), "--list"], capture_output=True,
                                    text=True, timeout=8, check=False)
            if result.returncode:
                return [], result.stderr.strip() or "USB discovery failed"
            return json.loads(result.stdout), None
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            return [], str(exc)

    def _windows(self) -> list[dict]:
        if not WINDOW_INFO.is_file():
            return []
        result = subprocess.run([str(WINDOW_INFO), "uxplay"], capture_output=True,
                                text=True, timeout=5, check=False)
        if result.returncode:
            return []
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

    def status(self) -> dict:
        with self._lock:
            running = bool(self._process and self._process.poll() is None)
            pid = self._process.pid if running and self._process else None
            pin = self._pin if running else None
            started = self._started_at if running else None
            logs = list(self._logs)[-8:]
            connected = running and self._client_connected
        stream_file = self._latest_stream()
        stream_ready = running and bool(stream_file and stream_file.stat().st_size > 8)
        if stream_ready:
            stream_size = stream_file.stat().st_size
            with self._lock:
                if stream_size != self._stream_last_size:
                    self._stream_last_size = stream_size
                    self._stream_active_at = time.monotonic()
                # UxPlay's headless dump can be authoritative even when its
                # stdout omits the usual connection-request diagnostic.
                connected = connected or time.monotonic() - self._stream_active_at < 12
        usb, usb_error = self._refresh_usb_async()
        return {
            "ok": UXPLAY.is_file() or USB_CAPTURE.is_file(),
            "airplay": {"available": UXPLAY.is_file(), "running": running,
                        "connected": connected, "stream_ready": stream_ready,
                        "headless": True,
                        "receiver": "EE2300 Capture",
                        "pin": pin, "pid": pid, "started_at": started, "logs": logs},
            "usb": {"available": USB_CAPTURE.is_file(), "connected": bool(usb),
                    "devices": usb, "error": usb_error},
            "active_source": "airplay" if connected and stream_ready else ("usb" if usb else None),
        }

    @staticmethod
    def _result(png: bytes, source: str, detail: dict) -> dict:
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise IPadCaptureError("Capture backend returned a non-PNG frame")
        if len(png) > MAX_FRAME_BYTES:
            raise IPadCaptureError(f"Frame exceeds {MAX_FRAME_BYTES} bytes")
        return {"ok": True, "source": source, "mime_type": "image/png",
                "bytes": len(png), "sha256": hashlib.sha256(png).hexdigest(),
                "captured_at": time.time(), **detail, "png": png}

    def capture(self, source: str = "auto") -> dict:
        if source not in {"auto", "airplay", "usb"}:
            raise IPadCaptureError("source must be auto, airplay, or usb")
        with self._lock:
            airplay_connected = self._client_connected
            airplay_running = bool(self._process and self._process.poll() is None)
        stream_file = self._latest_stream()
        stream_ready = bool(stream_file and stream_file.stat().st_size > 8)
        if source in {"auto", "airplay"} and (airplay_connected or (airplay_running and stream_ready)):
            return self._capture_airplay_frame(stream_file)
        if source in {"auto", "usb"}:
            if not USB_CAPTURE.is_file():
                raise IPadCaptureError("USB capture helper is not built")
            with tempfile.TemporaryDirectory(prefix="ipad-usb-") as directory:
                output = Path(directory) / "frame.png"
                result = subprocess.run([str(USB_CAPTURE), "--output", str(output)],
                                        capture_output=True, text=True, timeout=15, check=False)
                if result.returncode == 0 and output.is_file():
                    return self._result(output.read_bytes(), "usb", {})
                detail = result.stderr.strip() or "No USB iPad frame is available"
                raise IPadCaptureError(detail)
        raise IPadCaptureError("No active AirPlay mirror; start mirroring from iPad Control Center")

    def _capture_airplay_frame(self, stream_file: Path | None = None) -> dict:
        """Decode one recent frame, sharing it across fast browser pollers."""
        with self._frame_lock:
            if self._frame_cached and time.monotonic() - self._frame_cached_at < 0.45:
                return dict(self._frame_cached)
            with tempfile.TemporaryDirectory(prefix="ipad-airplay-") as directory:
                stream = Path(directory) / "frame.h264"
                output = Path(directory) / "frame.png"
                source = stream_file or self._latest_stream()
                if source is None:
                    raise IPadCaptureError("AirPlay frame is not ready yet")
                shutil.copyfile(source, stream)
                result = subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error",
                                         "-i", str(stream), "-fps_mode", "passthrough",
                                         "-update", "1", "-y", str(output)],
                                        capture_output=True, text=True,
                                        timeout=10, check=False)
                if result.returncode == 0 and output.is_file():
                    frame = self._result(output.read_bytes(), "airplay", {"headless": True})
                    self._frame_cached = frame
                    self._frame_cached_at = time.monotonic()
                    return dict(frame)
                raise IPadCaptureError(result.stderr.strip() or "AirPlay frame is not ready yet")


IPAD_CAPTURE = IPadCaptureService()
atexit.register(IPAD_CAPTURE.close)
