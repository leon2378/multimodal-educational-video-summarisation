"""People in camera frames, found by RF-DETR's COCO weights as they come (no fine-tuning).

Needs the `train` extra (PyTorch). The weights download on first use, to data/models/.
"""

from pathlib import Path

from PIL import Image

from lecture_detector.pdf import Box

WEIGHTS = Path("data/models/rf-detr-nano.pth")
PERSON = 1  # COCO's category id


class PersonFinder:
    def __init__(self, threshold: float = 0.5, weights: Path = WEIGHTS) -> None:
        from rfdetr import RFDETRNano

        weights.parent.mkdir(parents=True, exist_ok=True)
        self._model = RFDETRNano(pretrain_weights=str(weights))
        self._threshold = threshold

    def __call__(self, image: Image.Image) -> list[Box]:
        import supervision as sv

        detections = self._model.predict(image.convert("RGB"), threshold=self._threshold)
        if not isinstance(detections, sv.Detections) or detections.class_id is None:
            raise TypeError(f"expected detections for one image, got {type(detections).__name__}")
        return [
            (float(x0), float(y0), float(x1), float(y1))
            for (x0, y0, x1, y1), category in zip(detections.xyxy, detections.class_id, strict=True)
            if int(category) == PERSON
        ]
