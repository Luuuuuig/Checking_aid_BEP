"""Tests of the state of the aid as the review page shows it (aid/review/status.py): the state of each case folder in words, why a
failed case failed, the documents the intake left to the buyer, the runner with its launcher, the work mailbox, the reader and
Copilot, the problems in their order, and the last lines of the runner's log, all read from made-up files under tmp_path."""
import builtins
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from aid import notify, runner, start
from aid.case import load_case, open_case
from aid.connections import secrets
from aid.connections.microsoft import SIGN_IN_AGAIN, MicrosoftAccount
from aid.pipeline import NO_STORED_VERSION, RESULT_FILE, CaseResult, run_case
from aid.po_store import PoStore
from aid.readers.copilot_client import NO_TEXT_LAYER, NO_TEXT_ON_PAGE, NOT_ACCEPTED
from aid.review import status
from aid.review.close import finish_replaced
from aid.review.i18n import STRINGS
from aid.review.intake import SKIPPED
from aid.sources.mailbox import REVIEW_ITEMS_FILE, scan_folder
from aid.tests.conftest import REPO_ROOT, SETTINGS, deps
from aid.tests.fakes import CLIENT_ID, TENANT_ID, case_result
from aid.versioning import VersionChoice

NOW = datetime(2026, 11, 12, 9, 0)                   # the clock of deps()
HOUR = timedelta(hours=1)
WEEK = timedelta(days=status.DAYS)
WAITS = VersionChoice(version=None, method="none", reason=NO_STORED_VERSION, confidence=0.0)     # no version of the PO is stored
UNTEMPLATED = "ReaderError: no template for document.pdf"
# The nine states of a case in words, Dutch and English, as the review tab shows them.
STATE_WORDS = {"review": ("Ter beoordeling", "For review"), "waiting_po": ("Wacht op de inkooporder", "Waits for its purchase order"),
               "failed": ("Mislukt", "Failed"), "handled": ("Mislukt, door u afgehandeld", "Failed, handled by you"),
               "copilot_wait": ("Wacht op Copilot", "Waits for Copilot"), "reading": ("Wordt gelezen", "Being read"),
               "waiting": ("Wacht op de leverancier", "Waits for the supplier"), "closed_itself": ("Vanzelf afgesloten", "Closed by itself"),
               "closed_by_you": ("Door u afgesloten", "Closed by you")}


def folder(root: Path, po_number: str | None, opened: datetime, saved: str | None = None, **update) -> Path:
    """A case folder under root, opened at `opened` for the PO with a made-up document, holding a saved result with the status
    `saved` for the supplier S07 (no result without one), `update` laid over it (an error, a version); returns the folder."""
    document = root.parent / "document.pdf"
    if not document.exists():
        document.write_bytes(b"%PDF-1.4\n% a made-up document of the status tests\n")
    case = open_case(root, "confirmation", [document], po_number, opened)
    if saved is not None:
        case.write_step(8, "result", case_result(case.case_id, saved, supplier="S07").model_copy(update=update))
    return case.root


def records(path: Path, *actions: str) -> None:
    """Steps/09_close.json of the case folder with a record of each action, as a close, an undo and a replacement add theirs."""
    (path / "steps" / "09_close.json").write_text(json.dumps({"records": [{"action": action, "at": NOW.isoformat()} for action in actions]}),
                                                  encoding="utf-8")


def digest(root: Path, **notes) -> Path:
    """The runner's digest state of root, with these notes beside its three keys."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / runner.STATE_FILE
    path.write_text(json.dumps({"last_sent": None, "reported": [], "skipped": []} | notes), encoding="utf-8")
    return path


def aged(path: Path, moment: datetime) -> None:
    """The file's modification time set to the moment."""
    os.utime(path, (moment.timestamp(), moment.timestamp()))


def handled(path: Path) -> None:
    """The mark the review page writes into a failed case's folder once the buyer handled the case himself."""
    (path / status.HANDLED_FILE).write_text(json.dumps({"at": NOW.isoformat()}), encoding="utf-8")


def local(text: str) -> datetime:
    """A time in UTC, as the mailbox's state holds it, in the local time of this PC, without a zone."""
    return datetime.fromisoformat(text).astimezone().replace(tzinfo=None)


class MadeAnAccount(BaseException):
    """The state of the assistant made a Microsoft account or a store of the sign-in. A BaseException, so that no `except Exception`
    of the code under test can hide it."""


# --- the cases ---

def test_every_case_folder_gets_its_state_in_words(tmp_path):
    root = tmp_path / "cases"
    made = {
        "review": folder(root, "250107", NOW - HOUR, "review"),
        "waiting_po": folder(root, "250101", NOW - 2 * HOUR, "review", version=WAITS),
        "failed": folder(root, "250105", NOW - 3 * HOUR, "failed", error=UNTEMPLATED),
        "handled": folder(root, "250106", NOW - 4 * HOUR, "failed", error=UNTEMPLATED),
        "copilot_wait": folder(root, "250108", NOW - 5 * HOUR),
        "reading": folder(root, "250109", NOW - 6 * HOUR),
        "waiting": folder(root, "250110", NOW - 7 * HOUR, "waiting"),
        "closed_itself": folder(root, "250111", NOW - 8 * HOUR, "closed"),
        "closed_by_you": folder(root, "250112", NOW - 9 * HOUR, "closed"),
    }
    handled(made["handled"])
    records(made["closed_by_you"], "close")
    undone = folder(root, "250113", NOW - 10 * HOUR, "closed")             # closed by the aid, one of its writes undone on the page
    records(undone, "undo")
    # Open cases are listed whatever their age, a failed case not handled among them; closed and handled ones for a week.
    kept = [folder(root, "250121", NOW - 4 * WEEK, "review"), folder(root, "250122", NOW - 4 * WEEK + HOUR, "failed", error=UNTEMPLATED),
            folder(root, "250123", NOW - 4 * WEEK + 2 * HOUR, "waiting"), folder(root, "250124", NOW - 4 * WEEK + 3 * HOUR, "review", version=WAITS),
            folder(root, "250125", NOW - 4 * WEEK + 4 * HOUR)]
    gone = [folder(root, "250131", NOW - WEEK - HOUR, "closed"), folder(root, "250132", NOW - WEEK - 2 * HOUR, "closed"),
            folder(root, "250133", NOW - WEEK - 3 * HOUR, "failed", error=UNTEMPLATED)]
    records(gone[1], "close")
    handled(gone[2])
    replaced = folder(root, "250101", NOW - 11 * HOUR, "review", version=WAITS)    # checked again as a new case by the runner
    finish_replaced(load_case(root, replaced.name), made["waiting_po"].name, NOW, "nl")
    marked = folder(root, "250114", NOW - 12 * HOUR, "review")                    # a capture case the capture server replaced
    (marked / "capture.json").write_text(json.dumps({"replaced_by": "250114_20261112T090000"}), encoding="utf-8")
    torn = folder(root, "250115", NOW - 13 * HOUR, "review")
    (torn / "case.json").write_text('{"case_id": "250115_2026', encoding="utf-8")   # cut off: passed over
    (root / "prepared").mkdir()                                                    # a folder of the cases root that is no case
    (root / "prepared" / "marks.jsonl").write_text("", encoding="utf-8")
    digest(root, copilot_cases={made["copilot_wait"].name: {"refused": 1, "failed": 0, "read_after": False}})

    lines = status.case_lines(root, NOW)
    assert {line.case_id: line.state for line in lines} == ({path.name: state for state, path in made.items()} | {undone.name: "closed_itself"}
                                                           | dict(zip([path.name for path in kept], ["review", "failed", "waiting", "waiting_po", "reading"])))
    # The cases that need the buyer first, then the others, each newest first.
    assert [line.case_id for line in lines] == [path.name for path in (
        made["review"], made["failed"], kept[1], kept[0], made["waiting_po"], made["handled"], made["copilot_wait"], made["reading"],
        made["waiting"], made["closed_itself"], made["closed_by_you"], undone, kept[4], kept[3], kept[2])]
    by_id = {line.case_id: line for line in lines}
    assert by_id[made["review"].name] == status.CaseLine(made["review"].name, "250107", "S07", NOW - HOUR, "review", None)
    assert by_id[made["reading"].name] == status.CaseLine(made["reading"].name, "250109", None, NOW - 6 * HOUR, "reading", None)
    assert by_id[made["failed"].name].reason == by_id[made["handled"].name].reason == "parser"
    assert {line.reason for line in lines if line.state not in ("failed", "handled")} == {None}
    assert set(STATE_WORDS) == set(status.STATES)
    for state, (nl, en) in STATE_WORDS.items():
        assert status.state_text(state, "nl") == STRINGS["nl"][f"state:{state}"] == nl
        assert status.state_text(state, "en") == STRINGS["en"][f"state:{state}"] == en
    # While Copilot fails as a whole, the runner leaves every case without a result for a later pass: each waits for Copilot.
    digest(root, copilot={"since": (NOW - HOUR).isoformat(), "error": "Copilot refused: 503 no code", "waiting": 3})
    states = {line.case_id: line.state for line in status.case_lines(root, NOW)}
    assert [states[path.name] for path in (made["copilot_wait"], made["reading"], kept[4])] == ["copilot_wait"] * 3
    (root / runner.STATE_FILE).unlink()
    assert {line.case_id: line.state for line in status.case_lines(root, NOW)}[made["copilot_wait"].name] == "reading"
    assert status.case_lines(tmp_path / "nothing", NOW) == []


def test_a_failed_case_says_why_in_words_and_never_its_error_text(tmp_path, madeup_out):
    root = tmp_path / "cases"
    errors = [("unread", f"ReaderError: {NO_TEXT_LAYER}"), ("unread", f"ReaderError: {NO_TEXT_ON_PAGE.format(page=2)}"),
              ("copilot", f"ReaderError: {runner.COULD_NOT_READ.format(error='Copilot refused: 400 BadRequest')}"),
              ("parser", "ReaderError: no template for CART02.png: the parser reads PDF confirmations and HTML cart pages only"),
              ("other", "WriterError: the ERP refused the change (write: rule R04, article_price, article KM-0801)"),
              ("other", "OSError: [Errno 28] No space left on device"), ("other", None)]
    expected = {folder(root, f"2502{number:02d}", NOW - timedelta(minutes=number), "failed", error=error).name: reason
                for number, (reason, error) in enumerate(errors, 1)}
    d = deps(tmp_path, madeup_out)                       # the made-up failure: the parser has no template for MU05's scan
    case = open_case(d.settings.cases_root, "confirmation", [madeup_out / "MU05_conf.pdf"], "250105", NOW + timedelta(minutes=5))
    assert run_case(case, d).status == "failed"
    expected[case.case_id] = "parser"
    lines = status.case_lines(root, NOW)
    assert {line.case_id: line.reason for line in lines} == expected and {line.state for line in lines} == {"failed"}
    for line in lines:
        result = CaseResult.model_validate_json((root / line.case_id / "steps" / RESULT_FILE).read_text(encoding="utf-8"))
        assert (line.reason == "unread") == notify.unread_scan(result)             # as the digest and the tab Bestanden tell it
        assert not re.search(r"Error|BadRequest|R04|template|layer|ERP|space|refused|Copilot", " ".join(str(field) for field in line))
    words = {"unread": ("het document is een scan zonder leesbare tekst", "the document is a scan without readable text"),
             "parser": ("de assistent herkent de opmaak van dit document niet", "the assistant does not recognise the layout of this document"),
             "copilot": ("Copilot kon het document niet lezen", "Copilot could not read the document"),
             "other": ("er ging iets mis bij het verwerken van de zaak", "something went wrong while the case was processed")}
    assert set(words) == set(status.REASONS)
    for reason, (nl, en) in words.items():
        assert status.fail_text(reason, "nl") == nl and status.fail_text(reason, "en") == en


def test_the_status_reads_no_unchanged_result_twice(tmp_path, monkeypatch):
    """A tick of the review page reads every case folder: a result that did not change is read once, and one that was rewritten
    (another size, or the same size at another time) is read again, and so is the line of a case whose mark changed beside it."""
    root = tmp_path / "cases"
    made = [folder(root, "250107", NOW - HOUR, "review"), folder(root, "250101", NOW - 2 * HOUR, "review", version=WAITS),
            folder(root, "250105", NOW - 3 * HOUR, "failed", error=UNTEMPLATED), folder(root, "250111", NOW - 4 * HOUR, "closed"),
            folder(root, "250110", NOW - 5 * HOUR, "waiting"), folder(root, "250109", NOW - 6 * HOUR)]
    closing = case_result(made[0].name, "closed", supplier="S07").model_dump_json()
    opened: list[str] = []
    real = io.open

    def counting(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).name == RESULT_FILE and "r" in mode:
            opened.append(Path(file).parents[1].name)
        return real(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", counting)
    monkeypatch.setattr(builtins, "open", counting)
    first = status.case_lines(root, NOW)
    assert sorted(opened) == sorted(path.name for path in made[:5])           # each result once; the sixth case has none
    opened.clear()
    assert status.case_lines(root, NOW) == first and opened == []
    result = made[0] / "steps" / RESULT_FILE
    written = datetime.fromtimestamp(result.stat().st_mtime)
    result.write_text(closing, encoding="utf-8")                              # a close rewrote the result
    aged(result, written + timedelta(seconds=10))
    states = {line.case_id: line.state for line in status.case_lines(root, NOW)}
    assert opened == [made[0].name] and states[made[0].name] == "closed_itself"
    opened.clear()
    result = made[1] / "steps" / RESULT_FILE
    aged(result, datetime.fromtimestamp(result.stat().st_mtime) + timedelta(seconds=5))     # the same bytes written again later
    status.case_lines(root, NOW)
    status.case_lines(root, NOW)
    assert opened == [made[1].name]
    handled(made[2])                                                           # the buyer handled the failed case himself
    assert {line.case_id: line.state for line in status.case_lines(root, NOW)}[made[2].name] == "handled"
    records(made[3], "close")                                                  # an undone write's close record beside the result
    assert {line.case_id: line.state for line in status.case_lines(root, NOW)}[made[3].name] == "closed_by_you"


# --- the documents the intake left to the buyer ---

def email(path: Path, subject: str, attachment: Path) -> None:
    """An e-mail file from a made-up supplier with the subject and the file attached."""
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = "sales@s07.example.com", "buyer@example.com", subject
    message.set_content("Bijgaand onze bevestiging.")
    kind = "pdf" if attachment.suffix == ".pdf" else "png"
    message.add_attachment(attachment.read_bytes(), maintype="application" if kind == "pdf" else "image", subtype=kind, filename=attachment.name)
    path.write_bytes(bytes(message))


def test_a_document_left_to_the_buyer_is_read_and_named_in_words_never_by_its_reason(tmp_path, madeup_out):
    """The scan of the watched folder writes the lines (aid/sources/mailbox.py); review_items reads each as a document with its id,
    its time, its file as the list of skipped items names it and its reason by kind."""
    root, inbox = tmp_path / "cases", tmp_path / "inbox"
    inbox.mkdir()
    shutil.copy(madeup_out / "MU05_conf.pdf", inbox)                          # a scan without a text layer
    email(inbox / "foto.eml", "Bevestiging", madeup_out / "CART02.png")        # a photo of a page, attached
    email(inbox / "twee.eml", "Bevestiging PO 250101 en PO 250102", madeup_out / "MU01_conf.pdf")
    settings = SETTINGS.model_copy(update={"cases_root": root, "watched_folder": inbox})
    scan = scan_folder(inbox, PoStore(tmp_path / "store"), root, settings, NOW)
    assert scan.new_cases == [] and sorted(scan.skipped) == [("MU05_conf.pdf", "no text layer; class unknown"),
                                                            ("foto.eml/CART02.png", "no text layer; class unknown"),
                                                            ("twee.eml/MU01_conf.pdf", "more than one PO number")]
    with open(root / REVIEW_ITEMS_FILE, "a", encoding="utf-8") as lines:
        lines.write(json.dumps({"time": (NOW + HOUR).isoformat(), "file": "ander.pdf", "reason": "a reason of a later version"}) + "\n")
        lines.write(json.dumps({"time": NOW.isoformat(), "file": "MU05_conf.pdf", "reason": "no text layer; class unknown"}) + "\n")
        lines.write(json.dumps({"time": "no time", "file": "kapot.pdf", "reason": "more than one PO number"}) + "\n")
        lines.write('{"time": "2026-11-12T10:30:00", "file": "afge')        # cut off by a crash: passed over
    items = status.review_items(root, NOW)
    assert [(item.file, item.reason, item.state, item.time, item.handled_at) for item in items] == [
        ("MU05_conf.pdf", "scan", "look", NOW, None), ("foto.eml/CART02.png", "scan", "look", NOW, None),
        ("twee.eml/MU01_conf.pdf", "many", "look", NOW, None), ("ander.pdf", "other", "look", NOW + HOUR, None)]
    for item in items:
        assert item.item_id == "item-" + hashlib.sha1(f"{item.written}|{item.file}".encode()).hexdigest()[:12] == status.item_id(item.written, item.file)
        assert status.is_item(item.item_id) and not re.search(r"text layer|PO number|later version|class", " ".join(str(field) for field in item))
    assert items[0].written == NOW.isoformat() and len({item.item_id for item in items}) == 4
    assert not status.is_item("250105_20261112T090000") and not status.is_item(None) and not status.is_item("")    # a case id is none
    words = {"scan": ("het is een scan zonder leesbare tekst, en de assistent kon niet zien wat voor document het is",
                      "it is a scan without readable text, and the assistant could not tell what kind of document it is"),
             "many": ("het noemt meer dan één inkooporder, en de assistent kon niet kiezen bij welke het hoort",
                      "it names more than one purchase order, and the assistant could not choose which one it belongs to"),
             "other": ("de assistent kon het niet plaatsen", "the assistant could not place it")}
    for reason, (nl, en) in words.items():
        assert status.look_text(reason, "nl") == nl and status.look_text(reason, "en") == en
    for state, nl, en in (("look", "Zelf bekijken", "To look at yourself"), ("looked", "Door u afgehandeld", "Handled by you")):
        assert status.state_text(state, "nl") == nl and status.state_text(state, "en") == en
    assert {SKIPPED[reason] for reason in status.LOOK_REASONS} == {"scan", "numbers"}     # the scan's reasons, as the tab Bestanden knows them
    assert status.review_items(tmp_path / "nothing", NOW) == []


def test_a_document_left_to_the_buyer_carries_the_sha256_its_line_holds(tmp_path):
    """Each document comes with the sha256 of its bytes as its line holds it (aid/sources/mailbox.py), by which the review page tells
    it from another file of the same name; a line written before the scan noted one, or whose sha256 is no text, has none. The id
    stays that of the time and the file."""
    root = tmp_path / "cases"
    root.mkdir()
    digest = hashlib.sha256(b"a made-up document").hexdigest()
    lines = [{"time": NOW.isoformat(), "file": "met.pdf", "reason": "no text layer; class unknown", "sha256": digest},
             {"time": NOW.isoformat(), "file": "zonder.pdf", "reason": "no text layer; class unknown"},
             {"time": NOW.isoformat(), "file": "anders.pdf", "reason": "no text layer; class unknown", "sha256": 12}]
    (root / REVIEW_ITEMS_FILE).write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    items = status.review_items(root, NOW)
    assert [(item.file, item.sha256) for item in items] == [("anders.pdf", None), ("met.pdf", digest), ("zonder.pdf", None)]
    assert items[1].item_id == status.item_id(NOW.isoformat(), "met.pdf") and status.look_item(root, items[1].item_id).sha256 == digest


def test_a_document_marked_handled_is_handled_by_you_and_listed_for_a_week(tmp_path):
    root = tmp_path / "cases"
    root.mkdir()
    lines = [{"time": (NOW - 2 * WEEK).isoformat(), "file": "oud.png", "reason": "no text layer; class unknown"},
             {"time": (NOW - 2 * WEEK).isoformat(), "file": "oud-open.png", "reason": "no text layer; class unknown"},
             {"time": (NOW - HOUR).isoformat(), "file": "nieuw.pdf", "reason": "more than one PO number"}]
    (root / REVIEW_ITEMS_FILE).write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    old_open, old, new = status.review_items(root, NOW)
    assert [item.file for item in (old_open, old, new)] == ["oud-open.png", "oud.png", "nieuw.pdf"]
    marks = [status.handled_mark(old, NOW - timedelta(minutes=5)), status.handled_mark(new, NOW), status.handled_mark(new, NOW + HOUR)]
    assert marks[1] == {"id": new.item_id, "file": "nieuw.pdf", "time": lines[2]["time"], "at": NOW.isoformat(timespec="seconds")}
    (root / status.HANDLED_ITEMS_FILE).write_text("".join(json.dumps(mark) + "\n" for mark in marks) + '{"id": "item-0123', encoding="utf-8")
    assert [(item.file, item.state, item.handled_at) for item in status.review_items(root, NOW)] == [
        ("oud-open.png", "look", None), ("nieuw.pdf", "looked", NOW)]               # the first mark of an item counts
    assert [item.file for item in status.review_items(root, NOW, days=30)] == ["oud-open.png", "oud.png", "nieuw.pdf"]


# --- the assistant ---

def launcher_state(settings_file: Path, beat: float, starts: int = 1, given_up: bool = False) -> None:
    """The launcher's state file next to the settings file, written by the launcher's own writer (aid/start.py), with this heartbeat,
    the launcher's start at 08:01, and its runner started `starts` times, running or given up."""
    made = start._Supervisor(settings_file.resolve(), {"runner": [sys.executable, "-m", "aid.runner"]}, None, time.monotonic,
                             lambda seconds: None, lambda: beat, 60.0, start._Log(settings_file.parent), lambda proc: False)
    made.started = "2026-11-12T08:01:00"
    [part] = made.parts
    part.starts, part.started, part.state = starts, "2026-11-12T08:01:01", start.GIVEN_UP if given_up else start.RUNNING
    assert made._write() is None


def test_the_state_of_the_assistant_is_read_from_files_only(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("no network in the tests")

    def made_one(*args, **kwargs):
        raise MadeAnAccount("the state of the assistant made an account or a store")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(MicrosoftAccount, "__init__", made_one)
    monkeypatch.setattr(secrets.SecretStore, "__init__", made_one)
    root, settings_file = tmp_path / "cases", tmp_path / "aid" / "settings.json"
    settings_file.parent.mkdir()
    settings_file.write_text("{}", encoding="utf-8")
    parser = SETTINGS.model_copy(update={"cases_root": root, "reader": "parser"})
    llm = parser.model_copy(update={"reader": "llm"})

    def shown(settings=parser, given: Path | None = settings_file, **kwargs) -> status.Assistant:
        return status.assistant(given, root, settings, NOW, **kwargs)

    # The last pass: the time the runner last wrote its digest state.
    assert (shown().last_pass, shown().late) == (None, True)
    path = digest(root)
    aged(path, NOW - timedelta(minutes=11))
    assert (shown().last_pass, shown().late) == (NOW - timedelta(minutes=11), True)
    aged(path, NOW - timedelta(minutes=1))
    assert (shown().last_pass, shown().late) == (NOW - timedelta(minutes=1), False)
    # The launcher, by its heartbeat; a STOP file next to the settings file.
    off = status.Launcher(False, None, 0, False)
    assert shown().launcher == off and not shown().stopped
    launcher_state(settings_file, NOW.timestamp() - 5)
    assert shown().launcher == status.Launcher(True, datetime(2026, 11, 12, 8, 1), 1, False)
    launcher_state(settings_file, NOW.timestamp() - 25)                       # a heartbeat that stands still: no launcher runs
    assert shown().launcher == off
    launcher_state(settings_file, NOW.timestamp() - 1, starts=6, given_up=True)
    assert shown().launcher == status.Launcher(True, datetime(2026, 11, 12, 8, 1), 6, True)
    (settings_file.parent / status.STOP_FILE).write_text("", encoding="utf-8")
    assert shown().stopped and shown(given=None).launcher == off and not shown(given=None).stopped
    # The work mailbox, as the settings tab tells it.
    trigger = parser.model_copy(update={"ms_tenant_id": TENANT_ID, "ms_client_id": CLIENT_ID, "mail_folder": "AAMkADOrders="})
    assert shown().mailbox == status.Mailbox("off", None, None, 0, None)
    assert shown(trigger).mailbox == status.Mailbox("pending", None, None, 0, None)
    connection = {"mailbox": None, "folder": "AAMkADOrders=", "connected_at": "2026-11-12T08:00:00Z", "delta_link": None, "last_sync": None, "saved": 0}
    (root / "mailbox_state.json").write_text(json.dumps(connection), encoding="utf-8")
    assert shown(trigger).mailbox == status.Mailbox("unread", local("2026-11-12T08:00:00+00:00"), None, 0, None)
    (root / "mailbox_state.json").write_text(json.dumps(connection | {"delta_link": "made-up", "last_sync": "2026-11-12T08:30:00Z", "saved": 3}),
                                            encoding="utf-8")
    assert shown(trigger).mailbox == status.Mailbox("read", local("2026-11-12T08:00:00+00:00"), local("2026-11-12T08:30:00+00:00"), 3, None)
    digest(root, mailbox_lapsed={"since": "2026-11-12T08:40:00", "logged": "2026-11-12T08:40:00"})
    assert shown(trigger).mailbox.lapsed == datetime(2026, 11, 12, 8, 40)
    assert shown().mailbox.lapsed is None                                       # no mailbox is read: no lapse, as in the digest
    # The reader, and Copilot by the runner's own key ("copilot"), never by its error's text.
    assert (shown().reader, shown(llm).reader) == ("parser", "llm")
    tried = {"250108_20261112T080000": {"refused": 0, "failed": 1, "read_after": False}}
    digest(root, copilot={"since": "2026-11-12T08:15:00", "error": "Copilot refused: 400 BadRequest", "waiting": 2}, copilot_cases=tried)
    assert shown(llm).copilot == status.Copilot(datetime(2026, 11, 12, 8, 15), "code", "400", 2) and "BadRequest" not in repr(shown(llm))
    assert (shown().copilot, shown().refused) == (None, 0)                     # read without AI: Copilot does not matter
    for error, cause, code in ((f"no Microsoft account is signed in: {SIGN_IN_AGAIN}", "sign_in", None),
                               (f"the Microsoft sign-in has lapsed (invalid_grant): {SIGN_IN_AGAIN}", "sign_in", None),
                               (NOT_ACCEPTED, "sign_in", None), ("Copilot could not be reached: ConnectionError", "unreachable", None),
                               ("Copilot answered without a conversation id", "other", None), ("Copilot refused: 503 no code", "code", "503")):
        digest(root, copilot={"since": "2026-11-12T08:15:00", "error": error, "waiting": 1})
        assert shown(llm).copilot == status.Copilot(datetime(2026, 11, 12, 8, 15), cause, code, 1)
    digest(root, copilot_down={"since": "2026-11-12T08:15:00", "error": "Copilot refused: 400 BadRequest", "waiting": 2})
    assert shown(llm).copilot is None                                           # not the runner's key
    digest(root, copilot_cases={"a": {"refused": 1, "failed": 0, "read_after": False}, "b": {"refused": 0, "failed": 2, "read_after": True},
                                "c": {"refused": 0, "failed": 1, "read_after": False}})
    assert (shown(llm).copilot, shown(llm).refused, shown().refused) == (None, 2, 0)
    # The store of the sign-in, refused inside a git work tree, told without making anything.
    assert not shown().store_refused                                           # the tests' default folder lies outside every work tree
    (tmp_path / "home" / ".git").mkdir(parents=True)
    before = sorted(tmp_path.rglob("*"))
    assert shown(secrets_dir=tmp_path / "home" / ".config" / "inkoopassistent").store_refused
    (tmp_path / ".git").mkdir()                                                # the default folder of the tests lies under tmp_path
    assert shown().store_refused and sorted(tmp_path.rglob("*")) == sorted([*before, tmp_path / ".git"])


def test_the_problems_come_in_their_order(tmp_path):
    root, settings_file = tmp_path / "cases", tmp_path / "aid" / "settings.json"
    settings_file.parent.mkdir()
    settings_file.write_text("{}", encoding="utf-8")
    waiting = [folder(root, "250108", NOW - HOUR), folder(root, "250109", NOW - 2 * HOUR)]
    folder(root, "250107", NOW - 3 * HOUR, "review")
    settings = SETTINGS.model_copy(update={"cases_root": root, "reader": "llm", "ms_tenant_id": TENANT_ID, "ms_client_id": CLIENT_ID,
                                           "mail_folder": "AAMkADOrders="})
    aged(digest(root, mailbox_lapsed={"since": "2026-11-12T07:00:00", "logged": "2026-11-12T07:00:00"},
                copilot={"since": "2026-11-12T06:00:00", "error": "Copilot refused: 400 BadRequest", "waiting": 1},
                copilot_cases={waiting[0].name: {"refused": 0, "failed": 1, "read_after": False}}), NOW - timedelta(minutes=11))
    (tmp_path / ".git").mkdir()                                                # the store of the sign-in lies inside a work tree
    lines = status.case_lines(root, NOW)
    state = status.assistant(settings_file, root, settings, NOW)
    assert status.problems(state, lines) == [
        status.Problem("no_pass", NOW - timedelta(minutes=11)), status.Problem("store"), status.Problem("lapsed", datetime(2026, 11, 12, 7, 0)),
        status.Problem("copilot_down", datetime(2026, 11, 12, 6, 0), 2)]        # both cases without a result wait for Copilot
    assert [problem.kind for problem in status.problems(state._replace(stopped=True), lines)] == ["stopped", "store", "lapsed", "copilot_down"]
    given_up = state._replace(launcher=status.Launcher(True, NOW - HOUR, 6, True))
    assert status.problems(given_up, lines)[0] == status.Problem("given_up")
    assert status.problems(state._replace(last_pass=None), lines)[0] == status.Problem("no_pass_ever")
    calm = state._replace(last_pass=NOW - timedelta(minutes=1), late=False, store_refused=False, mailbox=state.mailbox._replace(lapsed=None),
                          copilot=None)
    assert status.problems(calm, lines) == [] and status.problems(calm._replace(refused=3), lines) == [status.Problem("copilot_refused", None, 3)]
    assert set(status.PROBLEMS) == {"stopped", "given_up", "no_pass_ever", "no_pass", "store", "lapsed", "copilot_down", "copilot_refused"}


# --- the log, and what the module imports ---

def test_the_log_tail_is_the_last_lines_newest_first(tmp_path):
    root = tmp_path / "cases"
    root.mkdir()
    assert status.log_tail(root) == []
    lines = [f"2026-11-12T09:{minute:02d}:00 250107_20261112T090000: line {minute}" for minute in range(25)]
    (root / runner.LOG_FILE).write_bytes(("\n".join(lines[:12]) + "\n\n" + "\r\n".join(lines[12:]) + "\n").encode("utf-8"))
    assert status.log_tail(root) == lines[::-1][:20] == lines[:4:-1] and status.log_tail(root, 3) == lines[:-4:-1]
    with open(root / runner.LOG_FILE, "ab") as log:                              # a byte that is no UTF-8, and a last line without its end
        log.write(b"2026-11-12T09:30:00 scan: caf\xe9\n" + b"x" * 200_000 + b"\n2026-11-12T09:31:00 mailbox: last")
    assert status.log_tail(root, 3) == ["2026-11-12T09:31:00 mailbox: last", "x" * 200_000, "2026-11-12T09:30:00 scan: caf�"]
    assert status.log_tail(root, 0) == []


def test_the_status_module_imports_neither_the_runner_nor_the_digest():
    """aid.notify imports the review page, which is to show the status: a fresh interpreter that imports aid.review.status loads
    neither aid.runner, aid.notify nor aid.start, and the names of their files and keys that the module reads are theirs."""
    code = "import sys, aid.review.status; print(sorted(name for name in ('aid.runner', 'aid.notify', 'aid.start') if name in sys.modules))"
    done = subprocess.run([sys.executable, "-B", "-W", "error", "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
    assert (done.returncode, done.stdout.strip()) == (0, "[]"), done.stderr
    assert (status.STATE_FILE, status.LOG_FILE, status.STOP_FILE, status.MAILBOX_LAPSED, status.COPILOT_DOWN, status.COPILOT_CASES,
            status.COULD_NOT_READ) == (runner.STATE_FILE, runner.LOG_FILE, runner.STOP_FILE, runner.MAILBOX_LAPSED, runner.COPILOT_DOWN,
                                       runner.COPILOT_CASES, runner.COULD_NOT_READ)
    assert (status.STOP_FILE, status.START_STATE, status.HEARTBEAT_FRESH) == (start.STOP_FILE, start.STATE_FILE, start.HEARTBEAT_FRESH)
    assert status.UNREAD_SCAN == notify._UNREAD_SCAN
