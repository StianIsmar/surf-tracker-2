from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np


class ExportCancelled(RuntimeError):
    pass


def run_export_command(
    command: list[str], cancel_event: threading.Event | None = None
) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ExportCancelled("Export stopped")
    process = subprocess.Popen(command)
    while True:
        try:
            return_code = process.wait(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if cancel_event is not None and cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise ExportCancelled("Export stopped")
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command)


@dataclass(frozen=True)
class Detection:
    time: float
    x: float
    y: float
    width: int
    height: int


@dataclass
class Track:
    detections: list[Detection] = field(default_factory=list)
    missed_frames: int = 0

    @property
    def duration(self) -> float:
        return self.detections[-1].time - self.detections[0].time

    @property
    def horizontal_travel(self) -> float:
        xs = [detection.x for detection in self.detections]
        return max(xs) - min(xs)

    @property
    def net_horizontal_travel(self) -> float:
        return abs(self.detections[-1].x - self.detections[0].x)

    @property
    def directionality(self) -> float:
        path_length = sum(
            abs(current.x - previous.x)
            for previous, current in zip(self.detections, self.detections[1:])
        )
        return self.net_horizontal_travel / path_length if path_length else 0.0


def detect_people_like_shapes(frame: np.ndarray) -> list[tuple[float, float, int, int]]:
    height, width = frame.shape[:2]
    top = int(height * 0.31)
    bottom = int(height * 0.59)
    roi = frame[top:bottom]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)
    _, mask = cv2.threshold(blackhat, 13, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    )

    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask)
    detections: list[tuple[float, float, int, int]] = []
    for component in range(1, count):
        x, y, box_width, box_height, area = stats[component]
        if not (18 <= area <= 1800):
            continue
        if not (3 <= box_width <= 55 and 7 <= box_height <= 75):
            continue
        if box_height / box_width < 0.55:
            continue
        center_x, center_y = centroids[component]
        detections.append((float(center_x), float(center_y + top), box_width, box_height))
    return detections


def update_tracks(
    tracks: list[Track],
    finished: list[Track],
    detections: list[tuple[float, float, int, int]],
    timestamp: float,
    max_distance: float = 55.0,
    max_missed_frames: int = 4,
) -> None:
    unmatched = set(range(len(detections)))
    for track in tracks:
        previous = track.detections[-1]
        matches = [
            (
                (detections[index][0] - previous.x) ** 2
                + (detections[index][1] - previous.y) ** 2,
                index,
            )
            for index in unmatched
        ]
        nearest = min(matches) if matches else None
        if nearest is not None and nearest[0] <= max_distance**2:
            _, index = nearest
            x, y, width, height = detections[index]
            track.detections.append(Detection(timestamp, x, y, width, height))
            track.missed_frames = 0
            unmatched.remove(index)
        else:
            track.missed_frames += 1

    finished.extend(track for track in tracks if track.missed_frames > max_missed_frames)
    tracks[:] = [track for track in tracks if track.missed_frames <= max_missed_frames]
    tracks.extend(
        Track(detections=[Detection(timestamp, *detections[index])])
        for index in unmatched
    )


def merge_intervals(
    intervals: list[tuple[float, float]],
    video_duration: float,
    padding: float = 3.0,
    merge_gap: float = 1.0,
) -> list[dict[str, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + merge_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [
        {
            "start": round(max(0.0, start - padding), 2),
            "end": round(min(video_duration, end + padding), 2),
            "duration": round(
                min(video_duration, end + padding) - max(0.0, start - padding), 2
            ),
        }
        for start, end in merged
    ]


def export_rides(
    video_path: Path,
    rides: list[dict[str, float]],
    output_directory: Path,
    progress_callback: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("FFmpeg is required for export. Install it with: brew install ffmpeg")

    output_directory.mkdir(parents=True, exist_ok=True)
    total_duration = sum(ride["duration"] for ride in rides)
    completed_duration = 0.0
    for index, ride in enumerate(rides, start=1):
        start = ride["start"]
        duration = ride["duration"]
        output_path = output_directory / f"ride_{index:03d}_{start:07.2f}.mp4"
        run_export_command(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(start),
                "-i",
                str(video_path),
                "-t",
                str(duration),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            cancel_event,
        )
        completed_duration += duration
        if progress_callback is not None:
            progress_callback(completed_duration / max(total_duration, 1.0))
        print(f"Exported {output_path}")


def find_rides(video_path: Path, analysis_fps: float = 5.0) -> list[dict[str, float]]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError(f"Could not open video: {video_path}")

    source_fps = capture.get(cv2.CAP_PROP_FPS)
    video_duration = capture.get(cv2.CAP_PROP_FRAME_COUNT) / source_fps
    frame_height = capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
    frame_step = max(1, round(source_fps / analysis_fps))
    active: list[Track] = []
    finished: list[Track] = []
    frame_index = 0

    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if frame_index % frame_step == 0:
            timestamp = frame_index / source_fps
            detections = detect_people_like_shapes(frame)
            update_tracks(active, finished, detections, timestamp)
        frame_index += 1

    capture.release()
    finished.extend(active)

    intervals = []
    for track in finished:
        median_y = float(np.median([detection.y for detection in track.detections]))
        if track.duration < 1.8 or track.net_horizontal_travel < 180:
            continue
        if track.directionality < 0.6 or median_y > 0.5 * frame_height:
            continue
        intervals.append((track.detections[0].time, track.detections[-1].time))
    return merge_intervals(intervals, video_duration)


def main() -> None:
    parser = argparse.ArgumentParser(description="Find surf rides without an AI model.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--analysis-fps", type=float, default=5.0)
    parser.add_argument("--output", type=Path, default=Path("rides.json"))
    parser.add_argument("--export-dir", type=Path)
    args = parser.parse_args()

    rides = find_rides(args.video, args.analysis_fps)
    args.output.write_text(json.dumps(rides, indent=2) + "\n", encoding="utf-8")
    print(f"Found {len(rides)} candidate rides. Results: {args.output}")
    if args.export_dir:
        export_rides(args.video, rides, args.export_dir)


if __name__ == "__main__":
    main()