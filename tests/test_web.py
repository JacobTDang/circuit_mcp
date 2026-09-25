from __future__ import annotations

from fastapi.testclient import TestClient

from circuit_mcp import paths, web
from circuit_mcp.cards import build_card


def client(tmp_path, monkeypatch):
    data = tmp_path / "command_center"
    monkeypatch.setattr(web, "DATA", data)
    monkeypatch.setattr(web, "FILES", data / "files")
    monkeypatch.setattr(web, "INDEX", data / "library.json")
    monkeypatch.setattr(web, "HISTORY", data / "history.jsonl")
    return TestClient(web.app, headers={"host": "localhost:2300"})


def test_dashboard_and_real_tool_execution(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        page = browser.get("/")
        assert page.status_code == 200
        assert "Circuit Command Center" in page.text
        status = browser.get("/api/status").json()
        assert status["ok"] is True
        assert "derive" in status["tools"]
        result = browser.post(
            "/api/tools/check_equivalence",
            json={"arguments": {"expr_a": "1/(s+1)", "expr_b": "1/(1+s)"}},
        ).json()
        assert result["equivalent"] is True


def test_the_bench_check_tools_are_exposed_in_the_command_center():
    assert {"compare_readings", "summing_dac_output"} <= set(web.TOOLS)


def test_dashboard_starts_as_a_manual_blank_spatial_workspace(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        page = browser.get("/")
        assert 'id="workspaceCanvas"' in page.text
        assert "click blank space to add something" in page.text
        assert 'data-spawn="ipad"' in page.text
        assert 'data-spawn="library"' in page.text
        assert "Your circuit desk is ready" not in page.text
        canvas_css = browser.get("/assets/canvas.css")
        assert canvas_css.status_code == 200
        assert ".workspace-item" in canvas_css.text
        assert "resize:horizontal" in canvas_css.text
        app_script = browser.get("/assets/app.js").text
        assert "data-generate-visual" in app_script
        assert "/api/showman/generate" in app_script
        assert "<video controls playsinline" in app_script
        assert "hydrateVisualCards" in app_script
        assert "videoMeta" in app_script


def test_page_title_block_is_removed_and_refresh_stays_reachable(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        page = browser.get("/")
        assert "LOCAL CIRCUIT LEARNING WORKSPACE" not in page.text
        assert "<header>" not in page.text
        assert 'id="title"' not in page.text
        assert "app.js?v=canvas-15" not in page.text
        aside = page.text.split("<aside>", 1)[1].split("</aside>", 1)[0]
        assert 'id="refresh"' in aside
        assert 'LOCAL · PORT <span id="localPort">' in aside
        app_script = browser.get("/assets/app.js").text
        assert "$('#title').textContent=names[view]" not in app_script
        assert "const t=$('#title');if(t)t.textContent=names[view]" in app_script
        assert "$('#refresh').onclick=refresh" in app_script
        assert "localPort.textContent=location.port" in app_script


def test_workspace_canvas_grows_to_contain_its_cards_instead_of_clipping(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        canvas_css = browser.get("/assets/canvas.css").text
        rule = canvas_css.split(".workspace-canvas{", 1)[1].split("}", 1)[0]
        assert "overflow:hidden" not in rule
        assert "205px" not in rule
        app_script = browser.get("/assets/app.js").text
        assert "canvas.style.height=''" in app_script
        # The height is measured from the rendered cards, in one place, so it can
        # be measured again after hydration replaces a placeholder body.
        fit = app_script.split("function fitCanvasHeight()", 1)[1].split("\n", 1)[0]
        assert "el.offsetTop+el.offsetHeight" in fit
        assert "canvas.style.height=`${bottom+40}px`" in fit


def test_app_js_surfaces_upstream_errors_and_guards_optional_fields(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        assert "function apiErrorMessage(data)" in app_script
        assert "throw new Error(apiErrorMessage(data))" in app_script
        assert "data.detail||'Request failed'" not in app_script
        assert "data.errors" in app_script
        assert "Number.isFinite(result.durationSec)" in app_script
        assert "Number.isFinite(result.fps)" in app_script


def test_app_js_preserves_typed_briefs_without_a_document_wide_observer(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        assert "MutationObserver" not in app_script
        # A visual keeps its typed brief and a server card keeps its payload: neither
        # is rebuilt from state on a status refresh.
        assert "if(item.kind==='visual'||item.server)return" in app_script
        assert "hydrateVisualCards()" in app_script


def test_app_js_persists_typed_briefs_before_generate_is_clicked(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        app_script = browser.get("/assets/app.js").text
        assert "addEventListener('input',event=>{const brief=event.target.closest('.visual-brief')" in app_script
        assert "item.brief=brief.value;saveCanvas()" in app_script
        assert "setTimeout(()=>{item.brief=brief.value;saveCanvas()},300)" in app_script


def test_legacy_animation_assets_are_not_loaded_by_the_workspace(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        page = browser.get("/")
        assert "/assets/animation.js" not in page.text
        assert "loadAnimations" not in page.text
        assert browser.get("/assets/animation.js").status_code == 404


def test_rendered_visuals_are_listed_for_the_board(tmp_path, monkeypatch):
    from circuit_mcp import web

    render = {"video": {"key": "videos/abc.mp4"}, "durationSec": 20.0, "fps": 30,
              "width": 960, "height": 540, "spec": {"specVersion": 1}}
    with client(tmp_path, monkeypatch) as browser:
        assert browser.get("/api/visuals").json()["items"] == []
        stored = web._db().create_visual("explain RC charging", render)
        items = browser.get("/api/visuals").json()["items"]
        assert [item["id"] for item in items] == [stored["id"]]
        # The board must never receive an upstream file:// handle.
        assert items[0]["url"] == "/api/showman/objects/videos/abc.mp4"
        assert "file://" not in browser.get("/api/visuals").text


def test_the_legacy_animation_routes_are_retired(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        assert browser.get("/api/animations").status_code == 404
        assert browser.post("/api/animations", json={"scene": {}}).status_code == 404


def test_upload_search_preview_and_delete_stay_in_private_store(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        response = browser.post(
            "/api/library",
            data={"category": "lecture"},
            files={"file": ("week-1.md", b"# RC filters\nTime constant tau=RC", "text/markdown")},
        )
        assert response.status_code == 200
        item = response.json()["item"]
        assert item["name"] == "week-1.md"
        assert (web.FILES / f"{item['id']}.md").read_text().startswith("# RC")
        found = browser.get("/api/library?q=time%20constant").json()["items"]
        assert [entry["id"] for entry in found] == [item["id"]]
        preview = browser.get(f"/api/library/{item['id']}/file")
        assert preview.content == b"# RC filters\nTime constant tau=RC"
        assert browser.delete(f"/api/library/{item['id']}").json()["ok"] is True
        assert not list(web.FILES.glob("*"))


def test_upload_rejects_unsafe_type_and_oversized_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "MAX_UPLOAD", 8)
    with client(tmp_path, monkeypatch) as browser:
        assert browser.post("/api/library", files={"file": ("bad.exe", b"x")}).status_code == 415
        assert browser.post("/api/library", files={"file": ("large.txt", b"123456789")}).status_code == 413
        assert not list(web.FILES.glob("*"))


def test_unknown_tool_and_invalid_host_are_rejected(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        assert browser.post("/api/tools/nope", json={"arguments": {}}).status_code == 404
        assert browser.get("/api/library/not-a-real-id/file").status_code == 404
    hostile = TestClient(web.app, headers={"host": "attacker.example"})
    assert hostile.get("/").status_code == 400


def test_ocr_response_explicitly_warns_that_full_pages_are_out_of_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(web.OCR_WORKER, "call", lambda request: {"ok": True, "latex": "x^2"})
    with client(tmp_path, monkeypatch) as browser:
        uploaded = browser.post(
            "/api/library",
            files={"file": ("formula.png", b"\x89PNG\r\n\x1a\nformula", "image/png")},
        ).json()["item"]
        result = browser.post(f"/api/library/{uploaded['id']}/ocr").json()
        assert result["ok"] is True
        assert "tightly cropped" in result["input_scope"]
        assert "does not read full pages" in result["scope_warning"]


def test_ipad_capture_uses_only_saved_rectangle_and_stores_exact_png(tmp_path, monkeypatch):
    calls = []
    png = b"\x89PNG\r\n\x1a\nexact-frame"
    monkeypatch.setattr(
        web, "workspace_configuration",
        lambda: {"ok": True, "display": 2, "x": 10, "y": 20, "width": 300, "height": 400},
    )
    def capture(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "png": png, "sha256": "abc", "region": [10, 20, 300, 400]}
    monkeypatch.setattr(web, "_capture_workspace", capture)
    with client(tmp_path, monkeypatch) as browser:
        result = browser.post("/api/workspace/capture")
        assert result.status_code == 200
        item = result.json()["item"]
        assert browser.get(f"/api/library/{item['id']}/file").content == png
    assert calls == [{"display": 2, "allow_full_display": False, "x": 10, "y": 20, "width": 300, "height": 400}]


def test_ipad_capture_requires_privacy_scoped_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "workspace_configuration", lambda: {"ok": False})
    with client(tmp_path, monkeypatch) as browser:
        assert browser.post("/api/workspace/capture").status_code == 409


def test_ipad_capture_can_use_explicit_dedicated_display(tmp_path, monkeypatch):
    calls = []
    png = b"\x89PNG\r\n\x1a\ndisplay-frame"
    monkeypatch.setattr(web, "workspace_configuration", lambda: {
        "ok": True, "mode": "display", "display": 2,
    })
    monkeypatch.setattr(web, "_capture_workspace", lambda **kwargs: (
        calls.append(kwargs) or {"ok": True, "png": png, "selection": {"kind": "display", "display": 2}}
    ))
    with client(tmp_path, monkeypatch) as browser:
        response = browser.post("/api/workspace/capture")
        assert response.status_code == 200
    assert calls == [{"display": 2, "allow_full_display": True}]


def test_live_ipad_source_control_and_persistent_capture(tmp_path, monkeypatch):
    png = b"\x89PNG\r\n\x1a\nlive-ipad"
    status = {"ok": True, "active_source": "airplay", "airplay": {
        "running": True, "connected": True, "pin": "1234"}, "usb": {"connected": False}}
    monkeypatch.setattr(web.IPAD_CAPTURE, "status", lambda: status)
    monkeypatch.setattr(web.IPAD_CAPTURE, "start_airplay", lambda: status)
    monkeypatch.setattr(web.IPAD_CAPTURE, "stop_airplay", lambda: {**status, "active_source": None})
    monkeypatch.setattr(web.IPAD_CAPTURE, "capture", lambda source="auto": {
        "ok": True, "source": "airplay", "mime_type": "image/png", "png": png,
        "bytes": len(png), "sha256": "abc", "captured_at": 1,
    })
    with client(tmp_path, monkeypatch) as browser:
        assert browser.get("/api/ipad/status").json()["active_source"] == "airplay"
        assert browser.post("/api/ipad/receiver/start").status_code == 200
        live = browser.get("/api/ipad/frame")
        assert live.status_code == 200
        assert live.content == png
        assert live.headers["content-type"] == "image/png"
        assert "no-store" in live.headers["cache-control"]
        assert browser.get("/api/library").json()["items"] == []
        captured = browser.post("/api/ipad/capture", json={"source": "auto"}).json()["item"]
        assert captured["source"] == "ipad_airplay"
        assert browser.get(f"/api/library/{captured['id']}/file").content == png
        assert browser.post("/api/ipad/receiver/stop").status_code == 200


def test_problem_attempt_tool_evidence_and_progress_workflow(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        document = browser.post(
            "/api/library", data={"category": "homework"},
            files={"file": ("rc.md", b"Find the RC pole", "text/markdown")},
        ).json()["item"]
        problem = browser.post("/api/problems", json={
            "title": "RC pole", "topic": "filters", "prompt": "Find the pole",
            "document_id": document["id"],
        }).json()["problem"]
        confirmed = browser.patch(f"/api/problems/{problem['id']}/interpretation", json={
            "circuit_interpretation": "series R, shunt C", "status": "confirmed",
        }).json()["problem"]
        assert confirmed["status"] == "confirmed"
        attempt = browser.post(f"/api/problems/{problem['id']}/attempts", json={
            "actor": "student", "answer": "-1/RC",
        }).json()["attempt"]
        checked = browser.post("/api/tools/check_equivalence", json={
            "attempt_id": attempt["id"],
            "arguments": {"expr_a": "-1/(R*C)", "expr_b": "-1/(R*C)"},
        }).json()
        assert checked["equivalent"] is True
        assert len(checked["evidence_id"]) == 32
        completed = browser.patch(f"/api/attempts/{attempt['id']}", json={
            "answer": "-1/RC", "status": "correct",
        }).json()["attempt"]
        assert completed["status"] == "correct"
        history = browser.get(f"/api/problems/{problem['id']}/attempts").json()["items"]
        assert history[0]["tool_calls"][0]["id"] == checked["evidence_id"]
        progress = browser.get("/api/progress").json()
        assert progress["problems"] == {"confirmed": 1}
        assert progress["attempts"] == {"correct": 1}
        context = browser.get("/api/context?q=RC").json()
        assert context["documents"][0]["id"] == document["id"]
        assert context["problems"][0]["id"] == problem["id"]
        integrity = browser.get("/api/database/integrity").json()
        assert integrity["ok"] is True
        assert integrity["files"]["checked"] == 1
        backup = browser.post("/api/database/backup").json()
        assert backup["ok"] is True
        assert (web.DATA / "backups" / backup["path"].split("/")[-1]).exists()


def test_ocr_revision_can_be_confirmed_with_a_correction(tmp_path, monkeypatch):
    monkeypatch.setattr(web.OCR_WORKER, "call", lambda request: {
        "ok": True, "latex": "1/(1-sRC)", "model": "unimernet_small",
        "device": "mps", "inference_seconds": 0.01,
    })
    with client(tmp_path, monkeypatch) as browser:
        document = browser.post(
            "/api/library", files={"file": ("formula.png", b"\x89PNG\r\n\x1a\nx", "image/png")},
        ).json()["item"]
        ocr = browser.post(f"/api/library/{document['id']}/ocr").json()
        assert ocr["confirmation_status"] == "unconfirmed"
        confirmed = browser.post(f"/api/transcriptions/{ocr['transcription_id']}/confirm", json={
            "corrected_content": "1/(1+sRC)",
        }).json()["transcription"]
        assert confirmed["status"] == "confirmed"
        assert confirmed["content"] == "1/(1+sRC)"


def test_soft_delete_moves_file_to_recoverable_trash(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        item = browser.post(
            "/api/library", files={"file": ("keep.md", b"recover me", "text/markdown")},
        ).json()["item"]
        assert browser.delete(f"/api/library/{item['id']}").status_code == 200
        assert not (web.FILES / f"{item['id']}.md").exists()
        assert (web.DATA / "trash" / f"{item['id']}.md").read_bytes() == b"recover me"


def test_the_canvas_reconciles_instead_of_rebuilding_every_card(tmp_path, monkeypatch):
    """Issue #19: recreating a card tears down a playing video and a typed brief."""
    with client(tmp_path, monkeypatch) as browser:
        script = browser.get("/assets/app.js").text
        assert "querySelectorAll('.workspace-item').forEach(x=>x.remove())" not in script, \
            "a blanket teardown destroys live card state on every render"
        assert "existing" in script and "keep" in script, "render must reuse elements it already has"
        # a reused element must not accumulate a second ResizeObserver
        assert script.count("new ResizeObserver") == 1


def test_the_suite_cannot_write_to_the_real_command_center_store(isolated_command_center):
    """A route that records data must never land rows in the developer's database."""
    project_store = paths.REPO_ROOT / ".local" / "command_center"

    assert web.DATA == isolated_command_center
    assert web.DATA != project_store
    assert project_store not in web.DATA.parents


def test_generate_records_the_visual_into_the_isolated_store(monkeypatch):
    """The route that persists a render must land it in the test's own store."""
    project_store = paths.REPO_ROOT / ".local" / "command_center"
    before = _visual_count(project_store)

    monkeypatch.setattr(web.SHOWMAN, "start", lambda *a, **k: {"ok": True, "authoring": "openrouter"})
    monkeypatch.setattr(web.SHOWMAN, "request_json", lambda path, payload, timeout: (
        200, {"video": {"key": "videos/x.mp4"}, "durationSec": 5, "fps": 30, "spec": {}}))
    browser = TestClient(web.app, headers={"host": "localhost:2300"})

    assert browser.post("/api/showman/generate", json={"brief": "explain an RC circuit"}).status_code == 200
    assert (web.DATA / "circuit_mcp.sqlite3").exists(), "the row belongs in the isolated store"
    assert _visual_count(project_store) == before, "the real database must be untouched"


def _visual_count(store) -> int:
    import sqlite3

    database = store / "circuit_mcp.sqlite3"
    if not database.exists():
        return 0
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return connection.execute("SELECT count(*) FROM visual_assets").fetchone()[0]


def test_posting_attempt_id_inside_arguments_is_refused(tmp_path, monkeypatch):
    """The web path records the call itself; a tool that also recorded it would double-count."""
    with client(tmp_path, monkeypatch) as browser:
        response = browser.post(
            "/api/tools/check_equivalence",
            json={"arguments": {"expr_a": "a", "expr_b": "a", "attempt_id": "x"}},
        )
    assert response.status_code == 422
    assert "attempt_id" in response.json()["detail"]


def test_the_canvas_feed_leaves_solutions_off_the_desk(tmp_path, monkeypatch):
    """A solution is homework; the board deletes what it closes, so it never sees one."""
    with client(tmp_path, monkeypatch) as browser:
        database = web._db()
        problem = database.create_problem("Exp 1 gain", "op-amps", "find the gain")
        database.create_card("solution", "Exp 1 gain", {"given": [], "steps": [], "answer": {}},
                             problem["id"])
        database.create_card("formula", "gain", {"items": []}, problem["id"])

        served = browser.get("/api/canvas").json()["items"]

    assert [card["kind"] for card in served] == ["formula"]
    assert [card["kind"] for card in database.list_cards()] == ["formula", "solution"]


def _hw1_sheet(browser):
    """The Module 2 HW1 fixture, stored the way the agent would store it."""
    from tests.fixtures import module2_hw1 as hw

    database = web._db()
    for entry in hw.PROBLEMS:
        problem = database.create_problem(entry["title"], entry["topic"], entry["prompt"],
                                          source_page=entry["page"])
        database.tag_problem(problem["id"], hw.TAG)
        card = build_card("solution", entry["title"], entry["solution"])
        database.create_card(card["kind"], card["title"], card["payload"], problem["id"])
    return database


def test_the_sheet_renders_all_eight_hw1_problems_in_page_order(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        _hw1_sheet(browser)
        page = browser.get("/solutions?tag=m2-hw1")

    assert page.status_code == 200
    from tests.fixtures import module2_hw1 as hw
    positions = [page.text.index(entry["title"]) for entry in hw.PROBLEMS]
    assert positions == sorted(positions), "problems must appear in page order"
    assert page.text.count('class="answer"') == 8
    assert "1 MΩ" not in page.text  # the answer is shown as written, not re-formatted
    assert "V/V" in page.text and "Ω" in page.text
    assert "<math" in page.text


def test_the_sheet_says_so_when_a_problem_has_no_solution(tmp_path, monkeypatch):
    """A sheet that silently omits an unfinished problem hides the thing worth seeing."""
    with client(tmp_path, monkeypatch) as browser:
        database = web._db()
        problem = database.create_problem("2.97 offset", "op-amps", "find Vos", source_page=9)
        database.tag_problem(problem["id"], "m2-hw2")
        page = browser.get("/solutions?tag=m2-hw2")

    assert "2.97 offset" in page.text
    assert "no solution yet" in page.text


def test_the_sheet_needs_an_assignment_tag(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        assert browser.get("/solutions").status_code == 400


def test_the_sheet_escapes_every_text_field(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as browser:
        database = web._db()
        problem = database.create_problem("<script>alert(1)</script>", "op-amps",
                                          "prompt <b>bold</b>", source_page=1)
        database.tag_problem(problem["id"], "m2-hw3")
        page = browser.get("/solutions?tag=m2-hw3")

    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


def test_the_sheet_stylesheet_prints_on_us_letter(tmp_path, monkeypatch):
    """The one page that leaves the app: it has to come out of a printer readable."""
    with client(tmp_path, monkeypatch) as browser:
        css = browser.get("/assets/solution.css").text

    assert "@page { size: letter" in css
    assert "@media print" in css
    assert "header.sheet button { display: none; }" in css, "the export button must not print"
    assert "background: #fff" in css, "a dark ground prints as a black rectangle"
    assert "break-inside: avoid" in css, "a problem split across pages is hard to grade"


def test_the_sheet_shows_the_checks_that_stand_behind_an_answer(tmp_path, monkeypatch):
    """The evidence line is the point of recording tool calls against an attempt."""
    with client(tmp_path, monkeypatch) as browser:
        database = web._db()
        problem = database.create_problem("Exp 1 gain", "op-amps", "find the gain", source_page=1)
        database.tag_problem(problem["id"], "m2-evidence")
        attempt = database.create_attempt(problem["id"], "student")
        database.record_tool_call("check_derivation", {"steps": ["1 + R2/R1"], "truth": "16"},
                                  {"ok": True, "kind": "ok"}, 12.0, attempt["id"])
        database.record_tool_call("check_equivalence", {"expr_a": "a", "expr_b": "b"},
                                  {"ok": True, "equivalent": False, "oracle": "numeric"}, 8.0, attempt["id"])
        database.record_tool_call("simulate_spice", {"netlist": "R1 1 0 1k"},
                                  {"ok": True, "points": []}, 40.0, attempt["id"])
        card = build_card("solution", "Exp 1 gain", {
            "given": [{"name": "R1", "value": 1000, "unit": "Ω"},
                      {"name": "R2", "value": 15000, "unit": "Ω"}],
            "steps": [{"expression": "1 + R2/R1"}, {"expression": "16"}],
            "answer": {"expression": "16", "unit": "V/V"},
            "attempt_id": attempt["id"],
        })
        database.create_card(card["kind"], card["title"], card["payload"], problem["id"])

        page = browser.get("/solutions?tag=m2-evidence").text

    assert "checked with" in page
    assert "check_derivation" in page and "check_equivalence" in page and "simulate_spice" in page
    assert page.count(">pass<") == 1 and page.count(">fail<") == 1 and page.count(">computed<") == 1
    assert "no checks recorded" not in page


def test_a_solution_whose_attempt_has_no_checks_says_so(tmp_path, monkeypatch):
    """Silence would read as verified; the sheet has to say nothing was recorded."""
    with client(tmp_path, monkeypatch) as browser:
        database = web._db()
        problem = database.create_problem("Exp 2 gain", "op-amps", "find the gain", source_page=1)
        database.tag_problem(problem["id"], "m2-silent")
        attempt = database.create_attempt(problem["id"], "student")
        card = build_card("solution", "Exp 2 gain", {
            "given": [], "steps": [{"expression": "16"}],
            "answer": {"expression": "16", "unit": "V/V"}, "attempt_id": attempt["id"]})
        database.create_card(card["kind"], card["title"], card["payload"], problem["id"])

        page = browser.get("/solutions?tag=m2-silent").text

    assert "no checks recorded against this attempt" in page


def test_healthz_answers_without_touching_the_desk(tmp_path, monkeypatch):
    """A launcher polls this until the server is up, so it must be cheap and always 200."""
    response = client(tmp_path, monkeypatch).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
