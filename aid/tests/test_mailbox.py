"""Tests of the watched folder source (aid/sources/mailbox.py) on the made-up documents, and on e-mails and variants of them
that the tests build with the standard library and the generator. Every address is made up (example.com)."""
import csv
import io
import json
import mimetypes
import os
import re
import shutil
import tempfile
import unicodedata
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import pytest
from openpyxl import Workbook
from reportlab.pdfbase.pdfmetrics import stringWidth

from aid.case import find_case_by_hash, load_case, sha256_file
from aid.decider import NoDecider
from aid.madeup.generate import TRYOUT_EXPORT_COLUMNS, Row, Text, confirmation_pages, export_csv, load_specs, pdf_bytes, po_pages
from aid.po_store import PoStore
from aid.readers.pdf_text import extract_lines
from aid.readers.po_pdf import fits
from aid.readers.supplier_parser import load_templates
from aid.sources import mailbox
from aid.sources.mailbox import (CASE_ID_TRIES, CONTEXT_CHARS, KEYWORDS, NOT_AN_EXPORT, TITLE_CLASSES, TITLE_LINES, ScanResult,
                                 classify_attachment, extract_eml, find_po_numbers, held_numbers, scan_folder, sender_of, titled_confirmation)
from aid.tests.conftest import REPO_ROOT, SETTINGS
from aid.tests.fakes import FakeDecider
from aid.tests.helpers import ANDERS_LAYOUT, ANDERS_PO, EXPORT_COLUMNS, MADEUP_LAYOUT, first_page

SPECS = {spec.case_id: spec for spec in load_specs(Path(__file__).resolve().parents[1] / "madeup" / "spec")}


def write_eml(path: Path, subject: str = "", body: str | None = "", html: str | None = None, attachments: list = (),
              sender: str | None = "supplier@example.com") -> Path:
    """An e-mail at `path`, built with the standard library from made-up addresses: this subject, this plain-text body (None for
    none), this HTML body (None for none) and these attachments, from this sender (None for an e-mail without a From header). An
    attachment is a file, attached under its own name, or a pair (name, bytes), where the name None attaches the bytes without a
    name, or a triple (name, bytes, content type). Without a type, the type is the one the name suggests, else PDF. A pair
    (name, EmailMessage) attaches the message itself, as a mail client does when it forwards a message as an attachment."""
    message = EmailMessage()
    if sender is not None:
        message["From"] = sender
    message["To"], message["Subject"] = "aid@example.com", subject
    if body is not None:
        message.set_content(body)
        if html is not None:
            message.add_alternative(html, subtype="html")
    elif html is not None:
        message.set_content(html, subtype="html")
    for attachment in attachments:
        name, data, *content_type = (attachment.name, attachment.read_bytes()) if isinstance(attachment, Path) else attachment
        if isinstance(data, EmailMessage):
            message.add_attachment(data, **({"filename": name} if name else {}))
            continue
        kind, _, subtype = (content_type or [mimetypes.guess_type(name or "")[0] or "application/pdf"])[0].partition("/")
        message.add_attachment(data, maintype=kind, subtype=subtype, **({"filename": name} if name else {}))
    path.write_bytes(message.as_bytes())
    return path


def test_classify(madeup_out):
    assert classify_attachment(madeup_out / "MU01_conf.pdf", None) == "confirmation" and classify_attachment(madeup_out / "MU01_po_v1.pdf", None) == "po_pdf"
    assert classify_attachment(Path("x.pdf"), "Pakbon nr 5") == "packing_slip" and classify_attachment(Path("x.pdf"), "Rechnung 77") == "invoice"


def test_find_po_numbers():
    assert find_po_numbers("Uw inkooporder 250101 en PO 250102", SETTINGS.po_number_pattern) == ["250101", "250102"]


def test_scan_opens_one_case_and_skips_duplicate(tmp_path, madeup_out):
    inbox = tmp_path / "inbox"; inbox.mkdir(); shutil.copy(madeup_out / "MU01_po_v1.pdf", inbox); shutil.copy(madeup_out / "MU01_conf.pdf", inbox)
    r = scan_folder(inbox, PoStore(tmp_path / "store"), tmp_path / "cases", SETTINGS, datetime(2026, 11, 16, 8, 0))
    assert r.stored_pos == ["250101"] and len(r.new_cases) == 1 and (inbox / "processed" / "MU01_conf.pdf").exists()
    shutil.copy(madeup_out / "MU01_conf.pdf", inbox / "again.pdf")
    r2 = scan_folder(inbox, PoStore(tmp_path / "store"), tmp_path / "cases", SETTINGS, datetime(2026, 11, 16, 9, 0))
    assert r2.new_cases == [] and r2.skipped == [("again.pdf", "duplicate")]


def test_the_attachments_of_an_email_are_unpacked_under_the_cases_root_never_in_the_temporary_folder(tmp_path, madeup_out, monkeypatch):
    """A scan unpacks an e-mail's attachments into a folder of its own under <cases_root>/tmp/work, made when the scan has an e-mail,
    and removes it when the scan is done, with the folders it made on the way: a document of a case never lies in the system's
    temporary folder, which is outside the aid's folders. A scan without an e-mail makes no folder for it."""
    (system := tmp_path / "system temp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system))
    unpacked, unpack = [], mailbox._Scan.unpack

    def noting(self, number, path, scratch):
        unpacked.append((path.name, scratch, scratch is not None and scratch.is_dir()))
        return unpack(self, number, path, scratch)
    monkeypatch.setattr(mailbox._Scan, "unpack", noting)
    inbox, cases = tmp_path / "inbox", tmp_path / "cases"
    inbox.mkdir()
    shutil.copy(madeup_out / "MU01_po_v1.pdf", inbox)
    write_eml(inbox / "confirmation.eml", "Orderbevestiging PO 250101", "made-up body", attachments=[madeup_out / "MU01_conf.pdf"])
    result = scan_folder(inbox, PoStore(tmp_path / "store"), cases, SETTINGS, datetime(2026, 11, 16, 8, 0))
    assert len(result.new_cases) == 1 and result.stored_pos == ["250101"]
    assert [name for name, _, _ in unpacked] == ["MU01_po_v1.pdf", "confirmation.eml"]
    assert all(scratch.parent == cases / "tmp" / "work" and seen for _, scratch, seen in unpacked)
    assert unpacked[0][1].name.startswith("aid_mailbox_") and not (cases / "tmp").exists() and list(system.iterdir()) == []
    shutil.copy(madeup_out / "MU02_po_v1.pdf", inbox)
    scan_folder(inbox, PoStore(tmp_path / "store"), cases, SETTINGS, datetime(2026, 11, 16, 9, 0))
    assert unpacked[-1] == ("MU02_po_v1.pdf", None, False) and not (cases / "tmp").exists()


def test_two_po_numbers_are_not_a_case(tmp_path, madeup_out):
    inbox = tmp_path / "inbox"; inbox.mkdir(); write_eml(inbox / "m.eml", subject="Bevestiging PO 250101 en PO 250102", attachments=[madeup_out / "MU01_conf.pdf"])
    r = scan_folder(inbox, PoStore(tmp_path / "store"), tmp_path / "cases", SETTINGS, datetime(2026, 11, 16, 8, 0))
    assert r.new_cases == [] and r.skipped[0][1] == "more than one PO number" and (tmp_path / "cases" / "review_items.jsonl").exists()


# The tests below cover what the rulings add to the brief's four tests above.

NOW = datetime(2026, 11, 16, 8, 0)


# --- find_po_numbers and the default PO number pattern ---

@pytest.mark.parametrize("text, number", [
    ("Uw inkooporder 250101, versie 1", "250101"),           # the nl confirmations print the reference like this
    ("Ihre Bestellung 250104", "250104"),                    # the de ones
    ("Your PO 250102", "250102"),                            # the en ones
    ("UW INKOOPORDER 250101", "250101"), ("uw inkooporder 250101", "250101"), ("IHRE BESTELLUNG 250104", "250104"),
    ("Inkooporder 250101", "250101"),                        # the heading of a PO PDF
    ("po 250101", "250101"), ("PO250101", "250101"), ("PO: 250101", "250101"), ("PO #250101", "250101"), ("Order-250101", "250101"),
    ("Bestelling 25010123", "25010123"), ("Bestellung\n250104", "250104"),
])
def test_the_default_pattern_finds_a_po_number_in_every_phrase_of_the_made_up_documents(text, number):
    assert find_po_numbers(text, SETTINGS.po_number_pattern) == [number]


@pytest.mark.parametrize("text", ["PO 12345", "Orderbevestiging", "Datum: 16-11-2026", "Uw referentie KM-1001", "Bestellung", ""])
def test_the_default_pattern_finds_no_po_number_where_there_is_none(text):
    assert find_po_numbers(text, SETTINGS.po_number_pattern) == []


def test_the_default_pattern_alone_does_not_depend_on_the_flags_of_find_po_numbers():
    """Whoever uses the setting directly gets the same case-insensitive match."""
    assert re.findall(SETTINGS.po_number_pattern, "Uw inkooporder 250101 en ihre bestellung 250104") == ["250101", "250104"]


def test_a_po_number_is_listed_once_and_in_the_order_of_its_first_appearance():
    text = "PO 250103, Ihre Bestellung 250101, nochmals PO 250103 und po 250102 sowie Order 250101"
    assert find_po_numbers(text, SETTINGS.po_number_pattern) == ["250103", "250101", "250102"]


def test_find_po_numbers_uses_the_pattern_it_is_given_without_regard_to_case():
    assert find_po_numbers("Ref AB-123456 and ref ab-654321", r"ab-(\d+)") == ["123456", "654321"]
    assert find_po_numbers("Ref AB-123456", r"AB-\d+") == ["AB-123456"]                  # no group: the whole match
    assert find_po_numbers("Ref AB-123456", r"(x\d+)?AB-(\d+)") == []                    # the first group is the number; it matched nothing


# --- documents drawn by the tests ---

def page_of(*lines: str) -> list[Row]:
    """A page of text rows, one per line."""
    return [Row([Text(0, line)]) for line in lines]


def drawn(path: Path, *pages: list[Row]) -> Path:
    """A PDF with a text layer at `path`, drawn by the generator from these pages."""
    path.write_bytes(pdf_bytes(list(pages), path.name))
    return path


# --- classification ---

@pytest.mark.parametrize("doc_class, keyword", [(doc_class, word) for doc_class, words in KEYWORDS.items() for word in words])
def test_every_keyword_gives_its_class(doc_class, keyword):
    assert classify_attachment(Path("x.pdf"), f"{keyword} 250101\nS01") == doc_class
    assert classify_attachment(Path("x.pdf"), f"{keyword.upper()} 250101\nS01") == doc_class
    if doc_class not in TITLE_CLASSES:                                                  # the keywords of a PO count at the start of a title line only
        assert classify_attachment(Path("x.pdf"), f"S01\nAdres\nTelefoon\nzie {keyword} 250101") == doc_class


def test_the_keywords_and_the_order_in_which_the_classes_are_tried():
    assert KEYWORDS == {
        "confirmation": ["orderbevestiging", "opdrachtbevestiging", "auftragsbestätigung", "bestellbestätigung", "order confirmation",
                         "order acknowledgement", "order acknowledgment", "acknowledgement of order"],
        "po_pdf": ["inkooporder", "purchase order", "bestellung"],
        "packing_slip": ["pakbon", "lieferschein", "packing slip", "delivery note"],
        "invoice": ["factuur", "rechnung", "invoice"], "quotation": ["offerte", "angebot", "quotation", "quote"]}
    assert list(KEYWORDS) == ["confirmation", "po_pdf", "packing_slip", "invoice", "quotation"]      # the order in which they are tried
    assert TITLE_LINES == 3 and TITLE_CLASSES == ("po_pdf",)


UNTITLED = "S01\nAdres\nTelefoon\n"                 # three lines that name no kind of document: the title lines of a page


@pytest.mark.parametrize("text", ["Auftragsbestätigung", "AUFTRAGSBESTÄTIGUNG", "Auftragsbestatigung", "auftragsbestatigung",
                                  unicodedata.normalize("NFD", "Auftragsbestätigung"), "ORDER\nCONFIRMATION", "Order   confirmation"])
def test_keywords_are_found_without_regard_to_case_accents_and_spacing(text):
    assert classify_attachment(Path("x.pdf"), text) == "confirmation"
    assert classify_attachment(Path("x.pdf"), "Factuur 77\nAdres\nTelefoon\n" + text) == "confirmation"      # and below a title line that begins with another keyword


def test_an_accent_in_the_text_does_not_hide_a_keyword():
    assert classify_attachment(Path("x.pdf"), "Fáctuur 5") == "invoice" and classify_attachment(Path("x.pdf"), "Pàkbon 5") == "packing_slip"


@pytest.mark.parametrize("text, expected", [
    ("S01\nOrderbevestiging\nUw inkooporder 250101\nPakbon en factuur volgen, zie offerte", "confirmation"),   # a confirmation names the PO and more
    ("Inkooporder 250101\nS01\nLevering DAP\nPakbon verplicht\nFactuuradres S01\nInvoice address\nOfferte 7", "po_pdf"),
    (UNTITLED + "Pakbon 5 bij factuur 77, offerte 12 en inkooporder 250101", "packing_slip"),
    (UNTITLED + "Factuur 77 bij offerte 12 en inkooporder 250101", "invoice"),
    (UNTITLED + "Offerte 12 op uw inkooporder 250101", "quotation"),
    ("Algemene voorwaarden", "other"), ("", "other"),
])
def test_the_first_class_in_the_order_with_a_keyword_on_the_page_wins(text, expected):
    assert classify_attachment(Path("x.pdf"), text) == expected


@pytest.mark.parametrize("lines, expected", [
    (["Inkooporder 250101", "S01", "Pakbon 5"], "po_pdf"),                                    # the PO word begins the first line
    (["S01", "Adres", "Inkooporder: 250101"], "po_pdf"),                                       # or the third
    (["S01", "Adres", "INKOOPORDER 250101"], "po_pdf"),
    (["S01", "Adres", "Uw inkooporder 250101"], "other"),                                      # inside a line it only mentions the PO
    (["S01", "Adres", "Telefoon", "Inkooporder 250101"], "other"),                             # the fourth line is not a title line
    (["", "S01", "  ", "Adres", "Inkooporder 250101"], "po_pdf"),                              # blank lines are not lines
    (["S01", "Adres", "Telefoon", "", "  ", "Inkooporder 250101"], "other"),
    (["S01", "Pakbon 5", "Uw inkooporder 250101"], "packing_slip"),                            # the PO is only named: the packing slip is what the title says
    (["S01", "Adres", "Telefoon", "Pakbon 5", "Uw inkooporder 250101"], "packing_slip"),
])
def test_a_po_keyword_counts_at_the_start_of_one_of_the_first_three_lines_only(lines, expected):
    assert classify_attachment(Path("x.pdf"), "\n".join(lines)) == expected


@pytest.mark.parametrize("lines, expected", [
    (["S01", "Factuur 77", "Uw inkooporder 250101", "Totaal EUR 192,00"], "invoice"),                  # a Dutch invoice that cites the PO on line 3
    (["S01", "Adres", "Uw inkooporder 250101", "Telefoon", "Factuur 77"], "invoice"),                  # with its title further down
    (["Invoice", "Purchase order: 250102", "Date: 2026-11-16"], "invoice"),                            # an English invoice that cites it on line 2
    (["Factuur 77", "Pakbon 5", "Inkooporder 250101"], "invoice"),                                      # the earliest line that begins with a keyword decides
    (["Offerte 12", "Pakbon 5", "Factuur 7"], "quotation"),                                             # also where the order of the classes would choose another
    (["Factuur 7", "Pakbon 5"], "invoice"),
    (["Purchase order: 250102", "Invoice"], "po_pdf"),                                                  # and for a PO
    (["Pakbon 5", "Invoice address: Main Street 1"], "packing_slip"),
])
def test_the_earliest_title_line_that_begins_with_a_keyword_decides(lines, expected):
    assert classify_attachment(Path("x.pdf"), "\n".join(lines)) == expected


@pytest.mark.parametrize("line", ["Factuuradres: Hoofdstraat 1", "Rechnungsadresse: Hauptstrasse 1", "Inkoopordernummer 5", "Pakbonnummer 5", "Purchase orders",
                                  "Bestellungen 5"])
def test_a_title_line_must_begin_with_a_whole_word(line):
    assert classify_attachment(Path("x.pdf"), f"{line}\nS01\nAdres") == "other"


def test_a_confirmation_keyword_anywhere_on_the_page_still_ranks_first():
    assert classify_attachment(Path("x.pdf"), "Factuur 77\nInkooporder 250101\nTelefoon\nBetreft: orderbevestiging 12") == "confirmation"
    assert classify_attachment(Path("x.pdf"), "Purchase order confirmation 4500123\nACME Ltd") == "confirmation"      # a title line that begins with the words of a PO


def test_the_keywords_of_the_other_classes_count_anywhere_on_the_page():
    lines = ["S01", "Adres", "Telefoon", "Postbus", "Plaats", "KvK", "BTW", "Pakbon 5"]
    assert classify_attachment(Path("x.pdf"), "\n".join(lines)) == "packing_slip"


@pytest.mark.parametrize("text", ["Factuuradres: Hoofdstraat 1", "Rechnungsadresse: Hauptstrasse 1", "Pakbonnummer 5", "Lieferscheinnummer 5",
                                  "Invoices", "Quoted price", "Orderbevestigingen", "Offertes",              # a longer word that starts with the keyword
                                  "Voorfactuur 5", "Preorder confirmation", "Sonderangebot"])               # and one that ends with it
def test_a_keyword_must_be_a_whole_word(text):
    assert classify_attachment(Path("x.pdf"), UNTITLED + text) == "other"


@pytest.mark.parametrize("text, expected", [
    ("Factuur: 77", "invoice"), ("(Factuur)", "invoice"), ("Factuur-77", "invoice"), ("FACTUUR", "invoice"), ("Rechnung/Invoice", "invoice"),
    ("Pakbon, nr 5", "packing_slip"), ("Offerte.", "quotation"), ("Orderbevestiging!", "confirmation"), ("-- Delivery note --", "packing_slip"),
])
def test_a_keyword_next_to_punctuation_is_still_a_word(text, expected):
    assert classify_attachment(Path("x.pdf"), UNTITLED + text) == expected


def test_a_keyword_is_text_and_not_an_expression_and_may_start_or_end_with_punctuation(monkeypatch):
    monkeypatch.setitem(KEYWORDS, "invoice", ["pro-forma (nr.)", "c.o.d."])          # a keyword that a later task or setting could add
    assert classify_attachment(Path("x.pdf"), UNTITLED + "Pro-forma (nr.) 5") == "invoice"
    assert classify_attachment(Path("x.pdf"), UNTITLED + "Cash c.o.d. only") == "invoice"
    assert classify_attachment(Path("x.pdf"), UNTITLED + "Pro-forma (nrX) 5") == "other"                 # a dot is a dot
    assert classify_attachment(Path("x.pdf"), UNTITLED + "cXoXdX") == "other"
    assert classify_attachment(Path("x.pdf"), UNTITLED + "Xc.o.d. and c.o.d.s") == "other"               # and the word does not touch letters
    assert classify_attachment(Path("x.pdf"), "Pro-forma (nr.) 5\nS01") == "invoice"                      # the same where a title line begins with it
    assert classify_attachment(Path("x.pdf"), "c.o.d. only\nS01") == "invoice"
    assert classify_attachment(Path("x.pdf"), "Pro-forma (nrX) 5\nS01") == "other" and classify_attachment(Path("x.pdf"), "cXoXdX\nS01") == "other"
    assert classify_attachment(Path("x.pdf"), "c.o.d.s only\nS01") == "other"


def test_a_dutch_po_page_with_words_of_other_classes_is_still_a_po(tmp_path):
    spec = SPECS["MU01"]
    [page] = po_pages(spec, spec.po.versions[0])
    page += page_of("Factuuradres: S01 Hoofdstraat 1", "Rechnungsadresse: Hauptstrasse 1", "Pakbon verplicht", "Offerte nr 7 is akkoord")
    assert classify_attachment(drawn(tmp_path / "po.pdf", page), None) == "po_pdf"


def test_an_english_po_page_with_an_invoice_address_is_a_po():
    page = "Purchase order 4500123\nSupplier: ACME Ltd\nDelivery: DAP Rotterdam\nInvoice address: Main Street 1\nDelivery note must accompany the goods"
    assert classify_attachment(Path("x.pdf"), page) == "po_pdf"
    assert classify_attachment(Path("x.pdf"), "ACME Ltd\nPurchase order 4500123\nInvoice address: Main Street 1") == "po_pdf"       # also with the invoice words in the title lines


def test_a_german_confirmation_titled_bestellbestaetigung_is_a_confirmation_and_not_a_po():
    page = "S04\nBestellbestätigung\nIhre Bestellung 250104\nDatum: 23.10.2026"
    assert classify_attachment(Path("x.pdf"), page) == "confirmation"
    assert classify_attachment(Path("x.pdf"), "Bestellung 250104\nS04") == "po_pdf"                  # the same word as a title is a PO


def test_a_confirmation_that_names_the_po_is_not_a_po_pdf(madeup_out):
    for name in ("MU01_conf.pdf", "MU02_conf.pdf", "MU04_conf.pdf"):                  # the nl, en and de confirmations all print "inkooporder", "PO" or "Bestellung"
        assert classify_attachment(madeup_out / name, None) == "confirmation", name


def test_every_made_up_pdf_is_classified_and_a_scan_has_no_class(madeup_out):
    scans = {f"{case_id}_conf.pdf" for case_id, spec in SPECS.items() if spec.confirmation and spec.confirmation.layout == "scan"}
    classes = {path.name: classify_attachment(path, None) for path in sorted(madeup_out.glob("*.pdf"))}
    assert scans == {"MU05_conf.pdf", "MU16_conf.pdf"} and len(classes) == 41
    assert classes == {name: "po_pdf" if "_po_v" in name else "other" if name in scans else "confirmation" for name in classes}


def test_every_made_up_document_names_its_own_po_number_and_no_other(madeup_out):
    """The default PO number pattern reads the nl, de and en phrases of all the made-up confirmations and the heading of the POs."""
    checked = 0
    for path in sorted(madeup_out.glob("*.pdf")):
        first_page = "\n".join(line.text for line in extract_lines(path) if line.page == 1)
        if first_page:                                                                                   # the scans (MU05, MU16) have no text
            assert find_po_numbers(first_page, SETTINGS.po_number_pattern) == [f"2501{path.name[2:4]}"], path.name
            checked += 1
    assert checked == 39


def test_the_class_comes_from_the_first_page_alone(tmp_path):
    pdf = drawn(tmp_path / "two_pages.pdf", page_of("Inkooporder 250101"), page_of("Factuur", "Pakbon"))
    assert classify_attachment(pdf, None) == "po_pdf"
    unclear = drawn(tmp_path / "unclear.pdf", page_of("Algemene voorwaarden"), page_of("Orderbevestiging"))
    assert classify_attachment(unclear, None) == "other"


def test_a_png_has_no_first_page_to_read(madeup_out):
    assert classify_attachment(madeup_out / "CART02.png", None) == "other"


# --- the decider ---

def test_a_scan_goes_to_the_decider_with_its_file_name_and_the_text_of_the_e_mail(madeup_out):
    decider = FakeDecider("confirmation", 0.9)
    assert classify_attachment(madeup_out / "MU05_conf.pdf", None, decider, "Bijgaand de bevestiging van PO 250105") == "confirmation"
    [decision] = decider.decisions
    assert decision.kind == "document_type" and decision.options == ["po_pdf", "confirmation", "packing_slip", "invoice", "quotation", "other"]
    assert decision.context["file_name"] == "MU05_conf.pdf" and decision.context["email_text"] == "Bijgaand de bevestiging van PO 250105"
    assert "first_page" not in decision.context                           # a scan has no text on its page
    assert set(decision.context["what_the_options_mean"]) == set(decision.options)


def test_without_an_e_mail_the_decider_gets_no_e_mail_text(madeup_out):
    decider = FakeDecider("invoice", 1.0)
    assert classify_attachment(madeup_out / "CART02.png", None, decider) == "invoice"
    assert set(decider.decisions[0].context) == {"file_name", "what_the_options_mean"}
    classify_attachment(madeup_out / "CART02.png", None, decider, "  \n")                  # an e-mail with no text says nothing either
    assert set(decider.decisions[1].context) == {"file_name", "what_the_options_mean"}


def test_a_text_that_no_keyword_settles_goes_to_the_decider_with_its_first_page():
    decider = FakeDecider("confirmation", 0.95)
    assert classify_attachment(Path("bevestiging.pdf"), "Wij bevestigen uw bestelling 250101", decider) == "confirmation"
    assert decider.decisions[0].context["first_page"] == "Wij bevestigen uw bestelling 250101" and "email_text" not in decider.decisions[0].context


def test_what_the_decider_is_given_of_a_long_text_is_cut():
    decider = FakeDecider(None, 0.0)
    classify_attachment(Path("x.pdf"), "x" * (CONTEXT_CHARS + 500), decider, "y" * (CONTEXT_CHARS + 500))
    assert len(decider.decisions[0].context["first_page"]) == len(decider.decisions[0].context["email_text"]) == CONTEXT_CHARS


@pytest.mark.parametrize("choice, confidence, expected", [
    ("invoice", 0.8, "invoice"),                                          # exactly the minimum confidence
    ("invoice", 0.79, "other"), ("invoice", 0.0, "other"),
    (None, 1.0, "other"),                                                 # no choice
    ("telegram", 1.0, "other"),                                           # not one of the options
    ("other", 0.95, "other"),
])
def test_the_decider_counts_only_with_enough_confidence_and_an_option(choice, confidence, expected):
    assert classify_attachment(Path("x.pdf"), "Algemene voorwaarden", FakeDecider(choice, confidence)) == expected


def test_the_decider_is_not_asked_when_a_keyword_settles_the_class(madeup_out):
    decider = FakeDecider("invoice", 1.0)
    assert classify_attachment(madeup_out / "MU01_conf.pdf", None, decider) == "confirmation"
    assert classify_attachment(Path("x.pdf"), "Pakbon 5", decider) == "packing_slip"
    assert decider.decisions == []


def test_no_decider_and_the_decider_that_chooses_nothing_leave_a_scan_other(madeup_out):
    assert classify_attachment(madeup_out / "MU05_conf.pdf", None, NoDecider()) == classify_attachment(madeup_out / "MU05_conf.pdf", None) == "other"


# --- extract_eml ---

def test_extract_eml_gives_the_subject_and_the_body_and_writes_the_attachments_in_order(tmp_path, madeup_out):
    eml = write_eml(tmp_path / "m.eml", subject="Bevestiging PO 250101", body="Beste inkoper,\n\n   bijgaand onze   bevestiging.\n",
                    attachments=[madeup_out / "MU02_conf.pdf", madeup_out / "MU01_conf.pdf"])
    text, attachments = extract_eml(eml, tmp_path / "out")
    folder = tmp_path / "out" / sha256_file(eml)[:12]                  # the first 12 hex digits of the sha256 of the message file
    assert text == "Bevestiging PO 250101\nBeste inkoper,\nbijgaand onze bevestiging."
    assert attachments == [folder / "MU02_conf.pdf", folder / "MU01_conf.pdf"]
    assert [a.read_bytes() for a in attachments] == [(madeup_out / "MU02_conf.pdf").read_bytes(), (madeup_out / "MU01_conf.pdf").read_bytes()]


def test_an_html_only_body_is_turned_into_text_by_stripping_the_tags(tmp_path):
    html = ("<html><head><style>p { color: red }</style></head><body><p>Beste <b>inkoper</b>,</p><p>wij bevestigen &amp; danken u.<br>Uw PO&nbsp;250101"
            " &lt;ok&gt;</p><script>var po = 'PO 999999';</script><table><tr><td>Order</td><td>250102</td></tr></table></body></html>")
    text, attachments = extract_eml(write_eml(tmp_path / "m.eml", subject="Bevestiging", body=None, html=html), tmp_path / "out")
    assert text == "Bevestiging\nBeste inkoper,\nwij bevestigen & danken u.\nUw PO 250101 <ok>\nOrder 250102" and attachments == []


def test_the_plain_part_is_preferred_to_the_html_part_and_an_empty_one_gives_way(tmp_path):
    both = write_eml(tmp_path / "both.eml", subject="S", body="plain PO 250101", html="<p>html PO 250102</p>")
    assert extract_eml(both, tmp_path / "out")[0] == "S\nplain PO 250101"
    empty = write_eml(tmp_path / "empty.eml", subject="S", body="  \n", html="<p>html PO 250102</p>")
    assert extract_eml(empty, tmp_path / "out")[0] == "S\nhtml PO 250102"


def test_an_e_mail_without_a_body_or_attachments_gives_its_subject_and_writes_nothing(tmp_path):
    text, attachments = extract_eml(write_eml(tmp_path / "m.eml", subject="Alleen een onderwerp", body=None), tmp_path / "out")
    assert (text, attachments) == ("Alleen een onderwerp", []) and not (tmp_path / "out").exists()
    assert extract_eml(write_eml(tmp_path / "n.eml", subject="", body=""), tmp_path / "out") == ("", [])


def test_an_encoded_subject_and_a_body_in_another_character_set_are_decoded(tmp_path):
    text, _ = extract_eml(write_eml(tmp_path / "m.eml", subject="Bevestiging café ü", body="Grüße, PO 250101"), tmp_path / "out")
    assert text == "Bevestiging café ü\nGrüße, PO 250101"
    raw = (b"From: supplier@example.com\r\nSubject: =?iso-8859-1?q?Caf=E9?=\r\nContent-Type: text/plain; charset=iso-8859-1\r\n"
           b"Content-Transfer-Encoding: 8bit\r\n\r\nCaf\xe9 PO 250101\r\n")
    (tmp_path / "latin.eml").write_bytes(raw)
    assert extract_eml(tmp_path / "latin.eml", tmp_path / "out")[0] == "Café\nCafé PO 250101"


def test_a_character_set_that_python_does_not_know_does_not_stop_the_e_mail(tmp_path):
    raw = (b"From: supplier@example.com\r\nSubject: S\r\nContent-Type: text/plain; charset=x-no-such-set\r\n"
           b"Content-Transfer-Encoding: 8bit\r\n\r\nBevestiging PO 250101\r\n")
    (tmp_path / "m.eml").write_bytes(raw)
    assert extract_eml(tmp_path / "m.eml", tmp_path / "out")[0] == "S\nBevestiging PO 250101"


@pytest.mark.parametrize("name, safe", [
    ("Orderbevestiging 250101 (2).pdf", "Orderbevestiging 250101 (2).pdf"), ("Auftragsbestätigung é.pdf", "Auftragsbestätigung é.pdf"),
    ("../../evil.pdf", "evil.pdf"), ("..\\..\\evil.pdf", "evil.pdf"), ("/etc/passwd", "passwd.pdf"), ("C:\\Users\\x\\bevestiging.pdf", "bevestiging.pdf"),
    ('a:b*c?d"e<f>g|h.pdf', "a_b_c_d_e_f_g_h.pdf"), ("a\x07b.pdf", "a_b.pdf"), (" .hidden. ", "hidden.pdf"),         # no extension: the one of the type
    ("..", "attachment_1.pdf"), ("   ", "attachment_1.pdf"), (None, "attachment_1.pdf"),
])
def test_an_attachment_is_written_under_its_name_made_safe(tmp_path, name, safe):
    eml = write_eml(tmp_path / "m.eml", attachments=[(name, b"%PDF-1.4", "application/pdf")])
    [attachment] = extract_eml(eml, tmp_path / "out")[1]
    assert attachment == tmp_path / "out" / sha256_file(eml)[:12] / safe and attachment.read_bytes() == b"%PDF-1.4"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m.eml", "out"]            # nothing was written beside the output folder


def test_an_attachment_without_a_name_gets_the_extension_of_its_type_and_its_place(tmp_path):
    eml = write_eml(tmp_path / "m.eml", attachments=[(None, b"1", "application/pdf"), (None, b"2", "image/png"), (None, b"3", "application/x-made-up")])
    assert [a.name for a in extract_eml(eml, tmp_path / "out")[1]] == ["attachment_1.pdf", "attachment_2.png", "attachment_3"]


def test_attachments_of_one_name_are_all_kept(tmp_path):
    eml = write_eml(tmp_path / "m.eml", attachments=[("a.pdf", b"1"), ("A.PDF", b"2"), ("a.pdf", b"3"), ("b.pdf", b"4")])
    attachments = extract_eml(eml, tmp_path / "out")[1]
    assert [a.name for a in attachments] == ["a.pdf", "A_2.PDF", "a_3.pdf", "b.pdf"] and [a.read_bytes() for a in attachments] == [b"1", b"2", b"3", b"4"]


def test_a_very_long_name_is_cut_and_keeps_its_extension(tmp_path):
    for name in ("x" * 400 + ".pdf", "é" * 300 + ".pdf"):
        [attachment] = extract_eml(write_eml(tmp_path / "m.eml", attachments=[(name, b"%PDF-1.4")]), tmp_path / "out")[1]
        assert attachment.exists() and attachment.name.endswith(".pdf") and len(attachment.name.encode()) <= 200


def test_text_parts_of_the_body_and_pictures_in_it_are_not_attachments(tmp_path):
    message = EmailMessage()
    message["Subject"] = "Bevestiging"
    message.set_content("Zie de bijlage.")
    message.add_alternative('<p>Zie de bijlage.</p><img src="cid:logo">', subtype="html")
    message.get_payload()[1].add_related(b"\x89PNG-made-up", "image", "png", cid="<logo>")        # the picture of the signature
    message.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="bevestiging.pdf")
    message.add_attachment(b"notities", maintype="text", subtype="plain", filename="notities.txt")  # a text file is an attachment
    (tmp_path / "m.eml").write_bytes(message.as_bytes())
    text, attachments = extract_eml(tmp_path / "m.eml", tmp_path / "out")
    assert text == "Bevestiging\nZie de bijlage." and [a.name for a in attachments] == ["bevestiging.pdf", "notities.txt"]


def test_a_forwarded_message_is_an_attachment_with_its_own_text(tmp_path):
    inner = EmailMessage()
    inner["Subject"] = "Doorgestuurd"
    inner.set_content("PO 250101")
    outer = EmailMessage()
    outer["Subject"] = "Fwd"
    outer.set_content("Zie bijlage.")
    outer.add_attachment(inner)
    (tmp_path / "m.eml").write_bytes(outer.as_bytes())
    [attachment] = extract_eml(tmp_path / "m.eml", tmp_path / "out")[1]
    assert extract_eml(attachment, tmp_path / "out2")[0] == "Doorgestuurd\nPO 250101"


def nested_eml(path: Path, attachments: list[tuple[str, bytes]], signed: bool = False) -> Path:
    """An e-mail whose attachments lie a level down: a multipart/mixed inside the multipart/mixed, or inside a multipart/signed that
    has a made-up signature part beside it. This is how forwarding and signing programs pack a message."""
    inner = EmailMessage()
    inner.set_content("Zie de bijlage.")
    for name, data in attachments:
        inner.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    outer = EmailMessage()
    outer["From"], outer["To"], outer["Subject"] = "supplier@example.com", "aid@example.com", "Doorgestuurd"
    if signed:
        outer["Content-Type"] = 'multipart/signed; protocol="application/pkcs7-signature"; micalg=sha-256'
        signature = EmailMessage()
        signature.set_content(b"made-up signature", maintype="application", subtype="pkcs7-signature", disposition="attachment", filename="smime.p7s")
        outer.attach(inner)
        outer.attach(signature)
    else:
        outer.make_mixed()
        outer.attach(inner)
    path.write_bytes(outer.as_bytes())
    return path


def logo_eml(path: Path) -> Path:
    """A mail with an HTML body and a picture in it and nothing else, packed as Outlook packs it: a multipart/related whose root is the
    multipart/alternative of the body, and the logo, an inline picture with a content id, beside it."""
    body = EmailMessage()
    body.set_content("Met vriendelijke groet")
    body.add_alternative('<p>Met vriendelijke groet</p><img src="cid:logo">', subtype="html")
    logo = EmailMessage()
    logo.set_content(b"\x89PNG-made-up", maintype="image", subtype="png", disposition="inline", filename="image001.png", cid="<logo>")
    message = EmailMessage()
    message["Subject"] = "Handtekening"
    message["Content-Type"] = 'multipart/related; type="multipart/alternative"'
    message.attach(body)
    message.attach(logo)
    path.write_bytes(message.as_bytes())
    return path


PDF = b"%PDF-1.4 made up"


def multipart(kind: str, *parts: EmailMessage) -> EmailMessage:
    """A multipart/<kind> message that holds these parts."""
    message = EmailMessage()
    message["Content-Type"] = f"multipart/{kind}"
    for part in parts:
        message.attach(part)
    return message


def text_part(text: str, subtype: str = "plain", **kw) -> EmailMessage:
    part = EmailMessage()
    part.set_content(text, subtype=subtype, **kw)
    return part


def file_part(data: bytes, maintype: str, subtype: str, name: str, disposition: str = "attachment", cid: str | None = None) -> EmailMessage:
    part = EmailMessage()
    part.set_content(data, maintype=maintype, subtype=subtype, disposition=disposition, filename=name, **({"cid": cid} if cid else {}))
    return part


def apple_rich_text(data: bytes, name: str) -> EmailMessage:
    """alternative > [text/plain, mixed > [text/html, the PDF, text/html]]: how Apple Mail writes rich text with a file."""
    return multipart("alternative", text_part("Beste inkoper, zie bijlage."),
                     multipart("mixed", text_part("<p>Beste inkoper,</p>", "html"), file_part(data, "application", "pdf", name, "inline"),
                               text_part("<p>Met vriendelijke groet</p>", "html")))


def alternative_with_related_pdf(data: bytes, name: str) -> EmailMessage:
    """alternative > [text/plain, related > [text/html, the PDF as an inline part]]."""
    return multipart("alternative", text_part("Zie bijlage."),
                     multipart("related", text_part("<p>Zie bijlage.</p>", "html"), file_part(data, "application", "pdf", name, "inline", "<pdf>")))


def related_in_mixed(data: bytes, name: str) -> EmailMessage:
    """mixed > [related > [alternative > [text/plain, text/html], a picture, the PDF]]: the whole message in one related."""
    return multipart("mixed", multipart("related", multipart("alternative", text_part("Zie bijlage."), text_part("<p>Zie <img src='cid:logo'></p>", "html")),
                                        file_part(b"\x89PNG-made-up", "image", "png", "image001.png", "inline", "<logo>"),
                                        file_part(data, "application", "pdf", name, "inline", "<pdf>")))


SHAPES = {"apple-rich-text": apple_rich_text, "alternative-related": alternative_with_related_pdf, "related-in-mixed": related_in_mixed}


def attachment_names(tmp_path: Path, message: EmailMessage) -> list[str]:
    message["Subject"] = "Bevestiging"
    (tmp_path / "m.eml").write_bytes(message.as_bytes())
    return [a.name for a in extract_eml(tmp_path / "m.eml", tmp_path / "out")[1]]


@pytest.mark.parametrize("build", SHAPES.values(), ids=SHAPES.keys())
def test_a_pdf_in_a_part_that_makes_the_body_is_found(tmp_path, build):
    assert attachment_names(tmp_path, build(PDF, "bevestiging.pdf")) == ["bevestiging.pdf"]          # and the picture of the related is not


def test_a_version_of_the_body_is_no_attachment_even_when_it_is_not_text_you_read(tmp_path):
    invite = text_part("BEGIN:VCALENDAR\nEND:VCALENDAR", "calendar")                   # a meeting request is a version of the body in an alternative
    message = multipart("alternative", text_part("Uitnodiging"), text_part("<p>Uitnodiging</p>", "html"), invite)
    assert attachment_names(tmp_path, message) == []


def test_a_pdf_that_is_all_there_is_of_a_related_is_found(tmp_path):
    message = multipart("alternative", text_part("Zie bijlage."), multipart("related", file_part(PDF, "application", "pdf", "bevestiging.pdf", "inline")))
    assert attachment_names(tmp_path, message) == ["bevestiging.pdf"]


@pytest.mark.parametrize("build, expected", [
    (lambda d, n: multipart("mixed", multipart("alternative", text_part("tekst"), text_part("<p>tekst</p>", "html")), file_part(d, "application", "pdf", n)),
     ["bevestiging.pdf"]),                                                                                        # Gmail
    (lambda d, n: multipart("mixed", multipart("related", multipart("alternative", text_part("tekst"), text_part("<p>tekst</p>", "html")),
                                               file_part(b"1", "image", "png", "image001.png", "inline", "<a>"),
                                               file_part(b"2", "image", "png", "image002.png", "inline", "<b>")), file_part(d, "application", "pdf", n)),
     ["bevestiging.pdf"]),                                                                                        # Outlook, with signature pictures
    (lambda d, n: multipart("related", multipart("alternative", text_part("tekst"), text_part("<p>tekst</p>", "html")),
                            file_part(b"1", "image", "png", "image001.png", "inline", "<a>")), []),               # Outlook, a signature picture and nothing else
    (lambda d, n: multipart("alternative", text_part("tekst"), text_part("<p>tekst</p>", "html")), []),           # plain alternative
    (lambda d, n: multipart("signed", multipart("mixed", text_part("tekst"), file_part(d, "application", "pdf", n)),
                            file_part(b"sig", "application", "pkcs7-signature", "smime.p7s")), ["bevestiging.pdf", "smime.p7s"]),   # signed
], ids=["gmail", "outlook", "logo-only", "plain-alternative", "signed"])
def test_the_usual_layouts_give_their_files_and_nothing_else(tmp_path, build, expected):
    assert attachment_names(tmp_path, build(PDF, "bevestiging.pdf")) == expected


def test_attachments_in_a_multipart_inside_a_multipart_are_found(tmp_path):
    eml = nested_eml(tmp_path / "m.eml", [("bevestiging.pdf", b"%PDF-1"), ("tweede.pdf", b"%PDF-2")])
    text, attachments = extract_eml(eml, tmp_path / "out")
    assert text == "Doorgestuurd\nZie de bijlage." and [a.name for a in attachments] == ["bevestiging.pdf", "tweede.pdf"]
    assert [a.read_bytes() for a in attachments] == [b"%PDF-1", b"%PDF-2"]


def test_attachments_in_a_signed_e_mail_are_found_and_so_is_the_signature_part(tmp_path):
    text, attachments = extract_eml(nested_eml(tmp_path / "m.eml", [("bevestiging.pdf", b"%PDF-1")], signed=True), tmp_path / "out")
    assert text == "Doorgestuurd\nZie de bijlage." and [a.name for a in attachments] == ["bevestiging.pdf", "smime.p7s"]


@pytest.mark.parametrize("name, content_type, expected", [
    ("Orderbevestiging", "application/pdf", "Orderbevestiging.pdf"), ("scan", "image/png", "scan.png"),
    ("Orderbevestiging.", "application/pdf", "Orderbevestiging.pdf"), ("Order 250101", "application/pdf", "Order 250101.pdf"),
    ("notities", "application/x-made-up", "notities"),                                   # a type that has no extension adds none
    ("Order 250101.v2", "application/pdf", "Order 250101.v2"), ("Voorwaarden.pdf", "image/png", "Voorwaarden.pdf"),     # a name with an extension keeps it
    (None, "application/pdf", "attachment_1.pdf"), (None, "image/png", "attachment_1.png"), (None, "message/rfc822", "attachment_1.eml"),
])
def test_an_attachment_without_an_extension_takes_one_from_its_content_type(tmp_path, name, content_type, expected):
    [attachment] = extract_eml(write_eml(tmp_path / "m.eml", attachments=[(name, b"made up", content_type)]), tmp_path / "out")[1]
    assert attachment.name == expected


def test_a_name_that_gets_an_extension_is_still_cut_to_its_size_and_kept_apart_from_its_namesakes(tmp_path):
    eml = write_eml(tmp_path / "m.eml", attachments=[("x" * 300, b"1", "application/pdf"), ("Orderbevestiging", b"2", "application/pdf"),
                                                      ("Orderbevestiging.", b"3", "application/pdf")])
    names = [a.name for a in extract_eml(eml, tmp_path / "out")[1]]
    assert len(names[0].encode()) <= 200 and names[0].endswith(".pdf") and names[1:] == ["Orderbevestiging.pdf", "Orderbevestiging_2.pdf"]


def test_an_attached_e_mail_is_one_attachment_under_its_own_name_and_is_not_opened(tmp_path, madeup_out):
    forwarded = EmailMessage()
    forwarded["Subject"] = "Doorgestuurd"
    forwarded.set_content("PO 250101")
    forwarded.add_attachment((madeup_out / "MU01_conf.pdf").read_bytes(), maintype="application", subtype="pdf", filename="MU01_conf.pdf")
    eml = write_eml(tmp_path / "m.eml", subject="Fwd", attachments=[("Fwd bevestiging.eml", forwarded), (None, forwarded)])
    attachments = extract_eml(eml, tmp_path / "out")[1]
    assert [a.name for a in attachments] == ["Fwd bevestiging.eml", "attachment_2.eml"]                       # not "MU01_conf.pdf"
    text, inner = extract_eml(attachments[0], tmp_path / "out2")
    assert text == "Doorgestuurd\nPO 250101" and [a.name for a in inner] == ["MU01_conf.pdf"]                 # the attached message is whole


def test_a_picture_of_the_body_is_no_attachment_when_the_e_mail_has_no_other(tmp_path):
    text, attachments = extract_eml(logo_eml(tmp_path / "m.eml"), tmp_path / "out")
    assert text == "Handtekening\nMet vriendelijke groet" and attachments == [] and not (tmp_path / "out").exists()


@pytest.mark.parametrize("maintype, subtype, disposition, name, is_attachment", [
    ("image", "png", "inline", "image001.png", False),                  # a picture of the body
    ("text", "css", "inline", "style.css", False),                      # its style sheet
    ("application", "pdf", "inline", "bevestiging.pdf", True),          # a document is no resource, however the sender packed it
    ("image", "png", "attachment", "scan.png", True),                   # a picture that says it is an attachment
    ("text", "plain", "attachment", "notities.txt", True),
])
def test_in_a_related_only_the_pictures_and_styles_of_the_body_are_resources(tmp_path, maintype, subtype, disposition, name, is_attachment):
    body = EmailMessage()
    body.set_content("Zie bijlage")
    part = EmailMessage()
    part.set_content(b"made up", maintype=maintype, subtype=subtype, disposition=disposition, filename=name, cid="<resource>")
    message = EmailMessage()
    message["Content-Type"] = "multipart/related"
    message.attach(body)
    message.attach(part)
    (tmp_path / "m.eml").write_bytes(message.as_bytes())
    assert [a.name for a in extract_eml(tmp_path / "m.eml", tmp_path / "out")[1]] == ([name] if is_attachment else [])


def test_text_after_an_inline_attachment_is_body_text_and_not_an_attachment(tmp_path):
    """Apple Mail writes the text before a PDF, the PDF, and the text after it as three parts of one multipart/mixed."""
    pdf = EmailMessage()
    pdf.set_content(b"%PDF-1", maintype="application", subtype="pdf", disposition="inline", filename="bevestiging.pdf", cid="<pdf>")
    message = EmailMessage()
    message["Content-Type"] = "multipart/mixed"
    for part in (text_part("Beste inkoper,"), pdf, text_part("Met vriendelijke groet"),                              # body text (the library leaves
                 text_part("<p>een stuk</p>", "html"), text_part("<p>nog een stuk</p>", "html"),                      # out only the first of each type)
                 text_part("tekst met een naam", disposition="inline", filename="inline.txt"),                        # a text with a file name is a file
                 text_part("tekst die zegt een bijlage te zijn", disposition="attachment"),                           # and so is one that says it is
                 text_part("losse notitie", disposition="attachment", filename="notities.txt")):
        message.attach(part)
    (tmp_path / "m.eml").write_bytes(message.as_bytes())
    assert [a.name for a in extract_eml(tmp_path / "m.eml", tmp_path / "out")[1]] == ["bevestiging.pdf", "inline.txt", "attachment_3.txt", "notities.txt"]


@pytest.mark.parametrize("maintype, subtype, disposition, name, expected", [
    ("application", "pdf", "attachment", "scan.pdf", ["scan.pdf"]),            # a PDF sent as the whole message
    ("application", "pdf", None, None, ["attachment_1.pdf"]),                  # also with no name and no disposition
    ("text", "plain", "attachment", "notities.txt", ["notities.txt"]),         # a text that says it is a file
    ("text", "plain", None, None, []),                                         # a text to read
    ("text", "html", None, None, []),
])
def test_a_message_of_one_part_is_an_attachment_unless_it_is_text_to_read(tmp_path, maintype, subtype, disposition, name, expected):
    message = EmailMessage()
    message["Subject"] = "Een deel"
    if maintype == "text":
        message.set_content("tekst", subtype=subtype, disposition=disposition, filename=name)
    else:
        message.set_content(b"%PDF-1", maintype=maintype, subtype=subtype, disposition=disposition, filename=name)
    (tmp_path / "m.eml").write_bytes(message.as_bytes())
    assert [a.name for a in extract_eml(tmp_path / "m.eml", tmp_path / "out")[1]] == expected


def test_a_pdf_sent_as_the_whole_message_is_its_attachment(tmp_path):
    message = EmailMessage()
    message["Subject"] = "Scan"
    message.set_content(b"%PDF-1.4 made up", maintype="application", subtype="pdf", filename="scan.pdf")
    (tmp_path / "m.eml").write_bytes(message.as_bytes())
    text, attachments = extract_eml(tmp_path / "m.eml", tmp_path / "out")
    assert text == "Scan" and [a.name for a in attachments] == ["scan.pdf"] and attachments[0].read_bytes() == b"%PDF-1.4 made up"


def test_reading_an_e_mail_again_writes_the_same_files_again(tmp_path, madeup_out):
    eml = write_eml(tmp_path / "m.eml", subject="S", attachments=[madeup_out / "MU01_conf.pdf"])
    first = extract_eml(eml, tmp_path / "out")
    assert extract_eml(eml, tmp_path / "out") == first and [p.name for p in first[1][0].parent.iterdir()] == ["MU01_conf.pdf"]


# --- scan_folder ---

def put(inbox: Path, source: Path, name: str | None = None) -> Path:
    """Copy the file into the inbox (made when it is not there yet), under its own name or under `name`."""
    inbox.mkdir(exist_ok=True)
    return Path(shutil.copy(source, inbox / (name or source.name)))


def scan(tmp_path: Path, inbox: Path, decider=None, now: datetime = NOW, settings=SETTINGS) -> ScanResult:
    """scan_folder with the store and the cases folder of this test."""
    return scan_folder(inbox, PoStore(tmp_path / "store"), tmp_path / "cases", settings, now, decider)


def review_items(tmp_path: Path) -> list[dict]:
    path = tmp_path / "cases" / "review_items.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def drawn_confirmation(path: Path, case_id: str = "MU01", drop: tuple[str, ...] = (), add: tuple[str, ...] = (), title: str | None = None) -> Path:
    """The made-up confirmation of `case_id`, drawn by the generator into `path` without the rows tagged in `drop` (the PO reference,
    for one) and with the text rows `add` below its heading. A different `title` gives a file with other bytes and the same text."""
    pages = confirmation_pages(SPECS[case_id])
    pages[0][:] = [row for row in pages[0] if row.ref not in drop]
    pages[0][2:2] = [Row([Text(0, text)]) for text in add]
    path.write_bytes(pdf_bytes(pages, title or path.name))
    return path


def drawn_po(path: Path, case_id: str = "MU01", heading: str | None = None) -> Path:
    """The first version of the made-up PO of `case_id`, drawn into `path` with another heading if one is given."""
    spec = SPECS[case_id]
    pages = po_pages(spec, spec.po.versions[0])
    if heading is not None:
        next(row for row in pages[0] if row.ref == "po_reference").texts[0].text = heading
    path.write_bytes(pdf_bytes(pages, path.name))
    return path


def inbox_of(tmp_path: Path) -> Path:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return inbox


def case_names(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "cases").iterdir() if p.is_dir()) if (tmp_path / "cases").exists() else []


# the case, the file and the e-mail

def test_an_e_mail_with_a_confirmation_attached_opens_a_case_for_the_po_on_its_page(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "m.eml", subject="Orderbevestiging", body="Bijgaand onze bevestiging.", attachments=[madeup_out / "MU01_conf.pdf"])
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000"] and r.stored_pos == [] and r.skipped == [] and review_items(tmp_path) == []
    case = load_case(tmp_path / "cases", r.new_cases[0])
    assert (case.kind, case.po_number) == ("confirmation", "250101")
    assert [f.name for f in case.input_files] == ["MU01_conf.pdf"]                    # the document is the input, not the e-mail
    assert sha256_file(case.input_files[0]) == sha256_file(madeup_out / "MU01_conf.pdf")
    assert (inbox / "processed" / "m.eml").exists() and list(inbox.iterdir()) == [inbox / "processed"]


@pytest.mark.parametrize("fields", [
    {"subject": "Bevestiging PO 250101"},                                                                      # in the subject
    {"body": "Uw inkooporder 250101 is bevestigd."},                                                           # in the plain-text body
    {"body": None, "html": "<p>Uw <b>inkooporder</b>&nbsp;250101 is bevestigd.</p>"},                          # in an HTML-only body
], ids=["subject", "body", "html"])
def test_the_po_number_in_the_e_mail_is_used_when_the_page_has_none(tmp_path, fields):
    inbox = inbox_of(tmp_path)
    pdf = drawn_confirmation(tmp_path / "conf.pdf", drop=("po_reference",))                                   # a confirmation that prints no PO number
    write_eml(inbox / "m.eml", attachments=[pdf], **fields)
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000"] and r.skipped == []


def test_the_same_number_in_the_e_mail_and_on_the_page_counts_once(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "m.eml", subject="Bevestiging PO 250101", body="Ihre Bestellung 250101", attachments=[madeup_out / "MU01_conf.pdf"])
    assert scan(tmp_path, inbox).new_cases == ["250101_20261116T080000"]


def test_a_loose_confirmation_opens_a_case_and_its_file_moves_to_processed(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU04_conf.pdf")                                                                   # German: "Ihre Bestellung 250104"
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250104_20261116T080000"] and (inbox / "processed" / "MU04_conf.pdf").is_file() and not (inbox / "MU04_conf.pdf").exists()


def test_the_po_number_pattern_of_the_settings_is_used(tmp_path):
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "m.eml", subject="Ref REF-250199", attachments=[drawn_confirmation(tmp_path / "conf.pdf", drop=("po_reference",))])
    r = scan(tmp_path, inbox, settings=SETTINGS.model_copy(update={"po_number_pattern": r"REF-(\d{6})"}))
    assert r.new_cases == ["250199_20261116T080000"]


# two or more PO numbers

@pytest.mark.parametrize("make", [
    lambda inbox, tmp, out: write_eml(inbox / "m.eml", subject="Bevestiging PO 250102", attachments=[out / "MU01_conf.pdf"]),               # e-mail and page differ
    lambda inbox, tmp, out: write_eml(inbox / "m.eml", subject="Bevestiging", body="PO 250101\nPO 250102", attachments=[out / "MU01_conf.pdf"]),   # both in the body
    lambda inbox, tmp, out: write_eml(inbox / "m.eml", subject="Bevestiging", attachments=[drawn_confirmation(tmp / "c.pdf", add=("Ook inkooporder 250102",))]),  # both on the page
    lambda inbox, tmp, out: drawn_confirmation(inbox / "c.pdf", add=("Ook inkooporder 250102",)),                                           # a file, both on the page
], ids=["email-and-page", "body", "page-in-email", "page-in-file"])
def test_two_different_po_numbers_anywhere_make_no_case(tmp_path, madeup_out, make):
    inbox = inbox_of(tmp_path)
    make(inbox, tmp_path, madeup_out)
    r = scan(tmp_path, inbox)
    assert r.new_cases == [] and [reason for _, reason in r.skipped] == ["more than one PO number"] and case_names(tmp_path) == []
    assert [item["reason"] for item in review_items(tmp_path)] == ["more than one PO number"]
    assert [p.name for p in (inbox / "processed").iterdir()] == [r.skipped[0][0].partition("/")[0]]            # the file was handled, so it is filed


# review items

def test_a_review_item_is_a_json_line_with_the_time_the_file_and_the_reason(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "m.eml", subject="Bevestiging PO 250101 en PO 250102", attachments=[madeup_out / "MU01_conf.pdf"])
    scan(tmp_path, inbox)
    assert (tmp_path / "cases" / "review_items.jsonl").read_text(encoding="utf-8") == (
        '{"time": "2026-11-16T08:00:00", "file": "m.eml/MU01_conf.pdf", "reason": "more than one PO number"}\n')


def test_review_items_are_appended_and_a_name_is_kept_as_it_is(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU05_conf.pdf", "bevestiging é.pdf")                                              # a scan: no class
    scan(tmp_path, inbox)
    drawn_confirmation(inbox / "twee.pdf", add=("Ook inkooporder 250102",))
    scan(tmp_path, inbox, now=datetime(2026, 11, 16, 9, 30))
    items = review_items(tmp_path)
    assert items == [{"time": "2026-11-16T08:00:00", "file": "bevestiging é.pdf", "reason": "no text layer; class unknown"},
                     {"time": "2026-11-16T09:30:00", "file": "twee.pdf", "reason": "more than one PO number"}]
    assert "é" in (tmp_path / "cases" / "review_items.jsonl").read_text(encoding="utf-8")                      # not escaped


def test_a_review_item_after_a_cut_off_line_starts_a_line_of_its_own(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "review_items.jsonl").write_text('{"time": "2026-11-15T08:00:00", "fi', encoding="utf-8")     # a crash cut the line off
    put(inbox, madeup_out / "MU05_conf.pdf")
    scan(tmp_path, inbox)
    lines = (tmp_path / "cases" / "review_items.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"time": "2026-11-15T08:00:00", "fi' and json.loads(lines[1])["file"] == "MU05_conf.pdf" and len(lines) == 2


def test_a_review_item_that_cannot_be_written_is_an_error_and_not_also_a_skip(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU05_conf.pdf")                                                                   # a scan: its skip comes with a review item
    (tmp_path / "cases").write_text("not a folder")                                                            # so the review file cannot be made
    r = scan(tmp_path, inbox)
    assert [name for name, _ in r.skipped] == ["MU05_conf.pdf"] and r.skipped[0][1].startswith("error: ")      # one entry, and it is the error
    assert (inbox / "MU05_conf.pdf").exists() and not (inbox / "processed").exists()


# PO PDFs

def test_po_pdfs_are_filed_in_the_store_whether_loose_or_in_an_e_mail(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU03_po_v1.pdf")
    write_eml(inbox / "m.eml", subject="Inkooporder", attachments=[madeup_out / "MU03_po_v2.pdf", madeup_out / "MU01_po_v1.pdf"])
    r = scan(tmp_path, inbox)
    assert r.stored_pos == ["250103", "250103", "250101"] and r.new_cases == [] and r.skipped == []
    assert [v.version for v in PoStore(tmp_path / "store").versions("250103")] == [1, 2] and len(PoStore(tmp_path / "store").versions("250101")) == 1
    assert sorted(p.name for p in (inbox / "processed").iterdir()) == ["MU03_po_v1.pdf", "m.eml"]


def test_a_po_pdf_that_the_store_holds_already_is_filed_as_the_version_it_is(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU03_po_v1.pdf")
    assert scan(tmp_path, inbox).stored_pos == ["250103"]
    put(inbox, madeup_out / "MU03_po_v1.pdf", "again.pdf")                                                     # the same bytes under another name
    assert scan(tmp_path, inbox).stored_pos == ["250103"] and len(PoStore(tmp_path / "store").versions("250103")) == 1


def test_a_po_pdf_that_arrives_with_its_confirmation_is_filed_before_the_confirmation_is_matched(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    drawn_confirmation(inbox / "a_confirmation.pdf", drop=("po_reference",))                                   # sorts before the PO and names no PO
    put(inbox, madeup_out / "MU01_po_v1.pdf", "z_po.pdf")
    r = scan(tmp_path, inbox)
    assert r.stored_pos == ["250101"] and r.new_cases == ["250101_20261116T080000"]


def test_a_po_pdf_that_the_store_refuses_stays_in_the_folder_as_an_error(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    drawn_po(inbox / "geen_nummer.pdf", heading="Inkooporder")                                                 # a PO that prints no PO number
    put(inbox, madeup_out / "MU01_po_v1.pdf")
    r = scan(tmp_path, inbox)
    assert r.stored_pos == ["250101"] and r.skipped == [("geen_nummer.pdf", "error: geen_nummer.pdf: no PO number found, so the PDF cannot be filed")]
    assert (inbox / "geen_nummer.pdf").exists() and not (inbox / "processed" / "geen_nummer.pdf").exists() and (inbox / "processed" / "MU01_po_v1.pdf").exists()


# the other classes

@pytest.mark.parametrize("lines, reason", [
    (("S01", "Adres", "Telefoon", "Pakbon 5", "Uw inkooporder 250101"), "packing_slip"), (("Rechnung 77",), "invoice"), (("Offerte 12",), "quotation"),
    (("Algemene voorwaarden",), "other")])
def test_a_document_of_another_class_is_skipped_with_its_class(tmp_path, lines, reason):
    inbox = inbox_of(tmp_path)
    drawn(inbox / "doc.pdf", page_of(*lines))
    r = scan(tmp_path, inbox, FakeDecider(None, 0.0))
    assert r == ScanResult(new_cases=[], stored_pos=[], skipped=[("doc.pdf", reason)]) and review_items(tmp_path) == []
    assert (inbox / "processed" / "doc.pdf").exists() and case_names(tmp_path) == [] and not (tmp_path / "store").exists()


def test_an_attachment_of_another_class_is_skipped_by_its_place_in_the_e_mail(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "m.eml", attachments=[drawn(tmp_path / "pakbon.pdf", page_of("Pakbon 5")), madeup_out / "MU01_conf.pdf"])
    r = scan(tmp_path, inbox)
    assert r.skipped == [("m.eml/pakbon.pdf", "packing_slip")] and r.new_cases == ["250101_20261116T080000"]


# a file without a text layer

def test_a_scan_and_a_png_without_a_decider_are_skipped_and_left_to_the_buyer(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU05_conf.pdf")
    put(inbox, madeup_out / "CART02.png")
    for decider in (None, NoDecider()):
        r = scan(tmp_path, inbox, decider)
        assert r.new_cases == [] and r.skipped == [("CART02.png", "no text layer; class unknown"), ("MU05_conf.pdf", "no text layer; class unknown")]
        for name in ("CART02.png", "MU05_conf.pdf"):
            shutil.move(inbox / "processed" / name, inbox / name)                                              # put them back for the second decider
    assert [item["file"] for item in review_items(tmp_path)] == ["CART02.png", "MU05_conf.pdf"] * 2
    assert {item["reason"] for item in review_items(tmp_path)} == {"no text layer; class unknown"}


def test_a_scan_that_the_decider_knows_is_handled_by_its_class(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "m.eml", subject="Bevestiging PO 250105", body="Bijgaand onze bevestiging.", attachments=[madeup_out / "MU05_conf.pdf"])
    decider = FakeDecider("confirmation", 0.9)
    r = scan(tmp_path, inbox, decider)
    assert r.new_cases == ["250105_20261116T080000"] and r.skipped == [] and review_items(tmp_path) == []
    [decision] = decider.decisions
    assert decision.kind == "document_type" and decision.context["file_name"] == "MU05_conf.pdf"
    assert decision.context["email_text"] == "Bevestiging PO 250105\nBijgaand onze bevestiging."


def test_a_png_that_the_decider_classes_as_a_confirmation_gets_a_case_without_a_po_number_when_nothing_names_one(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "CART02.png")                                                                      # a PNG: the quick read has nothing to read
    decider = FakeDecider("confirmation", 1.0)
    r = scan(tmp_path, inbox, decider)
    [case_id] = r.new_cases
    assert case_id == "unknown_20261116T080000" and load_case(tmp_path / "cases", case_id).po_number is None and r.skipped == []
    assert [f.name for f in load_case(tmp_path / "cases", case_id).input_files] == ["CART02.png"]
    assert set(decider.decisions[0].context) == {"file_name", "what_the_options_mean"}                         # a file that is no e-mail has no e-mail text


def test_a_scan_that_the_decider_is_unsure_about_is_skipped_for_review(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU05_conf.pdf")
    r = scan(tmp_path, inbox, FakeDecider("confirmation", 0.5))
    assert r.new_cases == [] and r.skipped == [("MU05_conf.pdf", "no text layer; class unknown")]
    assert [item["file"] for item in review_items(tmp_path)] == ["MU05_conf.pdf"]


def test_a_scan_that_the_decider_knows_to_be_something_else_is_skipped_with_that_class_and_no_review_item(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "CART02.png")
    put(inbox, madeup_out / "MU05_conf.pdf")
    assert scan(tmp_path, inbox, FakeDecider("other", 0.95)).skipped == [("CART02.png", "other"), ("MU05_conf.pdf", "other")]
    assert review_items(tmp_path) == []
    put(inbox, madeup_out / "MU05_conf.pdf", "again.pdf")
    assert scan(tmp_path, inbox, FakeDecider("invoice", 0.95)).skipped == [("again.pdf", "invoice")]


class UnreachableDecider:
    """A decider whose service cannot be reached: it raises, as the client of a real one does."""
    name = "unreachable"

    def decide(self, d):
        raise ConnectionError("no connection")


def test_a_decider_that_cannot_be_reached_leaves_the_file_for_the_next_scan(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "m.eml", subject="Bevestiging PO 250105", attachments=[madeup_out / "MU05_conf.pdf"])
    r = scan(tmp_path, inbox, UnreachableDecider())
    assert r.new_cases == [] and r.skipped == [("m.eml/MU05_conf.pdf", "error: no connection")] and (inbox / "m.eml").exists() and review_items(tmp_path) == []
    assert scan(tmp_path, inbox, FakeDecider("confirmation", 0.9), now=datetime(2026, 11, 16, 8, 1)).new_cases == ["250105_20261116T080100"]
    assert (inbox / "processed" / "m.eml").exists()


@pytest.mark.parametrize("error, reason", [
    (RuntimeError("the model said:\n   no   answer"), "error: the model said: no answer"),             # one line, with single spaces
    (ValueError(), "error: ValueError"),                                                                # no message: the name of the exception
])
def test_the_message_of_an_error_is_one_line_and_never_empty(tmp_path, madeup_out, error, reason):
    class Failing:
        name = "failing"
        def decide(self, d): raise error

    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "CART02.png")
    assert scan(tmp_path, inbox, Failing()).skipped == [("CART02.png", reason)] and (inbox / "CART02.png").exists()


# the duplicate check

def test_a_confirmation_that_is_resent_in_an_e_mail_is_a_duplicate_of_the_case_it_opened(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU01_conf.pdf")
    assert len(scan(tmp_path, inbox).new_cases) == 1
    write_eml(inbox / "again.eml", subject="Nogmaals onze bevestiging", attachments=[madeup_out / "MU01_conf.pdf"])
    r = scan(tmp_path, inbox, now=datetime(2026, 11, 17, 8, 0))
    assert r.new_cases == [] and r.skipped == [("again.eml/MU01_conf.pdf", "duplicate")] and len(case_names(tmp_path)) == 1
    assert (inbox / "processed" / "again.eml").exists() and review_items(tmp_path) == []


def test_a_copy_in_the_same_scan_is_a_duplicate_of_the_case_opened_a_moment_before(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU01_conf.pdf", "a.pdf")
    put(inbox, madeup_out / "MU01_conf.pdf", "b.pdf")
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000"] and r.skipped == [("b.pdf", "duplicate")]
    assert find_case_by_hash(tmp_path / "cases", sha256_file(madeup_out / "MU01_conf.pdf")).case_id == r.new_cases[0]


def test_a_resent_file_is_a_duplicate_before_it_is_classed_so_it_costs_no_decision_and_needs_no_decider(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU05_conf.pdf")                                                                   # a scan: only the decider can class it
    assert len(scan(tmp_path, inbox, FakeDecider("confirmation", 0.9)).new_cases) == 1
    put(inbox, madeup_out / "MU05_conf.pdf", "again.pdf")
    write_eml(inbox / "again.eml", attachments=[madeup_out / "MU05_conf.pdf"])
    decider = FakeDecider("invoice", 1.0)
    r = scan(tmp_path, inbox, decider, now=datetime(2026, 11, 17, 8, 0))
    assert r.new_cases == [] and r.skipped == [("again.eml/MU05_conf.pdf", "duplicate"), ("again.pdf", "duplicate")]
    assert decider.decisions == [] and review_items(tmp_path) == []                                            # not asked, and nothing for the buyer to look at
    put(inbox, madeup_out / "MU05_conf.pdf", "third.pdf")
    r = scan(tmp_path, inbox, UnreachableDecider(), now=datetime(2026, 11, 18, 8, 0))                         # and not even a decider that is down matters
    assert r.skipped == [("third.pdf", "duplicate")] and (inbox / "processed" / "third.pdf").exists()


def test_a_copy_of_a_scan_in_the_same_scan_is_a_duplicate_too_but_costs_its_decision(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU05_conf.pdf", "a.pdf")
    put(inbox, madeup_out / "MU05_conf.pdf", "b.pdf")
    decider = FakeDecider("confirmation", 0.9)
    r = scan(tmp_path, inbox, decider)
    assert r.new_cases == ["unknown_20261116T080000"] and r.skipped == [("b.pdf", "duplicate")]               # b was classed before a had its case
    assert [d.context["file_name"] for d in decider.decisions] == ["a.pdf", "b.pdf"]


def test_the_cases_are_searched_for_a_hash_once_for_each_file_of_a_scan(tmp_path, madeup_out, monkeypatch):
    asked = []
    real = find_case_by_hash
    monkeypatch.setattr("aid.sources.mailbox.find_case_by_hash", lambda root, digest: asked.append(digest) or real(root, digest))
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU01_conf.pdf", "a.pdf")
    put(inbox, madeup_out / "MU01_conf.pdf", "b.pdf")                                                           # a copy in the same scan
    put(inbox, madeup_out / "MU02_conf.pdf")
    put(inbox, madeup_out / "MU01_po_v1.pdf")
    drawn(inbox / "pakbon.pdf", page_of("Pakbon 5"))
    (inbox / "notities.docx").write_bytes(b"made up")                                                           # a type that is not read is not hashed
    r = scan(tmp_path, inbox)
    assert len(r.new_cases) == 2 and r.skipped == [("b.pdf", "duplicate"), ("notities.docx", "unsupported file type: .docx"), ("pakbon.pdf", "packing_slip")]
    assert len(asked) == 5 and sorted(set(asked)) == sorted({sha256_file(p) for p in inbox.glob("processed/*.pdf")})


def test_the_same_attachment_twice_in_one_e_mail_is_a_duplicate(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "m.eml", attachments=[madeup_out / "MU01_conf.pdf", ("kopie.pdf", (madeup_out / "MU01_conf.pdf").read_bytes())])
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000"] and r.skipped == [("m.eml/kopie.pdf", "duplicate")]


def test_the_review_items_in_the_cases_folder_do_not_disturb_the_duplicate_check(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU05_conf.pdf")                                                                   # a scan: a review item, so a file in cases/
    assert scan(tmp_path, inbox).new_cases == [] and review_items(tmp_path) != []
    put(inbox, madeup_out / "MU01_conf.pdf")
    assert len(scan(tmp_path, inbox).new_cases) == 1
    put(inbox, madeup_out / "MU01_conf.pdf", "again.pdf")
    assert scan(tmp_path, inbox).skipped == [("again.pdf", "duplicate")]


def test_the_same_text_in_another_file_is_not_a_duplicate(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU01_conf.pdf")
    drawn_confirmation(inbox / "other_bytes.pdf", title="another title")                                       # the same text, other bytes
    r = scan(tmp_path, inbox)
    assert len(r.new_cases) == 2 and r.skipped == []


# the quick read of a confirmation that names no PO

def test_a_confirmation_without_a_po_number_goes_to_the_one_po_that_holds_its_lines(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    PoStore(tmp_path / "store").add(madeup_out / "MU01_po_v1.pdf")
    drawn_confirmation(inbox / "conf.pdf", drop=("po_reference",))
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000"] and load_case(tmp_path / "cases", r.new_cases[0]).po_number == "250101" and r.skipped == []


def test_a_confirmation_without_a_po_number_that_no_po_holds_gets_a_case_without_one(tmp_path):
    inbox = inbox_of(tmp_path)
    drawn_confirmation(inbox / "conf.pdf", drop=("po_reference",))                                             # the store holds nothing
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["unknown_20261116T080000"] and load_case(tmp_path / "cases", r.new_cases[0]).po_number is None
    assert r.skipped == [] and review_items(tmp_path) == []                                                     # the routing, not the source, sends it to review


def test_a_confirmation_whose_lines_two_pos_hold_gets_a_case_without_a_po_number(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    store = PoStore(tmp_path / "store")
    store.add(madeup_out / "MU01_po_v1.pdf")
    store.add(drawn_po(tmp_path / "twin.pdf", heading="Inkooporder 250199"))                                   # another PO with the same lines
    drawn_confirmation(inbox / "conf.pdf", drop=("po_reference",))
    assert scan(tmp_path, inbox).new_cases == ["unknown_20261116T080000"]


def test_a_confirmation_in_a_layout_that_no_template_knows_gets_a_case_without_a_po_number(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    PoStore(tmp_path / "store").add(madeup_out / "MU01_po_v1.pdf")
    drawn(inbox / "conf.pdf", page_of("Orderbevestiging", "Wij bevestigen uw bestelling van 10 slangen KM-1001 en 4 klemmen KM-1002."))
    assert scan(tmp_path, inbox).new_cases == ["unknown_20261116T080000"]


def test_the_layout_templates_are_loaded_once_per_scan_and_only_when_needed(tmp_path, madeup_out, monkeypatch):
    loads = []
    real = load_templates
    monkeypatch.setattr("aid.sources.mailbox.load_templates", lambda: loads.append(1) or real())
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU01_conf.pdf")                                                                   # names its PO: no quick read
    scan(tmp_path, inbox)
    assert loads == []
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        drawn_confirmation(inbox / name, drop=("po_reference",), title=name)
    scan(tmp_path, inbox, now=datetime(2026, 11, 16, 9, 0))
    assert loads == [1]


def test_a_template_that_does_not_fit_the_pdf_is_no_error_but_no_lines(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    PoStore(tmp_path / "store").add(madeup_out / "MU01_po_v1.pdf")
    drawn(inbox / "conf.pdf", page_of("S01", "Orderbevestiging", "Pos Artikel Omschrijving Pos", "1 KM-1001 slang 1"))     # table_nl detects it, cannot read it
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["unknown_20261116T080000"] and r.skipped == [] and (inbox / "processed" / "conf.pdf").exists()


# case ids

def test_cases_of_one_po_opened_in_one_scan_get_ids_of_their_own(tmp_path):
    inbox = inbox_of(tmp_path)
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        drawn_confirmation(inbox / name)                                                                       # three different files naming PO 250101
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000", "250101_20261116T080001", "250101_20261116T080002"] and r.skipped == []
    assert [load_case(tmp_path / "cases", c).input_files[0].name for c in r.new_cases] == ["a.pdf", "b.pdf", "c.pdf"]


def test_cases_without_a_po_number_opened_in_one_scan_get_ids_of_their_own(tmp_path):
    inbox = inbox_of(tmp_path)
    for name in ("a.pdf", "b.pdf"):
        drawn_confirmation(inbox / name, drop=("po_reference",))
    assert scan(tmp_path, inbox).new_cases == ["unknown_20261116T080000", "unknown_20261116T080001"]


def test_a_case_that_exists_is_not_touched_by_a_later_scan_at_the_same_time(tmp_path):
    inbox = inbox_of(tmp_path)
    drawn_confirmation(inbox / "first.pdf")
    [first] = scan(tmp_path, inbox).new_cases
    before = (tmp_path / "cases" / first / "case.json").read_bytes()
    drawn_confirmation(inbox / "second.pdf", title="second")
    assert scan(tmp_path, inbox).new_cases == ["250101_20261116T080001"] and (tmp_path / "cases" / first / "case.json").read_bytes() == before


def test_when_no_id_is_free_the_file_stays_as_an_error(tmp_path, monkeypatch):
    tried = []
    def taken(cases_root, kind, files, po_number, now, found_by=None):
        tried.append(now)
        raise FileExistsError("the case exists")
    monkeypatch.setattr("aid.sources.mailbox.open_case", taken)
    inbox = inbox_of(tmp_path)
    drawn_confirmation(inbox / "conf.pdf")
    r = scan(tmp_path, inbox)
    assert r.new_cases == [] and r.skipped == [("conf.pdf", "error: the case exists")] and (inbox / "conf.pdf").exists()
    assert len(tried) == CASE_ID_TRIES and tried[-1] - tried[0] == timedelta(seconds=CASE_ID_TRIES - 1)


# filing

def test_processed_files_keep_their_name_and_a_taken_name_gets_a_number_before_the_extension(tmp_path):
    inbox = inbox_of(tmp_path)
    processed = inbox / "processed"
    processed.mkdir()
    for name in ("doc.pdf", "doc_2.pdf", "a.b.pdf"):
        drawn(processed / name, page_of("old"))
    for name in ("doc.pdf", "a.b.pdf"):
        drawn(inbox / name, page_of("Pakbon 5"))
    scan(tmp_path, inbox)
    assert sorted(p.name for p in processed.iterdir()) == ["a.b.pdf", "a.b_2.pdf", "doc.pdf", "doc_2.pdf", "doc_3.pdf"]
    assert (processed / "doc_3.pdf").read_bytes() != (processed / "doc.pdf").read_bytes()                      # the new one, not the old one, got the number
    drawn(inbox / "doc.pdf", page_of("Pakbon 6"))
    scan(tmp_path, inbox)
    assert (processed / "doc_4.pdf").exists()


def test_folders_in_the_folder_are_not_scanned(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    for folder in ("processed", "archive"):
        (inbox / folder).mkdir()
        put(inbox / folder, madeup_out / "MU01_conf.pdf", "old.pdf")
    r = scan(tmp_path, inbox)
    assert r == ScanResult(new_cases=[], stored_pos=[], skipped=[]) and sorted(p.name for p in inbox.iterdir()) == ["archive", "processed"]
    assert [p.name for p in (inbox / "processed").iterdir()] == ["old.pdf"] and [p.name for p in (inbox / "archive").iterdir()] == ["old.pdf"]


def test_an_empty_folder_gives_an_empty_result_and_creates_nothing(tmp_path):
    inbox = inbox_of(tmp_path)
    assert scan(tmp_path, inbox) == ScanResult(new_cases=[], stored_pos=[], skipped=[])
    assert list(inbox.iterdir()) == [] and list(tmp_path.iterdir()) == [inbox]


def test_a_folder_that_does_not_exist_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        scan(tmp_path, tmp_path / "nothing")


def test_the_result_is_new_for_every_scan(tmp_path):
    inbox = inbox_of(tmp_path)
    drawn(inbox / "doc.pdf", page_of("Pakbon 5"))
    first = scan(tmp_path, inbox)
    assert first.skipped == [("doc.pdf", "packing_slip")] and scan(tmp_path, inbox).skipped == [] and ScanResult().skipped == []


# files that cannot be handled

def test_a_file_that_raises_stays_where_it_is_and_the_others_are_handled(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    (inbox / "a_broken.pdf").write_bytes(b"this is not a PDF")
    put(inbox, madeup_out / "MU01_conf.pdf")
    r = scan(tmp_path, inbox)
    assert len(r.new_cases) == 1 and len(r.skipped) == 1 and r.skipped[0][0] == "a_broken.pdf" and r.skipped[0][1].startswith("error: ")
    assert "\n" not in r.skipped[0][1] and len(r.skipped[0][1]) > len("error: ")
    assert (inbox / "a_broken.pdf").read_bytes() == b"this is not a PDF" and sorted(p.name for p in inbox.iterdir()) == ["a_broken.pdf", "processed"]
    assert review_items(tmp_path) == []
    assert scan(tmp_path, inbox, now=datetime(2026, 11, 16, 8, 1)).skipped == r.skipped                      # tried again, and failed again


def test_an_attachment_that_raises_keeps_its_e_mail_and_the_other_attachments_are_handled(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "m.eml", attachments=[("kapot.pdf", b"not a PDF"), madeup_out / "MU01_conf.pdf"])
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000"] and [name for name, _ in r.skipped] == ["m.eml/kapot.pdf"] and r.skipped[0][1].startswith("error: ")
    assert (inbox / "m.eml").exists() and not (inbox / "processed").exists()
    again = scan(tmp_path, inbox, now=datetime(2026, 11, 16, 8, 1))                                           # the case is not opened twice
    assert again.new_cases == [] and again.skipped == [("m.eml/kapot.pdf", r.skipped[0][1]), ("m.eml/MU01_conf.pdf", "duplicate")]
    assert len(case_names(tmp_path)) == 1


def test_a_file_that_cannot_be_moved_stays_after_its_case_was_opened(tmp_path, madeup_out, monkeypatch):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU01_conf.pdf")
    monkeypatch.setattr(Path, "rename", lambda self, target: (_ for _ in ()).throw(PermissionError("file in use")))
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000"] and r.skipped == [("MU01_conf.pdf", "error: file in use")] and (inbox / "MU01_conf.pdf").exists()
    monkeypatch.undo()
    assert scan(tmp_path, inbox, now=datetime(2026, 11, 16, 8, 1)).skipped == [("MU01_conf.pdf", "duplicate")] and (inbox / "processed" / "MU01_conf.pdf").exists()


def test_an_e_mail_that_cannot_be_opened_stays(tmp_path, monkeypatch):
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "m.eml", subject="S")
    def broken(path, out_dir): raise OSError("disk full")
    monkeypatch.setattr("aid.sources.mailbox.extract_eml", broken)
    assert scan(tmp_path, inbox).skipped == [("m.eml", "error: disk full")] and (inbox / "m.eml").exists()


# e-mails that pack their attachments deeper

def test_a_confirmation_in_a_nested_e_mail_opens_its_case(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    nested_eml(inbox / "m.eml", [("MU01_conf.pdf", (madeup_out / "MU01_conf.pdf").read_bytes())])
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000"] and r.skipped == [] and (inbox / "processed" / "m.eml").exists()


def test_a_confirmation_in_a_signed_e_mail_opens_its_case_and_the_signature_is_listed_as_a_file_not_read(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    nested_eml(inbox / "m.eml", [("MU01_conf.pdf", (madeup_out / "MU01_conf.pdf").read_bytes())], signed=True)
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000"] and r.skipped == [("m.eml/smime.p7s", "unsupported file type: .p7s")] and review_items(tmp_path) == []


@pytest.mark.parametrize("build", SHAPES.values(), ids=SHAPES.keys())
def test_a_confirmation_in_a_part_that_makes_the_body_opens_its_case_and_is_not_filed_away_unseen(tmp_path, madeup_out, build):
    inbox = inbox_of(tmp_path)
    message = build((madeup_out / "MU01_conf.pdf").read_bytes(), "MU01_conf.pdf")
    message["Subject"] = "Bevestiging"
    (inbox / "m.eml").write_bytes(message.as_bytes())
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000"] and r.skipped == [] and (inbox / "processed" / "m.eml").exists()


def test_a_confirmation_without_an_extension_is_read_as_a_pdf(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "m.eml", attachments=[("Orderbevestiging", (madeup_out / "MU01_conf.pdf").read_bytes(), "application/pdf")])
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000"] and [f.name for f in load_case(tmp_path / "cases", r.new_cases[0]).input_files] == ["Orderbevestiging.pdf"]


def test_an_attached_e_mail_is_skipped_under_its_own_name_and_the_pdf_in_it_is_not_taken(tmp_path, madeup_out):
    forwarded = EmailMessage()
    forwarded["Subject"] = "Doorgestuurd"
    forwarded.set_content("Zie bijlage")
    forwarded.add_attachment((madeup_out / "MU01_conf.pdf").read_bytes(), maintype="application", subtype="pdf", filename="MU01_conf.pdf")
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "m.eml", attachments=[("Fwd bevestiging.eml", forwarded), madeup_out / "MU02_conf.pdf"])
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250102_20261116T080000"] and r.skipped == [("m.eml/Fwd bevestiging.eml", "unsupported file type: .eml")]


def test_a_mail_with_only_a_picture_in_its_body_is_an_e_mail_without_attachments_and_gives_no_review_item(tmp_path):
    inbox = inbox_of(tmp_path)
    logo_eml(inbox / "m.eml")
    assert scan(tmp_path, inbox).skipped == [("m.eml", "no attachment")] and review_items(tmp_path) == [] and (inbox / "processed" / "m.eml").exists()


# files of other types, and e-mails without documents

def test_files_and_attachments_of_other_types_are_skipped_and_filed(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    (inbox / "notities.docx").write_bytes(b"made up")
    (inbox / "README").write_bytes(b"made up")
    write_eml(inbox / "m.eml", attachments=[("logo.jpg", b"made up"), madeup_out / "MU01_conf.pdf", ("Voorwaarden.DOCX", b"made up")])
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000"]
    assert r.skipped == [("README", "unsupported file type: no extension"), ("m.eml/logo.jpg", "unsupported file type: .jpg"),
                         ("m.eml/Voorwaarden.DOCX", "unsupported file type: .docx"), ("notities.docx", "unsupported file type: .docx")]
    assert sorted(p.name for p in (inbox / "processed").iterdir()) == ["README", "m.eml", "notities.docx"] and review_items(tmp_path) == []


def test_an_e_mail_without_attachments_is_skipped_and_filed(tmp_path):
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "m.eml", subject="Bevestiging PO 250101", body="Alles akkoord.")
    assert scan(tmp_path, inbox).skipped == [("m.eml", "no attachment")] and (inbox / "processed" / "m.eml").exists()


def test_an_extension_in_capitals_is_the_same_type(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU01_conf.pdf", "BEVESTIGING.PDF")
    write_eml(tmp_path / "x.eml", subject="S")
    put(inbox, tmp_path / "x.eml", "MAIL.EML")
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000"] and r.skipped == [("MAIL.EML", "no attachment")]


def test_what_is_skipped_is_listed_by_file_and_the_attachments_of_an_e_mail_in_their_order(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "m.eml", attachments=[drawn(tmp_path / "pakbon.pdf", page_of("Pakbon 5")), ("logo.jpg", b"made up"),
                                            drawn(tmp_path / "factuur.pdf", page_of("Factuur 7")), ("kapot.pdf", b"not a PDF"),
                                            (None, b"made up", "application/x-made-up"), madeup_out / "MU05_conf.pdf"])
    (inbox / "a.docx").write_bytes(b"made up")
    drawn(inbox / "z.pdf", page_of("Offerte 3"))
    skipped = scan(tmp_path, inbox).skipped
    assert [name for name, _ in skipped] == ["a.docx", "m.eml/pakbon.pdf", "m.eml/logo.jpg", "m.eml/factuur.pdf", "m.eml/kapot.pdf",
                                             "m.eml/attachment_5", "m.eml/MU05_conf.pdf", "z.pdf"]
    assert [reason for _, reason in skipped if "pakbon" in _ or "factuur" in _ or "logo" in _] == ["packing_slip", "unsupported file type: .jpg", "invoice"]
    assert skipped[4][1].startswith("error: ") and skipped[5][1] == "unsupported file type: no extension" and skipped[6][1] == "no text layer; class unknown"


def test_what_is_skipped_is_listed_in_the_order_of_the_files(tmp_path, madeup_out):
    inbox = inbox_of(tmp_path)
    (inbox / "a.docx").write_bytes(b"made up")
    drawn(inbox / "b.pdf", page_of("Pakbon 5"))
    (inbox / "c.docx").write_bytes(b"made up")
    put(inbox, madeup_out / "MU01_conf.pdf", "d.pdf")
    put(inbox, madeup_out / "MU01_conf.pdf", "e.pdf")
    assert [name for name, _ in scan(tmp_path, inbox).skipped] == ["a.docx", "b.pdf", "c.docx", "e.pdf"]


def test_a_dot_file_is_neither_read_nor_skipped_nor_moved(tmp_path, madeup_out):
    inbox = tmp_path / "inbox"; inbox.mkdir(); shutil.copy(madeup_out / "MU01_conf.pdf", inbox)
    (inbox / ".DS_Store").write_bytes(b"made-up"); (inbox / ".hidden.pdf").write_bytes((madeup_out / "MU02_conf.pdf").read_bytes())
    r = scan_folder(inbox, PoStore(tmp_path / "store"), tmp_path / "cases", SETTINGS, datetime(2026, 11, 16, 8, 0))
    assert len(r.new_cases) == 1 and r.skipped == [] and (inbox / "processed" / "MU01_conf.pdf").exists()
    assert (inbox / ".DS_Store").read_bytes() == b"made-up" and (inbox / ".hidden.pdf").exists()
    assert not (inbox / "processed" / ".DS_Store").exists() and not (inbox / "processed" / ".hidden.pdf").exists()


# --- the answers to the aid's questions (final review, finding 9) ---

QUESTION_ID = "0123456789abcdef0123456789abcdef"            # the made-up id of a question the aid sent


def asked_case(tmp_path: Path, madeup_out):
    """A case under tmp_path/cases that sent a question (QUESTION_ID) and waits for the supplier."""
    from aid.case import open_case
    from aid.messaging import SentRecord, record_sent
    from aid.tests.fakes import case_result
    c = open_case(tmp_path / "cases", "confirmation", [madeup_out / "MU17_conf.pdf"], "250117", NOW)
    record_sent(c, SentRecord(message_id=QUESTION_ID, to=["sales@s17.example.com"], subject="Vraag over orderbevestiging PO 250117",
                              sent_at=NOW.isoformat(), template="line_missing_extra"))
    c.write_step(8, "result", case_result(c.case_id, "waiting"))
    return c


def answer_eml(path: Path, subject: str, sender: str = "sales@s17.example.com", attachments: list = ()) -> Path:
    """An e-mail that answers the question QUESTION_ID (In-Reply-To), with these attachments."""
    from email import message_from_bytes, policy
    message = message_from_bytes(write_eml(path, subject=subject, body="De hoeveelheid klopt.", attachments=attachments).read_bytes(),
                                 policy=policy.default)
    message.replace_header("From", sender)
    message["In-Reply-To"] = f"<{QUESTION_ID}@example.com>"
    path.write_bytes(message.as_bytes())
    return path


def test_a_reply_to_a_question_is_filed_into_its_case_and_opens_nothing(tmp_path, madeup_out):
    c = asked_case(tmp_path, madeup_out)
    inbox = inbox_of(tmp_path)
    answer_eml(inbox / "antwoord.eml", "Re: Vraag over orderbevestiging PO 250117", attachments=[madeup_out / "MU17_conf.pdf"])
    r = scan(tmp_path, inbox)
    assert r.replies == [("antwoord.eml", c.case_id)] and r.answered == [c.case_id] and r.new_cases == [] and r.skipped == []
    assert (c.root / "messages" / "replies" / "antwoord.eml").exists() and (inbox / "processed" / "antwoord.eml").exists()
    result = load_case(tmp_path / "cases", c.case_id).read_step("result")
    assert result["status"] == "review" and [i["reason"] for i in result["review_items"]] == ["supplier answered: read the reply"]


def test_a_bounce_of_a_question_is_filed_under_bounces_and_the_result_is_left(tmp_path, madeup_out):
    c = asked_case(tmp_path, madeup_out)
    before = c.read_step("result")
    inbox = inbox_of(tmp_path)
    answer_eml(inbox / "bounce.eml", "Undeliverable: Vraag over orderbevestiging PO 250117", sender="postmaster@example.com")
    r = scan(tmp_path, inbox)
    assert r.replies == [("bounce.eml", c.case_id)] and r.answered == [] and r.skipped == []
    assert (c.root / "messages" / "bounces" / "bounce.eml").exists() and c.read_step("result") == before


def test_an_e_mail_that_answers_no_question_is_handled_as_before(tmp_path, madeup_out):
    c = asked_case(tmp_path, madeup_out)
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "nieuw.eml", subject="Orderbevestiging", attachments=[madeup_out / "MU01_conf.pdf"])
    write_eml(inbox / "leeg.eml", subject="Re: iets anders")
    r = scan(tmp_path, inbox)
    assert r.replies == [] and len(r.new_cases) == 1 and r.skipped == [("leeg.eml", "no attachment")]
    assert not (c.root / "messages" / "replies").exists() and c.read_step("result")["status"] == "waiting"


# --- a PO layout in the settings: a PDF that fits it is a PO PDF ---

LAYOUT = SETTINGS.model_copy(update={"po_layout": MADEUP_LAYOUT})          # the made-up layout as the layout of the settings
EXACT = "inkoop@example.org"                                               # the made-up address the PO e-mails come from
ASKING = "Graag ontvangen wij uw orderbevestiging per e-mail."             # what a PO may say to its supplier


def asking_po(path: Path, case_id: str = "MU01") -> Path:
    """The first version of the made-up PO of `case_id` with a last row that asks the supplier for a confirmation."""
    spec = SPECS[case_id]
    pages = po_pages(spec, spec.po.versions[0])
    pages[0] += page_of(ASKING)
    return drawn(path, *pages)


def returned_po(path: Path, case_id: str = "MU08", title: tuple[str, ...] = ("S08", "Orderbevestiging")) -> Path:
    """A supplier's document that repeats the first page of the made-up PO of `case_id`, its PO label and title row included, below
    rows of its own (by default the supplier's name and the title of a confirmation)."""
    spec = SPECS[case_id]
    pages = po_pages(spec, spec.po.versions[0])
    pages[0][0:0] = page_of(*title)
    return drawn(path, *pages)


def test_with_a_layout_a_po_that_mentions_a_confirmation_is_filed_as_a_po(tmp_path):
    """A PO that asks for an order confirmation below its lines holds a keyword of a confirmation, which ranks first: without a layout
    it is taken for a confirmation and opens a case. With the layout of the settings it fits, and its header (the lines above the
    column titles) holds no such keyword, so it is a PO: it is filed, read with the layout, and no case is opened. A PO whose header
    holds the keyword is taken for a confirmation, as a supplier's confirmation that repeats the PO's page is."""
    inbox = inbox_of(tmp_path)
    po = asking_po(inbox / "po.pdf")
    assert classify_attachment(po, None) == "confirmation" and not titled_confirmation(first_page(po), MADEUP_LAYOUT)
    r = scan(tmp_path, inbox, settings=LAYOUT)
    assert r.stored_pos == ["250101"] and r.new_cases == [] and r.skipped == [] and case_names(tmp_path) == []
    [version] = PoStore(tmp_path / "store").versions("250101")
    assert (version.version, version.document.reader, len(version.document.lines)) == (1, "po_pdf:layout", 3)
    assert (inbox / "processed" / "po.pdf").exists()
    decider = FakeDecider("confirmation", 1.0)                              # a fitting PDF costs no decision
    asking_po(inbox / "po2.pdf", "MU02")
    assert scan(tmp_path, inbox, decider, settings=LAYOUT).stored_pos == ["250102"] and decider.decisions == []
    pages = po_pages(SPECS["MU03"], SPECS["MU03"].po.versions[0])
    pages[0][6:6] = page_of("Orderbevestiging verplicht binnen 2 werkdagen")     # in the header, right above the column titles
    asks_in_its_header = drawn(inbox / "po3.pdf", *pages)
    assert fits(first_page(asks_in_its_header), MADEUP_LAYOUT) and titled_confirmation(first_page(asks_in_its_header), MADEUP_LAYOUT)
    r = scan(tmp_path, inbox, settings=LAYOUT, now=datetime(2026, 11, 16, 9, 0))
    assert r.stored_pos == [] and r.new_cases == ["250103_20261116T090000"]


def test_a_document_titled_as_a_confirmation_opens_a_case_though_it_fits(tmp_path, madeup_out):
    """A supplier's confirmation that repeats the PO's own label and title row fits the layout, but a line above its title row holds a
    keyword of a confirmation: it is classed by the keywords, as without a layout, and opens a case. So does one whose title stands
    on its fourth line, below a letterhead of three lines. The made-up confirmations, which do not fit, open their cases as before."""
    inbox = inbox_of(tmp_path)
    returned = returned_po(inbox / "bevestiging.pdf")
    assert fits(first_page(returned), MADEUP_LAYOUT) and titled_confirmation(first_page(returned), MADEUP_LAYOUT)
    put(inbox, madeup_out / "MU01_conf.pdf")
    r = scan(tmp_path, inbox, settings=LAYOUT)
    assert r.new_cases == ["250101_20261116T080000", "250108_20261116T080000"] and r.stored_pos == [] and r.skipped == []
    assert [f.name for f in load_case(tmp_path / "cases", r.new_cases[1]).input_files] == ["bevestiging.pdf"]
    assert not (tmp_path / "store").exists()
    low = returned_po(inbox / "laag.pdf", title=("S08", "Postbus 1", "1234 AB Voorbeeldstad", "Orderbevestiging"))
    assert fits(first_page(low), MADEUP_LAYOUT) and titled_confirmation(first_page(low), MADEUP_LAYOUT)     # the title is its fourth line
    r = scan(tmp_path, inbox, settings=LAYOUT, now=datetime(2026, 11, 16, 9, 0))
    assert r.stored_pos == [] and r.new_cases == ["250108_20261116T090000"] and not (tmp_path / "store").exists()


TITLES = "Regel Artikel Omschrijving Aantal Eenheid Prijs Per Leverdatum"     # the title row of the made-up layout
LINE = "1 KM-1001 Kogelkraan 10 st 12,50 1 16-11-2026"


@pytest.mark.parametrize("header, titled", [
    ("S01\nOrderbevestiging\nUw inkooporder 250101", True), ("ORDER CONFIRMATION 4500123\nACME Ltd", True),
    ("Proefleverancier\nPostbus 1\n1234 AB Voorbeeldstad\nOrderbevestiging\nUw inkooporder 250101", True),   # below a letterhead
    ("Purchase order confirmation 4500123", True), ("Uw inkooporder 250101: orderbevestiging", True),        # anywhere on its line
    ("S04\n\n  \nAdres\nAuftragsbestätigung Nr. 7", True), ("AUFTRAGSBESTATIGUNG 7", True),                  # case and accents
    ("Inkooporder 250101\nVersie 1", False), ("Orderbevestigingen 2026\nS01", False),                       # no keyword as a word
    ("", False),
])
def test_a_header_that_holds_a_confirmation_keyword(header, titled):
    """With a layout set a PDF counts as titled as a confirmation when a line of its first page above its first title row holds a
    keyword of a confirmation as a whole word or phrase. Below the title row a keyword counts for nothing."""
    assert titled_confirmation(f"{header}\n{TITLES}\n{LINE}", MADEUP_LAYOUT) is titled
    assert titled_confirmation(f"Inkooporder 250101\n{TITLES}\n{LINE}\n{header}", MADEUP_LAYOUT) is False
    assert titled_confirmation(header, MADEUP_LAYOUT) is titled                 # a page without the title row: all its lines


def test_sender_of_gives_the_address_of_the_from_header_in_lower_case(tmp_path):
    assert sender_of(write_eml(tmp_path / "a.eml", sender="Inkoop <Inkoop@Example.ORG>")) == "inkoop@example.org"
    assert sender_of(write_eml(tmp_path / "b.eml", sender="sales@s01.example.com")) == "sales@s01.example.com"
    assert sender_of(write_eml(tmp_path / "c.eml", sender=None)) is None
    (tmp_path / "d.eml").write_bytes(b"From: not an address\r\nSubject: S\r\n\r\ntekst\r\n")
    assert sender_of(tmp_path / "d.eml") is None


def test_a_fitting_pdf_from_another_sender_opens_a_case(tmp_path, madeup_out):
    """With senders in the settings, a PDF that fits and came in an e-mail from anyone else is a confirmation, also when its title
    makes it a PO by the keywords, as a supplier's copy of the PO is: it opens a case and is not filed. From a listed address or
    domain it is filed, and so is a file that lies in the folder by itself, which has no sender."""
    listed = LAYOUT.model_copy(update={"po_senders": [EXACT]})
    po = madeup_out / "MU01_po_v1.pdf"
    assert classify_attachment(po, None) == "po_pdf"
    inbox = inbox_of(tmp_path)
    write_eml(inbox / "a_supplier.eml", subject="Uw order", attachments=[po], sender="sales@s01.example.com")
    write_eml(inbox / "b_no_sender.eml", subject="Order", attachments=[madeup_out / "MU02_po_v1.pdf"], sender=None)
    r = scan(tmp_path, inbox, settings=listed)
    assert r.new_cases == ["250101_20261116T080000", "250102_20261116T080000"] and r.stored_pos == [] and r.skipped == []
    assert [f.name for f in load_case(tmp_path / "cases", r.new_cases[0]).input_files] == ["MU01_po_v1.pdf"]
    assert not (tmp_path / "store").exists()
    write_eml(inbox / "c_exact.eml", attachments=[madeup_out / "MU03_po_v1.pdf"], sender=f"Inkoop <{EXACT.upper()}>")
    put(inbox, madeup_out / "MU04_po_v1.pdf", "d_loose.pdf")
    r = scan(tmp_path, inbox, settings=listed, now=datetime(2026, 11, 16, 9, 0))
    assert r.stored_pos == ["250103", "250104"] and r.new_cases == [] and r.skipped == []
    # The supplier sends the same e-mail again: a fitting PDF that the sender list makes a confirmation is searched for as any
    # confirmation is, so it is a duplicate of the case it opened and opens no second one.
    write_eml(inbox / "e_supplier_again.eml", subject="Uw order", attachments=[po], sender="sales@s01.example.com")
    r = scan(tmp_path, inbox, settings=listed, now=datetime(2026, 11, 16, 10, 0))
    assert r.skipped == [("e_supplier_again.eml/MU01_po_v1.pdf", "duplicate")] and r.new_cases == [] and r.stored_pos == []
    assert len(case_names(tmp_path)) == 2
    for number, (senders, sender, filed) in enumerate([
            (["example.org"], "erp@example.org", True), (["@Example.org "], "erp@example.org", True),      # a domain, with or without "@"
            (["example.org"], "erp@mail.example.org", False), (["example.org"], "erp@example.org.example.com", False),
            (["other@example.org", EXACT], EXACT, True), ([], "sales@s01.example.com", True)]):             # an empty list: any sender
        folder = tmp_path / f"run{number}"
        folder.mkdir()
        write_eml(inbox_of(folder) / "m.eml", attachments=[madeup_out / "MU05_po_v1.pdf"], sender=sender)
        r = scan(folder, folder / "inbox", settings=LAYOUT.model_copy(update={"po_senders": senders}))
        assert (r.stored_pos, len(r.new_cases)) == ((["250105"], 0) if filed else ([], 1)), (senders, sender)


def test_a_fitting_pdf_is_filed_though_a_case_holds_its_bytes(tmp_path, madeup_out):
    """Before the layout was set, a PO that asks for a confirmation opened a case, which holds its bytes. Given again with the layout
    set it is filed as a PO: the fit is tested before the cases are searched for a duplicate. A PDF that does not fit is still a
    duplicate of the case that holds it."""
    inbox = inbox_of(tmp_path)
    po = asking_po(tmp_path / "po.pdf")
    put(inbox, po)
    put(inbox, madeup_out / "MU02_conf.pdf")
    first = scan(tmp_path, inbox)
    assert first.new_cases == ["250102_20261116T080000", "250101_20261116T080000"] and first.stored_pos == []
    assert find_case_by_hash(tmp_path / "cases", sha256_file(po)).case_id == "250101_20261116T080000"
    put(inbox, po, "again.pdf")
    put(inbox, madeup_out / "MU02_conf.pdf", "conf_again.pdf")
    assert scan(tmp_path, inbox, now=datetime(2026, 11, 16, 9, 0)).skipped == [("again.pdf", "duplicate"), ("conf_again.pdf", "duplicate")]
    put(inbox, po, "again.pdf")
    put(inbox, madeup_out / "MU02_conf.pdf", "conf_again.pdf")
    r = scan(tmp_path, inbox, settings=LAYOUT, now=datetime(2026, 11, 16, 10, 0))
    assert r.stored_pos == ["250101"] and r.new_cases == [] and r.skipped == [("conf_again.pdf", "duplicate")]
    assert [v.version for v in PoStore(tmp_path / "store").versions("250101")] == [1] and len(case_names(tmp_path)) == 2


def test_with_a_layout_a_po_pdf_that_does_not_fit_stays_as_an_error(tmp_path, madeup_out):
    """With a layout set no PDF is read with the made-up template: a made-up PO, which the keywords still class as a PO, is refused
    by the store under the second layout and stays in the folder, and the second made-up PO is filed. Without a layout the second
    made-up PO has no keyword and is skipped as before."""
    anders = SETTINGS.model_copy(update={"po_layout": ANDERS_LAYOUT})
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU01_po_v1.pdf")
    put(inbox, ANDERS_PO)
    r = scan(tmp_path, inbox, settings=anders)
    assert r.stored_pos == ["IB-2600417"] and r.new_cases == [] and [name for name, _ in r.skipped] == ["MU01_po_v1.pdf"]
    assert r.skipped[0][1].startswith("error: MU01_po_v1.pdf: the PDF does not fit the purchase order layout")
    assert "250101" not in r.skipped[0][1] and "Inkooporder" not in r.skipped[0][1]                     # no text of the PDF in the reason
    assert (inbox / "MU01_po_v1.pdf").exists() and (inbox / "processed" / "ANDERS_po_v1.pdf").exists()
    assert PoStore(tmp_path / "store").versions("250101") == []
    other = tmp_path / "other"
    other.mkdir()
    put(inbox_of(other), ANDERS_PO)
    assert scan(other, other / "inbox", FakeDecider(None, 0.0)).skipped == [("ANDERS_po_v1.pdf", "other")]


def test_a_po_number_that_cannot_be_a_folder_name_is_skipped_without_the_number(tmp_path):
    """A PO PDF that fits and whose PO number holds a slash is refused by the store: it stays in the folder as an error, and the reason,
    which the digest and runner.log repeat, names the setting and not the number."""
    inbox = inbox_of(tmp_path)
    drawn_po(inbox / "po.pdf", heading="Inkooporder PO/2026/0417")
    r = scan(tmp_path, inbox, settings=LAYOUT)
    [(name, reason)] = r.skipped
    assert name == "po.pdf" and reason.startswith("error: po.pdf: the PO number") and "po_layout.po_label" in reason
    assert "PO/2026/0417" not in reason and "2026" not in reason and (inbox / "po.pdf").exists() and r.stored_pos == []


def test_title_words_printed_close_together_are_held_against_the_layout_as_its_reader_cuts_them(tmp_path):
    """The scan tests the fit on the first page as the layout reader reads it, with cells printed close together cut apart: a PO whose
    titles Prijs and Per stand 2 points apart fits and is filed."""
    pages = po_pages(SPECS["MU01"], SPECS["MU01"].po.versions[0])
    prijs, per = (next(t for t in pages[0][6].texts if t.text == word) for word in ("Prijs", "Per"))
    per.x = prijs.x + stringWidth(prijs.text, prijs.font, prijs.size) + 2
    inbox = inbox_of(tmp_path)
    drawn(inbox / "po.pdf", *pages)
    assert "PrijsPer" in "\n".join(line.text for line in extract_lines(inbox / "po.pdf"))
    r = scan(tmp_path, inbox, settings=LAYOUT)
    assert r.stored_pos == ["250101"] and r.skipped == [] and r.new_cases == []


# --- an export of the ERP in the folder or on an e-mail (Task 40) ---

EXPORTS = SETTINGS.model_copy(update={"export_columns": EXPORT_COLUMNS})      # the column titles of the made-up export, as a map
EXPORT_NL = REPO_ROOT / "aid" / "madeup" / "tryout" / "EXPORT_nl.csv"           # the made-up export in a Dutch form


class Recording(PoStore):
    """The PO store, noting the name of each file it is given to file, in the order it gets them."""

    def __init__(self, root: Path):
        super().__init__(root)
        self.given: list[str] = []

    def add(self, pdf, layout=None):
        self.given.append(Path(pdf).name)
        return super().add(pdf, layout)

    def from_export(self, path, *args, **kwargs):
        self.given.append(Path(path).name)
        return super().from_export(path, *args, **kwargs)


def export_workbook(path: Path, case_id: str, quantity: str) -> Path:
    """The ERP export of the made-up PO of `case_id` saved as a workbook, the quantity of its first line given as `quantity`."""
    rows = list(csv.reader(io.StringIO(export_csv(SPECS[case_id]))))
    rows[1][rows[0].index("quantity")] = quantity
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    workbook.save(path)
    return path


def test_an_export_in_the_folder_or_on_an_e_mail_is_filed_first_and_listed(tmp_path, madeup_out):
    """With export_columns set, a .csv or .xlsx file one of whose rows holds every column title of the map is an export of the ERP. In
    the folder or as an attachment of an e-mail it is filed before the PO PDFs and the confirmations of the same scan, whatever its
    name, listed in stored_exports with the PO numbers of the file and those whose look it filed, and moved to processed/. So a
    confirmation that names no PO number finds the PO of an export of the same scan by its lines. The same rows again file nothing,
    and other rows are the next look."""
    inbox = inbox_of(tmp_path)
    put(inbox, madeup_out / "MU03_po_v1.pdf", "a_po.pdf")
    drawn_confirmation(inbox / "b_conf.pdf", drop=("po_reference",))                     # MU01's confirmation without its PO number
    (inbox / "z_export.csv").write_text(export_csv(SPECS["MU01"]), encoding="utf-8")      # PO 250101 as the ERP exports it, the last file
    store = Recording(tmp_path / "store")
    r = scan_folder(inbox, store, tmp_path / "cases", EXPORTS, NOW)
    assert store.given == ["z_export.csv", "a_po.pdf"]
    assert r.stored_exports == [("z_export.csv", ["250101"], ["250101"])] and r.stored_pos == ["250103"]
    assert r.new_cases == ["250101_20261116T080000"] and r.skipped == []
    assert sorted(p.name for p in (inbox / "processed").iterdir()) == ["a_po.pdf", "b_conf.pdf", "z_export.csv"]
    [look] = store.snapshots("250101")
    assert (look.document.reader, store.versions("250101")) == ("export:z_export.csv", [])
    write_eml(inbox / "m.eml", subject="Export uit Exact", attachments=[inbox / "processed" / "z_export.csv"])
    write_eml(inbox / "n.eml", subject="Export uit Exact", attachments=[export_workbook(tmp_path / "Orderregels.xlsx", "MU01", "12")])
    r = scan_folder(inbox, store, tmp_path / "cases", EXPORTS, NOW + timedelta(hours=1))
    assert r.stored_exports == [("m.eml/z_export.csv", ["250101"], []), ("n.eml/Orderregels.xlsx", ["250101"], ["250101"])]
    assert r.skipped == [] and r.new_cases == [] and r.stored_pos == []
    assert [look.document.lines[0].quantity.value for look in store.snapshots("250101")] == ["10", "12"]
    assert sorted(p.name for p in (inbox / "processed").iterdir()) == ["a_po.pdf", "b_conf.pdf", "m.eml", "n.eml", "z_export.csv"]


def test_another_spreadsheet_is_skipped_as_before(tmp_path, madeup_out):
    """A spreadsheet that is not an export is skipped and filed away, as before Task 40, and puts nothing into the store; the reason
    says why (ruling E8). With export_columns set, a .csv or .xlsx file that is_export does not take, because no row of it holds every
    column title of the map (a price list, an export with a title renamed) or because it cannot be read as a table (a damaged
    workbook), is skipped as a spreadsheet without the export's column titles, so the buyer sees in the digest why his file was not
    read. With export_columns empty every spreadsheet, the export itself included, is an unsupported file type, and an old .xls always
    is."""
    for name, settings in (("set", EXPORTS), ("empty", SETTINGS)):
        folder = tmp_path / name
        folder.mkdir()
        inbox = inbox_of(folder)
        (inbox / "export.csv").write_text(export_csv(SPECS["MU01"]), encoding="utf-8")
        (inbox / "titles.csv").write_text(export_csv(SPECS["MU01"]).replace("delivery_date", "leverdatum", 1), encoding="utf-8")
        (inbox / "kapot.xlsx").write_bytes(b"PK\x03\x04 a workbook cut off in the mail")
        (inbox / "prijslijst.csv").write_text("artikel;prijs\nKM-1001;12,50\n", encoding="utf-8")
        workbook = Workbook()
        workbook.active.append(["artikel", "prijs"])
        workbook.active.append(["KM-1001", 12.5])
        workbook.save(inbox / "prijslijst.xlsx")
        (inbox / "oud.xls").write_bytes(b"made up")
        r = scan(folder, inbox, settings=settings)
        names = ["kapot.xlsx", "oud.xls", "prijslijst.csv", "prijslijst.xlsx", "titles.csv"]
        if name == "set":
            assert r.stored_exports == [("export.csv", ["250101"], ["250101"])]
            assert r.skipped == [(file, "unsupported file type: .xls" if file == "oud.xls" else NOT_AN_EXPORT) for file in names]
            assert NOT_AN_EXPORT == "spreadsheet without the export's column titles"
            assert [p.name for p in (folder / "store").iterdir()] == ["250101"]          # the export's look and nothing else
        else:
            assert r.stored_exports == [] and not (folder / "store").exists()
            assert r.skipped == [(file, f"unsupported file type: {Path(file).suffix}") for file in ["export.csv", *names]]
        assert sorted(p.name for p in (inbox / "processed").iterdir()) == ["export.csv", *names] and list(inbox.glob("*.*")) == []
        assert r.new_cases == [] and r.stored_pos == [] and review_items(folder) == []


def test_a_refused_export_stays_as_an_error(tmp_path, madeup_out):
    """An export that the store refuses stays in the folder as an error, as a refused PO PDF does: here the Dutch export with
    export_fill_down off, whose second line has no PO number. The reason is the store's, which names the file and the row and no
    value; nothing of the export is filed, and the rest of the scan goes on. An e-mail that carries it stays as well. With the
    setting right it is filed at the next scan, and the same rows on the e-mail file nothing more."""
    inbox = inbox_of(tmp_path)
    shutil.copy(EXPORT_NL, inbox / EXPORT_NL.name)
    put(inbox, madeup_out / "MU01_po_v1.pdf")
    write_eml(inbox / "m.eml", subject="Export uit Exact", attachments=[EXPORT_NL])
    dutch = SETTINGS.model_copy(update={"export_columns": TRYOUT_EXPORT_COLUMNS, "export_decimal_comma": True})
    r = scan(tmp_path, inbox, settings=dutch)
    refused = "error: EXPORT_nl.csv, row 5: an item code without a PO number"
    assert r.skipped == [("EXPORT_nl.csv", refused), ("m.eml/EXPORT_nl.csv", refused)]
    assert r.stored_exports == [] and r.stored_pos == ["250101"] and r.new_cases == []
    assert (inbox / "EXPORT_nl.csv").exists() and (inbox / "m.eml").exists() and [p.name for p in (inbox / "processed").iterdir()] == ["MU01_po_v1.pdf"]
    assert not (tmp_path / "store" / "250301").exists() and not (tmp_path / "store" / "250302").exists()
    r = scan(tmp_path, inbox, settings=dutch.model_copy(update={"export_fill_down": True}), now=NOW + timedelta(hours=1))
    assert r.stored_exports == [("EXPORT_nl.csv", ["250301", "250302"], ["250301", "250302"]),
                                ("m.eml/EXPORT_nl.csv", ["250301", "250302"], [])]
    assert r.skipped == [] and sorted(p.name for p in (inbox / "processed").iterdir()) == ["EXPORT_nl.csv", "MU01_po_v1.pdf", "m.eml"]
    store = PoStore(tmp_path / "store")
    assert [look.supplier for number in ("250301", "250302") for look in store.snapshots(number)] == ["S21", "S22"]
    first = store.snapshots("250301")[0].document.lines[0]
    assert (first.quantity.text, first.quantity.value) == ("1.000", "1000")      # the scan passes the decimal comma (ruling E6)
    # A title of the map that stands twice in the title row, all the others there too: is_export takes the file and the store refuses
    # it, so it stays as an error, as the refusals of the rows do (ruling E8).
    text = EXPORT_NL.read_text(encoding="utf-8")
    (inbox / "dubbel.csv").write_text(text.replace(";Bedrag", ";Prijs", 1), encoding="utf-8")
    r = scan(tmp_path, inbox, settings=dutch.model_copy(update={"export_fill_down": True}), now=NOW + timedelta(hours=2))
    assert r.skipped == [("dubbel.csv", "error: dubbel.csv, row 3: the column 'Prijs' stands twice")] and (inbox / "dubbel.csv").exists()


def cart_export(price: str) -> str:
    """The ERP export of CART01's PO 250201 (export_csv of the generator), the price of its first line given as `price`."""
    return export_csv(SPECS["CART01"]).replace(",0.95,", f",{price},", 1)


def first_prices(store: PoStore) -> list[str]:
    """The price of the first line of each look at PO 250201, in the order of filing."""
    return [look.document.lines[0].unit_price.value for look in store.snapshots("250201")]


def test_a_kept_e_mail_does_not_file_its_export_again(tmp_path):
    """Ruling E1, the reviewers' kept e-mail: an e-mail stays in the folder while one of its attachments raises, here a PDF that only
    the decider can class while the decider cannot be reached, and every pass handles it again, its export included. Its export is
    filed once: after a newer export of the same PO, the kept e-mail's older export files nothing at the passes that follow, and the
    newer one stays the PO as it stands."""
    inbox = inbox_of(tmp_path)
    (tmp_path / "Inkooporderregels.csv").write_text(cart_export("0.95"), encoding="utf-8")
    note = drawn(tmp_path / "notitie.pdf", page_of("Hallo, zie de bijlage."))                    # no keyword: only the decider
    kept = write_eml(inbox / "20261116T075500_aaaa.eml", subject="Export", attachments=[tmp_path / "Inkooporderregels.csv", note])
    label = f"{kept.name}/Inkooporderregels.csv"
    store = PoStore(tmp_path / "store")
    filed = {1: [(label, ["250201"], ["250201"])], 2: [(label, ["250201"], []), ("Inkooporderregels.csv", ["250201"], ["250201"])],
             3: [(label, ["250201"], [])], 4: [(label, ["250201"], [])]}
    for number in range(1, 5):
        if number == 2:
            (inbox / "Inkooporderregels.csv").write_text(cart_export("0.99"), encoding="utf-8")      # a newer export, dropped
        r = scan_folder(inbox, store, tmp_path / "cases", EXPORTS, NOW + timedelta(minutes=number), UnreachableDecider())
        assert r.stored_exports == filed[number] and kept.exists(), number
        assert first_prices(store) == (["0.95"] if number == 1 else ["0.95", "0.99"]), number
        assert store.as_it_stands("250201").document.lines[0].unit_price.value == ("0.95" if number == 1 else "0.99"), number


def test_a_locked_export_file_is_not_filed_again(tmp_path, monkeypatch):
    """Ruling E1, the reviewers' locked file: two exports that cannot be moved to processed/ (on Windows a file still open in Excel;
    here a rename that raises PermissionError), the newer one saved under another name because the first is still open. Every pass
    handles both again: each is filed once, and after each pass the newer one is the PO as it stands."""
    locked = {"Inkooporderregels.csv", "Inkooporderregels 2.csv"}
    rename = Path.rename

    def refused(self, target):
        if self.name in locked:
            raise PermissionError(13, "the file is open in another program")
        return rename(self, target)
    monkeypatch.setattr(Path, "rename", refused)
    inbox = inbox_of(tmp_path)
    store = PoStore(tmp_path / "store")
    (inbox / "Inkooporderregels.csv").write_text(cart_export("0.95"), encoding="utf-8")
    for number in range(1, 5):
        if number == 2:
            (inbox / "Inkooporderregels 2.csv").write_text(cart_export("0.99"), encoding="utf-8")
        r = scan_folder(inbox, store, tmp_path / "cases", EXPORTS, NOW + timedelta(minutes=number))
        assert sum(len(new) for _, _, new in r.stored_exports) == (1 if number <= 2 else 0), number
        assert sorted(name for name, reason in r.skipped if reason.startswith("error: ")) == sorted(locked & {p.name for p in inbox.iterdir()})
        assert first_prices(store) == (["0.95"] if number == 1 else ["0.95", "0.99"]), number
        assert store.as_it_stands("250201").document.lines[0].unit_price.value == ("0.95" if number == 1 else "0.99"), number
    assert sorted(p.name for p in inbox.iterdir() if p.is_file()) == sorted(locked)


def test_the_exports_of_a_pass_are_filed_in_the_order_of_their_times(tmp_path):
    """Ruling E3: the exports of one pass are filed in the order of the modification times of their files in the folder, for an
    attachment that of its e-mail, the name breaking a tie only. So the newest export stands also when its name sorts first, as
    'Inkooporderregels (1).csv', the usual name of a second copy, sorts before 'Inkooporderregels.csv', and an export put into the
    folder under a name that begins with a letter sorts after every e-mail, whose name begins with its date."""
    ten = datetime(2026, 11, 16, 10, 0).timestamp()

    def saved(path: Path, text: str, minute: int) -> Path:
        path.write_text(text, encoding="utf-8")
        os.utime(path, (ten + 60 * minute, ten + 60 * minute))
        return path

    for number, (older, newer) in enumerate([("Inkooporderregels.csv", "Inkooporderregels (1).csv"),
                                              ("Inkooporderregels (1).csv", "Inkooporderregels.csv"), ("b.csv", "a.csv")]):
        folder = tmp_path / f"pass{number}"
        folder.mkdir()
        inbox = inbox_of(folder)
        saved(inbox / older, cart_export("0.95"), 0)
        saved(inbox / newer, cart_export("0.99"), 5)
        store = PoStore(folder / "store")
        r = scan_folder(inbox, store, folder / "cases", EXPORTS, NOW)
        assert [label for label, _, _ in r.stored_exports] == [older, newer] and first_prices(store) == ["0.95", "0.99"], (older, newer)
        assert store.as_it_stands("250201").document.lines[0].unit_price.value == "0.99"
    folder = tmp_path / "mail"
    folder.mkdir()
    inbox = inbox_of(folder)
    saved(inbox / "Orderregels.csv", cart_export("0.95"), 0)                                    # a loose export of 10:00
    attached = saved(folder / "Inkooporderregels.csv", cart_export("0.99"), 5)
    mail = write_eml(inbox / "20261116T100500_bbbb.eml", subject="Export", attachments=[attached])
    os.utime(mail, (ten + 300, ten + 300))                                                      # the e-mail of 10:05
    store = PoStore(folder / "store")
    r = scan_folder(inbox, store, folder / "cases", EXPORTS, NOW)
    assert [label for label, _, _ in r.stored_exports] == ["Orderregels.csv", f"{mail.name}/Inkooporderregels.csv"]
    assert store.as_it_stands("250201").document.lines[0].unit_price.value == "0.99"
    folder = tmp_path / "tie"                                                                   # the same time: the name decides
    folder.mkdir()
    inbox = inbox_of(folder)
    saved(inbox / "a.csv", cart_export("0.99"), 0)
    saved(inbox / "b.csv", cart_export("0.95"), 0)
    store = PoStore(folder / "store")
    r = scan_folder(inbox, store, folder / "cases", EXPORTS, NOW)
    assert [label for label, _, _ in r.stored_exports] == ["a.csv", "b.csv"] and first_prices(store) == ["0.99", "0.95"]


# --- the PO numbers the store holds (Task 36) ---

def worded(path: Path, case_id: str = "MU01", reference: str = "Uw referentie 250101", title: str | None = None) -> Path:
    """The made-up confirmation of `case_id` with the text of its PO reference row replaced: the number under a word that the default
    pattern does not know, for one."""
    pages = confirmation_pages(SPECS[case_id])
    next(row for row in pages[0] if row.ref == "po_reference").texts[0].text = reference
    path.write_bytes(pdf_bytes(pages, title or path.name))
    return path


def unreadable_lines(path: Path, *lines: str) -> Path:
    """A confirmation whose lines no layout template reads, so that the quick read by lines finds no PO: a title and free text."""
    return drawn(path, page_of("S01", "Orderbevestiging", *lines))


def test_of_several_numbers_the_one_the_store_holds_is_the_po(tmp_path, madeup_out):
    """Rule 2: the pattern finds two numbers, one in the e-mail and one on the page; the one the store holds is the PO, and no review
    item is written. A PO the store knows from an export only is held as well."""
    inbox = inbox_of(tmp_path)
    store = PoStore(tmp_path / "store")
    store.add(madeup_out / "MU01_po_v1.pdf")
    write_eml(inbox / "m.eml", subject="Bevestiging PO 250199", attachments=[madeup_out / "MU01_conf.pdf"])      # the page names 250101
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000"] and r.skipped == [] and review_items(tmp_path) == []
    (tmp_path / "export.csv").write_text(export_csv(SPECS["MU02"]), encoding="utf-8")
    store.from_export(tmp_path / "export.csv", EXPORT_COLUMNS)                                       # PO 250102, a look and no PDF
    write_eml(inbox / "n.eml", subject="Uw PO 250102", body="Onze order 250188", attachments=[madeup_out / "MU02_conf.pdf"])
    write_eml(inbox / "o.eml", subject="Bevestiging", body="PO 250188 en PO 250102",
              attachments=[drawn_confirmation(tmp_path / "c.pdf", "MU02", drop=("po_reference",))])
    r = scan(tmp_path, inbox, now=NOW + timedelta(hours=1))
    assert r.new_cases == ["250102_20261116T090000", "250102_20261116T090001"] and r.skipped == [] and review_items(tmp_path) == []


def test_several_numbers_none_or_two_of_them_held_are_skipped_as_before(tmp_path, madeup_out):
    """Rule 2 and ruling N5: when the store holds none of the numbers, or more than one with an item code of its PO on the first page,
    the document is skipped with its review item, as before. (A held number without an item code of its PO on the page does not count:
    test_of_several_pattern_numbers_a_held_one_counts_only_with_an_item_code_on_the_page.)"""
    both = unreadable_lines(tmp_path / "both.pdf", "Wij leveren 10 stuks KM-1001 en 2 stuks KM-0201.")
    for name, held, page in (("none", (), madeup_out / "MU01_conf.pdf"), ("both", ("MU01_po_v1.pdf", "MU02_po_v1.pdf"), both)):
        folder = tmp_path / name
        folder.mkdir()
        inbox = inbox_of(folder)
        for pdf in held:
            PoStore(folder / "store").add(madeup_out / pdf)
        write_eml(inbox / "m.eml", subject="Bevestiging PO 250101 en PO 250102", attachments=[page])
        r = scan(folder, inbox)
        assert r.new_cases == [] and r.skipped == [(f"m.eml/{page.name}", "more than one PO number")] and case_names(folder) == [], name
        assert [item["reason"] for item in review_items(folder)] == ["more than one PO number"], name


def test_a_number_under_another_word_is_found_when_the_store_holds_it_and_an_item_code_agrees(tmp_path, madeup_out):
    """Rule 3: the pattern finds no number, as "Uw referentie" is no word of it. A number the store holds that stands in the e-mail's
    text or on the first page as a word of its own gives the case its PO, when one of that PO's item codes stands on the first page
    as a word of its own. The quick read by lines cannot read these pages, so the number alone decides; a number with letters, as an
    ERP may give one, is held as well."""
    inbox = inbox_of(tmp_path)
    store = PoStore(tmp_path / "store")
    store.add(madeup_out / "MU01_po_v1.pdf")
    store.add(ANDERS_PO, ANDERS_LAYOUT)                                                             # PO IB-2600417, item KM-2301 among them
    on_page = unreadable_lines(tmp_path / "a.pdf", "Uw referentie: 250101", "Wij leveren 10 stuks KM-1001 op 16-11-2026.")
    assert find_po_numbers(first_page(on_page), SETTINGS.po_number_pattern) == []
    assert held_numbers(first_page(on_page), first_page(on_page), store) == ["250101"]
    put(inbox, on_page)
    write_eml(inbox / "m.eml", subject="Bevestiging", body="Uw referentie 250101", attachments=[
        unreadable_lines(tmp_path / "b.pdf", "Wij leveren 4 stuks KM-1002.")])                       # the number in the e-mail, the code on the page
    put(inbox, unreadable_lines(tmp_path / "c.pdf", "Uw ordernummer: IB-2600417", "10 st KM-2301 Kogelkraan DN25 messing"))
    r = scan(tmp_path, inbox)
    assert r.new_cases == ["250101_20261116T080000", "IB-2600417_20261116T080000", "250101_20261116T080001"] and r.skipped == []
    assert [load_case(tmp_path / "cases", case).input_files[0].name for case in r.new_cases] == ["a.pdf", "c.pdf", "b.pdf"]
    # the made-up confirmation itself with the number under another word: the same PO as with "Uw inkooporder"
    put(inbox, worded(tmp_path / "d.pdf"))
    assert scan(tmp_path, inbox, now=NOW + timedelta(hours=1)).new_cases == ["250101_20261116T090000"]


def test_a_held_number_without_an_item_code_of_that_po_decides_nothing(tmp_path, madeup_out):
    """Rule 3: a held number counts only with an item code of its own PO on the first page, as a word of its own; a code in the e-mail
    does not count, nor a code that is part of a longer word. A number that is part of a longer word, and a held number of fewer than
    four characters, count for nothing. Two held numbers decide nothing either. Each document then goes to the quick read by lines,
    which cannot read these pages: the case is opened without a PO number, as before."""
    store = PoStore(tmp_path / "store")
    store.add(madeup_out / "MU01_po_v1.pdf")
    store.add(madeup_out / "MU02_po_v1.pdf")
    (tmp_path / "short.csv").write_text(export_csv(SPECS["MU01"]).replace("250101", "123"), encoding="utf-8")
    store.from_export(tmp_path / "short.csv", EXPORT_COLUMNS)                                         # PO 123, with MU01's item codes
    pages = {"other code": ("Uw referentie: 250101", "Wij leveren 10 stuks KM-9999."),
             "code in a word": ("Uw referentie: 250101", "Wij leveren 10 stuks KM-10011."),
             "number in a word": ("Uw referentie: 2501019", "Wij leveren 10 stuks KM-1001."),
             "short number": ("Uw referentie: 123", "Wij leveren 10 stuks KM-1001."),
             "two numbers": ("Uw referenties: 250101 en 250102", "Wij leveren KM-1001 en KM-0201.")}
    inbox = inbox_of(tmp_path)
    for n, (what, lines) in enumerate(pages.items()):
        pdf = unreadable_lines(tmp_path / f"{n}.pdf", *lines)
        assert held_numbers(first_page(pdf), first_page(pdf), store) == (["250101", "250102"] if what == "two numbers" else []), what
        put(inbox, pdf)
    write_eml(inbox / "m.eml", subject="Uw referentie 250101", body="Artikel KM-1001",
              attachments=[unreadable_lines(tmp_path / "e.pdf", "Wij leveren 10 stuks.")])
    assert held_numbers("Uw referentie 250101\nArtikel KM-1001\nS01", "S01\nWij leveren 10 stuks.", store) == []
    r = scan(tmp_path, inbox)
    assert r.new_cases == [f"unknown_20261116T08000{n}" for n in range(6)] and r.skipped == [] and review_items(tmp_path) == []
    assert all(load_case(tmp_path / "cases", case).po_number is None for case in r.new_cases)


def test_a_held_number_counts_only_as_a_token_of_its_own(tmp_path, madeup_out):
    """Rulings N4 and N16: a held number counts only where it stands apart, not joined to letters or digits by "-", ".", "/" or "," on
    either side. So the year of a date ("21-10-2026"), the digits of an article code ("KM-1001") and those of an amount ("1250,00")
    never count, while a number before a full stop or a comma that a space or the end follows ("Uw referentie 250101.", "250101,
    250102") does. The colon is no joiner: a number that a label and a colon stand before counts ("Ref:250101", "Uw
    referentie:250101"), as a time never gives four digits on one side of its colon. The reviewers' year_word.py: MU01's confirmation
    that names its new order 2040, not filed yet, and is dated
    21-10-2026 holds no held number when the store holds PO 2026 with MU01's item codes (the quick read by lines still finds that PO by
    its item codes, as before Task 36); a page the quick read cannot read is opened without a PO number."""
    store = PoStore(tmp_path / "store")
    store.add(madeup_out / "MU01_po_v1.pdf")
    store.add(madeup_out / "MU02_po_v1.pdf")
    for number in ("2026", "1001", "1250"):                                     # POs of four digits with MU01's item codes
        (tmp_path / f"{number}.csv").write_text(export_csv(SPECS["MU01"]).replace("250101", number), encoding="utf-8")
        store.from_export(tmp_path / f"{number}.csv", EXPORT_COLUMNS)
    for text, numbers in (("Datum: 21-10-2026", []), ("Leverdatum 2026-10-21", []), ("Artikel KM-1001", []), ("Totaal 1250,00", []),
                          ("Bedrag 1250.00", []), ("Periode 2026/27", []), ("Ref:250101", ["250101"]), ("Uw ref.:2026", ["2026"]),
                          ("Uw referentie:250101", ["250101"]), ("Levering om 12:30, 2026-11-23", []),
                          ("Uw referentie 250101.", ["250101"]), ("Uw referenties 250101, 250102", ["250101", "250102"]),
                          ("Uw ref.: 2026", ["2026"]), ("Uw referentie 1250, levering in week 47", ["1250"]), ("(2026)", ["2026"]),
                          ("Uw referentie: 250101.\nMet vriendelijke groet", ["250101"]), ("Uw referentie 250101", ["250101"])):
        page = f"{text}\nWij leveren 10 stuks KM-1001 en KM-0201."                # KM-1001 holds the held number 1001 too
        assert held_numbers(page, page, store) == numbers, text
    year = worded(tmp_path / "year.pdf", reference="Uw referentie 2040")
    assert "Datum: 21-10-2026" in first_page(year) and held_numbers(first_page(year), first_page(year), store) == []
    inbox = inbox_of(tmp_path)
    put(inbox, unreadable_lines(tmp_path / "a.pdf", "Uw referentie 2040", "Datum: 21-10-2026", "Wij leveren 10 stuks KM-1002."))
    put(inbox, unreadable_lines(tmp_path / "b.pdf", "Totaal 1250,00", "Wij leveren 10 stuks KM-1002."))
    assert scan(tmp_path, inbox).new_cases == ["unknown_20261116T080000", "unknown_20261116T080001"]


def test_of_several_pattern_numbers_a_held_one_counts_only_with_an_item_code_on_the_page(tmp_path, madeup_out):
    """Ruling N5: a held number among several numbers the pattern finds counts only when one of the item codes of its PO as it stands
    stands on the first page as a word of its own, as held_numbers requires; exactly one such number gives the case its PO, otherwise
    the document is skipped with its review item, as before Task 36. "Uw inkooporder 250121 (vervangt 250101)" on MU02's page, with
    250101 held and none of its item codes on the page, is skipped; on MU01's page, with the item codes of 250101, it is a case of
    250101. (The default pattern finds 250101 only after one of its words, so the default's page says "vervangt inkooporder 250101";
    a pattern of the fields that knows "vervangt" finds it in the ruling's own words.)"""
    store = PoStore(tmp_path / "store")
    store.add(madeup_out / "MU01_po_v1.pdf")
    replacing = SETTINGS.model_copy(update={"po_number_pattern": r"(?i:Uw\s+inkooporder|vervangt)[\s:#.-]*(\d{6,8})(?!\d)"})
    for n, (settings, reference) in enumerate(((SETTINGS, "Uw inkooporder 250121 (vervangt inkooporder 250101)"),
                                               (replacing, "Uw inkooporder 250121 (vervangt 250101)"))):
        folder = tmp_path / f"pattern{n}"
        shutil.copytree(store.root, folder / "store")
        inbox = inbox_of(folder)
        assert find_po_numbers(reference, settings.po_number_pattern) == ["250121", "250101"]
        put(inbox, worded(tmp_path / "other.pdf", "MU02", reference))                    # MU02's item codes: none of 250101
        r = scan(folder, inbox, settings=settings)
        assert r.new_cases == [] and r.skipped == [("other.pdf", "more than one PO number")], reference
        assert [item["reason"] for item in review_items(folder)] == ["more than one PO number"], reference
        put(inbox, worded(tmp_path / "same.pdf", "MU01", reference))                     # MU01's item codes, those of 250101
        assert scan(folder, inbox, now=NOW + timedelta(hours=1), settings=settings).new_cases == ["250101_20261116T090000"], reference
    both = tmp_path / "both"                                                             # two held numbers, each with its item codes
    shutil.copytree(store.root, both / "store")
    (tmp_path / "repeat.csv").write_text(export_csv(SPECS["MU01"]).replace("250101", "250121"), encoding="utf-8")
    PoStore(both / "store").from_export(tmp_path / "repeat.csv", EXPORT_COLUMNS)
    put(inbox_of(both), worded(tmp_path / "same.pdf", "MU01", "Uw inkooporder 250121 (vervangt inkooporder 250101)"))
    r = scan(both, both / "inbox")
    assert r.new_cases == [] and r.skipped == [("same.pdf", "more than one PO number")]


def test_a_number_that_a_digit_follows_is_never_cut_short(tmp_path, madeup_out):
    """Rulings N7 and N26: find_po_numbers passes over a number that a digit follows directly in the text, at the end of the pattern's
    group, whatever the pattern: the default, an own pattern and the pattern of the fields alike. So under the default and the fields'
    form, whose match starts after a word, a longer number is taken whole or not at all; an own pattern whose match can start inside a
    number may still take a number's tail. The reviewers' run: MU01's confirmation whose reference reads "Order 1234567890" opens no case
    of a PO 12345678; with PO 250101 in the store the quick read by lines links it to that PO, as it links a confirmation without a
    number."""
    default = SETTINGS.po_number_pattern
    assert default == r"(?i:PO|Inkooporder|Bestelling|Bestellung|Order)[\s:#-]*(\d{6,8})"         # the default stays as it is
    for text, numbers in (("Order 1234567890", []), ("Uw inkooporder 1234567890", []), ("PO 123456789", []), ("Order 12345678", ["12345678"]),
                          ("Order 12345678.", ["12345678"]), ("Order 1234567890 en PO 250101", ["250101"]), ("PO 250101-2", ["250101"]),
                          ("PO 250101/1", ["250101"]), ("Order 250101AB", ["250101"])):
        assert find_po_numbers(text, default) == numbers, text
    fields = r"(?i:Order)[\s:#.-]*(\d{6,8})(?!\d)"
    assert find_po_numbers("Order 1234567890", fields) == [] and find_po_numbers("Order. 250101", fields) == ["250101"]
    assert find_po_numbers("Order. 250101", default) == []                     # the one difference: the fields' form passes a full stop
    own = r"REF-(\d{6})"
    assert find_po_numbers("REF-2501019 en REF-250102", own) == ["250102"] and find_po_numbers("REF-250101-9", own) == ["250101"]
    assert find_po_numbers("nr 1234567 en 250101", r"\d{6}") == ["250101"]                            # no group: the whole match
    assert find_po_numbers("Factuur 1234567", r"(\d{6})(?!\d)") == ["234567"]          # a match that starts inside a number: its tail
    inbox = inbox_of(tmp_path)
    PoStore(tmp_path / "store").add(madeup_out / "MU01_po_v1.pdf")
    put(inbox, worded(tmp_path / "cut.pdf", reference="Order 1234567890"))
    assert scan(tmp_path, inbox).new_cases == ["250101_20261116T080000"]


# What the scan gave each made-up confirmation before Task 36 (the code of ee7b12b): with its PO reference the PO the reference names,
# whatever the store holds, and the scans none; without it (the row of the reference taken out and the page drawn again, the scans
# with a text layer then) the PO the quick read by lines finds in a store of every made-up PO, which is none for MU19, whose merged
# line leaves one item code of its PO, and none in an empty store. A decider classes the two scans as confirmations.
GOT = {case_id: f"2501{case_id[2:]}" for case_id in (f"MU{n:02d}" for n in range(1, 21))}
GOT_WITHOUT_REFERENCE = GOT | {"MU19": None}


def test_every_made_up_confirmation_gets_the_po_it_got(tmp_path, madeup_out):
    """Rule 4: for every made-up confirmation the scan gives the PO it gave before, with its PO reference and without it, with the
    store empty and with every made-up PO in it."""
    specs = [spec for spec in SPECS.values() if spec.confirmation is not None]
    assert len(specs) == 20
    full = PoStore(tmp_path / "full")
    for pdf in sorted(madeup_out.glob("*_po_v*.pdf")):
        full.add(pdf)
    for reference in ("with", "without"):
        for stored in ("empty", "full"):
            folder = tmp_path / f"{reference}_{stored}"
            folder.mkdir()
            inbox = inbox_of(folder)
            for spec in specs:
                name = f"{spec.case_id}_conf.pdf"
                if reference == "with":
                    put(inbox, madeup_out / name)
                else:
                    drawn_confirmation(inbox / name, spec.case_id, drop=("po_reference",))
            if stored == "full":
                shutil.copytree(full.root, folder / "store")
            r = scan(folder, inbox, decider=FakeDecider("confirmation", 0.9))
            got = {load_case(folder / "cases", case).input_files[0].name[:4]: load_case(folder / "cases", case).po_number for case in r.new_cases}
            if reference == "with":
                expected = GOT | {"MU05": None, "MU16": None}
            else:
                expected = GOT_WITHOUT_REFERENCE if stored == "full" else dict.fromkeys(GOT)
            assert got == expected and r.skipped == [], (reference, stored)
