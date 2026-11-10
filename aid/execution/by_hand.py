"""The list of the changes that the buyer enters in Exact by hand: a view over the prepared changes with one file of marks beside them.

From the buyer's seat no program reaches Exact, so the aid prepares every change: a row of <cases_root>/prepared/prepared_changes.csv,
written by the prepared writer (aid/execution/writer.py). The buyer enters in Exact what this list holds and ticks it off on the
review page's tab "In Exact invoeren" (aid/review/page.py, the list as it shows it in aid/review/hand_list.py), and the digest names
the number still to enter (aid/notify.py). The ticks are kept in <cases_root>/prepared/marks.jsonl, appended and never rewritten. A
change that a writer makes in a system one day never reaches prepared_changes.csv, so it never reaches the list. Nothing here changes
a case's result, a write log, the disposition log or prepared_changes.csv: marks.jsonl is the only file this module writes.

What a change is. A row is a change to enter when its kind is the price, the delivery date or the quantity of a PO line (LINE_KINDS)
or the purchase price of an article; a row that attaches or detaches a document or sets a status is never a row to tick. The place
of a change is the kind, the PO number and the line for a PO line, and the article code for an article price, whichever order or
price list asked. The rows of one place, in the order of the file, are cut into runs: neighbours with the same new value, a price or a
quantity compared as a number ("12.7" is "12.70"), a date as its text. A run is one change (Change), with the entry ids of all its
rows: the same change asked by several cases, or by three captures of one cart, is one change.

An undo row. undo_entry and undo_case of the writer prepare the reverse of a change as a row of its own, with the rule id "undo",
the case id of the change it reverses, and the old and the new value swapped. Such a row stands alone among the runs, and it belongs
to the last run of its place before it whose new value is the undo's old value (a number compared as a number); within that run it
takes away only the rows of its own case. When none of those rows has the mark "entered" or "seen", the undo cancels them: they and
the undo get the state undone and leave their place, as if they had not been asked (Exact still holds the value from before), while
the rows of other cases stay, so that a change another case asked too stays asked, with their entry ids only. A run left without
rows is cancelled whole, and an undo whose rows were cancelled already is undone with them. The marks of its own case's rows decide,
not those of the run: a change that stays asked for another case and is then ticked does not bring back what the undo cancelled.
When one of its case's rows has the mark "entered" or "seen", the undo is a change to enter: the old value back, which for an
article price the aid never read is unknown (None); the run stays as it is. An undo whose case has no row in the run it belongs to
cancels nothing: it is a change to enter when one of that run's rows has the mark "entered" or "seen", and else it is passed over,
with the state undone (it is no line that cannot be read, and is not counted as one). An undo whose run the file does not hold is a
change to enter too. The rows left at the place are then cut into runs again, an undo row among them as any other row.

The state of a change that no undo cancelled comes from the marks: entered when one of its rows has the mark "entered"; else seen
when one has the mark "seen"; else dropped when one has the mark "dropped"; else replaced when a later run of its place exists (a
newer document asked another value); else already when its new value is the value of the last entered or seen run of the place
before it (the marks say that Exact holds it); else open. So a change that a newer document replaced is not asked, a change that was
dropped is not asked again for the same value as long as no other value was asked for its place in between, and a change is not
asked while the marks say that Exact holds its value. Only the open changes are asked of the buyer. Of each entry the last mark
counts, and the mark "reopened" takes its marks away.

A line of marks.jsonl is one JSON object, {"entry_id": "9f...", "mark": "entered", "at": "2026-11-18T10:32:05", "by": "buyer"}. mark()
writes one line per entry id, all in one write, and nothing for an entry of a change that is not open when the mark is "entered" or
"dropped"; a mark is checked and written under one lock, so that two presses of the page at the same moment do not both tick one change.

A line of either file that cannot be read is passed over and counted, and the page says how many: the last line of a file that another
process is writing is the usual case. A line of prepared_changes.csv cannot be read when it is not UTF-8, when the CSV reader cannot
parse it (a field longer than the reader's limit, a carriage return inside a field without quotes), when it does not have the
columns of the file's header, or when it holds a kind, a version or a time that a row does not hold, or a change without its line or
its article; a line of marks.jsonl when it is no JSON object with an entry id, a mark this module knows and a time. A file that is
not there holds nothing. A new mark after a last line that a crash cut off starts a line of its own, as in the write log.
"""
import csv
import json
import os
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, NamedTuple, get_args

from pydantic import BaseModel

from aid.execution.ops import OpKind
from aid.execution.writer import PREPARED_FILE, UNDO_RULE_ID

FOLDER = "prepared"                              # under cases_root: prepared_changes.csv and marks.jsonl
MARKS_FILE = "marks.jsonl"                       # <cases_root>/prepared/marks.jsonl, appended, never rewritten
LINE_KINDS = ("po_line_price", "po_line_date", "po_line_quantity")
ARTICLE_KIND = "article_price"
KINDS = (*LINE_KINDS, ARTICLE_KIND)              # the kinds of a change to enter
Mark = Literal["entered", "dropped", "reopened", "seen"]
State = Literal["open", "entered", "seen", "dropped", "already", "replaced", "undone"]
_NUMBERS = ("po_line_price", "po_line_quantity", "article_price")     # the kinds whose values compare as numbers
_NEEDED = ("entry_id", "case_id", "po_number", "version", "line_id", "article_code", "kind", "old_value", "new_value", "rule_id",
           "timestamp")                          # the columns a row is read from: those of the writer, the document aside
_DONE = ("entered", "seen", "dropped")           # the marks that give a change its state, in the order they decide it
_HELD = ("entered", "seen")                      # the marks that say Exact holds a change's value
_LOCK = threading.Lock()


class Change(BaseModel):
    """One change to enter, with every prepared row that asks for it: one run of the rows of one place (see the module docstring)."""
    ids: list[str]                               # the entry ids of those rows, the oldest first
    kind: str
    po_number: str                               # of the newest row: "" for a change a price list asked
    version: int | None                          # of the newest row: the PO version its case was compared with
    line_id: str | None
    article_code: str | None
    old_value: str | None                        # of the newest row
    new_value: str | None                        # of the newest row
    case_ids: list[str]                          # the cases (and price lists) that asked for it, each once, in the order of the rows
    rule_id: str                                 # of the newest row
    prepared_at: str                             # the time of the first row
    state: State
    marked_at: str | None = None                 # for entered, seen and dropped: the time of the latest mark that gives the state
    marked_by: str | None = None                 # and who made it


class _Row(NamedTuple):
    """A row of prepared_changes.csv that holds a change to enter (see _row)."""
    entry_id: str
    case_id: str
    po_number: str
    version: int | None
    line_id: str | None
    article_code: str | None
    kind: str
    old_value: str | None
    new_value: str | None
    rule_id: str
    timestamp: str


class _Mark(NamedTuple):
    """The last mark of an entry: what it says, when, by whom, and its line in marks.jsonl (the later line wins)."""
    what: str
    at: str
    by: str | None
    line: int


def changes(prepared: Path) -> tuple[list[Change], int]:
    """Every change of the folder's prepared_changes.csv in the order it was first asked, each with its state by the folder's
    marks.jsonl, and the number of lines of the two files that were passed over (see the module docstring)."""
    rows, passed_rows = _rows(Path(prepared) / PREPARED_FILE)
    marks, passed_marks = _marks(Path(prepared) / MARKS_FILE)
    places: dict[tuple, list[tuple[int, _Row]]] = {}
    for index, row in enumerate(rows):
        places.setdefault(_place(row), []).append((index, row))
    found = [pair for place in places.values() for pair in _of_place(place, marks)]
    return [change for _, change in sorted(found, key=lambda pair: pair[0])], passed_rows + passed_marks


def open_changes(prepared: Path) -> list[Change]:
    """The changes still to enter in Exact: those of changes() whose state is open, in the order they were first asked."""
    return [change for change in changes(prepared)[0] if change.state == "open"]


def mark(prepared: Path, ids: list[str], what: Mark, now: datetime, by: str = "buyer") -> int:
    """Mark the entries with these ids, each once: one line per entry id in the folder's marks.jsonl, at now to the second, and return
    the number of lines written. An id that is no entry of a change of the folder gets no line, and neither does an entry of a change
    that is not open when the mark is "entered" or "dropped": marking such a change writes nothing and returns 0. A mark that is not
    one of the four raises ValueError."""
    if what not in get_args(Mark):
        raise ValueError(f"unknown mark {what!r}: expected one of {', '.join(get_args(Mark))}")
    prepared = Path(prepared)
    with _LOCK:                                  # the state is read and the lines written in one go
        found, _ = changes(prepared)
        owner = {entry_id: change for change in found for entry_id in change.ids}
        at = now.isoformat(timespec="seconds")
        lines = [json.dumps({"entry_id": entry_id, "mark": what, "at": at, "by": by}, ensure_ascii=False) for entry_id in dict.fromkeys(ids)
                 if entry_id in owner and (what not in ("entered", "dropped") or owner[entry_id].state == "open")]
        if lines:
            _append(prepared / MARKS_FILE, lines)
        return len(lines)


# --- the change of a run ---

def _place(row: _Row) -> tuple:
    """The place of a change: the kind, the PO and the line of a PO line, the article of an article price."""
    return (row.kind, row.po_number, row.line_id) if row.kind in LINE_KINDS else (row.kind, row.article_code)


def _same(kind: str, one: str | None, other: str | None) -> bool:
    """Whether two new values of a kind are the same: a price or a quantity as a number, anything else as its text."""
    if kind in _NUMBERS and (first := _number(one)) is not None and (second := _number(other)) is not None:
        return first == second
    return one == other


def _number(text: str | None) -> Decimal | None:
    try:
        number = Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _of_place(rows: list[tuple[int, _Row]], marks: dict[str, _Mark]) -> list[tuple[int, Change]]:
    """The changes of the rows of one place, in the order of the file, each with the index of its first row (see the module
    docstring): of each run the rows that undos cancelled, each undo row that cancelled them or was passed over, then the runs of the
    rows left."""
    runs = _runs(rows, undo_alone=True)
    gone = _gone(runs, marks)
    found = []
    for run in runs:
        out = [pair for pair in run if pair[0] in gone]
        if out:
            found.append((out[0][0], _change([row for _, row in out], marks, False, [], undone=True)))
    left = _runs([pair for run in runs for pair in run if pair[0] not in gone], undo_alone=False)
    held: list[str | None] = []                  # the value Exact holds by the marks: that of the last entered or seen run so far
    for number, run in enumerate(left):
        change = _change([row for _, row in run], marks, number == len(left) - 1, held)
        if change.state in _HELD:
            held = [change.new_value]
        found.append((run[0][0], change))
    return found


def _runs(rows: list[tuple[int, _Row]], undo_alone: bool) -> list[list[tuple[int, _Row]]]:
    """The rows of one place cut into runs, neighbours with the same new value; with undo_alone an undo row is a run of its own."""
    runs: list[list[tuple[int, _Row]]] = []
    for index, row in rows:
        before = runs[-1][-1][1] if runs else None
        if (before is not None and _same(row.kind, before.new_value, row.new_value)
                and not (undo_alone and UNDO_RULE_ID in (row.rule_id, before.rule_id))):
            runs[-1].append((index, row))
        else:
            runs.append([(index, row)])
    return runs


def _gone(runs: list[list[tuple[int, _Row]]], marks: dict[str, _Mark]) -> set[int]:
    """The indexes of the rows that leave their place (runs of one place, an undo row a run of its own; see the module docstring):
    an undo belongs to the last run before it, not an undo, whose new value is the undo's old value, and cancels the rows of its own
    case there, with itself, when none of them has the mark "entered" or "seen"; an undo whose case has no row there is passed over
    when no row of the run has such a mark. Any other undo stays, a change to enter."""
    gone: set[int] = set()
    for number, run in enumerate(runs):
        index, undo = run[0]
        if undo.rule_id != UNDO_RULE_ID:
            continue
        own = next((runs[earlier] for earlier in reversed(range(number)) if runs[earlier][0][1].rule_id != UNDO_RULE_ID
                    and _same(undo.kind, runs[earlier][-1][1].new_value, undo.old_value)), None)
        if own is None:
            continue                                 # the file does not hold its run
        mine = [pair for pair in own if pair[1].case_id == undo.case_id]
        if not _held(mine or own, marks):
            gone |= {index, *(row_index for row_index, _ in mine)}
    return gone


def _held(rows: list[tuple[int, _Row]], marks: dict[str, _Mark]) -> bool:
    """Whether one of the rows has the mark "entered" or "seen": the marks say that Exact holds its value."""
    return any(row.entry_id in marks and marks[row.entry_id].what in _HELD for _, row in rows)


def _change(run: list[_Row], marks: dict[str, _Mark], last: bool, held: list[str | None], undone: bool = False) -> Change:
    """The change of one run of a place, with its state (see the module docstring): last says whether it is the last run of its
    place, held is [the new value of the last entered or seen run before it], or [] when there is none, and undone that an undo
    cancelled it, or that it is the undo that did."""
    newest = run[-1]
    state, deciding = ("undone", []) if undone else _state(run, marks, last, held)
    latest = max(deciding, key=lambda one: one.line) if deciding else None
    return Change(ids=[row.entry_id for row in run], kind=newest.kind, po_number=newest.po_number, version=newest.version,
                  line_id=newest.line_id, article_code=newest.article_code, old_value=newest.old_value, new_value=newest.new_value,
                  case_ids=list(dict.fromkeys(row.case_id for row in run)), rule_id=newest.rule_id, prepared_at=run[0].timestamp,
                  state=state, marked_at=latest.at if latest else None, marked_by=latest.by if latest else None)


def _state(run: list[_Row], marks: dict[str, _Mark], last: bool, held: list[str | None]) -> tuple[str, list[_Mark]]:
    """The state of a run that no undo cancelled, and the marks of its rows that give it (see the module docstring)."""
    for what in _DONE:
        deciding = [marks[row.entry_id] for row in run if row.entry_id in marks and marks[row.entry_id].what == what]
        if deciding:
            return what, deciding
    if not last:
        return "replaced", []
    if held and _same(run[-1].kind, held[0], run[-1].new_value):
        return "already", []
    return "open", []


# --- the two files ---

def _lines(path: Path) -> list[bytes]:
    """The lines of the file that are not blank, split at b"\\n" as they were appended; none when there is no file."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    return [line for line in data.split(b"\n") if line.strip()]


def _fields(line: bytes) -> list[str] | None:
    """The fields of one line of the CSV file; None for a line that is not UTF-8 or that the CSV reader cannot parse."""
    try:
        return next(csv.reader([line.decode("utf-8").removesuffix("\r")]), [])
    except (UnicodeDecodeError, csv.Error):
        return None


def _rows(path: Path) -> tuple[list[_Row], int]:
    """The rows of prepared_changes.csv that are changes to enter, in the order of the file, and the number of lines passed over. The
    first line is the header; without the writer's columns there no line can be read. A line that repeats the header is no row."""
    lines = _lines(path)
    if not lines:
        return [], 0
    header = _fields(lines[0])
    if header:
        header[0] = header[0].removeprefix("﻿")
    if not header or not set(_NEEDED) <= set(header):
        return [], len(lines)
    rows, passed = [], 0
    for line in lines[1:]:
        fields = _fields(line)
        if fields == header:
            continue
        row = _row(dict(zip(header, fields))) if fields is not None and len(fields) == len(header) else None
        if row is None:
            passed += 1
        elif row.kind in KINDS:
            rows.append(row)
    return rows, passed


def _row(values: dict[str, str]) -> _Row | None:
    """The row of one line's values, by the header's columns; None when the line cannot be read as a row (see the module docstring).
    A row of any kind is checked, so that a line cut off is counted whatever it held; an empty value is None."""
    kind, entry_id = values["kind"], values["entry_id"]
    if kind not in get_args(OpKind) or not entry_id or "+" in entry_id or any(character.isspace() for character in entry_id):
        return None
    try:
        version = int(values["version"]) if values["version"] else None
        datetime.fromisoformat(values["timestamp"])
    except ValueError:
        return None
    line_id, article_code = values["line_id"] or None, values["article_code"] or None
    if (kind in LINE_KINDS and not (values["po_number"] and line_id)) or (kind == ARTICLE_KIND and not article_code):
        return None
    return _Row(entry_id=entry_id, case_id=values["case_id"], po_number=values["po_number"], version=version, line_id=line_id,
                article_code=article_code, kind=kind, old_value=values["old_value"] or None, new_value=values["new_value"] or None,
                rule_id=values["rule_id"], timestamp=values["timestamp"])


def _marks(path: Path) -> tuple[dict[str, _Mark], int]:
    """The last mark of each entry of marks.jsonl, an entry whose last mark is "reopened" having none, and the number of lines passed
    over."""
    found: dict[str, _Mark] = {}
    passed = 0
    for number, line in enumerate(_lines(path)):
        try:
            record = json.loads(line.decode("utf-8"))
            if not isinstance(record, dict):
                raise ValueError("no object")
            entry_id, what, at, by = record.get("entry_id"), record.get("mark"), record.get("at"), record.get("by")
            if not (isinstance(entry_id, str) and entry_id and what in get_args(Mark) and isinstance(at, str)
                    and (by is None or isinstance(by, str))):
                raise ValueError("no mark")
            datetime.fromisoformat(at)
        except ValueError:                       # also a line that is no JSON, or not UTF-8
            passed += 1
            continue
        if what == "reopened":
            found.pop(entry_id, None)
        else:
            found[entry_id] = _Mark(what, at, by, number)
    return found, passed


def _append(path: Path, lines: list[str]) -> None:
    """Add the lines at the end of the file in one write, creating the file and its folder on first use. When the file does not end
    with a newline (a crash cut its last line off), they start a line of their own, as in the write log."""
    data = "".join(f"{line}\n" for line in lines).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab+") as f:
        if f.seek(0, os.SEEK_END) > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                data = b"\n" + data
        f.write(data)                            # the file is opened for appending, so this goes to its end
