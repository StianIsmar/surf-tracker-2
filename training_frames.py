from __future__ import annotations

import json
import math
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2


WAVE_ZONE_TOP = 0.31
WAVE_ZONE_BOTTOM = 0.59


@dataclass(frozen=True)
class FrameSample:
    timestamp: float
    label: str
    source: str


def sample_interval(start: float, end: float, step: float) -> list[float]:
    count = max(1, math.ceil((end - start) / step))
    spacing = (end - start) / count
    return [round(start + (index + 0.5) * spacing, 3) for index in range(count)]


def build_frame_samples(
    rides: list[dict[str, object]],
    candidates: list[dict[str, object]],
    labels: list[dict[str, object]],
    positive_step: float = 0.5,
    negative_step: float = 2.0,
    exclusion_seconds: float = 1.0,
) -> list[FrameSample]:
    samples = [
        FrameSample(timestamp, "ride", "confirmed_ride")
        for ride in rides
        for timestamp in sample_interval(float(ride["start"]), float(ride["end"]), positive_step)
    ]
    rejected_clips = {
        int(label["clip"])
        for label in labels
        if label.get("ride") is False and type(label.get("clip")) is int
    }
    ride_ranges = [(float(ride["start"]), float(ride["end"])) for ride in rides]
    for clip_number in sorted(rejected_clips):
        if not 1 <= clip_number <= len(candidates):
            continue
        candidate = candidates[clip_number - 1]
        for timestamp in sample_interval(
            float(candidate["start"]), float(candidate["end"]), negative_step
        ):
            near_ride = any(
                start - exclusion_seconds <= timestamp <= end + exclusion_seconds
                for start, end in ride_ranges
            )
            if not near_ride:
                samples.append(FrameSample(timestamp, "non_ride", f"rejected_clip_{clip_number}"))

    unique = {(sample.timestamp, sample.label): sample for sample in samples}
    return sorted(unique.values(), key=lambda sample: (sample.timestamp, sample.label))


def extract_training_frames(
    source_path: Path,
    ground_truth_path: Path,
    candidates_path: Path,
    feedback_path: Path,
    output_path: Path,
) -> dict[str, object]:
    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
    samples = build_frame_samples(
        ground_truth.get("rides", []), candidates, feedback.get("labels", [])
    )
    if not samples:
        raise ValueError("No training frames can be generated from the current labels")

    temporary_path = output_path.with_name(f"{output_path.name}.tmp")
    shutil.rmtree(temporary_path, ignore_errors=True)
    frames_path = temporary_path / "frames"
    frames_path.mkdir(parents=True)

    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {source_path}")
    manifest_samples = []
    try:
        for index, sample in enumerate(samples, start=1):
            capture.set(cv2.CAP_PROP_POS_MSEC, sample.timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(f"Could not read frame at {sample.timestamp:.3f} seconds")
            height, width = frame.shape[:2]
            top = int(height * WAVE_ZONE_TOP)
            bottom = int(height * WAVE_ZONE_BOTTOM)
            cropped = frame[top:bottom]
            filename = f"{sample.label}_{index:04d}_{sample.timestamp:09.3f}.jpg"
            if not cv2.imwrite(str(frames_path / filename), cropped, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                raise RuntimeError(f"Could not write frame: {filename}")
            manifest_samples.append(
                {
                    **asdict(sample),
                    "file": f"frames/{filename}",
                    "crop": {"x": 0, "y": top, "width": width, "height": bottom - top},
                }
            )
    finally:
        capture.release()

    counts = {
        "ride": sum(sample.label == "ride" for sample in samples),
        "non_ride": sum(sample.label == "non_ride" for sample in samples),
    }
    manifest = {
        "source": source_path.name,
        "wave_zone": {"top": WAVE_ZONE_TOP, "bottom": WAVE_ZONE_BOTTOM},
        "counts": counts,
        "samples": manifest_samples,
    }
    (temporary_path / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    shutil.rmtree(output_path, ignore_errors=True)
    os.replace(temporary_path, output_path)
    return manifest