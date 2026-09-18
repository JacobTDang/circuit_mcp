# Bench-check tools: compare_readings, summing_dac_output, transcribe_page

Issues #51, #52, #53. Found while writing up EE 2300 Lab 1: every serious error
in that report was a bench or transcription mistake that a tool could have caught
while the circuit was still built.

## compare_readings (#51)

`compare_readings(build, measured, tolerance_pct=5)` in `compare.py`, exposed as an
MCP tool. It is a tool, not a field on the `expected` card: it returns a verdict
like `check_setup` and `check_derivation`, and the card stays a display.

- `build` is the same description an `expected` card takes; `expectations(build)`
  supplies the prediction.
- `measured` maps probe labels to readings. A bare number is volts for a DC probe
  and V RMS for an AC probe (the scope's AC RMS measurement). `{"vpp": x}` or
  `{"vpk": x}` gives an AC reading in that basis.
- Each probe returns expected, measured, `error_pct`, `within_tolerance`. Measured
  gains (both ends measured, converted to peak) are checked against predicted gains.
- An expected value within 1 mV of zero has no meaningful percent error; it is
  judged with an absolute 0.05 V band and `error_pct` is null.
- Out-of-tolerance readings carry a `hint`: `sign` (DC reading ≈ −expected),
  `source` (reading equals a source's setting and the probe is not on that
  source's node), `rail` (an op-amp output at the chip's swing limit).
- An unknown probe label, a malformed reading, or an unsimulatable build raises
  `CompareError`, mapped to the `compare_error` kind. Nothing is silently dropped.
- Out of scope: a "gain off by a resistor ratio" hint (too speculative).

## summing_dac_output (#52)

`summing_dac_output(codes, bits, r_feedback, r_bits, v_logic=1.0, tolerance_pct=5)`
in `course_metrics.py`, exposed as an MCP tool. This replaces the ticket's lean
toward an `expected`-card sweep: the ±5 % check needs an ideal, which only the DAC
arithmetic knows, and the Lab 1 DAC cannot clip (−7 V on ±10 V rails).

- Inverting summing amplifier, MSB first: `vo = −v_logic · Σ (Rf/Rᵢ)·bᵢ`.
- Ideal is `−v_logic · code`. Each code returns output, ideal, `error_pct`
  (null for code 0), `within_tolerance`.
- `dac_output` is unchanged; its description points op-amp summing DACs to the
  new tool, since the old description is how the wrong tool got used.

## transcribe_page (#53)

`transcribe_page(image_base64)` MCP tool.

- `page_segment.py`: numpy-only segmentation. Ink is anything far from the median
  background, so light and dark pages both work. Rows of ink form lines; lines
  closer than half the median line height merge (a fraction stays whole);
  horizontal gaps wider than three line heights (or 8 % of the page width) split
  side columns into separate boxes. Boxes come back in reading order: top to
  bottom, then left to right.
- Segmentation runs inside the OCR worker, which already decodes with PIL. No new
  dependencies; the server does not rely on its transitive Pillow. The module is
  imported both as `circuit_mcp.page_segment` and, from the worker script, as
  `page_segment`, so it uses no relative imports.
- At most 60 expressions per page; the result reports the true count and
  `truncated`.
- Output stays untrusted: every line is echoed and confirmed before any
  `check_derivation` verdict.
- The OCR model cannot run in CI, so the Lab 1 Exp 6 end-to-end check is a manual
  run.

## Delivery

One branch per issue off `main`, in order #51 → #52 → #53 (all three register tools
in `server.py`). Tests first, full suite green, PR `Closes #N`, merge, next branch
from the updated `main`. The project CLAUDE.md gains one line for `compare_readings`
and one for `transcribe_page` so the workflow actually uses them.
