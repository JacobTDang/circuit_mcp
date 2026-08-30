"""Keep every test off the developer's real command-center store.

Only a few tests redirected ``web.DATA``, which was harmless while the Showman
routes were read-only. Once ``/api/showman/generate`` began recording a visual,
any test exercising it wrote rows into the real database -- rows pointing at a
fixture key whose bytes never existed, so ``visual_list`` then advertised dead
URLs. Redirecting for the whole session makes that impossible rather than merely
unlikely.
"""
from __future__ import annotations

import pytest

from circuit_mcp import web

PROJECT_STORE = web.DATA


@pytest.fixture(autouse=True)
def isolated_command_center(tmp_path_factory, monkeypatch):
    """Point the web layer at a throwaway store for the duration of one test."""
    data = tmp_path_factory.mktemp("command_center")
    monkeypatch.setattr(web, "DATA", data)
    monkeypatch.setattr(web, "FILES", data / "files")
    monkeypatch.setattr(web, "INDEX", data / "library.json")
    monkeypatch.setattr(web, "HISTORY", data / "history.jsonl")
    return data
