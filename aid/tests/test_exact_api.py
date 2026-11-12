"""Tests of the Exact API writer (aid/execution/exact_api.py) against a fake Exact: an http.server on 127.0.0.1 in a thread.

The fake keeps the values it serves in `state` (path -> the fields of the resource), the files attached in `uploads`, and a
record of everything the client sent: `requests` (method and path), `queries`, `headers` and `bodies`, one item per request.
`fail_next` makes the next request (of `fail_method`, if set) answer with that status, `delay` makes a request wait before it
answers, `put_status` is the status of a PUT that worked, and `override(method, path, index)` can answer a request itself
(index counts the requests from 0). Nothing here leaves the machine, and no real address, name or token is used.
"""
import json
import socket
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from aid.execution.exact_api import ExactApiWriter
from aid.execution.ops import WriteOp
from aid.execution.writer import (PreparedWriter, RetryingWriter, UnknownWrite, UnverifiedWrite, WriteLog, WriteLogEntry, WriterError, inverse,
                                  outcome_not_known, undo_case)


def multipart_parts(headers: dict, body: bytes) -> list[tuple[str | None, str | None, bytes, str]]:
    """The parts of a multipart body as (field name, file name, content, content type)."""
    message = BytesParser(policy=policy.default).parsebytes(b"Content-Type: " + headers["Content-Type"].encode() + b"\r\n\r\n" + body)
    return [(part.get_param("name", header="content-disposition"), part.get_filename(), part.get_payload(decode=True),
             part.get_content_type()) for part in message.iter_parts()]


class FakeExact:
    """What the fake server serves and what it saw (see the module docstring)."""

    def __init__(self):
        self.url = ""
        self.state: dict[str, dict] = {}
        self.uploads: dict[str, list[tuple[str, bytes]]] = {}
        self.requests: list[tuple[str, str]] = []
        self.queries: list[dict[str, list[str]]] = []
        self.headers: list[dict[str, str]] = []
        self.bodies: list[bytes] = []
        self.fail_next: int | None = None
        self.fail_method: str | None = None
        self.delay = 0.0
        self.delay_method: str | None = None
        self.put_status = 200
        self.override = None
        self.release = threading.Event()              # set at the end of a test, so that a request that waits returns at once

    def answer(self, method: str, path: str, query: dict, headers: dict, body: bytes, index: int) -> tuple[int, object]:
        """The status and the body for one request: JSON, or bytes that are sent as they are."""
        if self.override is not None and (made := self.override(method, path, index)) is not None:
            return made
        if self.fail_next is not None and self.fail_method in (None, method):
            status, self.fail_next = self.fail_next, None
            return status, {"error": "failing on purpose"}
        if method == "POST":
            files = [(name, content) for field, name, content, _ in multipart_parts(headers, body) if field == "file"]
            self.uploads.setdefault(path, []).extend(files)
            return 201, {"attached": [name for name, _ in files]}
        if method == "DELETE":
            attached = self.uploads.get(path, [])
            kept = [item for item in attached if item[0] not in query.get("name", [])]
            self.uploads[path] = kept
            return (200, {}) if len(kept) < len(attached) else (404, {"error": "no such attachment"})
        if path not in self.state:
            return 404, {"error": "no such resource"}
        if method == "PUT":
            self.state[path].update(json.loads(body))
            return (self.put_status, b"") if self.put_status == 204 else (self.put_status, self.state[path])
        return 200, self.state[path]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):             # keep the output of the tests clean
        pass

    def respond(self):
        fake: FakeExact = self.server.fake
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        headers = dict(self.headers)
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        index = len(fake.requests)
        fake.requests.append((self.command, parts.path))
        fake.queries.append(query)
        fake.headers.append(headers)
        fake.bodies.append(body)
        if fake.delay and fake.delay_method in (None, self.command):
            fake.release.wait(fake.delay)
        status, payload = fake.answer(self.command, parts.path, query, headers, body, index)
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        try:
            self.send_response(status)
            if 300 <= status < 400:
                self.send_header("Location", "/elsewhere")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):    # the client gave up, as the timeout test makes it
            pass

    do_GET = do_PUT = do_POST = do_DELETE = respond


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch):
    """No proxy of the machine stands between the writer and the fake."""
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "127.0.0.1")


@pytest.fixture
def fake_exact():
    fake = FakeExact()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.fake = fake
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield fake
    fake.release.set()
    server.shutdown()
    server.server_close()
    thread.join()


ENDPOINTS = {"po_line": "/po/{po}/v{version}/line/{line}", "article": "/article/{article}", "attachment": "/po/{po}/attach",
             "status": "/po/{po}/status"}
OP = WriteOp(kind="po_line_price", po_number="250101", version=1, line_id="1", old_value="12.50", new_value="12.70")
NOW = datetime(2026, 11, 17, 10, 30)
LINE = "/po/250101/v1/line/1"
ARTICLE = "/article/KM-1001"
STATUS = "/po/250101/status"
ATTACH = "/po/250101/attach"
SECRET = "tok-7f3a9c-made-up"                     # a made-up token, looked for in everything the writer sends, logs and raises


def writer(fake: FakeExact, token=lambda: "t", **more) -> ExactApiWriter:
    return ExactApiWriter(fake.url, ENDPOINTS, token=token, **more)


def op(kind="po_line_price", old="12.50", new="12.70") -> WriteOp:
    return WriteOp(kind=kind, po_number="250101", version=1, line_id="1", old_value=old, new_value=new)


def article_op(old=None, new="12.70") -> WriteOp:
    return WriteOp(kind="article_price", po_number="250101", version=1, article_code="KM-1001", old_value=old, new_value=new)


def status_op() -> WriteOp:
    return WriteOp(kind="set_status", po_number="250101", version=1, old_value="Besteld", new_value="Bevestigd")


def file_op(kind: str, document) -> WriteOp:
    return WriteOp(kind=kind, po_number="250101", version=1, document=str(document))


def test_api_writer_sequence(fake_exact):
    w = ExactApiWriter(fake_exact.url, {"po_line": "/po/{po}/v{version}/line/{line}", "article": "/article/{article}", "attachment": "/po/{po}/attach", "status": "/po/{po}/status"}, token=lambda: "t")
    fake_exact.state["/po/250101/v1/line/1"] = {"price": "12.50"}
    e = w.apply(WriteOp(kind="po_line_price", po_number="250101", version=1, line_id="1", old_value="12.50", new_value="12.70"), "c1", "R04", {}, datetime(2026, 11, 17))
    assert e.status == "done" and e.previous == "12.50" and fake_exact.requests == [("GET", "/po/250101/v1/line/1"), ("PUT", "/po/250101/v1/line/1"), ("GET", "/po/250101/v1/line/1")]
    assert fake_exact.headers[-1]["Authorization"] == "Bearer t"

def test_api_writer_raises_on_error(fake_exact):
    fake_exact.fail_next = 503
    with pytest.raises(WriterError): ExactApiWriter(fake_exact.url, ENDPOINTS, token=lambda: "t").apply(OP, "c1", "R04", {}, datetime(2026, 11, 17))


# The tests below cover what the brief's rulings add to the two above.

# --- the three calls of a value operation, and the field of each kind ---

@pytest.mark.parametrize("operation, path, field, before, after", [
    pytest.param(op(), LINE, "price", "12.50", "12.70", id="po_line_price"),
    pytest.param(op("po_line_date", "2026-11-16", "2026-11-30"), LINE, "delivery_date", "2026-11-16", "2026-11-30", id="po_line_date"),
    pytest.param(op("po_line_quantity", "100", "120"), LINE, "quantity", "100", "120", id="po_line_quantity"),
    pytest.param(article_op("11.00", "12.70"), ARTICLE, "price", "11.00", "12.70", id="article_price"),
    pytest.param(status_op(), STATUS, "status", "Besteld", "Bevestigd", id="set_status"),
])
def test_a_value_operation_reads_writes_and_verifies_the_field_of_its_kind(fake_exact, operation, path, field, before, after):
    fake_exact.state[path] = {field: before, "other": "untouched"}
    entry = writer(fake_exact).apply(operation, "c1", "R04", {"flag_id": "F1"}, NOW)
    assert fake_exact.requests == [("GET", path), ("PUT", path), ("GET", path)]
    assert json.loads(fake_exact.bodies[1]) == {field: after}                  # only the field of the kind is sent
    assert fake_exact.state[path] == {field: after, "other": "untouched"}
    assert (entry.status, entry.previous, entry.new, entry.op, entry.error) == ("done", before, after, operation, None)
    assert entry.evidence == {"flag_id": "F1"}                                  # nothing to note: the value was the one expected


def test_every_request_asks_for_json(fake_exact):
    fake_exact.state[LINE] = {"price": "12.50"}
    writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)
    assert [headers["Accept"] for headers in fake_exact.headers] == ["application/json"] * 3
    assert fake_exact.headers[1]["Content-Type"] == "application/json"          # the PUT sends JSON


def test_the_entry_carries_the_case_the_rule_the_evidence_and_the_time(fake_exact):
    fake_exact.state[LINE] = {"price": "12.50"}
    evidence = {"flag_id": "F1", "source": {"page": 2, "line": 14, "text": "Prijs 12,70 per stuk"}, "values": [1, None, 1.5, True]}
    aware = datetime(2026, 11, 17, 10, 30, tzinfo=timezone(timedelta(hours=1)))
    first = writer(fake_exact).apply(OP, "250101_20261117T103000", "R04", evidence, aware)
    second = writer(fake_exact).apply(OP, "c2", "R05", {}, NOW)
    assert (first.case_id, first.rule_id, first.evidence, first.timestamp, first.undo_of) == \
        ("250101_20261117T103000", "R04", evidence, "2026-11-17T10:30:00+01:00", None)
    assert second.timestamp == "2026-11-17T10:30:00"
    assert uuid.UUID(first.entry_id).version == 4 and first.entry_id == uuid.UUID(first.entry_id).hex      # a UUID4 in hex
    assert first.entry_id != second.entry_id
    assert WriteLogEntry.model_validate_json(first.model_dump_json()) == first                              # it can be logged


def test_the_fields_come_from_a_map_that_the_caller_can_override(fake_exact):
    assert writer(fake_exact).fields == {"po_line_price": "price", "po_line_date": "delivery_date", "po_line_quantity": "quantity",
                                         "article_price": "price", "set_status": "status"}
    fake_exact.state[LINE] = {"NetPrice": "12.50", "price": "decoy"}
    w = writer(fake_exact, fields={"po_line_price": "NetPrice"})
    entry = w.apply(OP, "c1", "R04", {}, NOW)
    assert entry.previous == "12.50" and json.loads(fake_exact.bodies[1]) == {"NetPrice": "12.70"}
    assert fake_exact.state[LINE] == {"NetPrice": "12.70", "price": "decoy"}
    assert w.fields["po_line_date"] == "delivery_date"                          # the other kinds keep their defaults


@pytest.mark.parametrize("fields", [{"attach_document": "file"}, {"po_line": "price"}, {"po_line_price": ""}, {"po_line_price": None}])
def test_fields_for_a_kind_that_has_none_or_with_no_name_are_refused(fake_exact, fields):
    with pytest.raises(ValueError, match="field"):
        writer(fake_exact, fields=fields)


# --- the previous value, and what the entry notes about it ---

def test_an_article_price_without_an_old_value_logs_the_price_it_read(fake_exact):
    fake_exact.state[ARTICLE] = {"price": "11.00"}
    routed = article_op(old=None)
    entry = writer(fake_exact).apply(routed, "c1", "R04", {}, NOW)
    assert entry.previous == "11.00" and entry.new == "12.70"
    assert entry.op == routed.model_copy(update={"old_value": "11.00"})        # the logged op holds what the undo restores
    assert routed.old_value is None                                            # the op the caller holds is not changed
    assert entry.evidence == {}                                                # the old value was unknown, not different
    assert inverse(entry.op).new_value == "11.00"


def test_the_undo_through_the_api_writer_restores_the_price_that_was_read(fake_exact, tmp_path):
    fake_exact.state[ARTICLE] = {"price": "11.00"}
    w = writer(fake_exact)
    log = WriteLog(tmp_path)
    log.append(w.apply(article_op(old=None), "c1", "R04", {}, NOW))
    assert fake_exact.state[ARTICLE] == {"price": "12.70"}
    undone = undo_case(tmp_path, w, NOW + timedelta(hours=1))
    assert fake_exact.state[ARTICLE] == {"price": "11.00"}
    assert [(u.status, u.rule_id, u.previous, u.new, u.undo_of, u.evidence) for u in undone] == \
        [("done", "undo", "12.70", "11.00", log.entries()[0].entry_id, {"undo_of": log.entries()[0].entry_id})]


def test_a_read_value_that_differs_from_the_old_value_is_noted_and_the_change_goes_on(fake_exact):
    fake_exact.state[LINE] = {"price": "12.60"}
    evidence = {"flag_id": "F1"}
    entry = writer(fake_exact).apply(OP, "c1", "R04", evidence, NOW)
    assert entry.status == "done" and entry.previous == "12.60" and entry.new == "12.70"
    assert entry.evidence == {"flag_id": "F1", "read_before": "12.60"}
    assert evidence == {"flag_id": "F1"}                                        # the caller's evidence is not changed
    assert entry.op == OP                                                       # the op is logged as routed
    assert fake_exact.state[LINE] == {"price": "12.70"}


def test_an_empty_field_is_noted_as_the_value_read(fake_exact):
    fake_exact.state[LINE] = {"price": None}
    entry = writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)
    assert entry.previous is None and entry.evidence == {"read_before": None}


@pytest.mark.parametrize("empty", [None, "", "   ", "\t\n"], ids=["null", "empty text", "spaces", "white space"])
def test_a_field_that_is_empty_or_blank_with_no_old_value_to_restore_is_refused_before_the_write(fake_exact, empty):
    fake_exact.state[ARTICLE] = {"price": empty}                                # an undo would have to write this back: it is no value
    with pytest.raises(WriterError, match=r"read GET /article/KM-1001: .*empty.*no old value"):
        writer(fake_exact).apply(article_op(old=None), "c1", "R04", {}, NOW)
    assert fake_exact.requests == [("GET", ARTICLE)]


def test_applying_the_same_operation_twice_is_safe(fake_exact):
    fake_exact.state[LINE] = {"price": "12.50"}
    w = writer(fake_exact)
    first, second = w.apply(OP, "c1", "R04", {}, NOW), w.apply(OP, "c1", "R04", {}, NOW)
    assert (first.status, second.status) == ("done", "done")                    # a PUT of a value that is there verifies clean
    assert (first.previous, second.previous) == ("12.50", "12.70")
    assert first.evidence == {} and second.evidence == {"read_before": "12.70"}
    assert fake_exact.state[LINE] == {"price": "12.70"}
    assert fake_exact.requests == [("GET", LINE), ("PUT", LINE), ("GET", LINE)] * 2


def test_an_operation_without_an_old_value_notes_a_value_read_that_already_is_the_new_one(fake_exact):
    fake_exact.state[ARTICLE] = {"price": "12.70"}
    entry = writer(fake_exact).apply(article_op(old=None), "c1", "R04", {"flag_id": "F1"}, NOW)
    assert (entry.previous, entry.op.old_value, entry.new) == ("12.70", "12.70", "12.70")
    assert entry.evidence == {"flag_id": "F1", "read_before": "12.70"}


# --- a write that was sent and not checked, and the repeat that follows it ---

def unchecked_first_attempt(fake_exact, *indexes):
    """Makes the reads at these request indexes (the reads after a write) answer 503: the write goes through, its check does not."""
    fake_exact.override = lambda method, path, index: (503, {}) if index in indexes else None


def test_a_retried_article_write_logs_the_price_read_before_the_first_attempt(fake_exact, tmp_path):
    fake_exact.state[ARTICLE] = {"price": "11.00"}
    unchecked_first_attempt(fake_exact, 2)                                      # the first attempt writes 12.70 and cannot read it back
    w = writer(fake_exact)
    entry = RetryingWriter(w, PreparedWriter(tmp_path / "prepared"), retries=2).apply(article_op(old=None), "c1", "R04", {}, NOW)
    assert fake_exact.requests == [("GET", ARTICLE), ("PUT", ARTICLE), ("GET", ARTICLE), ("GET", ARTICLE)]     # the repeat only reads
    assert (entry.status, entry.error, entry.previous, entry.new) == ("done", None, "11.00", "12.70")
    assert entry.op.old_value == "11.00"                                        # the real price, not the one the first attempt wrote
    assert entry.evidence == {"read_before": "12.70"}                           # and the entry says what the repeat found
    log = WriteLog(tmp_path)
    log.append(entry)
    undo_case(tmp_path, w, NOW + timedelta(hours=1))
    assert fake_exact.state[ARTICLE] == {"price": "11.00"}                      # so the undo restores it


def test_a_retried_write_with_an_old_value_logs_the_value_read_before_the_first_attempt(fake_exact, tmp_path):
    fake_exact.state[LINE] = {"price": "12.50"}
    unchecked_first_attempt(fake_exact, 2)
    retrying = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path), retries=2)
    entry = retrying.apply(OP, "c1", "R04", {}, NOW)
    assert retrying.name == "exact_api+prepared"
    assert fake_exact.requests == [("GET", LINE), ("PUT", LINE), ("GET", LINE), ("GET", LINE)]
    assert (entry.status, entry.error, entry.previous, entry.evidence) == ("done", None, "12.50", {"read_before": "12.70"})
    assert entry.op == OP


def test_the_first_read_is_kept_over_several_attempts_that_could_not_check(fake_exact, tmp_path):
    fake_exact.state[ARTICLE] = {"price": "11.00"}
    unchecked_first_attempt(fake_exact, 2, 3)                                   # the first attempt writes 12.70; no read for two attempts
    entry = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path), retries=3).apply(article_op(old=None), "c1", "R04", {}, NOW)
    assert fake_exact.requests == [("GET", ARTICLE), ("PUT", ARTICLE)] + [("GET", ARTICLE)] * 3
    assert (entry.status, entry.previous, entry.op.old_value, entry.evidence) == ("done", "11.00", "11.00", {"read_before": "12.70"})
    assert entry.error is None and not (tmp_path / "prepared_changes.csv").exists()


def test_the_first_read_is_kept_when_a_repeat_reads_the_old_value_and_writes_again(fake_exact, tmp_path):
    fake_exact.state[ARTICLE] = {"price": "11.00"}                              # the reads after a write lag: 503, then the old price once
    fake_exact.override = lambda method, path, index: {2: (503, {}), 3: (200, {"price": "11.00"}), 5: (503, {})}.get(index)
    entry = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path), retries=3).apply(article_op(old=None), "c1", "R04", {}, NOW)
    assert fake_exact.requests == [("GET", ARTICLE), ("PUT", ARTICLE), ("GET", ARTICLE)] * 2 + [("GET", ARTICLE)]
    assert (entry.status, entry.error, entry.previous, entry.op.old_value) == ("done", None, "11.00", "11.00")


def test_a_write_that_was_checked_leaves_nothing_to_remember(fake_exact, tmp_path):
    fake_exact.state[ARTICLE] = {"price": "11.00"}
    unchecked_first_attempt(fake_exact, 2)
    w = writer(fake_exact)
    RetryingWriter(w, PreparedWriter(tmp_path), retries=2).apply(article_op(old=None), "c1", "R04", {}, NOW)
    again = w.apply(article_op(old=None), "c1", "R04", {}, NOW)                 # the same change once more, now that it was checked
    assert (again.previous, again.op.old_value, again.evidence) == ("12.70", "12.70", {"read_before": "12.70"})


@pytest.mark.parametrize("found, new", [("12.60", "12.70"), ("12.80", "12.80")],
                         ids=["another value", "the new value of another write"])
def test_what_is_remembered_is_used_only_for_the_value_that_was_written(fake_exact, found, new):
    fake_exact.state[ARTICLE] = {"price": "11.00"}
    unchecked_first_attempt(fake_exact, 2)
    w = writer(fake_exact)
    with pytest.raises(WriterError, match="verify GET"):
        w.apply(article_op(old=None), "c1", "R04", {}, NOW)                     # wrote 12.70, could not check
    fake_exact.state[ARTICLE]["price"] = found                                  # someone else changed the price since
    entry = w.apply(article_op(old=None, new=new), "c1", "R04", {}, NOW)
    assert (entry.previous, entry.op.old_value) == (found, found)               # the value read, not the one before the first attempt


@pytest.mark.parametrize("first, first_path, other, other_path", [
    pytest.param(op(), LINE, WriteOp(kind="po_line_price", po_number="250101", version=1, line_id="2", new_value="12.70"),
                 "/po/250101/v1/line/2", id="line"),
    pytest.param(op(), LINE, WriteOp(kind="po_line_price", po_number="250101", version=2, line_id="1", new_value="12.70"),
                 "/po/250101/v2/line/1", id="version"),
    pytest.param(op(), LINE, WriteOp(kind="po_line_price", po_number="250102", version=1, line_id="1", new_value="12.70"),
                 "/po/250102/v1/line/1", id="PO"),
    pytest.param(op(), LINE, WriteOp(kind="po_line_quantity", po_number="250101", version=1, line_id="1", new_value="12.70"),
                 LINE, id="kind"),
    pytest.param(article_op(old=None), ARTICLE,
                 WriteOp(kind="article_price", po_number="250101", version=1, article_code="KM-1002", new_value="12.70"),
                 "/article/KM-1002", id="article"),
])
def test_what_is_remembered_belongs_to_its_target(fake_exact, first, first_path, other, other_path):
    fake_exact.state[first_path] = {"price": "12.50", "quantity": "12.70"}
    unchecked_first_attempt(fake_exact, 2)
    w = writer(fake_exact)
    with pytest.raises(WriterError, match="verify GET"):
        w.apply(first, "c1", "R04", {}, NOW)                                    # wrote 12.70, could not check
    fake_exact.state.setdefault(other_path, {"price": "12.70", "quantity": "12.70"})     # the other target holds 12.70 on its own
    entry = w.apply(other, "c1", "R04", {}, NOW)
    assert (entry.previous, entry.op.old_value) == ("12.70", "12.70")           # what it read: the first write is not its history


# --- a write the system accepted whose check failed, and what the repeat makes of it ---

def reads_fail_after_the_write(fake_exact):
    """Every read after the first write answers 503: the write goes through and no attempt can check it."""
    fake_exact.override = lambda method, path, index: (503, {}) if method == "GET" and index > 1 else None


@pytest.mark.parametrize("answer, failed", [
    pytest.param((503, {}), "verify GET /article/KM-1001: HTTP 503", id="the read fails"),
    pytest.param((200, b"<html>maintenance</html>"), "verify GET /article/KM-1001: HTTP 200, the body is not JSON", id="no JSON"),
    pytest.param((200, {"price": "11.50"}), "verify GET /article/KM-1001: HTTP 200, read back '11.50' after writing '11'", id="another value"),
])
def test_the_api_writer_alone_raises_the_unverified_write_with_its_entry(fake_exact, answer, failed):
    """The system accepted the write and the check failed, or read a value that is neither the new one nor the one from before:
    the error carries the entry of the attempt, made, with the price read before the write and the failed check."""
    fake_exact.state[ARTICLE] = {"price": "12"}
    fake_exact.override = lambda method, path, index: answer if index == 2 else None
    with pytest.raises(UnverifiedWrite) as caught:
        writer(fake_exact).apply(article_op(old=None, new="11"), "c1", "R04", {"flag_id": "F1"}, NOW)
    assert isinstance(caught.value, WriterError) and str(caught.value) == failed
    assert caught.value.__cause__ is None and caught.value.__context__ is None       # nothing of the failed call to quote from
    entry = caught.value.entry
    assert (entry.status, entry.previous, entry.op.old_value, entry.new, entry.error) == ("done", "12", "12", "11", failed)
    assert (entry.case_id, entry.rule_id, entry.evidence, entry.timestamp) == ("c1", "R04", {"flag_id": "F1"}, NOW.isoformat())
    assert WriteLogEntry.model_validate_json(entry.model_dump_json()) == entry        # it can be logged
    assert fake_exact.requests == [("GET", ARTICLE), ("PUT", ARTICLE), ("GET", ARTICLE)] and fake_exact.state[ARTICLE] == {"price": "11"}


def test_a_check_that_reads_the_value_from_before_the_write_is_a_plain_writer_error(fake_exact):
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.override = lambda method, path, index: (200, {"price": "12.50"}) if method == "PUT" else None     # the write is ignored
    with pytest.raises(WriterError, match="verify GET .*read back '12.50' after writing '12.70'") as caught:
        writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)
    assert type(caught.value) is WriterError                                    # not an unverified write: the system holds the old value


def test_the_value_from_before_is_told_by_its_value_not_by_its_text(fake_exact):
    fake_exact.state[LINE] = {"price": 12.5}                                    # read as "12.5" before the write
    fake_exact.override = lambda method, path, index: (200, {"price": "12.50"}) if index > 0 else None       # ignored; read back as "12.50"
    with pytest.raises(WriterError, match="verify GET .*read back '12.50' after writing '12.70'") as caught:
        writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)
    assert type(caught.value) is WriterError


def test_an_empty_field_read_back_is_the_value_from_before_only_when_the_field_was_empty(fake_exact):
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.override = lambda method, path, index: (200, {"price": None}) if index == 2 else None         # the check reads a null
    with pytest.raises(UnverifiedWrite, match="verify GET .*read back None after writing '12.70'") as caught:
        writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)                      # neither the new value nor the one from before
    assert (caught.value.entry.status, caught.value.entry.previous) == ("done", "12.50")
    fake_exact.state[LINE] = {"price": None}
    fake_exact.override = lambda method, path, index: (200, {"price": None}) if method == "PUT" else None      # empty before, ignored
    with pytest.raises(WriterError, match="read back None after writing '12.70'") as ignored:
        writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)
    assert type(ignored.value) is WriterError


def test_an_accepted_article_price_whose_checks_fail_is_logged_as_made_with_the_price_read(fake_exact, tmp_path):
    """The outside review's example: an article price from 12 to 11, the rule knowing no old value, and no read after the write
    succeeds. The change is in the system, so it is logged as made, with the 12 read before the first attempt, and nothing is
    prepared; its undo puts the 12 back."""
    fake_exact.state[ARTICLE] = {"price": "12"}
    reads_fail_after_the_write(fake_exact)
    w = writer(fake_exact)
    retrying = RetryingWriter(w, PreparedWriter(tmp_path / "prepared"), retries=2)
    entry = retrying.apply(article_op(old=None, new="11"), "c1", "R04", {}, NOW)
    assert fake_exact.requests == [("GET", ARTICLE), ("PUT", ARTICLE), ("GET", ARTICLE), ("GET", ARTICLE)]
    assert fake_exact.state[ARTICLE] == {"price": "11"}
    assert (entry.status, entry.previous, entry.op.old_value, entry.new) == ("done", "12", "12", "11")
    assert entry.error == "verify GET /article/KM-1001: HTTP 503"                # the check of the write the system accepted
    assert not (tmp_path / "prepared").exists()                                 # nothing is prepared for a change the system holds
    WriteLog(tmp_path).append(entry)
    fake_exact.override = None                                                  # the reads work again
    [undone] = undo_case(tmp_path, retrying, NOW + timedelta(hours=1))
    assert fake_exact.state[ARTICLE] == {"price": "12"} and fake_exact.bodies[5] == b'{"price": "12"}'
    assert (undone.status, undone.previous, undone.new, undone.error) == ("done", "11", "12", None)


def test_a_repeat_that_finds_the_value_it_sent_counts_the_read_as_the_check(fake_exact, tmp_path):
    """The review's example with its own requests: the reads at 2 and 5 answer 503. The repeat reads the 11 the first attempt sent,
    which checks that write: the entry is made and verified with the 12 as its previous, and nothing is written again."""
    fake_exact.state[ARTICLE] = {"price": "12"}
    unchecked_first_attempt(fake_exact, 2, 5)
    entry = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path / "prepared"), retries=2).apply(article_op(old=None, new="11"), "c1", "R04", {}, NOW)
    assert fake_exact.requests == [("GET", ARTICLE), ("PUT", ARTICLE), ("GET", ARTICLE), ("GET", ARTICLE)]
    assert [json.loads(body) for body in fake_exact.bodies if body] == [{"price": "11"}]                # one write
    assert (entry.status, entry.error, entry.previous, entry.op.old_value, entry.new) == ("done", None, "12", "12", "11")
    assert entry.evidence == {"read_before": "11"} and not (tmp_path / "prepared").exists()


@pytest.mark.parametrize("lost", [502, 504], ids=["bad gateway", "gateway timeout"])
def test_a_write_whose_answer_was_lost_is_found_by_the_repeat_and_not_prepared(fake_exact, tmp_path, lost):
    """The system makes the change and the answer to the write is an error of something in between. The repeat finds the value the
    first attempt sent and counts that read as the check."""
    def answer_lost(method, path, index):
        if method == "PUT":
            fake_exact.state[path].update({"price": "11"})
            return lost, {}
        return None

    fake_exact.state[ARTICLE] = {"price": "12"}
    fake_exact.override = answer_lost
    w = writer(fake_exact)
    with pytest.raises(UnknownWrite) as caught:
        w.apply(article_op(old=None, new="11"), "c1", "R04", {}, NOW)
    assert str(caught.value) == f"write PUT /article/KM-1001: HTTP {lost}"                # made or not: this answer does not say
    entry = RetryingWriter(w, PreparedWriter(tmp_path / "prepared"), retries=2).apply(article_op(old=None, new="11"), "c1", "R04", {}, NOW)
    assert fake_exact.requests == [("GET", ARTICLE), ("PUT", ARTICLE), ("GET", ARTICLE)]              # the repeat read, and wrote nothing
    assert (entry.status, entry.error, entry.previous, entry.op.old_value) == ("done", None, "12", "12")
    assert not (tmp_path / "prepared").exists()


def test_a_po_line_changed_while_the_system_held_another_value_than_the_document_is_undone_to_the_value_read(fake_exact, tmp_path):
    """The outside review's second example: the document says 10, the system holds 12, the change to 11 is made. The undo writes
    the 12 back, the value read before the write, not the document's 10."""
    fake_exact.state[LINE] = {"price": "12"}
    w = writer(fake_exact)
    entry = w.apply(op(old="10", new="11"), "c1", "R04", {}, NOW)
    assert (entry.status, entry.previous, entry.op.old_value, entry.evidence) == ("done", "12", "10", {"read_before": "12"})
    WriteLog(tmp_path).append(entry)
    [undone] = undo_case(tmp_path, w, NOW + timedelta(hours=1))
    assert fake_exact.requests[3:] == [("GET", LINE), ("PUT", LINE), ("GET", LINE)] and fake_exact.bodies[4] == b'{"price": "12"}'
    assert fake_exact.state[LINE] == {"price": "12"}
    assert (undone.status, undone.previous, undone.new, undone.op.old_value, undone.op.new_value) == ("done", "11", "12", "11", "12")
    assert undone.evidence == {"undo_of": entry.entry_id}                       # the undo found the 11 it takes away: nothing to note


def test_both_at_once_the_unverified_change_of_a_line_is_undone_to_the_value_read(fake_exact, tmp_path):
    fake_exact.state[LINE] = {"price": "12"}
    reads_fail_after_the_write(fake_exact)
    retrying = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path / "prepared"), retries=2)
    entry = retrying.apply(op(old="10", new="11"), "c1", "R04", {}, NOW)
    assert (entry.status, entry.previous, entry.op.old_value, entry.error) == ("done", "12", "10", "verify GET /po/250101/v1/line/1: HTTP 503")
    assert entry.evidence == {"read_before": "12"} and not (tmp_path / "prepared").exists()
    WriteLog(tmp_path).append(entry)
    fake_exact.override = None
    undo_case(tmp_path, retrying, NOW + timedelta(hours=1))
    assert fake_exact.state[LINE] == {"price": "12"}


def test_a_check_that_keeps_reading_another_value_is_made_and_not_verified(fake_exact, tmp_path):
    """A system that stores another value than the one sent (here 11.50 for 11). The first check reads neither the new value nor
    the one from before; the repeat reads that value, writes again and reads it once more, which by itself would say that the
    write was ignored. One accepted write that no attempt verified is enough: logged as made, with the 12 read first."""
    def stores_another_value(method, path, index):
        if method == "PUT":
            fake_exact.state[path].update({"price": "11.50"})
            return 200, {}
        return None

    fake_exact.state[ARTICLE] = {"price": "12"}
    fake_exact.override = stores_another_value
    entry = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path / "prepared"), retries=2).apply(article_op(old=None, new="11"), "c1", "R04", {}, NOW)
    assert fake_exact.requests == [("GET", ARTICLE), ("PUT", ARTICLE), ("GET", ARTICLE)] * 2
    assert (entry.status, entry.previous, entry.op.old_value) == ("done", "12", "12")
    assert entry.error == "verify GET /article/KM-1001: HTTP 200, read back '11.50' after writing '11'"
    assert not (tmp_path / "prepared").exists()


@pytest.mark.parametrize("put, error", [
    pytest.param(200, "verify GET /po/250101/v1/line/1: HTTP 200, read back '12.50' after writing '12.70'", id="ignored"),
    pytest.param(409, "write PUT /po/250101/v1/line/1: HTTP 409", id="refused"),
    pytest.param(400, "write PUT /po/250101/v1/line/1: HTTP 400", id="refused as a bad request"),
])
def test_a_write_the_system_ignored_or_refused_is_still_prepared(fake_exact, tmp_path, put, error):
    """A write answered with success that left the value from before in place, on every attempt, was ignored; one answered with
    a refusal (4xx) was not made. Either way the system holds the old value and the change is prepared, as before."""
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.override = lambda method, path, index: (put, {"price": "12.50"}) if method == "PUT" else None
    entry = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path / "prepared"), retries=2).apply(OP, "c1", "R04", {}, NOW)
    assert fake_exact.requests == ([("GET", LINE), ("PUT", LINE), ("GET", LINE)] if put == 200 else [("GET", LINE), ("PUT", LINE)]) * 2
    assert (entry.status, entry.previous, entry.new, entry.error) == ("prepared", "12.50", "12.70", error)
    assert fake_exact.state[LINE] == {"price": "12.50"} and "12.70" in (tmp_path / "prepared" / "prepared_changes.csv").read_text()


# --- a write whose outcome is not known: no answer, or an answer that says neither made nor refused ---

class Dropping(requests.Session):
    """A session that gets no answer to its first `times` requests of one method: it raises `fault` for them, after handing the
    request to the server when `deliver` is true (the system then makes the change and the client never learns it)."""

    def __init__(self, method: str, fault: Exception, deliver: bool, times: int = 1):
        super().__init__()
        self.method, self.fault, self.deliver, self.left = method, fault, deliver, times

    def request(self, method, url, **kwargs):
        if method == self.method and self.left > 0:
            self.left -= 1
            if self.deliver:
                super().request(method, url, **kwargs)
            raise self.fault
        return super().request(method, url, **kwargs)


def answers(fake_exact, *script):
    """Answer the requests in turn by the script, one word per request; the requests after it are answered as they are. A read:
    "ok" (the value the system holds) or "503". A write: "ok" (made), "lost" (made, answered 502), "busy" (not made, answered
    503), "ignored" (not made, answered 200) or a status such as "409" (not made, refused)."""
    def answer(method, path, index):
        word = script[index] if index < len(script) else "ok"
        if word == "ok":
            return None
        if method == "GET":
            return int(word), {}
        if word == "lost":
            fake_exact.state[path].update(json.loads(fake_exact.bodies[index]))
            return 502, {}
        return (200, fake_exact.state[path]) if word == "ignored" else (503 if word == "busy" else int(word), {})
    fake_exact.override = answer


@pytest.mark.parametrize("status", [500, 502, 503, 504, 302])
def test_a_write_answered_with_neither_success_nor_a_refusal_is_an_unknown_write_with_its_entry(fake_exact, status):
    """A gateway's 502 can follow a write that was made, and a 503 one that was not: the answer does not say. The error carries
    the entry of the attempt, with the value read before the write and what happened to the write."""
    fake_exact.state[LINE] = {"price": "12.60"}
    fake_exact.fail_next, fake_exact.fail_method = status, "PUT"
    with pytest.raises(UnknownWrite) as caught:
        writer(fake_exact).apply(OP, "c1", "R04", {"flag_id": "F1"}, NOW)
    happened = f"write PUT /po/250101/v1/line/1: HTTP {status}"
    assert isinstance(caught.value, WriterError) and not isinstance(caught.value, UnverifiedWrite) and str(caught.value) == happened
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    entry = caught.value.entry
    assert (entry.status, entry.previous, entry.op, entry.new, entry.error) == ("done", "12.60", OP, "12.70", happened)
    assert entry.evidence == {"flag_id": "F1", "read_before": "12.60"} and WriteLogEntry.model_validate_json(entry.model_dump_json()) == entry
    assert fake_exact.requests == [("GET", LINE), ("PUT", LINE)]


@pytest.mark.parametrize("fault, deliver, happened", [
    pytest.param(requests.ReadTimeout("no answer"), True, "no answer within 20 s", id="timeout, the system made the write"),
    pytest.param(requests.ReadTimeout("no answer"), False, "no answer within 20 s", id="timeout, the write never arrived"),
    pytest.param(requests.ConnectionError("lost"), True, "ConnectionError", id="connection lost"),
])
def test_a_write_that_got_no_answer_is_an_unknown_write(fake_exact, fault, deliver, happened):
    fake_exact.state[LINE] = {"price": "12.60"}
    with pytest.raises(UnknownWrite) as caught:
        writer(fake_exact, session=Dropping("PUT", fault, deliver)).apply(OP, "c1", "R04", {}, NOW)
    assert str(caught.value) == f"write PUT /po/250101/v1/line/1: {happened}" == caught.value.entry.error
    assert caught.value.__cause__ is None and caught.value.__context__ is None       # nothing of the fault to quote from
    assert (caught.value.entry.status, caught.value.entry.previous) == ("done", "12.60")
    assert fake_exact.state[LINE] == {"price": "12.70" if deliver else "12.60"}


@pytest.mark.parametrize("fault", [requests.ConnectTimeout("connect timed out"), requests.exceptions.SSLError("handshake failed"),
                                   requests.exceptions.ProxyError("no tunnel")], ids=["connect time-out", "TLS failure", "proxy failure"])
def test_a_write_over_a_connection_that_was_never_made_is_a_plain_failure(fake_exact, tmp_path, fault):
    """Nothing was sent, so the outcome is known: not made. The change is prepared, a value and an attachment alike."""
    document = tmp_path / "conf.pdf"
    document.write_bytes(b"x")
    fake_exact.state[LINE] = {"price": "12.60"}
    for method, operation in (("PUT", OP), ("POST", file_op("attach_document", document))):
        w = writer(fake_exact, session=Dropping(method, fault, deliver=False, times=2))
        with pytest.raises(WriterError) as caught:
            w.apply(operation, "c1", "R04", {}, NOW)
        assert type(caught.value) is WriterError and caught.value.__context__ is None
        entry = RetryingWriter(writer(fake_exact, session=Dropping(method, fault, deliver=False, times=2)),
                               PreparedWriter(tmp_path / method)).apply(operation, "c1", "R04", {}, NOW)
        assert entry.status == "prepared" and not outcome_not_known(entry)
    assert fake_exact.state[LINE] == {"price": "12.60"} and fake_exact.uploads == {}


def test_an_attach_to_an_address_where_nothing_listens_is_prepared(tmp_path):
    """The check's proof: the connection is refused, so no request is sent. Before, the attachment was logged as made and not
    sure, and the writer never sent that file again."""
    with socket.socket() as probe:                                              # a port that was free a moment ago and has no listener
        probe.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{probe.getsockname()[1]}"
    document = tmp_path / "conf.pdf"
    document.write_bytes(b"x")
    route = RetryingWriter(ExactApiWriter(url, ENDPOINTS, token=lambda: "t", timeout=2.0), PreparedWriter(tmp_path / "prepared"))
    entry = route.apply(file_op("attach_document", document), "c1", "R01", {}, NOW)
    assert (entry.status, entry.error) == ("prepared", "attach POST /po/250101/attach: ConnectionError") and not outcome_not_known(entry)
    assert len((tmp_path / "prepared" / "prepared_changes.csv").read_text().splitlines()) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429])
def test_a_write_the_system_refused_is_a_plain_failure(fake_exact, status):
    fake_exact.state[LINE] = {"price": "12.60"}
    fake_exact.fail_next, fake_exact.fail_method = status, "PUT"
    with pytest.raises(WriterError) as caught:
        writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)
    assert type(caught.value) is WriterError and str(caught.value) == f"write PUT /po/250101/v1/line/1: HTTP {status}"


@pytest.mark.parametrize("token, happened", [("", "the token is empty"), (f"{SECRET}\nsecond line", "InvalidHeader"), (f"{SECRET}\u20ac", "UnicodeEncodeError")],
                         ids=["no token", "a header requests refuses", "a header that cannot be encoded"])
def test_a_write_that_was_never_sent_is_a_plain_failure_and_is_prepared(fake_exact, tmp_path, token, happened):
    """The token of the write is one that cannot be sent, on both attempts: nothing left the client, so the outcome is known."""
    fake_exact.state[LINE] = {"price": "12.60"}
    tokens = iter(["t", token, "t", token])
    entry = RetryingWriter(writer(fake_exact, token=lambda: next(tokens)), PreparedWriter(tmp_path / "prepared")).apply(OP, "c1", "R04", {}, NOW)
    assert (entry.status, entry.error) == ("prepared", f"write PUT /po/250101/v1/line/1: {happened}") and not outcome_not_known(entry)
    assert fake_exact.requests == [("GET", LINE)] * 2 and fake_exact.state[LINE] == {"price": "12.60"}


@pytest.mark.parametrize("script, retries, held", [
    pytest.param(("ok", "lost"), 1, "11", id="ok lost"),
    pytest.param(("ok", "lost", "503"), 2, "11", id="ok lost 503"),
    pytest.param(("503", "ok", "lost"), 2, "11", id="503 ok lost"),
    pytest.param(("ok", "busy", "ok", "lost"), 2, "11", id="ok busy ok lost"),
    pytest.param(("ok", "ignored", "ok", "ok", "lost"), 2, "11", id="ok ignored ok ok lost"),
    pytest.param(("ok", "409", "ok", "lost"), 2, "11", id="ok refused ok lost"),
    pytest.param(("ok", "busy"), 1, "12", id="ok busy"),
    pytest.param(("ok", "busy", "ok", "busy"), 2, "12", id="ok busy ok busy"),
    pytest.param(("ok", "busy", "503"), 2, "12", id="ok busy 503"),
])
def test_a_write_whose_last_outcome_is_not_known_is_logged_as_not_sure_and_never_prepared(fake_exact, tmp_path, script, retries, held):
    """The sequences the review found ending "prepared" while the system held the new value, and their kin: the last write of the
    operation got an answer that does not say whether it was made, and no read after it settled it. The entry is made as far as
    the log goes, with the 12 read before the first attempt and what happened to the write, and carries the note that its
    outcome is not known; nothing is prepared; the undo puts the 12 back."""
    fake_exact.state[ARTICLE] = {"price": "12"}
    answers(fake_exact, *script)
    retrying = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path / "prepared"), retries=retries)
    entry = retrying.apply(article_op(old=None, new="11"), "c1", "R04", {}, NOW)
    assert len(fake_exact.requests) == len(script) and fake_exact.state[ARTICLE] == {"price": held}
    assert (entry.status, entry.previous, entry.op.old_value, entry.new) == ("done", "12", "12", "11") and outcome_not_known(entry)
    assert entry.error == f"write PUT /article/KM-1001: HTTP {502 if held == '11' else 503}"
    assert not (tmp_path / "prepared").exists()
    WriteLog(tmp_path).append(entry)
    fake_exact.override = None
    undo_case(tmp_path, retrying, NOW + timedelta(hours=1))
    assert fake_exact.state[ARTICLE] == {"price": "12"}


@pytest.mark.parametrize("script, error", [
    pytest.param(("ok", "busy", "ok", "409"), "write PUT /article/KM-1001: HTTP 409", id="a 5xx, the old value read, a refusal"),
    pytest.param(("ok", "busy", "ok", "ignored", "ok"), "verify GET /article/KM-1001: HTTP 200, read back '12' after writing '11'",
                 id="a 5xx, the old value read, an ignored write"),
    pytest.param(("ok", "409", "ok", "409"), "write PUT /article/KM-1001: HTTP 409", id="refused twice"),
    pytest.param(("ok", "409", "503"), "read GET /article/KM-1001: HTTP 503", id="refused, then no read"),
])
def test_a_write_the_system_did_not_make_is_prepared(fake_exact, tmp_path, script, error):
    """A read of the value from before settles a write that was answered with neither success nor a refusal: it was not made. When
    the write after it is refused or ignored too, every outcome is known and the change is prepared, as after any failed attempt."""
    fake_exact.state[ARTICLE] = {"price": "12"}
    answers(fake_exact, *script)
    entry = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path / "prepared"), retries=2).apply(article_op(old=None, new="11"), "c1", "R04", {}, NOW)
    assert len(fake_exact.requests) == len(script) and fake_exact.state[ARTICLE] == {"price": "12"}
    assert (entry.status, entry.error) == ("prepared", error) and not outcome_not_known(entry)
    assert len((tmp_path / "prepared" / "prepared_changes.csv").read_text().splitlines()) == 2


@pytest.mark.parametrize("deliver, script, held", [
    pytest.param(False, ("ok", "ok", "409"), "12", id="the old value read, then a refusal"),
    pytest.param(False, ("ok", "ok", "ignored", "ok"), "12", id="the old value read, then an ignored write"),
    pytest.param(False, ("ok", "503"), "12", id="no read after it"),
    pytest.param(True, ("ok", "ok", "503"), "11", id="made, no read after it"),
])
def test_a_write_that_got_no_answer_is_not_settled_by_a_read_of_the_old_value(fake_exact, tmp_path, deliver, script, held):
    """No answer came to the first write (a timeout), so it can still land: reading the old value afterwards settles nothing, and
    neither does a refusal of the repeat's own write. The operation ends not sure, with the 12 read first. (A write that was not
    delivered is none of the requests the stand-in counts.)"""
    fake_exact.state[ARTICLE] = {"price": "12"}
    answers(fake_exact, *script)
    session = Dropping("PUT", requests.ReadTimeout("no answer"), deliver)
    retrying = RetryingWriter(writer(fake_exact, session=session), PreparedWriter(tmp_path / "prepared"), retries=2)
    entry = retrying.apply(article_op(old=None, new="11"), "c1", "R04", {}, NOW)
    assert (entry.status, entry.previous, entry.error) == ("done", "12", "write PUT /article/KM-1001: no answer within 20 s")
    assert outcome_not_known(entry) and not (tmp_path / "prepared").exists() and fake_exact.state[ARTICLE] == {"price": held}


def test_a_write_that_got_no_answer_and_landed_is_found_by_the_repeat(fake_exact, tmp_path):
    fake_exact.state[ARTICLE] = {"price": "12"}
    session = Dropping("PUT", requests.ReadTimeout("no answer"), deliver=True)
    entry = RetryingWriter(writer(fake_exact, session=session), PreparedWriter(tmp_path / "prepared")).apply(article_op(old=None, new="11"), "c1", "R04", {}, NOW)
    assert (entry.status, entry.error, entry.previous, entry.evidence) == ("done", None, "12", {"read_before": "11"})
    assert fake_exact.requests == [("GET", ARTICLE), ("PUT", ARTICLE), ("GET", ARTICLE)] and not (tmp_path / "prepared").exists()


def test_a_later_call_that_cannot_read_gets_an_entry_of_its_own_for_the_write_still_in_doubt(fake_exact, tmp_path):
    """The writer remembers the write in doubt, not the entry: a later call for the same change, here of another case, whose read
    fails raises with an entry of that call, with the value read before the first write as its previous."""
    fake_exact.state[ARTICLE] = {"price": "12"}
    answers(fake_exact, "ok", "lost", "503")
    w = writer(fake_exact)
    with pytest.raises(UnknownWrite) as first:
        w.apply(article_op(old=None, new="11"), "c1", "R04", {}, NOW)
    with pytest.raises(UnknownWrite) as second:
        w.apply(article_op(old=None, new="11"), "c2", "R05", {"flag_id": "F2"}, NOW + timedelta(minutes=5))
    assert str(second.value) == "read GET /article/KM-1001: HTTP 503" and second.value.__context__ is None
    one, other = first.value.entry, second.value.entry
    assert other.entry_id != one.entry_id and (other.case_id, other.rule_id, other.evidence) == ("c2", "R05", {"flag_id": "F2"})
    assert (other.status, other.previous, other.op.old_value, other.error) == ("done", "12", "12", "write PUT /article/KM-1001: HTTP 502")
    assert other.timestamp == (NOW + timedelta(minutes=5)).isoformat()


# --- an attachment or a detachment whose outcome is not known ---

def test_an_attach_whose_answer_is_lost_is_not_sent_again_and_is_logged_as_not_sure(fake_exact, tmp_path):
    """The file is attached and the answer is a gateway's error. A second POST would attach it twice, and nothing can be read to
    show that it is not there, so the repeat sends nothing: the attachment is logged as not sure, and nothing is prepared."""
    document = tmp_path / "conf.pdf"
    document.write_bytes(b"%PDF made up")

    def answer_lost(method, path, index):
        if method == "POST":
            fake_exact.uploads.setdefault(path, []).append(("conf.pdf", b"%PDF made up"))
            return 504, {}
        return None

    fake_exact.override = answer_lost
    attach = file_op("attach_document", document)
    entry = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path / "prepared")).apply(attach, "c1", "R01", {}, NOW)
    assert fake_exact.requests == [("POST", ATTACH)] and fake_exact.uploads[ATTACH] == [("conf.pdf", b"%PDF made up")]
    assert (entry.status, entry.op, entry.previous, entry.error) == ("done", attach, None, "attach POST /po/250101/attach: HTTP 504")
    assert outcome_not_known(entry) and not (tmp_path / "prepared").exists()


def test_an_attach_that_got_no_answer_is_not_sent_again_and_one_that_was_refused_is(fake_exact, tmp_path):
    document = tmp_path / "conf.pdf"
    document.write_bytes(b"x")
    attach = file_op("attach_document", document)
    silent = RetryingWriter(writer(fake_exact, session=Dropping("POST", requests.ReadTimeout("no answer"), deliver=False)),
                            PreparedWriter(tmp_path / "prepared"))
    entry = silent.apply(attach, "c1", "R01", {}, NOW)
    assert (entry.status, entry.error) == ("done", "attach POST /po/250101/attach: no answer within 20 s") and outcome_not_known(entry)
    assert fake_exact.requests == [] and not (tmp_path / "prepared").exists()
    fake_exact.override = lambda method, path, index: (413, {}) if method == "POST" else None       # refused: not made, so it is tried again
    refused = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path / "prepared")).apply(attach, "c1", "R01", {}, NOW)
    assert (refused.status, refused.error) == ("prepared", "attach POST /po/250101/attach: HTTP 413") and not outcome_not_known(refused)
    assert fake_exact.requests == [("POST", ATTACH)] * 2 and fake_exact.uploads == {}


def test_an_attach_in_doubt_whose_document_cannot_be_read_at_the_repeat_is_not_prepared(fake_exact, tmp_path, monkeypatch):
    """The check's proof: the POST arrives and its answer does not; at the repeat another program holds the document for a
    moment. The doubt is looked at before the document is read, so the attachment ends not sure, not prepared."""
    import pathlib

    document = tmp_path / "confirmation.pdf"
    document.write_bytes(b"%PDF-1.4 made up")
    real, reads = pathlib.Path.read_bytes, []

    def read_bytes(self):
        if self.name == "confirmation.pdf":
            reads.append(self)
            if len(reads) == 2:
                raise PermissionError(13, "held by another process", str(self))
        return real(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", read_bytes)
    session = Dropping("POST", requests.ReadTimeout("no answer"), deliver=True)
    entry = RetryingWriter(writer(fake_exact, session=session), PreparedWriter(tmp_path / "prepared")).apply(
        file_op("attach_document", document), "c1", "R01", {}, NOW)
    assert (entry.status, entry.error) == ("done", "attach POST /po/250101/attach: no answer within 20 s") and outcome_not_known(entry)
    assert not (tmp_path / "prepared").exists() and [name for name, _ in fake_exact.uploads[ATTACH]] == ["confirmation.pdf"]


def test_a_write_in_doubt_whose_repeat_reads_an_empty_field_is_not_prepared(fake_exact, tmp_path):
    """The check's proof: an article price without an old value, no answer to its write, and the repeat reads an empty field.
    The refusal of an empty field is no plain failure while a write is in doubt: not sure, with the 12 read first."""
    fake_exact.state[ARTICLE] = {"price": "12"}
    fake_exact.override = lambda method, path, index: (200, {"price": None}) if index == 1 else None       # the repeat's read
    session = Dropping("PUT", requests.ReadTimeout("no answer"), deliver=False)
    entry = RetryingWriter(writer(fake_exact, session=session), PreparedWriter(tmp_path / "prepared")).apply(article_op(old=None, new="11"), "c1", "R04", {}, NOW)
    assert (entry.status, entry.previous, entry.op.old_value) == ("done", "12", "12") and outcome_not_known(entry)
    assert entry.error == "write PUT /article/KM-1001: no answer within 20 s" and not (tmp_path / "prepared").exists()


def test_a_file_that_was_detached_is_no_longer_in_doubt(fake_exact, tmp_path):
    """The undo of an attachment that is not sure detaches the file. After that success nothing of the file is in doubt: the
    same writer attaches it again when asked."""
    document = tmp_path / "conf.pdf"
    document.write_bytes(b"x")

    def answer_lost_once(method, path, index):
        if method == "POST" and index == 0:
            fake_exact.uploads.setdefault(path, []).append(("conf.pdf", b"x"))
            return 502, {}
        return None

    fake_exact.override = answer_lost_once
    w = writer(fake_exact)
    retrying = RetryingWriter(w, PreparedWriter(tmp_path / "prepared"))
    entry = retrying.apply(file_op("attach_document", document), "c1", "R01", {}, NOW)
    assert outcome_not_known(entry) and fake_exact.requests == [("POST", ATTACH)]
    WriteLog(tmp_path).append(entry)
    [undone] = undo_case(tmp_path, retrying, NOW + timedelta(hours=1))
    assert (undone.op.kind, undone.status, undone.error) == ("detach_document", "done", None) and fake_exact.uploads[ATTACH] == []
    again = retrying.apply(file_op("attach_document", document), "c2", "R01", {}, NOW + timedelta(hours=2))
    assert (again.status, again.error) == ("done", None) and fake_exact.uploads[ATTACH] == [("conf.pdf", b"x")]
    assert fake_exact.requests == [("POST", ATTACH), ("DELETE", ATTACH), ("POST", ATTACH)]


def test_a_detach_in_doubt_is_sent_again_and_only_a_success_settles_it(fake_exact, tmp_path):
    """A DELETE can be sent twice without harm. When the first one was made and its answer lost, the repeat is answered that
    there is no such file: that does not say the file was kept, so the detachment is not sure, not prepared. When the first one
    was not made, the repeat makes it."""
    detach = file_op("detach_document", tmp_path / "conf.pdf")

    def answer_lost(method, path, index):
        if method == "DELETE" and index == 0:
            fake_exact.uploads[path] = []
            return 502, {}
        return None

    fake_exact.uploads[ATTACH] = [("conf.pdf", b"x")]
    fake_exact.override = answer_lost
    lost = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path / "prepared")).apply(detach, "c1", "undo", {}, NOW)
    assert (lost.status, lost.error) == ("done", "detach DELETE /po/250101/attach: HTTP 502") and outcome_not_known(lost)
    assert fake_exact.requests == [("DELETE", ATTACH)] * 2 and not (tmp_path / "prepared").exists()
    fake_exact.uploads[ATTACH] = [("conf.pdf", b"x")]
    fake_exact.override = lambda method, path, index: (503, {}) if index == 2 else None           # not made, then made
    made = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path / "prepared")).apply(detach, "c1", "undo", {}, NOW)
    assert (made.status, made.error) == ("done", None) and fake_exact.uploads[ATTACH] == [] and len(fake_exact.requests) == 4


# --- numbers read back ---

def test_numbers_are_compared_by_value_not_by_their_text(fake_exact):
    fake_exact.state[LINE] = {"price": 12.5}                                    # a JSON number, where the aid has "12.50"
    fake_exact.override = lambda method, path, index: (200, {"price": 12.7}) if index == 2 else None
    entry = writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)
    assert entry.status == "done" and entry.previous == "12.5" and entry.evidence == {}      # 12.5 is 12.50: nothing to note


def test_a_number_that_reads_back_different_is_an_error(fake_exact):
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.override = lambda method, path, index: (200, {"price": 12.71}) if index == 2 else None
    with pytest.raises(WriterError, match="verify GET"):
        writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)


@pytest.mark.parametrize("value", [["12.50"], {"price": "12.50"}, True])
def test_a_field_that_is_not_text_or_a_number_is_an_error(fake_exact, value):
    fake_exact.state[LINE] = {"price": value}
    with pytest.raises(WriterError, match=r"read GET /po/250101/v1/line/1: HTTP 200, .*'price' is not text or a number"):
        writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)
    assert fake_exact.requests == [("GET", LINE)]


# --- every way a call can go wrong is a WriterError that names the step, the path and the status ---

def test_a_404_on_the_read_is_a_writer_error_and_nothing_is_written(fake_exact):
    with pytest.raises(WriterError) as caught:
        writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)
    assert str(caught.value) == "read GET /po/250101/v1/line/1: HTTP 404"
    assert fake_exact.requests == [("GET", LINE)]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 429, 500, 502, 503])
def test_a_failed_write_is_a_writer_error(fake_exact, status):
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.fail_next, fake_exact.fail_method = status, "PUT"
    with pytest.raises(WriterError) as caught:
        writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)
    assert str(caught.value) == f"write PUT /po/250101/v1/line/1: HTTP {status}"
    assert fake_exact.requests == [("GET", LINE), ("PUT", LINE)]
    assert fake_exact.state[LINE] == {"price": "12.50"}


def test_a_failed_read_after_the_write_is_a_writer_error_that_says_verify(fake_exact):
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.override = lambda method, path, index: (503, {}) if index == 2 else None
    with pytest.raises(WriterError) as caught:
        writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)
    assert str(caught.value) == "verify GET /po/250101/v1/line/1: HTTP 503"


def test_a_value_that_reads_back_different_is_a_writer_error(fake_exact):
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.override = lambda method, path, index: (200, {"price": "12.60"}) if index == 2 else None      # the second read
    with pytest.raises(WriterError) as caught:
        writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)
    assert str(caught.value) == "verify GET /po/250101/v1/line/1: HTTP 200, read back '12.60' after writing '12.70'"
    assert fake_exact.requests == [("GET", LINE), ("PUT", LINE), ("GET", LINE)]


def test_a_write_that_the_server_ignored_is_caught_by_the_verification(fake_exact):
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.override = lambda method, path, index: (200, {"price": "12.50"}) if method == "PUT" else None
    with pytest.raises(WriterError, match="verify GET .*read back '12.50' after writing '12.70'"):
        writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)


@pytest.mark.parametrize("operation, path, field, unchanged", [
    pytest.param(status_op(), STATUS, "status", "Besteld", id="set_status"),
    pytest.param(op("po_line_date", "2026-11-16", "2026-11-30"), LINE, "delivery_date", "2026-11-16", id="po_line_date"),
])
def test_a_text_value_that_reads_back_different_is_a_writer_error(fake_exact, operation, path, field, unchanged):
    fake_exact.state[path] = {field: unchanged}
    fake_exact.override = lambda method, at, index: (200, {field: unchanged}) if method == "PUT" else None     # the write is ignored
    with pytest.raises(WriterError) as caught:
        writer(fake_exact).apply(operation, "c1", "R04", {}, NOW)
    assert str(caught.value) == f"verify GET {path}: HTTP 200, read back '{unchanged}' after writing '{operation.new_value}'"


def test_a_success_status_other_than_200_and_a_put_without_a_body_are_fine(fake_exact):
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.put_status = 204
    assert writer(fake_exact).apply(OP, "c1", "R04", {}, NOW).status == "done"


@pytest.mark.parametrize("step, index", [("read", 0), ("verify", 2)])
def test_a_body_that_is_not_json_is_a_writer_error(fake_exact, step, index):
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.override = lambda method, path, i: (200, b"<html>maintenance</html>") if i == index else None
    with pytest.raises(WriterError) as caught:
        writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)
    assert str(caught.value) == f"{step} GET /po/250101/v1/line/1: HTTP 200, the body is not JSON"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("body", [{"cost": "12.50"}, ["price"], "12.50", None], ids=["no field", "a list", "a string", "null"])
def test_a_body_without_the_field_is_a_writer_error(fake_exact, body):
    fake_exact.override = lambda method, path, index: (200, body)
    with pytest.raises(WriterError, match=r"read GET /po/250101/v1/line/1: HTTP 200, the body has no field 'price'"):
        writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)


@pytest.mark.parametrize("status", [300, 301, 302, 303, 307, 308])
def test_a_redirect_is_not_followed_and_is_a_writer_error(fake_exact, status):
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.state["/elsewhere"] = {"price": "12.70"}                         # a followed PUT would turn into this GET and seem to work
    fake_exact.fail_next, fake_exact.fail_method = status, "PUT"
    with pytest.raises(WriterError) as caught:
        writer(fake_exact).apply(OP, "c1", "R04", {}, NOW)
    assert str(caught.value) == f"write PUT /po/250101/v1/line/1: HTTP {status}"
    assert fake_exact.requests == [("GET", LINE), ("PUT", LINE)]


def test_no_answer_within_the_timeout_is_a_writer_error(fake_exact):
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.delay = 10
    started = time.monotonic()
    with pytest.raises(WriterError) as caught:
        writer(fake_exact, timeout=0.2).apply(OP, "c1", "R04", {}, NOW)
    assert str(caught.value) == "read GET /po/250101/v1/line/1: no answer within 0.2 s"
    assert time.monotonic() - started < 5


def test_no_answer_to_the_write_within_the_timeout_names_the_write(fake_exact):
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.delay, fake_exact.delay_method = 10, "PUT"
    with pytest.raises(WriterError, match=r"write PUT /po/250101/v1/line/1: no answer within 0\.2 s"):
        writer(fake_exact, timeout=0.2).apply(OP, "c1", "R04", {}, NOW)


def test_a_refused_connection_is_a_writer_error():
    with socket.socket() as probe:                                              # a port that was free a moment ago and has no listener
        probe.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{probe.getsockname()[1]}"
    with pytest.raises(WriterError) as caught:
        ExactApiWriter(url, ENDPOINTS, token=lambda: "t").apply(OP, "c1", "R04", {}, NOW)
    assert str(caught.value) == "read GET /po/250101/v1/line/1: ConnectionError"


def test_the_timeout_and_the_session_the_caller_gives_are_used_for_every_request(fake_exact, tmp_path):
    class Recording(requests.Session):
        def __init__(self):
            super().__init__()
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, kwargs["timeout"], kwargs["allow_redirects"]))
            return super().request(method, url, **kwargs)

    document = tmp_path / "conf.pdf"
    document.write_bytes(b"%PDF")
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.uploads[ATTACH] = [("conf.pdf", b"%PDF")]
    session = Recording()
    session.headers["X-Prepared"] = "yes"
    w = writer(fake_exact, session=session, timeout=7.5)
    w.apply(OP, "c1", "R04", {}, NOW)
    w.apply(file_op("attach_document", document), "c1", "R01", {}, NOW)
    w.apply(file_op("detach_document", document), "c1", "R01", {}, NOW)
    assert w.session is session
    assert session.calls == [("GET", 7.5, False), ("PUT", 7.5, False), ("GET", 7.5, False), ("POST", 7.5, False), ("DELETE", 7.5, False)]
    assert all(headers["X-Prepared"] == "yes" for headers in fake_exact.headers)
    assert writer(fake_exact).timeout == 20.0                                   # the default


# --- the token ---

def test_the_token_is_asked_for_on_every_request(fake_exact):
    fake_exact.state[LINE] = {"price": "12.50"}
    tokens = iter(["t1", "t2", "t3", "t4", "t5", "t6"])
    w = writer(fake_exact, token=lambda: next(tokens))
    w.apply(OP, "c1", "R04", {}, NOW)
    w.apply(op(old="12.70", new="12.80"), "c1", "R04", {}, NOW)
    assert [headers["Authorization"] for headers in fake_exact.headers] == [f"Bearer t{n}" for n in range(1, 7)]


def test_neither_the_token_nor_the_evidence_leaves_the_client(fake_exact, tmp_path):
    document = tmp_path / "conf.pdf"
    document.write_bytes(b"%PDF-1.4 made-up")
    fake_exact.state.update({LINE: {"price": "12.50"}, ARTICLE: {"price": "11.00"}, STATUS: {"status": "Besteld"}})
    fake_exact.uploads[ATTACH] = [("conf.pdf", b"%PDF-1.4 made-up")]
    evidence = {"flag_id": "F-evidence-7", "source": {"text": "Levering week 48 voor Zoe"}, "values": ["evidence-value-1", 42]}
    w = writer(fake_exact, token=lambda: SECRET)
    operations = (OP, article_op(old=None), status_op(), file_op("attach_document", document), file_op("detach_document", document))
    entries = [w.apply(operation, "c1", "R04", evidence, NOW) for operation in operations]
    assert len(fake_exact.requests) == 3 * 3 + 2
    for headers, query, body, (method, path) in zip(fake_exact.headers, fake_exact.queries, fake_exact.bodies, fake_exact.requests):
        assert headers["Authorization"] == f"Bearer {SECRET}"
        other_headers = [value for name, value in headers.items() if name != "Authorization"]
        sent = [method, path, json.dumps(query), body.decode("latin-1")] + other_headers
        assert not any(SECRET in part for part in sent)                         # the token is in the one header only
        assert not any(piece in part for part in sent + list(headers.values()) for piece in ("evidence", "Levering", "Zoe", "flag_id"))
    assert all(SECRET not in entry.model_dump_json() for entry in entries)      # nor does it go into an entry
    assert all(entry.evidence.get("flag_id") == "F-evidence-7" for entry in entries)                     # the entries keep the evidence


@pytest.mark.parametrize("failure", ["404", "503", "mismatch", "not json", "timeout", "refused", "refused header", "unencodable"])
def test_no_error_message_holds_the_token_and_no_error_keeps_the_error_it_came_from(fake_exact, failure):
    fake_exact.state[LINE] = {"price": "12.50"}
    url, token = fake_exact.url, SECRET
    if failure == "404":
        fake_exact.state.clear()
    elif failure == "503":
        fake_exact.fail_next = 503
    elif failure == "mismatch":
        fake_exact.override = lambda method, path, index: (200, {"price": "12.60"}) if index == 2 else None
    elif failure == "not json":
        fake_exact.override = lambda method, path, index: (200, b"not json")
    elif failure == "timeout":
        fake_exact.delay = 10
    elif failure == "refused header":
        token = f"{SECRET}\nsecond line"                                        # requests quotes this in its own error
    elif failure == "unencodable":
        token = f"{SECRET}\u20ac"                                               # http.client cannot send this header
    else:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{probe.getsockname()[1]}"
    w = ExactApiWriter(url, ENDPOINTS, token=lambda: token, timeout=0.2)
    with pytest.raises(WriterError) as caught:
        w.apply(OP, "c1", "R04", {}, NOW)
    assert SECRET not in str(caught.value) and SECRET not in "".join(traceback.format_exception(caught.value))
    # raised outside the except block of the error that came before it: no cause, no context, and so nothing to quote from
    assert caught.value.__cause__ is None and caught.value.__context__ is None and not caught.value.__suppress_context__


def test_a_check_that_fails_on_the_token_leaks_nothing_through_the_unverified_write(fake_exact):
    # the token of the third request, the check, is one that requests refuses and quotes in its own error
    fake_exact.state[LINE] = {"price": "12.50"}
    tokens = iter([SECRET, SECRET, f"{SECRET}\nsecond line"])
    with pytest.raises(UnverifiedWrite) as caught:
        writer(fake_exact, token=lambda: next(tokens)).apply(OP, "c1", "R04", {}, NOW)
    assert str(caught.value) == "verify GET /po/250101/v1/line/1: InvalidHeader" == caught.value.entry.error
    assert SECRET not in "".join(traceback.format_exception(caught.value)) and SECRET not in caught.value.entry.model_dump_json()
    assert caught.value.__cause__ is None and caught.value.__context__ is None and not caught.value.__suppress_context__
    assert fake_exact.requests == [("GET", LINE), ("PUT", LINE)] and fake_exact.state[LINE] == {"price": "12.70"}


def test_a_token_that_requests_refuses_does_not_leak_through_the_error(fake_exact):
    # requests quotes a header value it refuses (here: a line break) in its own error, so the writer must not pass that error on
    bad = f"{SECRET}\nsecond line"
    with pytest.raises(WriterError) as caught:
        writer(fake_exact, token=lambda: bad).apply(OP, "c1", "R04", {}, NOW)
    assert str(caught.value) == "read GET /po/250101/v1/line/1: InvalidHeader"
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert fake_exact.requests == []


def test_a_token_that_cannot_be_encoded_is_a_writer_error_and_the_retrying_writer_falls_back(fake_exact, tmp_path):
    w = writer(fake_exact, token=lambda: f"{SECRET}\u20ac")                      # a character outside latin-1
    with pytest.raises(WriterError) as caught:
        w.apply(OP, "c1", "R04", {}, NOW)
    assert str(caught.value) == "read GET /po/250101/v1/line/1: UnicodeEncodeError"
    entry = RetryingWriter(w, PreparedWriter(tmp_path), retries=2).apply(OP, "c1", "R04", {}, NOW)
    assert entry.status == "prepared" and entry.error == "read GET /po/250101/v1/line/1: UnicodeEncodeError"
    assert fake_exact.requests == []                                            # no request was made, the headers could not be sent


@pytest.mark.parametrize("token, cause", [
    (None, "the token is not text (NoneType)"),
    (123, "the token is not text (int)"),
    (b"bytes", "the token is not text (bytes)"),
    ("", "the token is empty"),
    ("   ", "the token is empty"),
    ("\n", "the token is empty"),
])
def test_a_token_that_is_empty_or_not_text_is_a_writer_error_and_nothing_is_sent(fake_exact, token, cause):
    with pytest.raises(WriterError) as caught:
        writer(fake_exact, token=lambda: token).apply(OP, "c1", "R04", {}, NOW)
    assert str(caught.value) == f"read GET /po/250101/v1/line/1: {cause}"
    assert fake_exact.requests == []


def test_a_token_function_that_fails_in_requests_is_a_writer_error_and_any_other_failure_goes_through(fake_exact):
    def refresh_fails():
        raise requests.ConnectionError(f"cannot refresh {SECRET}")             # a token fetched over HTTP, as a refresh would

    with pytest.raises(WriterError) as caught:
        writer(fake_exact, token=refresh_fails).apply(OP, "c1", "R04", {}, NOW)
    assert str(caught.value) == "read GET /po/250101/v1/line/1: ConnectionError"
    assert SECRET not in "".join(traceback.format_exception(caught.value)) and caught.value.__context__ is None

    def broken():
        raise KeyError("EXACT_TOKEN")                                           # a missing setting is a fault to see, not to retry

    with pytest.raises(KeyError):
        writer(fake_exact, token=broken).apply(OP, "c1", "R04", {}, NOW)
    assert fake_exact.requests == []


# --- attach and detach ---

def test_attach_posts_the_file_as_multipart(fake_exact, tmp_path):
    document = tmp_path / "conf_S01.pdf"
    content = b"%PDF-1.4\nmade-up bytes \x00\xff\x80 end"
    document.write_bytes(content)
    attach = file_op("attach_document", document)
    entry = writer(fake_exact).apply(attach, "c1", "R01", {"reason": "attach and confirm"}, NOW)
    assert fake_exact.requests == [("POST", ATTACH)]
    assert fake_exact.headers[0]["Authorization"] == "Bearer t"
    assert fake_exact.headers[0]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert multipart_parts(fake_exact.headers[0], fake_exact.bodies[0]) == [("file", "conf_S01.pdf", content, "application/pdf")]
    assert fake_exact.uploads == {ATTACH: [("conf_S01.pdf", content)]}
    assert (entry.status, entry.previous, entry.new, entry.op, entry.rule_id, entry.evidence) == \
        ("done", None, None, attach, "R01", {"reason": "attach and confirm"})


def test_attach_sends_a_file_of_an_unknown_type_as_binary(fake_exact, tmp_path):
    (tmp_path / "conf.zzzz").write_bytes(b"x")
    writer(fake_exact).apply(file_op("attach_document", tmp_path / "conf.zzzz"), "c1", "R01", {}, NOW)
    assert multipart_parts(fake_exact.headers[0], fake_exact.bodies[0]) == [("file", "conf.zzzz", b"x", "application/octet-stream")]


def test_attach_sends_the_name_of_the_file_without_its_folders(fake_exact, tmp_path):
    folder = tmp_path / "cases" / "250101_20261117T103000"
    folder.mkdir(parents=True)
    (folder / "conf.pdf").write_bytes(b"x")
    writer(fake_exact).apply(file_op("attach_document", folder / "conf.pdf"), "c1", "R01", {}, NOW)
    assert [name for _, name, _, _ in multipart_parts(fake_exact.headers[0], fake_exact.bodies[0])] == ["conf.pdf"]


def test_attach_then_undo_through_the_log_detaches_the_file(fake_exact, tmp_path):
    document = tmp_path / "conf.pdf"
    document.write_bytes(b"%PDF")
    w = writer(fake_exact)
    log = WriteLog(tmp_path / "case")
    (tmp_path / "case").mkdir()
    log.append(w.apply(file_op("attach_document", document), "c1", "R01", {}, NOW))
    assert fake_exact.uploads[ATTACH] == [("conf.pdf", b"%PDF")]
    undone = undo_case(tmp_path / "case", w, NOW + timedelta(hours=1))
    assert fake_exact.uploads[ATTACH] == []
    assert [(u.op.kind, u.status, u.undo_of) for u in undone] == [("detach_document", "done", log.entries()[0].entry_id)]
    assert fake_exact.requests == [("POST", ATTACH), ("DELETE", ATTACH)]


def test_detach_deletes_the_attachment_by_its_name(fake_exact, tmp_path):
    fake_exact.uploads[ATTACH] = [("conf & price (1).pdf", b"x"), ("other.pdf", b"y")]
    detach = file_op("detach_document", tmp_path / "gone" / "conf & price (1).pdf")      # the file need not be on disk to be detached
    entry = writer(fake_exact).apply(detach, "c1", "R01", {"undo_of": "e1"}, NOW)
    assert fake_exact.requests == [("DELETE", ATTACH)] and fake_exact.queries == [{"name": ["conf & price (1).pdf"]}]
    assert fake_exact.bodies == [b""] and fake_exact.headers[0]["Authorization"] == "Bearer t"
    assert fake_exact.uploads[ATTACH] == [("other.pdf", b"y")]
    assert (entry.status, entry.previous, entry.new, entry.op) == ("done", None, None, detach)


@pytest.mark.parametrize("document", [r"C:\cases\250101\conf.pdf", "cases/250101/conf.pdf", r"cases\250101/conf.pdf", "conf.pdf"])
def test_a_document_is_named_by_its_file_name_whichever_separator_its_path_has(fake_exact, document):
    fake_exact.uploads[ATTACH] = [("conf.pdf", b"x")]
    writer(fake_exact).apply(file_op("detach_document", document), "c1", "R01", {}, NOW)
    assert fake_exact.queries == [{"name": ["conf.pdf"]}] and fake_exact.uploads[ATTACH] == []


def test_detach_of_a_file_that_is_not_attached_is_a_writer_error(fake_exact, tmp_path):
    with pytest.raises(WriterError) as caught:
        writer(fake_exact).apply(file_op("detach_document", tmp_path / "conf.pdf"), "c1", "R01", {}, NOW)
    assert str(caught.value) == "detach DELETE /po/250101/attach: HTTP 404"


def test_a_failed_attach_is_a_writer_error(fake_exact, tmp_path):
    document = tmp_path / "conf.pdf"
    document.write_bytes(b"x")
    fake_exact.fail_next = 500
    with pytest.raises(WriterError) as caught:
        writer(fake_exact).apply(file_op("attach_document", document), "c1", "R01", {}, NOW)
    assert str(caught.value) == "attach POST /po/250101/attach: HTTP 500"
    assert fake_exact.uploads == {}


def test_attach_of_a_file_that_cannot_be_read_is_a_writer_error_and_sends_nothing(fake_exact, tmp_path):
    (tmp_path / "folder.pdf").mkdir()
    for document in (tmp_path / "gone.pdf", tmp_path / "folder.pdf"):
        with pytest.raises(WriterError, match=rf"attach POST /po/250101/attach: cannot read the document '{document.name}'") as caught:
            writer(fake_exact).apply(file_op("attach_document", document), "c1", "R01", {}, NOW)
        assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert fake_exact.requests == []


# --- what is refused before anything is sent ---

def test_evidence_that_is_not_json_fails_between_the_read_and_the_write(fake_exact):
    fake_exact.state[LINE] = {"price": "12.50"}
    with pytest.raises(ValueError, match="evidence is not JSON"):
        writer(fake_exact).apply(OP, "c1", "R04", {"object": object()}, NOW)
    assert fake_exact.requests == [("GET", LINE)] and fake_exact.state[LINE] == {"price": "12.50"}


def test_evidence_that_is_not_json_fails_before_a_file_is_attached_or_detached(fake_exact, tmp_path):
    document = tmp_path / "conf.pdf"
    document.write_bytes(b"x")
    for kind in ("attach_document", "detach_document"):
        with pytest.raises(ValueError, match="evidence is not JSON"):
            writer(fake_exact).apply(file_op(kind, document), "c1", "R01", {"object": object()}, NOW)
    assert fake_exact.requests == []


@pytest.mark.parametrize("operation, reason", [
    (WriteOp(kind="po_line_price", po_number="250101", version=1, line_id="1", old_value="12.50"), "no new value"),
    (op(new=""), "no new value"),
    (op(new="  "), "no new value"),
    (WriteOp(kind="po_line_price", po_number="250101", version=1, old_value="12.50", new_value="12.70"), "no line"),
    (WriteOp(kind="article_price", po_number="250101", version=1, old_value="11.00", new_value="12.70"), "no article"),
    (WriteOp(kind="po_line_date", po_number="250101", version=None, line_id="1", old_value="2026-11-16", new_value="2026-11-30"),
     "no version"),
    (WriteOp(kind="set_status", po_number="", version=1, old_value="Besteld", new_value="Bevestigd"), "no po"),
    (WriteOp(kind="attach_document", po_number="250101", version=1), "no document"),
    (WriteOp(kind="detach_document", po_number="250101", version=1, document=""), "no document"),
], ids=["new None", "new blank", "new spaces", "no line", "no article", "no version", "no PO number", "attach without document",
         "detach without document"])
def test_an_operation_the_writer_cannot_make_is_refused_before_anything_is_sent(fake_exact, operation, reason):
    with pytest.raises(ValueError, match=reason):               # not a WriterError: a retry or a prepared change would not help
        writer(fake_exact).apply(operation, "c1", "R04", {}, NOW)
    assert fake_exact.requests == []


@pytest.mark.parametrize("operation, name, value", [
    (WriteOp(kind="po_line_price", po_number="250101", version=1, line_id="..", old_value="12.50", new_value="12.70"), "line", ".."),
    (WriteOp(kind="po_line_price", po_number="250101", version=1, line_id=".", old_value="12.50", new_value="12.70"), "line", "."),
    (WriteOp(kind="article_price", po_number="250101", version=1, article_code="..", old_value="11.00", new_value="12.70"),
     "article", ".."),
    (WriteOp(kind="article_price", po_number="250101", version=1, article_code=".", old_value="11.00", new_value="12.70"),
     "article", "."),
    (WriteOp(kind="set_status", po_number="..", version=1, old_value="Besteld", new_value="Bevestigd"), "po", ".."),
    (WriteOp(kind="attach_document", po_number=".", version=1, document="conf.pdf"), "po", "."),
    (WriteOp(kind="detach_document", po_number="..", version=1, document="conf.pdf"), "po", ".."),
], ids=["line ..", "line .", "article ..", "article .", "PO ..", "attach PO .", "detach PO .."])
def test_a_value_that_a_path_reads_as_a_dot_segment_is_refused_before_anything_is_sent(fake_exact, operation, name, value):
    with pytest.raises(ValueError) as caught:
        writer(fake_exact).apply(operation, "c1", "R04", {}, NOW)
    assert str(caught.value) == \
        f"{operation.kind} on PO {operation.po_number} has the {name} '{value}', which a path reads as a dot segment"
    assert fake_exact.requests == []


@pytest.mark.parametrize("line", ["...", "1.0", ".a", "a..", "..%2F", "%2e%2e"])
def test_a_value_with_dots_in_it_is_no_dot_segment(fake_exact, line):
    path = f"/po/250101/v1/line/{line.replace('%', '%25')}"                      # quoting keeps the dots and encodes a percent sign
    fake_exact.state[path] = {"price": "12.50"}
    operation = WriteOp(kind="po_line_price", po_number="250101", version=1, line_id=line, old_value="12.50", new_value="12.70")
    assert writer(fake_exact).apply(operation, "c1", "R04", {}, NOW).status == "done"
    assert fake_exact.requests == [("GET", path), ("PUT", path), ("GET", path)]


def test_a_kind_without_a_configured_endpoint_is_a_writer_error_and_sends_nothing(fake_exact):
    w = ExactApiWriter(fake_exact.url, {"po_line": ENDPOINTS["po_line"]}, token=lambda: "t")
    with pytest.raises(WriterError, match=r"no endpoint 'status' .*set_status"):
        w.apply(status_op(), "c1", "R04", {}, NOW)
    assert fake_exact.requests == []
    fake_exact.state[LINE] = {"price": "12.50"}
    assert w.apply(OP, "c1", "R04", {}, NOW).status == "done"                   # the kinds that have an endpoint still work


# --- the address ---

def test_the_base_url_may_end_in_a_slash_and_have_a_prefix_and_a_path_may_lack_its_first_slash(fake_exact):
    fake_exact.state["/api/v2/po/250101/v1/line/1"] = {"price": "12.50"}
    w = ExactApiWriter(fake_exact.url + "/api/v2/", {"po_line": "po/{po}/v{version}/line/{line}"}, token=lambda: "t")
    w.apply(OP, "c1", "R04", {}, NOW)
    assert fake_exact.requests == [("GET", "/api/v2/po/250101/v1/line/1"), ("PUT", "/api/v2/po/250101/v1/line/1"),
                                   ("GET", "/api/v2/po/250101/v1/line/1")]


def test_a_value_in_a_path_is_quoted_as_one_segment(fake_exact):
    path = "/article/KM%2010%2F01%3Fa%23b%20%C3%98"
    fake_exact.state[path] = {"price": "11.00"}
    code = "KM 10/01?a#b \u00d8"
    operation = WriteOp(kind="article_price", po_number="250101", version=1, article_code=code, old_value="11.00", new_value="12.70")
    entry = writer(fake_exact).apply(operation, "c1", "R04", {}, NOW)
    assert fake_exact.requests == [("GET", path), ("PUT", path), ("GET", path)] and entry.op.article_code == code


def test_an_endpoint_may_use_any_of_the_four_values(fake_exact):
    fake_exact.state["/division/250101/1/v1/KM-1001"] = {"price": "11.00"}
    operation = WriteOp(kind="article_price", po_number="250101", version=1, line_id="1", article_code="KM-1001", old_value="11.00",
                        new_value="12.70")
    endpoints = {"article": "/division/{po}/{line}/v{version}/{article}"}
    ExactApiWriter(fake_exact.url, endpoints, token=lambda: "t").apply(operation, "c1", "R04", {}, NOW)
    assert fake_exact.requests[0] == ("GET", "/division/250101/1/v1/KM-1001")


# --- the arguments ---

@pytest.mark.parametrize("endpoints, reason", [
    ({"po-line": "/po/{po}/line/{line}"}, "unknown endpoint 'po-line'"),
    ({"po_line": "/po/{po}/line/{lin}"}, "endpoint 'po_line' .*'lin'"),
    ({"po_line": "/po/{po}/line/{line.real}"}, "endpoint 'po_line' .*'line.real'"),
    ({"po_line": "/po/{po}/line/{line:03}"}, "endpoint 'po_line' .*'line'"),
    ({"po_line": "/po/{po}/line/{line!r}"}, "endpoint 'po_line' .*'line'"),
    ({"po_line": "/po/{}/line/{line}"}, "endpoint 'po_line'"),
    ({"po_line": "/po/{po}/line/{line"}, "endpoint 'po_line'"),
    ({"po_line": "/po/{po}/line"}, "endpoint 'po_line' .*needs.*line"),
    ({"article": "/article"}, "endpoint 'article' .*needs.*article"),
    ({"attachment": "/attach"}, "endpoint 'attachment' .*needs.*po"),
    ({"status": "/status/{version}"}, "endpoint 'status' .*needs.*po"),
])
def test_endpoints_with_a_misspelt_key_or_a_template_that_would_write_to_the_wrong_place_are_refused(endpoints, reason):
    with pytest.raises(ValueError, match=reason):
        ExactApiWriter("http://127.0.0.1:1", endpoints, token=lambda: "t")


@pytest.mark.parametrize("base_url", ["", "example.com", "ftp://example.com", "http://", "https://", "https:///api/v1", "/api", None])
def test_a_base_url_that_is_not_an_http_address_is_refused(base_url):
    with pytest.raises(ValueError, match="base_url"):
        ExactApiWriter(base_url, ENDPOINTS, token=lambda: "t")


@pytest.mark.parametrize("base_url", [
    "http://example.com", "http://example.com:8080/api/v1", "http://10.0.0.5", "HTTP://EXAMPLE.COM", "http://localhost.example.com",
    "http://127.0.0.1.example.com", "http://127.0.0.1@example.com/api", "http://localhost:80@example.com", "http://example.com/localhost",
])
def test_a_plain_http_base_url_is_refused_unless_the_host_is_loopback(base_url):
    with pytest.raises(ValueError, match="base_url must be https unless the host is 127.0.0.1 or localhost"):
        ExactApiWriter(base_url, ENDPOINTS, token=lambda: SECRET)


@pytest.mark.parametrize("base_url", [
    "http://example.com\\@localhost/api", "https://example.com\\@localhost/api", "http://user\\pass@localhost", "http://localhost\\",
    "http://localhost/api\\v1", "https://example.com/api\\", "http://127.0.0.1\\@example.com", "HTTP://EXAMPLE.COM\\@LOCALHOST",
    "http://localhost:80\\@example.com", "example.com\\@localhost", "\\\\example.com\\share",
])
def test_a_base_url_with_a_backslash_is_refused_without_echoing_it(base_url):
    # urlsplit takes "localhost" as the host of http://example.com + backslash + @localhost/api and checks that, but requests
    # sends the token to example.com, in clear text: a backslash anywhere in the address is refused, whatever the scheme
    with pytest.raises(ValueError, match="base_url must not contain a backslash") as caught:
        ExactApiWriter(base_url, ENDPOINTS, token=lambda: SECRET)
    message = str(caught.value)
    assert all(part not in message for part in (base_url, "example.com", "localhost", "127.0.0.1", "user", "share"))


@pytest.mark.parametrize("base_url", [
    "https://example.com", "https://example.com/api/v1/", "HTTPS://EXAMPLE.COM", "https://127.0.0.1:8443", "https://localhost",
    "http://127.0.0.1", "http://127.0.0.1:8080/api", "http://localhost", "http://LOCALHOST:9/api/", "http://user@localhost:9",
    "https://example.com/api%5Cv1", "http://localhost/api%5C%40example.com",
])
def test_https_is_accepted_and_so_is_plain_http_to_the_machine_itself(base_url):
    assert ExactApiWriter(base_url, ENDPOINTS, token=lambda: SECRET).base_url == base_url.rstrip("/")     # nothing is sent


def test_a_token_that_is_not_a_function_is_refused_without_echoing_it():
    with pytest.raises(TypeError) as caught:
        ExactApiWriter("http://127.0.0.1:1", ENDPOINTS, token=SECRET)
    assert "token" in str(caught.value) and SECRET not in str(caught.value)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), "20", None])
def test_a_timeout_that_is_not_a_positive_number_is_refused(timeout):
    with pytest.raises(ValueError, match="timeout"):
        ExactApiWriter("http://127.0.0.1:1", ENDPOINTS, token=lambda: "t", timeout=timeout)


def test_the_writer_has_the_name_exact_api_and_keeps_its_own_copy_of_the_maps(fake_exact):
    endpoints, fields = dict(ENDPOINTS), {"po_line_price": "NetPrice"}
    w = ExactApiWriter(fake_exact.url, endpoints, token=lambda: "t", fields=fields)
    endpoints["po_line"] = "/changed/{po}/{line}"
    fields["po_line_price"] = "changed"
    fake_exact.state[LINE] = {"NetPrice": "12.50"}
    assert w.name == "exact_api" and ExactApiWriter.name == "exact_api"
    assert w.apply(OP, "c1", "R04", {}, NOW).status == "done"


# --- behind the interface of the writers ---

def test_retrying_writer_retries_a_transient_failure_and_logs_the_api_entry(fake_exact, tmp_path):
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.fail_next = 503                                                  # the first attempt fails at its read
    entry = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path), retries=2).apply(OP, "c1", "R04", {}, NOW)
    assert entry.status == "done" and entry.error is None
    assert fake_exact.requests == [("GET", LINE)] * 2 + [("PUT", LINE), ("GET", LINE)]
    assert not (tmp_path / "prepared_changes.csv").exists()


def test_retrying_writer_makes_a_prepared_change_when_the_api_stays_down(fake_exact, tmp_path):
    fake_exact.override = lambda method, path, index: (503, {})
    entry = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path), retries=3).apply(OP, "c1", "R04", {}, NOW)
    assert entry.status == "prepared" and entry.error == "read GET /po/250101/v1/line/1: HTTP 503"      # the failure is in the entry
    assert fake_exact.requests == [("GET", LINE)] * 3                           # three attempts and nothing written
    assert "12.70" in (tmp_path / "prepared_changes.csv").read_text()


def test_retrying_writer_does_not_retry_an_operation_the_api_writer_refuses(fake_exact, tmp_path):
    with pytest.raises(ValueError, match="no new value"):
        RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path)).apply(op(new=None), "c1", "R04", {}, NOW)
    assert fake_exact.requests == [] and not (tmp_path / "prepared_changes.csv").exists()


def test_the_entries_of_the_api_writer_go_into_the_write_log(fake_exact, tmp_path):
    fake_exact.state[LINE] = {"price": "12.50"}
    fake_exact.state[STATUS] = {"status": "Besteld"}
    w = writer(fake_exact)
    log = WriteLog(tmp_path)
    entries = [w.apply(OP, "c1", "R04", {"flag_id": "F1"}, NOW), w.apply(status_op(), "c1", "R01", {}, NOW)]
    for entry in entries:
        log.append(entry)
    assert log.entries() == entries


# --- through the pipeline ---

def test_the_pipeline_stops_at_an_article_price_the_exact_writer_could_not_check(fake_exact, tmp_path, madeup_out):
    """MU08 through RetryingWriter(the Exact writer, the prepared writer). The document says 12.50 for the line and the system
    holds 12.60; the line's price is written and checked. The article's price is written and no read after it succeeds: its entry
    is made, with the 12.40 read, and the case stops there for the buyer, the confirmation not attached and the status not set.
    Nothing is prepared. The undo of the case puts back what the system held: 12.40 and 12.60, not the document's 12.50."""
    from aid.pipeline import run_case
    from aid.sources.upload import open_upload_case
    from aid.tests.conftest import deps

    line, article, status = "/po/250108/v1/line/1", "/article/KM-0801", "/po/250108/status"
    fake_exact.state.update({line: {"price": "12.60"}, article: {"price": "12.40"}, status: {"status": "Besteld"}})
    written = []

    def article_reads_fail_after_its_write(method, path, index):
        if path == article and method == "PUT":
            written.append(index)
        return (503, {}) if path == article and method == "GET" and written else None

    fake_exact.override = article_reads_fail_after_its_write
    retrying = RetryingWriter(ExactApiWriter(fake_exact.url, ENDPOINTS, token=lambda: "t"), PreparedWriter(tmp_path / "cases" / "prepared"))
    d = deps(tmp_path, madeup_out, writer=retrying)
    c = open_upload_case(d.settings.cases_root, [madeup_out / "MU08_conf.pdf"], "250108", "confirmation", d.clock())
    r = run_case(c, d)
    assert fake_exact.requests == [("GET", line), ("PUT", line), ("GET", line), ("GET", article), ("PUT", article), ("GET", article), ("GET", article)]
    assert [(e.op.kind, e.status, e.previous, e.new, e.error) for e in r.write_entries] == [
        ("po_line_price", "done", "12.60", "12.70", None), ("article_price", "done", "12.40", "12.70", "verify GET /article/KM-0801: HTTP 503")]
    assert [(i.reason, i.entry_id) for i in r.review_items] == [
        ("write made, not verified: check in Exact", r.write_entries[1].entry_id),
        ("not applied: an earlier change waits for a look in Exact (rule R01, attach_document)", None),
        ("not applied: an earlier change waits for a look in Exact (rule R01, set_status)", None)]
    assert r.status == "review" and r.digest_line == "PO 250108 (S08): ter beoordeling, 2 schrijfacties"
    assert fake_exact.state == {line: {"price": "12.70"}, article: {"price": "12.70"}, status: {"status": "Besteld"}} and fake_exact.uploads == {}
    assert WriteLog(c.root).entries() == r.write_entries and not (tmp_path / "cases" / "prepared").exists()
    fake_exact.override = None
    undone = undo_case(c.root, retrying, NOW)
    assert [(u.op.kind, u.status, u.new) for u in undone] == [("article_price", "done", "12.40"), ("po_line_price", "done", "12.60")]
    assert fake_exact.state == {line: {"price": "12.60"}, article: {"price": "12.40"}, status: {"status": "Besteld"}}


def mu08(fake_exact, tmp_path, madeup_out, **options):
    """MU08 through RetryingWriter(the Exact writer, the prepared writer into cases/prepared), run; the document says 12.50 for
    the line, and the system holds 12.60 for the line and 12.40 for the article. Returns the case, its result and the route."""
    from aid.pipeline import run_case
    from aid.sources.upload import open_upload_case
    from aid.tests.conftest import deps

    fake_exact.state.update({MU_LINE: {"price": "12.60"}, MU_ARTICLE: {"price": "12.40"}, MU_STATUS: {"status": "Besteld"}})
    route = RetryingWriter(ExactApiWriter(fake_exact.url, ENDPOINTS, token=lambda: "t", **options), PreparedWriter(tmp_path / "cases" / "prepared"))
    d = deps(tmp_path, madeup_out, writer=route)
    c = open_upload_case(d.settings.cases_root, [madeup_out / "MU08_conf.pdf"], "250108", "confirmation", d.clock())
    return c, run_case(c, d), route


MU_LINE, MU_ARTICLE, MU_STATUS = "/po/250108/v1/line/1", "/article/KM-0801", "/po/250108/status"
NOT_SURE_ITEMS = ["write outcome not known: check in Exact",
                  "not applied: an earlier change waits for a look in Exact (rule R04, article_price, article KM-0801)",
                  "not applied: an earlier change waits for a look in Exact (rule R01, attach_document)",
                  "not applied: an earlier change waits for a look in Exact (rule R01, set_status)"]


def test_a_lost_answer_that_the_repeat_cannot_read_is_not_prepared_in_the_pipeline(fake_exact, tmp_path, madeup_out):
    """The review's proof: the system makes the write of the line's price and answers 502, and the read of the repeat answers 503.
    Before, the change was "prepared" with the document's 12.50 while the system held 12.70, and the undo wrote 12.50 into a
    line that had held 12.60. Now it is logged as made and not sure, with the 12.60 read; the case stops for the buyer's look;
    nothing is on the list; the undo through the route puts the 12.60 back."""
    def lost_then_no_read(method, path, index):
        if path == MU_LINE and method == "PUT":
            fake_exact.state[path].update(json.loads(fake_exact.bodies[index]))
            return 502, {}
        return (503, {}) if path == MU_LINE and method == "GET" and index > 0 else None

    fake_exact.override = lost_then_no_read
    c, r, route = mu08(fake_exact, tmp_path, madeup_out)
    [entry] = r.write_entries
    assert fake_exact.requests == [("GET", MU_LINE), ("PUT", MU_LINE), ("GET", MU_LINE)] and fake_exact.state[MU_LINE] == {"price": "12.70"}
    assert (entry.status, entry.previous, entry.op.old_value, entry.new, entry.error) == (
        "done", "12.60", "12.50", "12.70", "write PUT /po/250108/v1/line/1: HTTP 502")
    assert outcome_not_known(entry) and entry.evidence["read_before"] == "12.60"
    assert [i.reason for i in r.review_items] == NOT_SURE_ITEMS and r.review_items[0].entry_id == entry.entry_id
    assert r.status == "review" and not (tmp_path / "cases" / "prepared").exists()
    fake_exact.override = None
    [undone] = undo_case(c.root, route, NOW)
    assert (undone.status, undone.new) == ("done", "12.60") and fake_exact.state[MU_LINE] == {"price": "12.60"}


def test_writes_that_a_slow_system_makes_after_the_client_stopped_waiting_are_not_prepared(fake_exact, tmp_path, madeup_out):
    """The review's proof: the system takes longer over a write than the client waits, so each write times out and is made a
    little later; the read of the repeat still shows the old value. No answer settles nothing: the change is logged as made
    and not sure, with the 12.60 read, and nothing is prepared; the system then holds the new value."""
    fake_exact.delay, fake_exact.delay_method = 0.6, "PUT"
    c, r, route = mu08(fake_exact, tmp_path, madeup_out, timeout=0.2)
    [entry] = r.write_entries
    assert fake_exact.requests == [("GET", MU_LINE), ("PUT", MU_LINE)] * 2
    assert (entry.status, entry.previous, entry.new, entry.error) == ("done", "12.60", "12.70", "write PUT /po/250108/v1/line/1: no answer within 0.2 s")
    assert outcome_not_known(entry) and [i.reason for i in r.review_items] == NOT_SURE_ITEMS
    assert not (tmp_path / "cases" / "prepared").exists()
    deadline = time.monotonic() + 10                                             # the system finishes the writes it was sent
    while fake_exact.state[MU_LINE] != {"price": "12.70"} and time.monotonic() < deadline:
        time.sleep(0.05)
    assert fake_exact.state[MU_LINE] == {"price": "12.70"}


# --- what the buyer's decision does on the item of a write that asks for a look in Exact ---

def line_not_verified(fake_exact, tmp_path, madeup_out, through: str):
    """MU08 run through the route with the reads of the line failing after its write, so that the line's price is made and not
    verified (the system held 12.60, the document says 12.50). Returns the case, the result, the entry and the Deps of the page:
    one whose writer only prepares into cases/prepared ("the list"), or one whose writer is the route ("the route")."""
    from aid.tests.conftest import deps

    written = []

    def reads_of_the_line_fail_after_its_write(method, path, index):
        if path == MU_LINE and method == "PUT":
            written.append(index)
        return (503, {}) if path == MU_LINE and method == "GET" and written else None

    fake_exact.override = reads_of_the_line_fail_after_its_write
    c, r, route = mu08(fake_exact, tmp_path, madeup_out)
    fake_exact.override = None
    [entry] = r.write_entries
    assert (entry.status, entry.previous, entry.error) == ("done", "12.60", "verify GET /po/250108/v1/line/1: HTTP 503")
    assert [i.reason for i in r.review_items] == ["write made, not verified: check in Exact", *NOT_SURE_ITEMS[1:]]
    page = deps(tmp_path, madeup_out, writer=PreparedWriter(tmp_path / "cases" / "prepared") if through == "the list" else route)
    return c, r, entry, page


@pytest.mark.parametrize("through", ["the list", "the route"])
def test_bevestigen_on_a_write_made_and_not_verified_counts_it_as_made(fake_exact, tmp_path, madeup_out, through):
    """The buyer looked in Exact and the change stays. The close writes and prepares nothing for that operation (no second entry,
    no row on the list), and then does what waited behind it as after any settled item: the case closes, with the confirmation
    attached and the status set, prepared by a page that only prepares and made by a page whose writer is the route."""
    from aid.review.app import close_case, decide
    from aid.review.page import _undoable
    from aid.tests.conftest import read_csv
    from aid.tests.test_hand_list import listed_rows

    c, r, entry, page = line_not_verified(fake_exact, tmp_path, madeup_out, through)
    for item in r.review_items:
        decide(c, item.item_id, "confirm")
    before = len(fake_exact.requests)
    closed = close_case(c, page)
    status = "prepared" if through == "the list" else "done"
    assert closed.status == "closed" and closed.error is None
    assert [(e.rule_id, e.op.kind, e.status) for e in WriteLog(c.root).entries()] == [
        ("R04", "po_line_price", "done"), ("B01", "attach_document", status), ("B01", "set_status", status)]
    assert listed_rows(page) == [] and [e.entry_id for e in _undoable(c)][0] == entry.entry_id       # still there to undo
    if through == "the list":
        assert [row["kind"] for row in read_csv(tmp_path / "cases" / "prepared" / "prepared_changes.csv")] == ["attach_document", "set_status"]
        assert len(fake_exact.requests) == before
    else:
        assert fake_exact.requests[before:] == [("POST", "/po/250108/attach"), ("GET", MU_STATUS), ("PUT", MU_STATUS), ("GET", MU_STATUS)]
        assert not (tmp_path / "cases" / "prepared").exists()
    assert fake_exact.state == {MU_LINE: {"price": "12.70"}, MU_ARTICLE: {"price": "12.40"},
                                MU_STATUS: {"status": "Besteld" if through == "the list" else "Bevestigd"}}


@pytest.mark.parametrize("through", ["the list", "the route"])
def test_houden_on_a_write_made_and_not_verified_puts_the_value_read_back(fake_exact, tmp_path, madeup_out, through):
    """The buyer keeps the old value. The close undoes the entry as the tab's undo does: a page that only prepares puts the
    put-back on the list, naming the 12,60 the system held, and a page whose writer is the route writes the 12.60 back. What
    waited behind the change is not carried out: no article price, no attachment, no status."""
    from aid.review.app import close_case, decide
    from aid.review.page import _undoable
    from aid.tests.conftest import read_csv
    from aid.tests.test_hand_list import listed_rows

    c, r, entry, page = line_not_verified(fake_exact, tmp_path, madeup_out, through)
    for item in r.review_items:
        decide(c, item.item_id, "keep")
    before = len(fake_exact.requests)
    closed = close_case(c, page)
    assert closed.status == "closed" and closed.error is None and _undoable(c) == []
    [made, undo] = WriteLog(c.root).entries()
    assert (undo.rule_id, undo.undo_of, undo.op.kind, undo.op.old_value, undo.op.new_value) == ("undo", entry.entry_id, "po_line_price", "12.70", "12.60")
    assert [record["action"] for record in c.read_step("close")["records"]] == ["undo", "close"]
    if through == "the list":
        assert undo.status == "prepared" and len(fake_exact.requests) == before and fake_exact.state[MU_LINE] == {"price": "12.70"}
        assert listed_rows(page) == ["Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,60"]
        assert [row["rule_id"] for row in read_csv(tmp_path / "cases" / "prepared" / "prepared_changes.csv")] == ["undo"]
    else:
        assert undo.status == "done" and fake_exact.requests[before:] == [("GET", MU_LINE), ("PUT", MU_LINE), ("GET", MU_LINE)]
        assert fake_exact.bodies[before + 1] == b'{"price": "12.60"}' and fake_exact.state[MU_LINE] == {"price": "12.60"}
        assert not (tmp_path / "cases" / "prepared").exists()
    assert fake_exact.state[MU_ARTICLE] == {"price": "12.40"} and fake_exact.state[MU_STATUS] == {"status": "Besteld"} and fake_exact.uploads == {}


def test_after_houden_nothing_that_waited_behind_the_change_is_written_whatever_is_chosen_there(fake_exact, tmp_path, madeup_out):
    """"Houden" on the item of the line's price, and "Bevestigen" with the article box ticked on every item that waited behind
    it: the price is put back, and neither the line's price nor the article's is prepared again, nor the attachment or the
    status."""
    from aid.review.app import close_case, decide
    from aid.tests.test_hand_list import listed_rows

    c, r, entry, page = line_not_verified(fake_exact, tmp_path, madeup_out, "the list")
    decide(c, "RI1", "keep")
    for item in r.review_items[1:]:
        decide(c, item.item_id, "confirm", article_update=True)
    closed = close_case(c, page)
    assert closed.status == "closed" and [(e.rule_id, e.op.kind) for e in WriteLog(c.root).entries()] == [("R04", "po_line_price"), ("undo", "po_line_price")]
    assert listed_rows(page) == ["Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,60"]


def test_houden_through_a_route_that_is_down_stops_the_close(fake_exact, tmp_path, madeup_out):
    """The undo cannot be made: the close stops there, as at any write that does not go through, and the case stays in review."""
    from aid.review.app import close_case, decide

    c, r, entry, _ = line_not_verified(fake_exact, tmp_path, madeup_out, "the list")
    for item in r.review_items:
        decide(c, item.item_id, "keep")

    class Down:
        name = "down"

        def apply(self, *args):
            raise WriterError("503 service unavailable")

    from aid.tests.conftest import deps
    closed = close_case(c, deps(tmp_path, madeup_out, writer=Down()))
    assert closed.status == "review" and closed.error == "WriterError: 503 service unavailable (write: rule B01, po_line_price, line 1)"
    assert WriteLog(c.root).entries() == [entry]


# --- the tab's undo of a write made in the system, on a place the buyer ticked before ---

@pytest.mark.parametrize("checked", [True, False], ids=["verified", "not verified"])
def test_the_put_back_of_a_write_made_in_the_system_is_asked_whatever_was_ticked_before_it(fake_exact, tmp_path, madeup_out, checked):
    """The review's proof. The buyer entered 12,50 to 12,60 for an earlier confirmation and ticked that row; then MU08 went through
    the route, which read the 12.60 and wrote 12.70. The tab's undo of that write, through a page that only prepares, used to
    answer "Deze wijziging is al teruggedraaid." and list nothing, because the marks said that Exact holds 12,60. The put-back is
    judged against the write it puts back: it is on the list, and ticking it ends it."""
    from aid.execution.by_hand import changes, mark
    from aid.review.i18n import STRINGS
    from aid.review.page import _undo_choices, _undoable, undo_text
    from aid.tests.conftest import deps
    from aid.tests.test_hand_list import listed_rows

    prepared = tmp_path / "cases" / "prepared"
    clock = datetime(2026, 11, 12, 9, 0)
    earlier = PreparedWriter(prepared).apply(WriteOp(kind="po_line_price", po_number="250108", version=1, line_id="1", old_value="12.50", new_value="12.60"),
                                             "250108_20261111T090000", "B01", {}, clock - timedelta(days=1))
    # Ticked after the order was filed, whatever day the suite runs: a tick from before the print the store holds would be ended
    # by that print, which shows 12,50 (Task 43: a newer print ends a tick that it contradicts).
    assert mark(prepared, [earlier.entry_id], "entered", datetime.now() + timedelta(minutes=1)) == 1
    if checked:
        c, r, _ = mu08(fake_exact, tmp_path, madeup_out)
    else:
        c, r, _, _ = line_not_verified(fake_exact, tmp_path, madeup_out, "the list")
    page = deps(tmp_path, madeup_out, writer=PreparedWriter(prepared))
    entry = next(e for e in _undoable(c) if e.op.kind == "po_line_price")
    # Since Task 43 the case is compared with the ticked 12,60, laid over the printed 12,50, so the change starts from it; the
    # route read the same 12.60 in the system.
    assert (entry.previous, entry.op.old_value, entry.new) == ("12.60", "12.60", "12.70") and listed_rows(page) == []
    row = "Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,60"
    assert undo_text(c, entry.entry_id, page, "nl") == STRINGS["nl"]["hand_put_back_listed"].format(row=row)
    assert listed_rows(page) == [row]
    [first, put] = changes(prepared)[0]
    assert (first.state, put.state, put.old_value, put.new_value, put.puts_back) == ("entered", "open", "12.70", "12.60", entry.entry_id)
    assert entry.entry_id not in [value for _, value in _undo_choices(c, STRINGS["nl"])]            # undone once in the log
    assert mark(prepared, put.ids, "entered", clock + timedelta(hours=1)) == 1 and listed_rows(page) == []     # a tick after it ends it


# --- the undo of a change whose field was empty before the write ---

@pytest.mark.parametrize("held", [None, ""], ids=["null", "empty text"])
def test_the_undo_of_a_change_to_an_empty_field_writes_nothing_and_the_page_asks_to_empty_it(fake_exact, tmp_path, madeup_out, held):
    """The line's price was empty in the system when MU08's 12.70 was written. No value was held, so none is written back: not
    the document's 12.50, and not a blank. Through the route the undo is refused with a WriterError that says the field was
    empty, and nothing is sent; through the page, whose writer prepares, the put-back goes on the list and asks the buyer to
    empty the field."""
    from aid.review.i18n import STRINGS
    from aid.review.page import _undoable, undo_text
    from aid.tests.conftest import deps
    from aid.tests.test_hand_list import listed_rows

    c, r, route = mu08(fake_exact, tmp_path, madeup_out)
    assert r.status == "closed"
    fake_exact.state[MU_LINE] = {"price": held}                      # as if the field had been empty: write the line once more
    case_root = tmp_path / "again"
    case_root.mkdir()
    op_ = WriteOp(kind="po_line_price", po_number="250108", version=1, line_id="1", old_value="12.50", new_value="12.70")
    entry = route.apply(op_, c.case_id, "R04", {}, NOW)
    assert (entry.status, entry.previous, entry.evidence, entry.error) == ("done", held, {"read_before": held}, None)
    WriteLog(case_root).append(entry)
    before = len(fake_exact.requests)
    with pytest.raises(WriterError) as caught:
        undo_case(case_root, route, NOW + timedelta(hours=1))
    assert str(caught.value) == "cannot undo po_line_price on PO 250108, line 1: the field was empty before the write, so there is no value to write back"
    assert len(fake_exact.requests) == before and fake_exact.state[MU_LINE] == {"price": "12.70"} and WriteLog(case_root).entries() == [entry]
    assert not (tmp_path / "cases" / "prepared").exists()
    # the same entry in the case's own log, undone from the review tab
    WriteLog(c.root).append(entry)
    page = deps(tmp_path, madeup_out, writer=PreparedWriter(tmp_path / "cases" / "prepared"))
    assert entry.entry_id in [e.entry_id for e in _undoable(c)]
    row_nl = "Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → leeg"
    # The note ends there: the sentence of another old value (Task 43, W10) is for a put-back that names the order's value. This
    # one names none, so nothing is added although the order as the aid last saw it shows 12,50 for the line.
    assert undo_text(c, entry.entry_id, page, "nl") == f"Nog niet teruggedraaid in Exact. Op de lijst staat nu: {row_nl}. Maak het veld in Exact leeg en vink de regel af."
    assert listed_rows(page) == [row_nl]
    assert listed_rows(page, "en") == ["Purchase order 250108 (S08), Put back: line 1 (KM-0801): price 12.70 → empty"]
    assert STRINGS["en"]["hand_put_back_listed_empty"] == "Not put back in Exact yet. The list now holds: {row}. Empty the field in Exact and tick the row."
    assert len(fake_exact.requests) == before


# --- "Houden" where the page cannot put the old value on the list ---

@pytest.mark.parametrize("kind", ["set_status", "attach_document"])
def test_houden_on_a_look_of_a_status_or_an_attachment_tells_the_buyer_to_put_it_back_himself(fake_exact, tmp_path, madeup_out, kind):
    """The check's proof: MU01, a clean confirmation, through the route; the answer to the status (or to the attachment) is
    lost and nothing settles it. "Houden" through a page that only prepares: the put-back of a status or a file is a row of
    no list, so the close says, with the sentence of the tab's own undo, that the buyer puts it back in Exact himself, and the
    case stays in review; the next close, with nothing left to say, closes it."""
    from aid.pipeline import run_case
    from aid.review.app import decide
    from aid.review.i18n import STRINGS
    from aid.review.page import _undoable, close_text
    from aid.sources.upload import open_upload_case
    from aid.tests.conftest import deps
    from aid.tests.test_hand_list import listed_rows

    status, attach = "/po/250101/status", "/po/250101/attach"
    fake_exact.state[status] = {"status": "Besteld"}

    def answer_lost(method, path, index):
        if kind == "set_status" and path == status:
            if method == "PUT":
                fake_exact.state[path].update(json.loads(fake_exact.bodies[index]))
                return 502, {}
            return (503, {}) if any(m == "PUT" for m, _ in fake_exact.requests[:index]) else None
        if kind == "attach_document" and method == "POST":
            fake_exact.uploads.setdefault(path, []).append(("MU01_conf.pdf", b"x"))
            return 502, {}
        return None

    fake_exact.override = answer_lost
    prepared = tmp_path / "cases" / "prepared"
    route = RetryingWriter(ExactApiWriter(fake_exact.url, ENDPOINTS, token=lambda: "t"), PreparedWriter(prepared))
    d = deps(tmp_path, madeup_out, writer=route)
    c = open_upload_case(d.settings.cases_root, [madeup_out / "MU01_conf.pdf"], "250101", "confirmation", d.clock())
    r = run_case(c, d)
    fake_exact.override = None
    look = r.write_entries[-1]
    assert (look.op.kind, look.status) == (kind, "done") and outcome_not_known(look) and r.review_items[0].entry_id == look.entry_id
    page = d.model_copy(update={"writer": PreparedWriter(prepared)})
    for item in r.review_items:
        decide(c, item.item_id, "keep")
    text, still_open = close_text(c, page, "nl")
    assert still_open and text == f"De zaak is nu: ter beoordeling. {STRINGS['nl']['hand_put_back_cannot']}"
    [undo] = [e for e in WriteLog(c.root).entries() if e.undo_of == look.entry_id]
    assert (undo.status, undo.op.kind) == ("prepared", "set_status" if kind == "set_status" else "detach_document")
    assert listed_rows(page) == [] and _undoable(c) == [e for e in _undoable(c) if e.entry_id != look.entry_id]
    assert c.read_step("close")["records"][-1]["put_back_by_hand"] == [kind]
    assert close_text(c, page, "nl") == ("De zaak is nu: afgesloten.", False)           # said once; nothing more is written
    assert len([e for e in WriteLog(c.root).entries() if e.undo_of is not None]) == 1
    assert fake_exact.state[status] == {"status": "Bevestigd" if kind == "set_status" else "Besteld"}


def test_houden_whose_undo_comes_back_failed_stops_the_close(fake_exact, tmp_path, madeup_out):
    """The writer of the page answers the undo with an entry that says it changed nothing (status "failed"): the close stops
    there, as at any write that failed, and the case stays in review with the write still to undo."""
    from aid.review.app import close_case, decide
    from aid.review.page import _undoable
    from aid.tests.conftest import deps

    c, r, entry, _ = line_not_verified(fake_exact, tmp_path, madeup_out, "the list")
    for item in r.review_items:
        decide(c, item.item_id, "keep")

    class Locked:
        name = "locked"

        def apply(self, op, case_id, rule_id, evidence, now):
            return WriteLogEntry(entry_id="failed-undo", case_id=case_id, op=op, status="failed", previous=None, new=None, rule_id=rule_id,
                                 evidence=evidence, timestamp=now.isoformat(), error="the line is locked")

    closed = close_case(c, deps(tmp_path, madeup_out, writer=Locked()))
    assert closed.status == "review" and closed.error == "write failed: the line is locked (write: rule B01, po_line_price, line 1)"
    assert [(e.entry_id, e.status) for e in WriteLog(c.root).entries()][-1] == ("failed-undo", "failed")
    assert [e.entry_id for e in _undoable(c)] == [entry.entry_id]                      # a failed undo undid nothing
    assert c.read_step("close")["records"][-1]["carried_out"] == {}
