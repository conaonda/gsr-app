"""Run GSR checks against an isolated server/database, including browser QA.

Usage: python tests/run_checks.py [--skip-browser]
No user session or source video is read or changed.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


class IsolatedTestDirectory(tempfile.TemporaryDirectory):
    def cleanup(self):
        # Windows can release terminated child-process handles asynchronously.
        for attempt in range(51):
            try:
                return super().cleanup()
            except PermissionError as error:
                if os.name != 'nt' or error.winerror != 32 or attempt == 50:
                    raise
                time.sleep(.1)


def run(command, env=None):
    print('+ ' + ' '.join(str(item) for item in command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-browser', action='store_true')
    args = parser.parse_args()
    with IsolatedTestDirectory(prefix='gsr-checks-') as temp:
        temp_dir = Path(temp)
        env = dict(os.environ)
        env['GSR_DB_PATH'] = str(temp_dir / 'sessions.sqlite3')
        env['GSR_TEST_VIDEO_PATH'] = str(temp_dir / 'browser-synthetic.mp4')
        env['GSR_TEST_ARTIFACT_DIR'] = str(ROOT / 'data' / 'editor-qa')
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env['GSR_TEST_URL'] = base
        # Keep server output away from the test stream, but report it on failure.
        with (temp_dir / 'server.log').open('w+', encoding='utf-8') as log:
            process = subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', 'gsr.server:app', '--host',
                 '127.0.0.1', '--port', str(port)], cwd=ROOT, env=env,
                stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                with httpx.Client(base_url=base, timeout=1) as client:
                    for _ in range(100):
                        if process.poll() is not None:
                            raise RuntimeError('Isolated server exited before becoming ready')
                        try:
                            if client.get('/api/videos').status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(.1)
                    else:
                        raise RuntimeError('Isolated server did not become ready')
                run([sys.executable, '-m', 'pytest', '-q'], env)
                run(['node', '--test', 'tests/editor-core.test.cjs'], env)
                run(['node', '--check', 'web/app.js'], env)
                if not args.skip_browser:
                    run([sys.executable, 'tests/browser_editor.py'], env)
                print('All requested checks passed with an isolated session.', flush=True)
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read()[-12000:], file=sys.stderr)
                raise
            finally:
                if os.name == 'nt' and process.poll() is None:
                    # Windows venv launchers may spawn the real Python process.
                    # Stop this isolated server's tree before deleting its DB.
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   capture_output=True, check=True)
                elif process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == '__main__':
    main()
