# Showman integration scope

Showman is the sole production animation renderer. The former browser SVG renderer is removed.
The existing `animation_scenes` rows remain recoverable until migration is complete.

## Runtime boundary

- Pin Showman at `vendor/showman` as a Git submodule.
- `circuit_mcp` owns a subprocess manager that starts `npm start` on `127.0.0.1:2301`.
- Never expose port 2301 to the LAN. Browser traffic goes through authenticated, bounded FastAPI routes.
- Rendering works offline. `OPENROUTER_API_KEY` is optional, inherited from the process environment,
  never returned by status endpoints, logged, committed, or stored in SQLite.
- Detect missing Node 20+, missing build output, worker crashes, timeouts, and incompatible Showman revisions.

## Product flow

1. An agent submits an EE 2300 brief or an explicit Showman Scene Spec.
2. The harness validates course/problem ownership and forwards authoring or assembly to Showman.
3. A job row records status, progress, spec hash, source problem, output references, and errors.
4. The board manually spawns a visual card containing a poster preview or local MP4 player.
5. The student can play, pause, seek, change speed, expand, replay, or delete the local artifact.

## API and MCP surface

- `GET /api/showman/status`
- `POST /api/showman/author`
- `POST /api/showman/preview`
- `POST /api/showman/render`
- `GET /api/showman/jobs/{id}`
- `GET /api/showman/objects/{id}` with range-request support
- MCP tools: `visual_author`, `visual_preview`, `visual_render`, `visual_status`, `visual_get`

All request bodies have explicit size limits. Object paths and upstream URLs never pass through from callers.

## Persistence and migration

Add `visual_jobs` and `visual_artifacts`; do not overload the legacy scene table. Migrate a legacy row only
when it can be translated safely, otherwise retain it as archived JSON. After one release with successful
migration, remove legacy CRUD routes, MCP tools, schema/templates, and finally the old table in a versioned migration.

## Delivery phases

1. Worker manager, health checks, restart backoff, build/version diagnostics.
2. Safe HTTP proxy plus SQLite jobs/artifacts and content-addressed output caching.
3. MCP author/preview/render/status tools with course-aware prompts and local-only defaults.
4. Manually spawned draggable video cards with poster, progress, failure, and retry states.
5. Legacy-data migration and removal of old animation backend code.
6. E2E tests: offline rendering, OpenRouter authoring, seeking, range requests, cancellation, crash recovery,
   malformed specs, oversized bodies, missing dependencies, secret redaction, and reduced motion.

## Acceptance gates

- A circuit brief creates a valid Showman spec through the real MCP transport.
- The worker renders a deterministic preview and MP4 locally.
- The board embeds the result without loading remote assets.
- Current flow is visibly continuous at 30/60 fps and seeking is frame-correct.
- Circuit topology and equations are reviewed independently of animation quality.
- Worker failure cannot crash the MCP/UI process or expose secrets.
