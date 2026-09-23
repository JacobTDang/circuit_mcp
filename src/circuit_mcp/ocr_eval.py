"""Score a formula recogniser against confirmed transcriptions.

Segmentation finds the right lines; what comes back for each line is often
wrong on handwriting. Deciding whether another model is better needs a number,
and one number is not enough: a wrong bracket is untidy, while a lost minus
sign or a changed subscript makes a circuit derivation wrong. The real failure
was ``vb = va -> G = 10`` coming back as ``u_b = v_a -> v = 0`` -- a changed
subscript and a changed result in one line.

So a run reports exact match plus those three error classes separately, and a
candidate is only worth adopting if it improves exact match without making any
of them worse.

What the three classes do not catch is a changed stem: the ``G -> v`` half of
that same miss is neither a sign, a subscript nor a digit, and shows up only in
exact match. That is a real gap rather than an oversight -- naming it needs a
symbol-level alignment, which is a bigger thing than counting -- so exact match
stays the headline number and the misses are printed in full for reading.

Labels come from confirmed transcriptions. A prediction is never a label: it is
the thing being judged, and scoring a model against its own output measures
nothing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SUBSCRIPT = re.compile(r"_\{([^}]*)\}|_(\w)")
DIGIT = re.compile(r"\d")
SIGN = re.compile(r"(?<![\w}])-")
WHITESPACE = re.compile(r"\s+")
WRAPPER = re.compile(r"^\$+|\$+$")


class EvalError(ValueError):
    """An eval set that cannot be scored as it stands."""


@dataclass(frozen=True)
class Sample:
    """One line crop and the transcription confirmed for it.

    ``bbox`` is where the line sat on its source page, so a crop that was never
    committed can be cut again from the page it came from.
    """
    crop: str
    label: str
    source: str = ""
    note: str = ""
    bbox: tuple[int, ...] = ()


def normalise(latex: str) -> str:
    """Whitespace and ``$`` wrappers are not what is being judged."""
    return WHITESPACE.sub("", WRAPPER.sub("", latex.strip()))


def _subscripts(latex: str) -> list[str]:
    return sorted(match.group(1) or match.group(2) for match in SUBSCRIPT.finditer(latex))


def _multiset_difference(first: Iterable[str], second: Iterable[str]) -> int:
    """How many entries would have to change to turn one into the other."""
    remaining = list(second)
    wrong = 0
    for item in first:
        if item in remaining:
            remaining.remove(item)
        else:
            wrong += 1
    return wrong + len(remaining)


def score_one(expected: str, actual: str) -> dict[str, Any]:
    """One line, judged by the things that break a derivation."""
    want, got = normalise(expected), normalise(actual)
    return {
        "exact": want == got,
        "sign_errors": abs(len(SIGN.findall(want)) - len(SIGN.findall(got))),
        "subscript_errors": _multiset_difference(_subscripts(want), _subscripts(got)),
        "digit_errors": _multiset_difference(DIGIT.findall(want), DIGIT.findall(got)),
        "expected": want,
        "actual": got,
    }


def score(samples: list[Sample], predictions: list[str]) -> dict[str, Any]:
    """A whole run: the totals, and every line that was not exact."""
    if len(samples) != len(predictions):
        raise EvalError(f"{len(samples)} samples but {len(predictions)} predictions")
    if not samples:
        raise EvalError(
            "the eval set is empty. Labels come from confirmed transcriptions, so "
            "confirm the page's lines first, then rebuild the manifest."
        )
    lines = [{**score_one(sample.label, prediction), "crop": sample.crop}
             for sample, prediction in zip(samples, predictions)]
    return {
        "samples": len(lines),
        "exact": sum(line["exact"] for line in lines),
        "exact_rate": round(sum(line["exact"] for line in lines) / len(lines), 4),
        "sign_errors": sum(line["sign_errors"] for line in lines),
        "subscript_errors": sum(line["subscript_errors"] for line in lines),
        "digit_errors": sum(line["digit_errors"] for line in lines),
        "misses": [line for line in lines if not line["exact"]],
    }


def is_better(candidate: dict[str, Any], baseline: dict[str, Any]) -> bool:
    """Adopt only for more exact matches and no more of the errors that matter.

    A model that reads more lines perfectly while losing an extra minus sign is
    not an improvement for this: the sign is the part a wrong answer turns on.
    """
    return (
        candidate["exact"] > baseline["exact"]
        and candidate["sign_errors"] <= baseline["sign_errors"]
        and candidate["subscript_errors"] <= baseline["subscript_errors"]
    )


def load_manifest(path: Path) -> list[Sample]:
    """The eval set as committed. Crops live beside the manifest, not in git."""
    data = json.loads(Path(path).read_text())
    samples = []
    for index, entry in enumerate(data.get("samples", []), start=1):
        if not isinstance(entry, dict) or not entry.get("crop") or not entry.get("label"):
            raise EvalError(f"sample {index} needs a crop and a confirmed label")
        samples.append(Sample(entry["crop"], entry["label"], entry.get("source", ""),
                              entry.get("note", ""), tuple(entry.get("bbox", ()))))
    return samples


def samples_from_store(database: Any, kind: str = "expression") -> list[Sample]:
    """Build an eval set from the transcriptions that have been confirmed.

    Only confirmed ones: an unconfirmed transcription is a model's output, and
    scoring a model against its own output measures nothing at all.
    """
    with database._connect() as connection:
        rows = connection.execute(
            "SELECT t.id, t.content, t.document_id FROM transcriptions t "
            "WHERE t.confirmed_at IS NOT NULL AND t.kind=? ORDER BY t.created_at", (kind,)
        ).fetchall()
    return [Sample(crop=f"crops/{row['id']}.png", label=row["content"],
                   source=row["document_id"] or "") for row in rows]
