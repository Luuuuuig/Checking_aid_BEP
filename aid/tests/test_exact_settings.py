"""Tests of what the block "Koppeling met Exact" of the settings tab shows and writes (aid/review/exact_settings.py),
without a page: the first page of a sample purchase order as a table, the title row found by one of its words, the layout made from
the form and the form made from a layout, the senders, the trial read of a sample (what it reads, what is wrong with it and what the
reader passes by) and the state lines; and of part 2, the export of order lines: the first rows of a sample, its row of column titles,
the three settings made from the form, and the trial read of the sample with them (what it counts, the first and the last order, and
each refusal and problem by a fixed sentence)."""
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aid.execution.by_hand import mark, open_changes
from aid.execution.writer import PreparedWriter
from aid.madeup.generate import (PO_COLUMNS, REGULAR, TRYOUT_EXPORT_COLUMNS, TRYOUT_PO_LAYOUT, Row, Text, load_specs, pdf_bytes, po_pages,
                                 tryout_po_pages)
from aid.pipeline import run_case
from aid.po_store import PoStore
from aid.readers.pdf_text import extract_lines
from aid.readers.po_export import EXPORT_FIELDS, REFUSALS, ExportRefused, read_export
from aid.readers.po_layout import PoLayout
from aid.readers.po_pdf import layout_first_page, read_po_layout
from aid.review.exact_settings import (EXPORT_FORM, FORM, MEANINGS, NO_COLUMN, SLOTS, NotPdf, SampleUnreadable, as_setting, check,
                                       export_from_form, export_headers, export_lines, export_shown, export_text, export_trial, field_label,
                                       form_from_export, form_from_layout, head_headers, header_choices, known, last_export,
                                       layout_from_form, line_headers, page_lines, reading_shown, sample_head, saved_export, senders_from,
                                       state_lines, table_head, title_words, trial, unread)
from aid.review.i18n import STRINGS
from aid.schema import DocLine, DocValue, Document
from aid.settings import Settings
from aid.sources.mailbox import titled_confirmation
from aid.sources.upload import open_upload_case
from aid.tests.conftest import SETTINGS, deps
from aid.tests.helpers import (ANDERS_FORM, ANDERS_LAYOUT, ANDERS_MEANINGS, ANDERS_PO, COMMAS_TITLES, ERP_FORM, ERP_MEANINGS, EXPORT_NL,
                               EXPORT_NL_COLUMNS, EXPORT_NL_FORM, EXPORT_NL_ORDERS, EXPORT_NL_READ, EXPORT_NL_READ_EN, EXPORT_NL_TITLES,
                               MADEUP_LAYOUT, SAMPLES, SORTED_BY_DATE, SORTED_BY_DATE_COLUMNS, SORTED_BY_DATE_LONG, TABS_TITLES, TIE_TITLES,
                               TITLE_COMMAS, WIDE_TITLES, first_page, write_cut_samples, write_sheet, write_title_samples)

SPEC_DIR = Path(__file__).resolve().parents[1] / "madeup" / "spec"
NL, EN = STRINGS["nl"], STRINGS["en"]


def anders(tmp_path: Path, edit=None, name: str = "anders.pdf") -> Path:
    """The second made-up PO, after `edit` has changed the rows of its pages (tryout_po_pages of the generator)."""
    pages = tryout_po_pages()
    if edit is not None:
        edit(pages)
    path = tmp_path / name
    path.write_bytes(pdf_bytes(pages, "edited try-out PO"))
    return path


def madeup_po(tmp_path: Path, edit, name: str = "po.pdf") -> Path:
    """MU01's PO in the made-up layout, after `edit` has changed the rows of its pages: six header rows, the title row and three lines."""
    spec = next(s for s in load_specs(SPEC_DIR) if s.case_id == "MU01")
    pages = po_pages(spec, spec.po.versions[0])
    edit(pages)
    path = tmp_path / name
    path.write_bytes(pdf_bytes(pages, "edited PO"))
    return path


def drawn(tmp_path: Path, rows: list[Row], name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(pdf_bytes([rows], "made-up page"))
    return path


def without_cell(row: Row, title: str) -> None:
    """Take the cell under the title of the made-up layout out of a row of its lines table."""
    row.texts.remove(next(t for t in row.texts if t.x == next(c.x for c in PO_COLUMNS if c.title == title)))


# --- the first page and the title row ---

def test_page_lines_number_as_extract_lines_does_and_a_scan_gives_none(tmp_path, madeup_out):
    for pdf in (ANDERS_PO, madeup_out / "MU01_po_v1.pdf", madeup_out / "MU04_po_v1.pdf"):     # MU04's lines take two pages
        assert page_lines(pdf) == [[str(line.line), line.text] for line in extract_lines(pdf) if line.page == 1], pdf.name
    shown = page_lines(ANDERS_PO)
    assert len(shown) == 19 and shown[2] == ["3", "Proefleverancier S23"] and shown[7][1].startswith("Hoeveelheid Eenh. Artikelcode")
    assert page_lines(ANDERS_PO, limit=5) == shown[:5]
    many = drawn(tmp_path, [Row([Text(0, f"Regel {n}", REGULAR, 5)]) for n in range(1, 66)], "many.pdf")
    assert len(extract_lines(many)) == 65 and page_lines(many) == [[str(n), f"Regel {n}"] for n in range(1, 61)]     # the first 60
    assert page_lines(madeup_out / "MU05_conf.pdf") == [] and page_lines(madeup_out / "MU16_conf.pdf") == []          # the scans


def test_title_words_of_the_two_made_up_layouts(tmp_path, madeup_out):
    po = madeup_out / "MU01_po_v1.pdf"
    assert title_words(po, "Artikel") == MADEUP_LAYOUT.title_row == ["Regel", "Artikel", "Omschrijving", "Aantal", "Eenheid", "Prijs", "Per",
                                                                    "Leverdatum"]
    assert title_words(po, " artikel ") == MADEUP_LAYOUT.title_row                              # case and outer spaces do not count
    assert title_words(ANDERS_PO, "Artikelcode") == ANDERS_LAYOUT.title_row == ANDERS_FORM["title_row"]
    assert title_words(ANDERS_PO, "gewenste LEVERING") == ANDERS_LAYOUT.title_row               # several words, as a phrase
    assert title_words(ANDERS_PO, "Eenh") == ANDERS_LAYOUT.title_row                            # a full stop does not touch a word
    assert title_words(ANDERS_PO, "Artikel") is None                                            # a word of its own: not in "Artikelcode"
    assert title_words(ANDERS_PO, "Bestelling") == ["Bestelling"]                               # the first line that holds it: line 2
    assert title_words(ANDERS_PO, "Drukregelventiel") is None                                   # page 2 is not searched
    assert title_words(ANDERS_PO, "") is None and title_words(ANDERS_PO, "  ") is None
    assert title_words(madeup_out / "MU05_conf.pdf", "Artikel") is None                         # a scan
    long = drawn(tmp_path, [Row([Text(0, "Bestelnummer: X-1")]), Row([Text(0, " ".join(f"K{n}" for n in range(1, 22)))])], "long.pdf")
    assert title_words(long, "K7") == [f"K{n}" for n in range(1, 22)] and len(title_words(long, "K7")) > SLOTS == 20     # the page refuses it


# --- the form and the layout ---

def test_layout_from_form_refuses_each_problem_by_its_sentence():
    def refused(changes: dict | None = None, meanings: list | None = None, language: str = "nl") -> dict:
        layout, problems = layout_from_form(ANDERS_FORM | (changes or {}), ANDERS_MEANINGS if meanings is None else meanings, language)
        assert layout is None
        return problems

    def meanings(*pairs) -> list:
        changed = list(ANDERS_MEANINGS)
        for at, meaning in pairs:
            changed[at] = meaning
        return changed

    assert refused({"po_label": "  "}) == {"po_label": NL["exact_problem:po_label"]}
    assert refused({"po_label": ""}, language="en") == {"po_label": EN["exact_problem:po_label"]}
    assert refused(meanings=meanings((3, None))) == {"columns": NL["exact_problem:meanings"]}                 # a word without a meaning
    assert refused(meanings=ANDERS_MEANINGS[:5]) == {"columns": NL["exact_problem:meanings"]}                  # fewer meanings than words
    assert refused(meanings=meanings((1, "quantity"))) == {"columns": NL["exact_problem:twice"].format(meaning=NL["exact_meaning:quantity"])}
    assert refused(meanings=meanings((1, "quantity")), language="en") == {
        "columns": EN["exact_problem:twice"].format(meaning=EN["exact_meaning:quantity"])}
    assert refused(meanings=meanings((2, "skip"))) == {"columns": NL["exact_problem:item_code"]}
    assert refused(meanings=meanings((0, "skip"))) == {"columns": NL["exact_problem:quantity"]}                # no Regelnummer, no Aantal
    assert refused(meanings=meanings((0, "same"), (1, "quantity"))) == {"columns": NL["exact_problem:first_same"]}
    assert refused({"supplier_label": "Leverancier"}) == {"supplier_line": NL["exact_problem:supplier"]}      # the text and the line
    for line in ("drie", "0", "-1", "3.0", "1e2"):
        assert refused({"supplier_line": line}) == {"supplier_line": NL["exact_problem:supplier_line"]}, line
    assert refused({"title_row": []}, []) == {"title_row": NL["exact_problem:no_title"]}                       # no title row found yet
    assert refused({"title_row": ["Bestelling"]}, ["item_code"]) == {"title_row": NL["exact_problem:short_title"]}
    assert refused({"title_row": [f"K{n}" for n in range(1, 22)]}, ["item_code", "quantity"] + ["skip"] * 19) == {
        "title_row": NL["exact_title_long"]}
    several = refused({"po_label": "", "supplier_label": "Leverancier"}, meanings((1, "quantity"), (2, "skip")))
    assert several == {"po_label": NL["exact_problem:po_label"], "supplier_line": NL["exact_problem:supplier"],
                       "columns": " ".join([NL["exact_problem:twice"].format(meaning=NL["exact_meaning:quantity"]), NL["exact_problem:item_code"]])}
    assert NL["exact_problem:po_label"] == "Vul de tekst voor het inkoopordernummer in."
    assert NL["exact_problem:meanings"] == "Kies bij elk woord van de kopregel wat er in de kolom staat."
    assert NL["exact_problem:twice"] == "Twee kolommen hebben dezelfde betekenis: {meaning}."
    assert NL["exact_problem:item_code"] == "Kies een kolom als Artikelcode." and EN["exact_problem:item_code"] == "Choose one column as Item code."
    assert NL["exact_problem:quantity"] == "Zonder kolom Regelnummer is een kolom Aantal nodig."
    assert NL["exact_problem:supplier"] == "Vul de tekst voor de leverancier in of het regelnummer, niet allebei."
    assert NL["exact_title_long"] == "De kopregel heeft meer dan 20 woorden; dat kan dit formulier niet aan."


def test_a_full_form_gives_the_layout_of_the_second_made_up_po():
    layout, problems = layout_from_form(ANDERS_FORM | {"po_label": " Bestelnummer ", "currency_label": "Valuta "}, ANDERS_MEANINGS, "nl")
    assert problems == {} and layout == ANDERS_LAYOUT == PoLayout.model_validate(TRYOUT_PO_LAYOUT)
    assert as_setting(layout) == TRYOUT_PO_LAYOUT                                  # what a save writes: the keys the layout sets
    # the dropdowns beyond the title row are not read, whatever they hold
    assert layout_from_form(ANDERS_FORM, ANDERS_MEANINGS + ["item_code"] + [None] * (SLOTS - 9), "nl") == (ANDERS_LAYOUT, {})
    point = layout_from_form(ANDERS_FORM | {"numbers": "point", "per_piece": False, "table_end": " Totaal exclusief btw ", "version_label": "Versie",
                                            "supplier_line": "", "supplier_label": "Leverancier"}, ANDERS_MEANINGS, "en")[0]
    assert (point.decimal_comma, point.prices_per_piece, point.table_end, point.version_label) == (False, False, "Totaal exclusief btw", "Versie")
    assert (point.supplier_label, point.supplier_line) == ("Leverancier", None)
    assert list(MEANINGS) == ["line_id", "item_code", "description", "quantity", "unit", "unit_price", "price_basis", "discount_pct", "net_price",
                              "delivery_date", "same", "skip"]


def test_form_from_layout_gives_the_form_back():
    assert form_from_layout(ANDERS_LAYOUT) == ANDERS_FORM | {"title_word": "Artikelcode", "columns": ANDERS_MEANINGS}
    other = MADEUP_LAYOUT.model_copy(update={"supplier_label": None, "supplier_line": 4, "table_end": "Totaal", "decimal_comma": False,
                                             "prices_per_piece": True})
    for layout in (ANDERS_LAYOUT, MADEUP_LAYOUT, other):
        form = form_from_layout(layout)
        assert list(form)[:len(FORM)] == list(FORM)
        assert layout_from_form(form, form["columns"], "nl") == (layout, {})
    assert form_from_layout(MADEUP_LAYOUT)["title_word"] == "Artikel" and form_from_layout(other)["supplier_line"] == "4"
    empty = form_from_layout(None)                                                  # no layout set: an empty form, numbers with a comma
    assert empty == {field: "" for field in FORM} | {"numbers": "comma", "per_piece": False, "title_word": "", "title_row": [], "columns": []}


def test_senders_are_addresses_or_domains():
    assert senders_from("", "nl") == ([], None) and senders_from("  ", "nl") == ([], None) and senders_from(None, "nl") == ([], None)
    assert senders_from("inkoop@example.org", "nl") == (["inkoop@example.org"], None)
    assert senders_from(" inkoop@example.org , example.com,@example.org, ", "nl") == (["inkoop@example.org", "example.com", "@example.org"], None)
    for bad in ("Inkoop <inkoop@example.org>", "inkoop", "inkoop@", "a@b@example.org", "inkoop@example.org; orders@example.org", "example",
                "in koop@example.org"):
        assert senders_from(f"orders@example.org, {bad}", "nl") == (None, NL["exact_problem:sender"].format(text=bad)), bad
    assert senders_from("x, inkoop@example.org, y@", "en") == (None, " ".join([EN["exact_problem:sender"].format(text="x"),
                                                                                 EN["exact_problem:sender"].format(text="y@")]))
    assert NL["exact_problem:sender"] == "Dit is geen e-mailadres en geen domein: {text}."
    assert EN["exact_problem:sender"] == "This is neither an e-mail address nor a domain: {text}."


# --- the trial read ---

def test_check_names_each_problem_of_a_reading(tmp_path, madeup_out):
    assert check(ANDERS_PO, ANDERS_LAYOUT, "nl") == [] and check(madeup_out / "MU01_po_v1.pdf", MADEUP_LAYOUT, "nl") == []
    assert check(madeup_out / "MU05_conf.pdf", MADEUP_LAYOUT, "nl") == [NL["exact_problem:scan"]]                   # a scan
    # the title row of the sample is another, and so is its label
    assert check(madeup_out / "MU01_po_v1.pdf", ANDERS_LAYOUT, "nl") == [NL["exact_problem:title_differs"], NL["exact_problem:no_number"]]
    other_title = ANDERS_LAYOUT.model_copy(update={"title_row": [*ANDERS_LAYOUT.title_row[:-1], "Totaal"]})
    assert check(ANDERS_PO, other_title, "en") == [EN["exact_problem:title_differs"]]
    assert check(ANDERS_PO, ANDERS_LAYOUT.model_copy(update={"po_label": "Ordernummer"}), "nl") == [NL["exact_problem:no_number"]]
    # the label stands on the page, but no word with a digit follows it
    no_number = anders(tmp_path, lambda pages: setattr(pages[0][5].texts[0], "text", "Bestelnummer: volgt"), "no_number.pdf")
    assert check(no_number, ANDERS_LAYOUT, "nl") == [NL["exact_problem:no_number"]]
    # no order line below the title row
    no_lines = anders(tmp_path, lambda pages: (pages[0].__delitem__(slice(8, None)), pages[1].__delitem__(slice(1, None))), "no_lines.pdf")
    assert check(no_lines, ANDERS_LAYOUT, "nl") == [NL["exact_problem:no_lines"]]
    # a line without an item code, and one without a quantity, by their line numbers
    holes = madeup_po(tmp_path, lambda pages: (without_cell(pages[0][7], "Artikel"), without_cell(pages[0][8], "Aantal")), "holes.pdf")
    assert check(holes, MADEUP_LAYOUT, "nl") == [NL["exact_problem:no_item_code"].format(line=1), NL["exact_problem:no_quantity"].format(line=2)]
    assert check(holes, MADEUP_LAYOUT, "en") == [EN["exact_problem:no_item_code"].format(line=1), EN["exact_problem:no_quantity"].format(line=2)]
    # a layout without a quantity column reads no quantity on any line
    no_quantity = ANDERS_LAYOUT.model_copy(update={"columns": ["line_id", *ANDERS_LAYOUT.columns[1:]]})
    assert NL["exact_problem:no_quantity"].format(line=10) in check(ANDERS_PO, no_quantity, "nl")
    # what the store would refuse: two different PO numbers, and a PO number that cannot be the name of a folder
    two = anders(tmp_path, lambda pages: setattr(pages[1][0].texts[0], "text", "Bestelnummer: IB-2600999"), "two.pdf")
    assert check(two, ANDERS_LAYOUT, "nl") == [NL["exact_problem:two_pos"].format(places="regel 6 van de eerste pagina en pagina 2")]
    device = anders(tmp_path, lambda pages: setattr(pages[0][5].texts[0], "text", "Bestelnummer: COM1"), "device.pdf")
    assert check(device, ANDERS_LAYOUT, "nl") == [NL["exact_problem:folder"]]
    for problems in (check(two, ANDERS_LAYOUT, "nl"), check(device, ANDERS_LAYOUT, "nl"), check(holes, MADEUP_LAYOUT, "nl")):
        assert not any(value in " ".join(problems) for value in ("IB-26", "COM1", "KM-", "250101")), problems    # never a value read
    assert NL["exact_problem:title_differs"] == "De kopregel van het voorbeeld is anders dan de kopregel die u hebt ingevuld."
    assert NL["exact_problem:no_number"] == "Geen inkoopordernummer gevonden: controleer de tekst voor het nummer."
    assert NL["exact_problem:no_lines"] == "Geen orderregels gevonden: controleer de kopregel en de kolommen."
    assert (NL["exact_problem:no_item_code"], NL["exact_problem:no_quantity"]) == ("Regel {line}: geen artikelcode gelezen.",
                                                                                   "Regel {line}: geen aantal gelezen.")
    assert NL["exact_problem:scan"] == "Dit pdf-bestand heeft geen tekst (een scan of een afbeelding); de assistent kan het niet lezen."


def test_check_asks_the_reader_itself_last(tmp_path, monkeypatch):
    """A refusal of the reader that check does not know by its own rules still keeps the sample from being saved. The reader is asked
    on the text rows that the trial read already (read_po_layout_rows)."""
    import aid.review.exact_settings as exact

    def refusing(pdf, rows, layout, doc=None):
        assert rows and doc is not None                      # the rows and the reading of the trial, not the PDF once more
        raise ValueError("made-up: a refusal the reader gained later")

    monkeypatch.setattr(exact, "read_po_layout_rows", refusing)
    assert check(ANDERS_PO, ANDERS_LAYOUT, "nl") == [NL["exact_problem:refused"]]


def test_the_trial_reads_the_pdf_once(monkeypatch):
    """Ruling L5: one extraction of the text of the PDF for a whole trial read, the reader's own refusals included."""
    import aid.readers.pdf_text as pdf_text
    import aid.readers.po_pdf as po_pdf
    import aid.review.exact_settings as exact
    calls, real = [], pdf_text.extract_rows

    def counted(pdf, cut_cells=False):
        calls.append(Path(pdf).name)
        return real(pdf, cut_cells)

    for module in (exact, po_pdf):
        monkeypatch.setattr(module, "extract_rows", counted)
    tried = trial(ANDERS_PO, ANDERS_LAYOUT, "nl")
    assert calls == ["ANDERS_po_v1.pdf"] and tried.problems == [] and len(tried.document.lines) == 12
    assert read_po_layout(ANDERS_PO, ANDERS_LAYOUT) == tried.document         # the reader on its own reads the same, with one extraction
    assert calls == ["ANDERS_po_v1.pdf"] * 2


def test_the_reading_shows_normal_forms_printed_texts_and_question_marks(tmp_path):
    doc = read_po_layout(ANDERS_PO, ANDERS_LAYOUT)
    header, lines = reading_shown(doc, "nl", ANDERS_LAYOUT)
    assert header == [["Inkoopordernummer", "IB-2600417"], ["Datum", "2026-11-03 (03-11-2026)"], ["Leverancier", "Proefleverancier S23"],
                      ["Valuta", "EUR"], ["Leveringsconditie", "FCA Voorbeeldstad"]]          # no version: the layout asks for none
    assert line_headers("nl", ANDERS_LAYOUT) == ["Regelnummer", "Aantal", "Eenheid", "Artikelcode", "Omschrijving", "Leverdatum", "Prijs"]
    assert line_headers("en", ANDERS_LAYOUT)[:3] == ["Line number", "Quantity", "Unit"]
    assert len(lines) == 12 and lines[0] == ["1", "10", "pc (st)", "KM-2301", "Kogelkraan DN25 messing", "2026-11-16 (16-11-2026)", "12.5 (12,50)"]
    assert lines[3][1] == "1000 (1.000)" and lines[2][5] == "2026-W48 (week 48)" and lines[4][6] == "1234.5 (1.234,50)"
    assert lines[10][6] == "0.05 (0,05)" and lines[9][6] == "54 (54,00)"         # plain digits, without trailing zeros
    assert lines[1][4] == "Manometer 0-250 bar, glycerine"                         # the second row of the description is no line
    # a value the layout asks for and that was not read is a question mark; one printed and not readable keeps its text in brackets
    broken = doc.model_copy(update={"document_date": DocValue(text="3 nov", uncertain=True), "currency": DocValue(),
                                    "lines": [DocLine(line_id="1", quantity=DocValue(text="tien", uncertain=True),
                                                      item_code=DocValue(value="KM-1", text="KM-1"))]})
    header, lines = reading_shown(broken, "en", ANDERS_LAYOUT)
    assert header[1:4] == [["Date", "? (3 nov)"], ["Supplier", "Proefleverancier S23"], ["Currency", "?"]]
    assert lines == [["1", "? (tien)", "", "KM-1", "", "", ""]]                    # an empty cell is empty
    # without a layout every header field and every field of a line is shown
    header, lines = reading_shown(Document(kind="po", lines=[DocLine(line_id="1")]), "nl")
    assert [what for what, _ in header] == ["Inkoopordernummer", "Versie", "Datum", "Leverancier", "Valuta", "Leveringsconditie"]
    assert lines == [["1"] + [""] * 9] and len(line_headers("nl")) == 10


def test_the_trial_says_whether_the_total_can_be_worked_out(tmp_path, madeup_out):
    assert trial(ANDERS_PO, ANDERS_LAYOUT, "nl").total is True                       # every price per one unit
    assert trial(ANDERS_PO, ANDERS_LAYOUT.model_copy(update={"prices_per_piece": False}), "nl").total is False     # no basis printed
    assert trial(madeup_out / "MU01_po_v1.pdf", MADEUP_LAYOUT, "nl").total is True   # a basis on every line
    no_lines = anders(tmp_path, lambda pages: (pages[0].__delitem__(slice(8, None)), pages[1].__delitem__(slice(1, None))), "no_lines.pdf")
    assert trial(no_lines, ANDERS_LAYOUT, "nl").total is False                        # no line, no total
    scan = trial(madeup_out / "MU05_conf.pdf", MADEUP_LAYOUT, "nl")
    assert (scan.document, scan.problems, scan.warnings, scan.total) == (None, [NL["exact_problem:scan"]], [], False)


# --- what the reader would meet (amendments 2 and 5) ---

def test_a_sample_that_would_be_taken_for_a_confirmation_is_named_by_its_line(tmp_path):
    """With a layout set, a PDF whose first page holds a confirmation keyword on a line above the title row is taken for a
    confirmation (titled_confirmation of aid/sources/mailbox.py). The problem names the number of that line on the page table, never
    its text; a keyword below the title row is no problem."""
    titled = anders(tmp_path, lambda pages: setattr(pages[0][1].texts[0], "text", "Orderbevestiging"), "titled.pdf")
    assert titled_confirmation(layout_first_page(titled), ANDERS_LAYOUT)            # the watched folder would class it so
    assert check(titled, ANDERS_LAYOUT, "nl") == [NL["exact_problem:confirmation"].format(line=2)]
    assert page_lines(titled)[1] == ["2", "Orderbevestiging"]
    assert "Orderbevestiging" not in " ".join(check(titled, ANDERS_LAYOUT, "en"))
    below = anders(tmp_path, lambda pages: setattr(pages[0][-1].texts[0], "text", "Stuur ons uw orderbevestiging"), "below.pdf")
    assert not titled_confirmation(layout_first_page(below), ANDERS_LAYOUT) and check(below, ANDERS_LAYOUT, "nl") == []
    assert NL["exact_problem:confirmation"].startswith("Regel {line} van de eerste pagina, boven de kopregel, bevat een woord van een "
                                                       "orderbevestiging")                # amendment 2 with ruling L4's name of the line


def test_the_reading_names_the_refusals_and_warnings_of_the_reader_by_the_setting(tmp_path):
    """Below the two tables one line per refusal or warning of the reader: two purchase orders in one PDF, a label followed by no word
    with a digit, and a page without the title row, which the reader reads with the columns of an earlier title row, or not at all
    after the end text. Each names the setting or the line, never a value of the PDF."""
    assert trial(ANDERS_PO, ANDERS_LAYOUT, "nl").warnings == [NL["exact_warning:continued"].format(page=2)]
    ended = trial(ANDERS_PO, ANDERS_LAYOUT.model_copy(update={"table_end": "Wordt vervolgd"}), "en")
    assert ended.warnings == [EN["exact_warning:ended"].format(page=2)] and len(ended.document.lines) == 9 and ended.problems == []
    passed = anders(tmp_path, lambda pages: setattr(pages[0][1].texts[0], "text", "Bestelnummer volgt hieronder"), "passed.pdf")
    tried = trial(passed, ANDERS_LAYOUT, "nl")
    assert tried.problems == [] and tried.document.po_reference.value == "IB-2600417"
    assert tried.warnings == [NL["exact_warning:passed"].format(line=2), NL["exact_warning:continued"].format(page=2)]
    two = trial(anders(tmp_path, lambda pages: setattr(pages[1][0].texts[0], "text", "Bestelnummer: IB-2600999"), "two.pdf"), ANDERS_LAYOUT, "nl")
    assert two.problems == [NL["exact_problem:two_pos"].format(places="regel 6 van de eerste pagina en pagina 2")]
    lines = " ".join(tried.warnings + two.problems + ended.warnings)
    assert not any(value in lines for value in ("IB-26", "volgt", "Wordt", "KM-")) and "Bestelnummer" not in lines.replace('"Inkooporder:"', "")
    assert "inkoopordernummer" in NL["exact_warning:passed"] and "kopregel" in NL["exact_warning:continued"]
    assert "purchase order number" in EN["exact_warning:passed"] and "title row" in EN["exact_warning:ended"]


def test_two_numbers_after_the_label_are_named_by_their_lines_and_a_longer_label_reads_one(tmp_path):
    """Ruling L7: the problem names the lines of the first page's table, and the later pages, that hold the different numbers, and says
    that a longer text before the number reads the one line that holds the purchase order number. A title "Bestelling 1/2" above
    "Bestelling: IB-2600417", and "Bestelling 2/2" on page 2: the label "Bestelling" reads three numbers, "Bestelling:" one."""
    def titled(pages):
        pages[0][1].texts[0].text = "Bestelling 1/2"
        pages[0][5].texts[0].text = "Bestelling: IB-2600417"
        pages[1][0].texts[0].text = "Bestelling 2/2"
    pdf = anders(tmp_path, titled, "titled.pdf")
    short = ANDERS_LAYOUT.model_copy(update={"po_label": "Bestelling"})
    assert check(pdf, short, "nl") == [NL["exact_problem:two_pos"].format(places="regels 2 en 6 van de eerste pagina en pagina 2")]
    assert check(pdf, short, "en") == [EN["exact_problem:two_pos"].format(places="lines 2 and 6 of the first page and page 2")]
    longer = trial(pdf, ANDERS_LAYOUT.model_copy(update={"po_label": "Bestelling:"}), "nl")
    assert longer.problems == [] and longer.document.po_reference.value == "IB-2600417"
    three = anders(tmp_path, lambda pages: (titled(pages), pages[1].append(Row([Text(0, "Bestelling 3/3")]))), "three.pdf")
    assert NL["exact_problem:two_pos"].format(places="regels 2 en 6 van de eerste pagina en pagina 2") in check(three, short, "nl")
    assert NL["exact_problem:two_pos"] == (
        "Na de tekst voor het inkoopordernummer staan verschillende nummers, op {places}: zo zou de assistent deze pdf voor meer dan een "
        "inkooporder houden, en hij neemt hem niet op. Staat het inkoopordernummer maar op een van die regels, neem dan een langere tekst "
        "voor het nummer over die alleen daar staat, zoals \"Inkooporder:\".")
    assert EN["exact_problem:two_pos"] == (
        "The text before the purchase order number is followed by different numbers, on {places}: so the assistant would take this PDF "
        "for more than one purchase order, and it does not file it. If only one of those lines holds the purchase order number, copy a "
        "longer text before the number that stands only there, such as \"Inkooporder:\".")


def test_a_line_of_the_first_page_and_an_order_line_are_named_apart(tmp_path):
    """Ruling L4: a line of the first page's table is "Regel n van de eerste pagina"; an order line keeps "Regel n", its own number."""
    def edited(pages):
        without_cell(pages[0][8], "Artikel")                                  # order line 2 without its item code
        pages[0].append(Row([Text(0, "Vermeld de inkooporder op elke factuur.")]))       # line 11 of page 1: the label, no number
    tried = trial(madeup_po(tmp_path, edited, "apart.pdf"), MADEUP_LAYOUT, "nl")
    assert tried.problems == ["Regel 2: geen artikelcode gelezen."]
    assert tried.warnings == ["Regel 11 van de eerste pagina: na de tekst voor het inkoopordernummer staat geen woord met een cijfer; de "
                              "assistent leest daar geen nummer."]
    assert EN["exact_warning:passed"].startswith("Line {line} of the first page: ")
    assert NL["exact_problem:confirmation"] == ("Regel {line} van de eerste pagina, boven de kopregel, bevat een woord van een orderbevestiging; "
                                                "de assistent zou deze pdf voor een orderbevestiging houden.")
    assert EN["exact_problem:confirmation"] == ("Line {line} of the first page, above the title row, holds a word of an order confirmation; "
                                                "the assistant would take this PDF for an order confirmation.")
    assert (NL["exact_problem:no_item_code"], EN["exact_problem:no_quantity"]) == ("Regel {line}: geen artikelcode gelezen.",
                                                                                   "Line {line}: no quantity read.")


# --- numbers (ruling L1) ---

def erp(pdf: Path, numbers: str) -> PoLayout:
    """The layout of the review's ERP report with the number convention chosen."""
    layout, problems = layout_from_form(ERP_FORM | {"numbers": numbers}, ERP_MEANINGS, "nl")
    assert problems == {}, problems
    return layout


def column(pdf: Path, numbers: str, field: str) -> list[str]:
    """One column of the reading of a sample of the review, as the table of order lines shows it."""
    layout = erp(pdf, numbers)
    header, lines = reading_shown(trial(pdf, layout, "nl").document, "nl", layout)
    return [line[line_headers("nl", layout).index(NL[f"exact_meaning:{field}"])] for line in lines]


def test_a_number_reads_as_it_will_be_filed_in_plain_digits_with_the_printed_text_in_brackets():
    """Ruling L1 (a): one is "1 (1.000)", a thousand "1000 (1.000)", twelve and a half "12.5 (12,50)": a number never reads as a
    printed text that means another number."""
    c01, c02, c03 = (SAMPLES / name for name in ("c01_dutch_numbers.pdf", "c02_english_numbers.pdf", "c03_three_decimals.pdf"))
    assert column(c01, "comma", "quantity") == ["1000 (1.000)", "2500 (2.500)", "1000 (1.000)"]          # the right convention
    assert column(c01, "comma", "unit_price") == ["12.5 (12,50)", "38.1 (38,10)", "4.2 (4,20)"]
    assert column(c01, "comma", "price_basis") == ["1", "1", "100"]
    assert column(c01, "point", "quantity") == ["1 (1.000)", "2.5 (2.500)", "1 (1.000)"]                 # the wrong one: as it would be filed
    assert column(c02, "comma", "quantity") == ["1 (1,000)", "2.5 (2,500)", "1 (1,000)"]
    assert column(c02, "point", "quantity") == ["1000 (1,000)", "2500 (2,500)", "1000 (1,000)"]
    assert column(c02, "point", "unit_price") == ["12.5 (12.50)", "38.1 (38.10)", "4.2 (4.20)"]
    assert column(c03, "comma", "quantity") == ["10 (10,000)", "2.5 (2,500)"]


def test_a_number_printed_in_the_other_convention_names_its_column_and_refuses_the_save():
    """Ruling L1 (b): a number printed with exactly two digits after a comma while the convention is the point, or after a point while
    it is the comma, gives a problem that names the column and the convention that fits."""
    c01, c02, c03 = (SAMPLES / name for name in ("c01_dutch_numbers.pdf", "c02_english_numbers.pdf", "c03_three_decimals.pdf"))
    assert check(c01, erp(c01, "point"), "nl") == [NL["exact_problem:convention_comma"].format(column="Prijs")]
    assert check(c02, erp(c02, "comma"), "nl") == [NL["exact_problem:convention_point"].format(column="Prijs")]
    assert check(c02, erp(c02, "comma"), "en") == [EN["exact_problem:convention_point"].format(column="Price")]
    assert check(c01, erp(c01, "comma"), "nl") == check(c02, erp(c02, "point"), "nl") == check(c03, erp(c03, "comma"), "nl") == []
    assert check(ANDERS_PO, ANDERS_LAYOUT.model_copy(update={"decimal_comma": False}), "nl") == [
        NL["exact_problem:convention_comma"].format(column="Prijs")]                   # the Bedrag column is not read
    assert NL["exact_problem:convention_comma"] == ("In de kolom {column} staan getallen met een komma voor de decimalen (zoals 12,50): kies "
                                                    "\"1.234,50 (komma voor de decimalen)\".")
    assert NL["exact_problem:convention_point"] == ("In de kolom {column} staan getallen met een punt voor de decimalen (zoals 12.50): kies "
                                                    "\"1,234.50 (punt voor de decimalen)\".")
    assert EN["exact_problem:convention_comma"] == ("The column {column} holds numbers with a decimal comma (such as 12,50): choose "
                                                    "\"1.234,50 (decimal comma)\".")
    assert EN["exact_problem:convention_point"] == ("The column {column} holds numbers with a decimal point (such as 12.50): choose "
                                                    "\"1,234.50 (decimal point)\".")
    for language in ("nl", "en"):                                              # the choice is named as the radio button names it
        strings = STRINGS[language]
        assert f"\"{strings['exact_numbers:comma']}\"" in strings["exact_problem:convention_comma"]
        assert f"\"{strings['exact_numbers:point']}\"" in strings["exact_problem:convention_point"]


# --- the sample and the helpers it is read with ---

def test_a_pdf_that_cannot_be_opened_raises_an_error_of_its_own(tmp_path):
    """Ruling L11: a PDF cut short (half downloaded) cannot be opened; the block's functions say so with SampleUnreadable, which the
    page names by its own sentence."""
    cut = tmp_path / "cut.pdf"
    cut.write_bytes(ANDERS_PO.read_bytes()[:1500])
    for read in (lambda: page_lines(cut), lambda: title_words(cut, "Artikelcode"), lambda: trial(cut, ANDERS_LAYOUT, "nl"),
                 lambda: check(tmp_path / "gone.pdf", ANDERS_LAYOUT, "nl")):
        with pytest.raises(SampleUnreadable):
            read()
    assert NL["exact_problem:unopenable"] == ("Dit pdf-bestand kan niet worden geopend. Sla het opnieuw op uit de e-mail en kies het nog "
                                              "eens.")
    assert EN["exact_problem:unopenable"] == "This PDF cannot be opened. Save it again from the e-mail and choose it once more."


def test_the_helpers_the_block_uses_have_public_names_and_the_old_names_stay():
    """Ruling L13: the block imports no private name of the reader or the store; the private names stay as aliases of the public ones."""
    import aid.po_store as po_store
    import aid.readers.po_pdf as po_pdf
    import aid.review.exact_settings as exact
    for public, private in (("after_label", "_after_label"), ("folded", "_folded"), ("header_values", "_header_values"),
                            ("label_pattern", "_label_pattern"), ("layout_labels", "_labels"), ("read_layout", "_read_layout")):
        assert getattr(po_pdf, private) is getattr(po_pdf, public), public
    assert po_store._total is po_store.po_total
    source = Path(exact.__file__).read_text(encoding="utf-8")
    assert not re.search(r"from aid\.(readers\.po_pdf|po_store) import \(?[^)]*\b_\w", source)
    assert first_page(ANDERS_PO).splitlines()[5] == "Bestelnummer: IB-2600417 Besteldatum: 03-11-2026"     # the reader reads as before


# --- the state of the connection ---

def test_known_and_unread(tmp_path, madeup_out):
    store = PoStore(tmp_path / "store")
    assert known(store) == (0, None)                                              # no store folder yet
    for name in ("MU01_po_v1.pdf", "MU03_po_v1.pdf", "MU03_po_v2.pdf"):
        store.add(madeup_out / name)
    (store.root / "250199" / "v1").mkdir(parents=True)                           # a version folder without its po.json holds no version
    base = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc).timestamp()
    for folder, seconds in (("250101/v1", 3000), ("250103/v1", 4000), ("250103/v2", 2000)):
        os.utime(store.root / folder / "po.json", (base + seconds, base + seconds))
    assert known(store) == (2, ("250103", 1, datetime.fromtimestamp(base + 4000, timezone.utc)))      # filed last by the time of its po.json
    inbox = tmp_path / "inbox"
    assert unread(inbox, datetime.now(timezone.utc)) == 0                        # no folder yet
    (inbox / "processed").mkdir(parents=True)
    for name in ("a.pdf", "b.eml", ".hidden.pdf", "processed/c.pdf"):
        (inbox / name).write_bytes(b"made-up")
    now = datetime.now(timezone.utc)
    assert unread(inbox, now) == 0 and unread(inbox, now + timedelta(seconds=100)) == 0          # within two passes of the runner
    assert unread(inbox, now + timedelta(seconds=125)) == 2                     # longer: the two files, not a hidden one or processed/
    assert unread(inbox, now + timedelta(seconds=30), older_seconds=10) == 2
    assert unread(inbox, datetime.now() + timedelta(seconds=125)) == 2           # a time without a zone is the PC's own


def test_the_state_lines_hold_counts_and_no_content(tmp_path, madeup_out):
    settings = SETTINGS.model_copy(update={"po_store_root": tmp_path / "store", "watched_folder": tmp_path / "inbox", "cases_root": tmp_path / "cases"})
    store, now = PoStore(settings.po_store_root, settings.authorization_limit), datetime.now(timezone.utc)
    assert state_lines(settings, store, now, "nl") == [NL["exact_state_unset"], NL["exact_state_none"], NL["exact_state_columns_unset"],
                                                       NL["exact_state_no_export"], NL["exact_state_unread"].format(n=0),
                                                       NL["exact_state_waiting"].format(n=0),
                                                       NL["exact_state_to_enter"].format(n=0)]
    version = store.add(madeup_out / "MU08_po_v1.pdf")
    filed = datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc).timestamp()
    os.utime(store.root / "250108" / "v1" / "po.json", (filed, filed))
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "refused.pdf").write_bytes(b"made-up")
    lines = state_lines(settings.model_copy(update={"po_layout": ANDERS_LAYOUT}), store, now + timedelta(seconds=300), "en")
    local = datetime.fromtimestamp(filed, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    assert lines == [EN["exact_state_set"], EN["exact_state_known"].format(count=1, number="250108", version=1, time=local),
                     EN["exact_state_columns_unset"], EN["exact_state_no_export"], EN["exact_state_unread"].format(n=1),
                     EN["exact_state_waiting"].format(n=0),
                     EN["exact_state_to_enter"].format(n=0)]
    text = " ".join(lines)                                                         # no line of a document, no supplier, no amount
    assert version.supplier == "S08" and "S08" not in text and version.total is not None and str(version.total) not in text
    assert not any(line.description.text in text or line.item_code.text in text for line in version.document.lines)
    assert str(SETTINGS.authorization_limit) not in text and "5000" not in text and "refused.pdf" not in text
    assert NL["exact_state_set"] == "Opmaak van de inkooporder: ingesteld."
    assert NL["exact_state_unset"] == ("Opmaak van de inkooporder: nog niet ingesteld. Tot dan leest de assistent alleen de verzonnen "
                                       "voorbeeldinkooporders.")
    assert NL["exact_state_known"] == "Inkooporders die de assistent kent: {count}. Laatst opgenomen: inkooporder {number}, versie {version}, {time}."
    assert NL["exact_state_none"] == "De assistent kent nog geen inkooporders."
    assert NL["exact_state_unread"] == "Bestanden in de bewaakte map die de assistent niet kon lezen: {n}."


def test_the_state_says_how_many_changes_are_still_to_enter_in_exact(tmp_path, madeup_out):
    """Amendment 1(b) of Task 33: Task 34's state line, "Nog in te voeren in Exact: {n}.", is the last of the state lines and counts as
    the tab "In Exact invoeren" counts: the open changes of <cases_root>/prepared (aid/execution/by_hand.py); "?" while that list cannot
    be read. It names no change."""
    prepared = tmp_path / "cases" / "prepared"
    d = deps(tmp_path, madeup_out, writer=PreparedWriter(prepared))
    settings = d.settings.model_copy(update={"po_store_root": tmp_path / "store", "watched_folder": tmp_path / "inbox"})
    now = datetime.now(timezone.utc)
    assert state_lines(settings, d.store, now, "nl")[-1] == "Nog in te voeren in Exact: 0."
    run_case(open_upload_case(settings.cases_root, [madeup_out / "MU08_conf.pdf"], "250108", "confirmation", d.clock()), d)
    assert len(open_changes(prepared)) == 2                                        # the price of line 1 and the article's price
    lines = state_lines(settings, d.store, now, "en")
    assert lines[-1] == "Still to enter in Exact: 2." and len(lines) == 7 and "12,70" not in " ".join(lines) and "12.70" not in " ".join(lines)
    mark(prepared, open_changes(prepared)[0].ids, "entered", datetime(2026, 11, 12, 10, 0))
    assert state_lines(settings, d.store, now, "nl")[-1] == NL["exact_state_to_enter"].format(n=1)
    (prepared / "prepared_changes.csv").unlink()
    (prepared / "prepared_changes.csv").mkdir()                                    # a list that cannot be read
    assert state_lines(settings, d.store, now, "nl")[-1] == NL["exact_state_to_enter"].format(n="?")
    assert (NL["exact_state_to_enter"], EN["exact_state_to_enter"]) == ("Nog in te voeren in Exact: {n}.", "Still to enter in Exact: {n}.")


# --- the texts of the amendments ---

def test_a_label_of_several_words_is_taken_and_the_help_says_so(tmp_path):
    """Amendment 1: a label may be several words, matched as a phrase, and the help of the label fields says so."""
    phrase = anders(tmp_path, lambda pages: setattr(pages[0][5].texts[0], "text", "Bestelling nr. IB-2600417"), "phrase.pdf")
    layout, problems = layout_from_form(ANDERS_FORM | {"po_label": "Bestelling  nr."}, ANDERS_MEANINGS, "nl")
    assert problems == {} and layout.po_label == "Bestelling  nr." and check(phrase, layout, "nl") == []
    assert trial(phrase, layout, "nl").document.po_reference.value == "IB-2600417"
    assert NL["exact_help:po_label"].startswith("Het woord dat op de pdf direct voor het nummer staat. Neem het over uit de regels hierboven.")
    assert EN["exact_help:po_label"].startswith("The word that stands right before the number on the PDF. Copy it from the lines above.")
    for strings in (NL, EN):
        assert "\"Inkooporder nr.\"" in strings["exact_help:po_label"] and "\"Inkooporder nr.\"" in strings["exact_help:label"]
    assert "meer woorden" in NL["exact_help:po_label"] and "samen" in NL["exact_help:label"]
    assert "several words" in EN["exact_help:po_label"] and "together" in EN["exact_help:label"]


def test_the_senders_are_checked_as_the_settings_check_them_and_the_help_advises_to_fill_them_in():
    """Amendment 3: whatever the field takes, the settings take, and the help says why to fill it in."""
    for text in ("inkoop@example.org", "example.org, @example.com", "a.b+c@sub.example.org"):
        senders, problem = senders_from(text, "nl")
        assert problem is None and Settings.model_validate(SETTINGS.model_dump() | {"po_senders": senders}).po_senders == senders, text
    for entry in ("Inkoop <inkoop@example.org>", "example", "inkoop@example"):
        with pytest.raises(ValueError):
            Settings.model_validate(SETTINGS.model_dump() | {"po_senders": [entry]})
        assert senders_from(entry, "nl")[0] is None, entry
    assert NL["exact_field:senders"] == "Afzender van de e-mails met inkooporders uit Exact" and "mag leeg" not in NL["exact_field:senders"]
    assert EN["exact_field:senders"] == "Sender of the e-mails with purchase orders from Exact"
    assert NL["exact_help:senders"] == ("Het e-mailadres waarvan Exact de inkooporders stuurt, of alleen het deel na het apenstaartje; meer dan "
                                        "een met een komma ertussen. Vul dit in: een pdf in deze opmaak van een andere afzender behandelt de "
                                        "assistent dan als orderbevestiging, zodat een bevestiging die op uw inkooporder lijkt nooit als "
                                        "inkooporder wordt opgenomen.")
    assert EN["exact_help:senders"] == ("The e-mail address from which Exact sends the purchase orders, or only the part after the at sign; "
                                        "several with a comma between them. Fill this in: a PDF in this layout from another sender is then "
                                        "handled as an order confirmation, so that a confirmation that looks like your purchase order is never "
                                        "filed as a purchase order.")


def test_the_help_of_the_end_text_names_the_row_under_the_last_line_and_the_page_subtotal():
    """Amendment 4, with the Dutch word written with its diaeresis, as the page writes such words."""
    assert NL["exact_help:table_end"] == ("Neem de hele tekst over van de regel direct onder de laatste orderregel, bijvoorbeeld de totaalregel. "
                                          "Een subtotaal per pagina met dezelfde tekst beëindigt de tabel op die pagina.")
    assert EN["exact_help:table_end"] == ("Copy the whole text of the row right under the last order line, for example the total row. A subtotal "
                                          "per page with the same text ends the table on that page.")
    assert "Eén" in NL["settings_help:digest"]                                     # the page's own way with such a word


def test_the_strings_of_the_block_stand_together():
    """Amendment 7: the block's strings are one block in each language, so that a merge with another task's strings adds both."""
    for strings in (NL, EN):
        keys = list(strings)
        mine = [n for n, key in enumerate(keys) if key.startswith("exact_")]
        assert mine and mine == list(range(mine[0], mine[-1] + 1))


# --- the number recognition of part 1, and the cases that wait for their purchase order (Task 36) ---

from aid.review.close import cases_waiting_for_po  # noqa: E402
from aid.review.exact_settings import pattern_from, pattern_parts  # noqa: E402
from aid.sources.mailbox import find_po_numbers  # noqa: E402
from aid.tests.conftest import deps  # noqa: E402
from aid.tests.helpers import cases_for_the_count  # noqa: E402

DEFAULT = Settings.model_fields["po_number_pattern"].default
DIGITS = {"nl": "een heel getal van 1 tot 20, en het kleinste niet groter dan het grootste",
          "en": "a whole number from 1 to 20, the smallest not above the largest"}


def test_the_pattern_from_words_and_digits_and_back():
    """The pattern the fields write: the words as alternatives without regard to case, then spaces, a colon, a hash, a full stop or a
    dash, then a number of that many digits that no further digit follows; and the words and the digits read back from it. The words
    are typed with a comma between them, the spaces around them are not kept, and a space inside a word stands for any run of white
    space. The default pattern reads back as its five words and six to eight digits."""
    pattern, problems = pattern_from(" Inkooporder, Uw referentie ,Uw  ordernummer,, ", "6", " 8 ", "nl")
    assert problems == {} and pattern == r"(?i:Inkooporder|Uw\s+referentie|Uw\s+ordernummer)[\s:#.-]*(\d{6,8})(?!\d)"
    assert pattern_parts(pattern) == ("Inkooporder, Uw referentie, Uw ordernummer", 6, 8)
    for text, numbers in (("Uw referentie: 250101", ["250101"]), ("UW ORDERNUMMER #250102", ["250102"]), ("Inkooporder. 25010123", ["25010123"]),
                          ("Uw referentie\n250103", ["250103"]), ("uw  referentie - 250104,", ["250104"]), ("Inkooporder 12345", []),
                          ("Inkooporder 250101 en uw ordernummer 250102", ["250101", "250102"]), ("Order 250101", [])):
        assert find_po_numbers(text, pattern) == numbers, text
    marked, problems = pattern_from("Order-Nr., Best.nr (intern)", "4", "12", "en")
    assert problems == {} and pattern_parts(marked) == ("Order-Nr., Best.nr (intern)", 4, 12)
    assert find_po_numbers("Best.nr (intern): 4711", marked) == ["4711"] and find_po_numbers("Order-Nr. 4711", marked) == ["4711"]
    assert pattern_parts(DEFAULT) == ("PO, Inkooporder, Bestelling, Bestellung, Order", 6, 8)
    assert pattern_from("PO, Inkooporder, Bestelling, Bestellung, Order", "6", "8", "nl") == (
        r"(?i:PO|Inkooporder|Bestelling|Bestellung|Order)[\s:#.-]*(\d{6,8})(?!\d)", {})
    assert pattern_from("Inkooporder", "1", "20", "nl")[1] == {} and pattern_parts(pattern_from("Inkooporder", "7", "7", "nl")[0]) == ("Inkooporder", 7, 7)
    # refused, with the problem of each field in the page's language: no word, a number of digits that is no whole number from 1 to 20,
    # the fewest above the most
    assert pattern_from(" , ", "6", "8", "nl") == (None, {"words": "vul minstens een woord in"})
    for least, most in (("", "8"), ("6", ""), ("x", "8"), ("6.5", "8"), ("0", "8"), ("6", "21"), ("9", "8"), ("-6", "8"), ("６", "8")):
        assert pattern_from("Inkooporder", least, most, "nl") == (None, {"digits": DIGITS["nl"]}), (least, most)
    assert pattern_from("", "0", "0", "en") == (None, {"words": "fill in at least one word", "digits": DIGITS["en"]})


def test_a_number_of_ten_digits_is_not_cut_to_eight():
    """Rulings N7 and N26: the default does not cut a ten-digit number to eight digits, which would name another purchase order:
    find_po_numbers passes over a number that a digit follows at the end of the pattern's group, so the default, as the pattern of the
    fields, takes a longer number whole or not at all."""
    text = "Uw inkooporder 1234567890"
    assert find_po_numbers(text, DEFAULT) == [] and find_po_numbers("Uw inkooporder 12345678", DEFAULT) == ["12345678"]
    eight, ten = pattern_from("Inkooporder", "6", "8", "nl")[0], pattern_from("Inkooporder", "6", "10", "nl")[0]
    assert find_po_numbers(text, eight) == [] and find_po_numbers(text, ten) == ["1234567890"]
    assert find_po_numbers("Inkooporder 123456789012", ten) == [] and find_po_numbers("Inkooporder 250101/2", eight) == ["250101"]


def test_a_pattern_of_another_form_is_left_alone():
    """pattern_parts reads the words and the digits of a pattern of the form that the fields write, and of the form of the default; it
    gives None for any other pattern, which the block shows as an own pattern in the settings file and leaves alone while its fields are
    empty (aid/tests/test_settings_tab.py)."""
    for pattern in (r"REF-(\d{6})", r"(?i:PO)[\s:#-]*(\d{6})", r"(?:PO|Order)[\s:#-]*(\d{6,8})", r"(?i:P\.?O)[\s:#.-]*(\d{6,8})(?!\d)",
                    r"(?i:PO|)[\s:#.-]*(\d{6,8})(?!\d)", r"(?i:PO,Order)[\s:#.-]*(\d{6,8})(?!\d)", r"(?i:PO)[\s:#.-]*(\d{6,8})(?!\d)x",
                    r"(?i:PO)[\s:#.-]*(\d{0,8})(?!\d)", r"(?i:PO)[\s:#.-]*(\d{6,25})(?!\d)", r"(?i:PO)[\s:#.-]*(\d{9,8})(?!\d)",
                    r"(?i:Uw\ ref)[\s:#.-]*(\d{6,8})(?!\d)", r"(?i:PO\s+)[\s:#.-]*(\d{6,8})(?!\d)", r"(?i:PO)[\s:#-]*(\d{6,8})(?!\d)",
                    r"(?i:PO)[\s:#.-]*(\d{6,8})", r"(?i:PO\)[\s:#.-]*(\d{6,8})(?!\d)", ""):
        assert pattern_parts(pattern) is None, pattern
    assert pattern_parts(r"(?i:PO)[\s:#.-]*(\d{6,8})(?!\d)") == ("PO", 6, 8)
    assert (NL["exact_po_number_own"], EN["exact_po_number_own"]) == ("eigen patroon (in het instellingenbestand)", "own pattern (in the settings file)")


def test_the_state_counts_the_cases_that_wait_for_their_purchase_order(tmp_path, madeup_out):
    """Rule 10 and ruling N10: the block's state names how many cases wait for their purchase order (cases_waiting_for_po of
    aid/review/close.py), and nothing of them. Of seven confirmations only the two that wait count: not a closed one, a replaced one,
    a decided one, one in review for another reason, nor one opened without a PO number."""
    settings = SETTINGS.model_copy(update={"po_store_root": tmp_path / "store", "watched_folder": tmp_path / "inbox", "cases_root": tmp_path / "cases"})
    store, now = PoStore(settings.po_store_root), datetime.now(timezone.utc)
    assert state_lines(settings, store, now, "nl")[-2] == NL["exact_state_waiting"].format(n=0) == "Zaken die op hun inkooporder wachten: 0."
    waiting = cases_for_the_count(settings.cases_root, madeup_out, deps(tmp_path / "full", madeup_out))
    assert len([path for path in settings.cases_root.iterdir() if (path / "case.json").exists()]) == 7
    assert [case.case_id for case, _ in cases_waiting_for_po(settings.cases_root)] == waiting
    assert state_lines(settings, store, now, "en")[-2] == EN["exact_state_waiting"].format(n=2) == "Cases that wait for their purchase order: 2."


# --- part 2: the export of order lines set from one sample (Task 39) ---

def test_table_head_and_header_choices_of_a_csv_and_a_workbook_with_title_rows(tmp_path):
    """The first rows of a sample, each with its row number as a spreadsheet numbers it (a blank row counts), cut as the export reader
    cuts the file; and the row with the column titles: the one given, else the first row with three filled cells, with its titles."""
    head = table_head(EXPORT_NL)
    assert len(head) == 9 and all(len(row) == 11 for row in head)                    # its nine rows, each its number and ten cells
    assert head[0] == ["1", "Openstaande inkooporderregels (verzonnen voorbeeld)", *[""] * 9] and head[1] == ["2", *[""] * 10]
    assert head[2] == ["3", *EXPORT_NL_TITLES]
    assert head[3] == ["4", "250301", "S21", "KM-3101", "Slangklem 16-27 mm", "1.000", "st", "12,50", "100", "24-11-2026", "125,00"]
    assert head[-1][:2] == ["9", "Totaal"] and table_head(EXPORT_NL, rows=3) == head[:3]
    assert head_headers(head, "nl") == ["Rij", *"ABCDEFGHIJ"] and head_headers(head, "en") == ["Row", *"ABCDEFGHIJ"]
    assert header_choices(EXPORT_NL) == (3, EXPORT_NL_TITLES)                       # the title row and the blank row above it passed by
    assert header_choices(EXPORT_NL, 1) == (1, ["Openstaande inkooporderregels (verzonnen voorbeeld)"])
    assert header_choices(EXPORT_NL, 2) == (2, []) and header_choices(EXPORT_NL, 40) == (40, [])
    # a workbook: a title row, a row of two filled cells and a blank row above the column titles; a number or a date is its text
    book = write_sheet(tmp_path / "export.xlsx", [
        ["Openstaande inkooporderregels"], ["Afdrukdatum", "10-11-2026"], [],
        [" Inkooporder ", "Artikel", "aantal", "Prijs", "ARTIKEL", "Leverdatum", None, "Prijs"],
        ["250301", "KM-3101", 1000, 12.5, None, datetime(2026, 11, 24)]])
    assert table_head(book) == [["1", "Openstaande inkooporderregels", *[""] * 7], ["2", "Afdrukdatum", "10-11-2026", *[""] * 6],
                                ["3", *[""] * 8], ["4", " Inkooporder ", "Artikel", "aantal", "Prijs", "ARTIKEL", "Leverdatum", "", "Prijs"],
                                ["5", "250301", "KM-3101", "1000", "12.5", "", "2026-11-24", "", ""]]
    # the titles without the spaces around them, each once as the reader compares them (case not counting), an empty cell no title
    assert header_choices(book) == (4, ["Inkooporder", "Artikel", "aantal", "Prijs", "Leverdatum"])
    assert head_headers(table_head(book), "nl") == ["Rij", *"ABCDEFGH"] and head_headers([], "nl") == ["Rij"]
    # a CSV file is cut at the mark under which the whole file holds the most filled cells (ruling M21): semicolons before commas, also
    # when a description holds commas; a file of two columns at the mark that cuts a row into two
    commas = tmp_path / "commas.csv"
    commas.write_text("Inkooporder,Artikel,Aantal\n250301,KM-3101,5\n", encoding="utf-8")
    assert header_choices(commas) == (1, ["Inkooporder", "Artikel", "Aantal"]) and table_head(commas)[1] == ["2", "250301", "KM-3101", "5"]
    tabs = tmp_path / "tabs.csv"
    tabs.write_text("Overzicht\nInkooporder\tArtikel\tAantal\n250301\tKM-3101\t5\n", encoding="utf-8")
    assert header_choices(tabs) == (2, ["Inkooporder", "Artikel", "Aantal"])
    described = tmp_path / "described.csv"
    described.write_text("Inkooporder;Artikel;Omschrijving\n250301;KM-3101;Bout M8, verzinkt, 8.8, DIN 933\n", encoding="utf-8")
    assert header_choices(described) == (1, ["Inkooporder", "Artikel", "Omschrijving"])
    assert table_head(described)[1] == ["2", "250301", "KM-3101", "Bout M8, verzinkt, 8.8, DIN 933"]
    two = tmp_path / "two.csv"
    two.write_text("Inkooporder,Artikel\n250301,KM-3101\n", encoding="utf-8")
    assert header_choices(two) == (1, ["Inkooporder", "Artikel"])
    (tmp_path / "empty.csv").write_bytes(b"")
    assert table_head(tmp_path / "empty.csv") == [] and header_choices(tmp_path / "empty.csv") == (1, [])


def test_export_from_form_refuses_each_problem_by_its_sentence():
    def refused(changes: dict, language: str = "nl") -> dict:
        settings, problems = export_from_form(EXPORT_NL_FORM | changes, language)
        assert settings is None
        return problems

    assert refused({"po_number": NO_COLUMN}) == {"po_number": NL["exact_export_problem:po_number"]}
    assert refused({"item_code": NO_COLUMN}, "en") == {"item_code": EN["exact_export_problem:item_code"]}
    assert refused({"po_number": None, "item_code": " "}) == {"po_number": NL["exact_export_problem:po_number"],
                                                             "item_code": NL["exact_export_problem:item_code"]}
    # a column chosen for more than one field, named once by the field that chose it the second time (ruling M10); titles compared as the
    # reader compares them
    again = "exact_export_problem:more_than_once"
    assert refused({"net_price": "Prijs"}) == {"net_price": NL[again].format(column="\"Prijs\"")}
    assert refused({"net_price": " PRIJS "}, "en") == {"net_price": EN[again].format(column="\"Prijs\"")}
    assert refused({"discount_pct": "Prijs", "net_price": "Prijs"}) == {"discount_pct": NL[again].format(column="\"Prijs\"")}    # chosen three times
    assert refused({"po_number": NO_COLUMN, "description": "Artikel"}) == {
        "po_number": NL["exact_export_problem:po_number"], "description": NL[again].format(column="\"Artikel\"")}
    assert NL["exact_export_problem:po_number"] == "Kies de kolom met het inkoopordernummer."
    assert NL["exact_export_problem:item_code"] == "Kies de kolom met de artikelcode."
    assert NL[again] == "De kolom {column} is meer dan een keer gekozen."
    assert EN["exact_export_problem:po_number"] == "Choose the column with the purchase order number."
    assert EN["exact_export_problem:item_code"] == "Choose the column with the item code."
    assert EN[again] == "The column {column} was chosen more than once."
    assert NL["exact_problem:no_sample"] == "Kies eerst een voorbeeld." and EN["exact_problem:no_sample"] == "Choose a sample first."


def test_a_full_form_gives_the_three_settings():
    settings, problems = export_from_form(EXPORT_NL_FORM, "nl")
    assert problems == {} and settings == {"export_columns": EXPORT_NL_COLUMNS, "export_decimal_comma": True, "export_fill_down": True}
    assert list(settings["export_columns"]) == list(EXPORT_NL_COLUMNS)              # the fields in the order of the form
    assert settings["export_columns"] == TRYOUT_EXPORT_COLUMNS                        # the map that reads the made-up export
    assert Settings.model_validate(SETTINGS.model_dump() | settings).export_columns == EXPORT_NL_COLUMNS
    plain, problems = export_from_form(EXPORT_NL_FORM | {"line_id": " Regel ", "numbers": "point", "fill_down": False}, "en")
    assert problems == {} and plain["export_columns"]["line_id"] == "Regel" and list(plain["export_columns"])[:3] == ["po_number", "line_id",
                                                                                                                      "item_code"]
    assert (plain["export_decimal_comma"], plain["export_fill_down"]) == (False, False)
    assert EXPORT_FORM == EXPORT_FIELDS and NO_COLUMN == "(none)"
    # the form back from the settings: the title of each field, the choice for no column, the convention and the tick
    form = form_from_export(SETTINGS.model_copy(update=settings))
    assert form == EXPORT_NL_FORM and list(form) == [*EXPORT_FORM, "numbers", "fill_down"]
    assert form_from_export(SETTINGS) == dict.fromkeys(EXPORT_FORM, NO_COLUMN) | {"numbers": "point", "fill_down": False}
    assert saved_export(SETTINGS.model_copy(update=settings)) == settings
    assert [field_label(field, "nl") for field in EXPORT_FORM] == [
        "Inkoopordernummer (verplicht)", "Regelnummer", "Artikelcode (verplicht)", "Omschrijving", "Aantal", "Eenheid", "Prijs",
        "Prijs per (aantal)", "Korting in procenten", "Nettoprijs", "Leverdatum", "Leverancier"]
    assert [field_label(field, "en") for field in EXPORT_FORM] == [
        "Purchase order number (required)", "Line number", "Item code (required)", "Description", "Quantity", "Unit", "Price",
        "Price per (number)", "Discount in percent", "Net price", "Delivery date", "Supplier"]


def test_export_shown_counts_and_shows_the_first_order(tmp_path):
    values = export_from_form(EXPORT_NL_FORM, "nl")[0]
    text, lines = export_shown(EXPORT_NL, values, "nl")                       # the first and the last order (ruling M2), "1 rij" (M9)
    assert (text, lines) == (EXPORT_NL_READ, EXPORT_NL_ORDERS) and export_shown(EXPORT_NL, values, "en")[0] == EXPORT_NL_READ_EN
    assert export_headers(values, "nl") == ["Inkoopordernummer", "Regelnummer", "Artikelcode", "Omschrijving", "Aantal", "Eenheid", "Prijs",
                                            "Prijs per (aantal)", "Leverdatum", "Leverancier"]
    assert export_headers(values, "en")[:3] == ["Purchase order number", "Line number", "Item code"]
    # the cells that were not read: per field how many, with the first row of each, named by the column's title
    unread_cells = write_sheet(tmp_path / "unread.csv", [
        ["Inkooporder", "Artikel", "Aantal", "Prijs", "Leverdatum"], ["250301", "KM-3101", "tien", "12,50", "24-11-2026"],
        ["250301", "KM-3102", "5", "4,80", "morgen"], ["250301", "KM-3103", "drie", "38,75", "01-12-2026"], ["Totaal", "", "", "", ""]])
    columns = {"po_number": "Inkooporder", "item_code": "Artikel", "quantity": "Aantal", "unit_price": "Prijs", "delivery_date": "Leverdatum"}
    plain = {"export_columns": columns, "export_decimal_comma": True, "export_fill_down": False}
    text, lines = export_shown(unread_cells, plain, "nl")
    assert text.split("\n\n") == ["Gelezen: 1 inkooporder met 3 regels; 1 rij zonder artikelcode overgeslagen. Hieronder de eerste "
                                  "inkooporder: vergelijk haar met Exact.",
                                  "Niet gelezen: 2 waarden in de kolom \"Aantal\" (eerste: rij 2). Die waarden vergelijkt de assistent niet.",
                                  "Niet gelezen: 1 waarde in de kolom \"Leverdatum\" (rij 3). Die waarde vergelijkt de assistent niet."]
    assert NL["exact_export_unread"] == ("Niet gelezen: {count} waarden in de kolom {column} (eerste: rij {row}). Die waarden vergelijkt de "
                                         "assistent niet.")
    assert EN["exact_export_unread"] == ("Not read: {count} values in the column {column} (first: row {row}). The assistant does not compare "
                                         "those values.")
    assert lines == [["250301", "1", "KM-3101", "? (tien)", "12.5 (12,50)", "2026-11-24 (24-11-2026)"],
                     ["250301", "2", "KM-3102", "5", "4.8 (4,80)", "? (morgen)"], ["250301", "3", "KM-3103", "? (drie)", "38.75 (38,75)",
                                                                                     "2026-12-01 (01-12-2026)"]]
    assert export_headers(plain, "nl") == ["Inkoopordernummer", "Regelnummer", "Artikelcode", "Aantal", "Prijs", "Leverdatum"]
    # a line number column: the lines keep the numbers of the file
    numbered = write_sheet(tmp_path / "numbered.csv", [["Inkooporder", "Regel", "Artikel"], ["250301", "10", "KM-3101"], ["250301", "20", "KM-3102"],
                                                       ["250302", "10", "KM-3201"]])
    with_lines = {"export_columns": {"po_number": "Inkooporder", "line_id": "Regel", "item_code": "Artikel"}, "export_decimal_comma": False,
                  "export_fill_down": False}
    assert export_shown(numbered, with_lines, "nl") == (
        "Gelezen: 2 inkooporders met samen 3 regels; 0 rijen zonder artikelcode overgeslagen. Hieronder de eerste en de laatste "
        "inkooporder: vergelijk ze met Exact.", [["250301", "10", "KM-3101"], ["250301", "20", "KM-3102"], ["250302", "10", "KM-3201"]])
    # a sample that gives no order line is a problem of its own: there is no first order to compare
    empty = write_sheet(tmp_path / "empty.csv", [["Inkooporder", "Artikel", "Omschrijving"], ["250301", "", "Slangklem"],
                                                 ["250302", "", "Kogelkraan"]])
    tried = export_trial(empty, {"export_columns": {"po_number": "Inkooporder", "item_code": "Artikel"}, "export_decimal_comma": False,
                                 "export_fill_down": False}, "nl")
    assert tried.problems == [NL["exact_export_problem:no_lines"]] and export_lines(tried, {"export_columns": {}}, "nl") == []
    least = {"export_columns": {"po_number": "Inkooporder", "item_code": "Artikel"}}
    assert export_shown(empty, least, "nl") == (NL["exact_export_problem:no_lines"], [])


def test_a_refusal_of_the_reader_is_a_fixed_sentence_without_content(tmp_path):
    values = export_from_form(EXPORT_NL_FORM | {"fill_down": False}, "nl")[0]
    text, lines = export_shown(EXPORT_NL, values, "nl")                              # the PO number stands on the first row of each only
    assert (text, lines) == (NL["exact_export_refused:no_po_number"].format(row=5), [])
    assert text == ("Rij 5: een artikelcode zonder inkoopordernummer. Staat het nummer alleen op de eerste regel van elke inkooporder, vink "
                    "dat dan aan.")
    assert export_shown(EXPORT_NL, values, "en")[0] == ("Row 5: an item code without a purchase order number. If the number stands on the "
                                                        "first line of each purchase order only, tick that.")
    lacking = values | {"export_columns": values["export_columns"] | {"net_price": "Netto"}, "export_fill_down": True}
    assert export_shown(EXPORT_NL, lacking, "nl") == ("In dit bestand ontbreekt de kolom \"Netto\". Kies bij dat veld een kolom uit dit "
                                                      "bestand, of \"(staat niet in het bestand)\".", [])          # ruling M7
    assert EN["exact_export_refused:no_column"] == ("This file lacks the column {column}. Choose a column of this file for that field, or "
                                                    "\"(not in the file)\".")
    assert NL["exact_export_refused:unreadable"] == ("De assistent kan dit bestand niet lezen: het is beschadigd, of een cel bevat een waarde "
                                                     "die niet bij het soort cel past. Open het in Excel, sla het opnieuw op en kies het nog "
                                                     "eens.")                                  # ruling M7; its English stays
    assert EN["exact_export_refused:unreadable"] == ("The assistant cannot read this file: it is damaged, or a cell holds what its type does "
                                                     "not allow. Open it in Excel, save it again and choose it once more.")
    total = write_sheet(tmp_path / "total.csv", [["Inkooporder", "Artikel", "Aantal"], ["250301", "KM-3101", "5"], ["Totaal", "TOTAAL", "5"]])
    shown = export_shown(total, {"export_columns": {"po_number": "Inkooporder", "item_code": "Artikel"}}, "nl")[0]
    assert shown == NL["exact_export_refused:po_number_no_digit"].format(row=3)
    # a file of another type: the sentence of the brief, also when the sample is chosen
    old = tmp_path / "export.xls"
    old.write_bytes(EXPORT_NL.read_bytes())
    assert export_shown(old, lacking, "nl") == (NL["exact_export_refused:file_type"], [])
    assert NL["exact_export_refused:file_type"] == ("Dit bestand is geen Excel- of csv-bestand. Een oud Excel-bestand (.xls) slaat u eerst in "
                                                    "Excel op als .xlsx.")
    assert EN["exact_export_refused:file_type"] == "This file is no Excel or CSV file. Save an old Excel file (.xls) as .xlsx in Excel first."
    for read in (lambda: table_head(old), lambda: header_choices(old)):
        with pytest.raises(ExportRefused) as refused:
            read()
        assert refused.value.reason == "file_type"
    # a damaged workbook and a CSV file that no mark of the reader cuts are refused when they are chosen, by the reader's reasons
    (tmp_path / "damaged.xlsx").write_bytes(b"no workbook")
    (tmp_path / "pipes.csv").write_text("Inkooporder|Artikel|Aantal\n250301|KM-3101|5\n", encoding="utf-8")
    for name, reason in (("damaged.xlsx", "unreadable"), ("pipes.csv", "delimiter_or_encoding")):
        with pytest.raises(ExportRefused) as refused:
            table_head(tmp_path / name)
        assert refused.value.reason == reason, name
    shown = " ".join(export_shown(path, values, language)[0] for path in (EXPORT_NL, total, old) for language in ("nl", "en"))
    assert not any(content in shown for content in ("250301", "KM-", "Totaal", "TOTAAL", "Slangklem", "S21")), shown


def test_every_reason_the_reader_refuses_a_file_for_has_a_sentence_of_its_own(tmp_path):
    """Amendment 1: each reason of the export reader's REFUSALS is shown by a sentence of its own in both languages, chosen by the
    reason: it names the column or the row where the reader gives them, and never what a cell holds. Every refusal of the reader's own
    tests is met here."""
    from aid.tests.test_po_export import NL as MAP
    from aid.tests.test_po_export import REFUSED, REFUSED_FILES, texts, write_csv, write_xlsx
    rows_named = {"no_po_number", "po_number_date", "po_number_no_digit", "po_number_no_folder", "no_line_id", "line_id_date", "double_line_id",
                  "double_column", "surplus_cells", "cell_count"}
    for strings in (NL, EN):
        sentences = [strings[f"exact_export_refused:{reason}"] for reason in REFUSALS]
        assert len(set(sentences)) == len(REFUSALS)                                  # a sentence of its own for each reason
        for reason in REFUSALS:
            sentence = strings[f"exact_export_refused:{reason}"]
            assert ("{row}" in sentence) == (reason in rows_named), reason
            assert ("{column}" in sentence) == (reason in ("no_column", "double_column")), reason
            assert ("{names}" in sentence) == (reason == "several_sheets"), reason
        assert "pricelist_refused:sheets" not in strings        # one sentence, under one key, wherever a workbook is given
        assert "{row}" in strings["exact_export_refused:unreadable_row"]              # a workbook cell that cannot be read, by its row
    for language in ("nl", "en"):
        strings = STRINGS[language]
        for n, (case, (name, rows, columns, options, _, (reason, column, row))) in enumerate(REFUSED.items()):
            path = (write_xlsx if name.endswith(".xlsx") else write_csv)(tmp_path / f"{language}{n}" / name, rows)
            values = {"export_columns": columns, "export_decimal_comma": False, "export_fill_down": options.get("fill_down", False)}
            tried = export_trial(path, values, language)
            sentence = strings[f"exact_export_refused:{reason}"].format(column=f"\"{column}\"", row=row)
            assert tried.reading is None and tried.problems == [sentence], case
            assert not [text for text in texts(rows[1:]) if text in tried.problems[0]], case
        for n, (case, (name, write, _, (reason, row), hidden)) in enumerate(REFUSED_FILES.items()):
            path = tmp_path / f"{language}-file{n}" / name
            path.parent.mkdir()
            write(path)
            values = {"export_columns": MAP, "export_decimal_comma": False, "export_fill_down": False}
            tried = export_trial(path, values, language)
            key = f"exact_export_refused:{reason}" + ("_row" if row is not None else "")
            assert tried.reading is None and tried.problems == [strings[key].format(row=row, names="Open, Geleverd")], case
            if reason != "several_sheets":                      # that sentence names the sheets, and nothing else of the file
                assert not [text for text in hidden if text in tried.problems[0]], case
            assert "Overzicht" not in tried.problems[0], case


def test_a_text_column_in_the_other_convention_is_a_problem_and_a_number_cell_never_is(tmp_path):
    """Amendment 2 (ruling L1 of part 1): the first order shows its numbers in plain digits with the cell's text in brackets, so the
    wrong convention shows ("1 (1.000)"); and a text column of numbers with the other convention's decimal mark followed by one, two,
    four or more digits (ruling M6; exactly three digits prove nothing) is a problem that names the column and the choice that fits.
    A number cell of a workbook is read as a number whatever the convention says, so it never gives that problem."""
    point = export_from_form(EXPORT_NL_FORM | {"numbers": "point"}, "nl")[0]
    tried = export_trial(EXPORT_NL, point, "nl")
    assert tried.problems == [NL["exact_problem:convention_comma"].format(column="\"Prijs\"")]      # "1.000" alone tells nothing
    assert [line[4:7] for line in export_lines(tried, point, "nl")][:3] == [["1 (1.000)", "pc (st)", "12.5 (12,50)"], ["25", "m", "4.8 (4,80)"],
                                                                            ["4", "pc (st)", "38.75 (38,75)"]]
    assert export_shown(EXPORT_NL, point, "nl")[0].split("\n\n") == [EXPORT_NL_READ, NL["exact_problem:convention_comma"].format(column="\"Prijs\"")]
    assert export_trial(EXPORT_NL, export_from_form(EXPORT_NL_FORM, "nl")[0], "nl").problems == []
    english = write_sheet(tmp_path / "point.csv", [["Inkooporder", "Artikel", "Aantal", "Prijs", "Korting"],
                                                   ["250301", "KM-3101", "1,000", "12.50", "5"], ["250301", "KM-3102", "2", "4.80", "2.5"]])
    columns = {"po_number": "Inkooporder", "item_code": "Artikel", "quantity": "Aantal", "unit_price": "Prijs", "discount_pct": "Korting"}
    comma = {"export_columns": columns, "export_decimal_comma": True, "export_fill_down": False}
    assert export_trial(english, comma, "en").problems == [EN["exact_problem:convention_point"].format(column="\"Prijs\""),
                                                           EN["exact_problem:convention_point"].format(column="\"Korting\"")]    # "2.5" too (M6)
    assert export_trial(english, comma | {"export_decimal_comma": False}, "nl").problems == []
    # number cells of a workbook: never; a text cell of a workbook: as in a CSV file
    numbers = {"export_columns": {"po_number": "Inkooporder", "item_code": "Artikel", "quantity": "Aantal", "unit_price": "Prijs"},
               "export_decimal_comma": True, "export_fill_down": False}
    book = write_sheet(tmp_path / "book.xlsx", [["Inkooporder", "Artikel", "Aantal", "Prijs"], ["250301", "KM-3101", 1000, 1234.56],
                                                ["250301", "KM-3102", 2, 12.5]])
    for decimal_comma in (True, False):
        tried = export_trial(book, numbers | {"export_decimal_comma": decimal_comma}, "nl")
        assert tried.problems == [] and export_lines(tried, numbers, "nl")[0][3:] == ["1000", "1234.56"]
    texts = write_sheet(tmp_path / "texts.xlsx", [["Inkooporder", "Artikel", "Aantal", "Prijs"], ["250301", "KM-3101", 1000, "12.50"]])
    assert export_trial(texts, numbers, "nl").problems == [NL["exact_problem:convention_point"].format(column="\"Prijs\"")]


def test_the_state_lines_say_whether_the_columns_are_set_and_when_the_last_export_was_filed(tmp_path):
    """Amendment 3: the state lines gain whether the columns of the export are set and when the last export was filed, from the
    store's looks (the time of the newest look's po.json, filed_at), in the local time of this PC; never a PO, a supplier or a cell. The
    count of the cases that wait for their purchase order (Task 36) stays the last line."""
    settings = SETTINGS.model_copy(update={"po_store_root": tmp_path / "store", "watched_folder": tmp_path / "inbox", "cases_root": tmp_path / "cases"})
    store, now = PoStore(settings.po_store_root), datetime.now(timezone.utc)
    assert last_export(store) is None
    assert state_lines(settings, store, now, "nl")[2:4] == [NL["exact_state_columns_unset"], NL["exact_state_no_export"]]
    values = export_from_form(EXPORT_NL_FORM, "nl")[0]
    store.from_export(EXPORT_NL, values["export_columns"], True, True)
    filed = datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc).timestamp()
    os.utime(store.root / "250301" / "e1" / "po.json", (filed, filed))
    os.utime(store.root / "250302" / "e1" / "po.json", (filed - 600, filed - 600))
    assert last_export(store) == store.filed_at("250301", "e1") == datetime.fromtimestamp(filed).astimezone()
    lines = state_lines(settings.model_copy(update=values), store, now, "en")
    local = datetime.fromtimestamp(filed, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    assert lines == [EN["exact_state_unset"], EN["exact_state_known_export"].format(count=2, number="250301", time=local),     # ruling M8
                     EN["exact_state_columns_set"], EN["exact_state_export"].format(time=local), EN["exact_state_unread"].format(n=0),
                     EN["exact_state_waiting"].format(n=0),
                     EN["exact_state_to_enter"].format(n=0)]                    # Task 33's line, the last
    text = " ".join(lines)
    assert not any(content in text for content in ("250302", "S21", "KM-", "Inkooporder", "Artikel"))
    assert (NL["exact_state_columns_set"], NL["exact_state_columns_unset"]) == ("Kolommen van de export: ingesteld.",
                                                                                "Kolommen van de export: nog niet ingesteld.")
    assert (EN["exact_state_columns_set"], EN["exact_state_columns_unset"]) == ("Columns of the export: set.", "Columns of the export: not set yet.")
    assert (NL["exact_state_export"], NL["exact_state_no_export"]) == ("Laatste export opgenomen: {time}.", "Nog geen export opgenomen.")
    assert (EN["exact_state_export"], EN["exact_state_no_export"]) == ("Last export filed: {time}.", "No export filed yet.")


# --- the review round of part 2 (rulings M1 to M13) ---

PLAIN_MAP = {"po_number": "Inkooporder", "item_code": "Artikel", "description": "Omschrijving", "quantity": "Aantal", "unit_price": "Prijs"}


def set_cell(row: Row, title: str, text: str) -> None:
    """Give the cell under the title of the made-up layout in a row of its lines table another text."""
    next(t for t in row.texts if t.x == next(c.x for c in PO_COLUMNS if c.title == title)).text = text


def test_the_count_line_tells_the_rows_passed_over_apart(tmp_path):
    """Ruling M1 (findings 1 and 7): "{k} rijen zonder artikelcode overgeslagen" counts the rows whose item code cell is blank only;
    rows with an item code but no quantity get a line of their own, which refuses nothing; an item code that is a date, a time or an
    error value is a problem that refuses the save, and no line "Niet gelezen" is shown for the item code; a repeated row of column
    titles is counted nowhere."""
    received = write_sheet(tmp_path / "received_as_quantity.csv", [
        ["Inkooporder", "Artikel", "Omschrijving", "Besteld", "Ontvangen", "Prijs", "Leverdatum"],
        ["250301", "KM-3101", "Slangklem 16-27 mm", "1000", "1000", "12,50", "24-11-2026"],
        ["250301", "KM-3102", "Hydrauliekslang 12 mm", "25", "", "4,80", "24-11-2026"],
        ["250301", "KM-3103", "Kogelkraan DN20", "4", "", "38,75", "01-12-2026"],
        ["250302", "KM-3201", "O-ring 30 x 3 mm", "200", "", "0,13", "26-11-2026"],
        ["250302", "KM-3202", "Tandwielpomp 11 cc", "2", "2", "1234,50", "26-11-2026"], ["", "Totaal", "", "", "", "", ""]])
    values = {"export_columns": PLAIN_MAP | {"quantity": "Ontvangen", "delivery_date": "Leverdatum"}, "export_decimal_comma": True}
    tried = export_trial(received, values, "nl")
    assert tried.problems == []                     # the total row with a word in the item code column is rightly passed over: a save may go
    assert export_text(tried, values, "nl").split("\n\n") == [
        "Gelezen: 2 inkooporders met samen 2 regels; 0 rijen zonder artikelcode overgeslagen. Hieronder de eerste en de laatste inkooporder: "
        "vergelijk ze met Exact.",
        "4 rijen met een artikelcode maar zonder aantal overgeslagen (eerste: rij 3). Zijn dat orderregels, kies dan bij \"Aantal\" de kolom "
        "met het bestelde aantal."]
    assert export_text(tried, values, "en").split("\n\n")[1] == (
        "4 rows with an item code but no quantity passed over (first: row 3). If they are order lines, choose the column with the ordered "
        "quantity for \"Quantity\".")
    # blank_qty: the delivered quantity chosen, two order lines without one, and a total row without an item code
    blank_qty = write_sheet(tmp_path / "blank_qty.csv", [["Inkooporder", "Artikel", "Omschrijving", "Besteld", "Geleverd", "Prijs"],
                                                         ["250301", "KM-3101", "Slangklem", "1.000", "", "12,50"],
                                                         ["250301", "KM-3102", "Slang", "25", "25", "4,80"],
                                                         ["250301", "KM-3103", "Kogelkraan", "4", "", "38,75"], ["Totaal", "", "", "", "", "55,05"]])
    delivered = {"export_columns": PLAIN_MAP | {"quantity": "Geleverd"}, "export_decimal_comma": True}
    assert export_shown(blank_qty, delivered, "nl")[0].split("\n\n") == [
        "Gelezen: 1 inkooporder met 1 regel; 1 rij zonder artikelcode overgeslagen. Hieronder de eerste inkooporder: vergelijk haar met Exact.",
        "2 rijen met een artikelcode maar zonder aantal overgeslagen (eerste: rij 2). Zijn dat orderregels, kies dan bij \"Aantal\" de kolom "
        "met het bestelde aantal."]
    ordered = delivered | {"export_columns": PLAIN_MAP | {"quantity": "Besteld"}}
    assert export_shown(blank_qty, ordered, "nl")[0] == ("Gelezen: 1 inkooporder met 3 regels; 1 rij zonder artikelcode overgeslagen. "
                                                        "Hieronder de eerste inkooporder: vergelijk haar met Exact.")
    # date_code.xlsx: an item code that Excel turned into a date, and the row of column titles repeated lower down
    dated = write_sheet(tmp_path / "date_code.xlsx", [
        ["Inkooporder", "Artikel", "Omschrijving", "Aantal", "Prijs"], ["250301", "KM-3101", "Slangklem 16-27 mm", 1000, 12.5],
        ["250301", datetime(2026, 11, 3), "Pakking 3-11", 25, 4.8], ["250301", "KM-3103", "Kogelkraan DN20", 4, 38.75],
        ["Inkooporder", "Artikel", "Omschrijving", "Aantal", "Prijs"], ["250302", "KM-3201", "O-ring 30 x 3 mm", 200, 0.13]])
    plain = {"export_columns": PLAIN_MAP, "export_decimal_comma": True}
    tried = export_trial(dated, plain, "nl")
    problem = ("In 1 rij is de artikelcode een datum, een tijd of een foutwaarde (rij 3), zodat de assistent die rij overslaat. Kies de kolom "
               "met de artikelcode; staat die goed, kijk die rij dan na in het bestand: Excel kan een code in een datum omzetten.")
    assert tried.problems == [problem] and tried.reading.passed_over == 2                      # the repeated titles: counted nowhere
    assert export_text(tried, plain, "nl").split("\n\n") == [
        "Gelezen: 2 inkooporders met samen 3 regels; 0 rijen zonder artikelcode overgeslagen. Hieronder de eerste en de laatste inkooporder: "
        "vergelijk ze met Exact.", problem]                                                    # and no line "Niet gelezen" for the item code
    assert export_trial(dated, plain, "en").problems == [
        "In 1 row the item code is a date, a time or an error value (row 3), so the assistant passes that row over. Choose the column with the "
        "item code; if it is right, check that row in the file: Excel can turn a code into a date."]
    two = write_sheet(tmp_path / "two_dates.xlsx", [["Inkooporder", "Artikel", "Aantal"], ["250301", datetime(2026, 11, 3), 25],
                                                    ["250301", "KM-3102", 4], ["250301", datetime(2026, 3, 11), 1]])
    assert export_trial(two, plain | {"export_columns": {"po_number": "Inkooporder", "item_code": "Artikel", "quantity": "Aantal"}},
                        "nl").problems == [NL["exact_export_problem:item_code_date"].format(count=2, row=2)]
    assert NL["exact_export_problem:item_code_date"] == (
        "In {count} rijen is de artikelcode een datum, een tijd of een foutwaarde (eerste: rij {row}), zodat de assistent die rijen overslaat. "
        "Kies de kolom met de artikelcode; staat die goed, kijk die rijen dan na in het bestand: Excel kan een code in een datum omzetten.")
    assert EN["exact_export_problem:item_code_date"] == (
        "In {count} rows the item code is a date, a time or an error value (first: row {row}), so the assistant passes those rows over. Choose "
        "the column with the item code; if it is right, check those rows in the file: Excel can turn a code into a date.")
    assert NL["exact_export_no_quantity"] == ("{count} rijen met een artikelcode maar zonder aantal overgeslagen (eerste: rij {row}). Zijn dat "
                                              "orderregels, kies dan bij \"{field}\" de kolom met het bestelde aantal.")
    assert EN["exact_export_no_quantity"] == ("{count} rows with an item code but no quantity passed over (first: row {row}). If they are order "
                                              "lines, choose the column with the ordered quantity for \"{field}\".")


def test_the_trial_shows_the_first_and_the_last_order(tmp_path):
    """Rulings M2 and M15: with two orders or more the table of the trial shows the lines of the first order, of the order of the file's
    last order line and of the PO the file names last when that is yet another, each once, its PO number column telling them apart,
    and the count line ends "Hieronder de eerste en de laatste inkooporder: vergelijk ze met Exact."; with one order the brief's
    sentence stays. A grand total with a word in the item code column and a sum in the quantity column becomes, with the tick, a line
    of the order of the file's last order line, which the owner sees; in this export, sorted by PO number, that is the last order."""
    total = write_sheet(tmp_path / "grand_total_qty.csv", [["Inkooporder", "Artikel", "Omschrijving", "Aantal", "Prijs"],
                                                           ["250301", "KM-3101", "Slangklem 16-27 mm", "1000", "12,50"],
                                                           ["250301", "KM-3102", "Hydrauliekslang 12 mm", "25", "4,80"],
                                                           ["250302", "KM-3201", "O-ring 30 x 3 mm", "200", "0,13"],
                                                           ["250302", "KM-3202", "Tandwielpomp 11 cc", "2", "1234,50"], ["", "Totaal", "", "1227", ""]])
    values = {"export_columns": PLAIN_MAP, "export_decimal_comma": True, "export_fill_down": True}
    text, lines = export_shown(total, values, "nl")
    assert text == ("Gelezen: 2 inkooporders met samen 5 regels; 0 rijen zonder artikelcode overgeslagen. Hieronder de eerste en de laatste "
                    "inkooporder: vergelijk ze met Exact.")
    assert [line[:3] for line in lines] == [["250301", "1", "KM-3101"], ["250301", "2", "KM-3102"], ["250302", "1", "KM-3201"],
                                            ["250302", "2", "KM-3202"], ["250302", "3", "Totaal"]]
    assert lines[-1] == ["250302", "3", "Totaal", "", "1227", ""]                              # the total row, as the store would file it
    three = write_sheet(tmp_path / "three.csv", [["Inkooporder", "Artikel"], ["250301", "KM-1"], ["250302", "KM-2"], ["250303", "KM-3"]])
    least = {"export_columns": {"po_number": "Inkooporder", "item_code": "Artikel"}}
    assert export_shown(three, least, "nl")[1] == [["250301", "1", "KM-1"], ["250303", "1", "KM-3"]]    # not the order between them
    one = write_sheet(tmp_path / "one.csv", [["Inkooporder", "Artikel"], ["250301", "KM-1"], ["250301", "KM-2"]])
    assert export_shown(one, least, "nl") == ("Gelezen: 1 inkooporder met 2 regels; 0 rijen zonder artikelcode overgeslagen. Hieronder de "
                                              "eerste inkooporder: vergelijk haar met Exact.", [["250301", "1", "KM-1"], ["250301", "2", "KM-2"]])
    assert (NL["exact_export_first"], NL["exact_export_first_and_last"]) == (
        "Hieronder de eerste inkooporder: vergelijk haar met Exact.", "Hieronder de eerste en de laatste inkooporder: vergelijk ze met Exact.")
    assert (EN["exact_export_first"], EN["exact_export_first_and_last"]) == (
        "Below is the first purchase order: compare it with Exact.", "Below are the first and the last purchase order: compare them with Exact.")


def test_the_trial_shows_the_order_where_a_total_row_lands(tmp_path):
    """Ruling M15 (finding 1 of the check of the fix round): in an export sorted by delivery date, with the PO number on every line, the
    tick gives a total row at the end of the file the PO number of the row above it, which is neither the first order nor the PO the
    file names last. The trial shows the first order, the order of the file's last order line and the PO the file names last when that
    is yet another, each once and in that order, so the order that holds "Totaal" is shown; the count line keeps its sentence."""
    values = {"export_columns": SORTED_BY_DATE_COLUMNS, "export_decimal_comma": True, "export_fill_down": True}
    tried = export_trial(SORTED_BY_DATE, values, "nl")
    assert tried.problems == [] and export_text(tried, values, "nl") == (
        "Gelezen: 3 inkooporders met samen 5 regels; 0 rijen zonder artikelcode overgeslagen. Hieronder de eerste en de laatste inkooporder: "
        "vergelijk ze met Exact.")
    lines = export_lines(tried, values, "nl")
    assert [line[:3] for line in lines] == [["250301", "1", "KM-3101"], ["250302", "1", "KM-3201"], ["250302", "2", "KM-3202"],
                                            ["250302", "3", "Totaal"], ["250303", "1", "KM-3301"]]
    assert lines[3][4] == "1206 (1.206)"                                           # the total, as the store would file it
    longer = export_trial(SORTED_BY_DATE_LONG, values, "nl")                       # 32 rows: the total stands far below the first 15
    shown = export_lines(longer, values, "nl")
    assert [line[0] for line in shown] == ["250301"] * 5 + ["250306"] * 6 + ["250305"] * 5     # first, the last line's, named last
    assert shown[10][:3] == ["250306", "6", "Totaal"] and longer.problems == []
    # the order of the last line is the first order: the first and the PO named last, each once
    back = write_sheet(tmp_path / "back.csv", [["Inkooporder", "Artikel"], ["250301", "KM-1"], ["250302", "KM-2"], ["250301", "KM-3"]])
    least = {"export_columns": {"po_number": "Inkooporder", "item_code": "Artikel"}}
    assert export_shown(back, least, "nl")[1] == [["250301", "1", "KM-1"], ["250301", "2", "KM-3"], ["250302", "1", "KM-2"]]
    # the order of the last line is the PO named last, as in an export sorted by PO number: the first and the last, as before
    assert export_shown(EXPORT_NL, export_from_form(EXPORT_NL_FORM, "nl")[0], "nl")[1] == EXPORT_NL_ORDERS


def test_a_file_that_is_no_pdf_is_refused_by_an_error_of_its_own(tmp_path):
    """Ruling M3: part 1's sample field takes any file, so the block refuses a file that is no PDF, by its name, with an error of its
    own that the page names by its sentence; a .pdf that cannot be opened keeps SampleUnreadable (Task 32's ruling L11)."""
    for name in ("export.xls", "notes.txt", "order.docx"):
        other = tmp_path / name
        other.write_bytes(ANDERS_PO.read_bytes())                                 # even the bytes of a PDF under another name
        for read in (lambda: page_lines(other), lambda: title_words(other, "Artikelcode"), lambda: trial(other, ANDERS_LAYOUT, "nl"),
                     lambda: check(other, ANDERS_LAYOUT, "nl")):
            with pytest.raises(NotPdf):
                read()
    for name in ("order", "order.pdf.bak"):                                      # a PDF whose name does not end in .pdf (ruling M17)
        unnamed = tmp_path / name
        unnamed.write_bytes(ANDERS_PO.read_bytes())
        with pytest.raises(NotPdf):
            page_lines(unnamed)
    upper = tmp_path / "ORDER.PDF"
    upper.write_bytes(ANDERS_PO.read_bytes())
    assert page_lines(upper) == page_lines(ANDERS_PO)                             # the suffix in any case
    cut = tmp_path / "cut.pdf"
    cut.write_bytes(ANDERS_PO.read_bytes()[:1500])
    with pytest.raises(SampleUnreadable):
        page_lines(cut)


def test_the_sample_is_cut_where_its_rows_agree(tmp_path):
    """Ruling M5 (findings 3 and 11), with the rule of ruling M21 (the mark that gives the whole file the most filled cells): a
    tab-separated export with decimal commas, one in UTF-16 with its byte order mark, and a comma-separated one with semicolons in a
    description show each title in a cell of its own on row 1, and the trial reads what the export reader reads."""
    tabs, wide, commas = write_cut_samples(tmp_path)                 # the reviewers' three samples, as they hold them
    for path, titles, second in ((tabs, TABS_TITLES, ["250301", "KM-3101", "Slangklem", "1000", "12,50", "125,00"]),
                                 (wide, WIDE_TITLES, ["250301", "KM-3101", "Slangklem 16-27 mm", "1.000", "st", "12,50", "100", "24-11-2026",
                                                      "125,00"])):
        assert table_head(path)[0] == ["1", *titles] and header_choices(path) == (1, titles), path.name
        assert table_head(path)[1] == ["2", *second], path.name
    assert header_choices(commas) == (1, COMMAS_TITLES)
    assert table_head(commas)[2] == ["3", "250301", "KM-3102", "Hose 12 mm; EPDM; 10 bar", "25", "4.80"]
    for path, columns, decimal_comma in ((tabs, {"po_number": "Inkooporder", "item_code": "Artikel", "quantity": "Aantal", "unit_price": "Prijs"},
                                          True),
                                         (wide, {"po_number": "Inkooporder", "item_code": "Artikel", "quantity": "Aantal", "unit": "Eenheid",
                                                 "unit_price": "Prijs", "price_basis": "Per", "delivery_date": "Leverdatum"}, True),
                                         (commas, {"po_number": "PO Number", "item_code": "Item", "description": "Description", "quantity": "Qty"},
                                          False)):
        tried = export_trial(path, {"export_columns": columns, "export_decimal_comma": decimal_comma}, "nl")
        assert tried.problems == [] and tried.reading == read_export(path, columns, decimal_comma), path.name
    # a semicolon export whose amounts have decimal commas stays cut at its semicolons: the title row agrees with its order lines there
    assert header_choices(EXPORT_NL) == (3, EXPORT_NL_TITLES)


def test_the_sample_is_cut_at_the_mark_that_gives_the_file_the_most_filled_cells(tmp_path):
    """Rulings M16 and M21 (findings 2 and 5 of the check of the fix round, and the two cases M16 cut wrongly): a CSV sample is cut at
    the mark under which the whole file holds the most filled cells, since under the file's own mark every row is cut into its columns,
    while commas or semicolons inside some cells add cells on those rows only; on a tie, at the mark under which the widest row holds the
    most filled cells; on a tie again, at the first of the reader's marks (semicolon, comma, tab)."""
    footer, title, tabs, wide, tie = write_title_samples(tmp_path)                 # the checkers' five samples, as they hold them
    for path, row, titles, columns in ((footer, 3, EXPORT_NL_TITLES, EXPORT_NL_COLUMNS), (title, 3, EXPORT_NL_TITLES, EXPORT_NL_COLUMNS),
                                       (tabs, 3, TITLE_COMMAS, {"po_number": "Inkooporder", "item_code": "Artikel", "quantity": "Aantal",
                                                                "unit_price": "Prijs"}),
                                       (wide, 3, TITLE_COMMAS, {"po_number": "Inkooporder", "item_code": "Artikel", "unit_price": "Prijs"}),
                                       (tie, 1, TIE_TITLES, {"po_number": "Inkooporder", "item_code": "Artikel", "unit_price": "Prijs"})):
        assert header_choices(path) == (row, titles), path.name                    # the titles on their own row, each in its own cell
        assert table_head(path)[row - 1][:len(titles) + 1] == [str(row), *titles], path.name
        fill_down = path in (footer, title)
        tried = export_trial(path, {"export_columns": columns, "export_decimal_comma": True, "export_fill_down": fill_down}, "nl")
        assert tried.problems == [] and tried.reading == read_export(path, columns, True, fill_down), path.name
    assert table_head(footer)[0][:2] == ["1", "Openstaande inkooporderregels, alle leveranciers, week 47"]      # the title line whole
    # the checkers' comma file whose item codes hold a semicolon: as wide at both marks, but more filled cells at the commas (M21)
    semis = tmp_path / "two_columns_comma_semi.csv"
    semis.write_bytes(b"PO Number,Item\r\n250301,KM;3101\r\n250301,KM;3102\r\n")
    assert header_choices(semis) == (1, ["PO Number", "Item"]) and table_head(semis)[1] == ["2", "250301", "KM;3101"]
    least = {"po_number": "PO Number", "item_code": "Item"}
    tried = export_trial(semis, {"export_columns": least}, "nl")
    assert tried.problems == [] and tried.reading == read_export(semis, least) and tried.reading.rows == 2
    # as many filled cells at the semicolons and at the commas (five each): the mark of the widest row, the commas here
    wider = tmp_path / "wider.csv"
    wider.write_bytes(b"Inkooporder,Artikel,Aantal\n250301;KM-3101\n250302;KM-3201\n")
    assert header_choices(wider) == (1, ["Inkooporder", "Artikel", "Aantal"]) and table_head(wider)[1] == ["2", "250301;KM-3101", "", ""]
    # a tie again (four filled cells and a widest row of two at the semicolons and at the commas): the first of the reader's marks
    even = tmp_path / "even.csv"
    even.write_bytes(b"Inkooporder;Artikel,Aantal\n250301;KM-3101,5\n")
    assert header_choices(even) == (1, ["Inkooporder", "Artikel,Aantal"]) and table_head(even)[1] == ["2", "250301", "KM-3101,5"]


def test_the_decimal_mark_proves_the_convention_with_any_number_of_digits_but_three(tmp_path):
    """Ruling M6 (finding 4): a number printed with the other convention's decimal mark followed by one, two, four or more digits
    proves that convention, in part 2 and in part 1 alike; a mark followed by exactly three digits proves nothing."""
    four = write_sheet(tmp_path / "four_decimals_nl.csv", [["Inkooporder", "Artikel", "Omschrijving", "Aantal", "Prijs"],
                                                           ["250301", "KM-3101", "Slangklem 16-27 mm", "25", "0,1250"],
                                                           ["250301", "KM-3102", "Hydrauliekslang 12 mm", "40", "4,8000"],
                                                           ["250302", "KM-3201", "O-ring 30 x 3 mm", "2.500", "0,1250"],
                                                           ["250302", "KM-3202", "Tandwielpomp 11 cc", "1.000", "12,1250"]])
    point, comma = ({"export_columns": PLAIN_MAP, "export_decimal_comma": flag} for flag in (False, True))
    assert export_trial(four, point, "nl").problems == [NL["exact_problem:convention_comma"].format(column="\"Prijs\"")]
    assert export_trial(four, comma, "nl").problems == []
    assert export_trial(EXPORT_NL, export_from_form(EXPORT_NL_FORM, "nl")[0], "nl").problems == []          # the comma: nothing
    english = write_sheet(tmp_path / "english.csv", [["PO Number", "Item", "Qty", "Price"], ["250301", "KM-3101", "1,000", "12.50"],
                                                     ["250301", "KM-3102", "25", "4.80"]])
    plain_english = {"export_columns": {"po_number": "PO Number", "item_code": "Item", "quantity": "Qty", "unit_price": "Price"}}
    assert export_trial(english, plain_english | {"export_decimal_comma": False}, "en").problems == []       # the point: nothing
    for text, right, key in (("1,234.5678", False, "exact_problem:convention_point"), ("1.234,5", True, "exact_problem:convention_comma")):
        sample = write_sheet(tmp_path / f"{text}.csv", [["Inkooporder", "Artikel", "Prijs"], ["250301", "KM-3101", text]])
        values = {"export_columns": {"po_number": "Inkooporder", "item_code": "Artikel", "unit_price": "Prijs"}}
        assert export_trial(sample, values | {"export_decimal_comma": right}, "nl").problems == [], text
        assert export_trial(sample, values | {"export_decimal_comma": not right}, "nl").problems == [NL[key].format(column="\"Prijs\"")], text
    # part 1: a PDF whose prices are printed "0,1250", read while the point is chosen
    priced = madeup_po(tmp_path, lambda pages: [set_cell(row, "Prijs", "0,1250") for row in pages[0][7:10]], "priced.pdf")
    assert check(priced, MADEUP_LAYOUT.model_copy(update={"decimal_comma": False}), "nl") == [
        NL["exact_problem:convention_comma"].format(column="Prijs")]
    assert check(priced, MADEUP_LAYOUT.model_copy(update={"decimal_comma": True}), "nl") == []


def test_the_purchase_orders_known_count_the_looks_and_name_what_was_filed_last(tmp_path, madeup_out):
    """Ruling M8: the state line of the purchase orders the assistant knows counts every PO the store holds as it stands, a PDF version
    or a look from an export, and names what was filed last: a PDF version as before, or a look "uit een export"; on equal times the PDF
    counts, and among looks filed at the same time the highest PO number."""
    settings = SETTINGS.model_copy(update={"po_store_root": tmp_path / "store", "watched_folder": tmp_path / "inbox"})
    store, now = PoStore(settings.po_store_root), datetime.now(timezone.utc)
    store.add(madeup_out / "MU08_po_v1.pdf")                                      # PO 250108, a PDF version
    store.from_export(EXPORT_NL, EXPORT_NL_COLUMNS, True, True)                   # POs 250301 and 250302, looks
    base = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc).timestamp()

    def filed(folder: str, seconds: int) -> None:
        os.utime(store.root / folder / "po.json", (base + seconds, base + seconds))

    def at(seconds: int) -> str:
        return datetime.fromtimestamp(base + seconds, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")

    def known_line(language: str = "nl") -> str:
        return state_lines(settings, store, now, language)[1]

    filed("250108/v1", 0), filed("250301/e1", 600), filed("250302/e1", 300)      # a PDF, then an export
    assert known_line() == "Inkooporders die de assistent kent: 3. Laatst opgenomen: inkooporder 250301 uit een export, " + at(600) + "."
    assert known_line("en") == f"Purchase orders the assistant knows: 3. Filed last: purchase order 250301 from an export, {at(600)}."
    filed("250108/v1", 900)                                                       # an export, then a PDF
    assert known_line() == NL["exact_state_known"].format(count=3, number="250108", version=1, time=at(900))
    filed("250301/e1", 900), filed("250302/e1", 900)                              # the same time: the PDF counts
    assert known(store)[1][:2] == ("250108", 1)
    filed("250108/v1", 0)                                                         # looks of the same time: the highest PO number
    assert known(store) == (3, ("250302", None, datetime.fromtimestamp(base + 900, timezone.utc)))
    exports = PoStore(tmp_path / "exports")                                       # exports only
    exports.from_export(EXPORT_NL, EXPORT_NL_COLUMNS, True, True)
    count, (number, version, _) = known(exports)
    assert (count, version) == (2, None) and number in ("250301", "250302")
    assert state_lines(settings, exports, now, "nl")[1].startswith("Inkooporders die de assistent kent: 2. Laatst opgenomen: inkooporder 2503")
    assert state_lines(settings, PoStore(tmp_path / "empty"), now, "nl")[1] == NL["exact_state_none"]
    assert NL["exact_state_known_export"] == "Inkooporders die de assistent kent: {count}. Laatst opgenomen: inkooporder {number} uit een export, {time}."
    assert EN["exact_state_known_export"] == "Purchase orders the assistant knows: {count}. Filed last: purchase order {number} from an export, {time}."


def test_a_count_of_one_takes_the_singular(tmp_path):
    """Ruling M9 (finding 14): a count of 1 takes the singular in both languages, "(eerste: rij n)" becomes "(rij n)" and "samen" is left
    out for one order; every other count keeps the brief's sentences."""
    one = write_sheet(tmp_path / "one.csv", [["Inkooporder", "Artikel", "Aantal", "Leverdatum"], ["250301", "KM-3101", "5", "morgen"],
                                             ["250301", "KM-3102", "", "24-11-2026"], ["Totaal", "", "", ""]])
    values = {"export_columns": {"po_number": "Inkooporder", "item_code": "Artikel", "quantity": "Aantal", "delivery_date": "Leverdatum"}}
    assert export_shown(one, values, "nl")[0].split("\n\n") == [
        "Gelezen: 1 inkooporder met 1 regel; 1 rij zonder artikelcode overgeslagen. Hieronder de eerste inkooporder: vergelijk haar met Exact.",
        "1 rij met een artikelcode maar zonder aantal overgeslagen (rij 3). Is dat een orderregel, kies dan bij \"Aantal\" de kolom met het "
        "bestelde aantal.",
        "Niet gelezen: 1 waarde in de kolom \"Leverdatum\" (rij 2). Die waarde vergelijkt de assistent niet."]
    assert export_shown(one, values, "en")[0].split("\n\n") == [
        "Read: 1 purchase order with 1 line; 1 row without an item code passed over. Below is the first purchase order: compare it with Exact.",
        "1 row with an item code but no quantity passed over (row 3). If it is an order line, choose the column with the ordered quantity for "
        "\"Quantity\".",
        "Not read: 1 value in the column \"Leverdatum\" (row 2). The assistant does not compare that value."]
    for strings in (NL, EN):              # ruling M19: each "_one" key of part 2 beside the key without it, or the key that ends in "_many"
        for key in (key for key in strings if key.startswith("exact_export") and key.endswith("_one")):
            assert key[:-4] in strings or f"{key[:-4]}_many" in strings, key


def test_a_sample_is_read_once_for_its_first_rows_and_titles_and_once_for_a_trial(tmp_path, monkeypatch):
    """Ruling M11 (finding 5): the first rows and the titles of a sample come from one read of the file (sample_head), and a trial reads
    it once, for its reading and for the check of the number convention alike."""
    import aid.readers.po_export as po_export
    import aid.review.exact_settings as exact
    reads, real_text, real_rows = [], po_export.csv_text, po_export.workbook_rows

    def counted_text(path):
        reads.append(Path(path).name)
        return real_text(path)

    def counted_rows(path):
        reads.append(Path(path).name)
        return real_rows(path)

    for module in (po_export, exact):
        monkeypatch.setattr(module, "csv_text", counted_text)
        monkeypatch.setattr(module, "workbook_rows", counted_rows)
    head, row, titles = sample_head(EXPORT_NL)
    assert reads == ["EXPORT_nl.csv"]
    export_trial(EXPORT_NL, export_from_form(EXPORT_NL_FORM, "nl")[0], "nl")
    assert reads == ["EXPORT_nl.csv"] * 2
    book = write_sheet(tmp_path / "book.xlsx", [["Inkooporder", "Artikel", "Aantal", "Prijs"], ["250301", "KM-3101", 1000, "12,50"]])
    reads.clear()
    sample_head(book), export_trial(book, {"export_columns": {"po_number": "Inkooporder", "item_code": "Artikel", "unit_price": "Prijs"}}, "nl")
    assert reads == ["book.xlsx"] * 2
    assert (head, row, titles) == (table_head(EXPORT_NL), 3, EXPORT_NL_TITLES) and sample_head(EXPORT_NL, 1)[1:] == (1, [
        "Openstaande inkooporderregels (verzonnen voorbeeld)"])


def test_the_table_of_the_first_rows_shows_rows_1_to_15(tmp_path):
    """Ruling M13 (finding 15): the table of the first rows of a longer sample shows rows 1 to 15, each with its number."""
    long = write_sheet(tmp_path / "long.csv", [["Inkooporder", "Artikel", "Aantal"], *[["250301", f"KM-{n}", str(n)] for n in range(1, 30)]])
    head = table_head(long)
    assert [row[0] for row in head] == [str(n) for n in range(1, 16)] and head[-1] == ["15", "250301", "KM-14", "14"]


# --- part 3: the export of articles (Task 42) ---

from aid.review.exact_settings import (ARTICLE_FORM, ARTICLE_ROWS, article_label, articles_from_form, articles_headers,  # noqa: E402
                                       articles_rows, articles_text, articles_trial, form_from_articles, saved_articles)

ARTICLES_FORM = {"item_code": "Artikel", "price": "Inkoopprijs", "supplier": NO_COLUMN, "numbers": "comma"}     # one supplier's articles
ARTICLES_SAVED = {"article_columns": {"item_code": "Artikel", "price": "Inkoopprijs"}, "article_decimal_comma": True}


def articles_sample(path: Path, rows: list[list], titles: tuple = ("Artikel", "Omschrijving", "Inkoopprijs")) -> Path:
    """A made-up export of articles: a title above the row of column titles, then the rows (write_sheet)."""
    return write_sheet(path, [["Artikelen van een leverancier (verzonnen voorbeeld)"], [], list(titles), *rows])


def test_the_form_of_part_3_gives_the_two_settings_or_names_what_is_missing():
    """Amendment 3: part 3 sets the columns of the export of articles and the convention of its numbers, and nothing else."""
    settings, problems = articles_from_form(ARTICLES_FORM, "nl")
    assert problems == {} and settings == ARTICLES_SAVED
    named, problems = articles_from_form({"item_code": " Artikel ", "price": "Inkoopprijs", "supplier": "Crediteur", "numbers": "point"}, "en")
    assert problems == {} and named == {"article_columns": {"item_code": "Artikel", "price": "Inkoopprijs", "supplier": "Crediteur"},
                                        "article_decimal_comma": False}
    assert Settings.model_validate(SETTINGS.model_dump() | named).article_columns == named["article_columns"]
    assert articles_from_form({"numbers": "comma"}, "nl") == (None, {"item_code": "Kies de kolom met de artikelcode.",
                                                                      "price": "Kies de kolom met de inkoopprijs."})
    assert articles_from_form(ARTICLES_FORM | {"price": NO_COLUMN}, "en") == (None, {"price": "Choose the column with the purchase price."})
    assert articles_from_form(ARTICLES_FORM | {"supplier": " ARTIKEL "}, "nl") == (
        None, {"supplier": NL["exact_export_problem:more_than_once"].format(column="\"Artikel\"")})
    # the form back from the settings, and the two settings as the page keeps them
    assert form_from_articles(SETTINGS.model_copy(update=ARTICLES_SAVED)) == ARTICLES_FORM
    assert form_from_articles(SETTINGS) == {"item_code": NO_COLUMN, "price": NO_COLUMN, "supplier": NO_COLUMN, "numbers": "point"}
    assert saved_articles(SETTINGS) == {"article_columns": {}, "article_decimal_comma": False}
    assert saved_articles(SETTINGS.model_copy(update=ARTICLES_SAVED)) == ARTICLES_SAVED
    assert ARTICLE_FORM == ("item_code", "price", "supplier")
    assert [article_label(field, "nl") for field in ARTICLE_FORM] == ["Artikelcode (verplicht)", "Inkoopprijs (verplicht)", "Leverancier"]
    assert [article_label(field, "en") for field in ARTICLE_FORM] == ["Item code (required)", "Purchase price (required)", "Supplier"]


def test_the_trial_read_of_part_3_counts_what_it_read_and_names_what_it_passes_over(tmp_path):
    """Amendment 3: the trial read says how many articles it read and how many rows it passed over, with the count of each reason that
    occurs, a count of one in the singular, and shows the first rows as read; it writes nothing."""
    sample = articles_sample(tmp_path / "artikelen.csv", [["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", "op aanvraag"], ["KM-1003", "Ring", "8,00"],
                                                          ["KM-1003", "Ring", "8,10"], [None, "Totaal", "28,60"], ["KM-1005", "Kap", "1.000"]])
    before = sorted(path.name for path in tmp_path.iterdir())
    tried = articles_trial(sample, ARTICLES_SAVED, "nl")
    assert tried.problems == [] and articles_text(tried, "nl") == (
        "2 artikelen gelezen; 4 rijen overgeslagen (zonder artikelcode: 1, prijs niet te lezen: 1, artikel dubbel: 2).")
    assert articles_text(articles_trial(sample, ARTICLES_SAVED, "en"), "en") == (
        "2 articles read; 4 rows passed over (no item code: 1, price cannot be read: 1, article twice: 2).")
    assert articles_headers(ARTICLES_SAVED, "nl") == ["Rij", "Artikelcode", "Inkoopprijs"]
    assert articles_headers(ARTICLES_SAVED, "en") == ["Row", "Item code", "Purchase price"]
    assert articles_rows(tried, ARTICLES_SAVED, "nl") == [["4", "KM-1001", "12.5 (12,50)"], ["9", "KM-1005", "1000 (1.000)"]]
    assert sorted(path.name for path in tmp_path.iterdir()) == before
    # a count of one in the singular, and no brackets when nothing was passed over
    one = articles_sample(tmp_path / "een.csv", [["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", None]])
    assert articles_text(articles_trial(one, ARTICLES_SAVED, "nl"), "nl") == "1 artikel gelezen; 1 rij overgeslagen (prijs niet te lezen: 1)."
    assert articles_text(articles_trial(one, ARTICLES_SAVED, "en"), "en") == "1 article read; 1 row passed over (price cannot be read: 1)."
    clean = articles_sample(tmp_path / "schoon.xlsx", [["KM-1001", "Slang", 12.5], ["KM-1002", "Klem", 8]])
    assert articles_text(articles_trial(clean, ARTICLES_SAVED, "nl"), "nl") == "2 artikelen gelezen; 0 rijen overgeslagen."
    assert articles_text(articles_trial(clean, ARTICLES_SAVED, "en"), "en") == "2 articles read; 0 rows passed over."
    assert articles_rows(articles_trial(clean, ARTICLES_SAVED, "nl"), ARTICLES_SAVED, "nl") == [["4", "KM-1001", "12.5"], ["5", "KM-1002", "8"]]
    assert (NL["exact_articles_read"].format(articles=NL["pricelist_articles_many"], passed=NL["pricelist_passed_many"]),
            EN["exact_articles_read"].format(articles=EN["pricelist_articles_many"], passed=EN["pricelist_passed_many"])) == (
        "{n} artikelen gelezen; {k} rijen overgeslagen.", "{n} articles read; {k} rows passed over.")
    assert [(NL[f"exact_articles_left:{reason}"], EN[f"exact_articles_left:{reason}"]) for reason in ("no_item_code", "no_price", "twice",
                                                                                                    "no_supplier")] == [
        ("zonder artikelcode", "no item code"), ("prijs niet te lezen", "price cannot be read"), ("artikel dubbel", "article twice"),
        ("zonder leverancier", "no supplier")]
    # with a supplier column the table names the supplier of each row, and a row without one is counted
    named = {"article_columns": {"item_code": "Artikel", "price": "Inkoopprijs", "supplier": "Leverancier"}, "article_decimal_comma": True}
    several = articles_sample(tmp_path / "alle.csv", [["KM-1001", "S01", "12,50"], ["KM-2001", "S02", "3,20"], ["KM-9001", "", "1,00"]],
                              titles=("Artikel", "Leverancier", "Inkoopprijs"))
    tried = articles_trial(several, named, "nl")
    assert articles_text(tried, "nl") == "2 artikelen gelezen; 1 rij overgeslagen (zonder leverancier: 1)."
    assert articles_headers(named, "nl") == ["Rij", "Artikelcode", "Inkoopprijs", "Leverancier"] == articles_headers(None, "nl")
    assert articles_rows(tried, named, "nl") == [["4", "KM-1001", "12.5 (12,50)", "S01"], ["5", "KM-2001", "3.2 (3,20)", "S02"]]
    # the table shows the first rows only
    many = articles_sample(tmp_path / "veel.csv", [[f"KM-{n:04d}", "Deel", "1,00"] for n in range(40)])
    assert len(articles_rows(articles_trial(many, ARTICLES_SAVED, "nl"), ARTICLES_SAVED, "nl")) == ARTICLE_ROWS == 15


def test_the_trial_read_of_part_3_names_each_problem_by_a_sentence(tmp_path):
    """A refusal of the reader is its fixed sentence, the other decimal mark in the price column names the choice that fits, and a sample
    that gives no article says so; none of them shows what a cell holds."""
    sample = articles_sample(tmp_path / "artikelen.csv", [["KM-1001", "Slang", "13,75"], ["KM-1002", "Klem", "8,00"]])
    wrong = articles_trial(sample, ARTICLES_SAVED | {"article_decimal_comma": False}, "nl")
    assert wrong.problems == [NL["exact_problem:convention_comma"].format(column="\"Inkoopprijs\"")]
    assert articles_text(wrong, "nl") == "2 artikelen gelezen; 0 rijen overgeslagen.\n\n" + wrong.problems[0]
    points = articles_sample(tmp_path / "punten.csv", [["KM-1001", "Slang", "12.50"]])
    assert articles_trial(points, ARTICLES_SAVED, "en").problems == [EN["exact_problem:convention_point"].format(column="\"Inkoopprijs\"")]
    book = articles_sample(tmp_path / "artikelen.xlsx", [["KM-1001", "Slang", 12.5]])           # a number cell proves no convention
    assert articles_trial(book, ARTICLES_SAVED, "nl").problems == [] == articles_trial(book, ARTICLES_SAVED | {"article_decimal_comma": False}, "nl").problems
    nothing = articles_sample(tmp_path / "niets.csv", [[None, "Totaal", "20,50"], ["KM-1002", "Klem", "op aanvraag"]])
    tried = articles_trial(nothing, ARTICLES_SAVED, "nl")
    assert tried.problems == ["Het voorbeeld gaf geen artikel: geen rij onder de kolomnamen heeft een artikelcode en een prijs die te lezen is."]
    assert articles_text(tried, "nl").startswith("0 artikelen gelezen; 2 rijen overgeslagen (zonder artikelcode: 1, prijs niet te lezen: 1).\n\n")
    assert articles_trial(nothing, ARTICLES_SAVED, "en").problems == [
        "The sample gave no article: no row below the column titles has an item code and a price that can be read."]
    old = tmp_path / "artikelen.xls"
    old.write_bytes(sample.read_bytes())
    refused = articles_trial(old, ARTICLES_SAVED, "nl")
    assert refused.reading is None and refused.problems == [NL["exact_export_refused:file_type"]] and articles_rows(refused, ARTICLES_SAVED, "nl") == []
    assert articles_text(refused, "nl") == NL["exact_export_refused:file_type"]
    other = articles_trial(sample, {"article_columns": {"item_code": "Artikel", "price": "Prijs"}, "article_decimal_comma": True}, "nl")
    assert other.problems == [NL["exact_export_refused:no_column"].format(column="\"Prijs\"")]
    for trial_read in (wrong, tried, refused, other):
        assert not re.search(r"KM-1|13,75|Slang|Totaal|aanvraag", articles_text(trial_read, "nl"))
    with pytest.raises(OSError):
        articles_trial(tmp_path / "weg.csv", ARTICLES_SAVED, "nl")


# --- the second review round of the price list on the page (rulings Z16 and Z17): the samples of parts 2 and 3 ---

def test_a_sample_with_a_filled_cell_beyond_its_titles_is_refused_with_its_row(tmp_path):
    """Ruling Z16: the samples of parts 2 and 3 are read as the exports are. A row with a filled cell beyond the last column title,
    a decimal comma without quotation marks in a comma file, refuses the trial read of the sample ("Testen", and "Opslaan" with it)
    with the row named, so that no columns are saved from a file cut wrongly; a sample whose rows only end in empty cells is read as
    before. The first rows of the sample are still shown, as the file is cut, so that the owner sees what is wrong, and so that a
    heading above the titles that the block took for the titles can still be passed by with "Rij met de kolomnamen"."""
    from aid.review.exact_settings import sample_head
    orders = tmp_path / "orders.csv"
    orders.write_text("Inkooporder,Artikel,Aantal,Prijs\n250301,KM-3101,1000,12,50\n250301,KM-3102,25,4.80\n", encoding="utf-8")
    articles = tmp_path / "artikelen.csv"
    articles.write_text("Artikelen (verzonnen voorbeeld)\n\nArtikel,Omschrijving,Inkoopprijs\nKM-1001,Slang,12,70\n", encoding="utf-8")
    sentence = ("Rij 2 van dit bestand heeft een ander aantal cellen dan de rij met kolomtitels. In een bestand met komma's als "
                "scheidingsteken staat er dan meestal een komma in een getal of in een omschrijving zonder aanhalingstekens. Sla het bestand op "
                "als Excel-bestand (.xlsx) of met puntkomma's als scheidingsteken, en geef het opnieuw.")     # a comma file: ruling Z30
    values = {"export_columns": {"po_number": "Inkooporder", "item_code": "Artikel", "quantity": "Aantal", "unit_price": "Prijs"},
              "export_decimal_comma": True, "export_fill_down": False}
    tried = export_trial(orders, values, "nl")
    assert tried.reading is None and tried.problems == [sentence]
    trial = articles_trial(articles, ARTICLES_SAVED, "en")
    assert trial.reading is None and trial.problems == [
        "Row 4 of this file has another number of cells than the row of column titles. In a file with commas as the separator a number or "
        "a description then usually holds a comma without quotation marks. Save the file as an Excel file (.xlsx) or with semicolons as "
        "the separator, and give it again."]
    assert NL["exact_export_refused:cell_count"].format(row=2) == sentence
    # ruling Z30, the check's c16b samples: a row with room at its end, and a row shorter than the titles
    orders.write_text("Inkooporder,Artikel,Aantal,Prijs,Opmerking\n250301,KM-3101,1000,12,50,\n", encoding="utf-8")
    assert export_trial(orders, values, "nl").problems == [sentence]
    articles.write_text("Artikel,Omschrijving,Inkoopprijs,Opmerking\nKM-1001,Slang,12.70,\nKM-1002,Klem,8\n", encoding="utf-8")
    assert articles_trial(articles, ARTICLES_SAVED, "nl").problems == [sentence.replace("Rij 2", "Rij 3")]
    orders.write_text("Inkooporder,Artikel,Aantal,Prijs\n250301,KM-3101,1000,12,50\n250301,KM-3102,25,4.80\n", encoding="utf-8")
    # a semicolon file keeps ruling Z16's sentence, for a filled cell beyond the last title
    semicolons = tmp_path / "puntkomma.csv"
    semicolons.write_text("Inkooporder;Artikel;Aantal;Prijs\n250301;KM-3101;1000;12,50;x\n", encoding="utf-8")
    assert export_trial(semicolons, values, "nl").problems == [NL["exact_export_refused:surplus_cells"].format(row=2)]
    assert NL["exact_export_refused:surplus_cells"].startswith("Rij {row} van dit bestand heeft meer cellen dan de rij met kolomtitels.")
    assert sample_head(orders)[0][1] == ["2", "250301", "KM-3101", "1000", "12", "50"]         # shown as it is cut
    fine = tmp_path / "goed.csv"
    fine.write_text('Inkooporder,Artikel,Aantal,Prijs,,\n250301,KM-3101,1000,"12,50",,\n250301,KM-3102,25,"4,80", ,\n', encoding="utf-8")
    assert sample_head(fine)[1:] == (1, ["Inkooporder", "Artikel", "Aantal", "Prijs"])
    assert export_trial(fine, values, "nl").reading.rows == 2


def test_a_sample_with_data_on_a_hidden_sheet_only_is_told_so(tmp_path):
    """Ruling Z33, parts 2 and 3: a sample whose only data stands on a hidden sheet is refused at once, and by the trial read, in
    words that say so."""
    from openpyxl import Workbook
    from aid.review.exact_settings import refusal_text, sample_head
    book = Workbook()
    book.active.title = "Leeg"
    hidden = book.create_sheet("Vorige week")
    for row in (["Inkooporder", "Artikel", "Aantal", "Prijs"], ["250301", "KM-3101", 1000, 12.5]):
        hidden.append(row)
    hidden.sheet_state = "hidden"
    sample = tmp_path / "orders.xlsx"
    book.save(sample)
    dutch = ("Dit Excel-bestand heeft alleen gegevens op een verborgen werkblad. De assistent leest geen verborgen werkbladen: maak het "
             "werkblad zichtbaar, of sla het op als een eigen bestand, en geef het opnieuw.")
    english = ("This Excel file holds data on a hidden sheet only. The assistant reads no hidden sheets: unhide the sheet, or save it as a "
               "file of its own, and give it again.")
    with pytest.raises(ExportRefused) as refused:
        sample_head(sample)
    assert refused.value.reason == "hidden_sheet" and (refusal_text(refused.value, "nl"), refusal_text(refused.value, "en")) == (dutch, english)
    values = {"export_columns": {"po_number": "Inkooporder", "item_code": "Artikel", "quantity": "Aantal", "unit_price": "Prijs"},
              "export_decimal_comma": True, "export_fill_down": False}
    assert export_trial(sample, values, "nl").problems == [dutch]
    assert articles_trial(sample, {"article_columns": {"item_code": "Artikel", "price": "Prijs"}, "article_decimal_comma": True}, "en").problems == [english]
