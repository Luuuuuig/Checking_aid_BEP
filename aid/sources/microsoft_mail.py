"""The trigger of the post-check through the work mailbox: the messages that arrive in one folder of the mailbox are read through
Microsoft Graph with the user's own sign-in (aid/connections/microsoft.py) and saved as .eml files into the watched folder, where
scan_folder (aid/sources/mailbox.py) handles them as it handles the files of a mailbox rule: it classifies their attachments, opens
the cases and files the replies to the aid's questions by their Message-ID. The runner calls sync_mailbox at each pass, before the
scan, so that a confirmation that arrives opens its case in the same pass. A second folder of the same mailbox, the folder for orders
(typically Sent Items), may be read as well, for purchase orders only, by sync_orders, after the first and before the scan (see "The
folder for orders" below): the trigger reads one folder, or two.

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

The folder for orders. settings.order_folder names a second folder of the same mailbox, typically Sent Items, for when the ERP sends
the purchase orders through the buyer's own Outlook, so that they never reach the folder of mail_folder unless the buyer is in copy.
sync_orders(account, settings, now) reads it for purchase orders only, and only while the trigger is on and the folder is set
(orders_on); otherwise it asks nothing and returns OrderSync([], 0, None, 0, None). A reading is told which folder it reads by a
_Folder, the setting that holds the folder's id, the files of its connection and what its baseline selects: MAIN for the folder of
mail_folder, as above, and ORDERS for the folder for orders, whose connection is kept in <cases_root>/mailbox_orders_state.json, in
the form of the main folder's state ("folder" is order_folder, and "saved" counts the purchase orders it gave the watched folder),
with its marks in <cases_root>/mailbox_orders_known.json, and whose baseline asks for id,receivedDateTime,sentDateTime,hasAttachments.
The connection, the baseline at the connection and after an expired link, the links, the throttling and the errors of Graph are those
of the main folder, so that only what comes into the folder after its connection is read, also what a drag moves there. At a pass at
which the main folder found the sign-in lapsed, the runner records the connection alone, asking nothing (connect_orders), so that the
folder for orders is connected at the first pass after it is set, as the main folder is, whether or not the sign-in has lapsed. The
time of a message is its receivedDateTime, else its sentDateTime, which a sent copy may carry alone; an entry without either, or that
Graph marks removed, is skipped without a mark. Each other message goes through these steps, and the first that applies decides:
1. A known message is skipped and keeps its mark; in a baseline, so is a message received before the time it compares with, which
   becomes known unless Graph says it has no attachment.
2. A message whose hasAttachments is false is skipped by its listing alone: its e-mail is never asked and it gets no mark, since a
   message passed over so needs none to stay out. A missing value counts as true.
3. Its whole e-mail is asked (GET .../messages/<id>/$value) and held in memory only: it is never written. A 404 makes it known and
   skipped; any other status ends the sync as for the main folder, with the delta link of before.
4. The e-mail is read in memory, in one try: an exception there means that it cannot be read (the parser's IndexError on a malformed
   parameter, say), and it then counts as a message without a purchase order, known and skipped, nothing of it written or recorded;
   the sync goes on with the next message and gives their number (OrderSync.unread) and the class of the first failure
   (unread_kind), never its text. Passed over whole, known and skipped: the aid's own message, which carries the header X-Aid-Template
   or X-Aid-Case, a subject that begins with the digest's "[Inkoopassistent]", or what _own takes for the aid's own (none of the
   aid's messages carries an attachment besides); a reply or a forward, whose In-Reply-To or References header is not empty or whose
   subject begins with a reply or forward prefix (REPLY_OR_FORWARD of aid/messaging), so that a supplier's document the buyer answers
   or sends on is never filed as an order; an e-mail without a Message-ID, which could not be told apart from its copy in the main
   folder; and an e-mail whose Message-ID the main folder used (below). An attached e-mail is one attachment and is never opened.
5. Of the rest, each attachment whose name ends in ".pdf" (in any case), as the scan finds them (_named_attachments of
   aid/sources/mailbox.py), is classed from its bytes by is_po_pdf of that module: by the layout of the settings, or without one by the
   title words of the made-up purchase orders, never by the decider and never by the sender, since every message of Sent Items is the
   mailbox's own. Only its first page is read, so that a long PDF holds the sync for a moment only. A PDF whose first page cannot be
   read, or whose list of pages cannot be read, is no purchase order, and nothing of it is said; a purchase order whose later page
   cannot be read is taken, and the scan, whose reading of the first page reads the whole PDF, cannot read it, so that it stays in the
   watched folder as an error. A message without a purchase order is known and skipped, and nothing else of it is kept.
6. Each purchase order is written into the watched folder as <time>_<mark>_<code>.order.pdf (order_file_name: the time of the
   message as YYYYmmddTHHMMSS, the mark of its id, and the first 8 hex digits of the sha256 of the PDF, so that two attachments with
   the same bytes give one file), first under that name with a dot before it and ".part" after it and then renamed, so that the scan
   never reads half a file; the scan files such a bare file as a PO PDF without classing it again. A name whose file is in the
   watched folder or in its processed/ already is not written again: it was placed by a sync that ended before it recorded the
   message. Then the message is recorded in the used marks, and only then is it known. An OSError while a file is placed or the used
   marks are written (a full disk, a file another program holds) ends the sync as an error of Graph does, with the class of the error
   only, never a path: the message gets no mark and the delta link of before is kept, so that the next sync lists it again, places
   what was not placed and records it. No order is lost to a failure of the aid's own folders.
So nothing of a message without a purchase order is written into the aid's folders, named in a line or kept, but the mark of its id
when it has an attachment: the 12 hex digits of a hash of Microsoft's id, with no time, subject, sender or name. A delta round compares
no time, so that mark is what keeps a message from before the connection out when Graph lists it again, and it keeps a message from
being asked twice. OrderSync holds the order files written, the number of messages skipped, the error that ended the sync, and the
number and the class of the e-mails that could not be read.

One message seen in both folders is used once: an order e-mail with the buyer in copy has a copy in each, and so does a test e-mail
to oneself. Its identity is its Message-ID, kept by its mark only (message_mark: the first 12 hex digits of the sha256 of the header,
the same for both folders; the Message-ID itself holds a host or a domain and is never kept) in <cases_root>/mailbox_used.json, a JSON
object {mark: "main" or "orders"} in the order the marks came, cut to the newest USED_CAP. It is written whole right after each
message's files are placed, not at the end of the sync, so that a process that ends in between loses no order and records no more than
one message too few. The main folder reads and writes it only while a folder for orders is set (orders_on): it passes over a message
the folder for orders used, with no file and no line, records a message it saves as "main" right after its .eml is in place, and
records as "main" a message it passes over because its .eml lies in the watched folder or in processed/ while its mark is not known (a
sync cut off before its marks were written), its Message-ID read from that file; it looks for that file only for a message it would
otherwise read, so that a baseline never looks at the files of the messages from before the time it compares with. The used marks never
change the main folder's reading: a write of them that fails there is passed over, the record stays in memory, and the write is tried
again at the next record and once more at the end of the sync, after the main folder's own marks and state. A failure that lasts shows
when the folder for orders next records a message: its own write fails and ends its sync, with the class of the error. The folder for
orders records "orders" only for a message it took, and passes over one recorded "main". Each folder is stopped only by the other
folder's use, so that moving a message out of a folder and in again reads it again in that folder. Two messages with two Message-IDs
that carry the same PDF both reach the watched folder, and the PO store files the second as it does for the main folder. A file of the
used marks that cannot be read counts as empty. forget_orders removes the state and the marks of the folder for orders, and with
used=True the used marks too: the runner calls it while no folder for orders is set, and the settings tab when a save empties the
folder (with the used marks) or names another folder or mailbox (without them). orders_connection_of gives the state of the connection
of the folder for orders that the settings name, for the settings tab.

The scopes asked for are mail_scopes(settings.mailbox) of aid/connections/microsoft.py: another mailbox needs the shared ones.
The trigger only reads its folders: it never moves, flags, marks or deletes a message, and it sends nothing.

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
from email.parser import BytesHeaderParser, BytesParser
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote, unquote, urlsplit

import requests

from aid.case import _write_json                  # the state is rewritten whole or not at all, as a case file is
from aid.connections.microsoft import (GRAPH, GRAPH_TIMEOUT, SIGN_IN_AGAIN, MicrosoftAccount, MicrosoftUnreachable, SignInNeeded, mail_scopes,
                                       owner_path)
from aid.messaging import REPLY_OR_FORWARD
from aid.settings import Settings
from aid.sources.mailbox import PROCESSED_FOLDER, is_po_pdf, order_file_name
from aid.sources.mailbox import _named_attachments, _payload     # the attachments of an e-mail as the scan finds them, and their bytes

STATE_FILE = "mailbox_state.json"
KNOWN_FILE = "mailbox_known.json"                 # the marks of the known messages, apart from the state: a large folder gives many
ORDERS_STATE_FILE = "mailbox_orders_state.json"   # the state of the connection of the folder for orders, in the form of STATE_FILE
ORDERS_KNOWN_FILE = "mailbox_orders_known.json"   # the marks of its known messages that have an attachment
USED_FILE = "mailbox_used.json"                   # the marks of the Message-IDs of the messages used, with the folder that used each
USED_CAP = 10000                                  # the most marks USED_FILE keeps, the oldest going first
USED_BY = ("main", "orders")                      # the folder that used a message, as USED_FILE names it
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


class _Folder(NamedTuple):
    """Which folder of the mailbox a reading is for: the setting that holds its id, the files of its connection, and what its delta
    query selects of each message."""
    setting: str                                  # the name of the setting that holds the folder's id
    state_file: str                               # the state of its connection, under cases_root
    known_file: str                               # the marks of its known messages, under cases_root
    select: str                                   # the fields of a message that its baseline asks for


MAIN = _Folder("mail_folder", STATE_FILE, KNOWN_FILE, "id,receivedDateTime")
ORDERS = _Folder("order_folder", ORDERS_STATE_FILE, ORDERS_KNOWN_FILE, "id,receivedDateTime,sentDateTime,hasAttachments")


class MailSync(NamedTuple):
    saved: list[Path]                             # the .eml files written into the watched folder, in the order of Graph's answers
    skipped: int                                  # the messages passed over (see the module docstring)
    error: str | None                             # why the sync ended early, if it did


class OrderSync(NamedTuple):
    saved: list[Path]                             # the order files written into the watched folder, in the order of Graph's answers
    skipped: int                                  # the messages passed over (see "The folder for orders" in the module docstring)
    error: str | None                             # why the sync ended early, if it did: a GraphError's text, or the class of an OSError
    unread: int                                   # the new messages whose e-mail could not be read, passed over as without a purchase order
    unread_kind: str | None                       # the class of the first such failure, never its text


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


def orders_on(settings: Settings) -> bool:
    """Whether the runner reads a folder for orders: the trigger is on (trigger_on) and a folder for orders is set."""
    return trigger_on(settings) and bool(settings.order_folder)


def sync_mailbox(account: MicrosoftAccount, settings: Settings, now: datetime, session=None,
                 sleep: Callable[[float], None] = time.sleep) -> MailSync:
    """Save the messages that arrived in the folder of the settings since the last sync into the watched folder (see the module
    docstring). While a folder for orders is set (orders_on), a message that folder used is passed over, and each message saved is
    recorded as used by this folder (see "The folder for orders" in the module docstring)."""
    if not trigger_on(settings):
        return MailSync([], 0, None)
    root, inbox = Path(settings.cases_root), Path(settings.watched_folder)
    state, known = _connection(root, settings, now)
    token = account.token(mail_scopes(settings.mailbox))
    inbox.mkdir(parents=True, exist_ok=True)
    used = _Used(root) if orders_on(settings) else None
    reading = _Reading(session or requests, token, sleep, settings, state, known, inbox, _own_domains(account, settings), used=used)
    try:
        reading.run(now)
    except GraphError as error:
        reading.error = str(error)
    finally:
        state["saved"] += len(reading.saved)
        if reading.known != known:               # the marks first: a state without them would call its link with marks too few
            _write_json(root / KNOWN_FILE, sorted(reading.known))
        _write_json(root / STATE_FILE, state)
        if used is not None and used.unsaved:    # a write of the used marks that failed during the sync: once more, and quietly
            used.write_quietly()
    return MailSync(reading.saved, reading.skipped, reading.error)


def sync_orders(account: MicrosoftAccount, settings: Settings, now: datetime, session=None,
                sleep: Callable[[float], None] = time.sleep) -> OrderSync:
    """Give the watched folder the purchase orders of the messages that came into the folder for orders since the last sync (see
    "The folder for orders" in the module docstring). Off without a folder for orders (orders_on): it then asks nothing and returns
    OrderSync([], 0, None, 0, None). An error of Graph ends the sync with its text, and an OSError of the aid's own folders with its
    class; SignInNeeded and MicrosoftUnreachable are raised as by sync_mailbox."""
    if not orders_on(settings):
        return OrderSync([], 0, None, 0, None)
    root, inbox = Path(settings.cases_root), Path(settings.watched_folder)
    state, known = _connection(root, settings, now, ORDERS)
    token = account.token(mail_scopes(settings.mailbox))
    inbox.mkdir(parents=True, exist_ok=True)
    reading = _OrdersReading(session or requests, token, sleep, settings, state, known, inbox, _own_domains(account, settings), _Used(root))
    try:
        reading.run(now)
    except GraphError as error:
        reading.error = str(error)
    except OSError as error:                         # a file of the aid's own folders: its class only, never its path
        reading.error = type(error).__name__
    finally:
        state["saved"] += len(reading.saved)
        if reading.known != known:
            _write_json(root / ORDERS_KNOWN_FILE, sorted(reading.known))
        _write_json(root / ORDERS_STATE_FILE, state)
    return OrderSync(reading.saved, reading.skipped, reading.error, reading.unread, reading.unread_kind)


class _Reading:
    """One sync of a folder: the rounds of the delta query and the messages they list. Its step for a new message (message) is the
    main folder's."""

    def __init__(self, session, token: str, sleep: Callable[[float], None], settings: Settings, state: dict, known: set[str],
                 inbox: Path, own: set[str], folder: _Folder = MAIN, used: "_Used | None" = None):
        self.session, self.token, self.sleep, self.state, self.inbox, self.own = session, token, sleep, state, inbox, own
        self.settings, self.folder, self.used = settings, folder, used
        self.base = f"{GRAPH}/{owner_path(settings.mailbox)}"
        folder_id = getattr(settings, folder.setting)
        self.delta = f"{self.base}/mailFolders/{quote(folder_id, safe='')}/messages/delta?changeType=created"
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
        link = self.round(f"{self.delta}&$select={self.folder.select}", before, listed, since)
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
        """One entry of a delta query: one without an id or a time, or that Graph marks removed, is skipped; any other is handed to
        the folder's step for a message, with its id, its mark (the first 12 hex digits of the sha256 of its id) and its time."""
        message_id = item.get("id") if isinstance(item, dict) else None
        received = self.time_of(item) if isinstance(message_id, str) else None
        when = _utc(received) if isinstance(received, str) else None
        if not isinstance(message_id, str) or "@removed" in item or when is None:
            self.skipped += 1
            return
        self.message(item, message_id, hashlib.sha256(message_id.encode()).hexdigest()[:12], when, known, marks, since)

    @staticmethod
    def time_of(item: dict):
        """The time of an entry of the main folder: its receivedDateTime."""
        return item.get("receivedDateTime")

    def message(self, item: dict, message_id: str, mark: str, when: datetime, known: set[str], marks: set[str],
                since: datetime | None) -> None:
        """Save the e-mail of a message of the main folder when it is new, or count it as skipped (see the module docstring). While a
        folder for orders is set (self.used), a message that folder used is passed over, a message saved is recorded as used here
        right after its file is in place, and so is a message whose file a sync that was cut off placed; a write of the used marks
        that fails is passed over (see "The folder for orders" in the module docstring)."""
        name = f"{when:%Y%m%dT%H%M%S}_{mark}.eml"
        placed = None
        if mark not in known and (since is None or when >= since):  # only for a message it would read: an older one is not looked at
            placed = next((path for path in (self.inbox / name, self.inbox / PROCESSED_FOLDER / name) if path.exists()), None)
            if placed is not None and self.used is not None:          # placed by a sync cut off before its marks were written
                self.used.record_quietly(_file_mark(placed), "main")
        if mark in known or (since is not None and when < since) or placed is not None:
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
        identity = message_mark(response.content) if self.used is not None else None
        if identity is not None and self.used.folder_of(identity) == "orders":
            marks.add(mark)                                           # the folder for orders used its sent copy
            self.skipped += 1
            return
        partial = self.inbox / f".{name}.part"
        partial.write_bytes(response.content)
        os.replace(partial, self.inbox / name)
        if identity is not None:
            self.used.record_quietly(identity, "main")
        marks.add(mark)
        self.known.add(mark)                                          # a message saved stays known, whatever its round meets next
        self.saved.append(self.inbox / name)


class _OrdersReading(_Reading):
    """One sync of the folder for orders: the rounds, the baseline, the links and the errors of _Reading, with the folder's own time
    of an entry and its own step for a message (see "The folder for orders" in the module docstring)."""

    def __init__(self, session, token: str, sleep: Callable[[float], None], settings: Settings, state: dict, known: set[str],
                 inbox: Path, own: set[str], used: "_Used"):
        super().__init__(session, token, sleep, settings, state, known, inbox, own, ORDERS, used)
        self.unread, self.unread_kind = 0, None

    @staticmethod
    def time_of(item: dict):
        """The time of an entry of the folder for orders: its receivedDateTime, else its sentDateTime, which a sent copy may carry
        alone."""
        received = item.get("receivedDateTime")
        return received if isinstance(received, str) else item.get("sentDateTime")

    def message(self, item: dict, message_id: str, mark: str, when: datetime, known: set[str], marks: set[str],
                since: datetime | None) -> None:
        """Give the watched folder the purchase orders of a new message of the folder for orders, in the steps of the module
        docstring, or count it as skipped. An OSError while an order file is placed or the used marks are written is raised, before
        the message's mark, so that the next sync reads the message again."""
        attached = item.get("hasAttachments") is not False                # a missing value counts as true
        if mark in known or (since is not None and when < since):
            if mark in known or attached:
                marks.add(mark)
            self.skipped += 1
            return
        if not attached:                                                  # passed over by its listing alone: no mark, no request
            self.skipped += 1
            return
        response = _get(self.session, f"{self.base}/messages/{quote(message_id, safe='')}/$value", self.token, self.sleep)
        if response.status_code == 404:
            marks.add(mark)
            self.skipped += 1
            return
        if not 200 <= response.status_code < 300:
            raise GraphError(f"HTTP {response.status_code}", response.status_code)
        try:
            identity, orders = self.orders_of(response.content)
        except Exception as error:                                       # an e-mail that cannot be read: none of its text is kept
            self.unread += 1
            self.unread_kind = self.unread_kind or type(error).__name__
            identity, orders = None, []
        if not orders:
            marks.add(mark)
            self.skipped += 1
            return
        stem = f"{when:%Y%m%dT%H%M%S}_{mark}"
        for name, data in {order_file_name(stem, data): data for data in orders}.items():
            target = self.inbox / name
            if target.exists() or (self.inbox / PROCESSED_FOLDER / name).exists():
                continue                                                  # placed by a sync that ended before it recorded the message
            partial = self.inbox / f".{name}.part"
            partial.write_bytes(data)
            os.replace(partial, target)
            self.saved.append(target)
        self.used.record(identity, "orders")
        marks.add(mark)
        self.known.add(mark)

    def orders_of(self, content: bytes) -> tuple[str | None, list[bytes]]:
        """The mark of the Message-ID of an e-mail held in memory and the bytes of its PDF attachments that are purchase orders
        (is_po_pdf, with the settings): none for the aid's own message, a reply or a forward, an e-mail without a Message-ID and one
        that the main folder used. Any exception of the reading is the caller's."""
        mail = BytesParser(policy=policy.default).parsebytes(content)
        if _aids_own(mail, content, self.own) or _reply_or_forward(mail):
            return None, []
        identity = _mark_of(mail)
        if identity is None or self.used.folder_of(identity) == "main":
            return None, []
        pdfs = [_payload(part) for name, part in _named_attachments(mail) if name.lower().endswith(".pdf")]
        return identity, [data for data in pdfs if is_po_pdf(data, self.settings)]


def read_state(cases_root: Path, name: str = STATE_FILE) -> dict | None:
    """The state of a connection (see the module docstring), of the main folder unless the name of another state file is given, or
    None when there is none or it cannot be read."""
    try:
        state = json.loads((Path(cases_root) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or any(key not in state for key in _STATE_KEYS) or not isinstance(state["saved"], int) \
            or not isinstance(state["folder"], str) or not isinstance(state["mailbox"], str | None) \
            or not isinstance(state["delta_link"], str | None) or not _is_time(state["connected_at"]) \
            or not (state["last_sync"] is None or _is_time(state["last_sync"])):
        return None
    return {key: state[key] for key in _STATE_KEYS}


def connection_of(settings: Settings, folder: _Folder = MAIN) -> dict | None:
    """The state of the connection to the mailbox and the folder of the settings, of the main folder unless another is given, or None
    when there is none yet (no state, or a state of another mailbox or folder)."""
    state = read_state(settings.cases_root, folder.state_file)
    if state is None or (state["mailbox"] or "").lower() != (settings.mailbox or "").lower() \
            or state["folder"] != getattr(settings, folder.setting):
        return None
    return state


def forget_connection(cases_root: Path) -> None:
    """Remove the stored connection, its state and its marks, so that the next connection starts from its own moment."""
    for name in (STATE_FILE, KNOWN_FILE):
        (Path(cases_root) / name).unlink(missing_ok=True)


def orders_connection_of(settings: Settings) -> dict | None:
    """The state of the connection to the folder for orders of the settings, or None when there is none yet (connection_of)."""
    return connection_of(settings, ORDERS)


def connect_orders(settings: Settings, now: datetime) -> None:
    """Record the connection of the folder for orders of the settings, from now, when it has none yet, and ask Microsoft nothing: the
    runner calls it at a pass whose sign-in has lapsed, as the main folder records its own connection before it asks for the token, so
    that what comes into the folder from that pass on is read once the sign-in works again. Nothing without a folder for orders
    (orders_on). An OSError of the aid's own folders is raised."""
    if orders_on(settings):
        _connection(Path(settings.cases_root), settings, now, ORDERS)


def forget_orders(cases_root: Path, used: bool = False) -> None:
    """Remove the stored connection of the folder for orders, its state and its marks, and with `used` the used marks as well, so that
    a folder for orders connected later starts from its own moment and nothing of the folder is kept."""
    for name in (ORDERS_STATE_FILE, ORDERS_KNOWN_FILE, *((USED_FILE,) if used else ())):
        (Path(cases_root) / name).unlink(missing_ok=True)


def message_mark(content: bytes) -> str | None:
    """The mark of an e-mail's Message-ID, by which the two folders know a message they both have: the first 12 hex digits of the
    sha256 of the header's value without its outer spaces; None for an e-mail without one and for one whose headers cannot be read.
    The Message-ID itself is never kept, since it holds a host or a domain."""
    try:
        return _mark_of(BytesHeaderParser(policy=policy.default).parsebytes(content))
    except Exception:                                # headers the parser cannot take: no mark
        return None


class _Used:
    """The used marks of USED_FILE, read when a sync starts (see "The folder for orders" in the module docstring): the mark of the
    Message-ID of each message a folder used, with "main" or "orders", in the order the marks came, the newest USED_CAP kept."""

    def __init__(self, root: Path):
        self.path = Path(root) / USED_FILE
        self.marks = _read_used(self.path)
        self.unsaved = False                          # a record that a write did not save yet

    def folder_of(self, mark: str) -> str | None:
        """The folder that used the message of this mark, or None."""
        return self.marks.get(mark)

    def record(self, mark: str | None, folder: str) -> None:
        """Record the mark with the folder that used its message, unless the mark is recorded already, and write the file at once.
        An OSError of the write is raised; the record stays in memory, so that the next write holds it."""
        if mark is None or mark in self.marks:
            return
        self.marks[mark] = folder
        for oldest in list(self.marks)[:max(len(self.marks) - USED_CAP, 0)]:
            del self.marks[oldest]
        self.unsaved = True
        self.write()

    def record_quietly(self, mark: str | None, folder: str) -> None:
        """record for the main folder, whose reading the used marks never change: a write that fails is passed over."""
        try:
            self.record(mark, folder)
        except OSError:
            pass

    def write(self) -> None:
        _write_json(self.path, self.marks)
        self.unsaved = False

    def write_quietly(self) -> None:
        try:
            self.write()
        except OSError:
            pass


def _read_used(path: Path) -> dict[str, str]:
    """The used marks of the file, or none when it is missing or cannot be read."""
    try:
        marks = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(marks, dict):
        return {}
    return {mark: folder for mark, folder in marks.items() if isinstance(mark, str) and folder in USED_BY}


def _mark_of(mail) -> str | None:
    """The mark of the Message-ID of a parsed e-mail (message_mark); None without one."""
    identity = str(mail["Message-ID"] or "").strip()
    return hashlib.sha256(identity.encode()).hexdigest()[:12] if identity else None


def _file_mark(path: Path) -> str | None:
    """The mark of the Message-ID of an e-mail file of the watched folder; None for a file that cannot be read."""
    try:
        return message_mark(path.read_bytes())
    except OSError:
        return None


def _aids_own(mail, content: bytes, domains: set[str]) -> bool:
    """Whether an e-mail of the folder for orders is one the aid sent itself, whatever it carries: it has the header X-Aid-Template or
    X-Aid-Case, which every message of the aid carries, or its subject begins with the digest's prefix, or _own holds (Auto-Submitted
    with the digest's subject or the aid's own Message-ID at a domain the aid sends from)."""
    return mail["X-Aid-Template"] is not None or mail["X-Aid-Case"] is not None \
        or str(mail["Subject"] or "").startswith(DIGEST_PREFIX) or _own(content, domains)


def _reply_or_forward(mail) -> bool:
    """Whether an e-mail is a reply or a forward: its In-Reply-To or References header is not empty, or its subject begins with a reply
    or forward prefix (REPLY_OR_FORWARD of aid/messaging), which Outlook's forward carries without an In-Reply-To."""
    return bool(str(mail["In-Reply-To"] or "").strip() or str(mail["References"] or "").strip()) \
        or REPLY_OR_FORWARD.match(str(mail["Subject"] or "")) is not None


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


def _connection(root: Path, settings: Settings, now: datetime, folder: _Folder = MAIN) -> tuple[dict, set[str]]:
    """The state of the connection to the folder of the settings (the main folder unless another is given) and the marks it knows:
    the files', or a new connection from now, written at once so that its time is kept whatever the sync then meets."""
    state = connection_of(settings, folder)
    if state is not None:
        known = _read_known(root, folder.known_file)
        if known is None:                            # the marks are lost: a new baseline knows the folder again
            state["delta_link"], known = None, set()
        return state, known
    state = {"mailbox": settings.mailbox, "folder": getattr(settings, folder.setting), "connected_at": _stamp(now), "delta_link": None,
             "last_sync": None, "saved": 0}
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / folder.known_file, [])
    _write_json(root / folder.state_file, state)
    return state, set()


def _read_known(root: Path, name: str = KNOWN_FILE) -> set[str] | None:
    """The marks of the known messages of a folder (of the main folder unless the name of another file is given), or None when their
    file is missing or cannot be read."""
    try:
        marks = json.loads((root / name).read_text(encoding="utf-8"))
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
