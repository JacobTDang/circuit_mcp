"""Seeded, independently checked circuit-analysis stress matrix.

This complements unit fixtures with new numeric values on every seed. It calls
the same guarded functions exposed over MCP and exits nonzero on any mismatch.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from fractions import Fraction
from typing import Any

from circuit_mcp.server import (
    alias_frequency,
    bjt_emitter_follower,
    characterize_transfer,
    check_derivation,
    check_equivalence,
    converter_metrics,
    dac_output,
    opamp_limits,
    rectifier_metrics,
    relaxation_oscillator,
    simulate_spice,
    transimpedance,
)


def close(actual: float, expected: float, tolerance: float = 1e-7) -> bool:
    return math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance)


def run(seed: int, rounds: int) -> dict[str, Any]:
    rng = random.Random(seed)
    failures: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    def record(family: str, ok: bool, detail: Any) -> None:
        counts[family] = counts.get(family, 0) + 1
        if not ok:
            failures.append({"family": family, "case": counts[family], "detail": detail})

    for _ in range(rounds):
        # RC transfer identity, plus a deliberate sign error.
        r_units, c_units = rng.randint(1, 100), rng.randint(1, 100)
        r = r_units * 100
        c = float(f"{c_units}e-8")
        # The identity needs wc bound symbolically, which check_equivalence can
        # express by substituting its definition directly into the second side.
        good = check_equivalence("1/(1+s*R*C)", "(1/(R*C))/(s+1/(R*C))")
        record("equivalence_good", good.get("equivalent") is True, good)
        bad = check_equivalence("1/(1+s*R*C)", "1/(1-s*R*C)")
        record("equivalence_bad", bad.get("equivalent") is False, bad)

        # Parameterized symbolic-to-numeric derivation.
        tau = Fraction(r_units * 100 * c_units, 100_000_000)
        inverse_tau = 1 / tau
        truth = "1/(1+s*R*C)"
        tau_text = f"{tau.numerator}/{tau.denominator}"
        inverse_text = f"{inverse_tau.numerator}/{inverse_tau.denominator}"
        steps = [truth, f"1/(1+s*({tau_text}))", f"({inverse_text})/(s+({inverse_text}))"]
        checked = check_derivation(steps, truth, {"R": r, "C": c})
        record("derivation", checked.get("ok") is True, checked)

        # Stable, unstable, and marginal transfer classification.
        pole = rng.choice([-1, 0, 1]) * rng.randint(1, 20)
        characterized = characterize_transfer(f"1/(s-({pole}))")
        expected_class = (
            "asymptotically_stable" if pole < 0
            else "marginally_stable" if pole == 0
            else "unstable"
        )
        record(
            "stability",
            characterized.get("stability_classification") == expected_class,
            characterized,
        )

        # ADC transitions: either ideal or one deliberate duplicate.
        bits = rng.choice([2, 3, 4])
        levels = 1 << bits
        transitions = [index / levels for index in range(1, levels)]
        duplicate = rng.choice([False, True])
        duplicate_code = None
        if duplicate:
            duplicate_code = rng.randrange(1, len(transitions))
            transitions[duplicate_code] = transitions[duplicate_code - 1]
        metrics = converter_metrics("adc", bits, transitions, 0, 1)
        expected_missing = [] if duplicate_code is None else [duplicate_code]
        record(
            "converter",
            metrics.get("missing_codes") == expected_missing
            and metrics.get("strictly_increasing") is (not duplicate),
            metrics,
        )

        # Op-amp bandwidth and slew decisions around independently calculated limits.
        noise_gain = rng.randint(1, 20)
        gbw = rng.randint(1, 20) * 1e5
        peak = rng.randint(1, 20) / 2
        frequency = rng.randint(1, 50) * 1e3
        required = 2 * math.pi * frequency * peak
        slew = required * rng.choice([0.8, 1.0, 1.2])
        limits = opamp_limits(-noise_gain, noise_gain, gbw, slew, peak, frequency)
        record(
            "opamp_limits",
            limits.get("bandwidth_limited") is (frequency > gbw / noise_gain)
            and limits.get("slew_limited") is (required > slew),
            limits,
        )

        # Remaining closed-form course families.
        peak = rng.randint(1, 20)
        drop = rng.choice([0.0, 0.2, 0.7])
        rectified = rectifier_metrics(peak, drop)
        angle = math.asin(drop / peak)
        expected_dc = (2 * peak * math.cos(angle) - drop * (math.pi - 2 * angle)) / (2 * math.pi)
        record("rectifier", close(rectified.get("average_dc_v", math.nan), expected_dc), rectified)

        current = rng.randint(1, 20) * 1e-4
        beta = rng.randint(50, 300)
        thermal = rng.choice([0.025, 0.02585])
        load = rng.randint(1, 20) * 100.0
        follower = bjt_emitter_follower(current, beta, thermal, load)
        gm = current / thermal
        r_pi = beta / gm
        expected_gain = (beta + 1) * load / (r_pi + (beta + 1) * load)
        record(
            "bjt_follower",
            close(follower.get("gm_s", math.nan), gm)
            and close(follower.get("r_pi_ohm", math.nan), r_pi)
            and close(follower.get("voltage_gain", math.nan), expected_gain),
            follower,
        )

        rail = rng.randint(2, 20)
        threshold = rng.uniform(0.1, rail - 0.1)
        rc = rng.randint(1, 100) * 1e-5
        oscillator = relaxation_oscillator(rail, threshold, rc)
        expected_period = 2 * rc * math.log((rail + threshold) / (rail - threshold))
        record("oscillator", close(oscillator.get("period_s", math.nan), expected_period), oscillator)

        dac_bits = rng.choice([2, 3, 4, 8])
        maximum = (1 << dac_bits) - 1
        codes = [rng.randint(0, maximum) for _ in range(4)]
        span = rng.randint(1, 20)
        dac = dac_output(codes, dac_bits, 0, span)
        expected_outputs = [code * span / (1 << dac_bits) for code in codes]
        record("dac_output", all(close(a, b) for a, b in zip(dac.get("outputs_v", []), expected_outputs)), dac)

        sample_rate = rng.randint(1, 100) * 100.0
        input_frequency = rng.uniform(0, 10 * sample_rate)
        aliased = alias_frequency(input_frequency, sample_rate)
        remainder = input_frequency % sample_rate
        expected_alias = min(remainder, sample_rate - remainder)
        record("alias", close(aliased.get("alias_hz", math.nan), expected_alias), aliased)

        input_current = rng.uniform(-0.01, 0.01)
        feedback = rng.randint(1, 100) * 1000.0
        tia = transimpedance(input_current, feedback)
        record("transimpedance", close(tia.get("output_v", math.nan), -input_current * feedback), tia)

    # A smaller SPICE set keeps runtime bounded while varying real netlists.
    for _ in range(max(1, rounds // 5)):
        supply = rng.randint(1, 24)
        r1, r2 = rng.randint(1, 100), rng.randint(1, 100)
        expected = supply * r2 / (r1 + r2)
        result = simulate_spice(
            f"V1 in 0 {supply}\nR1 in out {r1}k\nR2 out 0 {r2}k",
            "op",
            ["v(out)"],
        )
        actual = result.get("points", [{}])[0].get("v(out)")
        record("spice_divider", isinstance(actual, (int, float)) and close(actual, expected), result)

    total = sum(counts.values())
    return {
        "ok": not failures,
        "seed": seed,
        "rounds": rounds,
        "total": total,
        "passed": total - len(failures),
        "failed": len(failures),
        "families": counts,
        "failures": failures[:25],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=407)
    parser.add_argument("--rounds", type=int, default=50)
    args = parser.parse_args()
    result = run(args.seed, args.rounds)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
