"""Managed localhost boundary for the pinned Showman rendering worker."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class ShowmanManager:
    def __init__(self, root: Path | None = None, port: int = 2301):
        self.root = root or Path(__file__).resolve().parents[2] / "vendor" / "showman"
        self.port = port
        self.process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._last_error = ""

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _health(self, timeout: float = .4) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/healthz", timeout=timeout) as response:
                return response.status == 200 and json.load(response).get("ok") is True
        except (OSError, ValueError, urllib.error.URLError):
            return False

    def start(self, timeout: float = 12) -> dict[str, Any]:
        with self._lock:
            if self._health(): return self.status()
            if not (self.root / "dist" / "service" / "worker.js").is_file():
                self._last_error = "Showman is not built; run npm ci && npm run build in vendor/showman"
                return self.status()
            env = {**os.environ, "PORT": str(self.port), "SHOWMAN_DATA_DIR": str(self.root.parent.parent / ".local" / "showman")}
            self.process = subprocess.Popen(
                ["node", "dist/service/worker.js"], cwd=self.root, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._health(): return self.status()
            if self.process and self.process.poll() is not None:
                self._last_error = f"Showman exited with code {self.process.returncode}"
                break
            time.sleep(.1)
        return self.status()

    def stop(self) -> None:
        with self._lock:
            process, self.process = self.process, None
        if process and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=3)

    def status(self) -> dict[str, Any]:
        running = self._health()
        return {"ok": running, "running": running, "port": self.port,
                "installed": (self.root / "package.json").is_file(),
                "built": (self.root / "dist" / "service" / "worker.js").is_file(),
                "openrouter_configured": bool(os.environ.get("OPENROUTER_API_KEY")),
                "error": "" if running else self._last_error}


SHOWMAN = ShowmanManager()
