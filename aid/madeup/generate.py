"""The made-up document set: purchase orders, order confirmations and cart pages drawn from the specs.

Real company documents may not be used until a written permission exists, so the tests and the model bake-off run
on this set. Each case is one JSON spec in aid/madeup/spec (MadeUpSpec). generate() draws every case into a folder:
for a confirmation case the PO PDF of each version in the fixed layout po_madeup and the supplier's confirmation in
one of six layouts; for a cart case the saved cart page (and a screenshot of it) with the PO export it is checked
against. write_references() writes the reference answer of every case. Its labels are worked out from the values
under the comparison rules of the scoring manual (docs/process/Scoring_Manual_Draft_2026-10-21.md, sections 2 and 3)
and must agree with the differences each spec declares. aid/madeup/README.md describes the cases.

    python3 -m aid.madeup.generate --spec aid/madeup/spec --out aid/madeup/out
"""
import argparse
import csv
import html
import io
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Annotated, Literal

import pypdfium2 as pdfium
from PIL import Image
from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

# ---------------------------------------------------------------- the spec

Language = Literal["nl", "de", "en"]
Layout = Literal["table_nl", "table_de", "text_en", "merged", "total_only", "scan"]
FieldName = Literal["price", "quantity", "unit", "delivery_date", "line_presence", "required_info"]
# A difference's kind, in the words of the comparison step's flag details.
Kind = Literal["different", "missing on the document", "missing line", "extra line", "not in cart", "later", "earlier"]
LineField = Literal["item_code", "description", "quantity", "unit", "unit_price", "price_basis", "discount_pct", "line_total", "delivery_date"]
Unit = Literal["pc", "m"]                                                    # normalised units
Number = Annotated[str, StringConstraints(pattern=r"^\d+(\.\d+)?$")]         # decimal point, no thousands separator
Money = Annotated[str, StringConstraints(pattern=r"^\d+\.\d{2}$")]           # two decimals
Basis = Annotated[str, StringConstraints(pattern=r"^[1-9]\d*$")]             # the price is per this many units
Day = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
DeliveryDate = Annotated[str, StringConstraints(pattern=r"^\d{4}-W\d{2}$|^\d{4}-\d{2}-\d{2}(/\d{4}-\d{2}-\d{2})?$")]  # a day, an ISO week or a range

LAYOUT_LANGUAGE = {"table_nl": "nl", "merged": "nl", "total_only": "nl", "scan": "nl", "table_de": "de", "text_en": "en"}

# Item codes are KM-<block><two-digit line number>, the block being the case number. MU01 and CART02 keep the codes
# the plan's later tests name (blocks 10 and 20), so MU10 and MU20 take the blocks MU01 and CART02 would have had
# under the rule, and CART01 takes 21. Every code belongs to one case.
ITEM_BLOCKS = {"MU01": "10", "MU10": "01", "MU20": "22", "CART01": "21", "CART02": "20"}


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")   # a misspelled key in a spec fails instead of being ignored


class PoLine(_Spec):
    line_id: str
    item_code: Annotated[str, StringConstraints(pattern=r"^KM-\d{4}$")]
    description: str
    quantity: Number
    unit: Unit
    unit_price: Money
    price_basis: Basis
    delivery_date: Day


class PoVersion(_Spec):
    version: int
    version_date: date
    lines: list[PoLine]


class Po(_Spec):
    po_number: str
    supplier: str
    currency: Literal["EUR"]
    incoterm: str
    buyer_authorization_exceeded: bool          # the PO total is above the made-up test limit in aid/tests/conftest.py
    versions: list[PoVersion]


class DocumentLine(_Spec):
    """A line of a confirmation or a cart page as the document prints it; None means the value is not printed."""
    line_id: str
    item_code: Annotated[str, StringConstraints(pattern=r"^KM-\d{4}( / KM-\d{4})*$")]   # a merged line holds several codes
    description: str
    quantity: Number
    unit: Unit | None
    unit_price: Money | None
    price_basis: Basis | None
    delivery_date: DeliveryDate | None
    discount_pct: Number | None = None
    line_total: Money | None = None              # printed by the total_only layout only
    text_override: dict[LineField, str] = {}     # printed instead of the value's usual form; "" leaves the cell empty

    @property
    def codes(self) -> list[str]:
        return self.item_code.split(" / ")


class Difference(_Spec):
    line_id: str = ""                            # the PO line; an extra line's own number; blank for required information
    field: FieldName
    kind: Kind


class Confirmation(_Spec):
    layout: Layout
    language: Language
    document_date: date
    version_quoted: int | None = None
    incoterm: str | None
    lines: list[DocumentLine]
    differences: list[Difference] = []

    @model_validator(mode="after")
    def _layout_rules(self):
        if LAYOUT_LANGUAGE[self.layout] != self.language:
            raise ValueError(f"layout {self.layout} is written in {LAYOUT_LANGUAGE[self.layout]}, not {self.language}")
        for line in self.lines:
            if self.layout == "total_only" and (line.line_total is None or line.unit_price is not None):
                raise ValueError(f"line {line.line_id}: the total_only layout prints a line total and no unit price")
        return self


class Cart(_Spec):
    layout: Literal["html", "png"]               # png: the saved page and a screenshot of it
    capture_date: date
    lines: list[DocumentLine]
    differences: list[Difference] = []


class MadeUpSpec(_Spec):
    case_id: Annotated[str, StringConstraints(pattern=r"^(MU|CART)\d{2}$")]
    po: Po
    confirmation: Confirmation | None = None
    cart: Cart | None = None

    @property
    def document(self) -> Confirmation | Cart:
        return self.confirmation or self.cart

    @model_validator(mode="after")
    def _conventions(self):
        """PO numbers 2501NN and 2502NN, suppliers S01 to S22 and the item-code blocks, which later tests rely on."""
        if (self.confirmation is None) == (self.cart is None):
            raise ValueError("a case has either a confirmation or a cart")
        number, cart = int(self.case_id[-2:]), self.case_id.startswith("CART")
        if self.po.po_number != f"{2502 if cart else 2501}{number:02d}":
            raise ValueError(f"{self.case_id} has PO number {self.po.po_number}")
        if self.po.supplier != f"S{number + 20 if cart else number:02d}":
            raise ValueError(f"{self.case_id} has supplier {self.po.supplier}")
        block = ITEM_BLOCKS.get(self.case_id, f"{number:02d}")
        for version in self.po.versions:
            for line in version.lines:
                if line.item_code != f"KM-{block}{int(line.line_id):02d}":
                    raise ValueError(f"{self.case_id} PO line {line.line_id} has item code {line.item_code}")
        for line in self.document.lines:
            if not all(code.startswith(f"KM-{block}") for code in line.codes):
                raise ValueError(f"{self.case_id} document line {line.line_id} has a code outside block {block}")
        quoted = self.confirmation.version_quoted if self.confirmation else None
        if quoted is not None and quoted not in [v.version for v in self.po.versions]:
            raise ValueError(f"{self.case_id} quotes version {quoted}, which the PO does not have")
        return self


def load_specs(spec_dir: Path) -> list[MadeUpSpec]:
    """Every spec of the folder, MU01 to MU20 first and then the cart cases."""
    paths = sorted(Path(spec_dir).glob("*.json"), key=lambda p: (p.stem.startswith("CART"), p.stem))
    specs = [MadeUpSpec.model_validate_json(p.read_text(encoding="utf-8")) for p in paths]
    for path, spec in zip(paths, specs):
        if path.stem != spec.case_id:
            raise ValueError(f"{path.name} holds case {spec.case_id}")
    return specs


# ---------------------------------------------------------------- values as the documents print them

UNIT_WORDS = {"nl": {"pc": "st", "m": "m"}, "de": {"pc": "Stk", "m": "m"}, "en": {"pc": "pcs", "m": "m"}}
WEEK_WORD = {"nl": "week", "de": "KW", "en": "week"}
CENT = Decimal("0.01")


def _number(value: str | Decimal, language: Language, places: int | None = None) -> str:
    """1.234,50 in Dutch and German, 1,234.50 in English; places=None keeps the digits as given."""
    text = f"{Decimal(value):,.{places}f}" if places is not None else f"{Decimal(value):,f}"
    return text if language == "en" else text.translate(str.maketrans(",.", ".,"))


def day_text(value: str | date, language: Language) -> str:
    """A day as the documents print it: 16-11-2026 in Dutch, 16.11.2026 in German, 2026-11-16 in English."""
    day = date.fromisoformat(value) if isinstance(value, str) else value
    return {"nl": f"{day:%d-%m-%Y}", "de": f"{day:%d.%m.%Y}", "en": day.isoformat()}[language]


def _delivery(value: str, language: Language) -> str:
    """A day, a week number ("KW 48" in German) or a range of two days."""
    if "-W" in value:
        return f"{WEEK_WORD[language]} {int(value[-2:])}"
    return " - ".join(day_text(part, language) for part in value.split("/"))


def cell_text(line: PoLine | DocumentLine, field: str, language: Language) -> str:
    """The text a document prints for one value of a line: the override if the spec gives one, "" if not printed."""
    overrides = getattr(line, "text_override", {})
    if field in overrides:
        return overrides[field]
    value = getattr(line, field, None)
    if value is None:
        return ""
    if field == "quantity":
        return _number(value, language)
    if field in ("unit_price", "line_total"):
        return _number(value, language, 2)
    if field == "unit":
        return UNIT_WORDS[language][value]
    if field == "discount_pct":
        return _number(value, language) + ("%" if language == "en" else " %")
    if field == "delivery_date":
        return _delivery(value, language)
    return value                                 # line_id, item_code, description and price_basis print as they are


# ---------------------------------------------------------------- layout: pages of text rows

REGULAR, BOLD, BODY = "Helvetica", "Helvetica-Bold", 9
MARGIN, ROW_SPACE, GAP = 50, 6, 10               # points
CONTENT_WIDTH = A4[0] - 2 * MARGIN
LINES_PER_PAGE = 25                              # MU04's 32 lines take two pages
SCREEN = (960, 540)                              # the cart screenshot, rendered at 1280 x 720 pixels
PRINTED = ("line_id", "item_code", "description", "quantity", "unit", "unit_price", "price_basis", "discount_pct", "line_total", "delivery_date")


@dataclass
class Text:
    x: float                                     # from the left margin
    text: str
    font: str = REGULAR
    size: float = BODY


@dataclass
class Row:
    """One line of text on a page. Task 5's extract_lines numbers these lines from 1 per page, top to bottom."""
    texts: list[Text]
    ref: str | None = None                       # what the row shows, for the references: "line <id>" or a header field
    gap: float = 0                               # extra space above the row
    rule: bool = False                           # a thin line under the row, below a table's column titles

    def __post_init__(self):
        if not any(t.text for t in self.texts):
            raise ValueError("an empty row would shift the line numbers of the rows below it")


@dataclass(frozen=True)
class Column:
    title: str
    field: str
    x: float
    width: float


def _columns(*specs: tuple[str, str, float]) -> list[Column]:
    columns, x = [], 0.0
    for title, field, width in specs:
        columns.append(Column(title, field, x, width))
        x += width
    return columns


PO_COLUMNS = _columns(("Regel", "line_id", 32), ("Artikel", "item_code", 58), ("Omschrijving", "description", 170), ("Aantal", "quantity", 42),
                      ("Eenheid", "unit", 45), ("Prijs", "unit_price", 55), ("Per", "price_basis", 30), ("Leverdatum", "delivery_date", 63))
TABLE_NL_COLUMNS = _columns(("Pos", "line_id", 25), ("Artikel", "item_code", 92), ("Omschrijving", "description", 145), ("Aantal", "quantity", 38),
                            ("Eenheid", "unit", 45), ("Prijs", "unit_price", 64), ("Per", "price_basis", 26), ("Leverdatum", "delivery_date", 60))
TOTAL_ONLY_COLUMNS = _columns(("Pos", "line_id", 25), ("Artikel", "item_code", 85), ("Omschrijving", "description", 165), ("Aantal", "quantity", 45),
                              ("Eenheid", "unit", 45), ("Totaal", "line_total", 68), ("Leverdatum", "delivery_date", 62))
TABLE_DE_COLUMNS = _columns(("Pos", "line_id", 25), ("Artikel-Nr.", "item_code", 55), ("Bezeichnung", "description", 130), ("Menge", "quantity", 38),
                            ("ME", "unit", 25), ("Preis", "unit_price", 45), ("PE", "price_basis", 25), ("Rabatt", "discount_pct", 38),
                            ("Liefertermin", "delivery_date", 114))
CART_COLUMNS = _columns(("Artikelnummer", "item_code", 120), ("Omschrijving", "description", 300), ("Aantal", "quantity", 80), ("Eenheid", "unit", 80),
                        ("Prijs", "unit_price", 100), ("Per", "price_basis", 60), ("Subtotaal", "subtotal", 120))

CONFIRMATION_WORDS = {
    "nl": {"title": "Orderbevestiging", "po": "Uw inkooporder", "version": "versie",
           "date": "Datum:", "currency": "Valuta:", "incoterm": "Leveringsvoorwaarden:"},
    "de": {"title": "Auftragsbestätigung", "po": "Ihre Bestellung", "version": "Version",
           "date": "Datum:", "currency": "Währung:", "incoterm": "Lieferbedingungen:"},
    "en": {"title": "Order confirmation", "po": "Your PO", "version": "version",
           "date": "Date:", "currency": "Currency:", "incoterm": "Delivery terms:"},
}


def _fit(text: str, width: float, font: str = REGULAR, size: float = BODY) -> str:
    """The text, if it leaves 6 points free in its space: pdfplumber joins characters less than 3 points apart into one word."""
    if stringWidth(text, font, size) > width - 6:
        raise ValueError(f"{text!r} is too wide for its space of {width} points")
    return text


def _table_row(line: PoLine | DocumentLine, columns: list[Column], language: Language) -> Row:
    """One table row; a value the layout has no column for is an error, so that no value goes silently unprinted."""
    shown = {c.field for c in columns} | set(getattr(line, "text_override", {}))
    hidden = [f for f in PRINTED if getattr(line, f, None) is not None and f not in shown]
    if hidden:
        raise ValueError(f"line {line.line_id}: the layout has no column for {', '.join(hidden)}")
    texts = [Text(c.x, _fit(text, c.width)) for c in columns if (text := cell_text(line, c.field, language))]
    return Row(texts, ref=f"line {line.line_id}")


def _paged(header: list[Row], first: list[Row], body: list[Row], repeat: list[Row]) -> list[list[Row]]:
    """The header and `first` on page 1, then LINES_PER_PAGE body rows per page, `repeat` above them on later pages."""
    chunks = [body[i:i + LINES_PER_PAGE] for i in range(0, len(body), LINES_PER_PAGE)] or [[]]
    return [(header + first if n == 0 else repeat) + chunk for n, chunk in enumerate(chunks)]


def _table_pages(header: list[Row], columns: list[Column], lines: list, language: Language) -> list[list[Row]]:
    titles = [Row([Text(c.x, _fit(c.title, c.width, BOLD)) for c in columns], gap=GAP, rule=True)]
    return _paged(header, titles, [_table_row(line, columns, language) for line in lines], titles)


def basis_words(line: DocumentLine) -> str:
    """The price basis in an English sentence: "each" for one unit, "per 100" for a hundred."""
    if "price_basis" in line.text_override:
        return line.text_override["price_basis"]
    if line.price_basis is None:
        return ""
    return "each" if line.price_basis == "1" else f"per {line.price_basis}"


def _sentence(line: DocumentLine) -> str:
    """An English prose line: "Line 1: 12 pcs of KM-1101 Hose clamp 20-32 mm at EUR 0.85 each, delivery 2026-11-16." """
    if line.line_total is not None:
        raise ValueError(f"line {line.line_id}: the prose layout prints no line totals")
    amount = " ".join(t for t in (cell_text(line, "quantity", "en"), cell_text(line, "unit", "en")) if t)
    text = f"Line {line.line_id}: {amount} of {cell_text(line, 'item_code', 'en')} {cell_text(line, 'description', 'en')}"
    if price := cell_text(line, "unit_price", "en"):
        text += f" at EUR {price} {basis_words(line)}".rstrip()
    if discount := cell_text(line, "discount_pct", "en"):
        text += f", less {discount} discount"
    delivery = cell_text(line, "delivery_date", "en")
    return text + (f", delivery {delivery}." if delivery else ".")


def po_pages(spec: MadeUpSpec, version: PoVersion) -> list[list[Row]]:
    """The PO PDF in the fixed layout po_madeup: six header rows, then the lines table."""
    po = spec.po
    header = [Row([Text(0, f"Inkooporder {po.po_number}", BOLD, 14)], ref="po_reference"),
              Row([Text(0, f"Versie {version.version}")], ref="version"),
              Row([Text(0, f"Datum {day_text(version.version_date, 'nl')}")], ref="document_date"),
              Row([Text(0, f"Leverancier: {po.supplier}")], ref="supplier"),
              Row([Text(0, f"Levering: {po.incoterm}")], ref="incoterm"),
              Row([Text(0, f"Valuta: {po.currency}")], ref="currency")]
    return _table_pages(header, PO_COLUMNS, version.lines, "nl")


def confirmation_pages(spec: MadeUpSpec) -> list[list[Row]]:
    """The supplier's confirmation: the header fields on rows of their own, then the lines as a table or as prose."""
    conf, po = spec.confirmation, spec.po
    words = CONFIRMATION_WORDS[conf.language]
    reference = f"{words['po']} {po.po_number}" + (f", {words['version']} {conf.version_quoted}" if conf.version_quoted is not None else "")
    header = [Row([Text(0, po.supplier, BOLD, 14)], ref="supplier"),
              Row([Text(0, words["title"], BOLD, 12)]),
              Row([Text(0, reference)], ref="po_reference"),
              Row([Text(0, f"{words['date']} {day_text(conf.document_date, conf.language)}")], ref="document_date"),
              Row([Text(0, f"{words['currency']} {po.currency}")], ref="currency")]
    if conf.incoterm is not None:
        header.append(Row([Text(0, f"{words['incoterm']} {conf.incoterm}")], ref="incoterm"))
    if conf.layout == "text_en":
        intro = [Row([Text(0, "Thank you for your order. We confirm the following lines:")], gap=GAP)]
        prose = [Row([Text(0, _fit(_sentence(line), CONTENT_WIDTH))], ref=f"line {line.line_id}") for line in conf.lines]
        return _paged(header, intro, prose, [])
    columns = {"table_de": TABLE_DE_COLUMNS, "total_only": TOTAL_ONLY_COLUMNS}.get(conf.layout, TABLE_NL_COLUMNS)
    return _table_pages(header, columns, conf.lines, conf.language)


def row_positions(pages: list[list[Row]]) -> dict[str, tuple[int, int]]:
    """Where each tagged row sits, as (page, line), both counted from 1."""
    return {row.ref: (p, n) for p, rows in enumerate(pages, 1) for n, row in enumerate(rows, 1) if row.ref}


# ---------------------------------------------------------------- drawing

SCAN_DPI, SCAN_ANGLE, SCAN_QUALITY = 150, 0.8, 75


def _draw(canvas: Canvas, pages: list[list[Row]], page_size: tuple[float, float]) -> None:
    """Draw the rows of each page from the top, one baseline per row."""
    width, height = page_size
    for rows in pages:
        y = height - MARGIN
        for row in rows:
            y -= row.gap + max(t.size for t in row.texts) + ROW_SPACE
            if y < MARGIN:
                raise ValueError("the rows do not fit on the page")
            for t in row.texts:
                canvas.setFont(t.font, t.size)
                canvas.drawString(MARGIN + t.x, y, t.text)
            if row.rule:
                canvas.setLineWidth(0.5)
                canvas.line(MARGIN, y - 3, width - MARGIN, y - 3)
        canvas.showPage()


def pdf_bytes(pages: list[list[Row]], title: str, page_size: tuple[float, float] = A4) -> bytes:
    """A PDF with a text layer, the same bytes on every run (reportlab's invariant mode)."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=page_size, invariant=1)
    canvas.setTitle(title)
    _draw(canvas, pages, page_size)
    canvas.save()
    return buffer.getvalue()


def scan_bytes(pages: list[list[Row]], title: str) -> bytes:
    """The pages as a scanned paper: each rendered at 150 dpi in grey, turned by 0.8 degrees on a white ground and put
    back into a PDF as a JPEG image, so that the PDF has no text layer."""
    canvas_buffer = io.BytesIO()
    canvas = Canvas(canvas_buffer, pagesize=A4, invariant=1)
    canvas.setTitle(title)
    document = pdfium.PdfDocument(pdf_bytes(pages, title))
    try:
        for index in range(len(document)):
            image = document[index].render(scale=SCAN_DPI / 72).to_pil().convert("L")
            image = image.rotate(SCAN_ANGLE, resample=Image.Resampling.BICUBIC, fillcolor=255)
            jpeg = io.BytesIO()
            image.save(jpeg, format="JPEG", quality=SCAN_QUALITY)
            canvas.drawImage(ImageReader(io.BytesIO(jpeg.getvalue())), 0, 0, width=A4[0], height=A4[1])
            canvas.showPage()
    finally:
        document.close()
    canvas.save()
    return canvas_buffer.getvalue()


def confirmation_pdf(spec: MadeUpSpec) -> bytes:
    """The confirmation PDF: with a text layer, or as a scan for the scan layout."""
    pages, title = confirmation_pages(spec), f"{spec.case_id} order confirmation"
    return scan_bytes(pages, title) if spec.confirmation.layout == "scan" else pdf_bytes(pages, title)


def cart_table(spec: MadeUpSpec) -> tuple[list[list[str]], str]:
    """The cart's article rows as the shop prints them, and the cart total."""
    rows, total = [], Decimal(0)
    for line in spec.cart.lines:
        subtotal = (Decimal(line.quantity) * Decimal(line.unit_price) / Decimal(line.price_basis)).quantize(CENT, ROUND_HALF_UP)
        total += subtotal
        rows.append([line.item_code, line.description, cell_text(line, "quantity", "nl"), cell_text(line, "unit", "nl"),
                     f"€ {cell_text(line, 'unit_price', 'nl')}", cell_text(line, "price_basis", "nl"), f"€ {_number(subtotal, 'nl', 2)}"])
    return rows, f"€ {_number(total, 'nl', 2)}"


def cart_html(spec: MadeUpSpec) -> str:
    """The cart page of the supplier's webshop as the buyer saves it from the browser."""
    rows, total = cart_table(spec)
    head = "".join(f"<th>{html.escape(c.title)}</th>" for c in CART_COLUMNS)
    body = "\n".join("      <tr>" + "".join(f"<td>{html.escape(text)}</td>" for text in row) + "</tr>" for row in rows)
    return f"""<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<title>Winkelwagen | {spec.po.supplier}</title>
<style>
  body {{ font-family: Helvetica, Arial, sans-serif; margin: 2em; }}
  table.cart {{ border-collapse: collapse; }}
  table.cart th, table.cart td {{ border-bottom: 1px solid #ccc; padding: 0.4em 1em; text-align: left; }}
</style>
</head>
<body>
  <h1>{spec.po.supplier}</h1>
  <h2>Winkelwagen</h2>
  <p>Datum: {day_text(spec.cart.capture_date, 'nl')}</p>
  <p>Uw referentie: {spec.po.po_number}</p>
  <table class="cart">
    <thead>
      <tr>{head}</tr>
    </thead>
    <tbody>
{body}
    </tbody>
    <tfoot>
      <tr><td colspan="{len(CART_COLUMNS) - 1}">Totaal excl. btw</td><td>{html.escape(total)}</td></tr>
    </tfoot>
  </table>
  <p>Prijzen in EUR, exclusief btw.</p>
</body>
</html>
"""


def cart_rows(spec: MadeUpSpec) -> list[Row]:
    """The rows of the cart screenshot from the top: the supplier, the title, the capture date, the reference, the column
    titles, one row per article, the total and the note on prices. The rows that hold a value are tagged as a confirmation's
    rows are ("supplier", "capture_date", "po_reference", "line <id>", "currency"), so that row_positions finds them."""
    table, total = cart_table(spec)
    size = 11
    page = [Row([Text(0, spec.po.supplier, BOLD, 16)], ref="supplier"),
            Row([Text(0, "Winkelwagen", BOLD, 13)], gap=4),
            Row([Text(0, f"Datum: {day_text(spec.cart.capture_date, 'nl')}", size=size)], gap=4, ref="capture_date"),
            Row([Text(0, f"Uw referentie: {spec.po.po_number}", size=size)], ref="po_reference"),
            Row([Text(c.x, c.title, BOLD, size) for c in CART_COLUMNS], gap=GAP, rule=True)]
    page += [Row([Text(c.x, _fit(text, c.width, REGULAR, size), size=size) for c, text in zip(CART_COLUMNS, row) if text], ref=f"line {line.line_id}")
             for line, row in zip(spec.cart.lines, table)]
    page += [Row([Text(CART_COLUMNS[0].x, "Totaal excl. btw", BOLD, size), Text(CART_COLUMNS[-1].x, total, BOLD, size)], gap=4),
             Row([Text(0, "Prijzen in EUR, exclusief btw.", size=size)], gap=GAP, ref="currency")]
    return page


def cart_png(spec: MadeUpSpec) -> bytes:
    """A screenshot of the cart page, 1280 x 720 pixels, with the same content as the saved page."""
    document = pdfium.PdfDocument(pdf_bytes([cart_rows(spec)], f"{spec.case_id} cart", SCREEN))
    try:
        image = document[0].render(scale=1280 / SCREEN[0]).to_pil()
    finally:
        document.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


EXPORT_COLUMNS = ["po_number", "line_id", "item_code", "description", "quantity", "unit", "unit_price", "price_basis", "delivery_date"]


def export_csv(spec: MadeUpSpec) -> str:
    """The PO side of a pre-check: the lines of the PO as the ERP export lists them (no PO PDF exists yet then)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    for line in spec.po.versions[-1].lines:
        writer.writerow([spec.po.po_number, line.line_id, line.item_code, line.description, line.quantity,
                         UNIT_WORDS["nl"][line.unit], line.unit_price, line.price_basis, line.delivery_date])
    return buffer.getvalue()


def case_files(spec: MadeUpSpec) -> list[tuple[str, bytes]]:
    """The files of one case, as (file name, content)."""
    case = spec.case_id
    if spec.confirmation:
        files = [(f"{case}_po_v{v.version}.pdf", pdf_bytes(po_pages(spec, v), f"{case} PO version {v.version}")) for v in spec.po.versions]
        return files + [(f"{case}_conf.pdf", confirmation_pdf(spec))]
    files = [(f"{case}.html", cart_html(spec).encode("utf-8"))]
    if spec.cart.layout == "png":
        files.append((f"{case}.png", cart_png(spec)))
    return files + [(f"{case}_export.csv", export_csv(spec).encode("utf-8"))]


def generate(spec_dir: Path, out_dir: Path) -> list[Path]:
    """Draw every case of spec_dir into out_dir; returns the files written. Every spec is checked first: one whose values
    do not give exactly the differences it declares stops the run before out_dir is made or any file is written."""
    specs = load_specs(spec_dir)
    for spec in specs:
        check_differences(spec, reference_items(spec))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for spec in specs:
        for name, content in case_files(spec):
            (out_dir / name).write_bytes(content)
            written.append(out_dir / name)
    return written


# ---------------------------------------------------------------- references

REFERENCE_COLUMNS = ["case_id", "reference_item_id", "item_type", "version_answered", "line_id", "line_label", "field", "label",
                     "source", "reader", "adjudicated", "note"]                     # the header of evaluation/templates/references.csv
DIFFERENCE_LABELS = ("different", "missing on the confirmation")


@dataclass
class Item:
    """One reference item. `kind` names a difference in the words of Difference.kind; it is not written out."""
    item_type: str
    field: str = ""
    label: str = ""
    line_id: str = ""
    line_label: str = ""
    source: str = ""
    note: str = ""
    kind: str = ""


def answered_version(spec: MadeUpSpec) -> PoVersion:
    """The PO version the document answers: the one it quotes, or the only one there is."""
    quoted = spec.confirmation.version_quoted if spec.confirmation else None
    if quoted is not None:
        return next(v for v in spec.po.versions if v.version == quoted)
    if len(spec.po.versions) == 1:
        return spec.po.versions[0]
    raise ValueError(f"{spec.case_id}: several PO versions and none quoted, which the made-up set does not have")


def _sources(spec: MadeUpSpec) -> dict[str, str]:
    """Where each row of the document is: "<file>, page p, line l" for a confirmation, "<file>, row r" for a cart."""
    if spec.cart:
        return {f"line {line.line_id}": f"{spec.case_id}.html, row {n}" for n, line in enumerate(spec.cart.lines, 1)}
    name = f"{spec.case_id}_conf.pdf"
    return {ref: f"{name}, page {p}, line {n}" for ref, (p, n) in row_positions(confirmation_pages(spec)).items()}


def _span(value: str) -> tuple[date, date]:
    """The days a delivery date covers: a day, Monday to Friday of an ISO week, or a range."""
    if "-W" in value:
        monday = date.fromisocalendar(int(value[:4]), int(value[-2:]), 1)
        return monday, monday + timedelta(days=4)
    first, _, last = value.partition("/")
    return date.fromisoformat(first), date.fromisoformat(last or first)


def compare_field(field: str, po: PoLine, line: DocumentLine, noun: str) -> tuple[str, str, str]:
    """(label, kind, note) of one field of a matched line pair, by the scoring manual's section 3."""
    if field == "unit":
        if line.unit is None:
            return "missing on the confirmation", "missing on the document", f"no unit on the {noun} line"
        if line.unit != po.unit:
            return "different", "different", f"PO {po.unit}, {noun} {line.unit}"
        return "same", "", ""
    if field in ("quantity", "price") and line.unit != po.unit:
        if line.unit is None:
            return "cannot be settled", "", f"the {noun} states no unit, so the value is not known to be in the PO's unit {po.unit}"
        return "cannot be settled", "", f"the {noun} states {line.unit} against {po.unit} on the PO and no document states a conversion"
    if field == "quantity":
        if Decimal(line.quantity) == Decimal(po.quantity):
            return "same", "", ""
        return "different", "different", f"PO {po.quantity}, {noun} {line.quantity}"
    if field == "price":
        if line.unit_price is None:
            total = (Decimal(po.quantity) * Decimal(po.unit_price) / Decimal(po.price_basis)).quantize(CENT, ROUND_HALF_UP)
            return "cannot be settled", "", f"only the line total {line.line_total} is printed (PO {po.quantity} x {po.unit_price} = {total})"
        if line.price_basis is None:
            return "cannot be settled", "", f"the {noun} states no price basis"
        net = Decimal(line.unit_price) * (1 - Decimal(line.discount_pct or 0) / 100)
        on_po_basis = (net * Decimal(po.price_basis) / Decimal(line.price_basis)).quantize(CENT, ROUND_HALF_UP)
        if on_po_basis == Decimal(po.unit_price):
            return "same", "", ""
        note = f"PO {po.unit_price} per {po.price_basis}, {noun} {line.unit_price} per {line.price_basis}"
        if line.discount_pct or line.price_basis != po.price_basis:
            note += (f" less {line.discount_pct}%" if line.discount_pct else "") + f" ({on_po_basis} per {po.price_basis} net)"
        return "different", "different", note
    if line.delivery_date is None:
        return "missing on the confirmation", "missing on the document", f"no delivery date on the {noun} line"
    first, last = _span(line.delivery_date)
    po_day = date.fromisoformat(po.delivery_date)
    if first <= po_day <= last:
        return "same", "", ""
    covers = f" ({first} to {last})" if "-W" in line.delivery_date else ""
    return "different", "later" if first > po_day else "earlier", f"PO {po.delivery_date}, {noun} {line.delivery_date}{covers}"


def _noun(spec: MadeUpSpec) -> str:
    return "cart" if spec.cart else "confirmation"


def _version_item(spec: MadeUpSpec, version: PoVersion, where: dict[str, str]) -> Item:
    quoted = spec.confirmation is not None and spec.confirmation.version_quoted is not None
    return Item("version", source=where.get("po_reference", "po"),
                note=f"the confirmation quotes version {version.version}" if quoted else "the PO has one version")


def _line_items(spec: MadeUpSpec, version: PoVersion, po: PoLine, where: dict[str, str]) -> list[Item]:
    """The line-presence item of one PO line and, if a document line answers it, the field items of the pair."""
    noun = _noun(spec)
    answering = [line for line in spec.document.lines if po.item_code in line.codes]
    if not answering:
        note = (f"{po.item_code} is not in the cart: it could not be placed" if spec.cart
                else f"PO line {po.line_id} ({po.item_code}) is not on the confirmation")
        return [Item("line presence", "line_presence", "missing on the confirmation", line_id=po.line_id, line_label="missing",
                     source="po", note=note, kind="not in cart" if spec.cart else "missing line")]
    if len(answering) > 1:
        raise ValueError(f"{spec.case_id}: PO line {po.line_id} is split, which the made-up set does not have")
    line = answering[0]
    together = [p.line_id for p in version.lines if p.item_code in line.codes]
    merged, po_lines = len(together) > 1, " and ".join(together)
    pair = {"line_id": po.line_id, "line_label": "merged" if merged else "matched", "source": where[f"line {line.line_id}"]}
    if merged:
        presence = f"{noun} line {line.line_id} answers PO lines {po_lines}"
    else:
        presence = f"answered by {noun} line {line.line_id}" if line.line_id != po.line_id else ""
    items = [Item("line presence", "line_presence", "same", note=presence, **pair)]
    for field in ("price", "quantity", "unit") if spec.cart else ("price", "quantity", "unit", "delivery_date"):
        if merged:                   # a value printed once for several PO lines cannot be split between them
            label, kind, note = "cannot be settled", "", f"merged line {line.line_id} prints the value once for PO lines {po_lines}"
        else:
            label, kind, note = compare_field(field, po, line, noun)
        items.append(Item("field", field, label, note=note, kind=kind, **pair))
    return items


def _extra_items(spec: MadeUpSpec, version: PoVersion, where: dict[str, str]) -> list[Item]:
    """A line-presence item for each document line that answers no PO line."""
    po_codes = {po.item_code for po in version.lines}
    return [Item("line presence", "line_presence", "different", line_id=line.line_id, line_label="extra", source=where[f"line {line.line_id}"],
                 note=f"{_noun(spec)} line {line.line_id} ({line.item_code}) answers no PO line", kind="extra line")
            for line in spec.document.lines if not po_codes & set(line.codes)]


def _required_item(spec: MadeUpSpec, where: dict[str, str]) -> Item:
    """The Incoterm, the required information of the scoring manual; a cart page is not checked for it."""
    if spec.cart:
        return Item("required information", "required_info", "not applicable",
                    note="the pre-check compares price, quantity and unit only (design, section 4)")
    incoterm = spec.confirmation.incoterm
    if incoterm is None:
        return Item("required information", "required_info", "missing on the confirmation", source="po",
                    note=f"the PO states {spec.po.incoterm}; the confirmation states no Incoterm", kind="missing on the document")
    if incoterm != spec.po.incoterm:
        return Item("required information", "required_info", "different", source=where["incoterm"],
                    note=f"PO {spec.po.incoterm}, confirmation {incoterm}", kind="different")
    return Item("required information", "required_info", "same", source=where["incoterm"])


def reference_items(spec: MadeUpSpec) -> list[Item]:
    """The reference items of one case: the version, each PO line's presence and fields, extra lines, required information."""
    version, where = answered_version(spec), _sources(spec)
    items = [_version_item(spec, version, where)]
    for po in version.lines:
        items += _line_items(spec, version, po, where)
    return items + _extra_items(spec, version, where) + [_required_item(spec, where)]


def check_differences(spec: MadeUpSpec, items: list[Item]) -> None:
    """The differences a spec declares must be exactly those its values give."""
    found = {(i.line_id, i.field, i.kind) for i in items if i.label in DIFFERENCE_LABELS}
    declared = {(d.line_id, d.field, d.kind) for d in spec.document.differences}
    if found != declared:
        raise ValueError(f"{spec.case_id}: the spec declares {sorted(declared)} but its values give {sorted(found)}")


def reference_rows(spec: MadeUpSpec) -> list[dict[str, str]]:
    """The rows of references.csv for one case, after checking them against the declared differences."""
    items = reference_items(spec)
    check_differences(spec, items)
    version = str(answered_version(spec).version)
    return [{"case_id": spec.case_id, "reference_item_id": f"{spec.case_id}-{n:03d}", "item_type": i.item_type,
             "version_answered": version, "line_id": i.line_id, "line_label": i.line_label, "field": i.field, "label": i.label,
             "source": i.source, "reader": "spec", "adjudicated": "no", "note": i.note} for n, i in enumerate(items, 1)]


def write_references(specs: list[MadeUpSpec], path: Path) -> int:
    """Write references.csv for all cases; returns the number of reference items."""
    rows = [row for spec in specs for row in reference_rows(spec)]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REFERENCE_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    """The command line: python3 -m aid.madeup.generate --spec aid/madeup/spec --out aid/madeup/out [--references FILE]."""
    parser = argparse.ArgumentParser(description="Draw the made-up document set and write its references.")
    parser.add_argument("--spec", type=Path, default=Path("aid/madeup/spec"), help="folder of the case specs")
    parser.add_argument("--out", type=Path, default=Path("aid/madeup/out"), help="folder for the documents")
    parser.add_argument("--references", type=Path, help="the references file (default: references.csv next to the spec folder)")
    args = parser.parse_args(argv)
    specs = load_specs(args.spec)
    files = generate(args.spec, args.out)        # checks every spec before writing anything
    references = args.references or args.spec.parent / "references.csv"
    count = write_references(specs, references)
    print(f"{len(files)} files in {args.out}, {count} reference items in {references}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
