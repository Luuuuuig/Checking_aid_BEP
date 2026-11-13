"""The tab "Bestanden" of the review page: a file given to the assistant on the page, and what became of each file the runner handled.

A file given on the page. accept takes a file the buyer gives in the upload field of the tab into the watched folder of the settings
(settings.watched_folder), through drop of aid/sources/dropin.py: under its own name made safe as the scan names an attachment, with _2,
_3 and so on when the folder holds that name already, written under a name with a dot first and renamed once it is whole, so that the
scan never reads half a file. Before that, accept refuses with NotAccepted, a ValueError, and writes nothing: a file of a type the scan
does not read, whose name (as it would stand in the watched folder) does not end in one of ACCEPTED ("type"); a spreadsheet (.xlsx or
.csv) while the columns of the export are not set (the setting export_columns: the scan would then skip it as a file of another type,
and the buyer would not know why; "export"); and an empty file ("empty"). From the watched folder on the file is the runner's, as any
file the mailbox rule saves there: the same classes, the same duplicate rule and the same refusals. The page writes nothing into the PO
store or into a case.

What became of a file. The scan of the watched folder records an event for each document it handled (ScanResult.events of
aid/sources/mailbox.py), and the runner appends each as one JSON line to <cases_root>/intake.jsonl (INTAKE_FILE, written by write, the
lines of a pass in one write; line_of makes a line): {"time": the time of the pass, "file": the file as the skipped list names it
("PO.eml/Inkooporder.pdf" for an attachment), "outcome": ..., "detail": ...}. A case that waited for its purchase order and is checked
again as a new case adds a line of its own, which names its document alone, without its e-mail (aid/runner.py, _recheck_waiting), and
so does a case opened from a scan that then failed at reading (aid/runner.py, _run_cases). A
file that stays in the watched folder as an error gets one line, not one per pass, and one more when the runner sets it aside after
STAY_DAYS days (aid/runner.py says how). The outcomes, OUTCOMES, with their details:
    po        a PO PDF filed in the store: "<PO number> v<version>"
    export    an export of the ERP filed: "<the number of its POs> <the number of those whose look was new or changed>"
    case      a case opened for a confirmation: its case id, <PO number>_<time> or unknown_<time>
    guess     a case opened for a PO that the scan found otherwise than as the one number of the pattern (po_found_by): its case id
    answer    an e-mail filed as a supplier's answer to a question of the aid: the id of its case
    bounce    an e-mail filed with its case as a bounce of a message of the aid: the id of its case
    auto      an e-mail filed with its case as an automatic reply (an out-of-office notice): the id of its case
    own       an e-mail filed with its case as a message of the aid's own side (the buyer's own reply to all, which names the
              question and is no supplier's answer): the id of its case
    recheck   a case that waited for its PO, checked again as a new case: the id of the new case
    unread    a case opened from a scan, which failed at reading because the document has no text to read (unread_scan of
              aid/notify.py): the id of the case
    skipped   a document left alone, or a file filed before: the scan's reason ("duplicate", "unsupported file type: .docx", ...)
    error     a document the scan could not handle, which stays in the folder: the detail of error_detail of aid/sources/mailbox.py,
              "export: <reason>" for an export the store refused (the reason one of REFUSALS of aid/readers/po_export.py), "version"
              for a PO PDF whose version the store holds from another file, and "" for any other error (ERRORS gives each its label)
    set_aside a file that stayed as an error and that the runner moved to processed/ after STAY_DAYS days: the whole days it stayed
So a line holds a file name, a PO number, a case id, a number of days or a fixed reason, and never what a document or an error says.

The table. recent gives the last lines of the file, the newest first, passing over a line that cannot be read: a line cut off while the
runner writes it, one whose outcome this table has no label for, and one whose detail has not the form of its outcome. shown gives the
fixed label of a line in the page's language, by the keys of aid/review/i18n.py that begin with "files_": an outcome by its own label, a
reason of a skip by SKIPPED, an error by ERRORS, its labels never quoting the error. A reason of a skip that SKIPPED does not know (a
later version's) is "Overgeslagen: niets gedaan"; an export refused for a reason the table does not know has the label of every other
reason of EXPORT_REFUSED, and any other detail of an error the label of an error the scan does not tell apart. rows gives the table as
the tab shows it: the time (when), the file and the label, with the mark of a try-out ("(proefdocument)", hand_tryout) behind the label
of a case made from a try-out document, as Task 33 marks such a case on the page (case_is_tryout of aid/tryout_mark.py). Behind the
label of a line that opened a case (OPENED: case, guess and recheck), and behind its mark of a try-out, stands what the case is now,
"Zaak geopend voor inkooporder 250105. Nu: mislukt." ("files_now"), its state read from the case's folder when the table is read
(case_states of aid/review/status.py), so that the intake file itself never changes: failed, failed and noted as handled by the
buyer, waiting for the supplier, closed by itself or by the buyer, waiting for Copilot, or being read. A case in review, also one that
waits for its purchase order, keeps its label alone, and so does a case whose folder is gone or cannot be read; no other outcome
changes, also one that names a case.
"""
import json
import os
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from aid.readers.po_export import EXPORT_SUFFIXES, REFUSALS
from aid.review import status as aid_status     # the module: what a case is now, read when the table is read
from aid.review.i18n import STRINGS
from aid.sources.dropin import FALLBACK, drop
from aid.sources.mailbox import DOCUMENT_SUFFIXES, NO_ORDER_LINES, NOT_AN_EXPORT
from aid.sources.mailbox import _append_lines    # JSON lines appended in one write, a line a crash cut off ended first, as review_items.jsonl
from aid.sources.mailbox import safe_name
from aid.tryout_mark import TRYOUT_OUTSIDE, case_is_tryout

ACCEPTED = (*DOCUMENT_SUFFIXES, ".eml", *EXPORT_SUFFIXES)       # ".pdf", ".png", ".eml", ".xlsx", ".csv": what the scan reads
INTAKE_FILE = "intake.jsonl"                                      # <cases_root>/intake.jsonl
REASONS = ("type", "export", "empty")                             # why accept refuses a file (NotAccepted.reason)
OUTCOMES = ("po", "export", "case", "guess", "answer", "bounce", "auto", "own", "recheck", "unread", "skipped", "error", "set_aside")
OPENED = ("case", "guess", "recheck")                             # the outcomes of a line that opened a case, its id the detail
UNKNOWN = "unknown"                                               # the PO part of the id of a case opened without a PO number
TABLE_LINES = 20                                                  # the lines the table shows
_BLOCK = 1 << 16                                                  # the bytes recent reads from the end of the file at a time
_UNSUPPORTED = "unsupported file type: "                          # the start of the scan's reason for a file of another type
_REFUSED = "export: "                                             # the start of the detail of an export the store refused
_CASE_ID = re.compile(r"[^/\\]+_[0-9]{8}T[0-9]{6}")              # the id of a case: <PO number>_<YYYYmmddTHHMMSS> (aid/case.py)
_COUNT = re.compile(r"[0-9]+")
# The scan's reasons of a skip (aid/sources/mailbox.py), each with the key of its label after "files_skipped:". A document of a class
# that the scan does not file (packing_slip, invoice, quotation, other) is skipped with its class as the reason.
SKIPPED = {"duplicate": "duplicate", "no attachment": "no_attachment", NOT_AN_EXPORT: "not_an_export", TRYOUT_OUTSIDE: "tryout",
           "no text layer; class unknown": "scan", "more than one PO number": "numbers", "packing_slip": "class", "invoice": "class",
           "quotation": "class", "other": "class", NO_ORDER_LINES: "no_order_lines"}
# The reasons for which the export reader refuses a file (ExportRefused.reason, REFUSALS of aid/readers/po_export.py), each with the
# key of its label after "files_error:": a row with an item code and no PO number; the columns of the file, a column of the map that no
# row holds or a title that stands twice; a row of a CSV file that is cut otherwise than its titles; a workbook with more than one
# visible sheet with data; one with data on a hidden sheet only (for these three the columns are right, so each has a line of its
# own that says what to do); and for every other reason the line that sends the buyer to the columns on the settings tab.
EXPORT_REFUSED = {reason: "no_po_number" if reason == "no_po_number" else "columns" if reason in ("no_column", "double_column")
                  else "cells" if reason in ("surplus_cells", "cell_count") else "sheets" if reason == "several_sheets"
                  else "hidden_sheet" if reason == "hidden_sheet" else "export" for reason in REFUSALS}
# Every form of the detail of an error (error_detail of aid/sources/mailbox.py), each with the key of its label: any error the scan does
# not tell apart, which the label says could not be read; a PO PDF whose version the store holds from another file; and an export
# refused for each reason of the export reader, by EXPORT_REFUSED.
ERRORS = {"": "files_error", "version": "files_error:version",
          **{f"{_REFUSED}{reason}": f"files_error:{key}" for reason, key in EXPORT_REFUSED.items()}}


class NotAccepted(ValueError):
    """A file that accept refuses: `name` as it was given, and `reason`, one of REASONS. The text names neither."""

    def __init__(self, name: str, reason: str):
        super().__init__(f"the file is not given to the assistant: {reason}")
        self.name, self.reason = name, reason


def accept(upload: Path, name: str, watched_folder: Path, exports: bool = False) -> Path:
    """Put the file `upload` into the watched folder under `name` made safe (see the module docstring) and return the file there: drop of
    aid/sources/dropin.py, after the refusals of the module docstring, each a NotAccepted with nothing written, the type first.
    `exports` says whether the columns of the export are set (export_columns of the settings); without them a spreadsheet is refused.
    A file that cannot be read or written raises its own error."""
    upload = Path(upload)
    suffix = os.path.splitext(safe_name(name, FALLBACK, upload.suffix))[1].lower()     # the type as the scan will judge it
    if suffix not in ACCEPTED:
        raise NotAccepted(name, "type")
    if suffix in EXPORT_SUFFIXES and not exports:
        raise NotAccepted(name, "export")
    if upload.stat().st_size == 0:
        raise NotAccepted(name, "empty")
    return drop(upload, name, watched_folder)


def line_of(event: tuple[str, str, str], now: datetime) -> dict:
    """The line of the intake file for the event (file, outcome, detail) at the time `now`, to the second."""
    file, outcome, detail = event
    return {"time": now.isoformat(timespec="seconds"), "file": file, "outcome": outcome, "detail": detail}


def write(cases_root: Path, lines: list[dict]) -> None:
    """Add the lines (line_of) to <cases_root>/intake.jsonl in one write, creating the file and the cases root on first use. A line
    that a crash cut off is ended first, so that a new line starts a line of its own."""
    _append_lines(Path(cases_root) / INTAKE_FILE, lines)


def append(cases_root: Path, events: Iterable[tuple[str, str, str]], now: datetime) -> None:
    """Add a line to <cases_root>/intake.jsonl for each (file, outcome, detail), at the time `now`, in one write (write)."""
    write(cases_root, [line_of(event, now) for event in events])


def recent(cases_root: Path, limit: int = TABLE_LINES) -> list[dict]:
    """The last `limit` lines of <cases_root>/intake.jsonl that can be read, the newest first; [] without the file. The file is read from
    its end, a block at a time, until enough lines are found. A line that cannot be read is passed over (see the module docstring)."""
    found: list[dict] = []
    try:
        f = open(Path(cases_root) / INTAKE_FILE, "rb")
    except FileNotFoundError:
        return found
    with f:
        end, rest = f.seek(0, os.SEEK_END), b""
        while end > 0 and len(found) < limit:
            start = max(0, end - _BLOCK)
            f.seek(start)
            lines = (f.read(end - start) + rest).split(b"\n")
            rest = lines.pop(0) if start > 0 else b""                 # the first line of the block may begin in the block before
            for line in reversed(lines):
                if (event := _read(line)) is not None:
                    found.append(event)
                    if len(found) == limit:
                        break
            end = start
    return found


def shown(event: dict, language: str) -> str:
    """The fixed label of the line in the language (see the module docstring), never its detail as it stands."""
    strings = STRINGS[language]
    outcome, detail = event.get("outcome"), event.get("detail") or ""
    if outcome == "po":
        number, _, version = detail.rpartition(" v")
        return strings["files_outcome:po"].format(number=number, version=version)
    if outcome == "export":
        orders, _, new = detail.partition(" ")
        return strings["files_outcome:export_one" if orders == "1" else "files_outcome:export"].format(n=orders, m=new)
    if outcome in ("case", "guess", "recheck"):
        number = _po_of(detail)
        if number is None:
            return strings["files_outcome:case_unknown"]
        return strings[f"files_outcome:{outcome}"].format(number=number)
    if outcome in ("answer", "bounce", "auto", "own", "unread"):
        return strings[f"files_outcome:{outcome}"]
    if outcome == "set_aside":
        return strings["files_outcome:set_aside"].format(n=detail)
    if outcome == "error":
        return strings[ERRORS.get(detail) or ("files_error:export" if detail.startswith(_REFUSED) else "files_error")]
    key = "type" if detail.startswith(_UNSUPPORTED) else SKIPPED.get(detail, "other")
    return strings[f"files_skipped:{key}"]


def when(event: dict) -> str:
    """The time of the line as the page shows a day and a time, the day first, "18-11-2026 10:32", in both languages: a time without
    a zone as it stands, since the runner's clock is the local time of this PC, and a time with a zone in the local time of this PC."""
    moment = datetime.fromisoformat(event["time"])
    return (moment.astimezone() if moment.utcoffset() is not None else moment).strftime("%d-%m-%Y %H:%M")


def rows(cases_root: Path, language: str, limit: int = TABLE_LINES) -> list[list[str]]:
    """The table of the tab as it shows the last lines (recent): the time, the file and the label in the language, with the mark of a
    try-out behind the label of a case made from a try-out document, and behind the label of a line that opened a case (OPENED) what
    the case is now (see the module docstring)."""
    strings = STRINGS[language]
    events = recent(cases_root, limit)
    states = aid_status.case_states(Path(cases_root), [event["detail"] for event in events if event["outcome"] in OPENED])
    table = []
    for event in events:
        label = shown(event, language) + (f" {strings['hand_tryout']}" if _tryout(cases_root, event) else "")
        key = f"files_now:{states.get(event['detail'])}" if event["outcome"] in OPENED else None      # none for a case in review
        table.append([when(event), event["file"], strings["files_now"].format(label=label, state=strings[key]) if key in strings else label])
    return table


def _tryout(cases_root: Path, event: dict) -> bool:
    """Whether the line names a case made from a try-out document (case_is_tryout of aid/tryout_mark.py)."""
    return event["outcome"] in OPENED and case_is_tryout(Path(cases_root), event["detail"])


def _po_of(case_id: str) -> str | None:
    """The PO number of a case by its id, <PO number>_<time>; None for a case opened without one, and for a detail that is no case id."""
    number, underscore, _ = case_id.rpartition("_")
    return number if underscore and number and number != UNKNOWN else None


def _read(line: bytes) -> dict | None:
    """The line as an event when it can be read and shown: a JSON object with the four texts, a time, one of OUTCOMES and a detail of
    the form of its outcome (_formed); else None."""
    try:
        event = json.loads(line)
        if not (isinstance(event, dict) and all(isinstance(event.get(key), str) for key in ("time", "file", "outcome", "detail"))):
            return None
        datetime.fromisoformat(event["time"])
    except ValueError:                           # no JSON, no UTF-8, or a time that is no time
        return None
    return event if event["outcome"] in OUTCOMES and _formed(event["outcome"], event["detail"]) else None


def _formed(outcome: str, detail: str) -> bool:
    """Whether the detail has the form of its outcome (see the module docstring), so that shown takes from it no more than a PO
    number, a version, two counts and a number of days. The detail of a skip or an error is never shown, whatever it holds."""
    if outcome == "po":
        number, v, version = detail.rpartition(" v")
        return bool(number and v) and _COUNT.fullmatch(version) is not None
    if outcome == "export":
        return len(counts := detail.split(" ")) == 2 and all(_COUNT.fullmatch(count) for count in counts)
    if outcome in ("case", "guess", "recheck", "answer", "bounce", "auto", "own", "unread"):
        return _CASE_ID.fullmatch(detail) is not None
    if outcome == "set_aside":
        return _COUNT.fullmatch(detail) is not None
    return True
