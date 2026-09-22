from __future__ import annotations

import argparse
from pathlib import Path

import torch
from ultralytics import YOLO


ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the fixed-camera surf MVP model.")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--model", default="yolo11s.pt")
    parser.add_argument("--device", default="mps" if torch.backends.mps.is_available() else "cpu")
    args = parser.parse_args()

    model = YOLO(args.model)
    model.train(
        data=str(ROOT / "yolo_dataset" / "dataset.yaml"),
        epochs=args.epochs,
        patience=20,
        imgsz=1280,
        batch=4,
        device=args.device,
        workers=0,
        cache=True,
        rect=True,
        mosaic=0.0,
        degrees=0.0,
        translate=0.05,
        scale=0.2,
        fliplr=0.5,
        project=str(ROOT / "runs"),
        name="surf_mvp",
        exist_ok=True,
        seed=42,
        plots=True,
    )


if __name__ == "__main__":
    main()