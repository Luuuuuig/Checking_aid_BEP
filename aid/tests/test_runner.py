"""Tests of the runner (aid/runner.py): the loop that both hosts run, on the made-up set, and the Deps its command builds."""
import json
import shutil
from datetime import datetime

import pytest

import aid.runner
from aid.case import open_case
from aid.decider import NoDecider
from aid.execution.exact_api import ExactApiWriter
from aid.execution.writer import PreparedWriter, RetryingWriter, WriterError
from aid.messaging import FileMailer, MailerError, SmtpMailer
from aid.pipeline import read_with_parser, record_dispositions, run_case
from aid.runner import build_deps, main, run_loop
from aid.settings import load_settings
from aid.tests.conftest import SETTINGS, deps


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


@pytest.mark.parametrize("content", ['{"last_sent": nu', '{"last_sent": null, "reported": []}', '[]'])
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


def test_the_llm_reader_is_refused_until_the_microsoft_client_exists(tmp_path, capsys):
    settings, path = settings_file(tmp_path, reader="llm")
    with pytest.raises(ValueError, match="Task 7b"):
        build_deps(settings, {})
    assert main(["--settings", str(path), "--max-passes", "1", "--poll-seconds", "0"]) == 2
    assert "Task 7b" in capsys.readouterr().err
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
