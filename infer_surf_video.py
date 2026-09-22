from __future__ import annotations

import argparse
import json
from pathlib import Path
from collections.abc import Callable

import cv2
import torch
from ultralytics import YOLO

from training_frames import WAVE_ZONE_BOTTOM, WAVE_ZONE_TOP


ROOT = Path(__file__).resolve().parent


def build_ride_intervals(
    detection_times: list[float],
    video_duration: float,
    max_gap: float = 0.6,
    minimum_hits: int = 4,
    padding: float = 2.0,
) -> list[dict[str, float]]:
    if not detection_times:
        return []
    groups: list[list[float]] = [[detection_times[0]]]
    for timestamp in detection_times[1:]:
        if timestamp - groups[-1][-1] <= max_gap:
            groups[-1].append(timestamp)
        else:
            groups.append([timestamp])

    intervals = []
    for group in groups:
        if len(group) < minimum_hits:
            continue
        start = max(0.0, group[0] - padding)
        end = min(video_duration, group[-1] + padding)
        if intervals and start <= intervals[-1]["end"]:
            intervals[-1]["end"] = round(max(intervals[-1]["end"], end), 2)
            intervals[-1]["duration"] = round(
                intervals[-1]["end"] - intervals[-1]["start"], 2
            )
            intervals[-1]["detections"] += len(group)
        else:
            intervals.append(
                {
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "duration": round(end - start, 2),
                    "detections": len(group),
                }
            )
    return intervals


def detect_rides(
    video_path: Path,
    model_path: Path,
    analysis_fps: float = 2.0,
    confidence: float = 0.4,
    batch_size: int = 4,
    device: str | None = None,
    progress_callback: Callable[[float], None] | None = None,
) -> dict[str, object]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError(f"Could not open video: {video_path}")
    source_fps = capture.get(cv2.CAP_PROP_FPS)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    video_duration = frame_count / source_fps
    frame_step = max(1, round(source_fps / analysis_fps))
    device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
    model = YOLO(str(model_path))
    detection_times: list[float] = []
    detections: list[dict[str, object]] = []
    frames: list[object] = []
    timestamps: list[float] = []
    frame_index = 0
    analyzed = 0

    def process_batch() -> None:
        nonlocal analyzed
        if not frames:
            return
        results = model.predict(
            source=frames,
            imgsz=1280,
            conf=confidence,
            device=device,
            verbose=False,
        )
        for timestamp, result in zip(timestamps, results):
            if result.boxes is None or len(result.boxes) == 0:
                continue
            scores = result.boxes.conf.cpu().tolist()
            detection_times.append(timestamp)
            detections.append(
                {
                    "time": round(timestamp, 3),
                    "count": len(scores),
                    "max_confidence": round(max(scores), 4),
                }
            )
        analyzed += len(frames)
        if progress_callback is not None:
            progress_callback(min(1.0, frame_index / max(1, frame_count)))
        if analyzed % 500 < len(frames):
            print(f"Analyzed {analyzed} frames ({timestamps[-1] / 60:.1f} minutes)", flush=True)
        frames.clear()
        timestamps.clear()

    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % frame_step == 0:
                height = frame.shape[0]
                top = int(height * WAVE_ZONE_TOP)
                bottom = int(height * WAVE_ZONE_BOTTOM)
                frames.append(frame[top:bottom])
                timestamps.append(frame_index / source_fps)
                if len(frames) >= batch_size:
                    process_batch()
            frame_index += 1
        process_batch()
        if progress_callback is not None:
            progress_callback(1.0)
    finally:
        capture.release()

    return {
        "source": video_path.name,
        "duration": round(video_duration, 3),
        "analysis_fps": analysis_fps,
        "confidence": confidence,
        "analyzed_frames": analyzed,
        "raw_detections": detections,
        "rides": build_ride_intervals(detection_times, video_duration),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect surf rides with the locally trained model.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--model", type=Path, default=ROOT / "runs" / "surf_mvp" / "weights" / "best.pt")
    parser.add_argument("--output", type=Path, default=Path("model_rides.json"))
    parser.add_argument("--analysis-fps", type=float, default=2.0)
    parser.add_argument("--confidence", type=float, default=0.4)
    parser.add_argument("--batch-size", type=int, default=4)
    args = parser.parse_args()
    result = detect_rides(
        args.video,
        args.model,
        analysis_fps=args.analysis_fps,
        confidence=args.confidence,
        batch_size=args.batch_size,
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Found {len(result['rides'])} rides. Results: {args.output}")


if __name__ == "__main__":
    main()