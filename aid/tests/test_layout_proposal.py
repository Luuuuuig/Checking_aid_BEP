"""Tests of the proposal of a purchase order layout from one sample PDF: proposal of aid/review/exact_settings.py, on the detection of
aid/review/layout_proposal.py, without a page. A proposal is a guess that the owner checks against the trial read, so the tests hold it
to the two made-up layouts and to the ERP report of the review, and to its rules: the supplier's line, the end text, the tick and the
sender are never proposed; the PO label comes from a known word only; the decimal mark needs proof; a title word that no list knows is
found by its cells or by arithmetic, or left open; a scan gives nothing; and the sample is read once."""
import re
from pathlib import Path

import pytest

from aid.madeup.generate import BOLD, Row, Text
from aid.review.exact_settings import (FORM, LABELS, MEANINGS, NotPdf, Proposal, SampleUnreadable, form_from_layout, layout_from_form,
                                       proposal, title_words, trial)
from aid.tests.helpers import ANDERS_FORM, ANDERS_LAYOUT, ANDERS_MEANINGS, ANDERS_PO, ERP_FORM, ERP_MEANINGS, MADEUP_LAYOUT, SAMPLES
from aid.tests.test_exact_settings import anders, drawn

NEVER = ("supplier_line", "table_end", "per_piece")          # what the owner alone gives: the proposal holds None for each
UNSAID = dict.fromkeys(NEVER)
# Titles that no word list knows, for the seven columns of the second made-up PO; the fifth is a title of two words, about as wide as
# the one it replaces, so that the range of days of one line still lies under it and the next title stands as far from it.
UNKNOWN = ("Hvh", "Mt", "Ref", "Tekst", "Verlangd moment", "Tarief", "Som")
TITLES = [(0, "Artikelcode"), (100, "Aantal"), (200, "Prijs")]                     # a small table for a page drawn by sheet
LINES = ([(0, "KM-1"), (100, "10"), (200, "12,50")], [(0, "KM-2"), (100, "4"), (200, "3,20")])


def sheet(tmp_path: Path, name: str, *rows) -> Path:
    """A made-up page: a row is one text at the left margin, or a list of (place from the left margin, text), one for each cell."""
    return drawn(tmp_path, [Row([Text(0, row)] if isinstance(row, str) else [Text(x, text) for x, text in row]) for row in rows], name)


def retitled(pages) -> None:
    """Give the title row of the second made-up PO, line 8 of its first page, the words that no list knows."""
    for text, word in zip(pages[0][7].texts, UNKNOWN, strict=True):
        text.text = word


def small_table(pages) -> None:
    """Put a table of three columns and one row above the title row of the second made-up PO, whose title row is then line 10."""
    pages[0][7:7] = [Row([Text(0, "Afleveradres", BOLD), Text(200, "Betaling", BOLD), Text(350, "Contact", BOLD)]),
                     Row([Text(0, "Voorbeeldweg 12"), Text(200, "30 dagen netto"), Text(350, "Inkoop")])]


# --- the two made-up layouts and the ERP report ---

def test_every_made_up_po_is_proposed_as_the_made_up_layout(madeup_out):
    pdfs = sorted(madeup_out.glob("MU*_po_v*.pdf"))
    assert len(pdfs) == 21
    for pdf in pdfs:
        found = proposal(pdf)
        assert isinstance(found, Proposal) and layout_from_form(found.form, found.form["columns"], "nl") == (MADEUP_LAYOUT, {}), pdf.name
        assert found.missing == [] and found.form["title_word"] == "Artikel" and found.form["numbers"] == "comma", pdf.name
        assert found.found | {"numbers": 8} == {"po_label": 1, "version_label": 2, "date_label": 3, "supplier_label": 4,
                                                "incoterm_label": 5, "currency_label": 6, "title_row": 7, "numbers": 8}, pdf.name
        assert found.found["numbers"] >= 8, pdf.name                              # a number of an order line proves the comma


def test_the_second_layout_is_proposed_but_for_the_suppliers_line_and_the_tick():
    found = proposal(ANDERS_PO)
    assert found.form == ANDERS_FORM | UNSAID | {"title_word": "Artikelcode", "columns": ANDERS_MEANINGS}
    assert list(found.form) == list(form_from_layout(None)) and list(found.form)[:len(FORM)] == list(FORM)      # the shape of the form
    assert found.found == {"po_label": 6, "date_label": 6, "incoterm_label": 7, "currency_label": 7, "numbers": 9, "title_row": 8}
    assert found.missing == ["supplier_label", "version_label"]                    # the PDF prints neither: the supplier stands on line 3
    # with the supplier's line and the tick, which the owner gives, the form is the layout that reads this PO
    assert layout_from_form(found.form | {"supplier_line": "3", "per_piece": True}, found.form["columns"], "nl") == (ANDERS_LAYOUT, {})
    # a proposal names fields and lines of the first page, never a value of the PDF
    assert all(isinstance(line, int) for line in found.found.values())
    assert {*found.found, *found.missing} <= {*LABELS, "numbers", "title_row", "columns"}


def test_the_search_of_the_title_row_finds_the_proposed_row_by_the_proposed_word(tmp_path, madeup_out):
    """The form holds a word for "Kopregel zoeken", which takes the first line of page 1 that holds the word. The proposal gives a
    word by which that search finds the proposed title row: the title of the item code, or the first word of the row that does when
    an earlier line holds that title as well."""
    for pdf, word in ((ANDERS_PO, "Artikelcode"), (madeup_out / "MU01_po_v1.pdf", "Artikel"),
                      (SAMPLES / "c01_dutch_numbers.pdf", "Artikelcode"), (anders(tmp_path, retitled, "unknown.pdf"), "Ref")):
        found = proposal(pdf)
        assert found.form["title_word"] == word and title_words(pdf, word) == found.form["title_row"], pdf.name
    earlier = sheet(tmp_path, "earlier.pdf", "Bestelnummer: X-1", "Vermeld de artikelcode op de pakbon", TITLES, *LINES)
    found = proposal(earlier)
    assert title_words(earlier, "Artikelcode") == ["Vermeld", "de", "artikelcode", "op", "de", "pakbon"]       # line 2, not the title row
    assert (found.form["title_row"], found.form["columns"]) == (["Artikelcode", "Aantal", "Prijs"],
                                                                ["item_code", "quantity", "unit_price"])
    assert found.form["title_word"] == "Aantal" and title_words(earlier, "Aantal") == found.form["title_row"]
    every = sheet(tmp_path, "every.pdf", "Bestelnummer: X-1", "Artikelcode, aantal en prijs staan per regel", TITLES, *LINES)
    assert proposal(every).form["title_word"] == "" and proposal(every).form["title_row"] == ["Artikelcode", "Aantal", "Prijs"]


def test_the_decimal_mark_is_proposed_on_proof_only(tmp_path):
    """The mark of the form is proposed when a number of the table shows one mark before one, two, four or more digits and no number
    shows the other so (the proof of the trial read); the line of the first such number is named. Without proof it is left as it is."""
    for name, numbers in (("c01_dutch_numbers.pdf", "comma"), ("c02_english_numbers.pdf", "point"), ("c03_three_decimals.pdf", "comma")):
        found = proposal(SAMPLES / name)
        assert found.form == ERP_FORM | UNSAID | {"numbers": numbers, "title_word": "Artikelcode", "columns": ERP_MEANINGS}, name
        assert found.found == {"supplier_label": 4, "po_label": 4, "date_label": 5, "currency_label": 6, "incoterm_label": 6, "numbers": 8,
                               "title_row": 7} and found.missing == ["version_label"], name
    titles = [*TITLES, (300, "Leverdatum")]

    def numbers_of(name: str, *lines) -> tuple:
        found = proposal(sheet(tmp_path, name, "Bestelnummer: X-1", titles, *lines))
        assert found.form["columns"] == ["item_code", "quantity", "unit_price", "delivery_date"], name
        return found.form["numbers"], found.found.get("numbers"), "numbers" in found.missing

    # whole numbers and a mark before three digits prove nothing, and neither does the point of a date
    assert numbers_of("unproven.pdf", [(0, "KM-1"), (100, "1.000"), (200, "12"), (300, "16.11.2026")],
                      [(0, "KM-2"), (100, "2.500"), (200, "3"), (300, "23.11.2026")]) == (None, None, True)
    # the two marks each before two digits: no proof of either
    assert numbers_of("both.pdf", [(0, "KM-1"), (100, "10"), (200, "12,50"), (300, "16.11.2026")],
                      [(0, "KM-2"), (100, "4"), (200, "0.25"), (300, "23.11.2026")]) == (None, None, True)
    # an item code is no number, whatever it looks like: the price on the second order line, line 4 of the page, proves the comma
    assert numbers_of("code.pdf", [(0, "47.11"), (100, "10"), (200, "12"), (300, "16.11.2026")],
                      [(0, "47.12"), (100, "4"), (200, "3,20"), (300, "23.11.2026")]) == ("comma", 4, False)
    assert numbers_of("point.pdf", [(0, "KM-1"), (100, "1,000"), (200, "12.50"), (300, "16.11.2026")]) == ("point", 3, False)


def test_a_layout_as_proposed_makes_the_sample_a_purchase_order_of_the_folder_for_orders(madeup_out):
    """The folder for purchase orders of the work mailbox takes a PDF for a purchase order by the saved layout alone (is_po_pdf of
    aid/sources/mailbox.py: a first page that fits it and whose header names no confirmation). So the layout that a sample proposes,
    with what the owner alone gives, takes the sample and the other orders of its layout, and no confirmation of a supplier."""
    from aid.sources.mailbox import is_po_pdf
    from aid.tests.test_mailbox import SETTINGS
    orders = sorted(madeup_out.glob("MU*_po_v*.pdf"))
    for sample, owners, others in ((madeup_out / "MU01_po_v1.pdf", {}, orders), (ANDERS_PO, {"supplier_line": "3", "per_piece": True}, []),
                                   (SAMPLES / "c01_dutch_numbers.pdf", {}, [])):
        found = proposal(sample)
        layout, problems = layout_from_form(found.form | owners, found.form["columns"], "nl")
        settings = SETTINGS.model_copy(update={"po_layout": layout})
        assert problems == {} and is_po_pdf(sample.read_bytes(), settings), sample.name
        assert all(is_po_pdf(order.read_bytes(), settings) for order in others), sample.name
        assert not any(is_po_pdf(conf.read_bytes(), settings) for conf in sorted(madeup_out.glob("MU*_conf*.pdf"))), sample.name
    assert not is_po_pdf((madeup_out / "MU01_po_v1.pdf").read_bytes(), settings)          # the ERP report's layout is another layout


# --- what a sample cannot give ---

def test_a_scan_gives_none_and_a_file_that_is_no_pdf_raises(tmp_path, madeup_out):
    assert proposal(madeup_out / "MU05_conf.pdf") is None and proposal(madeup_out / "MU16_conf.pdf") is None      # no text layer
    named = tmp_path / "inkooporder.txt"
    named.write_text("Bestelnummer: X-1\n", encoding="utf-8")
    with pytest.raises(NotPdf):
        proposal(named)
    cut = tmp_path / "cut.pdf"
    cut.write_bytes(ANDERS_PO.read_bytes()[:1500])
    for unreadable in (cut, tmp_path / "gone.pdf"):
        with pytest.raises(SampleUnreadable):
            proposal(unreadable)


def test_without_a_title_row_only_labels_are_proposed(tmp_path):
    found = proposal(anders(tmp_path, lambda pages: pages[0].__delitem__(7), "bare.pdf"))
    assert (found.form["title_row"], found.form["columns"], found.form["title_word"]) == ([], [], "")
    assert {label: found.form[label] for label in LABELS} == {label: ANDERS_FORM[label] for label in LABELS}
    assert found.found == {"po_label": 6, "date_label": 6, "incoterm_label": 7, "currency_label": 7}
    assert found.form["numbers"] is None                                           # no table, so no number that proves a mark
    assert found.missing == ["supplier_label", "version_label", "numbers", "title_row"]
    # a line of words above a line without a number is no title row: a text page gives the labels it holds and no more
    letter = proposal(sheet(tmp_path, "letter.pdf", "Bestelnummer: X-1", "Geachte leverancier", "Hierbij onze bestelling"))
    assert letter.form["po_label"] == "Bestelnummer" and letter.form["title_row"] == [] and "title_row" in letter.missing
    # a text with a digit is a value and never a label: the first day of a range of days is no label of the day after it
    days = proposal(sheet(tmp_path, "days.pdf", "Bestelnummer: X-1", [(0, "KM-1"), (100, "10"), (200, "23-11-2026 - 27-11-2026")]))
    assert days.form["date_label"] == "" and days.form["title_row"] == [] and days.found == {"po_label": 1}


# --- the labels ---

def test_the_po_label_comes_from_a_known_word_only(tmp_path):
    """With the header values printed under their labels no label stands before its value, and the one line of page 1 where a word
    stands before a word with a digit is the supplier's ("Proefleverancier S23"). A proposal that took any word before a digit would
    give that word as the PO label, and the trial read would read "S23" as the PO number without a problem. So the label is proposed
    only when it is made of the known words for a PO number, and is else left for the owner."""
    def under(pages):
        pages[0][5:7] = [Row([Text(0, "Bestelnummer", BOLD), Text(150, "Besteldatum", BOLD), Text(300, "Valuta", BOLD),
                              Text(400, "Leveringsconditie", BOLD)]),
                         Row([Text(0, "IB-2600417"), Text(150, "03-11-2026"), Text(300, "EUR"), Text(400, "FCA Voorbeeldstad")])]
    found = proposal(anders(tmp_path, under, "under.pdf"))
    assert {label: found.form[label] for label in LABELS} == dict.fromkeys(LABELS, "") and found.missing[:len(LABELS)] == list(LABELS)
    assert found.form["title_row"] == ANDERS_FORM["title_row"] and found.form["columns"] == ANDERS_MEANINGS      # the table is still found
    assert not set(LABELS) & set(found.found)

    def labelled(line: str) -> tuple[Path, Proposal]:
        pdf = sheet(tmp_path, "labelled.pdf", "Voorbeeldbedrijf B.V.", line, TITLES, *LINES)
        return pdf, proposal(pdf)

    for line, label in (("Inkooporder nr.: 4500123", "Inkooporder nr."), ("Bestelnummer 4500123", "Bestelnummer"),
                        ("Ons ordernummer: 4500123", "Ons ordernummer"), ("Purchase order no. 4500123", "Purchase order no."),
                        ("PO: 4500123", "PO"), ("Bestellnummer: 4500123", "Bestellnummer")):
        pdf, found = labelled(line)
        assert (found.form["po_label"], found.found["po_label"]) == (label, 2), line
        layout, problems = layout_from_form(found.form, found.form["columns"], "nl")
        tried = trial(pdf, layout, "nl")                                          # the label as proposed reads the number
        assert problems == {} and tried.problems == [] and tried.document.po_reference.value == "4500123", line
    for line in ("Dossier: 4500123", "Referentie 4500123", "PO Box 4500123", "Postcode: 1234 AB", "Uw ordernummer: 4500123",
                 "Ordernummer: volgt", "Order date: 03-11-2026", "Orderbevestiging 4500123"):
        found = labelled(line)[1]
        assert found.form["po_label"] == "" and found.missing[0] == "po_label" and "po_label" not in found.found, line
    # two texts of known words, each before a word with a digit: which of them is the PO label is the owner's to say
    two = proposal(sheet(tmp_path, "two.pdf", "Order 7 van 9", "Bestelnummer: 4500123", TITLES, *LINES))
    assert two.form["po_label"] == "" and two.missing[0] == "po_label"
    again = proposal(sheet(tmp_path, "again.pdf", "Bestelnummer: 4500123", "Zie bestelnummer 4500123", TITLES, *LINES))
    assert (again.form["po_label"], again.found["po_label"]) == ("Bestelnummer", 1)                # one label on two lines: its first line
    same = proposal(sheet(tmp_path, "same.pdf", "Inkooporder 4500123", "Ordernummer: 4500123", TITLES, *LINES))
    assert (same.form["po_label"], same.found["po_label"]) == ("Inkooporder", 1)                   # two labels of one number: the first


def test_a_label_known_by_its_value_is_proposed_only_where_one_reading_is_possible(tmp_path):
    """The labels of the date, the currency and the delivery terms are known by the value after them. The date of the PO is the one
    date above the table, or of several the one behind a known word; a delivery date is not the date of the PO. The currency is a
    currency code that stands alone after its label, and delivery terms begin with an Incoterm in capitals."""
    def labels(*header) -> dict:
        found = proposal(sheet(tmp_path, "header.pdf", "Bestelnummer: X-1", *header, TITLES, *LINES))
        assert found.form["title_row"] == ["Artikelcode", "Aantal", "Prijs"]
        return {label: (found.form[label], found.found[label]) for label in LABELS[1:] if found.form[label]}

    assert labels("Opgemaakt: 03-11-2026") == {"date_label": ("Opgemaakt", 2)}                    # the one date above the table
    assert labels("Leverdatum: 20-11-2026") == {}                                                # a delivery date
    assert labels("Afdrukdatum: 04-11-2026", "Besteldatum: 03-11-2026") == {"date_label": ("Besteldatum", 3)}
    assert labels("Afdrukdatum: 04-11-2026", "Vervaldatum: 03-12-2026") == {}                     # two dates, neither behind a known word
    assert labels("Valuta: EUR", "Totaal: € 100,00") == {"currency_label": ("Valuta", 2)}
    assert labels("Valuta: EUR", "Munt: USD") == {} and labels("Levering: DAP Voorbeeldstad", "Retour: EXW") == {}     # two: neither
    assert labels("Valuta: EUR", "Munt: EUR") == {"currency_label": ("Valuta", 2)}                # two labels of one value: the first
    assert labels("Datum: 03-11-2026", "Orderdatum: 03-11-2026", "Vervaldatum: 03-12-2026") == {"date_label": ("Datum", 2)}
    assert labels("Datum: 04-11-2026", "Orderdatum: 03-11-2026") == {}                            # two known labels of two dates
    assert labels("Bedragen in euro exclusief btw") == {}                                        # a code among other words
    assert labels("Levering: DAP Voorbeeldstad") == {"incoterm_label": ("Levering", 2)}
    assert labels("Zorg dat de pakbon bij de zending zit") == {}                                 # "dat" is no Incoterm
    assert labels("Versie: 2", "Leverancier: Proefleverancier S23") == {"version_label": ("Versie", 2),
                                                                        "supplier_label": ("Leverancier", 3)}
    assert labels("Wijziging: 2", "Aan: Proefleverancier S23") == {}                              # these two by a known word only
    assert labels("Versie: concept") == {}                                                       # a version is a whole number


# --- the title row and its columns ---

def test_unknown_title_words_are_found_by_their_cells_or_left_open(tmp_path):
    """Without a known word the title row is still found, by the cells under its words. A column is then known by its cells where
    one column alone holds dates, units, codes or texts of several words, and the three columns of which one is the product of the
    other two on every line are the quantity (printed whole), the price (printed with decimals) and the line amount, which is not
    read. One order line proves no product: its columns of numbers are left open."""
    found = proposal(anders(tmp_path, retitled, "unknown.pdf"))
    assert found.form["title_row"] == ["Hvh", "Mt", "Ref", "Tekst", "Verlangd", "moment", "Tarief", "Som"]
    assert found.found["title_row"] == 8 and found.form["columns"] == ANDERS_MEANINGS and found.form["title_word"] == "Ref"
    assert found.missing == ["supplier_label", "version_label"] and (found.form["numbers"], found.found["numbers"]) == ("comma", 9)

    def one_line(pages):
        retitled(pages)
        del pages[0][9:], pages[1:]
    found = proposal(anders(tmp_path, one_line, "one_line.pdf"))
    assert found.form["columns"] == [None, "unit", "item_code", "description", "delivery_date", "same", None, None]
    assert found.missing == ["supplier_label", "version_label", "columns"] and found.form["numbers"] == "comma"
    assert all(meaning is None or meaning in MEANINGS for meaning in found.form["columns"])


def test_a_meaning_that_two_columns_could_have_is_given_to_neither(tmp_path):
    """A wrong guess is worse than an open field: two columns that the list gives one field, two unknown columns of one kind, and a
    product that holds for more than one choice of columns are all left to the owner."""
    def columns(titles: list[str], *lines: list[str], step: int = 80) -> list:
        rows = [[(n * step, text) for n, text in enumerate(row)] for row in (titles, *lines)]
        found = proposal(sheet(tmp_path, "columns.pdf", "Bestelnummer: X-1", *rows))
        assert found.form["title_row"] == " ".join(titles).split()
        return found.form["columns"]

    assert columns(["Artikelcode", "Aantal", "Prijs", "Stuksprijs"], ["KM-1", "10", "12,50", "12,50"], ["KM-2", "4", "3,20", "3,20"]) == [
        "item_code", "quantity", None, None]                                       # two titles of the unit price
    assert columns(["Ref", "Alias", "Aantal"], ["KM-1", "LV-100", "10"], ["KM-2", "LV-200", "4"]) == [None, None, "quantity"]    # two codes
    assert columns(["Ref", "Aantal", "Soort"], ["KM-1", "10", "st"], ["KM-2", "4", "m"]) == ["item_code", "quantity", "unit"]
    # a letter and a number without a space can be a week and can be a code: "W48" is neither here, "week 48" is a week
    assert columns(["Ref", "Aantal", "Wens"], ["KM-1", "10", "W48"], ["KM-2", "4", "W49"]) == ["item_code", "quantity", None]
    assert columns(["Ref", "Aantal", "Wens"], ["KM-1", "10", "week 48"], ["KM-2", "4", "W49"]) == ["item_code", "quantity",
                                                                                                   "delivery_date"]
    # the price times one is the net price, and the quantity times either is the amount: no one product, so nothing by arithmetic
    assert columns(["Artikelcode", "Stuks", "Tarief", "Basis", "Schoon", "Som"], ["KM-1", "10", "12,50", "1", "12,50", "125,00"],
                   ["KM-2", "4", "3,20", "1", "3,20", "12,80"]) == ["item_code", None, None, None, None, None]
    # one product on every line, but both factors printed whole: the amount is known, the quantity and the price are not
    assert columns(["Artikelcode", "Stuks", "Tarief", "Som"], ["KM-1", "10", "12", "120"], ["KM-2", "4", "3", "12"]) == [
        "item_code", None, None, "skip"]
    assert columns(["Artikelcode", "Stuks", "Tarief", "Som"], ["KM-1", "10", "12,50", "125,00"], ["KM-2", "4", "3,20", "12,80"]) == [
        "item_code", "quantity", "unit_price", "skip"]
    # a title of several words is known as a whole, never by one of its words: "Artikel groep" is no item code
    assert columns(["Artikel groep", "Artikelcode", "Aantal"], ["Kranen", "KM-1", "10"], ["Meters", "KM-2", "4"], step=120) == [
        None, "same", "item_code", "quantity"]
    assert columns(["Prijs per eenheid", "Artikelcode", "Aantal"], ["12,50", "KM-1", "10"], ["3,20", "KM-2", "4"], step=120) == [
        "unit_price", "same", "same", "item_code", "quantity"]


def test_of_two_tables_the_one_with_known_titles_is_taken(tmp_path):
    found = proposal(anders(tmp_path, small_table, "two.pdf"))
    assert (found.form["title_row"], found.found["title_row"], found.form["columns"]) == (ANDERS_FORM["title_row"], 10, ANDERS_MEANINGS)
    assert found.found["po_label"] == 6 and found.missing == ["supplier_label", "version_label"]
    # without a known word the line is taken that has the most of its words above a word of the line right under it: seven of the
    # titles of the order lines stand above a cell of the first order line, and the small table has three titles
    found = proposal(anders(tmp_path, lambda pages: (retitled(pages), small_table(pages)), "two_unknown.pdf"))
    assert (found.form["title_row"][:3], found.found["title_row"], found.form["columns"]) == (["Hvh", "Mt", "Ref"], 10, ANDERS_MEANINGS)
    # and so it is with one order line, where the small table has more words under its titles down the page (8) than the other (7)
    found = proposal(anders(tmp_path, lambda pages: (retitled(pages), small_table(pages), pages[0].__delitem__(slice(11, None)),
                                                     pages.__delitem__(slice(1, None))), "two_unknown_short.pdf"))
    assert (found.form["title_row"][:3], found.found["title_row"]) == (["Hvh", "Mt", "Ref"], 10)
    # a name above a line with a number is no title row: its words stand a space apart, the titles of two columns further
    named = sheet(tmp_path, "named.pdf", "Voorbeeldbedrijf B.V.", "Voorbeeldweg 12", "1234 AB Voorbeeldstad", "Bestelnummer: X-1",
                  "Besteldatum: 03-11-2026", [(0, "Ref"), (100, "Stuks"), (200, "Tarief")], [(0, "KM-1"), (100, "10"), (200, "12,50")])
    found = proposal(named)
    assert (found.form["title_row"], found.found["title_row"], found.form["columns"]) == (["Ref", "Stuks", "Tarief"], 6,
                                                                                          ["item_code", None, None])
    # and neither is a sentence, whatever known words it holds
    told = proposal(sheet(tmp_path, "told.pdf", "Prijs per eenheid exclusief btw", "Bestelnummer: X-1",
                          [(0, "Artikelcode"), (100, "Aantal"), (200, "Tarief")], [(0, "KM-1"), (100, "10"), (200, "12,50")]))
    assert (told.form["title_row"], told.found["title_row"], told.form["columns"]) == (["Artikelcode", "Aantal", "Tarief"], 3,
                                                                                       ["item_code", "quantity", None])
    only = proposal(sheet(tmp_path, "only.pdf", "Prijs per eenheid exclusief btw", "Bestelnummer: X-1"))
    assert only.form["title_row"] == [] and only.missing[-1] == "title_row"


def test_a_title_row_in_doubt_is_left_open(tmp_path):
    """A wrong guess is worse than an open field. A small table with listed titles above order lines whose titles no list knows is
    one of two lines that can be the title row, and the known titles and the cells under the lines point at different ones; a title
    row printed over two lines ("Unit" over "price") is no line of its own. In both the title row is left to the owner."""
    amounts = ([(0, "KM-1"), (100, "10"), (200, "12,50"), (300, "125,00")], [(0, "KM-2"), (100, "4"), (200, "3,20"), (300, "12,80")])
    earlier = sheet(tmp_path, "earlier_orders.pdf", "Bestelnummer: X-1", "Nog te leveren uit eerdere orders",
                    [(0, "Artikelcode"), (100, "Aantal")], [(0, "OUD-7"), (100, "2")],
                    [(0, "Code"), (100, "Stuks"), (200, "Tarief"), (300, "Som")], *amounts)
    split = sheet(tmp_path, "split.pdf", "Bestelnummer: X-1", [(0, "Item"), (100, "Qty"), (200, "Unit"), (300, "Line")],
                  [(200, "price"), (300, "total")], *amounts)
    for pdf in (earlier, split):
        found = proposal(pdf)
        assert (found.form["title_row"], found.form["columns"], found.form["title_word"]) == ([], [], ""), pdf.name
        assert "title_row" not in found.found and found.missing[-1] == "title_row", pdf.name
        assert found.form["po_label"] == "Bestelnummer" and found.found["po_label"] == 1, pdf.name


def test_the_end_text_the_suppliers_line_the_tick_and_the_sender_are_never_proposed(tmp_path, madeup_out):
    """The second made-up PO has all three for the taking: the supplier alone on line 3, "Wordt vervolgd op pagina 2" under the last
    order line of page 1, which as an end text would end the table there, and no column for a price basis. They are the owner's."""
    total = sheet(tmp_path, "total.pdf", "Voorbeeldbedrijf B.V.", "Proefleverancier S23", "Bestelnummer: X-1", TITLES, *LINES,
                  [(0, "Totaal exclusief btw"), (200, "137,80")])
    for pdf in (ANDERS_PO, madeup_out / "MU01_po_v1.pdf", madeup_out / "MU04_po_v1.pdf", SAMPLES / "c01_dutch_numbers.pdf", total,
                anders(tmp_path, retitled, "unknown.pdf")):
        found = proposal(pdf)
        assert {field: found.form[field] for field in NEVER} == UNSAID, pdf.name
        assert not set(NEVER) & {*found.found, *found.missing}, pdf.name
        assert set(found.form) == set(form_from_layout(None)) and not any("sender" in key for key in found.form), pdf.name


# --- how it is built ---

def test_a_proposal_reads_the_pdf_once_and_its_first_page_only(monkeypatch):
    """One extraction of the text of the PDF for a whole proposal, as for a trial read (ruling L5); the detection is given the rows.
    The proposal looks at page 1 alone, so it reads page 1 alone (pages=[1] of extract_rows), as the folder for purchase orders reads a
    PDF (is_po_pdf of aid/sources/mailbox.py): a long sample is proposed in the time of one page, and the words of its later pages are
    never read. The second made-up PO has two pages."""
    import aid.readers.pdf_text as pdf_text
    import aid.readers.po_pdf as po_pdf
    import aid.review.exact_settings as exact
    import aid.review.layout_proposal as layout_proposal
    calls, opened, real, real_open = [], [], pdf_text.extract_rows, pdf_text.pdfplumber.open
    read, words = [], pdf_text.pdfplumber.page.Page.extract_words

    def counted(pdf, cut_cells=False, pages=None):
        calls.append((Path(pdf).name, cut_cells, pages))
        return real(pdf, cut_cells, pages)

    def counted_open(pdf, *args, **kwargs):
        opened.append(Path(pdf).name)
        return real_open(pdf, *args, **kwargs)

    for module in (pdf_text, po_pdf, exact):
        monkeypatch.setattr(module, "extract_rows", counted)
    monkeypatch.setattr(pdf_text.pdfplumber, "open", counted_open)
    monkeypatch.setattr(pdf_text.pdfplumber.page.Page, "extract_words",
                        lambda page, *args, **kwargs: read.append(page.page_number) or words(page, *args, **kwargs))
    assert proposal(ANDERS_PO).form["columns"] == ANDERS_MEANINGS
    assert calls == [("ANDERS_po_v1.pdf", True, [1])] and opened == ["ANDERS_po_v1.pdf"] and read == [1]
    read.clear()
    assert len(trial(ANDERS_PO, ANDERS_LAYOUT, "nl").document.lines) > 0 and read == [1, 2]       # the trial read reads every page
    assert not hasattr(layout_proposal, "extract_rows")


def test_the_module_imports_no_private_name_of_the_reader():
    """Ruling L13: the detection takes the reader's two helpers that cut a row into the cells under a title row by their public
    names; the names with an underscore stay as aliases. It is built without the page library and without the page's texts."""
    import aid.readers.po_pdf as po_pdf
    import aid.review.exact_settings as exact
    import aid.review.layout_proposal as layout_proposal
    for public, private in (("title_spans", "_title_spans"), ("cells_under", "_cells_under")):
        assert getattr(po_pdf, private) is getattr(po_pdf, public), public
    source = Path(layout_proposal.__file__).read_text(encoding="utf-8")
    assert "cells_under" in source and "title_spans" in source
    assert not re.search(r"from aid\.[\w.]+ import \(?[^)]*\b_\w", source)
    assert not re.search(r"gradio|aid\.review\.(page|i18n|exact_settings)", source)
    assert not re.search(r"from aid\.(readers\.po_pdf|po_store) import \(?[^)]*\b_\w", Path(exact.__file__).read_text(encoding="utf-8"))


def test_the_word_lists_are_plain_data_that_names_each_title_once():
    """The owner extends the lists. An entry is compared without regard to case and without a final full stop or colon, so one written
    otherwise would never be found; and a title under two meanings would be found under the first only."""
    import aid.review.layout_proposal as layout_proposal
    lists = {name: getattr(layout_proposal, name) for name in ("PO_WORDS", "NUMBER_WORDS", "DATE_LABELS", "VERSION_LABELS",
                                                               "SUPPLIER_LABELS", "UNITS")}
    lists |= {f"TITLES[{meaning}]": titles for meaning, titles in layout_proposal.TITLES.items()}
    for name, words in lists.items():
        assert isinstance(words, tuple) and words and len(set(words)) == len(words), name
        for word in words:
            assert word == " ".join(part.casefold().rstrip(".:") for part in word.split()) and word, (name, word)
    titles = [title for words in layout_proposal.TITLES.values() for title in words]
    assert len(set(titles)) == len(titles)
    assert set(layout_proposal.TITLES) == set(MEANINGS) - {"same"}
    assert not set(layout_proposal.PO_WORDS) & set(layout_proposal.NUMBER_WORDS)
    assert all(term.isupper() and len(term) == 3 for term in layout_proposal.INCOTERMS)
    for meaning, dutch, english, german in (("quantity", "aantal", "quantity", "menge"), ("unit_price", "prijs", "price", "preis"),
                                            ("item_code", "artikelcode", "item code", "artikelnummer"),
                                            ("delivery_date", "leverdatum", "delivery date", "liefertermin")):
        assert {dutch, english, german} <= set(layout_proposal.TITLES[meaning]), meaning
    assert {"inkooporder", "bestelnummer", "ordernummer", "purchase", "bestellnummer"} <= set(layout_proposal.PO_WORDS)
