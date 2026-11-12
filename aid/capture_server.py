"""The capture server: the passive pre-check of a webshop cart. The browser extension (aid/extension) sends the cart page the buyer
has open, and the server answers at once whether the cart agrees with its purchase order, before the buyer presses order.

    python3 -m aid.capture_server --settings <file> [--host 127.0.0.1] [--port 7870] [--testshop]

The routes:
- POST /capture takes the JSON {"url": ..., "html": ...} with the header X-Aid-Token equal to settings.capture_token, and answers
  200 with a CaptureAnswer as JSON. A missing or wrong token is 401, a body above MAX_BODY bytes 413 (refused on its length, before
  it is read), a body that is not a JSON object with the two string fields 400, and a page without a cart table 400 with the
  reader's reason (the log line names the kept copy of the page). A Content-Length with more digits than MAX_BODY is 413 too, and
  JSON nested too deeply 400. A try-out cart page (aid/tryout.py) sent while try-outs are not offered is 403, and its copy is
  removed: the answer's reason, which the bar shows, is a sentence in the settings' language ("Dit is een proefwinkelwagen: de
  assistent controleert hem alleen in de proefstand."), and the log line keeps the scan's reason, "try-out document outside the test
  mode". Another error of the pre-check is 500, written to <cases_root>/capture.log with its traceback and answered without its
  details; the saved page is then removed unless a case refers to it.
- GET /health answers 200 {"ok": true}.
- GET /testshop/<name>.html serves a made-up cart page of aid/madeup/out, only when the server was started with --testshop, and only
  a file of that folder whose name has no path parts; else 404, also for a name the file system refuses.
- GET /tryout/<name>.html serves a try-out cart of <cases_root>/tryout (write_cart of aid/tryout.py), with or without --testshop:
  an .html file of that folder whose name, percent-decoded, has no path parts, and only while try-outs are offered (offers_tryouts
  of aid/tryout_mark.py: test_mode true and no authorization limit set), by the settings file as it is now; else 404, also for a
  folder of such a name and for a name the file system refuses. The route answers this PC's own host only: a request with exactly
  one Host header, 127.0.0.1 or localhost, alone or with any port, as the review page asks of every request (OwnHost of
  aid/review/__main__.py); any other request is 404, so that a page whose domain name points at this PC is served no order's lines.
- OPTIONS answers the browser's preflight with 204. Access-Control-Allow-Origin is echoed on every answer, for an Origin that
  starts with chrome-extension:// or moz-extension:// only.
Every other path is 404. The server listens on 127.0.0.1 unless --host says otherwise. The token is compared with
hmac.compare_digest and is never logged or echoed.

The pre-check of one page (precheck): the page is saved as <cases_root>/captures/<YYYYmmddTHHMMSS>_<host>.html (the url's host
reduced to letters, digits, dots and dashes and cut to 60 characters, else "page"; a lone surrogate saved as "?"). Its earlier cases
are looked for: the cases that hold a file with its sha256, of any day, and, once the cart reader has read the page (a page it
refuses is kept as nocart_<name>, the page that extends the reader), the capture cases of the same day that hold the same cart: their
capture.json holds the same cart digest, the PO reference the page states and, per line, the item code, the quantity, the unit and
the unit price, each in normal form or else as printed (a try-out cart has a mark of its own in it, so that it is never one cart
with a webshop's). A case whose result is failed is no earlier case, and nor is a case that was replaced. A standing case that
holds the page itself answers at once: the page is not even read, and no later case of the same cart is looked at. Otherwise, of the
cases of the page and of the cart together, the one opened last decides, by the time in its id:
- A case that stands answers from its saved result, nothing new is opened and the new copy is removed, so that a reload, also of a
  page whose invisible parts changed, opens no new case. A case stands when its PO was found (below), and when it is no case of
  the capture server.
- For a capture case without a PO, and for one whose PO was a guess, whatever the buyer did on it, the rule of the cart's order is
  asked again (_asks_again), and the case answers only while the order found now is its own (_found_anew): a case without a PO
  while no order is found, a guess while no order is found or its own order is still the guess. A guess that answers so gives the
  first line of the kind the rule gives now ("not sure" or "already sent"), not of the kind saved with the case, and nothing of
  the case is written. When another order is found or guessed now, or the guess's own order is found for sure now, as when the
  export of the cart's own order was filed meanwhile, a new case is opened, no earlier than one second after the earlier one, and
  run. Then each earlier case that may be replaced (_replaceable) is finished as replaced (_replace): a guess on which nothing
  was done, and a case without a PO while its result is in review, whatever was decided in it. A guess on which something was
  done is never replaced and never changed: it stays on the review page as the buyer left it, and the new case answers from then
  on. Something was done on a case (_acted) when it holds a decision of the buyer, a line in its write log, a message sent, an
  undo, or a close that closed it or carried something out; a press of the close button that left the case in review and carried
  nothing out is no act. A case without a PO that the buyer closed is left exactly as it is.
- When a new case with a PO is opened, the cases of the same lines go with them (_same_lines): capture.json also keeps the lines
  digest, the cart digest without the PO reference the page states, and every capture case of any day that holds the same lines
  digest and the same host, the webshop of the page, may still be replaced, did not fail and was not opened after the new case is
  finished as replaced too. So the guess of a cart before the buyer typed the order's number into it, the case without a PO of
  that cart, and yesterday's untouched guess of the same cart leave no stale case behind. A cart of the same lines at another
  webshop is another cart, and a cart table without a line is matched by no lines digest, since every empty table has the same
  one. A capture.json without a host is matched by its lines digest alone, and one written before the lines digest was kept by
  its cart digest. The search is one pass over the capture.json files of the cases root, made only when a new case with a PO is
  opened.
- A replacement has two steps. First the mark: the earlier case's capture.json gets replaced_by with the new case's id. From the
  mark on the case answers no capture, the runner names it in no digest (aid/runner.py), and the review page does not offer it and
  refuses a decision, a close and an undo on it (marked_replaced of aid/review/app.py). Then the case is read again: when the
  buyer acted on it right before the mark, the mark is taken back and the case is left as it is; else finish_replaced of
  aid/review/close.py closes its result and records the replacement. The review page is another process, and the two share no
  lock, so each side looks twice: decide looks at the mark again after it appended its line and takes the line out when the case
  was marked meanwhile, and the close looks again right before it writes its result. A new case whose own run failed replaces
  nothing, and an earlier case whose capture.json cannot be read is left as it is. A replacement cut off before the mark is made
  when a capture meets the earlier case again. One that was cut off at any point after the mark is finished, never undone: the
  case stays marked, off the review page and out of the digests, until the next reading finishes it (_was_replaced), read again
  first in the same way: when a capture meets it (the same cart in a page of other bytes that day, the earlier case's own page, or
  a new case with a PO of the same lines, _same_lines), and at the latest when the server starts (finish_marked). That reading
  also finishes a case whose closed result was written while its record was not (a case without a PO whose result is closed while
  no close of the buyer closed it), and finishes again a case whose result a close of the review page wrote over in the instant of
  the mark. The page of the capture that was cut off does not finish it when that page came in other bytes than the earlier case's
  own page: the new case holds those bytes and answers at once.
The PO of a cart (find_po; cart_order of aid/sources/capture.py holds the rule and its words): the PO number the page states, when
the store knows that PO as it stands (from a PO PDF or from an export), whatever items it shares with the cart; a number the store
does not know decides nothing. Otherwise the items decide. The server is sure of an order by the items when exactly one stored PO
that was filed on the day of the capture holds exactly the cart's items, and that PO is not sent (the store has no PDF version of
it, and no supplier's confirmation of it came in): that order is found, capture.json holds how (po_found_by "cart items") and the
summary says so behind its number. Any other PO that fits, one that shares two item codes with the cart or holds exactly its
items, is a guess, of which case.json holds how (po_found_by), so that the pipeline prepares nothing and every difference is the
buyer's (its rule G01, aid/pipeline.py): "cart items of an order already sent" when the order the cart is compared with is sent,
else "cart items, not sure". A cart that no stored PO fits opens its case with an unknown PO. A try-out cart page (aid/tryout.py;
is_tryout of aid/tryout_mark.py, asked of the saved page) is taken only while try-outs are offered, as the scan of the watched
folder takes a try-out document: otherwise it is refused (TryoutOutside) before
anything else is done with it, also when an earlier case holds it. The case is opened with
open_capture_case (a case of the same PO in the same second is opened a second later, up to five times, without waiting),
capture.json is written into its folder (the page's sha256, the cart digest, the lines digest, the url's host, the saved page's
path and, when the order was found or guessed by the cart's items, how; the pipeline ignores it), and the case is run with
run_case over the
Deps the server was given and its dispositions recorded. main builds them
with the runner's build_deps for the reader "parser", whatever the settings' reader says: the cart reader reads the page and no model
is asked, not even Copilot with reader "llm", so that the pre-check stays fast and what the rules cannot settle goes to the buyer.
The server knows its settings file: before each pre-check, when the file's modification time changed, it reads the file again and
builds its Deps again as main built them (precheck_deps), and uses the new settings, the token included, from then on, so that the
pre-check never works with other thresholds than the runner's post-check once the buyer changed them on the settings tab of the
review page. A changed file that does not validate keeps the settings in use and is one line in capture.log that names the fields,
never a value. While it runs, and while the server resumes a case it found without a result (_result), the case folder holds .running
(the process id and the start), which keeps the runner's passes off the case (aid/runner.py; the runner reports a capture case in its
digest once it has its result). The server holds no reading, comparing or routing logic of its own.

The answer (CaptureAnswer). Its lines are one entry per flag, match and uncompared item of the findings, and one entry of kind
"review" without a line for each reason about the whole case: a review item without a finding (an unknown PO total, a run cut off
after writes) and a reason the routing spreads over every finding (CASE_LEVEL_REASONS: the first cases of a supplier, a supplier
marked for review, no version selected), each once. The pipeline's reason of a cart whose order was a guess is such a reason too:
its entry stands first, and its text, in place of the review page's label of that reason, says why the aid is not sure, which
order the cart was compared with, what to do when the cart is for a new order, and that the assistant prepared nothing (_GUESSES);
the entry stays until the saved result is closed, which the buyer does on the review page, who then checked the order. While it
stands the answer has a title, GUESS_TITLE ("Let op: kijk na of dit de goede inkooporder is", with the English in brackets), which
the extension shows in place of its own title of the verdict; no other answer has one. They are
in the order of the line ids (numeric when they are numbers; the review entries first). After them, under a match and under
differences, stands one entry that is the same for every cart: what a cart check does not compare ("Niet vergeleken: levertijd,
minimale afname of verpakkingseenheid, verzendkosten en leveringsvoorwaarden."), of the kind "flag" without a line, which every
script of the extension lists, also one that a browser loaded before the entry existed. The reason of a flag is the comparison's
detail, or the reason of its review item when the routing gave it one other
than the generic "cart: for the buyer" or a case-level reason, through the review page's labels; a reason without a label is the
fixed sentence "zie de zaak voor de reden" ("see the case for the reason"), never the raw reason. A PO line not in the cart always
says so, and an extra line too. A flag whose PO line the aid wrote or prepared, in any status, says what the aid did and why (an R10
action's reason read as that of its base rule). A flag the buyer decided on the review page says what the buyer decided. The item
code is the PO line's, in the version the case was checked against as its own step 01 holds it (else in the PO as it stands now),
else the cart line's; the text is the whole line as the banner shows it, in the settings' language. The
verdict is "failed" for a failed run, "no_po" for an unknown PO, "match" for a closed case whose every flag is settled as it stands
now: by the buyer's latest decision when there is one (a "confirm" with a write on its line and field made as the review page counts
it, aid.review.close._made: done or prepared, not failed or prepared after the write route failed, not undone, and a change that
asked the buyer for a look in Exact only once he confirmed its item and a close carried that out; a "keep" leaves it standing), else by such a
write of the rules (a case with review items counts as closed by the buyer only with a readable close record of the review page),
and "differences" otherwise; a line whose write was undone reads as a difference with "wijziging ongedaan gemaakt", and a line the
buyer confirmed gives the buyer's decision as its reason; the message sums it up in one sentence, a flag the aid wrote or prepared
not counted as a difference. The message of a cart whose order was found for sure by its items names the order with "(gekozen op
de artikelen in de winkelwagen)" behind its number, so that the buyer sees that the number was not read from the page, and that of
a cart without a PO says what to do: give the export of the order lines from Exact first, and reload the page after a minute. A write
is undone when its undo was made in a system, or when the undo's row on the list of changes to
enter in Exact (<cases_root>/prepared, aid/execution/by_hand.py) is entered or seen, held by the marks, or cancelled together with
the write's own row before the write was entered; while that row is open on the list (a put-back of the tab "In Exact invoeren"),
the write stands and its line says so: "terugzetten staat op de lijst In Exact invoeren". An undo that the list passed over leaves
the write standing. A prepared write is judged by the list as well, whatever case's write log holds the put-back: a put-back linked to
an entry of the change that holds its row, or, for a row that a later row replaced while Exact held its value, the put-back that took
that value out of Exact (the put-back that replaced the row, one of that same change made again, or that of the change whose value
the row asked again), undoes it once entered or seen and waits while open, so that the same cart checked again on another day is
told of the put-back too; a put-back made after another value was entered, or of a change of another value, never judges it. A
prepared write counts as made only while its change stands on the list: once a later case took the change off because its document
named the value Exact holds again (the mark "replaced" by a case), the line is a difference again, with "de lijst In Exact invoeren
zegt nu iets anders". While
the list cannot be read, no prepared write of a kind the list holds counts as made, whether the case has an undo of its own or not:
the line shows the difference between the cart and the order, with "de lijst In Exact invoeren kon niet worden gelezen", and the
banner is amber until the list can be read again, whatever case answers. A match that rested on a value entered in Exact (the case
holds steps/03_entered.json: the cart agreed with the order as the marks of that list said Exact held it, aid/pipeline.py) is
judged by the list as it is now, each time the case answers (_rested): while the marks still say Exact holds that value the match
stands; while a put-back of it waits on the list its line says so, as a flag; once a put-back gives the line its value from
before, the line is a difference again, "wijziging ongedaan gemaakt"; once the tick was taken back or ended, or another value was
entered for the line, it is a difference with "de lijst In Exact invoeren zegt nu iets anders"; and while the list cannot be read
it is a difference with the sentence of that case. A tick that the PO as it stands, given after it, contradicts counts as ended in
this judgement, by the list's own rule and in reading only: the answer of a stored case writes no mark, and does not wait for the
runner's pass to end the tick. A stored match is judged whole: a value that matched as the PO version printed it is a difference
with the same words once the list says Exact holds another value for its line; and while the
list cannot be read, a case with such a match is amber with the sentence of the unread list as a reason about the whole case. So
no stored case answers green where a new check of the same cart would not. A case
that ran while the list could not be read (the pipeline's rule L01) is in review: its reason is shown once, as a case-level reason,
in the words of the unread list.

One line per capture goes to stderr: the time, the host of the url, the case id and the verdict. The pre-checks run one at a time,
so that two captures sent together never open the same case twice. A handler thread waits at most TIMEOUT seconds for a client; a
client that goes away, the base handler's own errors (a client that timed out, a request line or a method it refuses) and any
other error outside the pre-check are one line each in capture.log, never a traceback on stderr. On Windows the port is bound
without SO_REUSEADDR, so that a second server on a port in use ends with exit code 2 there too.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel

from aid.case import Case, _write_json, find_cases_by_hash, load_case, sha256_file     # _write_json: capture.json whole or not at all
from aid.comparison import Findings, Flag
from aid.execution.by_hand import FOLDER as PREPARED, KINDS as LIST_KINDS, changes, held_now, place_of, same_value
from aid.execution.writer import LOG_FILE as WRITE_LOG_FILE, WriteLog, WriteLogEntry
from aid.messaging import MESSAGES, SENT_FILE
from aid.notify import review_link
from aid.pipeline import GUESSED_CART, LOOKS, RESULT_FILE, UNREAD_LIST, CaseResult, Deps, _waits_for_the_buyer, record_dispositions, run_case
from aid.po_store import PoStore, PoVersion
from aid.readers.cart import read_cart_html
from aid.readers.llm_client import ReaderError
# The review page's own readers of decisions.jsonl, of the version a case selected in its step 01, and of steps/09_close.json, so that
# no file name is copied (loaded with aid.review), and its finish of a case that another case replaces.
from aid.review.app import DECISIONS_FILE, _decisions as _review_decisions, _selected_version
from aid.review.close import BUYER_RULE, REPLACED, _made, _records as _close_records, _target, _written, finish_replaced, replaced
from aid.review.i18n import STRINGS, reason_label
from aid.routing import NO_VERSION, findings_by_id
from aid.runner import RUNNING_FILE, build_deps
from aid.schema import DocValue, Document
from aid.settings import Settings, load_settings
from aid.sources.capture import ALREADY_SENT, BY_ITEMS, GUESSES, NOT_SURE, cart_order, open_capture_case
from aid.tryout import CART_FOLDER
from aid.tryout_mark import TRYOUT_OUTSIDE, is_tryout, offers_tryouts, tryout_case

CAPTURES = "captures"                            # the folder under cases_root that holds the captured pages
CAPTURE_FILE = "capture.json"                    # what the server knows of a capture case, in its folder; the pipeline ignores it
NO_CART_PREFIX = "nocart_"                       # the prefix of a kept page that the reader refused
LOG_FILE = "capture.log"                         # the server's errors, under cases_root
MAX_BODY = 5 * 1024 * 1024                       # the largest body POST /capture reads, in bytes
TOKEN_HEADER = "X-Aid-Token"
DEFAULT_PORT = 7870
TIMEOUT = 60                                     # seconds a handler thread waits for a client
OPEN_ATTEMPTS = 6                                # the case opened at the clock's time, else up to five seconds later
HOST_LENGTH = 60                                 # the longest host in a file name
TESTSHOP = Path(__file__).resolve().parent / "madeup" / "out"        # the made-up cart pages the test shop serves
TRYOUT_SHOP = f"/{CART_FOLDER}/"                 # the path under which the try-out carts of <cases_root>/tryout are served
TRYOUT_MARK = "try-out cart"                     # what the cart digest of a try-out cart holds after its lines
EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://")
GENERIC_CART_REASON = "cart: for the buyer"      # the routing's reason for every question on a cart; the comparison's detail says more
_HOST_CHARACTERS = re.compile(r"[^A-Za-z0-9.-]")
_OWN_HOST = re.compile(r"(?:127\.0\.0\.1|localhost)(?::[0-9]+)?")      # this PC by its own names, alone or with a port

Verdict = Literal["match", "differences", "no_po", "failed"]


class CaptureAnswer(BaseModel):
    """What the extension shows: the case, its status, the verdict, one entry per finding (see _lines) and after them, under a
    match and under differences, the entry that says what a cart check does not compare (_not_compared), the link to the case on
    the review page and one sentence that sums the verdict up in the settings' language. `title`, when there is one, is shown in
    place of the extension's own title of the verdict: only a guess that still waits has one (GUESS_TITLE), and the JSON answer
    of every other case holds no title at all."""
    case_id: str
    po_number: str | None
    status: str
    verdict: Verdict
    lines: list[dict]
    review_url: str
    message: str
    title: str | None = None

    def as_json(self) -> dict:
        """The answer as the server sends it: without the title when it has none."""
        data = self.model_dump(mode="json")
        return data if self.title is not None else {name: value for name, value in data.items() if name != "title"}


class NoCartTable(ValueError):
    """The page holds no cart table: the reader's reason, answered with 400, and the kept copy of the page, named in the log."""

    def __init__(self, reason: str, kept: Path):
        super().__init__(reason)
        self.kept = kept


class TryoutOutside(ValueError):
    """A try-out cart page (aid/tryout.py) sent while try-outs are not offered: refused with 403. Its text is the reason for which
    the scan of the watched folder skips a try-out document, TRYOUT_OUTSIDE of aid/tryout_mark.py, which the log keeps; the answer
    carries a sentence for the bar in the settings' language (_TRYOUT_OUTSIDE)."""

    def __init__(self):
        super().__init__(TRYOUT_OUTSIDE)


def precheck(html: str, url: str, deps: Deps, settings: Settings) -> CaptureAnswer:
    """The whole pre-check of one captured page (see the module docstring): save, look for the earlier cases of the page or the cart,
    read, find the PO, open the case, run it, finish the cases it replaces (the earlier ones that may still be replaced, and for a
    new case with a PO the cases of the same lines of any day that may), answer. Raises NoCartTable for a page without a cart
    table; its saved copy is then kept as nocart_<name>. Raises TryoutOutside for a try-out cart page while try-outs are not
    offered. Any other exception before a case refers to the saved copy removes it, so that a failing pre-check leaves no page
    behind per page load."""
    cases_root = Path(settings.cases_root)
    now = deps.clock()
    page = _save(cases_root, html, url, now)
    case = cart = content = lines = how = None
    try:
        tryout = is_tryout(page)                 # a try-out cart of the aid's own making: taken only while try-outs are offered
        if tryout and not offers_tryouts(settings):
            raise TryoutOutside()
        digest = sha256_file(page)
        earlier = _live(find_cases_by_hash(cases_root, digest), deps, now, settings.language)
        if not earlier or _asks_again(earlier[0][0]):                  # else the same page was checked before, and its case stands
            try:
                cart = read_cart_html(page)
            except ReaderError as error:
                kept = page.with_name(NO_CART_PREFIX + page.name)
                page.replace(kept)
                raise NoCartTable(str(error), kept) from None
            content, lines = cart_digest(cart, tryout), lines_digest(cart, tryout)
            held = {found.case_id for found, _ in earlier}
            earlier = sorted(earlier + _same_cart(cases_root, content, now, deps, settings.language, held),
                             key=lambda found: _opened(found[0]), reverse=True)
            if not earlier or _asks_again(earlier[0][0]):
                number, how = cart_order(cart, deps.store, cases_root, now)
                if not earlier or _found_anew(earlier[0][0], number, how):      # no case yet, or another order than the earlier one's
                    case = _open_case(cases_root, page, number, _not_before(now, earlier), how if how in GUESSES else None)
                elif number is None:
                    how = None                   # no order found now: the earlier case answers as it was saved
    except NoCartTable:
        raise
    except BaseException:
        if case is None:
            page.unlink(missing_ok=True)
        raise
    opened = case is not None
    if opened:
        meta = {"sha256": digest, "cart_digest": content, "lines_digest": lines, "host": page_host(url), "page": str(page)}
        meta |= {"po_found_by": how} if how else {}
        result = _run(case, deps, lambda: _write_json(case.root / CAPTURE_FILE, meta))
    else:
        page.unlink()                            # an earlier case of the page or of the cart answers: nothing new is opened
        case, result = earlier[0]
        cart = cart if cart is not None else _cart(case.input_files[0])
        still = how if how in GUESSES else None  # a guess whose own order is still the guess: the kind the rule gives now
    if case.po_number is not None and result.status != "failed":       # a case with a PO that can answer replaces the open ones before it
        held = {old.case_id for old, _ in earlier} | {case.case_id}
        older = [old for old, _ in earlier if _opened(old) < _opened(case)]
        if opened:                               # and, when it is new, the cases of the same lines of any day, not opened after it
            same = lines if cart.lines else None  # a cart table without a line is matched by no lines digest
            older += [old for old in _same_lines(cases_root, same, content, page_host(url), held, now, settings.language)
                      if _opened(old)[0] <= _opened(case)[0]]
        for old in older:
            if _replaceable(old):                # read again: what the buyer did meanwhile stands
                _replace(old, case.case_id, now, settings.language)
    return _answer(case, result, cart, deps, settings, None if opened else still)


def _save(cases_root: Path, html: str, url: str, now: datetime) -> Path:
    """The page saved as <cases_root>/captures/<time>_<host>.html, with a number after it when a page of that host was saved in the
    same second. A lone surrogate in the text is saved as "?"."""
    folder = cases_root / CAPTURES
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{now:%Y%m%dT%H%M%S}_{page_host(url)}"
    path, number = folder / f"{stem}.html", 1
    while path.exists() or path.with_name(NO_CART_PREFIX + path.name).exists():
        number += 1
        path = folder / f"{stem}_{number}.html"
    path.write_bytes(html.encode("utf-8", errors="replace"))
    return path


def page_host(url: str) -> str:
    """The host of the url, reduced to letters, digits, dots and dashes and cut to HOST_LENGTH characters, for a file name; "page"
    when nothing is left."""
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    host = _HOST_CHARACTERS.sub("", host)[:HOST_LENGTH].strip(".")
    return host or "page"


def cart_digest(cart: Document, tryout: bool = False) -> str:
    """The sha256 of the cart as the reader normalises it: the PO reference the page states, then per line the item code, the
    quantity, the unit and the unit price, each its normal form or else its printed text. The cart of a try-out cart page (`tryout`:
    is_tryout of aid/tryout_mark.py says so of the page) has a mark of its own after them, so that a try-out cart and a webshop's
    cart with the same lines are never one cart: neither is answered from the other's case."""
    return _digest([_shown_value(cart.po_reference)] + _cart_lines(cart, tryout))


def lines_digest(cart: Document, tryout: bool = False) -> str:
    """The sha256 of what cart_digest holds without the PO reference the page states: the lines, and the mark of a try-out cart.
    The same cart typed with its order's number, or met on another day, has the same lines digest, by which the cases that a new
    case replaces are found (_same_lines)."""
    return _digest(_cart_lines(cart, tryout))


def _cart_lines(cart: Document, tryout: bool) -> list:
    return [[_shown_value(line.item_code), _shown_value(line.quantity), _shown_value(line.unit), _shown_value(line.unit_price)]
            for line in cart.lines] + ([TRYOUT_MARK] if tryout else [])


def _shown_value(value: DocValue) -> str | None:
    return value.value if value.value is not None else value.text


def _digest(content: list) -> str:
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode("utf-8")).hexdigest()


def _same_cart(cases_root: Path, content: str, now: datetime, deps: Deps, language: str,
               held: set[str]) -> list[tuple[Case, CaseResult]]:
    """The capture cases of the day of `now` whose capture.json holds the cart digest `content`, but those of the ids `held`, each
    with its result, as _live gives them."""
    day, cases = f"{now:%Y%m%d}", []
    for meta_path in sorted(cases_root.glob(f"*_{day}T*/{CAPTURE_FILE}")):
        if meta_path.parent.name in held:
            continue
        try:
            if json.loads(meta_path.read_text(encoding="utf-8")).get("cart_digest") != content:
                continue
            cases.append(load_case(cases_root, meta_path.parent.name))
        except (OSError, ValueError, AttributeError):
            continue
    return _live(cases, deps, now, language)


def _live(cases, deps: Deps, now: datetime, language: str) -> list[tuple[Case, CaseResult]]:
    """Of the cases, those that can answer a capture, each with its result, the one opened last first: no case that was replaced
    (_was_replaced), and no case whose result is failed. A case without a result (a run that was cut off) is run now."""
    live = []
    for case in sorted(cases, key=_opened, reverse=True):
        if not _was_replaced(case, now, language) and (result := _result(case, deps)).status != "failed":
            live.append((case, result))
    return live


def _opened(case: Case) -> tuple[str, str]:
    """The time in the id of the case, then the id: the order in which the cases of one page or cart were opened."""
    return case.case_id.rpartition("_")[2], case.case_id


def _not_before(now: datetime, earlier: list[tuple[Case, CaseResult]]) -> datetime:
    """When a new case of a page or cart is opened that has the cases `earlier`: at `now`, but no earlier than one second after the
    newest of them, by the times in their ids, so that the new case is the one opened last also when two captures fall in one second
    (ruling N23 of Task 36, for the capture server)."""
    newest = max((_opened(case)[0] for case, _ in earlier), default="")
    if f"{now:%Y%m%dT%H%M%S}" > newest:
        return now
    try:
        return datetime.strptime(newest, "%Y%m%dT%H%M%S").replace(tzinfo=now.tzinfo) + timedelta(seconds=1)
    except ValueError:
        return now


def _meta(case: Case) -> dict:
    """What the case's capture.json holds; nothing for a case that has none, or whose file cannot be read as an object."""
    try:
        meta = json.loads((case.root / CAPTURE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _asks_again(case: Case) -> bool:
    """Whether the rule of the cart's order is asked again when the page or the cart of the case comes, so that the case answers
    only while the order found then is its own (_found_anew): a case of the capture server (its folder holds capture.json) without
    a PO, or whose PO was a guess (case.json's po_found_by), whatever the buyer did on it. A case whose PO was found for sure, and
    a case that is no case of the capture server, stand and answer."""
    return (case.root / CAPTURE_FILE).is_file() and (case.po_number is None or case.po_found_by is not None)


def _pending(case: Case) -> bool:
    """Whether nothing stands in the way of replacing the case by a later one of the same cart: a case that asks again
    (_asks_again) without a PO, whatever was decided in it, or one whose PO was a guess and on which the buyer did nothing
    (_acted). A guess the buyer acted on is never replaced, and a case whose files cannot be read is left as it is."""
    if not _asks_again(case):
        return False
    if case.po_number is None:
        return True
    try:
        return not _acted(case)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def _acted(case: Case) -> bool:
    """Whether something was done on the case after its run: it holds a decision of the buyer, a line in its write log, a message
    sent, an undo, or a close that closed it or carried something out. A press of the close button that left the case in review
    and carried nothing out (no item decided) is no act: its record holds the status review, no item carried out, no write and no
    message. Raises what reading the case's files raises."""
    if _written(case.root / WRITE_LOG_FILE) or _written(case.root / DECISIONS_FILE) or (case.root / MESSAGES / SENT_FILE).exists():
        return True
    return any(record.get("action") != "close" or record.get("status") != "review" or record.get("carried_out")
               or record.get("write_entries") or record.get("messages") for record in _close_records(case))


def _found_anew(latest: Case, number: str | None, how: str | None) -> bool:
    """Whether the order found now, `number` and how it was found, is another than the one the case `latest` answers for, a case
    for which the rule is asked again (_asks_again), so that a new case is opened: for a case without a PO, any order, found or
    guessed; for a case whose PO was a guess, another order, found or guessed, or its own order found for sure now (by its number
    or as BY_ITEMS). While no order is found now, and for a guess while its own order is still the guess, the earlier case
    answers."""
    return number is not None and (number != latest.po_number or how not in GUESSES)


def _replaceable(case: Case) -> bool:
    """Whether the case, read now, may be finished as replaced by a case with a PO: a pending case (_pending), and for one without
    a PO only while its saved result is in review. A case without a PO that the buyer closed is left exactly as it is, and so is a
    guess on which the buyer did something, and a case whose files cannot be read."""
    if not _pending(case):
        return False
    if case.po_number is not None:
        return True
    try:
        result = json.loads((case.root / "steps" / RESULT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(result, dict) and result.get("status") == "review"


def _same_lines(cases_root: Path, lines: str | None, content: str, host: str, held: set[str], now: datetime,
                language: str) -> list[Case]:
    """The capture cases of any day that hold the same lines as the cart at the same webshop, whatever number their page states:
    their capture.json holds the lines digest `lines` and the host `host` (one that holds no host is matched by the digest alone),
    or, written before that digest was kept, no lines digest and the cart digest `content`. `lines` is None for a cart without a
    line: an empty cart table is never matched by its lines digest. But those of the ids `held`, those that were replaced
    (_was_replaced), and those that could not answer a capture: a case whose saved result is failed, which is never reused and
    keeps its result, and one that has no result that can be read. One pass over the capture.json files of the cases root, made
    only when a new case with a PO is opened for the cart (precheck), which then replaces those of them that may still be
    replaced."""
    cases = []
    for meta_path in sorted(cases_root.glob(f"*/{CAPTURE_FILE}")):
        if meta_path.parent.name in held:
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if "lines_digest" not in meta:
                if meta.get("cart_digest") != content:
                    continue
            elif lines is None or meta["lines_digest"] != lines or meta.get("host", host) != host:
                continue
            case = load_case(cases_root, meta_path.parent.name)
        except (OSError, ValueError, AttributeError, TypeError):
            continue
        if not _was_replaced(case, now, language) and _saved_status(case) not in (None, "failed"):
            cases.append(case)
    return cases


def _saved_status(case: Case) -> str | None:
    """The status of the saved result of the case; None without a result that can be read."""
    try:
        result = json.loads((case.root / "steps" / RESULT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return result.get("status") if isinstance(result, dict) else None


def _was_replaced(case: Case, now: datetime, language: str) -> bool:
    """Whether the case was replaced by another: its capture.json holds replaced_by, or its close records hold a replacement
    (replaced of aid/review/close.py). A marked case is read again, and a replacement that was cut off at any point after the mark
    is finished now, at `now` and in `language`: one whose result was never finished, one cut off between the closed result and
    its record (_half_finished), and one whose finish a close of the review page wrote over in the instant of the mark
    (_stands_replaced). When the finish fails the case stays marked and is tried again the next time it is met. Only when the
    buyer acted on the case before the mark (it may no longer be replaced, _replaceable, and no finish was begun) is the mark taken
    back and the case left as it is: it was not replaced."""
    by = _meta(case).get("replaced_by")
    if by is None:
        return replaced(case)
    if replaced(case):
        if _stands_replaced(case):
            return True
    elif not _replaceable(case) and not _half_finished(case):
        return not _unmark(case)
    try:
        finish_replaced(case, by, now, language)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return True


def _saved(case: Case) -> tuple[dict, list]:
    """The saved result of the case and its close records, as they stand in its files. Raises what reading them raises."""
    result = json.loads((case.root / "steps" / RESULT_FILE).read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("the saved result is no object")
    return result, [record for record in _close_records(case) if isinstance(record, dict)]


def _half_finished(case: Case) -> bool:
    """Whether a marked case without a PO is a finish that was cut off between its two writes (finish_replaced writes the closed
    result first and the record second): its result is closed and it holds no record of a close of the buyer that closed it. A
    press of the close button that left the case in review closed nothing, so its record does not count. A case without a PO
    that the buyer closed holds the record of that close, and is left as it is; so is a case whose files cannot be read."""
    if case.po_number is not None:
        return False
    try:
        result, records = _saved(case)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    closed_by_the_buyer = any(record.get("action") == "close" and record.get("status") == "closed" for record in records)
    return result.get("status") == "closed" and not closed_by_the_buyer


def _stands_replaced(case: Case) -> bool:
    """Whether a marked case that holds the record of its replacement still stands as finish_replaced left it: its result is
    closed and the replacement is its last record. A close of the review page that began right before the mark writes its own
    result and record after them (close_case looks at the mark once more before it writes, and the instant after that look is
    left); the case is then finished again. A case whose files cannot be read is left as it is."""
    try:
        result, records = _saved(case)
    except (OSError, ValueError, KeyError, TypeError):
        return True
    return result.get("status") == "closed" and bool(records) and records[-1].get("action") == REPLACED


def _replace(case: Case, by: str, now: datetime, language: str) -> None:
    """Finish the capture case as replaced by the case `by`. First the mark, replaced_by in its capture.json: from that moment the
    case answers no capture, the runner names it in no digest (aid/runner.py; ruling N20 of Task 36 for a capture case), and the
    review page offers it no more and refuses a decision, a close and an undo on it (marked_replaced of aid/review/app.py). Then
    the case is read again: when the buyer acted on it before the mark, so that it may no longer be replaced (_replaceable), the
    mark is taken back and the case is left as it is. Else finish_replaced of the review page's close: its result is closed,
    without a supplier, and the replacement recorded in steps/09_close.json and in the audit record. A case whose capture.json
    cannot be read now is left as it is, and nothing is written over that file: it is tried again the next time the page or the
    cart comes."""
    try:
        meta = json.loads((case.root / CAPTURE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(meta, dict):
        return
    _write_json(case.root / CAPTURE_FILE, meta | {"replaced_by": by})
    if not _replaceable(case):
        _write_json(case.root / CAPTURE_FILE, meta)
        return
    finish_replaced(case, by, now, language)


def _unmark(case: Case) -> bool:
    """Take the mark of a replacement out of the case's capture.json, which keeps everything else; whether the file no longer
    holds it. A file that cannot be read or written keeps its mark."""
    try:
        meta = json.loads((case.root / CAPTURE_FILE).read_text(encoding="utf-8"))
        if isinstance(meta, dict) and "replaced_by" in meta:
            _write_json(case.root / CAPTURE_FILE, {name: value for name, value in meta.items() if name != "replaced_by"})
        return isinstance(meta, dict)
    except (OSError, ValueError):
        return False


def finish_marked(cases_root: Path, now: datetime, language: str) -> None:
    """Finish every capture case under cases_root that its capture.json marks as replaced while its result was never finished as
    such (see _replace), each read again first as _was_replaced reads it: the server does so when it starts, so that a replacement
    cut off between the mark and the finish does not stay unfinished. A capture.json that cannot be read is passed over."""
    for meta_path in sorted(Path(cases_root).glob(f"*/{CAPTURE_FILE}")):
        try:
            marked = json.loads(meta_path.read_text(encoding="utf-8")).get("replaced_by") is not None
            case = load_case(cases_root, meta_path.parent.name) if marked else None
        except (OSError, ValueError, AttributeError):
            continue
        if case is not None:
            _was_replaced(case, now, language)


def _cart(path: Path) -> Document | None:
    """The cart of an earlier case, for the item codes of its extra lines; None when its input is no cart page."""
    try:
        return read_cart_html(path)
    except (ReaderError, OSError):
        return None


def _result(case: Case, deps: Deps) -> CaseResult:
    """The saved result of an earlier case; a case without one (a run that was cut off) is run now, as the runner would."""
    path = case.root / "steps" / RESULT_FILE
    if path.is_file():
        return CaseResult.model_validate_json(path.read_text(encoding="utf-8"))
    return _run(case, deps)


def _run(case: Case, deps: Deps, before=None) -> CaseResult:
    """run_case and record_dispositions on the case, with .running in its folder meanwhile (the process id and the clock's time), so
    that the runner leaves the case alone; `before` is done first, inside the same guard."""
    running = case.root / RUNNING_FILE
    running.write_text(json.dumps({"pid": os.getpid(), "started": deps.clock().isoformat()}) + "\n", encoding="utf-8")
    try:
        if before is not None:
            before()
        result = run_case(case, deps)
        record_dispositions(case, result)
    finally:
        running.unlink(missing_ok=True)
    return result


def find_po(cart: Document, store: PoStore, cases_root: Path, now: datetime) -> str | None:
    """The PO the cart is compared with, for a capture at `now` with the cases under `cases_root`: the number the page states when
    the store knows that PO, else the order chosen among those that hold the cart's items (cart_order of aid/sources/capture.py,
    which also says how it was found, and whether it is a guess); None for a cart of no stored PO."""
    return cart_order(cart, store, cases_root, now)[0]


def _open_case(cases_root: Path, page: Path, po_number: str | None, now: datetime, found_by: str | None = None) -> Case:
    """open_capture_case at `now`, with how the PO was guessed when it was (found_by); a case of the same PO opened in the same
    second is tried a second later, up to five times, without waiting (the case id is the time it names, not the time it was
    opened)."""
    for attempt in range(OPEN_ATTEMPTS):
        try:
            return open_capture_case(cases_root, page, po_number, now + timedelta(seconds=attempt), found_by)
        except FileExistsError:
            if attempt == OPEN_ATTEMPTS - 1:
                raise
    raise AssertionError("unreachable")


# --- the answer ---

_TEXTS = {
    "nl": {"line": "regel {line}", "values": "{field} {doc} in de winkelwagen, {po} op de inkooporder", "no value": "geen waarde",
           "extra": "extra regel, niet op de inkooporder", "uncompared": "{field} niet vergeleken",
           "done": "{field} op de inkooporder bijgewerkt van {old} naar {new}",
           "prepared": "{field} op de inkooporder: wijziging voorbereid van {old} naar {new}, zie de zaak",
           "no label": "zie de zaak voor de reden", "undone": "wijziging ongedaan gemaakt",
           "put_back": "terugzetten staat op de lijst In Exact invoeren",
           "unread": "de lijst In Exact invoeren kon niet worden gelezen",
           "other": "de lijst In Exact invoeren zegt nu iets anders", "still": "{field} {value}",
           "confirm": "besluit van de inkoper: waarde van de winkelwagen overgenomen",
           "keep": "besluit van de inkoper: waarde van de inkooporder gehouden"},
    "en": {"line": "line {line}", "values": "{field} {doc} in the cart, {po} on the purchase order", "no value": "no value",
           "extra": "extra line, not on the purchase order", "uncompared": "{field} not compared",
           "done": "{field} on the purchase order updated from {old} to {new}",
           "prepared": "{field} on the purchase order: change prepared from {old} to {new}, see the case",
           "no label": "see the case for the reason", "undone": "change undone",
           "put_back": "put-back is on the list Enter in Exact",
           "unread": "the list Enter in Exact could not be read",
           "other": "the list Enter in Exact now says otherwise", "still": "{field} {value}",
           "confirm": "the buyer's decision: the cart's value taken",
           "keep": "the buyer's decision: the purchase order's value kept"},
}
# The reasons the routing gives every finding of a case for a cause that concerns the whole case: shown once as a review entry, while
# each line keeps the comparison's detail. The generic reason of a question on a cart is left out altogether. The pipeline's reason of
# a cart whose order was guessed (GUESSED_CART) is such a reason too.
CASE_LEVEL_REASONS = frozenset({"new supplier", "supplier marked for review", NO_VERSION, UNREAD_LIST, GUESSED_CART})
# The line of a review item about the whole case, by its reason; any other reason is shown through the review page's labels.
_CASE_REASONS = {
    "nl": {"new supplier": "eerste zaken van deze leverancier gaan naar de inkoper",
           "supplier marked for review": "zaken van deze leverancier gaan naar de inkoper",
           UNREAD_LIST: _TEXTS["nl"]["unread"]},
    "en": {"new supplier": "the first cases of this supplier go to the buyer",
           "supplier marked for review": "the cases of this supplier go to the buyer",
           UNREAD_LIST: _TEXTS["en"]["unread"]},
}
# The line of a cart whose order was a guess, by how it was guessed (case.json's po_found_by, aid/sources/capture.py): it stands
# first, in place of the label of the reason, and says why the buyer is to check the order, what to do when the cart is for a new
# order, and that nothing was prepared.
_GUESSES = {
    "nl": {NOT_SURE: ("De assistent weet niet zeker welke inkooporder bij deze winkelwagen hoort. Vergeleken met inkooporder {po}: kijk "
                      "na of dit de goede is. Is deze winkelwagen voor een nieuwe inkooporder, geef dan eerst de export van de "
                      "orderregels uit Exact (tabblad Bestanden) en herlaad deze pagina na een minuut. De assistent heeft niets "
                      "voorbereid."),
           ALREADY_SENT: ("De inkooporder die het best bij deze winkelwagen past ({po}) is al verstuurd. Is dit een nieuwe bestelling, "
                          "geef dan eerst de export van de orderregels uit Exact (tabblad Bestanden) en herlaad deze pagina na een "
                          "minuut. De assistent heeft niets voorbereid.")},
    "en": {NOT_SURE: ("The assistant is not sure which purchase order this cart belongs to. Compared with purchase order {po}: check "
                      "that it is the right one. If this cart is for a new purchase order, first give the export of the order lines "
                      "from Exact (tab Files) and reload this page after a minute. The assistant has prepared nothing."),
           ALREADY_SENT: ("The purchase order that fits this cart best ({po}) was sent already. If this is a new order, first give the "
                          "export of the order lines from Exact (tab Files) and reload this page after a minute. The assistant has "
                          "prepared nothing.")},
}
# The last line of the bar under a match and under differences, a guess among them: what a cart check does not compare.
_NOT_COMPARED = {"nl": "Niet vergeleken: levertijd, minimale afname of verpakkingseenheid, verzendkosten en leveringsvoorwaarden.",
                 "en": "Not compared: delivery time, minimum or pack quantity, delivery costs and delivery terms."}
NOT_COMPARED = "not compared"                    # the reason of that entry
# The title of the bar of a guess that still waits, in place of the extension's title of the verdict "differences" ("the cart
# differs from the purchase order", which is not so for a cart equal to the order it was compared with). In both languages, as
# the extension's own titles are.
GUESS_TITLE = "Let op: kijk na of dit de goede inkooporder is (check that this is the right purchase order)"
# What the bar shows for a try-out cart sent while try-outs are not offered (TryoutOutside), in place of the reason in the log.
_TRYOUT_OUTSIDE = {"nl": "Dit is een proefwinkelwagen: de assistent controleert hem alleen in de proefstand.",
                   "en": "This is a try-out cart: the assistant checks it only in the test mode."}
# Why the rules made a change themselves (the reason of an AUTO action begins with one of these, after the "re-approval requested;
# R<n> " of an R10 action), which the review page has no label for because such a change never waits for the buyer.
_AUTO_REASONS = {
    "nl": {"price within threshold": "prijs binnen de drempel",
           "delivery date within the lead time plus the tolerance": "leverdatum binnen de levertijd plus de marge",
           "delivery date earlier than the PO date": "leverdatum eerder dan op de inkooporder"},
    "en": {"price within threshold": "price within the threshold",
           "delivery date within the lead time plus the tolerance": "delivery date within the lead time plus the tolerance",
           "delivery date earlier than the PO date": "delivery date earlier than on the purchase order"},
}
_REAPPROVAL = re.compile(r"re-approval requested; R\d+ ")
_LINE_WRITES = {"price": "po_line_price", "delivery_date": "po_line_date", "quantity": "po_line_quantity"}   # a flag's write by field


def _answer(case: Case, result: CaseResult, cart: Document | None, deps: Deps, settings: Settings,
            still: str | None = None) -> CaptureAnswer:
    """The answer of the case. `still` is the kind of guess the rule gives now for a case whose own order is still the guess (one
    of GUESSES), when it was asked: the first line is then the sentence of that kind, not of the kind saved with the case."""
    po = _version(deps, case, result)
    decisions = _decisions(case, result)
    closes, undone, waiting, unread, left = _history(case, Path(settings.cases_root) / PREPARED)
    writes = _Writes(_log_entries(case, result), undone, waiting, unread, _confirmed_looks(case, result), left)
    rested, blind = _rested(case, result, po, Path(settings.cases_root) / PREPARED, deps.store)
    how = case.po_found_by or _meta(case).get("po_found_by")       # how the order was found when the page did not state its number
    guess = _GUESSES[settings.language].get(still if still is not None and how in GUESSES else how)
    if guess is not None:                        # until the saved result is closed: the buyer closed the case, and checked the order
        guess = "" if result.status == "closed" else guess.format(po=case.po_number)
    lines = _lines(result, po.document if po is not None else None, cart, settings.language, decisions, writes, rested, blind,
                   guess)
    verdict = _verdict(case, result, decisions, closes, writes, rested, blind)
    message = _message(verdict, case, result, lines, settings.language, writes, rested, chosen=how == BY_ITEMS)
    if verdict in ("match", "differences"):      # after the entries of the case, and counted in no sentence
        lines = [*lines, _not_compared(settings.language)]
    waits = bool(guess) and any(entry["kind"] == "review" and entry["text"] == guess for entry in lines)      # the guess's line is shown
    return CaptureAnswer(case_id=case.case_id, po_number=case.po_number, status=result.status, verdict=verdict, lines=lines,
                         review_url=review_link(settings, case.case_id), message=message, title=GUESS_TITLE if waits else None)


def _not_compared(language: str) -> dict:
    """The entry that says what a cart check does not compare, in the settings' language. It is of the kind "flag" and has no
    line: the extension lists the entries of that kind under a match and under differences alike, also in a script that a
    browser loaded before this entry existed, so the line shows without the extension being reloaded."""
    return {"line_id": "", "item_code": None, "field": None, "kind": "flag", "po_value": None, "doc_value": None,
            "reason": NOT_COMPARED, "text": _NOT_COMPARED[language]}


class _Rested(BaseModel):
    """A value of a PO line that a match of a case rested on, judged again by the list of changes to enter in Exact as it is now
    (_rested): a value entered in Exact that the case was compared with (a record of its steps/03_entered.json), or the printed
    value of its PO version."""
    was: str | None                              # the value the PO version itself showed
    how: Literal["held", "waiting", "undone", "other", "unread"]
    now: str | None = None                       # for "other": the value the list says Exact holds now, when it says one


def _rested(case: Case, result: CaseResult, po: PoVersion | None, prepared: Path,
            store: PoStore) -> tuple[dict[tuple[str, str], _Rested], bool]:
    """Every value of a PO line that a match of the case rested on, by (line id, field), judged by the list as it is now
    (held_now of aid/execution/by_hand.py, where a tick that the PO as it stands, given after it, contradicts counts as ended, in
    reading only: the answer of a stored case writes no mark); and whether the list cannot be read while a match rests on the
    printed order. A value entered in Exact that the case was compared with in place of its PO version's own
    (steps/03_entered.json) is "held" while the marks still say Exact holds that value; "waiting" while they do and a put-back of it
    is open on the list; "undone" once a put-back gives the line its value from before; "other" when the tick was taken back or
    ended, or another value was entered since; "unread" while the list cannot be read. A value that matched as the PO version
    printed it is "other" once the list says Exact holds another value for its line (a tick that the print given last contradicts
    counts as ended already): the value a new check of the same cart would be compared with. Such a match has no entry otherwise, and while the list cannot be read it has none either: the second value returned says so then,
    and the answer says it once for the whole case. Only the fields the list holds (a price, a delivery date, a quantity) and
    the matches of one PO line are judged."""
    try:
        records = case.read_step("entered")["replaced"]
        found = {(record["line_id"], record["field"]): (record["was"], record["entered"]) for record in records}
    except (OSError, ValueError, KeyError, TypeError):
        found = {}
    matches = result.findings.matches if result.findings is not None else []
    printed = {(match.po_line_ids[0], match.field): match.value for match in matches
               if len(match.po_line_ids) == 1 and match.field in _LINE_WRITES and (match.po_line_ids[0], match.field) not in found}
    if po is None or not (found or printed):
        return {}, False
    try:
        held = held_now(po, prepared, tryout_case(case), store)
    except OSError:                              # also a list that cannot be understood
        return {key: _Rested(was=was, how="unread") for key, (was, _) in found.items()}, bool(printed)
    rested = {}
    for key, (was, entered) in found.items():
        kind, now = _LINE_WRITES.get(key[1], ""), held.get(key)
        if now is not None and same_value(kind, now.value, entered):
            rested[key] = _Rested(was=was, how="waiting" if now.waiting else "held")
        elif now is not None and now.put_back and same_value(kind, now.value, was):
            rested[key] = _Rested(was=was, how="undone")
        else:
            rested[key] = _Rested(was=was, how="other", now=now.value if now is not None else None)
    for key, value in printed.items():
        now = held.get(key)
        if now is not None and now.differs:
            rested[key] = _Rested(was=value, how="other", now=now.value)
    return rested, False


def _version(deps: Deps, case: Case, result: CaseResult) -> PoVersion | None:
    """The PO version the case was checked against: the one selected, as the case's own step 01 holds it (read as the review page
    reads it), so that a later version or look of the PO, or a PDF version with the number of a look, changes nothing; else, without
    a selected version or a step 01 that can be read, the PO as it stands; None for an unknown PO."""
    try:
        selected = _selected_version(case, result)
    except (OSError, ValueError, KeyError, TypeError):
        selected = None
    if selected is not None:
        return selected
    return deps.store.as_it_stands(case.po_number) if case.po_number else None


def _decisions(case: Case, result: CaseResult) -> dict[str, str]:
    """The buyer's last decision on each finding, by finding id, as the review page recorded it (decisions.jsonl, read by the review
    page's own reader); none when the file cannot be read."""
    try:
        by_item = _review_decisions(case)
    except (OSError, ValueError):
        return {}
    return {item.finding_id: by_item[item.item_id]["decision"] for item in result.review_items
            if item.finding_id is not None and item.item_id in by_item}


def _close_history(case: Case, prepared: Path | None = None) -> tuple[int | None, set[str], set[str]]:
    """What the review page did to the case after its run (_history): the number of its closes, the ids of the write-log entries
    that do not count as made (undone, taken off the list, or not judged because the list cannot be read), and those whose
    put-back waits."""
    closes, undone, waiting, _, _ = _history(case, prepared)
    return closes, undone, waiting


def _history(case: Case, prepared: Path | None = None) -> tuple[int | None, set[str], set[str], set[str], set[str]]:
    """What the review page did to the case after its run: the number of its closes (the "close" records of steps/09_close.json;
    None when that file cannot be read); the ids of the write-log entries that do not count as made; those whose put-back waits on
    the list of changes to enter in Exact; of the first, those that the list could not judge because it cannot be read; and, of
    the first too, the prepared writes whose change a later case took off the list. An
    undo, named by an "undo" record of the review page or by an undo entry of the write log, undoes its entry when it was made in a
    system (status "done"), or when its row on the list in `prepared` (aid/execution/by_hand.py) is entered or seen, held by the
    marks ("already"), or cancelled together with the entry's own row before the entry was entered (both "undone"). While that row is
    open on the list (a put-back made from the tab "In Exact invoeren", or an undo the page could only prepare), the entry stands,
    with its put-back waiting; an undo that failed, or whose row was dropped, replaced or passed over by the list (undone while the
    entry's row is not), undoes nothing. Besides its own undos, each prepared write of the case is judged by the list too
    (_put_backs_of): it is undone once a put-back linked by puts_back to an entry of the change that holds its row, or the put-back
    that took a replaced row's value out of Exact, is entered or seen, and its put-back waits while that put-back is open; so a case
    whose write another case's put-back undoes (the same cart captured again) is told. A prepared write counts as made only while
    its change stands on the list: once a later case took the change off because its document named the value Exact holds again
    (the mark "replaced" by a case, withdraw of aid/execution/by_hand.py), nothing asks the buyer to enter the write's value any
    more, and the write does not count as made. When the list cannot be read
    (prepared_changes.csv or marks.jsonl held by another program, or no list given), no prepared write of a kind the list holds
    counts as made, the case's own undo or not, and every undo not failed counts as undoing its entry: the line then shows a
    difference, never a write that stands, and the banner is amber until the list can be read again, whatever case answers."""
    try:
        records = _close_records(case)
    except (OSError, ValueError, KeyError, TypeError):
        records = None
    undos: dict[str, tuple[str, str | None]] = {}             # the id of an undo entry -> (the entry it undoes, its status)
    for record in records or []:
        made = record.get("undo")
        if record.get("action") == "undo" and isinstance(made, dict) and record.get("entry_id") is not None:
            undos[made.get("entry_id")] = (record["entry_id"], made.get("status"))
    try:
        lines = (case.root / WRITE_LOG_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    writes = []                                               # the case's own prepared writes of a kind the list holds
    for line in lines:
        try:
            entry = json.loads(line)
            if (undo_of := entry.get("undo_of")) is not None:
                undos[entry.get("entry_id")] = (undo_of, entry.get("status"))
            elif (entry.get("status") == "prepared" and isinstance(entry.get("entry_id"), str)
                  and (entry.get("op") or {}).get("kind") in LIST_KINDS):
                writes.append(entry["entry_id"])
        except (ValueError, AttributeError):
            continue
    asked = bool(writes) or any(status not in ("done", "failed") for _, status in undos.values())    # what only the list knows
    found = (_list(prepared) if prepared is not None else None) if asked else []
    states = {entry_id: change.state for change in found for entry_id in change.ids} if found is not None else None
    unread = set(writes) if found is None else set()          # the list cannot be read: no prepared write counts as made
    undone, waiting, left = set(unread), set(), set()
    for write in writes if found is not None else []:
        put = _put_backs_of(write, found)
        if put is not None and put.state in ("entered", "seen"):
            undone.add(write)
        elif put is not None and put.state == "open":
            waiting.add(write)
        elif any(change.withdrawn for change in found if write in change.ids):
            left.add(write)                      # a later case took the change off the list: nobody is asked to enter it
    undone |= left
    for undo_id, (of, status) in undos.items():
        if status == "failed":
            continue
        state = states.get(undo_id) if states is not None else None
        if (status == "done" or state is None or state in ("entered", "seen", "already")
                or (state == "undone" and states.get(of) == "undone")):
            undone.add(of)
        elif state == "open":
            waiting.add(of)
    closes = None if records is None else sum(1 for record in records if record.get("action") == "close")
    return closes, undone, waiting - undone, unread, left


def _list(prepared: Path) -> list | None:
    """The changes of the list of changes to enter in Exact (by_hand.changes); None when the list cannot be read (on Windows, a file
    of it that another program holds)."""
    try:
        return changes(prepared)[0]
    except (OSError, ValueError):
        return None


def _put_backs_of(entry_id: str, found: list):
    """The put-back on the list that judges the prepared write `entry_id` (see _history), or None: the newest put-back linked to an
    entry of the change that holds the write's row; else, when a later row replaced that change, the put-back that took the write's
    value out of Exact. That is, while the marks say that Exact held the write's value when its row was asked (the last entered or
    seen change of its place up to that row has it), the first entered or seen change after the row that sets another value, when
    that is a put-back of a change of the write's value: the put-back that replaced the row (the first later row of its place), one
    of that same change made again (K22), or the put-back of the change whose value the row asked again; while none has set another
    value, a put-back of such a change that stands open as the last row of the place, which waits. Never a put-back made after
    another value was entered, nor one of a change of another value: the case's own write, not a later change of another case."""
    owner = next((change for change in found if entry_id in change.ids), None)
    if owner is None:
        return None
    linked = [change for change in found if change.puts_back is not None and change.puts_back in owner.ids]
    if linked:
        return linked[-1]
    if owner.state != "replaced":
        return None
    place, kind, value = place_of(owner), owner.kind, owner.new_value
    runs = [change for change in found if place_of(change) == place and change.state != "undone"]    # the rows as the list has them
    at = runs.index(owner)
    held = [change for change in runs[:at + 1] if change.state in ("entered", "seen")]
    if not held or not same_value(kind, held[-1].new_value, value):
        return None                              # Exact did not hold the write's value when its row was asked
    holders = {entry: change for change in found for entry in change.ids}

    def puts_value_back(change) -> bool:
        holder = holders.get(change.puts_back) if change.puts_back is not None else None
        return holder is not None and same_value(kind, holder.new_value, value)
    for change in runs[at + 1:]:
        if change.state in ("entered", "seen") and not same_value(kind, change.new_value, value):
            return change if puts_value_back(change) else None     # what took the value out of Exact, if a put-back of it did
    return runs[-1] if runs[-1].state == "open" and puts_value_back(runs[-1]) else None


def _log_entries(case: Case, result: CaseResult) -> list[WriteLogEntry]:
    """The case's write log, undos included; the result's entries when the log cannot be read."""
    try:
        return WriteLog(case.root).entries()
    except (OSError, ValueError):
        return list(result.write_entries)


def _confirmed_looks(case: Case, result: CaseResult) -> set[str]:
    """The ids of the entries that asked the buyer for a look in Exact (a write made and not verified, or with an outcome that is
    not known) whose review item he confirmed and a close of the review page carried out: the change stands, as that close counts
    it. An empty set when the close records cannot be read."""
    looks = {item.item_id: item.entry_id for item in result.review_items if item.reason in LOOKS and item.entry_id}
    try:
        records = _close_records(case)
    except (OSError, ValueError, KeyError, TypeError):
        return set()
    return {looks[item_id] for record in records if record.get("action") == "close"
            for item_id, decision in (record.get("carried_out") or {}).items() if item_id in looks and decision.get("decision") == "confirm"}


class _Writes:
    """The writes of a case on its PO lines as the review page counts them made: the set of aid.review.close._made (done or
    prepared, not failed or prepared after the write route failed, not undone), with only the undos that _history counts as
    undoing (`undone`); a write that asks the buyer for a look in Exact counts once he confirmed its item and a close carried
    that out (`confirmed`, _confirmed_looks), as the close then counts it, and not before; `waiting`
    holds the entries whose put-back waits on the list of changes to enter in Exact, which stand meanwhile, `unread` those of
    `undone` that the list could not judge because it cannot be read, and `left` those of `undone` whose change a later case took
    off the list."""

    def __init__(self, entries: list[WriteLogEntry], undone: set[str], waiting: set[str] = frozenset(), unread: set[str] = frozenset(),
                 confirmed: set[str] = frozenset(), left: set[str] = frozenset()):
        self.undone, self.waiting, self.unread, self.confirmed, self.left = set(undone), set(waiting), set(unread), set(confirmed), set(left)
        self.entries = [e for e in entries if e.undo_of is None and e.op.kind in _LINE_WRITES.values()]
        self.made = _made([e for e in entries if e.undo_of is None or e.undo_of in self.undone])   # an undo that undoes nothing yet

    def is_made(self, entry: WriteLogEntry) -> bool:
        return (_target(entry.op) in self.made and entry.entry_id not in self.undone and entry.status in ("done", "prepared")
                and (_waits_for_the_buyer(entry) is None or entry.entry_id in self.confirmed))

    def on(self, flag, rules_only: bool = False) -> WriteLogEntry | None:
        """The latest made write on the flag's PO line and field; with rules_only, a write of the routing's rules only."""
        kind = _LINE_WRITES.get(flag.field)
        made = [e for e in self.entries if e.op.kind == kind and e.op.line_id in flag.po_line_ids and self.is_made(e)
                and not (rules_only and e.rule_id == BUYER_RULE)]
        return made[-1] if made else None

    def undid(self, flag) -> bool:
        """Whether a write on the flag's PO line and field was undone (and none is made now)."""
        kind = _LINE_WRITES.get(flag.field)
        return any(e.op.kind == kind and e.op.line_id in flag.po_line_ids and e.entry_id in self.undone for e in self.entries)

    def not_read(self, flag) -> bool:
        """Whether a write on the flag's PO line and field does not count as made because the list cannot be read."""
        kind = _LINE_WRITES.get(flag.field)
        return any(e.op.kind == kind and e.op.line_id in flag.po_line_ids and e.entry_id in self.unread for e in self.entries)

    def left_the_list(self, flag) -> bool:
        """Whether a write on the flag's PO line and field does not count as made because a later case took its change off the list."""
        kind = _LINE_WRITES.get(flag.field)
        return any(e.op.kind == kind and e.op.line_id in flag.po_line_ids and e.entry_id in self.left for e in self.entries)

    def lines(self) -> dict[str, str]:
        """The status of the latest made write of each PO line ("done" or "prepared"), by line id."""
        return {e.op.line_id: e.status for e in self.entries if self.is_made(e)}


def _verdict(case: Case, result: CaseResult, decisions: dict[str, str], closes: int | None, writes: _Writes,
             rested: dict[tuple[str, str], _Rested] | None = None, blind: bool = False) -> Verdict:
    """failed; no_po; for a closed case, match when every flag is settled by its current state (_settled), else differences. A
    closed case with review items was closed by the buyer (the pipeline never closes one), and without a readable close record it is
    differences: green is never the default. Neither is a match that rested on a value which the list no longer says Exact holds,
    or cannot say because it cannot be read (rested and blind, see _rested): such a line is a difference again, and a case whose
    matches the list cannot judge is never green."""
    if result.status == "failed":
        return "failed"
    if case.po_number is None:
        return "no_po"
    if result.status != "closed" or (result.review_items and not closes) or blind:
        return "differences"
    if any(one.how in _STALE for key, one in (rested or {}).items() if key in _matched(result)):
        return "differences"
    flags = [(finding_id, finding) for finding_id, finding in findings_by_id(result.findings).items() if isinstance(finding, Flag)] \
        if result.findings is not None else []
    return "match" if all(_settled(flag, decisions.get(finding_id), writes) for finding_id, flag in flags) else "differences"


_STALE = ("undone", "other", "unread")           # how a value a match rested on is judged when the list no longer says Exact holds it


def _matched(result: CaseResult) -> set[tuple[str, str]]:
    """The (PO line id, field) of each value of the case's findings that agreed, for a match of one PO line."""
    matches = result.findings.matches if result.findings is not None else []
    return {(match.po_line_ids[0], match.field) for match in matches if len(match.po_line_ids) == 1}


def _settled(flag, decision: str | None, writes: _Writes) -> bool:
    """Whether the flag no longer stands between the cart and the PO, by its current state. With a decision of the buyer, its latest
    decision counts: a "confirm" settles it when a write on its line and field is made (the rules' or the buyer's, as the review page
    counts it), or by itself on a field the review page writes nothing for; "keep" (the cart still differs) and any other decision
    do not. Without a decision, a made write of the rules settles it."""
    if decision is not None:
        return decision == "confirm" and (flag.field not in _LINE_WRITES or writes.on(flag) is not None)
    return writes.on(flag, rules_only=True) is not None


def _lines(result: CaseResult, po: Document | None, cart: Document | None, language: str,
           decisions: dict[str, str] | None = None, writes: _Writes | None = None,
           rested: dict[tuple[str, str], _Rested] | None = None, blind: bool = False, guess: str | None = None) -> list[dict]:
    """One entry per flag, match and uncompared item of the findings, and one of kind "review" without a line per reason about the
    whole case, in the order of the line ids: {"line_id", "item_code", "field", "kind", "po_value", "doc_value", "reason", "text"}
    (see the module docstring). A match is shown by what the list says of its value now (rested, see _rested): as a flag when the
    list no longer says Exact holds it, when the list cannot be read, and while a put-back of it waits on the list. With blind (the
    list cannot be read while a match rests on the printed order), the reason of the unread list stands among those about the
    whole case, once. `guess` is the line of a cart whose order was guessed, which stands first, in place of the label of that
    reason; "" leaves that entry out (the saved result is closed: the buyer closed the case), and None gives the label."""
    strings, texts, decisions, rested = STRINGS[language], _TEXTS[language], decisions or {}, rested or {}
    writes = writes or _Writes(list(result.write_entries), set())
    findings: Findings | None = result.findings

    def label(reason: str) -> str:
        return reason_label(reason, strings) or texts["no label"]

    case_reasons = list(dict.fromkeys([item.reason for item in result.review_items if item.finding_id is None]
                                      + [item.reason for item in result.review_items if item.reason in CASE_LEVEL_REASONS]
                                      + ([UNREAD_LIST] if blind else [])))
    case_reasons.sort(key=lambda reason: reason != GUESSED_CART)                 # the guess first, the others in their order
    case_texts = {reason: _CASE_REASONS[language].get(reason) or _lower(label(reason)) for reason in case_reasons}
    if guess is not None and GUESSED_CART in case_texts:
        case_texts[GUESSED_CART] = guess
    entries = [{"line_id": "", "item_code": None, "field": None, "kind": "review", "po_value": None, "doc_value": None,
                "reason": label(reason), "text": text} for reason, text in case_texts.items() if text]
    if findings is None:
        return entries
    reviewed: dict[str, str] = {}
    for item in result.review_items:
        if item.finding_id is not None and item.reason != GENERIC_CART_REASON and item.reason not in CASE_LEVEL_REASONS:
            reviewed.setdefault(item.finding_id, item.reason)
    ids = {id(finding): finding_id for finding_id, finding in findings_by_id(findings).items()}
    auto = {finding_id: action.reason for action in result.actions if action.route == "AUTO" for finding_id in action.finding_ids}

    def entry(kind: str, finding, po_value, doc_value, reason: str, text: str) -> dict:
        line_id = ", ".join(finding.po_line_ids or finding.doc_line_ids)
        item_code = _item_code(finding, po, cart)
        head = texts["line"].format(line=line_id) + (f" ({item_code})" if item_code else "")
        decided = texts.get(decisions.get(ids.get(id(finding)), ""), "") if kind != "match" else ""
        return {"line_id": line_id, "item_code": item_code, "field": finding.field, "kind": kind, "po_value": po_value,
                "doc_value": doc_value, "reason": reason, "text": f"{head}: {text}" + (f"; {decided}" if decided else "")}

    for flag in findings.flags:
        finding_id = ids.get(id(flag))
        field = _lower(strings.get(f"field:{flag.field}", flag.field))
        po_value, doc_value = _shown(flag.po_value), _shown(flag.doc_value)
        write = writes.on(flag)
        if flag.field == "line_presence" and flag.detail == "not in cart":        # whatever rule routed it, the line says so
            reason = label("could not be placed")
            text = _lower(reason)
        elif flag.field == "line_presence" and flag.detail == "extra line":
            reason, text = label("extra line"), texts["extra"]
        elif write is not None:
            text = texts[write.status].format(field=field, old=write.previous or write.op.old_value or po_value,
                                              new=write.new or write.op.new_value or doc_value)
            if finding_id in auto:                 # the rules' write: why they made it; the buyer's: the decision says it below
                why = _REAPPROVAL.sub("", auto[finding_id], count=1)
                reason = next((text for key, text in _AUTO_REASONS[language].items() if why.startswith(key)), texts["no label"])
                text += f" ({reason})"
            else:
                reason = texts.get(decisions.get(finding_id, ""), texts["no label"])
            if write.entry_id in writes.waiting:       # its put-back waits on the list: Exact still holds the change
                reason, text = texts["put_back"], f"{text}; {texts['put_back']}"
        else:
            undid = writes.undid(flag)                 # a write that the review page undid: the difference stands
            reason = (texts["unread"] if writes.not_read(flag) else texts["other"] if writes.left_the_list(flag)
                      else texts["undone"] if undid else label(reviewed.get(finding_id, flag.detail)))
            text = (_lower(reason) if flag.field == "line_presence" else
                    texts["values"].format(field=field, doc=texts["no value"] if doc_value is None else doc_value,
                                           po=texts["no value"] if po_value is None else po_value) + f" ({_lower(reason)})")
        entries.append(entry("flag", flag, po_value, doc_value, reason, text))
    for match in findings.matches:
        if match.field == "line_presence":         # the pair's label says it all: "aanwezig", "gesplitst", "samengevoegd"
            value = strings.get(f"pair:{match.value}", match.value)
            text = value
        else:
            value = match.value
            text = f"{_lower(strings.get(f'field:{match.field}', match.field))} {value}"
        one = rested.get((match.po_line_ids[0], match.field)) if len(match.po_line_ids) == 1 else None
        if one is not None and one.how == "waiting":       # the value stands, and its put-back waits on the list
            entries.append(entry("flag", match, value, value, texts["put_back"], f"{text}; {texts['put_back']}"))
        elif one is not None and one.how in _STALE:        # the list no longer says that Exact holds the value the match rested on
            reason = texts[one.how]
            shown = one.now if one.how == "other" and one.now is not None else one.was
            field = _lower(strings.get(f"field:{match.field}", match.field))
            entries.append(entry("flag", match, shown, value, reason, texts["values"].format(
                field=field, doc=value, po=texts["no value"] if shown is None else shown) + f" ({reason})"))
        else:
            entries.append(entry("match", match, value, value, "", text))
    for item in findings.uncompared:
        reason = label(reviewed.get(ids.get(id(item)), item.reason))
        field = _lower(strings.get(f"field:{item.field}", item.field))
        entries.append(entry("uncompared", item, None, None, reason, texts["uncompared"].format(field=field) + f" ({_lower(reason)})"))
    return sorted(entries, key=lambda e: _line_order(e["line_id"]))


def _lower(text: str) -> str:
    """The text with its first letter in lower case, for the middle of a sentence."""
    return text[:1].lower() + text[1:]


def _line_order(line_id: str) -> tuple:
    """The order of the entries: those without a line first, then by the first line id, numerically when every id is a number."""
    first = line_id.partition(", ")[0]
    return (bool(first), int(first) if first.isdecimal() else 0, first)


def _shown(value: DocValue | None) -> str | None:
    """A value as the banner shows it: the normal form, else the text as printed."""
    if value is None:
        return None
    return value.value if value.value is not None else value.text


def _item_code(finding, po: Document | None, cart: Document | None) -> str | None:
    """The item code of the finding's line: the PO version's line, else the cart's."""
    for doc, line_ids in ((po, finding.po_line_ids), (cart, finding.doc_line_ids)):
        if doc is None or not line_ids:
            continue
        line = next((line for line in doc.lines if line.line_id == line_ids[0]), None)
        if line is not None and line.item_code.value is not None:
            return line.item_code.value
    return None


_MESSAGES = {
    "nl": {"match": "Winkelwagen komt overeen met inkooporder {po}",
           "no_po": ("Geen inkooporder gevonden voor deze winkelwagen. Geef de assistent eerst de export van de orderregels uit Exact "
                     "(tabblad Bestanden) en herlaad deze pagina na een minuut."),
           "chosen": " (gekozen op de artikelen in de winkelwagen)",
           "failed": "De voorcontrole is mislukt; zie zaak {case}", "and": " en ",
           "done": ("; {n} regel op de inkooporder bijgewerkt", "; {n} regels op de inkooporder bijgewerkt"),
           "prepared": ("; {n} regel op de inkooporder voorbereid", "; {n} regels op de inkooporder voorbereid"),
           "agree": ("{n} regel komt overeen", "{n} regels komen overeen"), "differ": ("{n} verschil", "{n} verschillen"),
           "missing": ("{n} regel niet in de winkelwagen", "{n} regels niet in de winkelwagen"),
           "uncompared": ("{n} waarde niet vergeleken", "{n} waarden niet vergeleken"),
           "review": ("{n} reden voor de inkoper", "{n} redenen voor de inkoper"),
           "differences": "Inkooporder {po}: {parts}"},
    "en": {"match": "Cart matches purchase order {po}",
           "no_po": ("No purchase order found for this cart. First give the assistant the export of the order lines from Exact (tab "
                     "Files) and reload this page after a minute."),
           "chosen": " (chosen by the items in the cart)",
           "failed": "The pre-check failed; see case {case}", "and": " and ",
           "done": ("; {n} line updated on the purchase order", "; {n} lines updated on the purchase order"),
           "prepared": ("; {n} line prepared on the purchase order", "; {n} lines prepared on the purchase order"),
           "agree": ("{n} line matches", "{n} lines match"), "differ": ("{n} difference", "{n} differences"),
           "missing": ("{n} line not in the cart", "{n} lines not in the cart"),
           "uncompared": ("{n} value not compared", "{n} values not compared"),
           "review": ("{n} reason for the buyer", "{n} reasons for the buyer"),
           "differences": "Purchase order {po}: {parts}"},
}


def _message(verdict: Verdict, case: Case, result: CaseResult, lines: list[dict], language: str,
             writes: _Writes | None = None, rested: dict[tuple[str, str], _Rested] | None = None, chosen: bool = False) -> str:
    """One sentence that sums the verdict up: for differences, the PO lines that agree in full, the differences (a flag whose PO line
    the aid wrote or prepared is none; a match that rested on a value entered in Exact which the list no longer says Exact holds is
    one, see _rested), the lines not in the cart, the values not compared and the reasons for the buyer; after it, for a match or
    differences, the PO lines the aid updated or prepared. With `chosen`, the order was found for sure by the cart's items (the one
    order of the day with exactly those items, not sent), and its number is followed by the words that say so, so that the buyer
    sees that it was not read from the page. For a cart without a PO, what to do."""
    words = _MESSAGES[language]

    def part(key: str, n: int) -> str:
        return words[key][n != 1].format(n=n)
    if verdict not in ("match", "differences"):
        return words[verdict].format(po=case.po_number, case=case.case_id)
    writes = writes or _Writes(list(result.write_entries), set())
    statuses = list(writes.lines().values())
    suffix = "".join(part(status, statuses.count(status)) for status in ("done", "prepared") if status in statuses)
    po = f"{case.po_number}{words['chosen']}" if chosen else case.po_number
    if verdict == "match":
        return words["match"].format(po=po) + suffix
    findings = result.findings
    flags = findings.flags if findings is not None else []
    uncompared = findings.uncompared if findings is not None else []
    stale = [key for key, one in (rested or {}).items() if one.how in _STALE and key in _matched(result)]
    touched = {line for item in [*flags, *uncompared] for line in item.po_line_ids} | {line_id for line_id, _ in stale}
    agree = sum(1 for m in (findings.matches if findings is not None else [])
                if m.field == "line_presence" and not set(m.po_line_ids) & touched)
    missing = sum(1 for flag in flags if flag.detail == "not in cart")
    unwritten = sum(1 for flag in flags if flag.detail != "not in cart" and writes.on(flag) is None) + len(stale)
    counts = [("agree", agree), ("differ", unwritten), ("missing", missing), ("uncompared", len(uncompared)),
              ("review", sum(1 for entry in lines if entry["kind"] == "review"))]
    parts = [part(key, n) for key, n in counts if n or key == "agree"]
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + words["and"] + parts[-1]
    return words["differences"].format(po=po, parts=joined) + suffix


# --- the server ---

class _Handler(BaseHTTPRequestHandler):
    server: _CaptureServer
    server_version = "aid-capture"
    sys_version = ""
    timeout = TIMEOUT                            # the socket timeout of the handler thread: a stalled client lets it go

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", f"Content-Type, {TOKEN_HEADER}")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            return self._json(200, {"ok": True})
        name = path.removeprefix("/testshop/")
        page = _shop_page(name) if self.server.testshop and name != path else None
        if page is None and path.startswith(TRYOUT_SHOP) and self._own_host():
            self.server.refresh()                # a changed settings file is read first: the test mode as it is now decides
            page = _tryout_page(self.server.settings, unquote(path.removeprefix(TRYOUT_SHOP)))
        if page is not None:
            return self._send(200, page.read_bytes(), "text/html; charset=utf-8")
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/capture":
            return self._json(404, {"error": "not found"})
        self.server.refresh()                    # a changed settings file is read before its token is checked
        token, expected = self.headers.get(TOKEN_HEADER, ""), self.server.settings.capture_token or ""
        if not expected or not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
            return self._refuse(401, "missing or wrong token")
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal():
            return self._refuse(400, "the request needs a Content-Length")
        length = length.lstrip("0") or "0"                # read as its number, leading zeros or not
        if len(length) > len(str(MAX_BODY)) or int(length) > MAX_BODY:     # too many digits for int() is too large too
            self.close_connection = True
            return self._refuse(413, f"the page is larger than {MAX_BODY} bytes")
        try:
            body = json.loads(self.rfile.read(int(length)).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):    # RecursionError: JSON nested too deeply
            body = None
        if not (isinstance(body, dict) and isinstance(body.get("url"), str) and isinstance(body.get("html"), str)):
            return self._refuse(400, 'the body must be a JSON object with the strings "url" and "html"')
        try:
            with self.server.lock:
                settings = self.server.settings
                answer = precheck(body["html"], body["url"], self.server.deps, settings)
        except NoCartTable as error:
            return self._refuse(400, str(error), f"; the page is kept as {error.kept}")
        except TryoutOutside as error:           # the bar shows the answer's reason: a sentence for the buyer, in the settings' language
            return self._refuse(403, str(error), said=_TRYOUT_OUTSIDE[settings.language])
        except Exception as error:                # any other failure is the server's: logged, and answered without its details
            self.server.log_error_line(f"pre-check of a page of {page_host(body['url'])} failed: {type(error).__name__}: {error}\n"
                                       + traceback.format_exc())
            return self._json(500, {"error": f"the pre-check failed; see {LOG_FILE}"})
        print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {page_host(body['url'])} {answer.case_id} {answer.verdict}",
              file=sys.stderr, flush=True)
        self._json(200, answer.as_json())

    def log_message(self, format: str, *args) -> None:
        """No access log: the server writes its own line per capture and its errors to capture.log."""

    def log_error(self, format: str, *args) -> None:
        """An error of the base handler itself (a client that timed out, a request line or a method it refuses): one line in
        capture.log."""
        self.server.log_error_line(" ".join((format % args).split()))

    def _refuse(self, status: int, reason: str, note: str = "", said: str | None = None) -> None:
        """Answer `status` with the reason, and log both with the note, which the answer does not carry. With `said`, the answer
        carries that sentence in the reason's place, and the log keeps the reason."""
        self.server.log_error_line(f"{status} {self.command} {urlsplit(self.path).path}: {reason}{note}")
        self._json(status, {"error": said or reason})

    def _own_host(self) -> bool:
        """Whether the request names this PC as its host: exactly one Host header, 127.0.0.1 or localhost, alone or with a port,
        as the review page asks of every request (OwnHost of aid/review/__main__.py), so that a page whose domain name points at
        this PC is never served a try-out cart, which holds the lines of an order."""
        named = self.headers.get_all("Host") or []
        return len(named) == 1 and _OWN_HOST.fullmatch(named[0].strip().lower()) is not None

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _cors(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin.startswith(EXTENSION_ORIGINS):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")


def _shop_page(name: str) -> Path | None:
    """The made-up cart page of the test shop called `name`: an .html file of TESTSHOP, the name without path parts; else None,
    also for a name the file system refuses."""
    if not name.endswith(".html") or Path(name).name != name or "\\" in name or name.startswith("."):
        return None
    return _file(TESTSHOP / name)


def _tryout_page(settings: Settings, name: str) -> Path | None:
    """The try-out cart called `name` (write_cart of aid/tryout.py): an .html file of <cases_root>/tryout, the name without path
    parts, and only while try-outs are offered (offers_tryouts of aid/tryout_mark.py: test_mode true and no authorization limit
    set); else None, also for a folder of that name and for a name the file system refuses."""
    if not offers_tryouts(settings) or not name.endswith(".html") or Path(name).name != name or "\\" in name or "\x00" in name:
        return None
    return _file(Path(settings.cases_root) / CART_FOLDER / name)


def _file(path: Path) -> Path | None:
    """The path when it is a file; None for anything else, and for a name the file system refuses (one that is too long)."""
    try:
        return path if path.is_file() else None
    except (OSError, ValueError):
        return None


class _CaptureServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second server bind a port in use, so a capture server started twice would share the requests;
    # socket.create_server leaves it out there for the same reason.
    allow_reuse_address = os.name != "nt"

    def __init__(self, address: tuple[str, int], settings: Settings, deps: Deps, testshop: bool, settings_file: Path | None = None,
                 rebuild: Callable[[Settings], Deps] | None = None):
        super().__init__(address, _Handler)
        self.settings, self.deps, self.testshop = settings, deps, testshop
        self.settings_file, self.rebuild = settings_file, rebuild
        self.modified = _modified(settings_file)
        self.lock = threading.Lock()             # one pre-check at a time
        self._log_lock = threading.Lock()

    def refresh(self) -> None:
        """Read the settings file again when its modification time changed since it was last read, and use its settings and the
        Deps rebuild makes of them from then on. A file that cannot be read or does not validate, or settings that rebuild refuses,
        keep the settings in use and are one line in capture.log: the fields and their problems, or the class of the error, never a
        value. Such a file is not read again until it changes once more."""
        if self.settings_file is None:
            return
        with self.lock:                          # not while a pre-check runs with the settings in use
            modified = _modified(self.settings_file)
            if modified == self.modified:
                return
            self.modified = modified
            try:
                settings = load_settings(self.settings_file)
            except (OSError, ValueError) as error:       # load_settings names the fields at fault, never a value
                problem = type(error).__name__ if isinstance(error, OSError) else " ".join(str(error).split())
                self.log_error_line(f"the changed settings file is not used: {problem}; the settings in use are kept")
                return
            try:
                deps = self.rebuild(settings) if self.rebuild is not None else self.deps.model_copy(update={"settings": settings})
            except Exception as error:           # its text may quote a value of the settings
                self.log_error_line(f"the changed settings cannot be used ({type(error).__name__}); the settings in use are kept")
                return
            self.settings, self.deps = settings, deps

    def handle_error(self, request, client_address) -> None:
        """An error outside the pre-check (a client that went away while the answer was written, among others): one line in
        capture.log, never a traceback on stderr."""
        error = sys.exc_info()[1]
        self.log_error_line(f"request from {client_address[0]} ended: {type(error).__name__}: {error}")

    def log_error_line(self, text: str) -> None:
        """Append a line (and a traceback, if any) to <cases_root>/capture.log; a log that cannot be written goes to stderr."""
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {text.rstrip()}\n"
        try:
            with self._log_lock:
                Path(self.settings.cases_root).mkdir(parents=True, exist_ok=True)
                with open(Path(self.settings.cases_root) / LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line)
        except OSError:
            print(line, end="", file=sys.stderr)


def serve(settings: Settings, deps: Deps, host: str, port: int, testshop: bool, settings_file: Path | None = None,
          rebuild: Callable[[Settings], Deps] | None = None) -> ThreadingHTTPServer:
    """The capture server on host and port, bound but not yet serving: call its serve_forever (the tests do so in a thread). Given
    the settings file the settings came from, the server reads it again before a pre-check when it changed (_CaptureServer.refresh)
    and uses rebuild to make the Deps of the new settings; without rebuild the Deps keep everything but the settings. A replacement
    of a capture case that an earlier run of the server was cut off in is finished first (finish_marked)."""
    finish_marked(Path(settings.cases_root), deps.clock(), settings.language)
    return _CaptureServer((host, port), settings, deps, testshop, settings_file, rebuild)


def precheck_deps(settings: Settings, environ=os.environ) -> Deps:
    """The Deps of the pre-check as main builds them: the runner's build_deps for the reader "parser", whatever the settings' reader
    says, so that the cart reader reads the page and no model is asked."""
    return build_deps(settings.model_copy(update={"reader": "parser"}), environ)


def _modified(path: Path | None) -> int | None:
    """The modification time of the file in nanoseconds; None when there is no file, or it cannot be looked at."""
    try:
        return Path(path).stat().st_mtime_ns if path is not None else None
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    """Serve until Ctrl-C; 2 when the settings cannot be read or are refused (a file in which exact_base_url is set, with the one
    sentence of aid/settings.py), when capture_token is empty, or when the Deps are refused."""
    parser = argparse.ArgumentParser(prog="python3 -m aid.capture_server",
                                     description="Serve the pre-check of a webshop cart for the browser extension.")
    parser.add_argument("--settings", type=Path, required=True, help="settings.json")
    parser.add_argument("--host", default="127.0.0.1", help="the address to listen on (default 127.0.0.1, this computer only)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"the port (default {DEFAULT_PORT})")
    parser.add_argument("--testshop", action="store_true", help="also serve the made-up cart pages under /testshop/")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.settings)
    except (OSError, ValueError) as exc:
        print(f"aid.capture_server: {exc}", file=sys.stderr)
        return 2
    if not (settings.capture_token or "").strip():
        print("aid.capture_server: capture_token is empty in the settings; set it to the token the extension's options hold",
              file=sys.stderr)
        return 2
    try:                                         # the pre-check asks no model, whatever the settings' reader says
        deps = precheck_deps(settings)
        server = serve(settings, deps, args.host, args.port, args.testshop, settings_file=args.settings, rebuild=precheck_deps)
    except (OSError, ValueError, OverflowError) as exc:      # OverflowError: a port outside 0 to 65535
        print(f"aid.capture_server: {exc}", file=sys.stderr)
        return 2
    host, port = server.server_address[:2]
    print(f"Capture server on http://{host}:{port}" + (f" (test shop: http://{host}:{port}/testshop/CART02.html)" if args.testshop else "")
          + "; stop it with Ctrl-C", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
