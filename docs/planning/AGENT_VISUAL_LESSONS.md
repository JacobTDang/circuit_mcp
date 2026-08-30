# Agent-spawned visual lessons

## Outcome

An MCP client such as Claude or Codex can turn one teaching request into one or more
independent video canvases on the local workspace. Each canvas teaches a focused idea,
appears automatically when ready, and remains movable, resizable, replayable, and
regenerable. The browser is a view of persisted server state rather than the owner of
agent-created visuals.

## Current state

- The browser can manually spawn multiple `visual` cards, but stores them only in
  `localStorage`.
- `/api/showman/generate` produces one MP4 and the browser embeds it only when that
  browser initiated the request.
- The MCP server exposes the legacy declarative `animation_*` tools, not Showman video
  generation or workspace placement.
- Showman's worker has atomic brief-to-video generation. Its orchestrator can plan
  multiple scenes, but the command center does not consume a lesson/chapter manifest.
- The legacy animation scene schema and Showman's scene specification are separate
  contracts. They must not be silently mixed.

## Product contract

### A lesson is not one dense video

The main agent submits a lesson with:

- a title and teaching goal;
- learner level and assumed prerequisites;
- one or more focused canvas briefs;
- required concepts, equations, values, and visual checks;
- optional links to a local problem or document;
- a requested depth (`quick`, `standard`, or `deep`).

For `standard` depth, the default lesson decomposition is:

1. intuition and physical behavior;
2. mathematical model or derivation;
3. timeline, graph, or state evolution;
4. worked numerical example;
5. optional misconception/check canvas.

Each canvas should teach one claim in 20–45 seconds. The main agent may explicitly
request fewer or more canvases.

### Persisted entities

Add two database entities rather than extending browser-local state:

`visual_lessons`

- `id`, `title`, `goal`, `depth`, `problem_id`, `document_id`;
- `status` (`queued`, `rendering`, `partial`, `ready`, `failed`);
- `created_at`, `updated_at`, `actor`, and bounded error metadata.

`visual_canvases`

- `id`, `lesson_id`, `position`, `title`, `brief`;
- `status` (`queued`, `authoring`, `rendering`, `ready`, `failed`);
- Showman object key, localized video URL, captions key, duration, dimensions, fps;
- authored-spec/provenance reference, attempt count, and bounded error metadata;
- workspace `x`, `y`, `width`, and `z` with server defaults that the browser may update;
- revision and timestamps for synchronization.

Store object keys, not `file://` URLs. Continue serving objects through
`/api/showman/objects/{key}`.

## APIs

### MCP tools

`visual_lesson_create(...)`

- Accepts the lesson contract and a list of canvas briefs.
- Persists all canvases before rendering so the browser can show progress immediately.
- Returns lesson/canvas IDs without waiting for every MP4.

`visual_canvas_create(...)`

- Spawns one additional independent canvas in an existing or new lesson.

`visual_lesson_status(lesson_id)` and `visual_canvas_status(canvas_id)`

- Return bounded status, metadata, and errors.

`visual_canvas_regenerate(canvas_id, revised_brief)`

- Creates a new revision without losing the last playable video until replacement is
  ready.

`visual_canvas_remove(canvas_id)`

- Soft-deletes the workspace card; object retention remains a separate policy.

Keep granular Showman validation/preview tools available for agents that author specs
directly, but make lesson creation the normal tutoring path.

### Web API

- `GET /api/visual-lessons?updated_after=...`
- `POST /api/visual-lessons`
- `GET /api/visual-canvases?updated_after=...`
- `PATCH /api/visual-canvases/{id}/layout`
- `POST /api/visual-canvases/{id}/regenerate`
- `DELETE /api/visual-canvases/{id}`

Use monotonically increasing revisions or update cursors. Polling every 1–2 seconds is
adequate for localhost v1; server-sent events can replace it later without changing the
entity model.

## Browser behavior

- Merge server-owned visual canvases with manually spawned local components.
- Create a card as soon as a queued canvas appears; show explicit authoring and rendering
  phases.
- Replace the progress surface with `<video controls playsinline>` when ready.
- Preserve the previous video during regeneration and swap only after the new object is
  playable.
- Allow any number of visual cards within a bounded workspace maximum. Cascade initial
  placement and avoid exact overlap.
- Persist drag/resize changes through the layout endpoint with a debounced write.
- Never duplicate a card after refresh: identity is the server canvas ID.
- Make failures actionable: show retry, the failing phase, and a concise message while
  keeping the original brief.

## Pedagogical quality gate

Before a video is published as `ready`, verify:

- the authored spec contains the required labels/numeric values from `must_show`;
- title/topic similarity clears a deterministic lexical floor;
- a sampled frame is non-blank;
- MP4 metadata, captions, and duration are readable;
- duration is within the requested canvas range;
- circuit lessons contain a circuit visual plus at least one explanatory change, graph,
  equation, or state transition—not only a static schematic.

If semantic checks fail, retry authoring with explicit feedback. After the retry budget,
mark the canvas failed or use a deterministic topic-specific lesson builder. Never publish
an unrelated but technically valid render.

## Delivery phases

1. **Persistence and contracts** — migrations, repository methods, Pydantic inputs,
   lifecycle tests, and migration rollback coverage.
2. **Browser synchronization** — server cards, polling cursor, layout persistence,
   multiple simultaneous cards, reload/idempotency tests.
3. **MCP surface** — create/status/regenerate/remove tools with an injected render worker;
   protocol tests prove Claude/Codex can spawn multiple canvases.
4. **Render worker** — bounded queue, per-canvas isolation, cancellation, restart recovery,
   and object localization.
5. **Teaching planner** — depth templates and main-agent supplied briefs; do not delegate
   course correctness blindly to the helper model.
6. **Quality gate and fallbacks** — semantic checks, actual frame probes, deterministic
   circuit fallback, and clear failure states.
7. **End-to-end verification** — two concurrent lessons, five canvases, one failed render,
   browser reload during rendering, server restart, regeneration, and deletion.

## Acceptance scenario

An agent requests a deep RC charging lesson. Within two seconds, four queued cards appear:
intuition, derivation, time-constant graph, and worked example. They render independently;
completed cards become playable immediately. One simulated provider failure does not block
the other three. Refreshing the browser retains every card and its layout. The agent adds a
fifth misconception canvas through MCP, and it appears without user interaction.

## Boundary with Showman

The command center owns lesson intent, persistence, workspace placement, retries across
canvases, and student-facing status. Showman owns authoring and rendering a pedagogically
coherent canvas or lesson asset. Upstream deficiencies discovered during this plan are
captured in `vendor/showman/docs/planning/COMMAND_CENTER_INTEGRATION_ISSUES.md`.
