"""Opt-in real-video benchmark; never writes to the input or normal session.

python tests/benchmark_video.py ABSOLUTE_VIDEO_PATH --check-pixels --screenshots
Artifacts remain in ignored data/video-benchmark/<timestamp>/.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import cv2
import httpx
import numpy as np
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video', type=Path)
    parser.add_argument('--check-pixels', action='store_true')
    parser.add_argument('--screenshots', action='store_true')
    args = parser.parse_args()
    video = args.video.resolve(strict=True)
    if not video.is_file():
        parser.error('video must name an existing file')
    identity = (video.stat().st_size, video.stat().st_mtime_ns)
    output = ROOT/'data'/'video-benchmark'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True)
    report = {'status': 'incomplete', 'video': video.name, 'navigation': [], 'browser_errors': []}
    with tempfile.TemporaryDirectory(prefix='gsr-real-benchmark-') as temp:
        env = dict(os.environ, GSR_DB_PATH=str(Path(temp)/'session.sqlite3'))
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            port = reserved.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        with (output/'server.log').open('w+', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'gsr.server:app',
                '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT, env=env,
                stdout=log, stderr=subprocess.STDOUT)
            try:
                with httpx.Client(base_url=base, timeout=180) as client:
                    for _ in range(100):
                        try:
                            if client.get('/api/videos', timeout=1).status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(.1)
                    with sync_playwright() as p:
                        browser = p.chromium.launch(headless=True)
                        page = browser.new_page(viewport={'width':1600, 'height':1100}, device_scale_factor=2)
                        page.on('pageerror', lambda e: report['browser_errors'].append(str(e)))
                        page.goto(base)
                        page.locator('#videoPath').fill(str(video))
                        started = time.perf_counter()
                        with page.expect_response(lambda r: r.url.endswith('/api/videos') and r.request.method == 'POST', timeout=180000) as added:
                            page.locator('#importButton').click()
                        assert added.value.status == 200
                        record = added.value.json()
                        expect(page.locator('#sourceCanvasMessage')).to_be_hidden(timeout=180000)
                        report['import_first_frame_seconds'] = round(time.perf_counter()-started, 3)
                        print('First frame:', report['import_first_frame_seconds'], flush=True)
                        frames = client.get(f'/api/videos/{record["id"]}/frames').json()
                        report['frame_count'] = len(frames)
                        for target in [150, 2700, 5100, 150, 151, 150]:
                            index = min(target, len(frames)-1)
                            started = time.perf_counter()
                            page.locator('#frameSlider').evaluate('(e,n)=>{e.value=String(n);e.dispatchEvent(new Event("change",{bubbles:true}));}', index)
                            expect(page.locator('#sourceCanvasMessage')).to_be_hidden(timeout=180000)
                            expect(page.locator('#sourceCanvas')).to_have_attribute('data-frame-index', str(index))
                            wait = round(time.perf_counter()-started, 3)
                            report['navigation'].append({'frame':index,'seconds':wait})
                            print(f'Frame {index}: {wait}s', flush=True)
                        if args.screenshots:
                            page.screenshot(path=str(output/'editor.png'), full_page=True)
                        browser.close()
                    started = time.perf_counter()
                    reused = client.post('/api/videos', json={'path':str(video)})
                    assert reused.status_code == 200 and reused.json()['id'] == record['id']
                    report['reimport_seconds'] = round(time.perf_counter()-started, 3)
                    if args.check_pixels:
                        # Import only after pointing package-level app creation
                        # at the isolated session, never the user's database.
                        os.environ['GSR_DB_PATH'] = env['GSR_DB_PATH']
                        sys.path.insert(0, str(ROOT))
                        from gsr.media import extract_frame
                        verified = []
                        for index in sorted({min(n, len(frames)-1) for n in [150,2700,5100]}):
                            png = client.get(f'/api/videos/{record["id"]}/frame/{index}')
                            png.raise_for_status()
                            actual = cv2.imdecode(np.frombuffer(png.content,np.uint8),cv2.IMREAD_COLOR)
                            expected = cv2.imdecode(np.frombuffer(extract_frame(video,index),np.uint8),cv2.IMREAD_COLOR)
                            assert np.array_equal(actual, expected), f'Pixel mismatch at frame {index}'
                            verified.append(index)
                            print('Exact pixels:', index, flush=True)
                        report['pixel_equal_frames'] = verified
                    assert not report['browser_errors'], report['browser_errors']
                    assert identity == (video.stat().st_size, video.stat().st_mtime_ns)
                    report['status'] = 'passed'
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Report:', output/'report.json', flush=True)


if __name__ == '__main__':
    main()
