"""The agent's side of the canvas: MCP tools that add cards, and the routes the
browser polls to show them.

The math in a card runs in the guarded worker like every other SymPy call, so a
pathological expression cannot pin the server. The store only ever sees a payload
the card module already built and verified.
"""
from __future__ import annotations

import inspect
import re

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


def test_app_js_keeps_agent_card_layout_across_reload(tmp_path, monkeypatch):
    """readCanvas must keep formula/walkthrough/vocabulary or freeSpot reshuffles on refresh."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        # The allowlist inside readCanvas — not just CARD_KINDS elsewhere.
        read = app_script.split("function readCanvas()", 1)[1].split("function saveCanvas()", 1)[0]
        for kind in ("formula", "walkthrough", "vocabulary"):
            assert kind in read, f"readCanvas drops {kind}; layout resets on reload"


def test_app_js_renders_and_keeps_the_breadboard_and_expected_cards(tmp_path, monkeypatch):
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        read = app_script.split("function readCanvas()", 1)[1].split("function saveCanvas()", 1)[0]
        for kind in ("breadboard", "expected"):
            assert kind in read, f"readCanvas drops {kind}; layout resets on reload"
            assert f"'{kind}'" in app_script.split("const CARD_KINDS=", 1)[1].split(";", 1)[0]
        assert "card.kind==='breadboard'" in app_script and "card.kind==='expected'" in app_script


def test_app_js_keeps_the_layout_but_not_the_payload_of_a_server_card(tmp_path, monkeypatch):
    """A breadboard payload is about 30 KB; the poll re-attaches it within a tick."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        save = app_script.split("function saveCanvas()", 1)[1].split("\n", 1)[0]
        branch = re.search(r"server\s*\?\s*\(\{([^}]*)\}\)", save)
        assert branch, "saveCanvas must persist a server card without its payload"
        persisted = {field.strip() for field in branch.group(1).split(",")}
        assert "card" not in persisted, f"saveCanvas stores the whole payload: {sorted(persisted)}"
        assert persisted == {"id", "kind", "x", "y", "z", "w", "server"}


def test_app_js_refuses_to_render_a_card_kind_it_does_not_know(tmp_path, monkeypatch):
    """An unknown kind used to reach the canvas with no title and the activity body."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        poll = app_script.split("async function pollCanvasCards()", 1)[1].split("\n", 1)[0]
        assert "CARD_KINDS.has" in poll, "pollCanvasCards pushes every server kind onto the canvas"
        assert "unknown card kind" in poll, "an unknown kind must say so once, not render broken"


# --- the drawing stays on the canvas ------------------------------------------

from tests.fixtures import lab1  # noqa: E402


def test_a_breadboard_card_returns_the_wire_list_not_the_drawing(tmp_path, monkeypatch):
    """The SVG is 7 KB of holes the agent cannot act on; the canvas already has it."""
    database = _mcp(tmp_path, monkeypatch)
    result = server.canvas_card_add("breadboard", "Exp 1 build", lab1.EXP1_NONINVERTING)
    assert result["ok"] is True
    payload = result["card"]["payload"]
    assert payload["svg"] == "(rendered on the canvas)"
    assert payload["wires"][0].startswith("Power:")
    assert payload["holes"]["R1"] and payload["probes"]
    assert database.get_card(result["card"]["id"])["payload"]["svg"].startswith("<svg")


def test_app_js_shows_the_pot_positions_on_the_expected_card(tmp_path, monkeypatch):
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        footer = app_script.split("card.kind==='expected'", 1)[1].split("card-verified", 1)[1].split("\n", 1)[0]
        assert "p.pots" in footer, "the expected card never names the pot settings the numbers assume"
