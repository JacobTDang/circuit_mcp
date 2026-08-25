"""Step-chain differ: locate where a derivation diverges, not just that it did."""
import sympy as sp

from circuit_mcp.steps import check_steps

s = sp.Symbol("s")
R, C = sp.symbols("R C", positive=True)

TRUTH = 1 / (1 + s * R * C)          # first-order lowpass
CORRECT = [
    1 / (1 + s * R * C),
    (1 / (R * C)) / (s + 1 / (R * C)),  # divide through by RC
    1 / (R * C * s + 1),                # reorder
]


def test_correct_derivation_passes():
    result = check_steps(CORRECT, TRUTH)
    assert result.ok
    assert result.kind == "ok"


def test_algebra_error_is_located_at_the_transition_that_breaks():
    steps = [
        1 / (1 + s * R * C),
        1 / (1 - s * R * C),   # sign flipped here: transition 0 -> 1
        1 / (1 - s * R * C),
    ]
    result = check_steps(steps, TRUTH)
    assert not result.ok
    assert result.kind == "algebra"
    assert result.step_index == 0


def test_algebra_error_later_in_the_chain():
    steps = [
        1 / (1 + s * R * C),
        1 / (R * C * s + 1),
        2 / (R * C * s + 1),   # factor of 2 appears: transition 1 -> 2
    ]
    result = check_steps(steps, TRUTH)
    assert result.kind == "algebra"
    assert result.step_index == 1


def test_consistent_chain_with_wrong_answer_is_a_setup_error():
    """Every transition valid but the answer is wrong => the start was wrong.

    Equivalence is transitive, so a valid chain cannot move a correct start to
    an incorrect end. The fault must precede step 0.
    """
    steps = [
        1 / (1 + 2 * s * R * C),   # wrong from the outset
        1 / (2 * R * C * s + 1),   # a faithful rewrite of the wrong thing
    ]
    result = check_steps(steps, TRUTH)
    assert not result.ok
    assert result.kind == "setup"
    assert result.step_index == 0


def test_single_correct_step_passes():
    assert check_steps([TRUTH], TRUTH).ok


def test_single_wrong_step_cannot_be_localised():
    """With no transitions there is nothing to bisect; say so rather than guess."""
    result = check_steps([1 / (1 - s * R * C)], TRUTH)
    assert not result.ok
    assert result.kind == "final"
    assert result.step_index is None
    assert "step" in result.message.lower()


def test_failure_carries_a_counterexample():
    steps = [1 / (1 + s * R * C), 1 / (1 - s * R * C)]
    result = check_steps(steps, TRUTH)
    assert result.counterexample is not None


def test_empty_chain_is_rejected():
    result = check_steps([], TRUTH)
    assert not result.ok
    assert result.kind == "empty"
