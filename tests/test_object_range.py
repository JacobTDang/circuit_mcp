"""Byte-range support for the artifact proxy (issue #11).

A video element seeks by asking for byte ranges. Answering 200 with the whole
file makes seeking fall back to a full re-download, and Safari will not play at
all. The proxy must also stop reading whole videos into memory.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from circuit_mcp import web
from circuit_mcp.showman import ShowmanMissingObject

BODY = b"".join(bytes([i % 256]) for i in range(4096))
KEY = "videos/abc.mp4"


class _Upstream:
    """Stands in for the worker: honours Range the way Showman now does."""

    def __init__(self, body: bytes = BODY):
        self.body = body
        self.calls: list[str | None] = []

    def __call__(self, key, byte_range=None, timeout=30):
        self.calls.append(byte_range)
        if key != KEY:
            raise ShowmanMissingObject(f"no artifact for {key}")
        total = len(self.body)
        if byte_range is None:
            return web.ShowmanArtifact(200, "video/mp4", iter([self.body]), total, None, total)
        start, end = byte_range
        if start >= total:
            return web.ShowmanArtifact(416, "video/mp4", iter([b""]), 0, f"bytes */{total}", total)
        end = min(end if end is not None else total - 1, total - 1)
        chunk = self.body[start:end + 1]
        return web.ShowmanArtifact(206, "video/mp4", iter([chunk]), len(chunk),
                                   f"bytes {start}-{end}/{total}", total)


@pytest.fixture
def client(monkeypatch):
    upstream = _Upstream()
    monkeypatch.setattr(web.SHOWMAN, "object_response", upstream)
    browser = TestClient(web.app, headers={"host": "localhost:2300"})
    browser.upstream = upstream
    return browser


def test_a_plain_get_advertises_range_support(client):
    response = client.get(f"/api/showman/objects/{KEY}")
    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"
    assert response.content == BODY


def test_a_byte_range_returns_partial_content(client):
    response = client.get(f"/api/showman/objects/{KEY}", headers={"Range": "bytes=0-99"})
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-99/{len(BODY)}"
    assert response.headers["content-length"] == "100"
    assert response.content == BODY[:100]


def test_an_open_ended_range_runs_to_the_end(client):
    response = client.get(f"/api/showman/objects/{KEY}", headers={"Range": "bytes=4000-"})
    assert response.status_code == 206
    assert response.content == BODY[4000:]
    assert response.headers["content-range"] == f"bytes 4000-4095/{len(BODY)}"


def test_a_range_past_the_end_is_unsatisfiable(client):
    response = client.get(f"/api/showman/objects/{KEY}", headers={"Range": "bytes=99999-"})
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(BODY)}"


def test_an_unparseable_range_is_served_whole_rather_than_failing(client):
    """A malformed header is the client's problem; the artifact is still valid."""
    response = client.get(f"/api/showman/objects/{KEY}", headers={"Range": "furlongs=1-2"})
    assert response.status_code == 200
    assert client.upstream.calls == [None]


def test_a_missing_artifact_is_still_reported_as_gone(client):
    assert client.get("/api/showman/objects/videos/absent.mp4").status_code == 404


def test_an_invalid_key_is_still_refused(client):
    assert client.get("/api/showman/objects/videos/../../secret").status_code in (400, 404)


def test_the_body_is_streamed_rather_than_buffered(client):
    """A 200 MB render must not be read into memory to be served."""
    import inspect

    source = inspect.getsource(web.showman_object)
    assert "StreamingResponse" in source
    assert "object_bytes" not in source, "the whole-file read must be gone from the proxy"
