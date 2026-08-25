"""Setup check: does this system of equations describe this circuit?

The tests deliberately lean on *differently formulated but correct* systems.
Flagging correct-but-different work is the worst thing this tool can do, so
that case gets more coverage than the failing cases.
"""
import pytest
import sympy as sp

from circuit_mcp.analysis import S
from circuit_mcp.mna import SetupResult, check_setup, circuit_equations
from circuit_mcp.symbols import bind

DIVIDER = """
Vs 1 0 {V}
R1 1 2 {R1}
R2 2 0 {R2}
"""

INVERTING = """
Vs 1 0 {V}
Ri 1 2 {Ri}
Rf 2 3 {Rf}
E1 3 0 opamp 0 2 {A}
"""

RC_LOWPASS = """
Vs 1 0 s {V}
R1 1 2 {R}
C1 2 0 {C}
"""

# Deliberately assumption-free, i.e. *not* the symbols lcapy builds. A student
# writing plain sympy must still be understood -- see trap 1.
V, R1, R2 = sp.symbols("V R1 R2")
A, Ri, Rf = sp.symbols("A Ri Rf")
R, C = sp.symbols("R C")
V1, V2, V3 = sp.symbols("V1 V2 V3")
I_R1 = sp.Symbol("I_R1")


# --------------------------------------------------------------------------
# circuit_equations
# --------------------------------------------------------------------------

def test_circuit_equations_gives_lcapy_nodal_system_for_a_divider():
    system = circuit_equations(DIVIDER)
    assert system["unknowns"] == ["V1", "V2"]
    assert len(system["equations"]) == 2
    assert all(isinstance(eq, sp.Eq) for eq in system["equations"])
    assert system["display"]  # human-readable rendering exists
    assert system["note"] == ""


def test_circuit_equations_reports_the_true_node_voltages():
    system = circuit_equations(DIVIDER)
    solved = system["node_voltages"]
    assert solved["V0"] == 0
    # bind(), not sympy.Symbol: the returned expression holds lcapy's own
    # positive-assumption symbols, and a fresh Symbol("V") would not match.
    lcapy = bind(solved["V2"])
    assert sp.simplify(solved["V1"] - bind(solved["V1"])["V"]) == 0
    expected = lcapy["R2"] * lcapy["V"] / (lcapy["R1"] + lcapy["R2"])
    assert sp.simplify(solved["V2"] - expected) == 0


def test_a_freshly_built_symbol_does_not_match_lcapys():
    """Trap 1, stated outright: this is why bind() exists."""
    solved = circuit_equations(DIVIDER)["node_voltages"]["V1"]
    assert sp.simplify(solved - sp.Symbol("V")) != 0
    assert sp.simplify(solved - bind(solved)["V"]) == 0


def test_circuit_equations_reports_branch_currents():
    current = circuit_equations(DIVIDER)["branch_currents"]["I_R1"]
    lcapy = bind(current)
    expected = lcapy["V"] / (lcapy["R1"] + lcapy["R2"])
    assert sp.simplify(current - expected) == 0


def test_circuit_equations_is_honest_about_dependent_sources():
    """lcapy's nodal analysis refuses op-amps; say so instead of pretending."""
    system = circuit_equations(INVERTING)
    assert system["equations"] == []
    assert "dependent source" in system["note"].lower()
    # The solution is still available, which is what the setup check needs.
    solved = system["node_voltages"]["V3"]
    l = bind(solved)
    expected = -l["A"] * l["Rf"] * l["V"] / (l["A"] * l["Ri"] + l["Rf"] + l["Ri"])
    assert sp.simplify(solved - expected) == 0


def test_circuit_equations_on_a_malformed_netlist_raises():
    with pytest.raises(ValueError):
        circuit_equations("this is not a netlist")


# --------------------------------------------------------------------------
# check_setup -- the correct cases
# --------------------------------------------------------------------------

def test_correct_nodal_setup_passes():
    equations = [
        sp.Eq(V1, V),
        sp.Eq((V1 - V2) / R1, V2 / R2),
    ]
    result = check_setup(DIVIDER, equations, ["V1", "V2"])
    assert result.ok, result.message
    assert result.kind == "ok"
    assert result.failing_equation is None


def test_a_differently_formulated_but_correct_system_passes():
    """Reordered, scaled through, and written with the opposite sign sense.

    All three are things a student legitimately does. None is an error.
    """
    equations = [
        sp.Eq(V2 * (R1 + R2) - R2 * V1, 0),   # KCL, cleared of fractions
        sp.Eq(V - V1, 0),                     # source constraint, flipped
    ]
    result = check_setup(DIVIDER, equations, ["V1", "V2"])
    assert result.ok, result.message
    assert result.kind == "ok"


def test_a_redundant_extra_equation_is_not_an_error():
    """KCL at the reference node is implied, not wrong."""
    equations = [
        sp.Eq(V1, V),
        sp.Eq((V1 - V2) / R1, V2 / R2),
        sp.Eq((V1 - V2) / R1 + (0 - V2) / R2, 0),   # same law, restated
    ]
    result = check_setup(DIVIDER, equations, ["V1", "V2"])
    assert result.ok, result.message


def test_branch_current_unknowns_are_supported():
    equations = [
        sp.Eq(I_R1, (V - V2) / R1),
        sp.Eq(I_R1, V2 / R2),
    ]
    result = check_setup(DIVIDER, equations, ["V2", "I_R1"])
    assert result.ok, result.message


def test_a_mesh_formulation_of_the_same_circuit_passes():
    """One mesh current instead of two node voltages -- a different system entirely."""
    equations = [sp.Eq(V, I_R1 * R1 + I_R1 * R2)]   # KVL round the single loop
    result = check_setup(DIVIDER, equations, ["I_R1"])
    assert result.ok, result.message


def test_a_wrong_mesh_equation_is_still_caught():
    equations = [sp.Eq(V, I_R1 * R1 - I_R1 * R2)]
    result = check_setup(DIVIDER, equations, ["I_R1"])
    assert not result.ok
    assert result.kind == "not_satisfied"
    assert result.failing_equation == 0


def test_inverting_opamp_setup_passes():
    equations = [
        sp.Eq(V1, V),
        sp.Eq((V1 - V2) / Ri + (V3 - V2) / Rf, 0),   # KCL at the summing node
        sp.Eq(V3, A * (0 - V2)),                     # finite-gain op-amp
    ]
    result = check_setup(INVERTING, equations, ["V1", "V2", "V3"])
    assert result.ok, result.message


def test_inverting_opamp_alternative_formulation_passes():
    equations = [
        sp.Eq(V3 + A * V2, 0),                       # op-amp law, rearranged
        sp.Eq(Rf * (V1 - V2) + Ri * (V3 - V2), 0),   # KCL scaled by Ri*Rf
        sp.Eq(V1 - V, 0),
    ]
    result = check_setup(INVERTING, equations, ["V1", "V2", "V3"])
    assert result.ok, result.message


def test_laplace_domain_setup_passes_with_an_assumption_free_s():
    """Trap 1 again: lcapy's ``s`` is not ``sympy.Symbol('s')``."""
    assert S != sp.Symbol("s", positive=True)
    equations = [
        sp.Eq(V1, V),
        sp.Eq((V1 - V2) / R, S * C * V2),
    ]
    result = check_setup(RC_LOWPASS, equations, ["V1", "V2"])
    assert result.ok, result.message


def test_lcapy_symbols_in_the_student_equations_also_pass():
    """The other direction: symbols that *do* carry lcapy's assumptions."""
    pos_V, pos_R1, pos_R2 = sp.symbols("V R1 R2", positive=True)
    equations = [
        sp.Eq(V1, pos_V),
        sp.Eq((V1 - V2) / pos_R1, V2 / pos_R2),
    ]
    result = check_setup(DIVIDER, equations, ["V1", "V2"])
    assert result.ok, result.message


# --------------------------------------------------------------------------
# check_setup -- the failing cases
# --------------------------------------------------------------------------

def test_a_single_wrong_sign_is_located():
    equations = [
        sp.Eq(V1, V),
        sp.Eq((V1 - V2) / R1, -V2 / R2),   # sign flipped here
    ]
    result = check_setup(DIVIDER, equations, ["V1", "V2"])
    assert not result.ok
    assert result.kind == "not_satisfied"
    assert result.failing_equation == 1
    assert result.counterexample is not None


def test_a_wrong_sign_in_the_opamp_law_is_located():
    equations = [
        sp.Eq(V1, V),
        sp.Eq((V1 - V2) / Ri + (V3 - V2) / Rf, 0),
        sp.Eq(V3, A * V2),   # should be -A*V2
    ]
    result = check_setup(INVERTING, equations, ["V1", "V2", "V3"])
    assert not result.ok
    assert result.kind == "not_satisfied"
    assert result.failing_equation == 2


def test_the_first_wrong_equation_is_the_one_reported():
    equations = [
        sp.Eq(V1, 2 * V),                  # wrong
        sp.Eq((V1 - V2) / R1, -V2 / R2),   # also wrong
    ]
    result = check_setup(DIVIDER, equations, ["V1", "V2"])
    assert result.failing_equation == 0


def test_a_missing_equation_is_underdetermined():
    equations = [sp.Eq((V1 - V2) / R1, V2 / R2)]   # KCL only, no source law
    result = check_setup(DIVIDER, equations, ["V1", "V2"])
    assert not result.ok
    assert result.kind == "underdetermined"
    assert result.failing_equation is None


def test_trivially_true_equations_are_underdetermined_not_ok():
    """Satisfaction alone passes 0 == 0; the rank check is what catches it."""
    equations = [sp.Eq(V1 - V1, 0), sp.Eq(V2 - V2, 0)]
    result = check_setup(DIVIDER, equations, ["V1", "V2"])
    assert not result.ok
    assert result.kind == "underdetermined"


def test_a_restated_equation_does_not_count_towards_rank():
    equations = [
        sp.Eq(V1, V),
        sp.Eq(2 * V1, 2 * V),   # the same equation, scaled
    ]
    result = check_setup(DIVIDER, equations, ["V1", "V2"])
    assert not result.ok
    assert result.kind == "underdetermined"


# --------------------------------------------------------------------------
# check_setup -- refusing to guess
# --------------------------------------------------------------------------

def test_a_malformed_netlist_is_reported_not_raised():
    result = check_setup("this is not a netlist", [sp.Eq(V1, V)], ["V1"])
    assert not result.ok
    assert result.kind == "error"
    assert result.message.strip()
    assert "netlist" in result.message.lower()


def test_an_unshorted_source_loop_is_reported_not_raised():
    """lcapy only discovers this when it tries to solve."""
    result = check_setup("Vs 1 0 {V}\nVx 1 0 {Vb}\n", [sp.Eq(V1, V)], ["V1"])
    assert not result.ok
    assert result.kind == "error"
    assert result.message.strip()


def test_an_unknown_that_names_nothing_in_the_circuit_is_an_error():
    result = check_setup(DIVIDER, [sp.Eq(V1, V)], ["V1", "V9"])
    assert not result.ok
    assert result.kind == "error"
    assert "V9" in result.message


def test_an_unknown_that_never_appears_is_named_in_the_message():
    """An unknown nothing constrains is underdetermined -- and worth naming."""
    result = check_setup(DIVIDER, [sp.Eq(V1, V)], ["V1", "V2"])
    assert not result.ok
    assert result.kind == "underdetermined"
    assert "V2" in result.message


def test_no_unknowns_is_an_error():
    result = check_setup(DIVIDER, [sp.Eq(V1, V)], [])
    assert not result.ok
    assert result.kind == "error"


def test_no_equations_is_an_error():
    result = check_setup(DIVIDER, [], ["V1", "V2"])
    assert not result.ok
    assert result.kind == "error"


def test_a_system_nonlinear_in_the_unknowns_is_refused():
    """The rank test is a linearisation, so it must not be applied blindly."""
    equations = [sp.Eq(V1, V), sp.Eq(V1 * V2, V2 * V)]
    result = check_setup(DIVIDER, equations, ["V1", "V2"])
    assert not result.ok
    assert result.kind == "error"
    assert "nonlinear" in result.message.lower()


def test_result_is_frozen():
    result = check_setup(DIVIDER, [sp.Eq(V1, V), sp.Eq((V1 - V2) / R1, V2 / R2)],
                         ["V1", "V2"])
    assert isinstance(result, SetupResult)
    with pytest.raises(Exception):
        result.ok = False
