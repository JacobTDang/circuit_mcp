import json

from circuit_mcp import workspace


def test_workspace_configuration_round_trip_is_private_and_atomic(tmp_path, monkeypatch):
    destination = tmp_path / "workspace.json"
    monkeypatch.setenv("CIRCUIT_MCP_WORKSPACE_CONFIG", str(destination))

    assert workspace.read_workspace()["configured"] is False
    saved = workspace.configure_workspace(10, 20, 800, 600, display=2)
    assert saved["ok"] is True
    assert destination.exists()
    assert json.loads(destination.read_text()) == {
        "display": 2, "x": 10, "y": 20, "width": 800, "height": 600
    }
    assert workspace.read_workspace() == saved


def test_corrupt_workspace_configuration_is_reported(tmp_path, monkeypatch):
    destination = tmp_path / "workspace.json"
    destination.write_text("not json")
    monkeypatch.setenv("CIRCUIT_MCP_WORKSPACE_CONFIG", str(destination))
    result = workspace.read_workspace()
    assert result["ok"] is False
    assert "invalid" in result["message"]


def test_full_display_configuration_is_explicit_and_atomic(tmp_path, monkeypatch):
    destination = tmp_path / "workspace.json"
    monkeypatch.setenv("CIRCUIT_MCP_WORKSPACE_CONFIG", str(destination))
    saved = workspace.configure_display(2)
    assert saved["mode"] == "display"
    assert saved["display"] == 2
    assert workspace.read_workspace() == saved
