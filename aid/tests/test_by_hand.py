"""Tests of the list of the changes the buyer enters in Exact (aid/execution/by_hand.py): one change per run of the prepared rows of one
place, its state by the marks, the marks themselves, and the lines of either file that cannot be read. The rows are prepared by the
prepared writer itself (aid/execution/writer.py), as a case or a price list prepares them."""
import json
import os
import pathlib
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from aid.case import open_case
from aid.comparison import net_unit_price
from aid.execution.by_hand import (LINE_KINDS, MARKS_FILE, ListUnread, as_entered, changes, contradict, entered_values, filed_of, held_now,
                                   item_code, link_put_back, mark, place_of, withdraw,
                                   note_value, open_changes, put_back_of, put_back_state, reconcile, reopen, shown_now)
from aid.execution.ops import WriteOp
from aid.execution.writer import PREPARED_FILE, UNDO_RULE_ID, PreparedWriter, WriteLog, undo_entry
from aid.madeup.generate import BOLD, Row, Text, pdf_bytes
from aid.po_store import PoStore, PoVersion, po_total
from aid.schema import DocLine, DocValue, Document, SourceRef
from aid.tests.fakes import line
from aid.tryout_mark import FIRST_ROW

NOW = datetime(2026, 11, 18, 10, 32, 5)
AT = "2026-11-18T10:32:05"                          # NOW as a mark writes it


def price(new: str, old: str | None = "12.50", po: str = "250108", line: str = "1", version: int | None = 1) -> WriteOp:
    """A change of the price of a PO line, as rule R04 or a close prepares it."""
    return WriteOp(kind="po_line_price", po_number=po, version=version, line_id=line, old_value=old, new_value=new)


def quantity(new: str, old: str = "3", po: str = "250108", line: str = "1") -> WriteOp:
    return WriteOp(kind="po_line_quantity", po_number=po, version=1, line_id=line, old_value=old, new_value=new)


def date(new: str, old: str = "2026-11-16", po: str = "250108", line: str = "1") -> WriteOp:
    return WriteOp(kind="po_line_date", po_number=po, version=1, line_id=line, old_value=old, new_value=new)


def article(new: str, old: str | None = None, code: str = "KM-0801", po: str = "250108") -> WriteOp:
    """A change of an article's purchase price: a case of a PO asks it without the old price (the aid never read it), a price list
    with po "" and no version."""
    return WriteOp(kind="article_price", po_number=po, version=1 if po else None, article_code=code, old_value=old, new_value=new)


def asked(folder, case_id: str, *ops: WriteOp, rule_id: str = "R04", now: datetime = NOW) -> list[str]:
    """The operations prepared into the folder by the prepared writer, as the case with this id prepares them at now: their entry
    ids."""
    writer = PreparedWriter(folder)
    return [writer.apply(op, case_id, rule_id, {}, now).entry_id for op in ops]


def states(folder) -> list[tuple[list[str], str]]:
    """Each change of the folder in its order, by its entry ids and its state; no line may have been passed over."""
    found, skipped = changes(folder)
    assert skipped == 0
    return [(change.ids, change.state) for change in found]


def case(day: int, hour: int = 9) -> str:
    """The id of a made-up case of PO 250108 opened on that day of November 2026."""
    return f"250108_202611{day:02d}T{hour:02d}0000"


def test_the_same_change_from_two_cases_is_one_row(tmp_path):
    first = asked(tmp_path, case(18), price("12.70"), article("12.70"))
    second = asked(tmp_path, case(19), price("12.70"), article("12.70"))
    found, skipped = changes(tmp_path)
    assert skipped == 0 and [change.ids for change in found] == [[first[0], second[0]], [first[1], second[1]]]
    line, item = found
    assert (line.kind, line.po_number, line.version, line.line_id, line.article_code) == ("po_line_price", "250108", 1, "1", None)
    assert (line.old_value, line.new_value, line.rule_id, line.state, line.prepared_at) == ("12.50", "12.70", "R04", "open", AT)
    assert line.case_ids == [case(18), case(19)] and (line.marked_at, line.marked_by) == (None, None)
    assert (item.kind, item.article_code, item.old_value, item.new_value, item.case_ids) == ("article_price", "KM-0801", None, "12.70",
                                                                                              [case(18), case(19)])
    assert open_changes(tmp_path) == found
    assert changes(tmp_path / "nothing prepared yet") == ([], 0) and open_changes(tmp_path / "nothing prepared yet") == []


def test_a_ticked_change_is_not_asked_again(tmp_path):
    """A change ticked as entered is done: asked again by a later case it stays done, and when another value came and was dropped, the
    value the marks say Exact holds is not asked again either."""
    first = asked(tmp_path, case(18), price("12.70"))
    assert mark(tmp_path, first, "entered", NOW) == 1 and open_changes(tmp_path) == []
    [entered] = changes(tmp_path)[0]
    assert (entered.state, entered.marked_at, entered.marked_by) == ("entered", AT, "buyer")
    again = asked(tmp_path, case(19), price("12.70"))                  # a second confirmation asks the same price: the same change
    assert states(tmp_path) == [(first + again, "entered")] and open_changes(tmp_path) == []
    other = asked(tmp_path, case(20), price("12.90"))                  # another value, which the buyer drops
    assert mark(tmp_path, other, "dropped", NOW) == 1
    back = asked(tmp_path, case(21), price("12.70"))                   # the entered value asked once more: Exact holds it
    assert states(tmp_path) == [(first + again, "entered"), (other, "dropped"), (back, "already")] and open_changes(tmp_path) == []


def test_a_value_that_returns_after_another_was_entered_is_asked_again(tmp_path):
    first = asked(tmp_path, case(18), price("12.70"))
    mark(tmp_path, first, "entered", NOW)
    back = asked(tmp_path, case(19), price("12.50", old="12.70"))      # the old price again, entered as well
    mark(tmp_path, back, "entered", NOW)
    again = asked(tmp_path, case(20), price("12.70"))                  # Exact holds 12.50 by the marks, so 12.70 is asked again
    assert states(tmp_path) == [(first, "entered"), (back, "entered"), (again, "open")]
    assert [change.ids for change in open_changes(tmp_path)] == [again]
    # The same rows without the second tick: 12.50 was replaced before it was entered, and 12.70 is in Exact by the first tick.
    other = tmp_path / "other"
    first = asked(other, case(18), price("12.70"))
    mark(other, first, "entered", NOW)
    back, again = asked(other, case(19), price("12.50", old="12.70")), asked(other, case(20), price("12.70"))
    assert states(other) == [(first, "entered"), (back, "replaced"), (again, "already")] and open_changes(other) == []


def test_an_open_change_is_replaced_by_a_newer_value(tmp_path):
    older = asked(tmp_path, case(18), price("12.70"))
    newer = asked(tmp_path, case(19), price("12.90"))                  # a newer confirmation asks another price
    assert states(tmp_path) == [(older, "replaced"), (newer, "open")]
    [shown] = open_changes(tmp_path)
    assert (shown.ids, shown.old_value, shown.new_value, shown.case_ids) == (newer, "12.50", "12.90", [case(19)])
    assert mark(tmp_path, older, "entered", NOW) == 0 and not (tmp_path / MARKS_FILE).exists()      # a replaced change is no tick
    assert mark(tmp_path, newer, "entered", NOW) == 1
    assert states(tmp_path) == [(older, "replaced"), (newer, "entered")]          # the older value never comes back by itself


def test_a_dropped_change_stays_dropped_for_the_same_value(tmp_path):
    first = asked(tmp_path, case(18), price("12.70"))
    assert mark(tmp_path, first, "dropped", NOW) == 1
    [dropped] = changes(tmp_path)[0]
    assert (dropped.state, dropped.marked_at, dropped.marked_by) == ("dropped", AT, "buyer")
    again = asked(tmp_path, case(19), price("12.70"))                  # asked again by a later case with the same value
    assert states(tmp_path) == [(first + again, "dropped")] and open_changes(tmp_path) == []
    other = asked(tmp_path, case(20), price("12.90"))                  # another value is asked
    assert states(tmp_path) == [(first + again, "dropped"), (other, "open")]


def test_reopened_takes_the_marks_away(tmp_path):
    first = asked(tmp_path, case(18), price("12.70"))
    mark(tmp_path, first, "entered", NOW)
    assert mark(tmp_path, first, "reopened", NOW) == 1 and states(tmp_path) == [(first, "open")]
    mark(tmp_path, first, "dropped", NOW)
    mark(tmp_path, first, "reopened", NOW)
    assert states(tmp_path) == [(first, "open")]
    mark(tmp_path, first, "entered", NOW)                              # the last mark of an entry counts
    assert states(tmp_path) == [(first, "entered")]
    # a run of two rows, both ticked: one reopened leaves the other's tick, both reopened open the change again
    other = tmp_path / "other"
    ids = asked(other, case(18), price("12.70")) + asked(other, case(19), price("12.70"))
    assert mark(other, ids, "entered", NOW) == 2
    mark(other, ids[:1], "reopened", NOW)
    assert states(other) == [(ids, "entered")]
    mark(other, ids[1:], "reopened", NOW)
    assert states(other) == [(ids, "open")]


def test_three_captures_of_one_cart_give_one_row_per_change(tmp_path):
    """Three captures of one cart page, each a case of its own that prepares the same small price difference on the PO line and on
    the article: two changes to enter, not six."""
    cases = [f"250201_20261118T09{minute:02d}00" for minute in (0, 1, 2)]
    ids = [asked(tmp_path, one, price("0.96", old="0.95", po="250201"), article("0.96", code="KM-2101", po="250201")) for one in cases]
    found, skipped = changes(tmp_path)
    assert skipped == 0 and [(change.kind, change.ids, change.case_ids, change.state) for change in found] == [
        ("po_line_price", [one[0] for one in ids], cases, "open"), ("article_price", [one[1] for one in ids], cases, "open")]


def test_an_article_price_asked_by_two_orders_is_one_row(tmp_path):
    one = asked(tmp_path, "250108_20261118T090000", article("12.70", po="250108"))
    two = asked(tmp_path, "250111_20261118T100000", article("12.70", po="250111"))
    listed = asked(tmp_path, "pricelist_S08_20261118T120000", article("12.70", old="12.50", po=""), rule_id="P01")    # a price list
    [change] = changes(tmp_path)[0]
    assert change.ids == one + two + listed and change.state == "open"
    assert change.case_ids == ["250108_20261118T090000", "250111_20261118T100000", "pricelist_S08_20261118T120000"]
    assert (change.old_value, change.new_value, change.po_number, change.version, change.rule_id) == ("12.50", "12.70", "", None, "P01")
    asked(tmp_path, "250108_20261118T090000", article("0.45", code="KM-0802"))                        # another article: a change of its own
    assert [change.article_code for change in open_changes(tmp_path)] == ["KM-0801", "KM-0802"]


def test_numbers_compare_as_numbers(tmp_path):
    first = asked(tmp_path, case(18), price("12.7"), quantity("5"), article("12.7"), date("2026-W48"))
    second = asked(tmp_path, case(19), price("12.70"), quantity("5.0"), article("12.700"), date("2026-W48"))
    assert states(tmp_path) == [([one, two], "open") for one, two in zip(first, second)]
    friday = asked(tmp_path, case(20), date("2026-11-27"))             # the Friday of week 48 is another value than the week itself
    assert [change.new_value for change in open_changes(tmp_path) if change.kind == "po_line_date"] == ["2026-11-27"]
    assert states(tmp_path)[-1] == (friday, "open") and states(tmp_path)[3] == ([first[3], second[3]], "replaced")


def test_attach_and_status_are_no_rows(tmp_path):
    ids = asked(tmp_path, case(18), WriteOp(kind="attach_document", po_number="250108", version=1, document="/cases/x/input/MU08_conf.pdf"),
                WriteOp(kind="set_status", po_number="250108", version=1, old_value="Besteld", new_value="Bevestigd"),
                WriteOp(kind="detach_document", po_number="250108", version=1, document="/cases/x/input/MU08_conf.pdf"), rule_id="R01")
    assert changes(tmp_path) == ([], 0) and open_changes(tmp_path) == []
    assert mark(tmp_path, ids, "entered", NOW) == 0 and not (tmp_path / MARKS_FILE).exists()
    assert LINE_KINDS == ("po_line_price", "po_line_date", "po_line_quantity")


def test_a_damaged_line_is_passed_over_and_counted(tmp_path):
    """A line of either file that cannot be read is passed over and counted; the last line of a file that another process is writing
    is the usual case. The lines around it are read, and a mark written after a cut line starts a line of its own."""
    first = asked(tmp_path, case(18), price("12.70"))
    second = asked(tmp_path, case(18), price("4.10", old="4.00", line="2"))
    prepared = tmp_path / PREPARED_FILE
    whole = prepared.read_bytes()
    prepared.write_bytes(whole + b"9f0e1d2c,250108_20261118T110000,250108,1,3,,po_line_pr")        # cut off while it is written
    found, skipped = changes(tmp_path)
    assert skipped == 1 and [change.ids for change in found] == [first, second]
    marks = tmp_path / MARKS_FILE
    marks.write_text(json.dumps({"entry_id": first[0], "mark": "entered", "at": AT, "by": "buyer"}) + "\n"
                     + '{"entry_id": "' + second[0] + '", "mark": "drop', encoding="utf-8")         # the second mark cut off
    found, skipped = changes(tmp_path)
    assert skipped == 2 and [change.state for change in found] == ["entered", "open"]
    assert mark(tmp_path, second, "dropped", NOW) == 1                # on a line of its own after the cut one
    found, skipped = changes(tmp_path)
    assert skipped == 2 and [change.state for change in found] == ["entered", "dropped"]
    assert marks.read_text(encoding="utf-8").splitlines()[-1] == json.dumps({"entry_id": second[0], "mark": "dropped", "at": AT, "by": "buyer"})
    # Other lines that cannot be read: not UTF-8, a kind, a version or a time the file does not hold, a field more, a PO line without
    # its line; a mark that is no object, an unknown mark, a time that is none. A blank line is no line.
    columns = whole.split(b"\n")[0].decode("utf-8").split(",")
    good = dict(zip(columns, whole.split(b"\n")[1].decode("utf-8").split(",")))
    bad = [good | {"entry_id": "b1", "kind": "po_line_colour"}, good | {"entry_id": "b2", "version": "v1"},
           good | {"entry_id": "b3", "timestamp": "gisteren"}, good | {"entry_id": "b4", "line_id": ""}]
    with open(prepared, "ab") as f:
        f.write(b"\n\xff\xfe made-up bytes\n\n" + b"".join((",".join(row[column] for column in columns) + "\n").encode() for row in bad)
                + (",".join(good[column] for column in columns) + ",one field more\n").encode())
    with open(marks, "a", encoding="utf-8") as f:
        f.write('["entered"]\n{"entry_id": "x", "mark": "maybe", "at": "2026-11-18T10:32:05"}\n'
                '{"entry_id": "x", "mark": "entered", "at": "yesterday"}\n\n')
    found, skipped = changes(tmp_path)
    assert skipped == 2 + 6 + 3 and [change.ids for change in found] == [first, second]


def test_mark_writes_a_line_per_id_and_nothing_for_a_change_that_is_not_open(tmp_path):
    ids = asked(tmp_path, case(18), price("12.70")) + asked(tmp_path, case(19), price("12.70"))

    def marked(entry_ids: list[str], what: str, now: datetime = NOW, **by) -> int:
        """mark(), which never touches the prepared rows (the file keeps its bytes) and appends to the marks only."""
        before = (tmp_path / PREPARED_FILE).read_bytes()
        written = mark(tmp_path, entry_ids, what, now, **by)
        assert (tmp_path / PREPARED_FILE).read_bytes() == before
        return written

    def lines() -> list[dict]:
        return [json.loads(line) for line in (tmp_path / MARKS_FILE).read_text(encoding="utf-8").splitlines()]

    assert marked(ids + ids[:1], "entered") == 2                                  # one line per entry id, each once
    assert lines() == [{"entry_id": entry_id, "mark": "entered", "at": AT, "by": "buyer"} for entry_id in ids]
    assert marked(ids, "entered") == 0 and marked(ids, "dropped") == 0             # not open: nothing is written
    assert marked(["no-such-entry"], "reopened") == 0 and len(lines()) == 2
    assert marked(ids[:1], "reopened", datetime(2026, 11, 19, 8, 0), by="owner") == 1
    assert lines()[-1] == {"entry_id": ids[0], "mark": "reopened", "at": "2026-11-19T08:00:00", "by": "owner"}
    assert marked(ids[1:], "reopened") == 1 and open_changes(tmp_path)[0].ids == ids
    other = asked(tmp_path, case(20), price("4.10", old="4.00", line="2"))
    assert marked(ids + other, "dropped") == 3                                     # two open changes at once
    newer = asked(tmp_path, case(21), price("4.20", old="4.00", line="2"))
    assert marked(other + newer, "entered") == 1                                   # the dropped one is not ticked, the open one is
    assert [(change.ids, change.state) for change in changes(tmp_path)[0]] == [(ids, "dropped"), (other, "dropped"), (newer, "entered")]
    assert len(lines()) == 8 and sorted(path.name for path in tmp_path.iterdir()) == [MARKS_FILE, PREPARED_FILE]


# --- the review round: undo rows (Z2) and lines the CSV reader cannot parse (Z3) ---

def logged(folder, case_root, case_id: str, *ops: WriteOp) -> list[str]:
    """The operations prepared as a run prepares them: a row of the prepared writer in the folder and the entry in the write log of the
    case folder case_root, where the undo finds it. Their entry ids."""
    case_root.mkdir(parents=True, exist_ok=True)
    writer, ids = PreparedWriter(folder), []
    for op in ops:
        entry = writer.apply(op, case_id, "R04", {}, NOW)
        WriteLog(case_root).append(entry)
        ids.append(entry.entry_id)
    return ids


def undone(folder, case_root, *entry_ids: str, now: datetime = NOW) -> list[str]:
    """undo_entry of each entry through the prepared writer of the folder at now, the newest first as undo_case goes: the ids of the
    undo rows, in that order."""
    return [undo_entry(case_root, entry_id, PreparedWriter(folder), now).entry_id for entry_id in reversed(entry_ids)]


def test_an_undo_of_a_change_not_entered_cancels_it_and_the_place_asks_nothing(tmp_path):
    """The undo of a change that was never entered (nor seen) cancels it: neither shows a row, because Exact still holds the value
    from before; a change dropped is cancelled the same way."""
    case_root = tmp_path / "case"
    ids = logged(tmp_path, case_root, case(18), price("12.70"), article("12.70"))
    back = undone(tmp_path, case_root, *ids)                                  # the undo of the article first, then of the price
    assert open_changes(tmp_path) == []
    assert [(change.ids, change.state, change.rule_id) for change in changes(tmp_path)[0]] == [
        (ids[:1], "undone", "R04"), (ids[1:], "undone", "R04"), (back[:1], "undone", UNDO_RULE_ID), (back[1:], "undone", UNDO_RULE_ID)]
    assert mark(tmp_path, ids + back, "entered", NOW) == 0                    # nothing of it is a change to tick
    dropped = tmp_path / "dropped"
    ids = logged(dropped, dropped / "case", case(18), price("12.70"))
    mark(dropped, ids, "dropped", NOW)
    back = undone(dropped, dropped / "case", *ids)
    assert states(dropped) == [(ids, "undone"), (back, "undone")]
    again = asked(dropped, case(19), price("12.70"))                          # a newer document that asks the value again is asked
    assert states(dropped)[-1] == (again, "open")


def test_an_undo_of_an_entered_change_asks_to_put_the_value_from_before_back(tmp_path):
    case_root = tmp_path / "case"
    ids = logged(tmp_path, case_root, case(18), price("12.70"), article("12.70"))
    mark(tmp_path, ids, "entered", NOW)
    back = undone(tmp_path, case_root, *ids)
    assert states(tmp_path) == [(ids[:1], "entered"), (ids[1:], "entered"), (back[:1], "open"), (back[1:], "open")]
    put_article, put_price = open_changes(tmp_path)
    assert (put_price.old_value, put_price.new_value, put_price.rule_id, put_price.case_ids) == ("12.70", "12.50", UNDO_RULE_ID, [case(18)])
    assert (put_article.old_value, put_article.new_value) == ("12.70", None)  # the price before it the aid never knew
    assert mark(tmp_path, back, "entered", NOW) == 2 and open_changes(tmp_path) == []
    again = asked(tmp_path, case(19), price("12.70"))                         # Exact holds 12.50 now, so 12.70 is asked again
    assert states(tmp_path)[-1] == (again, "open")
    seen = tmp_path / "seen"                                                  # a change the aid saw in Exact counts as entered
    ids = logged(seen, seen / "case", case(18), price("12.70"))
    mark(seen, ids, "seen", NOW)
    back = undone(seen, seen / "case", *ids)
    assert states(seen) == [(ids, "seen"), (back, "open")]


def test_an_undo_belongs_to_the_last_run_of_its_place_with_its_value(tmp_path):
    """12.70 entered, then a newer document asks 12.90, and that document's case is undone: the undo of 12.90 cancels 12.90, and
    12.70 stays entered. An undo row whose change the folder does not hold is a change of its own."""
    first, second = tmp_path / "first", tmp_path / "second"
    entered = logged(tmp_path, first, case(18), price("12.70"))
    mark(tmp_path, entered, "entered", NOW)
    newer = logged(tmp_path, second, case(19), price("12.90"))
    back = undone(tmp_path, second, *newer)
    assert states(tmp_path) == [(entered, "entered"), (newer, "undone"), (back, "undone")] and open_changes(tmp_path) == []
    alone = tmp_path / "alone"
    row = asked(alone, case(18), price("12.50", old="12.70"), rule_id=UNDO_RULE_ID)
    assert states(alone) == [(row, "open")]


def test_a_line_the_csv_reader_cannot_parse_is_passed_over_and_counted(tmp_path):
    """A line longer than a field of the CSV reader may be (a zero-filled stretch that a crash can leave) and a bare carriage return in
    a field without quotes make the CSV reader raise: such a line is passed over and counted like any damaged line."""
    first = asked(tmp_path, case(18), price("12.70"))
    with open(tmp_path / PREPARED_FILE, "ab") as f:
        f.write(b"\x00" * 200_000 + b"\n" + b"9f0e,250108_20261118T110000,250108,1,3\r,,po_line_price,1,2,,R04,2026-11-18T11:00:00\n")
    second = asked(tmp_path, case(19), price("4.10", old="4.00", line="2"))
    found, passed = changes(tmp_path)
    assert passed == 2 and [change.ids for change in found] == [first, second]


def test_a_cancelled_change_is_as_if_it_had_never_been_asked(tmp_path):
    """An undo cancels its run as if neither had been asked: an older change that the cancelled one had replaced is asked again,
    the runs on either side of the cancelled pair are one change again, and two undo rows of one entered change are one change to
    enter."""
    first, second = tmp_path / "first", tmp_path / "second"
    older = logged(tmp_path, first, case(18), price("12.70"))
    newer = logged(tmp_path, second, case(19), price("12.90"))
    back = undone(tmp_path, second, *newer)
    assert states(tmp_path) == [(older, "open"), (newer, "undone"), (back, "undone")]
    again = asked(tmp_path, case(20), price("12.70"))
    assert states(tmp_path) == [(older + again, "open"), (newer, "undone"), (back, "undone")]
    entered = tmp_path / "entered"
    one = logged(entered, entered / "one", case(18), price("12.70"))
    other = logged(entered, entered / "other", case(19), price("12.70"))
    mark(entered, one + other, "entered", NOW)                                # the change ticked as the page ticks it: every entry id
    back = undone(entered, entered / "one", *one) + undone(entered, entered / "other", *other)
    assert states(entered) == [(one + other, "entered"), (back, "open")]
    [put] = open_changes(entered)
    assert (put.old_value, put.new_value, put.case_ids) == ("12.70", "12.50", [case(18), case(19)])


# --- the follow-up: an undo cancels only its own case's request (Z2a) ---

def cart(capture: int) -> str:
    """The id of a made-up case of a capture of the cart of PO 250201."""
    return f"250201_20261118T09{capture:02d}00"


def test_an_undo_of_one_of_two_confirmations_leaves_the_other_ones_request_asked(tmp_path):
    """Two confirmations asked the same 12.70 and the case of one is undone: the change stays asked, with the other case's entry id
    only. Ticked as entered, it stays entered and what the undo cancelled stays cancelled; undone after that, the other case's
    request asks to put the value back. Two cases that are both undone before any tick leave nothing on the list."""
    one = logged(tmp_path, tmp_path / "one", case(18), price("12.70"))
    other = logged(tmp_path, tmp_path / "other", case(19), price("12.70"))
    back = undone(tmp_path, tmp_path / "one", *one)
    assert states(tmp_path) == [(one, "undone"), (other, "open"), (back, "undone")]
    [still] = open_changes(tmp_path)
    assert (still.ids, still.case_ids, still.old_value, still.new_value) == (other, [case(19)], "12.50", "12.70")
    assert mark(tmp_path, still.ids, "entered", NOW) == 1
    assert states(tmp_path) == [(one, "undone"), (other, "entered"), (back, "undone")] and open_changes(tmp_path) == []
    again = undone(tmp_path, tmp_path / "other", *other)
    assert states(tmp_path) == [(one, "undone"), (other, "entered"), (back, "undone"), (again, "open")]
    assert [(change.old_value, change.new_value, change.case_ids) for change in open_changes(tmp_path)] == [("12.70", "12.50", [case(19)])]
    both = tmp_path / "both"
    one = logged(both, both / "one", case(18), price("12.70"))
    other = logged(both, both / "other", case(19), price("12.70"))
    back = undone(both, both / "one", *one) + undone(both, both / "other", *other)
    assert states(both) == [(one + other, "undone"), (back[:1], "undone"), (back[1:], "undone")] and open_changes(both) == []


def test_three_captures_of_one_cart_and_one_undone_keep_the_changes_of_the_other_two(tmp_path):
    """Three captures of one cart asked the same price on PO line 2 and the same article price, and the case of the second capture
    is undone: both changes stay asked with the entry ids of the first and the third capture; the second capture's rows and its undo
    rows leave the list."""
    captures = [logged(tmp_path, tmp_path / f"capture{n}", cart(n), price("4.15", old="4.10", po="250201", line="2"),
                       article("4.15", code="KM-2102", po="250201")) for n in (1, 2, 3)]
    first, second, third = captures
    back = undone(tmp_path, tmp_path / "capture2", *second)                  # the article's undo first, then the price's
    assert states(tmp_path) == [([first[0], third[0]], "open"), ([first[1], third[1]], "open"), (second[:1], "undone"),
                                (second[1:], "undone"), (back[:1], "undone"), (back[1:], "undone")]
    assert [change.case_ids for change in open_changes(tmp_path)] == [[cart(1), cart(3)], [cart(1), cart(3)]]


def test_an_undo_after_the_change_was_entered_asks_to_put_the_value_back_also_when_another_case_asked_it(tmp_path):
    """The two confirmations' change is ticked as entered, as the page ticks it, and then one case is undone: the undo asks to put the
    value from before back, as for a change of one case, and the entered change stays as it is. A request that came after the tick
    is not in Exact by the marks: its undo cancels it, and nothing is asked back for it."""
    one = logged(tmp_path, tmp_path / "one", case(18), price("12.70"))
    other = logged(tmp_path, tmp_path / "other", case(19), price("12.70"))
    [change] = open_changes(tmp_path)
    assert mark(tmp_path, change.ids, "entered", NOW) == 2
    back = undone(tmp_path, tmp_path / "one", *one)
    assert states(tmp_path) == [(one + other, "entered"), (back, "open")]
    [put] = open_changes(tmp_path)
    assert (put.old_value, put.new_value, put.case_ids, put.rule_id) == ("12.70", "12.50", [case(18)], UNDO_RULE_ID)
    later = tmp_path / "later"
    one = logged(later, later / "one", case(18), price("12.70"))
    mark(later, one, "entered", NOW)                                          # ticked before the second confirmation came
    other = logged(later, later / "other", case(19), price("12.70"))
    back = undone(later, later / "other", *other)
    assert states(later) == [(one, "entered"), (other, "undone"), (back, "undone")] and open_changes(later) == []


def test_an_undo_whose_case_asked_nothing_in_its_run_cancels_nothing(tmp_path):
    """An undo belongs to the last run of its place with its value, also when its own case asked nothing in that run: it then
    cancels nothing, and it is a change to enter only when that run is entered or seen; otherwise it is passed over."""
    first = asked(tmp_path, case(18), price("12.70"))
    newer = asked(tmp_path, case(20), price("12.90"))
    last = asked(tmp_path, case(19), price("12.70"))
    back = asked(tmp_path, case(18), price("12.50", old="12.70"), rule_id=UNDO_RULE_ID)    # its run by value is case 19's
    assert states(tmp_path) == [(first, "replaced"), (newer, "replaced"), (last, "open"), (back, "undone")]
    assert open_changes(tmp_path)[0].ids == last
    assert mark(tmp_path, last, "entered", NOW) == 1
    assert states(tmp_path) == [(first, "replaced"), (newer, "replaced"), (last, "entered"), (back, "open")]


# --- Task 35: a change seen in a newer version of its purchase order, the put-back's link, and a tick taken back ---

LATER = datetime(2026, 11, 19, 8, 0)                # the time of a pass of the runner after the cases ran
LATER_AT = "2026-11-19T08:00:00"
BEFORE, AFTER = datetime(2026, 11, 1, 9, 0), datetime(2026, 11, 19, 7, 0)    # filed before the rows of NOW, and after them


def store_of(tmp_path) -> PoStore:
    """An empty PO store under tmp_path; filed() puts versions into it as the store keeps them."""
    return PoStore(tmp_path / "store")


def filed(store: PoStore, n: int, *lines: DocLine, po: str = "250108", at: datetime | None = None) -> PoVersion:
    """Version n of the PO with these lines, numbered 1, 2, 3 unless a line has a number of its own, filed into the store as the store
    files a version: its po.json, written whole (no PDF behind it), with `at` as the time it was filed (the file's time, which
    PoStore.filed_at reads): by default version 1 before the rows prepared at NOW, as the version the cases were compared with, and
    any later version after them, as a print that Exact sent after the change."""
    numbered = [one if one.line_id else one.model_copy(update={"line_id": str(number)}) for number, one in enumerate(lines, 1)]
    document = Document(kind="po", po_reference=DocValue(value=po, text=po), version_hint=DocValue(value=str(n), text=str(n)),
                        supplier=DocValue(value="S08", text="S08"), lines=numbered)
    version = PoVersion(po_number=po, version=n, version_date=None, pdf_path=store.root / po / f"v{n}" / "po.pdf", document=document,
                        supplier="S08", total=None, above_authorization=None)
    folder = store.root / po / f"v{n}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "po.json").write_text(version.model_dump_json(indent=1) + "\n", encoding="utf-8")
    when = (at or (BEFORE if n == 1 else AFTER)).timestamp()
    os.utime(folder / "po.json", (when, when))
    return version


def compared(tmp_path, case_id: str, *versions: PoVersion) -> Path:
    """A case folder under tmp_path/cases whose step 01 holds these versions, as the pipeline writes it: the cases root."""
    root = tmp_path / "cases"
    made = open_case(root, "confirmation", [], case_id.partition("_")[0], datetime.strptime(case_id.partition("_")[2], "%Y%m%dT%H%M%S"))
    assert made.case_id == case_id
    made.write_step(1, "po_versions", {"po_number": made.po_number, "versions": [version.model_dump(mode="json") for version in versions]})
    return root


def marks_of(folder) -> list[dict]:
    path = folder / MARKS_FILE
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_a_price_a_date_and_a_quantity_are_seen_in_a_newer_version(tmp_path):
    """A newer version that shows the new value marks the change as seen, by "version <n>": the price as the line's net price per its
    own basis, rounded half up to the decimals of the new value and at least two; the delivery date by date_matches; the quantity as a
    number. The version the cases were compared with shows the old values and marks nothing."""
    store = store_of(tmp_path)
    filed(store, 1, line("KM-0801", "3", "12.50", date="2026-11-16", basis="1"), line("KM-0802", "50", "0.42", date="2026-11-16", basis="1"),
          line("KM-0803", "10", "45.00", basis="100"))
    price_ids = asked(tmp_path, case(18), price("12.70"))
    date_ids = asked(tmp_path, case(18), date("2026-11-20"))
    quantity_ids = asked(tmp_path, case(18), quantity("60", old="50", line="2"))
    per_hundred = asked(tmp_path, case(18), price("47.10", old="45.00", line="3"))
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 0 and marks_of(tmp_path) == []
    # version 2: a unit price less a discount that comes to 12.70, the new date, 60.000 pieces, and 47.10 per hundred
    filed(store, 2, line("KM-0801", "3", "13.37", date="2026-11-20", basis="1", discount="5"),
          line("KM-0802", "60.000", "0.42", date="2026-11-16", basis="1"), line("KM-0803", "10", "47.10", basis="100"))
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 4
    found = {change.ids[0]: change for change in changes(tmp_path)[0]}
    for ids in (price_ids, date_ids, quantity_ids, per_hundred):
        assert (found[ids[0]].state, found[ids[0]].marked_by, found[ids[0]].marked_at) == ("seen", "version 2", LATER_AT)
    assert marks_of(tmp_path) == [{"entry_id": ids[0], "mark": "seen", "at": LATER_AT, "by": "version 2"}
                                  for ids in (price_ids, date_ids, quantity_ids, per_hundred)]
    assert [shown_now(found[ids[0]], store, tmp_path / "cases") for ids in (price_ids, date_ids, quantity_ids, per_hundred)] == [
        "12.70", "2026-11-20", "60.000", "47.10"]
    # a price with three decimals is compared at three: 0.0425 a piece is not the 0.04 that the line shows at two
    other = tmp_path / "other"
    asked(other, case(18), price("0.0425", old="0.0400", line="1"))
    filed(store_of(other), 1, line("KM-0801", "3", "0.0400", basis="1"))
    filed(store_of(other), 2, line("KM-0801", "3", "0.04", basis="1"))
    assert reconcile(other, store_of(other), other / "cases", LATER) == 0
    filed(store_of(other), 3, line("KM-0801", "3", "0.0425", basis="1"))
    assert reconcile(other, store_of(other), other / "cases", LATER) == 1


def test_a_day_inside_the_new_week_counts_as_seen(tmp_path):
    """The new value is a week: a version that shows a day of that week shows it; a day of the next week does not, and neither does a
    week whose days reach outside a new value that is a day."""
    store = store_of(tmp_path)
    filed(store, 1, line("KM-0801", "3", "12.50", date="2026-11-16", basis="1"), line("KM-0802", "50", "0.42", date="2026-11-16", basis="1"))
    week = asked(tmp_path, case(18), date("2026-W48"))
    day = asked(tmp_path, case(18), date("2026-11-25", line="2"))
    filed(store, 2, line("KM-0801", "3", "12.50", date="2026-11-30", basis="1"), line("KM-0802", "50", "0.42", date="2026-W48", basis="1"))
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 0             # 30 November is in week 49; a week is no single day
    filed(store, 3, line("KM-0801", "3", "12.50", date="2026-11-25", basis="1"), line("KM-0802", "50", "0.42", date="2026-11-25", basis="1"))
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 2
    assert [(change.ids, change.state, change.marked_by) for change in changes(tmp_path)[0]] == [(week, "seen", "version 3"),
                                                                                                 (day, "seen", "version 3")]


def test_the_old_value_and_a_third_value_stay_open_and_the_third_is_noted(tmp_path):
    """A newer version that shows the old value, or a value that is neither the old nor the new one, marks nothing; the third value is
    what note_value gives for the row (Exact shows it now), the old value gives no note."""
    store = store_of(tmp_path)
    filed(store, 1, line("KM-0801", "3", "12.50", date="2026-11-16", basis="1"))
    ids = asked(tmp_path, case(18), price("12.70"), date("2026-11-20"))
    filed(store, 2, line("KM-0801", "3", "12.500", date="2026-11-16", basis="1"))                     # the old values once more
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 0
    price_change, date_change = open_changes(tmp_path)
    assert [note_value(change, store, tmp_path / "cases") for change in (price_change, date_change)] == [None, None]
    assert [shown_now(change, store, tmp_path / "cases") for change in (price_change, date_change)] == ["12.50", "2026-11-16"]
    filed(store, 3, line("KM-0801", "3", "12.80", date="2026-W47", basis="1"))                         # a third price and a third date
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 0 and [change.ids for change in open_changes(tmp_path)] == [ids[:1], ids[1:]]
    assert [note_value(change, store, tmp_path / "cases") for change in open_changes(tmp_path)] == ["12.80", "2026-W47"]
    assert marks_of(tmp_path) == []
    # the version the case itself was compared with is no newer version: it notes nothing, whatever it shows; a PO the store does not
    # know shows nothing
    alone = store_of(tmp_path / "alone")
    filed(alone, 1, line("KM-0801", "3", "12.80", date="2026-11-16", basis="1"))
    assert note_value(price_change, alone, tmp_path / "cases") is None and shown_now(price_change, alone, tmp_path / "cases") == "12.80"
    assert shown_now(price_change, store_of(tmp_path / "nothing"), tmp_path / "cases") is None


def test_the_line_is_paired_by_item_code_when_the_numbers_differ(tmp_path):
    """The line of a change is found by its line id when the item codes agree or one is unknown, the item code of the change's line
    coming from step 01 of its case; else by the item code when exactly one line of the newer version holds it; else nothing happens."""
    store = store_of(tmp_path)
    first = filed(store, 1, line("KM-0801", "3", "12.50", basis="1"), line("KM-0802", "50", "0.42", basis="1"))
    root = compared(tmp_path, case(18), first)
    ids = asked(tmp_path, case(18), price("12.70"))
    filed(store, 2, line("KM-0802", "50", "0.42", basis="1"), line("KM-0801", "3", "12.70", basis="1"))               # the lines swapped
    [change] = open_changes(tmp_path)
    assert item_code(change, root) == "KM-0801" and shown_now(change, store, root) == "12.70"
    assert reconcile(tmp_path, store, root, LATER) == 1 and states(tmp_path) == [(ids, "seen")]
    # by the line id: with the item codes unknown (no case folder), or when they agree
    unknown = tmp_path / "unknown"
    asked(unknown, case(18), price("12.70"))
    assert shown_now(open_changes(unknown)[0], store, unknown / "cases") == "0.42"          # line 1 of version 2, whose code is unknown here
    assert reconcile(unknown, store, unknown / "cases", LATER) == 0
    # two lines that hold the item code and agree with the asked line in all else (the second round of Task 43, W17: one that
    # differs in its quantity would tell them apart), and a line id that the version lacks with an unknown code: nothing is found
    twice = tmp_path / "twice"
    twice_store = store_of(twice)
    twice_root = compared(twice, case(18), filed(twice_store, 1, line("KM-0801", "3", "12.50", basis="1")))
    asked(twice, case(18), price("12.70"))
    filed(twice_store, 2, line("KM-0802", "3", "12.70", basis="1"), line("KM-0801", "3", "12.70", basis="1"), line("KM-0801", "3", "12.70", basis="1"))
    assert shown_now(open_changes(twice)[0], twice_store, twice_root) is None and reconcile(twice, twice_store, twice_root, LATER) == 0
    missing = tmp_path / "missing"
    asked(missing, case(18), price("12.70", line="9"))
    assert shown_now(open_changes(missing)[0], store, missing / "cases") is None


def test_an_article_price_is_never_seen(tmp_path):
    store = store_of(tmp_path)
    filed(store, 1, line("KM-0801", "3", "12.50", basis="1"))
    ids = asked(tmp_path, case(18), article("12.70"), article("12.70", old="12.50", po=""))
    filed(store, 2, line("KM-0801", "3", "12.70", basis="1"))
    [change] = open_changes(tmp_path)
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 0 and states(tmp_path) == [(ids, "open")]
    assert shown_now(change, store, tmp_path / "cases") is None and note_value(change, store, tmp_path / "cases") is None


def test_a_later_version_ends_the_tick_it_contradicts_and_never_a_drop(tmp_path):
    """A change that is entered, seen or dropped gets no mark "seen" from a later version. Since the review round of Task 43 (W9) a
    version filed after the mark of an entered or seen change that shows another value ends that tick: the change is open again. A
    version filed before the mark ends nothing, and a drop is never taken back."""
    store = store_of(tmp_path)
    filed(store, 1, line("KM-0801", "3", "12.50", date="2026-11-16", basis="1"), line("KM-0802", "50", "0.42", basis="1"))
    seen = asked(tmp_path, case(18), price("12.70"))
    entered = asked(tmp_path, case(18), date("2026-11-20"))
    dropped = asked(tmp_path, case(18), quantity("60", old="50", line="2"))
    mark(tmp_path, entered, "entered", NOW)
    mark(tmp_path, dropped, "dropped", NOW)
    filed(store, 2, line("KM-0801", "3", "12.70", date="2026-11-20", basis="1"), line("KM-0802", "60", "0.42", basis="1"))
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 1
    before = (tmp_path / MARKS_FILE).read_bytes()
    assert mark(tmp_path, seen + entered + dropped, "seen", LATER) == 0 and (tmp_path / MARKS_FILE).read_bytes() == before
    filed(store, 3, line("KM-0801", "3", "12.50", date="2026-11-16", basis="1"), line("KM-0802", "50", "0.42", basis="1"))    # all back
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 0
    # version 3 was filed after the tick of the date (NOW) and before the mark "seen" of the price (LATER): it ends the tick alone
    assert states(tmp_path) == [(seen, "seen"), (entered, "open"), (dropped, "dropped")]
    assert marks_of(tmp_path)[-1] == {"entry_id": entered[0], "mark": "reopened", "at": LATER_AT, "by": "version 3", "shown": "2026-11-16"}
    filed(store, 4, line("KM-0801", "3", "12.50", date="2026-11-16", basis="1"), line("KM-0802", "50", "0.42", basis="1"),
          at=LATER + timedelta(hours=1))                                         # filed after the price was seen too
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER + timedelta(hours=2)) == 0
    assert states(tmp_path) == [(seen, "open"), (entered, "open"), (dropped, "dropped")]


def test_a_second_pass_marks_nothing_more(tmp_path):
    store = store_of(tmp_path)
    filed(store, 1, line("KM-0801", "3", "12.50", basis="1"))
    ids = asked(tmp_path, case(18), price("12.70")) + asked(tmp_path, case(19), price("12.70"))      # one change asked by two cases
    filed(store, 2, line("KM-0801", "3", "12.70", basis="1"))
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 1 and len(marks_of(tmp_path)) == 2   # one line per entry id
    before = (tmp_path / MARKS_FILE).read_bytes()
    assert reconcile(tmp_path, store, tmp_path / "cases", datetime(2026, 11, 19, 8, 1)) == 0
    assert (tmp_path / MARKS_FILE).read_bytes() == before and states(tmp_path) == [(ids, "seen")]


def test_a_put_back_is_linked_by_its_mark_and_only_a_version_filed_after_it_ticks_it_off(tmp_path):
    """The row that puts a change back is linked by one line of marks.jsonl, {"entry_id", "mark": "puts_back", "of", "at"}, which gives
    it no state; put_back_of follows the link. A look at the order counts only when it was filed after the newest row of the change was
    prepared, for a put-back its own row (review round, K1): a version 2 that Exact printed before the buyer entered the change, and
    that still shows the old price, never ticks the put-back off, however new its number; a version filed after the put-back does."""
    store = store_of(tmp_path)
    filed(store, 1, line("KM-0801", "3", "12.50", basis="1"))
    case_root = tmp_path / "case"
    ids = logged(tmp_path, case_root, case(18), price("12.70"))                  # asked at NOW, 18 November 10:32
    filed(store, 2, line("KM-0801", "3", "12.50", basis="1"), at=datetime(2026, 11, 18, 11, 0))    # printed again for another reason
    assert reconcile(tmp_path, store, tmp_path / "cases", datetime(2026, 11, 18, 11, 30)) == 0       # it shows the old price
    mark(tmp_path, ids, "entered", datetime(2026, 11, 18, 12, 0))                # then the buyer enters 12,70 and ticks it
    back = undone(tmp_path, case_root, *ids, now=datetime(2026, 11, 18, 13, 0))  # and puts it back
    link_put_back(tmp_path, back[0], ids[0], datetime(2026, 11, 18, 13, 0))
    assert marks_of(tmp_path)[-1] == {"entry_id": back[0], "mark": "puts_back", "of": ids[0], "at": "2026-11-18T13:00:00"}
    assert put_back_of(tmp_path, ids[0]) == back[0] and put_back_of(tmp_path, back[0]) is None and put_back_of(tmp_path / "none", ids[0]) is None
    found, passed = changes(tmp_path)
    assert passed == 0 and [(change.ids, change.state, change.puts_back) for change in found] == [(ids, "entered", None), (back, "open", ids[0])]
    assert (found[-1].asked_at, found[0].asked_at) == ("2026-11-18T13:00:00", NOW.isoformat())     # the time of the newest row
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 0 and states(tmp_path)[-1] == (back, "open")   # version 2 came before
    assert note_value(found[-1], store, tmp_path / "cases") is None
    filed(store, 3, line("KM-0801", "3", "12.50", basis="1"))                    # after the put-back: Exact shows the old price again
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 1 and changes(tmp_path)[0][-1].marked_by == "version 3"
    # a link line that cannot be read is passed over and counted, as any line of marks.jsonl
    with open(tmp_path / MARKS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"entry_id": back[0], "mark": "puts_back", "at": LATER_AT}) + "\n")
    assert changes(tmp_path)[1] == 1


def test_a_look_counts_only_when_filed_after_the_newest_row_of_the_change(tmp_path):
    """A version filed before the newest row of the change's run was prepared marks nothing and gives no note, whatever it shows: a
    second case that asks the same value after it makes the change newer than that version (review round, K1)."""
    store = store_of(tmp_path)
    filed(store, 1, line("KM-0801", "3", "12.50", basis="1"), line("KM-0802", "50", "0.42", basis="1"))
    first = asked(tmp_path, case(18), price("12.70"), quantity("60", old="50", line="2"))
    filed(store, 2, line("KM-0801", "3", "12.70", basis="1"), line("KM-0802", "55", "0.42", basis="1"), at=datetime(2026, 11, 18, 11, 0))
    again = asked(tmp_path, case(19), price("12.70"), quantity("60", old="50", line="2"), now=datetime(2026, 11, 18, 12, 0))
    price_change, quantity_change = open_changes(tmp_path)
    assert (price_change.ids, quantity_change.ids) == ([first[0], again[0]], [first[1], again[1]])
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 0 and marks_of(tmp_path) == []
    assert [note_value(change, store, tmp_path / "cases") for change in (price_change, quantity_change)] == [None, None]
    assert shown_now(quantity_change, store, tmp_path / "cases") == "55"           # what the newest version shows, whenever it came
    filed(store, 3, line("KM-0801", "3", "12.70", basis="1"), line("KM-0802", "55", "0.42", basis="1"))   # after the second case
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 1 and states(tmp_path) == [(first[:1] + again[:1], "seen"),
                                                                                              (first[1:] + again[1:], "open")]
    assert note_value(open_changes(tmp_path)[0], store, tmp_path / "cases") == "55"


def test_a_look_from_an_export_filed_after_a_change_ticks_it_off_as_seen_in_an_export(tmp_path):
    """The PO as it stands is a look from an export when that was filed after the latest PDF version (Task 40): an export that shows
    the new price, filed after the change was asked, marks it as seen by "export"; one filed before the change marks nothing (review
    round, K1 and K9)."""
    store = store_of(tmp_path)
    filed(store, 1, line("KM-0801", "3", "12.50", basis="1"), line("KM-0802", "50", "0.42", basis="1"))
    ids = asked(tmp_path, case(18), price("12.70"))
    export = tmp_path / "export.csv"
    export.write_text("po_number,line_id,item_code,quantity,unit_price,price_basis\n250108,1,KM-0801,3,12.70,1\n250108,2,KM-0802,50,0.42,1\n",
                      encoding="utf-8")
    [look] = store.from_export(export, {})
    filed_json = store.root / "250108" / "e1" / "po.json"
    early = datetime(2026, 11, 18, 10, 0).timestamp()                               # before the change was asked at 10:32
    os.utime(filed_json, (early, early))
    assert store.as_it_stands("250108") == look and shown_now(open_changes(tmp_path)[0], store, tmp_path / "cases") == "12.70"
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 0
    os.utime(filed_json, (AFTER.timestamp(), AFTER.timestamp()))
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 1
    [seen] = changes(tmp_path)[0]
    assert (seen.ids, seen.state, seen.marked_by, seen.marked_at) == (ids, "seen", "export", LATER_AT)


def test_reopen_puts_a_change_on_the_list_again_or_writes_nothing(tmp_path):
    """reopen takes the marks of a change away when that puts it on the list again, and else writes nothing: a change a newer value
    replaced, or one whose put-back would cancel it (an open put-back or a dropped one), stays as it is. It gives the state the change
    would have without its marks, by which the page says why (review round, K4)."""
    first = asked(tmp_path, case(18), price("12.70"))
    mark(tmp_path, first, "entered", NOW)
    assert reopen(tmp_path, first, LATER) == "open" and states(tmp_path) == [(first, "open")]
    assert marks_of(tmp_path)[-1] == {"entry_id": first[0], "mark": "reopened", "at": LATER_AT, "by": "buyer"}
    mark(tmp_path, first, "dropped", NOW)
    newer = asked(tmp_path, case(19), price("12.90"))                         # a newer value: the dropped one would be replaced
    before = (tmp_path / MARKS_FILE).read_bytes()
    assert reopen(tmp_path, first, LATER) == "replaced" and (tmp_path / MARKS_FILE).read_bytes() == before
    assert states(tmp_path) == [(first, "dropped"), (newer, "open")]
    put = tmp_path / "put"
    case_root = put / "case"
    ids = logged(put, case_root, case(18), price("12.70"))
    mark(put, ids, "entered", NOW)
    back = undone(put, case_root, *ids)                                       # its put-back would cancel it once it is not entered
    assert reopen(put, ids, LATER) == "undone" and states(put) == [(ids, "entered"), (back, "open")]
    mark(put, back, "dropped", NOW)                                           # also when the put-back was dropped (review round, K4)
    assert reopen(put, ids, LATER) == "undone" and states(put) == [(ids, "entered"), (back, "dropped")]
    assert reopen(put, ["no-such-entry"], LATER) is None


# --- the second review round: a put-back stands on the list (K10), and K4 for a change that several cases asked (K11) ---

def test_a_put_back_belongs_to_the_run_of_the_entry_it_puts_back(tmp_path):
    """A put-back linked by puts_back belongs to the run that holds the entry it puts back, not to the last run of its place whose new
    value is its old value (second review round, K10): a later case that asks the change's value again ("already") no longer swallows
    it. The put-back is a change to enter, and that later run is replaced by it, as the newest decision for the place. An undo row
    without such a link keeps the rule it has: it belongs to the run asked again, which holds no row of its case and no tick, and is
    passed over there."""
    case_root = tmp_path / "case"
    ids = logged(tmp_path, case_root, case(18), price("12.70"))
    mark(tmp_path, ids, "entered", NOW)
    other = asked(tmp_path, case(19), price("12.90", old="12.70"))            # a newer value, which the buyer drops
    mark(tmp_path, other, "dropped", NOW)
    again = asked(tmp_path, case(20), price("12.70"))                         # the first confirmation sent again: Exact holds 12,70
    assert states(tmp_path) == [(ids, "entered"), (other, "dropped"), (again, "already")]
    back = undone(tmp_path, case_root, *ids, now=LATER)                       # the operator's undo, linked to nothing
    assert states(tmp_path) == [(ids, "entered"), (other, "dropped"), (again, "already"), (back, "undone")]
    link_put_back(tmp_path, back[0], ids[0], LATER)                           # linked, as "Terugdraaien in Exact" links it
    assert states(tmp_path) == [(ids, "entered"), (other, "dropped"), (again, "replaced"), (back, "open")]
    [put] = open_changes(tmp_path)
    assert (put.ids, put.old_value, put.new_value, put.rule_id, put.puts_back) == (back, "12.70", "12.50", UNDO_RULE_ID, ids[0])


def test_put_back_state_works_out_a_put_back_without_writing(tmp_path):
    """put_back_state gives the state that the put-back of an entry would have on the list, worked out without writing anything (second
    review round, K10): one more row as undo_entry prepares it, of the entry's case, linked to the entry; or, with row_id, the row that
    puts it back already, linked to it. None for an entry the folder does not hold."""
    case_root = tmp_path / "case"
    ids = logged(tmp_path, case_root, case(18), price("12.70"))
    mark(tmp_path, ids, "entered", NOW)
    def files(folder) -> dict:
        return {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}

    before = files(tmp_path)
    assert put_back_state(tmp_path, ids[0], LATER) == "open" and put_back_state(tmp_path, "no-such-entry", LATER) is None
    assert files(tmp_path) == before
    # another case's put-back at the line, entered: Exact holds 12,50 by the marks, so a put-back of 12,70, a run of its own (third
    # review round, K20), would be "already" and would not stand open
    other = PreparedWriter(tmp_path).apply(price("12.50", old="12.70"), case(19), UNDO_RULE_ID, {"undo_of": "elsewhere"}, NOW).entry_id
    mark(tmp_path, [other], "entered", NOW)
    before = files(tmp_path)
    assert put_back_state(tmp_path, ids[0], LATER) == "already" and files(tmp_path) == before
    # a put-back prepared already (the operator's undo, linked to nothing and passed over): its state once linked
    again = tmp_path / "again"
    first = logged(again, again / "case", case(18), price("12.70"))
    mark(again, first, "entered", NOW)
    dropped = asked(again, case(19), price("12.90", old="12.70"))
    mark(again, dropped, "dropped", NOW)
    asked(again, case(20), price("12.70"))                                    # asked again: "already"
    back = undone(again, again / "case", *first, now=LATER)
    before = files(again)
    assert states(again)[-1] == (back, "undone") and put_back_state(again, first[0], LATER, row_id=back[0]) == "open"
    assert put_back_state(again, first[0], LATER, row_id="no-such-row") is None and files(again) == before


def test_reopen_writes_nothing_for_a_change_with_a_put_back_whichever_case_it_undid(tmp_path):
    """Two cases asked the same price, one change with two entries, ticked; "Terugdraaien in Exact" puts back the newest entry, the
    second case's. reopen then writes nothing and returns "undone" whatever the put-back's state, open, dropped or entered, and
    whatever rows of other cases the run holds: the buyer asked for the value from before (second review round, K11)."""
    first = logged(tmp_path, tmp_path / "first", case(18), price("12.70"))
    second = logged(tmp_path, tmp_path / "second", case(19), price("12.70"))
    mark(tmp_path, first + second, "entered", NOW)
    back = undone(tmp_path, tmp_path / "second", *second, now=LATER)
    link_put_back(tmp_path, back[0], second[0], LATER)
    assert states(tmp_path) == [(first + second, "entered"), (back, "open")]
    for what in (None, "dropped", "reopened", "entered"):                    # the put-back open, dropped, open again, entered
        if what is not None:
            mark(tmp_path, back, what, LATER)
        before = (tmp_path / MARKS_FILE).read_bytes()
        assert reopen(tmp_path, first + second, LATER) == "undone", what
        assert reopen(tmp_path, second, LATER) == "undone" and reopen(tmp_path, first, LATER) == "undone", what
        assert (tmp_path / MARKS_FILE).read_bytes() == before
        assert states(tmp_path)[0] == (first + second, "entered"), what
    assert states(tmp_path) == [(first + second, "entered"), (back, "entered")]


# --- the third review round: a put-back stands alone (K20), and K11 for an undo made with the manual's recipe (K26) ---

def test_a_put_back_is_a_run_of_its_own(tmp_path):
    """A put-back linked by puts_back is a run of its own (third review round, K20): it never joins a neighbouring row, whatever its
    value. So it stands open after a dropped newer change that asked the same old value; its state is "already" when the marks say
    Exact holds its value (an entered put-back of another change restored it); and a later row with its value is a change of its own,
    which replaces it."""
    case_root = tmp_path / "case"
    ids = logged(tmp_path, case_root, case(18), price("12.70"))
    mark(tmp_path, ids, "entered", NOW)
    newer = asked(tmp_path, case(19), price("12.50", old="12.70"))            # asks the old value again, and is dropped
    mark(tmp_path, newer, "dropped", NOW)
    assert put_back_state(tmp_path, ids[0], LATER) == "open"
    back = undone(tmp_path, case_root, *ids, now=LATER)
    link_put_back(tmp_path, back[0], ids[0], LATER)
    assert states(tmp_path) == [(ids, "entered"), (newer, "dropped"), (back, "open")]
    later = asked(tmp_path, case(20), price("12.50", old="12.70"))            # the same value once more: a change of its own
    assert states(tmp_path) == [(ids, "entered"), (newer, "dropped"), (back, "replaced"), (later, "open")]
    # another change put back and entered first restores the old value: the put-back of the first is "already"
    other = tmp_path / "other"
    first = logged(other, other / "first", case(18), price("12.70"))
    mark(other, first, "entered", NOW)
    second = logged(other, other / "second", case(19), price("12.60"))       # asked against version 1 as well
    mark(other, second, "entered", NOW)
    put = undone(other, other / "second", *second, now=LATER)
    link_put_back(other, put[0], second[0], LATER)
    mark(other, put, "entered", LATER)
    assert states(other) == [(first, "entered"), (second, "entered"), (put, "entered")]
    assert put_back_state(other, first[0], LATER) == "already"


def test_reopen_writes_nothing_for_a_change_whose_case_undo_is_entered(tmp_path):
    """Two cases asked a change, ticked; the operator undid the second case with MANUAL 3.10's recipe (undo_case, linked to nothing),
    and the buyer entered that undo. Taking the tick back writes nothing and gives "undone", as for a put-back (third review round,
    K26); so does an undo marked seen. While the undo is still open, K26 does not apply."""
    first = logged(tmp_path, tmp_path / "first", case(18), price("12.70"))
    second = logged(tmp_path, tmp_path / "second", case(19), price("12.70"))
    mark(tmp_path, first + second, "entered", NOW)
    back = undone(tmp_path, tmp_path / "second", *second, now=LATER)
    assert states(tmp_path) == [(first + second, "entered"), (back, "open")]
    for what in ("entered", "reopened", "seen"):
        mark(tmp_path, back, what, LATER)
        if what == "reopened":
            continue
        before = (tmp_path / MARKS_FILE).read_bytes()
        assert reopen(tmp_path, first + second, LATER) == "undone", what
        assert (tmp_path / MARKS_FILE).read_bytes() == before and states(tmp_path) == [(first + second, "entered"), (back, what)]


def test_reopen_counts_only_an_undo_that_belongs_to_the_run_of_the_entry(tmp_path):
    """K26 counts an undo row of the entry's own case that belongs to the entry's run by the undo rule, not any undo of that case at
    the place. Case 18 asked 12.70 and later 12.90, both ticked, and the undo of 12.90 (MANUAL 3.10's recipe) was entered: taking
    back the tick of 12.70 says that a newer value came in ("replaced"), while taking back that of 12.90 gives "undone". The same for
    an entered put-back, which is an undo itself and has no undo of its own (third review round, the model lens)."""
    first = logged(tmp_path, tmp_path / "case", case(18), price("12.70"))
    mark(tmp_path, first, "entered", NOW)
    second = logged(tmp_path, tmp_path / "case", case(18), price("12.90", old="12.70"))
    mark(tmp_path, second, "entered", NOW)
    back = undone(tmp_path, tmp_path / "case", *second, now=LATER)            # linked to nothing
    mark(tmp_path, back, "entered", LATER)
    assert states(tmp_path) == [(first, "entered"), (second, "entered"), (back, "entered")]
    before = (tmp_path / MARKS_FILE).read_bytes()
    assert reopen(tmp_path, first, LATER) == "replaced" and reopen(tmp_path, second, LATER) == "undone"
    assert (tmp_path / MARKS_FILE).read_bytes() == before
    other = tmp_path / "other"
    first = logged(other, other / "case", case(18), price("12.70"))
    mark(other, first, "entered", NOW)
    put = undone(other, other / "case", *first)
    link_put_back(other, put[0], first[0], NOW)
    mark(other, put, "entered", NOW)
    second = logged(other, other / "case", case(18), price("12.90"))
    mark(other, second, "entered", NOW)
    back = undone(other, other / "case", *second, now=LATER)
    mark(other, back, "entered", LATER)
    assert states(other) == [(first, "entered"), (put, "entered"), (second, "entered"), (back, "entered")]
    before = (other / MARKS_FILE).read_bytes()
    assert reopen(other, put, LATER) == "replaced" and reopen(other, first, LATER) == "undone"
    assert (other / MARKS_FILE).read_bytes() == before


# --- Task 43: the values that the marks say Exact holds, laid over a version of the purchase order ---

def held(folder, po: str = "250108", tryout: bool = False) -> dict:
    """entered_values of the PO, each as the brief's pair, by the kind and the line id of its change: (the value, the time of the
    mark). Of two changes that carry one line id, the one marked last is in it."""
    return {(one.change.kind, one.change.line_id): one[:2] for one in entered_values(folder, po, tryout)}


def record(line_id: str, field: str, was: str | None, entered: str, at: str = AT, by: str = "buyer") -> dict:
    """One value that as_entered replaced, as step 03_entered of a case keeps it."""
    return {"line_id": line_id, "field": field, "was": was, "entered": entered, "marked_at": at, "by": by}


def test_the_entered_values_of_a_po_with_their_times(tmp_path):
    """entered_values gives the value that the marks say Exact holds for each line and kind of a PO, with the time of the mark and
    who made it: the new value of the last change of its place that is entered or seen, as changes() judges them (amendment 3). A
    change that is open, dropped, replaced or undone never counts, nor does an article price or a change of another PO (rule 4); a
    newer value entered after it takes its place, and a tick taken back counts no longer."""
    entered = asked(tmp_path, case(18), price("12.70"))
    mark(tmp_path, entered, "entered", NOW)
    newer = asked(tmp_path, case(19), price("12.90", old="12.70"))               # a newer price, still open: the entered one stays
    seen = asked(tmp_path, case(18), date("2026-11-20"))
    mark(tmp_path, seen, "seen", LATER, by="version 2")
    mark(tmp_path, asked(tmp_path, case(18), quantity("60", old="50", line="2")), "dropped", NOW)
    asked(tmp_path, case(18), price("0.45", old="0.42", line="2"))               # replaced by the next row, which stays open
    asked(tmp_path, case(19), price("0.47", old="0.42", line="2"))
    mark(tmp_path, asked(tmp_path, case(18), article("12.70")), "entered", NOW)   # an article price is no value of a PO line
    other = asked(tmp_path, "250111_20261118T090000", price("4.10", old="4.00", po="250111", line="3"))
    mark(tmp_path, other, "entered", LATER)
    cancelled = logged(tmp_path, tmp_path / "case", case(20), quantity("5", old="3", line="3"))
    undone(tmp_path, tmp_path / "case", *cancelled)                               # undone before it was entered
    found = {(one.change.kind, one.change.line_id): one for one in entered_values(tmp_path, "250108")}
    assert [one.change.kind for one in entered_values(tmp_path, "250108")] == ["po_line_price", "po_line_date"]     # as they were marked
    assert {place: (one.value, one.at, one.by) for place, one in found.items()} == {
        ("po_line_price", "1"): ("12.70", NOW, "buyer"), ("po_line_date", "1"): ("2026-11-20", LATER, "version 2")}
    assert found[("po_line_price", "1")][:2] == ("12.70", NOW) and found[("po_line_price", "1")].change.ids == entered
    assert found[("po_line_price", "1")].at == datetime.fromisoformat(found[("po_line_price", "1")].change.marked_at)
    assert all(one.change in changes(tmp_path)[0] for one in found.values())     # judged from the PO's own rows as from the whole list
    assert held(tmp_path, "250111") == {("po_line_price", "3"): ("4.10", LATER)}
    assert held(tmp_path, "250199") == {} and held(tmp_path / "nothing prepared") == {}
    mark(tmp_path, newer, "entered", LATER)                                       # the newer price entered: the last one decides
    assert held(tmp_path)[("po_line_price", "1")] == ("12.90", LATER)
    assert reopen(tmp_path, newer, LATER) == "open" and held(tmp_path)[("po_line_price", "1")] == ("12.70", NOW)
    assert reopen(tmp_path, entered, LATER) == "replaced" and held(tmp_path)[("po_line_price", "1")] == ("12.70", NOW)


def test_a_value_marked_before_the_version_was_filed_is_not_applied(tmp_path):
    """Rule 3: an entered or seen value is laid over a version that was filed before the mark, and a version filed after the mark,
    or in the very second of it, is taken as it is: the print that Exact made after the buyer entered the change is the order
    after the change. An open change counts for nothing. A change the aid saw in a newer version is laid over the older version,
    while the version it was seen in, which shows the value already, stays as it is."""
    store = store_of(tmp_path)
    first = filed(store, 1, line("KM-0801", "3", "12.50", date="2026-11-16", basis="1"), line("KM-0802", "50", "0.42", basis="1"))
    ids = asked(tmp_path, case(18), price("12.70"))
    assert as_entered(first, tmp_path, store.filed_at("250108", "v1"), None) == (first, [])        # asked, not entered
    mark(tmp_path, ids, "entered", NOW)
    laid, replaced = as_entered(first, tmp_path, store.filed_at("250108", "v1"), None)
    assert replaced == [record("1", "price", "12.50", "12.70")]
    assert laid.document.lines[0].unit_price == DocValue(value="12.70") and laid.document.lines[1] == first.document.lines[1]
    assert laid.document.lines[0].model_dump(exclude={"unit_price"}) == first.document.lines[0].model_dump(exclude={"unit_price"})
    assert laid.model_dump(exclude={"document", "total"}) == first.model_dump(exclude={"document", "total"})
    assert first.document.lines[0].unit_price.value == "12.50" and laid.total == Decimal("59.10")           # the version given is not changed
    second = filed(store, 2, line("KM-0801", "3", "12.50", date="2026-11-16", basis="1"), line("KM-0802", "50", "0.42", basis="1"))
    assert store.filed_at("250108", "v2").timestamp() == AFTER.timestamp() > NOW.timestamp()        # printed after the tick
    result = as_entered(second, tmp_path, store.filed_at("250108", "v2"), None)
    assert result[0] is second and result[1] == []
    assert as_entered(first, tmp_path, NOW, None)[0] is first                                         # filed in the second of the mark
    assert as_entered(first, tmp_path, NOW - timedelta(seconds=1), None)[1] == replaced
    seen = tmp_path / "seen"
    seen_store = store_of(seen)
    old = filed(seen_store, 1, line("KM-0801", "3", "12.50", basis="1"))
    asked(seen, case(18), price("12.70"))
    new = filed(seen_store, 2, line("KM-0801", "3", "13.37", basis="1", discount="5"))                # 12.70 net, filed on 19 November 7:00
    assert reconcile(seen, seen_store, seen / "cases", LATER) == 1                                    # seen at 8:00, after it was filed
    assert as_entered(old, seen, seen_store.filed_at("250108", "v1"), None)[1] == [record("1", "price", "12.50", "12.70", LATER_AT, "version 2")]
    result = as_entered(new, seen, seen_store.filed_at("250108", "v2"), None)
    assert result[0] is new and result[1] == []


def test_the_net_price_rule_and_the_total(tmp_path):
    """Rule 5: an entered price is written over the line's net price when the line states a net price or a discount, and else over
    its unit price; a date over the delivery date and a quantity over the quantity. The value written has no text and no source and
    is not uncertain, whatever the value before it was. The total, and whether it is above the authorization limit given, are worked
    out again as the store works them out: over the unit prices, with a line that prints no price basis counted per one where the
    stored total counted it so, and unknown where it was unknown."""
    cited = SourceRef(page=1, line=9, text="5 KM-0805 Sluitring M10 100 st 1,00 1")
    unsure = line("KM-0805", "100", basis="1").model_copy(update={"unit_price": DocValue(value="1.00", text="1,00", source=cited, uncertain=True)})
    lines = [line("KM-0801", "3", "12.50", date="2026-11-16", basis="1"),                  # a unit price alone
             line("KM-0802", "50", "0.50", basis="1", discount="10"),                      # a unit price less a discount: 0.45 net
             line("KM-0803", "10", "45.00", basis="100", net_price="44.00"),               # a net price beside the unit price
             line("KM-0804", "2", "8.00", date="2026-11-16", basis="1"), unsure]
    po = filed(store_of(tmp_path), 1, *lines)
    po = po.model_copy(update={"total": po_total(po.document), "above_authorization": False})
    assert po.total == Decimal("183.000")                                                  # 37.50 + 25.00 + 4.50 + 16.00 + 100.00
    ids = asked(tmp_path, case(18), price("12.70"), price("0.47", old="0.45", line="2"), price("44.50", old="44.00", line="3"),
                date("2026-11-20", line="4"), quantity("5", old="2", line="4"), price("1.10", old="1.00", line="5"))
    assert mark(tmp_path, ids, "entered", NOW) == 6
    laid, replaced = as_entered(po, tmp_path, BEFORE, Decimal("200"))
    one, two, three, four, five = laid.document.lines
    assert one.unit_price == DocValue(value="12.70") and one.net_price == DocValue()
    assert two.net_price == DocValue(value="0.47") and (two.unit_price, two.discount_pct) == (lines[1].unit_price, lines[1].discount_pct)
    assert three.net_price == DocValue(value="44.50") and three.unit_price == lines[2].unit_price
    assert (four.delivery_date, four.quantity) == (DocValue(value="2026-11-20"), DocValue(value="5")) and four.unit_price == lines[3].unit_price
    assert five.unit_price == DocValue(value="1.10") and not five.unit_price.uncertain and five.unit_price.source is None
    assert [net_unit_price(one) for one in laid.document.lines] == [Decimal("12.70"), Decimal("0.47"), Decimal("44.50"), Decimal("8.00"),
                                                                   Decimal("1.10")]
    assert replaced == [record("1", "price", "12.50", "12.70"), record("2", "price", "0.45", "0.47"), record("3", "price", "44.00", "44.50"),
                        record("4", "delivery_date", "2026-11-16", "2026-11-20"), record("4", "quantity", "2", "5"),
                        record("5", "price", None, "1.10")]                                 # an uncertain value shows nothing
    assert (laid.total, laid.above_authorization) == (Decimal("217.600"), True)             # 38.10 + 25.00 + 4.50 + 40.00 + 110.00
    assert as_entered(po, tmp_path, BEFORE, Decimal("217.60"))[0].above_authorization is False
    assert as_entered(po, tmp_path, BEFORE, None)[0].above_authorization is None
    assert (po.total, po.above_authorization, po.document.lines[0].unit_price.value) == (Decimal("183.000"), False, "12.50")    # not changed
    bare = tmp_path / "bare"
    printed = filed(store_of(bare), 1, line("KM-0801", "3", "12.50"), line("KM-0802", "50", "0.42"))     # no price basis printed
    mark(bare, asked(bare, case(18), price("12.70")), "entered", NOW)
    per_piece = printed.model_copy(update={"total": po_total(printed.document, "1"), "above_authorization": False})
    assert per_piece.total == Decimal("58.50") and printed.total is None
    counted = as_entered(per_piece, bare, BEFORE, Decimal("59"))[0]
    assert (counted.total, counted.above_authorization) == (Decimal("59.10"), True)
    unknown = as_entered(printed, bare, BEFORE, Decimal("59"))[0]
    assert (unknown.total, unknown.above_authorization, unknown.document.lines[0].unit_price.value) == (None, None, "12.70")


def test_an_entered_put_back_gives_the_old_value_again(tmp_path):
    """Rule 4: a put-back that is entered puts the old value back, so that the version filed before it is compared as it is again,
    and a version that showed yet another value gets the value put back. A put-back that is still open, or that the buyer dropped,
    leaves the entered value."""
    store = store_of(tmp_path)
    first = filed(store, 1, line("KM-0801", "3", "12.50", basis="1"))
    case_root = tmp_path / "case"
    ids = logged(tmp_path, case_root, case(18), price("12.70"))
    mark(tmp_path, ids, "entered", NOW)
    back = undone(tmp_path, case_root, *ids, now=LATER)
    link_put_back(tmp_path, back[0], ids[0], LATER)                               # as "Terugdraaien in Exact" prepares it: open
    assert states(tmp_path) == [(ids, "entered"), (back, "open")]
    assert as_entered(first, tmp_path, BEFORE, None)[1] == [record("1", "price", "12.50", "12.70")]
    mark(tmp_path, back, "dropped", LATER)
    assert as_entered(first, tmp_path, BEFORE, None)[1] == [record("1", "price", "12.50", "12.70")]
    mark(tmp_path, back, "reopened", LATER)
    assert mark(tmp_path, back, "entered", LATER) == 1                            # the old price is back in Exact
    assert held(tmp_path) == {("po_line_price", "1"): ("12.50", LATER)}
    result = as_entered(first, tmp_path, BEFORE, None)
    assert result[0] is first and result[1] == []
    other = filed(store, 2, line("KM-0801", "3", "12.40", basis="1"), at=BEFORE)
    laid, replaced = as_entered(other, tmp_path, BEFORE, None)
    assert replaced == [record("1", "price", "12.40", "12.50", LATER_AT)] and laid.document.lines[0].unit_price.value == "12.50"


def cases_of(tmp_path) -> Path:
    """The folder of the list under a cases root of its own, <tmp_path>/cases/prepared, where compared() makes the case folders: the
    place of the list that as_entered reads the cases of its changes from."""
    return tmp_path / "cases" / "prepared"


def test_an_entered_value_is_laid_over_the_line_the_list_finds(tmp_path):
    """Amendment 3 and rule 6: the line of an entered value in a version is found as reconcile finds a change's line: by its line
    id when the item codes agree or one is unknown, the code of the change's line coming from step 01 of its case, else by the item
    code when exactly one line of the version holds it. So a value entered for a case that was compared with one version is laid
    over the right line of a version whose lines are numbered otherwise. A value whose line the version does not have, or that two
    lines of it could be, is not applied."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, line("KM-0801", "3", "12.50", basis="1"), line("KM-0802", "50", "0.42", basis="1"))
    compared(tmp_path, case(18), first)
    mark(prepared, asked(prepared, case(18), price("12.70")), "entered", NOW)
    swapped = filed(store, 2, line("KM-0802", "50", "0.42", basis="1"), line("KM-0801", "3", "12.50", basis="1"), at=BEFORE)
    laid, replaced = as_entered(swapped, prepared, BEFORE, None)
    assert replaced == [record("2", "price", "12.50", "12.70")]                    # the line of KM-0801, which version 2 numbers 2
    assert [one.unit_price.value for one in laid.document.lines] == ["0.42", "12.70"]
    twice = filed(store, 3, line("KM-0802", "3", "12.50", basis="1"), line("KM-0801", "3", "12.50", basis="1"),
                  line("KM-0801", "3", "12.50", basis="1"), at=BEFORE)
    result = as_entered(twice, prepared, BEFORE, None)
    assert result[0] is twice and result[1] == []                                  # two lines hold the code and nothing tells them apart
    without = filed(store, 4, line("KM-0802", "50", "0.42", basis="1"), line("KM-0809", "3", "12.50", basis="1"), at=BEFORE)
    assert as_entered(without, prepared, BEFORE, None) == (without, [])           # the version has no line of KM-0801
    unknown = tmp_path / "unknown" / "prepared"                                    # no case folder: the code of the change is unknown
    mark(unknown, asked(unknown, case(18), price("12.70")), "entered", NOW)
    assert as_entered(swapped, unknown, BEFORE, None)[1] == [record("1", "price", "0.42", "12.70")]      # by the line id alone
    assert as_entered(filed(store_of(tmp_path / "short"), 1, line("KM-0801", "3", "12.50", basis="1")), unknown, BEFORE, None)[1] == [
        record("1", "price", "12.50", "12.70")]
    lacking = asked(unknown, case(18), price("5.10", old="5.00", line="9"))
    mark(unknown, lacking, "entered", NOW)
    assert as_entered(swapped, unknown, BEFORE, None)[1] == [record("1", "price", "0.42", "12.70")]      # no line 9: rule 6


def test_rows_under_two_numberings_that_name_one_line_are_one_place(tmp_path):
    """A case compared with a version that numbers the lines otherwise asks its change under another line id. Both rows name one
    line of the order, the one that holds their item code, so they are of one place (the review round of Task 43, W7): the change
    marked last is the value of that line, whichever version is compared (amendment 3)."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, line("KM-0801", "3", "12.50", basis="1"), line("KM-0802", "50", "0.42", basis="1"))
    other = filed(store, 2, line("KM-0802", "50", "0.42", basis="1").model_copy(update={"line_id": "10"}),
                  line("KM-0801", "3", "12.50", basis="1").model_copy(update={"line_id": "20"}), at=BEFORE)
    compared(tmp_path, case(18), first)
    compared(tmp_path, case(19), other)
    mark(prepared, asked(prepared, case(18), price("12.70")), "entered", NOW)
    mark(prepared, asked(prepared, case(19), price("12.90", old="12.70", line="20", version=2)), "entered", LATER)
    assert held(prepared) == {("po_line_price", "20"): ("12.90", LATER)}            # one place: its last entered change
    assert len({place_of(change) for change in changes(prepared)[0]}) == 1
    assert as_entered(first, prepared, BEFORE, None)[1] == [record("1", "price", "12.50", "12.90", LATER_AT)]
    assert as_entered(other, prepared, BEFORE, None)[1] == [record("20", "price", "12.50", "12.90", LATER_AT)]
    assert as_entered(first, prepared, AFTER, None)[1] == [record("1", "price", "12.50", "12.90", LATER_AT)]     # filed between the ticks
    assert as_entered(first, prepared, LATER, None) == (first, [])                 # filed after both


def tryout_document(folder: Path) -> Path:
    """A made-up document whose first row is the try-out's (aid/tryout_mark.py), for the input of a case made from a try-out."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "PROEF orderbevestiging.pdf"
    path.write_bytes(pdf_bytes([[Row([Text(0, FIRST_ROW, BOLD, 14)]), Row([Text(0, "Orderbevestiging")])]], "made-up try-out"))
    return path


def tried(tmp_path, case_id: str, *versions: PoVersion) -> Path:
    """compared(), for a case made from a try-out: its document is a try-out's."""
    root = tmp_path / "cases"
    made = open_case(root, "confirmation", [tryout_document(tmp_path / "made")], case_id.partition("_")[0],
                     datetime.strptime(case_id.partition("_")[2], "%Y%m%dT%H%M%S"))
    assert made.case_id == case_id
    made.write_step(1, "po_versions", {"po_number": made.po_number, "versions": [version.model_dump(mode="json") for version in versions]})
    return root


def test_the_marks_of_a_try_out_count_only_in_a_case_made_from_a_try_out(tmp_path):
    """Amendment 4: the marks of a change that cases made from a try-out asked count only in a case made from a try-out; the marks
    of every other change count in every case, and in a case made from a try-out the change marked last decides for a line, of two
    marked in the same second the one whose mark stands later in marks.jsonl. So nothing entered for a try-out is ever laid over the
    order in a supplier's own case."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, line("KM-0801", "3", "12.50", basis="1"), line("KM-0802", "50", "0.42", basis="1"))
    compared(tmp_path, case(18), first)
    tried(tmp_path, case(19), first)
    own = asked(prepared, case(18), price("12.70"))
    made_up = asked(prepared, case(19), price("12.63"), quantity("60", old="50", line="2"))
    assert [(change.ids, change.tryout) for change in changes(prepared)[0]] == [(own, False), (made_up[:1], True), (made_up[1:], True)]
    mark(prepared, made_up, "entered", NOW)
    assert held(prepared) == {} and as_entered(first, prepared, BEFORE, None) == (first, [])
    assert held(prepared, tryout=True) == {("po_line_price", "1"): ("12.63", NOW), ("po_line_quantity", "2"): ("60", NOW)}
    assert as_entered(first, prepared, BEFORE, None, tryout=True)[1] == [record("1", "price", "12.50", "12.63"), record("2", "quantity", "50", "60")]
    mark(prepared, own, "entered", LATER)                                         # the supplier's own change, entered after it
    assert held(prepared) == {("po_line_price", "1"): ("12.70", LATER)}
    assert held(prepared, tryout=True) == {("po_line_price", "1"): ("12.70", LATER), ("po_line_quantity", "2"): ("60", NOW)}
    assert as_entered(first, prepared, BEFORE, None)[1] == [record("1", "price", "12.50", "12.70", LATER_AT)]
    again = asked(prepared, case(19), price("12.76", old="12.63"))                # a second try-out, entered in the same second
    mark(prepared, again, "entered", LATER)
    assert held(prepared, tryout=True)[("po_line_price", "1")] == ("12.76", LATER) and held(prepared)[("po_line_price", "1")] == ("12.70", LATER)
    earlier = tmp_path / "earlier"                                                # the same two, the try-out's tick written first
    compared(earlier, case(18), first)
    tried(earlier, case(19), first)
    both = asked(cases_of(earlier), case(19), price("12.63")) + asked(cases_of(earlier), case(18), price("12.70"))
    assert mark(cases_of(earlier), both, "entered", NOW) == 2
    assert held(cases_of(earlier), tryout=True) == {("po_line_price", "1"): ("12.70", NOW)} == held(cases_of(earlier))


def test_a_price_per_another_basis_and_a_value_that_is_no_value_are_not_laid_over(tmp_path):
    """A price is a price per the price basis of the line its case was compared with (step 01 of the case). A version whose line
    states another basis is taken as it is for that price, as for a line it does not have; a line without a basis counts per one,
    and without the case the basis is taken as the same. A value that is no number, or no delivery date, in normal form is not laid
    over either, so that no row written by hand fails a case."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    per_hundred = filed(store, 1, line("KM-0801", "300", "12.50", basis="100"), line("KM-0802", "50", "0.42"))
    compared(tmp_path, case(18), per_hundred)
    mark(prepared, asked(prepared, case(18), price("12.70"), price("0.45", old="0.42", line="2")), "entered", NOW)
    per_piece = filed(store, 2, line("KM-0801", "300", "0.125", basis="1"), line("KM-0802", "50", "0.42", basis="1"), at=BEFORE)
    assert as_entered(per_hundred, prepared, BEFORE, None)[1] == [record("1", "price", "12.50", "12.70"), record("2", "price", "0.42", "0.45")]
    assert as_entered(per_piece, prepared, BEFORE, None)[1] == [record("2", "price", "0.42", "0.45")]     # line 2: no basis is per one
    loose = tmp_path / "loose" / "prepared"                                        # the same rows without their case
    mark(loose, asked(loose, case(18), price("12.70")), "entered", NOW)
    assert as_entered(per_piece, loose, BEFORE, None)[1] == [record("1", "price", "0.13", "12.70")]
    odd = tmp_path / "odd" / "prepared"
    ids = asked(odd, case(18), price("12,70"), date("16-11-2026"), quantity("vijf"), quantity("60", old="50", line="2"))
    assert mark(odd, ids, "entered", NOW) == 4
    dated = filed(store_of(tmp_path / "odd"), 1, line("KM-0801", "3", "12.50", date="2026-11-10", basis="1"), line("KM-0802", "50", "0.42"))
    assert as_entered(dated, odd, BEFORE, None)[1] == [record("2", "quantity", "50", "60")]


def test_a_list_that_cannot_be_read_raises_and_a_line_that_cannot_is_passed_over(tmp_path, monkeypatch):
    """Amendment 5: while prepared_changes.csv or marks.jsonl cannot be read, entered_values and as_entered raise the OSError, so
    that the caller knows that the marks did not count; a line of either file that cannot be read is passed over as changes()
    passes it over, and is no such failure."""
    first = filed(store_of(tmp_path), 1, line("KM-0801", "3", "12.50", basis="1"))
    mark(tmp_path, asked(tmp_path, case(18), price("12.70")), "entered", NOW)
    with open(tmp_path / PREPARED_FILE, "ab") as f:
        f.write(b"9f0e1d2c,250108_20261118T110000,250108,1,3,,po_line_pr")        # cut off while it is written
    with open(tmp_path / MARKS_FILE, "ab") as f:
        f.write(b'{"entry_id": "x", "mark": "ente')
    assert changes(tmp_path)[1] == 2 and as_entered(first, tmp_path, BEFORE, None)[1] == [record("1", "price", "12.50", "12.70")]
    real = pathlib.Path.read_bytes
    for name in (PREPARED_FILE, MARKS_FILE):
        def read_bytes(self, name=name):
            if self.name == name:
                raise PermissionError(13, "The process cannot access the file because it is being used by another process", str(self))
            return real(self)
        monkeypatch.setattr(pathlib.Path, "read_bytes", read_bytes)
        with pytest.raises(PermissionError):
            as_entered(first, tmp_path, BEFORE, None)
        with pytest.raises(PermissionError):
            entered_values(tmp_path, "250108")
        monkeypatch.setattr(pathlib.Path, "read_bytes", real)


def test_when_a_version_or_a_look_was_filed(tmp_path):
    """filed_of gives when the store filed the version a case is compared with: a PDF version by its own number, also an older one,
    and the PO's newest look from an export; None for a version that the store does not hold as it is given."""
    store = store_of(tmp_path)
    first = filed(store, 1, line("KM-0801", "3", "12.50", basis="1"))
    second = filed(store, 2, line("KM-0801", "3", "12.70", basis="1"))
    assert filed_of(store, first).timestamp() == BEFORE.timestamp() and filed_of(store, second).timestamp() == AFTER.timestamp()
    export = tmp_path / "export.csv"
    export.write_text("po_number,line_id,item_code,quantity,unit_price,price_basis\n250108,1,KM-0801,3,12.70,1\n", encoding="utf-8")
    [look] = store.from_export(export, {})
    when = datetime(2026, 11, 20, 9, 0).timestamp()
    os.utime(store.root / "250108" / "e1" / "po.json", (when, when))
    assert store.as_it_stands("250108") == look and filed_of(store, look).timestamp() == when
    assert filed_of(store, first).timestamp() == BEFORE.timestamp()
    other = first.model_copy(update={"document": first.document.model_copy(update={"lines": []})})    # version 1 as the store does not hold it
    assert filed_of(store, other) is None and filed_of(store_of(tmp_path / "empty"), first) is None


# --- the review round of Task 43: a value belongs to a line of the order (W1, W2, W7), a newer print ends a tick (W9), a change
# the supplier withdrew (W4), a list that cannot be understood (W5), the times of marks (W12) ---

def look(store: PoStore, n: int, *lines: DocLine, po: str = "250108", at: datetime = BEFORE) -> PoVersion:
    """The n-th look at the PO from an export with these lines, numbered 1, 2, 3 unless a line has a number of its own, filed into
    the store as it keeps a look (its po.json, with the export's path), at `at`: the look as the store gives it."""
    numbered = [one if one.line_id else one.model_copy(update={"line_id": str(number)}) for number, one in enumerate(lines, 1)]
    document = Document(kind="po", po_reference=DocValue(value=po, text=po), lines=numbered)
    version = PoVersion(po_number=po, version=1, version_date=None, pdf_path=store.root / f"export_{n}.csv", document=document,
                        supplier="S08", total=None, above_authorization=None)
    folder = store.root / po / f"e{n}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "po.json").write_text(version.model_dump_json(indent=1) + "\n", encoding="utf-8")
    os.utime(folder / "po.json", (at.timestamp(), at.timestamp()))
    return store.snapshots(po)[-1]


A, B, C = (line("KM-0801", "3", "12.50", basis="1"), line("KM-0802", "50", "0.42", basis="1"), line("KM-0803", "4", "38.75", basis="1"))


def test_two_lines_that_carry_one_line_id_under_two_numberings_both_keep_their_value(tmp_path):
    """W1 (the review's finding 1): the PDF numbers the lines KM-0801, KM-0802, KM-0803 as 1, 2, 3; an export without line ids
    lists KM-0803 first, so its line 1 is another line. A cart's change of the export's line 1 and a confirmation's change of the
    PDF's line 1 are ticked: they are two places, both are entered values, and each lands on its own line of either version. Before,
    the tick made last pushed the other out. The same with two PDF versions numbered by place after a line left the order."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    pdf = filed(store, 1, A, B, C)
    export = look(store, 1, C, A, B)
    compared(tmp_path, case(18), export)
    compared(tmp_path, case(19), pdf)
    cart = asked(prepared, case(18), price("39.00", old="38.75", line="1"))
    mark(prepared, cart, "entered", NOW)
    confirmation = asked(prepared, case(19), price("12.70", line="1"))
    mark(prepared, confirmation, "entered", LATER)
    assert [(one.change.ids, one.value) for one in entered_values(prepared, "250108")] == [(cart, "39.00"), (confirmation, "12.70")]
    assert len({place_of(change) for change in changes(prepared)[0]}) == 2
    assert as_entered(pdf, prepared, BEFORE, None)[1] == [record("1", "price", "12.50", "12.70", LATER_AT), record("3", "price", "38.75", "39.00")]
    assert as_entered(export, prepared, BEFORE, None)[1] == [record("1", "price", "38.75", "39.00"), record("2", "price", "12.50", "12.70", LATER_AT)]
    assert states(prepared) == [(cart, "entered"), (confirmation, "entered")]
    # two PDF versions: version 2 is printed after KM-0801 left the order, and numbers KM-0802 as 1 and KM-0803 as 2
    second = filed(store, 2, B, C, po="250109", at=BEFORE)
    first = filed(store, 1, A, B, C, po="250109")
    compared(tmp_path, "250109_20261118T090000", first)
    compared(tmp_path, "250109_20261119T090000", second)
    mark(prepared, asked(prepared, "250109_20261118T090000", price("0.45", old="0.42", po="250109", line="2")), "entered", NOW)
    mark(prepared, asked(prepared, "250109_20261119T090000", price("39.14", old="38.75", po="250109", line="2", version=2)), "entered", LATER)
    assert as_entered(second, prepared, BEFORE, None)[1] == [record("1", "price", "0.42", "0.45"), record("2", "price", "38.75", "39.14", LATER_AT)]


FIRST, SECOND = (line("KM-0804", "2", "7.00", date="2026-11-16", basis="1", desc="first delivery"),
                 line("KM-0804", "6", "7.00", date="2026-11-23", basis="1", desc="second delivery"))


def test_a_value_is_not_laid_over_a_line_it_cannot_be_told_from_another(tmp_path):
    """W2 and, since the second round, W17 (the check's finding 2): two lines of the order hold one item code (two deliveries of
    one article), and the export lists them the other way round than the PDF. Neither the item code nor a line id tells the two
    apart across prints. A change is laid over the one line of that article that agrees with the line it was asked for in its
    quantity, its delivery date and its unit price, the changed field apart: the same delivery, under whatever number. The rows of
    the two deliveries are places of their own, and a print contradicts the change of the delivery it shows otherwise."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    pdf = filed(store, 1, A, FIRST, SECOND)                                       # 2 = the first delivery, 3 = the second
    export = look(store, 1, A, SECOND, FIRST)                                     # 2 = the second delivery, 3 = the first
    compared(tmp_path, case(18), export)
    compared(tmp_path, case(19), pdf)
    cart = asked(prepared, case(18), price("6.93", old="7.00", line="2"))         # the second delivery, as the export numbers it
    mark(prepared, cart, "entered", NOW)
    assert as_entered(export, prepared, BEFORE, None)[1] == [record("2", "price", "7.00", "6.93")]
    assert as_entered(pdf, prepared, BEFORE, None)[1] == [record("3", "price", "7.00", "6.93")]      # the second delivery, never the first
    moved = asked(prepared, case(19), date("2026-11-14", line="2"))               # the first delivery, as the PDF numbers it
    mark(prepared, moved, "entered", LATER)
    assert as_entered(pdf, prepared, BEFORE, None)[1] == [record("2", "delivery_date", "2026-11-16", "2026-11-14", LATER_AT),
                                                          record("3", "price", "7.00", "6.93")]
    assert as_entered(export, prepared, BEFORE, None)[1] == [record("2", "price", "7.00", "6.93"),
                                                             record("3", "delivery_date", "2026-11-16", "2026-11-14", LATER_AT)]
    same = asked(prepared, case(19), price("6.93", old="7.00", line="2"))          # the first delivery asks the same price: another line
    assert states(prepared) == [(cart, "entered"), (moved, "entered"), (same, "open")]
    assert len({place_of(change) for change in changes(prepared)[0] if change.kind == "po_line_price"}) == 2
    newer = filed(store, 2, A, FIRST, SECOND.model_copy(update={"unit_price": DocValue(value="6.50", text="6,50")}), at=LATER + timedelta(hours=1))
    assert newer.version == 2 and reconcile(prepared, store, tmp_path / "cases", LATER + timedelta(hours=2)) == 0
    assert states(prepared) == [(cart, "open"), (moved, "open"), (same, "open")]   # version 2 shows 6.50 and the old date: both ticks end


def test_the_twin_of_a_ticked_change_is_not_asked_and_an_old_row_cannot_override_a_newer_tick(tmp_path):
    """W7 (the review's finding 7): the list judges a place as the comparison finds a line. A cart asks a price under the export's
    line 30 and a confirmation asks the same under the PDF's line 3: one change, so that a tick on either takes both off the list.
    And a row under one numbering that a newer tick under the other numbering replaced does not return to the list. A row whose
    case is gone names the line of its line id and is taken with the rows of that line id when those name one line."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    pdf = filed(store, 1, A, B, C)
    export = look(store, 1, *(one.model_copy(update={"line_id": number}) for one, number in ((A, "10"), (B, "20"), (C, "30"))))
    compared(tmp_path, case(18), export)
    compared(tmp_path, case(19), pdf)
    cart = asked(prepared, case(18), price("39.22", old="38.75", line="30"))
    confirmation = asked(prepared, case(19), price("39.22", old="38.75", line="3"))
    assert states(prepared) == [(cart + confirmation, "open")]
    assert mark(prepared, cart, "entered", NOW) == 1 and states(prepared) == [(cart + confirmation, "entered")]
    older = asked(prepared, case(18), price("0.45", old="0.42", line="20"))        # KM-0802 under the export's numbering
    mark(prepared, older, "entered", NOW)
    newer = asked(prepared, case(19), price("0.42", old="0.45", line="2"))         # and back, under the PDF's
    mark(prepared, newer, "entered", LATER)
    assert reopen(prepared, older, LATER) == "replaced"                            # before: "open", and the newer tick stopped counting
    assert as_entered(pdf, prepared, BEFORE, None)[1] == [record("3", "price", "38.75", "39.22")]
    gone = asked(prepared, "250108_20261101T090000", price("0.47", old="0.42", line="2"))     # no case folder: line 2, as the PDF's rows
    assert place_of(next(change for change in changes(prepared)[0] if change.ids == gone)) == ("po_line_price", "250108", ("code", "KM-0802"), False)
    alone = asked(prepared, "250108_20261101T100000", price("13.00", line="77"))   # a line id that no known row carries
    assert place_of(next(change for change in changes(prepared)[0] if change.ids == alone))[2] == ("id", "77")


def test_a_newer_print_ends_a_tick_that_it_contradicts(tmp_path):
    """W9 (the review's findings 9 and 10): a tick is believed until a newer print shows the order. The buyer ticks 12.70 without
    entering it. A PDF filed before the tick ends nothing. One filed after it that still shows 12.50 ends the tick: the change is
    open again, the value is laid over no version, and a mark "reopened" by "version 2" says why. The buyer ticks again: that later
    tick counts, also for the print that contradicted the first. A print that shows the entered value ends nothing."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, A, B)
    compared(tmp_path, case(18), first)
    ticked = asked(prepared, case(18), price("12.70"))
    mark(prepared, ticked, "entered", NOW)
    assert contradict(prepared, store, tmp_path / "cases", LATER) == 0 and states(prepared) == [(ticked, "entered")]
    second = filed(store, 2, A, B)                                                  # filed after the tick: Exact still prints 12.50
    assert as_entered(first, prepared, BEFORE, None)[1] == [record("1", "price", "12.50", "12.70")]      # until the list is judged
    assert reconcile(prepared, store, tmp_path / "cases", LATER) == 0
    assert states(prepared) == [(ticked, "open")] and entered_values(prepared, "250108") == []
    assert marks_of(prepared)[-1] == {"entry_id": ticked[0], "mark": "reopened", "at": LATER_AT, "by": "version 2", "shown": "12.50"}
    assert as_entered(first, prepared, BEFORE, None) == (first, []) and as_entered(second, prepared, AFTER, None) == (second, [])
    assert reconcile(prepared, store, tmp_path / "cases", LATER) == 0 and len(marks_of(prepared)) == 2       # nothing more
    again = LATER + timedelta(hours=1)
    assert mark(prepared, ticked, "entered", again) == 1                            # entered now, and ticked again
    assert reconcile(prepared, store, tmp_path / "cases", again + timedelta(hours=1)) == 0 and states(prepared) == [(ticked, "entered")]
    assert as_entered(second, prepared, AFTER, None)[1] == [record("1", "price", "12.50", "12.70", again.isoformat())]
    filed(store, 3, A.model_copy(update={"unit_price": DocValue(value="12.70", text="12,70")}), B, at=again + timedelta(hours=2))
    assert reconcile(prepared, store, tmp_path / "cases", again + timedelta(hours=3)) == 0 and states(prepared) == [(ticked, "entered")]


def test_an_export_ends_a_tick_for_the_pdf_versions_too_and_a_try_outs_tick_is_left(tmp_path):
    """W9: a look from an export filed after the tick that shows another value ends the tick, by "export", and the value is then
    laid over no PDF version either; a third value is noted behind the open row as before. The tick of a try-out's change is left as
    it is, and so is one whose line the look does not have."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, A, B)
    compared(tmp_path, case(18), first)
    tried(tmp_path, case(19), first)
    ticked = asked(prepared, case(18), price("12.70"))
    tryout = asked(prepared, case(19), price("12.63"))
    missing = asked(prepared, case(18), price("40.00", old="38.75", line="3"))
    for ids in (ticked, tryout, missing):
        mark(prepared, ids, "entered", NOW)
    look(store, 1, A.model_copy(update={"unit_price": DocValue(value="12.80", text="12,80")}), B, at=AFTER)
    assert reconcile(prepared, store, tmp_path / "cases", LATER) == 0
    assert states(prepared) == [(ticked, "open"), (tryout, "entered"), (missing, "entered")]
    assert marks_of(prepared)[-1] == {"entry_id": ticked[0], "mark": "reopened", "at": LATER_AT, "by": "export", "shown": "12.80"}
    assert as_entered(first, prepared, BEFORE, None) == (first, [])
    assert as_entered(first, prepared, BEFORE, None, tryout=True)[1] == [record("1", "price", "12.50", "12.63")]
    [change] = [change for change in changes(prepared)[0] if change.ids == ticked]
    assert note_value(change, store, tmp_path / "cases") == "12.80"                 # the note of the open row: what Exact shows now


def test_an_open_change_that_the_supplier_no_longer_asks_leaves_the_list(tmp_path):
    """W4 (the review's finding 4): 12.60 is entered; a newer confirmation asks 12.70, which stands open; the newest one names 12.60
    again, the value the marks say Exact holds. withdraw takes the open change off the list with the mark "replaced", by the case
    that found it. A line the document does not confirm, a line without an entered value, the buyer's own put-back and the change
    of a try-out stay; and when the supplier asks 12.70 once more, the change is open again."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, A, B)
    compared(tmp_path, case(18), first)
    entered = asked(prepared, case(18), price("12.60"))
    mark(prepared, entered, "entered", NOW)
    later = asked(prepared, case(19), price("12.70", old="12.60"))
    other = asked(prepared, case(19), price("0.45", old="0.42", line="2"))          # no value entered for line 2
    laid = as_entered(first, prepared, BEFORE, None)[0]
    assert withdraw(laid, prepared, {("2", "price")}, LATER, "case x") == 0 and withdraw(laid, prepared, {("1", "quantity")}, LATER, "case x") == 0
    assert withdraw(laid, prepared, {("1", "price")}, LATER, "case x", tryout=True) == 0
    assert withdraw(laid, prepared, {("1", "price"), ("2", "price")}, LATER, "case 250108_20261120T090000") == 1
    assert states(prepared) == [(entered, "entered"), (later, "replaced"), (other, "open")]
    assert marks_of(prepared)[-1] == {"entry_id": later[0], "mark": "replaced", "at": LATER_AT, "by": "case 250108_20261120T090000"}
    assert [change.ids for change in open_changes(prepared)] == [other]
    again = asked(prepared, case(20), price("12.70", old="12.60"))                  # the supplier asks it once more
    assert states(prepared) == [(entered, "entered"), (later + again, "open"), (other, "open")]
    # the buyer's own put-back of an entered change stays on the list when the supplier names the entered value again
    back_root = tmp_path / "put back"
    back_prepared, back_store = cases_of(back_root), store_of(back_root)
    printed = filed(back_store, 1, A, B)
    compared(back_root, case(18), printed)
    ticked = logged(back_prepared, back_root / "cases" / case(18), case(18), price("12.60"))
    mark(back_prepared, ticked, "entered", NOW)
    [back] = undone(back_prepared, back_root / "cases" / case(18), *ticked, now=LATER)
    link_put_back(back_prepared, back, ticked[0], LATER)
    assert withdraw(as_entered(printed, back_prepared, BEFORE, None)[0], back_prepared, {("1", "price")}, LATER, "case x") == 0
    assert [change.ids for change in open_changes(back_prepared)] == [[back]]


def test_a_list_whose_first_line_lacks_the_writers_columns_cannot_be_read(tmp_path):
    """W5 (the review's finding 5): a prepared_changes.csv saved from a spreadsheet with semicolons has no line that can be read.
    That is a list that cannot be read, an OSError, and no empty list: no tick of it is taken as not made."""
    ticked = asked(tmp_path, case(18), price("12.70"))
    mark(tmp_path, ticked, "entered", NOW)
    path = tmp_path / PREPARED_FILE
    path.write_text(path.read_text(encoding="utf-8").replace(",", ";"), encoding="utf-8")
    for read in (lambda: changes(tmp_path), lambda: entered_values(tmp_path, "250108"), lambda: open_changes(tmp_path),
                 lambda: mark(tmp_path, ticked, "dropped", LATER), lambda: reopen(tmp_path, ticked, LATER)):
        with pytest.raises(ListUnread) as refused:
            read()
        assert isinstance(refused.value, OSError) and "12.70" not in str(refused.value)
    assert len(marks_of(tmp_path)) == 1                                           # nothing was written meanwhile


def test_a_mark_keeps_the_fraction_of_its_second_and_a_seen_mark_has_the_time_of_marking(tmp_path):
    """W12 (the review's finding 13): a print filed 0.3 s into a second and a tick 0.9 s into the same second: the print came
    first, so the ticked value counts for it. A mark written to the second, as before this round, is still read. A mark "seen"
    carries the time it is given, the time of marking: a version that the pass filed before the look in which the change was seen
    gets the value (the runner gives its clock's time at the call: test_runner.py)."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    second = datetime(2026, 11, 18, 10, 32, 5)
    first = filed(store, 1, A, B, at=second + timedelta(milliseconds=300))
    compared(tmp_path, case(18), first)
    ticked = asked(prepared, case(18), price("12.70"), now=BEFORE)
    mark(prepared, ticked, "entered", second + timedelta(milliseconds=900))
    assert marks_of(prepared)[-1]["at"] == "2026-11-18T10:32:05.900000"
    assert as_entered(first, prepared, filed_of(store, first), None)[1] == [record("1", "price", "12.50", "12.70", "2026-11-18T10:32:05.900000")]
    with open(prepared / MARKS_FILE, "a", encoding="utf-8") as f:                  # a tick of before this round, to the second
        f.write(json.dumps({"entry_id": ticked[0], "mark": "entered", "at": "2026-11-18T10:32:06", "by": "buyer"}) + "\n")
    assert as_entered(first, prepared, filed_of(store, first), None)[1] == [record("1", "price", "12.50", "12.70", "2026-11-18T10:32:06")]
    began = datetime(2026, 11, 19, 8, 0)                                           # a pass of the runner begins
    open_ = asked(prepared, case(18), quantity("5"), now=BEFORE)
    entered = A.model_copy(update={"unit_price": DocValue(value="12.70", text="12,70")})        # both prints show the price entered
    older = filed(store, 2, entered, B, at=began + timedelta(seconds=2))           # an older print, filed in that pass
    filed(store, 3, entered.model_copy(update={"quantity": DocValue(value="5", text="5")}), B, at=began + timedelta(seconds=3))
    assert reconcile(prepared, store, tmp_path / "cases", began + timedelta(seconds=10)) == 1       # the time of marking
    assert marks_of(prepared)[-1] == {"entry_id": open_[0], "mark": "seen", "at": "2026-11-19T08:00:10", "by": "version 3"}
    assert record("1", "quantity", "3", "5", "2026-11-19T08:00:10", "version 3") in as_entered(older, prepared, filed_of(store, older), None)[1]


def test_a_price_is_kept_off_a_line_of_another_basis_both_ways(tmp_path):
    """W11 (the review's finding 12): both halves of the price-basis guard. A price asked for a line that prints no basis counts
    per one: it is laid over a line per 1, and not over a line per 100. A price asked per 100 is not laid over a line that prints
    no basis, nor over a line per 1."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    bare = filed(store, 1, line("KM-0801", "3", "12.50"), line("KM-0802", "500", "4.20", basis="100"))
    compared(tmp_path, case(18), bare)
    mark(prepared, asked(prepared, case(18), price("12.70"), price("4.25", old="4.20", line="2")), "entered", NOW)
    per_one = filed(store, 2, line("KM-0801", "3", "12.50", basis="1"), line("KM-0802", "500", "0.042", basis="1"), at=BEFORE)
    per_hundred = filed(store, 3, line("KM-0801", "3", "1250.00", basis="100"), line("KM-0802", "500", "4.20"), at=BEFORE)
    assert as_entered(bare, prepared, BEFORE, None)[1] == [record("1", "price", "12.50", "12.70"), record("2", "price", "4.20", "4.25")]
    assert as_entered(per_one, prepared, BEFORE, None)[1] == [record("1", "price", "12.50", "12.70")]      # no basis asked: per one
    assert as_entered(per_hundred, prepared, BEFORE, None) == (per_hundred, [])     # neither: per 100 for per one, no basis for per 100


def test_what_the_list_holds_now_for_the_lines_of_a_version(tmp_path):
    """held_now, for a reader that judges an earlier comparison again (the pre-check, W3 and W19): the value the marks say Exact
    holds at each line and field of a version; whether a put-back gave it; whether a put-back of it waits on the list; and
    whether it is another value than the version's own line shows."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, A, B)
    compared(tmp_path, case(18), first)
    case_root = tmp_path / "cases" / case(18)
    ticked = logged(prepared, case_root, case(18), price("12.70"))
    assert held_now(first, prepared) == {}
    mark(prepared, ticked, "entered", NOW)
    assert held_now(first, prepared) == {("1", "price"): ("12.70", False, False, True)}
    [back] = undone(prepared, case_root, *ticked, now=LATER)
    link_put_back(prepared, back, ticked[0], LATER)
    assert held_now(first, prepared) == {("1", "price"): ("12.70", False, True, True)}    # its put-back waits
    mark(prepared, [back], "entered", LATER)
    assert held_now(first, prepared) == {("1", "price"): ("12.50", True, False, False)}  # the value from before is back


def test_a_print_ends_every_tick_of_a_place_that_it_contradicts_in_one_pass(tmp_path):
    """W9: 12.70 is ticked, then 12.90. A print filed after both shows 12.50: one pass ends both ticks, so that the older one does
    not count as what Exact holds once the newer is gone. The newest change is open again; the older one stays replaced."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, A, B)
    compared(tmp_path, case(18), first)
    older = asked(prepared, case(18), price("12.70"))
    mark(prepared, older, "entered", NOW)
    newer = asked(prepared, case(18), price("12.90", old="12.70"))
    mark(prepared, newer, "entered", NOW + timedelta(minutes=5))
    filed(store, 2, A, B)
    assert contradict(prepared, store, tmp_path / "cases", LATER) == 2
    assert states(prepared) == [(older, "replaced"), (newer, "open")] and entered_values(prepared, "250108") == []
    assert as_entered(first, prepared, BEFORE, None) == (first, [])


# --- the second review round of Task 43: a print given again is a look at the order (W16), a line of an article that stands on
# several lines is told by its values (W17), a print that cannot be matched ends the tick (W18), what a change tells of an ended
# tick (W20) and of a case that took it off the list (W21), and the rules that the check's mutants left alive (W24) ---

def given_again(store: PoStore, name: str, at: datetime, po: str = "250108") -> None:
    """Note, as the store notes it, that a print which shows the PO as the version ("v1") or the look ("e1") holds it was given
    again at `at`: the given.txt in that folder, with that time (PoStore.given_at)."""
    path = store.root / po / name / "given.txt"
    path.touch()
    os.utime(path, (at.timestamp(), at.timestamp()))


def printed(store: PoStore, n: int, *lines: DocLine, sha: str, at: datetime | None = None) -> PoVersion:
    """filed(), for a version that knows the file it was read from (its sha256), as the store's own versions do."""
    version = filed(store, n, *lines, at=at).model_copy(update={"sha256": sha * 64})
    path = store.root / "250108" / f"v{n}" / "po.json"
    when = path.stat().st_mtime
    path.write_text(version.model_dump_json(indent=1) + "\n", encoding="utf-8")
    os.utime(path, (when, when))
    return version


def ended(prepared, ids: list[str]):
    """Change.ended of the change with these entry ids."""
    return next(change for change in changes(prepared)[0] if change.ids == ids).ended


@pytest.mark.parametrize("name", ["e1", "v1"])
def test_a_print_given_again_after_a_tick_ends_it_though_it_shows_nothing_new(tmp_path, name):
    """W16 (the check's findings 1 and 6): the buyer ticks 12.70 without entering it. Exact is unchanged, so the next export has
    the rows of the look before and the store files nothing: it notes when it was given. That time counts: the tick, made before
    it, is ended by a print that still shows 12.50, and the value is laid over no version. The same with the PDF the store holds,
    given again. Before, the look kept the time it was first filed and nothing ever ended such a tick."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, A, B)
    standing = look(store, 1, A, B, at=BEFORE + timedelta(hours=1)) if name == "e1" else first     # the daily export, before the tick
    compared(tmp_path, case(18), first)
    ticked = asked(prepared, case(18), price("12.70"))
    mark(prepared, ticked, "entered", NOW)
    assert contradict(prepared, store, tmp_path / "cases", LATER) == 0 and states(prepared) == [(ticked, "entered")]
    assert as_entered(first, prepared, filed_of(store, first), None)[1] == [record("1", "price", "12.50", "12.70")]
    given_again(store, name, NOW + timedelta(hours=2))                              # the same print, given after the tick
    assert filed_of(store, standing).timestamp() == (NOW + timedelta(hours=2)).timestamp()
    assert as_entered(standing, prepared, filed_of(store, standing), None) == (standing, [])       # rule 3: marked before it was given
    assert reconcile(prepared, store, tmp_path / "cases", LATER) == 0 and states(prepared) == [(ticked, "open")]
    by = "export" if name == "e1" else "version 1"
    assert marks_of(prepared)[-1] == {"entry_id": ticked[0], "mark": "reopened", "at": LATER_AT, "by": by, "shown": "12.50"}
    assert as_entered(first, prepared, BEFORE, None) == (first, []) and entered_values(prepared, "250108") == []
    again = LATER + timedelta(hours=1)                                              # entered now, and ticked again: that tick counts
    assert mark(prepared, ticked, "entered", again) == 1 and reconcile(prepared, store, tmp_path / "cases", again + timedelta(hours=1)) == 0
    assert states(prepared) == [(ticked, "entered")]
    given_again(store, name, again + timedelta(hours=2))                            # and the print once more, after that tick
    assert contradict(prepared, store, tmp_path / "cases", again + timedelta(hours=3)) == 1 and states(prepared) == [(ticked, "open")]


def test_a_print_numbered_by_place_keeps_each_deliverys_change_on_its_own_line(tmp_path):
    """W17 (the check's finding 2, its probe renumbered_dup): two deliveries of one article are lines 4 and 5 of version 1. The
    price of the first delivery is asked against version 1. A line above them leaves the order, and version 2 numbers its lines
    by their place: the first delivery is line 3, the second line 4. The entered price belongs to the line that agrees with the
    line it was asked for in its quantity and its delivery date, so it is laid over line 3 of version 2, never over line 4, the
    other delivery, which carries the number the change was asked under. The second delivery's own change, asked against version
    2 as line 4, is a row of its own, and one tick never covers both."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    v1 = filed(store, 1, A, B, C, FIRST, SECOND)
    compared(tmp_path, case(18), v1)
    ticked = asked(prepared, case(18), price("7.10", old="7.00", line="4"))
    v2 = filed(store, 2, A, B, FIRST, SECOND, at=BEFORE + timedelta(hours=1))        # KM-0803 left the order
    mark(prepared, ticked, "entered", NOW)
    assert as_entered(v1, prepared, BEFORE, None)[1] == [record("4", "price", "7.00", "7.10")]
    assert as_entered(v2, prepared, BEFORE, None)[1] == [record("3", "price", "7.00", "7.10")]      # before: line 4, the other delivery
    assert reconcile(prepared, store, tmp_path / "cases", LATER) == 0 and states(prepared) == [(ticked, "entered")]
    compared(tmp_path, case(19), v2)
    second = asked(prepared, case(19), price("7.10", old="7.00", line="4", version=2))        # the second delivery, raised too
    assert states(prepared) == [(ticked, "entered"), (second, "open")]              # a row of its own: never "already", never one change
    assert len({place_of(change) for change in changes(prepared)[0]}) == 2
    assert mark(prepared, second, "entered", LATER) == 1
    assert as_entered(v2, prepared, BEFORE, None)[1] == [record("3", "price", "7.00", "7.10"), record("4", "price", "7.00", "7.10", LATER_AT)]
    raised = SECOND.model_copy(update={"unit_price": DocValue(value="7.10", text="7,10")})
    filed(store, 3, A, B, FIRST, raised, at=LATER + timedelta(hours=1))               # a print after both ticks: the first still at 7.00
    assert reconcile(prepared, store, tmp_path / "cases", LATER + timedelta(hours=2)) == 0
    assert states(prepared) == [(ticked, "open"), (second, "entered")]              # each judged by its own delivery


def test_an_export_numbered_by_place_keeps_a_tick_on_its_own_delivery(tmp_path):
    """W17, the class of the check's seed 31468: an export without a line column numbers the lines by their place. A cart's change
    of the second delivery is asked against such an export, where that delivery is line 4 and the first delivery line 5. A line
    above them leaves the order: in the next export the second delivery is line 3 and the first line 4. That export shows the
    entered price on the second delivery, so the tick stands; line 4, the other delivery at 7.00, is not held against it. A later
    export in which the second delivery itself is back at 7.00 ends the tick. Before, the tick was held against line 4 and ended
    by the very print that showed its value."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = look(store, 1, A, B, C, SECOND, FIRST)
    compared(tmp_path, case(18), first)
    ticked = asked(prepared, case(18), price("6.93", old="7.00", line="4"))
    mark(prepared, ticked, "entered", NOW)
    lower = SECOND.model_copy(update={"unit_price": DocValue(value="6.93", text="6,93")})
    second = look(store, 2, B, C, lower, FIRST, at=AFTER)                           # KM-0801 left the order; filed after the tick
    assert reconcile(prepared, store, tmp_path / "cases", LATER) == 0 and states(prepared) == [(ticked, "entered")]
    assert as_entered(second, prepared, BEFORE, None) == (second, [])               # it shows the value on line 3; nothing on line 4
    look(store, 3, B, C, SECOND, FIRST, at=LATER + timedelta(hours=1))
    assert reconcile(prepared, store, tmp_path / "cases", LATER + timedelta(hours=2)) == 0 and states(prepared) == [(ticked, "open")]
    assert marks_of(prepared)[-1] | {"at": ""} == {"entry_id": ticked[0], "mark": "reopened", "at": "", "by": "export", "shown": "7.00"}


def test_the_changed_field_is_left_out_of_what_tells_a_delivery(tmp_path):
    """W17, the words "the changed field apart": the change of the first delivery's price is found in a print that shows another
    price on that delivery, by its quantity and its delivery date. So an entered price is laid over a version in which that
    delivery shows a third price, and a print that shows the new price ticks the open change off as seen."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    v1 = filed(store, 1, A, FIRST, SECOND)
    compared(tmp_path, case(18), v1)
    asked_ = asked(prepared, case(18), price("7.10", old="7.00", line="2"))
    third = FIRST.model_copy(update={"unit_price": DocValue(value="7.05", text="7,05")})
    v2 = filed(store, 2, A, SECOND, third, at=BEFORE + timedelta(hours=1))           # the deliveries the other way round, the first at 7.05
    mark(prepared, asked_, "entered", NOW)
    assert as_entered(v2, prepared, BEFORE + timedelta(hours=1), None)[1] == [record("3", "price", "7.05", "7.10")]
    assert reopen(prepared, asked_, NOW + timedelta(minutes=1)) == "open"
    raised = FIRST.model_copy(update={"unit_price": DocValue(value="7.10", text="7,10")})
    filed(store, 3, A, SECOND, raised)                                              # filed after the change was asked: the new price
    assert reconcile(prepared, store, tmp_path / "cases", LATER) == 1
    assert states(prepared) == [(asked_, "seen")] and marks_of(prepared)[-1]["by"] == "version 3"


def test_a_line_that_no_value_tells_apart_is_found_in_its_own_print_alone(tmp_path):
    """W17: when no line, or more than one, of the article agrees with the line a change was asked for, the change has no line in
    that print and nothing is laid over it. In the very print its case was compared with, its line is the line it was asked for.
    Two lines that nothing tells apart keep places of their own by their print and their line id."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    twin = line("KM-0804", "2", "7.00", date="2026-11-16", basis="1")
    v1 = printed(store, 1, A, twin, twin, sha="a")
    compared(tmp_path, case(18), v1)
    upper = asked(prepared, case(18), price("7.10", old="7.00", line="2"))
    lower = asked(prepared, case(18), price("7.20", old="7.00", line="3"))
    assert states(prepared) == [(upper, "open"), (lower, "open")]
    assert {place_of(change)[2] for change in changes(prepared)[0]} == {("several", "KM-0804", "a" * 64, "2"), ("several", "KM-0804", "a" * 64, "3")}
    mark(prepared, upper, "entered", NOW)
    mark(prepared, lower, "entered", NOW)
    assert as_entered(v1, prepared, BEFORE, None)[1] == [record("2", "price", "7.00", "7.10"), record("3", "price", "7.00", "7.20")]
    v2 = printed(store, 2, A, twin, twin, sha="b", at=BEFORE)                        # another print of the same lines: more than one agrees
    assert as_entered(v2, prepared, BEFORE, None) == (v2, [])
    moved = twin.model_copy(update={"delivery_date": DocValue(value="2026-11-30", text="30-11-2026")})
    v3 = printed(store, 3, A, moved, moved, sha="c", at=BEFORE)                      # and one in which no line agrees
    assert as_entered(v3, prepared, BEFORE, None) == (v3, [])


def test_a_print_that_cannot_be_matched_ends_the_tick(tmp_path):
    """W18 (the check's finding 3): the order as it stands was given after the tick and the tick's line cannot be found in it. The
    article stands on no line (the buyer changed the article of the line in Exact): the tick is ended, and the mark says that the
    print shows no value for it. The article stands on several lines of which none agrees with the line the change was asked for
    and none shows the ticked value: ended too. When one of those lines shows the ticked value, the print does not contradict the
    tick and it stands. A change whose article no case says is left as it is: nothing tells which line it was."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    v1 = filed(store, 1, A, FIRST, SECOND)
    compared(tmp_path, case(18), v1)
    single = asked(prepared, case(18), price("12.70"))
    double = asked(prepared, case(18), price("7.10", old="7.00", line="2"))
    unknown = asked(prepared, "250108_20261101T090000", price("0.47", old="0.42", line="9"))      # its case is gone
    for ids in (single, double, unknown):
        mark(prepared, ids, "entered", NOW)
    other = A.model_copy(update={"item_code": DocValue(value="KM-0899", text="KM-0899")})
    more = FIRST.model_copy(update={"quantity": DocValue(value="3", text="3"), "unit_price": DocValue(value="7.10", text="7,10")})
    filed(store, 2, other, more, SECOND)                                            # 7.10 stands on a line of the article: no contradiction
    assert reconcile(prepared, store, tmp_path / "cases", LATER) == 0
    assert states(prepared) == [(single, "open"), (double, "entered"), (unknown, "entered")]
    assert marks_of(prepared)[-1] == {"entry_id": single[0], "mark": "reopened", "at": LATER_AT, "by": "version 2", "shown": None}
    assert ended(prepared, single) == ("version 2", None) and ended(prepared, double) is None
    fewer = FIRST.model_copy(update={"quantity": DocValue(value="3", text="3")})
    filed(store, 3, other, fewer, SECOND, at=LATER + timedelta(hours=1))             # no line agrees, and none shows 7.10
    assert reconcile(prepared, store, tmp_path / "cases", LATER + timedelta(hours=2)) == 0
    assert states(prepared) == [(single, "open"), (double, "open"), (unknown, "entered")]
    assert marks_of(prepared)[-1] | {"at": ""} == {"entry_id": double[0], "mark": "reopened", "at": "", "by": "version 3", "shown": None}
    assert ended(prepared, double) == ("version 3", None) and ended(prepared, unknown) is None


def test_a_change_tells_which_print_ended_its_tick_and_what_it_showed(tmp_path):
    """W20: the change whose tick a print ended says so (Change.ended): what a mark calls the print, and the value it showed, the
    row's old value as well as a third one. It says nothing once the buyer ticked again, for a tick the buyer took back himself,
    for a change that was never ticked, and once a newer row replaced the change: only an open change has something to tell."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, A, B)
    compared(tmp_path, case(18), first)
    ticked = asked(prepared, case(18), price("12.70"))
    never = asked(prepared, case(18), price("0.45", old="0.42", line="2"))
    assert ended(prepared, ticked) is None
    mark(prepared, ticked, "entered", NOW)
    assert ended(prepared, ticked) is None
    filed(store, 2, A, B)                                                           # after the tick: still 12.50
    assert reconcile(prepared, store, tmp_path / "cases", LATER) == 0
    assert ended(prepared, ticked) == ("version 2", "12.50") and ended(prepared, never) is None
    again = LATER + timedelta(hours=1)
    mark(prepared, ticked, "entered", again)
    assert ended(prepared, ticked) is None                                          # ticked again: no longer ended
    assert reopen(prepared, ticked, again + timedelta(minutes=1)) == "open" and ended(prepared, ticked) is None      # the buyer's own
    mark(prepared, ticked, "entered", again + timedelta(minutes=2))
    assert reopen(prepared, ticked, again + timedelta(minutes=3), by="assistant") == "open" and ended(prepared, ticked) is None
    mark(prepared, ticked, "entered", again + timedelta(minutes=4))                 # a take-back by another name is no print either
    look(store, 1, A.model_copy(update={"unit_price": DocValue(value="12.80", text="12,80")}), B, at=again + timedelta(hours=1))
    assert reconcile(prepared, store, tmp_path / "cases", again + timedelta(hours=2)) == 0
    assert ended(prepared, ticked) == ("export", "12.80")                           # a third value: named too
    newer = asked(prepared, case(19), price("12.90"), now=again + timedelta(hours=3))        # a newer document asks another value
    assert states(prepared)[0] == (ticked, "replaced") and ended(prepared, ticked) is None and ended(prepared, newer) is None


def test_a_change_tells_that_a_case_took_it_off_the_list(tmp_path):
    """W21: a change that a later case took off the list because its document named the entered value again says so
    (Change.withdrawn), which a change replaced by a newer row, a dropped one and an open one do not."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, A, B)
    compared(tmp_path, case(18), first)
    entered = asked(prepared, case(18), price("12.60"))
    mark(prepared, entered, "entered", NOW)
    later = asked(prepared, case(19), price("12.70", old="12.60"))
    other = asked(prepared, case(19), price("0.45", old="0.42", line="2"))
    dropped = asked(prepared, case(19), quantity("5"))
    mark(prepared, dropped, "dropped", NOW)
    assert withdraw(as_entered(first, prepared, BEFORE, None)[0], prepared, {("1", "price")}, LATER, "case 250108_20261120T090000") == 1
    newer = asked(prepared, case(20), price("0.47", old="0.42", line="2"))           # replaces the open 0.45 by a newer row
    found = {tuple(change.ids): change for change in changes(prepared)[0]}
    assert [(found[tuple(ids)].state, found[tuple(ids)].withdrawn) for ids in (entered, later, other, dropped, newer)] == [
        ("entered", False), ("replaced", True), ("replaced", False), ("dropped", False), ("open", False)]
    again = asked(prepared, case(20), price("12.70", old="12.60"))                   # asked once more: on the list again, not withdrawn
    [back] = [change for change in changes(prepared)[0] if change.ids == later + again]
    assert (back.state, back.withdrawn) == ("open", False)


def test_withdraw_keeps_to_its_own_order(tmp_path):
    """W24 (the check's mutant C16): a second order holds the same article on the same line, with an open change of its own. A
    case of the first order whose document names the entered value again takes off the open change of its own order alone."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, A, B)
    filed(store, 1, A, B, po="250109")
    compared(tmp_path, case(18), first)
    compared(tmp_path, "250109_20261118T090000", store.versions("250109")[0])
    mark(prepared, asked(prepared, case(18), price("12.60")), "entered", NOW)
    own = asked(prepared, case(19), price("12.70", old="12.60"))
    foreign = asked(prepared, "250109_20261118T090000", price("12.70", old="12.60", po="250109"))
    assert withdraw(as_entered(first, prepared, BEFORE, None)[0], prepared, {("1", "price")}, LATER, "case x") == 1
    assert [(change.ids, change.state) for change in changes(prepared)[0] if change.ids in (own, foreign)] == [(own, "replaced"), (foreign, "open")]


def test_a_print_per_another_price_basis_ends_no_tick(tmp_path):
    """W24 (the check's mutant C14): the tick of a price per one unit, and a print given after it that states the line per 100
    with another number: the print says nothing that can be held against the tick, which stands."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, A, B)
    compared(tmp_path, case(18), first)
    ticked = asked(prepared, case(18), price("12.70"))
    mark(prepared, ticked, "entered", NOW)
    filed(store, 2, line("KM-0801", "3", "1250.00", basis="100"), B)                # after the tick, per 100
    assert contradict(prepared, store, tmp_path / "cases", LATER) == 0 and states(prepared) == [(ticked, "entered")]
    filed(store, 3, line("KM-0801", "3", "12.50", basis="1"), B, at=LATER)          # and per one again: that print ends it
    assert contradict(prepared, store, tmp_path / "cases", LATER + timedelta(hours=1)) == 1 and states(prepared) == [(ticked, "open")]


def test_a_print_given_at_the_instant_of_the_tick_ends_nothing(tmp_path):
    """W24 (the check's mutant C11): only a print given after the tick can contradict it. One given at the very instant of the
    mark does not end the tick; one given a moment later does."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, A, B)
    compared(tmp_path, case(18), first)
    ticked = asked(prepared, case(18), price("12.70"))
    mark(prepared, ticked, "entered", NOW)
    filed(store, 2, A, B, at=NOW)
    assert contradict(prepared, store, tmp_path / "cases", LATER) == 0 and states(prepared) == [(ticked, "entered")]
    given_again(store, "v2", NOW + timedelta(milliseconds=10))
    assert contradict(prepared, store, tmp_path / "cases", LATER) == 1 and states(prepared) == [(ticked, "open")]


def test_a_line_that_prints_no_item_code_is_found_by_its_line_id(tmp_path):
    """W24 (the check's mutant C03): a print in which the line carries no item code (an export without that cell) is paired by
    the line id alone, as before the round: the entered value is laid over it, and the print can show the change made."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, A, B)
    compared(tmp_path, case(18), first)
    ticked = asked(prepared, case(18), price("12.70"))
    open_ = asked(prepared, case(18), quantity("5"))
    mark(prepared, ticked, "entered", NOW)
    bare = A.model_copy(update={"item_code": DocValue()})
    second = filed(store, 2, bare, B, at=BEFORE)
    assert as_entered(second, prepared, BEFORE, None)[1] == [record("1", "price", "12.50", "12.70")]
    filed(store, 3, bare.model_copy(update={"quantity": DocValue(value="5", text="5")}), B, at=BEFORE + timedelta(hours=1))
    filed_three = store.root / "250108" / "v3" / "po.json"
    os.utime(filed_three, (AFTER.timestamp(), AFTER.timestamp()))
    assert reconcile(prepared, store, tmp_path / "cases", LATER) == 1                # the quantity is seen on the line without a code
    assert [(change.ids, change.state) for change in changes(prepared)[0]] == [(ticked, "open"), (open_, "seen")]


def test_a_row_without_an_item_code_is_tied_by_the_rows_of_its_own_order_that_name_one_line(tmp_path):
    """W24 (the check's mutants C08 and C09): a row whose case says no item code names the line of its line id. It is taken with
    the rows of that line id whose item code is known only when those are rows of its own order and name one line: under a line
    id that names two lines (two numberings), and under a line id that only another order's rows carry, it keeps a place of its
    own."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    pdf = filed(store, 1, A, B, C)
    export = look(store, 1, C, A, B)                                               # its line 1 is KM-0803, the PDF's is KM-0801
    other = filed(store, 1, B, A, po="250109")                                     # the other order: line 1 KM-0802, line 2 KM-0801
    compared(tmp_path, case(18), export)
    compared(tmp_path, case(19), pdf)
    compared(tmp_path, "250109_20261118T090000", other)
    asked(prepared, case(18), price("39.00", old="38.75", line="1"))                # line 1 = KM-0803 by the export
    asked(prepared, case(19), price("12.70", line="1"))                             # line 1 = KM-0801 by the PDF
    asked(prepared, case(19), price("0.45", old="0.42", line="2"))                  # line 2 = KM-0802, one line
    asked(prepared, case(19), price("39.50", old="38.75", line="3"))                # line 3 = KM-0803
    asked(prepared, "250109_20261118T090000", price("12.90", po="250109", line="2"))        # the other order's line 2 = KM-0801
    two = asked(prepared, "250108_20261101T090000", price("13.00", line="1"))        # no case folder: line 1 names two lines
    one = asked(prepared, "250108_20261101T100000", price("0.47", old="0.42", line="2"))
    own = asked(prepared, "250109_20261101T090000", price("13.10", po="250109", line="2"))
    foreign = asked(prepared, "250109_20261101T100000", price("9.99", po="250109", line="3"))      # only the first order's rows carry 3
    where = {tuple(change.ids): place_of(change)[2] for change in changes(prepared)[0]}
    assert where[tuple(two)] == ("id", "1") and where[tuple(one)] == ("code", "KM-0802")
    assert where[tuple(own)] == ("code", "KM-0801") and where[tuple(foreign)] == ("id", "3")


def test_what_held_now_says_of_a_put_back_of_another_line_and_of_an_undo_without_a_link(tmp_path):
    """W24 (the check's mutants C18 and C19): a put-back that waits on the list makes the value of its own change wait, never the
    value of another line; and the value that an entered undo row gave back is a put-back also when no mark links it."""
    prepared, store = cases_of(tmp_path), store_of(tmp_path)
    first = filed(store, 1, A, B)
    compared(tmp_path, case(18), first)
    case_root = tmp_path / "cases" / case(18)
    upper = logged(prepared, case_root, case(18), price("12.70"))
    lower = logged(prepared, case_root, case(18), price("0.45", old="0.42", line="2"))
    mark(prepared, upper + lower, "entered", NOW)
    [back] = undone(prepared, case_root, *lower, now=LATER)
    link_put_back(prepared, back, lower[0], LATER)
    held = held_now(first, prepared)
    assert (held[("1", "price")].waiting, held[("2", "price")].waiting) == (False, True)
    [unlinked] = undone(prepared, case_root, *upper, now=LATER)                     # an undo row, entered, that no mark links
    mark(prepared, [unlinked], "entered", LATER + timedelta(minutes=1))
    assert held_now(first, prepared)[("1", "price")][:3] == ("12.50", True, False)
# --- the put-back of a write made in a system ---

def test_a_tick_made_before_a_write_in_the_system_does_not_hide_its_put_back(tmp_path):
    """The buyer entered 12,50 to 12,60 by hand and ticked it. A program then wrote 12,70 into Exact, where it read the 12,60: that
    write is no row of the file. Its put-back, 12,70 back to 12,60, is linked to the entry of the write. The earlier tick says
    what Exact held before the write and nothing about what it holds after it, so the put-back is asked (open), not "already";
    a tick on the put-back itself still ends it. A put-back of a row of the file is judged by the marks as before: the same
    values there are "already"."""
    earlier = asked(tmp_path, case(17), price("12.60"))
    assert mark(tmp_path, earlier, "entered", NOW) == 1
    back = asked(tmp_path, case(18), price("12.60", old="12.70"), rule_id=UNDO_RULE_ID, now=LATER)
    link_put_back(tmp_path, back[0], "the-entry-of-the-write-made-in-the-system", LATER)
    assert states(tmp_path) == [(earlier, "entered"), (back, "open")]
    [put] = open_changes(tmp_path)
    assert (put.ids, put.old_value, put.new_value, put.puts_back) == (back, "12.70", "12.60", "the-entry-of-the-write-made-in-the-system")
    assert mark(tmp_path, back, "entered", LATER) == 1                       # the tick after the put-back row ends it
    assert states(tmp_path) == [(earlier, "entered"), (back, "entered")] and open_changes(tmp_path) == []
    # the other side: a put-back of a row of the file is "already" when the marks say that Exact holds its value
    other = tmp_path / "other"
    second = None
    for day, new, old in ((16, "12.60", "12.50"), (17, "12.70", "12.60"), (18, "12.60", "12.70")):
        ids = asked(other, case(day), price(new, old=old))
        assert mark(other, ids, "entered", NOW) == 1                         # each entered in its turn: Exact holds 12,60 again
        second = ids if day == 17 else second
    undo = asked(other, case(17), price("12.60", old="12.70"), rule_id=UNDO_RULE_ID, now=LATER)
    link_put_back(other, undo[0], second[0], LATER)
    assert states(other)[-1] == (undo, "already")


def test_a_put_back_of_a_write_in_the_system_is_replaced_by_a_later_row_as_any_other(tmp_path):
    back = asked(tmp_path, case(18), price("12.60", old="12.70"), rule_id=UNDO_RULE_ID)
    link_put_back(tmp_path, back[0], "the-entry-of-the-write-made-in-the-system", NOW)
    later = asked(tmp_path, case(19), price("12.80", old="12.70"), now=LATER)
    assert states(tmp_path) == [(back, "replaced"), (later, "open")]
