"""The detector's dataset: which lectures, and labelled frames written as COCO, split by lecture.

RF-DETR reads Roboflow's layout: train/, valid/ and test/, each with its images and an
_annotations.coco.json. The test lecture is never trained on. Validation (which picks the best
checkpoint) takes the last fifth of each training lecture, so it shares their slide template:
it's for choosing, not for reporting.
"""

import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from lecture_detector.frames import LABELS, LabelledFrame

Split = Literal["train", "valid", "test"]
VALID_FROM = 0.8  # share of a training lecture's duration after which frames validate


class LectureEntry(BaseModel):
    name: str
    video: str
    video_sha256: str
    pdf: str
    pdf_sha256: str
    split: Literal["train", "test"]


class LectureSet(BaseModel):
    source: str
    licence: str
    lectures: list[LectureEntry]

    @classmethod
    def load(cls, path: Path) -> "LectureSet":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def split_of(entry: LectureEntry, time_s: float, duration_s: float) -> Split:
    if entry.split == "test":
        return "test"
    return "valid" if time_s >= VALID_FROM * duration_s else "train"


class CocoWriter:
    """Accumulates one split's images and boxes, then writes _annotations.coco.json."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.images: list[dict[str, Any]] = []
        self.annotations: list[dict[str, Any]] = []
        self.counts: Counter[str] = Counter()

    def add(self, name: str, frame: LabelledFrame) -> None:
        image_id = len(self.images) + 1
        file_name = f"{name}-{frame.time_s:07.1f}.jpg"
        frame.image.convert("RGB").save(self.folder / file_name, quality=95)
        width, height = frame.image.size
        self.images.append(
            {"id": image_id, "file_name": file_name, "width": width, "height": height}
        )
        self.counts["images"] += 1
        for label, (left, top, right, bottom) in frame.boxes:
            w, h = right - left, bottom - top
            self.annotations.append(
                {
                    "id": len(self.annotations) + 1,
                    "image_id": image_id,
                    "category_id": LABELS.index(label) + 1,
                    "bbox": [round(left, 1), round(top, 1), round(w, 1), round(h, 1)],
                    "area": round(w * h, 1),
                    "iscrowd": 0,
                }
            )
            self.counts[label] += 1

    def close(self) -> None:
        categories = [
            {"id": i + 1, "name": label, "supercategory": "none"} for i, label in enumerate(LABELS)
        ]
        coco = {"images": self.images, "annotations": self.annotations, "categories": categories}
        (self.folder / "_annotations.coco.json").write_text(json.dumps(coco), encoding="utf-8")


def write(
    frames: Iterable[tuple[LectureEntry, float, LabelledFrame]], root: Path
) -> dict[Split, Counter[str]]:
    """Write every frame into its split; `frames` yields (lecture, its duration, frame)."""
    writers: dict[Split, CocoWriter] = {}
    for entry, duration_s, frame in frames:
        split = split_of(entry, frame.time_s, duration_s)
        if split not in writers:
            writers[split] = CocoWriter(root / split)
        writers[split].add(entry.name, frame)
    for writer in writers.values():
        writer.close()
    return {split: writer.counts for split, writer in writers.items()}
