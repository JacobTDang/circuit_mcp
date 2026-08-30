"""Retention for locally rendered artifacts.

A render costs an authoring call and a quarter-minute of wall clock; storing it
costs about 145 KB. So the policy keeps anything that cost something and deletes
only what has no claim on disk: artifacts nothing references, and ad-hoc renders
nobody attached to a problem.

Bytes and references expire in the same pass. Releasing bytes while a row still
advertises the URL would leave the board pointing at a 404.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .storage import CommandCenterDB

RETENTION_SECONDS = 3600.0
_SWEEP_INTERVAL = 300.0
_last_sweep = 0.0


def _keep_stems(database: CommandCenterDB) -> set[str]:
    """Every artifact a live row still points at, keyed by path without extension.

    Captions and subtitles sit beside their video under the same content hash, so
    matching on the stem keeps a kept video's siblings without having to know
    every extension Showman might add later.
    """
    stems: set[str] = set()
    for visual in database.list_visuals(limit=200):
        stems.add(str(Path(visual["object_key"]).with_suffix("")))
        spec_key = (visual.get("provenance") or {}).get("specKey")
        if isinstance(spec_key, str) and spec_key:
            stems.add(str(Path(spec_key).with_suffix("")))
    return stems


def sweep(database: CommandCenterDB, objects_dir: Path,
          retention_seconds: float = RETENTION_SECONDS) -> dict[str, Any]:
    """Expire unlinked visuals, then delete artifacts nothing claims."""
    cutoff = time.time() - retention_seconds
    expired = database.expire_unlinked_visuals(cutoff)

    objects_dir = Path(objects_dir)
    if not objects_dir.is_dir():
        return {"ok": True, "expired_visuals": len(expired), "deleted_objects": 0}

    root = objects_dir.resolve()
    keep = _keep_stems(database)
    deleted = 0
    for path in objects_dir.rglob("*"):
        # A symlink is not ours to follow: resolve first, and refuse anything
        # that leaves the store rather than deleting a target elsewhere on disk.
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            continue
        if not resolved.is_file():
            continue
        if str(path.relative_to(objects_dir).with_suffix("")) in keep:
            continue
        # A render in flight has no row yet; the grace window keeps live work.
        if path.stat().st_mtime > cutoff:
            continue
        path.unlink(missing_ok=True)
        deleted += 1
    return {"ok": True, "expired_visuals": len(expired), "deleted_objects": deleted}


def sweep_if_due(database: CommandCenterDB, objects_dir: Path) -> dict[str, Any] | None:
    """Sweep at most once per interval, so a busy request path stays cheap."""
    global _last_sweep
    now = time.monotonic()
    if now - _last_sweep < _SWEEP_INTERVAL:
        return None
    _last_sweep = now
    return sweep(database, objects_dir)
