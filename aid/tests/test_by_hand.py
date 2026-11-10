"""Tests of the list of the changes the buyer enters in Exact (aid/execution/by_hand.py): one change per run of the prepared rows of one
place, its state by the marks, the marks themselves, and the lines of either file that cannot be read. The rows are prepared by the
prepared writer itself (aid/execution/writer.py), as a case or a price list prepares them."""
import json
from datetime import datetime

from aid.execution.by_hand import LINE_KINDS, MARKS_FILE, changes, mark, open_changes
from aid.execution.ops import WriteOp
from aid.execution.writer import PREPARED_FILE, UNDO_RULE_ID, PreparedWriter, WriteLog, undo_entry

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


def asked(folder, case_id: str, *ops: WriteOp, rule_id: str = "R04") -> list[str]:
    """The operations prepared into the folder by the prepared writer, as the case with this id prepares them: their entry ids."""
    writer = PreparedWriter(folder)
    return [writer.apply(op, case_id, rule_id, {}, NOW).entry_id for op in ops]


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


def undone(folder, case_root, *entry_ids: str) -> list[str]:
    """undo_entry of each entry through the prepared writer of the folder, the newest first as undo_case goes: the ids of the undo
    rows, in that order."""
    return [undo_entry(case_root, entry_id, PreparedWriter(folder), NOW).entry_id for entry_id in reversed(entry_ids)]


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
