"""The exports of the click-timer of the evaluation (evaluation/timer.html): reading them, and checking the log of one case.

The timer writes two files. The events export is the timer log: one row per click, correction and recovery choice, never
deleted. The timing export has one row per finished case, with the active and passive seconds the timer worked out itself. The
evaluation protocol asks (section 3, "The timing procedure works") that in every pilot case the timer log is complete: start,
pauses and stop present. This module answers that from the two files alone, and it works the seconds out again from the events,
so that a timing row that does not belong to its events is seen.

The rules are the timer's own, read from its script. The log of a case is its events over all exports given, each once, in
the order of the number in the event id (cases). An undo and the click it names do not count (effective). The seconds are
worked out as the timer's summarise() does it (seconds): by the monotonic value within one page load; across a reload by the
recovery choice, with the clock of the PC for a gap counted as work and the last heartbeat of the old page for one counted as
a pause; a negative interval counts as zero and is named. The check of one case (check) keeps two things apart. Whether the
log is complete is the protocol's criterion and nothing else. Whether it is consistent (only steps the timer's buttons allow,
the events in the order of time, a timing row that agrees with the seconds worked out again to a tenth of a second, no negative
interval) is said beside it and never changes the first. Each problem is a sentence that names its likely cause; none holds a
time, seconds or a text of the files.

It imports nothing of the aid: the timer is not part of the aid, and the same rules must hold for a file from any browser.

Reading a CSV (table, and decode and delimiter under it) follows the rules every input of the records command is read by, so
the other modules of the command import them from here. The bytes are read at once and parsed from memory. They are taken as
UTF-8, with or without a byte order mark, else as Windows-1252, which is what a spreadsheet's plain "CSV" is; bytes that are
neither are refused with "save the file as CSV UTF-8". The delimiter is the comma or the semicolon, whichever the header line
holds more of. Cells are stripped, non-breaking spaces too; a row that is empty in every cell is skipped; a short row is padded.
A Refusal is a sentence for the person at the command line: it names the file by its name only, and what to do.
"""
import csv
import io
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

EVENT_COLUMNS = ("event_id", "case_id", "condition", "session", "event", "timestamp", "mono_ms", "page", "ref", "choice",
                 "last_heartbeat_timestamp", "last_heartbeat_mono_ms", "last_heartbeat_page", "note")
TIMING_COLUMNS = ("case_id", "active_seconds", "passive_seconds", "completed", "manual_fallback", "deviations", "started")
AS_EXPORTED = "is not as the timer writes it: give the file as the timer exported it"
# The timer's own table (MODE in timer.html): the state a click leaves the case in.
MODE = {"start": "active", "resume": "active", "wait_end": "active", "pause": "paused", "wait": "waiting"}
TERMINAL = frozenset({"complete", "incomplete"})
CONDITIONS = ("manual", "assisted")
# The enable rules of the timer's buttons (render() in timer.html) and of its recovery question: the states a step is legal from.
LEGAL_FROM = {"start": ("idle",), "pause": ("active", "waiting"), "resume": ("paused",), "wait": ("active",),
              "wait_end": ("waiting",), "recovery": ("active", "waiting"), "fallback": ("active",), "complete": ("active",),
              "incomplete": ("active",)}
TOLERANCE = Decimal("0.1")                     # seconds: one unit of the timing row's decimal
RUNNING = "the events export was taken while this case was running: export both files again after the stop"
TWO_HANDLINGS = "two handlings under one id: give the exports of one study only"
NO_EVENTS = "no events for this case: give the events export that holds it"
NO_TIMING_ROW = ("no timing row for this case: the timing export was taken before its stop, or is of another study: export both "
                 "files again")
NOT_THE_SAME_MOMENT = "not exports of the same moment: export both files again"
SUPERSEDED = "superseded by a later export"
_EVENT_ID = re.compile(r"E[0-9]+")
_NUMBER = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class Refusal(ValueError):
    """An input that cannot be used, as one sentence that names the file and what to do. The command ends on it with exit code
    2, before anything is written."""


# --- reading a CSV ---

@dataclass(frozen=True)
class Table:
    """A CSV file as read: the names of its columns and its rows, each a cell per column name. `numbers` holds, for each row, the
    number it has in the file (the header is row 1), which is the number a spreadsheet shows beside it."""
    name: str
    header: tuple[str, ...]
    rows: list[dict[str, str]]
    numbers: list[int]


def read_bytes(path: str | Path) -> bytes:
    """The bytes of an input file, read at once so that no handle stays open. A file that cannot be read is a Refusal that names
    it without its folder; a file that another program holds open is said as that."""
    path = Path(path)
    try:
        return path.read_bytes()
    except PermissionError:
        raise Refusal(f"{path.name} is open in another program (Excel?): close it and run again") from None
    except FileNotFoundError:
        raise Refusal(f"{path.name} cannot be read: it does not exist") from None
    except OSError as error:
        raise Refusal(f"{path.name} cannot be read: {error.strerror or type(error).__name__}") from None


def decode(data: bytes, name: str) -> str:
    """The text of a CSV file: UTF-8 with or without a byte order mark, else Windows-1252. Text with a NUL character is no CSV
    of either (it is UTF-16, which Windows-1252 would read without complaint)."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" not in text:
            return text
    raise Refusal(f"{name}: save the file as CSV UTF-8")


def delimiter(header_line: str) -> str:
    """The semicolon when the header line holds more semicolons than commas (a spreadsheet with a Dutch list separator), else the
    comma."""
    return ";" if header_line.count(";") > header_line.count(",") else ","


def table(data: bytes, name: str, columns: Sequence[str] = (), what: str = "") -> Table:
    """Parse the bytes of a CSV file by the rules of the module docstring. `name` is the file's name for the refusals; `columns`
    are the columns the file must have, and `what` says what kind of file it should be ("an events export of the timer").

    The header is the first row that is not empty, and no name may stand in it twice. Empty column names, as a spreadsheet
    adds them behind the last column, are no columns, and a cell under one must be empty: text there is text in no column, which
    is refused with the number of its row. Quotes are read strictly: a quote that is not closed would take the rows after it
    into one cell without a word."""
    text = decode(data, name)
    first = next((line for line in text.splitlines() if line.strip().strip(";,")), "")
    header: tuple[str, ...] | None = None
    rows, numbers = [], []
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter(first), strict=True)
    try:
        for number, record in enumerate(reader, start=1):
            cells = [cell.strip() for cell in record]
            if not any(cells):
                continue
            if header is None:
                header = tuple(cells)
                continue
            if any(cell for column, cell in zip(header, cells) if not column) or any(cells[len(header):]):
                raise Refusal(f"{name}, row {number}: a cell beyond the last column; a cell that holds the delimiter must stand "
                              "in quotes")
            cells += [""] * (len(header) - len(cells))
            rows.append({column: cell for column, cell in zip(header, cells) if column})
            numbers.append(number)
    except csv.Error:
        raise Refusal(f"{name} cannot be read as CSV near line {reader.line_num}: a quote that is not closed, or text behind a "
                      "closing quote") from None
    if header is None:
        raise Refusal(f"{name} is empty")
    twice = [column for index, column in enumerate(header) if column and column in header[:index]]
    if twice:
        raise Refusal(f"{name} names the column {twice[0]} twice: give each column its own name")
    missing = [column for column in columns if column not in header]
    if missing:
        raise Refusal(f"{name} {f'is not {what}: it lacks' if what else 'lacks'} the column{'s' if len(missing) > 1 else ''} "
                      + ", ".join(missing))
    return Table(name, tuple(column for column in header if column), rows, numbers)


# --- the two exports of the timer ---

@dataclass(frozen=True)
class Event:
    """One row of the events export: a click, a correction (undo) or a recovery choice of one case. `timestamp` is the clock of
    the PC as the timer wrote it; `mono_ms` is the timer's monotonic value in milliseconds, which only compares within one page
    load (`page`). The heartbeat cells are filled on a recovery after a reload: the last moment the old page was known alive."""
    event_id: str
    case_id: str
    condition: str
    session: str
    event: str
    timestamp: str
    mono_ms: Decimal
    page: str
    ref: str
    choice: str
    last_heartbeat_timestamp: str
    last_heartbeat_mono_ms: Decimal | None
    last_heartbeat_page: str
    note: str

    @property
    def number(self) -> int:
        """The whole number of the event id: the place of the event in the log of its study."""
        return int(self.event_id[1:])


@dataclass(frozen=True)
class TimingRow:
    """One row of the timing export: a finished case with the seconds the timer worked out, to one decimal."""
    case_id: str
    active_seconds: Decimal
    passive_seconds: Decimal
    completed: str
    manual_fallback: str
    deviations: str
    started: str


def _is_time(timestamp: str) -> bool:
    try:
        clock_ms(timestamp)
    except ValueError:
        return False
    return True


def clock_ms(timestamp: str) -> int:
    """A timestamp of the timer (the clock of the PC in ISO form, with its offset) as whole milliseconds, as the timer's own
    Date.parse reads it. A text that is no such time is a ValueError."""
    moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("a time without an offset")
    passed = moment - _EPOCH
    return passed.days * 86_400_000 + passed.seconds * 1000 + passed.microseconds // 1000


def parse_events(data: bytes, name: str) -> list[Event]:
    """The rows of one events export, in the order of the file. Refused: a file without the columns of the export, an event id
    that is not "E" and a whole number, and a row without a case id or with a clock value the timer did not write (a spreadsheet
    that saves the file rewrites its numbers, and the seconds cannot be worked out from what it leaves). The refusal names the
    row and the column, never the cell."""
    read = table(data, name, EVENT_COLUMNS, "an events export of the timer")
    events = []
    for number, row in zip(read.numbers, read.rows):
        if not _EVENT_ID.fullmatch(row["event_id"]):
            raise Refusal(f"{name}, row {number}: the event id is not E and a whole number: give the file as the timer exported it")
        heartbeat = row["last_heartbeat_mono_ms"]
        for column, valid in (("case_id", row["case_id"]), ("timestamp", _is_time(row["timestamp"])),
                              ("mono_ms", _NUMBER.fullmatch(row["mono_ms"])),
                              ("last_heartbeat_mono_ms", not heartbeat or _NUMBER.fullmatch(heartbeat))):
            if not valid:
                raise Refusal(f"{name}, row {number}: {column} {AS_EXPORTED}")
        cells = {column: row[column] for column in EVENT_COLUMNS}
        events.append(Event(**cells | {"mono_ms": Decimal(row["mono_ms"]),
                                       "last_heartbeat_mono_ms": Decimal(heartbeat) if heartbeat else None}))
    return events


def parse_timing(data: bytes, name: str) -> list[TimingRow]:
    """The rows of one timing export, in the order of the file. Refused: a file without the columns of the export, and a row
    without a case id or with seconds that are not a plain number with a decimal point."""
    read = table(data, name, TIMING_COLUMNS, "a timing export of the timer")
    rows = []
    for number, row in zip(read.numbers, read.rows):
        for column, valid in (("case_id", row["case_id"]), ("active_seconds", _NUMBER.fullmatch(row["active_seconds"])),
                              ("passive_seconds", _NUMBER.fullmatch(row["passive_seconds"]))):
            if not valid:
                raise Refusal(f"{name}, row {number}: {column} {AS_EXPORTED}")
        cells = {column: row[column] for column in TIMING_COLUMNS}
        rows.append(TimingRow(**cells | {"active_seconds": Decimal(row["active_seconds"]),
                                         "passive_seconds": Decimal(row["passive_seconds"])}))
    return rows


def read_events(paths: Iterable[str | Path]) -> list[Event]:
    """The events of every events export given, file after file. Merging the files is the work of cases()."""
    return [event for path in paths for event in parse_events(read_bytes(path), Path(path).name)]


def read_timing(paths: Iterable[str | Path]) -> list[TimingRow]:
    """The rows of every timing export given, file after file. The same case stands in every export taken after its stop; which
    of its rows counts is decided by check()."""
    return [row for path in paths for row in parse_timing(read_bytes(path), Path(path).name)]


# --- the log of one case, as the timer reads it ---

def cases(events: Iterable[Event]) -> dict[str, list[Event]]:
    """The log of every case: the events of all exports merged by case id and event id, each event once, in the order of the
    number in the event id. That is the order of the timer's own log, which its rules read; the text of the id gives another
    order (E10 before E9) and so does the timestamp (the pause of a reopened case carries the time of the stop it replaces).
    An event id that stands in the exports with two different contents is kept twice, in the order given, for check() to name.
    The cases stand in the order of their first event."""
    logs: dict[str, list[Event]] = {}
    seen: set[Event] = set()
    for event in events:
        if event not in seen:
            seen.add(event)
            logs.setdefault(event.case_id, []).append(event)
    for log in logs.values():
        log.sort(key=lambda event: event.number)
    return dict(sorted(logs.items(), key=lambda item: (item[1][0].number, item[0])))


def effective(events: Sequence[Event]) -> list[Event]:
    """The events of one case that count: all but the undo events and the events an undo names in `ref` (effective() of the
    timer). A cancelled click stays in the log and in the export; it only stops counting."""
    cancelled = {event.ref for event in events if event.event == "undo"}
    return [event for event in events if event.event != "undo" and event.event_id not in cancelled]


def _after(mode: str, event: Event) -> str:
    """The state of a case after an effective event, by the timer's rules: a click of MODE sets its state, a recovery counted as
    a pause leaves the case paused and one counted as work leaves it as it was, a stop ends it, and a fallback changes nothing."""
    if event.event in MODE:
        return MODE[event.event]
    if event.event == "recovery":
        return "paused" if event.choice == "pause" else mode
    return "done" if event.event in TERMINAL else mode


def state(events: Sequence[Event]) -> str:
    """The state the log of one case ends in, as state() of the timer gives it: idle (no event), active, paused, waiting or
    done."""
    mode = "idle"
    for event in effective(events):
        mode = _after(mode, event)
    return mode


def _interval(previous: Event, event: Event) -> tuple[Decimal, str]:
    """The milliseconds the timer reads between two effective events that follow each other, and what it reads them from.
    Within one page load ("page"): the monotonic value. Across a reload the monotonic values do not compare: a recovery counted
    as work takes the clock of the PC ("clock"); a recovery counted as a pause takes the time up to the last heartbeat of the
    old page ("heartbeat"); anything else across a reload counts nothing ("reload")."""
    if event.page == previous.page:
        return event.mono_ms - previous.mono_ms, "page"
    if event.event == "recovery" and event.choice == "work":
        return Decimal(clock_ms(event.timestamp) - clock_ms(previous.timestamp)), "clock"
    if event.event == "recovery" and event.last_heartbeat_mono_ms is not None and event.last_heartbeat_page == previous.page:
        return event.last_heartbeat_mono_ms - previous.mono_ms, "heartbeat"
    return Decimal(0), "reload"


def _steps(events: Sequence[Event]) -> Iterator[tuple[str, Event | None, Event, tuple[Decimal, str] | None]]:
    """Walk the effective events of one case as summarise() of the timer does. Per event: the state before it, the event before
    it, the event, and the interval the timer reads up to it (None before the start and after a stop, where it reads none)."""
    mode, previous = "idle", None
    for event in effective(events):
        yield mode, previous, event, _interval(previous, event) if previous is not None and mode not in ("idle", "done") else None
        mode, previous = _after(mode, event), event


@dataclass(frozen=True)
class Seconds:
    """The seconds of one case worked out from its events, not rounded, and the timer's own remarks on them ("negative interval
    before E7"), which it writes into the deviations of the timing row."""
    active: Decimal
    passive: Decimal
    flags: tuple[str, ...]


def seconds(events: Sequence[Event]) -> Seconds:
    """The active and passive seconds of one case, worked out again from its events: a port of summarise() of the timer. Time
    counts as active while the case is active and as passive while it waits for the aid; a pause counts as neither. A negative
    interval counts as zero and is named.

    The figures can differ from the timing row by the rounding of the exports only: at most 0.05 s for the row's one decimal and
    0.0001 s per interval for the monotonic values, which are exported to a tenth of a millisecond."""
    active = passive = Decimal(0)
    flags = []
    for mode, _, event, interval in _steps(events):
        if interval is None:
            continue
        counted = interval[0]
        if counted < 0:
            flags.append(f"negative interval before {event.event_id}")
            counted = Decimal(0)
        if mode == "active":
            active += counted
        elif mode == "waiting":
            passive += counted
    return Seconds(active / 1000, passive / 1000, tuple(flags))


def tenth(value: Decimal) -> str:
    """Seconds to one decimal as the timer writes them: a tie goes up (12.25 is "12.3"), where Python's formatting of a float
    gives 12.2."""
    return str(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


# --- the check of one case ---

@dataclass(frozen=True)
class Check:
    """What the two exports say about one case.

    Criterion 1 of the protocol's "the timing procedure works" is `log_complete`, with its parts: `start` (exactly one effective
    start, as the first effective event), `stop` (the last effective event is complete or incomplete) and the pauses (every
    pause, a recovery counted as a pause too, is followed by a resume before the stop: `pauses` equals `pauses_closed`). A case
    without a pause is complete. The waits are counted beside it and are no part of it (a wait is closed by its end or by a
    pause).

    `consistent` is kept apart and never changes log_complete. Its parts: `one_record` (every event id has one content, and the
    events carry one condition, manual or assisted), `legal_steps` (only steps the timer's buttons allow), `time_order` (the
    events stand in the order of time: by the monotonic value within one page load, by the clock of the PC across a reload),
    `no_negative_interval` (the timer counted no interval as zero for being negative) and `timing_agrees` (a timing row agrees
    with the events in completed, fallback and both seconds, within TOLERANCE).

    `problems` are the sentences for what makes log_complete or consistent false, each with its likely cause where one is known;
    `notes` say what is no problem. No sentence holds a time, seconds or a text of the files: events are named by their id.
    The other two criteria of the protocol (the boundaries of section 6.3, and active against passive time) are the
    researcher's judgement and are not answered here."""
    case_id: str
    condition: str                       # of the first event; "" without events
    conditions: tuple[str, ...]          # every condition the events carry, in the order of the log
    events: int                          # the events of the log, undo events and cancelled clicks included
    effective: tuple[Event, ...]
    log_complete: bool
    start: bool
    stop: bool
    pauses: int
    pauses_closed: int
    waits: int
    waits_closed: int
    consistent: bool
    one_record: bool
    legal_steps: bool
    time_order: bool
    no_negative_interval: bool
    timing_agrees: bool
    started: str                         # the timestamp of the first effective start, as the timing export has it
    stopped: str                         # the timestamp of the stop; "" while the case runs
    status: str                          # complete, incomplete, or "" while the case runs
    fallback: bool
    seconds: Seconds                     # worked out again from the events
    timing: TimingRow | None             # the timing row that counts: the one that agrees with the events
    superseded: int                      # the other timing rows of the case, when one counts
    problems: tuple[str, ...]
    notes: tuple[str, ...]


def check(events: Sequence[Event], timing: Sequence[TimingRow]) -> Check:
    """Check the log of one case: `events` is its log as cases() gives it, `timing` the rows of the case in the timing exports
    (several exports repeat a row; a case reopened between two exports has two rows that differ).

    The timing row that counts is the one that agrees with the seconds worked out from the events; any other is superseded by
    a later export, which is a note and no problem. When none agrees, or the log has no stop, no row counts. Of an event id
    with two contents the first is read."""
    first: dict[str, Event] = {}
    doubled: list[str] = []
    for event in events:
        if first.setdefault(event.event_id, event) != event and event.event_id not in doubled:
            doubled.append(event.event_id)
    log = list(first.values())
    clicks = effective(log)
    conditions = tuple(dict.fromkeys(event.condition for event in log))
    starts = [event for event in clicks if event.event == "start"]
    start = len(starts) == 1 and clicks[0] is starts[0]
    stop = bool(clicks) and clicks[-1].event in TERMINAL

    pauses = pauses_closed = waits = waits_closed = open_pauses = open_waits = fallbacks = 0
    restarted = False
    illegal = [(event.number, f"{event.event_id} undoes an event that is not in the log of this case")
               for event in log if event.event == "undo" and event.ref not in first]
    times: list[str] = []
    time_order = no_negative_interval = True
    for mode, previous, event, interval in _steps(log):
        name, pause = event.event, event.event == "pause" or (event.event == "recovery" and event.choice == "pause")
        if pause:
            pauses, open_pauses, waits_closed, open_waits = pauses + 1, open_pauses + 1, waits_closed + open_waits, 0
        elif name == "resume":
            pauses_closed, open_pauses = pauses_closed + open_pauses, 0
        elif name == "wait":
            waits, open_waits = waits + 1, open_waits + 1
        elif name == "wait_end":
            waits_closed, open_waits = waits_closed + open_waits, 0
        elif name in TERMINAL:
            open_pauses = open_waits = 0
        sentence = _illegal_step(mode, event, fallbacks)
        if sentence:
            illegal.append((event.number, sentence))
            restarted = restarted or sentence == TWO_HANDLINGS
        fallbacks += 1 if name == "fallback" else 0
        if previous is not None:
            sentences, ordered, negative = _order(previous, event, interval)
            times += sentences
            time_order, no_negative_interval = time_order and ordered, no_negative_interval and not negative

    problems: list[str] = []
    if not log:
        problems.append(NO_EVENTS)
    else:
        if not clicks or clicks[0].event != "start":
            problems.append("the log does not begin with a start")
        elif len(starts) > 1:
            problems.append(TWO_HANDLINGS if restarted else "more than one start in the log")
        if not stop:
            problems.append(RUNNING)
        elif pauses > pauses_closed:
            open_pauses = pauses - pauses_closed
            problems.append(f"{open_pauses} pause{'s' if open_pauses > 1 else ''} without a resume before the stop")
        problems += [f"{event_id} stands in the exports with two contents: give the exports of one study only"
                     for event_id in doubled]
        if len(conditions) > 1:
            problems.append("more than one condition in the events of this case")
        elif conditions[0] not in CONDITIONS:
            problems.append("the condition of this case is neither manual nor assisted")
        problems += [sentence for _, sentence in sorted(illegal, key=lambda step: step[0])] + times

    worked_out, status, fallback = seconds(log), clicks[-1].event if stop else "", fallbacks > 0
    rows = list(dict.fromkeys(timing))                                   # the same row of two exports is one row
    differing = {row: _differences(row, worked_out, status, fallback) for row in rows} if stop else {}
    agreeing = [row for row in rows if stop and not differing[row]]
    chosen = agreeing[-1] if agreeing else None
    superseded = len(rows) - 1 if chosen else 0
    if stop and not rows:
        problems.append(NO_TIMING_ROW)
    elif stop and not agreeing and len(rows) == 1:
        problems.append(f"the timing row does not agree with the events in {', '.join(differing[rows[0]])}: the two files are "
                        f"{NOT_THE_SAME_MOMENT}")
    elif stop and not agreeing:
        problems.append(f"none of the {len(rows)} timing rows agrees with the events: the files are {NOT_THE_SAME_MOMENT}")
    notes = (f"{superseded} timing row{'s' if superseded > 1 else ''} {SUPERSEDED}",) if superseded else ()

    one_record = not doubled and len(conditions) == 1 and conditions[0] in CONDITIONS
    return Check(
        case_id=log[0].case_id if log else (timing[0].case_id if timing else ""), condition=conditions[0] if conditions else "",
        conditions=conditions, events=len(log), effective=tuple(clicks),
        log_complete=start and stop and pauses == pauses_closed, start=start, stop=stop, pauses=pauses, pauses_closed=pauses_closed,
        waits=waits, waits_closed=waits_closed,
        consistent=one_record and not illegal and time_order and no_negative_interval and chosen is not None,
        one_record=one_record, legal_steps=not illegal, time_order=time_order, no_negative_interval=no_negative_interval,
        timing_agrees=chosen is not None,
        started=starts[0].timestamp if starts else "", stopped=clicks[-1].timestamp if stop else "", status=status,
        fallback=fallback, seconds=worked_out, timing=chosen, superseded=superseded,
        problems=tuple(dict.fromkeys(problems)), notes=notes)


def _illegal_step(mode: str, event: Event, fallbacks: int) -> str:
    """The sentence for an effective event that is no legal step from the state before it, or "". The steps are the timer's: a
    start opens the case; a pause comes from active or waiting, a resume from paused, a wait from active and its end from
    waiting; a recovery is asked for on an active or waiting case and carries its choice; a stop comes from active only; a wait
    and a fallback are steps of an assisted case, and a fallback comes once. A start after a stop is a second handling."""
    name, event_id = event.event, event.event_id
    if name not in LEGAL_FROM:
        return f"{event_id} is not an event of the timer"
    if name == "start" and mode == "done":
        return TWO_HANDLINGS
    if mode not in LEGAL_FROM[name]:
        where = {"idle": "before the start", "done": "after the stop"}.get(mode, f"while the case is {mode}")
        return f"{event_id}: {name} is not a legal step {where}"
    if name in ("wait", "fallback") and event.condition != "assisted":
        return f"{event_id}: {name} is only a step of an assisted case"
    if name == "fallback" and fallbacks:
        return f"{event_id}: a second fallback"
    if name == "recovery" and event.choice not in ("pause", "work"):
        return f"{event_id}: a recovery without the choice pause or work"
    return ""


def _order(previous: Event, event: Event, interval: tuple[Decimal, str] | None) -> tuple[list[str], bool, bool]:
    """Two effective events that follow each other: the sentences for what is wrong with their order, whether they stand in
    the order of time, and whether the timer read a negative interval between them. Time is read as the timer reads it: the
    monotonic value within one page load, the clock of the PC across a reload. A negative interval the timer read from the last
    heartbeat has a cause of its own: the page closed within the five seconds after a click, before the next heartbeat."""
    same_page = event.page == previous.page
    ordered = event.mono_ms >= previous.mono_ms if same_page else clock_ms(event.timestamp) >= clock_ms(previous.timestamp)
    clock = "the timer's clock" if same_page else "the clock of the PC"
    earlier = f"{event.event_id} is earlier than {previous.event_id} by {clock}"
    negative = interval is not None and interval[0] < 0
    if negative and interval[1] == "heartbeat":
        sentences = [f"negative interval before {event.event_id}: the last heartbeat before the reload is older than "
                     f"{previous.event_id}; the timer counts nothing between them"] + ([] if ordered else [earlier])
    elif negative:
        sentences = [f"negative interval before {event.event_id}: {earlier}; the timer counts it as zero"]
    else:
        sentences = [] if ordered else [earlier]
    return sentences, ordered, negative


def _differences(row: TimingRow, worked_out: Seconds, status: str, fallback: bool) -> list[str]:
    """In what a timing row does not agree with the log of its case: the seconds as numbers, within TOLERANCE of the seconds
    worked out again, and the two yes-or-no cells."""
    return [what for what, differs in (
        ("the active seconds", abs(row.active_seconds - worked_out.active) > TOLERANCE),
        ("the passive seconds", abs(row.passive_seconds - worked_out.passive) > TOLERANCE),
        ("completed", row.completed.lower() != ("yes" if status == "complete" else "no")),
        ("manual_fallback", row.manual_fallback.lower() != ("yes" if fallback else "no"))) if differs]
