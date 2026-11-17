"""The one plug point of a route into Exact, for the button "Aangevinkte regels in Exact zetten" of the review page's tab "In Exact
invoeren" (the design of 17 November, docs/superpowers/specs/2026-11-17-click-to-exact-design.md). No route exists yet, so in this
version route_for gives none and the page shows no button.

The plug point. route_for(settings) gives the route the review page is built with (aid/review/__main__.py gives it to build_app as
its route when there is one), or None. In this version it returns None and builds and reads nothing, not even a value of the settings it is given. It is the one
place where a connector is built once IT names the route into Exact: a connector is written for that route, and route_for builds
it from the settings. The runner, the batch and the review page keep the PreparedWriter as the writer of their Deps: a route is
never the Deps' writer, so the rules and the buyer's decisions still prepare every change for the list, and nothing reaches Exact
but a change that the buyer ticked and put into Exact with the button. The route is no Writer of aid/execution/writer.py and
nothing puts it in front of the prepared writer (the plug point that aid/execution/exact_api.py describes): it writes only from
the list, on the buyer's press.

A route (Route) has three parts. Its target is "test" or "live" (TARGETS), the administration of Exact it writes to: during the BEP
the page writes only through a route whose target is "test", and only while the settings are in the test mode. available() says
whether the route can be used now: the page counts a route as available only when available() returns True itself. write(change,
now) takes one open change of the list (by_hand.Change: the price, the delivery date or the quantity of an order line, or an
article's purchase price, with its one new value) and returns an Outcome. The page judges the target, the test mode, the settings
and available() before it reads a row and again as the last step before it logs the attempt; in that last check it asks available()
first, then judges the settings, reading the settings file itself as well, and reads the target once, after them, and names that
value in both lines of the route's log.

An outcome (Outcome) has a kind, a reason and the value read before. The kind is one of KINDS: verified (written and read back),
not_made (refused or not sent: nothing is in Exact), not_verified (accepted, but the value read back differs), not_known (no clear
answer) or handed_over (a route fed by a file took the change and has not applied it yet). The reason is a fixed key of the
connector's own, in lowercase letters, digits and underscores ("read_back", "refused"), never the text of an error, which may hold
an address, a path or a value of the company; the page never shows it, and the route's log keeps it. `before` is the value read
before the write, or None.

What a connector must do, and must never do:
- It reads the value before it writes, writes the new value, and reads it back: verified only when the value read back is the new
  value. A route that cannot read (a file for an Orbis task) can only hand a change over: handed_over, never verified.
- It never writes a value it did not mean to write: the new value of the change it is given, at the place the change names, and
  nothing else. It writes nothing on a refusal of its own and says not_made, and it says not_made only when nothing can be in
  Exact. When it cannot tell, it says not_known.
- write returns soon: the page holds its lock of the row from its checks to its mark, the call included, so that a slow write
  makes a press of the button in another browser tab wait (the lock keeps two presses from writing one row twice).
- write never raises: whatever goes wrong is an outcome with a fixed reason. The page takes a write that raises, or that gives no
  Outcome or one that does not validate again (built past its checks), as not_known all the same. The page gives write a copy of
  the change, and logs and marks its own: a connector that changes what it was given moves no mark.
- It takes its credential from the company's secret store under a name of its own: never from the settings file or the source, and
  never under the name of the token of the earlier route, which no command reads (section 3.10 of aid/MANUAL.md names it). Its
  target names the administration it reaches, as the company confirms it in a dated record.
- A route's target is fixed for the route's life: set when route_for builds it, and never read again from a file or a setting.
  write() never reaches another administration than that target.
- Two of the four points that exact_api.py leaves open apply to a press of the button, and a connector must settle them:
  A doubt that outlives its operation: a connector that remembers a write in doubt beyond the call that made it must not report a
  later call that sent nothing as made, and must not pass that doubt to another change. A read that lags behind the write: a read
  back that still shows the value from before is not_known, never not_made, unless the connector knows that Exact did not take
  the change. The other two (the article price that waits behind a look, a close's own write that asks for a look) arise only
  where a route is the Deps' writer, which this plug point never is.

The route's log. The page appends one line to <cases_root>/prepared/exact_writes.jsonl (LOG) before it calls the route (the attempt,
log_attempt: when that line cannot be written, nothing is sent) and one after the call (the outcome, log_outcome). Each line is a JSON
object that names the time, the entry ids of the change, its kind, the order, its version, the line, the article, the old and the
new value, the route's target, and the outcome's kind, its reason and the value read before (none for an attempt). The log is
appended and never rewritten, and read leniently: a line that cannot be read is passed over (records). A change whose last line is
a write in doubt (not_verified, not_known or handed_over, or an attempt with no outcome after it: the page stopped during the call,
or the outcome could not be written) is not written again by a later press until the buyer ticks it as entered or a print of the
order shows its value (a mark "entered" or "seen" of one of its entries after that line, in marks.jsonl): doubts and doubt_of. A
last line whose time the clock of this PC cannot place in its zone (a time without a zone in the year 1 or 9999, or on Windows one
before 1970) keeps the change held as well. The marks are read with the list's own reader of marks.jsonl (by_hand.mark_lines), so
that a line the list passes over settles nothing here either. The marks and the lines are compared by their times, which the clocks of the page and of the runner give, both on the one PC: a clock set
back between the two could keep a doubt too long or lift it too early. A verified write is marked "entered" by WRITTEN ("exact") through by_hand.mark, which the list reads as any tick, and only once its
outcome line is in the log: a write whose outcome line cannot be appended, whatever the route answered, is not marked, its attempt
stands for a write in doubt and the press counts it as not sure, so that the log keeps the value read before for every change the aid
marked. A change whose last line is a verified write
that no mark "entered" or "seen" settled, made at the line's time or after it (the press's own mark carries the time of its outcome
line), is in Exact with its row not ticked off (UNTICKED: the file of the marks was held at the mark): a later press does not write
it again, and its row says to tick it off.

A put-back. The button never writes a put-back: the buyer enters every one, as without a route. A change is a put-back by its rows
as prepared_changes.csv reads at the press (rules_of, is_put_back), not by the rule id of its newest row, since an undo row can join
neighbours of the same value: a change that holds an undo row (the rule id "undo"; an entry id that stands on more than one line
counts as one), whether the mark puts_back links it to the entry
it puts back (aid/execution/by_hand.py) or not (MANUAL 3.10's recipe, a link that could not be written), and, since its row could be
one, a change with an entry whose row that read does not give. doubt_of keeps every such change off, before the attempt is logged.
Its row says that the buyer enters it (BY_HAND). While the records still speak for the change it puts back (route_read: that change
is still marked entered by WRITTEN by the list's own reading, so that no print, tick or drop came after the aid's tick, and the last
line of the log that names its entry is a verified write), and the route read there before its write another value than the
put-back's, an empty field or nothing, the row names that (OTHER_BEFORE, with what it read), and so does the note of "Terugdraaien in
Exact". The put-back row itself names the row's old value, as for a change the buyer entered.

A value the button sends. Besides the rule of the copy buttons (hand_list.one_value: one number or one day), the button sends a new
value only in the normal form the aid's own writers give it (sendable): a plain decimal with a point for a price, a quantity or an
article price, a day that exists as yyyy-mm-dd for a delivery date. A value that a list edited by hand or damaged holds otherwise is
passed over.
"""
import json
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Literal, NamedTuple, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, field_validator

from aid.execution.by_hand import KINDS as LIST_KINDS     # the kinds of a change to enter: KINDS here are the outcome's
from aid.execution.by_hand import MARKS_FILE, Change, changes, mark_lines, prepared_rows, same_value
from aid.execution.writer import UNDO_RULE_ID
from aid.messaging import _append_line           # a line appended in one write, after a line a crash cut off
from aid.settings import Settings

LOG = "exact_writes.jsonl"                       # under <cases_root>/prepared: the attempts and the outcomes of the route
TARGETS = ("test", "live")                       # the administrations of Exact a route can write to
KINDS = ("verified", "not_made", "not_verified", "not_known", "handed_over")
DOUBTS = ("not_verified", "not_known", "handed_over")     # the kinds that leave the change in doubt: no mark, no second write
WRITTEN = "exact"                                # the `by` of the mark "entered" of a change the aid put into Exact through a route
UNTICKED = "unticked"                            # a verified write that no mark settled: in Exact, and its row not ticked off
BY_HAND = "put_back_by_hand"                     # a put-back, which the button never writes: the buyer enters it
OTHER_BEFORE = "put_back_other"                  # such a put-back whose row names what the route read in Exact before the aid's write
_SETTLED = ("entered", "seen")                   # the marks that settle a write in doubt: the buyer's tick, a print that showed it
_REASON = re.compile(r"[a-z][a-z0-9_]{0,39}")    # a fixed key: never the text of an error
_NUMBERS = ("po_line_price", "po_line_quantity", "article_price")      # the kinds whose values are numbers
_PLAIN = re.compile(r"[0-9]+(\.[0-9]+)?")       # a number as the aid's own writers give it: no sign, no exponent, no space, no comma
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class Outcome(BaseModel):
    """What a route says of one write (see the module docstring)."""
    model_config = ConfigDict(frozen=True)

    kind: Literal["verified", "not_made", "not_verified", "not_known", "handed_over"]
    reason: str                                  # a fixed key of the connector, never the text of an error
    before: str | None = None                    # the value read before the write

    @field_validator("reason")
    @classmethod
    def _fixed(cls, reason: str) -> str:
        if not _REASON.fullmatch(reason):
            raise ValueError("a reason is a fixed key of lowercase letters, digits and underscores, never the text of an error")
        return reason


class Held(NamedTuple):
    """Why the button keeps an open change off the route (doubt_of): the kind, one of DOUBTS for a write in doubt, UNTICKED for a
    verified write whose row was not ticked off, BY_HAND or OTHER_BEFORE for a put-back, which the button never writes; for
    OTHER_BEFORE what the route read in Exact before its write of the change put back ("" for an empty field, None when nothing was
    read); and for UNTICKED the time of that verified line, with its zone (None when the clock of this PC cannot place it), by which
    the row tells whether a print of the order came after the write (aid/review/hand_list.py)."""
    kind: str
    before: str | None = None
    at: datetime | None = None


class Read(NamedTuple):
    """What the route read in Exact before the aid's write of the change that a put-back puts back (route_read): the value, "" for
    an empty field, None when nothing was read."""
    before: str | None


@runtime_checkable
class Route(Protocol):
    """A route into Exact for the button of the tab "In Exact invoeren" (see the module docstring)."""
    target: str                                  # "test" or "live": the administration it writes to

    def available(self) -> bool:
        ...

    def write(self, change: Change, now: datetime) -> Outcome:
        ...


def route_for(settings: Settings) -> Route | None:
    """The route into Exact for these settings: None in this version, which builds and reads nothing (see the module docstring)."""
    return None


def log_attempt(prepared: Path, change: Change, target: str, now: datetime) -> None:
    """Append the attempt to write the change to the route's log, before the route is called; an OSError when it cannot be written,
    and then nothing may be sent."""
    _append_line(Path(prepared) / LOG, _line(change, target, now, None))


def log_outcome(prepared: Path, change: Change, target: str, outcome: Outcome, now: datetime) -> None:
    """Append the outcome of the write to the route's log, after the call; an OSError when it cannot be written, and then the attempt
    before it stands for a write in doubt."""
    _append_line(Path(prepared) / LOG, _line(change, target, now, outcome))


def records(prepared: Path) -> list[dict]:
    """The lines of the route's log that can be read, in the order of the file: each a JSON object with the entry ids (a list of
    texts), a time and an outcome (None for an attempt, else one of KINDS). A file that is not there holds none; one that cannot be
    opened raises its OSError."""
    try:
        data = (Path(prepared) / LOG).read_bytes()
    except FileNotFoundError:
        return []
    found = []
    for line in data.split(b"\n"):
        try:
            record = json.loads(line.decode("utf-8"))
            ids, at, kind = record.get("entry_ids"), record.get("at"), record.get("outcome")
            if not (isinstance(ids, list) and ids and all(isinstance(entry_id, str) for entry_id in ids) and isinstance(at, str)
                    and (kind is None or kind in KINDS)):
                continue
            datetime.fromisoformat(at)
        except (ValueError, AttributeError):     # a blank line, no JSON, no object, not UTF-8, no time
            continue
        found.append(record)
    return found


def doubt_of(prepared: Path, change: Change, logged: list[dict] | None = None, settling: list[tuple[str, datetime]] | None = None,
             found: list[Change] | None = None, rules: Mapping[str, str] | None = None) -> Held | None:
    """Why the button does not write the change (see the module docstring), or None when nothing keeps it off. First the last line
    of the log that names one of the change's entry ids (a second case can add an id to a change): a write in doubt (not_verified,
    not_known or handed_over; an attempt with no outcome after it is not_known) that no mark "entered" or "seen" of one of the
    change's entries made after it settled, or a verified write that no such mark made at its time or after it settled (UNTICKED:
    in Exact, its row not ticked off). Then a put-back, judged by the change's rows (is_put_back), which the button never writes
    (_put_back_held). `logged` is records(prepared), `settling` the marks that settle a doubt (_settling), `found` every change of
    the list (by_hand.changes) and `rules` the rule ids of its rows (rules_of), each read here when it is not given and needed; an
    OSError of a file read here is raised."""
    logged = records(prepared) if logged is None else logged
    ids = set(change.ids)
    last = next((record for record in reversed(logged) if ids & set(record["entry_ids"])), None)
    held = _doubt(last, ids, lambda: _settling(prepared) if settling is None else settling) if last is not None else None
    if held is not None:
        return held
    rules = rules_of(prepared) if rules is None else rules
    if not is_put_back(change, rules):
        return None
    return _put_back_held(change, rules, changes(prepared)[0] if found is None else found, logged)


def doubts(prepared: Path, found: Iterable[Change]) -> dict[str, Held]:
    """For the list of a page with a route: the open changes of `found` (every change of the list, as by_hand.changes gives them)
    that the button keeps off, as {the first entry id of the change: why} (doubt_of: a write in doubt, a verified write not ticked
    off, a put-back), the route's log, the marks and the rows of the list read once. A file among them that cannot be read gives
    none, so that the list still shows; the press itself refuses to write then."""
    found = list(found)
    try:
        logged = records(prepared)
        latest: dict[str, int] = {}              # each entry id: the place of the last line that names it
        for number, record in enumerate(logged):
            for entry_id in record["entry_ids"]:
                latest[entry_id] = number
        rules = rules_of(prepared)
        settled: list[list[tuple[str, datetime]]] = []

        def settling() -> list[tuple[str, datetime]]:        # read once, and only when a line asks for it
            if not settled:
                settled.append(_settling(prepared))
            return settled[0]
        held_by = {}
        for change in found:
            if change.state != "open":
                continue
            last = max((latest[entry_id] for entry_id in change.ids if entry_id in latest), default=None)
            held = _doubt(logged[last], set(change.ids), settling) if last is not None else None
            held_by[change.ids[0]] = held if held is not None else _put_back_held(change, rules, found, logged, latest)
    except OSError:
        return {}
    return {entry_id: held for entry_id, held in held_by.items() if held is not None}


def rules_of(prepared: Path) -> dict[str, str]:
    """The rule id of each row of the folder's prepared_changes.csv that holds a change to enter, by its entry id, as the file reads
    now (by_hand.prepared_rows, as the list reads it: a line that cannot be read gives no row). The rule id "undo" (UNDO_RULE_ID of
    aid/execution/writer.py) is that of a row that puts a change back. An entry id that stands on more than one line counts as an
    undo row, whatever rule ids its lines give (fail closed: the aid's writers give every row an entry id of its own, so only a list
    edited by hand or damaged repeats one, and one of its lines may be an undo row). A file that cannot be read raises its OSError."""
    rules: dict[str, str] = {}
    for row in prepared_rows(prepared, LIST_KINDS):
        rules[row.entry_id] = UNDO_RULE_ID if row.entry_id in rules else row.rule_id
    return rules


def is_put_back(change: Change, rules: Mapping[str, str]) -> bool:
    """Whether the change puts a change back, which the button never writes, judged by its rows and not by the rule id of its newest
    row (Change.rule_id), since an undo row can join neighbours of the same value: one of its entries is an undo row by `rules` (the
    rule ids of rules_of), linked by the mark puts_back or not; the change is linked (Change.puts_back); or one of its entries has no
    row in `rules`, its row not read now, so that it may be one."""
    return change.puts_back is not None or any(rules.get(entry_id, UNDO_RULE_ID) == UNDO_RULE_ID for entry_id in change.ids)


def route_read(put: Change, found: Iterable[Change], logged: list[dict], latest: dict[str, int] | None = None) -> Read | None:
    """What the route read in Exact before the aid put into Exact the change that the put-back `put` puts back, while the records
    still speak for it: the put-back links the entry it puts back (Change.puts_back), the change that holds that entry is still
    marked entered by WRITTEN by the list's own reading (`found`, every change of the list as by_hand.changes gives them: no later
    mark of that entry ended, replaced or overrode the aid's tick, such as a print's "reopened" or the buyer's tick after it), and
    the last line of the route's log that names that entry is a verified write (written_line). None otherwise. The note of the
    put-back's row (_put_back_held) and the note of "Terugdraaien in Exact" (aid/review/hand_list.py) both read it."""
    if put.puts_back is None:
        return None
    owner = next((one for one in found if put.puts_back in one.ids), None)
    if owner is None or owner.state != "entered" or owner.marked_by != WRITTEN:
        return None
    line = written_line(logged, put.puts_back, latest)
    if line is None:
        return None
    before = line.get("before")
    return Read(before if isinstance(before, str) else None)


def written_line(logged: list[dict], entry_id: str, latest: dict[str, int] | None = None) -> dict | None:
    """The last line of the route's log that names the entry, when its outcome is verified: the route wrote that entry's change and
    read it back, and the line's `before` is what it read in Exact before that write. None when the last line that names the entry
    is no verified write (an attempt, a write in doubt, a write not made), or no line names it. `latest` maps each entry id to the
    place of its last line, when the caller has it."""
    if latest is not None:
        last = logged[latest[entry_id]] if entry_id in latest else None
    else:
        last = next((record for record in reversed(logged) if entry_id in record["entry_ids"]), None)
    return last if last is not None and last["outcome"] == "verified" else None


def sendable(change: Change) -> bool:
    """Whether the change's new value is one the button may send: in the normal form that the aid's own writers give it
    (aid/execution/ops.py, the prepared writer): for a price, a quantity or an article price a plain decimal with a point, "12.70"
    or "60", without a sign, an exponent, a space or a comma, never NaN or Infinity; for a delivery date a day that exists, as
    yyyy-mm-dd, "2026-11-23". Stricter than hand_list.one_value, the rule of the copy buttons, which stays as it is: a value that a
    list edited by hand or damaged holds in another form is passed over."""
    new = change.new_value
    if not isinstance(new, str):
        return False
    if change.kind in _NUMBERS:
        return _PLAIN.fullmatch(new) is not None
    if change.kind != "po_line_date" or _DAY.fullmatch(new) is None:
        return False
    try:
        return date.fromisoformat(new).isoformat() == new
    except ValueError:                           # no such day: 2026-02-30
        return False


def _put_back_held(change: Change, rules: Mapping[str, str], found: Iterable[Change], logged: list[dict],
                   latest: dict[str, int] | None = None) -> Held | None:
    """For a put-back (is_put_back, by the rule ids `rules`), why the button does not write it, by the note its row carries; None
    for a change that is no put-back. The button never writes a put-back: the buyer enters it. Its row names what the route read in
    Exact before the aid's write of the change put back (OTHER_BEFORE, with that value, "" or None) when the records still speak for
    it (route_read: the change put back still marked entered by the aid, and a verified line of the route's log for its entry) and
    that is not the put-back's value: another value, an empty field, or nothing read. Any other put-back is BY_HAND, whose row says
    only that the buyer enters it: a put-back that no mark links (MANUAL 3.10's recipe, or a link that could not be written), one
    of a change the buyer entered, or whose tick a print or the buyer took over, and one whose value the route read."""
    if not is_put_back(change, rules):
        return None
    read = route_read(change, found, logged, latest)
    if read is None or (read.before and same_value(change.kind, read.before, change.new_value)):
        return Held(BY_HAND)
    return Held(OTHER_BEFORE, read.before)


def _doubt(last: dict, ids: set[str], settling) -> Held | None:
    """Why the change's last line of the log keeps it off (doubt_of), given the change's entry ids and a function that gives the
    marks that settle a doubt (_settling), asked only when the line is a write in doubt or a verified write: a write in doubt that
    no such mark made after the line settled, or a verified write that no such mark made at the line's time or after it settled.
    The mark of a press carries the time of its own outcome line when the clock gives one time, and settles that line. A line whose
    time the clock of this PC cannot place in its zone (year 1, year 9999: ValueError or OverflowError; on Windows a time without a
    zone before 1970, for which astimezone() can raise OSError) keeps the change held, as such a line in doubt or that verified write,
    since no mark can be compared with it; _settling passes such a mark over the same way."""
    kind = last["outcome"] or "not_known"
    if kind != "verified" and kind not in DOUBTS:
        return None
    try:
        since = _aware(datetime.fromisoformat(last["at"]))
    except (ValueError, OverflowError, OSError):     # a time the clock of this PC cannot place in its zone: held, not read as settled
        return Held(UNTICKED if kind == "verified" else kind)
    if kind == "verified":
        if any(entry_id in ids and at >= since for entry_id, at in settling()):
            return None                          # ticked off, by the press itself or by the buyer, or seen in a print
        return Held(UNTICKED, at=since)
    if any(entry_id in ids and at > since for entry_id, at in settling()):
        return None                              # the buyer ticked it, or a print showed its value, after that line
    return Held(kind)


def _settling(prepared: Path) -> list[tuple[str, datetime]]:
    """The marks of marks.jsonl that settle a write in doubt, whatever marks came after them: each mark "entered" (the buyer's tick)
    or "seen" (a print that showed the value), as (its entry id, its time). The lines are read by the list's own parser
    (by_hand.mark_lines), so that a line the list passes over settles nothing here either; a line whose time cannot be told with
    its zone is passed over too (year 1 or 9999, and on Windows a time without a zone before 1970, for which astimezone() can
    raise OSError). A file that is not there holds none; one that cannot be opened raises its OSError."""
    found = []
    for line in mark_lines(Path(prepared) / MARKS_FILE)[0]:
        if line.what not in _SETTLED:
            continue
        try:
            found.append((line.entry_id, _aware(datetime.fromisoformat(line.at))))
        except (ValueError, OverflowError, OSError):     # a time this PC cannot place in its zone (year 1 or 9999, or on Windows before 1970)
            continue
    return found


def _line(change: Change, target: str, now: datetime, outcome: Outcome | None) -> str:
    """One line of the route's log (see the module docstring)."""
    return json.dumps({"at": now.isoformat(), "entry_ids": list(change.ids), "kind": change.kind, "po_number": change.po_number,
                       "version": change.version, "line_id": change.line_id, "article_code": change.article_code,
                       "old_value": change.old_value, "new_value": change.new_value, "target": target,
                       "outcome": outcome.kind if outcome is not None else None, "reason": outcome.reason if outcome is not None else None,
                       "before": outcome.before if outcome is not None else None}, ensure_ascii=False)


def _aware(moment: datetime) -> datetime:
    """A time with its zone; a time without one is local time, so that one with a zone and one without compare."""
    return moment if moment.utcoffset() is not None else moment.astimezone()
