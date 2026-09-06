"""The agent's side of the canvas: MCP tools that add cards, and the routes the
browser polls to show them.

The math in a card runs in the guarded worker like every other SymPy call, so a
pathological expression cannot pin the server. The store only ever sees a payload
the card module already built and verified.
"""
from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

from circuit_mcp import server, web
from circuit_mcp.storage import CommandCenterDB

MATH = '<math xmlns="http://www.w3.org/1998/Math/MathML"'

FORMULA = {"items": [{"label": "time constant", "expression": "R*C"}]}
WALKTHROUGH = {
    "truth": "V_s*(1 - exp(-t/(R*C)))",
    "steps": [
        {"expression": "V_s - V_s*exp(-t/(R*C))", "note": "distribute"},
        {"expression": "V_s*(1 - exp(-t/(R*C)))", "note": "factor V_s"},
    ],
}
BROKEN = {
    "truth": "V_s*(1 - exp(-t/(R*C)))",
    "steps": [
        {"expression": "V_s*(1 - exp(-t/(R*C)))", "note": "start"},
        {"expression": "V_s*(1 + exp(-t/(R*C)))", "note": "sign slipped"},
    ],
}


def _db(tmp_path) -> CommandCenterDB:
    database = CommandCenterDB(tmp_path / "circuit_mcp.sqlite3")
    database.prepare(None, None)
    return database


def _mcp(tmp_path, monkeypatch) -> CommandCenterDB:
    database = _db(tmp_path)
    monkeypatch.setattr(server, "_storage", lambda: database)
    return database


# --- MCP tools ----------------------------------------------------------------

def test_the_card_math_runs_in_the_guarded_worker():
    assert "_guarded" in inspect.getsource(server.canvas_card_add)


def test_canvas_card_add_stores_a_rendered_formula_card(tmp_path, monkeypatch):
    database = _mcp(tmp_path, monkeypatch)
    result = server.canvas_card_add("formula", "RC", FORMULA)
    assert result["ok"] is True
    card = result["card"]
    assert card["kind"] == "formula" and card["title"] == "RC"
    assert card["payload"]["items"][0]["mathml"].startswith(MATH)
    assert database.get_card(card["id"])["id"] == card["id"]


def test_canvas_card_add_records_the_event_the_activity_feed_shows(tmp_path, monkeypatch):
    database = _mcp(tmp_path, monkeypatch)
    server.canvas_card_add("formula", "RC", FORMULA)
    events = database.events(limit=5)
    assert events[0]["kind"] == "canvas_card_add" and events[0]["ok"] is True


def test_a_verified_walkthrough_is_stored_and_marked(tmp_path, monkeypatch):
    _mcp(tmp_path, monkeypatch)
    result = server.canvas_card_add("walkthrough", "RC charging", WALKTHROUGH)
    assert result["ok"] is True
    assert result["card"]["payload"]["verified"] is True


def test_a_walkthrough_with_a_bad_step_is_refused_and_nothing_is_stored(tmp_path, monkeypatch):
    database = _mcp(tmp_path, monkeypatch)
    result = server.canvas_card_add("walkthrough", "wrong", BROKEN)
    assert result["ok"] is False
    assert result["error"] == "card_refused"
    assert "1 -> 2" in result["message"]
    assert database.list_cards() == []


def test_an_unparseable_expression_is_refused_with_the_item_named(tmp_path, monkeypatch):
    _mcp(tmp_path, monkeypatch)
    result = server.canvas_card_add("formula", "bad", {"items": [
        {"label": "ok", "expression": "R*C"}, {"label": "no", "expression": "R*C)"}]})
    assert result["ok"] is False and result["error"] == "card_refused"
    assert "item 2" in result["message"]


def test_cards_can_be_linked_to_a_problem_and_listed_for_it(tmp_path, monkeypatch):
    database = _mcp(tmp_path, monkeypatch)
    problem = database.create_problem("RC", "transients", "find tau")
    server.canvas_card_add("formula", "unlinked", FORMULA)
    linked = server.canvas_card_add("vocabulary", "terms", {"terms": [
        {"term": "tau", "definition": "time constant", "expression": "R*C"}]}, problem_id=problem["id"])
    listed = server.canvas_card_list(problem_id=problem["id"])
    assert listed["ok"] is True
    assert [c["id"] for c in listed["items"]] == [linked["card"]["id"]]
    assert len(server.canvas_card_list()["items"]) == 2


def test_canvas_card_remove_hides_the_card_and_reports_a_missing_one(tmp_path, monkeypatch):
    _mcp(tmp_path, monkeypatch)
    card = server.canvas_card_add("formula", "RC", FORMULA)["card"]
    assert server.canvas_card_remove(card["id"]) == {"ok": True, "removed": card["id"]}
    assert server.canvas_card_list()["items"] == []
    gone = server.canvas_card_remove(card["id"])
    assert gone["ok"] is False and gone["error"] == "not_found"


def test_the_card_tools_are_registered_on_the_mcp_server():
    import asyncio

    names = {tool.name for tool in asyncio.run(server.server.list_tools())}
    assert {"canvas_card_add", "canvas_card_list", "canvas_card_remove"} <= names


# --- browser routes -----------------------------------------------------------

def _browser(tmp_path, monkeypatch) -> TestClient:
    data = tmp_path / "command_center"
    monkeypatch.setattr(web, "DATA", data)
    monkeypatch.setattr(web, "FILES", data / "files")
    monkeypatch.setattr(web, "INDEX", data / "library.json")
    monkeypatch.setattr(web, "HISTORY", data / "history.jsonl")
    return TestClient(web.app, headers={"host": "localhost:2300"})


def test_the_browser_polls_cards_and_sees_the_mathml(tmp_path, monkeypatch):
    with _browser(tmp_path, monkeypatch) as browser:
        assert browser.get("/api/canvas").json()["items"] == []
        stored = web._db().create_card("formula", "RC", {"items": [
            {"label": "tau", "expression": "R*C", "mathml": f"{MATH}><mi>R</mi></math>"}]})
        items = browser.get("/api/canvas").json()["items"]
        assert [item["id"] for item in items] == [stored["id"]]
        assert items[0]["payload"]["items"][0]["mathml"].startswith(MATH)


def test_the_browser_can_close_a_card(tmp_path, monkeypatch):
    with _browser(tmp_path, monkeypatch) as browser:
        stored = web._db().create_card("formula", "RC", {"items": []})
        assert browser.delete(f"/api/canvas/{stored['id']}").status_code == 200
        assert browser.get("/api/canvas").json()["items"] == []
        assert browser.delete(f"/api/canvas/{stored['id']}").status_code == 404


def test_the_browser_cannot_author_cards(tmp_path, monkeypatch):
    """Content comes from the agent through MCP; the board only shows and closes."""
    with _browser(tmp_path, monkeypatch) as browser:
        assert browser.post("/api/canvas", json={"kind": "formula"}).status_code == 405
