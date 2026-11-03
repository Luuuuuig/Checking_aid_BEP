"""The text layer of a PDF, numbered the way every reader and every source reference counts it, and the page images.

A PDF page has no lines of its own, only words at positions. extract_lines groups the words of a page into lines by their
top coordinate (words whose tops are within 3 points belong to one line), numbers the lines of each page from 1, from the top
down, and joins the words of a line by single spaces from left to right. The made-up document set (aid/madeup) names its
sources the same way, and so does every reader: "page 2, line 5" is the fifth line that extract_lines gives for page 2.
A PDF that holds only a scanned image has no words and so no lines.

Page and line numbers start at 1 everywhere in the aid (SourceRef.page, SourceRef.line, TextLine, render_page).
"""
import io
import unicodedata
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from pydantic import BaseModel

LINE_TOLERANCE = 3.0         # points: a word joins the line of the first word above it when their tops differ by at most this


class TextLine(BaseModel):
    page: int                # 1-based
    line: int                # 1-based on its page, counted from the top
    text: str                # the words of the line from left to right, joined by single spaces
    x0: float                # left edge of the line, in points from the left edge of the page
    top: float               # top edge of the line, in points from the top of the page


class TextWord(BaseModel):
    text: str
    x0: float                # left and right edge of the word, in points from the left edge of the page
    x1: float


class TextRow(BaseModel):
    """A TextLine with the words it was joined from, left to right: what a reader needs to cut a line into table cells."""
    line: TextLine
    words: list[TextWord]


def extract_rows(pdf: Path) -> list[TextRow]:
    """The lines of every page with the words they hold, ordered by page and then from the top down. A PDF without a text layer gives []."""
    rows = []
    with pdfplumber.open(pdf) as document:
        for number, page in enumerate(document.pages, 1):
            for line_number, words in enumerate(_group_into_lines(page.extract_words()), 1):
                line = TextLine(page=number, line=line_number, text=" ".join(w["text"] for w in words),
                                x0=words[0]["x0"], top=min(w["top"] for w in words))
                rows.append(TextRow(line=line, words=[TextWord(text=w["text"], x0=w["x0"], x1=w["x1"]) for w in words]))
    return rows


def _group_into_lines(words: list[dict]) -> list[list[dict]]:
    """The words of one page as lines. Taken from the top down, a word starts a new line when its top is more than
    LINE_TOLERANCE below the top of the first word of the line before it. Each line is returned from left to right."""
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and word["top"] - lines[-1][0]["top"] <= LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def extract_lines(pdf: Path) -> list[TextLine]:
    """The text lines of the PDF, ordered by page and then from the top down, numbered from 1 on each page.
    A PDF without a text layer (a scan) gives []."""
    return [row.line for row in extract_rows(pdf)]


def page_count(pdf: Path) -> int:
    """The number of pages of the PDF, which Document.pages holds."""
    with pdfplumber.open(pdf) as document:
        return len(document.pages)


def render_page(pdf: Path, page: int, dpi: int = 150) -> bytes:
    """Page `page` (counted from 1) of the PDF as the bytes of a PNG image at `dpi` dots per inch. A scanned PDF renders like any other.
    Raises ValueError for a page the PDF does not have."""
    with pdfium.PdfDocument(pdf) as document:
        if not 1 <= page <= len(document):
            raise ValueError(f"{Path(pdf).name} has no page {page}: its pages are 1 to {len(document)}")
        image = document[page - 1].render(scale=dpi / 72).to_pil()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def find_source(lines: list[TextLine], page: int, needle: str) -> TextLine | None:
    """The first line of `page` whose text holds `needle`, or None. Case, runs of white space and the way an accented letter
    is composed do not matter. A needle with no text in it finds nothing, because it would verify nothing."""
    wanted = _squash(needle)
    if not wanted:
        return None
    return next((line for line in lines if line.page == page and wanted in _squash(line.text)), None)


def _squash(text: str) -> str:
    """The text in the form that find_source compares: composed letters, single spaces, no case."""
    return " ".join(unicodedata.normalize("NFC", text).split()).casefold()
