"""FastAPI service for the local GSR frame-registration workstation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Mapping
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt
from typing_extensions import Literal

from .media import (
    FrameInfo,
    MediaError,
    VideoInfo,
    current_file_identity,
    extract_frame,
    probe_video,
    propagate_ground_points,
)

try:
    from .geometry import estimate_registration
except ImportError:  # pragma: no cover - a clear error is raised on use
    estimate_registration = None  # type: ignore[assignment]


# Results are persisted with explicit provenance so a later export can be
# interpreted after the local implementation changes.  These values describe
# the deterministic P0 engine/configuration, not a claim about model training
# or player tracking.
ANALYSIS_PROVENANCE = {
    "engine": "gsr.geometry",
    "engine_version": "0.2.0",
    "config_version": "p0-v2",
    "schema_version": "gsr.registration.v1",
}
SESSION_EXPORT_SCHEMA_VERSION = "gsr.session.v1"
MAX_PROPAGATION_STEPS = 30
COORDINATE_ELIGIBILITY_POLICY_VERSION = "gsr.coordinate-eligibility.v2"


class VideoPathRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)


class FieldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    length: StrictFloat | None = Field(default=None, gt=0)
    width: StrictFloat | None = Field(default=None, gt=0)
    dimension_source: str = Field(default="", max_length=1000)
    dimensions_verified: StrictBool = False


class PointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: list[StrictFloat]
    pitch: list[StrictFloat]
    role: Literal["fit", "validation"]


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frame_index: StrictInt = Field(ge=0)
    field: FieldRequest
    points: list[PointRequest] = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=10_000)


class PreviewRegistrationRequest(RegistrationRequest):
    """A registration draft with a required client-side revision token."""

    draft_version: StrictInt = Field(ge=0)


class PropagationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registration_id: StrictInt = Field(ge=1)
    target_frame_index: StrictInt = Field(ge=0)


@dataclass(frozen=True)
class StoredVideo:
    id: int
    info: VideoInfo


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _json_safe(value: Any) -> Any:
    """Convert numpy/scalar geometry output into strict JSON values."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    if hasattr(value, "tolist"):
        return _json_safe(value.tolist())
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _source_revision(video: StoredVideo) -> str:
    """Return an opaque, stable revision for the persisted source identity.

    The database deliberately stores only the inexpensive source identity
    (size and nanosecond mtime).  Hashing those values keeps the identity
    useful to the browser's draft key without exposing a local path or making
    the frontend depend on the representation of the identity fields.
    """

    identity = f"{video.info.file_size}:{video.info.file_mtime_ns}".encode("ascii")
    return hashlib.sha256(identity).hexdigest()


def _video_response(video: StoredVideo) -> dict[str, Any]:
    return {
        "id": video.id,
        "name": video.info.name,
        "width": video.info.width,
        "height": video.info.height,
        "duration": video.info.duration,
        "time_base": video.info.time_base,
        "source_revision": _source_revision(video),
    }


def _frame_response(frame: FrameInfo) -> dict[str, Any]:
    return frame.as_dict()


class SessionStore:
    """Small append-only SQLite store for videos and registration results."""

    def __init__(self, db_path: str | Path):
        memory_database = str(db_path) == ":memory:"
        path = Path(db_path)
        if not memory_database:
            if not path.is_absolute():
                path = path.resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._memory_connection: sqlite3.Connection | None = None
        if memory_database:
            self._memory_connection = sqlite3.connect(":memory:", timeout=10.0, check_same_thread=False)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            connection = self._memory_connection
        else:
            connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    duration REAL,
                    time_base TEXT NOT NULL,
                    rotation INTEGER NOT NULL DEFAULT 0,
                    frames_json TEXT NOT NULL,
                    source_size INTEGER NOT NULL,
                    source_mtime_ns INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS videos_path_idx ON videos(path, id);
                CREATE TABLE IF NOT EXISTS registrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id INTEGER NOT NULL REFERENCES videos(id),
                    frame_index INTEGER NOT NULL,
                    pts INTEGER,
                    pts_source TEXT NOT NULL DEFAULT 'unknown',
                    time_seconds REAL,
                    points_json TEXT NOT NULL,
                    field_json TEXT NOT NULL,
                    note TEXT NOT NULL,
                    source TEXT NOT NULL CHECK(source IN ('manual', 'propagated')),
                    analysis_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS registrations_video_idx
                    ON registrations(video_id, id);
                """
            )
            # Sessions created by an early P0 build may already have the
            # append-only table.  Add the provenance column in place and mark
            # pre-provenance rows explicitly unknown rather than claiming
            # their timestamps were original packet PTS values.
            registration_columns = {
                str(column[1]) for column in connection.execute("PRAGMA table_info(registrations)").fetchall()
            }
            if "pts_source" not in registration_columns:
                try:
                    connection.execute(
                        "ALTER TABLE registrations ADD COLUMN pts_source TEXT NOT NULL DEFAULT 'unknown'"
                    )
                except sqlite3.OperationalError as exc:
                    # Another app instance may have completed the same
                    # idempotent migration between PRAGMA and ALTER.
                    if "duplicate column name" not in str(exc).lower():
                        raise

    @staticmethod
    def _frames_from_json(value: str) -> tuple[FrameInfo, ...]:
        try:
            records = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MediaError("Stored video frame index is corrupt") from exc
        if not isinstance(records, list) or not records:
            raise MediaError("Stored video frame index is empty")
        frames: list[FrameInfo] = []
        for expected_index, record in enumerate(records):
            if not isinstance(record, Mapping):
                raise MediaError("Stored video frame index is corrupt")
            try:
                index = int(record.get("index"))
            except (TypeError, ValueError):
                raise MediaError("Stored video frame index is corrupt") from None
            if index != expected_index:
                raise MediaError("Stored video frame index is corrupt")
            pts = record.get("pts")
            if pts is not None:
                try:
                    pts = int(pts)
                except (TypeError, ValueError):
                    raise MediaError("Stored video frame PTS is corrupt") from None
            seconds = record.get("time_seconds")
            if seconds is not None:
                try:
                    seconds = float(seconds)
                except (TypeError, ValueError):
                    raise MediaError("Stored video frame time is corrupt") from None
                if not math.isfinite(seconds):
                    raise MediaError("Stored video frame time is corrupt")
            # Historical frame indexes predate this field; preserve that
            # uncertainty rather than inferring original packet PTS from a
            # non-null integer alone.
            pts_source = str(record.get("pts_source") or "unknown")
            if pts_source not in {"original_pts", "best_effort", "missing", "unknown"}:
                raise MediaError("Stored video PTS provenance is corrupt")
            frames.append(FrameInfo(index=index, pts=pts, time_seconds=seconds, pts_source=pts_source))
        return tuple(frames)

    @staticmethod
    def _info_from_row(row: sqlite3.Row) -> StoredVideo:
        frames = SessionStore._frames_from_json(str(row["frames_json"]))
        info = VideoInfo(
            path=Path(str(row["path"])),
            name=str(row["name"]),
            width=int(row["width"]),
            height=int(row["height"]),
            duration=float(row["duration"]) if row["duration"] is not None else None,
            time_base=str(row["time_base"]),
            frames=frames,
            rotation=int(row["rotation"] or 0),
            file_size=int(row["source_size"]),
            file_mtime_ns=int(row["source_mtime_ns"]),
        )
        return StoredVideo(id=int(row["id"]), info=info)

    def add_video(self, info: VideoInfo) -> StoredVideo:
        frames_json = _json_dump([frame.as_dict() for frame in info.frames])
        with self._lock, self._connect() as connection:
            # Reusing an unchanged source is convenient for the UI while a
            # changed source gets a new immutable video record.
            row = connection.execute(
                "SELECT * FROM videos WHERE path = ? ORDER BY id DESC LIMIT 1",
                (str(info.path),),
            ).fetchone()
            if row is not None and int(row["source_size"]) == info.file_size and int(row["source_mtime_ns"]) == info.file_mtime_ns:
                return self._info_from_row(row)
            cursor = connection.execute(
                """
                INSERT INTO videos
                    (path, name, width, height, duration, time_base, rotation,
                     frames_json, source_size, source_mtime_ns, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(info.path),
                    info.name,
                    info.width,
                    info.height,
                    info.duration,
                    info.time_base,
                    info.rotation,
                    frames_json,
                    info.file_size,
                    info.file_mtime_ns,
                    _utc_now(),
                ),
            )
            row = connection.execute("SELECT * FROM videos WHERE id = ?", (cursor.lastrowid,)).fetchone()
            if row is None:  # pragma: no cover - SQLite guarantees this
                raise MediaError("Could not persist video metadata")
            return self._info_from_row(row)

    def list_videos(self) -> list[StoredVideo]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT * FROM videos ORDER BY id").fetchall()
        return [self._info_from_row(row) for row in rows]

    def get_video(self, video_id: int) -> StoredVideo | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return self._info_from_row(row) if row is not None else None

    def assert_current(self, video: StoredVideo) -> None:
        try:
            size, mtime_ns = current_file_identity(video.info.path)
        except MediaError as exc:
            raise HTTPException(status_code=409, detail="The indexed video is no longer available") from exc
        if size != video.info.file_size or mtime_ns != video.info.file_mtime_ns:
            raise HTTPException(
                status_code=409,
                detail="The video file changed after it was indexed; add it again before continuing",
            )

    def get_frame(self, video: StoredVideo, frame_index: int) -> FrameInfo:
        if isinstance(frame_index, bool) or not isinstance(frame_index, int):
            raise HTTPException(status_code=422, detail="Frame index must be an integer")
        if frame_index < 0 or frame_index >= len(video.info.frames):
            raise HTTPException(status_code=404, detail="Frame not found")
        return video.info.frames[frame_index]

    def add_registration(
        self,
        video_id: int,
        frame: FrameInfo,
        points: list[dict[str, Any]],
        field: dict[str, Any],
        note: str,
        source: str,
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        created_at = _utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO registrations
                    (video_id, frame_index, pts, time_seconds, points_json,
                     pts_source, field_json, note, source, analysis_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    frame.index,
                    frame.pts,
                    frame.time_seconds,
                    _json_dump(points),
                    frame.pts_source,
                    _json_dump(field),
                    note,
                    source,
                    _json_dump(analysis),
                    created_at,
                ),
            )
            registration_id = int(cursor.lastrowid)
        return _registration_response(
            registration_id,
            video_id,
            frame,
            points,
            field,
            note,
            source,
            analysis,
            created_at,
        )

    @staticmethod
    def _registration_from_row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            points = json.loads(str(row["points_json"]))
            field = json.loads(str(row["field_json"]))
            analysis = json.loads(str(row["analysis_json"]))
        except json.JSONDecodeError as exc:
            raise MediaError("Stored registration is corrupt") from exc
        frame = FrameInfo(
            index=int(row["frame_index"]),
            pts=int(row["pts"]) if row["pts"] is not None else None,
            time_seconds=float(row["time_seconds"]) if row["time_seconds"] is not None else None,
            pts_source=str(row["pts_source"] or "unknown"),
        )
        return _registration_response(
            int(row["id"]),
            int(row["video_id"]),
            frame,
            points,
            field,
            str(row["note"]),
            str(row["source"]),
            _correct_historical_analysis(analysis),
            str(row["created_at"]),
        )

    def get_registration(self, registration_id: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM registrations WHERE id = ?", (registration_id,)).fetchone()
        return self._registration_from_row(row) if row is not None else None

    def list_registrations(self, video_id: int) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM registrations WHERE video_id = ? ORDER BY id",
                (video_id,),
            ).fetchall()
        return [self._registration_from_row(row) for row in rows]


def _registration_response(
    registration_id: int,
    video_id: int,
    frame: FrameInfo,
    points: Any,
    field: Any,
    note: str,
    source: str,
    analysis: Mapping[str, Any],
    created_at: str,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "id": registration_id,
        "video_id": video_id,
        "frame_index": frame.index,
        "pts": frame.pts,
        "pts_source": frame.pts_source,
        "time_seconds": frame.time_seconds,
        "points": _json_safe(points),
        "field": _json_safe(field),
        "note": note,
        "created_at": created_at,
        "source": source,
    }
    response.update(_json_safe(dict(analysis)))
    return response


def _preview_response(
    video_id: int,
    frame: FrameInfo,
    draft_version: int,
    points: Any,
    field: Any,
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a preview result without assigning persistence identity."""

    response: dict[str, Any] = {
        "video_id": video_id,
        "frame_index": frame.index,
        "pts": frame.pts,
        "pts_source": frame.pts_source,
        "time_seconds": frame.time_seconds,
        "draft_version": draft_version,
        "source": "preview",
        "persisted": False,
        "field": _json_safe(field),
        "points": _json_safe(points),
    }
    response.update(_json_safe(dict(analysis)))
    return response


def _correct_historical_analysis(analysis: Any) -> dict[str, Any]:
    """Apply the v0.2 eligibility policy when reading old analysis rows.

    Analysis JSON is append-only: this function returns a copied response
    object and never writes the corrected value back to SQLite.  Rows created
    by v0.2 already have the correct value, so they do not receive a noisy
    correction marker.
    """

    if not isinstance(analysis, Mapping):
        return {"status": "unavailable", "coordinate_level": "image"}
    result = dict(analysis)
    status = result.get("status")
    original_level = result.get("coordinate_level")
    if status == "usable" or original_level == "image":
        return result

    correction = {
        "field": "coordinate_level",
        "original_value": original_level,
        "corrected_value": "image",
        "policy_version": COORDINATE_ELIGIBILITY_POLICY_VERSION,
    }
    result["coordinate_level"] = "image"
    # The marker is intentionally top-level so consumers can distinguish a
    # read-time policy correction from the historical geometry diagnostics.
    result["eligibility_correction"] = correction
    return result


def _default_db_path() -> Path:
    configured = os.environ.get("GSR_DB_PATH")
    if configured:
        return Path(configured)
    # Keep runtime state beside this local application, rather than in a user
    # video directory or in the source tree's tracked files.
    return Path(__file__).resolve().parents[1] / ".gsr" / "sessions.sqlite3"


def _is_loopback_host(host_value: str | None) -> bool:
    if not host_value:
        return False
    try:
        parsed = urlsplit("//" + host_value)
        host = parsed.hostname
    except ValueError:
        return False
    if not host:
        return False
    host = host.rstrip(".").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_loopback_origin(origin_value: str | None) -> bool:
    if origin_value is None:
        return True
    origin = origin_value.strip()
    if not origin or origin.lower() == "null":
        return False
    try:
        parsed = urlsplit(origin)
        host = parsed.hostname
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        return False
    return _is_loopback_host(host)


def _require_local_mutation(request: Request) -> None:
    # Host and Origin are checked independently.  Checking only Origin allows
    # non-browser callers to forge a local Origin while reaching a public bind;
    # checking only Host allows a browser page on another origin to drive local
    # file reads through the API.
    if not _is_loopback_host(request.headers.get("host")):
        raise HTTPException(status_code=403, detail="Mutating API calls require a loopback Host")
    if not _is_loopback_origin(request.headers.get("origin")):
        raise HTTPException(status_code=403, detail="Mutating API calls require a loopback Origin")


def _video_or_404(store: SessionStore, video_id: int) -> StoredVideo:
    if isinstance(video_id, bool) or video_id < 1:
        raise HTTPException(status_code=404, detail="Video not found")
    video = store.get_video(video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


def _validate_registration_payload(payload: RegistrationRequest, video: StoredVideo) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    field = payload.field.model_dump()
    for name in ("length", "width"):
        value = field.get(name)
        if value is not None and not math.isfinite(float(value)):
            raise HTTPException(status_code=422, detail=f"Field {name} must be finite")
    if field["dimensions_verified"] and (field["length"] is None or field["width"] is None):
        raise HTTPException(status_code=422, detail="Verified field dimensions require both length and width")
    points: list[dict[str, Any]] = []
    for number, point in enumerate(payload.points):
        if len(point.image) != 2 or len(point.pitch) != 2:
            raise HTTPException(status_code=422, detail=f"Point {number} image and pitch coordinates must each have two values")
        image = [float(value) for value in point.image]
        pitch = [float(value) for value in point.pitch]
        if not all(math.isfinite(value) for value in image + pitch):
            raise HTTPException(status_code=422, detail=f"Point {number} contains a non-finite coordinate")
        if not (0 <= image[0] < video.info.width and 0 <= image[1] < video.info.height):
            raise HTTPException(status_code=422, detail=f"Point {number} is outside the displayed video frame")
        if not all(0 <= value <= 1 for value in pitch):
            raise HTTPException(status_code=422, detail=f"Point {number} pitch coordinates must be normalized between 0 and 1")
        points.append({"image": image, "pitch": pitch, "role": point.role})
    return points, field


def _run_geometry(points: list[dict[str, Any]], field: dict[str, Any], image_size: tuple[int, int]) -> dict[str, Any]:
    if estimate_registration is None:
        raise HTTPException(status_code=500, detail="Geometry module is unavailable")
    try:
        output = estimate_registration(points, field, image_size)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail=f"Could not estimate registration: {exc}") from exc
    if not isinstance(output, Mapping):
        raise HTTPException(status_code=500, detail="Geometry module returned an invalid result")
    result = dict(_json_safe(output))
    # Keep the public shape stable even when a future geometry implementation
    # omits optional diagnostic fields.
    result.setdefault("matrix", None)
    result.setdefault("status", "unavailable")
    result.setdefault("coordinate_level", "image")
    result.setdefault("reasons", [])
    result.setdefault("validation_error_px", None)
    result.setdefault("fit_error_px", None)
    result.setdefault("valid_region", [])
    result.setdefault("projected_lines", [])
    result.setdefault("sensitivity", None)
    if result["status"] not in {"usable", "review", "unavailable"}:
        result["status"] = "review"
    if result["status"] != "usable":
        # Keep this invariant at the API boundary as well as in geometry so a
        # future engine or a legacy plugin cannot grant eligibility to a
        # result that still needs review.
        result["coordinate_level"] = "image"
    if not isinstance(result["reasons"], list):
        result["reasons"] = [str(result["reasons"])]
    result["analysis_provenance"] = dict(ANALYSIS_PROVENANCE)
    return result


def _prepare_registration(
    store: SessionStore, payload: RegistrationRequest, video: StoredVideo
) -> tuple[FrameInfo, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Validate and calculate a registration through the shared save/preview path."""

    frame = store.get_frame(video, payload.frame_index)
    points, field = _validate_registration_payload(payload, video)
    analysis = _run_geometry(points, field, (video.info.width, video.info.height))
    return frame, points, field, analysis


def create_app(db_path: str | Path | None = None) -> FastAPI:
    """Create an isolated API instance, useful for tests and embedding."""

    store = SessionStore(db_path if db_path is not None else _default_db_path())
    app = FastAPI(title="GSR", version="0.2.0")
    app.state.store = store

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        messages: list[str] = []
        for error in exc.errors():
            location = ".".join(str(item) for item in error.get("loc", ()) if item != "body")
            message = str(error.get("msg", "Invalid request"))
            messages.append(f"{location}: {message}" if location else message)
        return JSONResponse(status_code=422, content={"detail": "; ".join(messages) or "Invalid request"})

    @app.get("/api/videos")
    def list_videos() -> list[dict[str, Any]]:
        return [_video_response(video) for video in store.list_videos()]

    @app.post("/api/videos")
    def add_video(payload: VideoPathRequest, request: Request) -> dict[str, Any]:
        _require_local_mutation(request)
        try:
            info = probe_video(payload.path)
            video = store.add_video(info)
        except MediaError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _video_response(video)

    @app.get("/api/videos/{video_id}/frames")
    def list_frames(video_id: int) -> list[dict[str, Any]]:
        video = _video_or_404(store, video_id)
        store.assert_current(video)
        return [_frame_response(frame) for frame in video.info.frames]

    @app.get("/api/videos/{video_id}/frame/{frame_index}")
    def get_frame(video_id: int, frame_index: int) -> Response:
        video = _video_or_404(store, video_id)
        store.assert_current(video)
        store.get_frame(video, frame_index)
        try:
            encoded = extract_frame(video.info.path, frame_index)
        except MediaError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(content=encoded, media_type="image/png")

    @app.get("/api/videos/{video_id}/registrations")
    def list_registrations(video_id: int) -> list[dict[str, Any]]:
        video = _video_or_404(store, video_id)
        store.assert_current(video)
        return store.list_registrations(video.id)

    @app.post("/api/videos/{video_id}/registrations/preview")
    def preview_registration(
        video_id: int, payload: PreviewRegistrationRequest, request: Request
    ) -> dict[str, Any]:
        _require_local_mutation(request)
        video = _video_or_404(store, video_id)
        store.assert_current(video)
        frame, points, field, analysis = _prepare_registration(store, payload, video)
        return _preview_response(
            video.id,
            frame,
            payload.draft_version,
            points,
            field,
            analysis,
        )

    @app.post("/api/videos/{video_id}/registrations")
    def create_registration(video_id: int, payload: RegistrationRequest, request: Request) -> dict[str, Any]:
        _require_local_mutation(request)
        video = _video_or_404(store, video_id)
        store.assert_current(video)
        frame, points, field, analysis = _prepare_registration(store, payload, video)
        return store.add_registration(video.id, frame, points, field, payload.note, "manual", analysis)

    @app.get("/api/videos/{video_id}/export")
    def export_session(video_id: int) -> dict[str, Any]:
        video = _video_or_404(store, video_id)
        store.assert_current(video)
        return {
            "schema_version": SESSION_EXPORT_SCHEMA_VERSION,
            "analysis_provenance": dict(ANALYSIS_PROVENANCE),
            "video": _video_response(video),
            "frames": [_frame_response(frame) for frame in video.info.frames],
            "registrations": store.list_registrations(video.id),
        }

    @app.post("/api/videos/{video_id}/propagate")
    def propagate(video_id: int, payload: PropagationRequest, request: Request) -> dict[str, Any]:
        _require_local_mutation(request)
        video = _video_or_404(store, video_id)
        store.assert_current(video)
        source = store.get_registration(payload.registration_id)
        if source is None or int(source["video_id"]) != video.id:
            raise HTTPException(status_code=404, detail="Source registration not found for this video")
        source_frame = store.get_frame(video, int(source["frame_index"]))
        target_frame = store.get_frame(video, payload.target_frame_index)
        if source_frame.index == target_frame.index:
            raise HTTPException(status_code=422, detail="Propagation target must differ from the source frame")
        if abs(target_frame.index - source_frame.index) > MAX_PROPAGATION_STEPS:
            raise HTTPException(
                status_code=422,
                detail=f"Propagation is limited to {MAX_PROPAGATION_STEPS} adjacent frames per request",
            )
        try:
            points, tracking = propagate_ground_points(
                video.info.path,
                source_frame.index,
                target_frame.index,
                source["points"],
                (video.info.width, video.info.height),
            )
        except MediaError as exc:
            raise HTTPException(status_code=422, detail=f"Could not propagate registration: {exc}") from exc
        field = dict(source["field"])
        analysis = _run_geometry(points, field, (video.info.width, video.info.height))
        # Propagation has no independent target validation.  Force this gate
        # even if a future geometry implementation accepts fit points alone.
        analysis["status"] = "review"
        analysis["coordinate_level"] = "image"
        analysis["validation_error_px"] = None
        reasons = analysis.get("reasons")
        if not isinstance(reasons, list):
            reasons = [str(reasons)] if reasons else []
        if "propagated_results_require_independent_validation" not in reasons:
            reasons.append("propagated_results_require_independent_validation")
        analysis["reasons"] = reasons
        analysis["propagation"] = tracking
        note = f"Propagated from registration {source['id']}; independent validation is required."
        return store.add_registration(video.id, target_frame, points, field, note, "propagated", analysis)

    web_dir = Path(__file__).resolve().parents[1] / "web"
    if web_dir.is_dir():
        # Keep the asset URLs used by the standalone HTML stable while serving
        # the document itself at the local app root.  These are registered
        # after all API routes so they cannot shadow an endpoint.
        app.mount("/web", StaticFiles(directory=str(web_dir)), name="web-assets")

        @app.get("/", include_in_schema=False)
        def serve_index() -> FileResponse:
            return FileResponse(web_dir / "index.html")

    return app


app = create_app()


__all__ = ["app", "create_app", "SessionStore"]
