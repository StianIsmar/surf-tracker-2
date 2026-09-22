from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VALIDATION_RIDES = {4, 7}


def find_ride_number(timestamp: float, rides: list[dict[str, object]]) -> int:
    for index, ride in enumerate(rides, start=1):
        if float(ride["start"]) <= timestamp <= float(ride["end"]):
            return index
    raise ValueError(f"Frame at {timestamp:.3f} is outside every confirmed ride")


def choose_split(sample: dict[str, object], rides: list[dict[str, object]]) -> str:
    if sample["label"] == "ride":
        return "val" if find_ride_number(float(sample["timestamp"]), rides) in VALIDATION_RIDES else "train"
    source = str(sample["source"])
    clip_number = int(source.rsplit("_", 1)[-1])
    return "val" if clip_number % 5 == 0 else "train"


def yolo_lines(boxes: list[dict[str, float]]) -> list[str]:
    return [
        "0 " + " ".join(
            f"{value:.6f}"
            for value in (
                box["x"] + box["width"] / 2,
                box["y"] + box["height"] / 2,
                box["width"],
                box["height"],
            )
        )
        for box in boxes
    ]


def prepare_yolo_dataset(
    manifest_path: Path,
    annotations_path: Path,
    ground_truth_path: Path,
    output_path: Path,
) -> dict[str, dict[str, int]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    annotations = json.loads(annotations_path.read_text(encoding="utf-8")).get("annotations", {})
    rides = json.loads(ground_truth_path.read_text(encoding="utf-8")).get("rides", [])
    temporary_path = output_path.with_name(f"{output_path.name}.tmp")
    shutil.rmtree(temporary_path, ignore_errors=True)
    for split in ("train", "val"):
        (temporary_path / "images" / split).mkdir(parents=True)
        (temporary_path / "labels" / split).mkdir(parents=True)

    summary = {
        "train": {"images": 0, "positive_images": 0, "boxes": 0},
        "val": {"images": 0, "positive_images": 0, "boxes": 0},
    }
    frames_path = manifest_path.parent / "frames"
    for sample in manifest.get("samples", []):
        filename = Path(str(sample["file"])).name
        annotation = annotations.get(filename)
        if sample["label"] == "ride" and not (annotation and annotation.get("reviewed")):
            raise ValueError(f"Ride frame has not been reviewed: {filename}")
        boxes = annotation.get("boxes", []) if annotation else []
        split = choose_split(sample, rides)
        source_path = frames_path / filename
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        shutil.copy2(source_path, temporary_path / "images" / split / filename)
        label_path = temporary_path / "labels" / split / f"{Path(filename).stem}.txt"
        lines = yolo_lines(boxes)
        label_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        summary[split]["images"] += 1
        summary[split]["positive_images"] += bool(boxes)
        summary[split]["boxes"] += len(boxes)

    dataset_yaml = (
        f"path: {output_path.resolve()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: riding_surfer\n"
    )
    (temporary_path / "dataset.yaml").write_text(dataset_yaml, encoding="utf-8")
    (temporary_path / "summary.json").write_text(
        json.dumps({"validation_rides": sorted(VALIDATION_RIDES), "splits": summary}, indent=2) + "\n",
        encoding="utf-8",
    )
    shutil.rmtree(output_path, ignore_errors=True)
    os.replace(temporary_path, output_path)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare local surf annotations for YOLO training.")
    parser.add_argument("--output", type=Path, default=ROOT / "yolo_dataset")
    args = parser.parse_args()
    summary = prepare_yolo_dataset(
        ROOT / "training_data" / "manifest.json",
        ROOT / "training_annotations.json",
        ROOT / "ground_truth.json",
        args.output,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()