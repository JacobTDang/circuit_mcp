"""Locate where a derivation diverges.

Setup errors and algebra errors are different failures and want different
feedback, so they are reported separately. Conflating them produces the useless
"something is wrong somewhere" verdict this tool exists to avoid.

The setup case is an inference rather than a separate check: equivalence is
transitive, so a chain of valid transitions cannot carry a correct starting
expression to an incorrect final one. If every transition holds and the answer
is still wrong, the fault precedes step 0.
"""
from __future__ import annotations

from dataclasses import dataclass

import sympy as sp

from .equivalence import equivalent
from .symbols import SubstitutionError, bind


@dataclass(frozen=True)
class WrittenCheck:
    """One written derivation, parsed, evaluated and judged."""
    steps: list[sp.Expr]
    truth: sp.Expr
    evaluated_steps: list[sp.Expr]
    evaluated_truth: sp.Expr
    parameters: dict[str, float]
    result: "StepResult"


@dataclass(frozen=True)
class StepResult:
    ok: bool
    kind: str  # "ok" | "algebra" | "setup" | "final" | "empty"
    message: str
    step_index: int | None = None
    counterexample: dict | None = None


def check_steps(steps: list[sp.Expr], truth: sp.Expr) -> StepResult:
    """Check an ordered derivation against a ground-truth expression."""
    if not steps:
        return StepResult(False, "empty", "No steps supplied.")

    steps = [sp.sympify(step) for step in steps]
    truth = sp.sympify(truth)

    # Algebra: each transition must preserve equality.
    for index in range(len(steps) - 1):
        check = equivalent(steps[index], steps[index + 1])
        if not check.equivalent:
            return StepResult(
                False,
                "algebra",
                f"Step {index + 1} -> {index + 2} is not an equality: "
                f"{check.detail}.",
                step_index=index,
                counterexample=check.counterexample,
            )

    final = equivalent(steps[-1], truth)
    if final.equivalent:
        return StepResult(True, "ok", f"All {len(steps)} step(s) check out.")

    # One expression and no transitions: nothing to bisect.
    if len(steps) == 1:
        return StepResult(
            False,
            "final",
            "The answer does not match. Supply your intermediate steps "
            "and I can point at the line where it breaks.",
            counterexample=final.counterexample,
        )

    # Every transition held, yet the answer is wrong -- so the start was wrong.
    return StepResult(
        False,
        "setup",
        "Every transition is algebraically valid, so the algebra is not the "
        "problem -- step 1 does not describe this circuit. Check how the "
        "equations were set up.",
        step_index=0,
        counterexample=final.counterexample,
    )


def check_written(parse, texts: list, truth_text, parameters: dict | None = None) -> WrittenCheck:
    """Parse a written derivation, substitute any given values, and judge it.

    ``parse(text, where, symbols)`` belongs to the caller, so each surface keeps
    its own error type and wording. The binding order is the part worth sharing:
    the truth seeds the symbol table and each step adds whatever it introduces,
    so a symbol parsed fresh in step 4 is the same object step 1 used -- without
    that, correct work compares unequal and is reported wrong.
    """
    truth = parse(truth_text, "the ground truth", None)
    known = dict(bind(truth))
    parsed: list[sp.Expr] = []
    for index, text in enumerate(texts, start=1):
        expr = parse(text, f"step {index}", dict(known))
        known.update(bind(expr))
        parsed.append(expr)

    substitutions: dict[sp.Symbol, float] = {}
    for name, value in (parameters or {}).items():
        if name not in known:
            raise SubstitutionError(f"parameter {name!r} is not present in the derivation")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not sp.Float(value).is_finite:
            raise SubstitutionError(f"parameter {name!r} must be a finite number")
        # JSON numbers arrive as binary floats. Treat their shortest decimal
        # spelling as the intended exact value; otherwise 0.000001 becomes a
        # nearby Float and exact algebra reports phantom errors.
        substitutions[known[name]] = sp.Rational(str(value))
    evaluated = [step.subs(substitutions) for step in parsed]
    evaluated_truth = truth.subs(substitutions)
    return WrittenCheck(parsed, truth, evaluated, evaluated_truth, dict(parameters or {}),
                        check_steps(evaluated, evaluated_truth))
