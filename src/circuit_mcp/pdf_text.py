"""Read an uploaded PDF's text in an interpreter of its own.

pypdf is pure Python. On one of the desk's own threads a long PDF holds the GIL,
and every other request waits its turn for it: beside a 10,000-page upload the
board's poll went from 2 ms to 600 ms. A child process shares nothing with the
desk. This file imports only pypdf and the standard library, so the child starts
in a few hundredths of a second, and it runs as a script, so the child does not
depend on how this package was installed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pypdf import PdfReader
from pypdf import errors as pdf_errors


def read(path: Path, limit: int) -> tuple[str, int]:
    """The text a search keeps, and the page count."""
    reader = PdfReader(path)
    texts: list[str] = []
    joined = 0
    for page in reader.pages:
        # Text past the limit is cut off below, so a textbook stops being read once it is reached.
        if joined >= limit:
            break
        text = page.extract_text() or ""
        joined += len(text) + (1 if texts else 0)
        texts.append(text)
    return "\n".join(texts)[:limit], len(reader.pages)


def extract(path: Path, limit: int) -> tuple[str, int]:
    """``read`` in a child interpreter. A PDF it cannot read raises the same pypdf error here."""
    completed = subprocess.run([sys.executable, __file__, str(path), str(limit)],
                               capture_output=True, check=False)
    if completed.returncode != 0:
        stderr = completed.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"reading the PDF's text failed: {stderr[-2000:]}")
    answer = json.loads(completed.stdout)
    if "error" in answer:
        raise getattr(pdf_errors, answer["error"])(answer["message"])
    return answer["text"], answer["pages"]


if __name__ == "__main__":
    source, limit = Path(sys.argv[1]), int(sys.argv[2])
    try:
        text, pages = read(source, limit)
    except pdf_errors.PdfReadError as unreadable:
        answer = {"error": type(unreadable).__name__, "message": str(unreadable)}
    else:
        answer = {"text": text, "pages": pages}
    sys.stdout.write(json.dumps(answer))
