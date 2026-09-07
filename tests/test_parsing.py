"""The string boundary: untrusted text -> SymPy, without an interpreter escape.

Two things are being tested here, and they pull in opposite directions.

*Correctness*: an expression that survives parsing must mean what the student
wrote. Getting this subtly wrong is worse than rejecting the input outright.

*Safety*: ``sympify`` evaluates its argument, and ``parse_expr`` with a
restricted ``global_dict`` is **not** sufficient on its own -- the escape below
is real and was verified against this SymPy version. So the adversarial tests
assert both that ``ParseError`` is raised and that nothing was executed.
"""
import os

import pytest
import sympy as sp

from circuit_mcp.equivalence import equivalent
from circuit_mcp.parsing import ParseError, parse_equation, parse_expression
from circuit_mcp.symbols import bind


def lcapy_style_expr():
    """An expression whose symbols carry assumptions, as lcapy produces."""
    A, Ri, Rf = sp.symbols("A Ri Rf", positive=True)
    return -A * Rf / (A * Ri + Rf + Ri)


# --- ordinary expressions ---------------------------------------------------

def test_simple_expression():
    a, b = sp.symbols("a b")
    assert parse_expression("a + b") == a + b


def test_rational_function_in_s():
    """The shape this project actually produces -- finite-GBW inverting amp."""
    A0, Rf, Ri, s, wp = sp.symbols("A0 Rf Ri s wp")
    parsed = parse_expression("-A0*Rf/(A0*Ri + (Rf+Ri)*(1+s/wp))")
    assert equivalent(parsed, -A0 * Rf / (A0 * Ri + (Rf + Ri) * (1 + s / wp))).equivalent


def test_result_is_a_sympy_expression():
    assert isinstance(parse_expression("R*C*s"), sp.Expr)


def test_caret_is_exponentiation_not_xor():
    """Students write ``s^2``. In this domain ``^`` is never bitwise xor."""
    s, w0 = sp.symbols("s w0")
    assert parse_expression("s^2 + w0^2") == s**2 + w0**2


def test_implicit_multiplication():
    C, R, s = sp.symbols("C R s")
    assert parse_expression("1/(1 + s R C)") == 1 / (1 + s * R * C)


def test_implicit_multiplication_with_a_leading_coefficient():
    R = sp.Symbol("R")
    assert parse_expression("2R") == 2 * R


def test_whitespace_is_irrelevant():
    Rf, Ri = sp.symbols("Rf Ri")
    assert parse_expression("   Rf  /  Ri   ") == Rf / Ri


def test_underscored_names_survive():
    """``v_o``/``R_f`` are how the course writes subscripts."""
    assert {str(x) for x in parse_expression("v_o/v_i").free_symbols} == {"v_o", "v_i"}


def test_decimals_become_exact_rationals():
    """Float dust makes the symbolic oracle indecisive; keep the input exact."""
    R = sp.Symbol("R")
    parsed = parse_expression("0.5*R")
    assert parsed == R / 2
    assert not parsed.atoms(sp.Float)


def test_allowed_functions_are_available():
    assert parse_expression("sqrt(2)") == sp.sqrt(2)


def test_parenthesised_factors_multiply():
    a, b, c, d = sp.symbols("a b c d")
    assert parse_expression("(a+b)(c+d)") == (a + b) * (c + d)
    assert parse_expression("2(R+C)") == 2 * sp.Symbol("R") + 2 * sp.Symbol("C")


def test_a_name_that_collides_with_a_sympy_function_stays_a_symbol():
    """``beta`` and ``gamma`` are ordinary EE symbols, not special functions."""
    parsed = parse_expression("beta/(beta+1)")
    assert parsed.free_symbols == {sp.Symbol("beta")}
    assert not parsed.atoms(sp.Function)


def test_I_and_E_keep_their_sympy_meanings():
    """A documented collision: EE writes currents as I. SymPy's I wins here,
    because ground truth comes from lcapy and disagreeing with it would report
    correct work as wrong. Pinned so any change is a deliberate one."""
    assert parse_expression("I*w*L") == sp.I * sp.Symbol("w") * sp.Symbol("L")
    assert parse_expression("E") is sp.E


def test_a_parsed_s_carries_no_assumptions():
    """Trap 2: a positive ``s`` makes pole solving return [] instead of erroring."""
    s = next(iter(parse_expression("1/(s + 1)").free_symbols))
    assert str(s) == "s"
    assert s.assumptions0.get("positive") is None
    assert s.assumptions0.get("negative") is None


# --- the symbols parameter (trap 1) -----------------------------------------

def test_supplied_symbols_are_used_not_recreated():
    """The whole point: a fresh Symbol('A') never matches lcapy's positive A.

    Asserted by equality-with-assumptions rather than ``is``. SymPy caches
    expression construction on argument *equality*, so the object inside the
    result is not guaranteed to be the one handed in -- see
    ``test_supplied_symbols_survive_a_cleared_sympy_cache``. Equality including
    assumptions is the property that actually governs subs() and comparison.
    """
    A = sp.Symbol("A", positive=True)
    parsed = parse_expression("2*A", symbols={"A": A})
    got = next(iter(parsed.free_symbols))
    assert got == A
    assert sp.srepr(got) == sp.srepr(A)
    assert got.assumptions0["positive"] is True


def test_without_supplied_symbols_the_name_is_bare():
    """Documents the failure the symbols parameter exists to prevent."""
    A = sp.Symbol("A", positive=True)
    got = next(iter(parse_expression("2*A").free_symbols))
    assert got != A
    assert got.assumptions0.get("positive") is None


def test_supplied_symbols_survive_a_cleared_sympy_cache():
    """SymPy's construction cache is keyed on equality, not identity.

    ``Integer(2)*A`` can therefore return a cached Mul built from a different
    but equal ``A``. Clearing the cache makes that observable -- and importing
    lcapy clears it, so this really happens in this test suite. The guard in
    parse_expression must survive it, which is why it compares by equality.
    """
    A_before = sp.Symbol("A", positive=True)
    parse_expression("2*A", symbols={"A": A_before})

    sp.core.cache.clear_cache()
    A_after = sp.Symbol("A", positive=True)
    assert A_after is not A_before  # the cache really was cleared
    assert A_after == A_before

    got = next(iter(parse_expression("2*A", symbols={"A": A_after}).free_symbols))
    assert got == A_after
    assert got.assumptions0 == A_after.assumptions0


def test_symbols_bound_from_an_lcapy_expression_round_trip():
    """The real call site: bind() the ground truth, parse the student's string."""
    truth = lcapy_style_expr()
    parsed = parse_expression("-A*Rf/(A*Ri + Rf + Ri)", symbols=bind(truth))
    assert parsed == truth
    assert equivalent(parsed, truth).equivalent


def test_parsing_against_lcapy_ground_truth_needs_the_supplied_symbols():
    """The integration this parameter exists for.

    lcapy's own ``s`` carries assumptions, so a freshly parsed bare ``s`` is a
    different object. ``equivalent`` refuses to compare the two rather than
    return a verdict, which is the loud version of the silent wrong answer.
    Supplying the symbols is what makes the comparison possible at all.
    """
    import lcapy
    from circuit_mcp.symbols import SymbolConflictError

    R, C = sp.symbols("R C", positive=True)
    truth = 1 / (1 + lcapy.s.sympy * R * C)

    with pytest.raises(SymbolConflictError):
        equivalent(parse_expression("1/(1 + s*R*C)"), truth)

    parsed = parse_expression("1/(1 + s*R*C)", symbols=bind(truth))
    assert equivalent(parsed, truth).equivalent


def test_supplied_symbols_dict_is_not_mutated():
    """SymPy's auto_symbol writes into local_dict; the caller's map must survive."""
    A = sp.Symbol("A", positive=True)
    supplied = {"A": A}
    parse_expression("A + Rf + Ri", symbols=supplied)
    assert supplied == {"A": A}


def test_a_symbol_whose_name_disagrees_with_its_key_is_rejected():
    """Silently binding 'A' to Symbol('B') is exactly the wrong-answer failure."""
    with pytest.raises(ParseError, match="name"):
        parse_expression("A", symbols={"A": sp.Symbol("B", positive=True)})


def test_symbols_not_mentioned_in_the_text_are_simply_unused():
    A, Rf = sp.symbols("A Rf", positive=True)
    parsed = parse_expression("2*A", symbols={"A": A, "Rf": Rf})
    assert parsed.free_symbols == {A}
    assert Rf not in parsed.free_symbols


# --- adversarial ------------------------------------------------------------
#
# These payloads are live. `test_the_payload_is_genuinely_dangerous` proves it
# by running one through sympify and watching it write a file, so the rejection
# tests below are demonstrably not checking against an inert string.

def _write_payload(path: str) -> str:
    return "sqrt.__globals__['__builtins__']['open'](%r, 'w').write('pwned')" % path


def test_the_payload_is_genuinely_dangerous(tmp_path):
    """Baseline: sympify executes this. That is why this module exists."""
    target = str(tmp_path / "pwned.txt")
    try:
        sp.sympify(_write_payload(target))
    except Exception:  # noqa: BLE001 - the side effect is what is under test
        pass
    assert os.path.exists(target), "payload no longer works; pick a live one"


def test_the_payload_is_rejected_and_not_executed(tmp_path):
    target = str(tmp_path / "pwned.txt")
    with pytest.raises(ParseError):
        parse_expression(_write_payload(target))
    assert not os.path.exists(target)


def test_class_hierarchy_escape_is_rejected():
    """__subclasses__() reaches every class in the process, builtins included."""
    with pytest.raises(ParseError, match="__"):
        parse_expression("().__class__.__base__.__subclasses__()")


def test_function_globals_escape_is_rejected():
    """sqrt.__globals__ hands back a module dict holding the real __builtins__."""
    with pytest.raises(ParseError, match="__"):
        parse_expression("sqrt.__globals__")


def test_unicode_dunder_lookalike_is_rejected():
    """NFKC folds U+FF3F to '_', so a substring check for '__' alone is bypassable."""
    payload = "sqrt._＿globals_＿"
    assert "__" not in payload  # the naive guard would pass this straight through
    with pytest.raises(ParseError):
        parse_expression(payload)


def test_attribute_access_is_rejected():
    with pytest.raises(ParseError, match="[Aa]ttribute"):
        parse_expression("os.system")


def test_import_is_rejected():
    with pytest.raises(ParseError, match="import"):
        parse_expression("import os")


def test_lambda_is_rejected():
    with pytest.raises(ParseError, match="lambda"):
        parse_expression("lambda: 1")


def test_eval_is_rejected():
    with pytest.raises(ParseError, match="eval"):
        parse_expression("eval(1)")


def test_exec_is_rejected():
    with pytest.raises(ParseError, match="exec"):
        parse_expression("exec(1)")


def test_comprehension_keywords_are_rejected():
    with pytest.raises(ParseError):
        parse_expression("[x for x in (1,2)]")


def test_string_literals_are_rejected():
    with pytest.raises(ParseError):
        parse_expression("'abc'")


def test_statement_separator_is_rejected():
    with pytest.raises(ParseError):
        parse_expression("a; b")


def test_rejection_says_what_and_why():
    with pytest.raises(ParseError) as excinfo:
        parse_expression("x.__class__")
    message = str(excinfo.value)
    assert "__" in message
    assert "interpreter" in message.lower() or "escape" in message.lower()


def test_absurdly_long_input_is_rejected():
    with pytest.raises(ParseError, match="long"):
        parse_expression("a+" * 100_000 + "b")


# --- malformed input --------------------------------------------------------

def test_parse_error_is_a_value_error():
    """Callers upstream catch ValueError; ParseError must fall under it."""
    assert issubclass(ParseError, ValueError)


def test_unbalanced_parentheses_raise_parse_error():
    """A bare SymPy TokenError must not leak across this boundary."""
    with pytest.raises(ParseError):
        parse_expression("1/(1 + s")


def test_empty_string_raises_parse_error():
    with pytest.raises(ParseError, match="empty"):
        parse_expression("   ")


def test_non_string_input_raises_parse_error():
    with pytest.raises(ParseError):
        parse_expression(None)


def test_unknown_function_is_rejected():
    """SymPy's implicit multiplication reads ``A(s)`` as ``A*s``.

    That silently turns "gain as a function of s" into a product, which would
    make the checker confidently wrong. Refuse instead of guessing.
    """
    with pytest.raises(ParseError, match="A") as excinfo:
        parse_expression("A(s)")
    assert "*" in str(excinfo.value)


def test_a_symbol_is_not_callable_even_when_supplied():
    A = sp.Symbol("A", positive=True)
    with pytest.raises(ParseError, match="A"):
        parse_expression("A(s)", symbols={"A": A})


def test_supplied_laplace_variable_stays_bare():
    """Trap 2 again, this time through the symbols parameter."""
    s = sp.Symbol("s")
    parsed = parse_expression("1/(1 + s*R*C)", symbols={"s": s})
    got = next(x for x in parsed.free_symbols if str(x) == "s")
    assert got == s
    assert got.assumptions0.get("positive") is None


def test_trailing_operator_raises_parse_error():
    with pytest.raises(ParseError):
        parse_expression("R*C*")


def test_equals_is_not_an_expression():
    with pytest.raises(ParseError, match="="):
        parse_expression("a = b")


# --- equations --------------------------------------------------------------

def test_parse_equation_splits_lhs_and_rhs():
    Rf, Ri, vo, vi = sp.symbols("Rf Ri vo vi")
    eq = parse_equation("vo/vi = -Rf/Ri")
    assert isinstance(eq, sp.Eq)
    assert eq.lhs == vo / vi
    assert eq.rhs == -Rf / Ri


def test_parse_equation_supports_the_same_notation_as_expressions():
    s, w0, H = sp.symbols("s w0 H")
    eq = parse_equation("H = 1/(1 + s^2/w0^2)")
    assert eq.rhs == 1 / (1 + s**2 / w0**2)
    assert eq.lhs == H


def test_parse_equation_honours_supplied_symbols():
    A = sp.Symbol("A", positive=True)
    eq = parse_equation("H = 2*A", symbols={"A": A})
    got = next(s for s in eq.rhs.free_symbols if str(s) == "A")
    assert got == A
    assert got.assumptions0["positive"] is True


def test_parse_equation_rejects_no_equals():
    with pytest.raises(ParseError, match="="):
        parse_equation("no equals here")


def test_parse_equation_rejects_multiple_equals():
    with pytest.raises(ParseError, match="="):
        parse_equation("a = b = c")


def test_parse_equation_rejects_double_equals():
    """'==' is Python comparison, not an equation; be explicit rather than clever."""
    with pytest.raises(ParseError, match="="):
        parse_equation("a == b")


def test_parse_equation_rejects_an_empty_side():
    with pytest.raises(ParseError):
        parse_equation("a =")


def test_parse_equation_rejects_an_injection_attempt(tmp_path):
    target = str(tmp_path / "pwned.txt")
    with pytest.raises(ParseError):
        parse_equation("y = " + _write_payload(target))
    assert not os.path.exists(target)


# --- display-only parse: the tree keeps the written order --------------------

def test_parse_as_written_keeps_factor_and_term_order():
    from circuit_mcp.parsing import parse_as_written

    tree = parse_as_written("R*C")
    assert [str(a) for a in tree.args] == ["R", "C"]
    tree = parse_as_written("x^2 - 2*x + 1")
    assert [str(a) for a in tree.args][0] == "x**2"
    assert str(tree.args[-1]) == "1"


def test_parse_as_written_runs_the_same_screen_as_the_checker():
    from circuit_mcp.parsing import ParseError, parse_as_written

    with pytest.raises(ParseError):
        parse_as_written("R.__class__")
    with pytest.raises(ParseError):
        parse_as_written("")
