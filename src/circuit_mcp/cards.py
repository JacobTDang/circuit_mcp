"""Canvas cards: what the agent puts beside the student's work.

The agent writes the words; the server produces every piece of math. An
expression the checker cannot parse never reaches the canvas, and a walkthrough
whose steps do not hold is refused outright, so nothing on the canvas asserts
an equality the checker has not proved.

Text fields are stored as the agent wrote them. The browser escapes them on
render; the MathML is the one thing inserted as markup, and it comes from
SymPy, never from the agent.
"""
from __future__ import annotations

from typing import Any

import sympy as sp
from sympy.printing.mathml import MathMLPresentationPrinter

from .parsing import ParseError, parse_as_written, parse_expression
from .steps import check_steps
from .symbols import bind

KINDS = ("formula", "walkthrough", "vocabulary")
MAX_ITEMS = 24
MAX_TITLE = 120
MAX_TEXT = 600
MAX_EXPRESSION = 2_000


class CardError(ValueError):
    """A card was refused. The message says which part, and why."""


class _WrittenOrder(MathMLPresentationPrinter):
    """Print in the order the expression was written.

    ``order='old'`` keeps a product's factors as given; it would then sort a
    sum's terms by SymPy's own comparison, so terms come straight from the
    arguments instead. Only meaningful on a tree from ``parse_as_written``.
    """

    def _as_ordered_terms(self, expr, order=None):
        return list(expr.args)


_PRINTER = _WrittenOrder({"order": "old"})


def render(text: str, evaluated: sp.Expr) -> tuple[str, bool]:
    """Presentation MathML the browser renders natively -- no library, no CDN.

    Laid out as the text was written when SymPy can build that tree; otherwise
    the canonical form, with the second value ``False`` so the card says so.
    """
    try:
        tree, as_written = parse_as_written(text), True
    except ParseError:
        tree, as_written = evaluated, False
    body = _PRINTER.doprint(tree)
    return f'<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">{body}</math>', as_written


def _text(value: Any, where: str, limit: int, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise CardError(f"{where} must be text")
    value = value.strip()
    if required and not value:
        raise CardError(f"{where} must not be empty")
    if len(value) > limit:
        raise CardError(f"{where} exceeds {limit} characters")
    return value


def _expression(value: Any, where: str, symbols: dict[str, sp.Symbol] | None = None) -> sp.Expr:
    text = _text(value, f"{where} expression", MAX_EXPRESSION)
    try:
        return parse_expression(text, symbols)
    except ParseError as exc:
        raise CardError(f"{where}: {exc}") from exc


def _entries(content: dict[str, Any], key: str) -> list[dict[str, Any]]:
    entries = content.get(key)
    if not isinstance(entries, list):
        raise CardError(f"content needs a list under {key!r}")
    if not entries:
        raise CardError(f"{key} needs at least one entry")
    if len(entries) > MAX_ITEMS:
        raise CardError(f"{key} may hold at most {MAX_ITEMS} entries")
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise CardError(f"{key} entry {index} must be an object")
    return entries


def _formula(content: dict[str, Any]) -> dict[str, Any]:
    items = []
    for index, entry in enumerate(_entries(content, "items"), start=1):
        where = f"item {index}"
        expr = _expression(entry.get("expression"), where)
        mathml, as_written = render(entry["expression"], expr)
        items.append({
            "label": _text(entry.get("label"), f"{where} label", MAX_TEXT, required=False),
            "expression": entry["expression"].strip(),
            "mathml": mathml, "as_written": as_written,
        })
    return {"items": items}


def _walkthrough(content: dict[str, Any]) -> dict[str, Any]:
    """Ordered steps that must each be an equality and must reach the truth.

    Symbols bind the way ``check_derivation`` binds them: the truth seeds the
    table and each step adds what it introduces, so a freshly parsed ``C``
    compares equal to the ``C`` the truth already carries.
    """
    truth = _expression(content.get("truth"), "truth")
    known = dict(bind(truth))
    parsed: list[sp.Expr] = []
    steps = []
    for index, entry in enumerate(_entries(content, "steps"), start=1):
        where = f"step {index}"
        expr = _expression(entry.get("expression"), where, symbols=dict(known))
        known.update(bind(expr))
        parsed.append(expr)
        mathml, as_written = render(entry["expression"], expr)
        steps.append({
            "expression": entry["expression"].strip(),
            "note": _text(entry.get("note"), f"{where} note", MAX_TEXT, required=False),
            "mathml": mathml, "as_written": as_written,
        })
    result = check_steps(parsed, truth)
    if not result.ok:
        raise CardError(f"walkthrough refused: {result.message}")
    truth_mathml, truth_as_written = render(content["truth"], truth)
    return {
        "truth": {"expression": content["truth"].strip(), "mathml": truth_mathml,
                  "as_written": truth_as_written},
        "steps": steps,
        "verified": True,
    }


def _vocabulary(content: dict[str, Any]) -> dict[str, Any]:
    terms = []
    for index, entry in enumerate(_entries(content, "terms"), start=1):
        where = f"term {index}"
        term = {
            "term": _text(entry.get("term"), f"{where} name", MAX_TEXT),
            "definition": _text(entry.get("definition"), f"{where} definition", MAX_TEXT),
        }
        if entry.get("expression") is not None:
            expr = _expression(entry.get("expression"), where)
            term["expression"] = entry["expression"].strip()
            term["mathml"], term["as_written"] = render(entry["expression"], expr)
        terms.append(term)
    return {"terms": terms}


_BUILDERS = {"formula": _formula, "walkthrough": _walkthrough, "vocabulary": _vocabulary}


def build_card(kind: str, title: str, content: Any) -> dict[str, Any]:
    """Validate and render one card, or refuse it with a reason."""
    if kind not in KINDS:
        raise CardError(f"kind must be one of {', '.join(KINDS)}; got {kind!r}")
    title = _text(title, "title", MAX_TITLE)
    if not isinstance(content, dict):
        raise CardError("content must be an object")
    return {"kind": kind, "title": title, "payload": _BUILDERS[kind](content)}
