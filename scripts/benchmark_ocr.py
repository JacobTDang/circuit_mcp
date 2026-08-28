"""Run a labelled handwriting manifest through the persistent OCR worker."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from circuit_mcp.ocr_client import OCR_WORKER  # noqa: E402


def normalized(text: str) -> str:
    return "".join(text.split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest", nargs="?", default=ROOT / "benchmarks/handwriting/manifest.json"
    )
    args = parser.parse_args()
    manifest = Path(args.manifest).resolve()
    document = json.loads(manifest.read_text())
    samples = document.get("samples", [])
    if not samples:
        print(f"No samples in {manifest}. Add PNG/expected_latex entries first.")
        return 2

    correct = 0
    results = []
    for sample in samples:
        image = (manifest.parent / sample["png"]).resolve()
        result = OCR_WORKER.call({"action": "transcribe", "png": image.read_bytes()})
        expected = sample["expected_latex"]
        passed = result.get("ok") and normalized(result["latex"]) == normalized(expected)
        correct += int(bool(passed))
        results.append(
            {
                "name": sample.get("name", image.name),
                "passed": bool(passed),
                "expected": expected,
                "actual": result.get("latex"),
                "result": result,
            }
        )
    report = {
        "samples": len(samples),
        "exact": correct,
        "exact_rate": correct / len(samples),
        "results": results,
    }
    print(json.dumps(report, indent=2))
    return 0 if correct == len(samples) else 1


if __name__ == "__main__":
    raise SystemExit(main())
