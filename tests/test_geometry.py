from __future__ import annotations

import numpy as np

from gsr.geometry import estimate_registration, project_point


IMAGE_SIZE = (960, 540)


def _rectangle_points(*, validation: list[dict] | None = None) -> list[dict]:
    points = [
        {"image": [100, 80], "pitch": [0, 0], "role": "fit"},
        {"image": [860, 80], "pitch": [1, 0], "role": "fit"},
        {"image": [860, 460], "pitch": [1, 1], "role": "fit"},
        {"image": [100, 460], "pitch": [0, 1], "role": "fit"},
    ]
    return points + (validation or [])


def test_exact_rectangle_is_usable_and_projects_pitch_lines() -> None:
    points = _rectangle_points(
        validation=[
            {"image": [480, 80], "pitch": [0.5, 0], "role": "validation"},
            {"image": [480, 460], "pitch": [0.5, 1], "role": "validation"},
        ]
    )

    result = estimate_registration(points, {"dimensions_verified": False}, IMAGE_SIZE)

    assert result["status"] == "usable"
    assert result["coordinate_level"] == "normalized"
    assert result["validation_error_px"] < 1e-6
    assert result["fit_error_px"] < 1e-6
    assert result["valid_region"] == [
        [0.0, 0.0],
        [1.0, 0.0],
        [1.0, 1.0],
        [0.0, 1.0],
    ]
    assert len(result["projected_lines"]) == 5
    assert not any(
        np.allclose(line, [[0.0, 0.5], [1.0, 0.5]])
        or np.allclose(line, [[1.0, 0.5], [0.0, 0.5]])
        for line in result["projected_lines"]
    )
    assert result["sensitivity"] is not None

    projected = project_point(result["matrix"], [480, 270])
    assert projected is not None
    assert np.allclose(projected, [0.5, 0.5], atol=1e-9)


def test_projective_registration_recovers_non_affine_mapping() -> None:
    image = np.asarray(
        [[100.0, 80.0], [860.0, 80.0], [860.0, 460.0], [100.0, 460.0]],
        dtype=float,
    )
    expected = np.asarray(
        [[0.0012, 0.00012, -0.12], [0.00005, 0.0022, -0.18], [0.0000012, 0.0000007, 1.0]],
        dtype=float,
    )

    def pitch_for(points: np.ndarray) -> np.ndarray:
        homogeneous = (expected @ np.column_stack([points, np.ones(len(points))]).T).T
        return homogeneous[:, :2] / homogeneous[:, 2, None]

    pitch = pitch_for(image)
    validation_image = np.asarray([[480.0, 270.0], [480.0, 80.0]], dtype=float)
    validation_pitch = pitch_for(validation_image)
    points = [
        {"image": source.tolist(), "pitch": target.tolist(), "role": "fit"}
        for source, target in zip(image, pitch)
    ]
    points.extend(
        {
            "image": source.tolist(),
            "pitch": target.tolist(),
            "role": "validation",
        }
        for source, target in zip(validation_image, validation_pitch)
    )

    result = estimate_registration(points, None, IMAGE_SIZE)

    assert result["status"] == "usable"
    assert result["validation_error_px"] < 1e-5
    assert np.allclose(np.asarray(result["matrix"]), expected / expected[2, 2], atol=1e-8)


def test_extra_bad_fit_anchor_is_rejected_from_supported_hull() -> None:
    points = _rectangle_points(
        validation=[
            {"image": [480, 80], "pitch": [0.5, 0], "role": "validation"}
        ]
    )
    points.insert(
        4,
        {"image": [480, 270], "pitch": [0.7, 0.2], "role": "fit"},
    )

    result = estimate_registration(points, None, IMAGE_SIZE)

    assert result["status"] == "review"
    assert result["matrix"] is not None
    assert result["fit_error_px"] > 5.0
    assert result["valid_region"] == [
        [0.0, 0.0],
        [1.0, 0.0],
        [1.0, 1.0],
        [0.0, 1.0],
    ]


def test_missing_held_out_validation_keeps_matrix_for_review() -> None:
    result = estimate_registration(_rectangle_points(), None, IMAGE_SIZE)

    assert result["status"] == "review"
    assert result["matrix"] is not None
    assert result["validation_error_px"] is None
    assert "missing_validation_points" in result["reasons"]


def test_bad_held_out_validation_is_reviewed() -> None:
    result = estimate_registration(
        _rectangle_points(
            validation=[
                {"image": [480, 300], "pitch": [0.5, 0.5], "role": "validation"}
            ]
        ),
        None,
        IMAGE_SIZE,
    )

    assert result["status"] == "review"
    assert result["matrix"] is not None
    assert result["validation_error_px"] > 5.0
    assert "validation_error_exceeds_threshold" in result["reasons"]


def test_collinear_fit_is_unavailable() -> None:
    points = [
        {"image": [100 + i * 100, 100 + i * 100], "pitch": [i / 3, i / 3], "role": "fit"}
        for i in range(4)
    ]
    points.append({"image": [250, 250], "pitch": [0.5, 0.5], "role": "validation"})

    result = estimate_registration(points, None, IMAGE_SIZE)

    assert result["status"] == "unavailable"
    assert result["matrix"] is None
    assert result["coordinate_level"] == "image"
    assert "degenerate_fit_geometry" in result["reasons"]


def test_dimensions_are_metric_only_when_explicitly_verified() -> None:
    points = _rectangle_points(
        validation=[
            {"image": [480, 270], "pitch": [0.5, 0.5], "role": "validation"}
        ]
    )

    unverified = estimate_registration(
        points,
        {"length": 105, "width": 68, "dimensions_verified": False},
        IMAGE_SIZE,
    )
    verified = estimate_registration(
        points,
        {
            "length": 105,
            "width": 68,
            "dimension_source": "manual",
            "dimensions_verified": True,
        },
        IMAGE_SIZE,
    )

    assert unverified["coordinate_level"] == "normalized"
    assert verified["coordinate_level"] == "metric"
    assert unverified["matrix"] == verified["matrix"]


def test_validation_outside_fit_hull_is_reviewed_even_with_low_residual() -> None:
    result = estimate_registration(
        _rectangle_points(
            validation=[
                {"image": [900, 270], "pitch": [1.0, 0.5], "role": "validation"}
            ]
        ),
        None,
        IMAGE_SIZE,
    )

    assert result["status"] == "review"
    assert result["matrix"] is not None
    assert "validation_outside_valid_region" in result["reasons"]
