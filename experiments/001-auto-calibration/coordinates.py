"""Coordinate adapter for the pinned PnLCalib pinhole inference path.

No engine or application imports. Pitch units are normalized, never metric eligibility.
"""
from __future__ import annotations

import numpy as np


def input_geometry(width: int, height: int) -> dict:
    if width <= 0 or height <= 0:
        raise ValueError('Image dimensions must be positive')
    # Mirrors inference.py: only width==960 bypasses torchvision Resize((540,960)).
    model_width, model_height = (width, height) if width == 960 else (960, 540)
    forward = np.diag([model_width / width, model_height / height, 1.])
    return {
        'native_size': [width, height], 'model_size': [model_width, model_height],
        'native_to_model': forward.tolist(), 'model_to_native': np.linalg.inv(forward).tolist(),
        'resize': width != 960, 'crop': False, 'letterbox': False,
        'point_convention': 'upstream x/model_width,y/model_height; no extra half-pixel correction',
    }


def project(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float64)
    points = np.asarray(points, dtype=np.float64)
    homogeneous = np.column_stack([points, np.ones(len(points))]) @ matrix.T
    if not np.isfinite(homogeneous).all() or np.any(np.abs(homogeneous[:, -1]) < 1e-12):
        raise ValueError('Non-finite projection or point on projection horizon')
    return homogeneous[:, :-1] / homogeneous[:, -1:]


def camera_projection(camera: dict) -> np.ndarray:
    for key in ('radial_distortion', 'tangential_distortion', 'thin_prism_distortion'):
        if np.any(np.asarray(camera.get(key, []), dtype=float) != 0):
            raise ValueError('Distorted camera requires a composed projector, not a homography')
    fx, fy = camera['x_focal_length'], camera['y_focal_length']
    cx, cy = camera['principal_point']
    rotation = np.asarray(camera['rotation_matrix'], dtype=np.float64)
    center = np.asarray(camera['position_meters'], dtype=np.float64)
    if rotation.shape != (3, 3) or center.shape != (3,) or fx <= 0 or fy <= 0:
        raise ValueError('Invalid pinhole camera parameters')
    K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.]])
    P = K @ np.column_stack([rotation, -rotation @ center])
    if not np.isfinite(P).all():
        raise ValueError('Non-finite camera matrix')
    return P


def native_to_normalized_pitch(camera: dict) -> tuple[np.ndarray, np.ndarray]:
    P = camera_projection(camera)
    # Upstream 3D field origin is its center. Only restrict z=0 for ground H.
    plane_to_native = P[:, [0, 1, 3]]
    if np.linalg.matrix_rank(plane_to_native) != 3:
        raise ValueError('Singular ground-plane projection')
    centered_to_normalized = np.array([[1/105, 0, .5], [0, 1/68, .5], [0, 0, 1.]])
    H = centered_to_normalized @ np.linalg.inv(plane_to_native)
    H /= np.linalg.norm(H)
    return P, H


def validate_camera_adapter(camera: dict, upstream_P: np.ndarray, normalized_kp: dict,
                            native_kp: dict, width: int, height: int) -> dict:
    P, H = native_to_normalized_pitch(camera)
    # Cover boundaries and interior; these are algebraic probes, not accuracy labels.
    ground = np.array([[x, y, 0.] for x in (-52.5, -20., 0., 27., 52.5)
                       for y in (-34., -12., 8., 34.)])
    native = project(upstream_P, ground)
    expected = ground[:, :2] / np.array([105., 68.]) + .5
    recovered = project(H, native)
    returned = project(np.linalg.inv(H), expected)
    # Include above-ground points only in the full camera check, never the floor H.
    above_ground = np.array([[-52.5, -3.66, -2.44], [52.5, 3.66, -2.44]])
    camera_error = np.max(np.linalg.norm(project(P, above_ground)-project(upstream_P, above_ground), axis=1))
    normalized_error = np.max(np.abs(recovered-expected))
    roundtrip_error = np.max(np.linalg.norm(returned-native, axis=1))
    point_errors = [np.linalg.norm(np.array([v['x']*width, v['y']*height])-
                    np.array([native_kp[k]['x'], native_kp[k]['y']])) for k,v in normalized_kp.items()]
    point_error = max(point_errors, default=0.)
    if normalized_error > 1e-8 or roundtrip_error > 1e-5 or camera_error > 1e-5 or point_error > 1e-8:
        raise ValueError('Coordinate adapter algebraic checks failed')
    return {'status': 'passed', 'ground_probe_count': len(ground),
            'max_normalized_error': float(normalized_error),
            'max_native_roundtrip_px': float(roundtrip_error),
            'max_full_camera_difference_px': float(camera_error),
            'max_detector_denormalization_px': float(point_error),
            'meaning': 'coordinate algebra only; not independent field accuracy'}
