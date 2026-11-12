"""Tests of the try-out confirmation (aid/tryout.py): a made-up order confirmation drawn from a purchase order that the PO store holds,
read back with the parser (aid/readers/supplier_parser.py, which reads it as table_nl) and run as the runner runs a case. Every purchase
order here is made up: those of the made-up set, its two exports, and the PO in the second made-up layout (aid/madeup/tryout)."""
import csv
import dataclasses
import io
import json
import os
import shutil
from datetime import datetime, timedelta
from decimal import Decimal
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path

import pdfplumber
import pytest
from reportlab.pdfbase.pdfmetrics import stringWidth

import aid.tryout
from aid.capture_server import precheck
from aid.case import load_case
from aid.comparison import compare
from aid.decider import NoDecider
from aid.execution.by_hand import changes, mark, open_changes
from aid.execution.ops import WriteOp
from aid.execution.writer import PreparedWriter, RetryingWriter, WriteLogEntry
from aid.matching import match_lines
from aid.messaging import FileMailer
from aid.pipeline import GUESSED_PO, CaseResult, Deps, read_with_parser, run_case
from aid.po_store import PoStore, PoVersion
from aid.readers.cart import first_heading, read_cart_html
from aid.readers.llm_client import ReaderError
from aid.readers.pdf_text import extract_lines, extract_rows
from aid.readers.supplier_parser import detect_template, load_templates, read_with_template
from aid.review import hand_list
from aid.notify import list_link
import aid.tryout_mark
from aid.review.app import close_case, decide, load_case_view, undo
from aid.review.i18n import STRINGS
from aid.review.page import _case_label, close_text, decision_text, offered_cases
from aid.runner import build_deps, run_loop
from aid.schema import DocValue
from aid.settings import Settings
from aid.sources.capture import BY_ITEMS, NOT_SURE, cart_order
from aid.sources.dropin import drop
from aid.sources.mailbox import FOUND_HELD_NUMBER, scan_folder
from aid.sources.upload import open_upload_case
from aid.tests.conftest import SETTINGS, deps
from aid.tests.helpers import ANDERS_LAYOUT, ANDERS_PO, EXPORT_COLUMNS, filed_on
from aid.tryout import (CART_CHANGES, CHANGES, REASONS, TryoutRefused, cart_html, cart_link, checked_cart, checked_confirmation,
                        confirmation_pdf, file_name, known_pos, made_from, write_cart)
from aid.tryout_mark import NOTHING_SENT, TRYOUT_NOT_SENT, TRYOUT_OUTSIDE, tryout_case, tryout_writer
from aid.versioning import select_version

NOW = datetime(2026, 11, 12, 9, 0)                   # the time of the clock of deps(): the day of a try-out and its moment of making
COMPARED = ("item_code", "quantity", "unit", "unit_price", "price_basis", "delivery_date")    # what a read-back gives of each line
OPEN = SETTINGS.model_copy(update={"authorization_limit": None, "test_mode": True})   # the settings under which try-outs are taken
FIRST_LINE = {"none": {}, "price_small": {"unit_price": "12.63"}, "price_large": {"unit_price": "13.75"},
              "date_later": {"delivery_date": "2026-11-19"}, "date_much_later": {"delivery_date": "2027-01-15"}}   # of a line at 12.50, 16-11-2026
SUPPLIER_ROW = "Proefleverancier (verzonnen document)"
NOTE_ROW = "Dit is een proefdocument van de inkoopassistent. Het komt niet van een leverancier."
TITLE_ROW = "Pos Artikel Omschrijving Aantal Eenheid Prijs Per Leverdatum"
FIELD = {"none": None, "price_small": "price", "price_large": "price", "date_later": "delivery_date", "date_much_later": "delivery_date"}
EXPORT_TITLES = "po_number,line_id,item_code,description,quantity,unit,unit_price,price_basis,delivery_date\n"   # an export's title row


def drawn(folder: Path, po: PoVersion, change: str, name: str = "tryout.pdf", moment: datetime = NOW) -> Path:
    """The try-out of the PO with the change, written into the folder under the name."""
    path = folder / name
    path.write_bytes(confirmation_pdf(po, change, moment))
    return path


def checked(tmp_path: Path, store: PoStore, po: PoVersion, change: str, moment: datetime = NOW) -> bytes:
    """The try-out as the page makes it: read back and checked (checked_confirmation), with the settings of a try-out on the store."""
    settings = OPEN.model_copy(update={"cases_root": tmp_path / "cases", "po_store_root": store.root, "watched_folder": tmp_path / "inbox"})
    return checked_confirmation(po, change, moment, settings, store)


def read_back(path: Path):
    """The try-out as the parser reads it: with the template that detect_template finds for it, which is table_nl."""
    template = detect_template(path, load_templates())
    assert template is not None and template.name == "table_nl"
    return read_with_template(path, template)


def lines_of(doc) -> list[dict]:
    """The values of each line that a read-back gives back, in normal form, with its line id."""
    return [{"line_id": line.line_id, **{field: getattr(line, field).value for field in COMPARED}} for line in doc.lines]


def with_line(po: PoVersion, index: int, **values: str | None) -> PoVersion:
    """The PO with the values of one of its lines replaced: a value as the store holds one (its text the value), None for none; a
    line_id as it is."""
    lines = [line.model_copy() for line in po.document.lines]
    lines[index] = lines[index].model_copy(update={field: value if field == "line_id" else
                                                   DocValue(value=value, text=value) if value is not None else DocValue()
                                                   for field, value in values.items()})
    return po.model_copy(update={"document": po.document.model_copy(update={"lines": lines})})


def with_header(po: PoVersion, **values: str | None) -> PoVersion:
    """The PO with header values replaced, as with_line does for a line."""
    update = {field: DocValue(value=value, text=value) if value is not None else DocValue() for field, value in values.items()}
    return po.model_copy(update={"document": po.document.model_copy(update=update)})


def on_store(store: PoStore, tmp_path: Path) -> Deps:
    """The dependencies of a run on the store: the settings of a try-out with the cases under tmp_path, the parser, no decider, the
    prepared writer and the clock of deps()."""
    settings = OPEN.model_copy(update={"cases_root": tmp_path / "cases"})
    return Deps(settings=settings, store=store, reader=read_with_parser, decider=NoDecider(), writer=PreparedWriter(tmp_path / "prepared"),
                mailer=None, clock=lambda: NOW)


def refused(po: PoVersion, change: str, call=None) -> str:
    """The reason a try-out of the PO with the change is refused for, by confirmation_pdf or by `call`."""
    with pytest.raises(TryoutRefused) as refusal:
        (call or (lambda: confirmation_pdf(po, change, NOW)))()
    return refusal.value.reason


def test_a_small_price_change_reads_back_and_is_taken_over_by_the_rules(tmp_path, madeup_out):
    """The made-up PO of MU08 with the price of its first line 1 percent up: 12.50 becomes 12.63 (12.625 rounded half up to the two
    decimals the PO prints), every other value is the PO's, and the pipeline with the parser takes the change over by R04, within the
    thresholds, as it takes over MU08's own confirmation. The first row and the row under the table say that it is made up."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    po = made_from(d.store, "250108")
    assert po == d.store.versions("250108")[-1] and file_name(po, datetime(2026, 11, 12, 9, 30, 5)) == (
        "PROEF orderbevestiging 250108 20261112-093005.pdf")
    path = drawn(tmp_path, po, "price_small", file_name(po, NOW))
    texts = [line.text for line in extract_lines(path)]
    assert texts[:6] == [SUPPLIER_ROW, "Orderbevestiging", "Uw inkooporder 250108, versie 1", "Datum: 12-11-2026", "Valuta: EUR",
                         "Leveringsvoorwaarden: DAP Rotterdam"]
    assert texts[6] == TITLE_ROW and texts[-1] == NOTE_ROW
    doc = read_back(path)
    expected = lines_of(po.document)
    expected[0] |= {"unit_price": "12.63"}
    assert lines_of(doc) == expected
    assert (doc.po_reference.value, doc.version_hint.value, doc.document_date.value, doc.currency.value, doc.incoterm.value) == (
        "250108", "1", "2026-11-12", "EUR", "DAP Rotterdam")
    assert doc.supplier.value is None                       # the made-up supplier's row names no supplier: the case takes the PO's
    r = run_case(open_upload_case(d.settings.cases_root, [path], "250108", "confirmation", NOW), d)
    assert r.status == "closed" and [(a.rule_id, a.route, a.reason) for a in r.actions] == [
        ("R04", "AUTO", "price within threshold"), ("R01", "AUTO", "attach and confirm")]
    assert [(e.op.kind, e.op.line_id, e.previous, e.new) for e in r.write_entries[:2]] == [
        ("po_line_price", "1", "12.50", "12.63"), ("article_price", None, None, "12.63")]
    assert (r.version.version, r.version.method, r.version.reason, r.supplier) == (1, "reference", "quotes version 1", "S08")


def test_each_change_reads_back_with_that_change_only(tmp_path, madeup_out):
    """Each change of MU08's PO reads back with that change on the first line and every other value the PO's. A week or a range moves
    as the range of its days; a line with a discount or a net price prints the price the comparison compares, so that the comparison
    finds the change and nothing else."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    po = made_from(d.store, "250108")
    assert set(FIRST_LINE) == set(CHANGES)
    for change, changed in FIRST_LINE.items():
        expected = lines_of(po.document)
        expected[0] |= changed
        assert lines_of(read_back(drawn(tmp_path, po, change, f"{change}.pdf"))) == expected, change
    week = with_line(with_line(po, 0, delivery_date="2026-W48"), 1, delivery_date="2026-11-23/2026-11-27")
    moved = {"none": "2026-W48", "date_later": "2026-11-26/2026-11-30", "date_much_later": "2027-01-22/2027-01-26"}
    for change, first in moved.items():
        read = lines_of(read_back(drawn(tmp_path, week, change, f"week_{change}.pdf")))
        assert [line["delivery_date"] for line in read] == [first, "2026-11-23/2026-11-27"], change
    # a discount on line 1 (10.00 less 5 percent) and a net price on line 2: the comparison of the read-back finds the change only
    priced = with_line(with_line(po, 0, unit_price="10.00", discount_pct="5"), 1, net_price="0.40")
    for change, flags in {"none": [], "price_small": [("price", ["1"], "9.50", "9.60")], "date_later": [("delivery_date", ["1"], "2026-11-16", "2026-11-19")]}.items():
        doc = read_back(drawn(tmp_path, priced, change, f"priced_{change}.pdf"))
        findings = compare(match_lines(doc, priced.document, NoDecider()), doc, priced.document, "confirmation")
        assert [(f.field, f.po_line_ids, f.po_value.value, f.doc_value.value) for f in findings.flags] == flags, change
        assert findings.uncompared == [], change
    # line ids: whole numbers stand in Pos as the store holds them; with one that is none, the lines are numbered 1, 2 and matched by
    # their item codes, so that the comparison finds the change only
    for ids, printed in ((["10", "20"], ["10", "20"]), (["A1", "20"], ["1", "2"])):
        lined = po.model_copy(update={"document": po.document.model_copy(update={"lines": [
            line.model_copy(update={"line_id": line_id}) for line, line_id in zip(po.document.lines, ids)]})})
        doc = read_back(drawn(tmp_path, lined, "price_small", f"ids_{ids[0]}.pdf"))
        findings = compare(match_lines(doc, lined.document, NoDecider()), doc, lined.document, "confirmation")
        assert [line.line_id for line in doc.lines] == printed and findings.uncompared == []
        assert [(f.field, f.po_line_ids, f.doc_line_ids) for f in findings.flags] == [("price", [ids[0]], [printed[0]])]


def test_no_change_gives_no_difference(tmp_path, madeup_out):
    """'none' on MU08's PO and on MU04's (32 lines, so two pages, the column titles repeated on the second): read back, the try-out is
    the PO, and the pipeline finds no difference and nothing it cannot compare: the case closes with R01 alone."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    for minute, po_number in enumerate(("250108", "250104")):
        po = made_from(d.store, po_number)
        path = drawn(tmp_path, po, "none", f"{po_number}.pdf")
        assert lines_of(read_back(path)) == lines_of(po.document)
        r = run_case(open_upload_case(d.settings.cases_root, [path], po_number, "confirmation", NOW + timedelta(minutes=minute)), d)
        assert r.status == "closed" and (r.findings.flags, r.findings.uncompared) == ([], []) and [a.rule_id for a in r.actions] == ["R01"]
    assert len({line.page for line in extract_lines(tmp_path / "250104.pdf")}) == 2


def test_every_made_up_po_is_given_with_every_change_and_none_asks_a_supplier(tmp_path, madeup_out):
    """Ruling Y1 on the whole made-up set: every change on every made-up PO (the 20 of the made-up confirmations and the two that the
    made-up exports give, known from an export only) passes the read-back (checked_confirmation), so is still given; run as the runner
    runs a case with the parser, no action asks a supplier (R06, R07 and R08 ask) and none asks the approver (R10), and the one flag is
    the change on the first line, so that nothing differs but the chosen change."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    for case_id in ("CART01", "CART02"):
        d.store.from_export(madeup_out / f"{case_id}_export.csv", EXPORT_COLUMNS)
    numbers = sorted(folder.name for folder in d.store.root.iterdir())
    assert len(numbers) == 22 and set(FIELD) == set(CHANGES)
    moment = NOW
    for po_number in numbers:
        po = made_from(d.store, po_number)
        for change in CHANGES:
            moment += timedelta(minutes=1)
            path = tmp_path / f"{po_number}_{change}.pdf"
            path.write_bytes(checked(tmp_path, d.store, po, change, moment))
            r = run_case(open_upload_case(d.settings.cases_root, [path], po_number, "confirmation", moment), d)
            assert r.error is None, (po_number, change, r.error)
            assert [a.rule_id for a in r.actions if a.route == "ASK" or a.rule_id == "R10"] == [], (po_number, change)
            assert [(f.field, f.po_line_ids) for f in r.findings.flags] == ([] if FIELD[change] is None else [(FIELD[change], ["1"])]), (
                po_number, change)
    assert not (tmp_path / "cases" / "tmp").exists()            # the read-back's folders are gone again


def test_a_text_the_font_cannot_print_is_refused_before_anything_is_drawn(tmp_path, madeup_out):
    """Ruling Y1, findings 1 and 2 (the reviewers' s1_font.py and s12_font_more.py): the built-in font of the PDF prints only the
    characters of its encoding, cp1252, and any other as another letter, which the routing would ask the supplier about. So a try-out
    with such a character in any text it prints, a header row included, is refused for every change with the reason "character",
    whose text names the row or the line and the column but not the value. That check comes first (ruling Y26): an item code that is
    too wide as well is refused for its character. A character of the encoding, such as Ø, µ or è, prints and reads back as it is."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    po = made_from(d.store, "250108")
    variants = [("the header row of the Incoterm", with_header(po, incoterm="FCA Poznań")),
                ("the header row of the Incoterm", with_header(po, incoterm="EXW Plzeň")), ("line 1, column Eenheid", with_line(po, 0, unit="μm")),
                ("line 2, column Artikel", with_line(po, 1, item_code="KM-08Ω1")), ("line 2, column Artikel", with_line(po, 1, item_code="KM-Δ802")),
                ("line 1, column Omschrijving", with_line(po, 0, description="Δp-meter 0-10 bar")),
                ("line 1, column Omschrijving", with_line(po, 0, description="Manometer\tglycerine")),
                ("line 2, column Artikel", with_line(po, 1, item_code="KM-0802-" + "Ł" * 25))]      # ruling Y26: too wide as well
    for where, variant in variants:
        for change in CHANGES:
            with pytest.raises(TryoutRefused) as refusal:
                confirmation_pdf(variant, change, NOW)
            assert refusal.value.reason == "character" and str(refusal.value).startswith(f"{where}: "), (where, change)
            assert not set("ńňμΩΔŁ\t") & set(str(refusal.value)) and "Pozna" not in str(refusal.value)
        assert refused(variant, "none", lambda: checked(tmp_path, d.store, variant, "none")) == "character"
    assert not (tmp_path / "cases").exists()                # nothing written, not even the read-back's folder
    printable = with_header(with_line(with_line(po, 0, unit="µm", description="Kogelkraan Ø 25 mm"), 1, item_code="KM-08é2"),
                            incoterm="DAP Liège")
    for change in CHANGES:
        doc = read_back(drawn(tmp_path, printable, change, f"printable_{change}.pdf"))
        assert ([line.unit.value for line in doc.lines], doc.lines[1].item_code.value, doc.incoterm.value) == (["µm", "pc"], "KM-08é2", "DAP Liège")
        assert checked(tmp_path, d.store, printable, change) == confirmation_pdf(printable, change, NOW)


def test_a_line_matched_by_its_description_keeps_it_whole(tmp_path, madeup_out):
    """Ruling Y1, finding 2 (the reviewers' s11_placeholder_code.py): whether a line is matched by its item code is decided as the
    matcher decides it, by a code with a letter or a digit (norm_code). A line whose item code is a placeholder such as "-" or "." is
    matched by its description, so its description is printed whole: one that fits gives a try-out with the change only, for every
    change; one too wide for its column is refused for every change with the reason "value", and nothing is drawn. Never is such a
    line read back as a missing and an extra line."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    po = made_from(d.store, "250108")
    moment = NOW
    for code in ("-", ".", None):
        short = with_line(po, 1, item_code=code, description="Vrachtkosten")
        for change in CHANGES:
            moment += timedelta(minutes=1)
            path = tmp_path / f"short_{code}_{change}.pdf"
            path.write_bytes(checked(tmp_path, d.store, short, change, moment))
            assert read_back(path).lines[1].description.value == "Vrachtkosten"
            doc = read_back(path)
            findings = compare(match_lines(doc, short.document, NoDecider()), doc, short.document, "confirmation")
            assert [(f.field, f.po_line_ids) for f in findings.flags] == ([] if FIELD[change] is None else [(FIELD[change], ["1"])])
            assert findings.uncompared == [], (code, change)
        for description in ("Transportkosten en verpakking volgens offerte", "Montage op locatie door monteur"):
            long = with_line(po, 1, item_code=code, description=description)
            for change in CHANGES:
                with pytest.raises(TryoutRefused) as refusal:
                    confirmation_pdf(long, change, NOW)
                assert refusal.value.reason == "value" and str(refusal.value) == "line 2: the column Omschrijving is too narrow for the value of the line"
    # a line with a placeholder and no description, and one with neither: nothing could match it
    for code in ("-", None):
        assert refused(with_line(po, 1, item_code=code, description=None), "none") == "value"


def test_a_try_out_the_scan_would_not_place_is_refused(tmp_path):
    """Ruling Y1, findings 6 and 12, with Task 36's held numbers: a try-out is given only when the scan, given the try-out, finds its
    purchase order: by the PO number pattern of the settings (six to eight digits, by default), by a held number (an order number of at
    least four characters that the store holds, standing on the first page with one of that order's item codes), or by its lines when
    exactly one stored order has two of its item codes. A one-line order whose number has fewer than four characters, and an order of
    such a number with a repeat order of the same lines in the store, are refused with the reason "not_found" for every change; a
    one-line order of six digits, a one-line order of another number of four characters or more, and a two-line order of another
    number are given, and the scan opens their case for them. The PO in the second made-up layout, whose number has letters, is given
    also with a repeat order of its lines in the store: its held number places it."""
    store = PoStore(tmp_path / "store")
    export = tmp_path / "export.csv"
    export.write_text(EXPORT_TITLES + "IB5,1,KM-5001,Kogelkraan DN25,10,st,12.50,1,2026-11-16\n"
                      "IB6,1,KM-6001,Slangklem,4,st,0.85,1,2026-11-16\n"
                      "IB6,2,KM-6002,Sluitring,100,st,0.05,1,2026-11-16\n"
                      "IB7,1,KM-6001,Slangklem,1,st,1.00,1,2026-12-01\n"                    # a repeat order of IB6's lines
                      "IB7,2,KM-6002,Sluitring,1,st,1.00,1,2026-12-01\n"
                      "IB-2600500,1,KM-5001,Kogelkraan DN25,10,st,12.50,1,2026-11-16\n"
                      "IB-2600600,1,KM-6101,Slangklem,4,st,0.85,1,2026-11-16\n"
                      "IB-2600600,2,KM-6102,Sluitring,100,st,0.05,1,2026-11-16\n"
                      "260500,1,KM-5001,Kogelkraan DN25,10,st,12.50,1,2026-11-16\n", encoding="utf-8")
    store.from_export(export, EXPORT_COLUMNS)
    settings = OPEN.model_copy(update={"cases_root": tmp_path / "cases", "po_store_root": store.root, "watched_folder": tmp_path / "inbox"})
    for po_number in ("IB5", "IB6"):
        for change in CHANGES:
            assert refused(None, change, lambda: checked(tmp_path, store, made_from(store, po_number), change)) == "not_found", (po_number, change)
    moment = NOW
    for po_number in ("IB-2600500", "IB-2600600", "260500"):
        po = made_from(store, po_number)
        for change in CHANGES:
            moment += timedelta(seconds=1)
            drop_in = tmp_path / "made" / file_name(po, moment)
            drop_in.parent.mkdir(exist_ok=True)
            drop_in.write_bytes(checked(tmp_path, store, po, change, moment))
            drop(drop_in, drop_in.name, settings.watched_folder)
    scanned = scan_folder(settings.watched_folder, store, settings.cases_root, settings, NOW)
    assert sorted(load_case(settings.cases_root, case_id).po_number for case_id in scanned.new_cases) == (
        ["260500"] * 5 + ["IB-2600500"] * 5 + ["IB-2600600"] * 5)
    # a repeat order of the PO in the second made-up layout: an export of another number with the same lines
    po = store.add(ANDERS_PO, ANDERS_LAYOUT)
    assert checked(tmp_path, store, po, "price_small")
    lines = read_back(drawn(tmp_path, po, "none")).lines
    repeat = tmp_path / "repeat.csv"
    repeat.write_text(EXPORT_TITLES + "".join(f"IB-2600418,{n},{line.item_code.value},Herhaling,1,st,1.00,1,2026-12-01\n" for n, line in enumerate(lines, 1)),
                      encoding="utf-8")
    store.from_export(repeat, EXPORT_COLUMNS)
    for change in CHANGES:
        assert checked(tmp_path, store, po, change), change
    assert not (tmp_path / "cases" / "tmp").exists()


def test_a_try_out_that_would_not_read_back_as_the_order_is_refused(tmp_path, madeup_out, monkeypatch):
    """Ruling Y1: a try-out whose read-back gives any difference other than the chosen change, and one the parser cannot read, is
    refused with the reason "read_back", and nothing stays behind of the read-back."""
    import aid.tryout

    d = deps(tmp_path, madeup_out, settings=OPEN)
    po = made_from(d.store, "250108")

    def misreading(path: Path, kind: str):
        doc = read_with_parser(path, kind)
        line = doc.lines[1].model_copy(update={"unit": DocValue(value="m", text="m")})
        return doc.model_copy(update={"lines": [doc.lines[0], line]})

    def unreadable(path: Path, kind: str):
        raise ReaderError(f"no template for {path.name}")

    for reader in (misreading, unreadable):
        monkeypatch.setattr(aid.tryout, "read_with_parser", reader)
        for change in CHANGES:
            assert refused(po, change, lambda: checked(tmp_path, d.store, po, change)) == "read_back", (reader.__name__, change)
    monkeypatch.undo()
    assert checked(tmp_path, d.store, po, "price_small")
    assert not (tmp_path / "cases" / "tmp").exists()


def test_an_item_code_of_up_to_about_100_points_prints_whole(tmp_path, madeup_out):
    """Rulings Y2 and Y20: the item code column is as wide as the widest item code of the PO needs, at the cost of the description, so
    that an item code of up to about 100 points at the document's font size prints whole and reads back, for every change, through the
    read-back of checked_confirmation: codes of 15 characters, twenty digits and KM-08-00417-ABC-2026; the column titles stand where the
    widths put them, which the template reads. A wider item code, such as twenty capitals or a code of capitals with dashes over 100
    points, would leave the description less room than its title and is refused with the reason "item_code"."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    po = made_from(d.store, "250108")
    moment = NOW
    for first, second in (("ART-2026-000417", "ART-2026-000418"), ("12345678901234567890", "KM-08-00417-ABC-2026")):
        coded = with_line(with_line(po, 0, item_code=first), 1, item_code=second)
        for change in CHANGES:
            moment += timedelta(minutes=1)
            path = tmp_path / f"{first}_{change}.pdf"
            path.write_bytes(checked(tmp_path, d.store, coded, change, moment))
            doc = read_back(path)
            assert [line.item_code.value for line in doc.lines] == [first, second], change
            expected = lines_of(coded.document)
            expected[0] |= FIRST_LINE[change]
            assert lines_of(doc) == expected, change
        [titles] = [row for row in extract_rows(path) if row.line.text == TITLE_ROW]
        widest = max(stringWidth(code, "Helvetica", 9) for code in (first, second))
        assert titles.words[2].x0 - titles.words[1].x0 == pytest.approx(widest + 6, abs=0.01)     # 78 points whatever the codes before Y2
    for code in ("ABCDEFGHIJKLMNOPQRST", "BOSCH-0-986-494-WZ12", "W" * 15):      # 20 capitals; 20 characters of 103 points; 15 wide ones
        assert stringWidth(code, "Helvetica", 9) > 100.5
        for change in CHANGES:
            with pytest.raises(TryoutRefused) as refusal:
                confirmation_pdf(with_line(po, 1, item_code=code), change, NOW)
            assert refusal.value.reason == "item_code" and str(refusal.value) == "line 2: the item code is too wide for its column"


def test_every_try_out_is_a_document_of_its_own(tmp_path, madeup_out):
    """Ruling Y3, findings 3 and 7 (the reviewers' s2_twice.py and the walk of 3.17 step 2): the moment of making, to the second, is
    the PDF's title, which the parser does not read. Two try-outs of the same order and change differ in their bytes and in nothing
    the parser reads; given directly and five minutes later mailed as an attachment, each opens a case of its own, and neither is
    skipped as a duplicate."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = OPEN.model_copy(update={"cases_root": tmp_path / "cases", "watched_folder": tmp_path / "inbox"})
    po = made_from(d.store, "250108")
    later = NOW + timedelta(minutes=5)
    first, second = checked(tmp_path, d.store, po, "price_small", NOW), checked(tmp_path, d.store, po, "price_small", later)
    assert first != second
    paths = [tmp_path / "first.pdf", tmp_path / "second.pdf"]
    for path, pdf in zip(paths, (first, second)):
        path.write_bytes(pdf)
    assert [line.text for line in extract_lines(paths[0])] == [line.text for line in extract_lines(paths[1])]
    titles = []
    for path in paths:
        with pdfplumber.open(path) as pdf:
            titles.append(pdf.metadata["Title"])
    assert titles == ["made-up try-out order confirmation 2026-11-12 09:00:00", "made-up try-out order confirmation 2026-11-12 09:05:00"]
    assert confirmation_pdf(po, "price_small", NOW) == first      # the same moment gives the same bytes: the drawing is invariant
    drop(paths[0], file_name(po, NOW), settings.watched_folder)
    [case_id] = scan_folder(settings.watched_folder, d.store, settings.cases_root, settings, NOW).new_cases
    mail = EmailMessage()
    mail["From"], mail["To"], mail["Subject"] = "buyer@example.com", "aid@example.com", "Proefbevestiging"
    mail.set_content("Zie bijlage.")
    mail.add_attachment(second, maintype="application", subtype="pdf", filename=file_name(po, later))
    (settings.watched_folder / "20261112T090500_proef.eml").write_bytes(mail.as_bytes())
    scanned = scan_folder(settings.watched_folder, d.store, settings.cases_root, settings, later)
    assert len(scanned.new_cases) == 1 and scanned.skipped == [] and scanned.new_cases[0] != case_id
    assert [load_case(settings.cases_root, case).po_number for case in (case_id, *scanned.new_cases)] == ["250108", "250108"]


def test_a_new_price_is_rounded_to_the_decimals_the_order_prints_and_at_least_two(tmp_path, madeup_out):
    """Ruling Y9: a price with fewer than two decimals is raised to two, and one with more keeps its decimals: 12 becomes 12.12 by 1
    percent and 13.20 by 10, 0.125 becomes 0.126 and 0.138 (0.1375 rounded half up), and each moves at least one step of those
    decimals (0.01 becomes 0.02). The change stays the 1 or 10 percent its label says, and the read-back finds it alone."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    po = made_from(d.store, "250108")
    expected = {"12": ("12.12", "13.20"), "0.125": ("0.126", "0.138"), "0.01": ("0.02", "0.02"), "1250.5": ("1263.01", "1375.55")}
    moment = NOW
    for price, (small, large) in expected.items():
        priced = with_line(po, 0, unit_price=price)
        for change, new in (("price_small", small), ("price_large", large)):
            moment += timedelta(minutes=1)
            path = tmp_path / f"{price}_{change}.pdf"
            path.write_bytes(checked(tmp_path, d.store, priced, change, moment))
            doc = read_back(path)
            assert doc.lines[0].unit_price.value == new, (price, change)
            findings = compare(match_lines(doc, priced.document, NoDecider()), doc, priced.document, "confirmation")
            assert [(f.field, f.po_line_ids, Decimal(f.po_value.value), f.doc_value.value) for f in findings.flags] == [
                ("price", ["1"], Decimal(price), new)]


def test_the_header_rows_follow_what_the_order_states(tmp_path, madeup_out):
    """Ruling Y10, rule 3 (mutant M17): the rows of the currency and the Incoterm are printed only when the PO states them, so that a PO
    without them reads back without them, and its case finds no difference in the required information."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    po = made_from(d.store, "250108")
    head = [SUPPLIER_ROW, "Orderbevestiging", "Uw inkooporder 250108, versie 1", "Datum: 12-11-2026"]
    for variant, rows in ((with_header(po, incoterm=None), ["Valuta: EUR"]), (with_header(po, currency=None), ["Leveringsvoorwaarden: DAP Rotterdam"]),
                          (with_header(po, currency=None, incoterm=None), [])):
        path = drawn(tmp_path, variant, "none")
        texts = [line.text for line in extract_lines(path)]
        assert texts[:len(head) + len(rows) + 1] == [*head, *rows, TITLE_ROW]
        doc = read_back(path)
        assert (doc.currency.value, doc.incoterm.value) == (variant.document.currency.value, variant.document.incoterm.value)
        assert checked(tmp_path, d.store, variant, "price_small")


def test_two_equal_line_ids_are_numbered_and_the_change_goes_on_the_first_line(tmp_path, madeup_out):
    """Ruling Y10, rule 3 (mutant M50): a PO whose line ids are the same twice prints the places 1 and 2 in Pos, since the parser takes
    a row for a line by its own number there, and the change goes on the first line. The comparison cannot pair the lines of such a PO
    (it refuses a match that puts one PO line id in two pairs), so a case of it would fail: the read-back refuses the try-out with the
    reason "read_back"."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    po = with_line(with_line(made_from(d.store, "250108"), 0, line_id="1"), 1, line_id="1")
    doc = read_back(drawn(tmp_path, po, "price_small"))
    assert [(line.line_id, line.item_code.value, line.unit_price.value) for line in doc.lines] == [("1", "KM-0801", "12.63"), ("2", "KM-0802", "0.42")]
    with pytest.raises(ValueError):
        compare(match_lines(doc, po.document, NoDecider()), doc, po.document, "confirmation")
    for change in CHANGES:
        assert refused(po, change, lambda: checked(tmp_path, d.store, po, change)) == "read_back", change


def test_the_version_is_quoted_and_chosen_by_reference(tmp_path, madeup_out):
    """MU03's PO has two versions. A try-out quotes the PDF version it was made from ("Uw inkooporder 250103, versie 1"), and the
    version is chosen by that reference (aid/versioning.py), whichever version it was made from. A new try-out is made from the latest
    PDF version, which the dropdown of the page names."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    first, second = d.store.versions("250103")
    assert made_from(d.store, "250103") == second
    for po in (first, second):
        path = drawn(tmp_path, po, "none", f"v{po.version}.pdf")
        assert f"Uw inkooporder 250103, versie {po.version}" in [line.text for line in extract_lines(path)]
        doc = read_back(path)
        assert (doc.po_reference.value, doc.version_hint.value) == ("250103", str(po.version))
        choice = select_version(doc, d.store.candidates("250103", "confirmation"), NoDecider())
        assert (choice.version, choice.method, choice.reason) == (po.version, "reference", f"quotes version {po.version}")
        assert checked(tmp_path, d.store, po, "date_later")


def test_a_po_in_the_second_made_up_layout_gives_a_try_out(tmp_path):
    """The made-up PO in the second made-up layout (aid/madeup/tryout/ANDERS_po_v1.pdf), filed with its layout as the owner's own PO is
    filed: a PO number with letters, no line numbers (the reader numbers them 1 to 12), no price basis, a week and a range of days, and
    twelve lines over two pages. Each change reads back with the PO's values but for the change. Given into the watched folder, the
    try-out is an ordinary case: the scan opens it for the PO by its held number (the number recognition reads no number with letters),
    the run finds the change alone, and as a case whose PO the aid found otherwise than by the number recognition it goes to the buyer
    with the guess's reason and writes nothing (Task 36's ruling N18). Once the buyer takes the price over and closes the case, the
    change stands on the list of changes to enter."""
    settings = OPEN.model_copy(update={"cases_root": tmp_path / "cases", "po_store_root": tmp_path / "store",
                                       "watched_folder": tmp_path / "inbox", "po_layout": ANDERS_LAYOUT})
    store = PoStore(settings.po_store_root)
    po = store.add(ANDERS_PO, ANDERS_LAYOUT)
    assert known_pos(store) == [("IB-2600417 (Proefleverancier S23)", "IB-2600417")] and made_from(store, "IB-2600417") == po
    held = lines_of(po.document)
    assert len(held) == 12 and {line["price_basis"] for line in held} == {None}
    assert (held[2]["delivery_date"], held[8]["delivery_date"], held[3]["quantity"]) == ("2026-W48", "2026-11-23/2026-11-27", "1000")
    for change, changed in FIRST_LINE.items():
        expected = lines_of(po.document)
        expected[0] |= changed
        assert lines_of(read_back(drawn(tmp_path, po, change, f"{change}.pdf"))) == expected, change
        assert checked_confirmation(po, change, NOW, settings, store)
    source = tmp_path / "made.pdf"
    source.write_bytes(checked_confirmation(po, "price_small", NOW, settings, store))
    dropped = drop(source, file_name(po, NOW), settings.watched_folder)
    assert dropped == settings.watched_folder / "PROEF orderbevestiging IB-2600417 20261112-090000.pdf"
    scanned = scan_folder(settings.watched_folder, store, settings.cases_root, settings, NOW)
    [case_id] = scanned.new_cases
    case = load_case(settings.cases_root, case_id)
    assert (case.po_number, case.po_found_by) == ("IB-2600417", FOUND_HELD_NUMBER) and scanned.skipped == []
    d = Deps(settings=settings, store=store, reader=read_with_parser, decider=NoDecider(), writer=PreparedWriter(settings.cases_root / "prepared"),
             mailer=None, clock=lambda: NOW)
    r = run_case(case, d)
    assert [(f.field, f.po_line_ids, f.po_value.value, f.doc_value.value) for f in r.findings.flags] == [("price", ["1"], "12.50", "12.63")]
    assert (r.version.method, r.version.reason) == ("reference", "single version") and r.findings.uncompared == []
    assert [a.rule_id for a in r.actions] == ["G01"] and [(item.finding_id, item.reason) for item in r.review_items] == [("F1", GUESSED_PO)]
    assert (r.status, r.write_entries) == ("review", []) and open_changes(settings.cases_root / "prepared") == []
    decide(case, "RI1", "confirm", True)                                   # the buyer checks the order and takes the price over
    assert close_case(case, d).status == "closed"
    assert [(change.kind, change.po_number, change.new_value) for change in open_changes(settings.cases_root / "prepared")] == [
        ("po_line_price", "IB-2600417", "12.63"), ("article_price", "IB-2600417", "12.63")]


def test_a_long_description_is_cut_and_the_reasons_have_their_sentences(tmp_path, madeup_out):
    """A description of a line matched by its item code that is too wide for its column is cut on its row after its last whole word
    that fits, " …" showing the cut (a single word too wide is cut inside, with "…"), and the line still reads back with the PO's
    values but for the change. A value too wide for its column (a unit, a price basis) is refused with the reason "value" (ruling Y12,
    finding 15), never as a bare error. Every reason has its sentence in both languages, and none names a value."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    po = made_from(d.store, "250108")
    long = "Manometer 0-10 bar met glycerinevulling, aansluiting onder G 1/4, behuizing van roestvast staal"
    wide = with_line(with_line(po, 0, description=long), 1, description="Zeskantboutenassortimentsdoosverpakkingsvoorraadkastdeur")
    doc = read_back(drawn(tmp_path, wide, "price_small", "long.pdf"))
    cut, word = doc.lines[0].description.value, doc.lines[1].description.value
    assert cut.endswith(" …") and long.startswith(cut.removesuffix(" …") + " ") and len(cut) < len(long) and len(doc.lines) == 2
    assert word.endswith("…") and "Zeskantboutenassortimentsdoosverpakkingsvoorraadkastdeur".startswith(word.removesuffix("…")) and " " not in word
    assert [line.description.value for line in read_back(drawn(tmp_path, po, "none", "short.pdf")).lines] == [
        "Manometer 0-10 bar", "Zeskantbout M10 x 40"]                 # the column takes what the item codes leave: MU08's fit whole
    expected = lines_of(wide.document)
    expected[0] |= {"unit_price": "12.63"}
    assert lines_of(doc) == expected
    assert checked(tmp_path, d.store, wide, "price_small")
    # finding 15: a unit, a price basis or a quantity too wide for its column, for every change
    for line, column in ((with_line(po, 0, unit="verpakkingseenheid"), "Eenheid"), (with_line(po, 0, unit="doos 100 st"), "Eenheid"),
                         (with_line(po, 1, price_basis="10000"), "Per"), (with_line(po, 0, quantity="123456789.5"), "Aantal")):
        for change in CHANGES:
            with pytest.raises(TryoutRefused) as refusal:
                confirmation_pdf(line, change, NOW)
            assert refusal.value.reason == "value" and f"the column {column} is too narrow" in str(refusal.value), (column, change)
            assert "verpakking" not in str(refusal.value) and "10000" not in str(refusal.value)
    nl, en = STRINGS["nl"], STRINGS["en"]
    sentences = {"no_lines": ("zij heeft geen orderregels", "it has no order lines"),
                 "no_price": ("de eerste regels hebben geen prijs", "the first lines have no price"),
                 "no_date": ("de eerste regels hebben geen leverdatum", "the first lines have no delivery date"),
                 "character": ("een tekst bevat een teken dat het proefdocument niet kan afdrukken", "a text holds a character the try-out document cannot print"),
                 "item_code": ("een artikelcode is te lang", "an item code is too long"),
                 "value": ("een waarde past niet in het proefdocument", "a value does not fit the try-out document"),
                 "read_back": ("het proefdocument zou niet precies zo worden gelezen als de inkooporder",
                               "the try-out document would not read back exactly as the purchase order"),
                 "not_found": ("de assistent zou de inkooporder van het proefdocument niet vinden",
                               "the assistant would not find the purchase order of the try-out document"),
                 "version": ("de assistent zou niet weten met welke versie van de inkooporder zij moet vergelijken",
                             "the assistant would not know which version of the purchase order to compare with")}
    assert tuple(sentences) == REASONS
    for reason, (dutch, english) in sentences.items():
        assert (nl[f"tryout_reason:{reason}"], en[f"tryout_reason:{reason}"]) == (dutch, english)
    assert nl["tryout_refused"].format(reason=nl["tryout_reason:value"]) == (
        "Van deze inkooporder kan geen proefdocument worden gemaakt: een waarde past niet in het proefdocument.")
    assert en["tryout_refused"].format(reason=en["tryout_reason:value"]) == (
        "No try-out document can be made of this purchase order: a value does not fit the try-out document.")


def test_a_po_without_lines_or_without_the_value_is_refused(tmp_path, madeup_out):
    """A PO without lines gives no try-out; a change of the price, or of the delivery date, needs a line that states it, and the first
    line that does takes the change. Each refusal is a reason with its sentence, and nothing is drawn."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    po = made_from(d.store, "250108")
    empty = po.model_copy(update={"document": po.document.model_copy(update={"lines": []})})
    no_price = with_line(with_line(po, 0, unit_price=None), 1, unit_price=None)
    no_date = with_line(with_line(po, 0, delivery_date=None), 1, delivery_date=None)
    refusals = [(empty, change, "no_lines") for change in CHANGES]
    refusals += [(no_price, "price_small", "no_price"), (no_price, "price_large", "no_price")]
    refusals += [(no_date, "date_later", "no_date"), (no_date, "date_much_later", "no_date")]
    for refused_po, change, reason in refusals:
        assert refused(refused_po, change) == reason, change
    for change in ("none", "date_later"):                     # without prices the other changes are made
        assert lines_of(read_back(drawn(tmp_path, no_price, change, f"no_price_{change}.pdf")))[0]["unit_price"] is None
    second = with_line(po, 0, unit_price=None)                # the first line that states a price takes the change: line 2, 0.42 to 0.43
    assert [line["unit_price"] for line in lines_of(read_back(drawn(tmp_path, second, "price_small", "second.pdf")))] == [None, "0.43"]


def test_known_pos_are_the_pos_as_they_stand_the_one_filed_last_first(tmp_path, madeup_out):
    """Amendment 2 (Task 40 is merged): known_pos lists each PO as it stands (PoStore.as_it_stands), the one filed last first, at most
    `limit` of them. A PO known from an export only is listed by its number; its try-out quotes no version, since there is no PDF
    version to quote, and is compared with its look. A PO whose look came after its PDF is still made from its PDF version, which a
    confirmation is matched against while its PO has one."""
    store = PoStore(tmp_path / "store")
    store.add(madeup_out / "MU01_po_v1.pdf")
    store.add(madeup_out / "MU08_po_v1.pdf")
    store.from_export(madeup_out / "CART02_export.csv", EXPORT_COLUMNS)
    base = datetime(2026, 11, 12, 8, 0).timestamp()
    for folder, seconds in (("250101/v1", 0), ("250108/v1", 60), ("250202/e1", 120)):
        os.utime(store.root / folder / "po.json", (base + seconds, base + seconds))
    assert known_pos(store) == [("250202", "250202"), ("250108 (S08)", "250108"), ("250101 (S01)", "250101")]
    assert known_pos(store, limit=2) == [("250202", "250202"), ("250108 (S08)", "250108")]
    # an export of 250108 with another price on line 1, filed after its PDF: the PO as it stands is that look, filed last
    export = tmp_path / "later_export.csv"
    export.write_text(EXPORT_TITLES + "250108,1,KM-0801,Manometer 0-10 bar,3,st,12.90,1,2026-11-16\n"
                      "250108,2,KM-0802,Zeskantbout M10 x 40,50,st,0.42,1,2026-11-16\n", encoding="utf-8")
    store.from_export(export, EXPORT_COLUMNS)
    os.utime(store.root / "250108" / "e1" / "po.json", (base + 180, base + 180))
    assert store.as_it_stands("250108").document.reader.startswith("export:")
    assert [number for _, number in known_pos(store)] == ["250108", "250202", "250101"]
    pdf_version = made_from(store, "250108")
    assert pdf_version == store.versions("250108")[-1]
    doc = read_back(drawn(tmp_path, pdf_version, "none", "after_look.pdf"))
    assert doc.version_hint.value == "1" and lines_of(doc)[0]["unit_price"] == "12.50"          # the PDF's price, not the look's
    assert checked(tmp_path, store, pdf_version, "none")
    # the PO known from an export only: no version quoted, and the case is compared with its look
    look = made_from(store, "250202")
    assert look == store.as_it_stands("250202") and store.versions("250202") == []
    path = drawn(tmp_path, look, "price_small", file_name(look, NOW))
    assert "Uw inkooporder 250202" in [line.text for line in extract_lines(path)]
    doc = read_back(path)
    assert (doc.po_reference.value, doc.version_hint.value) == ("250202", None)
    assert checked(tmp_path, store, look, "price_small") == path.read_bytes()
    d = on_store(store, tmp_path)
    case = open_upload_case(d.settings.cases_root, [path], "250202", "confirmation", NOW)
    r = run_case(case, d)
    assert [PoVersion.model_validate(v) for v in case.read_step("po_versions")["versions"]] == [look]
    assert [(f.field, f.po_line_ids, f.po_value.value, f.doc_value.value) for f in r.findings.flags] == [("price", ["1"], "12.50", "12.63")]
    assert (r.version.version, r.version.reason) == (1, "single version") and r.findings.uncompared == []


# --- a case made from a try-out: the guards of aid/tryout_mark.py (ruling Y4) ---

SUPPLIER_ADDRESS = "orders-s08@example.com"         # a made-up address of the made-up supplier S08


def tryout_settings(tmp_path: Path, **values) -> Settings:
    """The settings of a data folder in the test mode: no limit, the cases and the watched folder under tmp_path, S08's made-up
    address, and the first cases of a supplier not held for the buyer unless `values` say so."""
    return OPEN.model_copy(update={"cases_root": tmp_path / "cases", "watched_folder": tmp_path / "inbox",
                                   "supplier_emails": {"S08": SUPPLIER_ADDRESS}, "first_cases_review": 0} | values)


def runner_pass(settings: Settings, store: PoStore, reader=read_with_parser, writer=None) -> None:
    """One pass of the runner (run_loop) over the watched folder, with the file mailer into <cases_root>/outbox, as the runner command
    makes it with send_via "file": every message the runner would send lies in the outbox. The writer is the prepared writer into
    <cases_root>/prepared unless one is given."""
    settings.watched_folder.mkdir(parents=True, exist_ok=True)
    d = Deps(settings=settings, store=store, reader=reader, decider=NoDecider(), writer=writer or PreparedWriter(settings.cases_root / "prepared"),
             mailer=FileMailer(settings.cases_root / "outbox", sender=settings.aid_email, clock=lambda: NOW), clock=lambda: NOW)
    assert run_loop(d, settings.cases_root.parent / "STOP", poll_seconds=0, max_passes=1) == 1


def outbox(settings: Settings) -> list[Path]:
    return sorted((settings.cases_root / "outbox").glob("*.eml"))


def results(settings: Settings) -> list[CaseResult]:
    """The result of every case under the cases root, in the order of the case ids."""
    return [CaseResult.model_validate_json(path.read_text(encoding="utf-8")) for path in sorted(settings.cases_root.glob("*/steps/08_result.json"))]


def give(settings: Settings, store: PoStore, po_number: str, change: str, moment: datetime = NOW) -> Path:
    """A try-out given to the watched folder as the page's "Proefbevestiging direct aan de assistent geven" gives it."""
    po = made_from(store, po_number)
    made = settings.cases_root.parent / "made" / file_name(po, moment)
    made.parent.mkdir(parents=True, exist_ok=True)
    made.write_bytes(checked_confirmation(po, change, moment, settings, store))
    return drop(made, made.name, settings.watched_folder)


def misreading(path: Path, kind: str):
    """A reader that misreads the quantity of the second line, as a model might: the routing then asks the supplier (R06)."""
    doc = read_with_parser(path, kind)
    second = doc.lines[1].model_copy(update={"quantity": DocValue(value="60", text="60")})
    return doc.model_copy(update={"lines": [doc.lines[0], second, *doc.lines[2:]]})


def test_the_scan_takes_a_try_out_only_while_try_outs_are_offered(tmp_path, madeup_out):
    """Ruling Y4(b), finding 4 (the reviewers' s9_limit_later.py): the scan takes a try-out only while test_mode is true and no
    authorization limit is set, as the part "Proefdraaien" offers one. A try-out made while it was offered and given after the limit
    was set (PO 250108's total is above 50.00), or after the test mode was switched off, is skipped as "try-out document outside the
    test mode" and moved to processed/, so that the digest says why: no case is opened and nothing reaches the approver. Given while
    try-outs are offered, the same try-out opens its case, which the rules take over."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = tryout_settings(tmp_path)
    po = made_from(d.store, "250108")
    pdf, name = checked_confirmation(po, "price_small", NOW, settings, d.store), file_name(po, NOW)      # "maken": the download
    for n, later in enumerate(({"authorization_limit": Decimal("50.00")}, {"test_mode": False},
                               {"test_mode": False, "authorization_limit": Decimal("50.00")}), 1):
        now_settings = settings.model_copy(update=later)
        now_settings.watched_folder.mkdir(parents=True, exist_ok=True)
        (now_settings.watched_folder / name).write_bytes(pdf)
        runner_pass(now_settings, PoStore(d.store.root, now_settings.authorization_limit))
        assert results(now_settings) == [] and outbox(now_settings) == [], later
        state = json.loads((settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
        assert state["skipped"] == [[name, "try-out document outside the test mode"]]
        assert not (settings.watched_folder / name).exists() and len(list((settings.watched_folder / "processed").iterdir())) == n
    (settings.watched_folder / name).write_bytes(pdf)
    runner_pass(settings, d.store)
    [r] = results(settings)
    assert [a.rule_id for a in r.actions] == ["R04", "R01"] and outbox(settings) == [] and TRYOUT_OUTSIDE == "try-out document outside the test mode"


def test_no_message_about_a_case_made_from_a_try_out_is_sent(tmp_path, madeup_out):
    """Ruling Y4(c) and the reviewers' notes on the reader "llm": a reader that misreads a try-out (a fake one, as a model might) makes
    the routing ask the supplier about a quantity (R06); a try-out whose PO is above a limit that was set after it was taken asks the
    approver (R10). Neither message is sent: the run records it, where it records the messages of a case, as "not sent (try-out
    document: nothing is sent)", the mailer is never asked (its outbox stays empty), and each finding of the message is a review item
    with that reason, so that the case comes to the buyer and does not wait for an answer. MU08's own confirmation, read with the same
    reader, sends its question: the guard is the try-out's alone."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = tryout_settings(tmp_path)
    give(settings, d.store, "250108", "none")
    runner_pass(settings, d.store, reader=misreading)
    [r] = results(settings)
    assert [(a.rule_id, a.route) for a in r.actions][0] == ("R06", "ASK") and outbox(settings) == []
    assert [(m["rule_id"], m["template"], m["status"]) for m in r.messages] == [("R06", "quantity_unit", "not sent (try-out document: nothing is sent)")]
    assert r.status == "review" and [(item.finding_id, item.reason) for item in r.review_items] == [("F1", NOTHING_SENT)]
    assert NOTHING_SENT == "try-out document: nothing is sent" and TRYOUT_NOT_SENT == f"not sent ({NOTHING_SENT})"
    case = load_case(settings.cases_root, r.case_id)
    assert [path for path in (case.root / "messages").rglob("*")] == []     # nothing where a message sent would be recorded
    # the request to the approver: a limit set after the try-out was taken, the case run as an upload
    limited = settings.model_copy(update={"authorization_limit": Decimal("50.00")})
    store = PoStore(d.store.root, limited.authorization_limit)
    made = tmp_path / "price_small.pdf"
    made.write_bytes(checked_confirmation(made_from(d.store, "250108"), "price_small", NOW, settings, d.store))
    approver = Deps(settings=limited, store=store, reader=read_with_parser, decider=NoDecider(), writer=PreparedWriter(tmp_path / "prepared2"),
                    mailer=FileMailer(tmp_path / "outbox2", sender=settings.aid_email, clock=lambda: NOW), clock=lambda: NOW)
    r2 = run_case(open_upload_case(tmp_path / "cases2", [made], "250108", "confirmation", NOW), approver)
    assert [(m["rule_id"], m["template"], m["status"]) for m in r2.messages] == [("R10", "reapproval", TRYOUT_NOT_SENT)]
    assert not (tmp_path / "outbox2").exists() and {item.reason for item in r2.review_items} == {NOTHING_SENT}
    # MU08's own confirmation, misread in the same way: its question is sent
    own = Deps(settings=settings.model_copy(update={"cases_root": tmp_path / "cases3"}), store=d.store, reader=misreading, decider=NoDecider(),
               writer=PreparedWriter(tmp_path / "prepared3"), mailer=FileMailer(tmp_path / "outbox3", sender=settings.aid_email, clock=lambda: NOW),
               clock=lambda: NOW)
    r3 = run_case(open_upload_case(tmp_path / "cases3", [madeup_out / "MU08_conf.pdf"], "250108", "confirmation", NOW), own)
    assert [(m["template"], m["status"]) for m in r3.messages] == [("quantity_unit", "sent")] and len(list((tmp_path / "outbox3").glob("*.eml"))) == 1


def test_the_review_tab_asks_the_supplier_nothing_about_a_try_out(tmp_path, madeup_out):
    """Ruling Y4(c), the reviewers' s10_ask.py: a try-out case in review (the supplier's first cases come to the buyer). "Leverancier
    vragen" on its price item is refused and records nothing, and the review tab answers "Dit is een proefdocument: de assistent stuurt
    hierover niets naar een leverancier."; a decision "ask" that stands in the case's decisions all the same (written before this
    guard) sends nothing either: the close records the question as not sent with that reason and keeps the case in review, and the
    outbox of the review page's file mailer stays empty."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = tryout_settings(tmp_path, first_cases_review=5)
    give(settings, d.store, "250108", "price_large")
    runner_pass(settings, d.store)
    [r] = results(settings)
    case = load_case(settings.cases_root, r.case_id)
    item = next(item.item_id for item in r.review_items if item.finding_id)
    assert r.status == "review" and tryout_case(case)
    with pytest.raises(ValueError):
        decide(case, item, "ask")
    assert not (case.root / "decisions.jsonl").exists()
    for language in ("nl", "en"):
        assert decision_text(case, item, "ask", False, language) == (STRINGS[language]["tryout_nothing_sent"], False)
    assert STRINGS["nl"]["tryout_nothing_sent"] == "Dit is een proefdocument: de assistent stuurt hierover niets naar een leverancier."
    assert STRINGS["en"]["tryout_nothing_sent"] == "This is a try-out document: the assistant sends nothing about it to a supplier."
    assert decision_text(case, item, "keep", False, "nl") == (STRINGS["nl"]["saved"].format(item=item), True)
    (case.root / "decisions.jsonl").write_text(json.dumps({"item_id": item, "decision": "ask", "article_update": False,
                                                           "at": "2026-11-12T09:30:00"}) + "\n", encoding="utf-8")
    page = Deps(settings=settings, store=d.store, reader=read_with_parser, decider=NoDecider(), writer=PreparedWriter(settings.cases_root / "prepared"),
                mailer=FileMailer(settings.cases_root / "outbox", sender=settings.aid_email, clock=lambda: NOW), clock=lambda: NOW)
    closed = close_case(case, page)
    assert closed.status == "review" and [(m["template"], m["status"]) for m in closed.messages] == [("missing_info", TRYOUT_NOT_SENT)]
    assert outbox(settings) == [] and [path for path in (case.root / "messages").rglob("*")] == []
    assert close_text(case, page, "nl")[0] == STRINGS["nl"]["new_status"].format(status=STRINGS["nl"]["status:review"])


def test_the_list_marks_the_changes_of_try_out_documents(tmp_path, madeup_out, monkeypatch):
    """Ruling Y4(d), finding 5 (the reviewers' s3_switch_off.py): on the tab "In Exact invoeren" each change that only try-out
    documents asked carries "(proefdocument)" behind its row, in the test mode and out of it, in the page's language; the change of a
    supplier's own confirmation carries nothing. Ruling Y4(a): tryout_case knows a case made from a try-out by its document alone, the
    first row of its first page, without a file in the case folder and without a change to case.json, and reads that page once."""
    import aid.tryout_mark

    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = tryout_settings(tmp_path)
    give(settings, d.store, "250101", "price_small")
    shutil.copy(madeup_out / "MU08_conf.pdf", settings.watched_folder / "MU08_conf.pdf")
    runner_pass(settings, d.store)
    tryout, own = (load_case(settings.cases_root, r.case_id) for r in sorted(results(settings), key=lambda r: r.case_id))
    found, passed = changes(settings.cases_root / "prepared")
    open_ = [change for change in found if change.state == "open"]
    rows = {language: [text for text, _ in hand_list.tick_rows(hand_list.blocks(open_, d.store, settings.cases_root, language))]
            for language in ("nl", "en")}
    assert rows["nl"] == ["Inkooporder 250101 (S01), regel 1 (KM-1001): prijs 12,50 → 12,63 (proefdocument)",
                          "Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,50 → 12,70",
                          "artikel KM-0801: inkoopprijs onbekend → 12,70", "artikel KM-1001: inkoopprijs onbekend → 12,63 (proefdocument)"]
    assert [text.endswith(" (try-out document)") for text in rows["en"]] == [True, False, False, True] and passed == 0
    assert sum(change.tryout for change in open_changes(settings.cases_root / "prepared")) == 2
    before = {path: path.read_bytes() for path in tryout.root.rglob("*") if path.is_file()}
    read = []
    monkeypatch.setattr(aid.tryout_mark, "extract_lines", lambda path: read.append(path) or extract_lines(path))
    aid.tryout_mark._first_row_is_tryout.cache_clear()
    assert [tryout_case(case) for case in (tryout, tryout, own, own)] == [True, True, False, False] and len(read) == 2
    assert {path: path.read_bytes() for path in tryout.root.rglob("*") if path.is_file()} == before
    assert (STRINGS["nl"]["hand_tryout"], STRINGS["en"]["hand_tryout"]) == ("(proefdocument)", "(try-out document)")


# --- the second review round: the digest, the writer, the list and the first cases (rulings Y17 to Y19, Y22 and Y27) ---

class SystemWriter:
    """A writer standing in for one that reaches a system, as the Exact writer does: it keeps each operation it is asked to make and
    makes it ("done")."""
    name = "exact_api"

    def __init__(self):
        self.asked: list[tuple[str, str]] = []

    def apply(self, op, case_id: str, rule_id: str, evidence: dict, now: datetime) -> WriteLogEntry:
        self.asked.append((case_id, op.kind))
        return WriteLogEntry(entry_id=f"{len(self.asked):032x}", case_id=case_id, op=op, status="done", previous=op.old_value,
                             new=op.new_value, rule_id=rule_id, evidence=evidence, timestamp=now.isoformat())


def digest_texts(settings: Settings) -> list[str]:
    """The text of each digest the runner wrote into the outbox, as the buyer reads it, without its empty lines."""
    texts = []
    for path in outbox(settings):
        mail = message_from_bytes(path.read_bytes(), policy=policy.default)
        if mail["Subject"].startswith("[Inkoopassistent]"):
            texts.append("\n".join(line for line in mail.get_body(preferencelist=("plain",)).get_content().splitlines() if line.strip()))
    return texts


def test_the_digest_marks_a_try_out_and_asks_nothing_of_exact_for_it(tmp_path, madeup_out):
    """Ruling Y17, finding 1 (the checkers' e1_digest.py and e1b_digest_after_switch.py): in the digest a case made from a try-out has
    " (proefdocument)" behind its line, its rows say "(proefdocument)" in place of "(voorbereid)" and "(zelf in Exact doen)", and the
    count of the changes to enter in Exact leaves its changes out: no line tells the buyer to do anything in Exact for it. So in the
    test mode with a digest per case, and in the daily digest made after the test mode was switched off, in English too. A supplier's
    own confirmation in the same digest keeps its words and its count: the marks are the try-out's alone."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    po = made_from(d.store, "250108")
    tryout_file = file_name(po, NOW)
    settings = tryout_settings(tmp_path / "per_case", digest="per_case")
    give(settings, d.store, "250108", "price_small")
    shutil.copy(madeup_out / "MU14_conf.pdf", settings.watched_folder / "MU14_conf.pdf")
    runner_pass(settings, d.store)
    [text] = digest_texts(settings)
    lines = text.splitlines()
    assert lines[:3] == ["Dagoverzicht 12-11-2026", f"In Exact in te voeren: 1 wijziging. Open het tabblad In Exact invoeren: {list_link(settings)}",
                         "Behandelde zaken"]
    start = lines.index("- PO 250108 (S08): afgesloten, 4 voorbereide wijzigingen (proefdocument)")
    assert lines[start + 1:start + 5] == ["    - prijs regel 1: 12,50 → 12,63 (proefdocument)", "    - artikelprijs KM-0801: onbekend → 12,63 (proefdocument)",
                                          f"    - bijlage {tryout_file} (proefdocument)", "    - status: Besteld → Bevestigd (proefdocument)"]
    own = lines.index("- PO 250114 (S14): afgesloten, 1 voorbereide wijziging, 2 in Exact zelf te doen")
    assert lines[own + 1].endswith(" (voorbereid)") and [line.endswith(" (zelf in Exact doen)") for line in lines[own + 2:own + 4]] == [True, True]
    assert not [line for line in lines[start:start + 5] if "voorbereid)" in line or "zelf in Exact" in line]
    # the daily digest made after the switch is turned off, in English: the marks stay, and nothing of the try-out is to enter
    settings = tryout_settings(tmp_path / "daily", digest="daily", digest_time="23:59", language="en")
    give(settings, d.store, "250108", "date_later")
    runner_pass(settings, d.store)
    assert digest_texts(settings) == []                                   # not due yet
    runner_pass(settings.model_copy(update={"test_mode": False, "digest_time": "00:01"}), d.store)
    [text] = digest_texts(settings)
    assert text.splitlines() == ["Daily digest 12-11-2026", "Handled cases", "- PO 250108 (S08): closed, 3 prepared changes (try-out document)",
                                 "    - delivery date line 1: 2026-11-16 → 2026-11-19 (try-out document)",
                                 f"    - attachment {tryout_file} (try-out document)", "    - status: Besteld → Bevestigd (try-out document)"]
    assert (settings.cases_root / "digests" / "2026-11-12.html").read_text(encoding="utf-8").count("(try-out document)") == 4


def test_a_try_out_writes_through_the_prepared_writer_only(tmp_path, madeup_out):
    """Ruling Y18, finding 2 (the checkers' e7_rest_writer.py): a case made from a try-out writes through the prepared writer only,
    never through a writer that reaches a system, whatever the Deps' writer is: here a chain of a writer into a system and the
    prepared writer (RetryingWriter, which no command builds: the test hands it to the Deps), at the run, and at the close and the
    undo of the review page. A supplier's own confirmation in the same pass writes through the system's writer as before."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = tryout_settings(tmp_path, exact_base_url="https://exact.example.com/api/")
    system = SystemWriter()
    chain = RetryingWriter(system, PreparedWriter(settings.cases_root / "prepared"))
    give(settings, d.store, "250108", "price_small")
    shutil.copy(madeup_out / "MU14_conf.pdf", settings.watched_folder / "MU14_conf.pdf")
    runner_pass(settings, d.store, writer=chain)
    tryout_result, own = sorted(results(settings), key=lambda r: r.case_id)
    assert {case for case, _ in system.asked} == {own.case_id} and all(entry.status == "done" for entry in own.write_entries)
    assert [(entry.op.kind, entry.status) for entry in tryout_result.write_entries] == [
        ("po_line_price", "prepared"), ("article_price", "prepared"), ("attach_document", "prepared"), ("set_status", "prepared")]
    # the review page: a try-out in review, confirmed, closed and undone, with the same chain
    settings = settings.model_copy(update={"first_cases_review": 5, "cases_root": tmp_path / "cases2", "watched_folder": tmp_path / "inbox2"})
    give(settings, d.store, "250101", "price_small")
    runner_pass(settings, d.store, writer=chain)
    [r] = results(settings)
    case = load_case(settings.cases_root, r.case_id)
    for item in r.review_items:
        decide(case, item.item_id, "confirm", article_update=True)
    page = Deps(settings=settings, store=d.store, reader=read_with_parser, decider=NoDecider(), writer=chain, mailer=None, clock=lambda: NOW)
    closed = close_case(case, page)
    entry = next(entry for entry in closed.write_entries if entry.op.kind == "po_line_price")
    assert undo(case, entry.entry_id, page).status == "prepared"
    assert {case_id for case_id, _ in system.asked} == {own.case_id} and closed.status == "closed"
    assert tryout_writer(chain, settings) is chain.fallback
    built = build_deps(settings.model_copy(update={"reader": "parser"}), {"AID_EXACT_TOKEN": "made-up-token"})     # the runner command's Deps
    assert type(built.writer) is PreparedWriter and tryout_writer(built.writer, settings) is built.writer         # no command builds a route
    assert built.writer.out_dir == settings.cases_root / "prepared"


def test_a_try_out_has_a_place_of_its_own_on_the_list(tmp_path, madeup_out):
    """Ruling Y19, finding 3 (the checkers' e4_replaced.py): the rows of a case made from a try-out are never one run with the rows of
    other cases, and neither replaces the other, since the place of a change includes whether a try-out asked it. A supplier's own
    change (MU08's confirmation: 12,70) stays open beside the later try-out's marked change (12,63) and survives its drop; so it does
    when the try-out came first. Every change on the list is all from try-outs or not at all, which its mark and the count of the
    digest follow."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    for order in ("supplier first", "try-out first"):
        settings = tryout_settings(tmp_path / order.replace(" ", "_"))
        for step in (order.split()[0], "try-out" if order.startswith("supplier") else "supplier"):
            if step == "supplier":
                settings.watched_folder.mkdir(parents=True, exist_ok=True)
                shutil.copy(madeup_out / "MU08_conf.pdf", settings.watched_folder / "MU08_conf.pdf")
            else:
                give(settings, d.store, "250108", "price_small")
            runner_pass(settings, d.store)
        prepared = settings.cases_root / "prepared"
        listed = sorted((change.state, change.tryout, hand_list.row_text(change, settings.cases_root, "nl")) for change in changes(prepared)[0])
        assert listed == [("open", False, "artikel KM-0801: inkoopprijs onbekend → 12,70"), ("open", False, "regel 1 (KM-0801): prijs 12,50 → 12,70"),
                          ("open", True, "artikel KM-0801: inkoopprijs onbekend → 12,63 (proefdocument)"),
                          ("open", True, "regel 1 (KM-0801): prijs 12,50 → 12,63 (proefdocument)")], order
        made_up = [entry_id for change in open_changes(prepared) if change.tryout for entry_id in change.ids]
        assert mark(prepared, made_up, "dropped", NOW) == 2
        assert sorted((change.new_value, change.tryout) for change in open_changes(prepared)) == [("12.70", False), ("12.70", False)], order


def test_a_version_the_aid_could_not_choose_has_a_reason_of_its_own(tmp_path, monkeypatch):
    """Ruling Y22, finding 6 (the checkers' version_probe2.py): a try-out of a PO whose number the parser does not read (letters, as in
    the second made-up layout) and that has two PDF versions with the same lines is found by the scan, but the aid would not know which
    version to compare it with: it is refused with the reason "version" for every change, never as an order not found. With one
    version the same order gives its try-out."""
    from aid.madeup import generate

    store = PoStore(tmp_path / "store")
    store.add(ANDERS_PO, ANDERS_LAYOUT)
    settings = OPEN.model_copy(update={"cases_root": tmp_path / "cases", "po_store_root": store.root, "watched_folder": tmp_path / "inbox",
                                       "po_layout": ANDERS_LAYOUT})
    assert checked_confirmation(made_from(store, "IB-2600417"), "price_small", NOW, settings, store)
    monkeypatch.setattr(generate, "TRYOUT_LINES", [dataclasses.replace(generate.TRYOUT_LINES[0], unit_price="12.90"), *generate.TRYOUT_LINES[1:]])
    second = tmp_path / "ANDERS_po_v2.pdf"
    second.write_bytes(generate.pdf_bytes(generate.tryout_po_pages(), "made-up PO in a second layout, version 2"))
    store.add(second, ANDERS_LAYOUT)
    assert [version.version for version in store.versions("IB-2600417")] == [1, 2]
    for change in CHANGES:
        assert refused(None, change, lambda: checked_confirmation(made_from(store, "IB-2600417"), change, NOW, settings, store)) == "version"
    assert not (tmp_path / "cases" / "tmp").exists()
    nl, en = STRINGS["nl"], STRINGS["en"]
    assert nl["tryout_reason:version"] == "de assistent zou niet weten met welke versie van de inkooporder zij moet vergelijken"
    assert en["tryout_reason:version"] == "the assistant would not know which version of the purchase order to compare with"


def test_a_try_out_is_no_suppliers_first_case(tmp_path, madeup_out):
    """Ruling Y27 (the checkers' e9_first_cases.py): the count of a supplier's closed cases for "Eerste zaken van een leverancier"
    passes over a case made from a try-out. With the setting at 1, a try-out of S08's order that the buyer confirmed and closed on
    the review page leaves S08's own confirmation the supplier's first case, which comes to the buyer (R11), as without the try-out."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = tryout_settings(tmp_path, first_cases_review=1)
    give(settings, d.store, "250108", "price_small")
    runner_pass(settings, d.store)
    [r] = results(settings)
    case = load_case(settings.cases_root, r.case_id)
    assert [a.rule_id for a in r.actions] == ["R11"]
    for item in r.review_items:
        decide(case, item.item_id, "confirm")
    page = Deps(settings=settings, store=d.store, reader=read_with_parser, decider=NoDecider(),
                writer=PreparedWriter(settings.cases_root / "prepared"), mailer=None, clock=lambda: NOW)
    assert close_case(case, page).status == "closed"
    shutil.copy(madeup_out / "MU08_conf.pdf", settings.watched_folder / "MU08_conf.pdf")
    runner_pass(settings.model_copy(update={"test_mode": False}), d.store)
    [own] = [result for result in results(settings) if result.case_id != r.case_id]
    assert own.status == "review" and [a.rule_id for a in own.actions] == ["R11"]


# --- a failed read is never remembered, and the review tab names a try-out (rulings Y29 and Y30) ---

def held_once(monkeypatch, fails: int = 1) -> tuple[list[Path], list[float]]:
    """tryout_mark's read of a try-out's first page failing `fails` times, as when another program holds the file for a moment, and
    then reading as before; the cache emptied, as in a process of its own, and the pauses between two tries recorded, not slept.
    Returns the files read and the pauses."""
    reads, pauses, left = [], [], [fails]

    def read(path):
        reads.append(Path(path))
        if left[0] and Path(path).name.startswith("PROEF"):
            left[0] -= 1
            raise PermissionError(13, "made-up: the file is held by another program for a moment")
        return extract_lines(path)
    monkeypatch.setattr(aid.tryout_mark, "extract_lines", read)
    monkeypatch.setattr(aid.tryout_mark, "sleep", pauses.append, raising=False)
    aid.tryout_mark._first_row_is_tryout.cache_clear()
    return reads, pauses


def test_a_failed_read_of_a_try_out_is_tried_again_and_never_kept(tmp_path, madeup_out, monkeypatch):
    """Ruling Y29 (the checkers' failopen.py): tryout_case reads a try-out's first page again, after a short pause, when the read
    fails, as when another program holds the file for a moment; and a failed read is never kept, so that the next call reads the file
    again. Only a call whose every try fails answers "no try-out", and only for itself. The list reads a case.json the same way: one
    that cannot be read once is read again, and the try-out's changes keep their place and their mark."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = tryout_settings(tmp_path)
    give(settings, d.store, "250108", "price_small")
    runner_pass(settings, d.store)
    [r] = results(settings)
    case = load_case(settings.cases_root, r.case_id)
    reads, pauses = held_once(monkeypatch)
    assert tryout_case(case)                                                     # failed once, read again within the call
    assert len(reads) == 2 and pauses == [aid.tryout_mark.READ_PAUSE] and 0 < aid.tryout_mark.READ_PAUSE <= 1
    assert tryout_case(case) and len(reads) == 2                                 # the answer read is kept
    reads, pauses = held_once(monkeypatch, fails=2)                              # every try of one call fails
    assert not tryout_case(case) and len(reads) == 2 and aid.tryout_mark._first_row_is_tryout.cache_info().currsize == 0
    assert tryout_case(case) and len(reads) == 3                                 # the failure was not kept: the file is read again
    load, torn = aid.tryout_mark.load_case, [1]

    def load_once(cases_root, case_id):                                         # a case.json that cannot be read for a moment
        if torn[0]:
            torn[0] -= 1
            raise ValueError("made-up: the case.json is being written")
        return load(cases_root, case_id)
    monkeypatch.setattr(aid.tryout_mark, "load_case", load_once, raising=False)
    assert [change.tryout for change in open_changes(settings.cases_root / "prepared")] == [True, True] and torn == [0]


def test_a_read_that_fails_once_leaves_every_guard_in_force(tmp_path, madeup_out, monkeypatch):
    """Ruling Y29 (the checkers' failopen.py, F2 and F3, and failopen_page.py): a process whose first read of a try-out's first page
    fails once keeps every guard. The runner, with the Exact writer before the prepared writer, writes the try-out's changes prepared
    only; the review page refuses "Leverancier vragen" on a try-out and sends nothing at its close; and the page's close of a try-out
    beside a supplier's own confirmation of the same order leaves the try-out's changes marked in a place of their own, and the
    supplier's open."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    system = SystemWriter()
    settings = tryout_settings(tmp_path / "runner", exact_base_url="https://exact.example.com/api/")
    give(settings, d.store, "250108", "price_small")
    held_once(monkeypatch)
    runner_pass(settings, d.store, writer=RetryingWriter(system, PreparedWriter(settings.cases_root / "prepared")))
    [r] = results(settings)
    assert system.asked == [] and [entry.status for entry in r.write_entries] == ["prepared"] * 4 and outbox(settings) == []
    # the review page: "ask" and the close of a try-out in review
    settings = tryout_settings(tmp_path / "page")
    give(settings, d.store, "250108", "price_large")
    runner_pass(settings, d.store)
    [r] = results(settings)
    case = load_case(settings.cases_root, r.case_id)
    item = next(item.item_id for item in r.review_items if item.finding_id)
    held_once(monkeypatch)
    assert decision_text(case, item, "ask", False, "nl") == (STRINGS["nl"]["tryout_nothing_sent"], False)
    (case.root / "decisions.jsonl").write_text(json.dumps({"item_id": item, "decision": "ask", "article_update": False,
                                                           "at": "2026-11-12T09:30:00"}) + "\n", encoding="utf-8")
    page = Deps(settings=settings, store=d.store, reader=read_with_parser, decider=NoDecider(),
                writer=RetryingWriter(system, PreparedWriter(settings.cases_root / "prepared")),
                mailer=FileMailer(settings.cases_root / "outbox", sender=settings.aid_email, clock=lambda: NOW), clock=lambda: NOW)
    held_once(monkeypatch)
    closed = close_case(case, page)
    assert closed.status == "review" and [(m["template"], m["status"]) for m in closed.messages] == [("missing_info", TRYOUT_NOT_SENT)]
    assert outbox(settings) == [] and system.asked == []
    # the list: a supplier's own confirmation and a try-out of 250108, both in review, settled on the page
    settings = tryout_settings(tmp_path / "list", first_cases_review=5)
    settings.watched_folder.mkdir(parents=True)
    shutil.copy(madeup_out / "MU08_conf.pdf", settings.watched_folder / "MU08_conf.pdf")
    runner_pass(settings, d.store)
    give(settings, d.store, "250108", "price_small")
    runner_pass(settings, d.store)
    page = page.model_copy(update={"settings": settings, "writer": PreparedWriter(settings.cases_root / "prepared")})
    held_once(monkeypatch)
    for r in results(settings):
        case = load_case(settings.cases_root, r.case_id)
        for item in r.review_items:
            decide(case, item.item_id, "confirm", article_update=True)
        assert close_case(case, page).status == "closed"
    listed = sorted((change.state, change.tryout, change.new_value) for change in changes(settings.cases_root / "prepared")[0])
    assert listed == [("open", False, "12.70"), ("open", False, "12.70"), ("open", True, "12.63"), ("open", True, "12.63")]


def test_the_review_tab_names_a_try_out(tmp_path, madeup_out):
    """Ruling Y30 (the check's worry a): in the case dropdown of the tab "Beoordelen" a case made from a try-out has " (proefdocument)"
    (" (try-out document)") behind its label, so that a try-out and a supplier's confirmation of the same order opened in the same
    minute never look the same; the supplier's case keeps its label. MANUAL 2.2 says so."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = tryout_settings(tmp_path, first_cases_review=5)
    give(settings, d.store, "250108", "price_small")
    shutil.copy(madeup_out / "MU08_conf.pdf", settings.watched_folder / "MU08_conf.pdf")
    runner_pass(settings, d.store)
    made_up = {r.case_id for r in results(settings) if tryout_case(load_case(settings.cases_root, r.case_id))}
    offered = offered_cases(settings.cases_root)
    assert len(offered) == 2 and len(made_up) == 1
    for language, one, mark in (("nl", "1 punt", "(proefdocument)"), ("en", "1 item", "(try-out document)")):
        labels = sorted((summary.case_id in made_up, _case_label(summary, STRINGS[language])) for summary in offered)
        assert labels == [(False, f"PO 250108 (S08), 2026-11-12 09:00, {one}"), (True, f"PO 250108 (S08), 2026-11-12 09:00, {one} {mark}")]
    [tried] = made_up
    assert load_case_view(load_case(settings.cases_root, tried)).summary.tryout
    manual = Path("aid/MANUAL.md").read_text(encoding="utf-8")
    assert 'A case made from a try-out confirmation (section 3.17) has "(proefdocument)" behind it' in manual


def test_the_confirmations_under_the_list_mark_a_try_out(tmp_path, madeup_out):
    """The merge with Task 35: the section "Bevestigd door de leverancier" under the list shows the confirmation of a try-out with
    "(proefdocument)" behind its line, in both languages, so that it never asks the buyer to attach a made-up document and set the
    status Bevestigd in the real Exact; a supplier's own confirmation keeps its line. MANUAL 2.6 says so."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = tryout_settings(tmp_path)
    give(settings, d.store, "250108", "price_small")
    shutil.copy(madeup_out / "MU14_conf.pdf", settings.watched_folder / "MU14_conf.pdf")
    runner_pass(settings, d.store)
    found = hand_list.confirmations(settings.cases_root / "prepared", NOW)
    assert sorted((item["po_number"], item.get("tryout", False), item["set_status"]) for item in found) == [("250108", True, True),
                                                                                                          ("250114", False, True)]
    for language, mark_text in (("nl", "(proefdocument)"), ("en", "(try-out document)")):
        lines = hand_list.confirmation_lines(found, settings.cases_root, language)
        assert [line.endswith(f" {mark_text}") for line in lines] == [item.get("tryout", False) for item in found]
        assert sum(mark_text in line for line in lines) == 1
    manual = Path("aid/MANUAL.md").read_text(encoding="utf-8")
    assert 'The confirmation of a try-out (section 3.17) carries "(proefdocument)" behind its line' in manual


def test_a_file_that_is_not_there_is_not_read_again(tmp_path, madeup_out, monkeypatch):
    """Ruling Y29 kept cheap: only a file that is there but cannot be read for a moment is read again after the pause. A case id
    without a case folder, such as that of a price list, whose rows stand on the list for good, and a document that is not there,
    give "no try-out" at once, without a pause, so that the list does not slow down with every price list imported. A price list's
    change then stands on the list as before."""
    from aid.tryout_mark import case_is_tryout, is_tryout

    pauses = []
    monkeypatch.setattr(aid.tryout_mark, "sleep", pauses.append, raising=False)
    assert not case_is_tryout(tmp_path / "cases", "pricelist_S08_20261118T120000") and not is_tryout(tmp_path / "gone.pdf")
    assert pauses == []
    d = deps(tmp_path, madeup_out, settings=OPEN)
    writer = PreparedWriter(d.settings.cases_root / "prepared")
    op = WriteOp(kind="article_price", po_number="", version=None, article_code="KM-0801", old_value="12.50", new_value="12.70")
    writer.apply(op, "pricelist_S08_20261118T120000", "PL1", {"supplier": "S08"}, NOW)                # as the price-list import writes
    assert [(change.case_ids, change.tryout) for change in open_changes(writer.out_dir)] == [(["pricelist_S08_20261118T120000"], False)]
    assert pauses == []


# --- Task 43: a try-out is made from the order as the aid compares it (amendment 4) ---

FILED = datetime(2026, 11, 1, 9, 0)                  # when the store filed the order: before every try-out and every tick
TICKED = NOW + timedelta(hours=1)                    # when the owner ticked a try-out's change as entered in the test Exact


def tried(folder: Path, pdf: bytes) -> list[dict]:
    """The lines of a try-out as the parser reads them back."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "tryout.pdf"
    path.write_bytes(pdf)
    return lines_of(read_back(path))


def test_a_second_try_out_is_made_from_the_order_with_the_entered_price(tmp_path, madeup_out):
    """The owner's walk with two try-outs of one order in the test mode. The first has the price of the first line 1 percent up
    (12.63); the rules take it over, and the owner enters it in the test Exact and ticks it. The next try-out is made from the
    order as a try-out's case is compared with it, the entered price laid over the version: with "Niets" it prints 12.63 and its
    case closes without a price item; with the price 1 percent up it prints one percent above the entered price, 12.76, and the
    rules prepare the change from 12.63. The supplier's own confirmation of that order is compared with the order as printed:
    nothing entered for a try-out reaches it."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = tryout_settings(tmp_path / "walk")
    prepared = settings.cases_root / "prepared"
    filed_on(d.store, "250108", "v1", FILED)
    po = made_from(d.store, "250108")
    give(settings, d.store, "250108", "price_small")
    runner_pass(settings, d.store)
    [first] = results(settings)
    assert [(f.field, f.po_value.value, f.doc_value.value) for f in first.findings.flags] == [("price", "12.50", "12.63")]
    [asked] = [change for change in open_changes(prepared) if change.kind == "po_line_price"]
    assert asked.tryout and mark(prepared, asked.ids, "entered", TICKED) == 1
    assert tried(tmp_path / "none", checked_confirmation(po, "none", NOW, settings, d.store))[0]["unit_price"] == "12.63"
    assert tried(tmp_path / "up", checked_confirmation(po, "price_small", NOW, settings, d.store))[0]["unit_price"] == "12.76"
    assert tried(tmp_path / "far up", checked_confirmation(po, "price_large", NOW, settings, d.store))[0]["unit_price"] == "13.89"
    assert po.document.lines[0].unit_price.value == "12.50" and made_from(d.store, "250108") == po         # the store holds the print
    give(settings, d.store, "250108", "none", moment=NOW + timedelta(minutes=5))
    runner_pass(settings, d.store)
    second = results(settings)[-1]
    assert (second.status, second.findings.flags, second.review_items) == ("closed", [], [])
    record = load_case(settings.cases_root, second.case_id).read_step("entered")
    assert record == {"version": 1, "replaced": [{"line_id": "1", "field": "price", "was": "12.50", "entered": "12.63",
                                                  "marked_at": TICKED.isoformat(), "by": "buyer"}]}
    give(settings, d.store, "250108", "price_small", moment=NOW + timedelta(minutes=10))
    runner_pass(settings, d.store)
    third = results(settings)[-1]
    assert [(f.field, f.po_value.value, f.doc_value.value) for f in third.findings.flags] == [("price", "12.63", "12.76")]
    assert [(e.op.kind, e.op.old_value, e.op.new_value) for e in third.write_entries[:1]] == [("po_line_price", "12.63", "12.76")]
    assert [(change.old_value, change.new_value, change.tryout) for change in open_changes(prepared) if change.kind == "po_line_price"] == [
        ("12.63", "12.76", True)]
    shutil.copy(madeup_out / "MU08_conf.pdf", settings.watched_folder / "MU08_conf.pdf")       # the supplier's own confirmation: 12.70
    runner_pass(settings, d.store)
    own = results(settings)[-1]
    assert [(f.field, f.po_value.value, f.doc_value.value) for f in own.findings.flags] == [("price", "12.50", "12.70")]
    assert not (settings.cases_root / own.case_id / "steps" / "03_entered.json").exists()


def test_a_try_out_follows_the_change_marked_last_and_is_not_made_while_the_list_cannot_be_read(tmp_path, madeup_out, monkeypatch):
    """In a case made from a try-out the change marked last decides for a line, so a try-out is made from that value: the supplier's
    own change, entered after the try-out's, is the price a try-out with "Niets" prints. A version filed after the ticks is printed
    as it is. While the list cannot be read, no try-out is made: the error is raised, so that the page says that it did not work,
    and nothing is written."""
    import pathlib

    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = tryout_settings(tmp_path / "walk")
    prepared = settings.cases_root / "prepared"
    filed_on(d.store, "250108", "v1", FILED)
    po = made_from(d.store, "250108")
    give(settings, d.store, "250108", "price_small")
    shutil.copy(madeup_out / "MU08_conf.pdf", settings.watched_folder / "MU08_conf.pdf")
    runner_pass(settings, d.store)
    made_up, own = sorted((change for change in open_changes(prepared) if change.kind == "po_line_price"), key=lambda change: not change.tryout)
    assert (made_up.new_value, made_up.tryout, own.new_value, own.tryout) == ("12.63", True, "12.70", False)
    mark(prepared, made_up.ids, "entered", TICKED)
    mark(prepared, own.ids, "entered", TICKED + timedelta(minutes=5))
    assert tried(tmp_path / "none", checked_confirmation(po, "none", NOW, settings, d.store))[0]["unit_price"] == "12.70"
    real = pathlib.Path.read_bytes

    def read_bytes(self):
        if self.name == "marks.jsonl":
            raise PermissionError(13, "The process cannot access the file because it is being used by another process", str(self))
        return real(self)
    before = sorted(path for path in tmp_path.rglob("*") if path.is_file())
    with monkeypatch.context() as held:
        held.setattr(pathlib.Path, "read_bytes", read_bytes)
        with pytest.raises(PermissionError):
            checked_confirmation(po, "none", NOW, settings, d.store)
    assert sorted(path for path in tmp_path.rglob("*") if path.is_file()) == before
    # the review round of Task 43, W9: an export filed after the ticks shows the old 12.50. The try-out is made from the order as
    # its case will be compared: the list is brought in line with that export first, which ends the supplier's own tick (the
    # try-out's is left), so the try-out prints the try-out's 12.63 and not the 12.70 that no longer counts
    export = tmp_path / "export_after.csv"
    export.write_text("po_number,line_id,item_code,description,quantity,unit,unit_price,price_basis,delivery_date\n"
                      "250108,1,KM-0801,Manometer 0-10 bar,3,st,12.50,1,2026-11-16\n250108,2,KM-0802,Zeskantbout M10 x 40,50,st,0.42,1,2026-11-16\n",
                      encoding="utf-8")
    assert d.store.from_export(export, {name: name for name in export.read_text(encoding="utf-8").splitlines()[0].split(",")})
    filed_on(d.store, "250108", "e1", TICKED + timedelta(hours=1))
    assert tried(tmp_path / "ended", checked_confirmation(po, "none", NOW, settings, d.store))[0]["unit_price"] == "12.63"
    assert [change.state for change in changes(prepared)[0] if change.kind == "po_line_price" and not change.tryout] == ["open"]
    filed_on(d.store, "250108", "v1", TICKED + timedelta(hours=2))                # had the order been filed after the ticks
    assert tried(tmp_path / "later", checked_confirmation(po, "none", NOW, settings, d.store))[0]["unit_price"] == "12.50"
# --- the try-out cart (Task 41, rule 6 and amendments 3 and 4) ---

CART_HEADING = "Proefwinkel (verzonnen pagina)"
SHOP_URL = "http://127.0.0.1:7870/tryout/250108_price_small.html"      # the address the extension sends with the try-out cart of 250108
READ_BACK = ("item_code", "quantity", "unit", "unit_price", "price_basis")   # what the cart reader gives back of each line


def cart_of(folder: Path, html: str, name: str = "cart.html"):
    """The page as the cart reader reads it."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(html, encoding="utf-8")
    return read_cart_html(folder / name)


def capture_deps(settings: Settings, store: PoStore, writer=None) -> Deps:
    """The Deps of the capture server on these settings and this store: the cart reader behind the parser, no decider, no mailer."""
    return Deps(settings=settings, store=store, reader=read_with_parser, decider=NoDecider(),
                writer=writer or PreparedWriter(settings.cases_root / "prepared"), mailer=None, clock=lambda: NOW)


def test_the_try_out_cart_reads_back_and_a_higher_price_gives_a_difference(tmp_path, madeup_out):
    """Rule 6: a try-out cart says in its heading that it is made up, states the number of its order as the made-up carts do, and holds
    the lines of the PO as the store holds it with at most the one change. Read back by the cart reader it gives the PO's item codes,
    quantities, units, prices and price bases; with "price_small" the price of the first line is 1 percent higher (12.50 becomes
    12.63), which the comparison finds as the one difference. write_cart writes the page as <cases_root>/tryout/<PO>_<change>.html."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    po = d.store.as_it_stands("250108")
    assert CART_CHANGES == ("none", "price_small") and set(CART_CHANGES) < set(CHANGES)
    for change, price in (("none", "12.50"), ("price_small", "12.63")):
        html = cart_html(po, change, NOW.date())
        cart = cart_of(tmp_path, html)
        assert (cart.supplier.value, cart.po_reference.value, cart.capture_date.value, cart.currency.value) == (
            CART_HEADING, "250108", "2026-11-12", "EUR")
        expected = [{field: getattr(line, field).value for field in READ_BACK} for line in po.document.lines]
        assert expected[0]["unit_price"] == "12.50"
        expected[0]["unit_price"] = price
        assert [{field: getattr(line, field).value for field in READ_BACK} for line in cart.lines] == expected, change
        assert not any(getattr(line, field).uncertain for line in cart.lines for field in READ_BACK)
        findings = compare(match_lines(cart, po.document, NoDecider()), cart, po.document, "cart")
        assert [(flag.field, flag.po_line_ids, flag.po_value.value, flag.doc_value.value) for flag in findings.flags] == (
            [] if change == "none" else [("price", ["1"], "12.50", "12.63")]), change
        assert findings.uncompared == []
        assert f"<h1>{CART_HEADING}</h1>" in html and "<p>Uw referentie: 250108</p>" in html and f"<p>{NOTE_ROW}</p>" in html
        assert checked_cart(po, change, NOW, OPEN.model_copy(update={"cases_root": tmp_path / "cases"}), d.store) == html
        name = write_cart(tmp_path / "cases", po, change, NOW.date())
        assert name == f"250108_{change}.html" and (tmp_path / "cases" / "tryout" / name).read_text(encoding="utf-8") == html
    assert sorted(path.name for path in (tmp_path / "cases" / "tryout").iterdir()) == ["250108_none.html", "250108_price_small.html"]
    assert not (tmp_path / "cases" / "tmp").exists()                           # the read-back's own folder is gone again
    assert cart_html(po, "none", NOW.date()) != cart_html(po, "none", NOW.date() + timedelta(days=1))    # the day stands on the page
    assert cart_link("250108_none.html") == "http://127.0.0.1:7870/tryout/250108_none.html"
    assert cart_link("IB 26#1_none.html") == "http://127.0.0.1:7870/tryout/IB%2026%231_none.html"
    with pytest.raises(ValueError):
        cart_html(po, "price_large", NOW.date())                                # no change of a try-out cart


def test_a_try_out_cart_prints_each_value_as_the_order_holds_it(tmp_path, madeup_out):
    """Rule 6: the lines of the PO as the store holds it. A quantity in the thousands, a price of three decimals, a price basis of 100,
    a unit that is no piece, a description with a mark of HTML in it, a line without a unit and one without a price basis read back
    as the order holds them, each cell only when the line states the value; a line with a discount or a net price prints the price
    the comparison compares. A PO known from an export states no currency, and nor does its try-out cart."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    po = d.store.as_it_stands("250108")
    lines = with_line(with_line(po, 0, quantity="1000", unit_price="0.125", price_basis="100", unit="doos", description="Slang <10 mm> & koppeling"),
                      1, unit=None, price_basis=None)
    cart = cart_of(tmp_path, cart_html(lines, "price_small", NOW.date()))
    first, second = cart.lines[0], cart.lines[1]
    assert [getattr(first, field).value for field in READ_BACK] == [po.document.lines[0].item_code.value, "1000", "doos", "0.126", "100"]
    assert first.description.value == "Slang <10 mm> & koppeling" and (second.unit.value, second.price_basis.value) == (None, None)
    findings = compare(match_lines(cart, lines.document, NoDecider()), cart, lines.document, "cart")
    assert [(flag.field, flag.doc_value.value) for flag in findings.flags] == [("price", "0.126")]
    priced = with_line(with_line(po, 0, unit_price="10.00", discount_pct="5"), 1, net_price="0.40")
    cart = cart_of(tmp_path, cart_html(priced, "price_small", NOW.date()))
    assert [line.unit_price.value for line in cart.lines[:2]] == ["9.60", "0.40"]
    export = tmp_path / "rows.csv"
    export.write_text(EXPORT_TITLES + "250150,1,KM-0801,Manometer 0-10 bar,3,st,12.50,1,2026-11-16\n"
                      "250150,2,KM-0802,Pakking,5,st,2.00,1,2026-11-16\n", encoding="utf-8")
    [look] = d.store.from_export(export, EXPORT_COLUMNS)
    page = cart_html(look, "none", NOW.date())
    assert "Valuta" not in page and cart_of(tmp_path, page).currency.value is None and "Valuta: EUR" in cart_html(po, "none", NOW.date())


def test_a_try_out_cart_is_read_back_before_it_leaves_the_page(tmp_path, madeup_out, monkeypatch):
    """Amendment 4 (Task 33's ruling Y1 for a cart): before the page offers the link, the aid reads the cart page back with the cart
    reader and compares it with the order as the store holds it, which is what the pre-check will compare the cart with. It is
    refused, with the reasons of a try-out and nothing written, when the order has no lines or no price to raise ("no_lines",
    "no_price"), when the page would read back as anything but the order with the chosen change, or would not be known as a try-out
    ("read_back"), and when the pre-check, asked at the moment of making, would not find its order or would only guess it
    ("not_found"): an order whose number the cart reader does not read from the page, unless it is the one order filed that day
    that holds exactly the cart's items and is not sent."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = OPEN.model_copy(update={"cases_root": tmp_path / "cases"})
    po = d.store.as_it_stands("250108")
    today = NOW.date()

    def reason(order: PoVersion, change: str = "none", store: PoStore = d.store, now: datetime = NOW) -> str:
        return refused(order, change, lambda: checked_cart(order, change, now, settings, store))
    empty = po.model_copy(update={"document": po.document.model_copy(update={"lines": []})})
    assert reason(empty) == "no_lines" and refused(empty, "none", lambda: cart_html(empty, "none", today)) == "no_lines"
    unpriced = po.model_copy(update={"document": po.document.model_copy(update={"lines": [
        line.model_copy(update={"unit_price": DocValue(), "net_price": DocValue()}) for line in po.document.lines]})})
    assert reason(unpriced, "price_small") == "no_price"
    # a reader that misreads a unit, and a page the reader cannot read
    real = aid.tryout.read_cart_html

    def misreading(path: Path):
        doc = real(path)
        line = doc.lines[1].model_copy(update={"unit": DocValue(value="m", text="m")})
        return doc.model_copy(update={"lines": [doc.lines[0], line, *doc.lines[2:]]})

    def unreadable(path: Path):
        raise ReaderError(f"no cart table in {path.name}")
    for reader in (misreading, unreadable):
        monkeypatch.setattr(aid.tryout, "read_cart_html", reader)
        for change in CART_CHANGES:
            assert reason(po, change) == "read_back", (reader.__name__, change)
    monkeypatch.undo()
    # the pre-check compares with the order as the store holds it: a try-out of other lines, or of a currency the cart reader does
    # not know, would show more than the chosen change
    assert reason(with_line(po, 1, quantity="7")) == "read_back" and reason(with_header(po, currency="CHF")) == "read_back"
    assert reason(with_line(po, 0, item_code="Totaal", quantity=None)) == "read_back"       # a row the cart reader takes for a total
    # an order of another number form: the cart reader does not read its number from the page
    store = PoStore(tmp_path / "store2")
    store.add(ANDERS_PO, ANDERS_LAYOUT)                                          # known from a PDF: by its items it is a guess
    anders = store.as_it_stands("IB-2600417")
    assert cart_of(tmp_path, cart_html(anders, "none", today)).po_reference.value is None
    assert reason(anders, "none", store) == "not_found"
    export = tmp_path / "anders.csv"
    export.write_text(EXPORT_TITLES + "IB-2600501,1,KM-0801,Manometer 0-10 bar,3,st,12.50,1,2026-11-16\n"
                      "IB-2600501,2,KM-0802,Pakking,5,st,2.00,1,2026-11-16\n", encoding="utf-8")
    [look] = store.from_export(export, EXPORT_COLUMNS)
    filed_on(store, "IB-2600501", "e1", NOW - timedelta(hours=1))                # filed today, the one order with exactly these items: found
    assert cart_order(cart_of(tmp_path, cart_html(look, "none", today)), store, settings.cases_root, NOW) == ("IB-2600501", BY_ITEMS)
    assert checked_cart(look, "price_small", NOW, settings, store)
    assert reason(look, "price_small", store, NOW + timedelta(days=1)) == "not_found"       # made the day after: a guess by then
    assert cart_order(cart_of(tmp_path, cart_html(look, "none", today)), store, settings.cases_root, NOW + timedelta(days=1)) == (
        "IB-2600501", NOT_SURE)
    twin = tmp_path / "twin.csv"
    twin.write_text(export.read_text(encoding="utf-8").replace("IB-2600501", "IB-2600502"), encoding="utf-8")
    store.from_export(twin, EXPORT_COLUMNS)                                      # a second such order of the day: a guess
    filed_on(store, "IB-2600502", "e1", NOW - timedelta(minutes=30))
    assert reason(look, "price_small", store) == "not_found"
    # a page the aid would not know as a try-out is never given
    monkeypatch.setattr(aid.tryout, "is_tryout", lambda path: False)
    assert reason(po) == "read_back"
    monkeypatch.undo()
    assert checked_cart(po, "price_small", NOW, settings, d.store)
    assert not (tmp_path / "cases").exists()                                     # nothing written, the read-back's folder gone
    assert set(REASONS) >= {"no_lines", "no_price", "read_back", "not_found"}


def test_a_try_out_cart_holds_no_markup_of_its_order(tmp_path, madeup_out):
    """The review's finding 15: every value of the order is printed as text. An item code and a description that hold a script tag
    and a closing td, a description with an image tag, a number and a currency with a tag in them: the page holds each of them
    escaped and holds no such tag, its table has the six cells of each line and no more, and the cart reader reads the values back
    as the order holds them."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    po = d.store.as_it_stands("250108")
    code, description = "</td><script>window.a=1</script>", "Slang </td><td><script>window.b=1</script> 10 mm"
    hostile = with_line(with_line(po, 0, item_code=code, description=description), 1, description="Koppeling <img src=x onerror=window.c=1>")
    hostile = with_header(hostile, currency="EUR</p><script>window.d=1</script>").model_copy(update={"po_number": "250108</p><script>e"})
    page = cart_html(hostile, "none", NOW.date())
    rows = len(po.document.lines)
    assert "<script" not in page and "<img" not in page and page.count("</script>") == 0
    assert (page.count("<td>"), page.count("</td>"), page.count("<tr>"), page.count("<p>")) == (6 * rows, 6 * rows, rows + 1, 4)
    for printed in ("<td>&lt;/td&gt;&lt;script&gt;window.a=1&lt;/script&gt;</td>",
                    "<td>Slang &lt;/td&gt;&lt;td&gt;&lt;script&gt;window.b=1&lt;/script&gt; 10 mm</td>",
                    "<td>Koppeling &lt;img src=x onerror=window.c=1&gt;</td>", "<p>Uw referentie: 250108&lt;/p&gt;&lt;script&gt;e</p>",
                    "<p>Valuta: EUR&lt;/p&gt;&lt;script&gt;window.d=1&lt;/script&gt;</p>"):
        assert printed in page, printed
    cart = cart_of(tmp_path, page)
    assert (cart.lines[0].item_code.text, cart.lines[0].description.value) == (code, description) and len(cart.lines) == rows
    assert cart.lines[1].description.value == "Koppeling <img src=x onerror=window.c=1>"


def test_a_try_out_cart_that_the_pre_check_would_check_against_another_order_is_refused(tmp_path, madeup_out):
    """The review's finding 16, with its two stores: the read-back refuses a cart when the pre-check would compare it with another
    order than the one it is made of, also when that other order would be found for sure. An order of another number form, known
    from a PDF of an earlier day, beside an order of today with the same items: the cart of the first would be checked against the
    second. An order whose number goes on after six digits, beside the order of the first six: the cart reader reads no number
    from its page, so the items decide, and its cart is given only while it is the one order of the day with those items."""
    settings = OPEN.model_copy(update={"cases_root": tmp_path / "cases"})
    today = NOW.date()

    def rows(number: str, po: PoVersion) -> str:
        """An export of the order `number` with the lines of the PO."""
        held = io.StringIO()
        written = csv.writer(held, lineterminator="\n")
        written.writerow(EXPORT_TITLES.strip().split(","))
        for line in po.document.lines:
            written.writerow([number, line.line_id, line.item_code.value, line.description.value, line.quantity.value, "st",
                              line.unit_price.value, line.price_basis.value or "1", line.delivery_date.value or "2026-11-20"])
        return held.getvalue()
    # the first store: IB-2600417 as a PDF version, and IB-2600418 from an export with the same items
    store = PoStore(tmp_path / "store1")
    version = store.add(ANDERS_PO, ANDERS_LAYOUT).version
    filed_on(store, "IB-2600417", f"v{version}", NOW - timedelta(days=1))
    anders = store.as_it_stands("IB-2600417")
    (tmp_path / "next.csv").write_text(rows("IB-2600418", anders), encoding="utf-8")
    store.from_export(tmp_path / "next.csv", EXPORT_COLUMNS)
    filed_on(store, "IB-2600418", "e1", NOW - timedelta(hours=1))
    cart = cart_of(tmp_path, cart_html(anders, "none", today))
    assert cart.po_reference.value is None and cart_order(cart, store, settings.cases_root, NOW) == ("IB-2600418", BY_ITEMS)
    assert refused(anders, "none", lambda: checked_cart(anders, "none", NOW, settings, store)) == "not_found"
    assert refused(anders, "price_small", lambda: checked_cart(anders, "price_small", NOW, settings, store)) == "not_found"
    assert checked_cart(store.as_it_stands("IB-2600418"), "none", NOW, settings, store)      # the order of today: its own cart is given
    # the second store: 250108 as a PDF version, and 250108-01 from an export with the same lines
    d = deps(tmp_path / "second", madeup_out, settings=OPEN)
    po = d.store.as_it_stands("250108")
    filed_on(d.store, "250108", "v1", NOW - timedelta(days=1))
    (tmp_path / "follow.csv").write_text(rows("250108-01", po), encoding="utf-8")
    [follow] = d.store.from_export(tmp_path / "follow.csv", EXPORT_COLUMNS)
    filed_on(d.store, "250108-01", "e1", NOW - timedelta(hours=1))
    page = cart_html(follow, "none", today)
    assert "<p>Uw referentie: 250108-01</p>" in page and cart_of(tmp_path, page).po_reference.value is None      # no part of it is read
    assert checked_cart(follow, "none", NOW, settings, d.store) == page          # the one order of today with these items: found
    answer = precheck(page, SHOP_URL, capture_deps(settings, d.store), settings)
    assert (answer.po_number, answer.verdict) == ("250108-01", "match")
    assert answer.message == "Winkelwagen komt overeen met inkooporder 250108-01 (gekozen op de artikelen in de winkelwagen)"
    assert checked_cart(po, "none", NOW, settings, d.store)                      # the cart of 250108 states its number: found by it
    filed_on(d.store, "250108", "v1", NOW - timedelta(hours=2))                  # both filed today: the aid cannot tell them apart
    assert refused(follow, "none", lambda: checked_cart(follow, "none", NOW, settings, d.store)) == "not_found"


def test_the_case_of_a_try_out_cart_takes_its_orders_supplier_and_never_the_pages_heading(tmp_path, madeup_out):
    """The review's findings 14 and 26: the heading of a try-out cart names the try-out shop, which is no supplier. The case takes
    the supplier of its order, and has none when the order names none, as an order known from an export without a supplier column
    does: the digest line and the list name no supplier then. With "Eerste zaken van een leverancier" above 0 such a case always
    comes to the buyer, since no supplier's cases can be counted for it; at 0 the rules settle it. A webshop's own cart still takes
    the name its page prints when its order names no supplier."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    export = tmp_path / "rows.csv"
    export.write_text(EXPORT_TITLES + "250150,1,KM-0801,Manometer 0-10 bar,3,st,12.50,1,2026-11-16\n"
                      "250150,2,KM-0802,Pakking,5,st,2.00,1,2026-11-16\n", encoding="utf-8")
    [look] = d.store.from_export(export, EXPORT_COLUMNS)
    assert look.supplier is None
    settings = tryout_settings(tmp_path, po_store_root=d.store.root)
    html = checked_cart(look, "price_small", NOW, settings, d.store)
    answer = precheck(html, SHOP_URL, capture_deps(settings, d.store), settings)
    assert (answer.verdict, answer.status, answer.po_number) == ("match", "closed", "250150")
    [r] = results(settings)
    assert r.supplier is None and r.digest_line == "voorcontrole PO 250150: afgesloten, 2 voorbereide wijzigingen"
    assert load_case(settings.cases_root, answer.case_id).read_step("route")["context"]["supplier"] == ""
    listed = sorted(hand_list.row_text(change, settings.cases_root, "nl") for change in changes(settings.cases_root / "prepared")[0])
    assert listed == ["artikel KM-0801: inkoopprijs onbekend → 12,63 (proefdocument)", "regel 1 (KM-0801): prijs 12,50 → 12,63 (proefdocument)"]
    assert CART_HEADING not in (settings.cases_root / answer.case_id / "steps" / "08_result.json").read_text(encoding="utf-8")
    real = precheck(html.replace(CART_HEADING, "S08"), "https://shop.example.com/cart", capture_deps(settings, d.store), settings)
    assert next(r for r in results(settings) if r.case_id == real.case_id).supplier == "S08"       # a webshop's cart: the page's name
    # with first cases above 0: the try-out cart of an order without a supplier always comes to the buyer
    first = tryout_settings(tmp_path / "first", po_store_root=d.store.root, first_cases_review=1)
    for change in CART_CHANGES:
        tried = precheck(checked_cart(look, change, NOW, first, d.store), SHOP_URL, capture_deps(first, d.store), first)
        assert (tried.verdict, tried.status) == ("differences", "review"), change
        assert "eerste zaken van deze leverancier gaan naar de inkoper" in [entry["text"] for entry in tried.lines], change
        case = load_case(first.cases_root, tried.case_id)
        for item in next(r for r in results(first) if r.case_id == tried.case_id).review_items:
            decide(case, item.item_id, "confirm")
        assert close_case(case, capture_deps(first, d.store)).status == "closed", change     # closed, and still no supplier's case
    # an order without a supplier that holds no line either gives a case without a supplier too: the cart without an order
    lost = precheck(html.replace("KM-0801", "XX-0001").replace("KM-0802", "XX-0002").replace("250150", "259999"), SHOP_URL,
                    capture_deps(settings, d.store), settings)
    assert lost.verdict == "no_po" and next(r for r in results(settings) if r.case_id == lost.case_id).supplier is None


def test_the_case_of_a_try_out_confirmation_of_an_order_without_a_supplier_keeps_the_printed_name(tmp_path, madeup_out):
    """The other side of the rule of a try-out cart's supplier (the check's mutant R1): only the heading of a try-out cart is
    never taken for a supplier. A try-out confirmation is treated as any confirmation: the case of one whose order names no
    supplier, an order known from an export without a supplier column, takes the name the reader read from the document. The
    parser reads none from a try-out's first row, so the case has none; a reader that takes that row for the supplier's name, as
    a model may, gives the case that name, as before this task."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    export = tmp_path / "rows.csv"
    export.write_text(EXPORT_TITLES + "250150,1,KM-0801,Manometer 0-10 bar,3,st,12.50,1,2026-11-16\n"
                      "250150,2,KM-0802,Pakking,5,st,2.00,1,2026-11-16\n", encoding="utf-8")
    [look] = d.store.from_export(export, EXPORT_COLUMNS)
    assert look.supplier is None

    def naming(path: Path, kind: str):
        doc = read_with_parser(path, kind)
        return doc.model_copy(update={"supplier": DocValue(value=SUPPLIER_ROW, text=SUPPLIER_ROW)})
    for reader, name in ((read_with_parser, None), (naming, SUPPLIER_ROW)):
        settings = tryout_settings(tmp_path / ("named" if name else "unnamed"), po_store_root=d.store.root)
        give(settings, d.store, "250150", "none")
        runner_pass(settings, d.store, reader=reader)
        [result] = results(settings)
        case = load_case(settings.cases_root, result.case_id)
        assert case.kind == "confirmation" and tryout_case(case) and case.po_number == "250150"
        assert result.supplier == name and case.read_step("route")["context"]["supplier"] == (name or "")
    assert SUPPLIER_ROW == "Proefleverancier (verzonnen document)"


def test_a_case_made_from_a_try_out_cart_has_every_guard_of_a_try_out(tmp_path, madeup_out):
    """Amendment 3: a try-out cart is a try-out for every guard of Task 33. aid/tryout_mark.py knows a capture case made from it by the
    first heading of its page, "Proefwinkel (verzonnen pagina)". Its order is found by the number on the page, never guessed, also
    for an order that was sent already. It writes through the prepared writer only, whatever the Deps' writer is, and its changes
    stand at a place of their own on the list "In Exact invoeren" with "(proefdocument)": a real cart with the same lines, checked the
    same day, is a case of its own, written through the system's writer or listed beside the try-out's change."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = tryout_settings(tmp_path, po_store_root=d.store.root, capture_token="made-up-token-for-the-tests")
    po = d.store.as_it_stands("250108")
    assert d.store.versions("250108")
    assert cart_order(cart_of(tmp_path, cart_html(po, "none", NOW.date())), d.store, settings.cases_root, NOW) == ("250108", None)
    html = checked_cart(po, "price_small", NOW, settings, d.store)
    system = SystemWriter()
    chain = RetryingWriter(system, PreparedWriter(settings.cases_root / "prepared"))
    answer = precheck(html, SHOP_URL, capture_deps(settings, d.store, chain), settings)
    assert (answer.verdict, answer.status, answer.po_number) == ("match", "closed", "250108")
    assert answer.message == "Winkelwagen komt overeen met inkooporder 250108; 1 regel op de inkooporder voorbereid"
    case = load_case(settings.cases_root, answer.case_id)
    assert tryout_case(case) and aid.tryout_mark.case_is_tryout(settings.cases_root, answer.case_id) and case.po_found_by is None
    assert aid.tryout_mark.is_tryout(case.input_files[0]) and aid.tryout_mark.CART_HEADING == CART_HEADING
    [r] = results(settings)
    assert [(entry.op.kind, entry.status) for entry in r.write_entries] == [("po_line_price", "prepared"), ("article_price", "prepared")]
    assert system.asked == [] and r.supplier == "S08"                           # the case takes the PO's supplier, not the page's heading
    # a real cart with the same lines, the same day: a case of its own, not answered from the try-out's
    real = html.replace(CART_HEADING, "S08")
    own = precheck(real, "https://shop.example.com/cart", capture_deps(settings, d.store, chain), settings)
    assert own.case_id != answer.case_id and not tryout_case(load_case(settings.cases_root, own.case_id))
    assert {case_id for case_id, _ in system.asked} == {own.case_id} and own.message == (
        "Winkelwagen komt overeen met inkooporder 250108; 1 regel op de inkooporder bijgewerkt")
    assert precheck(html, SHOP_URL, capture_deps(settings, d.store, chain), settings).case_id == answer.case_id       # each its own case
    again = html.replace("<body>", "<body><!-- again -->")                      # the same try-out cart in a page of other bytes
    assert precheck(again, SHOP_URL, capture_deps(settings, d.store, chain), settings).case_id == answer.case_id
    # both prepared: the try-out's change and the real one stand side by side on the list, the try-out's marked
    settings = tryout_settings(tmp_path / "list", po_store_root=d.store.root)
    for page in (html, real):
        assert precheck(page, SHOP_URL, capture_deps(settings, d.store), settings).verdict == "match"
    prepared = settings.cases_root / "prepared"
    listed = sorted((change.state, change.tryout, hand_list.row_text(change, settings.cases_root, "nl")) for change in changes(prepared)[0])
    assert listed == [("open", False, "artikel KM-0801: inkoopprijs onbekend → 12,63"), ("open", False, "regel 1 (KM-0801): prijs 12,50 → 12,63"),
                      ("open", True, "artikel KM-0801: inkoopprijs onbekend → 12,63 (proefdocument)"),
                      ("open", True, "regel 1 (KM-0801): prijs 12,50 → 12,63 (proefdocument)")]


def test_a_try_out_cart_page_is_known_by_its_bytes_and_its_first_heading(tmp_path, madeup_out, monkeypatch):
    """Amendment 3: aid/tryout_mark.py knows a try-out cart by the page alone. A page saved as HTML is one when its bytes hold the
    heading as the aid writes it and its first heading is that text, also in the copy of the page that a browser sends (other bytes
    around it, .htm). A webshop's page, which does not hold the text, is none, and is not even read as a page: a buyer's cases hold
    many of them, and each is asked once. A page that holds the text elsewhere, or as a later heading, is none either, and nor is a
    page whose bytes hold the text nowhere in one piece."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    page = cart_html(d.store.as_it_stands("250108"), "none", NOW.date())
    read = []
    monkeypatch.setattr(aid.tryout_mark, "first_heading", lambda path: read.append(path.name) or first_heading(path))

    def asked(name: str, html: str) -> bool:
        (tmp_path / name).write_text(html, encoding="utf-8")
        return aid.tryout_mark.is_tryout(tmp_path / name)
    assert asked("tryout.html", page) and asked("copy.htm", page.replace("<body>", '<body><div id="other">Welkom</div>'))
    assert not asked("shop.html", (madeup_out / "CART02.html").read_text(encoding="utf-8")) and read == ["tryout.html", "copy.htm"]
    assert not asked("renamed.html", page.replace(CART_HEADING, "S08"))
    assert not asked("later.html", page.replace("<body>", "<body><h1>S08</h1>"))               # a later heading
    assert not asked("text.html", page.replace(f"<h1>{CART_HEADING}</h1>", f"<h1>S08</h1><p>{CART_HEADING}</p>"))    # no heading at all
    split = page.replace(f"<h1>{CART_HEADING}</h1>", "<h1>Proefwinkel <b>(verzonnen pagina)</b></h1>")
    assert asked("split.html", split)                                           # its title holds the text, and its heading reads as it
    assert not asked("untitled.html", split.replace(f"<title>{CART_HEADING}</title>", "<title>Winkel</title>"))     # the text nowhere
    assert read == ["tryout.html", "copy.htm", "later.html", "text.html", "split.html"]     # only a page that holds the text is read
    assert not asked("page.txt", page) and not aid.tryout_mark.is_tryout(tmp_path / "missing.html")
    assert aid.tryout_mark.is_tryout(tmp_path / "tryout.html") and len(read) == 5            # asked again: the answer is kept


def test_the_digest_marks_a_try_out_cart_and_it_is_no_suppliers_first_case(tmp_path, madeup_out):
    """Amendment 3: the digest marks the case of a try-out cart with "(proefdocument)" and leaves its changes out of the count of the
    changes to enter in Exact, and the case is no supplier's first case: with "Eerste zaken van een leverancier" at 1, a try-out cart
    of S08's order that closed leaves S08's own cart the supplier's first case, which comes to the buyer."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = tryout_settings(tmp_path, po_store_root=d.store.root, digest="per_case")
    po = d.store.as_it_stands("250108")
    html = checked_cart(po, "price_small", NOW, settings, d.store)
    assert precheck(html, SHOP_URL, capture_deps(settings, d.store), settings).verdict == "match"
    runner_pass(settings, d.store)
    [text] = digest_texts(settings)
    lines = text.splitlines()
    assert lines[:2] == ["Dagoverzicht 12-11-2026", "Behandelde zaken"]         # no line of changes to enter: the try-out's are left out
    start = lines.index("- voorcontrole PO 250108 (S08): afgesloten, 2 voorbereide wijzigingen (proefdocument)")
    assert lines[start + 1:start + 3] == ["    - prijs regel 1: 12,50 → 12,63 (proefdocument)",
                                          "    - artikelprijs KM-0801: onbekend → 12,63 (proefdocument)"]
    # no supplier's first case
    first = tryout_settings(tmp_path / "first", po_store_root=d.store.root, first_cases_review=1)
    tried = precheck(html, SHOP_URL, capture_deps(first, d.store), first)
    assert (tried.verdict, tried.status) == ("differences", "review")           # S08 is new: the try-out comes to the buyer (R11)
    case = load_case(first.cases_root, tried.case_id)
    for item in results(first)[0].review_items:
        decide(case, item.item_id, "confirm")
    assert close_case(case, capture_deps(first, d.store)).status == "closed"
    own = precheck(html.replace(CART_HEADING, "S08"), "https://shop.example.com/cart", capture_deps(first, d.store), first)
    assert (own.verdict, own.status) == ("differences", "review")
    assert "eerste zaken van deze leverancier gaan naar de inkoper" in [entry["text"] for entry in own.lines]


# --- Task 43 meets Task 41: a try-out cart is made from the order as a try-out's case is compared with it ---

def test_a_try_out_cart_starts_from_what_was_entered_for_an_earlier_try_out(tmp_path, madeup_out):
    """The first round's concern 12, settled at the merge. A try-out raised the price of the first line to 12,63; the rules took it
    over, and the owner entered it in the test Exact and ticked it. A case made from a try-out is compared with the order with that
    value laid over it, so the next try-out cart is made from that order: with "Niets" its first line prints 12.63 and read back
    it is the compared order without a change, and with the price 1 percent up it prints 12.76. Before, the cart printed the 12.50
    of the order as filed, and its own case found a difference that no change of the cart had made. The store keeps the print."""
    d = deps(tmp_path, madeup_out, settings=OPEN)
    settings = tryout_settings(tmp_path / "walk")
    prepared = settings.cases_root / "prepared"
    filed_on(d.store, "250108", "v1", FILED)
    po = d.store.as_it_stands("250108")
    assert cart_of(tmp_path, checked_cart(po, "none", NOW, settings, d.store)).lines[0].unit_price.value == "12.50"       # nothing entered yet
    give(settings, d.store, "250108", "price_small")
    runner_pass(settings, d.store)
    [asked] = [change for change in open_changes(prepared) if change.kind == "po_line_price"]
    assert asked.tryout and mark(prepared, asked.ids, "entered", TICKED) == 1
    for change, price in (("none", "12.63"), ("price_small", "12.76")):
        page = checked_cart(po, change, NOW, settings, d.store)
        assert cart_of(tmp_path, page).lines[0].unit_price.value == price, change
    assert d.store.as_it_stands("250108") == po and po.document.lines[0].unit_price.value == "12.50"
