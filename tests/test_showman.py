from pathlib import Path

from circuit_mcp.showman import ShowmanManager


def test_status_never_exposes_openrouter_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "private-value")
    manager = ShowmanManager(tmp_path / "missing", port=32991)
    status = manager.status()
    assert status["openrouter_configured"] is True
    assert "private-value" not in repr(status)


def test_missing_build_is_reported_without_spawning(tmp_path):
    root = tmp_path / "showman"; root.mkdir(); (root / "package.json").write_text("{}")
    manager = ShowmanManager(root, port=32992)
    status = manager.start(timeout=.01)
    assert status["installed"] is True
    assert status["built"] is False
    assert "not built" in status["error"]
