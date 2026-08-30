"""A render must be bounded by wall clock, not by per-read socket timeouts (#30).

A generate was observed running 968 s against a nominal 600 s timeout: urlopen's
`timeout` applies to each socket operation, so a worker that keeps answering is
never cut off.
"""
from __future__ import annotations

import time

import pytest

from circuit_mcp.showman import ShowmanManager, ShowmanTimeoutError


def _manager(tmp_path, monkeypatch):
    root = tmp_path / "showman"
    (root / "dist" / "service").mkdir(parents=True)
    (root / "package.json").write_text("{}")
    (root / "dist" / "service" / "worker.js").write_text("// worker")
    manager = ShowmanManager(root, port=33100, data_dir=tmp_path / "data")
    monkeypatch.setattr(manager, "start", lambda *a, **k: {"ok": True})
    return manager


def test_a_slow_upstream_is_cut_off_at_the_deadline(tmp_path, monkeypatch):
    from circuit_mcp import showman

    def crawl(request, timeout=None):
        time.sleep(30)  # far past the budget; the deadline must not wait for it

    monkeypatch.setattr(showman.urllib.request, "urlopen", crawl)
    manager = _manager(tmp_path, monkeypatch)

    started = time.monotonic()
    with pytest.raises(ShowmanTimeoutError) as caught:
        manager.request_json("/generate", {"brief": "x"}, timeout=0.5)
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"the call must return at its deadline, not the upstream's ({elapsed:.1f}s)"
    assert "/generate" in str(caught.value)
    assert "0.5" in str(caught.value), "the message must name the budget it exceeded"


def test_a_prompt_upstream_is_untouched(tmp_path, monkeypatch):
    from circuit_mcp import showman

    class _Response:
        status = 200
        def read(self, *a): return b'{"ok": true}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(showman.urllib.request, "urlopen", lambda request, timeout=None: _Response())
    manager = _manager(tmp_path, monkeypatch)

    assert manager.request_json("/render", {"spec": {}}, timeout=30) == (200, {"ok": True})


def test_the_worker_keeps_its_stderr_for_diagnosis(tmp_path, monkeypatch):
    """A 16-minute render left no record of which phase was slow."""
    import subprocess

    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(manager, "start", ShowmanManager.start.__get__(manager))
    monkeypatch.setattr(manager, "_health", lambda timeout=.4: False)
    captured = {}

    class _Process:
        pid = 1
        returncode = 1
        def poll(self): return 1

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return _Process()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    manager.start(timeout=.01)

    assert captured["stderr"] is not subprocess.DEVNULL, "discarding stderr makes every incident undiagnosable"
    assert manager.log_path.exists()
