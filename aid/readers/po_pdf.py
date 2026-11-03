"""The reader of the purchase order PDF: a template parser, no AI.

The PO PDF has a fixed layout, so a template says all the reader needs (aid/readers/templates/<name>.json): a regular
expression for each header field, and for the lines table the field of each column and the text that marks its title row.

Header. A field's value is the first group of its expression, taken from the first text line of the PDF that matches.
Lines table. Each page is read on its own. Its title row is the first text line that holds the marker ("Artikel"). The
words of the title row show where the columns begin, because the layout has no ruling lines: a word of a later row belongs
to the column whose title begins at or before the middle of the word. Every text row below the title row is a line of the
PO when its line_id cell is not empty. A page without a title row has no lines. A PDF with no title row at all, or with a
title row whose number of words is not the number of columns, does not fit the template and is refused with a ValueError,
because cutting it into cells anyway would put values in the wrong fields.

Values. Every DocValue holds the text as printed, the normalised value and the text line it stands on (page, line number
and the text of the line). A text the normaliser cannot read keeps its text, has no value and is uncertain. An empty cell,
or a header field the PDF does not show, stays DocValue().
"""
import re
from bisect import bisect_right
from datetime import date
from itertools import groupby
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from aid.readers.pdf_text import TextLine, TextRow, TextWord, extract_rows, page_count
from aid.schema import (DocLine, DocValue, Document, SourceRef, normalize_basis, normalize_currency, normalize_date, normalize_number,
                        normalize_unit)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

HeaderField = Literal["po_reference", "version_hint", "supplier", "document_date", "currency", "incoterm"]
ColumnField = Literal["line_id", "item_code", "description", "quantity", "unit", "unit_price", "price_basis", "discount_pct", "net_price",
                      "delivery_date"]
_NUMBER_FIELDS = ("quantity", "unit_price", "discount_pct", "net_price")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")        # a misspelled key in a template fails instead of being ignored


class PoTable(_Strict):
    columns: list[ColumnField]                       # the DocLine field of each column, from left to right
    header_row_contains: str                         # the text that marks the title row of the table


class PoTemplate(_Strict):
    header: dict[HeaderField, str]                   # Document field -> regular expression whose first group is the value
    table: PoTable
    decimal_comma: bool = True                       # the number convention of the layout: "1.234,50" (see normalize_number)

    @model_validator(mode="after")
    def _table_has_a_line_id(self):
        if "line_id" not in self.table.columns:
            raise ValueError("the table needs a line_id column")
        return self


def load_template(name: str) -> PoTemplate:
    """The template aid/readers/templates/<name>.json."""
    return PoTemplate.model_validate_json((TEMPLATE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def read_po_pdf(pdf: Path, template: str = "po_madeup") -> Document:
    """Read the PO PDF with the template. Raises ValueError for a PDF the template does not fit (see the module docstring)."""
    pdf = Path(pdf)
    spec = load_template(template)
    rows = extract_rows(pdf)
    header = {field: _header_value(field, pattern, rows) for field, pattern in spec.header.items()}
    lines = _read_lines(rows, spec, _as_date(header.get("document_date")), f"{pdf.name} (template {template!r})")
    return Document(kind="po", source_file=pdf.name, pages=page_count(pdf), reader=f"po_pdf:{template}", lines=lines, **header)


# --- the header ---

_HEADER_NORMALISERS = {"document_date": lambda text: normalize_date(text, None), "currency": normalize_currency}


def _header_value(field: str, pattern: str, rows: list[TextRow]) -> DocValue:
    """The value of a header field: the first group of `pattern` (the whole match when it has no group) on the first line it matches."""
    for row in rows:
        found = re.search(pattern, row.line.text)
        if found is None:
            continue
        text = ((found.group(1) if found.re.groups else found.group(0)) or "").strip()
        if not text:
            continue                                 # the expression matched but holds nothing
        normalise = _HEADER_NORMALISERS.get(field)
        value = normalise(text) if normalise else text
        return DocValue(value=value, text=text, source=_source(row.line), uncertain=value is None)
    return DocValue()


def _as_date(document_date: DocValue | None) -> date | None:
    """The PO's own date, against which a delivery week without a year is read."""
    try:
        return date.fromisoformat(document_date.value) if document_date and document_date.value else None
    except ValueError:                               # a range or a week where a date was expected
        return None


def _source(line: TextLine) -> SourceRef:
    return SourceRef(page=line.page, line=line.line, text=line.text)


# --- the lines table ---

def _read_lines(rows: list[TextRow], spec: PoTemplate, document_date: date | None, where: str) -> list[DocLine]:
    lines = []
    for starts, table_rows in _table_pages(rows, spec.table, where):
        for row in table_rows:
            cells = dict(zip(spec.table.columns, _cells(row.words, starts)))
            line_id = cells.pop("line_id")
            if line_id:                              # a row without a line number is a continuation or a footer, not a line of the PO
                lines.append(DocLine(line_id=line_id, **{field: _cell_value(field, text, row.line, document_date, spec.decimal_comma)
                                                         for field, text in cells.items()}))
    return lines


def _table_pages(rows: list[TextRow], table: PoTable, where: str) -> list[tuple[list[float], list[TextRow]]]:
    """For each page that has a title row: the x positions where its columns begin and the text rows below the title row."""
    pages = []
    for page, group in groupby(rows, key=lambda row: row.line.page):
        page_rows = list(group)
        title = next((n for n, row in enumerate(page_rows) if table.header_row_contains in row.line.text), None)
        if title is None:
            continue
        starts = [word.x0 for word in page_rows[title].words]
        if len(starts) != len(table.columns):
            raise ValueError(f"{where}, page {page}: the title row has {len(starts)} words but the template lists {len(table.columns)} columns")
        pages.append((starts, page_rows[title + 1:]))
    if not pages:
        raise ValueError(f"{where}: no row contains {table.header_row_contains!r}, so the template does not fit this PDF")
    return pages


def _cells(words: list[TextWord], starts: list[float]) -> list[str]:
    """The words of a row as one text per column: a word goes to the column whose title begins at or before the middle of the word."""
    cells: list[list[str]] = [[] for _ in starts]
    for word in words:
        cells[max(bisect_right(starts, (word.x0 + word.x1) / 2) - 1, 0)].append(word.text)
    return [" ".join(cell) for cell in cells]


def _cell_value(field: str, text: str, line: TextLine, document_date: date | None, decimal_comma: bool) -> DocValue:
    """The DocValue of one cell: its text, its normalised value and its line. An empty cell is DocValue()."""
    if not text:
        return DocValue()
    if field in _NUMBER_FIELDS:
        value = normalize_number(text, decimal_comma=decimal_comma)
    elif field == "unit":
        value = normalize_unit(text)
    elif field == "price_basis":
        value = normalize_basis(text)                # the Per cell alone: a number in the price or in the row is no basis
    elif field == "delivery_date":
        value = normalize_date(text, document_date)
    else:
        value = text                                 # item_code and description stay as printed
    return DocValue(value=value, text=text, source=_source(line), uncertain=value is None)
