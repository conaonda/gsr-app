"""Small, dependency-light geometry engine for manual GSR registration.

The registration points supplied by the application are image pixels and
normalised pitch coordinates.  This module deliberately keeps the transform
in that coordinate system: a returned matrix maps an image point to a
``[0, 1]`` pitch point.  A metric field description only changes the
diagnostic ``coordinate_level``; it never changes the matrix convention.

The implementation uses a normalised DLT fit and a deterministic, small
RANSAC pass when more than four fit points are available.  There is no OpenCV
dependency because this module is also used by the lightweight local server
before optional video packages are installed.

The ``valid_region`` and ``projected_lines`` payloads are expressed in the
normalised pitch coordinate system as well.  The source-image hull is used
internally for validation, then projected through the matrix for the pitch
canvas.
"""

from __future__ import annotations

from itertools import combinations, islice
from math import comb
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


# Registration is intended to be useful for diagnostics, rather than to
# silently accept a numerically fragile model.  The limits are intentionally
# small; callers can still inspect a model in ``review`` status.
_FIT_ERROR_LIMIT_PX = 5.0
_VALIDATION_ERROR_LIMIT_PX = 5.0
_RANSAC_INLIER_LIMIT_PX = 8.0
_MIN_IMAGE_SPAN = 0.05
_MIN_PITCH_SPAN = 0.05
_MIN_IMAGE_HULL_FRACTION = 5.0e-4
_MIN_PITCH_HULL_FRACTION = 5.0e-4
_SENSITIVITY_LIMIT = 25.0
_EPS = 1.0e-12


def _unique_reasons(reasons: Iterable[str]) -> list[str]:
    """Keep reason ordering stable while removing duplicates."""

    result: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason and reason not in seen:
            result.append(reason)
            seen.add(reason)
    return result


def _result(
    *,
    matrix: np.ndarray | None,
    status: str,
    coordinate_level: str,
    reasons: Iterable[str],
    validation_error_px: float | None,
    fit_error_px: float | None,
    valid_region: Sequence[Sequence[float]] | None,
    projected_lines: Sequence[Sequence[Sequence[float]]] | None,
    sensitivity: float | None,
) -> dict[str, Any]:
    """Build the public JSON-compatible result shape."""

    serial_matrix: list[list[float]] | None
    if matrix is None:
        serial_matrix = None
    else:
        serial_matrix = [[float(value) for value in row] for row in matrix]

    return {
        "matrix": serial_matrix,
        "status": status,
        "coordinate_level": coordinate_level,
        "reasons": _unique_reasons(reasons),
        "validation_error_px": (
            None if validation_error_px is None else float(validation_error_px)
        ),
        "fit_error_px": None if fit_error_px is None else float(fit_error_px),
        "valid_region": [
            [float(point[0]), float(point[1])] for point in (valid_region or [])
        ],
        "projected_lines": [
            [
                [float(segment[0][0]), float(segment[0][1])],
                [float(segment[1][0]), float(segment[1][1])],
            ]
            for segment in (projected_lines or [])
        ],
        "sensitivity": None if sensitivity is None else float(sensitivity),
    }


def _empty_result(reasons: Iterable[str]) -> dict[str, Any]:
    return _result(
        matrix=None,
        status="unavailable",
        coordinate_level="image",
        reasons=reasons,
        validation_error_px=None,
        fit_error_px=None,
        valid_region=[],
        projected_lines=[],
        sensitivity=None,
    )


def _point(value: Any) -> tuple[np.ndarray | None, bool]:
    """Return a finite 2D point and whether malformed values were nonfinite."""

    if isinstance(value, Mapping):
        if "x" not in value or "y" not in value:
            return None, False
        value = (value.get("x"), value.get("y"))

    if isinstance(value, (str, bytes)):
        return None, False
    try:
        values = list(value)
    except (TypeError, ValueError):
        return None, False
    if len(values) < 2:
        return None, False
    if any(isinstance(item, (bool, np.bool_)) for item in values[:2]):
        return None, False

    try:
        result = np.asarray([values[0], values[1]], dtype=np.float64)
    except (TypeError, ValueError, OverflowError):
        return None, False
    if result.shape != (2,):
        return None, False
    if not np.isfinite(result).all():
        return None, True
    return result, False


def _image_size(value: Any) -> tuple[float, float] | None:
    if isinstance(value, Mapping):
        width_value = value.get("width")
        height_value = value.get("height")
    else:
        if isinstance(value, (str, bytes)):
            return None
        try:
            values = list(value)
        except (TypeError, ValueError):
            return None
        if len(values) < 2:
            return None
        width_value, height_value = values[0], values[1]

    if isinstance(width_value, (bool, np.bool_)) or isinstance(
        height_value, (bool, np.bool_)
    ):
        return None

    try:
        width = float(width_value)
        height = float(height_value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not np.isfinite([width, height]).all() or width <= 0.0 or height <= 0.0:
        return None
    return width, height


def _parse_points(
    points: Any,
) -> tuple[
    list[np.ndarray],
    list[np.ndarray],
    list[np.ndarray],
    list[np.ndarray],
    list[str],
]:
    """Parse point records while keeping fit and validation errors separate."""

    fit_images: list[np.ndarray] = []
    fit_pitch: list[np.ndarray] = []
    validation_images: list[np.ndarray] = []
    validation_pitch: list[np.ndarray] = []
    reasons: list[str] = []
    fit_errors: list[str] = []
    validation_errors: list[str] = []

    if isinstance(points, Mapping) or isinstance(points, (str, bytes)):
        return fit_images, fit_pitch, validation_images, validation_pitch, ["invalid_points"]
    try:
        records = list(points)
    except (TypeError, ValueError):
        return fit_images, fit_pitch, validation_images, validation_pitch, ["invalid_points"]

    for record in records:
        if not isinstance(record, Mapping):
            reasons.append("invalid_point")
            fit_errors.append("invalid_point")
            continue

        role = record.get("role")
        is_fit = role == "fit"
        is_validation = role == "validation"
        if not (is_fit or is_validation):
            reasons.append("invalid_point_role")
            fit_errors.append("invalid_point_role")
            continue

        image, image_nonfinite = _point(record.get("image"))
        pitch, pitch_nonfinite = _point(record.get("pitch"))
        destination_errors = fit_errors if is_fit else validation_errors
        if image is None or pitch is None:
            if image_nonfinite or pitch_nonfinite:
                reasons.append("nonfinite_input")
                destination_errors.append("nonfinite_input")
            else:
                reasons.append("invalid_point")
                destination_errors.append("invalid_point")
            continue

        # Pitch coordinates are a contract invariant.  Retaining no malformed
        # point is safer than allowing a single out-of-range point to distort
        # a fit.  Validation errors leave a fit available for review.
        if (pitch < 0.0).any() or (pitch > 1.0).any():
            reasons.append("pitch_out_of_range")
            destination_errors.append("pitch_out_of_range")
            continue

        if is_fit:
            fit_images.append(image)
            fit_pitch.append(pitch)
        else:
            validation_images.append(image)
            validation_pitch.append(pitch)

    # Invalid fit records are fatal to the fit.  Invalid validation records
    # are reported later as a review gate, while valid held-out records remain
    # useful for a residual diagnostic.
    if fit_errors:
        reasons.append("invalid_fit_points")
        reasons.extend(fit_errors)
    if validation_errors:
        reasons.append("invalid_validation_points")
        reasons.extend(validation_errors)

    return fit_images, fit_pitch, validation_images, validation_pitch, reasons


def _normalization_transform(points: np.ndarray) -> np.ndarray | None:
    """Hartley normalization transform for a finite ``N x 2`` array."""

    if points.ndim != 2 or points.shape[1] != 2 or len(points) == 0:
        return None
    centroid = np.mean(points, axis=0)
    distances = np.linalg.norm(points - centroid, axis=1)
    mean_distance = float(np.mean(distances))
    if not np.isfinite(mean_distance) or mean_distance <= _EPS:
        return None
    scale = np.sqrt(2.0) / mean_distance
    transform = np.array(
        [
            [scale, 0.0, -scale * centroid[0]],
            [0.0, scale, -scale * centroid[1]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    return transform


def _normalise(points: np.ndarray, transform: np.ndarray) -> np.ndarray:
    homogeneous = np.column_stack([points, np.ones(len(points), dtype=np.float64)])
    transformed = (transform @ homogeneous.T).T
    return transformed[:, :2] / transformed[:, 2:3]


def _dlt_fit(
    source: np.ndarray, destination: np.ndarray
) -> tuple[np.ndarray | None, float | None, str | None]:
    """Fit a homography mapping source image coordinates to pitch coordinates."""

    if (
        source.ndim != 2
        or destination.ndim != 2
        or source.shape != destination.shape
        or source.shape[1] != 2
        or len(source) < 4
    ):
        return None, None, "insufficient_fit_points"
    if not np.isfinite(source).all() or not np.isfinite(destination).all():
        return None, None, "nonfinite_input"

    source_transform = _normalization_transform(source)
    destination_transform = _normalization_transform(destination)
    if source_transform is None or destination_transform is None:
        return None, None, "degenerate_fit_geometry"
    source_normal = _normalise(source, source_transform)
    destination_normal = _normalise(destination, destination_transform)

    rows: list[list[float]] = []
    for (x, y), (u, v) in zip(source_normal, destination_normal):
        rows.append([-x, -y, -1.0, 0.0, 0.0, 0.0, u * x, u * y, u])
        rows.append([0.0, 0.0, 0.0, -x, -y, -1.0, v * x, v * y, v])
    design = np.asarray(rows, dtype=np.float64)

    try:
        # Four points produce an 8x9 design matrix and need the full Vh to
        # expose its one-dimensional null space.  For an overdetermined fit,
        # reduced SVD keeps Vh at 9x9 instead of allocating a large U matrix.
        _, singular_values, vh = np.linalg.svd(
            design, full_matrices=design.shape[0] < design.shape[1]
        )
    except np.linalg.LinAlgError:
        return None, None, "ill_conditioned_geometry"
    if len(singular_values) < 8 or vh.shape[0] < 9:
        return None, None, "ill_conditioned_geometry"

    # For an overdetermined noisy system the last singular value is a residual
    # rather than a model-direction singular value.  Use the penultimate value
    # there, and all available values for the exactly determined 8 x 9 case.
    model_singular_values = singular_values[:-1] if len(singular_values) >= 9 else singular_values
    smallest = float(model_singular_values[-1])
    largest = float(model_singular_values[0])
    if (
        not np.isfinite(model_singular_values).all()
        or smallest <= max(_EPS, largest * 1.0e-14)
    ):
        return None, None, "ill_conditioned_geometry"
    condition = largest / smallest
    if not np.isfinite(condition) or condition > 1.0e12:
        return None, None, "ill_conditioned_geometry"

    candidate = vh[-1].reshape(3, 3)
    try:
        matrix = np.linalg.inv(destination_transform) @ candidate @ source_transform
    except np.linalg.LinAlgError:
        return None, condition, "ill_conditioned_geometry"
    if not np.isfinite(matrix).all() or np.linalg.matrix_rank(matrix) < 3:
        return None, condition, "ill_conditioned_geometry"
    determinant = float(np.linalg.det(matrix))
    if not np.isfinite(determinant) or abs(determinant) <= _EPS:
        return None, condition, "ill_conditioned_geometry"
    try:
        matrix_condition = float(np.linalg.cond(matrix))
    except np.linalg.LinAlgError:
        return None, condition, "ill_conditioned_geometry"
    if not np.isfinite(matrix_condition) or matrix_condition > 1.0e12:
        return None, max(condition, matrix_condition), "ill_conditioned_geometry"

    # Homographies are scale invariant.  Normalising by h[2, 2] gives callers
    # stable, readable matrices for ordinary finite image/pitch mappings.
    scale = float(matrix[2, 2])
    if abs(scale) > _EPS:
        matrix = matrix / scale
    else:
        matrix = matrix / max(float(np.linalg.norm(matrix)), _EPS)
    if not np.isfinite(matrix).all():
        return None, condition, "ill_conditioned_geometry"
    return matrix, condition, None


def _project_array(matrix: np.ndarray, points: np.ndarray) -> np.ndarray | None:
    if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
        return None
    homogeneous = np.column_stack([points, np.ones(len(points), dtype=np.float64)])
    projected = (matrix @ homogeneous.T).T
    denominator = projected[:, 2]
    if (
        not np.isfinite(projected).all()
        or (np.abs(denominator) <= _EPS).any()
    ):
        return None
    result = projected[:, :2] / denominator[:, None]
    if not np.isfinite(result).all():
        return None
    return result


def project_point(matrix: Any, point: Any) -> list[float] | None:
    """Project one image point through a returned image-to-pitch matrix.

    ``None`` is returned for malformed inputs or a point at the projective
    horizon.  Returning a plain list keeps this helper directly JSON-friendly.
    """

    try:
        candidate = np.asarray(matrix, dtype=np.float64)
    except (TypeError, ValueError, OverflowError):
        return None
    if candidate.shape != (3, 3) or not np.isfinite(candidate).all():
        return None
    parsed, _ = _point(point)
    if parsed is None:
        return None
    projected = _project_array(candidate, parsed.reshape(1, 2))
    if projected is None:
        return None
    return [float(projected[0, 0]), float(projected[0, 1])]


def _convex_hull(points: np.ndarray) -> np.ndarray:
    """Monotonic-chain convex hull, returned counter-clockwise without closure."""

    if points.ndim != 2 or points.shape[1] != 2 or len(points) == 0:
        return np.empty((0, 2), dtype=np.float64)
    unique = np.unique(points.astype(np.float64), axis=0)
    if len(unique) <= 1:
        return unique
    ordered = sorted((float(x), float(y)) for x, y in unique)

    def cross(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    lower: list[tuple[float, float]] = []
    for item in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], item) <= 0.0:
            lower.pop()
        lower.append(item)
    upper: list[tuple[float, float]] = []
    for item in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], item) <= 0.0:
            upper.pop()
        upper.append(item)
    hull = lower[:-1] + upper[:-1]
    return np.asarray(hull, dtype=np.float64)


def _polygon_area(polygon: np.ndarray) -> float:
    if polygon.ndim != 2 or len(polygon) < 3:
        return 0.0
    return float(
        0.5
        * abs(
            np.sum(
                polygon[:, 0] * np.roll(polygon[:, 1], -1)
                - polygon[:, 1] * np.roll(polygon[:, 0], -1)
            )
        )
    )


def _spatial_guard(
    image_points: np.ndarray,
    pitch_points: np.ndarray,
    width: float,
    height: float,
) -> tuple[np.ndarray, list[str], bool]:
    """Return fit hull, advisory guards, and whether geometry is structurally degenerate."""

    reasons: list[str] = []
    image_hull = _convex_hull(image_points)
    pitch_hull = _convex_hull(pitch_points)
    structural = False

    if len(image_hull) < 3 or len(pitch_hull) < 3:
        reasons.append("degenerate_fit_geometry")
        structural = True
        return image_hull, reasons, structural

    centered_image = image_points - np.mean(image_points, axis=0)
    centered_pitch = pitch_points - np.mean(pitch_points, axis=0)
    if np.linalg.matrix_rank(centered_image, tol=1.0e-10) < 2 or np.linalg.matrix_rank(
        centered_pitch, tol=1.0e-10
    ) < 2:
        reasons.append("degenerate_fit_geometry")
        structural = True

    image_area = _polygon_area(image_hull)
    pitch_area = _polygon_area(pitch_hull)
    image_fraction = image_area / (width * height)
    if image_fraction < _MIN_IMAGE_HULL_FRACTION:
        reasons.append("fit_points_not_distributed")
    if pitch_area < _MIN_PITCH_HULL_FRACTION:
        reasons.append("fit_points_not_distributed")

    image_span = np.ptp(image_points, axis=0)
    pitch_span = np.ptp(pitch_points, axis=0)
    if image_span[0] < width * _MIN_IMAGE_SPAN or image_span[1] < height * _MIN_IMAGE_SPAN:
        reasons.append("fit_points_not_distributed")
    if pitch_span[0] < _MIN_PITCH_SPAN or pitch_span[1] < _MIN_PITCH_SPAN:
        reasons.append("fit_points_not_distributed")
    return image_hull, reasons, structural


def _point_in_convex_polygon(point: np.ndarray, polygon: np.ndarray, tolerance: float) -> bool:
    if len(polygon) < 3:
        return False
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        edge = end - start
        relative = point - start
        cross = float(edge[0] * relative[1] - edge[1] * relative[0])
        if cross < -tolerance:
            return False
    return True


def _inverse_matrix(matrix: np.ndarray) -> np.ndarray | None:
    try:
        inverse = np.linalg.inv(matrix)
    except np.linalg.LinAlgError:
        return None
    if not np.isfinite(inverse).all():
        return None
    return inverse


def _inverse_errors(
    matrix: np.ndarray,
    image_points: np.ndarray,
    pitch_points: np.ndarray,
) -> np.ndarray:
    inverse = _inverse_matrix(matrix)
    if inverse is None:
        return np.full(len(image_points), np.inf, dtype=np.float64)
    projected_images = _project_array(inverse, pitch_points)
    if projected_images is None:
        return np.full(len(image_points), np.inf, dtype=np.float64)
    return np.linalg.norm(projected_images - image_points, axis=1)


def _symmetric_errors(
    matrix: np.ndarray,
    image_points: np.ndarray,
    pitch_points: np.ndarray,
    width: float,
    height: float,
) -> np.ndarray:
    inverse_errors = _inverse_errors(matrix, image_points, pitch_points)
    forward = _project_array(matrix, image_points)
    if forward is None:
        return np.full(len(image_points), np.inf, dtype=np.float64)
    # Forward errors are normalised pitch units.  Converting by the shorter
    # image dimension gives a useful pixel-scale companion to inverse errors.
    forward_errors = np.linalg.norm(forward - pitch_points, axis=1) * min(width, height)
    return np.maximum(inverse_errors, forward_errors)


def _robust_fit(
    image_points: np.ndarray,
    pitch_points: np.ndarray,
    width: float,
    height: float,
) -> tuple[np.ndarray | None, np.ndarray, list[str]]:
    """Fit all points, using deterministic four-point hypotheses for outliers."""

    count = len(image_points)
    reasons: list[str] = []
    if count < 4:
        return None, np.zeros(count, dtype=bool), ["insufficient_fit_points"]

    if count == 4:
        matrix, _, reason = _dlt_fit(image_points, pitch_points)
        if reason:
            reasons.append(reason)
        return matrix, np.ones(count, dtype=bool), reasons

    # Keep hypothesis generation bounded and lazy.  In particular,
    # ``list(combinations(range(200), 4))`` would allocate tens of millions of
    # tuples before the first model could be evaluated.  A deterministic prefix
    # gives stable coverage for ordered/manual points, then a seeded sample
    # covers arbitrary order without depending on process-global RNG state.
    max_hypotheses = 512
    total = comb(count, 4)

    def iter_hypotheses() -> Iterable[tuple[int, int, int, int]]:
        combination_iterator = combinations(range(count), 4)
        if total <= max_hypotheses:
            yield from islice(combination_iterator, max_hypotheses)
            return

        seen: set[tuple[int, int, int, int]] = set()
        prefix_count = min(64, max_hypotheses)
        for candidate in islice(combination_iterator, prefix_count):
            typed = tuple(int(item) for item in candidate)
            seen.add(typed)
            yield typed

        random = np.random.default_rng(0)
        attempts = 0
        # Random draws are effectively unique for the large input sizes where
        # this branch matters.  The bounded attempt count is a second guard in
        # case a future change lowers the population or cap dramatically.
        while len(seen) < max_hypotheses and attempts < max_hypotheses * 20:
            attempts += 1
            candidate = tuple(
                sorted(int(item) for item in random.choice(count, size=4, replace=False))
            )
            if candidate in seen:
                continue
            seen.add(candidate)
            yield candidate

        # Complete the cap deterministically if random sampling happened to
        # collide too often.  This iterator is consumed only until the cap,
        # so it remains bounded even for a very large point set.
        if len(seen) < max_hypotheses:
            for candidate in combinations(range(count), 4):
                typed = tuple(int(item) for item in candidate)
                if typed in seen:
                    continue
                seen.add(typed)
                yield typed
                if len(seen) >= max_hypotheses:
                    break

    best_matrix: np.ndarray | None = None
    best_mask = np.zeros(count, dtype=bool)
    best_score: tuple[int, float] = (-1, np.inf)
    for indices in iter_hypotheses():
        source = image_points[list(indices)]
        destination = pitch_points[list(indices)]
        candidate, _, _ = _dlt_fit(source, destination)
        if candidate is None:
            continue
        errors = _symmetric_errors(candidate, image_points, pitch_points, width, height)
        mask = np.isfinite(errors) & (errors <= _RANSAC_INLIER_LIMIT_PX)
        inlier_count = int(np.count_nonzero(mask))
        median_error = float(np.median(errors[mask])) if inlier_count else np.inf
        score = (inlier_count, -median_error)
        if score > (best_score[0], -best_score[1]):
            best_matrix = candidate
            best_mask = mask
            best_score = (inlier_count, median_error)

    if best_matrix is None:
        matrix, _, reason = _dlt_fit(image_points, pitch_points)
        if reason:
            reasons.append(reason)
        return matrix, np.ones(count, dtype=bool), reasons

    # Refit and reclassify a few times.  At least four points are required for
    # every refinement so a sparse but valid registration remains available.
    matrix = best_matrix
    mask = best_mask if np.count_nonzero(best_mask) >= 4 else np.ones(count, dtype=bool)
    for _ in range(3):
        refined, _, reason = _dlt_fit(image_points[mask], pitch_points[mask])
        if refined is None:
            if reason:
                reasons.append(reason)
            break
        matrix = refined
        errors = _symmetric_errors(matrix, image_points, pitch_points, width, height)
        new_mask = np.isfinite(errors) & (errors <= _RANSAC_INLIER_LIMIT_PX)
        if np.count_nonzero(new_mask) < 4 or np.array_equal(new_mask, mask):
            break
        mask = new_mask
    return matrix, mask, reasons


def _clip_segment_to_convex_polygon(
    first: np.ndarray, second: np.ndarray, polygon: np.ndarray, tolerance: float = 1.0e-9
) -> tuple[np.ndarray, np.ndarray] | None:
    """Clip a segment against a counter-clockwise convex polygon."""

    if len(polygon) < 3:
        return None
    direction = second - first
    lower, upper = 0.0, 1.0
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        edge = end - start
        offset = first - start
        constant = float(edge[0] * offset[1] - edge[1] * offset[0])
        coefficient = float(edge[0] * direction[1] - edge[1] * direction[0])
        if abs(coefficient) <= _EPS:
            if constant < -tolerance:
                return None
            continue
        bound = (-tolerance - constant) / coefficient
        if coefficient > 0.0:
            lower = max(lower, bound)
        else:
            upper = min(upper, bound)
        if lower - upper > tolerance:
            return None
    if lower > upper:
        return None
    clipped_first = first + lower * direction
    clipped_second = first + upper * direction
    if not np.isfinite([*clipped_first, *clipped_second]).all():
        return None
    return clipped_first, clipped_second


def _projected_lines(hull: np.ndarray) -> list[list[list[float]]]:
    """Return generic boundary/halfway lines clipped to the supported pitch hull."""

    if len(hull) < 3:
        return []
    pitch_frame = np.asarray(
        [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], dtype=np.float64
    )
    # Keep a small slack for DLT roundoff when an anchor lands on an outer
    # edge.  Coordinates are normalised, so this is also an easily bounded
    # geometric tolerance.
    clip_tolerance = 1.0e-9
    output: list[list[list[float]]] = []
    # The contract only promises normalised pitch coordinates.  These lines
    # express the outer boundary and centre axes without assuming dimensions or
    # markings from a particular adult-pitch standard.
    # The normalized pitch contract guarantees only the outer boundary and
    # halfway line.  Keep the horizontal side markings generic and omit an
    # unsubstantiated horizontal midfield line.
    pitch_segments = (
        np.asarray([[0.0, 0.0], [0.0, 1.0]], dtype=np.float64),
        np.asarray([[0.0, 0.0], [1.0, 0.0]], dtype=np.float64),
        np.asarray([[0.5, 0.0], [0.5, 1.0]], dtype=np.float64),
        np.asarray([[1.0, 0.0], [1.0, 1.0]], dtype=np.float64),
        np.asarray([[0.0, 1.0], [1.0, 1.0]], dtype=np.float64),
    )
    for pitch_segment in pitch_segments:
        clipped = _clip_segment_to_convex_polygon(
            pitch_segment[0], pitch_segment[1], hull, clip_tolerance
        )
        if clipped is None:
            continue
        clipped = _clip_segment_to_convex_polygon(
            clipped[0], clipped[1], pitch_frame, clip_tolerance
        )
        if clipped is None:
            continue
        first, second = clipped
        if np.linalg.norm(second - first) <= 1.0e-8:
            continue
        output.append(
            [
                [float(first[0]), float(first[1])],
                [float(second[0]), float(second[1])],
            ]
        )
    return output


def _sensitivity(
    matrix: np.ndarray,
    image_points: np.ndarray,
    pitch_points: np.ndarray,
    inlier_mask: np.ndarray,
) -> float | None:
    """Estimate image-pixel displacement per one-pixel anchor perturbation."""

    active = np.asarray(inlier_mask, dtype=bool)
    if active.shape != (len(image_points),) or np.count_nonzero(active) < 4:
        active = np.ones(len(image_points), dtype=bool)
    source = image_points[active]
    destination = pitch_points[active]
    if len(source) > 32:
        # Sensitivity is a diagnostic, so a deterministic spread of anchors is
        # sufficient and prevents a large paste of points from dominating the
        # registration request's runtime.
        sample_indices = np.linspace(0, len(source) - 1, 32, dtype=int)
        source = source[sample_indices]
        destination = destination[sample_indices]
    inverse = _inverse_matrix(matrix)
    if inverse is None:
        return None
    probes = np.asarray(
        [
            [0.0, 0.0],
            [0.5, 0.0],
            [1.0, 0.0],
            [1.0, 0.5],
            [1.0, 1.0],
            [0.5, 1.0],
            [0.0, 1.0],
            [0.0, 0.5],
            [0.5, 0.5],
        ],
        dtype=np.float64,
    )
    baseline = _project_array(inverse, probes)
    if baseline is None:
        return None
    maximum = 0.0
    # The probes make this a useful geometric diagnostic while keeping the
    # computation bounded for a frame with many manually selected points.
    for index in range(len(source)):
        for axis in range(2):
            for delta in (-0.5, 0.5):
                perturbed = source.copy()
                perturbed[index, axis] += delta
                candidate, _, _ = _dlt_fit(perturbed, destination)
                if candidate is None:
                    return None
                candidate_inverse = _inverse_matrix(candidate)
                if candidate_inverse is None:
                    return None
                projected = _project_array(candidate_inverse, probes)
                if projected is None:
                    return None
                displacement = float(np.max(np.linalg.norm(projected - baseline, axis=1)))
                if not np.isfinite(displacement):
                    return None
                maximum = max(maximum, displacement / abs(delta))
    return float(maximum) if np.isfinite(maximum) else None


def _metric_level(field: Any) -> str:
    """Trust dimensions only when explicitly verified and physically positive."""

    if not isinstance(field, Mapping) or field.get("dimensions_verified") is not True:
        return "normalized"
    source = field.get("dimension_source")
    if not isinstance(source, str) or not source.strip():
        return "normalized"
    try:
        length = float(field.get("length"))
        width = float(field.get("width"))
    except (TypeError, ValueError, OverflowError):
        return "normalized"
    if isinstance(field.get("length"), (bool, np.bool_)) or isinstance(
        field.get("width"), (bool, np.bool_)
    ):
        return "normalized"
    if (
        not np.isfinite([length, width]).all()
        or length <= 0.0
        or width <= 0.0
    ):
        return "normalized"
    return "metric"


def estimate_registration(
    points: Any, field: Any, image_size: Any
) -> dict[str, Any]:
    """Estimate a manual image-to-normalised-pitch registration.

    The function never raises for ordinary malformed user input.  It returns
    ``unavailable`` when no defensible matrix can be fitted and ``review``
    when a matrix exists but a distribution or validation gate is not met.
    """

    size = _image_size(image_size)
    if size is None:
        return _empty_result(["invalid_image_size"])
    width, height = size

    parsed = _parse_points(points)
    if len(parsed) != 5:
        # This branch only protects against accidental internal shape changes;
        # _parse_points currently always returns the five documented values.
        return _empty_result(["invalid_points"])
    fit_images_list, fit_pitch_list, validation_images_list, validation_pitch_list, parse_reasons = parsed
    fit_images = np.asarray(fit_images_list, dtype=np.float64).reshape((-1, 2))
    fit_pitch = np.asarray(fit_pitch_list, dtype=np.float64).reshape((-1, 2))
    validation_images = np.asarray(validation_images_list, dtype=np.float64).reshape((-1, 2))
    validation_pitch = np.asarray(validation_pitch_list, dtype=np.float64).reshape((-1, 2))

    reasons: list[str] = list(parse_reasons)
    if "invalid_fit_points" in parse_reasons:
        return _empty_result(reasons)
    if len(fit_images) < 4:
        reasons.append("insufficient_fit_points")
        if len(validation_images) == 0:
            reasons.append("missing_validation_points")
        return _empty_result(reasons)

    hull, spatial_reasons, structural = _spatial_guard(fit_images, fit_pitch, width, height)
    reasons.extend(spatial_reasons)
    if structural:
        return _empty_result(reasons)

    matrix, inlier_mask, fit_reasons = _robust_fit(fit_images, fit_pitch, width, height)
    reasons.extend(fit_reasons)
    if matrix is None:
        return _empty_result(reasons or ["homography_unavailable"])

    # Robust fitting can reject a stray fit anchor.  The region in which the
    # model is trusted must follow the inliers, otherwise one bad point would
    # enlarge the displayed support polygon and make outside-hull validation
    # meaningless.
    active_mask = np.asarray(inlier_mask, dtype=bool)
    if active_mask.shape != (len(fit_images),) or np.count_nonzero(active_mask) < 4:
        active_mask = np.ones(len(fit_images), dtype=bool)
    image_hull, active_spatial_reasons, active_structural = _spatial_guard(
        fit_images[active_mask], fit_pitch[active_mask], width, height
    )
    reasons.extend(active_spatial_reasons)
    if active_structural:
        return _empty_result(reasons + ["degenerate_fit_geometry"])
    pitch_hull = _convex_hull(fit_pitch[active_mask])
    if len(pitch_hull) < 3:
        return _empty_result(reasons + ["degenerate_fit_geometry"])

    # Fit pixels may sit just outside a displayed crop.  Preserve the model
    # for inspection, but do not call it usable without an in-frame anchor set.
    image_bounds = (
        (fit_images[:, 0] >= 0.0)
        & (fit_images[:, 0] <= width)
        & (fit_images[:, 1] >= 0.0)
        & (fit_images[:, 1] <= height)
    )
    if not image_bounds.all():
        reasons.append("fit_point_outside_image")

    fit_errors = _inverse_errors(matrix, fit_images, fit_pitch)
    if not np.isfinite(fit_errors).all():
        fit_error_px: float | None = None
        reasons.append("invalid_fit_projection")
    else:
        fit_error_px = float(np.sqrt(np.mean(np.square(fit_errors))))
        if fit_error_px > _FIT_ERROR_LIMIT_PX:
            reasons.append("fit_error_exceeds_threshold")

    validation_error_px: float | None = None
    if len(validation_images) == 0:
        reasons.append("missing_validation_points")
    else:
        # Parse errors mean some records were not held out points at all.
        if "invalid_validation_points" in parse_reasons:
            reasons.append("invalid_validation_points")

        hull_tolerance = max(width, height, 1.0) * 1.0e-8
        outside_image_hull = [
            not _point_in_convex_polygon(point, image_hull, hull_tolerance)
            for point in validation_images
        ]
        outside_pitch_hull = [
            not _point_in_convex_polygon(point, pitch_hull, 1.0e-8)
            for point in validation_pitch
        ]
        if any(outside_image_hull) or any(outside_pitch_hull):
            reasons.append("validation_outside_valid_region")

        outside_image = (
            (validation_images[:, 0] < 0.0)
            | (validation_images[:, 0] > width)
            | (validation_images[:, 1] < 0.0)
            | (validation_images[:, 1] > height)
        )
        if outside_image.any():
            reasons.append("validation_outside_image")

        # A point copied into both roles is not independent held-out evidence.
        duplicate_validation = False
        for image_point, pitch_point in zip(validation_images, validation_pitch):
            image_duplicate = np.linalg.norm(fit_images - image_point, axis=1) <= hull_tolerance
            pitch_duplicate = np.linalg.norm(fit_pitch - pitch_point, axis=1) <= 1.0e-8
            if image_duplicate.any() and pitch_duplicate.any():
                duplicate_validation = True
                break
        if duplicate_validation:
            reasons.append("validation_not_independent")

        validation_errors = _inverse_errors(matrix, validation_images, validation_pitch)
        if np.isfinite(validation_errors).all():
            validation_error_px = float(np.sqrt(np.mean(np.square(validation_errors))))
            if validation_error_px > _VALIDATION_ERROR_LIMIT_PX:
                reasons.append("validation_error_exceeds_threshold")
        else:
            reasons.append("invalid_validation_projection")

    sensitivity = _sensitivity(matrix, fit_images, fit_pitch, inlier_mask)
    if sensitivity is None:
        reasons.append("sensitivity_unavailable")
    elif sensitivity > _SENSITIVITY_LIMIT:
        reasons.append("sensitivity_exceeds_threshold")
    projected_lines = _projected_lines(pitch_hull)
    valid_region = pitch_hull.tolist()

    # A structurally valid matrix can be shown in the UI even when one or more
    # review gates failed.  Only the fit and held-out validation gates grant
    # ``usable`` status.
    status = "usable" if not reasons else "review"
    # A matrix can still be useful for inspection in ``review`` status, but
    # metric/normalised eligibility is granted only after every usable gate
    # passes.  Review and unavailable results are therefore image-only even
    # when the caller supplied verified dimensions.
    coordinate_level = _metric_level(field) if status == "usable" else "image"
    return _result(
        matrix=matrix,
        status=status,
        coordinate_level=coordinate_level,
        reasons=reasons,
        validation_error_px=validation_error_px,
        fit_error_px=fit_error_px,
        valid_region=valid_region,
        projected_lines=projected_lines,
        sensitivity=sensitivity,
    )


__all__ = ["estimate_registration", "project_point"]
