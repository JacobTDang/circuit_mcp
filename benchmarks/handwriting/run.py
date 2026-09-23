"""Score a formula recogniser on this course's own handwriting.

    uv run python benchmarks/handwriting/run.py --model models/unimernet_small

Runs offline against the local OCR worker: every crop in the manifest goes
through the model, and the run is scored on exact match plus sign, subscript
and digit errors, then written to ``results/<model>-<stamp>.json`` beside this
file so one candidate can be compared with the last.

The manifest holds confirmed transcriptions only. ``--from-store`` rebuilds it
from the transcriptions confirmed in the command centre; until a page has been
confirmed there is nothing here to score against, and the run says so instead
of inventing labels from the model's own output.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from circuit_mcp.ocr_client import OCRWorker  # noqa: E402
from circuit_mcp.ocr_eval import EvalError, load_manifest, samples_from_store, score  # noqa: E402


def _predict(worker: OCRWorker, crop: Path) -> str:
    result = worker.call({"action": "transcribe", "png": crop.read_bytes()})
    if not result.get("ok"):
        raise EvalError(f"{crop.name}: {result.get('message') or result.get('error')}")
    return result.get("latex", "")


def _rebuild(manifest: Path) -> int:
    from circuit_mcp import paths
    from circuit_mcp.storage import CommandCenterDB

    database = CommandCenterDB(paths.data_dir() / "circuit_mcp.sqlite3")
    samples = samples_from_store(database)
    manifest.write_text(json.dumps(
        {"samples": [{"crop": s.crop, "label": s.label, "source": s.source} for s in samples]},
        indent=2, ensure_ascii=False) + "\n")
    print(f"{len(samples)} confirmed transcription(s) written to {manifest}")
    if not samples:
        print("Nothing has been confirmed yet, so there is no eval set to score against.")
    return 0


def _cut(samples: list, page: Path) -> int:
    """Cut the crops out of the page again.

    The crops are a student's own coursework and are not committed, so the
    manifest carries each box instead: the eval set can be rebuilt from the
    page it came from without the images ever being in git.
    """
    from PIL import Image

    image = Image.open(page)
    for sample in samples:
        if not sample.bbox:
            raise EvalError(f"{sample.crop} has no box to cut from")
        target = (HERE / sample.crop).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        image.crop(tuple(sample.bbox)).save(target)
    print(f"{len(samples)} crop(s) cut from {page}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=HERE / "manifest.json")
    parser.add_argument("--model", type=Path, help="model directory, for the record and the worker")
    parser.add_argument("--from-store", action="store_true",
                        help="rebuild the manifest from confirmed transcriptions and exit")
    parser.add_argument("--cut-crops", type=Path, metavar="PAGE_PNG",
                        help="cut every crop out of this page again, using the manifest's boxes")
    args = parser.parse_args(argv)

    if args.from_store:
        return _rebuild(args.manifest)

    if args.cut_crops:
        return _cut(load_manifest(args.manifest), args.cut_crops)

    samples = load_manifest(args.manifest)
    if not samples:
        print("The eval set is empty. Confirm a page's lines, then run --from-store.")
        return 1

    worker = OCRWorker()
    if args.model:
        model = args.model.resolve()
        worker._configuration = lambda: (  # the one knob a candidate needs
            OCRWorker()._configuration()[0], model, "auto")
    try:
        predictions = [_predict(worker, (HERE / sample.crop).resolve()) for sample in samples]
    finally:
        worker.shutdown()

    report = score(samples, predictions)
    report["model"] = str(args.model or "default")
    report["ran_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    results = HERE / "results"
    results.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (results / f"{(args.model.name if args.model else 'default')}-{stamp}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    print(f"{report['exact']}/{report['samples']} exact ({report['exact_rate']:.0%}) · "
          f"{report['sign_errors']} sign · {report['subscript_errors']} subscript · "
          f"{report['digit_errors']} digit")
    for miss in report["misses"]:
        print(f"  {miss['crop']}\n    want {miss['expected']}\n    got  {miss['actual']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
