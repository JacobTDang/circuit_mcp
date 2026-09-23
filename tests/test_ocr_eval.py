"""Scoring a recogniser: what counts as better, and what a miss is made of.

One number cannot decide this. A wrong bracket is untidy; a lost minus sign or
a changed subscript makes a circuit derivation wrong, so those are counted
separately and a candidate has to improve exact match without worsening them.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from circuit_mcp.ocr_eval import (
    EvalError, Sample, is_better, load_manifest, normalise, score, score_one,
)


def test_whitespace_and_dollar_wrappers_are_not_what_is_judged():
    assert normalise("$ v_b = v_a $") == normalise("v_b=v_a")


def test_the_real_lab1_miss_is_counted_as_a_subscript_and_a_digit_error():
    """vb = va -> G = 10 came back as u_b = v_a -> v = 0."""
    result = score_one("v_b = v_a \\rightarrow G = 10", "u_b = v_a \\rightarrow v = 0")
    assert result["exact"] is False
    assert result["digit_errors"] == 1       # the 1 of 10 is gone; the 0 survived
    assert result["subscript_errors"] == 0   # both subscripts survived -- the stems did not,
    # and a changed stem (G -> v) is caught only by exact match. See the module docstring.


def test_a_lost_minus_sign_is_a_sign_error():
    result = score_one("-R_f/R_1", "R_f/R_1")
    assert result["sign_errors"] == 1
    assert result["exact"] is False


def test_a_changed_subscript_is_a_subscript_error():
    result = score_one("R_{f}/R_{1}", "R_{f}/R_{2}")
    assert result["subscript_errors"] == 2   # 1 gone, 2 arrived


def test_an_exact_line_scores_no_errors():
    result = score_one("v_o = -10 v_i", "$v_o = -10 v_i$")
    assert result == {"exact": True, "sign_errors": 0, "subscript_errors": 0,
                      "digit_errors": 0, "expected": "v_o=-10v_i", "actual": "v_o=-10v_i"}


def test_a_run_reports_totals_and_every_miss():
    samples = [Sample("a.png", "v_o = -10"), Sample("b.png", "R_1 = 15")]
    result = score(samples, ["v_o = -10", "R_1 = 16"])
    assert result["samples"] == 2 and result["exact"] == 1
    assert result["exact_rate"] == 0.5
    assert [miss["crop"] for miss in result["misses"]] == ["b.png"]


def test_an_empty_eval_set_says_what_it_needs():
    with pytest.raises(EvalError, match="confirmed"):
        score([], [])


def test_a_candidate_must_not_trade_a_sign_for_an_exact_match():
    baseline = {"exact": 40, "sign_errors": 2, "subscript_errors": 3}
    better = {"exact": 45, "sign_errors": 2, "subscript_errors": 2}
    traded = {"exact": 45, "sign_errors": 4, "subscript_errors": 2}
    assert is_better(better, baseline) is True
    assert is_better(traded, baseline) is False


def test_the_committed_manifest_is_loadable(tmp_path):
    manifest = Path("benchmarks/handwriting/manifest.json")
    assert manifest.exists(), "the eval set lives in the repo, even while it is empty"
    assert isinstance(load_manifest(manifest), list)


def test_a_sample_without_a_confirmed_label_is_refused(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"samples": [{"crop": "a.png"}]}))
    with pytest.raises(EvalError, match="confirmed label"):
        load_manifest(path)


def test_the_lab1_eval_set_is_fourteen_confirmed_formula_lines():
    """The set the baseline was scored on, so a later edit cannot quietly shrink it."""
    samples = load_manifest(Path("benchmarks/handwriting/manifest.json"))
    assert len(samples) == 14
    assert all(sample.label.strip() for sample in samples)
    assert all(len(sample.bbox) == 4 for sample in samples), "each crop must be cuttable again"
    assert all("lab1-p3" in sample.crop for sample in samples)


def test_every_label_spells_the_page_rather_than_the_model():
    """The page writes uppercase V by hand; a label that says v would score the model against itself."""
    samples = load_manifest(Path("benchmarks/handwriting/manifest.json"))
    voltages = [sample.label for sample in samples if "V" in sample.label or "v" in sample.label]
    assert voltages, "the page is full of voltages"
    assert not any("v_{" in sample.label for sample in samples)
