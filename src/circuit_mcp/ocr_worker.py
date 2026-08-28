"""Persistent UniMERNet inference worker.

This file intentionally uses only the standard library until ``_Engine.load``.
It is executed by the isolated OCR virtual environment, which does not contain
the circuit server's dependencies. Requests and responses are framed pickles on
stdin/stdout; both pipe ends are private children of the local MCP process.
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
from pathlib import Path

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

    def transcribe(self, png: bytes) -> dict:
        self.load()
        from PIL import Image
        import torch

        try:
            image = Image.open(io.BytesIO(png)).convert("RGB")
            image.load()
        except Exception as exc:
            raise ValueError(f"Could not decode input image: {exc}") from exc
        width, height = image.size
        tensor = self.processor(image).unsqueeze(0).to(self.device)
        started = time.monotonic()
        with torch.inference_mode():
            output = self.model.generate(
                {"image": tensor}, temperature=0.0, do_sample=False
            )
        latex = output["pred_str"][0].strip()
        return {
            "ok": True,
            "latex": latex,
            "device": self.device,
            "model": self.model_dir.name,
            "image_width": width,
            "image_height": height,
            "inference_seconds": time.monotonic() - started,
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
