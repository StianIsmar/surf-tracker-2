from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import cv2

from infer_surf_video import detect_rides
from training_frames import extract_training_frames
from surf_detector import ExportCancelled, export_rides, run_export_command
from youtube_upload import YouTubePublisher


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
CLIPS_ROOT = ROOT / "clips"
FEEDBACK_PATH = ROOT / "feedback.json"
RIDES_PATH = ROOT / "rides.json"
GROUND_TRUTH_PATH = ROOT / "ground_truth.json"
TRAINING_DATA_PATH = ROOT / "training_data"
TRAINING_ANNOTATIONS_PATH = ROOT / "training_annotations.json"
MODEL_RIDES_PATH = ROOT / "model_rides_2hrs.json"
MODEL_FEEDBACK_PATH = ROOT / "model_feedback_2hrs.json"
MODEL_EXPORT_PATH = ROOT / "surf_rides_compilation.mp4"
ACTIVE_SESSION_PATH = ROOT / "active_session.json"
ACTIVE_MODEL_RIDES_PATH = ROOT / "active_model_rides.json"
ACTIVE_MODEL_FEEDBACK_PATH = ROOT / "active_model_feedback.json"
BASE_MODEL_PATH = ROOT / "runs" / "surf_mvp" / "weights" / "best.pt"
YOUTUBE_TEMP_PATH = ROOT / ".youtube_publish.mp4"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
SCAN_LOCK = threading.Lock()
SCAN_STATUS: dict[str, object] = {"state": "idle", "progress": 0.0}
EXPORT_LOCK = threading.Lock()
EXPORT_STATUS: dict[str, object] = {"state": "idle", "progress": 0.0}
EXPORT_CANCEL = threading.Event()
PUBLISH_LOCK = threading.RLock()
PUBLISH_STATUS: dict[str, object] = {"state": "idle", "progress": 0.0}
YOUTUBE_PUBLISHER = YouTubePublisher()


def reset_publish_artifacts() -> None:
    with PUBLISH_LOCK:
        if PUBLISH_STATUS.get("state") in ("encoding", "uploading"):
            raise ValueError("Wait for YouTube publishing to finish")
        YOUTUBE_TEMP_PATH.unlink(missing_ok=True)
        PUBLISH_STATUS.clear()
        PUBLISH_STATUS.update(state="idle", progress=0.0)


def save_active_session(session: dict[str, object]) -> None:
    temporary_path = ACTIVE_SESSION_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_path, ACTIVE_SESSION_PATH)


def register_session_video(video_path: Path) -> dict[str, object]:
    source_path = video_path.expanduser().resolve(strict=True)
    if not source_path.is_file() or source_path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Choose a supported video file")

    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        raise ValueError("The selected video could not be opened")
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        capture.release()
    if fps <= 0 or frame_count <= 0:
        raise ValueError("The selected video has invalid timing information")

    session = {
        "source_path": str(source_path),
        "name": source_path.name,
        "size": source_path.stat().st_size,
        "duration": round(frame_count / fps, 3),
        "width": width,
        "height": height,
        "status": "ready",
    }
    with PUBLISH_LOCK:
        reset_publish_artifacts()
        save_active_session(session)
    return session


def load_active_session() -> dict[str, object] | None:
    if not ACTIVE_SESSION_PATH.exists():
        return None
    session = json.loads(ACTIVE_SESSION_PATH.read_text(encoding="utf-8"))
    source_path = Path(str(session.get("source_path", "")))
    if not source_path.is_file():
        return None
    return session


def model_review_paths() -> tuple[Path, Path]:
    session = load_active_session()
    if session and session.get("results_path") and session.get("feedback_path"):
        return Path(str(session["results_path"])), Path(str(session["feedback_path"]))
    return MODEL_RIDES_PATH, MODEL_FEEDBACK_PATH


def run_current_model_scan() -> None:
    session = load_active_session()
    if session is None:
        raise ValueError("Choose a video before starting detection")
    if not BASE_MODEL_PATH.is_file():
        raise ValueError("The trained surf model is missing")

    def report(progress: float) -> None:
        with SCAN_LOCK:
            SCAN_STATUS.update(state="running", progress=round(progress, 4))

    result = detect_rides(
        Path(str(session["source_path"])), BASE_MODEL_PATH, progress_callback=report
    )
    temporary_path = ACTIVE_MODEL_RIDES_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_path, ACTIVE_MODEL_RIDES_PATH)
    ACTIVE_MODEL_FEEDBACK_PATH.unlink(missing_ok=True)
    session["results_path"] = str(ACTIVE_MODEL_RIDES_PATH)
    session["feedback_path"] = str(ACTIVE_MODEL_FEEDBACK_PATH)
    session["status"] = "review"
    save_active_session(session)
    with SCAN_LOCK:
        SCAN_STATUS.update(
            state="complete", progress=1.0, candidates=len(result["rides"]), error=None
        )


def start_current_model_scan() -> None:
    with SCAN_LOCK:
        if SCAN_STATUS["state"] == "running":
            raise ValueError("Detection is already running")
        SCAN_STATUS.clear()
        SCAN_STATUS.update(state="running", progress=0.0)

    def worker() -> None:
        try:
            run_current_model_scan()
        except Exception as error:
            with SCAN_LOCK:
                SCAN_STATUS.update(state="failed", error=str(error))

    threading.Thread(target=worker, name="surf-detection", daemon=True).start()


def run_youtube_publish(title: str, description: str) -> None:
    session = load_active_session()
    source_path = (
        Path(str(session["source_path"]))
        if session and session.get("results_path")
        else ROOT / "2hrsvid.mp4"
    )
    try:
        if not YOUTUBE_TEMP_PATH.is_file():
            with PUBLISH_LOCK:
                PUBLISH_STATUS.update(state="encoding", progress=0.0)
            export_model_compilation(source_path, YOUTUBE_TEMP_PATH)
        with PUBLISH_LOCK:
            PUBLISH_STATUS.update(state="uploading", progress=0.0)

        def report(progress: float) -> None:
            with PUBLISH_LOCK:
                PUBLISH_STATUS.update(state="uploading", progress=round(progress, 4))

        result = YOUTUBE_PUBLISHER.upload(
            YOUTUBE_TEMP_PATH, title, description, progress_callback=report
        )
        YOUTUBE_TEMP_PATH.unlink(missing_ok=True)
        with PUBLISH_LOCK:
            PUBLISH_STATUS.update(state="published", progress=1.0, error=None, **result)
    except Exception as error:
        with PUBLISH_LOCK:
            PUBLISH_STATUS.update(state="failed", error=str(error))


def start_youtube_publish(title: str, description: str) -> None:
    title = title.strip()
    description = description.strip()
    if not YOUTUBE_PUBLISHER.connected():
        raise ValueError("Connect your YouTube account first")
    if not title or len(title) > 100 or len(description) > 5000:
        raise ValueError("Enter a title up to 100 characters and a description up to 5000")
    with PUBLISH_LOCK:
        if PUBLISH_STATUS["state"] in ("encoding", "uploading"):
            raise ValueError("A YouTube publish is already running")
        PUBLISH_STATUS.clear()
        PUBLISH_STATUS.update(state="encoding", progress=0.0)
    threading.Thread(
        target=run_youtube_publish,
        args=(title, description),
        name="youtube-publish",
        daemon=True,
    ).start()


def merge_kept_clips(clips: list[dict[str, object]]) -> list[dict[str, float]]:
    merged: list[list[float]] = []
    for clip in sorted(clips, key=lambda item: float(item["start"])):
        start = float(clip["start"])
        end = float(clip["end"])
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [
        {"start": round(start, 2), "end": round(end, 2), "duration": round(end - start, 2)}
        for start, end in merged
    ]


def merge_candidate_rides(
    rides: list[dict[str, object]],
) -> list[tuple[dict[str, object], list[int]]]:
    merged: list[tuple[dict[str, object], list[int]]] = []
    indexed_rides = sorted(
        enumerate(rides, start=1), key=lambda item: float(item[1]["start"])
    )
    for raw_clip, ride in indexed_rides:
        start = float(ride["start"])
        end = float(ride["end"])
        if merged and start <= float(merged[-1][0]["end"]):
            candidate, raw_clips = merged[-1]
            candidate["end"] = round(max(float(candidate["end"]), end), 2)
            candidate["duration"] = round(
                float(candidate["end"]) - float(candidate["start"]), 2
            )
            candidate["detections"] = int(candidate.get("detections", 0)) + int(
                ride.get("detections", 0)
            )
            raw_clips.append(raw_clip)
        else:
            candidate = dict(ride)
            candidate["start"] = round(start, 2)
            candidate["end"] = round(end, 2)
            candidate["duration"] = round(end - start, 2)
            merged.append((candidate, [raw_clip]))
    return merged


def export_model_compilation(
    source_path: Path,
    output_path: Path,
    progress_callback: Callable[[str, float], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, object]:
    review = load_model_review_data()
    clips = review["clips"]
    if any(clip["ride"] is None for clip in clips):
        raise ValueError("Review every candidate before exporting")
    kept = [clip for clip in clips if clip["ride"] is True]
    if not kept:
        raise ValueError("Keep at least one candidate before exporting")
    intervals = merge_kept_clips(kept)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("FFmpeg is required for export")

    with tempfile.TemporaryDirectory(prefix="surf-export-", dir=ROOT) as temporary:
        temporary_path = Path(temporary)
        segments_path = temporary_path / "segments"
        if progress_callback is not None:
            progress_callback("encoding", 0.0)
        export_rides(
            source_path,
            intervals,
            segments_path,
            progress_callback=(
                lambda progress: progress_callback("encoding", progress * 0.95)
                if progress_callback is not None
                else None
            ),
            cancel_event=cancel_event,
        )
        segments = sorted(segments_path.glob("*.mp4"))
        if progress_callback is not None:
            progress_callback("joining", 0.95)
        concat_path = temporary_path / "concat.txt"
        concat_path.write_text(
            "".join(f"file '{segment.as_posix()}'\n" for segment in segments), encoding="utf-8"
        )
        temporary_output = temporary_path / output_path.name
        run_export_command(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_path),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(temporary_output),
            ],
            cancel_event,
        )
        os.replace(temporary_output, output_path)
        if progress_callback is not None:
            progress_callback("complete", 1.0)
    return {
        "file": output_path.name,
        "url": "/model-export",
        "kept_candidates": len(kept),
        "segments": len(intervals),
        "duration": round(sum(interval["duration"] for interval in intervals), 2),
        "size": output_path.stat().st_size,
        "output_path": str(output_path),
    }

def run_model_export(output_path: Path = MODEL_EXPORT_PATH) -> None:
    session = load_active_session()
    source_path = (
        Path(str(session["source_path"]))
        if session and session.get("results_path")
        else ROOT / "2hrsvid.mp4"
    )

    def report(stage: str, progress: float) -> None:
        with EXPORT_LOCK:
            if not EXPORT_CANCEL.is_set():
                EXPORT_STATUS.update(state=stage, progress=round(progress, 4))

    try:
        result = export_model_compilation(
            source_path,
            output_path,
            progress_callback=report,
            cancel_event=EXPORT_CANCEL,
        )
        with EXPORT_LOCK:
            EXPORT_STATUS.clear()
            EXPORT_STATUS.update(state="complete", progress=1.0, error=None, **result)
    except ExportCancelled:
        with EXPORT_LOCK:
            EXPORT_STATUS.clear()
            EXPORT_STATUS.update(state="cancelled", progress=0.0)
    except Exception as error:
        with EXPORT_LOCK:
            EXPORT_STATUS.update(state="failed", error=str(error))


def validate_export_path(value: str | None) -> Path:
    if value is None:
        return MODEL_EXPORT_PATH
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("Invalid export destination")
    output_path = Path(value).expanduser()
    if not output_path.is_absolute() or output_path.suffix.lower() != ".mp4":
        raise ValueError("Choose an absolute .mp4 export destination")
    output_path = output_path.resolve()
    if not output_path.parent.is_dir():
        raise ValueError("The export folder does not exist")
    session = load_active_session()
    source_path = (
        Path(str(session["source_path"])).resolve()
        if session and session.get("results_path")
        else (ROOT / "2hrsvid.mp4").resolve()
    )
    if output_path == source_path:
        raise ValueError("Choose a destination other than the source video")
    return output_path


def start_model_export(output_path: Path = MODEL_EXPORT_PATH) -> None:
    with EXPORT_LOCK:
        if EXPORT_STATUS.get("state") in ("preparing", "encoding", "joining", "stopping"):
            raise ValueError("An export is already running")
        EXPORT_CANCEL.clear()
        EXPORT_STATUS.clear()
        EXPORT_STATUS.update(
            state="preparing", progress=0.0, output_path=str(output_path)
        )
    threading.Thread(
        target=run_model_export,
        args=(output_path,),
        name="model-export",
        daemon=True,
    ).start()


def cancel_model_export() -> None:
    with EXPORT_LOCK:
        if EXPORT_STATUS.get("state") not in ("preparing", "encoding", "joining"):
            raise ValueError("No export is running")
        EXPORT_CANCEL.set()
        EXPORT_STATUS.update(state="stopping")


def load_model_review_data() -> dict[str, object]:
    results_path, feedback_path = model_review_paths()
    results = json.loads(results_path.read_text(encoding="utf-8"))
    feedback = {"labels": []}
    if feedback_path.exists():
        feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
    labels = {item["clip"]: item for item in feedback.get("labels", [])}
    clips = []
    for index, (ride, raw_clips) in enumerate(
        merge_candidate_rides(results.get("rides", [])), start=1
    ):
        group_labels = [labels[raw_clip] for raw_clip in raw_clips if raw_clip in labels]
        if any(label.get("ride") is True for label in group_labels):
            decision = True
        elif len(group_labels) == len(raw_clips) and all(
            label.get("ride") is False for label in group_labels
        ):
            decision = False
        else:
            decision = None
        notes = list(
            dict.fromkeys(
                label.get("note", "").strip()
                for label in group_labels
                if label.get("note", "").strip()
            )
        )
        clips.append(
            {
                "clip": index,
                "start": ride["start"],
                "end": ride["end"],
                "duration": ride["duration"],
                "detections": ride["detections"],
                "ride": decision,
                "note": "\n".join(notes),
            }
        )
    return {"source": results.get("source", "2hrsvid.mp4"), "clips": clips}


def save_model_label(clip_number: int, ride: bool, note: str) -> None:
    with PUBLISH_LOCK:
        reset_publish_artifacts()
        results_path, feedback_path = model_review_paths()
        results = json.loads(results_path.read_text(encoding="utf-8"))
        data = {"source": results.get("source", "video.mp4"), "labels": []}
        if feedback_path.exists():
            data = json.loads(feedback_path.read_text(encoding="utf-8"))
        groups = merge_candidate_rides(results.get("rides", []))
        if clip_number < 1 or clip_number > len(groups):
            raise ValueError("Unknown clip")
        raw_clips = groups[clip_number - 1][1]
        labels = [
            label for label in data.setdefault("labels", []) if label.get("clip") not in raw_clips
        ]
        for raw_clip in raw_clips:
            label: dict[str, object] = {"clip": raw_clip, "ride": ride}
            if note:
                label["note"] = note
            labels.append(label)
        data["labels"] = labels
        labels.sort(key=lambda item: item["clip"])
        temporary_path = feedback_path.with_suffix(".json.tmp")
        temporary_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary_path, feedback_path)


def load_training_annotations() -> dict[str, object]:
    manifest_path = TRAINING_DATA_PATH / "manifest.json"
    if not manifest_path.exists():
        return {"source": "wholeclip.mp4", "samples": []}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    saved = {"annotations": {}}
    if TRAINING_ANNOTATIONS_PATH.exists():
        saved = json.loads(TRAINING_ANNOTATIONS_PATH.read_text(encoding="utf-8"))
    annotations = saved.get("annotations", {})
    samples = []
    for sample in manifest.get("samples", []):
        filename = Path(sample["file"]).name
        annotation = annotations.get(filename)
        if annotation is None and sample["label"] == "non_ride":
            annotation = {"reviewed": True, "boxes": []}
        samples.append(
            {
                **sample,
                "filename": filename,
                "image_url": f"/training-frames/{filename}",
                "reviewed": bool(annotation and annotation.get("reviewed")),
                "boxes": annotation.get("boxes", []) if annotation else [],
            }
        )
    return {"source": manifest.get("source", "wholeclip.mp4"), "samples": samples}


def save_training_annotation(filename: str, boxes: list[dict[str, float]]) -> None:
    data = {"source": "wholeclip.mp4", "annotations": {}}
    if TRAINING_ANNOTATIONS_PATH.exists():
        data = json.loads(TRAINING_ANNOTATIONS_PATH.read_text(encoding="utf-8"))
    data.setdefault("annotations", {})[filename] = {"reviewed": True, "boxes": boxes}
    temporary_path = TRAINING_ANNOTATIONS_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_path, TRAINING_ANNOTATIONS_PATH)


def load_review_data() -> dict[str, object]:
    rides = json.loads(RIDES_PATH.read_text(encoding="utf-8"))
    feedback = json.loads(FEEDBACK_PATH.read_text(encoding="utf-8"))
    labels = {item["clip"]: item for item in feedback.get("labels", [])}
    clips = []
    for index, ride in enumerate(rides, start=1):
        matches = sorted(CLIPS_ROOT.glob(f"ride_{index:03d}_*.mp4"))
        if not matches:
            continue
        label = labels.get(index, {})
        clips.append(
            {
                "clip": index,
                "url": f"/clips/{matches[0].name}",
                "start": ride["start"],
                "end": ride["end"],
                "duration": ride["duration"],
                "ride": label.get("ride"),
                "note": label.get("note", ""),
            }
        )
    return {"source": feedback.get("source", "wholeclip.mp4"), "clips": clips}


def save_label(clip_number: int, ride: bool, note: str) -> None:
    data = json.loads(FEEDBACK_PATH.read_text(encoding="utf-8"))
    labels = data.setdefault("labels", [])
    label = next((item for item in labels if item.get("clip") == clip_number), None)
    if label is None:
        label = {"clip": clip_number}
        labels.append(label)
    label["ride"] = ride
    if note:
        label["note"] = note
    else:
        label.pop("note", None)
    labels.sort(key=lambda item: item["clip"])

    temporary_path = FEEDBACK_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_path, FEEDBACK_PATH)


def load_ground_truth() -> dict[str, object]:
    if not GROUND_TRUTH_PATH.exists():
        return {"source": "wholeclip.mp4", "rides": []}
    return json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))


def save_ground_truth(rides: list[dict[str, object]]) -> None:
    data = {"source": "wholeclip.mp4", "rides": rides}
    temporary_path = GROUND_TRUTH_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_path, GROUND_TRUTH_PATH)


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "SurfReview/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/clips":
            self.send_json(load_review_data())
            return
        if path == "/api/ground-truth":
            self.send_json(load_ground_truth())
            return
        if path == "/api/training-annotations":
            self.send_json(load_training_annotations())
            return
        if path == "/api/model-clips":
            self.send_json(load_model_review_data())
            return
        if path == "/api/session":
            self.send_json({"session": load_active_session()})
            return
        if path == "/api/session-scan":
            with SCAN_LOCK:
                status = dict(SCAN_STATUS)
            self.send_json(status)
            return
        if path == "/api/model-export/status":
            with EXPORT_LOCK:
                status = dict(EXPORT_STATUS)
            self.send_json(status)
            return
        if path == "/api/youtube/status":
            with PUBLISH_LOCK:
                publish_status = dict(PUBLISH_STATUS)
            self.send_json({**YOUTUBE_PUBLISHER.public_status(), **publish_status})
            return
        if path == "/api/youtube/auth":
            try:
                self.send_json({"authorization_url": YOUTUBE_PUBLISHER.authorization_url()})
            except ValueError as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/youtube/callback":
            query = parse_qs(parsed.query)
            try:
                YOUTUBE_PUBLISHER.complete_authorization(
                    query.get("code", [""])[0], query.get("state", [""])[0]
                )
            except ValueError as error:
                self.send_html(f"YouTube connection failed: {error}", HTTPStatus.BAD_REQUEST)
                return
            self.send_html("YouTube connected. You can close this window and return to Surf Tracker.")
            return
        if path == "/source-video":
            source_path = ROOT / "wholeclip.mp4"
            if source_path.is_file():
                self.send_file(source_path)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if path == "/model-video":
            session = load_active_session()
            source_path = (
                Path(str(session["source_path"]))
                if session and session.get("results_path")
                else ROOT / "2hrsvid.mp4"
            )
            if source_path.is_file():
                self.send_file(source_path)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if path == "/model-export":
            with EXPORT_LOCK:
                export_path_value = EXPORT_STATUS.get("output_path")
            export_path = Path(str(export_path_value)) if export_path_value else None
            if export_path is not None and export_path.is_file():
                self.send_file(export_path)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if path.startswith("/clips/"):
            filename = Path(path.removeprefix("/clips/")).name
            candidate = CLIPS_ROOT / filename
            if candidate.is_file() and filename == path.removeprefix("/clips/"):
                self.send_file(candidate)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if path.startswith("/training-frames/"):
            filename = Path(path.removeprefix("/training-frames/")).name
            candidate = TRAINING_DATA_PATH / "frames" / filename
            if candidate.is_file() and filename == path.removeprefix("/training-frames/"):
                self.send_file(candidate)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        relative_path = "index.html" if path == "/" else path.lstrip("/")
        candidate = (WEB_ROOT / relative_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(WEB_ROOT.resolve()):
            self.send_file(candidate)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/ground-truth":
            self.save_ground_truth_request()
            return
        if path == "/api/training-frames":
            self.generate_training_frames_request()
            return
        if path == "/api/training-annotations":
            self.save_training_annotation_request()
            return
        if path == "/api/model-feedback":
            self.save_model_feedback_request()
            return
        if path == "/api/model-export":
            self.export_model_compilation_request()
            return
        if path == "/api/model-export/cancel":
            self.cancel_model_export_request()
            return
        if path == "/api/session-source":
            self.register_session_source_request()
            return
        if path == "/api/session-scan":
            self.start_session_scan_request()
            return
        if path == "/api/youtube/credentials":
            self.import_youtube_credentials_request()
            return
        if path == "/api/youtube/publish":
            self.start_youtube_publish_request()
            return
        if path == "/api/youtube/disconnect":
            YOUTUBE_PUBLISHER.disconnect()
            self.send_json({"disconnected": True})
            return
        if path != "/api/feedback":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(length))
            clip_number = payload["clip"]
            ride = payload["ride"]
            note = payload.get("note", "").strip()
            valid_clips = {clip["clip"] for clip in load_review_data()["clips"]}
            if type(clip_number) is not int or clip_number not in valid_clips:
                raise ValueError("Unknown clip")
            if type(ride) is not bool or not isinstance(note, str) or len(note) > 500:
                raise ValueError("Invalid feedback")
            save_label(clip_number, ride, note)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.send_json({"error": "Invalid feedback"}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"saved": True})

    def register_session_source_request(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16384:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(length))
            source_path = payload["path"]
            if not isinstance(source_path, str) or "\x00" in source_path:
                raise ValueError("Invalid video path")
            session = register_session_video(Path(source_path))
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error) or "Invalid video"}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"registered": True, "session": session})

    def export_model_compilation_request(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 16384:
                raise ValueError("Invalid export request")
            payload = json.loads(self.rfile.read(length)) if length else {}
            output_path = validate_export_path(payload.get("path"))
            start_model_export(output_path)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"started": True}, HTTPStatus.ACCEPTED)

    def cancel_model_export_request(self) -> None:
        try:
            cancel_model_export()
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"stopping": True}, HTTPStatus.ACCEPTED)

    def start_session_scan_request(self) -> None:
        try:
            start_current_model_scan()
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"started": True}, HTTPStatus.ACCEPTED)

    def import_youtube_credentials_request(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 65536:
                raise ValueError("Invalid credentials file size")
            YOUTUBE_PUBLISHER.import_client_config(json.loads(self.rfile.read(length)))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"configured": True})

    def start_youtube_publish_request(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 8192:
                raise ValueError("Invalid publish request")
            payload = json.loads(self.rfile.read(length))
            title = payload["title"]
            description = payload.get("description", "")
            if not isinstance(title, str) or not isinstance(description, str):
                raise ValueError("Invalid video details")
            start_youtube_publish(title, description)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"started": True}, HTTPStatus.ACCEPTED)

    def save_model_feedback_request(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(length))
            clip_number = payload["clip"]
            ride = payload["ride"]
            note = payload.get("note", "").strip()
            valid_clips = {clip["clip"] for clip in load_model_review_data()["clips"]}
            if type(clip_number) is not int or clip_number not in valid_clips:
                raise ValueError("Unknown clip")
            if type(ride) is not bool or not isinstance(note, str) or len(note) > 500:
                raise ValueError("Invalid feedback")
            save_model_label(clip_number, ride, note)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.send_json({"error": "Invalid feedback"}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"saved": True})

    def save_training_annotation_request(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 65536:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(length))
            filename = payload["filename"]
            boxes = payload["boxes"]
            samples = load_training_annotations()["samples"]
            valid_files = {sample["filename"] for sample in samples}
            if not isinstance(filename, str) or filename not in valid_files:
                raise ValueError("Unknown frame")
            if not isinstance(boxes, list) or len(boxes) > 20:
                raise ValueError("Invalid boxes")
            normalized = []
            for box in boxes:
                values = [box[key] for key in ("x", "y", "width", "height")]
                if any(type(value) not in (int, float) for value in values):
                    raise ValueError("Invalid box")
                x, y, width, height = (float(value) for value in values)
                if x < 0 or y < 0 or width <= 0 or height <= 0:
                    raise ValueError("Invalid box")
                if x + width > 1.000001 or y + height > 1.000001:
                    raise ValueError("Invalid box")
                normalized.append(
                    {key: round(value, 6) for key, value in zip(("x", "y", "width", "height"), (x, y, width, height))}
                )
            save_training_annotation(filename, normalized)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.send_json({"error": "Invalid annotation"}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"saved": True})

    def generate_training_frames_request(self) -> None:
        try:
            manifest = extract_training_frames(
                ROOT / "wholeclip.mp4",
                GROUND_TRUTH_PATH,
                RIDES_PATH,
                FEEDBACK_PATH,
                TRAINING_DATA_PATH,
            )
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        except (OSError, RuntimeError):
            self.send_json(
                {"error": "Training frames could not be generated"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        self.send_json({"generated": True, "counts": manifest["counts"]})

    def save_ground_truth_request(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 65536:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(length))
            rides = payload["rides"]
            if not isinstance(rides, list) or len(rides) > 1000:
                raise ValueError("Invalid rides")
            normalized = []
            for ride in rides:
                start = ride["start"]
                end = ride["end"]
                note = ride.get("note", "").strip()
                if type(start) not in (int, float) or type(end) not in (int, float):
                    raise ValueError("Invalid interval")
                if start < 0 or end <= start or end > 24 * 60 * 60:
                    raise ValueError("Invalid interval")
                if not isinstance(note, str) or len(note) > 500:
                    raise ValueError("Invalid note")
                normalized.append(
                    {"start": round(float(start), 2), "end": round(float(end), 2), "note": note}
                )
            save_ground_truth(sorted(normalized, key=lambda ride: ride["start"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.send_json({"error": "Invalid ride intervals"}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"saved": True})

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, message: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        safe_message = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        body = f"<!doctype html><title>Surf Tracker</title><p>{safe_message}</p>".encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path) -> None:
        file_size = path.stat().st_size
        start = 0
        end = file_size - 1
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            requested = range_header.removeprefix("bytes=").split("-", 1)
            start = int(requested[0]) if requested[0] else 0
            end = int(requested[1]) if requested[1] else end
            end = min(end, file_size - 1)
            if start > end:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        else:
            self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with path.open("rb") as file:
            file.seek(start)
            remaining = end - start + 1
            try:
                while remaining:
                    chunk = file.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Review detected surf clips locally.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), ReviewHandler)
    print(f"Surf review available at http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()