"""Run the pinned PnLCalib adapter against a prepared E1 manifest.

The runner deliberately consumes the exact native PNGs and provenance from
``prepare.py``.  It loads the two detector models once per run, creates a new
calibration object for every frame, and writes an immutable-per-run output
directory.  It never writes to the source video, the application database, or
an existing run directory.

This is an experiment runner, not an application integration.  ``candidate``
means that the upstream solver returned a camera; it is not an accuracy or
``usable`` judgment.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback
from typing import Any

import numpy as np

from coordinates import input_geometry, native_to_normalized_pitch, validate_camera_adapter


PINNED_COMMIT = '8c87391d6f4ea40c5e4d65e61529916c7a49ce62'
EXPECTED_WEIGHTS = {
    'keypoints': '7ea78fa76aaf94976a8eca428d6e3c59697a93430cba1a4603e20284b61f5113',
    'lines': 'd72f4ed71734a2e3df9fa084f666e9b8adaef21bf69bac8952d6d3f970ff7455',
}
EXPECTED_CONFIGS = {
    'keypoints': 'ee9c1fa3f7147574370569c4161c8fa9d04fd8ba8f123a1cf7adb952efe90b73',
    'lines': '05ee5f459818ae6d92c40fbd566eb47f1cec9449127eeec32a0c0ae537c720a4',
}
OUTCOMES = ('candidate', 'no_solution', 'error', 'not_run')


class PreflightError(RuntimeError):
    """The fixed input or pinned execution environment is not safe to run."""


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def jsonable(value: Any) -> Any:
    """Convert arrays/scalars while refusing NaN and infinity."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        return jsonable(value.tolist())
    if isinstance(value, np.generic):
        return jsonable(value.item())
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, float) and not np.isfinite(value):
        raise ValueError('Cannot serialize non-finite value')
    return value


def write_json(path: Path, value: Any) -> None:
    """Write a generated report atomically within a newly-created run."""
    payload = json.dumps(jsonable(value), ensure_ascii=False, indent=2, allow_nan=False)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(payload + '\n', encoding='utf-8')
    temporary.replace(path)


def git_output(repo: Path, *args: str) -> str:
    command = [
        'git', '-c', f'safe.directory={repo}', '-C', str(repo), *args,
    ]
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT).strip()


def config_hash(config: dict[str, Any]) -> str:
    # Match the E0 adapter's hash format exactly.
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def load_manifest(path: Path, expected_count: int = 100) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Validate the prepared manifest and every selected PNG before inference."""
    path = path.resolve(strict=True)
    if not path.is_file() or path.name != 'manifest.json':
        raise PreflightError('manifest must be an existing manifest.json file')
    root = path.parent.resolve()
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightError(f'cannot read manifest: {exc}') from exc

    if manifest.get('schema_version') != 'gsr.auto-calibration.input.v1':
        raise PreflightError('unexpected input manifest schema')
    if manifest.get('status') != 'prepared':
        raise PreflightError(f"manifest status is {manifest.get('status')!r}, not prepared")
    if manifest.get('inference_status') != 'not_run':
        raise PreflightError('input manifest was already marked as inferred')
    if manifest.get('manual_inference_prompts') != 0:
        raise PreflightError('input manifest records manual inference prompts')
    if manifest.get('field_model_status') != 'unverified' or manifest.get('metric_eligible') is not False:
        raise PreflightError('input manifest has unsafe field-model eligibility')

    source = manifest.get('source')
    if not isinstance(source, dict) or not isinstance(source.get('sha256'), str):
        raise PreflightError('manifest has no source SHA256')
    source_hash = source['sha256'].lower()
    if len(source_hash) != 64:
        raise PreflightError('manifest source SHA256 is malformed')
    if not isinstance(manifest.get('time_base'), str) or not manifest['time_base']:
        raise PreflightError('manifest has no rational time base')

    samples = manifest.get('samples')
    if not isinstance(samples, list) or len(samples) != expected_count:
        raise PreflightError(f'expected exactly {expected_count} prepared samples')
    if manifest.get('requested_sample_count') != expected_count or manifest.get('actual_sample_count') != expected_count:
        raise PreflightError('manifest sample-count fields do not describe the fixed 100')
    reference_checks = manifest.get('reference_checks')
    if (not isinstance(reference_checks, list) or len(reference_checks) != 3
            or not all(item.get('png_bytes_equal') is True for item in reference_checks)):
        raise PreflightError('one or more reference PNG checks did not pass')

    checked: list[dict[str, Any]] = []
    previous_frame = -1
    previous_pts = None
    for ordinal, item in enumerate(samples):
        required = ('sample_id', 'frame_index', 'pts', 'native_path', 'native_sha256',
                    'display_width', 'display_height')
        if not isinstance(item, dict) or any(key not in item for key in required):
            raise PreflightError(f'sample {ordinal} is missing provenance fields')
        frame_index = item['frame_index']
        pts = item['pts']
        if not isinstance(frame_index, int) or frame_index <= previous_frame:
            raise PreflightError('sample frame indices are not strictly increasing')
        if not isinstance(pts, int) or (previous_pts is not None and pts <= previous_pts):
            raise PreflightError('sample PTS values are not strictly increasing')
        previous_frame, previous_pts = frame_index, pts
        if item['display_width'] != 2336 or item['display_height'] != 1080:
            raise PreflightError('prepared E1 images are not the expected native 2336x1080 frames')
        native_hash = str(item['native_sha256']).lower()
        if len(native_hash) != 64:
            raise PreflightError(f'sample {ordinal} has malformed native SHA256')
        native_path = (root / Path(str(item['native_path']))).resolve()
        try:
            native_path.relative_to(root)
        except ValueError as exc:
            raise PreflightError(f'sample {ordinal} escapes the manifest directory') from exc
        if native_path.suffix.lower() != '.png' or not native_path.is_file():
            raise PreflightError(f'sample {ordinal} native PNG is missing')
        if digest(native_path) != native_hash:
            raise PreflightError(f'sample {ordinal} native PNG hash differs from manifest')
        checked.append({**item, 'time_base': manifest['time_base'], 'native_path_absolute': native_path})

    return manifest, checked, digest(path)


def verify_source(source_path: Path | None, manifest: dict[str, Any]) -> None:
    if source_path is None:
        return
    source_path = source_path.resolve(strict=True)
    expected = manifest['source']
    if source_path.name != expected['name']:
        raise PreflightError('source filename differs from the prepared manifest')
    if source_path.stat().st_size != expected['bytes']:
        raise PreflightError('source size differs from the prepared manifest')
    if digest(source_path) != expected['sha256'].lower():
        raise PreflightError('source SHA256 differs from the prepared manifest')


@dataclass
class Preflight:
    repo: Path
    manifest_path: Path
    manifest: dict[str, Any]
    samples: list[dict[str, Any]]
    manifest_sha256: str
    upstream: Path
    weights: dict[str, Path]
    config_paths: dict[str, Path]
    upstream_commit: str
    adapter_commit: str
    adapter_sha256: dict[str, str]
    config_sha256: dict[str, str]


def preflight(args: argparse.Namespace) -> Preflight:
    script = Path(__file__).resolve()
    repo = script.parents[2]
    manifest_path = Path(args.manifest).resolve(strict=True)
    manifest, samples, manifest_sha256 = load_manifest(manifest_path, expected_count=100)
    verify_source(Path(args.source) if args.source else None, manifest)

    upstream = Path(args.upstream).resolve(strict=True)
    if not (upstream / 'inference.py').is_file():
        raise PreflightError('pinned upstream checkout is missing inference.py')
    upstream_commit = git_output(upstream, 'rev-parse', 'HEAD')
    if upstream_commit != PINNED_COMMIT:
        raise PreflightError('upstream revision differs from the pinned E0 revision')
    if git_output(upstream, 'diff', 'HEAD', '--'):
        raise PreflightError('upstream tracked files are modified')

    weights = {
        'keypoints': Path(args.weights_kp).resolve(strict=True),
        'lines': Path(args.weights_line).resolve(strict=True),
    }
    for name, path in weights.items():
        if digest(path) != EXPECTED_WEIGHTS[name]:
            raise PreflightError(f'{name} checkpoint SHA256 differs from E0')
    config_paths = {
        'keypoints': upstream / 'config/hrnetv2_w48.yaml',
        'lines': upstream / 'config/hrnetv2_w48_l.yaml',
    }
    config_sha256 = {}
    for name, path in config_paths.items():
        if not path.is_file():
            raise PreflightError(f'missing upstream config: {path.name}')
        config_sha256[name] = digest(path)
        if config_sha256[name] != EXPECTED_CONFIGS[name]:
            raise PreflightError(f'{name} upstream config SHA256 differs from E0')

    try:
        adapter_commit = git_output(repo, 'rev-parse', 'HEAD')
    except subprocess.CalledProcessError as exc:
        raise PreflightError(f'cannot identify adapter commit: {exc.output}') from exc
    output = Path(args.output).resolve()
    if output.exists():
        raise PreflightError(f'refusing existing output directory: {output}')
    try:
        output.relative_to(repo / 'data')
    except ValueError as exc:
        raise PreflightError('E1 output must be a new directory below repository data/') from exc
    if output.parent != repo / 'data' / 'auto-calibration':
        raise PreflightError('E1 output must be a direct child of data/auto-calibration/')

    return Preflight(
        repo=repo, manifest_path=manifest_path, manifest=manifest, samples=samples,
        manifest_sha256=manifest_sha256, upstream=upstream, weights=weights,
        config_paths=config_paths, upstream_commit=upstream_commit,
        adapter_commit=adapter_commit,
        adapter_sha256={'worker': digest(script), 'coordinates': digest(script.with_name('coordinates.py'))},
        config_sha256=config_sha256,
    )


@dataclass
class Runtime:
    cv2: Any
    torch: Any
    psutil: Any
    engine: Any
    models: dict[str, Any]
    device: str
    cuda: bool
    load_seconds: float
    environment: dict[str, Any]
    CapturedCalibration: Any


def load_runtime(pre: Preflight, args: argparse.Namespace, logger: Any) -> Runtime:
    import cv2
    import psutil
    import torch
    import torchvision.transforms as T
    import yaml

    if args.device.startswith('cuda') and not torch.cuda.is_available():
        raise PreflightError('CUDA requested but unavailable')
    torch.set_num_threads(8)
    sys.path.insert(0, str(pre.upstream))
    import inference as engine  # type: ignore

    engine.device = args.device
    engine.transform2 = T.Resize((540, 960))
    load_start = time.perf_counter()
    models = {}
    for name, factory_name in (('keypoints', 'get_cls_net'), ('lines', 'get_cls_net_l')):
        with pre.config_paths[name].open(encoding='utf-8') as source:
            cfg = yaml.safe_load(source)
        factory = getattr(engine, factory_name)
        model = factory(cfg)
        state = torch.load(pre.weights[name], map_location='cpu', weights_only=True)
        model.load_state_dict(state, strict=True)
        del state
        models[name] = model.to(args.device).eval()
    if args.device.startswith('cuda'):
        torch.cuda.synchronize(args.device)
    load_seconds = time.perf_counter() - load_start
    cuda = args.device.startswith('cuda')

    def sync() -> None:
        if cuda:
            torch.cuda.synchronize(args.device)

    class CapturedCalibration(engine.FramebyFrameCalib):
        def __init__(self, *pos: Any, **kw: Any) -> None:
            super().__init__(*pos, **kw)
            self.solver_seconds = None

        def update(self, keypoints: Any, lines: Any) -> None:
            self.normalized_keypoints = copy.deepcopy(keypoints)
            self.normalized_lines = copy.deepcopy(lines)
            super().update(keypoints, lines)
            self.native_keypoints = copy.deepcopy(self.keypoints_dict)
            self.native_lines = copy.deepcopy(self.lines_dict)

        def heuristic_voting(self, *pos: Any, **kw: Any) -> Any:
            start = time.perf_counter()
            try:
                return super().heuristic_voting(*pos, **kw)
            finally:
                self.solver_seconds = time.perf_counter() - start

    packages = {}
    for package in ('torch', 'torchvision', 'numpy', 'scipy', 'shapely', 'PyYAML', 'matplotlib', 'pillow', 'psutil'):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    environment = {
        'python': platform.python_version(), 'platform': platform.platform(),
        'packages': packages, 'opencv': cv2.__version__,
        'cuda_runtime': torch.version.cuda,
        'device_name': torch.cuda.get_device_name(args.device) if cuda else 'CPU',
        'dtype': 'float32', 'batch_size': 1,
        'model_load_measurement': 'two strict CPU loads followed by device transfer; excluded from per-frame inference_seconds',
    }
    logger.info('models loaded in %.3fs on %s', load_seconds, environment['device_name'])
    return Runtime(cv2=cv2, torch=torch, psutil=psutil, engine=engine, models=models,
                   device=args.device, cuda=cuda, load_seconds=load_seconds,
                   environment=environment, CapturedCalibration=CapturedCalibration)


def classify_error(exc: BaseException, phase: str) -> str:
    message = str(exc).lower()
    if 'out of memory' in message or 'cuda error' in message and 'memory' in message:
        return 'oom'
    if isinstance(exc, (OSError, IOError)) or phase == 'read':
        return 'io'
    if phase in ('invalid_output', 'save'):
        return 'invalid_output'
    return 'inference'


def sidecar_base(pre: Preflight, sample: dict[str, Any], args: argparse.Namespace,
                 output: Path, ordinal: int) -> dict[str, Any]:
    thresholds = {'kp': args.kp_threshold, 'line': args.line_threshold}
    config = {
        'kp_threshold': args.kp_threshold, 'line_threshold': args.line_threshold,
        'pnl_refine': bool(args.pnl_refine), 'device': args.device,
        'dtype': 'float32', 'batch_size': 1,
        'upstream_config_sha256': pre.config_sha256,
    }
    frame_dir = output / 'frames' / f'{ordinal:03d}-{sample["sample_id"]}'
    relative_dir = frame_dir.relative_to(output).as_posix()
    return {
        'schema_version': 'gsr.auto-calibration.pnlcalib.e1.v1',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'run_id': output.name, 'phase': args.phase, 'ordinal': ordinal,
        'manifest_sha256': pre.manifest_sha256,
        'manifest_sample_count': len(pre.samples),
        'sample_id': sample['sample_id'],
        'source': {
            'kind': 'video_frame', 'filename': pre.manifest['source']['name'],
            'sha256': pre.manifest['source']['sha256'], 'bytes': pre.manifest['source']['bytes'],
            'frame_index': sample['frame_index'], 'pts': sample['pts'],
            'time_base': sample['time_base'],
            'relative_seconds': sample.get('relative_seconds'),
            'time_seconds': sample.get('time_seconds'),
            'native_frame_sha256': sample['native_sha256'],
        },
        'engine_id': 'pnlcalib', 'upstream_commit': pre.upstream_commit,
        'adapter_commit': pre.adapter_commit, 'checkpoint_sha256': EXPECTED_WEIGHTS,
        'config': config, 'config_sha256': config_hash(config), 'thresholds': thresholds,
        'pnl_refine': bool(args.pnl_refine),
        'input_transform': None, 'model_field': {
            'id': 'pnlcalib-soccer-105x68', 'dimensions': [105, 68],
            'center_circle_radius': 9.15, 'origin': 'center', 'ground_z': 0,
        },
        'field_model_status': 'unverified', 'metric_eligible': False,
        'quality_state': 'unassessed', 'independent_accuracy': 'not_measured',
        'manual_prompt_count': 0, 'inference_outcome': 'error',
        'error_category': None, 'keypoint_count': None, 'line_count': None,
        'completed_keypoint_count': None, 'completed_line_count': None,
        'raw_prediction_path': None, 'overlay_path': None, 'detections_path': None,
        'transform_kind': None, 'transform_direction': None, 'native_transform': None,
        'load_seconds': None, 'inference_seconds': None, 'solver_seconds': None,
        'end_to_end_seconds': None, 'peak_allocated_vram_bytes': None,
        'peak_reserved_vram_bytes': None, 'rss_bytes': None,
        'output_dir': relative_dir,
    }


def process_one(pre: Preflight, sample: dict[str, Any], args: argparse.Namespace,
                runtime: Runtime, output: Path, ordinal: int, logger: Any) -> dict[str, Any]:
    frame_path = sample['native_path_absolute']
    frame_dir = output / 'frames' / f'{ordinal:03d}-{sample["sample_id"]}'
    frame_dir.mkdir(parents=True, exist_ok=False)
    report = sidecar_base(pre, sample, args, output, ordinal)
    started = time.perf_counter()
    phase = 'read'
    raw: dict[str, Any] = {}
    detector_seconds: dict[str, float] = {}
    stage_starts: dict[str, float] = {}
    cam = None

    def sync() -> None:
        if runtime.cuda:
            runtime.torch.cuda.synchronize(runtime.device)

    try:
        image = runtime.cv2.imread(str(frame_path))
        if image is None:
            raise OSError(f'could not read native frame {frame_path.name}')
        height, width = image.shape[:2]
        report['input_transform'] = input_geometry(width, height)
        report['runtime_environment'] = runtime.environment
        report['load_seconds'] = runtime.load_seconds
        cam = runtime.CapturedCalibration(iwidth=width, iheight=height, denormalize=True)

        def pre_hook(name: str):
            def hook(module: Any, inputs: Any) -> None:
                sync()
                stage_starts[name] = time.perf_counter()
            return hook

        def post_hook(name: str):
            def hook(module: Any, inputs: Any, output_value: Any) -> None:
                sync()
                detector_seconds[name] = time.perf_counter() - stage_starts[name]
                raw[name] = output_value.detach()
            return hook

        handles = []
        for name, model in runtime.models.items():
            handles.extend([model.register_forward_pre_hook(pre_hook(name)),
                            model.register_forward_hook(post_hook(name))])
        if runtime.cuda:
            runtime.torch.cuda.reset_peak_memory_stats(runtime.device)
        phase = 'inference'
        inference_start = time.perf_counter()
        try:
            result = runtime.engine.inference(
                cam, image, runtime.models['keypoints'], runtime.models['lines'],
                args.kp_threshold, args.line_threshold, args.pnl_refine,
            )
            sync()
        finally:
            for handle in handles:
                handle.remove()
        report['inference_seconds'] = time.perf_counter() - inference_start
        report['end_to_end_seconds'] = report['inference_seconds']
        report['detector_seconds'] = detector_seconds
        report['solver_seconds'] = cam.solver_seconds
        report['peak_allocated_vram_bytes'] = runtime.torch.cuda.max_memory_allocated(runtime.device) if runtime.cuda else None
        report['peak_reserved_vram_bytes'] = runtime.torch.cuda.max_memory_reserved(runtime.device) if runtime.cuda else None
        report['rss_bytes'] = runtime.psutil.Process().memory_info().rss

        phase = 'raw_save'
        np.savez_compressed(frame_dir / 'raw-heatmaps.npz',
                            **{name: tensor.cpu().numpy() for name, tensor in raw.items()})
        report['raw_prediction_path'] = (frame_dir / 'raw-heatmaps.npz').relative_to(output).as_posix()
        report['heatmap_shapes'] = {name: list(tensor.shape) for name, tensor in raw.items()}

        phase = 'decode'
        peaks = runtime.engine.get_keypoints_from_heatmap_batch_maxpool(raw['keypoints'][:, :-1])
        endpoints = runtime.engine.get_keypoints_from_heatmap_batch_maxpool_l(raw['lines'][:, :-1])
        detector_kp = runtime.engine.coords_to_dict(peaks, threshold=args.kp_threshold)[0]
        detector_lines = runtime.engine.coords_to_dict(endpoints, threshold=args.line_threshold)[0]
        report['detector_keypoints_model'] = detector_kp
        report['detector_lines_model'] = detector_lines
        report['keypoint_count'] = len(detector_kp)
        report['line_count'] = len(detector_lines)
        report['augmented_keypoints_native'] = {
            key: {**value, 'origin': 'detector' if key in detector_kp else 'derived_line_intersection',
                  'ground_plane': key not in [12, 15, 16, 19]}
            for key, value in cam.native_keypoints.items()
        }
        report['lines_native'] = cam.native_lines
        report['completed_keypoint_count'] = len(cam.native_keypoints)
        report['completed_line_count'] = len(cam.native_lines)
        report['derived_confidence_note'] = 'Upstream p=1 for derived intersections is a placeholder, not measured confidence'
        report['engine_result'] = result

        if result is None:
            report['inference_outcome'] = 'no_solution'
            report['coordinate_validation'] = {'status': 'not_available'}
            overlay = image.copy()
        else:
            phase = 'invalid_output'
            upstream_projection = runtime.engine.projection_from_cam_params(result)
            projection, native_transform = native_to_normalized_pitch(result['cam_params'])
            report['coordinate_validation'] = validate_camera_adapter(
                result['cam_params'], upstream_projection, cam.normalized_keypoints,
                cam.native_keypoints, width, height,
            )
            report['camera_projection_native'] = projection
            report['native_transform'] = native_transform
            report['transform_kind'] = 'homography_ground_z0'
            report['transform_direction'] = 'native_displayed_pixel_to_normalized_model_pitch'
            report['camera_units_note'] = 'Engine model units only; field dimensions are unverified'
            overlay = runtime.engine.project(image.copy(), upstream_projection)
            report['inference_outcome'] = 'candidate'

        phase = 'save'
        overlay_path = frame_dir / 'overlay.png'
        if not runtime.cv2.imwrite(str(overlay_path), overlay):
            raise OSError('could not save native overlay')
        detections = image.copy()
        for key, point in cam.native_keypoints.items():
            xy = (round(point['x']), round(point['y']))
            runtime.cv2.circle(detections, xy, 4, (0, 255, 255), -1)
            runtime.cv2.putText(detections, str(key), (xy[0] + 5, xy[1] - 5),
                                runtime.cv2.FONT_HERSHEY_SIMPLEX, .4, (0, 255, 255), 1)
        detections_path = frame_dir / 'detections.png'
        if not runtime.cv2.imwrite(str(detections_path), detections):
            raise OSError('could not save native detections')
        report['overlay_path'] = overlay_path.relative_to(output).as_posix()
        report['detections_path'] = detections_path.relative_to(output).as_posix()
        report['source_unchanged_verified'] = digest(frame_path) == str(sample['native_sha256']).lower()
        if not report['source_unchanged_verified']:
            raise PreflightError('native input changed during inference')
    except Exception as exc:
        report['inference_outcome'] = 'error'
        report['error_category'] = classify_error(exc, phase)
        report['error'] = str(exc)
        report['traceback'] = traceback.format_exc()
        logger.error('sample %03d %s -> error/%s: %s', ordinal, sample['sample_id'], report['error_category'], exc)
    finally:
        report['worker_seconds'] = time.perf_counter() - started
        report['end_to_end_seconds'] = report.get('end_to_end_seconds') or report['worker_seconds']
        try:
            write_json(frame_dir / 'sidecar.json', report)
        except Exception as exc:
            # A sidecar failure must itself be visible and must not masquerade as a
            # candidate.  This fallback contains only scalar provenance fields.
            fallback = {
                'schema_version': report['schema_version'], 'run_id': report['run_id'],
                'phase': report['phase'], 'ordinal': ordinal, 'sample_id': sample['sample_id'],
                'source': report['source'], 'engine_id': 'pnlcalib',
                'upstream_commit': pre.upstream_commit, 'checkpoint_sha256': EXPECTED_WEIGHTS,
                'config': report['config'], 'config_sha256': report['config_sha256'],
                'pnl_refine': bool(args.pnl_refine), 'manual_prompt_count': 0,
                'inference_outcome': 'error', 'error_category': 'invalid_output',
                'error': f'sidecar serialization failed: {exc}',
                'quality_state': 'unassessed', 'field_model_status': 'unverified',
                'metric_eligible': False, 'independent_accuracy': 'not_measured',
            }
            write_json(frame_dir / 'sidecar.json', fallback)
            report = fallback
    if report.get('inference_outcome') not in OUTCOMES[:-1]:
        report['inference_outcome'] = 'error'
    if report.get('inference_outcome') == 'candidate':
        logger.info('sample %03d %s -> candidate (%.3fs)', ordinal, sample['sample_id'], report.get('inference_seconds', 0.0))
    elif report.get('inference_outcome') == 'no_solution':
        logger.info('sample %03d %s -> no_solution (%.3fs)', ordinal, sample['sample_id'], report.get('inference_seconds', 0.0))
    return report


def smoke_indices(sample_count: int) -> list[int]:
    if sample_count != 100:
        raise ValueError('E1 smoke is defined for the fixed 100-sample manifest')
    return [0, (sample_count - 1) // 2, sample_count - 1]


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(record.get('inference_outcome', 'error') for record in records)
    for outcome in OUTCOMES:
        counts.setdefault(outcome, 0)
    errors = Counter(record.get('error_category') for record in records if record.get('inference_outcome') == 'error')
    return {
        'frame_count': len(records),
        'counts': {outcome: counts[outcome] for outcome in OUTCOMES},
        'error_count_by_category': {str(key): value for key, value in sorted(errors.items())},
        'manual_prompt_count_total': sum(int(record.get('manual_prompt_count', 0) or 0) for record in records),
        'quality_state': 'unassessed', 'independent_accuracy': 'not_measured',
        'field_model_status': 'unverified', 'metric_eligible': False,
        'interpretation': 'candidate is a solver-returned output only; no independent field-quality judgment was made',
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    pre = preflight(args)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    selected_indices = list(range(100)) if args.phase == 'full' else smoke_indices(100)
    selected = [pre.samples[index] for index in selected_indices]
    thresholds = {'kp': args.kp_threshold, 'line': args.line_threshold}
    config = {
        'kp_threshold': args.kp_threshold, 'line_threshold': args.line_threshold,
        'pnl_refine': bool(args.pnl_refine), 'device': args.device,
        'dtype': 'float32', 'batch_size': 1,
        'upstream_config_sha256': pre.config_sha256,
    }
    frame_records = []
    for ordinal, sample in enumerate(selected):
        frame_records.append({
            'ordinal': ordinal, 'manifest_ordinal': selected_indices[ordinal],
            'sample_id': sample['sample_id'], 'frame_index': sample['frame_index'],
            'pts': sample['pts'], 'time_base': sample['time_base'],
            'native_path': sample['native_path'], 'native_sha256': sample['native_sha256'],
            'pnl_refine': bool(args.pnl_refine), 'inference_outcome': 'not_run',
            'error_category': None,
            'output_dir': f'frames/{ordinal:03d}-{sample["sample_id"]}',
        })
    run_report: dict[str, Any] = {
        'schema_version': 'gsr.auto-calibration.pnlcalib.e1-run.v1',
        'created_at': datetime.now(timezone.utc).isoformat(), 'run_id': output.name,
        'status': 'running', 'phase': args.phase, 'engine_id': 'pnlcalib',
        'pnl_refine': bool(args.pnl_refine), 'thresholds': thresholds,
        'manual_prompt_count': 0, 'adapter_commit': pre.adapter_commit,
        'upstream_commit': pre.upstream_commit, 'checkpoint_sha256': EXPECTED_WEIGHTS,
        'config': config, 'config_sha256': config_hash(config),
        'manifest': {
            'filename': pre.manifest_path.name, 'sha256': pre.manifest_sha256,
            'source': pre.manifest['source'], 'time_base': pre.manifest['time_base'],
            'prepared_sample_count': len(pre.samples),
        },
        'samples': frame_records,
        'notes': [
            'refinement off/on are separate runs of the same detector family; this is an ablation',
            'candidate/no_solution/error/not_run are execution outcomes, not accuracy labels',
            'quality_state remains unassessed until independent evaluation',
        ],
    }
    write_json(output / 'run.json', run_report)
    log_path = output / 'run.log'
    import logging
    logger = logging.getLogger(f'pnlcalib-e1-{output.name}')
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(log_path, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    logger.info('run_id=%s phase=%s pnl_refine=%s samples=%d', output.name, args.phase, args.pnl_refine, len(selected))
    logger.info('manifest_sha256=%s source_sha256=%s time_base=%s', pre.manifest_sha256,
                pre.manifest['source']['sha256'], pre.manifest['time_base'])

    all_results: list[dict[str, Any]] = []
    try:
        runtime = load_runtime(pre, args, logger)
        run_report['runtime_environment'] = runtime.environment
        run_report['load_seconds'] = runtime.load_seconds
        write_json(output / 'run.json', run_report)
        for ordinal, sample in enumerate(selected):
            result = process_one(pre, sample, args, runtime, output, ordinal, logger)
            all_results.append(result)
            frame_records[ordinal].update({
                'inference_outcome': result.get('inference_outcome', 'error'),
                'error_category': result.get('error_category'),
                'sidecar_path': f'{frame_records[ordinal]["output_dir"]}/sidecar.json',
                'keypoint_count': result.get('keypoint_count'), 'line_count': result.get('line_count'),
                'inference_seconds': result.get('inference_seconds'),
                'worker_seconds': result.get('worker_seconds'),
            })
            run_report['manual_prompt_count'] += int(result.get('manual_prompt_count', 0) or 0)
            with (output / 'results.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(jsonable({
                    'ordinal': ordinal, 'manifest_ordinal': selected_indices[ordinal],
                    'sample_id': sample['sample_id'], 'frame_index': sample['frame_index'],
                    'pts': sample['pts'], 'time_base': sample['time_base'],
                    'pnl_refine': bool(args.pnl_refine),
                    'inference_outcome': result.get('inference_outcome', 'error'),
                    'error_category': result.get('error_category'),
                    'keypoint_count': result.get('keypoint_count'), 'line_count': result.get('line_count'),
                    'inference_seconds': result.get('inference_seconds'),
                    'worker_seconds': result.get('worker_seconds'),
                }), ensure_ascii=False) + '\n')
            write_json(output / 'run.json', run_report)
        run_report['status'] = 'completed'
    except KeyboardInterrupt:
        run_report['status'] = 'interrupted'
        logger.exception('run interrupted')
        raise
    except Exception as exc:
        run_report['status'] = 'error'
        run_report['error'] = str(exc)
        run_report['error_category'] = 'preflight' if not all_results else 'run'
        run_report['traceback'] = traceback.format_exc()
        logger.exception('run failed: %s', exc)
        raise
    finally:
        run_report['completed_at'] = datetime.now(timezone.utc).isoformat()
        run_report['summary'] = summarize(frame_records)
        write_json(output / 'run.json', run_report)
        write_json(output / 'summary.json', {
            'schema_version': 'gsr.auto-calibration.pnlcalib.e1-summary.v1',
            'run_id': output.name, 'phase': args.phase, 'pnl_refine': bool(args.pnl_refine),
            'manifest_sha256': pre.manifest_sha256, 'upstream_commit': pre.upstream_commit,
            'checkpoint_sha256': EXPECTED_WEIGHTS, 'config_sha256': run_report['config_sha256'],
            'status': run_report['status'], **run_report['summary'],
        })
        for item in logger.handlers:
            item.flush()
            item.close()
        logger.handlers.clear()
    return run_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--weights-kp', type=Path, required=True)
    parser.add_argument('--weights-line', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--phase', choices=('smoke', 'full'), required=True)
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--kp-threshold', type=float, default=.3434)
    parser.add_argument('--line-threshold', type=float, default=.7867)
    parser.add_argument('--pnl-refine', action='store_true')
    args = parser.parse_args()
    try:
        report = run(args)
    except (PreflightError, OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'E1 RUN FAILED: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({
        'run_id': report['run_id'], 'phase': report['phase'], 'pnl_refine': report['pnl_refine'],
        'status': report['status'], 'summary': report['summary'],
    }, ensure_ascii=False))
    return 0 if report['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
