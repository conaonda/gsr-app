"""Local video probing, exact frame extraction, and conservative point tracking.

The media layer deliberately uses the command line FFmpeg tools for frame
enumeration and extraction.  OpenCV's ``VideoCapture`` is useful for image
operations, but it cannot provide a reliable, codec-independent mapping from
decoded frame number to presentation timestamp (especially for B-frames and
variable frame rate material).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import subprocess
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np


FFPROBE = "ffprobe"
FFMPEG = "ffmpeg"


class MediaError(RuntimeError):
    """An input video could not be inspected or decoded."""


@dataclass(frozen=True)
class FrameInfo:
    """One decoded video frame and its original presentation timestamp."""

    index: int
    pts: int | None
    time_seconds: float | None
    pts_source: str = "original_pts"

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "pts": self.pts,
            "time_seconds": self.time_seconds,
            "pts_source": self.pts_source,
        }


@dataclass(frozen=True)
class VideoInfo:
    """Metadata needed by the API and the frame/propagation operations."""

    path: Path
    name: str
    width: int
    height: int
    duration: float | None
    time_base: str
    frames: tuple[FrameInfo, ...]
    rotation: int = 0
    file_size: int = 0
    file_mtime_ns: int = 0

    def as_dict(self) -> dict[str, Any]:
        # Keep the local path private.  It is needed internally, but returning
        # it from the API would make an export needlessly disclose a machine
        # path and would make browser state harder to move between machines.
        return {
            "id": None,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "duration": self.duration,
            "time_base": self.time_base,
        }


def _run_tool(args: Sequence[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise MediaError(f"Required media tool was not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"Media operation timed out while running {args[0]}") from exc
    except OSError as exc:
        raise MediaError(f"Could not run {args[0]}: {exc}") from exc


def _stderr(result: subprocess.CompletedProcess[bytes]) -> str:
    return result.stderr.decode("utf-8", errors="replace").strip()


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        # ffprobe emits integral timestamp fields as decimal strings.  Do not
        # round arbitrary floating values into an alleged PTS.
        text = str(value).strip()
        if not text or text.upper() == "N/A":
            return None
        if any(char in text for char in ".eE"):
            number = float(text)
            if not math.isfinite(number) or not number.is_integer():
                return None
            return int(number)
        return int(text, 10)
    except (TypeError, ValueError, OverflowError):
        return None


def _fraction(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "0/0"}:
        return None
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            denominator_value = float(denominator)
            if denominator_value == 0:
                return None
            result = float(numerator) / denominator_value
        else:
            result = float(text)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _rotation_from_stream(stream: Mapping[str, Any]) -> int:
    """Read the common FFmpeg rotation representations.

    The display matrix is exposed as ``side_data_list[].rotation`` by current
    ffprobe versions.  Older files often use the ``rotate`` stream tag.  A
    rotation is only used to determine the display dimensions here; ffmpeg's
    decoder applies the actual display matrix while extracting a PNG.
    """

    candidates: list[Any] = []
    tags = stream.get("tags")
    if isinstance(tags, Mapping):
        candidates.append(tags.get("rotate"))
    side_data = stream.get("side_data_list")
    if isinstance(side_data, list):
        for item in side_data:
            if isinstance(item, Mapping):
                candidates.append(item.get("rotation"))

    for candidate in candidates:
        try:
            if candidate is None:
                continue
            value = float(str(candidate).strip())
            if not math.isfinite(value):
                continue
            # Rotation metadata is conventionally an integer multiple of 90.
            # Normalize odd encoder values without allowing them to affect
            # dimensions as if they were a quarter turn.
            rounded = int(round(value / 90.0) * 90) % 360
            return rounded
        except (TypeError, ValueError, OverflowError):
            continue
    return 0


def _frame_pts(frame: Mapping[str, Any], time_base_value: float | None) -> tuple[int | None, float | None, str]:
    # Prefer the stream's actual PTS.  best_effort_timestamp is a useful
    # decoder fallback for files whose packet PTS is absent, but it can be a
    # synthesized value and must not override an explicitly present PTS.
    original_pts = _integer(frame.get("pts"))
    pts = original_pts
    pts_source = "original_pts"
    if pts is None:
        pts = _integer(frame.get("best_effort_timestamp"))
        pts_source = "best_effort" if pts is not None else "missing"

    # Deriving seconds from the integer PTS and time base avoids a second
    # rounded timestamp field drifting by a frame on long/VFR inputs.  If we
    # had to use best effort, retain its exact time field when available.
    seconds = None
    if pts is not None and time_base_value is not None and original_pts is not None:
        seconds = float(pts * time_base_value)
    if seconds is None:
        seconds = _finite_float(frame.get("pts_time"))
    if seconds is None:
        seconds = _finite_float(frame.get("best_effort_timestamp_time"))
    if seconds is None and pts is not None and time_base_value is not None:
        seconds = float(pts * time_base_value)
    return pts, seconds, pts_source


def _display_dimensions(width: int, height: int, rotation: int) -> tuple[int, int]:
    if rotation % 180:
        return height, width
    return width, height


def _canonical_path(path: str | Path) -> Path:
    try:
        candidate = Path(path)
    except TypeError as exc:
        raise MediaError("Video path must be a local filesystem path") from exc
    if not candidate.is_absolute():
        raise MediaError("Video path must be an absolute local path")
    if "\x00" in str(candidate):
        raise MediaError("Video path contains an invalid NUL character")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise MediaError("Video file does not exist") from exc
    except OSError as exc:
        raise MediaError(f"Could not resolve video path: {exc}") from exc
    if not resolved.is_file():
        raise MediaError("Video path is not a regular file")
    return resolved


def probe_video(path: str | Path) -> VideoInfo:
    """Probe a local video and return an actual decoded-frame PTS index."""

    resolved = _canonical_path(path)
    try:
        stat = resolved.stat()
    except OSError as exc:
        raise MediaError(f"Could not stat video file: {exc}") from exc

    # show_frames is intentional.  show_streams' duration/frame-rate values
    # cannot represent variable frame rate or timestamp discontinuities.
    result = _run_tool(
        (
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_streams",
            "-show_format",
            "-show_frames",
            "-of",
            "json",
            str(resolved),
        )
    )
    if result.returncode != 0:
        detail = _stderr(result)
        raise MediaError(f"ffprobe could not read the video{': ' + detail if detail else ''}")
    try:
        payload = json.loads(result.stdout.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MediaError("ffprobe returned invalid metadata") from exc

    streams = payload.get("streams")
    stream = streams[0] if isinstance(streams, list) and streams and isinstance(streams[0], Mapping) else None
    if stream is None:
        raise MediaError("The file has no readable video stream")

    try:
        coded_width = int(stream.get("width"))
        coded_height = int(stream.get("height"))
    except (TypeError, ValueError):
        raise MediaError("Video stream has invalid dimensions") from None
    if coded_width <= 0 or coded_height <= 0:
        raise MediaError("Video stream has invalid dimensions")

    time_base = str(stream.get("time_base") or "").strip()
    time_base_value = _fraction(time_base)
    if time_base_value is None or time_base_value <= 0:
        raise MediaError("Video stream has an invalid time base")

    duration = _finite_float(stream.get("duration"))
    if duration is None:
        format_section = payload.get("format")
        if isinstance(format_section, Mapping):
            duration = _finite_float(format_section.get("duration"))
    if duration is not None and duration < 0:
        duration = None

    rotation = _rotation_from_stream(stream)
    width, height = _display_dimensions(coded_width, coded_height, rotation)

    raw_frames = payload.get("frames")
    frames: list[FrameInfo] = []
    if isinstance(raw_frames, list):
        for raw in raw_frames:
            if not isinstance(raw, Mapping):
                continue
            if raw.get("media_type") not in (None, "video"):
                continue
            pts, seconds, pts_source = _frame_pts(raw, time_base_value)
            # Preserve every decoded video frame, even when a damaged stream
            # has a missing timestamp.  Skipping such a frame would shift every
            # subsequent decoded index away from ffmpeg's ``select(n)`` index.
            frames.append(FrameInfo(index=len(frames), pts=pts, time_seconds=seconds, pts_source=pts_source))
    if not frames:
        raise MediaError("Video stream contains no timestamped decoded frames")

    # A stream duration can be absent or can be less useful than the last PTS
    # for VFR inputs.  Keep the probe's duration when present; infer only when
    # necessary and when there is a positive frame interval to add.
    if duration is None:
        last = frames[-1].time_seconds
        if last is not None:
            duration = last

    return VideoInfo(
        path=resolved,
        name=resolved.name,
        width=width,
        height=height,
        duration=duration,
        time_base=time_base,
        frames=tuple(frames),
        rotation=rotation,
        file_size=int(stat.st_size),
        file_mtime_ns=int(stat.st_mtime_ns),
    )


def current_file_identity(path: str | Path) -> tuple[int, int]:
    """Return the inexpensive source identity used by persisted sessions."""

    resolved = _canonical_path(path)
    stat = resolved.stat()
    return int(stat.st_size), int(stat.st_mtime_ns)


def extract_frame(path: str | Path, index: int) -> bytes:
    """Extract one decoded frame as a PNG using FFmpeg's frame selector.

    No timestamp seek is used: timestamp seeking can land on a nearby keyframe
    and is especially easy to get wrong for B-frame and VFR content.  FFmpeg's
    normal autorotation is kept enabled so the PNG dimensions and coordinate
    system match the dimensions reported by :func:`probe_video`.
    """

    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise MediaError("Frame index must be a non-negative integer")
    resolved = _canonical_path(path)
    # The backslash escapes the comma for the FFmpeg filter parser.  ``n`` is
    # the decoded frame counter and therefore matches probe_video's ordering.
    selector = f"select=eq(n\\,{index})"
    result = _run_tool(
        (
            FFMPEG,
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(resolved),
            "-map",
            "0:v:0",
            "-vf",
            selector,
            "-frames:v",
            "1",
            "-fps_mode",
            "vfr",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "pipe:1",
        )
    )
    if result.returncode != 0 or not result.stdout:
        detail = _stderr(result)
        raise MediaError(f"Could not extract frame {index}{': ' + detail if detail else ''}")
    return bytes(result.stdout)


def decode_frame(path: str | Path, index: int) -> np.ndarray:
    """Extract and decode a frame as a BGR image."""

    encoded = extract_frame(path, index)
    image = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise MediaError(f"Could not decode extracted frame {index}")
    return image


def _point_xy(point: Mapping[str, Any]) -> tuple[float, float] | None:
    value = point.get("image")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) != 2:
        return None
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return x, y


def _convex_hull_mask(shape: tuple[int, int], points: np.ndarray, padding: float) -> np.ndarray | None:
    """Build a dilated fit-point hull mask for the tracking gate."""

    height, width = shape[:2]
    if points.shape[0] < 3:
        return None
    hull = cv2.convexHull(points.astype(np.float32).reshape(-1, 1, 2))
    if hull.shape[0] < 3 or abs(float(cv2.contourArea(hull))) < 4.0:
        return None
    mask = np.zeros((height, width), dtype=np.uint8)
    polygon = np.rint(hull.reshape(-1, 2)).astype(np.int32)
    cv2.fillConvexPoly(mask, polygon, 255)
    kernel_size = max(3, int(round(padding)) * 2 + 1)
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    return cv2.dilate(mask, kernel)


def _inside_mask(mask: np.ndarray, points: np.ndarray) -> np.ndarray:
    height, width = mask.shape[:2]
    integer_points = np.rint(points).astype(np.int32)
    result = np.zeros(points.shape[0], dtype=bool)
    for i, (x, y) in enumerate(integer_points):
        if 0 <= x < width and 0 <= y < height:
            result[i] = bool(mask[y, x])
    return result


def _track_step(previous: np.ndarray, following: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, float, np.ndarray] | None:
    """Track all fit points one frame and apply forward/backward gates."""

    if points.shape[0] < 4:
        return None
    gray_a = cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(following, cv2.COLOR_BGR2GRAY)
    # Keep the patch large enough for phone footage while limiting the chance
    # that a moving player dominates a small pitch landmark.
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01)
    forward, status_forward, error_forward = cv2.calcOpticalFlowPyrLK(
        gray_a,
        gray_b,
        points.astype(np.float32).reshape(-1, 1, 2),
        None,
        winSize=(31, 31),
        maxLevel=3,
        criteria=criteria,
        flags=0,
        minEigThreshold=1e-4,
    )
    if forward is None or status_forward is None:
        return None
    forward = forward.reshape(-1, 2)
    backward, status_backward, _ = cv2.calcOpticalFlowPyrLK(
        gray_b,
        gray_a,
        forward.astype(np.float32).reshape(-1, 1, 2),
        None,
        winSize=(31, 31),
        maxLevel=3,
        criteria=criteria,
        flags=0,
        minEigThreshold=1e-4,
    )
    if backward is None or status_backward is None:
        return None
    backward = backward.reshape(-1, 2)
    statuses = (status_forward.reshape(-1) != 0) & (status_backward.reshape(-1) != 0)
    errors = np.linalg.norm(backward - points, axis=1)
    forward_error = np.asarray(error_forward).reshape(-1) if error_forward is not None else np.full(points.shape[0], np.inf)
    valid = (
        statuses
        & np.isfinite(forward).all(axis=1)
        & np.isfinite(backward).all(axis=1)
        & np.isfinite(errors)
        & np.isfinite(forward_error)
        & (errors <= 2.0)
        & (forward_error <= 80.0)
    )

    # A fit point set should move coherently.  Permit perspective variation,
    # but reject a single point that follows a player or a compression edge.
    displacement = np.linalg.norm(forward - points, axis=1)
    median_displacement = float(np.median(displacement[valid])) if np.any(valid) else math.inf
    if not np.all(valid):
        return None
    if not math.isfinite(median_displacement):
        return None
    spread = np.abs(displacement - median_displacement)
    if np.any(spread > max(8.0, median_displacement * 3.0 + 2.0)):
        return None
    return forward, float(np.max(errors)), valid


def propagate_ground_points(
    path: str | Path,
    source_frame_index: int,
    target_frame_index: int,
    points: Sequence[Mapping[str, Any]],
    image_size: tuple[int, int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Conservatively propagate fit points between nearby video frames.

    The caller supplies points from the source registration.  Validation
    points are intentionally ignored.  Every step uses a fit-point hull gate,
    forward/backward Lucas--Kanade consistency, frame-bound checks, and a
    coherent-motion check.  Any failed gate rejects the whole propagation so
    the API never stores a plausible-looking partial result.
    """

    if isinstance(source_frame_index, bool) or isinstance(target_frame_index, bool):
        raise MediaError("Frame indices must be integers")
    if not isinstance(source_frame_index, int) or not isinstance(target_frame_index, int):
        raise MediaError("Frame indices must be integers")
    if source_frame_index < 0 or target_frame_index < 0:
        raise MediaError("Frame indices must be non-negative")
    if source_frame_index == target_frame_index:
        raise MediaError("Propagation target must differ from the source frame")
    try:
        width, height = int(image_size[0]), int(image_size[1])
    except (TypeError, ValueError, IndexError):
        raise MediaError("Invalid source image dimensions") from None
    if width <= 0 or height <= 0:
        raise MediaError("Invalid source image dimensions")

    fit_points: list[dict[str, Any]] = []
    coordinates: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, Mapping) or point.get("role") != "fit":
            continue
        xy = _point_xy(point)
        if xy is None:
            raise MediaError("Source registration contains invalid fit coordinates")
        x, y = xy
        if not (0 <= x < width and 0 <= y < height):
            raise MediaError("Source registration contains out-of-frame fit coordinates")
        # Keep pitch and role information but always produce a fresh dict so
        # caller/database objects cannot be mutated by tracking.
        copied = dict(point)
        copied["image"] = [x, y]
        copied["role"] = "fit"
        fit_points.append(copied)
        coordinates.append((x, y))
    if len(fit_points) < 4:
        raise MediaError("At least four fit points are required for propagation")

    current = np.asarray(coordinates, dtype=np.float32)
    direction = 1 if target_frame_index > source_frame_index else -1
    frame_indices = range(source_frame_index, target_frame_index, direction)
    previous = decode_frame(path, source_frame_index)
    if previous.shape[1] != width or previous.shape[0] != height:
        # The dimensions are provided from the same probe, but this catches a
        # changed source or unexpected FFmpeg autorotation before storing data.
        raise MediaError("Decoded source frame dimensions do not match the indexed video")

    max_dimension = float(max(width, height))
    for frame_index in frame_indices:
        next_index = frame_index + direction
        following = decode_frame(path, next_index)
        if following.shape[1] != width or following.shape[0] != height:
            raise MediaError("Decoded target frame dimensions do not match the indexed video")
        # Padding is deliberately modest.  The polygon remains the ground ROI
        # while a small border accounts for subpixel motion and line width.
        mask = _convex_hull_mask(previous.shape[:2], current, padding=max(4.0, max_dimension * 0.008))
        if mask is None or not np.all(_inside_mask(mask, current)):
            raise MediaError("Fit point hull is degenerate for propagation")
        tracked = _track_step(previous, following, current)
        if tracked is None:
            raise MediaError(f"Optical flow consistency failed between frames {frame_index} and {next_index}")
        next_points, _max_fb_error, _valid = tracked
        if not np.isfinite(next_points).all():
            raise MediaError("Optical flow produced non-finite coordinates")
        if np.any(next_points[:, 0] < 0) or np.any(next_points[:, 0] >= width) or np.any(next_points[:, 1] < 0) or np.any(next_points[:, 1] >= height):
            raise MediaError("Optical flow moved a fit point outside the video frame")
        current = next_points.astype(np.float32)
        previous = following

    for point, (x, y) in zip(fit_points, current.tolist()):
        point["image"] = [float(x), float(y)]
    return fit_points, {
        "tracking_steps": abs(target_frame_index - source_frame_index),
        "validation_inherited": False,
        "ground_roi": "fit_point_hull",
        "flow_check": "forward_backward_lk",
    }


__all__ = [
    "FrameInfo",
    "VideoInfo",
    "MediaError",
    "current_file_identity",
    "decode_frame",
    "extract_frame",
    "probe_video",
    "propagate_ground_points",
]
