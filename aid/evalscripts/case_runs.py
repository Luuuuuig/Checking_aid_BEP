"""Read-only access to the aid's case folders, for the records of the evaluation (aid/evalscripts/records.py).

The evaluation works with anonymised case ids; the aid names a case folder after the purchase order number and the time of opening.
This module is the one place where the two meet. It reads the case list, the key between a case id and its purchase order, finds
the folders that hold the runs of a case, and gives of a run what the records need. It writes nothing, anywhere, and it calls no
writer of the aid.

The case list (read_case_list) is a CSV with the columns case_id and po_number and, optionally, kind (confirmation, the default, or
cart), month, folder and document. The cases.csv of the batch (aid/batch.py: case_id, document, po_number, kind) is a valid case
list. It stays in restricted storage: nothing of it but the case id goes into an output. A file as a spreadsheet saves it is read
(read_table): UTF-8 with or without a byte order mark, else the Windows code page; commas or semicolons, by its first line; cells
without the spaces around them; rows that are empty in every cell passed over. A list that cannot be the key is refused with a
sentence (Refusal, a ValueError): a missing column, an empty case id, a case id twice, a kind that does not exist, one purchase
order and kind under two case ids unless each names its folder, and one folder under two case ids.

The runs of a case (folders, runs_of). The aid opens a new folder for the same input files when a case is run again: the runner
does so for a case that waited for its purchase order, and finishes the first folder as replaced (aid/runner.py, _recheck_waiting;
finish_replaced of aid/review/close.py). So the folders of a cases root are grouped by document, the kind and the sha256 of the
input files as case.json names them, and a group is the runs of one document, in the order they were opened. A second
confirmation of the same purchase order is another document, not a run: when the folders of a purchase order hold more than one
document the case list has to name the folder, and nothing is guessed. The run that counts for the raw output (counted) is the
first that was not replaced because it waited for its purchase order; an output names it as "run 2 of 2", never by its folder.

What a run gives. Its unreviewed output (output_of) is the payloads of the steps version, entered, match, compare and result in
the steps of audit.json, and nothing else: the close of the review page, an undo, a supplier's answer and an escalation change
steps/08_result.json and add their own lists to the audit record, and leave its steps as the run wrote them. Its figures
(figures_of) are the seconds, the call attempts, the cost and the kind of reader of that record. The present state of its folder
(state_of) is the one the review page shows, with who closed it. Only a folder that was run again in place has lost its first
output: that writes a new audit record and leaves steps/09_close.json, whose records the new audit record then lacks.

Reading only. Every file of a case folder is read with one Path.read_bytes() and parsed from memory, and none is read twice, so
that no handle on it stays open: on Windows the aid's own write to a file fails while another process has the file open, as the
aid replaces a file as a whole and does not try again (aid/case.py, _write_json). A file that cannot be read at that moment is
passed over and counted (Folders.unreadable, Run.unread), by its name inside the folder and never by the folder's. The records
are not made at all while the aid runs on the data folder (aid_running), unless the person at the keyboard says so. Only the
present state is told by the aid's own reading (case_states of aid/review/status.py), which opens for reading only as well.

Identifying text. No sentence of this module that can reach an output names a purchase order, a case folder, a supplier or a
document. holds_identifier tells whether a text that the researcher typed does, so that the records withhold it (WITHHELD).
snapshot gives the tree under a folder as the tests compare it before and after a command that must leave it as it was.
"""
import csv
import hashlib
import io
import json
import os
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ValidationError

from aid.audit import AUDIT_FILE, TOTAL, WALL_SECONDS
from aid.comparison import Findings
from aid.matching import MatchResult
from aid.pipeline import CaseResult, waits_for_po
from aid.review.app import marked_replaced
from aid.review.close import CLOSE_NAME, CLOSE_STEP, REPLACED
from aid.review.status import START_STATE, case_states
from aid.routing import NO_VERSION
from aid.versioning import VersionChoice

KINDS = ("confirmation", "cart")                 # the kinds of a case (aid/case.py); the first is the default of the case list
CASE_LIST_COLUMNS = ("case_id", "po_number")     # the columns a case list cannot do without
CLOSE_FILE = f"steps/{CLOSE_STEP:02d}_{CLOSE_NAME}.json"      # the records of the closes, the undos and a replacement of a folder
# Why a case has no runs (CaseRuns.reason). No sentence names a folder or a purchase order.
NO_RUN = "no run of the aid found"
SEVERAL = "{n} documents for this purchase order: name the folder in the case list"
FOLDER_MISSING = "the folder named in the case list is no case folder that can be read"
OTHER_KIND = "the folder named in the case list holds a case of another kind"
DOCUMENT_UNREAD = "the document named in the case list cannot be read now"
# The notes on the run that counts (Counted.note).
WAITED = "run {n} waited for its purchase order"
NO_LATER_RUN = "no later run found"
# Why the first output of a run is not kept (Output.reason), and what a run lacks that has no raw output (Output.lacks).
NOT_KEPT = "run again in the same folder (or no audit record): the first run's output is not kept"
CLOSES_UNREAD = "the records of the closes cannot be read: whether the first run's output is kept cannot be told"
OTHER_FORM = "the audit record does not have the form this copy of the program reads: no output is taken from it"
NO_OUTPUT = "no output"                          # the run failed before anything was compared
FIRST_NOT_KEPT = "first output not kept"
SKIPPED = "skipped"                              # the key of the payload of a step the run passed over (aid/pipeline.py)
# The remarks on the figures of a run (Figures.remarks).
NO_AUDIT = "no audit record"
RUN_AGAIN = "run again in the same folder: the figures are those of the last run"
LOCAL_CLOCK = "seconds by a clock without offset"
SUM_OF_STEPS = "seconds: sum of steps"
SECONDS_UNKNOWN = "seconds unknown"
COST_UNKNOWN = "cost unknown for {n} call{s}"
MODEL = "model:"                                 # the start of the source of a decider's answer that a model gave (aid/decider.py)
CLOSED_BY = {"closed_itself": "rule", "closed_by_you": "buyer"}      # who closed a case, by its state on the review page
RUNNING_WITHIN = 30.0                            # seconds: a heartbeat of the launcher younger than this is an aid that runs
AID_RUNS = "the aid runs on this data folder: stop it first, or give --while-running"     # the refusal of the records while it does
# What the records say when nothing above the cases root tells whether the aid runs (aid_checkable).
UNCHECKED_RUNNING = ("No settings file stands beside the cases folder, so whether the aid runs could not be checked: stop the aid "
                     "before log and sheet.")
SETTINGS_FILE = "settings.json"                  # the settings file of a data folder, beside the cases root (settings.example.json)
WITHHELD = "[withheld]"                          # what a record holds in place of a free text that holds identifying text
SHORTEST = 3                                     # a name or number shorter than this tells too little to withhold a text for


class Refusal(ValueError):
    """An input that the records cannot be made from. Its text is the sentence for the person at the keyboard: it names the file by
    its base name, the row and the case id, and never a purchase order number or a case folder."""


@dataclass(frozen=True)
class Table:
    """A CSV file as read_table read it."""
    name: str                                    # the base name of the file
    columns: list[str]                           # the column titles of its first row, in lower case
    rows: list[tuple[int, dict[str, str]]]       # each row that holds anything, with its number as a spreadsheet counts it


@dataclass(frozen=True)
class ListedCase:
    """One row of the case list: the key between the anonymised id of a case and what the aid knows it by."""
    case_id: str
    po_number: str                               # "" when the case list gives none
    kind: str                                    # "confirmation" or "cart"
    month: str
    folder: str                                  # the name of a case folder of the case, when the case list names one
    document: str                                # the supplier's document as the case list names it, when it does
    base: Path = Path(".")                       # the folder of the case list, from which a document is looked for (aid/batch.py)

    @property
    def document_path(self) -> Path | None:
        """Where the document of the case list lies: its path from the folder of the case list, or as it stands when it is
        absolute; None when the case list names no document."""
        return self.base / self.document if self.document else None


def read_table(path: Path) -> Table:
    """Read a CSV file as a spreadsheet saves it (see the module docstring), with one read of its bytes. Raises Refusal for a file
    that is not there, that another program holds, or that is no text."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise Refusal(f"{path.name} does not exist") from None
    except PermissionError:
        raise Refusal(f"{path.name} is open in another program (Excel?): close it and run again") from None
    except OSError as error:
        raise Refusal(f"{path.name} cannot be read ({type(error).__name__})") from None
    text = _decoded(data, path.name)
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = ";" if first.count(";") > first.count(",") else ","
    columns, rows = [], []
    for number, cells in enumerate(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter), start=1):
        cells = [cell.strip() for cell in cells]                    # str.strip takes a non-breaking space off as well
        if not any(cells):
            continue
        if not columns:
            columns = [cell.lower() for cell in cells]
        else:
            rows.append((number, dict(zip(columns, cells + [""] * (len(columns) - len(cells))))))
    return Table(path.name, columns, rows)


def _decoded(data: bytes, name: str) -> str:
    """The bytes of a CSV file as text: UTF-8 with or without a byte order mark, else the Windows code page, in which a spreadsheet
    saves a plain CSV. Raises Refusal for bytes that are neither."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise Refusal(f"{name} is not text in UTF-8: save the file as CSV UTF-8")


def read_case_list(path: Path) -> list[ListedCase]:
    """The cases of the case list at `path`, in its order (see the module docstring). Raises Refusal, with the sentence to show, for
    a file that cannot be read and for a list that cannot be the key between the case ids and the purchase orders."""
    table = read_table(path)
    for column in CASE_LIST_COLUMNS:
        if column not in table.columns:
            raise Refusal(f"{table.name} has no column {column}: a case list has the columns {' and '.join(CASE_LIST_COLUMNS)}")
    cases, rows = [], {}
    for number, row in table.rows:
        case_id, kind = row["case_id"], row.get("kind", "").lower() or KINDS[0]
        if not case_id:
            raise Refusal(f"{table.name}: row {number} has no case id")
        if case_id in rows:
            raise Refusal(f"{table.name}: case id {case_id} stands twice (rows {rows[case_id]} and {number})")
        if kind not in KINDS:
            raise Refusal(f"{table.name}: row {number} (case {case_id}) has the kind {row['kind']!r}: it is {' or '.join(KINDS)}")
        rows[case_id] = number
        cases.append(ListedCase(case_id, row["po_number"], kind, row.get("month", ""), row.get("folder", ""),
                                row.get("document", ""), Path(path).parent))
    _require_one_key(table.name, cases)
    return cases


def _require_one_key(name: str, cases: list[ListedCase]) -> None:
    """Refuse a case list in which two case ids would be given the same runs: one purchase order and kind under two ids of which
    one names no folder, or one folder under two ids."""
    orders: dict[tuple[str, str], list[ListedCase]] = {}
    folders: dict[str, ListedCase] = {}
    for case in cases:
        if case.folder:
            if case.folder in folders:
                raise Refusal(f"{name}: cases {folders[case.folder].case_id} and {case.case_id} name the same folder")
            folders[case.folder] = case
        if case.po_number:
            orders.setdefault((case.po_number, case.kind), []).append(case)
    for same in orders.values():
        if len(same) > 1 and not all(case.folder for case in same):
            raise Refusal(f"{name}: cases {same[0].case_id} and {same[1].case_id} have the same purchase order and kind: "
                          "name the folder of each in the column folder")


# --- the folders of the cases root, and the runs of a case ---

@dataclass(frozen=True)
class Folder:
    """A case folder as its case.json describes it. Its name holds the purchase order number: it is never written to an output."""
    name: str
    path: Path
    kind: str
    po_number: str | None                        # None for a case opened without a purchase order number
    opened_at: datetime                          # with an offset: a time written without one is local time
    files: tuple[tuple[str, str], ...]           # the name and the sha256 of each input file

    @property
    def document(self) -> tuple[str, tuple[str, ...]]:
        """What the folder is a run of: its kind and the sha256 of its input files, sorted. Two folders with the same document
        hold two runs of it."""
        return self.kind, tuple(sorted(sha256 for _, sha256 in self.files))


@dataclass(frozen=True, eq=False)
class Run:
    """One run of the aid: a case folder with its audit record and its records of closes, each read once."""
    folder: Folder
    audit: dict | None                           # audit.json; None when it is missing or cannot be read as an audit record
    closes: tuple[dict, ...] | None              # the records of steps/09_close.json, () without the file; None when unreadable
    unread: tuple[tuple[str, str], ...]          # the files that could not be read now: the name in the folder, the error's class

    def payload(self, step: str) -> dict | None:
        """The output of the step of this name as the audit record holds it; None when the record holds no such step."""
        steps = self.audit["steps"] if self.audit is not None else []
        return next((entry["payload"] for entry in reversed(steps) if entry["name"] == step), None)


@dataclass(frozen=True)
class Folders:
    """The case folders of a cases root, read once (folders)."""
    root: Path
    read: tuple[Folder, ...]                     # every folder whose case.json could be read, in the order of the names
    unreadable: int                              # the folders with a case.json that could not be read, which are passed over
    # the runs read so far, by folder name, so that no file of a folder is read twice
    _runs: dict[str, Run] = field(default_factory=dict, repr=False, compare=False)

    def run(self, folder: Folder) -> Run:
        """The run of the folder, read at the first call for it."""
        if folder.name not in self._runs:
            self._runs[folder.name] = _run(folder)
        return self._runs[folder.name]


@dataclass(frozen=True)
class CaseRuns:
    """The runs of a case of the case list (runs_of)."""
    runs: tuple[Run, ...]                        # in the order they were opened; none when the reason says why
    documents: int                               # how many documents the case's folders hold: 1 when it has runs
    reason: str                                  # why the case has no runs; "" when it has


def folders(cases_root: Path) -> Folders:
    """Every case folder under the cases root: each folder with a case.json, read with one read of that file. A case.json that
    cannot be read now, or that does not hold what a case is told by (the kind, the purchase order number, the time of opening and
    the input files with their sha256), is counted and its folder passed over. A cases root that is not there has no folders."""
    root = Path(cases_root)
    try:
        paths = sorted(path for path in root.iterdir() if path.is_dir())
    except OSError:
        paths = []
    read, unreadable = [], 0
    for path in paths:
        try:
            data = (path / "case.json").read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            continue                             # no case: a folder of the aid's own, such as the prepared changes
        except OSError:
            unreadable += 1
            continue
        try:
            read.append(_folder(path, json.loads(data)))
        except (ValueError, KeyError, TypeError, OverflowError):
            unreadable += 1
    return Folders(root, tuple(read), unreadable)


def _folder(path: Path, meta) -> Folder:
    """The folder of a case.json; ValueError, KeyError or TypeError for one that does not hold what a case is told by."""
    kind, po_number = meta["kind"], meta["po_number"]
    files = tuple((entry["name"], entry["sha256"]) for entry in meta["files"])
    texts = [kind, *(text for file in files for text in file)]
    if not all(isinstance(text, str) for text in texts) or not isinstance(po_number, str | None):
        raise ValueError("not the case.json of a case")
    return Folder(path.name, path, kind, po_number, _with_offset(datetime.fromisoformat(meta["opened_at"])), files)


def _with_offset(moment: datetime) -> datetime:
    """A time of the aid's records with an offset: one written without an offset is the local time of the PC, as the runner reads
    it (aid/runner.py, _identity), so that any two compare."""
    return moment.astimezone() if moment.utcoffset() is None else moment


def _run(folder: Folder) -> Run:
    """The run of a folder: its audit record and its records of closes, each with one read of the file."""
    unread: list[tuple[str, str]] = []
    audit = _read_json(folder.path, AUDIT_FILE, unread)
    if not (isinstance(audit, dict) and isinstance(audit.get("steps"), list)
            and all(isinstance(step, dict) and isinstance(step.get("name"), str) and "payload" in step for step in audit["steps"])):
        audit = None
    closes = _read_json(folder.path, CLOSE_FILE, unread, missing={"records": []})
    records = closes.get("records") if isinstance(closes, dict) else None
    known = isinstance(records, list) and all(isinstance(record, dict) for record in records)
    return Run(folder, audit, tuple(records) if known else None, tuple(unread))


def _read_json(folder: Path, name: str, unread: list[tuple[str, str]], missing=None):
    """The JSON of a file of a case folder, read with one read of its bytes and parsed from memory, so that no handle on the file
    stays open; `missing` for a file that is not there, None for one that holds no JSON, and None for one that cannot be read now,
    which is added to `unread` by its name in the folder and the class of the error."""
    try:
        data = (folder / name).read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return missing
    except OSError as error:
        unread.append((name, type(error).__name__))
        return None
    try:
        return json.loads(data)
    except ValueError:
        return None


def runs_of(case: ListedCase, found: Folders) -> CaseRuns:
    """The runs of a case of the case list among the folders of the cases root, in the order they were opened (then by name). The
    folders are grouped by document (Folder.document): the aid opens a new folder for the same files when a case is run again, so
    a group is the runs of one document. The group of the case is that of the folder the case list names; else, when the document
    it names is there, the group that holds a file with the document's sha256; else the groups in which a folder has the case's
    purchase order number and kind. More than one group is several documents for one purchase order, between which nothing is
    guessed: the case then has no runs and the reason says so, as it does for a case of which no folder is found."""
    groups: dict[tuple, list[Folder]] = {}
    for folder in found.read:
        groups.setdefault(folder.document, []).append(folder)
    if case.folder:
        named = next((folder for folder in found.read if folder.name == case.folder), None)
        if named is None or named.kind != case.kind:
            return CaseRuns((), 0, FOLDER_MISSING if named is None else OTHER_KIND)
        keys = [named.document]
    else:
        try:
            data = case.document_path.read_bytes() if case.document_path is not None else None
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            data = None
        except OSError:
            return CaseRuns((), 0, DOCUMENT_UNREAD)
        if data is not None:
            digest = hashlib.sha256(data).hexdigest()
            keys = [key for key in groups if key[0] == case.kind and digest in key[1]]
        else:
            keys = [key for key, members in groups.items()
                    if any(case.po_number and (folder.po_number, folder.kind) == (case.po_number, case.kind) for folder in members)]
    if len(keys) != 1:
        return CaseRuns((), len(keys), SEVERAL.format(n=len(keys)) if keys else NO_RUN)
    members = sorted(groups[keys[0]], key=lambda folder: (folder.opened_at, folder.name))
    return CaseRuns(tuple(found.run(folder) for folder in members), 1, "")


# --- the run that counts, and its unreviewed output ---

@dataclass(frozen=True)
class Counted:
    """The run of a case that counts for the raw output (counted)."""
    run: Run | None                              # None when no run counts
    label: str                                   # "run 2 of 2": which run it is, never a folder
    note: str                                    # the runs before it that waited for their purchase order; "" when none did


@dataclass(frozen=True)
class Output:
    """The unreviewed output of a run (output_of): the payloads of its audit record as they stand, and the same as the aid's own
    models. Without a kept output everything but the reason is None."""
    kept: bool                                   # False: what the audit record holds is not the first output of the folder
    reason: str                                  # why not; "" when it is kept
    status: str | None = None                    # the run's own status: that of the result payload
    version: dict | None = None                  # the payloads, None for a step the run did not reach or did not record
    entered: dict | None = None
    match: dict | None = None
    compare: dict | None = None
    result: dict | None = None
    choice: VersionChoice | None = None          # the version payload
    pairs: MatchResult | None = None             # the match payload; None also when the step was passed over
    findings: Findings | None = None             # the compare payload; None also when the step was passed over
    case_result: CaseResult | None = None        # the result payload

    @property
    def lacks(self) -> str:
        """What stands in the way of a raw output: "" when the run compared the document, "no version selected" when it selected
        no version, "no output" when it failed before the comparison, and "first output not kept"."""
        if not self.kept:
            return FIRST_NOT_KEPT
        if self.findings is not None:
            return ""
        return NO_VERSION if self.choice is not None and self.choice.version is None else NO_OUTPUT


def counted(runs: Sequence[Run]) -> Counted:
    """The run that counts among the runs of a case, in their order: the first that was not replaced because it waited for its
    purchase order. A run whose own result waits for its purchase order (waits_for_po of aid/pipeline.py on the result payload of
    its audit record) and whose folder was finished as replaced selected no version only because the order was not filed yet: the
    note names it and the next run counts."""
    notes = []
    for number, run in enumerate(runs, start=1):
        result = _model(CaseResult, run.payload("result"))
        if result is not None and waits_for_po(result) is not None and _replaced(run):
            notes.append(WAITED.format(n=number))
            continue
        return Counted(run, f"run {number} of {len(runs)}", "; ".join(notes))
    return Counted(None, "", "; ".join(notes + [NO_LATER_RUN]) if notes else "")


def _replaced(run: Run) -> bool:
    """Whether the folder of the run was finished as replaced: its steps/09_close.json holds the record of a replacement."""
    return any(record.get("action") == REPLACED for record in run.closes or ())


def output_of(run: Run) -> Output:
    """The unreviewed output of a run: the payloads of the steps version, entered, match, compare and result in the steps of its
    audit record, and nothing else. A close, an undo, a supplier's answer and an escalation leave those steps as the run wrote
    them and change steps/08_result.json; the step files may be those of an attempt that is in no audit record. So no step file is
    read, and the run's own status is that of the result payload. The output is not kept, and the reason says why, when the folder
    has no audit record that can be read, when its steps/09_close.json holds records that the audit record's own list lacks (the
    folder was run again in place, which writes a new audit record), when those records cannot be read, and when a payload is not
    what the aid's models read."""
    if run.audit is None:
        return Output(False, NOT_KEPT)
    if run.closes is None:
        return Output(False, CLOSES_UNREAD)
    if _run_again(run):
        return Output(False, NOT_KEPT)
    payloads = {name: run.payload(name) for name in ("version", "entered", "match", "compare", "result")}
    models = {"choice": (VersionChoice, payloads["version"]), "pairs": (MatchResult, payloads["match"]),
              "findings": (Findings, payloads["compare"]), "case_result": (CaseResult, payloads["result"])}
    try:
        read = {name: _model(model, payload, strict=True) for name, (model, payload) in models.items()}
    except ValidationError:
        return Output(False, OTHER_FORM)
    if not isinstance(payloads["entered"], dict | None):                # the one payload that no model of the aid reads
        return Output(False, OTHER_FORM)
    return Output(True, "", read["case_result"].status if read["case_result"] is not None else None, **payloads, **read)


def _run_again(run: Run) -> bool:
    """Whether the folder was run again in place: its steps/09_close.json holds a record that the list of its audit record lacks,
    as run_case(rerun=True) leaves it, which writes a new audit record and keeps that file."""
    known = run.audit.get(CLOSE_NAME) if run.audit is not None else None
    return any(record not in (known if isinstance(known, list) else []) for record in run.closes or ())


def _model(model: type[BaseModel], payload, strict: bool = False):
    """The payload of a step as the aid's own model of it; None without a payload and for a step the run passed over. A payload
    that the model does not read is None too, unless `strict`, which raises its ValidationError (a ValueError)."""
    if payload is None or (isinstance(payload, dict) and SKIPPED in payload):
        return None
    try:
        return model.model_validate(payload)
    except ValidationError:
        if strict:
            raise
        return None


# --- the figures of a run ---

@dataclass(frozen=True)
class Figures:
    """What the audit record of a run says the run took (figures_of)."""
    seconds: float | None                        # from the start of the run to its end; None when the record does not say
    call_attempts: int | None                    # the requests to a model: every attempt of the reader, every question to a decider
    cost_eur: Decimal | None                     # the cost of those calls; None when a call has none recorded
    reader: str                                  # the kind of reader: "parser", "llm", "cart_html"; "" when nothing was read
    started_at: datetime | None                  # when the run started, with an offset (a time without one is local time)
    remarks: tuple[str, ...]                     # how a figure was arrived at where the record does not give it outright


def figures_of(run: Run) -> Figures:
    """The figures of a run from its audit record, with remarks where a figure is not the record's own.

    The seconds are timings.wall_seconds. The runner and the review page run by a clock without a zone, and the audit record then
    holds none (aid/audit.py): finished_at minus started_at is taken when both carry no offset, as one clock gave them, and else
    the sum of the steps. The call attempts: a reader call counts its attempts (1 when the record has none), a decider call whose
    source begins "model:" counts 1, and the answer of the aid without a model (source "none") is no call. The cost is the sum of
    cost_eur over those model calls, as decimals, and None only when one of them has none. Attempts that the runner gave up on a
    failure of the service are in no audit record. The reader is reduced to its kind, the part before the first colon: the name
    of a parser template may be a supplier's."""
    audit = run.audit
    if audit is None:
        return Figures(None, None, None, "", None, (NO_AUDIT,))
    remarks = [RUN_AGAIN] if _run_again(run) else []
    seconds = _seconds(audit, remarks)
    attempts, cost = _calls(audit, remarks)
    document = run.payload("read_document")
    reader = document.get("reader") if isinstance(document, dict) else None
    started = _time(audit.get("started_at"))
    return Figures(seconds, attempts, cost, reader.partition(":")[0] if isinstance(reader, str) else "",
                   _with_offset(started) if started is not None else None, tuple(remarks))


def _seconds(audit: dict, remarks: list[str]) -> float | None:
    """The seconds of the run (see figures_of), with the remark that says where they come from when not from wall_seconds."""
    timings = audit.get("timings") if isinstance(audit.get("timings"), dict) else {}
    if _is_number(timings.get(WALL_SECONDS)):
        return timings[WALL_SECONDS]
    started, finished = _time(audit.get("started_at")), _time(audit.get("finished_at"))
    if started is not None and finished is not None and started.utcoffset() is None and finished.utcoffset() is None:
        remarks.append(LOCAL_CLOCK)
        return (finished - started).total_seconds()
    if _is_number(timings.get(TOTAL)):
        remarks.append(SUM_OF_STEPS)
        return timings[TOTAL]
    remarks.append(SECONDS_UNKNOWN)
    return None


def _calls(audit: dict, remarks: list[str]) -> tuple[int, Decimal | None]:
    """The call attempts and the cost of the model calls of the audit record (see figures_of)."""
    attempts, cost, unknown = 0, Decimal(0), 0
    for call in audit.get("calls") if isinstance(audit.get("calls"), list) else []:
        if not isinstance(call, dict):
            continue
        if call.get("kind") == "reader":
            made = call.get("attempts")
            attempts += made if type(made) is int and made >= 1 else 1
        elif call.get("kind") == "decider" and isinstance(call.get("source"), str) and call["source"].startswith(MODEL):
            attempts += 1
        else:
            continue                             # the answer of the aid without a model: no call
        try:
            cost += Decimal(str(call.get("cost_eur")))
        except InvalidOperation:                 # no cost recorded (None), or what is no amount
            unknown += 1
    if unknown:
        remarks.append(COST_UNKNOWN.format(n=unknown, s="" if unknown == 1 else "s"))
    return attempts, None if unknown else cost


def _is_number(value) -> bool:
    """Whether a value of a record is a number; true and false are none."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _time(text) -> datetime | None:
    """A time of the audit record as it was written, with or without an offset; None for what is no time."""
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


# --- the present state of a folder ---

@dataclass(frozen=True)
class State:
    """What a case folder is now (state_of)."""
    state: str                                   # one of STATES of aid/review/status.py; "" for a replaced folder, or when untold
    closed_by: str                               # "rule", "buyer" or "replaced"; "" for a folder that is not closed


def state_of(cases_root: Path, runs: Sequence[Run]) -> list[State]:
    """The present state of the folder of each of these runs, in their order: the state the review page shows for it (case_states
    of aid/review/status.py, which only reads), and who closed it. closed_by is "rule" when the folder is closed and its
    steps/09_close.json holds no close of the review page, "buyer" when it is closed and holds one, "replaced" for a folder that
    another replaced, which has no state, and "" otherwise. The run's own status (output_of) is what the run left; this is what
    became of it since."""
    states = case_states(cases_root, [run.folder.name for run in runs])
    found = []
    for run in runs:
        state = states.get(run.folder.name, "")
        replaced = not state and (_replaced(run) or marked_replaced(run.folder.path))
        found.append(State(state, "replaced" if replaced else CLOSED_BY.get(state, "")))
    return found


# --- the aid that runs on the data folder ---

def aid_running(cases_root: Path, now: float | None = None) -> bool:
    """Whether the aid runs on the data folder of this cases root: the state file of its launcher (aid/start.py), which lies next
    to the settings file, in the folder above the cases root, shows a heartbeat younger than RUNNING_WITHIN seconds at `now`, the
    clock of this PC when none is given. The launcher writes it every five seconds. On Windows a file that this command has open
    at the moment the aid replaces it makes that write of the aid fail, so the records are not made while the aid runs. No state
    file, or one that says nothing, is no aid that runs; one that cannot be read because something holds it is taken as one."""
    try:
        state = json.loads((Path(os.path.realpath(cases_root)).parent / START_STATE).read_bytes())
    except (FileNotFoundError, NotADirectoryError, ValueError):
        return False
    except OSError:
        return True
    launcher = state.get("launcher") if isinstance(state, dict) else None
    beat = launcher.get("heartbeat") if isinstance(launcher, dict) else None
    return _is_number(beat) and abs((time.time() if now is None else now) - beat) < RUNNING_WITHIN


def aid_checkable(cases_root: Path) -> bool:
    """Whether aid_running can tell anything for this cases root: the folder above it holds the settings file or the launcher's
    state file. The cases root is a setting (aid/settings.py), and the launcher writes its state file next to the settings file
    (aid/review/status.py): a cases root that lies elsewhere has no state file above it, whether the aid runs or not."""
    above = Path(os.path.realpath(cases_root)).parent
    return os.path.isfile(above / SETTINGS_FILE) or os.path.lexists(above / START_STATE)


# --- identifying text ---

def holds_identifier(text: str, cases: Iterable[ListedCase], runs: Iterable[Run | Folder]) -> bool:
    """Whether a free text holds identifying text: a purchase order number of the case list or of a folder, the name of a case
    folder (of the case list, or of these runs or folders), the name or the stem of an input file or of a document of the case
    list, or the supplier of a run's result. The comparison takes no account of capitals or of the kind and number of spaces. A
    name or number shorter than SHORTEST characters is left out: it would withhold nearly every text. A cell that holds
    identifying text is written as WITHHELD and named by its column in the remarks."""
    spoken = " ".join(text.casefold().split())
    return any(identifier in spoken for identifier in identifiers(cases, runs))


def identifiers(cases: Iterable[ListedCase], runs: Iterable[Run | Folder]) -> set[str]:
    """The identifying texts that holds_identifier looks for, in the form it compares them in."""
    found: set[str | None] = set()
    for case in cases:
        document = case.document.replace("\\", "/").rpartition("/")[2]
        found |= {case.po_number, case.folder, document, PurePosixPath(document).stem}
    for run in runs:
        folder = run.folder if isinstance(run, Run) else run
        found |= {folder.name, folder.po_number}
        for name, _ in folder.files:
            found |= {name, PurePosixPath(name).stem}
        result = run.payload("result") if isinstance(run, Run) else None
        found.add(result.get("supplier") if isinstance(result, dict) else None)
    spoken = {" ".join(identifier.casefold().split()) for identifier in found if isinstance(identifier, str)}
    return {identifier for identifier in spoken if len(identifier) >= SHORTEST}


# --- the tree under a folder, for the tests ---

def snapshot(folder: Path) -> dict[str, tuple]:
    """Everything under a folder as a test compares it before and after a command that only reads: by its name from the folder
    ("." for the folder itself), a file with its size, its sha256 and its modification time, a folder with its modification time,
    which moves when a file is made or removed in it. Reading for the snapshot changes none of them."""
    folder = Path(folder)
    found: dict[str, tuple] = {".": ("folder", folder.stat().st_mtime_ns)}
    for path in sorted(folder.rglob("*")):
        modified = path.stat().st_mtime_ns
        if path.is_dir():
            found[path.relative_to(folder).as_posix()] = ("folder", modified)
        else:
            data = path.read_bytes()
            found[path.relative_to(folder).as_posix()] = (len(data), hashlib.sha256(data).hexdigest(), modified)
    return found
