"""The reader of the purchase order PDF: a template parser, no AI.

read_po reads a PO PDF in one of two ways. Without a layout it reads with the template of the made-up layout (read_po_pdf), as
described first below. With a layout from the settings (the setting po_layout, a PoLayout of aid/readers/po_layout.py) it reads
with that layout and with nothing else (read_po_layout), as described under "A layout from the settings".

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

A layout from the settings. The real PO PDF is the company's, so its layout is data of the settings file and not a template of the
repository. The layout names words that stand on the page (see aid/readers/po_layout.py), and read_po_layout reads by these rules.

Text. The layout reader reads the text layer with the cells that a PDF prints close together cut apart (extract_rows with cut_cells,
aid/readers/pdf_text.py): a price that ends 2 points before its basis is two words, where a template reads one.

Fit. A PDF fits the layout (fits) when its first page holds a text line whose words are the words of the title row, word for word
and without regard to case, and a line that holds the PO label with a value after it. read_po_layout refuses a PDF that does not
fit with a ValueError; it never falls back on the template. It also refuses a PDF that fits and gives no PO number, more than one
PO number (a batch print of two POs), or no order line, because the store could not file it, would file two POs as one, or would
file an empty order. The errors name the file and the settings, never a text of the PDF or a word of the layout.

Header. A label is looked for as a word or a phrase of its own, case not counting: no letter, digit or underscore touches it, so
"Order" is not found in "Ordernummer", and a label of several words ("Inkooporder nr.") is matched as a whole, with any spaces
between its words. A place of a label that lies inside a longer label of the layout at the same place is passed by ("Order" inside
"Order date"). The text after a label is what follows it on its line, without the colons, hashes, full stops and spaces behind the
label, cut where another label of the layout begins, so that two labels may share a line ("Bestelnummer: 123 Besteldatum:
03-11-2026"). The PO number is the first word of that text when it holds a digit, the version when it is a whole number, and the
date when it reads as a date (normalize_date), the PO number and the version without a comma, semicolon, colon or full stop
printed right after them ("Inkooporder 250101," is PO 250101); a line where that word is none of these gives nothing, and the next
line that holds the label is tried, so that a title "Inkooporder  Pagina 1 van 1" above the numbered line is passed by. Every
line whose label is followed by a PO number counts for the refusal of two POs; the same number repeated in a page header is one.
The supplier, the currency and the Incoterm are the whole text after the first place of their label, empty or not. With
supplier_line the supplier is the whole text of that line of page 1.

Lines table. The title row of a page is its first line whose words are those of the layout's title row. Each of its words is a
column and gives the column the place of its title, from the left edge of the word to its right edge; a word with the meaning
"same" widens the column on its left instead, and a "skip" column is cut like any other and not read. A word of a later row
belongs to the column whose title it lies under for the greatest width, the leftmost of them when two are equal, so that a number
printed flush right under a short title is read with that title although it begins left of it. A word that lies under no title
goes with the next word when that word lies under a title and the two are one number printed in two words, one space apart: a
number with a space before its thousands ("1 234,50") or a currency sign and a number ("€ 4,20"). Any other word under no title
belongs to the column whose title begins at or before the middle of the word, as with a template.
A page with the title row is cut with its own columns, and its rows above the title row are not read. A page without one that
follows a page with one continues the table with the same columns, and all its rows are looked at. A page before the first title
row has no lines.
Which rows are lines. With a line_id column a row below the title row is an order line when its line number cell is a whole
number and its quantity, unit price or net price cell, whichever of them the layout has, reads as a number (normalize_number with
the layout's decimal convention); a layout with none of the three has its lines by the line number alone. Without a line_id column
a row is an order line when its item code cell is filled and its quantity cell reads as a number, and the lines are numbered 1, 2,
3 over all pages. Any other row, a total, the second row of a description or a footer that begins with a postal code, is no line
and is not read. table_end is matched as whole words, as a label is, and only on a row that is no order line: the first such row
that holds it ends the table on its page, and a later page is read only when it shows the title row again.
Values are made as with a template (see Values above), the numbers read with the layout's decimal convention. The layout's
prices_per_piece changes nothing here: the lines keep the price basis the PDF prints, which is none.

Shared with the settings page. read_po_layout_rows does what read_po_layout does on text rows already extracted, so that the trial
read of the settings page (aid/review/exact_settings.py) reads a PDF once. That page also uses read_layout, layout_labels,
label_pattern, after_label, header_values and folded; their earlier names, with an underscore, stay as aliases.
"""
import re
import unicodedata
from bisect import bisect_right
from collections.abc import Iterable, Iterator, Sequence
from datetime import date
from functools import lru_cache
from itertools import groupby
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from aid.readers.pdf_text import TextLine, TextRow, TextWord, extract_rows, page_count
from aid.readers.po_layout import PoLayout
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


def read_po(pdf: Path, layout: PoLayout | None) -> Document:
    """Read the PO PDF: with the template of the made-up layout when no layout is given (read_po_pdf), and with the layout when one
    is given (read_po_layout). With a layout the template is never tried, so a PDF the layout does not fit is refused."""
    return read_po_pdf(pdf) if layout is None else read_po_layout(pdf, layout)


def layout_first_page(pdf: Path) -> str:
    """The first page of the PDF as the layout reader reads it, a line of text to each text line, with the cells that the PDF prints
    close together cut apart (extract_rows with cut_cells): the text that the watched folder holds against a layout. "" for a scan."""
    return "\n".join(row.line.text for row in extract_rows(Path(pdf), cut_cells=True) if row.line.page == 1)


def fits(first_page_text: str, layout: PoLayout) -> bool:
    """Whether a PDF with this first page, a line of text to each text line, fits the layout: one line holds the words of the title
    row and nothing else, word for word and without regard to case, and one line holds the PO label as a word or phrase of its own,
    not inside a longer label of the layout, with a value after it (see the module docstring). A page without text, as a scan has,
    fits no layout."""
    lines = first_page_text.splitlines()
    labels = _labels(layout)
    return (any(_folded(line.split()) == _folded(layout.title_row) for line in lines)
            and any(text for line in lines for text in _after_label(line, labels["po_reference"], list(labels.values()))))


def header_lines(first_page_text: str, layout: PoLayout) -> list[str]:
    """The lines of a first page above its first title row, a line of text to each text line; all its lines when it holds no title
    row. The watched folder looks there for the title of a confirmation (titled_confirmation of aid/sources/mailbox.py)."""
    lines = first_page_text.splitlines()
    title = _folded(layout.title_row)
    return lines[:next((n for n, line in enumerate(lines) if _folded(line.split()) == title), len(lines))]


def read_po_layout(pdf: Path, layout: PoLayout) -> Document:
    """Read the PO PDF with the layout: Document(kind="po", reader="po_pdf:layout"). Raises ValueError for a PDF that does not fit the
    layout, and for one that fits and gives no PO number, more than one PO number or no order line (see the module docstring)."""
    pdf = Path(pdf)
    return read_po_layout_rows(pdf, extract_rows(pdf, cut_cells=True), layout)


def read_po_layout_rows(pdf: Path, rows: list[TextRow], layout: PoLayout, doc: Document | None = None) -> Document:
    """read_po_layout on the text rows of the PDF that extract_rows with cut_cells gave, and on what read_layout read from them when
    the caller has it: the same Document, or the same ValueError, without reading the PDF again. The trial read of the settings page
    (aid/review/exact_settings.py) asks it so."""
    if not fits("\n".join(row.line.text for row in rows if row.line.page == 1), layout):
        raise ValueError(f"{pdf.name}: the PDF does not fit the purchase order layout of the settings (po_layout): its first page has no "
                         "line with the words of the title row, or no line with the PO label and a value after it")
    if doc is None:
        doc = read_layout(pdf, rows, layout)
    if doc.po_reference.value is None:
        raise ValueError(f"{pdf.name}: no PO number found: on no line is the PO label of the layout (po_layout.po_label) followed by a "
                         "word with a digit, so the PDF cannot be filed")
    labels = layout_labels(layout)
    if len({value.value for value in header_values(rows, "po_reference", labels["po_reference"], list(labels.values()))}) > 1:
        raise ValueError(f"{pdf.name}: the PO label of the layout (po_layout.po_label) is followed by more than one PO number, so the PDF "
                         "holds more than one PO and cannot be filed")
    if not doc.lines:
        raise ValueError(f"{pdf.name}: no order line found below the title row of the layout (po_layout.title_row, po_layout.columns), "
                         "so the PDF cannot be filed")
    return doc


def read_layout(pdf: Path, rows: list[TextRow], layout: PoLayout) -> Document:
    """What the layout reads from the text rows of the PDF, whatever it finds: read_po_layout without its refusals."""
    header = _layout_header(rows, layout)
    lines = _layout_lines(rows, layout, _as_date(header.get("document_date")))
    return Document(kind="po", source_file=pdf.name, pages=page_count(pdf), reader="po_pdf:layout", lines=lines, **header)


_read_layout = read_layout                       # the name the helpers below and the tests knew it by


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


# The header fields that a layout gives a label, each with the name of its label in the layout.
_LABELS = {"po_reference": "po_label", "version_hint": "version_label", "document_date": "date_label", "supplier": "supplier_label",
           "currency": "currency_label", "incoterm": "incoterm_label"}
# The fields whose value is the first word after the label, each with what that word must be: a PO number holds a digit, a version is
# a whole number, a date reads as a date. A line where the word is none of these gives nothing, and the next line is tried.
_ONE_WORD = {"po_reference": lambda word: any(char.isdigit() for char in word), "version_hint": str.isdecimal,
             "document_date": lambda word: normalize_date(word, None) is not None}
_TRIMMED = ("po_reference", "version_hint")      # one-word values that lose a comma, semicolon, colon or full stop printed after them
_LEFT_OUT = re.compile(r"[\s:#.]*")                                # what follows a label and is no part of its value


def layout_labels(layout: PoLayout) -> dict[str, str]:
    """The label of each header field that the layout gives one; a label without text is none."""
    return {field: label for field, name in _LABELS.items() if (label := (getattr(layout, name) or "").strip())}


@lru_cache(maxsize=256)
def label_pattern(label: str) -> re.Pattern[str]:
    """The label as it is looked for in a line: its words, with any white space between them, without regard to case and as a word
    of its own. Where the label begins or ends with a letter, a digit or an underscore, none of those may touch it there."""
    words = label.split()
    before = r"(?<!\w)" if re.match(r"\w", words[0]) else ""
    after = r"(?!\w)" if re.search(r"\w$", words[-1]) else ""
    return re.compile(before + r"\s+".join(re.escape(word) for word in words) + after, re.IGNORECASE)


_labels, _label_pattern = layout_labels, label_pattern          # the names the reader's own functions use


def _label_places(text: str, label: str, labels: Sequence[str]) -> list[re.Match[str]]:
    """Where the label stands in the text as a word or phrase of its own, left to right, but for a place that lies inside a longer
    label of the layout standing at the same place ("Order" inside "Order date")."""
    places = list(_label_pattern(label).finditer(text))
    if not places:
        return []
    longer = [(found.start(), found.end()) for other in labels if other != label for found in _label_pattern(other).finditer(text)]
    return [place for place in places if not any(start <= place.start() and place.end() <= end and end - start > place.end() - place.start()
                                                 for start, end in longer)]


def after_label(text: str, label: str, labels: Sequence[str]) -> list[str]:
    """For each place of the label on a line (_label_places), the text that follows it: without the colons, hashes, full stops and
    spaces behind the label, and cut where another label of the layout begins. [] for a line that does not hold the label."""
    others = [other for other in labels if other != label]
    texts = []
    for place in _label_places(text, label, labels):
        rest = text[place.end():]
        rest = rest[_LEFT_OUT.match(rest).end():]
        cuts = [cut.start() for other in others if (cut := _label_pattern(other).search(rest))]
        texts.append(rest[:min(cuts, default=len(rest))].strip())
    return texts


_after_label = after_label


def header_values(rows: list[TextRow], field: str, label: str, labels: Sequence[str]) -> Iterator[DocValue]:
    """The values that the lines of the PDF give a header field, in the order of the PDF (see the module docstring). For the PO number,
    the version and the date each place of the label gives one when the first word after it is what the field asks (_ONE_WORD); the
    supplier, the currency and the Incoterm take the whole text after the first place of their label, also when it is empty."""
    wanted = _ONE_WORD.get(field)
    for row in rows:
        for text in _after_label(row.line.text, label, labels):
            if wanted is None:
                yield _header_doc_value(field, text, row)
                return
            word = text.split()[0] if text else ""
            if field in _TRIMMED:
                word = word.rstrip(",;:.")       # "Inkooporder 250101," is PO 250101
            if word and wanted(word):
                yield _header_doc_value(field, word, row)


_header_values = header_values


def _header_doc_value(field: str, text: str, row: TextRow) -> DocValue:
    """The DocValue of a header field: its text, its normal form and its line. No text is no value."""
    if not text:
        return DocValue()
    normalise = _HEADER_NORMALISERS.get(field)
    value = normalise(text) if normalise else text
    return DocValue(value=value, text=text, source=_source(row.line), uncertain=value is None)


def _layout_header(rows: list[TextRow], layout: PoLayout) -> dict[str, DocValue]:
    """The header fields a layout reads (see the module docstring): each from the first place of its label that gives a value, and the
    supplier from its line of page 1 when the layout names that line."""
    labels = _labels(layout)
    header = {field: next(_header_values(rows, field, label, list(labels.values())), DocValue()) for field, label in labels.items()}
    if layout.supplier_line is not None:
        row = next((row for row in rows if (row.line.page, row.line.line) == (1, layout.supplier_line)), None)
        header["supplier"] = DocValue(value=row.line.text, text=row.line.text, source=_source(row.line)) if row else DocValue()
    return header


def _fold(text: str) -> str:
    """A text as the layout's words are compared with the page's: without regard to case, with single spaces."""
    return " ".join(text.casefold().split())


def folded(words: Iterable[str]) -> list[str]:
    """Words as the layout's words are compared with the page's (_fold)."""
    return [_fold(word) for word in words]


_folded = folded


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


# --- the lines table of a layout ---

_NUMBER_CELLS = ("quantity", "unit_price", "net_price")      # with line numbers, one of these that the layout has must read as a number


def _layout_lines(rows: list[TextRow], layout: PoLayout, document_date: date | None) -> list[DocLine]:
    """The order lines a layout reads from the text rows of the PDF (see the module docstring)."""
    title = _folded(layout.title_row)
    fields = [None if meaning == "skip" else meaning for meaning in layout.columns if meaning != "same"]       # one per column
    numbers = [field for field in _NUMBER_CELLS if field in fields]
    end = _label_pattern(layout.table_end) if layout.table_end else None
    lines: list[DocLine] = []
    spans, ended = None, False                       # where the titles of the columns stand, by the last title row seen; the end met
    for _, group in groupby(rows, key=lambda row: row.line.page):
        page_rows = list(group)
        at = next((n for n, row in enumerate(page_rows) if _folded(word.text for word in row.words) == title), None)
        if at is not None:
            spans, page_rows, ended = _title_spans(page_rows[at].words, layout.columns), page_rows[at + 1:], False
        elif spans is None or ended:
            continue                                 # before the first title row, or after the end text: no lines on this page
        for row in page_rows:                        # a page without a title row after one with it continues the table
            cells = {field: text for field, text in zip(fields, _cells_under(row.words, spans, layout.decimal_comma)) if field}
            if "line_id" in cells:
                line_id = cells.pop("line_id")       # a whole number, and a quantity or a price: no total, remark or footer
                is_line = line_id.isdecimal() and (not numbers or any(_reads_as_number(cells[field], layout) for field in numbers))
            else:
                line_id = str(len(lines) + 1)
                is_line = bool(cells["item_code"]) and _reads_as_number(cells["quantity"], layout)
            if is_line:
                lines.append(DocLine(line_id=line_id, **{field: _cell_value(field, text, row.line, document_date, layout.decimal_comma)
                                                         for field, text in cells.items()}))
            elif end is not None and end.search(row.line.text):
                ended = True                         # the end text ends the table on this page
                break
    return lines


def _reads_as_number(text: str, layout: PoLayout) -> bool:
    return normalize_number(text, decimal_comma=layout.decimal_comma) is not None


def _title_spans(words: list[TextWord], meanings: Sequence[str]) -> list[tuple[float, float]]:
    """Where the title of each column stands on the title row: from the left edge of its first word to the right edge of its last.
    A word with the meaning "same" belongs to the title on its left."""
    spans: list[tuple[float, float]] = []
    for word, meaning in zip(words, meanings):
        if meaning == "same":
            spans[-1] = (spans[-1][0], word.x1)
        else:
            spans.append((word.x0, word.x1))
    return spans


def _cells_under(words: list[TextWord], spans: list[tuple[float, float]], decimal_comma: bool) -> list[str]:
    """The words of a row as one text per column: a word goes to the column whose title it lies under for the greatest width, the
    leftmost of them when two are equal. A word under no title goes to the column of the next word when that word lies under a title
    and the two are one number printed in two words (_one_number); otherwise to the column whose title begins at or before its middle."""
    starts = [x0 for x0, _ in spans]
    under = [[min(word.x1, x1) - max(word.x0, x0) for x0, x1 in spans] for word in words]
    best = [max(range(len(spans)), key=overlaps.__getitem__) for overlaps in under]
    cells: list[list[str]] = [[] for _ in spans]
    for n, word in enumerate(words):
        if under[n][best[n]] > 0:
            column = best[n]
        elif n + 1 < len(words) and under[n + 1][best[n + 1]] > 0 and _one_number(word, words[n + 1], decimal_comma):
            column = best[n + 1]
        else:
            column = max(bisect_right(starts, (word.x0 + word.x1) / 2) - 1, 0)
        cells[column].append(word.text)
    return [" ".join(cell) for cell in cells]


_FIRST_GROUP = re.compile(r"[-+]?\d{1,3}")              # the digits before the first space of a number like "1 234,50"
_NEXT_GROUP = re.compile(r"\d{3}(?!\d)")                # and the three digits after it


def _one_number(first: TextWord, second: TextWord, decimal_comma: bool) -> bool:
    """Whether two words of a row are one number printed in two words: they stand one space apart (the gap between them is narrower
    than an average character of theirs, which a space is and the gap between two columns is not), and they read as a number with a
    space before its thousands ("1 234,50", "12 345") or as a currency sign and a number ("€ 4,20"). So the last word of a description
    ("M6 x 20") and a quantity printed far to its right ("100") are not one number."""
    if second.x0 - first.x1 > (first.x1 - first.x0 + second.x1 - second.x0) / (len(first.text) + len(second.text)):
        return False
    if all(unicodedata.category(char) == "Sc" for char in first.text):
        return normalize_number(second.text, decimal_comma=decimal_comma) is not None
    return (_FIRST_GROUP.fullmatch(first.text) is not None and _NEXT_GROUP.match(second.text) is not None
            and normalize_number(f"{first.text} {second.text}", decimal_comma=decimal_comma) is not None)
