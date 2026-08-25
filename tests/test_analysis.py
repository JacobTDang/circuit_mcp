"""lcapy wrapper: netlist -> H(s), plus the finite gain-bandwidth workaround."""
import pytest
import sympy as sp

from circuit_mcp.analysis import (
    S, AssumptionError, transfer, ideal_limit, with_finite_gbw, poles,
)
from circuit_mcp.equivalence import equivalent

INVERTING = """
Vs 1 0 {V}
Ri 1 2 {Ri}
Rf 2 3 {Rf}
E1 3 0 opamp 0 2 {A}
"""


def test_s_carries_no_sign_assumptions():
    """Trap 2: a sign-restricted s makes pole solving silently return nothing.

    lcapy's own s carries complex/finite, which do NOT restrict sign and do not
    break solve(). Only positive/negative/nonnegative/nonpositive would.
    """
    for assumption in ("positive", "negative", "nonnegative", "nonpositive"):
        assert not S.assumptions0.get(assumption)


def test_S_is_lcapys_own_laplace_symbol():
    """Trap 3 at the source: a hand-made s would collide with lcapy's."""
    import lcapy
    assert S is lcapy.s.sympy


def test_rc_transfer_compares_against_truth_built_with_S():
    """The realistic case a bare S would have made raise on every RC circuit."""
    H = transfer("Vs 1 0 s {V}\nR1 1 2 {R}\nC1 2 0 {C}\n", 1, 0, 2, 0)
    syms = {str(x): x for x in H.free_symbols}
    truth = 1 / (1 + S * syms["R"] * syms["C"])
    assert equivalent(H, truth).equivalent


def test_transfer_of_inverting_amp_with_finite_gain():
    H = transfer(INVERTING, 1, 0, 3, 0)
    A, Ri, Rf = sp.symbols("A Ri Rf", positive=True)
    assert equivalent(H, -A * Rf / (A * Ri + Rf + Ri)).equivalent


def test_ideal_limit_collapses_to_textbook_gain():
    H = transfer(INVERTING, 1, 0, 3, 0)
    Ri, Rf = sp.symbols("Ri Rf", positive=True)
    assert equivalent(ideal_limit(H, "A"), -Rf / Ri).equivalent


def test_finite_gbw_matches_hand_derivation():
    """lcapy rejects s in a value field; substituting after the solve is equivalent."""
    H = with_finite_gbw(transfer(INVERTING, 1, 0, 3, 0), "A")
    A0, wp, Ri, Rf = sp.symbols("A0 wp Ri Rf", positive=True)
    truth = -A0 * Rf / (A0 * Ri + (Rf + Ri) * (1 + S / wp))
    assert equivalent(H, truth).equivalent


def test_finite_gbw_pole_is_the_gain_bandwidth_tradeoff():
    H = with_finite_gbw(transfer(INVERTING, 1, 0, 3, 0), "A")
    A0, wp, Ri, Rf = sp.symbols("A0 wp Ri Rf", positive=True)
    found = poles(H)
    assert len(found) == 1
    expected = -wp * (A0 * Ri + Rf + Ri) / (Rf + Ri)
    assert equivalent(found[0], expected).equivalent


def test_poles_rejects_an_expression_whose_s_is_assumed_positive():
    """Guard the trap rather than returning an empty pole list."""
    bad_s = sp.Symbol("s", positive=True)
    with pytest.raises(AssumptionError, match="assumption"):
        poles(1 / (bad_s + 1))


def test_ideal_limit_refuses_when_the_gain_symbol_is_absent():
    """sp.limit against an absent symbol returns the input unchanged, silently.

    Same family as trap 1: the transformation does nothing and raises nothing,
    so an RC lowpass would come back labelled "ideal" while being untouched.
    """
    from circuit_mcp.symbols import SubstitutionError
    H = transfer("Vs 1 0 s {V}\nR1 1 2 {R}\nC1 2 0 {C}\n", 1, 0, 2, 0)
    with pytest.raises(SubstitutionError, match="A"):
        ideal_limit(H, "A")


def test_substituting_a_missing_gain_symbol_is_loud():
    from circuit_mcp.symbols import SubstitutionError
    H = transfer(INVERTING, 1, 0, 3, 0)
    with pytest.raises(SubstitutionError):
        with_finite_gbw(H, "NotAGain")
