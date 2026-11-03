"""Tests of the AI-necessity check (aid/evalscripts/necessity.py). The LLM reader reads through the recorded answers or a fake
client: no test calls an AI service or the network."""
import shutil
from datetime import date

import pytest

from aid.evalscripts.necessity import main, necessity_decision
from aid.readers.llm_client import ReaderError
from aid.tests.conftest import REPO_ROOT, read_csv

SPEC_DIR = REPO_ROOT / "aid" / "madeup" / "spec"
RECORDED = REPO_ROOT / "aid" / "tests" / "fixtures" / "recorded"
COLUMNS = ["case", "layout", "reader", "fields_total", "fields_correct", "field_accuracy", "errors", "error"]


# --- the decision rule ---

def test_necessity_rule():
    assert necessity_decision(10, 4) == "keep_llm" and necessity_decision(10, 5) == "parser_only" and necessity_decision(0, 0) == "parser_only"


# --- the necessity command ---

@pytest.fixture
def two_cases(tmp_path, madeup_out):
    """A set of two made-up cases under tmp_path: MU01 (table_nl, which the parser reads) and MU05 (a scan, which it cannot), each
    confirmation with its spec."""
    cases, specs = tmp_path / "cases", tmp_path / "spec"
    cases.mkdir()
    specs.mkdir()
    for case in ("MU01", "MU05"):
        shutil.copy(madeup_out / f"{case}_conf.pdf", cases)
        shutil.copy(SPEC_DIR / f"{case}.json", specs)
    return cases, specs


def run_command(cases, specs, out, client=None):
    return main(["--cases", str(cases), "--spec", str(specs), "--recorded", str(RECORDED), "--out", str(out)], client=client)


def test_both_readers_read_every_case_and_the_scan_decides_for_the_llm(two_cases, tmp_path, capsys):
    """The parser reads MU01 and fails on the scan MU05, whose fields all count as wrong; the LLM reader reads both over the
    recorded answers. The LLM reader then makes fewer than half the parser's errors: keep_llm."""
    out = tmp_path / "necessity.csv"
    assert run_command(*two_cases, out) == 0
    rows = read_csv(out)
    assert list(rows[0]) == COLUMNS
    by = {(row["case"], row["reader"]): row for row in rows}
    assert sorted(by) == [("MU01", "llm"), ("MU01", "parser"), ("MU05", "llm"), ("MU05", "parser")]
    assert by["MU01", "parser"]["layout"] == "table_nl" and by["MU05", "parser"]["layout"] == "scan"
    for row in rows:
        assert int(row["errors"]) == int(row["fields_total"]) - int(row["fields_correct"])
    scan = by["MU05", "parser"]
    assert scan["fields_correct"] == "0" and scan["errors"] == scan["fields_total"] != "0" and scan["error"].startswith("no template")
    assert by["MU05", "llm"]["errors"] == "0" and by["MU05", "llm"]["error"] == "" and by["MU01", "llm"]["errors"] == "0"
    assert by["MU01", "parser"]["errors"] == "0" and by["MU01", "parser"]["error"] == ""
    printed = capsys.readouterr().out
    assert f"scan: parser {scan['errors']} errors, LLM reader 0 errors" in printed
    assert f"All cases: parser {scan['errors']} errors, LLM reader 0 errors" in printed
    assert "Decision: keep_llm" in printed and "made-up set" in printed
    assert [path.name for path in tmp_path.iterdir() if path.name.startswith("necessity")] == ["necessity.csv"]   # no .partial left


class Refusing:
    """An LLM client that refuses every request."""
    name = "refusing"

    def complete(self, req):
        raise ReaderError("the model refused to answer")


def test_a_failed_llm_reading_is_recorded_and_the_run_goes_on(two_cases, tmp_path, capsys):
    """A ReaderError of the LLM reader is the row of a reading with every field wrong and the error, not the end of the run; with
    the LLM reader failing on both cases its errors are not fewer than half the parser's: parser_only."""
    out = tmp_path / "necessity.csv"
    assert run_command(*two_cases, out, client=Refusing()) == 0
    llm = [row for row in read_csv(out) if row["reader"] == "llm"]
    assert [row["case"] for row in llm] == ["MU01", "MU05"]
    assert all(row["fields_correct"] == "0" and row["error"] == "the model refused to answer" for row in llm)
    assert "Decision: parser_only" in capsys.readouterr().out


def test_a_case_without_a_spec_stops_the_run_with_a_clear_message(two_cases, tmp_path, capsys):
    cases, specs = two_cases
    (specs / "MU05.json").unlink()
    with pytest.raises(SystemExit) as stopped:
        run_command(cases, specs, tmp_path / "necessity.csv")
    assert stopped.value.code == 2 and "MU05_conf.pdf has no spec" in capsys.readouterr().err
    assert not (tmp_path / "necessity.csv").exists()


def test_the_result_file_is_named_after_the_date_of_the_run(two_cases, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aid.evalscripts.necessity.today", lambda: date(2026, 11, 20))
    cases, specs = two_cases
    assert main(["--cases", str(cases), "--spec", str(specs), "--recorded", str(RECORDED)]) == 0
    assert len(read_csv(tmp_path / "aid" / "evalscripts" / "results" / "necessity_2026-11-20.csv")) == 4


def test_the_whole_made_up_set(madeup_out, tmp_path, capsys):
    """Every confirmation of the made-up set, read by both readers over the recorded answers: two rows per case. The parser fails
    on the two scans and the recorded answers are perfect readings, so the decision on the made-up set is keep_llm."""
    out = tmp_path / "necessity.csv"
    assert run_command(madeup_out, SPEC_DIR, out) == 0
    rows = read_csv(out)
    cases = sorted(path.name.removesuffix("_conf.pdf") for path in madeup_out.glob("*_conf.pdf"))
    assert len(cases) == 20 and sorted(row["case"] for row in rows) == sorted(cases * 2)
    assert {(row["case"], row["reader"]) for row in rows} == {(case, reader) for case in cases for reader in ("parser", "llm")}
    printed = capsys.readouterr().out
    assert "Decision: keep_llm" in printed and "made-up set" in printed


def test_without_recorded_answers_every_llm_row_says_so_and_the_decision_is_still_made(two_cases, tmp_path, capsys):
    """An empty recorded folder: RecordedClient has no answer, which is a read failure of the LLM reader, not the end of the run."""
    cases, specs = two_cases
    empty, out = tmp_path / "empty", tmp_path / "necessity.csv"
    empty.mkdir()
    assert main(["--cases", str(cases), "--spec", str(specs), "--recorded", str(empty), "--out", str(out)]) == 0
    llm = [row for row in read_csv(out) if row["reader"] == "llm"]
    assert [(row["case"], row["error"], row["fields_correct"]) for row in llm] == [("MU01", "no recorded answer", "0"),
                                                                                    ("MU05", "no recorded answer", "0")]
    assert "Decision: parser_only" in capsys.readouterr().out


class Broken:
    """An LLM client that fails with an error that is not a ReaderError."""
    name = "broken"

    def complete(self, req):
        raise RuntimeError("the connection dropped")


def test_any_other_failure_of_the_llm_reader_is_recorded_with_its_class(two_cases, tmp_path):
    out = tmp_path / "necessity.csv"
    assert run_command(*two_cases, out, client=Broken()) == 0
    assert {row["error"] for row in read_csv(out) if row["reader"] == "llm"} == {"RuntimeError: the connection dropped"}


def test_a_corrupt_pdf_is_a_read_failure_of_both_readers(two_cases, tmp_path, capsys):
    """A confirmation whose bytes are no PDF: the parser cannot take its text layer and the LLM reader has no answer for it; both
    rows hold their error and every field wrong, and the other case is read as before."""
    cases, specs = two_cases
    (cases / "MU01_conf.pdf").write_bytes(b"this is not a PDF")
    out = tmp_path / "necessity.csv"
    assert run_command(cases, specs, out) == 0
    by = {(row["case"], row["reader"]): row for row in read_csv(out)}
    assert by["MU01", "parser"]["error"] != "" and by["MU01", "parser"]["fields_correct"] == "0"
    assert by["MU01", "llm"]["error"] == "no recorded answer" and by["MU01", "llm"]["fields_correct"] == "0"
    assert by["MU05", "llm"]["errors"] == "0"
    assert "Decision:" in capsys.readouterr().out


@pytest.mark.parametrize("content,message", [("CART", "is the spec of a cart"), ("{not json", "cannot be read")])
def test_a_spec_that_is_no_confirmation_spec_stops_the_run_naming_the_file(two_cases, tmp_path, capsys, content, message):
    cases, specs = two_cases
    spec = specs / "MU05.json"
    spec.write_text((SPEC_DIR / "CART01.json").read_text(encoding="utf-8") if content == "CART" else content, encoding="utf-8")
    with pytest.raises(SystemExit) as stopped:
        run_command(cases, specs, tmp_path / "necessity.csv")
    error = capsys.readouterr().err
    assert stopped.value.code == 2 and message in error and str(spec) in error
    assert not list(tmp_path.glob("necessity.csv*"))
