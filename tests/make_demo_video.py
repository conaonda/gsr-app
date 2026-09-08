"""Generate an explicitly synthetic textured pitch for local integration QA."""
from pathlib import Path
import cv2
import numpy as np


def make_video(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    base = np.zeros((540, 960, 3), dtype=np.uint8)
    base[:] = (42, 91, 38)
    noise = rng.integers(-12, 13, base.shape, dtype=np.int16)
    base = np.clip(base.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    color = (210, 235, 210)
    cv2.rectangle(base, (100, 80), (860, 460), color, 3)
    cv2.line(base, (480, 80), (480, 460), color, 3)
    cv2.circle(base, (480, 270), 60, color, 3)
    cv2.putText(base, 'SYNTHETIC QA - NOT MATCH FOOTAGE', (120, 35), cv2.FONT_HERSHEY_SIMPLEX, .65, color, 1)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (960, 540))
    if not writer.isOpened():
        raise RuntimeError('Video encoder unavailable')
    for frame in range(30):
        transform = np.float32([[1, 0, frame * .35], [0, 1, frame * .15]])
        writer.write(cv2.warpAffine(base, transform, (960, 540)))
    writer.release()
    return path


if __name__ == '__main__':
    print(make_video(Path(__file__).resolve().parents[1] / 'data' / 'synthetic-qa.mp4'))
