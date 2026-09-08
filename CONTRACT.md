# GSR P0 implementation contract

Local-first Python FastAPI + vanilla HTML/CSS/JS, served at 127.0.0.1:8765. No external video transmission. Backend `gsr/server.py`, geometry `gsr/geometry.py`, frontend `web/index.html`, `web/app.js`, `web/style.css`.

API JSON unless stated:
- GET /api/videos -> array video records {id,name,width,height,duration,time_base}
- POST /api/videos {path} -> video record. Local absolute path, ffprobe actual PTS indexing.
- GET /api/videos/{id}/frames -> array {index,pts,time_seconds}
- GET /api/videos/{id}/frame/{index} -> PNG
- GET /api/videos/{id}/registrations -> array saved registration results
- POST /api/videos/{id}/registrations {frame_index,field:{length:null,width:null,dimension_source:"",dimensions_verified:false},points:[{image:[x,y],pitch:[x,y],role:"fit"|"validation"}],note:""} -> saved result
- GET /api/videos/{id}/export -> JSON full session export
- POST /api/videos/{id}/propagate {registration_id,target_frame_index} -> saved result (conservative ground ROI optical flow; may reject; never auto-validates)

Pitch points always normalized 0..1; image coordinates native displayed frame pixels. Field dimensions optional and never assumed verified. Server passes points and field to geometry. Geometry API `estimate_registration(points, field, image_size)` returns dict {matrix:3x3 image-to-normalized-pitch or null,status:"usable"|"review"|"unavailable",coordinate_level:"metric"|"normalized"|"image",reasons:[],validation_error_px:null|number,fit_error_px:null|number,valid_region:[[x,y]],projected_lines:[[[x,y],[x,y]]],sensitivity:null|number}. Fit >=4 distributed nondegenerate points, independent validation required for usable. Matrix kept even when review, coordinate_level image when not usable. Reject nonfinite input and ill-conditioned geometry. `project_point(matrix, point)` helper.

Saved result adds {id,video_id,frame_index,pts,time_seconds,points,field,note,created_at,source:"manual"|"propagated"}. Append-only SQLite. Errors {detail:string} with non-2xx. No fake analysis or default real data. UI Korean, polished diagnostic workstation, genuine empty state and loading/error handling. Frame fetch responds actual dimensions after rotation; indexing must be consistent. P0 only, automatic player tracking and P1-P3 explicitly future.

Final coordinate convention: `matrix` always maps native image pixels to normalized pitch coordinates. `valid_region` and `projected_lines` use normalized pitch coordinates; the UI applies the inverse matrix for source-frame overlays. Metric eligibility never changes matrix units; multiply normalized X/Y by independently verified length/width for meters. Only usable results grant coordinate eligibility; review/unavailable remains image-only. A polygon marks supported observations, not guaranteed accuracy at every enclosed point.

Frame records also expose `pts_source`: original_pts / best_effort / missing (legacy records may be unknown). Results include `analysis_provenance` with engine, engine_version, config_version, schema_version. Session export includes a schema version. Propagation requests are limited to 30 adjacent frames and always require fresh validation.
