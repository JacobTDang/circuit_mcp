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

from .breadboard import BuildError
from .breadboard_view import layout_payload
from .expect import ExpectError, expectations
from .schematic import SchematicError, draw as draw_schematic, svg as schematic_svg
from .parsing import ParseError, parse_as_written, parse_expression
from .equivalence import equivalent
from .steps import check_steps, check_written
from .symbols import SubstitutionError
from .symbols import bind

KINDS = ("formula", "walkthrough", "vocabulary", "breadboard", "expected", "schematic",
         "solution")
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


def _breadboard(content: dict[str, Any]) -> dict[str, Any]:
    """A verified layout. The build is the agent's, drawn only after the student confirmed it."""
    try:
        return layout_payload(content)
    except BuildError as exc:
        raise CardError(f"breadboard refused: {exc}") from exc


def _expected(content: dict[str, Any]) -> dict[str, Any]:
    """What the bench should read, from ngspice, for the same build."""
    try:
        return expectations(content)
    except (BuildError, ExpectError) as exc:
        raise CardError(f"expectation refused: {exc}") from exc


def _schematic(content: dict[str, Any]) -> dict[str, Any]:
    """The circuit itself, drawn from the netlist derive verified.

    The drawing is checked against that netlist before it is returned, so a card
    can never show a circuit other than the one the maths was done on.
    """
    netlist = content.get("netlist")
    if not isinstance(netlist, str) or not netlist.strip():
        raise CardError("a schematic card needs a netlist")
    try:
        drawing = draw_schematic(netlist)
    except SchematicError as exc:
        raise CardError(f"schematic refused: {exc}") from exc
    return {
        "netlist": netlist,
        "svg": schematic_svg(drawing),
        "elements": [
            {"name": element.name, "kind": element.kind, "nodes": list(element.nodes)}
            for element in drawing.elements
        ],
    }


def _given(content: dict[str, Any]) -> list[dict[str, Any]]:
    """The values the problem states, each with its unit, as chips."""
    entries = content.get("given", [])
    if not isinstance(entries, list):
        raise CardError("given must be a list")
    if len(entries) > MAX_ITEMS:
        raise CardError(f"given may hold at most {MAX_ITEMS} entries")
    chips = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise CardError(f"given {index} must be an object")
        name = _text(entry.get("name"), f"given {index} name", 60)
        value = entry.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CardError(f"given {index} value must be a number")
        chips.append({"name": name, "value": value,
                      "unit": _text(entry.get("unit"), f"given {index} unit", 20, required=False)})
    return chips


def _solution(content: dict[str, Any]) -> dict[str, Any]:
    """A finished problem: what was given, the work, the answer, and its evidence.

    The steps are checked the way ``check_derivation`` checks them, with the
    given values substituted, so a solution card cannot show working that does
    not hold. The answer must equal the last step, because an answer box saying
    something the work never reached is the one thing a graded page must not do.
    """
    chips = _given(content)
    entries = _entries(content, "steps")
    answer = content.get("answer")
    if not isinstance(answer, dict):
        raise CardError("a solution needs an answer with its unit")
    answer_text = _text(answer.get("expression"), "answer expression", MAX_EXPRESSION)
    unit = _text(answer.get("unit"), "answer unit", 20, required=False)

    texts = [_text(entry.get("expression"), f"step {index} expression", MAX_EXPRESSION)
             for index, entry in enumerate(entries, start=1)]
    parameters = {chip["name"]: chip["value"] for chip in chips}

    def parse(text: Any, where: str, symbols: dict[str, sp.Symbol] | None) -> sp.Expr:
        return _expression(text, where, symbols)

    try:
        checked = check_written(parse, texts[:-1], texts[-1], parameters)
    except SubstitutionError as exc:
        raise CardError(f"solution refused: {exc}") from exc
    if not checked.result.ok:
        raise CardError(f"solution refused: {checked.result.message}")
    last = checked.evaluated_truth
    answer_expr = _expression(answer_text, "answer", dict(bind(checked.truth)))
    if not equivalent(answer_expr.subs(
            {bind(checked.truth)[name]: sp.Rational(str(value))
             for name, value in parameters.items() if name in bind(checked.truth)}), last).equivalent:
        raise CardError(
            f"solution refused: the answer {answer_text!r} is not what the last step reaches"
        )

    steps = []
    for index, entry in enumerate(entries, start=1):
        expression = texts[index - 1]
        expr = (checked.steps + [checked.truth])[index - 1]
        mathml, as_written = render(expression, expr)
        steps.append({
            "expression": expression,
            "note": _text(entry.get("note"), f"step {index} note", MAX_TEXT, required=False),
            "mathml": mathml, "as_written": as_written,
        })
    answer_mathml, answer_as_written = render(answer_text, answer_expr)
    payload = {
        "given": chips,
        "steps": steps,
        "answer": {"expression": answer_text, "unit": unit,
                   "mathml": answer_mathml, "as_written": answer_as_written},
        "verified": True,
        "attempt_id": _text(content.get("attempt_id"), "attempt_id", 64, required=False),
    }
    netlist = content.get("schematic")
    if netlist is not None:
        if not isinstance(netlist, str) or not netlist.strip():
            raise CardError("schematic must be a netlist")
        try:
            payload["schematic"] = {"netlist": netlist, "svg": schematic_svg(draw_schematic(netlist))}
        except SchematicError as exc:
            raise CardError(f"solution refused: {exc}") from exc
    return payload


_BUILDERS = {"formula": _formula, "walkthrough": _walkthrough, "vocabulary": _vocabulary,
             "breadboard": _breadboard, "expected": _expected, "schematic": _schematic,
             "solution": _solution}


def build_card(kind: str, title: str, content: Any) -> dict[str, Any]:
    """Validate and render one card, or refuse it with a reason."""
    if kind not in KINDS:
        raise CardError(f"kind must be one of {', '.join(KINDS)}; got {kind!r}")
    title = _text(title, "title", MAX_TITLE)
    if not isinstance(content, dict):
        raise CardError("content must be an object")
    return {"kind": kind, "title": title, "payload": _BUILDERS[kind](content)}
