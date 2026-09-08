"""Cross-module acceptance against a running local instance (opt-in)."""
import os
import json
import subprocess
from pathlib import Path
import httpx
import pytest

BASE = os.environ.get('GSR_TEST_URL')
pytestmark = pytest.mark.skipif(not BASE, reason='Set GSR_TEST_URL for running-server acceptance')


def test_diagnostic_roundtrip():
    from make_demo_video import make_video
    path = make_video(Path(__file__).resolve().parents[1] / 'data' / 'acceptance.mp4')
    with httpx.Client(base_url=BASE, timeout=60) as client:
        response = client.post('/api/videos', json={'path': str(path)})
        assert response.status_code == 200, response.text
        video = response.json()
        prefix = f"/api/videos/{video['id']}"
        frames = client.get(prefix + '/frames').json()
        assert len(frames) == 30
        assert frames[10]['time_seconds'] == pytest.approx(1, abs=.01)
        png = client.get(prefix + '/frame/0')
        assert png.status_code == 200 and png.content.startswith(b'\x89PNG')
        pairs = [([100, 80], [0, 0]), ([860, 80], [1, 0]),
                 ([860, 460], [1, 1]), ([100, 460], [0, 1])]
        points = [{'image': a, 'pitch': b, 'role': 'fit'} for a, b in pairs]
        points += [{'image': [480, 80], 'pitch': [.5, 0], 'role': 'validation'},
                   {'image': [480, 460], 'pitch': [.5, 1], 'role': 'validation'}]
        payload = {'frame_index': 0, 'field': {'length': None, 'width': None,
                   'dimension_source': '', 'dimensions_verified': False}, 'points': points,
                   'note': 'Synthetic integration QA; not real match evidence'}
        saved = client.post(prefix + '/registrations', json=payload)
        assert saved.status_code == 200, saved.text
        result = saved.json()
        assert result['status'] == 'usable', result
        assert result['coordinate_level'] == 'normalized'
        assert result['validation_error_px'] < .1
        history = client.get(prefix + '/registrations').json()
        assert any(item['id'] == result['id'] for item in history)
        assert client.get(prefix + '/export').status_code == 200
        propagated = client.post(prefix + '/propagate', json={
            'registration_id': result['id'], 'target_frame_index': 1})
        assert propagated.status_code == 200, propagated.text
        assert propagated.json()['status'] != 'usable'
        assert propagated.json()['source'] == 'propagated'
        assert client.post('/api/videos', json={'path': str(path)},
                           headers={'Origin': 'https://unrelated.example'}).status_code == 403


def test_variable_frame_timestamps(tmp_path):
    path = tmp_path / 'variable.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=160x120:rate=10:duration=2', '-vf',
                    "select='eq(mod(n,3),0)+eq(n,1)'", '-fps_mode', 'vfr',
                    '-c:v', 'mpeg4', str(path)], check=True)
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                            '-show_frames', '-show_entries', 'frame=pts,pts_time',
                            '-of', 'json', str(path)], capture_output=True, text=True, check=True)
    expected = json.loads(probe.stdout)['frames']
    with httpx.Client(base_url=BASE, timeout=60) as client:
        response = client.post('/api/videos', json={'path': str(path)})
        assert response.status_code == 200, response.text
        actual = client.get(f"/api/videos/{response.json()['id']}/frames").json()
        assert [f['pts'] for f in actual] == [f['pts'] for f in expected]
        assert [f['time_seconds'] for f in actual] == pytest.approx(
            [float(f['pts_time']) for f in expected])
        gaps = [round(actual[i+1]['time_seconds'] - actual[i]['time_seconds'], 3)
                for i in range(len(actual)-1)]
        assert len(set(gaps)) > 1
