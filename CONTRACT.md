# GSR P0 implementation contract

Local-first Python FastAPI + vanilla HTML/CSS/JS, served at 127.0.0.1:8765. No external video transmission. Backend `gsr/server.py`, geometry `gsr/geometry.py`, frontend `web/index.html`, `web/app.js`, `web/style.css`.

API JSON unless stated:
- GET /api/videos -> array video records {id,name,width,height,duration,time_base}
- POST /api/videos {path} -> video record. Local absolute path, ffprobe actual PTS indexing.
- GET /api/videos/{id}/frames -> array {index,pts,time_seconds}
- GET /api/videos/{id}/frame/{index} -> PNG
- GET /api/videos/{id}/registrations -> array saved registration results
- POST /api/videos/{id}/registrations {frame_index,field:{length:null,width:null,dimension_source:"",dimensions_verified:false},points:[{image:[x,y],pitch:[x,y],role:"fit"|"validation"}],note:""} -> saved result
- POST /api/videos/{id}/registrations/preview accepts the same input plus required nonnegative integer `draft_version`. It validates and calculates through the same service as save, returns a result with `source:"preview"`, `persisted:false`, video/frame/PTS context and echoed `draft_version`, and creates no database rows or registration ID. Save recalculates from observations; client-supplied matrices/status are rejected.
- GET /api/videos/{id}/export -> JSON full session export
- POST /api/videos/{id}/propagate {registration_id,target_frame_index} -> saved result (conservative ground ROI optical flow; may reject; never auto-validates)

Pitch points always normalized 0..1; image coordinates native displayed frame pixels. Field dimensions optional and never assumed verified. Server passes points and field to geometry. Geometry API `estimate_registration(points, field, image_size)` returns dict {matrix:3x3 image-to-normalized-pitch or null,status:"usable"|"review"|"unavailable",coordinate_level:"metric"|"normalized"|"image",reasons:[],validation_error_px:null|number,fit_error_px:null|number,valid_region:[[x,y]],projected_lines:[[[x,y],[x,y]]],sensitivity:null|number}. Fit >=4 distributed nondegenerate points, independent validation required for usable. Matrix kept even when review, coordinate_level image when not usable. Reject nonfinite input and ill-conditioned geometry. `project_point(matrix, point)` helper.

Saved result adds {id,video_id,frame_index,pts,time_seconds,points,field,note,created_at,source:"manual"|"propagated"}. Append-only SQLite. Errors {detail:string} with non-2xx. No fake analysis or default real data. UI Korean, polished diagnostic workstation, genuine empty state and loading/error handling. Frame fetch responds actual dimensions after rotation; indexing must be consistent. P0 only, automatic player tracking and P1-P3 explicitly future.

Final coordinate convention: `matrix` always maps native image pixels to normalized pitch coordinates. `valid_region` and `projected_lines` use normalized pitch coordinates; the UI applies the inverse matrix for source-frame overlays. Metric eligibility never changes matrix units; multiply normalized X/Y by independently verified length/width for meters. Only usable results grant coordinate eligibility; review/unavailable remains image-only. A polygon marks supported observations, not guaranteed accuracy at every enclosed point.

Frame records also expose `pts_source`: original_pts / best_effort / missing (legacy records may be unknown). Results include `analysis_provenance` with engine, engine_version, config_version, schema_version. Session export includes a schema version. Propagation requests are limited to 30 adjacent frames and always require fresh validation.

## v0.2 editor and compatibility

Video records expose opaque `source_revision` derived from persisted source file identity. In-memory draft keys include video ID, this revision and frame index. A draft has separate editing history and version; frame changes save/restore that frame's draft. Browser restart persistence requires an explicit registration save. Zoom and pan belong to the view, never to the stored image/pitch observations.

Preview responses apply only when video ID, frame index and draft version still match the active editor. Editing any point/role/field invalidates current calculation eligibility. Selected saved snapshots and fresh draft previews remain distinct; pending responses cannot overwrite later edits or another frame. Source overlays use `inverse(matrix)` followed by the viewport transform. Independent validation mode hides predictive overlays.

The geometry and server boundaries both enforce `status != usable => coordinate_level == image`. Historical responses receive an `eligibility_correction` object when required: `{field:"coordinate_level",original_value,corrected_value:"image",policy_version:"gsr.coordinate-eligibility.v2"}`. This is a read-time policy correction, not a reassessment of geometry; the original analysis JSON and historical engine provenance remain unchanged. The retained matrix is for inspection and overlays, not permission to use a rejected result for spatial analysis.

RANSAC hypotheses remain deterministic and capped at 512; the combinatorial iterator is consumed lazily. Four-point DLT retains the underdetermined null-space basis; larger systems use reduced SVD. Quality thresholds are unchanged.
