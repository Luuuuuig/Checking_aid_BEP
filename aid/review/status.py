"""The state of the aid as the review page shows it, read from the aid's own files on this PC and never from Microsoft: what each
case is now, the documents the intake left to the buyer, the runner and its launcher, the work mailbox, the reader and Copilot, and
the last lines of the runner's log. Nothing here writes a file or needs gradio. Nothing of aid.runner, aid.notify or aid.start is
imported, as aid.notify imports the review page, which shows this state: the names of the files and the keys of the states of those
modules that are read here are constants of this module, which the tests hold equal to theirs.

The cases. case_lines gives a line for each case folder under the cases root (a folder with a case.json that can be read): its id,
its PO number, its supplier (once the case has a result), its time of opening and its state, one of STATES, told from its files
alone:
    review         its result is in review, and it does not wait for its PO
    waiting_po     its result is in review, and it waits for its PO (waiting_for_po of aid/review/close.py)
    failed         its result failed, and the buyer has not noted it as handled (HANDLED_FILE in its folder)
    handled        its result failed, and the buyer noted on the review page that he handled it himself (HANDLED_FILE)
    copilot_wait   it has no result yet, and the runner's digest state holds it among the cases Copilot failed, or notes that
                   Copilot fails as a whole, which leaves every case without a result for a later pass (aid/runner.py)
    reading        it has no result yet otherwise: the runner reads it at its next pass, or the capture server is reading it
    waiting        its result waits for the supplier's answer
    closed_itself  its result is closed, and steps/09_close.json holds no close of the review page (a case the aid closed itself,
                   also one of whose writes the buyer undid on the page)
    closed_by_you  its result is closed, and steps/09_close.json holds a close of the review page
Neither a case finished as replaced (steps/09_close.json holds its replacement, finish_replaced of aid/review/close.py) nor a case of
the capture server marked as replaced (marked_replaced of aid/review/app.py) has a line. A failed line also has its reason, one of
REASONS, told from the start of the result's error, which is never part of a line: "unread" for a scan without a text layer, which
Copilot does not read (as unread_scan of aid/notify.py tells it), "copilot" for a document that Copilot refused until the runner
finished the case as failed (COULD_NOT_READ of aid/runner.py), "parser" for a document the parser has no template for (aid/pipeline.py),
and "other" for any other failure. Every case that is not done is listed whatever its age, a failed case the buyer has not noted as
handled among them; a case closed, or failed and handled, only when it was opened in the last `days` days. The cases that need the
buyer (NEEDS) come first, then the others, each newest first. case_states gives the state of the cases named by their ids, whatever
their age, for the tab "Bestanden". state_text and fail_text give the state and the reason in words.

The cache. A tick of the review page reads the state of every case again, so case_lines keeps what it read of each case folder with
the size and the modification time of every file its state is told by (_WATCHED: case.json, the result, steps/09_close.json,
HANDLED_FILE, capture.json, decisions.jsonl, the write log and messages/sent.jsonl), and reads a folder again only when one of them
changed: a result rewritten by a run, a close or a reply filed has another size or another time. The cache keeps the case folders of
the last CACHED_ROOTS cases roots, and of those only the folders still there.

The documents left to the buyer. The scan of the watched folder writes a line to <cases_root>/review_items.jsonl for each document the
buyer must look at himself, which opens no case (aid/sources/mailbox.py): {"time", "file", "reason", "sha256"}. review_items gives
each as a LookItem: its id (item_id: ITEM_PREFIX and the first 12 hex digits of the sha1 of "<time>|<file>", which no case id
resembles), its time, the file as the list of skipped items names it ("PO.eml/scan.pdf" for an attachment of an e-mail), its reason by
kind ("scan" for a scan without a text layer that nobody could class, "many" for a confirmation that names more than one PO, by
LOOK_REASONS, and "other" for any other), never the scan's own text, when the buyer marked it handled, and the sha256 of the
document's bytes, by which the review page tells it from another file of the same name (none for a line written before the scan
noted it). A mark is a line of
<cases_root>/review_items_handled.jsonl (HANDLED_ITEMS_FILE), {"id", "file", "time", "at"}, which the review page appends in one write
(handled_mark makes it), and nothing else writes; the first mark of an item counts. An item waits for the buyer (its state "look")
until it is marked, and is "looked" after: it is listed while it waits, and once marked only when its time lies in the last `days`
days, oldest first; look_item gives one by its id, waiting or handled and whatever its age. A line of either file that cannot be read
(one a crash cut off) is passed over, and an item that stands twice counts once. look_text gives the reason in words.

The assistant. assistant gives what the review page says of the assistant (Assistant), from files only:
- the last pass: the modification time of the runner's digest state, which the runner writes at the end of the scan of every pass;
  late when it is PASS_LATE old or older, or when there is none;
- the launcher (Launcher): whether one runs, by the heartbeat of start_state.json next to the settings file, fresh while it is younger
  than HEARTBEAT_FRESH seconds by the page's clock, as aid/start.py judges it; its start, the starts of its runner and whether it gave
  the runner up after repeated errors; and whether a STOP file stands next to the settings file. Without a settings file none of this
  is known, and none of it is told;
- the work mailbox (Mailbox), as the settings tab tells it (_trigger_text of aid/review/page.py): "off" (no mailbox is read), "pending"
  (connected at the runner's next pass), "unread" (connected, not read yet) or "read", with its times in the local time of this PC;
  and while a mailbox is read, since when its sign-in has lapsed, as the runner notes it in the digest state;
- the reader of the settings, and only while it is Copilot ("llm"): Copilot's failure as a whole (Copilot), which the runner notes
  in the digest state under COPILOT_DOWN, with its first time, its cause and the cases it left waiting, the cause told from the error
  by a sign-in to make again ("sign_in"), a Microsoft that could not be reached ("unreachable"), a status code of three digits
  ("code", with the code) or else "other", never by the error's text; and the cases whose document Copilot refused and that the runner
  still tries, counted as the runner's digest counts them;
- whether the protected store of the sign-in lies inside a git work tree, which refuses every sign-in (work_tree_refusal of
  aid/connections/secrets.py, which makes nothing).
problems gives what is wrong (Problem), the most urgent first: a STOP file, else a runner the launcher gave up, else no pass yet or
none for PASS_LATE; a refused store; a lapsed sign-in of the mailbox; Copilot failing as a whole, with the cases that wait for it now,
or else refusing documents.

The log. log_tail gives the last lines of <cases_root>/runner.log, the newest first, as they stand.
"""
import hashlib
import json
import os
import re
import threading
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

from aid.case import load_case
from aid.connections import secrets              # the module: the folder of the store is looked up when it is asked for
from aid.connections.microsoft import SIGN_IN_AGAIN
from aid.execution.writer import LOG_FILE as WRITE_LOG
from aid.messaging import MESSAGES, SENT_FILE
from aid.pipeline import RESULT_FILE, CaseResult
from aid.readers.copilot_client import NO_TEXT_LAYER, NOT_ACCEPTED, UNREACHABLE
from aid.review.app import CAPTURE_FILE, DECISIONS_FILE, marked_replaced
from aid.review.close import CLOSE_NAME, CLOSE_STEP, REPLACED, waiting_for_po
from aid.review.i18n import STRINGS
from aid.settings import Settings
from aid.sources.mailbox import REVIEW_ITEMS_FILE
from aid.sources.microsoft_mail import connection_of, trigger_on

# The files and the keys of aid.runner, aid.start and aid.notify that are read here (see the module docstring); the tests hold them
# equal to theirs.
STATE_FILE = "digest_state.json"                 # the runner's digest state, in the cases root
LOG_FILE = "runner.log"                          # the runner's log, in the cases root
STOP_FILE = "STOP"                               # the stop switch of the runner and the launcher, next to the settings file
START_STATE = "start_state.json"                 # the launcher's state, next to the settings file
HEARTBEAT_FRESH = 20.0                           # seconds: a younger heartbeat is a launcher that runs
MAILBOX_LAPSED = "mailbox_lapsed"                # the key of the digest state while the sign-in of the mailbox has lapsed: {"since", ...}
COPILOT_DOWN = "copilot"                         # the key while Copilot fails as a whole: {"since", "error", "waiting"}
COPILOT_CASES = "copilot_cases"                  # the key of the cases Copilot failed: {case id: {"refused", "failed", "read_after"}}
COULD_NOT_READ = "Copilot could not read this document: {error}"    # the reader's error of a case the runner finished as failed
UNREAD_SCAN = f"ReaderError: {NO_TEXT_LAYER.partition(':')[0]}"     # the start of the error of a case that failed at reading a scan
NO_TEMPLATE = "ReaderError: no template for "    # the start of the error of a case the parser has no template for (aid/pipeline.py)

HANDLED_FILE = "handled_by_hand.json"            # in a failed case's folder: the buyer noted on the review page that he handled it
HANDLED_ITEMS_FILE = "review_items_handled.jsonl"    # in the cases root: the buyer's marks of the documents left to him
ITEM_PREFIX = "item-"                            # the start of the id of a document left to the buyer
DAYS = 7                                         # the days that a case closed, or failed and handled, and a document handled are listed
PASS_LATE = timedelta(minutes=10)                # no pass of the runner for this long is a problem
LOG_LINES = 20                                   # the lines of runner.log that log_tail gives
CACHED_ROOTS = 8                                 # the cases roots whose case folders the cache keeps
STATES = ("review", "waiting_po", "failed", "handled", "copilot_wait", "reading", "waiting", "closed_itself", "closed_by_you")
NEEDS = ("review", "failed")                     # the states of a case that waits for the buyer
DONE = ("handled", "closed_itself", "closed_by_you")    # the states of a case that is listed for DAYS days only
REASONS = ("unread", "copilot", "parser", "other")      # why a case failed
LOOK_REASONS = {"no text layer; class unknown": "scan", "more than one PO number": "many"}     # the scan's reasons of a review item
CAUSES = ("sign_in", "unreachable", "code", "other")    # the causes of Copilot's failure as a whole
PROBLEMS = ("stopped", "given_up", "no_pass_ever", "no_pass", "store", "lapsed", "copilot_down", "copilot_refused")

_COPILOT_REFUSED = f"ReaderError: {COULD_NOT_READ.partition(':')[0]}"
_NOT_ACCEPTED = NOT_ACCEPTED.partition(":")[0]   # Copilot refused the sign-in (a 401)
_UNREACHABLE = UNREACHABLE.partition(":")[0]
_CODE = re.compile(r"\b[1-5][0-9]{2}\b")        # a status code of HTTP in an error: three digits, standing on their own
_BLOCK = 1 << 16                                 # the bytes log_tail reads from the end of the log at a time
# The files of a case folder its state is told by (see "The cache" in the module docstring), case.json first.
_WATCHED = ("case.json", f"steps/{RESULT_FILE}", f"steps/{CLOSE_STEP:02d}_{CLOSE_NAME}.json", HANDLED_FILE, CAPTURE_FILE, DECISIONS_FILE,
            WRITE_LOG, f"{MESSAGES}/{SENT_FILE}")


class CaseLine(NamedTuple):
    """A case as the review page lists it (see the module docstring)."""
    case_id: str
    po_number: str | None
    supplier: str | None                         # the supplier of its result; None while it has none
    opened_at: datetime                          # when it was opened, in the local time of this PC
    state: str                                   # one of STATES
    reason: str | None                           # why it failed (states failed and handled), one of REASONS; else None


class LookItem(NamedTuple):
    """A document the intake left to the buyer to look at himself (see the module docstring)."""
    item_id: str
    time: datetime                               # when the scan left it, in the local time of this PC
    written: str                                 # that time as review_items.jsonl holds it, of which the id is made
    file: str                                    # the document as the list of skipped items names it
    reason: str                                  # "scan", "many" or "other"
    handled_at: datetime | None                  # when the buyer marked it handled; None while it waits for him
    sha256: str | None = None                    # of the document's bytes, as its line holds it; None for a line without one

    @property
    def state(self) -> str:
        """"look" while the document waits for the buyer, "looked" once he marked it handled."""
        return "look" if self.handled_at is None else "looked"


class Launcher(NamedTuple):
    """The launcher of aid/start.py, as its state file tells it (see the module docstring)."""
    running: bool                                # its heartbeat is fresh
    since: datetime | None                       # when the launcher that runs started
    starts: int                                  # how often it started the runner
    given_up: bool                               # it gave the runner up after repeated errors


class Mailbox(NamedTuple):
    """The work mailbox, as the settings tab tells it (see the module docstring)."""
    state: str                                   # "off", "pending", "unread" or "read"
    connected_at: datetime | None                # in the local time of this PC
    last_sync: datetime | None                   # the last read that ended without an error
    saved: int                                   # the messages saved into the watched folder since it was connected
    lapsed: datetime | None                      # since when its sign-in has lapsed, while it has


class Copilot(NamedTuple):
    """Copilot failing as a whole, as the runner notes it (see the module docstring)."""
    since: datetime                              # the first pass it failed in
    cause: str                                   # one of CAUSES
    code: str | None                             # the status code for the cause "code"
    waiting: int                                 # the cases it left waiting at the last pass that noted it


class Assistant(NamedTuple):
    """What the review page says of the assistant (see the module docstring)."""
    last_pass: datetime | None                   # the end of the scan of the runner's last pass; None before the first
    late: bool                                   # no pass for PASS_LATE, or none at all
    launcher: Launcher
    stopped: bool                                # a STOP file stands next to the settings file
    mailbox: Mailbox
    reader: str                                  # the reader of the settings: "parser" or "llm"
    copilot: Copilot | None                      # Copilot failing as a whole, while the settings read with it
    refused: int                                 # the cases whose document Copilot refused and that the runner still tries
    store_refused: bool                          # the store of the sign-in lies inside a git work tree


class Problem(NamedTuple):
    """A problem of the assistant (see problems)."""
    kind: str                                    # one of PROBLEMS
    since: datetime | None = None                # the last pass (no_pass), the lapse (lapsed) or Copilot's first failure (copilot_down)
    n: int = 0                                   # the cases waiting for Copilot (copilot_down) or whose document it refused (copilot_refused)


class _Read(NamedTuple):
    """What a case folder's state is told by, as the cache keeps it."""
    po_number: str | None
    supplier: str | None
    opened_at: datetime
    state: str | None                            # None while the case has no result: the digest state then tells its state
    reason: str | None


_CACHE: dict[Path, dict[str, tuple[tuple, _Read | None]]] = {}     # by cases root, by folder: the files' sizes and times, and what was read
_CACHE_LOCK = threading.Lock()                   # the review page reads in threads of its own


# --- the cases ---

def case_lines(cases_root: Path, now: datetime, days: int = DAYS) -> list[CaseLine]:
    """A line for each case under cases_root that is not done, and for each case done that was opened in the last `days` days before
    `now`, in their order (see the module docstring); none for a cases root that is not there."""
    root = Path(cases_root)
    waits = _copilot_waits(root)
    since = _local(now) - timedelta(days=days)
    key = root.absolute()
    known = _CACHE.get(key, {})
    try:
        folders = sorted(path for path in root.iterdir() if not path.name.startswith("."))
    except OSError:
        folders = []
    seen: dict[str, tuple[tuple, _Read | None]] = {}
    lines = []
    for path in folders:
        stats = _stats(path)
        if stats is None:
            continue
        hit = known.get(path.name)
        read = hit[1] if hit is not None and hit[0] == stats else _read(root, path)
        seen[path.name] = (stats, read)
        if read is None:
            continue
        state = read.state or ("copilot_wait" if waits(path.name) else "reading")
        if state in DONE and read.opened_at < since:
            continue
        lines.append(CaseLine(path.name, read.po_number, read.supplier, read.opened_at, state, read.reason))
    with _CACHE_LOCK:
        _CACHE.pop(key, None)
        _CACHE[key] = seen
        while len(_CACHE) > CACHED_ROOTS:
            _CACHE.pop(next(iter(_CACHE)))
    lines.sort(key=lambda line: (line.opened_at, line.case_id), reverse=True)
    return sorted(lines, key=lambda line: line.state not in NEEDS)


def case_states(cases_root: Path, case_ids: Iterable[str]) -> dict[str, str]:
    """The state of each of the cases named by its id (STATES), told as case_lines tells it but whatever the case's age, through its
    cache, which it reads and leaves as it is; a case whose folder is not there or is no case, cannot be read or holds a case replaced
    by another has none. The tab "Bestanden" of the review page says by it what the case of a line is now (aid/review/intake.py)."""
    root = Path(cases_root)
    waits = _copilot_waits(root)
    known = _CACHE.get(root.absolute(), {})
    found: dict[str, str] = {}
    for case_id in dict.fromkeys(case_ids):
        if not isinstance(case_id, str) or case_id in ("", ".", "..") or Path(case_id).name != case_id:
            continue
        path = root / case_id
        if (stats := _stats(path)) is None:
            continue
        hit = known.get(case_id)
        read = hit[1] if hit is not None and hit[0] == stats else _read(root, path)
        if read is not None:
            found[case_id] = read.state or ("copilot_wait" if waits(case_id) else "reading")
    return found


def _copilot_waits(root: Path) -> Callable[[str], bool]:
    """Whether a case of the cases root that has no result yet waits for Copilot, by the runner's digest state: its id among the cases
    Copilot failed, or a note that Copilot fails as a whole (see the module docstring)."""
    digest = _object(root / STATE_FILE)
    tried = digest.get(COPILOT_CASES)
    tried = set(tried) if isinstance(tried, dict) else set()
    down = isinstance(digest.get(COPILOT_DOWN), dict)
    return lambda case_id: down or case_id in tried


def state_text(state: str, language: str) -> str:
    """The state of a case (STATES), or of a document left to the buyer ("look", "looked"), in words in the language."""
    return STRINGS[language][f"state:{state}"]


def fail_text(reason: str, language: str) -> str:
    """Why a case failed (REASONS) in words in the language, never the error itself."""
    return STRINGS[language][f"fail:{reason}"]


def _stats(folder: Path) -> tuple | None:
    """The size and the modification time of each file of _WATCHED in the folder, None for one that is not there; None for a folder
    without a case.json, which is no case."""
    first = _stat(folder / _WATCHED[0])
    if first is None:
        return None
    return first, *(_stat(folder / name) for name in _WATCHED[1:])


def _stat(path: Path) -> tuple[int, int] | None:
    try:
        found = os.stat(path)
    except OSError:
        return None
    return found.st_size, found.st_mtime_ns


def _read(root: Path, folder: Path) -> _Read | None:
    """What the state of the case folder is told by (see the module docstring); None for a folder whose case.json or result cannot be
    read, and for a case replaced by another."""
    try:
        case = load_case(root, folder.name)
        opened = _local(datetime.fromisoformat(json.loads((folder / "case.json").read_bytes())["opened_at"]))
        if marked_replaced(folder):
            return None
        path = folder / "steps" / RESULT_FILE
        if not path.exists():
            return _Read(case.po_number, None, opened, None, None)
        result = CaseResult.model_validate_json(path.read_bytes())
        reason = None
        if result.status == "review":
            state = "review" if waiting_for_po(case, result) is None else "waiting_po"
        elif result.status == "failed":
            state, reason = "handled" if (folder / HANDLED_FILE).exists() else "failed", _reason(result.error)
        elif result.status == "closed":
            actions = _actions(case)
            if REPLACED in actions:
                return None
            state = "closed_by_you" if "close" in actions else "closed_itself"
        else:
            state = "waiting"
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return _Read(case.po_number, result.supplier, opened, state, reason)


def _actions(case) -> set:
    """The actions of the records of the case's steps/09_close.json (close, undo, replaced); none when it is not there, or cannot be
    read, as replaced of aid/review/close.py counts it."""
    try:
        records = case.read_step(CLOSE_NAME)["records"]
        return {record.get("action") for record in records if isinstance(record, dict)}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def _reason(error: str | None) -> str:
    """Why a case failed, one of REASONS, by the start of the error of its result (see the module docstring)."""
    error = error or ""
    if error.startswith(UNREAD_SCAN):
        return "unread"
    if error.startswith(_COPILOT_REFUSED):
        return "copilot"
    if error.startswith(NO_TEMPLATE):
        return "parser"
    return "other"


# --- the documents left to the buyer ---

def review_items(cases_root: Path, now: datetime, days: int = DAYS) -> list[LookItem]:
    """Each document the intake left to the buyer under cases_root that waits for him, and each he marked handled whose time lies in
    the last `days` days before `now`, oldest first (see the module docstring); none without the file."""
    since = _local(now) - timedelta(days=days)
    return sorted((item for item in _look_items(Path(cases_root)).values() if item.handled_at is None or item.time >= since),
                  key=lambda item: (item.time, item.file))


def look_item(cases_root: Path, item_id: str) -> LookItem | None:
    """The document left to the buyer under cases_root with this id, waiting or handled and whatever its age; None when no line of
    review_items.jsonl gives it."""
    return _look_items(Path(cases_root)).get(item_id)


def _look_items(root: Path) -> dict[str, LookItem]:
    """Every document left to the buyer under the cases root, by its id, with the first mark of each (see the module docstring)."""
    marks: dict[str, datetime] = {}
    for mark in _lines(root / HANDLED_ITEMS_FILE):
        if isinstance(mark.get("id"), str) and (at := _time(mark.get("at"))) is not None:
            marks.setdefault(mark["id"], at)
    found: dict[str, LookItem] = {}
    for line in _lines(root / REVIEW_ITEMS_FILE):
        written, file, reason = line.get("time"), line.get("file"), line.get("reason")
        if not isinstance(file, str) or not file or (time := _time(written)) is None:
            continue
        made = item_id(written, file)
        kind = LOOK_REASONS.get(reason, "other") if isinstance(reason, str) else "other"
        digest = line.get("sha256") if isinstance(line.get("sha256"), str) else None
        found.setdefault(made, LookItem(made, time, written, file, kind, marks.get(made), digest))
    return found


def item_id(time: str, file: str) -> str:
    """The id of a document left to the buyer, by the time and the file of its line in review_items.jsonl: ITEM_PREFIX and the first
    12 hex digits of the sha1 of "<time>|<file>"."""
    return ITEM_PREFIX + hashlib.sha1(f"{time}|{file}".encode("utf-8", "surrogatepass")).hexdigest()[:12]


def is_item(value) -> bool:
    """Whether a value of the review tab's case field names a document left to the buyer, and no case."""
    return isinstance(value, str) and value.startswith(ITEM_PREFIX)


def handled_mark(item: LookItem, at: datetime) -> dict:
    """The line of HANDLED_ITEMS_FILE that marks the document handled at `at`, the time of the page's clock, to the second."""
    return {"id": item.item_id, "file": item.file, "time": item.written, "at": at.isoformat(timespec="seconds")}


def look_text(reason: str, language: str) -> str:
    """Why the intake left a document to the buyer ("scan", "many", "other") in words in the language, never the scan's own reason."""
    return STRINGS[language][f"look:{reason}"]


def _lines(path: Path) -> list[dict]:
    """The JSON objects of the lines of the file; a line that cannot be read as one is passed over, and so is a file that cannot be
    read."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    found = []
    for line in data.split(b"\n"):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


# --- the assistant ---

def assistant(settings_file: Path | None, cases_root: Path, settings: Settings, now: datetime, secrets_dir: Path | None = None) -> Assistant:
    """What the review page says of the assistant (see the module docstring) at the time `now` of the page's clock. settings_file is
    the settings file the page was started with (None: none), cases_root the folder of the cases with the runner's files, settings
    the settings in use, and secrets_dir the folder of the store of the sign-in (default_secrets_dir of aid/connections/secrets.py
    when None)."""
    root, now = Path(cases_root), _local(now)
    digest = _object(root / STATE_FILE)
    last = _modified(root / STATE_FILE)
    folder = Path(settings_file).resolve().parent if settings_file is not None else None
    llm = settings.reader == "llm"
    store = Path(secrets_dir) if secrets_dir is not None else secrets.default_secrets_dir()
    return Assistant(last_pass=last, late=last is None or now - last >= PASS_LATE, launcher=_launcher(folder, now),
                     stopped=folder is not None and (folder / STOP_FILE).exists(), mailbox=_mailbox(settings, digest), reader=settings.reader,
                     copilot=_copilot(digest) if llm else None, refused=_refused(digest) if llm else 0,
                     store_refused=secrets.work_tree_refusal(store) is not None)


def problems(assistant: Assistant, lines: Sequence[CaseLine]) -> list[Problem]:
    """What is wrong, the most urgent first (see the module docstring); `lines` are the cases now (case_lines), whose cases that wait
    for Copilot are counted."""
    found = []
    if assistant.stopped:
        found.append(Problem("stopped"))
    elif assistant.launcher.given_up:
        found.append(Problem("given_up"))
    elif assistant.last_pass is None:
        found.append(Problem("no_pass_ever"))
    elif assistant.late:
        found.append(Problem("no_pass", assistant.last_pass))
    if assistant.store_refused:
        found.append(Problem("store"))
    if assistant.mailbox.lapsed is not None:
        found.append(Problem("lapsed", assistant.mailbox.lapsed))
    if assistant.copilot is not None:
        found.append(Problem("copilot_down", assistant.copilot.since, sum(1 for line in lines if line.state == "copilot_wait")))
    elif assistant.refused:
        found.append(Problem("copilot_refused", n=assistant.refused))
    return found


def _launcher(folder: Path | None, now: datetime) -> Launcher:
    """The launcher of the settings folder, by its state file while its heartbeat is fresh at `now` (see the module docstring)."""
    state = _object(folder / START_STATE) if folder is not None else {}
    launcher, parts = state.get("launcher"), state.get("parts")
    beat = launcher.get("heartbeat") if isinstance(launcher, dict) else None
    if not (isinstance(beat, (int, float)) and not isinstance(beat, bool) and abs(now.timestamp() - beat) < HEARTBEAT_FRESH):
        return Launcher(False, None, 0, False)
    runner = parts.get("runner") if isinstance(parts, dict) else None
    runner = runner if isinstance(runner, dict) else {}
    starts = runner.get("starts")
    return Launcher(True, _time(launcher.get("started")), starts if type(starts) is int else 0, runner.get("given_up") is True)


def _mailbox(settings: Settings, digest: dict) -> Mailbox:
    """The work mailbox of the settings (see the module docstring), its state as the settings tab reads it."""
    if not trigger_on(settings):
        return Mailbox("off", None, None, 0, None)
    lapse = digest.get(MAILBOX_LAPSED)
    lapsed = _time(lapse.get("since")) if isinstance(lapse, dict) else None
    state = connection_of(settings)
    if state is None:
        return Mailbox("pending", None, None, 0, lapsed)
    last = _utc(state["last_sync"]) if state["last_sync"] else None
    return Mailbox("unread" if last is None else "read", _utc(state["connected_at"]), last, state["saved"], lapsed)


def _copilot(digest: dict) -> Copilot | None:
    """Copilot failing as a whole as the digest state notes it, or None (see the module docstring)."""
    note = digest.get(COPILOT_DOWN)
    if not isinstance(note, dict) or (since := _time(note.get("since"))) is None:
        return None
    cause, code = _cause(note["error"] if isinstance(note.get("error"), str) else "")
    waiting = note.get("waiting")
    return Copilot(since, cause, code, waiting if type(waiting) is int and waiting >= 0 else 0)


def _cause(error: str) -> tuple[str, str | None]:
    """The cause of Copilot's failure as a whole, one of CAUSES, by its error (aid/readers/copilot_client.py), with the status code for
    the cause "code"; nothing else of the error."""
    if SIGN_IN_AGAIN in error or error.startswith(_NOT_ACCEPTED):
        return "sign_in", None
    if error.startswith(_UNREACHABLE):
        return "unreachable", None
    if (code := _CODE.search(error)) is not None:
        return "code", code.group()
    return "other", None


def _refused(digest: dict) -> int:
    """The cases whose document Copilot refused and that the runner still tries, as its digest counts them: a refusal of the document,
    or a failure of the chat that carried it once Copilot had read another case."""
    cases = digest.get(COPILOT_CASES)
    if not isinstance(cases, dict):
        return 0
    return sum(1 for record in cases.values()
               if isinstance(record, dict) and (record.get("refused") or (record.get("read_after") and record.get("failed"))))


# --- the log ---

def log_tail(cases_root: Path, n: int = LOG_LINES) -> list[str]:
    """The last n lines of <cases_root>/runner.log, the newest first, read from the end of the file a block at a time: each as it stands,
    its end of line and the spaces at its end taken off and a byte that is no UTF-8 replaced, an empty line passed over; none without
    the log."""
    found: list[str] = []
    if n <= 0:
        return found
    try:
        log = open(Path(cases_root) / LOG_FILE, "rb")
    except OSError:
        return found
    with log:
        end, rest = log.seek(0, os.SEEK_END), b""
        while end > 0 and len(found) < n:
            start = max(0, end - _BLOCK)
            log.seek(start)
            lines = (log.read(end - start) + rest).split(b"\n")
            rest = lines.pop(0) if start > 0 else b""          # the first line of the block may begin in the block before
            for line in reversed(lines):
                if text := line.decode("utf-8", errors="replace").rstrip():
                    found.append(text)
                    if len(found) == n:
                        break
            end = start
    return found


# --- times and files ---

def _local(moment: datetime) -> datetime:
    """The time in the local time of this PC, without a zone; a time without a zone is local time already."""
    return moment.astimezone().replace(tzinfo=None) if moment.utcoffset() is not None else moment


def _time(text) -> datetime | None:
    """A time written by the runner's, the launcher's or the page's clock, in the local time of this PC (a time without a zone is
    local time); None for what is no time."""
    if not isinstance(text, str):
        return None
    try:
        return _local(datetime.fromisoformat(text))
    except ValueError:
        return None


def _utc(text: str) -> datetime | None:
    """A time of the mailbox's state, kept in UTC (a time without a zone is UTC), in the local time of this PC; None for what is no
    time."""
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return _local(moment if moment.utcoffset() is not None else moment.replace(tzinfo=timezone.utc))


def _modified(path: Path) -> datetime | None:
    """The modification time of the file in the local time of this PC; None for a file that is not there."""
    try:
        return datetime.fromtimestamp(os.stat(path).st_mtime)
    except (OSError, OverflowError, ValueError):
        return None


def _object(path: Path) -> dict:
    """The JSON object of the file; {} for a file that is not there, cannot be read or holds no object."""
    try:
        found = json.loads(Path(path).read_bytes())
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}
