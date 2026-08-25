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
