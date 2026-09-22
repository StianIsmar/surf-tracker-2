import json
import subprocess
import threading
from pathlib import Path

import pytest

from surf_detector import (
    Detection,
    ExportCancelled,
    Track,
    find_rides,
    merge_intervals,
    run_export_command,
)
import review_server
from prepare_yolo_dataset import choose_split, yolo_lines
from infer_surf_video import build_ride_intervals
from review_server import (
    load_ground_truth,
    load_review_data,
    load_model_review_data,
    load_training_annotations,
    register_session_video,
    save_ground_truth,
    save_model_label,
    merge_kept_clips,
    save_training_annotation,
)
from training_frames import build_frame_samples


def test_track_directionality_distinguishes_travel_from_jitter() -> None:
    travel = Track(
        detections=[Detection(time, x, 400, 10, 25) for time, x in enumerate((0, 50, 100, 150))]
    )
    jitter = Track(
        detections=[Detection(time, x, 400, 10, 25) for time, x in enumerate((0, 50, 0, 50))]
    )

    assert travel.directionality == 1.0
    assert jitter.directionality == pytest.approx(1 / 3)


def test_merge_happens_before_padding() -> None:
    rides = merge_intervals([(10, 12), (16, 18)], video_duration=30, padding=3)

    assert rides == [
        {"start": 7, "end": 15, "duration": 8},
        {"start": 13, "end": 21, "duration": 8},
    ]


def test_known_example_contains_one_ride() -> None:
    rides = find_rides(Path("onewave.mp4"))

    assert rides == [{"start": 0.0, "end": 11.08, "duration": 11.08}]


def test_review_data_matches_exported_clips() -> None:
    data = load_review_data()

    assert len(data["clips"]) == 21
    assert data["clips"][0]["clip"] == 1
    assert data["clips"][0]["ride"] is True


def test_ground_truth_defaults_to_an_empty_review() -> None:
    data = load_ground_truth()

    assert data["source"] == "wholeclip.mp4"
    assert isinstance(data["rides"], list)


def test_ground_truth_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "ground_truth.json"
    monkeypatch.setattr(review_server, "GROUND_TRUTH_PATH", path)

    save_ground_truth([{"start": 10.5, "end": 18.25, "note": "clean ride"}])

    assert load_ground_truth()["rides"] == [
        {"start": 10.5, "end": 18.25, "note": "clean ride"}
    ]


def test_training_samples_include_rides_and_exclude_nearby_negatives() -> None:
    rides = [{"start": 10.0, "end": 12.0}]
    candidates = [
        {"start": 7.0, "end": 10.5},
        {"start": 20.0, "end": 24.0},
    ]
    labels = [
        {"clip": 1, "ride": False},
        {"clip": 2, "ride": False},
    ]

    samples = build_frame_samples(rides, candidates, labels)

    positives = [sample for sample in samples if sample.label == "ride"]
    negatives = [sample for sample in samples if sample.label == "non_ride"]
    assert len(positives) == 4
    assert negatives
    assert all(not 9.0 <= sample.timestamp <= 13.0 for sample in negatives)
    assert any(sample.source == "rejected_clip_2" for sample in negatives)


def test_training_annotation_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    training_path = tmp_path / "training_data"
    training_path.mkdir()
    (training_path / "manifest.json").write_text(
        '{"source":"video.mp4","samples":[{"timestamp":1,"label":"ride",'
        '"source":"confirmed_ride","file":"frames/ride.jpg","crop":{}}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(review_server, "TRAINING_DATA_PATH", training_path)
    monkeypatch.setattr(review_server, "TRAINING_ANNOTATIONS_PATH", tmp_path / "annotations.json")

    save_training_annotation(
        "ride.jpg", [{"x": 0.1, "y": 0.2, "width": 0.05, "height": 0.3}]
    )

    sample = load_training_annotations()["samples"][0]
    assert sample["reviewed"] is True
    assert sample["boxes"] == [{"x": 0.1, "y": 0.2, "width": 0.05, "height": 0.3}]


def test_yolo_conversion_and_whole_ride_split() -> None:
    rides = [
        {"start": 1.0, "end": 2.0},
        {"start": 3.0, "end": 4.0},
        {"start": 5.0, "end": 6.0},
        {"start": 7.0, "end": 8.0},
    ]
    sample = {"label": "ride", "timestamp": 7.5, "source": "confirmed_ride"}
    box = {"x": 0.1, "y": 0.2, "width": 0.2, "height": 0.4}

    assert choose_split(sample, rides) == "val"
    assert yolo_lines([box]) == ["0 0.200000 0.400000 0.200000 0.400000"]


def test_model_detections_require_temporal_support() -> None:
    intervals = build_ride_intervals(
        [1.0, 10.0, 10.5, 12.0, 20.0, 20.5],
        video_duration=30.0,
        max_gap=1.5,
        minimum_hits=2,
    )

    assert intervals == [
        {"start": 8.0, "end": 14.0, "duration": 6.0, "detections": 3},
        {"start": 18.0, "end": 22.5, "duration": 4.5, "detections": 2},
    ]


def test_model_detections_merge_overlapping_padded_intervals() -> None:
    intervals = build_ride_intervals(
        [10.0, 10.5, 11.0, 11.5, 14.0, 14.5, 15.0, 15.5],
        video_duration=30.0,
        max_gap=0.6,
        minimum_hits=4,
        padding=2.0,
    )

    assert intervals == [
        {"start": 8.0, "end": 17.5, "duration": 9.5, "detections": 8}
    ]


def test_model_feedback_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results_path = tmp_path / "model_rides.json"
    results_path.write_text(
        '{"source":"session.mp4","rides":[{"start":1,"end":3,"duration":2,"detections":4}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(review_server, "MODEL_RIDES_PATH", results_path)
    monkeypatch.setattr(review_server, "MODEL_FEEDBACK_PATH", tmp_path / "feedback.json")
    monkeypatch.setattr(review_server, "ACTIVE_SESSION_PATH", tmp_path / "missing-session.json")

    save_model_label(1, True, "clean ride")

    clip = load_model_review_data()["clips"][0]
    assert clip["ride"] is True
    assert clip["note"] == "clean ride"


def test_model_review_merges_overlaps_and_saves_all_raw_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results_path = tmp_path / "model_rides.json"
    results_path.write_text(
        '{"source":"session.mp4","rides":['
        '{"start":10,"end":15,"duration":5,"detections":4},'
        '{"start":14,"end":18,"duration":4,"detections":6},'
        '{"start":30,"end":34,"duration":4,"detections":5}]}',
        encoding="utf-8",
    )
    feedback_path = tmp_path / "feedback.json"
    monkeypatch.setattr(review_server, "MODEL_RIDES_PATH", results_path)
    monkeypatch.setattr(review_server, "MODEL_FEEDBACK_PATH", feedback_path)
    monkeypatch.setattr(review_server, "ACTIVE_SESSION_PATH", tmp_path / "missing-session.json")

    clips = load_model_review_data()["clips"]
    assert [(clip["start"], clip["end"], clip["detections"]) for clip in clips] == [
        (10.0, 18.0, 10),
        (30.0, 34.0, 5),
    ]

    save_model_label(1, True, "one continuous ride")

    saved = json.loads(feedback_path.read_text(encoding="utf-8"))
    assert saved["labels"] == [
        {"clip": 1, "ride": True, "note": "one continuous ride"},
        {"clip": 2, "ride": True, "note": "one continuous ride"},
    ]


def test_kept_clips_merge_overlaps_without_joining_separate_rides() -> None:
    clips = [
        {"start": 10.0, "end": 15.0},
        {"start": 14.0, "end": 18.0},
        {"start": 20.0, "end": 22.0},
    ]

    assert merge_kept_clips(clips) == [
        {"start": 10.0, "end": 18.0, "duration": 8.0},
        {"start": 20.0, "end": 22.0, "duration": 2.0},
    ]


def test_model_export_worker_reports_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    session_path = tmp_path / "session.json"
    session_path.write_text(
        json.dumps({"source_path": str(source_path), "results_path": "results.json"}),
        encoding="utf-8",
    )
    stages = []

    output_path = tmp_path / "chosen-output.mp4"

    def fake_export(source, output, progress_callback=None, cancel_event=None):
        assert source == source_path
        assert output == output_path
        assert cancel_event is review_server.EXPORT_CANCEL
        progress_callback("encoding", 0.5)
        stages.append(dict(review_server.EXPORT_STATUS))
        progress_callback("joining", 0.95)
        return {
            "url": "/model-export",
            "duration": 42.0,
            "size": 2048,
            "output_path": str(output_path),
        }

    monkeypatch.setattr(review_server, "ACTIVE_SESSION_PATH", session_path)
    monkeypatch.setattr(review_server, "export_model_compilation", fake_export)
    review_server.EXPORT_STATUS.clear()
    review_server.EXPORT_STATUS.update(state="preparing", progress=0.0)

    review_server.run_model_export(output_path)

    assert stages == [{"state": "encoding", "progress": 0.5}]
    assert review_server.EXPORT_STATUS == {
        "state": "complete",
        "progress": 1.0,
        "error": None,
        "url": "/model-export",
        "duration": 42.0,
        "size": 2048,
        "output_path": str(output_path),
    }


def test_model_export_worker_reports_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    session_path = tmp_path / "session.json"
    session_path.write_text(
        json.dumps({"source_path": str(source_path), "results_path": "results.json"}),
        encoding="utf-8",
    )

    def fake_export(*args, **kwargs):
        raise review_server.ExportCancelled("Export stopped")

    monkeypatch.setattr(review_server, "ACTIVE_SESSION_PATH", session_path)
    monkeypatch.setattr(review_server, "export_model_compilation", fake_export)
    review_server.EXPORT_STATUS.clear()
    review_server.EXPORT_STATUS.update(state="stopping", progress=0.5)

    review_server.run_model_export(tmp_path / "chosen-output.mp4")

    assert review_server.EXPORT_STATUS == {"state": "cancelled", "progress": 0.0}


def test_export_cancellation_terminates_active_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel_event = threading.Event()

    class FakeProcess:
        terminated = False

        def wait(self, timeout=None):
            if not self.terminated:
                cancel_event.set()
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            return 0

        def terminate(self):
            self.terminated = True

        def kill(self):
            raise AssertionError("A responsive process should not be killed")

    process = FakeProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda command: process)

    with pytest.raises(ExportCancelled, match="Export stopped"):
        run_export_command(["ffmpeg"], cancel_event)

    assert process.terminated is True


def test_register_session_video_keeps_original_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "new-session.mp4"
    source_path.write_bytes(b"original video data")
    session_path = tmp_path / "active_session.json"

    class FakeCapture:
        def isOpened(self) -> bool:
            return True

        def get(self, property_id: int) -> float:
            values = {
                review_server.cv2.CAP_PROP_FPS: 25.0,
                review_server.cv2.CAP_PROP_FRAME_COUNT: 1500.0,
                review_server.cv2.CAP_PROP_FRAME_WIDTH: 1920.0,
                review_server.cv2.CAP_PROP_FRAME_HEIGHT: 1080.0,
            }
            return values[property_id]

        def release(self) -> None:
            pass

    monkeypatch.setattr(review_server, "ACTIVE_SESSION_PATH", session_path)
    monkeypatch.setattr(review_server.cv2, "VideoCapture", lambda _: FakeCapture())

    session = register_session_video(source_path)

    assert session["source_path"] == str(source_path.resolve())
    assert session["duration"] == 60.0
    assert list(tmp_path.glob("*.mp4")) == [source_path]
    assert json.loads(session_path.read_text())["source_path"] == str(source_path.resolve())


def test_current_model_scan_runs_inference_and_prepares_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "session.mp4"
    source_path.write_bytes(b"video")
    model_path = tmp_path / "best.pt"
    model_path.write_bytes(b"model")
    session_path = tmp_path / "active_session.json"
    results_path = tmp_path / "active_model_rides.json"
    feedback_path = tmp_path / "active_model_feedback.json"
    session_path.write_text(
        json.dumps({"source_path": str(source_path), "name": source_path.name}),
        encoding="utf-8",
    )
    calls = []

    def fake_detect(video: Path, model: Path, progress_callback):
        calls.append((video, model))
        progress_callback(0.5)
        return {"source": video.name, "rides": [{"start": 1, "end": 3}]}

    monkeypatch.setattr(review_server, "ACTIVE_SESSION_PATH", session_path)
    monkeypatch.setattr(review_server, "ACTIVE_MODEL_RIDES_PATH", results_path)
    monkeypatch.setattr(review_server, "ACTIVE_MODEL_FEEDBACK_PATH", feedback_path)
    monkeypatch.setattr(review_server, "BASE_MODEL_PATH", model_path)
    monkeypatch.setattr(review_server, "detect_rides", fake_detect)

    review_server.run_current_model_scan()

    saved_session = json.loads(session_path.read_text())
    assert calls == [(source_path, model_path)]
    assert saved_session["status"] == "review"
    assert saved_session["results_path"] == str(results_path)
    assert json.loads(results_path.read_text())["source"] == source_path.name