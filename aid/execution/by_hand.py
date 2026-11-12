"""The list of the changes that the buyer enters in Exact by hand: a view over the prepared changes with one file of marks beside them.

From the buyer's seat no program reaches Exact, so the aid prepares every change: a row of <cases_root>/prepared/prepared_changes.csv,
written by the prepared writer (aid/execution/writer.py). The buyer enters in Exact what this list holds and ticks it off on the
review page's tab "In Exact invoeren" (aid/review/page.py, the list as it shows it in aid/review/hand_list.py), and the digest names
the number still to enter (aid/notify.py). The ticks are kept in <cases_root>/prepared/marks.jsonl, appended and never rewritten. A
change that a writer makes in a system one day never reaches prepared_changes.csv, so it never reaches the list. Nothing here changes
a case's result, a write log, the disposition log or prepared_changes.csv: marks.jsonl is the only file this module writes.

What a change is. A row is a change to enter when its kind is the price, the delivery date or the quantity of a PO line (LINE_KINDS)
or the purchase price of an article; a row that attaches or detaches a document or sets a status is never a row to tick. The place
of a change is the kind, the PO number and the line of the order for a PO line, and the article code for an article price, whichever
order or price list asked, and whether a case made from a try-out document asked it (tryout_case of aid/tryout_mark.py: the case of
the row's case id is one; a price list never is). The line of the order is not the line id alone: a PDF and an export may number
the lines differently, and Exact may number a print by the place of its lines. Step 01 of a row's case says which item code the
row's line id held in the version the case was compared with (_placed). Rows name one line when they carry one line id and their
item codes agree, or, whatever their line ids, when they carry an item code that one line of that version holds. An item code
that several lines hold (two deliveries of one article) tells no line from another, and neither does a line id across prints: such
a row names its line by what tells it from the other lines of its article where it was asked, its quantity, its delivery date and
its unit price, the field it changes apart; when not even those tell it, by its own print (the sha256 of the file its version was
read from) and its line id. So the changes of two deliveries are never one row, and one tick never covers both. A row whose case
says no item code (the case is gone, the line has none) names the
line of its line id, and is taken with the rows of that line id whose item code is known when those are rows of its own order and
name one line. So the list's
states and the comparison judge one thing as one place: a change asked under the other numbering is not asked again once its twin
is ticked, and two changes that carry one line id and name two lines never push each other out. So the rows of a try-out are never one change with those of other cases, and neither
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
newer document asked another value), or when its newest row has the mark "replaced" (the supplier's newest document no longer
asks it: withdraw, below); else already when its new value is the value of the last entered or seen run of the place before it
(the marks say that Exact holds it); else open. So a change that a newer document replaced is not asked, a change that was
dropped is not asked again for the same value as long as no other value was asked for its place in between, and a change is not
asked while the marks say that Exact holds its value. Only the open changes are asked of the buyer. Of each entry the last mark
counts, and the mark "reopened" takes its marks away.

A line of marks.jsonl is one JSON object, {"entry_id": "9f...", "mark": "entered", "at": "2026-11-18T10:32:05.412", "by": "buyer"},
its time with the fraction of the second when the clock gives one (a print filed earlier in the same second came before the tick;
a line written to the second, as before, is read as it is). mark() writes one line per entry id, all in one write, and nothing for
an entry of a change that is not open when the mark is "entered", "dropped", "seen" or "replaced"; a mark is checked and written
under one lock, so that two presses of the page at the same moment do not both tick one change. reopen() writes the mark "reopened" for a change only when that puts it on the list again, and else nothing; it says which
state the change would have without its marks, so that the page can say why it stays off the list. A change that was put back (an
entry of it has a put-back, by the mark puts_back, or an undo row of its own case that belongs to the entry's run, linked or not,
with the mark "entered" or "seen") never returns to the list that way: the buyer asked for the value from before.

Seen in Exact. At each pass of the runner, after the cases ran, reconcile() compares every open change of a PO line with the PO as it
stands for the list (newest_version: PoStore.given_last, the latest PDF version or the latest look from an export, the one the
store was given last). The line is found by its
line id when the item codes agree or one is unknown, the item code of the change's line coming from step 01 of its case (item_code),
else by the item code when exactly one line of that look holds it; with no line found nothing happens. The look counts only when the
store was given it (PoStore.given_at) after the newest row of the change was prepared (its timestamp in prepared_changes.csv,
Change.asked_at): so the version the case was compared with, which shows the old value, never ticks a change off, and a put-back
row (below) is ticked off only by a look given after it. given_at is when the look was filed, or later, when a print that shows
the PO so came again (the same PDF, the look's own export, an export with its rows): a print that shows nothing new is a look at
the order at the time it was given. The time that counts is when the store was given the look, not when Exact made it: a print that
reaches the aid late (the runner was stopped, the mailbox sign-in had lapsed, a PDF was saved and given to the aid later) counts as
new and can tick a change or a put-back off, also when Exact printed it before the buyer entered the change; MANUAL 2.6 asks the
buyer to check such put-backs in Exact when the aid was stopped for a while. A look that counts and shows the change's new value
marks it as seen, by "version <n>" for a PDF version and by "export" for a look from an export: a price when the line's net price
per its own basis, rounded half up to the decimals of the new value and at least two, equals it; a delivery date when the look's
date lies within the new value (date_matches of aid/comparison.py); a quantity when the two are equal as numbers. The mark carries
the time of marking, not the time at which the runner's pass began: the value then counts for an older print that the same pass
filed before the look. When the change's item code stands on several lines, of the look or of the version the change was asked
in, neither the code nor a line id tells one from another (a print may number its lines by their place). The change's line is
then, in the very print its case was compared with, the line it was asked for, and in any other print the one line of that
article that agrees with the line it was asked for in its quantity, its delivery date and its unit price, the changed field
apart; when no line, or more than one, agrees so, the change has no line there (_line_of). A price is compared only with a line
that states the price basis the change was asked per. Article prices are
never seen from a purchase order, and a drop is never taken back by a later document.

A newer print ends a tick that it contradicts. A tick is the buyer's word, believed until a newer print shows the order. After the
changes seen, reconcile() ends (contradict) each tick of a PO line that the PO as it stands contradicts: at each place, the last
change that is entered or seen, when the store was given that look after its mark (given_at: also a print that shows nothing new,
so an export with the rows of the look before ends a tick given without the entry) and it shows another value for its line. A
print in which the tick's line cannot be found ends it too, when the change's item code is known and no line of that article
shows the ticked value: the article stands on no line, or on several of which none agrees with the line the change was asked
for. When one of those lines shows the ticked value the tick stands, and so does a tick whose line the look does hold while its
value there cannot be told (missing or uncertain, a price per another basis). Doubt is resolved towards asking again. The marks
"entered" and "seen" of its rows are taken away with the mark "reopened", by "version <n>" or "export", with the value the print
showed ("shown", None for a line not found): the change is judged as if it had never been ticked, so it is open again, its value
is laid over no version, and the next case finds the difference and asks it as any other. The open change says which print ended
its tick and what it showed (Change.ended), for the note of its row on the tab. A print that shows the value, or was given at the
time of the mark or before it, ends nothing; if the print was an older one given late, the buyer ticks again, and the later tick
counts. The change of
a try-out is left as it is: its tick never counts in a supplier's case. The pipeline runs reconcile() for the PO of a case before
it lays the entered values over the selected version, so that no case closes as confirmed on a tick that the newest print
contradicts.

A change the supplier withdrew. withdraw() takes off the list, with the mark "replaced" by "case <id>", the open changes of a line
whose value a case's document names again as the marks say Exact holds it: a later document had asked another value, and the
newest one returns to the entered one. The buyer's own put-back stays, a case made from a try-out takes off try-outs' changes
alone, and when the supplier asks the other value once more, the new row has no such mark and the change is open again. A
change taken off so says it (Change.withdrawn), so that the pre-check no longer counts the write that asked it as prepared.
note_value gives what Exact shows now for
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
open. A put-back of an entry that is no row of the file puts back a write that a program made in Exact (the review tab's undo of
such a write, prepared by the page's writer): it is never already, because the marks that say what Exact holds were made before
that write and say nothing about what Exact holds after it. A put-back whose own row can no longer be read (its line of the file
is damaged and passed over) is judged the same way: its row is not among those read, and since that row was entered, Exact holds
its value and the put-back is asked. put_back_state works out, without writing, the state a put-back would
have, so that the page answers by it (open: the put-back is made; already: it was put back already) before anything is written.

Entered values. The pipeline (aid/pipeline.py) compares a document with the version it selected as Exact holds it by the marks:
as_entered lays the entered values over that version, and changes neither the store nor which version is selected. entered_values
gives them, for the lines of one PO: for each place the new value of its last change that is entered or seen, as the states above
judge the changes, with the time of the mark that gives that state. as_entered gives each of them its line of the version (below);
of those that name one line there, the one marked last decides (_held_on). So an open, dropped, replaced or undone change never counts;
a put-back that is entered or seen is such a change, and the old value is back; a put-back that is still open, or was dropped, is
none, and the entered value stays. The marks of a change that cases made from a try-out asked count only in a case made from a
try-out; there the two places of a line, the try-out's and the other, are decided by the change marked last, the one whose mark
stands last in marks.jsonl. A value counts for a version that the store was last given before the mark was made (given_at); a
version or a look given at the time of the mark or after it is taken as it is, as the print that Exact made after the change. The
line of the version is
found as a look's line is found above, so that a value entered for a case that was compared with one version lands on the right
line of a version that numbers its lines otherwise; a value whose line the version does not have, or cannot be told from another
line of it (several lines hold its item code, and no line or more than one agrees with the line the change was asked for), is not
applied, and neither is a
price whose line there states another price basis than the line the change was asked for (a price is a price per that basis; a
line without a basis counts per one, and a change whose case does not say is taken as stated per the line's own), nor a value that
is no number or no delivery date in normal form. A value that the line shows already, as "seen" judges it, replaces nothing: the
version a change was seen in stays as it is. A price is written over the line's net price when the line states a net price or a
discount, else over its unit price; a delivery date and a quantity over their own values. The value written has no text and no
source and is not uncertain, and the version's total, and whether it is above the authorization limit, are worked out again as the
store works them out (po_total of aid/po_store.py, over the unit prices). While prepared_changes.csv or marks.jsonl cannot be read,
entered_values and as_entered raise the OSError, so that their caller knows that the marks did not count. held_now gives, for a
reader that judges an earlier comparison again (the pre-check), what the marks say of each line of a version as the list is now;
given the store, it counts a tick that the PO as it stands contradicts as ended, by contradict's own rule and in reading only.

A line of either file that cannot be read is passed over and counted, and the page says how many: the last line of a file that another
process is writing is the usual case. A line of prepared_changes.csv cannot be read when it is not UTF-8, when the CSV reader cannot
parse it (a field longer than the reader's limit, a carriage return inside a field without quotes), when it does not have the
columns of the file's header, or when it holds a kind, a version or a time that a row does not hold, or a change without its line or
its article; a line of marks.jsonl when it is no JSON object with an entry id, a mark this module knows and a time (and, for the mark
puts_back, the entry it puts back). A file that is not there holds nothing. A prepared_changes.csv whose first line lacks the
columns of the prepared writer (it was saved from a spreadsheet with another separator) has no line that can be read: that is a
list that cannot be read, ListUnread, an OSError as for a file that another program holds, and never an empty list, so that no
tick of it is taken as not made. A new mark after a last line that a crash cut off starts a
line of its own, as in the write log.
"""
import csv
import json
import os
import threading
from collections.abc import Collection, Iterable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path, PureWindowsPath
from typing import Literal, NamedTuple, get_args

from pydantic import BaseModel

from aid.comparison import date_matches, date_span, net_unit_price
from aid.execution.ops import OpKind
from aid.execution.writer import PREPARED_FILE, UNDO_RULE_ID
from aid.po_store import PoStore, PoVersion, po_total
from aid.schema import DocLine, DocValue
from aid.tryout_mark import case_is_tryout

FOLDER = "prepared"                              # under cases_root: prepared_changes.csv and marks.jsonl
MARKS_FILE = "marks.jsonl"                       # <cases_root>/prepared/marks.jsonl, appended, never rewritten
LINE_KINDS = ("po_line_price", "po_line_date", "po_line_quantity")
ARTICLE_KIND = "article_price"
KINDS = (*LINE_KINDS, ARTICLE_KIND)              # the kinds of a change to enter
FIELDS = dict(zip(LINE_KINDS, ("price", "delivery_date", "quantity")))     # what each kind changes, as the comparison names the field
Mark = Literal["entered", "dropped", "reopened", "seen", "replaced"]
PUTS_BACK = "puts_back"                          # the mark that links a put-back row to the entry it puts back (link_put_back)
EXPORT = "export"                                # what a mark "seen" calls a look from an export (by), beside "version <n>"
WITHDRAWN = "replaced"                           # the mark of an open change that a later document of its supplier no longer asks
STEP_01 = "01_po_versions.json"                  # the step of a case that holds the versions it was compared with (aid/pipeline.py)
ASKED = "asked now"                              # the entry id of the row a look ahead adds (ArticleLook.asked): no entry id of a
                                                 # row of the file holds a space (_row)
State = Literal["open", "entered", "seen", "dropped", "already", "replaced", "undone"]
_NUMBERS = ("po_line_price", "po_line_quantity", "article_price")     # the kinds whose values compare as numbers
_NEEDED = ("entry_id", "case_id", "po_number", "version", "line_id", "article_code", "kind", "old_value", "new_value", "rule_id",
           "timestamp")                          # the columns a row is read from: those of the writer, the document aside
_DONE = ("entered", "seen", "dropped")           # the marks that give a change its state, in the order they decide it
_HELD = ("entered", "seen")                      # the marks that say Exact holds a change's value
_LOCK = threading.Lock()


class ListUnread(OSError):
    """prepared_changes.csv cannot be understood: its first line lacks the columns of the prepared writer (a file saved from a
    spreadsheet with another separator, for one). An OSError, so that it counts wherever a file that cannot be read counts: no tick
    of such a list is taken as not made."""


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
    place: tuple = ()                            # the place of its rows (_place): for a PO line the line of the order they name
    item: str | None = None                      # for a PO line: its item code where the newest row that knows one was asked
    ended: tuple[str, str | None] | None = None  # for an open change whose tick a print ended (contradict): what the mark calls that
                                                 # print ("version <n>" or "export") and the value it showed for the line, None when
                                                 # the line could not be found in it
    withdrawn: bool = False                      # replaced by the mark "replaced": its supplier's newest document no longer asks it


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
    where: tuple = ()                            # for a PO line: the line of the order it names (_placed sets it)
    asked: "_Asked | None" = None                # for a PO line: what step 01 of its case says of that line (_placed sets it)


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
    """Mark the entries with these ids, each once: one line per entry id in the folder's marks.jsonl, at now with the fraction of its
    second (a print filed earlier in the same second came before it), and return the number of lines written. An id that is no entry
    of a change of the folder gets no line, and neither does an entry of a change that is not open when the mark is "entered",
    "dropped", "seen" or "replaced": marking such a change writes nothing and returns 0. A mark that is not one of the five raises
    ValueError."""
    if what not in get_args(Mark):
        raise ValueError(f"unknown mark {what!r}: expected one of {', '.join(get_args(Mark))}")
    prepared = Path(prepared)
    with _LOCK:                                  # the state is read and the lines written in one go
        found, _ = changes(prepared)
        owner = {entry_id: change for change in found for entry_id in change.ids}
        at = now.isoformat()
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
        at, entries = now.isoformat(), {row.entry_id for row in rows}
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


class ArticleLook:
    """The article prices of the list as one read of the folder's two files gives them, for the tab "Prijslijst"
    (aid/review/pricelist_page.py) to look at the list and ahead of a press, with nothing written. The rows of try-outs are left
    out: a price list is never a try-out, so its rows never share a place with theirs (see the module docstring)."""

    def __init__(self, prepared: Path):
        rows, _ = _rows(Path(prepared) / PREPARED_FILE, (ARTICLE_KIND,))
        self._marks, self._links, _ = _marks(Path(prepared) / MARKS_FILE)
        self._places: dict[str, list[tuple[int, _Row]]] = {}
        for index, row in enumerate(rows):
            if not row.tryout:
                self._places.setdefault(row.article_code, []).append((index, row))

    def open(self) -> dict[str, Change]:
        """The open change of each article that has one: the article code -> the change. A place has at most one."""
        found = {}
        for code, rows in self._places.items():
            change = next((change for _, change in _of_place(rows, self._marks, self._links) if change.state == "open"), None)
            if change is not None:
                found[code] = change
        return found

    def asked(self, article_code: str, new_value: str, without: Collection[str] = ()) -> list[Change]:
        """The changes of the article's place, in the order of the file, as they would be with one more row at the end of the file
        that asks this purchase price (its entry id is ASKED), and without the marks of the entries `without`, as the mark
        "reopened" takes them away."""
        rows = self._places.get(article_code, [])
        row = _Row(entry_id=ASKED, case_id=ASKED, po_number="", version=None, line_id=None, article_code=article_code, kind=ARTICLE_KIND,
                   old_value=None, new_value=new_value, rule_id="", timestamp="")
        marks = {entry_id: one for entry_id, one in self._marks.items() if entry_id not in without} if without else self._marks
        return [change for _, change in _of_place([*rows, (rows[-1][0] + 1 if rows else 0, row)], marks, self._links)]


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

def reconcile(prepared: Path, store: PoStore, cases_root: Path, now: datetime, po_number: str | None = None) -> int:
    """Bring the list in line with the PO as it stands (see the module docstring), for every PO or for the one given. First every
    open change of a PO line is compared with it, and each change that it shows, when it was filed after the change was asked, is
    marked as seen at now, the time of marking (the runner gives its clock's time at the call, not the time its pass began), by
    "version <n>" or "export"; then each tick that it contradicts is ended (contradict). Returns the number
    of changes marked as seen. A second pass over the same documents marks nothing more: a change marked is no longer open, and a
    tick ended is no longer a tick."""
    seen = 0
    for change in open_changes(prepared):
        if po_number is not None and change.po_number != po_number:
            continue
        look = _look(change, store, cases_root)
        if look is not None and _shows(change.kind, look.shown, change.new_value):
            if mark(prepared, change.ids, "seen", now, by=look.by) > 0:
                seen += 1
    contradict(prepared, store, cases_root, now, po_number)
    return seen


def contradict(prepared: Path, store: PoStore, cases_root: Path, now: datetime, po_number: str | None = None) -> int:
    """End each tick that a newer print contradicts (see the module docstring), for every PO or for the one given: at each place of
    a PO line, the last change that is entered or seen, when the PO as it stands was filed after its mark and shows another value
    for its line than the change's. Its marks "entered" and "seen" are taken away with the mark "reopened", at now, by "version <n>"
    or "export", so that the change is judged as if it had not been ticked: open again, with the note of what Exact shows now when
    that is a third value. Returns the number of changes whose ticks were ended. The change of a try-out is left as it is (its tick
    never counts in a supplier's case), and so is a change whose line the look does not have, or shows per another price basis."""
    prepared = Path(prepared)
    with _LOCK:                                  # the states are read and the lines written in one go, as in mark()
        rows, _ = _rows(prepared / PREPARED_FILE, LINE_KINDS, po_number)
        marks, links, _ = _marks(prepared / MARKS_FILE)
        _, found = _contradicted(rows, marks, links, store, cases_root)
        lines = [json.dumps({"entry_id": entry_id, "mark": "reopened", "at": now.isoformat(), "by": standing.by, "shown": standing.shown},
                            ensure_ascii=False) for entry_ids, standing in found for entry_id in entry_ids]
        if lines:
            _append(prepared / MARKS_FILE, lines)
        return len(found)


def _contradicted(rows: list[_Row], marks: dict[str, _Mark], links: dict[str, str], store: PoStore,
                  cases_root: Path) -> "tuple[dict[str, _Mark], list[tuple[list[str], _Look]]]":
    """The judgement of contradict(), without writing: the marks left once every tick that the PO as it stands contradicts is
    ended, and for each change ended the entry ids whose marks "entered" or "seen" go, with the look that ends them. At each
    place of a PO line the last entered or seen change is judged, and again once it is ended, since an older tick of the place is
    then the last. A tick stands when the look was not given after its mark, when it shows the tick's value, and when it shows
    no value that can be told for a line it does hold (a value that is missing or uncertain, a price per another basis); it is
    ended when the look shows another value, and when the tick's line cannot be found in it and no line of its article shows the
    tick's value (_Look.lost). The change of a try-out is left as it is."""
    found = []
    while True:
        holders: dict[tuple, Change] = {}        # by place: its last entered or seen change
        for change in _changes(rows, marks, links):
            if change.state in _HELD and not change.tryout:
                holders[place_of(change)] = change
        taken: list[str] = []
        for change in holders.values():
            standing = _standing(change, store, cases_root)
            if standing is None or standing.filed.timestamp() <= datetime.fromisoformat(change.marked_at).timestamp():
                continue                         # no print given after the tick
            if _shows(change.kind, standing.shown, change.new_value) or (standing.shown is None and not standing.lost):
                continue                         # it shows the value, or says nothing that can be told of a line it holds
            entry_ids = [entry_id for entry_id in change.ids if entry_id in marks and marks[entry_id].what in _HELD]
            found.append((entry_ids, standing))
            taken += entry_ids
        if not taken:
            return marks, found
        marks = {entry_id: one for entry_id, one in marks.items() if entry_id not in taken}


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
    """The PO as the aid last saw it in Exact: the print of it that the store was given last (PoStore.given_last), its latest PDF
    version or its latest look from an export, also one that came again without showing anything new; None for a PO the store
    does not know, or whose files cannot be read. The one place that says which look at a PO the list compares a change with."""
    last = _given_last(store, po_number)
    return last[0] if last is not None else None


def _given_last(store: PoStore, po_number: str) -> tuple[PoVersion, str] | None:
    """PoStore.given_last, None also when the store's files cannot be read."""
    try:
        return store.given_last(po_number)
    except (OSError, ValueError):
        return None


def item_code(change: Change, cases_root: Path) -> str | None:
    """The item code of the change's line in the version its case was compared with: step 01 of the newest case of the change that
    holds that line; None when no case does, or the line has no item code there."""
    asked = _asked_of(change, cases_root)
    return asked.code if asked is not None else None


def _code(change: Change, cases_root: Path) -> str | None:
    """The item code by which the change's line is found in a version: that of its newest row whose case knows one (Change.item),
    else item_code for a change that does not say."""
    return change.item if change.item is not None else item_code(change, cases_root)


class _Asked(NamedTuple):
    """The line a row names, as step 01 of its case holds it in the version the case was compared with."""
    code: str | None                             # its item code there
    several: bool                                # whether more than one line of that version holds that item code
    basis: str | None                            # the price basis it states there
    values: tuple = (None, None, None)           # its quantity, its delivery date and its unit price there, as _told gives them
    siblings: tuple = ()                         # those three of every line of that version that holds its item code, itself too
    print_id: str | None = None                  # the sha256 of the file that version was read from: the same print, or another


_PARTS = dict(zip(LINE_KINDS, (2, 1, 0)))        # the place in _Asked.values of the value that each kind of change changes


def _told(quantity: str | None, date: str | None, price: str | None) -> tuple:
    """A line's quantity, delivery date and unit price as two prints are compared by them: a number as a number, a date as its
    text, a missing value as None."""
    def number(value: str | None) -> str | None:
        found = _number(value)
        return format(found.normalize(), "f") if found is not None else value
    return (number(quantity), date, number(price))


def _apart(kind: str, values: tuple) -> tuple:
    """The values of _told that a change of this kind leaves as they are: all but the one it changes."""
    return tuple(value for place, value in enumerate(values) if place != _PARTS.get(kind))


def _agrees(kind: str, asked: _Asked, line: DocLine) -> bool:
    """Whether a line of another print agrees with the line a change was asked for in its quantity, its delivery date and its unit
    price, the changed field apart."""
    return _apart(kind, asked.values) == _apart(kind, _told(line.quantity.value, line.delivery_date.value, line.unit_price.value))


def _asked(cases_root: Path, case_id: str, version: int | None, line_id: str | None) -> _Asked | None:
    """What step 01 of the case says of that line of that version; None when the case has no step 01 that can be read (a case that
    is gone, a price list), no version of that number, or no such line. The step is read once for each state of its file."""
    if not case_id or case_id in (".", "..") or PureWindowsPath(case_id).name != case_id:
        return None                              # no name of a case folder
    try:
        path = Path(cases_root) / case_id / "steps" / STEP_01
        stat = path.stat()
        return _step_01(str(path), stat.st_mtime_ns, stat.st_size).get((version, line_id))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):       # also no cases root given
        return None


@lru_cache(maxsize=4096)
def _step_01(path: str, changed: int, size: int) -> dict[tuple[int | None, str | None], _Asked]:
    """Every line of every version of a case's step 01, by (version, line id); `changed` and `size` are the file's, so that a step
    written again is read again. A file that cannot be read raises, and is tried again the next time."""
    found: dict[tuple[int | None, str | None], _Asked] = {}
    for version in json.loads(Path(path).read_bytes().decode("utf-8"))["versions"]:
        lines = version["document"]["lines"]
        codes = [line["item_code"]["value"] for line in lines]
        told = [_told(line["quantity"]["value"], line["delivery_date"]["value"], line["unit_price"]["value"]) for line in lines]
        for line, code, values in zip(lines, codes, told):
            siblings = tuple(other for other, held in zip(told, codes) if code is not None and held == code)
            found.setdefault((version["version"], line["line_id"]),
                             _Asked(code, len(siblings) > 1, line["price_basis"]["value"], values, siblings, version.get("sha256")))
    return found


def _asked_of(change: Change, cases_root: Path) -> _Asked | None:
    """What step 01 says of the change's line (its newest row's version and line id): of the newest case of the change that holds
    that line; None when no case does."""
    for case_id in reversed(change.case_ids):
        asked = _asked(cases_root, case_id, change.version, change.line_id)
        if asked is not None:
            return asked
    return None


class _Look(NamedTuple):
    """The PO as it stands, as a change is compared with it: the value it shows for the change, what a mark calls it, and when the
    store was last given it."""
    version: PoVersion
    shown: str | None                            # None for a line it does not have, a value that is missing or uncertain, and a
                                                 # price that the line states per another basis than the change's
    by: str                                      # "version <n>" for a PDF version, "export" for a look from an export
    filed: datetime                              # when the store was last given a print that shows the PO so (PoStore.given_at)
    lost: bool = False                           # the change's line cannot be found in it, and no line of its article shows the
                                                 # change's value: a print that cannot be matched (see _lost)


def _look(change: Change, store: PoStore, cases_root: Path) -> _Look | None:
    """The PO as it stands with the value it shows for the change and what a mark calls it, when it was filed after the newest row of
    the change was prepared (see the module docstring); None otherwise, for an article price, when the store cannot say when the
    look was filed, and when it shows no value for the change."""
    standing = _standing(change, store, cases_root)
    if standing is None or standing.shown is None or standing.filed.timestamp() <= datetime.fromisoformat(change.asked_at).timestamp():
        return None                              # it came before the change was asked: it cannot show the change made
    return standing


def _standing(change: Change, store: PoStore, cases_root: Path) -> _Look | None:
    """The print of the PO that the store was given last (newest_version) with the value it shows for the change of a PO line,
    whenever it was given; None for an article price, a PO the store does not know, and when the store cannot say when it was
    given that print."""
    if change.kind not in LINE_KINDS:
        return None
    last = _given_last(store, change.po_number)
    standing = _filed(store, change.po_number, last[1]) if last is not None else None
    if standing is None:
        return None
    version = last[0]
    line = _line_of(change, version, cases_root)
    told = line is not None and (change.kind != "po_line_price" or _same_basis(change, line, cases_root))
    return _Look(version, _value_of(change, line) if told else None, *standing, lost=line is None and _lost(change, version, cases_root))


def _lost(change: Change, version: PoVersion, cases_root: Path) -> bool:
    """Whether a change whose line the version does not give (_line_of) is lost in it: its item code is known, and no line of that
    article in the version shows the change's new value. So a print in which the article stands on no line, or on several of
    which none agrees with the line the change was asked for and none shows its value, cannot be matched; one in which a line of
    the article shows the value does not contradict the change. A change whose item code no case says is never lost: nothing
    tells which line it was."""
    code = _code(change, cases_root)
    if code is None:
        return False
    holding = [line for line in version.document.lines if line.item_code.value == code]
    return not any(_shows(change.kind, _value_of(change, line), change.new_value)
                   and (change.kind != "po_line_price" or _same_basis(change, line, cases_root)) for line in holding)


def _filed(store: PoStore, po_number: str, name: str) -> tuple[str, datetime] | None:
    """What a mark calls the print that the store was given last, by the name of its folder (PoStore.given_last: "v<n>" for a PDF
    version, "e<n>" for a look from an export), and when the store was last given it (PoStore.given_at: when it was filed, or
    later, when the same PDF or an export with the same rows came again). None when the store cannot say."""
    try:
        return (f"version {name[1:]}" if name.startswith("v") else EXPORT), store.given_at(po_number, name)
    except (OSError, ValueError):
        return None


def _line_of(change: Change, version: PoVersion, cases_root: Path) -> DocLine | None:
    """The line of the version that the change is about (see the module docstring): by its line id when the item codes agree or one
    is unknown, else by the item code when exactly one line holds it; None when no line is found. When the change's item code
    stands on several lines, of this version or of the one the change was asked in, neither the code nor a line id tells one of
    them from another across prints (a print may number its lines by their place): the change's line is then the line it was asked
    for in the very print its case was compared with, and in any other print the one line of that article that agrees with the
    line it was asked for in its quantity, its delivery date and its unit price, the changed field apart; None when no line, or
    more than one, agrees so."""
    code = _code(change, cases_root)
    holding = [line for line in version.document.lines if code is not None and line.item_code.value == code]
    asked = _asked_of(change, cases_root) if holding else None
    if len(holding) > 1 or (asked is not None and asked.several):
        if asked is None:
            return None                              # no case says which line it was asked for
        if asked.print_id is not None and asked.print_id == version.sha256:
            return next((line for line in holding if line.line_id == change.line_id), None)       # the print it was asked in
        agreeing = [line for line in holding if _agrees(change.kind, asked, line)]
        return agreeing[0] if len(agreeing) == 1 else None
    numbered = next((line for line in version.document.lines if line.line_id == change.line_id), None)
    if numbered is not None and (code is None or numbered.item_code.value is None or numbered.item_code.value == code):
        return numbered
    return holding[0] if holding else None


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


# --- the values the marks say Exact holds, laid over a version of the PO ---

class Entered(NamedTuple):
    """The value that the marks say Exact holds at one line of a PO (entered_values), with the change whose mark says so."""
    value: str | None                            # the new value of that change, in normal form; None for a value the aid does not know
    at: datetime                                 # the time of the mark that gives the change its state (Change.marked_at)
    by: str | None                               # who made that mark (Change.marked_by): "buyer", "version <n>" or "export"
    change: Change                               # the change: the last entered or seen one of its place
    mark: int                                    # the line of that mark in marks.jsonl: of two changes, the later line was marked last


def entered_values(prepared: Path, po_number: str, tryout: bool = False, store: PoStore | None = None) -> list[Entered]:
    """What the marks of the folder say Exact holds for the lines of the PO (see the module docstring): for each place of a PO line
    its last change that is entered or seen, as changes() judges them from the two files, with its new value, the time of the mark,
    who made it and the change itself, in the order in which they were marked. A place without such a change is not in it. A place
    is a line of the order, so two changes that carry one line id under two numberings and name two lines are both in it. The
    changes that cases made from a try-out asked count only with tryout, for a case made from a try-out. An OSError of either file
    is raised. Only the rows of the PO's lines are judged: a place of a PO line holds rows of that PO alone, so its changes are
    those that changes() gives, and the cases of the other orders on the list are not read for a case's run. With a store, a tick
    that the PO as it stands contradicts counts as ended, judged in reading only by contradict()'s own rule: no mark is written."""
    rows, _ = _rows(Path(prepared) / PREPARED_FILE, LINE_KINDS, po_number)
    marks, links, _ = _marks(Path(prepared) / MARKS_FILE)
    if store is not None:                        # in reading only: the ticks that the PO as it stands contradicts count as ended
        marks, _ = _contradicted(rows, marks, links, store, Path(prepared).parent)
    last: dict[tuple, Entered] = {}              # by place: its last entered or seen change
    for change in _changes(rows, marks, links):
        if change.kind in LINE_KINDS and change.po_number == po_number and change.state in _HELD and (tryout or not change.tryout):
            last[place_of(change)] = Entered(change.new_value, datetime.fromisoformat(change.marked_at), change.marked_by, change,
                                             max(marks[entry_id].line for entry_id in change.marked_ids))
    return sorted(last.values(), key=lambda one: one.mark)


def as_entered(po: PoVersion, prepared: Path, filed: datetime, limit: Decimal | None, tryout: bool = False) -> tuple[PoVersion, list[dict]]:
    """The version of a PO as Exact holds it by the marks of the folder (see the module docstring): each value of entered_values
    that was marked after the version was filed, at `filed`, put on its line of the version, and what was replaced, in the order of
    the lines: {"line_id": the line in the version, "field": "price", "delivery_date" or "quantity", "was": the value the line
    showed (in the normal form of shown_now; None for a missing or uncertain one), "entered", "marked_at", "by"}. The version itself,
    and no record, when nothing was replaced. With tryout the version is the one a case made from a try-out is compared with. The
    total is worked out again, a line that prints no price basis counted per one where the version's own total is known (it was
    counted so, or every line prints its basis), and whether it is above `limit`, the authorization limit; None for an unknown
    total or no limit. Neither the version given nor the store is changed. An OSError of the list's two files is raised."""
    cases_root, lines = Path(prepared).parent, list(po.document.lines)
    replaced = []
    for (index, kind), entered in sorted(_held_on(po, prepared, tryout).items(), key=lambda item: (item[0][0], LINE_KINDS.index(item[0][1]))):
        shown = _value_of(entered.change, lines[index])
        if (entered.at.timestamp() > filed.timestamp() and _stands(kind, entered.value) and not _shows(kind, shown, entered.value)
                and (kind != "po_line_price" or _same_basis(entered.change, lines[index], cases_root))):
            replaced.append({"line_id": lines[index].line_id, "field": FIELDS[kind], "was": shown, "entered": entered.value,
                             "marked_at": entered.change.marked_at, "by": entered.by})
            lines[index] = _with(lines[index], kind, entered.value)
    if not replaced:
        return po, []
    document = po.document.model_copy(update={"lines": lines})
    total = po_total(document, "1" if po.total is not None else None)
    above = None if total is None or limit is None else total > limit
    return po.model_copy(update={"document": document, "total": total, "above_authorization": above}), replaced


def _held_on(po: PoVersion, prepared: Path, tryout: bool, store: PoStore | None = None) -> dict[tuple[int, str], Entered]:
    """What the marks say Exact holds on the lines of this version: (the place of a line in the version, the kind) -> the value.
    Each value of entered_values is first given its line of the version (_line_of); of those that name one line, the one marked
    last decides. A value whose line the version does not have, or cannot be told from another line of it, is left out."""
    cases_root, lines = Path(prepared).parent, po.document.lines
    chosen: dict[tuple[int, str], Entered] = {}
    for entered in entered_values(prepared, po.po_number, tryout, store):       # in the order of their marks: the later one stays
        line = _line_of(entered.change, po, cases_root)
        if line is not None:
            chosen[(next(index for index, one in enumerate(lines) if one is line), entered.change.kind)] = entered
    return chosen


class Held(NamedTuple):
    """What the marks say Exact holds at one line and field of a version, as the list is now (held_now)."""
    value: str | None                            # the value, in normal form
    put_back: bool                               # the change that says so is a put-back: the value from before a change is back
    waiting: bool                                # a put-back of that change is open on the list: the buyer asked for the value before
    differs: bool = False                        # laid over the version, the value would take the place of another on its line


def held_now(po: PoVersion, prepared: Path, tryout: bool = False, store: PoStore | None = None) -> dict[tuple[str, str], Held]:
    """What the marks of the folder say Exact holds on the lines of the version, whenever the version was filed: (line id, field) ->
    the value, whether a put-back gave it, whether a put-back of it waits on the list, and whether it is
    another value than the version's line shows, by as_entered()'s own rule (a value that can stand on the line, that the line
    does not show, and for a price a line that states the change's basis). For a reader that judges again, by the list as it is
    now, a case that was compared before (the pre-check, aid/capture_server.py). With a store, a tick that the PO as it stands,
    given after it, contradicts counts as ended, by contradict()'s own rule and in reading only: no mark is written. An OSError of
    the list's two files is raised."""
    cases_root, lines = Path(prepared).parent, po.document.lines
    open_ = [change for change in open_changes(prepared) if change.puts_back is not None]

    def differs(index: int, kind: str, entered: Entered) -> bool:
        return (_stands(kind, entered.value) and not _shows(kind, _value_of(entered.change, lines[index]), entered.value)
                and (kind != "po_line_price" or _same_basis(entered.change, lines[index], cases_root)))
    return {(lines[index].line_id, FIELDS[kind]):
            Held(entered.value, entered.change.puts_back is not None or entered.change.rule_id == UNDO_RULE_ID,
                 any(put.puts_back in entered.change.ids for put in open_), differs(index, kind, entered))
            for (index, kind), entered in _held_on(po, prepared, tryout, store).items()}


def withdraw(po: PoVersion, prepared: Path, confirmed: Iterable[tuple[str, str]], now: datetime, by: str, tryout: bool = False) -> int:
    """Take off the list the open changes that the supplier no longer asks (see the module docstring). `po` is the version a case
    was compared with, the entered values laid over it (as_entered), and `confirmed` the (line id, field) pairs of its lines whose
    value the case's document names again. Where the marks say Exact holds a value for such a line and field and the version shows
    it, every open change of that line and field gets the mark "replaced", at now, by `by`: a later document asked another value
    and the newest one returns to the value Exact holds. A put-back is the buyer's own and stays, and a case made from a try-out
    (tryout) takes off the changes of try-outs alone, any other case those of the other cases alone. Returns the number of changes
    taken off."""
    cases_root, lines, confirmed = Path(prepared).parent, po.document.lines, set(confirmed)
    held = _held_on(po, prepared, tryout)
    ids: list[list[str]] = []
    for change in open_changes(prepared):
        if (change.kind not in LINE_KINDS or change.po_number != po.po_number or change.tryout != tryout or change.puts_back is not None
                or change.rule_id == UNDO_RULE_ID):
            continue
        line = _line_of(change, po, cases_root)
        index = next((index for index, one in enumerate(lines) if one is line), None)
        entered = held.get((index, change.kind)) if index is not None else None
        if (entered is not None and (line.line_id, FIELDS[change.kind]) in confirmed
                and _shows(change.kind, _value_of(entered.change, line), entered.value)):
            ids.append(change.ids)
    return sum(1 for one in ids if mark(prepared, one, WITHDRAWN, now, by=by) > 0)


def filed_of(store: PoStore, version: PoVersion) -> datetime | None:
    """When the store was last given the version that a case is compared with (PoStore.given_at: when it was filed, or later,
    when a print that shows the PO so came again): a PDF version of its PO by its own number, "v<n>", or the PO's newest look
    from an export, "e<n>" the n-th, the one look a case is ever compared with. None for a version that the store does not hold
    as it is given here, and when the store cannot say."""
    try:
        if any(version == stored for stored in store.versions(version.po_number)):
            return store.given_at(version.po_number, f"v{version.version}")
        looks = store.snapshots(version.po_number)
        if looks and version == looks[-1]:
            return store.given_at(version.po_number, f"e{len(looks)}")
    except (OSError, ValueError):
        pass
    return None


def _stands(kind: str, value: str | None) -> bool:
    """Whether a value can stand on a line as a value of its kind: a delivery date, or a number, in normal form."""
    if kind == "po_line_date":
        try:
            return value is not None and bool(date_span(value))
        except ValueError:
            return False
    return _number(value) is not None


def _same_basis(change: Change, line: DocLine, cases_root: Path) -> bool:
    """Whether the line states the price basis that the change's price is a price per: that of the change's line in the version
    its case was compared with (step 01 of its case), a line without a basis counting per one; True when no case says."""
    asked = _asked_of(change, cases_root)
    return asked is None or _number(asked.basis or "1") == _number(line.price_basis.value or "1")


def _with(line: DocLine, kind: str, value: str) -> DocLine:
    """The line with the value in the place of its own: a price as its net price when the line states a net price or a discount,
    else as its unit price; a delivery date; a quantity. The value has no text and no source and is not uncertain."""
    if kind == "po_line_price":
        name = "net_price" if line.net_price.value is not None or line.discount_pct.value is not None else "unit_price"
    else:
        name = "delivery_date" if kind == "po_line_date" else "quantity"
    return line.model_copy(update={name: DocValue(value=value)})


# --- the change of a run ---

def _changes(rows: list[_Row], marks: dict[str, _Mark], links: dict[str, str]) -> list[Change]:
    """The changes of the rows, in the order they were first asked, each with its state by the marks and its put-back link."""
    places: dict[tuple, list[tuple[int, _Row]]] = {}
    for index, row in enumerate(rows):
        places.setdefault(_place(row), []).append((index, row))
    found = [pair for place in places.values() for pair in _of_place(place, marks, links)]
    return [change for _, change in sorted(found, key=lambda pair: pair[0])]


def _place(row: _Row) -> tuple:
    """The place of a change: the kind, the PO and the line of the order for a PO line (the row's where, see _placed; its line id
    for a row that _placed did not see), the article of an article price; and, last, whether a case made from a try-out asked it
    (the row's tryout), so that a try-out's rows are never one run with those of other cases (see the module docstring)."""
    where = (row.kind, row.po_number, row.where or ("id", row.line_id)) if row.kind in LINE_KINDS else (row.kind, row.article_code)
    return (*where, row.tryout)


def same_value(kind: str, one: str | None, other: str | None) -> bool:
    """Whether two values of a kind are the same, as the runs compare them: a price or a quantity as a number, anything else as its
    text."""
    return _same(kind, one, other)


def place_of(change: Change) -> tuple:
    """The place of a change, as _place gives it for its rows (Change.place): whether a try-out asked it, last, included. A change
    that was not made from rows has the place its own line id gives."""
    if change.place:
        return change.place
    where = (change.kind, change.po_number, ("id", change.line_id)) if change.kind in LINE_KINDS else (change.kind, change.article_code)
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
    entries = {row.entry_id for _, row in rows}
    held: list[str | None] = []                  # the value Exact holds by the marks: that of the last entered or seen run so far
    for number, run in enumerate(left):
        # a put-back of an entry that is no row of the file puts back a write made in a system: no mark says what Exact held after it
        system = any(row.entry_id in links and links[row.entry_id] not in entries for _, row in run)
        change = _change([row for _, row in run], marks, links, number == len(left) - 1, [] if system else held)
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
    prints = getattr(marks, "ended", {})         # entry id -> the print that ended its tick, and what it showed (_marks)
    state, deciding = ("undone", []) if undone else _state(run, marks, last, held)
    latest = max((marks[entry_id] for entry_id in deciding), key=lambda one: one.line) if deciding else None
    return Change(ids=[row.entry_id for row in run], kind=newest.kind, po_number=newest.po_number, version=newest.version,
                  line_id=newest.line_id, article_code=newest.article_code, old_value=newest.old_value, new_value=newest.new_value,
                  case_ids=list(dict.fromkeys(row.case_id for row in run)), rule_id=newest.rule_id, prepared_at=run[0].timestamp,
                  asked_at=newest.timestamp,
                  state=state, marked_at=latest.at if latest else None, marked_by=latest.by if latest else None, marked_ids=deciding,
                  puts_back=next((links[row.entry_id] for row in reversed(run) if row.entry_id in links), None), tryout=newest.tryout,
                  place=_place(newest), item=next((row.asked.code for row in reversed(run) if row.asked is not None and row.asked.code), None),
                  ended=next((prints[row.entry_id] for row in reversed(run) if row.entry_id in prints), None) if state == "open" else None,
                  withdrawn=state == "replaced" and newest.entry_id in marks and marks[newest.entry_id].what == WITHDRAWN)


def _state(run: list[_Row], marks: dict[str, _Mark], last: bool, held: list[str | None]) -> tuple[str, list[str]]:
    """The state of a run that no undo cancelled, and the entry ids of its rows whose marks give it (see the module docstring)."""
    for what in _DONE:
        deciding = [row.entry_id for row in run if row.entry_id in marks and marks[row.entry_id].what == what]
        if deciding:
            return what, deciding
    if not last or (run[-1].entry_id in marks and marks[run[-1].entry_id].what == WITHDRAWN):
        return "replaced", []                    # a later run of its place, or its supplier's newest document no longer asks it
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


def _rows(path: Path, kinds: tuple[str, ...] = KINDS, po_number: str | None = None) -> tuple[list[_Row], int]:
    """The rows of prepared_changes.csv of the kinds (by default those of a change to enter), in the order of the file, and the
    number of lines passed over. The first line is the header; without the writer's columns there no line can be read. A line that
    repeats the header is no row. A row whose case, under the folder's cases_root, is made from a try-out has tryout set (_made_up).
    With po_number only the rows of that PO are given, and only their cases are looked at."""
    lines = _lines(path)
    if not lines:
        return [], 0
    header = _fields(lines[0])
    if header:
        header[0] = header[0].removeprefix("﻿")
    if not header or not set(_NEEDED) <= set(header):
        raise ListUnread(f"{Path(path).name}: the first line lacks the columns of the prepared writer")
    rows, passed = [], 0
    for line in lines[1:]:
        fields = _fields(line)
        if fields == header:
            continue
        row = _row(dict(zip(header, fields))) if fields is not None and len(fields) == len(header) else None
        if row is None:
            passed += 1
        elif row.kind in kinds and po_number in (None, row.po_number):
            rows.append(row)
    cases_root = Path(path).parent.parent                              # the cases of the rows, under the folder's cases_root
    made_up = _made_up(cases_root, rows)
    return _placed(cases_root, [row._replace(tryout=True) if row.case_id in made_up else row for row in rows]), passed


def _placed(cases_root: Path, rows: list[_Row]) -> list[_Row]:
    """The rows, each row of a PO line with the line of the order it names (where) and what step 01 of its case says of that line
    (asked). Rows name one line when they carry one line id and their item codes agree, or, whatever their line ids, when they
    carry an item code that one line holds: ("code", the item code). An item code that several lines of the version hold tells no
    line from another, and neither does a line id across prints, so such a row names its line by what tells it from the other
    lines of its article where it was asked: ("several", the item code, its quantity, delivery date and unit price there, the
    changed field apart), or, when not even those tell it, ("several", the item code, the sha256 of its print, the line id)
    (_line_key). A row whose case says no item code (a case that is gone, a line without one) names the line of its line id,
    ("id", the line id), and is taken with the rows of its own order under that line id whose item code is known when those name
    one line."""
    asked = [_asked(cases_root, row.case_id, row.version, row.line_id) if row.kind in LINE_KINDS else None for row in rows]
    keys = [_line_key(row, one) if row.kind in LINE_KINDS else () for row, one in zip(rows, asked)]
    known: dict[tuple, set[tuple]] = {}          # (PO, line id) -> the lines that its rows with an item code name
    for row, one, key in zip(rows, asked, keys):
        if one is not None and one.code is not None:
            known.setdefault((row.po_number, row.line_id), set()).add(key)
    placed = []
    for row, one, key in zip(rows, asked, keys):
        if row.kind in LINE_KINDS and (one is None or one.code is None):
            named = known.get((row.po_number, row.line_id), ())
            key = next(iter(named)) if len(named) == 1 else key
        placed.append(row._replace(where=key, asked=one))
    return placed


def _line_key(row: _Row, asked: _Asked | None) -> tuple:
    """The line of the order that a row of a PO line names, by what step 01 of its case says of it (see _placed)."""
    if asked is None or asked.code is None:
        return ("id", row.line_id)
    if not asked.several:
        return ("code", asked.code)
    apart = _apart(row.kind, asked.values)           # what tells this line from the others of its article, the changed field apart
    if sum(1 for other in asked.siblings if _apart(row.kind, other) == apart) == 1:
        return ("several", asked.code, apart)
    return ("several", asked.code, asked.print_id, row.line_id)      # not even that tells it: the line of its own print alone


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


class _Marks(dict):
    """The last mark of each entry (_marks), and beside it `ended`: for an entry whose last mark is a "reopened" that a print
    wrote (contradict: the mark holds "shown"), what the mark calls that print and the value it showed."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ended: dict[str, tuple[str, str | None]] = {}


def _marks(path: Path) -> tuple[dict[str, _Mark], dict[str, str], int]:
    """The last mark of each entry of marks.jsonl, an entry whose last mark is "reopened" having none; the entry each put-back row
    puts back, by the mark puts_back (row -> entry, the later line winning); and the number of lines passed over."""
    found = _Marks()
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
            found.ended.pop(entry_id, None)
            if "shown" in record:                # a print ended the tick (contradict alone writes what the print showed, or null
                shown = record.get("shown")      # for a line it did not have); any other take-back says nothing of a print
                found.ended[entry_id] = (by, shown if isinstance(shown, str) else None)
        else:
            found[entry_id] = _Mark(what, at, by, number)
            found.ended.pop(entry_id, None)
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
