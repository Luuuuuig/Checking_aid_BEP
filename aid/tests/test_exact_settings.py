"""Tests of what the block "Koppeling met Exact (bij de installatie)" of the settings tab shows and writes (aid/review/exact_settings.py),
without a page: the first page of a sample purchase order as a table, the title row found by one of its words, the layout made from
the form and the form made from a layout, the senders, the trial read of a sample (what it reads, what is wrong with it and what the
reader passes by) and the state lines."""
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aid.madeup.generate import PO_COLUMNS, REGULAR, TRYOUT_PO_LAYOUT, Row, Text, load_specs, pdf_bytes, po_pages, tryout_po_pages
from aid.po_store import PoStore
from aid.readers.pdf_text import extract_lines
from aid.readers.po_layout import PoLayout
from aid.readers.po_pdf import layout_first_page, read_po_layout
from aid.review.exact_settings import (FORM, MEANINGS, SLOTS, SampleUnreadable, as_setting, check, form_from_layout, known, layout_from_form,
                                       line_headers, page_lines, reading_shown, senders_from, state_lines, title_words, trial, unread)
from aid.review.i18n import STRINGS
from aid.schema import DocLine, DocValue, Document
from aid.settings import Settings
from aid.sources.mailbox import titled_confirmation
from aid.tests.conftest import SETTINGS
from aid.tests.helpers import (ANDERS_FORM, ANDERS_LAYOUT, ANDERS_MEANINGS, ANDERS_PO, ERP_FORM, ERP_MEANINGS, MADEUP_LAYOUT, SAMPLES,
                               first_page)

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
    settings = SETTINGS.model_copy(update={"po_store_root": tmp_path / "store", "watched_folder": tmp_path / "inbox"})
    store, now = PoStore(settings.po_store_root, settings.authorization_limit), datetime.now(timezone.utc)
    assert state_lines(settings, store, now, "nl") == [NL["exact_state_unset"], NL["exact_state_none"], NL["exact_state_unread"].format(n=0)]
    version = store.add(madeup_out / "MU08_po_v1.pdf")
    filed = datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc).timestamp()
    os.utime(store.root / "250108" / "v1" / "po.json", (filed, filed))
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "refused.pdf").write_bytes(b"made-up")
    lines = state_lines(settings.model_copy(update={"po_layout": ANDERS_LAYOUT}), store, now + timedelta(seconds=300), "en")
    local = datetime.fromtimestamp(filed, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    assert lines == [EN["exact_state_set"], EN["exact_state_known"].format(count=1, number="250108", version=1, time=local),
                     EN["exact_state_unread"].format(n=1)]
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
