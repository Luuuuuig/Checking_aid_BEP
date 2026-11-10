"""Tests of the pipeline (aid/pipeline.py), with its messages and the end of their wait (and the batch's call of it), and of the upload
source (aid/sources/upload.py), on the made-up set."""
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from email import message_from_bytes, policy

import pytest

from aid.batch import main as batch_main
from aid.case import sha256_file
from aid.decider import PROMPT as DECISION_PROMPT
from aid.decider import LlmDecider
from aid.execution.writer import PreparedWriter, RetryingWriter, WriteLog, WriteLogEntry, WriterError
from aid.messaging import FileMailer, MailerError, SentRecord, file_reply, pending_asks, record_sent
from aid.pipeline import DISPOSITION_COLUMNS, CaseResult, ReviewItem, escalate_pending, file_answer, read_with_parser, record_dispositions, run_case
from aid.po_store import PoStore
from aid.readers.llm_client import LlmResponse, ReaderError, RecordedClient
from aid.readers.supplier_llm import read_supplier_document, reading_request
from aid.routing import Action, MessageSpec
from aid.schema import DocValue, SourceRef
from aid.sources.upload import open_upload_case
from aid.tests.conftest import REPO_ROOT, SETTINGS, deps, read_csv


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
                                              doc_value=None)]
    assert result.status == "review" and result.digest_line == "PO 250117 (S17): ter beoordeling, geen schrijfacties"
    assert result.model_copy(update={"status": r.status, "review_items": r.review_items, "digest_line": r.digest_line}) == r
    escalation = {"case_id": c.case_id, "message_id": r.messages[0]["message_id"], "reason": "no answer in 3 working days", "at": later.isoformat()}
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
    file_reply(bounced, an_email(tmp_path / "bounce.eml", "Onbestelbaar: Vraag over orderbevestiging PO 250117", "postmaster@example.com"))
    file_reply(answered, an_email(tmp_path / "reply.eml", "Re: Vraag over orderbevestiging PO 250111", "sales@s11.example.com"))
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
