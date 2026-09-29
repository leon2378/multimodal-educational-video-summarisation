"""Fine-tune RF-DETR on the dataset (docs/adr/0007). Needs the `train` extra and a GPU.

RF-DETR scores the test split (the held-out lecture) when training ends, per class, against its
automatic labels; the results are in the run folder.
"""

import sys
from pathlib import Path
from typing import Literal

WEIGHTS_DIR = Path("data/models")
Size = Literal["nano", "small"]


def train(
    dataset_dir: Path,
    out: Path,
    size: Size = "nano",
    epochs: int = 50,
    batch_size: int = 4,
    grad_accum_steps: int = 4,
    resolution: int | None = None,
) -> None:
    from rfdetr import RFDETRNano, RFDETRSmall

    model_class = RFDETRNano if size == "nano" else RFDETRSmall
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    weights = str(WEIGHTS_DIR / f"rf-detr-{size}.pth")
    # Square input size, a multiple of 32; the default is 384 for Nano and 512 for Small.
    model = (
        model_class(pretrain_weights=weights, resolution=resolution)
        if resolution
        else model_class(pretrain_weights=weights)
    )
    model.train(
        dataset_dir=str(dataset_dir),
        output_dir=str(out),
        epochs=epochs,
        batch_size=batch_size,
        grad_accum_steps=grad_accum_steps,
        early_stopping=True,
        run_test=True,
        log_per_class_metrics=True,
        tensorboard=False,
        progress_bar="tqdm",
        # Worker processes on Windows re-import the entry point; loading in-process is simpler.
        num_workers=0 if sys.platform == "win32" else 2,
    )
