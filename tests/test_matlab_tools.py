"""MCP matlab_status / matlab_eval — wiring only; the Engine lives in matlab_bridge."""
from __future__ import annotations

import asyncio
import base64
import inspect
import json

import pytest

from circuit_mcp import server
from circuit_mcp.matlab_bridge import EvalResult, MatlabError


def test_matlab_status_returns_the_bridge_status_unchanged(monkeypatch):
    payload = {
        "ok": True,
        "enabled": True,
        "engine_importable": True,
        "session_alive": False,
        "matlab_version": None,
        "note": "ready",
    }
    monkeypatch.setattr(server.matlab_bridge, "status", lambda: payload)
    assert server.matlab_status() is payload


def test_matlab_eval_returns_text_only_when_there_is_no_figure(monkeypatch):
    monkeypatch.setattr(
        server.matlab_bridge,
        "evaluate",
        lambda code, timeout_s=None: EvalResult(
            ok=True, output="ans =\n     2", truncated=False, notes=[], figure_png=None
        ),
    )
    result = server.matlab_eval("1+1")
    assert [c.type for c in result.content] == ["text"]
    body = json.loads(result.content[0].text)
    assert body == {"ok": True, "output": "ans =\n     2", "truncated": False, "notes": []}
    assert result.structured_content == body


def test_matlab_eval_attaches_a_png_image_when_the_bridge_returns_one(monkeypatch):
    png = b"\x89PNG\r\n\x1a\nfake"
    monkeypatch.setattr(
        server.matlab_bridge,
        "evaluate",
        lambda code, timeout_s=None: EvalResult(
            ok=True, output="", truncated=False, notes=["exported figure"], figure_png=png
        ),
    )
    result = server.matlab_eval("plot(1:3)")
    assert [c.type for c in result.content] == ["text", "image"]
    body = json.loads(result.content[0].text)
    assert body == {"ok": True, "output": "", "truncated": False, "notes": ["exported figure"]}
    assert result.content[1].mime_type == "image/png"
    assert base64.b64decode(result.content[1].data) == png
    assert result.structured_content == body


@pytest.mark.parametrize(
    "kind,message",
    [
        ("disabled", "set CIRCUIT_MCP_ENABLE_MATLAB=1"),
        ("engine_missing", "matlab.engine is not installed"),
        ("start_failed", "could not start MATLAB"),
        ("timeout", "evaluation exceeded 30s"),
        ("eval_error", "Undefined function 'nope'"),
    ],
)
def test_matlab_eval_maps_each_MatlabError_kind_to_a_tool_failure(monkeypatch, kind, message):
    def boom(code, timeout_s=None):
        raise MatlabError(kind, message)

    monkeypatch.setattr(server.matlab_bridge, "evaluate", boom)
    result = server.matlab_eval("nope")
    assert result.structured_content == {"ok": False, "error": kind, "message": message}
    assert json.loads(result.content[0].text) == result.structured_content


def test_matlab_tools_are_registered_on_the_mcp_server():
    names = {tool.name for tool in asyncio.run(server.server.list_tools())}
    assert {"matlab_status", "matlab_eval"} <= names


def test_matlab_tools_do_not_run_under_the_lcapy_timeout():
    """The Engine must not live under the forking symbolic worker."""
    for name in ("matlab_status", "matlab_eval"):
        assert "_guarded" not in inspect.getsource(getattr(server, name))
