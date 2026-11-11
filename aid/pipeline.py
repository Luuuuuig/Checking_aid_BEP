"""The pipeline: one case from its supplier document to its result, through every step of the aid, with its audit record.

run_case takes a case (aid/case.py) and the dependencies of the run (Deps) and goes through these steps in this order. Each step
writes its output to the case folder as steps/<nn>_<name>.json and records it in the audit record (aid/audit.py) with its seconds:

    01_po_versions    what the store gives the case's PO to be matched against (PoStore.candidates of aid/po_store.py): its PDF
                      versions, or the PO as it stands alone when that is a look from an ERP export and the case is a cart or the PO
                      has no PDF version; none when the PO number is unknown
    02_read_document  the supplier's document, the first input file of the case, as deps.reader reads it
    03_version        the version the document answers (aid/versioning.py), among those of step 01. A cart is checked against the PO
                      as it stands: the last of step 01, the latest PDF version or the look from the export
    04_match          the line pairs (aid/matching.py)
    05_compare        the findings (aid/comparison.py), with the selected version as their version
    06_route          the context of the case and the actions (aid/routing.py), each action also a decision in the audit
    07_execute        the actions carried out, in their order
    08_result         the CaseResult

A case is run once. run_case refuses a case folder that holds its result already (ValueError "case already run"), because a second
run would apply its writes again, and with a mailer send its messages again, unless it is called with rerun=True. A folder without a
result whose run was cut off before its first write or message is run as a new case. One cut off after it, whose write_log.jsonl
holds an entry or whose messages/sent.jsonl exists, is not run again, since that would repeat the writes and the messages: it is
finished as review with one review item "run cut off after writes: check the write log", the entries of its write log in the
result and the audit, no write and no message, and its audit record saved.

The pipeline calls the modules and records what they give; it compares, routes and writes nothing by itself. Without a selected
version nothing is matched or compared: steps 04 and 05 hold {"skipped": "no version selected"}, the result has no findings (which
is not "no difference") and the routing hands the case to the buyer. The decider is asked through a wrapper that records each of its
answers as a call in the audit, for the version and for the lines alike, and a model decider (LlmDecider) is named as the decider's
model of the audit. A reader that returns its model's response with the Document has the call recorded, and a reading by the LLM
reader names the reader's model, with the version and the hash of its prompt. Each decision of the audit cites the settings its rule
read (RULE_SETTINGS of aid/routing.py; for R10 also those of the rule it took its writes from) and the findings the action covers,
by the ids the routing gives them.

A case whose PO number was found otherwise than as the one number of the PO number pattern (case.json's po_found_by: a held number
chosen among the pattern's numbers, a held number when the pattern found none, the quick read by lines, or the runner's check again of
a case without a number; aid/sources/mailbox.py) is the aid's guess, so it goes to the buyer. Like R11, but outside the routing, one
REVIEW action of the pipeline's own rule GUESSED_RULE covers the whole case, with the reason GUESSED_PO and every finding in the order
the routing gives them, and takes the place of the routing's actions: nothing is written or asked, and the case is in review. When
no version was selected, an action of R09 with the reason "no version selected" (NO_VERSION), as the routing makes it, follows it, so
that the buyer sees that nothing was compared and decides that item as any other.

The context of the routing. The supplier is the PO's: that of the selected version, else of the latest stored version (a PO keeps its
supplier), else the name the document prints, as for a cart whose export names no supplier. Its lead time is the one the settings
give it, and cases_seen_for_supplier counts the other cases under cases_root whose steps/08_result.json is closed for that supplier.
Whether the PO is above the authorization comes from the selected version, the delivery dates, price bases and item codes from its
lines, and the document to attach is the input file the reader read.

Execution carries out the actions in their order, so that R01, the routing's last action, comes last.
- An AUTO action applies each of its operations through deps.writer, with the action's finding ids and its flags, values and sources
  included, as the evidence. Each entry the writer returns goes into the result and the audit first and then into the case's write
  log (aid/execution/writer.py), so that a change is never left without a record, even when the log cannot be written. A change made,
  or prepared because the aid has no write route (the prepared writer as the writer), is done with. A change the writer could only
  prepare after the write route failed, which the entry's error says (as the fallback of RetryingWriter gives it), is a review item
  "prepared change: apply by hand", and a change it could not make at all (status "failed") a review item "write failed: <why>":
  a failed call becomes a prepared change and a review item (design specification, section 6). Once a write waits for the buyer so,
  no later operation of the case goes to the writer: each is a review item "not applied: an earlier write failed (<the write>)",
  without an entry and without a line in the write log, so that a PO is never confirmed (R01's set_status) over a correction that
  was not made. An R10 action then sends its request to the approver to approve the PO again (template "reapproval",
  render_reapproval of aid/messaging) for the changes it made before such a failure, and no request when it made none.
- An ASK action sends its question to the supplier (render of aid/messaging), to the address settings.supplier_emails gives the PO's
  supplier, the buyer in copy; its table shows each item's PO lines as the routing joins them ("1, 2"), with the item code and the
  description of the first of them in the selected version.
- Each finding of a REVIEW action is a review item, and an action about the whole case (no version, a new supplier without findings)
  is one item without a finding. Review items are numbered RI1, RI2, ... in the order they arise.
A message goes through deps.mailer and is recorded in the result and the audit: the rule, the template, the items as the routing
gives them, the finding ids, the status and the time. A message sent has the status "sent" and the message id, addressees, subject,
time and refused copies (refused_cc, the buyer's copy that the server refused, which needs no review item) of the mailer's record,
which then goes to the case's messages/sent.jsonl. Without a mailer each message is "not sent (no mailer)". A question to a supplier
the settings give no address is "not sent (no address for the supplier)", and a message the mailer cannot send, whatever it raises,
is "not sent (<the error's class>)", the audit keeping the error's text: each finding of the message is then a review item, "no
address for the supplier" or "message not sent", and the run goes on. No message about a case made from a try-out document is sent
(tryout_case of aid/tryout_mark.py: its document's first row is the try-out's): without asking the mailer, each is recorded as "not
sent (try-out document: nothing is sent)", and each finding of the message is a review item with the reason "try-out document:
nothing is sent", so that the case comes to the buyer instead of waiting for an answer. Such a case writes through the prepared
writer only, never through the Exact writer (tryout_writer), and it is not counted among a supplier's closed cases.

The status: review when the case has a review item, from a REVIEW action, a write or a message not sent (a question of the same case
is sent all the same, but the buyer has to act), else waiting when an action is ASK, else closed; a request for re-approval does not
change it. An exception at any step makes the case failed: the steps after it do not run, what was done stays done, and the error is
in the result and in the audit. Its text names the write it failed at, if any: its rule, operation and line. In the audit it carries
its traceback as well, unless it is a ReaderError or a WriterError of its own, the failures a run expects; one that wraps another
error (the ValueError behind "no template", which may be a pydantic ValidationError) keeps the traceback of that error. On every
path the result is written as step 08 and the audit record is saved, so that run_case never returns with a case left without them.

The digest line names the PO, the supplier and the status, and counts the writes made, the changes prepared and the writes failed,
each on its own. The line of a cart begins with the word for the pre-check ("voorcontrole", in English "pre-check").

escalate_pending ends the wait for the suppliers' answers, when the batch or the runner calls it: a waiting case whose latest question
bounced, or has no reply after settings.ask_wait_working_days working days (ended_waits of aid/messaging), gets a review item in its
saved result and the escalation in its audit record, once per question. A case whose files cannot be read is named, and the others
go on.

waits_for_po tells from a saved result whether its case waits for its PO: in review with no version selected because version
selection had no candidate, no version of the PO stored ("po"), or no stored version that the document quotes ("version <n>"). The
runner checks such a case again once the PO, or that version, is filed (aid/runner.py); checked_again of aid/review/close.py adds
what the case folder must hold for that, and waiting_for_po that the case has a PO number of its own, without which it waits for
nothing.

file_answer files an e-mail that answers a question of the aid into its case: the mailbox (aid/sources/mailbox.py) calls it for each
e-mail in the watched folder. The e-mail answers a question when its In-Reply-To or References header names a Message-ID whose part
before the "@" is the message id of a record in a case's messages/sent.jsonl that is not a re-approval request (the approver's
answer to one is handled as any other e-mail). file_reply of aid/messaging files it under replies/,
bounces/ or auto/, and the case's audit record lists it under "answers". A real reply gives the case's saved result a review item
"supplier answered: read the reply" (one per reply) and the status review (a failed case keeps its status), so that the wait ends
and the buyer decides; a bounce is left to escalate_pending, and an automatic reply changes nothing.

record_dispositions writes the aid's own decisions on a case to its disposition log (evaluation protocol v2, section 6.4).
"""
import csv
import email
import json
import re
import time
import traceback
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from email import policy
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, SkipValidation

from aid.audit import AUDIT_FILE, Audit
from aid.case import Case, _write_json, load_case        # _write_json: the audit record is rewritten whole or not at all, as audit.py does
from aid.comparison import Field, Findings, Flag, Uncompared, compare
from aid.decider import Decider, Decision, DecisionAnswer, LlmDecider, decision_request
from aid.execution.ops import WriteOp
from aid.execution.writer import LOG_FILE, WriteLog, WriteLogEntry, Writer, WriterError
from aid.matching import match_lines
from aid.messaging import (MESSAGES, REAPPROVAL, SENT_FILE, Mailer, ended_waits, file_reply, is_auto_reply, is_bounce, record_escalation,
                           record_sent, render, render_reapproval)
from aid.po_store import PoStore, PoVersion
from aid.readers.cart import is_cart_page, read_cart_html
from aid.readers.llm_client import LlmResponse, ReaderError
from aid.readers.supplier_llm import reading_request
from aid.readers.supplier_parser import detect_template, load_templates, read_with_template
from aid.routing import (NO_VERSION, RULE_SETTINGS, Action, CaseContext, MessageSpec, findings_by_id, route,
                         _shown)                 # _shown: a value as the routing's questions show it
from aid.routing import _id_order, _uncompared_ids    # the order of the finding ids in an action, as the routing gives it
from aid.schema import DocLine, DocValue, Document
from aid.settings import Settings
from aid.tryout_mark import NOTHING_SENT, TRYOUT_NOT_SENT, case_is_tryout, tryout_case, tryout_writer
from aid.versioning import VersionChoice, select_version

Status = Literal["closed", "review", "waiting", "failed"]
RESULT_FILE = "08_result.json"                   # the result in steps/, which a case that was run holds
NOT_SENT = "not sent (no mailer)"                # the status of a message recorded without a mailer
SENT = "sent"                                    # the status of a message the mailer sent
UNADDRESSED = "no address for the supplier"      # why a question was not sent; with UNSENT, the reasons of their review items
UNSENT = "message not sent"
BOUNCED = "message bounced"                      # the reason of the review item of a question that bounced
UNREADABLE = "unreadable"                        # escalate_pending's reason for a case whose files it cannot read
PREPARED_BY_HAND = "prepared change: apply by hand"
NOT_APPLIED = "not applied: an earlier write failed"
CUT_OFF = "run cut off after writes: check the write log"     # the review item of a case whose run was cut off after a write or a message
ANSWERED = "supplier answered: read the reply"   # the review item of a case whose supplier replied
# The reasons of version selection (aid/versioning.py) when it had no candidate: no version of the PO stored, and a quoted version
# that is not stored ("quotes version 2, which is not stored").
NO_STORED_VERSION, QUOTES_VERSION = "no stored version", "quotes version "
GUESSED_PO = "PO found otherwise than by the number recognition"     # the reason of a case whose PO was guessed (case.json's po_found_by)
GUESSED_RULE = "P01"                             # the pipeline's own rule of that reason: one REVIEW action for the whole case, as R11
DISPOSITION_FILE = "dispositions.csv"
DISPOSITION_COLUMNS = ("case_id", "version_selected", "line_id", "field", "decision", "source", "note")   # evaluation/templates/dispositions.csv


class Deps(BaseModel, arbitrary_types_allowed=True):
    """What a run needs besides its case. The reader takes the path of the document and the kind of the case and returns the
    Document, or the Document and its model's response when a model read it. The clock gives the time of every record. pydantic
    cannot check an object against a Protocol, so the decider, the writer and the mailer are taken as they are given."""
    settings: Settings
    store: PoStore
    reader: Callable[[Path, str], Document | tuple[Document, LlmResponse]]
    decider: SkipValidation[Decider]
    writer: SkipValidation[Writer]
    mailer: SkipValidation[Mailer | None]        # None records each message as not sent
    clock: Callable[[], datetime]
    aid_version: str = "0.1"


class ReviewItem(BaseModel):
    """One item the buyer decides on the review page."""
    item_id: str                                 # "RI1", "RI2", ... in the order the items arise
    finding_id: str | None                       # the flag ("F1") or the uncompared item ("U1"); None for an item about the whole case
    reason: str                                  # the reason of the REVIEW action, or why a write waits for the buyer
    field: Field | None                          # the field of the finding
    po_value: DocValue | None                    # the values of a flag; None for an uncompared item or the whole case
    doc_value: DocValue | None
    entry_id: str | None = None                  # the write-log entry of a write that waits for the buyer


class CaseResult(BaseModel):
    case_id: str
    status: Status
    version: VersionChoice | None                # None when the run failed before the version was selected
    findings: Findings | None                    # None when nothing was compared: no version, or a failure before the comparison
    actions: list[Action]
    write_entries: list[WriteLogEntry]
    messages: list[dict]                         # one record per message: rule_id, template, items, finding_ids, status, timestamp
    review_items: list[ReviewItem]
    digest_line: str
    error: str | None = None                     # the type and text of the exception that failed the case, and the write it failed at
    supplier: str | None = None                  # the PO's supplier, by which later cases count the closed cases of their supplier


def run_case(case: Case, deps: Deps, rerun: bool = False) -> CaseResult:
    """Run the steps of the module docstring on the case and return its result, which is also step 08 of the case folder. The failure
    of a step is the status "failed" of the result, not an exception. run_case raises ValueError ("case already run") for a case that
    holds its result already, unless rerun is True, which runs it again: it applies its writes again and, with a mailer, sends every
    message again. Otherwise it raises only when it is interrupted or when the result or the audit record cannot be written."""
    if not rerun and (case.root / "steps" / RESULT_FILE).exists():
        raise ValueError(f"case already run: {case.case_id} holds steps/{RESULT_FILE}; rerun=True runs it again and applies its writes again")
    if not rerun and _cut_off_after_writes(case):
        return _Run(case, deps).finish_cut_off()
    return _Run(case, deps).result()


def _cut_off_after_writes(case: Case) -> bool:
    """Whether an earlier run of the case wrote or sent something: its write log holds a line, or messages/sent.jsonl exists."""
    log = case.root / LOG_FILE
    return (case.root / MESSAGES / SENT_FILE).exists() or (log.is_file() and bool(log.read_bytes().strip()))


class _Run:
    """One run of the pipeline on one case. It keeps what the steps have given so far, so that a failure at any step has a result."""

    def __init__(self, case: Case, deps: Deps):
        self.case, self.deps, self.settings = case, deps, deps.settings
        self.audit = Audit(case, deps.settings, started_at=deps.clock(), aid_version=deps.aid_version)
        self.decider = _RecordedDecider(deps.decider, self.audit)
        self.step, self.started = "start", time.monotonic()
        self.writing: str | None = None          # the write in progress, which the error of a failure at it names
        self.version: VersionChoice | None = None
        self.findings: Findings | None = None
        self.by_id: dict[str, Flag | Uncompared] = {}
        self.supplier: str | None = None
        self.po_lines: dict[str, DocLine] = {}   # the lines of the selected version by line id, which a question's table shows
        self.ctx: CaseContext | None = None
        self.actions: list[Action] = []
        self.entries: list[WriteLogEntry] = []
        self.messages: list[dict] = []
        self.review_items: list[ReviewItem] = []

    def result(self) -> CaseResult:
        """Run the steps and return the result, written and recorded, with the audit record saved."""
        try:
            self.steps()
            status, error = self.status(), None
        except Exception as exc:
            status, error = "failed", f"{type(exc).__name__}: {exc}" + (f" (write: {self.writing})" if self.writing else "")
            traced = not isinstance(exc, (ReaderError, WriterError)) or exc.__cause__ is not None
            self.audit.error(self.step, f"{error}\n{''.join(traceback.format_exception(exc)).rstrip()}" if traced else error)
        self.begin("result")
        result = CaseResult(case_id=self.case.case_id, status=status, version=self.version, findings=self.findings, actions=self.actions,
                            write_entries=self.entries, messages=self.messages, review_items=self.review_items,
                            digest_line=_digest_line(self.case.po_number, self.supplier, status, self.entries, self.settings.language,
                                                     self.case.kind),
                            error=error, supplier=self.supplier)
        try:
            self.end(8, result)
        finally:
            self.audit.save(finished_at=self.deps.clock())
        return result

    def finish_cut_off(self) -> CaseResult:
        """The result of a case whose run was cut off after a write or a message (see the module docstring): review, one item, the
        entries of the write log, nothing written or sent. A write log that cannot be read is recorded as an error in the audit, and
        the item still sends the buyer to it."""
        self.begin("cut_off")
        for path in self.case.input_files:
            if path.is_file():
                self.audit.input(path)
        try:
            self.entries = WriteLog(self.case.root).entries()
        except ValueError as error:
            self.audit.error(self.step, f"{type(error).__name__}: {error}")
        for entry in self.entries:
            self.audit.write(entry)
        try:
            self.supplier = self.case.read_step("route")["context"]["supplier"] or None
        except (OSError, ValueError, KeyError, TypeError):
            self.supplier = None
        self.review(CUT_OFF, None)
        self.begin("result")
        result = CaseResult(case_id=self.case.case_id, status="review", version=None, findings=None, actions=[], write_entries=self.entries,
                            messages=[], review_items=self.review_items,
                            digest_line=_digest_line(self.case.po_number, self.supplier, "review", self.entries, self.settings.language,
                                                     self.case.kind),
                            supplier=self.supplier)
        try:
            self.end(8, result)
        finally:
            self.audit.save(finished_at=self.deps.clock())
        return result

    def steps(self) -> None:
        """Steps 01 to 07 of the module docstring."""
        for path in self.case.input_files:
            self.audit.input(path)
        if isinstance(self.deps.decider, LlmDecider):
            self.audit.model(*_decider_model(self.deps.decider), role="decider")

        self.begin("po_versions")
        versions = self.deps.store.candidates(self.case.po_number, self.case.kind) if self.case.po_number else []
        self.supplier = versions[-1].supplier if versions else None
        self.end(1, {"po_number": self.case.po_number, "versions": [version.model_dump(mode="json") for version in versions]})

        self.begin("read_document")
        doc = self.read()
        self.end(2, doc)

        self.begin("version")
        self.version = select_version(doc, versions[-1:] if self.case.kind == "cart" else versions, self.decider)
        po = next((version for version in versions if version.version == self.version.version), None)
        self.po_lines = {line.line_id: line for line in po.document.lines} if po is not None else {}
        self.supplier = (po.supplier if po is not None else None) or self.supplier or doc.supplier.value
        self.end(3, self.version)

        self.begin("match")
        match = match_lines(doc, po.document, self.decider) if po is not None else None
        self.end(4, match if match is not None else {"skipped": NO_VERSION})

        self.begin("compare")
        if po is not None:
            self.findings = compare(match, doc, po.document, self.case.kind).model_copy(update={"version": self.version.version})
        self.end(5, self.findings if self.findings is not None else {"skipped": NO_VERSION})

        self.begin("route")
        nothing = Findings(version=None, flags=[], matches=[], uncompared=[])                     # nothing compared: R09 reviews the case
        findings = self.findings if self.findings is not None else nothing
        self.ctx = ctx = self.context(po)
        self.actions = route(findings, ctx, self.settings)
        if self.case.po_found_by is not None:        # a guessed PO: the buyer checks it first (see the module docstring)
            finding_ids = [flag.flag_id for flag in sorted(findings.flags, key=_id_order)] + _uncompared_ids(findings)
            unversioned = [Action(route="REVIEW", rule_id="R09", finding_ids=[], reason=NO_VERSION)] if findings.version is None else []
            self.actions = [Action(route="REVIEW", rule_id=GUESSED_RULE, finding_ids=finding_ids, reason=GUESSED_PO), *unversioned]
        self.by_id = findings_by_id(findings)
        for action in self.actions:
            self.audit.decision(action.rule_id, _thresholds(action, ctx, self.settings), self.inputs(action, ctx), action.route)
        self.end(6, {"context": ctx.model_dump(mode="json"), "actions": [action.model_dump(mode="json") for action in self.actions]})

        self.begin("execute")
        self.execute()
        self.end(7, {"write_entries": [entry.model_dump(mode="json") for entry in self.entries], "messages": self.messages,
                     "review_items": [item.model_dump(mode="json") for item in self.review_items]})

    def begin(self, step: str) -> None:
        self.step, self.started = step, time.monotonic()

    def end(self, number: int, payload: BaseModel | dict) -> None:
        """Write the output of the step begun last to the case folder and record it in the audit, with the seconds since it began."""
        seconds = time.monotonic() - self.started
        self.case.write_step(number, self.step, payload)
        self.audit.step(self.step, payload, seconds)

    def read(self) -> Document:
        """Step 02: the case's first input file, read by deps.reader. When the reader returns its model's response, the call is
        recorded, and for a reading by the LLM reader its model too; a ReaderError that carries the model's last answer has that call
        recorded before it fails the case."""
        if not self.case.input_files:
            raise ValueError(f"case {self.case.case_id} has no document to read")
        path = self.case.input_files[0]
        try:
            read = self.deps.reader(path, self.case.kind)
        except ReaderError as error:
            if error.response is not None:
                self.audit.call("reader", error.response)
            raise
        if not isinstance(read, tuple):
            return read
        doc, response = read
        self.audit.call("reader", response)
        if (prompt_version := _llm_prompt_version(doc)) is not None:
            self.audit.model(response.model, prompt_version, reading_request(path, self.case.kind, prompt_version).system, role="reader")
        return doc

    def context(self, po: PoVersion | None) -> CaseContext:
        """The context of the routing (see the module docstring)."""
        lines = po.document.lines if po is not None else []
        supplier = self.supplier or ""
        return CaseContext(supplier=supplier, po_number=self.case.po_number or "", version=self.version.version,
                           po_date=_by_line(lines, "delivery_date"), po_basis=_by_line(lines, "price_basis"),
                           po_item_code=_by_line(lines, "item_code"), po_above_authorization=po.above_authorization if po is not None else None,
                           supplier_lead_time_days=self.settings.supplier_lead_times.get(supplier),
                           cases_seen_for_supplier=_closed_cases(self.settings.cases_root, supplier, self.case.case_id),
                           document_path=str(self.case.input_files[0]), kind=self.case.kind)

    def covered(self, action: Action) -> list[dict]:
        """The findings an action covers, with their values, as JSON."""
        return [self.by_id[finding_id].model_dump(mode="json") for finding_id in action.finding_ids if finding_id in self.by_id]

    def inputs(self, action: Action, ctx: CaseContext) -> dict:
        """What the action's rule was applied to: the findings it covers, with their values, and the facts of the case the rules read."""
        return {"finding_ids": action.finding_ids, "findings": self.covered(action),
                "context": {"supplier": ctx.supplier, "kind": ctx.kind, "version": ctx.version, "cases_seen_for_supplier": ctx.cases_seen_for_supplier,
                            "supplier_lead_time_days": ctx.supplier_lead_time_days, "po_above_authorization": ctx.po_above_authorization}}

    def execute(self) -> None:
        """Step 07: the actions in their order (see the module docstring). A case made from a try-out writes through the prepared writer
        only (tryout_writer of aid/tryout_mark.py)."""
        log = WriteLog(self.case.root)
        writer = tryout_writer(self.deps.writer, self.settings) if tryout_case(self.case) else self.deps.writer
        stopped = False                              # a write waits for the buyer: no later operation goes to the writer
        for action in self.actions:
            if action.route == "AUTO":
                evidence = {"finding_ids": action.finding_ids, "findings": self.covered(action)}
                finding_id = action.finding_ids[0] if action.finding_ids else None
                made = []                                # the operations of the action that were made, or prepared by design
                for op in action.ops:
                    if stopped:
                        self.review(f"{NOT_APPLIED} ({_write_name(action.rule_id, op)})", finding_id)
                        continue
                    self.writing = _write_name(action.rule_id, op)
                    entry = writer.apply(op, self.case.case_id, action.rule_id, evidence, self.deps.clock())
                    self.entries.append(entry)       # recorded before the log is written, so that no change is without a record
                    self.audit.write(entry)
                    if (reason := _waits_for_the_buyer(entry)) is not None:
                        self.review(reason, finding_id, entry.entry_id)
                        stopped = True
                    else:
                        made.append(op)
                    log.append(entry)
                    self.writing = None
                if action.rule_id == "R10" and made:
                    self.message(action, REAPPROVAL, [_change(op) for op in made], made)
            elif action.route == "ASK":
                self.message(action, action.message.template, action.message.items)
            else:
                self.review_findings(action.reason, action.finding_ids)

    def review(self, reason: str, finding_id: str | None, entry_id: str | None = None) -> None:
        """Add a review item about the finding, with its field and, for a flag, its values; entry_id names the write it is about."""
        finding = self.by_id.get(finding_id) if finding_id is not None else None
        flag = finding if isinstance(finding, Flag) else None
        self.review_items.append(ReviewItem(item_id=f"RI{len(self.review_items) + 1}", finding_id=finding_id, reason=reason,
                                            field=finding.field if finding is not None else None, po_value=flag.po_value if flag else None,
                                            doc_value=flag.doc_value if flag else None, entry_id=entry_id))

    def review_findings(self, reason: str, finding_ids: list[str]) -> None:
        """A review item for each of the findings, or one item without a finding when there are none (an action about the whole case)."""
        for finding_id in finding_ids or [None]:
            self.review(reason, finding_id)

    def message(self, action: Action, template: str, items: list[dict], made: list[WriteOp] | None = None) -> None:
        """Send a message of the action through the mailer and record it (see the module docstring): the question of an ASK action to
        the supplier, or, given the changes it made, the request of an R10 action to the approver. Nothing about a case made from a
        try-out is sent (aid/tryout_mark.py)."""
        if tryout_case(self.case):
            self.record(action, template, items, TRYOUT_NOT_SENT)
            self.review_findings(NOTHING_SENT, action.finding_ids)
            return
        if self.deps.mailer is None:
            self.record(action, template, items, NOT_SENT)
            return
        if made is not None:
            message = render_reapproval(made, self.ctx, self.settings).model_copy(update={"case_id": self.case.case_id})
        elif address := self.settings.supplier_emails.get(self.ctx.supplier):
            spec = MessageSpec(template=template, items=[self.with_po_line(item) for item in items])
            message = render(spec, self.case, self.ctx, self.settings, address)
        else:
            self.record(action, template, items, f"not sent ({UNADDRESSED})")
            self.review_findings(UNADDRESSED, action.finding_ids)
            return
        try:
            sent = self.deps.mailer.send(message)
        except Exception as error:                   # whatever stops the mailer, the run goes on and the buyer sends the message
            self.record(action, template, items, f"not sent ({type(error).__name__})", error=f"{type(error).__name__}: {error}")
            self.review_findings(UNSENT, action.finding_ids)
            return
        self.record(action, template, items, SENT, message_id=sent.message_id, to=sent.to, subject=sent.subject, sent_at=sent.sent_at,
                    refused_cc=sent.refused_cc)
        record_sent(self.case, sent)                 # after the result and the audit, so that a message sent is never without a record

    def record(self, action: Action, template: str, items: list[dict], status: str, error: str | None = None,
               **sent: str | list[str] | int) -> None:
        """Record a message in the result and the audit; the audit also keeps the error of a message the mailer could not send."""
        record = {"rule_id": action.rule_id, "template": template, "items": items, "finding_ids": action.finding_ids, "status": status,
                  "timestamp": self.deps.clock().isoformat()} | sent
        self.messages.append(record)
        self.audit.message(record if error is None else record | {"error": error})

    def with_po_line(self, item: dict) -> dict:
        """An item of a question for the message to show, the routing's keys kept: its line is its po_line_ids as the routing joins them
        ("1, 2"), with the item code and the description of the first of them in the selected version. An item without such a line (an
        extra line) stays as it is."""
        line = self.po_lines.get(item["po_line_ids"].partition(", ")[0])
        if line is None:
            return item
        return item | {"line_id": item["po_line_ids"], "item_code": _shown(line.item_code), "description": _shown(line.description)}

    def status(self) -> Status:
        if self.review_items:
            return "review"
        return "waiting" if any(action.route == "ASK" for action in self.actions) else "closed"


class _RecordedDecider:
    """The run's decider, recording each of its answers as a call in the audit."""

    def __init__(self, decider: Decider, audit: Audit):
        self.decider, self.audit, self.name = decider, audit, decider.name

    def decide(self, d: Decision) -> DecisionAnswer:
        answer = self.decider.decide(d)
        self.audit.call("decider", answer)
        return answer


# --- the readers ---

def read_with_parser(path: Path, kind: str) -> Document:
    """The reader of a run that needs no model. A PDF confirmation is read by the parser baseline (aid/readers/supplier_parser.py): the
    first layout template that detect_template finds reads the PDF. A cart page saved as HTML (is_cart_page: .html or .htm) is read by
    the cart reader (read_cart_html of aid/readers/cart.py), whose ReaderError "no cart table in <file name>" fails a page without a cart
    table. What else it cannot read raises ReaderError, whose text begins "no template for <file name>", so that the case fails as
    unreadable: a cart screenshot (the LLM reader's) or any other file, a PDF no template fits (a scan has no text, so none does) and a
    PDF its template does not fit after all (the ValueError of read_with_template, whose text follows and which stays the ReaderError's
    cause)."""
    path = Path(path)
    if is_cart_page(path, kind):
        return read_cart_html(path)
    if kind != "confirmation" or path.suffix.lower() != ".pdf":
        raise ReaderError(f"no template for {path.name}: the parser reads PDF confirmations and HTML cart pages only")
    template = detect_template(path, load_templates())
    if template is None:
        raise ReaderError(f"no template for {path.name}")
    try:
        return read_with_template(path, template)
    except ValueError as error:
        raise ReaderError(f"no template for {path.name}: {error}") from error


def _llm_prompt_version(doc: Document) -> str | None:
    """The prompt version of a reading by the LLM reader, which names its reader "llm:<client name>:<prompt version>"; None for any
    other reader."""
    return doc.reader.rpartition(":")[2] if doc.reader.startswith("llm:") else None


_PROMPT_VERSION = re.compile(r"<!--\s*\w+\s+(v\d+)\s*-->")      # the first line of a prompt file: "<!-- decide v1 -->"


def _decider_model(decider: LlmDecider) -> tuple[str, str, str]:
    """The model, the prompt version and the system prompt of a model decider: its client's name and the decision prompt, the same
    for every decision, whose first line names its version."""
    system = decision_request(Decision(kind="version", options=[], context={})).system
    found = _PROMPT_VERSION.fullmatch(system.partition("\n")[0].strip())
    return decider.client.name, found[1] if found else "unknown", system


# --- the context, the decisions and the writes ---

def _by_line(lines: list[DocLine], field: str) -> dict[str, str]:
    """A field of the PO lines by line id, for the lines that state it."""
    return {line.line_id: getattr(line, field).value for line in lines if getattr(line, field).value is not None}


def _closed_cases(cases_root: Path, supplier: str, this_case: str) -> int:
    """The number of the other cases under cases_root whose steps/08_result.json is closed for this supplier; 0 for an unknown
    supplier. A result that cannot be read is not counted, and neither is a case made from a try-out (aid/tryout_mark.py), which is
    no supplier's first case, nor a case finished as replaced, which has no supplier (finish_replaced of aid/review/close.py)."""
    if not supplier:
        return 0
    count = 0
    for path in Path(cases_root).glob(f"*/steps/{RESULT_FILE}"):
        if path.parents[1].name == this_case:
            continue
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (isinstance(result, dict) and result.get("status") == "closed" and result.get("supplier") == supplier
                and not case_is_tryout(cases_root, path.parents[1].name)):
            count += 1
    return count


def _thresholds(action: Action, ctx: CaseContext, settings: Settings) -> dict:
    """The settings the action's rule read (RULE_SETTINGS), and for an R10 action those of the rule it took its writes from, by name,
    as JSON values. The authorization limit is "set" or "unset", never its amount, and supplier_lead_times holds the entry of the
    case's supplier alone."""
    values = settings.model_dump(mode="json", exclude={"authorization_limit"})
    thresholds = {}
    for name in (*RULE_SETTINGS.get(action.rule_id, ()), *RULE_SETTINGS.get(action.base_rule_id, ())):
        if name == "authorization_limit":
            thresholds[name] = "unset" if settings.authorization_limit is None else "set"
        elif name == "supplier_lead_times":
            thresholds[name] = {ctx.supplier: values[name][ctx.supplier]} if ctx.supplier in values[name] else {}
        else:
            thresholds[name] = values[name]
    return thresholds


def _write_name(rule_id: str, op: WriteOp) -> str:
    """A write as the error of a failure at it names it: its rule, its operation and the line or the article it changes."""
    return ", ".join([f"rule {rule_id}", op.kind] + ([f"line {op.line_id}"] if op.line_id else [])
                     + ([f"article {op.article_code}"] if op.article_code else []))


def _waits_for_the_buyer(entry: WriteLogEntry) -> str | None:
    """Why a write waits for the buyer: a change the writer could not make, or one it could only prepare after the write route failed
    (the entry's error says so); None for a change made, or prepared because the aid has no write route."""
    if entry.status == "failed":
        return f"write failed: {entry.error or 'the writer made no change'}"
    if entry.status == "prepared" and entry.error is not None:
        return PREPARED_BY_HAND
    return None


def _change(op: WriteOp) -> dict[str, str]:
    """A change an R10 action sends for re-approval, as its request lists it, in plain strings."""
    return {"kind": op.kind, "line_id": op.line_id or "", "article_code": op.article_code or "", "old_value": op.old_value or "",
            "new_value": op.new_value or ""}


# --- the digest line ---

_WORDS = {"nl": {"status": {"closed": "afgesloten", "review": "ter beoordeling", "waiting": "wacht op de leverancier", "failed": "mislukt"},
                 "unknown": "onbekend", "none": "geen schrijfacties", "cart": "voorcontrole",
                 "entries": {"done": ("schrijfactie", "schrijfacties"), "prepared": ("voorbereide wijziging", "voorbereide wijzigingen"),
                             "failed": ("mislukte schrijfactie", "mislukte schrijfacties")}},
          "en": {"status": {"closed": "closed", "review": "for review", "waiting": "waiting for the supplier", "failed": "failed"},
                 "unknown": "unknown", "none": "no writes", "cart": "pre-check",
                 "entries": {"done": ("write", "writes"), "prepared": ("prepared change", "prepared changes"),
                             "failed": ("failed write", "failed writes")}}}


def _digest_line(po_number: str | None, supplier: str | None, status: Status, entries: list[WriteLogEntry], language: str,
                 case_kind: str) -> str:
    """The case in one line, in the language of the settings: "PO 250101 (S01): afgesloten, 2 schrijfacties", in English "PO 250101
    (S01): closed, 2 writes". The writes made, the changes prepared and the writes failed are counted each on their own ("1
    schrijfactie, 2 voorbereide wijzigingen"). An unknown supplier is left out and an unknown PO is called so. The line of a cart, the
    pre-check, begins with "voorcontrole", in English "pre-check": "voorcontrole PO 250202 (S22): ter beoordeling, geen schrijfacties"."""
    words = _WORDS[language]
    counts = Counter(entry.status for entry in entries)
    parts = [f"{counts[kind]} {words['entries'][kind][counts[kind] != 1]}" for kind in ("done", "prepared", "failed") if counts[kind]]
    precheck = f"{words['cart']} " if case_kind == "cart" else ""
    return (f"{precheck}PO {po_number or words['unknown']}{f' ({supplier})' if supplier else ''}: {words['status'][status]}, "
            f"{', '.join(parts) or words['none']}")


# --- the wait for the suppliers' answers ---

def escalate_pending(cases_root: Path, now: datetime, settings: Settings) -> list[tuple[str, str]]:
    """End the wait of each question of ended_waits (aid/messaging) under cases_root at now, with the wait of the settings, and return
    (case id, reason) pairs in the order of the case folders.

    Only a case whose saved result (steps/08_result.json) is waiting is escalated: the result gets a review item without a finding,
    "message bounced" or "no answer in <n> working days" ("1 working day"), the status review and the digest line to match; the
    case's audit.json gets {"case_id", "message_id", "reason", "at"} in its list "escalations", rewritten whole; and
    messages/escalated.jsonl notes the question, so that it is escalated once while a question sent after it waits on its own. A
    result that is closed, in review or failed is left alone, and a case whose run was cut off before its result is left until it
    has one. A case whose files cannot be read (its messages, its case.json, its result or its audit record) is returned as (case id,
    "unreadable: <the class of the error>"), and the others go on."""
    days = settings.ask_wait_working_days
    silent = f"no answer in {days} working {'day' if days == 1 else 'days'}"
    ended, unreadable = ended_waits(cases_root, now, days)
    outcome = [(case_id, f"{UNREADABLE}: {error}") for case_id, error in unreadable]
    for wait in ended:
        try:
            case = load_case(cases_root, wait.case_id)
            path, audit_path = case.root / "steps" / RESULT_FILE, case.root / AUDIT_FILE
            if not path.exists():
                continue
            result = CaseResult.model_validate_json(path.read_text(encoding="utf-8"))
            if result.status != "waiting":
                continue
            record = json.loads(audit_path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError(f"{audit_path} holds no audit record")
        except (OSError, ValueError) as error:
            outcome.append((wait.case_id, f"{UNREADABLE}: {type(error).__name__}"))
            continue
        reason = BOUNCED if wait.bounced else silent
        item = ReviewItem(item_id=f"RI{len(result.review_items) + 1}", finding_id=None, reason=reason, field=None, po_value=None, doc_value=None)
        case.write_step(8, "result", result.model_copy(update={
            "review_items": [*result.review_items, item], "status": "review",
            "digest_line": _digest_line(case.po_number, result.supplier, "review", result.write_entries, settings.language, case.kind)}))
        escalation = {"case_id": wait.case_id, "message_id": wait.message_id, "reason": reason, "at": now.isoformat()}
        _write_json(audit_path, record | {"escalations": [*record.get("escalations", []), escalation]})
        record_escalation(case, reason, now, wait.message_id)    # last: a case whose result is in review is not escalated again anyway
        outcome.append((wait.case_id, reason))
    return sorted(outcome, key=lambda pair: pair[0])


def waits_for_po(result: CaseResult) -> str | None:
    """What the saved result of a case waits for (see the module docstring): "po" when it is in review with no version selected because
    no version of its PO was stored; "version <n>" when it is in review with no version selected because the document quotes version n,
    a whole number, that was not stored; None for any other result, a quoted version that is no whole number among them, which no PO
    PDF can be filed as."""
    choice = result.version
    if result.status != "review" or choice is None or choice.version is not None:
        return None
    if choice.reason == NO_STORED_VERSION:
        return "po"
    quoted = choice.reason.removeprefix(QUOTES_VERSION).partition(",")[0] if choice.reason.startswith(QUOTES_VERSION) else ""
    return f"version {int(quoted)}" if quoted.isdecimal() else None


# --- the answers of the suppliers ---

_MESSAGE_ID = re.compile(r"<([^<>@\s]+)@[^<>\s]*>")      # a Message-ID, "<local part@domain>", whose local part is the aid's message id


def file_answer(cases_root: Path, eml: Path, now: datetime, language: str = "nl") -> str | None:
    """File the e-mail into the case whose sent question it answers (see the module docstring) and return the case id; None when it
    answers no question of the aid (a re-approval request is none), and then nothing is filed. A real reply puts the case's saved
    result in review with the item ANSWERED, one item per reply, and the digest line in `language` (the settings' language, which
    the mailbox passes); a failed result keeps its status, and a case without a result yet keeps the reply until its run ends. A
    bounce and an automatic reply leave the result as it is. The saved result is read and the new one built before the e-mail is
    copied, and the copy is removed when the result cannot be written, so that an e-mail tried again leaves one copy: a result that
    cannot be read raises ValueError with nothing filed. A case whose sent.jsonl cannot be read is passed over, line by line. The
    case's audit.json, when it has one, gets {"file", "filed_under", "at"} in its list "answers", at now."""
    with open(eml, "rb") as f:
        mail = email.message_from_binary_file(f, policy=policy.default)
    named = set()
    for header in ("In-Reply-To", "References"):
        for value in mail.get_all(header) or []:
            named.update(_MESSAGE_ID.findall(str(value)))
    if not named:
        return None
    case_id = _case_of(Path(cases_root), named)
    if case_id is None:
        return None
    case = load_case(cases_root, case_id)
    path = case.root / "steps" / RESULT_FILE
    answered = None                                  # the result with the reply's review item, when there is one to write
    if is_real_reply(eml) and path.exists():
        result = CaseResult.model_validate_json(path.read_text(encoding="utf-8"))
        status = "failed" if result.status == "failed" else "review"
        item = ReviewItem(item_id=f"RI{len(result.review_items) + 1}", finding_id=None, reason=ANSWERED, field=None, po_value=None, doc_value=None)
        answered = result.model_copy(update={
            "review_items": [*result.review_items, item], "status": status,
            "digest_line": _digest_line(case.po_number, result.supplier, status, result.write_entries, language, case.kind)})
    filed = file_reply(case, eml)
    if answered is not None:
        try:
            case.write_step(8, "result", answered)
        except BaseException:
            filed.unlink(missing_ok=True)
            raise
    _note_answer(case, filed, now)
    return case_id


def is_real_reply(eml: Path) -> bool:
    """Whether file_reply files the e-mail under replies/: neither a bounce nor an automatic reply."""
    return not is_bounce(eml) and not is_auto_reply(eml)


def _note_answer(case: Case, filed: Path, now: datetime) -> None:
    """Add the filed answer to the list "answers" of the case's audit.json, rewritten whole; a case without a readable audit record
    is left as it is, since the answer is filed in messages/ all the same."""
    path = case.root / AUDIT_FILE
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(record, dict):
        answer = {"file": filed.name, "filed_under": filed.parent.name, "at": now.isoformat()}
        _write_json(path, record | {"answers": [*record.get("answers", []), answer]})


def _case_of(cases_root: Path, message_ids: set[str]) -> str | None:
    """The id of the first case (in folder order) whose messages/sent.jsonl holds a question (any template but the re-approval
    request) with one of the message ids, or None. A line that cannot be read is passed over, and so is a file."""
    for path in sorted(cases_root.glob(f"*/{MESSAGES}/{SENT_FILE}")):
        try:
            lines = path.read_bytes().split(b"\n")
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line) if line.strip() else None
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("template") != REAPPROVAL and record.get("message_id") in message_ids:
                return path.parents[1].name
    return None


# --- the disposition log ---

_WRITTEN = {"done": "Exact update executed", "prepared": "prepared", "failed": "write failed"}
_LINE_FIELDS = {"po_line_price": "price", "po_line_date": "delivery_date", "po_line_quantity": "quantity"}


def record_dispositions(case: Case, result: CaseResult, case_id: str | None = None) -> Path:
    """Append the aid's own decisions on the case to <case root>/dispositions.csv, the disposition log of evaluation protocol v2
    (section 6.4), with the header of evaluation/templates/dispositions.csv (DISPOSITION_COLUMNS), and return its path. The header
    is written once, on a file that is new or empty.

    case_id is the evaluation's id of the case when the caller gives one (the batch gives the case_id of cases.csv), else the aid's
    folder id; version_selected is the version the aid selected. One row per write entry, in the order of the writes: decision "Exact
    update executed (<rule>)" for a change made in the system, "prepared (<rule>)" for a change prepared for the buyer and "write
    failed (<rule>)" for an entry that wrote nothing, the rule being that of the action. Its field is what was written (price,
    delivery_date or quantity on a PO line, else the kind of the write: article_price, attach_document or set_status) and its line the
    PO line written, if any. Then one row per item of each question to the supplier, decision "ask the supplier (<rule>)", with the
    item's field and PO line, or the document's line where the PO has none (an extra line); a request to approve a PO again is no
    disposition and has no row. The source of a row is the page and line of the document's value of its finding ("page 1, line 8"),
    else its rule, and its note says where it comes from: "folder 250108_20261112T090000; rule R04; finding F1" (no finding for a
    write about the whole case, such as R01's). The items that wait for the buyer get their rows when the buyer decides them."""
    flags = {flag.flag_id: flag for flag in result.findings.flags} if result.findings is not None else {}
    common = {"case_id": case_id or result.case_id, "version_selected": result.version.version if result.version is not None else None}
    rows = []
    for entry in result.write_entries:
        finding_ids = entry.evidence.get("finding_ids", [])
        rows.append(common | {"line_id": entry.op.line_id, "field": _LINE_FIELDS.get(entry.op.kind, entry.op.kind),
                              "decision": f"{_WRITTEN[entry.status]} ({entry.rule_id})", "source": _source(flags, finding_ids, entry.rule_id),
                              "note": _note(result.case_id, entry.rule_id, finding_ids)})
    for message in result.messages:
        if message["template"] == REAPPROVAL:
            continue
        for finding_id, item in zip(message["finding_ids"], message["items"]):
            rows.append(common | {"line_id": item["po_line_ids"] or item["doc_line_ids"], "field": item["field"],
                                  "decision": f"ask the supplier ({message['rule_id']})",
                                  "source": _source(flags, [finding_id], message["rule_id"]), "note": _note(result.case_id, message["rule_id"], [finding_id])})
    path = case.root / DISPOSITION_FILE
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DISPOSITION_COLUMNS, lineterminator="\n")
        if f.tell() == 0:                        # a file opened for appending starts at its end, so 0 means new or empty
            writer.writeheader()
        writer.writerows(rows)
    return path


def _source(flags: dict[str, Flag], finding_ids: list[str], rule_id: str) -> str:
    """Where the document states the value of the first of the findings that has one, as "page 1, line 8"; else the rule."""
    for finding_id in finding_ids:
        flag = flags.get(finding_id)
        if flag is not None and flag.doc_value is not None and flag.doc_value.source is not None:
            return f"page {flag.doc_value.source.page}, line {flag.doc_value.source.line}"
    return rule_id


def _note(folder: str, rule_id: str, finding_ids: list[str]) -> str:
    """Where a row of the disposition log comes from: "folder 250108_20261112T090000; rule R04; finding F1"."""
    return f"folder {folder}; rule {rule_id}" + (f"; finding {', '.join(finding_ids)}" if finding_ids else "")
