"""The writers of the checking aid, the write log and the undo.

One change to the ERP is one WriteOp (aid/execution/ops.py). A Writer applies it and returns the WriteLogEntry that records
it: the value before, the value after, the rule that asked for it and the evidence the rule rested on. The digest shows
these entries and the undo works from them. A writer that cannot make a change raises WriterError.

- PreparedWriter changes nothing in any system. It appends the change to prepared_changes.csv, for the buyer to make by
  hand, and returns an entry with status "prepared". A row names the case, the PO, the line or article, the kind of change,
  the old and the new value and, for an attached or detached file, the document.
- RetryingWriter tries a primary writer a fixed number of times and, when every attempt raised WriterError, has a fallback
  writer take the change instead. The entry it returns is the fallback's, carrying the primary's last error message, so that
  the failure is in the write log and not only in the file of prepared changes. A change the system accepted whose check failed
  (UnverifiedWrite) is never handed to the fallback: when no attempt verifies it, it is logged as made, with the value read
  before the write as its previous value and the failed check as its error. Neither is a change whose write got no answer, or
  no clear one, and that nothing settled since (UnknownWrite): it is logged the same way, with what happened as its error and
  the note in its evidence that its outcome is not known (outcome_not_known).
- WriteLog is the write log of one case, case_root/write_log.jsonl: one JSON line per entry, appended and never rewritten.
  A writer does not append to it: the caller appends each entry a writer returns. undo_entry and undo_case are the caller
  of their own writer, so they append their entries themselves. A line of the log that is not an entry (a crash can cut the
  last one off) is not read around: reading raises a ValueError that names the file and the line.

The undo applies the inverse of a logged entry through a writer and logs the result with undo_of naming the entry. For a value
that was changed in a system (status "done") it writes back the value read before the write, the entry's previous, which need
not be the old value of the operation: that one comes from the document. It never writes a value the system did not hold:
where the field was empty before the write, or nothing was read, a writer into a system is given nothing to write and the
undo is refused with a WriterError, and the prepared writer prepares a row without a new value, for the buyer to put the
field back himself. It reverses a case's entries one at a time, newest first, so that a case that stops half way has logged
each undo it made and a second call carries on with the rest. An entry is undone once (an undo that came back "failed"
reversed nothing, so it does not count), but for the review page's "Terugdraaien in Exact", which undoes an entry a second
time once its first put-back was replaced or dropped and nothing newer stands in the way (undo_entry's again); an entry that
wrote nothing (status "failed") is not undone, and an undo entry is never undone.
"""
import csv
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ValidationError

from aid.execution.ops import WriteOp

LOG_FILE = "write_log.jsonl"
PREPARED_FILE = "prepared_changes.csv"
PREPARED_COLUMNS = ("entry_id", "case_id", "po_number", "version", "line_id", "article_code", "kind", "old_value", "new_value",
                    "document", "rule_id", "timestamp")
UNDO_RULE_ID = "undo"                            # the rule_id of an undo entry
READ_BEFORE = "read_before"                      # the note in an entry's evidence of the value a writer read before its write
OUTCOME, NOT_KNOWN = "outcome", "not known"      # the note in the evidence of a write whose outcome is not known (outcome_not_known)
_INVERSE_KIND = {"attach_document": "detach_document", "detach_document": "attach_document"}


class WriteLogEntry(BaseModel):
    """One write, or one prepared change, as the write log keeps it."""
    entry_id: str                                # a UUID4 in hex, made by the writer, so that ids never collide across runs
    case_id: str
    op: WriteOp
    status: Literal["done", "prepared", "failed"]    # made in the system, left to the buyer, or not written at all
    previous: str | None                         # the value before the change
    new: str | None                              # the value after it
    rule_id: str                                 # the routing rule that asked for the change, or "undo"
    evidence: dict                               # what the rule rested on
    timestamp: str                               # the `now` the caller gave, as now.isoformat()
    undo_of: str | None = None                   # the entry_id of the entry this entry reverses
    error: str | None = None                     # why the primary writer failed, when a fallback writer made this entry; on an entry
                                                 # with status "done": the failed check of a write made and not verified, or what
                                                 # happened to a write whose outcome is not known


class WriterError(Exception):
    """A writer could not make the change; the message says why. RetryingWriter answers it with another attempt, then with its fallback."""


class UnverifiedWrite(WriterError):
    """The system accepted the write and the check after it failed, so the system may hold the change; the message says how the
    check failed. `entry` is the entry of that attempt: status "done", `previous` the value read before the write, and `error` the
    failed check. RetryingWriter answers it with another attempt and, when none verifies the change, returns that entry: the
    change is logged as made and is never handed to the fallback."""

    def __init__(self, message: str, entry: WriteLogEntry):
        super().__init__(message)
        self.entry = entry


class UnknownWrite(WriterError):
    """A write was sent and its outcome is not known: no answer came, or one that says neither made nor refused, and nothing
    has settled it since. The message says what happened in the attempt. `entry` is the entry of the attempt: status "done",
    `previous` the value read before the write, and `error` what happened to the write. A writer raises it after the attempt
    that left the write in doubt and after every later attempt at the operation that fails without settling it, so a plain
    WriterError after it says that the system did not make the write. RetryingWriter returns the entry when the last attempt
    still raised it: the change is logged as made and not verified, with the note that its outcome is not known, and is never
    handed to the fallback."""

    def __init__(self, message: str, entry: WriteLogEntry):
        super().__init__(message)
        self.entry = entry


class Writer(Protocol):
    """Applies one operation and returns its log entry. It raises WriterError when it cannot make the change, and nothing else
    for a failure it expects: RetryingWriter lets any other exception through. A writer into a system raises UnverifiedWrite, a
    WriterError that carries the entry, when the system accepted the change and the check after it failed, and UnknownWrite,
    which carries the entry too, while a write it sent has an outcome that is not known. The evidence goes into the entry and
    so into the write log, which is JSON, so the caller builds it from JSON values: strings, numbers, booleans, None, lists
    and dicts. A writer builds the entry and checks that it can be written as JSON (entry.model_dump_json()) before it makes
    the change, so that a bad entry fails before there is anything to log."""
    name: str

    def apply(self, op: WriteOp, case_id: str, rule_id: str, evidence: dict, now: datetime) -> WriteLogEntry: ...


def _json_line(entry: WriteLogEntry) -> str:
    """The line the write log keeps for the entry: its JSON and a newline. Only the evidence can hold something that is not
    JSON, so that is what the ValueError of a bad entry says, with the change it was for."""
    try:
        return entry.model_dump_json() + "\n"
    except ValueError as exc:                        # pydantic's PydanticSerializationError
        raise ValueError(f"cannot log the entry for rule {entry.rule_id}, {entry.op.kind} on PO {entry.op.po_number}: "
                         f"its evidence is not JSON ({exc})") from exc


def _first_problem(exc: ValidationError) -> str:
    """The first thing pydantic found wrong with a line, as 'field: message', or only the message when it is about the whole line."""
    problem = exc.errors(include_url=False, include_input=False)[0]
    field = ".".join(str(part) for part in problem["loc"])
    return f"{field}: {problem['msg']}" if field else problem["msg"]


class PreparedWriter:
    """Prepares each change instead of making it: one row per change in <out_dir>/prepared_changes.csv."""
    name = "prepared"

    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir)
        self.path = self.out_dir / PREPARED_FILE

    def apply(self, op: WriteOp, case_id: str, rule_id: str, evidence: dict, now: datetime) -> WriteLogEntry:
        """Append the change to the CSV and return its entry: status "prepared", previous and new the old and new value of the
        operation. The folder is created when it is missing and the header is written once, on a file that is new or empty.
        An entry that cannot be written to the write log (evidence that is not JSON) raises ValueError before anything is
        written, and a folder or file that cannot be written raises WriterError: either way no row and no entry are made."""
        entry = WriteLogEntry(entry_id=uuid.uuid4().hex, case_id=case_id, op=op, status="prepared", previous=op.old_value, new=op.new_value,
                              rule_id=rule_id, evidence=evidence, timestamp=now.isoformat())
        _json_line(entry)                            # a bad entry fails here, before the folder or the CSV is touched
        row = {"entry_id": entry.entry_id, "case_id": case_id, "po_number": op.po_number, "version": op.version, "line_id": op.line_id,
               "article_code": op.article_code, "kind": op.kind, "old_value": op.old_value, "new_value": op.new_value,
               "document": op.document, "rule_id": rule_id, "timestamp": entry.timestamp}
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="") as f:
                rows = csv.DictWriter(f, fieldnames=PREPARED_COLUMNS, lineterminator="\n")
                if f.tell() == 0:                # a file opened for appending starts at its end, so 0 means new or empty
                    rows.writeheader()
                rows.writerow(row)
        except OSError as exc:
            raise WriterError(f"cannot write {self.path}: {exc}") from exc
        return entry


class RetryingWriter:
    """Applies each operation through `primary`, and through `fallback` when the primary failed every time.

    `retries` is the number of attempts the primary gets in all, at least 1. Only WriterError counts as a failed attempt: any
    other exception from the primary propagates at once. Every attempt repeats the whole operation, so the primary must be
    safe to repeat. When the last attempt has failed the fallback applies the operation, and the entry it returns is the
    entry of the call, with `error` set to the message of the primary's last failure (the name of its exception class when
    it had no message, so that `error` is never an empty string). If the fallback raises WriterError too, that error
    propagates: neither writer could make the change.

    An attempt that raised UnverifiedWrite was accepted by the system, so the system may hold the change, whatever the other
    attempts failed at. When no attempt returned an entry, the entry of the call is then the entry of the first such attempt,
    whose `previous` is the value read before the change and whose `error` is its failed check (the message of the
    UnverifiedWrite, or the name of its class, when the entry came without one): the change is logged as made, and the fallback
    is not asked, so it is never prepared as well.

    An attempt that raised UnknownWrite left a write whose outcome is not known. The writer raises it again after every later
    attempt that does not settle it, so what the last attempt raised decides: after an UnknownWrite, and with no write the
    system accepted, the entry of the call is the entry of the first UnknownWrite since the last plain failure, with the error
    of the last one (what happened to the write that is in doubt now; its message when its entry came without an error) and
    the note {"outcome": "not known"} added to its evidence (outcome_not_known), and the fallback is not asked; after a plain
    WriterError the system did not make the write, and the fallback prepares the change."""

    def __init__(self, primary: Writer, fallback: Writer, retries: int = 2):
        if retries < 1:
            raise ValueError(f"retries is the number of attempts on the primary writer, so at least 1, not {retries}")
        self.primary = primary
        self.fallback = fallback
        self.retries = retries
        self.name = f"{primary.name}+{fallback.name}"

    def apply(self, op: WriteOp, case_id: str, rule_id: str, evidence: dict, now: datetime) -> WriteLogEntry:
        last_error, unverified, in_doubt = "", None, None
        for _ in range(self.retries):
            try:
                return self.primary.apply(op, case_id, rule_id, evidence, now)
            except WriterError as exc:
                last_error = str(exc) or type(exc).__name__
                if isinstance(exc, UnverifiedWrite) and unverified is None:
                    # the first write the system accepted: its previous is the value before the change, its error the failed check
                    unverified = exc.entry.model_copy(update={"error": exc.entry.error or last_error})
                elif isinstance(exc, UnknownWrite):          # the entry of the first of them, with what the writer says is in doubt now
                    first = in_doubt if in_doubt is not None else exc.entry
                    in_doubt = first.model_copy(update={"error": exc.entry.error or last_error, "evidence": first.evidence | {OUTCOME: NOT_KNOWN}})
                elif not isinstance(exc, UnverifiedWrite):
                    in_doubt = None                      # the writer says that the system did not make the write that was in doubt
        if unverified is not None:                       # made, as far as anyone knows: logged so, and not prepared as well
            return unverified
        if in_doubt is not None:                         # made or not, nobody knows: logged as made and not sure, never prepared
            return in_doubt
        entry = self.fallback.apply(op, case_id, rule_id, evidence, now)
        return entry.model_copy(update={"error": last_error})


class WriteLog:
    """The write log of one case: case_root/write_log.jsonl, one JSON line per entry, appended and never rewritten."""

    def __init__(self, case_root: Path):
        self.path = Path(case_root) / LOG_FILE

    def append(self, entry: WriteLogEntry) -> None:
        """Add the entry as the last line of the log, creating the file on first use. An entry that cannot be written as JSON
        raises ValueError before the file is opened, so it leaves no trace. When the file does not end with a newline (a crash
        cut its last line off), the entry starts a line of its own, so that the damage stays in the cut-off line."""
        line = _json_line(entry).encode("utf-8")
        with open(self.path, "ab+") as f:
            if f.seek(0, os.SEEK_END) > 0:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    line = b"\n" + line
            f.write(line)                            # the file is opened for appending, so this goes to its end

    def entries(self) -> list[WriteLogEntry]:
        """Every entry of the log, in the order they were appended; [] when the log has no file yet. Blank lines are skipped. A
        line that is not an entry (cut off, not UTF-8, not an entry's JSON) raises ValueError naming the file and the line
        number: a damaged log is not read around."""
        try:
            f = open(self.path, "rb")
        except FileNotFoundError:
            return []
        entries = []
        with f:
            # A binary file is split at b"\n" only, as the lines were appended. Text mode would fail on a character cut in two
            # without saying where, and str.splitlines would split at U+2028 and U+0085, which a JSON line may hold.
            for number, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    entries.append(WriteLogEntry.model_validate_json(line))
                except ValidationError as exc:
                    raise ValueError(f"{self.path}, line {number}: not a write-log entry ({_first_problem(exc)})") from exc
        return entries


def inverse(op: WriteOp) -> WriteOp:
    """The operation that reverses `op`: the same operation with its old and new value swapped, so that a price, a date, a
    quantity or a status goes back to its previous value. attach_document and detach_document are each other's inverse and
    keep their document. `op` is not changed."""
    return op.model_copy(update={"kind": _INVERSE_KIND.get(op.kind, op.kind), "old_value": op.new_value, "new_value": op.old_value})


def outcome_not_known(entry: WriteLogEntry) -> bool:
    """Whether the entry is of a write whose outcome is not known: logged as made (status "done"), with the note in its
    evidence that RetryingWriter gives the entry of an UnknownWrite."""
    return entry.status == "done" and entry.evidence.get(OUTCOME) == NOT_KNOWN


def read_before(entry: WriteLogEntry) -> Literal["value", "empty", "nothing"]:
    """What was read before the write of a value that was changed in a system: "value" when the entry's `previous` holds a
    value; "empty" when the field was empty (a blank text as the previous, or none with the read noted in the evidence, as a
    writer notes a null it read); "nothing" when no read is on record."""
    if entry.previous is not None and entry.previous.strip():
        return "value"
    return "empty" if entry.previous is not None or READ_BEFORE in entry.evidence else "nothing"


def undo_entry(case_root: Path, entry_id: str, writer: Writer, now: datetime, again: bool = False) -> WriteLogEntry | None:
    """Reverse one entry of the case's log: apply the inverse of its operation through `writer` with the rule id "undo" and
    the evidence {"undo_of": entry_id}, append the writer's entry to the log with undo_of set to entry_id, and return it. The
    undo of a value that was changed in a system (status "done") writes back the value read before the write, the entry's
    `previous`: the old value of the operation is the document's, which the system need not have held. When the field was
    empty before the write, or nothing was read (read_before), there is no value to write back: a writer into a system is not
    called, and the undo is refused with a WriterError that names the kind, the place and which of the two it is; the
    PreparedWriter prepares the undo without a new value, a row that asks the buyer to put the field back. The
    undo of a prepared change is the plain inverse, the old value its row showed.

    It returns None, and writes and logs nothing, when the id is not in the log, when an undo entry with undo_of naming it
    exists (it was undone already; an undo that came back with status "failed" reversed nothing and does not count), when
    its status is "failed" (nothing was written) or when it is an undo entry itself (an undo is never undone). With again,
    an entry undone already is undone once more: the review page's "Terugdraaien in Exact" asks it when the put-back it
    prepared before can no longer return to the list (newer rows came after it) and those rows no longer ask anything
    (aid/review/hand_list.py). A WriterError from the writer propagates and nothing is logged."""
    log = WriteLog(case_root)
    entries = log.entries()
    entry = next((e for e in entries if e.entry_id == entry_id), None)
    if entry is None or entry.undo_of is not None or entry.status == "failed":
        return None                                  # not in the log, an undo entry, or nothing was written
    if not again and any(e.undo_of == entry_id and e.status != "failed" for e in entries):
        return None                                  # undone already
    back = inverse(entry.op)
    if entry.status == "done" and entry.op.kind not in _INVERSE_KIND:       # a value changed in a system: what it held goes back,
        read = read_before(entry)                                           # not what the document said
        if read != "value" and not isinstance(writer, PreparedWriter):
            where = f", line {entry.op.line_id}" if entry.op.line_id else f", article {entry.op.article_code}" if entry.op.article_code else ""
            why = "the field was empty before the write" if read == "empty" else "no value was read before the write"
            raise WriterError(f"cannot undo {entry.op.kind} on PO {entry.op.po_number}{where}: {why}, so there is no value to write back")
        back = back.model_copy(update={"new_value": entry.previous if read == "value" else None})
    made = writer.apply(back, entry.case_id, UNDO_RULE_ID, {"undo_of": entry_id}, now)
    undo = made.model_copy(update={"undo_of": entry_id})
    log.append(undo)
    return undo


def undo_case(case_root: Path, writer: Writer, now: datetime) -> list[WriteLogEntry]:
    """Reverse every entry of the case's log that undo_entry does not refuse, newest first, and return the undo entries made
    in that order. A second call returns []. If the writer raises WriterError part way, the undos made before it are logged
    and the error propagates; calling again undoes the rest. An undo that comes back "failed" is among the entries returned,
    and the next call tries that entry again."""
    undone = []
    for entry in reversed(WriteLog(case_root).entries()):
        undo = undo_entry(case_root, entry.entry_id, writer, now)
        if undo is not None:
            undone.append(undo)
    return undone
