# v0.2 first development bundle

Scope: reference discussion PR-01 through PR-04: invariant fixes, real non-persisting preview, usable source editor, overlay/async integration. PR-05 media caching/continuous decode and v0.3 line/arc fitting remain follow-ups. Existing P0 APIs and stored observations remain readable.

Follow-up status (2026-09-09): source-checked frame caching, exact-PTS seeking,
index reuse and a named pitch-reference input are now implemented. The original
first-bundle scope and ownership below are historical. See
[the improvement plan](docs/improvement-plan.md) for evidence and remaining
continuous-decoder/line-arc work.

## Shared interface

- `POST /api/videos/{video_id}/registrations/preview` accepts existing registration input plus `draft_version` (nonnegative integer). Returns geometry fields plus `video_id`, `frame_index`, `pts`, `pts_source`, `time_seconds`, `draft_version`, `source:"preview"`, `persisted:false`, `field`, `points`, `analysis_provenance`; no database registration ID and no writes. Same validation/calculation as saving; existing save request stays compatible.
- Video records add opaque `source_revision` derived from persisted source identity. Frontend draft key is video ID + source revision + frame index.
- `review` and `unavailable` always have `coordinate_level:"image"`. Old persisted results receive a read-time eligibility correction with original value and policy version, without overwriting historical SQLite analysis.
- `matrix`: image pixels -> normalized pitch. Overlay inverse projects `projected_lines`/`valid_region` from pitch -> image. Validation residual connects observed image point to inverse-projected pitch observation. Draw all through viewport transform; do not modify source data for zoom/pan.
- `web/editor-core.js` exposes global `window.GSREditor` AND CommonJS exports for dependency-free Node tests. API: `Viewport` constructor; `fit(imageWidth,imageHeight,viewWidth,viewHeight)`; `resize(viewWidth,viewHeight)`; `toImage({x,y})`; `toView({x,y})`; `zoomAt(factor,{x,y})`; `pan(dx,dy)`; `oneToOne()`; numeric `scale`, `offsetX`, `offsetY`. Canvas uses CSS pixel view size and DPR rendering. Fit records source/view dimensions. 1:1 means one source pixel per CSS pixel. `project(matrix,[x,y]) -> [x,y]|null`; `invert(matrix)->matrix|null`.
- `DraftStore`: `load(key)->snapshot|null`, `save(key,snapshot)`, `clear()`. Deep copies. `History`: `reset(snapshot)`, `commit(snapshot)`, `undo()->snapshot|null`, `redo()->snapshot|null`, getters `canUndo/canRedo`. Bounded capacity (default100), commit only once per completed drag; identical snapshots do not add entries. UI manages per-frame histories/draft versions and previews.

## Ownership

Backend agent: gsr/geometry.py, gsr/server.py, new gsr/registration_service.py as useful, tests/test_geometry.py, tests/test_api.py. Fix bounded hypotheses + coordinate eligibility; add preview and immutable historical correction tests; add source_revision.
Editor-core agent: web/editor-core.js, tests/editor-core.test.cjs only. Pure viewport/projection/history/draft algorithms plus tests for nonaffine homographies, dimensions/DPI-independent coordinates, history bounds/isolation.
Frontend agent: web/app.js, web/index.html, web/style.css only. Integrate editor-core and API, source-first layout, zoom/pan/fit/1:1/loupe, select/add-fit/add-validation/pan tools, drag/nudge/undo/redo, frame drafts, real preview with strict async version keys, source overlays with toggles, independent validation mode hides predictive overlay. Saved result and draft-preview state must be distinct. Reuse current data/API; no invented line fitting.
Root: integration tests, docs, final review, local real-video availability, commit/push. All agents share files: do not revert others. No Sol Advisor.
