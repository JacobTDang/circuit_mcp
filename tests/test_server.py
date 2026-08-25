"""The MCP tool surface: strings in, JSON-serialisable verdicts out.

The tools are called directly rather than over a transport. What the transport
does with a dict is mcp's problem; what this module has to get right is the
translation -- strings to expressions, expressions to a verdict, and every
failure to a named, actionable error rather than a stack trace or a silence.

Two integration properties get more coverage than anything else, because both
fail *quietly* and both produce a confident wrong verdict on correct work:

* an expression must be parsed against the ground truth's own symbols, or
  lcapy's ``s`` and a freshly parsed ``s`` compare unequal while printing
  identically (see ``SymbolConflictError``);
* a tool must be bounded by wall-clock time, because the parser bounds parse
  cost and not evaluation cost -- ``9^(9^9)`` parses instantly and then never
  finishes.
"""
import asyncio
import json
import os
import sys
import time

import pytest
import sympy as sp

from circuit_mcp import server as server_module
from circuit_mcp.analysis import transfer
from circuit_mcp.equivalence import equivalent
from circuit_mcp.server import (
    check_derivation,
    check_equivalence,
    check_setup,
    circuit_equations,
    derive,
)
from circuit_mcp.symbols import SymbolConflictError

INVERTING = """
Vs 1 0 {V}
Ri 1 2 {Ri}
Rf 2 3 {Rf}
E1 3 0 opamp 0 2 {A}
"""

DIVIDER = """
Vs 1 0 {V}
R1 1 2 {R1}
R2 2 0 {R2}
"""

RC_LOWPASS = """
Vs 1 0 s {V}
R1 1 2 {R}
C1 2 0 {C}
"""

TOOL_NAMES = {
    "derive",
    "check_equivalence",
    "check_derivation",
    "circuit_equations",
    "check_setup",
}


def exact(rendered: dict) -> sp.Expr:
    """Recover an expression the way a caller would: through ``srepr``."""
    return sp.sympify(rendered["srepr"])


# --------------------------------------------------------------------------
# registration and the serialisation contract
# --------------------------------------------------------------------------

def test_every_tool_is_registered_with_a_description():
    tools = asyncio.run(server_module.server.list_tools())
    assert {tool.name for tool in tools} == TOOL_NAMES
    for tool in tools:
        assert tool.description and tool.description.strip()


def test_every_tool_returns_something_json_serialisable():
    results = [
        derive(INVERTING, 1, 0, 3, 0, "gbw"),
        check_equivalence("Rf/Ri", "Ri/Rf"),
        check_derivation(["-Rf/Ri"], "-Ri/Rf"),
        circuit_equations(DIVIDER),
        check_setup(DIVIDER, ["V1 = V"], ["V1", "V2"]),
    ]
    for result in results:
        json.dumps(result)  # raises TypeError on a stray SymPy object


def test_rendered_expressions_carry_both_a_readable_and_an_exact_form():
    rendered = derive(RC_LOWPASS, 1, 0, 2, 0, "finite")["transfer_function"]
    assert set(rendered) == {"text", "srepr"}
    truth = transfer(RC_LOWPASS, 1, 0, 2, 0)
    assert rendered["text"] == str(truth)

    # srepr carries the assumptions, so the recovered expression holds lcapy's
    # own symbol objects. equivalent() raises on a name bound to two different
    # symbols, so getting a verdict at all is the assertion that they survived.
    recovered = exact(rendered)
    assert equivalent(recovered, truth).equivalent
    assert {sp.srepr(sym) for sym in recovered.free_symbols} == {
        sp.srepr(sym) for sym in truth.free_symbols
    }


def test_the_readable_form_alone_does_not_survive_the_round_trip():
    """Why both forms are rendered, stated as a test.

    ``str`` prints ``C`` for lcapy's ``Symbol('C', positive=True)``. Reading
    that back gives a bare ``Symbol('C')`` -- prints the same, hashes
    differently, and would have the numeric oracle assign the two independent
    values. equivalence.py refuses rather than return that verdict.
    """
    rendered = derive(RC_LOWPASS, 1, 0, 2, 0, "finite")["transfer_function"]
    truth = transfer(RC_LOWPASS, 1, 0, 2, 0)
    with pytest.raises(SymbolConflictError):
        equivalent(sp.sympify(rendered["text"]), truth)


# --------------------------------------------------------------------------
# derive
# --------------------------------------------------------------------------

def test_derive_finite_returns_the_transfer_function_as_written():
    result = derive(INVERTING, 1, 0, 3, 0, "finite")
    assert result["ok"] is True
    assert result["mode"] == "finite"
    assert exact(result["transfer_function"]) == transfer(INVERTING, 1, 0, 3, 0)
    assert result["poles"] == []  # no reactive element, so no pole in s


def test_derive_accepts_node_names_as_strings():
    """Over JSON a node arrives as "1" at least as often as 1."""
    assert derive(INVERTING, "1", "0", "3", "0", "finite") == derive(
        INVERTING, 1, 0, 3, 0, "finite"
    )


def test_derive_ideal_collapses_to_the_textbook_gain():
    result = derive(INVERTING, 1, 0, 3, 0, "ideal")
    assert result["ok"] is True
    Ri, Rf = sp.symbols("Ri Rf", positive=True)
    assert equivalent(exact(result["transfer_function"]), -Rf / Ri).equivalent


def test_derive_gbw_reports_the_gain_bandwidth_pole():
    result = derive(INVERTING, 1, 0, 3, 0, "gbw")
    assert result["ok"] is True
    A0, wp, Ri, Rf = sp.symbols("A0 wp Ri Rf", positive=True)
    assert len(result["poles"]) == 1
    expected = -wp * (A0 * Ri + Rf + Ri) / (Rf + Ri)
    assert equivalent(exact(result["poles"][0]), expected).equivalent


def test_derive_reports_the_pole_of_an_rc_lowpass():
    result = derive(RC_LOWPASS, 1, 0, 2, 0, "finite")
    assert len(result["poles"]) == 1
    truth = transfer(RC_LOWPASS, 1, 0, 2, 0)
    R, C = (lambda b: (b["R"], b["C"]))({str(x): x for x in truth.free_symbols})
    assert equivalent(exact(result["poles"][0]), -1 / (R * C)).equivalent


def test_derive_names_the_symbols_it_solved_in():
    assert derive(INVERTING, 1, 0, 3, 0, "finite")["symbols"] == ["A", "Rf", "Ri"]


def test_derive_rejects_an_unknown_mode():
    result = derive(INVERTING, 1, 0, 3, 0, "magic")
    assert result["ok"] is False
    assert result["error"] == "bad_mode"
    assert "magic" in result["message"]
    assert "finite" in result["message"]  # the message names the real options


def test_derive_refuses_to_idealise_a_circuit_with_no_gain_symbol():
    """``sp.limit`` on an absent symbol is a *silent* no-op, not an error.

    Idealising an RC lowpass would hand back the unchanged H(s) labelled
    "ideal", which is exactly the plausible-wrong-answer this tool exists to
    prevent. It has to be refused out loud.
    """
    result = derive(RC_LOWPASS, 1, 0, 2, 0, "ideal")
    assert result["ok"] is False
    assert result["error"] == "missing_gain"
    assert "transfer_function" not in result


def test_derive_refuses_finite_gbw_on_a_circuit_with_no_gain_symbol():
    result = derive(RC_LOWPASS, 1, 0, 2, 0, "gbw")
    assert result["ok"] is False
    assert result["error"] == "missing_gain"


def test_derive_on_a_malformed_netlist_reports_a_circuit_error():
    result = derive("this is not a netlist", 1, 0, 2, 0, "finite")
    assert result["ok"] is False
    assert result["error"] == "circuit_error"
    assert result["message"].strip()


# --------------------------------------------------------------------------
# check_equivalence
# --------------------------------------------------------------------------

def test_equivalent_expressions_are_proved_symbolically():
    result = check_equivalence("Rf/(Ri + Rf)", "1/(1 + Ri/Rf)")
    assert result["ok"] is True
    assert result["equivalent"] is True
    assert result["oracle"] == "symbolic"
    assert result["counterexample"] is None


def test_differing_expressions_come_back_with_a_stringified_counterexample():
    result = check_equivalence("Rf/Ri", "Ri/Rf")
    assert result["equivalent"] is False
    assert result["oracle"] == "numeric"
    counterexample = result["counterexample"]
    assert set(counterexample) == {"Rf", "Ri"}
    assert all(isinstance(k, str) and isinstance(v, str)
               for k, v in counterexample.items())
    assert result["detail"].strip()


def test_both_sides_are_parsed_onto_one_set_of_symbols():
    """Parsing each side independently is what raises SymbolConflictError.

    Nothing here supplies a ground truth to bind against, so the first side
    becomes the reference for the second. Without that the two ``s`` objects
    are equal by construction here, but the shared-symbol rule is the property
    being asserted -- one object per name across both sides.
    """
    result = check_equivalence("1/(1 + s R C)", "1/(1 + s*R*C)")
    assert result["equivalent"] is True
    a, b = exact(result["expr_a"]), exact(result["expr_b"])
    by_name = {str(sym): sym for sym in a.free_symbols}
    assert all(sym == by_name[str(sym)] for sym in b.free_symbols)


def test_an_unparseable_expression_is_a_parse_error_naming_the_side():
    result = check_equivalence("Rf/Ri", "Rf/(Ri")
    assert result["ok"] is False
    assert result["error"] == "parse_error"
    assert "expr_b" in result["message"]


def test_an_expression_reaching_for_the_interpreter_is_refused():
    result = check_equivalence("Rf.__class__", "1")
    assert result["ok"] is False
    assert result["error"] == "parse_error"


# --------------------------------------------------------------------------
# check_derivation
# --------------------------------------------------------------------------

TRUTH = "-A*Rf/(A*Ri + Rf + Ri)"


def test_a_correct_derivation_checks_out():
    result = check_derivation(
        [TRUTH, "-Rf/(Ri + (Rf + Ri)/A)"], TRUTH
    )
    assert result["ok"] is True
    assert result["kind"] == "ok"
    assert result["step_index"] is None


def test_a_dropped_term_is_located_at_the_transition_that_dropped_it():
    result = check_derivation(
        [TRUTH, "-Rf/(Ri + (Rf + Ri)/A)", "-Rf/Ri"], TRUTH
    )
    assert result["ok"] is False
    assert result["kind"] == "algebra"
    assert result["step_index"] == 1  # 0-based: the step 2 -> step 3 transition
    assert result["counterexample"]
    assert all(isinstance(v, str) for v in result["counterexample"].values())


def test_valid_algebra_from_a_wrong_start_is_reported_as_a_setup_error():
    result = check_derivation(["-Rf/Ri", "-(Rf/Ri)"], TRUTH)
    assert result["ok"] is False
    assert result["kind"] == "setup"
    assert result["step_index"] == 0


def test_a_lone_wrong_answer_asks_for_the_working():
    result = check_derivation(["-Rf/Ri"], TRUTH)
    assert result["ok"] is False
    assert result["kind"] == "final"
    assert result["step_index"] is None


def test_an_s_domain_derivation_checks_out():
    result = check_derivation(
        ["1/(1 + s R C)", "(1/(R C))/(s + 1/(R C))"], "1/(1 + s*R*C)"
    )
    assert result["ok"] is True, result["message"]


def test_no_steps_is_reported_not_silently_passed():
    result = check_derivation([], TRUTH)
    assert result["ok"] is False
    assert result["kind"] == "empty"


def test_the_derivation_is_echoed_back_for_confirmation():
    """Transcribe -> confirm -> check: the reading has to be visible."""
    result = check_derivation([TRUTH], TRUTH)
    assert [exact(step) for step in result["steps"]] == [exact(result["truth"])]


def test_derives_readable_form_is_what_feeds_back_as_the_ground_truth():
    """The actual workflow, end to end: derive -> check_derivation.

    It has to be the ``text`` rendering. ``srepr`` is exact but is not an input
    format -- parsing.py rejects its quotes and its call syntax on purpose, so
    feeding it back would fail rather than silently mean something else.
    """
    truth = derive(INVERTING, 1, 0, 3, 0, "gbw")["transfer_function"]
    checked = check_derivation([truth["text"]], truth["text"])
    assert checked["ok"] is True, checked["message"]

    refused = check_derivation([truth["srepr"]], truth["text"])
    assert refused["ok"] is False
    assert refused["error"] == "parse_error"


def test_an_unparseable_step_names_which_step_it_was():
    result = check_derivation([TRUTH, "-Rf/(Ri"], TRUTH)
    assert result["ok"] is False
    assert result["error"] == "parse_error"
    assert "step 2" in result["message"]


# --------------------------------------------------------------------------
# circuit_equations
# --------------------------------------------------------------------------

def test_circuit_equations_returns_lcapys_system_for_a_divider():
    result = circuit_equations(DIVIDER)
    assert result["ok"] is True
    assert result["equations_available"] is True
    assert result["unknowns"] == ["V1", "V2"]
    assert len(result["equations"]) == 2
    assert all(set(eq) == {"text", "srepr"} for eq in result["equations"])
    assert result["display"]
    assert result["note"] == ""


def test_circuit_equations_reports_the_solved_values():
    result = circuit_equations(DIVIDER)
    solved = exact(result["node_voltages"]["V2"])
    by_name = {str(sym): sym for sym in solved.free_symbols}
    expected = (
        by_name["R2"] * by_name["V"] / (by_name["R1"] + by_name["R2"])
    )
    assert equivalent(solved, expected).equivalent
    assert result["branch_currents"]["I_R1"]


def test_circuit_equations_surfaces_the_dependent_source_note():
    """An op-amp has no nodal system in lcapy. "No equations" is not the answer."""
    result = circuit_equations(INVERTING)
    assert result["ok"] is True
    assert result["equations"] == []
    assert result["equations_available"] is False
    assert "dependent source" in result["note"].lower()
    # The solved values are the real oracle and must still be there.
    assert result["node_voltages"]["V3"]


def test_circuit_equations_on_a_malformed_netlist_reports_a_circuit_error():
    result = circuit_equations("this is not a netlist")
    assert result["ok"] is False
    assert result["error"] == "circuit_error"
    assert result["message"].strip()


# --------------------------------------------------------------------------
# check_setup
# --------------------------------------------------------------------------

def test_a_correct_nodal_setup_passes():
    result = check_setup(
        DIVIDER, ["V1 = V", "(V1 - V2)/R1 = V2/R2"], ["V1", "V2"]
    )
    assert result["ok"] is True, result["message"]
    assert result["kind"] == "ok"
    assert result["failing_equation"] is None


def test_a_differently_formulated_but_correct_setup_passes():
    result = check_setup(
        DIVIDER, ["V2*(R1 + R2) - R2*V1 = 0", "V - V1 = 0"], ["V1", "V2"]
    )
    assert result["ok"] is True, result["message"]


def test_a_wrong_sign_is_located_by_index():
    result = check_setup(
        DIVIDER, ["V1 = V", "(V1 - V2)/R1 = -V2/R2"], ["V1", "V2"]
    )
    assert result["ok"] is False
    assert result["kind"] == "not_satisfied"
    assert result["failing_equation"] == 1


def test_a_missing_equation_is_underdetermined():
    result = check_setup(DIVIDER, ["V1 = V"], ["V1", "V2"])
    assert result["ok"] is False
    assert result["kind"] == "underdetermined"
    assert "V2" in result["message"]


def test_an_unknown_that_names_nothing_is_reported_not_raised():
    result = check_setup(DIVIDER, ["V1 = V"], ["V1", "V9"])
    assert result["ok"] is False
    assert result["kind"] == "error"
    assert "V9" in result["message"]


def test_a_malformed_netlist_is_reported_not_raised():
    result = check_setup("this is not a netlist", ["V1 = V"], ["V1"])
    assert result["ok"] is False
    assert "netlist" in result["message"].lower()


def test_an_equation_without_an_equals_sign_is_a_parse_error():
    result = check_setup(DIVIDER, ["V1 + V2"], ["V1", "V2"])
    assert result["ok"] is False
    assert result["error"] == "parse_error"
    assert "equation 1" in result["message"].lower()


def test_the_parsed_equations_are_echoed_back():
    result = check_setup(DIVIDER, ["V1 = V"], ["V1", "V2"])
    assert [eq["text"] for eq in result["equations"]] == ["Eq(V1, V)"]


# --------------------------------------------------------------------------
# the wall-clock bound
# --------------------------------------------------------------------------

def test_the_timeout_is_a_module_level_constant():
    assert isinstance(server_module.TIMEOUT_SECONDS, (int, float))
    assert server_module.TIMEOUT_SECONDS > 0


def test_a_pathological_expression_times_out_instead_of_hanging(monkeypatch):
    """``9^(9^9)`` passes every safety screen and then never finishes.

    parsing.py bounds how much text the tokenizer sees, not how much work the
    resulting expression does. Verified: this input is still burning CPU after
    eight seconds. A ThreadPoolExecutor timeout would return here while leaving
    the work running forever, so the bound has to be enforced on something the
    process can actually kill.
    """
    monkeypatch.setattr(server_module, "TIMEOUT_SECONDS", 2.0)
    start = time.monotonic()
    result = check_equivalence("9^(9^9)", "1")
    elapsed = time.monotonic() - start

    assert result["ok"] is False
    assert result["error"] == "timeout"
    assert "2.0" in result["message"]
    assert elapsed < 30, f"took {elapsed:.1f}s -- the bound did not hold"


def test_the_work_runs_in_a_process_because_a_thread_cannot_be_killed():
    """The mechanism, asserted rather than assumed.

    mcp dispatches a synchronous tool on a worker thread, so SIGALRM cannot be
    armed from a tool body at all, and a thread-pool future returns while the
    runaway keeps running. Only a process can be ended.
    """
    command = server_module._worker_command()
    assert command[0] == sys.executable
    assert command[1:] == ["-m", "circuit_mcp.server", "--worker"]
    # The worker is a fresh interpreter, so it has to be told where to import
    # from -- an installed distribution is not assumed.
    source_root = os.path.dirname(os.path.dirname(server_module.__file__))
    path = server_module._worker_environment()["PYTHONPATH"].split(os.pathsep)
    assert source_root in path


def test_a_timeout_does_not_wedge_the_next_call(monkeypatch):
    """The runaway is killed, not merely abandoned to keep a core busy."""
    monkeypatch.setattr(server_module, "TIMEOUT_SECONDS", 2.0)
    check_equivalence("9^(9^9)", "1")

    monkeypatch.setattr(server_module, "TIMEOUT_SECONDS", 20.0)
    after = check_equivalence("Rf/Ri", "Rf/Ri")
    assert after["equivalent"] is True
