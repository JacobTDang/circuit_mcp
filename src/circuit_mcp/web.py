"""Local-only web command center for course files and circuit tools."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pypdf import PdfReader
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .ocr_client import OCR_WORKER
from .storage import CommandCenterDB, StorageError
from .capture import CaptureError, capture_workspace as _capture_workspace
from .workspace import configure_display as _configure_display
from .ipad_capture import IPAD_CAPTURE, IPadCaptureError
from .showman import (SHOWMAN, ShowmanArtifact, ShowmanConnectionError,
                      ShowmanMissingObject, ShowmanTimeoutError)
from .retention import sweep_if_due
from .server import (
    alias_frequency,
    bjt_emitter_follower,
    characterize_transfer,
    check_derivation,
    check_equivalence,
    check_setup,
    circuit_equations,
    converter_metrics,
    dac_output,
    derive,
    import_waveform_csv,
    instrument_query,
    instrument_status,
    opamp_limits,
    quantize,
    rectifier_metrics,
    relaxation_oscillator,
    simulate_spice,
    spectrum_metrics,
    transimpedance,
    configure_workspace,
    ocr_status,
    workspace_configuration,
    workspace_status,
)

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).with_name("static")
DATA = ROOT / ".local" / "command_center"
FILES = DATA / "files"
INDEX = DATA / "library.json"
HISTORY = DATA / "history.jsonl"
DATABASE = DATA / "circuit_mcp.sqlite3"
TRASH = DATA / "trash"
MAX_UPLOAD = 50 * 1024 * 1024
ALLOWED = {
    ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".txt": "text/plain", ".md": "text/markdown",
    ".csv": "text/csv",
}
@asynccontextmanager
async def _lifespan(_: FastAPI):
    try:
        yield
    finally:
        IPAD_CAPTURE.stop_airplay()
        SHOWMAN.stop()


app = FastAPI(title="Circuit Command Center", docs_url=None, redoc_url=None,
              lifespan=_lifespan)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "localhost:2300", "127.0.0.1:2300"],
)
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


class ToolRequest(BaseModel):
    arguments: dict[str, Any]
    attempt_id: str | None = None


class ShowmanAuthorRequest(BaseModel):
    brief: str


class ShowmanPreviewRequest(BaseModel):
    spec: dict[str, Any]
    frame: int = 0


class ShowmanRenderRequest(BaseModel):
    spec: dict[str, Any]


class ShowmanGenerateRequest(BaseModel):
    brief: str


class ProblemRequest(BaseModel):
    title: str
    topic: str
    prompt: str
    document_id: str | None = None
    circuit_interpretation: str = ""
    status: str = "draft"
    source_page: int | None = None


class InterpretationRequest(BaseModel):
    circuit_interpretation: str
    status: str = "confirmed"


class AttemptRequest(BaseModel):
    actor: str
    answer: str = ""
    status: str = "working"


class AttemptCompleteRequest(BaseModel):
    answer: str
    status: str
    first_divergence: str | None = None


class ConfirmationRequest(BaseModel):
    corrected_content: str | None = None


class DisplayRequest(BaseModel):
    display: int


class SourceRequest(BaseModel):
    source: str = "auto"


def _db() -> CommandCenterDB:
    database = CommandCenterDB(DATA / "circuit_mcp.sqlite3")
    database.prepare(INDEX, HISTORY)
    return database


def _prepare() -> None:
    FILES.mkdir(parents=True, exist_ok=True)
    (DATA / "trash").mkdir(parents=True, exist_ok=True)
    _db()


def _read_index() -> list[dict[str, Any]]:
    return _db().list_documents(limit=500)


def _record(kind: str, name: str, ok: bool, entity_type: str | None = None,
            entity_id: str | None = None, details: dict[str, Any] | None = None) -> None:
    _db().record_event(kind, name, ok, entity_type, entity_id, details)


def _extract(path: Path, extension: str) -> tuple[str, int | None]:
    if extension == ".pdf":
        reader = PdfReader(path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)[:2_000_000], len(reader.pages)
    if extension in {".txt", ".md", ".csv"}:
        return path.read_text(errors="replace")[:2_000_000], None
    return "", None


TOOLS: dict[str, Callable[..., Any]] = {
    "derive": derive, "check_equivalence": check_equivalence,
    "check_derivation": check_derivation, "circuit_equations": circuit_equations,
    "check_setup": check_setup, "simulate_spice": simulate_spice,
    "characterize_transfer": characterize_transfer,
    "converter_metrics": converter_metrics, "quantize": quantize,
    "spectrum_metrics": spectrum_metrics, "opamp_limits": opamp_limits,
    "rectifier_metrics": rectifier_metrics,
    "bjt_emitter_follower": bjt_emitter_follower,
    "relaxation_oscillator": relaxation_oscillator,
    "dac_output": dac_output, "alias_frequency": alias_frequency,
    "transimpedance": transimpedance,
    "import_waveform_csv": import_waveform_csv,
    "workspace_status": workspace_status,
    "workspace_configuration": workspace_configuration,
    "configure_workspace": configure_workspace,
    "ocr_status": ocr_status,
    "instrument_status": instrument_status,
    "instrument_query": instrument_query,
}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status() -> dict[str, Any]:
    items = _read_index()
    return {
        "ok": True, "tools": sorted(TOOLS), "tool_count": len(TOOLS),
        "library_count": len(items), "database": _db().integrity(), "workspace": workspace_status(),
        "workspace_configuration": workspace_configuration(),
        "ocr": OCR_WORKER.availability(), "instruments": instrument_status(),
    }


@app.get("/api/showman/status")
def showman_status(start: bool = False) -> dict[str, Any]:
    """Report the local renderer state; optionally start the pinned worker."""
    return SHOWMAN.start() if start else SHOWMAN.status()


OFFLINE_AUTHORING = (
    "The Showman worker has no authoring model configured, so it can only produce "
    "generic template lessons unrelated to your brief. Set OPENROUTER_API_KEY in .env "
    "and restart the command center."
)


def _authoring_worker() -> dict[str, Any]:
    """Return a worker that can actually author the caller's brief, or fail loudly."""
    state = SHOWMAN.start()
    if not state.get("ok"):
        raise HTTPException(503, state.get("error") or "Showman is unavailable")
    if state.get("authoring") == "offline":
        raise HTTPException(503, OFFLINE_AUTHORING)
    return state


@app.post("/api/showman/author")
def showman_author(request: ShowmanAuthorRequest) -> Response:
    brief = request.brief.strip()
    if not brief or len(brief) > 20_000:
        raise HTTPException(400, "brief must contain 1 to 20000 characters")
    _authoring_worker()
    try:
        status_code, result = SHOWMAN.request_json("/author", {"brief": brief}, timeout=120)
    except ShowmanTimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    except ShowmanConnectionError as exc:
        raise HTTPException(502, str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(content=json.dumps(result), status_code=status_code, media_type="application/json")


@app.post("/api/showman/preview")
def showman_preview(request: ShowmanPreviewRequest) -> Response:
    try: png = SHOWMAN.preview(request.spec, request.frame)
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    except ShowmanTimeoutError as exc: raise HTTPException(504, str(exc)) from exc
    except RuntimeError as exc: raise HTTPException(502, str(exc)) from exc
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


def _localize_object_reference(value: Any) -> Any:
    """Rewrite every object reference anywhere in a Showman payload to the local proxy.

    Showman's LocalObjectStorage returns a raw upstream URL (a file:// path when
    no public URL is configured) alongside each object's content-addressed key.
    That upstream URL must never reach the browser, so any dict carrying a
    string "key" -- video, captions, poster, or any future artifact, at any
    nesting depth -- has its "url" replaced with the local proxy path.
    """
    if isinstance(value, dict):
        localized = {field: _localize_object_reference(item) for field, item in value.items()}
        key = localized.get("key")
        if isinstance(key, str):
            localized["url"] = f"/api/showman/objects/{key}"
        return localized
    if isinstance(value, list):
        return [_localize_object_reference(item) for item in value]
    return value


def _localize_showman_result(result: dict[str, Any]) -> dict[str, Any]:
    localized = _localize_object_reference(result)
    video = localized.get("video")
    if isinstance(video, dict) and isinstance(video.get("url"), str):
        localized["videoUrl"] = video["url"]
    return localized


@app.post("/api/showman/render")
def showman_render(request: ShowmanRenderRequest) -> Response:
    try: status_code, result = SHOWMAN.request_json("/render", {"spec": request.spec}, timeout=600)
    except ShowmanTimeoutError as exc: raise HTTPException(504, str(exc)) from exc
    except ShowmanConnectionError as exc: raise HTTPException(502, str(exc)) from exc
    except (RuntimeError, ValueError) as exc: raise HTTPException(503, str(exc)) from exc
    return Response(content=json.dumps(_localize_showman_result(result)), status_code=status_code, media_type="application/json")


@app.post("/api/showman/generate")
def showman_generate(request: ShowmanGenerateRequest) -> Response:
    """Turn one brief into one local video. The brief is never substituted."""
    brief = request.brief.strip()
    if not brief or len(brief) > 20_000:
        raise HTTPException(400, "brief must contain 1 to 20000 characters")
    _authoring_worker()
    try:
        status_code, result = SHOWMAN.request_json("/generate", {"brief": brief}, timeout=600)
    except ShowmanTimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    except ShowmanConnectionError as exc:
        raise HTTPException(502, str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc
    localized = _localize_showman_result(result)
    if status_code < 400 and isinstance(result.get("video"), dict):
        # Track it, so a render started here is not mistaken for an orphan and swept.
        database = _db()
        try:
            localized["visualId"] = database.create_visual(brief, result)["id"]
        except StorageError as exc:
            raise HTTPException(500, f"render succeeded but could not be recorded: {exc}") from exc
        sweep_if_due(database, SHOWMAN.data_dir / "objects")
    return Response(content=json.dumps(localized), status_code=status_code, media_type="application/json")


_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _byte_range(header: str | None) -> tuple[int, int | None] | None:
    """Parse a single byte range. A header we cannot read means serve it whole."""
    if not header:
        return None
    match = _RANGE.match(header.strip())
    if match is None:
        return None
    first, last = match.group(1), match.group(2)
    if not first:
        return None if not last else (-int(last), None)
    return int(first), int(last) if last else None


@app.get("/api/showman/objects/{key:path}")
def showman_object(key: str, request: Request) -> Response:
    """Serve one artifact, honouring byte ranges so a video can be seeked."""
    try:
        artifact = SHOWMAN.object_response(key, _byte_range(request.headers.get("range")))
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    except ShowmanMissingObject as exc: raise HTTPException(404, str(exc)) from exc
    except ShowmanTimeoutError as exc: raise HTTPException(504, str(exc)) from exc
    except ShowmanConnectionError as exc: raise HTTPException(502, str(exc)) from exc
    except RuntimeError as exc: raise HTTPException(502, str(exc)) from exc
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=31536000, immutable"}
    if artifact.content_range:
        headers["Content-Range"] = artifact.content_range
    if artifact.length is not None:
        headers["Content-Length"] = str(artifact.length)
    return StreamingResponse(artifact.chunks, status_code=artifact.status,
                             media_type=artifact.media_type, headers=headers)


@app.get("/api/library")
def library(q: str = "", category: str = "") -> dict[str, Any]:
    try:
        items = _db().list_documents(q, category, 500)
    except StorageError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "items": items}


@app.post("/api/library")
async def upload(file: UploadFile = File(...), category: str = Form("homework")) -> dict[str, Any]:
    _prepare()
    if category not in {"homework", "lecture", "reference", "solution"}:
        raise HTTPException(400, "invalid category")
    original = Path(file.filename or "upload").name
    extension = Path(original).suffix.lower()
    if extension not in ALLOWED:
        raise HTTPException(415, f"allowed types: {sorted(ALLOWED)}")
    identifier = uuid.uuid4().hex
    target = FILES / f"{identifier}{extension}"
    size = 0
    digest = hashlib.sha256()
    try:
        with target.open("xb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "file exceeds 50 MB")
                stream.write(chunk)
                digest.update(chunk)
        text, pages = _extract(target, extension)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    item = {
        "id": identifier, "name": original, "category": category,
        "extension": extension, "media_type": ALLOWED[extension], "size": size,
        "created": time.time(), "pages": pages, "sha256": digest.hexdigest(),
        "relative_path": f"files/{identifier}{extension}", "source": "upload",
    }
    try:
        stored = _db().add_document(item, text)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    _record("upload", original, True, "document", identifier)
    return {"ok": True, "item": stored}


def _item(identifier: str) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{32}", identifier) is None:
        raise HTTPException(404)
    try:
        return _db().get_document(identifier, include_text=False)
    except StorageError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/library/{identifier}/file")
def uploaded_file(identifier: str) -> FileResponse:
    item = _item(identifier)
    path = FILES / f"{identifier}{item['extension']}"
    return FileResponse(path, media_type=item["media_type"], filename=item["name"], content_disposition_type="inline")


@app.delete("/api/library/{identifier}")
def delete_upload(identifier: str) -> dict[str, Any]:
    item = _item(identifier)
    source = FILES / f"{identifier}{item['extension']}"
    target = DATA / "trash" / f"{identifier}{item['extension']}"
    if source.exists(): os.replace(source, target)
    try:
        _db().delete_document(identifier)
    except Exception:
        if target.exists(): os.replace(target, source)
        raise
    _record("delete", item["name"], True, "document", identifier, {"recoverable": True})
    return {"ok": True}


@app.post("/api/library/{identifier}/ocr")
def ocr_upload(identifier: str) -> dict[str, Any]:
    item = _item(identifier)
    if item["extension"] != ".png":
        raise HTTPException(400, "OCR currently requires a tightly cropped PNG formula")
    result = OCR_WORKER.call({"action": "transcribe", "png": (FILES / f"{identifier}.png").read_bytes()})
    result["input_scope"] = "single tightly cropped mathematical expression"
    result["scope_warning"] = (
        "UniMERNet does not read full pages, prose, or circuit connectivity. "
        "Use an agent's vision for the page and confirm its interpretation."
    )
    if result.get("ok") and str(result.get("latex", "")).strip():
        transcription = _db().add_transcription(
            identifier, str(result["latex"]), "formula_ocr", "latex",
            str(result.get("model", "unimernet")), result.get("device"),
            float(result.get("inference_seconds", 0)) * 1000,
        )
        result["transcription_id"] = transcription["id"]
        result["confirmation_status"] = transcription["status"]
    _record("ocr", item["name"], bool(result.get("ok")), "document", identifier)
    return result


@app.post("/api/workspace/capture")
def capture_ipad() -> dict[str, Any]:
    configuration = workspace_configuration()
    if not configuration.get("ok"):
        raise HTTPException(409, "Select a mirrored display or configure a capture rectangle first.")
    try:
        if configuration.get("mode") == "display":
            captured = _capture_workspace(display=configuration["display"], allow_full_display=True)
        else:
            captured = _capture_workspace(
                display=configuration["display"], allow_full_display=False,
                x=configuration["x"], y=configuration["y"],
                width=configuration["width"], height=configuration["height"],
            )
    except CaptureError as exc:
        raise HTTPException(503, str(exc)) from exc
    identifier = uuid.uuid4().hex
    target = FILES / f"{identifier}.png"
    _prepare()
    target.write_bytes(captured.pop("png"))
    item = {
        "id": identifier, "name": f"iPad capture {time.strftime('%Y-%m-%d %H-%M-%S')}.png",
        "category": "homework", "extension": ".png", "media_type": "image/png",
        "size": target.stat().st_size, "created": time.time(), "pages": None,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "relative_path": f"files/{identifier}.png", "source": "ipad_capture",
        "capture": captured,
    }
    try:
        stored = _db().add_document(item, "")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    _record("capture", item["name"], True, "document", identifier)
    return {"ok": True, "item": stored}


@app.post("/api/workspace/display")
def select_mirrored_display(request: DisplayRequest) -> dict[str, Any]:
    try:
        result = _configure_display(request.display)
    except CaptureError as exc:
        raise HTTPException(400, str(exc)) from exc
    _record("workspace", f"mirrored display {request.display}", True)
    return result


@app.get("/api/ipad/status")
def ipad_status() -> dict[str, Any]:
    return IPAD_CAPTURE.status()


@app.post("/api/ipad/receiver/start")
def ipad_receiver_start() -> dict[str, Any]:
    try:
        return IPAD_CAPTURE.start_airplay()
    except IPadCaptureError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/ipad/receiver/stop")
def ipad_receiver_stop() -> dict[str, Any]:
    return IPAD_CAPTURE.stop_airplay()


@app.post("/api/ipad/capture")
def ipad_screen_capture(request: SourceRequest) -> dict[str, Any]:
    try:
        captured = IPAD_CAPTURE.capture(request.source)
    except IPadCaptureError as exc:
        raise HTTPException(503, str(exc)) from exc
    identifier = uuid.uuid4().hex
    _prepare()
    target = FILES / f"{identifier}.png"
    png = captured.pop("png")
    target.write_bytes(png)
    item = {
        "id": identifier, "name": f"iPad screen {time.strftime('%Y-%m-%d %H-%M-%S')}.png",
        "category": "homework", "extension": ".png", "media_type": "image/png",
        "size": len(png), "created": time.time(), "pages": None,
        "sha256": hashlib.sha256(png).hexdigest(),
        "relative_path": f"files/{identifier}.png", "source": f"ipad_{captured['source']}",
        "capture": captured,
    }
    try:
        stored = _db().add_document(item, "")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    _record("capture", item["name"], True, "document", identifier,
            {"source": captured["source"]})
    return {"ok": True, "item": stored}


@app.get("/api/ipad/frame")
def ipad_live_frame() -> Response:
    """Return a transient current frame without adding it to the library."""
    try:
        captured = IPAD_CAPTURE.capture("airplay")
    except IPadCaptureError as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(
        content=captured["png"], media_type="image/png",
        headers={"Cache-Control": "no-store, max-age=0", "X-Frame-SHA256": captured["sha256"]},
    )


@app.post("/api/tools/{name}")
def run_tool(name: str, request: ToolRequest) -> Any:
    tool = TOOLS.get(name)
    if tool is None:
        raise HTTPException(404, "tool is not exposed in the command center")
    started = time.monotonic()
    try:
        result = tool(**request.arguments)
    except TypeError as exc:
        raise HTTPException(422, str(exc)) from exc
    duration_ms = (time.monotonic() - started) * 1000
    evidence = _db().record_tool_call(name, request.arguments, result, duration_ms, request.attempt_id)
    _record("tool", name, bool(result.get("ok")), "tool_call", evidence["id"],
            {"attempt_id": request.attempt_id, "duration_ms": duration_ms})
    result["evidence_id"] = evidence["id"]
    return result


@app.get("/api/history")
def history() -> dict[str, Any]:
    return {"ok": True, "events": _db().events(100)}


@app.get("/api/visuals")
def visuals(problem_id: str = "", limit: int = 50) -> dict[str, Any]:
    """List locally rendered visuals so the board can show what an agent made."""
    database = _db()
    sweep_if_due(database, SHOWMAN.data_dir / "objects")
    try:
        items = database.list_visuals(problem_id or None, limit)
    except StorageError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "items": items}


@app.get("/api/canvas")
def canvas_cards(problem_id: str = "", limit: int = 50) -> dict[str, Any]:
    """Cards the agent has put on the canvas. The board polls this; it never authors."""
    try:
        items = _db().list_cards(problem_id or None, limit)
    except StorageError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "items": items}


@app.delete("/api/canvas/{card_id}")
def canvas_card_close(card_id: str) -> dict[str, Any]:
    """Closing a card on the board takes it off the canvas for the agent too."""
    database = _db()
    try:
        card = database.get_card(card_id)
        database.delete_card(card_id)
    except StorageError as exc:
        raise HTTPException(404, str(exc)) from exc
    _record("canvas_card_close", f"{card['kind']}: {card['title'][:60]}", True,
            "canvas_card", card_id, {"actor": "browser"})
    return {"ok": True, "removed": card_id}


@app.get("/api/problems")
def problems(topic: str = "", status: str = "") -> dict[str, Any]:
    try: return {"ok": True, "items": _db().list_problems(topic, status)}
    except StorageError as exc: raise HTTPException(400, str(exc)) from exc


@app.post("/api/problems")
def create_problem(request: ProblemRequest) -> dict[str, Any]:
    try: problem = _db().create_problem(**request.model_dump())
    except StorageError as exc: raise HTTPException(400, str(exc)) from exc
    _record("problem_create", problem["title"], True, "problem", problem["id"])
    return {"ok": True, "problem": problem}


@app.get("/api/problems/{identifier}")
def get_problem(identifier: str) -> dict[str, Any]:
    try: return {"ok": True, "problem": _db().get_problem(identifier)}
    except StorageError as exc: raise HTTPException(404, str(exc)) from exc


@app.patch("/api/problems/{identifier}/interpretation")
def update_interpretation(identifier: str, request: InterpretationRequest) -> dict[str, Any]:
    try: problem = _db().update_problem(identifier, request.circuit_interpretation, request.status)
    except StorageError as exc: raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "problem": problem}


@app.post("/api/problems/{identifier}/attempts")
def create_attempt(identifier: str, request: AttemptRequest) -> dict[str, Any]:
    try: attempt = _db().create_attempt(identifier, **request.model_dump())
    except StorageError as exc: raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "attempt": attempt}


@app.get("/api/problems/{identifier}/attempts")
def attempts(identifier: str) -> dict[str, Any]:
    try: return {"ok": True, "items": _db().attempt_history(identifier)}
    except StorageError as exc: raise HTTPException(404, str(exc)) from exc


@app.patch("/api/attempts/{identifier}")
def complete_attempt(identifier: str, request: AttemptCompleteRequest) -> dict[str, Any]:
    try: attempt = _db().complete_attempt(identifier, **request.model_dump())
    except StorageError as exc: raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "attempt": attempt}


@app.post("/api/transcriptions/{identifier}/confirm")
def confirm_transcription(identifier: str, request: ConfirmationRequest) -> dict[str, Any]:
    try: transcription = _db().confirm_transcription(identifier, request.corrected_content)
    except StorageError as exc: raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "transcription": transcription}


@app.get("/api/progress")
def progress() -> dict[str, Any]: return _db().course_progress()


@app.get("/api/context")
def context(q: str) -> dict[str, Any]: return _db().study_context(q)


@app.get("/api/database/integrity")
def database_integrity() -> dict[str, Any]:
    database, files = _db().integrity(), _db().audit_files(FILES)
    return {"ok": database["ok"] and files["ok"], "database": database, "files": files}


@app.post("/api/database/backup")
def backup_database() -> dict[str, Any]:
    destination = DATA / "backups" / time.strftime("circuit-mcp-%Y%m%d-%H%M%S.sqlite3")
    result = _db().backup(destination)
    _record("backup", Path(result["path"]).name, True, "database", None,
            {"sha256": result["sha256"], "size_bytes": result["size_bytes"]})
    return result
