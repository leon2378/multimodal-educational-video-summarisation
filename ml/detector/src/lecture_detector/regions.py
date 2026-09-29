"""Figures, tables and annotations on each page of a slide PDF, boxed by the vision LLM.

The LLM is the teacher here: it sees each page once, rendered clean from the PDF, and its boxes
are carried onto every video frame that shows the page (lecture_detector.align). The boxes are
saved as a dataset file with the model and prompt that made them, so the detector's training
data can be rebuilt without asking again, and reviewed.
"""

import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models import Model

from lecture_detector.pdf import Box, SlidePdf
from lecture_llm.agents import Prompt, Usage

RegionLabel = Literal["figure", "table", "annotation"]
PROMPT = Path("prompts/detector/page-regions.v1.md")
# Pixels across a rendered page: enough for small annotations to be legible.
RENDER_WIDTH = 1280


class _Region(BaseModel):
    label: RegionLabel
    box_2d: list[int] = Field(min_length=4, max_length=4)


class _Regions(BaseModel):
    regions: list[_Region]


class Region(BaseModel):
    label: RegionLabel
    # Page points from the top left: (left, top, right, bottom).
    box: Box


class PageRegions(BaseModel):
    page: int  # 1-based, as a PDF viewer numbers pages
    regions: list[Region]


class RegionsFile(BaseModel):
    pdf: str
    pdf_sha256: str
    model: str
    prompt: str
    created_at: datetime
    pages: list[PageRegions]
    usage: Usage


def to_page_box(box_2d: list[int], width: float, height: float) -> Box | None:
    """[ymin, xmin, ymax, xmax] on a 0-1000 scale to page points; None if it's empty."""
    ymin, xmin, ymax, xmax = (min(1000, max(0, v)) for v in box_2d)
    if xmax - xmin < 3 or ymax - ymin < 3:
        return None
    return (xmin * width / 1000, ymin * height / 1000, xmax * width / 1000, ymax * height / 1000)


def label_pages(pdf: SlidePdf, pdf_sha256: str, model: Model, prompt: Prompt) -> RegionsFile:
    agent = Agent(model, output_type=_Regions, instructions=prompt.text)
    pages = []
    usage = Usage()
    for index in range(len(pdf)):
        width, height = pdf.size(index)
        buffer = io.BytesIO()
        pdf.render(index, scale=RENDER_WIDTH / width).save(buffer, "PNG")
        result = agent.run_sync(
            [f"Slide page {index + 1}:", BinaryContent(buffer.getvalue(), media_type="image/png")]
        )
        usage.add(result.usage)
        regions = []
        for region in result.output.regions:
            box = to_page_box(region.box_2d, width, height)
            if box is not None:
                regions.append(Region(label=region.label, box=box))
        pages.append(PageRegions(page=index + 1, regions=regions))
    return RegionsFile(
        pdf=pdf.path.name,
        pdf_sha256=pdf_sha256,
        model=f"{model.system}:{model.model_name}",
        prompt=prompt.fingerprint,
        created_at=datetime.now(UTC),
        pages=pages,
        usage=usage,
    )
