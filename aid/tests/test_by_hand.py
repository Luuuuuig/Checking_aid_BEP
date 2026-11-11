"""Tests of the list of the changes the buyer enters in Exact (aid/execution/by_hand.py): one change per run of the prepared rows of one
place, its state by the marks, the marks themselves, and the lines of either file that cannot be read. The rows are prepared by the
prepared writer itself (aid/execution/writer.py), as a case or a price list prepares them."""
import json
import os
from datetime import datetime
from pathlib import Path

from aid.case import open_case
from aid.execution.by_hand import (LINE_KINDS, MARKS_FILE, changes, item_code, link_put_back, mark, note_value, open_changes, put_back_of,
                                   put_back_state, reconcile, reopen, shown_now)
from aid.execution.ops import WriteOp
from aid.execution.writer import PREPARED_FILE, UNDO_RULE_ID, PreparedWriter, WriteLog, undo_entry
from aid.po_store import PoStore, PoVersion
from aid.schema import DocLine, DocValue, Document
from aid.tests.fakes import line

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
    # two lines that hold the item code, and a line id that the version lacks with an unknown code: nothing is found
    twice = tmp_path / "twice"
    twice_store = store_of(twice)
    twice_root = compared(twice, case(18), filed(twice_store, 1, line("KM-0801", "3", "12.50", basis="1")))
    asked(twice, case(18), price("12.70"))
    filed(twice_store, 2, line("KM-0802", "3", "12.70", basis="1"), line("KM-0801", "3", "12.70", basis="1"), line("KM-0801", "1", "12.70", basis="1"))
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


def test_a_later_version_never_takes_a_mark_back(tmp_path):
    """A change that is entered, seen or dropped stays so whatever a later version shows, and mark() writes no "seen" over it."""
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
    filed(store, 3, line("KM-0801", "3", "12.50", date="2026-11-16", basis="1"), line("KM-0802", "50", "0.42", basis="1"))    # all back
    assert reconcile(tmp_path, store, tmp_path / "cases", LATER) == 0
    assert states(tmp_path) == [(seen, "seen"), (entered, "entered"), (dropped, "dropped")]
    assert mark(tmp_path, seen + entered + dropped, "seen", LATER) == 0 and (tmp_path / MARKS_FILE).read_bytes() == before


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
