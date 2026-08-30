import os
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

from circuit_mcp.showman import (
    ShowmanConnectionError,
    ShowmanManager,
    ShowmanTimeoutError,
)


def test_status_never_exposes_openrouter_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "private-value")
    manager = ShowmanManager(tmp_path / "missing", port=32991, data_dir=tmp_path / "data")
    status = manager.status()
    assert status["key_available"] is True
    assert "private-value" not in repr(status)


def test_missing_build_is_reported_without_spawning(tmp_path):
    root = tmp_path / "showman"; root.mkdir(); (root / "package.json").write_text("{}")
    manager = ShowmanManager(root, port=32992)
    status = manager.start(timeout=.01)
    assert status["installed"] is True
    assert status["built"] is False
    assert "not built" in status["error"]


def test_client_rejects_unlisted_paths_and_large_payloads(tmp_path, monkeypatch):
    manager = ShowmanManager(tmp_path, port=32993)
    monkeypatch.setattr(manager, "start", lambda: {"ok": True})
    try:
        manager.request_json("/objects/private", {})
        assert False
    except ValueError as exc:
        assert "unsupported" in str(exc)
    try:
        manager.request_json("/validate", {"text": "x" * (2 * 1024 * 1024)})
        assert False
    except ValueError as exc:
        assert "2 MiB" in str(exc)


def test_web_author_and_preview_are_bounded(tmp_path, monkeypatch):
    from circuit_mcp import web
    from fastapi.testclient import TestClient

    monkeypatch.setattr(web.SHOWMAN, "start", lambda *a, **k: {"ok": True, "authoring": "openrouter"})
    monkeypatch.setattr(web.SHOWMAN, "request_json", lambda path, payload, timeout: (202, {"jobId": "job-1"}))
    monkeypatch.setattr(web.SHOWMAN, "preview", lambda spec, frame: b"\x89PNG\r\n\x1a\nframe")
    client = TestClient(web.app, headers={"host": "localhost:2300"})
    authored = client.post("/api/showman/author", json={"brief": "explain an RC circuit"})
    assert authored.status_code == 202 and authored.json()["jobId"] == "job-1"
    preview = client.post("/api/showman/preview", json={"spec": {"title": "RC"}, "frame": 3})
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    assert "no-store" in preview.headers["cache-control"]
    assert client.post("/api/showman/author", json={"brief": ""}).status_code == 400


def test_render_urls_are_rewritten_to_the_local_proxy(monkeypatch):
    from circuit_mcp import web
    from fastapi.testclient import TestClient

    monkeypatch.setattr(web.SHOWMAN, "request_json", lambda path, payload, timeout: (
        200, {"video": {"key": "video/abc.mp4", "url": "http://127.0.0.1/private"}}
    ))
    monkeypatch.setattr(web.SHOWMAN, "object_response",
                        lambda key, byte_range=None, timeout=30: web.ShowmanArtifact(
                            200, "video/mp4", iter([b"video"]), 5, None, 5))
    client = TestClient(web.app, headers={"host": "localhost:2300"})
    rendered = client.post("/api/showman/render", json={"spec": {"title": "RC"}})
    assert rendered.json()["videoUrl"] == "/api/showman/objects/video/abc.mp4"
    artifact = client.get("/api/showman/objects/video/abc.mp4")
    assert artifact.content == b"video" and artifact.headers["content-type"] == "video/mp4"


def test_every_object_reference_is_localized_not_just_video(monkeypatch):
    """Issue #7: captions, poster, and any nested artifact must lose their
    upstream file:// URL, not just the top-level video."""
    from circuit_mcp import web
    from fastapi.testclient import TestClient

    upstream = {
        "video": {
            "key": "videos/a40e5d8d.mp4",
            "url": "file:///Users/jacobdang/Desktop/tools/circuit_mcp/.local/showman/objects/videos/a40e5d8d.mp4",
        },
        "captions": {
            "key": "videos/a40e5d8d.vtt",
            "url": "file:///Users/jacobdang/Desktop/tools/circuit_mcp/.local/showman/objects/videos/a40e5d8d.vtt",
        },
        "poster": {
            "key": "videos/a40e5d8d.png",
            "url": "file:///Users/jacobdang/Desktop/tools/circuit_mcp/.local/showman/objects/videos/a40e5d8d.png",
        },
        "tracks": [
            {"key": "videos/extra.vtt", "url": "file:///Users/jacobdang/extra.vtt"},
        ],
    }
    monkeypatch.setattr(web.SHOWMAN, "request_json", lambda path, payload, timeout: (200, upstream))
    client = TestClient(web.app, headers={"host": "localhost:2300"})

    rendered = client.post("/api/showman/render", json={"spec": {"title": "RC"}})

    body = rendered.json()
    assert body["video"]["url"] == "/api/showman/objects/videos/a40e5d8d.mp4"
    assert body["captions"]["url"] == "/api/showman/objects/videos/a40e5d8d.vtt"
    assert body["poster"]["url"] == "/api/showman/objects/videos/a40e5d8d.png"
    assert body["tracks"][0]["url"] == "/api/showman/objects/videos/extra.vtt"
    assert "file://" not in rendered.text
    assert "/Users/" not in rendered.text


def test_object_keys_cannot_escape_the_showman_namespace(tmp_path):
    manager = ShowmanManager(tmp_path, port=32994)
    for key in ("../secret", "video/../../secret", "/absolute"):
        try:
            manager.object_bytes(key)
            assert False
        except ValueError:
            pass


# --- transport error mapping (issue #9) ---------------------------------------


def _raise(exc):
    def _raiser(*args, **kwargs):
        raise exc
    return _raiser


def test_request_json_read_timeout_is_reported_as_a_typed_timeout(monkeypatch):
    """A read timeout must name which call timed out and after how long, not a bare 500."""
    manager = ShowmanManager(Path("/nonexistent"), port=33010)
    monkeypatch.setattr(manager, "start", lambda: {"ok": True})
    monkeypatch.setattr(urllib.request, "urlopen", _raise(TimeoutError("timed out")))

    try:
        manager.request_json("/render", {"spec": {}}, timeout=45)
        assert False
    except ShowmanTimeoutError as exc:
        assert "/render" in str(exc)
        assert "45" in str(exc)


def test_request_json_dropped_connection_is_reported_as_a_typed_error(monkeypatch):
    """A worker killed mid-request raises URLError; it must not escape as a bare 500."""
    manager = ShowmanManager(Path("/nonexistent"), port=33011)
    monkeypatch.setattr(manager, "start", lambda: {"ok": True})
    monkeypatch.setattr(urllib.request, "urlopen", _raise(urllib.error.URLError("connection refused")))

    try:
        manager.request_json("/render", {"spec": {}}, timeout=45)
        assert False
    except ShowmanConnectionError as exc:
        assert "/render" in str(exc)


def test_object_bytes_read_timeout_is_reported_as_a_typed_timeout(monkeypatch):
    manager = ShowmanManager(Path("/nonexistent"), port=33012)
    monkeypatch.setattr(manager, "start", lambda: {"ok": True})
    monkeypatch.setattr(urllib.request, "urlopen", _raise(TimeoutError("timed out")))

    try:
        manager.object_bytes("videos/a.mp4", timeout=20)
        assert False
    except ShowmanTimeoutError as exc:
        assert "videos/a.mp4" in str(exc)
        assert "20" in str(exc)


def test_object_bytes_dropped_connection_is_reported_as_a_typed_error(monkeypatch):
    manager = ShowmanManager(Path("/nonexistent"), port=33013)
    monkeypatch.setattr(manager, "start", lambda: {"ok": True})
    monkeypatch.setattr(urllib.request, "urlopen", _raise(urllib.error.URLError("connection refused")))

    try:
        manager.object_bytes("videos/a.mp4")
        assert False
    except ShowmanConnectionError as exc:
        assert "videos/a.mp4" in str(exc)


def test_preview_read_timeout_is_reported_as_a_typed_timeout(monkeypatch):
    manager = ShowmanManager(Path("/nonexistent"), port=33014)
    monkeypatch.setattr(manager, "start", lambda: {"ok": True})
    monkeypatch.setattr(urllib.request, "urlopen", _raise(TimeoutError("timed out")))

    try:
        manager.preview({"title": "RC"}, timeout=10)
        assert False
    except ShowmanTimeoutError as exc:
        assert "preview" in str(exc)
        assert "10" in str(exc)


def test_preview_dropped_connection_is_reported_as_a_typed_error(monkeypatch):
    manager = ShowmanManager(Path("/nonexistent"), port=33015)
    monkeypatch.setattr(manager, "start", lambda: {"ok": True})
    monkeypatch.setattr(urllib.request, "urlopen", _raise(urllib.error.URLError("connection refused")))

    try:
        manager.preview({"title": "RC"})
        assert False
    except ShowmanConnectionError as exc:
        assert "preview" in str(exc)


def test_render_route_maps_a_showman_timeout_to_504_with_an_actionable_message(monkeypatch):
    from circuit_mcp import web
    from fastapi.testclient import TestClient

    def _raise_timeout(path, payload, timeout):
        raise ShowmanTimeoutError(f"POST {path}", timeout)

    monkeypatch.setattr(web.SHOWMAN, "request_json", _raise_timeout)
    client = TestClient(web.app, headers={"host": "localhost:2300"})

    response = client.post("/api/showman/render", json={"spec": {"title": "RC"}})

    assert response.status_code == 504
    assert "/render" in response.json()["detail"]


def test_render_route_maps_a_dropped_connection_to_502(monkeypatch):
    from circuit_mcp import web
    from fastapi.testclient import TestClient

    def _raise_dropped(path, payload, timeout):
        raise ShowmanConnectionError(f"POST {path}", "connection refused")

    monkeypatch.setattr(web.SHOWMAN, "request_json", _raise_dropped)
    client = TestClient(web.app, headers={"host": "localhost:2300"})

    response = client.post("/api/showman/render", json={"spec": {"title": "RC"}})

    assert response.status_code == 502


def test_object_route_maps_a_showman_timeout_to_504(monkeypatch):
    from circuit_mcp import web
    from fastapi.testclient import TestClient

    def _raise_timeout(key, byte_range=None, timeout=30):
        raise ShowmanTimeoutError(f"GET /objects/{key}", timeout)

    monkeypatch.setattr(web.SHOWMAN, "object_response", _raise_timeout)
    client = TestClient(web.app, headers={"host": "localhost:2300"})

    response = client.get("/api/showman/objects/videos/a.mp4")

    assert response.status_code == 504


def test_preview_route_maps_a_showman_timeout_to_504(monkeypatch):
    from circuit_mcp import web
    from fastapi.testclient import TestClient

    def _raise_timeout(spec, frame):
        raise ShowmanTimeoutError("POST /preview", 30)

    monkeypatch.setattr(web.SHOWMAN, "preview", _raise_timeout)
    client = TestClient(web.app, headers={"host": "localhost:2300"})

    response = client.post("/api/showman/preview", json={"spec": {"title": "RC"}, "frame": 0})

    assert response.status_code == 504


# --- worker lifecycle (issues #2, #3, #4, #5) ---------------------------------


class _FakeProcess:
    """Stand-in for a spawned worker that is genuinely alive (its pid is ours)."""

    def __init__(self):
        self.pid = os.getpid()
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _built_root(tmp_path):
    """A directory that looks like an installed and built Showman checkout."""
    root = tmp_path / "showman"
    (root / "dist" / "service").mkdir(parents=True)
    (root / "package.json").write_text("{}")
    (root / "dist" / "service" / "worker.js").write_text("// worker")
    return root


def _manager(tmp_path, port, healthy):
    """Build a manager whose health probe and spawn are both controlled."""
    root = _built_root(tmp_path)
    manager = ShowmanManager(root, port=port, data_dir=tmp_path / "data")
    spawns = []

    def fake_popen(*args, **kwargs):
        spawns.append(kwargs.get("env", {}))
        healthy["value"] = True
        return _FakeProcess()

    manager._health = lambda timeout=.4: healthy["value"]
    return manager, spawns, fake_popen


def test_data_dir_does_not_depend_on_the_checkout_location(tmp_path):
    """A relocated root must not push the data directory outside the project."""
    relocated = ShowmanManager(tmp_path / "elsewhere" / "showman", port=32995)
    assert relocated.data_dir == ShowmanManager(port=32995).data_dir


def test_concurrent_starts_spawn_exactly_one_worker(tmp_path, monkeypatch):
    """Issue #5: the readiness wait must not sit outside the lock."""
    healthy = {"value": False}
    manager, spawns, fake_popen = _manager(tmp_path, 32996, healthy)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    barrier = threading.Barrier(6)

    def race():
        barrier.wait()
        manager.start(timeout=2)

    threads = [threading.Thread(target=race) for _ in range(6)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()

    assert len(spawns) == 1


def test_a_stale_build_is_replaced_rather_than_adopted(tmp_path, monkeypatch):
    """Issue #2: adopting a stale build silently runs code you are not reading."""
    healthy = {"value": True}
    manager, spawns, fake_popen = _manager(tmp_path, 32997, healthy)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    manager._write_identity(_FakeProcess(), fingerprint="a-stale-build")
    # The recorded pid is provably our worker, so it is safe to stop and replace.
    manager._is_our_worker = lambda pid: True
    manager._terminate = lambda pid: healthy.__setitem__("value", False)

    manager.start(timeout=2)

    assert len(spawns) == 1, "a worker whose build does not match must be replaced"
    assert manager._read_identity()["fingerprint"] == manager.build_fingerprint()


def test_a_stale_worker_we_cannot_verify_is_never_killed(tmp_path, monkeypatch):
    """Pids are reused; only a process proven to be our worker may be terminated."""
    healthy = {"value": True}
    manager, spawns, fake_popen = _manager(tmp_path, 33003, healthy)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    manager._write_identity(_FakeProcess(), fingerprint="a-stale-build")
    manager._is_our_worker = lambda pid: False
    killed = []
    manager._terminate = lambda pid: killed.append(pid)

    status = manager.start(timeout=2)

    assert killed == [], "a pid we cannot identify must never be signalled"
    assert status["ok"] is False and spawns == []


def test_a_matching_worker_is_adopted_without_respawning(tmp_path, monkeypatch):
    healthy = {"value": True}
    manager, spawns, fake_popen = _manager(tmp_path, 32998, healthy)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    manager._write_identity(_FakeProcess(), fingerprint=manager.build_fingerprint())

    status = manager.start(timeout=2)

    assert spawns == [], "a current, verified worker should be reused"
    assert status["ok"] is True


def test_an_unidentified_process_on_the_port_is_refused(tmp_path, monkeypatch):
    """Issue #2: a healthy stranger on 2301 must fail loudly, not be adopted."""
    healthy = {"value": True}
    manager, spawns, fake_popen = _manager(tmp_path, 32999, healthy)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    status = manager.start(timeout=2)

    assert status["ok"] is False
    assert str(manager.port) in status["error"]
    assert spawns == [], "must not spawn into a port that is already taken"


def test_status_reports_the_workers_authoring_not_the_parent_env(tmp_path, monkeypatch):
    """Issue #3: the parent's key says nothing about what the worker can do."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "parent-only-value")
    healthy = {"value": True}
    manager, _, _ = _manager(tmp_path, 33000, healthy)
    manager._write_identity(
        _FakeProcess(), fingerprint=manager.build_fingerprint(), authoring="offline"
    )

    status = manager.status()

    assert status["authoring"] == "offline"
    assert "parent-only-value" not in repr(status)


def test_status_cannot_claim_authoring_for_a_worker_that_is_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "parent-only-value")
    healthy = {"value": False}
    manager, _, _ = _manager(tmp_path, 33001, healthy)

    assert manager.status()["authoring"] == "unknown"


def test_a_spawned_worker_inherits_the_authoring_key_and_binds_loopback(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "forwarded-value")
    healthy = {"value": False}
    manager, spawns, fake_popen = _manager(tmp_path, 33002, healthy)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    manager.start(timeout=2)

    assert spawns[0]["OPENROUTER_API_KEY"] == "forwarded-value"
    assert spawns[0]["SHOWMAN_HOST"] == "127.0.0.1"
    assert manager._read_identity()["authoring"] == "openrouter"


# --- authoring capability gate (issues #1, #4) --------------------------------


def _web_client(monkeypatch, authoring, calls):
    from circuit_mcp import web
    from fastapi.testclient import TestClient

    state = {"ok": True, "authoring": authoring, "error": ""}
    monkeypatch.setattr(web.SHOWMAN, "start", lambda *a, **k: state)
    monkeypatch.setattr(web.SHOWMAN, "status", lambda *a, **k: state)
    monkeypatch.setattr(web.SHOWMAN, "request_json", lambda path, payload, timeout: (
        calls.append((path, payload)) or (200, {"video": {"key": "videos/x.mp4"}, "durationSec": 8, "fps": 30})
    ))
    return TestClient(web.app, headers={"host": "localhost:2300"})


def test_generate_refuses_when_the_worker_cannot_author(monkeypatch):
    """Issue #1: an offline worker returns unrelated template lessons, so refuse loudly."""
    calls = []
    client = _web_client(monkeypatch, "offline", calls)

    response = client.post("/api/showman/generate", json={"brief": "explain an RC circuit"})

    assert response.status_code == 503
    assert calls == [], "must not ask an offline worker to author a lesson"
    assert "OPENROUTER_API_KEY" in response.json()["detail"]


def test_author_refuses_when_the_worker_cannot_author(monkeypatch):
    calls = []
    client = _web_client(monkeypatch, "offline", calls)

    assert client.post("/api/showman/author", json={"brief": "explain an RC circuit"}).status_code == 503
    assert calls == []


def test_generate_forwards_the_submitted_brief_verbatim(monkeypatch):
    """Issue #4: no canned circuit spec may be substituted for the user's brief."""
    calls = []
    client = _web_client(monkeypatch, "openrouter", calls)
    brief = "Derive the resonant frequency of a series RLC circuit with R = 47 ohm"

    response = client.post("/api/showman/generate", json={"brief": brief})

    assert response.status_code == 200
    assert calls == [("/generate", {"brief": brief})]


def test_no_canned_circuit_specification_remains_in_the_generate_path():
    from circuit_mcp import web

    source = Path(web.__file__).read_text()
    for canned in ("12 V", "R = 1 kΩ", "C = 100 µF", "physics.circuit"):
        assert canned not in source, f"hardcoded lesson content {canned!r} must not ship"


def test_stop_never_signals_a_worker_this_manager_did_not_start(tmp_path):
    """The app lifespan calls stop(); a test process must not kill a live dev worker."""
    manager = ShowmanManager(_built_root(tmp_path), port=33004, data_dir=tmp_path / "data")
    manager._write_identity(_FakeProcess(), fingerprint=manager.build_fingerprint())
    signalled = []
    manager._terminate = lambda pid: signalled.append(pid)

    manager.stop()

    assert signalled == [], "stop() may only terminate a process this manager holds"
