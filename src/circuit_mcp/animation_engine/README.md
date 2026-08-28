# Animation engine

This package owns agent-created, hand-drawn visual explanations. It is kept
separate from the mathematical solvers: solver results may become scene data,
but animation data never participates in a mathematical verdict.

## Product contract

An MCP client submits bounded JSON, not HTML, SVG, CSS, or JavaScript. The
server validates and stores it. The browser renders it as deterministic,
seeded Rough.js SVG in a draggable board card.

A scene can coordinate four layers:

1. **Physical intuition** — charge, current, energy, signal flow, switching.
2. **Circuit model** — components, nodes, probes, operating states.
3. **Mathematics** — equations, substitutions, cancellation, term movement.
4. **Evidence** — time plots, Bode plots, spectra, transfer curves, readings.

## Package boundaries

```text
animation_engine/
├── schema.py          bounded scene validation and normalization
├── primitives.py      supported visual vocabulary (next)
├── templates/         legacy scene builders pending migration
├── README.md          architecture and implementation plan
└── COVERAGE.md        course coverage acceptance matrix

static/animation/      vendored Rough.js and browser renderer (next)
storage.py             scene/revision/problem-link persistence (next)
server.py              MCP animation tools (next)
web.py                 board polling and scene API (next)
```

## Scene model

Every scene has a stable ID after persistence, schema version, title, canvas
size, deterministic seed, duration, elements, timeline steps, and optional
problem link. Elements use semantic types such as `resistor`, `equation`,
`phasor`, or `plot`; arbitrary SVG paths are not part of the first release.

Timeline steps change declared properties of existing elements and attach a
caption. The renderer interpolates numeric properties. Structural changes
happen only at step boundaries. Playback must support pause, replay, scrubbing,
chapter navigation, 0.5x/1x/2x speed, and reduced-motion mode.

## Security and resource limits

- Reject unknown fields and primitive types.
- Reject markup, URLs, event handlers, and executable expressions.
- At most 128 elements, 128 steps, and 512 property changes per scene.
- Canvas dimensions: 320–1920 by 240–1080.
- Duration: 250 ms–10 minutes.
- Text fields are plain text and individually bounded.
- Colors come from a small theme palette or validated hex values.
- Coordinates and numeric animation values must be finite.
- Roughness uses a stored seed, making screenshots and tests reproducible.

## Implementation sequence

1. Finalize schema and adversarial validation tests.
2. Vendor and integrity-pin Rough.js; do not depend on a runtime CDN.
3. Implement the SVG primitive renderer and hand-drawn theme.
4. Implement timeline interpolation and accessible playback controls.
5. Add SQLite scenes, revisions, and problem links.
6. Add MCP create/update/get/list/delete and template tools.
7. Poll for new MCP scenes and auto-spawn them on the open board.
8. Add course templates in the order listed in `COVERAGE.md`.
9. Run transport, persistence, browser, timing, and visual regression tests.

## Acceptance rule

“Covered” means a representative scene is schema-valid, created through the
real MCP transport, persisted, auto-spawned, rendered in a real browser, and
its important states are checked at start/middle/end. A unit test alone does
not mark a course topic covered.
