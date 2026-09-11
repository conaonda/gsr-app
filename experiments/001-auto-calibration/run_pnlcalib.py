"""Run one pinned PnLCalib image without manual prompts; save raw/native sidecars.

Upstream remains an external checkout. No network requests or GSR database access.
Use a new output folder for every run. See e0-results.md for the tested environment.
"""
from __future__ import annotations

import argparse
import copy
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

import numpy as np

from coordinates import input_geometry, native_to_normalized_pitch, validate_camera_adapter

PINNED_COMMIT = '8c87391d6f4ea40c5e4d65e61529916c7a49ce62'


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def serializable(value):
    if isinstance(value, np.ndarray):
        return serializable(value.tolist())
    if isinstance(value, np.generic):
        return serializable(value.item())
    if isinstance(value, dict):
        return {str(k): serializable(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(v) for v in value]
    if isinstance(value, float) and not np.isfinite(value):
        raise ValueError('Cannot serialize non-finite prediction')
    return value


def run(args) -> dict:
    upstream = args.upstream.resolve(strict=True)
    image_path = args.image.resolve(strict=True)
    weights = {name: path.resolve(strict=True) for name,path in
               [('keypoints', args.weights_kp), ('lines', args.weights_line)]}
    revision = subprocess.check_output(['git', '-C', str(upstream), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != PINNED_COMMIT:
        raise ValueError('Upstream revision differs from candidates.json')
    tracked_changes = subprocess.check_output(['git', '-C', str(upstream), 'diff', 'HEAD', '--'], text=True)
    if tracked_changes:
        raise ValueError('Upstream tracked files must be unmodified')
    args.output.mkdir(parents=True, exist_ok=False)
    input_hash = digest(image_path)
    config_paths = {'keypoints': upstream/'config/hrnetv2_w48.yaml',
                    'lines': upstream/'config/hrnetv2_w48_l.yaml'}
    config = {'kp_threshold': args.kp_threshold, 'line_threshold': args.line_threshold,
              'pnl_refine': args.pnl_refine, 'device': args.device, 'dtype': 'float32', 'batch_size': 1,
              'upstream_config_sha256': {k: digest(v) for k,v in config_paths.items()}}
    report = {'schema_version': 'gsr.auto-calibration.pnlcalib.v1',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'sample_id': args.sample_id or f'image-{input_hash[:16]}',
        'source': {'kind': 'still_image', 'filename': image_path.name, 'sha256': input_hash,
                   'frame_index': None, 'pts': None, 'time_base': None},
        'engine_id': 'pnlcalib', 'upstream_commit': revision,
        'checkpoint_sha256': {k: digest(v) for k,v in weights.items()},
        'config': config, 'config_sha256': hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),
        'adapter_sha256': {'worker': digest(Path(__file__)), 'coordinates': digest(Path(__file__).with_name('coordinates.py'))},
        'manual_prompt_count': 0, 'inference_outcome': 'error', 'quality_state': 'unassessed',
        'field_model_status': 'unverified', 'metric_eligible': False,
        'independent_accuracy': 'not_measured', 'native_transform': None,
        'model_field': {'id': 'pnlcalib-soccer-105x68', 'dimensions': [105,68],
                        'center_circle_radius': 9.15, 'origin': 'center', 'ground_z': 0},
    }
    phase = 'environment'
    started = time.perf_counter()
    try:
        import cv2
        import torch
        import torchvision.transforms as T
        import yaml
        import psutil

        sys.path.insert(0, str(upstream))
        import inference as engine

        if args.device.startswith('cuda') and not torch.cuda.is_available():
            raise RuntimeError('CUDA requested but unavailable')
        torch.set_num_threads(8)
        engine.device = args.device
        engine.transform2 = T.Resize((540,960))
        image = cv2.imread(str(image_path))
        if image is None:
            raise ValueError('Could not read input image')
        height, width = image.shape[:2]
        report['input_transform'] = input_geometry(width,height)
        report['environment'] = {'python': platform.python_version(), 'platform': platform.platform(),
            'packages': {p: importlib.metadata.version(p) for p in
                         ['torch','torchvision','numpy','scipy','shapely','PyYAML','matplotlib','pillow','psutil']},
            'opencv': cv2.__version__, 'cuda_runtime': torch.version.cuda,
            'device_name': torch.cuda.get_device_name(args.device) if args.device.startswith('cuda') else 'CPU'}
        cuda = args.device.startswith('cuda')
        def sync():
            if cuda:
                torch.cuda.synchronize(args.device)

        load_start = time.perf_counter()
        models = {}
        for name,factory in [('keypoints',engine.get_cls_net),('lines',engine.get_cls_net_l)]:
            with config_paths[name].open(encoding='utf-8') as source:
                cfg = yaml.safe_load(source)
            model = factory(cfg)
            state = torch.load(weights[name], map_location='cpu', weights_only=True)
            model.load_state_dict(state, strict=True)
            del state
            models[name] = model.to(args.device).eval()
        sync()
        report['model_load_seconds'] = time.perf_counter()-load_start

        class CapturedCalibration(engine.FramebyFrameCalib):
            def update(self, keypoints, lines):
                self.normalized_keypoints = copy.deepcopy(keypoints)
                self.normalized_lines = copy.deepcopy(lines)
                super().update(keypoints,lines)
                self.native_keypoints = copy.deepcopy(self.keypoints_dict)
                self.native_lines = copy.deepcopy(self.lines_dict)

            def heuristic_voting(self, *pos, **kw):
                start = time.perf_counter()
                try:
                    return super().heuristic_voting(*pos, **kw)
                finally:
                    self.solver_seconds = time.perf_counter()-start

        cam = CapturedCalibration(iwidth=width,iheight=height,denormalize=True)
        raw = {}; detector_seconds = {}; stage_starts = {}
        handles = []
        def pre(name):
            def hook(module, inputs):
                sync(); stage_starts[name] = time.perf_counter()
            return hook
        def post(name):
            def hook(module, inputs, output):
                sync(); detector_seconds[name] = time.perf_counter()-stage_starts[name]
                raw[name] = output.detach()
            return hook
        for name,model in models.items():
            handles += [model.register_forward_pre_hook(pre(name)), model.register_forward_hook(post(name))]
        if cuda:
            torch.cuda.reset_peak_memory_stats(args.device)
        phase = 'inference'
        inference_start = time.perf_counter()
        try:
            result = engine.inference(cam,image,models['keypoints'],models['lines'],
                                      args.kp_threshold,args.line_threshold,args.pnl_refine)
            sync()
        finally:
            for handle in handles:
                handle.remove()
        report['processing_seconds'] = time.perf_counter()-inference_start
        report['detector_seconds'] = detector_seconds
        report['solver_seconds'] = cam.solver_seconds
        report['peak_vram_allocated_bytes'] = torch.cuda.max_memory_allocated(args.device) if cuda else None
        report['peak_vram_reserved_bytes'] = torch.cuda.max_memory_reserved(args.device) if cuda else None
        report['rss_after_inference_bytes'] = psutil.Process().memory_info().rss
        report['raw_prediction_path'] = 'raw-heatmaps.npz'
        np.savez_compressed(args.output/'raw-heatmaps.npz',
                            **{name: tensor.cpu().numpy() for name,tensor in raw.items()})
        report['heatmap_shapes'] = {name: list(tensor.shape) for name,tensor in raw.items()}
        peaks = engine.get_keypoints_from_heatmap_batch_maxpool(raw['keypoints'][:,:-1])
        endpoints = engine.get_keypoints_from_heatmap_batch_maxpool_l(raw['lines'][:,:-1])
        detector_kp = engine.coords_to_dict(peaks, threshold=args.kp_threshold)[0]
        detector_lines = engine.coords_to_dict(endpoints, threshold=args.line_threshold)[0]
        report['detector_keypoints_model'] = detector_kp
        report['detector_lines_model'] = detector_lines
        report['augmented_keypoints_native'] = {
            k: {**v, 'origin': 'detector' if k in detector_kp else 'derived_line_intersection',
                'ground_plane': k not in [12,15,16,19]} for k,v in cam.native_keypoints.items()}
        report['lines_native'] = cam.native_lines
        report['derived_confidence_note'] = 'Upstream p=1 for derived intersections is a placeholder, not measured confidence'
        report['engine_result'] = result
        phase = 'invalid_output'
        if result is None:
            report['inference_outcome'] = 'no_solution'
            report['coordinate_validation'] = {'status': 'not_available'}
            overlay = image.copy()
        else:
            upstream_P = engine.projection_from_cam_params(result)
            P,H = native_to_normalized_pitch(result['cam_params'])
            report['coordinate_validation'] = validate_camera_adapter(result['cam_params'],upstream_P,
                cam.normalized_keypoints,cam.native_keypoints,width,height)
            report['camera_projection_native'] = P
            report['native_transform'] = H
            report['transform_kind'] = 'homography_ground_z0'
            report['transform_direction'] = 'native_displayed_pixel_to_normalized_model_pitch'
            report['camera_units_note'] = 'Engine model units only; field dimensions are unverified'
            overlay = engine.project(image.copy(),upstream_P)
            report['inference_outcome'] = 'candidate'
        if not cv2.imwrite(str(args.output/'overlay.png'),overlay):
            raise RuntimeError('Could not save native overlay')
        detections = image.copy()
        for key,point in cam.native_keypoints.items():
            xy = (round(point['x']),round(point['y']))
            cv2.circle(detections,xy,4,(0,255,255),-1)
            cv2.putText(detections,str(key),(xy[0]+5,xy[1]-5),cv2.FONT_HERSHEY_SIMPLEX,.4,(0,255,255),1)
        if not cv2.imwrite(str(args.output/'detections.png'),detections):
            raise RuntimeError('Could not save native observations')
        report['source_unchanged_verified'] = digest(image_path) == input_hash
        if not report['source_unchanged_verified']:
            raise ValueError('Input changed during inference')
    except Exception as exc:
        report.update(inference_outcome='error', error_category='oom' if 'out of memory' in str(exc).lower() else phase,
                      error=str(exc), traceback=traceback.format_exc())
    finally:
        report['worker_seconds'] = time.perf_counter()-started
        try:
            safe = serializable(report)
        except ValueError as exc:
            # Preserve the error rather than writing NaN as an apparently valid candidate.
            safe = {k: report[k] for k in ('schema_version','sample_id','source','engine_id','upstream_commit',
                                           'checkpoint_sha256','config','config_sha256','manual_prompt_count')}
            safe.update(inference_outcome='error', error_category='invalid_output',error=str(exc),
                        quality_state='unassessed',field_model_status='unverified',metric_eligible=False)
        (args.output/'sidecar.json').write_text(json.dumps(safe,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return safe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--weights-kp', type=Path, required=True)
    parser.add_argument('--weights-line', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sample-id')
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--kp-threshold', type=float, default=.3434)
    parser.add_argument('--line-threshold', type=float, default=.7867)
    parser.add_argument('--pnl-refine', action='store_true')
    result = run(parser.parse_args())
    print(json.dumps({k: result.get(k) for k in ('sample_id','inference_outcome','processing_seconds','peak_vram_allocated_bytes','error')}))
    return 1 if result['inference_outcome']=='error' else 0


if __name__ == '__main__':
    raise SystemExit(main())
