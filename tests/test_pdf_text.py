"""Reading an uploaded PDF's text in an interpreter of its own (issue #121)."""
from __future__ import annotations

import subprocess
import sys

import pytest
from pypdf import PageObject, PdfReader, errors

from circuit_mcp import pdf_text
from tests.fixtures.pdfs import encrypted_pdf, text_pdf

CHAPTER = [f"page {number} of the capacitor chapter" for number in range(1, 41)]


def _pdf(tmp_path, content: bytes):
    path = tmp_path / "upload.pdf"
    path.write_bytes(content)
    return path


def test_reading_stops_once_the_kept_text_is_full(tmp_path, monkeypatch):
    """Every page used to be read, and everything past the limit then thrown away."""
    path = _pdf(tmp_path, text_pdf(CHAPTER))
    whole = "\n".join(page.extract_text() for page in PdfReader(path).pages)
    read, extract_text = [], PageObject.extract_text

    def counted(page, *args, **kwargs):
        read.append(page)
        return extract_text(page, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", counted)
    assert pdf_text.read(path, 100) == (whole[:100], 40)
    assert len(read) < 10


def test_the_text_is_read_by_a_child_interpreter(tmp_path, monkeypatch):
    """pypdf is pure Python: read on a desk thread it holds the GIL, and the
    board's poll went from 2 ms to 600 ms beside a 10,000-page upload."""
    path = _pdf(tmp_path, text_pdf(CHAPTER))
    launched, run = [], subprocess.run

    def recorded(command, **kwargs):
        launched.append(command)
        return run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", recorded)
    assert pdf_text.extract(path, 2_000_000) == pdf_text.read(path, 2_000_000)
    assert [command[0] for command in launched] == [sys.executable]


@pytest.mark.parametrize(("content", "failure"), [
    (encrypted_pdf("secret"), errors.FileNotDecryptedError),
    (b"this is not a pdf", errors.PdfStreamError),
    (b"", errors.EmptyFileError),
], ids=["open-password", "damaged", "empty"])
def test_a_pdf_the_child_cannot_read_raises_its_own_pypdf_error_here(tmp_path, content, failure):
    with pytest.raises(failure) as raised:
        pdf_text.extract(_pdf(tmp_path, content), 2_000_000)
    assert type(raised.value) is failure


def test_a_child_that_fails_for_another_reason_says_why(tmp_path):
    with pytest.raises(RuntimeError, match="FileNotFoundError"):
        pdf_text.extract(tmp_path / "missing.pdf", 2_000_000)
