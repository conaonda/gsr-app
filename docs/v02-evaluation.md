# v0.2 first bundle evaluation

## Implemented scope

The first bundle from the design discussion covers PR-01 through PR-04: coordinate eligibility and bounded computation, non-persisting preview, source editing, and inverse-projected overlays. The existing SQLite schema and point observations remain readable. Media caching/continuous decoding (PR-05), line/arc observation contracts and player tracking remain follow-ups.

## Automated evidence

`python tests/run_checks.py` starts a temporary database and loopback server and runs the Python, Node and Chromium checks. The fixtures are generated textured pitch imagery, variable-frame-rate test patterns and rotated synthetic video. None is real match footage.

Required checks include:

- Review/unavailable results cannot grant normalized or metric eligibility, including legacy read/export paths.
- Repeated previews produce no saved registrations; preview/save use the same observations and calculation.
- Large point sets do not enumerate every four-point combination.
- Viewport transforms round-trip source pixels across zoom, pan, resize and 1:1 mode.
- Canvas point entry and drag use original image coordinates at DPR 2; one undo reverses a drag.
- Current-session frame drafts restore edited points and field settings.
- Old preview responses do not replace a newer draft; saved observations remain unchanged.
- Pitch lines/support/residuals affect original-frame pixels and can be toggled.
- Mobile layout does not overflow horizontally.

On 2026-09-09 the isolated Windows run passed 20 Python tests, 11 Node
editor-core tests, JavaScript syntax validation and the Chromium flow at
DPR 2. The browser flow covered actual source/pitch clicks, no-write
preview, save, zoomed dragging, undo/redo, frame draft restoration, delayed
responses after edits/frame navigation/history selection, independent
validation overlay suppression and a 390-pixel-wide mobile layout. Desktop
and mobile screenshots were inspected. These results concern functional
correctness on generated fixtures, not measured real-match accuracy.

## Real-video evaluation still needed

The discussion refers to `20260717_153525.mp4`; it was not included among the attachments accessible to this task and was not found in the scoped project, Downloads or Videos folders. A local path is needed to run the real-video evaluation. Do not treat synthetic test success as measured accuracy on that recording.

Use three repeatable tasks on selected frames: create an initial registration, correct a deliberately misplaced point, and visit adjacent frames then return. Record human interaction time separately from system waiting time, correction count, abandoned registrations and independent validation error. Real pitch coordinates must come from independent field evidence; the estimated transform cannot serve as its own ground truth.

The draft targets of cached-frame switching within 300 ms and preview within 1 s are not performance guarantees. PR-05 caching is not in this bundle. Establish baseline/new measurements on the same local clip before confirming release-level usability or accuracy claims.
