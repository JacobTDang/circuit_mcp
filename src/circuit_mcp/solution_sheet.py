"""One assignment as one readable page: the work, the answers, and the evidence.

The course grades shown work and takes one PDF, so the deliverable is a page a
student can read, check and hand in -- not a feed of cards. It is rendered on
the server rather than in ``app.js`` so that what is handed in is what the tests
assert, line for line.

Every text field is escaped here. The MathML comes from the card builder, which
produced it from an expression the parser accepted, so it is the one thing on
the page that is markup by intent rather than by accident.
"""
from __future__ import annotations

from html import escape
from typing import Any

MISSING = "no solution yet"


def _chip(given: dict[str, Any]) -> str:
    value = given["value"]
    shown = f"{value:g}" if isinstance(value, (int, float)) else str(value)
    unit = f" {escape(given['unit'])}" if given.get("unit") else ""
    return f'<li><b>{escape(given["name"])}</b> = {escape(shown)}{unit}</li>'


def _step(index: int, step: dict[str, Any]) -> str:
    note = f'<p class="note">{escape(step["note"])}</p>' if step.get("note") else ""
    return (f'<li><div class="math">{step["mathml"]}</div>{note}</li>')


def _evidence(calls: list[dict[str, Any]]) -> str:
    """What was checked, and how it came out, from the recorded tool calls."""
    if not calls:
        return '<p class="evidence none">no checks recorded against this attempt</p>'
    shown = ", ".join(
        f'{escape(call["tool_name"])} <span class="verdict {escape(call.get("verdict") or "")}">'
        f'{escape(call.get("verdict") or "recorded")}</span>'
        for call in calls
    )
    return f'<p class="evidence">checked with {shown}</p>'


def _solution(payload: dict[str, Any], calls: list[dict[str, Any]]) -> str:
    given = "".join(_chip(chip) for chip in payload.get("given", []))
    steps = "".join(_step(index, step) for index, step in enumerate(payload.get("steps", []), start=1))
    answer = payload.get("answer", {})
    unit = f' <span class="unit">{escape(answer.get("unit", ""))}</span>' if answer.get("unit") else ""
    schematic = payload.get("schematic")
    drawing = f'<figure class="schematic">{schematic["svg"]}</figure>' if schematic else ""
    return (
        (f'<ul class="given">{given}</ul>' if given else "")
        + drawing
        + (f'<ol class="steps">{steps}</ol>' if steps else "")
        + f'<div class="answer"><span class="label">answer</span>'
          f'<span class="math">{answer.get("mathml", "")}</span>{unit}</div>'
        + _evidence(calls)
    )


def render(assignment: str, problems: list[dict[str, Any]]) -> str:
    """The whole sheet. ``problems`` is in the order it should be handed in."""
    blocks = []
    for index, problem in enumerate(problems, start=1):
        solution = problem.get("solution")
        body = (_solution(solution["payload"], problem.get("tool_calls", []))
                if solution else f'<p class="missing">{MISSING}</p>')
        blocks.append(
            f'<article class="problem"><header><span class="number">{index}</span>'
            f'<h2>{escape(problem["title"])}</h2></header>'
            f'<p class="prompt">{escape(problem.get("prompt", ""))}</p>{body}</article>'
        )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{escape(assignment)} solutions</title>"
        '<link rel="stylesheet" href="/assets/solution.css">'
        "</head><body>"
        f'<header class="sheet"><h1>{escape(assignment)}</h1>'
        '<button type="button" onclick="window.print()">Export PDF</button></header>'
        f'<main>{"".join(blocks)}</main>'
        "</body></html>"
    )
