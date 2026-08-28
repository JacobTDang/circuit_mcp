"""The MCP tool surface: strings in, JSON-serialisable verdicts out.

The model does the transcription; this server does the mathematics, and it does
none of it itself -- every number and every verdict comes from lcapy or SymPy.
So the job here is translation, and the two places translation goes wrong are
both silent:

**Symbols.** lcapy's ``s`` carries ``complex=True, finite=True`` and its
component symbols carry ``positive=True``. SymPy hashes a symbol on its name
*and* its assumptions, so a freshly parsed ``s`` is a different object that
prints identically and never compares equal. Feeding both to ``equivalent()``
raises :class:`~circuit_mcp.symbols.SymbolConflictError` -- which is the safety
net, not the plan. The plan is that everything is parsed against the ground
truth's own symbols: ``parse_expression(text, symbols=bind(truth))``. Where
there is no natural ground truth, as in :func:`check_equivalence`, the first
expression parsed becomes the reference for the second so that one object
serves each name.

**Time.** ``parsing`` bounds how much *text* the tokenizer sees; it cannot bound
how much *work* the resulting expression does. ``9^(9^9)`` clears every safety
screen, parses instantly, and then never finishes -- measured still running
after eight seconds. Every tool therefore runs under a wall-clock limit.

Two keys carry the outcome, and they mean different things:

* ``ok`` is on every result. ``False`` plus an ``error`` key means the tool
  could not run -- bad netlist, bad expression, timeout. ``False`` plus a
  ``kind`` key means it ran and the answer is wrong, which is the useful case.
* Expressions are rendered twice: ``text`` for a human and ``srepr`` for exact
  recovery. ``srepr`` carries the assumptions, so it is the only form that
  reconstructs the same symbol objects in a caller that speaks SymPy directly.
  It is *not* an input format -- ``parsing`` rejects quotes and unknown call
  syntax, deliberately, so what goes back into a tool is the ``text`` form.
  That is safe because everything inside one call is parsed against one symbol
  table, so the bare symbols on both sides are the same objects.
"""
from __future__ import annotations

import atexit
import base64
import json
import os
import pickle
import selectors
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from typing import Any, NoReturn

import sympy as sp
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent

from .analysis import (
    AssumptionError,
    ideal_limit,
    poles,
    transfer,
    with_finite_gbw,
)
from .animation_engine import build_template, template_names, validate_scene
from .capture import CaptureError, capture_status as _capture_status
from .capture import capture_workspace as _capture_workspace
from .course_metrics import (
    MetricsError,
    alias_frequency as _alias_frequency,
    bjt_emitter_follower as _bjt_emitter_follower,
    converter_metrics as _converter_metrics,
    dac_output as _dac_output,
    opamp_limits as _opamp_limits,
    quantize as _quantize,
    rectifier_metrics as _rectifier_metrics,
    relaxation_oscillator as _relaxation_oscillator,
    spectrum_metrics as _spectrum_metrics,
    transimpedance as _transimpedance,
    transfer_metrics as _transfer_metrics,
)
from .equivalence import equivalent
from .instruments import InstrumentError
from .instruments import instrument_query as _instrument_query
from .instruments import instrument_status as _instrument_status
from .ipad_capture import IPAD_CAPTURE, IPadCaptureError
from .lab import LabDataError, import_waveform_csv as _import_waveform_csv
from .mna import CircuitError, SetupInputError
from .mna import check_setup as _mna_check_setup
from .mna import circuit_equations as _mna_circuit_equations
from .ocr_client import OCR_WORKER
from .parsing import ParseError, parse_equation, parse_expression
from .steps import check_steps
from .spice import SpiceError, simulate_spice as _simulate_spice
from .symbols import SubstitutionError, SymbolConflictError, bind
from .storage import CommandCenterDB, StorageError, default_data_dir
from .workspace import configure_workspace as _configure_workspace
from .workspace import read_workspace as _read_workspace

server = MCPServer("circuit_mcp")

# Wall-clock budget for one tool call, in seconds. Generous enough for a
# second-order rational function through ``simplify``, short enough that a
# pathological input is a bounded annoyance rather than a wedged server.
TIMEOUT_SECONDS = 20.0

# The open-loop gain symbol, as the course and ``analysis`` both spell it. A
# netlist that names its gain something else is rejected by name rather than
# guessed at -- see ``_derive``.
_GAIN = "A"

_MODES = ("finite", "ideal", "gbw")

# lcapy's failure modes when a netlist is unbuildable or unsolvable. Mirrors
# ``mna._LCAPY_ERRORS``: ``OSError`` is in the list because lcapy reads a
# single-line string as a *filename*, so a garbled one-line netlist surfaces as
# FileNotFoundError rather than anything circuit-shaped.
_LCAPY_ERRORS = (ValueError, KeyError, RuntimeError, OSError)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _rendered(expr: Any) -> dict[str, str]:
    """An expression in both forms: readable, and exactly recoverable.

    ``str`` is what a person reads, and it is lossy in the one way that matters
    here -- it prints lcapy's ``Symbol('C', positive=True)`` as ``C``, so
    reading it back yields a bare ``Symbol('C')`` that prints identically and
    compares unequal. ``srepr`` spells the assumptions out, so ``sympify`` of it
    recovers the same symbol objects.

    Note that recovery is exact in *value*, not necessarily in *tree*: SymPy
    re-evaluates as it rebuilds, so a non-canonical expression out of lcapy can
    come back reassociated. Compare with ``equivalent()``, not with ``==``.
    """
    expr = sp.sympify(expr)
    return {"text": str(expr), "srepr": sp.srepr(expr)}


def _rendered_values(values: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {name: _rendered(value) for name, value in values.items()}


def _rendered_roles(roles) -> list[dict[str, Any]]:
    """Equation classifications with every SymPy value made JSON-safe."""
    return [
        {
            "index": role.index,
            "role": role.role,
            "unknown": role.unknown,
            "value": None if role.value is None else _rendered(role.value),
            "element": role.element,
            "detail": role.detail,
        }
        for role in roles
    ]


def _rendered_matrix(matrix: dict | None) -> dict | None:
    """lcapy's ``A x = b`` as nested lists, or ``None`` when it has no system."""
    if matrix is None:
        return None
    a, b = matrix["A"], matrix["b"]
    return {
        "A": [
            [_rendered(a[row, col]) for col in range(a.cols)]
            for row in range(a.rows)
        ],
        "b": [_rendered(entry) for entry in b],
    }


def _stringified(counterexample: dict | None) -> dict[str, str] | None:
    """A counterexample's keys are SymPy symbols; JSON needs strings."""
    if not counterexample:
        return None
    return {str(symbol): str(value) for symbol, value in counterexample.items()}


def _failure(kind: str, message: str) -> dict[str, Any]:
    """A tool that could not run. ``error`` names *which* way it could not."""
    return {"ok": False, "error": kind, "message": message}


# ---------------------------------------------------------------------------
# parsing, always against a reference
# ---------------------------------------------------------------------------

def _expression(
    text: str, where: str, symbols: dict[str, sp.Symbol] | None = None
) -> sp.Expr:
    """Parse one expression, saying which input failed if it does."""
    try:
        return parse_expression(text, symbols)
    except ParseError as exc:
        raise ParseError(f"Could not read {where}: {exc}") from exc


def _equation(
    text: str, where: str, symbols: dict[str, sp.Symbol] | None = None
) -> sp.Eq:
    try:
        return parse_equation(text, symbols)
    except ParseError as exc:
        raise ParseError(f"Could not read {where}: {exc}") from exc


# ---------------------------------------------------------------------------
# implementations
# ---------------------------------------------------------------------------

def _transfer(netlist: str, in_pos, in_neg, out_pos, out_neg) -> sp.Expr:
    """``analysis.transfer``, with lcapy's failures named as circuit errors."""
    try:
        return transfer(netlist, in_pos, in_neg, out_pos, out_neg)
    except _LCAPY_ERRORS as exc:
        raise CircuitError(
            f"lcapy could not derive a transfer function from this netlist: {exc}"
        ) from exc


def _derive(netlist: str, in_pos, in_neg, out_pos, out_neg, mode: str) -> dict[str, Any]:
    if mode not in _MODES:
        return _failure(
            "bad_mode",
            f"Unknown mode {mode!r}. Use 'finite' for the circuit as written, "
            f"'ideal' to take the open-loop gain to infinity, or 'gbw' for a "
            f"single-pole finite gain-bandwidth.",
        )

    result = _transfer(netlist, in_pos, in_neg, out_pos, out_neg)

    if mode != "finite":
        # ``sp.limit`` against a symbol the expression does not contain returns
        # the expression unchanged and raises nothing, so an 'ideal' answer for
        # a circuit with no op-amp would be the original H(s) wearing the wrong
        # label. Refuse instead: a plausible wrong answer is the one output this
        # tool must never produce.
        if _GAIN not in bind(result):
            return _failure(
                "missing_gain",
                f"No symbol named {_GAIN!r} in this transfer function "
                f"({sorted(bind(result))}), so there is no open-loop gain to "
                f"take a limit in. Mode {mode!r} applies to a circuit whose "
                f"op-amp gain is written {_GAIN!r}; use mode 'finite' otherwise.",
            )
        result = (
            ideal_limit(result, _GAIN)
            if mode == "ideal"
            else with_finite_gbw(result, _GAIN)
        )

    return {
        "ok": True,
        "mode": mode,
        "transfer_function": _rendered(result),
        "poles": [_rendered(pole) for pole in poles(result)],
        "symbols": sorted(bind(result)),
    }


def _check_equivalence(expr_a: str, expr_b: str) -> dict[str, Any]:
    # No ground truth here, so the first expression *is* the reference: parsing
    # each side independently is exactly what makes two identical-looking
    # symbols compare unequal.
    a = _expression(expr_a, "expr_a")
    b = _expression(expr_b, "expr_b", symbols=bind(a))

    verdict = equivalent(a, b)
    return {
        "ok": True,
        "equivalent": verdict.equivalent,
        "oracle": verdict.oracle,
        "counterexample": _stringified(verdict.counterexample),
        "detail": verdict.detail,
        "expr_a": _rendered(a),
        "expr_b": _rendered(b),
    }


def _check_derivation(
    steps: list[str], truth: str, parameters: dict[str, float] | None = None
) -> dict[str, Any]:
    truth_expr = _expression(truth, "the ground truth")

    # The truth's symbols seed the table; each step adds whatever it introduces,
    # so step k+1 binds onto the objects step k already used.
    known = dict(bind(truth_expr))
    parsed: list[sp.Expr] = []
    for index, text in enumerate(steps, start=1):
        expr = _expression(text, f"step {index}", symbols=dict(known))
        known.update(bind(expr))
        parsed.append(expr)

    substitutions: dict[sp.Symbol, float] = {}
    for name, value in (parameters or {}).items():
        if name not in known:
            raise SubstitutionError(f"parameter {name!r} is not present in the derivation")
        if not isinstance(value, (int, float)) or not sp.Float(value).is_finite:
            raise SubstitutionError(f"parameter {name!r} must be a finite number")
        # JSON numbers arrive as binary floats. Treat their shortest decimal
        # spelling as the student's intended exact value; otherwise 0.000001
        # becomes a nearby Float and exact algebra reports phantom errors.
        substitutions[known[name]] = sp.Rational(str(value))
    checked_steps = [step.subs(substitutions) for step in parsed]
    checked_truth = truth_expr.subs(substitutions)
    result = check_steps(checked_steps, checked_truth)
    return {
        "ok": result.ok,
        "kind": result.kind,
        "message": result.message,
        "step_index": result.step_index,
        "counterexample": _stringified(result.counterexample),
        "steps": [_rendered(step) for step in parsed],
        "truth": _rendered(truth_expr),
        "parameters": dict(parameters or {}),
        "evaluated_steps": [_rendered(step) for step in checked_steps],
        "evaluated_truth": _rendered(checked_truth),
    }


def _circuit_equations(netlist: str) -> dict[str, Any]:
    system = _mna_circuit_equations(netlist)
    equations = system["equations"]
    return {
        "ok": True,
        "domain": system["domain"],
        "unknowns": system["unknowns"],
        # Empty only when lcapy refuses to form the system at all -- it will not
        # touch a circuit containing a dependent source, which is every op-amp.
        # ``note`` says so; reading this as "the circuit has no equations" is
        # the misreading it exists to prevent.
        "equations_available": bool(equations),
        "equations": [_rendered(equation) for equation in equations],
        "display": list(system["display"]),
        "matrix": _rendered_matrix(system["matrix"]),
        "node_voltages": _rendered_values(system["node_voltages"]),
        "branch_currents": _rendered_values(system["branch_currents"]),
        "note": system["note"],
    }


def _check_setup(
    netlist: str, equations: list[str], unknowns: list[str]
) -> dict[str, Any]:
    known: dict[str, sp.Symbol] = {}
    parsed: list[sp.Eq] = []
    for index, text in enumerate(equations, start=1):
        equation = _equation(text, f"equation {index}", symbols=dict(known))
        known.update(bind(equation))
        parsed.append(equation)

    result = _mna_check_setup(netlist, parsed, list(unknowns))
    return {
        "ok": result.ok,
        "kind": result.kind,
        "message": result.message,
        "failing_equation": result.failing_equation,
        "counterexample": _stringified(result.counterexample),
        "equations": [_rendered(equation) for equation in parsed],
        "unknowns": list(unknowns),
        "equation_roles": _rendered_roles(result.equation_roles),
    }


def _spice(netlist: str, analysis: str, outputs: list[str]) -> dict[str, Any]:
    return _simulate_spice(netlist, analysis, outputs)


def _characterize(
    expr: str, parameters: dict[str, float], feedback: str = "none"
) -> dict[str, Any]:
    parsed = _expression(expr, "transfer function")
    if feedback not in {"none", "negative_unity"}:
        raise MetricsError("feedback must be 'none' or 'negative_unity'")
    result = _transfer_metrics(parsed, parameters)
    result["analysis_scope"] = "supplied_transfer"
    result["feedback"] = feedback
    if feedback == "negative_unity":
        result["closed_loop"] = _transfer_metrics(parsed / (1 + parsed), parameters)
    return result


def _storage() -> CommandCenterDB:
    data = default_data_dir()
    database = CommandCenterDB(data / "circuit_mcp.sqlite3")
    database.prepare(data / "library.json", data / "history.jsonl")
    return database


def _safe_document(document: dict[str, Any]) -> dict[str, Any]:
    result = dict(document)
    result.pop("relative_path", None)
    return result


def _library_search(query: str, category: str, limit: int) -> dict[str, Any]:
    return {"ok": True, "items": [_safe_document(item) for item in _storage().list_documents(query, category, limit)]}


def _document_get(document_id: str) -> dict[str, Any]:
    return {"ok": True, "document": _safe_document(_storage().get_document(document_id))}


def _problem_get(problem_id: str) -> dict[str, Any]:
    return {"ok": True, "problem": _storage().get_problem(problem_id)}


def _study_context(query: str, limit: int) -> dict[str, Any]:
    result = _storage().study_context(query, limit)
    result["documents"] = [_safe_document(item) for item in result["documents"]]
    return result


def _attempt_history(problem_id: str, limit: int) -> dict[str, Any]:
    return {"ok": True, "items": _storage().attempt_history(problem_id, limit)}


def _course_progress() -> dict[str, Any]:
    return _storage().course_progress()


def _problem_create(title: str, topic: str, prompt: str, document_id: str | None,
                    circuit_interpretation: str, status: str, source_page: int | None) -> dict[str, Any]:
    database = _storage()
    problem = database.create_problem(title, topic, prompt, document_id, circuit_interpretation, status, source_page)
    database.record_event("problem_create", problem["title"], True, "problem", problem["id"], {"actor": "mcp"})
    return {"ok": True, "problem": problem}


def _problem_update_interpretation(problem_id: str, circuit_interpretation: str,
                                   status: str) -> dict[str, Any]:
    database = _storage(); problem = database.update_problem(problem_id, circuit_interpretation, status)
    database.record_event("problem_update", problem["title"], True, "problem", problem_id, {"actor": "mcp"})
    return {"ok": True, "problem": problem}


def _transcription_confirm(transcription_id: str, corrected_content: str | None) -> dict[str, Any]:
    database = _storage(); transcription = database.confirm_transcription(transcription_id, corrected_content)
    database.record_event("transcription_confirm", "transcription", True, "transcription", transcription["id"], {"actor": "mcp"})
    return {"ok": True, "transcription": transcription}


def _attempt_create(problem_id: str, actor: str, answer: str, status: str) -> dict[str, Any]:
    database = _storage(); attempt = database.create_attempt(problem_id, actor, answer, status)
    database.record_event("attempt_create", actor, True, "attempt", attempt["id"], {"actor": "mcp"})
    return {"ok": True, "attempt": attempt}


def _attempt_complete(attempt_id: str, answer: str, status: str,
                      first_divergence: str | None) -> dict[str, Any]:
    database = _storage(); attempt = database.complete_attempt(attempt_id, answer, status, first_divergence)
    database.record_event("attempt_complete", status, True, "attempt", attempt_id, {"actor": "mcp"})
    return {"ok": True, "attempt": attempt}


def _problem_tag(problem_id: str, tag: str) -> dict[str, Any]:
    database = _storage(); problem = database.tag_problem(problem_id, tag)
    database.record_event("problem_tag", tag, True, "problem", problem_id, {"actor": "mcp"})
    return {"ok": True, "problem": problem}


def _animation_create(scene: dict[str, Any], problem_id: str | None) -> dict[str, Any]:
    database = _storage(); item = database.create_animation(validate_scene(scene), problem_id)
    database.record_event("animation_create", item["title"], True, "animation", item["id"], {"actor": "mcp"})
    return {"ok": True, "animation": item, "board_action": "spawn"}


def _animation_list(updated_after: float, limit: int) -> dict[str, Any]:
    return {"ok": True, "items": _storage().list_animations(updated_after, limit)}


def _animation_update(animation_id: str, scene: dict[str, Any]) -> dict[str, Any]:
    item = _storage().update_animation(animation_id, validate_scene(scene))
    return {"ok": True, "animation": item, "board_action": "refresh"}


def _animation_delete(animation_id: str) -> dict[str, Any]:
    _storage().delete_animation(animation_id); return {"ok": True, "animation_id": animation_id, "board_action": "remove"}


def _animation_from_template(template: str, title: str | None, problem_id: str | None) -> dict[str, Any]:
    item = _storage().create_animation(build_template(template, title), problem_id)
    return {"ok": True, "animation": item, "board_action": "spawn"}


_IMPLEMENTATIONS = {
    "derive": _derive,
    "check_equivalence": _check_equivalence,
    "check_derivation": _check_derivation,
    "circuit_equations": _circuit_equations,
    "check_setup": _check_setup,
    "simulate_spice": _spice,
    "characterize_transfer": _characterize,
    "converter_metrics": _converter_metrics,
    "quantize": _quantize,
    "opamp_limits": _opamp_limits,
    "rectifier_metrics": _rectifier_metrics,
    "bjt_emitter_follower": _bjt_emitter_follower,
    "relaxation_oscillator": _relaxation_oscillator,
    "dac_output": _dac_output,
    "alias_frequency": _alias_frequency,
    "transimpedance": _transimpedance,
    "library_search": _library_search,
    "document_get": _document_get,
    "problem_get": _problem_get,
    "study_context": _study_context,
    "attempt_history": _attempt_history,
    "course_progress": _course_progress,
    "problem_create": _problem_create,
    "problem_update_interpretation": _problem_update_interpretation,
    "transcription_confirm": _transcription_confirm,
    "attempt_create": _attempt_create,
    "attempt_complete": _attempt_complete,
    "problem_tag": _problem_tag,
    "animation_create": _animation_create,
    "animation_list": _animation_list,
    "animation_update": _animation_update,
    "animation_delete": _animation_delete,
    "animation_from_template": _animation_from_template,
    "import_waveform_csv": _import_waveform_csv,
    "instrument_status": _instrument_status,
    "instrument_query": _instrument_query,
    "spectrum_metrics": _spectrum_metrics,
    "workspace_status": _capture_status,
    "capture_workspace": _capture_workspace,
}


def _crash_child_for_test() -> NoReturn:
    """Terminate a call child without a response, for lifecycle tests only.

    This implementation is deliberately absent from the MCP tool registry. It
    gives the worker harness a deterministic way to exercise a child that dies
    between receiving a request and writing its framed response; discovering
    and killing a short-lived process by PID made that test timing-dependent.
    """
    os._exit(7)


_IMPLEMENTATIONS["_crash_child_for_test"] = _crash_child_for_test


# ---------------------------------------------------------------------------
# failures
# ---------------------------------------------------------------------------

# Most specific first: every one of these is a ValueError subclass, so order is
# what keeps a bad netlist distinguishable from a bad expression.
_ERROR_KINDS: tuple[tuple[type[BaseException], str], ...] = (
    (ParseError, "parse_error"),
    (SymbolConflictError, "symbol_conflict"),
    (SubstitutionError, "substitution_error"),
    (AssumptionError, "assumption_error"),
    (CircuitError, "circuit_error"),
    (SetupInputError, "setup_input_error"),
    (CaptureError, "capture_error"),
    (SpiceError, "spice_error"),
    (MetricsError, "metrics_error"),
    (LabDataError, "lab_data_error"),
    (InstrumentError, "instrument_error"),
    (StorageError, "storage_error"),
)


def _translate(exc: BaseException) -> dict[str, Any]:
    """One exception -> one named error kind, never a stack trace."""
    for cls, kind in _ERROR_KINDS:
        if isinstance(exc, cls):
            return _failure(kind, str(exc))
    # Unrecognised, but still named and still structured. Loud, not swallowed.
    return _failure("internal_error", f"{type(exc).__name__}: {exc}")


def _dispatch(name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    implementation = _IMPLEMENTATIONS.get(name)
    if implementation is None:  # the name crossed a process boundary to get here
        return _failure(
            "internal_error",
            f"No implementation registered for {name!r}. "
            f"Known: {sorted(_IMPLEMENTATIONS)}.",
        )
    try:
        return implementation(**kwargs)
    except Exception as exc:  # translated to a named kind, never re-raised raw
        return _translate(exc)


# ---------------------------------------------------------------------------
# the wall-clock bound
# ---------------------------------------------------------------------------
#
# A process, not ``signal.SIGALRM``, and not a thread pool.
#
# * A ``ThreadPoolExecutor`` timeout is not a bound at all. Python cannot kill a
#   thread, so the future returns while the runaway keeps a core busy for the
#   life of the process. It looks like it works, which is worse than failing.
# * ``SIGALRM`` can only be armed from the main thread, and mcp 2.1.0 dispatches
#   a *synchronous* tool through ``anyio.to_thread.run_sync``
#   (``mcp/server/mcpserver/utilities/func_metadata.py``). A tool body therefore
#   never runs on the main thread, and ``signal.signal`` raises ``ValueError:
#   signal only works in main thread of the main interpreter`` there. It would
#   pass a direct unit test and fail on the first real request.
# * A process can be killed. ``SIGKILL`` ends the computation and reclaims the
#   memory it was accumulating, which is the only version of this that is
#   actually true.
#
# What that used to cost was one interpreter start-up *per call*: ~550ms of
# Python and lcapy import against tool bodies that measure 0.1-11ms. Nearly all
# of every call was start-up, on an interactive path.
#
# So the worker is started once and kept. It does not, however, run tool code
# itself -- it forks a child per call and the child does the work:
#
#         server ──pipe──> worker ──fork──> child (runs one call, exits)
#
# The fork is what makes reuse *safe*, and safety is the whole reason this
# shape was chosen over simply looping in the worker:
#
# * lcapy keeps one process-global ``SymbolRegistry`` -- ``state.symbols``,
#   which ``new_context()`` deliberately shares with every context -- so every
#   circuit built in a process publishes its component names to every circuit
#   built afterwards. In a worker that looped, a netlist naming its source
#   ``V1`` would make a later, unrelated ``check_setup`` whose unknown is the
#   node voltage ``V1`` come back "Unknown(s) ['V1'] share a name with a
#   circuit symbol". Measured, not theorised: a correct setup that passed
#   before the intervening call was refused after it.
# * SymPy caches expression construction on argument *equality*, and a symbol's
#   equality includes its assumptions, so a cached ``Mul`` can only be handed
#   back for a request whose symbols are equal -- assumptions and all -- to the
#   ones it was built from. That cache is therefore not a source of wrong
#   answers on its own. It is not the part that needed fixing; the registry is.
#
# Clearing caches between calls would mean enumerating every piece of global
# state in SymPy and lcapy correctly, and staying right about it as both
# libraries change. A fork does not need the enumeration: the child gets a
# copy-on-write snapshot and everything it touches dies with it. A call
# therefore begins in exactly the state a freshly started interpreter would be
# in -- which is the property the old spawn-per-call design had, kept, at
# ~2.7ms instead of ~550ms.
#
# ``subprocess`` rather than ``multiprocessing``: a ``spawn`` child re-executes
# the parent's ``__main__`` before it unpickles anything, so the worker's
# behaviour would depend on how the server happened to be launched. Verified
# failing -- launched from a heredoc, the child dies with ``FileNotFoundError:
# .../<stdin>`` and the tool reports an internal error. Naming the module to run
# removes the question. The child is told where this package lives explicitly,
# for the same reason.
#
# Forking is safe *here* specifically because the worker is single-threaded and
# imports nothing that starts threads or touches CoreFoundation -- checked:
# one thread after importing the complete server, including python-control and
# matplotlib. Forking from the server process itself would not be safe, because
# mcp runs tool bodies on an anyio thread pool. The worker remains
# single-threaded after all imports; this must be rechecked when dependencies
# change rather than inferred from their names.

_WORKER_FLAG = "--worker"

# Not ``__name__``: in the worker that is ``"__main__"``. ``__spec__.name`` is
# the importable path under both.
_WORKER_MODULE = __spec__.name if __spec__ is not None else __name__

# Enough stderr to recognise a crash, not so much that a traceback becomes the
# tool's answer.
_STDERR_TAIL = 800

# Every message is a length then that many bytes. Pipes deliver in arbitrary
# chunks, so a frame is the only way to know a message is complete rather than
# merely paused. An empty frame is a ping, answered by an empty frame: it
# confirms the worker is up and has finished importing without inventing a tool
# call to ask it.
_HEADER = struct.Struct("!Q")

# How long to wait for a worker asked politely to leave before insisting.
_SHUTDOWN_GRACE = 2.0


def _worker_command() -> list[str]:
    return [sys.executable, "-m", _WORKER_MODULE, _WORKER_FLAG]


def _worker_environment() -> dict[str, str]:
    """The parent's import path, handed to the child explicitly.

    The worker is a fresh interpreter and has to be told where this package
    lives. Deriving that from the running ``sys.path`` rather than trusting an
    installed distribution means the worker also works from a source checkout,
    and from under pytest's ``pythonpath`` setting.

    Still load-bearing, and not the historical workaround it looks like: this
    project lives under an iCloud-synced ``~/Desktop``, iCloud sets
    ``UF_HIDDEN`` on dot-prefixed entries, and since 3.12 ``site.addpackage()``
    silently skips a hidden ``.pth``. The editable install breaks again every
    time iCloud re-flags the venv, with no diagnostic. Verified broken while
    this was written.
    """
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        entry for entry in sys.path if entry
    )
    return environment


class _WorkerTimeout(Exception):
    """The worker did not answer inside the budget."""


class _WorkerGone(Exception):
    """The worker vanished -- it cannot have been the tool body that did it."""


# ---------------------------------------------------------------------------
# framed pipe I/O
# ---------------------------------------------------------------------------

def _write_all(fd: int, data: bytes) -> None:
    """``os.write`` until it is all gone; a pipe accepts partial writes."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _read_exactly(fd: int, count: int) -> bytes | None:
    """Exactly ``count`` bytes, or ``None`` at a clean end of stream."""
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = os.read(fd, remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_exactly_before(fd: int, count: int, deadline: float) -> bytes:
    """``_read_exactly`` under a deadline, without blocking past it.

    Selecting on the descriptor rather than reading straight away is what keeps
    a timeout a timeout: a blocking read on a worker that is busy forever would
    never come back to be cancelled.
    """
    chunks: list[bytes] = []
    remaining = count
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        while remaining:
            left = deadline - time.monotonic()
            if left <= 0:
                raise _WorkerTimeout
            if not selector.select(left):
                raise _WorkerTimeout
            chunk = os.read(fd, remaining)
            if not chunk:
                # The writer closed. Killed mid-call, or gone entirely; either
                # way there is no answer coming and nothing to wait for.
                raise _WorkerGone
            chunks.append(chunk)
            remaining -= len(chunk)
    return b"".join(chunks)


def _write_all_before(fd: int, data: bytes, deadline: float) -> None:
    """``_write_all`` under the same deadline, for a worker that stopped reading."""
    view = memoryview(data)
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_WRITE)
        while view:
            left = deadline - time.monotonic()
            if left <= 0:
                raise _WorkerTimeout
            if not selector.select(left):
                raise _WorkerTimeout
            try:
                view = view[os.write(fd, view):]
            except BrokenPipeError as exc:
                raise _WorkerGone from exc


def _tail(stream: bytes) -> str:
    text = stream.decode("utf-8", "replace").strip()
    if len(text) > _STDERR_TAIL:
        text = "..." + text[-_STDERR_TAIL:]
    return f"Worker stderr: {text}" if text else "The worker said nothing."


def _exit_detail(status: int) -> str:
    """Why a forked child produced no answer, in the terms the OS reported it."""
    if os.WIFSIGNALED(status):
        number = os.WTERMSIG(status)
        return f"killed by signal {number} ({signal.Signals(number).name})"
    if os.WIFEXITED(status):
        return f"exited with code {os.WEXITSTATUS(status)}"
    return f"ended with wait status {status}"


# ---------------------------------------------------------------------------
# the worker, from the server's side
# ---------------------------------------------------------------------------

class _Worker:
    """One long-lived process that forks a child per call.

    Serialised by a lock: mcp dispatches synchronous tools on a thread pool, so
    two calls really can arrive at once, and one worker behind one pair of pipes
    has no way to tell whose frame is whose. The work itself is milliseconds,
    so queueing costs far less than the start-up it avoids.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._stderr = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def pid(self) -> int | None:
        """The running worker's pid, which is also its process group id."""
        process = self._process
        return None if process is None else process.pid

    def prewarm(self) -> None:
        """Start the worker and wait for it to answer, before any call needs it.

        Raises rather than reporting a failure dict: nothing has been asked yet,
        so there is no verdict this could be confused with.
        """
        with self._lock:
            deadline = time.monotonic() + TIMEOUT_SECONDS
            process = self._ensure()
            _write_all_before(process.stdin.fileno(), _HEADER.pack(0), deadline)
            out = process.stdout.fileno()
            (size,) = _HEADER.unpack(
                _read_exactly_before(out, _HEADER.size, deadline)
            )
            if size:  # a ping is answered by a ping, not by a payload
                _read_exactly_before(out, size, deadline)
                raise _WorkerGone(f"worker answered a ping with {size} bytes")

    def _start(self) -> subprocess.Popen:
        # A regular file rather than a pipe: nothing drains the worker's stderr
        # between calls, and a pipe that fills up would wedge the worker on its
        # next warning. lcapy warns routinely ("Removing voltage source ...").
        self._stderr = tempfile.TemporaryFile()
        process = subprocess.Popen(
            _worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            env=_worker_environment(),
            bufsize=0,
            # Its own session, so one killpg reaches the worker *and* whatever
            # it forked. Without this the group is the server's own, and
            # killing it would take the server down with it.
            start_new_session=True,
        )
        self._process = process
        return process

    def _ensure(self) -> subprocess.Popen:
        process = self._process
        if process is None or process.poll() is not None:
            self._discard()
            process = self._start()
        return process

    def _discard(self) -> None:
        """Kill the worker's whole group and reap it. Safe to call twice."""
        process, self._process = self._process, None
        stderr, self._stderr = self._stderr, None
        if process is not None:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except OSError:
                    pass
            process.wait()  # reap, so nothing is left as a zombie
        if stderr is not None:
            stderr.close()

    def shutdown(self) -> None:
        """Ask the worker to leave, then insist. Registered with ``atexit``."""
        with self._lock:
            process = self._process
            if process is None:
                return
            if process.poll() is None:
                try:
                    process.stdin.close()  # end of stream: the loop returns
                except OSError:
                    pass
                try:
                    process.wait(timeout=_SHUTDOWN_GRACE)
                except subprocess.TimeoutExpired:
                    pass
            self._discard()

    def _read_stderr(self) -> bytes:
        if self._stderr is None:
            return b""
        try:
            self._stderr.seek(0)
            return self._stderr.read()
        except OSError:
            return b""

    # -- one call ----------------------------------------------------------

    def _exchange(
        self, process: subprocess.Popen, request: bytes, deadline: float
    ) -> dict[str, Any]:
        _write_all_before(
            process.stdin.fileno(), _HEADER.pack(len(request)) + request, deadline
        )
        out = process.stdout.fileno()
        (size,) = _HEADER.unpack(_read_exactly_before(out, _HEADER.size, deadline))
        return pickle.loads(_read_exactly_before(out, size, deadline))

    def call(self, name: str, kwargs: dict[str, Any], limit: float) -> dict[str, Any]:
        request = pickle.dumps((name, kwargs), protocol=pickle.HIGHEST_PROTOCOL)

        with self._lock:
            # Two attempts, and only for a worker that was already gone. A tool
            # body runs in a forked child and cannot bring the worker down, so
            # "the worker vanished" is never a statement about this input --
            # which is what makes retrying it honest rather than a loop around
            # a crash.
            for attempt in (0, 1):
                deadline = time.monotonic() + limit
                try:
                    process = self._ensure()
                except OSError as exc:
                    return _failure(
                        "internal_error", f"Could not start the {name} worker: {exc}"
                    )

                try:
                    return self._exchange(process, request, deadline)
                except _WorkerTimeout:
                    self._discard()  # kills the runaway child with its group
                    return _failure(
                        "timeout",
                        f"{name} exceeded the {limit}s wall-clock limit and the "
                        f"worker was killed. An expression can parse instantly "
                        f"and still take unbounded time to evaluate -- 9^(9^9) "
                        f"is the standing example. Simplify the input, or reduce "
                        f"the size of the literals in it.",
                    )
                except _WorkerGone:
                    stderr = self._read_stderr()
                    self._discard()
                    if attempt == 0:
                        continue  # it had already died; a fresh one may serve
                    return _failure(
                        "internal_error",
                        f"The {name} worker died without answering. "
                        f"{_tail(stderr)}",
                    )
                except Exception as exc:  # a corrupted frame is not a verdict
                    stderr = self._read_stderr()
                    self._discard()
                    return _failure(
                        "internal_error",
                        f"The {name} worker returned something unreadable "
                        f"({type(exc).__name__}: {exc}). {_tail(stderr)}",
                    )

        # Unreachable: the loop either returns or continues exactly once.
        return _failure("internal_error", f"The {name} worker could not be reached.")


_WORKER = _Worker()
atexit.register(_WORKER.shutdown)


def _guarded(name: str, **kwargs: Any) -> dict[str, Any]:
    """Run one tool implementation in a worker that can actually be killed."""
    return _WORKER.call(name, kwargs, TIMEOUT_SECONDS)


# ---------------------------------------------------------------------------
# the worker, from its own side
# ---------------------------------------------------------------------------

def _run_child(request: bytes, read_fd: int, write_fd: int) -> NoReturn:
    """Do one call and exit. Never returns, and never raises into the loop."""
    try:
        os.close(read_fd)
        # The child inherits the worker's end of the request pipe. Nothing here
        # reads it, but lcapy's SymbolRegistry.lookup() can drop into pdb, and
        # a debugger reading fd 0 would eat the next call's frame. Point it at
        # /dev/null so anything reaching for stdin gets EOF instead.
        with open(os.devnull, "rb") as devnull:
            os.dup2(devnull.fileno(), 0)
        name, kwargs = pickle.loads(request)
        _write_all(
            write_fd,
            pickle.dumps(_dispatch(name, kwargs), protocol=pickle.HIGHEST_PROTOCOL),
        )
        os.close(write_fd)
    except BaseException:  # noqa: BLE001 -- the parent reads the exit status
        traceback.print_exc(file=sys.stderr)
        os._exit(1)
    # ``os._exit``: a forked child must not run atexit handlers or flush
    # buffers it inherited a copy of.
    os._exit(0)


def _serve_one_call(request: bytes) -> bytes:
    """Fork, let the child do the work, and come back with whatever it wrote."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        _run_child(request, read_fd, write_fd)  # never returns

    os.close(write_fd)
    chunks: list[bytes] = []
    try:
        while True:
            chunk = os.read(read_fd, 1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(read_fd)
    _, status = os.waitpid(pid, 0)

    payload = b"".join(chunks)
    if payload:
        return payload
    # No answer: the child was killed, or died before it could write one. The
    # worker itself is fine, so this is reported and the loop carries on.
    return pickle.dumps(
        _failure(
            "internal_error",
            f"The worker's child produced no result -- it {_exit_detail(status)}.",
        ),
        protocol=pickle.HIGHEST_PROTOCOL,
    )


def _serve_forever() -> None:
    """Worker entry point: framed calls in, framed results out, until EOF."""
    # Anything that prints to stdout -- an lcapy warning, a stray debug print in
    # a dependency -- would land in the middle of a frame and be read as part of
    # a result. Keep a private copy of the real stdout for the protocol and
    # point fd 1 at stderr, which children inherit already redirected.
    protocol_out = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    protocol_in = sys.stdin.fileno()

    while True:
        header = _read_exactly(protocol_in, _HEADER.size)
        if header is None:
            return  # the server closed the pipe; leave quietly
        (size,) = _HEADER.unpack(header)
        if size == 0:  # a ping: already imported, so answering is the proof
            _write_all(protocol_out, _HEADER.pack(0))
            continue
        request = _read_exactly(protocol_in, size)
        if request is None:
            return
        payload = _serve_one_call(request)
        _write_all(protocol_out, _HEADER.pack(len(payload)) + payload)


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

@server.tool()
def derive(
    netlist: str,
    in_pos: str | int,
    in_neg: str | int,
    out_pos: str | int,
    out_neg: str | int,
    mode: str = "finite",
) -> dict[str, Any]:
    """Ground-truth transfer function between two node pairs, and its poles.

    ``netlist`` is lcapy syntax. Write an s-domain source as ``Vs 1 0 s {V}``:
    a plain ``Vs 1 0 {V}`` with a capacitor present solves the DC steady state
    instead, silently, and an s-domain derivation checked against that would be
    called wrong while being entirely right.

    ``mode`` selects the op-amp model:

    * ``finite`` -- the circuit exactly as written, open-loop gain ``A``.
    * ``ideal`` -- ``A`` taken to infinity, i.e. the textbook result.
    * ``gbw`` -- ``A`` replaced by ``A0 / (1 + s/wp)``, whose pole is the
      gain-bandwidth tradeoff.

    ``ideal`` and ``gbw`` require the open-loop gain to be named ``A`` in the
    netlist and are refused otherwise, because substituting a symbol that is not
    there changes nothing and would return the original result mislabelled.

    Each expression comes back as ``text`` and ``srepr``. Pass ``text`` to
    :func:`check_derivation` as the ground truth; ``srepr`` is the exact record,
    assumptions included, for a caller reconstructing the expression in SymPy.
    """
    return _guarded(
        "derive",
        netlist=netlist,
        in_pos=in_pos,
        in_neg=in_neg,
        out_pos=out_pos,
        out_neg=out_neg,
        mode=mode,
    )


@server.tool()
def check_equivalence(expr_a: str, expr_b: str) -> dict[str, Any]:
    """Are two expressions algebraically equal?

    Decided by two oracles. ``oracle`` says which one settled it: ``symbolic``
    is a proof, ``numeric`` is random substitution -- conclusive when it finds a
    disagreement, strong evidence when it does not. A disagreement comes with a
    ``counterexample``: the values at which the two sides differ.

    Both sides are parsed onto one set of symbols, so ``Rf`` on the left is the
    same object as ``Rf`` on the right.
    """
    return _guarded("check_equivalence", expr_a=expr_a, expr_b=expr_b)


@server.tool()
def check_derivation(
    steps: list[str], truth: str, parameters: dict[str, float] | None = None
) -> dict[str, Any]:
    """Find where an ordered derivation diverges from the truth.

    ``steps`` is the working in order, one expression per step; ``truth`` is the
    ground-truth result, normally the ``text`` rendering from :func:`derive`.

    ``kind`` says what went wrong, because the three failures want different
    feedback:

    * ``algebra`` -- a transition is not an equality. ``step_index`` is 0-based
      and names the *transition*: 1 means step 2 -> step 3.
    * ``setup`` -- every transition holds but the answer is still wrong, so the
      fault is before step 1: the equations do not describe the circuit.
    * ``final`` -- a single expression that does not match, with no working to
      bisect. Send the intermediate steps and the transition can be located.

    ``parameters`` optionally supplies finite numeric values used throughout
    the derivation. Both the original and evaluated steps are echoed, so a
    symbolic-to-numeric substitution is checked without hiding what changed.

    The parsed steps are echoed back. Check them against what was actually
    written before trusting a verdict -- a misread subscript produces a
    confident "your step 3 is wrong" about a step 3 that was fine.
    """
    return _guarded(
        "check_derivation", steps=list(steps), truth=truth,
        parameters=parameters or {},
    )


@server.tool()
def circuit_equations(netlist: str) -> dict[str, Any]:
    """lcapy's own nodal system for a netlist, plus the solved node voltages.

    ``equations_available`` is ``False`` for any circuit containing a dependent
    source -- which is every op-amp -- because lcapy will not form a nodal
    system for one. That is a limit of the tool, not a statement about the
    circuit: ``note`` says which source caused it, and the solved values below
    are still exact and are what a setup check actually compares against. Do not
    report it as "this circuit has no equations".
    """
    return _guarded("circuit_equations", netlist=netlist)


@server.tool()
def check_setup(
    netlist: str, equations: list[str], unknowns: list[str]
) -> dict[str, Any]:
    """Does a system of equations describe this circuit?

    ``equations`` are written ``"lhs = rhs"``. ``unknowns`` names what the
    system solves for: node voltages as ``V1`` or ``V_1``, branch currents as
    ``I_R1`` or ``IR1``.

    This is not a diff against lcapy's equations -- there are many correct
    formulations, and flagging a correct-but-different one is the worst thing
    this tool could do. Instead each equation is checked against the circuit's
    actual solution, and the system is checked for full rank. So ``kind`` is
    ``not_satisfied`` when an equation is false of the circuit (``failing_equation``
    is its 0-based index) and ``underdetermined`` when every equation holds but
    they do not pin the unknowns down.

    ``equation_roles`` classifies each satisfied equation as ``law``, ``solved``,
    ``ambiguous``, or ``trivial``. This is advisory and never changes the
    verdict: on small circuits a circuit law and a solved answer can be the same
    equation up to scaling, so the server reports that ambiguity rather than
    guessing what the student intended.

    Two conventions are lcapy's and are assumed here: node voltages reference
    node ``0``, and a branch current flows *into* the first node named for that
    element in the netlist. The opposite current direction reads as a sign error.
    """
    return _guarded(
        "check_setup",
        netlist=netlist,
        equations=list(equations),
        unknowns=list(unknowns),
    )


@server.tool()
def simulate_spice(
    netlist: str, analysis: str, outputs: list[str] | None = None
) -> dict[str, Any]:
    """Run a bounded local ngspice operating-point, sweep, AC, or transient analysis.

    ``netlist`` contains components and may contain models/parameters, but not
    control blocks, file includes, shell commands, an analysis directive, or
    ``.end``; this tool appends the chosen analysis itself. ``analysis`` is one
    of ``op``, ``dc SOURCE START STOP STEP``, ``ac dec|oct|lin N START STOP``,
    or ``tran TSTEP TSTOP [TSTART [TMAX]]``. ``outputs`` optionally selects
    vectors such as ``v(out)`` or ``i(v1)``. Results are numeric simulation,
    useful for nonlinear and time-domain checking; they are not symbolic proof.
    """
    return _guarded(
        "simulate_spice", netlist=netlist, analysis=analysis, outputs=outputs or []
    )


@server.tool()
def characterize_transfer(
    expression: str,
    parameters: dict[str, float] | None = None,
    feedback: str = "none",
) -> dict[str, Any]:
    """Characterize a numeric loop or system transfer function.

    Returns poles, zeros, stability, DC gain, bandwidth, gain/phase crossover
    data, an explicit stability classification, and step-response metrics when
    meaningful. Supply every symbol except ``s`` in ``parameters``. For loop
    margins, pass the loop transfer and set ``feedback='negative_unity'`` to
    also receive a separately labelled ``closed_loop`` characterization.
    """
    return _guarded(
        "characterize_transfer", expr=expression, parameters=parameters or {},
        feedback=feedback,
    )


@server.tool()
def converter_metrics(
    kind: str,
    bits: int,
    values: list[float],
    v_min: float = 0.0,
    v_max: float = 1.0,
) -> dict[str, Any]:
    """Compute endpoint INL/DNL and missing-code/monotonicity results.

    For a DAC, ``values`` contains all ``2**bits`` measured output levels in
    code order. For an ADC, it contains the ``2**bits - 1`` transition voltages.
    Results are in LSB.
    """
    return _guarded(
        "converter_metrics", kind=kind, bits=bits, values=list(values),
        v_min=v_min, v_max=v_max,
    )


@server.tool()
def spectrum_metrics(
    samples: list[float],
    sample_rate: float,
    fundamental_hz: float,
    harmonics: int = 5,
) -> dict[str, Any]:
    """Compute coherent-FFT harmonics, THD, SINAD, and ENOB.

    The record must contain an integer number of fundamental cycles; refusing
    incoherent data avoids silently grading spectral leakage as distortion.
    Amplitudes are single-sided peak amplitudes for real-valued samples.
    """
    return _guarded(
        "spectrum_metrics", samples=list(samples), sample_rate=sample_rate,
        fundamental_hz=fundamental_hz, harmonics=harmonics,
    )


@server.tool()
def quantize(
    values: list[float], bits: int, v_min: float = 0.0, v_max: float = 1.0
) -> dict[str, Any]:
    """Apply an ideal unipolar ADC quantizer and return codes and errors.

    Codes use half-open bins over ``[v_min, v_max)`` and saturate outside that
    interval. Reconstruction uses bin centers, so an in-range ideal error is
    bounded by half an LSB.
    """
    return _guarded(
        "quantize", values=list(values), bits=bits, v_min=v_min, v_max=v_max
    )


@server.tool()
def opamp_limits(
    gain: float,
    noise_gain: float,
    gbw_hz: float,
    slew_rate_v_s: float,
    output_peak_v: float,
    signal_hz: float,
) -> dict[str, Any]:
    """Check first-order bandwidth and slew-rate limits for an op-amp stage."""
    return _guarded(
        "opamp_limits", gain=gain, noise_gain=noise_gain, gbw_hz=gbw_hz,
        slew_rate_v_s=slew_rate_v_s, output_peak_v=output_peak_v,
        signal_hz=signal_hz,
    )


@server.tool()
def rectifier_metrics(input_peak_v: float, diode_drop_v: float = 0.0) -> dict[str, Any]:
    """Analyze a constant-drop half-wave rectifier, including its DC average."""
    return _guarded("rectifier_metrics", input_peak_v=input_peak_v, diode_drop_v=diode_drop_v)


@server.tool()
def bjt_emitter_follower(
    collector_current_a: float, beta: float, thermal_voltage_v: float, load_ohm: float
) -> dict[str, Any]:
    """Compute hybrid-pi gm, r_pi, and emitter-follower gain with r_o neglected."""
    return _guarded(
        "bjt_emitter_follower", collector_current_a=collector_current_a,
        beta=beta, thermal_voltage_v=thermal_voltage_v, load_ohm=load_ohm,
    )


@server.tool()
def relaxation_oscillator(rail_v: float, threshold_v: float, rc_s: float) -> dict[str, Any]:
    """Compute symmetric Schmitt-trigger RC oscillator period and frequency."""
    return _guarded("relaxation_oscillator", rail_v=rail_v, threshold_v=threshold_v, rc_s=rc_s)


@server.tool()
def dac_output(
    codes: list[int], bits: int, v_min: float = 0.0, v_max: float = 1.0
) -> dict[str, Any]:
    """Map ideal straight-binary DAC codes to output voltages."""
    return _guarded("dac_output", codes=list(codes), bits=bits, v_min=v_min, v_max=v_max)


@server.tool()
def alias_frequency(input_hz: float, sample_rate_hz: float) -> dict[str, Any]:
    """Fold a real sinusoid into the first Nyquist zone."""
    return _guarded("alias_frequency", input_hz=input_hz, sample_rate_hz=sample_rate_hz)


@server.tool()
def transimpedance(input_current_a: float, feedback_ohm: float) -> dict[str, Any]:
    """Analyze an ideal inverting current-to-voltage op-amp stage."""
    return _guarded("transimpedance", input_current_a=input_current_a, feedback_ohm=feedback_ohm)


@server.tool()
def library_search(query: str = "", category: str = "", limit: int = 20) -> dict[str, Any]:
    """Search local document names and extracted text; returns metadata, never file paths."""
    return _guarded("library_search", query=query, category=category, limit=limit)


@server.tool()
def document_get(document_id: str) -> dict[str, Any]:
    """Read one local document's metadata and bounded extracted text by opaque ID."""
    return _guarded("document_get", document_id=document_id)


@server.tool()
def problem_get(problem_id: str) -> dict[str, Any]:
    """Read one confirmed or draft problem and its tags by opaque ID."""
    return _guarded("problem_get", problem_id=problem_id)


@server.tool()
def study_context(query: str, limit: int = 10) -> dict[str, Any]:
    """Find local notes and problems relevant to a bounded course query."""
    return _guarded("study_context", query=query, limit=limit)


@server.tool()
def attempt_history(problem_id: str, limit: int = 100) -> dict[str, Any]:
    """Read prior attempts and summarized MCP evidence for one problem."""
    return _guarded("attempt_history", problem_id=problem_id, limit=limit)


@server.tool()
def course_progress() -> dict[str, Any]:
    """Summarize EE 2300 problem and attempt states by topic."""
    return _guarded("course_progress")


@server.tool()
def problem_create(
    title: str, topic: str, prompt: str, document_id: str | None = None,
    circuit_interpretation: str = "", status: str = "draft",
    source_page: int | None = None,
) -> dict[str, Any]:
    """Create a bounded local problem record; this does not judge its mathematics."""
    return _guarded(
        "problem_create", title=title, topic=topic, prompt=prompt,
        document_id=document_id, circuit_interpretation=circuit_interpretation,
        status=status, source_page=source_page,
    )


@server.tool()
def problem_update_interpretation(
    problem_id: str, circuit_interpretation: str, status: str = "confirmed"
) -> dict[str, Any]:
    """Store a user-confirmed circuit interpretation and workflow status."""
    return _guarded(
        "problem_update_interpretation", problem_id=problem_id,
        circuit_interpretation=circuit_interpretation, status=status,
    )


@server.tool()
def transcription_confirm(
    transcription_id: str, corrected_content: str | None = None
) -> dict[str, Any]:
    """Confirm a transcription or preserve a corrected revision that supersedes it."""
    return _guarded(
        "transcription_confirm", transcription_id=transcription_id,
        corrected_content=corrected_content,
    )


@server.tool()
def attempt_create(
    problem_id: str, actor: str, answer: str = "", status: str = "working"
) -> dict[str, Any]:
    """Start a local student or agent attempt for an existing problem."""
    return _guarded(
        "attempt_create", problem_id=problem_id, actor=actor,
        answer=answer, status=status,
    )


@server.tool()
def attempt_complete(
    attempt_id: str, answer: str, status: str,
    first_divergence: str | None = None,
) -> dict[str, Any]:
    """Complete an attempt as correct, incorrect, partial, or gap."""
    return _guarded(
        "attempt_complete", attempt_id=attempt_id, answer=answer,
        status=status, first_divergence=first_divergence,
    )


@server.tool()
def problem_tag(problem_id: str, tag: str) -> dict[str, Any]:
    """Attach one normalized course tag to a problem."""
    return _guarded("problem_tag", problem_id=problem_id, tag=tag)


@server.tool()
def animation_create(scene: dict[str, Any], problem_id: str | None = None) -> dict[str, Any]:
    """Create a validated hand-drawn visual scene and request that the board spawn it."""
    return _guarded("animation_create", scene=scene, problem_id=problem_id)


@server.tool()
def animation_list(updated_after: float = 0, limit: int = 100) -> dict[str, Any]:
    """List persisted visual scenes for board synchronization."""
    return _guarded("animation_list", updated_after=updated_after, limit=limit)


@server.tool()
def animation_update(animation_id: str, scene: dict[str, Any]) -> dict[str, Any]:
    """Replace a visual scene with a validated revision and refresh its board card."""
    return _guarded("animation_update", animation_id=animation_id, scene=scene)


@server.tool()
def animation_delete(animation_id: str) -> dict[str, Any]:
    """Soft-delete a visual scene and remove it from synchronized boards."""
    return _guarded("animation_delete", animation_id=animation_id)


@server.tool()
def animation_from_template(template: str, title: str | None = None, problem_id: str | None = None) -> dict[str, Any]:
    """Spawn one official EE 2300 visual template; use animation_list_templates for names."""
    return _guarded("animation_from_template", template=template, title=title, problem_id=problem_id)


@server.tool()
def animation_list_templates() -> dict[str, Any]:
    """List visual templates spanning the official EE 2300 catalog areas."""
    return {"ok": True, "templates": template_names()}


@server.tool()
def import_waveform_csv(
    csv_text: str, time_column: str, value_columns: list[str]
) -> dict[str, Any]:
    """Parse a bounded oscilloscope/DMM CSV payload without filesystem access."""
    return _guarded(
        "import_waveform_csv", csv_text=csv_text, time_column=time_column,
        value_columns=list(value_columns),
    )


@server.tool()
def instrument_status() -> dict[str, Any]:
    """List VISA instruments only when explicitly enabled in the MCP environment."""
    return _guarded("instrument_status")


@server.tool()
def instrument_query(
    resource: str, query: str, timeout_ms: int = 5000
) -> dict[str, Any]:
    """Issue one allow-listed read-only SCPI query to an explicitly enabled instrument."""
    return _guarded(
        "instrument_query", resource=resource, query=query, timeout_ms=timeout_ms
    )


@server.tool()
def workspace_status() -> dict[str, Any]:
    """Can this Mac capture a mirrored iPad workspace?

    This is a read-only capability check and does not take a screenshot or
    trigger the macOS privacy prompt. ``permission`` remains
    ``unknown_until_capture`` because macOS exposes denial through the capture
    attempt itself.
    """
    return _guarded("workspace_status")


@server.tool(structured_output=False)
def capture_workspace(
    display: int = 1,
    allow_full_display: bool = False,
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> CallToolResult:
    """Capture the current mirrored iPad screen for visual inspection.

    The privacy-safe path is to pass all of ``x``, ``y``, ``width``, and
    ``height`` and capture only the visible iPad region in global screen
    coordinates. A whole display (numbered from 1) is refused unless
    ``allow_full_display`` is explicitly true, because it can expose unrelated
    windows and notifications. Capture happens only when this tool is called;
    there is no background recording.

    The result contains a PNG image plus JSON metadata. ``sha256`` lets a client
    tell whether the page changed since its last observation. Before checking
    handwritten mathematics, transcribe the visible equations and ask the user
    to confirm that transcription; subscripts, signs, and fraction bars are not
    safe to infer silently.
    """
    result = _guarded(
        "capture_workspace",
        display=display,
        allow_full_display=allow_full_display,
        x=x,
        y=y,
        width=width,
        height=height,
    )
    if not result.get("ok"):
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result))],
            structured_content=result,
        )

    png = result.pop("png")
    return CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps(result)),
            ImageContent(
                type="image",
                data=base64.b64encode(png).decode("ascii"),
                mime_type="image/png",
            ),
        ],
        structured_content=result,
    )


@server.tool()
def ipad_capture_status() -> dict[str, Any]:
    """Report AirPlay receiver and USB-C iPad screen availability."""
    return IPAD_CAPTURE.status()


@server.tool()
def ipad_receiver_start() -> dict[str, Any]:
    """Start the local PIN-protected EE2300 AirPlay receiver."""
    try:
        return IPAD_CAPTURE.start_airplay()
    except IPadCaptureError as exc:
        return _failure("ipad_capture_error", str(exc))


@server.tool()
def ipad_receiver_stop() -> dict[str, Any]:
    """Stop the local AirPlay receiver and discard its ephemeral PIN."""
    return IPAD_CAPTURE.stop_airplay()


@server.tool(structured_output=False)
def capture_ipad_screen(source: str = "auto") -> CallToolResult:
    """Capture the current iPadOS screen, preferring AirPlay then USB-C."""
    try:
        result = IPAD_CAPTURE.capture(source)
    except IPadCaptureError as exc:
        failure = _failure("ipad_capture_error", str(exc))
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(failure))],
                              structured_content=failure)
    png = result.pop("png")
    return CallToolResult(content=[
        TextContent(type="text", text=json.dumps(result)),
        ImageContent(type="image", data=base64.b64encode(png).decode("ascii"),
                     mime_type="image/png"),
    ], structured_content=result)


def _decode_image(image_base64: str) -> bytes | dict[str, Any]:
    """Strict base64 input for OCR tools, or a structured error."""
    try:
        png = base64.b64decode(image_base64, validate=True)
    except (ValueError, TypeError) as exc:
        return _failure("bad_image", f"image_base64 is not valid base64: {exc}")
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        return _failure("bad_image", "Decoded image is not a PNG.")
    return png


def _transcription_content(
    result: dict[str, Any], png: bytes | None = None
) -> CallToolResult:
    """OCR metadata/LaTeX and, for workspace calls, the exact reviewed frame."""
    blocks: list[Any] = [
        TextContent(type="text", text=json.dumps(result, sort_keys=True))
    ]
    if png is not None:
        blocks.append(
            ImageContent(
                type="image",
                data=base64.b64encode(png).decode("ascii"),
                mime_type="image/png",
            )
        )
    return CallToolResult(content=blocks, structured_content=result)


@server.tool()
def ocr_status(load_model: bool = False) -> dict[str, Any]:
    """Report UniMERNet availability, process state, device, and memory data.

    With ``load_model=false`` this is cheap and does not start PyTorch. Set it
    true to start the persistent worker, load UniMERNet, and prove that its
    selected device (normally ``mps`` on Apple Silicon) is operational.
    """
    if not load_model and OCR_WORKER.pid is None:
        return OCR_WORKER.availability()
    return OCR_WORKER.call({"action": "status", "load_model": load_model})


@server.tool(structured_output=False)
def transcribe_image(image_base64: str) -> CallToolResult:
    """Convert one tightly cropped PNG mathematical expression to LaTeX.

    UniMERNet is a formula recognizer, not a page-layout or circuit-topology
    model. Crop to one expression before calling. The output is untrusted
    transcription: echo it to the student and obtain confirmation before using
    it in any circuit verdict.
    """
    decoded = _decode_image(image_base64)
    if isinstance(decoded, dict):
        return _transcription_content(decoded)
    result = OCR_WORKER.call({"action": "transcribe", "png": decoded})
    return _transcription_content(result)


@server.tool()
def workspace_configuration() -> dict[str, Any]:
    """Return the saved privacy-scoped iPad screen capture source."""
    return _read_workspace()


@server.tool()
def configure_workspace(
    x: int, y: int, width: int, height: int, display: int = 1
) -> dict[str, Any]:
    """Save the visible iPad screen rectangle used by transcription tools.

    Coordinates are global macOS screen coordinates. Only this rectangle is
    captured; full-display capture is intentionally not configurable here.
    """
    try:
        return _configure_workspace(x, y, width, height, display)
    except CaptureError as exc:
        return _failure("capture_error", str(exc))


@server.tool(structured_output=False)
def transcribe_workspace(
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
    display: int | None = None,
) -> CallToolResult:
    """Capture an iPad screen region and transcribe it locally with UniMERNet.

    Explicit coordinates override the saved workspace. When none are supplied,
    :func:`configure_workspace` must have saved a region first. The response
    includes the exact PNG seen by OCR, the LaTeX, frame hash, model/device, and
    timing. Always show both image and transcription to the student and wait for
    confirmation before checking their mathematics.
    """
    supplied = (x, y, width, height)
    if all(value is None for value in supplied):
        configuration = _read_workspace()
        if not configuration.get("ok"):
            return _transcription_content(configuration)
        x, y, width, height = (
            configuration["x"],
            configuration["y"],
            configuration["width"],
            configuration["height"],
        )
        chosen_display = configuration["display"]
    else:
        chosen_display = 1 if display is None else display
    captured = _guarded(
        "capture_workspace",
        display=chosen_display,
        allow_full_display=False,
        x=x,
        y=y,
        width=width,
        height=height,
    )
    if not captured.get("ok"):
        return _transcription_content(captured)
    png = captured.pop("png")
    transcription = OCR_WORKER.call({"action": "transcribe", "png": png})
    result = {**transcription, "capture": captured}
    return _transcription_content(result, png)


def main() -> None:
    """Serve over stdio, or serve tool calls as the worker subprocess."""
    if _WORKER_FLAG in sys.argv[1:]:
        _serve_forever()
        return
    # Pay the worker's start-up now rather than inside the first tool call, so
    # the first question of a session answers as fast as the rest. A failure
    # here is said out loud on stderr and then left alone: every tool call
    # reports its own error anyway, and refusing to serve would turn a
    # recoverable hiccup into a dead server.
    try:
        _WORKER.prewarm()
    except Exception as exc:  # noqa: BLE001 -- announced, not swallowed
        print(
            f"circuit_mcp: could not pre-start the worker "
            f"({type(exc).__name__}: {exc}); the first tool call will retry.",
            file=sys.stderr,
        )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
