"""Tests of the review page (aid/review): the cases that wait for the buyer, one case as the page shows it, the buyer's decisions, the
close and the undo, the strings of the page, the construction of the page and its command, on the made-up set."""
import json
import re
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from email import message_from_bytes, policy
from pathlib import Path
from typing import get_args

import pytest

from aid.comparison import Detail, Findings, Flag, Reason
from aid.execution.writer import PreparedWriter, RetryingWriter, WriteLog, WriteLogEntry, WriterError
from aid.messaging import FileMailer
from aid.pipeline import CaseResult, ReviewItem, escalate_pending, run_case
from aid.readers.pdf_text import render_page
from aid.review.__main__ import company_values
from aid.review.__main__ import main as review_main
from aid.review.app import (CaseSummary, FlagRow, build_app, close_case, decide, import_gradio, list_open_cases, load_case_view, page_rows,
                            undo)
from aid.review.i18n import STRINGS
from aid.review.page import _article_items, close_text, undo_text
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
               "no address for the supplier", "message not sent", "message bounced", "no answer in 3 working days"]
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
    """--share is for the made-up set only: with a settings file that holds an Exact address the command refuses it (exit 2) before
    it builds anything. The example settings hold no company value; an authorization limit is a company value too, and a watched
    folder outside the repository is not (the made-up set may be kept in Drive)."""
    example = json.loads((REPO_ROOT / "aid" / "settings.example.json").read_text(encoding="utf-8"))
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(example | {"exact_base_url": "https://erp.example.com", "watched_folder": str(REPO_ROOT / "aid" / "inbox")}),
                    encoding="utf-8")
    assert review_main(["--settings", str(path), "--share"]) == 2
    assert "internal use only" in capsys.readouterr().err and not (tmp_path / "cases").exists()
    assert company_values(load_settings(REPO_ROOT / "aid" / "settings.example.json")) == []
    assert company_values(load_settings(path)) == ["exact_base_url is set"]
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


def test_the_page_reads_the_query_on_load(tmp_path, madeup_out):
    """The page's load reads the query of the request: the case is selected among the offered cases and the write remembered for the
    undo choices of that case."""
    d = deps(tmp_path, madeup_out, writer=done_writer())
    closed, r = run(d, madeup_out, "MU01", "250101")
    entry_id = r.write_entries[1].entry_id
    app = build_app(d)
    [opened] = [f.fn for f in app.fns.values() if getattr(f.fn, "__name__", "") == "opened"]
    choice, wanted = opened("nl", SimpleNamespace(query_params={"case": closed.case_id, "undo": entry_id}))
    assert choice["value"] == closed.case_id and closed.case_id in [value for _, value in choice["choices"]] and wanted == entry_id
    choice, wanted = opened("nl", SimpleNamespace(query_params={}))
    assert choice["value"] is None and wanted is None


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
