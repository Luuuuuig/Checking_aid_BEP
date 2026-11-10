"""The capture server: the passive pre-check of a webshop cart. The browser extension (aid/extension) sends the cart page the buyer
has open, and the server answers at once whether the cart agrees with its purchase order, before the buyer presses order.

    python3 -m aid.capture_server --settings <file> [--host 127.0.0.1] [--port 7870] [--testshop]

The routes:
- POST /capture takes the JSON {"url": ..., "html": ...} with the header X-Aid-Token equal to settings.capture_token, and answers
  200 with a CaptureAnswer as JSON. A missing or wrong token is 401, a body above MAX_BODY bytes 413 (refused on its length, before
  it is read), a body that is not a JSON object with the two string fields 400, and a page without a cart table 400 with the
  reader's reason (the log line names the kept copy of the page). A Content-Length with more digits than MAX_BODY is 413 too, and
  JSON nested too deeply 400. Another error of the pre-check is 500, written to <cases_root>/capture.log with its traceback and
  answered without its details; the saved page is then removed unless a case refers to it.
- GET /health answers 200 {"ok": true}.
- GET /testshop/<name>.html serves a made-up cart page of aid/madeup/out, only when the server was started with --testshop, and only
  a file of that folder whose name has no path parts; else 404.
- OPTIONS answers the browser's preflight with 204. Access-Control-Allow-Origin is echoed on every answer, for an Origin that
  starts with chrome-extension:// or moz-extension:// only.
Every other path is 404. The server listens on 127.0.0.1 unless --host says otherwise. The token is compared with
hmac.compare_digest and is never logged or echoed.

The pre-check of one page (precheck): the page is saved as <cases_root>/captures/<YYYYmmddTHHMMSS>_<host>.html (the url's host
reduced to letters, digits, dots and dashes and cut to 60 characters, else "page"; a lone surrogate saved as "?"). When a case holds
a file with its sha256 already and its result is not failed, that case answers from its saved result, nothing new is opened and the
new copy is removed. Else the cart reader reads the page; a page it refuses is kept as nocart_<name>, the page that extends the
reader. When a capture case of the same day holds the same cart (its capture.json holds the same cart digest: the PO reference the
page states and, per line, the item code, the quantity, the unit and the unit price, each in normal form or else as printed) and its
result is not failed, that case answers and the new copy is removed, so that a reload of a page whose invisible parts changed opens
no new case. Else the PO is found (find_po): the PO number the page states, when the store holds that PO, else the one
PoStore.find_by_lines gives (several: among those whose latest version names the page's supplier, the one with the newest version
date, a version without a date last, else the first; none: the case opens with an unknown PO). The case is opened with
open_capture_case (a case of the same PO in the same second is opened a second later, up to five times, without waiting),
capture.json is written into its folder (the page's sha256, the cart digest, the url's host and the saved page's path; the pipeline
ignores it), and the case is run with run_case over the Deps the server was given and its dispositions recorded. main builds them
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
marked for review, no version selected), each once. They are in the order of the line ids (numeric when they are numbers; the review
entries first). The reason of a flag is the comparison's detail, or the reason of its review item when the routing gave it one other
than the generic "cart: for the buyer" or a case-level reason, through the review page's labels; a reason without a label is the
fixed sentence "zie de zaak voor de reden" ("see the case for the reason"), never the raw reason. A PO line not in the cart always
says so, and an extra line too. A flag whose PO line the aid wrote or prepared, in any status, says what the aid did and why (an R10
action's reason read as that of its base rule). A flag the buyer decided on the review page says what the buyer decided. The item
code is the PO line's, else the cart line's; the text is the whole line as the banner shows it, in the settings' language. The
verdict is "failed" for a failed run, "no_po" for an unknown PO, "match" for a closed case whose every flag is settled as it stands
now: by the buyer's latest decision when there is one (a "confirm" with a write on its line and field made as the review page counts
it, aid.review.close._made: done or prepared, not waiting for the buyer, not undone; a "keep" leaves it standing), else by such a
write of the rules (a case with review items counts as closed by the buyer only with a readable close record of the review page),
and "differences" otherwise; a line whose write was undone reads as a difference with "wijziging ongedaan gemaakt", and a line the
buyer confirmed gives the buyer's decision as its reason; the message sums it up in one sentence, a flag the aid wrote or prepared
not counted as a difference.

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
from urllib.parse import urlsplit

from pydantic import BaseModel

from aid.case import Case, find_case_by_hash, load_case, sha256_file
from aid.comparison import Findings, Flag
from aid.execution.writer import LOG_FILE as WRITE_LOG_FILE, WriteLog, WriteLogEntry
from aid.notify import review_link
from aid.pipeline import RESULT_FILE, CaseResult, Deps, _waits_for_the_buyer, record_dispositions, run_case
from aid.po_store import PoStore, PoVersion
from aid.readers.cart import read_cart_html
from aid.readers.llm_client import ReaderError
# The review page's own readers of decisions.jsonl and steps/09_close.json, so that no file name is copied (loaded with aid.review).
from aid.review.app import _decisions as _review_decisions
from aid.review.close import BUYER_RULE, _made, _records as _close_records, _target
from aid.review.i18n import STRINGS, reason_label
from aid.routing import NO_VERSION, findings_by_id
from aid.runner import RUNNING_FILE, build_deps
from aid.schema import DocValue, Document
from aid.settings import Settings, load_settings
from aid.sources.capture import open_capture_case

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
EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://")
GENERIC_CART_REASON = "cart: for the buyer"      # the routing's reason for every question on a cart; the comparison's detail says more
_HOST_CHARACTERS = re.compile(r"[^A-Za-z0-9.-]")

Verdict = Literal["match", "differences", "no_po", "failed"]


class CaptureAnswer(BaseModel):
    """What the extension shows: the case, its status, the verdict, one entry per finding (see _lines), the link to the case on the
    review page and one sentence that sums the verdict up in the settings' language."""
    case_id: str
    po_number: str | None
    status: str
    verdict: Verdict
    lines: list[dict]
    review_url: str
    message: str


class NoCartTable(ValueError):
    """The page holds no cart table: the reader's reason, answered with 400, and the kept copy of the page, named in the log."""

    def __init__(self, reason: str, kept: Path):
        super().__init__(reason)
        self.kept = kept


def precheck(html: str, url: str, deps: Deps, settings: Settings) -> CaptureAnswer:
    """The whole pre-check of one captured page (see the module docstring): save, dedupe, read, find the PO, open the case, run it,
    answer. Raises NoCartTable for a page without a cart table; its saved copy is then kept as nocart_<name>. Any other exception
    before a case refers to the saved copy removes it, so that a failing pre-check leaves no page behind per page load."""
    cases_root = Path(settings.cases_root)
    now = deps.clock()
    page = _save(cases_root, html, url, now)
    opened = False
    try:
        digest = sha256_file(page)
        case = find_case_by_hash(cases_root, digest)
        if case is not None and (result := _result(case, deps)).status != "failed":
            page.unlink()                        # the same page was checked before: its case answers, nothing new is opened
            return _answer(case, result, _cart(case.input_files[0]), deps, settings)
        try:
            cart = read_cart_html(page)
        except ReaderError as error:
            kept = page.with_name(NO_CART_PREFIX + page.name)
            page.replace(kept)
            raise NoCartTable(str(error), kept) from None
        content = cart_digest(cart)
        if (earlier := _same_cart(cases_root, content, now, deps)) is not None:
            page.unlink()                        # the same cart in a page whose invisible parts changed: its case answers
            return _answer(*earlier, cart, deps, settings)
        case = _open_case(cases_root, page, find_po(cart, deps.store), now)
        opened = True
    except NoCartTable:
        raise
    except BaseException:
        if not opened:
            page.unlink(missing_ok=True)
        raise
    meta = {"sha256": digest, "cart_digest": content, "host": page_host(url), "page": str(page)}
    result = _run(case, deps, lambda: (case.root / CAPTURE_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n",
                                                                           encoding="utf-8"))
    return _answer(case, result, cart, deps, settings)


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


def cart_digest(cart: Document) -> str:
    """The sha256 of the cart as the reader normalises it: the PO reference the page states, then per line the item code, the
    quantity, the unit and the unit price, each its normal form or else its printed text."""
    def shown(value: DocValue) -> str | None:
        return value.value if value.value is not None else value.text
    content = [shown(cart.po_reference)] + [[shown(line.item_code), shown(line.quantity), shown(line.unit), shown(line.unit_price)]
                                            for line in cart.lines]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode("utf-8")).hexdigest()


def _same_cart(cases_root: Path, content: str, now: datetime, deps: Deps) -> tuple[Case, CaseResult] | None:
    """The first capture case of the day of `now` (in folder-name order) whose capture.json holds the cart digest `content` and
    whose result is not failed, with its result; None when there is none."""
    day = f"{now:%Y%m%d}"
    for meta_path in sorted(cases_root.glob(f"*_{day}T*/{CAPTURE_FILE}")):
        try:
            if json.loads(meta_path.read_text(encoding="utf-8")).get("cart_digest") != content:
                continue
            case = load_case(cases_root, meta_path.parent.name)
        except (OSError, ValueError, AttributeError):
            continue
        if (result := _result(case, deps)).status != "failed":
            return case, result
    return None


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


def find_po(cart: Document, store: PoStore) -> str | None:
    """The PO of the cart: the one the page states when the store holds it, else the one find_by_lines gives (see the module
    docstring); None for a cart of no stored PO."""
    stated = cart.po_reference.value
    if stated and store.versions(stated):
        return stated
    candidates = store.find_by_lines(cart)
    if len(candidates) <= 1:
        return candidates[0] if candidates else None
    latest = {number: store.versions(number)[-1] for number in candidates}
    supplier = cart.supplier.value
    same = [number for number in candidates if supplier and latest[number].supplier == supplier]
    if not same:
        return candidates[0]
    # The newest version date first, a version without a date last; max keeps the first of the candidates among equals.
    return max(same, key=lambda n: (latest[n].version_date is not None, str(latest[n].version_date or "")))


def _open_case(cases_root: Path, page: Path, po_number: str | None, now: datetime) -> Case:
    """open_capture_case at `now`; a case of the same PO opened in the same second is tried a second later, up to five times, without
    waiting (the case id is the time it names, not the time it was opened)."""
    for attempt in range(OPEN_ATTEMPTS):
        try:
            return open_capture_case(cases_root, page, po_number, now + timedelta(seconds=attempt))
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
           "confirm": "besluit van de inkoper: waarde van de winkelwagen overgenomen",
           "keep": "besluit van de inkoper: waarde van de inkooporder gehouden"},
    "en": {"line": "line {line}", "values": "{field} {doc} in the cart, {po} on the purchase order", "no value": "no value",
           "extra": "extra line, not on the purchase order", "uncompared": "{field} not compared",
           "done": "{field} on the purchase order updated from {old} to {new}",
           "prepared": "{field} on the purchase order: change prepared from {old} to {new}, see the case",
           "no label": "see the case for the reason", "undone": "change undone",
           "confirm": "the buyer's decision: the cart's value taken",
           "keep": "the buyer's decision: the purchase order's value kept"},
}
# The reasons the routing gives every finding of a case for a cause that concerns the whole case: shown once as a review entry, while
# each line keeps the comparison's detail. The generic reason of a question on a cart is left out altogether.
CASE_LEVEL_REASONS = frozenset({"new supplier", "supplier marked for review", NO_VERSION})
# The line of a review item about the whole case, by its reason; any other reason is shown through the review page's labels.
_CASE_REASONS = {
    "nl": {"new supplier": "eerste zaken van deze leverancier gaan naar de inkoper",
           "supplier marked for review": "zaken van deze leverancier gaan naar de inkoper"},
    "en": {"new supplier": "the first cases of this supplier go to the buyer",
           "supplier marked for review": "the cases of this supplier go to the buyer"},
}
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


def _answer(case: Case, result: CaseResult, cart: Document | None, deps: Deps, settings: Settings) -> CaptureAnswer:
    po = _version(deps, case.po_number, result)
    decisions = _decisions(case, result)
    closes, undone = _close_history(case)
    writes = _Writes(_log_entries(case, result), undone)
    lines = _lines(result, po.document if po is not None else None, cart, settings.language, decisions, writes)
    verdict = _verdict(case, result, decisions, closes, writes)
    return CaptureAnswer(case_id=case.case_id, po_number=case.po_number, status=result.status, verdict=verdict, lines=lines,
                         review_url=review_link(settings, case.case_id),
                         message=_message(verdict, case, result, lines, settings.language, writes))


def _version(deps: Deps, po_number: str | None, result: CaseResult) -> PoVersion | None:
    """The PO version the case was checked against: the one selected, else the latest stored; None for an unknown PO."""
    versions = deps.store.versions(po_number) if po_number else []
    selected = result.version.version if result.version is not None else None
    return next((v for v in versions if v.version == selected), versions[-1] if versions else None)


def _decisions(case: Case, result: CaseResult) -> dict[str, str]:
    """The buyer's last decision on each finding, by finding id, as the review page recorded it (decisions.jsonl, read by the review
    page's own reader); none when the file cannot be read."""
    try:
        by_item = _review_decisions(case)
    except (OSError, ValueError):
        return {}
    return {item.finding_id: by_item[item.item_id]["decision"] for item in result.review_items
            if item.finding_id is not None and item.item_id in by_item}


def _close_history(case: Case) -> tuple[int | None, set[str]]:
    """What the review page did to the case after its run: the number of its closes (the "close" records of steps/09_close.json;
    None when that file cannot be read), and the ids of the write-log entries undone, by its "undo" records or by an undo entry of
    the write log."""
    try:
        records = _close_records(case)
    except (OSError, ValueError, KeyError, TypeError):
        records = None
    undone = {record.get("entry_id") for record in records or [] if record.get("action") == "undo"}
    try:
        lines = (case.root / WRITE_LOG_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            if (undo_of := json.loads(line).get("undo_of")) is not None:
                undone.add(undo_of)
        except (ValueError, AttributeError):
            continue
    closes = None if records is None else sum(1 for record in records if record.get("action") == "close")
    return closes, undone - {None}


def _log_entries(case: Case, result: CaseResult) -> list[WriteLogEntry]:
    """The case's write log, undos included; the result's entries when the log cannot be read."""
    try:
        return WriteLog(case.root).entries()
    except (OSError, ValueError):
        return list(result.write_entries)


class _Writes:
    """The writes of a case on its PO lines as the review page counts them made: the set of aid.review.close._made (done or
    prepared, not waiting for the buyer, not undone), with the entries the review page's undo records name taken out as well."""

    def __init__(self, entries: list[WriteLogEntry], undone: set[str]):
        self.undone = undone | {e.undo_of for e in entries if e.undo_of is not None and e.status != "failed"}
        self.entries = [e for e in entries if e.undo_of is None and e.op.kind in _LINE_WRITES.values()]
        self.made = _made(entries)

    def is_made(self, entry: WriteLogEntry) -> bool:
        return (_target(entry.op) in self.made and entry.entry_id not in self.undone and entry.status in ("done", "prepared")
                and _waits_for_the_buyer(entry) is None)

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

    def lines(self) -> dict[str, str]:
        """The status of the latest made write of each PO line ("done" or "prepared"), by line id."""
        return {e.op.line_id: e.status for e in self.entries if self.is_made(e)}


def _verdict(case: Case, result: CaseResult, decisions: dict[str, str], closes: int | None, writes: _Writes) -> Verdict:
    """failed; no_po; for a closed case, match when every flag is settled by its current state (_settled), else differences. A
    closed case with review items was closed by the buyer (the pipeline never closes one), and without a readable close record it is
    differences: green is never the default."""
    if result.status == "failed":
        return "failed"
    if case.po_number is None:
        return "no_po"
    if result.status != "closed" or (result.review_items and not closes):
        return "differences"
    flags = [(finding_id, finding) for finding_id, finding in findings_by_id(result.findings).items() if isinstance(finding, Flag)] \
        if result.findings is not None else []
    return "match" if all(_settled(flag, decisions.get(finding_id), writes) for finding_id, flag in flags) else "differences"


def _settled(flag, decision: str | None, writes: _Writes) -> bool:
    """Whether the flag no longer stands between the cart and the PO, by its current state. With a decision of the buyer, its latest
    decision counts: a "confirm" settles it when a write on its line and field is made (the rules' or the buyer's, as the review page
    counts it), or by itself on a field the review page writes nothing for; "keep" (the cart still differs) and any other decision
    do not. Without a decision, a made write of the rules settles it."""
    if decision is not None:
        return decision == "confirm" and (flag.field not in _LINE_WRITES or writes.on(flag) is not None)
    return writes.on(flag, rules_only=True) is not None


def _lines(result: CaseResult, po: Document | None, cart: Document | None, language: str,
           decisions: dict[str, str] | None = None, writes: _Writes | None = None) -> list[dict]:
    """One entry per flag, match and uncompared item of the findings, and one of kind "review" without a line per reason about the
    whole case, in the order of the line ids: {"line_id", "item_code", "field", "kind", "po_value", "doc_value", "reason", "text"}
    (see the module docstring)."""
    strings, texts, decisions = STRINGS[language], _TEXTS[language], decisions or {}
    writes = writes or _Writes(list(result.write_entries), set())
    findings: Findings | None = result.findings

    def label(reason: str) -> str:
        return reason_label(reason, strings) or texts["no label"]

    case_reasons = list(dict.fromkeys([item.reason for item in result.review_items if item.finding_id is None]
                                      + [item.reason for item in result.review_items if item.reason in CASE_LEVEL_REASONS]))
    entries = [{"line_id": "", "item_code": None, "field": None, "kind": "review", "po_value": None, "doc_value": None,
                "reason": label(reason), "text": _CASE_REASONS[language].get(reason) or _lower(label(reason))}
               for reason in case_reasons]
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
        else:
            undid = writes.undid(flag)                 # a write that the review page undid: the difference stands
            reason = texts["undone"] if undid else label(reviewed.get(finding_id, flag.detail))
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
    "nl": {"match": "Winkelwagen komt overeen met inkooporder {po}", "no_po": "Geen inkooporder gevonden voor deze winkelwagen",
           "failed": "De voorcontrole is mislukt; zie zaak {case}", "and": " en ",
           "done": ("; {n} regel op de inkooporder bijgewerkt", "; {n} regels op de inkooporder bijgewerkt"),
           "prepared": ("; {n} regel op de inkooporder voorbereid", "; {n} regels op de inkooporder voorbereid"),
           "agree": ("{n} regel komt overeen", "{n} regels komen overeen"), "differ": ("{n} verschil", "{n} verschillen"),
           "missing": ("{n} regel niet in de winkelwagen", "{n} regels niet in de winkelwagen"),
           "uncompared": ("{n} waarde niet vergeleken", "{n} waarden niet vergeleken"),
           "review": ("{n} reden voor de inkoper", "{n} redenen voor de inkoper"),
           "differences": "Inkooporder {po}: {parts}"},
    "en": {"match": "Cart matches purchase order {po}", "no_po": "No purchase order found for this cart",
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
             writes: _Writes | None = None) -> str:
    """One sentence that sums the verdict up: for differences, the PO lines that agree in full, the differences (a flag whose PO line
    the aid wrote or prepared is none), the lines not in the cart, the values not compared and the reasons for the buyer; after it,
    for a match or differences, the PO lines the aid updated or prepared."""
    words = _MESSAGES[language]

    def part(key: str, n: int) -> str:
        return words[key][n != 1].format(n=n)
    if verdict not in ("match", "differences"):
        return words[verdict].format(po=case.po_number, case=case.case_id)
    writes = writes or _Writes(list(result.write_entries), set())
    statuses = list(writes.lines().values())
    suffix = "".join(part(status, statuses.count(status)) for status in ("done", "prepared") if status in statuses)
    if verdict == "match":
        return words["match"].format(po=case.po_number) + suffix
    findings = result.findings
    flags = findings.flags if findings is not None else []
    uncompared = findings.uncompared if findings is not None else []
    touched = {line for item in [*flags, *uncompared] for line in item.po_line_ids}
    agree = sum(1 for m in (findings.matches if findings is not None else [])
                if m.field == "line_presence" and not set(m.po_line_ids) & touched)
    missing = sum(1 for flag in flags if flag.detail == "not in cart")
    unwritten = sum(1 for flag in flags if flag.detail != "not in cart" and writes.on(flag) is None)
    counts = [("agree", agree), ("differ", unwritten), ("missing", missing), ("uncompared", len(uncompared)),
              ("review", sum(1 for entry in lines if entry["kind"] == "review"))]
    parts = [part(key, n) for key, n in counts if n or key == "agree"]
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + words["and"] + parts[-1]
    return words["differences"].format(po=case.po_number, parts=joined) + suffix


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
                answer = precheck(body["html"], body["url"], self.server.deps, self.server.settings)
        except NoCartTable as error:
            return self._refuse(400, str(error), f"; the page is kept as {error.kept}")
        except Exception as error:                # any other failure is the server's: logged, and answered without its details
            self.server.log_error_line(f"pre-check of a page of {page_host(body['url'])} failed: {type(error).__name__}: {error}\n"
                                       + traceback.format_exc())
            return self._json(500, {"error": f"the pre-check failed; see {LOG_FILE}"})
        print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {page_host(body['url'])} {answer.case_id} {answer.verdict}",
              file=sys.stderr, flush=True)
        self._json(200, answer.model_dump(mode="json"))

    def log_message(self, format: str, *args) -> None:
        """No access log: the server writes its own line per capture and its errors to capture.log."""

    def log_error(self, format: str, *args) -> None:
        """An error of the base handler itself (a client that timed out, a request line or a method it refuses): one line in
        capture.log."""
        self.server.log_error_line(" ".join((format % args).split()))

    def _refuse(self, status: int, reason: str, note: str = "") -> None:
        """Answer `status` with the reason, and log both with the note, which the answer does not carry."""
        self.server.log_error_line(f"{status} {self.command} {urlsplit(self.path).path}: {reason}{note}")
        self._json(status, {"error": reason})

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
    """The made-up cart page of the test shop called `name`: an .html file of TESTSHOP, the name without path parts; else None."""
    if not name.endswith(".html") or Path(name).name != name or "\\" in name or name.startswith("."):
        return None
    path = TESTSHOP / name
    return path if path.is_file() else None


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
    and uses rebuild to make the Deps of the new settings; without rebuild the Deps keep everything but the settings."""
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
    """Serve until Ctrl-C; 2 when the settings cannot be read, when capture_token is empty, or when the Deps are refused."""
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
