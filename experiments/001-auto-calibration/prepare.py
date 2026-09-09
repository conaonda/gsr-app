"""Exact-PTS pilot preparation, not model inference. Python 3.11+ / FFmpeg.
Usage: python prepare.py VIDEO --output NEW_DIR [--count 100]
No network calls, no existing output overwrite, no GSR session mutation.
"""
from __future__ import annotations
import argparse
import bisect
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def run(args: list[str], timeout: int) -> bytes:
    p = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    if p.returncode:
        raise RuntimeError(f'{Path(args[0]).name}: {p.stderr.decode(errors="replace")[-3000:]}')
    return p.stdout


def parse_index(probe: dict) -> tuple[Fraction, list[dict]]:
    base = Fraction(probe['streams'][0]['time_base'])
    if base <= 0:
        raise ValueError('Invalid time base')
    frames = []
    for i, f in enumerate(probe.get('frames', [])):
        pts, source = f.get('pts'), 'original_pts'
        if pts is None or pts == 'N/A':
            pts, source = f.get('best_effort_timestamp'), 'best_effort'
        if pts is None or pts == 'N/A':
            raise ValueError(f'Missing PTS at {i}; never substitute nominal FPS')
        pts = int(pts)
        if frames and pts <= frames[-1]['pts']:
            raise ValueError('Pilot requires increasing PTS; ambiguous-PTS adapter is not implemented')
        frames.append({'frame_index': i, 'pts': pts, 'pts_source': source})
    if not frames:
        raise ValueError('No video frames')
    return base, frames


def sample_indices(pts: list[int], count: int) -> list[int]:
    if count < 1 or not pts or any(b <= a for a, b in zip(pts, pts[1:])):
        raise ValueError('Need positive count and increasing timestamps')
    if count >= len(pts):
        return list(range(len(pts)))
    targets = ([Fraction(pts[0] + pts[-1], 2)] if count == 1 else
        [Fraction(pts[0]) + Fraction(pts[-1] - pts[0]) * i / (count - 1) for i in range(count)])
    selected = set()
    for t in targets:
        r = bisect.bisect_left(pts, t)
        candidates = [i for i in (r-1, r) if 0 <= i < len(pts)]
        selected.add(min(candidates, key=lambda i: (abs(Fraction(pts[i]) - t), i)))
    return sorted(selected)


def prepare(video: Path, out: Path, count: int = 100, timeout: int = 900, checks: int = 3) -> dict:
    video, out = video.resolve(strict=True), out.resolve()
    if not video.is_file() or out.exists():
        raise ValueError('Input must be a file and output must be a NEW directory')
    if not 1 <= count <= 400 or not 0 <= checks <= 20 or timeout <= 0:
        raise ValueError('count 1..400; checks 0..20; timeout > 0')
    tools = {n: shutil.which(n) for n in ('ffmpeg', 'ffprobe')}
    if not all(tools.values()):
        raise RuntimeError('Install FFmpeg and FFprobe on PATH')
    source_hash = digest(video)
    stat = video.stat()
    identity = (stat.st_size, stat.st_mtime_ns)
    started = time.perf_counter()
    (out / 'native').mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 'gsr.auto-calibration.input.v1', 'status': 'preparing',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source': {'name': video.name, 'sha256': source_hash, 'bytes': stat.st_size},
        'inference_status': 'not_run', 'manual_inference_prompts': 0,
        'field_model_status': 'unverified', 'metric_eligible': False,
        'evaluation_labels_status': 'not_created', 'requested_sample_count': count}
    try:
        report['tools'] = {n: run([p, '-version'], 15).decode(errors='replace').splitlines()[0] for n, p in tools.items()}
        probe = json.loads(run([tools['ffprobe'], '-v', 'error', '-threads', '2',
            '-select_streams', 'v:0', '-show_frames', '-show_entries',
            'stream=width,height,time_base:stream_tags=rotate:stream_side_data=rotation:frame=pts,best_effort_timestamp',
            '-of', 'json', str(video)], timeout))
        base, frames = parse_index(probe)
        pts = [f['pts'] for f in frames]
        selected = sample_indices(pts, count)
        (out / 'frame_index.json').write_text(json.dumps({'time_base': str(base), 'frames': frames}, indent=2), encoding='utf-8')
        report.update(time_base=str(base), total_frames=len(frames), probe_stream=probe['streams'][0],
            indexed_span_seconds=float((pts[-1]-pts[0])*base), actual_sample_count=len(selected),
            sampling='uniform_timestamp_nearest_unique',
            precision_note='Integer PTS plus rational time_base and frame_index are canonical; float seconds are display-only')
        selector = '+'.join(f'eq(n\\,{i})' for i in selected)
        prefix = [tools['ffmpeg'], '-v', 'error', '-nostdin', '-threads', '2', '-i', str(video), '-map', '0:v:0']
        run(prefix + ['-vf', f'select={selector}', '-frames:v', str(len(selected)), '-fps_mode', 'vfr',
            '-c:v', 'png', '-threads', '2', '-start_number', '0', str(out / 'native/%03d.png')], timeout)
        files = sorted((out / 'native').glob('*.png'))
        if len(files) != len(selected):
            raise RuntimeError('Selected and decoded frame counts differ')
        samples = []
        for i, path in zip(selected, files, strict=True):
            with path.open('rb') as f:
                header = f.read(24)
            if len(header) != 24 or header[:8] != b'\x89PNG\r\n\x1a\n' or header[12:16] != b'IHDR':
                raise RuntimeError('Invalid PNG')
            w, h = struct.unpack('>II', header[16:24])
            samples.append({**frames[i], 'sample_id': f'{source_hash[:16]}-{i:08d}',
                'relative_seconds': float((pts[i]-pts[0])*base), 'time_seconds': float(pts[i]*base),
                'native_path': path.relative_to(out).as_posix(), 'native_sha256': digest(path),
                'display_width': w, 'display_height': h,
                'preprocessing': {'resize': False, 'crop': False, 'autorotate': True},
                'evaluation_role': 'zero_shot_pilot_not_generalization_test'})
        report['samples'] = samples
        report['reference_checks'] = []
        for pos in sample_indices(list(range(len(samples))), min(checks, len(samples))) if checks else []:
            i = samples[pos]['frame_index']
            png = run(prefix + ['-vf', f'select=eq(n\\,{i})', '-frames:v', '1', '-fps_mode', 'vfr',
                '-f', 'image2pipe', '-c:v', 'png', '-threads', '2', 'pipe:1'], timeout)
            same = hashlib.sha256(png).hexdigest() == samples[pos]['native_sha256']
            report['reference_checks'].append({'frame_index': i, 'png_bytes_equal': same})
            if not same:
                raise RuntimeError(f'Single-frame reference differs at {i}')
        windows = []
        for ratio in (Fraction(1,8), Fraction(3,8), Fraction(5,8), Fraction(7,8)):
            center = pts[0] + ratio*(pts[-1]-pts[0])
            half = Fraction(5, 2) / base
            lo, hi = bisect.bisect_left(pts, center-half), bisect.bisect_right(pts, center+half)-1
            windows.append({'first_frame_index': lo, 'last_frame_index': hi,
                'sampling': 'every_original_frame', 'extracted': False, 'inference_status': 'not_run'})
        report['temporal_windows'] = windows
        after = video.stat()
        if identity != (after.st_size, after.st_mtime_ns) or digest(video) != source_hash:
            raise RuntimeError('Source changed during preparation')
        report.update(status='prepared', source_unchanged_verified=True)
        return report
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        raise
    finally:
        report['preparation_seconds'] = round(time.perf_counter()-started, 3)
        (out / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('video', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--count', type=int, default=100)
    p.add_argument('--timeout', type=int, default=900)
    p.add_argument('--check-samples', type=int, default=3)
    a = p.parse_args()
    try:
        result = prepare(a.video, a.output, a.count, a.timeout, a.check_samples)
    except (ValueError, RuntimeError, OSError, KeyError, IndexError, subprocess.TimeoutExpired) as exc:
        print(f'PREPARATION FAILED: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({'status': result['status'], 'samples': result['actual_sample_count'],
        'inference_status': 'not_run', 'manifest': str(a.output / 'manifest.json')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
