"""MATLAB bridge: opt-in Engine session, evalc capture, kill-on-stuck-timeout."""
from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path

import pytest

from circuit_mcp import matlab_bridge as bridge


# --- fake matlab.engine -------------------------------------------------------

class FakeFuture:
    def __init__(self, value=None, *, delay=0.0, error=None, cancel_ok=True):
        self._value = value
        self._delay = delay
        self._error = error
        self._cancel_ok = cancel_ok
        self._cancelled = False
        self._started = time.monotonic()
        self.cancel_calls = 0

    def result(self, timeout=None):
        if self._cancelled:
            raise TimeoutError("future was cancelled")
        waited = time.monotonic() - self._started
        if timeout is not None and self._delay > timeout:
            time.sleep(min(0.01, max(timeout, 0)))
            raise TimeoutError("MATLAB future timed out")
        remaining = self._delay - waited
        if remaining > 0:
            time.sleep(remaining)
        if self._error is not None:
            raise self._error
        return self._value

    def cancel(self):
        self.cancel_calls += 1
        if self._cancel_ok:
            self._cancelled = True
            return True
        return False


class FakeEngine:
    def __init__(self, *, pid=4242, version="R2024b", evalc_impl=None, export_impl=None):
        self.pid = pid
        self.version = version
        self.quit_calls = 0
        self.calls: list[tuple] = []
        self._evalc_impl = evalc_impl
        self._export_impl = export_impl
        self._visible_set = False
        self.current_figure = None
        self.exportgraphics_available = True

    def feature(self, name, nargout=1):
        assert name == "getpid"
        return float(self.pid)

    def eval(self, code, nargout=0, background=False):
        self.calls.append(("eval", code, nargout, background))
        if "defaultFigureVisible" in code:
            self._visible_set = True
            return None if not background else FakeFuture(None)
        if code.strip() == "version":
            return self.version if not background else FakeFuture(self.version)
        compact = code.replace(" ", "")
        if "exist('exportgraphics'" in compact or 'exist("exportgraphics"' in compact:
            val = 2.0 if self.exportgraphics_available else 0.0
            return val if not background else FakeFuture(val)
        if code.startswith("exportgraphics(") or code.startswith("print("):
            path = code.split("'")[1] if "'" in code else None
            if self._export_impl:
                self._export_impl(path, code)
            elif path:
                Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 8)
            return None if not background else FakeFuture(None)
        if "CurrentFigure" in code:
            empty = self.current_figure is None
            value = 1.0 if empty else 0.0
            return value if not background else FakeFuture(value)
        return None if not background else FakeFuture(None)

    def evalc(self, code, nargout=1, background=False):
        self.calls.append(("evalc", code, nargout, background))
        if self._evalc_impl is not None:
            value = self._evalc_impl(code)
        else:
            value = f"out:{code}"
        if isinstance(value, FakeFuture):
            return value
        if background:
            return FakeFuture(value)
        return value

    def quit(self):
        self.quit_calls += 1


def _install_fake_engine(monkeypatch, start_future):
    engine_mod = types.ModuleType("matlab.engine")
    matlab_mod = types.ModuleType("matlab")
    matlab_mod.engine = engine_mod
    engine_mod.start_matlab = lambda background=False: start_future
    monkeypatch.setitem(sys.modules, "matlab", matlab_mod)
    monkeypatch.setitem(sys.modules, "matlab.engine", engine_mod)
    return engine_mod


@pytest.fixture(autouse=True)
def _reset_bridge(monkeypatch):
    monkeypatch.delenv(bridge.ENABLE_VAR, raising=False)
    bridge.shutdown()
    yield
    bridge.shutdown()


# --- status / enablement ------------------------------------------------------

def test_status_is_disabled_by_default_and_never_starts_matlab(monkeypatch):
    starts = []

    def boom(background=False):
        starts.append(True)
        raise AssertionError("status must not start MATLAB")

    engine_mod = types.ModuleType("matlab.engine")
    matlab_mod = types.ModuleType("matlab")
    matlab_mod.engine = engine_mod
    engine_mod.start_matlab = boom
    monkeypatch.setitem(sys.modules, "matlab", matlab_mod)
    monkeypatch.setitem(sys.modules, "matlab.engine", engine_mod)

    result = bridge.status()
    assert result["ok"] is True
    assert result["enabled"] is False
    assert result["engine_importable"] is True
    assert result["session_alive"] is False
    assert result["matlab_version"] is None
    assert bridge.ENABLE_VAR in result["note"]
    assert starts == []


def test_status_reports_engine_missing_when_import_fails(monkeypatch):
    sys.modules.pop("matlab.engine", None)
    sys.modules.pop("matlab", None)
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name in {"matlab", "matlab.engine"}:
            raise ImportError("no matlab")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    result = bridge.status()
    assert result["engine_importable"] is False
    assert result["enabled"] is False
    assert result["note"]


def test_evaluate_refuses_when_disabled():
    with pytest.raises(bridge.MatlabError) as raised:
        bridge.evaluate("1+1")
    assert raised.value.kind == "disabled"


def test_evaluate_refuses_when_engine_missing(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")
    sys.modules.pop("matlab.engine", None)
    sys.modules.pop("matlab", None)
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name in {"matlab", "matlab.engine"}:
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    with pytest.raises(bridge.MatlabError) as raised:
        bridge.evaluate("1+1")
    assert raised.value.kind == "engine_missing"


def test_evaluate_maps_start_failure(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")
    future = FakeFuture(error=RuntimeError("license checkout failed"))
    _install_fake_engine(monkeypatch, future)
    with pytest.raises(bridge.MatlabError) as raised:
        bridge.evaluate("1")
    assert raised.value.kind == "start_failed"


# --- eval path ----------------------------------------------------------------

def test_evaluate_returns_evalc_output_and_truncates(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")
    eng = FakeEngine(evalc_impl=lambda code: "x" * 1_000_001)
    _install_fake_engine(monkeypatch, FakeFuture(eng))

    result = bridge.evaluate("disp(1)", timeout_s=10)
    assert result.ok is True
    assert result.truncated is True
    assert len(result.output) == 1_000_000
    assert eng._visible_set is True
    assert any(c[0] == "evalc" for c in eng.calls)


def test_evaluate_timeout_with_successful_cancel(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")
    eng = FakeEngine()
    _install_fake_engine(monkeypatch, FakeFuture(eng))
    stuck = FakeFuture(value="never", delay=10.0, cancel_ok=True)
    eng._evalc_impl = lambda code: stuck

    with pytest.raises(bridge.MatlabError) as raised:
        bridge.evaluate("while 1; end", timeout_s=5)
    assert raised.value.kind == "timeout"
    assert stuck.cancel_calls >= 1
    assert bridge.status()["session_alive"] is True


def test_evaluate_timeout_kills_pid_when_cancel_fails(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")
    eng = FakeEngine(pid=7777)
    _install_fake_engine(monkeypatch, FakeFuture(eng))
    stuck = FakeFuture(value="never", delay=10.0, cancel_ok=False)
    eng._evalc_impl = lambda code: stuck
    killed = []
    monkeypatch.setattr(bridge.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    with pytest.raises(bridge.MatlabError) as raised:
        bridge.evaluate("while 1; end", timeout_s=5)
    assert raised.value.kind == "timeout"
    assert killed and killed[0][0] == 7777
    assert bridge.status()["session_alive"] is False


def test_evaluate_timeout_ignores_process_lookup_when_pid_is_gone(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")
    eng = FakeEngine(pid=8888)
    _install_fake_engine(monkeypatch, FakeFuture(eng))
    stuck = FakeFuture(value="never", delay=10.0, cancel_ok=False)
    eng._evalc_impl = lambda code: stuck

    def gone(pid, sig):
        raise ProcessLookupError(f"no such process: {pid}")

    monkeypatch.setattr(bridge.os, "kill", gone)
    with pytest.raises(bridge.MatlabError) as raised:
        bridge.evaluate("while 1; end", timeout_s=5)
    assert raised.value.kind == "timeout"
    assert bridge.status()["session_alive"] is False


def test_evaluate_timeout_surfaces_permission_error_from_kill(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")
    eng = FakeEngine(pid=9999)
    _install_fake_engine(monkeypatch, FakeFuture(eng))
    stuck = FakeFuture(value="never", delay=10.0, cancel_ok=False)
    eng._evalc_impl = lambda code: stuck

    def denied(pid, sig):
        raise PermissionError(f"Operation not permitted: {pid}")

    monkeypatch.setattr(bridge.os, "kill", denied)
    with pytest.raises(bridge.MatlabError) as raised:
        bridge.evaluate("while 1; end", timeout_s=5)
    assert raised.value.kind == "timeout"
    assert "PermissionError" in str(raised.value)
    assert bridge.status()["session_alive"] is False


def test_figure_probe_failure_is_reported_as_a_note(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")
    eng = FakeEngine()
    _install_fake_engine(monkeypatch, FakeFuture(eng))

    def boom_eval(code, nargout=0, background=False):
        if "isempty(get(groot,'CurrentFigure'))" in code:
            raise RuntimeError("Engine link is down")
        return FakeEngine.eval(eng, code, nargout=nargout, background=background)

    eng.eval = boom_eval  # type: ignore[method-assign]
    result = bridge.evaluate("1+1", timeout_s=10)
    assert result.ok is True
    assert result.figure_png is None
    assert result.notes and result.notes[0].startswith("figure probe failed:")


def test_version_probe_failure_is_a_start_failure(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")
    eng = FakeEngine()
    _install_fake_engine(monkeypatch, FakeFuture(eng))

    def boom_eval(code, nargout=0, background=False):
        if code.strip() == "version":
            raise RuntimeError("version unavailable")
        return FakeEngine.eval(eng, code, nargout=nargout, background=background)

    eng.eval = boom_eval  # type: ignore[method-assign]
    killed = []
    monkeypatch.setattr(bridge.os, "kill", lambda pid, sig: killed.append(pid))

    with pytest.raises(bridge.MatlabError) as raised:
        bridge.evaluate("1", timeout_s=10)
    assert raised.value.kind == "start_failed"
    assert "version" in str(raised.value).lower()
    assert bridge.status()["session_alive"] is False
    assert eng.quit_calls >= 1
    assert killed == [eng.pid]


def test_evaluate_maps_eval_errors(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")
    eng = FakeEngine()
    _install_fake_engine(monkeypatch, FakeFuture(eng))
    eng._evalc_impl = lambda code: FakeFuture(error=RuntimeError("Undefined function 'nope'"))

    with pytest.raises(bridge.MatlabError) as raised:
        bridge.evaluate("nope", timeout_s=5)
    assert raised.value.kind == "eval_error"
    assert "nope" in str(raised.value)


def test_evaluate_rejects_overlong_code(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")
    with pytest.raises(bridge.MatlabError) as raised:
        bridge.evaluate("x" * 100_001, timeout_s=5)
    assert raised.value.kind == "eval_error"


def test_figure_export_success(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")
    eng = FakeEngine()
    eng.current_figure = 1.0
    _install_fake_engine(monkeypatch, FakeFuture(eng))

    result = bridge.evaluate("plot(1:3)", timeout_s=10)
    assert result.ok is True
    assert result.figure_png is not None
    assert result.figure_png.startswith(b"\x89PNG")


def test_figure_export_failure_still_returns_ok(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")
    eng = FakeEngine()
    eng.current_figure = 1.0
    _install_fake_engine(monkeypatch, FakeFuture(eng))

    def failing_eval(code, nargout=0, background=False):
        if code.startswith("exportgraphics") or code.startswith("print"):
            raise RuntimeError("cannot write figure")
        return FakeEngine.eval(eng, code, nargout=nargout, background=background)

    eng.eval = failing_eval  # type: ignore[method-assign]

    result = bridge.evaluate("plot(1)", timeout_s=10)
    assert result.ok is True
    assert result.figure_png is None
    assert result.notes and any("figure" in n.lower() or "export" in n.lower() for n in result.notes)


def test_oversized_png_is_skipped_with_a_note(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")

    def write_huge(path, code):
        Path(path).write_bytes(b"\x89PNG" + b"z" * 5_000_001)

    eng = FakeEngine(export_impl=write_huge)
    eng.current_figure = 1.0
    _install_fake_engine(monkeypatch, FakeFuture(eng))

    result = bridge.evaluate("plot(1)", timeout_s=10)
    assert result.ok is True
    assert result.figure_png is None
    assert any("size" in n.lower() or "large" in n.lower() or "5" in n for n in result.notes)


def test_evaluate_calls_are_serialized(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")
    active = 0
    max_active = 0
    gate = threading.Lock()

    def slow(code):
        nonlocal active, max_active
        with gate:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with gate:
            active -= 1
        return "ok"

    eng = FakeEngine(evalc_impl=slow)
    _install_fake_engine(monkeypatch, FakeFuture(eng))
    errors: list[BaseException] = []

    def worker():
        try:
            bridge.evaluate("1", timeout_s=10)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert max_active == 1


def test_shutdown_is_idempotent(monkeypatch):
    monkeypatch.setenv(bridge.ENABLE_VAR, "1")
    eng = FakeEngine(pid=999)
    _install_fake_engine(monkeypatch, FakeFuture(eng))
    assert bridge.evaluate("1", timeout_s=5).ok is True
    monkeypatch.setattr(bridge.os, "kill", lambda pid, sig: None)
    bridge.shutdown()
    bridge.shutdown()
    assert eng.quit_calls >= 1
    assert bridge.status()["session_alive"] is False
