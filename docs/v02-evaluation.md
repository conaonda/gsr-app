# v0.2 first bundle evaluation

## Implemented scope

The first bundle from the design discussion covers PR-01 through PR-04: coordinate eligibility and bounded computation, non-persisting preview, source editing, and inverse-projected overlays. The existing SQLite schema and point observations remain readable. A subsequent improvement adds the pitch reference, source-checked caches and exact-PTS seeking described in [the improvement plan](improvement-plan.md). A persistent decoder, line/arc observation contracts and player tracking remain follow-ups.

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

## Real-video evaluation

The local file `20260717_153525.mp4` was tested on 2026-09-09. It is a
2336×1080 video of about 176 seconds. The original file and the normal user
session were not modified; the test used an isolated SQLite database.

Measured browser waits were 73.315 seconds for import and the first frame,
1.913 seconds for frame 150, 8.007 seconds for frame 2700, 15.010 seconds for
frame 5100, and 0.980 seconds for an adjacent frame. These measurements are
machine-specific and are not release performance guarantees.

The editor successfully loaded the real frames, accepted point input, zoomed
and dragged a point, undid the drag, restored a frame draft, and performed a
non-persisting preview. With only two provisional points, the server correctly
returned `unavailable` with image-level coordinates and the reasons
`insufficient_fit_points` and `missing_validation_points`; no real-field
accuracy claim was made. The footage often shows only part of the pitch while
the camera moves, so four known, well-distributed correspondences could not be
established from this run.

Detailed timings, screenshots, and the exact test limitations are recorded in
the local ignored artifact `data/real-video-qa/RESULTS.md`.

Use three repeatable tasks on selected frames: create an initial registration, correct a deliberately misplaced point, and visit adjacent frames then return. Record human interaction time separately from system waiting time, correction count, abandoned registrations and independent validation error. Real pitch coordinates must come from independent field evidence; the estimated transform cannot serve as its own ground truth.

The draft targets of cached-frame switching within 300 ms and preview within 1 s are not performance guarantees. The subsequent cache/seek change measured 0.121–0.135 seconds for returning to a cached frame and 0.884–0.893 seconds for selected uncached frames on this clip. First import was 16.830 seconds. Frames 150, 2700 and 5100 matched sequential decoding pixel-for-pixel. See [the before/after measurements and remaining work](improvement-plan.md) for the test scope; this does not establish real-field registration accuracy.
