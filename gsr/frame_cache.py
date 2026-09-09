"""Bounded exact-frame cache and PTS-checked accelerated extraction.

The decoded index remains authoritative. Seeking is enabled only for an
unambiguous original-PTS index, and the filter must emit that exact timestamp.
Other sources keep the original sequential decoder.
"""
from collections import OrderedDict
from concurrent.futures import Future
import threading
from typing import Callable

from .media import FFMPEG, MediaError, VideoInfo, _run_tool, extract_frame


def extract_indexed_frame(info: VideoInfo, index: int) -> bytes:
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
    if safe and index > 0:
        # Absolute input timestamp, with copyts preserving the decoder's time
        # base. A keyframe landing alone is never accepted as the target.
        seek = max(info.frames[0].time_seconds or 0.0, frame.time_seconds - 1.0)
        try:
            result = _run_tool((
                FFMPEG, '-v', 'error', '-nostdin', '-copyts',
                '-seek_timestamp', '1', '-ss', f'{seek:.9f}', '-noaccurate_seek',
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
