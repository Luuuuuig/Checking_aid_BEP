"""Tests of the parser baseline for supplier documents (aid/readers/supplier_parser.py) on the made-up order confirmations."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aid.evalscripts.score_reading import expected_fields
from aid.madeup.generate import TABLE_DE_COLUMNS, TABLE_NL_COLUMNS, Row, Text, confirmation_pages, load_specs, pdf_bytes
from aid.readers.supplier_parser import LayoutTemplate, detect_template, load_templates, read_with_template
from aid.schema import DocValue

SPEC_DIR = Path(__file__).resolve().parents[1] / "madeup" / "spec"
SPECS = load_specs(SPEC_DIR)
TEXT_CASES = [s for s in SPECS if s.confirmation and s.confirmation.layout != "scan"]       # the 18 confirmations with a text layer
SCANS = [s for s in SPECS if s.confirmation and s.confirmation.layout == "scan"]            # MU05 and MU16
LAYOUT_OF = {"table_nl": "table_nl", "merged": "table_nl", "total_only": "table_nl", "table_de": "table_de", "text_en": "text_en"}
DOC_FIELDS = ("po_reference", "version_hint", "supplier", "document_date", "currency", "incoterm", "capture_date")      # the keys of expected_fields
LINE_FIELDS = ("item_code", "description", "quantity", "unit", "unit_price", "price_basis", "discount_pct", "net_price", "delivery_date")
CELLS = ("item_code", "description", "quantity", "unit", "unit_price", "price_basis", "delivery_date")


def by_name(n): return next(t for t in load_templates() if t.name == n)


# --- the tests of the brief ---

@pytest.mark.parametrize("case,layout", [("MU01", "table_nl"), ("MU14", "table_de"), ("MU02", "text_en")])
def test_parser_reads_layout(madeup_out, case, layout):
    t = detect_template(madeup_out / f"{case}_conf.pdf", load_templates()); assert t.name == layout
    doc = read_with_template(madeup_out / f"{case}_conf.pdf", t)
    exp = expected_fields(SPEC_DIR / f"{case}.json")
    assert all(getattr(l, f).value == exp[(l.line_id, f)] for l in doc.lines for f in ("item_code", "quantity", "unit_price"))


def test_week_number_normalised(madeup_out):
    doc = read_with_template(madeup_out / "MU14_conf.pdf", by_name("table_de"))
    assert doc.lines[0].delivery_date.value == "2026-W48"


def test_scan_returns_nothing(madeup_out):
    doc = read_with_template(madeup_out / "MU05_conf.pdf", by_name("table_nl"))
    assert doc.lines == [] and doc.po_reference.missing


def test_merged_line_is_uncertain(madeup_out):
    doc = read_with_template(madeup_out / "MU19_conf.pdf", detect_template(madeup_out / "MU19_conf.pdf", load_templates()))
    assert any(l.item_code.uncertain and "KM-1901" in l.item_code.text for l in doc.lines)


# --- the whole set ---

def _values(doc) -> dict[tuple[str, str], str | None]:
    """The values of a Document under the keys of expected_fields: (line_id, field), the header fields under "doc"."""
    return {("doc", f): getattr(doc, f).value for f in DOC_FIELDS} | {(l.line_id, f): getattr(l, f).value for l in doc.lines for f in LINE_FIELDS}


@pytest.mark.parametrize("spec", TEXT_CASES, ids=[s.case_id for s in TEXT_CASES])
def test_the_set_reads_as_its_specs(madeup_out, spec):
    """All 18 confirmations with a text layer: the layout is found and the header and every line read back as the spec drew them."""
    pdf = madeup_out / f"{spec.case_id}_conf.pdf"
    template = detect_template(pdf, load_templates())
    assert template.name == LAYOUT_OF[spec.confirmation.layout]
    doc = read_with_template(pdf, template)
    expected = expected_fields(SPEC_DIR / f"{spec.case_id}.json")
    got = _values(doc)
    assert got.keys() == expected.keys()
    assert {key: (got[key], value) for key, value in expected.items() if got[key] != value} == {}
    assert [l.line_id for l in doc.lines] == [l.line_id for l in spec.confirmation.lines]
    flagged = {("doc", f) for f in DOC_FIELDS if getattr(doc, f).uncertain} | {(l.line_id, f) for l in doc.lines for f in LINE_FIELDS if getattr(l, f).uncertain}
    assert flagged == ({("1", "item_code")} if spec.case_id == "MU19" else set())            # only the two codes on one line are in doubt


@pytest.mark.parametrize("spec", SCANS, ids=[s.case_id for s in SCANS])
def test_a_scan_has_no_template_and_reads_as_nothing(madeup_out, spec):
    pdf = madeup_out / f"{spec.case_id}_conf.pdf"
    assert detect_template(pdf, load_templates()) is None
    doc = read_with_template(pdf, by_name("table_nl"))
    assert (doc.kind, doc.reader, doc.source_file, doc.pages, doc.lines) == ("confirmation", "parser:table_nl", pdf.name, 1, [])
    assert all(getattr(doc, f) == DocValue() for f in DOC_FIELDS)


def test_a_po_pdf_is_no_confirmation(madeup_out):
    assert detect_template(madeup_out / "MU01_po_v1.pdf", load_templates()) is None


# --- what the reader returns ---

def test_the_document_and_its_header(madeup_out):
    doc = read_with_template(madeup_out / "MU01_conf.pdf", by_name("table_nl"))
    assert (doc.kind, doc.reader, doc.source_file, doc.pages) == ("confirmation", "parser:table_nl", "MU01_conf.pdf", 1)
    assert (doc.supplier.value, doc.po_reference.value, doc.currency.value, doc.incoterm.value) == ("S01", "250101", "EUR", "DAP Rotterdam")
    assert (doc.document_date.text, doc.document_date.value) == ("21-10-2026", "2026-10-21")
    header = (doc.supplier, doc.po_reference, doc.document_date, doc.currency, doc.incoterm)
    assert [(v.source.page, v.source.line) for v in header] == [(1, 1), (1, 3), (1, 4), (1, 5), (1, 6)]      # each value names the row it came from
    assert doc.po_reference.source.text == "Uw inkooporder 250101" and doc.po_reference.text == "250101"
    assert doc.version_hint == DocValue() and doc.capture_date == DocValue() and not any(v.uncertain for v in header)


def test_the_version_a_confirmation_quotes(madeup_out):
    doc = read_with_template(madeup_out / "MU03_conf.pdf", by_name("table_nl"))
    assert (doc.po_reference.value, doc.version_hint.value, doc.version_hint.text) == ("250103", "1", "1")
    assert doc.version_hint.source.line == doc.po_reference.source.line == 3


def test_a_version_of_two_digits(tmp_path):
    def edit(pages):
        pages[0][2].texts[0].text = "Uw inkooporder 250103, versie 12"
    doc = read_with_template(_edited(tmp_path, "MU03", edit), by_name("table_nl"))
    assert (doc.po_reference.value, doc.version_hint.value) == ("250103", "12")


@pytest.mark.parametrize("case,layout,reference", [("MU03", "table_nl", "Uw inkooporder 250103, versie 2"), ("MU04", "table_de", "Ihre Bestellung 250104, Version 2"),
                                                    ("MU02", "text_en", "Your PO 250102, version 2")])
def test_the_version_a_confirmation_quotes_is_read_in_each_language(tmp_path, case, layout, reference):
    """The generator words the quote as the layout does; the set itself has only MU03 quoting a version."""
    doc = read_with_template(_quoting(tmp_path, case, 2), by_name(layout))
    assert (doc.version_hint.value, doc.version_hint.text, doc.version_hint.source.text) == ("2", "2", reference)
    assert doc.version_hint.source.line == doc.po_reference.source.line == 3


@pytest.mark.parametrize("case,layout,title,text,po_number", [
    ("MU01", "table_nl", "Omschrijving", "Pomp versie 2", "250101"),
    ("MU04", "table_de", "Bezeichnung", "Pumpe Version 2", "250104"),
    ("MU02", "text_en", None, "Pump version 3", "250102"),
])
def test_a_version_in_a_description_is_not_the_version_the_confirmation_quotes(tmp_path, case, layout, title, text, po_number):
    """These headers quote no version. A description that mentions one, in a table row or in a sentence, must not give the reading a version:
    the version check would take it for a quote."""
    if title:
        def edit(pages):
            _cell(pages[0][7], title, TABLE_DE_COLUMNS if layout == "table_de" else TABLE_NL_COLUMNS).text = text
        pdf = _edited(tmp_path, case, edit)
    else:
        pdf = _prose(tmp_path, description=text)
    doc = read_with_template(pdf, by_name(layout))
    assert doc.version_hint == DocValue() and doc.po_reference.value == po_number
    assert doc.lines[0].description.value == text


def test_a_line_keeps_the_text_of_each_cell_beside_its_value(madeup_out):
    line = read_with_template(madeup_out / "MU01_conf.pdf", by_name("table_nl")).lines[0]
    assert line.line_id == "1"
    assert [(getattr(line, f).text, getattr(line, f).value) for f in CELLS] == [
        ("KM-1001", "KM-1001"), ("Hydraulische slang 10 mm, 2 m", "Hydraulische slang 10 mm, 2 m"), ("10", "10"), ("st", "pc"),
        ("12,50", "12.50"), ("1", "1"), ("16-11-2026", "2026-11-16")]
    assert line.discount_pct == DocValue() and line.net_price == DocValue()                  # the layout has no such columns
    assert all(getattr(line, f).source.page == 1 and getattr(line, f).source.line == 8 for f in CELLS)
    assert line.unit_price.source.text == "1 KM-1001 Hydraulische slang 10 mm, 2 m 10 st 12,50 1 16-11-2026"


def test_the_lines_of_a_second_page(madeup_out):
    """MU04: 25 lines on page 1 and 7 on page 2, where the title row is repeated and is not a line."""
    doc = read_with_template(madeup_out / "MU04_conf.pdf", by_name("table_de"))
    assert doc.pages == 2 and [l.line_id for l in doc.lines] == [str(n) for n in range(1, 33)]
    assert [l.item_code.value for l in doc.lines] == [f"KM-04{n:02d}" for n in range(1, 33)]
    last_on_first, first_on_second = doc.lines[24], doc.lines[25]
    assert (last_on_first.unit_price.source.page, last_on_first.unit_price.source.line) == (1, 32)
    assert (first_on_second.unit_price.source.page, first_on_second.unit_price.source.line) == (2, 2)
    assert doc.lines[31].unit_price.source.page == 2 and all(l.unit_price.source is not None for l in doc.lines)


def test_every_cell_of_every_line_has_its_text_and_its_source(madeup_out):
    for name, layout in (("MU04_conf.pdf", "table_de"), ("MU02_conf.pdf", "text_en")):
        for line in read_with_template(madeup_out / name, by_name(layout)).lines:
            for field in CELLS:
                cell = getattr(line, field)
                assert cell.text and cell.value is not None and not cell.uncertain, (name, line.line_id, field)
                assert cell.source is not None and cell.text in cell.source.text, (name, line.line_id, field)


# --- the three layouts ---

def test_a_total_only_confirmation_has_no_price(madeup_out):
    """MU06 prints a Totaal column and no Prijs or Per: the total maps to nothing, and the prices stay missing for the later rule."""
    template = detect_template(madeup_out / "MU06_conf.pdf", load_templates())
    assert template.name == "table_nl"
    doc = read_with_template(madeup_out / "MU06_conf.pdf", template)
    assert [l.item_code.value for l in doc.lines] == ["KM-0601", "KM-0602", "KM-0603"]
    assert [(l.quantity.value, l.unit.value, l.delivery_date.value) for l in doc.lines] == [("3", "pc", "2026-11-16"), ("2", "pc", "2026-11-16"), ("12", "pc", "2026-11-16")]
    assert all(l.unit_price == DocValue() and l.price_basis == DocValue() and l.net_price == DocValue() for l in doc.lines)


def test_a_price_cell_that_holds_its_basis(madeup_out):
    """MU09 line 1 prints "12,50 / 100 st" in the price cell and leaves the Per cell empty."""
    first, second = read_with_template(madeup_out / "MU09_conf.pdf", by_name("table_nl")).lines
    assert (first.unit_price.text, first.unit_price.value, first.unit_price.uncertain) == ("12,50 / 100 st", "12.50", False)
    assert (first.price_basis.text, first.price_basis.value, first.price_basis.uncertain) == ("/ 100 st", "100", False)
    assert first.price_basis.source.line == first.unit_price.source.line == 8
    assert (second.unit_price.text, second.unit_price.value, second.price_basis.text, second.price_basis.value) == ("0,95", "0.95", "1", "1")


def test_a_discount_and_the_price_unit_column_of_table_de(madeup_out):
    """MU10: Rabatt holds "5 %" on line 1 and nothing on line 2; PE is the price basis."""
    first, second = read_with_template(madeup_out / "MU10_conf.pdf", by_name("table_de")).lines
    assert (first.discount_pct.text, first.discount_pct.value, first.unit.text, first.unit.value) == ("5 %", "5", "Stk", "pc")
    assert (first.unit_price.value, first.price_basis.value, first.delivery_date.text, first.delivery_date.value) == ("20.00", "1", "16.11.2026", "2026-11-16")
    assert second.discount_pct == DocValue() and second.unit_price.value == "4.10"


def test_a_delivery_range_and_the_decimal_comma_of_table_de(madeup_out):
    doc = read_with_template(madeup_out / "MU15_conf.pdf", by_name("table_de"))
    assert doc.lines[0].delivery_date.text == "30.11.2026 - 04.12.2026" and doc.lines[0].delivery_date.value == "2026-11-30/2026-12-04"
    assert (doc.lines[0].unit_price.text, doc.lines[0].unit_price.value) == ("980,00", "980.00")


def test_a_prose_line_is_cut_into_its_values(madeup_out):
    """MU02: "Line 1: 2 pcs of KM-0201 Gear pump 8 cc at EUR 1.234,50 each, delivery 2026-11-23." The price is written the European way
    in an English text, and "each" is the price basis."""
    doc = read_with_template(madeup_out / "MU02_conf.pdf", by_name("text_en"))
    line = doc.lines[0]
    assert line.line_id == "1"
    assert [(getattr(line, f).text, getattr(line, f).value) for f in CELLS] == [
        ("KM-0201", "KM-0201"), ("Gear pump 8 cc", "Gear pump 8 cc"), ("2", "2"), ("pcs", "pc"), ("1.234,50", "1234.50"), ("each", "1"),
        ("2026-11-23", "2026-11-23")]
    assert line.unit_price.source.line == 8 and line.unit_price.source.text.startswith("Line 1: 2 pcs of KM-0201")
    assert (doc.currency.value, doc.incoterm.value, doc.document_date.value) == ("EUR", "FCA Rotterdam", "2026-10-22")


def test_a_price_in_another_currency_code_is_read(tmp_path):
    """MU02 line 1 priced "at USD 1.234,50 each": any three-letter code makes a price phrase, and the description stops before it."""
    def edit(pages):
        row = pages[0][7]
        row.texts[0].text = row.texts[0].text.replace("at EUR", "at USD")
    line = read_with_template(_edited(tmp_path, "MU02", edit), by_name("text_en")).lines[0]
    assert (line.description.value, line.unit_price.text, line.unit_price.value, line.price_basis.value) == ("Gear pump 8 cc", "1.234,50", "1234.50", "1")


def test_a_pack_size_in_a_description_is_no_price_basis(madeup_out):
    """MU11 prints "(packed per 12)" in the description: it stays in the description and the basis is still each."""
    line = read_with_template(madeup_out / "MU11_conf.pdf", by_name("text_en")).lines[0]
    assert (line.description.value, line.quantity.value, line.price_basis.text, line.price_basis.value) == (
        "Hose clamp 20-32 mm (packed per 12)", "12", "each", "1")


def test_a_unit_the_document_does_not_print_is_missing(madeup_out):
    """MU13 line 1 has no unit cell: the quantity before it and the price after it are still read."""
    line = read_with_template(madeup_out / "MU13_conf.pdf", by_name("table_de")).lines[0]
    assert (line.unit, line.quantity.value, line.unit_price.value) == (DocValue(), "25", "3.20")


def test_an_incoterm_the_document_does_not_print_is_missing(madeup_out):
    """MU20 has no row with delivery terms."""
    doc = read_with_template(madeup_out / "MU20_conf.pdf", by_name("text_en"))
    assert doc.incoterm == DocValue() and doc.currency.value == "EUR" and len(doc.lines) == 2


# --- cells the normalisers cannot read, cells that are empty, words that sit between columns ---

def _edited(tmp_path: Path, case: str, edit) -> Path:
    """The confirmation of `case` drawn by the generator after `edit` has changed the rows of its pages."""
    pages = confirmation_pages(next(s for s in SPECS if s.case_id == case))
    edit(pages)
    path = tmp_path / f"{case}_edited.pdf"
    path.write_bytes(pdf_bytes(pages, "edited confirmation"))
    return path


def _cell(row, title: str, columns=TABLE_NL_COLUMNS) -> Text:
    """The text of `row` that stands in the column with this title."""
    return next(t for t in row.texts if t.x == next(c.x for c in columns if c.title == title))


def _with_cell(tmp_path: Path, title: str, text: str, shift: float = 0) -> Path:
    """MU01's confirmation with the cell under `title` of its first line replaced by `text` ("" leaves the cell empty) and drawn
    `shift` points to the right of its column."""
    def edit(pages):
        first_line = pages[0][7]                              # six header rows and the title row come first
        cell = _cell(first_line, title)
        if text:
            cell.text, cell.x = text, cell.x + shift
        else:
            first_line.texts.remove(cell)
    return _edited(tmp_path, "MU01", edit)


@pytest.mark.parametrize("title,text,field,value,uncertain", [
    ("Prijs", "1.234,50", "unit_price", "1234.50", False),            # decimal comma: "." is the thousands mark
    ("Aantal", "1.000", "quantity", "1000", False),                   # ... so a quantity of a thousand is not 1.000
    ("Eenheid", "Stuks", "unit", "pc", False),
    ("Per", "100", "price_basis", "100", False),
    ("Leverdatum", "week 48", "delivery_date", "2026-W48", False),
    ("Leverdatum", "wk 2", "delivery_date", "2027-W02", False),       # the confirmation's own date (21 Oct 2026) puts a week without a year in 2027
    ("Prijs", "n.v.t.", "unit_price", None, True),                    # not readable: the text stays, the value is None and uncertain
    ("Aantal", "tien", "quantity", None, True),
    ("Per", "n.v.t.", "price_basis", None, True),
    ("Leverdatum", "31-02-2026", "delivery_date", None, True),        # no such day
])
def test_a_cell_is_normalised_or_marked_uncertain(tmp_path, title, text, field, value, uncertain):
    cell = getattr(read_with_template(_with_cell(tmp_path, title, text), by_name("table_nl")).lines[0], field)
    assert (cell.value, cell.text, cell.uncertain) == (value, text, uncertain)
    assert (cell.source.page, cell.source.line) == (1, 8) and text in cell.source.text


def test_a_basis_inside_the_price_that_cannot_be_read_is_uncertain(tmp_path):
    """MU09 line 1 with the price "12,50 / n.v.t.": the price is read, and the basis is found but unreadable."""
    def edit(pages):
        price = next(t for t in pages[0][7].texts if t.text == "12,50 / 100 st")
        price.text = "12,50 / n.v.t."
    line = read_with_template(_edited(tmp_path, "MU09", edit), by_name("table_nl")).lines[0]
    assert (line.unit_price.value, line.unit_price.uncertain) == ("12.50", False)
    assert (line.price_basis.value, line.price_basis.text, line.price_basis.uncertain) == (None, "/ n.v.t.", True)


def test_a_per_cell_wins_over_a_basis_inside_the_price(tmp_path):
    """The Per column is read alone as the basis, also when the price cell holds a basis as well."""
    def edit(pages):
        pages[0][7].texts.append(Text(next(c.x for c in TABLE_NL_COLUMNS if c.title == "Per"), "1000"))
    line = read_with_template(_edited(tmp_path, "MU09", edit), by_name("table_nl")).lines[0]
    assert (line.unit_price.value, line.price_basis.text, line.price_basis.value) == ("12.50", "1000", "1000")


def test_a_price_cell_with_only_a_basis_has_no_price(tmp_path):
    """The number before the mark is the price. With nothing before it the price is unreadable, and is not the 100 behind the mark."""
    def edit(pages):
        next(t for t in pages[0][7].texts if t.text == "12,50 / 100 st").text = "/ 100 st"
    line = read_with_template(_edited(tmp_path, "MU09", edit), by_name("table_nl")).lines[0]
    assert (line.unit_price.value, line.unit_price.text, line.unit_price.uncertain) == (None, "/ 100 st", True)
    assert (line.price_basis.value, line.price_basis.uncertain) == ("100", False)


def test_a_whole_number_in_the_price_is_no_basis(tmp_path):
    """MU01's first line with an empty Per cell: the basis is missing, also when the price is a whole number such as 13."""
    line = read_with_template(_with_cell(tmp_path, "Per", ""), by_name("table_nl")).lines[0]
    assert (line.unit_price.value, line.price_basis) == ("12.50", DocValue())

    def edit(pages):
        _cell(pages[0][7], "Prijs").text = "13"
        pages[0][7].texts.remove(_cell(pages[0][7], "Per"))
    line = read_with_template(_edited(tmp_path, "MU01", edit), by_name("table_nl")).lines[0]
    assert (line.unit_price.value, line.price_basis) == ("13", DocValue())


@pytest.mark.parametrize("title,field", [("Prijs", "unit_price"), ("Aantal", "quantity"), ("Eenheid", "unit"), ("Per", "price_basis"),
                                         ("Leverdatum", "delivery_date"), ("Artikel", "item_code"), ("Omschrijving", "description")])
def test_an_empty_cell_stays_missing(tmp_path, title, field):
    """A missing Per cell is missing, not 1, although the rest of the row holds other numbers; the line is still read."""
    doc = read_with_template(_with_cell(tmp_path, title, ""), by_name("table_nl"))
    assert getattr(doc.lines[0], field) == DocValue() and doc.lines[0].line_id == "1" and len(doc.lines) == 3


def test_a_word_goes_to_the_column_that_holds_its_middle(tmp_path):
    """A price drawn flush right under its title starts a few points left of the title and still belongs to the Prijs column."""
    line = read_with_template(_with_cell(tmp_path, "Prijs", "12,50", shift=-5), by_name("table_nl")).lines[0]
    assert (line.unit_price.value, line.unit.value, line.unit.text) == ("12.50", "pc", "st")
    line = read_with_template(_with_cell(tmp_path, "Eenheid", "st", shift=-2), by_name("table_nl")).lines[0]            # "st" is 7 points wide
    assert (line.unit.value, line.quantity.value) == ("pc", "10")


def test_a_word_left_of_the_first_title_belongs_to_the_first_column(tmp_path):
    """A line number drawn flush right under "Pos" starts left of the title: it is still the line number, not a cell of the last column."""
    doc = read_with_template(_with_cell(tmp_path, "Pos", "1", shift=-6), by_name("table_nl"))
    assert [l.line_id for l in doc.lines] == ["1", "2", "3"] and doc.lines[0].delivery_date.value == "2026-11-16"


def test_a_row_without_a_line_number_is_not_a_line(tmp_path):
    doc = read_with_template(_with_cell(tmp_path, "Pos", ""), by_name("table_nl"))
    assert [l.line_id for l in doc.lines] == ["2", "3"]


def test_columns_are_found_by_title_word_and_not_by_position(tmp_path):
    """MU01 with the columns Aantal and Eenheid swapped in the title row and in the first line: each value follows its title word."""
    def edit(pages):
        by_title = {t.text: t for t in pages[0][6].texts}
        by_title["Aantal"].text, by_title["Eenheid"].text = "Eenheid", "Aantal"
        quantity, unit = _cell(pages[0][7], "Aantal"), _cell(pages[0][7], "Eenheid")
        quantity.text, unit.text = unit.text, quantity.text
    line = read_with_template(_edited(tmp_path, "MU01", edit), by_name("table_nl")).lines[0]
    assert (line.quantity.text, line.quantity.value, line.unit.text, line.unit.value) == ("10", "10", "st", "pc")


def test_a_description_with_header_words_is_neither_a_title_row_nor_a_header(tmp_path):
    """MU01 quotes no version, so nothing in the header can be mistaken for one. A description that holds the title marker and a version
    is no new title row (the columns stay) and no header row (the version stays missing)."""
    def edit(pages):
        _cell(pages[0][7], "Omschrijving").text = "Artikel versie 9"
    doc = read_with_template(_edited(tmp_path, "MU01", edit), by_name("table_nl"))
    assert len(doc.lines) == 3 and doc.lines[0].description.value == "Artikel versie 9" and doc.lines[0].quantity.value == "10"
    assert doc.version_hint == DocValue() and doc.po_reference.source.line == 3


def test_a_header_value_that_cannot_be_read_is_uncertain(tmp_path):
    def edit(pages):
        pages[0][3].texts[0].text = "Datum: 31-02-2026"           # no such day
        pages[0][4].texts[0].text = "Valuta: XYZ"                  # a currency the aid does not know
    doc = read_with_template(_edited(tmp_path, "MU01", edit), by_name("table_nl"))
    assert (doc.document_date.value, doc.document_date.text, doc.document_date.uncertain) == (None, "31-02-2026", True)
    assert (doc.currency.value, doc.currency.text, doc.currency.uncertain) == (None, "XYZ", True)
    assert doc.document_date.source.line == 4 and doc.currency.source.line == 5
    assert doc.po_reference.value == "250101" and not doc.po_reference.uncertain and len(doc.lines) == 3
    assert doc.lines[0].delivery_date.value == "2026-11-16"          # a date without a year does not need the document date


def test_a_week_needs_the_document_date(tmp_path):
    """The delivery week of MU14 has no year; without a readable document date it cannot be placed, and says so."""
    def edit(pages):
        pages[0][3].texts[0].text = "Datum: 31.02.2026"
    doc = read_with_template(_edited(tmp_path, "MU14", edit), by_name("table_de"))
    assert doc.document_date.uncertain and doc.lines[0].delivery_date.text == "KW 48"
    assert (doc.lines[0].delivery_date.value, doc.lines[0].delivery_date.uncertain) == (None, True)


def test_the_first_row_that_matches_gives_the_header_value(tmp_path):
    """A label that comes back further down (a page header repeated with another value) does not replace the first value."""
    pdf = _edited(tmp_path, "MU01", lambda pages: pages[0].append(Row([Text(0, "Valuta: USD")])))
    doc = read_with_template(pdf, by_name("table_nl"))
    assert (doc.currency.value, doc.currency.source.line) == ("EUR", 5) and [l.line_id for l in doc.lines] == ["1", "2", "3"]


def test_a_header_field_the_pdf_does_not_show_is_missing(tmp_path):
    doc = read_with_template(_edited(tmp_path, "MU01", lambda pages: pages[0].pop(4)), by_name("table_nl"))          # the "Valuta" row
    assert doc.currency == DocValue() and doc.incoterm.value == "DAP Rotterdam" and doc.incoterm.source.line == 5
    assert [l.item_code.source.line for l in doc.lines] == [7, 8, 9]                 # the rows below moved up by one


def test_two_item_codes_in_a_prose_line(tmp_path):
    """The prose layout can also put two codes on one line: one DocLine, both codes in the text, uncertain."""
    def edit(pages):
        row = pages[0][7]
        row.texts[0].text = row.texts[0].text.replace("KM-0201", "KM-0201 / KM-0202")
    first, second = read_with_template(_edited(tmp_path, "MU02", edit), by_name("text_en")).lines
    assert (first.item_code.text, first.item_code.uncertain) == ("KM-0201 / KM-0202", True)
    assert (first.description.value, first.unit_price.value) == ("Gear pump 8 cc", "1234.50")
    assert (second.item_code.value, second.item_code.uncertain) == ("KM-0202", False)


def _prose(tmp_path: Path, **changes) -> Path:
    """MU02's confirmation with the first line changed as given (fields of the spec's line) and drawn by the generator."""
    spec = next(s for s in SPECS if s.case_id == "MU02")
    lines = [spec.confirmation.lines[0].model_copy(update=changes), *spec.confirmation.lines[1:]]
    spec = spec.model_copy(update={"confirmation": spec.confirmation.model_copy(update={"lines": lines})})
    path = tmp_path / "prose.pdf"
    path.write_bytes(pdf_bytes(confirmation_pages(spec), "prose confirmation"))
    return path


def _quoting(tmp_path: Path, case: str, version: int) -> Path:
    """The confirmation of `case` drawn as if it quoted version `version` of the PO ("versie 2", "Version 2", "version 2")."""
    spec = next(s for s in SPECS if s.case_id == case)
    spec = spec.model_copy(update={"confirmation": spec.confirmation.model_copy(update={"version_quoted": version})})
    path = tmp_path / f"{case}_quoting_{version}.pdf"
    path.write_bytes(pdf_bytes(confirmation_pages(spec), "quoting confirmation"))
    return path


@pytest.mark.parametrize("changes,expected", [
    ({"unit": None}, {"unit": None, "quantity": ("2", "2", False), "item_code": ("KM-0201", "KM-0201", False)}),
    ({"delivery_date": None}, {"delivery_date": None, "price_basis": ("each", "1", False), "description": ("Gear pump 8 cc",) * 2 + (False,)}),
    ({"unit_price": "4.80", "price_basis": "100", "text_override": {}}, {"unit_price": ("4.80", "4.80", False), "price_basis": ("per 100", "100", False)}),
    ({"discount_pct": "5"}, {"discount_pct": ("5%", "5", False), "delivery_date": ("2026-11-23", "2026-11-23", False)}),
    ({"unit_price": None, "price_basis": None, "text_override": {}}, {"unit_price": None, "price_basis": None, "description": ("Gear pump 8 cc",) * 2 + (False,),
                                                                    "delivery_date": ("2026-11-23", "2026-11-23", False)}),
    ({"text_override": {"unit_price": "n.v.t."}}, {"unit_price": ("n.v.t.", None, True), "price_basis": ("each", "1", False)}),
    ({"delivery_date": "2026-W48"}, {"delivery_date": ("week 48", "2026-W48", False)}),
])
def test_the_optional_parts_of_a_prose_line(tmp_path, changes, expected):
    """A sentence may leave out the unit, the price, the basis or the delivery, or add a discount: what is not there is missing."""
    line = read_with_template(_prose(tmp_path, **changes), by_name("text_en")).lines[0]
    got = {field: None if getattr(line, field) == DocValue() else (getattr(line, field).text, getattr(line, field).value, getattr(line, field).uncertain)
           for field in expected}
    assert got == expected
    assert line.line_id == "1" and line.quantity.value == "2"


def test_the_number_convention_belongs_to_the_template(tmp_path):
    """The same "1.000" is a thousand under the Dutch convention and one with three decimals under the English one."""
    pdf = _with_cell(tmp_path, "Aantal", "1.000")
    assert read_with_template(pdf, by_name("table_nl")).lines[0].quantity.value == "1000"
    assert read_with_template(pdf, by_name("table_nl").model_copy(update={"decimal_comma": False})).lines[0].quantity.value == "1.000"
    assert (by_name("table_nl").decimal_comma, by_name("table_de").decimal_comma, by_name("text_en").decimal_comma) == (True, True, False)


# --- finding the layout ---

def test_detect_template_takes_the_first_template_that_matches(madeup_out):
    pdf = madeup_out / "MU01_conf.pdf"
    first, second = by_name("table_nl").model_copy(update={"name": "first"}), by_name("table_nl").model_copy(update={"name": "second"})
    assert detect_template(pdf, [first, second]).name == "first"
    assert detect_template(pdf, [second, first]).name == "second"
    assert detect_template(pdf, [by_name("text_en"), by_name("table_de"), first]).name == "first"
    assert detect_template(pdf, [by_name("text_en"), by_name("table_de")]) is None
    assert detect_template(pdf, []) is None


def test_detect_template_needs_every_expression_to_match(madeup_out):
    pdf = madeup_out / "MU01_conf.pdf"
    template = by_name("table_nl")
    assert detect_template(pdf, [template.model_copy(update={"detect": [*template.detect, "Auftragsbest\u00e4tigung"]})]) is None
    assert detect_template(pdf, [template.model_copy(update={"detect": ["Orderbevestiging"]})]) is not None


@pytest.mark.parametrize("name,layout", [("MU01_conf.pdf", "table_nl"), ("MU04_conf.pdf", "table_de"), ("MU02_conf.pdf", "text_en")])
def test_each_layout_is_found_only_by_its_own_template(madeup_out, name, layout):
    assert [t.name for t in load_templates() if detect_template(madeup_out / name, [t])] == [layout]


# --- which rows are lines ---

def _with_table(template: LayoutTemplate, **changes) -> LayoutTemplate:
    """The template with keys of its table changed; made through validation, so that a key the table does not know fails."""
    return LayoutTemplate.model_validate(template.model_dump() | {"table": template.table | changes})


def test_a_page_without_a_title_row_continues_the_table(tmp_path):
    """MU04 with the title row of page 2 removed: page 2 is cut with the columns of page 1, and all 32 lines are read."""
    doc = read_with_template(_edited(tmp_path, "MU04", lambda pages: pages[1].pop(0)), by_name("table_de"))
    assert _values(doc) == expected_fields(SPEC_DIR / "MU04.json")
    last_on_first, first_on_second = doc.lines[24], doc.lines[25]
    assert (last_on_first.unit_price.source.page, last_on_first.unit_price.source.line) == (1, 32)
    assert (first_on_second.line_id, first_on_second.unit_price.source.page, first_on_second.unit_price.source.line) == ("26", 2, 1)


def test_a_page_before_the_first_title_row_has_no_lines(tmp_path):
    """A cover page: there are no columns to cut it with, and its rows are not lines, also when one begins with a number."""
    def edit(pages):
        pages.insert(0, [Row([Text(0, "Cover letter")]), Row([Text(0, "1 General terms apply")])])
    doc = read_with_template(_edited(tmp_path, "MU01", edit), by_name("table_nl"))
    assert doc.pages == 2 and [l.line_id for l in doc.lines] == ["1", "2", "3"] and doc.lines[0].item_code.source.page == 2


@pytest.mark.parametrize("case,layout,drop", [("MU01", "table_nl", [(0, 6)]), ("MU04", "table_de", [(0, 6), (1, 0)])],
                         ids=["a page", "two pages"])
def test_a_document_with_rows_and_no_title_row_is_refused(tmp_path, case, layout, drop):
    """No page has the title row (the template is for another layout): nothing can be cut into columns, and the file is named."""
    def edit(pages):
        for page, row in drop:
            pages[page].pop(row)
    pdf = _edited(tmp_path, case, edit)
    with pytest.raises(ValueError, match=rf"{case}_edited\.pdf \(template '{layout}'\): no row contains"):
        read_with_template(pdf, by_name(layout))


def test_a_total_row_is_not_a_line(tmp_path):
    """"Totaal EUR 192,00" under the lines of MU01: its first cell is not a line number."""
    pdf = _edited(tmp_path, "MU01", lambda pages: pages[0].append(Row([Text(0, "Totaal EUR 192,00")])))
    assert [l.line_id for l in read_with_template(pdf, by_name("table_nl")).lines] == ["1", "2", "3"]


@pytest.mark.parametrize("drop_title_of_page_2", [False, True], ids=["title row repeated", "continuation page"])
def test_a_page_number_is_not_a_line(tmp_path, drop_title_of_page_2):
    """"Seite 1 von 2" under the lines of page 1 of MU04 and "Seite 2 von 2" under page 2: no phantom lines between or after the 32."""
    def edit(pages):
        pages[0].append(Row([Text(0, "Seite 1 von 2")]))
        pages[1].append(Row([Text(0, "Seite 2 von 2")]))
        if drop_title_of_page_2:
            pages[1].pop(0)
    doc = read_with_template(_edited(tmp_path, "MU04", edit), by_name("table_de"))
    assert _values(doc) == expected_fields(SPEC_DIR / "MU04.json")


def test_the_line_id_pattern_of_the_template_decides_which_rows_are_lines(madeup_out, tmp_path):
    """The default is a whole number. A row is a line when its line number matches the pattern in full."""
    totals = _edited(tmp_path, "MU01", lambda pages: pages[0].append(Row([Text(0, "Totaal EUR 192,00")])))
    wider = _with_table(by_name("table_nl"), line_id_pattern=r"\d+|Totaal")
    assert [l.line_id for l in read_with_template(totals, wider).lines] == ["1", "2", "3", "Totaal"]
    anything = _with_table(by_name("table_nl"), line_id_pattern=r"\w+")                  # "Pos", the title word, would pass: the title row is no row
    assert [l.line_id for l in read_with_template(madeup_out / "MU01_conf.pdf", anything).lines] == ["1", "2", "3"]
    one_digit = _with_table(by_name("table_de"), line_id_pattern=r"\d")                  # "10" is not a digit: full match, not a prefix
    assert [l.line_id for l in read_with_template(madeup_out / "MU04_conf.pdf", one_digit).lines] == [str(n) for n in range(1, 10)]


def test_a_line_id_pattern_that_matches_nothing_yields_no_lines(madeup_out):
    """A table with its title row and no row that is a line is an empty table, not an error: the header is still read."""
    doc = read_with_template(madeup_out / "MU01_conf.pdf", _with_table(by_name("table_nl"), line_id_pattern=r"X\d+"))
    assert doc.lines == [] and doc.po_reference.value == "250101" and doc.currency.value == "EUR"


# --- a PDF the template does not fit ---

@pytest.mark.parametrize("name,layout,message", [
    ("MU02_conf.pdf", "table_nl", "no row contains 'Artikel'"),                 # prose lines, no table
    ("MU01_conf.pdf", "text_en", "no line of the text matches"),                # a table, no prose lines
    ("MU01_po_v1.pdf", "table_nl", "no column for the line number"),            # a table of another layout: Regel, not Pos
])
def test_a_pdf_the_template_does_not_fit_is_refused(madeup_out, name, layout, message):
    """A PDF with a text layer that the template cannot cut into lines is refused, not read as a confirmation with no lines:
    no lines would look like lines missing on the confirmation."""
    with pytest.raises(ValueError, match=message):
        read_with_template(madeup_out / name, by_name(layout))


@pytest.mark.parametrize("code", ["EUR", "USD"])
def test_a_price_phrase_that_does_not_fit_is_refused_and_not_folded_into_the_description(tmp_path, code):
    """"at EUR 1.234,50 net each": "net each" is no basis. The description may not swallow the price phrase, so the line is refused
    instead of being read with no price and a description that holds one. The same holds for any currency code."""
    def edit(pages):
        row = pages[0][7]
        row.texts[0].text = row.texts[0].text.replace("at EUR 1.234,50 each", f"at {code} 1.234,50 net each")
    with pytest.raises(ValueError, match="page 1, line 8: the row starts like a line but does not match"):
        read_with_template(_edited(tmp_path, "MU02", edit), by_name("text_en"))


def test_a_sentence_that_starts_like_a_line_but_does_not_fit_is_refused(tmp_path):
    """MU02 with line 2 worded differently ("of" left out). Skipping it would read as a PO line the confirmation leaves out."""
    def edit(pages):
        pages[0][8].texts[0].text = "Line 2: 2 pcs KM-0202 Pump seal kit at EUR 48.20 each, delivery 2026-11-23."
    with pytest.raises(ValueError, match=r"page 1, line 9: the row starts like a line but does not match"):
        read_with_template(_edited(tmp_path, "MU02", edit), by_name("text_en"))
    def edit_intro(pages):
        pages[0][6].texts[0].text = "Thank you. We confirm the lines below:"           # not a line, and not an error
    assert len(read_with_template(_edited(tmp_path, "MU02", edit_intro), by_name("text_en")).lines) == 2


def test_a_title_word_twice_is_refused(tmp_path):
    """Two columns for one field cannot be told apart, so the reader does not choose."""
    pdf = _edited(tmp_path, "MU01", lambda pages: pages[0][6].texts.append(Text(500, "Prijs")))
    with pytest.raises(ValueError, match="'unit_price' more than once"):
        read_with_template(pdf, by_name("table_nl"))


# --- the templates ---

def test_load_templates(tmp_path):
    templates = load_templates()
    assert [(t.name, t.language, t.table["strategy"]) for t in templates] == [("table_de", "de", "lines"), ("table_nl", "nl", "lines"), ("text_en", "en", "text")]
    assert "po_madeup" not in [t.name for t in templates]                          # the PO template belongs to read_po_pdf
    assert [t.name for t in load_templates(Path(__file__).resolve().parents[1] / "readers" / "templates")] == [t.name for t in templates]
    assert load_templates(tmp_path) == []
    with pytest.raises(FileNotFoundError, match="no_such_folder"):
        load_templates(tmp_path / "no_such_folder")


def test_a_template_file_is_named_like_its_template(tmp_path):
    data = by_name("table_nl").model_dump()
    (tmp_path / "table_nl.json").write_text(json.dumps(data | {"name": "table_xx"}), encoding="utf-8")
    with pytest.raises(ValueError, match="table_nl.json holds the template 'table_xx'"):
        load_templates(tmp_path)
    (tmp_path / "table_nl.json").write_text(json.dumps(data | {"colour": "red"}), encoding="utf-8")
    with pytest.raises(ValueError, match="table_nl.json: colour: Extra inputs are not permitted"):                       # the message names the file
        load_templates(tmp_path)


NL, EN = by_name("table_nl").model_dump(), by_name("text_en").model_dump()          # the templates as plain dicts, which the cases below change
LINES, TEXT, LINE_REGEX = NL["table"], EN["table"], EN["table"]["line_regex"]
BAD_TEMPLATES = [                  # (template, what is changed in it, a part of the error); each breaks one rule, and the error names that rule
    pytest.param(NL, {"colour": "red"}, "Extra inputs are not permitted", id="a key the template does not know"),
    pytest.param(NL, {"language": "fr"}, "Input should be", id="a language that is not nl, de or en"),
    pytest.param(NL, {"detect": []}, "at least 1 item", id="no detect expression (it would match everything)"),
    pytest.param(NL, {"detect": ["("]}, "detect '\\(' is not a regular expression", id="a detect expression that does not compile"),
    pytest.param(NL, {"header": NL["header"] | {"colour": "x"}}, "Input should be", id="a header field that is no Document field"),
    pytest.param(NL, {"header": NL["header"] | {"currency": "Valuta: \\w+"}}, "of currency needs exactly one group", id="a header expression with no group"),
    pytest.param(NL, {"header": NL["header"] | {"currency": "(Valuta): (\\w+)"}}, "of currency needs exactly one group", id="a header expression with two groups"),
    pytest.param(NL, {"table": {}}, "Unable to extract tag using discriminator", id="a table with no strategy"),
    pytest.param(NL, {"table": LINES | {"strategy": "grid"}}, "does not match any of the expected tags", id="a strategy that does not exist"),
    pytest.param(NL, {"table": LINES | {"colour": "x"}}, "colour: Extra inputs are not permitted", id="a table key the strategy does not know"),
    pytest.param(NL, {"table": LINES | {"columns": [c for c in LINES["columns"] if c["field"] != "line_id"]}}, "the table needs a line_id column",
                 id="no line_id column"),
    pytest.param(NL, {"table": LINES | {"columns": [*LINES["columns"], {"title": "Kleur", "field": "colour"}]}}, "Input should be",
                 id="a column for a field that is no DocLine field"),
    pytest.param(NL, {"table": LINES | {"columns": [*LINES["columns"], {"title": "Pos", "field": "net_price"}]}}, "names two columns",
                 id="a title word for two columns"),
    pytest.param(NL, {"table": LINES | {"columns": [*LINES["columns"], {"title": "Netto", "field": "unit_price"}]}}, "has two columns",
                 id="two columns for one field"),
    pytest.param(NL, {"table": LINES | {"basis_mark": "("}}, "basis_mark '\\(' is not a regular expression", id="a basis_mark that does not compile"),
    pytest.param(NL, {"table": LINES | {"line_id_pattern": "("}}, "line_id_pattern '\\(' is not a regular expression",
                 id="a line_id_pattern that does not compile"),
    pytest.param(NL, {"table": LINES | {"item_code_regex": "("}}, "item_code_regex '\\(' is not a regular expression",
                 id="an item_code_regex that does not compile"),
    pytest.param(EN, {"table": TEXT | {"line_regex": "("}}, "line_regex '\\(' is not a regular expression", id="a line_regex that does not compile"),
    pytest.param(EN, {"table": TEXT | {"line_start": "("}}, "line_start '\\(' is not a regular expression", id="a line_start that does not compile"),
    pytest.param(EN, {"table": TEXT | {"line_regex": LINE_REGEX.replace("(?P<line_id>", "(?P<line_no>")}}, "needs a group line_id",
                 id="a line_regex group that is no DocLine field"),
    pytest.param(EN, {"table": TEXT | {"line_regex": LINE_REGEX.replace("(?P<line_id>\\d+)", "\\d+")}}, "needs a group line_id",
                 id="a line_regex with no line_id group"),
    pytest.param(EN, {"table": TEXT | {"basis_mark": "/"}}, "basis_mark: Extra inputs are not permitted", id="a basis_mark in a text table"),
    pytest.param(EN, {"table": TEXT | {"line_id_pattern": "\\d+"}}, "line_id_pattern: Extra inputs are not permitted", id="a line_id_pattern in a text table"),
]


@pytest.mark.parametrize("template,change,message", BAD_TEMPLATES)
def test_what_a_template_may_not_say(template, change, message):
    with pytest.raises(ValidationError, match=message):
        LayoutTemplate.model_validate(template | change)


def test_the_unchanged_templates_are_valid():
    for template in (NL, EN, by_name("table_de").model_dump()):
        assert LayoutTemplate.model_validate(template).name == template["name"]
