"""Opt-in MATLAB Engine bridge: persistent session, evalc capture, kill-on-stuck.

Soft dependency: this module must import when ``matlab.engine`` is absent.
All Engine use is lazy. MCP tools live in ``server``; they must not route calls
through the forking symbolic worker.
"""
from __future__ import annotations

import atexit
import os
import signal
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any

ENABLE_VAR = "CIRCUIT_MCP_ENABLE_MATLAB"

MAX_CODE_CHARS = 100_000
MAX_OUTPUT_CHARS = 1_000_000
MAX_PNG_BYTES = 5_000_000
DEFAULT_TIMEOUT_S = 30.0
MIN_TIMEOUT_S = 5.0
MAX_TIMEOUT_S = 120.0
START_TIMEOUT_S = 90.0
CANCEL_GRACE_S = 2.0

_LOCK = threading.Lock()
_ENGINE: Any | None = None
_ENGINE_PID: int | None = None
_MATLAB_VERSION: str | None = None


class MatlabError(Exception):
    """Bridge refused or failed. ``kind`` is a stable error token for MCP clients."""

    _KINDS = frozenset({"disabled", "engine_missing", "start_failed", "timeout", "eval_error"})

    def __init__(self, kind: str, message: str) -> None:
        if kind not in self._KINDS:
            raise ValueError(f"unknown MatlabError kind: {kind!r}")
        self.kind = kind
        super().__init__(message)


@dataclass
class EvalResult:
    ok: bool
    output: str
    truncated: bool
    notes: list[str] = field(default_factory=list)
    figure_png: bytes | None = None


def _enabled() -> bool:
    return os.environ.get(ENABLE_VAR) == "1"


def _probe_engine_importable() -> bool:
    try:
        __import__("matlab.engine")
    except ImportError:
        return False
    return True


def _load_engine_module() -> Any:
    try:
        return __import__("matlab.engine", fromlist=["start_matlab"])
    except ImportError as exc:
        raise MatlabError(
            "engine_missing",
            "matlab.engine is not available; install matlabengine matching your MATLAB release",
        ) from exc


def status() -> dict:
    """Probe enablement and Engine importability without starting MATLAB."""
    enabled = _enabled()
    importable = _probe_engine_importable()
    alive = _ENGINE is not None
    if not enabled:
        note = f"Set {ENABLE_VAR}=1 in the MCP environment to permit MATLAB evaluation."
    elif not importable:
        note = "matlab.engine is not importable; install matlabengine matching your MATLAB release."
    elif alive:
        note = "MATLAB session is warm."
    else:
        note = "MATLAB is enabled; the Engine starts on the first matlab_eval call."
    return {
        "ok": True,
        "enabled": enabled,
        "engine_importable": importable,
        "session_alive": alive,
        "matlab_version": _MATLAB_VERSION if alive else None,
        "note": note,
    }


def _clamp_timeout(timeout_s: float | None) -> float:
    value = DEFAULT_TIMEOUT_S if timeout_s is None else float(timeout_s)
    if value < MIN_TIMEOUT_S:
        return MIN_TIMEOUT_S
    if value > MAX_TIMEOUT_S:
        return MAX_TIMEOUT_S
    return value


def _drop_engine() -> None:
    global _ENGINE, _ENGINE_PID, _MATLAB_VERSION
    _ENGINE = None
    _ENGINE_PID = None
    _MATLAB_VERSION = None


def _kill_pid(pid: int | None) -> None:
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        # The process is already gone — the only OSError that is safe to ignore.
        pass


def _wait_future(future: Any, timeout_s: float, *, kind: str, message: str) -> Any:
    try:
        return future.result(timeout=timeout_s)
    except TimeoutError as exc:
        raise MatlabError(kind, message) from exc
    except MatlabError:
        raise
    except Exception as exc:
        raise MatlabError(kind, f"{type(exc).__name__}: {exc}") from exc


def _ensure_engine() -> Any:
    global _ENGINE, _ENGINE_PID, _MATLAB_VERSION
    if _ENGINE is not None:
        return _ENGINE
    module = _load_engine_module()
    try:
        start_future = module.start_matlab(background=True)
    except Exception as exc:
        raise MatlabError("start_failed", f"{type(exc).__name__}: {exc}") from exc
    try:
        engine = _wait_future(
            start_future,
            START_TIMEOUT_S,
            kind="start_failed",
            message=f"MATLAB failed to start within {START_TIMEOUT_S:.0f}s",
        )
    except MatlabError:
        raise
    except Exception as exc:
        raise MatlabError("start_failed", f"{type(exc).__name__}: {exc}") from exc

    try:
        raw_pid = engine.feature("getpid", nargout=1)
        pid = int(float(raw_pid))
    except Exception as exc:
        try:
            engine.quit()
        except Exception:
            pass
        raise MatlabError(
            "start_failed", f"could not read MATLAB pid: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        engine.eval("set(groot,'defaultFigureVisible','off')", nargout=0)
    except Exception as exc:
        _kill_pid(pid)
        try:
            engine.quit()
        except Exception:
            pass
        raise MatlabError(
            "start_failed",
            f"could not configure headless figures: {type(exc).__name__}: {exc}",
        ) from exc

    try:
        version = str(engine.eval("version", nargout=1))
    except Exception as exc:
        _kill_pid(pid)
        try:
            engine.quit()
        except Exception:
            pass
        raise MatlabError(
            "start_failed",
            f"version probe failed: {type(exc).__name__}: {exc}",
        ) from exc

    _ENGINE = engine
    _ENGINE_PID = pid
    _MATLAB_VERSION = version
    return engine


def _handle_eval_timeout(future: Any) -> None:
    """Cancel the future; if it stays busy, kill the MATLAB process and drop state."""
    cancelled = False
    try:
        cancelled = bool(future.cancel())
    except Exception:
        cancelled = False

    if not cancelled:
        deadline = time.monotonic() + CANCEL_GRACE_S
        while time.monotonic() < deadline:
            try:
                cancelled_fn = getattr(future, "cancelled", None)
                if callable(cancelled_fn) and bool(cancelled_fn()):
                    cancelled = True
                    break
            except Exception:
                break
            try:
                if bool(future.cancel()):
                    cancelled = True
                    break
            except Exception:
                break
            time.sleep(0.05)

    if cancelled:
        return

    pid = _ENGINE_PID
    engine = _ENGINE
    _drop_engine()
    try:
        _kill_pid(pid)
    except OSError as exc:
        if engine is not None:
            try:
                engine.quit()
            except Exception:
                pass
        raise MatlabError(
            "timeout",
            f"evaluation timed out and kill failed: {type(exc).__name__}: {exc}",
        ) from exc
    if engine is not None:
        try:
            engine.quit()
        except Exception:
            pass


def _run_evalc(engine: Any, code: str, timeout_s: float) -> str:
    try:
        future = engine.evalc(code, nargout=1, background=True)
    except Exception as exc:
        raise MatlabError("eval_error", f"{type(exc).__name__}: {exc}") from exc

    try:
        output = future.result(timeout=timeout_s)
    except TimeoutError as exc:
        try:
            _handle_eval_timeout(future)
        except MatlabError:
            raise
        raise MatlabError("timeout", f"evaluation exceeded {timeout_s:.0f}s") from exc
    except MatlabError:
        raise
    except Exception as exc:
        raise MatlabError("eval_error", f"{type(exc).__name__}: {exc}") from exc

    if output is None:
        return ""
    return str(output)


def _has_current_figure(engine: Any) -> tuple[bool, str | None]:
    """Return (has_figure, note). Never call gcf. Probe failures become a note."""
    try:
        empty = engine.eval("isempty(get(groot,'CurrentFigure'))", nargout=1)
    except Exception as exc:
        return False, f"figure probe failed: {type(exc).__name__}: {exc}"
    return (not bool(empty)), None


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _export_current_figure(engine: Any) -> tuple[bytes | None, str | None]:
    """Return (png_bytes, note). note is set on skip/failure; never raises for export."""
    path: str | None = None
    data: bytes | None = None
    try:
        fd, path = tempfile.mkstemp(prefix="circuit-mcp-matlab-", suffix=".png")
        os.close(fd)
        try:
            has_export = engine.eval("exist('exportgraphics','file')", nargout=1)
        except Exception:
            has_export = 0
        # Double-quote the groot property so the file path is the only
        # single-quoted string (keeps escaping simple for the Engine).
        matlab_path = path.replace("\\", "/")
        if float(has_export or 0) != 0:
            engine.eval(
                f"exportgraphics(get(groot,\"CurrentFigure\"),'{matlab_path}')",
                nargout=0,
            )
        else:
            engine.eval(
                f"print(get(groot,\"CurrentFigure\"),'{matlab_path}','-dpng')",
                nargout=0,
            )
        data = _read_bytes(path)
    except Exception as exc:
        return None, f"figure export failed: {type(exc).__name__}: {exc}"
    finally:
        if path is not None:
            try:
                os.unlink(path)
            except OSError:
                pass

    if not data:
        return None, "figure export failed: empty read"
    if len(data) > MAX_PNG_BYTES:
        return None, f"figure PNG exceeded {MAX_PNG_BYTES} bytes and was skipped"
    return data, None


def evaluate(code: str, timeout_s: float | None = None) -> EvalResult:
    """Run MATLAB ``code`` via evalc in the persistent session."""
    if not _enabled():
        raise MatlabError("disabled", f"MATLAB is disabled; set {ENABLE_VAR}=1 explicitly")
    if not isinstance(code, str):
        raise MatlabError("eval_error", "code must be a string")
    if len(code) > MAX_CODE_CHARS:
        raise MatlabError("eval_error", f"code exceeds {MAX_CODE_CHARS} characters")

    bound = _clamp_timeout(timeout_s)
    with _LOCK:
        engine = _ensure_engine()
        output = _run_evalc(engine, code, bound)
        truncated = False
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS]
            truncated = True

        notes: list[str] = []
        figure_png: bytes | None = None
        has_figure, probe_note = _has_current_figure(engine)
        if probe_note:
            notes.append(probe_note)
        elif has_figure:
            figure_png, note = _export_current_figure(engine)
            if note:
                notes.append(note)

        return EvalResult(
            ok=True,
            output=output,
            truncated=truncated,
            notes=notes,
            figure_png=figure_png,
        )


def shutdown() -> None:
    """Best-effort quit, then kill the captured pid if needed. Idempotent."""
    with _LOCK:
        engine = _ENGINE
        pid = _ENGINE_PID
        _drop_engine()
        if engine is not None:
            try:
                engine.quit()
            except Exception:
                pass
        _kill_pid(pid)


atexit.register(shutdown)
