"""A lecture's slide PDF: pages rendered as images, and their text lines with positions."""

import re
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

# A box as (left, top, right, bottom), y growing downwards.
Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class TextLine:
    text: str
    # In page points from the top left.
    box: Box


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class SlidePdf:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._pdf = pdfium.PdfDocument(path)

    def __len__(self) -> int:
        return len(self._pdf)

    def size(self, index: int) -> tuple[float, float]:
        """Width and height of a page in points."""
        width, height = self._pdf[index].get_size()
        return float(width), float(height)

    def render(self, index: int, scale: float = 1.0) -> Image.Image:
        """The page as an RGB image, `scale` pixels per point."""
        image: Image.Image = self._pdf[index].render(scale=scale).to_pil()
        return image.convert("RGB")

    def text_lines(self, index: int) -> list[TextLine]:
        """The page's text, a line at a time, each with the box around its characters."""
        page = self._pdf[index]
        height = page.get_height()
        textpage = page.get_textpage()
        lines: list[TextLine] = []
        chars: list[str] = []
        boxes: list[Box] = []

        def close() -> None:
            if chars and boxes:
                lines.append(
                    TextLine(
                        "".join(chars),
                        (
                            min(b[0] for b in boxes),
                            min(b[1] for b in boxes),
                            max(b[2] for b in boxes),
                            max(b[3] for b in boxes),
                        ),
                    )
                )
            chars.clear()
            boxes.clear()

        for i in range(textpage.count_chars()):
            char = textpage.get_text_range(i, 1)
            if char in "\r\n":
                close()
                continue
            chars.append(char)
            left, bottom, right, top = textpage.get_charbox(i)
            if right > left:
                boxes.append((left, height - top, right, height - bottom))
        close()
        return lines
