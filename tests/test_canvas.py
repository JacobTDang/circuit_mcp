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


# --- a solution is homework, not a desk card ----------------------------------

SOLUTION = {
    "given": [{"name": "R1", "value": 1000, "unit": "Ω"}, {"name": "R2", "value": 15000, "unit": "Ω"}],
    "steps": [{"expression": "1 + R2/R1"}, {"expression": "16"}],
    "answer": {"expression": "16", "unit": "V/V"},
}


def _desk_and_sheet(tmp_path, monkeypatch) -> TestClient:
    """The MCP tools and the browser routes on one store, as the app runs them."""
    browser = _browser(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "_storage", web._db)
    return browser


def _solved(page: int, tag: str = "hw3") -> dict:
    problem = web._db().create_problem(f"Problem {page}", "op-amps", f"Problem {page}: find the gain.",
                                       source_page=page)
    web._db().tag_problem(problem["id"], tag)
    added = server.canvas_card_add("solution", f"P{page} gain", SOLUTION, problem_id=problem["id"])
    assert added["ok"] is True, added
    return added["card"]


def test_canvas_card_list_is_the_desk_and_never_returns_a_solution(tmp_path, monkeypatch):
    with _desk_and_sheet(tmp_path, monkeypatch):
        solution = _solved(1)
        formula = server.canvas_card_add("formula", "gain", FORMULA, problem_id=solution["problem_id"])["card"]

        assert [card["id"] for card in server.canvas_card_list()["items"]] == [formula["id"]]
        assert [card["id"] for card in server.canvas_card_list(problem_id=solution["problem_id"])["items"]] \
            == [formula["id"]]


def test_canvas_card_remove_refuses_a_solution_and_leaves_it_on_the_sheet(tmp_path, monkeypatch):
    with _desk_and_sheet(tmp_path, monkeypatch) as browser:
        solution = _solved(1)

        refused = server.canvas_card_remove(solution["id"])

        assert refused["ok"] is False and refused["error"] == "not_a_desk_card"
        assert "solutions sheet" in refused["message"]
        assert web._db().get_card(solution["id"])["id"] == solution["id"]
        sheet = browser.get("/solutions?tag=hw3").text
    assert "no solution yet" not in sheet and 'class="answer"' in sheet


def test_the_browser_cannot_close_a_solution(tmp_path, monkeypatch):
    """The board never lists one, but the route must not rest on the id staying secret."""
    with _desk_and_sheet(tmp_path, monkeypatch) as browser:
        solution = _solved(1)

        closed = browser.delete(f"/api/canvas/{solution['id']}")

        assert closed.status_code == 409
        assert "solutions sheet" in closed.json()["detail"]
        assert "canvas_card_close" not in [event["kind"] for event in web._db().events()]
        sheet = browser.get("/solutions?tag=hw3").text
    assert "no solution yet" not in sheet and 'class="answer"' in sheet


def test_a_solution_citing_its_own_problems_attempt_is_stored(tmp_path, monkeypatch):
    with _desk_and_sheet(tmp_path, monkeypatch):
        problem = web._db().create_problem("Problem 1", "op-amps", "find the gain")
        attempt = web._db().create_attempt(problem["id"], "student")

        added = server.canvas_card_add("solution", "P1 gain", {**SOLUTION, "attempt_id": attempt["id"]},
                                       problem_id=problem["id"])

    assert added["ok"] is True, added
    assert added["card"]["payload"]["attempt_id"] == attempt["id"]


def test_a_solution_citing_another_problems_attempt_is_refused(tmp_path, monkeypatch):
    """The sheet prints the cited attempt's checks as this problem's evidence."""
    with _desk_and_sheet(tmp_path, monkeypatch):
        database = web._db()
        mine = database.create_problem("HW1 P1 divider", "dividers", "find V2")
        other = database.create_problem("HW1 P2 other", "dividers", "find V3")
        borrowed = database.create_attempt(other["id"], "student")

        refused = server.canvas_card_add("solution", "P1", {**SOLUTION, "attempt_id": borrowed["id"]},
                                         problem_id=mine["id"])

        assert refused["ok"] is False and refused["error"] == "wrong_attempt"
        assert "HW1 P2 other" in refused["message"]
        assert database.list_cards(kind="solution") == []


def test_a_solution_citing_an_attempt_that_does_not_exist_is_refused(tmp_path, monkeypatch):
    with _desk_and_sheet(tmp_path, monkeypatch):
        problem = web._db().create_problem("Problem 3", "dividers", "find V_out")
        for made_up in ("0123456789abcdef0123456789abcdef", "not-an-attempt"):
            refused = server.canvas_card_add("solution", "P3", {**SOLUTION, "attempt_id": made_up},
                                             problem_id=problem["id"])
            assert refused["ok"] is False and refused["error"] == "wrong_attempt"
            assert made_up in refused["message"]
        assert web._db().list_cards(kind="solution") == []


def test_clearing_the_desk_through_mcp_leaves_every_solution_on_the_sheet(tmp_path, monkeypatch):
    """List the desk and remove everything listed -- the natural way to clear it between problems."""
    with _desk_and_sheet(tmp_path, monkeypatch) as browser:
        _solved(1)
        _solved(2)
        server.canvas_card_add("formula", "gain", FORMULA)

        for card in server.canvas_card_list()["items"]:
            assert server.canvas_card_remove(card["id"])["ok"] is True

        assert server.canvas_card_list()["items"] == []
        sheet = browser.get("/solutions?tag=hw3").text
    assert "no solution yet" not in sheet
    assert sheet.count('class="answer"') == 2


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


def test_app_js_keeps_a_build_step_with_the_drawing(tmp_path, monkeypatch):
    """The legend sat open under a drawing that already filled an 800px window, so a step and its parts could not be seen together."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        branch = app_script.split("card.kind==='breadboard'", 1)[1].split("card.kind==='expected'", 1)[0]
        assert "<details" in branch, "the net legend stays open and pushes the steps below the window"
        details = branch.split("<details", 1)[1].split(">", 1)[0]
        assert "card-legend" in details and "open" not in details
        assert branch.index("<details") < branch.index("${p.svg"), "the legend has to sit on the drawing, not between it and the steps"
        focus = app_script.split("function boardFocus(", 1)[1].split("function boardReveal(", 1)[0]
        assert "boardReveal(" in focus
        reveal = app_script.split("function boardReveal(", 1)[1].split("\nfunction ", 1)[0]
        assert "scrollBy(" in reveal and "scrollTop" in reveal and ".is-lit" in reveal and "card-wires" in reveal
        css = browser.get("/assets/canvas.css").text
        board = css.split(".card-board{", 1)[1].split("}", 1)[0]
        assert "position:sticky" in board and "max-height:calc(100vh - 72px - 52px)" in board
        card = css.split('.workspace-item[data-kind="breadboard"]{', 1)[1].split("}", 1)[0]
        assert "overflow:visible" in card


def test_app_js_pins_a_build_step_from_the_keyboard(tmp_path, monkeypatch):
    """Enter did nothing, and once a net was pinned the keyboard could not light a step."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        focusin = app_script.split("addEventListener('focusin'", 1)[1].split("});", 1)[0]
        assert "dataset.pinned)return" not in focusin
        board = app_script.split("function boardFocus(", 1)[1]
        assert "addEventListener('keydown'" in board
        keys = board.split("addEventListener('keydown'", 1)[1].split("});", 1)[0]
        assert "Escape" in keys and "Enter" in keys and "dataset.pinned" in keys


def test_the_breadboard_drawing_has_no_minimum_width(tmp_path, monkeypatch):
    """min-width:560px stopped the second press of minus from shrinking the drawing."""
    with _browser(tmp_path, monkeypatch) as browser:
        css = browser.get("/assets/canvas.css").text
        rule = css.split(".card-board svg{", 1)[1].split("}", 1)[0]
        assert "560px" not in rule
        assert "min-width:0" in rule


def test_app_js_draws_the_net_legend_and_links_each_step_to_the_board(tmp_path, monkeypatch):
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        branch = app_script.split("card.kind==='breadboard'", 1)[1].split("card.kind==='expected'", 1)[0]
        assert 'class="card-nets"' in branch and 'data-nets="${escapeHtml(n.net)}"' in branch
        assert 'data-step="${i+1}"' in branch
        assert "escapeHtml(n.name)" in branch and "(n.members||[]).map(escapeHtml)" in branch
        assert "/^#[0-9a-fA-F]{6}$/.test(n.color)" in branch, "a legend colour reaches a style attribute unchecked"
        assert "function boardFocus(" in app_script
        for event in ("pointerover", "pointerout", "focusin", "click"):
            assert f"document.addEventListener('{event}',e=>{{const[card,t]=boardTarget(e)" in app_script, event
        css = browser.get("/assets/canvas.css").text
        assert ".card-board.is-focusing-step" in css and ".card-board.is-focusing-net" in css


def test_app_js_retries_a_poll_whose_render_failed(tmp_path, monkeypatch):
    """The signature was stamped before the render, so a render that threw was never retried."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        poll = app_script.split("async function pollCanvasCards()", 1)[1].split("pollCanvasCards();", 1)[0]
        before, after = poll.split("cardsSignature=signature", 1)
        assert "renderCanvas()" not in after.split("catch", 1)[0] or "renderCanvas()" in before, \
            "the signature must not be stamped until the render has run"
        assert "cardsSignature=''" in poll, "a failed poll must clear the signature so the next one retries"


def test_app_js_survives_a_card_it_cannot_render(tmp_path, monkeypatch):
    """One malformed reading used to abort renderCanvas mid-loop, losing every later card."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        body = app_script.split("function cardBody(item)", 1)[1].split("const baseCardBody", 1)[0]
        assert "try{" in body and "catch" in body, "cardBody must not let one card take down the render"
        assert "card-error" in body, "a card that cannot render should say so in its place"
        assert "r.volts.toFixed(3)" not in body, "a missing or non-numeric reading must not throw"


def test_app_js_measures_the_canvas_after_hydration(tmp_path, monkeypatch):
    """Height was measured while server cards still showed 'loading card…'."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        poll = app_script.split("async function pollCanvasCards()", 1)[1].split("pollCanvasCards();", 1)[0]
        hydration = poll.split("hydrated.forEach", 1)[1]
        assert "renderCanvas()" in hydration, "the canvas must be re-measured once the real bodies are in"
        render = app_script.split("function renderCanvas()", 1)[1].split("\n", 1)[0]
        assert "fitCanvasHeight()" in render


def test_app_js_states_the_output_swing_as_approximate(tmp_path, monkeypatch):
    """The LM324 headroom figure is a datasheet approximation, and the card said it as fact."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        assert "output swing about" in app_script


def test_app_js_stores_no_server_card_payload_locally(tmp_path, monkeypatch):
    """A server card's payload is re-fetched; keeping it would serve a stale card after a republish."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        save = app_script.split("function saveCanvas()", 1)[1].split("function componentBody", 1)[0]
        server_branch, local_branch = save.split("server?", 1)[1].split(":", 1)
        assert "card" not in server_branch, "a server card must be stored by id and position only"
        assert "card" in local_branch, "a local card carries its own content"


def test_app_js_renders_and_keeps_the_schematic_card(tmp_path, monkeypatch):
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        read = app_script.split("function readCanvas()", 1)[1].split("function saveCanvas()", 1)[0]
        assert "schematic" in read, "readCanvas drops schematic; layout resets on reload"
        assert "'schematic'" in app_script.split("const CARD_KINDS=", 1)[1].split(";", 1)[0]
        body = app_script.split("function cardBodyFor(item)", 1)[1].split("const baseCardBody", 1)[0]
        assert "data-schematic" in body and "card-netlist" in body


def test_app_js_rasterises_a_schematic_on_a_white_ground(tmp_path, monkeypatch):
    """A drawing that inherits the dark canvas would export as dark ink on dark."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        png = app_script.split("window.schematicPng=", 1)[1].split("const baseCardBody", 1)[0]
        assert "fillStyle='#ffffff'" in png
        assert "color:#111" in png
        assert "toBlob" in png


def test_app_js_keeps_the_schematic_blob_until_a_download_can_read_it(tmp_path, monkeypatch):
    """Revoking the object URL in the same turn as the click races WKWebView.

    The app turns the click into a download only after the navigation delegate
    runs, and the bytes are read after the save panel returns. A browser still
    downloads from the same `<a download>` click, so the filename stays.
    """
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        png = app_script.split("window.schematicPng=", 1)[1].split("const baseCardBody", 1)[0]
        assert "link.download=`schematic-${id}.png`" in png
        click = png.index("link.click()")
        revoke = png.index("URL.revokeObjectURL")
        assert "setTimeout" in png[click:revoke], "the revoke has to wait until the download has the bytes"
        # The save panel is in front of the user for as long as they take.
        # A delay of a turn is the race this is fixing.
        delay_ms = int(png[revoke:].split(",", 1)[1].split(")", 1)[0])
        assert delay_ms >= 60_000


def test_app_js_never_puts_a_solution_on_the_desk(tmp_path, monkeypatch):
    """Closing a desk card deletes it, and that must never reach finished homework."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        kinds = app_script.split("const CARD_KINDS=", 1)[1].split(";", 1)[0]
        assert "solution" not in kinds


def test_app_js_opens_the_solutions_sheet_for_a_tag(tmp_path, monkeypatch):
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        assert "/solutions?tag=" in app_script
        assert "encodeURIComponent(tag)" in app_script, "a tag must not be pasted into the URL raw"


# --- where cards land on the desk ---------------------------------------------

def _app_function(app_script: str, signature: str) -> str:
    """One app.js function, which the file keeps on a single line."""
    return app_script.split(signature, 1)[1].split("\n", 1)[0]


def test_app_js_places_each_new_card_by_the_drawn_height_of_the_ones_before(tmp_path, monkeypatch):
    """Issue #116: cards in one poll were spaced by a guessed 320px, so a 1,700px board was covered from row d."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        free = _app_function(app_script, "function freeSpot(")
        assert "320" not in free, "freeSpot guesses the height of a card it has not drawn"
        assert "cardBoxes()" in free, "freeSpot must measure the cards as they are drawn"
        place = _app_function(app_script, "function placeCard(")
        assert place.index("freeSpot(") < place.index("canvasItems.push(") < place.index("renderCanvas()"), \
            "each new card must be drawn before the next one's spot is chosen"
        poll = _app_function(app_script, "async function pollCanvasCards()")
        assert "placeCard(card)" in poll
        assert poll.index("hydrated.forEach") < poll.index("placeCard(card)"), \
            "a saved card still showing 'loading card…' is measured at its placeholder height"


def test_app_js_moves_a_covered_card_down_instead_of_leaving_it_buried(tmp_path, monkeypatch):
    """A card clips its body, so a covered part cannot be scrolled into view; saved layouts from before #116 overlap."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        settle = _app_function(app_script, "function settleCanvas()")
        assert "cardBoxes()" in settle
        assert "box.y=" in settle and ".x=" not in settle, "a covered card moves straight down, never across"
        assert "saveCanvas()" in settle, "the pulled-apart layout must be what a reload finds"
        render = _app_function(app_script, "function renderCanvas()")
        assert "settleCanvas()" in render, "every render, including the first after a reload, must leave no overlap"
        drop = _app_function(app_script, "function bindDrags()").split("head.onpointerup=", 1)[1]
        assert "settleCanvas()" in drop, "a card dropped on another must not stay on it until the next reload"


def test_app_js_measures_a_card_where_it_will_end_up(tmp_path, monkeypatch):
    """A card mid-slide reports its old offsetTop, and mid-resize its old height."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        boxes = _app_function(app_script, "function cardBoxes()")
        assert "x:item.x,y:item.y" in boxes and "el.offsetWidth" in boxes and "el.offsetHeight" in boxes
        rule = browser.get("/assets/canvas.css").text.split(".workspace-item{", 1)[1].split("}", 1)[0]
        transition = rule.split("transition:", 1)[1].split(";", 1)[0]
        assert "width" not in transition, "an animated width is read at its old value, so its height is wrong"


def test_app_js_places_nothing_on_a_desk_it_cannot_measure(tmp_path, monkeypatch):
    """On another tab the desk is display:none, so every card measures 0x0 and all would land on one spot."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        poll = _app_function(app_script, "async function pollCanvasCards()")
        assert poll.index("if(!$('#workspaceCanvas').clientWidth)return") < poll.index("placeCard(card)")
        assert poll.index("if(!$('#workspaceCanvas').clientWidth)return") < poll.index("cardsSignature=signature"), \
            "a poll that placed nothing must not be stamped as done"
        observer = _app_function(app_script, "function renderCanvas()").split("new ResizeObserver(", 1)[1]
        assert "contentRect" not in observer, \
            "the content box is 2px narrower than the border-box width a card is given, so each reload shrank it"
        assert "el.offsetWidth" in observer and "if(!w||" in observer, "a hidden card reports width 0"


def test_app_js_fit_sets_the_width_and_leaves_the_card_where_it_is(tmp_path, monkeypatch):
    """Issue #127: Fit pulled the card up to y=40, over whatever was at the top of the desk."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        size = _app_function(app_script, "function sizeIPad(")
        fit = size.split("mode==='fit'", 1)[1].split("}else{", 1)[0]
        assert "item.y" not in fit and "style.top" not in fit, "Fit moves the card up the desk"
        assert "item.x=Math.max(0,Math.min(item.x,canvas.clientWidth-item.w))" in fit, "a fitted card stays inside the canvas"
        assert "settleCanvas()" in size, "a wider card must move what it now covers out of the way"


def test_app_js_wraps_a_card_that_no_longer_fits_instead_of_sliding_it_over_its_neighbour(tmp_path, monkeypatch):
    """Issue #129: narrowing the window clamped x, so the right-hand card of a row slid over the left-hand one."""
    with _browser(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        render = _app_function(app_script, "function renderCanvas()")
        assert "Math.min(item.x,canvas.clientWidth-item.w)" not in render, "clamping x slides a card over its neighbour"
        assert "if(item.x+item.w>width)item.x=" in render, "a card past the right edge starts a new row at the left"
        assert "settleCanvas()" in render, "the wrapped card must then move below whatever it lands on"
        assert "if(width){" in render, "a desk hidden behind another tab has no width to fit cards to"
        show = _app_function(app_script, "function show(view)")
        assert "if(view==='dashboard')renderCanvas()" in show, \
            "a window resized while another tab was showing must be laid out when the desk comes back"
