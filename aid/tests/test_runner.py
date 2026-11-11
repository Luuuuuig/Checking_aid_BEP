"""Tests of the runner (aid/runner.py): the loop that both hosts run, on the made-up set, and the Deps its command builds."""
import csv
import json
import os
import shutil
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
import requests

import aid.runner
from aid.case import open_case
from aid.connections.microsoft import MicrosoftAccount
from aid.connections.secrets import SecretStore
from aid.decider import LlmDecider, NoDecider
from aid.execution.exact_api import ExactApiWriter
from aid.execution.writer import PreparedWriter, RetryingWriter, WriterError
from aid.messaging import FileMailer, MailerError, SmtpMailer
from aid.pipeline import read_with_parser, record_dispositions, run_case
from aid.readers.copilot_client import CopilotClient
from aid.runner import build_deps, main, run_loop
from aid.settings import load_settings
from aid.tests.conftest import REPO_ROOT, SETTINGS, deps
from aid.tests.fakes import ACCESS_TOKEN, CLIENT_ID, COPILOT, TENANT_ID, USERNAME, FakeApp, FakeResponse, copilot_session


def test_run_loop_handles_the_inbox_and_obeys_the_stop_file(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out); inbox = tmp_path / "inbox"; inbox.mkdir(); shutil.copy(madeup_out / "MU01_conf.pdf", inbox)
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"watched_folder": inbox})})
    stop = tmp_path / "STOP"
    assert run_loop(d, stop, poll_seconds=0, max_passes=1) == 1 and (inbox / "processed" / "MU01_conf.pdf").exists()
    assert len(list(d.settings.cases_root.glob("250101_*/audit.json"))) == 1
    stop.write_text(""); assert run_loop(d, stop, poll_seconds=0) == 0


# The tests below cover what the rulings of the task add to the one above.

AFTER_DIGEST_TIME = datetime(2026, 11, 12, 17, 0)        # past the digest_time 16:30 of the settings


def loop_deps(tmp_path, madeup_out, clock=None, mailer=None):
    """deps() with the watched folder tmp_path/inbox (created), and the clock and the mailer given."""
    d = deps(tmp_path, madeup_out)
    inbox = tmp_path / "inbox"; inbox.mkdir(exist_ok=True)
    update = {"settings": d.settings.model_copy(update={"watched_folder": inbox}), "mailer": mailer}
    if clock is not None:
        update["clock"] = lambda: clock
    return d.model_copy(update=update)


def opened(d, madeup_out, name, po_number):
    """A case of the made-up confirmation `name` opened under the cases root, as an earlier pass cut off before its run leaves it."""
    return open_case(d.settings.cases_root, "confirmation", [madeup_out / f"{name}_conf.pdf"], po_number, d.clock())


def test_the_runner_empties_the_work_folder_at_its_start(tmp_path, madeup_out, monkeypatch):
    """A part that was ended in the middle of its copy (the attachments of an e-mail, the PDF that Copilot reads) leaves it in
    <cases_root>/tmp/work. The runner empties that folder at its own start, before its first pass, also when STOP keeps it from
    making one: only what lies in the folder goes, and the folder itself, the review page's files beside it and the cases stay. A
    failure to empty it is one line in runner.log, which names no file, and the runner goes on with its passes."""
    d = loop_deps(tmp_path, madeup_out)
    cases = d.settings.cases_root
    work = cases / "tmp" / "work"
    left = [work / "aid_mailbox_x" / "0a1b2c3d4e5f" / "MU07_conf.pdf", work / "aid-copilot-y" / "document.pdf", work / "stray.pdf"]
    for path in left:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF made-up")
    page_file = cases / "tmp" / "0a1b2c" / "page_1.png"                  # the review page's, beside the folder work
    page_file.parent.mkdir(parents=True)
    page_file.write_bytes(b"made-up")
    case = opened(d, madeup_out, "MU01", "250101")
    stop = tmp_path / "STOP"
    stop.write_text("", encoding="utf-8")
    assert run_loop(d, stop, poll_seconds=0) == 0                          # no pass, and the folder is emptied all the same
    assert work.is_dir() and list(work.iterdir()) == [] and page_file.is_file() and (case.root / "case.json").is_file()
    assert not (cases / "runner.log").exists()
    # a folder that cannot be emptied: one line in the log, and the pass is made
    stop.unlink()
    (work / "aid_mailbox_z").mkdir()
    (work / "aid_mailbox_z" / "MU08_conf.pdf").write_bytes(b"%PDF made-up")

    def refused(path, *args, **kwargs):
        raise PermissionError(13, "made-up: in use", str(path))
    monkeypatch.setattr(aid.runner, "shutil", SimpleNamespace(rmtree=refused))
    assert run_loop(d, stop, poll_seconds=0, max_passes=1) == 1
    assert (case.root / "steps" / "08_result.json").exists()                # the runner went on
    [line] = [line for line in (cases / "runner.log").read_text(encoding="utf-8").splitlines() if " work folder: " in line]
    assert line.endswith("work folder: PermissionError: 1 of the 1 copies left in tmp/work could not be removed; the runner goes on")
    assert (work / "aid_mailbox_z" / "MU08_conf.pdf").is_file()


def test_the_runner_removes_the_copies_of_drops_cut_off_at_its_start(tmp_path, madeup_out, monkeypatch):
    """Ruling Y15 of Task 33: at its start, as it empties tmp/work, also when STOP keeps it from making a pass, the runner removes from
    the watched folder the copies that drops cut off left there (remove_leftovers of aid/sources/dropin.py): drop's own temporary files,
    older than ten minutes. A younger one, a dot file of another program and the files to scan stay. A copy that cannot be removed is
    one line in runner.log, which names no file, and the runner goes on with its passes."""
    d = loop_deps(tmp_path, madeup_out)
    inbox = d.settings.watched_folder
    old, young, other = inbox / ".dropin_k3j2h1.part", inbox / ".dropin_a1b2c3.part", inbox / ".DS_Store"
    for path, age in ((old, 3600), (young, 60), (other, 3600)):
        path.write_bytes(b"%PDF made-up")
        os.utime(path, (time.time() - age, time.time() - age))
    stop = tmp_path / "STOP"
    stop.write_text("", encoding="utf-8")
    assert run_loop(d, stop, poll_seconds=0) == 0
    assert not old.exists() and young.exists() and other.exists() and not (d.settings.cases_root / "runner.log").exists()
    stop.unlink()
    monkeypatch.setattr(aid.runner, "remove_leftovers", lambda folder: [PermissionError(13, "made-up: in use"), PermissionError(13, "made-up")])
    case = opened(d, madeup_out, "MU01", "250101")
    assert run_loop(d, stop, poll_seconds=0, max_passes=1) == 1 and (case.root / "steps" / "08_result.json").exists()
    monkeypatch.setattr(aid.runner, "remove_leftovers", lambda folder: [PermissionError(13, "made-up: in use")])
    assert run_loop(d, stop, poll_seconds=0, max_passes=1) == 1
    lines = [line for line in (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines() if " watched folder: " in line]
    assert [line.partition(" watched folder: ")[2] for line in lines] == [
        "PermissionError: 2 copies of drops cut off could not be removed from the watched folder; the runner goes on",
        "PermissionError: 1 copy of a drop cut off could not be removed from the watched folder; the runner goes on"]       # ruling Y24


def test_a_case_left_without_a_result_is_run_on_the_next_pass(tmp_path, madeup_out):
    d = loop_deps(tmp_path, madeup_out)
    case = opened(d, madeup_out, "MU01", "250101")
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1) == 1
    assert (case.root / "steps" / "08_result.json").exists() and (case.root / "dispositions.csv").exists()


def test_a_case_already_run_is_left_alone(tmp_path, madeup_out):
    d = loop_deps(tmp_path, madeup_out)
    case = opened(d, madeup_out, "MU01", "250101")
    record_dispositions(case, run_case(case, d))
    result, dispositions = (case.root / "steps" / "08_result.json").read_bytes(), (case.root / "dispositions.csv").read_bytes()
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2) == 2
    assert (case.root / "steps" / "08_result.json").read_bytes() == result
    assert (case.root / "dispositions.csv").read_bytes() == dispositions
    assert not (d.settings.cases_root / "runner.log").exists()


def test_without_a_mailer_the_digest_is_written_to_files_with_the_duplicate_skipped(tmp_path, madeup_out):
    d = loop_deps(tmp_path, madeup_out, clock=AFTER_DIGEST_TIME)
    inbox = d.settings.watched_folder
    shutil.copy(madeup_out / "MU01_conf.pdf", inbox); shutil.copy(madeup_out / "MU01_conf.pdf", inbox / "MU01_copy.pdf")
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1) == 1
    digests = d.settings.cases_root / "digests"
    text = (digests / "2026-11-12.txt").read_text(encoding="utf-8")
    assert "MU01_copy.pdf: duplicate" in text and "PO 250101" in text
    assert "MU01_copy.pdf" in (digests / "2026-11-12.html").read_text(encoding="utf-8")
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert state == {"last_sent": AFTER_DIGEST_TIME.isoformat(), "reported": [], "skipped": []}


def test_before_the_digest_time_no_digest_is_made_and_the_day_is_kept(tmp_path, madeup_out):
    d = loop_deps(tmp_path, madeup_out)                          # the clock of deps() stands at 9:00
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert not (d.settings.cases_root / "digests").exists()
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert state["last_sent"] is None and [case_id[:6] for case_id in state["reported"]] == ["250101"] and state["skipped"] == []


def test_the_digest_goes_through_the_mailer_once_a_day(tmp_path, madeup_out):
    out = tmp_path / "prepared"
    d = loop_deps(tmp_path, madeup_out, clock=AFTER_DIGEST_TIME, mailer=FileMailer(out, sender="aid@example.com", clock=lambda: AFTER_DIGEST_TIME))
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    sent = list(out.glob("*.eml"))
    assert len(sent) == 1 and "Dagoverzicht 12-11-2026" in sent[0].read_text(encoding="utf-8")
    digests = d.settings.cases_root / "digests"                       # the HTML is kept for a flow to pick up; the text was sent
    assert [p.name for p in digests.iterdir()] == ["2026-11-12.html"] and "PO 250101" in (digests / "2026-11-12.html").read_text(encoding="utf-8")
    shutil.copy(madeup_out / "MU02_conf.pdf", d.settings.watched_folder)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)      # the same day: the second case waits for tomorrow's digest
    assert list(out.glob("*.eml")) == sent
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert [case_id[:6] for case_id in state["reported"]] == ["250102"]


def test_a_case_that_raises_is_logged_and_the_pass_goes_on(tmp_path, madeup_out, monkeypatch):
    d = loop_deps(tmp_path, madeup_out)
    broken, fine = opened(d, madeup_out, "MU01", "250101"), opened(d, madeup_out, "MU02", "250102")

    def run_or_raise(case, deps, rerun=False):
        if case.case_id == broken.case_id:
            raise RuntimeError("made-up failure")
        return run_case(case, deps, rerun)
    monkeypatch.setattr(aid.runner, "run_case", run_or_raise)
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2) == 2
    assert (fine.root / "steps" / "08_result.json").exists() and not (broken.root / "steps" / "08_result.json").exists()
    log = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1 and broken.case_id in log[0] and "RuntimeError: made-up failure" in log[0]   # once in the session


def test_a_case_that_keeps_failing_is_logged_once_and_its_success_once(tmp_path, madeup_out, monkeypatch):
    d = loop_deps(tmp_path, madeup_out)
    case = opened(d, madeup_out, "MU01", "250101")
    calls = []

    def fail_twice(case, deps, rerun=False):
        calls.append(1)
        if len(calls) <= 2:
            raise RuntimeError("made-up failure")
        return run_case(case, deps, rerun)
    monkeypatch.setattr(aid.runner, "run_case", fail_twice)
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=3) == 3
    log = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == 2 and "RuntimeError" in log[0] and case.case_id in log[1] and "after an earlier failure" in log[1]


def test_a_torn_case_file_is_logged_once(tmp_path, madeup_out):
    d = loop_deps(tmp_path, madeup_out)
    torn = d.settings.cases_root / "250101_20261112T090000"; (torn / "steps").mkdir(parents=True)
    (torn / "case.json").write_text('{"case_id": "250101_2026', encoding="utf-8")
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=3) == 3
    log = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1 and torn.name in log[0]


def test_a_case_with_a_result_but_no_dispositions_gets_them_and_is_reported(tmp_path, madeup_out):
    d = loop_deps(tmp_path, madeup_out)
    case = opened(d, madeup_out, "MU01", "250101")
    run_case(case, d)                                            # the process died before the dispositions and the report
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1) == 1
    assert len((case.root / "dispositions.csv").read_text(encoding="utf-8").splitlines()) >= 1
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert state["reported"] == [case.case_id]


def test_dot_files_and_folders_are_ignored(tmp_path, madeup_out):
    d = loop_deps(tmp_path, madeup_out, clock=AFTER_DIGEST_TIME)
    inbox, cases = d.settings.watched_folder, d.settings.cases_root
    (inbox / ".ipynb_checkpoints").mkdir(); (inbox / ".DS_Store").write_bytes(b"made-up")
    hidden = cases / ".ipynb_checkpoints" / "copy"; hidden.mkdir(parents=True)
    shutil.copy(madeup_out / "MU01_conf.pdf", inbox)
    shutil.copytree(opened(d, madeup_out, "MU02", "250102").root, hidden, dirs_exist_ok=True)   # a case's files under a dot folder
    shutil.rmtree(cases / "250102_20261112T170000")
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1) == 1
    assert not (hidden / "steps" / "08_result.json").exists()
    assert ".DS_Store" not in (cases / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")
    assert not (cases / "runner.log").exists()


@pytest.mark.parametrize("content", ['{"last_sent": nu', '{"last_sent": null, "reported": []}', '[]',
                                     '{"last_sent": null, "reported": [], "skipped": [], "copilot": {"since": "2026-11-12T08:00:00", "error": "x"}}'])
def test_a_state_file_that_cannot_be_used_starts_a_fresh_state(tmp_path, madeup_out, content):
    d = loop_deps(tmp_path, madeup_out)
    d.settings.cases_root.mkdir(parents=True); (d.settings.cases_root / "digest_state.json").write_text(content, encoding="utf-8")
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1) == 1
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert state["last_sent"] is None and [case_id[:6] for case_id in state["reported"]] == ["250101"] and state["skipped"] == []
    log = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1 and "digest_state.json" in log[0] and "fresh" in log[0]


def test_a_missing_watched_folder_is_created(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    inbox = tmp_path / "drive" / "inbox"
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"watched_folder": inbox})})
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1) == 1
    assert inbox.is_dir() and not (d.settings.cases_root / "runner.log").exists()


def digests_in(folder):
    return [path for path in folder.glob("*.eml") if "Dagoverzicht" in path.read_text(encoding="utf-8")]


def test_per_case_mode_makes_a_digest_after_each_pass_that_handled_a_case(tmp_path, madeup_out):
    out = tmp_path / "prepared"
    d = loop_deps(tmp_path, madeup_out, mailer=FileMailer(out, clock=lambda: datetime(2026, 11, 12, 9, 0)))   # 9:00, before digest_time
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"digest": "per_case"})})
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert len(digests_in(out)) == 1
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)         # nothing happened: no digest
    assert len(digests_in(out)) == 1
    shutil.copy(madeup_out / "MU02_conf.pdf", d.settings.watched_folder)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert len(digests_in(out)) == 2


def test_an_escalation_is_reported_in_the_next_digest(tmp_path, madeup_out):
    now = [AFTER_DIGEST_TIME]
    out = tmp_path / "prepared"
    d = loop_deps(tmp_path, madeup_out, mailer=FileMailer(out, clock=lambda: now[0]))
    d = d.model_copy(update={"clock": lambda: now[0],
                             "settings": d.settings.model_copy(update={"supplier_emails": {"S11": "s11@example.com"}})})
    shutil.copy(madeup_out / "MU11_conf.pdf", d.settings.watched_folder)    # MU11 waits for the supplier's answer
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert len(digests_in(out)) == 1
    now[0] = datetime(2026, 11, 18, 17, 0)                                   # past the three working days of the wait
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    latest = [path for path in digests_in(out) if "18-11-2026" in path.read_text(encoding="utf-8")]
    assert len(latest) == 1
    text = latest[0].read_text(encoding="utf-8")
    assert "Wacht op u" in text and "250111" in text


class RaisingMailer:
    name = "raising"

    def send(self, msg):
        raise MailerError("made-up failure")


def test_a_digest_the_mailer_refuses_is_made_again_at_the_next_pass(tmp_path, madeup_out):
    d = loop_deps(tmp_path, madeup_out, clock=AFTER_DIGEST_TIME, mailer=RaisingMailer())
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert state["last_sent"] is None and len(state["reported"]) == 1
    assert "digest: MailerError: made-up failure" in (d.settings.cases_root / "runner.log").read_text(encoding="utf-8")
    out = tmp_path / "prepared"
    d = d.model_copy(update={"mailer": FileMailer(out, clock=lambda: AFTER_DIGEST_TIME)})
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert len(digests_in(out)) == 1 and "PO 250101" in digests_in(out)[0].read_text(encoding="utf-8")
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert state == {"last_sent": AFTER_DIGEST_TIME.isoformat(), "reported": [], "skipped": []}


# --- the Deps of the command (ruling 5) ---

def settings_file(tmp_path, **values):
    """A made-up settings file in tmp_path with relative folders; values replace those of the tests' settings."""
    data = json.loads(SETTINGS.model_dump_json()) | {"cases_root": "cases", "po_store_root": "po_store", "watched_folder": "inbox",
                                                       "reader": "parser"} | values
    path = tmp_path / "settings.json"; path.write_text(json.dumps(data), encoding="utf-8")
    return load_settings(path), path


def test_build_deps_without_exact_or_smtp_prepares_and_files(tmp_path):
    settings, _ = settings_file(tmp_path)
    d = build_deps(settings, {})
    assert d.reader is read_with_parser and isinstance(d.decider, NoDecider)
    assert isinstance(d.writer, PreparedWriter) and d.writer.out_dir == tmp_path / "cases" / "prepared"
    assert isinstance(d.mailer, FileMailer) and d.mailer.out_dir == tmp_path / "cases" / "outbox" and d.mailer.sender == SETTINGS.aid_email
    assert d.store.root == tmp_path / "po_store"


def test_build_deps_with_exact_and_smtp(tmp_path):
    settings, _ = settings_file(tmp_path, exact_base_url="https://exact.example.com/api", smtp_host="smtp.example.com", smtp_user="aid@example.com",
                                exact_endpoints={"po_line": "po/{po}/lines/{line}"}, exact_fields={"po_line_price": "NetPrice"})
    environ = {"AID_MAIL_PASSWORD": "made-up-password", "AID_EXACT_TOKEN": "made-up-token"}
    d = build_deps(settings, environ)
    assert isinstance(d.writer, RetryingWriter) and isinstance(d.writer.primary, ExactApiWriter) and isinstance(d.writer.fallback, PreparedWriter)
    exact = d.writer.primary
    assert exact.base_url == "https://exact.example.com/api" and exact.endpoints == {"po_line": "po/{po}/lines/{line}"}
    assert exact.fields["po_line_price"] == "NetPrice" and exact.token() == "made-up-token"
    environ["AID_EXACT_TOKEN"] = "changed"; assert exact.token() == "changed"           # read at each call
    del environ["AID_EXACT_TOKEN"]
    with pytest.raises(WriterError, match="AID_EXACT_TOKEN"):
        exact.token()
    assert isinstance(d.mailer, SmtpMailer) and (d.mailer.host, d.mailer.port, d.mailer.user, d.mailer.sender) == (
        "smtp.example.com", 587, "aid@example.com", SETTINGS.aid_email)


def test_build_deps_without_the_mailbox_password_files_the_messages(tmp_path):
    settings, _ = settings_file(tmp_path, smtp_host="smtp.example.com", smtp_user="aid@example.com")
    assert isinstance(build_deps(settings, {}).mailer, FileMailer)


@pytest.mark.parametrize("ids", [{}, {"ms_tenant_id": TENANT_ID}, {"ms_client_id": CLIENT_ID}])
def test_the_llm_reader_is_refused_without_the_tenant_and_the_client(tmp_path, capsys, ids):
    settings, path = settings_file(tmp_path, reader="llm", **ids)
    refusal = ("reader 'llm' needs the Copilot sign-in: fill the tenant and the client on the Settings tab and sign in for Copilot")
    with pytest.raises(ValueError) as raised:
        build_deps(settings, {}, account=copilot_account(tmp_path))
    assert str(raised.value) == refusal
    assert main(["--settings", str(path), "--max-passes", "1", "--poll-seconds", "0"]) == 2
    assert capsys.readouterr().err.splitlines()[-1] == f"aid.runner: {refusal}"
    assert not (tmp_path / "inbox").exists()


def test_main_runs_the_loop(tmp_path):
    _, path = settings_file(tmp_path)
    assert main(["--settings", str(path), "--max-passes", "1", "--poll-seconds", "0"]) == 0
    assert (tmp_path / "inbox").is_dir() and (tmp_path / "cases" / "digest_state.json").exists()



# --- the final review (findings 1, 2 and 3) ---

def test_a_file_that_stays_in_the_inbox_as_an_error_is_listed_once_and_logged_once(tmp_path, madeup_out):
    """A PDF that cannot be read stays in the inbox and is tried again at each pass: the digest state lists it once and runner.log
    has one line for it in a run of the loop."""
    d = loop_deps(tmp_path, madeup_out)
    (d.settings.watched_folder / "bad.pdf").write_bytes(b"not a PDF")
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=4) == 4
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert [name for name, _ in state["skipped"]] == ["bad.pdf"] and state["skipped"][0][1].startswith("error: ")
    log = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1 and "bad.pdf" in log[0]


def test_a_damaged_case_file_does_not_stop_the_intake(tmp_path, madeup_out):
    """A case folder whose case.json is torn is logged once; the confirmations that arrive are still opened and run."""
    d = loop_deps(tmp_path, madeup_out)
    torn = d.settings.cases_root / "250100_20261112T080000"; torn.mkdir(parents=True)
    (torn / "case.json").write_text('{"case_id": "250100_2026', encoding="utf-8")
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2) == 2
    assert (d.settings.watched_folder / "processed" / "MU01_conf.pdf").exists()
    assert len(list(d.settings.cases_root.glob("250101_*/steps/08_result.json"))) == 1
    log = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1 and torn.name in log[0]


class NoWrites:
    """A writer and a mailer that fail when they are used: a case cut off after its writes is not run again."""
    name = "none"

    def apply(self, *args):
        raise AssertionError("a write was applied again")

    def send(self, msg):
        raise AssertionError("a message was sent again")


def test_a_case_cut_off_after_a_write_is_finished_in_review_and_reported(tmp_path, madeup_out):
    """A folder prepared from a cut-off run: MU01 run, then its result and audit removed and one entry left in its write log. The
    next pass finishes it as review, writes and sends nothing, records its dispositions and reports it."""
    d = loop_deps(tmp_path, madeup_out)
    case = opened(d, madeup_out, "MU01", "250101")
    result = run_case(case, d)
    (case.root / "steps" / "08_result.json").unlink(); (case.root / "audit.json").unlink()
    (case.root / "write_log.jsonl").write_text(result.write_entries[0].model_dump_json() + "\n", encoding="utf-8")
    d = d.model_copy(update={"writer": NoWrites(), "mailer": NoWrites()})
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1) == 1
    finished = case.read_step("result")
    assert finished["status"] == "review" and [i["reason"] for i in finished["review_items"]] == ["run cut off after writes: check the write log"]
    assert len(finished["write_entries"]) == 1 and len((case.root / "write_log.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert (case.root / "audit.json").exists() and (case.root / "dispositions.csv").exists()
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert state["reported"] == [case.case_id] and not (d.settings.cases_root / "runner.log").exists()


def test_a_settings_file_that_fails_stops_the_command_without_its_values(tmp_path, capsys):
    """A wrong authorization amount stops the command with exit code 2 and the field named; the amount is never printed."""
    _, path = settings_file(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8")) | {"authorization_limit": "12,345.67"}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert main(["--settings", str(path), "--max-passes", "1", "--poll-seconds", "0"]) == 2
    err = capsys.readouterr().err
    assert "authorization_limit" in err and "12,345.67" not in err and "Traceback" not in err
    assert not (tmp_path / "inbox").exists()



# --- round 2: the answers in the runner ---

def answer_to_the_question(out, inbox, name="antwoord.eml", subject="Re: Vraag over orderbevestiging PO 250111"):
    """A made-up reply in the inbox to the question the file mailer wrote to `out`."""
    from email import message_from_bytes, policy
    [question] = [path for path in out.glob("*.eml") if "Dagoverzicht" not in path.read_text(encoding="utf-8")]
    message_id = message_from_bytes(question.read_bytes(), policy=policy.default)["Message-ID"]
    (inbox / name).write_bytes(f"From: s11@example.com\nTo: aid@example.com\nSubject: {subject}\nIn-Reply-To: {message_id}\n\n"
                               "De hoeveelheid klopt.\n".encode())
    return question


def waiting_mu11(tmp_path, madeup_out, now):
    out = tmp_path / "prepared"
    d = loop_deps(tmp_path, madeup_out, mailer=FileMailer(out, sender="aid@example.com", clock=lambda: now[0]))
    d = d.model_copy(update={"clock": lambda: now[0],
                             "settings": d.settings.model_copy(update={"supplier_emails": {"S11": "s11@example.com"}})})
    shutil.copy(madeup_out / "MU11_conf.pdf", d.settings.watched_folder)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    [case] = d.settings.cases_root.glob("250111_*")
    return d, out, case


def test_a_reply_is_reported_in_the_next_digest(tmp_path, madeup_out):
    now = [AFTER_DIGEST_TIME]
    d, out, case = waiting_mu11(tmp_path, madeup_out, now)
    answer_to_the_question(out, d.settings.watched_folder)
    now[0] = datetime(2026, 11, 13, 17, 0)                                   # the next day, before any wait has ended
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    [latest] = [path for path in digests_in(out) if "13-11-2026" in path.read_text(encoding="utf-8")]
    text = latest.read_text(encoding="utf-8")
    block = text[text.index("Wacht op u"):]
    assert "250111" in block and "supplier answered: read the reply" in block


def test_a_bounce_is_not_reported_as_an_answer(tmp_path, madeup_out):
    now = [datetime(2026, 11, 12, 9, 0)]                                     # before the digest time: the state keeps what is reported
    d, out, case = waiting_mu11(tmp_path, madeup_out, now)
    state = d.settings.cases_root / "digest_state.json"
    reported = json.loads(state.read_text(encoding="utf-8"))["reported"]
    answer_to_the_question(out, d.settings.watched_folder, name="auto.eml", subject="Automatisch antwoord: afwezig")
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert json.loads(state.read_text(encoding="utf-8"))["reported"] == reported and (case / "messages" / "auto" / "auto.eml").exists()


def test_a_reply_to_a_case_with_a_damaged_result_leaves_one_copy(tmp_path, madeup_out):
    now = [datetime(2026, 11, 12, 9, 0)]
    d, out, case = waiting_mu11(tmp_path, madeup_out, now)
    result = case / "steps" / "08_result.json"; good = result.read_bytes(); result.write_text("{", encoding="utf-8")
    answer_to_the_question(out, d.settings.watched_folder)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2)
    assert (d.settings.watched_folder / "antwoord.eml").exists() and not (case / "messages" / "replies").exists()
    result.write_bytes(good)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert [p.name for p in (case / "messages" / "replies").iterdir()] == ["antwoord.eml"]
    assert json.loads(result.read_text(encoding="utf-8"))["status"] == "review"


def test_a_reply_to_a_cut_off_case_is_filed_and_the_case_finished_in_review(tmp_path, madeup_out):
    from aid.messaging import SentRecord, record_sent
    d = loop_deps(tmp_path, madeup_out)
    case = opened(d, madeup_out, "MU11", "250111")
    record_sent(case, SentRecord(message_id="ab" * 16, to=["s11@example.com"], subject="s", sent_at=d.clock().isoformat(), template="quantity_unit"))
    (d.settings.watched_folder / "antwoord.eml").write_bytes(
        f"From: s11@example.com\nTo: aid@example.com\nSubject: Re: vraag\nIn-Reply-To: <{'ab' * 16}@example.com>\n\nKlopt.\n".encode())
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert (case.root / "messages" / "replies" / "antwoord.eml").exists()
    finished = case.read_step("result")
    assert finished["status"] == "review" and [i["reason"] for i in finished["review_items"]] == ["run cut off after writes: check the write log"]


# --- the AI service: Microsoft 365 Copilot (Task 27) ---

def copilot_account(tmp_path, app: FakeApp | None = None) -> MicrosoftAccount:
    """The account of the fake sign-in, signed in unless another application is given."""
    return MicrosoftAccount(TENANT_ID, CLIENT_ID, SecretStore(tmp_path / "secrets"), app=app or FakeApp(accounts=[USERNAME]))


def copilot_deps(tmp_path, madeup_out, session, app: FakeApp | None = None, waits: list | None = None, clock=None):
    """loop_deps with the reader and the decider that build_deps makes for reader "llm" over the fake sign-in and the session; the
    client's waits for a Retry-After are noted in `waits` instead of waited."""
    d = loop_deps(tmp_path, madeup_out, clock=clock)
    (tmp_path / "settings").mkdir(exist_ok=True)
    settings, _ = settings_file(tmp_path / "settings", reader="llm", ms_tenant_id=TENANT_ID, ms_client_id=CLIENT_ID)
    built = build_deps(settings, {}, account=copilot_account(tmp_path, app), session=session)
    built.decider.client.sleep = (waits if waits is not None else []).append
    return d.model_copy(update={"reader": built.reader, "decider": built.decider})


class Unreachable(FakeApp):
    """A sign-in whose renewal of the token cannot reach Microsoft, as msal raises it when no cached token is left."""

    def acquire_token_silent_with_error(self, scopes, account, **kwargs):
        self.calls.append(("acquire_token_silent_with_error", list(scopes), {}))
        if self.offline:
            raise requests.ConnectionError("made-up detail: login.microsoftonline.com unreachable")
        return super().acquire_token_silent_with_error(scopes, account, **kwargs)

    offline = True


def test_reader_llm_builds_the_copilot_reader_and_decider_over_the_sign_in_of_the_settings(tmp_path, madeup_out):
    from aid.tests.helpers import answer_from_spec
    (tmp_path / "settings").mkdir()
    settings, _ = settings_file(tmp_path / "settings", reader="llm", ms_tenant_id=TENANT_ID, ms_client_id=CLIENT_ID)
    signed, session = copilot_account(tmp_path), copilot_session(answer_from_spec("MU01"))
    d = build_deps(settings, {}, account=signed, session=session)
    assert isinstance(d.decider, LlmDecider) and isinstance(d.decider.client, CopilotClient)
    copilot = d.decider.client
    assert (copilot.account, copilot.session, copilot.time_zone, d.decider.name) == (signed, session, "Europe/Amsterdam", "model:microsoft-365-copilot")
    assert copilot.work == settings.cases_root / "tmp" / "work"           # the copy of a PDF it reads: under the cases, never in %TEMP%
    cart = d.reader(madeup_out / "CART01.html", "cart")                     # the cart reader, which needs no model
    assert cart.reader == "cart_html" and session.calls == []
    doc, response = d.reader(madeup_out / "MU01_conf.pdf", "confirmation")
    assert doc.reader == "llm:microsoft-365-copilot:v1" and response.request_id == "made-up-conversation-0001"
    assert [call["url"] for call in session.calls] == [COPILOT, f"{COPILOT}/made-up-conversation-0001/chat"]
    assert isinstance(build_deps(settings.model_copy(update={"reader": "parser"}), {}, account=signed).decider, NoDecider)


def test_the_reader_and_the_decider_share_one_copilot_client(tmp_path, madeup_out):
    """A retry of the reader waits through the decider's client, so the two are one client."""
    from aid.tests.helpers import answer_from_spec
    (tmp_path / "settings").mkdir()
    settings, _ = settings_file(tmp_path / "settings", reader="llm", ms_tenant_id=TENANT_ID, ms_client_id=CLIENT_ID)
    session = copilot_session(FakeResponse(503, headers={"Retry-After": "3"}), answer_from_spec("MU01"))
    d, waits = build_deps(settings, {}, account=copilot_account(tmp_path), session=session), []
    d.decider.client.sleep = waits.append
    doc, _ = d.reader(madeup_out / "MU01_conf.pdf", "confirmation")
    assert waits == [3] and len(doc.lines) == 3


def test_reader_llm_makes_the_account_of_the_settings_with_the_store_of_the_pc(tmp_path, monkeypatch):
    (tmp_path / "settings").mkdir()
    settings, _ = settings_file(tmp_path / "settings", reader="llm", ms_tenant_id=TENANT_ID, ms_client_id=CLIENT_ID)
    monkeypatch.setattr(aid.runner, "default_secrets_dir", lambda: tmp_path / "store")
    d = build_deps(settings, {})
    made = d.decider.client.account
    assert (made.tenant_id, made.client_id, made.store.directory) == (TENANT_ID, CLIENT_ID, (tmp_path / "store").resolve())


def test_a_copilot_error_during_a_case_leaves_it_without_a_result_for_the_next_pass(tmp_path, madeup_out):
    """Copilot answers 503 twice in the first pass: the case keeps no result and no audit record, one line in runner.log names the
    error and never the token, and the next pass reads the case through Copilot."""
    from aid.tests.helpers import answer_from_spec
    busy = FakeResponse(503, headers={"Retry-After": "1"})
    session = copilot_session(answer_from_spec("MU01"), created=[busy, busy, FakeResponse(201, json_data={"id": "made-up-conversation-0001"})])
    d = copilot_deps(tmp_path, madeup_out, session)
    case = opened(d, madeup_out, "MU01", "250101")
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1) == 1
    root = d.settings.cases_root
    assert not (case.root / "steps" / "08_result.json").exists() and not (case.root / "audit.json").exists()
    assert not (case.root / "dispositions.csv").exists()
    [line] = (root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert line.endswith(f"{case.case_id}: CopilotError: Copilot refused: 503 no code") and ACCESS_TOKEN not in line
    assert json.loads((root / "digest_state.json").read_text(encoding="utf-8"))["reported"] == []
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1) == 1
    result = case.read_step("result")
    assert result["status"] != "failed" and (case.root / "dispositions.csv").exists()
    audit = json.loads((case.root / "audit.json").read_text(encoding="utf-8"))
    assert [(call["kind"], call["model"], call["request_id"]) for call in audit["calls"]] == [
        ("reader", "microsoft-365-copilot", "made-up-conversation-0001")]
    assert {model["role"]: model["name"] for model in audit["model"]} == {"reader": "microsoft-365-copilot", "decider": "microsoft-365-copilot"}
    assert ACCESS_TOKEN not in (root / "runner.log").read_text(encoding="utf-8") + (case.root / "audit.json").read_text(encoding="utf-8")


def test_a_network_failure_while_the_token_is_renewed_leaves_the_case_for_the_next_pass(tmp_path, madeup_out):
    """The PC wakes before its network: the renewal of the token cannot reach Microsoft. The case keeps no result, and the next pass,
    with the network back, reads it through Copilot."""
    from aid.tests.helpers import answer_from_spec
    app, session = Unreachable(accounts=[USERNAME]), copilot_session(answer_from_spec("MU01"))
    d = copilot_deps(tmp_path, madeup_out, session, app=app)
    case = opened(d, madeup_out, "MU01", "250101")
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1) == 1
    assert not (case.root / "steps" / "08_result.json").exists() and session.calls == []
    [line] = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert line.endswith(f"{case.case_id}: CopilotError: Copilot could not be reached: ConnectionError")
    assert "login.microsoftonline.com" not in line
    app.offline = False
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert case.read_step("result")["status"] != "failed" and len(session.calls) == 2


def test_after_a_service_failure_the_other_cases_of_the_pass_wait_for_the_next_pass(tmp_path, madeup_out):
    """Copilot asks the aid to wait (429) on every request: the first case asks twice, as the client does, and the three others of
    the pass are not sent at all. One line in runner.log; the next pass, with Copilot back, reads all four."""
    from aid.tests.helpers import answer_from_spec
    busy = FakeResponse(429, headers={"Retry-After": "30"})
    session, waits = copilot_session(answer_from_spec("MU01"), created=[busy, busy, FakeResponse(201, json_data={"id": "made-up-conversation-0001"})]), []
    d = copilot_deps(tmp_path, madeup_out, session, waits=waits)
    cases = [opened(d, madeup_out, name, f"25010{n}") for n, name in enumerate(("MU01", "MU02", "MU03", "MU04"), 1)]
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert [call["url"] for call in session.calls] == [COPILOT, COPILOT] and waits == [30]
    assert not any((case.root / "steps" / "08_result.json").exists() for case in cases)
    [line] = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert line.endswith(f"{cases[0].case_id}: CopilotError: Copilot refused: 429 no code")
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert {key: state["copilot"][key] for key in ("error", "waiting")} == {"error": "Copilot refused: 429 no code", "waiting": 4}
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert all((case.root / "steps" / "08_result.json").exists() for case in cases)
    assert "copilot" not in json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))


def test_a_conversation_copilot_ends_for_one_case_does_not_hold_back_the_others(tmp_path, madeup_out):
    """Copilot ends the conversation of the first case (its own refusal of that document): that case keeps no result, and the pass
    goes on with the next case, which is read."""
    from aid.tests.fakes import copilot_answer, sent_text
    from aid.tests.helpers import answer_from_spec
    ended = lambda request: copilot_answer("Sorry, I can't chat about this.", sent_text(request), state="disengagedForRai")  # noqa: E731
    session = copilot_session(ended, answer_from_spec("MU02"))
    d = copilot_deps(tmp_path, madeup_out, session)
    first, second = opened(d, madeup_out, "MU01", "250101"), opened(d, madeup_out, "MU02", "250102")
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert not (first.root / "steps" / "08_result.json").exists() and (second.root / "steps" / "08_result.json").exists()
    [line] = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert line.endswith(f"{first.case_id}: CopilotError: Copilot ended the conversation: disengagedForRai")
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert "copilot" not in state and state["copilot_cases"] == {first.case_id: {"refused": 1, "failed": 0, "read_after": True}}


def test_the_digest_names_copilot_and_the_cases_that_wait_for_it(tmp_path, madeup_out):
    session = copilot_session(created=FakeResponse(403, json_data={"error": {"code": "Forbidden", "message": "made-up"}}))
    d = copilot_deps(tmp_path, madeup_out, session, clock=AFTER_DIGEST_TIME)
    opened(d, madeup_out, "MU01", "250101"), opened(d, madeup_out, "MU02", "250102")
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    text = (d.settings.cases_root / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")
    assert "Copilot werkt niet (Copilot refused: 403 Forbidden): 2 zaken wachten erop" in text and "made-up" not in text
    assert "Copilot werkt niet" in (d.settings.cases_root / "digests" / "2026-11-12.html").read_text(encoding="utf-8")


def failing_for(po_number: str, answer: str):
    """A chat that answers 500 for the document of the PO and the answer for any other document."""
    from aid.tests.fakes import copilot_answer, sent_text

    def chat(request):
        if any(po_number in entry["text"] for entry in json.loads(request["data"]).get("additionalContext", [])):
            return FakeResponse(500)
        return copilot_answer(answer, sent_text(request))
    return chat


def test_a_case_that_keeps_failing_goes_last_and_is_finished_as_failed_after_its_third_failure(tmp_path, madeup_out):
    """MU01's chat always answers 500. Pass 1 asks MU01 first: the failure stops the pass. Pass 2 asks MU01 last, so MU02 is read
    first; MU01 fails a second time. Pass 3 fails MU01 a third time, after Copilot read another case: it is finished as failed."""
    from aid.tests.helpers import answer_from_spec
    session = copilot_session(failing_for("250101", answer_from_spec("MU02")))
    d = copilot_deps(tmp_path, madeup_out, session)
    first, second = opened(d, madeup_out, "MU01", "250101"), opened(d, madeup_out, "MU02", "250102")
    chats = lambda: [c for c in session.calls if c["url"].endswith("/chat")]  # noqa: E731
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert len(chats()) == 1 and not (second.root / "steps" / "08_result.json").exists()
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert "250102" in chats()[1]["data"].decode() and second.read_step("result")["status"] != "failed"
    assert not (first.root / "steps" / "08_result.json").exists()
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    result = first.read_step("result")
    assert result["status"] == "failed" and result["error"] == "ReaderError: Copilot could not read this document: Copilot refused: 500 no code"
    assert len(chats()) == 4 and (first.root / "dispositions.csv").exists()
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert first.case_id in state["reported"] and "copilot_cases" not in state and "copilot" not in state
    assert "finished as failed" in (d.settings.cases_root / "runner.log").read_text(encoding="utf-8")


def test_a_document_copilot_refuses_three_times_is_finished_as_failed(tmp_path, madeup_out):
    from aid.tests.fakes import copilot_answer, sent_text
    ended = lambda request: copilot_answer("Sorry.", sent_text(request), state="disengagedForRai")  # noqa: E731
    d = copilot_deps(tmp_path, madeup_out, copilot_session(ended))
    case = opened(d, madeup_out, "MU01", "250101")
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2)
    assert not (case.root / "steps" / "08_result.json").exists()
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert case.read_step("result")["error"] == "ReaderError: Copilot could not read this document: Copilot ended the conversation: disengagedForRai"


def test_an_outage_never_finishes_a_case(tmp_path, madeup_out):
    """Copilot refuses every request (403) for five passes: nothing is read, so no failure counts toward the cap; the two cases take
    turns at being asked first."""
    session = copilot_session(created=FakeResponse(403, json_data={"error": {"code": "Forbidden"}}))
    d = copilot_deps(tmp_path, madeup_out, session)
    cases = [opened(d, madeup_out, "MU01", "250101"), opened(d, madeup_out, "MU02", "250102")]
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=5)
    assert not any((case.root / "steps" / "08_result.json").exists() for case in cases) and len(session.calls) == 5
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert state["copilot"]["waiting"] == 2 and all(not record["read_after"] for record in state["copilot_cases"].values())


def test_a_400_on_the_create_call_stops_the_pass(tmp_path, madeup_out):
    session = copilot_session(created=FakeResponse(400, json_data={"error": {"code": "BadRequest"}}))
    d = copilot_deps(tmp_path, madeup_out, session)
    for n, name in enumerate(("MU01", "MU02", "MU03"), 1):
        opened(d, madeup_out, name, f"25010{n}")
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2)
    assert len(session.calls) == 2 and len((d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()) == 2


def test_a_refused_document_is_a_line_of_its_own_and_not_copilot_down(tmp_path, madeup_out):
    """MU01's conversation is ended and MU02 is read in the same pass: the digest names the document Copilot could not read, and
    does not say that Copilot does not work."""
    from aid.tests.fakes import copilot_answer, sent_text
    from aid.tests.helpers import answer_from_spec
    ended = lambda request: copilot_answer("Sorry.", sent_text(request), state="disengagedForRai")  # noqa: E731
    d = copilot_deps(tmp_path, madeup_out, copilot_session(ended, answer_from_spec("MU02")), clock=AFTER_DIGEST_TIME)
    opened(d, madeup_out, "MU01", "250101"), opened(d, madeup_out, "MU02", "250102")
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    text = (d.settings.cases_root / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")
    assert "Copilot kon 1 document niet lezen; nieuwe poging bij de volgende ronde" in text and "Copilot werkt niet" not in text


def per_case_copilot(tmp_path, madeup_out, session, now: list, app: FakeApp | None = None):
    """copilot_deps in the per-case mode, with a clock that the test moves on and a file mailer: (the Deps, the mailer's folder)."""
    out = tmp_path / "prepared"
    d = copilot_deps(tmp_path, madeup_out, session, app=app)
    return d.model_copy(update={"settings": d.settings.model_copy(update={"digest": "per_case"}), "clock": lambda: now[0],
                                "mailer": FileMailer(out, clock=lambda: now[0])}), out


def test_in_the_per_case_mode_a_failing_copilot_makes_one_digest_until_it_works_again(tmp_path, madeup_out):
    from datetime import timedelta
    from aid.tests.helpers import answer_from_spec
    down = FakeResponse(403, json_data={"error": {"code": "Forbidden"}})
    up = FakeResponse(201, json_data={"id": "made-up-conversation-0001", "state": "active"})
    session = copilot_session(answer_from_spec("MU01"), created=[down, down, down, up, down])
    now = [datetime(2026, 11, 12, 9, 0)]
    d, out = per_case_copilot(tmp_path, madeup_out, session, now)
    opened(d, madeup_out, "MU01", "250101")
    for _ in range(3):                                                       # three passes, one minute apart: one digest names it
        run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
        now[0] += timedelta(minutes=1)
    assert len(digests_in(out)) == 1 and "Copilot werkt niet" in digests_in(out)[0].read_text(encoding="utf-8")
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)            # Copilot reads the case: the line goes
    now[0] += timedelta(minutes=1)
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert "copilot" not in state
    opened(d, madeup_out, "MU02", "250102")
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)            # it fails again: one more digest names it
    named = [path for path in digests_in(out) if "Copilot werkt niet" in path.read_text(encoding="utf-8")]
    assert len(named) == 2


def test_in_the_per_case_mode_a_lapse_after_a_refused_document_makes_its_digest(tmp_path, madeup_out):
    from datetime import timedelta
    from aid.tests.fakes import copilot_answer, sent_text
    ended = lambda request: copilot_answer("Sorry.", sent_text(request), state="disengagedForRai")  # noqa: E731
    app = FakeApp(accounts=[USERNAME])
    now = [datetime(2026, 11, 12, 9, 0)]
    d, out = per_case_copilot(tmp_path, madeup_out, copilot_session(ended), now, app=app)
    opened(d, madeup_out, "MU01", "250101")
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    now[0] += timedelta(minutes=1)
    app.silent = None                                                        # the sign-in lapses
    opened(d, madeup_out, "MU02", "250102")
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert any("Copilot werkt niet (the Microsoft sign-in has lapsed" in path.read_text(encoding="utf-8") for path in digests_in(out))


def test_a_state_file_that_cannot_be_written_never_stops_the_runner(tmp_path, madeup_out, monkeypatch):
    """While Copilot fails, the runner notes it in the state; a state file that cannot be written is logged once and the loop goes on."""
    write = aid.runner._write_json

    def refused(path, data):
        if path.name == "digest_state.json":
            raise PermissionError("made-up: the file is held by another process")
        write(path, data)
    monkeypatch.setattr(aid.runner, "_write_json", refused)
    session = copilot_session(created=FakeResponse(403, json_data={"error": {"code": "Forbidden"}}))
    d = copilot_deps(tmp_path, madeup_out, session)
    opened(d, madeup_out, "MU01", "250101")
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=3) == 3
    log = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert len([line for line in log if "digest_state.json: PermissionError" in line]) == 1


def test_a_lapsed_sign_in_during_a_case_leaves_it_without_a_result_and_is_logged_once(tmp_path, madeup_out):
    session = copilot_session("{}")
    d = copilot_deps(tmp_path, madeup_out, session, app=FakeApp())                # nobody is signed in
    case = opened(d, madeup_out, "MU01", "250101")
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=3) == 3
    assert not (case.root / "steps" / "08_result.json").exists() and session.calls == []
    [line] = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert f"{case.case_id}: SignInNeeded: no Microsoft account is signed in" in line


def test_a_copilot_error_of_the_decider_during_a_case_leaves_it_without_a_result(tmp_path, madeup_out):
    """MU03's PO has two versions. The reader gives a reading that quotes no version, has no date and whose lines match neither
    version, so that the decider is asked which version it answers: Copilot refuses, and the case keeps no result."""
    from aid.schema import DocValue
    from aid.tests.helpers import document_from_spec
    session = copilot_session(FakeResponse(503), FakeResponse(503))
    d = copilot_deps(tmp_path, madeup_out, session)
    doc = document_from_spec("MU03")
    doc = doc.model_copy(update={"version_hint": DocValue(), "document_date": DocValue(),
                                 "lines": [line.model_copy(update={"item_code": DocValue(value="XX-0000", text="XX-0000")}) for line in doc.lines]})
    d = d.model_copy(update={"reader": lambda path, kind: doc})
    case = opened(d, madeup_out, "MU03", "250103")
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert not (case.root / "steps" / "08_result.json").exists() and (case.root / "steps" / "02_read_document.json").exists()
    [line] = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert line.endswith(f"{case.case_id}: CopilotError: Copilot refused: 503 no code")
    assert [call["url"] for call in session.calls] == [COPILOT] + [f"{COPILOT}/made-up-conversation-0001/chat"] * 2


def test_a_copilot_error_while_the_scan_classifies_leaves_the_file_in_the_inbox(tmp_path, madeup_out):
    """The decider of the scan is the Copilot decider as well: a scan that Copilot cannot class now stays in the inbox, as any file
    that raised does, and is tried again at the next pass, logged once."""
    session = copilot_session(created=FakeResponse(403, json_data={"error": {"code": "Forbidden", "message": "made-up"}}))
    d = copilot_deps(tmp_path, madeup_out, session)
    shutil.copy(madeup_out / "MU05_conf.pdf", d.settings.watched_folder)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2)
    assert (d.settings.watched_folder / "MU05_conf.pdf").exists() and [call["url"] for call in session.calls] == [COPILOT, COPILOT]
    assert len((d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()) == 1
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert state["skipped"] == [["MU05_conf.pdf", "error: Copilot refused: 403 Forbidden"]]


# --- the third review round ---

class LapsesAfter(FakeApp):
    """A sign-in whose renewal Microsoft refuses (invalid_grant) from the `after`-th silent request on."""
    after = 10 ** 6

    def acquire_token_silent_with_error(self, scopes, account, **kwargs):
        self.calls.append(("acquire_token_silent_with_error", list(scopes), {}))
        asked = len([call for call in self.calls if call[0] == "acquire_token_silent_with_error"])
        return {"error": "invalid_grant"} if asked >= self.after else {"access_token": ACCESS_TOKEN, "token_type": "Bearer"}


def test_a_lapse_after_another_case_was_read_stops_the_pass_and_never_counts_toward_the_cap(tmp_path, madeup_out):
    """MU01's chat answers 504 in pass 1 (its chat failed once). Pass 2 reads MU02, then the sign-in lapses before MU01's turn, and
    stays lapsed in pass 3: MU01 is not finished, its failures stay one, and the digest's note says Copilot does not work."""
    from aid.tests.fakes import copilot_answer, sent_text
    from aid.tests.helpers import answer_from_spec
    flaky = {"left": 1}

    def chat(request):
        texts = " ".join(entry["text"] for entry in json.loads(request["data"]).get("additionalContext", []))
        if "250101" in texts and flaky["left"]:
            return FakeResponse(504)
        return copilot_answer(answer_from_spec("MU01" if "250101" in texts else "MU02"), sent_text(request))
    app, session = LapsesAfter(accounts=[USERNAME]), copilot_session(chat)
    d = copilot_deps(tmp_path, madeup_out, session, app=app)
    first, second = opened(d, madeup_out, "MU01", "250101"), opened(d, madeup_out, "MU02", "250102")
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    flaky["left"] = 0
    app.after = len([call for call in app.calls if call[0] == "acquire_token_silent_with_error"]) + 2      # MU02's token, then the lapse
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2)
    state = json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert (second.root / "steps" / "08_result.json").exists() and not (first.root / "steps" / "08_result.json").exists()
    assert state["copilot_cases"][first.case_id] == {"refused": 0, "failed": 1, "read_after": True}
    assert state["copilot"]["error"].startswith("the Microsoft sign-in has lapsed (invalid_grant)")


def test_a_400_on_create_stops_the_pass_whatever_the_cases_failed_before(tmp_path, madeup_out):
    """MU01 and MU02 fail their chats (500) until each has two failures after MU03 was read; then the create call is refused with a
    400: one create call, the pass stops, nothing is finished and nothing counts."""
    from aid.tests.fakes import copilot_answer, sent_text
    from aid.tests.helpers import answer_from_spec
    mode = {"create": None, "poison": {"250101", "250102"}}

    def created(request):
        return mode["create"] or FakeResponse(201, json_data={"id": "made-up-conversation-0001", "state": "active"})

    def chat(request):
        texts = " ".join(entry["text"] for entry in json.loads(request["data"]).get("additionalContext", []))
        name = next(name for name, po in (("MU01", "250101"), ("MU02", "250102"), ("MU03", "250103")) if po in texts)
        if any(po in texts for po in mode["poison"]):
            return FakeResponse(500)
        return copilot_answer(answer_from_spec(name), sent_text(request))
    session = copilot_session(chat, created=created)
    d = copilot_deps(tmp_path, madeup_out, session)
    cases = [opened(d, madeup_out, name, po) for name, po in (("MU01", "250101"), ("MU02", "250102"), ("MU03", "250103"))]
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=3)
    root = d.settings.cases_root
    before = json.loads((root / "digest_state.json").read_text(encoding="utf-8"))["copilot_cases"]
    assert [record["failed"] for record in before.values()] == [2, 2] and all(record["read_after"] for record in before.values())
    mode["create"], mode["poison"] = FakeResponse(400, json_data={"error": {"code": "BadRequest"}}), set()
    calls = len(session.calls)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    state = json.loads((root / "digest_state.json").read_text(encoding="utf-8"))
    assert [call["url"] for call in session.calls[calls:]] == [COPILOT]
    assert not (cases[0].root / "steps" / "08_result.json").exists() and not (cases[1].root / "steps" / "08_result.json").exists()
    assert [record["failed"] for record in state["copilot_cases"].values()] == [2, 2]
    assert state["copilot"]["error"] == "Copilot refused: 400 BadRequest"


def test_a_due_digest_with_a_state_file_that_cannot_be_written_goes_out_once(tmp_path, madeup_out, monkeypatch):
    """Four passes after the digest time, the state file refused each time: one digest, and one line in the log for the file."""
    write = aid.runner._write_json

    def refused(path, data):
        if path.name == "digest_state.json":
            raise PermissionError("made-up: the file is held by another process")
        write(path, data)
    monkeypatch.setattr(aid.runner, "_write_json", refused)
    out = tmp_path / "prepared"
    d = loop_deps(tmp_path, madeup_out, clock=AFTER_DIGEST_TIME, mailer=FileMailer(out, sender="aid@example.com", clock=lambda: AFTER_DIGEST_TIME))
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=4) == 4
    assert len(digests_in(out)) == 1
    log = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1 and "digest_state.json: PermissionError" in log[0]


# --- the line of the changes to enter in Exact (Task 34) ---

def test_the_runner_passes_the_open_changes_and_open_rows_alone_make_no_per_case_digest(tmp_path, madeup_out):
    """The digest names the changes open on the list, read from <cases_root>/prepared, where the command's writer prepares them. In
    the per-case mode open changes alone make no digest; the digest of a case names what is open then, and none when nothing is."""
    from email import message_from_bytes, policy
    from html import escape

    from aid.execution.by_hand import mark, open_changes
    from aid.notify import list_link

    out = tmp_path / "outbox"
    d = loop_deps(tmp_path, madeup_out, clock=AFTER_DIGEST_TIME, mailer=FileMailer(out, clock=lambda: AFTER_DIGEST_TIME))
    prepared = d.settings.cases_root / "prepared"
    d = d.model_copy(update={"writer": PreparedWriter(prepared)})                  # where build_deps has the changes prepared
    link = list_link(d.settings)

    def texts() -> list[str]:
        return [message_from_bytes(path.read_bytes(), policy=policy.default).get_content() for path in digests_in(out)]

    shutil.copy(madeup_out / "MU08_conf.pdf", d.settings.watched_folder)          # the price of PO line 1 and the article's price
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    [daily] = texts()
    assert daily.splitlines()[:3] == ["Dagoverzicht 12-11-2026", "", f"In Exact in te voeren: 2 wijzigingen. Open het tabblad In Exact invoeren: {link}"]
    assert f'In Exact invoeren: <a href="{escape(link)}">' in (d.settings.cases_root / "digests" / "2026-11-12.html").read_text(encoding="utf-8")
    per_case = d.model_copy(update={"settings": d.settings.model_copy(update={"digest": "per_case"})})
    run_loop(per_case, tmp_path / "STOP", poll_seconds=0, max_passes=2)           # nothing new: the open changes make no digest
    assert len(texts()) == 1
    [price] = [change for change in open_changes(prepared) if change.kind == "po_line_price"]
    mark(prepared, price.ids, "entered", AFTER_DIGEST_TIME)
    shutil.copy(madeup_out / "MU14_conf.pdf", d.settings.watched_folder)          # a case: the article's price and MU14's date are open
    run_loop(per_case, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    [newest] = [text for text in texts() if "250114" in text]
    assert newest.splitlines()[2] == f"In Exact in te voeren: 2 wijzigingen. Open het tabblad In Exact invoeren: {link}"
    for change in open_changes(prepared):
        mark(prepared, change.ids, "dropped", AFTER_DIGEST_TIME)
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)          # nothing open any more: a digest without the line
    run_loop(per_case, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    [last] = [text for text in texts() if "250101" in text]
    assert "In Exact" not in last and "tab=invoeren" not in last and len(texts()) == 3
    assert not (d.settings.cases_root / "runner.log").exists()


def test_a_list_that_cannot_be_read_is_said_in_the_digest(tmp_path, madeup_out):
    """When the list cannot be read at all (here its file is a folder), the digest says so in its line, with the link to the tab, and
    the log names the class of the error (review round, Z4). In the per-case mode that line alone makes no digest."""
    from aid.notify import list_link

    d = loop_deps(tmp_path, madeup_out, clock=AFTER_DIGEST_TIME)
    (d.settings.cases_root / "prepared" / "prepared_changes.csv").mkdir(parents=True)
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    text = (d.settings.cases_root / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")
    assert text.splitlines()[2] == ("De lijst In Exact invoeren kon niet worden gelezen. Open het tabblad om te zien wat er nog in te voeren is: "
                                    f"{list_link(d.settings)}") and "PO 250101" in text
    log = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    [line] = [line for line in log if " digest: " in line]
    assert ("IsADirectoryError" in line or "PermissionError" in line) and "the list of changes to enter" in line
    [seen] = [line for line in log if " list of changes: " in line]            # the pass's comparison with the PO store (Task 35)
    assert len(log) == 2 and ("IsADirectoryError" in seen or "PermissionError" in seen)
    per_case = d.model_copy(update={"settings": d.settings.model_copy(update={"digest": "per_case"}), "clock": lambda: datetime(2026, 11, 13, 9, 0)})
    run_loop(per_case, tmp_path / "STOP", poll_seconds=0, max_passes=1)        # nothing to report: no digest for the line alone
    assert sorted(path.name for path in (d.settings.cases_root / "digests").iterdir()) == ["2026-11-12.html", "2026-11-12.txt"]


def test_a_damaged_line_that_the_csv_reader_cannot_parse_is_counted_and_the_digest_goes_out(tmp_path, madeup_out):
    """A zero-filled stretch of prepared_changes.csv, longer than a field of the CSV reader may be, is a line passed over (review round,
    Z3): the next day's digest is made with the line of the changes still open, and the log has no error of the digest."""
    from email import message_from_bytes, policy

    out = tmp_path / "outbox"
    d = loop_deps(tmp_path, madeup_out, clock=AFTER_DIGEST_TIME, mailer=FileMailer(out, clock=lambda: AFTER_DIGEST_TIME))
    prepared = d.settings.cases_root / "prepared"
    d = d.model_copy(update={"writer": PreparedWriter(prepared)})
    shutil.copy(madeup_out / "MU08_conf.pdf", d.settings.watched_folder)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    with open(prepared / "prepared_changes.csv", "ab") as f:
        f.write(b"\x00" * 200_000 + b"\n")
    shutil.copy(madeup_out / "MU14_conf.pdf", d.settings.watched_folder)
    later = d.model_copy(update={"clock": lambda: datetime(2026, 11, 13, 17, 0)})
    run_loop(later, tmp_path / "STOP", poll_seconds=0, max_passes=2)
    texts = [message_from_bytes(path.read_bytes(), policy=policy.default).get_content() for path in digests_in(out)]
    [newest] = [text for text in texts if "13-11-2026" in text]
    assert newest.splitlines()[2].startswith("In Exact in te voeren: 3 wijzigingen.") and "250114" in newest
    assert not (d.settings.cases_root / "runner.log").exists()


# --- a confirmation that came before its purchase order is checked again (Task 36) ---

from email.message import EmailMessage  # noqa: E402

from aid.case import Case, load_case  # noqa: E402
from aid.madeup.generate import confirmation_pages, export_csv, load_specs, pdf_bytes, po_pages  # noqa: E402
from aid.messaging import SentRecord, record_sent  # noqa: E402
from aid.pipeline import CaseResult, waits_for_po  # noqa: E402
from aid.po_store import PoStore  # noqa: E402
from aid.review.app import decide  # noqa: E402
from aid.review.close import cases_waiting_for_po, finish_replaced, waiting_for_po  # noqa: E402
from aid.runner import _Loop, _pass, _scan  # noqa: E402
from aid.sources.mailbox import FOUND_AMONG_NUMBERS, FOUND_BY_LINES, FOUND_HELD_NUMBER, scan_folder  # noqa: E402
from aid.pipeline import CUT_OFF, GUESSED_PO  # noqa: E402
from aid.routing import NO_VERSION  # noqa: E402
from aid.tests.helpers import EXPORT_COLUMNS  # noqa: E402

NINE = datetime(2026, 11, 12, 9, 0)
SPECS = {spec.case_id: spec for spec in load_specs(REPO_ROOT / "aid" / "madeup" / "spec")}
WEBSHOP = "Webwinkelbestelling WS-77812"            # a reference row that names no PO number of the buyer, as a webshop's


def waiting_deps(tmp_path, madeup_out, now: list):
    """loop_deps with an empty PO store and the clock at now[0], which the test moves on."""
    d = loop_deps(tmp_path, madeup_out)
    return d.model_copy(update={"store": PoStore(tmp_path / "empty_store", d.settings.authorization_limit), "clock": lambda: now[0]})


def result_of(case_root) -> CaseResult:
    return CaseResult.model_validate_json((case_root / "steps" / "08_result.json").read_text(encoding="utf-8"))


def cases_of(d, prefix: str) -> list:
    return sorted(path for path in d.settings.cases_root.glob(f"{prefix}_*") if (path / "case.json").exists())


def reported(d) -> list[str]:
    return json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))["reported"]


def referenced(path, case_id: str = "MU01", reference: str | None = "Uw referentie 250101"):
    """The made-up confirmation of case_id with the text of its PO reference row replaced, a number under another word for one; with
    reference None, without that row."""
    pages = confirmation_pages(SPECS[case_id])
    if reference is None:
        pages[0][:] = [row for row in pages[0] if row.ref != "po_reference"]
    else:
        next(row for row in pages[0] if row.ref == "po_reference").texts[0].text = reference
    path.write_bytes(pdf_bytes(pages, path.name))
    return path


def found_by(case_root) -> str | None:
    """How the case's PO was found when not by the one number of the pattern (ruling N18): case.json's po_found_by, else None."""
    return json.loads((case_root / "case.json").read_text(encoding="utf-8")).get("po_found_by")


def replaced_by(case_root) -> str | None:
    """The id of the case that replaced this one, from the record of its replacement in steps/09_close.json; None without one."""
    path = case_root / "steps" / "09_close.json"
    records = json.loads(path.read_text(encoding="utf-8"))["records"] if path.exists() else []
    return next((record["by"] for record in records if record["action"] == "replaced"), None)


def scanned_pos(d, documents: list, folder) -> dict:
    """The PO number the scan gives each document now, by its file name, against a copy of the store: where the check again must link
    a case without a number (ruling N2)."""
    if d.store.root.exists():
        shutil.copytree(d.store.root, folder / "store")
    inbox = folder / "inbox"
    inbox.mkdir(parents=True)
    for document in documents:
        shutil.copy(document, inbox)
    result = scan_folder(inbox, PoStore(folder / "store"), folder / "cases", d.settings, NINE)
    return {load_case(folder / "cases", case_id).input_files[0].name: load_case(folder / "cases", case_id).po_number
            for case_id in result.new_cases}


def linked(d) -> dict:
    """The PO number of the case that lives on for each input file, the cases that were not replaced, by the name of the file."""
    return {load_case(d.settings.cases_root, path.name).input_files[0].name: load_case(d.settings.cases_root, path.name).po_number
            for path in sorted(d.settings.cases_root.iterdir()) if (path / "case.json").exists() and replaced_by(path) is None}


def po_of(path, case_id: str, number: str):
    """The first version of the made-up PO of case_id drawn as a PO of another number, with the same lines."""
    spec = SPECS[case_id]
    pages = po_pages(spec, spec.po.versions[0])
    row = next(row for row in pages[0] if row.ref == "po_reference")
    row.texts[0].text = row.texts[0].text.replace(spec.po.po_number, number)
    path.write_bytes(pdf_bytes(pages, path.name))
    return path


def test_a_confirmation_before_its_po_is_checked_again_as_a_new_case_in_the_pass_that_files_the_po(tmp_path, madeup_out):
    """Rules 5, 6 and 7: a confirmation whose PO the store does not hold yet runs and waits in review for its PO, also through a pass
    that files nothing. The pass that files the PO opens a new case from the same input file, at the pass's time, and runs it in the
    same pass like any case; the old case is finished as replaced by it."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    loop = _Loop(d)
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    _pass(loop)
    [first] = cases_of(d, "250101")
    result = result_of(first)
    assert (result.status, result.version.reason, waits_for_po(result)) == ("review", "no stored version", "po")
    assert [(case.case_id, waits) for case, waits in cases_waiting_for_po(d.settings.cases_root)] == [(first.name, "po")]
    now[0] = datetime(2026, 11, 12, 9, 1)
    _pass(loop)                                                                           # a pass that files nothing changes nothing
    assert cases_of(d, "250101") == [first] and result_of(first) == result
    shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder)
    now[0] = datetime(2026, 11, 12, 9, 2)
    _pass(loop)
    [_, second] = cases_of(d, "250101")
    assert second.name == "250101_20261112T090200"                                      # the pass's time
    new = result_of(second)
    assert (new.status, new.version.version, new.supplier) == ("closed", 1, "S01") and (second / "dispositions.csv").exists()
    assert json.loads((second / "case.json").read_text(encoding="utf-8"))["files"] == json.loads((first / "case.json").read_text(encoding="utf-8"))["files"]
    old = result_of(first)
    assert (old.status, old.supplier) == ("closed", None) and old.review_items == result.review_items
    assert json.loads((first / "steps" / "09_close.json").read_text(encoding="utf-8")) == {
        "records": [{"action": "replaced", "at": "2026-11-12T09:02:00", "by": second.name}]}
    assert cases_waiting_for_po(d.settings.cases_root) == [] and not (d.settings.cases_root / "runner.log").exists()


def test_the_old_case_is_replaced_and_not_reported_again(tmp_path, madeup_out):
    """Rule 7: the old case leaves the digest's list of cases to report, and the digest names the new case only; its result is closed
    with no supplier and the digest line to match, and its record of the replacement is in step 09 and in the audit record. A second
    confirmation of the same PO opened in the same pass gets the next free second."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    loop = _Loop(d)
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    _pass(loop)
    [first] = cases_of(d, "250101")
    assert reported(d) == [first.name]
    now[0] = AFTER_DIGEST_TIME
    shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder)
    shutil.copy(referenced(tmp_path / "again.pdf", reference="Uw inkooporder 250101, opnieuw"), d.settings.watched_folder)
    _pass(loop)
    [_, scanned, second] = cases_of(d, "250101")
    assert (scanned.name, second.name) == ("250101_20261112T170000", "250101_20261112T170001")
    old = result_of(first)
    assert (old.status, old.supplier, old.digest_line) == ("closed", None, "PO 250101: afgesloten, geen schrijfacties")
    audit = json.loads((first / "audit.json").read_text(encoding="utf-8"))
    assert audit["close"] == [{"action": "replaced", "at": AFTER_DIGEST_TIME.isoformat(), "by": second.name}]
    text = (d.settings.cases_root / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")
    assert text.count("PO 250101 (S01): afgesloten") == 2 and "PO 250101: afgesloten" not in text and "ter beoordeling" not in text
    assert reported(d) == [] and json.loads((d.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))["last_sent"]


def test_a_case_that_quoted_a_version_waits_for_that_version(tmp_path, madeup_out):
    """Rules 5 and 6: MU03's confirmation quotes version 1 of PO 250103 while the store holds version 2 only, so it waits for version
    1. Filing version 2 again, or another PO, leaves it waiting; the pass that files version 1 checks it again, and the new case
    selects version 1."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    d.store.add(madeup_out / "MU03_po_v2.pdf")
    loop = _Loop(d)
    shutil.copy(madeup_out / "MU03_conf.pdf", d.settings.watched_folder)
    _pass(loop)
    [first] = cases_of(d, "250103")
    assert waits_for_po(result_of(first)) == "version 1" and result_of(first).version.reason == "quotes version 1, which is not stored"
    result = result_of(first)                                                     # what waits_for_po takes from a result, and what not
    for status in ("closed", "waiting", "failed"):
        assert waits_for_po(result.model_copy(update={"status": status})) is None, status
    for reason, waits in (("quotes version 02, which is not stored", "version 2"), ("quotes version 2a, which is not stored", None),
                          ("no stored version", "po"), ("cannot select", None)):
        assert waits_for_po(result.model_copy(update={"version": result.version.model_copy(update={"reason": reason})})) == waits, reason
    assert waits_for_po(result.model_copy(update={"version": None})) is None
    for minute, pdf in ((1, "MU03_po_v2.pdf"), (2, "MU01_po_v1.pdf")):
        now[0] = datetime(2026, 11, 12, 9, minute)
        shutil.copy(madeup_out / pdf, d.settings.watched_folder / f"{minute}_{pdf}")
        _pass(loop)
        assert cases_of(d, "250103") == [first] and result_of(first).status == "review", pdf
    now[0] = datetime(2026, 11, 12, 9, 3)
    shutil.copy(madeup_out / "MU03_po_v1.pdf", d.settings.watched_folder)
    _pass(loop)
    [_, second] = cases_of(d, "250103")
    assert (result_of(second).version.version, result_of(second).version.reason) == (1, "quotes version 1")
    assert result_of(first).status == "closed" and first.joinpath("steps", "09_close.json").exists()


def test_a_decided_or_written_case_is_left_alone(tmp_path, madeup_out):
    """Rules 5 and 9: a case on which the buyer decided, a case with a record of a close, a case that wrote or sent anything, a case of
    the capture server and a cart are never checked again; only the case on which nothing was done is. It is opened last of the cases
    of its document, so that it has no successor (ruling N1) and a new case is opened for it."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    root = d.settings.cases_root

    def waiting(minute: int, kind: str = "confirmation", document: str = "MU01_conf.pdf"):
        case = open_case(root, kind, [madeup_out / document], "250101", datetime(2026, 11, 12, 8, minute))
        run_case(case, d)
        return case

    decided, closed, written, sent, captured, plain = (waiting(minute) for minute in range(6))
    cart = waiting(6, "cart", "CART01.html")
    decide(decided, "RI1", "unresolved")
    closed.write_step(9, "close", {"records": [{"action": "close", "at": NINE.isoformat(), "decisions": {}, "carried_out": {}}]})
    other = open_case(tmp_path / "elsewhere", "confirmation", [madeup_out / "MU01_conf.pdf"], "250101", NINE)     # closed, with two writes
    entries = run_case(other, deps(tmp_path / "full", madeup_out)).write_entries
    (written.root / "write_log.jsonl").write_text(entries[0].model_dump_json() + "\n", encoding="utf-8")
    record_sent(sent, SentRecord(message_id="cd" * 16, to=["s01@example.com"], subject="s", sent_at=NINE.isoformat(), template="missing_info"))
    (captured.root / "capture.json").write_text("{}", encoding="utf-8")
    assert [case.case_id for case, _ in cases_waiting_for_po(root)] == [plain.case_id]
    assert waits_for_po(result_of(cart.root)) == "po" and waits_for_po(result_of(decided.root)) == "po"     # waiting by their results alone
    shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder)
    _pass(_Loop(d))
    for case in (decided, closed, written, sent, captured, cart):
        assert result_of(case.root).status == "review", case.case_id
    assert result_of(plain.root).status == "closed" and len(cases_of(d, "250101")) == 8


def test_a_case_without_a_number_is_linked_by_its_page_and_a_case_with_one_keeps_it(tmp_path, madeup_out):
    """Rulings N2 and N3: a case opened without a PO number (a.pdf names its PO under "Uw referentie", with the item codes) waits for
    nothing and is not counted; the pass that files its PO links it, as held_numbers finds that PO on its first page. A case with a
    number of its own (the pattern finds 250188 in the subject of b.eml) waits for that number and is counted, and the check again
    never links it to another number, though its page names PO 250101 with its item codes. A case without a number whose page shows
    the PO's number without one of its item codes (c.pdf, MU02's lines) is linked to nothing, as the scan would link it to nothing,
    and stays in review for the buyer."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    root, inbox = d.settings.cases_root, d.settings.watched_folder
    shutil.copy(referenced(tmp_path / "a.pdf"), inbox)                                           # no number the pattern finds
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = "sales@s01.example.com", "aid@example.com", "Bevestiging order 250188"
    message.add_attachment(referenced(tmp_path / "b.pdf").read_bytes(),
                           maintype="application", subtype="pdf", filename="b.pdf")
    (inbox / "b.eml").write_bytes(message.as_bytes())                                           # the pattern finds 250188 in the subject
    shutil.copy(referenced(tmp_path / "c.pdf", "MU02", "Your reference 250101"), inbox)          # the number without an item code of its PO
    loop = _Loop(d)
    _pass(loop)
    assert sorted(path.name for path in root.iterdir() if (path / "case.json").exists()) == [
        "250188_20261112T090000", "unknown_20261112T090000", "unknown_20261112T090001"]
    assert [(case.case_id, waits) for case, waits in cases_waiting_for_po(root)] == [("250188_20261112T090000", "po")]
    assert all(waits_for_po(result_of(root / name)) == "po" for name in ("unknown_20261112T090000", "unknown_20261112T090001"))
    now[0] = datetime(2026, 11, 12, 9, 5)
    shutil.copy(madeup_out / "MU01_po_v1.pdf", inbox)
    _pass(loop)
    [renewed] = cases_of(d, "250101")
    assert renewed.name == "250101_20261112T090500" and load_case(root, renewed.name).input_files[0].name == "a.pdf"
    assert result_of(renewed).version.version == 1 and replaced_by(root / "unknown_20261112T090000") == renewed.name
    assert [(case.case_id, waits) for case, waits in cases_waiting_for_po(root)] == [("250188_20261112T090000", "po")]
    for name in ("250188_20261112T090000", "unknown_20261112T090001"):
        assert result_of(root / name).status == "review" and replaced_by(root / name) is None, name
    assert scanned_pos(d, [tmp_path / "a.pdf", tmp_path / "c.pdf"], tmp_path / "scan") == {"a.pdf": "250101", "c.pdf": None}


def test_the_first_pass_looks_at_every_po_of_the_store(tmp_path, madeup_out):
    """Rule 6: at the first pass of a run of the loop every PO of the store counts as filed, so a case whose PO was filed while the
    loop did not run (here put into the store directly) is checked again then. A later pass of the same run looks only at the POs it
    files."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    loop = _Loop(d)
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    _pass(loop)
    [first] = cases_of(d, "250101")
    d.store.add(madeup_out / "MU01_po_v1.pdf")
    now[0] = datetime(2026, 11, 12, 9, 1)
    _pass(loop)
    assert cases_of(d, "250101") == [first] and result_of(first).status == "review"
    now[0] = datetime(2026, 11, 12, 9, 2)
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1) == 1                  # the runner started again
    [_, second] = cases_of(d, "250101")
    assert second.name == "250101_20261112T090200" and result_of(first).status == "closed" and result_of(second).status == "closed"


def test_a_check_again_cut_off_before_the_old_case_was_finished_opens_no_second_case(tmp_path, madeup_out, monkeypatch):
    """Rule 7: when the runner stopped after the new case was opened and before the old one was finished as replaced, the next pass
    finishes the old case as replaced by that new case and opens no other; the failure itself was logged with the old case's id."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    loop = _Loop(d)
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    _pass(loop)
    [first] = cases_of(d, "250101")

    def cut_off(case, by, now, language):
        raise OSError("the process ended here")
    monkeypatch.setattr(aid.runner, "finish_replaced", cut_off)
    shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder)
    now[0] = datetime(2026, 11, 12, 9, 1)
    _pass(loop)
    [_, second] = cases_of(d, "250101")
    assert result_of(first).status == "review" and result_of(second).status == "closed"
    [line] = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert first.name in line and "OSError: the process ended here" in line
    monkeypatch.undo()
    now[0] = datetime(2026, 11, 12, 9, 2)
    _pass(_Loop(d))
    assert cases_of(d, "250101") == [first, second] and result_of(first).status == "closed"
    assert json.loads((first / "steps" / "09_close.json").read_text(encoding="utf-8"))["records"][0]["by"] == second.name


def test_a_po_whose_look_an_export_filed_in_the_pass_checks_its_waiting_case_again(tmp_path, madeup_out):
    """Amendment 1: the POs whose look an export filed in the pass, new or changed, count as filed, and a confirmation of a PO known from
    an export only is compared with that look, so it no longer waits. The same export given again files nothing, so it checks nothing
    again: the scan of a pass gives the number of each PO whose look an export filed as new or changed, and of each PO PDF it filed,
    also one the store held already, and nothing for an export that filed nothing."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"export_columns": EXPORT_COLUMNS})})
    loop = _Loop(d)
    shutil.copy(madeup_out / "MU02_conf.pdf", d.settings.watched_folder)
    _pass(loop)
    [first] = cases_of(d, "250102")
    assert waits_for_po(result_of(first)) == "po"
    now[0] = datetime(2026, 11, 12, 9, 1)
    (d.settings.watched_folder / "orderregels.csv").write_text(export_csv(SPECS["MU02"]), encoding="utf-8")
    _pass(loop)
    [_, second] = cases_of(d, "250102")
    [look] = json.loads((second / "steps" / "01_po_versions.json").read_text(encoding="utf-8"))["versions"]
    assert look["document"]["reader"] == "export:orderregels.csv" and result_of(second).version.version == 1
    assert result_of(first).status == "closed" and cases_waiting_for_po(d.settings.cases_root) == []
    state, inbox = {"last_sent": None, "reported": [], "skipped": []}, d.settings.watched_folder
    for name, text, filed in (("again.csv", export_csv(SPECS["MU02"]), []),                          # the same rows: nothing filed
                              ("changed.csv", export_csv(SPECS["MU02"]).replace(",2,", ",3,", 1), ["250102"])):
        (inbox / name).write_text(text, encoding="utf-8")
        assert _scan(d, d.settings.cases_root, state, now[0], set()) == filed, name
    shutil.copy(madeup_out / "MU01_po_v1.pdf", inbox)
    assert _scan(d, d.settings.cases_root, state, now[0], set()) == ["250101"]
    shutil.copy(madeup_out / "MU01_po_v1.pdf", inbox / "again.pdf")                                  # a PO PDF the store holds already
    shutil.copy(madeup_out / "MU07_po_v1.pdf", inbox)
    (inbox / "z.csv").write_text(export_csv(SPECS["MU02"]).replace(",2,", ",4,", 1), encoding="utf-8")
    assert _scan(d, d.settings.cases_root, state, now[0], set()) == ["250107", "250101", "250102"]      # the PDFs, then the exports


# --- the review round of Task 36: a replacement found by its input files and its time, the cases without a number, a number kept ---

def test_a_confirmation_that_waits_a_second_time_is_checked_again_until_it_is_compared(tmp_path, madeup_out):
    """Ruling N1 and the reviewers' chain_loss.py: MU03's confirmation quotes version 1 of PO 250103. It waits for the PO; version 2 is
    filed a pass later and checks it again, and the new case waits for version 1; version 1 is filed a pass after that and checks that
    case again. In the end a case is compared with version 1 and no case is lost: each case before it is finished as replaced by the
    next one, and only the last is reported."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    loop = _Loop(d)
    shutil.copy(madeup_out / "MU03_conf.pdf", d.settings.watched_folder)
    _pass(loop)
    for minute, pdf in ((1, "MU03_po_v2.pdf"), (2, "MU03_po_v1.pdf")):
        now[0] = datetime(2026, 11, 12, 9, minute)
        shutil.copy(madeup_out / pdf, d.settings.watched_folder)
        _pass(loop)
    chain(d)


def chain(d) -> None:
    """The end of MU03's chain: three cases, each replaced by the next, the last compared with version 1; nothing waits, nothing went
    to the log, and only the last case is reported."""
    first, second, third = cases_of(d, "250103")
    assert [result_of(case).version.reason for case in (first, second)] == ["no stored version", "quotes version 1, which is not stored"]
    assert (replaced_by(first), replaced_by(second), replaced_by(third)) == (second.name, third.name, None)
    assert (result_of(third).version.version, result_of(third).version.reason) == (1, "quotes version 1")
    assert cases_waiting_for_po(d.settings.cases_root) == [] and reported(d) == [third.name]
    assert not (d.settings.cases_root / "runner.log").exists()


def test_the_chain_through_restarts(tmp_path, madeup_out):
    """Ruling N1 and the reviewers' chain_loss_restart.py: the chain of the test above, with the two versions put into the store while
    the runner does not run, and three starts of the runner, whose first pass looks at every PO of the store."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    shutil.copy(madeup_out / "MU03_conf.pdf", d.settings.watched_folder)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    for minute, pdf in ((1, "MU03_po_v2.pdf"), (2, "MU03_po_v1.pdf")):
        d.store.add(madeup_out / pdf)
        now[0] = datetime(2026, 11, 12, 9, minute)
        run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    chain(d)


def test_a_check_again_cut_off_after_its_new_case_waits_again_opens_no_second_case(tmp_path, madeup_out, monkeypatch):
    """Ruling N1: the runner stopped after it opened the new case and before it finished the old one, and the new case waits again,
    for the version its confirmation quotes. The next start finishes the old case as replaced by that new case, opens no other and
    counts one waiting case; the version filed later checks the new case again."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    root, loop = d.settings.cases_root, _Loop(d)
    shutil.copy(madeup_out / "MU03_conf.pdf", d.settings.watched_folder)
    _pass(loop)
    [first] = cases_of(d, "250103")

    def cut_off(case, by, now, language):
        raise OSError("the process ended here")
    monkeypatch.setattr(aid.runner, "finish_replaced", cut_off)
    shutil.copy(madeup_out / "MU03_po_v2.pdf", d.settings.watched_folder)
    now[0] = datetime(2026, 11, 12, 9, 1)
    _pass(loop)
    [_, second] = cases_of(d, "250103")
    assert [(case.case_id, waits) for case, waits in cases_waiting_for_po(root)] == [(first.name, "po"), (second.name, "version 1")]
    monkeypatch.undo()
    now[0] = datetime(2026, 11, 12, 9, 2)
    loop = _Loop(d)                                                                       # the runner started again
    _pass(loop)
    assert cases_of(d, "250103") == [first, second] and replaced_by(first) == second.name
    assert [(case.case_id, waits) for case, waits in cases_waiting_for_po(root)] == [(second.name, "version 1")]
    shutil.copy(madeup_out / "MU03_po_v1.pdf", d.settings.watched_folder)
    now[0] = datetime(2026, 11, 12, 9, 3)
    _pass(loop)
    [_, _, third] = cases_of(d, "250103")
    assert replaced_by(second) == third.name and result_of(third).version.version == 1 and cases_waiting_for_po(root) == []


def test_the_successor_is_the_newest_live_case_of_the_same_document(tmp_path, madeup_out):
    """Ruling N1: the live successor of a waiting case is the newest case opened after it from the same input files and of the same
    kind that is not itself finished as replaced, whatever its PO number and whether or not it waits. Besides the waiting case, the
    same confirmation was opened by hand before it, and after it: decided on, replaced, under another PO number and not run yet,
    replaced again (the newest of them), and as a cart; another confirmation was opened after all of them. The pass that files the
    waiting case's PO finishes it as replaced by the case of the other PO number, and opens no case."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    root, document = d.settings.cases_root, madeup_out / "MU01_conf.pdf"

    def opened(po_number, minute, kind="confirmation", files=(document,), ran=True):
        case = open_case(root, kind, list(files), po_number, datetime(2026, 11, 12, 8, minute))
        if ran:
            run_case(case, d)
        return case
    before, waiting, decided, first_replaced = opened("250101", 0), opened("250101", 10), opened("250101", 11), opened(None, 12)
    newest_live, last_replaced = opened("250199", 13, ran=False), opened("250101", 14)
    opened("250101", 15, kind="cart")
    opened("250102", 16, files=(madeup_out / "MU02_conf.pdf",))
    for case in (before, decided):
        decide(case, "RI1", "unresolved")
    for case in (first_replaced, last_replaced):
        finish_replaced(case, "250101_20261112T085900", datetime(2026, 11, 12, 8, 59), "nl")
    shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder)
    _pass(_Loop(d))
    assert replaced_by(waiting.root) == newest_live.case_id and not list(root.glob("*_20261112T0900*"))
    assert [replaced_by(case.root) for case in (before, decided, newest_live)] == [None, None, None]
    assert [(case.case_id, waits) for case, waits in cases_waiting_for_po(root)] == [
        ("250102_20261112T081600", "po"), (newest_live.case_id, "po")]


def test_a_check_again_cut_off_for_a_case_without_a_number_opens_no_second_case(tmp_path, madeup_out, monkeypatch):
    """Rulings N1 and N2: a case opened without a PO number (MU01's confirmation names its PO under "Uw referentie") is linked when its
    PO is filed; the runner stopped after it opened the new case of PO 250101 and before it finished the old case. The next start
    finishes the old case as replaced by that case, whose PO number is not the old case's, and opens no other."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    shutil.copy(referenced(tmp_path / "a.pdf"), d.settings.watched_folder)
    _pass(_Loop(d))
    [first] = cases_of(d, "unknown")

    def cut_off(case, by, now, language):
        raise OSError("the process ended here")
    monkeypatch.setattr(aid.runner, "finish_replaced", cut_off)
    shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder)
    now[0] = datetime(2026, 11, 12, 9, 1)
    _pass(_Loop(d))
    [second] = cases_of(d, "250101")
    assert replaced_by(first) is None and result_of(first).status == "review" and result_of(second).version.version == 1
    monkeypatch.undo()
    now[0] = datetime(2026, 11, 12, 9, 2)
    _pass(_Loop(d))
    assert cases_of(d, "250101") == [second] and replaced_by(first) == second.name and result_of(first).status == "closed"


def test_a_case_without_a_number_waits_for_nothing_and_is_linked_when_its_po_is_filed(tmp_path, madeup_out):
    """Ruling N2 and the reviewers' no_number.py: a confirmation that does not print the buyer's PO number (a webshop's, with an order
    number of its own) is opened without a PO number. Its result waits by waits_for_po, but the case waits for nothing: waiting_for_po
    gives None, so the review tab shows no hint and the block does not count it, and it stays in review for the buyer. The pass that
    files its PO links it by the quick read by lines, as the scan links the same document once the PO is held: a new case of PO 250101
    from the same file, and the old case finished as replaced. The link is the assistant's guess, so the new case goes to the buyer
    with the reason of ruling N18, its case.json saying how its PO was found, and nothing is written for it."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    root, loop = d.settings.cases_root, _Loop(d)
    shutil.copy(referenced(tmp_path / "webshop.pdf", reference=WEBSHOP), d.settings.watched_folder)
    _pass(loop)
    [first] = cases_of(d, "unknown")
    assert (result_of(first).status, waits_for_po(result_of(first))) == ("review", "po")
    assert waiting_for_po(load_case(root, first.name)) is None and cases_waiting_for_po(root) == []
    now[0] = datetime(2026, 11, 12, 9, 1)
    shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder)
    _pass(loop)
    [second] = cases_of(d, "250101")
    assert second.name == "250101_20261112T090100" and result_of(second).version.version == 1 and replaced_by(first) == second.name
    assert load_case(root, second.name).input_files[0].name == "webshop.pdf"
    assert scanned_pos(d, [tmp_path / "webshop.pdf"], tmp_path / "scan") == {"webshop.pdf": "250101"}
    result = result_of(second)
    assert (result.status, [item.reason for item in result.review_items], result.write_entries) == ("review", [GUESSED_PO], [])
    assert found_by(second) == FOUND_BY_LINES and found_by(first) is None


def test_the_made_up_confirmations_without_their_reference_are_linked_as_the_scan_links_them(tmp_path, madeup_out):
    """Ruling N2 and the reviewers' noref_flow.py: the 20 made-up confirmations drawn without the row of their PO reference, given while
    the store is empty, are 20 cases without a PO number, and none of them is counted as waiting. The pass that files every made-up PO
    links each of them to the PO the scan gives the same document then (19 of them: MU19's merged line leaves one item code of its PO),
    and a restart links no more."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    root, loop = d.settings.cases_root, _Loop(d)
    drawn = [referenced(tmp_path / f"{case_id}_conf.pdf", case_id, None) for case_id, spec in SPECS.items() if spec.confirmation is not None]
    for pdf in drawn:
        shutil.copy(pdf, d.settings.watched_folder)
    _pass(loop)
    assert len(cases_of(d, "unknown")) == 20 and cases_waiting_for_po(root) == []
    now[0] = datetime(2026, 11, 12, 9, 1)
    for pdf in sorted(madeup_out.glob("MU*_po_v*.pdf")):
        shutil.copy(pdf, d.settings.watched_folder)
    _pass(loop)
    expected = scanned_pos(d, drawn, tmp_path / "scan")
    assert linked(d) == expected and sum(number is not None for number in expected.values()) == 19 and expected["MU19_conf.pdf"] is None
    assert cases_waiting_for_po(root) == []
    now[0] = datetime(2026, 11, 12, 9, 2)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert linked(d) == expected


def test_a_case_whose_number_stood_only_in_its_e_mail_is_linked_by_its_lines(tmp_path, madeup_out):
    """Ruling N2 and the reviewers' email_only.py: the e-mail names the PO under a word the pattern does not know ("Bevestiging uw
    referentie 250102"), and its attachment, MU02's confirmation without the row of its PO reference, names none. The case is opened
    without a PO number and waits for nothing. It keeps the attachment only, so the check again reads its first page: the pass that
    files PO 250102 links it by the quick read by lines, as the scan links the same attachment then."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    root, loop = d.settings.cases_root, _Loop(d)
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = "verkoop@s02.example.com", "aid@example.com", "Bevestiging uw referentie 250102"
    message.add_attachment(referenced(tmp_path / "bevestiging.pdf", "MU02", None).read_bytes(), maintype="application", subtype="pdf",
                           filename="bevestiging.pdf")
    (d.settings.watched_folder / "m.eml").write_bytes(message.as_bytes())
    _pass(loop)
    [first] = cases_of(d, "unknown")
    assert waiting_for_po(load_case(root, first.name)) is None and cases_waiting_for_po(root) == []
    now[0] = datetime(2026, 11, 12, 9, 1)
    shutil.copy(madeup_out / "MU02_po_v1.pdf", d.settings.watched_folder)
    _pass(loop)
    [second] = cases_of(d, "250102")
    assert replaced_by(first) == second.name and result_of(second).version.version == 1
    assert scanned_pos(d, [tmp_path / "bevestiging.pdf"], tmp_path / "scan") == {"bevestiging.pdf": "250102"}


def test_a_case_that_names_two_held_pos_stays_with_the_buyer(tmp_path, madeup_out):
    """Ruling N2 and the reviewers' two_held.py: the confirmation names two POs the store holds, 250101 and a repeat order 250199 of the
    same articles, under a word the pattern does not know. The scan cannot choose and opens the case without a PO number: it waits for
    nothing, so no hint and no count, and it stays in review for the buyer. Neither the PO given again nor a restart links it, as the
    scan would not link the same document either."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    root = d.settings.cases_root
    d.store.add(madeup_out / "MU01_po_v1.pdf")
    (tmp_path / "repeat.csv").write_text(export_csv(SPECS["MU01"]).replace("250101", "250199"), encoding="utf-8")
    d.store.from_export(tmp_path / "repeat.csv", EXPORT_COLUMNS)
    shutil.copy(referenced(tmp_path / "two.pdf", reference="Uw referenties: 250101, 250199"), d.settings.watched_folder)
    loop = _Loop(d)
    _pass(loop)
    [first] = cases_of(d, "unknown")
    assert waiting_for_po(load_case(root, first.name)) is None and cases_waiting_for_po(root) == []
    now[0] = datetime(2026, 11, 12, 9, 1)
    shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder)
    _pass(loop)
    now[0] = datetime(2026, 11, 12, 9, 2)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert cases_of(d, "250101") == [] and cases_of(d, "250199") == [] and result_of(first).status == "review"
    assert replaced_by(first) is None and scanned_pos(d, [tmp_path / "two.pdf"], tmp_path / "scan") == {"two.pdf": None}


def test_a_case_without_a_number_is_linked_only_to_a_po_filed_in_the_pass_except_at_the_first_pass(tmp_path, madeup_out):
    """Ruling N2 (the reviewers' surviving mutation R5): a case without a number is linked only to a PO filed in that pass, by the
    number on its first page and by the quick read by lines alike; at the first pass of a run any PO of the store counts as filed. Here
    PO 250101 comes into the store outside a pass: a later pass that files PO 250102 links neither case, and the next start links both."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    loop = _Loop(d)
    shutil.copy(referenced(tmp_path / "a.pdf"), d.settings.watched_folder)                     # its number on the page, with its item codes
    shutil.copy(referenced(tmp_path / "b.pdf", reference=WEBSHOP), d.settings.watched_folder)   # no number: its lines only
    _pass(loop)
    first = cases_of(d, "unknown")
    assert len(first) == 2
    d.store.add(madeup_out / "MU01_po_v1.pdf")
    now[0] = datetime(2026, 11, 12, 9, 1)
    shutil.copy(madeup_out / "MU02_po_v1.pdf", d.settings.watched_folder)
    _pass(loop)
    assert cases_of(d, "250101") == [] and all(replaced_by(case) is None for case in first)
    now[0] = datetime(2026, 11, 12, 9, 2)
    _pass(_Loop(d))                                                                       # the runner started again
    assert [path.name for path in cases_of(d, "250101")] == ["250101_20261112T090200", "250101_20261112T090201"]
    assert linked(d) == {"a.pdf": "250101", "b.pdf": "250101"}
    assert [found_by(path) for path in cases_of(d, "250101")] == [FOUND_HELD_NUMBER, FOUND_BY_LINES]          # ruling N18


def test_a_case_keeps_its_own_number(tmp_path, madeup_out):
    """Ruling N3 and the reviewers' sweep_relink.py: the confirmation names its own new order by the pattern ("Uw inkooporder 250121")
    and an earlier order of the same articles as a bare number ("vervangt 250101"), which the store holds with those item codes. The case
    of 250121 waits for 250121: the check again never links it to another number, not at the first pass, not at a later pass and not at
    a restart. The pass that files PO 250121 checks it again."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    root = d.settings.cases_root
    d.store.add(madeup_out / "MU01_po_v1.pdf")
    shutil.copy(referenced(tmp_path / "new.pdf", reference="Uw inkooporder 250121 (vervangt 250101)"), d.settings.watched_folder)
    loop = _Loop(d)
    _pass(loop)
    [first] = cases_of(d, "250121")
    now[0] = datetime(2026, 11, 12, 9, 1)
    _pass(loop)
    now[0] = datetime(2026, 11, 13, 8, 0)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)                              # the next morning's start
    assert [(case.case_id, waits) for case, waits in cases_waiting_for_po(root)] == [(first.name, "po")] and cases_of(d, "250101") == []
    now[0] = datetime(2026, 11, 13, 8, 1)
    shutil.copy(po_of(tmp_path / "po250121.pdf", "MU01", "250121"), d.settings.watched_folder)
    _pass(_Loop(d))
    [_, second] = cases_of(d, "250121")
    assert replaced_by(first) == second.name and result_of(second).version.version == 1 and cases_waiting_for_po(root) == []
    assert cases_of(d, "250101") == []


def test_a_replaced_case_whose_dispositions_were_never_recorded_is_not_reported(tmp_path, madeup_out):
    """Ruling N6 and the reviewers' recover_report.py: the process ended between run_case and record_dispositions of a waiting case. The
    pass that files its PO replaces it, then records its dispositions as for any case and never reports it: the digest names the new
    case only."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    case = open_case(d.settings.cases_root, "confirmation", [madeup_out / "MU01_conf.pdf"], "250101", NINE)
    run_case(case, d)
    now[0] = AFTER_DIGEST_TIME
    shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder)
    _pass(_Loop(d))
    [first, second] = cases_of(d, "250101")
    assert replaced_by(first) == second.name and (first / "dispositions.csv").exists()
    text = (d.settings.cases_root / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")
    assert "PO 250101: afgesloten" not in text and text.count("PO 250101 (S01): afgesloten") == 1


def test_a_replaced_case_whose_record_could_not_be_added_leaves_the_digest_all_the_same(tmp_path, madeup_out):
    """Ruling N6 and the reviewers' half_finished.py: the audit record of the waiting case cannot be read, so finish_replaced raises
    after it closed the case's result and wrote its record in step 09. The case is closed as replaced and is not reported again, so
    the digest names the new case only; the failure is logged."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    loop = _Loop(d)
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    _pass(loop)
    [first] = cases_of(d, "250101")
    assert reported(d) == [first.name]
    (first / "audit.json").write_text("{damaged", encoding="utf-8")
    now[0] = AFTER_DIGEST_TIME
    shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder)
    _pass(loop)
    [_, second] = cases_of(d, "250101")
    assert result_of(first).status == "closed" and replaced_by(first) == second.name
    text = (d.settings.cases_root / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")
    assert "PO 250101: afgesloten" not in text and text.count("PO 250101 (S01): afgesloten") == 1
    [line] = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert line.startswith(f"{AFTER_DIGEST_TIME.isoformat()} recheck {first.name}: JSONDecodeError: ")


def test_a_case_whose_check_again_fails_is_logged_once_in_a_run_of_the_loop(tmp_path, madeup_out, monkeypatch):
    """Rulings N13 and N19: a case whose check again fails is logged once in a run of the loop, as the runner logs its other repeated
    failures, once more after a restart, and once more when it later succeeds. A pass that files another PO (the checkers' log_noise.py)
    does not check the case again, so it neither logs a recovery nor lets the next failure be logged again: the check again counts as
    run only when it reached the case's link. The new case opened by the first try is the one the old case is finished by."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    loop = _Loop(d)
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    _pass(loop)
    [first] = cases_of(d, "250101")

    def locked(case, by, now, language):
        raise OSError("locked")
    monkeypatch.setattr(aid.runner, "finish_replaced", locked)
    for minute, pdf in ((1, "MU01_po_v1.pdf"), (2, "MU02_po_v1.pdf"), (3, "MU01_po_v1.pdf"), (4, "MU07_po_v1.pdf"), (5, "MU01_po_v1.pdf")):
        now[0] = datetime(2026, 11, 12, 9, minute)
        shutil.copy(madeup_out / pdf, d.settings.watched_folder / f"{minute}.pdf")
        _pass(loop)
    now[0] = datetime(2026, 11, 12, 9, 6)
    loop = _Loop(d)                                                                       # the runner started again
    _pass(loop)
    monkeypatch.undo()
    now[0] = datetime(2026, 11, 12, 9, 7)
    shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder / "7.pdf")
    _pass(loop)
    [_, second] = cases_of(d, "250101")
    assert second.name == "250101_20261112T090100" and replaced_by(first) == second.name
    assert (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines() == [
        f"2026-11-12T09:01:00 recheck {first.name}: OSError: locked", f"2026-11-12T09:06:00 recheck {first.name}: OSError: locked",
        f"2026-11-12T09:07:00 recheck {first.name}: ran after an earlier failure"]


# --- the second review round of Task 36: a guessed link goes to the buyer, the check again is safer and cheaper ---

from aid.readers.supplier_parser import detect_template, load_templates, read_with_template  # noqa: E402
from aid.review.close import close_case  # noqa: E402
from aid.review.exact_settings import pattern_from  # noqa: E402


class ProcessEnded(BaseException):
    """The runner's process ends here (a log-off, a crash, the launcher's second Ctrl-C): nothing after this line runs. A BaseException,
    so that no `except Exception` of the runner catches it."""


def test_a_case_without_a_number_stays_with_the_buyer_once_the_recognition_finds_a_number(tmp_path, madeup_out):
    """Ruling N17 and the checkers' recognition_then_held.py and recognition_then_lines.py: a confirmation opened without a PO number
    before the owner added its word to the number recognition names its own new order under "Uw kenmerk", with and without the earlier
    order of the same articles ("vervangt 250101"). Once the recognition finds a number on its first page, the check again passes over
    the case: the earlier order 250101, filed after the change, links nothing, also at the first pass of a run; the case stays in
    review, unlinked, for the buyer, while the scan of the same document now opens a case of its own number, 250150."""
    words = pattern_from("Uw kenmerk, PO, Inkooporder, Bestelling, Bestellung, Order", "6", "8", "nl")[0]
    for n, reference in enumerate(("Uw kenmerk 250150 (vervangt 250101)", "Uw kenmerk 250150")):
        now = [NINE]
        d = waiting_deps(tmp_path / f"run{n}", madeup_out, now)
        shutil.copy(referenced(tmp_path / f"{n}.pdf", reference=reference), d.settings.watched_folder)
        _pass(_Loop(d))
        [first] = cases_of(d, "unknown")
        d = d.model_copy(update={"settings": d.settings.model_copy(update={"po_number_pattern": words})})
        now[0] = datetime(2026, 11, 12, 10, 0)
        shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder)
        loop = _Loop(d)
        _pass(loop)
        now[0] = datetime(2026, 11, 12, 10, 1)
        shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder / "again.pdf")
        _pass(loop)
        assert cases_of(d, "250101") == [] and replaced_by(first) is None and result_of(first).status == "review", reference
        assert scanned_pos(d, [tmp_path / f"{n}.pdf"], tmp_path / f"scan{n}") == {f"{n}.pdf": "250150"}, reference


def test_a_guessed_link_goes_to_the_buyer(tmp_path, madeup_out):
    """Ruling N18: a confirmation whose PO the assistant found otherwise than by the one number its pattern found is the assistant's
    guess, so it goes to the buyer: like R11, one REVIEW action covers the whole case, with the reason "PO found otherwise than by the
    number recognition", nothing is written or asked, and the case is in review, never closed by itself; its case.json says how its PO
    was found. At the scan: a held number chosen among several pattern numbers (the sequences lens's "Uw inkooporder 250121, vervangt
    inkooporder 250101", with 250101 held: opened for 250101 with the reason, not closed), a held number when the pattern found none
    ("Uw referentie 250101"), and the quick read by lines (a webshop's page). A confirmation with exactly one pattern number has neither
    the reason nor the record. (The link of the check again: test_a_case_without_a_number_waits_for_nothing_and_is_linked_...)"""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    root, inbox = d.settings.cases_root, d.settings.watched_folder
    d.store.add(madeup_out / "MU01_po_v1.pdf")
    for name, reference in (("a.pdf", "Uw inkooporder 250121, vervangt inkooporder 250101"), ("b.pdf", "Uw referentie 250101"),
                            ("c.pdf", WEBSHOP)):
        shutil.copy(referenced(tmp_path / name, reference=reference), inbox)
    shutil.copy(madeup_out / "MU01_conf.pdf", inbox)                                    # "Uw inkooporder 250101": the pattern's one number
    _pass(_Loop(d))
    cases = {load_case(root, path.name).input_files[0].name: path for path in cases_of(d, "250101")}
    assert sorted(cases) == ["MU01_conf.pdf", "a.pdf", "b.pdf", "c.pdf"]
    for name, how in (("a.pdf", FOUND_AMONG_NUMBERS), ("b.pdf", FOUND_HELD_NUMBER), ("c.pdf", FOUND_BY_LINES)):
        result = result_of(cases[name])
        assert (result.status, [item.reason for item in result.review_items], result.write_entries, result.messages) == (
            "review", [GUESSED_PO], [], []), name
        assert found_by(cases[name]) == how and result.version.version == 1, name
    single = result_of(cases["MU01_conf.pdf"])
    assert single.status == "closed" and single.review_items == [] and found_by(cases["MU01_conf.pdf"]) is None
    assert not (tmp_path / "prepared_changes.csv").exists() or "a.pdf" not in (tmp_path / "prepared_changes.csv").read_text(encoding="utf-8")
    assert len([entry for entry in single.write_entries if entry.op.kind == "set_status"]) == 1


def test_a_guessed_case_makes_no_promise_and_its_replacement_stays_a_guess(tmp_path, madeup_out):
    """Ruling N18 with N2: the number recognition knows "PO" and "Bestelling" only, so MU03's confirmation ("Uw inkooporder 250103,
    versie 1") is linked by its held number to PO 250103, whose version 2 alone the store holds, while the document quotes version 1.
    A guessed number is not the confirmation's own: the case is the buyer's, so it shows no hint and is not counted, though its result
    waits for version 1; its review keeps R09's item "no version selected" beside the guess's (ruling N29). Once version 1 is filed
    the runner checks it again all the same, and the new case, compared with version 1, is a guess as well: in review with the
    reason."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"po_number_pattern": pattern_from("PO, Bestelling", "6", "8", "nl")[0]})})
    root = d.settings.cases_root
    d.store.add(madeup_out / "MU03_po_v2.pdf")
    shutil.copy(madeup_out / "MU03_conf.pdf", d.settings.watched_folder)
    loop = _Loop(d)
    _pass(loop)
    [first] = cases_of(d, "250103")
    result = result_of(first)
    assert (found_by(first), result.status, [item.reason for item in result.review_items]) == (FOUND_HELD_NUMBER, "review",
                                                                                               [GUESSED_PO, NO_VERSION])
    assert waits_for_po(result) == "version 1" and waiting_for_po(load_case(root, first.name)) is None and cases_waiting_for_po(root) == []
    now[0] = datetime(2026, 11, 12, 9, 1)
    shutil.copy(madeup_out / "MU03_po_v1.pdf", d.settings.watched_folder)
    _pass(loop)
    [_, second] = cases_of(d, "250103")
    new = result_of(second)
    assert replaced_by(first) == second.name and found_by(second) == FOUND_HELD_NUMBER and new.version.version == 1
    assert (new.status, [item.reason for item in new.review_items][:1], new.write_entries) == ("review", [GUESSED_PO], [])


def test_a_replaced_case_leaves_the_digest_before_it_is_finished(tmp_path, madeup_out, monkeypatch):
    """Ruling N20 and the checkers' replay_n6_window.py and replay_n6_digest.py: the old case is taken out of the cases the next digest
    reports, and the state saved, before finish_replaced rewrites its result. So no digest names the replaced case, whatever step the
    runner's process ends at: while the next waiting case is checked again, inside finish_replaced after the result was written, or
    right after it."""
    import aid.review.close as close
    real_open_free, real_add, real_finish = aid.runner.open_free, close._add_record, aid.runner.finish_replaced

    def ended_opening(cases_root, kind, files, po_number, at, *args, **kwargs):
        if po_number == "250102":
            raise ProcessEnded("while the new case of 250102 was being opened")
        return real_open_free(cases_root, kind, files, po_number, at, *args, **kwargs)

    def ended_recording(case, record):
        if record.get("action") == "replaced":
            raise ProcessEnded("after the result was closed, before the record")
        return real_add(case, record)

    def ended_after(case, by, at, language):
        real_finish(case, by, at, language)
        raise ProcessEnded("right after finish_replaced")
    for where, module, name, fake in (("window", aid.runner, "open_free", ended_opening), ("record", close, "_add_record", ended_recording),
                                      ("after", aid.runner, "finish_replaced", ended_after)):
        now = [NINE]
        d = waiting_deps(tmp_path / where, madeup_out, now)
        for document in ("MU01_conf.pdf", "MU02_conf.pdf"):
            shutil.copy(madeup_out / document, d.settings.watched_folder)
        loop = _Loop(d)
        _pass(loop)
        [first] = cases_of(d, "250101")
        assert first.name in reported(d), where
        monkeypatch.setattr(module, name, fake)
        now[0] = datetime(2026, 11, 12, 9, 1)
        for document in ("MU01_po_v1.pdf", "MU02_po_v1.pdf"):
            shutil.copy(madeup_out / document, d.settings.watched_folder)
        with pytest.raises(ProcessEnded):
            _pass(loop)
        monkeypatch.undo()
        assert result_of(first).status == "closed" and first.name not in reported(d), where
        now[0] = AFTER_DIGEST_TIME
        _pass(_Loop(d))                                                                    # the runner started again; the digest is due
        text = (d.settings.cases_root / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")
        assert "PO 250101: afgesloten" not in text and text.count("PO 250101 (S01): afgesloten") == 1, where


def test_the_check_again_of_a_case_without_a_number_looks_at_the_filed_pos_first(tmp_path, madeup_out, monkeypatch):
    """Ruling N21: the check again of a case without a number costs what one filed PO costs. It first tests only the POs filed in the
    pass, whether one of them can be the case's PO (its number on the first page as held_numbers requires, or two of the case's item
    codes in that PO as it stands); only then does it run the whole-store lookups, the held numbers and the quick read by lines, as the
    scan would. A pass that files an unrelated PO runs neither; the pass that files the case's PO links it as before."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    for pdf in ("MU02_po_v1.pdf", "MU03_po_v1.pdf", "MU07_po_v1.pdf"):
        d.store.add(madeup_out / pdf)
    for n in range(3):
        shutil.copy(referenced(tmp_path / f"{n}.pdf", reference=None if n else WEBSHOP), d.settings.watched_folder)
    loop = _Loop(d)
    _pass(loop)
    first = cases_of(d, "unknown")
    assert len(first) == 3
    calls = {"po_without_number": 0, "find_by_lines": 0}
    real_lookup, real_lines = aid.runner.po_without_number, PoStore.find_by_lines

    def lookup(*args, **kwargs):
        calls["po_without_number"] += 1
        return real_lookup(*args, **kwargs)

    def lines(self, *args, **kwargs):
        calls["find_by_lines"] += 1
        return real_lines(self, *args, **kwargs)
    monkeypatch.setattr(aid.runner, "po_without_number", lookup)
    monkeypatch.setattr(PoStore, "find_by_lines", lines)
    now[0] = datetime(2026, 11, 12, 9, 1)
    shutil.copy(madeup_out / "MU20_po_v1.pdf", d.settings.watched_folder)                # no case's PO
    _pass(loop)
    assert calls == {"po_without_number": 0, "find_by_lines": 0} and all(replaced_by(case) is None for case in first)
    now[0] = datetime(2026, 11, 12, 9, 2)
    shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder)                # the PO of all three
    _pass(loop)
    assert calls["po_without_number"] == 3 and linked(d) == {"0.pdf": "250101", "1.pdf": "250101", "2.pdf": "250101"}


def test_a_case_decided_and_closed_while_the_check_again_runs_is_left_alone(tmp_path, madeup_out, monkeypatch):
    """Ruling N22 and the checkers' replay_race_decision.py: while the runner checks the first waiting case again, the buyer decides on
    the second one on the page and closes it. Just before it opens the new case, and again just before it finishes the old one, the
    check again reads the case's state from its folder, so the decided and closed case keeps its close, gets no record "replaced", and
    no second set of writes is prepared for its PO. A decision saved while the new case is being opened leaves the old case alone and
    removes the new case (ruling N28)."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    root = d.settings.cases_root
    for document in ("MU01_conf.pdf", "MU02_conf.pdf"):
        shutil.copy(madeup_out / document, d.settings.watched_folder)
    loop = _Loop(d)
    _pass(loop)
    [w1], [w2] = cases_of(d, "250101"), cases_of(d, "250102")
    real = aid.runner._checked_for

    def checked_for(case, *args, **kwargs):
        if case.case_id == w1.name and not (w2 / "decisions.jsonl").exists():           # meanwhile, on the page
            decide(load_case(root, w2.name), "RI1", "keep")
            assert close_case(load_case(root, w2.name), d).status == "closed"
        return real(case, *args, **kwargs)
    monkeypatch.setattr(aid.runner, "_checked_for", checked_for)
    now[0] = datetime(2026, 11, 12, 9, 1)
    for document in ("MU01_po_v1.pdf", "MU02_po_v1.pdf"):
        shutil.copy(madeup_out / document, d.settings.watched_folder)
    _pass(loop)
    assert cases_of(d, "250102") == [w2] and replaced_by(w2) is None and result_of(w2).supplier == "S02"
    assert [record["action"] for record in json.loads((w2 / "steps" / "09_close.json").read_text(encoding="utf-8"))["records"]] == ["close"]
    rows = [line for line in (tmp_path / "prepared_changes.csv").read_text(encoding="utf-8").splitlines() if ",250102," in line]
    assert len([line for line in rows if "set_status" in line]) == 1
    assert replaced_by(w1) is not None and result_of(w1).status == "closed"
    # a decision saved while the new case is being opened: the second read finds it, so the old case is left alone and the new case,
    # which has not run, is removed (ruling N28)
    monkeypatch.undo()
    now[0] = datetime(2026, 11, 12, 9, 2)
    shutil.copy(madeup_out / "MU07_conf.pdf", d.settings.watched_folder)
    _pass(loop)
    [w3] = cases_of(d, "250107")
    real_open = aid.runner.open_free

    def opening(cases_root, kind, files, po_number, at, *args, **kwargs):
        opened = real_open(cases_root, kind, files, po_number, at, *args, **kwargs)
        decide(load_case(root, w3.name), "RI1", "unresolved")                         # meanwhile, on the page
        return opened
    monkeypatch.setattr(aid.runner, "open_free", opening)
    now[0] = datetime(2026, 11, 12, 9, 3)
    shutil.copy(madeup_out / "MU07_po_v1.pdf", d.settings.watched_folder)
    _pass(loop)
    assert replaced_by(w3) is None and result_of(w3).status == "review" and cases_of(d, "250107") == [w3]


def test_a_replacement_is_opened_after_the_case_it_replaces(tmp_path, madeup_out, monkeypatch):
    """Ruling N23 and the checkers' replay_n1_same_second.py: three confirmations without a number come in one scan and are opened at
    08:01:00, :01 and :02. The pass at 08:01:01 files the PO of the second; its new case is opened no earlier than one second after the
    old case's time, at 08:01:02, and the process ends before the old case is finished. The runner started again finds that new case as
    the old case's successor: one live case of the confirmation, opened after the case it replaces."""
    now = [datetime(2026, 11, 12, 8, 1, 0)]
    d = waiting_deps(tmp_path, madeup_out, now)
    for n, case_id in enumerate(("MU01", "MU02", "MU07"), 1):
        shutil.copy(referenced(tmp_path / f"{n}.pdf", case_id, None), d.settings.watched_folder)
    _pass(_Loop(d))
    unknown = cases_of(d, "unknown")
    assert [path.name for path in unknown] == [f"unknown_20261112T08010{n}" for n in range(3)]

    def ended(case, by, at, language):
        raise ProcessEnded("before the old case was finished")
    monkeypatch.setattr(aid.runner, "finish_replaced", ended)
    now[0] = datetime(2026, 11, 12, 8, 1, 1)
    shutil.copy(madeup_out / "MU02_po_v1.pdf", d.settings.watched_folder)
    with pytest.raises(ProcessEnded):
        _pass(_Loop(d))
    monkeypatch.undo()
    assert [path.name for path in cases_of(d, "250102")] == ["250102_20261112T080102"]
    now[0] = datetime(2026, 11, 12, 8, 2, 1)
    _pass(_Loop(d))                                                                        # the runner started again
    assert [path.name for path in cases_of(d, "250102")] == ["250102_20261112T080102"] and replaced_by(unknown[1]) == "250102_20261112T080102"
    assert linked(d)["2.pdf"] == "250102"


HELD_VS_LINES = ("po_number,line_id,item_code,description,quantity,unit,unit_price,price_basis,delivery_date\n"
                 "250199,1,KM-1001,\"Hydraulische slang 10 mm, 2 m\",10,st,12.50,1,2026-11-16\n"
                 "250199,2,KM-9901,Koppeling,4,st,9.50,1,2026-11-16\n"
                 "250199,3,KM-9902,Filter,6,st,7.50,1,2026-11-16\n")


def test_the_held_number_comes_before_the_lines_at_the_scan_and_at_the_check_again(tmp_path, madeup_out):
    """Ruling N25 and the checkers' test_zz_held_vs_lines.py: MU01's confirmation names "Uw referentie 250199", a repeat order held as an
    export look with one of MU01's item codes (KM-1001) beside two others, while its lines point to exactly one other PO, 250101, which
    holds all three of its item codes. The scan and the check again both give the held number, 250199, as rule 3 and ruling N2 say."""
    document = referenced(tmp_path / "c.pdf", reference="Uw referentie 250199")
    (tmp_path / "repeat.csv").write_text(HELD_VS_LINES, encoding="utf-8")
    now = [NINE]
    d = waiting_deps(tmp_path / "loop", madeup_out, now)
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"export_columns": EXPORT_COLUMNS})})
    shutil.copy(document, d.settings.watched_folder)
    _pass(_Loop(d))
    [first] = cases_of(d, "unknown")
    now[0] = datetime(2026, 11, 12, 9, 1)
    shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder)
    shutil.copy(tmp_path / "repeat.csv", d.settings.watched_folder)
    _pass(_Loop(d))
    lines = read_with_template(document, detect_template(document, load_templates()))
    assert d.store.find_by_lines(lines) == ["250101"]                                    # the lines alone point to the other PO
    assert replaced_by(first) is not None and replaced_by(first).startswith("250199_")
    assert scanned_pos(d, [document], tmp_path / "scan") == {"c.pdf": "250199"}


# --- the third review round (rulings N28 to N33) ---

from aid.review.app import load_case_view, page_rows  # noqa: E402
from aid.review.i18n import STRINGS  # noqa: E402


def prepared_rows(tmp_path, po_number: str) -> list[tuple[str, str]]:
    """The (rule, kind) of each row of prepared_changes.csv for the PO, in their order."""
    path = tmp_path / "prepared_changes.csv"
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines())) if path.exists() else []
    return [(row["rule_id"], row["kind"]) for row in rows if row["po_number"] == po_number]


def test_a_decision_saved_while_the_new_case_is_opened_removes_the_new_case(tmp_path, madeup_out, monkeypatch):
    """Ruling N28 and the checkers' race_opened_then_close.json: MU02's confirmation waits for its PO from 08:01, and the pass at 08:02
    files the PO. Right after the check again opened the new case, the buyer saves "Houden" on the waiting case. The second read of
    the case's state finds the decision: the new case, which has not run, is removed, and the old case is left alone. The buyer then
    closes the old case, and the first pass after a restart changes nothing: one live case, one set of writes for the confirmation
    (rule B01, the buyer's close), and the decision carried out."""
    now = [datetime(2026, 11, 12, 8, 1)]
    d = waiting_deps(tmp_path, madeup_out, now)
    root = d.settings.cases_root
    shutil.copy(madeup_out / "MU02_conf.pdf", d.settings.watched_folder)
    loop = _Loop(d)
    _pass(loop)
    [old] = cases_of(d, "250102")
    real_open, opened = aid.runner.open_free, []

    def opening(cases_root, kind, files, po_number, at, *args, **kwargs):
        case = real_open(cases_root, kind, files, po_number, at, *args, **kwargs)
        opened.append(case.root)
        decide(load_case(root, old.name), "RI1", "keep")                                 # meanwhile, on the page
        return case
    monkeypatch.setattr(aid.runner, "open_free", opening)
    now[0] = datetime(2026, 11, 12, 8, 2)
    shutil.copy(madeup_out / "MU02_po_v1.pdf", d.settings.watched_folder)
    _pass(loop)
    monkeypatch.undo()
    assert [path.name for path in opened] == ["250102_20261112T080200"] and not opened[0].exists()
    assert cases_of(d, "250102") == [old] and replaced_by(old) is None and result_of(old).status == "review"
    assert close_case(load_case(root, old.name), d).status == "closed"                    # the buyer's close
    now[0] = datetime(2026, 11, 12, 8, 3)
    _pass(_Loop(d))                                                                        # the runner started again
    assert cases_of(d, "250102") == [old] and replaced_by(old) is None
    assert [record["action"] for record in json.loads((old / "steps" / "09_close.json").read_text(encoding="utf-8"))["records"]] == ["close"]
    assert prepared_rows(tmp_path, "250102") == [("B01", "attach_document"), ("B01", "set_status")]
    assert "250102_20261112T080200" not in reported(d) and not (root / "runner.log").exists()


def test_the_second_read_removes_the_new_case_before_the_state_is_saved(tmp_path, madeup_out, monkeypatch):
    """Rulings N28 and N35: the second read comes before the old case leaves the digest's list. A decision saved while the new case is
    opened is found there, so the new case is removed before the check again saves any state: a process that would end at that save
    (a log-off, a crash) leaves no new case behind, which the next start would run beside the decided case."""
    now = [datetime(2026, 11, 12, 8, 1)]
    d = waiting_deps(tmp_path, madeup_out, now)
    root = d.settings.cases_root
    shutil.copy(madeup_out / "MU02_conf.pdf", d.settings.watched_folder)
    loop = _Loop(d)
    _pass(loop)
    [old] = cases_of(d, "250102")
    real_open, real_save = aid.runner.open_free, aid.runner._save_state

    def opening(cases_root, kind, files, po_number, at, *args, **kwargs):
        case = real_open(cases_root, kind, files, po_number, at, *args, **kwargs)
        decide(load_case(root, old.name), "RI1", "keep")                                 # meanwhile, on the page
        return case

    def save(cases_root, state):
        if old.name not in state["reported"]:
            raise ProcessEnded("while the old case leaves the digest's list")
        real_save(cases_root, state)
    monkeypatch.setattr(aid.runner, "open_free", opening)
    monkeypatch.setattr(aid.runner, "_save_state", save)
    now[0] = datetime(2026, 11, 12, 8, 2)
    shutil.copy(madeup_out / "MU02_po_v1.pdf", d.settings.watched_folder)
    try:
        _pass(loop)
    except ProcessEnded:                                                                   # only without the second read
        pass
    monkeypatch.undo()
    now[0] = datetime(2026, 11, 12, 8, 3)
    _pass(_Loop(d))                                                                        # the runner started again
    assert cases_of(d, "250102") == [old] and replaced_by(old) is None and result_of(old).status == "review"

def test_a_decision_saved_after_the_second_read_stops_the_finish(tmp_path, madeup_out, monkeypatch):
    """Rulings N28 and N35 and the checkers' race_reread2_decide.json: MU02's confirmation waits for its PO from 08:01, and the pass at
    08:02 files the PO. The buyer saves "Houden" on the waiting case after the check again's second read of its state: right after
    that read, or while the old case leaves the digest's list. The third read, right before the old case would be finished, finds the
    decision: the new case, which has not run, is removed, and the old case is left alone with its decision, back in the digest's
    list. The buyer then closes it, and the first pass after a restart changes nothing: one live case, one set of writes for the
    confirmation (rule B01, the buyer's close), and the decision carried out."""
    for where in ("after_the_second_read", "while_the_state_is_saved"):
        now = [datetime(2026, 11, 12, 8, 1)]
        d = waiting_deps(tmp_path / where, madeup_out, now)
        root = d.settings.cases_root
        shutil.copy(madeup_out / "MU02_conf.pdf", d.settings.watched_folder)
        loop = _Loop(d)
        _pass(loop)
        [old] = cases_of(d, "250102")
        assert old.name in reported(d), where
        real_read, real_save, reads = aid.runner.checked_again, aid.runner._save_state, []

        def deciding():
            if not (old / "decisions.jsonl").exists():
                decide(load_case(root, old.name), "RI1", "keep")                         # meanwhile, on the page

        def read(case):
            waits = real_read(case)
            if case.case_id == old.name:
                reads.append(waits)
                if len(reads) == 2 and where == "after_the_second_read":
                    deciding()
            return waits

        def save(cases_root, state):
            real_save(cases_root, state)
            if where == "while_the_state_is_saved" and len(reads) == 2 and old.name not in state["reported"]:
                deciding()
        monkeypatch.setattr(aid.runner, "checked_again", read)
        monkeypatch.setattr(aid.runner, "_save_state", save)
        now[0] = datetime(2026, 11, 12, 8, 2)
        shutil.copy(madeup_out / "MU02_po_v1.pdf", d.settings.watched_folder)
        _pass(loop)
        monkeypatch.undo()
        assert reads == ["po", "po", None], where
        assert cases_of(d, "250102") == [old] and not (root / "250102_20261112T080200").exists(), where
        assert replaced_by(old) is None and result_of(old).status == "review", where
        assert load_case_view(load_case(root, old.name)).decisions == {"RI1": "keep"} and reported(d) == [old.name], where
        assert close_case(load_case(root, old.name), d).status == "closed", where           # the buyer's close
        now[0] = datetime(2026, 11, 12, 8, 3)
        _pass(_Loop(d))                                                                    # the runner started again
        assert cases_of(d, "250102") == [old] and replaced_by(old) is None, where
        assert prepared_rows(tmp_path / where, "250102") == [("B01", "attach_document"), ("B01", "set_status")], where
        assert not (root / "runner.log").exists(), where


def test_a_decision_during_the_check_again_removes_a_successor_that_has_not_run(tmp_path, madeup_out, monkeypatch):
    """Ruling N35: a confirmation waits from 08:01; the pass at 08:02 opens its new case and the process ends before the old case is
    finished. After the restart the check again finds that new case as the old case's successor, and right then the buyer saves
    "Houden" on the old case, so the old case is left alone. A successor that has not run (MU02's) is removed, and the old case is the
    confirmation's one live case. A successor that has run stays: MU07's, which the pass at 08:03 ran to its result, in review with
    nothing written (that pass could not list the cases to check again, so the old case was not checked), and MU02's, whose run that
    pass cut off after its writes, which the runner then finishes for the buyer: removing it would lose the record of what it wrote."""
    for how, name, number in (("not_run", "MU02", "250102"), ("run", "MU07", "250107"), ("cut_off_after_writes", "MU02", "250102")):
        now = [datetime(2026, 11, 12, 8, 1)]
        d = waiting_deps(tmp_path / how, madeup_out, now)
        root = d.settings.cases_root
        shutil.copy(madeup_out / f"{name}_conf.pdf", d.settings.watched_folder)
        _pass(_Loop(d))
        [old] = cases_of(d, number)

        def ended(case, by, at, language):
            raise ProcessEnded("before the old case was finished")
        monkeypatch.setattr(aid.runner, "finish_replaced", ended)
        now[0] = datetime(2026, 11, 12, 8, 2)
        shutil.copy(madeup_out / f"{name}_po_v1.pdf", d.settings.watched_folder)
        with pytest.raises(ProcessEnded):
            _pass(_Loop(d))
        monkeypatch.undo()
        [_, successor] = cases_of(d, number)
        if how != "not_run":                                                               # the successor runs at 08:03

            def unlisted(cases_root):
                raise OSError("the cases folder cannot be listed")
            monkeypatch.setattr(aid.runner, "cases_checked_again", unlisted)
            real_step = Case.write_step

            def step(case, n, *args, **kwargs):
                if n == 8 and how == "cut_off_after_writes":
                    raise ProcessEnded("after the writes, before the result")
                return real_step(case, n, *args, **kwargs)
            monkeypatch.setattr(Case, "write_step", step)
            now[0] = datetime(2026, 11, 12, 8, 3)
            try:
                _pass(_Loop(d))
            except ProcessEnded:
                assert how == "cut_off_after_writes"
            monkeypatch.undo()
            written = (successor / "write_log.jsonl").read_text(encoding="utf-8").strip() if (successor / "write_log.jsonl").exists() else ""
            assert replaced_by(old) is None and ((successor / "steps" / "08_result.json").exists(), bool(written)) == (
                (True, False) if how == "run" else (False, True)), how
        real_successor = aid.runner._successor

        def finding(cases_root, case):
            found = real_successor(cases_root, case)
            decide(load_case(root, old.name), "RI1", "keep")                             # meanwhile, on the page
            return found
        monkeypatch.setattr(aid.runner, "_successor", finding)
        now[0] = datetime(2026, 11, 12, 8, 4)
        _pass(_Loop(d))                                                                    # the runner started again
        monkeypatch.undo()
        assert replaced_by(old) is None and result_of(old).status == "review", how
        if how == "not_run":
            assert cases_of(d, number) == [old] and not successor.exists()
        else:
            assert cases_of(d, number) == [old, successor] and replaced_by(successor) is None, how
            assert [item.reason for item in result_of(successor).review_items] == (["price above threshold"] if how == "run" else [CUT_OFF]), how


def test_a_guessed_case_without_a_version_says_that_nothing_was_compared(tmp_path, madeup_out):
    """Ruling N29 and the checkers' replay_guess_no_version.py and replay_guess_no_version_close.py: the number recognition knows "PO"
    and "Bestelling" only, so MU03's confirmation ("Uw inkooporder 250103, versie 1") is linked by its held number to PO 250103, whose
    version 2 alone the store holds. No version is selected, so the case's review keeps R09's item "Geen versie van de inkooporder
    gekozen" beside the guess's item: the page shows the two items and no order, with no hint, and no count lists the case. "Bevestigen"
    on the guess's item and the close leave the case in review with nothing written: the item of R09 is decided as any other."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"po_number_pattern": pattern_from("PO, Bestelling", "6", "8", "nl")[0]})})
    root = d.settings.cases_root
    d.store.add(madeup_out / "MU03_po_v2.pdf")
    shutil.copy(madeup_out / "MU03_conf.pdf", d.settings.watched_folder)
    _pass(_Loop(d))
    [case_root] = cases_of(d, "250103")
    result = result_of(case_root)
    assert found_by(case_root) == FOUND_HELD_NUMBER and (result.status, result.version.version, result.findings) == ("review", None, None)
    assert [(item.item_id, item.finding_id, item.reason) for item in result.review_items] == [("RI1", None, GUESSED_PO), ("RI2", None, NO_VERSION)]
    view = load_case_view(load_case(root, case_root.name), d.store)
    assert view.po_pages == [] and [row[5] for row in page_rows(view.flags, "nl")] == [STRINGS["nl"][f"reason:{GUESSED_PO}"],
                                                                                         "Geen versie van de inkooporder gekozen"]
    assert waiting_for_po(load_case(root, case_root.name)) is None and cases_waiting_for_po(root) == []
    decide(load_case(root, case_root.name), "RI1", "confirm")
    closed = close_case(load_case(root, case_root.name), d)
    assert (closed.status, closed.write_entries) == ("review", []) and prepared_rows(tmp_path, "250103") == []
    assert load_case_view(load_case(root, case_root.name)).decisions == {"RI1": "confirm"}


def test_each_finding_of_a_guessed_case_is_its_own_item(tmp_path, madeup_out):
    """Ruling N30 and the checkers' n18k/consequence.py: the one REVIEW action of a guessed case lists every finding, so each finding
    is an item of its own, each with the guess's reason. MU07's confirmation under "Ihre Referenz 250107" (a held number) has its price
    flag, and MU06's under "Ihre Referenz 250106" its three uncompared items. "Bevestigen" on MU07's price item and the close then write
    the supplier's price over the PO line, as on any case."""
    now, guessed = [NINE], {}
    for case_id, number, expected in (("MU07", "250107", [("RI1", "F1", "price")]),
                                      ("MU06", "250106", [("RI1", "U1", "price"), ("RI2", "U2", "price"), ("RI3", "U3", "price")])):
        d = waiting_deps(tmp_path / case_id, madeup_out, now)
        d.store.add(madeup_out / f"{case_id}_po_v1.pdf")
        shutil.copy(referenced(tmp_path / f"{case_id}.pdf", case_id, f"Ihre Referenz {number}"), d.settings.watched_folder)
        _pass(_Loop(d))
        [case_root] = cases_of(d, number)
        result = result_of(case_root)
        assert found_by(case_root) == FOUND_HELD_NUMBER and (result.status, result.write_entries, result.messages) == ("review", [], []), case_id
        assert [(item.item_id, item.finding_id, item.field) for item in result.review_items] == expected, case_id
        assert [item.reason for item in result.review_items] == [GUESSED_PO] * len(expected), case_id
        guessed[case_id] = (d, load_case(d.settings.cases_root, case_root.name))
    d, case = guessed["MU07"]
    decide(case, "RI1", "confirm")
    assert [(entry.rule_id, entry.op.kind, entry.op.new_value) for entry in close_case(case, d).write_entries] == [
        ("B01", "po_line_price", "14.00"), ("B01", "attach_document", None), ("B01", "set_status", "Bevestigd")]


def test_the_check_again_links_a_case_by_a_held_number_alone(tmp_path, madeup_out):
    """Ruling N31 and the checkers' n21c/held_only.py: MU01's confirmation under "Uw referentie 250101", a word the number recognition
    does not know, with its line 1 alone (item code KM-1001): its lines point to no PO, and its first page holds the PO's number with
    one of the PO's item codes. It waits without a number. The pass that files PO 250101 links it by the held number, and so does
    the first pass of a run after the PO was filed while the loop did not run, as the scan of the same document does."""
    pages = confirmation_pages(SPECS["MU01"])
    next(row for row in pages[0] if row.ref == "po_reference").texts[0].text = "Uw referentie 250101"
    pages[0][:] = [row for row in pages[0] if row.ref not in ("line 2", "line 3")]
    document = tmp_path / "one_line.pdf"
    document.write_bytes(pdf_bytes(pages, document.name))
    for when in ("filed_in_the_pass", "at_a_first_pass"):
        now = [NINE]
        d = waiting_deps(tmp_path / when, madeup_out, now)
        shutil.copy(document, d.settings.watched_folder)
        loop = _Loop(d)
        _pass(loop)
        [waiting] = cases_of(d, "unknown")
        now[0] = datetime(2026, 11, 12, 9, 1)
        if when == "filed_in_the_pass":
            shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder)
            _pass(loop)
        else:
            d.store.add(madeup_out / "MU01_po_v1.pdf")                                     # filed while the loop did not run
            _pass(_Loop(d))                                                                # the first pass of a new run
        assert d.store.find_by_lines(read_with_template(document, detect_template(document, load_templates()))) == [], when
        [case_root] = cases_of(d, "250101")
        assert replaced_by(waiting) == case_root.name and found_by(case_root) == FOUND_HELD_NUMBER, when
        assert scanned_pos(d, [document], tmp_path / f"scan_{when}") == {"one_line.pdf": "250101"}, when


def test_the_recheck_stage_logs_a_recovery_only_after_a_pass_that_filed_a_po(tmp_path, madeup_out, monkeypatch):
    """Ruling N33 and the checkers' stage_recovery.py: the stage of the check again fails (the cases folder cannot be listed) in the
    pass at 09:01, which files MU02's PO. The pass at 09:02 files nothing: the stage had nothing to do, so no recovery is logged and
    the failure stays noted. The pass at 09:03 files MU01's PO and checks MU01's waiting case again: then the recovery is logged."""
    now = [NINE]
    d = waiting_deps(tmp_path, madeup_out, now)
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    loop = _Loop(d)
    _pass(loop)
    [waiting] = cases_of(d, "250101")

    def broken(cases_root):
        raise OSError("the cases folder cannot be listed")
    monkeypatch.setattr(aid.runner, "cases_checked_again", broken)
    now[0] = datetime(2026, 11, 12, 9, 1)
    shutil.copy(madeup_out / "MU02_po_v1.pdf", d.settings.watched_folder)
    _pass(loop)
    monkeypatch.undo()
    now[0] = datetime(2026, 11, 12, 9, 2)
    _pass(loop)                                                                            # files nothing
    log = d.settings.cases_root / "runner.log"
    assert log.read_text(encoding="utf-8").splitlines() == ["2026-11-12T09:01:00 recheck: OSError: the cases folder cannot be listed"]
    now[0] = datetime(2026, 11, 12, 9, 3)
    shutil.copy(madeup_out / "MU01_po_v1.pdf", d.settings.watched_folder)
    _pass(loop)
    assert log.read_text(encoding="utf-8").splitlines()[1:] == ["2026-11-12T09:03:00 recheck: ran after an earlier failure"]
    assert replaced_by(waiting) is not None


# --- a change seen in a new PO PDF (Task 35) ---

def test_the_pass_marks_what_a_new_po_pdf_shows(tmp_path, madeup_out):
    """After the cases ran, each pass compares every open change of a PO line with the newest version of its PO: a new PO PDF that
    Exact sent after the change, dropped into the inbox, ticks the price off as seen in version 2, and the article's price stays open.
    The next pass over the same documents marks nothing more, and nothing is logged."""
    from aid.execution.by_hand import MARKS_FILE, changes
    from aid.tests.helpers import newer_po

    # The case runs on 2 November; the new PDF is filed when this test runs, after it, as Exact sends it after the change (a look
    # counts only when it was filed after the change was asked: Task 35's review round, K1).
    d = loop_deps(tmp_path, madeup_out, clock=datetime(2026, 11, 2, 9, 0))
    prepared = d.settings.cases_root / "prepared"
    d = d.model_copy(update={"writer": PreparedWriter(prepared)})                  # where build_deps has the changes prepared
    shutil.copy(madeup_out / "MU08_conf.pdf", d.settings.watched_folder)
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert [(change.kind, change.state) for change in changes(prepared)[0]] == [("po_line_price", "open"), ("article_price", "open")]
    assert not (prepared / MARKS_FILE).exists()                                     # version 1 shows the old price
    newer_po(d.settings.watched_folder / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.70"})
    d = d.model_copy(update={"clock": lambda: datetime(2026, 11, 12, 9, 0)})
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert [version.version for version in d.store.versions("250108")] == [1, 2]
    price, article = changes(prepared)[0]
    assert (price.state, price.marked_by, price.marked_at) == ("seen", "version 2", "2026-11-12T09:00:00") and article.state == "open"
    marks = (prepared / MARKS_FILE).read_bytes()
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2)
    assert (prepared / MARKS_FILE).read_bytes() == marks
    assert not (d.settings.cases_root / "runner.log").exists()


def test_a_list_that_cannot_be_compared_is_logged_once_and_the_pass_goes_on(tmp_path, madeup_out, monkeypatch):
    """The comparison of the open changes is one guarded step of the pass: an error in it is logged once in a run of the loop, with
    its stage, and once more when it works again; the digest and the rest of the pass go on."""
    d = loop_deps(tmp_path, madeup_out, clock=AFTER_DIGEST_TIME)
    calls = []

    def failing(prepared, store, cases_root, now):
        calls.append(now)
        if len(calls) < 3:
            raise OSError("made-up failure")
        return 0
    monkeypatch.setattr(aid.runner, "reconcile", failing)
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=3) == 3 and len(calls) == 3
    log = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert [line.split(" ", 1)[1] for line in log] == ["list of changes: OSError: made-up failure", "list of changes: ran after an earlier failure"]
    assert (d.settings.cases_root / "digests" / "2026-11-12.txt").exists()
