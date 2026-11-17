"""Tests of the reading and the check of the timer's exports (aid/evalscripts/timer_log.py).

The rows of EVENTS, TIMING and of the second study are exports of evaluation/timer.html itself: its own click handlers were run
in node with a simulated clock (start, pause, reload and so on, with the heartbeat every five seconds), and the two export
buttons gave the text that stands here unchanged. The seconds in TIMER are what the timer's own summarise() gives for each case,
obtained once in the same way and written down; the last test of this file asks the timer again for the same rows, where node
and evaluation/timer.html are present. The rows of HAND_MADE were written by hand: no click of the timer gives them. All case ids
and notes are made up.
"""
import json
import re
import shutil
import subprocess
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from aid.evalscripts import timer_log
from aid.evalscripts.timer_log import (RUNNING, TWO_HANDLINGS, Refusal, TimingRow, cases, check, effective, parse_events,
                                       parse_timing, read_events, read_timing, seconds, state, table, tenth)
from aid.tests.conftest import REPO_ROOT

# Twelve cases of one study, exported after the last stop. Per case, what was clicked:
# C001 manual: 60 s of work, a pause of 30 s, 45.5 s of work.
# C002 manual: a pause after 10 s that is undone 5 s later, complete 10 s after that.
# C003 assisted: 10 s of work, 20 s of waiting for the aid, 15 s of work.
# C004 assisted: a wait that a pause ends, a manual fallback, stopped as incomplete.
# C005 manual: the page reloaded 12 s after the start and the gap counted as a pause (the last heartbeat 7.5 s after the start).
# C006 manual: the page reloaded and the gap counted as work.
# C007 assisted: the page reloaded while waiting for the aid and the gap counted as continued, so as waiting.
# C008 manual: stopped as incomplete after 10 s, reopened as a pause, 20 s of work, complete.
# C009 manual: complete after 60 s, the page reloaded, reopened with the 60 s since counted as work, 60 s more.
# C010 manual: the page reloaded 2 s after a resume and counted as a pause; the last heartbeat is 1 s older than the resume.
# C011 manual: 12.25 s of work, a tie at a tenth of a second.
# C012 assisted: 10 s of work, 12 s of waiting, 4 s of work.
EVENTS = """\
event_id,case_id,condition,session,event,timestamp,mono_ms,page,ref,choice,last_heartbeat_timestamp,last_heartbeat_mono_ms,last_heartbeat_page,note
E1,C001,manual,,start,2026-12-01T09:00:00.000Z,1796115600000.3,mx4guqo0-bb5ex3,,,,,,
E2,C001,manual,,pause,2026-12-01T09:01:00.000Z,1796115660000.3,mx4guqo0-bb5ex3,,,,,,
E3,C001,manual,,resume,2026-12-01T09:01:30.000Z,1796115690000.3,mx4guqo0-bb5ex3,,,,,,
E4,C001,manual,,complete,2026-12-01T09:02:15.500Z,1796115735500.3,mx4guqo0-bb5ex3,,,,,,
E5,C002,manual,,start,2026-12-01T09:02:35.500Z,1796115755500.3,mx4guqo0-bb5ex3,,,,,,
E6,C002,manual,,pause,2026-12-01T09:02:45.500Z,1796115765500.3,mx4guqo0-bb5ex3,,,,,,
E7,C002,manual,,undo,2026-12-01T09:02:50.500Z,1796115770500.3,mx4guqo0-bb5ex3,E6,,,,,pause clicked by mistake
E8,C002,manual,,complete,2026-12-01T09:03:00.500Z,1796115780500.3,mx4guqo0-bb5ex3,,,,,,
E9,C003,assisted,,start,2026-12-01T09:03:20.500Z,1796115800500.3,mx4guqo0-bb5ex3,,,,,,
E10,C003,assisted,,wait,2026-12-01T09:03:30.500Z,1796115810500.3,mx4guqo0-bb5ex3,,,,,,
E11,C003,assisted,,wait_end,2026-12-01T09:03:50.500Z,1796115830500.3,mx4guqo0-bb5ex3,,,,,,
E12,C003,assisted,,complete,2026-12-01T09:04:05.500Z,1796115845500.3,mx4guqo0-bb5ex3,,,,,,
E13,C004,assisted,,start,2026-12-01T09:04:25.500Z,1796115865500.3,mx4guqo0-bb5ex3,,,,,,
E14,C004,assisted,,wait,2026-12-01T09:04:33.500Z,1796115873500.3,mx4guqo0-bb5ex3,,,,,,
E15,C004,assisted,,pause,2026-12-01T09:04:45.500Z,1796115885500.3,mx4guqo0-bb5ex3,,,,,,
E16,C004,assisted,,resume,2026-12-01T09:05:15.500Z,1796115915500.3,mx4guqo0-bb5ex3,,,,,,
E17,C004,assisted,,fallback,2026-12-01T09:05:17.500Z,1796115917500.3,mx4guqo0-bb5ex3,,,,,,
E18,C004,assisted,,incomplete,2026-12-01T09:05:22.500Z,1796115922500.3,mx4guqo0-bb5ex3,,,,,,the confirmation cannot be read
E19,C005,manual,,start,2026-12-01T09:05:42.500Z,1796115942500.3,mx4guqo0-bb5ex3,,,,,,
E20,C005,manual,,recovery,2026-12-01T09:07:54.500Z,1796116074500.3,mx4h4wsk-9saeyk,,pause,2026-12-01T09:05:50.000Z,1796115950000.3,mx4guqo0-bb5ex3,"page reloaded; time up to the last heartbeat counted, the gap after it as a pause"
E21,C005,manual,,resume,2026-12-01T09:07:57.500Z,1796116077500.3,mx4h4wsk-9saeyk,,,,,,
E22,C005,manual,,complete,2026-12-01T09:08:12.500Z,1796116092500.3,mx4h4wsk-9saeyk,,,,,,
E23,C006,manual,,start,2026-12-01T09:08:32.500Z,1796116112500.3,mx4h4wsk-9saeyk,,,,,,
E24,C006,manual,,recovery,2026-12-01T09:09:12.500Z,1796116152500.3,mx4h6kz8-ks2kno,,work,2026-12-01T09:08:39.500Z,1796116119500.3,mx4h4wsk-9saeyk,page reloaded; gap counted as work
E25,C006,manual,,complete,2026-12-01T09:09:17.500Z,1796116157500.3,mx4h6kz8-ks2kno,,,,,,
E26,C007,assisted,,start,2026-12-01T09:09:37.500Z,1796116177500.3,mx4h6kz8-ks2kno,,,,,,
E27,C007,assisted,,wait,2026-12-01T09:09:47.500Z,1796116187500.3,mx4h6kz8-ks2kno,,,,,,
E28,C007,assisted,,recovery,2026-12-01T09:10:52.500Z,1796116252500.3,mx4h8q50-oybh3d,,work,2026-12-01T09:09:52.500Z,1796116192500.3,mx4h6kz8-ks2kno,page reloaded; gap counted as passive waiting (the case was waiting for the aid)
E29,C007,assisted,,wait_end,2026-12-01T09:10:54.500Z,1796116254500.3,mx4h8q50-oybh3d,,,,,,
E30,C007,assisted,,complete,2026-12-01T09:10:59.500Z,1796116259500.3,mx4h8q50-oybh3d,,,,,,
E31,C008,manual,,start,2026-12-01T09:11:19.500Z,1796116279500.3,mx4h8q50-oybh3d,,,,,,
E32,C008,manual,,incomplete,2026-12-01T09:11:29.500Z,1796116289500.3,mx4h8q50-oybh3d,,,,,,stopped by mistake
E33,C008,manual,,undo,2026-12-01T09:12:19.500Z,1796116339500.3,mx4h8q50-oybh3d,E32,,,,,the case was not finished
E34,C008,manual,,pause,2026-12-01T09:11:29.500Z,1796116289500.3,mx4h8q50-oybh3d,,,,,,back-dated to the cancelled incomplete click
E35,C008,manual,,resume,2026-12-01T09:12:24.500Z,1796116344500.3,mx4h8q50-oybh3d,,,,,,
E36,C008,manual,,complete,2026-12-01T09:12:44.500Z,1796116364500.3,mx4h8q50-oybh3d,,,,,,
E37,C009,manual,,start,2026-12-01T09:13:04.500Z,1796116384500.3,mx4h8q50-oybh3d,,,,,,
E38,C009,manual,,complete,2026-12-01T09:14:04.500Z,1796116444500.3,mx4h8q50-oybh3d,,,,,,
E39,C009,manual,,undo,2026-12-01T09:15:04.500Z,1796116504500.3,mx4he4l0-ewtjq0,E38,,,,,completed too early
E40,C009,manual,,recovery,2026-12-01T09:15:04.500Z,1796116504500.3,mx4he4l0-ewtjq0,,work,,,,reopened after E38; the time since counted as work
E41,C009,manual,,complete,2026-12-01T09:16:04.500Z,1796116564500.3,mx4he4l0-ewtjq0,,,,,,
E42,C010,manual,,start,2026-12-01T09:16:24.500Z,1796116584500.3,mx4he4l0-ewtjq0,,,,,,
E43,C010,manual,,pause,2026-12-01T09:16:36.500Z,1796116596500.3,mx4he4l0-ewtjq0,,,,,,
E44,C010,manual,,resume,2026-12-01T09:16:40.500Z,1796116600500.3,mx4he4l0-ewtjq0,,,,,,
E45,C010,manual,,recovery,2026-12-01T09:17:22.500Z,1796116642500.3,mx4hh32c-qcqk8i,,pause,2026-12-01T09:16:39.500Z,1796116599500.3,mx4he4l0-ewtjq0,"page reloaded; time up to the last heartbeat counted, the gap after it as a pause"
E46,C010,manual,,resume,2026-12-01T09:17:23.500Z,1796116643500.3,mx4hh32c-qcqk8i,,,,,,
E47,C010,manual,,complete,2026-12-01T09:17:33.500Z,1796116653500.3,mx4hh32c-qcqk8i,,,,,,
E48,C011,manual,,start,2026-12-01T09:17:54.500Z,1796116674500.0,mx4hhrr8-m8yzre,,,,,,
E49,C011,manual,,complete,2026-12-01T09:18:06.750Z,1796116686750.0,mx4hhrr8-m8yzre,,,,,,
E50,C012,assisted,,start,2026-12-01T09:18:26.750Z,1796116706750.0,mx4hhrr8-m8yzre,,,,,,
E51,C012,assisted,,wait,2026-12-01T09:18:36.750Z,1796116716750.0,mx4hhrr8-m8yzre,,,,,,
E52,C012,assisted,,wait_end,2026-12-01T09:18:48.750Z,1796116728750.0,mx4hhrr8-m8yzre,,,,,,
E53,C012,assisted,,complete,2026-12-01T09:18:52.750Z,1796116732750.0,mx4hhrr8-m8yzre,,,,,,
"""
TIMING = """\
case_id,active_seconds,passive_seconds,completed,manual_fallback,deviations,started
C001,105.5,0.0,yes,no,,2026-12-01T09:00:00.000Z
C002,25.0,0.0,yes,no,undo of pause (E6): pause clicked by mistake,2026-12-01T09:02:35.500Z
C003,25.0,20.0,yes,no,,2026-12-01T09:03:20.500Z
C004,15.0,12.0,no,yes,incomplete: the confirmation cannot be read,2026-12-01T09:04:25.500Z
C005,22.5,0.0,yes,no,"page reloaded, time up to the last heartbeat counted, the gap after it as a pause",2026-12-01T09:05:42.500Z
C006,45.0,0.0,yes,no,"page reloaded, gap counted as work",2026-12-01T09:08:32.500Z
C007,15.0,67.0,yes,no,"page reloaded, gap counted as passive waiting (the case was waiting for the aid)",2026-12-01T09:09:37.500Z
C008,30.0,0.0,yes,no,"incomplete: stopped by mistake; reopened, undo of incomplete (E32), gap counted as a pause: the case was not finished",2026-12-01T09:11:19.500Z
C009,180.0,0.0,yes,no,"reopened, undo of complete (E38), gap counted as work: completed too early",2026-12-01T09:13:04.500Z
C010,22.0,0.0,yes,no,"page reloaded, time up to the last heartbeat counted, the gap after it as a pause; negative interval before E45",2026-12-01T09:16:24.500Z
C011,12.3,0.0,yes,no,,2026-12-01T09:17:54.500Z
C012,14.0,12.0,yes,no,,2026-12-01T09:18:26.750Z
"""
EVENTS_HEADER, TIMING_HEADER = EVENTS.splitlines()[0], TIMING.splitlines()[0]
# The timing row of C008 in the export taken after its first stop, before it was reopened.
TIMING_BEFORE_REOPENING = TIMING_HEADER + "\nC008,10.0,0.0,no,no,incomplete: stopped by mistake,2026-12-01T09:11:19.500Z\n"
# After "New study (reset)" C001 was handled once more, in the other condition: the event ids go on where the first study ended.
SECOND_STUDY = EVENTS_HEADER + """
E54,C001,assisted,,start,2026-12-01T09:28:52.750Z,1796117332750.0,mx4hhrr8-m8yzre,,,,,,
E55,C001,assisted,,complete,2026-12-01T09:29:22.750Z,1796117362750.0,mx4hhrr8-m8yzre,,,,,,
"""
TIMING_SECOND_STUDY = TIMING_HEADER + "\nC001,30.0,0.0,yes,no,,2026-12-01T09:28:52.750Z\n"
# Written by hand, three cases:
# C013: a resume that the timer's clock puts one second before its pause, on one page load.
# C014: a reload counted as work, where the monotonic values of the new page lie 3 s behind the clock of the PC.
# C015: a second start that the timer's clock puts before the stop of the first handling.
HAND_MADE = EVENTS_HEADER + """
E56,C013,manual,,start,2026-12-01T09:40:00.000Z,1796118000000.0,mx4iabc0-handmd,,,,,,
E57,C013,manual,,pause,2026-12-01T09:40:10.000Z,1796118010000.0,mx4iabc0-handmd,,,,,,
E58,C013,manual,,resume,2026-12-01T09:40:09.000Z,1796118009000.0,mx4iabc0-handmd,,,,,,
E59,C013,manual,,complete,2026-12-01T09:40:12.000Z,1796118012000.0,mx4iabc0-handmd,,,,,,
E60,C014,manual,,start,2026-12-01T09:50:00.000Z,1796118600000.0,mx4iabc0-handmd,,,,,,
E61,C014,manual,,recovery,2026-12-01T09:50:40.000Z,1796118637000.0,mx4ixyz0-handmd,,work,2026-12-01T09:50:05.000Z,1796118605000.0,mx4iabc0-handmd,page reloaded; gap counted as work
E62,C014,manual,,complete,2026-12-01T09:50:45.000Z,1796118642000.0,mx4ixyz0-handmd,,,,,,
E63,C015,manual,,start,2026-12-01T09:55:00.000Z,1796118900000.0,mx4ixyz0-handmd,,,,,,
E64,C015,manual,,complete,2026-12-01T09:55:10.000Z,1796118910000.0,mx4ixyz0-handmd,,,,,,
E65,C015,manual,,start,2026-12-01T09:55:05.000Z,1796118905000.0,mx4ixyz0-handmd,,,,,,
E66,C015,manual,,complete,2026-12-01T09:55:20.000Z,1796118920000.0,mx4ixyz0-handmd,,,,,,
"""
TIMING_HAND_MADE = TIMING_HEADER + "\nC013,13.0,0.0,yes,no,negative interval before E58,2026-12-01T09:40:00.000Z\n"

# The sequences of the tests: the case, the exports that hold its events, and the last event id that counts (an export taken
# earlier ends there).
SEQUENCES = {**{f"C{n:03d}": (f"C{n:03d}", (EVENTS,), 53) for n in range(1, 13)},
             "C008 before it was reopened": ("C008", (EVENTS,), 32),
             "C012 while it waits for the aid": ("C012", (EVENTS,), 51),
             "C001 of two studies": ("C001", (EVENTS, SECOND_STUDY), 55),
             **{case_id: (case_id, (HAND_MADE,), 66) for case_id in ("C013", "C014", "C015")}}
# What the timer's own summarise() and state() give for each sequence: the active seconds, the passive seconds, the flags and the
# state. Obtained once by running the script of evaluation/timer.html in node on these rows (see the last test), and written down.
TIMER = {
    "C001": ("105.5", "0.0", [], "done"),
    "C002": ("25.0", "0.0", [], "done"),
    "C003": ("25.0", "20.0", [], "done"),
    "C004": ("15.0", "12.0", [], "done"),
    "C005": ("22.5", "0.0", [], "done"),
    "C006": ("45.0", "0.0", [], "done"),
    "C007": ("15.0", "67.0", [], "done"),
    "C008": ("30.0", "0.0", [], "done"),
    "C009": ("180.0", "0.0", [], "done"),
    "C010": ("22.0", "0.0", ["negative interval before E45"], "done"),
    "C011": ("12.3", "0.0", [], "done"),
    "C012": ("14.0", "12.0", [], "done"),
    "C008 before it was reopened": ("10.0", "0.0", [], "done"),
    "C012 while it waits for the aid": ("10.0", "0.0", [], "waiting"),
    "C001 of two studies": ("135.5", "0.0", [], "done"),
    "C013": ("13.0", "0.0", ["negative interval before E58"], "done"),
    "C014": ("45.0", "0.0", [], "done"),
    "C015": ("25.0", "0.0", [], "done"),
}


def export(*case_ids: str, source: str = EVENTS) -> bytes:
    """The header of an export and its rows of these cases (of every case when none is named), as the bytes of a file."""
    header, *rows = source.splitlines()
    column = header.split(",").index("case_id")
    return "\n".join([header] + [row for row in rows if not case_ids or row.split(",")[column] in case_ids]).encode("utf-8") + b"\n"


def log(name: str) -> list:
    """The events of one sequence of SEQUENCES, merged and ordered as the command does it."""
    case_id, exports, last = SEQUENCES[name]
    events = [event for text in exports for event in parse_events(text.encode("utf-8"), "export.csv") if event.number <= last]
    return cases(events)[case_id]


def ids(events) -> list[str]:
    return [event.event_id for event in events]


def timing(case_id: str, *exports: str) -> list[TimingRow]:
    """The rows of one case in these timing exports (in TIMING when none is given), export after export."""
    rows = [row for text in exports or (TIMING,) for row in parse_timing(text.encode("utf-8"), "timing.csv")]
    return [row for row in rows if row.case_id == case_id]


def made(condition: str, *steps, case_id: str = "C020") -> list:
    """A log written by hand, for the sequences no click of the timer gives. A step is the name of the event, the second it
    stands at, and further cells by the name of their column; the event ids go from E101 and every event is of one page load
    unless a step names another page."""
    rows = [EVENTS_HEADER]
    for index, (event, second, *more) in enumerate(steps):
        moment = datetime(2026, 12, 2, 10, tzinfo=timezone.utc) + timedelta(seconds=second)
        cells = dict.fromkeys(EVENTS_HEADER.split(","), "") | {
            "event_id": f"E{101 + index}", "case_id": case_id, "condition": condition, "event": event, "page": "page-1",
            "timestamp": moment.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "mono_ms": f"{1796205600000 + second * 1000:.1f}"} | (more[0] if more else {})
        rows.append(",".join(cells.values()))
    return cases(parse_events("\n".join(rows).encode("utf-8"), "made.csv"))[case_id]


def row_for(events, **cells) -> list[TimingRow]:
    """The timing row the timer would write for a log, with the cells given changed."""
    clicks, worked_out = effective(events), seconds(events)
    row = TimingRow(events[0].case_id, Decimal(tenth(worked_out.active)), Decimal(tenth(worked_out.passive)),
                    "yes" if clicks[-1].event == "complete" else "no",
                    "yes" if any(click.event == "fallback" for click in clicks) else "no",
                    "; ".join(worked_out.flags), events[0].timestamp)
    return [replace(row, **cells)]


def c011_with_another_stop() -> list:
    """The events of C011 as another export might have them: the same event ids, and E49 ten seconds later."""
    changed = export("C011").replace(b"12-01T09:18:06.750Z,1796116686750.0", b"12-01T09:18:16.750Z,1796116696750.0")
    assert changed != export("C011")
    return parse_events(changed, "b.csv")


def write(folder, name: str, data: bytes | str):
    """A file under the test's folder with this content; returns its path."""
    path = folder / name
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


# --- reading a CSV: the rules every input of the records command is read by ---

def test_a_csv_is_read_from_bytes_with_or_without_a_byte_order_mark():
    plain = table(b"case_id,note\nC001,caf\xc3\xa9\n", "list.csv")
    marked = table(b"\xef\xbb\xbfcase_id,note\nC001,caf\xc3\xa9\n", "list.csv")
    assert plain.header == marked.header == ("case_id", "note")
    assert plain.rows == marked.rows == [{"case_id": "C001", "note": "caf\u00e9"}]


def test_a_csv_saved_in_the_windows_code_page_is_read():
    """A spreadsheet's plain "CSV" is in Windows-1252, where an accented letter is one byte that is no UTF-8."""
    saved = "case_id;note\r\nC001;caf\u00e9\r\n".encode("cp1252")
    assert table(saved, "list.csv").rows == [{"case_id": "C001", "note": "caf\u00e9"}]


def test_bytes_that_are_no_text_are_refused_with_what_to_do():
    with pytest.raises(Refusal, match="list.csv: save the file as CSV UTF-8"):
        table(b"case_id,note\nC001,\x81\x8d\n", "list.csv")
    with pytest.raises(Refusal, match="list.csv: save the file as CSV UTF-8"):
        table("case_id,note\nC001,x\n".encode("utf-16"), "list.csv")


def test_the_delimiter_is_taken_from_the_header_line():
    """A spreadsheet with a Dutch list separator writes semicolons; a comma in a cell of such a file is text."""
    semicolons = table(b"case_id;note\nC001;late, see the mail\n", "list.csv")
    assert semicolons.rows == [{"case_id": "C001", "note": "late, see the mail"}]
    assert table(b'case_id,note\nC001,"a; b"\n', "list.csv").rows == [{"case_id": "C001", "note": "a; b"}]


def test_cells_are_stripped_and_empty_rows_skipped_and_short_rows_padded():
    """Spaces and non-breaking spaces around a cell go; a row of delimiters only is no row; a row cut short has empty cells; the
    empty columns a spreadsheet adds behind the header are not columns. Each row keeps the number it has in the file."""
    data = "case_id ; note;;\r\n\u00a0C001 ;  first\u00a0;;\r\n;;;\r\n\r\nC002\r\n".encode("utf-8")
    read = table(data, "list.csv")
    assert read.header == ("case_id", "note")
    assert read.rows == [{"case_id": "C001", "note": "first"}, {"case_id": "C002", "note": ""}]
    assert read.numbers == [2, 5]


def test_a_cell_beyond_the_header_is_refused_with_its_row():
    with pytest.raises(Refusal, match="list.csv, row 3: a cell beyond the last column"):
        table(b"case_id,note\nC001,fine\nC002,late, see the mail\n", "list.csv")


def test_a_column_named_twice_is_refused():
    with pytest.raises(Refusal, match="list.csv names the column note twice"):
        table(b"case_id,note,note\nC001,a,b\n", "list.csv")


def test_a_quote_that_is_not_closed_is_refused_and_not_read_into_one_cell():
    with pytest.raises(Refusal, match="list.csv cannot be read as CSV near line 3: a quote that is not closed"):
        table(b'case_id,note\nC001,"late\nC002,fine\n', "list.csv")
    with pytest.raises(Refusal, match="list.csv cannot be read as CSV near line 2: .* text behind a closing quote"):
        table(b'case_id,note\nC001,"late" today\n', "list.csv")
    assert table(b'case_id,note\nC001,"said ""late"", twice\nover"\n', "list.csv").rows == [
        {"case_id": "C001", "note": 'said "late", twice\nover'}]


def test_a_file_without_its_columns_is_refused():
    with pytest.raises(Refusal, match="list.csv lacks the columns? po_number, kind"):
        table(b"case_id,note\nC001,x\n", "list.csv", columns=("case_id", "po_number", "kind"))
    with pytest.raises(Refusal, match="empty.csv is empty"):
        table(b"\n\n", "empty.csv", columns=("case_id",))


# --- reading the two exports of the timer ---

def test_the_events_export_is_read_into_one_row_per_event():
    events = parse_events(EVENTS.encode("utf-8"), "timer_events_2026-12-01-09-19.csv")
    assert len(events) == 53 and [event.event_id for event in events[:3]] == ["E1", "E2", "E3"]
    recovery = events[19]
    assert (recovery.event_id, recovery.number, recovery.case_id) == ("E20", 20, "C005")
    assert (recovery.condition, recovery.session) == ("manual", "")
    assert (recovery.event, recovery.choice, recovery.ref) == ("recovery", "pause", "")
    assert recovery.timestamp == "2026-12-01T09:07:54.500Z" and recovery.mono_ms == Decimal("1796116074500.3")
    assert recovery.page == "mx4h4wsk-9saeyk" and recovery.last_heartbeat_page == "mx4guqo0-bb5ex3"
    assert recovery.last_heartbeat_timestamp == "2026-12-01T09:05:50.000Z"
    assert recovery.last_heartbeat_mono_ms == Decimal("1796115950000.3")
    assert recovery.note == "page reloaded; time up to the last heartbeat counted, the gap after it as a pause"
    assert events[0].last_heartbeat_mono_ms is None and events[6].ref == "E6" and events[6].note == "pause clicked by mistake"


def test_the_timing_export_is_read_into_one_row_per_case():
    rows = parse_timing(TIMING.encode("utf-8"), "timing_2026-12-01-09-19.csv")
    assert [row.case_id for row in rows] == [f"C{n:03d}" for n in range(1, 13)]
    fallback = rows[3]
    assert (fallback.active_seconds, fallback.passive_seconds) == (Decimal("15.0"), Decimal("12.0"))
    assert (fallback.completed, fallback.manual_fallback) == ("no", "yes")
    assert fallback.deviations == "incomplete: the confirmation cannot be read" and fallback.started == "2026-12-01T09:04:25.500Z"


def test_an_export_without_its_columns_is_refused():
    with pytest.raises(Refusal, match="timing.csv is not an events export of the timer: it lacks the columns event_id, condition"):
        parse_events(TIMING.encode("utf-8"), "timing.csv")
    with pytest.raises(Refusal, match="events.csv is not a timing export of the timer: it lacks the columns active_seconds"):
        parse_timing(EVENTS.encode("utf-8"), "events.csv")


@pytest.mark.parametrize("event_id", ["7", "e7", "E", "E7a", "E-7", "E7.0", ""])
def test_an_event_id_that_is_not_e_and_a_whole_number_is_refused(event_id):
    data = f"{EVENTS_HEADER}\n{event_id},C001,manual,,start,2026-12-01T09:00:00.000Z,1796115600000.3,page-1,,,,,,\n".encode("utf-8")
    with pytest.raises(Refusal, match="events.csv, row 2: the event id is not E and a whole number"):
        parse_events(data, "events.csv")


@pytest.mark.parametrize("column, cell", [("mono_ms", "1,79612E+12"), ("mono_ms", ""), ("timestamp", "1-12-2026 09:00"),
                                          ("timestamp", "2026-12-01T09:00:00"), ("last_heartbeat_mono_ms", "1,79612E+12"),
                                          ("case_id", "")])
def test_a_cell_the_timer_did_not_write_is_refused_with_its_row_and_column(column, cell):
    """A spreadsheet that saves the export rewrites its numbers; the seconds cannot be worked out from such a file, so it is
    refused with what to do, and the cell itself is not repeated."""
    cells = dict(zip(EVENTS_HEADER.split(","), "E20,C005,manual,,recovery,2026-12-01T09:07:54.500Z,1796116074500.3,p2,,pause,"
                                                "2026-12-01T09:05:50.000Z,1796115950000.3,p1,".split(",")))
    cells[column] = cell
    data = (EVENTS_HEADER.replace(",", ";") + "\n" + ";".join(cells.values()) + "\n").encode("utf-8")
    with pytest.raises(Refusal) as refused:
        parse_events(data, "events.csv")
    assert str(refused.value) == (f"events.csv, row 2: {column} is not as the timer writes it: give the file as the timer "
                                  "exported it")


@pytest.mark.parametrize("column, cell", [("active_seconds", "105,5"), ("passive_seconds", ""), ("case_id", "")])
def test_a_timing_cell_the_timer_did_not_write_is_refused(column, cell):
    cells = dict(zip(TIMING_HEADER.split(","), "C001;105.5;0.0;yes;no;;2026-12-01T09:00:00.000Z".split(";")))
    cells[column] = cell
    data = (TIMING_HEADER.replace(",", ";") + "\n" + ";".join(cells.values()) + "\n").encode("utf-8")
    with pytest.raises(Refusal) as refused:
        parse_timing(data, "timing.csv")
    assert str(refused.value) == (f"timing.csv, row 2: {column} is not as the timer writes it: give the file as the timer "
                                  "exported it")


def test_several_files_are_read_in_the_order_given(tmp_path):
    first = write(tmp_path, "timer_events_a.csv", export("C001", "C002"))
    second = write(tmp_path, "timer_events_b.csv", export("C002", "C003"))
    assert [event.event_id for event in read_events([first, second])] == [f"E{n}" for n in [*range(1, 9), *range(5, 13)]]
    one = write(tmp_path, "timing_a.csv", export("C001", source=TIMING))
    two = write(tmp_path, "timing_b.csv", export("C001", "C002", source=TIMING))
    assert [row.case_id for row in read_timing([one, str(two)])] == ["C001", "C001", "C002"]


def test_a_file_that_another_program_holds_open_is_said_plainly(tmp_path, monkeypatch):
    """A spreadsheet that has the file open makes the read fail on Windows; the refusal names the file, without its folder."""
    path = write(tmp_path, "timing_a.csv", TIMING)

    def held(self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(timer_log.Path, "read_bytes", held)
    with pytest.raises(Refusal) as refused:
        read_timing([path])
    assert str(refused.value) == "timing_a.csv is open in another program (Excel?): close it and run again"


def test_a_file_that_is_not_there_is_refused_by_its_name(tmp_path):
    with pytest.raises(Refusal) as refused:
        read_events([tmp_path / "timer_events_c.csv"])
    assert str(refused.value) == "timer_events_c.csv cannot be read: it does not exist"


# --- the events of a case: merged over the exports, in the order of the log ---

def test_the_events_of_several_exports_are_merged_by_case_and_event_id():
    """An export after each session repeats every event of the sessions before: each event counts once."""
    early = [event for event in parse_events(EVENTS.encode("utf-8"), "a.csv") if event.number <= 32]
    late = parse_events(EVENTS.encode("utf-8"), "b.csv")
    merged = cases(early + late + parse_events(SECOND_STUDY.encode("utf-8"), "c.csv"))
    assert list(merged) == [f"C{n:03d}" for n in range(1, 13)]
    assert ids(merged["C008"]) == ["E31", "E32", "E33", "E34", "E35", "E36"]
    assert ids(merged["C001"]) == ["E1", "E2", "E3", "E4", "E54", "E55"]
    assert sum(len(events) for events in merged.values()) == 55


def test_the_events_of_a_case_are_ordered_by_the_number_of_the_event_id():
    """Not by the text of the id, which would put E9 behind E12, and not by the timestamp: the pause E34 of a reopened case
    carries the time of the stop it replaces and stands behind the undo E33 all the same, as in the timer's own log."""
    backwards = list(reversed(parse_events(EVENTS.encode("utf-8"), "a.csv")))
    merged = cases(backwards)
    assert ids(merged["C003"]) == ["E9", "E10", "E11", "E12"]
    assert ids(merged["C008"]) == ["E31", "E32", "E33", "E34", "E35", "E36"]
    assert merged["C008"][3].timestamp < merged["C008"][2].timestamp
    assert list(merged) == [f"C{n:03d}" for n in range(1, 13)]


def test_the_same_event_id_with_other_content_stays_in_the_log_twice():
    """Two exports that disagree about one event are not one study; both rows are kept, for check() to name."""
    one, other = parse_events(export("C011"), "a.csv"), c011_with_another_stop()
    merged = cases(one + other)["C011"]
    assert ids(merged) == ["E48", "E49", "E49"] and merged[1] == one[1] and merged[2] == other[1]


# --- the effective events and the state, as the timer has them ---

def test_an_undo_and_the_event_it_names_are_not_effective():
    assert ids(effective(log("C002"))) == ["E5", "E8"]
    assert ids(effective(log("C008"))) == ["E31", "E34", "E35", "E36"]
    assert ids(effective(log("C009"))) == ["E37", "E40", "E41"]
    assert effective(log("C001")) == log("C001") and effective([]) == []


@pytest.mark.parametrize("name", TIMER)
def test_the_state_of_a_case_is_the_timers_own(name):
    assert state(log(name)) == TIMER[name][3]


def test_the_state_follows_the_recovery_choice():
    """A recovery counted as a pause leaves the case paused; one counted as work leaves it as it was, waiting too."""
    assert state(log("C005")[:2]) == "paused" and state(log("C006")[:2]) == "active" and state(log("C007")[:3]) == "waiting"
    assert state([]) == "idle" and state(log("C004")[:3]) == "paused" and state(log("C004")[:5]) == "active"


# --- the seconds worked out again ---

@pytest.mark.parametrize("name", TIMER)
def test_the_seconds_worked_out_again_are_the_timers_own_for_every_sequence(name):
    active, passive, flags, _ = TIMER[name]
    worked_out = seconds(log(name))
    assert (tenth(worked_out.active), tenth(worked_out.passive), list(worked_out.flags)) == (active, passive, flags)


def test_work_counts_and_a_pause_does_not():
    assert seconds(log("C001")).active == Decimal("105.5") and seconds(log("C001")).passive == 0


def test_a_pause_that_was_undone_counts_as_work():
    assert seconds(log("C002")).active == Decimal("25")


def test_waiting_for_the_aid_is_passive_time_also_when_a_pause_ends_it():
    assert (seconds(log("C003")).active, seconds(log("C003")).passive) == (Decimal("25"), Decimal("20"))
    assert (seconds(log("C004")).active, seconds(log("C004")).passive) == (Decimal("15"), Decimal("12"))


def test_a_reload_counted_as_a_pause_counts_the_work_up_to_the_last_heartbeat():
    """Of the 12 s before the reload the 7.5 s up to the last heartbeat count, by the monotonic value of the old page; the gap
    and the pause after it do not; then 15 s of work on the new page."""
    assert seconds(log("C005")).active == Decimal("22.5")


def test_a_reload_counted_as_work_counts_the_gap_by_the_clock_of_the_pc():
    """The monotonic values of two page loads do not compare: across the reload the timestamps give the 40 s."""
    assert seconds(log("C006")).active == Decimal("45")


def test_a_reload_while_waiting_counts_the_gap_as_passive_time():
    assert (seconds(log("C007")).active, seconds(log("C007")).passive) == (Decimal("15"), Decimal("67"))


def test_a_case_reopened_as_a_pause_keeps_its_work_and_adds_what_follows_the_resume():
    assert seconds(log("C008 before it was reopened")).active == Decimal("10") and seconds(log("C008")).active == Decimal("30")


def test_a_case_reopened_as_work_counts_the_time_since_the_cancelled_stop():
    assert seconds(log("C009")).active == Decimal("180")


def test_a_negative_interval_counts_as_zero_and_is_named():
    """C010: the last heartbeat is older than the last click, so nothing counts between them (the 2 s of work before the reload
    are lost). C013: the resume stands before its pause by the timer's own clock."""
    assert seconds(log("C010")).active == Decimal("22") and seconds(log("C010")).flags == ("negative interval before E45",)
    assert seconds(log("C013")).active == Decimal("13") and seconds(log("C013")).flags == ("negative interval before E58",)


def test_a_tie_at_a_tenth_of_a_second_is_rounded_as_the_timer_rounds_it():
    """12.25 s is "12.3" in the timer's export, where Python's own formatting gives 12.2."""
    assert seconds(log("C011")).active == Decimal("12.25") and tenth(seconds(log("C011")).active) == "12.3"
    assert f"{12.25:.1f}" == "12.2" and tenth(Decimal("0")) == "0.0" and tenth(Decimal("105.5")) == "105.5"


def test_the_monotonic_values_of_two_page_loads_are_never_compared():
    """C014: by the clock of the PC the reload took 40 s, by the monotonic values 37 s; the timer counts the 40."""
    assert seconds(log("C014")).active == Decimal("45")


def test_no_interval_is_read_after_a_stop():
    """C015: the second start stands before the first stop by the timer's clock; the timer reads no interval there, so none is
    negative."""
    assert seconds(log("C015")).active == Decimal("25") and seconds(log("C015")).flags == ()


def test_a_case_that_still_runs_has_the_seconds_up_to_its_last_event():
    assert seconds(log("C012 while it waits for the aid")).active == Decimal("10") and seconds([]).active == 0


# --- the check of one case: criterion 1 (the log is complete) and, apart from it, whether the log is consistent ---

COMPLETE_CASES = {   # the condition, the events, the pauses and the waits (each: how many, how many closed), consistent
    "C001": ("manual", 4, (1, 1), (0, 0), True),
    "C002": ("manual", 4, (0, 0), (0, 0), True),          # the pause that was undone is no pause of the log
    "C003": ("assisted", 4, (0, 0), (1, 1), True),
    "C004": ("assisted", 6, (1, 1), (1, 1), True),        # the wait is closed by the pause that ends it
    "C005": ("manual", 4, (1, 1), (0, 0), True),          # the recovery counted as a pause is a pause
    "C006": ("manual", 3, (0, 0), (0, 0), True),
    "C007": ("assisted", 5, (0, 0), (1, 1), True),
    "C008": ("manual", 6, (1, 1), (0, 0), True),
    "C009": ("manual", 5, (0, 0), (0, 0), True),
    "C010": ("manual", 6, (2, 2), (0, 0), False),         # a negative interval
    "C011": ("manual", 2, (0, 0), (0, 0), True),
    "C012": ("assisted", 4, (0, 0), (1, 1), True),
}


@pytest.mark.parametrize("case_id", COMPLETE_CASES)
def test_every_case_of_the_export_has_a_complete_log(case_id):
    condition, events, pauses, waits, consistent = COMPLETE_CASES[case_id]
    checked = check(log(case_id), timing(case_id))
    assert (checked.case_id, checked.condition, checked.conditions, checked.events) == (case_id, condition, (condition,), events)
    assert (checked.log_complete, checked.start, checked.stop) == (True, True, True)
    assert (checked.pauses, checked.pauses_closed) == pauses and (checked.waits, checked.waits_closed) == waits
    assert checked.consistent is consistent and checked.one_record and checked.legal_steps and checked.timing_agrees
    assert checked.no_negative_interval is consistent and checked.time_order
    assert checked.timing == timing(case_id)[0] and checked.superseded == 0 and checked.notes == ()
    assert (checked.problems == ()) is consistent
    assert tenth(checked.seconds.active) == TIMER[case_id][0] and tenth(checked.seconds.passive) == TIMER[case_id][1]


def test_the_check_says_what_the_log_holds():
    """What the rows of the per-case log are written from: the effective events, the first start, the stop and how it ended."""
    checked = check(log("C004"), timing("C004"))
    assert ids(checked.effective) == ["E13", "E14", "E15", "E16", "E17", "E18"]
    assert (checked.started, checked.stopped) == ("2026-12-01T09:04:25.500Z", "2026-12-01T09:05:22.500Z")
    assert (checked.status, checked.fallback) == ("incomplete", True)
    reopened = check(log("C008"), timing("C008"))
    assert ids(reopened.effective) == ["E31", "E34", "E35", "E36"] and reopened.events == 6
    assert (reopened.started, reopened.stopped, reopened.status, reopened.fallback) == (
        "2026-12-01T09:11:19.500Z", "2026-12-01T09:12:44.500Z", "complete", False)


def test_an_export_taken_while_the_case_runs_is_named_as_the_cause():
    """The events export holds a running case and the timing export does not: no stop, an open wait, no timing row."""
    checked = check(log("C012 while it waits for the aid"), [])
    assert (checked.log_complete, checked.start, checked.stop) == (False, True, False)
    assert (checked.waits, checked.waits_closed, checked.pauses, checked.pauses_closed) == (1, 0, 0, 0)
    assert checked.consistent is False and checked.timing_agrees is False and checked.legal_steps and checked.timing is None
    assert checked.problems == (RUNNING,)
    assert RUNNING == "the events export was taken while this case was running: export both files again after the stop"
    assert (checked.started, checked.stopped, checked.status) == ("2026-12-01T09:18:26.750Z", "", "")


def test_a_case_paused_when_the_export_was_taken_has_an_open_pause():
    checked = check(log("C004")[:3], [])
    assert (checked.log_complete, checked.stop, checked.pauses, checked.pauses_closed) == (False, False, 1, 0)
    assert (checked.waits, checked.waits_closed) == (1, 1) and checked.problems == (RUNNING,)


def test_a_case_without_a_pause_is_complete():
    assert check(log("C011"), timing("C011")).log_complete is True and check(log("C011"), timing("C011")).pauses == 0


def test_a_complete_log_stays_complete_when_it_is_not_consistent():
    """Criterion 1 is start, pauses and stop, and nothing else: a timing row that does not agree, or none, leaves it as it is."""
    for rows in ([], row_for(log("C001"), active_seconds=Decimal("99.0"))):
        checked = check(log("C001"), rows)
        assert checked.log_complete is True and checked.consistent is False and checked.problems != ()


# the timing row

def test_a_timing_row_agrees_within_a_tenth_of_a_second():
    """The row has one decimal and the events a tenth of a millisecond: 12.25 s worked out agrees with 12.3 and with 12.2 in the
    row, and a tenth beside the figure worked out still agrees; two tenths do not."""
    assert check(log("C011"), row_for(log("C011"), active_seconds=Decimal("12.2"))).consistent is True
    assert check(log("C001"), row_for(log("C001"), active_seconds=Decimal("105.6"))).consistent is True
    assert check(log("C001"), row_for(log("C001"), active_seconds=Decimal("105.4"))).consistent is True
    off = check(log("C001"), row_for(log("C001"), active_seconds=Decimal("105.7")))
    assert (off.consistent, off.timing_agrees, off.timing) == (False, False, None)
    assert off.problems == ("the timing row does not agree with the events in the active seconds: the two files are not exports "
                            "of the same moment: export both files again",)


def test_a_timing_row_must_agree_in_completed_fallback_and_both_seconds():
    for cells, named in (({"passive_seconds": Decimal("11.5")}, "the passive seconds"), ({"completed": "yes"}, "completed"),
                         ({"manual_fallback": "no"}, "manual_fallback"),
                         ({"completed": "yes", "active_seconds": Decimal("9")}, "the active seconds, completed")):
        checked = check(log("C004"), row_for(log("C004"), **cells))
        assert checked.consistent is False and checked.log_complete is True
        assert checked.problems == (f"the timing row does not agree with the events in {named}: the two files are not exports of "
                                    "the same moment: export both files again",)


def test_a_case_with_a_stop_and_no_timing_row_says_so():
    checked = check(log("C001"), [])
    assert (checked.log_complete, checked.consistent, checked.timing) == (True, False, None)
    assert checked.problems == ("no timing row for this case: the timing export was taken before its stop, or is of another study: "
                                "export both files again",)


def test_the_same_timing_row_in_two_exports_is_one_row():
    checked = check(log("C003"), timing("C003", TIMING, TIMING))
    assert (checked.consistent, checked.superseded, checked.notes, checked.timing) == (True, 0, (), timing("C003")[0])


@pytest.mark.parametrize("exports", [(TIMING_BEFORE_REOPENING, TIMING), (TIMING, TIMING_BEFORE_REOPENING)])
def test_of_two_timing_rows_of_a_reopened_case_the_one_that_agrees_with_the_events_counts(exports):
    """The case was reopened between two exports: the row of the first stop is superseded, which is said and is no problem."""
    checked = check(log("C008"), timing("C008", *exports))
    assert (checked.consistent, checked.timing_agrees, checked.problems) == (True, True, ())
    assert checked.timing.active_seconds == Decimal("30.0") and checked.timing.completed == "yes"
    assert checked.superseded == 1 and checked.notes == ("1 timing row superseded by a later export",)


def test_two_timing_rows_of_which_none_agrees_are_a_problem():
    """The events export is the old one (the case stopped as incomplete after 10 s), the two timing rows are of later exports."""
    later = timing("C008") + row_for(log("C008"), active_seconds=Decimal("31.5"))
    checked = check(log("C008 before it was reopened"), later)
    assert (checked.log_complete, checked.consistent, checked.timing, checked.superseded) == (True, False, None, 0)
    assert checked.problems == ("none of the 2 timing rows agrees with the events: the files are not exports of the same moment: "
                                "export both files again",)


def test_a_case_reopened_and_running_when_the_events_were_exported_is_not_matched_with_its_old_row():
    reopened = log("C008")[:5]                                          # up to the resume after the reopening
    checked = check(reopened, timing("C008", TIMING_BEFORE_REOPENING))
    assert (checked.log_complete, checked.stop, checked.consistent, checked.timing) == (False, False, False, None)
    assert checked.problems == (RUNNING,)


# two studies under one id

def test_the_same_id_handled_again_after_a_reset_is_named_as_two_handlings():
    """Two starts with a stop between them: the seconds of the merged log are those of neither handling, and neither timing row
    agrees. The cause is said once."""
    rows = timing("C001") + timing("C001", TIMING_SECOND_STUDY)
    checked = check(log("C001 of two studies"), rows)
    assert (checked.log_complete, checked.start, checked.stop) == (False, False, True)
    assert (checked.consistent, checked.legal_steps, checked.one_record, checked.timing_agrees) == (False, False, False, False)
    assert checked.conditions == ("manual", "assisted") and checked.condition == "manual"
    assert TWO_HANDLINGS == "two handlings under one id: give the exports of one study only"
    assert checked.problems == (TWO_HANDLINGS, "more than one condition in the events of this case",
                                "none of the 2 timing rows agrees with the events: the files are not exports of the same moment: "
                                "export both files again")


def test_an_event_id_with_two_contents_is_a_problem_of_the_case_and_the_first_content_is_read():
    one, other = parse_events(export("C011"), "a.csv"), c011_with_another_stop()
    checked = check(cases(one + other)["C011"], timing("C011"))
    assert (checked.log_complete, checked.consistent, checked.one_record, checked.legal_steps) == (True, False, False, True)
    assert checked.problems == ("E49 stands in the exports with two contents: give the exports of one study only",)
    assert checked.seconds.active == Decimal("12.25") and checked.events == 2 and checked.timing_agrees


# the steps the timer allows

def legal(checked) -> tuple:
    return checked.legal_steps, checked.problems


def test_a_step_the_timers_buttons_do_not_allow_is_named_with_its_event():
    """The enable rules of the timer's buttons: pause from active or waiting, resume from paused, the end of a wait from waiting,
    a stop from active only."""
    resumed = made("manual", ("start", 0), ("resume", 5), ("complete", 9))
    assert legal(check(resumed, row_for(resumed))) == (False, ("E102: resume is not a legal step while the case is active",))
    paused_twice = made("manual", ("start", 0), ("pause", 5), ("pause", 6), ("resume", 8), ("complete", 9))
    assert legal(check(paused_twice, row_for(paused_twice))) == (
        False, ("E103: pause is not a legal step while the case is paused",))
    no_wait = made("assisted", ("start", 0), ("wait_end", 5), ("complete", 9))
    assert legal(check(no_wait, row_for(no_wait))) == (False, ("E102: wait_end is not a legal step while the case is active",))
    after = made("manual", ("start", 0), ("complete", 5), ("pause", 9))
    assert check(after, []).problems == (RUNNING, "E103: pause is not a legal step after the stop")
    twice = made("manual", ("start", 0), ("start", 5), ("complete", 9))
    assert check(twice, row_for(twice)).problems == ("more than one start in the log",
                                                     "E102: start is not a legal step while the case is active")
    before = made("manual", ("pause", 0), ("start", 5), ("complete", 9))
    checked = check(before, row_for(before))
    assert (checked.log_complete, checked.start, checked.legal_steps) == (False, False, False)
    assert checked.problems == ("the log does not begin with a start", "1 pause without a resume before the stop",
                                "E101: pause is not a legal step before the start",
                                "E102: start is not a legal step while the case is paused")


def test_a_stop_while_paused_leaves_the_pause_open_and_the_log_incomplete():
    stopped = made("manual", ("start", 0), ("pause", 5), ("complete", 9))
    checked = check(stopped, row_for(stopped))
    assert (checked.log_complete, checked.start, checked.stop, checked.pauses, checked.pauses_closed) == (False, True, True, 1, 0)
    assert checked.problems == ("1 pause without a resume before the stop",
                                "E103: complete is not a legal step while the case is paused")
    late = check(made("manual", ("start", 0), ("pause", 5), ("complete", 9), ("resume", 12)), [])
    assert (late.pauses, late.pauses_closed, late.stop) == (1, 0, False)        # a resume after the stop closes no pause


def test_a_stop_while_waiting_is_not_legal_and_the_log_is_complete_all_the_same():
    """Criterion 1 names the pauses, not the waits: the open wait is counted and the step is named under consistency."""
    for stop in ("complete", "incomplete"):
        stopped = made("assisted", ("start", 0), ("wait", 5), (stop, 9))
        checked = check(stopped, row_for(stopped))
        assert (checked.log_complete, checked.waits, checked.waits_closed, checked.consistent) == (True, 1, 0, False)
        assert checked.problems == (f"E103: {stop} is not a legal step while the case is waiting",)


def test_a_wait_and_a_fallback_are_steps_of_an_assisted_case_only_and_a_fallback_comes_once():
    waited = made("manual", ("start", 0), ("wait", 5), ("wait_end", 7), ("fallback", 8), ("complete", 9))
    assert legal(check(waited, row_for(waited))) == (False, ("E102: wait is only a step of an assisted case",
                                                             "E104: fallback is only a step of an assisted case"))
    twice = made("assisted", ("start", 0), ("fallback", 5), ("fallback", 7), ("complete", 9))
    assert legal(check(twice, row_for(twice))) == (False, ("E103: a second fallback",))
    undone = made("assisted", ("start", 0), ("fallback", 5), ("undo", 6, {"ref": "E102"}), ("fallback", 7), ("complete", 9))
    assert legal(check(undone, row_for(undone))) == (True, ())
    paused = made("assisted", ("start", 0), ("pause", 5), ("fallback", 6), ("resume", 7), ("complete", 9))
    assert legal(check(paused, row_for(paused))) == (False, ("E103: fallback is not a legal step while the case is paused",))


def test_a_recovery_is_legal_while_the_case_runs_and_needs_its_choice():
    """The timer asks for a recovery choice only when the page closed on an active or waiting case."""
    paused = made("manual", ("start", 0), ("pause", 5), ("recovery", 60, {"choice": "pause", "page": "page-2"}),
                  ("resume", 70, {"page": "page-2"}), ("complete", 80, {"page": "page-2"}))
    assert legal(check(paused, row_for(paused))) == (False, ("E103: recovery is not a legal step while the case is paused",))
    no_choice = made("manual", ("start", 0), ("recovery", 60, {"page": "page-2"}), ("complete", 80, {"page": "page-2"}))
    assert legal(check(no_choice, row_for(no_choice))) == (False, ("E102: a recovery without the choice pause or work",))


def test_an_event_the_timer_does_not_know_and_an_undo_of_nothing_are_named_without_their_text():
    unknown = made("manual", ("start", 0), ("lunch at C020 Street", 5), ("complete", 9))
    assert legal(check(unknown, row_for(unknown))) == (False, ("E102 is not an event of the timer",))
    undo = made("manual", ("start", 0), ("undo", 5, {"ref": "E7"}), ("complete", 9))
    assert legal(check(undo, row_for(undo))) == (False, ("E102 undoes an event that is not in the log of this case",))


def test_a_condition_that_is_neither_manual_nor_assisted_is_a_problem():
    other = made("with the aid", ("start", 0), ("complete", 9))
    checked = check(other, row_for(other))
    assert (checked.log_complete, checked.consistent, checked.one_record) == (True, False, False)
    assert checked.problems == ("the condition of this case is neither manual nor assisted",)


# the order of time

def test_a_negative_interval_is_named_with_its_cause():
    """By the timer's own clock (C013), or because the last heartbeat before a reload is older than the last click (C010): the
    timer counts nothing there, and the order of the events is the order of time all the same."""
    clock = check(log("C013"), timing("C013", TIMING_HAND_MADE))
    assert (clock.log_complete, clock.consistent, clock.time_order, clock.no_negative_interval, clock.timing_agrees) == (
        True, False, False, False, True)
    assert clock.problems == ("negative interval before E58: E58 is earlier than E57 by the timer's clock; the timer counts it "
                              "as zero",)
    heartbeat = check(log("C010"), timing("C010"))
    assert (heartbeat.log_complete, heartbeat.consistent) == (True, False)
    assert (heartbeat.time_order, heartbeat.no_negative_interval, heartbeat.timing_agrees) == (True, False, True)
    assert heartbeat.problems == ("negative interval before E45: the last heartbeat before the reload is older than E44; the timer "
                                  "counts nothing between them",)


def test_events_out_of_the_order_of_time_are_named_also_where_the_timer_counts_nothing():
    """Across a reload the timer compares no clock unless the gap counts as work; the check reads the clock of the PC there,
    whatever the monotonic values of the two page loads say."""
    back = made("manual", ("start", 0), ("pause", 50), ("resume", 20, {"page": "page-2", "mono_ms": "1796205699000.0"}),
                ("complete", 30, {"page": "page-2", "mono_ms": "1796205709000.0"}))
    checked = check(back, row_for(back))
    assert (checked.log_complete, checked.consistent) == (True, False)
    assert (checked.time_order, checked.no_negative_interval) == (False, True)
    assert checked.problems == ("E103 is earlier than E102 by the clock of the PC",)


def test_the_order_of_time_is_read_as_the_timer_reads_it():
    """Within one page load the monotonic value decides, also when the clock of the PC was set back meanwhile; across a reload
    the clock of the PC decides, also when the new page's monotonic values lie behind those of the old one."""
    set_back = made("manual", ("start", 0), ("pause", 50, {"timestamp": "2026-12-02T09:59:00.000Z"}), ("resume", 60),
                    ("complete", 70))
    checked = check(set_back, row_for(set_back))
    assert (checked.time_order, checked.consistent, checked.problems) == (True, True, ())
    reloaded = made("manual", ("start", 0), ("pause", 50), ("resume", 60, {"page": "page-2", "mono_ms": "1796205000000.0"}),
                    ("complete", 70, {"page": "page-2", "mono_ms": "1796205010000.0"}))
    checked = check(reloaded, row_for(reloaded))
    assert (checked.time_order, checked.consistent, checked.problems) == (True, True, ())


def test_a_second_start_before_the_first_stop_is_two_handlings_out_of_the_order_of_time():
    checked = check(log("C015"), row_for(log("C015")))
    assert (checked.log_complete, checked.start, checked.time_order, checked.no_negative_interval) == (False, False, False, True)
    assert checked.problems == (TWO_HANDLINGS, "E65 is earlier than E64 by the timer's clock")


# no events, and what no sentence may hold

def test_a_case_with_a_timing_row_and_no_events_is_not_complete():
    checked = check([], timing("C001"))
    assert (checked.case_id, checked.condition, checked.conditions, checked.events) == ("C001", "", (), 0)
    assert (checked.log_complete, checked.start, checked.stop) == (False, False, False)
    assert (checked.consistent, checked.timing) == (False, None)
    assert checked.problems == ("no events for this case: give the events export that holds it",)
    assert (checked.started, checked.stopped, checked.status, checked.seconds.active) == ("", "", "", 0)


def test_no_sentence_of_the_check_holds_a_time_or_seconds():
    """The check goes to the terminal and to a file that holds no seconds: its sentences name events by their id and nothing
    that was measured."""
    other = timing("C008", TIMING_BEFORE_REOPENING)
    checks = [check(log(name), rows) for name in TIMER for rows in ([], other, timing(SEQUENCES[name][0]))]
    sentences = [sentence for checked in checks for sentence in checked.problems + checked.notes]
    assert len(set(sentences)) >= 6
    for sentence in sentences:
        assert not re.search(r"[0-9][.:][0-9]|2026", sentence), sentence


# --- the timer itself, asked again ---

# Loads the script of timer.html as evaluation/test_timer_state.js does, without a browser, makes the timer's own case objects
# of the rows it is given (the export turned back) and asks the timer's summarise(), state() and effective() about each.
ASK_THE_TIMER = r"""
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const elements = new Map();
const element = () => ({ value: '', textContent: '', innerHTML: '', disabled: false, classList: { toggle() {} } });
const context = vm.createContext({ console, Date, Math, Set, Map, JSON, performance, setInterval: () => 1, setTimeout: () => 1,
  document: { getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); } },
  window: { addEventListener() {} }, localStorage: { getItem: () => null, setItem() {} },
  prompt: () => null, confirm: () => false, alert() {} });
vm.runInContext(source, context);
const answers = {};
for (const [name, rows] of Object.entries(JSON.parse(fs.readFileSync(0, 'utf8')))) {
  context.c = { case_id: name, condition: rows[0].condition, events: rows.map(r => ({
    id: r.event_id, event: r.event, timestamp: r.timestamp, mono: Number(r.mono_ms), page: r.page, ref: r.ref, choice: r.choice,
    alive: r.last_heartbeat_page ? { timestamp: r.last_heartbeat_timestamp, mono: Number(r.last_heartbeat_mono_ms),
                                     page: r.last_heartbeat_page } : null, note: r.note })) };
  const s = vm.runInContext('summarise(c)', context);
  answers[name] = { active: s.active, passive: s.passive, flags: Array.from(s.flags), state: vm.runInContext('state(c)', context),
                    effective: Array.from(vm.runInContext('effective(c).map(e => e.id)', context)) };
}
process.stdout.write(JSON.stringify(answers));
"""


def test_the_timer_itself_gives_the_seconds_written_down_here_for_the_same_rows():
    """The figures of TIMER are the timer's and not this module's: evaluation/timer.html is run in node on the rows of every
    sequence, and its summarise(), state() and effective() must give what stands in TIMER and what this module works out.
    Skipped where node or the file is absent: the aid's own repository holds no evaluation/timer.html."""
    node, timer = shutil.which("node"), REPO_ROOT / "evaluation" / "timer.html"
    if node is None or not timer.is_file():
        pytest.skip("needs node and evaluation/timer.html")
    rows = {name: [{column: "" if cell is None else str(cell) for column, cell in asdict(event).items()} for event in log(name)]
            for name in TIMER}
    asked = subprocess.run([node, "-e", ASK_THE_TIMER, str(timer)], input=json.dumps(rows), capture_output=True, encoding="utf-8",
                           timeout=120, check=True)
    answers = json.loads(asked.stdout)
    assert list(answers) == list(TIMER)
    for name, answer in answers.items():
        assert (answer["active"], answer["passive"], answer["flags"], answer["state"]) == TIMER[name], name
        worked_out = seconds(log(name))
        assert (tenth(worked_out.active), tenth(worked_out.passive), list(worked_out.flags)) == (
            answer["active"], answer["passive"], answer["flags"]), name
        assert state(log(name)) == answer["state"] and ids(effective(log(name))) == answer["effective"], name
