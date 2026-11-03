"""Tests of the PO PDF reader (aid/readers/po_pdf.py) on the made-up PO PDFs."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from aid.madeup.generate import PO_COLUMNS, load_specs, pdf_bytes, po_pages
from aid.readers.po_pdf import PoTemplate, load_template, read_po_pdf
from aid.schema import DocValue

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

def _edited_po(tmp_path: Path, edit) -> Path:
    """A PO PDF drawn like MU01's after `edit` has changed the rows of its pages (po_pages of the generator)."""
    spec = next(s for s in load_specs(SPEC_DIR) if s.case_id == "MU01")
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
