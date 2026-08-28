"""Spike: does lcapy keep an s-dependent opamp gain symbolic?

Gates the design. Ideal opamps (A -> oo) are assumed fine; the open question is
finite gain-bandwidth, A(s) = A0 / (1 + s/wp), needed in circuit analysis.
"""
import warnings
warnings.filterwarnings("ignore", category=SyntaxWarning)

from lcapy import Circuit, s, symbol
import sympy as sp

def show(label, fn):
    print(f"\n--- {label} ---")
    try:
        print(fn())
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")

# Inverting amp: source -> Ri -> inverting node -> Rf -> output.
def inverting(gain):
    return Circuit(f"""
Vs 1 0 {{V}}
Ri 1 2 {{Ri}}
Rf 2 3 {{Rf}}
E1 3 0 opamp 0 2 {{{gain}}}
""")

# 1. finite constant gain
show("finite constant gain A", lambda: inverting("A").transfer(1, 0, 3, 0))

# 2. ideal: take the limit A -> oo
def ideal():
    H = inverting("A").transfer(1, 0, 3, 0)
    A = symbol("A")
    return sp.limit(H.sympy, A.sympy, sp.oo)
show("ideal (limit A -> oo)  [expect -Rf/Ri]", ideal)

# 3. THE QUESTION: s-dependent gain
def sdep():
    H = inverting("A0 / (1 + s / wp)").transfer(1, 0, 3, 0)
    return H.simplify()
show("s-dependent A(s) = A0/(1+s/wp)", sdep)

# 4. does it stay analysable? poles of the s-dependent case
def poles():
    H = inverting("A0 / (1 + s / wp)").transfer(1, 0, 3, 0)
    return H.poles()
show("poles of the s-dependent result", poles)

# 5. equivalence primitives the checker depends on
def equiv():
    H = inverting("A0 / (1 + s / wp)").transfer(1, 0, 3, 0)
    expr = H.sympy
    # exact srepr round-trip across a serialization boundary
    rt = sp.sympify(sp.srepr(expr))
    exact = sp.simplify(rt - expr) == 0
    # random numeric substitution as the second oracle
    syms = sorted(expr.free_symbols, key=str)
    subs = {sym: sp.Rational(i + 3, i + 7) for i, sym in enumerate(syms)}
    numeric = sp.N(expr.subs(subs) - rt.subs(subs))
    return f"srepr round-trip exact: {exact}\nnumeric delta: {numeric}\nfree symbols: {syms}"
show("equivalence primitives (srepr + numeric)", equiv)
