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
from aid.execution.writer import PreparedWriter, RetryingWriter, WriteLog, WriteLogEntry, WriterError, inverse, undo_case


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
    assert fake_exact.requests == [("GET", ARTICLE), ("PUT", ARTICLE), ("GET", ARTICLE)] * 2
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
    assert fake_exact.requests == [("GET", LINE), ("PUT", LINE), ("GET", LINE)] * 2
    assert (entry.status, entry.error, entry.previous, entry.evidence) == ("done", None, "12.50", {"read_before": "12.70"})
    assert entry.op == OP


def test_the_first_read_is_kept_over_several_attempts_that_wrote_and_could_not_check(fake_exact, tmp_path):
    fake_exact.state[ARTICLE] = {"price": "11.00"}
    unchecked_first_attempt(fake_exact, 2, 5)                                   # two attempts write 12.70 and cannot read it back
    entry = RetryingWriter(writer(fake_exact), PreparedWriter(tmp_path), retries=3).apply(article_op(old=None), "c1", "R04", {}, NOW)
    assert len(fake_exact.requests) == 9
    assert (entry.status, entry.previous, entry.op.old_value, entry.evidence) == ("done", "11.00", "11.00", {"read_before": "12.70"})


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
