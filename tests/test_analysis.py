"""lcapy wrapper: netlist -> H(s), plus the finite gain-bandwidth workaround."""
import pytest
import sympy as sp

from circuit_mcp.analysis import (
    S, AssumptionError, transfer, ideal_limit, with_finite_gbw, poles,
)
from circuit_mcp.equivalence import equivalent
from circuit_mcp.symbols import bind

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


# --- a current input, and the resistance looking into a port -------------------

# Module 2 HW1 Problem 3: a current source into the summing node (1), and a T
# network -- R1 to the mid node (2), R2 from there to ground, R3 on to the output.
T_NETWORK = """
Is 0 1 {Is}
R1 1 2 {R1}
R2 2 0 {R2}
R3 2 3 {R3}
E1 3 0 opamp 0 1 {A}
"""

# Problem 2.9 (a)-(d): four inverting amplifiers, each fed through 15 kOhm.
P29 = {
    "a": "R1 1 2 15e3\nRf 2 3 90e3\nE1 3 0 opamp 0 2 {A}",
    "b": "R1 1 2 15e3\nRf 2 3 90e3\nRl 3 0 15e3\nE1 3 0 opamp 0 2 {A}",
    "c": "R1 1 2 15e3\nRs 2 0 15e3\nRf 2 3 90e3\nE1 3 0 opamp 0 2 {A}",
    "d": "R1 1 2 15e3\nRf 2 3 90e3\nRp 4 0 15e3\nE1 3 0 opamp 4 2 {A}",
}


def test_a_current_input_derives_the_t_network_transresistance():
    """The T network is why this exists: the current source had to be hand-mapped before."""
    from circuit_mcp.analysis import transfer

    result = ideal_limit(transfer(T_NETWORK, 0, 1, 3, 0, kind="current"))
    # Built from the result's own symbols: lcapy's carry assumptions, and a bare
    # Symbol("R1") is a different object that compares unequal while printing the same.
    known = bind(result)
    R1, R2, R3 = known["R1"], known["R2"], known["R3"]
    assert equivalent(result, (R1 * R2 + R1 * R3 + R2 * R3) / R2).equivalent


def test_the_current_input_direction_sets_the_sign():
    """Current into the summing node inverts; out of it does not."""
    from circuit_mcp.analysis import transfer

    into_node = ideal_limit(transfer(T_NETWORK, 1, 0, 3, 0, kind="current"))
    out_of_node = ideal_limit(transfer(T_NETWORK, 0, 1, 3, 0, kind="current"))
    assert equivalent(into_node, -out_of_node).equivalent


def test_an_unknown_input_kind_is_refused():
    from circuit_mcp.analysis import transfer

    with pytest.raises(ValueError, match="voltage"):
        transfer(INVERTING, 1, 0, 3, 0, kind="charge")


@pytest.mark.parametrize("name", sorted(P29))
def test_port_impedance_is_the_series_resistor_on_every_2_9_circuit(name):
    from circuit_mcp.analysis import port_impedance

    assert ideal_limit(port_impedance(P29[name], 1, 0)) == 15000


def test_a_source_across_the_port_is_removed_rather_than_killed():
    """A killed voltage source is a short, so leaving it in would answer 0 every time."""
    from circuit_mcp.analysis import port_impedance

    driven = "Vi 1 0 {Vi}\n" + P29["a"]
    assert ideal_limit(port_impedance(driven, 1, 0)) == 15000


def test_a_shorted_port_is_refused_rather_than_answered_zero():
    from circuit_mcp.analysis import PortError, port_impedance

    with pytest.raises(PortError, match="short"):
        port_impedance("W 1 2\nR1 2 0 1e3", 1, 2)


def test_a_port_the_circuit_does_not_reach_is_refused():
    from circuit_mcp.analysis import PortError, port_impedance

    with pytest.raises(PortError):
        port_impedance("R1 1 0 1e3\nR2 2 3 1e3", 2, 3)
