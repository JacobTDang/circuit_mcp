"""Persistent UniMERNet inference worker.

This file intentionally imports only the standard library and ``page_segment``
(numpy) at module level; PIL, PyTorch and UniMERNet are imported where they are
used. It is executed by the isolated OCR virtual environment, which does not
contain the circuit server's dependencies.
Requests and responses are framed pickles on stdin/stdout; both pipe ends are
private children of the local MCP process.
"""
from __future__ import annotations

import contextlib
import io
import os
import pickle
import resource
import struct
import sys
import time
import traceback
import uuid
from pathlib import Path

try:
    from . import page_segment   # imported as circuit_mcp.ocr_worker, by the tests and tooling
except ImportError:              # run as a script by OCRWorker: this file's directory is sys.path[0]
    import page_segment

MAX_PAGE_EXPRESSIONS = 60
# A small PNG can decode to a huge image, which the persistent worker would hold
# several times over (RGB, grayscale, ink mask), so a page's size is checked from its
# header before any pixel is decoded. A 300 dpi letter page is ~8.4 M pixels.
MAX_PAGE_PIXELS = 40_000_000

HEADER = struct.Struct("!Q")


def _read_exactly(stream, count: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _write_frame(stream, value) -> None:
    payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    stream.write(HEADER.pack(len(payload)))
    stream.write(payload)
    stream.flush()


class _Engine:
    def __init__(self, model_dir: str, requested_device: str) -> None:
        self.model_dir = Path(model_dir).resolve()
        self.requested_device = requested_device
        self.model = None
        self.processor = None
        self.device = None
        self.loaded_at = None
        self.load_seconds = None
        # At most one page is held open at a time: a page's pixels are tens of
        # megabytes, and the client only ever reads one page at a time.
        self._page: dict | None = None

    def _select_device(self, torch) -> str:
        requested = self.requested_device
        if requested != "auto":
            if requested == "mps" and not torch.backends.mps.is_available():
                raise RuntimeError("MPS was requested but PyTorch reports it unavailable.")
            if requested == "cuda" and not torch.cuda.is_available():
                raise RuntimeError("CUDA was requested but PyTorch reports it unavailable.")
            return requested
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def load(self) -> None:
        if self.model is not None:
            return
        checkpoint = self.model_dir / "unimernet_small.pth"
        if not checkpoint.exists():
            matches = sorted(self.model_dir.glob("unimernet_*.pth"))
            if len(matches) != 1:
                raise RuntimeError(
                    f"Expected one unimernet_*.pth checkpoint in {self.model_dir}; "
                    f"found {[path.name for path in matches]}."
                )
            checkpoint = matches[0]
        for required in ("config.json", "tokenizer.json"):
            if not (self.model_dir / required).exists():
                raise RuntimeError(f"Model directory is missing {required}.")

        # Import only in the dedicated worker. Importing torch into the MCP
        # process and then forking the symbolic worker is unsafe on macOS.
        os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        import torch
        from omegaconf import OmegaConf
        from unimernet.models.unimernet.unimernet import UniMERModel
        from unimernet.processors.formula_processor import FormulaImageEvalProcessor

        device = self._select_device(torch)
        config = OmegaConf.create(
            {
                "model_name": str(self.model_dir),
                "model_config": {
                    "model_name": str(self.model_dir),
                    "max_seq_len": 1536,
                },
                "tokenizer_name": "nougat",
                "tokenizer_config": {"path": str(self.model_dir)},
                "load_pretrained": True,
                "pretrained": str(checkpoint),
                "load_finetuned": False,
            }
        )
        started = time.monotonic()
        model = UniMERModel.from_config(config)
        if device == "mps":
            # UniMERNet 0.2.3 treats every non-CPU device as CUDA and enters
            # torch.cuda.amp.autocast. Metal needs no CUDA context at all.
            model.maybe_autocast = lambda dtype=torch.float16: contextlib.nullcontext()
        model = model.to(device).eval()

        self.model = model
        self.processor = FormulaImageEvalProcessor([192, 672])
        self.device = device
        self.loaded_at = time.time()
        self.load_seconds = time.monotonic() - started

    def status(self) -> dict:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return {
            "ok": True,
            "backend": "unimernet",
            "model_dir": str(self.model_dir),
            "requested_device": self.requested_device,
            "device": self.device,
            "loaded": self.model is not None,
            "loaded_at": self.loaded_at,
            "load_seconds": self.load_seconds,
            # ru_maxrss is bytes on macOS and KiB on Linux.
            "max_rss": usage.ru_maxrss,
            "pid": os.getpid(),
        }

    def _decode(self, png: bytes, max_pixels: int | None = None):
        from PIL import Image

        try:
            image = Image.open(io.BytesIO(png))
        except Exception as exc:
            raise ValueError(f"Could not decode input image: {exc}") from exc
        width, height = image.size   # read from the header: no pixel is decoded yet
        if max_pixels is not None and width * height > max_pixels:
            raise ValueError(
                f"Image is {width}x{height} ({width * height} pixels); "
                f"the limit is {max_pixels} pixels."
            )
        try:
            image = image.convert("RGB")
            image.load()
        except Exception as exc:
            raise ValueError(f"Could not decode input image: {exc}") from exc
        return image

    def _latex(self, image) -> tuple[str, float]:
        """One cropped expression -> (LaTeX, seconds spent generating)."""
        import torch

        tensor = self.processor(image).unsqueeze(0).to(self.device)
        started = time.monotonic()
        with torch.inference_mode():
            output = self.model.generate(
                {"image": tensor}, temperature=0.0, do_sample=False
            )
        return output["pred_str"][0].strip(), time.monotonic() - started

    def transcribe(self, png: bytes) -> dict:
        self.load()
        image = self._decode(png)
        width, height = image.size
        latex, seconds = self._latex(image)
        return {
            "ok": True,
            "latex": latex,
            "device": self.device,
            "model": self.model_dir.name,
            "image_width": width,
            "image_height": height,
            "inference_seconds": seconds,
        }

    def page_open(self, png: bytes) -> dict:
        """Prepare a page and hold it, transcribing nothing yet.

        Splitting the page open from reading its boxes is what lets the client
        release its lock between boxes: a page can take ten minutes, and a
        single ``transcribe_image`` call should not wait behind all of it.

        Only one page is held at a time. A second open replaces the first, and
        the abandoned page's id is then refused rather than silently reused.
        """
        import numpy as np

        from PIL import Image

        image, boxes, width, height = self._segment(png)
        self._page = {
            "id": uuid.uuid4().hex,
            "image": image,
            "boxes": boxes[:MAX_PAGE_EXPRESSIONS],
        }
        return {
            "ok": True,
            "page_id": self._page["id"],
            "expression_count": len(boxes),
            "truncated": len(boxes) > MAX_PAGE_EXPRESSIONS,
            "device": self.device,
            "model": self.model_dir.name,
            "image_width": width,
            "image_height": height,
        }

    def page_expression(self, page_id: str, index: int) -> dict:
        """One box of the page held open, transcribed now."""
        page = self._page
        if page is None or page["id"] != page_id:
            return {
                "ok": False,
                "error": "page_expired",
                "message": "The worker no longer holds that page. Send the page again.",
            }
        if not 0 <= index < len(page["boxes"]):
            return {
                "ok": False,
                "error": "no_such_expression",
                "message": f"Page has {len(page['boxes'])} expressions; asked for index {index}.",
            }
        box = page["boxes"][index]
        latex, spent = self._latex(page["image"].crop(box))
        return {
            "ok": True,
            "index": index,
            "bbox": list(box),
            "latex": latex,
            "inference_seconds": spent,
        }

    def page_close(self, page_id: str) -> dict:
        """Drop a page the client is done with, so its pixels are not held."""
        if self._page is not None and self._page["id"] == page_id:
            self._page = None
        return {"ok": True}

    def _segment(self, png: bytes):
        """Decode, size-check, segment and de-frame a page. No model work."""
        import numpy as np

        from PIL import Image

        image = self._decode(png, max_pixels=MAX_PAGE_PIXELS)
        self.load()
        width, height = image.size
        gray = np.asarray(image.convert("L"))
        boxes = page_segment.expression_boxes(gray)
        # Segmentation already ignores frames, but a box's padding can still
        # reach one, and a frame edge inside a crop is ink the recognizer tries
        # to read. Paint it out at the page's own background level first.
        frames = page_segment.frame_mask(page_segment.ink_mask(gray))
        if frames.any():
            painted = np.array(image)
            if painted.ndim == 3:
                painted[frames] = np.median(painted.reshape(-1, painted.shape[-1]), axis=0)
            else:
                painted[frames] = int(np.median(painted))
            image = Image.fromarray(painted)
        return image, boxes, width, height

    def transcribe_page(self, png: bytes) -> dict:
        """Every line of working on a page as its own box, in reading order.

        Bands top to bottom; within a band, columns left to right; within a
        column, lines top to bottom, so a side calculation stays together. The
        page is decoded and size-checked before the model loads, so a refused
        page never costs a model load.

        The client drives ``page_open``/``page_expression`` instead, so that it
        can release its lock between boxes; this stays for a caller that wants
        the whole page in one request.
        """
        opened = self.page_open(png)
        expressions, seconds = [], 0.0
        for index in range(len(self._page["boxes"])):
            result = self.page_expression(opened["page_id"], index)
            seconds += result["inference_seconds"]
            expressions.append({"index": index, "bbox": result["bbox"], "latex": result["latex"]})
        self.page_close(opened["page_id"])
        return {
            "ok": True,
            "expressions": expressions,
            "expression_count": opened["expression_count"],
            "truncated": opened["truncated"],
            "device": self.device,
            "model": self.model_dir.name,
            "image_width": opened["image_width"],
            "image_height": opened["image_height"],
            "inference_seconds": seconds,
        }


def serve(model_dir: str, device: str) -> None:
    engine = _Engine(model_dir, device)
    source = sys.stdin.buffer
    sink = sys.stdout.buffer
    while True:
        header = _read_exactly(source, HEADER.size)
        if header is None:
            return
        (size,) = HEADER.unpack(header)
        payload = _read_exactly(source, size)
        if payload is None:
            return
        try:
            request = pickle.loads(payload)
            action = request.get("action")
            if action == "status":
                if request.get("load_model"):
                    engine.load()
                response = engine.status()
            elif action == "transcribe":
                response = engine.transcribe(request["png"])
            elif action == "transcribe_page":
                response = engine.transcribe_page(request["png"])
            elif action == "page_open":
                response = engine.page_open(request["png"])
            elif action == "page_expression":
                response = engine.page_expression(request["page_id"], request["index"])
            elif action == "page_close":
                response = engine.page_close(request["page_id"])
            else:
                response = {
                    "ok": False,
                    "error": "unknown_action",
                    "message": f"Unknown OCR worker action {action!r}.",
                }
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            response = {
                "ok": False,
                "error": "ocr_error",
                "message": f"{type(exc).__name__}: {exc}",
            }
        _write_frame(sink, response)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: ocr_worker.py MODEL_DIR DEVICE")
    serve(sys.argv[1], sys.argv[2])
