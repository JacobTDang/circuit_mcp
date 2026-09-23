"""A worker that speaks the framed-pickle protocol without loading a model.

The real worker needs an 810 MB checkpoint, which no test can load. This stands
in for it so the client's page loop, its lock fairness and its failure paths can
be tested against a real subprocess and real pipes.

Timings come from the environment so a test can make one box slow without
making every call slow:

- ``STUB_BOXES``: how many expressions a page has.
- ``STUB_BOX_SECONDS``: how long one box takes.
- ``STUB_IMAGE_SECONDS``: how long a single-image transcription takes.
- ``STUB_FORGET_AFTER``: forget the page after this many boxes, which is what a
  restarted worker looks like from the client's side.
"""
from __future__ import annotations

import os
import pickle
import struct
import sys
import time
import uuid

HEADER = struct.Struct("!Q")


def _read_exactly(stream, count):
    chunks = []
    while count:
        chunk = stream.read(count)
        if not chunk:
            return None
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


def main() -> None:
    boxes = int(os.environ.get("STUB_BOXES", "3"))
    box_seconds = float(os.environ.get("STUB_BOX_SECONDS", "0.2"))
    image_seconds = float(os.environ.get("STUB_IMAGE_SECONDS", "0.02"))
    forget_after = int(os.environ.get("STUB_FORGET_AFTER", "0"))
    source, sink = sys.stdin.buffer, sys.stdout.buffer
    page_id = None
    served = 0

    while True:
        header = _read_exactly(source, HEADER.size)
        if header is None:
            return
        (size,) = HEADER.unpack(header)
        payload = _read_exactly(source, size)
        if payload is None:
            return
        request = pickle.loads(payload)
        action = request.get("action")

        if action == "status":
            response = {"ok": True, "loaded": False, "device": "cpu"}
        elif action == "transcribe":
            time.sleep(image_seconds)
            response = {"ok": True, "latex": "one image", "inference_seconds": image_seconds}
        elif action == "page_open":
            page_id = uuid.uuid4().hex
            served = 0
            response = {
                "ok": True, "page_id": page_id, "expression_count": boxes,
                "truncated": False, "device": "cpu", "model": "stub",
                "image_width": 400, "image_height": 300,
            }
        elif action == "page_expression":
            if page_id is None or request["page_id"] != page_id:
                response = {"ok": False, "error": "page_expired",
                            "message": "The worker no longer holds that page."}
            else:
                time.sleep(box_seconds)
                served += 1
                if forget_after and served >= forget_after:
                    page_id = None
                index = request["index"]
                response = {"ok": True, "index": index, "bbox": [0, index * 10, 40, index * 10 + 8],
                            "latex": f"expr{index + 1}", "inference_seconds": box_seconds}
        elif action == "page_close":
            page_id = None
            response = {"ok": True}
        else:
            response = {"ok": False, "error": "unknown_action", "message": str(action)}

        body = pickle.dumps(response, protocol=pickle.HIGHEST_PROTOCOL)
        sink.write(HEADER.pack(len(body)) + body)
        sink.flush()


if __name__ == "__main__":
    main()
