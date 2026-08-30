"""Managed localhost boundary for the pinned Showman rendering worker."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKER_SCRIPT = Path("dist") / "service" / "worker.js"
OBJECT_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,240}")


class ShowmanManager:
    """Own one Showman worker: spawn it, prove it is ours, and report what it can do.

    A worker is a separate process with its own code and its own environment.
    Neither can be inferred from this process, so both are verified explicitly:
    a build fingerprint pins the code, and the authoring mode recorded at spawn
    pins the capability. A healthy stranger on the port is refused, never adopted.
    """

    def __init__(self, root: Path | None = None, port: int = 2301,
                 data_dir: Path | None = None):
        self.root = Path(root) if root else PROJECT_ROOT / "vendor" / "showman"
        self.port = port
        self.data_dir = Path(data_dir) if data_dir else PROJECT_ROOT / ".local" / "showman"
        self.process: subprocess.Popen[bytes] | None = None
        self._lock = threading.RLock()
        self._last_error = ""

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _health(self, timeout: float = .4) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/healthz", timeout=timeout) as response:
                return response.status == 200 and json.load(response).get("ok") is True
        except (OSError, ValueError):
            return False

    # -- identity -------------------------------------------------------------

    def build_fingerprint(self) -> str:
        """Hash the built worker so a rebuilt or reverted checkout is a new identity."""
        if not (self.root / WORKER_SCRIPT).is_file():
            return ""
        dist = self.root / "dist"
        digest = hashlib.sha256()
        for path in sorted(p for p in dist.rglob("*") if p.is_file()):
            stat = path.stat()
            digest.update(f"{path.relative_to(dist)}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
        return digest.hexdigest()

    @staticmethod
    def _authoring_mode(env: Mapping[str, str]) -> str:
        """Mirror Showman's createDefaultAuthor precedence: OpenRouter > Anthropic > offline."""
        if env.get("OPENROUTER_API_KEY"):
            return "openrouter"
        if env.get("ANTHROPIC_API_KEY"):
            return "anthropic"
        return "offline"

    @property
    def _identity_path(self) -> Path:
        return self.data_dir / f"worker-{self.port}.json"

    def _read_identity(self) -> dict[str, Any] | None:
        try:
            identity = json.loads(self._identity_path.read_text())
        except (OSError, ValueError):
            return None
        return identity if isinstance(identity, dict) else None

    def _write_identity(self, process: Any, fingerprint: str, authoring: str | None = None) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._identity_path.write_text(json.dumps({
            "pid": process.pid, "port": self.port, "fingerprint": fingerprint,
            "authoring": authoring or self._authoring_mode(os.environ),
            "started_at": time.time(),
        }))

    @staticmethod
    def _process_alive(pid: Any) -> bool:
        if not isinstance(pid, int) or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _is_our_worker(self, pid: Any) -> bool:
        """Confirm a recorded pid still runs our worker; pids are reused, so alive is not enough."""
        if not self._process_alive(pid):
            return False
        try:
            listing = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                                     capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return False
        return WORKER_SCRIPT.as_posix() in listing.stdout

    def _verified_identity(self) -> dict[str, Any] | None:
        """Return the identity of the running worker only when it is provably ours and current."""
        identity = self._read_identity()
        if not identity or identity.get("port") != self.port:
            return None
        if identity.get("fingerprint") != self.build_fingerprint():
            return None
        if not self._process_alive(identity.get("pid")):
            return None
        return identity

    # -- lifecycle ------------------------------------------------------------

    def _terminate(self, pid: int) -> None:
        try:
            os.kill(pid, 15)
        except (ProcessLookupError, PermissionError, OSError):
            return

    def _replace_unverified_worker(self) -> bool:
        """Stop a stale worker we can prove is ours. Refuse anything else."""
        identity = self._read_identity() or {}
        pid = identity.get("pid")
        if identity.get("port") == self.port and self._is_our_worker(pid):
            self._terminate(pid)
            self._identity_path.unlink(missing_ok=True)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if not self._health():
                    return True
                time.sleep(.1)
            self._last_error = (
                f"a stale Showman worker (pid {pid}) on port {self.port} did not stop")
            return False
        self._last_error = (
            f"port {self.port} is already served by a process this manager did not start; "
            "stop it and retry so the pinned build is the one that runs")
        return False

    def start(self, timeout: float = 12) -> dict[str, Any]:
        with self._lock:
            if self._health():
                if self._verified_identity():
                    return self.status()
                if not self._replace_unverified_worker():
                    return self.status()
            if not (self.root / WORKER_SCRIPT).is_file():
                self._last_error = "Showman is not built; run npm ci && npm run build in vendor/showman"
                return self.status()
            fingerprint = self.build_fingerprint()
            env = {**os.environ, "PORT": str(self.port), "SHOWMAN_HOST": "127.0.0.1",
                   "SHOWMAN_DATA_DIR": str(self.data_dir)}
            self.data_dir.mkdir(parents=True, exist_ok=True)
            process = subprocess.Popen(
                ["node", str(WORKER_SCRIPT)], cwd=self.root, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            )
            self.process = process
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self._health():
                    self._write_identity(process, fingerprint, self._authoring_mode(env))
                    self._last_error = ""
                    return self.status()
                if process.poll() is not None:
                    self._last_error = f"Showman exited with code {process.returncode}"
                    return self.status()
                time.sleep(.1)
            self._last_error = f"Showman did not become healthy within {timeout:g}s"
            return self.status()

    def restart(self, timeout: float = 12) -> dict[str, Any]:
        self.stop()
        return self.start(timeout)

    def stop(self) -> None:
        """Stop only a worker this manager started. Adopting sessions leave it running,
        and a stale worker is reaped by start() instead, so an unrelated process
        importing this module can never signal a live worker."""
        with self._lock:
            process, self.process = self.process, None
            if process is not None:
                self._identity_path.unlink(missing_ok=True)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def status(self) -> dict[str, Any]:
        running = self._health()
        identity = self._verified_identity() if running else None
        return {
            "ok": running and identity is not None,
            "running": running,
            "port": self.port,
            "installed": (self.root / "package.json").is_file(),
            "built": bool(self.build_fingerprint()),
            "worker_verified": identity is not None,
            "authoring": identity.get("authoring", "unknown") if identity else "unknown",
            "key_available": self._authoring_mode(os.environ) != "offline",
            "error": "" if (running and identity) else self._last_error,
        }

    def request_json(self, path: str, payload: dict[str, Any], timeout: float = 30) -> tuple[int, dict[str, Any]]:
        """Send bounded JSON to one fixed worker capability path."""
        if path not in {"/author", "/validate", "/assemble", "/build", "/generate", "/render"}:
            raise ValueError("unsupported Showman capability")
        if not self.start().get("ok"):
            raise RuntimeError(self._last_error or "Showman is unavailable")
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        if len(encoded) > 2 * 1024 * 1024:
            raise ValueError("Showman request exceeds 2 MiB")
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=encoded,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            try: body = json.load(exc)
            except (ValueError, OSError): body = {"error": "showman_request_failed"}
            return exc.code, body

    def object_bytes(self, key: str, timeout: float = 30) -> tuple[bytes, str]:
        """Read a content-addressed artifact; keys never escape Showman's object namespace."""
        if not OBJECT_KEY.fullmatch(key) or ".." in key.split("/"):
            raise ValueError("invalid Showman object key")
        if not self.start().get("ok"): raise RuntimeError(self._last_error or "Showman is unavailable")
        try:
            with urllib.request.urlopen(f"{self.base_url}/objects/{key}", timeout=timeout) as response:
                data = response.read(256 * 1024 * 1024 + 1)
                if len(data) > 256 * 1024 * 1024: raise ValueError("artifact exceeds 256 MiB")
                return data, response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Showman object failed with status {exc.code}") from exc

    def preview(self, spec: dict[str, Any], frame: int = 0, timeout: float = 30) -> bytes:
        """Render one bounded local preview frame without accepting an upstream URL."""
        if frame < 0 or frame > 1_000_000:
            raise ValueError("frame is outside the supported range")
        if not self.start().get("ok"):
            raise RuntimeError(self._last_error or "Showman is unavailable")
        encoded = json.dumps({"spec": spec, "frame": frame}, separators=(",", ":")).encode()
        if len(encoded) > 2 * 1024 * 1024:
            raise ValueError("Showman request exceeds 2 MiB")
        request = urllib.request.Request(
            f"{self.base_url}/preview", data=encoded,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read(16 * 1024 * 1024 + 1)
                if len(data) > 16 * 1024 * 1024: raise ValueError("preview exceeds 16 MiB")
                return data
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Showman preview failed with status {exc.code}") from exc


SHOWMAN = ShowmanManager()
