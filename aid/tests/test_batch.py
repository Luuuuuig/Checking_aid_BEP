"""Tests of the batch runner (aid/batch.py) on the made-up set."""
import csv
import json
import shutil
from datetime import datetime

import pytest

from aid.batch import SUMMARY_COLUMNS, main
from aid.madeup.generate import TRYOUT_EXPORT_COLUMNS, MadeUpSpec, export_csv
from aid.po_store import EXPORT_FIELDS, PoStore
from aid.tests.conftest import REPO_ROOT, SETTINGS, read_csv


def test_batch_summary(tmp_path, madeup_out):
    cases = tmp_path / "cases.csv"; cases.write_text("case_id,document,po_number,kind\nMU01,%s,250101,confirmation\nMU07,%s,250107,confirmation\nMU17,%s,250117,confirmation\n" % (madeup_out / "MU01_conf.pdf", madeup_out / "MU07_conf.pdf", madeup_out / "MU17_conf.pdf"))
    settings = tmp_path / "s.json"; settings.write_text(SETTINGS.model_copy(update={"cases_root": tmp_path / "cases", "po_store_root": tmp_path / "store"}).model_dump_json())
    assert main(["--cases", str(cases), "--po-dir", str(madeup_out), "--settings", str(settings), "--reader", "parser", "--out", str(tmp_path / "summary.csv")]) == 0
    rows = read_csv(tmp_path / "summary.csv"); assert [r["status"] for r in rows] == ["closed", "review", "waiting"]


# The tests below cover what the rulings of the task add to the one above.

RECORDED = REPO_ROOT / "aid" / "tests" / "fixtures" / "recorded"
CONFIRMATIONS = [f"MU{n:02d}" for n in range(1, 21)]
CARTS = {"CART01": ("CART01.html", "250201"), "CART02": ("CART02.png", "250202")}
# The status of each made-up case through the batch with the parser and the settings of the tests. The parser reads PDF
# confirmations and cart pages saved as HTML: the two scans (MU05, MU16) have no text, and the cart screenshot CART02.png is the LLM
# reader's. CART01's page agrees with the PO the batch files from CART01_export.csv, so its one action is R01 without operations.
PARSER_STATUS = {"MU01": "closed", "MU02": "closed", "MU03": "closed", "MU04": "closed", "MU05": "failed", "MU06": "review", "MU07": "review",
                 "MU08": "closed", "MU09": "review", "MU10": "review", "MU11": "waiting", "MU12": "review", "MU13": "review", "MU14": "closed",
                 "MU15": "review", "MU16": "failed", "MU17": "waiting", "MU18": "waiting", "MU19": "review", "MU20": "waiting",
                 "CART01": "closed", "CART02": "failed"}
# Through the LLM reader over the synthetic recorded answers, which are perfect readings: the scans are read, MU05 has no difference
# and MU16 lacks a delivery date. CART02's 2.5 percent price difference is above the 2 percent of the settings. CART01's saved page is
# read by the cart reader, as with the parser.
RECORDED_STATUS = {case: status for case, status in PARSER_STATUS.items() if case in CONFIRMATIONS} | {"MU05": "closed", "MU16": "review",
                                                                                                      "CART01": "closed", "CART02": "review"}


def write_cases(tmp_path, madeup_out, names: list[str]):
    """cases.csv for made-up cases: a confirmation MUnn as MUnn_conf.pdf with PO number 2501nn, a cart as its page."""
    path = tmp_path / "cases.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        rows = csv.writer(f, lineterminator="\n")
        rows.writerow(["case_id", "document", "po_number", "kind"])
        for name in names:
            document, po_number = CARTS[name] if name in CARTS else (f"{name}_conf.pdf", f"2501{name[2:]}")
            rows.writerow([name, madeup_out / document, po_number, "cart" if name in CARTS else "confirmation"])
    return path


def write_settings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(SETTINGS.model_copy(update={"cases_root": tmp_path / "cases", "po_store_root": tmp_path / "store"}).model_dump_json(), encoding="utf-8")
    return path


def batch(tmp_path, madeup_out, names: list[str], *reader: str) -> tuple[int, list[dict]]:
    """Run the batch on the made-up cases `names` with the reader options; returns its exit code and the rows of the summary."""
    out = tmp_path / "summary.csv"
    code = main(["--cases", str(write_cases(tmp_path, madeup_out, names)), "--po-dir", str(madeup_out), "--settings", str(write_settings(tmp_path)),
                 *reader, "--out", str(out)])
    return code, read_csv(out)


def test_the_made_up_set_through_the_batch_with_the_parser(tmp_path, madeup_out):
    code, rows = batch(tmp_path, madeup_out, [*CONFIRMATIONS, *CARTS], "--reader", "parser")
    assert code == 1 and {row["case_id"]: row["status"] for row in rows} == PARSER_STATUS
    assert [row["case_id"] for row in rows] == [*CONFIRMATIONS, *CARTS]                     # in the order of cases.csv
    assert {row["case_id"]: row["error"] for row in rows if row["status"] == "failed"} == {
        "MU05": "ReaderError: no template for MU05_conf.pdf", "MU16": "ReaderError: no template for MU16_conf.pdf",
        "CART02": "ReaderError: no template for CART02.png: the parser reads PDF confirmations and HTML cart pages only"}
    summary = {row["case_id"]: row for row in rows}
    assert summary["MU08"] == {"case_id": "MU08", "status": "closed", "version": "1", "flags": "1", "uncompared": "0", "actions": "R04 AUTO; R01 AUTO",
                               "writes": "4", "messages": "0", "review_items": "0", "error": ""}
    assert summary["MU12"] | {"error": ""} == {"case_id": "MU12", "status": "review", "version": "1", "flags": "1", "uncompared": "2",
                                               "actions": "R06 ASK; R09 REVIEW", "writes": "0", "messages": "1", "review_items": "2", "error": ""}
    assert summary["MU05"] | {"error": ""} == {"case_id": "MU05", "status": "failed", "version": "", "flags": "", "uncompared": "", "actions": "",
                                               "writes": "0", "messages": "0", "review_items": "0", "error": ""}
    assert tuple(rows[0]) == SUMMARY_COLUMNS == ("case_id", "status", "version", "flags", "uncompared", "actions", "writes", "messages",
                                                 "review_items", "error")


def test_the_batch_records_the_dispositions_under_its_case_ids_and_prepares_the_changes(tmp_path, madeup_out):
    code, rows = batch(tmp_path, madeup_out, ["MU08", "MU17"], "--reader", "parser")
    assert code == 0 and [row["status"] for row in rows] == ["closed", "waiting"]
    [mu08] = (tmp_path / "cases").glob("250108_*")
    [mu17] = (tmp_path / "cases").glob("250117_*")
    dispositions = read_csv(mu08 / "dispositions.csv")
    assert [(row["case_id"], row["decision"]) for row in dispositions] == [("MU08", "prepared (R04)")] * 2 + [("MU08", "prepared (R01)")] * 2
    assert dispositions[0]["note"] == f"folder {mu08.name}; rule R04; finding F1"
    assert [(row["case_id"], row["decision"]) for row in read_csv(mu17 / "dispositions.csv")] == [("MU17", "ask the supplier (R07)")]
    assert [row["case_id"] for row in read_csv(tmp_path / "cases" / "prepared" / "prepared_changes.csv")] == [mu08.name] * 4
    assert (mu08 / "audit.json").exists() and (mu17 / "audit.json").exists()


def test_the_made_up_set_through_the_batch_with_the_recorded_reader(tmp_path, madeup_out):
    """The LLM reader over the synthetic recorded answers reads every confirmation, the scans too, and the cart screenshot; the cart
    reader reads the saved cart page. The batch files their POs from the ERP exports in --po-dir before the cases."""
    code, rows = batch(tmp_path, madeup_out, [*CONFIRMATIONS, *CARTS], "--reader", "recorded", "--recorded-dir", str(RECORDED))
    assert code == 0 and {row["case_id"]: row["status"] for row in rows} == RECORDED_STATUS
    summary = {row["case_id"]: row for row in rows}
    assert (summary["CART02"]["actions"], summary["CART02"]["writes"]) == ("R07 REVIEW; R05 REVIEW", "0")
    [mu05] = (tmp_path / "cases").glob("250105_*")
    assert [call["model"] for call in json.loads((mu05 / "audit.json").read_text(encoding="utf-8"))["calls"]] == ["synthetic"]


def test_the_batch_files_each_export_with_the_column_map_of_the_settings(tmp_path, madeup_out):
    """An export whose columns have names of their own is filed with settings.export_columns before the cases run; a map that the
    export does not fit stops the batch before any case is opened. The file has a column for each of the twelve export fields; the
    made-up export fills nine of them, so the cells of the discount, the net price and the supplier are blank."""
    po_dir = tmp_path / "po"
    po_dir.mkdir()
    names = {field: f"Kolom {n}" for n, field in enumerate(EXPORT_FIELDS, 1)}
    with open(po_dir / "CART01_export.csv", "w", encoding="utf-8", newline="") as f:
        rows = csv.writer(f, lineterminator="\n")
        rows.writerow(names.values())
        rows.writerows([row.get(field, "") for field in EXPORT_FIELDS] for row in read_csv(madeup_out / "CART01_export.csv"))
    cases, out = write_cases(tmp_path, madeup_out, ["CART01"]), tmp_path / "summary.csv"

    def run(columns: dict[str, str], name: str) -> int:
        settings = tmp_path / f"{name}.json"
        settings.write_text(SETTINGS.model_copy(update={"cases_root": tmp_path / name / "cases", "po_store_root": tmp_path / name / "store",
                                                        "export_columns": columns}).model_dump_json(), encoding="utf-8")
        return main(["--cases", str(cases), "--po-dir", str(po_dir), "--settings", str(settings), "--reader", "parser", "--out", str(out)])

    with pytest.raises(ValueError, match=r"^CART01_export\.csv has no column 'po_number'$"):
        run({}, "identity")                                  # the empty setting means the columns are named after the fields
    assert not (tmp_path / "identity" / "cases").exists()
    assert run(names, "named") == 0
    [row] = read_csv(out)
    assert (row["case_id"], row["status"], row["version"], row["actions"]) == ("CART01", "closed", "1", "R01 AUTO")


def test_the_batch_passes_the_export_settings(tmp_path):
    """The made-up Dutch export of the try-out folder (semicolons, decimal commas, the PO number on the first row of each order only)
    is filed with the three export settings. With export_fill_down left at its default the file stops the batch before any case is
    opened; with export_decimal_comma left at its default the 1.000 pieces of the first line are one piece."""
    po_dir = tmp_path / "po"
    po_dir.mkdir()
    shutil.copy2(REPO_ROOT / "aid" / "madeup" / "tryout" / "EXPORT_nl.csv", po_dir / "NL_export.csv")
    cases = tmp_path / "cases.csv"
    cases.write_text("case_id,document,po_number,kind\n", encoding="utf-8")

    def run(name: str, **export) -> int:
        settings = tmp_path / f"{name}.json"
        folders = {"cases_root": tmp_path / name / "cases", "po_store_root": tmp_path / name / "store"}
        used = SETTINGS.model_copy(update={**folders, "export_columns": TRYOUT_EXPORT_COLUMNS, **export})
        settings.write_text(used.model_dump_json(), encoding="utf-8")
        return main(["--cases", str(cases), "--po-dir", str(po_dir), "--settings", str(settings), "--reader", "parser",
                     "--out", str(tmp_path / name / "summary.csv")])

    def filed(name: str) -> list[tuple]:
        """Each version the store of the run holds for the two POs of the export, with the id, item code and quantity of its lines."""
        versions = [v for number in ("250301", "250302") for v in PoStore(tmp_path / name / "store").versions(number)]
        return [(v.po_number, v.version, v.supplier, v.document.reader,
                 [(line.line_id, line.item_code.value, line.quantity.value) for line in v.document.lines]) for v in versions]

    with pytest.raises(ValueError, match=r"^NL_export\.csv, row 5: an item code without a PO number$"):
        run("defaults")
    assert not (tmp_path / "defaults" / "store").exists() and not (tmp_path / "defaults" / "cases").exists()
    assert run("filled", export_fill_down=True) == 0 and run("both", export_fill_down=True, export_decimal_comma=True) == 0
    assert filed("both") == [
        ("250301", 1, "S21", "export:NL_export.csv", [("1", "KM-3101", "1000"), ("2", "KM-3102", "25"), ("3", "KM-3103", "4")]),
        ("250302", 1, "S22", "export:NL_export.csv", [("1", "KM-3201", "200"), ("2", "KM-3202", "2")])]
    assert [lines[0] for *_, lines in filed("filled")] == [("1", "KM-3101", "1.000"), ("1", "KM-3201", "200")]


def test_the_batch_files_the_po_pdfs_before_the_exports(tmp_path, madeup_out):
    """A PO with a PDF and rows in an export: the PDF, which prints its version, is version 1, and the export the next version."""
    po_dir = tmp_path / "po"
    po_dir.mkdir()
    shutil.copy2(madeup_out / "MU01_po_v1.pdf", po_dir)
    spec = MadeUpSpec.model_validate_json((REPO_ROOT / "aid" / "madeup" / "spec" / "MU01.json").read_text(encoding="utf-8"))
    (po_dir / "MU01_export.csv").write_text(export_csv(spec), encoding="utf-8")       # PO 250101 as the ERP export lists it
    cases = tmp_path / "cases.csv"
    cases.write_text("case_id,document,po_number,kind\n", encoding="utf-8")
    assert main(["--cases", str(cases), "--po-dir", str(po_dir), "--settings", str(write_settings(tmp_path)), "--reader", "parser",
                 "--out", str(tmp_path / "summary.csv")]) == 0
    assert [(v.version, v.document.reader, v.pdf_path.name) for v in PoStore(tmp_path / "store").versions("250101")] == [
        (1, "po_pdf:po_madeup", "po.pdf"), (2, "export:MU01_export.csv", "MU01_export.csv")]


def test_a_case_that_cannot_be_opened_fails_in_its_row(tmp_path, madeup_out):
    path = write_cases(tmp_path, madeup_out, ["MU01"])
    path.write_text(path.read_text(encoding="utf-8") + f"MU99,{madeup_out / 'MU99_conf.pdf'},250199,confirmation\n", encoding="utf-8")
    out = tmp_path / "summary.csv"
    assert main(["--cases", str(path), "--po-dir", str(madeup_out), "--settings", str(write_settings(tmp_path)), "--reader", "parser",
                 "--out", str(out)]) == 1
    rows = read_csv(out)
    assert [(row["case_id"], row["status"]) for row in rows] == [("MU01", "closed"), ("MU99", "failed")]
    assert rows[1]["error"].startswith("FileNotFoundError: input file not found")


def test_the_recorded_reader_needs_its_folder_and_the_po_folder_must_exist(tmp_path, capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["--cases", "c.csv", "--po-dir", str(tmp_path), "--settings", "s.json", "--reader", "recorded", "--out", str(tmp_path / "o.csv")])
    assert stopped.value.code == 2 and "--reader recorded needs --recorded-dir" in capsys.readouterr().err
    with pytest.raises(SystemExit) as stopped:
        main(["--cases", "c.csv", "--po-dir", str(tmp_path / "missing"), "--settings", "s.json", "--reader", "parser", "--out", str(tmp_path / "o.csv")])
    assert stopped.value.code == 2 and "is not a folder" in capsys.readouterr().err


def test_a_second_case_of_one_po_in_the_same_second_opens_on_the_next(tmp_path, madeup_out, monkeypatch):
    """With the clock standing still, the second case of PO 250101 would have the id of the first: it opens one second later, as the
    watched folder does, and both run."""
    monkeypatch.setattr("aid.batch._now", lambda: datetime(2026, 11, 12, 9, 0))
    code, rows = batch(tmp_path, madeup_out, ["MU01", "MU01"], "--reader", "parser")
    assert code == 0 and [(row["case_id"], row["status"]) for row in rows] == [("MU01", "closed"), ("MU01", "closed")]
    assert sorted(path.name for path in (tmp_path / "cases").glob("250101_*")) == ["250101_20261112T090000", "250101_20261112T090001"]


def test_a_cases_file_without_case_ids_gives_a_failed_row_per_line(tmp_path, madeup_out):
    path = tmp_path / "cases.csv"
    path.write_text(f"document,po_number,kind\n{madeup_out / 'MU01_conf.pdf'},250101,confirmation\n{madeup_out / 'MU07_conf.pdf'},250107,confirmation\n",
                    encoding="utf-8")
    out = tmp_path / "summary.csv"
    assert main(["--cases", str(path), "--po-dir", str(madeup_out), "--settings", str(write_settings(tmp_path)), "--reader", "parser",
                 "--out", str(out)]) == 1
    assert [(row["case_id"], row["status"], row["error"]) for row in read_csv(out)] == [
        ("", "failed", "ValueError: line 2 of cases.csv has no case_id"), ("", "failed", "ValueError: line 3 of cases.csv has no case_id")]
    assert not (tmp_path / "cases").exists() or not list((tmp_path / "cases").glob("2501*"))


def test_a_bad_line_is_named_by_its_line_in_the_file(tmp_path, madeup_out):
    """The reader skips a blank line, so the row after it is not on the line its number would give: the error names the line."""
    path = tmp_path / "cases.csv"
    path.write_text(f"case_id,document,po_number,kind\nMU01,{madeup_out / 'MU01_conf.pdf'},250101,confirmation\n\n"
                    f",{madeup_out / 'MU07_conf.pdf'},250107,confirmation\n", encoding="utf-8")
    out = tmp_path / "summary.csv"
    assert main(["--cases", str(path), "--po-dir", str(madeup_out), "--settings", str(write_settings(tmp_path)), "--reader", "parser",
                 "--out", str(out)]) == 1
    assert [(row["case_id"], row["status"], row["error"]) for row in read_csv(out)] == [
        ("MU01", "closed", ""), ("", "failed", "ValueError: line 4 of cases.csv has no case_id")]


def test_a_case_without_a_po_number_runs_and_goes_to_review(tmp_path, madeup_out):
    """A blank po_number is an unknown PO: the case opens as unknown_<time>, finds no version and waits for the buyer."""
    path = tmp_path / "cases.csv"
    path.write_text(f"case_id,document,po_number,kind\nX1,{madeup_out / 'MU01_conf.pdf'},,confirmation\n", encoding="utf-8")
    out = tmp_path / "summary.csv"
    assert main(["--cases", str(path), "--po-dir", str(madeup_out), "--settings", str(write_settings(tmp_path)), "--reader", "parser",
                 "--out", str(out)]) == 0
    [row] = read_csv(out)
    assert (row["case_id"], row["status"], row["version"], row["actions"], row["error"]) == ("X1", "review", "", "R09 REVIEW", "")
    [case] = (tmp_path / "cases").glob("unknown_*")
    assert json.loads((case / "steps" / "08_result.json").read_text(encoding="utf-8"))["digest_line"] == "PO onbekend (S01): ter beoordeling, geen schrijfacties"
