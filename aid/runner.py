"""The runner: the loop that both hosts run, Google Colab for the made-up set (aid/colab) and the company platform for company
documents, and the command that runs it on the company platform.

    python3 -m aid.runner --settings <path> [--poll-seconds N] [--max-passes N]

run_loop makes a pass every poll_seconds until the stop file exists. Each pass, at the time of the Deps clock:
- scan_folder (aid/sources/mailbox.py) handles the watched folder of the settings, created when it is missing: PO PDFs go into the
  store, a case is opened under cases_root for each confirmation, an e-mail that answers a question of the aid is filed into its case
  (a real reply puts the case in review and is reported in the next digest, a bounce waits for the escalation), and the files it
  handled move to processed/;
- every case folder under cases_root without steps/08_result.json is run with run_case and its dispositions recorded: the cases the
  scan opened and those an earlier pass that was cut off left without a result. A case cut off after a write or a message is not
  run again: run_case finishes it as review ("run cut off after writes: check the write log"), and it is reported like any case. A
  case already run is left alone, except that a case with a result and without dispositions.csv (a process that ended between the
  two) gets its dispositions and is reported;
- escalate_pending ends the wait of the questions that bounced or had no answer in time;
- when digest_due says so, the digest of the cases handled and the files skipped since the last digest is made: its HTML is written
  to <cases_root>/digests/<day>.html, for a flow outside the code to pick up, and its text is sent through the mailer of the Deps,
  or, without a mailer, written to <cases_root>/digests/<day>.txt.

What the next digest is to report is kept in <cases_root>/digest_state.json, so that it survives the end of a session: {"last_sent":
the time of the last digest or null, "reported": the ids of the cases handled since then, "skipped": the [file, reason] pairs of the
files skipped since then, each pair once}. A case escalated by escalate_pending is reported again, as it now waits for the buyer. In
the per-case mode a digest is made only when there is something to report; daily, the digest of a day without cases says so. A state
file that cannot be read, or that lacks a key, starts a fresh state, with a line in the log. An error of one case, of the scan, of
the escalation or of the digest is written to <cases_root>/runner.log with the case id or the stage, and the pass goes on: the
digest that failed is made again at the next pass. A case that fails is logged once in a run of the loop, and once more when it
later succeeds, so that a case that keeps failing does not fill the log; so is a file that the scan leaves in the inbox as an error,
which is tried again at each pass, and a case folder whose case.json cannot be read, which the scan passes over. Files and folders
whose name starts with a dot (".ipynb_checkpoints", ".DS_Store") are no case and no skipped file; the scan leaves them where they
are.

The command builds the Deps for company documents with build_deps and runs the loop until the file STOP exists next to the settings
file. A settings file that cannot be read or validated stops it with exit code 2 and the fields at fault, never their values. The
reader is the parser (settings reader "parser"): the LLM reader needs the Microsoft client of Task 7b, which is not yet available,
and company documents never go to another AI service, so the command refuses reader "llm" with exit code 2. The decider is
NoDecider. The writer is RetryingWriter over the ExactApiWriter, with PreparedWriter into <cases_root>/prepared as its fallback,
when exact_base_url is set, the token read from the environment variable AID_EXACT_TOKEN at each call; else the PreparedWriter
alone. The mailer is SmtpMailer when smtp_host and smtp_user are set and AID_MAIL_PASSWORD is in the environment, else FileMailer
into <cases_root>/outbox. The clock is datetime.now. The host, how the command is started and kept running, the folder and where the
credentials are kept follow the company's answer (aid/README.md, "Running on the company side").
"""
import argparse
import json
import os
import sys
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from aid.case import _write_json, load_case        # _write_json: the state is rewritten whole or not at all, as a case file is
from aid.decider import NoDecider
from aid.execution.exact_api import ExactApiWriter
from aid.execution.writer import PreparedWriter, RetryingWriter, WriterError
from aid.messaging import FileMailer, SmtpMailer
from aid.notify import build_digest, digest_due, send_digest
from aid.pipeline import DISPOSITION_FILE, RESULT_FILE, UNREADABLE, CaseResult, Deps, escalate_pending, read_with_parser, record_dispositions, run_case
from aid.po_store import PoStore
from aid.settings import Settings, load_settings
from aid.sources.mailbox import scan_folder

LOG_FILE = "runner.log"
STATE_FILE = "digest_state.json"
DIGESTS_FOLDER = "digests"
STOP_FILE = "STOP"                               # next to the settings file, for the command
TOKEN_ENV = "AID_EXACT_TOKEN"
PASSWORD_ENV = "AID_MAIL_PASSWORD"
ALREADY_RUN = "case already run"                 # the start of run_case's ValueError for a case that holds its result
SCAN = "scan"                                    # the stage of the scan, as runner.log names it
ERROR = "error: "                                # the start of the reason of a file the scan left in the inbox after an exception
NO_LLM = ("reader 'llm' needs the Microsoft client of Task 7b, which is not yet available, and company documents never go to "
          "another AI service; set reader to 'parser' to run without a model")


def run_loop(deps: Deps, stop_file: Path, poll_seconds: float = 60.0, max_passes: int | None = None) -> int:
    """Make a pass (see the module docstring) every poll_seconds until the stop file exists, or until max_passes passes are made,
    and return the number of passes made. The stop file is looked for before each pass."""
    passes, failed = 0, set()                    # failed: the cases that failed in this run of the loop, each logged once
    while (max_passes is None or passes < max_passes) and not Path(stop_file).exists():
        _pass(deps, failed)
        passes += 1
        if max_passes is None or passes < max_passes:
            time.sleep(poll_seconds)
    return passes


def _pass(deps: Deps, failed: set[str]) -> None:
    settings, now = deps.settings, deps.clock()
    root = Path(settings.cases_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log(root, now, "cases root", exc)
        return
    state = _load_state(root, now)
    _guarded(root, now, failed, SCAN, lambda: _scan(deps, root, state, now, failed))
    for case_id in _without_result(root):
        _guarded(root, now, failed, case_id, lambda: _run(deps, root, state, case_id))
    for case_id in _without_dispositions(root):
        _guarded(root, now, failed, case_id, lambda: _recover(root, state, case_id))
    try:
        for case_id, reason in escalate_pending(root, now, settings):
            if reason.startswith(f"{UNREADABLE}:"):
                _log(root, now, case_id, reason)
            else:
                _report(root, state, case_id)
    except Exception as exc:
        _log(root, now, "escalation", exc)
    try:
        _digest(deps, root, state, now)
    except Exception as exc:
        _log(root, now, "digest", exc)


def _guarded(root: Path, now: datetime, failed: set[str], case_id: str, work) -> None:
    """Do the work on the case. Its first failure in this run of the loop is logged, and so is its first success after one."""
    try:
        work()
    except Exception as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(ALREADY_RUN):
            return
        if case_id not in failed:
            _log(root, now, case_id, exc)
            failed.add(case_id)
        return
    if case_id in failed:
        _log(root, now, case_id, "ran after an earlier failure")
        failed.discard(case_id)


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
    _save_state(root, state)


def _run(deps: Deps, root: Path, state: dict, case_id: str) -> None:
    """Run a case, report it and record its dispositions."""
    case = load_case(root, case_id)
    result = run_case(case, deps)
    _report(root, state, case_id)
    record_dispositions(case, result)


def _recover(root: Path, state: dict, case_id: str) -> None:
    """Record the dispositions of a case that has its result and none, and report it."""
    case = load_case(root, case_id)
    result = CaseResult.model_validate_json((case.root / "steps" / RESULT_FILE).read_text(encoding="utf-8"))
    record_dispositions(case, result)
    _report(root, state, case_id)


def _digest(deps: Deps, root: Path, state: dict, now: datetime) -> None:
    """Make the digest when it is due and there is a reason to, and start a new period in the state."""
    settings = deps.settings
    last_sent = datetime.fromisoformat(state["last_sent"]) if state["last_sent"] else None
    if not digest_due(now, last_sent, settings):
        return
    if settings.digest == "per_case" and not state["reported"] and not state["skipped"]:
        return
    results = []
    for case_id in state["reported"]:
        try:
            results.append(CaseResult.model_validate_json((root / case_id / "steps" / RESULT_FILE).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            _log(root, now, case_id, exc)
    digest = build_digest(results, [(name, reason) for name, reason in state["skipped"]], settings, now.date())
    folder = root / DIGESTS_FOLDER
    folder.mkdir(exist_ok=True)
    (folder / f"{digest.period}.html").write_text(digest.html, encoding="utf-8")       # for a flow to pick up; the runner sends no HTML
    if deps.mailer is not None:
        send_digest(digest, deps.mailer, settings)
    else:
        (folder / f"{digest.period}.txt").write_text(digest.text, encoding="utf-8")
    state.update(last_sent=now.isoformat(), reported=[], skipped=[])
    _save_state(root, state)


def _without_result(root: Path) -> list[str]:
    """The ids of the case folders under root that hold no result, in the order of their names."""
    return [path.name for path in _case_folders(root) if not (path / "steps" / RESULT_FILE).exists()]


def _without_dispositions(root: Path) -> list[str]:
    """The ids of the case folders under root that hold a result and no dispositions.csv, in the order of their names."""
    return [path.name for path in _case_folders(root)
            if (path / "steps" / RESULT_FILE).exists() and not (path / DISPOSITION_FILE).exists()]


def _case_folders(root: Path) -> list[Path]:
    return sorted(path for path in root.iterdir() if path.is_dir() and not _hidden(path.name) and (path / "case.json").is_file())


def _hidden(name: str) -> bool:
    return name.startswith(".")


def _report(root: Path, state: dict, case_id: str) -> None:
    """Note that the next digest reports the case, and save the state at once."""
    if case_id not in state["reported"]:
        state["reported"].append(case_id)
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
        if not isinstance(state["reported"], list) or not all(isinstance(skip, list) and len(skip) == 2 for skip in state["skipped"]):
            raise ValueError("its lists are not lists of case ids and of [file, reason] pairs")
    except (OSError, ValueError, TypeError) as exc:
        _log(root, now, STATE_FILE, f"{type(exc).__name__}: {' '.join(str(exc).split())}; a fresh state is started")
        return fresh
    return state


def _save_state(root: Path, state: dict) -> None:
    _write_json(root / STATE_FILE, state)


def _log(root: Path, now: datetime, what: str, error: Exception | str) -> None:
    """Add a line to runner.log: the time, the case id or the stage, and the error."""
    line = f"{now.isoformat()} {what}: {error if isinstance(error, str) else f'{type(error).__name__}: {' '.join(str(error).split())}'}"
    try:
        with open(root / LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:                              # no log to write to (the cases root cannot be made): the console has the line
        print(line, file=sys.stderr)


# --- the command on the company platform ---

def build_deps(settings: Settings, environ: Mapping[str, str]) -> Deps:
    """The Deps of the command for company documents (see the module docstring); `environ` is the environment that the Exact token
    and the mailbox password are looked up in. Raises ValueError for reader "llm" and for Exact settings the writer refuses."""
    if settings.reader != "parser":
        raise ValueError(NO_LLM)
    prepared = PreparedWriter(settings.cases_root / "prepared")
    writer = prepared
    if settings.exact_base_url:
        writer = RetryingWriter(ExactApiWriter(settings.exact_base_url, settings.exact_endpoints, lambda: _token(environ),
                                               fields=settings.exact_fields), prepared)
    # SmtpMailer reads the password from os.environ at each send, not from `environ`; main passes os.environ, so the two agree.
    if settings.smtp_host and settings.smtp_user and PASSWORD_ENV in environ:
        mailer = SmtpMailer(settings.smtp_host, settings.smtp_port, settings.smtp_user, password_env=PASSWORD_ENV, sender=settings.aid_email)
    else:
        mailer = FileMailer(settings.cases_root / "outbox", sender=settings.aid_email)
    return Deps(settings=settings, store=PoStore(settings.po_store_root, settings.authorization_limit), reader=read_with_parser,
                decider=NoDecider(), writer=writer, mailer=mailer, clock=datetime.now)


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
    passes = run_loop(deps, stop_file, poll_seconds=args.poll_seconds, max_passes=args.max_passes)
    print(f"{passes} {'pass' if passes == 1 else 'passes'}; stopped" + (f" by {stop_file}" if stop_file.exists() else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
