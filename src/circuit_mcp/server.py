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

import os
import pickle
import subprocess
import sys
from typing import Any

import sympy as sp
from mcp.server.mcpserver import MCPServer

from .analysis import (
    AssumptionError,
    ideal_limit,
    poles,
    transfer,
    with_finite_gbw,
)
from .equivalence import equivalent
from .mna import CircuitError, SetupInputError
from .mna import check_setup as _mna_check_setup
from .mna import circuit_equations as _mna_circuit_equations
from .parsing import ParseError, parse_equation, parse_expression
from .steps import check_steps
from .symbols import SubstitutionError, SymbolConflictError, bind

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


def _check_derivation(steps: list[str], truth: str) -> dict[str, Any]:
    truth_expr = _expression(truth, "the ground truth")

    # The truth's symbols seed the table; each step adds whatever it introduces,
    # so step k+1 binds onto the objects step k already used.
    known = dict(bind(truth_expr))
    parsed: list[sp.Expr] = []
    for index, text in enumerate(steps, start=1):
        expr = _expression(text, f"step {index}", symbols=dict(known))
        known.update(bind(expr))
        parsed.append(expr)

    result = check_steps(parsed, truth_expr)
    return {
        "ok": result.ok,
        "kind": result.kind,
        "message": result.message,
        "step_index": result.step_index,
        "counterexample": _stringified(result.counterexample),
        "steps": [_rendered(step) for step in parsed],
        "truth": _rendered(truth_expr),
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
    }


_IMPLEMENTATIONS = {
    "derive": _derive,
    "check_equivalence": _check_equivalence,
    "check_derivation": _check_derivation,
    "circuit_equations": _circuit_equations,
    "check_setup": _check_setup,
}


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
# A subprocess, not ``signal.SIGALRM``, and not a thread pool.
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
# ``subprocess`` rather than ``multiprocessing``: a ``spawn`` child re-executes
# the parent's ``__main__`` before it unpickles anything, so the worker's
# behaviour would depend on how the server happened to be launched. Verified
# failing -- launched from a heredoc, the child dies with ``FileNotFoundError:
# .../<stdin>`` and the tool reports an internal error. Naming the module to run
# removes the question. The child is told where this package lives explicitly,
# for the same reason.
#
# The cost is one interpreter start-up per call. ``mcp``, ``lcapy`` and
# ``sympy`` import in well under a second between them, against tool bodies that
# routinely take longer than that, so it is paid gladly.

_WORKER_FLAG = "--worker"

# Not ``__name__``: in the worker that is ``"__main__"``. ``__spec__.name`` is
# the importable path under both.
_WORKER_MODULE = __spec__.name if __spec__ is not None else __name__

# Enough stderr to recognise a crash, not so much that a traceback becomes the
# tool's answer.
_STDERR_TAIL = 800


def _worker_command() -> list[str]:
    return [sys.executable, "-m", _WORKER_MODULE, _WORKER_FLAG]


def _worker_environment() -> dict[str, str]:
    """The parent's import path, handed to the child explicitly.

    The worker is a fresh interpreter and has to be told where this package
    lives. Deriving that from the running ``sys.path`` rather than trusting an
    installed distribution means the worker also works from a source checkout,
    and from under pytest's ``pythonpath`` setting.
    """
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        entry for entry in sys.path if entry
    )
    return environment


def _tail(stream: bytes) -> str:
    text = stream.decode("utf-8", "replace").strip()
    if len(text) > _STDERR_TAIL:
        text = "..." + text[-_STDERR_TAIL:]
    return f"Worker stderr: {text}" if text else "The worker said nothing."


def _guarded(name: str, **kwargs: Any) -> dict[str, Any]:
    """Run one tool implementation in a worker that can actually be killed."""
    limit = TIMEOUT_SECONDS
    request = pickle.dumps((name, kwargs), protocol=pickle.HIGHEST_PROTOCOL)

    try:
        finished = subprocess.run(
            _worker_command(),
            input=request,
            capture_output=True,
            timeout=limit,
            env=_worker_environment(),
        )
    except subprocess.TimeoutExpired:
        # subprocess.run kills the child before this propagates, so the
        # computation stops here rather than running on unattended.
        return _failure(
            "timeout",
            f"{name} exceeded the {limit}s wall-clock limit and the worker was "
            f"killed. An expression can parse instantly and still take unbounded "
            f"time to evaluate -- 9^(9^9) is the standing example. Simplify the "
            f"input, or reduce the size of the literals in it.",
        )
    except OSError as exc:
        return _failure(
            "internal_error", f"Could not start the {name} worker: {exc}"
        )

    if finished.returncode != 0:
        return _failure(
            "internal_error",
            f"The {name} worker exited with code {finished.returncode}. "
            f"{_tail(finished.stderr)}",
        )

    try:
        return pickle.loads(finished.stdout)
    except Exception as exc:  # a corrupted result must not read as a verdict
        return _failure(
            "internal_error",
            f"The {name} worker returned something unreadable "
            f"({type(exc).__name__}: {exc}). {_tail(finished.stderr)}",
        )


def _serve_one_call() -> None:
    """Worker entry point: one pickled call in, one finished dict out.

    ``_dispatch`` translates every exception, so this writes a result or the
    process died -- there is no third outcome for the parent to interpret.
    """
    name, kwargs = pickle.load(sys.stdin.buffer)
    sys.stdout.buffer.write(
        pickle.dumps(_dispatch(name, kwargs), protocol=pickle.HIGHEST_PROTOCOL)
    )
    sys.stdout.buffer.flush()


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
def check_derivation(steps: list[str], truth: str) -> dict[str, Any]:
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

    The parsed steps are echoed back. Check them against what was actually
    written before trusting a verdict -- a misread subscript produces a
    confident "your step 3 is wrong" about a step 3 that was fine.
    """
    return _guarded("check_derivation", steps=list(steps), truth=truth)


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


def main() -> None:
    """Serve over stdio, or run a single call as a worker subprocess."""
    if _WORKER_FLAG in sys.argv[1:]:
        _serve_one_call()
        return
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
