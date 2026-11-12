"""Tests of the pipeline (aid/pipeline.py), with its messages and the end of their wait (and the batch's call of it), and of the upload
source (aid/sources/upload.py), on the made-up set."""
import csv
import hashlib
import io
import json
import os
from datetime import datetime, timedelta
from decimal import Decimal
from email import message_from_bytes, policy
from pathlib import Path

import pytest

from aid import demo, pipeline, versioning
from aid.batch import main as batch_main
from aid.case import load_case, sha256_file
from aid.decider import PROMPT as DECISION_PROMPT
from aid.decider import LlmDecider
from aid.execution.by_hand import MARKS_FILE, changes, mark, open_changes, reconcile, reopen
from aid.execution.ops import WriteOp
from aid.execution.writer import (PREPARED_FILE, PreparedWriter, RetryingWriter, UnknownWrite, UnverifiedWrite, WriteLog, WriteLogEntry, WriterError,
                                  outcome_not_known, undo_entry)
from aid.madeup.generate import BOLD, GAP, TABLE_NL_COLUMNS, MadeUpSpec, Row, Text, export_csv, pdf_bytes, po_pages
from aid.messaging import FileMailer, HandOverUnconfirmed, MailerError, SentRecord, file_reply, pending_asks, record_sent
from aid.pipeline import (DISPOSITION_COLUMNS, GUESSED_PO, UNREAD_LIST, CaseResult, ReviewItem, end_empty_waits, escalate_pending, file_answer,
                          read_with_parser, record_dispositions, run_case)
from aid.po_store import PoStore, PoVersion
from aid.readers.llm_client import LlmResponse, ReaderError, RecordedClient
from aid.readers.supplier_llm import read_supplier_document, reading_request
from aid.routing import Action, MessageSpec
from aid.schema import DocValue, SourceRef
from aid.sources.upload import open_upload_case
from aid.tests import made_up_batch
from aid.tests.conftest import REPO_ROOT, SETTINGS, deps, read_csv
from aid.tests.helpers import ANDERS_LAYOUT, ANDERS_PO, EXPORT_COLUMNS, confirmation_again, filed_on, given_on, newer_po
from aid.tryout import confirmation_pdf as tryout_pdf


def test_no_difference_closes(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out); c = open_upload_case(d.settings.cases_root, [madeup_out / "MU01_conf.pdf"], "250101", "confirmation", d.clock())
    r = run_case(c, d)
    assert r.status == "closed" and {e.op.kind for e in r.write_entries} == {"attach_document", "set_status"}
    assert (c.root / "audit.json").exists() and (c.root / "steps" / "05_compare.json").exists()
    rows = read_csv(record_dispositions(c, r)); assert rows[0]["decision"].startswith("prepared (R01)")

def test_price_above_threshold_goes_to_review(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out); c = open_upload_case(d.settings.cases_root, [madeup_out / "MU07_conf.pdf"], "250107", "confirmation", d.clock())
    r = run_case(c, d); assert r.status == "review" and r.review_items[0].field == "price" and r.write_entries == []

def test_failure_is_recorded(tmp_path, madeup_out):
    def boom(p, k): raise ReaderError("boom")
    d = deps(tmp_path, madeup_out).model_copy(update={"reader": boom})
    c = open_upload_case(d.settings.cases_root, [madeup_out / "MU01_conf.pdf"], "250101", "confirmation", d.clock())
    r = run_case(c, d); assert r.status == "failed" and "boom" in r.error and json.loads((c.root / "audit.json").read_text())["errors"]


# The tests below cover what the rulings of the task and its review add to the three above.

NOW = datetime(2026, 11, 12, 9, 0)                   # the clock of deps()
LATER = NOW + timedelta(minutes=1)                   # for a second case of one PO, which needs a case id of its own
RECORDED = REPO_ROOT / "aid" / "tests" / "fixtures" / "recorded"
STEP_FILES = ["01_po_versions.json", "02_read_document.json", "03_version.json", "04_match.json", "05_compare.json", "06_route.json",
              "07_execute.json", "08_result.json"]


def run(d, madeup_out, case: str, po_number: str, kind="confirmation", document: str | None = None, now: datetime = NOW):
    """Open the made-up case `case` as an upload of its confirmation (or of `document`) and run it; returns the case and its result."""
    c = open_upload_case(d.settings.cases_root, [madeup_out / (document or f"{case}_conf.pdf")], po_number, kind, now)
    return c, run_case(c, d)


def audit(c) -> dict:
    return json.loads((c.root / "audit.json").read_text(encoding="utf-8"))


def recorded_reader(path, kind):
    """The LLM reader over the synthetic recorded answers: no model is called."""
    return read_supplier_document(path, RecordedClient(RECORDED), kind)


def with_settings(d, **changes):
    return d.model_copy(update={"settings": d.settings.model_copy(update=changes)})


def unquoted(path, kind):
    """The parser's reading without the version the document quotes and without the item codes of its lines: on MU03, whose date is
    that of neither version, nothing but the decider can then settle the version."""
    doc = read_with_parser(path, kind)
    return doc.model_copy(update={"version_hint": DocValue(), "lines": [line.model_copy(update={"item_code": DocValue()}) for line in doc.lines]})


class FailingAt:
    """Prepares each change as PreparedWriter does, and raises WriterError at the n-th change, which it does not resolve."""
    name = "failing"

    def __init__(self, out_dir, n: int):
        self.prepared, self.n, self.calls = PreparedWriter(out_dir), n, 0

    def apply(self, op, case_id, rule_id, evidence, now):
        self.calls += 1
        if self.calls == self.n:
            raise WriterError("the ERP refused the change")
        return self.prepared.apply(op, case_id, rule_id, evidence, now)


class Refusing:
    """A write route that refuses every change of one kind, however often it is tried, and makes every other change it is given."""
    name = "route"

    def __init__(self, kind: str):
        self.kind, self.given = kind, []

    def apply(self, op, case_id, rule_id, evidence, now):
        self.given.append(op.kind)
        if op.kind == self.kind:
            raise WriterError("503 service unavailable")
        return WriteLogEntry(entry_id=f"{rule_id}-{op.kind}", case_id=case_id, op=op, status="done", previous=op.old_value, new=op.new_value,
                             rule_id=rule_id, evidence=evidence, timestamp=now.isoformat())


class Unchecked:
    """A write route into a system that accepts every change and cannot check the changes of one kind, however often it is tried:
    for those it raises UnverifiedWrite with the entry of the attempt, made, whose previous is the value it read before the write
    (`read`); with answered False it gets no answer to the write of that kind at all, and raises UnknownWrite with the entry. It
    makes every other change it is given."""
    name = "route"

    def __init__(self, kind: str, read: str, answered: bool = True):
        self.kind, self.read, self.given, self.answered = kind, read, [], answered

    def apply(self, op, case_id, rule_id, evidence, now):
        self.given.append(op.kind)
        entry = WriteLogEntry(entry_id=f"{rule_id}-{op.kind}-{len(self.given)}", case_id=case_id, op=op, status="done", previous=op.old_value,
                              new=op.new_value, rule_id=rule_id, evidence=evidence, timestamp=now.isoformat())
        if op.kind != self.kind:
            return entry
        failed = "the check after the write failed" if self.answered else "no answer to the write"
        raise (UnverifiedWrite if self.answered else UnknownWrite)(failed, entry.model_copy(update={"previous": self.read, "error": failed}))


class Executing:
    """Makes each change (status "done"), as a writer to the ERP does; or returns the statuses given, one per change in turn, with
    `error` on an entry that is not done. It changes nothing anywhere."""
    name = "executing"

    def __init__(self, *statuses: str, error: str | None = None):
        self.statuses, self.error = list(statuses), error

    def apply(self, op, case_id, rule_id, evidence, now):
        status = self.statuses.pop(0) if self.statuses else "done"
        return WriteLogEntry(entry_id=f"{rule_id}-{op.kind}", case_id=case_id, op=op, status=status, previous=op.old_value, new=op.new_value,
                             rule_id=rule_id, evidence=evidence, timestamp=now.isoformat(), error=None if status == "done" else self.error)


class ScriptedClient:
    """A model client that answers every request with the same text; it calls no model."""
    name = "scripted"

    def __init__(self, text: str):
        self.text, self.requests = text, []

    def complete(self, req):
        self.requests.append(req)
        return LlmResponse(text=self.text, model="scripted-model", input_tokens=50, output_tokens=8, seconds=0.25, request_id="r1")


# --- the status of a case ---

def test_a_correction_within_the_threshold_is_written_and_the_case_closes(tmp_path, madeup_out):
    """MU08: the price of line 1, within 2 percent and 25 euro, is written to the PO line and to the article (R04); then the document
    is attached and the status set (R01), last. Each entry is in the write log, with the flag's values and its id as evidence. The
    prepared writer is the writer when the aid has no write route, so its changes are done with: no review item."""
    c, r = run(deps(tmp_path, madeup_out), madeup_out, "MU08", "250108")
    assert r.status == "closed" and r.error is None and [(a.rule_id, a.route) for a in r.actions] == [("R04", "AUTO"), ("R01", "AUTO")]
    assert [(e.op.kind, e.rule_id, e.status, e.previous, e.new) for e in r.write_entries] == [
        ("po_line_price", "R04", "prepared", "12.50", "12.70"), ("article_price", "R04", "prepared", None, "12.70"),
        ("attach_document", "R01", "prepared", None, None), ("set_status", "R01", "prepared", "Besteld", "Bevestigd")]
    assert WriteLog(c.root).entries() == r.write_entries and len(read_csv(tmp_path / "prepared_changes.csv")) == 4
    evidence = r.write_entries[0].evidence
    assert evidence["finding_ids"] == ["F1"] and evidence["findings"] == [r.findings.flags[0].model_dump(mode="json")]
    assert (evidence["findings"][0]["po_value"]["value"], evidence["findings"][0]["doc_value"]["value"]) == ("12.50", "12.70")
    assert r.write_entries[2].op.document == str(c.input_files[0]) and r.write_entries[2].evidence["finding_ids"] == []
    assert r.version.version == 1 and r.findings.version == 1 and r.supplier == "S08" and r.messages == [] and r.review_items == []
    assert sorted(path.name for path in (c.root / "steps").iterdir()) == STEP_FILES and c.read_step("result") == r.model_dump(mode="json")


def test_a_question_waits_for_the_supplier(tmp_path, madeup_out):
    """MU17: the missing line is a question to the supplier (R07). Without a mailer it is recorded as not sent, and the case waits."""
    c, r = run(deps(tmp_path, madeup_out), madeup_out, "MU17", "250117")
    assert r.status == "waiting" and r.write_entries == [] and r.review_items == []
    assert r.messages == [{"rule_id": "R07", "template": "line_missing_extra",
                           "items": [{"po_line_ids": "3", "doc_line_ids": "", "field": "line_presence", "po_value": "KM-1703", "doc_value": ""}],
                           "finding_ids": ["F1"], "status": "not sent (no mailer)", "timestamp": NOW.isoformat()}]
    assert audit(c)["messages"] == r.messages


def test_a_case_with_review_items_and_a_question_is_in_review(tmp_path, madeup_out):
    """MU12: the unit is asked (R06), and the price and the quantity, which cannot be compared across units, go to the buyer (R09).
    The question is recorded, and the case waits for the buyer: review. Each uncompared item is a review item with its field."""
    c, r = run(deps(tmp_path, madeup_out), madeup_out, "MU12", "250112")
    assert r.status == "review" and [(m["rule_id"], m["status"]) for m in r.messages] == [("R06", "not sent (no mailer)")]
    assert [(i.item_id, i.finding_id, i.field, i.reason, i.po_value, i.doc_value) for i in r.review_items] == [
        ("RI1", "U1", "price", "unknown unit conversion", None, None), ("RI2", "U2", "quantity", "unknown unit conversion", None, None)]


def test_a_review_item_carries_the_values_of_its_flag(tmp_path, madeup_out):
    """MU07: the price above the threshold is one review item, with the field and the values of the flag and the reason of its action."""
    c, r = run(deps(tmp_path, madeup_out), madeup_out, "MU07", "250107")
    flag = r.findings.flags[0]
    assert r.review_items == [ReviewItem(item_id="RI1", finding_id="F1", reason="price above threshold", field="price", po_value=flag.po_value,
                                         doc_value=flag.doc_value)]
    assert (flag.po_value.value, flag.doc_value.value) == ("12.50", "14.00")


def test_without_a_version_nothing_is_compared_and_the_case_goes_to_review(tmp_path, madeup_out):
    """A PO the store does not hold: no version, nothing matched or compared (no findings, which is not "no difference"), and one
    review item for the whole case."""
    c, r = run(deps(tmp_path, madeup_out), madeup_out, "MU01", "999999")
    assert r.status == "review" and r.version.version is None and r.version.reason == "no stored version" and r.findings is None
    assert [(a.rule_id, a.route, a.reason) for a in r.actions] == [("R09", "REVIEW", "no version selected")]
    assert [(i.item_id, i.finding_id, i.field, i.reason) for i in r.review_items] == [("RI1", None, None, "no version selected")]
    assert c.read_step("match") == c.read_step("compare") == {"skipped": "no version selected"} and r.write_entries == []
    assert r.supplier == "S01" and r.digest_line == "PO 999999 (S01): ter beoordeling, geen schrijfacties"   # the name the document prints


# --- the writes ---

def test_after_a_change_prepared_because_the_route_failed_no_write_is_applied(tmp_path, madeup_out):
    """MU08 through RetryingWriter(a route that refuses the first change and would make the others, PreparedWriter). The first change
    is prepared by the fallback with the route's error and is a review item "prepared change: apply by hand" naming its entry (a
    failed call becomes a prepared change and a review item). No later operation goes to the writer: each is a review item "not
    applied", in order, without an entry; so the PO is not confirmed (no set_status) and the case is in review."""
    route = Refusing("po_line_price")
    c, r = run(deps(tmp_path, madeup_out, writer=RetryingWriter(route, PreparedWriter(tmp_path))), madeup_out, "MU08", "250108")
    [entry] = r.write_entries
    assert (entry.op.kind, entry.status, entry.error) == ("po_line_price", "prepared", "503 service unavailable")
    assert route.given == ["po_line_price", "po_line_price"] and WriteLog(c.root).entries() == [entry]          # two tries, then nothing
    assert [(i.item_id, i.finding_id, i.field, i.reason, i.entry_id) for i in r.review_items] == [
        ("RI1", "F1", "price", "prepared change: apply by hand", entry.entry_id),
        ("RI2", "F1", "price", "not applied: an earlier write failed (rule R04, article_price, article KM-0801)", None),
        ("RI3", None, None, "not applied: an earlier write failed (rule R01, attach_document)", None),
        ("RI4", None, None, "not applied: an earlier write failed (rule R01, set_status)", None)]
    assert r.status == "review" and r.review_items[0].doc_value == r.findings.flags[0].doc_value
    assert r.digest_line == "PO 250108 (S08): ter beoordeling, 1 voorbereide wijziging"


def test_the_writes_before_a_failed_one_stay_normal_writes(tmp_path, madeup_out):
    """The mirror case: the route makes the first change and refuses the second. The first stays a normal write (no review item,
    "Exact update executed"), the second is prepared and waits for the buyer, and the operations after it are not applied."""
    route = Refusing("article_price")
    c, r = run(deps(tmp_path, madeup_out, writer=RetryingWriter(route, PreparedWriter(tmp_path))), madeup_out, "MU08", "250108")
    assert [(e.op.kind, e.status) for e in r.write_entries] == [("po_line_price", "done"), ("article_price", "prepared")]
    assert route.given == ["po_line_price", "article_price", "article_price"]
    assert [(i.reason, i.entry_id) for i in r.review_items] == [
        ("prepared change: apply by hand", r.write_entries[1].entry_id),
        ("not applied: an earlier write failed (rule R01, attach_document)", None), ("not applied: an earlier write failed (rule R01, set_status)", None)]
    assert [row["decision"] for row in read_csv(record_dispositions(c, r))] == ["Exact update executed (R04)", "prepared (R04)"]
    assert r.status == "review" and r.digest_line == "PO 250108 (S08): ter beoordeling, 1 schrijfactie, 1 voorbereide wijziging"


def test_the_pipeline_stops_at_a_write_made_and_not_verified(tmp_path, madeup_out):
    """MU08 through RetryingWriter(a route whose system accepts the article price and cannot check it, PreparedWriter). The change
    is logged as made, with the price the route read as its previous value and the failed check as its error, and is a review item
    of its own, "write made, not verified: check in Exact", naming its entry. Nothing is prepared, and it counts as a write made in
    the digest line and the disposition log. The operations after it are not applied, as after a failed write, so the PO is not
    confirmed over a correction nobody checked; their items say that an earlier change waits for a look in Exact, since no write
    failed."""
    route = Unchecked("article_price", read="12.40")
    c, r = run(deps(tmp_path, madeup_out, writer=RetryingWriter(route, PreparedWriter(tmp_path))), madeup_out, "MU08", "250108")
    assert route.given == ["po_line_price", "article_price", "article_price"]                           # two tries, then nothing
    assert [(e.op.kind, e.status, e.previous, e.new, e.error) for e in r.write_entries] == [
        ("po_line_price", "done", "12.50", "12.70", None), ("article_price", "done", "12.40", "12.70", "the check after the write failed")]
    assert r.write_entries[1].entry_id == "R04-article_price-2"                                         # the first attempt the system accepted
    assert WriteLog(c.root).entries() == r.write_entries and not (tmp_path / "prepared_changes.csv").exists()
    assert [(i.item_id, i.finding_id, i.field, i.reason, i.entry_id) for i in r.review_items] == [
        ("RI1", "F1", "price", "write made, not verified: check in Exact", "R04-article_price-2"),
        ("RI2", None, None, "not applied: an earlier change waits for a look in Exact (rule R01, attach_document)", None),
        ("RI3", None, None, "not applied: an earlier change waits for a look in Exact (rule R01, set_status)", None)]
    assert r.status == "review" and r.digest_line == "PO 250108 (S08): ter beoordeling, 2 schrijfacties"
    assert [row["decision"] for row in read_csv(record_dispositions(c, r))] == ["Exact update executed (R04)", "Exact update executed (R04)"]
    assert [entry["error"] for entry in audit(c)["writes"]] == [None, "the check after the write failed"]


def test_the_pipeline_stops_at_a_write_whose_outcome_is_not_known(tmp_path, madeup_out):
    """MU08 through a route that gets no answer to the write of the line's price, on either attempt. Nobody knows whether the system
    made the change, so nothing is prepared: the entry is logged as made, with the price the route read and the note that its
    outcome is not known, and its review item has a reason of its own, which does not say "made". Everything else is as for a
    write made and not verified: the operations after it wait for the buyer's look in Exact."""
    route = Unchecked("po_line_price", read="12.60", answered=False)
    c, r = run(deps(tmp_path, madeup_out, writer=RetryingWriter(route, PreparedWriter(tmp_path))), madeup_out, "MU08", "250108")
    [entry] = r.write_entries
    assert route.given == ["po_line_price", "po_line_price"] and not (tmp_path / "prepared_changes.csv").exists()
    assert (entry.op.kind, entry.status, entry.previous, entry.new, entry.error) == ("po_line_price", "done", "12.60", "12.70", "no answer to the write")
    assert outcome_not_known(entry) and WriteLog(c.root).entries() == [entry]
    assert [(i.item_id, i.finding_id, i.reason, i.entry_id) for i in r.review_items] == [
        ("RI1", "F1", "write outcome not known: check in Exact", entry.entry_id),
        ("RI2", "F1", "not applied: an earlier change waits for a look in Exact (rule R04, article_price, article KM-0801)", None),
        ("RI3", None, "not applied: an earlier change waits for a look in Exact (rule R01, attach_document)", None),
        ("RI4", None, "not applied: an earlier change waits for a look in Exact (rule R01, set_status)", None)]
    assert r.status == "review" and r.digest_line == "PO 250108 (S08): ter beoordeling, 1 schrijfactie"


def test_reapproval_is_not_requested_for_a_write_made_and_not_verified(tmp_path, madeup_out):
    """MU08 above the limit, its correction an R10 action: the request to the approver lists the changes made and checked before
    the one that was not, as before a failed write; when the first change is the one not checked, no request is recorded."""
    d = deps(tmp_path, madeup_out)
    limit = d.store.versions("250108")[0].total / 2
    limited = d.model_copy(update={"settings": d.settings.model_copy(update={"authorization_limit": limit}), "store": PoStore(d.store.root, limit)})
    article = RetryingWriter(Unchecked("article_price", read="12.40"), PreparedWriter(tmp_path))
    c, r = run(limited.model_copy(update={"writer": article}), madeup_out, "MU08", "250108")
    assert [(a.rule_id, a.route) for a in r.actions] == [("R10", "AUTO"), ("R01", "AUTO")] and r.status == "review"
    assert [(m["template"], [item["kind"] for item in m["items"]]) for m in r.messages] == [("reapproval", ["po_line_price"])]
    line = RetryingWriter(Unchecked("po_line_price", read="12.60"), PreparedWriter(tmp_path))
    c, r = run(limited.model_copy(update={"writer": line}), madeup_out, "MU08", "250108", now=LATER)
    assert r.status == "review" and r.messages == []
    assert [(e.op.kind, e.status, e.previous) for e in r.write_entries] == [("po_line_price", "done", "12.60")]


def test_a_write_that_failed_waits_for_the_buyer(tmp_path, madeup_out):
    """MU14 through a writer whose entry says it changed nothing: a review item "write failed: <why>", and the operations after it,
    R01's attachment and status, are not applied."""
    c, r = run(deps(tmp_path, madeup_out, writer=Executing("failed", error="the line is locked")), madeup_out, "MU14", "250114")
    [entry] = r.write_entries
    assert r.status == "review" and [(i.reason, i.entry_id) for i in r.review_items] == [
        ("write failed: the line is locked", entry.entry_id), ("not applied: an earlier write failed (rule R01, attach_document)", None),
        ("not applied: an earlier write failed (rule R01, set_status)", None)]
    assert r.digest_line == "PO 250114 (S14): ter beoordeling, 1 mislukte schrijfactie"


def test_reapproval_is_requested_only_for_the_changes_made_before_a_failure(tmp_path, madeup_out):
    """MU08 above a limit of half its PO's total, its correction an R10 action. When the route refuses the article price, the request
    to the approver lists the PO line's price alone, the change made; when it refuses the first change, nothing was made and no
    request is recorded."""
    d = deps(tmp_path, madeup_out)
    limit = d.store.versions("250108")[0].total / 2
    limited = d.model_copy(update={"settings": d.settings.model_copy(update={"authorization_limit": limit}), "store": PoStore(d.store.root, limit)})
    c, r = run(limited.model_copy(update={"writer": RetryingWriter(Refusing("article_price"), PreparedWriter(tmp_path))}), madeup_out, "MU08", "250108")
    assert [(a.rule_id, a.route) for a in r.actions] == [("R10", "AUTO"), ("R01", "AUTO")] and r.status == "review"
    assert [(m["template"], [item["kind"] for item in m["items"]]) for m in r.messages] == [("reapproval", ["po_line_price"])]
    c, r = run(limited.model_copy(update={"writer": RetryingWriter(Refusing("po_line_price"), PreparedWriter(tmp_path))}), madeup_out, "MU08", "250108",
               now=LATER)
    assert r.status == "review" and r.messages == [] and [e.op.kind for e in r.write_entries] == ["po_line_price"]


def test_deps_can_be_built_twice_on_one_tmp_path(tmp_path, madeup_out):
    assert deps(tmp_path, madeup_out).store.versions("250101") == deps(tmp_path, madeup_out).store.versions("250101")


def test_a_writer_failure_stops_the_case_with_the_writes_so_far_in_the_log(tmp_path, madeup_out):
    """MU08 with a writer that fails at the second change: the first change is in the write log, the result and the audit, the
    changes after it are not tried, and the case fails with the writer's error, which names the write it failed at."""
    c, r = run(deps(tmp_path, madeup_out, writer=FailingAt(tmp_path, 2)), madeup_out, "MU08", "250108")
    assert r.status == "failed" and r.error == "WriterError: the ERP refused the change (write: rule R04, article_price, article KM-0801)"
    assert [e.op.kind for e in r.write_entries] == ["po_line_price"] and WriteLog(c.root).entries() == r.write_entries
    record = audit(c)
    assert [w["op"]["kind"] for w in record["writes"]] == ["po_line_price"] and record["errors"] == [{"step": "execute", "message": r.error}]
    assert not (c.root / "steps" / "07_execute.json").exists() and c.read_step("result")["status"] == "failed"
    assert [a.rule_id for a in r.actions] == ["R04", "R01"] and r.digest_line == "PO 250108 (S08): mislukt, 1 voorbereide wijziging"


def test_a_change_the_write_log_cannot_hold_is_in_the_result_and_the_audit(tmp_path, madeup_out):
    """MU08 with a folder where the case's write log would be: the first change is made and cannot be logged, and it is in the result
    and in the audit all the same; the error names the write."""
    d = deps(tmp_path, madeup_out)
    c = open_upload_case(d.settings.cases_root, [madeup_out / "MU08_conf.pdf"], "250108", "confirmation", NOW)
    (c.root / "write_log.jsonl").mkdir()
    r = run_case(c, d)
    assert r.status == "failed" and r.error.startswith("IsADirectoryError: ") and r.error.endswith("(write: rule R04, po_line_price, line 1)")
    assert [e.op.kind for e in r.write_entries] == ["po_line_price"]
    assert [w["entry_id"] for w in audit(c)["writes"]] == [r.write_entries[0].entry_id] and "Traceback" in audit(c)["errors"][0]["message"]


# --- failures ---

def test_a_scan_fails_at_reading_with_the_parser(tmp_path, madeup_out):
    """MU05 has no text layer, so no template fits it: the case fails at reading, with the error in the result and in the audit, and its
    result is written all the same. The PO's supplier is known from the store."""
    c, r = run(deps(tmp_path, madeup_out), madeup_out, "MU05", "250105")
    assert r.status == "failed" and r.error == "ReaderError: no template for MU05_conf.pdf"
    assert (r.version, r.findings, r.actions, r.write_entries) == (None, None, [], [])
    assert audit(c)["errors"] == [{"step": "read_document", "message": r.error}]
    assert sorted(path.name for path in (c.root / "steps").iterdir()) == ["01_po_versions.json", "08_result.json"]
    assert c.read_step("result")["status"] == "failed" and r.digest_line == "PO 250105 (S05): mislukt, geen schrijfacties"


def test_a_fault_behind_no_template_keeps_its_traceback(tmp_path, madeup_out, monkeypatch):
    """A pydantic ValidationError is a ValueError, so the parser reader turns it into "no template" like a layout that does not fit;
    the ReaderError keeps it as its cause, and the audit keeps the traceback of both."""
    def faulty(pdf, template):
        return SourceRef.model_validate({"page": 1})                    # a reading the schema refuses: a fault, not a layout

    monkeypatch.setattr("aid.pipeline.read_with_template", faulty)
    c, r = run(deps(tmp_path, madeup_out), madeup_out, "MU01", "250101")
    assert r.status == "failed" and r.error.startswith("ReaderError: no template for MU01_conf.pdf: 2 validation errors for SourceRef")
    [error] = audit(c)["errors"]
    assert error["message"].startswith(r.error) and "Traceback" in error["message"] and "pydantic_core._pydantic_core.ValidationError" in error["message"]


def test_a_reader_error_records_what_its_call_took(tmp_path, madeup_out):
    """A ReaderError that carries the model's last answer: the call is recorded, so that the cost of a failed reading is not lost."""
    response = LlmResponse(text="", model="m", input_tokens=120, output_tokens=7, seconds=1.5, request_id="req-1", attempts=2)

    def cut_off(path, kind):
        raise ReaderError("the answer was cut off at max_tokens", response, attempts=2)

    c, r = run(deps(tmp_path, madeup_out).model_copy(update={"reader": cut_off}), madeup_out, "MU01", "250101")
    assert r.status == "failed" and r.error == "ReaderError: the answer was cut off at max_tokens"
    assert audit(c)["calls"] == [{"kind": "reader", "model": "m", "input_tokens": 120, "output_tokens": 7, "cost_eur": None, "seconds": 1.5,
                                  "request_id": "req-1", "attempts": 2}]


def test_an_unexpected_error_fails_the_case_with_its_traceback_in_the_audit(tmp_path, madeup_out):
    """An error that is not a reader's or a writer's is a fault of the aid: the audit keeps its traceback, the result its type and text."""
    def broken(path, kind):
        raise KeyError("lines")

    c, r = run(deps(tmp_path, madeup_out).model_copy(update={"reader": broken}), madeup_out, "MU01", "250101")
    assert r.status == "failed" and r.error == "KeyError: 'lines'"
    [error] = audit(c)["errors"]
    assert error["step"] == "read_document" and error["message"].startswith(r.error) and "Traceback" in error["message"]


def test_a_case_is_run_once_unless_it_is_asked_to_run_again(tmp_path, madeup_out):
    """A second run would apply the writes again: run_case refuses a case that holds its result, unless rerun is True."""
    d = deps(tmp_path, madeup_out)
    c, r = run(d, madeup_out, "MU01", "250101")
    with pytest.raises(ValueError, match="^case already run"):
        run_case(c, d)
    assert WriteLog(c.root).entries() == r.write_entries and c.read_step("result") == r.model_dump(mode="json")
    assert run_case(c, d, rerun=True).status == "closed" and len(WriteLog(c.root).entries()) == 4     # applied and logged again


def test_a_case_whose_run_was_cut_off_is_run_as_a_new_one(tmp_path, madeup_out):
    """A case folder with the steps of a run that stopped before its result (a session that ended) is run normally."""
    d = deps(tmp_path, madeup_out)
    c = open_upload_case(d.settings.cases_root, [madeup_out / "MU01_conf.pdf"], "250101", "confirmation", NOW)
    c.write_step(1, "po_versions", {"po_number": "250101", "versions": []})
    assert run_case(c, d).status == "closed" and len(c.read_step("po_versions")["versions"]) == 1



class Forbidden:
    """A writer and a mailer that fail the test when they are used: a run cut off after its writes must write and send nothing."""
    name = "forbidden"

    def apply(self, *args):
        raise AssertionError("a write was applied again")

    def send(self, msg):
        raise AssertionError("a message was sent again")


def cut_off_after_a_write(d, madeup_out):
    """MU01 as a run cut off after its first write leaves it: the steps up to 07 begun, one entry in the write log, no result."""
    c, r = run(d, madeup_out, "MU01", "250101")
    (c.root / "steps" / "08_result.json").unlink(); (c.root / "audit.json").unlink()
    (c.root / "write_log.jsonl").write_text(r.write_entries[0].model_dump_json() + "\n", encoding="utf-8")
    return c, r.write_entries[0]


def test_a_run_cut_off_after_a_write_is_finished_in_review_and_not_run_again(tmp_path, madeup_out):
    """Running such a case again would apply its writes again and send its messages again. run_case finishes it as review with one
    item, the entries of the write log in the result and the audit, and no new write or message."""
    d = deps(tmp_path, madeup_out)
    c, logged = cut_off_after_a_write(d, madeup_out)
    r = run_case(c, d.model_copy(update={"writer": Forbidden(), "mailer": Forbidden()}))
    assert r.status == "review" and r.write_entries == [logged] and r.messages == []
    assert [(item.item_id, item.reason, item.finding_id) for item in r.review_items] == [("RI1", "run cut off after writes: check the write log", None)]
    assert WriteLog(c.root).entries() == [logged] and c.read_step("result") == r.model_dump(mode="json")
    assert audit(c)["writes"] == [logged.model_dump(mode="json")] and r.digest_line.startswith("PO 250101") and "ter beoordeling" in r.digest_line
    with pytest.raises(ValueError, match="^case already run"):
        run_case(c, d)


def test_a_run_cut_off_after_a_message_is_not_run_again(tmp_path, madeup_out):
    """A case whose messages/sent.jsonl exists has sent a message: it is finished in review, and nothing is sent again."""
    d = deps(tmp_path, madeup_out)
    c = open_upload_case(d.settings.cases_root, [madeup_out / "MU17_conf.pdf"], "250117", "confirmation", NOW)
    record_sent(c, SentRecord(message_id="m1", to=["sales@s17.example.com"], subject="s", sent_at=NOW.isoformat(), template="line_missing_extra"))
    r = run_case(c, d.model_copy(update={"writer": Forbidden(), "mailer": Forbidden()}))
    assert r.status == "review" and r.write_entries == [] and [item.reason for item in r.review_items] == ["run cut off after writes: check the write log"]
    assert (c.root / "audit.json").exists()


# --- the answers of the suppliers ---

def a_reply(path, subject: str, sender: str, in_reply_to: str = "", references: str = ""):
    """A made-up e-mail to the aid's mailbox that names the messages it answers in In-Reply-To and References."""
    headers = "".join(f"{name}: {value}\n" for name, value in (("In-Reply-To", in_reply_to), ("References", references)) if value)
    path.write_bytes(f"From: {sender}\nTo: aid@example.com\nSubject: {subject}\n{headers}\nThe quantity stands.\n".encode())
    return path


def asked(d, madeup_out, tmp_path):
    """MU17 run with the file mailer: its question is sent and the case waits. Returns the case and the question's Message-ID."""
    c, r = run(mailing(d, tmp_path / "outbox"), madeup_out, "MU17", "250117")
    assert r.status == "waiting"
    return c, f"<{r.messages[0]['message_id']}@example.com>"


def test_a_reply_to_a_question_is_filed_and_puts_the_case_in_review(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    c, message_id = asked(d, madeup_out, tmp_path)
    reply = a_reply(tmp_path / "reply.eml", "Re: Vraag over orderbevestiging PO 250117", "sales@s17.example.com",
                    in_reply_to=message_id)
    assert file_answer(d.settings.cases_root, reply, NOW + timedelta(hours=2)) == c.case_id
    assert (c.root / "messages" / "replies" / "reply.eml").exists()
    result = c.read_step("result")
    assert result["status"] == "review" and [(i["item_id"], i["reason"]) for i in result["review_items"]] == [("RI1", "supplier answered: read the reply")]
    assert "ter beoordeling" in result["digest_line"]
    assert escalate_pending(d.settings.cases_root, NOW + timedelta(days=30), d.settings) == []


def test_a_reply_found_by_its_references_is_filed_too(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    c, message_id = asked(d, madeup_out, tmp_path)
    reply = a_reply(tmp_path / "reply.eml", "Antw: vraag", "sales@s17.example.com", references=f"<other@example.org> {message_id}")
    assert file_answer(d.settings.cases_root, reply, NOW) == c.case_id and c.read_step("result")["status"] == "review"


def test_a_bounce_of_a_question_is_filed_under_bounces_and_leaves_the_result(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    c, message_id = asked(d, madeup_out, tmp_path)
    before = c.read_step("result")
    bounce = a_reply(tmp_path / "bounce.eml", "Undeliverable: Vraag over orderbevestiging PO 250117", "postmaster@example.com",
                     in_reply_to=message_id)
    assert file_answer(d.settings.cases_root, bounce, NOW) == c.case_id
    assert (c.root / "messages" / "bounces" / "bounce.eml").exists() and c.read_step("result") == before
    assert escalate_pending(d.settings.cases_root, NOW, d.settings) == [(c.case_id, "message bounced")]


def test_an_e_mail_that_answers_no_message_of_the_aid_is_not_filed(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    c, _ = asked(d, madeup_out, tmp_path)
    other = a_reply(tmp_path / "other.eml", "Re: something else", "sales@s17.example.com", in_reply_to="<0123abcd@example.com>")
    plain = a_reply(tmp_path / "plain.eml", "Orderbevestiging", "sales@s17.example.com")
    assert file_answer(d.settings.cases_root, other, NOW) is None and file_answer(d.settings.cases_root, plain, NOW) is None
    assert not (c.root / "messages" / "replies").exists() and c.read_step("result")["status"] == "waiting"



def test_a_reply_to_a_re_approval_request_is_not_a_supplier_answer(tmp_path, madeup_out):
    """The approver's answer to a re-approval request names a message the aid sent, but no question: it is not filed, and the
    question's wait goes on."""
    d = deps(tmp_path, madeup_out)
    c, _ = asked(d, madeup_out, tmp_path)
    record_sent(c, SentRecord(message_id="feed" * 8, to=["approver@example.com"], subject="s", sent_at=NOW.isoformat(), template="reapproval"))
    reply = a_reply(tmp_path / "ok.eml", "Re: PO 250117 opnieuw ter fiattering", "approver@example.com", in_reply_to=f"<{'feed' * 8}@example.com>")
    assert file_answer(d.settings.cases_root, reply, NOW) is None
    assert not (c.root / "messages" / "replies").exists() and c.read_step("result")["status"] == "waiting"


def test_a_torn_line_in_sent_jsonl_does_not_hide_the_records_after_it(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    c, message_id = asked(d, madeup_out, tmp_path)
    sent = c.root / "messages" / "sent.jsonl"
    sent.write_bytes(b'{"message_id": "torn' + b"\n" + sent.read_bytes())
    reply = a_reply(tmp_path / "reply.eml", "Re: vraag", "sales@s17.example.com", in_reply_to=message_id)
    assert file_answer(d.settings.cases_root, reply, NOW) == c.case_id


def test_a_message_id_without_angle_brackets_matches_nothing(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    c, message_id = asked(d, madeup_out, tmp_path)
    reply = a_reply(tmp_path / "reply.eml", "Re: vraag", "sales@s17.example.com", in_reply_to=message_id.strip("<>"))
    assert file_answer(d.settings.cases_root, reply, NOW) is None and not (c.root / "messages" / "replies").exists()


def test_a_reply_after_an_escalation_and_a_second_reply_each_add_an_item(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    c, message_id = asked(d, madeup_out, tmp_path)
    assert escalate_pending(d.settings.cases_root, NOW + timedelta(days=30), d.settings) == [(c.case_id, "no answer in 3 working days")]
    for name in ("reply.eml", "again.eml"):
        reply = a_reply(tmp_path / name, "Re: vraag", "sales@s17.example.com", in_reply_to=message_id)
        assert file_answer(d.settings.cases_root, reply, NOW + timedelta(days=31)) == c.case_id
    result = c.read_step("result")
    assert result["status"] == "review" and [(i["item_id"], i["reason"]) for i in result["review_items"]] == [
        ("RI1", "no answer in 3 working days"), ("RI2", "supplier answered: read the reply"), ("RI3", "supplier answered: read the reply")]
    assert sorted(p.name for p in (c.root / "messages" / "replies").iterdir()) == ["again.eml", "reply.eml"]
    assert [a["file"] for a in audit(c)["answers"]] == ["reply.eml", "again.eml"]


def test_a_reply_to_a_case_without_a_result_is_filed_without_an_item(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    c = open_upload_case(d.settings.cases_root, [madeup_out / "MU17_conf.pdf"], "250117", "confirmation", NOW)
    record_sent(c, SentRecord(message_id="ab" * 16, to=["sales@s17.example.com"], subject="s", sent_at=NOW.isoformat(), template="line_missing_extra"))
    reply = a_reply(tmp_path / "reply.eml", "Re: vraag", "sales@s17.example.com", in_reply_to=f"<{'ab' * 16}@example.com>")
    assert file_answer(d.settings.cases_root, reply, NOW) == c.case_id
    assert (c.root / "messages" / "replies" / "reply.eml").exists() and not (c.root / "steps" / "08_result.json").exists()
    assert [item.reason for item in run_case(c, d).review_items] == ["run cut off after writes: check the write log"]


def test_a_damaged_result_leaves_no_copy_of_the_reply(tmp_path, madeup_out):
    """The result is read and checked before the e-mail is copied: an e-mail that stays in the inbox as an error and is tried again
    leaves no copies behind, and once the result is readable it is filed once."""
    d = deps(tmp_path, madeup_out)
    c, message_id = asked(d, madeup_out, tmp_path)
    path = c.root / "steps" / "08_result.json"; good = path.read_bytes(); path.write_text("{", encoding="utf-8")
    reply = a_reply(tmp_path / "reply.eml", "Re: vraag", "sales@s17.example.com", in_reply_to=message_id)
    for _ in range(2):
        with pytest.raises(ValueError):
            file_answer(d.settings.cases_root, reply, NOW)
    assert not (c.root / "messages" / "replies").exists()
    path.write_bytes(good)
    assert file_answer(d.settings.cases_root, reply, NOW) == c.case_id
    assert [p.name for p in (c.root / "messages" / "replies").iterdir()] == ["reply.eml"]


def test_the_digest_line_of_an_answered_case_is_in_the_language_given(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    c, message_id = asked(d, madeup_out, tmp_path)
    reply = a_reply(tmp_path / "reply.eml", "Re: question", "sales@s17.example.com", in_reply_to=message_id)
    file_answer(d.settings.cases_root, reply, NOW, language="en")
    assert c.read_step("result")["digest_line"] == "PO 250117 (S17): for review, no writes"


# --- the messages ---

SUPPLIER = "sales@s17.example.com"                   # made-up addresses of the made-up suppliers
ADDRESSES = {"S17": SUPPLIER, "S11": "sales@s11.example.com", "S12": "sales@s12.example.com", "S20": "sales@s20.example.com"}


def mailing(d, out, **changes):
    """The dependencies d with the file mailer into out, at the clock of deps(), and the made-up suppliers' addresses unless the
    changes to the settings say otherwise."""
    settings = {"supplier_emails": ADDRESSES} | changes
    return with_settings(d, **settings).model_copy(update={"mailer": FileMailer(out, sender="aid@example.com", clock=lambda: NOW)})


def read_eml(path):
    return message_from_bytes(path.read_bytes(), policy=policy.default)


def an_email(path, subject: str, sender: str):
    """A made-up e-mail to the aid's mailbox, saved at path."""
    path.write_bytes(f"From: {sender}\nTo: aid@example.com\nSubject: {subject}\n\nA made-up message.\n".encode())
    return path


def above_authorization(d):
    """d with an authorization limit of half the total of MU08's PO (as test_po_store.py derives its limits), and the limit."""
    limit = d.store.versions("250108")[0].total / 2
    return d.model_copy(update={"settings": d.settings.model_copy(update={"authorization_limit": limit}), "store": PoStore(d.store.root, limit)}), limit


def mailer_of_the_settings(d, environ: dict | None = None):
    """d with the mailer that the runner builds of d's settings (mailer_of, as build_deps calls it), at the clock of deps()."""
    from aid.messaging.microsoft_mailer import mailer_of
    return d.model_copy(update={"mailer": mailer_of(d.settings, environ or {}, lambda: None, clock=lambda: NOW)})


@pytest.mark.parametrize("values, cause, words", [
    ({}, "no_mailbox", "no mailbox connected"),
    ({"smtp_host": "smtp.example.com", "smtp_user": "aid@example.com"}, "no_password", "mail server password not set"),
    ({"send_via": "smtp"}, "no_mailbox", "no mailbox connected"),
], ids=["nothing connected", "no password", "smtp without a server"])
def test_a_question_of_the_run_that_nothing_can_send_is_not_asked(tmp_path, madeup_out, values, cause, words):
    """Ruling C12. The settings as a new installation has them (send_via "auto", nothing connected), a mail server without its
    password, or "smtp" without a server: the runner's mailer is the file mailer given for want of a route. The run writes no
    file and counts nothing as asked: its question is recorded "not sent", with the cause, and the case gets the review item
    "message not sent", as for a message a mailer could not send, and goes to the buyer, who connects the mailbox and chooses
    "Leverancier vragen" on that item. The digest lists the case under "Wacht op u" with the label, not under "Vraag gesteld"."""
    from datetime import date

    from aid.notify import build_digest
    d = mailer_of_the_settings(with_settings(deps(tmp_path, madeup_out), supplier_emails=ADDRESSES, **values))
    c, r = run(d, madeup_out, "MU17", "250117")
    assert r.status == "review" and [(m["status"], m["cause"]) for m in r.messages] == [(f"not sent ({words})", cause)]
    assert [(i.finding_id, i.reason) for i in r.review_items] == [("F1", "message not sent")]
    assert not (d.settings.cases_root / "outbox").exists() and not list(tmp_path.rglob("*.eml")) and not (c.root / "messages" / "sent.jsonl").exists()
    assert audit(c)["messages"][0]["cause"] == cause and "error" not in audit(c)["messages"][0]
    digest = [line.strip() for line in build_digest([r], [], d.settings, date(2026, 11, 12)).text.splitlines()]
    assert digest[digest.index("Wacht op u") + 2] == "- Bericht niet verzonden (ontbrekende of extra regel, regel 3)"
    assert not any(line.startswith("Vraag gesteld") for line in digest)


def test_a_question_of_the_run_is_written_as_a_file_by_choice_and_counts(tmp_path, madeup_out):
    """Ruling C12: with send_via "file" the run's question is written as a file into cases/outbox and counts as asked, as before:
    the case waits on it."""
    d = mailer_of_the_settings(with_settings(deps(tmp_path, madeup_out), supplier_emails=ADDRESSES, send_via="file"))
    c, r = run(d, madeup_out, "MU17", "250117")
    [eml] = (d.settings.cases_root / "outbox").glob("*.eml")
    assert r.status == "waiting" and [m["status"] for m in r.messages] == ["sent"] and r.review_items == []
    assert [json.loads(line)["message_id"] for line in (c.root / "messages" / "sent.jsonl").read_text(encoding="utf-8").splitlines()] == [eml.stem]


def test_with_send_via_auto_the_run_judges_the_route_once_per_message(tmp_path, madeup_out):
    """Ruling C8, the run's side. send_via "auto": the run looks once at who is signed in, and the mailer that look gives sends
    the message. When the store of the sign-in cannot be read at a second look, nothing goes through the file mailer that such a
    look would give for want of a route: the question goes out through the mailbox, and no file is written."""
    from types import SimpleNamespace

    from aid.messaging.microsoft_mailer import AutoMailer
    looks, taken = [], []

    def account():
        looks.append(1)
        if len(looks) > 1:
            raise OSError("made-up: the store of the sign-in is held by another process")
        return object()

    def send(msg):
        taken.append(msg)
        return SentRecord(message_id="1" * 32, to=msg.to, subject=msg.subject, sent_at=NOW.isoformat(), template=msg.template)
    mailbox = SimpleNamespace(account=SimpleNamespace(account=account), send=send)
    other = FileMailer(tmp_path / "cases" / "outbox", sender="aid@example.com", clock=lambda: NOW, unrouted="no_mailbox")
    d = with_settings(deps(tmp_path, madeup_out), supplier_emails=ADDRESSES).model_copy(update={"mailer": AutoMailer(mailbox, other)})
    c, r = run(d, madeup_out, "MU17", "250117")
    assert r.status == "waiting" and [m["status"] for m in r.messages] == ["sent"] and len(taken) == 1 and len(looks) == 1
    assert not list(tmp_path.rglob("*.eml"))


class CopyRefused:
    """A mailer that sends each message and reports the buyer's copy refused, as a server that refuses the Cc address does."""
    name = "copy-refused"

    def send(self, msg):
        return SentRecord(message_id="0" * 32, to=msg.to, subject=msg.subject, sent_at=NOW.isoformat(), template=msg.template, refused_cc=1)


class Unreachable:
    """A mailer whose every send fails with the error given, as a mailbox that cannot be reached; it counts its tries."""
    name = "unreachable"

    def __init__(self, error: Exception):
        self.error, self.tries = error, 0

    def send(self, msg):
        self.tries += 1
        raise self.error


def test_a_question_is_sent_to_the_supplier_with_the_buyer_in_copy(tmp_path, madeup_out):
    """MU17 with the file mailer: the question about the missing line goes to the address the settings give S17, the buyer in copy,
    as one e-mail whose table shows the item code and the description of the PO line. The message is recorded as sent, with its
    id, addressee, subject and time, in the result, the audit and messages/sent.jsonl, and the case waits for the supplier."""
    out = tmp_path / "outbox"
    c, r = run(mailing(deps(tmp_path, madeup_out), out), madeup_out, "MU17", "250117")
    [eml] = out.glob("*.eml")
    mail, subject = read_eml(eml), "Vraag over orderbevestiging PO 250117"
    assert (mail["From"], mail["To"], mail["Cc"], mail["Subject"]) == ("aid@example.com", SUPPLIER, SETTINGS.buyer_email, subject)
    assert (mail["X-Aid-Template"], mail["X-Aid-Case"], mail["Message-ID"]) == ("line_missing_extra", c.case_id, f"<{eml.stem}@example.com>")
    assert "KM-1703" in mail.get_content() and "Afdichtingsset pomp" in mail.get_content()        # the description is the PO line's
    sent = {"message_id": eml.stem, "to": [SUPPLIER], "subject": subject, "sent_at": "2026-11-12T09:00:00", "refused_cc": 0}
    assert r.messages == [{"rule_id": "R07", "template": "line_missing_extra",
                           "items": [{"po_line_ids": "3", "doc_line_ids": "", "field": "line_presence", "po_value": "KM-1703", "doc_value": ""}],
                           "finding_ids": ["F1"], "status": "sent", "timestamp": NOW.isoformat()} | sent]
    assert audit(c)["messages"] == r.messages and c.read_step("execute")["messages"] == r.messages
    lines = (c.root / "messages" / "sent.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [sent | {"template": "line_missing_extra"}]
    assert r.status == "waiting" and r.review_items == [] and r.digest_line == "PO 250117 (S17): wacht op de leverancier, geen schrijfacties"
    assert [row["decision"] for row in read_csv(record_dispositions(c, r))] == ["ask the supplier (R07)"]


def test_a_refused_copy_is_recorded_and_the_question_waits(tmp_path, madeup_out):
    """The server refused the buyer's copy and accepted the supplier's address: the question went, the record and the audit count the
    refused copy, and no review item is added."""
    c, r = run(mailing(deps(tmp_path, madeup_out), tmp_path / "outbox").model_copy(update={"mailer": CopyRefused()}), madeup_out, "MU17", "250117")
    assert [(m["status"], m["refused_cc"]) for m in r.messages] == [("sent", 1)] and audit(c)["messages"] == r.messages
    assert r.status == "waiting" and r.review_items == []


def test_a_question_over_two_po_lines_shows_both_lines(tmp_path, madeup_out, monkeypatch):
    """An item of the routing over PO lines "1, 2" (routed here by hand on MU11): its row shows both lines, with the item code and
    the description of the first, and the record keeps the routing's item."""
    item = {"po_line_ids": "1, 2", "doc_line_ids": "1", "field": "quantity", "po_value": "35", "doc_value": "37"}
    asked = Action(route="ASK", rule_id="R06", finding_ids=["F1"], message=MessageSpec(template="quantity_unit", items=[item]),
                   reason="ask the supplier about the quantity or unit")
    monkeypatch.setattr("aid.pipeline.route", lambda findings, ctx, settings: [asked])
    out = tmp_path / "outbox"
    c, r = run(mailing(deps(tmp_path, madeup_out), out), madeup_out, "MU11", "250111")
    [eml] = out.glob("*.eml")
    rows = [[cell.strip() for cell in line.split("|")] for line in read_eml(eml).get_content().splitlines() if "|" in line]
    assert rows[1:] == [["1, 2", "KM-1101", "Slangklem 20-32 mm", "35", "37"]] and r.messages[0]["items"] == [item]


def test_the_reapproval_is_sent_to_the_approver_and_names_no_amount(tmp_path, madeup_out):
    """MU08 above a limit of half its PO's total: after the price correction (R10), the request to approve the PO again goes to the
    approver, the buyer in copy, and lists both changes. A request sent leaves the case as the rules route it, and it is no question
    that waits. Neither the limit nor the PO's total is in any e-mail the run wrote."""
    out = tmp_path / "outbox"
    limited, limit = above_authorization(mailing(deps(tmp_path, madeup_out), out))
    c, r = run(limited, madeup_out, "MU08", "250108")
    [eml] = out.glob("*.eml")
    mail = read_eml(eml)
    assert (mail["To"], mail["Cc"], mail["Subject"]) == (SETTINGS.approver_email, SETTINGS.buyer_email, "Inkooporder PO 250108 opnieuw ter fiattering")
    assert (mail["X-Aid-Template"], mail["X-Aid-Case"]) == ("reapproval", c.case_id)
    assert "- Prijs van de orderregel, regel 1: oude waarde 12.50, nieuwe waarde 12.70" in mail.get_content()
    assert "- Artikelprijs, artikel KM-0801: oude waarde onbekend, nieuwe waarde 12.70" in mail.get_content()
    assert [(m["rule_id"], m["template"], m["status"], m["to"], m["message_id"]) for m in r.messages] == [
        ("R10", "reapproval", "sent", [SETTINGS.approver_email], eml.stem)]
    assert r.status == "closed" and r.review_items == [] and [e.op.kind for e in r.write_entries] == [
        "po_line_price", "article_price", "attach_document", "set_status"]
    assert [json.loads(line)["template"] for line in (c.root / "messages" / "sent.jsonl").read_text(encoding="utf-8").splitlines()] == ["reapproval"]
    assert pending_asks(limited.settings.cases_root, NOW + timedelta(days=30), 3) == []
    total = limited.store.versions("250108")[0].total
    for path in out.glob("*.eml"):
        written = read_eml(path)
        text = written.get_content() + "".join(f"{name}: {value}" for name, value in written.items())
        assert not [amount for amount in (limit, total) if str(amount) in text or str(amount).encode() in path.read_bytes()]


def test_a_question_to_a_supplier_without_an_address_goes_to_the_buyer(tmp_path, madeup_out):
    """MU17 with a mailer but no address for S17: nothing is sent, the message is recorded as not sent for that reason, and its
    finding is a review item, so that the buyer asks the supplier."""
    out = tmp_path / "outbox"
    c, r = run(mailing(deps(tmp_path, madeup_out), out, supplier_emails={}), madeup_out, "MU17", "250117")
    assert not out.exists() and not (c.root / "messages" / "sent.jsonl").exists()
    assert [(m["template"], m["status"]) for m in r.messages] == [("line_missing_extra", "not sent (no address for the supplier)")]
    assert audit(c)["messages"] == r.messages
    assert [(i.item_id, i.finding_id, i.field, i.reason) for i in r.review_items] == [("RI1", "F1", "line_presence", "no address for the supplier")]
    assert r.status == "review" and r.error is None


def test_a_message_the_mailer_cannot_send_goes_to_the_buyer_and_the_run_goes_on(tmp_path, madeup_out):
    """A mailer that fails: the message is recorded as not sent with the class of the error, the audit keeps the error's text, each
    finding of the message is a review item, and the run goes on: for MU08 above the limit, R01 still attaches and confirms."""
    failing = Unreachable(MailerError("ConnectionRefusedError: could not connect to smtp.example.com:587"))
    d = mailing(deps(tmp_path, madeup_out), tmp_path / "outbox")
    c, r = run(d.model_copy(update={"mailer": failing}), madeup_out, "MU17", "250117")
    assert failing.tries == 1 and r.status == "review" and r.error is None
    assert [(m["template"], m["status"]) for m in r.messages] == [("line_missing_extra", "not sent (MailerError)")]
    assert audit(c)["messages"] == [r.messages[0] | {"error": "MailerError: ConnectionRefusedError: could not connect to smtp.example.com:587"}]
    assert [(i.item_id, i.finding_id, i.reason) for i in r.review_items] == [("RI1", "F1", "message not sent")]
    assert not (c.root / "messages" / "sent.jsonl").exists()
    limited, _ = above_authorization(d.model_copy(update={"mailer": Unreachable(OSError("no space left on the device"))}))
    c, r = run(limited, madeup_out, "MU08", "250108")
    assert [(m["template"], m["status"]) for m in r.messages] == [("reapproval", "not sent (OSError)")]
    assert audit(c)["messages"][0]["error"] == "OSError: no space left on the device"
    assert [e.op.kind for e in r.write_entries] == ["po_line_price", "article_price", "attach_document", "set_status"]
    assert [(i.finding_id, i.field, i.reason) for i in r.review_items] == [("F1", "price", "message not sent")] and r.status == "review"


def test_a_question_without_an_answer_goes_to_review_once_its_wait_is_over(tmp_path, madeup_out):
    """MU17's question, sent on Thursday 12 November at 9:00, waits three working days, the weekend skipped: until Tuesday 17 November
    at 9:00. After that, escalate_pending adds a review item to the case's saved result, which is then in review, adds the escalation
    to the case's audit record and to messages/escalated.jsonl, with the question's message id, and returns it; a second call adds
    nothing."""
    d = mailing(deps(tmp_path, madeup_out), tmp_path / "outbox")
    c, r = run(d, madeup_out, "MU17", "250117")
    recorded = audit(c)
    deadline = datetime(2026, 11, 17, 9, 0)
    assert escalate_pending(d.settings.cases_root, deadline, d.settings) == [] and c.read_step("result") == r.model_dump(mode="json")
    later = deadline + timedelta(minutes=1)
    assert escalate_pending(d.settings.cases_root, later, d.settings) == [(c.case_id, "no answer in 3 working days")]
    result = CaseResult.model_validate(c.read_step("result"))
    assert result.review_items == [ReviewItem(item_id="RI1", finding_id=None, reason="no answer in 3 working days", field=None, po_value=None,
                                              doc_value=None, question=r.messages[0]["message_id"])]      # the item names its question
    assert result.status == "review" and result.digest_line == "PO 250117 (S17): ter beoordeling, geen schrijfacties"
    assert result.model_copy(update={"status": r.status, "review_items": r.review_items, "digest_line": r.digest_line}) == r
    question = r.messages[0]["message_id"]                    # the line names the question it covers (ruling C3) and the decisions' lines
    escalation = {"case_id": c.case_id, "message_id": question, "reason": "no answer in 3 working days", "at": later.isoformat(),
                  "covers": [question], "decisions": 0}
    escalated = (c.root / "messages" / "escalated.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in escalated] == [escalation] and audit(c) == recorded | {"escalations": [escalation]}
    assert escalate_pending(d.settings.cases_root, later + timedelta(days=7), d.settings) == []
    assert c.read_step("result") == result.model_dump(mode="json") and audit(c)["escalations"] == [escalation]


def test_a_wait_of_one_working_day_is_named_in_the_singular(tmp_path, madeup_out):
    d = mailing(deps(tmp_path, madeup_out), tmp_path / "outbox", ask_wait_working_days=1)
    c, _ = run(d, madeup_out, "MU17", "250117")
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 13, 9, 1), d.settings) == [(c.case_id, "no answer in 1 working day")]


def test_a_bounce_goes_to_review_at_once_and_a_reply_ends_the_wait(tmp_path, madeup_out):
    """With a wait of five working days: MU17's question bounces (a Dutch notice) and goes to review at once; MU11's supplier answers,
    so its case is never escalated; MU20's supplier sends only an out-of-office notice, which ends no wait, and its case goes to review
    when the five days are over."""
    d = mailing(deps(tmp_path, madeup_out), tmp_path / "outbox", ask_wait_working_days=5)
    bounced, _ = run(d, madeup_out, "MU17", "250117")
    answered, _ = run(d, madeup_out, "MU11", "250111")
    silent, _ = run(d, madeup_out, "MU20", "250120")
    from aid.tests.test_messaging import at_time     # an e-mail that names no message answers the latest question sent before it was filed

    file_reply(bounced, at_time(an_email(tmp_path / "bounce.eml", "Onbestelbaar: Vraag over orderbevestiging PO 250117", "postmaster@example.com"),
                                NOW + timedelta(minutes=2)))
    file_reply(answered, at_time(an_email(tmp_path / "reply.eml", "Re: Vraag over orderbevestiging PO 250111", "sales@s11.example.com"),
                                 NOW + timedelta(minutes=2)))
    assert file_reply(silent, an_email(tmp_path / "ooo.eml", "Automatisch antwoord: Vraag over orderbevestiging PO 250120", "sales@s20.example.com")
                      ) == silent.root / "messages" / "auto" / "ooo.eml"
    assert escalate_pending(d.settings.cases_root, NOW + timedelta(minutes=5), d.settings) == [(bounced.case_id, "message bounced")]
    assert [(i["item_id"], i["reason"]) for i in bounced.read_step("result")["review_items"]] == [("RI1", "message bounced")]
    assert escalate_pending(d.settings.cases_root, NOW + timedelta(days=30), d.settings) == [(silent.case_id, "no answer in 5 working days")]
    assert answered.read_step("result")["status"] == "waiting" and silent.read_step("result")["status"] == "review"


def test_only_a_waiting_case_is_escalated(tmp_path, madeup_out):
    """When the waits of their questions end, a case in review (MU12: its question and two review items), a case the buyer closed
    and a failed case are left alone, and a case whose run was cut off before its result is left until it has one; the waiting case
    beside them is escalated."""
    d = mailing(deps(tmp_path, madeup_out), tmp_path / "outbox")
    in_review, _ = run(d, madeup_out, "MU12", "250112")
    closed, r = run(d, madeup_out, "MU11", "250111")
    closed.write_step(8, "result", r.model_copy(update={"status": "closed"}))
    failed, r = run(d, madeup_out, "MU20", "250120")
    failed.write_step(8, "result", r.model_copy(update={"status": "failed", "error": "WriterError: the ERP refused the change"}))
    cut = open_upload_case(d.settings.cases_root, [madeup_out / "MU18_conf.pdf"], "250118", "confirmation", NOW)
    record_sent(cut, SentRecord(message_id="m1", to=["sales@s18.example.com"], subject="s", sent_at=NOW.isoformat(), template="line_missing_extra"))
    waiting, _ = run(d, madeup_out, "MU17", "250117")
    left = (in_review, closed, failed)
    before = {case.case_id: case.read_step("result") for case in left}
    later = NOW + timedelta(days=30)
    assert pending_asks(d.settings.cases_root, later, 3) == sorted(case.case_id for case in (*left, cut, waiting))
    assert in_review.read_step("result")["status"] == "review"
    assert escalate_pending(d.settings.cases_root, later, d.settings) == [(waiting.case_id, "no answer in 3 working days")]
    assert {case.case_id: case.read_step("result") for case in left} == before
    assert not [case for case in (*left, cut) if (case.root / "messages" / "escalated.jsonl").exists()]


def test_a_question_sent_after_an_escalation_waits_on_its_own(tmp_path, madeup_out):
    """MU17's question is escalated when its wait ends. A rerun on Monday 30 November sends a new question, which waits its own three
    working days, until Thursday 3 December at 9:00, and is then escalated on its own."""
    d = mailing(deps(tmp_path, madeup_out), tmp_path / "outbox")
    c, first = run(d, madeup_out, "MU17", "250117")
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 17, 9, 1), d.settings) == [(c.case_id, "no answer in 3 working days")]
    monday = datetime(2026, 11, 30, 9, 0)
    again = d.model_copy(update={"clock": lambda: monday, "mailer": FileMailer(tmp_path / "outbox", sender="aid@example.com", clock=lambda: monday)})
    second = run_case(c, again, rerun=True)
    assert second.status == "waiting" and second.messages[0]["message_id"] != first.messages[0]["message_id"]
    assert escalate_pending(d.settings.cases_root, datetime(2026, 12, 3, 9, 0), d.settings) == []
    assert escalate_pending(d.settings.cases_root, datetime(2026, 12, 3, 9, 1), d.settings) == [(c.case_id, "no answer in 3 working days")]
    lines = (c.root / "messages" / "escalated.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["message_id"] for line in lines] == [first.messages[0]["message_id"], second.messages[0]["message_id"]]


def test_a_case_whose_files_cannot_be_read_is_named_and_the_others_go_on(tmp_path, madeup_out):
    """A torn last line in one case's sent.jsonl and a damaged saved result in another: each is returned as unreadable, with the class
    of its error, and the waiting case after them is escalated."""
    d = mailing(deps(tmp_path, madeup_out), tmp_path / "outbox")
    torn, _ = run(d, madeup_out, "MU11", "250111")
    sent = torn.root / "messages" / "sent.jsonl"
    sent.write_bytes(sent.read_bytes() + b'{"message_id": "m2", "to"')
    damaged, _ = run(d, madeup_out, "MU17", "250117")
    (damaged.root / "steps" / "08_result.json").write_text("{not json", encoding="utf-8")
    waiting, _ = run(d, madeup_out, "MU20", "250120")
    assert escalate_pending(d.settings.cases_root, NOW + timedelta(days=30), d.settings) == [
        (torn.case_id, "unreadable: ValidationError"), (damaged.case_id, "unreadable: ValidationError"),
        (waiting.case_id, "no answer in 3 working days")]


def test_the_batch_escalates_the_questions_whose_wait_is_over(tmp_path, madeup_out, monkeypatch, capsys):
    """After its own cases the batch calls escalate_pending with its clock, on the cases under cases_root, and prints a line for each
    case it sent to review and for each case whose files it cannot read."""
    d = mailing(deps(tmp_path, madeup_out), tmp_path / "outbox")
    torn, _ = run(d, madeup_out, "MU11", "250111")
    sent = torn.root / "messages" / "sent.jsonl"
    sent.write_bytes(sent.read_bytes() + b'{"message_id": "m2", "to"')
    c, _ = run(d, madeup_out, "MU17", "250117")
    monkeypatch.setattr("aid.batch._now", lambda: datetime(2026, 11, 18, 9, 0))
    cases, settings = tmp_path / "cases.csv", tmp_path / "settings.json"
    cases.write_text(f"case_id,document,po_number,kind\nMU01,{madeup_out / 'MU01_conf.pdf'},250101,confirmation\n", encoding="utf-8")
    settings.write_text(d.settings.model_copy(update={"po_store_root": tmp_path / "store"}).model_dump_json(), encoding="utf-8")
    assert batch_main(["--cases", str(cases), "--po-dir", str(madeup_out), "--settings", str(settings), "--reader", "parser",
                       "--out", str(tmp_path / "summary.csv")]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[0].startswith("MU01 (250101_20261118T090000): ") and printed[3].startswith("1 cases, 1 closed")
    assert printed[1:3] == [f"{torn.case_id}: unreadable: ValidationError", f"{c.case_id}: escalated to review: no answer in 3 working days"]
    assert c.read_step("result")["status"] == "review"


# --- a reply belongs to its question (Task 44) ---

def test_a_question_asked_after_a_reply_to_another_comes_back_when_its_own_wait_ends(tmp_path, madeup_out):
    """MU12: the run asks S12 about the unit (R06) on Thursday 12 November at 9:00 and leaves two items to the buyer. The supplier
    answers that question, and the audit keeps which question the reply names. On Friday at 10:00 the buyer asks about the price on
    the review page and keeps the rest: the case waits for the second question, which has no reply of its own, and the reply to the
    first does not answer it. Three working days later, on Wednesday 18 November at 10:00, its wait ends: the case comes back with
    "no answer", and the escalation names the second question."""
    from aid.review.app import close_case, decide

    out = tmp_path / "outbox"
    d = mailing(deps(tmp_path, madeup_out), out)
    c, r = run(d, madeup_out, "MU12", "250112")
    [first] = [m["message_id"] for m in r.messages]
    assert r.status == "review" and [i.item_id for i in r.review_items] == ["RI1", "RI2"]
    reply = a_reply(tmp_path / "reply.eml", "Re: Vraag over orderbevestiging PO 250112", "sales@s12.example.com", in_reply_to=f"<{first}@example.com>")
    assert file_answer(d.settings.cases_root, reply, NOW + timedelta(hours=2)) == c.case_id
    assert audit(c)["answers"] == [{"file": "reply.eml", "filed_under": "replies", "at": (NOW + timedelta(hours=2)).isoformat(), "message_id": first,
                                    "decisions": 0}]          # no decision made yet when it came (ruling C1)
    friday = datetime(2026, 11, 13, 10, 0)
    decide(c, "RI1", "ask"); decide(c, "RI2", "keep"); decide(c, "RI3", "keep")
    closed = close_case(c, d.model_copy(update={"clock": lambda: friday, "mailer": FileMailer(out, sender="aid@example.com", clock=lambda: friday)}))
    second = closed.messages[-1]["message_id"]
    assert closed.status == "waiting" and [(m["template"], m["status"]) for m in closed.messages] == [("quantity_unit", "sent"), ("missing_info", "sent")]
    assert pending_asks(d.settings.cases_root, datetime(2026, 11, 18, 10, 0), 3) == []
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 18, 10, 0), d.settings) == []
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 18, 10, 1), d.settings) == [(c.case_id, "no answer in 3 working days")]
    result = c.read_step("result")
    assert result["status"] == "review" and [i["reason"] for i in result["review_items"]][-2:] == [
        "supplier answered: read the reply", "no answer in 3 working days"]
    [escalated] = (c.root / "messages" / "escalated.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(escalated)["message_id"] == second and audit(c)["escalations"][0]["message_id"] == second
    assert escalate_pending(d.settings.cases_root, datetime(2026, 12, 14, 10, 0), d.settings) == []


def test_a_delivery_report_is_filed_as_the_bounce_of_the_question_it_returns(tmp_path, madeup_out):
    """A delivery report in the standard form names no message in its own headers: it is filed into the case whose question it
    returns, under bounces/, and that question comes back to the buyer as bounced at once. A report of a message the aid did not
    send is not filed."""
    from aid.tests.test_messaging import delivery_report

    d = deps(tmp_path, madeup_out)
    c, message_id = asked(d, madeup_out, tmp_path)
    before = c.read_step("result")
    assert file_answer(d.settings.cases_root, delivery_report(tmp_path / "other.eml", "<0123abcd@example.org>"), NOW) is None
    assert not (c.root / "messages" / "bounces").exists()
    assert file_answer(d.settings.cases_root, delivery_report(tmp_path / "dsn.eml", message_id), NOW + timedelta(minutes=5)) == c.case_id
    assert (c.root / "messages" / "bounces" / "dsn.eml").exists() and c.read_step("result") == before
    assert audit(c)["answers"][0]["filed_under"] == "bounces" and audit(c)["answers"][0]["message_id"] == message_id.strip("<>").partition("@")[0]
    assert escalate_pending(d.settings.cases_root, NOW + timedelta(minutes=6), d.settings) == [(c.case_id, "message bounced")]


# --- the review round of Task 44 (rulings S15, S17, S20, S21, S23, S24, S25) ---

def test_an_e_mail_that_names_questions_of_two_cases_belongs_to_the_case_named_first(tmp_path, madeup_out):
    """Ruling S15. Two cases of one order (a second confirmation while the first question is out), one question each. A reply whose
    In-Reply-To names the later case and whose References name both belongs to the later case, not to the one whose folder sorts
    first: that case gets the reply and its item, and the other keeps waiting. A delivery report with an In-Reply-To of one case
    that returns a message of the other belongs to the case its In-Reply-To names."""
    from aid.tests.test_messaging import delivery_report

    d = mailing(deps(tmp_path, madeup_out), tmp_path / "outbox")
    a, first = run(d, madeup_out, "MU17", "250117")
    b, second = run(d, madeup_out, "MU17", "250117", now=LATER)
    assert a.case_id < b.case_id and (first.status, second.status) == ("waiting", "waiting")
    of_a, of_b = (r.messages[0]["message_id"] for r in (first, second))
    reply = a_reply(tmp_path / "reply.eml", "Re: Vraag over orderbevestiging PO 250117", "sales@s17.example.com",
                    in_reply_to=f"<{of_b}@example.com>", references=f"<{of_a}@example.com> <{of_b}@example.com>")
    assert file_answer(d.settings.cases_root, reply, NOW + timedelta(hours=2)) == b.case_id
    assert b.read_step("result")["status"] == "review" and [a["message_id"] for a in audit(b)["answers"]] == [of_b]
    assert a.read_step("result") == first.model_dump(mode="json") and "answers" not in audit(a) and not (a.root / "messages" / "replies").exists()
    report = delivery_report(tmp_path / "dsn.eml", f"<{of_b}@example.com>")
    report.write_bytes(report.read_bytes().replace(b"Subject:", f"In-Reply-To: <{of_a}@example.com>\nSubject:".encode(), 1))
    assert file_answer(d.settings.cases_root, report, NOW + timedelta(hours=3)) == a.case_id
    assert (a.root / "messages" / "bounces" / "dsn.eml").exists() and [a["message_id"] for a in audit(a)["answers"]] == [of_a]
    assert not (b.root / "messages" / "bounces").exists()


def two_asked(tmp_path, madeup_out, own=()):
    """MU12 with three questions out of two closes' worth: the run asked about the unit on Thursday (answered here, and the reply
    kept), and on Friday at 10:00 the buyer asked about both of his items in one close. Returns the Deps, the case and the ids of
    the two questions of that close, in the order sent."""
    from aid.review.app import close_case, decide

    out = tmp_path / "outbox"
    d = mailing(deps(tmp_path, madeup_out), out)
    c, r = run(d, madeup_out, "MU12", "250112")
    reply = a_reply(tmp_path / "reply.eml", "Re: Vraag over orderbevestiging PO 250112", "sales@s12.example.com",
                    in_reply_to=f"<{r.messages[0]['message_id']}@example.com>")
    assert file_answer(d.settings.cases_root, reply, NOW + timedelta(hours=2)) == c.case_id
    friday = datetime(2026, 11, 13, 10, 0)
    decide(c, "RI1", "ask"); decide(c, "RI2", "ask"); decide(c, "RI3", "keep")
    closed = close_case(c, d.model_copy(update={"clock": lambda: friday, "mailer": FileMailer(out, sender="aid@example.com", clock=lambda: friday)}))
    assert closed.status == "waiting" and [m["status"] for m in closed.messages] == ["sent", "sent", "sent"]
    return d, c, [m["message_id"] for m in closed.messages[1:]]


def test_two_questions_of_one_close_that_bounce_are_each_shown_once(tmp_path, madeup_out):
    """Ruling S20. Two questions sent in one close to a wrong address both bounce: the next pass shows each bounce once, as an item
    of its own with its own line in the escalations, and no later pass shows either again."""
    d, c, (second, third) = two_asked(tmp_path, madeup_out)
    for number, question in enumerate((second, third)):
        bounce = a_reply(tmp_path / f"bounce{number}.eml", "Undeliverable: Vraag over orderbevestiging PO 250112", "postmaster@example.com",
                         in_reply_to=f"<{question}@example.com>")
        assert file_answer(d.settings.cases_root, bounce, datetime(2026, 11, 13, 10, 5)) == c.case_id
    at = datetime(2026, 11, 13, 10, 6)
    assert escalate_pending(d.settings.cases_root, at, d.settings) == [(c.case_id, "message bounced"), (c.case_id, "message bounced")]
    result = c.read_step("result")
    assert result["status"] == "review" and [(i["item_id"], i["reason"]) for i in result["review_items"]][-2:] == [
        ("RI4", "message bounced"), ("RI5", "message bounced")]
    escalated = [json.loads(line) for line in (c.root / "messages" / "escalated.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [line["message_id"] for line in escalated] == [second, third] and audit(c)["escalations"] == escalated
    assert escalate_pending(d.settings.cases_root, datetime(2026, 12, 14, 9, 0), d.settings) == [] and c.read_step("result") == result


def test_the_audit_names_the_question_an_e_mail_belongs_to_of_the_two_it_names(tmp_path, madeup_out):
    """Ruling S24. A reply that names two questions of one case belongs to the one its In-Reply-To names, whichever was sent first,
    and the audit's entry names that question."""
    d, c, (second, third) = two_asked(tmp_path, madeup_out)
    for name, named, other in (("a.eml", second, third), ("b.eml", third, second)):
        reply = a_reply(tmp_path / name, "Re: Vraag over orderbevestiging PO 250112", "sales@s12.example.com",
                        in_reply_to=f"<{named}@example.com>", references=f"<{other}@example.com> <{named}@example.com>")
        assert file_answer(d.settings.cases_root, reply, datetime(2026, 11, 13, 11, 0)) == c.case_id
    assert [(a["file"], a["message_id"]) for a in audit(c)["answers"]][1:] == [("a.eml", second), ("b.eml", third)]


def test_a_message_of_the_aids_own_side_is_filed_as_such_and_ends_no_wait(tmp_path, madeup_out):
    """Ruling S21. The buyer is in copy of every question, so his own reply to all names it: an e-mail whose sender is one of the
    addresses of the aid's own side is filed with its case under own/, leaves the result as it is and ends no wait, so that the
    supplier's silence is still reported; the same e-mail from any other address is the supplier's answer."""
    d = deps(tmp_path, madeup_out)
    c, message_id = asked(d, madeup_out, tmp_path)
    before = c.read_step("result")
    own = [d.settings.buyer_email, d.settings.aid_email, None]
    mine = a_reply(tmp_path / "mine.eml", "RE: Vraag over orderbevestiging PO 250117", f"De inkoper <{d.settings.buyer_email.upper()}>",
                   in_reply_to=message_id)
    assert file_answer(d.settings.cases_root, mine, NOW + timedelta(hours=1), own=own) == c.case_id
    assert (c.root / "messages" / "own" / "mine.eml").exists() and not (c.root / "messages" / "replies").exists()
    assert c.read_step("result") == before and [(a["file"], a["filed_under"]) for a in audit(c)["answers"]] == [("mine.eml", "own")]
    assert escalate_pending(d.settings.cases_root, datetime(2026, 11, 17, 9, 1), d.settings) == [(c.case_id, "no answer in 3 working days")]
    other = deps(tmp_path / "other", madeup_out)
    c2, message_id = asked(other, madeup_out, tmp_path / "other")
    theirs = a_reply(tmp_path / "theirs.eml", "RE: Vraag over orderbevestiging PO 250117", "sales@s17.example.com", in_reply_to=message_id)
    assert file_answer(other.settings.cases_root, theirs, NOW + timedelta(hours=1), own=own) == c2.case_id
    assert c2.read_step("result")["status"] == "review" and (c2.root / "messages" / "replies" / "theirs.eml").exists()


def test_a_waiting_case_without_a_question_comes_back_to_the_buyer(tmp_path, madeup_out):
    """Ruling S23, the net under every path to a case that waits for nothing. A waiting case with no question outstanding comes back
    at the next pass: with the reply's item when a reply was filed that has none (a file put into replies/ by hand), else with
    the reason "no question is open" (a run without a mailer; a case whose reply was read and decided, left waiting by an earlier
    version). A case whose question is out, a closed case and a case in review are left as they are, and nothing is given twice."""
    from aid.tests.test_messaging import at_time

    d = mailing(deps(tmp_path, madeup_out), tmp_path / "outbox")
    root = d.settings.cases_root
    out, _ = run(d, madeup_out, "MU17", "250117")                            # its question is out
    by_hand, _ = run(d, madeup_out, "MU11", "250111")                        # a reply put into its folder by hand
    folder = by_hand.root / "messages" / "replies"
    folder.mkdir()
    at_time(an_email(folder / "antwoord.eml", "Uw vraag over PO 250111", "sales@s11.example.com"), NOW + timedelta(hours=3))
    unasked, r = run(d.model_copy(update={"mailer": None}), madeup_out, "MU20", "250120")     # no mailer: nothing was asked
    assert r.status == "waiting" and not (unasked.root / "messages" / "sent.jsonl").exists()
    old, r = run(d, madeup_out, "MU17", "250117", now=LATER)                 # the reply was read and kept, and the case left waiting
    reply = a_reply(tmp_path / "reply.eml", "Re: Vraag", "sales@s17.example.com", in_reply_to=f"<{r.messages[0]['message_id']}@example.com>")
    assert file_answer(root, reply, NOW + timedelta(hours=1)) == old.case_id
    old.write_step(8, "result", CaseResult.model_validate(old.read_step("result")).model_copy(update={"status": "waiting"}))
    closed, r = run(d.model_copy(update={"mailer": None}), madeup_out, "MU13", "250113")
    closed.write_step(8, "result", r.model_copy(update={"status": "closed"}))
    in_review, _ = run(d, madeup_out, "MU12", "250112")
    left = {case.case_id: (case.read_step("result"), audit(case)) for case in (out, closed, in_review)}
    later = NOW + timedelta(hours=4)
    assert end_empty_waits(root, later, d.settings) == sorted([
        (by_hand.case_id, "supplier answered: read the reply"), (unasked.case_id, "no question is open"), (old.case_id, "no question is open")])
    for case, reasons in ((by_hand, ["supplier answered: read the reply"]), (unasked, ["no question is open"]),
                          (old, ["supplier answered: read the reply", "no question is open"])):
        result = CaseResult.model_validate(case.read_step("result"))
        assert result.status == "review" and "ter beoordeling" in result.digest_line
        assert [(i.item_id, i.reason, i.finding_id) for i in result.review_items] == [(f"RI{n}", reason, None) for n, reason in enumerate(reasons, 1)]
        assert audit(case)["escalations"] == [{"case_id": case.case_id, "message_id": None, "reason": reasons[-1], "at": later.isoformat()}]
        assert not (case.root / "messages" / "escalated.jsonl").exists()
    assert {case.case_id: (case.read_step("result"), audit(case)) for case in (out, closed, in_review)} == left
    assert end_empty_waits(root, later + timedelta(days=30), d.settings) == []
    assert escalate_pending(root, later + timedelta(days=30), d.settings) == [(out.case_id, "no answer in 3 working days")]


def test_a_pass_reads_the_replies_of_the_cases_that_wait_only(tmp_path, madeup_out, monkeypatch):
    """Ruling S25. escalate_pending reads the filed replies of the cases that wait, and of no other: not those of a closed case
    nor of a case in review, however many replies they hold."""
    import aid.messaging

    d = mailing(deps(tmp_path, madeup_out), tmp_path / "outbox")
    cases = {}
    for name, po in (("MU11", "250111"), ("MU17", "250117"), ("MU20", "250120")):
        c, r = run(d, madeup_out, name, po)
        other = a_reply(tmp_path / "other.eml", "Re: iets anders", f"sales@s{po[-2:]}.example.com", in_reply_to="<0123abcd@example.org>")
        file_reply(c, other)                                                 # filed by hand: it names another message and answers nothing
        cases[name] = (c, r)
    closed, r = cases["MU11"]
    closed.write_step(8, "result", r.model_copy(update={"status": "closed"}))
    in_review, r = cases["MU17"]
    in_review.write_step(8, "result", r.model_copy(update={"status": "review"}))
    waiting, _ = cases["MU20"]
    read, seen = aid.messaging.named_ids, []
    monkeypatch.setattr(aid.messaging, "named_ids", lambda eml: (seen.append(Path(eml).parents[2].name), read(eml))[1])
    assert escalate_pending(d.settings.cases_root, NOW + timedelta(days=30), d.settings) == [(waiting.case_id, "no answer in 3 working days")]
    assert seen == [waiting.case_id]


def test_a_reply_that_cannot_be_read_at_a_pass_leaves_its_case_for_the_next(tmp_path, madeup_out, monkeypatch):
    """Ruling S17. The read of one filed reply fails at a pass: its case is named as unreadable and not escalated, the others go
    on, and at the next pass, when the file reads again, the reply is the answer it is."""
    import aid.messaging

    d = mailing(deps(tmp_path, madeup_out), tmp_path / "outbox")
    held, r = run(d, madeup_out, "MU11", "250111")
    late = file_reply(held, a_reply(tmp_path / "late.eml", "Re: Vraag", "sales@s11.example.com", in_reply_to="<0123abcd@example.org>"))
    other, _ = run(d, madeup_out, "MU17", "250117")
    read = aid.messaging.named_ids

    def locked(eml):
        if Path(eml) == late:
            raise PermissionError(13, "made-up: the file is in use by another program")
        return read(eml)
    monkeypatch.setattr(aid.messaging, "named_ids", locked)
    later = NOW + timedelta(days=30)
    assert escalate_pending(d.settings.cases_root, later, d.settings) == [
        (held.case_id, "unreadable: PermissionError"), (other.case_id, "no answer in 3 working days")]
    assert held.read_step("result") == r.model_dump(mode="json")
    monkeypatch.setattr(aid.messaging, "named_ids", read)
    assert escalate_pending(d.settings.cases_root, later, d.settings) == [(held.case_id, "no answer in 3 working days")]


class Doubtful:
    """A mailer whose hand-over is never confirmed: the message may have left, under the id of its record."""
    name = "doubtful"

    def send(self, msg):
        record = SentRecord(message_id="ab" * 16, to=msg.to, subject=msg.subject, sent_at=NOW.isoformat(), template=msg.template, unconfirmed=True)
        raise HandOverUnconfirmed("TimeoutError: no confirmation that the draft was sent", record)


def test_a_question_of_the_run_whose_hand_over_was_not_confirmed_counts_as_asked(tmp_path, madeup_out):
    """Ruling S5. The mailer could not confirm the hand-over of the run's question: the run records the message as sent, with the
    note that it was not confirmed and under the id it would carry, and the case waits. So a reply that names it is filed with the
    case, and when none comes the case returns after the wait; nothing is sent a second time."""
    d = mailing(deps(tmp_path, madeup_out), tmp_path / "outbox").model_copy(update={"mailer": Doubtful()})
    c, r = run(d, madeup_out, "MU17", "250117")
    [message] = r.messages
    assert r.status == "waiting" and r.review_items == [] and (message["status"], message["unconfirmed"], message["message_id"]) == ("sent", True, "ab" * 16)
    assert audit(c)["messages"][0]["unconfirmed"] is True and "error" not in audit(c)["messages"][0]
    [line] = (c.root / "messages" / "sent.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["unconfirmed"] is True and pending_asks(d.settings.cases_root, datetime(2026, 11, 17, 9, 1), 3) == [c.case_id]
    reply = a_reply(tmp_path / "reply.eml", "Re: Vraag over orderbevestiging PO 250117", "sales@s17.example.com", in_reply_to=f"<{'ab' * 16}@example.com>")
    assert file_answer(d.settings.cases_root, reply, NOW + timedelta(hours=2)) == c.case_id
    assert c.read_step("result")["status"] == "review" and escalate_pending(d.settings.cases_root, NOW + timedelta(days=30), d.settings) == []


# --- the audit record ---

def test_the_audit_record_of_a_closed_case(tmp_path, madeup_out):
    """MU08 with the parser: the input by its hash, each step with its seconds and output, each action as a decision with the
    settings its rule read and the findings and facts it was applied to, each write, and no message, error, model or call: the
    version and the lines are settled by rule, so the decider is never asked."""
    c, r = run(deps(tmp_path, madeup_out), madeup_out, "MU08", "250108")
    record = audit(c)
    assert record["inputs"] == [{"name": "MU08_conf.pdf", "sha256": sha256_file(madeup_out / "MU08_conf.pdf")}]
    assert [s["name"] for s in record["steps"]] == ["po_versions", "read_document", "version", "match", "compare", "route", "execute", "result"]
    assert all(s["seconds"] >= 0 for s in record["steps"]) and record["steps"][-1]["payload"] == c.read_step("result")
    assert record["steps"][2]["payload"] == r.version.model_dump(mode="json") and record["steps"][4]["payload"] == r.findings.model_dump(mode="json")
    assert [(d["rule_id"], d["threshold"], d["result"]) for d in record["decisions"]] == [
        ("R04", {"price_pct": "2.0", "price_abs": "25.00"}, "AUTO"), ("R01", {}, "AUTO")]
    inputs = record["decisions"][0]["inputs"]
    assert inputs["finding_ids"] == ["F1"] and inputs["findings"] == [r.findings.flags[0].model_dump(mode="json")]
    assert inputs["context"] == {"supplier": "S08", "kind": "confirmation", "version": 1, "cases_seen_for_supplier": 0,
                                 "supplier_lead_time_days": 10, "po_above_authorization": False}
    assert record["writes"] == [e.model_dump(mode="json") for e in r.write_entries]
    assert (record["messages"], record["errors"], record["model"], record["calls"]) == ([], [], [], [])
    assert record["settings"]["authorization_limit"] == "set" and record["started_at"] == NOW.isoformat() == record["finished_at"]


def test_the_thresholds_of_each_rule(tmp_path, madeup_out):
    """Each rule that occurs on the made-up set cites the settings it read (RULE_SETTINGS of the routing): the price rules the two
    price thresholds, the lead time rules the tolerance and the supplier's own lead time (none when the settings give the supplier
    none), the questions the suppliers whose messages are off, the supplier rule the number of first cases and the marked
    suppliers, and the attachment and the uncompared items nothing."""
    d = deps(tmp_path, madeup_out)
    thresholds = {}
    for case in ("MU06", "MU07", "MU08", "MU11", "MU14", "MU15", "MU17", "MU20"):
        c, _ = run(d, madeup_out, case, f"2501{case[2:]}")
        thresholds |= {decision["rule_id"]: decision["threshold"] for decision in audit(c)["decisions"]}
    c, _ = run(with_settings(d, first_cases_review=1, review_suppliers=["S99"]), madeup_out, "MU01", "250101", now=LATER)
    thresholds |= {decision["rule_id"]: decision["threshold"] for decision in audit(c)["decisions"]}
    price, asked = {"price_pct": "2.0", "price_abs": "25.00"}, {"messages_off_suppliers": []}
    assert thresholds == {"R01": {}, "R02": {"lead_time_tolerance_days": 7, "supplier_lead_times": {"S14": 10}},
                          "R03": {"lead_time_tolerance_days": 7, "supplier_lead_times": {"S15": 10}}, "R04": price, "R05": price,
                          "R06": asked, "R07": asked, "R08": asked, "R09": {}, "R11": {"first_cases_review": 1, "review_suppliers": ["S99"]}}
    c, _ = run(with_settings(d, supplier_lead_times={}), madeup_out, "MU15", "250115", now=LATER)
    assert [(x["rule_id"], x["threshold"]) for x in audit(c)["decisions"]] == [("R03", {"lead_time_tolerance_days": 7, "supplier_lead_times": {}})]


def test_reapproval_is_recorded_with_the_limit_as_set_and_never_its_amount(tmp_path, madeup_out):
    """MU08 above a limit of half its PO's total: the price correction becomes R10, which names the rule it took its writes from, and
    its decision names the limit "set" beside the price thresholds of R04. Its request to the approver is a message recorded as not
    sent, which leaves the case as the rules route it and is no disposition. The amount is in no file of the case."""
    d = deps(tmp_path, madeup_out)
    limit = d.store.versions("250108")[0].total / 2                   # from the made-up PO's total, as test_po_store.py derives its limits
    limited = d.model_copy(update={"settings": d.settings.model_copy(update={"authorization_limit": limit}), "store": PoStore(d.store.root, limit)})
    c, r = run(limited, madeup_out, "MU08", "250108")
    assert [(a.rule_id, a.route, a.base_rule_id) for a in r.actions] == [("R10", "AUTO", "R04"), ("R01", "AUTO", None)] and r.status == "closed"
    [reapproval, _] = audit(c)["decisions"]
    assert reapproval["threshold"] == {"authorization_limit": "set", "price_pct": "2.0", "price_abs": "25.00"}
    assert reapproval["inputs"]["context"]["po_above_authorization"] is True
    assert r.messages == audit(c)["messages"] == [{"rule_id": "R10", "template": "reapproval", "items": [
        {"kind": "po_line_price", "line_id": "1", "article_code": "", "old_value": "12.50", "new_value": "12.70"},
        {"kind": "article_price", "line_id": "", "article_code": "KM-0801", "old_value": "", "new_value": "12.70"}],
        "finding_ids": ["F1"], "status": "not sent (no mailer)", "timestamp": NOW.isoformat()}]
    assert [row["decision"] for row in read_csv(record_dispositions(c, r))] == ["prepared (R10)"] * 2 + ["prepared (R01)"] * 2
    assert not [path for path in c.root.rglob("*") if path.is_file() and str(limit).encode() in path.read_bytes()]


def test_the_audit_records_the_reader_model_and_its_call(tmp_path, madeup_out):
    """MU08 read by the LLM reader over its recorded answer: the call with what it took, and the reader's model with the version and
    the hash of the reading prompt."""
    c, r = run(deps(tmp_path, madeup_out).model_copy(update={"reader": recorded_reader}), madeup_out, "MU08", "250108")
    assert r.status == "closed" and c.read_step("read_document")["reader"] == "llm:recorded:v1"
    record = audit(c)
    assert record["calls"] == [{"kind": "reader", "model": "synthetic", "input_tokens": 0, "output_tokens": 0, "cost_eur": None, "seconds": 0.0,
                                "request_id": None, "attempts": 1}]
    prompt = reading_request(c.input_files[0], "confirmation").system
    assert record["model"] == [{"role": "reader", "name": "synthetic", "prompt_version": "v1",
                                "system_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}]


def test_the_audit_records_each_answer_of_the_decider_and_its_model(tmp_path, madeup_out):
    """MU03 read without its version quote and its item codes: neither the date nor the lines settle the version, so the decider is
    asked, once. Its answer is a call in the audit, and the model decider is the decider's model there."""
    client = ScriptedClient('{"choice": "1", "confidence": 0.9, "rationale": "the quantities are those of version 1"}')
    d = deps(tmp_path, madeup_out).model_copy(update={"reader": unquoted, "decider": LlmDecider(client)})
    c, r = run(d, madeup_out, "MU03", "250103")
    assert (r.version.version, r.version.method, r.status, len(client.requests)) == (1, "model", "closed", 1)
    record = audit(c)
    assert record["calls"] == [{"kind": "decider", "source": "model:scripted-model", "input_tokens": 50, "output_tokens": 8, "cost_eur": None,
                                "seconds": 0.25, "request_id": "r1"}]
    prompt = DECISION_PROMPT.read_text(encoding="utf-8").strip()
    assert record["model"] == [{"role": "decider", "name": "scripted", "prompt_version": "v1",
                                "system_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}]


def test_a_line_match_decision_is_recorded_as_a_decider_call(tmp_path, madeup_out):
    """MU01 read with line 1's item code left out and a description that holds the PO code KM-1001: a code held in a text never
    makes a pair by itself, so the matching asks the decider which PO line the line answers. The answer pairs the line and is the
    one call of the audit."""
    def held_code(path, kind):
        doc = read_with_parser(path, kind)
        line = doc.lines[0].model_copy(update={"item_code": DocValue(), "description": DocValue(value="Vervangt KM-1001", text="Vervangt KM-1001")})
        return doc.model_copy(update={"lines": [line, *doc.lines[1:]]})

    client = ScriptedClient('{"choice": "1", "confidence": 0.9, "rationale": "the line names KM-1001"}')
    d = deps(tmp_path, madeup_out).model_copy(update={"reader": held_code, "decider": LlmDecider(client)})
    c, r = run(d, madeup_out, "MU01", "250101")
    assert r.status == "closed" and [request.user_text.splitlines()[0] for request in client.requests] == ["The question: line_match"]
    assert [(pair["po_line_ids"], pair["doc_line_ids"], pair["basis"]) for pair in c.read_step("match")["pairs"]][-1] == (["1"], ["1"], "model")
    assert [(call["kind"], call["source"]) for call in audit(c)["calls"]] == [("decider", "model:scripted-model")]


def test_the_answers_of_no_decider_are_recorded_too(tmp_path, madeup_out):
    """The same case with NoDecider: its answer chooses nothing, so the version cannot be selected; the answer is still a call."""
    c, r = run(deps(tmp_path, madeup_out).model_copy(update={"reader": unquoted}), madeup_out, "MU03", "250103")
    assert r.status == "review" and r.version.reason == "cannot select" and r.findings is None
    record = audit(c)
    assert [(call["kind"], call["source"]) for call in record["calls"]] == [("decider", "none")] and record["model"] == []


# --- dispositions ---

def test_dispositions_of_the_writes_and_of_the_questions(tmp_path, madeup_out):
    """The header of the protocol's template; one row per write, with the rule of its action, the field it wrote and the page and line
    of the document's value (else the rule); one row per item of a question; the note names the aid's folder, the rule and the
    finding. The case id is the evaluation's when the caller gives one, else the folder's. A second call appends."""
    d = deps(tmp_path, madeup_out)
    c, r = run(d, madeup_out, "MU08", "250108")
    path, folder = record_dispositions(c, r), c.case_id
    template = (REPO_ROOT / "evaluation" / "templates" / "dispositions.csv").read_text(encoding="utf-8").strip()
    assert path == c.root / "dispositions.csv" and path.read_text(encoding="utf-8").splitlines()[0] == template == ",".join(DISPOSITION_COLUMNS)
    rows = read_csv(path)
    assert [tuple(row.values()) for row in rows] == [
        (folder, "1", "1", "price", "prepared (R04)", "page 1, line 8", f"folder {folder}; rule R04; finding F1"),
        (folder, "1", "", "article_price", "prepared (R04)", "page 1, line 8", f"folder {folder}; rule R04; finding F1"),
        (folder, "1", "", "attach_document", "prepared (R01)", "R01", f"folder {folder}; rule R01"),
        (folder, "1", "", "set_status", "prepared (R01)", "R01", f"folder {folder}; rule R01")]
    record_dispositions(c, r)
    assert read_csv(path) == rows + rows and path.read_text(encoding="utf-8").count("case_id,") == 1
    c, r = run(d, madeup_out, "MU17", "250117")
    assert [tuple(row.values()) for row in read_csv(record_dispositions(c, r, case_id="C017"))] == [
        ("C017", "1", "3", "line_presence", "ask the supplier (R07)", "R07", f"folder {c.case_id}; rule R07; finding F1")]


def test_a_question_cites_the_value_on_the_document(tmp_path, madeup_out):
    """MU11: the quantity the confirmation prints stands on page 1, line 8; MU18: an extra line has no PO line, so its row names the
    document's line."""
    d = deps(tmp_path, madeup_out)
    c, r = run(d, madeup_out, "MU11", "250111")
    assert [(row["field"], row["line_id"], row["decision"], row["source"]) for row in read_csv(record_dispositions(c, r))] == [
        ("quantity", "1", "ask the supplier (R06)", "page 1, line 8")]
    c, r = run(d, madeup_out, "MU18", "250118")
    assert [(row["field"], row["line_id"], row["decision"]) for row in read_csv(record_dispositions(c, r))] == [
        ("line_presence", "3", "ask the supplier (R07)")]


def test_an_executed_write_is_an_exact_update_and_a_review_has_no_row(tmp_path, madeup_out):
    """MU14 through a writer that makes its changes: "Exact update executed". MU07 waits for the buyer, who records the disposition."""
    d = deps(tmp_path, madeup_out, writer=Executing())
    c, r = run(d, madeup_out, "MU14", "250114")
    assert [(row["field"], row["line_id"], row["decision"]) for row in read_csv(record_dispositions(c, r))] == [
        ("delivery_date", "1", "Exact update executed (R02)"), ("attach_document", "", "Exact update executed (R01)"),
        ("set_status", "", "Exact update executed (R01)")]
    c, r = run(d, madeup_out, "MU07", "250107")
    assert read_csv(record_dispositions(c, r)) == []


# --- the digest line, the context and the cart ---

def test_the_digest_line_in_dutch_and_in_english(tmp_path, madeup_out):
    """The PO, the supplier and the status, and the writes made, the changes prepared and the writes failed, each counted on its own."""
    d = deps(tmp_path, madeup_out)
    made, english = d.model_copy(update={"writer": Executing()}), with_settings(d, language="en")
    assert run(made, madeup_out, "MU01", "250101")[1].digest_line == "PO 250101 (S01): afgesloten, 2 schrijfacties"
    assert run(with_settings(made, language="en"), madeup_out, "MU01", "250101", now=LATER)[1].digest_line == "PO 250101 (S01): closed, 2 writes"
    assert run(d, madeup_out, "MU08", "250108")[1].digest_line == "PO 250108 (S08): afgesloten, 4 voorbereide wijzigingen"
    assert run(english, madeup_out, "MU08", "250108", now=LATER)[1].digest_line == "PO 250108 (S08): closed, 4 prepared changes"
    mixed = d.model_copy(update={"writer": Executing("done", "prepared", "prepared")})
    assert run(mixed, madeup_out, "MU14", "250114")[1].digest_line == "PO 250114 (S14): afgesloten, 1 schrijfactie, 2 voorbereide wijzigingen"
    assert run(d, madeup_out, "MU07", "250107")[1].digest_line == "PO 250107 (S07): ter beoordeling, geen schrijfacties"
    assert run(english, madeup_out, "MU07", "250107", now=LATER)[1].digest_line == "PO 250107 (S07): for review, no writes"
    assert run(d, madeup_out, "MU17", "250117")[1].digest_line == "PO 250117 (S17): wacht op de leverancier, geen schrijfacties"
    assert run(english, madeup_out, "MU17", "250117", now=LATER)[1].digest_line == "PO 250117 (S17): waiting for the supplier, no writes"
    assert run(english, madeup_out, "MU05", "250105")[1].digest_line == "PO 250105 (S05): failed, no writes"


def test_cases_seen_counts_the_closed_cases_of_the_same_supplier(tmp_path, madeup_out):
    """cases_seen_for_supplier counts the other cases that closed for the PO's supplier: a case in review does not count, nor does a
    case of another supplier. With first_cases_review at 1, a supplier without a closed case is new (R11) and goes to review."""
    d = deps(tmp_path, madeup_out)
    assert run(d, madeup_out, "MU02", "250102")[1].status == "closed"           # supplier S02
    assert run(d, madeup_out, "MU07", "250107")[1].status == "review"           # supplier S07
    strict = with_settings(d, first_cases_review=1)
    seen = {}
    for case, po_number in (("MU02", "250102"), ("MU07", "250107"), ("MU01", "250101")):
        c, r = run(strict, madeup_out, case, po_number, now=LATER)
        seen[case] = (c.read_step("route")["context"]["cases_seen_for_supplier"], r.status, [(a.rule_id, a.reason) for a in r.actions])
    assert seen == {"MU02": (1, "closed", [("R01", "attach and confirm")]), "MU07": (0, "review", [("R11", "new supplier")]),
                    "MU01": (0, "review", [("R11", "new supplier")])}


def test_the_context_of_the_routing(tmp_path, madeup_out):
    """The context comes from the selected version: its supplier, the supplier's lead time, the dates, price bases and item codes of
    its lines, whether it is above the authorization, and the case's document."""
    c, r = run(deps(tmp_path, madeup_out), madeup_out, "MU14", "250114")
    assert c.read_step("route")["context"] == {
        "supplier": "S14", "po_number": "250114", "version": 1, "po_date": {"1": "2026-11-16", "2": "2026-11-16"}, "po_basis": {"1": "1", "2": "1"},
        "po_item_code": {"1": "KM-1401", "2": "KM-1402"}, "po_above_authorization": False, "supplier_lead_time_days": 10,
        "cases_seen_for_supplier": 0, "document_path": str(c.input_files[0]), "kind": "confirmation"}


def test_a_cart_is_checked_against_the_po_of_its_export(tmp_path, madeup_out):
    """CART02, the screenshot read by the LLM reader over its recorded answer, against the PO filed from the ERP export. With a price
    threshold of 3 percent, the 2.5 percent difference on KM-2002 is corrected on the PO line and the article (R04); the PO line
    that is not in the cart goes to the buyer (R07). A cart gets no attachment and no status."""
    d = deps(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"price_pct": Decimal("3.0")})).model_copy(update={"reader": recorded_reader})
    [version] = d.store.from_export(madeup_out / "CART02_export.csv", {})      # the empty map: each column is named after its field
    c, r = run(d, madeup_out, "CART02", "250202", kind="cart", document="CART02.png")
    assert r.status == "review" and (r.version.version, r.version.method) == (version.version, "reference") and r.supplier == "S22"
    assert [(a.rule_id, a.route, a.reason) for a in r.actions] == [("R07", "REVIEW", "could not be placed"), ("R04", "AUTO", "price within threshold")]
    assert [(e.op.kind, e.op.line_id, e.op.article_code, e.previous, e.new) for e in r.write_entries] == [
        ("po_line_price", "2", None, "40.00", "41.00"), ("article_price", None, "KM-2002", None, "41.00")]
    assert [(i.finding_id, i.field, i.reason) for i in r.review_items] == [("F1", "line_presence", "could not be placed")]
    assert r.digest_line == "voorcontrole PO 250202 (S22): ter beoordeling, 2 voorbereide wijzigingen"


# --- what a case is matched against: the PDF versions, or the PO as it stands (Task 40) ---

def export_of(path: Path, case_id: str, **first_line: str) -> Path:
    """The ERP export of the made-up PO of `case_id` (export_csv of the generator: the lines of its latest version), written to
    `path` with the cells of its first line given in `first_line` (column -> text) replaced."""
    spec = MadeUpSpec.model_validate_json((REPO_ROOT / "aid" / "madeup" / "spec" / f"{case_id}.json").read_text(encoding="utf-8"))
    rows = list(csv.reader(io.StringIO(export_csv(spec))))
    for column, text in first_line.items():
        rows[1][rows[0].index(column)] = text
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)
    return path


def filed_later(store: PoStore, po_number: str, look: str, seconds: int = 60) -> None:
    """Make `look` ("e1") of the PO filed `seconds` after the newest po.json of the PO's other folders, so that the order of filing
    the test needs does not rest on the clock."""
    folder = store.root / po_number
    newest = max(path.stat().st_mtime for path in folder.glob("*/po.json") if path.parent.name != look)
    os.utime(folder / look / "po.json", (newest + seconds, newest + seconds))


def test_select_version_gets_the_pdf_versions_for_a_store_of_pdfs(tmp_path, madeup_out, monkeypatch):
    """For a store that holds only PO PDFs a case is matched against the PDF versions, as before Task 40: select_version gets every
    version of the PO for a confirmation and the latest alone for a cart, here on PO 250103, which has two PDFs, and step 01 holds
    every version for both."""
    given = []

    def spy(doc, versions, decider):
        given.append(list(versions))
        return versioning.select_version(doc, versions, decider)
    monkeypatch.setattr("aid.pipeline.select_version", spy)
    d = deps(tmp_path, madeup_out)
    both = d.store.versions("250103")
    assert [v.version for v in both] == [1, 2] and d.store.snapshots("250103") == []
    confirmation, r = run(d, madeup_out, "MU03", "250103")
    cart, cart_result = run(d, madeup_out, "CART01", "250103", kind="cart", document="CART01.html", now=LATER)
    assert given == [both, both[-1:]] and (r.version.version, cart_result.version.version) == (1, 2)    # MU03 quotes version 1
    for case in (confirmation, cart):
        assert [PoVersion.model_validate(v) for v in case.read_step("po_versions")["versions"]] == both


def test_the_made_up_batch_writes_the_files_it_wrote(tmp_path, capsys):
    """A golden comparison (rule 7 of Task 40): the made-up batch, run as python3 -B -m aid.demo runs it, writes the summary and, for
    each of its 22 cases, the step files, the disposition log and the write log that the code of e26bbb7, before Task 40, wrote. They
    are held against the record of that run (aid/tests/fixtures/made_up_batch.json, made by aid/tests/made_up_batch.py, whose
    docstring says how to make it again), by path and by the sha256 of the normalised content. Step 01 of the two carts is included:
    the look keeps the export's path and its sha256. Only the PO store differs, by design: e1 where it held v1."""
    run = tmp_path / "run"
    assert demo.main(["--out", str(run)]) == 0
    capsys.readouterr()
    recorded = json.loads(made_up_batch.FIXTURE.read_text(encoding="utf-8"))
    written = made_up_batch.record(run, REPO_ROOT)
    assert recorded["commit"] == "e26bbb7" and len(recorded["files"]) == 193
    assert [name for name in sorted(set(recorded["files"]) | set(written)) if recorded["files"].get(name) != written.get(name)] == []
    assert len([name for name in written if name.endswith("/steps/08_result.json")]) == 22
    for po_number in ("250201", "250202"):
        assert [p.name for p in (run / "po_store" / po_number).iterdir()] == ["e1"]


def drawn_po(path: Path, case_id: str, po_number: str) -> Path:
    """Version 1 of the made-up PO of `case_id` drawn by the generator into `path` under the PO number `po_number`, so that a PO
    can have a PDF with the lines of another."""
    spec = MadeUpSpec.model_validate_json((REPO_ROOT / "aid" / "madeup" / "spec" / f"{case_id}.json").read_text(encoding="utf-8"))
    pages = po_pages(spec, spec.po.versions[0])
    next(row for row in pages[0] if row.ref == "po_reference").texts[0].text = f"Inkooporder {po_number}"
    path.write_bytes(pdf_bytes(pages, path.name))
    return path


def test_a_cart_of_a_po_with_a_pdf_and_a_later_look_is_checked_against_the_look(tmp_path, madeup_out):
    """PO 250201 has a PDF (with MU03's lines) and, filed after it, the look of CART01's export: the look is the PO as it stands. A
    cart is matched against it alone, and CART01's page agrees with it line by line, while a confirmation of the same PO would get
    the PDF. Step 01 holds the look alone, read with the PDF's number and supplier, and the findings are taken against it."""
    d = deps(tmp_path, madeup_out)
    pdf = d.store.add(drawn_po(tmp_path / "250201.pdf", "MU03", "250201"))
    [look] = d.store.from_export(madeup_out / "CART01_export.csv", EXPORT_COLUMNS)
    filed_later(d.store, "250201", "e1")
    assert d.store.candidates("250201", "confirmation") == [pdf] and (look.version, look.supplier) == (1, "S03")
    c, r = run(d, madeup_out, "CART01", "250201", kind="cart", document="CART01.html")
    assert [PoVersion.model_validate(v) for v in c.read_step("po_versions")["versions"]] == [look]
    assert (r.status, r.version.version, r.findings.version, r.findings.flags, r.findings.uncompared) == ("closed", 1, 1, [], [])
    assert {line.item_code.value for line in look.document.lines} == {"KM-2101", "KM-2102", "KM-2103"}
    assert sorted({m.po_line_ids[0] for m in r.findings.matches if m.field == "line_presence"}) == ["1", "2", "3"]
    assert r.supplier == "S03"


def test_a_confirmation_of_a_po_with_an_export_and_one_pdf_selects_the_pdf(tmp_path, madeup_out):
    """PO 250101 has its PDF and, filed after it, a look from an export whose price of line 1 differs: the look is the PO as it
    stands, but a confirmation is never matched against an export while its PO has a PDF. MU01 is checked against the PDF version
    alone, as without the export, and closes."""
    d = deps(tmp_path, madeup_out)
    [look] = d.store.from_export(export_of(tmp_path / "MU01_export.csv", "MU01", unit_price="13.75"), EXPORT_COLUMNS)
    filed_later(d.store, "250101", "e1")
    [pdf] = d.store.versions("250101")
    assert d.store.as_it_stands("250101") == look and look.document.lines[0].unit_price.value == "13.75"
    c, r = run(d, madeup_out, "MU01", "250101")
    assert (r.status, r.version.version, r.version.reason, r.findings.flags) == ("closed", 1, "single version", [])
    assert [PoVersion.model_validate(v) for v in c.read_step("po_versions")["versions"]] == [pdf]
    assert c.read_step("route")["context"]["supplier"] == "S01"


def test_a_confirmation_of_a_po_known_from_an_export_only_is_compared_with_it(tmp_path, madeup_out):
    """A store that knows PO 250108 from an export only: a confirmation of it is matched against its look, the one candidate, which
    reads as version 1. MU08's price of line 1 (12.70 against the export's 12.50, within the thresholds) is found against it, and the
    look is what step 01 holds."""
    d = deps(tmp_path, madeup_out)
    store = PoStore(tmp_path / "exports_only", d.settings.authorization_limit)
    d = d.model_copy(update={"store": store})
    [look] = store.from_export(export_of(tmp_path / "MU08_export.csv", "MU08"), EXPORT_COLUMNS)
    assert store.versions("250108") == [] and store.candidates("250108", "confirmation") == [look]
    c, r = run(d, madeup_out, "MU08", "250108")
    assert (r.version.version, r.version.method, r.version.reason) == (1, "reference", "single version")
    assert [PoVersion.model_validate(v) for v in c.read_step("po_versions")["versions"]] == [look]
    assert [(f.field, f.po_line_ids, f.po_value.value, f.doc_value.value) for f in r.findings.flags] == [("price", ["1"], "12.50", "12.70")]
    assert r.findings.version == 1 and r.supplier == "S08"


# --- the readers and the upload ---

def test_the_parser_reader_refuses_what_it_cannot_read(tmp_path, madeup_out, monkeypatch):
    assert read_with_parser(madeup_out / "MU01_conf.pdf", "confirmation").reader == "parser:table_nl"
    with pytest.raises(ReaderError, match=r"^no template for MU05_conf\.pdf$"):
        read_with_parser(madeup_out / "MU05_conf.pdf", "confirmation")                     # a scan has no text
    for name, kind in (("CART01.html", "confirmation"), ("CART02.png", "cart"), ("MU01_conf.pdf", "cart"), ("CART02.png", "confirmation")):
        with pytest.raises(ReaderError, match=rf"^no template for {name}: the parser reads PDF confirmations and HTML cart pages only$"):
            read_with_parser(madeup_out / name, kind)

    def does_not_fit(pdf, template):
        raise ValueError("no row contains 'Pos'")

    monkeypatch.setattr("aid.pipeline.read_with_template", does_not_fit)
    with pytest.raises(ReaderError, match=r"^no template for MU01_conf\.pdf: no row contains 'Pos'$") as refused:
        read_with_parser(madeup_out / "MU01_conf.pdf", "confirmation")
    assert isinstance(refused.value.__cause__, ValueError)


def test_open_upload_case(tmp_path, madeup_out):
    c = open_upload_case(tmp_path / "cases", [madeup_out / "MU01_conf.pdf"], " 250101 ", "confirmation", NOW)
    assert (c.case_id, c.kind, c.po_number) == ("250101_20261112T090000", "confirmation", "250101")
    assert [p.name for p in c.input_files] == ["MU01_conf.pdf"] and c.input_files[0].read_bytes() == (madeup_out / "MU01_conf.pdf").read_bytes()
    unknown = open_upload_case(tmp_path / "cases", [madeup_out / "CART02.png"], "", "cart", NOW)
    assert (unknown.case_id, unknown.kind, unknown.po_number) == ("unknown_20261112T090000", "cart", None)
    with pytest.raises(ValueError, match="unknown case kind"):
        open_upload_case(tmp_path / "cases", [madeup_out / "MU01_conf.pdf"], "250101", "invoice", NOW)


# --- a PO read with a layout from the settings ---

def confirmation_of(po, path, prices: dict[str, str] | None = None):
    """A made-up supplier's confirmation of the stored PO version `po`, drawn at `path` in the layout the parser reads as table_nl:
    the PO's currency and Incoterm, and each of its lines with the texts the PO prints, the price of a line in `prices` (line id ->
    text) replaced. The Per column stays empty: this supplier prints no price basis."""
    doc = po.document
    rows = [Row([Text(0, po.supplier, BOLD, 14)]), Row([Text(0, "Orderbevestiging", BOLD, 12)]),
            Row([Text(0, f"Uw inkooporder {po.po_number}")]), Row([Text(0, "Datum: 05-11-2026")]),
            Row([Text(0, f"Valuta: {doc.currency.text}")]), Row([Text(0, f"Leveringsvoorwaarden: {doc.incoterm.text}")]),
            Row([Text(column.x, column.title, BOLD) for column in TABLE_NL_COLUMNS], gap=GAP, rule=True)]
    for line in doc.lines:
        texts = {"line_id": line.line_id, "item_code": line.item_code.text, "description": line.description.text,
                 "quantity": line.quantity.text, "unit": line.unit.text, "delivery_date": line.delivery_date.text,
                 "unit_price": (prices or {}).get(line.line_id, line.unit_price.text)}
        rows.append(Row([Text(column.x, texts[column.field]) for column in TABLE_NL_COLUMNS if column.field in texts]))
    path.write_bytes(pdf_bytes([rows], path.name))
    return path


def test_a_confirmation_without_a_basis_is_compared_with_a_po_whose_prices_are_per_piece(tmp_path, madeup_out):
    """The second made-up PO prints no price basis and is filed with its layout, which says that every price is per one unit. Its lines
    keep an empty price basis, so the prices of a confirmation that prints none are compared: the one that differs within the
    thresholds is taken over (R04, which counts a line without a basis as per one) and the case closes. The total is known, so the
    PO is known not to be above the authorization. A made-up PO shows the other side: its lines print their basis, and the prices of
    a confirmation without one are then left uncompared, which is what a basis written into the lines would do to every case."""
    d = deps(tmp_path, madeup_out)
    po = d.store.add(ANDERS_PO, ANDERS_LAYOUT)
    assert (po.total, po.above_authorization) == (Decimal("4417.85"), False) and all(line.price_basis.missing for line in po.document.lines)
    document = confirmation_of(po, tmp_path / "anders_conf.pdf", prices={"1": "12,70"})     # 12,50 on the PO: 1.6 percent, 2,00 on the line
    c = open_upload_case(d.settings.cases_root, [document], po.po_number, "confirmation", NOW)
    r = run_case(c, d)
    assert r.status == "closed" and r.error is None
    assert (r.version.version, r.version.reason, r.supplier) == (1, "single version", "Proefleverancier S23")
    assert r.findings.uncompared == []
    assert [(f.field, f.po_line_ids, f.po_value.value, f.doc_value.value, f.detail) for f in r.findings.flags] == [
        ("price", ["1"], "12.50", "12.70", "different")]
    assert [m.po_line_ids for m in r.findings.matches if m.field == "price"] == [[str(n)] for n in range(2, 13)]     # eleven prices agree
    assert len(r.findings.matches) == 60                                         # five items a line but for one price, and the Incoterm
    assert [(a.rule_id, a.route) for a in r.actions] == [("R04", "AUTO"), ("R01", "AUTO")] and r.review_items == [] and r.messages == []
    assert [(e.op.kind, e.op.line_id, e.previous, e.new) for e in r.write_entries[:2]] == [
        ("po_line_price", "1", "12.50", "12.70"), ("article_price", None, None, "12.70")]
    context = c.read_step("route")["context"]
    assert (context["po_basis"], context["po_above_authorization"], context["po_item_code"]["1"]) == ({}, False, "KM-2301")
    assert c.read_step("po_versions")["versions"][0]["document"]["reader"] == "po_pdf:layout"

    made_up = d.store.versions("250108")[0]                                      # a made-up PO: every line prints its basis
    assert [line.price_basis.value for line in made_up.document.lines] == ["1", "1"]
    c2 = open_upload_case(d.settings.cases_root, [confirmation_of(made_up, tmp_path / "250108_conf.pdf")], "250108", "confirmation", NOW)
    r2 = run_case(c2, d)
    assert [(u.field, u.po_line_ids, u.reason) for u in r2.findings.uncompared] == [
        ("price", ["1"], "unknown price basis"), ("price", ["2"], "unknown price basis")]
    assert r2.status == "review" and r2.write_entries == []


# --- Task 43: the values entered in Exact count in the next comparison ---

FILED = datetime(2026, 11, 1, 9, 0)                  # when the store filed its POs in these tests: before every case and every tick
TICKED = NOW + timedelta(hours=1)                    # when the buyer ticked a change as entered in Exact
ENTERED_STEPS = sorted([*STEP_FILES, "03_entered.json"])
AUDIT_STEPS = ["po_versions", "read_document", "version", "match", "compare", "route", "execute", "result"]


def with_list(tmp_path, madeup_out, settings=None):
    """deps() as the runner builds its Deps: the prepared writer into <cases_root>/prepared, where the pipeline reads the list "In
    Exact invoeren" and its marks. Every version of the store is filed at FILED, before every case and every tick, so that no test
    rests on the day it runs."""
    d = deps(tmp_path, madeup_out, writer=PreparedWriter(tmp_path / "cases" / "prepared"), settings=settings)
    for stored in d.store.root.glob("*/*/po.json"):
        os.utime(stored, (FILED.timestamp(), FILED.timestamp()))
    return d


def ticked(d, case, kind: str = "po_line_price", at: datetime = TICKED, what: str = "entered") -> list[str]:
    """The open changes of this kind that the case asked, marked as the buyer marks them on the tab, at `at`: their entry ids."""
    prepared = d.settings.cases_root / "prepared"
    ids = [entry_id for change in open_changes(prepared) if change.kind == kind and case.case_id in change.case_ids for entry_id in change.ids]
    assert ids and mark(prepared, ids, what, at) == len(ids)
    return ids


def again(d, document: Path, po_number: str, now: datetime, kind: str = "confirmation"):
    """A case opened from this document and run: the case and its result."""
    c = open_upload_case(d.settings.cases_root, [document], po_number, kind, now)
    return c, run_case(c, d)


def price_flags(result) -> list[tuple]:
    return [(f.po_line_ids, f.po_value.value, f.doc_value.value) for f in result.findings.flags if f.field == "price"]


def step_names(c) -> list[str]:
    return sorted(path.name for path in (c.root / "steps").iterdir())


def test_a_second_confirmation_with_the_entered_price_has_no_price_flag(tmp_path, madeup_out):
    """MU08 confirms 12.70 where the order prints 12.50, within the thresholds: the rules prepare the change. While it is only
    asked, a second confirmation with that price is compared with the order as printed, as before this task. Once the buyer has
    entered the price and ticked it, the next confirmation is compared with 12.70: no price flag, no change prepared again, and
    step 03_entered of the case, which is a step of the audit record too, names the value that took the printed one's place. Step
    01 keeps the version as the store holds it."""
    d = with_list(tmp_path, madeup_out)
    prepared = d.settings.cases_root / "prepared"
    first, r1 = run(d, madeup_out, "MU08", "250108")
    assert (r1.status, price_flags(r1)) == ("closed", [(["1"], "12.50", "12.70")]) and step_names(first) == sorted(STEP_FILES)
    asked, r2 = again(d, confirmation_again(tmp_path / "MU08_asked.pdf", "MU08"), "250108", LATER)
    assert price_flags(r2) == [(["1"], "12.50", "12.70")] and step_names(asked) == sorted(STEP_FILES)       # not ticked: as printed
    assert [step["name"] for step in audit(asked)["steps"]] == AUDIT_STEPS and "entered" not in audit(asked)["timings"]
    assert len(ticked(d, first)) == 2                                            # one change, asked by both cases
    second, r3 = again(d, confirmation_again(tmp_path / "MU08_second.pdf", "MU08"), "250108", LATER + timedelta(minutes=1))
    assert (r3.status, r3.findings.flags, r3.findings.uncompared, r3.review_items) == ("closed", [], [], [])
    assert ("price", ["1"], "12.70") in [(m.field, m.po_line_ids, m.value) for m in r3.findings.matches]
    assert [(a.rule_id, a.route) for a in r3.actions] == [("R01", "AUTO")]
    assert [e.op.kind for e in r3.write_entries] == ["attach_document", "set_status"]
    record = {"version": 1, "replaced": [{"line_id": "1", "field": "price", "was": "12.50", "entered": "12.70",
                                          "marked_at": TICKED.isoformat(), "by": "buyer"}]}
    assert second.read_step("entered") == record and step_names(second) == ENTERED_STEPS
    recorded = audit(second)
    assert [step["name"] for step in recorded["steps"]] == [*AUDIT_STEPS[:3], "entered", *AUDIT_STEPS[3:]]
    assert recorded["steps"][3]["payload"] == record and "entered" in recorded["timings"]
    [stored] = d.store.versions("250108")
    assert [PoVersion.model_validate(v) for v in second.read_step("po_versions")["versions"]] == [stored]
    assert stored.document.lines[0].unit_price.value == "12.50" and second.read_step("route")["context"]["version"] == 1
    assert [(change.kind, change.state, len(change.ids)) for change in changes(prepared)[0]] == [
        ("po_line_price", "entered", 2), ("article_price", "open", 2)]            # nothing asked a third time


def test_one_that_returns_to_the_old_price_is_flagged_against_the_entered_price(tmp_path, madeup_out):
    """After the buyer entered 12.70, a confirmation that names the old 12.50 again differs from what Exact holds: the price flag
    has the entered price as the order's value, without a text or a source, and the rules, within the thresholds, prepare the
    change from the entered price back to the old one."""
    d = with_list(tmp_path, madeup_out)
    first, _ = run(d, madeup_out, "MU08", "250108")
    ticked(d, first)
    back, r = again(d, confirmation_again(tmp_path / "MU08_back.pdf", "MU08", prices={"1": "12.50"}), "250108", LATER)
    assert [(f.field, f.po_line_ids, f.detail) for f in r.findings.flags] == [("price", ["1"], "different")]
    assert (r.findings.flags[0].po_value, r.findings.flags[0].doc_value.value) == (DocValue(value="12.70"), "12.50")
    assert [(a.rule_id, a.route) for a in r.actions] == [("R04", "AUTO"), ("R01", "AUTO")] and r.status == "closed"
    assert [(e.op.kind, e.op.old_value, e.op.new_value) for e in r.write_entries[:2]] == [
        ("po_line_price", "12.70", "12.50"), ("article_price", None, "12.50")]
    [asked] = [change for change in open_changes(d.settings.cases_root / "prepared") if change.kind == "po_line_price"]
    assert (asked.old_value, asked.new_value, asked.case_ids) == ("12.70", "12.50", [back.case_id])
    assert back.read_step("entered")["replaced"][0]["entered"] == "12.70"


def test_a_newer_version_is_compared_as_it_is(tmp_path, madeup_out):
    """Rule 3: Exact printed the order again after the buyer entered and ticked the price, as version 2 with that price. A
    confirmation that quotes version 2 is compared with it as it is: nothing is laid over it, and the case has no step
    03_entered. A late confirmation that quotes version 1 is compared with version 1 and the entered price. Had version 2, with
    yet another price, come in before the tick, the tick would count for it too, and step 03_entered names that version."""
    d = with_list(tmp_path, madeup_out)
    first, _ = run(d, madeup_out, "MU08", "250108")
    ticked(d, first)
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.70"}))
    filed_on(d.store, "250108", "v2", TICKED + timedelta(hours=1))
    newer, r = again(d, confirmation_again(tmp_path / "MU08_v2.pdf", "MU08", prices={"1": "12.70"}, quoted=2), "250108", LATER)
    assert (r.version.version, r.version.reason, r.findings.flags, r.status) == (2, "quotes version 2", [], "closed")
    assert step_names(newer) == sorted(STEP_FILES)
    late, r = again(d, confirmation_again(tmp_path / "MU08_v1.pdf", "MU08", quoted=1), "250108", LATER + timedelta(minutes=2))
    assert (r.version.version, r.findings.flags) == (1, []) and late.read_step("entered")["replaced"][0]["entered"] == "12.70"
    assert late.read_step("entered")["version"] == 1
    other = with_list(tmp_path / "before the tick", madeup_out)
    first, _ = run(other, madeup_out, "MU08", "250108")
    other.store.add(newer_po(tmp_path / "MU08_po_v2_other.pdf", "MU08", 2, {"1": "12.80"}))
    filed_on(other.store, "250108", "v2", TICKED - timedelta(minutes=30))         # version 2 came in before the tick
    ticked(other, first)
    before, r = again(other, confirmation_again(tmp_path / "MU08_before.pdf", "MU08", quoted=2), "250108", LATER + timedelta(minutes=3))
    assert r.findings.flags == [] and step_names(before) == ENTERED_STEPS
    assert before.read_step("entered") == {"version": 2, "replaced": [{"line_id": "1", "field": "price", "was": "12.80", "entered": "12.70",
                                                                         "marked_at": TICKED.isoformat(), "by": "buyer"}]}


def test_a_newer_print_that_contradicts_a_tick_ends_it(tmp_path, madeup_out):
    """The review round, W9 (findings 9 and 10): the buyer ticks 12.70 without entering it, and Exact prints the order again with
    the old 12.50, filed after the tick. The supplier's second confirmation at 12,70, which quotes the new print, is flagged against
    it and the rules prepare the change in the ordinary way; its row stands on the list as open, with the row of the first case,
    so that no case closes as confirmed on a tick that the newest print contradicts. A late confirmation that quotes version 1 is
    compared with version 1 as it was printed too: the value is laid over no version. Once the buyer has entered the price and
    ticks again, that tick counts."""
    d = with_list(tmp_path, madeup_out)
    prepared = d.settings.cases_root / "prepared"
    first, _ = run(d, madeup_out, "MU08", "250108")
    ids = ticked(d, first)
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.50"}))
    filed_on(d.store, "250108", "v2", TICKED + timedelta(hours=1))
    second, r = again(d, confirmation_again(tmp_path / "MU08_v2.pdf", "MU08", quoted=2), "250108", LATER)
    assert (r.version.version, price_flags(r), r.status) == (2, [(["1"], "12.50", "12.70")], "closed")
    assert [(a.rule_id, a.route) for a in r.actions] == [("R04", "AUTO"), ("R01", "AUTO")] and step_names(second) == sorted(STEP_FILES)
    [asked] = [change for change in changes(prepared)[0] if change.kind == "po_line_price"]
    assert (asked.state, asked.case_ids, asked.ids[:1]) == ("open", [first.case_id, second.case_id], ids)       # on the list again
    late, r = again(d, confirmation_again(tmp_path / "MU08_v1.pdf", "MU08", quoted=1), "250108", LATER + timedelta(minutes=1))
    assert (r.version.version, price_flags(r)) == (1, [(["1"], "12.50", "12.70")]) and step_names(late) == sorted(STEP_FILES)
    [asked] = [change for change in changes(prepared)[0] if change.kind == "po_line_price"]
    assert mark(prepared, asked.ids, "entered", TICKED + timedelta(hours=2)) == 3   # entered now, and ticked again: three cases asked it
    after, r = again(d, confirmation_again(tmp_path / "MU08_after.pdf", "MU08", quoted=2), "250108", LATER + timedelta(minutes=2))
    assert r.findings.flags == [] and after.read_step("entered")["replaced"][0] | {"marked_at": ""} == {
        "line_id": "1", "field": "price", "was": "12.50", "entered": "12.70", "marked_at": "", "by": "buyer"}


def test_an_export_that_contradicts_a_tick_ends_it_for_a_confirmation_too(tmp_path, madeup_out):
    """W9 (finding 9): a confirmation is compared with the PDF, never with an export. An export filed after the tick that shows
    the old price still ends the tick: the second confirmation at 12,70 is flagged against version 1 as it was printed, and is not
    closed as confirmed on the tick."""
    d = with_list(tmp_path, madeup_out)
    prepared = d.settings.cases_root / "prepared"
    first, _ = run(d, madeup_out, "MU08", "250108")
    ticked(d, first)
    export = tmp_path / "export_after.csv"
    export.write_text("po_number,line_id,item_code,description,quantity,unit,unit_price,price_basis,delivery_date\n"
                      "250108,1,KM-0801,Manometer 0-10 bar,3,st,12.50,1,2026-11-16\n250108,2,KM-0802,Zeskantbout M10 x 40,50,st,0.42,1,2026-11-16\n",
                      encoding="utf-8")
    assert d.store.from_export(export, {name: name for name in export.read_text(encoding="utf-8").splitlines()[0].split(",")})
    filed_on(d.store, "250108", "e1", TICKED + timedelta(hours=1))
    second, r = again(d, confirmation_again(tmp_path / "MU08_again.pdf", "MU08"), "250108", LATER)
    assert (r.version.version, price_flags(r)) == (1, [(["1"], "12.50", "12.70")]) and step_names(second) == sorted(STEP_FILES)
    [asked] = [change for change in changes(prepared)[0] if change.kind == "po_line_price"]
    assert asked.state == "open" and len(asked.ids) == 2
    reopened = [json.loads(line) for line in (prepared / MARKS_FILE).read_text(encoding="utf-8").splitlines()][-1]
    assert (reopened["mark"], reopened["by"]) == ("reopened", "export")


def test_select_version_gets_the_same_versions(tmp_path, madeup_out, monkeypatch):
    """Rule 1: with entered values on the list, select_version is given what the store gives, as before this task: every PDF
    version for a confirmation and the PO as it stands alone for a cart, each as the store holds it, without an entered value."""
    given = []

    def spy(doc, versions, decider):
        given.append(list(versions))
        return versioning.select_version(doc, versions, decider)
    monkeypatch.setattr("aid.pipeline.select_version", spy)
    d = with_list(tmp_path, madeup_out)
    first, _ = run(d, madeup_out, "MU08", "250108")
    ticked(d, first)
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.80"}))
    filed_on(d.store, "250108", "v2", FILED)
    both = d.store.versions("250108")
    second, r = again(d, confirmation_again(tmp_path / "MU08_v1.pdf", "MU08", quoted=1), "250108", LATER)
    assert given[-1] == both and [version.document.lines[0].unit_price.value for version in given[-1]] == ["12.50", "12.80"]
    assert r.findings.flags == [] and len(second.read_step("entered")["replaced"]) == 1                     # the entered value counted
    [look] = d.store.from_export(madeup_out / "CART01_export.csv", EXPORT_COLUMNS)
    filed_on(d.store, "250201", "e1", FILED)
    page = tmp_path / "CART01.html"
    page.write_text((madeup_out / "CART01.html").read_text(encoding="utf-8").replace("€ 4,10", "€ 4,15"), encoding="utf-8")
    cart, r = again(d, page, "250201", LATER + timedelta(minutes=1), kind="cart")
    assert price_flags(r) == [(["2"], "4.10", "4.15")]
    ticked(d, cart)
    shown = tmp_path / "shown again" / "CART01.html"
    shown.parent.mkdir()
    shown.write_text(page.read_text(encoding="utf-8") + "<!-- shown again -->", encoding="utf-8")
    cart2, r = again(d, shown, "250201", LATER + timedelta(minutes=2), kind="cart")
    assert given[-1] == given[-2] == d.store.candidates("250201", "cart") == [look]
    assert given[-1][0].document.lines[1].unit_price.value == "4.10"
    assert r.findings.flags == [] and cart2.read_step("entered")["replaced"][0]["entered"] == "4.15"


def test_a_cart_is_compared_through_the_same_step(tmp_path, madeup_out):
    """Rule 7: a cart is checked against its order as it stands, a look from an export, with the entered values laid over it. After
    the buyer entered the price that the rules prepared for a cart, the same cart shown again has no price flag; a look from an
    export filed after the tick is taken as it is."""
    d = with_list(tmp_path, madeup_out)
    d.store.from_export(madeup_out / "CART01_export.csv", EXPORT_COLUMNS)
    filed_on(d.store, "250201", "e1", FILED)
    page = tmp_path / "CART01.html"
    page.write_text((madeup_out / "CART01.html").read_text(encoding="utf-8").replace("€ 4,10", "€ 4,15"), encoding="utf-8")
    first, r = again(d, page, "250201", NOW, kind="cart")
    assert (r.status, price_flags(r), [(a.rule_id, a.route) for a in r.actions]) == ("closed", [(["2"], "4.10", "4.15")], [("R04", "AUTO")])
    ticked(d, first)
    second, r = again(d, page, "250201", LATER, kind="cart")
    assert (r.status, r.findings.flags, [(a.rule_id, a.reason) for a in r.actions], r.write_entries) == (
        "closed", [], [("R01", "no difference")], [])
    assert second.read_step("entered") == {"version": 1, "replaced": [{"line_id": "2", "field": "price", "was": "4.10", "entered": "4.15",
                                                                      "marked_at": TICKED.isoformat(), "by": "buyer"}]}
    export = tmp_path / "CART01_export.csv"                                       # a newer export, in which the line holds 4.20
    export.write_text((madeup_out / "CART01_export.csv").read_text(encoding="utf-8").replace("4.10", "4.20"), encoding="utf-8")
    d.store.from_export(export, EXPORT_COLUMNS)
    filed_on(d.store, "250201", "e2", TICKED + timedelta(hours=1))
    third, r = again(d, page, "250201", LATER + timedelta(minutes=1), kind="cart")
    assert price_flags(r) == [(["2"], "4.20", "4.15")] and step_names(third) == sorted(STEP_FILES)


def test_a_later_change_starts_from_the_entered_value(tmp_path, madeup_out):
    """Amendment 6: what the pipeline does after the comparison sees an entered value as the order's. A third confirmation with yet
    another price prepares the change from the entered price to the new one, and its undo puts the entered price back. The total and
    the authorization are worked out with the entered price: the limit lies between the printed total (58.50) and the total with
    the entered price (59.10), so the change of the third confirmation asks the approver, and the request names it from the
    entered price. Against the order as printed the same confirmation would be above the price threshold."""
    limited = SETTINGS.model_copy(update={"authorization_limit": Decimal("58.80")})
    d = with_list(tmp_path, madeup_out, settings=limited)
    [stored] = d.store.versions("250108")
    assert (stored.total, stored.above_authorization) == (Decimal("58.50"), False)
    first, r1 = run(d, madeup_out, "MU08", "250108")
    assert [(a.rule_id, a.route) for a in r1.actions] == [("R04", "AUTO"), ("R01", "AUTO")] and r1.messages == []
    ticked(d, first)
    third, r = again(d, confirmation_again(tmp_path / "MU08_third.pdf", "MU08", prices={"1": "12.90"}), "250108", LATER)
    assert price_flags(r) == [(["1"], "12.70", "12.90")]
    assert [(a.rule_id, a.route, a.base_rule_id) for a in r.actions] == [("R10", "AUTO", "R04"), ("R01", "AUTO", None)]
    price, item = r.write_entries[:2]
    assert (price.op.kind, price.op.old_value, price.op.new_value, price.previous) == ("po_line_price", "12.70", "12.90", "12.70")
    assert (item.op.kind, item.op.new_value) == ("article_price", "12.90")
    context = third.read_step("route")["context"]
    assert context["po_above_authorization"] is True and context["po_basis"] == {"1": "1", "2": "1"}
    [request] = r.messages
    assert (request["template"], request["rule_id"]) == ("reapproval", "R10")
    assert request["items"][0] == {"kind": "po_line_price", "line_id": "1", "article_code": "", "old_value": "12.70", "new_value": "12.90"}
    back = undo_entry(third.root, price.entry_id, d.writer, LATER)
    assert (back.op.old_value, back.op.new_value) == ("12.90", "12.70")           # the undo puts the entered price back
    printed = tmp_path / "as printed"                                              # the same documents with nothing entered
    plain = with_list(printed, madeup_out, settings=limited)
    run(plain, madeup_out, "MU08", "250108")
    _, r = again(plain, tmp_path / "MU08_third.pdf", "250108", LATER)
    assert price_flags(r) == [(["1"], "12.50", "12.90")] and [(a.rule_id, a.route) for a in r.actions] == [("R05", "REVIEW")]


def held_file(name: str, error: type[OSError] = PermissionError):
    """pathlib's read_bytes for a time in which the file of the list with this name cannot be read: a PermissionError, as Windows
    gives for a file that another program holds, or another OSError."""
    real = Path.read_bytes

    def read_bytes(self):
        if self.name == name:
            raise error(13, "The process cannot access the file because it is being used by another process", str(self))
        return real(self)
    return read_bytes


@pytest.mark.parametrize("name, error", [(PREPARED_FILE, PermissionError), (MARKS_FILE, PermissionError), (MARKS_FILE, OSError)])
def test_a_case_run_while_the_list_cannot_be_read_goes_to_the_buyer(tmp_path, madeup_out, monkeypatch, name, error):
    """Amendment 5 and the review round's W6: while prepared_changes.csv or marks.jsonl cannot be read (a PermissionError, or any
    other OSError), the case is compared with the version as it is, and step 03_entered says so: {"version": n, "replaced": [],
    "unread": true}, a step of the audit record too. The aid then does not know what Exact holds, so the rules prepare nothing,
    request nothing and send nothing: one REVIEW action of the pipeline's own rule L01 covers the whole case, and every difference is
    the buyer's to decide. Once the list can be read again, the next case counts the entered value."""
    d = with_list(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"authorization_limit": Decimal("50")}))
    prepared = d.settings.cases_root / "prepared"
    first, _ = run(d, madeup_out, "MU08", "250108")
    ticked(d, first)
    rows = (prepared / PREPARED_FILE).read_bytes()
    with monkeypatch.context() as held:
        held.setattr(Path, "read_bytes", held_file(name, error))
        blind, r = again(d, confirmation_again(tmp_path / "MU08_blind.pdf", "MU08"), "250108", LATER)
        back, old = again(d, confirmation_again(tmp_path / "MU08_back.pdf", "MU08", prices={"1": "12.50"}), "250108", LATER + timedelta(seconds=30))
    assert (r.status, price_flags(r)) == ("review", [(["1"], "12.50", "12.70")])
    assert [(a.rule_id, a.route, a.finding_ids, a.reason) for a in r.actions] == [("L01", "REVIEW", ["F1"], UNREAD_LIST)]
    assert (r.write_entries, r.messages) == ([], []) and (prepared / PREPARED_FILE).read_bytes() == rows
    assert [(item.finding_id, item.reason, item.field) for item in r.review_items] == [("F1", UNREAD_LIST, "price")]
    assert blind.read_step("entered") == {"version": 1, "replaced": [], "unread": True} and step_names(blind) == ENTERED_STEPS
    assert audit(blind)["steps"][3] == {"name": "entered", "seconds": audit(blind)["steps"][3]["seconds"],
                                         "payload": {"version": 1, "replaced": [], "unread": True}}
    assert [decision["rule_id"] for decision in audit(blind)["decisions"]] == ["L01"]
    # the confirmation that returns to the printed 12,50 has no difference against the order as filed: it still goes to the buyer
    assert (old.status, old.findings.flags, old.write_entries) == ("review", [], [])
    assert [(item.finding_id, item.reason) for item in old.review_items] == [(None, UNREAD_LIST)]
    seen, r = again(d, confirmation_again(tmp_path / "MU08_seen.pdf", "MU08"), "250108", LATER + timedelta(minutes=1))
    assert r.findings.flags == [] and seen.read_step("entered")["replaced"][0]["entered"] == "12.70"
    assert "unread" not in seen.read_step("entered") and r.status == "closed"


def test_a_list_that_cannot_be_understood_sends_the_case_to_the_buyer(tmp_path, madeup_out):
    """W5 (finding 5) with W6: prepared_changes.csv was saved from a spreadsheet with semicolons between its fields. No tick of it
    can be read, so the case is compared with the order as filed and goes to the buyer under L01, as while a program holds the
    file; before, the list counted as empty, the change was prepared again and the approver was asked a second time."""
    d = with_list(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"authorization_limit": Decimal("50")}))
    prepared = d.settings.cases_root / "prepared"
    first, r = run(d, madeup_out, "MU08", "250108")
    assert [a.rule_id for a in r.actions] == ["R10", "R01"]
    ticked(d, first)
    rows = (prepared / PREPARED_FILE).read_text(encoding="utf-8")
    (prepared / PREPARED_FILE).write_text(rows.replace(",", ";"), encoding="utf-8")
    saved = (prepared / PREPARED_FILE).read_bytes()
    second, r = again(d, confirmation_again(tmp_path / "MU08_again.pdf", "MU08"), "250108", LATER)
    assert (r.status, [a.rule_id for a in r.actions], r.write_entries, r.messages) == ("review", ["L01"], [], [])
    assert second.read_step("entered")["unread"] is True and (prepared / PREPARED_FILE).read_bytes() == saved


def test_a_guessed_order_and_an_unread_list_are_both_told(tmp_path, madeup_out, monkeypatch):
    """W6 beside the rule of a guessed order: a case whose PO the aid guessed, run while the list cannot be read, has the action
    of G01 for its findings and one of L01 for the whole case, so that the buyer reads both reasons."""
    d = with_list(tmp_path, madeup_out)
    first, _ = run(d, madeup_out, "MU08", "250108")
    ticked(d, first)
    c = open_upload_case(d.settings.cases_root, [confirmation_again(tmp_path / "MU08_guess.pdf", "MU08")], "250108", "confirmation", LATER)
    meta = json.loads((c.root / "case.json").read_text(encoding="utf-8")) | {"po_found_by": "held number"}
    (c.root / "case.json").write_text(json.dumps(meta), encoding="utf-8")
    with monkeypatch.context() as held:
        held.setattr(Path, "read_bytes", held_file(MARKS_FILE))
        r = run_case(load_case(d.settings.cases_root, c.case_id), d)
    assert [(a.rule_id, a.finding_ids) for a in r.actions] == [("G01", ["F1"]), ("L01", [])]
    assert [(item.finding_id, item.reason) for item in r.review_items] == [("F1", GUESSED_PO), (None, UNREAD_LIST)]


def test_a_step_file_of_an_earlier_attempt_is_removed(tmp_path, madeup_out, monkeypatch):
    """W8 (findings 8 and 11): the run of a second confirmation is cut off after step 03_entered; the buyer takes the tick back;
    the case is run again, as the runner resumes one without a result. It is compared with the order as filed, and the step file
    of the first attempt is gone, so that the review tab and the audit record say the same. An "unread" step goes the same way."""
    d = with_list(tmp_path, madeup_out)
    prepared = d.settings.cases_root / "prepared"
    first, _ = run(d, madeup_out, "MU08", "250108")
    ids = ticked(d, first)
    c = open_upload_case(d.settings.cases_root, [confirmation_again(tmp_path / "MU08_cut.pdf", "MU08")], "250108", "confirmation", LATER)

    def stopped(*args, **kwargs):
        raise KeyboardInterrupt("the runner is stopped while the case runs")
    with monkeypatch.context() as cut, pytest.raises(KeyboardInterrupt):
        cut.setattr(pipeline, "match_lines", stopped)
        run_case(c, d)
    assert step_names(c) == ["01_po_versions.json", "02_read_document.json", "03_entered.json", "03_version.json"]
    assert reopen(prepared, ids, LATER) == "open"
    r = run_case(load_case(d.settings.cases_root, c.case_id), d)
    assert price_flags(r) == [(["1"], "12.50", "12.70")] and step_names(c) == sorted(STEP_FILES)
    assert "entered" not in [step["name"] for step in audit(c)["steps"]]
    blind = open_upload_case(d.settings.cases_root, [confirmation_again(tmp_path / "MU08_held.pdf", "MU08")], "250108", "confirmation",
                             LATER + timedelta(minutes=1))
    with monkeypatch.context() as held, pytest.raises(KeyboardInterrupt):
        held.setattr(Path, "read_bytes", held_file(MARKS_FILE))
        held.setattr(pipeline, "match_lines", stopped)
        run_case(blind, d)
    assert blind.read_step("entered")["unread"] is True
    r = run_case(load_case(d.settings.cases_root, blind.case_id), d)
    assert "03_entered.json" not in step_names(blind) and [a.rule_id for a in r.actions] != ["L01"]


def test_a_change_the_supplier_withdrew_leaves_the_list(tmp_path, madeup_out):
    """W4 (finding 4): 12.60 is entered and ticked; a newer confirmation names 12.70, whose change stands open; the newest one
    names 12.60 again. Its case has no difference, and it takes the open change off the list as replaced: nothing is left to enter
    for the line. The change of a confirmation that the buyer still has to look at stays (a guessed order), and when the supplier
    names 12.70 once more, the change is asked again."""
    d = with_list(tmp_path, madeup_out)
    prepared = d.settings.cases_root / "prepared"

    def line_prices() -> list[tuple]:
        return [(change.old_value, change.new_value, change.state, len(change.ids)) for change in changes(prepared)[0] if change.kind == "po_line_price"]
    first, _ = again(d, confirmation_again(tmp_path / "MU08_1260.pdf", "MU08", prices={"1": "12.60"}), "250108", NOW)
    ticked(d, first)
    again(d, confirmation_again(tmp_path / "MU08_1270.pdf", "MU08"), "250108", LATER)
    assert line_prices() == [("12.50", "12.60", "entered", 1), ("12.60", "12.70", "open", 1)]
    guess = open_upload_case(d.settings.cases_root, [confirmation_again(tmp_path / "MU08_guess.pdf", "MU08", prices={"1": "12.60"})], "250108",
                             "confirmation", LATER + timedelta(minutes=1))
    meta = json.loads((guess.root / "case.json").read_text(encoding="utf-8")) | {"po_found_by": "held number"}
    (guess.root / "case.json").write_text(json.dumps(meta), encoding="utf-8")
    run_case(load_case(d.settings.cases_root, guess.case_id), d)
    assert line_prices()[1][2] == "open"                                           # the aid's guess takes nothing off the list
    back, r = again(d, confirmation_again(tmp_path / "MU08_back.pdf", "MU08", prices={"1": "12.60"}), "250108", LATER + timedelta(minutes=2))
    assert (r.status, r.findings.flags, r.write_entries[0].op.kind) == ("closed", [], "attach_document")
    assert line_prices() == [("12.50", "12.60", "entered", 1), ("12.60", "12.70", "replaced", 1)]
    assert [change for change in open_changes(prepared) if change.kind == "po_line_price"] == []
    last = [json.loads(line) for line in (prepared / MARKS_FILE).read_text(encoding="utf-8").splitlines()][-1]
    assert (last["mark"], last["by"]) == ("replaced", f"case {back.case_id}")
    again(d, confirmation_again(tmp_path / "MU08_1270_again.pdf", "MU08"), "250108", LATER + timedelta(minutes=3))
    assert line_prices() == [("12.50", "12.60", "entered", 1), ("12.60", "12.70", "open", 2)]


def never_counting(d, tmp_path) -> None:
    """Rows and marks on the list of d that count in no supplier's case of PO 250108 (rule 4, amendment 4): an open change of its
    line 1 and one that it replaced, a dropped one, one undone before it was entered, an entered article price, an entered change
    of another PO, and the entered change of a case made from a try-out."""
    prepared = d.settings.cases_root / "prepared"
    writer, other = PreparedWriter(prepared), "250108_20261109T090000"

    def asked(case_id: str, op: WriteOp) -> str:
        return writer.apply(op, case_id, "R04", {}, NOW - timedelta(days=1)).entry_id
    asked(other, WriteOp(kind="po_line_price", po_number="250108", version=1, line_id="1", old_value="12.50", new_value="12.60"))
    asked(other, WriteOp(kind="po_line_price", po_number="250108", version=1, line_id="1", old_value="12.50", new_value="12.65"))
    dropped = asked(other, WriteOp(kind="po_line_quantity", po_number="250108", version=1, line_id="2", old_value="50", new_value="60"))
    article = asked(other, WriteOp(kind="article_price", po_number="250108", version=1, article_code="KM-0802", new_value="0.45"))
    elsewhere = asked("250107_20261110T090000", WriteOp(kind="po_line_price", po_number="250107", version=1, line_id="1",
                                                        old_value="12.50", new_value="12.70"))
    cancelled = tmp_path / "cancelled case"
    cancelled.mkdir()
    entry = writer.apply(WriteOp(kind="po_line_date", po_number="250108", version=1, line_id="2", old_value="2026-11-16",
                                 new_value="2026-11-20"), other, "R02", {}, NOW - timedelta(days=1))
    WriteLog(cancelled).append(entry)
    undo_entry(cancelled, entry.entry_id, writer, NOW - timedelta(days=1))
    [stored] = d.store.versions("250108")
    document = tmp_path / "PROEF orderbevestiging.pdf"
    document.write_bytes(tryout_pdf(stored, "none", NOW))
    tried = open_upload_case(d.settings.cases_root, [document], "250108", "confirmation", NOW - timedelta(days=2))
    made_up = asked(tried.case_id, WriteOp(kind="po_line_price", po_number="250108", version=1, line_id="2", old_value="0.42",
                                           new_value="0.43"))
    assert mark(prepared, [dropped], "dropped", TICKED - timedelta(days=1)) == 1
    assert mark(prepared, [article, elsewhere, made_up], "entered", TICKED - timedelta(days=1)) == 3
    assert sorted((change.state, change.tryout) for change in changes(prepared)[0]) == [
        ("dropped", False), ("entered", False), ("entered", False), ("entered", True), ("open", False), ("replaced", False),
        ("undone", False), ("undone", False)]


def test_without_marks_every_step_file_is_as_before(tmp_path, madeup_out):
    """Rule 2, on a case of its own beside the golden comparison of the whole made-up batch above: with nothing on the list that
    counts for its order, a case writes the step files, the result and the audit record that it writes with an empty list: no
    step 03_entered and no new key. Here the list of the second world holds rows and marks that never count for a supplier's
    case of PO 250108 (rule 4): open, replaced, dropped and undone changes, an article price, the change of another PO and the
    entered change of a try-out."""
    worlds = {}
    for name in ("empty list", "marks that never count"):
        d = with_list(tmp_path / name, madeup_out)
        if name != "empty list":
            never_counting(d, tmp_path / name)
        c, r = run(d, madeup_out, "MU08", "250108")
        assert r.status == "closed" and step_names(c) == sorted(STEP_FILES)
        places = [(tmp_path / name, "<RUN>"), (REPO_ROOT, "<REPO>")]
        steps = {path.name: made_up_batch._json(json.loads(path.read_text(encoding="utf-8")), places) for path in (c.root / "steps").iterdir()}
        recorded = audit(c)
        assert [step["name"] for step in recorded["steps"]] == AUDIT_STEPS and list(recorded["timings"]) == [*AUDIT_STEPS, "total", "wall_seconds"]
        recorded["timings"] = dict.fromkeys(recorded["timings"])
        worlds[name] = steps | {"audit.json": made_up_batch._json(recorded, places)}
    assert worlds["empty list"] == worlds["marks that never count"]


def test_a_try_outs_entered_value_reaches_no_suppliers_case(tmp_path, madeup_out):
    """Amendment 4 in the pipeline: a value entered for a change that a try-out asked is laid over the order in a case made from a
    try-out, and never in a supplier's own case, which is compared with the order as printed."""
    d = with_list(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"authorization_limit": None, "test_mode": True}))
    [stored] = d.store.versions("250108")
    document = tmp_path / "PROEF orderbevestiging 1.pdf"
    document.write_bytes(tryout_pdf(stored, "price_small", NOW))
    tried, r = again(d, document, "250108", NOW)
    assert price_flags(r) == [(["1"], "12.50", "12.63")]
    assert [change.tryout for change in open_changes(d.settings.cases_root / "prepared")] == [True, True]
    ticked(d, tried)
    own, r = run(d, madeup_out, "MU08", "250108", now=LATER)                      # the supplier's own confirmation, at 12.70
    assert price_flags(r) == [(["1"], "12.50", "12.70")] and step_names(own) == sorted(STEP_FILES)
    laid = stored.document.lines[0].model_copy(update={"unit_price": DocValue(value="12.63")})
    entered = stored.model_copy(update={"document": stored.document.model_copy(update={"lines": [laid, *stored.document.lines[1:]]})})
    second = tmp_path / "PROEF orderbevestiging 2.pdf"
    second.write_bytes(tryout_pdf(entered, "none", LATER))                        # a second try-out, of the order with the entered price
    next_tried, r = again(d, second, "250108", LATER + timedelta(minutes=1))
    assert r.findings.flags == [] and next_tried.read_step("entered")["replaced"] == [
        {"line_id": "1", "field": "price", "was": "12.50", "entered": "12.63", "marked_at": TICKED.isoformat(), "by": "buyer"}]


# --- the review round of Task 43, W1: an entered value belongs to a line of the order, not to a line id ---

ORDER = "250155"                                     # a made-up order of its own, drawn by the made-up generator
ORDER_LINES = {"a": ("KM-5501", "Manometer 0-10 bar", "3", "12.50", "1"), "b": ("KM-5502", "Zeskantbout M10 x 40", "500", "4.20", "100"),
               "c": ("KM-5503", "Kogelkraan DN20", "4", "38.75", "1"),
               "d": ("KM-5504", "Pakking eerste levering", "2", "7.00", "1"),       # two deliveries of one article: d and e
               "e": ("KM-5504", "Pakking tweede levering", "6", "7.00", "1")}
ORDER_DATES = {"e": "2026-11-23"}                    # the delivery date of a line; every other line has 16 November


def order_values(key: str, prices: dict[str, str], dates: dict[str, str] | None = None, codes: dict[str, str] | None = None) -> dict:
    """The values of the line `key` of the made-up order, at its own price, delivery date and item code or the ones given."""
    code, description, quantity, price, basis = ORDER_LINES[key]
    return {"item_code": (codes or {}).get(key, code), "description": description, "quantity": quantity, "unit": "pc",
            "unit_price": prices.get(key, price), "price_basis": basis, "delivery_date": (dates or {}).get(key, ORDER_DATES.get(key, "2026-11-16"))}


def order_spec(keys: str, prices: dict[str, str], version: int = 1, quoted: int | None = None, document: bool = False,
               dates: dict[str, str] | None = None, codes: dict[str, str] | None = None):
    """The made-up order with the lines named by `keys` ("abc"), numbered by their place, each at its own price, delivery date and
    item code or the ones given; with document, a confirmation of those lines that quotes version `quoted`."""
    from datetime import date as day

    from aid.madeup.generate import Confirmation, DocumentLine, MadeUpSpec, Po, PoLine
    from aid.madeup.generate import PoVersion as SpecVersion

    def values(key: str) -> dict:
        return order_values(key, prices, dates, codes)
    po = Po(po_number=ORDER, supplier="S08", currency="EUR", incoterm="DAP Rotterdam", buyer_authorization_exceeded=False,
            versions=[SpecVersion(version=version, version_date=day(2026, 10, 19),
                                  lines=[PoLine(line_id=str(number), **values(key)) for number, key in enumerate(keys, 1)])])
    confirmation = Confirmation(layout="table_nl", language="nl", document_date=day(2026, 10, 21), version_quoted=quoted, incoterm="DAP Rotterdam",
                                lines=[DocumentLine(line_id=str(number), **values(key)) for number, key in enumerate(keys, 1)]) if document else None
    return MadeUpSpec.model_construct(case_id="MU55", po=po, confirmation=confirmation, cart=None)


def order_pdf(path: Path, keys: str, version: int, prices: dict[str, str] | None = None, dates: dict[str, str] | None = None,
              codes: dict[str, str] | None = None) -> Path:
    from aid.madeup import generate
    spec = order_spec(keys, prices or {}, version, dates=dates, codes=codes)
    path.write_bytes(generate.pdf_bytes(generate.po_pages(spec, spec.po.versions[0]), path.name))
    return path


def order_confirmation(path: Path, keys: str, prices: dict[str, str], quoted: int, dates: dict[str, str] | None = None) -> Path:
    from aid.madeup import generate
    path.write_bytes(generate.pdf_bytes(generate.confirmation_pages(order_spec(keys, prices, quoted, quoted, document=True, dates=dates)), path.name))
    return path


def order_export(path: Path, keys: str, prices: dict[str, str] | None = None, dates: dict[str, str] | None = None) -> Path:
    """An export of the made-up order with the lines named by `keys` in that order and no line column, so that the lines are
    numbered by their place, as the export of an overview numbers them."""
    names = ["po_number", "item_code", "description", "quantity", "unit", "unit_price", "price_basis", "delivery_date"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=names)
        writer.writeheader()
        writer.writerows({"po_number": ORDER} | order_values(key, prices or {}, dates) | {"unit": "st"} for key in keys)
    return path


def order_columns(path: Path) -> dict[str, str]:
    """The column map of an export of order_export: each of its titles is the field's own name."""
    return {name: name for name in path.read_text(encoding="utf-8").splitlines()[0].split(",")}


def test_two_prints_numbered_by_place_keep_each_lines_entered_price(tmp_path, madeup_out):
    """W1 (finding 1, the reviewers' sequence "renumbered", the order above the authorization limit): Exact numbers the lines of a
    print by their place. Version 1 holds KM-5501, KM-5502, KM-5503 as 1, 2, 3; after KM-5501 left the order, version 2 holds
    KM-5502 as 1 and KM-5503 as 2. The price of KM-5502 is entered for a confirmation of version 1 (its line 2) and that of
    KM-5503 for one of version 2 (its line 2 too). A confirmation of version 2 with both entered prices has no difference: both
    values are laid over, nothing is prepared again, and the approver is asked nothing more. Before, the tick made last pushed the
    other value out, the change of KM-5502 was prepared again and requested a second time with an old value Exact no longer held."""
    d = with_list(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"authorization_limit": Decimal("100"),
                                                                             "supplier_lead_times": {"S08": 10}}))
    prepared = d.settings.cases_root / "prepared"
    d.store.add(order_pdf(tmp_path / "order_v1.pdf", "abc", 1))
    filed_on(d.store, ORDER, "v1", FILED)
    first, r = again(d, order_confirmation(tmp_path / "x.pdf", "abc", {"b": "4.25"}, 1), ORDER, NOW)
    assert [(a.rule_id, a.route) for a in r.actions] == [("R10", "AUTO"), ("R01", "AUTO")] and len(r.messages) == 1
    d.store.add(order_pdf(tmp_path / "order_v2.pdf", "bc", 2))                      # KM-5501 left the order: Exact prints it again
    filed_on(d.store, ORDER, "v2", NOW + timedelta(minutes=30))
    ticked(d, first)
    second, r = again(d, order_confirmation(tmp_path / "y.pdf", "bc", {"b": "4.25", "c": "39.14"}, 2), ORDER, TICKED + timedelta(minutes=5))
    assert price_flags(r) == [(["2"], "38.75", "39.14")] and len(r.messages) == 1  # KM-5502's 4.25 is known: no flag for it
    assert second.read_step("entered")["replaced"] == [{"line_id": "1", "field": "price", "was": "4.20", "entered": "4.25",
                                                         "marked_at": TICKED.isoformat(), "by": "buyer"}]
    ticked(d, second, at=TICKED + timedelta(minutes=10))
    third, r = again(d, order_confirmation(tmp_path / "z.pdf", "bc", {"b": "4.25", "c": "39.14"}, 2), ORDER, TICKED + timedelta(minutes=15))
    assert (r.status, r.findings.flags, r.messages) == ("closed", [], [])
    assert [e.op.kind for e in r.write_entries if e.op.kind in ("po_line_price", "article_price")] == []       # nothing prepared again
    assert [(one["line_id"], one["entered"]) for one in third.read_step("entered")["replaced"]] == [("1", "4.25"), ("2", "39.14")]
    assert [change.state for change in changes(prepared)[0] if change.kind == "po_line_price"] == ["entered", "entered"]
    assert [change for change in open_changes(prepared) if change.kind == "po_line_price"] == []


# --- the second review round of Task 43: a print that shows nothing new is a look at the order (W16), two deliveries of one
# article are told by their values (W17), a print that cannot be matched ends the tick (W18) ---

MU08_EXPORT = ("po_number,line_id,item_code,description,quantity,unit,unit_price,price_basis,delivery_date\n"
               "250108,1,KM-0801,Manometer 0-10 bar,3,st,12.50,1,2026-11-16\n250108,2,KM-0802,Zeskantbout M10 x 40,50,st,0.42,1,2026-11-16\n")
AFTER_TICK = TICKED + timedelta(hours=1)             # when a print is given after the buyer's tick


def last_mark(prepared: Path) -> dict:
    return [json.loads(line) for line in (prepared / MARKS_FILE).read_text(encoding="utf-8").splitlines()][-1]


def line_changes(prepared: Path, kind: str = "po_line_price") -> list[tuple]:
    """The changes of that kind on the list: (line id, old value, new value, state, number of rows)."""
    return [(change.line_id, change.old_value, change.new_value, change.state, len(change.ids)) for change in changes(prepared)[0] if change.kind == kind]


def order_deps(tmp_path, madeup_out, limit: str | None = None):
    """with_list() for the made-up order: S08 with a lead time, and the authorization limit given."""
    return with_list(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={
        "authorization_limit": Decimal(limit) if limit is not None else None, "supplier_lead_times": {"S08": 10}}))


@pytest.mark.parametrize("how", ["the same file", "another file", "the pdf"])
def test_a_print_that_shows_nothing_new_ends_a_tick_given_without_the_entry(tmp_path, madeup_out, how):
    """W16 (the check's findings 1 and 6, its probe same_rows): the order has a PDF and a look from an export, both at 12.50. The
    rules prepare 12.70 and the buyer ticks it without entering it. Exact is unchanged, so the next export has the rows of the look
    before: the store files nothing, and still it is a look at the order at the time it was given. The supplier's second
    confirmation at 12,70 is flagged against the printed 12.50 and prepared again; it is not closed as confirmed on the tick.
    The same when that export is the very file of the look, and when the PDF that the store holds is given again."""
    d = with_list(tmp_path, madeup_out)
    prepared = d.settings.cases_root / "prepared"
    export = tmp_path / "export.csv"
    export.write_text(MU08_EXPORT, encoding="utf-8")
    columns = {name: name for name in MU08_EXPORT.splitlines()[0].split(",")}
    if how != "the pdf":
        assert d.store.from_export(export, columns)                                   # the daily export, before the tick
        filed_on(d.store, "250108", "e1", FILED + timedelta(hours=1))
    first, _ = run(d, madeup_out, "MU08", "250108")
    ticked(d, first)
    if how == "the pdf":
        assert d.store.add(madeup_out / "MU08_po_v1.pdf").version == 1 and len(d.store.versions("250108")) == 1
    else:
        again_path = export if how == "the same file" else tmp_path / "next morning.csv"
        again_path.write_text(MU08_EXPORT.replace("12.50", "12.5") if how == "another file" else MU08_EXPORT, encoding="utf-8")
        filed: list[str] = []
        assert d.store.from_export(again_path, columns, filed=filed) and filed == [] and len(d.store.snapshots("250108")) == 1
    name, by = ("v1", "version 1") if how == "the pdf" else ("e1", "export")
    given_on(d.store, "250108", name, AFTER_TICK)
    second, r = again(d, confirmation_again(tmp_path / "MU08_again.pdf", "MU08"), "250108", AFTER_TICK + timedelta(minutes=5))
    assert (r.version.version, price_flags(r), r.status) == (1, [(["1"], "12.50", "12.70")], "closed")
    assert [(a.rule_id, a.route) for a in r.actions] == [("R04", "AUTO"), ("R01", "AUTO")] and step_names(second) == sorted(STEP_FILES)
    assert line_changes(prepared) == [("1", "12.50", "12.70", "open", 2)]            # on the list again, asked by both cases
    assert last_mark(prepared) | {"at": ""} == {"entry_id": last_mark(prepared)["entry_id"], "mark": "reopened", "at": "", "by": by, "shown": "12.50"}


def test_an_unchanged_export_ends_the_tick_of_a_delivery_date(tmp_path, madeup_out):
    """W16, the check's minimised seed 30009: a later delivery date is prepared and ticked without the entry; the next export has
    the rows of the look before. The next confirmation, which names the order as Exact holds it, has no difference: it is compared
    with the printed date, and no change back to it is prepared. Before, the ticked date was laid over the order, the supplier's
    own date was flagged as a difference and a change back was prepared, which Exact held already."""
    d = order_deps(tmp_path, madeup_out)
    prepared = d.settings.cases_root / "prepared"
    d.store.add(order_pdf(tmp_path / "order_v1.pdf", "abc", 1))
    filed_on(d.store, ORDER, "v1", FILED)
    export = order_export(tmp_path / "export.csv", "abc")
    assert d.store.from_export(export, order_columns(export))
    filed_on(d.store, ORDER, "e1", FILED + timedelta(hours=1))
    first, r = again(d, order_confirmation(tmp_path / "x.pdf", "abc", {}, 1, dates={"b": "2026-11-19"}), ORDER, NOW)
    assert [(f.field, f.po_line_ids) for f in r.findings.flags] == [("delivery_date", ["2"])] and r.status == "closed"
    ticked(d, first, kind="po_line_date")
    assert d.store.from_export(export, order_columns(export)) and len(d.store.snapshots(ORDER)) == 1      # the same export again
    given_on(d.store, ORDER, "e1", AFTER_TICK)
    second, r = again(d, order_confirmation(tmp_path / "y.pdf", "abc", {}, 1), ORDER, AFTER_TICK + timedelta(minutes=5))
    assert (r.status, r.findings.flags, step_names(second)) == ("closed", [], sorted(STEP_FILES))
    assert [e.op.kind for e in r.write_entries if e.op.kind == "po_line_date"] == []
    assert line_changes(prepared, "po_line_date") == [("2", "2026-11-16", "2026-11-19", "open", 1)] and last_mark(prepared)["by"] == "export"


def test_an_unchanged_export_ends_a_second_tick_after_exact_was_changed_by_hand(tmp_path, madeup_out):
    """W16, the check's minimised seed 30014, the order above the authorization limit: 4.25 is prepared, requested, entered and
    ticked. The buyer then changes the price in Exact to 4.30 by hand: a PDF and an export show it, and the tick is ended. He ticks
    4.25 once more without entering it, and the next export has the rows of the look before. The confirmation that follows, which
    quotes the new print and names 4.30, has no difference and asks the approver nothing. Before, the second tick stood: 4.25 was
    laid over the print, a change to 4.30 was prepared, and the approver was sent a request with an old value Exact did not hold."""
    d = order_deps(tmp_path, madeup_out, limit="100")
    prepared = d.settings.cases_root / "prepared"
    d.store.add(order_pdf(tmp_path / "order_v1.pdf", "abc", 1))
    filed_on(d.store, ORDER, "v1", FILED)
    first, r = again(d, order_confirmation(tmp_path / "x.pdf", "abc", {"b": "4.25"}, 1), ORDER, NOW)
    assert [(a.rule_id, a.route) for a in r.actions] == [("R10", "AUTO"), ("R01", "AUTO")] and len(r.messages) == 1
    ticked(d, first)
    d.store.add(order_pdf(tmp_path / "order_v2.pdf", "abc", 2, prices={"b": "4.30"}))        # changed in Exact by hand: both prints
    export = order_export(tmp_path / "export.csv", "abc", prices={"b": "4.30"})
    assert d.store.from_export(export, order_columns(export))
    for name in ("v2", "e1"):
        filed_on(d.store, ORDER, name, AFTER_TICK)
    assert reconcile(prepared, d.store, d.settings.cases_root, AFTER_TICK + timedelta(minutes=1)) == 0
    assert line_changes(prepared) == [("2", "4.20", "4.25", "open", 1)] and last_mark(prepared)["by"] == "version 2"
    again_at = AFTER_TICK + timedelta(hours=1)
    ticked(d, first, at=again_at)                                                   # once more, without the entry
    assert d.store.from_export(export, order_columns(export)) and len(d.store.snapshots(ORDER)) == 1
    given_on(d.store, ORDER, "e1", again_at + timedelta(hours=1))
    second, r = again(d, order_confirmation(tmp_path / "y.pdf", "abc", {"b": "4.30"}, 2), ORDER, again_at + timedelta(hours=2))
    assert (r.version.version, r.status, r.findings.flags, r.messages, step_names(second)) == (2, "closed", [], [], sorted(STEP_FILES))
    assert line_changes(prepared) == [("2", "4.20", "4.25", "open", 1)] and last_mark(prepared) | {"at": "", "entry_id": ""} == {
        "entry_id": "", "mark": "reopened", "at": "", "by": "export", "shown": "4.30"}


@pytest.mark.parametrize("second,flags,states", [
    ("7.00", [], [("4", "7.00", "7.10", "entered", 1)]),
    ("7.10", [(["4"], "7.00", "7.10")], [("4", "7.00", "7.10", "entered", 1), ("4", "7.00", "7.10", "open", 1)])])
def test_a_tick_is_never_laid_over_the_other_delivery_of_its_article(tmp_path, madeup_out, second, flags, states):
    """W17 (the check's finding 2, its probe renumbered_dup, both halves): two deliveries of one article are lines 4 and 5 of
    version 1. The supplier raises the first delivery to 7,10, quoting version 1. A line above them leaves the order and Exact
    prints version 2, numbered by place: the first delivery is line 3, the second line 4. The buyer enters 7.10 on the first
    delivery and ticks. The next confirmation quotes version 2. With the second delivery at 7,00 it has no difference; with the
    second delivery raised to 7,10 too, that line is flagged and prepared as a row of its own, never confirmed silently. Before,
    the tick was laid over line 4 of version 2, the other delivery, which carries the number the change was asked under."""
    d = order_deps(tmp_path, madeup_out)
    prepared = d.settings.cases_root / "prepared"
    d.store.add(order_pdf(tmp_path / "order_v1.pdf", "abcde", 1))
    filed_on(d.store, ORDER, "v1", FILED)
    first, r = again(d, order_confirmation(tmp_path / "x.pdf", "abcde", {"d": "7.10"}, 1), ORDER, NOW)
    assert price_flags(r) == [(["4"], "7.00", "7.10")] and r.status == "closed"
    d.store.add(order_pdf(tmp_path / "order_v2.pdf", "abde", 2))                     # KM-5503 left the order: 3 and 4 are the deliveries
    filed_on(d.store, ORDER, "v2", NOW + timedelta(minutes=30))
    ticked(d, first)
    case, r = again(d, order_confirmation(tmp_path / "y.pdf", "abde", {"d": "7.10", "e": second}, 2), ORDER, AFTER_TICK)
    assert (r.version.version, price_flags(r), r.status) == (2, flags, "closed")
    assert case.read_step("entered")["replaced"] == [{"line_id": "3", "field": "price", "was": "7.00", "entered": "7.10",
                                                      "marked_at": TICKED.isoformat(), "by": "buyer"}]
    assert line_changes(prepared) == states and len({change.place for change in changes(prepared)[0] if change.kind == "po_line_price"}) == len(states)


def test_a_tick_of_a_delivery_that_left_the_order_is_laid_over_no_line(tmp_path, madeup_out):
    """W17, the check's minimised seed 30103: the later date of the first delivery is prepared against version 1 (its line 4). That
    delivery leaves the order, and in version 2 line 4 is the second delivery. The buyer ticks the change all the same. The next
    confirmation, which quotes version 2, has no difference: the ticked date belongs to no line of version 2. Before, it was laid
    over line 4, and the second delivery's own date was flagged and a change back was prepared."""
    d = order_deps(tmp_path, madeup_out)
    prepared = d.settings.cases_root / "prepared"
    d.store.add(order_pdf(tmp_path / "order_v1.pdf", "abcde", 1))
    filed_on(d.store, ORDER, "v1", FILED)
    first, r = again(d, order_confirmation(tmp_path / "x.pdf", "abcde", {}, 1, dates={"d": "2026-11-19"}), ORDER, NOW)
    assert [(f.field, f.po_line_ids) for f in r.findings.flags] == [("delivery_date", ["4"])]
    d.store.add(order_pdf(tmp_path / "order_v2.pdf", "abce", 2))                     # the first delivery left the order
    filed_on(d.store, ORDER, "v2", NOW + timedelta(minutes=30))
    ticked(d, first, kind="po_line_date")
    second, r = again(d, order_confirmation(tmp_path / "y.pdf", "abce", {}, 2), ORDER, AFTER_TICK)
    assert (r.version.version, r.status, r.findings.flags, step_names(second)) == (2, "closed", [], sorted(STEP_FILES))
    assert [e.op.kind for e in r.write_entries if e.op.kind == "po_line_date"] == []


def test_a_print_numbered_by_place_that_shows_the_entered_date_ends_no_tick(tmp_path, madeup_out):
    """W17, the check's minimised seed 30859: the later date of the first delivery (line 4 of version 1) is entered and ticked. A
    line above leaves the order; version 2, numbered by place, shows the entered date on line 3, the first delivery. The tick
    stands: the print shows its value on its own line. The next confirmation, which quotes version 2, has no difference. Before,
    the tick was held against line 4, the second delivery, and ended; ticked again, it was laid over that line."""
    d = order_deps(tmp_path, madeup_out)
    prepared = d.settings.cases_root / "prepared"
    d.store.add(order_pdf(tmp_path / "order_v1.pdf", "abcde", 1))
    filed_on(d.store, ORDER, "v1", FILED)
    first, _ = again(d, order_confirmation(tmp_path / "x.pdf", "abcde", {}, 1, dates={"d": "2026-11-19"}), ORDER, NOW)
    ticked(d, first, kind="po_line_date")
    d.store.add(order_pdf(tmp_path / "order_v2.pdf", "acde", 2, dates={"d": "2026-11-19"}))
    filed_on(d.store, ORDER, "v2", AFTER_TICK)
    second, r = again(d, order_confirmation(tmp_path / "y.pdf", "acde", {}, 2, dates={"d": "2026-11-19"}), ORDER, AFTER_TICK + timedelta(minutes=5))
    assert (r.version.version, r.status, r.findings.flags, step_names(second)) == (2, "closed", [], sorted(STEP_FILES))
    assert line_changes(prepared, "po_line_date") == [("4", "2026-11-16", "2026-11-19", "entered", 1)]
    assert last_mark(prepared)["mark"] == "entered"                                 # no print ended it


def test_the_changes_of_two_deliveries_are_two_rows(tmp_path, madeup_out):
    """W17, the check's minimised seed 30425: 7,08 is prepared for the first delivery against version 1, where it is line 4. A line
    above leaves the order; in version 2, numbered by place, line 4 is the second delivery, and the supplier asks 7,08 for that
    one too. The list holds two rows, each a place of its own, so that one tick never covers both deliveries. Before, the two
    were one change with the rows of two lines of the order."""
    d = order_deps(tmp_path, madeup_out)
    prepared = d.settings.cases_root / "prepared"
    d.store.add(order_pdf(tmp_path / "order_v1.pdf", "abcde", 1))
    filed_on(d.store, ORDER, "v1", FILED)
    first, _ = again(d, order_confirmation(tmp_path / "x.pdf", "abcde", {"d": "7.08"}, 1), ORDER, NOW)
    d.store.add(order_pdf(tmp_path / "order_v2.pdf", "bcde", 2))                     # KM-5501 left the order
    filed_on(d.store, ORDER, "v2", NOW + timedelta(minutes=30))
    second, r = again(d, order_confirmation(tmp_path / "y.pdf", "bcde", {"e": "7.08"}, 2), ORDER, LATER + timedelta(hours=1))
    assert price_flags(r) == [(["4"], "7.00", "7.08")]
    assert line_changes(prepared) == [("4", "7.00", "7.08", "open", 1), ("4", "7.00", "7.08", "open", 1)]
    one, other = [change for change in changes(prepared)[0] if change.kind == "po_line_price"]
    assert one.place != other.place and (one.case_ids, other.case_ids) == ([first.case_id], [second.case_id])
    assert mark(prepared, one.ids, "entered", TICKED + timedelta(hours=2)) == 1
    assert [state for *_, state, _ in line_changes(prepared)] == ["entered", "open"]


@pytest.mark.parametrize("print_", ["an export of both deliveries", "another article on the line"])
def test_a_tick_whose_line_a_newer_print_does_not_show_so_is_ended(tmp_path, madeup_out, print_):
    """W18 (the check's finding 3, its probes dup_export and code_changed): the buyer ticks a price without entering it. A newer
    print is filed after the tick. An export that lists the two deliveries of the article the other way round than the PDF, both
    at the old 7.00: the tick of the first delivery is held against that delivery, wherever it stands, and ended. A PDF in which
    the buyer has put another article on the line: the tick's line cannot be found, and the tick is ended. Either way the
    supplier's next confirmation of the raised price is flagged against the printed price again, never closed as confirmed."""
    d = order_deps(tmp_path, madeup_out)
    prepared = d.settings.cases_root / "prepared"
    d.store.add(order_pdf(tmp_path / "order_v1.pdf", "abcde", 1))
    filed_on(d.store, ORDER, "v1", FILED)
    key, line_id, price = ("d", "4", "7.10") if print_.startswith("an export") else ("a", "1", "12.70")
    first, r = again(d, order_confirmation(tmp_path / "x.pdf", "abcde", {key: price}, 1), ORDER, NOW)
    assert [ids for ids, _, _ in price_flags(r)] == [[line_id]]
    ticked(d, first)
    if print_.startswith("an export"):
        export = order_export(tmp_path / "export.csv", "abced")                      # the second delivery first: 4 and 5 change places
        assert d.store.from_export(export, order_columns(export))
        filed_on(d.store, ORDER, "e1", AFTER_TICK)
        by, shown = "export", "7.00"
    else:
        d.store.add(order_pdf(tmp_path / "order_v2.pdf", "abcde", 2, codes={"a": "KM-5599"}))
        filed_on(d.store, ORDER, "v2", AFTER_TICK)
        by, shown = "version 2", None
    second, r = again(d, order_confirmation(tmp_path / "y.pdf", "abcde", {key: price}, 1), ORDER, AFTER_TICK + timedelta(minutes=5))
    assert (r.version.version, [ids for ids, _, _ in price_flags(r)], step_names(second)) == (1, [[line_id]], sorted(STEP_FILES))
    assert last_mark(prepared) | {"at": "", "entry_id": ""} == {"entry_id": "", "mark": "reopened", "at": "", "by": by, "shown": shown}
    assert [state for *_, state, _ in line_changes(prepared)] == ["open"]
