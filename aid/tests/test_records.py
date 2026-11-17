"""Tests of the records command of the evaluation (aid/evalscripts/records.py): the helpers its three subcommands share (the
wildcards, the refusals for the output folder, the writing of all files together, the manifest), the command line, and the
subcommand log with its two files, timer_check.csv and case_log.csv.

The timer's exports are written by the helpers of this file as the timer writes them, with made-up case ids and notes; the case
folders are made by the aid itself from the made-up set (open_case, run_case, the review page's close) or written by hand. No test
calls a model or the network.
"""
import argparse
import ast
import builtins
import contextlib
import csv
import hashlib
import io
import itertools
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from aid import start
from aid.case import Case, open_case
from aid.evalscripts import case_runs, records
from aid.evalscripts.records import check_out, expand, main, write_all, write_manifest
from aid.evalscripts.timer_log import EVENT_COLUMNS, TIMING_COLUMNS, Refusal
from aid.pipeline import NO_STORED_VERSION, CaseResult, Deps, record_dispositions, run_case
from aid.po_store import PoStore
from aid.review.app import decide
from aid.review.close import close_case
from aid.tests.conftest import REPO_ROOT, deps, read_csv
from aid.tests.fakes import case_result
from aid.versioning import VersionChoice


def a_parser() -> argparse.ArgumentParser:
    """A parser as a subcommand has one: its error() ends the command with exit code 2 and the sentence on stderr."""
    return argparse.ArgumentParser(prog="python -m aid.evalscripts.records log")


def refused(capsys, call, *args, **kwargs) -> str:
    """The sentence a call is refused with: it must end the command with exit code 2. Returns what stood on stderr."""
    with pytest.raises(SystemExit) as stopped:
        call(*args, **kwargs)
    assert stopped.value.code == 2
    return capsys.readouterr().err


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- the wildcards of the file arguments ---

def test_a_file_argument_with_a_wildcard_is_expanded_by_the_command(tmp_path, capsys):
    """The command line of Windows expands no wildcard, so the command does: a star or a question mark gives the files that
    match, in the order of their names, each once; a name without one is kept as it stands, whether the file is there or not."""
    for name in ("timer_events_2.csv", "timer_events_1.csv", "timing_1.csv", "timer_events [1].csv"):
        (tmp_path / name).write_text("", encoding="utf-8")
    found = expand(a_parser(), [str(tmp_path / "timer_events_*.csv"), str(tmp_path / "timing_?.csv"), str(tmp_path / "absent.csv")])
    assert found == [tmp_path / "timer_events_1.csv", tmp_path / "timer_events_2.csv", tmp_path / "timing_1.csv", tmp_path / "absent.csv"]
    assert expand(a_parser(), [str(tmp_path / "timer_events_1.csv"), str(tmp_path / "timer_*_1.csv")]) == [tmp_path / "timer_events_1.csv"]
    assert expand(a_parser(), [str(tmp_path / "timer_events [1]*")]) == [tmp_path / "timer_events [1].csv"]     # a bracket is no wildcard
    assert expand(a_parser(), None) == [] and expand(a_parser(), tmp_path / "timing_1.csv") == [tmp_path / "timing_1.csv"]
    assert capsys.readouterr().err == ""


def test_a_pattern_that_matches_nothing_is_refused_by_its_name(tmp_path, capsys):
    """The sentence names the argument and the pattern without its folder."""
    error = refused(capsys, expand, a_parser(), [str(tmp_path / "timer_events_*.csv")], "--events")
    assert "--events: no file matches timer_events_*.csv" in error and str(tmp_path) not in error
    assert "no file matches t?ming.csv" in refused(capsys, expand, a_parser(), [str(tmp_path / "t?ming.csv")])


# --- the refusals for the output folder ---

NAMES = ["case_log.csv", "timer_check.csv", "log_manifest.json"]


@pytest.fixture
def data_folder(tmp_path) -> Path:
    """A data folder of the aid under tmp_path/data, written by hand: the settings file beside the cases root, the PO store and
    the watched folder, and one case folder."""
    data = tmp_path / "data"
    for name in ("cases/250101_20261112T090000/steps", "cases/prepared", "po_store", "inbox"):
        (data / name).mkdir(parents=True)
    (data / "settings.json").write_text("{}", encoding="utf-8")
    (data / "cases" / "250101_20261112T090000" / "case.json").write_text("{}", encoding="utf-8")
    return data


def test_an_output_folder_outside_the_data_folder_is_accepted_and_not_made(tmp_path, data_folder):
    """The check makes nothing: the folder is made when the files are written. It gives the folder back as a resolved path."""
    out = tmp_path / "records" / "2026-11-23"
    assert check_out(a_parser(), out, data_folder / "cases", NAMES) == Path(os.path.realpath(out)) and not (tmp_path / "records").exists()
    assert check_out(a_parser(), str(out), None, NAMES) == Path(os.path.realpath(out))                  # counts has no cases root
    out.mkdir(parents=True)
    (out / "notes.txt").write_text("a file of another name is no output", encoding="utf-8")
    assert check_out(a_parser(), out, data_folder / "cases", NAMES) == Path(os.path.realpath(out))
    try:
        (tmp_path / "shortcut").symlink_to(tmp_path / "records", target_is_directory=True)
    except OSError:                                                       # Windows without the right to make a link
        return
    resolved = Path(os.path.realpath(tmp_path / "records")) / "2026-11-24"
    assert check_out(a_parser(), tmp_path / "shortcut" / "2026-11-24", None, NAMES) == resolved


@pytest.mark.parametrize("out,cases_root,sentence", [
    ("data/cases", "data/cases", "--out is the cases root (--cases-root) or lies inside it"),
    ("data/cases/new", "data/cases", "--out is the cases root (--cases-root) or lies inside it"),
    ("data/cases/prepared/new/deeper", "data/cases", "--out is the cases root (--cases-root) or lies inside it"),
    ("records/../data/cases/new", "data/cases", "--out is the cases root (--cases-root) or lies inside it"),
    ("link_to_cases/new", "data/cases", "--out is the cases root (--cases-root) or lies inside it"),
    ("data/cases/250101_20261112T090000", None, "--out lies inside a case folder"),
    ("data/cases/250101_20261112T090000/steps/records", None, "--out lies inside a case folder"),
    ("data/cases", None, "--out is a cases root or lies inside one"),
    ("data/cases/new", None, "--out is a cases root or lies inside one"),
    ("link_to_cases", None, "--out is a cases root or lies inside one"),
    ("data", None, "--out is a data folder of the aid or lies inside one"),
    ("data/inbox", None, "--out is a data folder of the aid or lies inside one"),
    ("data/po_store/records", None, "--out is a data folder of the aid or lies inside one"),
    ("data/records", "data/cases", "--out is a data folder of the aid or lies inside one"),
])
def test_an_output_folder_in_the_aids_own_folders_is_refused_before_it_is_made(tmp_path, data_folder, capsys, out, cases_root, sentence):
    """The command writes nothing into a case folder, the cases root, the PO store or the watched folder. The paths are resolved
    before they are compared, so that a link and a path with dots are seen for what they are; every sentence says what to do."""
    (tmp_path / "records").mkdir()
    try:
        (tmp_path / "link_to_cases").symlink_to(data_folder / "cases", target_is_directory=True)
    except OSError:                                                       # Windows without the right to make a link
        if "link_to_cases" in out:
            pytest.skip("this system makes no symbolic links")
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    error = refused(capsys, check_out, a_parser(), tmp_path / out, tmp_path / cases_root if cases_root else None, NAMES)
    assert sentence in error and "give a new folder outside the aid's data folder" in error
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == before


def test_the_refusal_names_the_folder_that_stands_in_the_way(tmp_path, data_folder, capsys):
    """A case folder that was copied somewhere for a look makes a cases root of the folder it lies in: the sentence says which
    folder it is, so that the output can be given another place. The name of a case folder is never shown."""
    error = refused(capsys, check_out, a_parser(), data_folder / "inbox" / "records", None, NAMES)
    assert "--out is a data folder of the aid or lies inside one (data holds a settings.json beside cases, po_store or inbox)" in error
    error = refused(capsys, check_out, a_parser(), data_folder / "cases" / "prepared" / "records", None, NAMES)
    assert "--out is a cases root or lies inside one (cases holds a folder with a case.json)" in error and "2501" not in error
    error = refused(capsys, check_out, a_parser(), data_folder / "cases" / "250101_20261112T090000" / "steps", None, NAMES)
    assert "--out lies inside a case folder" in error and "2501" not in error


@pytest.mark.parametrize("left", ["case_log.csv", "timer_check.csv", "log_manifest.json", "timer_check.csv.partial"])
def test_an_output_that_is_already_there_is_never_overwritten(tmp_path, capsys, left):
    """There is no force option: a sheet that a rater filled is not written over, and a half-written file of a run that was cut off
    is not taken for nothing. The sentence says what to do."""
    out = tmp_path / "records"
    out.mkdir()
    (out / left).write_text("filled by hand", encoding="utf-8")
    error = refused(capsys, check_out, a_parser(), out, None, NAMES)
    assert f"{left} is already in {out}: give a new folder, for example one per day" in error
    assert (out / left).read_text(encoding="utf-8") == "filled by hand"
    file = tmp_path / "a_file"
    file.write_text("", encoding="utf-8")
    assert "--out is a file: give a folder" in refused(capsys, check_out, a_parser(), file, None, NAMES)


# --- writing all files together ---

def test_all_files_are_written_under_a_partial_name_and_renamed_together(tmp_path):
    """Text is written as UTF-8 with the line ends it has; bytes as they are. The folder is made when it is missing."""
    out = tmp_path / "records" / "2026-11-23"
    check = "case_id,hints\nC01,caf\u00e9\n"
    written = write_all(out, {"timer_check.csv": check, "sheet.xlsx": b"PK\x03\x04", "log_manifest.json": "{}\n"})
    assert written == [out / "timer_check.csv", out / "sheet.xlsx", out / "log_manifest.json"]
    assert (out / "timer_check.csv").read_bytes() == check.encode("utf-8")     # no \r\n, on any system
    assert (out / "sheet.xlsx").read_bytes() == b"PK\x03\x04"
    assert sorted(path.name for path in out.iterdir()) == ["log_manifest.json", "sheet.xlsx", "timer_check.csv"]


def test_a_failure_while_writing_leaves_no_file_of_the_run(tmp_path, monkeypatch):
    """The second rename fails as it does when a virus scanner or a spreadsheet holds the file: the partial files are removed, and
    so is the file that was renamed already, so that the next run is not refused for an output that is already there. The sentence
    is plain and names the file without its folder."""
    out = tmp_path / "records"
    out.mkdir()
    (out / "notes.txt").write_text("kept", encoding="utf-8")
    real, renamed = os.replace, []

    def replace(source, target):
        if len(renamed) == 1:
            raise PermissionError(13, "Permission denied", str(source))
        renamed.append(target)
        real(source, target)
    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(Refusal) as stopped:
        write_all(out, {"case_log.csv": "a\n", "timer_check.csv": "b\n", "log_manifest.json": "{}\n"})
    assert str(stopped.value) == "timer_check.csv is open in another program (Excel?): close it and run again"
    assert [path.name for path in out.iterdir()] == ["notes.txt"] and len(renamed) == 1


def test_a_folder_that_cannot_be_made_or_written_into_is_said_as_that(tmp_path, monkeypatch):
    """A place where the person may not write is no file that a spreadsheet holds: the sentence does not send them looking for one."""
    (tmp_path / "a_file").write_text("", encoding="utf-8")
    with pytest.raises(Refusal) as stopped:
        write_all(tmp_path / "a_file" / "records", {"timer_check.csv": "a\n"})
    assert re.fullmatch(r"the folder of --out cannot be made \((NotADirectoryError|FileExistsError)\): give a folder you may write into",
                        str(stopped.value))
    real = builtins.open

    def no_right(file, mode="r", *args, **kwargs):
        if "x" in mode:
            raise PermissionError(13, "Permission denied", str(file))
        return real(file, mode, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", no_right)
    with pytest.raises(Refusal) as stopped:
        write_all(tmp_path / "records", {"timer_check.csv": "a\n"})
    assert str(stopped.value) == ("the records cannot be written into the folder of --out (PermissionError): give a folder you may "
                                  "write into")
    assert list((tmp_path / "records").iterdir()) == []


def test_writing_never_goes_over_a_file_that_is_there(tmp_path):
    """Also without the check of the output folder: nothing is written when one of the names, or its partial name, is taken."""
    out = tmp_path / "records"
    out.mkdir()
    (out / "timer_check.csv.partial").write_text("left by a run that was cut off", encoding="utf-8")
    with pytest.raises(Refusal, match=r"^timer_check\.csv\.partial is already in .*: give a new folder, for example one per day$"):
        write_all(out, {"case_log.csv": "a\n", "timer_check.csv": "b\n"})
    assert [path.name for path in out.iterdir()] == ["timer_check.csv.partial"]


# --- the manifest ---

def test_the_manifest_says_what_the_records_were_made_from(tmp_path, monkeypatch):
    """The subcommand and its options, the day, the aid's version, and the base name and sha256 of every input and output file.
    The outputs given with their content are written together with the manifest, as one set; a path among the options or the
    inputs is reduced to its base name."""
    monkeypatch.setattr(records, "today", lambda: date(2026, 11, 23))
    events = tmp_path / "downloads" / "timer_events_2026-11-23-15-02.csv"
    events.parent.mkdir()
    events.write_text("event_id\n", encoding="utf-8")
    out = tmp_path / "records"
    path = write_manifest(out, "log_manifest.json", subcommand="log",
                          options={"events": [events], "cases_root": tmp_path / "data" / "cases", "check_only": True, "seq": None},
                          inputs=[events, ("case_list.csv", b"case_id,po_number\n")], outputs={"timer_check.csv": "case_id\nC01\n"},
                          cases=1, notes=["a note that applies"], set="made-up", statements={"references_locked": "2026-12-01"})
    assert path == out / "log_manifest.json" and sorted(file.name for file in out.iterdir()) == ["log_manifest.json", "timer_check.csv"]
    manifest = json.loads(path.read_bytes())
    assert list(manifest)[:6] == ["subcommand", "options", "day", "aid_version", "inputs", "outputs"]
    assert manifest["subcommand"] == "log" and manifest["day"] == "2026-11-23" and manifest["aid_version"] == "0.1"
    assert manifest["options"] == {"events": ["timer_events_2026-11-23-15-02.csv"], "cases_root": "cases", "check_only": True, "seq": None}
    assert manifest["inputs"] == [{"name": events.name, "sha256": sha256(events)},
                                  {"name": "case_list.csv", "sha256": hashlib.sha256(b"case_id,po_number\n").hexdigest()}]
    assert manifest["outputs"] == [{"name": "timer_check.csv", "sha256": sha256(out / "timer_check.csv")}]
    assert (manifest["cases"], manifest["notes"], manifest["set"]) == (1, ["a note that applies"], "made-up")
    assert manifest["statements"] == {"references_locked": "2026-12-01"}
    assert str(tmp_path) not in path.read_text(encoding="utf-8") and path.read_bytes().endswith(b"}\n")


def test_the_manifest_of_files_that_are_written_already_names_them_by_their_content(tmp_path):
    """A subcommand that wrote its outputs with write_all gives their names: the sha256 values are those of the files."""
    out = tmp_path / "records"
    write_all(out, {"raw_output_counts.csv": "field\nprice\n", "raw_output_totals.csv": "quantity\n"})
    names = ("raw_output_counts.csv", "raw_output_totals.csv")
    path = write_manifest(out, "counts_manifest.json", subcommand="counts", options={}, outputs=[names[0], out / names[1]])
    manifest = json.loads(path.read_bytes())
    assert manifest["outputs"] == [{"name": name, "sha256": sha256(out / name)} for name in names]
    assert manifest["inputs"] == [] and sorted(file.name for file in out.iterdir()) == [
        "counts_manifest.json", "raw_output_counts.csv", "raw_output_totals.csv"]


# --- the command line ---

def test_the_command_names_its_three_subcommands(capsys):
    """Without a subcommand, or with one that does not exist, the command ends with exit code 2 and says how it is called: by
    python, as the folder version has no python3."""
    for arguments in ([], ["pilot"]):
        error = refused(capsys, main, arguments)
        assert "usage: python -m aid.evalscripts.records" in error and "log" in error and "sheet" in error and "counts" in error
    with pytest.raises(SystemExit) as stopped:
        main(["log", "--help"])
    printed = capsys.readouterr().out
    assert stopped.value.code == 0 and "usage: python -m aid.evalscripts.records log" in printed
    for option in ("--out", "--events", "--timing", "--assignment", "--case-list", "--cases-root", "--dispositions", "--seq",
                   "--check-only", "--while-running"):
        assert option in printed


class FakeRawOutput:
    """What aid/evalscripts/raw_output.py is to the command: the arguments of sheet and counts, and their work."""

    def __init__(self, adds_out: bool, fails: Exception | None = None):
        self.adds_out, self.fails, self.calls = adds_out, fails, []

    def _arguments(self, name):
        def add(parser):
            self.calls.append((f"add_{name}_arguments", parser.prog))
            if self.adds_out:
                parser.add_argument("--out", type=Path, required=True)
            parser.add_argument("--set", required=True)
        return add

    def _work(self, name):
        def run(args, parser):
            self.calls.append((f"run_{name}", parser.prog, args.out, args.set))
            if self.fails is not None:
                raise self.fails
            return 0
        return run

    def __getattr__(self, name):
        kind, _, subcommand = name.partition("_")
        if kind == "add":
            return self._arguments(subcommand.removesuffix("_arguments"))
        if kind == "run":
            return self._work(subcommand)
        raise AttributeError(name)


@pytest.mark.parametrize("subcommand", ["sheet", "counts"])
@pytest.mark.parametrize("adds_out", [True, False])
def test_sheet_and_counts_are_the_work_of_raw_output(monkeypatch, tmp_path, subcommand, adds_out):
    """The command gives the subcommand its own parser, lets raw_output add its arguments and run. --out is required for every
    subcommand, whether raw_output adds the argument itself or leaves it to the command."""
    fake = FakeRawOutput(adds_out)
    monkeypatch.setitem(sys.modules, "aid.evalscripts.raw_output", fake)
    assert main([subcommand, "--out", str(tmp_path / "records"), "--set", "made-up"]) == 0
    prog = f"python -m aid.evalscripts.records {subcommand}"
    assert fake.calls == [(f"add_{subcommand}_arguments", prog), (f"run_{subcommand}", prog, tmp_path / "records", "made-up")]
    assert not (tmp_path / "records").exists()


@pytest.mark.parametrize("refusal", [Refusal("sheet.csv: save the file as CSV UTF-8"), case_runs.Refusal("case_list.csv does not exist")])
def test_a_refusal_of_a_subcommand_ends_the_command_with_its_sentence(monkeypatch, tmp_path, capsys, refusal):
    """Whichever module refuses an input, the command ends with exit code 2 and the sentence, and without a traceback."""
    monkeypatch.setitem(sys.modules, "aid.evalscripts.raw_output", FakeRawOutput(True, refusal))
    error = refused(capsys, main, ["counts", "--out", str(tmp_path / "records"), "--set", "made-up"])
    assert f"python -m aid.evalscripts.records counts: error: {refusal}" in error and "Traceback" not in error


# --- the timer's exports, as the tests write them ---

NINE = datetime(2026, 11, 12, 9, 0)                  # the clock of deps(): a time without offset, as the runner's
BASE = NINE.astimezone()                             # the same moment with its offset: the timer's clock is that of the same PC
CHECK_COLUMNS = ["case_id", "condition", "events", "log_complete", "start", "stop", "pauses", "pauses_closed", "waits", "waits_closed",
                 "consistent", "boundaries_unambiguous", "active_passive_apart", "hints", "problems"]
LOG_COLUMNS = ["case_id", "month", "band", "analysis_band", "supplier_code", "condition", "session", "block", "position", "timer_events",
               "started", "stopped", "active_seconds", "passive_seconds", "aid_runs_in_handling", "aid_runs_outside_handling",
               "aid_call_attempts", "aid_run_seconds", "aid_cost_eur", "aid_reader", "aid_status", "aid_state", "closed_by",
               "version_selected", "aid_version", "flags_raw", "review_items", "could_not_compare", "dispositions", "judgement_issues",
               "manual_fallback", "fallback_reason", "completed", "deviations", "remarks"]
SEQ_COLUMNS = ["seq_rating", "seq_recorded_at", "seq_status", "closed_by_rule", "seq_reason", "seq_note"]
AID_COLUMNS = LOG_COLUMNS[LOG_COLUMNS.index("aid_runs_in_handling"):LOG_COLUMNS.index("version_selected")] + [
    "aid_version", "flags_raw", "review_items", "could_not_compare", "fallback_reason"]
RUNNING = "the events export was taken while this case was running: export both files again after the stop"
RESEARCHERS = ("Criteria 2 and 3 (the boundaries of section 6.3 could be applied without ambiguity; active and passive time could be "
               "told apart) are the researcher's judgement: write yes or no in the columns boundaries_unambiguous and "
               "active_passive_apart of timer_check.csv.")


def at(seconds: float) -> str:
    """The timestamp the timer writes for the moment `seconds` after BASE: the clock of the PC in UTC, to the millisecond."""
    return (BASE + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def text_of(rows: list[list[str]]) -> str:
    """Rows as the text of a CSV file with commas and \\n line ends, as the timer writes its exports."""
    written = io.StringIO(newline="")
    csv.writer(written, lineterminator="\n").writerows(rows)
    return written.getvalue()


class Exports:
    """The two exports of a timer on which the test clicked: every click is a row of the events export, with the next event id,
    and a case that was stopped has its row in the timing export, with the seconds the test gives it."""

    def __init__(self):
        self.events, self.timing = [list(EVENT_COLUMNS)], [list(TIMING_COLUMNS)]

    def click(self, case_id: str, condition: str, event: str, seconds: float, *, session: str = "", ref: str = "", choice: str = "",
              note: str = "") -> str:
        """One click `seconds` after BASE; returns its event id."""
        event_id = f"E{len(self.events)}"
        mono = f"{(BASE + timedelta(seconds=seconds)).timestamp() * 1000:.1f}"
        self.events.append([event_id, case_id, condition, session, event, at(seconds), mono, "page-1", ref, choice, "", "", "", note])
        return event_id

    def handled(self, case_id: str, condition: str, *clicks: tuple[str, float], session: str = "", active: str | None = None,
                passive: str = "0.0", completed: str = "yes", fallback: str = "no", deviations: str = "") -> None:
        """The clicks of one case, each an event and its seconds after BASE; with `active`, its row of the timing export too."""
        for event, seconds in clicks:
            self.click(case_id, condition, event, seconds, session=session)
        if active is not None:
            self.timing.append([case_id, active, passive, completed, fallback, deviations, at(clicks[0][1])])

    def write(self, folder: Path, stamp: str = "2026-11-12-09-30") -> tuple[Path, Path]:
        """The two files as the timer names them, in the folder; returns the events file and the timing file."""
        folder.mkdir(parents=True, exist_ok=True)
        events, timing = folder / f"timer_events_{stamp}.csv", folder / f"timing_{stamp}.csv"
        events.write_text(text_of(self.events), encoding="utf-8", newline="")
        timing.write_text(text_of(self.timing), encoding="utf-8", newline="")
        return events, timing


def two_cases() -> Exports:
    """A manual case with a pause and an assisted case with a wait for the aid, both stopped as complete."""
    exports = Exports()
    exports.handled("P01", "manual", ("start", 0), ("pause", 60), ("resume", 90), ("complete", 150), active="120.0")
    exports.handled("P02", "assisted", ("start", 200), ("wait", 210), ("wait_end", 230), ("complete", 260), active="40.0", passive="20.0")
    return exports


def log(*arguments) -> int:
    """Run the subcommand log with these arguments."""
    return main(["log", *(str(argument) for argument in arguments)])


def rows_of(path: Path) -> dict[str, dict[str, str]]:
    """The rows of a record by case id."""
    return {row["case_id"]: row for row in read_csv(path)}


def without_times(printed: str) -> bool:
    """Whether a text of the terminal holds no time of day and no seconds: neither 09:02 nor 120.0. The sentence on the other two
    criteria names section 6.3 of the protocol, which is neither."""
    return not re.search(r"\d:\d\d|\d\.\d", printed.replace(RESEARCHERS, ""))


# --- log: the check of the timer log ---

def test_the_pilot_without_a_case_list_and_without_an_assignment(tmp_path, capsys):
    """The two exports alone give both records. The condition comes from the events, the rows stand by first start, the seconds
    are those of the timing row, and the columns of the aid stay blank without a remark, as nothing was said about the aid."""
    events, timing = two_cases().write(tmp_path / "downloads")
    out = tmp_path / "records"
    assert log("--out", out, "--events", events, "--timing", timing) == 0
    assert sorted(path.name for path in out.iterdir()) == ["case_log.csv", "log_manifest.json", "timer_check.csv"]
    check, logged = read_csv(out / "timer_check.csv"), read_csv(out / "case_log.csv")
    assert list(check[0]) == CHECK_COLUMNS and list(logged[0]) == LOG_COLUMNS
    assert check[0] == dict(zip(CHECK_COLUMNS, ["P01", "manual", "4", "yes", "yes", "yes", "1", "1", "0", "0", "yes", "", "", "", ""]))
    assert check[1] == dict(zip(CHECK_COLUMNS, ["P02", "assisted", "4", "yes", "yes", "yes", "0", "0", "1", "1", "yes", "", "", "", ""]))
    assert [(row["case_id"], row["condition"]) for row in logged] == [("P01", "manual"), ("P02", "assisted")]
    first, second = logged
    assert first["timer_events"] == f"start {at(0)}; pause {at(60)}; resume {at(90)}; complete {at(150)}"
    assert (first["started"], first["stopped"], first["active_seconds"], first["passive_seconds"]) == (at(0), at(150), "120.0", "0.0")
    assert (second["started"], second["stopped"], second["active_seconds"], second["passive_seconds"]) == (at(200), at(260), "40.0", "20.0")
    for row in logged:
        assert (row["manual_fallback"], row["completed"], row["deviations"], row["remarks"]) == ("no", "yes", "", "")
        assert all(row[column] == "" for column in AID_COLUMNS + ["month", "band", "analysis_band", "supplier_code", "session", "block",
                                                                 "position", "version_selected", "dispositions", "judgement_issues"])
    printed = capsys.readouterr().out
    assert "Criterion 1 of 3 (the timer log is complete): 2 of 2 cases" in printed and RESEARCHERS in printed
    assert "Cases: 1 manual, 1 assisted; in both conditions 0; with a wait 1; with a fallback 0; closed by rule 0" in printed
    assert "Not complete or not consistent" not in printed and "case_log.csv is not written" not in printed
    assert "judgement_issues 2" in printed and without_times(printed)


def test_the_records_are_plain_csv_files(tmp_path):
    """UTF-8, commas and \\n line ends on every system; and timer_check.csv, which is made after each session, holds no seconds
    and no time."""
    events, timing = two_cases().write(tmp_path / "downloads")
    assert log("--out", tmp_path / "records", "--events", events, "--timing", timing) == 0
    for name in ("timer_check.csv", "case_log.csv"):
        data = (tmp_path / "records" / name).read_bytes()
        assert b"\r" not in data and data.endswith(b"\n") and data.decode("utf-8").count("\n") == 3
    check = (tmp_path / "records" / "timer_check.csv").read_text(encoding="utf-8")
    assert check.startswith(",".join(CHECK_COLUMNS) + "\nP01,manual,4,yes,") and without_times(check)


def test_a_case_that_was_exported_while_it_ran_is_named_with_the_likely_cause(tmp_path, capsys):
    """The three criteria stay apart: the log of P02 is not complete, and the exit code is 0 all the same, as the records are
    written. The terminal names the case and its problem, and no time."""
    exports = two_cases()
    exports.events = exports.events[:-1]                                  # the export was taken before the stop of P02
    exports.timing = exports.timing[:-1]
    events, timing = exports.write(tmp_path / "downloads")
    assert log("--out", tmp_path / "records", "--events", events, "--timing", timing) == 0
    check = rows_of(tmp_path / "records" / "timer_check.csv")["P02"]
    assert (check["log_complete"], check["start"], check["stop"], check["consistent"]) == ("no", "yes", "no", "no")
    assert check["problems"] == RUNNING and (check["boundaries_unambiguous"], check["active_passive_apart"]) == ("", "")
    logged = rows_of(tmp_path / "records" / "case_log.csv")["P02"]
    assert (logged["started"], logged["stopped"], logged["active_seconds"], logged["passive_seconds"], logged["completed"]) == (
        at(200), "", "", "", "")
    printed = capsys.readouterr().out
    assert "Criterion 1 of 3 (the timer log is complete): 1 of 2 cases" in printed
    assert f"Not complete or not consistent:\n  P02 (not complete, not consistent): {RUNNING}" in printed and without_times(printed)


def test_without_a_timing_export_the_seconds_are_those_worked_out_from_the_events(tmp_path, capsys):
    """The log is complete, which is criterion 1; it cannot be called consistent without the timer's own figures, and the cause
    is said as what it is. The seconds are worked out from the events, with a remark."""
    events, _ = two_cases().write(tmp_path / "downloads")
    assert log("--out", tmp_path / "records", "--events", events) == 0
    check = rows_of(tmp_path / "records" / "timer_check.csv")["P02"]
    assert (check["log_complete"], check["consistent"]) == ("yes", "no")
    assert check["problems"] == "no timing export was given: give --timing, so that the seconds can be compared"
    logged = rows_of(tmp_path / "records" / "case_log.csv")["P02"]
    assert (logged["active_seconds"], logged["passive_seconds"]) == ("40.0", "20.0")
    assert logged["remarks"] == "seconds worked out from the events: no timing row agrees with them"
    assert "P02 (complete, not consistent): no timing export was given" in capsys.readouterr().out


def test_a_timing_export_alone_gives_cases_without_events(tmp_path):
    _, timing = two_cases().write(tmp_path / "downloads")
    assert log("--out", tmp_path / "records", "--timing", timing) == 0
    check = rows_of(tmp_path / "records" / "timer_check.csv")["P01"]
    assert (check["events"], check["log_complete"], check["consistent"]) == ("0", "no", "no")
    assert check["problems"] == "no events export was given: give --events, the timer log itself"


def test_the_exports_of_two_moments_are_merged_and_an_older_timing_row_is_superseded(tmp_path):
    """P01 was stopped as incomplete, exported, reopened as work and completed, and exported again: the events of both exports
    are one log, the timing row that agrees with it counts, and the older row is a hint and no problem."""
    first = Exports()
    first.handled("P01", "manual", ("start", 0), active="60.0", completed="no", deviations="incomplete: stopped by mistake")
    stop = first.click("P01", "manual", "incomplete", 60, note="stopped by mistake")
    second = Exports()
    second.events, second.timing = [row[:] for row in first.events], [list(TIMING_COLUMNS)]
    second.click("P01", "manual", "undo", 100, ref=stop, note="the case was not finished")
    second.click("P01", "manual", "recovery", 100, choice="work", note=f"reopened after {stop}; the time since counted as work")
    second.handled("P01", "manual", ("complete", 130), active="130.0")
    second.timing[-1][-1] = at(0)
    one, old = first.write(tmp_path / "downloads", "2026-11-12-09-05")
    two, new = second.write(tmp_path / "downloads", "2026-11-12-09-30")
    assert log("--out", tmp_path / "records", "--events", one, two, "--timing", old, new) == 0
    check = rows_of(tmp_path / "records" / "timer_check.csv")["P01"]
    assert (check["events"], check["log_complete"], check["consistent"], check["problems"]) == ("5", "yes", "yes", "")
    assert check["hints"] == ("1 timing row superseded by a later export; 1 recovery choice in the log (a reload or a reopening): the gap "
                              "was counted as the researcher chose; 1 click undone")
    logged = rows_of(tmp_path / "records" / "case_log.csv")["P01"]
    assert (logged["active_seconds"], logged["completed"]) == ("130.0", "yes")
    assert logged["timer_events"] == f"start {at(0)}; recovery {at(100)}; complete {at(130)}"


# --- log: the case list and the runs of the aid ---

def ticking(first: datetime, seconds: float):
    """A clock that gives `first` when it is asked the first time and a moment `seconds` later every time after."""
    asked = []

    def clock() -> datetime:
        asked.append(1)
        return first if len(asked) == 1 else first + timedelta(seconds=seconds)
    return clock


@pytest.fixture
def full(tmp_path, madeup_out) -> Deps:
    """The dependencies of a run on the made-up set, with the cases under tmp_path/data/cases and the store holding every made-up
    purchase order."""
    made = deps(tmp_path, madeup_out)
    return made.model_copy(update={"settings": made.settings.model_copy(update={"cases_root": tmp_path / "data" / "cases"})})


def run_at(full: Deps, document: Path, po_number: str | None, seconds: float, took: float = 0.0, store: PoStore | None = None) -> Case:
    """A case opened from the document `seconds` after NINE and run as the runner runs one: by a clock without offset, and with the
    aid's own decisions written to the disposition log of the folder. The run takes `took` seconds. With `store`, it reads its
    purchase orders from that store."""
    moment = NINE + timedelta(seconds=seconds)
    case = open_case(full.settings.cases_root, "confirmation", [document], po_number, moment)
    result = run_case(case, full.model_copy(update={"clock": ticking(moment, took)} | ({"store": store} if store is not None else {})))
    record_dispositions(case, result)
    return case


def case_list(tmp_path: Path, *rows: str, header: str = "case_id,po_number,kind,month") -> Path:
    """A case list with these rows under tmp_path/restricted."""
    path = tmp_path / "restricted" / "case_list.csv"
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def pilot(tmp_path, madeup_out, full) -> dict:
    """A pilot of five cases, timed and run on the made-up set. P01 is handled by hand, and the aid is run on its document long
    after (a replay). P02 to P05 are assisted: the aid closes P02 by its rules (it was also run the day before, while the aid was
    built), the buyer decides the one flag of P03 and closes it, no run is found for P04, and the run of P05 fails at reading, after
    which the researcher clicks the manual fallback. Returns the arguments of log and the case folders by case id."""
    exports = two_cases()
    exports.handled("P03", "assisted", ("start", 300), ("complete", 400), active="100.0")
    exports.handled("P04", "assisted", ("start", 500), ("complete", 560), active="60.0")
    exports.handled("P05", "assisted", ("start", 600), ("fallback", 650), ("complete", 700), active="100.0", fallback="yes")
    events, timing = exports.write(tmp_path / "downloads")
    built = run_at(full, madeup_out / "MU01_conf.pdf", "250101", -86400, took=2.0)
    second = run_at(full, madeup_out / "MU01_conf.pdf", "250101", 205, took=12.5)
    third = run_at(full, madeup_out / "MU07_conf.pdf", "250107", 305, took=3.0)
    decide(third, "RI1", "keep")
    assert close_case(third, full).status == "closed"
    fifth = run_at(full, madeup_out / "MU05_conf.pdf", "250105", 605, took=1.0)
    replay = run_at(full, madeup_out / "MU02_conf.pdf", "250102", 2000)
    listed = case_list(tmp_path, "P01,250102,confirmation,2026-09", "P02,250101,,2026-09", "P03,250107,,2026-10", "P04,250103,,2026-10",
                       "P05,250105,,2026-10")
    arguments = ["--events", events, "--timing", timing, "--case-list", listed, "--cases-root", full.settings.cases_root]
    return {"arguments": arguments, "folders": {"P01": [replay], "P02": [built, second], "P03": [third], "P05": [fifth]}}


def aid_cells(row: dict[str, str]) -> list[str]:
    """The cells of a row of the log that the case folders fill, in the order of AID_COLUMNS."""
    return [row[column] for column in AID_COLUMNS]


def test_the_log_of_a_pilot_with_a_case_list(pilot, tmp_path, capsys):
    """The runs of an assisted case that started between its start and its stop are its runs in the handling: the attempts, the
    seconds, the cost and the reader are theirs alone, and the run that counts among them gives the status, the version with its
    method and the numbers of the unreviewed output. The present state of its folder tells who closed the case."""
    out = tmp_path / "records"
    assert log("--out", out, *pilot["arguments"]) == 0
    logged = rows_of(out / "case_log.csv")
    assert list(logged) == ["P01", "P02", "P03", "P04", "P05"]
    assert [row["month"] for row in logged.values()] == ["2026-09"] * 2 + ["2026-10"] * 3
    assert aid_cells(logged["P02"]) == ["1", "1", "0", "12.5", "0", "parser", "closed", "closed_itself", "rule", "1 (reference)",
                                        "0", "0", "0", ""]
    assert logged["P02"]["remarks"] == "seconds by a clock without offset"
    assert aid_cells(logged["P03"]) == ["1", "0", "0", "3.0", "0", "parser", "review", "closed_by_you", "buyer", "1 (reference)",
                                        "1", "1", "0", ""]
    assert aid_cells(logged["P05"]) == ["1", "0", "0", "1.0", "0", "", "failed", "failed", "", "", "", "0", "", "the aid's run failed"]
    assert (logged["P05"]["manual_fallback"], logged["P05"]["completed"]) == ("yes", "yes")
    assert aid_cells(logged["P04"]) == [""] * len(AID_COLUMNS) and logged["P04"]["remarks"] == "assisted case: no run of the aid found"
    check = rows_of(out / "timer_check.csv")
    assert check["P02"]["hints"] == "closed by rule: the timer's stop for such a case is still to settle"
    assert check["P03"]["hints"] == "assisted case with a run of the aid and no wait event"
    assert check["P05"]["hints"] == "assisted case with a run of the aid and no wait event" and check["P04"]["hints"] == ""
    assert all(row["log_complete"] == "yes" and row["consistent"] == "yes" and row["problems"] == "" for row in check.values())
    printed = capsys.readouterr().out
    assert "Criterion 1 of 3 (the timer log is complete): 5 of 5 cases" in printed
    assert "Cases: 1 manual, 4 assisted; in both conditions 0; with a wait 1; with a fallback 1; closed by rule 1" in printed
    assert without_times(printed) and str(tmp_path / "data") not in printed


def test_a_manual_case_keeps_its_aid_columns_blank_and_a_run_after_the_handling_is_a_remark(pilot, tmp_path):
    """The raw output of a manual case is made by a replay, after the handling: nothing of it was shown to the researcher, so
    nothing of it stands in the log beside the handling."""
    out = tmp_path / "records"
    assert log("--out", out, *pilot["arguments"]) == 0
    manual = rows_of(out / "case_log.csv")["P01"]
    assert aid_cells(manual) == [""] * len(AID_COLUMNS) and manual["remarks"] == "run after the handling"
    assert (manual["version_selected"], manual["dispositions"]) == ("", "")
    assert rows_of(out / "timer_check.csv")["P01"]["hints"] == ""


def by_hand(root: Path, name: str, seconds: float, *steps: tuple[str, dict], result: CaseResult | None = None, po_number: str = "250301",
            sha256: str = "ab" * 32, closes: list[dict] | None = None, **more) -> Path:
    """A case folder written by hand under root, opened and run `seconds` after NINE by a clock without offset: its case.json, an
    audit record with these steps and the result's, the records of its closes and what else is given, the result as step 08
    and the records of its closes as step 09, as the aid keeps them in both. Returns the folder."""
    folder, moment = root / name, (NINE + timedelta(seconds=seconds)).isoformat()
    (folder / "steps").mkdir(parents=True)
    meta = {"case_id": name, "kind": "confirmation", "po_number": po_number, "opened_at": moment,
            "files": [{"name": "Offer_Example.pdf", "sha256": sha256, "bytes": 1}]}
    (folder / "case.json").write_text(json.dumps(meta), encoding="utf-8")
    payloads = [*steps, *([("result", result.model_dump(mode="json"))] if result is not None else [])]
    recorded = [{"name": step, "seconds": 0.0, "payload": payload} for step, payload in payloads]
    audit = {"started_at": moment, "finished_at": moment, "steps": recorded}
    (folder / "audit.json").write_text(json.dumps(audit | {"close": closes or []} | more), encoding="utf-8")
    if result is not None:
        (folder / "steps" / "08_result.json").write_text(result.model_dump_json(), encoding="utf-8")
    if closes is not None:
        (folder / "steps" / "09_close.json").write_text(json.dumps({"records": closes}), encoding="utf-8")
    return folder


WAITS = VersionChoice(version=None, method="none", reason=NO_STORED_VERSION, confidence=0.0)     # no version of the PO is stored


def one_assisted_case(tmp_path: Path, *clicks: tuple[str, float], fallback: str = "no") -> list:
    """The arguments of log for one assisted case P01 of the made-up order 250301, started at BASE and stopped 100 seconds later
    (or clicked as given), with its case list; the case folders are for the test to write under tmp_path/data/cases."""
    exports = Exports()
    exports.handled("P01", "assisted", *(clicks or (("start", 0), ("complete", 100))), active="100.0", fallback=fallback)
    events, timing = exports.write(tmp_path / "downloads")
    (tmp_path / "data" / "cases").mkdir(parents=True, exist_ok=True)
    listed = case_list(tmp_path, "P01,250301,,")
    return ["--events", events, "--timing", timing, "--case-list", listed, "--cases-root", tmp_path / "data" / "cases"]


def test_a_first_run_that_waited_for_its_purchase_order_does_not_count(tmp_path):
    """The aid opened a second folder for the same document once the order was filed and finished the first as replaced: both
    runs are of the handling, the second counts, and the remark says which run it is, never a folder. The model calls of both are
    the attempts and the cost of the case."""
    root = tmp_path / "data" / "cases"
    arguments = one_assisted_case(tmp_path, ("start", 0), ("fallback", 80), ("complete", 100), fallback="yes")
    waited = case_result("250301_a", "review").model_copy(update={"version": WAITS})
    reader = {"kind": "reader", "model": "made-up-model", "cost_eur": "0.0125", "seconds": 3.1, "attempts": 3}
    decider = {"kind": "decider", "source": "model:made-up-model", "cost_eur": "0.0005", "seconds": 1.0}
    read = ("read_document", {"reader": "llm:made-up:v1"})
    by_hand(root, "250301_a", 10, read, ("version", WAITS.model_dump(mode="json")), result=waited,
            closes=[{"action": "replaced", "at": "2026-11-12T09:00:40", "by": "250301_b"}], calls=[reader])
    by_hand(root, "250301_b", 40, read, result=case_result("250301_b", "review"),
            calls=[reader, decider, {"kind": "decider", "source": "none", "cost_eur": None}])
    out = tmp_path / "records"
    assert log("--out", out, *arguments) == 0
    row = rows_of(out / "case_log.csv")["P01"]
    assert aid_cells(row)[:9] == ["2", "0", "7", "0.0", "0.0255", "llm", "review", "review", ""]
    assert row["remarks"] == "seconds by a clock without offset; run 1 waited for its purchase order; run 2 of 2 in the handling counts"
    assert row["fallback_reason"] == ""                                   # the run that counts neither failed nor selected no version
    assert "250301" not in (out / "case_log.csv").read_text(encoding="utf-8") + (out / "timer_check.csv").read_text(encoding="utf-8")


def test_a_fallback_after_a_run_that_selected_no_version_says_why(tmp_path):
    root = tmp_path / "data" / "cases"
    arguments = one_assisted_case(tmp_path, ("start", 0), ("fallback", 80), ("complete", 100), fallback="yes")
    waited = case_result("250301_a", "review").model_copy(update={"version": WAITS})
    skipped = ("compare", {"skipped": "no version selected"})
    by_hand(root, "250301_a", 10, ("version", WAITS.model_dump(mode="json")), skipped, result=waited)
    assert log("--out", tmp_path / "records", *arguments) == 0
    row = rows_of(tmp_path / "records" / "case_log.csv")["P01"]
    assert (row["aid_version"], row["flags_raw"]) == ("none (none)", "")
    assert (row["manual_fallback"], row["fallback_reason"]) == ("yes", "no version selected")


def test_a_run_that_failed_is_no_reason_for_a_fallback_that_was_not_clicked(tmp_path):
    """The reason stands beside a manual fallback only: without the click the column stays blank, and the status says what the
    run did."""
    root = tmp_path / "data" / "cases"
    arguments = one_assisted_case(tmp_path)
    by_hand(root, "250301_a", 10, result=case_result("250301_a", "failed"))
    assert log("--out", tmp_path / "records", *arguments) == 0
    row = rows_of(tmp_path / "records" / "case_log.csv")["P01"]
    assert (row["aid_status"], row["manual_fallback"], row["fallback_reason"]) == ("failed", "no", "")


def test_a_run_of_before_the_handling_is_not_the_run_that_counts(tmp_path):
    """The case was run while the aid was built, and that run failed. The run of the handling is the one whose output the
    researcher saw: its status and its calls are the case's, and the earlier run is counted apart."""
    root = tmp_path / "data" / "cases"
    arguments = one_assisted_case(tmp_path)
    reader = {"kind": "reader", "model": "made-up-model", "cost_eur": "0.0125", "seconds": 3.1, "attempts": 2}
    by_hand(root, "250301_a", -3600, result=case_result("250301_a", "failed"), calls=[reader, reader])
    by_hand(root, "250301_b", 10, ("read_document", {"reader": "llm:made-up:v1"}), result=case_result("250301_b", "closed"), calls=[reader])
    assert log("--out", tmp_path / "records", *arguments) == 0
    row = rows_of(tmp_path / "records" / "case_log.csv")["P01"]
    assert aid_cells(row)[:7] == ["1", "1", "2", "0.0", "0.0125", "llm", "closed"] and row["remarks"] == "seconds by a clock without offset"


def test_two_documents_for_one_purchase_order_leave_the_aid_columns_blank_with_that_remark(tmp_path):
    """The command does not guess which document is the case: the case list has to name the folder."""
    root = tmp_path / "data" / "cases"
    arguments = one_assisted_case(tmp_path)
    by_hand(root, "250301_a", 10, result=case_result("250301_a", "closed"))
    by_hand(root, "250301_b", 20, result=case_result("250301_b", "closed"), sha256="cd" * 32)
    assert log("--out", tmp_path / "records", *arguments) == 0
    row = rows_of(tmp_path / "records" / "case_log.csv")["P01"]
    assert aid_cells(row) == [""] * len(AID_COLUMNS)
    assert row["remarks"] == "2 documents for this purchase order: name the folder in the case list"


def test_an_assisted_case_whose_only_run_lies_outside_its_handling_says_so(tmp_path):
    """A run that started before the start of the timer, or after its stop, is no run of the handling: it is counted apart and
    nothing of it is summed or shown."""
    root = tmp_path / "data" / "cases"
    arguments = one_assisted_case(tmp_path)
    by_hand(root, "250301_a", -30, ("read_document", {"reader": "parser:table_nl"}), result=case_result("250301_a", "closed"))
    by_hand(root, "250301_b", 101, ("read_document", {"reader": "parser:table_nl"}), result=case_result("250301_b", "closed"))
    assert log("--out", tmp_path / "records", *arguments) == 0
    row = rows_of(tmp_path / "records" / "case_log.csv")["P01"]
    assert aid_cells(row) == ["0", "2"] + [""] * (len(AID_COLUMNS) - 2)
    assert row["remarks"] == "assisted case: no run of the aid in the handling"


def test_a_folder_run_again_in_place_gives_no_output_and_says_so(tmp_path):
    """The records of its closes are not in its audit record: what the record holds is not the first output of the folder."""
    root = tmp_path / "data" / "cases"
    arguments = one_assisted_case(tmp_path)
    closed = [{"action": "close", "at": "2026-11-12T09:01:00"}]
    by_hand(root, "250301_a", 10, result=case_result("250301_a", "review"), closes=closed, close=[])
    assert log("--out", tmp_path / "records", *arguments) == 0
    row = rows_of(tmp_path / "records" / "case_log.csv")["P01"]
    assert (row["aid_runs_in_handling"], row["aid_status"], row["aid_version"], row["flags_raw"]) == ("1", "", "", "")
    assert row["remarks"] == ("run again in the same folder: the figures are those of the last run; seconds by a clock without offset; "
                              "run again in the same folder (or no audit record): the first run's output is not kept")


def test_a_timer_case_outside_the_case_list_and_a_listed_case_without_a_timer_record(tmp_path, capsys):
    """A case that the timer timed and the case list does not hold, and a listed case without events: two rows, and each says
    what it lacks. A case without any timer record has no verdict, and is not counted in the verdict on criterion 1."""
    root = tmp_path / "data" / "cases"
    arguments = one_assisted_case(tmp_path)
    by_hand(root, "250301_a", 10, result=case_result("250301_a", "closed"))
    arguments[arguments.index("--case-list") + 1] = case_list(tmp_path, "P09,250301,,")
    assert log("--out", tmp_path / "records", *arguments) == 0
    logged, check = rows_of(tmp_path / "records" / "case_log.csv"), rows_of(tmp_path / "records" / "timer_check.csv")
    assert list(logged) == ["P01", "P09"]                                  # by first start; a case without a start stands last
    assert aid_cells(logged["P01"]) == [""] * len(AID_COLUMNS) and logged["P01"]["remarks"] == "not in the case list"
    assert aid_cells(logged["P09"])[:2] == ["", ""] and logged["P09"]["started"] == ""     # nothing to place its run in
    assert logged["P09"]["remarks"] == "no start and stop of the timer to place the runs of the aid in"
    verdict = ("events", "log_complete", "start", "stop", "consistent", "problems")
    assert [check["P09"][column] for column in verdict] == ["0", "", "", "", "", ""]
    assert check["P09"]["hints"] == "no timer record: not handled yet, or its exports were not given"
    printed = capsys.readouterr().out
    assert "Criterion 1 of 3 (the timer log is complete): 1 of 1 cases" in printed and "without a timer record 1" in printed


# --- log: the assignment, and what is withheld until handling is finished ---

ASSIGNMENT_HEADER = "order,session,block,orientation,case_id,lines,band,supplier_group,condition"       # evaluation/randomise.py
NOT_FINISHED = "handling is not finished: case_log.csv is not written"


def assignment(tmp_path: Path, *rows: str, header: str = ASSIGNMENT_HEADER, encoding: str = "utf-8") -> Path:
    """An assignment with these rows under tmp_path."""
    path = tmp_path / "assignment.csv"
    path.write_bytes(("\n".join([header, *rows]) + "\n").encode(encoding))
    return path


def fourteen_cases(tmp_path: Path, handled: int = 14) -> tuple[Path, Path, Path]:
    """An assignment of fourteen cases, C01 to C14, handled in the order C14 to C01, and the timer's exports after the first
    `handled` of them were handled, each in a minute. Band 1 holds one case per condition; the other two bands hold three."""
    bands = ["1"] * 2 + ["2 to 3"] * 6 + ["4 or more"] * 6
    rows, exports = [], Exports()
    for order in range(1, 15):
        number = 15 - order
        case_id, condition, session = f"C{number:02d}", ("manual", "assisted")[number % 2], str((order - 1) // 8 + 1)
        rows.append(f"{order},{session},{(order - 1) // 4 + 1},MAAM,{case_id},{number},{bands[number - 1]},G{number % 3},{condition}")
        if order <= handled:
            exports.handled(case_id, condition, ("start", order * 100), ("complete", order * 100 + 60), session=session, active="60.0")
    return (assignment(tmp_path, *rows), *exports.write(tmp_path / "downloads"))


def test_with_an_assignment_the_rows_stand_in_the_handling_order_with_what_it_planned(tmp_path, capsys):
    """Band, supplier code, session and block are the assignment's; the position is the place in the session of eight; and the
    analysis band follows the sparse-band rule on the allocation counts: band 1, with one case per condition, joins the middle
    band."""
    planned, events, timing = fourteen_cases(tmp_path)
    out = tmp_path / "records"
    assert log("--out", out, "--assignment", planned, "--events", events, "--timing", timing) == 0
    logged = read_csv(out / "case_log.csv")
    assert [row["case_id"] for row in logged] == [f"C{number:02d}" for number in range(14, 0, -1)]
    assert [row["position"] for row in logged] == ["1", "2", "3", "4", "5", "6", "7", "8", "1", "2", "3", "4", "5", "6"]
    assert [row["session"] for row in logged] == ["1"] * 8 + ["2"] * 6
    assert [row["block"] for row in logged] == ["1"] * 4 + ["2"] * 4 + ["3"] * 4 + ["4"] * 2
    assert [row["condition"] for row in logged][:3] == ["manual", "assisted", "manual"] and logged[0]["supplier_code"] == "G2"
    bands = [("4 or more", "4 or more")] * 6 + [("2 to 3", "2 to 3")] * 6 + [("1", "2 to 3")] * 2
    assert [(row["band"], row["analysis_band"]) for row in logged] == bands
    assert sorted(path.name for path in out.iterdir()) == ["case_log.csv", "log_manifest.json", "timer_check.csv"]
    assert NOT_FINISHED not in capsys.readouterr().out


def test_while_an_assigned_case_has_no_stop_the_log_is_not_written(tmp_path, capsys):
    """The log is the first file that sets the seconds beside the condition, and the protocol looks at neither until all timed
    handling is finished. After a session the check alone is written: it holds no seconds and no time."""
    planned, events, timing = fourteen_cases(tmp_path, handled=8)
    out = tmp_path / "records"
    assert log("--out", out, "--assignment", planned, "--events", events, "--timing", timing) == 0
    assert sorted(path.name for path in out.iterdir()) == ["log_manifest.json", "timer_check.csv"]
    check = read_csv(out / "timer_check.csv")
    assert [row["case_id"] for row in check] == [f"C{number:02d}" for number in range(14, 0, -1)]
    assert [row["log_complete"] for row in check] == ["yes"] * 8 + [""] * 6 and check[8]["condition"] == "manual"
    assert check[8]["hints"] == "no timer record: not handled yet, or its exports were not given"
    assert without_times((out / "timer_check.csv").read_text(encoding="utf-8"))
    printed = capsys.readouterr().out
    assert NOT_FINISHED in printed and "Criterion 1 of 3 (the timer log is complete): 8 of 8 cases" in printed
    assert "without a timer record 6" in printed and "case_log.csv" not in printed.replace(NOT_FINISHED, "") and without_times(printed)
    manifest = json.loads((out / "log_manifest.json").read_bytes())
    assert [file["name"] for file in manifest["outputs"]] == ["timer_check.csv"] and manifest["cases"] == 14


def test_an_assigned_case_that_was_reopened_keeps_the_log_back(tmp_path, capsys):
    """Every assigned case has events, but the stop of the last was cancelled: it has no effective stop."""
    planned, events, timing = fourteen_cases(tmp_path)
    rows = events.read_text(encoding="utf-8").splitlines()
    cells = rows[-1].split(",")
    undo = ["E99", "C01", cells[2], cells[3], "undo", at(1500), cells[6], "page-1", cells[0], "", "", "", "", "stopped too early"]
    events.write_text("\n".join([*rows, ",".join(undo)]) + "\n", encoding="utf-8")
    assert log("--out", tmp_path / "records", "--assignment", planned, "--events", events, "--timing", timing) == 0
    assert not (tmp_path / "records" / "case_log.csv").exists() and NOT_FINISHED in capsys.readouterr().out


def test_check_only_writes_the_check_alone(tmp_path, capsys):
    """For a run after each session, also when every case has its stop: the pilot, or the last session."""
    events, timing = two_cases().write(tmp_path / "downloads")
    out = tmp_path / "records"
    assert log("--out", out, "--events", events, "--timing", timing, "--check-only") == 0
    assert sorted(path.name for path in out.iterdir()) == ["log_manifest.json", "timer_check.csv"]
    manifest = json.loads((out / "log_manifest.json").read_bytes())
    assert manifest["options"]["check_only"] is True and [file["name"] for file in manifest["outputs"]] == ["timer_check.csv"]
    printed = capsys.readouterr().out
    assert "Criterion 1 of 3 (the timer log is complete): 2 of 2 cases" in printed and NOT_FINISHED not in printed


def test_a_condition_that_differs_from_the_assignment_is_a_problem_of_the_check(tmp_path, capsys):
    """The condition of the log is the assignment's. Events in another condition, and a case that the assignment does not hold,
    are problems of the check, which then cannot call the log consistent; they change nothing in whether it is complete."""
    exports = two_cases()
    events, timing = exports.write(tmp_path / "downloads")
    planned = assignment(tmp_path, "1,1,1,AM,P02,2,2 to 3,G1,manual")
    assert log("--out", tmp_path / "records", "--assignment", planned, "--events", events, "--timing", timing) == 0
    check, logged = read_csv(tmp_path / "records" / "timer_check.csv"), read_csv(tmp_path / "records" / "case_log.csv")
    assert [(row["case_id"], row["condition"], row["log_complete"], row["consistent"]) for row in check] == [
        ("P02", "manual", "yes", "no"), ("P01", "manual", "yes", "no")]
    assert check[0]["problems"] == "the events carry the condition assisted where the assignment gives manual"
    assert check[1]["problems"] == "not in the assignment"
    assert [(row["case_id"], row["condition"], row["band"]) for row in logged] == [("P02", "manual", "2 to 3"), ("P01", "manual", "")]
    printed = capsys.readouterr().out
    assert "in both conditions 1" in printed and "P02 (complete, not consistent): the events carry the condition assisted" in printed


@pytest.mark.parametrize("header,row,sentence", [
    ("order,session,block,case_id,band,condition", "1,1,1,C01,1,manual", "is not an assignment: it lacks the column supplier_group"),
    (ASSIGNMENT_HEADER, "first,1,1,MA,C01,1,1,G1,manual", "row 2: order is not a whole number from 1"),
    (ASSIGNMENT_HEADER, "1,1,1,MA,C01,1,1,G1,manual\n1,1,1,MA,C02,1,1,G1,assisted", "row 3: the order 1 stands twice"),
    (ASSIGNMENT_HEADER, "1,1,1,MA,C01,1,1,G1,manual\n2,1,1,MA,C01,1,1,G1,assisted", "row 3: the case id C01 stands twice"),
    (ASSIGNMENT_HEADER, "1,1,1,MA,,1,1,G1,manual", "row 2: no case id"),
    (ASSIGNMENT_HEADER, "1,1,1,MA,C01,1,1,G1,by hand", "row 2: the condition is neither manual nor assisted"),
    (ASSIGNMENT_HEADER, "1,1,1,MA,C01,1,2-3,G1,manual", "row 2: the band is not one of 1, 2 to 3, 4 or more"),
])
def test_an_assignment_that_is_not_the_frozen_one_is_refused(tmp_path, capsys, header, row, sentence):
    """A band that a spreadsheet turned into a date, an order typed over: the assignment is the output of the frozen script, and
    a file that is not is refused with the row and the column, before anything is written."""
    events, timing = two_cases().write(tmp_path / "downloads")
    planned = assignment(tmp_path, row, header=header)
    error = refused(capsys, log, "--out", tmp_path / "records", "--assignment", planned, "--events", events, "--timing", timing)
    assert f"error: --assignment: assignment.csv{', ' if sentence.startswith('row') else ' '}{sentence}" in error
    assert not (tmp_path / "records").exists()


@pytest.mark.parametrize("encoding", ["utf-8-sig", "cp1252"])
def test_an_assignment_saved_by_a_spreadsheet_is_read(tmp_path, encoding):
    """Semicolons, a byte order mark or the Windows code page, and spaces around a cell."""
    events, timing = two_cases().write(tmp_path / "downloads")
    planned = assignment(tmp_path, "1;1;1;AM;P02;2;2 to 3;Gro\u00dfhandel ;assisted", "2;1;1;AM; P01;1;1;G2;manual",
                         header=ASSIGNMENT_HEADER.replace(",", ";"), encoding=encoding)
    assert log("--out", tmp_path / "records", "--assignment", planned, "--events", events, "--timing", timing) == 0
    logged = read_csv(tmp_path / "records" / "case_log.csv")
    codes = [(row["case_id"], row["supplier_code"], row["analysis_band"]) for row in logged]
    assert codes == [("P02", "Gro\u00dfhandel", "all"), ("P01", "G2", "all")]


# --- the analysis band ---

def counts_of(*numbers: int) -> dict[str, dict[str, int]]:
    """The allocation counts per band and condition: manual and assisted of band 1, of 2 to 3, and of 4 or more."""
    return {band: {"manual": numbers[2 * index], "assisted": numbers[2 * index + 1]} for index, band in enumerate(records.BANDS)}


@pytest.mark.parametrize("numbers,merged", [
    ((3, 3, 3, 3, 3, 3), ("1", "2 to 3", "4 or more")),
    ((2, 5, 3, 3, 3, 3), ("2 to 3", "2 to 3", "4 or more")),               # fewer than three in either condition is sparse
    ((3, 3, 3, 3, 9, 0), ("1", "2 to 3", "2 to 3")),
    ((1, 1, 9, 9, 2, 2), ("all", "all", "all")),                           # both outer bands sparse
    ((3, 3, 2, 3, 3, 3), ("all", "all", "all")),                           # the middle band itself is sparse
    ((2, 0, 0, 3, 3, 3), ("all", "all", "all")),                           # still sparse after merging
    ((2, 1, 1, 2, 3, 3), ("2 to 3", "2 to 3", "4 or more")),               # sparse alone, three per condition together
])
def test_the_sparse_band_rule_of_protocol_section_9_2(numbers, merged):
    """A band with fewer than three cases in either condition is merged with the 2-to-3-line band; if both outer bands are
    sparse, or any band is still sparse after merging, all bands are combined."""
    assert records.analysis_bands(counts_of(*numbers)) == dict(zip(records.BANDS, merged))


def test_the_analysis_band_is_the_one_the_analysis_script_gives():
    """evaluation/analyse.py holds the rule the estimates are made by (sparse_merge). The aid's own repository does not hold that
    file, so the rule is written again in the records; where the file is present the two are compared on every allocation of
    up to four cases per cell."""
    script = REPO_ROOT / "evaluation" / "analyse.py"
    if not script.is_file():
        pytest.skip("evaluation/analyse.py is not in this repository")

    def assigned(path: Path, name: str):
        """The statement of a script that gives the name its value: an assignment, or the definition of a function."""
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            names = [target.id for target in node.targets if isinstance(target, ast.Name)] if isinstance(node, ast.Assign) else [
                node.name] if isinstance(node, ast.FunctionDef) else []
            if name in names:
                return node
        raise LookupError(name)
    theirs = {"BANDS": ast.literal_eval(assigned(script.with_name("randomise.py"), "BANDS").value),
              "CONDS": ast.literal_eval(assigned(script, "CONDS").value)}
    assert (theirs["BANDS"], theirs["CONDS"]) == (records.BANDS, records.CONDITIONS)
    exec(compile(ast.Module([assigned(script, "sparse_merge")], []), str(script), "exec"), theirs)
    for numbers in itertools.product(range(5), repeat=6):
        assert records.analysis_bands(counts_of(*numbers)) == theirs["sparse_merge"](counts_of(*numbers)), numbers


# --- log: the disposition log ---

DISPOSITION_HEADER = "case_id,version_selected,line_id,field,decision,source,note"                   # evaluation/templates/dispositions.csv


def kept_by_hand(tmp_path: Path, *rows: str, name: str = "dispositions_manual.csv", header: str = DISPOSITION_HEADER) -> Path:
    """A disposition log kept by hand, with these rows, under tmp_path/restricted."""
    path = tmp_path / "restricted" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def test_the_dispositions_of_an_assisted_case_are_the_rows_its_runs_in_the_handling_wrote(pilot, tmp_path):
    """The aid keeps its disposition log in the case folder, and the case_id column of that file holds the name of the folder: the
    rows are counted whatever that column holds, and none of their cells is copied. The version selected is the one the
    disposition log names. The rows of the run of the day before are not of this handling."""
    out = tmp_path / "records"
    assert log("--out", out, *pilot["arguments"]) == 0
    logged = rows_of(out / "case_log.csv")
    own = {case_id: [read_csv(folder.root / "dispositions.csv") for folder in folders] for case_id, folders in pilot["folders"].items()}
    assert [len(rows) for rows in own["P02"]] == [2, 2] and own["P03"][0][0]["case_id"] == pilot["folders"]["P03"][0].case_id
    assert (logged["P02"]["dispositions"], logged["P02"]["version_selected"]) == ("2", "1")
    assert (logged["P03"]["dispositions"], logged["P03"]["version_selected"]) == (str(len(own["P03"][0])), "1") and len(own["P03"][0]) >= 1
    assert (logged["P05"]["dispositions"], logged["P05"]["version_selected"]) == ("0", "")           # the failed run decided nothing
    assert (logged["P04"]["dispositions"], logged["P01"]["dispositions"]) == ("", "")                # no log of these cases was given
    everything = "".join(path.read_text(encoding="utf-8") for path in out.iterdir())
    assert "folder 2501" not in everything and "prepared (R01)" not in everything


def test_a_disposition_log_kept_by_hand_is_counted_by_the_case_id_or_by_a_folder_of_the_case(pilot, tmp_path):
    """The manual condition has no log of the aid: the researcher gives the file. Its rows count for the case whose id they
    carry; a row that carries the name of a folder of an assisted case counts for that case. A copy of a file that a case folder
    holds is not counted a second time, and the replay of a manual case adds nothing."""
    third = pilot["folders"]["P03"][0]
    by_id = kept_by_hand(tmp_path, "P01,2,1,price,accept,page 1 line 8,",
                         "P01,2,2,delivery_date,ask the supplier,page 1 line 9,asked by phone", "P04,1,1,price,accept,,",
                         f"{third.case_id},1,2,quantity,accept,,typed in afterwards", "P99,1,1,price,accept,,",
                         f"{pilot['folders']['P01'][0].case_id},1,,set_status,prepared (R01),R01,the replay of the manual case")
    copy = tmp_path / "restricted" / "copy_of_the_aids_log.csv"
    copy.write_bytes((third.root / "dispositions.csv").read_bytes())
    out = tmp_path / "records"
    assert log("--out", out, *pilot["arguments"], "--dispositions", by_id, copy) == 0
    logged = rows_of(out / "case_log.csv")
    assert (logged["P01"]["dispositions"], logged["P01"]["version_selected"]) == ("2", "2")
    assert (logged["P04"]["dispositions"], logged["P04"]["version_selected"]) == ("1", "1")
    assert logged["P03"]["dispositions"] == str(len(read_csv(third.root / "dispositions.csv")) + 1)
    assert (logged["P02"]["dispositions"], logged["P05"]["dispositions"]) == ("2", "0")
    everything = "".join(path.read_text(encoding="utf-8") for path in out.iterdir())
    assert "asked by phone" not in everything and "page 1 line 8" not in everything and third.case_id not in everything
    manifest = json.loads((out / "log_manifest.json").read_bytes())
    assert manifest["options"]["dispositions"] == ["dispositions_manual.csv", "copy_of_the_aids_log.csv"]
    assert {"name": "dispositions_manual.csv", "sha256": sha256(by_id)} in manifest["inputs"]


def test_a_version_in_the_disposition_log_that_is_no_number_is_not_copied(tmp_path):
    """No cell of the disposition log goes into the log but the version, and that only as a whole number. Two versions in the rows
    of one case are both shown."""
    events, timing = two_cases().write(tmp_path / "downloads")
    by_id = kept_by_hand(tmp_path, "P01,1,1,price,accept,,", "P01,2,2,price,accept,,", "P01,2,3,price,accept,,",
                         "P02,the second one,1,price,accept,,")
    assert log("--out", tmp_path / "records", "--events", events, "--timing", timing, "--dispositions", by_id) == 0
    logged = rows_of(tmp_path / "records" / "case_log.csv")
    assert (logged["P01"]["dispositions"], logged["P01"]["version_selected"], logged["P01"]["remarks"]) == ("3", "1; 2", "")
    assert (logged["P02"]["dispositions"], logged["P02"]["version_selected"]) == ("1", "")
    assert logged["P02"]["remarks"] == "a version_selected of the disposition log is not a whole number: not copied"


# --- log: the SEQ record, and the text the researcher typed ---

SEQ_HEADER = "case_id,seq_rating,recorded_at,administration_status,closed_by_rule,reason,note"       # evaluation/templates/seq_ratings.csv


def seq_record(tmp_path: Path, *rows: str, header: str = SEQ_HEADER, encoding: str = "utf-8") -> Path:
    """A SEQ record with these rows under tmp_path/restricted."""
    path = tmp_path / "restricted" / "seq_ratings.csv"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(("\n".join([header, *rows]) + "\n").encode(encoding))
    return path


def test_the_seq_record_stands_beside_each_case_and_is_compared_with_who_closed_it(pilot, tmp_path, capsys):
    """Six more columns, from the row of the SEQ record with the case id. Whether the aid closed the case by its rules is said
    twice, by the researcher in that record and by the case folder: a disagreement is a remark. A row of the record that belongs to
    no case of the log is named on the terminal."""
    stamp = "2026-11-12T09:05:00+01:00"
    seq = seq_record(tmp_path, f"P01,6,{stamp},immediate,,,", f"P02,7,{stamp},immediate,no,,nothing to review",
                     f"P03,3,{stamp},late,no,rated after the next case,the flag was hard to place", "P04,,,missing,,forgotten,",
                     f"P05,2,{stamp},immediate,yes,,", f"P77,5,{stamp},immediate,,,")
    out = tmp_path / "records"
    assert log("--out", out, *pilot["arguments"], "--seq", seq) == 0
    rows = read_csv(out / "case_log.csv")
    assert list(rows[0]) == LOG_COLUMNS + SEQ_COLUMNS
    logged = {row["case_id"]: row for row in rows}
    assert [logged["P03"][column] for column in SEQ_COLUMNS] == ["3", stamp, "late", "no", "rated after the next case",
                                                                 "the flag was hard to place"]
    assert [logged["P04"][column] for column in SEQ_COLUMNS] == ["", "", "missing", "", "forgotten", ""]
    assert "the SEQ record says closed_by_rule no; the case folder says closed by rule" in logged["P02"]["remarks"]
    assert "the SEQ record says closed_by_rule yes; the case folder says not closed" in logged["P05"]["remarks"]
    assert "SEQ" not in logged["P03"]["remarks"] and "SEQ" not in logged["P01"]["remarks"]            # agreed; and nothing to compare
    assert "1 row of the SEQ record belongs to no case of the log: P77" in capsys.readouterr().out


@pytest.mark.parametrize("header,rows,sentence", [
    ("case_id,seq_rating,recorded_at,administration_status,reason,note", ["P01,6,,immediate,,"],
     "seq_ratings.csv is not a SEQ record: it lacks the column closed_by_rule"),
    (SEQ_HEADER, ["P01,6,,immediate,,,", "P01,5,,late,,,"], "seq_ratings.csv, row 3: the case id P01 stands twice"),
    (SEQ_HEADER, [",6,,immediate,,,"], "seq_ratings.csv, row 2: no case id"),
])
def test_a_seq_record_that_is_not_one_row_per_case_is_refused(tmp_path, capsys, header, rows, sentence):
    events, timing = two_cases().write(tmp_path / "downloads")
    seq = seq_record(tmp_path, *rows, header=header)
    error = refused(capsys, log, "--out", tmp_path / "records", "--events", events, "--timing", timing, "--seq", seq)
    assert f"error: --seq: {sentence}" in error and not (tmp_path / "records").exists()


def test_typed_text_that_holds_identifying_text_is_withheld(pilot, tmp_path, capsys):
    """What the researcher typed into the timer and the SEQ record goes into the log, which is an anonymised record: a text that
    names a purchase order of the case list, a case folder, a document or a supplier of a run is written as [withheld], and the
    remarks name its column. A text that names none is copied."""
    arguments = pilot["arguments"]
    timing = Path(arguments[arguments.index("--timing") + 1])
    lines = timing.read_text(encoding="utf-8").splitlines()
    lines[2] = lines[2].replace(",yes,no,,", ",yes,no,order 250101 came in late,")
    lines[3] = lines[3].replace(",yes,no,,", ",yes,no,paused for a phone call,")
    timing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    folder = pilot["folders"]["P03"][0].case_id
    seq = seq_record(tmp_path, "P02,7,,immediate,yes,see MU01_conf.pdf,", f"P03,3,,immediate,no,,opened {folder} twice",
                     "P05,2,,immediate,no,,S05 sent a scan")
    out = tmp_path / "records"
    assert log("--out", out, *arguments, "--seq", seq) == 0
    logged = rows_of(out / "case_log.csv")
    assert (logged["P02"]["deviations"], logged["P02"]["seq_reason"]) == ("[withheld]", "[withheld]")
    assert "withheld for identifying text: deviations, seq_reason" in logged["P02"]["remarks"]
    assert (logged["P03"]["deviations"], logged["P03"]["seq_note"]) == ("paused for a phone call", "[withheld]")
    assert "withheld for identifying text: seq_note" in logged["P03"]["remarks"] and logged["P05"]["seq_note"] == "[withheld]"
    everything = "".join(path.read_text(encoding="utf-8") for path in out.iterdir())
    for identifying in ("250101", "250107", "MU01", "S05", folder):
        assert identifying not in everything
    assert "4 cells of typed text hold identifying text and were withheld" in capsys.readouterr().out


def test_without_a_case_list_typed_text_is_copied_and_the_terminal_says_that_it_was_not_checked(tmp_path, capsys):
    """Nothing is known to check a text against. Without a timing row that counts, the deviations are the notes of the events."""
    exports = Exports()
    exports.click("P01", "manual", "start", 0, note="deviation from the order: the first case was not ready")
    exports.click("P01", "manual", "incomplete", 60, note="the confirmation cannot be read")
    events, _ = exports.write(tmp_path / "downloads")
    assert log("--out", tmp_path / "records", "--events", events) == 0
    logged = rows_of(tmp_path / "records" / "case_log.csv")["P01"]
    assert logged["deviations"] == "deviation from the order: the first case was not ready; the confirmation cannot be read"
    assert logged["completed"] == "no"
    assert logged["remarks"].endswith("; not checked for identifying text, as no case list was given: deviations")
    assert "Typed text was copied without a check for identifying text: no case list was given." in capsys.readouterr().out


def test_without_a_case_list_the_deviations_of_a_timing_row_are_marked_as_not_checked(tmp_path, capsys):
    exports = two_cases()
    exports.timing[1][5] = "same issue as on PO 250102 and MU05_conf.pdf"
    events, timing = exports.write(tmp_path / "downloads")
    assert log("--out", tmp_path / "records", "--events", events, "--timing", timing) == 0
    logged = rows_of(tmp_path / "records" / "case_log.csv")
    assert logged["P01"]["remarks"] == "not checked for identifying text, as no case list was given: deviations"
    assert logged["P02"]["remarks"] == ""
    assert "Typed text was copied without a check for identifying text: no case list was given." in capsys.readouterr().out


def test_a_file_whose_name_holds_identifying_text_is_not_named_in_the_manifest(pilot, tmp_path):
    """A disposition log that is named after a case folder: the manifest keeps the sha256 and withholds the name."""
    folder = pilot["folders"]["P03"][0]
    named = kept_by_hand(tmp_path, "P01,2,1,price,accept,,", name=f"{folder.case_id}_dispositions.csv")
    out = tmp_path / "records"
    assert log("--out", out, *pilot["arguments"], "--dispositions", named) == 0
    manifest = json.loads((out / "log_manifest.json").read_bytes())
    assert manifest["options"]["dispositions"] == ["[withheld]"] and {"name": "[withheld]", "sha256": sha256(named)} in manifest["inputs"]
    assert "2501" not in (out / "log_manifest.json").read_text(encoding="utf-8")


# --- log: the refusals, and reading only ---

def data_of(pilot: dict) -> Path:
    """The data folder of the pilot, as the aid leaves one: the settings file beside the cases root."""
    data = Path(pilot["arguments"][-1]).parent
    (data / "settings.json").write_text("{}", encoding="utf-8")
    return data


def heartbeat(data: Path, age: float) -> None:
    """The state file of a launcher on the data folder whose last heartbeat is `age` seconds old."""
    launcher = {"pid": 4242, "started": "2026-11-12T08:00:00", "heartbeat": time.time() - age, "heartbeat_at": "2026-11-12T09:00:00"}
    (data / start.STATE_FILE).write_text(json.dumps({"launcher": launcher, "parts": {}}), encoding="utf-8")


REFUSALS = {
    "no input at all": (lambda a, t: [],
                        "log needs at least one of --events, --timing, --assignment and --case-list: give the timer's exports"),
    "a disposition log alone": (lambda a, t: ["--dispositions", a[1], "--seq", a[3]], "log needs at least one of --events, --timing"),
    "a case list without a cases root": (lambda a, t: a[:6],
                                         "--case-list needs --cases-root: give the cases folder of the aid's data folder"),
    "a cases root without a case list": (lambda a, t: a[:4] + a[6:],
                                         "--cases-root needs --case-list: give the key between the case ids and the purchase orders"),
    "a cases root that is not there": (lambda a, t: a[:7] + [t / "data" / "casus"], "--cases-root is not a folder that is there"),
    "an input file that is not there": (lambda a, t: ["--events", t / "downloads" / "timer_events_2026.csv"],
                                        "--events: timer_events_2026.csv cannot be read: it does not exist"),
    "a file without its columns": (lambda a, t: ["--events", a[3]], "--events: timing_2026-11-12-09-30.csv is not an events export of "
                                                                    "the timer: it lacks the columns event_id,"),
    "a pattern that matches nothing": (lambda a, t: a[:2] + ["--timing", t / "downloads" / "timing_*.xlsx"],
                                       "--timing: no file matches timing_*.xlsx"),
    "two files for an argument that takes one": (lambda a, t: a[:4] + ["--assignment", t / "downloads" / "tim*.csv"],
                                                 "--assignment takes one file: 2 files match tim*.csv"),
    "a case list that cannot be the key": (lambda a, t: a[:4] + ["--case-list", a[1], "--cases-root", a[7]],
                                           "--case-list: timer_events_2026-11-12-09-30.csv has no column po_number"),
}


@pytest.mark.parametrize("name", list(REFUSALS))
def test_a_command_line_that_cannot_be_run_is_refused_with_one_sentence(pilot, tmp_path, capsys, name):
    """Exit code 2, a sentence that names the argument and what to do, and nothing written: the folder of --out is not made and
    the data folder is as it was."""
    data, (arguments, sentence) = data_of(pilot), REFUSALS[name]
    before = case_runs.snapshot(data)
    error = refused(capsys, log, "--out", tmp_path / "records", *arguments(pilot["arguments"], tmp_path))
    assert f"python -m aid.evalscripts.records log: error: {sentence}" in error and "Traceback" not in error
    assert not (tmp_path / "records").exists() and case_runs.snapshot(data) == before


@pytest.mark.parametrize("out,sentence", [
    ("data/cases/records", "--out is the cases root (--cases-root) or lies inside it: give a new folder outside the aid's data folder"),
    ("data/records", "--out is a data folder of the aid or lies inside one"),
    ("data/inbox", "--out is a data folder of the aid or lies inside one"),
])
def test_log_writes_nothing_into_the_aids_own_folders(pilot, tmp_path, capsys, out, sentence):
    data = data_of(pilot)
    (data / "inbox").mkdir()
    before = case_runs.snapshot(data)
    assert sentence in refused(capsys, log, "--out", tmp_path / out, *pilot["arguments"])
    assert case_runs.snapshot(data) == before


@pytest.mark.parametrize("marker", ["directory", "file"])
def test_log_writes_nothing_into_a_clone_of_a_repository(tmp_path, capsys, marker):
    """The log is a record of restricted storage: a folder inside a clone (its .git is a folder, or a file in a worktree) is
    refused, also when it is a folder deeper down."""
    events, timing = two_cases().write(tmp_path / "downloads")
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / ".git").mkdir() if marker == "directory" else (clone / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    for out in (clone / "records_log", clone / "evaluation" / "records_log"):
        error = refused(capsys, log, "--out", out, "--events", events, "--timing", timing)
        assert ("--out lies inside a clone of a repository (clone holds a .git): the records are of restricted storage: give a "
                "folder outside every clone") in error
        assert not out.exists()


def test_a_second_run_into_the_same_folder_is_refused_and_changes_nothing(tmp_path, capsys):
    """Every run of the pilot week needs a folder of its own, and the sentence says so."""
    events, timing = two_cases().write(tmp_path / "downloads")
    out = tmp_path / "records"
    assert log("--out", out, "--events", events, "--timing", timing, "--check-only") == 0
    before = case_runs.snapshot(out)
    error = refused(capsys, log, "--out", out, "--events", events, "--timing", timing)
    assert f"timer_check.csv is already in {out}: give a new folder, for example one per day" in error
    assert case_runs.snapshot(out) == before


def test_log_is_not_made_while_the_aid_runs_on_the_data_folder(pilot, tmp_path, capsys):
    """The launcher's heartbeat beside the settings is younger than half a minute: on Windows a file that the command has open
    makes a write of the aid fail, so the command refuses, unless the person at the keyboard says the aid may run."""
    data = data_of(pilot)
    heartbeat(data, age=3.0)
    before = case_runs.snapshot(data)
    error = refused(capsys, log, "--out", tmp_path / "records", *pilot["arguments"])
    assert "error: the aid runs on this data folder: stop it first, or give --while-running" in error
    assert not (tmp_path / "records").exists() and case_runs.snapshot(data) == before
    assert log("--out", tmp_path / "records", *pilot["arguments"], "--while-running") == 0
    assert json.loads((tmp_path / "records" / "log_manifest.json").read_bytes())["options"]["while_running"] is True
    heartbeat(data, age=300.0)
    assert log("--out", tmp_path / "later", *pilot["arguments"]) == 0     # a launcher that stopped five minutes ago


def test_log_says_when_it_cannot_check_whether_the_aid_runs(pilot, tmp_path, capsys):
    """The cases root is a setting: when no settings file stands above it, the launcher's state file is not found either."""
    assert log("--out", tmp_path / "records", *pilot["arguments"]) == 0
    assert "whether the aid runs could not be checked" in capsys.readouterr().out
    data_of(pilot)
    assert log("--out", tmp_path / "again", *pilot["arguments"]) == 0
    assert "whether the aid runs could not be checked" not in capsys.readouterr().out


def test_log_leaves_everything_under_the_data_folder_as_it_was(pilot, tmp_path, capsys):
    """Names, sizes, content and modification times, of the cases root and of what lies beside it: after the full log, after the
    check alone and with every other record given."""
    data = data_of(pilot)
    before = case_runs.snapshot(data)
    conditions = ["manual"] + ["assisted"] * 4
    planned = assignment(tmp_path, *(f"{order},1,1,MAAM,P0{order},1,1,G1,{conditions[order - 1]}" for order in range(1, 6)))
    seq, kept = seq_record(tmp_path, "P02,7,,immediate,yes,,"), kept_by_hand(tmp_path, "P01,2,1,price,accept,,")
    assert log("--out", tmp_path / "one", *pilot["arguments"]) == 0
    assert log("--out", tmp_path / "two", *pilot["arguments"], "--check-only") == 0
    assert log("--out", tmp_path / "three", *pilot["arguments"], "--assignment", planned, "--seq", seq, "--dispositions", kept) == 0
    assert case_runs.snapshot(data) == before
    assert sorted(path.name for path in (tmp_path / "three").iterdir()) == ["case_log.csv", "log_manifest.json", "timer_check.csv"]
    assert str(data) not in capsys.readouterr().out


def test_log_opens_no_file_of_the_data_folder_for_writing_and_reads_none_twice_itself(pilot, tmp_path, monkeypatch):
    """Every file of a case folder that the command reads itself is read with one read of its bytes, once, so that no handle stays
    open while it works: the case.json, the audit record, the records of the closes and the disposition log. The present state is
    told by the review page's own reading (aid/review/status.py), which the command asks once for all cases and which opens for
    reading only as well."""
    data = data_of(pilot)
    real_read, real_open, real_state = Path.read_bytes, io.open, case_runs.state_of
    reads, opens, asked = [], [], []

    def read_bytes(self):
        if self.is_relative_to(data) and len(asked) != 1:
            reads.append(self.relative_to(data).as_posix())
        return real_read(self)

    def opening(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).is_relative_to(data):
            opens.append(mode)
        return real_open(file, mode, *args, **kwargs)

    def state_of(cases_root, runs):
        asked.append(1)
        try:
            return real_state(cases_root, runs)
        finally:
            asked.append(2)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(io, "open", opening)
    monkeypatch.setattr(builtins, "open", opening)
    monkeypatch.setattr(case_runs, "state_of", state_of)
    assert log("--out", tmp_path / "records", *pilot["arguments"]) == 0
    assert asked == [1, 2] and opens and set(opens) <= {"r", "rb"} and len(reads) == len(set(reads))
    third = f"cases/{pilot['folders']['P03'][0].case_id}"
    assert {f"{third}/case.json", f"{third}/audit.json", f"{third}/steps/09_close.json", f"{third}/dispositions.csv"} <= set(reads)
    assert f"cases/{pilot['folders']['P02'][0].case_id}/dispositions.csv" not in reads               # no run of the handling


def test_a_file_that_another_program_holds_is_said_plainly_or_passed_over(pilot, tmp_path, capsys, monkeypatch):
    """An input that a spreadsheet has open ends the command with the sentence for it. A file of a case folder that cannot be
    read at that moment does not: the folder or the file is passed over and counted, by the file's name and never the folder's."""
    arguments, folders = pilot["arguments"], pilot["folders"]
    held = {Path(arguments[3])}
    real_read = Path.read_bytes

    def read_bytes(self):
        if self in held:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read(self)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    error = refused(capsys, log, "--out", tmp_path / "records", *arguments)
    assert "error: --timing: timing_2026-11-12-09-30.csv is open in another program (Excel?): close it and run again" in error
    held.clear()
    held |= {folders["P05"][0].root / "case.json", folders["P03"][0].root / "audit.json", folders["P02"][1].root / "dispositions.csv"}
    assert log("--out", tmp_path / "records", *arguments) == 0
    logged = rows_of(tmp_path / "records" / "case_log.csv")
    assert logged["P05"]["remarks"] == "assisted case: no run of the aid found" and logged["P05"]["aid_status"] == ""
    assert "run 1: audit.json could not be read now (PermissionError)" in logged["P03"]["remarks"] and logged["P03"]["aid_status"] == ""
    assert "run 2: dispositions.csv could not be read now (PermissionError)" in logged["P02"]["remarks"]
    printed = capsys.readouterr().out
    assert "1 case folder could not be read now and was passed over" in printed and "2501" not in printed


def test_the_exports_may_be_given_by_a_pattern(tmp_path):
    """--events timer_events_*.csv, as it is typed on a command line that expands nothing."""
    first, second = two_cases(), two_cases()
    first.events, first.timing = first.events[:5], first.timing[:2]       # the export after the first case
    first.write(tmp_path / "downloads", "2026-11-12-09-05")
    second.write(tmp_path / "downloads", "2026-11-12-09-30")
    out = tmp_path / "records"
    downloads = tmp_path / "downloads"
    assert log("--out", out, "--events", downloads / "timer_events_*.csv", "--timing", downloads / "timing_????-*.csv") == 0
    assert [row["consistent"] for row in read_csv(out / "timer_check.csv")] == ["yes", "yes"]
    manifest = json.loads((out / "log_manifest.json").read_bytes())
    assert manifest["options"]["events"] == ["timer_events_2026-11-12-09-05.csv", "timer_events_2026-11-12-09-30.csv"]
    assert len(manifest["inputs"]) == 4


# --- log: the manifest ---

def test_the_manifest_of_the_log_says_what_it_was_made_from(pilot, tmp_path, monkeypatch):
    """The sha256 values are those of the files; the options hold base names only; and nothing in it is a path, a purchase order
    number or the name of a case folder."""
    monkeypatch.setattr(records, "today", lambda: date(2026, 11, 23))
    arguments, out = pilot["arguments"], tmp_path / "records"
    seq = seq_record(tmp_path, "P02,7,,immediate,yes,,")
    assert log("--out", out, *arguments, "--seq", seq) == 0
    text = (out / "log_manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(text)
    assert (manifest["subcommand"], manifest["day"], manifest["aid_version"], manifest["cases"]) == ("log", "2026-11-23", "0.1", 5)
    assert manifest["options"] == {"events": ["timer_events_2026-11-12-09-30.csv"], "timing": ["timing_2026-11-12-09-30.csv"],
                                   "dispositions": [], "assignment": None, "case_list": "case_list.csv", "seq": "seq_ratings.csv",
                                   "cases_root": "cases", "check_only": False, "while_running": False}
    given = [Path(arguments[1]), Path(arguments[3]), Path(arguments[5]), seq]
    assert manifest["inputs"] == [{"name": path.name, "sha256": sha256(path)} for path in given]
    assert manifest["outputs"] == [{"name": name, "sha256": sha256(out / name)} for name in ("timer_check.csv", "case_log.csv")]
    assert manifest["notes"] == ["attempts abandoned on a service failure leave no record in a case folder",
                                 "rows that a later close appended to a disposition log cannot be told from the rows written before the "
                                 "timer stopped"]
    assert str(tmp_path) not in text and "2501" not in text and "\\\\" not in text


def test_the_manifest_of_a_log_without_case_folders_has_no_notes_on_them(tmp_path):
    events, timing = two_cases().write(tmp_path / "downloads")
    assert log("--out", tmp_path / "records", "--events", events, "--timing", timing) == 0
    assert json.loads((tmp_path / "records" / "log_manifest.json").read_bytes())["notes"] == []


# --- log: what goes wrong on the way ---

def test_a_record_that_cannot_be_renamed_ends_the_command_plainly_and_leaves_nothing(tmp_path, capsys, monkeypatch):
    """A virus scanner or a spreadsheet holds a file at the moment of the rename: exit code 2, the sentence, and no file of the run
    left in the folder, so that the same command can be given again."""
    events, timing = two_cases().write(tmp_path / "downloads")

    def held(source, target):
        raise PermissionError(13, "Permission denied", str(source))
    monkeypatch.setattr(os, "replace", held)
    error = refused(capsys, log, "--out", tmp_path / "records", "--events", events, "--timing", timing)
    assert "error: timer_check.csv is open in another program (Excel?): close it and run again" in error
    assert list((tmp_path / "records").iterdir()) == []
    monkeypatch.undo()
    assert log("--out", tmp_path / "records", "--events", events, "--timing", timing) == 0


def test_a_present_state_that_cannot_be_read_now_is_a_remark_and_no_path_reaches_the_terminal(pilot, tmp_path, capsys, monkeypatch):
    """The review page's own reading meets a file that the aid is writing: the log is written without the present state, the
    remarks say so, and the error with the path of the case folder is not shown."""
    folder = pilot["folders"]["P02"][1].root

    def held(cases_root, runs):
        raise PermissionError(13, "Permission denied", str(folder / "steps" / "08_result.json"))
    monkeypatch.setattr(case_runs, "state_of", held)
    assert log("--out", tmp_path / "records", *pilot["arguments"]) == 0
    row = rows_of(tmp_path / "records" / "case_log.csv")["P02"]
    assert (row["aid_status"], row["aid_state"], row["closed_by"]) == ("closed", "", "")
    assert "the present state of the case folder could not be read now (PermissionError)" in row["remarks"]
    captured = capsys.readouterr()
    assert "2501" not in captured.out + captured.err and "closed by rule 0" in captured.out


def test_an_error_of_the_system_on_the_way_shows_no_path(pilot, tmp_path, capsys, monkeypatch):
    """Whatever file it is, its path may hold a purchase order number: the command ends with the class of the error alone."""
    def broken(cases_root):
        raise OSError(5, "Input/output error", str(Path(cases_root) / "250101_20261112T090325"))
    monkeypatch.setattr(case_runs, "folders", broken)
    error = refused(capsys, log, "--out", tmp_path / "records", *pilot["arguments"])
    assert "error: a file could not be read or written (OSError): run again" in error
    assert "2501" not in error and "Traceback" not in error and not (tmp_path / "records").exists()


def test_a_run_without_a_start_time_is_not_placed_in_the_handling_and_says_so(tmp_path):
    """A folder whose audit record is missing or torn: when the run started is not known, so it is no run of the handling."""
    root = tmp_path / "data" / "cases"
    arguments = one_assisted_case(tmp_path)
    folder = by_hand(root, "250301_a", 10, result=case_result("250301_a", "closed"))
    (folder / "audit.json").write_text("{torn", encoding="utf-8")
    assert log("--out", tmp_path / "records", *arguments) == 0
    row = rows_of(tmp_path / "records" / "case_log.csv")["P01"]
    assert aid_cells(row)[:3] == ["0", "0", ""]
    assert row["remarks"] == "run 1: no audit record with a start time, so it cannot be placed against the handling"


def test_only_a_run_that_lies_before_or_after_the_handling_is_counted_outside_it(tmp_path):
    """A run that cannot be placed is counted neither in the handling nor outside it: the remark says so, and the case is not said
    to have had no run in the handling, as nobody knows. Without a start and a stop of the timer no run can be placed, and both
    columns stay blank."""
    root = tmp_path / "data" / "cases"
    arguments = one_assisted_case(tmp_path)
    by_hand(root, "250301_a", -30, result=case_result("250301_a", "closed"))
    torn = by_hand(root, "250301_b", 10, result=case_result("250301_b", "closed"), sha256="ab" * 32)
    (torn / "audit.json").write_text("{torn", encoding="utf-8")
    assert log("--out", tmp_path / "records", *arguments) == 0
    row = rows_of(tmp_path / "records" / "case_log.csv")["P01"]
    assert aid_cells(row)[:2] == ["0", "1"]
    assert row["remarks"] == "run 2: no audit record with a start time, so it cannot be placed against the handling"
    exports = Exports()
    exports.handled("P01", "assisted", ("start", 0))                      # started and not stopped yet
    events, _ = exports.write(tmp_path / "later")
    assert log("--out", tmp_path / "again", "--events", events, *arguments[4:]) == 0
    row = rows_of(tmp_path / "again" / "case_log.csv")["P01"]
    assert aid_cells(row)[:2] == ["", ""] and row["remarks"] == "no start and stop of the timer to place the runs of the aid in"


def test_case_ids_that_differ_only_in_their_spelling_are_named_as_a_pair(tmp_path, capsys):
    """P01 in the timer, p1 in the case list and P 01 in the SEQ record are three ids to the command and most likely one case to
    the researcher: the terminal and the remarks name them, and nothing is merged."""
    root = tmp_path / "data" / "cases"
    arguments = one_assisted_case(tmp_path)
    by_hand(root, "250301_a", 10, result=case_result("250301_a", "closed"))
    arguments[arguments.index("--case-list") + 1] = case_list(tmp_path, "p1,250301,,", "P02,250302,,")
    seq = seq_record(tmp_path, "P 01,6,,immediate,,,", "P02,5,,immediate,,,")
    assert log("--out", tmp_path / "records", *arguments, "--seq", seq) == 0
    logged = rows_of(tmp_path / "records" / "case_log.csv")
    assert list(logged) == ["P01", "P02", "p1"]
    assert "case ids that differ only in capitals, spaces or leading zeros: P 01, p1" in logged["P01"]["remarks"]
    assert "case ids that differ only in capitals, spaces or leading zeros: P 01, P01" in logged["p1"]["remarks"]
    assert "differ only" not in logged["P02"]["remarks"]
    assert "Case ids that differ only in capitals, spaces or leading zeros: P 01, P01, p1" in capsys.readouterr().out


def test_events_of_a_timer_that_ran_with_an_assignment_give_no_log_without_that_assignment(tmp_path, capsys):
    """The timer writes the session of the assignment into every event, and leaves it blank in the pilot. Events with a session
    are those of the held-out run: without the assignment the command cannot tell whether handling is finished, so it writes the
    check alone and asks for the assignment."""
    exports = Exports()
    exports.handled("C01", "manual", ("start", 0), ("complete", 60), session="1", active="60.0")
    events, timing = exports.write(tmp_path / "downloads")
    out = tmp_path / "records"
    assert log("--out", out, "--events", events, "--timing", timing) == 0
    assert sorted(path.name for path in out.iterdir()) == ["log_manifest.json", "timer_check.csv"]
    printed = capsys.readouterr().out
    assert ("the events carry a session, so the timer ran with an assignment: give --assignment; case_log.csv is not written" in printed
            and NOT_FINISHED not in printed)
    planned = assignment(tmp_path, "1,1,1,M,C01,1,1,G1,manual")
    assert log("--out", tmp_path / "again", "--events", events, "--timing", timing, "--assignment", planned) == 0
    assert (tmp_path / "again" / "case_log.csv").exists()


# --- the three subcommands end to end on a demo folder ---

@pytest.fixture
def demo_folder(tmp_path) -> dict:
    """A demo folder as `python -m aid.demo --out <folder> --reader recorded` makes it: the 22 made-up cases run by the aid, with
    their case list (cases.csv) beside the cases root. Returns the folder and the moments before and after the demo ran, by the
    clock of the PC, as the timer reads it."""
    from aid import demo

    folder = tmp_path / "demo"
    before = datetime.now(timezone.utc)
    with contextlib.redirect_stdout(io.StringIO()):
        assert demo.main(["--out", str(folder), "--reader", "recorded"]) == 0
    return {"folder": folder, "before": before, "after": datetime.now(timezone.utc)}


def seconds_after_base(moment: datetime) -> float:
    """A moment as the seconds after BASE that the helpers of the timer's exports take."""
    return (moment - BASE).total_seconds()


def test_the_three_subcommands_on_a_demo_folder_only_read_it(demo_folder, tmp_path, capsys):
    """The pilot check of the log, the sheet of each control point and the counts of each sheet, run end to end through the
    command on the made-up set, with the references of aid/madeup/references.csv. Before and after every subcommand, and after a
    refusal of each, every file under the demo folder (the cases root, the PO store, the inbox, the settings and the case list)
    has its name, its size, its sha256 and its modification time as before. Nothing printed names a path under the cases root."""
    folder = demo_folder["folder"]
    cases_root, case_list_file = folder / "cases", folder / "cases.csv"
    references = REPO_ROOT / "aid" / "madeup" / "references.csv"
    start_at = seconds_after_base(demo_folder["before"]) - 5
    stop_at = seconds_after_base(demo_folder["after"]) + 30
    exports = Exports()
    exports.handled("MU07", "assisted", ("start", start_at), ("wait", start_at + 1), ("wait_end", stop_at - 10),
                    ("complete", stop_at), active="11.0", passive=f"{stop_at - 11 - start_at:.1f}")
    exports.handled("MU02", "manual", ("start", stop_at + 10), ("pause", stop_at + 20), ("resume", stop_at + 30),
                    ("complete", stop_at + 70), active="50.0")
    events, timing = exports.write(tmp_path / "downloads")
    whole = case_runs.snapshot(folder)

    def unchanged() -> None:
        assert case_runs.snapshot(folder) == whole

    printed = []
    assert log("--out", tmp_path / "log", "--events", events, "--timing", timing, "--case-list", case_list_file,
               "--cases-root", cases_root) == 0
    unchanged()
    printed.append(capsys.readouterr().out)
    assert "Criterion 1 of 3 (the timer log is complete): 2 of 2 cases" in printed[-1] and without_times(printed[-1])
    logged = rows_of(tmp_path / "log" / "case_log.csv")
    assert len(logged) == 22 and list(logged)[:2] == ["MU07", "MU02"]
    assert (logged["MU07"]["aid_runs_in_handling"], logged["MU07"]["flags_raw"], logged["MU07"]["aid_version"]) == (
        "1", "1", "1 (reference)")
    assert aid_cells(logged["MU02"]) == [""] * len(AID_COLUMNS) and logged["MU02"]["remarks"] == "run before the handling"
    check = rows_of(tmp_path / "log" / "timer_check.csv")
    assert check["MU07"]["log_complete"] == check["MU07"]["consistent"] == "yes" and check["MU07"]["problems"] == ""
    assert check["MU01"]["hints"] == "no timer record: not handled yet, or its exports were not given"
    assert log("--out", tmp_path / "check", "--events", events, "--timing", timing, "--case-list", case_list_file,
               "--cases-root", cases_root, "--check-only") == 0
    unchanged()
    printed.append(capsys.readouterr().out)
    assert sorted(path.name for path in (tmp_path / "check").iterdir()) == ["log_manifest.json", "timer_check.csv"]
    assert "timer_check.csv is already in" in refused(capsys, log, "--out", tmp_path / "log", "--events", events)
    unchanged()

    found = {"confirmation": (13, 20), "cart": (2, 2)}
    for kind, (differences, cases) in found.items():
        sheet = tmp_path / f"sheet_{kind}"
        assert main(["sheet", "--out", str(sheet), "--set", "made-up", "--kind", kind, "--case-list", str(case_list_file),
                     "--cases-root", str(cases_root), "--references", str(references)]) == 0
        unchanged()
        printed.append(capsys.readouterr().out)
        assert f"control point {kind}: {cases} cases" in printed[-1]
        assert sorted(path.name for path in sheet.iterdir()) == ["raw_output_scores.csv", "raw_output_scores.xlsx",
                                                                 "sheet_manifest.json"]
        error = refused(capsys, main, ["sheet", "--out", str(cases_root / "records"), "--set", "made-up", "--kind", kind,
                                       "--case-list", str(case_list_file), "--cases-root", str(cases_root)])
        assert "--out is the cases root (--cases-root) or lies inside it" in error
        unchanged()
        for source in ("raw_output_scores.csv", "raw_output_scores.xlsx"):
            counted = tmp_path / f"counts_{kind}_{source.rpartition('.')[2]}"
            assert main(["counts", "--out", str(counted), "--set", "made-up", "--classes", "mechanical",
                         "--sheet", str(sheet / source)]) == 0
            unchanged()
            printed.append(capsys.readouterr().out)
            assert printed[-1].startswith("Set made-up, classes mechanical: the aid's behaviour on made-up documents")
            total = rows_of_field(counted / "raw_output_counts.csv")["total"]
            assert (total["differences"], total["detections"], total["false_flags"], total["recall"], total["unscored_rows"]) == (
                str(differences), str(differences), "0", f"{differences} of {differences}", "0")
            assert "claim 1" not in (counted / "raw_output_counts.csv").read_text(encoding="utf-8")
        assert "--sheet: " in refused(capsys, main, ["counts", "--out", str(tmp_path / "never"), "--set", "made-up",
                                                     "--classes", "rater", "--sheet", str(case_list_file)])
        unchanged()
    assert not (tmp_path / "never").exists() and not (cases_root / "records").exists()
    for text in printed:
        assert str(cases_root) not in text and "250107" not in text


def rows_of_field(path: Path) -> dict[str, dict[str, str]]:
    """The rows of raw_output_counts.csv by field."""
    return {row["field"]: row for row in read_csv(path)}


# --- what the documents say of the command ---

def section(text: str, heading: str) -> str:
    """The part of a Markdown text under `heading` (a whole heading line), up to the next heading of the same or a higher level."""
    lines = text.splitlines()
    start = lines.index(heading)
    level = len(heading) - len(heading.lstrip("#"))
    end = next((number for number in range(start + 1, len(lines))
                if lines[number].startswith("#") and len(lines[number]) - len(lines[number].lstrip("#")) <= level), len(lines))
    return "\n".join(lines[start:end])


def test_the_documents_say_how_the_records_command_is_run_and_what_it_only_reads():
    """The README, the manual's section 6, the evaluation's README, the README of the blank records, the package's docstring and
    the results README each name the command where the reader of that document looks for it, with the sentences the owner needs:
    it only reads the case folders and the PO store, --out is a new folder outside the aid's data folder, the sheet is a record of
    restricted storage, the aid is stopped first, the order of work, and the Windows form of each subcommand. The parts written for
    the command hold no dash of the kinds that prose of this repository avoids."""
    command = "-m aid.evalscripts.records"
    readme = (REPO_ROOT / "aid" / "README.md").read_text(encoding="utf-8")
    for subcommand in ("log", "sheet", "counts"):
        assert f"python3 {command} {subcommand} --out <folder>" in readme
    manual = section((REPO_ROOT / "aid" / "MANUAL.md").read_text(encoding="utf-8"), "## 6. The evaluation commands")
    part = manual[manual.index("**The evaluation records**"):]
    for words in ("only reads the case folders and the PO store", "a new folder outside the aid's data folder",
                  "outside every clone and every synchronised folder", "restricted storage", "only counts may go to the repository",
                  "Stop the aid before `log` and `sheet`", "--check-only", "--references-locked", "--classes rater",
                  "--classes mechanical", "not the frozen aid of the evaluation"):
        assert words in part, words
    for subcommand in ("log", "sheet", "counts"):
        assert f'"<folder>\\python\\python.exe" -I -X utf8 {command} {subcommand} --out' in part
    evaluation = (REPO_ROOT / "evaluation" / "README.md").read_text(encoding="utf-8")
    assert evaluation.count(command) >= 3 and "timer_check.csv" in evaluation and "raw_output_scores" in evaluation
    given = re.findall(r"--(?:out|sheet|case-list|seq) (<[^>]+>/\S+|\S+)", evaluation)  # run from the repository root: no path in it
    assert len(given) >= 8 and all(path.startswith("<restricted folder>/") for path in given), given
    templates = (REPO_ROOT / "evaluation" / "templates" / "README.md").read_text(encoding="utf-8")
    row = next(line for line in templates.splitlines() if line.startswith("| `raw_output_scores.csv`"))
    assert f"{command} sheet" in row and "pre-filled" in row
    package = (REPO_ROOT / "aid" / "evalscripts" / "__init__.py").read_text(encoding="utf-8")
    assert "records" in package and "only reads" in package
    results = (REPO_ROOT / "aid" / "evalscripts" / "results" / "README.md").read_text(encoding="utf-8")
    made_up = section(results, "# The evaluation records: the made-up result")
    assert "recall 13 of 13" in made_up and "recall 12 of 13" in made_up and "--classes mechanical" in made_up
    for text in (part, made_up, row, package):
        assert "\u2014" not in text and "\u2013" not in text
