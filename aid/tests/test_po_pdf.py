"""Tests of the PO PDF reader (aid/readers/po_pdf.py) on the made-up PO PDFs: with the template of the made-up layout, and with a
layout as the settings hold one (aid/readers/po_layout.py), on the made-up PO PDFs and on the made-up PO in a second layout."""
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from reportlab.pdfbase.pdfmetrics import stringWidth

from aid.madeup.generate import PO_COLUMNS, TRYOUT_PO_LAYOUT, Row, Text, generate_tryout, load_specs, pdf_bytes, po_pages, tryout_po_pages
from aid.madeup.generate import main as generate_main
from aid.readers.pdf_text import extract_lines, extract_rows
from aid.readers.po_layout import PoLayout
from aid.readers.po_pdf import (PoTemplate, fits, layout_first_page, load_template, read_po, read_po_layout, read_po_pdf,
                                _read_layout)    # _read_layout: what a layout reads, without the refusals of read_po_layout
from aid.schema import DocValue, SourceRef
from aid.tests.helpers import ANDERS_LAYOUT, ANDERS_PO, MADEUP_LAYOUT, first_page

SPEC_DIR = Path(__file__).resolve().parents[1] / "madeup" / "spec"
PO_CASES = [(s, v) for s in load_specs(SPEC_DIR) if s.confirmation for v in s.po.versions]      # the 21 PO PDFs of the set
CELLS = ("item_code", "description", "quantity", "unit", "unit_price", "price_basis", "delivery_date")


def test_read_po_madeup(madeup_out):
    doc = read_po_pdf(madeup_out / "MU01_po_v1.pdf")
    assert doc.po_reference.value == "250101" and doc.version_hint.value == "1"
    assert [l.item_code.value for l in doc.lines] == ["KM-1001", "KM-1002", "KM-1003"]
    assert doc.lines[0].unit_price.value == "12.50" and doc.lines[0].price_basis.value == "1" and doc.lines[0].unit.value == "pc"
    assert doc.lines[0].delivery_date.value == "2026-11-16"
    assert doc.lines[0].unit_price.source.page == 1 and "12,50" in doc.lines[0].unit_price.source.text


def test_every_value_has_a_source(madeup_out):
    doc = read_po_pdf(madeup_out / "MU04_po_v1.pdf")
    assert len(doc.lines) == 32 and all(l.unit_price.source is not None for l in doc.lines)
    assert doc.lines[31].unit_price.source.page == 2


# --- what the reader returns ---

def test_the_document_and_its_header(madeup_out):
    doc = read_po_pdf(madeup_out / "MU01_po_v1.pdf")
    assert (doc.kind, doc.reader, doc.source_file, doc.pages) == ("po", "po_pdf:po_madeup", "MU01_po_v1.pdf", 1)
    assert (doc.supplier.value, doc.incoterm.value, doc.currency.value) == ("S01", "DAP Rotterdam", "EUR")
    assert (doc.document_date.text, doc.document_date.value) == ("19-10-2026", "2026-10-19")
    header = (doc.po_reference, doc.version_hint, doc.document_date, doc.supplier, doc.incoterm, doc.currency)
    assert [(v.source.page, v.source.line) for v in header] == [(1, n) for n in range(1, 7)]       # each value names the row it came from
    assert doc.po_reference.source.text == "Inkooporder 250101" and doc.po_reference.text == "250101"
    assert doc.capture_date == DocValue() and not any(v.uncertain for v in header)


def test_a_line_keeps_the_text_of_each_cell_beside_its_value(madeup_out):
    line = read_po_pdf(madeup_out / "MU01_po_v1.pdf").lines[0]
    assert line.line_id == "1"
    assert [(getattr(line, f).text, getattr(line, f).value) for f in CELLS] == [
        ("KM-1001", "KM-1001"), ("Hydraulische slang 10 mm, 2 m", "Hydraulische slang 10 mm, 2 m"), ("10", "10"), ("st", "pc"),
        ("12,50", "12.50"), ("1", "1"), ("16-11-2026", "2026-11-16")]
    assert line.discount_pct == DocValue() and line.net_price == DocValue()                  # the layout has no such columns


def test_the_lines_of_a_second_page(madeup_out):
    """MU04: 25 lines on page 1 and 7 on page 2, where the title row is repeated and is not a line."""
    doc = read_po_pdf(madeup_out / "MU04_po_v1.pdf")
    assert doc.pages == 2 and [l.line_id for l in doc.lines] == [str(n) for n in range(1, 33)]
    assert [l.item_code.value for l in doc.lines] == [f"KM-04{n:02d}" for n in range(1, 33)]
    last_on_first, first_on_second = doc.lines[24], doc.lines[25]
    assert (last_on_first.item_code.source.page, last_on_first.item_code.source.line) == (1, 32)
    assert (first_on_second.item_code.source.page, first_on_second.item_code.source.line) == (2, 2)


def test_every_cell_of_every_line_has_its_text_and_its_source(madeup_out):
    for line in read_po_pdf(madeup_out / "MU04_po_v1.pdf").lines:
        for field in CELLS:
            cell = getattr(line, field)
            assert cell.text and cell.value is not None and not cell.uncertain, (line.line_id, field)
            assert cell.source is not None and cell.text in cell.source.text, (line.line_id, field)


@pytest.mark.parametrize("spec,version", PO_CASES, ids=[f"{s.case_id}_v{v.version}" for s, v in PO_CASES])
def test_the_set_reads_as_its_specs(madeup_out, spec, version):
    """All 21 PO PDFs: the header and every cell read back as the value the spec drew."""
    doc = read_po_pdf(madeup_out / f"{spec.case_id}_po_v{version.version}.pdf")
    assert (doc.po_reference.value, doc.version_hint.value, doc.document_date.value, doc.supplier.value, doc.incoterm.value, doc.currency.value) == (
        spec.po.po_number, str(version.version), version.version_date.isoformat(), spec.po.supplier, spec.po.incoterm, spec.po.currency)
    assert [(l.line_id, *(getattr(l, f).value for f in CELLS)) for l in doc.lines] == [
        (p.line_id, p.item_code, p.description, p.quantity, p.unit, p.unit_price, p.price_basis, p.delivery_date) for p in version.lines]


def test_the_two_versions_of_mu03(madeup_out):
    v1, v2 = (read_po_pdf(madeup_out / f"MU03_po_v{n}.pdf") for n in (1, 2))
    assert (v1.version_hint.value, v2.version_hint.value) == ("1", "2")
    assert (v1.lines[1].quantity.value, v2.lines[1].quantity.value) == ("4", "6")


# --- cells the normalisers cannot read, and cells that are empty ---

def _edited_po(tmp_path: Path, edit, case_id: str = "MU01") -> Path:
    """A PO PDF drawn like the first version of `case_id`'s PO after `edit` has changed the rows of its pages (po_pages of the
    generator)."""
    spec = next(s for s in load_specs(SPEC_DIR) if s.case_id == case_id)
    pages = po_pages(spec, spec.po.versions[0])
    edit(pages)
    path = tmp_path / "po.pdf"
    path.write_bytes(pdf_bytes(pages, "edited PO"))
    return path


def _po_with_cell(tmp_path: Path, title: str, text: str, shift: float = 0) -> Path:
    """MU01's PO with the cell under `title` of its first line replaced by `text` ("" leaves the cell empty) and drawn `shift`
    points to the right of its column."""
    def edit(pages):
        first_line = pages[0][7]                              # six header rows and the title row come first
        cell = next(t for t in first_line.texts if t.x == next(c.x for c in PO_COLUMNS if c.title == title))
        if text:
            cell.text, cell.x = text, cell.x + shift
        else:
            first_line.texts.remove(cell)
    return _edited_po(tmp_path, edit)


@pytest.mark.parametrize("title,text,field,value,uncertain", [
    ("Prijs", "1.234,50", "unit_price", "1234.50", False),             # decimal comma: "." is the thousands mark
    ("Aantal", "1.000", "quantity", "1000", False),                    # ... so a quantity of a thousand is not 1.000
    ("Eenheid", "Stuks", "unit", "pc", False),
    ("Per", "100", "price_basis", "100", False),
    ("Leverdatum", "KW 47", "delivery_date", "2026-W47", False),
    ("Leverdatum", "wk 2", "delivery_date", "2027-W02", False),        # a week without a year: the PO's own date (19 Oct 2026) puts it in 2027
    ("Prijs", "n.v.t.", "unit_price", None, True),                     # not readable: the text stays, the value is None and uncertain
    ("Aantal", "tien", "quantity", None, True),
    ("Per", "n.v.t.", "price_basis", None, True),
    ("Leverdatum", "31-02-2026", "delivery_date", None, True),         # no such day
])
def test_a_cell_is_normalised_or_marked_uncertain(tmp_path, title, text, field, value, uncertain):
    cell = getattr(read_po_pdf(_po_with_cell(tmp_path, title, text)).lines[0], field)
    assert (cell.value, cell.text, cell.uncertain) == (value, text, uncertain)
    assert (cell.source.page, cell.source.line) == (1, 8) and text in cell.source.text


@pytest.mark.parametrize("title,field", [("Prijs", "unit_price"), ("Aantal", "quantity"), ("Eenheid", "unit"), ("Per", "price_basis"),
                                         ("Leverdatum", "delivery_date"), ("Artikel", "item_code"), ("Omschrijving", "description")])
def test_an_empty_cell_stays_missing(tmp_path, title, field):
    """A missing Per cell is missing, not 1, although the rest of the row holds other numbers; the line is still read."""
    doc = read_po_pdf(_po_with_cell(tmp_path, title, ""))
    assert getattr(doc.lines[0], field) == DocValue() and doc.lines[0].line_id == "1" and len(doc.lines) == 3


def test_a_word_goes_to_the_column_that_holds_its_middle(tmp_path):
    """A price drawn flush right under its title starts a few points left of the title and still belongs to the Prijs column."""
    doc = read_po_pdf(_po_with_cell(tmp_path, "Prijs", "12,50", shift=-5))
    assert (doc.lines[0].unit_price.value, doc.lines[0].unit.value, doc.lines[0].unit.text) == ("12.50", "pc", "st")
    doc = read_po_pdf(_po_with_cell(tmp_path, "Eenheid", "st", shift=-2))                 # "st" is 7 points wide: its middle is still right of the title
    assert (doc.lines[0].unit.value, doc.lines[0].quantity.value) == ("pc", "10")


def test_a_row_without_a_line_number_is_not_a_line(tmp_path):
    """A continuation or a footer has no Regel cell: it is skipped, and the rows around it are still read."""
    doc = read_po_pdf(_po_with_cell(tmp_path, "Regel", ""))
    assert [l.line_id for l in doc.lines] == ["2", "3"]


def test_a_header_value_that_cannot_be_read_is_uncertain(tmp_path):
    def edit(pages):
        pages[0][2].texts[0].text = "Datum 31-02-2026"           # no such day
        pages[0][5].texts[0].text = "Valuta: XYZ"                # a currency the aid does not know
    doc = read_po_pdf(_edited_po(tmp_path, edit))
    assert (doc.document_date.value, doc.document_date.text, doc.document_date.uncertain) == (None, "31-02-2026", True)
    assert (doc.currency.value, doc.currency.text, doc.currency.uncertain) == (None, "XYZ", True)
    assert doc.document_date.source.line == 3 and doc.currency.source.line == 6
    assert doc.po_reference.value == "250101" and not doc.po_reference.uncertain and len(doc.lines) == 3


def test_a_header_field_the_pdf_does_not_show_is_missing(tmp_path):
    doc = read_po_pdf(_edited_po(tmp_path, lambda pages: pages[0].pop(1)))          # the "Versie" row
    assert doc.version_hint == DocValue() and doc.po_reference.value == "250101"
    assert [l.item_code.source.line for l in doc.lines] == [7, 8, 9]                 # the rows below moved up by one


def test_a_description_with_header_words_is_neither_a_title_row_nor_a_header(tmp_path):
    doc = read_po_pdf(_po_with_cell(tmp_path, "Omschrijving", "Artikel Versie 9"))
    assert len(doc.lines) == 3 and doc.lines[0].description.value == "Artikel Versie 9"
    assert doc.version_hint.value == "1" and doc.version_hint.source.line == 2          # the first row that matches is the header row


# --- a PDF the template does not fit, and the template itself ---

@pytest.mark.parametrize("name,message", [
    ("MU05_conf.pdf", "no row contains 'Artikel'"),               # a scan: no text layer at all
    ("MU02_conf.pdf", "no row contains 'Artikel'"),               # prose lines, no table
    ("MU04_conf.pdf", "9 words but the template lists 8 columns"),
    ("MU06_conf.pdf", "7 words but the template lists 8 columns"),
])
def test_a_pdf_the_template_does_not_fit_is_refused(madeup_out, name, message):
    with pytest.raises(ValueError, match=message):
        read_po_pdf(madeup_out / name)


def test_an_unknown_template_is_an_error(madeup_out):
    with pytest.raises(FileNotFoundError, match="no_such_layout"):
        read_po_pdf(madeup_out / "MU01_po_v1.pdf", template="no_such_layout")


def test_the_madeup_template_and_what_a_template_may_say():
    template = load_template("po_madeup")
    assert template.table.columns == ["line_id", "item_code", "description", "quantity", "unit", "unit_price", "price_basis", "delivery_date"]
    assert template.table.header_row_contains == "Artikel" and template.decimal_comma is True
    data = template.model_dump()
    for change in ({"table": data["table"] | {"columns": [*data["table"]["columns"], "colour"]}},      # a column that is no DocLine field
                   {"table": data["table"] | {"columns": ["item_code", "unit_price"]}},                  # no line_id
                   {"header": data["header"] | {"colour": "x"}},                                         # a header field that is no Document field
                   {"layout": "x"}):                                                                     # a key the template does not know
        with pytest.raises(ValidationError):
            PoTemplate.model_validate(data | change)


# --- a layout from the settings (po_layout): the made-up layout written as one, and a second made-up layout ---

TITLE = "Regel Artikel Omschrijving Aantal Eenheid Prijs Per Leverdatum"
PAGE = "\n".join(["Inkooporder 250101", "Versie 1", "Datum 19-10-2026", "Leverancier: S01", "Levering: DAP Rotterdam", "Valuta: EUR", TITLE,
                  "1 KM-1001 Hydraulische slang 10 mm, 2 m 10 st 12,50 1 16-11-2026"])         # the first page of a made-up PO, as text
# The order lines of the second made-up PO: the quantity, the unit, the item code, the description, the delivery date and the unit
# price, each as printed and then in normal form. The PDF prints no line numbers and no price basis.
ANDERS_LINES = [
    (("10", "10"), ("st", "pc"), "KM-2301", "Kogelkraan DN25 messing", ("16-11-2026", "2026-11-16"), ("12,50", "12.50")),
    (("4", "4"), ("st", "pc"), "KM-2302", "Manometer 0-250 bar, glycerine", ("16-11-2026", "2026-11-16"), ("38,10", "38.10")),
    (("250", "250"), ("m", "m"), "KM-2303", "Hydraulische slang 10 mm", ("week 48", "2026-W48"), ("3,20", "3.20")),
    (("1.000", "1000"), ("st", "pc"), "KM-2304", "Zeskantbout M10 x 40 verzinkt", ("23-11-2026", "2026-11-23"), ("0,42", "0.42")),
    (("2", "2"), ("st", "pc"), "KM-2305", "Tandwielpomp 8 cc", ("30-11-2026", "2026-11-30"), ("1.234,50", "1234.50")),
    (("25", "25"), ("st", "pc"), "KM-2306", "Slangklem 20-32 mm", ("16-11-2026", "2026-11-16"), ("0,85", "0.85")),
    (("12", "12"), ("st", "pc"), "KM-2307", "Snelkoppeling 1/2 inch", ("16-11-2026", "2026-11-16"), ("6,40", "6.40")),
    (("6", "6"), ("st", "pc"), "KM-2308", "Afdichtingsset cilinder 50 mm", ("23-11-2026", "2026-11-23"), ("18,75", "18.75")),
    (("40", "40"), ("m", "m"), "KM-2309", "Persluchtslang 8 mm blauw", ("23-11-2026 - 27-11-2026", "2026-11-23/2026-11-27"),
     ("1,15", "1.15")),
    (("3", "3"), ("st", "pc"), "KM-2310", "Drukregelventiel 3/8 inch", ("30-11-2026", "2026-11-30"), ("54,00", "54.00")),
    (("100", "100"), ("st", "pc"), "KM-2311", "Sluitring M10 verzinkt", ("23-11-2026", "2026-11-23"), ("0,05", "0.05")),
    (("1", "1"), ("st", "pc"), "KM-2312", "Oliefilter retour 10 micron", ("30-11-2026", "2026-11-30"), ("27,90", "27.90")),
]


def _same_reading(pdf: Path) -> bool:
    """Whether the layout reader with the made-up layout gives the Document of the template reader, but for the reader's name."""
    return read_po_layout(pdf, MADEUP_LAYOUT).model_dump(exclude={"reader"}) == read_po_pdf(pdf).model_dump(exclude={"reader"})


def _edited_anders(tmp_path: Path, edit) -> Path:
    """The second made-up PO after `edit` has changed the rows of its pages (tryout_po_pages of the generator)."""
    pages = tryout_po_pages()
    edit(pages)
    path = tmp_path / "anders.pdf"
    path.write_bytes(pdf_bytes(pages, "edited try-out PO"))
    return path


@pytest.mark.parametrize("spec,version", PO_CASES, ids=[f"{s.case_id}_v{v.version}" for s, v in PO_CASES])
def test_the_made_up_layout_written_as_a_layout_reads_every_made_up_po_as_the_template_does(madeup_out, spec, version):
    """All 21 PO PDFs: the header, the lines, and every value with its printed text, its normal form and its source line are those of
    the template's reading. Only the name of the reader differs."""
    pdf = madeup_out / f"{spec.case_id}_po_v{version.version}.pdf"
    by_template, by_layout = read_po_pdf(pdf), read_po_layout(pdf, MADEUP_LAYOUT)
    assert (by_template.reader, by_layout.reader, by_layout.kind) == ("po_pdf:po_madeup", "po_pdf:layout", "po")
    assert by_layout.model_dump(exclude={"reader"}) == by_template.model_dump(exclude={"reader"})
    assert len(by_layout.lines) == len(version.lines) and all(line.unit_price.source is not None for line in by_layout.lines)
    assert fits(first_page(pdf), MADEUP_LAYOUT)
    assert read_po(pdf, None) == by_template and read_po(pdf, MADEUP_LAYOUT) == by_layout      # read_po: the template without a layout


def test_a_second_layout_is_read():
    """ANDERS_po_v1.pdf, the made-up PO in the second made-up layout: two labels on one line (the PO number with the date, the Incoterm
    with the currency), a PO number with letters, the supplier on a line of its own, no line numbers, numbers flush right, a title of
    two words joined with `same`, a skipped column, a total row and a second page without the title row."""
    doc = read_po_layout(ANDERS_PO, ANDERS_LAYOUT)
    assert (doc.kind, doc.reader, doc.source_file, doc.pages) == ("po", "po_pdf:layout", "ANDERS_po_v1.pdf", 2)
    assert fits(first_page(ANDERS_PO), ANDERS_LAYOUT)
    # the header: the first word after the label for the number and the date, the whole text up to the next label for the Incoterm
    numbers = SourceRef(page=1, line=6, text="Bestelnummer: IB-2600417 Besteldatum: 03-11-2026")
    terms = SourceRef(page=1, line=7, text="Leveringsconditie: FCA Voorbeeldstad Valuta: EUR")
    assert doc.po_reference == DocValue(value="IB-2600417", text="IB-2600417", source=numbers)
    assert doc.document_date == DocValue(value="2026-11-03", text="03-11-2026", source=numbers)
    assert doc.incoterm == DocValue(value="FCA Voorbeeldstad", text="FCA Voorbeeldstad", source=terms)
    assert doc.currency == DocValue(value="EUR", text="EUR", source=terms)
    assert doc.supplier == DocValue(value="Proefleverancier S23", text="Proefleverancier S23",
                                    source=SourceRef(page=1, line=3, text="Proefleverancier S23"))      # the whole of line 3
    assert doc.version_hint == DocValue() and doc.capture_date == DocValue()                             # the PDF prints no version
    # the lines: numbered 1, 2, 3 over both pages, each value with its text and its normal form
    assert [line.line_id for line in doc.lines] == [str(n) for n in range(1, 13)]
    for line, (quantity, unit, code, description, delivery, price) in zip(doc.lines, ANDERS_LINES):
        assert (line.quantity.text, line.quantity.value) == quantity and (line.unit.text, line.unit.value) == unit, line.line_id
        assert (line.item_code.text, line.item_code.value) == (code, code), line.line_id
        assert (line.description.text, line.description.value) == (description, description), line.line_id
        assert (line.delivery_date.text, line.delivery_date.value) == delivery, line.line_id
        assert (line.unit_price.text, line.unit_price.value) == price, line.line_id
        assert line.price_basis == DocValue() and line.discount_pct == DocValue() and line.net_price == DocValue(), line.line_id
        values = (line.quantity, line.unit, line.item_code, line.description, line.delivery_date, line.unit_price)
        assert all(value.source == line.quantity.source and not value.uncertain and value.text in value.source.text for value in values)
    first, ninth, tenth = doc.lines[0], doc.lines[8], doc.lines[9]
    assert first.quantity.source == SourceRef(page=1, line=9, text="10 st KM-2301 Kogelkraan DN25 messing 16-11-2026 12,50 125,00")
    assert (ninth.quantity.source.page, ninth.quantity.source.line) == (1, 18)       # the wrapped description of line 2 took a row
    assert (tenth.quantity.source.page, tenth.quantity.source.line) == (2, 2)        # page 2 has no title row and goes on
    # the title of two words: the last day of line 9's range lies under "levering" and reaches under "Stuksprijs". The column that
    # `same` widened takes it; with "levering" as a column of its own that is not read, the range would read as its first day
    assert ninth.delivery_date.source.text == "40 m KM-2309 Persluchtslang 8 mm blauw 23-11-2026 - 27-11-2026 1,15 46,00"
    unjoined = ANDERS_LAYOUT.model_copy(update={"columns": [("skip" if column == "same" else column) for column in ANDERS_LAYOUT.columns]})
    cut = read_po_layout(ANDERS_PO, unjoined).lines[8].delivery_date
    assert (cut.text, cut.value) == ("23-11-2026", "2026-11-23")
    # what the PDF is drawn like: the rows that are no lines, the column that is not read, and the numbers flush right
    rows = extract_rows(ANDERS_PO)
    texts = [row.line.text for row in rows]
    assert "aansluiting onder, G 1/4" in texts and "Totaal exclusief btw 4.417,85" in texts and "Wordt vervolgd op pagina 2" in texts
    assert sum(1 for row in rows if row.line.text.split() == ANDERS_LAYOUT.title_row) == 1 and rows[7].line.page == 1
    amounts = {"125,00", "2.469,00", "4.417,85"}
    assert not amounts & {value.text for line in doc.lines for _, value in line if isinstance(value, DocValue)}
    title = rows[7].words
    quantities = [row.words[0] for row in rows if len(row.words) > 2 and row.words[2].text.startswith("KM-")]
    prices = [row.words[-2] for row in rows if len(row.words) > 2 and row.words[2].text.startswith("KM-")]
    assert (title[0].text, title[-2].text) == ("Hoeveelheid", "Stuksprijs") and len(quantities) == len(prices) == 12
    assert all(abs(word.x1 - title[0].x1) < 0.5 for word in quantities) and len({round(word.x0) for word in quantities}) > 2
    assert all(abs(word.x1 - title[-2].x1) < 0.5 for word in prices) and len({round(word.x0) for word in prices}) > 2


def test_the_second_made_up_po_is_the_one_the_generator_draws(tmp_path, capsys):
    """The repository's ANDERS_po_v1.pdf holds the rows that generate_tryout draws, and its layout is the one the generator states.
    The command line writes the try-out folder with --tryout and then nothing else: no document of the set and no references."""
    [drawn] = [path for path in generate_tryout(tmp_path / "direct") if path.name == ANDERS_PO.name]
    assert drawn == tmp_path / "direct" / "ANDERS_po_v1.pdf" and extract_rows(drawn) == extract_rows(ANDERS_PO)
    out, references = tmp_path / "out", tmp_path / "references.csv"
    assert generate_main(["--tryout", str(tmp_path / "command"), "--out", str(out), "--references", str(references)]) == 0
    assert extract_rows(tmp_path / "command" / ANDERS_PO.name) == extract_rows(ANDERS_PO)
    assert not out.exists() and not references.exists() and ANDERS_PO.name in capsys.readouterr().out
    assert ANDERS_LAYOUT == PoLayout.model_validate(TRYOUT_PO_LAYOUT) and ANDERS_LAYOUT.prices_per_piece and ANDERS_LAYOUT.table_end is None
    assert ANDERS_LAYOUT.title_row == ["Hoeveelheid", "Eenh.", "Artikelcode", "Benaming", "Gewenste", "levering", "Stuksprijs", "Bedrag"]
    assert ANDERS_LAYOUT.columns == ["quantity", "unit", "item_code", "description", "delivery_date", "same", "unit_price", "skip"]
    manual = (Path(__file__).resolve().parents[1] / "MANUAL.md").read_text(encoding="utf-8")       # section 3.12 shows this layout
    assert json.loads(manual.split('```json\n"po_layout": ', 1)[1].split("\n```", 1)[0]) == TRYOUT_PO_LAYOUT


def test_the_end_text_ends_the_table(tmp_path):
    """Below the total row of the second made-up PO stands a row that reads as an order line: a number under the quantity title and a
    text under the item code title. Without an end text it is line 13. The end text is matched as whole words, case and spacing not
    counting, and only on a row that is no order line: a text of the total row ends the table there, so the row below it is not
    read; a row of page 1 ends page 1's table, and page 2, which shows no title row, is not read; a text of an order line, a part of
    a word and a text that no row holds end nothing."""
    pdf = _edited_anders(tmp_path, lambda pages: pages[1].append(Row([Text(40, "2"), Text(95, "pallets"), Text(152, "per levering")])))
    codes = [f"KM-23{n:02d}" for n in range(1, 13)]
    unended = read_po_layout(pdf, ANDERS_LAYOUT)
    assert [line.item_code.value for line in unended.lines] == [*codes, "pallets"] and unended.lines[12].line_id == "13"
    for end, read in (("Totaal exclusief btw", codes), ("totaal  EXCLUSIEF", codes),          # whole words, case and spacing not counting
                      ("Totaal", codes),                                                       # a word of the total row
                      ("Wordt vervolgd", codes[:9]),                                           # a row of page 1: page 2 is not read
                      ("KM-2305", [*codes, "pallets"]),                                        # an order line never ends the table
                      ("Kogelkraan", [*codes, "pallets"]),
                      ("otaal", [*codes, "pallets"]), ("Totaa", [*codes, "pallets"]),          # a part of a word is no word
                      ("Totaal inclusief", [*codes, "pallets"])):                              # a text no row holds ends nothing
        doc = read_po_layout(pdf, ANDERS_LAYOUT.model_copy(update={"table_end": end}))
        assert [line.item_code.value for line in doc.lines] == read, end
        assert [line.line_id for line in doc.lines] == [str(n) for n in range(1, len(read) + 1)], end


def test_a_page_subtotal_ends_its_page_and_a_page_with_the_title_row_goes_on(tmp_path):
    """MU04's 32 lines stand on two pages, and page 2 repeats the title row. A subtotal row under page 1's lines that holds the end
    text ends page 1's table: a row below it on page 1 that looks like a line is not read. Page 2 shows the title row again, so its
    lines are read. Where page 2 does not repeat the title row, the table that ended on page 1 does not go on."""
    def subtotal(pages):
        pages[0] += [Row([Text(90, "Subtotaal pagina 1"), Text(347, "1.234,00")]),
                     Row([Text(0, "99"), Text(32, "KM-9999"), Text(260, "5"), Text(347, "1,00")])]
    end = MADEUP_LAYOUT.model_copy(update={"table_end": "Subtotaal pagina"})
    pdf = _edited_po(tmp_path, subtotal, "MU04")
    every = [f"KM-04{n:02d}" for n in range(1, 33)]
    assert [line.item_code.value for line in read_po_layout(pdf, MADEUP_LAYOUT).lines] == [*every[:25], "KM-9999", *every[25:]]
    assert [line.item_code.value for line in read_po_layout(pdf, end).lines] == every
    assert [line.item_code.value for line in read_po_layout(pdf, MADEUP_LAYOUT.model_copy(update={"table_end": "Totaal"})).lines] == [
        *every[:25], "KM-9999", *every[25:]]                                                   # "Totaal" is no word of "Subtotaal"

    def no_titles_on_page_2(pages):
        subtotal(pages)
        del pages[1][0]
    pdf = _edited_po(tmp_path, no_titles_on_page_2, "MU04")
    assert [line.item_code.value for line in read_po_layout(pdf, end).lines] == every[:25]
    assert [line.item_code.value for line in read_po_layout(pdf, MADEUP_LAYOUT).lines] == [*every[:25], "KM-9999", *every[25:]]


def test_fits_needs_the_whole_title_row_and_the_label():
    assert fits(PAGE, MADEUP_LAYOUT) and fits(PAGE.upper(), MADEUP_LAYOUT) and fits(PAGE.lower(), MADEUP_LAYOUT)      # case does not count
    for title in ("Pos Artikel Omschrijving Aantal Eenheid Prijs Per Leverdatum",              # one word is another
                  "Regel Artikelnr Omschrijving Aantal Eenheid Prijs Per Leverdatum",          # one word is longer
                  "Regel Artikel Omschrijving Aantal Eenheid Prijs Per",                       # a word is missing
                  "Regel Artikel Omschrijving Aantal Eenheid Prijs Per Leverdatum Bedrag",     # a word too many
                  "Regel Artikel Omschrijving Aantal Eenheid Per Prijs Leverdatum",            # another order
                  "Regel Artikel Omschrijving Aantal\nEenheid Prijs Per Leverdatum"):          # over two lines
        assert not fits(PAGE.replace(TITLE, title), MADEUP_LAYOUT), title
    for line, fit in (("Inkooporder: 250101", True), ("Inkooporder #250101", True), ("INKOOPORDER. 250101", True),
                      ("Uw inkooporder 250101, versie 1", True),                               # a word of its own, anywhere on its line
                      ("Inkooporder nr. 250101", True), ("Inkooporder  Pagina 1 van 1", True),   # a value, though no PO number (rule 12)
                      ("Inkooporder", False), ("Inkooporder:", False),                         # no value after it
                      ("Inkoopordernummer 250101", False), ("Proefinkooporder 250101", False), # part of a longer word
                      ("Bestelling 250101", False),                                            # another word
                      ("Inkooporder Versie 1", False)):                                        # nothing before the next label of the layout
        assert fits(PAGE.replace("Inkooporder 250101", line), MADEUP_LAYOUT) is fit, line
    assert fits(f"S01\n{TITLE}\nZie inkooporder 250101", MADEUP_LAYOUT)                        # on any lines of the page, in any order
    assert not fits("", MADEUP_LAYOUT) and not fits(TITLE, MADEUP_LAYOUT) and not fits("Inkooporder 250101", MADEUP_LAYOUT)


def test_no_made_up_confirmation_fits_a_po_layout(madeup_out):
    """Every made-up confirmation names the PO ("Uw inkooporder 250101", "Ihre Bestellung", "Your PO") and most have a table of their
    own, but none prints the title row of a PO word for word: not one fits the made-up PO layout or the second layout. Each
    made-up PO fits its own layout and not the other."""
    confirmations, pos = sorted(madeup_out.glob("*_conf.pdf")), sorted(madeup_out.glob("*_po_v*.pdf"))
    assert (len(confirmations), len(pos), len(PO_CASES)) == (20, 21, 21)
    for pdf in confirmations:
        assert not fits(first_page(pdf), MADEUP_LAYOUT) and not fits(first_page(pdf), ANDERS_LAYOUT), pdf.name
    for pdf in pos:
        assert fits(first_page(pdf), MADEUP_LAYOUT) and not fits(first_page(pdf), ANDERS_LAYOUT), pdf.name
    assert fits(first_page(ANDERS_PO), ANDERS_LAYOUT) and not fits(first_page(ANDERS_PO), MADEUP_LAYOUT)


def test_a_fitting_pdf_without_a_number_or_a_line_is_refused(tmp_path):
    """No PO number: the PO label has a value after it, so the PDF fits, but on no line is the label followed by a word with a digit
    ("Inkooporder nr. 250101" with the label "Inkooporder", and a later line that names the label with no number). No order line:
    no row below the title row has a whole number as its line number, or there is no row at all. The error names the file and the
    setting, never a text of the PDF."""
    def no_number(pages):
        pages[0][0].texts[0].text = "Inkooporder nr. 250101"
        pages[0].append(Row([Text(0, "Vermeld de inkooporder op elke zending")]))
    pdf = _edited_po(tmp_path, no_number)
    assert fits(first_page(pdf), MADEUP_LAYOUT)
    with pytest.raises(ValueError, match=r"^po\.pdf: no PO number found.*po_layout\.po_label") as refused:
        read_po_layout(pdf, MADEUP_LAYOUT)
    assert not any(text in str(refused.value) for text in ("250101", "nr.", "Vermeld"))

    def no_rows(pages):
        del pages[0][7:]                                          # the title row is the last row of the page

    def no_line_numbers(pages):
        for row in pages[0][7:]:
            row.texts[0].text += "a"                              # "1a", "2a", "3a"
    for edit in (no_rows, no_line_numbers):
        pdf = _edited_po(tmp_path, edit)
        assert fits(first_page(pdf), MADEUP_LAYOUT)
        with pytest.raises(ValueError, match=r"^po\.pdf: no order line found .*po_layout") as refused:
            read_po_layout(pdf, MADEUP_LAYOUT)
        assert "KM-" not in str(refused.value) and "Regel" not in str(refused.value)


def test_with_a_layout_a_pdf_that_does_not_fit_is_refused(madeup_out):
    """With a layout a PDF is read with the layout or not at all: a confirmation, a scan and a PO in another layout are refused, and
    so is a made-up PO under the second layout, although the template of the made-up layout reads it."""
    po = madeup_out / "MU01_po_v1.pdf"
    for pdf, layout in ((madeup_out / "MU01_conf.pdf", MADEUP_LAYOUT), (madeup_out / "MU05_conf.pdf", MADEUP_LAYOUT),
                        (ANDERS_PO, MADEUP_LAYOUT), (po, ANDERS_LAYOUT)):
        for read in (read_po_layout, read_po):
            refusal = rf"^{re.escape(pdf.name)}: the PDF does not fit the purchase order layout .*po_layout"
            with pytest.raises(ValueError, match=refusal) as refused:
                read(pdf, layout)
            assert not any(word in str(refused.value) for word in (*layout.title_row, layout.po_label, "250101", "IB-2600417"))
    assert read_po_pdf(po).po_reference.value == read_po(po, None).po_reference.value == "250101"


def test_a_layout_is_checked():
    """What a layout may say: each refusal names the field of the layout that is at fault."""
    valid = {"po_label": "Inkooporder", "title_row": ["Regel", "Artikel", "Aantal"], "columns": ["line_id", "item_code", "quantity"]}
    layout = PoLayout.model_validate(valid)
    assert (layout.decimal_comma, layout.prices_per_piece) == (True, False)
    assert (layout.date_label, layout.supplier_label, layout.supplier_line, layout.version_label, layout.currency_label,
            layout.incoterm_label, layout.table_end) == (None,) * 7
    for change, field in (
            ({"columns": ["line_id", "item_code"]}, "columns"),                                  # the title row and the columns differ
            ({"title_row": ["Regel", "Artikel"]}, "columns"),
            ({"title_row": ["Artikel"], "columns": ["item_code"]}, "title_row"),                 # fewer than two entries
            ({"title_row": [], "columns": []}, "title_row"),
            ({"title_row": ["Regel", "Artikel", ""]}, "title_row"),                               # an empty entry
            ({"title_row": ["Regel", "Artikel nr", "Aantal"]}, "title_row"),                      # two printed words in one entry
            ({"title_row": ["Regel", "Artikel", " Aantal"]}, "title_row"),
            ({"title_row": ["Artikel"], "columns": ["item_code"]}, "columns"),
            ({"columns": ["same", "item_code", "quantity"]}, "columns"),                         # the first column is 'same'
            ({"columns": ["line_id", "description", "quantity"]}, "columns"),                    # no column is item_code
            ({"columns": ["item_code", "quantity", "quantity"]}, "columns"),                     # a field is named twice
            ({"columns": ["item_code", "description", "unit_price"]}, "columns"),                # neither a line_id nor a quantity column
            ({"po_label": ""}, "po_label"), ({"po_label": "   "}, "po_label"),                   # the PO label is empty
            ({"supplier_label": "Leverancier", "supplier_line": 4}, "supplier_line"),            # a supplier label and a supplier line
            ({"columns": ["line_id", "item_code", "colour"]}, "columns"),                        # a meaning the aid does not have
            ({"supplier_line": 0}, "supplier_line"),                                             # lines are counted from 1
            ({"layout": "x"}, "layout")):                                                        # a key the layout does not know
        with pytest.raises(ValidationError) as refused:
            PoLayout.model_validate(valid | change)
        assert field in {str(error["loc"][0]) for error in refused.value.errors()}, change
    with pytest.raises(ValidationError) as refused:                                             # what the owner is told
        PoLayout.model_validate(valid | {"title_row": ["Regel", "Artikel nr", "Aantal"]})
    [error] = refused.value.errors(include_input=False)
    assert error["loc"] == ("title_row",) and "entry of its own" in error["msg"] and "'same'" in error["msg"]
    assert "Artikel" not in error["msg"] and "nr" not in error["msg"].split()
    for change in ({"columns": ["line_id", "item_code", "skip"]},                               # a line number and no quantity
                   {"columns": ["item_code", "same", "quantity"]},                               # a quantity and no line number
                   {"title_row": ["A", "B", "C", "D", "E", "F"], "columns": ["skip", "item_code", "same", "same", "skip", "quantity"]},
                   {"supplier_line": 4}, {"supplier_label": "Leverancier"}):                     # `skip` and `same` may stand twice
        PoLayout.model_validate(valid | change)
    blank = PoLayout.model_validate(valid | {"date_label": "  ", "supplier_label": "", "supplier_line": 4, "table_end": ""})
    assert (blank.date_label, blank.supplier_label, blank.supplier_line, blank.table_end) == (None, None, 4, None)   # an empty text: none


# --- the rules of the layout reader on variants of the made-up POs ---

def test_a_header_value_is_the_text_after_its_label(tmp_path):
    """Several labels on one row: the number, the version and the date are the first word after their label, whatever the case of the
    label and with the colon, hash or full stop after it left out; the supplier and the Incoterm are the whole text up to the next
    label of the layout."""
    def edit(pages):
        pages[0][0].texts[0].text = "INKOOPORDER #IB-2600417 versie. 3 definitief datum: 20-10-2026 08:15"
        pages[0][3].texts[0].text = "Leverancier: Proefleverancier S23 Levering: FCA Voorbeeldstad Valuta: EUR"
        del pages[0][4:6], pages[0][1:3]                              # the rows of the Incoterm and the currency, the version and the date
    doc = read_po_layout(_edited_po(tmp_path, edit), MADEUP_LAYOUT)
    assert (doc.po_reference.value, doc.version_hint.value, doc.document_date.value) == ("IB-2600417", "3", "2026-10-20")
    assert (doc.po_reference.text, doc.version_hint.text, doc.document_date.text) == ("IB-2600417", "3", "20-10-2026")
    assert doc.po_reference.source == doc.version_hint.source == doc.document_date.source and doc.po_reference.source.line == 1
    assert (doc.supplier.value, doc.incoterm.value, doc.currency.value) == ("Proefleverancier S23", "FCA Voorbeeldstad", "EUR")
    assert doc.supplier.source.line == doc.incoterm.source.line == doc.currency.source.line == 2 and len(doc.lines) == 3


def test_the_po_number_version_and_date_come_from_the_first_line_where_they_read(tmp_path):
    """The PO number is the first word after the PO label when that word holds a digit, the version when it is a whole number and the
    date when it reads as a date. A line where the word after the label is none of these gives nothing, and the next line that holds
    the label is tried: a title "Inkooporder  Pagina 1 van 1" above the numbered line, and "Opmaak versie 2b, datum", are passed by.
    The supplier, the currency and the Incoterm are the whole text after the first line that holds their label. A description with
    the words of a label changes nothing, because the header rows come first."""
    def edit(pages):
        pages[0][0:0] = [Row([Text(0, "Inkooporder"), Text(400, "Pagina 1 van 1")]), Row([Text(0, "Opmaak versie 2b, datum")])]
    doc = read_po_layout(_edited_po(tmp_path, edit), MADEUP_LAYOUT)
    assert (doc.po_reference.value, doc.po_reference.text, doc.po_reference.source.line) == ("250101", "250101", 3)
    assert (doc.version_hint.value, doc.version_hint.source.line) == ("1", 4)                   # "2b," is no whole number
    assert (doc.document_date.value, doc.document_date.text, doc.document_date.source.line) == ("2026-10-19", "19-10-2026", 5)
    assert (doc.supplier.value, doc.supplier.source.line, len(doc.lines)) == ("S01", 6, 3)
    doc = read_po_layout(_po_with_cell(tmp_path, "Omschrijving", "Artikel Versie 9"), MADEUP_LAYOUT)
    assert (doc.version_hint.value, doc.version_hint.source.line, doc.lines[0].description.value) == ("1", 2, "Artikel Versie 9")


def test_the_supplier_on_a_line_of_its_own(madeup_out):
    """supplier_line gives the whole text of that line of page 1, counted from 1 as the text layer counts; a line the page lacks gives
    no supplier."""
    data = MADEUP_LAYOUT.model_dump() | {"supplier_label": None}
    pdf = madeup_out / "MU04_po_v1.pdf"                                                          # two pages: line 5 of page 1 counts
    doc = read_po_layout(pdf, PoLayout.model_validate(data | {"supplier_line": 5}))
    assert doc.supplier == DocValue(value="Levering: CPT Rotterdam", text="Levering: CPT Rotterdam",
                                    source=SourceRef(page=1, line=5, text="Levering: CPT Rotterdam"))
    assert read_po_layout(pdf, PoLayout.model_validate(data | {"supplier_line": 99})).supplier == DocValue()
    assert read_po_layout(pdf, PoLayout.model_validate(data)).supplier == DocValue()             # neither a label nor a line


@pytest.mark.parametrize("title,text", [("Prijs", "1.234,50"), ("Aantal", "1.000"), ("Eenheid", "Stuks"), ("Per", "100"),
                                        ("Leverdatum", "KW 47"), ("Leverdatum", "wk 2"), ("Prijs", "n.v.t."), ("Aantal", "tien"),
                                        ("Per", "n.v.t."), ("Leverdatum", "31-02-2026"), ("Prijs", ""), ("Per", ""), ("Artikel", ""),
                                        ("Omschrijving", ""), ("Regel", ""), ("Omschrijving", "Artikel Versie 9")])
def test_a_cell_reads_with_the_layout_as_with_the_template(tmp_path, title, text):
    """A cell the normaliser reads, one it cannot read (the text stays, no value, uncertain) and an empty cell (missing) give the
    values of the template's reading, each with its text and its source line."""
    assert _same_reading(_po_with_cell(tmp_path, title, text))


def test_a_header_reads_with_the_layout_as_with_the_template(tmp_path):
    def unreadable(pages):
        pages[0][2].texts[0].text = "Datum 31-02-2026"           # no such day
        pages[0][5].texts[0].text = "Valuta: XYZ"                # a currency the aid does not know
    pdf = _edited_po(tmp_path, unreadable)
    doc, by_template = read_po_layout(pdf, MADEUP_LAYOUT), read_po_pdf(pdf)
    not_the_date = {"reader": True, "document_date": True}
    assert doc.model_dump(exclude=not_the_date) == by_template.model_dump(exclude=not_the_date)
    printed = by_template.document_date
    assert (printed.value, printed.text, printed.uncertain) == (None, "31-02-2026", True)
    assert doc.document_date == DocValue()                     # the date after its label must read as a date, and no other line holds one
    assert (doc.currency.value, doc.currency.text, doc.currency.uncertain) == (None, "XYZ", True)
    assert _same_reading(_edited_po(tmp_path, lambda pages: pages[0].pop(1)))                    # no "Versie" row: no version


def test_numbers_are_read_with_the_decimal_convention_of_the_layout(tmp_path):
    point = MADEUP_LAYOUT.model_copy(update={"decimal_comma": False})
    for text, with_comma, with_point in (("1.000", "1000", "1.000"), ("1,000", "1.000", "1000"), ("1,234.50", "1234.50", "1234.50")):
        pdf = _po_with_cell(tmp_path, "Aantal", text)
        assert read_po_layout(pdf, MADEUP_LAYOUT).lines[0].quantity.value == with_comma, text
        assert read_po_layout(pdf, point).lines[0].quantity.value == with_point, text


def test_a_word_belongs_to_the_title_it_overlaps_most(tmp_path):
    """A long price drawn flush right under the short title "Prijs" begins left of the title and its middle lies left of it as well: by
    the middle alone it falls in the unit column, as the template reader shows. The layout reader gives it to the title it lies under.
    A word under no title, as the later words of a description, goes by its middle, as before."""
    pdf = _po_with_cell(tmp_path, "Prijs", "1.234,5678", shift=-27.5)
    words = {word.text: word for row in extract_rows(pdf)[6:8] for word in row.words}
    price, title, unit_title = words["1.234,5678"], words["Prijs"], words["Eenheid"]
    assert abs(price.x1 - title.x1) < 0.5 and (price.x0 + price.x1) / 2 < title.x0            # flush right; the middle is left of the title
    assert min(price.x1, unit_title.x1) - price.x0 > 0                                         # and it lies under a part of "Eenheid" too
    by_template, by_layout = read_po_pdf(pdf).lines[0], read_po_layout(pdf, MADEUP_LAYOUT).lines[0]
    assert (by_template.unit_price, by_template.unit.text) == (DocValue(), "st 1.234,5678")
    assert (by_layout.unit_price.value, by_layout.unit.text, by_layout.unit.value) == ("1234.5678", "st", "pc")
    assert by_layout.description.value == "Hydraulische slang 10 mm, 2 m"                      # six words, one of them under the title


def test_a_row_is_a_line_when_its_line_number_is_a_whole_number(tmp_path):
    """With a line number column a row is a line only when that cell is a whole number: a total, a remark and a number with a letter
    are no lines, where the template takes every row with a filled first cell."""
    def edit(pages):
        pages[0][8].texts[0].text = "2a"
        pages[0] += [Row([Text(0, "Totaal"), Text(347, "175,00")]), Row([Text(0, "10 dagen netto")])]
    pdf = _edited_po(tmp_path, edit)
    assert [line.line_id for line in read_po_layout(pdf, MADEUP_LAYOUT).lines] == ["1", "3"]
    assert [line.line_id for line in read_po_pdf(pdf).lines] == ["1", "2a", "3", "Totaal", "10 dagen"]


def test_a_page_before_the_first_title_row_has_no_lines(tmp_path):
    """A cover page in front of the PO: the PDF does not fit, because its first page has no title row, and is refused. What the layout
    reads of it all the same (the reading without the refusals) has the lines of the page with the title row only, although a row of
    the cover page looks like a line."""
    def edit(pages):
        pages.insert(0, [Row([Text(0, "Inkooporder 250101")]), Row([Text(0, "9"), Text(32, "KM-9999"), Text(260, "5")])])
    pdf = _edited_po(tmp_path, edit)
    assert not fits(first_page(pdf), MADEUP_LAYOUT)
    with pytest.raises(ValueError, match="does not fit the purchase order layout"):
        read_po_layout(pdf, MADEUP_LAYOUT)
    doc = _read_layout(pdf, extract_rows(pdf), MADEUP_LAYOUT)
    assert [(line.line_id, line.item_code.value, line.item_code.source.page) for line in doc.lines] == [
        ("1", "KM-1001", 2), ("2", "KM-1002", 2), ("3", "KM-1003", 2)]
    assert (doc.po_reference.value, doc.po_reference.source.page, doc.pages) == ("250101", 1, 2)


def test_a_skipped_column_is_not_read_and_same_widens_the_column_on_its_left(madeup_out):
    """On the made-up PO: with Per as a column that is not read no line has a price basis, and with Omschrijving joined to Artikel the
    item code cell takes the description's words while the columns to the right stay where they are."""
    pdf = madeup_out / "MU01_po_v1.pdf"
    columns = MADEUP_LAYOUT.columns
    skipped = MADEUP_LAYOUT.model_copy(update={"columns": [("skip" if field == "price_basis" else field) for field in columns]})
    doc = read_po_layout(pdf, skipped)
    assert [line.price_basis for line in doc.lines] == [DocValue()] * 3 and [line.unit_price.value for line in doc.lines] == ["12.50"] * 3
    joined = MADEUP_LAYOUT.model_copy(update={"columns": [("same" if field == "description" else field) for field in columns]})
    line = read_po_layout(pdf, joined).lines[0]
    assert (line.item_code.value, line.description, line.quantity.value) == ("KM-1001 Hydraulische slang 10 mm, 2 m", DocValue(), "10")


# --- the review round: close cells, end texts, footers, labels, two POs, numbers in two words, later pages ---

SMALL = PoLayout(po_label="Bestelnummer", title_row=["Regel", "Artikel", "Aantal"], columns=["line_id", "item_code", "quantity"])
SMALL_LINES = [Row([Text(0, "1"), Text(40, "KM-1001"), Text(200, "10")]), Row([Text(0, "2"), Text(40, "KM-1002"), Text(200, "4")])]


def _small_po(tmp_path: Path, header: list[str], pages: list[list[Row]] | None = None, name: str = "small.pdf") -> Path:
    """A small made-up PO: one row for each text of `header`, the title row Regel Artikel Aantal and two lines, or the pages given."""
    rows = [Row([Text(0, text)]) for text in header] + [Row([Text(0, "Regel"), Text(40, "Artikel"), Text(200, "Aantal")], gap=10),
                                                         *SMALL_LINES]
    path = tmp_path / name
    path.write_bytes(pdf_bytes(pages or [rows], name))
    return path


def _right(x: float, text: str, bold: bool = False) -> Text:
    """A text that ends at x, as a report prints a number flush right."""
    font = "Helvetica-Bold" if bold else "Helvetica"
    return Text(x - stringWidth(text, font, 9), text, font, 9)


def test_cells_printed_close_together_are_read_as_two_values(tmp_path):
    """MU01's first line with its price 12,50 printed flush right, ending 2 points before its basis 100, which stands under its own
    title. The text layer joins the two into one word, which the template reads as the price 12.50100 with no basis. The layout reader
    cuts the word where the two cells meet, so the price and the basis are two values, each with its own text; the description,
    printed with ordinary letter spacing, reads as before."""
    def edit(pages):
        line = pages[0][7]
        price, per = (next(t for t in line.texts if t.x == next(c.x for c in PO_COLUMNS if c.title == title)) for title in ("Prijs", "Per"))
        per.text, price.x = "100", per.x - 2 - stringWidth(price.text, price.font, price.size)
    pdf = _edited_po(tmp_path, edit)
    by_template = read_po_pdf(pdf).lines[0]
    assert (by_template.unit_price.text, by_template.unit_price.value, by_template.price_basis) == ("12,50100", "12.50100", DocValue())
    line = read_po_layout(pdf, MADEUP_LAYOUT).lines[0]
    assert (line.unit_price.text, line.unit_price.value, line.price_basis.text, line.price_basis.value) == ("12,50", "12.50", "100", "100")
    assert line.unit_price.source.text == "1 KM-1001 Hydraulische slang 10 mm, 2 m 10 st 12,50 100 16-11-2026"
    assert (line.description.text, line.description.value) == (by_template.description.text, "Hydraulische slang 10 mm, 2 m")


def test_title_words_printed_close_together_fit_as_the_layout_reader_cuts_them(tmp_path):
    """The titles Prijs and Per printed 2 points apart are one word in the text layer as a template reads it, so that first page holds
    no line of the layout's title words. layout_first_page gives the page as the layout reader cuts it: the PDF fits and is read."""
    def edit(pages):
        titles = pages[0][6]
        prijs, per = (next(t for t in titles.texts if t.text == word) for word in ("Prijs", "Per"))
        per.x = prijs.x + stringWidth(prijs.text, prijs.font, prijs.size) + 2
    pdf = _edited_po(tmp_path, edit)
    as_a_template_reads = "\n".join(line.text for line in extract_lines(pdf) if line.page == 1)
    assert "PrijsPer" in as_a_template_reads and not fits(as_a_template_reads, MADEUP_LAYOUT)
    assert fits(layout_first_page(pdf), MADEUP_LAYOUT) and fits(first_page(pdf), MADEUP_LAYOUT)
    assert [(line.line_id, line.unit_price.value, line.price_basis.value) for line in read_po_layout(pdf, MADEUP_LAYOUT).lines] == [
        ("1", "12.50", "1"), ("2", "12.50", "1"), ("3", "12.50", "1")]


def test_a_footer_that_begins_with_a_postal_code_is_no_line(tmp_path):
    """With a line number column a row is an order line when its line number cell is a whole number and its quantity cell or its price
    cell reads as a number; either is enough. A footer at the left margin that begins with a postal code has a whole number under the
    line number title and neither of the two: it is no line, where the template takes it for one. Line 3 printed without its quantity
    is still a line, because its price reads."""
    def edit(pages):
        third = pages[0][9]
        third.texts = [t for t in third.texts if t.x != next(c.x for c in PO_COLUMNS if c.title == "Aantal")]
        pages[0].append(Row([Text(0, "1234 AB Voorbeeldstad  IBAN NL00BANK0123456789")], gap=20))
    pdf = _edited_po(tmp_path, edit)
    lines = read_po_layout(pdf, MADEUP_LAYOUT).lines
    assert [line.line_id for line in lines] == ["1", "2", "3"] and (lines[2].quantity, lines[2].unit_price.value) == (DocValue(), "12.50")
    assert [line.line_id for line in read_po_pdf(pdf).lines] == ["1", "2", "3", "1234 AB"]          # the template takes it for a line


def test_a_label_of_several_words_and_a_label_inside_a_longer_one(tmp_path):
    """(a) A label of several words is matched as a phrase of whole words: with "Inkooporder nr." the number after "nr." is read. With
    the label "Inkooporder" the word after it is "nr.", which holds no digit, so that line gives no PO number and the PDF is refused.
    (b) A match of a label inside a longer label of the layout at the same place is passed by: with "Order" and "Order date", the line
    "Order date: 03-11-2026" printed above "Order: 4500123" gives the date, and the PO number comes from the line below. (c) An address
    "PO Box 61" above "PO: 4500123" gives no PO number, and a dash after the label is no PO number either."""
    pdf = _small_po(tmp_path, ["Inkooporder  nr. 250101", "Datum: 03-11-2026"])
    phrase = SMALL.model_copy(update={"po_label": "Inkooporder nr.", "date_label": "Datum"})
    assert read_po_layout(pdf, phrase).po_reference.value == "250101"
    with pytest.raises(ValueError, match="no PO number found.*po_layout.po_label"):
        read_po_layout(pdf, SMALL.model_copy(update={"po_label": "Inkooporder"}))
    pdf = _small_po(tmp_path, ["Voorbeeldbedrijf B.V.", "Order date: 03-11-2026", "Order: 4500123"])
    order = SMALL.model_copy(update={"po_label": "Order", "date_label": "Order date"})
    assert fits(layout_first_page(pdf), order)
    doc = read_po_layout(pdf, order)
    assert (doc.po_reference.value, doc.po_reference.source.line, doc.document_date.value, doc.document_date.source.line) == (
        "4500123", 3, "2026-11-03", 2)
    pdf = _small_po(tmp_path, ["PO Box 61", "PO: 4500123"])
    assert read_po_layout(pdf, SMALL.model_copy(update={"po_label": "PO"})).po_reference.value == "4500123"
    pdf = _small_po(tmp_path, ["Bestelnummer - 4500123"])
    assert fits(layout_first_page(pdf), SMALL)                                                        # a value after the label
    with pytest.raises(ValueError, match="no PO number found"):
        read_po_layout(pdf, SMALL)                                                                  # but "-" holds no digit


def test_a_pdf_that_holds_two_pos_is_refused(tmp_path):
    """A batch print: page 2 holds another PO, with its own number after the PO label, its own title row and its own line. The PDF is
    refused by the setting and not by the numbers, and nothing of it is read as one PO. A page header that repeats the same PO number
    on every page is fine."""
    def second_po(number: str):
        def edit(pages):
            pages.append([Row([Text(0, f"Inkooporder {number}")]), Row([Text(c.x, c.title, "Helvetica-Bold") for c in PO_COLUMNS], gap=10),
                          Row([Text(0, "1"), Text(32, "KM-9901"), Text(260, "5"), Text(302, "st"), Text(347, "1,00"), Text(402, "1")])])
        return edit
    pdf = _edited_po(tmp_path, second_po("250199"))
    with pytest.raises(ValueError, match=r"^po\.pdf: .*po_layout\.po_label") as refused:
        read_po_layout(pdf, MADEUP_LAYOUT)
    assert "250101" not in str(refused.value) and "250199" not in str(refused.value)
    doc = read_po_layout(_edited_po(tmp_path, second_po("250101 (vervolg)")), MADEUP_LAYOUT)
    assert (doc.po_reference.value, doc.pages, [line.item_code.value for line in doc.lines]) == (
        "250101", 2, ["KM-1001", "KM-1002", "KM-1003", "KM-9901"])


def test_a_number_printed_in_two_words_is_read_with_its_column(tmp_path):
    """Prices and amounts flush right under short titles: "1 234,50" is two words in the text layer, and so is "€ 1.234,50". The first
    word lies under no title; it goes to the column of the next word, which lies under a title, because the two read as one number or
    as a currency sign and a number. So the price is 1234.50 and the unit keeps its own text, also for the amount of a column that is
    not read."""
    layout = PoLayout(po_label="Bestelnummer", title_row=["Artikel", "Aantal", "Eenh.", "Prijs", "Bedrag"],
                      columns=["item_code", "quantity", "unit", "unit_price", "skip"])
    def line(code, quantity, price, amount):
        return Row([Text(0, code), _right(110, quantity), Text(120, "st"), _right(250, price), _right(330, amount)])
    rows = [Row([Text(0, "Bestelnummer: 4500123")]),
            Row([Text(0, "Artikel", "Helvetica-Bold"), _right(110, "Aantal", True), Text(120, "Eenh.", "Helvetica-Bold"),
                 _right(250, "Prijs", True), _right(330, "Bedrag", True)], gap=10),
            line("KM-1001", "2", "1 234,50", "2 469,00"), line("KM-1002", "1", "€ 1.234,50", "€ 1.234,50"),
            line("KM-1003", "3", "12,50", "37,50")]
    pdf = tmp_path / "two_words.pdf"
    pdf.write_bytes(pdf_bytes([rows], "two words"))
    words = [w.text for w in extract_rows(pdf)[2].words]
    assert words == ["KM-1001", "2", "st", "1", "234,50", "2", "469,00"]
    lines = read_po_layout(pdf, layout).lines
    assert [(l.unit.text, l.unit_price.text, l.unit_price.value) for l in lines] == [
        ("st", "1 234,50", "1234.50"), ("st", "€ 1.234,50", "1234.50"), ("st", "12,50", "12.50")]
    assert [l.quantity.value for l in lines] == ["2", "1", "3"]


def test_a_number_under_the_quantity_title_without_an_item_code_is_no_line(tmp_path):
    """In a layout without line numbers a row is an order line when its item code cell is filled and its quantity reads as a number:
    a total row of the second made-up PO that prints the number of pieces under the quantity title and nothing under the item code
    title is no line."""
    pdf = _edited_anders(tmp_path, lambda pages: pages[1].insert(-2, Row([Text(40, "14"), Text(152, "Totaal aantal stuks")])))
    assert "14 Totaal aantal stuks" in [row.line.text for row in extract_rows(pdf)]
    assert [line.item_code.value for line in read_po_layout(pdf, ANDERS_LAYOUT).lines] == [f"KM-23{n:02d}" for n in range(1, 13)]


def test_a_later_page_with_its_own_title_row_is_cut_with_its_own_columns(tmp_path):
    """Page 2 repeats the title row at other places than page 1, below the page number and a row that repeats page 1's line as a
    reminder. Page 2 is cut with its own columns, and the rows above its title row are no lines."""
    page1 = [Row([Text(0, "Bestelnummer: 4500123")]), Row([Text(0, "Regel"), Text(40, "Artikel"), Text(200, "Aantal")], gap=10),
             Row([Text(0, "1"), Text(40, "KM-1001"), Text(200, "10")])]
    page2 = [Row([Text(0, "2")]), Row([Text(0, "1"), Text(220, "KM-1001"), Text(400, "10")]),
             Row([Text(0, "Regel"), Text(220, "Artikel"), Text(400, "Aantal")], gap=10),
             Row([Text(0, "2"), Text(220, "KM-1002"), Text(400, "4")])]
    pdf = _small_po(tmp_path, [], pages=[page1, page2])
    lines = read_po_layout(pdf, SMALL).lines
    assert [(l.line_id, l.item_code.value, l.quantity.value, l.item_code.source.page) for l in lines] == [
        ("1", "KM-1001", "10", 1), ("2", "KM-1002", "4", 2)]


def test_a_po_number_or_version_loses_a_trailing_comma_semicolon_colon_or_full_stop(tmp_path):
    """A header value that is one word, the PO number or the version, loses a comma, semicolon, colon or full stop printed right after
    it, in its value and its text: "Inkooporder 250101, versie 3." gives PO 250101 and version 3, and a page header that repeats the
    number with a full stop after it gives the same number, so the PDF holds one PO."""
    def edit(pages):
        pages[0][0].texts[0].text = "Inkooporder 250101, versie 3."
        del pages[0][1]                                                      # the row "Versie 1"
        pages.append([Row([Text(0, "Vervolg van inkooporder 250101.")])])
    doc = read_po_layout(_edited_po(tmp_path, edit), MADEUP_LAYOUT)
    assert (doc.po_reference.value, doc.po_reference.text, doc.version_hint.value, doc.version_hint.text) == ("250101", "250101", "3", "3")
    assert (doc.pages, len(doc.lines)) == (2, 3)
