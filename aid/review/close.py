"""The close of a case on the review page: close_case carries out the buyer's decisions (aid/review/app.py records them), and undo
reverses one write. The rules are in the docstring of close_case."""
import csv
import json
from collections.abc import Callable

from aid.audit import AUDIT_FILE
from aid.case import Case
from aid.comparison import Flag, Uncompared
from aid.execution.ops import WriteOp
from aid.execution.writer import WriteLog, WriteLogEntry, WriterError, undo_entry
from aid.messaging import ESCALATED_FILE, MESSAGES, QUESTIONS, REAPPROVAL, REPLIES, record_sent, render, render_reapproval
from aid.pipeline import (DISPOSITION_COLUMNS, DISPOSITION_FILE, NOT_SENT, RESULT_FILE, SENT, UNADDRESSED, UNSENT, CaseResult, Deps,
                          ReviewItem, Status, record_dispositions)
from aid.review.app import _context, _decisions, _line, _saved_result, _selected_version
from aid.routing import CONFIRMED, ORDERED, MessageSpec, findings_by_id
from aid.schema import DocLine
# Private helpers of other modules, so that the close writes what they write in the same form: the case folder's JSON, a value as a
# question shows it, and the pipeline's names of a write, its waits, its re-approval items, its digest line and the source and note
# of a disposition row.
from aid.case import _write_json
from aid.messaging import _escalated, _holds_files
from aid.pipeline import _change, _digest_line, _note, _source, _waits_for_the_buyer, _write_name
from aid.routing import _shown

BUYER_RULE = "B01"                               # the rule id of the buyer's decisions, in the write log and the disposition log
CLOSE_STEP, CLOSE_NAME = 9, "close"              # steps/09_close.json: the records of the closes and the undos
_WRITES = {"price": "po_line_price", "delivery_date": "po_line_date", "quantity": "po_line_quantity"}     # what "confirm" writes
_QUESTIONS = {"quantity": "quantity_unit", "unit": "quantity_unit", "line_presence": "line_missing_extra", "required_info": "missing_info"}
_ASKED_OTHERWISE = "missing_info"               # the question about a price, a delivery date or an uncompared item
_DISPOSITIONS = {"keep": "accept the PO value", "confirm": "accept the supplier's value", "ask": "ask the supplier", "unresolved": "unresolved"}
_ACKNOWLEDGED = "accept"                         # "confirm" or "keep" on an item without a finding


def close_case(case: Case, deps: Deps) -> CaseResult:
    """Carry out the buyer's decisions on a case in review and return its result, which is also written back to steps/08_result.json.
    A case that is not in review, or has no result, is refused with ValueError.

    Each review item's last decision is carried out once: a later close skips an item whose same decision (for "confirm", with the
    same article_update) an earlier close carried out, and an undecided item waits. In the order of the items:
    - "confirm" on a flag of the price, the delivery date or the quantity of one PO line, whose document value is known, writes that
      value over the PO line's (po_line_price, po_line_date, po_line_quantity, old_value the PO's value); with article_update, a price
      also over the article's (article_price, old_value None) when the routing context knows the line's item code. Any other
      "confirm" (a unit, a line, the required information, an uncompared item, an item without a finding) writes nothing: the buyer
      handles it outside the aid.
    - "keep" and "unresolved" write and send nothing.
    - "ask" is a question to the supplier with the template of the field (quantity and unit quantity_unit, a line
      line_missing_extra, anything else missing_info), one message per template, rendered as the pipeline renders a question and sent
      through deps.mailer to the address the settings give the supplier; without a mailer it is recorded as "not sent (no mailer)".
    Every write goes through deps.writer with the rule B01, the evidence naming the item, the decision and the finding, and is logged
    in the write log at once. An operation that the write log shows made already (not failed, not waiting for the buyer, not undone)
    is not made again, so that two items on one finding write it once and a close after a stopped one repeats nothing. A write that
    raises WriterError, or comes back waiting for the buyer (failed, or prepared after the write route failed), stops the close:
    nothing after it is done, and the result's error names it as the pipeline does ("WriterError: <why> (write: rule B01, ...)").
    When the PO is above the authorization and the close changed a PO line, the approver is asked to approve it again for the
    changes of this close (render_reapproval, as the pipeline's R10 does), even when the close stopped after them. A request the
    mailer cannot send (or that has no mailer to go through the address of) is recorded "not sent (<class>)", as the pipeline does,
    and the case gets a review item "message not sent" without a finding (once) and stays in review: step 09 keeps the changes of
    the request, and the next close sends it again with the changes it makes itself, until it is sent; then that review item is
    settled without a decision of its own.

    When every review item is decided "confirm" or "keep", nothing stopped or held the close and no question is open, the case
    closes: on a confirmation with a PO number the document is attached and the status set from Besteld to Bevestigd, as R01 makes
    them (each unless it was made already). A question is open when the latest question to the supplier in the result's messages (a
    template of messaging.QUESTIONS, status "sent", whether the run or an earlier close sent it) has no reply filed under
    messages/replies/ and its wait was not escalated (messages/escalated.jsonl does not name it): the supplier may still answer, so
    the PO is not confirmed. The status is review when the close stopped, a message could not be sent (no address for the supplier,
    the mailer failed), an item is undecided or unresolved, or an item is left on "ask" from an earlier close while the case's wait
    has ended (messages/escalated.jsonl exists, so that no later escalation would bring the case back); else waiting when an item is
    on "ask" or a question is open (escalate_pending then ends its wait as it ends any other, and puts the case back in review);
    else closed.

    The saved result keeps its review items (and gets the item of a request not sent), the status, the new writes and messages, the
    error and its digest line. The error is None when nothing stopped: a close that completes clears it, also the run's error of a
    failed case that was escalated to review, which its audit record keeps. The close is recorded in steps/09_close.json and in the "close" list of the audit record (decisions, the
    items carried out, writes, messages, status, error and the write it stopped at), and in the disposition log: first a row per item
    carried out without a write ("accept the PO value (B01)" for keep, "accept the supplier's value (B01)" for confirm, "accept
    (B01)" for either on an item without a finding, "ask the supplier (B01)", "unresolved (B01)"), then the rows of the new writes as
    record_dispositions gives them ("Exact update executed (B01)", "prepared (B01)", "write failed (B01)"). The rows carry the
    evaluation's case id of the first row of an existing disposition log (the batch writes the id of cases.csv there), else the
    folder id; their note names the folder. The close's record in step 09 says whether a question kept the case waiting
    ("question_open"), which the page names by a fixed label."""
    if not (case.root / "steps" / RESULT_FILE).exists():
        raise ValueError(f"case {case.case_id} has no result: it was not run")
    result = _saved_result(case)
    if result.status != "review":
        raise ValueError(f"case {case.case_id} is not in review: its status is {result.status}")
    return _Close(case, deps, result).run()


def undo(case: Case, entry_id: str, deps: Deps) -> WriteLogEntry | None:
    """Reverse one entry of the case's write log: undo_entry of aid/execution/writer.py through deps.writer at the clock's time. An
    undo made is recorded in steps/09_close.json and in the audit record; one that undo_entry refuses (None) records nothing, and its
    WriterError propagates."""
    made = undo_entry(case.root, entry_id, deps.writer, deps.clock())
    if made is not None:
        _add_record(case, {"action": "undo", "at": deps.clock().isoformat(), "entry_id": entry_id, "undo": made.model_dump(mode="json")})
    return made


class _Waiting(Exception):
    """A write that came back waiting for the buyer; its text is why."""


class _Close:
    """One close of a case (see close_case). It keeps what it did, so that a close that stops is saved with it."""

    def __init__(self, case: Case, deps: Deps, result: CaseResult):
        self.case, self.deps, self.settings, self.result = case, deps, deps.settings, result
        self.ctx = _context(case, result)
        self.decisions = _decisions(case)
        self.done = {}                            # item -> the decision an earlier close carried out last
        self.pending: dict | None = None          # the request for re-approval not sent yet: its ops, findings, items and review item
        self.settled: set[str] = set()            # the review items of requests sent since
        for record in _records(case):
            if record["action"] == "close":
                self.done |= record["carried_out"]
                self.pending = record.get("reapproval_pending")
                self.settled |= set(record.get("settled", []))
        self.settled_before = set(self.settled)
        self.new_items: list[ReviewItem] = []     # the review item of a request not sent, added by this close
        self.log = WriteLog(case.root)
        self.made = _made(self.log.entries())
        self.by_id = findings_by_id(result.findings) if result.findings is not None else {}
        self.flags = {flag.flag_id: flag for flag in result.findings.flags} if result.findings is not None else {}
        version = _selected_version(case, result)
        self.po_lines: dict[str, DocLine] = {line.line_id: line for line in version.document.lines} if version is not None else {}
        self.entries: list[WriteLogEntry] = []
        self.messages: list[dict] = []            # as the result keeps them
        self.recorded: list[dict] = []            # as the records keep them, with the error of a message not sent
        self.carried: dict[str, dict] = {}        # item -> its decision, carried out by this close
        self.rows: dict[str, dict] = {}           # item -> its row in the disposition log
        self.asks: dict[str, list[tuple[ReviewItem, Flag | Uncompared]]] = {}
        self.changes: list[tuple[WriteOp, dict]] = []     # the value writes made, with their evidence, for the re-approval
        self.error: str | None = None
        self.stop: dict | None = None
        self.writing: WriteOp | None = None
        self.held = False                         # a message not sent, or a question that cannot be asked, keeps the case in review
        self.open_question = _open_question(case, result)       # the message id of a question still open, else None
        self.evaluation_id = _evaluation_id(case)    # the case id of the disposition log's first row, else None

    def run(self) -> CaseResult:
        try:
            self.guarded(self.carry_out)
            self.reapproval()
            if self.error is None:
                self.questions()
                if self.closes():
                    self.guarded(self.closing_operations)
        except Exception as fault:                # a fault the close does not expect: what was done is saved, then it is raised
            self.error = self.error or f"{type(fault).__name__}: {fault}"
            try:
                self.save()
            except Exception as failure:
                raise failure from fault
            raise
        return self.save()

    def guarded(self, step: Callable[[], None]) -> None:
        """Run a step; a write that raises WriterError or waits for the buyer stops the close."""
        try:
            step()
        except (WriterError, _Waiting) as stopped:
            waiting = isinstance(stopped, _Waiting)
            self.error = f"{'' if waiting else type(stopped).__name__ + ': '}{stopped} (write: {_write_name(BUYER_RULE, self.writing)})"
            self.stop = {"write": self.writing.model_dump(mode="json"), "message": str(stopped), "waiting": waiting}

    def carry_out(self) -> None:
        """The decided items in their order: their writes, their rows, and their questions gathered by template."""
        for item in self.result.review_items:
            decision = self.decisions.get(item.item_id)
            if decision is None or _same(self.done.get(item.item_id), decision):
                continue
            finding = self.by_id.get(item.finding_id) if item.finding_id is not None else None
            if decision["decision"] == "ask":
                if finding is None:
                    self.held = True              # decide() refuses this; a hand-made line cannot be carried out either
                else:
                    self.asks.setdefault(_QUESTIONS.get(finding.field, _ASKED_OTHERWISE), []).append((item, finding))
                continue
            ops = self.ops(finding, decision) if decision["decision"] == "confirm" else []
            evidence = {"item_id": item.item_id, "decision": decision["decision"], "article_update": decision["article_update"],
                        "finding_ids": [item.finding_id] if item.finding_id else [],
                        "findings": [finding.model_dump(mode="json")] if finding is not None else []}
            written = [self.write(op, evidence) for op in ops]
            if not any(written):                  # nothing to write, or every write made already (a second item on one finding)
                self.rows[item.item_id] = self.row(item, finding, decision["decision"])
            self.carried[item.item_id] = decision

    def ops(self, finding: Flag | Uncompared | None, decision: dict) -> list[WriteOp]:
        """What "confirm" writes (see close_case)."""
        kind = _WRITES.get(finding.field) if isinstance(finding, Flag) else None
        if kind is None or len(finding.po_line_ids) != 1 or finding.doc_value is None or finding.doc_value.value is None:
            return []
        [line_id], new = finding.po_line_ids, finding.doc_value.value
        ops = [self.op(kind, line_id=line_id, old_value=finding.po_value.value if finding.po_value else None, new_value=new)]
        if kind == "po_line_price" and decision["article_update"] and (code := self.ctx.po_item_code.get(line_id)):
            ops.append(self.op("article_price", article_code=code, new_value=new))
        return ops

    def op(self, kind: str, **fields) -> WriteOp:
        return WriteOp(kind=kind, po_number=self.ctx.po_number, version=self.ctx.version, **fields)

    def write(self, op: WriteOp, evidence: dict) -> bool:
        """Make the operation through the writer and log it, unless it was made already (False); a write that does not go through
        stops the close."""
        if _target(op) in self.made:
            return False
        self.writing = op
        entry = self.deps.writer.apply(op, self.case.case_id, BUYER_RULE, evidence, self.deps.clock())
        self.entries.append(entry)                # kept before the log is written, so that no change is without a record
        self.log.append(entry)
        if (reason := _waits_for_the_buyer(entry)) is not None:
            raise _Waiting(reason)
        self.made.add(_target(op))
        if op.kind in _WRITES.values() or op.kind == "article_price":
            self.changes.append((op, evidence))
        return True

    def row(self, item: ReviewItem, finding: Flag | Uncompared | None, decision: str) -> dict:
        """The disposition row of an item carried out without a write."""
        words = _ACKNOWLEDGED if item.finding_id is None and decision in ("confirm", "keep") else _DISPOSITIONS[decision]
        finding_ids = [item.finding_id] if item.finding_id else []
        return {"case_id": self.evaluation_id or self.result.case_id, "version_selected": self.result.version.version if self.result.version else None,
                "line_id": _line(finding) if finding is not None else "", "field": item.field or "", "decision": f"{words} ({BUYER_RULE})",
                "source": _source(self.flags, finding_ids, BUYER_RULE),
                "note": f"{_note(self.result.case_id, BUYER_RULE, finding_ids)}; item {item.item_id}"}

    def reapproval(self) -> None:
        """The request to approve the PO again for the changes of this close, with those of a request an earlier close could not
        send (see close_case)."""
        earlier = self.pending
        if not self.ctx.po_above_authorization or (earlier is None and not any(op.kind in _WRITES.values() for op, _ in self.changes)):
            return
        ops = [WriteOp.model_validate(op) for op in (earlier["ops"] if earlier else [])] + [op for op, _ in self.changes]
        finding_ids = list(dict.fromkeys([*(earlier["finding_ids"] if earlier else []),
                                          *(f for _, evidence in self.changes for f in evidence["finding_ids"])]))
        item_ids = list(dict.fromkeys([*(earlier["item_ids"] if earlier else []), *(evidence["item_id"] for _, evidence in self.changes)]))
        status = self.send(REAPPROVAL, [_change(op) for op in ops], finding_ids, item_ids, made=ops)
        if status in (SENT, NOT_SENT):
            self.pending = None
            if earlier is not None:
                self.settled.add(earlier["review_item"])
            return
        review_item = earlier["review_item"] if earlier else f"RI{len(self.result.review_items) + 1}"
        if earlier is None:
            self.new_items.append(ReviewItem(item_id=review_item, finding_id=None, reason=UNSENT, field=None, po_value=None, doc_value=None))
        self.pending = {"ops": [op.model_dump(mode="json") for op in ops], "finding_ids": finding_ids, "item_ids": item_ids,
                        "review_item": review_item}

    def questions(self) -> None:
        """The questions gathered, one message per template; the items of a question sent, or recorded without a mailer, are
        carried out."""
        for template, asked in self.asks.items():
            items = [_question_item(finding) for _, finding in asked]
            status = self.send(template, items, [item.finding_id for item, _ in asked], [item.item_id for item, _ in asked])
            if status in (SENT, NOT_SENT):
                for item, finding in asked:
                    self.carried[item.item_id] = self.decisions[item.item_id]
                    self.rows[item.item_id] = self.row(item, finding, "ask")

    def send(self, template: str, items: list[dict], finding_ids: list[str], item_ids: list[str], made: list[WriteOp] | None = None) -> str:
        """Send a message through the mailer and record it, as the pipeline does: a question to the supplier, or with the changes
        made the request to the approver. Returns its status; one that is not sent holds the case in review."""
        if self.deps.mailer is None:
            return self.record(template, items, finding_ids, item_ids, NOT_SENT)
        if made is not None:
            message = render_reapproval(made, self.ctx, self.settings).model_copy(update={"case_id": self.case.case_id})
        elif address := self.settings.supplier_emails.get(self.ctx.supplier):
            spec = MessageSpec(template=template, items=[self.with_po_line(item) for item in items])
            message = render(spec, self.case, self.ctx, self.settings, address)
        else:
            self.held = True
            return self.record(template, items, finding_ids, item_ids, f"not sent ({UNADDRESSED})")
        try:
            sent = self.deps.mailer.send(message)
        except Exception as error:               # whatever stops the mailer, the buyer sends the message
            self.held = True
            return self.record(template, items, finding_ids, item_ids, f"not sent ({type(error).__name__})", error=f"{type(error).__name__}: {error}")
        status = self.record(template, items, finding_ids, item_ids, SENT, message_id=sent.message_id, to=sent.to, subject=sent.subject,
                             sent_at=sent.sent_at, refused_cc=sent.refused_cc)
        record_sent(self.case, sent)
        return status

    def record(self, template: str, items: list[dict], finding_ids: list[str], item_ids: list[str], status: str, error: str | None = None,
               **sent) -> str:
        record = {"rule_id": BUYER_RULE, "template": template, "items": items, "finding_ids": finding_ids, "item_ids": item_ids,
                  "status": status, "timestamp": self.deps.clock().isoformat()} | sent
        self.messages.append(record)
        self.recorded.append(record if error is None else record | {"error": error})
        return status

    def with_po_line(self, item: dict) -> dict:
        """An item of a question with its PO line ids, joined, and the item code and description of the first of them in the
        selected version; an item without a PO line there (an extra line) as it is."""
        line = self.po_lines.get(item["po_line_ids"].partition(", ")[0])
        if line is None:
            return item
        return item | {"line_id": item["po_line_ids"], "item_code": _shown(line.item_code), "description": _shown(line.description)}

    def items(self) -> list[ReviewItem]:
        return [*self.result.review_items, *self.new_items]

    def latest(self, item: ReviewItem) -> str | None:
        """The item's last decision; a review item of a request for re-approval sent since counts as acknowledged."""
        decision = self.decisions.get(item.item_id, {}).get("decision")
        return decision or ("confirm" if item.item_id in self.settled else None)

    def closes(self) -> bool:
        return not self.held and self.open_question is None and all(self.latest(item) in ("confirm", "keep") for item in self.items())

    def closing_operations(self) -> None:
        """On a confirmation with a PO number, the document attached and the status set, as R01 makes them."""
        if self.case.kind != "confirmation" or not self.ctx.po_number:
            return
        evidence = {"decision": "close", "item_ids": [item.item_id for item in self.result.review_items], "finding_ids": []}
        self.write(self.op("attach_document", document=self.ctx.document_path), evidence)
        self.write(self.op("set_status", old_value=ORDERED, new_value=CONFIRMED), evidence)

    def status(self) -> Status:
        decided = [self.latest(item) for item in self.items()]
        wait_ended = (self.case.root / MESSAGES / ESCALATED_FILE).exists()
        stale = wait_ended and any(self.latest(item) == "ask" and item.item_id not in self.carried for item in self.items())
        if self.error is not None or self.held or stale or any(decision in (None, "unresolved") for decision in decided):
            return "review"
        return "waiting" if "ask" in decided or self.open_question is not None else "closed"

    def save(self) -> CaseResult:
        """Write the result back, record the close in step 09 and the audit record, and append its disposition rows."""
        status = self.status()
        entries = [*self.result.write_entries, *self.entries]
        result = self.result.model_copy(update={
            "review_items": self.items(), "status": status, "write_entries": entries, "messages": [*self.result.messages, *self.messages], "error": self.error,
            "digest_line": _digest_line(self.case.po_number, self.result.supplier, status, entries, self.settings.language,
                                       self.case.kind)})
        self.case.write_step(8, "result", result)
        _add_record(self.case, {"action": "close", "at": self.deps.clock().isoformat(), "decisions": self.decisions,
                                "carried_out": self.carried, "write_entries": [entry.model_dump(mode="json") for entry in self.entries],
                                "messages": self.recorded, "status": status, "error": self.error, "stop": self.stop,
                                "reapproval_pending": self.pending, "settled": sorted(self.settled - self.settled_before),
                                "question_open": status == "waiting" and self.open_question is not None})
        _append_rows(self.case, [self.rows[item.item_id] for item in self.result.review_items if item.item_id in self.rows])
        record_dispositions(self.case, self.result.model_copy(update={"write_entries": self.entries, "messages": []}),
                            case_id=self.evaluation_id)
        return result


def _same(earlier: dict | None, now: dict) -> bool:
    """Whether an earlier close carried out this decision: the same decision and, for "confirm", the same article_update."""
    if earlier is None or earlier["decision"] != now["decision"]:
        return False
    return now["decision"] != "confirm" or earlier["article_update"] == now["article_update"]


def _target(op: WriteOp) -> tuple:
    """What an operation changes, and to what."""
    return op.kind, op.po_number, op.version, op.line_id, op.article_code, op.new_value, op.document


def _made(entries: list[WriteLogEntry]) -> set[tuple]:
    """The operations of the write log that are made: not an undo, not waiting for the buyer, not undone."""
    undone = {entry.undo_of for entry in entries if entry.undo_of is not None and entry.status != "failed"}
    return {_target(entry.op) for entry in entries
            if entry.undo_of is None and entry.entry_id not in undone and _waits_for_the_buyer(entry) is None}


def _open_question(case: Case, result: CaseResult) -> str | None:
    """The message id of the latest question to the supplier that the result's messages hold as sent, when no reply is filed under
    messages/replies/ and messages/escalated.jsonl does not name it; else None (see close_case). Any reply filed to the case counts as
    the answer to its latest question, as ended_waits counts it: a reply is not matched to a message."""
    sent = [message for message in result.messages if message.get("template") in QUESTIONS and message.get("status") == SENT]
    if not sent:
        return None
    folder = case.root / MESSAGES
    latest = sent[-1].get("message_id")
    if _holds_files(folder / REPLIES) or latest in _escalated(folder / ESCALATED_FILE):
        return None
    return latest


def _evaluation_id(case: Case) -> str | None:
    """The case id of the first data row of the case's disposition log, which the batch writes with the evaluation's id; None
    without the file or without a row."""
    path = case.root / DISPOSITION_FILE
    if not path.exists():
        return None
    with open(path, encoding="utf-8", newline="") as f:
        first = next(csv.DictReader(f), None)
    return (first or {}).get("case_id") or None


def _question_item(finding: Flag | Uncompared) -> dict[str, str]:
    """The item of a question about a finding in plain strings, as the routing gives them; an uncompared item has no values."""
    flag = finding if isinstance(finding, Flag) else None
    return {"po_line_ids": ", ".join(finding.po_line_ids), "doc_line_ids": ", ".join(finding.doc_line_ids), "field": finding.field,
            "po_value": _shown(flag.po_value) if flag else "", "doc_value": _shown(flag.doc_value) if flag else ""}


def _records(case: Case) -> list[dict]:
    try:
        return case.read_step(CLOSE_NAME)["records"]
    except FileNotFoundError:
        return []


def _add_record(case: Case, record: dict) -> None:
    """Add a record of a close or an undo to steps/09_close.json and to the "close" list of the case's audit record."""
    case.write_step(CLOSE_STEP, CLOSE_NAME, {"records": [*_records(case), record]})
    path = case.root / AUDIT_FILE
    audit = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    _write_json(path, audit | {"close": [*audit.get("close", []), record]})


def _append_rows(case: Case, rows: list[dict]) -> None:
    """Append rows to the disposition log, with its header on a file that is new or empty, as record_dispositions writes it."""
    if not rows:
        return
    with open(case.root / DISPOSITION_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DISPOSITION_COLUMNS, lineterminator="\n")
        if f.tell() == 0:
            writer.writeheader()
        writer.writerows(rows)
