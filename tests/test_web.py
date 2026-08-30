from __future__ import annotations

from fastapi.testclient import TestClient

from circuit_mcp import web


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
        assert "if(item.kind==='visual')return" in app_script
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


def test_animation_scene_lifecycle_is_persistent_and_bounded(tmp_path, monkeypatch):
    scene = {"title": "Phasor lesson", "elements": [
        {"id": "v1", "type": "phasor", "x": 100, "y": 100, "angle": 30, "color": "blue"}
    ], "steps": [{"at_ms": 0, "caption": "Rotate the voltage phasor."}]}
    with client(tmp_path, monkeypatch) as browser:
        created = browser.post("/api/animations", json={"scene": scene}).json()["animation"]
        assert created["revision"] == 1
        assert browser.get("/api/animations").json()["items"][0]["id"] == created["id"]
        scene["title"] = "Updated phasor lesson"
        updated = browser.put(f"/api/animations/{created['id']}", json={"scene": scene}).json()["animation"]
        assert updated["revision"] == 2
        assert browser.delete(f"/api/animations/{created['id']}").json()["ok"] is True
        assert browser.get("/api/animations").json()["items"] == []
        unsafe = {"title": "bad", "elements": [{"id": "x", "type": "text", "onclick": "steal()"}]}
        assert browser.post("/api/animations", json={"scene": unsafe}).status_code == 400


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
