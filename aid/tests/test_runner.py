"""Tests of the runner (aid/runner.py): the loop that both hosts run, on the made-up set, and the Deps its command builds."""
import json
import shutil
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
from aid.tests.conftest import SETTINGS, deps
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
    [line] = (d.settings.cases_root / "runner.log").read_text(encoding="utf-8").splitlines()
    assert " digest: " in line and ("IsADirectoryError" in line or "PermissionError" in line) and "the list of changes to enter" in line
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
