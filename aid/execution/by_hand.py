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
price list asked, and whether a case made from a try-out document asked it (tryout_case of aid/tryout_mark.py: the case of the
row's case id is one; a price list never is). So the rows of a try-out are never one change with those of other cases, and neither
replaces the other: a supplier's own change stays open beside a later try-out's and outlives its drop, and every change is either
all from try-outs (Change.tryout, which the tab marks) or not at all. The rows of one place, in the order of the file, are cut into
runs: neighbours with the same new value, a price or a quantity compared as a number ("12.7" is "12.70"), a date as its text. A run
is one change (Change), with the entry ids of all its rows: the same change asked by several cases, or by three captures of one
cart, is one change.

An undo row. undo_entry and undo_case of the writer prepare the reverse of a change as a row of its own, with the rule id "undo", the
case id of the change it reverses, and the old and the new value swapped. Such a row stands alone among the runs, and it belongs to the
last run of its place before it whose new value is the undo's old value (a number compared as a number), or, for a put-back linked by
the mark puts_back (below), to the run that holds the entry it puts back; within that run it takes away only the rows of its own case.
When none of those rows has the mark "entered" or "seen", the undo cancels them: they and the undo get the state undone and leave their
place, as if they had not been asked (Exact still holds the value from before), while the rows of other cases stay, so that a change
another case asked too stays asked, with their entry ids only. A run left without rows is cancelled whole, and an undo whose rows were
cancelled already is undone with them. The marks of its own case's rows decide, not those of the run: a change that stays asked for
another case and is then ticked does not bring back what the undo cancelled. When one of its case's rows has the mark "entered" or
"seen", the undo is a change to enter: the old value back, which for an article price the aid never read is unknown (None); the run
stays as it is. An undo whose case has no row in the run it belongs to cancels nothing: it is a change to enter when one of that run's
rows has the mark "entered" or "seen", and else it is passed over, with the state undone (it is no line that cannot be read, and is not
counted as one). An undo whose run the file does not hold is a change to enter too. The rows left at the place are then cut into runs
again, an undo row among them as any other row, except a put-back linked by puts_back, which is a run of its own (below).

The state of a change that no undo cancelled comes from the marks: entered when one of its rows has the mark "entered"; else seen
when one has the mark "seen"; else dropped when one has the mark "dropped"; else replaced when a later run of its place exists (a
newer document asked another value); else already when its new value is the value of the last entered or seen run of the place
before it (the marks say that Exact holds it); else open. So a change that a newer document replaced is not asked, a change that was
dropped is not asked again for the same value as long as no other value was asked for its place in between, and a change is not
asked while the marks say that Exact holds its value. Only the open changes are asked of the buyer. Of each entry the last mark
counts, and the mark "reopened" takes its marks away.

A line of marks.jsonl is one JSON object, {"entry_id": "9f...", "mark": "entered", "at": "2026-11-18T10:32:05", "by": "buyer"}. mark()
writes one line per entry id, all in one write, and nothing for an entry of a change that is not open when the mark is "entered",
"dropped" or "seen"; a mark is checked and written under one lock, so that two presses of the page at the same moment do not both tick
one change. reopen() writes the mark "reopened" for a change only when that puts it on the list again, and else nothing; it says which
state the change would have without its marks, so that the page can say why it stays off the list. A change that was put back (an
entry of it has a put-back, by the mark puts_back, or an undo row of its own case that belongs to the entry's run, linked or not,
with the mark "entered" or "seen") never returns to the list that way: the buyer asked for the value from before.

Seen in Exact. At each pass of the runner, after the cases ran, reconcile() compares every open change of a PO line with the PO as it
stands (newest_version: PoStore.as_it_stands, the latest PDF version or a look from an export filed after it). The line is found by its
line id when the item codes agree or one is unknown, the item code of the change's line coming from step 01 of its case (item_code),
else by the item code when exactly one line of that look holds it; with no line found nothing happens. The look counts only when it
was filed (PoStore.filed_at) after the newest row of the change was prepared (its timestamp in prepared_changes.csv, Change.asked_at):
so the version the case was compared with, which shows the old value, never ticks a change off, and a put-back row (below) is ticked
off only by a look filed after it. The time that counts is when the store filed the look, not when Exact made it: a print that
reaches the aid late (the runner was stopped, the mailbox sign-in had lapsed, a PDF was saved and given to the aid later) counts as
new and can tick a change or a put-back off, also when Exact printed it before the buyer entered the change; MANUAL 2.6 asks the
buyer to check such put-backs in Exact when the aid was stopped for a while. A look that counts and shows the change's new value
marks it as seen, by "version <n>" for a PDF version and by "export" for a look from an export: a price when the line's net price
per its own basis, rounded half up to the decimals of the new value and at least two, equals it; a delivery date when the look's
date lies within the new value (date_matches of aid/comparison.py); a quantity when the two are equal as numbers. A mark is never
taken back by a later document, and article prices are never seen from a purchase order. note_value gives what Exact shows now for
an open row whose line shows, in a look that counts, neither its old nor its new value; shown_now gives what the PO as it stands
shows, whatever it is and whenever it came.

A put-back. A row that puts a change back in Exact (the review page's "Terugdraaien in Exact", aid/review/hand_list.py) is linked to the
entry it puts back by one more line of marks.jsonl, {"entry_id": the new row, "mark": "puts_back", "of": the entry, "at": ...}, written
by link_put_back. The link gives the row no state of its own; Change.puts_back holds it, and put_back_of follows it from the entry put
back. It does decide which run the undo row belongs to: a put-back belongs to the run that holds the entry it puts back, whatever
runs came after that one, and not to the last run of its place whose new value is its old value, which is the rule of an undo row
without a link (above). So a later document that asks the change's value again does not pass the put-back over: the put-back is a
change to enter, and as the newest decision for its place it replaces that later run. When the rows of the place are cut into runs
again, a put-back is a run of its own: it never joins a neighbouring row, whatever its value. Its state comes from its own marks;
else it is replaced when a later row of its place exists; else it is already when the marks say Exact holds its value; else it is
open. put_back_state works out, without writing, the state a put-back would have, so that the page answers by it (open: the put-back
is made; already: it was put back already) before anything is written.

A line of either file that cannot be read is passed over and counted, and the page says how many: the last line of a file that another
process is writing is the usual case. A line of prepared_changes.csv cannot be read when it is not UTF-8, when the CSV reader cannot
parse it (a field longer than the reader's limit, a carriage return inside a field without quotes), when it does not have the
columns of the file's header, or when it holds a kind, a version or a time that a row does not hold, or a change without its line or
its article; a line of marks.jsonl when it is no JSON object with an entry id, a mark this module knows and a time (and, for the mark
puts_back, the entry it puts back). A file that is not there holds nothing. A new mark after a last line that a crash cut off starts a
line of its own, as in the write log.
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

from aid.case import load_case
from aid.comparison import date_matches, net_unit_price
from aid.execution.ops import OpKind
from aid.execution.writer import PREPARED_FILE, UNDO_RULE_ID
from aid.po_store import PoStore, PoVersion
from aid.schema import DocLine
from aid.tryout_mark import case_is_tryout

FOLDER = "prepared"                              # under cases_root: prepared_changes.csv and marks.jsonl
MARKS_FILE = "marks.jsonl"                       # <cases_root>/prepared/marks.jsonl, appended, never rewritten
LINE_KINDS = ("po_line_price", "po_line_date", "po_line_quantity")
ARTICLE_KIND = "article_price"
KINDS = (*LINE_KINDS, ARTICLE_KIND)              # the kinds of a change to enter
Mark = Literal["entered", "dropped", "reopened", "seen"]
PUTS_BACK = "puts_back"                          # the mark that links a put-back row to the entry it puts back (link_put_back)
EXPORT = "export"                                # what a mark "seen" calls a look from an export (by), beside "version <n>"
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
    asked_at: str                                # the time of the newest row: a look at the PO filed after it can show the change
    state: State
    marked_at: str | None = None                 # for entered, seen and dropped: the time of the latest mark that gives the state
    marked_by: str | None = None                 # and who made it: "buyer", "version <n>" for a change seen in version n of the PO
                                                 # PDF, or "export" for one seen in a look from an export (EXPORT)
    marked_ids: list[str] = []                   # for entered, seen and dropped: the entry ids whose marks give the state
    puts_back: str | None = None                 # the entry id this change puts back in Exact, by the mark puts_back
    tryout: bool = False                         # whether cases made from a try-out asked it, by its place (see the module docstring)


class _Row(NamedTuple):
    """A row of prepared_changes.csv (see _row): one that holds a change to enter, or for prepared_rows one of the kinds asked."""
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
    document: str | None = None                  # the file a row attaches or detaches
    tryout: bool = False                         # whether the row's case is made from a try-out (_rows sets it)


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
    marks, links, passed_marks = _marks(Path(prepared) / MARKS_FILE)
    return _changes(rows, marks, links), passed_rows + passed_marks


def open_changes(prepared: Path) -> list[Change]:
    """The changes still to enter in Exact: those of changes() whose state is open, in the order they were first asked."""
    return [change for change in changes(prepared)[0] if change.state == "open"]


def mark(prepared: Path, ids: list[str], what: Mark, now: datetime, by: str = "buyer") -> int:
    """Mark the entries with these ids, each once: one line per entry id in the folder's marks.jsonl, at now to the second, and return
    the number of lines written. An id that is no entry of a change of the folder gets no line, and neither does an entry of a change
    that is not open when the mark is "entered", "dropped" or "seen": marking such a change writes nothing and returns 0, so that a
    later document never takes a mark back. A mark that is not one of the four raises ValueError."""
    if what not in get_args(Mark):
        raise ValueError(f"unknown mark {what!r}: expected one of {', '.join(get_args(Mark))}")
    prepared = Path(prepared)
    with _LOCK:                                  # the state is read and the lines written in one go
        found, _ = changes(prepared)
        owner = {entry_id: change for change in found for entry_id in change.ids}
        at = now.isoformat(timespec="seconds")
        lines = [json.dumps({"entry_id": entry_id, "mark": what, "at": at, "by": by}, ensure_ascii=False) for entry_id in dict.fromkeys(ids)
                 if entry_id in owner and (what == "reopened" or owner[entry_id].state == "open")]
        if lines:
            _append(prepared / MARKS_FILE, lines)
        return len(lines)


def reopen(prepared: Path, ids: list[str], now: datetime, by: str = "buyer") -> State | None:
    """Put a handled change on the list again, the mark "reopened" for each entry id given (a tick or a drop taken back), when that
    makes the change that holds the first id open again, and return the state the change has without the marks of these entries:
    "open" when the lines were written. Otherwise nothing is written, and the state says why the change does not return to the list:
    "replaced" (a newer value of its place came in after it), "already" (the marks say that Exact holds its value) or "undone" (its own
    put-back, or another undo of its case, would cancel it once it is not entered: the buyer asked for the value from before). A change
    an entry of which a put-back links to (the mark puts_back), or an entry of which has an undo row of its own case that belongs to
    the entry's run by the undo rule, linked or not (MANUAL 3.10's recipe), and has the mark "entered" or "seen", is "undone" whatever
    else: whichever case's row the undo undid, and whatever rows of other cases the run holds. An undo of the same case that belongs
    to another run of the place undoes another value and does not count. None when no change holds the first id. The state is worked
    out and the lines written under the lock of mark()."""
    prepared = Path(prepared)
    ids = list(dict.fromkeys(ids))
    with _LOCK:
        rows, _ = _rows(prepared / PREPARED_FILE)
        marks, links, _ = _marks(prepared / MARKS_FILE)
        now_ids = next((change.ids for change in _changes(rows, marks, links) if ids and ids[0] in change.ids), [])
        if set(links.values()) & {*ids, *now_ids} or _undone_in_exact({*ids, *now_ids}, rows, marks, links):
            return "undone"                      # put back: whichever case's row the undo undid, whatever rows the run holds
        after = _changes(rows, {entry_id: one for entry_id, one in marks.items() if entry_id not in ids}, links)
        change = next((change for change in after if ids and ids[0] in change.ids), None)
        if change is None or change.state != "open":
            return change.state if change is not None else None
        at, entries = now.isoformat(timespec="seconds"), {row.entry_id for row in rows}
        _append(prepared / MARKS_FILE, [json.dumps({"entry_id": entry_id, "mark": "reopened", "at": at, "by": by}, ensure_ascii=False)
                                        for entry_id in ids if entry_id in entries])
        return "open"


def _undone_in_exact(entries: set[str], rows: list[_Row], marks: dict[str, _Mark], links: dict[str, str]) -> bool:
    """Whether an entry among these has an undo row of its own case, linked or not (MANUAL 3.10's recipe too), that has the mark
    "entered" or "seen" and belongs to the run that holds the entry (_owners): Exact then holds the value from before, as the buyer
    asked. An undo of the same case that belongs to another run of the place undoes another value, and does not count."""
    places: dict[tuple, list[tuple[int, _Row]]] = {}
    for index, row in enumerate(rows):
        places.setdefault(_place(row), []).append((index, row))
    return any(own is not None and undo.entry_id in marks and marks[undo.entry_id].what in _HELD
               and any(row.entry_id in entries and row.case_id == undo.case_id for _, row in own)
               for place in places.values() for _, undo, own in _owners(_runs(place, undo_alone=True), links))


def link_put_back(prepared: Path, entry_id: str, of: str, now: datetime) -> None:
    """Link the prepared row `entry_id` to the entry `of` that it puts back in Exact: one line {"entry_id", "mark": "puts_back", "of",
    "at"} in the folder's marks.jsonl, at now to the second. The link gives the row no state."""
    line = json.dumps({"entry_id": entry_id, "mark": PUTS_BACK, "of": of, "at": now.isoformat(timespec="seconds")}, ensure_ascii=False)
    with _LOCK:
        _append(Path(prepared) / MARKS_FILE, [line])


def put_back_state(prepared: Path, of: str, now: datetime, row_id: str | None = None) -> State | None:
    """The state that the put-back of the entry `of` would have on the list, worked out without writing anything: the rows of the
    folder with one more row as undo_entry prepares it through the prepared writer (the inverse of the row of `of`: its old and its
    new value swapped, of its case, with the rule id "undo", at now, at the end of the file), linked to `of` as link_put_back links
    it; or, with row_id, the row of the folder that puts it back already, linked to it. None when the folder holds no row `of`, or no
    row row_id."""
    prepared = Path(prepared)
    rows, _ = _rows(prepared / PREPARED_FILE)
    marks, links, _ = _marks(prepared / MARKS_FILE)
    own = next((row for row in rows if row.entry_id == of), None)
    if own is None or (row_id is not None and not any(row.entry_id == row_id for row in rows)):
        return None
    if row_id is None:
        row_id = "put back"                      # no entry id of a row of the file holds a space (_row)
        rows = [*rows, own._replace(entry_id=row_id, old_value=own.new_value, new_value=own.old_value, rule_id=UNDO_RULE_ID,
                                    timestamp=now.isoformat())]
    found = next((change for change in _changes(rows, marks, {**links, row_id: of}) if row_id in change.ids), None)
    return found.state if found is not None else None


def put_back_of(prepared: Path, entry_id: str) -> str | None:
    """The entry id of the row that puts this entry back, by the marks puts_back of the folder; the latest such row when there are
    several, and None when there is none."""
    _, links, _ = _marks(Path(prepared) / MARKS_FILE)
    return next((row for row, of in reversed(links.items()) if of == entry_id), None)


def prepared_rows(prepared: Path, kinds: tuple[str, ...]) -> list[_Row]:
    """The rows of the folder's prepared_changes.csv of these kinds, in the order of the file; a line that cannot be read is passed
    over, as changes() passes it over."""
    return _rows(Path(prepared) / PREPARED_FILE, kinds)[0]


# --- seen in a newer version of the PO ---

def reconcile(prepared: Path, store: PoStore, cases_root: Path, now: datetime) -> int:
    """Compare every open change of a PO line with the PO as it stands, and mark each change that it shows, when it was filed after the
    change was asked, as seen at now, by "version <n>" or "export" (see the module docstring); return the number of changes marked. A
    second pass over the same documents marks nothing more: a change marked is no longer open."""
    seen = 0
    for change in open_changes(prepared):
        look = _look(change, store, cases_root)
        if look is not None and _shows(change.kind, look.shown, change.new_value):
            if mark(prepared, change.ids, "seen", now, by=look.by) > 0:
                seen += 1
    return seen


def shown_now(change: Change, store: PoStore, cases_root: Path) -> str | None:
    """The value the PO as it stands (newest_version) shows for the change's line and field, in normal form, whenever it was filed: a
    price as the line's net price per its own basis, rounded half up to the decimals of the change's new value and at least two; a
    delivery date or a quantity as the look holds it. None for an article price, a PO the store does not know, a line not found (see
    the module docstring) and a value that is missing or uncertain."""
    if change.kind not in LINE_KINDS:
        return None
    version = newest_version(store, change.po_number)
    line = _line_of(change, version, cases_root) if version is not None else None
    return _value_of(change, line) if line is not None else None


def note_value(change: Change, store: PoStore, cases_root: Path) -> str | None:
    """What Exact shows now for a change of a PO line, when the PO as it stands, filed after the change was asked, shows neither its old
    nor its new value: the value of the note of its row on the list; None otherwise."""
    look = _look(change, store, cases_root)
    if look is None or _shows(change.kind, look.shown, change.old_value) or _shows(change.kind, look.shown, change.new_value):
        return None
    return look.shown


def newest_version(store: PoStore, po_number: str) -> PoVersion | None:
    """The PO as the aid last saw it in Exact: the PO as it stands (PoStore.as_it_stands), its latest PDF version or a look from an
    export filed after it; None for a PO the store does not know, or whose files cannot be read. The one place that says which look
    at a PO the list compares a change with."""
    try:
        return store.as_it_stands(po_number)
    except (OSError, ValueError):
        return None


def item_code(change: Change, cases_root: Path) -> str | None:
    """The item code of the change's line in the version its case was compared with: step 01 of the newest case of the change that
    holds that version; None when no case does, or the line has no item code there."""
    for case_id in reversed(change.case_ids):
        try:
            versions = [PoVersion.model_validate(version) for version in load_case(cases_root, case_id).read_step("po_versions")["versions"]]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        version = next((version for version in versions if version.version == change.version), None)
        if version is not None:
            line = next((line for line in version.document.lines if line.line_id == change.line_id), None)
            return line.item_code.value if line is not None else None
    return None


class _Look(NamedTuple):
    """The PO as it stands, as a change is compared with it: the value it shows for the change, and what a mark "seen" calls it."""
    version: PoVersion
    shown: str
    by: str                                      # "version <n>" for a PDF version, "export" for a look from an export


def _look(change: Change, store: PoStore, cases_root: Path) -> _Look | None:
    """The PO as it stands with the value it shows for the change and what a mark calls it, when it was filed after the newest row of
    the change was prepared (see the module docstring); None otherwise, for an article price, and when the store cannot say when the
    look was filed."""
    if change.kind not in LINE_KINDS:
        return None
    version = newest_version(store, change.po_number)
    standing = _filed(store, change.po_number, version) if version is not None else None
    if standing is None or standing[1].timestamp() <= datetime.fromisoformat(change.asked_at).timestamp():
        return None                              # it came before the change was asked: it cannot show the change made
    line = _line_of(change, version, cases_root)
    shown = _value_of(change, line) if line is not None else None
    return _Look(version, shown, standing[0]) if shown is not None else None


def _filed(store: PoStore, po_number: str, standing: PoVersion) -> tuple[str, datetime] | None:
    """What a mark calls the PO as it stands, and when it was filed (PoStore.filed_at): a PDF version is its latest version, "v<n>";
    a look from an export is its newest look, "e<n>" the n-th (po_store.py names the folders so). None when the store cannot say."""
    try:
        versions = store.versions(po_number)
        if versions and standing == versions[-1]:
            return f"version {standing.version}", store.filed_at(po_number, f"v{standing.version}")
        return EXPORT, store.filed_at(po_number, f"e{len(store.snapshots(po_number))}")
    except (OSError, ValueError):
        return None


def _line_of(change: Change, version: PoVersion, cases_root: Path) -> DocLine | None:
    """The line of the version that the change is about (see the module docstring): by its line id when the item codes agree or one
    is unknown, else by the item code when exactly one line holds it; None when no line is found."""
    code = item_code(change, cases_root)
    numbered = next((line for line in version.document.lines if line.line_id == change.line_id), None)
    if numbered is not None and (code is None or numbered.item_code.value is None or numbered.item_code.value == code):
        return numbered
    holding = [line for line in version.document.lines if code is not None and line.item_code.value == code]
    return holding[0] if len(holding) == 1 else None


def _value_of(change: Change, line: DocLine) -> str | None:
    """The value the line shows for the change's field, in normal form (see shown_now); None when it is missing or uncertain."""
    if change.kind == "po_line_price":
        if any(value.uncertain for value in (line.unit_price, line.net_price, line.discount_pct, line.price_basis)):
            return None
        own = line if line.price_basis.value is not None else line.model_copy(      # no basis: the net price as it stands, per one
            update={"price_basis": line.price_basis.model_copy(update={"value": "1"})})
        price = net_unit_price(own, None, max(2, _decimals(change.new_value)))
        return format(price, "f") if price is not None else None
    value = line.delivery_date if change.kind == "po_line_date" else line.quantity
    return value.value if not value.uncertain else None


def _shows(kind: str, shown: str | None, value: str | None) -> bool:
    """Whether a value a version shows is the value of a change: a delivery date that lies within it (date_matches), a price or a
    quantity equal to it as a number."""
    if shown is None or value is None:
        return False
    if kind == "po_line_date":
        try:
            return date_matches(shown, value)
        except ValueError:
            return False
    first, second = _number(shown), _number(value)
    return first is not None and second is not None and first == second


def _decimals(text: str | None) -> int:
    """The number of decimals of a number in normal form, 0 for a text that is no number."""
    number = _number(text)
    return max(-number.as_tuple().exponent, 0) if number is not None else 0


# --- the change of a run ---

def _changes(rows: list[_Row], marks: dict[str, _Mark], links: dict[str, str]) -> list[Change]:
    """The changes of the rows, in the order they were first asked, each with its state by the marks and its put-back link."""
    places: dict[tuple, list[tuple[int, _Row]]] = {}
    for index, row in enumerate(rows):
        places.setdefault(_place(row), []).append((index, row))
    found = [pair for place in places.values() for pair in _of_place(place, marks, links)]
    return [change for _, change in sorted(found, key=lambda pair: pair[0])]


def _place(row: _Row) -> tuple:
    """The place of a change: the kind, the PO and the line of a PO line, the article of an article price; and, last, whether a case
    made from a try-out asked it (the row's tryout), so that a try-out's rows are never one run with those of other cases (see the
    module docstring)."""
    where = (row.kind, row.po_number, row.line_id) if row.kind in LINE_KINDS else (row.kind, row.article_code)
    return (*where, row.tryout)


def same_value(kind: str, one: str | None, other: str | None) -> bool:
    """Whether two values of a kind are the same, as the runs compare them: a price or a quantity as a number, anything else as its
    text."""
    return _same(kind, one, other)


def place_of(change: Change) -> tuple:
    """The place of a change, as _place gives it for its rows: whether a try-out asked it, last, included."""
    where = (change.kind, change.po_number, change.line_id) if change.kind in LINE_KINDS else (change.kind, change.article_code)
    return (*where, change.tryout)


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


def _of_place(rows: list[tuple[int, _Row]], marks: dict[str, _Mark], links: dict[str, str]) -> list[tuple[int, Change]]:
    """The changes of the rows of one place, in the order of the file, each with the index of its first row (see the module
    docstring): of each run the rows that undos cancelled, each undo row that cancelled them or was passed over, then the runs of the
    rows left."""
    runs = _runs(rows, undo_alone=True)
    gone = _gone(runs, marks, links)
    found = []
    for run in runs:
        out = [pair for pair in run if pair[0] in gone]
        if out:
            found.append((out[0][0], _change([row for _, row in out], marks, links, False, [], undone=True)))
    left = _runs([pair for run in runs for pair in run if pair[0] not in gone], undo_alone=False, alone=set(links))
    held: list[str | None] = []                  # the value Exact holds by the marks: that of the last entered or seen run so far
    for number, run in enumerate(left):
        change = _change([row for _, row in run], marks, links, number == len(left) - 1, held)
        if change.state in _HELD:
            held = [change.new_value]
        found.append((run[0][0], change))
    return found


def _runs(rows: list[tuple[int, _Row]], undo_alone: bool, alone: set[str] = frozenset()) -> list[list[tuple[int, _Row]]]:
    """The rows of one place cut into runs, neighbours with the same new value; with undo_alone an undo row is a run of its own, and
    so is always a row whose entry id is in `alone` (a put-back linked by puts_back: it never joins a neighbour, whatever its value)."""
    runs: list[list[tuple[int, _Row]]] = []
    for index, row in rows:
        before = runs[-1][-1][1] if runs else None
        if (before is not None and _same(row.kind, before.new_value, row.new_value)
                and not (undo_alone and UNDO_RULE_ID in (row.rule_id, before.rule_id))
                and row.entry_id not in alone and before.entry_id not in alone):
            runs[-1].append((index, row))
        else:
            runs.append([(index, row)])
    return runs


def _gone(runs: list[list[tuple[int, _Row]]], marks: dict[str, _Mark], links: dict[str, str]) -> set[int]:
    """The indexes of the rows that leave their place (runs of one place, an undo row a run of its own; see the module docstring):
    an undo cancels the rows of its own case in the run it belongs to (_owners), with itself, when none of them has the mark
    "entered" or "seen"; an undo whose case has no row there is passed over when no row of the run has such a mark. Any other undo
    stays, a change to enter."""
    gone: set[int] = set()
    for index, undo, own in _owners(runs, links):
        if own is None:
            continue                                 # the file does not hold its run
        mine = [pair for pair in own if pair[1].case_id == undo.case_id]
        if not _held(mine or own, marks):
            gone |= {index, *(row_index for row_index, _ in mine)}
    return gone


def _owners(runs: list[list[tuple[int, _Row]]], links: dict[str, str]):
    """Each undo row of the runs of one place (an undo row a run of its own), as (its index, the row, the run it belongs to): the last
    run before it, not an undo, whose new value is the undo's old value, or for a put-back that links (links, row -> entry) the run
    that holds the entry it puts back; None when the file does not hold that run."""
    for number, run in enumerate(runs):
        index, undo = run[0]
        if undo.rule_id != UNDO_RULE_ID:
            continue
        if undo.entry_id in links:                   # a put-back: the run of the entry it puts back, whatever came after that run
            own = next((runs[earlier] for earlier in range(number) if runs[earlier][0][1].rule_id != UNDO_RULE_ID
                        and any(row.entry_id == links[undo.entry_id] for _, row in runs[earlier])), None)
        else:
            own = next((runs[earlier] for earlier in reversed(range(number)) if runs[earlier][0][1].rule_id != UNDO_RULE_ID
                        and _same(undo.kind, runs[earlier][-1][1].new_value, undo.old_value)), None)
        yield index, undo, own


def _held(rows: list[tuple[int, _Row]], marks: dict[str, _Mark]) -> bool:
    """Whether one of the rows has the mark "entered" or "seen": the marks say that Exact holds its value."""
    return any(row.entry_id in marks and marks[row.entry_id].what in _HELD for _, row in rows)


def _change(run: list[_Row], marks: dict[str, _Mark], links: dict[str, str], last: bool, held: list[str | None],
            undone: bool = False) -> Change:
    """The change of one run of a place, with its state (see the module docstring): last says whether it is the last run of its
    place, held is [the new value of the last entered or seen run before it], or [] when there is none, and undone that an undo
    cancelled it, or that it is the undo that did. links are the put-back links of marks.jsonl, row -> the entry it puts back."""
    newest = run[-1]
    state, deciding = ("undone", []) if undone else _state(run, marks, last, held)
    latest = max((marks[entry_id] for entry_id in deciding), key=lambda one: one.line) if deciding else None
    return Change(ids=[row.entry_id for row in run], kind=newest.kind, po_number=newest.po_number, version=newest.version,
                  line_id=newest.line_id, article_code=newest.article_code, old_value=newest.old_value, new_value=newest.new_value,
                  case_ids=list(dict.fromkeys(row.case_id for row in run)), rule_id=newest.rule_id, prepared_at=run[0].timestamp,
                  asked_at=newest.timestamp,
                  state=state, marked_at=latest.at if latest else None, marked_by=latest.by if latest else None, marked_ids=deciding,
                  puts_back=next((links[row.entry_id] for row in reversed(run) if row.entry_id in links), None), tryout=newest.tryout)


def _state(run: list[_Row], marks: dict[str, _Mark], last: bool, held: list[str | None]) -> tuple[str, list[str]]:
    """The state of a run that no undo cancelled, and the entry ids of its rows whose marks give it (see the module docstring)."""
    for what in _DONE:
        deciding = [row.entry_id for row in run if row.entry_id in marks and marks[row.entry_id].what == what]
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


def _rows(path: Path, kinds: tuple[str, ...] = KINDS) -> tuple[list[_Row], int]:
    """The rows of prepared_changes.csv of the kinds (by default those of a change to enter), in the order of the file, and the
    number of lines passed over. The first line is the header; without the writer's columns there no line can be read. A line that
    repeats the header is no row. A row whose case, under the folder's cases_root, is made from a try-out has tryout set (_made_up)."""
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
        elif row.kind in kinds:
            rows.append(row)
    made_up = _made_up(Path(path).parent.parent, rows)                # the cases of try-outs, under the folder's cases_root
    return [row._replace(tryout=True) if row.case_id in made_up else row for row in rows], passed


def _made_up(cases_root: Path, rows: list[_Row]) -> set[str]:
    """The case ids of the rows whose case is made from a try-out (case_is_tryout of aid/tryout_mark.py), each case read once; a
    case that cannot be read, and a price list, are none."""
    return {case_id for case_id in dict.fromkeys(row.case_id for row in rows) if case_is_tryout(cases_root, case_id)}


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
                rule_id=values["rule_id"], timestamp=values["timestamp"], document=values.get("document") or None)


def _marks(path: Path) -> tuple[dict[str, _Mark], dict[str, str], int]:
    """The last mark of each entry of marks.jsonl, an entry whose last mark is "reopened" having none; the entry each put-back row
    puts back, by the mark puts_back (row -> entry, the later line winning); and the number of lines passed over."""
    found: dict[str, _Mark] = {}
    links: dict[str, str] = {}
    passed = 0
    for number, line in enumerate(_lines(path)):
        try:
            record = json.loads(line.decode("utf-8"))
            if not isinstance(record, dict):
                raise ValueError("no object")
            entry_id, what, at, by = record.get("entry_id"), record.get("mark"), record.get("at"), record.get("by")
            of = record.get("of")
            if not (isinstance(entry_id, str) and entry_id and isinstance(at, str) and (by is None or isinstance(by, str))):
                raise ValueError("no mark")
            if what not in get_args(Mark) and not (what == PUTS_BACK and isinstance(of, str) and of):
                raise ValueError("no mark")
            datetime.fromisoformat(at)
        except ValueError:                       # also a line that is no JSON, or not UTF-8
            passed += 1
            continue
        if what == PUTS_BACK:
            links.pop(entry_id, None)
            links[entry_id] = of
        elif what == "reopened":
            found.pop(entry_id, None)
        else:
            found[entry_id] = _Mark(what, at, by, number)
    return found, links, passed


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
