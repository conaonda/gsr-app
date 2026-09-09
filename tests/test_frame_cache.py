from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import subprocess
import threading

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import gsr.frame_cache as cache_module
from gsr.frame_cache import FrameCache, extract_indexed_frame
from gsr.media import FrameInfo, probe_video, extract_frame
from gsr.server import create_app
from make_demo_video import make_video


@pytest.mark.parametrize('kind', ['bframes', 'vfr', 'offset', 'rotated'])
def test_seek_matches_sequential_pixels_with_exact_pts(tmp_path, monkeypatch, kind):
    path = tmp_path / 'frames.mp4'
    filters = {'vfr': "select='eq(mod(n,3),0)+eq(n,1)'", 'offset': 'setpts=PTS+2/TB'}
    args = ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=size=160x120:rate=12:duration=4']
    if kind in filters:
        args += ['-vf', filters[kind], '-fps_mode', 'vfr']
    args += ['-c:v', 'libx264', '-g', '12', '-bf', '3', str(path)]
    subprocess.run(args, check=True)
    if kind == 'rotated':
        rotated = tmp_path / 'rotated.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-c', 'copy',
                        '-metadata:s:v:0', 'rotate=90', str(rotated)], check=True)
        path = rotated
    info = probe_video(path)
    # A broken seek must fail this test, not be hidden by a correct fallback.
    def no_fallback(*_):
        raise AssertionError('The PTS seek unexpectedly fell back to sequential decoding')
    monkeypatch.setattr(cache_module, 'extract_frame', no_fallback)
    for index in sorted({1, 8, len(info.frames)//2, len(info.frames)-1}):
        expected = cv2.imdecode(np.frombuffer(extract_frame(path, index), np.uint8), cv2.IMREAD_COLOR)
        actual = cv2.imdecode(np.frombuffer(extract_indexed_frame(info, index), np.uint8), cv2.IMREAD_COLOR)
        assert np.array_equal(actual, expected), (kind, index)


def test_ambiguous_pts_and_failed_seek_use_sequential(tmp_path, monkeypatch):
    info = probe_video(make_video(tmp_path / 'test.mp4'))
    monkeypatch.setattr(cache_module, 'extract_frame', lambda *_: b'sequential')
    def no_seek(*_, **__):
        raise AssertionError('Ambiguous timestamps must not be used for seeking')
    monkeypatch.setattr(cache_module, '_run_tool', no_seek)
    for frames in [
        (FrameInfo(0, 0, 0, 'best_effort'), FrameInfo(1, 1, .1)),
        (FrameInfo(0, 1, 0), FrameInfo(1, 1, .1)),
        (FrameInfo(0, None, None, 'missing'), FrameInfo(1, 1, .1)),
        (FrameInfo(0, 2**54, 0), FrameInfo(1, 2**54+2, .1)),
    ]:
        assert extract_indexed_frame(replace(info, frames=frames), 1) == b'sequential'
    monkeypatch.setattr(cache_module, '_run_tool', lambda *_, **__: subprocess.CompletedProcess([], 0, b'', b''))
    assert extract_indexed_frame(info, 1) == b'sequential'


def test_cache_lru_bounds_and_failed_load_retry():
    cache = FrameCache(max_bytes=6, max_entries=2)
    assert cache.get(('a',), lambda: b'aaa') == (b'aaa', False)
    cache.get(('b',), lambda: b'bb')
    assert cache.get(('a',), lambda: b'bad')[1]
    cache.get(('c',), lambda: b'ccc')
    assert cache.size == 6
    assert not cache.get(('b',), lambda: b'bb')[1]
    cache.get(('large',), lambda: b'x'*7)
    assert cache.size <= 6
    assert not cache.get(('large',), lambda: b'x'*7)[1]
    with pytest.raises(ValueError):
        cache.get(('failure',), lambda: (_ for _ in ()).throw(ValueError('decode')))
    assert cache.get(('failure',), lambda: b'ok') == (b'ok', False)


def test_concurrent_cache_miss_shares_decode():
    cache = FrameCache()
    started, release = threading.Event(), threading.Event()
    calls = []
    def load():
        calls.append(1)
        started.set()
        assert release.wait(3)
        return b'png'
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get, ('frame',), load)
        assert started.wait(3)
        second = pool.submit(cache.get, ('frame',), load)
        release.set()
        assert first.result()[0] == second.result()[0] == b'png'
    assert len(calls) == 1


def test_reimport_reuses_index_and_cache_never_hides_source_change(tmp_path, monkeypatch):
    path = make_video(tmp_path / 'test.mp4')
    app = create_app(tmp_path / 'sessions.sqlite3')
    client = TestClient(app)
    headers = {'Host':'127.0.0.1', 'Origin':'http://127.0.0.1'}
    record = client.post('/api/videos', json={'path':str(path)}, headers=headers).json()
    def no_probe(*_):
        raise AssertionError('Unchanged source should reuse the stored index')
    monkeypatch.setattr('gsr.server.probe_video', no_probe)
    assert client.post('/api/videos', json={'path':str(path)}, headers=headers).json() == record
    prefix = f'/api/videos/{record["id"]}'
    first = client.get(prefix+'/frame/1')
    second = client.get(prefix+'/frame/1')
    assert first.headers['X-GSR-Frame-Cache'] == 'miss'
    assert second.headers['X-GSR-Frame-Cache'] == 'hit'
    assert first.content == second.content
    assert client.get(prefix+'/source').json()['source_revision'] == record['source_revision']
    with path.open('ab') as output:
        output.write(b'changed')
    assert client.get(prefix+'/frame/1').status_code == 409
    assert client.get(prefix+'/source').status_code == 409
