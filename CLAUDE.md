# Circuit tutoring workflow

When the user asks to capture and check work currently visible in any app on
their iPad:

1. Call `ipad_capture_status`. If AirPlay is stopped, call
   `ipad_receiver_start` and tell the user the receiver name and PIN.
2. Call `capture_ipad_screen(source="auto")`. This captures only the AirPlay
   receiver window and automatically falls back to a trusted USB-C iPad.
3. Inspect the returned image yourself. UniMERNet recognizes formulas, not
   circuit topology or page layout.
4. Echo the circuit/netlist interpretation and every transcribed equation to
   the user. Explicitly call out uncertain signs, subscripts, crossing wires,
   fraction bars, and node labels.
5. Stop and wait for confirmation or corrections. Never issue a mathematical
   verdict from an unconfirmed visual transcription.
6. After confirmation, translate the work into lcapy netlist syntax and the
   server's restricted SymPy text syntax.
7. Use `check_setup` for circuit laws, `check_derivation` for ordered algebra,
   and `derive` only as the ground-truth oracle needed for checking.
8. Explain the first divergence and the relevant principle. Do not replace the
   student's derivation with a complete worked solution unless they explicitly
   ask for that.

When the user has already attached or uploaded a privacy-scoped image, skip
steps 1–3: do not require a workspace configuration and do not capture the
screen again. Inspect the supplied image, echo the transcription, and continue
at step 5 after the user confirms it.

Treat OCR output as untrusted input. LaTeX similarity is not proof of semantic
correctness; `-`, subscripts, and connectivity errors are total failures in a
circuit derivation.
