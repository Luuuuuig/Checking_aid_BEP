"""The runner: the loop that both hosts run, Google Colab for the made-up set (aid/colab) and the company platform for company
documents, and the command that runs it on the company platform.

    python3 -m aid.runner --settings <path> [--poll-seconds N] [--max-passes N]

run_loop first empties <cases_root>/tmp/work, where the runner keeps the attachments of an e-mail and the PDF that Copilot reads
for a moment, so that what a runner that was ended in the middle of such a copy left there never stays (_empty_work; a copy that
cannot be removed is one line in the log, and the runner goes on), and in the same way removes from the watched folder the copies
that drops of the review page cut off left there, drop's own temporary files older than ten minutes (_empty_drops, remove_leftovers
of aid/sources/dropin.py). Then it makes a pass every poll_seconds until the stop file exists. Each pass, at the time of the Deps
clock:
- when the settings name a folder of the work mailbox and the tenant and the client of the Microsoft sign-in (trigger_on of
  aid/sources/microsoft_mail.py), sync_mailbox saves the messages that arrived in that folder into the watched folder as .eml files,
  first, so that a confirmation that arrives opens its case in the same pass. A sign-in that has lapsed (SignInNeeded) is noted in
  the digest state as "mailbox_lapsed": {"since", "logged"}, which the digest names in one line, "Postvak niet verbonden: meld u
  opnieuw aan op het tabblad Instellingen", until a sync of the mailbox ends without an error or the trigger is turned off; it is
  logged at most once an hour (LAPSE_LOG). Any other error of the mailbox is logged as a failing case is, also once more after
  the settings name another mailbox, folder, tenant or client, and the pass goes on with the folder. Such an error leaves a lapse
  as it is: a Microsoft that cannot be reached (MicrosoftUnreachable), or a store of the sign-in that this process may not use
  (StoreRefused), says nothing about the sign-in. While the trigger is off, the stored connection is removed
  (forget_connection), so that a folder connected again later is read from that moment;
- scan_folder (aid/sources/mailbox.py) handles the watched folder of the settings, created when it is missing: PO PDFs go into the
  store, a case is opened under cases_root for each confirmation, an e-mail that answers a question of the aid is filed into its case
  (a real reply puts the case in review and is reported in the next digest, a bounce waits for the escalation, and an automatic
  reply or an e-mail of the aid's own side, the address of the account signed in to the work mailbox among them, changes
  nothing), and the files it handled move to processed/;
- what became of each document of the scan (ScanResult.events) is appended to <cases_root>/intake.jsonl, a line each, which the review
  page's tab "Bestanden" shows (aid/review/intake.py): the file as the skipped list names it, the outcome and its detail, at the time
  of the pass, written first, before the scan's cases are reported and its skipped list is noted. A file that stays in the watched
  folder (an error, or a file that was handled and could not be moved) is handled again at every pass, so its lines are written once
  (_intake). The record of what was written keeps, for each file that stayed after a pass, by its name: its sha256, or none while
  its content cannot be read (another program holds it), the time of the pass that wrote its first line as a staying file, and the
  lines of it that intake.jsonl holds; a line that waits to be written (below) is added once it is written. Within a run of the loop
  the loop's own record decides, its lines that wait counted (_Intake, as a closed digest period is kept), so that a state that cannot
  be saved repeats no line; the copy in the digest state, under "intake", is what a new run starts from, so that a run that ended while
  lines waited writes the line of a file that still stays. While a
  file of that name has that content (read before the scan; a file whose content cannot be read counts as the same file until it can
  be read), a line that says the same as the last line written of its document is not written again, and "received before" of a
  document is kept back when its last line told a filing (FILINGS) and written after any other line, such as an error, so that the
  table's last word on a document is true. A file with another content under the same name gets its lines as a new one, and a file
  that left the folder is forgotten, so that the same file given again later has its line again. A file that stays is not tried for
  ever: a pass that tries it again, finds it still an error, and comes STAY_DAYS days or more after its first line as a staying file,
  moves it to processed/ as a handled file with one line, the outcome "set_aside" with the whole days since then, says so in
  runner.log ("set aside <file>", with its days) and names it once in the state's skipped list for the next digest, as "set aside
  after <n> days without being read" (SET_ASIDE_REASON) in place of its earlier reasons; one that cannot be moved then is tried again
  at the next pass, and its failure logged once in a run of the loop as "set aside <file>". Lines that
  cannot be written wait in the loop, each with the time of its own pass, and are written before the lines of the next write, in
  their order; the stage "intake file" is logged once in a run of the loop with the number of lines that wait, and once more when they
  are written. They are lost only when the process ends before they could be written;
- the cases that wait for their PO are checked again (_recheck_waiting), for the POs the scan filed in this pass: the number of each
  PO PDF it filed, also one the store held already, and of each PO whose look an export filed as new or changed (ScanResult
  stored_exports). At the first pass of a run of the loop every PO of the store counts as filed, so that a PO filed while the loop
  did not run is not missed; a later pass makes that sweep again only when it could not be made (the cases root could not be read).
  The cases checked again are the confirmations in review with no version selected because the store had no version of their PO, or
  not the version the document quotes, on which nothing was done (checked_again of aid/review/close.py). A case with a PO number of
  its own waits for that PO (waiting_for_po) and is checked again for that number only, never for another: when that PO was filed and
  the store now holds a version of it, or the version it quoted (PoStore.candidates, so a look from an export counts while the PO has
  no PDF). A case opened without a PO number waits for nothing and stays with the buyer, and it is checked again for the PO the scan
  would give its document now (po_without_number of aid/sources/mailbox.py, on the first page of its input file), when that PO was
  filed; never once the number recognition finds a number on that page (it was changed after the document came in: the buyer
  decides). Its first page and its quick read by lines are read once in a run of the loop, and the whole store is asked only when a
  PO filed in the pass can be its PO (could_be_po), so that the check costs what the POs filed cost. A PO found so is a guess: the new
  case gets how in its case.json (po_found_by) and goes to the buyer (aid/pipeline.py). Checked again means: the case's state is read
  from its folder just before the new case is opened, again once it is opened, and a third time right before the old case is
  finished, and a case decided or closed meanwhile is left alone, back in the digest's list of cases to report when the third read
  finds it so. Either read then removes the new case when it has not run: the case this check opened, or a successor an earlier pass
  opened that holds no result and wrote and sent nothing (_not_run); a successor that has run stays. Only a decision saved in the
  very moment the old case is finished is overridden: the close then refuses the case, which is no longer in review. When the case
  has a live successor, a case opened after it from input files of the same sha256 and of the same kind that was not itself
  finished as replaced (the newest of them, whatever its PO number and whether or not it waits: a check again cut off before it
  finished the old case opened it), the old case is finished as replaced by it and no case is opened; else a new case is opened
  from the same input files with that PO number, at the pass's time but no earlier than one second after the old case was opened, or
  the first free second after that (open_free), so that a replacement is always opened after the case it replaces, and runs in this
  pass like any case. The old case leaves the digest's list of cases to report, and the state is saved, before it is finished as
  replaced (finish_replaced of aid/review/close.py: closed, no supplier, the new case's id in its record), so that no end of the
  process leaves a replaced case in that list; once it is finished, the intake file gets the line "recheck" of its document, named
  alone, without its e-mail, with the new case's id, or the line waits in the loop as the scan's lines do. An error of one case is logged as "recheck <case id>" once in a run of the loop, as a failing case is, and the
  others go on; a check again counts as run only when it reached the case's link, so a pass that files
  another PO logs no recovery, and the stage of the check again only in a pass that filed a PO (at the first pass of a run, when the
  store holds one), so that a pass that files nothing logs no recovery of the stage. The case is tried again at the next pass that
  files its PO, or at the next start;
- every case folder under cases_root without steps/08_result.json is run with run_case and its dispositions recorded: the cases the
  scan opened and those an earlier pass that was cut off left without a result. A case cut off after a write or a message is not
  run again: run_case finishes it as review ("run cut off after writes: check the write log"), and it is reported like any case. A
  case opened from a scan that fails at reading, because Copilot reads the text layer only (unread_scan of aid/notify.py), gets a
  line of its own in the intake file, its document with the outcome "unread" and the case's id, so that the tab "Bestanden" says
  what became of the scan. A
  case already run is left alone, except that a case with a result and without dispositions.csv (a process that ended between the
  two) gets its dispositions and is reported, unless it was finished as replaced, which is never reported (_recover). A case folder
  with a .running file younger than ten minutes is left alone: the capture server (aid/capture_server.py) is running it; an older
  one is a run that crashed, and the case is resumed. A failure of
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
- every open change of a PO line on the list of changes to enter in Exact (aid/execution/by_hand.py, reconcile) is compared with its
  PO as it stands, the latest PDF version or a look from an export filed after it, when that was filed after the change was asked,
  so that a PO PDF that Exact sent after the buyer entered the change, filed by the scan, ticks it off as seen in that version; a
  change that is ticked, seen or dropped stays so whatever a later PDF shows. An error of this step is logged as the stage "list of
  changes", once in a run of the loop, and the pass goes on;
- a case of the capture server (its folder holds capture.json) that has its result and was never reported is reported once: the
  server runs its cases itself and never writes the runner's state. A capture case that the server replaced by a new case of the
  same page or cart (its capture.json holds replaced_by, which the server writes before it closes the case's result; or, while
  that file cannot be read, its close records hold the replacement) is never reported, and leaves the digest's list of cases to
  report when it stands there; the digest looks at that mark again for each case it is about to name, after it has read the
  case's result, so that no digest names a replaced capture case as closed, whatever step the server's process is at or ended at;
- escalate_pending ends the wait of the questions that bounced or had no answer in time, and end_empty_waits brings a case
  back to the buyer that waits while no question of it is out;
- when digest_due says so, the digest of the cases handled and the files skipped since the last digest is made: its HTML is written to
  <cases_root>/digests/<day>.html, for a flow outside the code to pick up, and its text is sent through the mailer of the Deps, or,
  without a mailer, written to <cases_root>/digests/<day>.txt. The digest names the number of changes still to enter in Exact, the
  open changes of <cases_root>/prepared (aid/execution/by_hand.py), where the command's writer prepares them, as they are after the
  step above, but those of try-out documents (Change.tryout), which are never to enter in Exact, with the link to the review page's
  tab "In Exact invoeren"; a lapse of the mailbox's sign-in, a failing Copilot with the cases waiting for it, and the documents
  Copilot could not read while it read others, in a line each. In the per-case mode a lapse or a failure of Copilot that no digest
  has named yet makes a digest too; open changes alone make none. A list that cannot be read, for any reason, is logged with the
  class of its error, and the digest says in that line that the list could not be read, with the same link; in the per-case mode
  that line alone makes no digest either. A digest state file that cannot be written is logged
  once in a run of the loop and never stops the runner (_save_quietly); a digest made while it cannot be written goes out once all the
  same, as the loop keeps the period it closed until the file holds it (_closed_again). A digest that the work mailbox cannot send
  because the sign-in has lapsed (SignInLapsed of aid/messaging/microsoft_mailer.py) is written through the file mailer into
  <cases_root>/outbox instead, the period is closed as for a digest sent, and the lapse is logged under the same once-an-hour limit.
  So is a digest that it cannot send because this process may not use the store of the sign-in (a SignInLapsed of the kind
  "store"), but that is no lapse, as no sign-in puts it right: the state notes no lapse, so that neither the digest nor the review
  page tells the buyer to sign in again, and it is logged once in a run of the loop, until a digest goes out through the mailbox
  again (the review page tells a store inside a git work tree by itself).

When run_loop is given the settings file the Deps were built from, it looks at the file's modification time at the start of each
pass. When the time has changed, the file is read again with load_settings and the pass runs with the Deps that `rebuild` makes of
its settings (the command rebuilds them with build_deps), or with the Deps given and the new settings. A file that cannot be read
or validated, or whose Deps cannot be built, leaves the settings in use as they are, with one line in the log that names the fields
at fault, if any, and never a value. So a save on the settings tab of the review page is taken over at the next pass.

What the next digest is to report is kept in <cases_root>/digest_state.json, so that it survives the end of a session: {"last_sent":
the time of the last digest or null, "reported": the ids of the cases handled since then, "skipped": the [file, reason] pairs of the
files skipped since then, each pair once, and, once there is one, "captures": the ids of the capture server's cases reported, and
while a file stays in the watched folder, "intake": the record of the lines of the intake file written of it, by its name (_intake)}. A case escalated by escalate_pending is reported again, as it now waits for the buyer. In
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
(NO_LLM). The writer is always the PreparedWriter into <cases_root>/prepared: no command builds a write route into Exact, and a
settings file in which exact_base_url is set is refused as a file that cannot be used, with the one sentence of aid/settings.py
(WRITE_ROUTE_SET). The mailer follows send_via, and is made by mailer_of of aid/messaging/microsoft_mailer.py, the one builder that
the review page makes the mailer of a close with too: "microsoft" is the MicrosoftMailer, which sends from
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
import shutil
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path

from aid.case import TEMPORARY, WORK, Case, _write_json, load_case, sha256_file    # _write_json: the state is rewritten whole or not at all
from aid.connections.microsoft import SIGN_IN_AGAIN, MicrosoftAccount, SignInNeeded
from aid.connections.secrets import LazySecretStore, default_secrets_dir
from aid.decider import LlmDecider, NoDecider
from aid.execution.by_hand import FOLDER as PREPARED, open_changes, reconcile
from aid.execution.writer import PreparedWriter
from aid.messaging import FileMailer
from aid.messaging.microsoft_mailer import OUTBOX, SignInLapsed, mailer_of
from aid.notify import build_digest, digest_due, send_digest, unread_scan
from aid.pipeline import (DISPOSITION_FILE, RESULT_FILE, UNREADABLE, CaseResult, Deps, end_empty_waits, escalate_pending, read_with_parser,
                          record_dispositions, run_case)
from aid.pipeline import _cut_off_after_writes      # whether a run cut off wrote or sent something, which removing its case would lose
from aid.po_store import PoStore
from aid.readers.cart import is_cart_page, read_cart_html
from aid.readers.copilot_client import CopilotClient, CopilotError
from aid.readers.llm_client import LlmClient, LlmRequest, LlmResponse, ReaderError
from aid.readers.supplier_llm import read_supplier_document
from aid.readers.supplier_parser import load_templates
from aid.review.close import cases_checked_again, checked_again, finish_replaced, replaced
from aid.review.intake import line_of
from aid.review.intake import write as write_intake
from aid.schema import Document
from aid.settings import Settings, load_settings
from aid.sources.dropin import remove_leftovers
from aid.sources.mailbox import (PROCESSED_FOLDER, ScanResult, could_be_po, find_po_numbers, held_pos, open_free, po_without_number,
                                 quick_read, scan_folder, to_processed)
from aid.sources.mailbox import _first_page_text     # the first page of a document as the scan reads it, for po_without_number
from aid.sources.microsoft_mail import forget_connection, sync_mailbox, trigger_on

LOG_FILE = "runner.log"
RUNNING_FILE = ".running"                        # in a case folder while the capture server runs the case: {"pid", "started"}
RUNNING_FRESH = timedelta(minutes=10)            # a .running file at least this old is a run that crashed: the case is resumed
CAPTURE_FILE = "capture.json"                    # what marks a case of the capture server (aid/capture_server.py)
STATE_FILE = "digest_state.json"
DIGESTS_FOLDER = "digests"
STOP_FILE = "STOP"                               # next to the settings file, for the command
ALREADY_RUN = "case already run"                 # the start of run_case's ValueError for a case that holds its result
SCAN = "scan"                                    # the stage of the scan, as runner.log names it
RECHECK = "recheck"                              # the stage of the check again of the cases that wait for their PO
MAILBOX = "mailbox"                              # the stage of the work mailbox
SETTINGS = "settings"                            # the stage of a settings file that changed
SEEN = "list of changes"                         # the stage that compares the open changes with their POs as they stand
WORK_FOLDER = "work folder"                      # the stage of <cases_root>/tmp/work, emptied at the start of the runner
DROPS = "watched folder"                         # the stage of the copies of drops cut off, removed from it at the start of the runner
MAILBOX_LAPSED = "mailbox_lapsed"                # the key of the digest state while the sign-in of the mailbox has lapsed
LAPSE_LOG = timedelta(hours=1)                   # a lapsed sign-in is logged at most once in this time
STORE_DIGEST = "digest store"                    # among the loop's failed stages while a refused store sends its digests to the outbox
ERROR = "error: "                                # the start of the reason of a file the scan left in the inbox after an exception
NO_LLM = "reader 'llm' needs the Copilot sign-in: fill the tenant and the client on the Settings tab and sign in for Copilot"
SERVICE_FAILURES = (CopilotError, SignInNeeded)  # a failure of the AI service while a case runs: the case runs again at a later pass
COPILOT_DOWN = "copilot"                         # the key of the digest state while the AI service fails: {"since", "error", "waiting"}
COPILOT_CASES = "copilot_cases"                  # the key of the cases the AI service failed, in the order they are tried last
COPILOT_CAP = 3                                  # the failures after which a case is finished as failed (_run_cases)
COULD_NOT_READ = "Copilot could not read this document: {error}"
INTAKE = "intake"                                # the key of the digest state that keeps the lines written of the files that stay
INTAKE_STAGE = "intake file"                     # the stage of the intake file of the review page's tab "Bestanden", as runner.log names it
STAY_DAYS = 30                                   # the days after which a file that stays as an error is set aside (see the module docstring)
SET_ASIDE = "set aside"                          # the stage of a file set aside, with its name, as runner.log names it
SET_ASIDE_REASON = "set aside after {n} days without being read"     # its reason in the state's skipped list, for the next digest
FILINGS = ("po", "export", "case", "guess", "answer", "bounce", "auto", "own", "recheck")     # the outcomes that tell a filing (_intake)


def run_loop(deps: Deps, stop_file: Path, poll_seconds: float = 60.0, max_passes: int | None = None, *, settings_file: Path | None = None,
             rebuild: Callable[[Settings], Deps] | None = None, account: MicrosoftAccount | None = None, session=None) -> int:
    """Make a pass (see the module docstring) every poll_seconds until the stop file exists, or until max_passes passes are made,
    and return the number of passes made. The stop file is looked for before each pass.

    settings_file is the file the settings of the Deps were read from, read again when it changes, and rebuild makes the Deps of its
    new settings (see the module docstring). account is the Microsoft account the work mailbox is read with, and session the
    requests session Graph is called through; without an account one is made of the settings (_microsoft_account)."""
    loop, passes = _Loop(deps, settings_file, rebuild, account, session), 0
    _empty_work(Path(deps.settings.cases_root), deps.clock())
    _empty_drops(Path(deps.settings.watched_folder), Path(deps.settings.cases_root), deps.clock())
    while (max_passes is None or passes < max_passes) and not Path(stop_file).exists():
        loop.reload()
        _pass(loop)
        passes += 1
        if max_passes is None or passes < max_passes:
            time.sleep(poll_seconds)
    return passes


def _empty_work(root: Path, now: datetime) -> None:
    """Empty <cases_root>/tmp/work, where the runner keeps a copy for a moment (the attachments of an e-mail while it scans the
    folder, the PDF that Copilot reads; work_copy_folder of aid/case.py): what a part that was ended in the middle of its copy left
    there goes at the next start of the runner. Only what lies in that folder is removed; the folder itself, the review page's files
    beside it and a folder that is a link elsewhere are left. A copy that cannot be removed is counted, and the count is one line in
    runner.log, which names no file; the runner goes on."""
    work = root / TEMPORARY / WORK
    if work.is_symlink() or not work.is_dir():
        return
    try:
        entries = list(work.iterdir())
    except OSError as exc:
        _log(root, now, WORK_FOLDER, f"{type(exc).__name__}: {TEMPORARY}/{WORK} could not be read; the runner goes on")
        return
    failed: list[OSError] = []
    for path in entries:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            failed.append(exc)
    if failed:
        _log(root, now, WORK_FOLDER, f"{type(failed[0]).__name__}: {len(failed)} of the {len(entries)} copies left in {TEMPORARY}/{WORK} "
                                     "could not be removed; the runner goes on")


def _empty_drops(folder: Path, root: Path, now: datetime) -> None:
    """Remove from the watched folder the copies that drops of the review page cut off left there (remove_leftovers of
    aid/sources/dropin.py: drop's own temporary files older than ten minutes), as _empty_work empties tmp/work. A copy that cannot be
    removed is counted, and the count is one line in runner.log, which names no file; the runner goes on."""
    failed = remove_leftovers(folder)
    if failed:
        copies = "1 copy of a drop cut off" if len(failed) == 1 else f"{len(failed)} copies of drops cut off"
        _log(root, now, DROPS, f"{type(failed[0]).__name__}: {copies} could not be removed from the watched folder; the runner goes on")


class _Loop:
    """What a run of the loop keeps from one pass to the next: the Deps, the cases and the stages that failed in it (each logged
    once), the settings file with its modification time, the Microsoft account of the work mailbox, the period the last digest
    closed while the state file could not keep it (_closed_again), whether its passes have checked the waiting cases against every PO
    of the store yet (_pass), the first page and the quick read of each case without a number that it checked again, and the record
    of the files that stay with the lines of the intake file that wait (_Intake)."""

    def __init__(self, deps: Deps, settings_file: Path | None = None, rebuild: Callable[[Settings], Deps] | None = None,
                 account: MicrosoftAccount | None = None, session=None):
        self.deps, self.rebuild, self.given, self.session = deps, rebuild, account, session
        self.settings_file = Path(settings_file) if settings_file is not None else None
        self.modified = _modified(self.settings_file)
        self.failed: set[str] = set()
        self.made: tuple[tuple, MicrosoftAccount] | None = None     # the account made of the settings, with its tenant and client
        self.closed: dict | None = None          # {"last_sent", "reported", "skipped"} of a digest whose state was not saved
        self.first = True                        # the next check of the waiting cases takes every PO of the store as filed
        self.seen: dict[str, tuple[str, Document | None]] = {}   # the first page and the quick read of each case without a number
        self.intake = _Intake()                  # the record of the files that stay and the lines that wait (_intake)

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
        except Exception as exc:                 # its text may quote a value of the settings
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

    def signed_in(self, settings: Settings) -> str | None:
        """The address of the account signed in to the work mailbox, read from its store without the network; None without the
        tenant and the client of the sign-in, without an account, and when the store cannot be read. An e-mail from that address
        is of the aid's own side (scan_folder of aid/sources/mailbox.py)."""
        if not (settings.ms_tenant_id and settings.ms_client_id):
            return None
        try:
            return self.account(settings).account()
        except Exception:                        # the store refused or cannot be read: the settings' own addresses still count
            return None


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
    filed: list[str] = []                        # the POs the scan filed in this pass
    _guarded(root, now, failed, SCAN, lambda: filed.extend(_scan(deps, root, state, now, failed, loop.intake, loop.signed_in(settings))))

    def recheck() -> bool:
        """The waiting cases checked again for the POs filed, and at the first pass of the run for every PO of the store; False when
        no PO was filed, so that the stage had nothing to do (_guarded)."""
        pos = [*filed, *(held_pos(deps.store) if loop.first else [])]
        _recheck_waiting(deps, root, state, now, pos, failed, loop.seen, loop.intake)
        loop.first = False
        return bool(pos)
    _guarded(root, now, failed, RECHECK, recheck)
    _run_cases(deps, root, state, now, failed, loop.intake)
    _guarded(root, now, failed, SEEN, lambda: reconcile(root / PREPARED, deps.store, root, deps.clock()))      # marked when it is seen
    for case_id in _without_dispositions(root, deps.clock()):
        _guarded(root, now, failed, case_id, lambda: _recover(root, state, case_id))
    _guarded(root, now, failed, "captures", lambda: _report_captures(root, state, deps.clock()))
    try:
        for case_id, reason in escalate_pending(root, now, settings):
            if reason.startswith(f"{UNREADABLE}:"):
                _log(root, now, case_id, reason)
            else:
                _report(root, state, case_id)
        for case_id, _ in end_empty_waits(root, now, settings):          # a case that waits while no question of it is out
            _report(root, state, case_id)
    except Exception as exc:
        _log(root, now, "escalation", exc)
    try:
        _digest(loop, root, state, now)
    except Exception as exc:
        _log(root, now, "digest", exc)


def _guarded(root: Path, now: datetime, failed: set[str], case_id: str, work) -> Exception | None:
    """Do the work on the case, and return the exception that stopped it, if any. Its first failure in this run of the loop is
    logged, and so is its first success after one. A work that returns False had nothing to do (a check again that did not reach the
    case's link, or the stage of the check again in a pass that filed no PO): that is no success, so an earlier failure stays noted
    and nothing is logged."""
    try:
        done = work()
    except Exception as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(ALREADY_RUN):
            return None
        if case_id not in failed:
            _log(root, now, case_id, exc)
            failed.add(case_id)
        return exc
    if done is not False and case_id in failed:
        _log(root, now, case_id, "ran after an earlier failure")
        failed.discard(case_id)
    return None


def _run_cases(deps: Deps, root: Path, state: dict, now: datetime, failed: set[str], intake: "_Intake | None" = None) -> None:
    """Run every case without a result (_run), each guarded, and keep in the state what the AI service's failures call for. A case
    opened from a scan that failed at reading (unread_scan of aid/notify.py) gets a line of its own in the intake file, for the
    review page's tab "Bestanden": its document, named alone as a case checked again is, the outcome "unread" and the case's id,
    written as the scan's lines are, or waiting in the loop (`intake`) as they do.

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
    intake = intake if intake is not None else _Intake()
    for case_id in [case_id for case_id in pending if case_id not in records] + list(records):
        if down:
            waiting += 1
            continue
        answered: list[bool] = []
        unread: list[tuple[str, str, str]] = []
        failure = _guarded(root, now, failed, case_id, lambda: _run(deps, root, state, case_id, answered, unread))
        if unread:                                                       # a scan that failed at reading: its line on the tab
            _write_intake(root, now, failed, intake, unread, state)
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


def _scan(deps: Deps, root: Path, state: dict, now: datetime, failed: set[str], intake: "_Intake | None" = None,
          signed_in: str | None = None) -> list[str]:
    """Scan the watched folder (with the address of the signed-in account, when there is one, among those of the aid's own side),
    write what became of each document to the intake file first (_intake, with what the loop keeps of it,
    `intake`; a call without one keeps nothing beyond the call), then report each case that a supplier's reply put in review, and note
    what the scan skipped in the state, each (file, reason) once, since a file that stays in the inbox as an error is tried again at
    every pass. Such an error is logged once in a run of the loop, as a failing case is. The lines come first, so that a failure of the
    reports (the state cannot be saved) loses none. Returns the POs the scan filed, each once: the number of each PO PDF it filed, also
    one the store held already, then of each PO whose look an export filed as new or changed."""
    settings = deps.settings
    inbox = Path(settings.watched_folder)
    inbox.mkdir(parents=True, exist_ok=True)
    intake = intake if intake is not None else _Intake()
    record = intake.record if intake.record is not None else state.get(INTAKE, {})
    stayed = _contents(inbox, record)            # the files that stayed after the last pass, as they are before this scan
    scan = scan_folder(inbox, deps.store, root, settings, now, deps.decider, own=[signed_in])
    _intake(root, inbox, state, scan, stayed, now, failed, intake)
    for case_id in scan.answered:                # a real reply put its case in review: the next digest reports it
        _report(root, state, case_id)
    for name, reason in scan.skipped:
        if _hidden(name.split("/")[0]) or name.split("/")[0] in intake.set_aside:     # a file set aside is named as such (_intake)
            continue
        if [name, reason] not in state["skipped"]:
            state["skipped"].append([name, reason])
        if reason.startswith(ERROR) and (key := f"{SCAN} {name}") not in failed:
            _log(root, now, key, reason)
            failed.add(key)
    _save_quietly(root, state, now, failed)
    return list(dict.fromkeys([*scan.stored_pos, *(number for _, _, new in scan.stored_exports for number in new)]))


class _Intake:
    """What a run of the loop keeps of the intake file (see the module docstring): the record of each file that stays in the watched
    folder, by its name, which decides within the run (None until a pass has read it from the state, the copy a new run starts from),
    each with the lines of it that intake.jsonl holds ("written") and those that wait to be written ("waiting", the loop's alone); the
    lines that could not be written yet, each with the time of its own pass (line_of), in their order; and the files the last scan step
    set aside."""

    def __init__(self):
        self.record: dict[str, dict] | None = None
        self.waiting: list[dict] = []
        self.set_aside: set[str] = set()


def _intake(root: Path, inbox: Path, state: dict, scan: ScanResult, stayed: dict[str, str | None], now: datetime, failed: set[str],
            intake: _Intake) -> None:
    """Write a line to the intake file for each event of the scan (aid/review/intake.py), but of a file that stays (see the module
    docstring) none that says the same as the last line of its document, written or waiting (O1), and no "received before" after a line
    that told a filing (O2); set aside each file that stayed STAY_DAYS days as an error (O8), which the state's skipped list then names
    once, for the next digest, in place of its earlier reasons (SET_ASIDE_REASON); and keep the record of each file that stays, in the
    loop (`intake`) and, without the lines that wait, in the state under INTAKE: {"sha256": its content, or None while it cannot be read,
    "since": the time of the pass that wrote its first line as a staying file, "written": [[file, outcome, detail], ...], the lines that
    intake.jsonl holds}. A line that waits is added to "written" once its write succeeds (_write_intake), so that a run that ends while
    lines wait leaves a record from which the next run writes the line of a file that still stays. `stayed` gives the content of each file
    the record names that is in the folder, read before the scan: None for one that cannot be read. A file stays when the scan left it in
    the folder, as an error or because it could not be moved (an error in the skipped list)."""
    record = intake.record if intake.record is not None else state.get(INTAKE, {})
    stays = {name.split("/")[0] for name, reason in scan.skipped if reason.startswith(ERROR)}
    by_file: dict[str, list[tuple[str, str, str]]] = {}
    for event in scan.events:
        if not _hidden(event[0].split("/")[0]):
            by_file.setdefault(event[0].split("/")[0], []).append(event)
    events, kept, aside = [], {}, {}
    for name, its in by_file.items():
        old = record.get(name)
        same = old is not None and name in stayed and (old["sha256"] is None or stayed[name] is None or stayed[name] == old["sha256"])
        written = [tuple(line) for line in old["written"]] if same else []
        waiting = [tuple(line) for line in old.get("waiting", [])] if same else []
        last = {line[0]: line for line in written + waiting}      # the last line of each document of the file, written or waiting
        for event in its:
            before = last.get(event[0])
            if before is not None and (event == before or (event[1:] == ("skipped", "duplicate") and before[1] in FILINGS)):
                continue
            events.append(event)
            waiting.append(event)
            last[event[0]] = event
        if name not in stays or not (inbox / name).is_file():
            continue
        since = datetime.fromisoformat(old["since"]) if same else now
        if same and any(outcome == "error" for _, outcome, _ in its) and (days := _elapsed(since, now).days) >= STAY_DAYS:
            if _set_aside(root, inbox / name, now, failed, days):
                events.append((name, "set_aside", str(days)))
                aside[name] = days
                continue
        content = stayed[name] if name in stayed else _content(inbox / name)
        if content is None and same:
            content = old["sha256"]
        kept[name] = {"sha256": content, "since": since.isoformat(), "written": [list(line) for line in written],
                      "waiting": [list(line) for line in waiting]}
    intake.record, intake.set_aside = kept, set(aside)
    _write_intake(root, now, failed, intake, events)
    _keep_record(state, intake)
    for name, days in aside.items():                   # the next digest names the file once, as set aside
        state["skipped"] = [pair for pair in state["skipped"] if pair[0].split("/")[0] != name]
        state["skipped"].append([name, SET_ASIDE_REASON.format(n=days)])


def _keep_record(state: dict, intake: _Intake) -> None:
    """Put the loop's record into the state under INTAKE without the lines that wait (_intake); none when no file stays."""
    if intake.record:
        state[INTAKE] = {name: {"sha256": entry["sha256"], "since": entry["since"], "written": list(entry["written"])}
                         for name, entry in intake.record.items()}
    else:
        state.pop(INTAKE, None)


def _set_aside(root: Path, path: Path, now: datetime, failed: set[str], days: int) -> bool:
    """Move a file that stayed STAY_DAYS days as an error to processed/, as a handled file (to_processed), say so in runner.log with the
    file and its whole days, and say whether it went. One that cannot be moved stays, to be tried again at the next pass; that is
    logged once in a run of the loop, with the class of the error and never its text."""
    key = f"{SET_ASIDE} {path.name}"
    try:
        to_processed(path)
    except OSError as exc:
        if key not in failed:
            _log(root, now, key, f"{type(exc).__name__}: the file stays in the watched folder and is tried again at the next pass")
            failed.add(key)
        return False
    failed.discard(key)
    _log(root, now, key, f"not read after {days} days; moved to {PROCESSED_FOLDER}/")
    return True


def _write_intake(root: Path, now: datetime, failed: set[str], intake: _Intake, events: list[tuple[str, str, str]],
                  state: dict | None = None) -> None:
    """Write the lines that wait (intake.waiting), then a line for each event at the time `now`, in one write. When they cannot be
    written they all wait, each with the time of its own pass, for the next write, at the next pass or at the check again of this one;
    the failure is logged once in a run of the loop, with the number of lines that wait and the class of the error, never a path, and
    the write after it says how many lines that waited it wrote. Once they are written, the lines that waited of each file that stays
    are written ones in its record (_intake); given the `state`, its copy of the record is brought up to date and saved then."""
    lines = [*intake.waiting, *(line_of(event, now) for event in events)]
    if not lines:
        return
    try:
        write_intake(root, lines)
    except OSError as exc:
        intake.waiting = lines
        if INTAKE_STAGE not in failed:
            count = len(lines)
            _log(root, now, INTAKE_STAGE, f"{type(exc).__name__}: {_lines(count)} could not be written; "
                                          f"{'it waits' if count == 1 else 'they wait'} for the next pass")
            failed.add(INTAKE_STAGE)
        return
    waited, intake.waiting = len(intake.waiting), []
    moved = False
    for entry in (intake.record or {}).values():
        if entry.get("waiting"):
            entry["written"], entry["waiting"], moved = entry["written"] + entry["waiting"], [], True
    if moved and state is not None:
        _keep_record(state, intake)
        _save_quietly(root, state, now, failed)
    if INTAKE_STAGE in failed:
        _log(root, now, INTAKE_STAGE, f"ran after an earlier failure; {_lines(waited)} that waited {'is' if waited == 1 else 'are'} written")
        failed.discard(INTAKE_STAGE)


def _lines(count: int) -> str:
    return "1 line" if count == 1 else f"{count} lines"


def _contents(inbox: Path, record: dict) -> dict[str, str | None]:
    """The sha256 of each file of the watched folder that the record names and that is there, by its name: None for one whose content
    cannot be read (another program holds it), which counts as the same file as its record (see the module docstring)."""
    found = {}
    for name in record:
        if (inbox / name).is_file():
            found[name] = _content(inbox / name)
    return found


def _content(path: Path) -> str | None:
    """The sha256 of the file; None when it is not there or cannot be read."""
    try:
        return sha256_file(path) if path.is_file() else None
    except OSError:
        return None


def _recheck_waiting(deps: Deps, root: Path, state: dict, now: datetime, filed: list[str], failed: set[str],
                     seen: dict[str, tuple[str, Document | None]], intake: _Intake | None = None) -> list[str]:
    """Check the cases that wait for their PO, and the cases without a PO number, again for the POs `filed` in this pass (see the module
    docstring), and return the ids of the new cases, which _run_cases runs next. `seen` keeps, for the run of the loop, the first page
    and the quick read of each case without a number that was checked already, and `intake` the lines of the intake file that wait
    (_write_intake). An error of one case is logged as "recheck <case id>" once in a run of the loop, as a failing case is (_guarded),
    and the next case is checked."""
    filed = list(dict.fromkeys(filed))
    if not filed:
        return []
    new, read = [], []
    intake = intake if intake is not None else _Intake()

    def templates() -> list:
        """The layout templates of the quick read by lines, read once, at their first use (load_templates)."""
        if not read:
            read.append(load_templates())
        return read[0]

    def check(case: Case, waits: str) -> bool:
        """Check the case again; whether it reached the case's link (a PO found and the finish tried), so that a recovery is logged
        only then (_guarded). The case's state is read three times (see the module docstring): a decision saved meanwhile wins."""
        found = _checked_for(case, waits, deps, filed, templates, seen)
        if found is None or checked_again(case) != waits:        # nothing filed for it, or decided or closed meanwhile
            return False
        po_number, how = found
        renewed = _successor(root, case) or open_free(root, case.kind, case.input_files, po_number, _not_before(case.root, now),
                                                      case.po_found_by or how)

        def left_alone() -> bool:
            """The old case is left alone, and the new case removed when it has not run (_not_run); one that has run stays."""
            if _not_run(renewed):
                _remove_unrun(renewed.root)
            return False
        if checked_again(case) != waits:                         # decided or closed while its new case was opened
            return left_alone()
        listed = case.case_id in state["reported"]
        if listed:                                               # out of the digest's list before its result is closed
            state["reported"] = [case_id for case_id in state["reported"] if case_id != case.case_id]
            _save_state(root, state)
        if checked_again(case) != waits:                         # decided or closed after the second read: back in the digest's list
            if listed:
                _report(root, state, case.case_id)
            return left_alone()
        new.append(renewed.case_id)
        finish_replaced(case, renewed.case_id, now, deps.settings.language)
        name = case.input_files[0].name if case.input_files else case.case_id      # the document alone, without its e-mail
        _write_intake(root, now, failed, intake, [(name, "recheck", renewed.case_id)], state)
        return True
    for case, waits in cases_checked_again(root):
        _guarded(root, now, failed, f"{RECHECK} {case.case_id}", lambda: check(case, waits))
    return new


def _checked_for(case: Case, waits: str, deps: Deps, filed: list[str], templates: Callable[[], list],
                 seen: dict[str, tuple[str, Document | None]]) -> tuple[str, str | None] | None:
    """The PO a case is checked again for, with how it was found when not as the number of the case (po_found_by), or None. A case with
    a PO number of its own is checked again for that number only, never for another: when that PO was filed and the store now holds
    what the case waits for, a version of the PO ("po") or the version it quoted ("version <n>"), among the candidates its document is
    matched against. A case without a PO number is checked again for the PO the scan would give its document now (po_without_number of
    aid/sources/mailbox.py, on the first page of its input file, which is all the case keeps of its e-mail), when that PO was filed;
    but never once the number recognition finds a number on that page, which the buyer then decides on, and the whole store is asked
    only when a PO filed can be its PO (could_be_po), so that the check costs what the POs filed cost. `seen` keeps the first page and
    the quick read of the case for the run of the loop."""
    store = deps.store
    if case.po_number is not None:
        if case.po_number not in filed:
            return None
        stored = [f"version {version.version}" for version in store.candidates(case.po_number, case.kind)]
        return (case.po_number, None) if (stored and waits == "po") or waits in stored else None
    if not case.input_files:
        return None
    path = case.input_files[0]
    if case.case_id not in seen:
        page = _first_page_text(path)
        seen[case.case_id] = (page, quick_read(path, page, templates))
    page, reading = seen[case.case_id]
    if find_po_numbers(page, deps.settings.po_number_pattern) or not could_be_po(filed, page, reading, store):
        return None
    found, how = po_without_number(path, page, page, store, templates)
    return (found, how) if found is not None and found in filed else None


def _not_before(folder: Path, now: datetime) -> datetime:
    """When to open the case that replaces the case of the folder: at `now`, the time of the pass, but no earlier than one second after
    the old case was opened (by its case.json), so that a replacement is always opened after the case it replaces and _successor finds
    it; `now` when the time of opening cannot be read."""
    try:
        opened = datetime.fromisoformat(json.loads((folder / "case.json").read_text(encoding="utf-8"))["opened_at"])
        gap = _elapsed(opened, now)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return now
    return now if gap >= timedelta(seconds=1) else now + (timedelta(seconds=1) - gap)


def _not_run(case: Case) -> bool:
    """Whether the case has not run, so that removing it loses nothing: it holds no result, and no run of it that was cut off wrote or
    sent anything (_cut_off_after_writes of aid/pipeline.py). A run cut off before that leaves steps that the next run writes again."""
    return not (case.root / "steps" / RESULT_FILE).exists() and not _cut_off_after_writes(case)


def _remove_unrun(folder: Path) -> None:
    """Remove the folder of a case that has not run (_not_run): its case.json first, so that a folder the removal leaves half way is
    no case (_case_folders), then the rest. An error propagates, for the stage's log."""
    (folder / "case.json").unlink()
    shutil.rmtree(folder)


def _successor(root: Path, old: Case) -> Case | None:
    """The live successor of a case that is checked again: the newest case opened after it (by the time of opening in case.json) from
    input files of the same sha256, in the same order, and of the same kind, whatever its PO number and whether or not it waits, that
    was not itself finished as replaced (replaced of aid/review/close.py); None when there is none. It is the case that a check again
    cut off before it finished the old case opened, or one opened since from the same document. A case folder whose case.json cannot
    be read is passed over."""
    mine = _identity(old.root)
    if mine is None:
        return None
    files, opened = mine
    later = []
    for path in _case_folders(root):
        theirs = _identity(path)
        if path.name == old.case_id or theirs is None or theirs[0] != files or theirs[1] <= opened:
            continue
        try:
            case = load_case(root, path.name)
        except (OSError, ValueError):
            continue
        if case.kind == old.kind and not replaced(case):
            later.append((theirs[1], path.name, case))
    return max(later, key=lambda entry: entry[:2])[2] if later else None


def _identity(folder: Path) -> tuple[list[str], datetime] | None:
    """The sha256 of each input file of the case folder as its case.json names them, and the time the case was opened, with a zone (a
    time without one is local time, as _elapsed takes it, so that any two compare); None for a folder whose case.json cannot be read
    as such."""
    try:
        meta = json.loads((folder / "case.json").read_text(encoding="utf-8"))
        opened = datetime.fromisoformat(meta["opened_at"])
        return [entry["sha256"] for entry in meta["files"]], opened.astimezone() if opened.utcoffset() is None else opened
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None


def _run(deps: Deps, root: Path, state: dict, case_id: str, answered: list[bool] | None = None,
         unread: list[tuple[str, str, str]] | None = None) -> None:
    """Run a case, report it and record its dispositions. run_case makes any exception of a step the failure of the case; a failure of
    the AI service (SERVICE_FAILURES) in the reader or in the decider is carried past it instead (_carrying), as an interruption is,
    so that the case keeps no result and no audit record of the attempt, and it is raised here for _guarded to log, the case not
    reported. Such a failure comes before any write or message of the case: the steps that ask a model come before them. True is
    appended to `answered` when the model answered during the run, and the line of the intake file to `unread` when the case failed
    at reading a scan (unread_scan of aid/notify.py): (its document, "unread", the case id)."""
    case = load_case(root, case_id)
    try:
        result = run_case(case, _carrying(deps, answered if answered is not None else []))
    except _ServiceFailed as failed:
        raise failed.error from None
    if unread is not None and unread_scan(result):
        unread.append((case.input_files[0].name if case.input_files else case_id, "unread", case_id))
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
    """Record the dispositions of a case that has its result and none, and report it, unless it was replaced (_replaced_case:
    finished as replaced, or a capture case marked as replaced): its dispositions are recorded as for any case, and the case that
    replaced it is the one reported."""
    case = load_case(root, case_id)
    result = CaseResult.model_validate_json((case.root / "steps" / RESULT_FILE).read_text(encoding="utf-8"))
    record_dispositions(case, result)
    if not _replaced_case(case.root):
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
    results, replaced_meanwhile = [], []
    for case_id in state["reported"]:
        try:
            result = CaseResult.model_validate_json((root / case_id / "steps" / RESULT_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _log(root, now, case_id, exc)
            continue
        # Looked at after the result was read: the capture server marks a case before it closes its result, so a case that is not
        # marked now was read as it stood before any replacement. A case whose mark cannot be read is known by its close record.
        if _replaced_case(root / case_id):
            replaced_meanwhile.append(case_id)
        else:
            results.append(result)
    if replaced_meanwhile:                       # replaced since this pass looked at the capture cases: named in no digest
        state["reported"] = [case_id for case_id in state["reported"] if case_id not in replaced_meanwhile]
    if settings.digest == "per_case" and not state["reported"] and not state["skipped"] and not unnamed:
        return
    try:
        to_enter = sum(1 for change in open_changes(root / PREPARED) if not change.tryout)       # a try-out's changes are not to enter
    except Exception as exc:                     # the list cannot be read now: the digest says so, and is not held back for it
        _log(root, now, "digest", f"{type(exc).__name__}: the list of changes to enter cannot be read; the digest says so in the "
                                  f"line of the list")
        to_enter = None
    digest = build_digest(results, [(name, reason) for name, reason in state["skipped"]], settings, now.date(),
                          mailbox_lapsed=lapse is not None, copilot_down=(copilot["error"], copilot["waiting"]) if copilot else None,
                          copilot_refused=refused, to_enter=to_enter)
    folder = root / DIGESTS_FOLDER
    folder.mkdir(exist_ok=True)
    (folder / f"{digest.period}.html").write_text(digest.html, encoding="utf-8")       # for a flow to pick up; the runner sends no HTML
    if deps.mailer is not None:
        try:
            send_digest(digest, deps.mailer, settings)
        except SignInLapsed as error:            # the work mailbox cannot send it: the digest is written as a file, as without SMTP
            send_digest(digest, FileMailer(root / OUTBOX, sender=settings.aid_email), settings)
            line = f"the digest is written to {OUTBOX}/, as the work mailbox cannot send it: {error}"
            if error.kind == "store":            # a store this process may not use: no lapse, which a sign-in would put right
                if STORE_DIGEST not in loop.failed:
                    _log(root, now, MAILBOX, line)
                    loop.failed.add(STORE_DIGEST)
            else:
                try:
                    _lapsed(root, state, now, line)
                except OSError:                  # the state file: the save below names it
                    pass
        else:
            loop.failed.discard(STORE_DIGEST)
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
    "captures" list. A capture case that the server replaced (_replaced_case: marked in its capture.json, or finished with the
    record of a replacement) is not reported, and is taken out of the cases the next digest reports when it stands among them, the
    state saved at once. Only a case that is not reported yet, or that stands among the cases to report, has its capture.json and
    its close records read, so that a pass costs what the captures not yet reported cost."""
    listed, known = set(state["reported"]), set(state.get("captures", []))
    for path in _case_folders(root):
        name = path.name
        if not (path / CAPTURE_FILE).is_file() or (name in known and name not in listed):
            continue
        if _replaced_case(path):
            if name in listed:
                state["reported"] = [case_id for case_id in state["reported"] if case_id != name]
                _save_state(root, state)
        elif name not in known and (path / "steps" / RESULT_FILE).exists() and not _running(path, now):
            _report(root, state, name)


def _replaced_capture(folder: Path) -> bool:
    """Whether the case of the folder is a capture case that the capture server replaced by a new case of the same page or cart: its
    capture.json holds replaced_by, which the server writes before it closes the case's result (aid/capture_server.py, _replace). A
    folder without the file, or whose file cannot be read, holds none."""
    try:
        meta = json.loads((folder / CAPTURE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(meta, dict) and meta.get("replaced_by") is not None


def _replaced_case(folder: Path) -> bool:
    """Whether the case of the folder was replaced by a new case: a capture case marked as replaced (_replaced_capture), or a case
    whose close records hold a replacement (replaced of aid/review/close.py), by which a capture case that was finished is still
    known while its capture.json cannot be read. A folder whose case.json cannot be read holds none."""
    if _replaced_capture(folder):
        return True
    try:
        return replaced(load_case(folder.parent, folder.name))
    except (OSError, ValueError):
        return False


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
        if (intake := state.get(INTAKE)) is not None:                    # only while a file stays in the watched folder
            if not (isinstance(intake, dict) and all(_staying(record) for record in intake.values())):
                raise ValueError(f"its {INTAKE} is not the lines written of the files that stay in the watched folder")
    except (OSError, ValueError, TypeError) as exc:
        _log(root, now, STATE_FILE, f"{type(exc).__name__}: {' '.join(str(exc).split())}; a fresh state is started")
        return fresh
    return state


def _staying(record) -> bool:
    """Whether a record of the state's INTAKE has its form (_intake): its sha256 a text or None, its since a time, and its lines
    written, each three texts."""
    if not (isinstance(record, dict) and set(record) == {"sha256", "since", "written"} and isinstance(record["sha256"], str | None)
            and isinstance(record["since"], str) and isinstance(record["written"], list)
            and all(isinstance(line, list) and len(line) == 3 and all(isinstance(part, str) for part in line) for line in record["written"])):
        return False
    try:
        datetime.fromisoformat(record["since"])
    except ValueError:
        return False
    return True


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
    """The Deps of the command for company documents (see the module docstring); `environ` is the environment that the mailbox
    password is looked up in, `account` the Microsoft account that Copilot is asked with and a MicrosoftMailer sends through, made
    of the settings when none is given, and `session` the requests session that Copilot is called through (None: requests). The
    writer is the prepared writer, whatever the settings hold. Raises ValueError for reader "llm" without the tenant and the
    client (NO_LLM)."""
    reader, decider = read_with_parser, NoDecider()
    if settings.reader == "llm":
        if not (settings.ms_tenant_id and settings.ms_client_id):
            raise ValueError(NO_LLM)
        account = account or _microsoft_account(settings)
        client = CopilotClient(account, session=session, work=settings.cases_root / TEMPORARY / WORK)     # a PDF's copy: under the cases
        reader, decider = llm_reader(client), LlmDecider(client)
    return Deps(settings=settings, store=PoStore(settings.po_store_root, settings.authorization_limit), reader=reader,
                decider=decider, writer=PreparedWriter(settings.cases_root / "prepared"),
                mailer=mailer_of(settings, environ, lambda: account or _microsoft_account(settings)), clock=datetime.now)


def llm_reader(client: LlmClient) -> Callable[[Path, str], Document | tuple[Document, LlmResponse]]:
    """The reader of a run with a model, as demo.live_reader wires it: read_supplier_document over the client for a PDF or a PNG
    document, and the cart reader (read_cart_html), which needs no model, for a cart page saved as HTML."""
    def reader(path: Path, kind: str):
        if is_cart_page(path, kind):
            return read_cart_html(path)
        return read_supplier_document(path, client, kind)
    return reader


def _microsoft_account(settings: Settings) -> MicrosoftAccount:
    """The Microsoft account of the settings' tenant and client, with the token cache of the store in default_secrets_dir(), made
    at its first use (LazySecretStore): a store this process may not use is then an error of the mailbox, logged, and never a
    reason for the runner not to start."""
    return MicrosoftAccount(settings.ms_tenant_id, settings.ms_client_id, LazySecretStore(default_secrets_dir()))


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
