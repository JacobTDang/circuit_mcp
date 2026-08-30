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
import signal
import sys
import threading
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
    configure_workspace,
    ocr_status,
    transcribe_image,
    transcribe_workspace,
    workspace_status,
    workspace_configuration,
    circuit_equations,
    characterize_transfer,
    derive,
    simulate_spice,
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
    "alias_frequency",
    "bjt_emitter_follower",
    "derive",
    "check_equivalence",
    "check_derivation",
    "circuit_equations",
    "check_setup",
    "workspace_status",
    "capture_workspace",
    "ipad_capture_status",
    "ipad_receiver_start",
    "ipad_receiver_stop",
    "capture_ipad_screen",
    "ocr_status",
    "visual_status",
    "visual_generate",
    "visual_list",
    "visual_get",
    "visual_preview",
    "transcribe_image",
    "transcribe_workspace",
    "configure_workspace",
    "workspace_configuration",
    "simulate_spice",
    "characterize_transfer",
    "converter_metrics",
    "dac_output",
    "spectrum_metrics",
    "quantize",
    "opamp_limits",
    "rectifier_metrics",
    "relaxation_oscillator",
    "transimpedance",
    "library_search",
    "document_get",
    "problem_get",
    "study_context",
    "attempt_history",
    "course_progress",
    "problem_create",
    "problem_update_interpretation",
    "transcription_confirm",
    "attempt_create",
    "attempt_complete",
    "problem_tag",
    "import_waveform_csv",
    "instrument_status",
    "instrument_query",
}


def exact(rendered: dict) -> sp.Expr:
    """Recover an expression the way a caller would: through ``srepr``."""
    return sp.sympify(rendered["srepr"])


def test_loop_characterization_labels_and_computes_negative_unity_feedback():
    result = characterize_transfer("100/(s*(s+10))", feedback="negative_unity")
    assert result["analysis_scope"] == "supplied_transfer"
    assert result["feedback"] == "negative_unity"
    assert result["stable"] is False
    assert result["stability_classification"] == "marginally_stable"
    assert result["closed_loop"]["stable"] is True
    assert result["closed_loop"]["stability_classification"] == "asymptotically_stable"


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
        workspace_status(),
        ocr_status(),
        simulate_spice("V1 in 0 1\nR1 in 0 1k", "op", ["v(in)"]),
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


def test_derivation_applies_explicit_numeric_parameters_to_every_step():
    result = check_derivation(
        ["1/(1 + s*R*C)", "1/(1 + s*1000*0.000001)", "1000/(s + 1000)"],
        "1/(1 + s*R*C)",
        {"R": 1000, "C": 1e-6},
    )
    assert result["ok"] is True, result["message"]
    assert result["parameters"] == {"R": 1000, "C": 1e-6}


def test_derivation_decimal_parameters_do_not_create_binary_float_phantoms():
    result = check_derivation(
        ["1/(1+s*R*C)", "1/(1+s*(427/125000))", "(125000/427)/(s+125000/427)"],
        "1/(1+s*R*C)",
        {"R": 6100, "C": 5.6e-7},
    )
    assert result["ok"] is True, result["message"]


def test_derivation_with_parameters_locates_later_coefficient_error():
    result = check_derivation(
        ["1/(1 + s*R*C)", "1/(1 + s*1000*0.000001)", "100/(s + 1000)"],
        "1/(1 + s*R*C)",
        {"R": 1000, "C": 1e-6},
    )
    assert result["ok"] is False
    assert result["kind"] == "algebra"
    assert result["step_index"] == 1


def test_derivation_rejects_unknown_parameter_name():
    result = check_derivation(["1/(1+s*R*C)"], "1/(1+s*R*C)", {"X": 2})
    assert result["ok"] is False
    assert result["error"] == "substitution_error"


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


def test_setup_equation_roles_are_exposed_and_json_serialisable():
    result = check_setup(
        DIVIDER,
        ["V1 = V", "V2 = R2*V/(R1 + R2)"],
        ["V1", "V2"],
    )

    assert result["ok"] is True
    assert [role["role"] for role in result["equation_roles"]] == [
        "law", "ambiguous"
    ]
    assert result["equation_roles"][0]["element"] == "Vs"
    second = result["equation_roles"][1]
    assert second["index"] == 1
    assert second["unknown"] == "V2"
    assert second["value"] is not None
    assert set(second["value"]) == {"text", "srepr"}
    json.dumps(result)


def test_setup_equation_roles_are_empty_when_validation_stops_early():
    result = check_setup(DIVIDER, ["V1 = V + 1"], ["V1"])
    assert result["ok"] is False
    assert result["kind"] == "not_satisfied"
    assert result["equation_roles"] == []


def test_transcribe_image_rejects_non_base64_without_starting_ocr_worker():
    before = server_module.OCR_WORKER.pid
    result = transcribe_image("not base64!")
    assert result.structured_content["ok"] is False
    assert result.structured_content["error"] == "bad_image"
    assert server_module.OCR_WORKER.pid == before


def test_workspace_transcription_returns_exact_frame_and_local_ocr_metadata(monkeypatch):
    png = b"\x89PNG\r\n\x1a\nformula"
    monkeypatch.setattr(
        server_module,
        "_guarded",
        lambda name, **kwargs: {
            "ok": True,
            "mime_type": "image/png",
            "bytes": len(png),
            "sha256": "abc",
            "selection": {"kind": "region"},
            "png": png,
        },
    )
    monkeypatch.setattr(
        server_module.OCR_WORKER,
        "call",
        lambda request: {
            "ok": True,
            "latex": r"V_o=-\frac{R_f}{R_i}",
            "device": "mps",
            "model": "unimernet_small",
            "inference_seconds": 0.7,
        },
    )
    result = transcribe_workspace(x=1, y=2, width=300, height=200)
    assert result.structured_content["ok"] is True
    assert result.structured_content["device"] == "mps"
    assert result.structured_content["capture"]["sha256"] == "abc"
    assert [block.type for block in result.content] == ["text", "image"]


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
# the wall-clock bound, and the worker that enforces it
# --------------------------------------------------------------------------
#
# The bound is enforced in a *process*, because a process is the only thing
# Python can actually end -- see the comment block in server.py. What these
# tests hold onto is that making that process persistent, which is what makes
# a call cost milliseconds instead of half a second, did not quietly cost
# correctness: every call must still see the same state a freshly started
# interpreter would.

# An entirely ordinary netlist whose *source* is named V1. lcapy registers
# component names in one process-global SymbolRegistry, so building this
# circuit publishes 'V1' to every circuit built afterwards in that process.
# That is the concrete leak a persistent worker would otherwise have: a later
# check_setup whose unknown is the node voltage V1 gets refused as circular.
NAMES_V1 = """
V1 1 0 {V1}
Ra 1 2 {Ra}
Rb 2 0 {Rb}
"""

ODDLY_NAMED = """
Vs 1 0 {V}
Rzz 1 2 {Rzz}
Rqq 2 0 {Rqq}
"""

CORRECT_SETUP = (DIVIDER, ["V1 = V", "(V1 - V2)/R1 = V2/R2"], ["V1", "V2"])


def test_the_timeout_is_a_module_level_constant():
    assert isinstance(server_module.TIMEOUT_SECONDS, (int, float))
    assert server_module.TIMEOUT_SECONDS > 0


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
    # from -- an installed distribution is not assumed. Still load-bearing:
    # iCloud re-hides this venv's .pth files, which breaks the editable
    # install without warning.
    source_root = os.path.dirname(os.path.dirname(server_module.__file__))
    path = server_module._worker_environment()["PYTHONPATH"].split(os.pathsep)
    assert source_root in path


# --------------------------------------------------------------------------
# the latency bug itself
# --------------------------------------------------------------------------

def test_the_worker_is_started_once_and_reused():
    """Interpreter start-up is paid per *server*, not per call."""
    check_equivalence("Rf/Ri", "Rf/Ri")  # warm
    first = server_module._WORKER.pid
    assert first is not None
    for _ in range(5):
        assert check_equivalence("Rf/Ri", "Rf/Ri")["equivalent"] is True
    assert server_module._WORKER.pid == first


def test_a_warm_call_does_not_pay_interpreter_startup():
    """The production latency bug, as a regression test.

    A fresh interpreter plus the lcapy import costs ~550ms, against tool
    bodies that take single-digit milliseconds. Twenty spawns would be ~11s;
    anything close to that means start-up crept back into the per-call path.
    """
    check_equivalence("Rf/Ri", "Rf/Ri")  # warm
    start = time.monotonic()
    for _ in range(20):
        check_equivalence("Rf/Ri", "Rf/Ri")
    elapsed = time.monotonic() - start
    assert elapsed < 3.0, f"20 calls took {elapsed:.1f}s -- start-up is back"


# --------------------------------------------------------------------------
# state must not leak from one call into the next
# --------------------------------------------------------------------------

def test_an_earlier_call_cannot_change_a_later_verdict():
    """The leak that makes a naive persistent worker unsafe.

    lcapy's ``Circuit.symbols`` is one process-global SymbolRegistry shared by
    every context, so building NAMES_V1 publishes 'V1' to every circuit built
    after it. mna's setup check refuses an unknown that shares a name with a
    circuit symbol, so the second call below comes back 'error: Unknown(s)
    ['V1'] share a name with a circuit symbol' -- a confident refusal of
    correct work, caused entirely by an unrelated earlier call.
    """
    before = check_setup(*CORRECT_SETUP)
    assert before["ok"] is True, before["message"]

    circuit_equations(NAMES_V1)

    after = check_setup(*CORRECT_SETUP)
    assert after == before, "an unrelated earlier call changed this verdict"


def test_a_prior_derivation_does_not_change_how_a_later_one_parses():
    """Symbol assumptions are the other half of the leak.

    Every expression here is parsed against a ground truth's own symbols, and
    the objects that ground truth is built from come out of lcapy's shared
    registry. Work in between must not reach them.
    """
    truth = derive(INVERTING, 1, 0, 3, 0, "finite")["transfer_function"]["text"]
    before = check_derivation([truth], truth)
    assert before["ok"] is True, before["message"]

    derive(RC_LOWPASS, 1, 0, 2, 0, "finite")
    circuit_equations(NAMES_V1)
    circuit_equations(ODDLY_NAMED)
    check_equivalence("A/(1 + A)", "1/(1/A + 1)")

    assert check_derivation([truth], truth) == before


def test_a_battery_of_calls_is_unaffected_by_anything_run_before_it():
    """The general property, rather than one instance of it.

    Same inputs, same outputs, regardless of history. Compared as whole
    result dicts so a drift in any field -- verdict, counterexample, rendered
    symbols -- fails this.
    """
    def battery():
        return [
            derive(INVERTING, 1, 0, 3, 0, "gbw"),
            derive(RC_LOWPASS, 1, 0, 2, 0, "finite"),
            check_equivalence("Rf/(Ri + Rf)", "1/(1 + Ri/Rf)"),
            check_derivation([TRUTH, "-Rf/(Ri + (Rf + Ri)/A)"], TRUTH),
            circuit_equations(DIVIDER),
            check_setup(*CORRECT_SETUP),
        ]

    first = battery()
    circuit_equations(NAMES_V1)
    circuit_equations(ODDLY_NAMED)
    derive(RC_LOWPASS, 1, 0, 2, 0, "finite")
    check_equivalence("s/(1 + s)", "1/(1/s + 1)")
    check_derivation(["-Rf/Ri"], TRUTH)

    assert battery() == first


# --------------------------------------------------------------------------
# the bound still bounds
# --------------------------------------------------------------------------

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


def test_a_timeout_leaves_nothing_of_the_worker_running(monkeypatch):
    """Killed, not merely abandoned to keep a core busy.

    The worker runs in its own session, so one killpg reaches the worker and
    the runaway it forked. Asserting the *group* is gone is what distinguishes
    a real kill from letting go of a pipe.
    """
    monkeypatch.setattr(server_module, "TIMEOUT_SECONDS", 2.0)
    check_equivalence("Rf/Ri", "Rf/Ri")  # warm, so there is a group to kill
    group = server_module._WORKER.pid
    assert group is not None

    assert check_equivalence("9^(9^9)", "1")["error"] == "timeout"

    with pytest.raises(ProcessLookupError):
        os.killpg(group, 0)


def test_a_timeout_does_not_wedge_the_next_call(monkeypatch):
    """The runaway is killed, not merely abandoned to keep a core busy."""
    monkeypatch.setattr(server_module, "TIMEOUT_SECONDS", 2.0)
    check_equivalence("9^(9^9)", "1")

    monkeypatch.setattr(server_module, "TIMEOUT_SECONDS", 20.0)
    after = check_equivalence("Rf/Ri", "Rf/Ri")
    assert after["equivalent"] is True


def test_state_is_still_clean_after_a_timeout_forced_a_restart(monkeypatch):
    """A restart is a state change too, so the invariant is re-checked here."""
    before = check_setup(*CORRECT_SETUP)
    assert before["ok"] is True, before["message"]

    monkeypatch.setattr(server_module, "TIMEOUT_SECONDS", 2.0)
    assert check_equivalence("9^(9^9)", "1")["error"] == "timeout"
    monkeypatch.setattr(server_module, "TIMEOUT_SECONDS", 20.0)

    assert check_setup(*CORRECT_SETUP) == before


# --------------------------------------------------------------------------
# worker lifecycle
# --------------------------------------------------------------------------

def test_a_worker_that_died_on_its_own_is_replaced():
    """Nothing runs tool code in the worker itself, so its death is external.

    That is what makes replacing it and retrying honest rather than a retry
    loop around a crashing input.
    """
    check_equivalence("Rf/Ri", "Rf/Ri")  # warm
    first = server_module._WORKER.pid
    os.kill(first, signal.SIGKILL)

    after = check_equivalence("Rf/Ri", "Rf/Ri")
    assert after["equivalent"] is True
    assert server_module._WORKER.pid not in (None, first)


def test_the_worker_survives_a_call_that_dies_mid_flight():
    """A tool body runs in a forked child, so it cannot take the worker down.

    Killing that child is also the 'worker dies mid-read' case: the parent is
    blocked reading a pipe whose writer just vanished. It has to come back
    with a named error rather than block forever.
    """
    check_equivalence("Rf/Ri", "Rf/Ri")  # warm
    worker = server_module._WORKER.pid
    start = time.monotonic()
    result = server_module._WORKER.call(
        "_crash_child_for_test", {}, server_module.TIMEOUT_SECONDS
    )
    elapsed = time.monotonic() - start

    assert result["ok"] is False
    assert result["error"] == "internal_error"
    assert "exited with code 7" in result["message"]
    assert elapsed < 25, f"took {elapsed:.1f}s -- it blocked on a dead writer"
    # The worker itself is untouched, and still serving.
    assert server_module._WORKER.pid == worker
    assert check_equivalence("Rf/Ri", "Rf/Ri")["equivalent"] is True


def test_scoped_storage_implementations_persist_problem_attempt_and_progress(tmp_path, monkeypatch):
    data = tmp_path / "command_center"
    monkeypatch.setattr(server_module, "default_data_dir", lambda: data)
    database = server_module._storage()
    database.add_document({
        "id": "d" * 32, "name": "rc-note.md", "category": "lecture",
        "extension": ".md", "media_type": "text/markdown", "size": 2,
        "sha256": "0" * 64, "relative_path": "files/internal.md",
        "source": "upload", "created": time.time(), "pages": None,
    }, "RC reference")
    searched = server_module._dispatch("library_search", {"query": "RC", "category": "", "limit": 10})
    assert searched["items"][0]["id"] == "d" * 32
    assert "relative_path" not in searched["items"][0]
    assert "relative_path" not in server_module._dispatch("document_get", {"document_id": "d" * 32})["document"]
    created = server_module._dispatch("problem_create", {
        "title": "RC pole", "topic": "filters", "prompt": "Find the pole",
        "document_id": None, "circuit_interpretation": "", "status": "draft",
        "source_page": None,
    })
    assert created["ok"] is True
    problem_id = created["problem"]["id"]
    updated = server_module._dispatch("problem_update_interpretation", {
        "problem_id": problem_id, "circuit_interpretation": "series R, shunt C",
        "status": "confirmed",
    })
    assert updated["problem"]["status"] == "confirmed"
    tagged = server_module._dispatch("problem_tag", {"problem_id": problem_id, "tag": "exam-1"})
    assert tagged["problem"]["tags"] == ["exam-1"]
    attempt = server_module._dispatch("attempt_create", {
        "problem_id": problem_id, "actor": "student", "answer": "", "status": "working",
    })["attempt"]
    completed = server_module._dispatch("attempt_complete", {
        "attempt_id": attempt["id"], "answer": "-1/RC", "status": "correct",
        "first_divergence": None,
    })
    assert completed["attempt"]["status"] == "correct"
    assert server_module._dispatch("course_progress", {})["attempts"] == {"correct": 1}
    assert server_module._dispatch("attempt_history", {"problem_id": problem_id, "limit": 10})["items"][0]["id"] == attempt["id"]
    assert server_module._dispatch("study_context", {"query": "RC", "limit": 10})["problems"][0]["id"] == problem_id


def test_scoped_storage_errors_are_structured_and_no_sql_surface_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "default_data_dir", lambda: tmp_path / "data")
    missing = server_module._dispatch("problem_get", {"problem_id": "not-real"})
    assert missing["ok"] is False
    assert missing["error"] == "storage_error"
    assert "sql" not in server_module._IMPLEMENTATIONS


def test_concurrent_calls_each_get_their_own_answer():
    """mcp runs synchronous tools on a thread pool, so calls really do overlap.

    One worker behind one pair of pipes means a frame from call A could be
    handed to call B. Each thread asks a question only it would recognise.
    """
    outcomes: dict[int, dict] = {}

    def ask(index: int) -> None:
        outcomes[index] = check_equivalence(f"R{index}*Ri", f"Ri*R{index}")

    threads = [threading.Thread(target=ask, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(outcomes) == 8
    for index, result in outcomes.items():
        assert result["ok"] is True
        assert result["equivalent"] is True
        assert f"R{index}" in result["expr_a"]["text"]


def test_prewarming_moves_startup_off_the_first_call():
    """``main`` pays the worker's start-up before serving, not inside a call.

    Without it the first question of a session -- the one a person is waiting
    on -- is the one that pays half a second of interpreter and lcapy import.
    """
    server_module._WORKER.shutdown()
    assert server_module._WORKER.pid is None

    start = time.monotonic()
    server_module._WORKER.prewarm()
    startup = time.monotonic() - start
    assert server_module._WORKER.pid is not None

    start = time.monotonic()
    assert check_equivalence("Rf/Ri", "Rf/Ri")["equivalent"] is True
    first_call = time.monotonic() - start

    assert first_call < startup, (
        f"first call took {first_call*1000:.0f}ms against {startup*1000:.0f}ms "
        f"of start-up -- it is still paying for the import"
    )


def test_a_worker_that_cannot_start_is_reported_rather_than_retried_forever():
    """The replacement is attempted once, then the failure is named.

    A worker that dies instantly is the shape of a broken import path -- which
    this venv produces on its own whenever iCloud re-hides its .pth files.
    """
    server_module._WORKER.shutdown()
    original = server_module._worker_command
    server_module._worker_command = lambda: [sys.executable, "-c", "raise SystemExit(3)"]
    try:
        start = time.monotonic()
        result = check_equivalence("Rf/Ri", "Rf/Ri")
        elapsed = time.monotonic() - start
    finally:
        server_module._worker_command = original
        server_module._WORKER.shutdown()

    assert result["ok"] is False
    assert result["error"] == "internal_error"
    assert elapsed < 10, f"took {elapsed:.1f}s -- it kept retrying"
    # And a real worker still starts afterwards.
    assert check_equivalence("Rf/Ri", "Rf/Ri")["equivalent"] is True
