"""Initial validation boundary for declarative animation scenes."""
from __future__ import annotations

import math
from typing import Any

SCENE_SCHEMA_VERSION = 1
MAX_ELEMENTS = 128
MAX_STEPS = 128
PRIMITIVE_TYPES = frozenset({
    "text", "equation", "line", "arrow", "highlight", "resistor",
    "capacitor", "inductor", "voltage_source", "current_source", "switch",
    "diode", "opamp", "bjt", "ground", "node", "probe", "flow",
    "plot", "waveform", "phasor", "pole_zero", "block", "meter",
})
ELEMENT_FIELDS = frozenset({"id", "type", "x", "y", "w", "h", "r", "text",
                            "label", "size", "color", "opacity", "width", "angle",
                            "path", "points", "tracks"})
STEP_FIELDS = frozenset({"at_ms", "caption", "changes"})
CHANGE_FIELDS = frozenset({"id", "opacity", "color", "x", "y", "rotation", "scale"})
COLORS = {"ink", "muted", "blue", "red", "amber", "green"}
TRACK_PROPERTIES = {"opacity", "x", "y", "rotation", "scale", "progress", "dash_offset"}
EASINGS = {"linear", "easeInOutSine", "easeOutCubic", "easeOutExpo", "easeOutBack", "easeOutSpring"}


class SceneValidationError(ValueError):
    """Scene data is unsafe, unbounded, or structurally invalid."""


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SceneValidationError(f"{name} must be a finite number")
    return float(value)


def validate_scene(scene: dict[str, Any]) -> dict[str, Any]:
    """Validate the stable outer contract; primitive validation follows next."""
    if not isinstance(scene, dict):
        raise SceneValidationError("scene must be an object")
    allowed = {"schema_version", "title", "width", "height", "seed",
               "duration_ms", "loop", "problem_id", "elements", "steps", "camera"}
    unknown = set(scene) - allowed
    if unknown:
        raise SceneValidationError(f"unknown scene fields: {sorted(unknown)}")
    title = scene.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 160:
        raise SceneValidationError("title must contain 1 to 160 characters")
    width, height = _finite(scene.get("width", 960), "width"), _finite(scene.get("height", 600), "height")
    duration = _finite(scene.get("duration_ms", 8000), "duration_ms")
    if not 320 <= width <= 1920 or not 240 <= height <= 1080:
        raise SceneValidationError("canvas dimensions are outside the supported range")
    if not 250 <= duration <= 600_000:
        raise SceneValidationError("duration_ms is outside the supported range")
    elements, steps = scene.get("elements", []), scene.get("steps", [])
    if not isinstance(elements, list) or len(elements) > MAX_ELEMENTS:
        raise SceneValidationError("elements must be a bounded list")
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise SceneValidationError("steps must be a bounded list")
    ids: set[str] = set()
    for element in elements:
        if not isinstance(element, dict) or element.get("type") not in PRIMITIVE_TYPES:
            raise SceneValidationError("element has an unsupported primitive type")
        if set(element) - ELEMENT_FIELDS:
            raise SceneValidationError("element has unknown fields")
        identifier = element.get("id")
        if not isinstance(identifier, str) or not identifier.isidentifier() or len(identifier) > 64 or identifier in ids:
            raise SceneValidationError("element IDs must be unique identifiers")
        ids.add(identifier)
        for key in ("x", "y", "w", "h", "r", "size", "opacity", "width", "angle"):
            if key in element: _finite(element[key], f"element {identifier}.{key}")
        for key in ("text", "label", "path"):
            if key in element and (not isinstance(element[key], str) or len(element[key]) > 2000):
                raise SceneValidationError(f"element {identifier}.{key} must be bounded text")
        if "color" in element and element["color"] not in COLORS:
            raise SceneValidationError("element color must use the visual theme")
        if "points" in element:
            points = element["points"]
            if not isinstance(points, list) or len(points) > 2000:
                raise SceneValidationError("plot points must be a bounded list")
            for point in points:
                if not isinstance(point, list) or len(point) != 2: raise SceneValidationError("plot points must be [x,y]")
                _finite(point[0], "point x"); _finite(point[1], "point y")
        _validate_tracks(element.get("tracks", []), duration, f"element {identifier}")
    camera = scene.get("camera")
    if camera is not None:
        if not isinstance(camera, dict) or set(camera) - {"x", "y", "zoom", "tracks"}:
            raise SceneValidationError("camera must contain only x, y, zoom, and tracks")
        for key in ("x", "y", "zoom"):
            if key in camera: _finite(camera[key], f"camera.{key}")
        if "zoom" in camera and camera["zoom"] <= 0:
            raise SceneValidationError("camera.zoom must be positive")
        _validate_tracks(camera.get("tracks", []), duration, "camera", {"x", "y", "zoom"})
    changes_count = 0
    last_at = -1.0
    for step in steps:
        if not isinstance(step, dict) or set(step) - STEP_FIELDS:
            raise SceneValidationError("step has unknown fields")
        at = _finite(step.get("at_ms", 0), "step.at_ms")
        if at < last_at or at > duration: raise SceneValidationError("steps must be ordered within the duration")
        last_at = at
        caption = step.get("caption", "")
        if not isinstance(caption, str) or len(caption) > 1000: raise SceneValidationError("caption is too large")
        changes = step.get("changes", [])
        if not isinstance(changes, list): raise SceneValidationError("changes must be a list")
        changes_count += len(changes)
        for change in changes:
            if not isinstance(change, dict) or set(change) - CHANGE_FIELDS or change.get("id") not in ids:
                raise SceneValidationError("change must target a known element")
            if "color" in change and change["color"] not in COLORS: raise SceneValidationError("change color must use the theme")
            for key in ("opacity", "x", "y", "rotation", "scale"):
                if key in change: _finite(change[key], f"change.{key}")
    if changes_count > 512: raise SceneValidationError("scene has too many property changes")
    normalized = dict(scene)
    normalized.update(schema_version=SCENE_SCHEMA_VERSION, title=title.strip(),
                      width=int(width), height=int(height), duration_ms=int(duration),
                      seed=int(scene.get("seed", 407)), loop=bool(scene.get("loop", False)),
                      elements=elements, steps=steps)
    return normalized


def _validate_tracks(tracks: Any, duration: float, owner: str, properties: set[str] | None = None) -> None:
    """Validate deterministic numeric animation tracks without accepting executable values."""
    if not isinstance(tracks, list) or len(tracks) > 24:
        raise SceneValidationError(f"{owner}.tracks must be a bounded list")
    allowed_properties = properties or TRACK_PROPERTIES
    seen: set[str] = set()
    for track in tracks:
        if not isinstance(track, dict) or set(track) - {"property", "keyframes"}:
            raise SceneValidationError(f"{owner} track has unknown fields")
        prop = track.get("property")
        if prop not in allowed_properties or prop in seen:
            raise SceneValidationError(f"{owner} track property is unsupported or duplicated")
        seen.add(prop)
        frames = track.get("keyframes")
        if not isinstance(frames, list) or not 2 <= len(frames) <= 64:
            raise SceneValidationError(f"{owner} track needs 2 to 64 keyframes")
        last_time = -1.0
        for frame in frames:
            if not isinstance(frame, dict) or set(frame) - {"t_ms", "value", "easing"}:
                raise SceneValidationError(f"{owner} keyframe has unknown fields")
            at = _finite(frame.get("t_ms"), f"{owner} keyframe.t_ms")
            _finite(frame.get("value"), f"{owner} keyframe.value")
            if at < last_time or at > duration:
                raise SceneValidationError(f"{owner} keyframes must be ordered within the duration")
            if frame.get("easing", "linear") not in EASINGS:
                raise SceneValidationError(f"{owner} keyframe easing is unsupported")
            last_time = at
