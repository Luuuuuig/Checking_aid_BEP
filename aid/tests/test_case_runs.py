"""Tests of the read-only access to the aid's case folders that the evaluation records are made from (aid/evalscripts/case_runs.py):
the case list, the runs of a case, the unreviewed output and the audit figures of a run, the present state of its folder, the aid
that still runs on the data folder, and the check of a free text against identifying text. The case folders are made by the aid
itself from the made-up set (open_case, run_case, the review page's close) or written by hand; no test calls a model or the network."""
import builtins
import io
import json
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from aid import start
from aid.case import Case, open_case
from aid.evalscripts import case_runs
from aid.evalscripts.case_runs import (AID_RUNS, WITHHELD, ListedCase, aid_running, counted, figures_of, folders, holds_identifier,
                                       output_of, read_case_list, runs_of, snapshot, state_of)
from aid.pipeline import NO_STORED_VERSION, CaseResult, Deps, run_case
from aid.po_store import PoStore
from aid.review.app import decide
from aid.review.close import close_case, finish_replaced
from aid.review.status import START_STATE
from aid.tests.conftest import deps
from aid.tests.fakes import case_result
from aid.tests.helpers import confirmation_again
from aid.versioning import VersionChoice

NINE = datetime(2026, 11, 12, 9, 0)                  # the clock of deps(), and the time the first case of a test is opened
FIRST = "250101_20261112T090000"                     # the folder of a case of the made-up order 250101 opened at NINE
SHA = "ab" * 32                                      # the sha256 of a made-up document of a folder written by hand
WAITS = VersionChoice(version=None, method="none", reason=NO_STORED_VERSION, confidence=0.0)     # no version of the PO is stored
NOT_KEPT = "run again in the same folder (or no audit record): the first run's output is not kept"


def listed(tmp_path: Path, text: str, encoding: str = "utf-8") -> Path:
    """A case list with this text under tmp_path, in the encoding."""
    path = tmp_path / "case_list.csv"
    path.write_bytes(text.encode(encoding))
    return path


# --- the case list ---

def test_the_case_list_of_the_batch_is_a_valid_case_list(tmp_path):
    """cases.csv of aid/batch.py has case_id, document, po_number and kind. The kind is a confirmation when it is left out, and a
    document is looked for from the folder of the case list, as the batch does."""
    path = listed(tmp_path, "case_id,document,po_number,kind\nC01,docs/first.pdf,250101,confirmation\nC02,,250201,cart\nC03,,250102,\n")
    rows = read_case_list(path)
    assert [(row.case_id, row.po_number, row.kind, row.month, row.folder, row.document) for row in rows] == [
        ("C01", "250101", "confirmation", "", "", "docs/first.pdf"), ("C02", "250201", "cart", "", "", ""),
        ("C03", "250102", "confirmation", "", "", "")]
    assert rows[0].document_path == tmp_path / "docs" / "first.pdf" and rows[1].document_path is None
    assert ListedCase("C04", "250104", "confirmation", "", "", "first.pdf").document_path == Path("first.pdf")     # a row made by hand


@pytest.mark.parametrize("encoding", ["utf-8-sig", "cp1252"])
def test_a_case_list_saved_by_a_spreadsheet_is_read_whatever_its_delimiter_and_code_page(tmp_path, encoding):
    """Semicolons, a byte order mark or the Windows code page, cells with spaces and non-breaking spaces around them, a column title
    in capitals, and a row that is empty in every cell."""
    text = ("Case_ID;po_number;month;folder\r\n C01\u00a0;250101 ;M\u00e4rz;\r\n;;;\r\n"
            "C02;250102;april;250102_20261112T090000\r\n")
    rows = read_case_list(listed(tmp_path, text, encoding))
    assert [(row.case_id, row.po_number, row.kind, row.month, row.folder) for row in rows] == [
        ("C01", "250101", "confirmation", "M\u00e4rz", ""), ("C02", "250102", "confirmation", "april", "250102_20261112T090000")]


@pytest.mark.parametrize("text,sentence", [
    ("case_id,po_number\nC01,250101\nC02,250102\nC01,250103\n", "case id C01 stands twice (rows 2 and 4)"),
    ("case_id,po_number\nC01,250101\n,250102\n", "row 3 has no case id"),
    ("id,po_number\nC01,250101\n", "has no column case_id"),
    ("case_id\nC01\n", "has no column po_number"),
    ("case_id,po_number,kind\nC01,250101,offer\n", "row 2 (case C01) has the kind 'offer': it is confirmation or cart"),
    ("case_id,po_number\nC01,250101\nC02,250101\n", "cases C01 and C02 have the same purchase order and kind: name the folder of each"),
    ("case_id,po_number,folder\nC01,250101,250101_20261112T090000\nC02,250101,250101_20261112T090000\n",
     "cases C01 and C02 name the same folder"),
])
def test_a_case_list_that_cannot_be_the_key_is_refused_with_the_sentence(tmp_path, text, sentence):
    """The refusals name the file, the row and the case id, and never a purchase order number or a folder."""
    with pytest.raises(ValueError) as refused:
        read_case_list(listed(tmp_path, text))
    message = str(refused.value)
    assert sentence in message and message.startswith("case_list.csv") and "2501" not in message


def test_one_purchase_order_under_two_case_ids_is_read_when_each_names_its_folder(tmp_path):
    """Two documents of one purchase order can be two cases, but only when the case list says which folder is which."""
    text = "case_id,po_number,folder\nC01,250101,250101_20261112T090000\nC02,250101,250101_20261112T090500\n"
    assert [row.case_id for row in read_case_list(listed(tmp_path, text))] == ["C01", "C02"]
    text = "case_id,po_number,kind\nC01,250101,\nC02,250101,cart\nC03,,\nC04,,\n"      # a confirmation and a cart; and no number at all
    assert [row.case_id for row in read_case_list(listed(tmp_path, text))] == ["C01", "C02", "C03", "C04"]


def test_a_case_list_that_cannot_be_read_as_text_or_at_all_is_refused_plainly(tmp_path, monkeypatch):
    path = tmp_path / "case_list.csv"
    with pytest.raises(ValueError, match=r"^case_list\.csv does not exist$"):
        read_case_list(path)
    path.write_bytes(b"case_id,po_number\nC01,25\x81\n")                # no UTF-8 and no text of the Windows code page
    with pytest.raises(ValueError, match=r"^case_list\.csv is not text in UTF-8: save the file as CSV UTF-8$"):
        read_case_list(path)

    def held(self):
        raise PermissionError(13, "Permission denied", str(self))
    monkeypatch.setattr(Path, "read_bytes", held)
    with pytest.raises(ValueError, match=r"^case_list\.csv is open in another program \(Excel\?\): close it and run again$"):
        read_case_list(path)


# --- the folders of a cases root ---

@pytest.fixture
def full(tmp_path, madeup_out) -> Deps:
    """The dependencies of a run on the made-up set whose store holds every made-up purchase order; the cases under tmp_path/cases."""
    return deps(tmp_path, madeup_out)


def opened(full: Deps, document: Path, po_number: str | None, minutes: int = 0, kind: str = "confirmation") -> Case:
    """A case opened from the document, `minutes` after NINE."""
    return open_case(full.settings.cases_root, kind, [document], po_number, NINE + timedelta(minutes=minutes))


def ran(full: Deps, document: Path, po_number: str | None, minutes: int = 0, kind: str = "confirmation") -> Case:
    """A case opened from the document, `minutes` after NINE, and run."""
    case = opened(full, document, po_number, minutes, kind)
    run_case(case, full)
    return case


def by_hand(root: Path, name: str, *, kind: str = "confirmation", po_number: str | None = "250101", opened_at: str = "2026-11-12T09:00:00",
            sha256: str = SHA, audit: dict | None = None, closes: list[dict] | None = None) -> Path:
    """A case folder written by hand under root: its case.json and, when given, its audit.json and the records of its
    steps/09_close.json. Returns the folder."""
    folder = root / name
    (folder / "steps").mkdir(parents=True)
    meta = {"case_id": name, "kind": kind, "po_number": po_number, "opened_at": opened_at,
            "files": [{"name": "document.pdf", "sha256": sha256, "bytes": 1}]}
    (folder / "case.json").write_text(json.dumps(meta), encoding="utf-8")
    if audit is not None:
        (folder / "audit.json").write_text(json.dumps(audit), encoding="utf-8")
    if closes is not None:
        (folder / "steps" / "09_close.json").write_text(json.dumps({"records": closes}), encoding="utf-8")
    return folder


def case(case_id: str = "C01", po_number: str = "250101", kind: str = "confirmation", folder: str = "", document: str = "",
         base: Path = Path(".")) -> ListedCase:
    """A row of a case list."""
    return ListedCase(case_id, po_number, kind, "", folder, document, base)


def names(found) -> list[str]:
    """The folder names of the runs, in their order."""
    return [run.folder.name for run in found.runs]


def test_every_readable_case_folder_is_read_and_the_others_are_counted(full, madeup_out):
    """A folder is a case when it holds a case.json. One whose case.json is no JSON, or lacks what the runs are told by, is counted
    and passed over; a folder without one (the prepared changes) and a file are no cases and are not counted."""
    root = full.settings.cases_root
    ran(full, madeup_out / "MU01_conf.pdf", "250101")
    (root / "prepared").mkdir()
    (root / "prepared" / "prepared_changes.csv").write_text("po_number\n", encoding="utf-8")
    (root / "digest_state.json").write_text("{}", encoding="utf-8")
    (root / "250102_20261112T090100").mkdir()
    (root / "250102_20261112T090100" / "case.json").write_text("{torn", encoding="utf-8")
    without_time = by_hand(root, "250103_20261112T090200")
    meta = json.loads((without_time / "case.json").read_text(encoding="utf-8"))
    (without_time / "case.json").write_text(json.dumps({key: value for key, value in meta.items() if key != "opened_at"}), encoding="utf-8")
    found = folders(root)
    assert [folder.name for folder in found.read] == [FIRST] and found.unreadable == 2 and found.root == root
    folder = found.read[0]
    assert (folder.path, folder.kind, folder.po_number) == (root / FIRST, "confirmation", "250101")
    assert folder.opened_at == NINE.astimezone() and folder.opened_at.utcoffset() is not None     # a time without offset is local time
    assert [name for name, _ in folder.files] == ["MU01_conf.pdf"] and all(len(sha256) == 64 for _, sha256 in folder.files)
    assert folders(root / "nowhere").read == () and folders(root / "nowhere").unreadable == 0


# --- the runs of a case ---

def test_a_case_with_one_run_has_that_run(full, madeup_out):
    ran(full, madeup_out / "MU01_conf.pdf", "250101")
    ran(full, madeup_out / "MU02_conf.pdf", "250102", minutes=1)
    found = runs_of(case(), folders(full.settings.cases_root))
    assert names(found) == [FIRST] and found.documents == 1 and found.reason == ""


def test_a_case_without_a_run_says_so(full, madeup_out):
    """No folder of the purchase order, a cart of the same order (another control point), and a case list row without anything to
    find a folder by."""
    ran(full, madeup_out / "MU02_conf.pdf", "250102")
    by_hand(full.settings.cases_root, "250101_20261112T090500", kind="cart")
    for listed_case in (case(), case(po_number="")):
        found = runs_of(listed_case, folders(full.settings.cases_root))
        assert found.runs == () and found.documents == 0 and found.reason == "no run of the aid found"


def test_the_runs_of_one_document_stand_in_the_order_they_were_opened(tmp_path):
    """The order is the time of opening, then the folder name. A time without offset is local time, as the runner reads it
    (aid/runner.py, _identity), so that a folder of the runner and one of the batch, whose times carry an offset, compare."""
    root = tmp_path / "cases"
    local = NINE.astimezone()
    by_hand(root, "250101_c", opened_at="2026-11-12T09:00:00")                                    # the runner's clock: no offset
    by_hand(root, "250101_a", opened_at=(local + timedelta(minutes=1)).astimezone(timezone.utc).isoformat())      # the batch's
    by_hand(root, "250101_b", opened_at=(local - timedelta(minutes=1)).astimezone(timezone(timedelta(hours=5))).isoformat())
    by_hand(root, "unknown_d", po_number=None, opened_at="2026-11-12T09:01:00")                   # the same moment as 250101_a
    found = runs_of(case(), folders(root))
    assert names(found) == ["250101_b", "250101_c", "250101_a", "unknown_d"] and found.documents == 1


def test_a_first_run_opened_without_a_purchase_order_number_is_a_run_of_its_document(full, madeup_out):
    """The runner opens a new folder for the same files once the purchase order is known (aid/runner.py, _recheck_waiting): the two
    folders are the runs of one document, though only the second carries the number."""
    ran(full, madeup_out / "MU01_conf.pdf", None)
    ran(full, madeup_out / "MU01_conf.pdf", "250101", minutes=5)
    found = runs_of(case(), folders(full.settings.cases_root))
    assert names(found) == ["unknown_20261112T090000", "250101_20261112T090500"] and found.documents == 1 and found.reason == ""


def test_two_documents_for_one_purchase_order_are_not_guessed(full, madeup_out, tmp_path):
    """A second confirmation of the order is another document, not a run: the case list has to say which folder is the case."""
    ran(full, madeup_out / "MU01_conf.pdf", "250101")
    again = confirmation_again(tmp_path / "MU01_again_conf.pdf", "MU01", {"1": "13.00"})
    ran(full, again, "250101", minutes=5)
    ran(full, again, "250101", minutes=9)
    found = runs_of(case(), folders(full.settings.cases_root))
    assert found.runs == () and found.documents == 2
    assert found.reason == "2 documents for this purchase order: name the folder in the case list"


def test_a_folder_named_in_the_case_list_gives_the_runs_of_its_document(full, madeup_out, tmp_path):
    ran(full, madeup_out / "MU01_conf.pdf", "250101")
    again = confirmation_again(tmp_path / "MU01_again_conf.pdf", "MU01", {"1": "13.00"})
    ran(full, again, "250101", minutes=5)
    ran(full, again, "250101", minutes=9)
    read = folders(full.settings.cases_root)
    assert names(runs_of(case(folder=FIRST), read)) == [FIRST]
    second = runs_of(case(folder="250101_20261112T090900"), read)
    assert names(second) == ["250101_20261112T090500", "250101_20261112T090900"] and second.documents == 1 and second.reason == ""
    absent = runs_of(case(folder="250101_20261112T100000"), read)
    assert absent.runs == () and absent.reason == "the folder named in the case list is no case folder that can be read"
    other = runs_of(case(kind="cart", folder=FIRST), read)
    assert other.runs == () and other.reason == "the folder named in the case list holds a case of another kind"


def test_the_document_of_the_case_list_finds_its_runs_by_its_content(full, madeup_out, tmp_path):
    """The document is told by its sha256, whatever its name: with it the runs are found also when the order has two documents,
    and a document that was never run has no run, whatever else was run for its order. A document that is not there leaves the
    purchase order number to find the runs."""
    ran(full, madeup_out / "MU01_conf.pdf", "250101")
    again = confirmation_again(tmp_path / "MU01_again_conf.pdf", "MU01", {"1": "13.00"})
    ran(full, again, "250101", minutes=5)
    read = folders(full.settings.cases_root)
    copy = tmp_path / "list" / "documents" / "renamed.pdf"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(again.read_bytes())
    assert names(runs_of(case(document="documents/renamed.pdf", base=tmp_path / "list"), read)) == ["250101_20261112T090500"]
    assert names(runs_of(case(document=str(madeup_out / "MU01_conf.pdf")), read)) == [FIRST]
    never = runs_of(case(document=str(madeup_out / "MU03_conf.pdf")), read)
    assert never.runs == () and never.documents == 0 and never.reason == "no run of the aid found"
    assert runs_of(case(document="documents/gone.pdf", base=tmp_path / "list"), read).documents == 2


# --- the run that counts, and its unreviewed output ---

def recorded(*steps: tuple[str, dict], result: CaseResult | None = None, **more) -> dict:
    """An audit record written by hand: these steps by name and payload, then the result's, and what else is given."""
    payloads = [*steps, *([("result", result.model_dump(mode="json"))] if result is not None else [])]
    return {"steps": [{"name": name, "seconds": 0.0, "payload": payload} for name, payload in payloads]} | more


def waited(name: str) -> dict:
    """The audit record of a run that is in review because no version of its purchase order was stored."""
    result = case_result(name, "review").model_copy(update={"version": WAITS})
    return recorded(("version", WAITS.model_dump(mode="json")), ("match", {"skipped": "no version selected"}),
                    ("compare", {"skipped": "no version selected"}), result=result)


def replaced_by(name: str) -> list[dict]:
    """The records of a folder that was finished as replaced by the folder of this name."""
    return [{"action": "replaced", "at": "2026-11-12T09:05:00", "by": name}]


def the_run(root: Path, listed_case: ListedCase | None = None):
    """The run that counts of the case (by default the case of the made-up order 250101) under the cases root."""
    return counted(runs_of(listed_case or case(), folders(root)).runs).run


def test_the_first_run_counts_and_its_output_is_what_its_audit_record_holds(full, madeup_out):
    """The unreviewed output is the payloads of the steps version, entered, match, compare and result of audit.json, and the run's
    own status that of the result payload. MU07 has one price flag, which goes to the buyer."""
    one = ran(full, madeup_out / "MU07_conf.pdf", "250107")
    found = runs_of(case(po_number="250107"), folders(full.settings.cases_root))
    the = counted(found.runs)
    assert the.run is found.runs[0] and the.label == "run 1 of 1" and the.note == ""
    out = output_of(the.run)
    steps = {step["name"]: step["payload"] for step in json.loads((one.root / "audit.json").read_bytes())["steps"]}
    assert out.kept and out.reason == "" and out.lacks == "" and out.status == "review"
    assert (out.version, out.match, out.compare, out.result) == (steps["version"], steps["match"], steps["compare"], steps["result"])
    assert out.entered is None                                            # no value entered in Exact was laid over the version
    assert (out.choice.version, out.choice.method) == (1, "reference") and [flag.flag_id for flag in out.findings.flags] == ["F1"]
    assert out.pairs.pairs and out.case_result.status == "review"
    assert the.run.payload("read_document")["reader"] == "parser:table_de" and the.run.payload("no_such_step") is None


def test_a_run_that_waited_for_its_purchase_order_and_was_replaced_does_not_count(full, madeup_out, tmp_path):
    """The first run met a store without the order: no version, in review. Once the order is filed the runner opens a new folder
    for the same document and finishes the first as replaced (aid/runner.py, _recheck_waiting): the second run is the one that
    counts, and the outputs say which run it is, never a folder."""
    empty = full.model_copy(update={"store": PoStore(tmp_path / "empty_store")})
    first = opened(full, madeup_out / "MU01_conf.pdf", "250101")
    run_case(first, empty)
    second = opened(full, madeup_out / "MU01_conf.pdf", "250101", minutes=5)
    finish_replaced(first, second.case_id, NINE + timedelta(minutes=5), "nl")
    run_case(second, full)
    found = runs_of(case(), folders(full.settings.cases_root))
    the = counted(found.runs)
    assert names(found) == [first.case_id, second.case_id] and the.run is found.runs[1]
    assert the.label == "run 2 of 2" and the.note == "run 1 waited for its purchase order"
    assert output_of(the.run).status == "closed" and output_of(the.run).lacks == ""
    before = output_of(found.runs[0])                                     # the first run's own status, though its folder says closed now
    assert before.kept and before.status == "review" and before.lacks == "no version selected"
    assert json.loads((first.root / "steps" / "08_result.json").read_bytes())["status"] == "closed"


def test_a_run_that_still_waits_for_its_purchase_order_counts_and_has_no_version(full, madeup_out, tmp_path):
    empty = full.model_copy(update={"store": PoStore(tmp_path / "empty_store")})
    run_case(opened(full, madeup_out / "MU01_conf.pdf", "250101"), empty)
    the = counted(runs_of(case(), folders(full.settings.cases_root)).runs)
    out = output_of(the.run)
    assert the.label == "run 1 of 1" and the.note == "" and out.kept and out.status == "review" and out.lacks == "no version selected"
    assert out.choice.version is None and out.compare == {"skipped": "no version selected"} and out.findings is None and out.pairs is None


def test_only_a_run_that_both_waited_and_was_replaced_is_passed_over(tmp_path):
    """Two runs that waited and were replaced before the third; and a run that was replaced without waiting (a case of the capture
    server), which counts as the first run it is."""
    root = tmp_path / "cases"
    by_hand(root, "250101_a", opened_at="2026-11-12T09:00:00", audit=waited("250101_a"), closes=replaced_by("250101_b"))
    by_hand(root, "250101_b", opened_at="2026-11-12T09:05:00", audit=waited("250101_b"), closes=replaced_by("250101_c"))
    by_hand(root, "250101_c", opened_at="2026-11-12T09:09:00", audit=recorded(result=case_result("250101_c", "closed")))
    the = counted(runs_of(case(), folders(root)).runs)
    assert the.run.folder.name == "250101_c" and the.label == "run 3 of 3"
    assert the.note == "run 1 waited for its purchase order; run 2 waited for its purchase order"
    by_hand(root, "250201_a", kind="cart", po_number="250201", sha256="cd" * 32, audit=recorded(result=case_result("250201_a", "review")),
            closes=replaced_by("250201_b"))
    by_hand(root, "250201_b", kind="cart", po_number="250201", sha256="cd" * 32, opened_at="2026-11-12T09:05:00",
            audit=recorded(result=case_result("250201_b", "closed")))
    cart = counted(runs_of(case(po_number="250201", kind="cart"), folders(root)).runs)
    assert cart.run.folder.name == "250201_a" and cart.label == "run 1 of 2" and cart.note == ""


def test_a_case_without_a_run_that_counts_has_none(tmp_path):
    """No runs at all; and a run that waited and was replaced whose new folder is not among the runs (its case.json is torn)."""
    root = tmp_path / "cases"
    nothing = counted(())
    assert nothing.run is None and nothing.label == "" and nothing.note == ""
    by_hand(root, "250101_a", audit=waited("250101_a"), closes=replaced_by("250101_b"))
    alone = counted(runs_of(case(), folders(root)).runs)
    assert alone.run is None and alone.label == "" and alone.note == "run 1 waited for its purchase order; no later run found"


def test_a_failed_run_counts_and_has_no_output(full, madeup_out):
    """The parser has no template for the scan MU05: the run fails at reading, before anything is compared."""
    ran(full, madeup_out / "MU05_conf.pdf", "250105")
    the = counted(runs_of(case(po_number="250105"), folders(full.settings.cases_root)).runs)
    out = output_of(the.run)
    assert the.label == "run 1 of 1" and out.kept and out.status == "failed" and out.lacks == "no output"
    assert (out.version, out.entered, out.match, out.compare) == (None, None, None, None) and out.result["status"] == "failed"
    assert (out.choice, out.pairs, out.findings) == (None, None, None) and out.case_result.status == "failed"


def test_the_buyers_close_and_the_step_files_do_not_change_the_output_of_the_run(full, madeup_out):
    """The close of the review page rewrites steps/08_result.json and adds its record to the audit record, whose steps stay as the
    run left them. A step file is never read: one that a later attempt left is in no audit record."""
    one = ran(full, madeup_out / "MU07_conf.pdf", "250107")
    decide(one, "RI1", "keep")
    assert close_case(one, full).status == "closed"
    (one.root / "steps" / "05_compare.json").write_text('{"version": 9, "flags": [], "matches": [], "uncompared": []}', encoding="utf-8")
    out = output_of(the_run(full.settings.cases_root, case(po_number="250107")))
    assert out.kept and out.status == "review" and [flag.flag_id for flag in out.findings.flags] == ["F1"] and out.findings.version == 1
    assert json.loads((one.root / "steps" / "08_result.json").read_bytes())["status"] == "closed"


def test_a_folder_run_again_in_place_does_not_keep_its_first_output(full, madeup_out):
    """run_case(rerun=True) writes a new audit record over the first and leaves steps/09_close.json: the records of closes that
    the audit record lacks tell that the output in it is not the first run's."""
    one = ran(full, madeup_out / "MU07_conf.pdf", "250107")
    decide(one, "RI1", "keep")
    close_case(one, full)
    assert output_of(the_run(full.settings.cases_root, case(po_number="250107"))).kept
    run_case(one, full, rerun=True)
    out = output_of(the_run(full.settings.cases_root, case(po_number="250107")))
    assert not out.kept and out.reason == NOT_KEPT and out.lacks == "first output not kept" and out.status is None
    assert (out.version, out.entered, out.match, out.compare, out.result) == (None,) * 5
    assert (out.choice, out.pairs, out.findings, out.case_result) == (None,) * 4


@pytest.mark.parametrize("content", [None, "{torn", "[]", '{"steps": "none"}', '{"steps": [{"name": "result"}]}'])
def test_a_folder_without_an_audit_record_that_can_be_read_keeps_no_output(tmp_path, content):
    root = tmp_path / "cases"
    folder = by_hand(root, "250101_a")
    if content is not None:
        (folder / "audit.json").write_text(content, encoding="utf-8")
    out = output_of(the_run(root))
    assert not out.kept and out.reason == NOT_KEPT and out.status is None and out.result is None


def test_what_cannot_be_checked_or_understood_is_not_given_as_the_output(tmp_path):
    """Records of closes that cannot be understood leave open whether the folder was run again; and a payload that is not what
    the aid's own models read (another version of the program) is not passed on as if it were."""
    root = tmp_path / "cases"
    folder = by_hand(root, "250101_a", audit=recorded(result=case_result("250101_a", "closed")), closes=[])
    assert output_of(the_run(root)).kept
    (folder / "steps" / "09_close.json").write_text('{"records": "none"}', encoding="utf-8")
    out = output_of(the_run(root))
    assert not out.kept and out.status is None
    assert out.reason == "the records of the closes cannot be read: whether the first run's output is kept cannot be told"
    by_hand(root, "250102_a", po_number="250102", sha256="cd" * 32,
            audit=recorded(("compare", {"flags": "many"}), result=case_result("250102_a", "review")))
    out = output_of(the_run(root, case(po_number="250102")))
    assert not out.kept and out.status is None and out.compare is None
    assert out.reason == "the audit record does not have the form this copy of the program reads: no output is taken from it"


def test_the_values_entered_in_exact_are_part_of_the_output(tmp_path):
    """The step entered is recorded only when the run laid a value entered in Exact over the selected version."""
    root = tmp_path / "cases"
    entered = {"version": 1, "replaced": [{"line_id": "1", "field": "price", "was": "12.50", "entered": "13.00",
                                           "marked_at": "2026-11-11T10:00:00", "by": "buyer"}]}
    by_hand(root, "250101_a", audit=recorded(("entered", entered), result=case_result("250101_a", "closed")))
    assert output_of(the_run(root)).entered == entered


# --- the figures of a run ---

def ticking(first: datetime, seconds: float):
    """A clock that gives `first` when it is asked the first time and a moment `seconds` later every time after."""
    asked = []

    def clock() -> datetime:
        asked.append(1)
        return first if len(asked) == 1 else first + timedelta(seconds=seconds)
    return clock


def test_the_figures_of_a_run_come_from_its_audit_record(full, madeup_out):
    """A run by a clock with an offset, as the batch's: the seconds are the audit record's own from start to finish. The parser
    asks no model, so the run has no call attempt and no cost, and the reader is named by its kind, without its template."""
    began = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)
    one = opened(full, madeup_out / "MU01_conf.pdf", "250101")
    run_case(one, full.model_copy(update={"clock": ticking(began, 4.2)}))
    figures = figures_of(the_run(full.settings.cases_root))
    assert figures.seconds == 4.2 and figures.call_attempts == 0 and figures.cost_eur == Decimal("0") and figures.reader == "parser"
    assert figures.started_at == began and figures.remarks == ()


def test_a_run_by_a_clock_without_offset_has_its_seconds_from_its_two_times(full, madeup_out):
    """The runner and the review page run by datetime.now, without a zone: the audit record then holds no seconds from start to
    finish (aid/audit.py), and the two times, which one clock gave, are taken instead and the remarks say so."""
    one = opened(full, madeup_out / "MU01_conf.pdf", "250101")
    run_case(one, full.model_copy(update={"clock": ticking(NINE, 4.2)}))
    assert json.loads((one.root / "audit.json").read_bytes())["timings"]["wall_seconds"] is None
    figures = figures_of(the_run(full.settings.cases_root))
    assert figures.seconds == 4.2 and figures.remarks == ("seconds by a clock without offset",)
    assert figures.started_at == NINE.astimezone() and figures.started_at.utcoffset() is not None


def test_seconds_that_no_two_times_give_are_the_sum_of_the_steps_or_unknown(tmp_path):
    root = tmp_path / "cases"
    mixed = {"started_at": "2026-11-12T09:00:00", "finished_at": "2026-11-12T09:00:05+00:00"}
    by_hand(root, "250101_a", audit=recorded(**mixed, timings={"read_document": 3.5, "total": 3.75, "wall_seconds": None}))
    figures = figures_of(the_run(root))
    assert figures.seconds == 3.75 and figures.remarks == ("seconds: sum of steps",)
    by_hand(root, "250102_a", po_number="250102", sha256="cd" * 32, audit=recorded(**mixed))
    figures = figures_of(the_run(root, case(po_number="250102")))
    assert figures.seconds is None and figures.remarks == ("seconds unknown",)


READER_CALL = {"kind": "reader", "model": "made-up-model", "input_tokens": 900, "output_tokens": 300, "cost_eur": "0.0125", "seconds": 3.1,
               "request_id": "r1", "attempts": 3}
DECIDER_CALL = {"kind": "decider", "source": "model:made-up-model", "input_tokens": 200, "output_tokens": 20, "cost_eur": "0.0005",
                "seconds": 1.0, "request_id": "r2"}
NO_DECIDER = {"kind": "decider", "source": "none", "input_tokens": None, "output_tokens": None, "cost_eur": None, "seconds": None,
              "request_id": None}
TIMED = {"started_at": "2026-11-12T09:00:00+00:00", "finished_at": "2026-11-12T09:00:05+00:00",
         "timings": {"total": 4.0, "wall_seconds": 5.0}}


@pytest.mark.parametrize("calls,attempts,cost,remarks", [
    ([], 0, Decimal("0"), ()),
    ([NO_DECIDER, NO_DECIDER], 0, Decimal("0"), ()),                       # the answers of the aid without a model are no calls
    ([READER_CALL, {**READER_CALL, "attempts": None, "cost_eur": "0.004"}, DECIDER_CALL, NO_DECIDER], 5, Decimal("0.0170"), ()),
    ([{key: value for key, value in READER_CALL.items() if key != "attempts"}], 1, Decimal("0.0125"), ()),
    ([READER_CALL, {**DECIDER_CALL, "cost_eur": None}], 4, None, ("cost unknown for 1 call",)),
    ([{**READER_CALL, "cost_eur": None}, {**DECIDER_CALL, "cost_eur": None}, DECIDER_CALL], 5, None, ("cost unknown for 2 calls",)),
])
def test_the_call_attempts_and_the_cost_of_a_run_count_its_model_calls(tmp_path, calls, attempts, cost, remarks):
    """A reader call counts its attempts, one when the record has none; a decider call of a model counts one; the answer of the
    aid without a model is no call. The cost is added up as decimals, and is unknown only when a model call has none."""
    root = tmp_path / "cases"
    by_hand(root, "250101_a", audit=recorded(calls=calls, **TIMED))
    figures = figures_of(the_run(root))
    assert (figures.call_attempts, figures.cost_eur, figures.remarks) == (attempts, cost, remarks) and figures.seconds == 5.0


@pytest.mark.parametrize("reader,kind", [("parser:table_nl", "parser"), ("llm:recorded:v1", "llm"), ("cart_html", "cart_html"), (None, "")])
def test_the_reader_of_a_run_is_named_by_its_kind_only(tmp_path, reader, kind):
    """The name of a parser template may be a supplier's: only what stands before the first colon is kept. A run that failed at
    reading names no reader."""
    root = tmp_path / "cases"
    steps = [("read_document", {"kind": "confirmation", "reader": reader})] if reader is not None else []
    by_hand(root, "250101_a", audit=recorded(*steps, **TIMED))
    assert figures_of(the_run(root)).reader == kind


def test_a_folder_without_an_audit_record_has_no_figures_and_one_run_again_says_whose_they_are(tmp_path):
    root = tmp_path / "cases"
    by_hand(root, "250101_a")
    figures = figures_of(the_run(root))
    assert (figures.seconds, figures.call_attempts, figures.cost_eur, figures.reader, figures.started_at) == (None, None, None, "", None)
    assert figures.remarks == ("no audit record",)
    by_hand(root, "250102_a", po_number="250102", sha256="cd" * 32, audit=recorded(calls=[READER_CALL], **TIMED),
            closes=[{"action": "close", "at": "2026-11-12T10:00:00"}])
    figures = figures_of(the_run(root, case(po_number="250102")))
    assert figures.call_attempts == 3 and figures.remarks == ("run again in the same folder: the figures are those of the last run",)


# --- the present state of a folder ---

def test_the_present_state_of_a_folder_says_who_closed_it(full, madeup_out, tmp_path):
    """The state is the one the review page shows (aid/review/status.py), and closed_by tells a case the aid closed by its rules
    from one the buyer closed and from a folder that another replaced. The run's own status stays what the run left."""
    root = full.settings.cases_root
    empty = full.model_copy(update={"store": PoStore(tmp_path / "empty_store")})
    first = opened(full, madeup_out / "MU01_conf.pdf", "250101")
    run_case(first, empty)
    second = opened(full, madeup_out / "MU01_conf.pdf", "250101", minutes=5)
    finish_replaced(first, second.case_id, NINE + timedelta(minutes=5), "nl")
    run_case(second, full)
    review = ran(full, madeup_out / "MU07_conf.pdf", "250107", minutes=6)
    run_case(opened(full, madeup_out / "MU02_conf.pdf", "250102", minutes=7), empty)
    ran(full, madeup_out / "MU05_conf.pdf", "250105", minutes=8)
    opened(full, madeup_out / "MU03_conf.pdf", "250103", minutes=9)

    def states(po_number: str) -> list[tuple[str, str]]:
        return [(state.state, state.closed_by) for state in state_of(root, runs_of(case(po_number=po_number), folders(root)).runs)]
    assert states("250101") == [("", "replaced"), ("closed_itself", "rule")]
    assert states("250107") == [("review", "")]
    assert states("250102") == [("waiting_po", "")] and states("250105") == [("failed", "")] and states("250103") == [("reading", "")]
    decide(review, "RI1", "keep")
    close_case(review, full)
    assert states("250107") == [("closed_by_you", "buyer")]
    assert output_of(the_run(root, case(po_number="250107"))).status == "review"
    assert state_of(root, ()) == []


# --- the aid that runs on the data folder ---

def test_the_aid_runs_on_a_data_folder_while_the_heartbeat_of_its_launcher_is_fresh(tmp_path, monkeypatch):
    """The launcher writes start_state.json next to the settings file, the folder above the cases root, every five seconds
    (aid/start.py). A heartbeat younger than 30 seconds is an aid that runs; an older one, or a file that says nothing, is none."""
    root = tmp_path / "data" / "cases"
    root.mkdir(parents=True)
    state = tmp_path / "data" / start.STATE_FILE
    assert not aid_running(root, now=1_000_000.0)

    def beat(heartbeat) -> None:
        launcher = {"pid": 4242, "started": "2026-11-12T08:00:00", "heartbeat": heartbeat, "heartbeat_at": "2026-11-12T09:00:00"}
        state.write_text(json.dumps({"launcher": launcher, "parts": {}}), encoding="utf-8")
    beat(1_000_000.0 - 29.0)
    assert aid_running(root, now=1_000_000.0)
    beat(1_000_000.0 - 31.0)
    assert not aid_running(root, now=1_000_000.0)
    beat(time.time())
    assert aid_running(root)                                              # by the clock of this PC when no time is given
    for nothing in (True, None, "soon"):
        beat(nothing)
        assert not aid_running(root, now=1.0)
    state.write_text("{torn", encoding="utf-8")
    assert not aid_running(root, now=1_000_000.0)
    read = Path.read_bytes

    def held(self):
        if self == state:
            raise PermissionError(13, "Permission denied", str(self))
        return read(self)
    monkeypatch.setattr(Path, "read_bytes", held)
    assert aid_running(root, now=1_000_000.0)                             # a state file that something holds: an aid at work
    assert AID_RUNS == "the aid runs on this data folder: stop it first, or give --while-running"     # the refusal of the records


def test_whether_the_aid_runs_can_be_checked_only_beside_a_settings_file_or_a_state_file(tmp_path):
    """The cases root is a setting and may lie elsewhere: without a settings file or the launcher's state file in the folder above
    it, nothing says whether the aid runs."""
    root = tmp_path / "data" / "cases"
    root.mkdir(parents=True)
    assert not case_runs.aid_checkable(root)
    (tmp_path / "data" / "settings.json").write_text("{}", encoding="utf-8")
    assert case_runs.aid_checkable(root)
    (tmp_path / "data" / "settings.json").unlink()
    (tmp_path / "data" / START_STATE).write_text("{}", encoding="utf-8")
    assert case_runs.aid_checkable(root)
    assert case_runs.UNCHECKED_RUNNING.startswith("No settings file stands beside the cases folder")


# --- identifying text ---

def test_a_free_text_that_holds_identifying_text_is_told(full, madeup_out):
    """What the researcher typed (a deviation, a note of the timer) goes into a record only when it names no purchase order of
    the case list, no case folder, no input file by its name or its stem, and no supplier of a run."""
    ran(full, madeup_out / "MU01_conf.pdf", "250101")
    read = folders(full.settings.cases_root)
    cases = [case("C01", "250101"), case("C02", "250102", document="documents/Offer Example.pdf")]
    runs = runs_of(cases[0], read).runs
    assert holds_identifier("called about order 250101 today", cases, runs)
    assert holds_identifier("PO250102: price asked again", cases, ())      # a number of the case list, also without any run
    assert holds_identifier("see folder 250101_20261112t090000", [], runs)                       # whatever the capitals
    assert holds_identifier("opened mu01_conf.pdf twice", [], runs) and holds_identifier("the file MU01_conf was slow", [], runs)
    assert holds_identifier("the offer example was not attached", cases, ())                     # the document of the case list
    assert holds_identifier("S01 called back", [], runs)                                         # the supplier of the run's result
    assert holds_identifier("250101_20261112T090000", [], read.read)                             # folders in place of runs
    for harmless in ("paused for a phone call; resumed after 5 minutes", "", "C01 took long"):
        assert not holds_identifier(harmless, cases, runs)
    assert WITHHELD == "[withheld]" and not holds_identifier(WITHHELD, cases, runs)              # what a record holds in its place


def test_a_name_too_short_to_tell_anything_withholds_nothing(tmp_path):
    """An input file called 1.pdf has the stem 1: taken as identifying text, it would withhold every note that holds that digit."""
    root = tmp_path / "cases"
    folder = by_hand(root, "250101_a")
    meta = json.loads((folder / "case.json").read_text(encoding="utf-8"))
    meta["files"][0]["name"] = "1.pdf"
    (folder / "case.json").write_text(json.dumps(meta), encoding="utf-8")
    read = folders(root)
    assert not holds_identifier("paused at 11:15 for 1 minute", [], read.read) and holds_identifier("scan 1.pdf", [], read.read)


# --- reading only, and never with a handle left open ---

@pytest.fixture
def data_folder(full, madeup_out, tmp_path) -> Path:
    """A data folder under tmp_path/data as the aid leaves one: the settings and the state of a launcher that stopped, and under
    cases a run that waited for its order with the run that replaced it, a case the buyer closed and a failed run. Returns the
    cases root."""
    data = tmp_path / "data"
    here = full.model_copy(update={"settings": full.settings.model_copy(update={"cases_root": data / "cases"})})
    empty = here.model_copy(update={"store": PoStore(tmp_path / "empty_store")})
    first = opened(here, madeup_out / "MU01_conf.pdf", "250101")
    run_case(first, empty)
    second = opened(here, madeup_out / "MU01_conf.pdf", "250101", minutes=5)
    finish_replaced(first, second.case_id, NINE + timedelta(minutes=5), "nl")
    run_case(second, here)
    review = ran(here, madeup_out / "MU07_conf.pdf", "250107", minutes=6)
    decide(review, "RI1", "keep")
    close_case(review, here)
    ran(here, madeup_out / "MU05_conf.pdf", "250105", minutes=8)
    (data / "settings.json").write_text("{}", encoding="utf-8")
    (data / start.STATE_FILE).write_text(json.dumps({"launcher": {"heartbeat": 1.0}, "parts": {}}), encoding="utf-8")
    return data / "cases"


LISTED = [case("C01", "250101"), case("C05", "250105"), case("C07", "250107"), case("C09", "250109")]


def everything(root: Path) -> list:
    """Every function of the module on the cases root, as the records call them; what they gave, for a look."""
    read = folders(root)
    gave = [aid_running(root), read.unreadable]
    for listed_case in LISTED:
        found = runs_of(listed_case, read)
        the = counted(found.runs)
        gave += [found.reason, the.label, the.note, state_of(root, found.runs), holds_identifier("a note", LISTED, found.runs)]
        gave += [(output_of(run).lacks, figures_of(run).remarks, run.unread) for run in found.runs]
    return gave


def test_reading_leaves_the_data_folder_as_it_was(data_folder):
    """Names, sizes, content and modification times of everything under the data folder, the cases root included, are the same
    after every function of the module ran, twice."""
    before = snapshot(data_folder.parent)
    gave = everything(data_folder)
    assert everything(data_folder) == gave and snapshot(data_folder.parent) == before
    assert gave[:2] == [False, 0] and "run 2 of 2" in gave and "no run of the aid found" in gave


def test_every_file_of_a_case_folder_is_read_once_with_one_read_of_its_bytes(data_folder, monkeypatch):
    """On Windows the aid's own write to a file fails while another process has the file open (aid/case.py replaces a file as a
    whole and does not try again). So a file is read with one read_bytes and parsed from memory, no file is read twice, and no
    file under the data folder is opened for anything but reading; the present state is told by the review page's own reading
    (aid/review/status.py), which opens for reading only as well."""
    real_read, real_open = Path.read_bytes, io.open
    reading, opens = [], []

    def read_bytes(self):
        reading.append(self)
        try:
            return real_read(self)
        finally:
            reading.pop()

    def opening(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).is_relative_to(data_folder.parent):
            opens.append((Path(file).relative_to(data_folder.parent).as_posix(), mode, bool(reading)))
        return real_open(file, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(io, "open", opening)
    monkeypatch.setattr(builtins, "open", opening)
    read = folders(data_folder)
    for listed_case in LISTED * 2:
        found = runs_of(listed_case, read)
        gave = [counted(found.runs), holds_identifier("a note", LISTED, found.runs)]
        gave += [(output_of(run), figures_of(run)) for run in found.runs]
    assert gave and aid_running(data_folder) is False
    names = [name for name, _, _ in opens]
    assert all(mode == "rb" and inside for _, mode, inside in opens) and len(names) == len(set(names))
    folder = "cases/250107_20261112T090600"
    assert {f"{folder}/case.json", f"{folder}/audit.json", f"{folder}/steps/09_close.json", start.STATE_FILE} <= set(names)
    assert not any("/steps/" in name and "09_close" not in name for name in names)       # no step file of a run
    del opens[:]
    assert state_of(data_folder, runs_of(LISTED[2], read).runs)[0].closed_by == "buyer"
    assert opens and all(mode in ("r", "rb") for _, mode, _ in opens)


def test_a_file_that_cannot_be_read_now_is_counted_and_passed_over(data_folder, monkeypatch, tmp_path):
    """While the aid writes a file, or another program holds it, a read fails with an OSError. The folder is then passed over and
    counted, or the run says which of its files could not be read, by the file's name in the folder and never by the folder's."""
    document = tmp_path / "document.pdf"
    document.write_bytes(b"%PDF-1.4 made up")
    held = {data_folder / "250105_20261112T090800" / "case.json", data_folder / "250107_20261112T090600" / "audit.json",
            data_folder / "250101_20261112T090500" / "steps" / "09_close.json", document}
    real_read = Path.read_bytes

    def read_bytes(self):
        if self in held:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read(self)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    read = folders(data_folder)
    assert read.unreadable == 1 and len(read.read) == 3
    assert runs_of(LISTED[1], read).reason == "no run of the aid found"
    review = runs_of(LISTED[2], read).runs[0]
    assert review.unread == (("audit.json", "PermissionError"),) and review.audit is None
    assert not output_of(review).kept and figures_of(review).remarks == ("no audit record",)
    second = runs_of(LISTED[0], read).runs[1]
    assert second.unread == (("steps/09_close.json", "PermissionError"),) and second.closes is None
    assert output_of(second).reason == "the records of the closes cannot be read: whether the first run's output is kept cannot be told"
    assert counted(runs_of(LISTED[0], read).runs).run is second and figures_of(second).reader == "parser"
    unread = runs_of(case("C10", "250110", document=str(document)), read)
    assert unread.runs == () and unread.reason == "the document named in the case list cannot be read now"


# --- the tree under a folder, for the tests of the records ---

def test_a_snapshot_tells_every_change_under_a_folder(tmp_path):
    """Names, sizes, sha256 and modification times: what the tests hold a command that only reads to."""
    (tmp_path / "cases" / "one").mkdir(parents=True)
    file = tmp_path / "cases" / "one" / "case.json"
    file.write_text("{}", encoding="utf-8")
    before = snapshot(tmp_path / "cases")
    assert sorted(before) == [".", "one", "one/case.json"] and snapshot(tmp_path / "cases") == before
    stamp = file.stat().st_mtime_ns
    file.write_text("[]", encoding="utf-8")                              # the same size, another content
    os.utime(file, ns=(stamp, stamp))
    assert snapshot(tmp_path / "cases") != before
    file.write_text("{}", encoding="utf-8")
    os.utime(file, ns=(stamp + 1_000_000_000, stamp + 1_000_000_000))     # the same content, written a second later
    assert snapshot(tmp_path / "cases") != before
    os.utime(file, ns=(stamp, stamp))
    assert snapshot(tmp_path / "cases") == before
    (tmp_path / "cases" / "one" / "case.json.tmp").write_text("", encoding="utf-8")
    assert snapshot(tmp_path / "cases") != before
