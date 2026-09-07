"""Canvas cards: the agent writes the words, the server produces every piece of math.

Nothing on the canvas may assert an equality the checker has not proved. These
tests pin that boundary: an expression the parser rejects never becomes a card,
and a walkthrough with one bad transition is refused, naming the step.
"""
from __future__ import annotations

import pytest

from circuit_mcp.cards import KINDS, MAX_ITEMS, MAX_TEXT, CardError, build_card

MATH = '<math xmlns="http://www.w3.org/1998/Math/MathML"'


def test_the_kinds_are_exactly_the_ones_the_canvas_renders():
    assert KINDS == ("formula", "walkthrough", "vocabulary")


# --- formula ------------------------------------------------------------------

def test_a_formula_card_renders_every_expression_as_mathml():
    card = build_card("formula", "RC charging", {"items": [
        {"label": "capacitor voltage", "expression": "V_s*(1 - exp(-t/(R*C)))"},
        {"label": "time constant", "expression": "R*C"},
    ]})
    assert card["kind"] == "formula" and card["title"] == "RC charging"
    items = card["payload"]["items"]
    assert [item["label"] for item in items] == ["capacitor voltage", "time constant"]
    assert all(item["mathml"].startswith(MATH) for item in items)
    assert "<msub><mi>V</mi><mi>s</mi></msub>" in items[0]["mathml"]
    assert "<mfrac>" in items[0]["mathml"]
    assert items[1]["expression"] == "R*C"


def test_a_formula_the_checker_cannot_parse_never_becomes_a_card():
    with pytest.raises(CardError, match="item 2"):
        build_card("formula", "bad", {"items": [
            {"label": "fine", "expression": "R*C"},
            {"label": "broken", "expression": "V_s*(1 - exp(-t/(R*C))"},
        ]})


def test_a_formula_card_needs_at_least_one_item():
    with pytest.raises(CardError, match="at least one"):
        build_card("formula", "empty", {"items": []})


# --- walkthrough --------------------------------------------------------------

def test_a_walkthrough_whose_steps_all_hold_is_accepted_and_marked_verified():
    """Each transition is an identity, and the chain lands on the truth."""
    card = build_card("walkthrough", "RC charging", {
        "truth": "V_s*(1 - exp(-t/(R*C)))",
        "steps": [
            {"expression": "(V_s*R*C - V_s*R*C*exp(-t/(R*C)))/(R*C)", "note": "over a common denominator"},
            {"expression": "V_s - V_s*exp(-t/(R*C))", "note": "cancel RC"},
            {"expression": "V_s*(1 - exp(-t/(R*C)))", "note": "factor out V_s"},
        ],
    })
    payload = card["payload"]
    assert payload["verified"] is True
    assert len(payload["steps"]) == 3
    assert payload["steps"][1]["note"] == "cancel RC"
    assert all(step["mathml"].startswith(MATH) for step in payload["steps"])
    assert payload["truth"]["mathml"].startswith(MATH)


def test_a_physics_substitution_is_not_an_algebra_step():
    """I -> C*dV_C is a circuit law, not an identity. That belongs to check_setup;
    a walkthrough is the algebra after setup, so the card is refused."""
    with pytest.raises(CardError, match=r"[Ss]tep 1 -> 2"):
        build_card("walkthrough", "KVL", {
            "truth": "V_s - V_C - C*R*dV_C",
            "steps": [
                {"expression": "V_s - V_C - R*I", "note": "KVL around the loop"},
                {"expression": "V_s - V_C - R*C*dV_C", "note": "the capacitor current is C dV/dt"},
            ],
        })


def test_a_walkthrough_with_one_bad_transition_is_refused_naming_the_step():
    with pytest.raises(CardError, match=r"[Ss]tep 2 -> 3"):
        build_card("walkthrough", "wrong", {
            "truth": "V_s*(1 - exp(-t/(R*C)))",
            "steps": [
                {"expression": "V_s*(1 - exp(-t/(R*C)))", "note": "start"},
                {"expression": "V_s*(1 - exp(-t/(R*C)))", "note": "same"},
                {"expression": "V_s*(1 + exp(-t/(R*C)))", "note": "sign slipped"},
            ],
        })


def test_a_walkthrough_that_holds_but_lands_on_the_wrong_answer_is_refused():
    with pytest.raises(CardError, match="does not"):
        build_card("walkthrough", "setup", {
            "truth": "V_s*(1 - exp(-t/(R*C)))",
            "steps": [{"expression": "V_s*exp(-t/(R*C))", "note": "discharging, not charging"}],
        })


def test_a_walkthrough_uses_the_truths_symbols_so_lcapy_assumptions_do_not_bite():
    """A symbol parsed fresh must bind onto the truth's, or correct work looks wrong."""
    card = build_card("walkthrough", "bind", {
        "truth": "1/(1 + s*R*C)",
        "steps": [
            {"expression": "(1/(s*C))/(R + 1/(s*C))", "note": "voltage divider"},
            {"expression": "1/(s*C*R + 1)", "note": "multiply through by sC"},
        ],
    })
    assert card["payload"]["verified"] is True


# --- vocabulary ---------------------------------------------------------------

def test_a_vocabulary_card_keeps_definitions_as_text_and_renders_optional_math():
    card = build_card("vocabulary", "terms", {"terms": [
        {"term": "time constant", "definition": "how long the capacitor takes to reach 63% of its final voltage",
         "expression": "R*C"},
        {"term": "KVL", "definition": "voltages around any closed loop sum to zero"},
    ]})
    terms = card["payload"]["terms"]
    assert terms[0]["mathml"].startswith(MATH)
    assert "mathml" not in terms[1]
    assert terms[1]["definition"].startswith("voltages")


def test_vocabulary_math_is_parsed_like_everything_else():
    with pytest.raises(CardError, match="term 1"):
        build_card("vocabulary", "terms", {"terms": [
            {"term": "broken", "definition": "x", "expression": "R*C)"},
        ]})


# --- bounds and shape ---------------------------------------------------------

def test_an_unknown_kind_is_refused():
    with pytest.raises(CardError, match="kind"):
        build_card("diagram", "t", {})


def test_titles_and_text_are_bounded():
    with pytest.raises(CardError, match="title"):
        build_card("formula", "", {"items": [{"label": "l", "expression": "R"}]})
    with pytest.raises(CardError, match="title"):
        build_card("formula", "x" * 200, {"items": [{"label": "l", "expression": "R"}]})
    with pytest.raises(CardError, match="note"):
        build_card("walkthrough", "t", {"truth": "R", "steps": [
            {"expression": "R", "note": "n" * (MAX_TEXT + 1)}]})


def test_item_counts_are_bounded():
    with pytest.raises(CardError, match=str(MAX_ITEMS)):
        build_card("formula", "many", {"items": [
            {"label": f"l{i}", "expression": "R"} for i in range(MAX_ITEMS + 1)]})


def test_text_is_stored_as_text_not_markup():
    """The browser escapes on render; the card must not pre-bake HTML either way."""
    card = build_card("formula", "<b>title</b>", {"items": [
        {"label": "<i>label</i>", "expression": "R"}]})
    assert card["title"] == "<b>title</b>"
    assert card["payload"]["items"][0]["label"] == "<i>label</i>"


def test_content_must_be_a_mapping_with_the_right_shape():
    with pytest.raises(CardError, match="items"):
        build_card("formula", "t", {"steps": []})
    with pytest.raises(CardError, match="steps"):
        build_card("walkthrough", "t", {"truth": "R"})
    with pytest.raises(CardError, match="expression"):
        build_card("formula", "t", {"items": [{"label": "no expression"}]})


# --- math is shown as written, not as SymPy sorts it -------------------------

def test_formulas_render_in_the_order_they_were_written():
    """SymPy sorts R*C to C*R and moves exp() into a denominator. A student is
    learning the textbook form, so the card shows what was typed."""
    card = build_card("formula", "order", {"items": [
        {"label": "tau", "expression": "R*C"},
        {"label": "current", "expression": "(V_s/R)*exp(-t/(R*C))"},
        {"label": "poly", "expression": "x^2 - 2*x + 1"},
    ]})
    tau, current, poly = (item["mathml"] for item in card["payload"]["items"])
    assert tau.index("<mi>R</mi>") < tau.index("<mi>C</mi>")
    assert "<mfrac>" in current and "<msub><mi>V</mi><mi>s</mi></msub>" in current
    # exp stays in the numerator: e appears before the closing of the first fraction row
    assert current.index("&ExponentialE;") < current.index("</mfrac>")
    assert poly.index("<msup>") < poly.index("<mn>2</mn><mo>&InvisibleTimes;</mo>") < poly.rindex("<mn>1</mn>")
    assert all(item["as_written"] is True for item in card["payload"]["items"])


def test_when_the_written_form_cannot_be_laid_out_the_canonical_form_is_flagged(monkeypatch):
    """The fallback is visible in the payload, never silent."""
    from circuit_mcp import cards
    from circuit_mcp.parsing import ParseError

    def refuse(text):
        raise ParseError("no layout")

    monkeypatch.setattr(cards, "parse_as_written", refuse)
    card = build_card("formula", "canonical", {"items": [{"label": "tau", "expression": "R*C"}]})
    item = card["payload"]["items"][0]
    assert item["as_written"] is False
    assert item["mathml"].startswith(MATH)
