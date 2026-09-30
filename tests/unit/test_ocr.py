"""OCR on slides, and routing the slides it can't handle to the vision LLM."""

import io
import math
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from lecture_core.storage import LocalStorage
from lecture_core.timeline import SlideDeck, SlideImage
from lecture_llm.agents import LectureLLM, Prompts
from lecture_perception.ocr import (
    DeckOcr,
    OcrLine,
    RoutingConfig,
    SlideOCR,
    SlideOcr,
    ink_outside_text,
    reading_from_ocr,
    route,
    slide_area,
)
from lecture_pipeline import stages
from lecture_pipeline.cache import StageCache, StageResult, StageSpec, cache_key
from tests.unit.fakes import FakeLLM

PROMPTS = Prompts.load(Path(__file__).resolve().parents[2] / "prompts" / "pipeline")
AREA = (0, 0, 480, 360)


def line(
    text: str,
    top: float,
    height: float = 20,
    left: float = 40,
    angle: float = 0,
    score: float = 0.98,
) -> OcrLine:
    width = 12 * len(text)
    dx, dy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    corners = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    box = [(left + x * dx - y * dy, top + x * dy + y * dx) for x, y in corners]
    return OcrLine(text=text, score=score, box=box)


def slide(slide_id: int, lines: list[OcrLine], ink: float = 0.01) -> SlideOcr:
    return SlideOcr(slide_id=slide_id, lines=lines, area=AREA, ink_outside_text=ink)


def text_slide(slide_id: int, ink: float = 0.01) -> SlideOcr:
    return slide(
        slide_id,
        [line("A TITLE", 10, height=40), line("- one bullet with words", 80)]
        + [line(f"- bullet number {n}", 110 + 30 * n) for n in range(3)],
        ink,
    )


def render_slide(title: str, bullets: list[str], bars: int = 0) -> Image.Image:
    """A white slide with a big title and bullets, optionally between black bars."""
    image = Image.new("RGB", (640 + 2 * bars, 480), "black")
    draw = ImageDraw.Draw(image)
    draw.rectangle((bars, 0, bars + 639, 479), fill="white")
    draw.text((bars + 40, 40), title, fill="black", font=ImageFont.load_default(size=44))
    for n, bullet in enumerate(bullets):
        draw.text(
            (bars + 60, 160 + 50 * n), bullet, fill="black", font=ImageFont.load_default(size=26)
        )
    return image


def test_ocr_reads_a_slides_title_and_text() -> None:
    image = render_slide("COUNTING OPERATIONS", ["assume these steps take", "count the operations"])

    reading = reading_from_ocr(SlideOCR().read(3, image))

    assert reading.slide_id == 3
    assert reading.reader == "ocr"
    assert reading.title.upper() == "COUNTING OPERATIONS"
    assert reading.text.lower().splitlines() == ["assume these steps take", "count the operations"]


def test_the_slide_area_leaves_out_bars_beside_it() -> None:
    image = render_slide("TITLE", [], bars=80)

    assert slide_area(image) == (80, 0, 720, 480)


def test_ink_outside_text_measures_figures_not_letters() -> None:
    image = render_slide("A TITLE", [])
    title = OcrLine(text="A TITLE", score=0.99, box=[(30, 30), (320, 30), (320, 100), (30, 100)])
    plain = ink_outside_text(image, [title], (0, 0, 640, 480))
    ImageDraw.Draw(image).rectangle((100, 200, 299, 299), fill="gray")
    with_figure = ink_outside_text(image, [title], (0, 0, 640, 480))

    assert plain < 0.001
    assert with_figure == pytest.approx(200 * 100 / (640 * 480), rel=0.05)


def test_slides_with_more_than_text_go_to_the_vision_llm() -> None:
    deck = DeckOcr(
        model="test",
        slides=[
            *(text_slide(n) for n in range(4)),
            text_slide(4, ink=0.05),  # a plot: ink beyond the template's usual 0.01
            slide(5, [*text_slide(5).lines, *(line("note", 200, angle=-30) for _ in range(2))]),
            slide(6, [line("words " * 6, 80, score=0.8)]),
            slide(7, [line("x", 80)]),
        ],
    )

    assert route(deck, RoutingConfig()) == {
        4: ["figure"],
        5: ["annotations"],
        6: ["low confidence"],
        7: ["little text"],
    }


def test_an_ocr_reading_has_the_title_then_the_text_with_bullets() -> None:
    ocr = slide(
        0,
        [
            line("\uf0a7 constant time", 120),
            line("TYPES OF ORDERS OF", 10, height=40),
            line("GROWTH", 52, height=40),
            line("• linear", 150),
            line("n log n", 180),
        ],
    )

    reading = reading_from_ocr(ocr)

    assert reading.title == "TYPES OF ORDERS OF GROWTH"
    assert reading.text == "- constant time\n- linear\nn log n"


def _deck_with_images(tmp_path: Path) -> tuple[stages.Context, StageResult[SlideDeck]]:
    store = LocalStorage(tmp_path)
    ctx = stages.Context(StageCache(store), store)
    slides = []
    for n in range(3):
        buffer = io.BytesIO()
        render_slide(f"SLIDE {n}", ["some text"]).save(buffer, "JPEG")
        store.put_bytes(f"slides/{n}.jpg", buffer.getvalue(), "image/jpeg")
        slides.append(SlideImage(id=n, image_key=f"slides/{n}.jpg", first_seen_s=10.0 * n))
    deck = SlideDeck(slides=slides, spans=[], slide_share=1.0)
    return ctx, StageResult(key="deck", output=deck, cached=False)


def test_routed_reading_sends_only_the_routed_slides_to_the_llm(tmp_path: Path) -> None:
    ctx, deck = _deck_with_images(tmp_path)
    texts = StageResult(
        key="ocr",
        output=DeckOcr(model="test", slides=[text_slide(0), text_slide(1, ink=0.2), text_slide(2)]),
        cached=False,
    )
    fake = FakeLLM()
    llm = LectureLLM(fake.model, PROMPTS)

    routed = stages.read_slides(ctx, deck, llm, texts, "routed").output
    ocr_only = stages.read_slides(ctx, deck, llm, texts, "ocr").output

    assert [(r.reader, r.title) for r in routed.readings] == [
        ("ocr", "A TITLE"),
        ("vlm", "Slide 1"),
        ("ocr", "A TITLE"),
    ]
    assert routed.routed == {1: ["figure"]}
    assert routed.usage.requests == 1
    assert [r.reader for r in ocr_only.readings] == ["ocr"] * 3
    assert ocr_only.usage.requests == 0
    assert fake.calls == ["_SlideBatch"]


def test_a_routed_slide_the_llm_leaves_untitled_keeps_the_ocr_title(tmp_path: Path) -> None:
    ctx, deck = _deck_with_images(tmp_path)
    texts = StageResult(
        key="ocr",
        output=DeckOcr(model="test", slides=[text_slide(0), text_slide(1, ink=0.2), text_slide(2)]),
        cached=False,
    )
    llm = LectureLLM(FakeLLM(untitled=True).model, PROMPTS)

    reading = stages.read_slides(ctx, deck, llm, texts, "routed").output.readings[1]

    # The vision LLM's reading, with OCR's title.
    assert (reading.reader, reading.title, reading.latex) == ("vlm", "A TITLE", ["O(n)"])


def test_reading_every_slide_with_the_llm_keeps_its_old_cache_key(tmp_path: Path) -> None:
    ctx, deck = _deck_with_images(tmp_path)
    llm = LectureLLM(FakeLLM().model, PROMPTS)

    result = stages.read_slides(ctx, deck, llm, mode="vlm")

    # The key lectures were read under before OCR existed, so they aren't read again.
    before_ocr = StageSpec(
        "read_slides",
        "1",
        model=llm.model_name,
        params={"prompt": llm.prompts.read_slides.fingerprint, "per_request": 8},
    )
    assert result.key == cache_key(before_ocr, {"slides": "deck"})
    assert [r.reader for r in result.output.readings] == ["vlm"] * 3
