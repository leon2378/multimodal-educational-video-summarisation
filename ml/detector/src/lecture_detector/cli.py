"""lecture-detector: build the frame detector's dataset, train it and score it (docs/adr/0007).

    uv run lecture-detector regions    # box each PDF page's figures, tables and annotations (LLM)
    uv run lecture-detector dataset    # label frames from the videos, write COCO splits
    uv run lecture-detector train      # fine-tune RF-DETR on the GPU, then score the test lecture
    uv run lecture-detector evaluate --run data/detector/runs/<run>   # routing, vs Phase 5a's rule

The videos and PDFs listed in ml/detector/datasets/lectures.json go in data/lectures/.
"""

import argparse
import hashlib
import sys
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw

from lecture_detector import dataset
from lecture_detector.frames import LabelledFrame, labelled_frames
from lecture_detector.pdf import SlidePdf
from lecture_detector.regions import PROMPT, RegionsFile, label_pages
from lecture_llm.agents import Prompt
from lecture_llm.models import LLMConfigError, make_model
from lecture_llm.settings import LLMSettings
from lecture_perception import media

LECTURES = Path("ml/detector/datasets/lectures.json")
REGIONS = Path("ml/detector/datasets/regions")
MEDIA = Path("data/lectures")
DATASET = Path("data/detector/dataset")
RUNS = Path("data/detector/runs")
COLOURS = {
    "slide": "orange",
    "person": "magenta",
    "figure": "blue",
    "annotation": "red",
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lecture-detector", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--lectures", type=Path, default=LECTURES)
    parser.add_argument("--media", type=Path, default=MEDIA, help="where the videos and PDFs are")
    commands = parser.add_subparsers(dest="command", required=True)
    regions = commands.add_parser("regions", help="box each PDF page's regions with the LLM")
    regions.add_argument("--redo", action="store_true", help="label PDFs already labelled again")
    built = commands.add_parser("dataset", help="label frames and write the COCO dataset")
    built.add_argument("--out", type=Path, default=DATASET)
    built.add_argument("--no-people", action="store_true", help="skip camera frames (no PyTorch)")
    built.add_argument("--preview", type=int, default=12, help="frames per lecture drawn to check")
    trained = commands.add_parser("train", help="fine-tune RF-DETR (needs the train extra)")
    trained.add_argument("--dataset", type=Path, default=DATASET)
    trained.add_argument("--size", choices=["nano", "small"], default="nano")
    trained.add_argument("--epochs", type=int, default=50)
    trained.add_argument("--batch-size", type=int, default=4)
    trained.add_argument("--grad-accum", type=int, default=4)
    trained.add_argument("--resolution", type=int, help="square input size, a multiple of 32")
    judged = commands.add_parser("evaluate", help="routing on the test lecture, vs 5a's rule")
    judged.add_argument("--run", type=Path, required=True)
    judged.add_argument("--dataset", type=Path, default=DATASET)
    args = parser.parse_args(argv)
    if args.command == "evaluate":
        return _evaluate(args.run, args.dataset)
    if args.command == "train":
        from lecture_detector.train import train

        stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
        out = RUNS / f"{args.size}{args.resolution or ''}-{stamp}"
        train(
            args.dataset,
            out,
            args.size,
            args.epochs,
            args.batch_size,
            args.grad_accum,
            args.resolution,
        )
        print(f"Run saved to {out}")
        return 0

    lectures = dataset.LectureSet.load(args.lectures)
    try:
        for entry in lectures.lectures:
            _check(args.media / entry.video, entry.video_sha256)
            _check(args.media / entry.pdf, entry.pdf_sha256)
    except LookupError as error:
        print(f"{error}\n{lectures.source}", file=sys.stderr)
        return 2
    if args.command == "regions":
        return _regions(lectures, args.media, args.redo)
    return _dataset(lectures, args.media, args.out, not args.no_people, args.preview)


def _regions(lectures: dataset.LectureSet, media_dir: Path, redo: bool) -> int:
    try:
        model = make_model(LLMSettings())
    except LLMConfigError as error:
        print(error, file=sys.stderr)
        return 2
    prompt = Prompt.load(PROMPT.parent, PROMPT.stem)
    REGIONS.mkdir(parents=True, exist_ok=True)
    for entry in lectures.lectures:
        target = REGIONS / f"{Path(entry.pdf).stem}.json"
        if target.exists() and not redo:
            print(f"{target} exists")
            continue
        labelled = label_pages(SlidePdf(media_dir / entry.pdf), entry.pdf_sha256, model, prompt)
        target.write_bytes((labelled.model_dump_json(indent=1) + "\n").encode("utf-8"))
        boxes = sum(len(page.regions) for page in labelled.pages)
        print(f"{target}: {boxes} regions on {len(labelled.pages)} pages")
    return 0


def _dataset(
    lectures: dataset.LectureSet, media_dir: Path, out: Path, people: bool, preview: int
) -> int:
    finder = None
    if people:
        from lecture_detector.people import PersonFinder

        finder = PersonFinder()

    def frames() -> Iterator[tuple[dataset.LectureEntry, float, LabelledFrame]]:
        for entry in lectures.lectures:
            regions_file = REGIONS / f"{Path(entry.pdf).stem}.json"
            regions = RegionsFile.model_validate_json(regions_file.read_text(encoding="utf-8"))
            video = media_dir / entry.video
            duration_s = media.probe(video).duration_s
            lecture = labelled_frames(video, SlidePdf(media_dir / entry.pdf), regions, finder)
            alignment = lecture.alignment
            print(
                f"{entry.name}: {len(alignment.pages)} slides matched to pages, "
                f"{alignment.inliers} of {alignment.points} points fit, median error "
                f"{alignment.median_error_px:.2f} px"
            )
            drawn = 0
            kinds: dict[str, int] = {}
            for frame in lecture.frames:
                kinds[frame.kind] = kinds.get(frame.kind, 0) + 1
                if drawn < preview and len(frame.boxes) > 1 and int(frame.time_s) % 7 == 0:
                    _draw(frame).save(out / "preview" / f"{entry.name}-{frame.time_s:07.1f}.jpg")
                    drawn += 1
                yield entry, duration_s, frame
            left_out = ", ".join(f"{n} {why}" for why, n in lecture.skipped.most_common())
            print(f"  kept {kinds}; left out: {left_out or 'none'}")

    (out / "preview").mkdir(parents=True, exist_ok=True)
    counts = dataset.write(frames(), out)
    for split, counted in sorted(counts.items()):
        print(f"{split}: " + ", ".join(f"{n} {label}" for label, n in counted.most_common()))
    print(f"Written to {out}")
    return 0


def _evaluate(run: Path, dataset_dir: Path) -> int:
    from lecture_detector import evaluate

    frames = evaluate.slide_frames(dataset_dir / "test")
    images = [path for path, _ in frames]
    reference = [routed for _, routed in frames]
    answers = {"5a rule": evaluate.rule_routes(images)}
    for threshold in (0.3, 0.5):
        answers[f"detector @ {threshold}"] = evaluate.detector_routes(images, run, threshold)
    print(f"{len(frames)} test slide frames, {sum(reference)} with a figure or annotation")
    print("| Answer | Routed | Agrees with labels | Precision | Recall | F1 |")
    print("|---|---|---|---|---|---|")
    for name, answer in answers.items():
        s = evaluate.score(reference, answer)
        print(
            f"| {name} | {s.routed} | {s.agree}/{s.frames} | {s.precision:.2f} | "
            f"{s.recall:.2f} | {s.f1:.2f} |"
        )
    return 0


def _draw(frame: LabelledFrame) -> Image.Image:
    image = frame.image.convert("RGB")
    draw = ImageDraw.Draw(image)
    for label, box in frame.boxes:
        draw.rectangle(box, outline=COLOURS[label], width=2)
    return image


def _check(path: Path, sha256: str) -> None:
    if not path.is_file():
        raise LookupError(f"missing {path}")
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    if digest.hexdigest() != sha256:
        raise LookupError(f"{path} isn't the file this dataset was made with")


if __name__ == "__main__":
    sys.exit(main())
