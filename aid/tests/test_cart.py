"""Tests of the pre-check on captured cart pages: the cart reader (aid/readers/cart.py), the capture source (aid/sources/capture.py)
and a cart case through the pipeline (aid/pipeline.py), on the made-up set. No test calls an AI service: the screenshot is read
only through the recorded answers."""
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from aid.evalscripts.score_reading import expected_fields, score_reading
from aid.pipeline import GUESSED_CART, GUESSED_PO, GUESSED_RULE, read_with_parser, run_case
from aid.pricelist import RULE_ID as PRICE_LIST_RULE
from aid.readers.cart import first_heading, is_cart_page, read_cart_html, read_cart_image
from aid.readers.llm_client import ReaderError, RecordedClient
from aid.schema import DocLine, DocValue, Document, SourceRef
from aid.review.i18n import STRINGS, reason_label
from aid.sources.capture import ALREADY_SENT, GUESSES, NOT_SURE, open_capture_case
from aid.tests.conftest import REPO_ROOT, deps
from aid.tests.helpers import EXPORT_COLUMNS, document_from_spec

RECORDED = REPO_ROOT / "aid" / "tests" / "fixtures" / "recorded"     # the recorded answers, CART02.png among them


def test_read_cart_html(madeup_out):
    doc = read_cart_html(madeup_out / "CART02.html")
    assert doc.kind == "cart" and [l.item_code.value for l in doc.lines] == ["KM-2001", "KM-2002"] and doc.lines[1].unit_price.value == "41.00"

def test_cart_case_routes(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out); d.store.from_export(madeup_out / "CART02_export.csv", EXPORT_COLUMNS)   # EXPORT_COLUMNS: the made-up export's column map, in helpers.py
    c = open_capture_case(d.settings.cases_root, madeup_out / "CART02.html", "250202", d.clock())
    r = run_case(c, d)
    assert r.status == "review" and {i.reason for i in r.review_items} == {"could not be placed", "price above threshold"} and not any(e.op.kind == "set_status" for e in r.write_entries)

def test_cart_png_uses_llm(madeup_out):
    doc, _ = read_cart_image(madeup_out / "CART02.png", RecordedClient(RECORDED))
    assert doc.kind == "cart" and len(doc.lines) == 2


# The tests below cover what the rulings of the task add to the three above.

SPEC_DIR = REPO_ROOT / "aid" / "madeup" / "spec"
NOW = datetime(2026, 11, 12, 9, 0)                   # the clock of deps()
HEADER = [name for name, field in Document.model_fields.items() if field.annotation is DocValue]
FIELDS = [name for name, field in DocLine.model_fields.items() if field.annotation is DocValue]
# The text lines of CART01.html as the cart reader numbers them: the text of each block from the top, a table row with its cells
# joined by " | ". The page's title is in its head and is no line.
CART01_LINES = ["S21", "Winkelwagen", "Datum: 03-11-2026", "Uw referentie: 250201",
                "Artikelnummer | Omschrijving | Aantal | Eenheid | Prijs | Per | Subtotaal",
                "KM-2101 | Slangklem 12-20 mm RVS | 25 | st | € 0,95 | 1 | € 23,75",
                "KM-2102 | Kogellager 6204 2RS | 10 | st | € 4,10 | 1 | € 41,00",
                "KM-2103 | Vetpatroon 400 g lithium | 6 | st | € 6,20 | 1 | € 37,20",
                "Totaal excl. btw | € 101,95", "Prijzen in EUR, exclusief btw."]


def values(doc: Document) -> list[DocValue]:
    """Every DocValue of the document: the header fields, then the fields of each line."""
    return [getattr(doc, name) for name in HEADER] + [getattr(line, name) for line in doc.lines for name in FIELDS]


def page(tmp_path, html: str, name: str = "shop.html"):
    path = tmp_path / name
    path.write_text(html, encoding="utf-8")
    return path


# --- the saved page of the made-up set ---

@pytest.mark.parametrize("case", ["CART01", "CART02"])
def test_the_saved_page_reads_as_its_perfect_reading(madeup_out, case):
    """Every value, its text and its source as document_from_spec gives them for the saved page (CART02 read as HTML)."""
    doc = read_cart_html(madeup_out / f"{case}.html")
    assert (doc.source_file, doc.pages, doc.reader) == (f"{case}.html", 1, "cart_html")
    assert doc.model_copy(update={"reader": "spec"}) == document_from_spec(case, "cart", html=True)


def test_cart01_scores_one_and_each_source_names_its_line_of_the_page(madeup_out):
    """CART01's page scores a field accuracy of 1.0 against its spec (a page has no text lines for score_reading, so no source is
    scored there), and each source names the line of the page whose text holds the value's text."""
    doc = read_cart_html(madeup_out / "CART01.html")
    score = score_reading(doc, expected_fields(SPEC_DIR / "CART01.json"), [])
    assert (score.fields_total, score.field_accuracy, score.errors) == (34, 1.0, [])
    cited = [(value.text, value.source) for value in values(doc) if value.source is not None]
    assert len(cited) == 4 + 3 * 6          # supplier, capture date, PO number, currency; six printed values on each of the 3 lines
    for text, source in cited:
        assert source.page == 1 and CART01_LINES[source.line - 1] == source.text and text in source.text, (text, source)
    assert (doc.supplier.source.line, doc.capture_date.source.line, doc.po_reference.source.line, doc.currency.source.line) == (1, 3, 4, 10)
    assert {line.unit_price.source.line for line in doc.lines} == {6, 7, 8}
    assert document_from_spec("CART01", "cart") == document_from_spec("CART01", "cart", html=True)     # CART01's document is its page


# --- the lines, the cart table and its rows ---

SHOP = """<!doctype html>
<html lang="en">
<head><title>Cart - Example shop</title><style>td { color: red }</style>
<script>var header = "Item | Qty | Price | EUR";</script></head>
<body>
  <div class="logo"><h1>Example shop</h1></div>
  <p>Order 2502031 for site <b>Rotterdam</b><br>Date: 2026-11-05</p>
  <table><tr><th>Item</th><th>Qty</th></tr><tr><td>Not a cart</td><td>1</td></tr></table>
  <table>
    <thead><tr><th>Product</th><th> Description </th><th>QTY.</th><th>Unit</th><th>Price per piece</th><th>Price:</th><th>Basis</th><th>Delivery</th></tr></thead>
    <tbody>
      <tr><td>KM-9001</td><td>Hose <i>clamp</i>   20 mm</td><td>12</td><td>pcs</td><td>1.10</td><td>€&nbsp;1,05</td><td>per 100</td><td>week 47</td></tr>
      <tr><td colspan="8">Delivered in three days</td></tr>
      <tr><td>KM-9002</td><td></td><td>on request</td><td>pcs</td><td></td><td>€ 2,50</td><td>1</td><td>16-11-2026</td></tr>
    </tbody>
    <tfoot><tr><td>Total</td><td></td><td></td><td></td><td></td><td>€ 13,10</td><td></td><td></td></tr></tfoot>
  </table>
</body>
</html>
"""
SHOP_LINES = ["Example shop", "Order 2502031 for site Rotterdam", "Date: 2026-11-05", "Item | Qty", "Not a cart | 1",
              "Product | Description | QTY. | Unit | Price per piece | Price: | Basis | Delivery",
              "KM-9001 | Hose clamp 20 mm | 12 | pcs | 1.10 | € 1,05 | per 100 | week 47", "Delivered in three days",
              "KM-9002 | | on request | pcs | | € 2,50 | 1 | 16-11-2026", "Total | | | | | € 13,10 | |"]


def line(n: int) -> SourceRef:
    return SourceRef(page=1, line=n, text=SHOP_LINES[n - 1])


def test_the_header_values_cite_the_lines_of_the_page(tmp_path):
    """The head (title, style, script) gives no line, a <br> ends one, inline elements stay in theirs. The supplier is the first
    heading, the PO number the first line with the pattern, the capture date the "Date" line and the currency the first sign when
    the page names no currency."""
    doc = read_cart_html(page(tmp_path, SHOP))
    assert (doc.kind, doc.source_file, doc.pages) == ("cart", "shop.html", 1)
    assert (doc.supplier, doc.po_reference, doc.capture_date, doc.currency) == (
        DocValue(value="Example shop", text="Example shop", source=line(1)), DocValue(value="2502031", text="2502031", source=line(2)),
        DocValue(value="2026-11-05", text="2026-11-05", source=line(3)), DocValue(value="EUR", text="€", source=line(7)))
    assert doc.document_date == doc.version_hint == doc.incoterm == DocValue()


def test_the_first_table_with_an_item_a_quantity_and_a_price_column_is_the_cart(tmp_path):
    """The first table has no price column. In the cart table the header cells match as whole folded texts ("QTY." is a quantity,
    "Price per piece" no price), a row across the table and the footer are no article lines, and the article lines are numbered
    from 1. A cell that cannot be read keeps its text without a value, and an empty cell is no value."""
    first, second = read_cart_html(page(tmp_path, SHOP)).lines
    assert (first.line_id, second.line_id) == ("1", "2")
    assert {name: (value.value, value.text) for name in FIELDS if (value := getattr(first, name)).text} == {
        "item_code": ("KM-9001", "KM-9001"), "description": ("Hose clamp 20 mm", "Hose clamp 20 mm"), "quantity": ("12", "12"),
        "unit": ("pc", "pcs"), "unit_price": ("1.05", "€ 1,05"), "price_basis": ("100", "per 100"), "delivery_date": ("2026-W47", "week 47")}
    assert all(getattr(first, name).source == line(7) for name in FIELDS if getattr(first, name).text)
    assert first.discount_pct == first.net_price == DocValue() and not any(getattr(first, name).uncertain for name in FIELDS)
    assert second.quantity == DocValue(value=None, text="on request", source=line(9), uncertain=True)
    assert second.description == second.discount_pct == DocValue()
    assert (second.unit_price.value, second.price_basis.value, second.delivery_date.value) == ("2.50", "1", "2026-11-16")


def test_a_cart_table_inside_a_layout_table_keeps_its_rows_as_lines(tmp_path):
    """A row whose cells hold a table is no line: its cells are blocks, and the rows of the inner table are lines of their own. A
    template and a noscript block give no line, so the header names in the template do not count."""
    html = ("<html><body><template><p>Artikel | Aantal | Prijs | EUR</p></template><noscript><p>Zet JavaScript aan</p></noscript>"
            "<table><tr><td><h1>S40</h1></td><td><table><tr><th>Artikel</th><th>Aantal</th><th>Prijs</th></tr>"
            "<tr><td>KM-9201</td><td>4</td><td>€ 3,00</td></tr></table></td></tr></table></body></html>")
    doc = read_cart_html(page(tmp_path, html))
    assert (doc.supplier.source, doc.currency) == (SourceRef(page=1, line=1, text="S40"),
                                                   DocValue(value="EUR", text="€", source=SourceRef(page=1, line=3, text="KM-9201 | 4 | € 3,00")))
    assert [(line.line_id, line.item_code.value, line.quantity.value, line.unit_price.value, line.item_code.source.line) for line in doc.lines] == [
        ("1", "KM-9201", "4", "3.00", 3)]


def test_without_a_heading_a_date_line_or_a_currency_word_the_values_come_from_the_rest_of_the_page(tmp_path):
    """The supplier from the title, the capture date from <meta name="capture-date">, else from the file's modification time, and
    the currency from its sign; a header row of plain cells; the PO number after "Bestelling"."""
    html = ("<html><head><title>S30 webshop</title>{meta}</head><body><table><tr><td>Artikel</td><td>Aantal</td><td>Prijs</td></tr>"
            "<tr><td>KM-9101</td><td>2</td><td>$ 4.50</td></tr></table><p>Bestelling: 2502032</p></body></html>")
    doc = read_cart_html(page(tmp_path, html.format(meta='<meta name="Capture-Date" content="06-11-2026">')))
    row = SourceRef(page=1, line=2, text="KM-9101 | 2 | $ 4.50")
    assert (doc.supplier, doc.capture_date, doc.currency) == (DocValue(value="S30 webshop", text="S30 webshop"),
                                                              DocValue(value="2026-11-06", text="06-11-2026"), DocValue(value="USD", text="$", source=row))
    assert (doc.po_reference.value, doc.po_reference.source.line, [l.unit_price.value for l in doc.lines]) == ("2502032", 3, ["4.50"])
    saved = page(tmp_path, html.format(meta=""), "saved.html")
    stamp = datetime(2026, 11, 7, 12, 0).timestamp()
    os.utime(saved, (stamp, stamp))
    assert read_cart_html(saved).capture_date == DocValue(value="2026-11-07")


def test_a_page_without_a_cart_table_is_refused(tmp_path):
    for body in ("<p>Uw winkelwagen is leeg.</p>",
                 "<table><tr><th>Artikel</th><th>Aantal</th><th>Prijs per stuk</th></tr><tr><td>KM-1</td><td>1</td><td>2,00</td></tr></table>"):
        with pytest.raises(ReaderError, match=r"^no cart table in shop\.html$"):
            read_cart_html(page(tmp_path, f"<html><body>{body}</body></html>"))


# Small pages: a cart table with these rows (by default under Artikel, Aantal, Prijs), after `before`.
ROW = "<tr><td>KM-9001</td><td>1</td><td>€ 2,00</td></tr>"
CART = f"<table><tr><th>Artikel</th><th>Aantal</th><th>Prijs</th></tr>{ROW}</table>"


def cart_page(tmp_path, rows: str, header: str = "<th>Artikel</th><th>Aantal</th><th>Prijs</th>", before: str = "") -> Document:
    return read_cart_html(page(tmp_path, f"<html><body>{before}<table><tr>{header}</tr>{rows}</table></body></html>"))


def test_a_po_number_needs_a_label_of_its_own_and_ends_with_its_digits(tmp_path):
    """"Repo" holds the label PO inside a word, and ten digits are no PO number of 6 to 8: neither line gives a PO number."""
    for text in ("Repo 250201", "Order 1234567890"):
        assert cart_page(tmp_path, ROW, before=f"<p>{text}</p>").po_reference == DocValue(), text


def test_a_number_that_goes_on_is_no_po_number(tmp_path):
    """A number of six to eight digits that goes on is another number, of which the reader reads no part: one followed directly by
    a letter, or by a separator (a hyphen, an en dash, a slash, a full stop, an underscore, a comma, a colon) with a letter or a
    digit right behind it. A separator at the end of the text or before a space ends the number, as the full stop of a sentence
    does, and a label written against its number is still a label."""
    for text in ("Uw referentie: 250301-01", "Uw referentie: 250301A", "Uw referentie: 250301/2", "Uw referentie: 250301.1",
                 "Uw referentie: 2503011a", "Uw referentie: 250301-b", "Uw referentie: 250301/ab", "Uw referentie: IB-2600417",
                 "Uw referentie: 25030", "Uw referentie: 250301001",
                 "Uw referentie: 250301_01", "Uw referentie: 250301,5", "Uw referentie: 250301:2", "Uw referentie: 250301\u20132",
                 "Uw referentie: 250301_a", "Uw referentie: 250301,b", "Uw referentie: 250301:c", "Uw referentie: 250301\u2013d"):
        assert cart_page(tmp_path, ROW, before=f"<p>{text}</p>").po_reference == DocValue(), text
    for text in ("Uw referentie: 250301.", "Uw referentie: 250301. Dank voor uw bestelling", "Uw referentie: 250301- zie hieronder",
                 "Uw referentie: 250301-", "Uw referentie: 250301/", "Uw referentie: 250301, met dank", "Uw referentie: PO250301",
                 "Uw referentie: 250301 a", "Uw referentie: 250301",
                 "Uw referentie: 250301,", "Uw referentie: 250301:", "Uw referentie: 250301: zie hieronder", "Uw referentie: 250301_",
                 "Uw referentie: 250301\u2013", "Uw referentie: 250301 \u2013 2 regels", "Uw referentie: 250301, 5 regels"):
        assert cart_page(tmp_path, ROW, before=f"<p>{text}</p>").po_reference.value == "250301", text
    assert cart_page(tmp_path, ROW, before="<p>Uw referentie: 250301-01</p><p>Inkooporder 250302</p>").po_reference.value == "250302"


def test_form_controls_give_the_text_they_show(tmp_path):
    """An input gives its value and a hidden input none; a select gives the text of its selected option. A cell that holds only a
    control without a value (a select with no option selected, an empty field) gives no value."""
    doc = cart_page(tmp_path, '<tr><td>KM-9301</td><td><input type="hidden" name="line" value="98765"><input type="number" name="qty" value="25">'
                              '</td><td><select name="unit"><option>st</option><option selected>doos</option></select></td><td>€ 2,00</td></tr>'
                              '<tr><td>KM-9302</td><td><select name="qty"><option>1</option><option>2</option></select></td>'
                              '<td><input type="text" name="unit"></td><td>€ 3,00</td></tr>',
                    header="<th>Artikel</th><th>Aantal</th><th>Eenheid</th><th>Prijs</th>")
    first, second = doc.lines
    row = SourceRef(page=1, line=2, text="KM-9301 | 25 | doos | € 2,00")
    assert (first.quantity, first.unit) == (DocValue(value="25", text="25", source=row), DocValue(value="doos", text="doos", source=row))
    assert (second.quantity, second.unit, second.unit_price.source.text) == (DocValue(), DocValue(), "KM-9302 | | | € 3,00")


def test_a_heading_in_a_table_row_of_its_own_is_the_supplier(tmp_path):
    """The heading's text is the value, and the line of its row the source."""
    doc = cart_page(tmp_path, ROW, before="<table><tr><td><h1>S40</h1></td><td>Winkelwagen</td></tr></table>")
    assert doc.supplier == DocValue(value="S40", text="S40", source=SourceRef(page=1, line=1, text="S40 | Winkelwagen"))


def test_the_first_heading_of_a_page_is_given_as_the_supplier_is_read(tmp_path, madeup_out):
    """first_heading gives the text of the first heading of a page saved as HTML, the one read_cart_html takes the supplier from: in
    a block of its own or in a table row, never the title of the page, a heading the browser does not show, or a later heading. A
    page without a heading, with or without a cart table, has none."""
    assert first_heading(madeup_out / "CART02.html") == "S22" == read_cart_html(madeup_out / "CART02.html").supplier.value

    def saved(html: str) -> Path:
        path = tmp_path / "page.html"
        path.write_text(html, encoding="utf-8")
        return path
    assert first_heading(saved("<html><head><title>Shop</title></head><body><p>Welkom</p><h3>Eerste  <b>kop</b></h3><h1>Tweede</h1>"
                               "</body></html>")) == "Eerste kop"
    assert first_heading(saved("<table><tr><td><h2>S40</h2></td><td>Winkelwagen</td></tr></table><h1>Later</h1>")) == "S40"
    assert first_heading(saved("<template><h1>Verborgen</h1></template><noscript><h1>Ook</h1></noscript><h2>Zichtbaar</h2>")) == "Zichtbaar"
    assert first_heading(saved("<html><head><title>Alleen een titel</title></head><body><p>Geen kop</p></body></html>")) == ""
    assert first_heading(saved("")) == ""


def test_a_total_or_shipping_row_without_a_quantity_is_no_cart_line(tmp_path):
    """A row without a cell across columns whose item cell is a word for a total, the tax or the shipping (a colon after it, or
    capitals, count the same) and whose quantity cell is empty is no line of the cart; a row with a quantity is one."""
    doc = cart_page(tmp_path, "<tr><td>KM-9401</td><td>2</td><td>€ 5,00</td></tr><tr><td>Subtotaal:</td><td></td><td>€ 10,00</td></tr>"
                              "<tr><td>Verzendkosten</td><td></td><td>€ 6,95</td></tr><tr><td>KM-9402</td><td>1</td><td>€ 3,00</td></tr>"
                              "<tr><td>TOTAL</td><td> </td><td>€ 19,95</td></tr><tr><td>BTW</td><td>1</td><td>€ 4,19</td></tr>")
    assert [(line.line_id, line.item_code.value) for line in doc.lines] == [("1", "KM-9401"), ("2", "KM-9402"), ("3", "BTW")]


def test_a_capture_date_meta_with_a_time_gives_its_day_and_one_without_a_date_is_passed_over(tmp_path):
    """The date part of a date and time is the capture date. A meta that gives no date leaves the date to the "Datum" line, and
    without one to the file's modification time."""
    stamp = datetime(2026, 11, 7, 12, 0).timestamp()

    def capture(content: str, before: str = "") -> DocValue:
        path = page(tmp_path, f'<html><head><meta name="capture-date" content="{content}"></head><body>{before}{CART}</body></html>')
        os.utime(path, (stamp, stamp))
        return read_cart_html(path).capture_date

    assert capture("2026-11-03T14:05:00+01:00") == DocValue(value="2026-11-03", text="2026-11-03T14:05:00+01:00")
    assert capture("2026-11-03 14:05") == DocValue(value="2026-11-03", text="2026-11-03 14:05")
    assert capture("gisteren", "<p>Datum: 04-11-2026</p>") == DocValue(value="2026-11-04", text="04-11-2026",
                                                                        source=SourceRef(page=1, line=1, text="Datum: 04-11-2026"))
    assert capture("2026-11-03Tnoon") == DocValue(value="2026-11-07")           # what follows the "T" is no time


def test_a_page_in_windows_1252_is_read_in_the_charset_it_declares(tmp_path):
    path = tmp_path / "shop.html"
    path.write_bytes(('<html><head><meta charset="windows-1252"></head><body><table><tr><th>Artikel</th><th>Omschrijving</th>'
                      '<th>Aantal</th><th>Prijs</th></tr><tr><td>KM-9501</td><td>Gerätehalter café</td><td>3</td><td>€ 12,50</td></tr>'
                      '</table></body></html>').encode("cp1252"))
    with pytest.raises(UnicodeDecodeError):
        path.read_text(encoding="utf-8")                                     # the bytes are no UTF-8
    doc = read_cart_html(path)
    assert (doc.lines[0].description.value, doc.lines[0].unit_price.text, doc.lines[0].unit_price.value, doc.currency) == (
        "Gerätehalter café", "€ 12,50", "12.50", DocValue(value="EUR", text="€", source=doc.lines[0].unit_price.source))


def test_a_row_without_text_gives_no_line(tmp_path):
    doc = cart_page(tmp_path, "<tr><td>KM-9701</td><td>1</td><td>€ 2,00</td></tr><tr><td></td><td> </td><td></td></tr>"
                              "<tr><td>KM-9702</td><td>2</td><td>€ 3,00</td></tr>")
    assert [(line.line_id, line.item_code.value, line.item_code.source.line) for line in doc.lines] == [("1", "KM-9701", 2), ("2", "KM-9702", 3)]


def test_a_quantity_with_its_unit_reads_as_the_number_and_keeps_its_text(tmp_path):
    [line] = cart_page(tmp_path, "<tr><td>KM-9801</td><td>25 st</td><td>€ 2,00</td></tr>").lines
    assert line.quantity == DocValue(value="25", text="25 st", source=SourceRef(page=1, line=2, text="KM-9801 | 25 st | € 2,00"))


def test_german_headers_and_decimal_commas(tmp_path):
    [line] = cart_page(tmp_path, "<tr><td>KM-9901</td><td>Schlauchschelle 20-32 mm</td><td>2,5</td><td>m</td><td>1.234,50 €</td>"
                                 "<td>16.11.2026</td></tr>",
                       header="<th>Artikel</th><th>Beschreibung</th><th>Menge</th><th>Einheit</th><th>Preis</th><th>Lieferung</th>").lines
    assert {name: (value.value, value.text) for name in FIELDS if (value := getattr(line, name)).text} == {
        "item_code": ("KM-9901", "KM-9901"), "description": ("Schlauchschelle 20-32 mm", "Schlauchschelle 20-32 mm"),
        "quantity": ("2.5", "2,5"), "unit": ("m", "m"), "unit_price": ("1234.50", "1.234,50 €"), "delivery_date": ("2026-11-16", "16.11.2026")}


# --- the capture source and the pipeline ---

def test_open_capture_case(tmp_path, madeup_out):
    c = open_capture_case(tmp_path / "cases", madeup_out / "CART01.html", " 250201 ", NOW)
    assert (c.case_id, c.kind, c.po_number, [p.name for p in c.input_files]) == ("250201_20261112T090000", "cart", "250201", ["CART01.html"])
    copy, original = c.input_files[0], madeup_out / "CART01.html"
    assert copy.read_bytes() == original.read_bytes() and copy.stat().st_mtime_ns == original.stat().st_mtime_ns   # the capture date may fall back on it
    unknown = open_capture_case(tmp_path / "cases", madeup_out / "CART02.png", "", NOW)
    assert (unknown.case_id, unknown.kind, unknown.po_number) == ("unknown_20261112T090000", "cart", None)
    assert c.po_found_by is None and "po_found_by" not in json.loads((c.root / "case.json").read_text(encoding="utf-8"))
    guessed = open_capture_case(tmp_path / "cases", madeup_out / "CART02.html", "250202", NOW, found_by=NOT_SURE)
    assert guessed.po_found_by == NOT_SURE == json.loads((guessed.root / "case.json").read_text(encoding="utf-8"))["po_found_by"]


def test_a_cart_whose_order_was_a_guess_goes_to_the_buyer_with_a_reason_of_its_own(tmp_path, madeup_out):
    """A capture case opened with how its order was guessed (case.json's po_found_by) gets the pipeline's one REVIEW action for the
    whole case, rule G01, as a confirmation whose order was guessed does, with a reason of its own, by which the review page and the
    digest name the guess of a cart in its own words. Nothing is written, also where the rules would take a price over. The rule's
    id is the pipeline's own: P01 is the price list's rule (aid/pricelist.py), and one id names one rule."""
    d = deps(tmp_path, madeup_out)
    d.store.from_export(madeup_out / "CART01_export.csv", EXPORT_COLUMNS)
    page_ = tmp_path / "CART01.html"
    page_.write_text((madeup_out / "CART01.html").read_text(encoding="utf-8").replace("€ 4,10", "€ 4,15"), encoding="utf-8")
    found = run_case(open_capture_case(d.settings.cases_root, page_, "250201", NOW), d)
    assert [(a.rule_id, a.route) for a in found.actions] == [("R04", "AUTO")] and found.status == "closed"
    for minute, how in enumerate(GUESSES, 1):
        r = run_case(open_capture_case(d.settings.cases_root, page_, "250201", NOW + timedelta(minutes=minute), found_by=how), d)
        assert [(a.rule_id, a.route, a.reason, a.finding_ids) for a in r.actions] == [("G01", "REVIEW", GUESSED_CART, ["F1"])]
        assert (r.status, r.write_entries, [(i.finding_id, i.reason) for i in r.review_items]) == ("review", [], [("F1", GUESSED_CART)])
    assert GUESSED_CART == "PO chosen by the items in the cart" and GUESSED_CART != GUESSED_PO
    assert GUESSES == (NOT_SURE, ALREADY_SENT) == ("cart items, not sure", "cart items of an order already sent")
    assert (GUESSED_RULE, PRICE_LIST_RULE) == ("G01", "P01")
    assert reason_label(GUESSED_CART, STRINGS["nl"]) == ("Kijk na of dit de goede inkooporder is: de assistent koos haar op de artikelen in "
                                                         "de winkelwagen.")
    assert reason_label(GUESSED_CART, STRINGS["en"]) == ("Check that this is the right purchase order: the assistant chose it by the items in "
                                                         "the cart.")


def test_is_cart_page():
    """The test by which the readers of a run send a page to the cart reader: a cart whose file is .html or .htm, in any case."""
    cases = {("CART01.html", "cart"): True, ("shop.HTM", "cart"): True, ("CART01.html", "confirmation"): False, ("CART02.png", "cart"): False,
             ("CART02.html.png", "cart"): False}
    assert {case: is_cart_page(Path(case[0]), case[1]) for case in cases} == cases


def test_the_parser_reads_a_cart_page_saved_as_html_and_leaves_a_screenshot_to_the_llm_reader(tmp_path, madeup_out):
    assert read_with_parser(madeup_out / "CART01.html", "cart") == read_cart_html(madeup_out / "CART01.html")
    shutil.copy2(madeup_out / "CART01.html", tmp_path / "CART01.HTM")
    assert read_with_parser(tmp_path / "CART01.HTM", "cart").reader == "cart_html"
    for name, kind in (("CART02.png", "cart"), ("CART01.html", "confirmation")):
        with pytest.raises(ReaderError, match=rf"^no template for {name}: the parser reads PDF confirmations and HTML cart pages only$"):
            read_with_parser(madeup_out / name, kind)


def test_a_cart_case_is_a_pre_check_that_writes_nothing(tmp_path, madeup_out):
    """CART02's saved page against the PO of its export: the PO line not in the cart (R07) and the price 2.5 percent above the PO's
    (R05) go to the buyer, nothing is written, and the digest line names the pre-check, in Dutch and in English."""
    d = deps(tmp_path, madeup_out)
    d.store.from_export(madeup_out / "CART02_export.csv", EXPORT_COLUMNS)
    r = run_case(open_capture_case(d.settings.cases_root, madeup_out / "CART02.html", "250202", NOW), d)
    assert [(a.rule_id, a.route, a.reason) for a in r.actions] == [("R07", "REVIEW", "could not be placed"), ("R05", "REVIEW", "price above threshold")]
    assert [(i.finding_id, i.field) for i in r.review_items] == [("F1", "line_presence"), ("F2", "price")]
    assert (r.write_entries, r.supplier, r.version.method) == ([], "S22", "reference")
    assert r.digest_line == "voorcontrole PO 250202 (S22): ter beoordeling, geen schrijfacties"
    english = d.model_copy(update={"settings": d.settings.model_copy(update={"language": "en"})})
    r = run_case(open_capture_case(d.settings.cases_root, madeup_out / "CART02.html", "250202", NOW + timedelta(minutes=1)), english)
    assert r.digest_line == "pre-check PO 250202 (S22): for review, no writes"
