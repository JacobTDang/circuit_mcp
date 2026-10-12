"""Two-oracle equivalence: symbolic for the proof, numeric for the safety net."""
import pytest
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


# --- sign- and phase-blind functions: positive trials cannot see them ---------

def _parsed(text_a, text_b, symbols=None):
    from circuit_mcp.parsing import parse_expression
    from circuit_mcp.symbols import bind

    left = parse_expression(text_a, symbols)
    return left, parse_expression(text_b, {**(symbols or {}), **bind(left)})


@pytest.mark.parametrize("text_a, text_b", [
    ("Abs(Vo)", "Vo"),
    ("re(Z)", "Z"),
    ("conjugate(H)", "H"),
    ("im(Z)", "0"),
    ("arg(Vo)", "0"),
    ("sign(Vo)", "1"),
    ("Max(Vo, 0)", "Vo"),
    ("Heaviside(Vo)", "1"),
    ("Abs(1/(1 + s*R*C))", "1/(1 + s*R*C)"),
    ("Abs(H)^2", "H^2"),
    ("sqrt(Vo^2)", "Vo"),
    ("sqrt(R^2*C^2*w^2)", "R*C*w"),
])
def test_a_step_that_holds_only_at_positive_values_is_not_equivalent(text_a, text_b):
    """Every one of these agrees at every positive value and was certified equal."""
    left, right = _parsed(text_a, text_b)
    verdict = equivalent(left, right)
    assert verdict.equivalent is False
    assert verdict.counterexample is not None
    assert sp.simplify(left.subs(verdict.counterexample) - right.subs(verdict.counterexample)) != 0


def test_a_dropped_magnitude_is_caught_at_a_negative_value():
    left, right = _parsed("Abs(Vo)", "Vo")
    verdict = equivalent(left, right)
    assert verdict.counterexample[sp.Symbol("Vo")] < 0
    assert "negative" in verdict.detail


def test_a_dropped_real_part_is_caught_at_a_complex_value_and_says_so():
    left, right = _parsed("re(Z)", "Z")
    verdict = equivalent(left, right)
    assert verdict.counterexample[sp.Symbol("Z")].as_real_imag()[1] != 0
    assert "every real value" in verdict.detail
    assert "I" not in verdict.detail


def test_a_magnitude_of_a_positive_symbol_is_still_proved():
    """lcapy's component symbols carry positive=True; Abs(R1) is R1."""
    R1 = sp.Symbol("R1", positive=True)
    assert equivalent(sp.Abs(R1), R1).equivalent
    assert equivalent(sp.sqrt(R1**2), R1).equivalent


def test_a_positive_symbol_beside_a_signed_one_keeps_positive_values():
    R1 = sp.Symbol("R1", positive=True)
    left, right = _parsed("Abs(Vo)*R1", "Vo*R1", {"R1": R1})
    verdict = equivalent(left, right)
    assert verdict.equivalent is False
    assert verdict.counterexample[R1] > 0
    assert verdict.counterexample[sp.Symbol("Vo")] < 0


@pytest.mark.parametrize("text_a, text_b", [
    # A phasor written with j: the parts beside j are real quantities.
    ("Abs(1/(1 + j*w*R*C))", "1/sqrt(1 + (w*R*C)^2)"),
    ("re(1/(1 + j*w*R*C))", "1/(1 + (w*R*C)^2)"),
    ("arg(1/(1 + j*w*R*C))", "-atan(w*R*C)"),
    ("Abs(R + 1/(j*w*C))", "sqrt(R^2 + 1/(w*C)^2)"),
    ("re(R + 1/(j*w*C))", "R"),
    ("im(R + 1/(j*w*C))", "-1/(w*C)"),
    ("conjugate(1/(1 + j*w*R*C))", "1/(1 - j*w*R*C)"),
    # Identities for every complex value.
    ("Abs(Z)^2", "Z*conjugate(Z)"),
    ("Abs(Z)", "sqrt(re(Z)^2 + im(Z)^2)"),
    ("conjugate(conjugate(H))", "H"),
    # For every real value; Max takes no complex argument, so a and b are real.
    ("Max(a, b)", "(a + b + Abs(a - b))/2"),
    # A square root split needs L and C positive, and nothing here can make
    # them negative: only V sits inside Abs.
    ("Abs(V)*sqrt(L*C)", "Abs(V)*sqrt(L)*sqrt(C)"),
    ("Abs(V)*log(R1*R2)", "Abs(V)*(log(R1) + log(R2))"),
])
def test_a_genuine_identity_with_a_sign_or_phase_function_still_holds(text_a, text_b):
    left, right = _parsed(text_a, text_b)
    verdict = equivalent(left, right)
    assert verdict.equivalent is True, verdict.detail


@pytest.mark.parametrize("text_a, text_b", [
    ("sqrt(L*C)", "sqrt(L)*sqrt(C)"),
    ("log(R1*R2)", "log(R1) + log(R2)"),
    ("1/sqrt(L*C)", "1/(sqrt(L)*sqrt(C))"),
])
def test_a_root_or_log_split_of_component_values_still_holds(text_a, text_b):
    """These need positive values, which is what a circuit's L, C and R are."""
    left, right = _parsed(text_a, text_b)
    assert equivalent(left, right).equivalent is True


def test_a_check_that_cannot_evaluate_off_the_positive_values_is_inconclusive():
    """At every negative x the left side divides by zero; that is not agreement."""
    x = sp.Symbol("x", real=True)
    verdict = equivalent(x / (x + sp.Abs(x)), sp.Rational(1, 2))
    assert verdict.equivalent is False
    assert verdict.counterexample is None
    assert "inconclusive" in verdict.detail
