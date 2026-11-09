"""The trigger of the post-check through the work mailbox: the messages that arrive in one folder of the mailbox are read through
Microsoft Graph with the user's own sign-in (aid/connections/microsoft.py) and saved as .eml files into the watched folder, where
scan_folder (aid/sources/mailbox.py) handles them as it handles the files of a mailbox rule: it classifies their attachments, opens
the cases and files the replies to the aid's questions by their Message-ID. The runner calls sync_mailbox at each pass, before the
scan, so that a confirmation that arrives opens its case in the same pass.

sync_mailbox(account, settings, now) is off when settings.mail_folder is None or the tenant or the client is missing (trigger_on):
it then asks nothing and returns MailSync([], 0, None). Otherwise:

- The connection. The first sync of a (mailbox, folder) pair records the time of the connection, connected_at (`now` in UTC; a
  time without a zone is local time), and takes the folder's baseline: the delta query
  GET https://graph.microsoft.com/v1.0/{me | users/<mailbox>}/mailFolders/<folder>/messages/delta?changeType=created
  &$select=id,receivedDateTime, followed by each @odata.nextLink to the @odata.deltaLink, which is stored. Of the messages it
  lists, those received before connected_at were in the folder at the connection: they become known and none is saved. One
  received at or after connected_at arrived while the first attempts failed (a folder not shared yet, a lapsed sign-in), and is
  saved as below. When the settings name another mailbox or another folder than the state does, a new connection starts.
- Later syncs call the stored delta link, which lists the messages created in the folder since, and save each that is not known
  yet, whatever its receive time: an e-mail that arrives, and one that an Outlook rule or a drag moves into the folder, starts its
  case. A message is known by its mark, the first 12 hex digits of the sha256 of its id: one of the baseline, one saved before,
  and one passed over as below, so that Graph giving a message again (a replay, or a change of its read state) saves nothing. A
  message moved out of the folder and back in gets a new id from Graph, and is new again.
- A new baseline is taken in the same way when Graph no longer knows the delta link (410, or an error code syncStateNotFound,
  syncStateInvalid or resyncRequired: the answers Microsoft documents for an expired state token,
  https://learn.microsoft.com/en-us/graph/delta-query-overview), and when a link was dropped (below). The time it compares with is
  then last_sync, the last sync that ended without an error: a message received at or after it is saved unless it is known or its
  file is there, and the others become known. So mail that arrived meanwhile is not lost, but a message moved into the folder
  meanwhile with an older receive time is not read (the manual says to move it out and in again). A baseline is stored, its delta
  link and its marks, only once its last page has been read, its marks then replacing the known ones (so that the marks of the
  messages no longer in the folder go): one that stops part-way keeps the marks of before, with those of the files it saved, and
  the delta link of before, so that the next sync takes the baseline again.
- The links. A stored delta link or a next link goes with the token, and the messages it lists are saved, so it is called only
  when it leads to a delta query of a mail folder on Microsoft Graph beyond doubt: printable ASCII without a backslash (no space,
  tab or control character), the scheme https, no user part before the host, the host graph.microsoft.com and no port or port
  443 by urllib.parse.urlsplit, which must be able to read it (a square bracket in the host it refuses), and a path that, %-decoded
  and in any case, ends in "/messages/delta", holds a segment "mailFolders" or "mailFolders(...)" (Graph writes both) and no
  "." or "..". The folder's id is not compared, since Graph's own spelling of it in its links cannot be checked here. urlsplit and
  requests read a backslash or a user part differently, so that such a link could pass a check of the host and still be sent
  elsewhere. Any other link ends the sync with the error "unexpected link host" before the token is sent, the link is dropped,
  and the next sync takes a new baseline as above. The state file is no security boundary for all that: whoever can write the
  aid's folder can also change its settings, so the manual's advice to keep that folder to the runner's user stands.
- The messages. The whole e-mail of each new message, GET .../messages/<id>/$value, is written into the watched folder as
  <its receivedDateTime as YYYYmmddTHHMMSS, in UTC>_<its mark>.eml: first under that name with a dot before it and ".part"
  after it, which the scan passes over, and then renamed, so that the scan never reads half a file. Passed over and counted as
  skipped: a known message, a message whose file is in the watched folder or in its processed/ already, an entry that Graph marks
  removed or that has no id or receive time, a message that is gone (404) by the time its e-mail is asked for, and the aid's own
  e-mails, which come back into the mailbox it sends from (the digest, and the buyer's copy of each question): an e-mail with
  Auto-Submitted: auto-generated whose subject begins with the digest's "[Inkoopassistent]" or whose Message-ID is one the aid
  makes, 32 hex digits at the domain of an address the aid sends from (settings.mailbox, the signed-in account, settings.aid_email).
  A supplier's reply to a question, a bounce, and a supplier's own automatic mail are saved.
- The errors. On 429 and 503 (Graph's throttling) the sync waits the Retry-After seconds, at most RETRY_CAP, through `sleep`, and
  asks once more; a second refusal ends the sync with the error "HTTP 429" or "HTTP 503". A 401, and an account without a usable
  sign-in, raise SignInNeeded. A Microsoft that cannot be reached, for the token or for Graph (a request that fails: no
  connection, a timeout), raises MicrosoftUnreachable with the class of the error only, never its text, which holds the address:
  it says nothing about the sign-in, so the runner keeps a lapse it noted. A 404 on the folder ends the sync with the error "mail
  folder not found"; any other status with "HTTP <status>", and an answer that cannot be read with the class of the error. The
  files saved before an error stay, and are counted; the delta link stays the one before, so that the next sync asks again from
  there, also for a message whose e-mail could not be had.
- The state, <cases_root>/mailbox_state.json, rewritten whole at each sync: {"mailbox": the mailbox (null: the account's own),
  "folder": the folder's id, "connected_at", "delta_link": the link to call next (null until the baseline is taken, or after a
  link was dropped), "last_sync": the time of the last sync that ended without an error, "saved": the number of messages saved
  since the connection}, the times in UTC. The marks of the known messages are kept apart in <cases_root>/mailbox_known.json, a
  list, written only when they changed: a large folder gives many. A state file that cannot be read starts a new connection; a
  marks file that cannot be read drops the delta link, so that a new baseline is taken. forget_connection removes both files: the
  runner calls it while the trigger is off, and the settings tab when a save turns the trigger off or names another folder, so
  that connecting a folder again later starts from that moment and reads nothing of the time in between.

The scopes asked for are mail_scopes(settings.mailbox) of aid/connections/microsoft.py: another mailbox needs the shared ones.
The trigger only reads the folder: it never moves, flags, marks or deletes a message, and it sends nothing.

connection_of gives the state of the connection that the settings name, for the settings tab of the review page. list_folders
and folder_summary are the tab's reads of the mailbox: the folders at the top of the mailbox (GET .../mailFolders?$top=100), and
the number of messages in a folder and the receive time of its newest message (GET .../mailFolders/<id> and
.../mailFolders/<id>/messages?$top=1&$orderby=receivedDateTime desc&$select=receivedDateTime), never a subject or a sender. They
raise GraphError with the status code or the class of the error (a Microsoft that cannot be reached included), and SignInNeeded
as above. Graph is called with requests, or with the `session` given, which the tests replace with a fake.
"""
import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from email import policy
from email.parser import BytesHeaderParser
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote, unquote, urlsplit

import requests

from aid.case import _write_json                  # the state is rewritten whole or not at all, as a case file is
from aid.connections.microsoft import (GRAPH, GRAPH_TIMEOUT, SIGN_IN_AGAIN, MicrosoftAccount, MicrosoftUnreachable, SignInNeeded, mail_scopes,
                                       owner_path)
from aid.settings import Settings
from aid.sources.mailbox import PROCESSED_FOLDER

STATE_FILE = "mailbox_state.json"
KNOWN_FILE = "mailbox_known.json"                 # the marks of the known messages, apart from the state: a large folder gives many
RETRY_CAP = 30                                    # the longest wait, in seconds, after a 429 or a 503
THROTTLED = (429, 503)
FOLDER_GONE = "mail folder not found"
WRONG_HOST = "unexpected link host"
DIGEST_PREFIX = "[Inkoopassistent]"                # the start of the digest's subject (SUBJECT_PREFIX of aid/notify.py)
_RESYNC = {"syncstatenotfound", "syncstateinvalid", "resyncrequired"}       # the error codes of an expired delta link, in lower case
_STATE_KEYS = ("mailbox", "folder", "connected_at", "delta_link", "last_sync", "saved")
_AID_ID = re.compile(r"<[0-9a-f]{32}@([^<>@\s]+)>")   # a Message-ID the aid makes: a UUID in hex at the domain it sends from
_PRINTABLE = re.compile(r"[!-~]+")                # printable ASCII without a space: a link of Graph's, its escapes included
_GRAPH_HOST = urlsplit(GRAPH).hostname


class MailSync(NamedTuple):
    saved: list[Path]                             # the .eml files written into the watched folder, in the order of Graph's answers
    skipped: int                                  # the messages passed over (see the module docstring)
    error: str | None                             # why the sync ended early, if it did


class GraphError(Exception):
    """An answer of Graph that cannot be used; its text is the status code ("HTTP 404") or the class of the error only."""

    def __init__(self, text: str, status: int | None = None):
        super().__init__(text)
        self.status = status


class _Expired(GraphError):
    """A delta link or a next link that Graph no longer knows."""


def trigger_on(settings: Settings) -> bool:
    """Whether the runner reads the mailbox: a folder is set, and so are the tenant and the client of the sign-in."""
    return bool(settings.mail_folder and settings.ms_tenant_id and settings.ms_client_id)


def sync_mailbox(account: MicrosoftAccount, settings: Settings, now: datetime, session=None,
                 sleep: Callable[[float], None] = time.sleep) -> MailSync:
    """Save the messages that arrived in the folder of the settings since the last sync into the watched folder (see the module
    docstring)."""
    if not trigger_on(settings):
        return MailSync([], 0, None)
    root, inbox = Path(settings.cases_root), Path(settings.watched_folder)
    state, known = _connection(root, settings, now)
    token = account.token(mail_scopes(settings.mailbox))
    inbox.mkdir(parents=True, exist_ok=True)
    reading = _Reading(session or requests, token, sleep, settings, state, known, inbox, _own_domains(account, settings))
    try:
        reading.run(now)
    except GraphError as error:
        reading.error = str(error)
    finally:
        state["saved"] += len(reading.saved)
        if reading.known != known:               # the marks first: a state without them would call its link with marks too few
            _write_json(root / KNOWN_FILE, sorted(reading.known))
        _write_json(root / STATE_FILE, state)
    return MailSync(reading.saved, reading.skipped, reading.error)


class _Reading:
    """One sync: the rounds of the delta query and the messages they list."""

    def __init__(self, session, token: str, sleep: Callable[[float], None], settings: Settings, state: dict, known: set[str],
                 inbox: Path, own: set[str]):
        self.session, self.token, self.sleep, self.state, self.inbox, self.own = session, token, sleep, state, inbox, own
        self.base = f"{GRAPH}/{owner_path(settings.mailbox)}"
        self.delta = f"{self.base}/mailFolders/{quote(settings.mail_folder, safe='')}/messages/delta?changeType=created"
        self.known: set[str] = set(known)
        self.saved: list[Path] = []
        self.skipped, self.error = 0, None

    def run(self, now: datetime) -> None:
        link = self.state["delta_link"]
        if link is not None:
            try:
                link = self.round(link, self.known, self.known)
            except _Expired:
                link = None
        if link is None:                             # a new connection, a link Graph no longer knows, or a link that was dropped
            link = self.baseline(_utc(self.state["last_sync"] or self.state["connected_at"]))
        self.state.update(delta_link=link, last_sync=_stamp(now))

    def baseline(self, since: datetime) -> str | None:
        """A new baseline of the folder, which saves the messages received at or after `since` that are not known and knows the
        others; its delta link. Its marks replace the known ones only once its last page has been read (see the module
        docstring)."""
        before, listed = set(self.known), set()
        link = self.round(f"{self.delta}&$select=id,receivedDateTime", before, listed, since)
        self.known = listed
        return link

    def round(self, url: str, known: set[str], marks: set[str], since: datetime | None = None) -> str | None:
        """Follow the pages of a delta query from the url to its delta link, which it returns: each message listed is saved when it
        is new (not in `known`, and received at or after `since` when given), and its mark is added to `marks`."""
        while True:
            if not _on_graph(url):
                self.state["delta_link"] = None
                raise GraphError(WRONG_HOST)
            response = _get(self.session, url, self.token, self.sleep)
            if _expired(response):
                raise _Expired(f"HTTP {response.status_code}", response.status_code)
            if response.status_code == 404:
                raise GraphError(FOLDER_GONE, 404)
            page = _json(response)
            items = page.get("value", [])
            if not isinstance(items, list):
                raise GraphError("ValueError: the answer of Graph holds no list of messages")
            for item in items:
                self.entry(item, known, marks, since)
            if next_link := page.get("@odata.nextLink"):
                url = next_link
                continue
            return page.get("@odata.deltaLink")

    def entry(self, item, known: set[str], marks: set[str], since: datetime | None) -> None:
        """Save the e-mail of one entry of a delta query when it is new, or count it as skipped (see the module docstring)."""
        message_id = item.get("id") if isinstance(item, dict) else None
        received = item.get("receivedDateTime") if isinstance(message_id, str) else None
        when = _utc(received) if isinstance(received, str) else None
        if not isinstance(message_id, str) or "@removed" in item or when is None:
            self.skipped += 1
            return
        mark = hashlib.sha256(message_id.encode()).hexdigest()[:12]
        name = f"{when:%Y%m%dT%H%M%S}_{mark}.eml"
        new = mark not in known and (since is None or when >= since) and not (self.inbox / name).exists() \
            and not (self.inbox / PROCESSED_FOLDER / name).exists()
        if not new:
            marks.add(mark)
            self.skipped += 1
            return
        response = _get(self.session, f"{self.base}/messages/{quote(message_id, safe='')}/$value", self.token, self.sleep)
        if response.status_code == 404 or (200 <= response.status_code < 300 and _own(response.content, self.own)):
            marks.add(mark)                                           # gone since the delta query listed it, or the aid's own
            self.skipped += 1
            return
        if not 200 <= response.status_code < 300:
            raise GraphError(f"HTTP {response.status_code}", response.status_code)
        partial = self.inbox / f".{name}.part"
        partial.write_bytes(response.content)
        os.replace(partial, self.inbox / name)
        marks.add(mark)
        self.known.add(mark)                                          # a message saved stays known, whatever its round meets next
        self.saved.append(self.inbox / name)


def read_state(cases_root: Path) -> dict | None:
    """The state of the connection (see the module docstring), or None when there is none or it cannot be read."""
    try:
        state = json.loads((Path(cases_root) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or any(key not in state for key in _STATE_KEYS) or not isinstance(state["saved"], int) \
            or not isinstance(state["folder"], str) or not isinstance(state["mailbox"], str | None) \
            or not isinstance(state["delta_link"], str | None) or not _is_time(state["connected_at"]) \
            or not (state["last_sync"] is None or _is_time(state["last_sync"])):
        return None
    return {key: state[key] for key in _STATE_KEYS}


def connection_of(settings: Settings) -> dict | None:
    """The state of the connection to the mailbox and the folder of the settings, or None when there is none yet (no state, or a
    state of another mailbox or folder)."""
    state = read_state(settings.cases_root)
    if state is None or (state["mailbox"] or "").lower() != (settings.mailbox or "").lower() or state["folder"] != settings.mail_folder:
        return None
    return state


def forget_connection(cases_root: Path) -> None:
    """Remove the stored connection, its state and its marks, so that the next connection starts from its own moment."""
    for name in (STATE_FILE, KNOWN_FILE):
        (Path(cases_root) / name).unlink(missing_ok=True)


def list_folders(account: MicrosoftAccount, mailbox: str | None, session=None) -> list[tuple[str, str]]:
    """The folders at the top of the mailbox as (display name, id), in Graph's order."""
    try:
        token = account.token(mail_scopes(mailbox))
        page = _json(_get(session or requests, f"{GRAPH}/{owner_path(mailbox)}/mailFolders?$top=100", token, time.sleep))
    except MicrosoftUnreachable as error:
        raise GraphError(str(error)) from None
    return [(str(folder.get("displayName") or folder["id"]), folder["id"]) for folder in page.get("value", [])
            if isinstance(folder, dict) and isinstance(folder.get("id"), str)]


def folder_summary(account: MicrosoftAccount, mailbox: str | None, folder: str, session=None) -> tuple[int, datetime | None]:
    """The number of messages in the folder and the receive time of its newest message, in UTC (None when the folder is empty)."""
    session, path = session or requests, f"{GRAPH}/{owner_path(mailbox)}/mailFolders/{quote(folder, safe='')}"
    try:
        token = account.token(mail_scopes(mailbox))
        count = _json(_get(session, path, token, time.sleep)).get("totalItemCount")
        newest = _json(_get(session, f"{path}/messages?$top=1&$orderby={quote('receivedDateTime desc')}&$select=receivedDateTime",
                            token, time.sleep)).get("value") or [{}]
    except MicrosoftUnreachable as error:
        raise GraphError(str(error)) from None
    received = newest[0].get("receivedDateTime") if isinstance(newest[0], dict) else None
    return (count if isinstance(count, int) else 0), (_utc(received) if isinstance(received, str) else None)


def _connection(root: Path, settings: Settings, now: datetime) -> tuple[dict, set[str]]:
    """The state of the connection to the folder of the settings and the marks it knows: the files', or a new connection from now,
    written at once so that its time is kept whatever the sync then meets."""
    state = connection_of(settings)
    if state is not None:
        known = _read_known(root)
        if known is None:                            # the marks are lost: a new baseline knows the folder again
            state["delta_link"], known = None, set()
        return state, known
    state = {"mailbox": settings.mailbox, "folder": settings.mail_folder, "connected_at": _stamp(now), "delta_link": None,
             "last_sync": None, "saved": 0}
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / KNOWN_FILE, [])
    _write_json(root / STATE_FILE, state)
    return state, set()


def _read_known(root: Path) -> set[str] | None:
    """The marks of the known messages, or None when their file is missing or cannot be read."""
    try:
        marks = json.loads((root / KNOWN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return set(marks) if isinstance(marks, list) and all(isinstance(mark, str) for mark in marks) else None


def _own_domains(account: MicrosoftAccount, settings: Settings) -> set[str]:
    """The domains of the addresses the aid sends from, in lower case: the mailbox's, the signed-in account's and aid_email's."""
    addresses = [settings.mailbox, settings.aid_email]
    try:
        addresses.append(account.account())
    except Exception:                                # a cache that cannot be read now: the other two still count
        pass
    return {address.rpartition("@")[2].strip().lower() for address in addresses if isinstance(address, str) and "@" in address}


def _on_graph(url) -> bool:
    """Whether the link is a delta query of a mail folder on Microsoft Graph beyond doubt, so that the token may go with it and its
    messages be saved (see the module docstring)."""
    if not isinstance(url, str) or not _PRINTABLE.fullmatch(url) or "\\" in url:
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:                               # a square bracket in the host, or a port that is no number
        return False
    if not (parts.scheme == "https" and "@" not in parts.netloc and parts.hostname == _GRAPH_HOST and port in (None, 443)):
        return False
    segments = unquote(parts.path).lower().split("/")
    return segments[-2:] == ["messages", "delta"] and not {".", ".."} & set(segments) \
        and any(segment == "mailfolders" or segment.startswith("mailfolders(") for segment in segments)


def _own(content: bytes, domains: set[str]) -> bool:
    """Whether an e-mail is one the aid sent itself, `domains` being those the aid sends from (see the module docstring)."""
    try:
        mail = BytesHeaderParser(policy=policy.default).parsebytes(content)
        if str(mail["Auto-Submitted"] or "").partition(";")[0].strip().lower() != "auto-generated":
            return False
        if str(mail["Subject"] or "").startswith(DIGEST_PREFIX):
            return True
        aid_id = _AID_ID.fullmatch(str(mail["Message-ID"] or "").strip())
    except (ValueError, TypeError, IndexError):          # headers the parser cannot take are no sign of the aid
        return False
    return aid_id is not None and aid_id.group(1).lower() in domains


def _get(session, url: str, token: str, sleep: Callable[[float], None]):
    """GET the address with the token, honouring Graph's throttling (see the module docstring); 401 raises SignInNeeded and a
    request that fails MicrosoftUnreachable with the class of its error, never its text, which holds the address."""
    for attempt in (1, 2):
        try:
            response = session.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=GRAPH_TIMEOUT)
        except requests.RequestException as error:
            raise MicrosoftUnreachable(type(error).__name__) from None
        if response.status_code not in THROTTLED or attempt == 2:
            break
        sleep(_retry_after(response))
    if response.status_code == 401:
        raise SignInNeeded(f"Microsoft Graph refused the sign-in: {SIGN_IN_AGAIN}")
    return response


def _json(response) -> dict:
    """The JSON object of a successful answer; GraphError with the status code otherwise."""
    if not 200 <= response.status_code < 300:
        raise GraphError(f"HTTP {response.status_code}", response.status_code)
    try:
        data = response.json()
    except ValueError:
        raise GraphError("ValueError: the answer of Graph is no JSON") from None
    if not isinstance(data, dict):
        raise GraphError("ValueError: the answer of Graph is no JSON object")
    return data


def _expired(response) -> bool:
    """Whether a delta answer says that its state token has expired."""
    if response.status_code == 410:
        return True
    if not 400 <= response.status_code < 500:
        return False
    try:
        code = (response.json().get("error") or {}).get("code") or ""
    except (ValueError, AttributeError):
        return False
    return str(code).lower() in _RESYNC


def _retry_after(response) -> float:
    """The seconds Graph asks to wait, at most RETRY_CAP; RETRY_CAP when it says no number."""
    try:
        return min(max(float(response.headers.get("Retry-After", "")), 0.0), RETRY_CAP)
    except ValueError:
        return RETRY_CAP


def _stamp(moment: datetime) -> str:
    """A time in UTC as Graph writes it, "2026-11-12T08:00:00Z"; a time without a zone is local time."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_time(value) -> bool:
    return isinstance(value, str) and _utc(value) is not None


def _utc(text: str) -> datetime | None:
    """A time of Graph or of the state in UTC; a time without a zone is taken as UTC; None for a text that is no time."""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.utcoffset() is None else moment.astimezone(timezone.utc)
