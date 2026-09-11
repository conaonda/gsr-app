"""Synthetic adapter checks; no model weights, footage or GPU required."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / 'experiments/001-auto-calibration/coordinates.py'
spec = importlib.util.spec_from_file_location('pnl_coordinates', SCRIPT)
coordinates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordinates)


def camera():
    forward = np.array([0.,70.,30.]); forward /= np.linalg.norm(forward)
    right = np.array([1.,0.,0.])
    down = np.cross(forward,right)
    return {'x_focal_length': 1200., 'y_focal_length': 1150.,
            'principal_point': [1168.,540.], 'position_meters': [0.,-70.,-30.],
            'rotation_matrix': np.stack([right,down,forward]).tolist(),
            'radial_distortion': [0.]*6, 'tangential_distortion': [0.]*2,
            'thin_prism_distortion': [0.]*4}


class CoordinatesTests(unittest.TestCase):
    def test_nonuniform_resize_and_inverse(self):
        geometry = coordinates.input_geometry(2336,1080)
        self.assertEqual(geometry['model_size'],[960,540])
        points = np.array([[0.,0.],[1168.,540.],[2336.,1080.]])
        model = coordinates.project(geometry['native_to_model'],points)
        np.testing.assert_allclose(model,[[0,0],[480,270],[960,540]])
        np.testing.assert_allclose(coordinates.project(geometry['model_to_native'],model),points)

    def test_upstream_width_only_resize_bypass_is_preserved(self):
        geometry = coordinates.input_geometry(960,720)
        self.assertEqual(geometry['model_size'],[960,720])
        self.assertFalse(geometry['resize'])
        with self.assertRaises(ValueError):
            coordinates.input_geometry(0,720)

    def test_camera_center_and_ground_direction(self):
        c = camera()
        P,H = coordinates.native_to_normalized_pitch(c)
        points = np.array([[-52.5,-34.,0.],[52.5,34.,0.],[0.,0.,0.],[10.,20.,0.]])
        # Direct camera equation is independent from plane homography construction.
        K = np.array([[1200.,0.,1168.],[0.,1150.,540.],[0.,0.,1.]])
        R = np.asarray(c['rotation_matrix']); C = np.asarray(c['position_meters'])
        camera_points = (points-C) @ R.T
        homogeneous = camera_points @ K.T
        pixels = homogeneous[:,:2]/homogeneous[:,2:]
        expected = points[:,:2]/[105.,68.]+.5
        np.testing.assert_allclose(coordinates.project(H,pixels),expected,atol=1e-12)
        np.testing.assert_allclose(coordinates.project(P,points),pixels,atol=1e-10)

    def test_full_camera_check_keeps_goal_height(self):
        c = camera()
        K = np.array([[1200.,0.,1168.],[0.,1150.,540.],[0.,0.,1.]])
        R = np.asarray(c['rotation_matrix']); C = np.asarray(c['position_meters'])
        P = K @ R @ np.column_stack([np.eye(3),-C])
        result = coordinates.validate_camera_adapter(c,P,{1:{'x':.2,'y':.7}},
                     {1:{'x':467.2,'y':756.}},2336,1080)
        self.assertEqual(result['status'],'passed')

    def test_distortion_is_not_silently_discarded(self):
        for key in ['radial_distortion','tangential_distortion','thin_prism_distortion']:
            c = camera(); c[key][0] = .01
            with self.assertRaises(ValueError):
                coordinates.native_to_normalized_pitch(c)

    def test_singular_and_nonfinite_camera_rejected(self):
        c = camera(); c['position_meters'] = [0.,-70.,0.]
        with self.assertRaises(ValueError):
            coordinates.native_to_normalized_pitch(c)
        c = camera(); c['x_focal_length'] = float('nan')
        with self.assertRaises(ValueError):
            coordinates.native_to_normalized_pitch(c)


if __name__ == '__main__':
    unittest.main()
