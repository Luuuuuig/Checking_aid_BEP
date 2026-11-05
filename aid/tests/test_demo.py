"""The one-command demo of aid/demo.py: a folder with valid settings, the 22 made-up cases and the batch's first pass."""
import csv

from aid import demo
from aid.demo import RECORDED_DIR, main
from aid.readers.llm_client import RecordedClient
from aid.runner import run_loop
from aid.runner import build_deps
from aid.review.app import list_open_cases
from aid.settings import load_settings
from aid.tests.conftest import read_csv
from aid.tests.test_batch import PARSER_STATUS, RECORDED_STATUS


def test_the_demo_prepares_the_folder_and_runs_the_made_up_set(tmp_path, capsys):
    """One command gives a settings file that loads, the 22 cases, the summary with the statuses the batch pins and the folders
    the review page and the runner need."""
    out = tmp_path / "demo"
    assert main(["--out", str(out)]) == 0
    settings = load_settings(out / "settings.json")
    assert settings.reader == "parser" and settings.cases_root == out / "cases" and settings.watched_folder == out / "inbox"
    assert settings.supplier_lead_times["S01"] == 10 and settings.supplier_lead_times["S22"] == 10
    cases = read_csv(out / "cases.csv")
    assert [row["case_id"] for row in cases] == list(PARSER_STATUS) and {row["kind"] for row in cases} == {"confirmation", "cart"}
    assert cases[0]["po_number"] == "250101" and cases[-1]["document"].endswith("CART02.html")
    summary = read_csv(out / "summary.csv")
    expected = PARSER_STATUS | {"CART02": "review"}                  # the demo gives CART02 as its saved page, which the parser run reads
    assert {row["case_id"]: row["status"] for row in summary} == expected
    assert (out / "inbox").is_dir() and (out / "cases" / "prepared" / "prepared_changes.csv").exists()
    open_pos = {summary.po_number for summary in list_open_cases(settings.cases_root)}       # the page lists the cases in review
    assert open_pos == {row["po_number"] for row in cases if expected[row["case_id"]] == "review"}
    text = capsys.readouterr().out
    assert "22 made-up cases run with the parser reader" in text and "MU05, MU16" in text and "aid.review" in text


def test_the_recorded_reader_replays_every_document(tmp_path):
    out = tmp_path / "demo"
    assert main(["--out", str(out), "--reader", "recorded"]) == 0
    assert {row["case_id"]: row["status"] for row in read_csv(out / "summary.csv")} == RECORDED_STATUS


def test_a_folder_that_is_not_empty_is_refused_without_force(tmp_path, capsys):
    out = tmp_path / "demo"
    out.mkdir()
    (out / "note.txt").write_text("x", encoding="utf-8")
    assert main(["--out", str(out)]) == 2 and "not empty" in capsys.readouterr().err
    assert not (out / "settings.json").exists()
    assert main(["--out", str(out), "--force"]) == 0 and (out / "summary.csv").exists()


def test_the_anthropic_reader_needs_the_key_in_the_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["--out", str(tmp_path / "demo"), "--reader", "anthropic"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err and not (tmp_path / "demo" / "summary.csv").exists()


def test_the_anthropic_reader_is_wired_like_the_notebook(tmp_path, monkeypatch):
    """With the client replaced by the recorded one (no call), the live option reads every document and asks the decider."""
    class Replayed(RecordedClient):
        def __init__(self, model, effort="medium"):
            super().__init__(RECORDED_DIR)
            self.model, self.effort = model, effort
    monkeypatch.setenv("ANTHROPIC_API_KEY", "made-up-key-for-the-test")
    monkeypatch.setattr(demo, "AnthropicClient", Replayed)
    out = tmp_path / "demo"
    assert main(["--out", str(out), "--reader", "anthropic"]) == 0
    assert {row["case_id"]: row["status"] for row in read_csv(out / "summary.csv")} == RECORDED_STATUS


def test_a_prepared_folder_lets_the_loop_open_a_case_by_itself(tmp_path, capsys):
    """--prepare-only files the purchase orders and runs nothing; a confirmation dropped into the inbox is then a new case for the
    loop, not a duplicate."""
    import os
    import shutil
    from aid.demo import MADEUP_OUT
    out = tmp_path / "live"
    assert main(["--out", str(out), "--prepare-only"]) == 0
    assert not (out / "summary.csv").exists() and (out / "inbox").is_dir()
    assert "no case run" in capsys.readouterr().out
    settings = load_settings(out / "settings.json")
    assert settings.po_store_root.exists() and len(list(settings.po_store_root.iterdir())) >= 21
    shutil.copy(MADEUP_OUT / "MU03_conf.pdf", out / "inbox")
    deps = build_deps(settings, dict(os.environ))
    assert run_loop(deps, out / "STOP", poll_seconds=0, max_passes=1) == 1
    assert (out / "inbox" / "processed" / "MU03_conf.pdf").exists()
    folders = [p.name for p in settings.cases_root.iterdir() if p.name.startswith("250103_")]
    assert len(folders) == 1 and (settings.cases_root / folders[0] / "steps" / "08_result.json").exists()
