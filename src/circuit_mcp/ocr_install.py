"""Install the handwriting recogniser without leaving the app.

``scripts/setup_ocr.sh`` does this in a terminal with ``uv``. The packaged app
has neither: no terminal in the story, no uv in the bundle, and a server PATH
bounded to ``/usr/bin:/bin:/usr/sbin:/sbin`` on purpose, so it could not reach
one if it were installed. The running interpreter's own ``venv`` and ``pip``
are what is actually available, and they are enough.

Two properties make this safe to offer from a button.

*Nothing moves into place until every step has succeeded.* Each step writes into
a staging folder beside the destination, and only a complete run renames them
in. A failure halfway therefore leaves the desk exactly as it was rather than
leaving ``ocr_status`` describing an environment with no model in it.

*Whatever a failure leaves behind is removed and named.* Roughly two gigabytes
of half-written virtualenv is not something to find out about later, so the
cleanup reports the paths it deleted instead of tidying up silently.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import paths

MODEL_REPOSITORY = "wanderkid/unimernet_small"
PACKAGE = "unimernet==0.2.3"
# The checkpoint, measured. The packages are roughly another 1.2 GB on top, which
# the offer says in words rather than pretending to a figure that moves with pip.
CHECKPOINT_BYTES = 810_284_404

IDLE, RUNNING, DONE, FAILED, CANCELLED = "idle", "running", "done", "failed", "cancelled"


class OCRInstallError(RuntimeError):
    """The install cannot be started as asked."""


@dataclass(frozen=True)
class Step:
    label: str
    detail: str
    argv: list[str]


def human_size(count: int) -> str:
    megabytes = count / 1_000_000
    return f"{megabytes / 1000:.1f} GB" if megabytes >= 1000 else f"{megabytes:.0f} MB"


class OCRInstaller:
    """One install at a time, reported while it runs."""

    def __init__(self, run: Callable[[Step, Path], int] | None = None):
        self._run = run or self._run_step
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state = IDLE
        self._step = ""
        self._detail = ""
        self._removed: list[str] = []
        self._process: subprocess.Popen | None = None
        self.cancelled = False

    # -- where it goes ---------------------------------------------------------

    @property
    def venv_dir(self) -> Path:
        """``<...>/venv``, from the interpreter path the rest of the app already reads."""
        return paths.ocr_python().parent.parent

    @property
    def model_dir(self) -> Path:
        return paths.ocr_model()

    def _staging(self) -> Path:
        return self.venv_dir.parent / ".installing"

    def plan(self, staging: Path) -> list[Step]:
        venv = staging / "venv"
        return [
            Step("environment", "Creating the Python environment",
                 # --copies, not symlinks: the base interpreter lives inside the app
                 # bundle, and a symlink into it breaks the moment the app is replaced.
                 [sys.executable, "-m", "venv", "--copies", str(venv)]),
            Step("packages", "Downloading the recogniser's Python packages",
                 [str(venv / "bin" / "python"), "-m", "pip", "install", "--quiet", PACKAGE]),
            Step("model", f"Downloading the {human_size(CHECKPOINT_BYTES)} model",
                 [str(venv / "bin" / "hf"), "download", MODEL_REPOSITORY,
                  "--local-dir", str(staging / "model")]),
        ]

    # -- reporting -------------------------------------------------------------

    def state(self) -> dict:
        from .ocr_client import OCRWorker

        return {
            "state": self._state,
            "step": self._step,
            "detail": self._detail,
            "removed": list(self._removed),
            "installed": OCRWorker().availability()["ok"],
            "download_bytes": CHECKPOINT_BYTES,
            "download_size": human_size(CHECKPOINT_BYTES),
            "download_note": (
                f"About {human_size(CHECKPOINT_BYTES)} for the model, and roughly 1.2 GB of "
                f"Python packages with it. Both go in {self.venv_dir.parent}."
            ),
        }

    # -- running ---------------------------------------------------------------

    def start(self) -> dict:
        from .ocr_client import OCRWorker

        with self._lock:
            if self._state == RUNNING:
                raise OCRInstallError("An install is already running.")
            if OCRWorker().availability()["ok"]:
                raise OCRInstallError("The recogniser is already installed.")
            self.cancelled = False
            self._removed = []
            self._state = RUNNING
            self._step = ""
            self._detail = "Starting"
            self._thread = threading.Thread(target=self._install, daemon=True)
            self._thread.start()
        return self.state()

    def cancel(self) -> dict:
        self.cancelled = True
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
        return self.state()

    def _run_step(self, step: Step, staging: Path) -> int:
        self._process = subprocess.Popen(step.argv, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, text=True)
        try:
            _, error = self._process.communicate()
        finally:
            returncode, self._process = self._process.returncode, None
        if returncode != 0 and not self.cancelled:
            self._detail = (error or "").strip()[-400:] or f"{step.label} failed"
        return returncode

    def _install(self) -> None:
        staging = self._staging()
        self._discard(staging)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            for step in self.plan(staging):
                if self.cancelled:
                    return self._stop(CANCELLED, staging)
                self._step, self._detail = step.label, step.detail
                if self._run(step, staging) != 0:
                    return self._stop(CANCELLED if self.cancelled else FAILED, staging)
            if self.cancelled:
                return self._stop(CANCELLED, staging)
            self._land(staging)
        except OSError as failure:
            self._detail = str(failure)
            return self._stop(FAILED, staging)
        self._step, self._detail, self._state = "", "Installed", DONE

    def _land(self, staging: Path) -> None:
        """Rename both results in. Only reached when every step succeeded."""
        self._step, self._detail = "install", "Putting it in place"
        for source, target in ((staging / "venv", self.venv_dir),
                               (staging / "model", self.model_dir)):
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.rmtree(target)
            source.replace(target)
        self._discard(staging)

    def _stop(self, state: str, staging: Path) -> None:
        self._discard(staging, record=True)
        self._step = ""
        self._state = state
        if state == CANCELLED:
            self._detail = "Cancelled"

    def _discard(self, staging: Path, record: bool = False) -> None:
        """Remove the staging folder. ``record`` when it held work the student lost.

        A successful run also clears it, and reporting that as something removed
        would put a deletion notice on a screen where nothing went wrong.
        """
        if not staging.exists():
            return
        shutil.rmtree(staging, ignore_errors=True)
        if record:
            self._removed.append(str(staging))


OCR_INSTALLER = OCRInstaller()
