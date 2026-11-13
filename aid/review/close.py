"""The close of a case on the review page: close_case carries out the buyer's decisions (aid/review/app.py records them), and undo
reverses one write. The rules are in the docstring of close_case.

A case that waits for its PO (waiting_for_po) is one the runner checks again as a new case once its PO is filed (aid/runner.py,
_recheck_waiting); finish_replaced then finishes the old case, so that it leaves the review page and a decision recorded on it is
never carried out. Only a case with a PO number of its own, the one number of the PO number pattern, waits: cases_waiting_for_po
lists those, for the hint of the review tab and the count on the settings tab. A confirmation opened without a PO number waits for
nothing, as nothing the aid can promise ends its wait, and stays with the buyer, and so does a case whose PO number was the aid's
guess (case.json's po_found_by); the runner still links a case without a number when it can, and checks a guessed case again once
the version it quotes is filed (checked_again, cases_checked_again)."""
import csv
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from aid.audit import AUDIT_FILE
from aid.case import Case, load_case
from aid.comparison import Flag, Uncompared
from aid.execution.by_hand import FOLDER, KINDS, link_put_back
from aid.execution.ops import WriteOp
from aid.execution.writer import LOG_FILE, WriteLog, WriteLogEntry, WriterError, undo_entry
from aid.messaging import (MESSAGES, QUESTIONS, REAPPROVAL, SENT_FILE, HandOverUnconfirmed, SentRecord, question_ends, record_sent, render,
                           render_reapproval)
# BUYER_RULE: the rule id of the buyer's decisions, in the write log and the disposition log
from aid.pipeline import (BUYER_RULE, DISPOSITION_COLUMNS, DISPOSITION_FILE, LOOKS, NOT_APPLIED_LOOK, NOT_SENT, RESULT_FILE, SENT, UNADDRESSED,
                          UNROUTED_WORDS, UNSENT, CaseResult, Deps, ReviewItem, Status, asked_by_the_run, record_dispositions, waits_for_po)
from aid.review.app import CAPTURE_FILE, DECISIONS_FILE, _context, _decision_log, _line, _saved_result, _selected_version, marked_replaced
from aid.routing import CONFIRMED, ORDERED, MessageSpec, findings_by_id
from aid.schema import DocLine
from aid.tryout_mark import TRYOUT_NOT_SENT, mark_for_sending, tryout_case, tryout_writer
# Private helpers of other modules, so that the close writes what they write in the same form: the case folder's JSON, a value as a
# question shows it, and the pipeline's names of a write, its waits, its re-approval items, its digest line and the source and note
# of a disposition row; and the reading of a messages/ folder, so that the questions of a case and those still out come from one
# read of it.
from aid.case import _write_json
from aid.messaging import _wait
from aid.pipeline import _change, _digest_line, _note, _source, _waits_for_the_buyer, _write_name
from aid.routing import _shown

CLOSE_STEP, CLOSE_NAME = 9, "close"              # steps/09_close.json: the records of the closes, the undos and a replacement
REPLACED = "replaced"                            # the action of the record of a case finished as replaced (finish_replaced)
_WRITES = {"price": "po_line_price", "delivery_date": "po_line_date", "quantity": "po_line_quantity"}     # what "confirm" writes
_QUESTIONS = {"quantity": "quantity_unit", "unit": "quantity_unit", "line_presence": "line_missing_extra", "required_info": "missing_info"}
_ASKED_OTHERWISE = "missing_info"               # the question about a price, a delivery date or an uncompared item
_DISPOSITIONS = {"keep": "accept the PO value", "confirm": "accept the supplier's value", "ask": "ask the supplier", "unresolved": "unresolved"}
_ACKNOWLEDGED = "accept"                         # "confirm" or "keep" on an item without a finding
# Why the close did not send a message, besides the kinds of a mailer's own error (MailerError.kind of aid/messaging: "sign_in", "store",
# "unreachable", "refused", "no_password") and of a mailer without a route ("no_mailbox", "settings"): the case's document cannot
# be read, so that nobody can say whether it is a try-out. The record of the message keeps the kind as its "cause", by which the
# page says what to do (aid/review/page.py); its status reads "not sent (<the words of the cause>)".
NO_DOCUMENT = "document"
NO_ADDRESS = "no_address"                        # the cause of a question to a supplier without an address in the settings
_NOT_SENT_WORDS = UNROUTED_WORDS | {NO_DOCUMENT: "document cannot be read"}


def close_case(case: Case, deps: Deps) -> CaseResult:
    """Carry out the buyer's decisions on a case in review and return its result, which is also written back to steps/08_result.json.
    A case that is not in review, or has no result, is refused with ValueError, and so is a case of the capture server that is
    marked as replaced by another (marked_replaced of aid/review/app.py), whose finish may still be missing. The server marks
    from another process and without a lock, so the mark is looked at twice: before the close begins, and again right before the
    result is written back; a case marked meanwhile is refused in the same way and nothing of the close is written. A mark that
    comes after that second look finds the close's result and record written after the replacement's, and the server's next
    reading of the marked case finishes it again (aid/capture_server.py, _was_replaced).

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
      "ask" on the item of a bounce or a silence of a question the run asked itself (asked_by_the_run of aid/pipeline.py) sends that
      question again, its template and its items, gathered with the others of its template (ruling C4 of the clean-up of Task 44).
      A question can be asked again (below). "ask" on a cart is not sent, as decide refuses it.
    - An item that asks the buyer to look in Exact, because the write of a change was made and not verified or its outcome is not
      known (the reasons LOOKS of aid/pipeline.py; the item names the entry), is decided by what he saw there. "confirm": the
      change is in Exact and stays. The entry counts as made, so nothing is written or prepared for its operation, and the rest of
      the case goes on as after any settled item. "keep": the old value must stay. The entry is undone through the writer, as
      the review tab's undo does (undo; a put-back that the writer only prepared is linked on the list "In Exact invoeren", and
      one of a status or an attachment, which is a row of no list, keeps the case in review at this close, whose record names its
      kind under "put_back_by_hand" so that the page tells the buyer to put it back in Exact himself), and
      what waited behind the change is not carried out: the items "not applied: an earlier change waits for a look in Exact"
      write nothing whatever their decision, and the close attaches nothing and sets no status. "ask" is refused on such an item
      by decide.
    Every write goes through deps.writer (for a case made from a try-out through its prepared writer only, aid/tryout_mark.py) with
    the rule B01, the evidence naming the item, the decision and the finding, and is logged in the write log at once. An operation
    that the write log shows made already (not failed, not prepared after the write route failed, not undone; a change made and
    not verified, or not sure, counts as made) is not made again, so that two items
    on one finding write it once and a close after a stopped one repeats nothing. A write that raises WriterError, or comes back
    waiting for the buyer (failed, prepared after the write route failed, made and not verified, or with an outcome that is not
    known), stops the close:
    nothing after it is done, and the result's error names it as the pipeline does ("WriterError: <why> (write: rule B01, ...)").
    When the PO is above the authorization and the close changed a PO line, the approver is asked to approve it again for the
    changes of this close (render_reapproval, as the pipeline's R10 does), even when the close stopped after them. A change of the
    run that asked for a look in Exact is among them once the buyer chose "confirm" on its item: the run sent no request for it,
    and "keep" sends none.

    A message that is not sent. A question or a request that the mailer cannot send is recorded "not sent (<the class of the
    error>)", as the pipeline does, with the kind of its cause ("cause": the mailer's own, MailerError.kind of aid/messaging).
    Nothing is given to a mailer that has no route (the file mailer that mailer_of gives for want of a way to send, or the
    settings file that cannot be read: "not sent (no mailbox connected)" and the like), and nothing is sent about a case whose
    document cannot be read at that moment, since nobody can say then whether it is a try-out ("not sent (document cannot be
    read)"); neither is written as a file. A supplier without an address in the settings gives "not sent (no address for the
    supplier)", with the cause "no_address". In each case nothing is recorded as asked, the case stays in review, and it gets a
    review item without a finding, once: "message not sent", or "no address for the supplier"; a later failure of the same
    question gives that item the reason of the latest one (ruling C7 of the clean-up of Task 44). Step 09 keeps which item belongs
    to which message (the changes of a request under "reapproval_pending", the template of a question under "unsent_questions"),
    and the next close sends the message again (a request with the changes it makes itself). Once it is sent, that review item is
    settled without a decision of its own, and so is the item of a question when no item asks it any more (the buyer decided the
    item otherwise): the saved result marks it "settled", and the review page and the digest leave it out. A mailer that could not
    confirm the hand-over (HandOverUnconfirmed) leaves the message recorded as sent, with
    "unconfirmed" true and the id it carries, and in sent.jsonl: it counts as asked, the case waits, and nothing is sent a second
    time. Nothing about a case made from a try-out is sent (aid/tryout_mark.py): a question or a request of such a case is
    recorded "not sent (try-out document: nothing is sent)" without asking the mailer; the request counts as settled, and a
    question keeps the case in review, its item on "ask" not carried out (decide refuses "ask" on such a case, so only a decision
    written before that refusal comes here).

    A question and its item. The question about an item is the latest one sent that names the item: messages/sent.jsonl keeps
    the items a close's question is about ("item_ids"), so that this is known also after a close that was cut off before its
    record was saved (ruling C2 of the clean-up of Task 44), and the result's message records name them too. It is out while it
    waits for a reply of its own and no escalation covers it, or bounced and was not shown to the buyer yet (outstanding_questions
    of aid/messaging: a reply belongs to the question it names, so the reply to one question does not answer another). While its
    question is out, the item's "ask" stands: a later close leaves it, and "ask" chosen once more on it sends nothing new, also when
    no record of a close shows that it was asked. Once the question has ended (answered, bounced and shown, or without an answer
    in time) the "ask" is over and the item is undecided again, as the page shows it (standing_decisions). A decision made after
    that is a new one, also "ask" again: the close sends the question again, as a new question with a wait of its own. Which
    decision is new is told by its line in decisions.jsonl: each close keeps the number of the last line it read
    ("decisions_read"), and the end of a question keeps the number of the last line at that moment (question_ends of
    aid/messaging: a reply's entry in the audit record, an escalation's line). An "ask" recorded before the item's question ended
    is over with it, also one written while the question was out (a tick on the row, another choice and back; ruling C1 of the
    clean-up of Task 44).

    When every review item is decided "confirm" or "keep", nothing stopped or held the close and no question is open, the case
    closes: on a confirmation with a PO number the document is attached and the status set from Besteld to Bevestigd, as R01 makes
    them (each unless it was made already). A question is open when a question of messages/sent.jsonl that this close did not send
    itself (the run's, or an earlier close's) is still out, whether or not the result's messages hold its record (ruling C2 of the
    clean-up of Task 44: a run cut off behind its question): the supplier may still answer, so the PO is not confirmed, and none
    of the operations that the answer was to allow is made. The status is review when the
    close stopped, a message could not be sent, or an item is undecided (an "ask" that is over among them) or unresolved; else
    waiting when an item is on "ask" or a question is open (escalate_pending then ends its wait as it ends any other, and puts the
    case back in review); else closed. So a case waits only while a question of it is out. When a reply or a bounce filed with the
    case cannot be read at that moment, nobody can say which questions are out: the close then does nothing, the case stays in
    review, and its record says so ("unread_reply").

    The saved result keeps its review items (and gets the items of the messages not sent), the status, the new writes and messages,
    the error and its digest line. Before it is written back the saved result is read again, after the messages were sent and once
    more at the end, and the review items and the message records that another process added since the close began are kept (a
    reply that the runner filed while the close was sending): such an item is undecided, so the case stays in review. The error is
    None when nothing stopped: a close that completes clears it, also the run's error of a failed case that was escalated to
    review, which its audit record keeps. The close is recorded in steps/09_close.json and in the "close" list of the audit record
    (decisions, the items carried out, writes, messages, status, error and the write it stopped at), and in the disposition log:
    first a row per item carried out without a write ("accept the PO value (B01)" for keep, "accept the supplier's value (B01)" for
    confirm, "accept (B01)" for either on an item without a finding, "ask the supplier (B01)", "unresolved (B01)"), then the rows of
    the new writes as record_dispositions gives them ("Exact update executed (B01)", "prepared (B01)", "write failed (B01)"). The
    rows carry the evaluation's case id of the first row of an existing disposition log (the batch writes the id of cases.csv
    there), else the folder id; their note names the folder. The close's record in step 09 says whether a question kept the case
    waiting ("question_open"), which the page names by a fixed label."""
    if not (case.root / "steps" / RESULT_FILE).exists():
        raise ValueError(f"case {case.case_id} has no result: it was not run")
    result = _saved_result(case)
    if result.status != "review":
        raise ValueError(f"case {case.case_id} is not in review: its status is {result.status}")
    if marked_replaced(case.root):
        raise ValueError(f"case {case.case_id} is not in review: another case replaced it")
    return _Close(case, deps, result).run()


def undo(case: Case, entry_id: str, deps: Deps, again: bool = False) -> WriteLogEntry | None:
    """Reverse one entry of the case's write log: undo_entry of aid/execution/writer.py through deps.writer at the clock's time, or for
    a case made from a try-out through the prepared writer only (tryout_writer of aid/tryout_mark.py), with again for an entry undone
    already that is to be undone once more (see undo_entry). An undo made is recorded in steps/09_close.json and in the audit record;
    one that undo_entry refuses (None) records nothing, and its WriterError propagates. On a case of the capture server that is
    marked as replaced by another (marked_replaced of aid/review/app.py) nothing is undone: None, before the writer is asked."""
    if marked_replaced(case.root):
        return None
    writer = tryout_writer(deps.writer, deps.settings) if tryout_case(case) else deps.writer
    made = undo_entry(case.root, entry_id, writer, deps.clock(), again=again)
    if made is not None:
        _add_record(case, {"action": "undo", "at": deps.clock().isoformat(), "entry_id": entry_id, "undo": made.model_dump(mode="json")})
    return made


# --- a case that waits for its PO, and its finish when it is checked again ---

def waiting_for_po(case: Case, result: CaseResult | None = None) -> str | None:
    """What the case waits for when it waits for its PO ("po" or "version <n>"), else None: a case with a PO number of its own, the
    one number of the PO number pattern, that the runner checks again (checked_again). A case opened without a PO number waits for
    nothing, and nor does a case whose PO number was the aid's guess (case.json's po_found_by): it is the buyer's. `result` is the
    case's saved result when the caller has read it already (aid/review/status.py), so that it is not read again."""
    return checked_again(case, result) if case.po_number is not None and case.po_found_by is None else None


def checked_again(case: Case, result: CaseResult | None = None) -> str | None:
    """What the saved result of the case waits for when the runner checks the case again, as waits_for_po of aid/pipeline.py gives it
    ("po" or "version <n>"), else None. That is a confirmation, with or without a PO number, whose saved result waits by waits_for_po
    and on which nothing was done: no line in its write log, no message sent (messages/sent.jsonl), no decision of the buyer
    (decisions.jsonl), no record in steps/09_close.json (a close, an undo, a replacement), and no case of the capture server
    (capture.json). A case whose result or whose records cannot be read is not checked again. `result` is the saved result when the
    caller has read it already; else it is read here."""
    if case.kind != "confirmation" or (case.root / CAPTURE_FILE).exists() or (case.root / MESSAGES / SENT_FILE).exists():
        return None
    try:
        if _written(case.root / LOG_FILE) or _written(case.root / DECISIONS_FILE) or _records(case):
            return None
        return waits_for_po(result if result is not None else _saved_result(case))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def cases_waiting_for_po(cases_root: Path) -> list[tuple[Case, str]]:
    """Each case under cases_root that waits for its PO (waiting_for_po), with what it waits for, in the order of the folders' names."""
    return [(case, waits) for case, waits in cases_checked_again(cases_root) if case.po_number is not None and case.po_found_by is None]


def cases_checked_again(cases_root: Path) -> list[tuple[Case, str]]:
    """Each case under cases_root that the runner checks again (checked_again), with what its result waits for, in the order of the
    folders' names. A folder whose name starts with a dot, or whose case.json cannot be read, is passed over."""
    root, found = Path(cases_root), []
    for path in sorted(root.iterdir()) if root.is_dir() else []:
        if path.name.startswith(".") or not (path / "steps" / RESULT_FILE).is_file():
            continue
        try:
            case = load_case(root, path.name)
        except (OSError, ValueError):
            continue
        if (waits := checked_again(case)) is not None:
            found.append((case, waits))
    return found


def replaced(case: Case) -> bool:
    """Whether the case was finished as replaced: its steps/09_close.json holds the record of a replacement (finish_replaced). A file
    of records that cannot be read counts as none."""
    try:
        return any(isinstance(record, dict) and record.get("action") == REPLACED for record in _records(case))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def finish_replaced(case: Case, by: str, now: datetime, language: str) -> None:
    """Finish a case that the runner checked again as the new case `by`: its saved result gets the status closed, no supplier and the
    digest line to match, in `language`, and keeps everything else; then the record {"action": "replaced", "at": now, "by": the new case
    id} is added to steps/09_close.json and to the audit record. So the review page no longer offers it, close_case refuses it (it is
    not in review) and a decision recorded on it is never carried out, and it counts toward no supplier's first cases (_closed_cases of
    aid/pipeline.py counts the closed cases of a supplier). The result is written first: when the record then cannot be added, the
    case is closed all the same. The runner takes the case out of the digest's list before it calls this (aid/runner.py)."""
    result = _saved_result(case)
    case.write_step(8, "result", result.model_copy(update={
        "status": "closed", "supplier": None,
        "digest_line": _digest_line(case.po_number, None, "closed", result.write_entries, language, case.kind)}))
    _add_record(case, {"action": REPLACED, "at": now.isoformat(), "by": by})


def _written(path: Path) -> bool:
    """Whether the file holds anything but white space."""
    return path.is_file() and bool(path.read_bytes().strip())


class _Waiting(Exception):
    """A write that came back waiting for the buyer; its text is why."""


class _Close:
    """One close of a case (see close_case). It keeps what it did, so that a close that stops is saved with it."""

    def __init__(self, case: Case, deps: Deps, result: CaseResult):
        self.case, self.deps, self.settings, self.result = case, deps, deps.settings, result
        self.writer = tryout_writer(deps.writer, deps.settings) if tryout_case(case) else deps.writer      # a try-out writes prepared only
        self.ctx = _context(case, result)
        self.decisions, self.lines, self.read = _decision_log(case)      # the last decisions, their line numbers, the last line read
        records = _records(case)
        self.done = {}                            # item -> the decision an earlier close carried out last
        self.asked = _asked_before(records)       # item -> the "ask" an earlier close dealt with last (_asked_before)
        self.pending: dict | None = None          # the request for re-approval not sent yet: its ops, findings, items and review item
        self.unsent: dict[str, str] = {}          # template -> the review item of a question of it that could not be sent yet
        self.settled: set[str] = set()            # the review items of messages sent since
        for record in records:
            if record["action"] == "close":
                self.done |= record["carried_out"]
                self.pending = record.get("reapproval_pending")
                self.unsent = dict(record.get("unsent_questions", {}))
                self.settled |= set(record.get("settled", []))
        self.settled_before = set(self.settled)
        self.new_items: list[ReviewItem] = []     # the review items of the messages not sent, added by this close
        self.added_items: list[ReviewItem] = []   # the review items another process added to the saved result since the close began
        self.added_messages: list[dict] = []      # the message records another process added since
        self.log = WriteLog(case.root)
        logged = self.log.entries()
        self.made = _made(logged)
        undone = {entry.undo_of for entry in logged if entry.undo_of is not None and entry.status != "failed"}
        # the entries that ask for a look in Exact and are not undone, by their id, and whether the buyer keeps the old value of one
        self.looks = {entry.entry_id: entry for entry in logged if entry.entry_id not in undone and _waits_for_the_buyer(entry) in LOOKS}
        self.kept = any(item.reason in LOOKS and self.decisions.get(item.item_id, {}).get("decision") == "keep" for item in result.review_items)
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
        self.by_hand: list[str] = []              # the kinds of the changes this close could not put back on a list ("keep" on a look)
        self.writing: WriteOp | None = None
        self.held = False                         # a message not sent, or a question that cannot be asked, keeps the case in review
        self.unread = False                       # a reply filed with the case cannot be read now: the close does nothing
        self.mark: bool | None = None             # the try-out mark of the document, judged when the first message is sent
        self.judged = False
        self.sent: list[SentRecord] = []         # the questions of the case (messages/sent.jsonl), in the order sent
        self.out: set[str] = set()                # the message ids of those that are still out
        self.ends: dict[str, int] = {}            # question -> the last line of decisions.jsonl when it ended (question_ends)
        self.open_question: str | None = None     # the message id of the latest question still out that this close did not send
        self.look()
        self.evaluation_id = _evaluation_id(case)    # the case id of the disposition log's first row, else None

    def run(self) -> CaseResult:
        try:
            if not self.unread:
                self.guarded(self.carry_out)
                self.reapproval()
                if self.error is None:
                    self.questions()
                    self.refresh()                # a reply filed while a message was sent: its item and its answer count
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

    def look(self) -> None:
        """Read the questions of the case and which of them are still out, from messages/sent.jsonl and what is filed with them
        (_questions), and when each that ended did. A reply or a bounce filed with the case that cannot be read at this moment
        leaves that unknown: the close then does nothing (unread), since it can neither confirm the order nor say that a question
        is open. The question that is open is the latest one out that this close did not send itself."""
        try:
            self.sent, self.out, self.ends = _questions(self.case, self.settings.ask_wait_working_days)
        except OSError:
            self.out, self.unread, self.held = set(), True, True
        own = {message.get("message_id") for message in self.messages}
        self.open_question = next((question.message_id for question in reversed(self.sent)
                                   if question.message_id in self.out and question.message_id not in own), None)

    def refresh(self) -> None:
        """Read the saved result again and take what another process added to it since the close began: the review items (a reply
        that the runner filed while the close was sending, say) and the message records. The items this close added itself are
        numbered after them. Then the questions that are out are read again (look)."""
        try:
            saved = _saved_result(self.case)
        except (OSError, ValueError):             # not readable at this moment: the close goes on with what it read at its start
            saved = None
        if saved is not None:
            self.added_items = saved.review_items[len(self.result.review_items):]
            self.added_messages = saved.messages[len(self.result.messages):]
            first = len(self.result.review_items) + len(self.added_items) + 1
            renamed = {item.item_id: f"RI{first + number}" for number, item in enumerate(self.new_items)}
            if any(old != new for old, new in renamed.items()):
                self.new_items = [item.model_copy(update={"item_id": renamed[item.item_id]}) for item in self.new_items]
                self.unsent = {template: renamed.get(item_id, item_id) for template, item_id in self.unsent.items()}
                if self.pending is not None:
                    self.pending = self.pending | {"review_item": renamed.get(self.pending["review_item"], self.pending["review_item"])}
        if not self.unread:
            self.look()

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
            if decision is None:
                continue
            finding = self.by_id.get(item.finding_id) if item.finding_id is not None else None
            if decision["decision"] == "ask":
                if self.dealt_with(item.item_id):
                    continue                      # an earlier close asked: the choice stands while its question is out, and is over after
                asked = self.question_of(item, finding)
                if asked is None or self.case.kind == "cart":
                    self.held = True              # decide() refuses these; a hand-made line cannot be carried out either
                elif self.own_question(item.item_id) in self.out:
                    self.carried[item.item_id] = decision     # chosen once more while the question about the item is out: nothing new to ask
                else:
                    template, question_items, finding_ids = asked
                    self.asks.setdefault(template, []).append((item, finding, question_items, finding_ids))
                continue
            if _same(self.done.get(item.item_id), decision):
                continue
            look = self.looks.get(item.entry_id) if item.reason in LOOKS else None      # the entry the buyer looked at in Exact
            if look is not None and decision["decision"] == "keep":
                self.put_back(look)
            behind = self.kept and item.reason.startswith(NOT_APPLIED_LOOK)           # it waited behind a change that is put back
            ops = self.ops(finding, decision) if decision["decision"] == "confirm" and not behind else []
            evidence = {"item_id": item.item_id, "decision": decision["decision"], "article_update": decision["article_update"],
                        "finding_ids": [item.finding_id] if item.finding_id else [],
                        "findings": [finding.model_dump(mode="json")] if finding is not None else []}
            written = [self.write(op, evidence) for op in ops]
            if look is not None and decision["decision"] == "confirm" and (look.op.kind in _WRITES.values() or look.op.kind == "article_price"):
                self.changes.append((look.op, evidence))                             # it stays: the approver is asked for it now
            if not any(written):                  # nothing to write, or every write made already (a second item on one finding)
                self.rows[item.item_id] = self.row(item, finding, decision["decision"])
            self.carried[item.item_id] = decision

    def question_of(self, item: ReviewItem, finding: Flag | Uncompared | None) -> tuple[str, list[dict], list[str]] | None:
        """What "ask" on the item asks: the template, the items and the finding ids of its question. For an item with a finding, the
        question about that finding; for the item of a bounce or a silence of a question the run asked itself, that question again
        (asked_by_the_run); None for any other item, about which nothing can be asked."""
        if finding is not None:
            return _QUESTIONS.get(finding.field, _ASKED_OTHERWISE), [_question_item(finding)], [item.finding_id]
        record = asked_by_the_run(self.result, item)
        return (record["template"], list(record["items"]), list(record["finding_ids"])) if record is not None else None

    def dealt_with(self, item_id: str) -> bool:
        """Whether the item's last decision is an "ask" that an earlier close dealt with, or that is over with the item's
        question (_dealt_with)."""
        own = self.own_question(item_id)
        ended = self.ends.get(own) if own is not None and own not in self.out else None
        return _dealt_with(self.decisions.get(item_id), self.lines.get(item_id), self.asked.get(item_id), ended)

    def own_question(self, item_id: str) -> str | None:
        """The message id of the latest question sent about the item, by sent.jsonl and by the message records of the result, of
        the closes and of this close (_own_question)."""
        return _own_question(self.sent, [*self.result.messages, *self.added_messages, *self.messages], item_id)

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

    def put_back(self, entry: WriteLogEntry) -> None:
        """"keep" on the item of a change that asked for a look in Exact: undo the entry through the writer, as the review tab's
        undo does (undo), and link a put-back that the writer only prepared on the list "In Exact invoeren". A put-back that was
        only prepared for a status or a file is a row of no list: the buyer puts it back in Exact himself, so the close notes
        its kind (by_hand, which the page says with the sentence of the tab's undo) and the case stays in review this once. An
        entry undone already is left as it is; an undo that wrote nothing, or raises WriterError, stops the close."""
        self.writing = entry.op
        made = undo(self.case, entry.entry_id, self.deps)
        if made is not None and made.status == "failed":
            raise _Waiting(_waits_for_the_buyer(made))
        if made is not None and made.status == "prepared" and made.op.kind in KINDS:
            link_put_back(Path(self.settings.cases_root) / FOLDER, made.entry_id, entry.entry_id, self.deps.clock())
        elif made is not None and made.status == "prepared":
            self.by_hand.append(entry.op.kind)
            self.held = True

    def write(self, op: WriteOp, evidence: dict) -> bool:
        """Make the operation through the writer and log it, unless it was made already (False); a write that does not go through
        stops the close."""
        if _target(op) in self.made:
            return False
        self.writing = op
        entry = self.writer.apply(op, self.case.case_id, BUYER_RULE, evidence, self.deps.clock())
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
        if status in (SENT, NOT_SENT, TRYOUT_NOT_SENT):
            self.pending = None
            if earlier is not None:
                self.settled.add(earlier["review_item"])
            return
        self.pending = {"ops": [op.model_dump(mode="json") for op in ops], "finding_ids": finding_ids, "item_ids": item_ids,
                        "review_item": earlier["review_item"] if earlier else self.add_item(UNSENT)}

    def add_item(self, reason: str) -> str:
        """Add the review item of a message that was not sent, without a finding, and return its id."""
        item_id = f"RI{len(self.items()) + 1}"
        self.new_items.append(ReviewItem(item_id=item_id, finding_id=None, reason=reason, field=None, po_value=None, doc_value=None))
        return item_id

    def questions(self) -> None:
        """The questions gathered, one message per template, its items each once; the items of a question sent, or recorded without
        a mailer, are carried out. A question that could not be sent leaves the review item of the run for it, once, with the
        reason of the latest failure ("message not sent", or "no address for the supplier"); when a later close sends a question of
        that template, or no item asks one any more (the buyer decided the item otherwise), the item is settled."""
        for template in [template for template in self.unsent if template not in self.asks]:
            self.settled.add(self.unsent.pop(template))
        for template, asked in self.asks.items():
            items = list({json.dumps(item, sort_keys=True): item for _, _, question_items, _ in asked for item in question_items}.values())
            finding_ids = list(dict.fromkeys(finding_id for *_, ids in asked for finding_id in ids))
            status = self.send(template, items, finding_ids, [item.item_id for item, *_ in asked])
            if status in (SENT, NOT_SENT):
                for item, finding, *_ in asked:
                    self.carried[item.item_id] = self.decisions[item.item_id]
                    self.rows[item.item_id] = self.row(item, finding, "ask")
                if template in self.unsent:
                    self.settled.add(self.unsent.pop(template))
            elif status != TRYOUT_NOT_SENT:
                reason = UNADDRESSED if status == f"not sent ({UNADDRESSED})" else UNSENT
                if template in self.unsent:
                    self.relabel(self.unsent[template], reason)
                else:
                    self.unsent[template] = self.add_item(reason)

    def relabel(self, item_id: str, reason: str) -> None:
        """Give the review item of a question not sent the reason of its latest failure."""
        def relabelled(items: list[ReviewItem]) -> list[ReviewItem]:
            return [item.model_copy(update={"reason": reason}) if item.item_id == item_id else item for item in items]
        self.result = self.result.model_copy(update={"review_items": relabelled(self.result.review_items)})
        self.added_items, self.new_items = relabelled(self.added_items), relabelled(self.new_items)

    def send(self, template: str, items: list[dict], finding_ids: list[str], item_ids: list[str], made: list[WriteOp] | None = None) -> str:
        """Send a message through the mailer and record it, as the pipeline does: a question to the supplier, or with the changes
        made the request to the approver. Returns its status; one that is not sent holds the case in review. Nothing is sent, and
        nothing written as a file, about a case made from a try-out, about a case whose document cannot be read at this moment
        (mark_for_sending of aid/tryout_mark.py: nobody can say then whether it is a try-out), or through a mailer that has no
        route (its `unrouted`: a file mailer given for want of a way to send, or the settings file that cannot be read). A mailer
        that could not confirm the hand-over (HandOverUnconfirmed) leaves the message recorded as sent, marked "unconfirmed"."""
        if not self.judged:
            self.mark, self.judged = mark_for_sending(self.case), True
        if self.mark:                             # nothing about a case made from a try-out is sent (aid/tryout_mark.py)
            if made is None:
                self.held = True
            return self.record(template, items, finding_ids, item_ids, TRYOUT_NOT_SENT)
        if self.deps.mailer is None:
            return self.record(template, items, finding_ids, item_ids, NOT_SENT)
        mailer = self.deps.mailer
        route = mailer.current() if hasattr(mailer, "current") else mailer     # the mailer of send_via "auto" judges once per message
        if (cause := NO_DOCUMENT if self.mark is None else getattr(route, "unrouted", None)) is not None:
            return self.unsent_for(template, items, finding_ids, item_ids, cause)
        if made is not None:
            message = render_reapproval(made, self.ctx, self.settings).model_copy(update={"case_id": self.case.case_id})
        elif address := self.settings.supplier_emails.get(self.ctx.supplier):
            spec = MessageSpec(template=template, items=[self.with_po_line(item) for item in items])
            message = render(spec, self.case, self.ctx, self.settings, address)
        else:
            self.held = True
            return self.record(template, items, finding_ids, item_ids, f"not sent ({UNADDRESSED})", cause=NO_ADDRESS)
        unconfirmed = {}
        try:
            sent = route.send(message)            # through the route judged: a second look might give the file mailer of no route
        except HandOverUnconfirmed as doubt:      # it may have left: recorded as sent, so that a reply finds its case, and not sent again
            sent, unconfirmed = doubt.record, {"unconfirmed": True}
        except Exception as error:                # whatever stops the mailer: not sent, the case stays in review, the next close sends it
            self.held = True
            return self.record(template, items, finding_ids, item_ids, f"not sent ({type(error).__name__})",
                               error=f"{type(error).__name__}: {error}", cause=getattr(error, "kind", "refused"))
        status = self.record(template, items, finding_ids, item_ids, SENT, message_id=sent.message_id, to=sent.to, subject=sent.subject,
                             sent_at=sent.sent_at, refused_cc=sent.refused_cc, **unconfirmed)
        record_sent(self.case, sent if made is not None else sent.model_copy(update={"item_ids": item_ids}))     # a question: its items
        return status

    def unsent_for(self, template: str, items: list[dict], finding_ids: list[str], item_ids: list[str], cause: str) -> str:
        """Record a message that the close did not give to the mailer, for this cause, and hold the case in review."""
        self.held = True
        return self.record(template, items, finding_ids, item_ids, f"not sent ({_NOT_SENT_WORDS.get(cause, cause)})", cause=cause)

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
        return [*self.result.review_items, *self.added_items, *self.new_items]

    def latest(self, item: ReviewItem) -> str | None:
        """The decision that stands on the item: its last decision, unless that is an "ask" that is over (an earlier close dealt
        with it, or it was recorded before the question about the item ended, and that question is no longer out: the item is
        undecided again). A settled item (the item of a message sent since, or of a question no item asks any more) counts as
        acknowledged whatever was chosen on it, since the page no longer shows it."""
        if item.item_id in self.settled:
            return "confirm"
        decision = self.decisions.get(item.item_id, {}).get("decision")
        if decision == "ask" and item.item_id not in self.carried and self.dealt_with(item.item_id) and self.own_question(item.item_id) not in self.out:
            decision = None
        return decision

    def closes(self) -> bool:
        return not self.held and self.open_question is None and all(self.latest(item) in ("confirm", "keep") for item in self.items())

    def closing_operations(self) -> None:
        """On a confirmation with a PO number, the document attached and the status set, as R01 makes them; neither when the
        buyer keeps the old value of a change that asked for a look in Exact, since they waited behind that change."""
        if self.case.kind != "confirmation" or not self.ctx.po_number or self.kept:
            return
        evidence = {"decision": "close", "item_ids": [item.item_id for item in self.result.review_items], "finding_ids": []}
        self.write(self.op("attach_document", document=self.ctx.document_path), evidence)
        self.write(self.op("set_status", old_value=ORDERED, new_value=CONFIRMED), evidence)

    def status(self) -> Status:
        decided = [self.latest(item) for item in self.items()]
        if self.error is not None or self.held or any(decision in (None, "unresolved") for decision in decided):
            return "review"
        return "waiting" if "ask" in decided or self.open_question is not None else "closed"

    def save(self) -> CaseResult:
        """Write the result back, record the close in step 09 and the audit record, and append its disposition rows. The saved
        result is read once more first (refresh), so that what another process added while the close was at work is kept. Then,
        right before the write, the mark of a replacement is looked at again: a case that was marked as replaced since the close
        began is refused here, before anything is written (see close_case)."""
        self.refresh()
        if marked_replaced(self.case.root):
            raise ValueError(f"case {self.case.case_id} is not in review: another case replaced it")
        status = self.status()
        entries = [*self.result.write_entries, *self.entries]
        items = [item.model_copy(update={"settled": True}) if item.item_id in self.settled and not item.settled else item for item in self.items()]
        result = self.result.model_copy(update={
            "review_items": items, "status": status, "write_entries": entries,
            "messages": [*self.result.messages, *self.added_messages, *self.messages], "error": self.error,
            "digest_line": _digest_line(self.case.po_number, self.result.supplier, status, entries, self.settings.language,
                                       self.case.kind)})
        self.case.write_step(8, "result", result)
        _add_record(self.case, {"action": "close", "at": self.deps.clock().isoformat(), "decisions": self.decisions,
                                "carried_out": self.carried, "write_entries": [entry.model_dump(mode="json") for entry in self.entries],
                                "messages": self.recorded, "status": status, "error": self.error, "stop": self.stop,
                                **({"put_back_by_hand": self.by_hand} if self.by_hand else {}),
                                "reapproval_pending": self.pending, "settled": sorted(self.settled - self.settled_before),
                                "question_open": status == "waiting" and self.open_question is not None,
                                "decisions_read": self.read, "unsent_questions": self.unsent, "unread_reply": self.unread})
        _append_rows(self.case, [self.rows[item.item_id] for item in self.result.review_items if item.item_id in self.rows])
        record_dispositions(self.case, self.result.model_copy(update={"write_entries": self.entries, "messages": []}),
                            case_id=self.evaluation_id)
        return result


def standing_decisions(case: Case, working_days: int) -> dict[str, dict]:
    """The decisions that stand on the review items of a case, by item id, as the page shows them: the last decision of each
    item (decisions.jsonl), without an "ask" that is over. An "ask" is over when a close dealt with it (it sent the question, or
    found one out already), or it was recorded before the item's question ended (ruling C1 of the clean-up of Task 44), and the
    question about the item is no longer out, with the wait of `working_days`: answered, bounced and shown to the buyer, or
    without an answer in time. The item then has no choice, and what the buyer chooses next is a new
    decision, also "ask" once more, which the next close sends as a new question. While the question is out the "ask" stands. A
    case whose records or messages cannot be read at this moment shows its last decisions as they are; a decisions file that
    cannot be read raises ValueError (_decisions of aid/review/app.py)."""
    decisions, lines, _ = _decision_log(case)
    try:
        asked, messages = _asked_before(_records(case)), _saved_result(case).messages
        sent, out, ends = _questions(case, working_days)
    except (OSError, ValueError, KeyError, TypeError):
        return decisions
    standing = {}
    for item_id, decision in decisions.items():
        own = _own_question(sent, messages, item_id)
        ended = ends.get(own) if own is not None and own not in out else None
        if not (_dealt_with(decision, lines.get(item_id), asked.get(item_id), ended) and own not in out):
            standing[item_id] = decision
    return standing


def _asked_before(records: list[dict]) -> dict[str, tuple[dict, int | None]]:
    """For each item, the "ask" that a close dealt with last, by the records of the closes: the decision as that close carried it
    out, and the number of the last line of decisions.jsonl that close had read ("decisions_read"; None for a record made before
    that number was kept)."""
    asked: dict[str, tuple[dict, int | None]] = {}
    for record in records:
        if record["action"] == "close":
            for item_id, decision in record["carried_out"].items():
                if decision["decision"] == "ask":
                    asked[item_id] = (decision, record.get("decisions_read"))
    return asked


def _dealt_with(decision: dict | None, line: int | None, asked: tuple[dict, int | None] | None, ended: int | None = None) -> bool:
    """Whether the last decision of an item, which stands on this line of decisions.jsonl, is an "ask" that is over with the
    item's question or that a close dealt with already. Over: the item's question has ended, and the decision was recorded before
    it did (`ended`, the number of the last line of decisions.jsonl when it ended; None while it is out, or when its end was not
    noted), also when it was written while that question was out (ruling C1 of the clean-up of Task 44). Dealt with
    (_asked_before): the decision was among the lines that close had read. A decision made after both is a new one, also when it
    is "ask" again. For a close that kept no line number, the decision is the one it carried out when the two are the same
    record."""
    if decision is None or decision["decision"] != "ask":
        return False
    if ended is not None and line is not None and line <= ended:
        return True
    if asked is None:
        return False
    carried, read = asked
    return line is not None and line <= read if read is not None else decision == carried


def _own_question(questions: list[SentRecord], messages: list[dict], item_id: str) -> str | None:
    """The message id of the latest question sent about the item, in the order of sent.jsonl: by the lines of sent.jsonl, whose
    lines of a close's question name the items it is about, and by the message records of a result ("item_ids", which a close
    writes too; the questions of the run are about no review item), a record whose question sent.jsonl does not hold coming after
    the others. None when none was sent."""
    named = [message.get("message_id") for message in messages
             if message.get("status") == SENT and message.get("template") in QUESTIONS and item_id in message.get("item_ids", [])]
    order = [question.message_id for question in questions]
    about = {question.message_id for question in questions if item_id in question.item_ids} | set(named)
    asked = [message_id for message_id in [*order, *(name for name in named if name not in set(order))] if message_id in about]
    return asked[-1] if asked else None


def _same(earlier: dict | None, now: dict) -> bool:
    """Whether an earlier close carried out this decision: the same decision and, for "confirm", the same article_update."""
    if earlier is None or earlier["decision"] != now["decision"]:
        return False
    return now["decision"] != "confirm" or earlier["article_update"] == now["article_update"]


def _target(op: WriteOp) -> tuple:
    """What an operation changes, and to what."""
    return op.kind, op.po_number, op.version, op.line_id, op.article_code, op.new_value, op.document


def _made(entries: list[WriteLogEntry]) -> set[tuple]:
    """The operations of the write log that are made: not an undo, not failed or prepared after the write route failed, not
    undone. A change made and not verified, or whose outcome is not known, counts as made: the system may hold it, so no close
    writes or prepares it again."""
    undone = {entry.undo_of for entry in entries if entry.undo_of is not None and entry.status != "failed"}
    return {_target(entry.op) for entry in entries
            if entry.undo_of is None and entry.entry_id not in undone and _waits_for_the_buyer(entry) in (None, *LOOKS)}


def _questions(case: Case, working_days: int) -> tuple[list[SentRecord], set[str], dict[str, int]]:
    """The questions of the case (messages/sent.jsonl, in the order sent), the message ids of those still out with the wait of
    `working_days` (those that wait for a reply of their own and that no escalation covers, and those that bounced and were not
    shown to the buyer yet, since the next pass still brings them to him: outstanding_questions of aid/messaging), and for each
    that ended the last line of decisions.jsonl at its end (question_ends). Nothing for a case that sent nothing. A reply or a
    bounce filed with the case that cannot be read at this moment raises OSError."""
    folder = case.root / MESSAGES
    if not (folder / SENT_FILE).exists():
        return [], set(), {}
    wait = _wait(folder, working_days)
    return wait.questions, {question.message_id for question in (*wait.open(), *wait.bounced())}, question_ends(folder)


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
