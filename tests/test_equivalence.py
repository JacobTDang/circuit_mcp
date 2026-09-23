"""Two-oracle equivalence: symbolic for the proof, numeric for the safety net."""
import sympy as sp

from circuit_mcp.equivalence import equivalent

a, b = sp.symbols("a b")
s = sp.Symbol("s")  # never assume positive: see test_analysis
R, C = sp.symbols("R C", positive=True)


def test_identical_expressions_are_equivalent():
    assert equivalent(a + b, a + b).equivalent


def test_algebraically_equal_but_written_differently():
    assert equivalent((a + b) ** 2, a**2 + 2 * a * b + b**2).equivalent


def test_unequal_expressions_are_not_equivalent():
    assert not equivalent(a + b, a - b).equivalent


def test_unequal_expressions_carry_a_counterexample():
    """A bare 'wrong' is not useful feedback; it must show where they diverge."""
    result = equivalent(a + b, a - b)
    assert result.counterexample is not None
    subs = result.counterexample
    assert sp.simplify((a + b).subs(subs) - (a - b).subs(subs)) != 0


def test_rational_functions_in_s():
    """First-order lowpass written in two equivalent forms."""
    h1 = 1 / (1 + s * R * C)
    h2 = 1 / (R * C) / (s + 1 / (R * C))
    assert equivalent(h1, h2).equivalent


def test_sign_error_in_numerator_is_caught():
    """The realistic homework mistake."""
    h1 = -R * C * s / (1 + s * R * C)
    h2 = R * C * s / (1 + s * R * C)
    assert not equivalent(h1, h2).equivalent


def test_result_reports_which_oracle_decided():
    """Needed to tell a proof from a probabilistic verdict."""
    assert equivalent(a + b, a + b).oracle in {"symbolic", "numeric"}


# --- Trap 3: refuse to compare mismatched symbols ---------------------------

def test_equivalent_raises_rather_than_returning_a_wrong_verdict():
    """Mismatched assumptions previously reported correct work as wrong."""
    from circuit_mcp.symbols import SymbolConflictError
    import pytest
    lcapy_side = -sp.Symbol("A", positive=True) * sp.Symbol("Rf", positive=True)
    parsed_side = -sp.Symbol("A") * sp.Symbol("Rf")
    with pytest.raises(SymbolConflictError):
        equivalent(lcapy_side, parsed_side)


def test_reconciled_expressions_compare_equivalent():
    from circuit_mcp.symbols import reconcile
    lcapy_side = -sp.Symbol("A", positive=True) * sp.Symbol("Rf", positive=True)
    parsed_side = -sp.Symbol("A") * sp.Symbol("Rf")
    assert equivalent(reconcile(parsed_side, lcapy_side), lcapy_side).equivalent


def test_laplace_variable_assumption_mismatch_is_caught():
    from circuit_mcp.symbols import SymbolConflictError
    import pytest
    with pytest.raises(SymbolConflictError):
        equivalent(1 / (1 + sp.Symbol("s")), 1 / (1 + sp.Symbol("s", positive=True)))


def test_a_sum_with_a_symbolic_bound_is_confirmed_term_by_term():
    """A summation bound is not a continuous symbol; random substitution hangs on it."""
    from circuit_mcp.parsing import parse_expression

    total = parse_expression("sum_n(V/R^i, i, 1, n)")
    closed = parse_expression("V*(1 - R^(-n))/(R - 1)")
    verdict = equivalent(total, closed)
    assert verdict.equivalent is True
    assert "n = 1" in verdict.detail


def test_a_sum_that_disagrees_at_one_n_is_rejected():
    from circuit_mcp.parsing import parse_expression

    total = parse_expression("sum_n(V/R^i, i, 1, n)")
    verdict = equivalent(total, parse_expression("V/R"))
    assert verdict.equivalent is False
