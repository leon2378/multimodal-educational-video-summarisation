"""Does the detector route slides to the vision LLM as well as Phase 5a's rule?

On each slide frame of the test split, the question is the one routing asks: does this slide
show a figure or an annotation? The automatic labels (the LLM's boxes on the PDF pages) are the
reference; the detector answers yes when it finds a figure or annotation above a confidence
threshold, and 5a's rule (lecture_perception.ocr.route) from ink outside text, lines at an angle
and OCR confidence, with each test lecture's frames as its deck. The reference is itself made
by a model, so this measures agreement with it, not the truth.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from lecture_perception.ocr import DeckOcr, RoutingConfig, SlideOCR, route

ROUTED = {"figure", "annotation"}


@dataclass(frozen=True)
class Scores:
    frames: int
    routed: int  # frames the answer sends to the vision LLM
    agree: int
    precision: float
    recall: float

    @property
    def f1(self) -> float:
        total = self.precision + self.recall
        return 2 * self.precision * self.recall / total if total else 0.0


def score(reference: list[bool], answer: list[bool]) -> Scores:
    both = sum(r and a for r, a in zip(reference, answer, strict=True))
    return Scores(
        frames=len(reference),
        routed=sum(answer),
        agree=sum(r == a for r, a in zip(reference, answer, strict=True)),
        precision=both / sum(answer) if any(answer) else 0.0,
        recall=both / sum(reference) if any(reference) else 0.0,
    )


def slide_frames(split: Path) -> list[tuple[Path, bool]]:
    """Each slide frame of a COCO split, and whether its labels route it."""
    coco: dict[str, Any] = json.loads((split / "_annotations.coco.json").read_text("utf-8"))
    names = {c["id"]: c["name"] for c in coco["categories"]}
    labels: dict[int, set[str]] = {}
    for annotation in coco["annotations"]:
        labels.setdefault(annotation["image_id"], set()).add(names[annotation["category_id"]])
    return [
        (split / image["file_name"], bool(labels.get(image["id"], set()) & ROUTED))
        for image in coco["images"]
        if "slide" in labels.get(image["id"], set())
    ]


def rule_routes(images: list[Path]) -> list[bool]:
    """The rule's baseline is a percentile of its deck, so each lecture is routed on its own
    (frames are named <lecture>-<seconds>.jpg)."""
    ocr = SlideOCR()
    decks: dict[str, list[int]] = {}
    for index, path in enumerate(images):
        decks.setdefault(path.stem.rsplit("-", 1)[0], []).append(index)
    routed: set[int] = set()
    for indices in decks.values():
        deck = DeckOcr(slides=[ocr.read(i, Image.open(images[i])) for i in indices], model="")
        routed |= set(route(deck, RoutingConfig()))
    return [i in routed for i in range(len(images))]


def detector_routes(images: list[Path], run: Path, threshold: float) -> list[bool]:
    import supervision as sv
    from rfdetr import RFDETRNano, RFDETRSmall

    config = json.loads((run / "training_config.json").read_text("utf-8"))
    model_class = RFDETRSmall if "small" in run.name else RFDETRNano
    weights = str(run / "checkpoint_best_total.pth")
    resolution = config.get("model_config", {}).get("resolution")
    model = (
        model_class(pretrain_weights=weights, resolution=resolution)
        if resolution
        else model_class(pretrain_weights=weights)
    )
    names = model.class_names
    routes = []
    for path in images:
        detections = model.predict(Image.open(path).convert("RGB"), threshold=threshold)
        if not isinstance(detections, sv.Detections):
            raise TypeError(f"expected detections for one image, got {type(detections).__name__}")
        ids: list[int] = [] if detections.class_id is None else detections.class_id.tolist()
        routes.append(bool({names[int(c)] for c in ids} & ROUTED))
    return routes
