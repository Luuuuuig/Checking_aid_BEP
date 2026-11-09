"""The runner: the loop that both hosts run, Google Colab for the made-up set (aid/colab) and the company platform for company
documents, and the command that runs it on the company platform.

    python3 -m aid.runner --settings <path> [--poll-seconds N] [--max-passes N]

run_loop makes a pass every poll_seconds until the stop file exists. Each pass, at the time of the Deps clock:
- when the settings name a folder of the work mailbox and the tenant and the client of the Microsoft sign-in (trigger_on of
  aid/sources/microsoft_mail.py), sync_mailbox saves the messages that arrived in that folder into the watched folder as .eml files,
  first, so that a confirmation that arrives opens its case in the same pass. A sign-in that has lapsed (SignInNeeded) is noted in
  the digest state as "mailbox_lapsed": {"since", "logged"}, which the digest names in one line, "Postvak niet verbonden: meld je
  opnieuw aan op het tabblad Instellingen", until a sync of the mailbox ends without an error or the trigger is turned off; it is
  logged at most once an hour (LAPSE_LOG). Any other error of the mailbox is logged as a failing case is, also once more after
  the settings name another mailbox, folder, tenant or client, and the pass goes on with the folder. Such an error leaves a lapse
  as it is: a Microsoft that cannot be reached (MicrosoftUnreachable), or a store of the sign-in that this process may not use
  (StoreRefused), says nothing about the sign-in. While the trigger is off, the stored connection is removed
  (forget_connection), so that a folder connected again later is read from that moment;
- scan_folder (aid/sources/mailbox.py) handles the watched folder of the settings, created when it is missing: PO PDFs go into the
  store, a case is opened under cases_root for each confirmation, an e-mail that answers a question of the aid is filed into its case
  (a real reply puts the case in review and is reported in the next digest, a bounce waits for the escalation), and the files it
  handled move to processed/;
- every case folder under cases_root without steps/08_result.json is run with run_case and its dispositions recorded: the cases the
  scan opened and those an earlier pass that was cut off left without a result. A case cut off after a write or a message is not
  run again: run_case finishes it as review ("run cut off after writes: check the write log"), and it is reported like any case. A
  case already run is left alone, except that a case with a result and without dispositions.csv (a process that ended between the
  two) gets its dispositions and is reported. A case folder with a .running file younger than ten minutes is left alone: the
  capture server (aid/capture_server.py) is running it; an older one is a run that crashed, and the case is resumed. A failure of
  the AI service while a case runs, a CopilotError or a SignInNeeded of its reader or its decider (aid/readers/copilot_client.py),
  is no failure of the case: the case is left without a result, as an interrupted run leaves it, the error is logged as the error
  of a failing case is, and a later pass runs the case again (_run). The cases the service failed are tried after the others, the
  one failed last at the very end, kept in the digest state as "copilot_cases": {case id: {"refused", "failed", "read_after"}}.
  After a failure of the service as a whole, every other case of the pass is left for the next pass without asking the service,
  and the failure is noted in the digest state as "copilot": {"since", "error", "waiting"} until a pass reads a case through
  Copilot; a failure that is the document's own (a refusal of the document, or a failure after Copilot read another case) holds
  back no other case. A case is finished as failed, "Copilot could not read this document: <error>", after three refusals of the
  document, or three failures once Copilot read another case after its first one; failures while nothing else is read never
  finish a case (_run_cases);
- a case of the capture server (its folder holds capture.json) that has its result and was never reported is reported once: the
  server runs its cases itself and never writes the runner's state;
- escalate_pending ends the wait of the questions that bounced or had no answer in time;
- when digest_due says so, the digest of the cases handled and the files skipped since the last digest is made: its HTML is written to
  <cases_root>/digests/<day>.html, for a flow outside the code to pick up, and its text is sent through the mailer of the Deps, or,
  without a mailer, written to <cases_root>/digests/<day>.txt. The digest names a lapse of the mailbox's sign-in, a failing Copilot
  with the cases waiting for it, and the documents Copilot could not read while it read others, in a line each. In the per-case mode a
  lapse or a failure of Copilot that no digest has named yet makes a digest too. A digest state file that cannot be written is logged
  once in a run of the loop and never stops the runner (_save_quietly); a digest made while it cannot be written goes out once all the
  same, as the loop keeps the period it closed until the file holds it (_closed_again). A digest that the work mailbox cannot send
  because the sign-in has lapsed (SignInLapsed of aid/messaging/microsoft_mailer.py) is written through the file mailer into
  <cases_root>/outbox instead, the period is closed as for a digest sent, and the lapse is logged under the same once-an-hour limit.

When run_loop is given the settings file the Deps were built from, it looks at the file's modification time at the start of each
pass. When the time has changed, the file is read again with load_settings and the pass runs with the Deps that `rebuild` makes of
its settings (the command rebuilds them with build_deps), or with the Deps given and the new settings. A file that cannot be read
or validated, or whose Deps cannot be built, leaves the settings in use as they are, with one line in the log that names the fields
at fault, if any, and never a value. So a save on the settings tab of the review page is taken over at the next pass.

What the next digest is to report is kept in <cases_root>/digest_state.json, so that it survives the end of a session: {"last_sent":
the time of the last digest or null, "reported": the ids of the cases handled since then, "skipped": the [file, reason] pairs of the
files skipped since then, each pair once, and, once there is one, "captures": the ids of the capture server's cases reported}. A case escalated by escalate_pending is reported again, as it now waits for the buyer. In
the per-case mode a digest is made only when there is something to report; daily, the digest of a day without cases says so. A state
file that cannot be read, or that lacks a key, starts a fresh state, with a line in the log. An error of one case, of the scan, of
the escalation, of the digest, of the mailbox ("mailbox") or of a changed settings file ("settings") is written to
<cases_root>/runner.log with the case id or the stage, and the pass goes on: the digest that failed is made again at the next pass.
A case that fails is logged once in a run of the loop, and once more when it later succeeds, so that a case that keeps failing does
not fill the log; so is a file that the scan leaves in the inbox as an error, which is tried again at each pass, and a case folder
whose case.json cannot be read, which the scan passes over. Files and folders whose name starts with a dot (".ipynb_checkpoints",
".DS_Store") are no case and no skipped file; the scan leaves them where they are.

The command builds the Deps for company documents with build_deps and runs the loop until the file STOP exists next to the settings
file. A settings file that cannot be read or validated stops it with exit code 2 and the fields at fault, never their values. With
reader "parser" in the settings the reader is the parser and the decider NoDecider; with reader "llm" both ask Microsoft 365 Copilot
(the paragraph below), and without the tenant and the client in the settings the command refuses reader "llm" with exit code 2
(NO_LLM). The writer is RetryingWriter over the ExactApiWriter, with PreparedWriter into <cases_root>/prepared as its fallback,
when exact_base_url is set, the token read from the environment variable AID_EXACT_TOKEN at each call; else the PreparedWriter
alone. The mailer follows send_via: "microsoft" is the MicrosoftMailer (aid/messaging/microsoft_mailer.py), which sends from
settings.mailbox, or from the signed-in account's own mailbox when it is empty; "smtp" is SmtpMailer when smtp_host and smtp_user
are set and AID_MAIL_PASSWORD is in the environment, else FileMailer into <cases_root>/outbox; "file" is FileMailer; and "auto",
when mail_folder is set and the tenant and the client too, is the AutoMailer, which sends each message through the mailbox while an
account is signed in and else as "smtp" does; without a folder "auto" is "smtp". Who is signed in is read from the token cache at
each message, so that building the Deps asks Microsoft nothing; an account whose sign-in has lapsed is still signed in, and its
messages are not sent (aid/messaging/microsoft_mailer.py says why). The Microsoft account of the mailer and of the mailbox is made
of the settings' tenant and client with the store in default_secrets_dir() (aid/connections/secrets.py), whose token cache the
review page fills when the buyer signs in; the store is made at its first use, so that one this process may not use never stops
the command. The clock is datetime.now. The command gives run_loop its settings file, so that the
settings are read again when the file changes. The host, how the command is started and kept running, the folder and where the
credentials are kept follow the company's answer (aid/README.md, "Running on the company side").

The AI service. With reader "llm" the AI service is Microsoft 365 Copilot, CopilotClient of aid/readers/copilot_client.py, asked
with the same Microsoft account as the mailer and the mailbox: the reader is read_supplier_document over that client for a PDF or a
PNG document, and the cart reader (read_cart_html, which needs no model) for a cart page saved as HTML, as demo.live_reader wires
them (llm_reader); the decider is LlmDecider over the same client. Company documents go to no other AI service. Copilot reads the
text layer only: the case of a document without one (a scan) fails as unreadable, as with the parser, and the digest lists it for
the buyer.
"""
import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path

from aid.case import _write_json, load_case        # _write_json: the state is rewritten whole or not at all, as a case file is
from aid.connections.microsoft import SIGN_IN_AGAIN, MicrosoftAccount, SignInNeeded
from aid.connections.secrets import LazySecretStore, default_secrets_dir
from aid.decider import LlmDecider, NoDecider
from aid.execution.exact_api import ExactApiWriter
from aid.execution.writer import PreparedWriter, RetryingWriter, WriterError
from aid.messaging import FileMailer, Mailer, SmtpMailer
from aid.messaging.microsoft_mailer import AutoMailer, MicrosoftMailer, SignInLapsed
from aid.notify import build_digest, digest_due, send_digest
from aid.pipeline import DISPOSITION_FILE, RESULT_FILE, UNREADABLE, CaseResult, Deps, escalate_pending, read_with_parser, record_dispositions, run_case
from aid.po_store import PoStore
from aid.readers.cart import is_cart_page, read_cart_html
from aid.readers.copilot_client import CopilotClient, CopilotError
from aid.readers.llm_client import LlmClient, LlmRequest, LlmResponse, ReaderError
from aid.readers.supplier_llm import read_supplier_document
from aid.schema import Document
from aid.settings import Settings, load_settings
from aid.sources.mailbox import scan_folder
from aid.sources.microsoft_mail import forget_connection, sync_mailbox, trigger_on

LOG_FILE = "runner.log"
RUNNING_FILE = ".running"                        # in a case folder while the capture server runs the case: {"pid", "started"}
RUNNING_FRESH = timedelta(minutes=10)            # a .running file at least this old is a run that crashed: the case is resumed
CAPTURE_FILE = "capture.json"                    # what marks a case of the capture server (aid/capture_server.py)
STATE_FILE = "digest_state.json"
DIGESTS_FOLDER = "digests"
STOP_FILE = "STOP"                               # next to the settings file, for the command
TOKEN_ENV = "AID_EXACT_TOKEN"
PASSWORD_ENV = "AID_MAIL_PASSWORD"
ALREADY_RUN = "case already run"                 # the start of run_case's ValueError for a case that holds its result
SCAN = "scan"                                    # the stage of the scan, as runner.log names it
MAILBOX = "mailbox"                              # the stage of the work mailbox
SETTINGS = "settings"                            # the stage of a settings file that changed
OUTBOX = "outbox"                                # under cases_root: the messages written as files instead of sent
MAILBOX_LAPSED = "mailbox_lapsed"                # the key of the digest state while the sign-in of the mailbox has lapsed
LAPSE_LOG = timedelta(hours=1)                   # a lapsed sign-in is logged at most once in this time
ERROR = "error: "                                # the start of the reason of a file the scan left in the inbox after an exception
NO_LLM = "reader 'llm' needs the Copilot sign-in: fill the tenant and the client on the Settings tab and sign in for Copilot"
SERVICE_FAILURES = (CopilotError, SignInNeeded)  # a failure of the AI service while a case runs: the case runs again at a later pass
COPILOT_DOWN = "copilot"                         # the key of the digest state while the AI service fails: {"since", "error", "waiting"}
COPILOT_CASES = "copilot_cases"                  # the key of the cases the AI service failed, in the order they are tried last
COPILOT_CAP = 3                                  # the failures after which a case is finished as failed (_run_cases)
COULD_NOT_READ = "Copilot could not read this document: {error}"


def run_loop(deps: Deps, stop_file: Path, poll_seconds: float = 60.0, max_passes: int | None = None, *, settings_file: Path | None = None,
             rebuild: Callable[[Settings], Deps] | None = None, account: MicrosoftAccount | None = None, session=None) -> int:
    """Make a pass (see the module docstring) every poll_seconds until the stop file exists, or until max_passes passes are made,
    and return the number of passes made. The stop file is looked for before each pass.

    settings_file is the file the settings of the Deps were read from, read again when it changes, and rebuild makes the Deps of its
    new settings (see the module docstring). account is the Microsoft account the work mailbox is read with, and session the
    requests session Graph is called through; without an account one is made of the settings (_microsoft_account)."""
    loop, passes = _Loop(deps, settings_file, rebuild, account, session), 0
    while (max_passes is None or passes < max_passes) and not Path(stop_file).exists():
        loop.reload()
        _pass(loop)
        passes += 1
        if max_passes is None or passes < max_passes:
            time.sleep(poll_seconds)
    return passes


class _Loop:
    """What a run of the loop keeps from one pass to the next: the Deps, the cases and the stages that failed in it (each logged
    once), the settings file with its modification time, the Microsoft account of the work mailbox, and the period the last digest
    closed while the state file could not keep it (_closed_again)."""

    def __init__(self, deps: Deps, settings_file: Path | None = None, rebuild: Callable[[Settings], Deps] | None = None,
                 account: MicrosoftAccount | None = None, session=None):
        self.deps, self.rebuild, self.given, self.session = deps, rebuild, account, session
        self.settings_file = Path(settings_file) if settings_file is not None else None
        self.modified = _modified(self.settings_file)
        self.failed: set[str] = set()
        self.made: tuple[tuple, MicrosoftAccount] | None = None     # the account made of the settings, with its tenant and client
        self.closed: dict | None = None          # {"last_sent", "reported", "skipped"} of a digest whose state was not saved

    def reload(self) -> None:
        """Read the settings file again when it has changed since the Deps were built of it (see the module docstring)."""
        if self.settings_file is None or (modified := _modified(self.settings_file)) == self.modified:
            return
        self.modified = modified
        root, now = Path(self.deps.settings.cases_root), self.deps.clock()
        try:
            settings = load_settings(self.settings_file)
        except (OSError, ValueError) as exc:     # load_settings names the fields at fault, never a value
            problem = type(exc).__name__ if isinstance(exc, OSError) else " ".join(str(exc).split())
            _log(root, now, SETTINGS, f"{problem}; the settings in use are kept")
            return
        try:
            deps = self.rebuild(settings) if self.rebuild is not None else self.deps.model_copy(update={"settings": settings})
        except Exception as exc:                 # its text may quote a value of the settings (the Exact writer's checks do)
            _log(root, now, SETTINGS, f"{type(exc).__name__}: the changed settings cannot be used; the settings in use are kept")
            return
        if _connection(settings) != _connection(self.deps.settings):
            self.failed.discard(MAILBOX)         # another mailbox or folder: its errors are new
        self.deps = deps

    def account(self, settings: Settings) -> MicrosoftAccount:
        """The account the mailbox is read with: the one given, else the one made of the settings' tenant and client."""
        if self.given is not None:
            return self.given
        key = (settings.ms_tenant_id, settings.ms_client_id)
        if self.made is None or self.made[0] != key:
            self.made = (key, _microsoft_account(settings))
        return self.made[1]


def _pass(loop: _Loop) -> None:
    deps, failed = loop.deps, loop.failed
    settings, now = deps.settings, deps.clock()
    root = Path(settings.cases_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log(root, now, "cases root", exc)
        return
    state = _load_state(root, now)
    _closed_again(state, loop)
    if not trigger_on(settings):                                          # no mailbox is read: no lapse to name, no connection
        if MAILBOX_LAPSED in state:
            del state[MAILBOX_LAPSED]
            _save_quietly(root, state, now, failed)
        try:
            forget_connection(root)
        except OSError as exc:
            if MAILBOX not in failed:
                _log(root, now, MAILBOX, exc)
                failed.add(MAILBOX)
    else:
        try:
            _read_mailbox(loop, root, state, now)
        except Exception as exc:                 # the digest state could not be saved: the pass goes on with the folder
            _log(root, now, MAILBOX, exc)
    _guarded(root, now, failed, SCAN, lambda: _scan(deps, root, state, now, failed))
    _run_cases(deps, root, state, now, failed)
    for case_id in _without_dispositions(root, deps.clock()):
        _guarded(root, now, failed, case_id, lambda: _recover(root, state, case_id))
    _guarded(root, now, failed, "captures", lambda: _report_captures(root, state, deps.clock()))
    try:
        for case_id, reason in escalate_pending(root, now, settings):
            if reason.startswith(f"{UNREADABLE}:"):
                _log(root, now, case_id, reason)
            else:
                _report(root, state, case_id)
    except Exception as exc:
        _log(root, now, "escalation", exc)
    try:
        _digest(loop, root, state, now)
    except Exception as exc:
        _log(root, now, "digest", exc)


def _guarded(root: Path, now: datetime, failed: set[str], case_id: str, work) -> Exception | None:
    """Do the work on the case, and return the exception that stopped it, if any. Its first failure in this run of the loop is
    logged, and so is its first success after one."""
    try:
        work()
    except Exception as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(ALREADY_RUN):
            return None
        if case_id not in failed:
            _log(root, now, case_id, exc)
            failed.add(case_id)
        return exc
    if case_id in failed:
        _log(root, now, case_id, "ran after an earlier failure")
        failed.discard(case_id)
    return None


def _run_cases(deps: Deps, root: Path, state: dict, now: datetime, failed: set[str]) -> None:
    """Run every case without a result (_run), each guarded, and keep in the state what the AI service's failures call for.

    The order: the cases the AI service has not failed come first, by name; then the cases it failed (COPILOT_CASES), the case
    failed last at the end, so that a case that keeps failing never holds back the others. Only a failure of the chat that carries
    the document (CopilotError.chat) can be the document's own: when it is a refusal of the document (CopilotError.case_only), or
    when Copilot read another case since the case's first failure; the pass then goes on with the other cases. Any other failure of
    the AI service (SERVICE_FAILURES) is a failure of the service as a whole: a lapsed sign-in, a failure to get the token or to
    create the conversation, whatever was read before, and a failure of the chat while nothing else was read since the case's first
    failure. The other cases of the pass are then left for the next pass without asking the service again, and only the case that
    failed is logged. A case is finished as failed (_finish) when the service refused it as a document COPILOT_CAP times, or failed
    its chat COPILOT_CAP times in all once Copilot had read another case after its first failure; only failures of the chat count,
    so an outage or a lapse never finishes a case. A case counts as read when the model answered in its run and the service did not
    fail it.

    The note on the service (COPILOT_DOWN, for the digest's line "Copilot werkt niet") is set by a failure of the service as a whole,
    with the number of cases it left waiting, and keeps the time of its first such pass; a pass that reads a case through Copilot
    removes it, unless the sign-in, the token or the conversation failed in it after that, and so do Deps without a model decider. A
    state file that cannot be written is logged once in a run of the loop and the pass goes on (_save_quietly)."""
    # The .running checks read the clock when they run: a capture that started during the scan is newer than the pass's start.
    pending = _without_result(root, deps.clock())
    records = {case_id: dict(record) for case_id, record in state.get(COPILOT_CASES, {}).items() if case_id in pending}
    before = (state.get(COPILOT_DOWN), state.get(COPILOT_CASES))
    down, read, outage, whole, waiting = False, False, None, False, 0
    for case_id in [case_id for case_id in pending if case_id not in records] + list(records):
        if down:
            waiting += 1
            continue
        answered: list[bool] = []
        failure = _guarded(root, now, failed, case_id, lambda: _run(deps, root, state, case_id, answered))
        if not isinstance(failure, SERVICE_FAILURES):
            if answered:
                read = True
                for other, record in records.items():
                    if other != case_id:
                        record["read_after"] = True
            continue
        of_the_chat = isinstance(failure, CopilotError) and failure.chat
        refused = of_the_chat and failure.case_only
        record = records.pop(case_id, {"refused": 0, "failed": 0, "read_after": False})
        records[case_id] = record                                        # tried last from now on
        if not of_the_chat:                                              # the sign-in, the token or the conversation: the service
            down, outage, whole, waiting = True, failure, True, waiting + 1
            continue
        record["refused" if refused else "failed"] += 1
        if record["refused"] >= COPILOT_CAP or (record["refused"] + record["failed"] >= COPILOT_CAP and record["read_after"]):
            _finish(deps, root, state, now, failed, case_id, failure)
            continue
        if refused or record["read_after"]:                              # the document's own failure: the pass goes on
            continue
        down, outage, waiting = True, failure, waiting + 1
    records = {case_id: record for case_id, record in records.items() if not (root / case_id / "steps" / RESULT_FILE).exists()}
    if not isinstance(deps.decider, LlmDecider) or (read and not whole):
        state.pop(COPILOT_DOWN, None)
    elif outage is not None:
        since = (state.get(COPILOT_DOWN) or {}).get("since", now.isoformat())
        state[COPILOT_DOWN] = {"since": since, "error": " ".join(str(outage).split()), "waiting": waiting}
    if records:
        state[COPILOT_CASES] = records
    else:
        state.pop(COPILOT_CASES, None)
    if (state.get(COPILOT_DOWN), state.get(COPILOT_CASES)) != before:
        _save_quietly(root, state, now, failed)


def _finish(deps: Deps, root: Path, state: dict, now: datetime, failed: set[str], case_id: str, failure: Exception) -> None:
    """Finish a case the AI service could not read as failed: an ordinary failed result through run_case, whose reader raises
    ReaderError COULD_NOT_READ, reported and with its dispositions; one line in the log."""
    error = COULD_NOT_READ.format(error=" ".join(str(failure).split()))

    def refused(path: Path, kind: str):
        raise ReaderError(error)
    try:
        _run(deps.model_copy(update={"reader": refused}), root, state, case_id)
    except Exception as exc:
        _log(root, now, case_id, exc)
        return
    _log(root, now, case_id, f"finished as failed after {COPILOT_CAP} failures: {error}")
    failed.discard(case_id)


def _save_quietly(root: Path, state: dict, now: datetime, failed: set[str]) -> bool:
    """Save the state and say whether it was saved; a file that cannot be written is logged once in a run of the loop, and the pass
    goes on."""
    try:
        _save_state(root, state)
    except OSError as exc:
        if STATE_FILE not in failed:
            _log(root, now, STATE_FILE, exc)
            failed.add(STATE_FILE)
        return False
    failed.discard(STATE_FILE)
    return True


def _read_mailbox(loop: _Loop, root: Path, state: dict, now: datetime) -> None:
    """Save what arrived in the folder of the work mailbox into the watched folder (sync_mailbox), before the scan. A lapsed sign-in
    is noted in the state for the digest and logged at most once in LAPSE_LOG, and the first sync without an error after it ends the
    lapse, with a line; any other error is logged once in a run of the loop, as a failing case is, and so is the first read after
    it, and leaves a lapse as it is (see the module docstring)."""
    settings = loop.deps.settings
    try:
        error = sync_mailbox(loop.account(settings), settings, now, session=loop.session).error
    except SignInNeeded:
        _lapsed(root, state, now, f"not connected, the Microsoft sign-in has lapsed: {SIGN_IN_AGAIN}")
        return
    except Exception as exc:                     # MicrosoftUnreachable, StoreRefused and the like: nothing learnt of the sign-in
        error = f"{type(exc).__name__}: {' '.join(str(exc).split())}"
    if error:
        if MAILBOX not in loop.failed:
            _log(root, now, MAILBOX, error)
            loop.failed.add(MAILBOX)
        return
    if MAILBOX_LAPSED in state:
        del state[MAILBOX_LAPSED]
        _save_state(root, state)
        _log(root, now, MAILBOX, "signed in again: the mailbox is read")
        loop.failed.discard(MAILBOX)
    elif MAILBOX in loop.failed:
        _log(root, now, MAILBOX, "read after an earlier failure")
        loop.failed.discard(MAILBOX)


def _lapsed(root: Path, state: dict, now: datetime, line: str) -> None:
    """Note in the state that the sign-in has lapsed, since now unless it had before, and log the line unless a line about the
    lapse was logged within LAPSE_LOG."""
    lapse = state.get(MAILBOX_LAPSED)
    if lapse is None:
        lapse = state[MAILBOX_LAPSED] = {"since": now.isoformat()}
    if "logged" not in lapse or _elapsed(datetime.fromisoformat(lapse["logged"]), now) >= LAPSE_LOG:
        lapse["logged"] = now.isoformat()
        _log(root, now, MAILBOX, line)
    _save_state(root, state)


def _connection(settings: Settings) -> tuple:
    """What names the connection to the work mailbox."""
    return settings.ms_tenant_id, settings.ms_client_id, settings.mailbox, settings.mail_folder


def _elapsed(earlier: datetime, now: datetime) -> timedelta:
    """The time from earlier to now; a time without a zone is local time, so that one with a zone and one without compare."""
    if (earlier.utcoffset() is None) != (now.utcoffset() is None):
        earlier, now = earlier.astimezone(), now.astimezone()
    return now - earlier


def _scan(deps: Deps, root: Path, state: dict, now: datetime, failed: set[str]) -> None:
    """Scan the watched folder, report each case that a supplier's reply put in review, and note what the scan skipped in the state,
    each (file, reason) once, since a file that stays in the inbox as an error is tried again at every pass. Such an error is logged once in a run of the loop, as a failing case is."""
    settings = deps.settings
    inbox = Path(settings.watched_folder)
    inbox.mkdir(parents=True, exist_ok=True)
    scan = scan_folder(inbox, deps.store, root, settings, now, deps.decider)
    for case_id in scan.answered:                # a real reply put its case in review: the next digest reports it
        _report(root, state, case_id)
    for name, reason in scan.skipped:
        if _hidden(name.split("/")[0]):
            continue
        if [name, reason] not in state["skipped"]:
            state["skipped"].append([name, reason])
        if reason.startswith(ERROR) and (key := f"{SCAN} {name}") not in failed:
            _log(root, now, key, reason)
            failed.add(key)
    _save_quietly(root, state, now, failed)


def _run(deps: Deps, root: Path, state: dict, case_id: str, answered: list[bool] | None = None) -> None:
    """Run a case, report it and record its dispositions. run_case makes any exception of a step the failure of the case; a failure of
    the AI service (SERVICE_FAILURES) in the reader or in the decider is carried past it instead (_carrying), as an interruption is,
    so that the case keeps no result and no audit record of the attempt, and it is raised here for _guarded to log, the case not
    reported. Such a failure comes before any write or message of the case: the steps that ask a model come before them. True is
    appended to `answered` when the model answered during the run."""
    case = load_case(root, case_id)
    try:
        result = run_case(case, _carrying(deps, answered if answered is not None else []))
    except _ServiceFailed as failed:
        raise failed.error from None
    _report(root, state, case_id)
    record_dispositions(case, result)


class _ServiceFailed(BaseException):
    """A failure of the AI service while a case runs, carried past run_case, whose steps catch every Exception (see _run)."""

    def __init__(self, error: Exception):
        super().__init__(type(error).__name__)
        self.error = error


def _carrying(deps: Deps, answered: list[bool]) -> Deps:
    """The Deps of one case's run, whose reader, and whose decider when it asks a model, raise a failure of the AI service as
    _ServiceFailed, and append True to `answered` when the model answers: a reading with the model's response, a ReaderError that
    carries one (an answer that could not be used), or a decision. The decider stays an LlmDecider over a client of the same name, so
    that the audit names its model as before."""
    reader, decider = deps.reader, deps.decider

    def read(path: Path, kind: str):
        try:
            reading = reader(path, kind)
        except SERVICE_FAILURES as error:
            raise _ServiceFailed(error) from None
        except ReaderError as error:
            if error.response is not None:
                answered.append(True)
            raise
        if isinstance(reading, tuple):
            answered.append(True)
        return reading
    if isinstance(decider, LlmDecider):
        decider = LlmDecider(_CarryingClient(decider.client, answered))
    return deps.model_copy(update={"reader": read, "decider": decider})


class _CarryingClient:
    """The client of a model decider during a case's run: its answers as they are, noted in `answered`, and a failure of the AI
    service as _ServiceFailed."""

    def __init__(self, client: LlmClient, answered: list[bool]):
        self.client, self.name, self.answered = client, client.name, answered

    def complete(self, req: LlmRequest) -> LlmResponse:
        try:
            response = self.client.complete(req)
        except SERVICE_FAILURES as error:
            raise _ServiceFailed(error) from None
        self.answered.append(True)
        return response


def _recover(root: Path, state: dict, case_id: str) -> None:
    """Record the dispositions of a case that has its result and none, and report it."""
    case = load_case(root, case_id)
    result = CaseResult.model_validate_json((case.root / "steps" / RESULT_FILE).read_text(encoding="utf-8"))
    record_dispositions(case, result)
    _report(root, state, case_id)


def _digest(loop: _Loop, root: Path, state: dict, now: datetime) -> None:
    """Make the digest when it is due and there is a reason to, and start a new period in the state. When the state file cannot keep
    the new period, the loop keeps it (_closed_again), so that the digest goes out once all the same."""
    deps = loop.deps
    settings = deps.settings
    last_sent = datetime.fromisoformat(state["last_sent"]) if state["last_sent"] else None
    if not digest_due(now, last_sent, settings):
        return
    lapse = state.get(MAILBOX_LAPSED) if trigger_on(settings) else None
    copilot = state.get(COPILOT_DOWN)
    refused = sum(1 for record in state.get(COPILOT_CASES, {}).values() if record["refused"] or (record["read_after"] and record["failed"]))
    unnamed = any(note is not None and (last_sent is None or _elapsed(last_sent, datetime.fromisoformat(note["since"])) > timedelta(0))
                  for note in (lapse, copilot))
    if settings.digest == "per_case" and not state["reported"] and not state["skipped"] and not unnamed:
        return
    results = []
    for case_id in state["reported"]:
        try:
            results.append(CaseResult.model_validate_json((root / case_id / "steps" / RESULT_FILE).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            _log(root, now, case_id, exc)
    digest = build_digest(results, [(name, reason) for name, reason in state["skipped"]], settings, now.date(),
                          mailbox_lapsed=lapse is not None, copilot_down=(copilot["error"], copilot["waiting"]) if copilot else None,
                          copilot_refused=refused)
    folder = root / DIGESTS_FOLDER
    folder.mkdir(exist_ok=True)
    (folder / f"{digest.period}.html").write_text(digest.html, encoding="utf-8")       # for a flow to pick up; the runner sends no HTML
    if deps.mailer is not None:
        try:
            send_digest(digest, deps.mailer, settings)
        except SignInLapsed as error:            # the work mailbox cannot send it: the digest is written as a file, as without SMTP
            send_digest(digest, FileMailer(root / OUTBOX, sender=settings.aid_email), settings)
            try:
                _lapsed(root, state, now, f"the digest is written to {OUTBOX}/, as the work mailbox cannot send it: {error}")
            except OSError:                      # the state file: the save below names it
                pass
    else:
        (folder / f"{digest.period}.txt").write_text(digest.text, encoding="utf-8")
    closed = {"last_sent": now.isoformat(), "reported": list(state["reported"]), "skipped": [list(pair) for pair in state["skipped"]]}
    state.update(last_sent=now.isoformat(), reported=[], skipped=[])
    loop.closed = None if _save_quietly(root, state, now, loop.failed) else closed


def _closed_again(state: dict, loop: _Loop) -> None:
    """Put back into the state read from the file the period that the last digest closed when the file could not keep it: its time,
    and the cases and files that digest named taken out; nothing once the file holds that time."""
    closed = loop.closed
    if closed is None:
        return
    if state["last_sent"] == closed["last_sent"]:
        loop.closed = None
        return
    state["last_sent"] = closed["last_sent"]
    state["reported"] = [case_id for case_id in state["reported"] if case_id not in closed["reported"]]
    state["skipped"] = [pair for pair in state["skipped"] if list(pair) not in closed["skipped"]]


def _without_result(root: Path, now: datetime) -> list[str]:
    """The ids of the case folders under root that hold no result, in the order of their names, except a case the capture server is
    running (a .running file younger than RUNNING_FRESH at now, the time of the clock)."""
    return [path.name for path in _case_folders(root)
            if not (path / "steps" / RESULT_FILE).exists() and not _running(path, now)]


def _without_dispositions(root: Path, now: datetime) -> list[str]:
    """The ids of the case folders under root that hold a result and no dispositions.csv, in the order of their names, except a case
    the capture server is running."""
    return [path.name for path in _case_folders(root) if (path / "steps" / RESULT_FILE).exists()
            and not (path / DISPOSITION_FILE).exists() and not _running(path, now)]


def _running(folder: Path, now: datetime) -> bool:
    """Whether the capture server is running the case of the folder: its .running file names a start less than RUNNING_FRESH away
    from now, before it or after it (a start a little in the future is the clock of another process, or a run that began after the
    caller read its clock). A file that cannot be read counts by the time it was last modified."""
    path = folder / RUNNING_FILE
    if not path.is_file():
        return False
    try:
        started = datetime.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["started"])
        return abs(now - started) < RUNNING_FRESH
    except (OSError, ValueError, KeyError, TypeError):
        try:
            return abs(datetime.now().timestamp() - path.stat().st_mtime) < RUNNING_FRESH.total_seconds()
        except OSError:
            return False


def _report_captures(root: Path, state: dict, now: datetime) -> None:
    """Report each case of the capture server that has its result, is no longer running and was never reported, once: the server
    runs its cases itself and never writes the runner's state, so the runner reports them. The ids reported are kept in the state's
    "captures" list."""
    for path in _case_folders(root):
        if ((path / CAPTURE_FILE).is_file() and (path / "steps" / RESULT_FILE).exists() and path.name not in state.get("captures", [])
                and not _running(path, now)):
            _report(root, state, path.name)


def _case_folders(root: Path) -> list[Path]:
    return sorted(path for path in root.iterdir() if path.is_dir() and not _hidden(path.name) and (path / "case.json").is_file())


def _hidden(name: str) -> bool:
    return name.startswith(".")


def _modified(path: Path | None) -> int | None:
    """The modification time of the file in nanoseconds; None when there is no file to look at."""
    try:
        return path.stat().st_mtime_ns if path is not None else None
    except OSError:
        return None


def _report(root: Path, state: dict, case_id: str) -> None:
    """Note that the next digest reports the case, and save the state at once. A case of the capture server is noted in "captures"
    as well, so that _report_captures does not report it again."""
    if case_id not in state["reported"]:
        state["reported"].append(case_id)
    if (root / case_id / CAPTURE_FILE).is_file() and case_id not in state.setdefault("captures", []):
        state["captures"].append(case_id)
    _save_state(root, state)


def _load_state(root: Path, now: datetime) -> dict:
    """The digest state of root; a fresh one when there is none, and, with a line in the log, when it cannot be read or used."""
    fresh = {"last_sent": None, "reported": [], "skipped": []}
    path = root / STATE_FILE
    if not path.exists():
        return fresh
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("it holds no object")
        if missing := [key for key in fresh if key not in state]:
            raise ValueError(f"it has no {', '.join(missing)}")
        if state["last_sent"] is not None:
            datetime.fromisoformat(state["last_sent"])
        if not isinstance(state["reported"], list) or not isinstance(state.get("captures", []), list) or not all(isinstance(skip, list) and len(skip) == 2 for skip in state["skipped"]):
            raise ValueError("its lists are not lists of case ids and of [file, reason] pairs")
        if (lapse := state.get(MAILBOX_LAPSED)) is not None:             # only while the sign-in of the mailbox has lapsed
            if not isinstance(lapse, dict) or not all(isinstance(lapse.get(key), str) for key in ("since", "logged")):
                raise ValueError(f"its {MAILBOX_LAPSED} is not the time of a lapse")
            for key in ("since", "logged"):
                datetime.fromisoformat(lapse[key])
        if (copilot := state.get(COPILOT_DOWN)) is not None:              # only while the AI service fails
            if not (isinstance(copilot, dict) and isinstance(copilot.get("since"), str) and isinstance(copilot.get("error"), str)
                    and type(copilot.get("waiting")) is int and copilot["waiting"] >= 0):
                raise ValueError(f"its {COPILOT_DOWN} is not a failure of the AI service")
            datetime.fromisoformat(copilot["since"])
        if (cases := state.get(COPILOT_CASES)) is not None:              # only while the AI service has failed a case
            if not (isinstance(cases, dict) and all(
                    isinstance(record, dict) and set(record) == {"refused", "failed", "read_after"}
                    and type(record["refused"]) is int and type(record["failed"]) is int and type(record["read_after"]) is bool
                    for record in cases.values())):
                raise ValueError(f"its {COPILOT_CASES} are not the failures of the AI service by case")
    except (OSError, ValueError, TypeError) as exc:
        _log(root, now, STATE_FILE, f"{type(exc).__name__}: {' '.join(str(exc).split())}; a fresh state is started")
        return fresh
    return state


def _save_state(root: Path, state: dict) -> None:
    _write_json(root / STATE_FILE, state)


def _log(root: Path, now: datetime, what: str, error: Exception | str) -> None:
    """Add a line to runner.log: the time, the case id or the stage, and the error."""
    shown = error if isinstance(error, str) else f"{type(error).__name__}: {' '.join(str(error).split())}"
    line = f"{now.isoformat()} {what}: {shown}"
    try:
        with open(root / LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:                              # no log to write to (the cases root cannot be made): the console has the line
        print(line, file=sys.stderr)


# --- the command on the company platform ---

def build_deps(settings: Settings, environ: Mapping[str, str], account: MicrosoftAccount | None = None, session=None) -> Deps:
    """The Deps of the command for company documents (see the module docstring); `environ` is the environment that the Exact token
    and the mailbox password are looked up in, `account` the Microsoft account that Copilot is asked with and a MicrosoftMailer
    sends through, made of the settings when none is given, and `session` the requests session that Copilot is called through
    (None: requests). Raises ValueError for reader "llm" without the tenant and the client (NO_LLM) and for Exact settings the
    writer refuses."""
    reader, decider = read_with_parser, NoDecider()
    if settings.reader == "llm":
        if not (settings.ms_tenant_id and settings.ms_client_id):
            raise ValueError(NO_LLM)
        account = account or _microsoft_account(settings)
        client = CopilotClient(account, session=session)
        reader, decider = llm_reader(client), LlmDecider(client)
    prepared = PreparedWriter(settings.cases_root / "prepared")
    writer = prepared
    if settings.exact_base_url:
        writer = RetryingWriter(ExactApiWriter(settings.exact_base_url, settings.exact_endpoints, lambda: _token(environ),
                                               fields=settings.exact_fields), prepared)
    return Deps(settings=settings, store=PoStore(settings.po_store_root, settings.authorization_limit), reader=reader,
                decider=decider, writer=writer, mailer=_mailer(settings, environ, account), clock=datetime.now)


def llm_reader(client: LlmClient) -> Callable[[Path, str], Document | tuple[Document, LlmResponse]]:
    """The reader of a run with a model, as demo.live_reader wires it: read_supplier_document over the client for a PDF or a PNG
    document, and the cart reader (read_cart_html), which needs no model, for a cart page saved as HTML."""
    def reader(path: Path, kind: str):
        if is_cart_page(path, kind):
            return read_cart_html(path)
        return read_supplier_document(path, client, kind)
    return reader


def _mailer(settings: Settings, environ: Mapping[str, str], account: MicrosoftAccount | None) -> Mailer:
    """The mailer that send_via names (see the module docstring). Nothing here asks Microsoft anything: "auto" and the From of the
    work mailbox are decided at each message."""
    # SmtpMailer reads the password from os.environ at each send, not from `environ`; main passes os.environ, so the two agree.
    if settings.send_via != "file" and settings.smtp_host and settings.smtp_user and PASSWORD_ENV in environ:
        other = SmtpMailer(settings.smtp_host, settings.smtp_port, settings.smtp_user, password_env=PASSWORD_ENV, sender=settings.aid_email)
    else:
        other = FileMailer(settings.cases_root / OUTBOX, sender=settings.aid_email)
    if settings.send_via == "microsoft" or (settings.send_via == "auto" and trigger_on(settings)):
        microsoft = MicrosoftMailer(account or _microsoft_account(settings), settings.mailbox, "", clock=datetime.now)
        return microsoft if settings.send_via == "microsoft" else AutoMailer(microsoft, other)
    return other


def _microsoft_account(settings: Settings) -> MicrosoftAccount:
    """The Microsoft account of the settings' tenant and client, with the token cache of the store in default_secrets_dir(), made
    at its first use (LazySecretStore): a store this process may not use is then an error of the mailbox, logged, and never a
    reason for the runner not to start."""
    return MicrosoftAccount(settings.ms_tenant_id, settings.ms_client_id, LazySecretStore(default_secrets_dir()))


def _token(environ: Mapping[str, str]) -> str:
    """The Exact token, read from the environment at each call."""
    token = environ.get(TOKEN_ENV)
    if not token:
        raise WriterError(f"the environment variable {TOKEN_ENV} is not set")
    return token


def main(argv: list[str] | None = None) -> int:
    """Run the loop with the Deps of build_deps until STOP exists next to the settings file; 0, or 2 when the settings file cannot be
    read or validated (load_settings names the fields, never their values) or the Deps are refused."""
    parser = argparse.ArgumentParser(description="Run the checking aid on the watched folder until the file STOP exists next to the settings.")
    parser.add_argument("--settings", type=Path, required=True, help="settings.json")
    parser.add_argument("--poll-seconds", type=float, default=60.0, help="seconds between passes (default 60)")
    parser.add_argument("--max-passes", type=int, default=None, help="stop after this many passes")
    args = parser.parse_args(argv)
    try:                                         # a settings file missing or invalid (named by field, never by value), or Deps refused
        deps = build_deps(load_settings(args.settings), os.environ)
    except (OSError, ValueError) as exc:
        print(f"aid.runner: {exc}", file=sys.stderr)
        return 2
    stop_file = args.settings.resolve().parent / STOP_FILE
    passes = run_loop(deps, stop_file, poll_seconds=args.poll_seconds, max_passes=args.max_passes, settings_file=args.settings,
                      rebuild=lambda settings: build_deps(settings, os.environ))
    print(f"{passes} {'pass' if passes == 1 else 'passes'}; stopped" + (f" by {stop_file}" if stop_file.exists() else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
