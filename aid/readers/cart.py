"""The cart reader: a page of a supplier's webshop cart, captured by the buyer before ordering, read into the Document of aid.schema
for the pre-check (design specification, section 4).

A page saved from the browser as HTML is read without a model (read_cart_html), whatever reads the other documents of a run: the
readers of a run send it here when is_cart_page says so. A screenshot of the page (PNG) is read by the LLM reader of
aid/readers/supplier_llm.py (read_cart_image).

The lines of an HTML page. A page has no text layer of the kind aid/readers/pdf_text.py gives a PDF, so this reader settles how its
lines are counted, and each source of a value read from a page cites them. The lines are the text lines of the page in document order:
the text of each block element (a heading, a paragraph, a div, a list item and the like), its runs of white space as one space, is one
line, and a <br> ends a line as well; an inline element (a span, a link, bold text) stays in the line of its block. A form control
gives the text it shows: an input its value, except an input of a type that shows no text (hidden, checkbox, radio, password, file,
image, color, range), and a select the text of its selected option (the one marked selected; none without one). A table row is one
line, the texts of its cells joined by " | ", the text of a cell being its own lines joined by a space (an empty cell leaves two
separators side by side: "KM-2101 | | 25"); a row whose cells hold a table is no line of its own, and its cells are blocks. What a
browser does not show gives no line: the head of the page with its title, scripts, styles, templates and what a page shows only without
scripts (noscript). A block or a row without text gives no line, and the lines are numbered from 1. The source of a value is (1, the
number of its line, the text of that line): a page is one page.

The cart table is the first <table> whose header row has an item column (artikel, item, product or artikelnummer), a quantity column
(aantal, qty, quantity or menge) and a price column (prijs, price or preis). Its header row is its first row of th cells only, else its
first row. A column matches on the whole text of its header cell, folded: case, runs of white space and the spaces, full stops and colons
around the text do not count, so "QTY." is a quantity column and "Prijs per stuk" no price column. The reader also reads, where the
header has them, a description column (omschrijving, description or beschreibung), a unit column (eenheid, unit or einheit), a price
basis column (per, basis or prijseenheid) and a delivery column (levering, leverdatum, delivery or lieferung). The lines of the cart are
the rows below the header row, except the rows of the table's head and foot, a row with a cell across several columns (a total or a
note), and a row whose item cell, folded as a header cell is, is one of TOTAL_WORDS (totaal, total, subtotaal, subtotal, btw, vat,
verzendkosten, verzending, shipping; "Totaal:" too) while its quantity cell is empty; the line_id of each is its position among them,
from 1.

The values. A cell's text goes through the normaliser of aid.schema for its field: the quantity and the price through normalize_number
(a decimal comma is read as such, and the schema's rule settles a number such as "1.234"), the unit through normalize_unit, the price
basis through normalize_basis ("per 100", "1") and the delivery date through normalize_date, a week without a year in the year of the
capture date. The item code and the description are their text. A cell that cannot be normalised keeps its text, with value None and
marked uncertain, never a guess; an empty cell is no value. The header values:
- supplier: the text of the first heading (h1 to h6), in a block of its own or in a cell of a table row, whose line it then cites,
  else the title of the page, which has no source;
- po_reference: the PO number of the first line that holds one (PO_NUMBER: a label that is a word of its own, as in "Uw referentie:
  250201", and a number of 6 to 8 digits that ends there, so that "Repo 250201" and "Order 1234567890" hold none);
- currency: the first currency code or word on the page (EUR, euro, USD, GBP), else the first currency sign (€, $, £);
- capture_date: the date of <meta name="capture-date" content="...">, which has no source: its content read as a date, or the
  date part of a date and time ("2026-11-03T14:05:00+01:00": the text before the "T" or the space, when the rest is a time). When the
  page has no such meta, or its content gives no date, the date of the first line that begins with "Datum" or "Date" and a date, as
  "Datum: 03-11-2026" does (the date runs to the end of the line or of its cell); else the day the file was last modified, which has
  no text and no source (a case keeps the time of its copy, see aid/case.py).
A cart has no document date, version or Incoterm. A page without a cart table raises ReaderError "no cart table in <file name>".
"""
import re
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import NavigableString, PreformattedString, Tag

from aid.readers.llm_client import LlmClient, LlmResponse, ReaderError
from aid.readers.supplier_llm import read_supplier_document
from aid.schema import (DocLine, DocValue, Document, SourceRef, normalize_basis, normalize_currency, normalize_date, normalize_number,
                        normalize_unit)

# A PO number on the page. The pattern of the settings (po_number_pattern) is not available to a reader, so it is written here, with
# the words for a reference that a cart page uses. The label is a word of its own and the number takes every digit that follows it.
PO_NUMBER = re.compile(r"(?<![^\W\d_])(?i:PO|Inkooporder|Bestelling|Bestellung|Order|Referentie|Reference)[\s:#-]*(\d{6,8})(?!\d)")
# The folded header texts of the columns the reader knows, by the field each column holds; a cart table has the first three.
COLUMN_NAMES = {"item_code": ("artikel", "item", "product", "artikelnummer"), "quantity": ("aantal", "qty", "quantity", "menge"),
                "unit_price": ("prijs", "price", "preis"), "description": ("omschrijving", "description", "beschreibung"),
                "unit": ("eenheid", "unit", "einheit"), "price_basis": ("per", "basis", "prijseenheid"),
                "delivery_date": ("levering", "leverdatum", "delivery", "lieferung")}
REQUIRED = ("item_code", "quantity", "unit_price")
# The words, folded, of an item cell that sums the cart up instead of naming an article: a total, the tax or the shipping costs.
TOTAL_WORDS = frozenset({"totaal", "total", "subtotaal", "subtotal", "btw", "vat", "verzendkosten", "verzending", "shipping"})

_BLOCKS = frozenset({"address", "article", "aside", "blockquote", "body", "caption", "dd", "details", "dialog", "div", "dl", "dt", "fieldset",
                     "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "html", "legend",
                     "li", "main", "nav", "ol", "p", "pre", "section", "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul"})
_HIDDEN = frozenset({"head", "title", "script", "style", "template", "noscript"})    # not shown by a browser
_TEXTLESS_INPUTS = frozenset({"hidden", "checkbox", "radio", "password", "file", "image", "color", "range"})   # show no value as text
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_CURRENCY_WORD = re.compile(r"(?<![^\W\d_])(?:eur|euro|usd|gbp)(?![^\W\d_])", re.IGNORECASE)     # a whole word, any case
_CURRENCY_SIGN = re.compile(r"[€$£]")
_DATE_LINE = re.compile(r"(?:datum|date)\b[\s:|]*(?P<text>\d[^|]*)", re.IGNORECASE)


@dataclass
class _Line:
    """One text line of the page (see the module docstring)."""
    number: int
    text: str
    element: Tag                                 # the block whose text the line is; for a table row, its <tr>
    cells: list[str] | None = None               # the texts of a table row's cells; None for a line that is no table row


def is_cart_page(path: Path, kind: str) -> bool:
    """Whether the document of a case is a cart page saved as HTML, which read_cart_html reads: the kind "cart" and the suffix .html or
    .htm, in any case."""
    return kind == "cart" and Path(path).suffix.lower() in (".html", ".htm")


def read_cart_html(path: Path) -> Document:
    """Read the cart page saved as HTML at `path` into a Document of kind "cart" (see the module docstring). Raises ReaderError "no
    cart table in <file name>" for a page without a cart table."""
    path = Path(path)
    soup = BeautifulSoup(path.read_bytes(), "lxml")
    lines = _page_lines(soup)
    table = _cart_table(soup, lines)
    if table is None:
        raise ReaderError(f"no cart table in {path.name}")
    columns, rows = table
    capture_date = _capture_date(soup, lines, path)
    day = _as_date(capture_date.value)
    doc_lines = [DocLine(line_id=str(number), **{field: _cell(field, row, column, day) for field, column in columns.items()})
                 for number, row in enumerate(rows, 1)]
    return Document(kind="cart", supplier=_supplier(soup, lines), po_reference=_po_reference(lines), currency=_currency(lines),
                    capture_date=capture_date, lines=doc_lines, source_file=path.name, pages=1, reader="cart_html")


def read_cart_image(path: Path, client: LlmClient) -> tuple[Document, LlmResponse]:
    """Read a screenshot of a cart page (PNG) with the model behind `client`: read_supplier_document(path, client, kind="cart")."""
    return read_supplier_document(path, client, kind="cart")


# --- the lines of the page ---

def _page_lines(root: Tag) -> list[_Line]:
    """The text lines of the page, or of one element of it, numbered from 1 (see the module docstring)."""
    lines: list[_Line] = []
    pending: list[str] = []                      # the text of the line being read, as the page gives it

    def end_line(element: Tag) -> None:
        text = " ".join("".join(pending).split())
        pending.clear()
        if text:
            lines.append(_Line(len(lines) + 1, text, element))

    def walk(node: Tag, block: Tag) -> None:
        for child in node.children:
            if isinstance(child, PreformattedString):                   # a comment, the doctype
                continue
            if isinstance(child, NavigableString):
                pending.append(str(child))
            elif child.name in _HIDDEN:
                continue
            elif child.name == "br":
                end_line(block)
            elif child.name in ("input", "select"):
                pending.append(f" {_control_text(child)} ")
            elif child.name == "tr" and child.find("table") is None:
                end_line(block)
                cells = [" ".join(line.text for line in _page_lines(cell)) for cell in _cells(child)]
                if any(cells):
                    lines.append(_Line(len(lines) + 1, " ".join(" | ".join(cells).split()), child, cells))
            elif child.name in _BLOCKS:
                end_line(block)
                walk(child, child)
                end_line(child)
            else:                                                        # an inline element
                walk(child, block)

    walk(root, root)
    end_line(root)
    return lines


def _control_text(control: Tag) -> str:
    """The text a form control shows: an input its value, none for an input of a type that shows no text; a select the text of its
    selected option, none without one."""
    if control.name == "select":
        option = control.find("option", selected=True)
        return option.get_text() if option is not None else ""
    return "" if str(control.get("type", "text")).strip().casefold() in _TEXTLESS_INPUTS else str(control.get("value", ""))


def _cells(row: Tag) -> list[Tag]:
    return row.find_all(["td", "th"], recursive=False)


def _source(line: _Line) -> SourceRef:
    return SourceRef(page=1, line=line.number, text=line.text)


# --- the cart table ---

def _cart_table(soup: BeautifulSoup, lines: list[_Line]) -> tuple[dict[str, int], list[_Line]] | None:
    """The columns of the cart table, as {field: column}, and its rows that are lines of the cart; None for a page without one."""
    rows: dict[int, list[_Line]] = {}            # the row lines of each table, by the table's id
    for line in lines:
        if line.cells is not None:
            rows.setdefault(id(line.element.find_parent("table")), []).append(line)
    for table in soup.find_all("table"):
        table_rows = rows.get(id(table), [])
        if not table_rows:
            continue
        start = next((n for n, row in enumerate(table_rows) if all(cell.name == "th" for cell in _cells(row.element))), 0)
        columns = _columns(table_rows[start])
        if all(field in columns for field in REQUIRED):
            return columns, [row for row in table_rows[start + 1:] if _is_cart_line(row, columns)]
    return None


def _columns(header: _Line) -> dict[str, int]:
    """The column of each field the header row names: the position of the first cell whose folded text is one of the field's names,
    counted in columns (a cell across several columns takes as many)."""
    columns, position = {}, 0
    for cell, text in zip(_cells(header.element), header.cells):
        folded = _fold(text)
        for field, names in COLUMN_NAMES.items():
            if folded in names:
                columns.setdefault(field, position)
        position += _span(cell)
    return columns


def _fold(text: str) -> str:
    """The text with its case folded, runs of white space as one space and the spaces, full stops and colons around it removed."""
    return " ".join(text.casefold().split()).strip(" .:")


def _is_cart_line(row: _Line, columns: dict[str, int]) -> bool:
    """Whether a row below the header row is a line of the cart: not in the table's head or foot, no cell across several columns, and
    not a total, the tax or the shipping (a word of TOTAL_WORDS in the item cell and an empty quantity cell)."""
    if row.element.parent.name in ("thead", "tfoot") or any(_span(cell) > 1 for cell in _cells(row.element)):
        return False
    return not (_fold(_text_at(row, columns["item_code"])) in TOTAL_WORDS and not _text_at(row, columns["quantity"]))


def _span(cell: Tag) -> int:
    """The number of columns the cell takes: its colspan, 1 when it has none that is a whole number above zero."""
    span = str(cell.get("colspan", "1")).strip()
    return int(span) if span.isdecimal() and int(span) > 0 else 1


# --- the values ---

def _cell(field: str, row: _Line, column: int, day: date | None) -> DocValue:
    """The value of `field` in the cell of the row at `column`: its text and its normalised value, citing the row's line."""
    text = _text_at(row, column)
    if not text:
        return DocValue()
    value = _normalise(field, text, day)
    return DocValue(value=value, text=text, source=_source(row), uncertain=value is None)


def _text_at(row: _Line, column: int) -> str:
    """The text of the row's cell at `column`; "" for a row that ends before it."""
    return row.cells[column] if column < len(row.cells) else ""


def _normalise(field: str, text: str, day: date | None) -> str | None:
    if field in ("quantity", "unit_price"):
        return normalize_number(text)
    if field == "unit":
        return normalize_unit(text)
    if field == "price_basis":
        return normalize_basis(text)
    if field == "delivery_date":
        return normalize_date(text, day)
    return text                                  # the item code and the description


def _supplier(soup: BeautifulSoup, lines: list[_Line]) -> DocValue:
    for line in lines:
        if heading := _heading_text(line):
            return DocValue(value=heading, text=heading, source=_source(line))
    title = " ".join(soup.title.get_text().split()) if soup.title is not None else ""
    return DocValue(value=title, text=title) if title else DocValue()


def _heading_text(line: _Line) -> str:
    """The text of the heading on the line: the line's own text when it is the text of a heading, the text of the first heading in the
    cells of a table row; "" for a line without one."""
    if line.cells is None:
        return line.text if line.element.name in _HEADINGS or line.element.find_parent(_HEADINGS) else ""
    heading = line.element.find(_HEADINGS)
    return " ".join(part.text for part in _page_lines(heading)) if heading is not None else ""


def _po_reference(lines: list[_Line]) -> DocValue:
    for line in lines:
        if found := PO_NUMBER.search(line.text):
            return DocValue(value=found[1], text=found[1], source=_source(line))
    return DocValue()


def _currency(lines: list[_Line]) -> DocValue:
    for pattern in (_CURRENCY_WORD, _CURRENCY_SIGN):
        for line in lines:
            if found := pattern.search(line.text):
                return DocValue(value=normalize_currency(found[0]), text=found[0], source=_source(line))
    return DocValue()


def _capture_date(soup: BeautifulSoup, lines: list[_Line], path: Path) -> DocValue:
    meta = soup.find("meta", attrs={"name": lambda name: name is not None and name.strip().casefold() == "capture-date"})
    content = " ".join(str(meta.get("content", "")).split()) if meta is not None else ""
    if content and (value := _meta_date(content)) is not None:
        return DocValue(value=value, text=content)
    for line in lines:
        if found := _DATE_LINE.match(line.text):
            text = found["text"].strip()
            value = normalize_date(text, None)
            return DocValue(value=value, text=text, source=_source(line), uncertain=value is None)
    return DocValue(value=date.fromtimestamp(path.stat().st_mtime).isoformat())


def _meta_date(content: str) -> str | None:
    """The date the capture-date meta gives: its content read as a date, else the date part of a date and time, the text before its
    first "T" or space when the rest is a time; None when neither reads."""
    value = normalize_date(content, None)
    if value is None and (found := re.fullmatch(r"(.+?)[T ](.+)", content)) and _is_time(found[2]):
        value = normalize_date(found[1], None)
    return value


def _is_time(text: str) -> bool:
    """Whether the text is a time of day in ISO form ("14:05:00+01:00", "14:05")."""
    try:
        time.fromisoformat(text)
    except ValueError:
        return False
    return True


def _as_date(value: str | None) -> date | None:
    """The capture date as a date (a week as its Monday), against which a delivery week without a year is read; None for no date or a
    range."""
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None
