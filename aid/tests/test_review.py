"""Tests of the review page (aid/review): the cases that wait for the buyer, one case as the page shows it, the buyer's decisions, the
close and the undo, the strings of the page, the construction of the page and its command, on the made-up set."""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from email import message_from_bytes, policy
from pathlib import Path
from typing import get_args

import pytest

from aid.case import load_case
from aid.comparison import Detail, Findings, Flag, Reason
from aid.execution.writer import PreparedWriter, RetryingWriter, UnknownWrite, UnverifiedWrite, WriteLog, WriteLogEntry, WriterError
from aid.messaging import FileMailer
from aid.pipeline import CaseResult, ReviewItem, escalate_pending, run_case
from aid.po_store import PoStore
from aid.readers.pdf_text import render_page
from aid.review.__main__ import company_values
from aid.review.__main__ import main as review_main
from aid.review.app import (CaseSummary, FlagRow, build_app, close_case, decide, import_gradio, list_open_cases, load_case_view, page_rows,
                            undo)
from aid.review.i18n import STRINGS
from aid.review.page import _article_items, _decision_choices, close_text, undo_text
from aid.schema import DocValue
from aid.settings import load_settings
from aid.sources.upload import open_upload_case
from aid.tests.conftest import REPO_ROOT, SETTINGS, deps, read_csv


def test_decide_and_close_writes(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out); c = open_upload_case(d.settings.cases_root, [madeup_out / "MU07_conf.pdf"], "250107", "confirmation", d.clock()); r = run_case(c, d)
    view = load_case_view(c); assert view.flags[0].field == "price" and view.reapproval_banner is True
    decide(c, view.flags[0].item_id, "confirm", article_update=True)
    r2 = close_case(c, d); assert r2.status == "closed" and {e.op.kind for e in r2.write_entries} >= {"po_line_price", "article_price"}
    assert read_csv(c.root / "dispositions.csv")[-1]["decision"].startswith("prepared")
    assert undo(c, r2.write_entries[0].entry_id, d).undo_of == r2.write_entries[0].entry_id and list_open_cases(d.settings.cases_root) == []

def test_i18n_keys_match():
    assert set(STRINGS["nl"]) == set(STRINGS["en"]) and STRINGS["nl"]["close"] == "Zaak afsluiten"

def test_build_app_constructs(tmp_path, madeup_out):
    gr = import_gradio(); assert isinstance(build_app(deps(tmp_path, madeup_out)), gr.Blocks)


# The tests below cover what the rulings of the task and the answers to its questions add to the three above.

NOW = datetime(2026, 11, 12, 9, 0)                   # the clock of deps()
LATER = NOW + timedelta(minutes=1)                   # for a second case in one test, which needs a case id of its own
SUPPLIER = "sales@s07.example.com"                   # a made-up address of the made-up supplier S07
PNG = b"\x89PNG\r\n\x1a\n"
NEW_SUPPLIER = SETTINGS.model_copy(update={"first_cases_review": 5})      # the default: the first five cases of a supplier are reviewed
CODE = re.compile(r"\b[RB]\d\d\b|_")                 # a rule code, or a name of the code such as po_line_price


def run(d, madeup_out, case: str, po_number: str, now: datetime = NOW):
    """Open the made-up case `case` as an upload of its confirmation and run it; returns the case and its result."""
    c = open_upload_case(d.settings.cases_root, [madeup_out / f"{case}_conf.pdf"], po_number, "confirmation", now)
    return c, run_case(c, d)


def mailing(d, out):
    """The dependencies d with the file mailer into out, at the clock of deps(), and S07's made-up address."""
    settings = d.settings.model_copy(update={"supplier_emails": {"S07": SUPPLIER}})
    return d.model_copy(update={"settings": settings, "mailer": FileMailer(out, sender="aid@example.com", clock=lambda: NOW)})


def rows(c) -> list[tuple[str, str, str, str]]:
    """The line, field, decision and source of each row of the case's disposition log."""
    return [(row["line_id"], row["field"], row["decision"], row["source"]) for row in read_csv(c.root / "dispositions.csv")]


def audit(c) -> dict:
    return json.loads((c.root / "audit.json").read_text(encoding="utf-8"))


def read_eml(path):
    return message_from_bytes(path.read_bytes(), policy=policy.default)


def cart_case(d, madeup_out, document: str, po_number: str):
    """A cart case of the made-up page `document` with a saved result in review by hand: one quantity flag, an item for the buyer. The
    parser of this branch reads no cart, so the result is written as the pipeline would write it."""
    c = open_upload_case(d.settings.cases_root, [madeup_out / document], po_number, "cart", NOW)
    flag = Flag(flag_id="F1", field="quantity", po_line_ids=["1"], doc_line_ids=["1"], po_value=DocValue(value="2", text="2"),
                doc_value=DocValue(value="3", text="3"), detail="different")
    item = ReviewItem(item_id="RI1", finding_id="F1", reason="cart: for the buyer", field="quantity", po_value=flag.po_value, doc_value=flag.doc_value)
    c.write_step(8, "result", CaseResult(case_id=c.case_id, status="review", version=None,
                                         findings=Findings(version=None, flags=[flag], matches=[], uncompared=[]), actions=[], write_entries=[],
                                         messages=[], review_items=[item], digest_line=f"PO {po_number}: ter beoordeling, geen schrijfacties"))
    return c


class FailingAt:
    """Prepares each change as PreparedWriter does, and raises WriterError at the n-th change."""
    name = "failing"

    def __init__(self, out_dir, n: int):
        self.prepared, self.n, self.count = PreparedWriter(out_dir), n, 0

    def apply(self, op, case_id, rule_id, evidence, now):
        self.count += 1
        if self.count == self.n:
            raise WriterError("the ERP refused the change")
        return self.prepared.apply(op, case_id, rule_id, evidence, now)


class Down:
    """A write route that is down: every change raises WriterError."""
    name = "down"

    def apply(self, op, case_id, rule_id, evidence, now):
        raise WriterError("503 service unavailable")


# --- the view ---

def test_the_view_of_a_case(tmp_path, madeup_out):
    """MU07: the pages of the confirmation and of the selected PO version as PNG images at 100 dpi; the price flag as the row of its
    review item, with its values, sources and reason; the matches numbered M1, M2, ... with their value on both sides; nothing
    uncompared; the last decision of each item; and the banner, as the PO is above the authorization."""
    d = deps(tmp_path, madeup_out); c, r = run(d, madeup_out, "MU07", "250107")
    view = load_case_view(c)
    assert view.summary == CaseSummary(case_id=c.case_id, po_number="250107", supplier="S07", status="review", opened_at=NOW.isoformat(), items=1)
    po_pdf = Path(c.read_step("po_versions")["versions"][0]["pdf_path"])
    assert view.doc_pages == [render_page(c.input_files[0], 1, dpi=100)] and view.po_pages == [render_page(po_pdf, 1, dpi=100)]
    assert view.doc_pages[0].startswith(PNG) and view.po_pages[0].startswith(PNG)
    assert view.flags == [FlagRow(item_id="RI1", field="price", line="1", po_value="12.50", doc_value="14.00",
                                  po_source="p1 r8: 1 KM-0701 Klephuis DN25 3 st 12,50 1 16-11-2026",
                                  doc_source="p1 r8: 1 KM-0701 Ventilgehäuse DN25 3 Stk 14,00 1 16.11.2026", thumbnail=None,
                                  reason="price above threshold")]
    assert [(m.item_id, m.field, m.line, m.po_value, m.doc_value, m.po_source, m.doc_source, m.reason) for m in view.matches[:5]] == [
        ("M1", "quantity", "1", "3", "3", "", "", ""), ("M2", "unit", "1", "pc", "pc", "", "", ""),
        ("M3", "delivery_date", "1", "2026-11-16", "2026-11-16", "", "", ""), ("M4", "line_presence", "1", "matched", "matched", "", "", ""),
        ("M5", "price", "2", "55.00", "55.00", "", "", "")]
    assert len(view.matches) == 10 and (view.matches[-1].field, view.matches[-1].line, view.matches[-1].po_value) == ("required_info", "", "FCA Rotterdam")
    assert view.uncompared == [] and view.decisions == {} and view.reapproval_banner is True
    decide(c, "RI1", "keep"); decide(c, "RI1", "confirm", article_update=True)
    assert load_case_view(c).decisions == {"RI1": "confirm"}                        # the last decision of an item counts


def test_every_review_item_has_a_row(tmp_path, madeup_out):
    """MU12 for a new supplier: the unit flag and the two uncompared items are review items, so their rows carry the items' ids and
    reason. MU01 for a new supplier has no finding: its one item gets a row in `flags`, without values."""
    d = deps(tmp_path, madeup_out, settings=NEW_SUPPLIER)
    c, r = run(d, madeup_out, "MU12", "250112")
    view = load_case_view(c)
    assert [(row.item_id, row.field, row.line, row.po_value, row.doc_value, row.reason) for row in view.flags] == [
        ("RI1", "unit", "1", "m", "pc", "new supplier")]
    assert [(row.item_id, row.field, row.line, row.po_value, row.doc_value, row.reason) for row in view.uncompared] == [
        ("RI2", "price", "1", "", "", "new supplier"), ("RI3", "quantity", "1", "", "", "new supplier")]
    c, r = run(d, madeup_out, "MU01", "250101")
    assert load_case_view(c).flags == [FlagRow(item_id="RI1", field="", line="", po_value="", doc_value="", po_source="", doc_source="",
                                               thumbnail=None, reason="new supplier")]


def test_a_cart_shows_its_png_and_an_html_cart_no_page(tmp_path, madeup_out):
    """A PNG cart is its own one page and an HTML cart has none; neither has PO pages without a stored version, nor a banner."""
    d = deps(tmp_path, madeup_out)
    png, html = cart_case(d, madeup_out, "CART02.png", "250202"), cart_case(d, madeup_out, "CART01.html", "250201")
    view = load_case_view(png)
    assert view.doc_pages == [(madeup_out / "CART02.png").read_bytes()] and view.po_pages == [] and view.reapproval_banner is False
    assert load_case_view(html).doc_pages == [] and [row.item_id for row in view.flags] == ["RI1"]


def test_the_open_cases_and_the_saved_result_after_a_close(tmp_path, madeup_out):
    """The cases in review are listed, oldest first, with their PO, supplier and number of items; a case without a result and a
    closed case are not. After a close the saved result is the one returned, which list_open_cases reads back as closed; its record
    is in step 09 and in the audit record."""
    d = deps(tmp_path, madeup_out)
    later, _ = run(d, madeup_out, "MU07", "250107", now=LATER)
    c, r = run(d, madeup_out, "MU07", "250107")
    run(d, madeup_out, "MU01", "250101")                                              # closed by the pipeline
    open_upload_case(d.settings.cases_root, [madeup_out / "MU08_conf.pdf"], "250108", "confirmation", NOW)      # not run
    assert list_open_cases(d.settings.cases_root) == [
        CaseSummary(case_id=c.case_id, po_number="250107", supplier="S07", status="review", opened_at=NOW.isoformat(), items=1),
        CaseSummary(case_id=later.case_id, po_number="250107", supplier="S07", status="review", opened_at=LATER.isoformat(), items=1)]
    decide(c, "RI1", "keep")
    r2 = close_case(c, d)
    assert [s.case_id for s in list_open_cases(d.settings.cases_root)] == [later.case_id] and CaseResult.model_validate(c.read_step("result")) == r2
    assert r2.review_items == r.review_items and r2.digest_line == "PO 250107 (S07): afgesloten, 2 voorbereide wijzigingen"
    records = c.read_step("close")["records"]
    assert [(record["action"], record["status"], record["carried_out"]["RI1"]["decision"]) for record in records] == [("close", "closed", "keep")]
    assert audit(c)["close"] == records and audit(c)["case_id"] == c.case_id      # added to the run's record


# --- the decisions ---

def test_decide_records_the_decision_and_refuses_what_it_cannot_record(tmp_path, madeup_out):
    """A decision is one JSON line; an unknown decision, an unknown item, a question about an item without a finding and a question
    about a cart are refused with a plain ValueError and record nothing."""
    d = deps(tmp_path, madeup_out); c, r = run(d, madeup_out, "MU07", "250107")
    with pytest.raises(ValueError, match="unknown decision"):
        decide(c, "RI1", "approve")
    with pytest.raises(ValueError, match="no review item"):
        decide(c, "RI9", "keep")
    assert not (c.root / "decisions.jsonl").exists()
    decide(c, "RI1", "confirm", article_update=True)
    [line] = (c.root / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    assert (record["item_id"], record["decision"], record["article_update"]) == ("RI1", "confirm", True) and set(record) == {"item_id", "decision", "article_update", "at"}
    assert datetime.fromisoformat(record["at"]).microsecond == 0 and len(record["at"]) == 19
    whole, _ = run(deps(tmp_path, madeup_out, settings=NEW_SUPPLIER), madeup_out, "MU01", "250101")
    with pytest.raises(ValueError, match="no question"):
        decide(whole, "RI1", "ask")
    cart = cart_case(d, madeup_out, "CART02.png", "250202")
    with pytest.raises(ValueError, match="cart"):
        decide(cart, "RI1", "ask")
    assert not (whole.root / "decisions.jsonl").exists() and not (cart.root / "decisions.jsonl").exists()


# --- the close ---

def test_a_case_not_in_review_is_refused(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    closed, r = run(d, madeup_out, "MU01", "250101")
    with pytest.raises(ValueError, match="not in review"):
        close_case(closed, d)
    not_run = open_upload_case(d.settings.cases_root, [madeup_out / "MU07_conf.pdf"], "250107", "confirmation", NOW)
    with pytest.raises(ValueError, match="no result"):
        close_case(not_run, d)
    assert WriteLog(closed.root).entries() == r.write_entries and not (not_run.root / "steps" / "09_close.json").exists()


def test_keep_closes_the_case_without_a_change(tmp_path, madeup_out):
    """MU07 kept: no change to the PO line, so no request for re-approval; the case closes, the document attached and the status set
    as R01 makes them, under the buyer's rule."""
    d = deps(tmp_path, madeup_out); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "keep")
    r2 = close_case(c, d)
    assert r2.status == "closed" and r2.messages == [] and r2.error is None
    assert [(e.op.kind, e.rule_id, e.op.document, e.op.old_value, e.op.new_value) for e in r2.write_entries] == [
        ("attach_document", "B01", str(c.input_files[0]), None, None), ("set_status", "B01", None, "Besteld", "Bevestigd")]
    assert rows(c) == [("1", "price", "accept the PO value (B01)", "page 1, line 8"), ("", "attach_document", "prepared (B01)", "B01"),
                       ("", "set_status", "prepared (B01)", "B01")]


def test_ask_sends_the_question_and_the_case_waits(tmp_path, madeup_out):
    """MU07 asked: a price has no template of its own, so the question is missing_info, to S07's address with the buyer in copy, its
    table showing the PO line's item code and description. It is recorded as sent under the buyer's rule, and the case waits."""
    out = tmp_path / "outbox"
    d = mailing(deps(tmp_path, madeup_out), out); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "ask")
    r2 = close_case(c, d)
    [eml] = out.glob("*.eml")
    mail = read_eml(eml)
    assert (mail["To"], mail["Cc"], mail["X-Aid-Template"], mail["X-Aid-Case"]) == (SUPPLIER, SETTINGS.buyer_email, "missing_info", c.case_id)
    assert all(text in mail.get_content() for text in ("KM-0701", "Klephuis DN25", "12.50", "14.00"))
    [message] = r2.messages
    assert (message["rule_id"], message["template"], message["status"], message["finding_ids"], message["message_id"]) == (
        "B01", "missing_info", "sent", ["F1"], eml.stem)
    assert message["items"] == [{"po_line_ids": "1", "doc_line_ids": "1", "field": "price", "po_value": "12.50", "doc_value": "14.00"}]
    assert [json.loads(line)["message_id"] for line in (c.root / "messages" / "sent.jsonl").read_text(encoding="utf-8").splitlines()] == [eml.stem]
    assert r2.status == "waiting" and r2.write_entries == [] and list_open_cases(d.settings.cases_root) == []
    assert rows(c) == [("1", "price", "ask the supplier (B01)", "page 1, line 8")]


def test_unresolved_keeps_the_case_in_review_and_a_second_close_repeats_nothing(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "unresolved")
    r2 = close_case(c, d)
    assert r2.status == "review" and r2.write_entries == [] and [s.case_id for s in list_open_cases(d.settings.cases_root)] == [c.case_id]
    assert close_case(c, d) == r2 and rows(c) == [("1", "price", "unresolved (B01)", "page 1, line 8")]
    assert [record["carried_out"] for record in c.read_step("close")["records"]][1] == {}


def test_a_writer_error_at_the_second_write_leaves_the_case_in_review(tmp_path, madeup_out):
    """MU07 confirmed with the article update, through a writer that refuses the second change: the PO line's price is made and
    logged, the article price is not, and the case stays in review with the error, which names the write; the request for re-approval
    lists the change made. With the writer back, the next close makes the article price, not the PO line's price again, and closes."""
    d = deps(tmp_path, madeup_out); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "confirm", article_update=True)
    r2 = close_case(c, d.model_copy(update={"writer": FailingAt(tmp_path, 2)}))
    assert r2.status == "review" and r2.error == "WriterError: the ERP refused the change (write: rule B01, article_price, article KM-0701)"
    assert [e.op.kind for e in WriteLog(c.root).entries()] == ["po_line_price"] == [e.op.kind for e in r2.write_entries]
    assert CaseResult.model_validate(c.read_step("result")) == r2 and [s.case_id for s in list_open_cases(d.settings.cases_root)] == [c.case_id]
    assert [(m["template"], m["status"], [item["kind"] for item in m["items"]]) for m in r2.messages] == [
        ("reapproval", "not sent (no mailer)", ["po_line_price"])]
    assert rows(c) == [("1", "price", "prepared (B01)", "page 1, line 8")]
    [record] = c.read_step("close")["records"]
    assert record["error"] == r2.error and record["stop"]["write"]["kind"] == "article_price" and record["stop"]["waiting"] is False
    r3 = close_case(c, d)
    assert r3.status == "closed" and r3.error is None and r3.messages == r2.messages            # no PO line changed this time
    assert [e.op.kind for e in r3.write_entries] == ["po_line_price", "article_price", "attach_document", "set_status"] == [
        e.op.kind for e in WriteLog(c.root).entries()]
    assert rows(c)[1:] == [("", "article_price", "prepared (B01)", "page 1, line 8"), ("", "attach_document", "prepared (B01)", "B01"),
                           ("", "set_status", "prepared (B01)", "B01")]


def test_the_disposition_rows_of_a_close(tmp_path, madeup_out):
    """MU12 for a new supplier: the decision rows come first, in the order of the items, with the words of protocol v2 (section 6.4)
    and the buyer's rule, then the writes; an item carried out by an earlier close gets no second row."""
    d = deps(tmp_path, madeup_out, settings=NEW_SUPPLIER); c, r = run(d, madeup_out, "MU12", "250112")
    decide(c, "RI1", "confirm"); decide(c, "RI2", "keep"); decide(c, "RI3", "unresolved")
    r2 = close_case(c, d)
    assert r2.status == "review" and r2.write_entries == []
    assert rows(c) == [("1", "unit", "accept the supplier's value (B01)", "page 1, line 8"), ("1", "price", "accept the PO value (B01)", "B01"),
                       ("1", "quantity", "unresolved (B01)", "B01")]
    decide(c, "RI3", "keep")
    assert close_case(c, d).status == "closed"
    assert rows(c)[3:] == [("1", "quantity", "accept the PO value (B01)", "B01"), ("", "attach_document", "prepared (B01)", "B01"),
                           ("", "set_status", "prepared (B01)", "B01")]
    lines = (c.root / "dispositions.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == (REPO_ROOT / "evaluation" / "templates" / "dispositions.csv").read_text(encoding="utf-8").strip()
    assert read_csv(c.root / "dispositions.csv")[0] == {
        "case_id": c.case_id, "version_selected": "1", "line_id": "1", "field": "unit", "decision": "accept the supplier's value (B01)",
        "source": "page 1, line 8", "note": f"folder {c.case_id}; rule B01; finding F1; item RI1"}


def test_two_items_on_one_finding_write_it_once(tmp_path, madeup_out):
    """MU08 while the write route is down: the price is prepared after the route failed (RI1), and the article price (RI2, on the same
    flag) and R01's attachment and status (RI3, RI4, without a finding) are not applied. With the route back, the buyer confirms the
    price twice, once with the article update: the price is written once and the article price once, the items without a finding are
    acknowledged, and the case closes with the document attached."""
    c, r = run(deps(tmp_path, madeup_out, writer=RetryingWriter(Down(), PreparedWriter(tmp_path))), madeup_out, "MU08", "250108")
    assert [(row.item_id, row.field, row.line, row.po_value, row.doc_value) for row in load_case_view(c).flags] == [
        ("RI1", "price", "1", "12.50", "12.70"), ("RI2", "price", "1", "12.50", "12.70"), ("RI3", "", "", "", ""), ("RI4", "", "", "", "")]
    decide(c, "RI1", "confirm"); decide(c, "RI2", "confirm", article_update=True); decide(c, "RI3", "confirm"); decide(c, "RI4", "keep")
    r2 = close_case(c, deps(tmp_path, madeup_out))
    assert [(e.op.kind, e.rule_id, e.status) for e in r2.write_entries[len(r.write_entries):]] == [
        ("po_line_price", "B01", "prepared"), ("article_price", "B01", "prepared"), ("attach_document", "B01", "prepared"), ("set_status", "B01", "prepared")]
    assert r2.status == "closed" and r2.error is None
    assert [row[2] for row in rows(c)] == ["accept (B01)", "accept (B01)"] + ["prepared (B01)"] * 4


def test_a_cart_closes_without_attaching(tmp_path, madeup_out):
    """A cart's quantity confirmed: the PO line's quantity is written, and no document is attached and no status set."""
    d = deps(tmp_path, madeup_out); c = cart_case(d, madeup_out, "CART02.png", "250202")
    decide(c, "RI1", "confirm")
    r2 = close_case(c, d)
    assert r2.status == "closed" and [(e.op.kind, e.op.old_value, e.op.new_value) for e in r2.write_entries] == [("po_line_quantity", "2", "3")]


def test_the_reapproval_lists_the_changes_of_the_close(tmp_path, madeup_out):
    """MU07 confirmed with the article update, above the authorization: the request to approve the PO again goes to the approver with
    the two changes, and names neither the PO's total nor the limit."""
    out = tmp_path / "outbox"
    d = mailing(deps(tmp_path, madeup_out), out); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "confirm", article_update=True)
    r2 = close_case(c, d)
    [eml] = out.glob("*.eml")
    mail = read_eml(eml)
    assert (mail["To"], mail["Cc"], mail["X-Aid-Template"], mail["X-Aid-Case"]) == (SETTINGS.approver_email, SETTINGS.buyer_email, "reapproval", c.case_id)
    assert "- Prijs van de orderregel, regel 1: oude waarde 12.50, nieuwe waarde 14.00" in mail.get_content()
    assert "- Artikelprijs, artikel KM-0701: oude waarde onbekend, nieuwe waarde 14.00" in mail.get_content()
    assert [(m["rule_id"], m["template"], m["status"], m["finding_ids"], [i["kind"] for i in m["items"]]) for m in r2.messages] == [
        ("B01", "reapproval", "sent", ["F1"], ["po_line_price", "article_price"])]
    text = mail.get_content() + "".join(f"{name}: {value}" for name, value in mail.items())
    total = d.store.versions("250107")[0].total
    assert str(total) not in text and str(d.settings.authorization_limit) not in text and r2.status == "closed"


def test_after_the_wait_an_item_left_on_ask_keeps_the_case_in_review(tmp_path, madeup_out):
    """MU07 asked on Thursday 12 November at 9:00 and not answered: after three working days escalate_pending puts the case back in
    review with an item of its own. The buyer acknowledges that item and leaves the price on "ask": the question is not sent again,
    and the case stays in review (not waiting, which no later escalation would end), until the buyer decides the price otherwise."""
    out = tmp_path / "outbox"
    d = mailing(deps(tmp_path, madeup_out), out); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "ask")
    assert close_case(c, d).status == "waiting"
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 17, 9, 1), d.settings) == [(c.case_id, "no answer in 3 working days")]
    decide(c, "RI2", "keep")
    assert close_case(c, d).status == "review" and len(list(out.glob("*.eml"))) == 1
    decide(c, "RI1", "keep")
    assert close_case(c, d).status == "closed"


def test_an_undo_is_recorded(tmp_path, madeup_out):
    """The undo of the buyer's price change restores the PO value through the writer, and is recorded in step 09 and in the audit
    record; an entry undone already is refused (None) and records nothing more."""
    d = deps(tmp_path, madeup_out); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "confirm")
    entry = close_case(c, d).write_entries[0]
    made = undo(c, entry.entry_id, d)
    assert (made.op.kind, made.op.old_value, made.op.new_value, made.undo_of, made.rule_id) == ("po_line_price", "14.00", "12.50", entry.entry_id, "undo")
    assert undo(c, entry.entry_id, d) is None
    records = c.read_step("close")["records"]
    assert [record["action"] for record in records] == ["close", "undo"] and records[1]["undo"]["entry_id"] == made.entry_id
    assert audit(c)["close"] == records


# --- the page ---

def test_the_page_shows_labels_and_no_code(tmp_path, madeup_out):
    """The rows of the lists as the page shows them: fields, reasons and the kinds of matched pairs by their labels, in Dutch and in
    English, never as their text, which may carry a rule code ("rule R04, article_price"). Each reason the routing and the pipeline
    give, and each detail and reason of the comparison, has a label of its own in both languages."""
    c, r = run(deps(tmp_path, madeup_out, writer=RetryingWriter(Down(), PreparedWriter(tmp_path))), madeup_out, "MU08", "250108")
    view = load_case_view(c)
    shown = page_rows(view.flags, "nl")
    assert shown[0] == ["RI1", "Prijs", "1", "12.50", "12.70", "Voorbereide wijziging: met de hand doorvoeren",
                        view.flags[0].po_source, view.flags[0].doc_source]
    assert [row[5] for row in shown[1:]] == ["Niet uitgevoerd: een eerdere schrijfactie is mislukt"] * 3
    assert page_rows(view.matches, "en")[3][1:5] == ["Line present", "1", "present", "present"]
    reasons = [*get_args(Detail), *get_args(Reason), "new supplier", "supplier marked for review", "no version selected",
               "split line: parts differ", "no delivery date on the document", "no lead time known for the supplier",
               "delivery date beyond the lead time plus the tolerance", "no price on the document", "quantity unknown for the price rule",
               "price above threshold", "PO total unknown", "cart: for the buyer", "messages off for this supplier", "could not be placed", "not covered by a rule",
               "prepared change: apply by hand", "write failed: the line is locked", "not applied: an earlier write failed (rule R01, set_status)",
               "write made, not verified: check in Exact", "write outcome not known: check in Exact",
               "not applied: an earlier change waits for a look in Exact (rule R01, set_status)",
               "no address for the supplier", "message not sent", "message bounced", "no answer in 3 working days",
               "supplier answered: read the reply", "no question is open"]
    for language in ("nl", "en"):
        labels = [row[5] for row in page_rows([FlagRow(item_id="RI1", field="price", line="1", po_value="", doc_value="", po_source="",
                                                       doc_source="", thumbnail=None, reason=reason) for reason in reasons], language)]
        assert STRINGS[language]["reason:other"] not in labels and not [label for label in labels if CODE.search(label) or "3" in label]
        assert len(set(labels)) == len(reasons)                       # one label of its own per reason
    assert page_rows([FlagRow(item_id="RI1", field="price", line="", po_value="", doc_value="", po_source="", doc_source="", thumbnail=None,
                              reason="a reason not known to the page (rule R04)")], "nl")[0][5] == STRINGS["nl"]["reason:other"]


def test_the_page_lists_the_open_cases_and_makes_no_network_call(tmp_path, madeup_out, monkeypatch):
    """The page, built with an open case, offers that case by its PO, supplier, time of opening and number of items; building it
    connects to nothing (gradio's analytics are off) and leaves no event loop open."""
    def refuse(*args, **kwargs):
        raise OSError("no network in the tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    d = deps(tmp_path, madeup_out); c, r = run(d, madeup_out, "MU07", "250107")
    gr = import_gradio()
    app = build_app(d)
    [cases] = [block for block in app.blocks.values() if isinstance(block, gr.Dropdown) and block.label == STRINGS["nl"]["case"]]
    assert cases.choices == [("PO 250107 (S07), 2026-11-12 09:00, 1 punt", c.case_id)]
    assert not [block for block in app.blocks.values() if isinstance(block, gr.Markdown) and CODE.search(str(block.value))]


def test_share_is_refused_with_company_settings(tmp_path, capsys):
    """--share is for the made-up set only: with a settings file that holds an SMTP server the command refuses it (exit 2) before
    it builds anything. The example settings hold no company value; an authorization limit is a company value too, and a watched
    folder outside the repository is not (the made-up set may be kept in Drive)."""
    example = json.loads((REPO_ROOT / "aid" / "settings.example.json").read_text(encoding="utf-8"))
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(example | {"smtp_host": "smtp.example.com", "watched_folder": str(REPO_ROOT / "aid" / "inbox")}),
                    encoding="utf-8")
    assert review_main(["--settings", str(path), "--share"]) == 2
    assert "internal use only" in capsys.readouterr().err and not (tmp_path / "cases").exists()
    assert company_values(load_settings(REPO_ROOT / "aid" / "settings.example.json")) == []
    assert company_values(load_settings(path)) == ["smtp_host is set"]
    assert company_values(SETTINGS) == ["authorization_limit is set"]                  # the made-up test limit of conftest
    assert company_values(SETTINGS.model_copy(update={"authorization_limit": None, "watched_folder": tmp_path})) == []


# --- review round 1 ---

class Unreachable:
    """A mailer whose every send fails with OSError, as a mailbox that cannot be reached."""
    name = "unreachable"

    def send(self, msg):
        raise OSError("no route to the mailbox")


class Refusing:
    """A write route that refuses one kind of change and makes the others (status "done")."""
    name = "refusing"

    def __init__(self, kind: str):
        self.kind = kind

    def apply(self, op, case_id, rule_id, evidence, now):
        if op.kind == self.kind:
            raise WriterError("503 service unavailable")
        return WriteLogEntry(entry_id=uuid.uuid4().hex, case_id=case_id, op=op, status="done", previous=op.old_value, new=op.new_value,
                             rule_id=rule_id, evidence=evidence, timestamp=now.isoformat())


def test_a_reapproval_the_mailer_cannot_send_is_sent_again_by_the_next_close(tmp_path, madeup_out):
    """MU07 confirmed above the authorization while the mailbox cannot be reached: the request is recorded "not sent (OSError)",
    the case gets a review item "message not sent" without a finding and stays in review, and the page says so. The next close, with
    the mailbox back, sends one request listing the earlier changes, writes nothing again and closes the case."""
    out = tmp_path / "outbox"
    d = mailing(deps(tmp_path, madeup_out), out); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "confirm", article_update=True)
    text, still_open = close_text(c, d.model_copy(update={"mailer": Unreachable()}), "nl")
    r2 = CaseResult.model_validate(c.read_step("result"))
    assert still_open and r2.status == "review" and STRINGS["nl"]["message_not_sent"] in text and "OSError" not in text
    assert [(m["template"], m["status"]) for m in r2.messages] == [("reapproval", "not sent (OSError)")]
    assert [(i.item_id, i.finding_id, i.reason) for i in r2.review_items] == [("RI1", "F1", "price above threshold"), ("RI2", None, "message not sent")]
    assert ("RI2", "Bericht niet verzonden") in [(row[0], row[5]) for row in page_rows(load_case_view(c).flags, "nl")]
    r3 = close_case(c, d)
    [eml] = out.glob("*.eml")
    content = read_eml(eml).get_content()
    assert "- Prijs van de orderregel, regel 1: oude waarde 12.50, nieuwe waarde 14.00" in content and "- Artikelprijs, artikel KM-0701" in content
    assert r3.status == "closed" and [e.op.kind for e in r3.write_entries] == ["po_line_price", "article_price", "attach_document", "set_status"]
    assert [(m["template"], m["status"]) for m in r3.messages] == [("reapproval", "not sent (OSError)"), ("reapproval", "sent")]
    assert c.read_step("close")["records"][-1]["reapproval_pending"] is None


def test_a_question_the_mailer_cannot_send_keeps_the_case_in_review(tmp_path, madeup_out):
    """The question of an "ask" that the mailer cannot send is recorded as not sent and leaves the case in review, its item not
    carried out; the next close, with the mailbox back, sends it and the case waits."""
    out = tmp_path / "outbox"
    d = mailing(deps(tmp_path, madeup_out), out); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "ask")
    r2 = close_case(c, d.model_copy(update={"mailer": Unreachable()}))
    assert r2.status == "review" and [(m["template"], m["status"]) for m in r2.messages] == [("missing_info", "not sent (OSError)")]
    assert not (c.root / "messages" / "sent.jsonl").exists() and not (c.root / "dispositions.csv").read_text(encoding="utf-8").splitlines()[1:]
    assert c.read_step("close")["records"][0]["messages"][0]["error"] == "OSError: no route to the mailbox"
    assert close_case(c, d).status == "waiting" and len(list(out.glob("*.eml"))) == 1
    assert rows(c) == [("1", "price", "ask the supplier (B01)", "page 1, line 8")]


def test_a_question_to_a_supplier_without_an_address_keeps_the_case_in_review(tmp_path, madeup_out):
    out = tmp_path / "outbox"
    d = mailing(deps(tmp_path, madeup_out), out); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "ask")
    r2 = close_case(c, d.model_copy(update={"settings": d.settings.model_copy(update={"supplier_emails": {}})}))
    assert r2.status == "review" and [m["status"] for m in r2.messages] == ["not sent (no address for the supplier)"] and not out.exists()


def test_the_page_names_a_refused_write_and_a_failed_undo_by_a_label(tmp_path, madeup_out):
    """The writer's text (which may name an endpoint or a folder) stays in the result and the records; the page shows a fixed label."""
    d = deps(tmp_path, madeup_out); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "confirm", article_update=True)
    text, still_open = close_text(c, d.model_copy(update={"writer": FailingAt(tmp_path, 2)}), "en")
    assert still_open and STRINGS["en"]["write_refused"] in text and "Article price, article KM-0701" in text
    assert "ERP" not in text and "B01" not in text and "the ERP refused" in c.read_step("result")["error"]
    entry = WriteLog(c.root).entries()[0]
    text = undo_text(c, entry.entry_id, d.model_copy(update={"writer": Down()}), "nl")
    assert text == STRINGS["nl"]["undo_failed"] and "503" not in text


def test_a_second_item_whose_write_was_made_gets_its_row(tmp_path, madeup_out):
    """MU08 with the route down: RI2, on the same flag as RI1 and confirmed without the article update, writes nothing that RI1 did
    not write, and gets its decision row all the same."""
    c, r = run(deps(tmp_path, madeup_out, writer=RetryingWriter(Down(), PreparedWriter(tmp_path))), madeup_out, "MU08", "250108")
    decide(c, "RI1", "confirm"); decide(c, "RI2", "confirm")
    close_case(c, deps(tmp_path, madeup_out))
    assert rows(c) == [("1", "price", "accept the supplier's value (B01)", "page 1, line 8"), ("1", "price", "prepared (B01)", "page 1, line 8")]


def test_a_damaged_decisions_file_is_not_read_around(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out); c, r = run(d, madeup_out, "MU07", "250107")
    (c.root / "decisions.jsonl").write_text('{"item_id": "RI1", "decis', encoding="utf-8")
    with pytest.raises(ValueError, match=r"decisions.jsonl, line 1: not a decision"):
        load_case_view(c)


def test_a_fault_while_saving_keeps_the_original_fault_as_its_cause(tmp_path, madeup_out, monkeypatch):
    class Broken:
        name = "broken"

        def apply(self, *args):
            raise RuntimeError("a fault of the writer")

    d = deps(tmp_path, madeup_out); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "confirm")

    def full(case, record):
        raise OSError("no space left on the device")

    monkeypatch.setattr("aid.review.close._add_record", full)
    with pytest.raises(OSError, match="no space") as raised:
        close_case(c, d.model_copy(update={"writer": Broken()}))
    assert isinstance(raised.value.__cause__, RuntimeError)


def test_the_article_box_is_ticked_for_a_waiting_article_price(tmp_path, madeup_out):
    """MU08 when the route refuses the article price: the review item of that waiting write is the one whose article box is ticked."""
    c, r = run(deps(tmp_path, madeup_out, writer=RetryingWriter(Refusing("article_price"), PreparedWriter(tmp_path))), madeup_out, "MU08", "250108")
    assert [(i.item_id, i.reason) for i in r.review_items][0] == ("RI1", "prepared change: apply by hand")
    assert _article_items(c) == {"RI1"}


# --- review round 2 ---

@pytest.mark.parametrize("module", ["aid.review.app", "aid.review.close", "aid.review.page", "aid.review.i18n", "aid.review.__main__"])
def test_each_module_of_the_review_page_imports_on_its_own(module):
    """In a fresh interpreter, from the repository root: close.py and page.py build on app.py, which re-exports them."""
    done = subprocess.run([sys.executable, "-B", "-c", f"import {module}"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


# --- the final review, group A ---

from types import SimpleNamespace  # noqa: E402

from aid.messaging import SentRecord  # noqa: E402
from aid.pipeline import record_dispositions  # noqa: E402
from aid.review.__main__ import refuse_share  # noqa: E402
from aid.review.page import _undo_choices, _undoable, offered_cases, selection_from_query  # noqa: E402

MADE_UP = load_settings(REPO_ROOT / "aid" / "settings.example.json")


@pytest.mark.parametrize("update, named", [
    ({"exact_base_url": "https://erp.example.com"}, "exact_base_url"),
    ({"authorization_limit": "5000"}, "authorization_limit"),
    ({"exact_endpoints": {"po_line_price": "/api/lines"}}, "exact_endpoints"),
    ({"exact_fields": {"po_line_price": "NetPrice"}}, "exact_fields"),
    ({"smtp_host": "smtp.example.com"}, "smtp_host"),
    ({"smtp_user": "aid"}, "smtp_user"),
    ({"supplier_emails": {"S01": "sales@s01.example.com", "S02": "sales@s02.example.net"}}, "supplier_emails"),
    ({"buyer_email": "buyer@example.net"}, "buyer_email"),
    ({"aid_email": "aid@example.net"}, "aid_email"),
    ({"approver_email": "approver@example.net"}, "approver_email")])
def test_refuse_share_names_each_company_value(update, named):
    """refuse_share gives the reason for each company value of the settings, and None for the made-up settings, which may keep their
    folders anywhere (in Colab they are in Drive) and their addresses on example.com and example.org."""
    settings = MADE_UP.model_validate(MADE_UP.model_dump() | update)
    reason = refuse_share(settings)
    assert reason is not None and named in reason and "internal use only" in reason
    assert "5000" not in reason and "example.net" not in reason      # names the setting, not its value


def test_refuse_share_accepts_the_made_up_settings(tmp_path):
    made_up = MADE_UP.model_copy(update={"supplier_emails": {"S01": "sales@s01.example.com", "S02": "sales@s02.example.org"},
                                         "approver_email": "approver@example.org", "watched_folder": tmp_path, "cases_root": tmp_path / "cases"})
    assert refuse_share(MADE_UP) is None and refuse_share(made_up) is None
    assert refuse_share(MADE_UP.model_copy(update={"buyer_email": "buyer@example.net"})) is not None


def test_a_layout_or_a_sender_list_refuses_the_share_link(tmp_path, capsys):
    """The layout of the PO PDF and the senders of the PO e-mails are set on the company side from the company's own documents: with
    either in the settings a public share link is refused, by the name of the setting and never by a word of the layout."""
    layout = {"po_label": "Bestelnummer", "title_row": ["Hoeveelheid", "Artikelcode"], "columns": ["quantity", "item_code"]}
    made_up = MADE_UP.model_dump()
    assert company_values(MADE_UP.model_validate(made_up | {"po_layout": layout})) == ["po_layout is set"]
    assert company_values(MADE_UP.model_validate(made_up | {"po_senders": ["inkoop@example.org"]})) == ["po_senders is not empty"]
    both = made_up | {"po_layout": layout, "po_senders": ["example.org"], "exact_base_url": "https://erp.example.com"}
    both = MADE_UP.model_validate(both)
    assert company_values(both) == ["exact_base_url is set", "po_layout is set", "po_senders is not empty"]
    reason = refuse_share(both)
    assert reason is not None and "internal use only" in reason
    assert not any(word in reason for word in ("Bestelnummer", "Hoeveelheid", "example.org"))
    assert company_values(MADE_UP) == [] and refuse_share(MADE_UP.model_copy(update={"po_layout": None, "po_senders": []})) is None
    example = json.loads((REPO_ROOT / "aid" / "settings.example.json").read_text(encoding="utf-8"))
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(example | {"po_layout": layout, "cases_root": str(tmp_path / "cases")}), encoding="utf-8")
    assert review_main(["--settings", str(path), "--share"]) == 2
    err = capsys.readouterr().err
    assert "po_layout is set" in err and "Bestelnummer" not in err and not (tmp_path / "cases").exists()


def test_saved_export_columns_refuse_the_share_link(tmp_path, capsys):
    """The column titles of the export are chosen on the company side from the company's own export (Task 39): once they are saved a
    public share link is refused, by the name of the setting and never by a title. The number convention and the tick alone are no
    company values."""
    columns = {"po_number": "Bestelnummer", "item_code": "Artikelnummer"}
    made_up = MADE_UP.model_dump()
    settings = MADE_UP.model_validate(made_up | {"export_columns": columns})
    assert company_values(settings) == ["export_columns is not empty"]
    reason = refuse_share(settings)
    assert reason is not None and "internal use only" in reason and not any(title in reason for title in columns.values())
    assert company_values(MADE_UP.model_validate(made_up | {"export_decimal_comma": True, "export_fill_down": True})) == []
    every = MADE_UP.model_validate(made_up | {"export_columns": columns, "po_senders": ["example.org"], "exact_base_url": "https://erp.example.com"})
    assert company_values(every) == ["exact_base_url is set", "po_senders is not empty", "export_columns is not empty"]
    example = json.loads((REPO_ROOT / "aid" / "settings.example.json").read_text(encoding="utf-8"))
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(example | {"export_columns": columns, "cases_root": str(tmp_path / "cases")}), encoding="utf-8")
    assert review_main(["--settings", str(path), "--share"]) == 2
    err = capsys.readouterr().err
    assert "export_columns is not empty" in err and "Bestelnummer" not in err and not (tmp_path / "cases").exists()


def test_saved_article_columns_refuse_the_share_link_and_the_kept_columns_are_no_setting():
    """The column titles of the export of articles are chosen on the company side from the company's own file (Task 42): once they
    are saved a public share link is refused, by the name of the setting and never by a title. The number convention alone is no
    company value. The columns kept for a supplier's price list are no setting (ruling Z9 of the review round): the settings refuse
    the key, so that no press of the tab Prijslijst can change the settings file."""
    made_up = MADE_UP.model_dump()
    articles = MADE_UP.model_validate(made_up | {"article_columns": {"item_code": "Artikelnummer", "price": "Inkoopprijs"}})
    assert company_values(articles) == ["article_columns is not empty"]
    reason = refuse_share(articles)
    assert reason is not None and "internal use only" in reason
    assert not any(word in reason for word in ("Artikelnummer", "Inkoopprijs"))
    assert company_values(MADE_UP.model_validate(made_up | {"article_decimal_comma": True})) == []
    assert "pricelist_columns" not in made_up and "pricelist_columns" not in (REPO_ROOT / "aid" / "settings.example.json").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        MADE_UP.model_validate(made_up | {"pricelist_columns": {"Voorbeeld BV": {"item_code": "Bestelcode", "price": "Netto"}}})


def test_share_is_refused_with_the_reason(tmp_path, capsys):
    example = json.loads((REPO_ROOT / "aid" / "settings.example.json").read_text(encoding="utf-8"))
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(example | {"smtp_host": "smtp.example.com", "cases_root": "/srv/company/cases"}), encoding="utf-8")
    assert review_main(["--settings", str(path), "--share"]) == 2
    err = capsys.readouterr().err
    assert refuse_share(load_settings(path)) in err and not (tmp_path / "cases").exists()


def done_writer():
    """A write route that makes every change (status "done")."""
    return Refusing("no such kind")


def test_the_query_selects_a_case_and_preselects_an_undo(tmp_path, madeup_out):
    """?case=<id>&undo=<entry> as the digest builds them: a case in review is selected; any case with a write made in the system
    and not undone (here a closed case and one that waits for the supplier) is offered and selected, with the write preselected
    (undone only when the buyer clicks); a write that is not undoable, a case that is not offered and an id that is no case select
    nothing."""
    d = deps(tmp_path, madeup_out, writer=done_writer())
    closed, r = run(d, madeup_out, "MU01", "250101")
    review, _ = run(d, madeup_out, "MU07", "250107", now=LATER)
    prepared, p = run(deps(tmp_path, madeup_out), madeup_out, "MU02", "250102", now=LATER + timedelta(minutes=1))
    waiting, w = run(d, madeup_out, "MU01", "250101", now=LATER + timedelta(minutes=2))
    waiting.write_step(8, "result", w.model_copy(update={"status": "waiting"}))      # as after a close that wrote and asked
    root = d.settings.cases_root
    assert r.status == "closed" and {e.status for e in r.write_entries} == {"done"} and p.status == "closed"
    first = r.write_entries[0].entry_id
    assert [s.case_id for s in offered_cases(root)] == [closed.case_id, review.case_id, waiting.case_id]
    assert selection_from_query(root, {"case": waiting.case_id, "undo": w.write_entries[1].entry_id}) == (waiting.case_id, w.write_entries[1].entry_id)
    assert selection_from_query(root, {"case": closed.case_id, "undo": first}) == (closed.case_id, first)
    assert selection_from_query(root, {"case": review.case_id}) == (review.case_id, None)
    assert selection_from_query(root, {"case": closed.case_id, "undo": "nothing"}) == (closed.case_id, None)
    assert selection_from_query(root, {"case": prepared.case_id, "undo": p.write_entries[0].entry_id}) == (None, None)
    assert selection_from_query(root, {"case": "../elsewhere"}) == (None, None) and selection_from_query(root, {}) == (None, None)
    assert WriteLog(closed.root).entries() == r.write_entries                         # nothing undone by opening the link
    for e in r.write_entries:
        undo(closed, e.entry_id, d)
    assert [s.case_id for s in offered_cases(root)] == [review.case_id, waiting.case_id]
    assert selection_from_query(root, {"case": closed.case_id}) == (None, None)


def test_an_unreadable_write_log_offers_no_undo(tmp_path, madeup_out):
    """A case in review whose write log cannot be read is still offered and selected, with no write to undo; a case not in review
    with such a log is not offered."""
    d = deps(tmp_path, madeup_out); c, r = run(d, madeup_out, "MU07", "250107")
    other, _ = run(deps(tmp_path, madeup_out, writer=done_writer()), madeup_out, "MU01", "250101", now=LATER)
    for case in (c, other):
        (case.root / "write_log.jsonl").write_text('{"entry_id": "e1", "cut', encoding="utf-8")
    root = d.settings.cases_root
    assert [s.case_id for s in offered_cases(root)] == [c.case_id]
    assert selection_from_query(root, {"case": c.case_id, "undo": "e1"}) == (c.case_id, None)


def test_a_settings_file_that_cannot_be_read_exits_2_without_its_values(tmp_path, capsys):
    """A missing settings file, or one whose authorization_limit is no amount, ends the command with exit 2 and a message that does
    not repeat the value."""
    example = json.loads((REPO_ROOT / "aid" / "settings.example.json").read_text(encoding="utf-8"))
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(example | {"authorization_limit": "not-an-amount-xyz"}), encoding="utf-8")
    assert review_main(["--settings", str(path)]) == 2
    out = capsys.readouterr()
    assert out.err and "not-an-amount-xyz" not in out.err + out.out
    assert review_main(["--settings", str(tmp_path / "missing.json")]) == 2 and capsys.readouterr().err


def test_the_review_page_refuses_a_settings_file_in_which_exact_base_url_is_set(tmp_path, capsys, monkeypatch):
    """Ruling V10: no command builds the write route into Exact. The review page's command ends with 2 and the one sentence, with
    --share and without, and makes nothing: no cases folder, no temporary folder of the page."""
    monkeypatch.setattr("aid.review.__main__.build_app", lambda *a, **k: pytest.fail("the page was built"))
    example = json.loads((REPO_ROOT / "aid" / "settings.example.json").read_text(encoding="utf-8"))
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(example | {"exact_base_url": "https://erp.example.com", "cases_root": "cases", "po_store_root": "po_store",
                                          "watched_folder": "inbox"}), encoding="utf-8")
    sentence = ('exact_base_url is set, but this version of the aid writes nothing in Exact: every change is prepared for the tab '
                '"In Exact invoeren". Remove exact_base_url from the settings file.')
    for arguments in ([], ["--share"]):
        assert review_main(["--settings", str(path), *arguments]) == 2
        out = capsys.readouterr()
        assert out.err.splitlines() == [f"the settings are refused: {sentence}"] and out.out == "" and "erp.example.com" not in out.err
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["settings.json"]


def test_the_page_reads_the_query_on_load(tmp_path, madeup_out):
    """The page's load reads the query of the request: the case is selected among the offered cases and the write remembered for the
    undo choices of that case."""
    d = deps(tmp_path, madeup_out, writer=done_writer())
    closed, r = run(d, madeup_out, "MU01", "250101")
    entry_id = r.write_entries[1].entry_id
    app = build_app(d)
    [opened] = [f.fn for f in app.fns.values() if getattr(f.fn, "__name__", "") == "opened"]
    choice, wanted, selected = opened("nl", SimpleNamespace(query_params={"case": closed.case_id, "undo": entry_id}))
    assert choice["value"] == closed.case_id and closed.case_id in [value for _, value in choice["choices"]] and wanted == entry_id
    assert selected == {"__type__": "update"}                  # the tab of the review stays the one shown
    choice, wanted, selected = opened("nl", SimpleNamespace(query_params={}))
    assert choice["value"] is None and wanted is None and selected == {"__type__": "update"}


def test_prepared_entries_are_not_offered_for_undo(tmp_path, madeup_out):
    """A change only prepared was not made in the system: the page offers no undo for it, as the digest gives it no undo link."""
    d = deps(tmp_path, madeup_out); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "confirm")
    r2 = close_case(c, d)
    assert {e.status for e in r2.write_entries} == {"prepared"} and _undoable(c) == [] and _undo_choices(c, STRINGS["nl"]) == []
    made, _ = run(deps(tmp_path / "done", madeup_out, writer=done_writer()), madeup_out, "MU01", "250101")
    assert [e.status for e in _undoable(made)] == ["done", "done"]


def test_an_open_question_of_the_run_keeps_the_case_waiting(tmp_path, madeup_out):
    """MU12: the run asks S12 about the unit (R06) and leaves the price and the quantity (R09) to the buyer. Kept on both, the close
    writes nothing, attaches nothing and sets no status: the case waits for the supplier, and the page says a question is open. Once
    the wait has ended and escalate_pending puts the case back in review, the close closes it as before."""
    out = tmp_path / "outbox"
    d = deps(tmp_path, madeup_out)
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"supplier_emails": {"S12": "sales@s12.example.com"}}),
                             "mailer": FileMailer(out, sender="aid@example.com", clock=lambda: NOW)})
    c, r = run(d, madeup_out, "MU12", "250112")
    assert r.status == "review" and [(m["template"], m["status"]) for m in r.messages] == [("quantity_unit", "sent")]
    decide(c, "RI1", "keep"); decide(c, "RI2", "keep")
    text, still_open = close_text(c, d, "nl")
    r2 = CaseResult.model_validate(c.read_step("result"))
    assert r2.status == "waiting" and not still_open and r2.write_entries == [] and WriteLog(c.root).entries() == []
    assert STRINGS["nl"]["question_open"] in text and STRINGS["nl"]["status:waiting"] in text
    assert [row[2] for row in rows(c)][-2:] == ["accept the PO value (B01)"] * 2
    assert c.read_step("close")["records"][-1]["carried_out"].keys() == {"RI1", "RI2"}
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 17, 9, 1), d.settings) == [(c.case_id, "no answer in 3 working days")]
    decide(c, "RI3", "keep")
    r3 = close_case(c, d)
    assert r3.status == "closed" and [e.op.kind for e in r3.write_entries] == ["attach_document", "set_status"]


def test_a_reply_ends_the_open_question(tmp_path, madeup_out):
    out = tmp_path / "outbox"
    d = deps(tmp_path, madeup_out)
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"supplier_emails": {"S12": "sales@s12.example.com"}}),
                             "mailer": FileMailer(out, sender="aid@example.com", clock=lambda: NOW)})
    c, r = run(d, madeup_out, "MU12", "250112")
    (c.root / "messages" / "replies").mkdir(parents=True)
    (c.root / "messages" / "replies" / "answer.eml").write_bytes(b"Subject: Re: Vraag\r\n\r\nDe eenheid is per stuk.\r\n")
    # a file that names no message answers the latest question sent before it was put there: an hour after the run's question
    os.utime(c.root / "messages" / "replies" / "answer.eml", ((NOW + timedelta(hours=1)).timestamp(),) * 2)
    decide(c, "RI1", "keep"); decide(c, "RI2", "keep")
    assert close_case(c, d).status == "closed"


def test_the_close_writes_the_evaluation_case_id_of_the_batch(tmp_path, madeup_out):
    """A case the batch ran has the evaluation's id in the first row of its disposition log; the close's rows carry that id, and their
    note the folder."""
    d = deps(tmp_path, madeup_out)
    c, r = run(deps(tmp_path, madeup_out, writer=RetryingWriter(Down(), PreparedWriter(tmp_path))), madeup_out, "MU08", "250108")
    path = c.root / "dispositions.csv"
    path.unlink(missing_ok=True)
    record_dispositions(c, r, case_id="P-08")
    assert read_csv(path)[0]["case_id"] == "P-08" and r.status == "review"
    for item in r.review_items:
        decide(c, item.item_id, "keep")
    close_case(c, d)
    after = read_csv(path)
    assert len(after) > len(r.write_entries) + 1 and {row["case_id"] for row in after} == {"P-08"}
    assert all(row["note"].startswith(f"folder {c.case_id}") for row in after)


def test_without_a_batch_row_the_close_writes_the_folder_id(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "keep")
    close_case(c, d)
    assert {row["case_id"] for row in read_csv(c.root / "dispositions.csv")} == {c.case_id}


class CcRefused:
    """A mailer whose server takes the message and refuses the buyer's copy."""
    name = "cc-refused"

    def send(self, msg):
        return SentRecord(message_id=uuid.uuid4().hex, to=msg.to, subject=msg.subject, sent_at=NOW.isoformat(timespec="seconds"),
                          template=msg.template, refused_cc=1)


def test_the_message_record_of_a_close_counts_a_refused_copy(tmp_path, madeup_out):
    d = mailing(deps(tmp_path, madeup_out), tmp_path / "outbox"); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "ask")
    r2 = close_case(c, d.model_copy(update={"mailer": CcRefused()}))
    [message] = r2.messages
    assert message["status"] == "sent" and message["refused_cc"] == 1
    assert c.read_step("close")["records"][-1]["messages"][0]["refused_cc"] == 1


# --- a case that waits for its purchase order, and a case replaced when it was checked again (Task 36) ---

from aid.pipeline import _closed_cases  # noqa: E402
from aid.review.close import finish_replaced, waiting_for_po  # noqa: E402

# The hint of a case that waits for its PO (ruling N2), word for word: for a PO, here 250101, and for a quoted version, here version 1
# of PO 250103.
# Since Task 37 the hint names the tab 'Bestanden', where the buyer gives the PDF or the e-mail, and no longer the folder the aid reads.
HINT = {"nl": ("Inkooporder 250101 kent de assistent nog niet. Komt die inkooporder uit Exact binnen, dan controleert de assistent deze "
               "bevestiging vanzelf opnieuw; u hoeft hier niets te besluiten. Blijft zij weg, geef dan de pdf van de inkooporder of de "
               "e-mail van Exact op het tabblad Bestanden. Is 250101 niet een van uw inkoopordernummers, besluit dan zelf over deze zaak."),
        "en": ("The assistant does not know purchase order 250101 yet. When that purchase order comes in from Exact, the assistant checks "
               "this confirmation again by itself; you need not decide anything here. If it stays away, give the PDF of the purchase order "
               "or the e-mail from Exact on the tab Files. If 250101 is not one of your purchase order numbers, decide on this case "
               "yourself.")}
VERSION_HINT = {"nl": ("Versie 1 van inkooporder 250103 kent de assistent nog niet. Komt die versie uit Exact binnen, dan controleert de "
                       "assistent deze bevestiging vanzelf opnieuw; u hoeft hier niets te besluiten. Blijft zij weg, geef dan de pdf van "
                       "die versie of de e-mail van Exact op het tabblad Bestanden."),
                "en": ("The assistant does not know version 1 of purchase order 250103 yet. When that version comes in from Exact, the "
                       "assistant checks this confirmation again by itself; you need not decide anything here. If it stays away, give the "
                       "PDF of that version or the e-mail from Exact on the tab Files.")}


def without_po(d, tmp_path):
    """The dependencies d with an empty PO store: a confirmation then waits for its PO."""
    return d.model_copy(update={"store": PoStore(tmp_path / "empty_store")})


def test_a_replaced_case_is_not_offered_and_a_decision_on_it_is_never_carried_out(tmp_path, madeup_out):
    """Rule 8: a case finished as replaced is closed with no supplier and the digest line to match, keeps its review items and its
    version, and has the record of its replacement in step 09 and in the audit record. The page no longer offers it, not even by the
    digest's link; a decision that a browser tab still showing it records is never carried out, because the close refuses a case that
    is not in review; and it does not count toward its supplier's first cases."""
    d = deps(tmp_path, madeup_out)
    c, r = run(without_po(d, tmp_path), madeup_out, "MU01", "250101")
    root = d.settings.cases_root
    assert (r.status, r.supplier, waiting_for_po(c)) == ("review", "S01", "po") and [s.case_id for s in offered_cases(root)] == [c.case_id]
    finish_replaced(c, "250101_20261112T090100", LATER, "nl")
    result = CaseResult.model_validate(c.read_step("result"))
    assert (result.status, result.supplier, result.digest_line) == ("closed", None, "PO 250101: afgesloten, geen schrijfacties")
    assert result.model_dump(exclude={"status", "supplier", "digest_line"}) == r.model_dump(exclude={"status", "supplier", "digest_line"})
    record = {"action": "replaced", "at": LATER.isoformat(), "by": "250101_20261112T090100"}
    assert c.read_step("close") == {"records": [record]} and audit(c)["close"] == [record] and audit(c)["case_id"] == c.case_id
    assert offered_cases(root) == [] and list_open_cases(root) == [] and selection_from_query(root, {"case": c.case_id}) == (None, None)
    assert waiting_for_po(c) is None
    decide(c, "RI1", "keep")                                                          # the stale tab records a decision
    with pytest.raises(ValueError, match="not in review"):
        close_case(c, d)
    assert close_text(c, d, "nl") == (STRINGS["nl"]["not_closed"], False)
    assert not (c.root / "write_log.jsonl").exists() and not (tmp_path / "prepared_changes.csv").exists()
    assert c.read_step("close") == {"records": [record]}
    assert _closed_cases(root, "S01", "another case") == 0
    first, r2 = run(deps(tmp_path, madeup_out, settings=NEW_SUPPLIER.model_copy(update={"first_cases_review": 1})), madeup_out, "MU01",
                    "250101", now=LATER + timedelta(minutes=1))
    assert [item.reason for item in r2.review_items] == ["new supplier"]            # the replaced case is no closed case of S01


def test_the_hint_on_a_waiting_case(tmp_path, madeup_out):
    """Rule 10 and ruling N2: the review tab shows the hint above the documents on a case that waits for its PO, in the page's
    language, naming what it waits for: the PO by its number, or the version of the PO that the confirmation quotes. It shows on no
    other case: not on a case opened without a PO number, whose result waits all the same (it waits for nothing the aid can promise,
    and stays with the buyer), not on a case in review for another reason and not without a case."""
    d = deps(tmp_path, madeup_out)
    waiting, _ = run(without_po(d, tmp_path), madeup_out, "MU01", "250101")
    only_v2 = without_po(d, tmp_path / "v2")
    only_v2.store.add(madeup_out / "MU03_po_v2.pdf")
    quoted, r = run(only_v2, madeup_out, "MU03", "250103", now=LATER)
    assert r.version.reason == "quotes version 1, which is not stored"
    numberless, r = run(without_po(d, tmp_path), madeup_out, "MU02", None, now=LATER + timedelta(minutes=1))
    assert numberless.po_number is None and r.version.reason == "no stored version" and waiting_for_po(numberless) is None
    other, _ = run(d, madeup_out, "MU07", "250107", now=LATER + timedelta(minutes=2))
    app = build_app(d)
    [show] = [f for f in app.fns.values() if getattr(f.fn, "__name__", "") == "show"]
    [relabel] = [f for f in app.fns.values() if getattr(f.fn, "__name__", "") == "relabel"]
    at = [getattr(block, "elem_id", None) for block in show.outputs].index("review-hint")
    again = [getattr(block, "elem_id", None) for block in relabel.outputs].index("review-hint")      # the language switch relabels it
    for case, hint in ((waiting, HINT), (quoted, VERSION_HINT)):
        shown = show.fn(case.case_id, "nl")
        assert shown[at] == {"__type__": "update", "value": hint["nl"], "visible": True} and shown[-1] == ""
        state = shown[[type(block).__name__ for block in show.outputs].index("State")]
        assert relabel.fn("en", case.case_id, state)[again] == {"__type__": "update", "value": hint["en"], "visible": True}
    for case_id in (numberless.case_id, other.case_id, None):
        assert show.fn(case_id, "nl")[at] == {"__type__": "update", "value": "", "visible": False}, case_id
        assert relabel.fn("en", case_id, None)[again]["visible"] is False, case_id
    for language in ("nl", "en"):
        assert STRINGS[language]["waiting_hint:po"].format(number="250101") == HINT[language]
        assert STRINGS[language]["waiting_hint:version"].format(number="250103", version="1") == VERSION_HINT[language]
    gr = import_gradio()
    [block] = [block for block in app.blocks.values() if getattr(block, "elem_id", None) == "review-hint"]
    assert isinstance(block, gr.Markdown) and block.visible is False


GUESSED = {"nl": ("Kijk na of dit de goede inkooporder is: de assistent vond haar niet aan het nummer achter een van de woorden van de "
                   "nummerherkenning."),
           "en": ("Check that this is the right purchase order: the assistant did not find it by the number after a word of the number "
                  "recognition.")}


def test_the_reason_of_a_guessed_link(tmp_path, madeup_out):
    """Ruling N18: a case whose PO was found otherwise than by the one number of the pattern (case.json's po_found_by) is in review with
    one item for the whole case, as R11 makes one, and nothing written; the page and the digest name its reason by its label, word for
    word. The same confirmation opened with the pattern's number is closed."""
    from aid.case import open_case
    from aid.pipeline import GUESSED_PO
    from aid.sources.mailbox import FOUND_BY_LINES
    d = deps(tmp_path, madeup_out)
    guessed = open_case(d.settings.cases_root, "confirmation", [madeup_out / "MU01_conf.pdf"], "250101", NOW, found_by=FOUND_BY_LINES)
    result = run_case(guessed, d)
    assert (result.status, [(item.reason, item.finding_id) for item in result.review_items], result.write_entries) == (
        "review", [(GUESSED_PO, None)], [])
    assert json.loads((guessed.root / "case.json").read_text(encoding="utf-8"))["po_found_by"] == FOUND_BY_LINES
    for language in ("nl", "en"):
        assert STRINGS[language][f"reason:{GUESSED_PO}"] == GUESSED[language]
        assert GUESSED[language] in str(page_rows(load_case_view(guessed).flags, language)), language
    assert [decision["rule_id"] for decision in audit(guessed)["decisions"]] == ["G01"]
    plain, r = run(d, madeup_out, "MU01", "250101", now=LATER)
    assert r.status == "closed" and "po_found_by" not in json.loads((plain.root / "case.json").read_text(encoding="utf-8"))


def test_the_tables_of_the_review_tab_keep_their_columns_in_view(tmp_path, madeup_out):
    """Ruling N32 and finding 7 of the second check: the three tables of the review tab (the differences, the matched values and the
    uncompared items) give each of their eight columns a width, in whole percents that make the page's width together, so that their
    cells wrap inside their columns and a long reason, as the guess's, never pushes the two source columns out of view. The reason
    gets the widest column, the two sources the next widest."""
    gr = import_gradio()
    nl = STRINGS["nl"]
    headers = [nl[column] for column in ("item", "field", "line", "po_value", "doc_value", "reason", "po_source", "doc_source")]
    app = build_app(deps(tmp_path, madeup_out))
    tables = [block for block in app.blocks.values() if isinstance(block, gr.Dataframe) and block.value["headers"] == headers]
    assert len(tables) == 3
    for table in tables:
        widths = dict(zip(headers, [int(width.rstrip("%")) for width in table.column_widths or []]))
        assert len(widths) == len(headers) and sum(widths.values()) == 100 and table.wrap
        ranked = sorted(widths, key=widths.get, reverse=True)
        assert ranked[0] == nl["reason"] and set(ranked[1:3]) == {nl["po_source"], nl["doc_source"]}


def rendered_saves(app, state: dict) -> list:
    """The events that the decision rows of the review tab make for the case of `state`, as the page renders them when the case is
    chosen: one per radio and one per tick (gr.render, which a test can only reach through the page's own render function)."""
    from gradio.context import LocalContext
    [renderable] = app.default_config.renderables
    token = LocalContext.blocks_config.set(app.default_config)
    try:
        renderable.apply(state, "nl")
    finally:
        LocalContext.blocks_config.reset(token)
    return [f for f in app.default_config.fns.values() if getattr(f, "rendered_in", None) is renderable]


def test_the_hint_follows_a_decision_and_the_refresh(tmp_path, madeup_out):
    """Ruling N8: a decision saved on a waiting case hides the hint at once, as the case then waits no longer and is not checked again;
    the decision's radio and its tick update the hint with the status line. "Lijst vernieuwen" updates the hint as well, for a decision
    saved in another browser tab or a case the runner replaced meanwhile. Driven through the page's own wiring: the events the
    decision rows make, and the refresh button's."""
    d = deps(tmp_path, madeup_out)
    waiting, _ = run(without_po(d, tmp_path), madeup_out, "MU01", "250101")
    second, _ = run(without_po(d, tmp_path), madeup_out, "MU02", "250102", now=LATER)
    app = build_app(d)
    [show] = [f for f in app.fns.values() if getattr(f.fn, "__name__", "") == "show"]
    hint_block = show.outputs[[getattr(block, "elem_id", None) for block in show.outputs].index("review-hint")]
    shown = show.fn(waiting.case_id, "nl")
    state = shown[[type(block).__name__ for block in show.outputs].index("State")]
    saves = rendered_saves(app, state)
    assert len(saves) == 2 and {app.default_config.blocks[target[0]].__class__.__name__ for f in saves for target in f.targets} == {"Radio", "Checkbox"}
    for f in saves:                                                          # the radio's and the tick's outputs: the status line and the hint
        assert [type(block).__name__ for block in f.outputs] == ["Markdown", "Markdown"] and f.outputs[1] is hint_block
    [radio] = [f for f in saves if app.default_config.blocks[f.targets[0][0]].__class__.__name__ == "Radio"]
    assert radio.fn(None, False, "nl", state) == ({"__type__": "update"}, {"__type__": "update"})          # nothing chosen: nothing changes
    message, hint = radio.fn("unresolved", False, "nl", state)
    assert message == STRINGS["nl"]["saved"].format(item="RI1") and hint == {"__type__": "update", "value": "", "visible": False}
    assert waiting_for_po(waiting) is None
    [refresh] = [f for f in app.fns.values() if getattr(f.fn, "__name__", "") == "refreshed"]
    assert [getattr(block, "label", None) for block in refresh.inputs] == [STRINGS["nl"]["language"], STRINGS["nl"]["case"]]
    assert refresh.outputs[1] is hint_block and getattr(refresh.outputs[0], "label", None) == STRINGS["nl"]["case"]
    assert [getattr(app.blocks[target[0]], "value", None) for target in refresh.targets] == [STRINGS["nl"]["refresh"]]
    choices, hint = refresh.fn("nl", second.case_id)
    assert hint == {"__type__": "update", "value": HINT["nl"].replace("250101", "250102"), "visible": True}
    decide(second, "RI1", "keep")                                            # in another browser tab
    choices, hint = refresh.fn("en", second.case_id)
    assert hint == {"__type__": "update", "value": "", "visible": False}
    assert [value for _, value in choices["choices"]] == [summary.case_id for summary in offered_cases(d.settings.cases_root)]
    finish_replaced(waiting, "250101_20261112T090500", LATER, "nl")          # the runner replaced it
    assert refresh.fn("nl", waiting.case_id)[1]["visible"] is False and refresh.fn("nl", None)[1]["visible"] is False
    assert refresh.fn("nl", "250199_20261112T090000")[1]["visible"] is False          # a case whose folder is gone


# --- the page's temporary files (Task 30) ---

TEMPORARY = """
import json, os, sys
import aid.review.__main__ as review

seen = {}

def vibe():
    return os.path.join(os.environ["GRADIO_TEMP_DIR"], "vibe_edit_history")

class Page:
    def launch(self, **options):
        seen["launched"] = True
        seen["thread_free"] = options.get("prevent_thread_lock")
        os.makedirs(vibe(), exist_ok=True)                # as the page library does whenever it makes its application
        if os.environ.get("MADE_UP_VIBE_FILE"):
            open(os.path.join(vibe(), "kept.txt"), "w").close()

    def block_thread(self):
        seen["vibe_folder_while_the_page_runs"] = os.path.isdir(vibe())

def build_app(deps, **options):
    seen["folder"] = os.environ.get("GRADIO_TEMP_DIR")
    seen["no_proxy"] = [os.environ.get("NO_PROXY"), os.environ.get("no_proxy")]
    seen["gradio"] = "gradio" in sys.modules              # the page library is imported in build_app, after the folder is named
    seen["vibe_mode"] = os.environ.get("GRADIO_VIBE_MODE")
    os.makedirs(vibe(), exist_ok=True)
    return Page()

review.build_app, review.launch_options = build_app, lambda page, share: {}
seen["code"] = review.main(["--settings", sys.argv[1]])
print(json.dumps(seen))
"""


def test_the_page_keeps_its_temporary_files_under_the_cases_root(tmp_path, madeup_out):
    """The page library keeps an uploaded file and the page images of a document in its temporary folder: <cases_root>/tmp, named
    in GRADIO_TEMP_DIR before the library is imported and emptied when the page starts, unless the environment names a folder
    already. The command runs in a fresh interpreter, as the launcher starts it, with build_app replaced so that no page is built."""
    example = json.loads((REPO_ROOT / "aid" / "settings.example.json").read_text(encoding="utf-8"))
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(example | {"reader": "parser"}), encoding="utf-8")
    left = tmp_path / "cases" / "tmp" / "0a1b2c" / "page_1.png"            # left by a page that was ended without its own clean-up
    left.parent.mkdir(parents=True)
    left.write_bytes(PNG)
    working = tmp_path / "cases" / "tmp" / "work" / "aid_mailbox_x" / "MU07_conf.pdf"     # the runner's, while it scans the inbox
    working.parent.mkdir(parents=True)
    working.write_bytes(b"%PDF")
    environment = {name: value for name, value in os.environ.items() if name != "GRADIO_TEMP_DIR" and not name.lower().endswith("_proxy")}
    environment["NO_PROXY"] = "intranet.example.com"                       # what the environment holds there is kept

    def review_command(**more) -> dict:
        done = subprocess.run([sys.executable, "-B", "-c", TEMPORARY, str(path)], cwd=REPO_ROOT, env=environment | more,
                              capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.splitlines()[-1])

    seen = review_command(GRADIO_VIBE_MODE="1")
    # The page library makes a folder vibe_edit_history in its temporary folder whenever it makes its application, at the build and
    # again at the launch, and writes there only in its "vibe" mode: the command does not pass that mode on to the page, and
    # removes the folder, empty as it then is, once the page is launched (ruling S28 of the review round of Task 44).
    assert seen == {"folder": str(tmp_path / "cases" / "tmp"), "gradio": False, "launched": True, "code": 0, "vibe_mode": None,
                    "thread_free": True, "vibe_folder_while_the_page_runs": False,
                    "no_proxy": ["intranet.example.com,127.0.0.1,localhost", "127.0.0.1,localhost"]}     # no proxy for the page itself
    assert sorted(path.name for path in (tmp_path / "cases" / "tmp").iterdir()) == ["work"] and working.is_file()     # the work stays
    assert review_command(MADE_UP_VIBE_FILE="1")["vibe_folder_while_the_page_runs"] is True      # not empty: left as it is (rmdir only)
    assert (tmp_path / "cases" / "tmp" / "vibe_edit_history" / "kept.txt").is_file()
    # a folder the environment names is the one used, and it is not emptied
    own = tmp_path / "own temporary folder"
    own.mkdir()
    (own / "kept.txt").write_text("another program's", encoding="utf-8")
    left.parent.mkdir(parents=True)
    left.write_bytes(PNG)
    assert review_command(GRADIO_TEMP_DIR=str(own))["folder"] == str(own)
    assert (own / "kept.txt").is_file() and left.is_file()
    # the page removes a temporary file an hour after it was made: every hour, the files older than an hour
    assert build_app(deps(tmp_path, madeup_out)).delete_cache == (3600, 3600)


def test_a_moved_folder_keeps_its_purchase_order_pages(tmp_path, madeup_out):
    """The aid from a folder may be moved (MANUAL 3.16). The store gives a PDF version's pdf_path as the po.pdf in the version's own
    folder under its current root, whatever po.json holds, and the page looks the selected version of a case up in the current
    store when the path of step 01 is not there. Nothing written changes, and in place every path reads as stored."""
    before = tmp_path / "before"
    store = PoStore(before / "po_store", SETTINGS.authorization_limit)
    store.add(madeup_out / "MU07_po_v1.pdf")
    d = deps(before, madeup_out).model_copy(update={"store": store})
    c, r = run(d, madeup_out, "MU07", "250107")
    stored = before / "po_store" / "250107" / "v1" / "po.json"
    assert store.versions("250107")[0].pdf_path == Path(json.loads(stored.read_text(encoding="utf-8"))["pdf_path"])     # in place
    assert len(load_case_view(c).po_pages) == 1
    written = {path.relative_to(before): path.read_bytes() for path in before.rglob("*.json")}
    shutil.move(str(before), str(tmp_path / "after"))
    after = tmp_path / "after"
    moved_store = PoStore(after / "po_store", SETTINGS.authorization_limit)
    assert moved_store.versions("250107")[0].pdf_path == after / "po_store" / "250107" / "v1" / "po.pdf"
    moved = load_case(after / "cases", c.case_id)
    assert load_case_view(moved).po_pages == []                          # the path of step 01 names the folder before the move
    view = load_case_view(moved, moved_store)
    assert len(view.po_pages) == 1 and len(view.doc_pages) == 1
    # the page itself, built on the moved folder
    moved_deps = d.model_copy(update={"store": moved_store, "settings": d.settings.model_copy(update={"cases_root": after / "cases"})})
    [show] = [f.fn for f in build_app(moved_deps).fns.values() if getattr(f.fn, "__name__", "") == "show"]
    shown = show(c.case_id, "nl")
    assert len(shown[1]) == 1 and len(shown[2]) == 1                     # the supplier's document and the PO, a page each
    assert {path.relative_to(after): path.read_bytes() for path in after.rglob("*.json")} == written     # nothing written changed


def test_a_look_from_an_export_that_is_gone_shows_no_purchase_order_pages(tmp_path, madeup_out):
    """A cart is matched against a look from an export (aid/po_store.py), which the store gives with the number of the PO's latest
    PDF version and which names its export, not a copy in the store. The page shows no pages of an export, and when the export is
    gone it shows none either: the lookup for a moved folder is for the store's own copy of a PDF (po.pdf), so it never gives the
    pages of the PDF version that has the look's number."""
    from aid.tests.helpers import EXPORT_COLUMNS
    from aid.tests.test_pipeline import drawn_po, filed_later
    d = deps(tmp_path, madeup_out)
    pdf = d.store.add(drawn_po(tmp_path / "250201.pdf", "MU03", "250201"))
    export = tmp_path / "exports" / "CART01_export.csv"
    export.parent.mkdir()
    shutil.copy(madeup_out / "CART01_export.csv", export)
    [look] = d.store.from_export(export, EXPORT_COLUMNS)
    filed_later(d.store, "250201", "e1")
    c = open_upload_case(d.settings.cases_root, [madeup_out / "CART01.html"], "250201", "cart", NOW)
    run_case(c, d)
    assert [v["pdf_path"] for v in c.read_step("po_versions")["versions"]] == [str(export)]          # step 01 holds the look
    assert (look.version, pdf.version, d.store.snapshots("250201")[0].pdf_path) == (1, 1, export)    # a look keeps its export's path
    assert load_case_view(c, d.store).po_pages == []
    export.unlink()
    assert load_case_view(c, d.store).po_pages == [] and load_case_view(c, d.store).doc_pages == []   # an HTML cart has no pages


# --- no proxy between the aid and its own pages (Task 30) ---

def test_the_review_command_lets_no_proxy_stand_between_the_page_library_and_the_page():
    """127.0.0.1 and localhost are added to NO_PROXY and no_proxy, each keeping what it holds. Python takes the PC's own proxy from
    the system (Windows, macOS) only while the environment names no proxy at all, and NO_PROXY is such a name: so when the
    environment named none, the system's http and https proxies are written into it too, and every other address keeps the proxy
    it had. An environment that names a proxy keeps it as it is."""
    from aid.review.__main__ import own_hosts_without_proxy
    environ = {}
    own_hosts_without_proxy(environ, system_proxies=lambda: {})
    assert environ == {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    own_hosts_without_proxy(environ, system_proxies=lambda: {})
    assert environ == {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}         # a second time adds nothing
    environ = {"NO_PROXY": "intranet.example.com, .corp.example.org,", "no_proxy": "LOCALHOST", "HTTPS_PROXY": "http://proxy.example.com:3128"}
    own_hosts_without_proxy(environ, system_proxies=lambda: {"http": "http://other.example.com:8080"})
    assert environ == {"NO_PROXY": "intranet.example.com, .corp.example.org,127.0.0.1,localhost", "no_proxy": "LOCALHOST,127.0.0.1",
                       "HTTPS_PROXY": "http://proxy.example.com:3128"}
    environ = {}
    system = {"http": "http://proxy.example.com:8080", "https": "http://proxy.example.com:8080", "ftp": "http://proxy.example.com:8080"}
    own_hosts_without_proxy(environ, system_proxies=lambda: system)
    assert environ == {"http_proxy": "http://proxy.example.com:8080", "https_proxy": "http://proxy.example.com:8080",
                       "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}


def test_the_page_librarys_request_to_its_own_address_goes_past_the_proxy(monkeypatch):
    """The page library asks its own start-up address (http://127.0.0.1:<port>/) with httpx once it is started. A proxy of the PC
    cannot reach this PC's 127.0.0.1, so through it that request fails and the page ends: here a proxy of the environment that
    nothing listens on stands for it. After own_hosts_without_proxy the same request goes to the page itself."""
    import httpx

    from aid.review.__main__ import own_hosts_without_proxy
    from aid.tests.helpers import local_page, unreachable_proxy, without_proxies
    without_proxies(monkeypatch)
    monkeypatch.setenv("http_proxy", unreachable_proxy())
    for name in ("NO_PROXY", "no_proxy"):                       # recorded, so that what the call adds is undone after the test
        monkeypatch.setenv(name, "")
    with local_page() as url:
        with pytest.raises(httpx.HTTPError):
            httpx.get(url, timeout=5)
        own_hosts_without_proxy()
        assert httpx.get(url, timeout=5).status_code == 200
        assert httpx.get(url.replace("127.0.0.1", "localhost"), timeout=5).status_code == 200


# --- Task 43: the values entered in Exact that a case was compared with ---

import pathlib  # noqa: E402

from aid.execution.by_hand import mark, open_changes  # noqa: E402
from aid.review.app import EnteredValue  # noqa: E402
from aid.review.page import entered_text  # noqa: E402
from aid.tests.helpers import confirmation_again, filed_on  # noqa: E402

ENTERED = {"nl": "Sinds deze inkooporder is opgenomen, is in Exact ingevoerd: regel 1: prijs 12,70. De assistent vergelijkt met deze waarden.",
           "en": "Since this purchase order was filed, this was entered in Exact: line 1: price 12.70. The assistant compares with these values."}
LIST_UNREAD = {"nl": "De lijst In Exact invoeren kon niet worden gelezen: de assistent vergelijkt met de inkooporder zoals zij is opgenomen.",
               "en": "The list Enter in Exact could not be read: the assistant compares with the purchase order as it was filed."}


def test_the_page_names_the_applied_changes(tmp_path, madeup_out, monkeypatch):
    """Rule 7 and amendment 5: above the lists of the review tab, a case that was compared with values entered in Exact names each of
    them, in the page's language and with the values as the tab "In Exact invoeren" shows them, and the list of differences shows
    the entered value as the order's, without a source. A case run while the list could not be read says that instead. A case that
    was compared with the order as it was filed shows no line."""
    d = deps(tmp_path, madeup_out, writer=PreparedWriter(tmp_path / "cases" / "prepared"))
    prepared = d.settings.cases_root / "prepared"
    filed_on(d.store, "250108", "v1", datetime(2026, 11, 1, 9, 0))               # filed before the tick, whatever day the suite runs
    first, _ = run(d, madeup_out, "MU08", "250108")
    ids = [entry_id for change in open_changes(prepared) if change.kind == "po_line_price" for entry_id in change.ids]
    assert mark(prepared, ids, "entered", NOW + timedelta(hours=1)) == 1
    high = open_upload_case(d.settings.cases_root, [confirmation_again(tmp_path / "MU08_high.pdf", "MU08", prices={"1": "14.00"})],
                            "250108", "confirmation", LATER)
    assert run_case(high, d).status == "review"                                   # 14.00 against the entered 12.70: above the threshold
    view = load_case_view(high)
    assert view.entered == [EnteredValue(line="1", field="price", value="12.70")] and view.entered_unread is False
    assert (view.flags[0].po_value, view.flags[0].doc_value, view.flags[0].po_source) == ("12.70", "14.00", "")
    assert load_case_view(first).entered == [] and load_case_view(first).entered_unread is False
    real = pathlib.Path.read_bytes

    def read_bytes(self):
        if self.name == "marks.jsonl":
            raise PermissionError(13, "The process cannot access the file because it is being used by another process", str(self))
        return real(self)
    with monkeypatch.context() as held:                                           # the list held by another program during a run
        held.setattr(pathlib.Path, "read_bytes", read_bytes)
        blind = open_upload_case(d.settings.cases_root, [confirmation_again(tmp_path / "MU08_blind.pdf", "MU08", prices={"1": "14.00"})],
                                 "250108", "confirmation", LATER + timedelta(minutes=1))
        assert run_case(blind, d).status == "review"
    unread = load_case_view(blind)
    assert (unread.entered, unread.entered_unread, unread.flags[0].po_value) == ([], True, "12.50")
    assert page_rows(unread.flags, "nl")[0][5] == LIST_UNREAD["nl"] and page_rows(unread.flags, "en")[0][5] == LIST_UNREAD["en"]     # L01's reason
    closed = open_upload_case(d.settings.cases_root, [confirmation_again(tmp_path / "MU08_closed.pdf", "MU08", prices={"1": "12.90"})],
                              "250108", "confirmation", LATER + timedelta(minutes=2))
    assert run_case(closed, d).status == "closed"                                 # within the threshold from 12.70: prepared, closed
    assert load_case_view(closed).entered == [EnteredValue(line="1", field="price", value="12.70")]          # a closed case shown has it too
    gr = import_gradio()
    app = build_app(d)
    [show] = [f for f in app.fns.values() if getattr(f.fn, "__name__", "") == "show"]
    [relabel] = [f for f in app.fns.values() if getattr(f.fn, "__name__", "") == "relabel"]
    at = [getattr(block, "elem_id", None) for block in show.outputs].index("review-entered")
    switched = [getattr(block, "elem_id", None) for block in relabel.outputs].index("review-entered")     # the language switch relabels it
    for case, text in ((high, ENTERED), (blind, LIST_UNREAD), (closed, ENTERED)):
        shown = show.fn(case.case_id, "nl")
        assert shown[at] == {"__type__": "update", "value": text["nl"], "visible": True} and shown[-1] == ""
        state = shown[[type(block).__name__ for block in show.outputs].index("State")]
        assert relabel.fn("en", case.case_id, state)[switched] == {"__type__": "update", "value": text["en"], "visible": True}
    for case_id in (first.case_id, None):
        assert show.fn(case_id, "nl")[at] == {"__type__": "update", "value": "", "visible": False}, case_id
        assert relabel.fn("en", case_id, None)[switched] == {"__type__": "update", "value": "", "visible": False}, case_id
    blocks = list(app.blocks.values())
    [line] = [block for block in blocks if getattr(block, "elem_id", None) == "review-entered"]
    galleries = [blocks.index(block) for block in blocks if isinstance(block, gr.Gallery)]
    tables = [blocks.index(block) for block in blocks if isinstance(block, gr.Dataframe)]
    assert isinstance(line, gr.Markdown) and line.visible is False and max(galleries) < blocks.index(line) < min(tables)     # above the lists
    [title] = [blocks.index(block) for block in blocks if isinstance(block, gr.Markdown) and block.value == f"### {STRINGS['nl']['flags']}"]
    assert blocks.index(line) < title                                             # and above the title of the first list, not under it


def test_the_line_escapes_a_line_id_for_markdown():
    """A line id from an export is any text: its characters that Markdown reads are escaped in the line above the lists, as on the
    tab "In Exact invoeren", so that the line shows the id as it is."""
    values = [EnteredValue(line="A_1*[x]", field="price", value="12.70")]
    assert entered_text(values, False, "nl") == ("Sinds deze inkooporder is opgenomen, is in Exact ingevoerd: regel A\\_1\\*\\[x\\]: prijs 12,70. De "
                                                "assistent vergelijkt met deze waarden.")


def test_the_reason_of_a_case_run_without_the_list_is_in_the_manuals_table():
    """The review round's W6: the reason of the pipeline's rule L01 has amendment 5's sentence as its label in both languages, and
    the table of reasons in MANUAL 2.4 holds it."""
    from aid.pipeline import UNREAD_LIST, UNREAD_RULE

    manual = (REPO_ROOT / "aid" / "MANUAL.md").read_text(encoding="utf-8")
    assert UNREAD_RULE == "L01" and (STRINGS["nl"][f"reason:{UNREAD_LIST}"], STRINGS["en"][f"reason:{UNREAD_LIST}"]) == (LIST_UNREAD["nl"], LIST_UNREAD["en"])
    assert f"| {UNREAD_LIST} | {LIST_UNREAD['nl']} | {LIST_UNREAD['en']} |" in manual


def test_the_line_of_the_entered_values_in_both_languages():
    """The texts of rule 7 and of amendment 5, word for word: the line names each value by its line and its kind, a price or a
    quantity with the decimal mark of the language, a day as dd-mm-yyyy and a week by its number, joined by semicolons; with
    nothing entered and the list read, there is no line."""
    values = [EnteredValue(line="1", field="price", value="12.70"), EnteredValue(line="2", field="delivery_date", value="2026-W48"),
              EnteredValue(line="2", field="quantity", value="60.5"), EnteredValue(line="10", field="delivery_date", value="2026-11-20")]
    assert entered_text(values, False, "nl") == ("Sinds deze inkooporder is opgenomen, is in Exact ingevoerd: regel 1: prijs 12,70; regel 2: "
                                                "leverdatum week 48 (2026); regel 2: aantal 60,5; regel 10: leverdatum 20-11-2026. De "
                                                "assistent vergelijkt met deze waarden.")
    assert entered_text(values, False, "en") == ("Since this purchase order was filed, this was entered in Exact: line 1: price 12.70; line "
                                                "2: delivery date week 48 (2026); line 2: quantity 60.5; line 10: delivery date 20-11-2026. "
                                                "The assistant compares with these values.")
    assert entered_text(values[:1], False, "nl") == ENTERED["nl"] and entered_text(values[:1], False, "en") == ENTERED["en"]
    assert entered_text([], True, "nl") == LIST_UNREAD["nl"] and entered_text(values, True, "en") == LIST_UNREAD["en"]
    assert entered_text([], False, "nl") == "" == entered_text([], False, "en")
    nl, en = STRINGS["nl"], STRINGS["en"]
    assert nl["entered_values"] == ("Sinds deze inkooporder is opgenomen, is in Exact ingevoerd: {changes}. De assistent vergelijkt met deze "
                                    "waarden.")
    assert en["entered_values"] == ("Since this purchase order was filed, this was entered in Exact: {changes}. The assistant compares with "
                                    "these values.")
    assert [nl[f"entered:{field}"] for field in ("price", "delivery_date", "quantity")] == [
        "regel {line}: prijs {value}", "regel {line}: leverdatum {value}", "regel {line}: aantal {value}"]
    assert [en[f"entered:{field}"] for field in ("price", "delivery_date", "quantity")] == [
        "line {line}: price {value}", "line {line}: delivery date {value}", "line {line}: quantity {value}"]
    assert (nl["entered_unread"], en["entered_unread"]) == (LIST_UNREAD["nl"], LIST_UNREAD["en"])


def test_the_documents_say_what_the_aid_compares_with():
    """Task 43 in the documents: the manual's sections 1, 2.2 and 2.6 say that a document is compared with the order as Exact holds
    it by the ticks, 2.2 quotes the line above the lists and the sentence of a list that could not be read word for word, 2.6 and
    the Dutch guide have the two sentences of the answer to check A7, 3.1 and 3.6 name the new step file, 3.10 and 3.17 say how a
    value is laid over a version and what a try-out is made from, 4.9 says what remains (a tick without an entry is believed), and
    the README says what the aid compares with. The limitation that a cart asks the same change again until a new export comes
    is gone."""
    from aid.tests.test_hand_tab import manual_sections

    manual, whole = manual_sections(), Path("aid/MANUAL.md").read_text(encoding="utf-8")
    guide = ("Laat u Exact de inkooporder na de wijziging opnieuw versturen, dan vinkt de assistent de wijziging zelf af. Doet u dat niet, "
             "vink de wijziging dan af op het tabblad In Exact invoeren: de assistent vergelijkt latere documenten dan met de waarde die u "
             "hebt ingevoerd.")
    assert guide in manual["2.6"] and guide in manual["7"]
    assert "**What a document is compared with.**" in manual["1"] and "a tick is the buyer's word" in manual["1"]
    for words in (f"\"{ENTERED['nl']}\"", f"\"{LIST_UNREAD['nl']}\"", "regel 1: prijs 12,70; regel 2: leverdatum 20-11-2026"):
        assert words in manual["2.2"], words
    for words in ("**What the next check is compared with.**", "(Verrichten)", "counts for the next try-out alone",
                  "A version or an export that the aid was given after the tick is taken as it is"):
        assert words in manual["2.6"], words
    assert f"\"{ENTERED['nl']}\"" in manual["7"] and f"\"{LIST_UNREAD['nl']}\"" in manual["7"]
    assert "03_entered.json only when" in manual["3.1"] and "`steps/03_entered.json`" in manual["3.6"]
    for words in ("`as_entered` of `aid/execution/by_hand.py`", "`PoStore.given_at`", "`PoStore.filed_at`", '`"unread": true`', "check A7"):
        assert words in manual["3.10"], words
    assert "prints 12,63 and its case closes without a price item" in manual["3.17"] and "prints 12,76" in manual["3.17"]
    assert "**A tick without an entry is believed.**" in manual["4.9"]
    for words in ("asks for the same change again until a new export comes", "does not learn the aid's own changes", "is the owner's check A7;"):
        assert words not in whole, words
    readme = Path("aid/README.md").read_text(encoding="utf-8")
    assert "with the purchase order as Exact holds it (the version of the order that the document answers" in readme
    assert "—" not in whole and "—" not in readme


def test_the_documents_say_what_the_review_round_of_task_43_ruled():
    """The review round of Task 43 in the documents: a newer print ends a tick that it contradicts (sections 1, 2.6, 4.9 and the
    Dutch guide, W9); a value belongs to a line of the order (1, 2.6 and 3.10, W1, W2 and W7); a change the supplier takes back
    leaves the list (2.6, W4); a list that cannot be read or understood sends the case to the buyer (2.2, 2.6 and 3.10, W5 and W6);
    the pre-check judges a stored match by the list (2.5, W3); and the put-back says when the aid knows another old value (2.6 and
    the guide, W10), in the words of the page."""
    from aid.tests.test_hand_tab import manual_sections

    manual, whole = manual_sections(), Path("aid/MANUAL.md").read_text(encoding="utf-8")
    assert "that the aid is given after the tick shows another value for that line, whether that print shows anything new or not. Such a print ends the tick" in manual["1"]
    assert "not to a line number" in manual["1"]
    for words in ("**A newer print ends a tick.**", "No case closes as confirmed on a tick that the newest print contradicts.",
                  "an export given after the tick that shows another value ends the tick for the PDF versions too",
                  "tick the row again once the value is in Exact: the later tick counts",
                  "A change that the supplier takes back leaves the list",
                  "are one row of the list", "the aid prepares, requests and asks nothing for it",
                  STRINGS["nl"]["hand_put_back_other"].format(old="12,50", source=STRINGS["nl"]["hand_put_back_source:marks"], value="12,70"),
                  f"\"volgens {STRINGS['nl']['hand_put_back_source:look']}\""):
        assert words in manual["2.6"], words
    assert "What a later PDF shows never takes back a tick" not in whole
    assert "it prepared no change, requested no approval and asked the supplier nothing" in manual["2.2"]
    assert "(de lijst In Exact invoeren zegt nu iets anders)" in manual["2.5"] and "is judged by the list again each time it is shown" in manual["2.5"]
    assert "removed\n                   when the run writes none" in manual["3.1"]
    for words in ("`contradict`", "rule L01", "`withdraw`", "`ListUnread`", "the one marked last decides"):
        assert words in manual["3.10"], words
    assert "that print ends the tick, the change stands on the list again" in manual["4.9"]
    for words in ("tot een nieuwere inkooporder-pdf of export uit Exact voor die regel een andere waarde laat zien. Dan staat de wijziging "
                  "weer op de lijst", "vink de wijziging dan opnieuw af zodra de waarde in Exact staat",
                  "Zo'n zaak komt altijd bij u, en de assistent heeft er zelf niets voor voorbereid of gevraagd.",
                  STRINGS["nl"]["hand_put_back_other"].format(old="12,50", source=STRINGS["nl"]["hand_put_back_source:marks"], value="12,70")):
        assert words in manual["7"], words
    assert "—" not in whole


def test_the_documents_say_what_the_second_review_round_of_task_43_ruled():
    """The manual after the second review round of Task 43: a print that shows nothing new is a look at the order at the time it
    was given, and the sentences that stated the old limit are gone (1, 2.6, 3.1, 3.10, 4.9, 6 and the Dutch guide, W16); a line of
    an article that stands on several lines is told by its values (2.6 and 3.10, W17); a print that cannot be matched ends the
    tick (2.6, W18); the pre-check judges a stored match whole and counts a prepared change only while it stands on the list
    (2.5, W19 and W21); a row that a print put back says so (2.6 and the guide, W20); the tab's sentence for a list that cannot
    be understood (2.6 and the guide, W22); and the unread sentence beside a guessed order (2.2, W25): in the words of the page."""
    from aid.tests.test_hand_tab import manual_sections

    manual, whole = manual_sections(), Path("aid/MANUAL.md").read_text(encoding="utf-8")
    nl = STRINGS["nl"]
    for words in ("an export in which the lines of the order differ from the export before, is filed", "so it changes nothing here",
                  "(a PDF for a PDF, an export for an export)", "Dit is niet gelukt (ListUnread)", "as the reason of each of its items"):
        assert words not in whole, words
    for words in ("A print that shows nothing new counts as well", "A file that stays in the watched folder was given once",
                  "agrees with the line it was asked for in its quantity, its delivery date and its unit price, the changed value apart",
                  "The changes of two deliveries of one article are rows of their own, and one tick never covers both.",
                  "It holds for a print that shows nothing new as well", "A print in which the line of the tick cannot be found ends the tick too",
                  "When one of several lines of the article shows the ticked value, the print does not contradict the tick and it stands.",
                  f"prijs 12,50 → 12,70. {nl['hand_tick_ended'].format(value='12,50')}\"", f"\"{nl['hand_tick_ended_unfound']}\"",
                  f"\"{nl['hand_list_changed']}\"", "an export counts whether its lines differ from the export before or not", "Never save that file from a spreadsheet."):
        assert words in manual["2.6"], words
    assert "as the reason of its items, or of one item of its own when the order was also found by a guess" in manual["2.2"]
    for words in ("counts as ended at once, also before the runner's next pass", "A cart that agreed with its order as it was printed is judged the same way",
                  "counts as prepared only while it stands on the list", "no cart is green while the list cannot be read"):
        assert words in manual["2.5"], words
    assert "given.txt (empty) once a print that shows the PO so was given again" in manual["3.1"]
    for words in ("`PoStore.given_at`", "`given.txt`", "the changes of two deliveries are never one row", "`held_now`",
                  "or its line cannot be found there and no line of its article shows its value"):
        assert words in manual["3.10"], words
    assert "and still counts as such a print: it is a look at the order at the time it was given" in manual["4.9"]
    assert "the time of an empty `given.txt`" in manual["6"] and "A file that stays in `inbox/` is given once" in manual["6"]
    for words in ("Dat geldt ook voor een export waarin niets is veranderd", f"\"{nl['hand_tick_ended'].format(value='12,50')}\"",
                  f"\"{nl['hand_tick_ended_unfound']}\"", f"\"{nl['hand_list_changed']}\"", "Sla dat bestand nooit op vanuit een rekenblad.",
                  "sluit het programma dat het bestand open heeft en druk op \"Lijst vernieuwen\"",
                  "\"de lijst In Exact invoeren zegt nu iets anders\""):
        assert words in manual["7"], words
    assert "—" not in whole
# --- a question asked on the page is sent, and a reply belongs to its question (Task 44) ---

import base64  # noqa: E402
import smtplib  # noqa: E402

import requests  # noqa: E402

from aid.connections.microsoft import MicrosoftAccount  # noqa: E402
from aid.connections.secrets import SecretStore  # noqa: E402
from aid.pipeline import file_answer, read_with_parser  # noqa: E402
from aid.review.page import decision_text  # noqa: E402
from aid.settings import update_settings_file  # noqa: E402
from aid.tests.fakes import ACCESS_TOKEN, CLIENT_ID, TENANT_ID, USERNAME, FakeApp, FakeSession  # noqa: E402
from aid.tests.test_microsoft_mailer import graph  # noqa: E402

MAILBOX = {"ms_tenant_id": TENANT_ID, "ms_client_id": CLIENT_ID}       # the app registration of the fake sign-in
WAITING = {"nl": "De zaak is nu: wacht op de leverancier.", "en": "The case is now: waiting for the supplier."}
NOT_SENT = "De zaak is nu: ter beoordeling. Een bericht is niet verzonden; de zaak blijft ter beoordeling."
# What the page says after a message that was not sent, by the kind of its cause, and after one written as a file by the settings'
# choice (the review round of Task 44; the tests of that round, further on, spell every sentence out).
LAPSED = f"{NOT_SENT} De aanmelding bij Microsoft is verlopen: meld u opnieuw aan onder Instellingen, Postvak en AI, en sluit de zaak opnieuw af."
NO_MAILBOX = f"{NOT_SENT} Er is geen postvak verbonden: verbind een postvak onder Instellingen, Postvak en AI, en sluit de zaak opnieuw af."
STORE = (f"{NOT_SENT} De beveiligde opslag van de aanmelding op deze pc is geweigerd: er is niets verzonden. Onder Instellingen, Postvak "
         "en AI, staat waarom en wat u kunt doen; sluit de zaak daarna opnieuw af.")


def filed(language: str, folder: str = "cases/outbox") -> str:
    return STRINGS[language]["message_filed"].format(folder=folder)


@pytest.fixture
def smtp_server(monkeypatch):
    """smtplib.SMTP replaced by a stand-in that connects to nothing, accepts every recipient and keeps the e-mails it is given
    after DATA, parsed, which the fixture returns; the password of the aid's mailbox is a made-up one in the environment."""
    taken = []

    class Server:
        does_esmtp = True

        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            pass

        def ehlo_or_helo_if_needed(self):
            pass

        def has_extn(self, name):
            return False

        def mail(self, sender, options=()):
            return 250, b"2.1.0 ok"

        def rcpt(self, recipient, options=()):
            return 250, b"2.1.5 ok"

        def docmd(self, command, args=""):
            return (354, b"go ahead") if command == "data" else (250, b"2.0.0 ok")

        def send(self, data):
            taken.append(message_from_bytes(data[:-3], policy=policy.default))

        def getreply(self):
            return 250, b"2.0.0 queued"

    monkeypatch.setattr(smtplib, "SMTP", Server)
    monkeypatch.setenv("AID_MAIL_PASSWORD", uuid.uuid4().hex)
    return taken


def sending_page(tmp_path, madeup_out, app: FakeApp | None = None, session: FakeSession | None = None, environ=None, share: bool = False, **values):
    """The review page as its command builds it, on a made-up settings file with S07's made-up address and `values`: the page makes the
    mailer of each close from the settings as they are then (`environ` is where the password of the aid's mailbox is looked up; none
    by default), with a fake sign-in and a fake Graph. The mailer its Deps were given writes into tmp_path/given, which a page that
    builds its own never uses. Returns (the settings file, the Deps, the page, the account of the sign-in)."""
    data = json.loads(SETTINGS.model_dump_json()) | {"cases_root": "cases", "po_store_root": "po_store", "watched_folder": "inbox",
                                                       "reader": "parser", "supplier_emails": {"S07": SUPPLIER}} | values
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    d = deps(tmp_path, madeup_out).model_copy(update={"settings": load_settings(path),
                                                      "mailer": FileMailer(tmp_path / "given", sender="aid@example.com", clock=lambda: NOW)})
    store = SecretStore(tmp_path / "secrets")
    account = MicrosoftAccount(TENANT_ID, CLIENT_ID, store, app=(app or FakeApp()).bind(store.token_cache()))
    page = build_app(d, settings_file=path, share=share, account_factory=lambda tenant, client: account, session=session or FakeSession(),
                     environ={} if environ is None else environ)
    return path, d, page, account


def pressed_close(page, case, language: str = "nl") -> tuple[str, str | None]:
    """"Zaak afsluiten" pressed on the page for the case: what the page says, and the case its dropdown then shows (None once the
    case has left the review)."""
    [close] = [f.fn for f in page.fns.values() if getattr(f.fn, "__name__", "") == "close"]
    text, choice = close(case.case_id, language)
    return text, choice["value"]


def asked_on_the_page(d, madeup_out, now: datetime = NOW):
    """MU07 in review with "Leverancier vragen" on its price: the case."""
    c, _ = run(d, madeup_out, "MU07", "250107", now=now)
    decide(c, "RI1", "ask")
    return c


def sent_lines(c) -> list[dict]:
    path = c.root / "messages" / "sent.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def changed_on_disk(path, **values) -> None:
    """The settings file with these values, as a save on the settings tab writes them, and a modification time that differs."""
    before = path.stat().st_mtime_ns
    update_settings_file(path, values)
    os.utime(path, ns=(before + 2_000_000_000, before + 2_000_000_000))


def test_a_close_sends_its_question_through_the_mailer_of_the_settings(tmp_path, madeup_out, smtp_server):
    """The page as its command builds it sends a close's question the way the runner sends its own: through the mailer that send_via
    names, made from the settings as they are at the close. With the SMTP server set, the question goes to it (a stand-in here), to
    S07's made-up address with the buyer in copy; nothing is written as a file, the mailer the Deps were given is not used, and the
    page says the new status and no more. A change of the settings file counts for the next close without a restart: with send_via
    "microsoft" the next question goes out through the work mailbox of the account signed in (a fake Graph)."""
    mailbox = graph()
    path, d, page, _ = sending_page(tmp_path, madeup_out, app=FakeApp(accounts=[USERNAME]), session=mailbox, environ=os.environ,
                                    send_via="smtp", smtp_host="smtp.example.com", smtp_user="aid@example.com")
    c = asked_on_the_page(d, madeup_out)
    assert pressed_close(page, c) == (WAITING["nl"], None)
    [mail] = smtp_server
    assert (mail["To"], mail["Cc"], mail["X-Aid-Template"], mail["X-Aid-Case"]) == (SUPPLIER, SETTINGS.buyer_email, "missing_info", c.case_id)
    [message] = c.read_step("result")["messages"]
    assert (message["rule_id"], message["status"], message["sent_at"]) == ("B01", "sent", "2026-11-12T09:00:00")      # the page's clock
    assert mail["Message-ID"] == f"<{message['message_id']}@example.com>" and [line["message_id"] for line in sent_lines(c)] == [message["message_id"]]
    assert c.read_step("result")["status"] == "waiting" and mailbox.calls == []
    assert not (tmp_path / "cases" / "outbox").exists() and not (tmp_path / "given").exists()
    changed_on_disk(path, send_via="microsoft", **MAILBOX)
    second = asked_on_the_page(d, madeup_out, now=LATER)
    assert pressed_close(page, second, "en") == (WAITING["en"], None)
    create, send = mailbox.calls
    mime = message_from_bytes(base64.b64decode(create["data"]), policy=policy.default)
    assert (mime["From"], mime["To"], mime["Cc"], mime["X-Aid-Case"]) == (USERNAME, SUPPLIER, SETTINGS.buyer_email, second.case_id)
    assert create["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}" and send["url"].endswith("/send")
    [line] = sent_lines(second)
    assert line["internet_message_id"] == mime["Message-ID"] and len(smtp_server) == 1 and not (tmp_path / "cases" / "outbox").exists()


def test_a_sign_in_counts_for_the_next_close_without_a_restart(tmp_path, madeup_out):
    """send_via "auto" with a folder of the work mailbox chosen and nobody signed in: there is no way to send, so nothing is asked
    and nothing written as a file; the case stays with the buyer and the page says that no mailbox is connected (ruling S4 of the
    review round). After the sign-in (on the account the page's settings tab signs in with) the next close of that case sends its
    question through the mailbox, without a restart of the page."""
    mailbox = graph()
    path, d, page, account = sending_page(tmp_path, madeup_out, session=mailbox, mail_folder="AAMkADMadeUpFolder0001=", **MAILBOX)
    first = asked_on_the_page(d, madeup_out)
    assert pressed_close(page, first) == (NO_MAILBOX, first.case_id)
    assert not (tmp_path / "cases" / "outbox").exists() and mailbox.calls == [] and sent_lines(first) == []
    account.sign_in_browser(["Mail.Send"])
    assert pressed_close(page, first) == (WAITING["nl"], None)
    assert len(mailbox.calls) == 2 and not (tmp_path / "cases" / "outbox").exists()
    assert sent_lines(first)[0]["internet_message_id"]


def test_a_question_that_could_not_be_sent_keeps_the_case_in_review(tmp_path, madeup_out):
    """The sign-in of the work mailbox has lapsed when the buyer closes the case: the page does what the run does for its own
    messages. The question is recorded as not sent with the class of the error, the case stays in review, and the page says so by
    its fixed sentences, never by the error's text. Nothing is recorded as asked: no line in sent.jsonl, no row "ask the supplier",
    the item not carried out, and no file written in its place. After a new sign-in the same close sends the question, and the case
    waits."""
    mailbox, application = graph(), FakeApp(accounts=[USERNAME], silent=None)
    path, d, page, _ = sending_page(tmp_path, madeup_out, app=application, session=mailbox, send_via="microsoft", **MAILBOX)
    c = asked_on_the_page(d, madeup_out)
    assert pressed_close(page, c) == (LAPSED, c.case_id)
    result = c.read_step("result")
    assert result["status"] == "review" and [(m["template"], m["status"]) for m in result["messages"]] == [("missing_info", "not sent (SignInLapsed)")]
    record = c.read_step("close")["records"][-1]
    assert record["carried_out"] == {} and record["messages"][0]["error"].startswith("SignInLapsed: SignInNeeded: ")
    assert sent_lines(c) == [] and rows(c) == [] and mailbox.calls == []
    assert not (tmp_path / "cases" / "outbox").exists() and not (tmp_path / "given").exists()
    assert [s.case_id for s in list_open_cases(d.settings.cases_root)] == [c.case_id]
    application.silent = {"access_token": ACCESS_TOKEN, "token_type": "Bearer"}             # signed in again
    assert pressed_close(page, c) == (WAITING["nl"], None)
    assert len(mailbox.calls) == 2 and len(sent_lines(c)) == 1 and rows(c) == [("1", "price", "ask the supplier (B01)", "page 1, line 8")]


class NoConnection(FakeSession):
    """A Graph that cannot be reached: every request fails as requests fails without a network."""

    def post(self, url, headers=None, data=None, timeout=None, **kwargs):
        self.calls.append({"method": "POST", "url": url})
        raise requests.ConnectionError("made-up detail: graph.microsoft.com unreachable")


@pytest.mark.parametrize("mailbox, error", [(NoConnection, "MailerError: ConnectionError: could not create the draft"),
                                            (lambda: graph(created=403), "MailerError: HTTP 403: could not create the draft")],
                         ids=["no connection", "a refusal"])
def test_a_question_the_mailbox_cannot_take_keeps_the_case_in_review(tmp_path, madeup_out, mailbox, error):
    """No connection to the work mailbox, or its refusal: as a lapsed sign-in, the case stays in review with the page's sentence for a
    message that was not sent and the kind of its cause, and nothing is recorded as asked. The error's own text stays in the record
    of the close."""
    path, d, page, _ = sending_page(tmp_path, madeup_out, app=FakeApp(accounts=[USERNAME]), session=mailbox(), send_via="microsoft", **MAILBOX)
    c = asked_on_the_page(d, madeup_out)
    text, shown = pressed_close(page, c)
    cause = STRINGS["nl"]["not_sent:refused" if "403" in error else "not_sent:unreachable"]
    assert (text, shown) == (f"{NOT_SENT} {cause}", c.case_id) and "403" not in text and "ConnectionError" not in text
    record = c.read_step("close")["records"][-1]
    assert [(m["status"], m["error"]) for m in record["messages"]] == [("not sent (MailerError)", error)] and record["carried_out"] == {}
    assert c.read_step("result")["status"] == "review" and sent_lines(c) == [] and rows(c) == []


def test_a_store_of_the_sign_in_that_the_pc_refuses_leaves_the_question_unsent(tmp_path, madeup_out):
    """The page cannot make the account of the sign-in, because its store lies inside a git work tree, and the store the runner
    would take is one this process may not use either: the question is not sent, as while a sign-in has lapsed, and the case stays
    in review. The page says that the store of this PC was refused and where the settings tab says why (finding 2 of the owner's
    test), not that the sign-in lapsed, which no sign-in puts right; the record names neither the folder of the store nor its kind,
    and keeps the cause "store"."""
    path, d, _, _ = sending_page(tmp_path, madeup_out, send_via="microsoft", **MAILBOX)
    work_tree = tmp_path / "work-tree"
    (work_tree / ".git").mkdir(parents=True)
    default = tmp_path / "default-secrets"                   # where the conftest keeps the store of the PC for this test
    default.mkdir()
    (default / "backend").write_text("made-up-kind\n", encoding="utf-8")
    mailbox = graph()
    page = build_app(d, settings_file=path, environ={}, session=mailbox,
                     account_factory=lambda tenant, client: MicrosoftAccount(tenant, client, SecretStore(work_tree / "secrets")))
    c = asked_on_the_page(d, madeup_out)
    assert pressed_close(page, c) == (STORE, c.case_id) and STRINGS["nl"]["not_sent:store"] in STORE
    [message] = c.read_step("close")["records"][-1]["messages"]
    assert message["status"] == "not sent (SignInLapsed)" and message["error"].startswith("SignInLapsed: StoreRefused: ")
    assert message["cause"] == "store"
    assert str(tmp_path) not in message["error"] and "made-up-kind" not in message["error"]
    assert mailbox.calls == [] and sent_lines(c) == [] and not (tmp_path / "cases" / "outbox").exists() and not (work_tree / "secrets").exists()


@pytest.mark.parametrize("language", ["nl", "en"])
def test_a_question_written_as_a_file_is_said_on_the_page(tmp_path, madeup_out, language):
    """send_via "file", the owner's choice for a run in which nothing may leave: the question is written to cases/outbox, recorded
    as sent, and the case waits; after the close the page says that the message was not sent but written as a file, because the
    settings say so (ruling S4 of the review round; without that choice and without a way to send, nothing is written). A request
    to the approver written as a file is said in the same way."""
    path, d, page, _ = sending_page(tmp_path, madeup_out, send_via="file")
    c = asked_on_the_page(d, madeup_out)
    assert pressed_close(page, c, language) == (f"{WAITING[language]} {filed(language)}", None)
    [eml] = (tmp_path / "cases" / "outbox").glob("*.eml")
    mail = read_eml(eml)
    assert (mail["To"], mail["Cc"], mail["X-Aid-Case"]) == (SUPPLIER, SETTINGS.buyer_email, c.case_id)
    assert [line["message_id"] for line in sent_lines(c)] == [eml.stem] and c.read_step("result")["status"] == "waiting"
    assert not (tmp_path / "given").exists()
    confirmed, _ = run(d, madeup_out, "MU07", "250107", now=LATER)
    decide(confirmed, "RI1", "confirm")
    text, _ = pressed_close(page, confirmed, language)
    assert text == f"{STRINGS[language]['new_status'].format(status=STRINGS[language]['status:closed'])} {filed(language)}"
    assert len(list((tmp_path / "cases" / "outbox").glob("*.eml"))) == 2
    kept, _ = run(d, madeup_out, "MU07", "250107", now=LATER + timedelta(minutes=1))       # a close without a message says nothing of files
    decide(kept, "RI1", "keep")
    assert pressed_close(page, kept, language)[0] == STRINGS[language]["new_status"].format(status=STRINGS[language]["status:closed"])


def test_a_page_given_its_own_deps_keeps_the_mailer_it_was_given(tmp_path, madeup_out, smtp_server):
    """A page built with Deps of its own, as a test or the notebook builds it, sends through the mailer of those Deps, whatever the
    settings name; and so does a page on a public link, which never makes a mailer."""
    values = {"send_via": "smtp", "smtp_host": "smtp.example.com", "smtp_user": "aid@example.com"}
    path, d, _, _ = sending_page(tmp_path, madeup_out, **values)
    c = asked_on_the_page(d, madeup_out)
    assert pressed_close(build_app(d, settings_file=path), c) == (f"{WAITING['nl']} {filed('nl', 'given')}", None)
    shared = sending_page(tmp_path, madeup_out, environ=os.environ, share=True, **values)[2]
    second = asked_on_the_page(d, madeup_out, now=LATER)
    assert pressed_close(shared, second) == (f"{WAITING['nl']} {filed('nl', 'given')}", None)
    assert len(list((tmp_path / "given").glob("*.eml"))) == 2 and smtp_server == [] and not (tmp_path / "cases" / "outbox").exists()


def test_a_question_of_the_run_that_was_not_sent_is_asked_from_the_page(tmp_path, madeup_out):
    """MU17's question could not be sent when the runner checked the confirmation (the sign-in had lapsed, say): the case came to the
    buyer with "Bericht niet verzonden" on the missing line. Once the mailbox works, "Leverancier vragen" on that item and a close
    send the question of its field, and the case waits for the supplier."""
    out = tmp_path / "outbox"
    d = mailing(deps(tmp_path, madeup_out), out)
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"supplier_emails": {"S17": "sales@s17.example.com"}})})
    c, r = run(d.model_copy(update={"mailer": Unreachable()}), madeup_out, "MU17", "250117")
    assert r.status == "review" and [(i.item_id, i.reason) for i in r.review_items] == [("RI1", "message not sent")]
    assert page_rows(load_case_view(c).flags, "nl")[0][5] == "Bericht niet verzonden" and not out.exists()
    decide(c, "RI1", "ask")
    text, still_open = close_text(c, d, "nl")
    assert text.startswith(WAITING["nl"]) and not still_open
    [eml] = out.glob("*.eml")
    mail = read_eml(eml)
    assert (mail["To"], mail["X-Aid-Template"], mail["X-Aid-Case"]) == ("sales@s17.example.com", "line_missing_extra", c.case_id)
    assert [line["message_id"] for line in sent_lines(c)] == [eml.stem]


def two_differences(path, kind):
    """The parser's reading of MU17 (PO line 3 is missing) with the quantity of line 1 raised by 5, so that the rules ask two
    questions in one run: about the quantity (R06) and about the missing line (R07)."""
    doc = read_with_parser(path, kind)
    first = doc.lines[0]
    quantity = first.quantity.model_copy(update={"value": str(int(float(first.quantity.value)) + 5)})
    return doc.model_copy(update={"lines": [first.model_copy(update={"quantity": quantity}), *doc.lines[1:]]})


def answer(d, tmp_path, message_id: str, name: str = "antwoord.eml", at: datetime = NOW + timedelta(hours=2)) -> str | None:
    """A made-up reply to the message with this id, filed as the scan of the watched folder files it: the case id, or None."""
    reply = tmp_path / name
    reply.write_bytes(f"From: sales@supplier.example.com\nTo: aid@example.com\nSubject: Re: Vraag over orderbevestiging\n"
                      f"In-Reply-To: <{message_id}@example.com>\n\nHet klopt zo.\n".encode())
    return file_answer(d.settings.cases_root, reply, at)


@pytest.mark.parametrize("answered", [0, 1], ids=["the first question answered", "the second question answered"])
def test_a_reply_to_one_of_two_questions_leaves_the_case_waiting_and_confirms_nothing(tmp_path, madeup_out, answered):
    """One run asks S17 two questions. The supplier answers one of them, and the buyer reads the reply and closes the case: the other
    question has no reply of its own, so the close confirms nothing. It makes none of the operations that the answer was to allow (no
    confirmation attached, no status set), leaves the case waiting and the page says that a question is open. When the wait of that
    question ends, the case comes back, and then the close confirms the order."""
    out = tmp_path / "outbox"
    d = mailing(deps(tmp_path, madeup_out), out)
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"supplier_emails": {"S17": "sales@s17.example.com"}}), "reader": two_differences})
    c, r = run(d, madeup_out, "MU17", "250117")
    asked = [m["message_id"] for m in r.messages]
    assert r.status == "waiting" and sorted(m["template"] for m in r.messages) == ["line_missing_extra", "quantity_unit"]
    assert answer(d, tmp_path, asked[answered]) == c.case_id
    decide(c, "RI1", "keep")
    text, still_open = close_text(c, d, "nl")
    assert text == f"{WAITING['nl']} {STRINGS['nl']['question_open']}." and not still_open
    r2 = CaseResult.model_validate(c.read_step("result"))
    record = c.read_step("close")["records"][-1]
    assert r2.status == "waiting" and record["question_open"] is True and record["messages"] == []
    assert r2.write_entries == [] and WriteLog(c.root).entries() == [] and list_open_cases(d.settings.cases_root) == []
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 17, 9, 0), d.settings) == []
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 17, 9, 1), d.settings) == [(c.case_id, "no answer in 3 working days")]
    [escalated] = (c.root / "messages" / "escalated.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(escalated)["message_id"] == asked[1 - answered]
    decide(c, "RI2", "keep")
    r3 = close_case(c, d)
    assert r3.status == "closed" and [e.op.kind for e in r3.write_entries] == ["attach_document", "set_status"]


def test_an_item_left_on_ask_after_its_answer_keeps_the_case_in_review(tmp_path, madeup_out):
    """MU07 asked from the page and answered: the case comes back with the reply's item. The buyer acknowledges that item and leaves
    the price on "Leverancier vragen": no question is outstanding, so the close does not set the case to waiting, where nothing would
    ever bring it back. It stays in review, with nothing sent again, until the buyer decides the price otherwise."""
    out = tmp_path / "outbox"
    d = mailing(deps(tmp_path, madeup_out), out)
    c = asked_on_the_page(d, madeup_out)
    assert close_case(c, d).status == "waiting"
    [question] = sent_lines(c)
    assert answer(d, tmp_path, question["message_id"]) == c.case_id
    decide(c, "RI2", "keep")
    text, still_open = close_text(c, d, "nl")
    assert (text, still_open) == (STRINGS["nl"]["new_status"].format(status=STRINGS["nl"]["status:review"]), True)
    record = c.read_step("close")["records"][-1]
    assert record["status"] == "review" and record["messages"] == [] and record["carried_out"].keys() == {"RI2"}
    assert len(sent_lines(c)) == 1 and len(list(out.glob("*.eml"))) == 1
    assert [s.case_id for s in list_open_cases(d.settings.cases_root)] == [c.case_id]
    assert escalate_pending(d.settings.cases_root, NOW + timedelta(days=30), d.settings) == []
    decide(c, "RI1", "keep")
    assert close_case(c, d).status == "closed"


def test_an_item_left_on_ask_keeps_the_case_waiting_while_its_question_is_open(tmp_path, madeup_out):
    """MU12: the run's question and the buyer's own are both without a reply when the reply to the run's question is filed. The
    buyer reads it and closes: the price stays on "Leverancier vragen" and its question is still open, so the case waits, the page
    says that a question is open, and that question comes back when its own wait ends."""
    out = tmp_path / "outbox"
    d = mailing(deps(tmp_path, madeup_out), out)
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"supplier_emails": {"S12": "sales@s12.example.com"}})})
    c, r = run(d, madeup_out, "MU12", "250112")
    decide(c, "RI1", "ask"); decide(c, "RI2", "keep")
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {filed('nl', 'outbox')} {STRINGS['nl']['question_open']}.", False)
    first, second = [line["message_id"] for line in sent_lines(c)]
    assert answer(d, tmp_path, first) == c.case_id
    decide(c, "RI3", "keep")
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {STRINGS['nl']['question_open']}.", False)
    assert c.read_step("close")["records"][-1]["question_open"] is True and len(sent_lines(c)) == 2
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 17, 9, 1), d.settings) == [(c.case_id, "no answer in 3 working days")]
    assert json.loads((c.root / "messages" / "escalated.jsonl").read_text(encoding="utf-8").splitlines()[0])["message_id"] == second


def test_a_bounce_that_was_not_escalated_yet_keeps_the_case_waiting(tmp_path, madeup_out):
    """MU12 again: the run's question bounces while the case is in review for its other items, so nothing has told the buyer yet. The
    close does not confirm the order: the case waits, and the next escalation brings it back as bounced."""
    out = tmp_path / "outbox"
    d = mailing(deps(tmp_path, madeup_out), out)
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"supplier_emails": {"S12": "sales@s12.example.com"}})})
    c, r = run(d, madeup_out, "MU12", "250112")
    bounce = tmp_path / "bounce.eml"
    bounce.write_bytes(f"From: postmaster@example.com\nTo: aid@example.com\nSubject: Undeliverable: Vraag over orderbevestiging PO 250112\n"
                       f"In-Reply-To: <{r.messages[0]['message_id']}@example.com>\n\nNo such user.\n".encode())
    assert file_answer(d.settings.cases_root, bounce, NOW + timedelta(hours=1)) == c.case_id
    assert escalate_pending(d.settings.cases_root, NOW + timedelta(hours=1), d.settings) == []          # in review: not escalated
    decide(c, "RI1", "keep"); decide(c, "RI2", "keep")
    r2 = close_case(c, d)
    assert r2.status == "waiting" and r2.write_entries == [] and c.read_step("close")["records"][-1]["question_open"] is True
    assert escalate_pending(d.settings.cases_root, NOW + timedelta(hours=2), d.settings) == [(c.case_id, "message bounced")]


def test_a_try_out_sends_nothing(tmp_path, madeup_out, smtp_server):
    """A case made from a try-out document on the page that sends: with the SMTP server set, "Leverancier vragen" is refused, and a
    decision "ask" that stands in its decisions all the same sends nothing. The close asks no mailer: the stand-in server gets no
    e-mail, no file is written, nothing is recorded as sent, and the case stays in review. The guard is the try-out's alone: the
    supplier's own confirmation of the same order, asked on the same page, is sent."""
    from aid.decider import NoDecider
    from aid.pipeline import Deps
    from aid.tests.test_tryout import NOW as MADE, OPEN, give, outbox, results, runner_pass, tryout_settings
    from aid.tryout_mark import TRYOUT_NOT_SENT, tryout_case

    store = deps(tmp_path, madeup_out, settings=OPEN).store
    settings = tryout_settings(tmp_path, first_cases_review=5, send_via="smtp", smtp_host="smtp.example.com", smtp_user="aid@example.com")
    give(settings, store, "250108", "price_large")
    runner_pass(settings, store)                              # the runner of the tests' try-outs: a file mailer, which gets nothing
    [r] = results(settings)
    case = load_case(settings.cases_root, r.case_id)
    item = next(item.item_id for item in r.review_items if item.finding_id)
    assert r.status == "review" and tryout_case(case)
    path = tmp_path / "settings.json"
    path.write_text(settings.model_dump_json(), encoding="utf-8")
    d = Deps(settings=load_settings(path), store=store, reader=read_with_parser, decider=NoDecider(), writer=PreparedWriter(settings.cases_root / "prepared"),
             mailer=FileMailer(tmp_path / "given", sender=settings.aid_email, clock=lambda: MADE), clock=lambda: MADE)
    page = build_app(d, settings_file=path, environ=os.environ, session=FakeSession())
    assert decision_text(case, item, "ask", False, "nl") == (STRINGS["nl"]["tryout_nothing_sent"], False)
    assert not (case.root / "decisions.jsonl").exists()
    (case.root / "decisions.jsonl").write_text(json.dumps({"item_id": item, "decision": "ask", "article_update": False,
                                                           "at": "2026-11-12T09:30:00"}) + "\n", encoding="utf-8")
    assert pressed_close(page, case) == (STRINGS["nl"]["new_status"].format(status=STRINGS["nl"]["status:review"]), case.case_id)
    closed = case.read_step("result")
    assert closed["status"] == "review" and [(m["template"], m["status"]) for m in closed["messages"]] == [("missing_info", TRYOUT_NOT_SENT)]
    assert smtp_server == [] and outbox(settings) == [] and not (tmp_path / "given").exists()
    assert [found for found in (case.root / "messages").rglob("*")] == [] and case.read_step("close")["records"][-1]["carried_out"] == {}
    # the supplier's own confirmation of the same order, on the same page: its question is sent
    shutil.copy(madeup_out / "MU08_conf.pdf", settings.watched_folder)
    runner_pass(settings, store)
    [own] = [load_case(settings.cases_root, result.case_id) for result in results(settings) if result.case_id != r.case_id]
    items = own.read_step("result")["review_items"]
    asked = next(i["item_id"] for i in items if i["finding_id"])
    for i in items:
        decide(own, i["item_id"], "ask" if i["item_id"] == asked else "keep")
    assert pressed_close(page, own) == (WAITING["nl"], None)
    [mail] = smtp_server
    assert (mail["To"], mail["X-Aid-Case"]) == (settings.supplier_emails["S08"], own.case_id) and outbox(settings) == []
    assert not (tmp_path / "given").exists()


# --- the review round of Task 44: the page sends (rulings S1 to S10) and an item's own question (S11, S12, S14, S17, S20) ---

import gradio  # noqa: E402

import aid.messaging  # noqa: E402
from aid.messaging import HandOverUnconfirmed, MailerError, outstanding_questions  # noqa: E402
from aid.review.close import standing_decisions  # noqa: E402

REVIEW = {"nl": "De zaak is nu: ter beoordeling.", "en": "The case is now: for review."}
CLOSED = {"nl": "De zaak is nu: afgesloten.", "en": "The case is now: closed."}
UNSENT = {"nl": f"{REVIEW['nl']} Een bericht is niet verzonden; de zaak blijft ter beoordeling.",
          "en": f"{REVIEW['en']} A message was not sent; the case stays for review."}
CAUSES = {      # the kind of cause the page names after a message that was not sent, as the rulings give them
    "sign_in": ("De aanmelding bij Microsoft is verlopen: meld u opnieuw aan onder Instellingen, Postvak en AI, en sluit de zaak opnieuw af.",
                "The Microsoft sign-in has lapsed: sign in again under Settings, Mailbox and AI, and close the case again."),
    "unreachable": ("Het postvak of de mailserver was niet bereikbaar: sluit de zaak later opnieuw af.",
                    "The mailbox or the mail server could not be reached: close the case again later."),
    "refused": ("Het postvak of de mailserver weigerde het bericht: geef dit door aan de onderzoeker.",
                "The mailbox or the mail server refused the message: pass this on to the researcher."),
    "no_mailbox": ("Er is geen postvak verbonden: verbind een postvak onder Instellingen, Postvak en AI, en sluit de zaak opnieuw af.",
                   "No mailbox is connected: connect a mailbox under Settings, Mailbox and AI, and close the case again."),
    "no_password": ("Het wachtwoord van de mailserver is niet ingesteld: geef dit door aan de onderzoeker.",
                    "The mail server's password is not set: pass this on to the researcher."),
    "settings": ("De instellingen kunnen nu niet worden gelezen: er is niets verstuurd. Geef dit door aan de onderzoeker.",
                 "The settings cannot be read now: nothing was sent. Pass this on to the researcher."),
    "document": ("Het document van de zaak kan nu niet worden gelezen: er is niets verstuurd.",
                 "The case's document cannot be read now: nothing was sent."),
}
UNCONFIRMED = {"nl": ("Niet zeker of het bericht is verstuurd: het postvak gaf geen bevestiging. Kijk bij Verzonden items. Staat het er niet, "
                      "dan komt de zaak na de wachttijd bij u terug en stelt u de vraag opnieuw."),
               "en": ("Not sure whether the message was sent: the mailbox gave no confirmation. Look in Sent items. If it is not there, the "
                      "case comes back to you after the wait and you ask the question again.")}
UNREAD = {"nl": "Een antwoord bij deze zaak kan nu niet worden gelezen: sluit de zaak zo opnieuw af.",
          "en": "A reply filed with this case cannot be read now: close the case again in a moment."}
OPEN = {"nl": "Vraag aan de leverancier staat open.", "en": "A question to the supplier is open."}


def written(language: str, folder: str = "cases/outbox") -> str:
    """The page's sentence after a close whose message was written as a file into this folder, by the owner's choice."""
    return {"nl": f"Het bericht is niet verstuurd maar als bestand klaargezet ({folder}): de instellingen laten berichten als bestand schrijven.",
            "en": f"The message was not sent but written as a file ({folder}): the settings have messages written as files."}[language]


def not_sent(language: str, cause: str | None = None) -> str:
    """What the page says after a close whose message was not sent, with the sentence of its kind of cause."""
    return UNSENT[language] + (f" {CAUSES[cause][language == 'en']}" if cause else "")


class Clock:
    """A clock that a test moves; the Deps and their file mailer read the same one."""

    def __init__(self, now: datetime = NOW):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def world(tmp_path, madeup_out):
    """The Deps of a test that moves the time: a file mailer into tmp_path/outbox, a made-up address for every made-up supplier,
    and one clock for the run, the close and the mailer. Returns the Deps and the clock."""
    clock, d = Clock(), deps(tmp_path, madeup_out)
    settings = d.settings.model_copy(update={"supplier_emails": {f"S{n:02d}": f"sales@s{n:02d}.example.com" for n in range(1, 24)}})
    return d.model_copy(update={"settings": settings, "clock": clock,
                                "mailer": FileMailer(tmp_path / "outbox", sender="aid@example.com", clock=clock)}), clock


def questions(c) -> list[str]:
    """The message ids of the questions the case sent, in the order sent."""
    return [line["message_id"] for line in sent_lines(c) if line["template"] != "reapproval"]


def out(c, d) -> list[str]:
    """The message ids of the questions of the case that are still out."""
    return [question.message_id for question in outstanding_questions(c.root / "messages", d.settings.ask_wait_working_days)]


def bounce(d, tmp_path, message_id: str, at: datetime, name: str = "ndr.eml") -> str | None:
    """A made-up non-delivery notice of the message with this id, filed as the scan of the watched folder files it."""
    notice = tmp_path / name
    notice.write_bytes(f"From: postmaster@example.com\nTo: aid@example.com\nSubject: Undeliverable: Vraag over orderbevestiging\n"
                       f"In-Reply-To: <{message_id}@example.com>\n\nNo such user.\n".encode())
    return file_answer(d.settings.cases_root, notice, at)


def items(c) -> list[tuple[str, str]]:
    return [(item["item_id"], item["reason"]) for item in c.read_step("result")["review_items"]]


def shown_choices(page_or_deps, c, language: str = "nl") -> dict[str, str]:
    """The choice the page shows on each review item of the case: what its radios are set to when the case is opened."""
    page = page_or_deps if hasattr(page_or_deps, "fns") else build_app(page_or_deps)
    [show] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "show"]
    state = show.fn(c.case_id, language)[[type(block).__name__ for block in show.outputs].index("State")]
    return {item_id: decision["decision"] for item_id, decision in state["decisions"].items()}


def after_the_press(page, case, language: str = "nl") -> tuple[str, str | None]:
    """What the status line and the case list show once the browser has run everything that a press on "Zaak afsluiten" sets off,
    as the page wires it: the close, whose outputs are the status line and the case list, then every function wired to the change
    of a component whose value the close changed (the case list, when the case leaves the review), and the step chained after the
    close. An output that is an update without a value leaves its component as it is, as the browser leaves it."""
    functions = list(page.fns.values())
    [closing] = [f for f in functions if getattr(f.fn, "__name__", "") == "close"]
    line, cases = closing.outputs
    values = {line._id: "", cases._id: case.case_id}

    def value_of(block):
        return values.get(block._id, language if isinstance(block, gradio.Radio) else None)

    def take(outputs, results) -> list:
        changed = []
        for block, result in zip(outputs, results):
            if isinstance(result, dict) and result.get("__type__") == "update":
                if "value" not in result:
                    continue
                result = result["value"]
            if values.get(block._id) != result:
                changed.append(block)
            values[block._id] = result
        return changed

    changed = take(closing.outputs, closing.fn(*(value_of(block) for block in closing.inputs)))
    for block in changed:
        for wired in functions:
            if (block._id, "change") in [(target[0], target[1]) for target in wired.targets]:
                take(wired.outputs, wired.fn(*(value_of(given) for given in wired.inputs)))
    for chained in functions:
        if chained.trigger_after == closing._id:
            take(chained.outputs, chained.fn(*(value_of(given) for given in chained.inputs)))
    return values[line._id], values[cases._id]


def test_the_change_of_the_case_list_leaves_the_status_line_alone_when_no_case_is_chosen(tmp_path, madeup_out):
    """Ruling S1. A close that takes the case out of the review empties the case list, and that change runs the function that
    shows the chosen case. With no case chosen it gives no new text for the status line, so that what the close said stays on the
    screen; with a case chosen it empties the line, as before."""
    d = mailing(deps(tmp_path, madeup_out), tmp_path / "outbox")
    c, _ = run(d, madeup_out, "MU07", "250107")
    [show] = [f for f in build_app(d).fns.values() if getattr(f.fn, "__name__", "") == "show"]
    line = [type(block).__name__ for block in show.outputs].index("Markdown", len(show.outputs) - 1)
    for none in (None, ""):
        assert show.fn(none, "nl")[line] == {"__type__": "update"}
    assert show.fn(c.case_id, "nl")[line] == ""


@pytest.mark.parametrize("language", ["nl", "en"])
def test_what_the_page_says_after_a_close_stays_on_the_screen(tmp_path, madeup_out, language):
    """Ruling S1, the chain of events of a press on "Zaak afsluiten" as the page wires it, not the handler alone: the text of every
    close is still on the status line after the change of the case list that the close causes. The four cases of the finding: a
    question written as a file (send_via "file"), a case closed, a case that waits on an earlier question, and an undecided item."""
    path, d, page, _ = sending_page(tmp_path, madeup_out, send_via="file", supplier_emails={"S07": SUPPLIER, "S12": "sales@s12.example.com"})
    asked = asked_on_the_page(d, madeup_out)
    assert after_the_press(page, asked, language) == (f"{WAITING[language]} {written(language)}", None)
    kept, _ = run(d, madeup_out, "MU07", "250107", now=LATER)
    decide(kept, "RI1", "keep")
    assert after_the_press(page, kept, language) == (CLOSED[language], None)
    runner = d.model_copy(update={"mailer": FileMailer(tmp_path / "cases" / "outbox", sender="aid@example.com", clock=lambda: NOW)})
    open_question, _ = run(runner, madeup_out, "MU12", "250112")                 # the run's own question is out
    decide(open_question, "RI1", "keep"); decide(open_question, "RI2", "keep")
    assert after_the_press(page, open_question, language) == (f"{WAITING[language]} {OPEN[language]}", None)
    undecided, _ = run(d, madeup_out, "MU07", "250107", now=LATER + timedelta(minutes=1))
    assert after_the_press(page, undecided, language) == (REVIEW[language], undecided.case_id)


@pytest.mark.parametrize("language", ["nl", "en"])
@pytest.mark.parametrize("cause, application, mailbox", [
    ("sign_in", lambda: FakeApp(accounts=[USERNAME], silent=None), graph),
    ("unreachable", lambda: FakeApp(accounts=[USERNAME]), lambda: NoConnection()),
    ("unreachable", lambda: FakeApp(accounts=[USERNAME]), lambda: graph(created=503)),
    ("refused", lambda: FakeApp(accounts=[USERNAME]), lambda: graph(created=403)),
    ("refused", lambda: FakeApp(accounts=[USERNAME]), lambda: graph(sent=403)),
], ids=["sign-in lapsed", "no connection", "HTTP 503", "HTTP 403 at the draft", "HTTP 403 at the send"])
def test_a_question_that_could_not_be_sent_leaves_an_item_and_the_page_says_why(tmp_path, madeup_out, language, cause, application, mailbox):
    """Ruling S2. The close could not send its question: the page says so, with the kind of the cause and what to do (never a text
    of the server, an address or a status code), and the case gets the review item of the run for a message that was not sent,
    "Bericht niet verzonden", which is there after a reload. The item needs no decision of its own: once a later close sends the
    question it is settled, and the case waits."""
    session, signed = mailbox(), application()
    path, d, page, _ = sending_page(tmp_path, madeup_out, app=signed, session=session, send_via="microsoft", **MAILBOX)
    c = asked_on_the_page(d, madeup_out)
    text, chosen = after_the_press(page, c, language)
    assert (text, chosen) == (not_sent(language, cause), c.case_id)
    assert not re.search(r"\d{3}|Error|example|graph", text) and items(c) == [("RI1", "price above threshold"), ("RI2", "message not sent")]
    assert c.read_step("result")["review_items"][1]["finding_id"] is None and sent_lines(c) == []
    record = c.read_step("close")["records"][-1]
    assert record["carried_out"] == {} and record["unsent_questions"] == {"missing_info": "RI2"} and [m["cause"] for m in record["messages"]] == [cause]
    assert page_rows(load_case_view(c).flags, language)[-1][5] == STRINGS[language]["reason:message not sent"]      # after a reload
    assert shown_choices(page, c) == {"RI1": "ask"}
    text, chosen = after_the_press(page, c, language)                           # pressed again while it still fails: the same item
    assert (text, chosen) == (not_sent(language, cause), c.case_id) and [item_id for item_id, _ in items(c)] == ["RI1", "RI2"]
    decide(c, "RI2", "keep")                                                    # a choice on the item does not let the case go while the
    assert after_the_press(page, c, language) == (not_sent(language, cause), c.case_id)      # question is still not sent
    assert c.read_step("result")["status"] == "review" and sent_lines(c) == []
    signed.silent = {"access_token": ACCESS_TOKEN, "token_type": "Bearer"}
    page = sending_page(tmp_path, madeup_out, app=signed, session=graph(), send_via="microsoft", **MAILBOX)[2]      # the mailbox works again
    assert after_the_press(page, c, language) == (WAITING[language], None)
    record = c.read_step("close")["records"][-1]
    assert record["settled"] == ["RI2"] and record["unsent_questions"] == {} and record["carried_out"].keys() == {"RI1"}
    assert c.read_step("result")["status"] == "waiting" and len(sent_lines(c)) == 1 and len(items(c)) == 2


def test_a_question_without_an_address_leaves_the_item_of_the_run(tmp_path, madeup_out):
    """The settings hold no address for the supplier: the close records the question as not sent, as the run does, and the case
    gets the run's item for it, "Geen e-mailadres van de leverancier"; the page says what is missing (ruling C7 of the clean-up).
    With the address set, the next close sends it."""
    path, d, page, _ = sending_page(tmp_path, madeup_out, send_via="file", supplier_emails={})
    c = asked_on_the_page(d, madeup_out)
    assert after_the_press(page, c) == (f"{not_sent('nl')} {NO_ADDRESS['nl']}", c.case_id)
    assert items(c)[-1] == ("RI2", "no address for the supplier") and not (tmp_path / "cases" / "outbox").exists()
    changed_on_disk(path, supplier_emails={"S07": SUPPLIER})
    assert after_the_press(page, c) == (f"{WAITING['nl']} {written('nl')}", None)
    assert c.read_step("close")["records"][-1]["settled"] == ["RI2"]


def spoiled(path, how: str) -> None:
    """The settings file as an edit by hand leaves it when it goes wrong: a value that does not validate, a file cut in the
    middle, or the file moved away."""
    before = path.stat().st_mtime_ns
    text = path.read_text(encoding="utf-8")
    if how == "moved away":
        path.rename(path.with_name("settings.bak"))
        return
    path.write_text(text.replace('"send_via": "smtp"', '"send_via": "postduif"') if how == "a wrong value" else text[:len(text) // 2], encoding="utf-8")
    os.utime(path, ns=(before + 2_000_000_000, before + 2_000_000_000))


@pytest.mark.parametrize("how", ["a wrong value", "cut in the middle", "moved away"])
def test_a_close_while_the_settings_file_cannot_be_read_sends_nothing(tmp_path, madeup_out, smtp_server, how):
    """Ruling S3. The page was started with settings that send through the mail server. The owner edits the file by hand and
    leaves it unreadable: a close that has a question to send then sends nothing and writes no file, keeps the case in review with
    the item of a message not sent, and says that the settings cannot be read. A close without a message goes on as before. Once
    the file reads again, the next close sends by the settings as they are then."""
    path, d, page, _ = sending_page(tmp_path, madeup_out, environ=os.environ, send_via="smtp", smtp_host="smtp.example.com", smtp_user="aid@example.com")
    good = path.read_text(encoding="utf-8")
    c = asked_on_the_page(d, madeup_out)
    kept, _ = run(d, madeup_out, "MU07", "250107", now=LATER)
    decide(kept, "RI1", "keep")
    spoiled(path, how)
    assert after_the_press(page, c) == (not_sent("nl", "settings"), c.case_id)
    assert smtp_server == [] and sent_lines(c) == [] and not (tmp_path / "cases" / "outbox").exists() and not (tmp_path / "given").exists()
    assert items(c)[-1] == ("RI2", "message not sent") and c.read_step("close")["records"][-1]["messages"][0]["cause"] == "settings"
    assert after_the_press(page, kept) == (CLOSED["nl"], None)                  # nothing to send: as before
    path.write_text(good.replace('"send_via": "smtp"', '"send_via": "file"'), encoding="utf-8")
    os.utime(path, ns=(path.stat().st_mtime_ns + 4_000_000_000,) * 2)
    assert after_the_press(page, c) == (f"{WAITING['nl']} {written('nl')}", None)
    assert smtp_server == [] and len(list((tmp_path / "cases" / "outbox").glob("*.eml"))) == 1


@pytest.mark.parametrize("trouble", ["moved away and back", "one look that fails"])
def test_a_failed_look_at_the_settings_file_does_not_stick(tmp_path, madeup_out, smtp_server, monkeypatch, trouble):
    """Ruling C6. The settings file cannot be looked at for a moment and is then back as it was, the same bytes and the same time
    (a sync tool that moves it, a share that does not answer once): the close in that moment sends nothing and says that the
    settings cannot be read, and the next close looks at the file again, reads it and sends."""
    import aid.review.page as page_module
    path, d, page, _ = sending_page(tmp_path, madeup_out, environ=os.environ, send_via="smtp", smtp_host="smtp.example.com", smtp_user="aid@example.com")
    c = asked_on_the_page(d, madeup_out)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    if trouble == "moved away and back":
        path.rename(path.with_name("settings.away"))
        assert after_the_press(page, c) == (not_sent("nl", "settings"), c.case_id)
        path.with_name("settings.away").rename(path)
    else:
        real_modified, real_load, down = page_module._modified, page_module.load_settings, [True]

        def load(file):
            if down[0]:
                raise PermissionError(13, "made-up: the share does not answer")
            return real_load(file)
        monkeypatch.setattr(page_module, "_modified", lambda file: None if down[0] else real_modified(file))
        monkeypatch.setattr(page_module, "load_settings", load)
        assert after_the_press(page, c) == (not_sent("nl", "settings"), c.case_id)
        down[0] = False
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before and smtp_server == []
    assert after_the_press(page, c) == (WAITING["nl"], None)
    assert len(smtp_server) == 1 and len(sent_lines(c)) == 1


def test_with_send_via_auto_the_route_is_judged_once_per_message(tmp_path, madeup_out):
    """Ruling C8. send_via "auto", a folder chosen and the account signed in: the close judges the route once, and the mailer that
    judgement gives is the one that sends. When the store of the sign-in cannot be read at the next look (or the account is signed
    out right after the first), the mailbox cannot send: the question is not sent and the page says so. Nothing is written as a
    file through the file mailer that a second judgement would give for want of a route, and nothing counts as asked."""
    mailbox = graph()
    path, d, page, account = sending_page(tmp_path, madeup_out, app=FakeApp(accounts=[USERNAME]), session=mailbox,
                                          mail_folder="AAMkADMadeUpFolder0001=", **MAILBOX)
    c = asked_on_the_page(d, madeup_out)
    real, looks = account.account, []

    def flaky():
        looks.append(1)
        if len(looks) == 2:
            raise OSError("made-up: the store of the sign-in is held by another process")
        return real()
    account.account = flaky
    text, chosen = after_the_press(page, c)
    assert text.startswith(UNSENT["nl"]) and chosen == c.case_id
    assert mailbox.calls == [] and not list(tmp_path.rglob("*.eml")) and sent_lines(c) == [] and len(looks) == 2
    assert c.read_step("result")["status"] == "review" and items(c)[-1] == ("RI2", "message not sent")
    assert after_the_press(page, c) == (WAITING["nl"], None) and len(mailbox.calls) == 2      # the store reads again: sent


NO_ADDRESS = {"nl": "Er is geen e-mailadres van de leverancier: vul het in onder Instellingen, Leveranciers, en sluit de zaak opnieuw af.",
              "en": "There is no e-mail address for the supplier: fill it in under Settings, Suppliers, and close the case again."}


def shown_rows(c, language: str = "nl") -> list[tuple[str, str]]:
    """The rows of the review items the page shows for the case after a reload: (item, reason label)."""
    return [(row[0], row[5]) for row in page_rows(load_case_view(c).flags, language)]


def digest_of(c, d) -> list[str]:
    """The lines of the daily digest that holds the case, as the runner builds it from the saved result."""
    from datetime import date
    from aid.notify import build_digest
    digest = build_digest([CaseResult.model_validate(c.read_step("result"))], [], d.settings, date(2026, 11, 12)).text
    return [line.strip() for line in digest.splitlines() if line.strip()]


@pytest.mark.parametrize("language", ["nl", "en"])
def test_the_item_of_an_unsent_question_follows_its_question(tmp_path, madeup_out, language):
    """Ruling C7 (the check's test_c_items.py c1 and c2, and its browser run on the item that stayed). A fresh folder: nothing
    connected and no address for the supplier. The item of the question that could not be sent carries the reason of the latest
    failure, and the page names a missing address with a sentence of its own: first no mailbox ("Bericht niet verzonden"), then,
    the mailbox connected, no address ("Geen e-mailadres van de leverancier"), then, the address added and the sign-in lapsed,
    not sent again. Once the question was sent the item goes from the page, from the count of the case list and from the digest;
    the digest names the question once, whatever the number of attempts."""
    mailbox, signed = graph(), FakeApp(accounts=[USERNAME])
    path, d, page, account = sending_page(tmp_path, madeup_out, app=FakeApp(), session=mailbox, supplier_emails={})
    c = asked_on_the_page(d, madeup_out)
    assert after_the_press(page, c, language) == (not_sent(language, "no_mailbox"), c.case_id)
    assert shown_rows(c, language)[-1] == ("RI2", STRINGS[language]["reason:message not sent"])
    page = sending_page(tmp_path, madeup_out, app=signed, session=mailbox, supplier_emails={}, mail_folder="AAMkADMadeUpFolder0001=", **MAILBOX)[2]
    assert after_the_press(page, c, language) == (f"{UNSENT[language]} {NO_ADDRESS[language]}", c.case_id)
    assert items(c) == [("RI1", "price above threshold"), ("RI2", "no address for the supplier")]
    assert shown_rows(c, language)[-1] == ("RI2", STRINGS[language]["reason:no address for the supplier"])
    assert c.read_step("close")["records"][-1]["messages"][0]["cause"] == "no_address"
    changed_on_disk(path, supplier_emails={"S07": SUPPLIER})
    signed.silent = None                                                    # the sign-in lapses
    assert after_the_press(page, c, language) == (not_sent(language, "sign_in"), c.case_id)
    assert items(c)[-1] == ("RI2", "message not sent") and len(items(c)) == 2
    signed.silent = {"access_token": ACCESS_TOKEN, "token_type": "Bearer"}
    assert after_the_press(page, c, language) == (WAITING[language], None)
    [question] = sent_lines(c)
    assert answer(d, tmp_path, question["message_id"]) == c.case_id                # the reply brings the case back to the buyer
    assert [row[0] for row in shown_rows(c, language)] == ["RI1", "RI3"]
    assert [summary.items for summary in list_open_cases(d.settings.cases_root)] == [2]
    digest = digest_of(c, d)
    assert not any("Bericht niet verzonden" in line or "Geen e-mailadres" in line for line in digest)
    c.write_step(8, "result", CaseResult.model_validate(c.read_step("result")).model_copy(update={"status": "waiting"}))
    assert sum(line.count("ontbrekende gegevens") for line in digest_of(c, d)) == 1


def test_an_unsent_question_is_settled_when_its_item_is_decided_otherwise(tmp_path, madeup_out):
    """Ruling C7 (the check's test_c_items.py c3). Nothing connected: the question about the price is not sent and the case gets
    the item "Bericht niet verzonden". The buyer, told that no mailbox is connected, keeps the PO's value on the price instead: no
    item asks that question any more, so its unsent item is settled with it, and the case closes without a choice on that item."""
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    c = asked_on_the_page(d, madeup_out)
    assert after_the_press(page, c) == (NO_MAILBOX, c.case_id) and items(c)[-1] == ("RI2", "message not sent")
    decide(c, "RI1", "keep")
    assert after_the_press(page, c) == (CLOSED["nl"], None)
    record = c.read_step("close")["records"][-1]
    assert record["settled"] == ["RI2"] and record["unsent_questions"] == {} and sent_lines(c) == []
    assert [e["op"]["kind"] for e in record["write_entries"]] == ["attach_document", "set_status"]


def test_a_choice_made_on_the_unsent_item_ends_with_it(tmp_path, madeup_out):
    """Ruling C7: a settled item has no row any more, so what was chosen on it counts no longer. "Onopgelost" chosen on the item
    "Geen e-mailadres van de leverancier" does not keep the case in review once its question was sent: the case waits on it."""
    path, d, page, _ = sending_page(tmp_path, madeup_out, send_via="file", supplier_emails={})
    c = asked_on_the_page(d, madeup_out)
    assert after_the_press(page, c) == (f"{not_sent('nl')} {NO_ADDRESS['nl']}", c.case_id)
    decide(c, "RI2", "unresolved")
    changed_on_disk(path, supplier_emails={"S07": SUPPLIER})
    assert after_the_press(page, c) == (f"{WAITING['nl']} {written('nl')}", None)
    assert c.read_step("result")["review_items"][1]["settled"] is True and [row[0] for row in shown_rows(c)] == ["RI1"]


@pytest.mark.parametrize("language", ["nl", "en"])
@pytest.mark.parametrize("values, cause", [({}, "no_mailbox"), (MAILBOX, "no_mailbox"), (MAILBOX | {"mail_folder": "AAMkADMadeUpFolder0001="}, "no_mailbox"),
                                           ({"smtp_host": "smtp.example.com", "smtp_user": "aid@example.com"}, "no_password"),
                                           ({"send_via": "smtp"}, "no_mailbox")],
                         ids=["nothing connected", "a sign-in without a folder", "a folder and nobody signed in", "no password", "smtp without a server"])
def test_for_want_of_a_route_nothing_is_asked(tmp_path, madeup_out, language, values, cause):
    """Ruling S4 (b). The settings name a way to send that is not there: the page's close writes no file and counts nothing as
    asked. It is a message not sent: the case stays in review with its item, and the page says what is missing."""
    path, d, page, _ = sending_page(tmp_path, madeup_out, **values)
    c = asked_on_the_page(d, madeup_out)
    assert after_the_press(page, c, language) == (not_sent(language, cause), c.case_id)
    assert not (tmp_path / "cases" / "outbox").exists() and not (tmp_path / "given").exists() and sent_lines(c) == [] and rows(c) == []
    assert items(c)[-1] == ("RI2", "message not sent") and c.read_step("result")["status"] == "review"
    words = {"no_mailbox": "no mailbox connected", "no_password": "mail server password not set"}[cause]
    assert [(m["status"], m["cause"]) for m in c.read_step("close")["records"][-1]["messages"]] == [(f"not sent ({words})", cause)]


@pytest.mark.parametrize("language", ["nl", "en"])
def test_a_message_written_as_a_file_by_choice_counts_and_names_its_folder(tmp_path, madeup_out, language):
    """Rulings S4 (c) and S26. With send_via "file" the question is written to cases/outbox, counts as asked and the case waits; the
    page says that it was written as a file because the settings say so, and names the folder the mailer writes into: its path
    from the cases folder when it lies inside it, else its name. A close without a message says nothing of files."""
    path, d, page, _ = sending_page(tmp_path, madeup_out, send_via="file", **MAILBOX)
    c = asked_on_the_page(d, madeup_out)
    assert pressed_close(page, c, language) == (f"{WAITING[language]} {written(language)}", None)
    [eml] = (tmp_path / "cases" / "outbox").glob("*.eml")
    assert (read_eml(eml)["To"], read_eml(eml)["X-Aid-Case"]) == (SUPPLIER, c.case_id)
    assert [line["message_id"] for line in sent_lines(c)] == [eml.stem] and c.read_step("result")["status"] == "waiting"
    assert STRINGS[language]["message_filed"].format(folder="cases/outbox") == written(language) and not (tmp_path / "given").exists()
    for folder, words in ((tmp_path / "given", "given"), (tmp_path / "cases" / "uit" / "post", "cases/uit/post")):       # a page given its own mailer
        own = d.model_copy(update={"mailer": FileMailer(folder, sender="aid@example.com", clock=lambda: NOW)})
        second = asked_on_the_page(own, madeup_out, now=LATER + timedelta(minutes=len(words)))
        assert close_text(second, own, language) == (f"{WAITING[language]} {written(language, words)}", False)
        assert len(list(folder.glob("*.eml"))) == 1
    kept, _ = run(d, madeup_out, "MU07", "250107", now=LATER)
    decide(kept, "RI1", "keep")
    assert pressed_close(page, kept, language) == (CLOSED[language], None)


@pytest.mark.parametrize("language", ["nl", "en"])
def test_a_hand_over_that_was_not_confirmed_counts_as_asked_and_says_so(tmp_path, madeup_out, language):
    """Ruling S5. The draft is made and its send gets no answer in time: the message may have left. The close records it as sent
    with the note that the hand-over was not confirmed, under the id the supplier would have received, the case waits, and the
    page says to look in Sent items. Nothing is sent a second time, and a reply that names the message is filed with the case and
    is the answer to its question."""
    mailbox = graph()

    def no_answer(request):
        raise requests.ReadTimeout("made-up detail: no answer in time")
    [send_url] = [key for key in mailbox.answers if key[1].endswith("/send")]
    mailbox.answers[send_url] = [no_answer]
    path, d, page, _ = sending_page(tmp_path, madeup_out, app=FakeApp(accounts=[USERNAME]), session=mailbox, send_via="microsoft", **MAILBOX)
    c = asked_on_the_page(d, madeup_out)
    assert after_the_press(page, c, language) == (f"{WAITING[language]} {UNCONFIRMED[language]}", None)
    [message] = c.read_step("result")["messages"]
    [line] = sent_lines(c)
    assert (message["status"], message["unconfirmed"], line["unconfirmed"]) == ("sent", True, True) and message["message_id"] == line["message_id"]
    mime = message_from_bytes(base64.b64decode(mailbox.calls[0]["data"]), policy=policy.default)
    assert mime["Message-ID"] == line["internet_message_id"] and c.read_step("result")["status"] == "waiting"
    assert c.read_step("close")["records"][-1]["carried_out"].keys() == {"RI1"} and items(c) == [("RI1", "price above threshold")]
    assert pressed_close(page, c, language) == (STRINGS[language]["not_closed"], None) and len(mailbox.calls) == 2      # nothing sent again
    assert out(c, d) == [line["message_id"]]
    assert answer(d, tmp_path, line["message_id"]) == c.case_id and out(c, d) == [] and items(c)[-1] == ("RI2", "supplier answered: read the reply")


def test_a_reply_filed_while_the_close_is_sending_keeps_its_item(tmp_path, madeup_out):
    """Ruling S6 (the review's test_x8_race.py). MU12 after its run: the run's question is out and two items wait for the buyer,
    who asks about the first and keeps the second. While the close is sending that question, the runner files the supplier's reply
    to the run's question and adds its item to the saved result, and a message record besides. The close keeps both: the case stays
    in review with the reply's item, which the buyer reads; after that the case waits on the question the close sent."""
    d, clock = world(tmp_path, madeup_out)
    c, r = run(d, madeup_out, "MU12", "250112")
    [first] = questions(c)
    decide(c, "RI1", "ask"); decide(c, "RI2", "keep")

    class Slow(FileMailer):
        """The file mailer, during whose send the runner's pass files the reply to the run's own question."""

        def send(self, msg):
            assert answer(d, tmp_path, first) == c.case_id
            saved = CaseResult.model_validate(c.read_step("result"))
            c.write_step(8, "result", saved.model_copy(update={"messages": [*saved.messages, {"rule_id": "X", "template": "made_up", "status": "sent"}]}))
            return super().send(msg)

    text, still_open = close_text(c, d.model_copy(update={"mailer": Slow(tmp_path / "outbox", sender="aid@example.com", clock=clock)}), "nl")
    assert (text, still_open) == (f"{REVIEW['nl']} {written('nl', 'outbox')}", True)
    result = c.read_step("result")
    assert result["status"] == "review" and items(c) == [*((item.item_id, item.reason) for item in r.review_items),
                                                         ("RI3", "supplier answered: read the reply")]
    assert [m["template"] for m in result["messages"]] == ["quantity_unit", "made_up", "missing_info"]
    assert [s.case_id for s in list_open_cases(d.settings.cases_root)] == [c.case_id] and len(questions(c)) == 2
    assert shown_choices(d, c) == {"RI1": "ask", "RI2": "keep"}                 # its question is out: the choice stands
    decide(c, "RI3", "keep")
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {OPEN['nl']}", False)
    assert WriteLog(c.root).entries() == [] and out(c, d) == [questions(c)[1]]


def test_a_document_that_cannot_be_read_at_the_close_sends_nothing(tmp_path, madeup_out, smtp_server, monkeypatch):
    """Ruling S7. Whether the case is made from a try-out is known by its document alone. When that document cannot be read at the
    close (held by another program, or gone), the page cannot tell, and sends nothing: a message not sent, with the sentence that
    the document cannot be read. This holds for a try-out and for a supplier's own document alike; once the document reads again,
    the question of the supplier's document goes out."""
    import aid.tryout_mark as mark

    path, d, page, _ = sending_page(tmp_path, madeup_out, environ=os.environ, send_via="smtp", smtp_host="smtp.example.com", smtp_user="aid@example.com")
    c = asked_on_the_page(d, madeup_out)
    read = mark.extract_lines

    def held(document):
        raise PermissionError(13, "made-up: held by another program")
    monkeypatch.setattr(mark, "extract_lines", held)
    monkeypatch.setattr(mark, "READ_PAUSE", 0)
    mark._first_row_is_tryout.cache_clear()
    assert after_the_press(page, c) == (not_sent("nl", "document"), c.case_id)
    assert smtp_server == [] and sent_lines(c) == [] and items(c)[-1] == ("RI2", "message not sent")
    assert c.read_step("close")["records"][-1]["messages"][0]["status"] == "not sent (document cannot be read)"
    monkeypatch.setattr(mark, "extract_lines", read)
    gone = c.input_files[0].with_name("elders.pdf")
    c.input_files[0].rename(gone)                                               # the file is gone: nothing to judge by
    assert after_the_press(page, c) == (not_sent("nl", "document"), c.case_id) and smtp_server == []
    gone.rename(c.input_files[0])
    assert after_the_press(page, c) == (WAITING["nl"], None) and len(smtp_server) == 1 and c.read_step("close")["records"][-1]["settled"] == ["RI2"]


def test_a_try_out_whose_document_cannot_be_read_is_asked_about_nowhere(tmp_path, madeup_out, smtp_server, monkeypatch):
    """Ruling S7, the finding behind it: a try-out whose document cannot be read at the close, with an "ask" in its decisions, is
    not taken for a supplier's: nothing goes to the address the settings hold for the order's real supplier."""
    import aid.tryout_mark as mark
    from aid.decider import NoDecider
    from aid.pipeline import Deps
    from aid.tests.test_tryout import NOW as MADE, OPEN as TRYOUTS, give, outbox, results, runner_pass, tryout_settings

    store = deps(tmp_path, madeup_out, settings=TRYOUTS).store
    settings = tryout_settings(tmp_path, first_cases_review=5, send_via="smtp", smtp_host="smtp.example.com", smtp_user="aid@example.com")
    give(settings, store, "250108", "price_large")
    runner_pass(settings, store)
    [r] = results(settings)
    case = load_case(settings.cases_root, r.case_id)
    path = tmp_path / "settings.json"
    path.write_text(settings.model_dump_json(), encoding="utf-8")
    d = Deps(settings=load_settings(path), store=store, reader=read_with_parser, decider=NoDecider(), writer=PreparedWriter(settings.cases_root / "prepared"),
             mailer=FileMailer(tmp_path / "given", sender=settings.aid_email, clock=lambda: MADE), clock=lambda: MADE)
    page = build_app(d, settings_file=path, environ=os.environ, session=FakeSession())
    item = next(item.item_id for item in r.review_items if item.finding_id)
    (case.root / "decisions.jsonl").write_text(json.dumps({"item_id": item, "decision": "ask", "article_update": False,
                                                           "at": "2026-11-12T09:30:00"}) + "\n", encoding="utf-8")

    def held(document):
        raise PermissionError(13, "made-up: held by another program")
    monkeypatch.setattr(mark, "extract_lines", held)
    monkeypatch.setattr(mark, "READ_PAUSE", 0)
    mark._first_row_is_tryout.cache_clear()
    text, chosen = pressed_close(page, case)
    assert text == not_sent("nl", "document") and chosen == case.case_id
    assert smtp_server == [] and outbox(settings) == [] and not (tmp_path / "given").exists() and not (case.root / "messages" / "sent.jsonl").exists()


def test_a_cart_is_not_asked_about_at_the_close(tmp_path, madeup_out):
    """Ruling S9. decide refuses "Leverancier vragen" on a cart; an "ask" that stands in the decisions of a cart all the same (a
    line written by hand) is not sent by the close either: nothing is written or recorded as asked, and the case stays in review."""
    d, clock = world(tmp_path, madeup_out)
    c = cart_case(d, madeup_out, "CART01.html", "250107")
    c.write_step(8, "result", CaseResult.model_validate(c.read_step("result")).model_copy(update={"supplier": "S07"}))
    with pytest.raises(ValueError, match="a cart is not asked about"):
        decide(c, "RI1", "ask")
    (c.root / "decisions.jsonl").write_text(json.dumps({"item_id": "RI1", "decision": "ask", "article_update": False,
                                                        "at": "2026-11-12T09:30:00"}) + "\n", encoding="utf-8")
    assert close_text(c, d, "nl") == (REVIEW["nl"], True)
    record = c.read_step("close")["records"][-1]
    assert record["messages"] == [] and record["carried_out"] == {} and sent_lines(c) == [] and not (tmp_path / "outbox").exists()


def test_a_question_with_its_reply_and_a_bounce_is_answered(tmp_path, madeup_out):
    """Ruling S11 (the review's scen.py both). The supplier answers the run's question, and a non-delivery notice of the same
    question is filed besides (one of several recipients). The question has its reply: it is not outstanding and never comes back
    as bounced. The buyer reads the reply and closes: the order is confirmed, and the next pass has nothing to show."""
    d, clock = world(tmp_path, madeup_out)
    c, r = run(d, madeup_out, "MU17", "250117")
    [first] = questions(c)
    assert answer(d, tmp_path, first) == c.case_id and bounce(d, tmp_path, first, NOW + timedelta(hours=3)) == c.case_id
    assert out(c, d) == [] and (c.root / "messages" / "bounces" / "ndr.eml").exists()
    decide(c, "RI1", "keep")
    assert close_text(c, d, "nl") == (CLOSED["nl"], False)
    assert [e.op.kind for e in WriteLog(c.root).entries()] == ["attach_document", "set_status"]
    assert escalate_pending(d.settings.cases_root, NOW + timedelta(hours=4), d.settings) == [] and items(c) == [("RI1", "supplier answered: read the reply")]


def test_a_bounce_of_an_answered_question_does_not_come_back_after_a_later_question(tmp_path, madeup_out):
    """Ruling S11 (the review's scen2.py stalebounce). MU12: the reply and a bounce of the run's question are filed while the case is
    in review. The buyer keeps the reply's item and asks a question of his own: the pass after it shows no bounce, since the
    second question did not bounce and the first was answered; the case waits on the second question alone."""
    d, clock = world(tmp_path, madeup_out)
    c, r = run(d, madeup_out, "MU12", "250112")
    [first] = questions(c)
    assert answer(d, tmp_path, first) == c.case_id and bounce(d, tmp_path, first, NOW + timedelta(hours=3)) == c.case_id
    decide(c, "RI1", "ask"); decide(c, "RI2", "keep"); decide(c, "RI3", "keep")
    clock.now = NOW + timedelta(hours=4)
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {written('nl', 'outbox')}", False)
    clock.now += timedelta(minutes=1)
    assert escalate_pending(d.settings.cases_root, clock.now, d.settings) == [] and out(c, d) == [questions(c)[1]]
    assert c.read_step("result")["status"] == "waiting" and len(items(c)) == 3


@pytest.mark.parametrize("earlier", ["escalated", "answered"])
def test_an_item_left_on_ask_is_judged_by_its_own_question(tmp_path, madeup_out, earlier):
    """Ruling S12 (the review's scen.py leftask, both halves). MU12 with the run's question in its past, escalated in one half and
    answered in the other. The buyer asks about both of his items in one close, and the supplier answers the question about the
    first. The buyer reads the reply, keeps the first item and leaves the second on "Leverancier vragen": its own question is
    still out, so the case waits and the page says that a question is open, whatever became of the earlier questions. When that
    question's own wait ends, the case comes back, and the item shows no choice any more."""
    d, clock = world(tmp_path, madeup_out)
    c, r = run(d, madeup_out, "MU12", "250112")
    [first] = questions(c)
    if earlier == "escalated":
        decide(c, "RI1", "keep"); decide(c, "RI2", "keep")
        assert close_text(c, d, "nl") == (f"{WAITING['nl']} {OPEN['nl']}", False)
        clock.now = datetime(2026, 11, 17, 9, 1)
        assert escalate_pending(d.settings.cases_root, clock.now, d.settings) == [(c.case_id, "no answer in 3 working days")]
    else:
        assert answer(d, tmp_path, first) == c.case_id
    decide(c, "RI3", "keep"); decide(c, "RI1", "ask"); decide(c, "RI2", "ask")
    clock.now += timedelta(hours=1)
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {written('nl', 'outbox')}", False)
    asked = {line["template"]: line["message_id"] for line in sent_lines(c)[1:]}
    assert sorted(asked) == ["missing_info", "quantity_unit"] and out(c, d) == questions(c)[1:]
    clock.now += timedelta(hours=2)
    assert answer(d, tmp_path, asked["missing_info"], name="r1.eml", at=clock.now) == c.case_id
    on_price = next(item["item_id"] for item in c.read_step("result")["review_items"][:2] if item["field"] == "price")
    on_unit = "RI2" if on_price == "RI1" else "RI1"
    assert shown_choices(d, c).get(on_unit) == "ask" and on_price not in shown_choices(d, c)
    decide(c, "RI4", "keep")
    assert close_text(c, d, "nl") == (REVIEW["nl"], True)      # the price's own question has ended and nothing is chosen on it: undecided,
    decide(c, on_price, "keep")                                # whatever other question is still out
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {OPEN['nl']}", False)
    record = c.read_step("close")["records"][-1]
    assert (record["status"], record["question_open"], record["messages"]) == ("waiting", True, []) and out(c, d) == [asked["quantity_unit"]]
    clock.now = datetime(2026, 12, 14, 10, 0)
    assert escalate_pending(d.settings.cases_root, clock.now, d.settings) == [(c.case_id, "no answer in 3 working days")]
    assert json.loads((c.root / "messages" / "escalated.jsonl").read_text(encoding="utf-8").splitlines()[-1])["message_id"] == asked["quantity_unit"]
    assert on_unit not in shown_choices(d, c)


@pytest.mark.parametrize("ended", ["answered", "bounced", "no answer"])
def test_a_question_can_be_asked_again(tmp_path, madeup_out, ended):
    """Ruling S14. MU07: the buyer asks about the price on Thursday, and the question ends: the supplier answers, the message
    bounces, or the wait passes without an answer. The case is back with the buyer, and the page shows no choice on the price: its
    earlier "Leverancier vragen" was carried out and is over. An item on which nothing is chosen keeps the case in review, and
    sends nothing. Choosing "Leverancier vragen" again is a new decision: the close sends the question again, as a new question
    with a wait of its own, and the case waits on it; its reply ends that wait."""
    d, clock = world(tmp_path, madeup_out)
    c, _ = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "ask")
    assert close_case(c, d).status == "waiting"
    [first] = questions(c)
    assert shown_choices(d, c) == {"RI1": "ask"}                                # while its question is out, the choice stands
    if ended == "answered":
        assert answer(d, tmp_path, first) == c.case_id
        back = "supplier answered: read the reply"
    elif ended == "bounced":
        assert bounce(d, tmp_path, first, NOW + timedelta(minutes=5)) == c.case_id
        assert shown_choices(d, c) == {"RI1": "ask"}                            # not shown to the buyer yet: the next pass does that
        assert escalate_pending(d.settings.cases_root, NOW + timedelta(minutes=6), d.settings) == [(c.case_id, "message bounced")]
        back = "message bounced"
    else:
        assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 17, 9, 1), d.settings) == [(c.case_id, "no answer in 3 working days")]
        back = "no answer in 3 working days"
    assert items(c)[-1] == ("RI2", back) and shown_choices(d, c) == {} and standing_decisions(c, 3) == {}
    decide(c, "RI2", "keep")
    friday = clock.now = datetime(2026, 11, 20, 10, 0)
    assert close_text(c, d, "nl") == (REVIEW["nl"], True)                       # nothing chosen on the price: undecided
    assert len(questions(c)) == 1 and c.read_step("close")["records"][-1]["carried_out"].keys() == {"RI2"}
    decide(c, "RI1", "ask")
    assert shown_choices(d, c) == {"RI1": "ask", "RI2": "keep"}
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {written('nl', 'outbox')}", False)
    second = questions(c)[1]
    assert second != first and sent_lines(c)[1]["sent_at"] == friday.isoformat() and out(c, d) == [second]
    assert c.read_step("result")["status"] == "waiting" and len(list((tmp_path / "outbox").glob("*.eml"))) == 2
    assert [row[2] for row in rows(c)].count("ask the supplier (B01)") == 2
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 25, 10, 0), d.settings) == []      # its own three working days
    # A late e-mail about the first question brings the case back while the second is out: the item is judged by the latest
    # question about it, so its "Leverancier vragen" stands, and the close leaves the case waiting on that question.
    assert answer(d, tmp_path, first, name="laat.eml", at=datetime(2026, 11, 23, 9, 0)) == c.case_id
    assert items(c)[-1] == ("RI3", "supplier answered: read the reply") and shown_choices(d, c) == {"RI1": "ask", "RI2": "keep"}
    decide(c, "RI3", "keep")
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {OPEN['nl']}", False) and len(questions(c)) == 2
    assert answer(d, tmp_path, second, name="tweede.eml", at=datetime(2026, 11, 24, 9, 0)) == c.case_id
    assert out(c, d) == [] and items(c)[-1] == ("RI4", "supplier answered: read the reply")
    assert shown_choices(d, c) == {"RI2": "keep", "RI3": "keep"}
    assert escalate_pending(d.settings.cases_root, datetime(2026, 12, 14, 9, 0), d.settings) == []
    decide(c, "RI4", "keep"); decide(c, "RI1", "keep")
    assert close_text(c, d, "nl") == (CLOSED["nl"], False)


def test_asking_again_while_the_question_is_out_sends_nothing_new(tmp_path, madeup_out):
    """Ruling S14: while the question about an item is out, "Leverancier vragen" chosen once more on it asks nothing new. MU12:
    the buyer asks about his first item; the reply to the run's question brings the case back while that question is out. He
    chooses "Leverancier vragen" on the item again and keeps the rest: nothing is sent, the case waits on the question that is
    out, and when that one ends the item shows no choice."""
    d, clock = world(tmp_path, madeup_out)
    c, r = run(d, madeup_out, "MU12", "250112")
    decide(c, "RI1", "ask"); decide(c, "RI2", "keep")
    assert close_case(c, d).status == "waiting"
    first, second = questions(c)
    assert answer(d, tmp_path, first) == c.case_id
    decide(c, "RI1", "ask"); decide(c, "RI3", "keep")
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {OPEN['nl']}", False)
    assert questions(c) == [first, second] and c.read_step("close")["records"][-1]["messages"] == []
    assert answer(d, tmp_path, second, name="tweede.eml") == c.case_id
    assert shown_choices(d, c) == {"RI2": "keep", "RI3": "keep"}


@pytest.mark.parametrize("touch", ["the tick", "Houden and back"])
def test_a_choice_recorded_while_the_question_is_out_is_over_with_it(tmp_path, madeup_out, touch):
    """Ruling C1 (the check's scen4.py stale_tick and stale). MU19: the buyer asks about the price and leaves the other items
    for later, so the case stays in review while its question is out. He touches the price row again: he sets its tick "Ook de
    artikelprijs bijwerken", or clicks "Houden" and then "Leverancier vragen" again; each writes a line of decisions.jsonl. The
    answer comes before the next close. That line was recorded while the question was out, so it is over with the question: the
    row shows no choice, and the close with the other items kept sends nothing and leaves the case in review. A choice made after
    the answer is a new one, and the question goes out again."""
    d, clock = world(tmp_path, madeup_out)
    c, _ = run(d, madeup_out, "MU19", "250119")
    others = [item_id for item_id, _ in items(c) if item_id != "RI1"]
    decide(c, "RI1", "ask")
    assert close_case(c, d).status == "review"
    [first] = questions(c)
    if touch == "the tick":
        decide(c, "RI1", "ask", True)
    else:
        decide(c, "RI1", "keep"); decide(c, "RI1", "ask")
    assert shown_choices(d, c)["RI1"] == "ask"                                  # while the question is out, the choice stands
    assert answer(d, tmp_path, first) == c.case_id
    assert "RI1" not in shown_choices(d, c) and "RI1" not in standing_decisions(c, 3)
    lines = len((c.root / "decisions.jsonl").read_text(encoding="utf-8").splitlines())
    assert audit(c)["answers"][-1]["decisions"] == lines                         # the answer notes how far the decisions went
    for item_id in [*others, items(c)[-1][0]]:
        decide(c, item_id, "keep")
    clock.now = NOW + timedelta(hours=3)
    assert close_text(c, d, "nl") == (REVIEW["nl"], True) and questions(c) == [first]
    decide(c, "RI1", "ask")
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {written('nl', 'outbox')}", False) and len(questions(c)) == 2


@pytest.mark.parametrize("ended", ["answered", "bounced", "no answer"])
def test_a_choice_recorded_while_the_close_sends_is_over_with_its_question(tmp_path, madeup_out, ended):
    """Ruling C1 (the check's scen4.py during). MU07: while the close sends the question about the price, the buyer touches the
    row (the tick), which writes a line after the close read the decisions. When the question has ended (answered, bounced and
    shown, or without an answer in time) that line is over with it: the row shows no choice and nothing is sent again."""
    d, clock = world(tmp_path, madeup_out)
    c, _ = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "ask")

    class Touched:
        def send(self, message):
            decide(c, "RI1", "ask", True)
            return d.mailer.send(message)
    assert close_case(c, d.model_copy(update={"mailer": Touched()})).status == "waiting"
    [first] = questions(c)
    if ended == "answered":
        assert answer(d, tmp_path, first) == c.case_id
    elif ended == "bounced":
        assert bounce(d, tmp_path, first, NOW + timedelta(minutes=5)) == c.case_id
        assert escalate_pending(d.settings.cases_root, NOW + timedelta(minutes=6), d.settings) == [(c.case_id, "message bounced")]
    else:
        assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 17, 9, 1), d.settings) == [(c.case_id, "no answer in 3 working days")]
    assert shown_choices(d, c) == {} and standing_decisions(c, 3) == {}
    decide(c, "RI2", "keep")
    clock.now = datetime(2026, 11, 20, 10, 0)
    assert close_text(c, d, "nl") == (REVIEW["nl"], True) and questions(c) == [first]


def cut_off_run(d, madeup_out, monkeypatch, case: str, po_number: str):
    """The made-up case run once, cut off right before its result is written (the PC switched off), and run again, as the next pass
    of the runner runs a case without a result: the case."""
    import aid.pipeline as pipeline
    c = open_upload_case(d.settings.cases_root, [madeup_out / f"{case}_conf.pdf"], po_number, "confirmation", NOW)
    real = pipeline._Run.end

    def cut(self, number, payload):
        if number == 8:
            raise KeyboardInterrupt("made-up: the PC is switched off")
        return real(self, number, payload)
    with monkeypatch.context() as patched:
        patched.setattr(pipeline._Run, "end", cut)
        with pytest.raises(KeyboardInterrupt):
            run_case(c, d)
    run_case(c, d)
    return c


def test_after_a_run_cut_off_behind_its_question_the_close_waits_and_says_so(tmp_path, madeup_out, monkeypatch):
    """Ruling C2 (the check's scen4.py cutoff). MU17's run sent its question and was cut off before its result was written; run
    again it comes back in review with the one item of a run cut off, and its result holds no message. Which question is out is read
    from messages/sent.jsonl: the buyer's "Houden" on that item does not confirm the order over the question, the close waits and
    says that a question is open, and the wait of the question ends as any other."""
    d, clock = world(tmp_path, madeup_out)
    c = cut_off_run(d, madeup_out, monkeypatch, "MU17", "250117")
    result = c.read_step("result")
    assert result["status"] == "review" and result["messages"] == [] and items(c) == [("RI1", "run cut off after writes: check the write log")]
    [question] = questions(c)
    decide(c, "RI1", "keep")
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {OPEN['nl']}", False)
    record = c.read_step("close")["records"][-1]
    assert record["write_entries"] == [] and record["question_open"] is True and c.read_step("result")["status"] == "waiting"
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 17, 9, 1), d.settings) == [(c.case_id, "no answer in 3 working days")]
    assert out(c, d) == [] and questions(c) == [question]


def test_after_a_close_cut_off_behind_its_send_the_question_is_not_sent_again(tmp_path, madeup_out, monkeypatch):
    """Ruling C2 (the check's scen4.py killed). MU07: the close sends the question about the price and is cut off before its
    result and its record are saved (the page ended while it was at work). sent.jsonl names the item the question is about, so the
    next close, with "Leverancier vragen" still on the item, sends nothing while that question is out: the case waits on it, and
    says so. After its answer the item shows no choice."""
    import aid.review.close as close_module
    d, clock = world(tmp_path, madeup_out)
    c, _ = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "ask")
    with monkeypatch.context() as patched:
        patched.setattr(close_module._Close, "save", lambda self: (_ for _ in ()).throw(KeyboardInterrupt("made-up: the page is ended")))
        with pytest.raises(KeyboardInterrupt):
            close_case(c, d)
    [question] = questions(c)
    assert sent_lines(c)[0]["item_ids"] == ["RI1"] and c.read_step("result")["status"] == "review"
    clock.now = NOW + timedelta(minutes=5)
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {OPEN['nl']}", False)
    assert questions(c) == [question] and len(list((tmp_path / "outbox").glob("*.eml"))) == 1
    assert answer(d, tmp_path, question) == c.case_id and shown_choices(d, c) == {}


def rendered(app, state: dict) -> list:
    """The radios and the ticks that the decision rows of the review tab render for the case of `state`, as the page renders them."""
    from gradio.context import LocalContext
    [renderable] = app.default_config.renderables
    token = LocalContext.blocks_config.set(app.default_config)
    try:
        renderable.apply(state, "nl")
    finally:
        LocalContext.blocks_config.reset(token)
    keys = (f"decision-{state['case_id']}-", f"article-{state['case_id']}-")
    return [block for block in app.default_config.blocks.values() if str(getattr(block, "key", "")).startswith(keys)]


def rendered_choices(app, state: dict) -> dict[str, list[str]]:
    """The decisions the radios of the review tab offer on each item of the case of `state`, as the page renders them."""
    prefix = f"decision-{state['case_id']}-"
    return {str(block.key).removeprefix(prefix): [value for _, value in block.choices] for block in rendered(app, state)
            if isinstance(block, gradio.Radio)}


def test_a_row_on_which_a_choice_stands_takes_another_choice(tmp_path, madeup_out):
    """Ruling C7 (and C1 and C4: the buyer chooses again), found by the browser check of the clean-up: on the item of a question
    that could not be sent, "Leverancier vragen" stands, and the buyer chooses "Waarde van de inkooporder houden" instead. The
    radios and the tick of every row are rendered as taking input, also where a choice stands: the browser greys out a rendered
    component that holds a value unless it is told that it takes input."""
    d = deps(tmp_path, madeup_out)
    c, _ = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "ask")
    page = build_app(d)
    blocks = rendered(page, shown_state(page, c))
    assert sorted(type(block).__name__ for block in blocks) == ["Checkbox", "Radio"] and [block.value for block in blocks if isinstance(block, gradio.Radio)] == ["ask"]
    assert all(block.interactive is True for block in blocks)


def shown_state(page, c, language: str = "nl") -> dict:
    [show] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "show"]
    return show.fn(c.case_id, language)[[type(block).__name__ for block in show.outputs].index("State")]


@pytest.mark.parametrize("ended", ["bounced", "no answer"])
def test_after_a_bounce_or_a_silence_of_the_runs_question_the_buyer_can_ask_again(tmp_path, madeup_out, ended):
    """Ruling C4 (the check's scen4.py runasked). MU17: the run asks S17 itself, and its question bounces or gets no answer in
    time. The case comes back with the item "Bericht kwam terug" or "Geen antwoord binnen de wachttijd", which names that question.
    On it the page offers "Leverancier vragen", and the close then sends the question again as a new question with a wait of its
    own: the same template and items, to the address the settings give now (after a bounce, the corrected one). While it is out
    the choice stands; once it has ended the item shows no choice. On the item of a question asked on the page, and on the item of
    an answer, "Leverancier vragen" is not offered: the buyer asks again on the item the question was about."""
    d, clock = world(tmp_path, madeup_out)
    c, r = run(d, madeup_out, "MU17", "250117")
    [asked] = r.messages
    first = asked["message_id"]
    if ended == "bounced":
        assert bounce(d, tmp_path, first, NOW + timedelta(minutes=5)) == c.case_id
        assert escalate_pending(d.settings.cases_root, NOW + timedelta(minutes=6), d.settings) == [(c.case_id, "message bounced")]
        corrected = "orders@s17.example.com"
        d = d.model_copy(update={"settings": d.settings.model_copy(update={"supplier_emails": d.settings.supplier_emails | {"S17": corrected}})})
    else:
        assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 17, 9, 1), d.settings) == [(c.case_id, "no answer in 3 working days")]
        corrected = "sales@s17.example.com"
    [(item_id, reason)] = items(c)
    assert c.read_step("result")["review_items"][0]["question"] == first
    page = build_app(d)
    state = shown_state(page, c)
    assert state["again"] == ["RI1"] and rendered_choices(page, state) == {"RI1": ["confirm", "keep", "ask", "unresolved"]}
    decide(c, "RI1", "ask")
    clock.now = datetime(2026, 11, 18, 10, 0)
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {written('nl', 'outbox')}", False)
    second = sent_lines(c)[-1]
    assert questions(c) == [first, second["message_id"]] and second["to"] == [corrected] and second["item_ids"] == ["RI1"]
    assert (second["template"], second["sent_at"]) == (asked["template"], clock.now.isoformat())
    [message] = c.read_step("result")["messages"][1:]
    assert (message["template"], message["items"], message["finding_ids"], message["item_ids"]) == (
        asked["template"], asked["items"], asked["finding_ids"], ["RI1"])
    assert out(c, d) == [second["message_id"]] and shown_choices(d, c) == {"RI1": "ask"}
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 23, 10, 0), d.settings) == []      # its own three working days
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 23, 10, 1), d.settings) == [(c.case_id, "no answer in 3 working days")]
    assert items(c)[-1] == ("RI2", "no answer in 3 working days") and shown_choices(d, c) == {}
    assert rendered_choices(page, shown_state(page, c)) == {"RI1": ["confirm", "keep", "ask", "unresolved"], "RI2": ["confirm", "keep", "unresolved"]}
    with pytest.raises(ValueError, match="RI2 is about the whole case"):
        decide(c, "RI2", "ask")
    assert answer(d, tmp_path, second["message_id"], at=datetime(2026, 11, 23, 11, 0)) == c.case_id
    with pytest.raises(ValueError, match="RI3 is about the whole case"):
        decide(c, "RI3", "ask")


def test_a_reply_that_cannot_be_read_at_the_close_holds_the_case(tmp_path, madeup_out, monkeypatch):
    """Ruling S17 (the review's scen2.py unreadable). MU07: the first question was escalated, the buyer asked again and that
    question is out, and a late reply to the first lies with the case. At the close that reply cannot be read: nobody can say then
    which question it answers, so the close does nothing, the case stays in review and the page says to close again in a moment.
    Once the file reads again, the same close leaves the case waiting on the open question."""
    d, clock = world(tmp_path, madeup_out)
    c, _ = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "ask")
    assert close_case(c, d).status == "waiting"
    clock.now = datetime(2026, 11, 17, 9, 1)
    assert escalate_pending(d.settings.cases_root, clock.now, d.settings) == [(c.case_id, "no answer in 3 working days")]
    decide(c, "RI1", "ask"); decide(c, "RI2", "keep")
    assert close_case(c, d).status == "waiting"
    first, second = questions(c)
    assert answer(d, tmp_path, first, name="laat.eml", at=clock.now) == c.case_id
    late = c.root / "messages" / "replies" / "laat.eml"
    decide(c, "RI3", "keep"); decide(c, "RI1", "keep")
    before = (c.read_step("result"), WriteLog(c.root).entries(), len(c.read_step("close")["records"]))
    read = aid.messaging.named_ids

    def locked(eml):
        if Path(eml) == late:
            raise PermissionError(13, "made-up: the file is in use by another program")
        return read(eml)
    monkeypatch.setattr(aid.messaging, "named_ids", locked)
    for language in ("nl", "en"):
        assert close_text(c, d, language) == (f"{REVIEW[language]} {UNREAD[language]}", True)
    record = c.read_step("close")["records"][-1]
    assert (record["unread_reply"], record["carried_out"], record["messages"], record["write_entries"]) == (True, {}, [], [])
    assert (c.read_step("result"), WriteLog(c.root).entries()) == before[:2]
    assert shown_choices(d, c)["RI1"] == "keep"                                 # the page shows the choices as they were made
    monkeypatch.setattr(aid.messaging, "named_ids", read)
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {OPEN['nl']}", False) and WriteLog(c.root).entries() == []


def test_a_later_questions_bounce_ends_no_wait_that_still_runs(tmp_path, madeup_out):
    """Ruling S20 (the review's scen.py laterbounce). MU12: the run's question went out on Thursday at 9:00 and has no reply. At
    10:00 the buyer asks a second question, which bounces at 10:05 and comes back to him. He keeps everything and closes at
    10:35: the first question's own wait has not ended, so the order is not confirmed over it. The case waits, the page says that
    a question is open, and the first question comes back by itself when its three working days are over."""
    d, clock = world(tmp_path, madeup_out)
    c, r = run(d, madeup_out, "MU12", "250112")
    clock.now = NOW + timedelta(hours=1)
    decide(c, "RI1", "ask"); decide(c, "RI2", "keep")
    assert close_case(c, d).status == "waiting"
    first, second = questions(c)
    clock.now += timedelta(minutes=5)
    assert bounce(d, tmp_path, second, clock.now) == c.case_id
    assert escalate_pending(d.settings.cases_root, clock.now, d.settings) == [(c.case_id, "message bounced")] and out(c, d) == [first]
    clock.now += timedelta(minutes=30)
    decide(c, "RI3", "keep"); decide(c, "RI1", "keep")
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {OPEN['nl']}", False)
    assert WriteLog(c.root).entries() == [] and c.read_step("close")["records"][-1]["question_open"] is True
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 17, 9, 0), d.settings) == []
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 17, 9, 1), d.settings) == [(c.case_id, "no answer in 3 working days")]
    escalated = [json.loads(line)["message_id"] for line in (c.root / "messages" / "escalated.jsonl").read_text(encoding="utf-8").splitlines()]
    assert escalated == [second, first] and out(c, d) == []
    decide(c, "RI4", "keep")
    assert close_text(c, d, "nl") == (CLOSED["nl"], False)


def test_a_sign_in_on_the_settings_tab_counts_for_the_close(tmp_path, madeup_out):
    """Ruling S24 (the shared accounts). The page makes one Microsoft account for the tenant and client of the settings: the
    settings tab signs in with it and the close sends through it. Here each account the page makes has a store and an application
    of its own, so that a sign-in made through the tab's own handler reaches the close only when the two share the account."""
    from aid.tests.fakes import GRAPH, FakeResponse

    mailbox, made = graph(), []
    mailbox.answers[("GET", f"{GRAPH}/me/mailFolders?$top=100")] = [FakeResponse(json_data={"value": []})]      # the tab lists the folders
    path, d, _, _ = sending_page(tmp_path, madeup_out, send_via="microsoft", **MAILBOX)

    def factory(tenant, client):
        store = SecretStore(tmp_path / f"secrets-{len(made)}")
        made.append(MicrosoftAccount(tenant, client, store, app=FakeApp().bind(store.token_cache())))
        return made[-1]
    page = build_app(d, settings_file=path, environ={}, session=mailbox, account_factory=factory)
    c = asked_on_the_page(d, madeup_out)
    assert after_the_press(page, c) == (not_sent("nl", "sign_in"), c.case_id)
    [sign_in] = [f.fn for f in page.fns.values() if getattr(f.fn, "__name__", "") == "sign_in_browser"]
    list(sign_in(TENANT_ID, CLIENT_ID, "", "nl"))
    assert after_the_press(page, c) == (WAITING["nl"], None)
    assert len(made) == 1 and len([call for call in mailbox.calls if call["url"].endswith("/send")]) == 1
# --- a write made and not verified ---

class Unchecked:
    """A write route into a system that accepts every change and cannot check the changes of one kind: for those it raises
    UnverifiedWrite with the entry of the attempt, made, whose previous is the value it read before the write (`read`). It makes
    every other change (status "done")."""
    name = "unchecked"

    def __init__(self, kind: str, read: str, answered: bool = True):
        self.kind, self.read, self.answered = kind, read, answered

    def apply(self, op, case_id, rule_id, evidence, now):
        entry = WriteLogEntry(entry_id=uuid.uuid4().hex, case_id=case_id, op=op, status="done", previous=op.old_value, new=op.new_value,
                              rule_id=rule_id, evidence=evidence, timestamp=now.isoformat())
        if op.kind != self.kind:
            return entry
        failed = "verify: no answer" if self.answered else "write: no answer"       # with answered False the write itself got no answer
        raise (UnverifiedWrite if self.answered else UnknownWrite)(failed, entry.model_copy(update={"previous": self.read, "error": failed}))


NOT_VERIFIED_NL = "Wijziging in Exact gemaakt, maar de controle erna lukte niet: kijk in Exact of de waarde klopt"
NOT_VERIFIED_EN = "Change made in Exact, but the check after it failed: look in Exact whether the value is right"
NOT_SURE_NL = "Niet zeker of de wijziging in Exact is gemaakt: kijk in Exact welke waarde er staat"
NOT_SURE_EN = "Not sure whether the change was made in Exact: look in Exact which value is there"


def test_the_page_offers_the_undo_of_a_write_made_and_not_verified_and_its_put_back_names_the_value_read(tmp_path, madeup_out):
    """MU08 when the system accepts the new price of the order line and the route cannot check it; the document says 12.50 and the
    route read 12.60. The page shows the item by the label of its reason, in both languages, and the digest uses the same label;
    the write is among the writes to undo. The page's own writer only prepares, so its undo is a put-back on the list "In Exact
    invoeren", and that names the value read, 12,60, not the document's 12,50."""
    from datetime import date

    from aid.notify import build_digest

    d = deps(tmp_path, madeup_out, writer=RetryingWriter(Unchecked("po_line_price", read="12.60"), PreparedWriter(tmp_path)))
    c, r = run(d, madeup_out, "MU08", "250108")
    [entry] = r.write_entries
    assert (entry.status, entry.previous, entry.op.old_value, entry.error) == ("done", "12.60", "12.50", "verify: no answer")
    assert [(i.item_id, i.reason, i.entry_id) for i in r.review_items][0] == ("RI1", "write made, not verified: check in Exact", entry.entry_id)
    view = load_case_view(c)
    assert [row[5] for row in page_rows(view.flags, "nl")] == [NOT_VERIFIED_NL] + ["Niet uitgevoerd: kijk eerst in Exact naar de eerdere wijziging"] * 3
    assert [row[5] for row in page_rows(view.flags, "en")] == [NOT_VERIFIED_EN] + ["Not applied: look in Exact at the earlier change first"] * 3
    assert f"    - {NOT_VERIFIED_NL} (prijs, regel 1)" in build_digest([r], [], d.settings, date(2026, 11, 12)).text
    root = d.settings.cases_root
    assert [e.entry_id for e in _undoable(c)] == [entry.entry_id] == [value for _, value in _undo_choices(c, STRINGS["nl"])]
    assert selection_from_query(root, {"case": c.case_id, "undo": entry.entry_id}) == (c.case_id, entry.entry_id)
    page = d.model_copy(update={"writer": PreparedWriter(root / "prepared")})         # the writer of the page, which only prepares
    assert undo_text(c, entry.entry_id, page, "nl") == STRINGS["nl"]["hand_put_back_listed"].format(
        row="Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,60")
    assert [(row["kind"], row["old_value"], row["new_value"], row["rule_id"]) for row in read_csv(root / "prepared" / "prepared_changes.csv")] == [
        ("po_line_price", "12.70", "12.60", "undo")]
    assert _undoable(c) == []                                                         # undone once: no longer offered


def test_the_tab_the_digest_and_the_put_back_name_such_a_write_by_the_value_read(tmp_path, madeup_out):
    """One name for one write: the document says 12.50 and the system held 12.60 when the 12.70 was written. The tab's line under
    "Schrijfactie", the digest's line with its undo link, and the put-back all say 12,60 and 12,70; none says the 12,50 that the
    system never held."""
    from datetime import date

    from aid.notify import build_digest, undo_link

    d = deps(tmp_path, madeup_out, writer=RetryingWriter(Unchecked("po_line_price", read="12.60"), PreparedWriter(tmp_path)))
    c, r = run(d, madeup_out, "MU08", "250108")
    [entry] = r.write_entries
    assert _undo_choices(c, STRINGS["nl"]) == [("Prijs van de orderregel, regel 1: 12.60 -> 12.70", entry.entry_id)]
    assert _undo_choices(c, STRINGS["en"]) == [("Price of the PO line, line 1: 12.60 -> 12.70", entry.entry_id)]
    digest = build_digest([r], [], d.settings, date(2026, 11, 12))
    assert f"    - prijs regel 1: 12,60 → 12,70 [ongedaan maken]({undo_link(d.settings, c.case_id, entry.entry_id)})" in digest.text.splitlines()
    assert f'<a href="{undo_link(d.settings, c.case_id, entry.entry_id)}">ongedaan maken</a>'.replace("&", "&amp;") in digest.html
    page = d.model_copy(update={"writer": PreparedWriter(d.settings.cases_root / "prepared")})
    assert "Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,60" in undo_text(c, entry.entry_id, page, "nl")
    assert "12,50" not in digest.text


def test_the_tab_and_the_digest_name_a_write_into_an_empty_field_leeg_and_one_without_a_read_onbekend(tmp_path):
    """Where the field was empty before the write (a null, an empty or a blank text was read) the tab's line and the digest's say
    "leeg", and where nothing was read "onbekend", as the put-back does: never the document's old value, which Exact did not hold.
    An attached file has no values, and a prepared change keeps what its row shows."""
    from datetime import date

    from aid.case import Case
    from aid.notify import build_digest
    from aid.tests.fakes import case_result

    root = tmp_path / "c1"
    root.mkdir()
    op = dict(kind="po_line_price", po_number="250101", version=1, line_id="1", old_value="12.50", new_value="12.70")
    made = [("e1", None, {}), ("e2", None, {"read_before": None}), ("e3", "", {"read_before": ""}), ("e4", "  ", {"read_before": "  "}),
            ("e5", "12.60", {"read_before": "12.60"})]
    entries = [WriteLogEntry(entry_id=entry_id, case_id="c1", op=op, status="done", previous=previous, new="12.70", rule_id="R04", evidence=evidence,
                             timestamp=NOW.isoformat()) for entry_id, previous, evidence in made]
    entries.append(WriteLogEntry(entry_id="e6", case_id="c1", op=dict(kind="attach_document", po_number="250101", version=1, document="conf.pdf"),
                                 status="done", previous=None, new=None, rule_id="R01", evidence={}, timestamp=NOW.isoformat()))
    for entry in entries:
        WriteLog(root).append(entry)
    case = Case(case_id="c1", root=root, kind="confirmation", po_number="250101", input_files=[])
    line = "Prijs van de orderregel, regel 1: {} -> 12.70"
    assert [label for label, _ in _undo_choices(case, STRINGS["nl"])] == [
        line.format("onbekend"), line.format("leeg"), line.format("leeg"), line.format("leeg"), line.format("12.60"), "Bevestiging bijgevoegd"]
    assert [label for label, _ in _undo_choices(case, STRINGS["en"])][:2] == ["Price of the PO line, line 1: unknown -> 12.70",
                                                                               "Price of the PO line, line 1: empty -> 12.70"]
    prepared = WriteLogEntry(entry_id="e7", case_id="c1", op=dict(kind="article_price", po_number="250101", version=1, article_code="KM-1001", new_value="12.70"),
                             status="prepared", previous=None, new="12.70", rule_id="R04", evidence={}, timestamp=NOW.isoformat())
    result = case_result("250101_20261112T090000", "closed", supplier="S01").model_copy(update={"write_entries": [*entries, prepared]})
    text = build_digest([result], [], SETTINGS, date(2026, 11, 12)).text
    writes = [line_.strip().partition(" [")[0] for line_ in text.splitlines() if line_.startswith("    - ")]
    assert writes == ["- prijs regel 1: onbekend → 12,70", "- prijs regel 1: leeg → 12,70", "- prijs regel 1: leeg → 12,70", "- prijs regel 1: leeg → 12,70",
                      "- prijs regel 1: 12,60 → 12,70", "- bijlage conf.pdf", "- artikelprijs KM-1001: onbekend → 12,70 (voorbereid)"]
    english = build_digest([result], [], SETTINGS.model_copy(update={"language": "en"}), date(2026, 11, 12)).text
    assert "    - price line 1: empty → 12.70 [undo]" in english and "    - price line 1: unknown → 12.70 [undo]" in english


def test_the_put_back_of_an_article_price_into_an_empty_field_reads_leeg(tmp_path, madeup_out):
    """An article's price was written by a program into a field that was empty; its put-back, prepared by the page, reads
    "Terugzetten: artikel KM-0801: inkoopprijs 12,70 → leeg", not "terugzetten naar de prijs van daarvoor", which is for a price
    the aid never read."""
    d = deps(tmp_path, madeup_out, writer=done_writer())
    c, r = run(d, madeup_out, "MU08", "250108")
    page = d.model_copy(update={"writer": PreparedWriter(d.settings.cases_root / "prepared")})
    article = next(e for e in r.write_entries if e.op.kind == "article_price")
    empty = article.model_copy(update={"entry_id": "empty-article", "previous": None, "evidence": article.evidence | {"read_before": None}})
    WriteLog(c.root).append(empty)
    assert undo_text(c, "empty-article", page, "nl") == STRINGS["nl"]["hand_put_back_listed_empty"].format(row="Terugzetten: artikel KM-0801: inkoopprijs 12,70 → leeg")
    # the same price where nothing was read: the row the list always had for it
    assert undo_text(c, article.entry_id, page, "nl") == STRINGS["nl"]["hand_put_back_listed"].format(
        row="Terugzetten: artikel KM-0801: inkoopprijs 12,70 terugzetten naar de prijs van daarvoor")


@pytest.mark.parametrize("answered", [True, False], ids=["made, not verified", "not sure"])
def test_leverancier_vragen_is_not_offered_on_an_item_that_asks_for_a_look_in_exact(tmp_path, madeup_out, answered):
    """Such an item is no question for the supplier: the page does not offer "Leverancier vragen" on it, decide refuses it, and
    the three other decisions are recorded as on any item. The items behind it are offered as before."""
    d = deps(tmp_path, madeup_out, writer=RetryingWriter(Unchecked("po_line_price", read="12.60", answered=answered), PreparedWriter(tmp_path)))
    c, r = run(d, madeup_out, "MU08", "250108")
    view = load_case_view(c)
    look, behind = view.flags[0], view.flags[1]
    assert look.reason == ("write made, not verified: check in Exact" if answered else "write outcome not known: check in Exact")
    assert [decision for _, decision in _decision_choices(look, c.kind, STRINGS["nl"])] == ["confirm", "keep", "unresolved"]
    assert [decision for _, decision in _decision_choices(behind, c.kind, STRINGS["nl"])] == ["confirm", "keep", "ask", "unresolved"]
    with pytest.raises(ValueError, match="RI1 asks for a look in Exact"):
        decide(c, "RI1", "ask")
    assert not (c.root / "decisions.jsonl").exists()
    for decision in ("confirm", "keep", "unresolved"):
        decide(c, "RI1", decision)
    assert page_rows([look], "nl")[0][5] == (NOT_VERIFIED_NL if answered else NOT_SURE_NL)
    assert page_rows([look], "en")[0][5] == (NOT_VERIFIED_EN if answered else NOT_SURE_EN)
    from datetime import date

    from aid.notify import build_digest

    waiting = build_digest([r], [], d.settings, date(2026, 11, 12)).text.splitlines()       # the digest uses the page's labels
    assert f"    - {NOT_VERIFIED_NL if answered else NOT_SURE_NL} (prijs, regel 1)" in waiting
    assert "    - Niet uitgevoerd: kijk eerst in Exact naar de eerdere wijziging (prijs, regel 1)" in waiting
    assert waiting.count("    - Niet uitgevoerd: kijk eerst in Exact naar de eerdere wijziging") == 2


@pytest.mark.parametrize("answered", [True, False], ids=["made, not verified", "not sure"])
def test_the_request_to_the_approver_follows_the_buyers_look(tmp_path, madeup_out, answered):
    """MU08 above the authorization, the line's price made and not verified, or not sure. The run asks the approver nothing.
    "Bevestigen" on the item counts the change as made, so the close then sends the request for it, once: a second close sends
    nothing again. "Houden" sends none."""
    for decision in ("confirm", "keep"):
        base = tmp_path / decision
        base.mkdir()
        d = deps(base, madeup_out)
        limit = d.store.versions("250108")[0].total / 2
        route = RetryingWriter(Unchecked("po_line_price", read="12.60", answered=answered), PreparedWriter(base))
        d = d.model_copy(update={"settings": d.settings.model_copy(update={"authorization_limit": limit}), "store": PoStore(d.store.root, limit)})
        c, r = run(d.model_copy(update={"writer": route}), madeup_out, "MU08", "250108")
        assert [(a.rule_id, a.route) for a in r.actions] == [("R10", "AUTO"), ("R01", "AUTO")] and r.messages == []
        page = d.model_copy(update={"writer": PreparedWriter(d.settings.cases_root / "prepared")})
        for item in r.review_items[:-1]:
            decide(c, item.item_id, decision)
        decide(c, r.review_items[-1].item_id, "unresolved")           # the case stays in review, for a second close
        first = close_case(c, page)
        requests_ = [(m["template"], m["status"], [(i["kind"], i["new_value"]) for i in m["items"]]) for m in first.messages]
        assert first.status == "review"
        assert requests_ == ([("reapproval", "not sent (no mailer)", [("po_line_price", "12.70")])] if decision == "confirm" else [])
        # the close wrote nothing for the operation itself: one entry after "Bevestigen", the entry and its undo after "Houden"
        assert [(e.rule_id, e.op.kind) for e in WriteLog(c.root).entries()] == [("R10", "po_line_price")] + [("undo", "po_line_price")] * (decision == "keep")
        decide(c, r.review_items[-1].item_id, decision)
        second = close_case(c, page)
        assert second.status == "closed" and second.messages == first.messages          # nothing sent again


def test_the_undo_of_a_change_made_without_a_value_read_asks_the_buyer_for_the_old_value(tmp_path, madeup_out):
    """MU08 through a route that makes the line's price and reads nothing before it (no previous value, no note of a read). The
    undo has no value to write back. A page whose writer is that route says that the undo failed, and nothing is written; a
    page whose writer prepares puts the put-back on the list with the old value unknown, for the buyer to enter."""
    class Blind(Unchecked):
        def apply(self, op, case_id, rule_id, evidence, now):
            self.given = getattr(self, "given", 0) + 1
            return super().apply(op, case_id, rule_id, evidence, now).model_copy(update={"previous": None})

    route = Blind("no such kind", read="")
    d = deps(tmp_path, madeup_out, writer=route)
    c, r = run(d, madeup_out, "MU08", "250108")
    entry = r.write_entries[0]
    assert (entry.op.kind, entry.status, entry.previous, entry.evidence.get("read_before", "no note")) == ("po_line_price", "done", None, "no note")
    given = route.given
    assert undo_text(c, entry.entry_id, d, "nl") == STRINGS["nl"]["undo_failed"] and route.given == given
    assert [e.entry_id for e in WriteLog(c.root).entries()] == [e.entry_id for e in r.write_entries]       # nothing logged
    page = d.model_copy(update={"writer": PreparedWriter(d.settings.cases_root / "prepared")})
    row = "Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → onbekend"
    assert undo_text(c, entry.entry_id, page, "nl") == f"Nog niet teruggedraaid in Exact. Op de lijst staat nu: {row}. Voer de oude waarde in Exact in en vink de regel af."
    assert [(row["old_value"], row["new_value"], row["rule_id"]) for row in read_csv(d.settings.cases_root / "prepared" / "prepared_changes.csv")] == [
        ("12.70", "", "undo")]


def test_bevestigen_after_the_tabs_own_undo_prepares_the_change_as_on_any_item(tmp_path, madeup_out):
    """The buyer first undid the write from the tab, so it no longer stands; "Bevestigen" on its item then takes the supplier's
    value as on any item: the change is prepared once, and the approver is asked for that one change, once."""
    d = deps(tmp_path, madeup_out)
    limit = d.store.versions("250108")[0].total / 2
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"authorization_limit": limit}), "store": PoStore(d.store.root, limit)})
    c, r = run(d.model_copy(update={"writer": RetryingWriter(Unchecked("po_line_price", read="12.60"), PreparedWriter(tmp_path))}), madeup_out, "MU08", "250108")
    page = d.model_copy(update={"writer": PreparedWriter(d.settings.cases_root / "prepared")})
    assert "Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,60" in undo_text(c, r.write_entries[0].entry_id, page, "nl")
    for item in r.review_items:
        decide(c, item.item_id, "confirm")
    closed = close_case(c, page)
    assert closed.status == "closed" and [(e.rule_id, e.op.kind, e.status) for e in WriteLog(c.root).entries()] == [
        ("R10", "po_line_price", "done"), ("undo", "po_line_price", "prepared"), ("B01", "po_line_price", "prepared"),
        ("B01", "attach_document", "prepared"), ("B01", "set_status", "prepared")]
    assert [(m["template"], [(i["kind"], i["old_value"], i["new_value"]) for i in m["items"]]) for m in closed.messages] == [
        ("reapproval", [("po_line_price", "12.50", "12.70")])]


def test_a_close_stops_at_a_write_made_and_not_verified(tmp_path, madeup_out):
    """MU07 confirmed by the buyer through a route whose system accepts the price and cannot check it. The write is logged as made,
    with the value the route read, and the close stops there as at a failed write: nothing after it is done and the case stays in
    review. The page names the stop by the label of the reason, and the write is among the writes to undo."""
    d = deps(tmp_path, madeup_out); c, r = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "confirm", article_update=True)
    route = d.model_copy(update={"writer": RetryingWriter(Unchecked("po_line_price", read="12.60"), PreparedWriter(tmp_path))})
    text, still_open = close_text(c, route, "nl")
    [entry] = WriteLog(c.root).entries()
    assert (entry.op.kind, entry.status, entry.previous, entry.new, entry.error) == ("po_line_price", "done", "12.60", "14.00", "verify: no answer")
    assert still_open and NOT_VERIFIED_NL in text and "verify" not in text and not (tmp_path / "prepared_changes.csv").exists()
    result = c.read_step("result")
    assert result["status"] == "review" and result["error"] == "write made, not verified: check in Exact (write: rule B01, po_line_price, line 1)"
    assert [e.entry_id for e in _undoable(c)] == [entry.entry_id]


# --- the seven mutants of the check of the review round that no test noticed (ruling C11 of the clean-up) ---

class FilingThenFailing:
    """A mailer during whose send the runner files something with the case (`during`), and that then cannot reach the server."""
    name = "filing-then-failing"

    def __init__(self, during):
        self.during = during

    def send(self, message):
        self.during()
        from aid.messaging import MailerError
        raise MailerError("made-up: no connection", "unreachable")


def test_a_reply_filed_while_a_send_fails_comes_before_the_closes_own_item(tmp_path, madeup_out):
    """Mutants K04 and K06 (the check's test_d_race_fail.py). MU12: the buyer asks about his first item and keeps the second. While
    the close sends that question, the runner files the supplier's reply to the run's question, and the send then fails. The close
    numbers its own item, "Bericht niet verzonden", after the reply's item, and step 09 names that number as the item of the
    question not sent; the next close, which sends it, settles that item."""
    d, clock = world(tmp_path, madeup_out)
    c, _ = run(d, madeup_out, "MU12", "250112")
    [first] = questions(c)
    decide(c, "RI1", "ask"); decide(c, "RI2", "keep")
    failing = d.model_copy(update={"mailer": FilingThenFailing(lambda: answer(d, tmp_path, first))})
    assert close_text(c, failing, "nl") == (not_sent("nl", "unreachable"), True)
    assert [item_id for item_id, _ in items(c)] == ["RI1", "RI2", "RI3", "RI4"]
    assert items(c)[2:] == [("RI3", "supplier answered: read the reply"), ("RI4", "message not sent")]
    assert c.read_step("close")["records"][-1]["unsent_questions"] == {"missing_info": "RI4"}
    decide(c, "RI3", "keep")
    clock.now = NOW + timedelta(hours=4)
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {written('nl', 'outbox')}", False)
    record = c.read_step("close")["records"][-1]
    assert record["settled"] == ["RI4"] and record["unsent_questions"] == {} and len(questions(c)) == 2


class FilingThenRefusing:
    """A writer during whose write the runner files something with the case (`during`), and that then refuses the write."""
    name = "filing-then-refusing"

    def __init__(self, during):
        self.during = during

    def apply(self, op, case_id, rule_id, evidence, now):
        self.during()
        raise WriterError("made-up: the write route refused the change")


def test_a_reply_filed_while_a_close_stops_at_a_write_keeps_its_item(tmp_path, madeup_out):
    """Mutant K07. MU07: the question about the price was answered; the buyer confirms the supplier's price and keeps the reply's
    item. While the close writes the price, a second reply of the supplier is filed, and the write is refused: the close stops
    there. The saved result is read once more right before it is written back, so the second reply's item stays."""
    d, clock = world(tmp_path, madeup_out)
    c, _ = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "ask")
    assert close_case(c, d).status == "waiting"
    [first] = questions(c)
    assert answer(d, tmp_path, first) == c.case_id
    decide(c, "RI1", "confirm"); decide(c, "RI2", "keep")
    late = lambda: answer(d, tmp_path, first, name="nog-een.eml", at=NOW + timedelta(hours=3))      # noqa: E731
    text, still = close_text(c, d.model_copy(update={"writer": FilingThenRefusing(late)}), "nl")
    assert still and c.read_step("close")["records"][-1]["stop"] is not None and c.read_step("result")["status"] == "review"
    assert items(c) == [("RI1", "price above threshold"), ("RI2", "supplier answered: read the reply"), ("RI3", "supplier answered: read the reply")]


def test_a_choice_on_the_unsent_item_while_there_is_still_no_route_keeps_the_case_in_review(tmp_path, madeup_out):
    """Mutant K16 (the check's test_g_held.py). Nothing connected: the question about the price is not sent. The buyer, while the
    question still stands on "Leverancier vragen", makes a choice on the item "Bericht niet verzonden" as well. The next close,
    with still no way to send, sends nothing, and the case stays in review: a message that could not be given to a mailer holds
    the case, whatever is chosen on its item."""
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    c = asked_on_the_page(d, madeup_out)
    assert after_the_press(page, c) == (NO_MAILBOX, c.case_id)
    decide(c, "RI2", "keep")
    assert after_the_press(page, c) == (NO_MAILBOX, c.case_id)
    assert c.read_step("result")["status"] == "review" and sent_lines(c) == [] and not list(tmp_path.rglob("*.eml"))


def test_a_save_on_the_settings_tab_after_an_unreadable_look_puts_its_settings_into_use(tmp_path, madeup_out, smtp_server, monkeypatch):
    """Mutant K21. The settings file did not read at a look, and was put right; then the buyer saves on the settings tab. The
    settings the tab wrote are in use at once, and the state "cannot be read" is over: a close right after it sends through them,
    also when the file cannot be opened again at that moment (held by another program right after the save)."""
    import aid.review.page as page_module
    path, d, page, _ = sending_page(tmp_path, madeup_out, environ=os.environ, send_via="smtp", smtp_host="smtp.example.com", smtp_user="aid@example.com")
    good = path.read_text(encoding="utf-8")
    c = asked_on_the_page(d, madeup_out)
    spoiled(path, "cut in the middle")
    assert after_the_press(page, c) == (not_sent("nl", "settings"), c.case_id)
    path.write_text(good, encoding="utf-8")
    os.utime(path, ns=(path.stat().st_mtime_ns + 4_000_000_000,) * 2)

    def held(file):
        raise PermissionError(13, "made-up: held by another program")
    monkeypatch.setattr(page_module, "load_settings", held)
    [save_reader] = [f.fn for f in page.fns.values() if getattr(f.fn, "__name__", "") == "save_reader"]
    assert save_reader("parser", "nl") == ("parser", STRINGS["nl"]["settings_buyer_saved"])
    assert after_the_press(page, c) == (WAITING["nl"], None) and len(smtp_server) == 1


class FailingByTurn:
    """A mailer whose sends fail one after the other with the kinds given."""
    name = "failing-by-turn"

    def __init__(self, *kinds):
        self.kinds = list(kinds)

    def send(self, message):
        from aid.messaging import MailerError
        raise MailerError("made-up: the message did not go", self.kinds.pop(0))


@pytest.mark.parametrize("language", ["nl", "en"])
def test_two_causes_in_one_close_each_get_their_sentence(tmp_path, madeup_out, language):
    """Mutant K23. MU19: the buyer asks about the price and about the quantity, two kinds of question. The first is refused and
    the second finds no connection: after the close the page names both causes, each with its sentence, in the order they arose."""
    d, clock = world(tmp_path, madeup_out)
    c, _ = run(d, madeup_out, "MU19", "250119")
    decide(c, "RI1", "ask"); decide(c, "RI2", "ask")
    text, still = close_text(c, d.model_copy(update={"mailer": FailingByTurn("refused", "unreachable")}), language)
    assert still and text == f"{UNSENT[language]} {CAUSES['refused'][language == 'en']} {CAUSES['unreachable'][language == 'en']}"


def test_asking_again_on_a_close_recorded_before_the_line_numbers_were_kept(tmp_path, madeup_out):
    """Mutant K03 (the check's test_f_old_record.py). MU07's close was recorded as the closes before the review round recorded
    them, without the number of the last line of decisions.jsonl it had read. After the question ended without an answer, the
    item shows no choice; "Leverancier vragen" chosen on it again is a new decision, and the next close sends the question again."""
    d, clock = world(tmp_path, madeup_out)
    c, _ = run(d, madeup_out, "MU07", "250107")
    decide(c, "RI1", "ask")
    assert close_case(c, d).status == "waiting"
    path = c.root / "steps" / "09_close.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for record in data["records"]:
        for key in ("decisions_read", "unsent_questions", "unread_reply"):
            record.pop(key, None)
    path.write_text(json.dumps(data), encoding="utf-8")
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 17, 9, 1), d.settings) == [(c.case_id, "no answer in 3 working days")]
    assert shown_choices(d, c) == {}
    decide(c, "RI2", "keep")
    with open(c.root / "decisions.jsonl", "a", encoding="utf-8") as f:       # a choice of a later second than the first "ask"
        f.write(json.dumps({"item_id": "RI1", "decision": "ask", "article_update": False, "at": "2026-11-20T10:00:00"}) + "\n")
    clock.now = datetime(2026, 11, 20, 10, 0)
    assert close_text(c, d, "nl") == (f"{WAITING['nl']} {written('nl', 'outbox')}", False) and len(questions(c)) == 2


STALE_FILE_SENTENCES = ("`null`: messages are written as files", "else SMTP when it is set up, else files", "through the SMTP server or as files",
                        "go through SMTP or as files", "writes every message as a file", "without a warning",
                        "writes each of its messages as a file", "else writes the messages as files")


def test_no_text_says_that_a_message_is_written_as_a_file_for_want_of_a_way_to_send():
    """Rulings C10 and C12. Neither a close of the review page nor the run writes a question or a request as a file for want of a
    way to send: only send_via "file" writes them, and the daily digest is written into cases/outbox/. The manual's sentences on
    smtp_host, send_via, the step of the connection and signing out, the README and the label of "Automatisch" on the settings tab
    say so, and no sentence says that the runner writes its messages as files "without a warning"."""
    texts = {name: (REPO_ROOT / "aid" / name).read_text(encoding="utf-8") for name in ("MANUAL.md", "README.md")}
    assert [(name, stale) for name, text in texts.items() for stale in STALE_FILE_SENTENCES if stale in text] == []
    assert STRINGS["nl"]["settings_via:auto"].endswith("anders de SMTP-server als die is ingesteld; anders wordt niets verzonden")
    assert STRINGS["en"]["settings_via:auto"].endswith("else the SMTP server when it is set up; else nothing is sent")
    assert "only the digest is written into `cases/outbox/`" in texts["MANUAL.md"] and "message not sent" in texts["README.md"]


def test_the_manual_says_what_a_write_made_and_not_verified_is_and_what_an_undo_restores():
    """MANUAL 2.4 has the rows of the three reasons with the labels as the page shows them, each beginning by saying that no
    installation has a write route, and says what "Bevestigen" and "Houden" do on an item that asks for a look in Exact; 2.2 names
    the stop of a close at such a write; 3.10 says that no command builds the route, with the sentence a settings file with
    exact_base_url gets, what the module is, what a route does with a write it cannot check or whose outcome it does not know,
    what a builder of a route must still settle, and which value an undo writes back; 3.2, 3.4, 3.13 and section 5 say that the
    key stays empty and that the token is not used, and so does the README; section 1 says that no installation writes in Exact;
    the Dutch guide does not speak of the route; and the docstrings of the module, of the list and of the capture server say what
    the rulings V11 and V13 ask of them."""
    from aid import capture_server
    from aid.execution import by_hand, exact_api
    whole = Path("aid/MANUAL.md").read_text(encoding="utf-8")

    def section(number: str, following: str, level: str = "###") -> str:
        return whole.split(f"\n{level} {number} ")[1].split(f"\n{level} {following} ")[0]

    reasons, route, page = section("2.4", "2.5"), section("3.10", "3.11"), section("2.2", "2.3")
    one, troubles, guide = section("1.", "2.", "##"), section("5.", "6.", "##"), whole.split("\n## 7. ")[1]
    nl, en = STRINGS["nl"], STRINGS["en"]
    assert nl["reason:write made, not verified: check in Exact"] == NOT_VERIFIED_NL and en["reason:write made, not verified: check in Exact"] == NOT_VERIFIED_EN
    assert nl["reason:write outcome not known: check in Exact"] == NOT_SURE_NL and en["reason:write outcome not known: check in Exact"] == NOT_SURE_EN
    behind = "reason:not applied: an earlier change waits for a look in Exact *"
    assert (nl[behind], en[behind]) == ("Niet uitgevoerd: kijk eerst in Exact naar de eerdere wijziging", "Not applied: look in Exact at the earlier change first")
    assert (nl["reason:not applied: an earlier write failed *"], en["reason:not applied: an earlier write failed *"]) == (
        "Niet uitgevoerd: een eerdere schrijfactie is mislukt", "Not applied: an earlier write failed")
    assert f"| write made, not verified: check in Exact | {NOT_VERIFIED_NL} | {NOT_VERIFIED_EN} |" in reasons
    assert f"| write outcome not known: check in Exact | {NOT_SURE_NL} | {NOT_SURE_EN} |" in reasons
    assert f"| not applied: an earlier change waits for a look in Exact ... | {nl[behind]} | {en[behind]} |" in reasons
    assert "| not applied: an earlier write failed ... | Niet uitgevoerd: een eerdere schrijfactie is mislukt | Not applied: an earlier write failed |" in reasons
    for words in ("\"Bevestigen\": the change is in Exact and stays.", "prepares nothing more for it", "\"Houden\": the old value must stay.",
                  "what waited behind the change is not carried out", "\"Leverancier vragen\" is not offered"):
        assert words in reasons, words
    assert f"\"{NOT_VERIFIED_NL}\" or \"{NOT_SURE_NL}\"" in page and "nor on an item that asks you to look in Exact" in page
    for words in ("`UnverifiedWrite`", "`UnknownWrite`", "it is logged as made", "\"write made, not verified: check in Exact\"",
                  "\"write outcome not known: check in Exact\"", "`\"outcome\": \"not known\"`", "that write can still land",
                  "is not sent again and ends as not sure", "takes that read as the check", "the value read before the write",
                  "An undo never writes a value the system did not hold", f"\"{nl['hand_put_back_listed_empty'].split('. ')[-1]}\"",
                  "never makes the put-back \"already put back\"", "includes it in the request to the approver"):
        assert words in route, words
    for words in ("*No token*", "*A status that is not 2xx*", "*A value that reads back different*", "*No connection or no answer*",
                  "\"write outcome not known: check in Exact\"", "\"not applied: an earlier change waits for a look in Exact\""):
        assert words in troubles, words
    for words in ("puts the change on the list \"In Exact invoeren\" for the buyer to enter",
                  "It writes nothing in Exact itself, and no installation does (section 3.10).",
                  "puts both on the list \"In Exact invoeren\" for the buyer to enter"):
        assert words in one, words
    assert "web API route" not in one and "the aid makes the change itself" not in whole and "the aid writes the new" not in whole
    # the last round (V10 to V13): no command builds the route, and the buyer's guide does not speak of it
    sentence = ('exact_base_url is set, but this version of the aid writes nothing in Exact: every change is prepared for the tab '
                '"In Exact invoeren". Remove exact_base_url from the settings file.')
    no_route = "Only with a program's write route, which no installation has (section 3.10)."
    for row in (f"| write made, not verified: check in Exact | {NOT_VERIFIED_NL} | {NOT_VERIFIED_EN} | {no_route} ",
                f"| write outcome not known: check in Exact | {NOT_SURE_NL} | {NOT_SURE_EN} | {no_route} ",
                f"| not applied: an earlier change waits for a look in Exact ... | {nl[behind]} | {en[behind]} | {no_route} "):
        assert row in reasons, row
    assert ("(the rows \"write made, not verified: check in Exact\" and \"write outcome not known: check in Exact\" above)" in reasons
            and "above \"write failed\"" not in reasons)
    assert f"\"{nl['hand_put_back_cannot']}\" and the case stays in review until the next close" in reasons
    assert "(\"leeg\" where the field was empty, \"onbekend\" where nothing was read)" in page
    for words in ("No command builds a write route into Exact", f"\n    {sentence}\n", nl["settings_file_write_route"].split(" Hieronder")[0],
                  "none of them reads `AID_EXACT_TOKEN`", "**What the module `aid/execution/exact_api.py` is.**",
                  "was never run against Exact", "**What a builder of a route must still settle.**", "A doubt that outlives its operation",
                  "The article price that waits behind a look", "A close's own write that asks for a look", "A read that lags behind the write",
                  "a connection that was never made: refused, a connect time-out, a TLS or a proxy failure", "`put_back_by_hand`",
                  f"\"{nl['hand_put_back_cannot']}\"",
                  "by \"leeg\" where the field was empty before the write and \"onbekend\" where nothing was read, as its put-back does"):
        assert words in route, words
    settings_file, starting, capture = section("3.2", "3.3"), section("3.4", "3.5"), section("3.13", "3.14")
    assert "| `exact_base_url` | `null` | Must stay `null` or empty. No command builds a write route into Exact" in settings_file
    assert "no command reads `AID_EXACT_TOKEN` (section 3.10)" in settings_file and "the Exact route (" not in settings_file
    assert "`AID_EXACT_TOKEN` is not used: no command reads it (section 3.10)" in starting and "$env:AID_MAIL_PASSWORD" in starting
    assert "its writer is the prepared writer" in capture and "`AID_EXACT_TOKEN` is not used" in capture
    assert "**A settings file with `exact_base_url` set.**" in troubles and sentence in troubles
    assert "a connection that was never made (refused, a connect time-out, a TLS or a proxy failure) sent nothing" in troubles
    for stale in ("(when `exact_base_url` is set)", "$env:AID_EXACT_TOKEN", "Without the token every write to Exact", "If it were set, the runner would write"):
        assert stale not in whole, stale
    for words in ("kijk in Exact of de waarde klopt", "kijk in Exact welke waarde er staat", "Vraagt een punt u om in Exact te kijken"):
        assert words not in guide, words
    readme = Path("aid/README.md").read_text(encoding="utf-8")
    for words in ("`exact_base_url` must stay `null` or empty", "no command reads `AID_EXACT_TOKEN`", "exact_api.py: the Exact writer, which no command builds",
                  "are the keys of a write route into Exact that no command builds"):
        assert words in readme, words
    assert "`AID_EXACT_TOKEN` and `AID_MAIL_PASSWORD`" not in readme
    module = " ".join((exact_api.__doc__ or "").split())
    assert module.startswith("A writer into a system behind a web API, built by no command: kept as the place where a write route into Exact would plug in.")
    for words in ("has never been run against Exact, whose web service has another shape than that stand-in",
                  "such a writer implements the Writer protocol", "RetryingWriter takes it as its primary writer in front of the PreparedWriter",
                  "What a builder of a route must still settle.", "- A doubt that outlives its operation.", "- The article price that waits behind a look.",
                  "- A close's own write that asks for a look.", "- A read that lags behind the write.",
                  "a connection that was never made: refused, a connect time-out, a TLS or a proxy failure"):
        assert words in module, words
    listed = " ".join((by_hand.__doc__ or "").split())
    assert "A put-back whose own row can no longer be read (its line of the file is damaged and passed over) is judged the same way" in listed
    served = " ".join((capture_server.__doc__ or "").split())
    assert "asked the buyer for a look in Exact only once he confirmed its item and a close carried that out" in served
    assert "counts once he confirmed its item and a close carried that out" in " ".join((capture_server._Writes.__doc__ or "").split())


# --- where Task 44 and Task 45 meet in one close ---

def test_a_look_kept_and_a_question_that_cannot_be_sent_in_one_close(tmp_path, madeup_out):
    """MU08's price was written and not verified, so its item asks the buyer to look in Exact, and the items behind it wait. The
    buyer keeps the old value on that item ("Houden") and asks the supplier about an item behind it, at a moment at which nothing
    can send. One close does what each rule says. The change is put back, once, what waited behind it is not carried out, and
    nothing is attached and no status set. The question is not sent and not written as a file: the case stays in review with the
    item "Bericht niet verzonden", and the page says why. Once the settings have messages written as files, the next close sends
    the question and settles that item, and puts nothing back a second time."""
    d = deps(tmp_path, madeup_out, writer=RetryingWriter(Unchecked("po_line_price", read="12.60"), PreparedWriter(tmp_path)))
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"supplier_emails": {"S08": "sales@s08.example.com"}})})
    c, r = run(d, madeup_out, "MU08", "250108")
    look, behind, *others = r.review_items
    assert look.reason == "write made, not verified: check in Exact" and behind.reason.startswith("not applied: an earlier change waits for a look")
    outbox = tmp_path / "cases" / "outbox"
    page = d.model_copy(update={"writer": PreparedWriter(d.settings.cases_root / "prepared"),
                                "mailer": FileMailer(outbox, sender="aid@example.com", clock=lambda: NOW, unrouted="no_mailbox")})
    decide(c, look.item_id, "keep"); decide(c, behind.item_id, "ask")
    for other in others:
        decide(c, other.item_id, "keep")
    assert close_text(c, page, "nl") == (not_sent("nl", "no_mailbox"), True)
    record = c.read_step("close")["records"][-1]
    unsent = f"RI{len(r.review_items) + 1}"
    assert [(e.rule_id, e.op.kind) for e in WriteLog(c.root).entries()] == [("R04", "po_line_price"), ("undo", "po_line_price")]
    assert [(m["status"], m["cause"], m["item_ids"]) for m in record["messages"]] == [("not sent (no mailbox connected)", "no_mailbox", [behind.item_id])]
    assert list(record["unsent_questions"].values()) == [unsent] and items(c)[-1] == (unsent, "message not sent")
    assert record["carried_out"].keys() == {look.item_id, *(other.item_id for other in others)} and "put_back_by_hand" not in record
    assert sent_lines(c) == [] and not outbox.exists() and c.read_step("result")["status"] == "review"
    assert shown_choices(page, c)[behind.item_id] == "ask"
    by_choice = page.model_copy(update={"mailer": FileMailer(outbox, sender="aid@example.com", clock=lambda: NOW)})
    assert close_text(c, by_choice, "nl") == (f"{WAITING['nl']} {written('nl')}", False)
    record = c.read_step("close")["records"][-1]
    assert record["settled"] == [unsent] and record["unsent_questions"] == {} and record["carried_out"].keys() == {behind.item_id}
    assert [e.op.kind for e in WriteLog(c.root).entries()] == ["po_line_price", "po_line_price"]      # put back once, nothing attached
    assert len(sent_lines(c)) == 1 and len(list(outbox.glob("*.eml"))) == 1 and c.read_step("result")["status"] == "waiting"


# --- the strip, the fold, the case list and the timer of the tab "Beoordelen" (findings 3, 4, 5 and 8 of the owner's test) ---

from html import unescape  # noqa: E402

from aid.case import open_case  # noqa: E402
from aid.review.page import SETTINGS_TAB  # noqa: E402
from aid.tests.test_status import launcher_state  # noqa: E402


def name_of(block) -> str:
    """A component of the page by its elem_id, else its label, else its value, else its class ("State", "Timer", the page's "Tabs",
    and the status line, which starts empty, as "Markdown")."""
    if isinstance(block, (gradio.State, gradio.Timer, gradio.Tabs)):
        return getattr(block, "elem_id", None) or type(block).__name__
    value = getattr(block, "value", None)
    return block.elem_id or getattr(block, "label", None) or (str(value) if value not in (None, "") else type(block).__name__)


def handler_of(page, name: str):
    [found] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == name]
    return found


def called(page, name: str, *args) -> dict:
    """What the page's handler of that name gives for the inputs, by the name of each output (name_of)."""
    f = handler_of(page, name)
    given = f.fn(*args)
    return dict(zip([name_of(block) for block in f.outputs], given if len(f.outputs) > 1 else (given,), strict=True))


def bound(page, name: str) -> tuple:
    """The triggers of the page's handler of that name, sorted, with the names of its inputs and of its outputs."""
    f = handler_of(page, name)
    return (sorted((name_of(page.blocks[block]) if block != page._id else "page", event) for block, event in f.targets),
            [name_of(block) for block in f.inputs], [name_of(block) for block in f.outputs])


def table_rows(html: str) -> list[list[str]]:
    """The rows of the fold's table, the head first, each as the texts of its cells."""
    return [[unescape(re.sub(r"<[^>]+>", "", cell)) for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row)]
            for row in re.findall(r"<tr>(.*?)</tr>", html)]


def digest_state(d, minutes_ago: int, **notes):
    """The runner's digest state in the cases root with these notes, written `minutes_ago` minutes before the clock of deps(): the
    time of the runner's last pass."""
    path = d.settings.cases_root / "digest_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_sent": None, "reported": [], "skipped": []} | notes), encoding="utf-8")
    os.utime(path, ((NOW - timedelta(minutes=minutes_ago)).timestamp(),) * 2)
    return path


def test_the_strip_counts_the_cases_by_state_and_names_the_problems(tmp_path, madeup_out):
    """Findings 3, 4 and 8. The strip at the top of "Beoordelen" counts the cases by their state, names what is wrong with the
    assistant and ends with its last pass, its mailbox, its reader and the time it was read, all from the aid's own files; the fold
    under it lists every case with the time it came in, its order, its state and what the buyer does, a case closed by itself apart
    from one the buyer closed. Copilot shows as failing by the runner's own note in its digest state ("copilot"), never by another
    key and never by its error's text. No name in the strip reads as a rule code, and on a public share link it holds the chips only."""
    nl = STRINGS["nl"]
    path, d, page, _ = sending_page(tmp_path, madeup_out, reader="llm", **MAILBOX)
    review, _ = run(d, madeup_out, "MU07", "250107")
    failed = [run(d, madeup_out, "MU05", "250105", now=NOW + timedelta(minutes=minute))[0] for minute in (1, 2)]
    itself, result = run(d, madeup_out, "MU02", "250102", now=NOW + timedelta(minutes=3))       # closed by the aid itself
    by_you, _ = run(d, madeup_out, "MU07", "250107", now=NOW + timedelta(minutes=4))
    decide(by_you, "RI1", "confirm")                                                             # its price change goes on the list
    waiting, kept = run(d, madeup_out, "MU01", "250101", now=NOW + timedelta(minutes=5))
    waiting.write_step(8, "result", kept.model_copy(update={"status": "waiting"}))
    open_case(d.settings.cases_root, "confirmation", [madeup_out / "MU03_conf.pdf"], "250103", NOW + timedelta(minutes=6))   # read next pass
    on_the_list = d.model_copy(update={"writer": PreparedWriter(d.settings.cases_root / "prepared")})       # as the page's own close
    assert result.status == "closed" and close_case(by_you, on_the_list).status == "closed"
    assert not (itself.root / "steps" / "09_close.json").exists()
    shown = called(page, "strip_ticked", "nl")
    strip = shown["review-strip"]
    for words in (nl["chip:review"].format(n=1), nl["chip:failed"].format(n=2), nl["chip:waiting"].format(n=1),
                  nl["chip:closed_itself"].format(n=1), nl["problem:no_pass_ever"], nl["strip_mailbox:off"], nl["strip_reader:llm"],
                  nl["strip_updated"].format(time="09:00")):
        assert words in strip, words
    assert "Laatste ronde" not in strip and "Copilot werkt niet" not in strip and "Wacht op Copilot" not in strip
    assert shown["review-all"] == {"__type__": "update", "label": nl["review_all"].format(n=7)}
    assert table_rows(shown["review-all-table"]) == [
        [nl["all_column:time"], nl["all_column:case"], nl["all_column:state"], nl["all_column:todo"]],
        ["12-11-2026 09:02", "Inkooporder 250105 (S05)", nl["state:failed"], nl["todo:failed"]],
        ["12-11-2026 09:01", "Inkooporder 250105 (S05)", nl["state:failed"], nl["todo:failed"]],
        ["12-11-2026 09:00", "Inkooporder 250107 (S07)", nl["state:review"], nl["todo:review"]],
        ["12-11-2026 09:06", "Inkooporder 250103", nl["state:reading"], nl["todo:reading"]],
        ["12-11-2026 09:05", "Inkooporder 250101 (S01)", nl["state:waiting"], nl["todo:waiting"]],
        ["12-11-2026 09:04", "Inkooporder 250107 (S07)", nl["state:closed_by_you"], nl["todo:closed_changes"]],
        ["12-11-2026 09:03", "Inkooporder 250102 (S02)", nl["state:closed_itself"], nl["todo:nothing"]]]
    for key in ("copilot_down", "copilot"):              # the runner writes the second only; the first is a key it never writes
        digest_state(d, 1, **{key: {"since": "2026-11-12T08:15:00", "error": "Copilot refused: 400 BadRequest", "waiting": 1}})
        shown = called(page, "strip_ticked", "nl")
        strip = shown["review-strip"]
        assert nl["strip_pass"].format(time="08:59") in strip and nl["problem:no_pass_ever"] not in strip
        assert ("Copilot werkt niet" in strip) is ("Wacht op Copilot" in strip) is (key == "copilot"), key
    assert nl["problem:copilot_down_one"].format(since="12-11-2026 08:15") in strip and nl["chip:copilot_wait"].format(n=1) in strip
    assert "BadRequest" not in strip and "400" not in strip
    assert not CODE.search(strip) and not CODE.search(shown["review-all-table"])
    _, _, shared, _ = sending_page(tmp_path, madeup_out, share=True, reader="llm", **MAILBOX)
    chips = called(shared, "strip_ticked", "nl")
    assert set(chips) == {"review-strip", "review-all", "review-all-table"}                     # no button on a share link
    assert nl["chip:failed"].format(n=2) in chips["review-strip"] and nl["chip:copilot_wait"].format(n=1) in chips["review-strip"]
    for words in ("Laatste ronde", "Copilot werkt niet", "Bijgewerkt", nl["strip_reader:llm"]):
        assert words not in chips["review-strip"], words
    assert table_rows(chips["review-all-table"]) == table_rows(shown["review-all-table"])


def test_the_timer_refreshes_beoordelen_without_touching_the_chosen_case(tmp_path, madeup_out):
    """Finding 5. One timer at the top level of the page reads the strip and the case list again every 30 seconds, as the page's load,
    "Lijst vernieuwen" and the language switch do, through two handlers of its own, which reach no part of the view of the case and
    never set the case chosen; the list of the tab "In Exact invoeren", whose read empties the buyer's ticks, is not on the timer. A
    new case is listed and counted in the tab's label; the case list is sent only when it changed; and a case on screen that left the
    list (closed in another browser tab) keeps its place in it, with a line that says so, until another case is chosen."""
    nl = STRINGS["nl"]
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    [timer] = [block for block in page.blocks.values() if isinstance(block, gradio.Timer)]
    assert timer.value == 30 and timer in page.children
    every = [("Lijst vernieuwen", "click"), ("Taal", "change"), ("Timer", "tick"), ("page", "load")]
    assert bound(page, "strip_ticked") == (every, ["Taal"], ["review-strip", "review-strip-action", "review-all", "review-all-table"])
    assert bound(page, "queue_ticked") == (every, ["Taal", "Zaak", "State"],
                                           ["Beoordelen", "Zaak", "review-pick-row", "review-pick", "review-oldest", "review-gone", "State"])
    queue = handler_of(page, "queue_ticked")
    assert queue.inputs[2] is queue.outputs[-1] and {target[0] for target in handler_of(page, "refreshed").targets} < {target[0] for target in queue.targets}
    view = {id(block) for block in handler_of(page, "show").outputs}
    for name in ("strip_ticked", "queue_ticked"):
        assert handler_of(page, name).show_progress == "hidden" and not view & {id(block) for block in handler_of(page, name).outputs}, name
    for name in ("list_opened", "show", "refreshed"):
        assert len([f for f in page.fns.values() if getattr(f.fn, "__name__", "") == name]) == 1, name
    assert ("Timer", "tick") not in bound(page, "list_opened")[0]
    first, _ = run(d, madeup_out, "MU07", "250107")                               # the case the buyer has open
    label = "PO 250107 (S07), 2026-11-12 09:00, 1 punt"
    ticked = called(page, "queue_ticked", "nl", first.case_id, None)
    assert ticked["Beoordelen"] == {"__type__": "update", "label": "Beoordelen (1)"}
    assert ticked["Zaak"] == {"__type__": "update", "choices": [(label, first.case_id)]}
    assert ticked["review-pick-row"] == {"__type__": "update"}                   # case_framed alone shows and hides it
    assert ticked["review-gone"] == {"__type__": "update", "value": "", "visible": False}
    second, _ = run(d, madeup_out, "MU07", "250107", now=LATER)                   # a new case comes in meanwhile
    ticked = called(page, "queue_ticked", "nl", first.case_id, ticked["State"])
    assert ticked["Beoordelen"] == {"__type__": "update", "label": "Beoordelen (2)"}
    assert ticked["Zaak"] == {"__type__": "update", "choices": [(label, first.case_id), (label.replace("09:00", "09:01"), second.case_id)]}
    again = called(page, "queue_ticked", "nl", first.case_id, ticked["State"])
    assert again["Zaak"] == {"__type__": "update"} and again["State"] == ticked["State"]          # nothing changed: nothing is sent
    decide(first, "RI1", "keep")
    assert close_case(first, d).status == "closed"                               # closed in another browser tab
    gone = called(page, "queue_ticked", "nl", first.case_id, again["State"])
    assert gone["Zaak"] == {"__type__": "update", "choices": [(label.replace("09:00", "09:01"), second.case_id), (f"{label}, afgesloten", first.case_id)]}
    assert gone["review-gone"] == {"__type__": "update", "value": nl["review_gone"].format(po="250107"), "visible": True}
    assert gone["Beoordelen"] == {"__type__": "update", "label": "Beoordelen (1)"} and gone["review-pick-row"] == {"__type__": "update"}
    assert called(page, "refreshed", "nl", first.case_id)["Zaak"] == {"__type__": "update", "choices": gone["Zaak"]["choices"]}   # kept there too
    chosen = called(page, "queue_ticked", "nl", second.case_id, gone["State"])    # another case chosen: the closed one leaves the list
    assert chosen["Zaak"] == {"__type__": "update", "choices": [(label.replace("09:00", "09:01"), second.case_id)]}
    assert chosen["review-gone"] == {"__type__": "update", "value": "", "visible": False}


def test_a_tick_reads_no_unchanged_case_again(tmp_path, madeup_out, monkeypatch):
    """B1.4 and section 8 of the redesign: a tick of the page reads only what changed. The case list of each tick is read through a
    cache of the cases offered, as the strip is read through the status's own: with nothing changed between two ticks, the second
    opens no result and no write log of any case, neither for the list nor for the strip; a case whose result a close wrote again
    is read again, alone, and the list follows it."""
    import builtins
    import io
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    review = [run(d, madeup_out, "MU07", "250107", now=NOW + timedelta(minutes=minute))[0] for minute in range(3)]
    run(d, madeup_out, "MU05", "250105", now=NOW + timedelta(minutes=3))                       # a failed case
    opened: list[Path] = []
    real = io.open

    def counting(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).name in ("08_result.json", "write_log.jsonl") and "r" in mode:
            opened.append(Path(file))
        return real(file, mode, *args, **kwargs)

    first = called(page, "queue_ticked", "nl", None, None)
    called(page, "strip_ticked", "nl")
    monkeypatch.setattr(io, "open", counting)
    monkeypatch.setattr(builtins, "open", counting)
    again = called(page, "queue_ticked", "nl", None, first["State"])
    called(page, "strip_ticked", "nl")
    assert again["Zaak"] == {"__type__": "update"} and opened == []          # nothing changed: nothing read, nothing sent
    decide(review[0], "RI1", "keep")
    assert close_case(review[0], d).status == "closed"                     # closed in another browser tab
    opened.clear()
    closed = called(page, "queue_ticked", "nl", None, again["State"])
    assert opened and {(path.parents[1] if path.name == "08_result.json" else path.parent).name for path in opened} == {review[0].case_id}
    assert [value for _, value in closed["Zaak"]["choices"]][:2] == [case.case_id for case in review[1:]]


def test_the_review_label_is_the_same_from_the_timer_and_the_language_switch(tmp_path, madeup_out):
    """Two handlers write the label of the tab "Beoordelen" at a language switch: the page's anonymous relabel of the tabs and the
    timer's queue_ticked. Both give the same label, the number of cases that wait for the buyer (in review, or failed and not noted
    as handled) behind the tab's name and no number when none waits, so that the label is right whichever runs last. A case that
    waits for its purchase order is not counted."""
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    [review_tab] = [block for block in page.blocks.values() if isinstance(block, gradio.Tab) and block.label == STRINGS["nl"]["review_tab"]]
    [relabel] = [f.fn for f in page.fns.values() if f.outputs[:1] == [review_tab] and getattr(f.fn, "__name__", "") == "<lambda>"]
    for language, label in (("nl", "Beoordelen"), ("en", "Review")):
        assert relabel(language)[0] == called(page, "queue_ticked", language, None, None)["Beoordelen"] == {"__type__": "update", "label": label}
    run(d, madeup_out, "MU07", "250107")
    run(d, madeup_out, "MU05", "250105", now=LATER)
    run(without_po(d, tmp_path), madeup_out, "MU01", "250101", now=LATER + timedelta(minutes=1))
    for language, label in (("nl", "Beoordelen (2)"), ("en", "Review (2)")):
        assert relabel(language)[0] == called(page, "queue_ticked", language, None, None)["Beoordelen"] == {"__type__": "update", "label": label}
    assert relabel("en")[1] == {"__type__": "update", "label": STRINGS["en"]["settings_tab"]}
    assert (STRINGS["nl"]["review_tab_count"], STRINGS["en"]["review_tab_count"]) == ("Beoordelen ({n})", "Review ({n})")


def test_the_pick_line_and_the_oldest_case(tmp_path, madeup_out):
    """Finding 5. With no case chosen, a line under "Zaak" says to choose one, or that none waits, and "Oudste zaak openen" chooses the
    oldest case in review, else the oldest failed one, sending the case list with it so that the case is among its choices; the line
    and the button go once a case is chosen (case_framed, on the change of "Zaak" beside show)."""
    nl, en = STRINGS["nl"], STRINGS["en"]
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    assert bound(page, "oldest_opened") == ([("review-oldest", "click")], ["Taal"], ["Zaak"])
    assert bound(page, "case_framed") == ([("Zaak", "change")], ["Zaak", "Taal"], ["review-pick-row", "review-pick", "review-oldest", "review-gone",
                                                                                 "review-failed", "Zaak afsluiten", "review-handled", "### Besluiten"])
    assert bound(page, "show")[0] == [("Zaak", "change")] and handler_of(page, "case_framed").show_progress == "hidden"
    none = called(page, "case_framed", None, "nl")
    assert none["review-pick-row"] == {"__type__": "update", "visible": True} and none["review-pick"] == nl["review_pick_none"]
    assert none["review-oldest"] == {"__type__": "update", "value": nl["review_open_oldest"], "visible": False}
    assert called(page, "oldest_opened", "nl")["Zaak"] == {"__type__": "update"}                 # none waits: nothing is chosen
    failed, _ = run(d, madeup_out, "MU05", "250105")                                            # the oldest, but failed
    older, _ = run(d, madeup_out, "MU07", "250107", now=LATER)
    newer, _ = run(d, madeup_out, "MU07", "250107", now=LATER + timedelta(minutes=1))
    opened = called(page, "oldest_opened", "nl")["Zaak"]
    assert opened["value"] == older.case_id and [value for _, value in opened["choices"]] == [older.case_id, newer.case_id, failed.case_id]
    waits = called(page, "case_framed", None, "en")
    assert waits["review-pick"] == en["review_pick"] and waits["review-oldest"] == {"__type__": "update", "value": en["review_open_oldest"], "visible": True}
    chosen = called(page, "case_framed", older.case_id, "nl")
    assert chosen["review-pick-row"] == {"__type__": "update", "visible": False} and chosen["review-gone"] == {"__type__": "update", "value": "", "visible": False}
    for case in (older, newer):
        decide(case, "RI1", "keep")
        assert close_case(case, d).status == "closed"
    assert called(page, "oldest_opened", "nl")["Zaak"] == {"__type__": "update", "choices": [(f"PO 250105 (S05), 2026-11-12 09:00, {nl['case_failed_look']}",
                                                                                             failed.case_id)], "value": failed.case_id}
    assert (nl["review_pick"], nl["review_pick_none"], nl["review_open_oldest"]) == (
        "Kies een zaak bij Zaak, of open de oudste.", "Er wachten geen zaken op uw besluit. Een nieuwe zaak verschijnt hier vanzelf.", "Oudste zaak openen")


def test_the_strip_action_names_where_it_leads_and_opens_the_settings_tab(tmp_path, madeup_out):
    """While the assistant has a problem, one button beside the strip names the place that handles the most urgent one ("Stand van de
    assistent openen" for a runner that made no pass, "Postvak en AI openen" for the store of the sign-in, the mailbox or Copilot)
    and opens the tab Instellingen (strip_acted). It is not there on a public share link or without a settings file."""
    nl, en = STRINGS["nl"], STRINGS["en"]
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    (tmp_path / "share").mkdir()
    _, _, shared, _ = sending_page(tmp_path / "share", madeup_out, share=True)
    for other in (shared, build_app(d)):
        assert not [block for block in other.blocks.values() if getattr(block, "elem_id", None) == "review-strip-action"]
        assert "strip_acted" not in {getattr(f.fn, "__name__", "") for f in other.fns.values()}
    assert bound(page, "strip_acted") == ([("review-strip-action", "click")], ["Taal"], ["Tabs", "settings-sections"])
    assert called(page, "strip_acted", "nl")["Tabs"] == {"__type__": "update", "selected": SETTINGS_TAB} and SETTINGS_TAB == "instellingen"
    shown = called(page, "strip_ticked", "nl")
    assert shown["review-strip-action"] == {"__type__": "update", "value": nl["act:assistant"], "visible": True}      # no pass yet
    digest_state(d, 11)
    shown = called(page, "strip_ticked", "en")
    assert en["problem:no_pass"].format(time="12-11-2026 08:49") in shown["review-strip"]
    assert shown["review-strip-action"] == {"__type__": "update", "value": en["act:assistant"], "visible": True}
    digest_state(d, 1)
    assert called(page, "strip_ticked", "nl")["review-strip-action"] == {"__type__": "update", "visible": False}    # nothing wrong
    (tmp_path / ".git").mkdir()          # the store of the sign-in (tmp_path/default-secrets in the tests) lies in a git work tree
    shown = called(page, "strip_ticked", "nl")
    assert nl["problem:store"] in shown["review-strip"]
    assert shown["review-strip-action"] == {"__type__": "update", "value": nl["act:connections"], "visible": True}
    assert (nl["act:assistant"], nl["act:connections"]) == ("Stand van de assistent openen", "Postvak en AI openen")


def test_the_strip_action_opens_the_section_that_handles_the_problem(tmp_path, madeup_out, monkeypatch):
    """Findings 2, 6 and 8. The strip's button opens the tab Instellingen on the section of it that handles the most urgent problem,
    the one the button names, as the aid's files tell it at the press (strip_acted): "Stand van de assistent" for a runner that made
    no pass, a STOP file or a runner the launcher gave up, "Postvak en AI" for a refused store of the sign-in, a lapsed sign-in of
    the mailbox and Copilot, failing or refusing documents. A press after the problem went opens "Stand van de assistent", which
    says what the assistant is doing now. That section is read again after every press (assistant_shown, chained after it), and
    nothing asks Microsoft."""
    nl, en = STRINGS["nl"], STRINGS["en"]
    path, d, page, _ = sending_page(tmp_path, madeup_out, reader="llm", mail_folder="AAMkADMadeUpFolder0001=",
                                    mail_folder_name="Orderbevestigingen", **MAILBOX)
    [tabs] = [block for block in page.blocks.values() if isinstance(block, gradio.Tabs) and block.elem_classes == ["page-tabs"]]
    [sections] = [block for block in page.blocks.values() if isinstance(block, gradio.Tabs) and block.elem_id == "settings-sections"]
    assert {"koppelingen", "assistent"} <= {section.id for section in sections.children}
    acted = handler_of(page, "strip_acted")
    assert [(page.blocks[block].elem_id, event) for block, event in acted.targets] == [("review-strip-action", "click")]
    assert [name_of(block) for block in acted.inputs] == ["Taal"] and acted.outputs == [tabs, sections] and acted.show_progress == "hidden"
    [after] = [f for f in page.fns.values() if f.trigger_after == acted._id]
    assert getattr(after.fn, "__name__", "") == "assistant_shown" and after.inputs == acted.inputs and after.show_progress == "hidden"
    assert [block.elem_id for block in after.outputs] == ["assistant-lines", "assistant-log", "assistant-log-help"]

    def no_network(*args, **kwargs):
        raise OSError("no network in the tests")

    def made_one(*args, **kwargs):
        raise AssertionError("the strip's button made a Microsoft account")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(MicrosoftAccount, "__init__", made_one)

    def pressed(language: str = "nl") -> tuple[str, str | None]:
        """The section a press opens now, with the tab Instellingen, and the label of the button as the strip shows it now (None
        while it is hidden)."""
        page_tab, section = acted.fn(language)
        assert page_tab == {"__type__": "update", "selected": SETTINGS_TAB}
        return section["selected"], called(page, "strip_ticked", language)["review-strip-action"].get("value")

    assistant, connections = ("assistent", nl["act:assistant"]), ("koppelingen", nl["act:connections"])
    assert pressed() == assistant                                          # no pass yet
    digest_state(d, 11)
    assert pressed() == assistant                                          # no pass for 11 minutes
    digest_state(d, 1)
    (path.parent / "STOP").write_text("", encoding="utf-8")
    assert pressed() == assistant                                          # a STOP file next to the settings file
    (path.parent / "STOP").unlink()
    launcher_state(path, NOW.timestamp() - 1, starts=6, given_up=True)
    assert pressed() == assistant                                          # the launcher gave the runner up
    launcher_state(path, NOW.timestamp() - 1)
    assert pressed() == ("assistent", None)                               # nothing wrong: no button, and the state of the assistant
    (tmp_path / ".git").mkdir()                                            # the store of the sign-in lies inside a git work tree
    assert pressed() == connections
    digest_state(d, 11)
    assert pressed() == assistant                                          # the most urgent first, as the button names it
    (tmp_path / ".git").rmdir()
    digest_state(d, 1, mailbox_lapsed={"since": "2026-11-12T08:40:00", "logged": "2026-11-12T08:40:00"})
    assert pressed() == connections                                        # the sign-in of the mailbox lapsed
    digest_state(d, 1, copilot_cases={"250105_20261112T080000": {"refused": True}})
    assert pressed("en") == ("koppelingen", en["act:connections"])        # Copilot refuses documents
    digest_state(d, 1, copilot={"since": "2026-11-12T08:15:00", "error": "Copilot refused: 400 BadRequest", "waiting": 0})
    assert pressed() == connections                                        # Copilot fails as a whole
    lines, log, help_line = after.fn("nl")                                 # the section read again after the press
    assert f"**{nl['reader_state:down'].format(since='12-11-2026 08:15', cause='code 400', n=0)}**" in lines.split("\n\n")
    assert (log, help_line) == (nl["assistant_log_none"], nl["assistant_log_help"]) and "BadRequest" not in lines


def test_a_failed_case_marked_handled_in_another_tab_leaves_no_notice_in_the_old_language(tmp_path, madeup_out):
    """A failed case on screen that another browser tab notes as handled: the language switch hides its notice, as choosing it now
    would (case_framed), rather than leave it in the language it was shown in, and the line under the list says that it was noted as
    handled, not that it was closed or replaced. A document left to the buyer, noted as handled meanwhile, goes the same way."""
    nl, en = STRINGS["nl"], STRINGS["en"]
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    ids = left_to_the_buyer(d, madeup_out, tmp_path)
    failed, _ = run(d, madeup_out, "MU05", "250105")
    assert called(page, "case_framed", failed.case_id, "nl")["review-failed"]["visible"] is True
    called(page, "handled_marked", failed.case_id, "nl")                                     # another browser tab
    hidden = {"__type__": "update", "value": "", "visible": False}
    assert called(page, "failed_relabel", "en", failed.case_id) == {"review-failed": hidden,
                                                                     "review-handled": {"__type__": "update", "value": en["handled_go"]}}
    for strings, language in ((nl, "nl"), (en, "en")):
        gone = called(page, "queue_ticked", language, failed.case_id, None)["review-gone"]
        assert gone == {"__type__": "update", "value": strings["review_gone_handled"].format(po="250105"), "visible": True}, language
    assert (nl["review_gone_handled"], en["review_gone_handled"]) == (
        "Inkooporder {po} staat niet meer op de lijst: de zaak is intussen als afgehandeld genoteerd. Kies een andere zaak.",
        "Purchase order {po} is no longer on the list: it was noted as handled meanwhile. Choose another case.")
    called(page, "handled_marked", ids[SCAN], "nl")
    assert called(page, "failed_relabel", "en", ids[SCAN])["review-failed"] == hidden


def test_only_the_choice_of_a_case_shows_or_hides_the_pick_row(tmp_path, madeup_out):
    """The row with the line under "Zaak" and "Oudste zaak openen" stands while no case is chosen, and follows the value of "Zaak"
    alone: case_framed, bound to its change, shows and hides it, and a tick never does, since the case the tick was sent with may
    have been chosen meanwhile (a page opened from the digest's link, which chooses its case once the page has loaded, or a choice
    made while a tick was on its way), so that the row never stands above an open case until the next tick."""
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    case, _ = run(d, madeup_out, "MU07", "250107")
    for chosen in (None, case.case_id):
        assert called(page, "queue_ticked", "nl", chosen, None)["review-pick-row"] == {"__type__": "update"}, chosen
    assert called(page, "case_framed", None, "nl")["review-pick-row"] == {"__type__": "update", "visible": True}
    assert called(page, "case_framed", case.case_id, "nl")["review-pick-row"] == {"__type__": "update", "visible": False}


class RefusedMailbox:
    """The work mailbox of the runner's Deps, which sends nothing: the store of the sign-in refused (the mailer's SignInLapsed of the
    kind "store", with its fixed text), or the sign-in lapsed (of the kind "sign_in")."""

    def __init__(self, kind: str):
        self.kind = kind

    def send(self, message):
        from aid.messaging.microsoft_mailer import STORE_REFUSED, SignInLapsed
        raise SignInLapsed(STORE_REFUSED if self.kind == "store" else "SignInNeeded: made-up", kind=self.kind)


def test_a_refused_store_is_no_lapsed_sign_in_on_the_strip_after_a_digest(tmp_path, madeup_out):
    """Finding 2. A digest that the work mailbox cannot send because this PC refuses the store of the sign-in goes to the outbox, as
    for a lapsed sign-in, but it is no lapse of the mailbox: the runner notes no lapse in its digest state and logs one line in a run
    of the loop, so that neither the strip nor "Stand van de assistent" tells the buyer that the sign-in lapsed and to sign in again,
    which cannot help; they tell the refused store (here one inside a git work tree, which the page tells by itself). A digest that a
    lapsed sign-in cannot send still notes the lapse."""
    from aid import runner
    from aid.messaging.microsoft_mailer import STORE_REFUSED
    nl = STRINGS["nl"]
    path, d, page, _ = sending_page(tmp_path, madeup_out, send_via="microsoft", digest="per_case", mail_folder="AAMkADMadeUpFolder0001=",
                                    mail_folder_name="Orderbevestigingen", **MAILBOX)
    review, _ = run(d, madeup_out, "MU07", "250107")
    (tmp_path / ".git").mkdir()                          # the owner's case: .git above the store of the sign-in
    root = d.settings.cases_root
    loop = runner._Loop(d.model_copy(update={"mailer": RefusedMailbox("store")}))
    for minute in (0, 1):                                # two digests in one run of the loop
        runner._digest(loop, root, {"last_sent": None, "reported": [review.case_id], "skipped": []}, NOW + timedelta(minutes=minute))
    saved = json.loads((root / "digest_state.json").read_text(encoding="utf-8"))
    assert "mailbox_lapsed" not in saved and saved["last_sent"] == (NOW + timedelta(minutes=1)).isoformat()
    assert len(list((root / "outbox").glob("*.eml"))) == 2
    [line] = (root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert line == f"{NOW.isoformat()} mailbox: the digest is written to outbox/, as the work mailbox cannot send it: {STORE_REFUSED}"
    strip = called(page, "strip_ticked", "nl")["review-strip"]
    assert nl["problem:store"] in strip and nl["problem:lapsed"] not in strip
    lines = called(page, "sections_relabelled", "nl")["assistant-lines"].split("\n\n")
    assert f"**{nl['assistant_store']}**" in lines and not [text for text in lines if "verlopen" in text]
    lapsed = runner._Loop(d.model_copy(update={"mailer": RefusedMailbox("sign_in")}))
    runner._digest(lapsed, root, {"last_sent": None, "reported": [review.case_id], "skipped": []}, NOW + timedelta(minutes=2))
    assert "mailbox_lapsed" in json.loads((root / "digest_state.json").read_text(encoding="utf-8"))
    assert nl["problem:lapsed"] in called(page, "strip_ticked", "nl")["review-strip"]


def test_a_failed_case_is_offered_with_why_and_can_be_marked_handled(tmp_path, madeup_out):
    """Finding 3. A case the assistant could not finish is offered after the cases offered_cases gives, as "mislukt: zelf bekijken";
    chosen, it shows its supplier's document and why it failed in words, never by its error, and "Als afgehandeld noteren" in place
    of "Zaak afsluiten". The press writes handled_by_hand.json into the case's folder with the time of the page's clock, and no other
    file, and the case leaves the list and the counts. offered_cases and selection_from_query stay as they are, so that the digest's
    links do not change."""
    nl, en = STRINGS["nl"], STRINGS["en"]
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    failed, result = run(d, madeup_out, "MU05", "250105")                  # the parser has no template for the made-up scan
    review, _ = run(d, madeup_out, "MU07", "250107", now=LATER)
    assert result.status == "failed" and result.error.startswith("ReaderError: no template for ")
    root = d.settings.cases_root
    listed = [("PO 250107 (S07), 2026-11-12 09:01, 1 punt", review.case_id), ("PO 250105 (S05), 2026-11-12 09:00, mislukt: zelf bekijken", failed.case_id)]
    assert called(page, "refreshed", "nl", None)["Zaak"]["choices"] == listed == called(page, "queue_ticked", "nl", None, None)["Zaak"]["choices"]
    assert handler_of(page, "opened").fn("nl", SimpleNamespace(query_params={}))[0]["choices"] == listed
    assert called(page, "refreshed", "en", None)["Zaak"]["choices"][-1] == ("PO 250105 (S05), 2026-11-12 09:00, failed: look yourself", failed.case_id)
    assert [summary.case_id for summary in offered_cases(root)] == [review.case_id]
    assert selection_from_query(root, {"case": failed.case_id}) == (None, None)
    framed = called(page, "case_framed", failed.case_id, "nl")
    notice = nl["failed_notice"].format(reason=nl["fail:parser"])
    assert notice.startswith("**De assistent kon deze zaak niet afmaken:** de assistent herkent de opmaak van dit document niet. Bekijk")
    assert framed["review-failed"] == {"__type__": "update", "value": notice, "visible": True}
    assert framed["Zaak afsluiten"] == framed["### Besluiten"] == {"__type__": "update", "visible": False}
    assert framed["review-handled"] == {"__type__": "update", "visible": True}
    other = called(page, "case_framed", review.case_id, "nl")
    assert other["review-failed"] == {"__type__": "update", "value": "", "visible": False} and other["review-handled"] == {"__type__": "update", "visible": False}
    assert other["Zaak afsluiten"] == other["### Besluiten"] == {"__type__": "update", "visible": True}
    english = called(page, "failed_relabel", "en", failed.case_id)
    assert english == {"review-failed": {"__type__": "update", "value": en["failed_notice"].format(reason=en["fail:parser"])},
                       "review-handled": {"__type__": "update", "value": en["handled_go"]}}
    assert called(page, "failed_relabel", "nl", review.case_id)["review-failed"] == {"__type__": "update", "value": "", "visible": False}
    assert bound(page, "failed_relabel") == ([("Taal", "change")], ["Taal", "Zaak"], ["review-failed", "review-handled"])
    shown = handler_of(page, "show").fn(failed.case_id, "nl")
    assert shown[-1] == "" and len(shown[1]) == 1 and shown[2] == [] and shown[3]["data"] == []       # the supplier's document, no rows
    assert bound(page, "handled_marked") == ([("review-handled", "click")], ["Zaak", "Taal"], ["Markdown", "Zaak"])
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    marked = called(page, "handled_marked", failed.case_id, "nl")
    after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    mark = failed.root / "handled_by_hand.json"
    assert set(after) == {*before, mark} and all(after[path] == before[path] for path in before)
    assert json.loads(after[mark]) == {"at": "2026-11-12T09:00:00"}
    assert marked["Markdown"] == nl["handled_done"].format(po="250105") == ("Inkooporder 250105 staat als door u afgehandeld genoteerd en staat "
                                                                            "niet meer op de lijst.")
    assert marked["Zaak"] == {"__type__": "update", "choices": listed[:1], "value": None}
    assert called(page, "handled_marked", failed.case_id, "en")["Markdown"] == en["handled_done"].format(po="250105")     # another browser tab
    assert mark.read_bytes() == after[mark]                                                        # the first mark counts
    assert called(page, "queue_ticked", "nl", None, None)["Beoordelen"] == {"__type__": "update", "label": "Beoordelen (1)"}
    strip = called(page, "strip_ticked", "nl")
    assert "Mislukt" not in strip["review-strip"]
    assert [row[2:] for row in table_rows(strip["review-all-table"])[1:] if row[1] == "Inkooporder 250105 (S05)"] == [[nl["state:handled"], nl["todo:nothing"]]]
    assert [summary.case_id for summary in offered_cases(root)] == [review.case_id] and selection_from_query(root, {"case": failed.case_id}) == (None, None)


# --- the documents the intake left to the buyer, on the tab "Beoordelen" (finding 9 of the owner's test) ---

import io  # noqa: E402

from PIL import Image  # noqa: E402

from aid.readers.pdf_text import page_count  # noqa: E402
from aid.review import page as page_module  # noqa: E402
from aid.review import status as aid_status  # noqa: E402
from aid.sources.mailbox import REVIEW_ITEMS_FILE, scan_folder  # noqa: E402
from aid.tests.test_status import email  # noqa: E402

RAW = ("text layer", "class unknown", "PO number")      # of the scan's own reasons, which the page never shows
SCAN, MANY, PHOTO = "MU05_conf.pdf", "twee.eml/MU01_conf.pdf", "foto.eml/CART02.png"     # as the list of skipped items names them


def left_to_the_buyer(d, madeup_out, tmp_path) -> dict[str, str]:
    """Three documents that open no case, scanned as the runner scans the watched folder of the page's settings, with a PO store that
    holds no order: the made-up scan MU05, which has no text layer, and an e-mail that names two purchase orders, at NOW, and an
    e-mail with a photo of a page a minute later. The scan leaves each to the buyer in review_items.jsonl and moves each file to
    processed/ in the watched folder. Returns the id of each document by its file as the list of skipped items names it."""
    inbox, root = d.settings.watched_folder, d.settings.cases_root
    inbox.mkdir(parents=True, exist_ok=True)
    shutil.copy(madeup_out / "MU05_conf.pdf", inbox)
    email(inbox / "twee.eml", "Bevestiging PO 250101 en PO 250102", madeup_out / "MU01_conf.pdf")
    none = PoStore(tmp_path / "no-orders")
    first = scan_folder(inbox, none, root, d.settings, NOW)
    email(inbox / "foto.eml", "Bevestiging", madeup_out / "CART02.png")
    second = scan_folder(inbox, none, root, d.settings, LATER)
    assert first.skipped + second.skipped == [(SCAN, "no text layer; class unknown"), (MANY, "more than one PO number"),
                                              (PHOTO, "no text layer; class unknown")]
    assert sorted(path.name for path in (inbox / "processed").iterdir()) == ["MU05_conf.pdf", "foto.eml", "twee.eml"]
    return {item.file: item.item_id for item in aid_status.review_items(root, NOW)}


def images_of(path) -> list[bytes]:
    """The pixels of each page of the document as the page renders it, at 100 dpi."""
    pages = [render_page(path, number, dpi=100) for number in range(1, page_count(path) + 1)] if path.suffix == ".pdf" else [path.read_bytes()]
    return [Image.open(io.BytesIO(page)).tobytes() for page in pages]


def test_a_document_left_to_the_buyer_is_offered_after_the_failed_cases_and_named_in_words(tmp_path, madeup_out):
    """Finding 9. A document the intake left to the buyer to look at himself, which opens no case (a scan that nobody could class, a
    confirmation that names more than one purchase order), is offered in the case list after the failed cases, oldest first, as
    "Document MU05_conf.pdf, 2026-11-12 09:00, zelf bekijken". The strip counts it ("Zelf bekijken: 3"), the tab's label counts it
    with the cases that wait for the buyer, and the fold lists it with its state and what the buyer does, among the cases by its
    time: all in words, never by the scan's own reason. "Oudste zaak openen" takes the oldest case in review, else the oldest failed
    case, else the oldest document."""
    nl, en = STRINGS["nl"], STRINGS["en"]
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    ids = left_to_the_buyer(d, madeup_out, tmp_path)
    failed, _ = run(d, madeup_out, "MU05", "250105")
    review, _ = run(d, madeup_out, "MU07", "250107", now=LATER)
    documents = [(f"Document {SCAN}, 2026-11-12 09:00, zelf bekijken", ids[SCAN]), (f"Document {MANY}, 2026-11-12 09:00, zelf bekijken", ids[MANY]),
                 (f"Document {PHOTO}, 2026-11-12 09:01, zelf bekijken", ids[PHOTO])]
    listed = [("PO 250107 (S07), 2026-11-12 09:01, 1 punt", review.case_id),
              ("PO 250105 (S05), 2026-11-12 09:00, mislukt: zelf bekijken", failed.case_id), *documents]
    ticked = called(page, "queue_ticked", "nl", None, None)
    assert ticked["Zaak"]["choices"] == listed == called(page, "refreshed", "nl", None)["Zaak"]["choices"]
    assert handler_of(page, "opened").fn("nl", SimpleNamespace(query_params={}))[0]["choices"] == listed
    assert called(page, "refreshed", "en", None)["Zaak"]["choices"][2:3] == [(f"Document {SCAN}, 2026-11-12 09:00, look yourself", ids[SCAN])]
    assert all(aid_status.is_item(value) for _, value in documents)
    root = d.settings.cases_root                                  # offered_cases and the digest's links stay as they are
    assert [summary.case_id for summary in offered_cases(root)] == [review.case_id] and selection_from_query(root, {"case": ids[SCAN]}) == (None, None)
    assert ticked["Beoordelen"] == {"__type__": "update", "label": "Beoordelen (5)"}
    [review_tab] = [block for block in page.blocks.values() if isinstance(block, gradio.Tab) and block.label == nl["review_tab"]]
    [relabel] = [f.fn for f in page.fns.values() if f.outputs[:1] == [review_tab] and getattr(f.fn, "__name__", "") == "<lambda>"]
    assert relabel("en")[0] == called(page, "queue_ticked", "en", None, None)["Beoordelen"] == {"__type__": "update", "label": "Review (5)"}
    strip = called(page, "strip_ticked", "nl")
    assert (nl["chip:look"], en["chip:look"]) == ("Zelf bekijken: {n}", "To look at: {n}") and "Zelf bekijken: 3" in strip["review-strip"]
    assert "To look at: 3" in called(page, "strip_ticked", "en")["review-strip"]
    assert strip["review-all"] == {"__type__": "update", "label": nl["review_all"].format(n=5)}
    todo = "Kies het document bij Zaak, bekijk het en handel het zelf af."
    assert (nl["todo:look"], en["todo:look"]) == (todo, "Choose the document under Case, look at it and handle it yourself.")
    assert table_rows(strip["review-all-table"])[1:] == [
        ["12-11-2026 09:01", "Inkooporder 250107 (S07)", nl["state:review"], nl["todo:review"]],
        ["12-11-2026 09:01", f"Document {PHOTO}", "Zelf bekijken", todo],
        ["12-11-2026 09:00", "Inkooporder 250105 (S05)", nl["state:failed"], nl["todo:failed"]],
        ["12-11-2026 09:00", f"Document {SCAN}", "Zelf bekijken", todo],
        ["12-11-2026 09:00", f"Document {MANY}", "Zelf bekijken", todo]]
    for shown in (ticked, strip, called(page, "strip_ticked", "en"), called(page, "queue_ticked", "en", None, None)):
        assert not [words for words in RAW if words in str(shown)]
    assert called(page, "oldest_opened", "nl")["Zaak"]["value"] == review.case_id
    decide(review, "RI1", "keep")
    assert close_case(review, d).status == "closed"
    assert called(page, "oldest_opened", "nl")["Zaak"]["value"] == failed.case_id
    called(page, "handled_marked", failed.case_id, "nl")
    assert called(page, "oldest_opened", "nl")["Zaak"] == {"__type__": "update", "choices": documents, "value": ids[SCAN]}
    none = called(page, "case_framed", None, "nl")
    assert none["review-pick"] == nl["review_pick"] and none["review-oldest"] == {"__type__": "update", "value": nl["review_open_oldest"], "visible": True}
    assert called(page, "queue_ticked", "nl", None, None)["Beoordelen"] == {"__type__": "update", "label": "Beoordelen (3)"}


def test_a_document_left_to_the_buyer_shows_why_and_its_pages_where_the_scan_put_it(tmp_path, madeup_out):
    """Finding 9. A document chosen in the case list shows why the assistant could not place it, in words, with what to do, in the
    place and the look of the notice of a failed case, and "Als afgehandeld noteren" in place of "Zaak afsluiten" and of the title of
    the decisions; its pages show in the supplier's gallery, rendered as a case's document is, from where the scan put it: the file in
    processed/ of the watched folder, or the attachment inside its e-mail there, of two files of the same name the one whose bytes the
    scan noted. A document that is no longer there shows no page, and the notice says where to look for it."""
    nl, en = STRINGS["nl"], STRINGS["en"]
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    ids = left_to_the_buyer(d, madeup_out, tmp_path)
    processed = d.settings.watched_folder / "processed"
    framed = called(page, "case_framed", ids[SCAN], "nl")
    notice = ("**De assistent kon dit document niet plaatsen:** het is een scan zonder leesbare tekst, en de assistent kon niet zien wat voor "
              "document het is. Bekijk het document en handel het zelf af in Exact. Druk daarna op Als afgehandeld noteren; het document "
              "gaat dan van de lijst.")
    assert nl["item_notice"].format(reason=nl["look:scan"]) == notice
    assert framed["review-failed"] == {"__type__": "update", "value": notice, "visible": True}
    assert framed["Zaak afsluiten"] == framed["### Besluiten"] == {"__type__": "update", "visible": False}
    assert framed["review-handled"] == {"__type__": "update", "visible": True}
    assert framed["review-pick-row"] == {"__type__": "update", "visible": False} and framed["review-gone"] == {"__type__": "update", "value": "", "visible": False}
    many = called(page, "case_framed", ids[MANY], "nl")["review-failed"]["value"]
    assert many == nl["item_notice"].format(reason="het noemt meer dan één inkooporder, en de assistent kon niet kiezen bij welke het hoort")
    assert called(page, "failed_relabel", "en", ids[MANY]) == {"review-failed": {"__type__": "update", "value": en["item_notice"].format(reason=en["look:many"])},
                                                                "review-handled": {"__type__": "update", "value": en["handled_go"]}}
    assert en["item_notice"].startswith("**The assistant could not place this document:** {reason}. Look at the document and handle it yourself")
    show = handler_of(page, "show").fn
    for file, document in ((SCAN, madeup_out / "MU05_conf.pdf"), (MANY, madeup_out / "MU01_conf.pdf"), (PHOTO, madeup_out / "CART02.png")):
        shown = show(ids[file], "nl")
        assert [image.tobytes() for image in shown[1]] == images_of(document), file
        assert shown[0] == {"__type__": "update", "visible": False} and shown[2] == [] and [table["data"] for table in shown[3:6]] == [[], [], []]
        assert shown[6] == {"__type__": "update", "choices": [], "value": None} and shown[7] is None and shown[-1] == ""
        assert shown[8] == {"__type__": "update", "value": "", "visible": False} == shown[9]
    assert not (d.settings.cases_root / "tmp").exists()                       # an attachment's copy for its pages went again
    later = processed / "MU05_conf_2.pdf"                                     # a later document of the same name, filed by a later scan
    shutil.copy(madeup_out / "MU01_conf.pdf", later)
    os.utime(processed / SCAN, ((NOW - timedelta(minutes=1)).timestamp(),) * 2)
    os.utime(later, ((NOW + timedelta(hours=1)).timestamp(),) * 2)
    assert [image.tobytes() for image in show(ids[SCAN], "nl")[1]] == images_of(madeup_out / "MU05_conf.pdf")
    (processed / "foto.eml").unlink()
    gone = called(page, "case_framed", ids[PHOTO], "nl")["review-failed"]
    missing = "Het document staat niet meer op deze pc: zoek het in uw postvak, in de e-mail van 12-11-2026 09:01."
    assert gone == {"__type__": "update", "value": f"{nl['item_notice'].format(reason=nl['look:scan'])} {missing}", "visible": True}
    assert called(page, "failed_relabel", "en", ids[PHOTO])["review-failed"]["value"].endswith(
        " The document is no longer on this PC: look for it in your mailbox, in the e-mail of 12-11-2026 09:01.")
    assert show(ids[PHOTO], "nl")[1] == []
    assert not [words for words in RAW if words in str(framed) + many + gone["value"]]


def scanned_as(d, tmp_path, document: Path, name: str, at: datetime, modified: datetime) -> None:
    """The document put into the watched folder of the page's settings under `name`, with this modification time, and scanned at `at`
    as the runner scans the folder, with a PO store that holds no order."""
    inbox = d.settings.watched_folder
    inbox.mkdir(parents=True, exist_ok=True)
    shutil.copy(document, inbox / name)
    os.utime(inbox / name, (modified.timestamp(),) * 2)
    scan_folder(inbox, PoStore(tmp_path / "no-orders"), d.settings.cases_root, d.settings, at)


def test_a_document_left_to_the_buyer_shows_its_own_file_of_several_of_the_same_name(tmp_path, madeup_out):
    """Finding 9. Of several documents of the same name that the scan left to the buyer, each shows its own pages, which the page tells
    from the others by the sha256 of its bytes that the scan noted in its line, whatever the times of the files say: a scan that came
    into the watched folder while the runner read the mailbox, after the time of its pass, and a copy that kept the older time of its
    own file, as a copy in Windows Explorer does. Each of two attachments of the same name, in two e-mails of the same name, shows its
    own pages too."""
    nl = STRINGS["nl"]
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    root, processed = d.settings.cases_root, d.settings.watched_folder / "processed"
    photo, turned = tmp_path / "photo.pdf", tmp_path / "turned.pdf"          # two more scans without text, as PDFs of an image
    Image.open(madeup_out / "CART02.png").convert("RGB").save(photo)
    Image.open(madeup_out / "CART02.png").convert("RGB").rotate(180).save(turned)
    later, days = NOW + timedelta(hours=2), NOW + timedelta(days=2)
    scanned_as(d, tmp_path, madeup_out / "MU05_conf.pdf", "scan.pdf", NOW, NOW - timedelta(minutes=5))     # Monday's scan
    scanned_as(d, tmp_path, photo, "scan.pdf", later, later + timedelta(seconds=3))        # it came while the pass read the mailbox
    scanned_as(d, tmp_path, turned, "scan.pdf", days, NOW - timedelta(days=1))             # Wednesday: Sunday's scan, copied in now
    assert sorted(path.name for path in processed.iterdir()) == ["scan.pdf", "scan_2.pdf", "scan_3.pdf"]
    items = aid_status.review_items(root, days)
    assert [(item.file, item.time) for item in items] == [("scan.pdf", NOW), ("scan.pdf", later), ("scan.pdf", days)]
    show = handler_of(page, "show").fn
    for item, own in zip(items, ("scan.pdf", "scan_2.pdf", "scan_3.pdf"), strict=True):
        assert [image.tobytes() for image in show(item.item_id, "nl")[1]] == images_of(processed / own), own
        assert called(page, "case_framed", item.item_id, "nl")["review-failed"]["value"] == nl["item_notice"].format(reason=nl["look:scan"])
    assert len({tuple(images_of(processed / own)) for own in ("scan.pdf", "scan_2.pdf", "scan_3.pdf")}) == 3
    for number, document in enumerate((madeup_out / "MU05_conf.pdf", photo), 1):             # two e-mails "post.eml", each with a "scan.pdf"
        mail = tmp_path / f"mail{number}"
        mail.mkdir()
        shutil.copy(document, mail / "scan.pdf")
        email(mail / "post.eml", "Bevestiging", mail / "scan.pdf")
        scanned_as(d, tmp_path, mail / "post.eml", "post.eml", days + timedelta(minutes=number), NOW - timedelta(days=3))
    attached = [item for item in aid_status.review_items(root, days) if item.file == "post.eml/scan.pdf"]
    assert sorted(path.name for path in processed.iterdir() if path.suffix == ".eml") == ["post.eml", "post_2.eml"] and len(attached) == 2
    for item, document in zip(attached, (madeup_out / "MU05_conf.pdf", photo), strict=True):
        assert [image.tobytes() for image in show(item.item_id, "nl")[1]] == images_of(document), document.name


def test_a_document_that_cannot_be_told_from_another_of_its_name_shows_no_page(tmp_path, madeup_out):
    """Finding 9. A document left to the buyer whose bytes no file of its name in processed/ holds any more (another file of the same
    name stands in its place) shows no page, and its notice says where to look for it, rather than another document. A line written
    before the scan noted the sha256 shows the one file of its name, and no page of two, as the page cannot tell which is the one."""
    nl = STRINGS["nl"]
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    ids = left_to_the_buyer(d, madeup_out, tmp_path)
    root, processed = d.settings.cases_root, d.settings.watched_folder / "processed"
    show = handler_of(page, "show").fn
    notice = f"{nl['item_notice'].format(reason=nl['look:scan'])} {nl['item_missing'].format(time='12-11-2026 09:00')}"
    shutil.copy(madeup_out / "MU01_conf.pdf", processed / SCAN)                  # another document under its name
    assert show(ids[SCAN], "nl")[1] == []
    assert called(page, "case_framed", ids[SCAN], "nl")["review-failed"] == {"__type__": "update", "value": notice, "visible": True}
    shutil.copy(madeup_out / "MU05_conf.pdf", processed / SCAN)                  # its own again
    lines = [json.loads(line) for line in (root / REVIEW_ITEMS_FILE).read_text(encoding="utf-8").splitlines()]
    assert all(len(line["sha256"]) == 64 for line in lines)
    (root / REVIEW_ITEMS_FILE).write_text("".join(json.dumps({key: value for key, value in line.items() if key != "sha256"}) + "\n"
                                                  for line in lines), encoding="utf-8")     # as a scan before this version wrote them
    assert aid_status.look_item(root, ids[SCAN]).sha256 is None
    assert [image.tobytes() for image in show(ids[SCAN], "nl")[1]] == images_of(madeup_out / "MU05_conf.pdf")
    assert called(page, "case_framed", ids[SCAN], "nl")["review-failed"]["value"] == nl["item_notice"].format(reason=nl["look:scan"])
    shutil.copy(madeup_out / "MU01_conf.pdf", processed / "MU05_conf_2.pdf")    # a second file of its name
    assert show(ids[SCAN], "nl")[1] == []
    assert called(page, "case_framed", ids[SCAN], "nl")["review-failed"] == {"__type__": "update", "value": notice, "visible": True}


def test_marking_a_document_handled_writes_its_mark_alone_and_takes_it_off_the_list(tmp_path, madeup_out):
    """Finding 9. "Als afgehandeld noteren" on a document appends its mark to review_items_handled.jsonl in the cases folder, with the
    time of the page's clock, and writes nothing else; the document then leaves the list, the strip's chip and the tab's count, and
    the fold shows it as handled by the buyer. A mark that stands already (from another browser tab) is not written again, and a mark
    after a line that a crash cut off starts a line of its own."""
    nl, en = STRINGS["nl"], STRINGS["en"]
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    ids = left_to_the_buyer(d, madeup_out, tmp_path)
    root = d.settings.cases_root
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    marked = called(page, "handled_marked", ids[SCAN], "nl")
    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    mark = root / aid_status.HANDLED_ITEMS_FILE
    assert set(after) == {*before, mark} and all(after[path] == before[path] for path in before)
    assert [json.loads(line) for line in after[mark].splitlines()] == [{"id": ids[SCAN], "file": SCAN, "time": "2026-11-12T09:00:00",
                                                                         "at": "2026-11-12T09:00:00"}]
    assert marked["Markdown"] == nl["item_handled_done"].format(file="MU05\\_conf\\.pdf") == (
        "Het document MU05\\_conf\\.pdf staat als door u afgehandeld genoteerd en staat niet meer op de lijst.")
    rest = [(f"Document {MANY}, 2026-11-12 09:00, zelf bekijken", ids[MANY]), (f"Document {PHOTO}, 2026-11-12 09:01, zelf bekijken", ids[PHOTO])]
    assert marked["Zaak"] == {"__type__": "update", "choices": rest, "value": None}
    again = called(page, "handled_marked", ids[SCAN], "en")                  # another browser tab
    assert again["Markdown"] == en["item_handled_done"].format(file="MU05\\_conf\\.pdf") and mark.read_bytes() == after[mark]
    assert called(page, "queue_ticked", "nl", None, None)["Beoordelen"] == {"__type__": "update", "label": "Beoordelen (2)"}
    strip = called(page, "strip_ticked", "nl")
    assert "Zelf bekijken: 2" in strip["review-strip"]
    assert [row for row in table_rows(strip["review-all-table"]) if row[1] == f"Document {SCAN}"] == [
        ["12-11-2026 09:00", f"Document {SCAN}", "Door u afgehandeld", nl["todo:nothing"]]]
    with open(mark, "ab") as cut:
        cut.write(b'{"id": "item-0123')                                       # a mark that a crash cut off
    called(page, "handled_marked", ids[PHOTO], "nl")
    assert [item.file for item in aid_status.review_items(root, NOW) if item.state == "look"] == [MANY]
    assert called(page, "queue_ticked", "nl", None, None)["Zaak"]["choices"] == rest[:1]
    assert "Zelf bekijken: 1" in called(page, "strip_ticked", "nl")["review-strip"]
    assert (root / REVIEW_ITEMS_FILE).read_bytes() == before[root / REVIEW_ITEMS_FILE]


def test_the_handlers_of_the_case_list_take_a_document_without_reading_it_as_a_case(tmp_path, madeup_out, monkeypatch):
    """Finding 9. With a document chosen, every handler that takes the value of "Zaak" answers without reading the document's id as a
    case (load_case is never asked for it) and raises nothing: the language switch and "Lijst vernieuwen" keep it chosen and treat it
    as no case, a close and an undo answer as with no case chosen. A document on screen that another browser tab noted as handled
    keeps its place in the list, with a line that says so, until another case is chosen."""
    nl, en = STRINGS["nl"], STRINGS["en"]
    path, d, page, _ = sending_page(tmp_path, madeup_out)
    ids = left_to_the_buyer(d, madeup_out, tmp_path)
    run(d, madeup_out, "MU07", "250107")
    chosen = ids[SCAN]
    real = page_module.load_case

    def load_case_of_a_case(cases_root, case_id):
        assert not aid_status.is_item(case_id), case_id
        return real(cases_root, case_id)

    monkeypatch.setattr(page_module, "load_case", load_case_of_a_case)
    relabelled = called(page, "relabel", "en", chosen, None)
    assert (f"Document {SCAN}, 2026-11-12 09:00, look yourself", chosen) in relabelled["Zaak"]["choices"]
    assert relabelled["review-hint"] == {"__type__": "update", "value": "", "visible": False}
    assert relabelled[nl["undo_choice"]] == {"__type__": "update", "label": en["undo_choice"], "choices": [], "value": None}
    refreshed = called(page, "refreshed", "nl", chosen)
    assert (f"Document {SCAN}, 2026-11-12 09:00, zelf bekijken", chosen) in refreshed["Zaak"]["choices"]
    assert refreshed["review-hint"] == {"__type__": "update", "value": "", "visible": False}
    assert called(page, "close", chosen, "nl") == {"Markdown": nl["no_case"], "Zaak": {"__type__": "update"}}
    assert after_the_press(page, SimpleNamespace(case_id=chosen), "en") == (en["no_case"], chosen)
    for entry_id in (None, "W1"):
        assert called(page, "do_undo", chosen, entry_id, "nl") == {"Markdown": nl["choose_write"], nl["undo_choice"]: {"__type__": "update"}}
    ticked = called(page, "queue_ticked", "nl", chosen, None)
    assert ticked["review-gone"] == {"__type__": "update", "value": "", "visible": False} and ticked["review-pick-row"] == {"__type__": "update"}
    assert called(page, "failed_relabel", "nl", chosen)["review-failed"]["value"].startswith("**De assistent kon dit document niet plaatsen:**")
    called(page, "strip_ticked", "en")
    handler_of(page, "show").fn(chosen, "en")
    called(page, "handled_marked", chosen, "en")                              # noted as handled in another browser tab
    gone = called(page, "queue_ticked", "nl", chosen, ticked["State"])
    kept = (f"Document {SCAN}, 2026-11-12 09:00, door u afgehandeld", chosen)
    assert gone["Zaak"]["choices"][-1] == kept and gone["review-gone"] == {"__type__": "update", "visible": True, "value": (
        "Het document MU05\\_conf\\.pdf staat niet meer op de lijst: het is intussen als afgehandeld genoteerd. Kies een andere zaak.")}
    assert called(page, "refreshed", "nl", chosen)["Zaak"]["choices"] == gone["Zaak"]["choices"]
    assert called(page, "relabel", "nl", chosen, None)["Zaak"]["choices"] == gone["Zaak"]["choices"]
    assert called(page, "relabel", "en", chosen, None)["Zaak"]["choices"][-1] == (f"Document {SCAN}, 2026-11-12 09:00, handled by you", chosen)
    framed = called(page, "case_framed", chosen, "nl")
    assert framed["review-failed"] == {"__type__": "update", "value": "", "visible": False} and framed["review-handled"] == {"__type__": "update", "visible": False}
    other = called(page, "queue_ticked", "nl", ids[MANY], gone["State"])     # another one chosen: the handled document leaves the list
    assert kept not in other["Zaak"]["choices"] and other["review-gone"] == {"__type__": "update", "value": "", "visible": False}
