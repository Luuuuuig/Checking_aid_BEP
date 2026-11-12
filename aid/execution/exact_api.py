"""A writer into a system behind a web API, built by no command: kept as the place where a write route into Exact would plug in.

What this module is. The aid writes nothing in Exact. Every command gives its Deps the PreparedWriter, whose changes the buyer
enters from the tab "In Exact invoeren", and a settings file in which exact_base_url is set is refused (aid/settings.py). This
writer was written against a stand-in of the tests, a made-up server on 127.0.0.1, and has never been run against Exact, whose
web service has another shape than that stand-in. It is kept, with its tests, as the place where a writer for a real door would
plug in: such a writer implements the Writer protocol (aid/execution/writer.py), and RetryingWriter takes it as its primary
writer in front of the PreparedWriter as its fallback, so that a change the door cannot make is tried again and then prepared
for the buyer. The address, the paths and the names of the fields are arguments and not code (the settings keep the keys
exact_base_url, exact_endpoints and exact_fields for them), and one write operation becomes calls to that API and the entry that
logs it, as described below.

What a builder of a route must still settle. The checks of this module found these, and they are not put right, since nothing
builds the route:
- A doubt that outlives its operation. The writer remembers a write whose outcome is not known for its target as long as the
  writer lives, and a writer of the runner lives across cases. A later case's call for the same change is then logged as made
  and not sure, with the first case's error and its previous value, although nothing was sent in that case. A doubt must end
  with the operation that left it, or a later call that sent nothing must not be logged as made.
- The article price that waits behind a look. When the price of an order line is made and not verified, the article price of
  the same rule is not applied and is a review item of its own. "Bevestigen" on that item writes the article price only with
  the article box ticked, which the page ticks for no such item, and nothing on the page names the article.
- A close's own write that asks for a look. On a review page whose writer is a route, a write that the close makes itself and
  that ends not verified or not sure stops the close, and the next close counts it as made: no review item was decided on it,
  and above the authorization limit no request goes to the approver.
- A read that lags behind the write. When the system made the change and its read still shows the value from before, the
  writer takes the write as ignored: the change is tried again and then prepared, while the system holds it.

The calls of one operation:
- po_line_price, po_line_date, po_line_quantity, article_price and set_status read the field at the address of the operation (GET),
  write the new value there (PUT, with that field only) and read the field again (GET) to check it. The address is
  endpoints["po_line"] ({po}, {version}, {line}), endpoints["article"] ({article}) or endpoints["status"] ({po}); the field comes
  from `fields` (DEFAULT_FIELDS). The value read first is the entry's `previous`. An operation without an old value (the routing
  does not know an article's price) is logged with the value read as its old value, so that the undo restores it; a field that
  is empty or blank then is refused, as that change could not be undone. An operation with an old value that differs from the
  value read is made all the same, and the entry's evidence notes the value read under "read_before"; so does an operation
  without an old value when the value read already is its new value.
- attach_document posts the file that op.document names to endpoints["attachment"] ({po}) as multipart (the field "file", the
  name of the file without its folders) and detach_document deletes it there (DELETE, the name in the query parameter "name").
  Neither reads a value first, so their entries have no previous and no new value.

Any failure of a call raises WriterError, which RetryingWriter answers: a status that is not 2xx (a redirect is not followed, as
a PUT turned into a GET would write nothing), a value that reads back different from the one written, a connection error, a
timeout, a header that cannot be encoded, a token that is empty or not text, a body that is not JSON or has no usable field, a
file that cannot be read. The message names the step (read, write, verify, attach, detach), the method, the path and the status
where the call got one. It holds nothing else of the call, so that it cannot hold the token: requests puts a header value it
refuses into its own error, so the writer keeps only the name of that error's class and raises its own error outside the
except block, which leaves it no context to quote from. An operation that cannot be made for a reason of its own (no new
value, no line, no document, a value that a path would read as a dot segment, "." or "..") raises ValueError before anything is
sent, as does evidence that is not JSON, which is found after the read and before the write (before the call, for a file). A
kind with no endpoint raises WriterError, so that it is prepared. A base URL must be https, or http for 127.0.0.1 and localhost
only (the tests), so that the token never travels in clear text, and it must hold no backslash: requests reads the host of such
an address differently from urlsplit, which the check of the host uses.

A write that the system accepted (a 2xx answer) and whose check then fails is not like the other failures, because the system may
hold the change. The read after the write decides. When it shows the value that was read before the write, the system ignored
the write: a plain WriterError, so that the change is tried again and then prepared. When it fails, or shows any other value
than the new one, the error is an UnverifiedWrite (aid/execution/writer.py), a WriterError that carries the entry of the
attempt, status "done", with the failed check as its `error`: RetryingWriter repeats the operation and, when no attempt verifies
the change, logs it as made and never prepares it.

Nor is a write whose outcome is not known. The outcome of a write is known when nothing was sent (no usable token, a header that
cannot be sent, a connection that was never made: refused, a connect time-out, a TLS or a proxy failure), when the system refused
it (a 4xx answer: not made) and when it accepted it (2xx: made, and the check decides). It is not known when no answer came over
a connection that stood (a timeout, a lost connection) or the answer is neither 2xx nor 4xx: a gateway's 502 can follow a write
that was made. The error is then an UnknownWrite, which carries the entry as an UnverifiedWrite does, with what happened
to the write as its `error`, and the writer remembers the doubt for the target. A later attempt at the operation settles it by
its read: the new value means made and checked (below); the value from before means not made, but only after a write that got
an answer, since one that got no answer can still land. Until it is settled, every attempt that fails raises UnknownWrite again
(one whose read fails with an entry of its own call, built from what is remembered), and a plain WriterError says that the
write was not made; RetryingWriter logs a change whose last attempt still raised it as made and not sure, and never prepares it.

The token is asked for on every request and sent as "Authorization: Bearer <token>" and nowhere else. The evidence is only
put into the entry and is never sent. The value operations are safe to repeat, as RetryingWriter repeats a whole operation: a
PUT of a value that is there already verifies clean. A repeat after a write that was sent and not checked finds the new value
there, so the writer remembers for each target (kind, PO, version, line or article) the value it sent and the value it read
before such a write, until a write to that target has been checked. A repeat that reads the value it remembers sending takes
that read as the check of that write and writes nothing again: it logs the remembered value as its `previous` (and as the old
value of an operation that had none) and notes the value it found under "read_before". So a write whose answer was lost, or
whose check failed, is found by a repeat whose read works; when no read does, the change ends as not sure, or as made and not
verified (above). The memory is the writer's own: a new writer starts without it.
attach_document is one POST, which a repeat would make a second time, and the API has no read of the attachments that could
show that the file is not there: an attachment whose outcome is not known is not sent again by this writer, and every later
attempt raises UnknownWrite, also one whose document can no longer be read, so that it is logged as not sure and not attached
twice. A detachment is a DELETE, which can be
sent again without harm: it is, and only a success settles it. A success of either forgets what is remembered of both.
"""
import mimetypes
import string
import uuid
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PureWindowsPath
from typing import NamedTuple
from urllib.parse import quote, urlsplit

import requests
import urllib3

from aid.execution.ops import WriteOp
from aid.execution.writer import UnknownWrite, UnverifiedWrite, WriteLogEntry, WriterError, _json_line

# The field that each value operation writes, until the answer to G9 gives the real names.
DEFAULT_FIELDS = {"po_line_price": "price", "po_line_date": "delivery_date", "po_line_quantity": "quantity", "article_price": "price",
                  "set_status": "status"}
# The endpoints, each with the values its path must hold: without them the operations on different lines, articles or POs would
# go to one address.
ENDPOINT_NEEDS = {"po_line": ("po", "line"), "article": ("article",), "attachment": ("po",), "status": ("po",)}
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")      # the hosts a plain http base URL may name
_PLACEHOLDERS = ("po", "version", "line", "article")
_NOT_JSON = object()                             # what _read holds for a body that is not JSON
_ENDPOINT_OF_KIND = {"po_line_price": "po_line", "po_line_date": "po_line", "po_line_quantity": "po_line", "article_price": "article",
                     "set_status": "status", "attach_document": "attachment", "detach_document": "attachment"}


class _NoClearAnswer(WriterError):
    """A request that was sent and got no answer (`silent`), or an answer that is neither a success nor a refusal: after a write,
    the system may or may not have made the change."""

    def __init__(self, message: str, silent: bool):
        super().__init__(message)
        self.silent = silent


class _Sent(NamedTuple):
    """A write that was sent to a target and not checked: the value sent and the value read before it (None for a file), and,
    while the outcome of a write to the target is not known, what happened to that write (`doubt`) and whether it got no answer
    at all (`silent`)."""
    value: str | None
    before: str | None
    doubt: str | None = None
    silent: bool = False


class ExactApiWriter:
    """Applies each operation through the Exact API (see the module docstring).

    `base_url` is the address of the API: https, or http for LOOPBACK_HOSTS only, with no backslash in it. `endpoints` maps endpoint names
    (ENDPOINT_NEEDS) to path templates that start under the base URL and name the values of the operation in braces: {po},
    {version}, {line} and {article}. A misspelt name, a template that leaves out a value its endpoint needs and a name that is
    not an endpoint are refused when the writer is made. An endpoint that is left out makes the operations of its kinds raise
    WriterError. `token` is a function that returns the token to use now. `session` is a prepared requests.Session (proxies, a CA
    bundle); it must not set an authorization of its own, and the writer does not close it. `timeout` is the number of seconds
    each request may take to connect and to answer. `fields` replaces the field names of some operation kinds (DEFAULT_FIELDS
    holds the others)."""
    name = "exact_api"

    def __init__(self, base_url: str, endpoints: dict[str, str], token: Callable[[], str], session: requests.Session | None = None,
                 timeout: float = 20.0, fields: dict[str, str] | None = None):
        if isinstance(base_url, str) and "\\" in base_url:      # urlsplit and requests read the host of such an address differently
            raise ValueError("base_url must not contain a backslash: requests would read its host differently from the check of the host")
        address = urlsplit(base_url) if isinstance(base_url, str) else None
        if address is None or address.scheme not in ("http", "https") or not address.netloc:
            raise ValueError("base_url must be an http or https address")
        if address.scheme == "http" and address.hostname not in LOOPBACK_HOSTS:
            raise ValueError(f"base_url must be https unless the host is {' or '.join(LOOPBACK_HOSTS)}, "
                             "so that the token never travels in clear text")
        if not callable(token):
            raise TypeError("token must be a function that returns the token to use now")
        if not isinstance(timeout, (int, float)) or not timeout > 0:
            raise ValueError(f"timeout is a number of seconds above 0, not {timeout!r}")
        unknown = sorted(set(endpoints) - set(ENDPOINT_NEEDS))
        if unknown:
            raise ValueError(f"unknown endpoint {', '.join(map(repr, unknown))}; the endpoints are {', '.join(ENDPOINT_NEEDS)}")
        for key, template in endpoints.items():
            named = _placeholders(key, template)
            for need in ENDPOINT_NEEDS[key]:
                if need not in named:
                    raise ValueError(f"endpoint {key!r} needs {{{need}}} in its path, or its operations would go to one address")
        fields = dict(fields or {})
        for kind, field in fields.items():
            if kind not in DEFAULT_FIELDS:
                raise ValueError(f"fields names the field of a value operation ({', '.join(DEFAULT_FIELDS)}), not of {kind!r}")
            if not isinstance(field, str) or not field:
                raise ValueError(f"the field of {kind} must be a name, not {field!r}")
        self.base_url = base_url.rstrip("/")
        self.endpoints = dict(endpoints)
        self.token = token
        self.session = session if session is not None else requests.Session()
        self.timeout = timeout
        self.fields = DEFAULT_FIELDS | fields
        # target -> the write that was sent to it and not checked (_Sent), until a write to the target is checked
        # The key is the address of the op, not the resource: the memory serves the retry of one op after an unchecked write.
        self._unchecked: dict[tuple, _Sent] = {}

    def apply(self, op: WriteOp, case_id: str, rule_id: str, evidence: dict, now: datetime) -> WriteLogEntry:
        """Make the change and return its entry: status "done", `previous` the value read before a value operation (the one read
        before an earlier attempt that wrote and did not check, where there was one; None for attach and detach), `new` the new
        value, `evidence` as given plus the note "read_before" where the module docstring says so, `entry_id` a UUID4 in hex and
        `timestamp` now.isoformat(). The entry is built and checked as JSON after the read and before the write. Raises
        WriterError for a failed call, UnverifiedWrite with the entry for a write the system accepted whose check failed,
        UnknownWrite with the entry while the outcome of a write is not known, and ValueError for an operation or evidence that
        cannot be used (the module docstring has all four)."""
        path = self._path(op)
        if op.kind in self.fields:
            return self._change_value(op, path, case_id, rule_id, evidence, now)
        return self._change_file(op, path, case_id, rule_id, evidence, now)

    def _path(self, op: WriteOp) -> str:
        """The path of the operation's resource: the endpoint of its kind with the values filled in, each quoted as one segment."""
        key = _ENDPOINT_OF_KIND[op.kind]
        template = self.endpoints.get(key)
        if template is None:
            raise WriterError(f"no endpoint {key!r} is configured, so {op.kind} cannot be made through the API")
        values = {"po": op.po_number, "version": op.version, "line": op.line_id, "article": op.article_code}
        named = _placeholders(key, template)
        for name in _PLACEHOLDERS:
            if name not in named:
                continue
            if values[name] in (None, ""):
                raise ValueError(f"{op.kind} on PO {op.po_number} has no {name}, which the endpoint {key!r} needs")
            if str(values[name]) in (".", ".."):       # quoting leaves dots alone, and the request would resolve them
                raise ValueError(f"{op.kind} on PO {op.po_number} has the {name} {values[name]!r}, which a path reads as a dot segment")
        quoted = {name: quote(str(values[name]), safe="") for name in named}
        return "/" + template.format(**quoted).lstrip("/")

    def _change_value(self, op: WriteOp, path: str, case_id: str, rule_id: str, evidence: dict, now: datetime) -> WriteLogEntry:
        if op.new_value is None or not op.new_value.strip():
            raise ValueError(f"{op.kind} on PO {op.po_number} has no new value to write")
        field = self.fields[op.kind]
        target = (op.kind, op.po_number, op.version, op.line_id, op.article_code)
        sent = self._unchecked.get(target)
        if sent is not None and not _same(sent.value, op.new_value):
            sent = None                                # the write remembered was another operation's
        doubt = sent if sent is not None and sent.doubt is not None else None      # a write of this operation whose outcome is not known
        failed = None
        try:
            read, where = self._read("read", path, field)
        except WriterError as exc:
            if doubt is None:
                raise
            failed = str(exc)
        if failed is not None:                         # nothing settled the write in doubt; raised here, so that the error has no context
            raise self._failure(failed, self._remembered(op, case_id, rule_id, evidence, now, doubt), doubt)
        found_sent = sent is not None and _same(read, op.new_value)
        previous = sent.before if found_sent else read     # an earlier attempt sent the value found now: the one before it is the previous
        if op.old_value is None:                       # the routing does not know an article's price: the undo restores what was read
            if previous is None or not previous.strip():
                refused = f"{where}, {field!r} is empty and the operation has no old value, so the change could not be undone"
                if doubt is None:
                    raise WriterError(refused)
                raise self._failure(refused, self._remembered(op, case_id, rule_id, evidence, now, doubt), doubt)     # still in doubt
            op = op.model_copy(update={"old_value": previous})
            if _same(read, op.new_value):              # nothing to change: the value was there already, or an earlier attempt made it
                evidence = evidence | {"read_before": read}
        elif not _same(read, op.old_value):
            evidence = evidence | {"read_before": read}
        entry = self._entry(op, case_id, rule_id, evidence, now, previous)
        if found_sent:                                 # this read is the check of that write: nothing is written again
            self._unchecked.pop(target, None)
            return entry
        if doubt is not None and not doubt.silent and _same(read, doubt.before):
            doubt = None                               # that write was answered, and the value from before is still there: it was not made
        self._unchecked[target] = _Sent(op.new_value, previous, *((doubt.doubt, doubt.silent) if doubt is not None else ()))
        refused, unclear = None, None
        try:
            self._send("write", "PUT", path, json={field: op.new_value})
        except _NoClearAnswer as exc:
            unclear = (str(exc), exc.silent)
        except WriterError as exc:
            refused = str(exc)
        if unclear is not None:                        # made or not, this answer does not say; a write that got no answer stays the one in doubt
            if doubt is None or not doubt.silent:
                doubt = _Sent(op.new_value, previous, *unclear)
                self._unchecked[target] = doubt
            raise self._failure(unclear[0], entry, doubt)
        if refused is not None:                        # not sent, or refused: this write was not made
            raise self._failure(refused, entry, doubt)
        failed = None                                  # how the check of the write the system accepted failed, if it did
        try:
            read_back, where = self._read("verify", path, field)
        except WriterError as exc:
            failed = str(exc)
        if failed is None and not _same(read_back, op.new_value):
            failed = f"{where}, read back {read_back!r} after writing {op.new_value!r}"
            if _same(read_back, read):                 # the value from before the write: the system ignored the write
                raise self._failure(failed, entry, doubt)
        if failed is not None:                         # raised here and not in the except block, so that the error has no context
            raise UnverifiedWrite(failed, entry.model_copy(update={"error": failed}))
        self._unchecked.pop(target, None)              # the write is checked: the next attempt on this target starts afresh
        return entry

    def _remembered(self, op: WriteOp, case_id: str, rule_id: str, evidence: dict, now: datetime, doubt: _Sent) -> WriteLogEntry:
        """The entry of an attempt that made no entry of its own while a write of the operation is in doubt: built from what is
        remembered of that write, the value read before it as the previous value (and as the old value of an operation without)."""
        known = op if op.old_value is not None else op.model_copy(update={"old_value": doubt.before})
        return self._entry(known, case_id, rule_id, evidence, now, doubt.before)

    @staticmethod
    def _failure(message: str, entry: WriteLogEntry, doubt: _Sent | None) -> WriterError:
        """The error of an attempt that made no write of its own that the system accepted: a plain WriterError, or, while the
        outcome of a write of the operation is not known, an UnknownWrite with the entry and what happened to that write."""
        if doubt is None:
            return WriterError(message)
        return UnknownWrite(message, entry.model_copy(update={"error": doubt.doubt}))

    def _change_file(self, op: WriteOp, path: str, case_id: str, rule_id: str, evidence: dict, now: datetime) -> WriteLogEntry:
        name = PureWindowsPath(op.document or "").name         # PureWindowsPath cuts at either kind of separator
        if not name:
            raise ValueError(f"{op.kind} on PO {op.po_number} names no document")
        target = (op.kind, op.po_number, name)
        doubt = self._unchecked.get(target)                    # an earlier attempt whose outcome is not known
        if op.kind == "detach_document":                       # a DELETE can be sent again without harm
            entry = self._entry(op, case_id, rule_id, evidence, now, op.old_value)
            self._send_file("detach", "DELETE", path, target, entry, doubt, params={"name": name})
            return entry
        entry = self._entry(op, case_id, rule_id, evidence, now, op.old_value)
        if doubt is not None:                          # a second POST would attach the file twice, and nothing can be read to settle it:
            # looked at before the document is read, so that no failure of this attempt turns the doubt into a plain failure
            raise self._failure(f"attach POST {path}: not sent again, the outcome of an earlier attempt is not known", entry, doubt)
        try:
            content, problem = Path(op.document).read_bytes(), None
        except OSError as exc:
            content, problem = None, type(exc).__name__
        if content is None:                            # raised here and not in the except block, so that the error has no context
            raise WriterError(f"attach POST {path}: cannot read the document {name!r} ({problem})")
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self._send_file("attach", "POST", path, target, entry, None, files={"file": (name, content, content_type)})
        return entry

    def _send_file(self, step: str, method: str, path: str, target: tuple, entry: WriteLogEntry, doubt: _Sent | None, **options) -> None:
        """The one request of an attachment or a detachment. A success settles the file: what is remembered of either kind is
        forgotten. No answer, or one that is neither a success nor a refusal, leaves the outcome not known: UnknownWrite, and the
        target remembers it. A refusal is a plain WriterError, unless an earlier attempt is still in doubt."""
        refused, unclear = None, None
        try:
            self._send(step, method, path, **options)
        except _NoClearAnswer as exc:
            unclear = str(exc)
        except WriterError as exc:
            refused = str(exc)
        if unclear is not None:
            doubt = doubt if doubt is not None else _Sent(None, None, unclear, True)
            self._unchecked[target] = doubt
            raise self._failure(unclear, entry, doubt)
        if refused is not None:
            raise self._failure(refused, entry, doubt)
        for kind in ("attach_document", "detach_document"):
            self._unchecked.pop((kind, *target[1:]), None)

    @staticmethod
    def _entry(op: WriteOp, case_id: str, rule_id: str, evidence: dict, now: datetime, previous: str | None) -> WriteLogEntry:
        """The entry for a change that is about to be made; one that cannot be logged fails here, before the change."""
        entry = WriteLogEntry(entry_id=uuid.uuid4().hex, case_id=case_id, op=op, status="done", previous=previous, new=op.new_value,
                              rule_id=rule_id, evidence=evidence, timestamp=now.isoformat())
        _json_line(entry)
        return entry

    def _send(self, step: str, method: str, path: str, **options) -> requests.Response:
        """One request with the token and the timeout. Anything but a 2xx answer is a WriterError: a plain one when nothing was
        sent (no usable token, a header or an address that cannot be sent, a connection that was never made: refused, a connect
        time-out, a TLS or a proxy failure) or the answer is a refusal (4xx), and a _NoClearAnswer when the request was sent
        and no answer came, or the answer is neither a success nor a refusal. The error is raised
        after the try statement and not in its except blocks, so that it keeps no context: the errors of requests can quote
        the token."""
        where = f"{step} {method} {path}"
        failure, response, asked = None, None, False
        try:
            token = self.token()
            if not isinstance(token, str):
                failure = f"the token is not text ({type(token).__name__})"
            elif not token.strip():
                failure = "the token is empty"
            else:
                headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
                asked = True
                response = self.session.request(method, self.base_url + path, headers=headers, timeout=self.timeout,
                                                allow_redirects=False, **options)
        except requests.Timeout as exc:
            failure = f"no answer within {self.timeout:g} s"
            asked = asked and not _never_connected(exc)
        except (requests.RequestException, UnicodeError) as exc:       # only the class is kept: its text can quote the header values
            failure = type(exc).__name__
            # a ValueError: the request could not be made; a connection that never stood: either way nothing was sent
            asked = asked and not isinstance(exc, ValueError) and not _never_connected(exc)
        if failure is not None:
            raise _NoClearAnswer(f"{where}: {failure}", silent=True) if asked else WriterError(f"{where}: {failure}")
        if 200 <= response.status_code < 300:
            return response
        if 400 <= response.status_code < 500:
            raise WriterError(f"{where}: HTTP {response.status_code}")
        raise _NoClearAnswer(f"{where}: HTTP {response.status_code}", silent=False)

    def _read(self, step: str, path: str, field: str) -> tuple[str | None, str]:
        """The field of the resource at `path` as text (None where the resource has null), and the words that name the call."""
        response = self._send(step, "GET", path)
        where = f"{step} GET {path}: HTTP {response.status_code}"
        try:
            body = response.json()
        except ValueError:
            body = _NOT_JSON
        if body is _NOT_JSON:                          # raised here and not in the except block, so that the error has no context
            raise WriterError(f"{where}, the body is not JSON")
        if not isinstance(body, dict) or field not in body:
            raise WriterError(f"{where}, the body has no field {field!r}")
        value = body[field]
        if value is None or isinstance(value, str):
            return value, where
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value), where
        raise WriterError(f"{where}, the field {field!r} is not text or a number")


def _never_connected(exc: Exception) -> bool:
    """Whether a request failed before a connection stood, so that nothing was sent: a connect time-out, a TLS or a proxy
    failure, or a connection that was refused or whose host was not found (the error then holds urllib3's NewConnectionError as
    its reason). A connection that was made and then lost is not among them."""
    if isinstance(exc, (requests.exceptions.ConnectTimeout, requests.exceptions.SSLError, requests.exceptions.ProxyError)):
        return True
    reason = getattr(exc.args[0], "reason", None) if exc.args else None
    return isinstance(reason, urllib3.exceptions.NewConnectionError)


def _placeholders(key: str, template: str) -> set[str]:
    """The values that the path template of endpoint `key` names. Only the four values of an operation may be named, as they
    are: not an attribute of one, not with a format or a conversion, and not by position."""
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:                          # a brace that is not closed
        raise ValueError(f"endpoint {key!r} has a path that is not a template ({exc})") from None
    named = set()
    for _, name, spec, conversion in parsed:
        if name is None:                               # text without a value
            continue
        if name not in _PLACEHOLDERS:
            raise ValueError(f"endpoint {key!r} names {name!r} in its path, which is not one of "
                             f"{', '.join('{' + placeholder + '}' for placeholder in _PLACEHOLDERS)}")
        if spec or conversion:
            raise ValueError(f"endpoint {key!r} names {name!r} with a format or a conversion, which is not supported")
        named.add(name)
    return named


def _same(read: str | None, expected: str | None) -> bool:
    """Whether two values are the same: equal text, or numbers of equal value, as an API may give "12.7" for "12.70"."""
    if read == expected:
        return True
    if read is None or expected is None:
        return False
    try:
        return Decimal(read) == Decimal(expected)
    except InvalidOperation:                           # one of them is not a number
        return False
