import csv
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest
from pydantic import ValidationError

from aid.execution.ops import OpKind, WriteOp
from aid.execution.writer import (PreparedWriter, RetryingWriter, UnknownWrite, UnverifiedWrite, WriteLog, WriteLogEntry, WriterError, inverse,
                                  outcome_not_known, undo_case, undo_entry)


def test_prepared_writer_logs_previous(tmp_path):
    w = PreparedWriter(tmp_path); op = WriteOp(kind="po_line_price", po_number="250101", version=1, line_id="1", old_value="12.50", new_value="12.70")
    e = w.apply(op, "c1", "R04", {"flag_id": "f1"}, datetime(2026, 11, 10, 10, 0))
    assert e.status == "prepared" and e.previous == "12.50" and "12.70" in (tmp_path / "prepared_changes.csv").read_text()

def test_undo_reverses_in_order(tmp_path):
    log = WriteLog(tmp_path); w = PreparedWriter(tmp_path)
    for i, (old, new) in enumerate([("12.50", "12.70"), ("Besteld", "Bevestigd")]):
        kind = "po_line_price" if i == 0 else "set_status"
        log.append(w.apply(WriteOp(kind=kind, po_number="250101", version=1, line_id="1", old_value=old, new_value=new), "c1", "R04", {}, datetime(2026, 11, 10, 10, i)))
    undone = undo_case(tmp_path, w, datetime(2026, 11, 10, 11, 0))
    assert [u.op.kind for u in undone] == ["set_status", "po_line_price"] and undone[1].op.new_value == "12.50" and undone[0].undo_of == log.entries()[1].entry_id
    assert undo_case(tmp_path, w, datetime(2026, 11, 10, 12, 0)) == []

def test_retrying_writer_falls_back(tmp_path):
    class Failing:  name = "failing"; calls = 0
    def apply(self, *a, **k): Failing.calls += 1; raise WriterError("503")
    Failing.apply = apply
    w = RetryingWriter(Failing(), PreparedWriter(tmp_path), retries=2)
    e = w.apply(WriteOp(kind="po_line_date", po_number="250101", version=1, line_id="1", old_value="2026-11-16", new_value="2026-11-30"), "c1", "R02", {}, datetime(2026, 11, 10))
    assert Failing.calls == 2 and e.status == "prepared" and e.error == "503"


# The tests below cover what the brief's rulings add to the three above.

NOW = datetime(2026, 11, 10, 10, 0)
HEADER = ["entry_id", "case_id", "po_number", "version", "line_id", "article_code", "kind", "old_value", "new_value", "document", "rule_id",
          "timestamp"]
INVERSE_KINDS = {"po_line_price": "po_line_price", "po_line_date": "po_line_date", "po_line_quantity": "po_line_quantity", "article_price": "article_price",
                 "attach_document": "detach_document", "detach_document": "attach_document", "set_status": "set_status"}


def make_op(kind="po_line_price", old="12.50", new="12.70", **more) -> WriteOp:
    return WriteOp(kind=kind, po_number="250101", version=1, line_id="1", old_value=old, new_value=new, **more)


def make_entry(entry_id, op=None, status="prepared", **more) -> WriteLogEntry:
    op = op or make_op()
    fields = dict(entry_id=entry_id, case_id="c1", op=op, status=status, previous=op.old_value, new=op.new_value, rule_id="R04", evidence={},
                  timestamp="2026-11-10T10:00:00")
    return WriteLogEntry(**(fields | more))


class FakeLive:
    """Stands in for the live writer of a later task. `script` has one item per call: None to make the change, "failed" to return
    an entry with status "failed" (nothing written), the entry to return, or the exception to raise. Calls beyond the script make
    the change."""
    name = "fake_live"

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0

    def apply(self, op, case_id, rule_id, evidence, now):
        self.calls += 1
        step = self.script.pop(0) if self.script else None
        if isinstance(step, Exception):
            raise step
        if isinstance(step, WriteLogEntry):
            return step
        return WriteLogEntry(entry_id=f"live{self.calls}", case_id=case_id, op=op, status="failed" if step == "failed" else "done",
                             previous=op.old_value, new=op.new_value, rule_id=rule_id, evidence=evidence, timestamp=now.isoformat())


def apply_and_log(case_root, writer, *ops) -> list[WriteLogEntry]:
    """Apply each operation through the writer, append its entry to the case log and return the entries."""
    log = WriteLog(case_root)
    entries = []
    for i, op in enumerate(ops):
        entries.append(writer.apply(op, "c1", "R04", {}, datetime(2026, 11, 10, 10, i)))
        log.append(entries[-1])
    return entries


def csv_rows(folder) -> list[dict]:
    with open(folder / "prepared_changes.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# --- the operation ---

def test_write_op_kinds_and_required_fields():
    assert get_args(OpKind) == ("po_line_price", "po_line_date", "po_line_quantity", "article_price", "attach_document", "detach_document", "set_status")
    assert set(INVERSE_KINDS) == set(get_args(OpKind))             # the test of inverse below covers every kind
    op = WriteOp(kind="attach_document", po_number="250101", version=None)
    assert (op.line_id, op.article_code, op.old_value, op.new_value, op.document) == (None, None, None, None, None)
    with pytest.raises(ValidationError):
        WriteOp(kind="delete_po", po_number="250101", version=1)
    with pytest.raises(ValidationError):
        WriteOp(kind="set_status", po_number="250101")            # the version may be None but must be given


# --- the write log ---

def test_write_log_round_trips_every_field_through_the_file(tmp_path):
    op = WriteOp(kind="po_line_date", po_number="250101", version=3, line_id="2", article_code="KM-1001", old_value="2026-11-16", new_value="2026-W48",
                 document="conf.pdf")
    entry = WriteLogEntry(entry_id="e1", case_id="250101_20261110T100000", op=op, status="done", previous="2026-11-16", new="2026-W48", rule_id="R02",
                          evidence={"flag_id": "f1", "source": {"page": 2, "line": 14, "text": "Levering week 48 voor Zoë"}, "values": ["a", None, 1.5, True]},
                          timestamp="2026-11-10T10:00:00+01:00", undo_of="e0", error="503")
    log = WriteLog(tmp_path)
    assert not (tmp_path / "write_log.jsonl").exists()            # the file appears with the first entry
    log.append(entry)
    assert (tmp_path / "write_log.jsonl").is_file()
    assert WriteLog(tmp_path).entries() == [entry]                # a new WriteLog on the folder reads what the first wrote


def test_write_log_is_one_json_line_per_entry_and_only_appended_to(tmp_path):
    log = WriteLog(tmp_path)
    first, second = make_entry("e1"), make_entry("e2", make_op("set_status", "Besteld", "Bevestigd"))
    log.append(first)
    path = tmp_path / "write_log.jsonl"
    after_first = path.read_bytes()
    log.append(second)
    data = path.read_bytes()
    assert data.startswith(after_first)                           # the first line was not touched
    lines = data.decode("utf-8").split("\n")
    assert len(lines) == 3 and lines[2] == ""                     # two lines, each ended by a newline
    assert lines[:2] == [first.model_dump_json(), second.model_dump_json()]
    assert [json.loads(line)["entry_id"] for line in lines[:2]] == ["e1", "e2"]
    assert log.entries() == [first, second]


def test_write_log_without_a_file_is_empty_and_reading_does_not_create_one(tmp_path):
    assert WriteLog(tmp_path).entries() == []
    assert list(tmp_path.iterdir()) == []


def test_write_log_keeps_an_entry_on_one_line_whatever_its_text_holds(tmp_path):
    odd = "a\u2028b\u2029c\u0085d\re\nf\x0cg\x1ch"            # line separators that str.splitlines would split at
    log = WriteLog(tmp_path)
    log.append(make_entry("e1", evidence={"text": odd}))
    log.append(make_entry("e2"))
    assert (tmp_path / "write_log.jsonl").read_bytes().count(b"\n") == 2
    entries = log.entries()
    assert [e.entry_id for e in entries] == ["e1", "e2"] and entries[0].evidence == {"text": odd}


def test_write_log_append_of_an_entry_that_is_not_json_leaves_no_trace(tmp_path):
    log = WriteLog(tmp_path)
    with pytest.raises(ValueError, match="evidence"):
        log.append(make_entry("e1", evidence={"object": object()}))
    assert list(tmp_path.iterdir()) == []
    log.append(make_entry("e2"))
    with pytest.raises(ValueError, match="evidence"):
        log.append(make_entry("e3", evidence={"object": object()}))
    assert [e.entry_id for e in log.entries()] == ["e2"]


def test_write_log_error_for_evidence_that_is_not_json_says_which_change_it_was(tmp_path):
    with pytest.raises(ValueError) as caught:
        WriteLog(tmp_path).append(make_entry("e1", make_op("po_line_date", "2026-11-16", "2026-11-30"), rule_id="R02", evidence={"object": object()}))
    message = str(caught.value)
    assert "R02" in message and "po_line_date" in message and "250101" in message and "evidence is not JSON" in message


def test_write_log_append_to_an_empty_file_starts_at_its_first_line(tmp_path):
    path = tmp_path / "write_log.jsonl"
    path.write_bytes(b"")
    WriteLog(tmp_path).append(make_entry("e1"))
    assert path.read_bytes() == (make_entry("e1").model_dump_json() + "\n").encode("utf-8")


def test_write_log_skips_blank_lines_and_refuses_a_damaged_line(tmp_path):
    log = WriteLog(tmp_path)
    log.append(make_entry("e1"))
    path = tmp_path / "write_log.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n  \n")
    log.append(make_entry("e2"))
    assert [e.entry_id for e in log.entries()] == ["e1", "e2"]
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"entry_id": "e3", "case_')                      # a line cut off by a crash
    with pytest.raises(ValueError, match=re.escape(f"{path}, line 5: not a write-log entry")):         # the blank lines count as lines
        log.entries()


@pytest.mark.parametrize("damage, reason", [
    (b'{"entry_id": "e2", "case_', "Invalid JSON"),                            # cut off by a crash
    (b'{"entry_id": "e2", "case_id": "Zo\xc3', "Invalid JSON"),                  # cut off in the middle of a character
    (b'{"entry_id": "\xff", "case_id": "c1"}', "Invalid JSON"),                  # a whole line holding bytes that are not UTF-8
    (b'{"entry_id": "e2"}', "case_id: Field required"),                        # JSON, but not an entry
])
def test_write_log_names_the_file_and_the_line_of_a_damaged_entry(tmp_path, damage, reason):
    log = WriteLog(tmp_path)
    log.append(make_entry("e1"))
    path = tmp_path / "write_log.jsonl"
    with open(path, "ab") as f:
        f.write(damage + b"\n")
    log.append(make_entry("e3"))
    with pytest.raises(ValueError, match=re.escape(f"{path}, line 2: not a write-log entry") + ".*" + re.escape(reason)):
        log.entries()


def test_write_log_append_after_a_torn_tail_keeps_the_damage_in_the_fragment(tmp_path):
    path = tmp_path / "write_log.jsonl"
    log = WriteLog(tmp_path)
    log.append(make_entry("e1"))
    with open(path, "ab") as f:
        f.write(b'{"entry_id": "e2", "case_')                       # a crash cut the second line off, before its newline
    log.append(make_entry("e3"))
    lines = path.read_bytes().split(b"\n")
    assert len(lines) == 4 and lines[3] == b""
    assert lines[1] == b'{"entry_id": "e2", "case_'                  # the fragment keeps its own line
    assert json.loads(lines[2])["entry_id"] == "e3"                  # and the new entry is whole on the next one
    with pytest.raises(ValueError, match=re.escape(f"{path}, line 2")):
        log.entries()


# --- the entries a writer makes ---

def test_entry_ids_are_uuid4_hex_and_never_repeat(tmp_path):
    ids = [PreparedWriter(tmp_path).apply(make_op(), "c1", "R04", {}, NOW).entry_id for _ in range(3)]
    assert len(set(ids)) == 3
    for entry_id in ids:
        assert re.fullmatch(r"[0-9a-f]{32}", entry_id) and uuid.UUID(entry_id).version == 4


def test_timestamp_is_now_as_given(tmp_path):
    w = PreparedWriter(tmp_path)
    assert w.apply(make_op(), "c1", "R04", {}, datetime(2026, 11, 10, 10, 0)).timestamp == "2026-11-10T10:00:00"
    aware = datetime(2026, 11, 10, 10, 0, 5, tzinfo=timezone(timedelta(hours=1)))
    assert w.apply(make_op(), "c1", "R04", {}, aware).timestamp == "2026-11-10T10:00:05+01:00"


# --- the prepared writer ---

def test_prepared_writer_entry_carries_the_call(tmp_path):
    op = make_op(article_code="KM-1001")
    e = PreparedWriter(tmp_path).apply(op, "c1", "R04", {"flag_id": "f1"}, NOW)
    assert (e.case_id, e.op, e.status, e.previous, e.new, e.rule_id, e.evidence, e.undo_of, e.error) == \
           ("c1", op, "prepared", "12.50", "12.70", "R04", {"flag_id": "f1"}, None, None)


def test_prepared_writer_csv_has_one_row_per_change_with_its_columns(tmp_path):
    w = PreparedWriter(tmp_path)
    assert w.name == "prepared"
    a = w.apply(WriteOp(kind="article_price", po_number="250101", version=2, line_id="3", article_code="KM-1001", old_value="12.50", new_value="12.70"),
                "c1", "R04", {"flag_id": "f1"}, NOW)
    b = w.apply(WriteOp(kind="attach_document", po_number="250102", version=None, document="conf.pdf"), "c2", "R01", {}, datetime(2026, 11, 10, 10, 5))
    assert (tmp_path / "prepared_changes.csv").read_text(encoding="utf-8").splitlines()[0] == ",".join(HEADER)
    assert csv_rows(tmp_path) == [
        {"entry_id": a.entry_id, "case_id": "c1", "po_number": "250101", "version": "2", "line_id": "3", "article_code": "KM-1001", "kind": "article_price",
         "old_value": "12.50", "new_value": "12.70", "document": "", "rule_id": "R04", "timestamp": "2026-11-10T10:00:00"},
        {"entry_id": b.entry_id, "case_id": "c2", "po_number": "250102", "version": "", "line_id": "", "article_code": "", "kind": "attach_document",
         "old_value": "", "new_value": "", "document": "conf.pdf", "rule_id": "R01", "timestamp": "2026-11-10T10:05:00"},
    ]


def test_prepared_csv_quotes_commas_quotes_and_line_breaks_in_a_value(tmp_path):
    w = PreparedWriter(tmp_path)
    document = 'conf, "final" v2.pdf'
    status = 'Besteld "spoed"\r\nvolgt'
    a = w.apply(WriteOp(kind="attach_document", po_number="250101", version=1, document=document), "c1", "R01", {}, NOW)
    b = w.apply(WriteOp(kind="set_status", po_number="250101", version=1, old_value=status, new_value="Bevestigd"), "c1", "R01", {}, NOW)
    raw = (tmp_path / "prepared_changes.csv").read_bytes()
    assert raw.startswith(",".join(HEADER).encode("utf-8") + b"\n")  # rows end in a bare newline, as in the other CSV files of the aid
    assert b'"conf, ""final"" v2.pdf"' in raw                       # a comma or a quote puts the value in quotes, and a quote is doubled
    assert b'"Besteld ""spoed""\r\nvolgt"' in raw                   # a line break inside a value is written as it is (newline="")
    rows = csv_rows(tmp_path)                                        # csv.DictReader, opened with newline="", gives one row per change
    assert [(r["entry_id"], r["document"], r["old_value"]) for r in rows] == [(a.entry_id, document, ""), (b.entry_id, "", status)]


def test_prepared_writer_checks_that_the_entry_can_be_logged_before_it_writes(tmp_path):
    out = tmp_path / "prepared"
    w = PreparedWriter(out)
    with pytest.raises(ValueError, match="evidence"):
        w.apply(make_op(), "c1", "R04", {"object": object()}, NOW)
    assert not out.exists()                                          # no row, no file and no folder: the write was never started
    w.apply(make_op(), "c1", "R04", {"flag_id": "f1"}, NOW)
    assert len(csv_rows(out)) == 1                                   # and the writer still works


def test_prepared_writer_writes_the_header_once_over_two_writers(tmp_path):
    PreparedWriter(tmp_path).apply(make_op(), "c1", "R04", {}, NOW)
    PreparedWriter(tmp_path).apply(make_op("set_status", "Besteld", "Bevestigd"), "c1", "R01", {}, NOW)
    lines = (tmp_path / "prepared_changes.csv").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and lines[0] == ",".join(HEADER) and lines.count(lines[0]) == 1
    assert [r["kind"] for r in csv_rows(tmp_path)] == ["po_line_price", "set_status"]


def test_prepared_writer_creates_its_folder(tmp_path):
    out = tmp_path / "drive" / "prepared"
    PreparedWriter(out).apply(make_op(), "c1", "R04", {}, NOW)
    assert len(csv_rows(out)) == 1


def test_prepared_writer_raises_writer_error_for_a_folder_it_cannot_write(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the folder should be", encoding="utf-8")
    with pytest.raises(WriterError, match="prepared_changes.csv"):
        PreparedWriter(blocked).apply(make_op(), "c1", "R04", {}, NOW)
    assert blocked.read_text(encoding="utf-8") == "a file where the folder should be"
    out = tmp_path / "out"
    (out / "prepared_changes.csv").mkdir(parents=True)             # the file itself cannot be opened
    with pytest.raises(WriterError, match="prepared_changes.csv"):
        PreparedWriter(out).apply(make_op(), "c1", "R04", {}, NOW)


# --- the retrying writer ---

def test_retrying_writer_name_and_default_attempts(tmp_path):
    primary = FakeLive(WriterError("a"), WriterError("b"), WriterError("c"))
    w = RetryingWriter(primary, PreparedWriter(tmp_path))
    assert w.name == "fake_live+prepared"
    assert w.apply(make_op(), "c1", "R04", {}, NOW).status == "prepared"
    assert primary.calls == 2                                      # retries defaults to 2


def test_retrying_writer_returns_the_primary_entry_when_a_retry_succeeds(tmp_path):
    primary = FakeLive(WriterError("503"))
    e = RetryingWriter(primary, PreparedWriter(tmp_path)).apply(make_op(), "c1", "R04", {"flag_id": "f1"}, NOW)
    assert primary.calls == 2 and e.entry_id == "live2" and e.status == "done" and e.error is None
    assert not (tmp_path / "prepared_changes.csv").exists()        # the fallback was not used


def test_retrying_writer_falls_back_with_the_last_error_and_the_fallbacks_entry(tmp_path):
    primary = FakeLive(WriterError("503"), WriterError("timeout"), WriterError("connection reset"))
    e = RetryingWriter(primary, PreparedWriter(tmp_path), retries=3).apply(make_op(), "c1", "R04", {"flag_id": "f1"}, NOW)
    assert primary.calls == 3 and e.status == "prepared" and e.error == "connection reset"
    assert (e.case_id, e.rule_id, e.evidence, e.previous, e.new) == ("c1", "R04", {"flag_id": "f1"}, "12.50", "12.70")
    assert [r["entry_id"] for r in csv_rows(tmp_path)] == [e.entry_id]


def test_retrying_writer_names_the_exception_when_the_error_has_no_message(tmp_path):
    class Unreachable(WriterError):
        pass

    for failure, name in [(WriterError(), "WriterError"), (Unreachable(), "Unreachable")]:
        e = RetryingWriter(FakeLive(failure), PreparedWriter(tmp_path), retries=1).apply(make_op(), "c1", "R04", {}, NOW)
        assert e.status == "prepared" and e.error == name          # never "", which `if entry.error` would read as no failure


def test_retrying_writer_lets_any_other_exception_through_at_once(tmp_path):
    primary = FakeLive(RuntimeError("a bug"))
    with pytest.raises(RuntimeError, match="a bug"):
        RetryingWriter(primary, PreparedWriter(tmp_path)).apply(make_op(), "c1", "R04", {}, NOW)
    assert primary.calls == 1 and not (tmp_path / "prepared_changes.csv").exists()


def test_retrying_writer_raises_when_the_fallback_fails_too(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the folder should be", encoding="utf-8")
    primary = FakeLive(WriterError("503"), WriterError("503"))
    with pytest.raises(WriterError, match="blocked"):
        RetryingWriter(primary, PreparedWriter(blocked)).apply(make_op(), "c1", "R04", {}, NOW)
    assert primary.calls == 2


def test_retrying_writer_needs_at_least_one_attempt(tmp_path):
    for retries in (0, -1):
        with pytest.raises(ValueError, match="at least 1"):
            RetryingWriter(FakeLive(), PreparedWriter(tmp_path), retries=retries)
    single = FakeLive(WriterError("503"))
    assert RetryingWriter(single, PreparedWriter(tmp_path), retries=1).apply(make_op(), "c1", "R04", {}, NOW).status == "prepared"
    assert single.calls == 1


# --- a write the system accepted whose check failed ---

def unchecked(entry_id: str, previous: str, failed: str, op: WriteOp | None = None) -> UnverifiedWrite:
    """What a writer into a system raises when the system accepted its write and the check after it failed: the entry of that
    attempt, made, with the value it read before the write as its previous and the failed check as its error."""
    return UnverifiedWrite(failed, make_entry(entry_id, op or make_op(), status="done", previous=previous, error=failed))


def test_an_accepted_write_whose_checks_fail_is_logged_as_made_with_the_value_read(tmp_path):
    """The document says 12.50 and the system held 12.60. Both attempts are accepted and neither can be checked; a writer without a
    memory of its own reads, at the second attempt, the 12.70 that the first one wrote. The entry of the call is the first
    attempt's as it is, so that its previous is the value read before the change and its error the check that failed; the
    fallback is never asked, so nothing is prepared."""
    first, second = unchecked("attempt1", "12.60", "check 1 failed"), unchecked("attempt2", "12.70", "check 2 failed")
    primary = FakeLive(first, second)
    e = RetryingWriter(primary, PreparedWriter(tmp_path)).apply(make_op(), "c1", "R04", {"flag_id": "f1"}, NOW)
    assert primary.calls == 2
    assert (e.entry_id, e.status, e.previous, e.new, e.error) == ("attempt1", "done", "12.60", "12.70", "check 1 failed")
    assert e == first.entry
    assert not (tmp_path / "prepared_changes.csv").exists()        # never handed to the fallback, never prepared
    WriteLog(tmp_path).append(e)
    assert WriteLog(tmp_path).entries() == [e]                     # and it can be logged


@pytest.mark.parametrize("script, calls, error", [
    pytest.param(lambda: (unchecked("attempt1", "12.60", "check failed"), WriterError("503")), 2, "check failed", id="then a failed attempt"),
    pytest.param(lambda: (WriterError("503"), unchecked("attempt1", "12.60", "check failed")), 2, "check failed", id="after a failed attempt"),
    pytest.param(lambda: (unchecked("attempt1", "12.60", "check failed"), WriterError("503"), WriterError("timeout")), 3, "check failed",
                 id="then two failed attempts"),
    pytest.param(lambda: (UnverifiedWrite("check failed", make_entry("attempt1", status="done", previous="12.60")),), 1, "check failed",
                 id="an entry without the error"),
    pytest.param(lambda: (UnverifiedWrite("the message of the failure", make_entry("attempt1", status="done", previous="12.60", error="check failed")),),
                 1, "check failed", id="an entry with an error of its own"),
    pytest.param(lambda: (unchecked("attempt1", "12.60", ""),), 1, "UnverifiedWrite", id="one attempt, no message"),
])
def test_one_accepted_write_that_no_attempt_verifies_keeps_the_change_from_the_fallback(tmp_path, script, calls, error):
    """One attempt that the system accepted is enough: whatever the other attempts failed at, the system may hold the change, so
    it is logged as made with the value that attempt read and its failed check as the error (never an empty one: the message of
    the failure, or the name of its class, where the entry came without)."""
    primary = FakeLive(*script())
    e = RetryingWriter(primary, PreparedWriter(tmp_path), retries=calls).apply(make_op(), "c1", "R04", {}, NOW)
    assert primary.calls == calls and (e.entry_id, e.status, e.previous, e.error) == ("attempt1", "done", "12.60", error)
    assert not (tmp_path / "prepared_changes.csv").exists()


def test_a_write_the_system_ignored_is_still_prepared(tmp_path):
    """A writer whose check reads back the value that was there before the write raises a plain WriterError: the system ignored
    the write. That is a failed attempt as any other, and after the last one the change is prepared, as before."""
    ignored = "verify: read back the value from before the write"
    primary = FakeLive(WriterError(ignored), WriterError(ignored))
    e = RetryingWriter(primary, PreparedWriter(tmp_path)).apply(make_op(), "c1", "R04", {}, NOW)
    assert primary.calls == 2 and (e.status, e.previous, e.new, e.error) == ("prepared", "12.50", "12.70", ignored)
    assert [(r["entry_id"], r["old_value"], r["new_value"]) for r in csv_rows(tmp_path)] == [(e.entry_id, "12.50", "12.70")]


def test_an_attempt_that_finds_its_own_earlier_write_counts_the_read_as_the_check(tmp_path):
    """The first attempt is accepted and cannot be checked. The second finds the value the first one sent and returns its entry,
    made and verified, with the first attempt's previous value (the read itself is the system writer's: test_exact_api.py shows
    it on the Exact writer, with the requests). That entry is the entry of the call as it is: no error, nothing of the attempt
    before it, and nothing prepared."""
    first = unchecked("attempt1", "12.60", "check failed")
    verified = make_entry("attempt2", status="done", previous="12.60", evidence={"read_before": "12.70"})
    primary = FakeLive(first, verified)
    e = RetryingWriter(primary, PreparedWriter(tmp_path)).apply(make_op(), "c1", "R04", {}, NOW)
    assert primary.calls == 2 and e == verified and (e.status, e.previous, e.error) == ("done", "12.60", None)
    assert not (tmp_path / "prepared_changes.csv").exists()


def test_an_unverified_write_is_a_writer_error_that_carries_its_entry():
    failure = unchecked("attempt1", "12.60", "check failed")
    assert isinstance(failure, WriterError) and str(failure) == "check failed"
    assert (failure.entry.entry_id, failure.entry.status, failure.entry.previous, failure.entry.error) == ("attempt1", "done", "12.60", "check failed")


# --- a write whose outcome is not known ---

def in_doubt(entry_id: str, previous: str, happened: str) -> UnknownWrite:
    """What a writer into a system raises when a write was sent and no answer, or no clear one, came: the entry of the attempt, with
    the value it read before the write as its previous and what happened as its error."""
    return UnknownWrite(happened, make_entry(entry_id, status="done", previous=previous, error=happened))


def test_a_write_whose_outcome_is_not_known_is_logged_as_not_sure_and_never_prepared(tmp_path):
    """No answer came to the write of either attempt, and nothing settled what the system did with it. The entry of the call is the
    first attempt's, made as far as the log goes (status "done", the value read before the first attempt), with what the writer
    last said happened to the write as its error and the note that the outcome is not known; the fallback is never asked."""
    first, second = in_doubt("attempt1", "12.60", "write: no answer"), in_doubt("attempt2", "12.60", "write: no answer again")
    primary = FakeLive(first, second)
    e = RetryingWriter(primary, PreparedWriter(tmp_path)).apply(make_op(), "c1", "R04", {"flag_id": "f1"}, NOW)
    assert primary.calls == 2
    assert (e.entry_id, e.status, e.previous, e.new, e.error) == ("attempt1", "done", "12.60", "12.70", "write: no answer again")
    assert outcome_not_known(e) and e.evidence == {"outcome": "not known"}
    assert not outcome_not_known(first.entry)                      # the note is the retrying writer's: the entry given is not changed
    assert not (tmp_path / "prepared_changes.csv").exists()
    WriteLog(tmp_path).append(e)
    assert outcome_not_known(WriteLog(tmp_path).entries()[0])      # the note is in the write log


@pytest.mark.parametrize("script, retries, status, entry_id, error, not_known", [
    pytest.param(lambda: (in_doubt("a1", "12.60", "no answer"),), 1, "done", "a1", "no answer", True, id="one attempt"),
    pytest.param(lambda: (WriterError("503"), in_doubt("a2", "12.60", "no answer")), 2, "done", "a2", "no answer", True, id="after a failed attempt"),
    pytest.param(lambda: (in_doubt("a1", "12.60", "no answer"), WriterError("settled: not made")), 2, "prepared", None, "settled: not made", False,
                 id="then a plain failure: the writer says no write is in doubt"),
    pytest.param(lambda: (in_doubt("a1", "12.60", "no answer"), WriterError("not made"), in_doubt("a3", "12.60", "no answer again")), 3,
                 "done", "a3", "no answer again", True, id="settled, then in doubt again"),
    pytest.param(lambda: (in_doubt("a1", "12.60", "no answer"), unchecked("a2", "12.60", "check failed")), 2, "done", "a2", "check failed", False,
                 id="then accepted and not checked: made, not verified"),
    pytest.param(lambda: (unchecked("a1", "12.60", "check failed"), in_doubt("a2", "12.70", "no answer")), 2, "done", "a1", "check failed", False,
                 id="accepted and not checked, then in doubt: made, not verified"),
    pytest.param(lambda: (unchecked("a1", "12.60", "check failed"), in_doubt("a2", "12.70", "no answer"), WriterError("not made")), 3,
                 "done", "a1", "check failed", False, id="an accepted write stays, whatever settles the doubt after it"),
    pytest.param(lambda: (UnknownWrite("no answer", make_entry("a1", status="done", previous="12.60")),), 1, "done", "a1", "no answer", True,
                 id="an entry without the error"),
    pytest.param(lambda: (UnknownWrite("", make_entry("a1", status="done", previous="12.60")),), 1, "done", "a1", "UnknownWrite", True,
                 id="no message"),
])
def test_what_the_last_attempt_knows_decides_a_write_in_doubt(tmp_path, script, retries, status, entry_id, error, not_known):
    """A writer raises UnknownWrite after the attempt that left a write in doubt and after every later attempt that fails without
    settling it, so a plain failure after it says that the system did not make the write: the change is then prepared, as after
    any failed attempt. A write the system accepted (UnverifiedWrite) is made whatever comes after it."""
    primary = FakeLive(*script())
    e = RetryingWriter(primary, PreparedWriter(tmp_path), retries=retries).apply(make_op(), "c1", "R04", {}, NOW)
    assert primary.calls == retries and (e.status, e.error, outcome_not_known(e)) == (status, error, not_known)
    assert (e.entry_id == entry_id) if entry_id else (e.previous == "12.50")
    assert (tmp_path / "prepared_changes.csv").exists() == (status == "prepared")


def test_an_unknown_write_is_a_writer_error_that_carries_its_entry():
    failure = in_doubt("attempt1", "12.60", "no answer")
    assert isinstance(failure, WriterError) and not isinstance(failure, UnverifiedWrite) and str(failure) == "no answer"
    assert (failure.entry.entry_id, failure.entry.status, failure.entry.previous, failure.entry.error) == ("attempt1", "done", "12.60", "no answer")


# --- the inverse ---

@pytest.mark.parametrize("kind, back_kind", INVERSE_KINDS.items())
def test_inverse_swaps_old_and_new_for_every_kind(kind, back_kind):
    op = WriteOp(kind=kind, po_number="250101", version=2, line_id="3", article_code="KM-1001", old_value="old", new_value="new", document="conf.pdf")
    back = inverse(op)
    assert back == WriteOp(kind=back_kind, po_number="250101", version=2, line_id="3", article_code="KM-1001", old_value="new", new_value="old",
                           document="conf.pdf")
    assert inverse(back) == op                                     # reversing twice gives the operation back
    assert (op.old_value, op.new_value) == ("old", "new")          # and the operation itself is not changed


def test_inverse_sets_a_status_back_and_detaches_the_attached_document():
    status = inverse(WriteOp(kind="set_status", po_number="250101", version=1, old_value="Besteld", new_value="Bevestigd"))
    assert (status.kind, status.old_value, status.new_value) == ("set_status", "Bevestigd", "Besteld")
    detach = inverse(WriteOp(kind="attach_document", po_number="250101", version=1, document="conf.pdf"))
    assert (detach.kind, detach.document) == ("detach_document", "conf.pdf")
    assert inverse(detach).kind == "attach_document"


# --- the undo ---

def assert_refused(tmp_path, entries, entry_id):
    """Build a log of `entries` and assert that undo_entry returns None for `entry_id`, calls no writer and leaves the log as it was."""
    log = WriteLog(tmp_path)
    for e in entries:
        log.append(e)
    live = FakeLive()
    assert undo_entry(tmp_path, entry_id, live, NOW) is None
    assert live.calls == 0 and log.entries() == entries


def test_undo_entry_applies_the_inverse_through_the_writer_and_logs_it(tmp_path):
    w = PreparedWriter(tmp_path)
    [entry] = apply_and_log(tmp_path, w, make_op(article_code="KM-1001"))
    undo = undo_entry(tmp_path, entry.entry_id, w, datetime(2026, 11, 11, 9, 30))
    assert undo.op == inverse(entry.op) and (undo.previous, undo.new) == ("12.70", "12.50")
    assert undo.rule_id == "undo" and undo.evidence == {"undo_of": entry.entry_id} and undo.undo_of == entry.entry_id
    assert undo.case_id == "c1" and undo.timestamp == "2026-11-11T09:30:00" and undo.entry_id != entry.entry_id
    assert WriteLog(tmp_path).entries() == [entry, undo]           # undo_entry appended it to the case log itself
    assert [(r["entry_id"], r["rule_id"], r["old_value"], r["new_value"]) for r in csv_rows(tmp_path)][-1] == (undo.entry_id, "undo", "12.70", "12.50")


def test_undo_entry_refuses_an_unknown_id(tmp_path):
    assert_refused(tmp_path, [make_entry("e1")], "nope")


def test_undo_entry_refuses_an_entry_that_was_undone_already(tmp_path):
    w = PreparedWriter(tmp_path)
    [entry] = apply_and_log(tmp_path, w, make_op())
    assert undo_entry(tmp_path, entry.entry_id, w, NOW) is not None
    log_before, csv_before = WriteLog(tmp_path).entries(), (tmp_path / "prepared_changes.csv").read_bytes()
    assert undo_entry(tmp_path, entry.entry_id, w, NOW) is None
    assert WriteLog(tmp_path).entries() == log_before and (tmp_path / "prepared_changes.csv").read_bytes() == csv_before


def test_undo_entry_refuses_a_failed_entry(tmp_path):
    assert_refused(tmp_path, [make_entry("e1", status="failed", error="503")], "e1")


def test_undo_entry_refuses_an_undo_entry(tmp_path):
    w = PreparedWriter(tmp_path)
    [entry] = apply_and_log(tmp_path, w, make_op())
    undo = undo_entry(tmp_path, entry.entry_id, w, NOW)
    log_before = WriteLog(tmp_path).entries()
    assert undo_entry(tmp_path, undo.entry_id, w, NOW) is None
    assert WriteLog(tmp_path).entries() == log_before


def test_undo_entry_does_not_count_an_undo_that_came_back_failed(tmp_path):
    log = WriteLog(tmp_path)
    log.append(make_entry("e1"))
    live = FakeLive("failed")                                        # the first undo reverses nothing, later ones are made
    first = undo_entry(tmp_path, "e1", live, NOW)
    assert (first.status, first.undo_of) == ("failed", "e1")         # it is logged, as every undo entry is
    second = undo_entry(tmp_path, "e1", live, NOW)                   # but e1 was not reversed, so it can be undone again
    assert (second.status, second.undo_of) == ("done", "e1")
    assert undo_entry(tmp_path, "e1", live, NOW) is None             # and now it has been
    assert live.calls == 2 and [e.status for e in log.entries()] == ["prepared", "failed", "done"]
    assert undo_entry(tmp_path, first.entry_id, live, NOW) is None   # the failed undo is an undo entry, and an undo is never undone


def test_undo_case_tries_an_entry_again_after_its_undo_came_back_failed(tmp_path):
    log = WriteLog(tmp_path)
    for i in (1, 2):
        log.append(make_entry(f"e{i}", make_op(old=f"{i}.00", new=f"{i + 1}.00")))
    live = FakeLive(None, "failed")                                  # the undo of e2 is made, the undo of e1 comes back failed
    assert [(u.undo_of, u.status) for u in undo_case(tmp_path, live, NOW)] == [("e2", "done"), ("e1", "failed")]
    assert [(u.undo_of, u.status) for u in undo_case(tmp_path, live, NOW)] == [("e1", "done")]
    assert undo_case(tmp_path, live, NOW) == [] and live.calls == 3


def test_undo_entry_on_one_entry_leaves_the_others(tmp_path):
    w = PreparedWriter(tmp_path)
    first, second, third = apply_and_log(tmp_path, w, make_op(), make_op("po_line_date", "2026-11-16", "2026-11-30"), make_op("set_status", "Besteld", "Bevestigd"))
    undo = undo_entry(tmp_path, second.entry_id, w, NOW)
    assert undo.undo_of == second.entry_id
    assert WriteLog(tmp_path).entries() == [first, second, third, undo]
    assert len(csv_rows(tmp_path)) == 4                            # the three changes and the one undo
    assert [u.undo_of for u in undo_case(tmp_path, w, NOW)] == [third.entry_id, first.entry_id]      # the other two can still be undone


def test_undo_entry_and_undo_case_of_a_case_without_a_log_do_nothing(tmp_path):
    w = PreparedWriter(tmp_path)
    assert undo_entry(tmp_path, "e1", w, NOW) is None and undo_case(tmp_path, w, NOW) == []
    assert list(tmp_path.iterdir()) == []                          # no log and no prepared change was made


def test_undo_case_goes_newest_first_and_skips_what_undo_entry_refuses(tmp_path):
    ops = [make_op("po_line_price", "12.50", "12.70"), make_op("po_line_date", "2026-11-16", "2026-11-30"), make_op("po_line_quantity", "10", "12"),
           make_op("set_status", "Besteld", "Bevestigd")]
    log = WriteLog(tmp_path)
    for i, op in enumerate(ops, start=1):
        log.append(make_entry(f"e{i}", op, status="failed" if i == 2 else "done"))
    live = FakeLive()
    assert undo_entry(tmp_path, "e3", live, NOW).undo_of == "e3"   # e3 is undone on its own first
    undone = undo_case(tmp_path, live, NOW)
    assert [u.undo_of for u in undone] == ["e4", "e1"]             # not e3 (undone), e2 (failed) or the undo entry made for e3
    assert [u.op for u in undone] == [inverse(ops[3]), inverse(ops[0])]
    assert live.calls == 3
    assert undo_case(tmp_path, live, NOW) == [] and live.calls == 3


def test_undo_case_that_stops_half_way_has_logged_what_it_did(tmp_path):
    log = WriteLog(tmp_path)
    for i in (1, 2, 3):
        log.append(make_entry(f"e{i}", make_op(old=f"{i}.00", new=f"{i + 1}.00")))
    with pytest.raises(WriterError):
        undo_case(tmp_path, FakeLive(None, WriterError("503")), NOW)
    assert [e.undo_of for e in log.entries()] == [None, None, None, "e3"]       # e3 was undone and logged; the failed undo of e2 left nothing
    assert [u.undo_of for u in undo_case(tmp_path, FakeLive(), NOW)] == ["e2", "e1"]
    assert undo_case(tmp_path, FakeLive(), NOW) == []


def test_undo_case_restores_the_status_then_detaches_the_document(tmp_path):
    w = PreparedWriter(tmp_path)
    apply_and_log(tmp_path, w, WriteOp(kind="attach_document", po_number="250101", version=1, document="conf.pdf"),
                  WriteOp(kind="set_status", po_number="250101", version=1, old_value="Besteld", new_value="Bevestigd"))
    undone = undo_case(tmp_path, w, NOW)
    assert [(u.op.kind, u.op.document, u.op.new_value) for u in undone] == [("set_status", None, "Besteld"), ("detach_document", "conf.pdf", None)]
    rows = csv_rows(tmp_path)
    assert [r["kind"] for r in rows] == ["attach_document", "set_status", "set_status", "detach_document"]
    assert [r["document"] for r in rows] == ["conf.pdf", "", "", "conf.pdf"]       # the prepared detach names the file too


def test_undo_through_a_retrying_writer_logs_the_fallback_entry(tmp_path):
    [entry] = apply_and_log(tmp_path, FakeLive(), make_op())
    w = RetryingWriter(FakeLive(WriterError("503"), WriterError("503")), PreparedWriter(tmp_path))
    undo = undo_entry(tmp_path, entry.entry_id, w, NOW)
    assert (undo.status, undo.error, undo.undo_of) == ("prepared", "503", entry.entry_id)
    assert WriteLog(tmp_path).entries() == [entry, undo]
    assert [r["entry_id"] for r in csv_rows(tmp_path)] == [undo.entry_id]


# --- the undo of a change made in a system ---

def test_the_undo_writes_back_the_value_read_before_the_write(tmp_path):
    """The document said 10 and the system held 12 when the change to 11 was made (the entry's previous). The undo writes the 12
    back, through a writer into the system and as the change the prepared writer prepares, and takes the 11 away."""
    made = make_entry("e1", make_op(old="10", new="11"), status="done", previous="12", evidence={"read_before": "12"})
    for folder, writer in ((tmp_path / "live", FakeLive()), (tmp_path / "prepared", PreparedWriter(tmp_path / "prepared"))):
        folder.mkdir()
        WriteLog(folder).append(made)
        undo = undo_entry(folder, "e1", writer, NOW + timedelta(hours=1))
        assert (undo.op.kind, undo.op.old_value, undo.op.new_value) == ("po_line_price", "11", "12")
        assert undo.op == make_op(old="11", new="12") and (undo.previous, undo.new) == ("11", "12")
        assert (undo.rule_id, undo.undo_of, undo.evidence) == ("undo", "e1", {"undo_of": "e1"})
        assert WriteLog(folder).entries() == [made, undo]
    assert [(r["old_value"], r["new_value"], r["rule_id"]) for r in csv_rows(tmp_path / "prepared")] == [("11", "12", "undo")]


def test_the_undo_of_a_case_writes_back_what_each_change_made_in_a_system_read(tmp_path):
    log = WriteLog(tmp_path)
    log.append(make_entry("e1", make_op(old="10", new="11"), status="done", previous="12"))
    log.append(make_entry("e2", make_op("po_line_quantity", old="100", new="120"), status="done", previous="100"))
    undone = undo_case(tmp_path, FakeLive(), NOW)
    assert [(u.undo_of, u.op.old_value, u.op.new_value) for u in undone] == [("e2", "120", "100"), ("e1", "11", "12")]


NOT_READ = [
    pytest.param(None, {"read_before": None}, "the field was empty before the write", id="null read"),
    pytest.param("", {"read_before": ""}, "the field was empty before the write", id="empty text read"),
    pytest.param("  ", {"read_before": "  "}, "the field was empty before the write", id="blank read"),
    pytest.param(None, {}, "no value was read before the write", id="nothing read"),
]


@pytest.mark.parametrize("previous, evidence, why", NOT_READ)
def test_the_undo_of_a_change_made_in_a_system_writes_no_value_the_system_did_not_hold(tmp_path, previous, evidence, why):
    """The field was empty before the write, or nothing was read: there is no value to write back, and the document's old value
    is one the system never held. A writer into a system gets no operation at all: the undo is refused with a WriterError that
    names the kind, the place and which of the two it is, and nothing is written or logged. Through RetryingWriter too: the
    fallback is not asked."""
    made = make_entry("e1", make_op(old="10", new="11"), status="done", previous=previous, evidence=evidence)
    WriteLog(tmp_path).append(made)
    live = FakeLive()
    for writer in (live, RetryingWriter(live, PreparedWriter(tmp_path / "prepared"))):
        with pytest.raises(WriterError) as caught:
            undo_entry(tmp_path, "e1", writer, NOW)
        assert type(caught.value) is WriterError and str(caught.value) == f"cannot undo po_line_price on PO 250101, line 1: {why}, so there is no value to write back"
    assert live.calls == 0 and WriteLog(tmp_path).entries() == [made] and not (tmp_path / "prepared").exists()
    with pytest.raises(WriterError, match="cannot undo po_line_price"):
        undo_case(tmp_path, live, NOW)                             # the undo of the case stops there, as at any refused undo


@pytest.mark.parametrize("previous, evidence, why", NOT_READ)
def test_the_prepared_writer_puts_such_an_undo_on_the_list_without_a_value(tmp_path, previous, evidence, why):
    """The page's writer only prepares: the row asks the buyer, with the value written as its old value and no new value."""
    made = make_entry("e1", make_op(old="10", new="11"), status="done", previous=previous, evidence=evidence)
    WriteLog(tmp_path).append(made)
    undo = undo_entry(tmp_path, "e1", PreparedWriter(tmp_path / "prepared"), NOW)
    assert (undo.status, undo.op.old_value, undo.op.new_value, undo.undo_of) == ("prepared", "11", None, "e1")
    assert [(r["old_value"], r["new_value"], r["rule_id"]) for r in csv_rows(tmp_path / "prepared")] == [("11", "", "undo")]


def test_the_refused_undo_names_the_article_and_a_file_is_detached_as_before(tmp_path):
    article = WriteOp(kind="article_price", po_number="250101", version=1, article_code="KM-1001", new_value="12.70")
    attached = make_entry("e2", WriteOp(kind="attach_document", po_number="250101", version=1, document="conf.pdf"), status="done")
    log = WriteLog(tmp_path)
    log.append(make_entry("e1", article, status="done"))             # made by a writer that read nothing: no previous, no note
    log.append(attached)
    live = FakeLive()
    with pytest.raises(WriterError) as caught:
        undo_case(tmp_path, live, NOW)
    assert str(caught.value) == "cannot undo article_price on PO 250101, article KM-1001: no value was read before the write, so there is no value to write back"
    [detached] = [e for e in log.entries() if e.undo_of == "e2"]     # the file, undone first, is detached: no value is read for a file
    assert detached.op == inverse(attached.op) and (detached.op.kind, detached.op.document, detached.op.new_value) == ("detach_document", "conf.pdf", None)
    assert live.calls == 1


def test_a_second_undo_of_a_change_made_in_a_system_writes_back_the_value_read_too(tmp_path):
    """The review page's "Terugdraaien in Exact" undoes an entry once more (again); that undo is the same undo."""
    WriteLog(tmp_path).append(make_entry("e1", make_op(old="10", new="11"), status="done", previous="12"))
    live = FakeLive()
    first = undo_entry(tmp_path, "e1", live, NOW)
    assert undo_entry(tmp_path, "e1", live, NOW) is None
    second = undo_entry(tmp_path, "e1", live, NOW, again=True)
    assert [(u.op.old_value, u.op.new_value, u.undo_of) for u in (first, second)] == [("11", "12", "e1")] * 2


def test_the_undo_of_a_prepared_change_is_unchanged(tmp_path):
    """A change that was only prepared is undone by the plain inverse of its operation, its row's old value back: the list of the
    changes to enter names that value. The status decides it, not the entry's previous."""
    w = PreparedWriter(tmp_path)
    [entry] = apply_and_log(tmp_path, w, make_op(old="10", new="11"))
    undo = undo_entry(tmp_path, entry.entry_id, w, NOW + timedelta(hours=1))
    assert undo.op == inverse(entry.op) and (undo.op.old_value, undo.op.new_value) == ("11", "10")
    assert [(r["old_value"], r["new_value"], r["rule_id"]) for r in csv_rows(tmp_path)] == [("10", "11", "R04"), ("11", "10", "undo")]
    other = tmp_path / "other"
    other.mkdir()
    WriteLog(other).append(make_entry("e1", make_op(old="10", new="11"), status="prepared", previous="12"))
    assert undo_entry(other, "e1", FakeLive(), NOW).op == make_op(old="11", new="10")
