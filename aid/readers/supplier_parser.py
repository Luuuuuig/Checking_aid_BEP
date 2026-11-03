"""The parser baseline for supplier documents: an order confirmation read by a layout template, no AI.

A layout template (aid/readers/templates/<name>.json, a LayoutTemplate) says all the reader needs about one layout. detect_template
finds the template of a PDF and read_with_template reads the PDF with it into a Document of kind "confirmation", the schema that the
LLM reader fills as well, so that both are scored against the same references. This reader is the baseline of the AI-necessity check:
what rules alone can read. The templates of the PO PDF (po_<name>.json) belong to read_po_pdf and are not loaded here.

Which template. detect_template joins the text lines of the PDF and returns the first template of the list whose detect expressions
all match somewhere in that text. A PDF without a text layer (a scan) has no text, so it matches nothing and gives None. The merged
and total_only documents of the made-up set are table_nl documents with a line or a column of their own, and table_nl reads them.

Header. The value of a field is the group of its expression, taken from the first text line of the PDF that matches. The supplier
stands alone on the first row of a made-up confirmation, without a label, so its expression is the whole row.

Lines. The table of a template has one of two strategies.
  "lines": the confirmation has a table. The title row of a page is the first text line that holds header_row_contains. The words
    of the title row show where the columns begin, because the layout has no ruling lines: a word of a later row belongs to the
    column whose title begins at or before the middle of the word. A column is tied to a DocLine field by its title word, not by its
    place in the row: a title word that the template does not name has no field and its cells are left out (the Totaal column of a
    total_only confirmation), and a column that the template names and the title row lacks is missing. basis_mark, when the
    template has it, is the text in a unit_price cell where the price basis starts ("/" in "12,50 / 100 st").
    Which rows are lines: a row below a title row is a line when its line_id cell matches line_id_pattern in full (a whole number
    unless the template says otherwise). Any other row below the title row, a total ("Totaal EUR 192,00"), a page number ("Seite 1
    von 2") or the continuation of a description, is not a line and is skipped.
    Pages: a page with a title row is cut with its own columns. A page without one that follows a page with one is a continuation
    page: it is cut with the columns of the last title row before it, so that a table whose title row is not repeated is still read
    to its end. A page before the first title row (a cover page) has no lines. A document with rows but no title row on any page is
    refused.
  "text": the confirmation has one sentence per line. line_regex matches such a sentence, and its named groups are the DocLine
    fields (line_id, item_code, quantity, ...). A row that matches nothing is not a line (an introduction), unless it begins as a
    line does (line_start): that row is refused, because skipping it would read as a PO line the confirmation leaves out.
In both, item_code_regex, when the template has it, is one item code. A line whose item code cell holds it more than once (two codes
on one line, "KM-1901 / KM-1902") is still one DocLine: its item_code keeps the whole cell as value and as text, and is uncertain.

Values. Every DocValue holds the text as printed, the normalised value and the text line it stands on. Numbers go through
normalize_number with the template's decimal_comma, the unit through normalize_unit, a delivery date through normalize_date with the
confirmation's own date (so that "KW 48" gets its year), item code and description stay as printed. The price basis is read from a
cell or part of a cell that holds the basis alone: the Per column, or, when that cell is empty, the part of the price cell from
basis_mark on; never the whole price cell and never a quantity cell. The number of such a price cell is the part before the mark.
A text the normaliser cannot read keeps its text, has no value and is uncertain. An empty cell, or a header field the PDF does not
show, stays DocValue().

A PDF that has a text layer but that the template cannot be applied to (no title row, no line sentence) is refused with a
ValueError: a reading with no lines would look like a confirmation that leaves every PO line out.
"""
import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from itertools import groupby
from pathlib import Path
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from aid.readers.pdf_text import TextLine, TextRow, TextWord, extract_lines, extract_rows, page_count
from aid.schema import (DocLine, DocValue, Document, SourceRef, normalize_basis, normalize_currency, normalize_date, normalize_number,
                        normalize_unit)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
PO_TEMPLATE_PREFIX = "po_"                           # po_madeup.json and its kind belong to read_po_pdf

HeaderField = Literal["po_reference", "version_hint", "supplier", "document_date", "currency", "incoterm"]
LineField = Literal["line_id", "item_code", "description", "quantity", "unit", "unit_price", "price_basis", "discount_pct", "net_price",
                    "delivery_date"]
_NUMBER_FIELDS = ("quantity", "unit_price", "discount_pct", "net_price")


# --- the template ---

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")        # a misspelled key in a template fails instead of being ignored


def _compile(pattern: str, what: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as error:
        raise ValueError(f"{what} {pattern!r} is not a regular expression: {error}") from None


def _problems(error: ValidationError) -> str:
    """The problems of a ValidationError in one line, without the link and the "Value error" that pydantic adds to each."""
    problems = []
    for problem in error.errors(include_url=False):
        where, message = ".".join(str(part) for part in problem["loc"]), problem["msg"].removeprefix("Value error, ")
        problems.append(f"{where}: {message}" if where else message)
    return "; ".join(problems)


class _Column(_Strict):
    title: str                                       # the title word of the column on the title row
    field: LineField


class _LinesTable(_Strict):
    strategy: Literal["lines"]
    header_row_contains: str = Field(min_length=1)   # the text that marks the title row of the table
    columns: list[_Column]
    line_id_pattern: str = r"\d+"                    # expression: a row is a line when its line_id cell matches it in full
    basis_mark: str | None = None                    # expression: where the price basis starts inside a unit_price cell
    item_code_regex: str | None = None               # expression: one item code

    @model_validator(mode="after")
    def _check(self):
        fields = [column.field for column in self.columns]
        if "line_id" not in fields:
            raise ValueError("the table needs a line_id column")
        if len(set(fields)) != len(fields) or len({column.title for column in self.columns}) != len(fields):
            raise ValueError("a field has two columns or a title word names two columns")
        for pattern, what in ((self.line_id_pattern, "line_id_pattern"), (self.basis_mark, "basis_mark"), (self.item_code_regex, "item_code_regex")):
            if pattern is not None:
                _compile(pattern, what)
        return self


class _TextTable(_Strict):
    strategy: Literal["text"]
    line_regex: str                                  # expression with one named group per DocLine field, line_id among them
    line_start: str | None = None                    # expression: how a row that is a line begins; such a row must match line_regex
    item_code_regex: str | None = None

    @model_validator(mode="after")
    def _check(self):
        groups = _compile(self.line_regex, "line_regex").groupindex
        unknown = [name for name in groups if name not in get_args(LineField)]
        if unknown or "line_id" not in groups:
            raise ValueError(f"line_regex needs a group line_id and groups named after DocLine fields only (not {unknown})")
        for pattern, what in ((self.line_start, "line_start"), (self.item_code_regex, "item_code_regex")):
            if pattern is not None:
                _compile(pattern, what)
        return self


_TABLE = TypeAdapter(Annotated[_LinesTable | _TextTable, Field(discriminator="strategy")])


class LayoutTemplate(_Strict):
    name: str                                        # the file name without .json
    language: Literal["nl", "de", "en"]
    detect: list[str] = Field(min_length=1)          # expressions; all must match somewhere in the text of the PDF
    header: dict[HeaderField, str]                   # Document field -> expression with one group, the value
    table: dict                                      # a _LinesTable or a _TextTable, checked when the template is made
    decimal_comma: bool = True                       # the number convention of the layout: "1.234,50" (see normalize_number)

    @model_validator(mode="after")
    def _check(self):
        for pattern in self.detect:
            _compile(pattern, "detect")
        for field, pattern in self.header.items():
            if _compile(pattern, f"the expression of {field}").groups != 1:
                raise ValueError(f"the expression of {field} needs exactly one group, the value")
        try:
            _TABLE.validate_python(self.table)
        except ValidationError as error:
            raise ValueError(f"table: {_problems(error)}") from None
        return self


def load_templates(dir: Path = TEMPLATE_DIR) -> list[LayoutTemplate]:
    """The layout templates of the folder (the PO templates po_<name>.json are not among them), ordered by file name, which is the
    order detect_template tries them in. A template file is named like its template. Raises FileNotFoundError for a folder that is not
    there, because an empty list would pass for "no layout known"."""
    folder = Path(dir)
    if not folder.is_dir():
        raise FileNotFoundError(f"the template folder {folder} does not exist")
    templates = []
    for path in sorted(folder.glob("*.json")):
        if path.stem.startswith(PO_TEMPLATE_PREFIX):
            continue
        try:
            template = LayoutTemplate.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as error:
            raise ValueError(f"{path.name}: {_problems(error)}") from error
        if template.name != path.stem:
            raise ValueError(f"{path.name} holds the template {template.name!r}: a template file is named like its template")
        templates.append(template)
    return templates


def detect_template(pdf: Path, templates: list[LayoutTemplate]) -> LayoutTemplate | None:
    """The first template of the list whose detect expressions all match the text of the PDF, or None. A scan has no text: None."""
    text = "\n".join(line.text for line in extract_lines(Path(pdf)))
    if not text:
        return None
    return next((t for t in templates if all(re.search(pattern, text, re.MULTILINE) for pattern in t.detect)), None)


# --- reading ---

@dataclass(frozen=True)
class _Reading:
    """What turning a cell into a DocValue needs besides the cell."""
    document_date: date | None                       # the confirmation's own date, against which a week without a year is read
    decimal_comma: bool
    code: re.Pattern[str] | None                     # one item code, when the template says what it looks like

    def holds_several_codes(self, text: str) -> bool:
        return self.code is not None and len(list(self.code.finditer(text))) > 1


def read_with_template(pdf: Path, template: LayoutTemplate) -> Document:
    """Read the confirmation PDF with the template: Document(kind="confirmation", reader="parser:<name>"). A PDF without a text layer
    gives no lines and no header values. Raises ValueError for a PDF with a text layer that the template does not fit."""
    pdf = Path(pdf)
    rows = extract_rows(pdf)
    document = {"kind": "confirmation", "source_file": pdf.name, "pages": page_count(pdf), "reader": f"parser:{template.name}"}
    if not rows:
        return Document(**document)
    header = {field: _header_value(field, pattern, rows) for field, pattern in template.header.items()}
    table = _TABLE.validate_python(template.table)
    reading = _Reading(document_date=_own_date(header.get("document_date")), decimal_comma=template.decimal_comma,
                       code=re.compile(table.item_code_regex) if table.item_code_regex else None)
    where = f"{pdf.name} (template {template.name!r})"
    lines = _table_lines(rows, table, reading, where) if isinstance(table, _LinesTable) else _text_lines(rows, table, reading, where)
    return Document(**document, lines=lines, **header)


# --- the header ---

_HEADER_NORMALISERS = {"document_date": lambda text: normalize_date(text, None), "currency": normalize_currency}
_ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _header_value(field: str, pattern: str, rows: list[TextRow]) -> DocValue:
    """The value of a header field: the group of `pattern` on the first line it matches and finds something on."""
    for row in rows:
        found = re.search(pattern, row.line.text)
        text = ((found.group(1) or "") if found else "").strip()
        if not text:
            continue
        normalise = _HEADER_NORMALISERS.get(field)
        value = normalise(text) if normalise else text
        return DocValue(value=value, text=text, source=_source(row.line), uncertain=value is None)
    return DocValue()


def _own_date(document_date: DocValue) -> date | None:
    """The confirmation's own date as a day, or None when it is missing or unreadable (a range or a week is no day)."""
    if document_date.value and _ISO_DAY.fullmatch(document_date.value):
        return date.fromisoformat(document_date.value)
    return None


def _source(line: TextLine) -> SourceRef:
    return SourceRef(page=line.page, line=line.line, text=line.text)


# --- the lines ---

def _table_lines(rows: list[TextRow], table: _LinesTable, reading: _Reading, where: str) -> list[DocLine]:
    field_of = {column.title: column.field for column in table.columns}
    is_line_id = re.compile(table.line_id_pattern)
    basis_mark = re.compile(table.basis_mark) if table.basis_mark else None
    lines, columns = [], None                        # columns: where the columns begin and their fields, by the last title row seen
    for _, group in groupby(rows, key=lambda row: row.line.page):
        page_rows = list(group)
        title = next((n for n, row in enumerate(page_rows) if table.header_row_contains in row.line.text), None)
        if title is not None:
            columns, page_rows = _columns(page_rows[title], field_of, where), page_rows[title + 1:]
        elif columns is None:
            continue                                 # a page before the first title row, a cover page, has no lines
        starts, fields = columns                     # a page without a title row after one with it continues the table
        for row in page_rows:
            cells = {field: text for field, text in zip(fields, _cells(row.words, starts)) if field}
            if cells["line_id"] and is_line_id.fullmatch(cells["line_id"]):       # a total or a page number is no line
                lines.append(_doc_line(cells, row.line, reading, basis_mark))
    if columns is None:
        raise ValueError(f"{where}: no row contains {table.header_row_contains!r}, so the template does not fit this PDF")
    return lines


def _text_lines(rows: list[TextRow], table: _TextTable, reading: _Reading, where: str) -> list[DocLine]:
    pattern = re.compile(table.line_regex)
    start = re.compile(table.line_start) if table.line_start else None
    lines = []
    for row in rows:
        found = pattern.search(row.line.text)
        if found is None:
            if start and start.search(row.line.text):        # a line that is skipped would read as a PO line the confirmation leaves out
                raise ValueError(f"{where}, page {row.line.page}, line {row.line.line}: the row starts like a line but does not match "
                                 f"the line expression of the template: {row.line.text!r}")
            continue                                         # an introduction or a closing sentence
        cells = {field: text.strip() for field, text in found.groupdict().items() if text and text.strip()}
        if cells.get("line_id"):
            lines.append(_doc_line(cells, row.line, reading))
    if not lines:
        raise ValueError(f"{where}: no line of the text matches the line expression, so the template does not fit this PDF")
    return lines


def _columns(title: TextRow, field_of: dict[str, str], where: str) -> tuple[list[float], list[str | None]]:
    """Where each column of a page begins and the DocLine field of each (None for a title word the template does not name)."""
    fields = [field_of.get(word.text) for word in title.words]
    named = [field for field in fields if field]
    for field in named:
        if named.count(field) > 1:
            raise ValueError(f"{where}, page {title.line.page}: the title row names {field!r} more than once")
    if "line_id" not in named:
        raise ValueError(f"{where}, page {title.line.page}: the title row has no column for the line number")
    return [word.x0 for word in title.words], fields


def _cells(words: list[TextWord], starts: list[float]) -> list[str]:
    """The words of a row as one text per column: a word goes to the column whose title begins at or before the middle of the word."""
    cells: list[list[str]] = [[] for _ in starts]
    for word in words:
        cells[max(bisect_right(starts, (word.x0 + word.x1) / 2) - 1, 0)].append(word.text)
    return [" ".join(cell) for cell in cells]


def _doc_line(cells: dict[str, str], line: TextLine, reading: _Reading, basis_mark: re.Pattern[str] | None = None) -> DocLine:
    """One DocLine from the texts of its fields. A unit_price cell with a basis_mark in it is read in two parts: the number before the
    mark is the price, and the rest of the cell from the mark on is the price basis, unless a Per cell of its own gives one."""
    values = {field: _cell_value(field, text, line, reading) for field, text in cells.items() if field != "line_id"}
    price = cells.get("unit_price", "")
    mark = basis_mark.search(price) if basis_mark and price else None
    if mark:
        values["unit_price"] = _cell_value("unit_price", price, line, reading, read_from=price[:mark.start()])
        if not cells.get("price_basis"):
            values["price_basis"] = _cell_value("price_basis", price[mark.start():].strip(), line, reading)
    return DocLine(line_id=cells["line_id"], **values)


def _cell_value(field: str, text: str, line: TextLine, reading: _Reading, read_from: str | None = None) -> DocValue:
    """The DocValue of one cell: its text, its normalised value and its line. `read_from` is the part of the text the value is read
    from, when that is not all of it. An empty cell is DocValue()."""
    if not text:
        return DocValue()
    part = text if read_from is None else read_from
    if field in _NUMBER_FIELDS:
        value = normalize_number(part, decimal_comma=reading.decimal_comma)
    elif field == "unit":
        value = normalize_unit(part)
    elif field == "price_basis":
        value = normalize_basis(part)                # the basis alone: a number in the price or in the row is no basis
    elif field == "delivery_date":
        value = normalize_date(part, reading.document_date)
    else:
        value = text                                 # item_code and description stay as printed
    uncertain = value is None or (field == "item_code" and reading.holds_several_codes(text))
    return DocValue(value=value, text=text, source=_source(line), uncertain=uncertain)
