"""Symbol binding — guards trap 1 from the spike.

lcapy's symbols carry assumptions, so sympy.Symbol('A') is a different object.
subs() against a bare Symbol silently returns the expression unchanged. A
substitution that quietly does nothing is the exact failure this tool exists to
prevent, so it must raise instead.
"""
import pytest
import sympy as sp

from circuit_mcp.symbols import bind, safe_subs, SubstitutionError


def lcapy_style_expr():
    """An expression whose symbols carry assumptions, as lcapy produces."""
    A, Ri, Rf = sp.symbols("A Ri Rf", positive=True)
    return -A * Rf / (A * Ri + Rf + Ri)


def test_bind_returns_symbols_by_name():
    expr = lcapy_style_expr()
    names = bind(expr)
    assert set(names) == {"A", "Ri", "Rf"}
    assert names["A"].assumptions0["positive"] is True


def test_safe_subs_raises_on_silent_noop():
    """The trap: a bare Symbol does not match an assumption-carrying one."""
    expr = lcapy_style_expr()
    bare = sp.Symbol("A")  # no assumptions -> will not match
    with pytest.raises(SubstitutionError, match="did not change"):
        safe_subs(expr, bare, sp.Integer(5))


def test_safe_subs_succeeds_when_bound_by_name():
    expr = lcapy_style_expr()
    A = bind(expr)["A"]
    out = safe_subs(expr, A, sp.Integer(5))
    assert out != expr
    assert "A" not in {str(x) for x in out.free_symbols}


def test_safe_subs_reports_the_symbol_it_could_not_find():
    expr = lcapy_style_expr()
    with pytest.raises(SubstitutionError, match="Q"):
        safe_subs(expr, sp.Symbol("Q"), sp.Integer(1))


# --- Trap 3: same name, different assumptions -------------------------------
# Two symbols sharing a name but differing in assumptions are distinct objects
# to SymPy. Expressions built from them print identically yet compare as
# unequal, which yields a confident wrong verdict rather than an error.

def test_conflicts_detects_same_name_different_assumptions():
    from circuit_mcp.symbols import conflicts
    bare = sp.Symbol("A") * 2
    assumed = sp.Symbol("A", positive=True) * 2
    found = conflicts(bare, assumed)
    assert "A" in found
    assert len(found["A"]) == 2


def test_conflicts_empty_when_symbols_agree():
    from circuit_mcp.symbols import conflicts
    A = sp.Symbol("A", positive=True)
    assert conflicts(A * 2, A + 1) == {}


def test_assert_no_conflicts_names_the_offender():
    from circuit_mcp.symbols import assert_no_conflicts, SymbolConflictError
    with pytest.raises(SymbolConflictError, match="A"):
        assert_no_conflicts(sp.Symbol("A"), sp.Symbol("A", positive=True))


def test_reconcile_rewrites_onto_the_reference_symbols():
    from circuit_mcp.symbols import reconcile, conflicts
    reference = sp.Symbol("A", positive=True) * sp.Symbol("R", positive=True)
    parsed = sp.Symbol("A") * sp.Symbol("R")
    fixed = reconcile(parsed, reference)
    assert conflicts(fixed, reference) == {}
    assert fixed == reference


def test_reconcile_is_a_noop_when_already_consistent():
    from circuit_mcp.symbols import reconcile
    A = sp.Symbol("A", positive=True)
    assert reconcile(A * 3, A) == A * 3
