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


# --------------------------------------------------------------------------
# check_setup -- classifying setup laws against restated answers
#
# The tool cannot refuse an equation for being "the answer": the answer is true
# of the circuit and it does pin the unknowns down. What it can do is *say*
# which equations hand over a solved value. That reporting has to survive the
# two setups that legitimately state one unknown in knowns -- a node tied to a
# source, and an op-amp virtual ground -- so those get tested first.
# --------------------------------------------------------------------------

LADDER = """
Vs 1 0 {V}
R1 1 2 {R1}
R2 2 3 {R2}
R3 3 0 {R3}
"""

# A node held at exactly zero by a single element. Stands in for an ideal
# op-amp virtual ground, which lcapy cannot model directly (its opamp always
# carries a finite gain), and is the same shape: V_N fixed by one element.
PINNED = """
Vs 1 0 {V}
R1 1 2 {R1}
Vb 2 0 0
"""

R3 = sp.Symbol("R3")


def roles(result):
    return tuple(role.role for role in result.equation_roles)


def test_a_source_constraint_is_a_law_not_a_handed_over_answer():
    """Danger case: `V1 = Vs` states one unknown entirely in knowns."""
    equations = [sp.Eq(V1, V), sp.Eq((V1 - V2) / R1, V2 / R2)]
    result = check_setup(DIVIDER, equations, ["V1", "V2"])
    assert result.ok, result.message
    assert roles(result) == ("law", "law")
    assert result.equation_roles[0].element == "Vs"


def test_a_virtual_ground_is_a_law_not_a_handed_over_answer():
    """Danger case: `V2 = 0` is one element's constitutive relation."""
    equations = [sp.Eq(V1, V), sp.Eq(V2, 0)]
    result = check_setup(PINNED, equations, ["V1", "V2"])
    assert result.ok, result.message
    assert roles(result) == ("law", "law")
    assert result.equation_roles[1].element == "Vb"


def test_an_equation_coupling_two_unknowns_is_a_law():
    equations = [sp.Eq(V1, V), sp.Eq((V1 - V2) / R1, V2 / R2)]
    result = check_setup(DIVIDER, equations, ["V1", "V2"])
    assert result.equation_roles[1].role == "law"
    assert result.equation_roles[1].unknown is None


def test_the_divider_answer_is_reported_as_ambiguous_not_as_a_law():
    """The headline case -- and it is genuinely undecidable, so say so."""
    equations = [sp.Eq(V1, V), sp.Eq(V2, R2 * V / (R1 + R2))]
    result = check_setup(DIVIDER, equations, ["V1", "V2"])
    assert result.ok, result.message
    assert roles(result) == ("law", "ambiguous")
    assert result.equation_roles[1].unknown == "V2"
    assert "node 2" in result.equation_roles[1].detail


def test_the_textbook_kcl_gets_the_same_verdict_as_the_divider_answer():
    """`(Vs - V2)/R1 = V2/R2` *is* the answer, scaled by R1*R2.

    Two equations differing by a nonzero factor are one equation. Any rule
    separating them would have to call this standard setup wrong, so both land
    in the same bucket -- that is the honest verdict, not a miss.
    """
    kcl = check_setup(DIVIDER, [sp.Eq(V1, V), sp.Eq((V - V2) / R1, V2 / R2)],
                      ["V1", "V2"])
    answer = check_setup(DIVIDER, [sp.Eq(V1, V), sp.Eq(V2, R2 * V / (R1 + R2))],
                         ["V1", "V2"])
    assert kcl.ok and answer.ok
    assert roles(kcl) == roles(answer) == ("law", "ambiguous")


def test_a_ladder_node_law_is_not_reported_as_solved():
    equations = [
        sp.Eq(V1, V),
        sp.Eq((V1 - V2) / R1, (V2 - V3) / R2),
        sp.Eq((V2 - V3) / R2, V3 / R3),
    ]
    result = check_setup(LADDER, equations, ["V1", "V2", "V3"])
    assert result.ok, result.message
    assert roles(result) == ("law", "law", "law")


def test_a_ladder_answer_is_reported_as_solved():
    """Here the node law and the answer really are different equations."""
    equations = [
        sp.Eq(V1, V),
        sp.Eq((V1 - V2) / R1, (V2 - V3) / R2),
        sp.Eq(V3, R3 * V / (R1 + R2 + R3)),
    ]
    result = check_setup(LADDER, equations, ["V1", "V2", "V3"])
    assert result.ok, result.message
    assert roles(result) == ("law", "law", "solved")
    assert result.equation_roles[2].unknown == "V3"


def test_a_mesh_kvl_is_never_reported_as_solved():
    """KVL round the one loop *is* the answer for the loop current."""
    result = check_setup(DIVIDER, [sp.Eq(V, I_R1 * R1 + I_R1 * R2)], ["I_R1"])
    assert result.ok, result.message
    assert roles(result) == ("ambiguous",)


def test_the_inverting_opamp_answer_is_reported_as_solved():
    equations = [
        sp.Eq(V1, V),
        sp.Eq((V1 - V2) / Ri + (V3 - V2) / Rf, 0),
        sp.Eq(V3, -A * Rf * V / (A * Ri + Rf + Ri)),
    ]
    result = check_setup(INVERTING, equations, ["V1", "V2", "V3"])
    assert result.ok, result.message
    assert roles(result) == ("law", "law", "solved")


def test_the_inverting_opamp_setup_is_all_laws():
    equations = [
        sp.Eq(V1, V),
        sp.Eq((V1 - V2) / Ri + (V3 - V2) / Rf, 0),
        sp.Eq(V3, A * (0 - V2)),
    ]
    result = check_setup(INVERTING, equations, ["V1", "V2", "V3"])
    assert result.ok, result.message
    assert roles(result) == ("law", "law", "law")


def test_an_equation_that_holds_identically_is_reported_as_trivial():
    result = check_setup(DIVIDER, [sp.Eq(V1 - V1, 0), sp.Eq(V2 - V2, 0)],
                         ["V1", "V2"])
    assert result.kind == "underdetermined"
    assert roles(result) == ("trivial", "trivial")


def test_classification_never_changes_the_verdict():
    """Reporting only. An equation that hands over the answer still passes."""
    result = check_setup(DIVIDER, [sp.Eq(V1, V), sp.Eq(V2, R2 * V / (R1 + R2))],
                         ["V1", "V2"])
    assert result.ok
    assert result.kind == "ok"
    assert result.failing_equation is None


def test_the_message_says_which_equations_hand_over_a_solved_value():
    equations = [
        sp.Eq(V1, V),
        sp.Eq((V1 - V2) / R1, (V2 - V3) / R2),
        sp.Eq(V3, R3 * V / (R1 + R2 + R3)),
    ]
    message = check_setup(LADDER, equations, ["V1", "V2", "V3"]).message
    assert "Equation 2" in message
    assert "V3" in message


def test_the_message_admits_when_law_and_answer_cannot_be_told_apart():
    equations = [sp.Eq(V1, V), sp.Eq(V2, R2 * V / (R1 + R2))]
    message = check_setup(DIVIDER, equations, ["V1", "V2"]).message
    assert "node 2" in message
    assert "cannot" in message.lower()


def test_an_all_law_setup_says_nothing_extra():
    equations = [sp.Eq(V1, V), sp.Eq((V1 - V2) / R1, V2 / R2)]
    message = check_setup(DIVIDER, equations, ["V1", "V2"]).message
    assert "hands" not in message.lower()
    assert "solved value" not in message.lower()


def test_roles_are_empty_when_the_check_never_got_that_far():
    result = check_setup("this is not a netlist", [sp.Eq(V1, V)], ["V1"])
    assert result.kind == "error"
    assert result.equation_roles == ()


def test_roles_are_empty_for_an_unsatisfied_equation():
    result = check_setup(DIVIDER, [sp.Eq(V1, V), sp.Eq((V1 - V2) / R1, -V2 / R2)],
                         ["V1", "V2"])
    assert result.kind == "not_satisfied"
    assert result.equation_roles == ()


# --------------------------------------------------------------------------
# check_setup -- the two duals of "a node tied to a source"
#
# A quantity is a *known* if the independent sources fix it without solving the
# circuit. Voltage sources do that for node potentials, and they chain: two in
# series pin the far node too. Current sources do the same for branch currents,
# through any element in series with them. Both must count, or a constitutive
# relation written with the known substituted gets reported as a solved value.
# --------------------------------------------------------------------------

CURRENT_DRIVEN = """
Is 1 0 {Iin}
R1 1 2 {R1}
R2 2 0 {R2}
"""

STACKED = """
Va 1 0 {Va}
Vb 2 1 {Vb}
R1 2 3 {R1}
R2 3 0 {R2}
"""

Iin, Va, Vb = sp.symbols("Iin Va Vb")


def test_ohms_law_with_a_source_current_substituted_is_a_law():
    """`V2 = Iin*R2` is R2's own relation: the source fixes the current."""
    equations = [sp.Eq(V2, Iin * R2), sp.Eq(V1 - V2, Iin * R1)]
    result = check_setup(CURRENT_DRIVEN, equations, ["V1", "V2"])
    assert result.ok, result.message
    assert roles(result) == ("law", "law")
    assert result.equation_roles[0].element == "R2"


def test_a_current_driven_answer_is_still_reported_as_solved():
    """The knowns extension must not blunt the check it exists to protect."""
    equations = [sp.Eq(V1, Iin * (R1 + R2)), sp.Eq(V2, Iin * R2)]
    result = check_setup(CURRENT_DRIVEN, equations, ["V1", "V2"])
    assert result.ok, result.message
    assert roles(result) == ("solved", "law")
    assert result.equation_roles[0].unknown == "V1"


def test_stacked_sources_pin_the_far_node_too():
    """Node 2 is known through Va then Vb, so KCL at node 3 has this form."""
    equations = [sp.Eq(V3, R2 * (Va + Vb) / (R1 + R2))]
    result = check_setup(STACKED, equations, ["V3"])
    assert result.ok, result.message
    assert roles(result) == ("ambiguous",)


def test_a_node_law_beyond_a_stacked_source_is_still_a_law():
    equations = [sp.Eq(V1, Va), sp.Eq(V2 - V1, Vb),
                 sp.Eq((V2 - V3) / R1, V3 / R2)]
    result = check_setup(STACKED, equations, ["V1", "V2", "V3"])
    assert result.ok, result.message
    assert roles(result) == ("law", "law", "law")
