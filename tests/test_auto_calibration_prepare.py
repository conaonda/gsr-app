"""Input preparation tests, not evidence of calibration accuracy."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'experiments/001-auto-calibration/prepare.py'
spec = importlib.util.spec_from_file_location('auto_prepare', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PreparationTests(unittest.TestCase):
    def test_sampling_uses_timestamps_not_frame_fraction(self):
        self.assertEqual(module.sample_indices([0, 1, 2, 100, 101], 3), [0, 2, 4])
        self.assertEqual(module.sample_indices([10, 20, 30], 1), [1])
        self.assertEqual(module.sample_indices([0, 10], 100), [0, 1])

    def test_sampler_rejects_ambiguous_values(self):
        for pts, count in [([], 5), ([0, 1], 0), ([0, 0], 2), ([10, 9], 2)]:
            with self.assertRaises(ValueError):
                module.sample_indices(pts, count)

    def test_large_integer_pts_remain_exact(self):
        start = 2**54
        self.assertEqual(module.sample_indices([start, start+1, start+2], 2), [0, 2])

    def test_timestamp_provenance_is_not_upgraded(self):
        base, frames = module.parse_index({'streams': [{'time_base': '1/90000'}],
            'frames': [{'pts': 100}, {'best_effort_timestamp': 200}]})
        self.assertEqual(str(base), '1/90000')
        self.assertEqual([f['pts_source'] for f in frames], ['original_pts', 'best_effort'])
        for invalid in ([{}], [{'pts': 2}, {'pts': 2}], []):
            with self.assertRaises(ValueError):
                module.parse_index({'streams': [{'time_base': '1/10'}], 'frames': invalid})

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source.mp4'
            source.write_bytes(b'not-a-video')
            with self.assertRaises(ValueError):
                module.prepare(source, root)
            self.assertEqual(source.read_bytes(), b'not-a-video')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Requires FFmpeg')
    def test_actual_video_preparation_and_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'synthetic.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc2=size=160x120:rate=12:duration=2', '-vf',
                "select='eq(mod(n,3),0)+eq(n,1)'", '-fps_mode', 'vfr',
                '-c:v', 'mpeg4', str(source)], check=True, timeout=60)
            before = module.digest(source)
            report = module.prepare(source, root/'pilot', count=6, timeout=60, checks=3)
            self.assertEqual(report['status'], 'prepared')
            self.assertEqual(report['inference_status'], 'not_run')
            self.assertEqual(report['actual_sample_count'], 6)
            self.assertTrue(all(x['png_bytes_equal'] for x in report['reference_checks']))
            self.assertFalse(report['metric_eligible'])
            self.assertEqual(module.digest(source), before)
            saved = json.loads((root/'pilot/manifest.json').read_text())
            self.assertEqual(saved['status'], 'prepared')
            self.assertEqual(len(list((root/'pilot/native').glob('*.png'))), 6)


if __name__ == '__main__':
    unittest.main()
