"""Bounded exact-frame cache and PTS-checked accelerated extraction.

The decoded index remains authoritative. Seeking is enabled only for an
unambiguous original-PTS index, and the filter must emit that exact timestamp.
Other sources keep the original sequential decoder.
"""
from collections import OrderedDict
from concurrent.futures import Future
import threading
import struct
from typing import Callable

import numpy as np
import cv2

from .media import FFMPEG, MediaError, VideoInfo, _run_tool, extract_frame


def _seek_prefix(info: VideoInfo, index: int) -> tuple[str, ...] | None:
    frame = info.frames[index]
    pts = [item.pts for item in info.frames]
    safe = (
        frame.time_seconds is not None and frame.time_seconds >= 0
        and all(item.pts_source == 'original_pts' and item.pts is not None
                for item in info.frames)
        and pts[0] >= 0
        and all(value < 2**53 for value in pts)
        and all(a < b for a, b in zip(pts, pts[1:]))
    )
    if safe:
        # Absolute input timestamp, with copyts preserving the decoder's time
        # base. A keyframe landing alone is never accepted as the target.
        seek = max(info.frames[0].time_seconds or 0.0, frame.time_seconds - 1.0)
        return ('-copyts', '-seek_timestamp', '1', '-ss', f'{seek:.9f}', '-noaccurate_seek')
    return None


def extract_indexed_frame(info: VideoInfo, index: int) -> bytes:
    frame = info.frames[index]
    prefix = _seek_prefix(info, index)
    if prefix is not None and index > 0:
        try:
            result = _run_tool((
                FFMPEG, '-v', 'error', '-nostdin', *prefix,
                '-i', str(info.path), '-map', '0:v:0',
                '-vf', f'select=eq(pts\\,{frame.pts})',
                '-frames:v', '1', '-fps_mode', 'vfr',
                '-f', 'image2pipe', '-c:v', 'png', 'pipe:1',
            ), timeout=15)
            if result.returncode == 0 and result.stdout.startswith(b'\x89PNG\r\n\x1a\n'):
                return bytes(result.stdout)
        except MediaError:
            pass
    return extract_frame(info.path, index)


def iter_indexed_frames(info: VideoInfo, first: int, last: int, max_bytes: int = 64 * 1024 * 1024):
    """Decode a short contiguous range once per bounded raw-frame window.

    Windows also support backward tracking by reversing their decoded order.
    No decoder is left running when the consumer stops or a tracking gate fails.
    """
    if min(first, last) < 0 or max(first, last) >= len(info.frames) or abs(last-first) > 30:
        raise MediaError('Frame provider supports at most 30 adjacent steps in the indexed video')
    frame_bytes = info.width * info.height * 3
    window = max(1, min(31, max_bytes // frame_bytes))
    direction = 1 if last >= first else -1
    indices = list(range(first, last + direction, direction))
    for offset in range(0, len(indices), window):
        selected = indices[offset:offset+window]
        low, high = min(selected), max(selected)
        count = len(selected)
        prefix = _seek_prefix(info, low)
        decoded = None
        attempts = []
        if prefix is not None:
            attempts.append((prefix, f'select=between(pts\\,{info.frames[low].pts}\\,{info.frames[high].pts})', 30))
        attempts.append(((), f'select=between(n\\,{low}\\,{high})', None))
        for options, selector, timeout in attempts:
            try:
                result = _run_tool((FFMPEG, '-v', 'error', '-nostdin', *options,
                    '-i', str(info.path), '-map', '0:v:0', '-vf', selector,
                    '-frames:v', str(count), '-fps_mode', 'vfr',
                    '-f', 'image2pipe', '-c:v', 'png', 'pipe:1'), timeout=timeout)
                if result.returncode == 0:
                    decoded = _decode_png_sequence(result.stdout, count, info.width, info.height)
                    if decoded is not None:
                        break
            except MediaError:
                continue
        if decoded is None:
            raise MediaError('Could not decode the complete indexed frame window')
        for index in selected:
            yield decoded[index-low]


def _decode_png_sequence(data: bytes, count: int, width: int, height: int):
    # Preserve the reference PNG encoder's pixel format (including 16-bit PNG
    # for 10-bit sources), then use exactly the same OpenCV conversion.
    frames = []
    offset = 0
    while offset < len(data) and len(frames) < count:
        start = offset
        if data[offset:offset+8] != b'\x89PNG\r\n\x1a\n':
            return None
        offset += 8
        while offset + 12 <= len(data):
            length = struct.unpack_from('>I', data, offset)[0]
            kind = data[offset+4:offset+8]
            offset += length + 12
            if offset > len(data):
                return None
            if kind == b'IEND':
                frame = cv2.imdecode(np.frombuffer(data[start:offset], np.uint8), cv2.IMREAD_COLOR)
                if frame is None or frame.shape != (height, width, 3):
                    return None
                frames.append(frame)
                break
        else:
            return None
    return frames if offset == len(data) and len(frames) == count else None


class FrameCache:
    """Byte- and count-bounded LRU; concurrent misses for one key share work."""

    def __init__(self, max_bytes: int = 128 * 1024 * 1024, max_entries: int = 32):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.size = 0
        self._items: OrderedDict[tuple, bytes] = OrderedDict()
        self._pending: dict[tuple, Future] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple, loader: Callable[[], bytes]) -> tuple[bytes, bool]:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key], True
            future = self._pending.get(key)
            owner = future is None
            if owner:
                future = self._pending[key] = Future()
        if not owner:
            return future.result(), True
        try:
            value = loader()
            with self._lock:
                if len(value) <= self.max_bytes and self.max_entries > 0:
                    self._items[key] = value
                    self.size += len(value)
                    while self.size > self.max_bytes or len(self._items) > self.max_entries:
                        _, old = self._items.popitem(last=False)
                        self.size -= len(old)
                future.set_result(value)
            return value, False
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)
