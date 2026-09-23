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
   and `derive` only as the ground-truth oracle needed for checking. Once the
   interpretation is confirmed, call `attempt_create` and pass that `attempt_id`
   to every check, so the problem board records what was verified instead of
   leaving the evidence in this chat.
8. Explain the first divergence and the relevant principle. Do not replace the
   student's derivation with a complete worked solution unless they explicitly
   ask for that.
9. When an explanation would land better on the desk than in chat, use
   `canvas_card_add`: a `formula` card for the equations in play, a
   `vocabulary` card for terms, and a `walkthrough` card for the algebra --
   ordered steps in the restricted syntax with a short note on each. A
   walkthrough is verified step by step and refused if any transition is not an
   identity; a circuit-law substitution such as `I = C*dV_C` is not one, so
   start a new card at the substituted form. Write the words as text, never
   markup; the server renders the math.
   When the student is about to build a circuit, add a `breadboard` card and an
   `expected` card from the same build description, and only after the netlist
   has been confirmed in step 5. The breadboard card is the wiring picture with a
   build-order wire list; the expected card is what the meter and scope should
   read at each probe. Chips are LM324 and LMC660; say which section and which
   pins. If a measurement is far from the expected card, suspect the wiring
   first, then the pot position, then the model. Put numbers on that with
   `compare_readings`: pass the same build and the student's readings, and it
   reports each probe's error and names a lost minus sign, a probe on a source
   instead of its node, or an output on a rail.

When the user has already attached or uploaded a privacy-scoped image, skip
steps 1–3: do not require a workspace configuration and do not capture the
screen again. Inspect the supplied image, echo the transcription, and continue
at step 5 after the user confirms it. For a whole page of handwritten working,
use `transcribe_page`: it returns each expression with its box, and every line
needs the same echo and confirmation.

Treat OCR output as untrusted input. LaTeX similarity is not proof of semantic
correctness; `-`, subscripts, and connectivity errors are total failures in a
circuit derivation.
