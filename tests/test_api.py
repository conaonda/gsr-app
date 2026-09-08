from __future__ import annotations

import json
from pathlib import Path
import subprocess

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from gsr.server import create_app
from gsr.media import _frame_pts

from make_demo_video import make_video


LOOPBACK_HEADERS = {
    "Host": "127.0.0.1:8765",
    "Origin": "http://127.0.0.1:8765",
}


def _registerable_points() -> list[dict]:
    points = [
        {"image": [100, 80], "pitch": [0, 0], "role": "fit"},
        {"image": [860, 80], "pitch": [1, 0], "role": "fit"},
        {"image": [860, 460], "pitch": [1, 1], "role": "fit"},
        {"image": [100, 460], "pitch": [0, 1], "role": "fit"},
        {"image": [480, 80], "pitch": [0.5, 0], "role": "validation"},
        {"image": [480, 460], "pitch": [0.5, 1], "role": "validation"},
    ]
    return points


def _add_video(client: TestClient, path: Path) -> dict:
    response = client.post("/api/videos", json={"path": str(path)}, headers=LOOPBACK_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def test_missing_original_pts_is_explicitly_marked_as_fallback() -> None:
    assert _frame_pts({"best_effort_timestamp": "7", "best_effort_timestamp_time": "0.7"}, 0.1) == (
        7,
        0.7,
        "best_effort",
    )
    assert _frame_pts({}, 0.1) == (None, None, "missing")


def test_real_generated_video_round_trip_and_sqlite_persistence(tmp_path: Path) -> None:
    video_path = make_video(tmp_path / "synthetic.mp4")
    db_path = tmp_path / "sessions.sqlite3"

    client = TestClient(create_app(db_path))
    video = _add_video(client, video_path)
    assert video == {
        "id": video["id"],
        "name": "synthetic.mp4",
        "width": 960,
        "height": 540,
        "duration": pytest.approx(3.0),
        "time_base": video["time_base"],
    }

    frames = client.get(f"/api/videos/{video['id']}/frames").json()
    assert len(frames) == 30
    assert frames[0]["pts_source"] == "original_pts"
    assert [item["pts"] for item in frames[:3]] == [0, 1024, 2048]
    assert [item["time_seconds"] for item in frames[:3]] == pytest.approx([0.0, 0.1, 0.2])

    png_response = client.get(f"/api/videos/{video['id']}/frame/10")
    assert png_response.status_code == 200
    assert png_response.content.startswith(b"\x89PNG\r\n\x1a\n")
    image = cv2.imdecode(np.frombuffer(png_response.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None and image.shape[:2] == (540, 960)

    registration = client.post(
        f"/api/videos/{video['id']}/registrations",
        json={
            "frame_index": 0,
            "field": {
                "length": None,
                "width": None,
                "dimension_source": "",
                "dimensions_verified": False,
            },
            "points": _registerable_points(),
            "note": "synthetic QA only",
        },
        headers=LOOPBACK_HEADERS,
    )
    assert registration.status_code == 200, registration.text
    saved = registration.json()
    assert saved["status"] == "usable"
    assert saved["source"] == "manual"
    assert saved["pts_source"] == "original_pts"
    assert saved["analysis_provenance"]["engine"] == "gsr.geometry"
    assert saved["analysis_provenance"]["schema_version"] == "gsr.registration.v1"

    propagated = client.post(
        f"/api/videos/{video['id']}/propagate",
        json={"registration_id": saved["id"], "target_frame_index": 1},
        headers=LOOPBACK_HEADERS,
    )
    assert propagated.status_code == 200, propagated.text
    assert propagated.json()["status"] == "review"
    assert propagated.json()["source"] == "propagated"
    assert all(item["role"] == "fit" for item in propagated.json()["points"])
    assert propagated.json()["validation_error_px"] is None

    # A fresh app instance reads the same append-only rows.
    reopened = TestClient(create_app(db_path))
    listed = reopened.get("/api/videos").json()
    assert listed == [video]
    history = reopened.get(f"/api/videos/{video['id']}/registrations").json()
    assert [item["source"] for item in history] == ["manual", "propagated"]
    assert history[0]["pts_source"] == saved["pts_source"]
    exported = reopened.get(f"/api/videos/{video['id']}/export").json()
    assert exported["schema_version"] == "gsr.session.v1"
    assert exported["analysis_provenance"] == saved["analysis_provenance"]
    assert exported["video"] == video
    assert len(exported["frames"]) == 30
    assert len(exported["registrations"]) == 2
    # Machine-local paths stay private to the SQLite source index.
    assert str(video_path) not in json.dumps(exported)


def test_rotated_video_reports_and_serves_display_dimensions(tmp_path: Path) -> None:
    source = make_video(tmp_path / "source.mp4")
    rotated = tmp_path / "rotated.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-display_rotation:v:0",
            "90",
            "-i",
            str(source),
            "-c",
            "copy",
            str(rotated),
        ],
        check=True,
    )
    client = TestClient(create_app(tmp_path / "sessions.sqlite3"))
    video = _add_video(client, rotated)
    assert (video["width"], video["height"]) == (540, 960)
    frame = client.get(f"/api/videos/{video['id']}/frame/0")
    assert frame.status_code == 200
    image = cv2.imdecode(np.frombuffer(frame.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None and image.shape[:2] == (960, 540)


def test_mutations_require_loopback_origin_and_host(tmp_path: Path) -> None:
    video_path = make_video(tmp_path / "secure.mp4")
    client = TestClient(create_app(tmp_path / "sessions.sqlite3"))

    assert client.post("/api/videos", json={"path": str(video_path)}).status_code == 403
    assert client.post(
        "/api/videos",
        json={"path": str(video_path)},
        headers={"Host": "127.0.0.1:8765", "Origin": "https://unrelated.example"},
    ).status_code == 403
    assert client.post(
        "/api/videos",
        json={"path": str(video_path)},
        headers={"Host": "example.com", "Origin": "http://127.0.0.1:8765"},
    ).status_code == 403
    assert _add_video(client, video_path)["id"] == 1


def test_invalid_inputs_are_rejected_without_writing_a_session(tmp_path: Path) -> None:
    video_path = make_video(tmp_path / "invalid.mp4")
    client = TestClient(create_app(tmp_path / "sessions.sqlite3"))
    video = _add_video(client, video_path)
    prefix = f"/api/videos/{video['id']}"

    assert client.post(
        "/api/videos",
        json={"path": "relative/video.mp4"},
        headers=LOOPBACK_HEADERS,
    ).status_code == 400
    assert client.get(f"{prefix}/frame/999").status_code == 404
    bad_points = _registerable_points()
    bad_points[0] = {"image": [100, 80], "pitch": [2, 0], "role": "fit"}
    response = client.post(
        f"{prefix}/registrations",
        json={
            "frame_index": 0,
            "field": {"dimensions_verified": False},
            "points": bad_points,
        },
        headers=LOOPBACK_HEADERS,
    )
    assert response.status_code == 422
    assert isinstance(response.json().get("detail"), str)
    assert client.get(f"{prefix}/registrations").json() == []


def test_persisted_source_identity_rejects_a_changed_file(tmp_path: Path) -> None:
    video_path = make_video(tmp_path / "changed.mp4")
    client = TestClient(create_app(tmp_path / "sessions.sqlite3"))
    video = _add_video(client, video_path)
    video_path.write_bytes(video_path.read_bytes() + b"changed")
    response = client.get(f"/api/videos/{video['id']}/frames")
    assert response.status_code == 409
    assert "changed" in response.json()["detail"].lower()
