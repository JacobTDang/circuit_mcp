"""Keep every test off the developer's machine.

Four tests once passed here and failed the first time CI ran them, each for a
reason that had nothing to do with what it was testing: one stubbed a method
the route had stopped calling, so the *real* Showman answered and the test only
passed on a machine where Showman starts. That is the shape of the problem --
a test that can reach the developer's own store, renderer or model is a test
that can pass for the wrong reason and fail on a clean machine.

So the store is redirected for every test, and reaching a real worker is an
error rather than a slow success. A test that genuinely needs to spawn one says
so with ``@pytest.mark.spawns_workers``, which makes the dependency visible
instead of ambient.
"""
from __future__ import annotations

import pytest

from circuit_mcp import ocr_client, showman, web

PROJECT_STORE = web.DATA


@pytest.fixture(autouse=True)
def isolated_command_center(tmp_path_factory, monkeypatch):
    """Point every location the command center reads at a throwaway store."""
    data = tmp_path_factory.mktemp("command_center")
    # paths.py reads these when called, which covers the modules that ask for a
    # location at call time; the constants below are the ones taken at import.
    for variable, location in (
        ("CIRCUIT_MCP_DATA_DIR", data),
        ("CIRCUIT_MCP_SHOWMAN_DATA_DIR", data / "showman"),
        ("CIRCUIT_MCP_RUNTIME_DIR", data / "runtime"),
        ("CIRCUIT_MCP_WORKSPACE_CONFIG", data / "workspace.json"),
    ):
        monkeypatch.setenv(variable, str(location))
    monkeypatch.setattr(web, "DATA", data)
    monkeypatch.setattr(web, "FILES", data / "files")
    monkeypatch.setattr(web, "INDEX", data / "library.json")
    monkeypatch.setattr(web, "HISTORY", data / "history.jsonl")
    return data


@pytest.fixture(autouse=True)
def no_ambient_workers(request, monkeypatch):
    """Starting a real renderer or recogniser is an error unless the test says so.

    Both exist on a development machine and on neither CI nor a fresh checkout,
    so a test that quietly uses one reports a pass here and a failure there --
    or worse, passes in both places while testing the wrong thing.
    """
    if "spawns_workers" in request.keywords:
        return

    def no_showman(*_: object, **__: object) -> None:
        raise AssertionError(
            "this test started the real Showman worker. Stub the method the route "
            "calls, or mark the test spawns_workers if it really needs one."
        )

    def no_recogniser(*_: object, **__: object) -> None:
        raise AssertionError(
            "this test started the real OCR worker, which needs an 810 MB checkpoint "
            "that CI does not have. Stub it, or mark the test spawns_workers."
        )

    monkeypatch.setattr(showman.SHOWMAN, "start", no_showman)
    monkeypatch.setattr(ocr_client.OCRWorker, "_start", no_recogniser)
