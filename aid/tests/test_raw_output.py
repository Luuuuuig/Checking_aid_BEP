"""Tests of the sheet of the aid's unreviewed output and of the counts made from it (aid/evalscripts/raw_output.py): the reference
labels the sheet accepts, the rows of one case, the two files of the sheet, the 22 made-up cases as the demo runs them with the
parser and with the recorded reader, the strict reading of a sheet a rater filled, and the counts. The runs are the aid's own on
the made-up set or are put together by hand from the aid's models; no test calls a model or the network."""
import contextlib
import csv
import hashlib
import io
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import openpyxl
import pytest

from aid import demo
from aid.comparison import Findings, Flag, Match, Uncompared
from aid.evalscripts import case_runs, raw_output
from aid.evalscripts.case_runs import folders, read_case_list, runs_of, snapshot
from aid.evalscripts.raw_output import (CASE_COLUMNS, COUNT_COLUMNS, NO_OUTPUT, NO_VERSION, NOT_KEPT, NOT_RUN, SHEET_COLUMNS, TOTAL_COLUMNS,
                                        Reading, ReferenceItem, Text, count, counts_files, read_references, read_sheet, reading_of,
                                        sheet_csv, sheet_rows, sheet_xlsx)
from aid.madeup.generate import DIFFERENCE_LABELS, REFERENCE_COLUMNS, load_specs, reference_rows
from aid.matching import MatchResult, Pair
from aid.review.status import START_STATE
from aid.schema import DocValue, SourceRef
from aid.tests.conftest import REPO_ROOT
from aid.versioning import VersionChoice

SPEC_DIR = REPO_ROOT / "aid" / "madeup" / "spec"
MADE_UP_REFERENCES = REPO_ROOT / "aid" / "madeup" / "references.csv"
HEADER = ",".join(REFERENCE_COLUMNS)


def references(*rows: str, header: str = HEADER) -> bytes:
    """The bytes of a references file with these rows under the header of the template."""
    return "\n".join([header, *rows, ""]).encode("utf-8")


VERSION = "C01,C01-001,version,1,,,,,po,second,yes,"


# --- the references ---

def test_the_made_up_references_are_read_with_their_own_words():
    """Every label the generator of the made-up set can give is accepted, and the labels it calls differences are the ones the
    sheet reads as differences."""
    read = read_references(MADE_UP_REFERENCES.read_bytes(), "references.csv")
    rows = [row for spec in load_specs(SPEC_DIR) for row in reference_rows(spec)]
    assert sum(len(items) for items in read.values()) == len(rows) == 442 and len(read) == 22
    by_id = {item.item_id: item for items in read.values() for item in items}
    for row in rows:
        item = by_id[row["reference_item_id"]]
        assert (item.case_id, item.line_id, item.line_label, item.field) == (
            row["case_id"], row["line_id"], row["line_label"], row["field"])
        assert item.label == row["label"] and item.difference == (row["label"] in DIFFERENCE_LABELS)
    first = read["MU01"][0]
    assert first == ReferenceItem("MU01", "MU01-001", "version", "", "", "", "", 1, (1, 3))
    assert by_id["MU17-012"].cites is None and by_id["MU17-012"].line_label == "missing"          # an item only the PO shows
    assert by_id["CART02-010"].cites is None                                                      # a row of a cart page, no line


def test_the_words_of_the_scoring_manual_are_read_as_the_made_up_words():
    """The manual says present and missing for required information, and cannot be settled from the documents; capitals and the
    spaces around a label do not matter."""
    read = read_references(references(
        VERSION,
        "C01,C01-002,Line presence,1,1,Matched,line_presence, Same ,\"c.pdf, page 2, line 5\",second,yes,",
        "C01,C01-003,field,1,1,matched,price,Cannot be settled from the documents,po,second,yes,",
        "C01,C01-004,field,1,1,matched,quantity,UNRESOLVED,po,second,yes,",
        "C01,C01-005,required information,1,,,required_info,Present,po,second,yes,",
        "C02,C02-001,version,unresolved,,,,,po,second,yes,",
        "C02,C02-002,required information,1,,,,missing,po,second,yes,"), "references.csv")
    labels = [(item.item_id, item.field, item.label, item.comparable) for case in read.values() for item in case]
    assert labels == [("C01-001", "", "", ""), ("C01-002", "line_presence", "same", "yes"), ("C01-003", "price", "cannot be settled", "no"),
                      ("C01-004", "quantity", "unresolved", "no"), ("C01-005", "required_info", "same", "yes"),
                      ("C02-001", "", "", ""), ("C02-002", "required_info", "missing on the confirmation", "yes")]
    assert read["C01"][1].cites == (2, 5) and read["C01"][0].version == 1 and read["C02"][0].version is None


@pytest.mark.parametrize("rows,sentence", [
    ([VERSION, "C01,C01-002,field,1,1,matched,price,differs,po,r,no,"], "C01-002 has the label 'differs'"),
    ([VERSION, "C01,C01-002,field,1,1,matched,price,,po,r,no,"], "C01-002 has the label ''"),
    ([VERSION, "C01,C01-002,field,1,1,matched,price,missing,po,r,no,"], "C01-002 has the label 'missing'"),
    ([VERSION, "C01,C01-002,field,1,1,paired,price,same,po,r,no,"], "C01-002 has the line label 'paired'"),
    ([VERSION, "C01,C01-002,line presence,1,1,missing,line_presence,same,po,r,no,"],
     "C01-002 has the label 'same' with the line label 'missing'"),
    ([VERSION, "C01,C01-002,line presence,1,1,,line_presence,same,po,r,no,"], "C01-002 has the line label ''"),
    ([VERSION, "C01,C01-002,field,1,1,matched,weight,same,po,r,no,"], "C01-002 has the field 'weight'"),
    ([VERSION, "C01,C01-002,remark,1,1,matched,price,same,po,r,no,"], "C01-002 has the item type 'remark'"),
    (["C01,C01-001,version,first,,,,,po,r,no,"], "C01-001 has the answered version 'first'"),
    ([VERSION.replace(",,,,,po", ",,,,same,po")], "C01-001 has the label 'same'"),
    ([VERSION, VERSION.replace("C01-001", "C01-002")], "case C01 has 2 version items"),
    (["C01,C01-002,field,1,1,matched,price,same,po,r,no,"], "case C01 has 0 version items"),
    ([VERSION, "C01,C01-001,field,1,1,matched,price,same,po,r,no,"], "the reference item id C01-001 stands twice (rows 2 and 3)"),
    ([VERSION, "C01,,field,1,1,matched,price,same,po,r,no,"], "row 3 has no reference item id"),
    ([VERSION, ",C01-002,field,1,1,matched,price,same,po,r,no,"], "row 3 has no case id"),
])
def test_a_references_file_with_a_word_outside_the_lists_is_refused_by_its_item(rows, sentence):
    """A label outside the list would get no class, or a wrong one, without a word: the file is refused, naming the label and the
    reference item id and nothing a document holds."""
    with pytest.raises(raw_output.Refusal) as refused:
        read_references(references(*rows), "references.csv")
    message = str(refused.value)
    assert sentence in message and message.startswith("references.csv")


def test_a_references_file_without_its_columns_is_refused():
    with pytest.raises(raw_output.Refusal, match="references.csv is not a references file: it lacks the columns line_label, label"):
        read_references(references(header="case_id,reference_item_id,item_type,version_answered,line_id,field,source"), "references.csv")


# --- the rows of one case, from runs put together by hand ---

PO_TEXT = Text({(1, 10): "1 KM-1 Klep 10 st 12,50 16-11-2026", (1, 11): "2 KM-2 Slang 4 st 8,00 16-11-2026"})
DOC_TEXT = Text({(1, 8): "1 KM-1 Klep 12 st 14,00 23-11-2026", (1, 9): "2 KM-2 Slang 4 st 8,00 16-11-2026"})
SINGLE = VersionChoice(version=1, method="reference", reason="single version", confidence=1.0)


def ids(text: str) -> list[str]:
    """The line ids of "1+2"; none for ""."""
    return text.split("+") if text else []


def said(value: str | None, line: int, text: str | None = None) -> DocValue | None:
    """A value as a reader gives it, printed on this line of page 1; None for no value at all."""
    return None if value is None else DocValue(value=value, text=text or value, source=SourceRef(page=1, line=line, text="the line"))


def flag(number: int, field: str, po: str, doc: str, detail: str = "different", po_value: DocValue | None = said("12,50", 10),
         doc_value: DocValue | None = said("14,00", 8)) -> Flag:
    return Flag(flag_id=f"F{number}", field=field, po_line_ids=ids(po), doc_line_ids=ids(doc), po_value=po_value, doc_value=doc_value,
                detail=detail)


def match(field: str, po: str, doc: str) -> Match:
    return Match(field=field, po_line_ids=ids(po), doc_line_ids=ids(doc), value="a value that no row may hold")


def uncompared(field: str, po: str, doc: str, reason: str = "total only") -> Uncompared:
    return Uncompared(field=field, po_line_ids=ids(po), doc_line_ids=ids(doc), reason=reason)


def pair(po: str, doc: str, kind: str = "matched") -> Pair:
    return Pair(po_line_ids=ids(po), doc_line_ids=ids(doc), basis="code", confidence=1.0, kind=kind)


def reading(*found, pairs=(pair("1", "1"), pair("2", "2")), unmatched="", extra="", uncertain=(), choice=SINGLE, **more) -> Reading:
    """The output of a run that compared the document: these flags, matches and could-not-compare items on these line pairs."""
    findings = Findings(version=choice.version, flags=[item for item in found if isinstance(item, Flag)],
                        matches=[item for item in found if isinstance(item, Match)],
                        uncompared=[item for item in found if isinstance(item, Uncompared)])
    lines = MatchResult(pairs=list(pairs), unmatched_po=ids(unmatched), extra_doc=ids(extra), uncertain=list(uncertain))
    return Reading(**{"choice": choice, "pairs": lines, "findings": findings, "document": DOC_TEXT, "po": PO_TEXT, "run": "run 1 of 1"}
                   | more)


def item(number: int, field: str, line_id: str, label: str, line_label: str = "matched", cites: tuple[int, int] | None = None,
         item_type: str = "") -> ReferenceItem:
    """A reference item of case C01, of the type that goes with its field."""
    item_type = item_type or {"line_presence": "line presence", "required_info": "required information"}.get(field, "field")
    return ReferenceItem("C01", f"C01-{number:03d}", item_type, field, line_id, "" if field == "required_info" else line_label, label,
                         None, cites)


def answered(version: int | None = 1) -> ReferenceItem:
    """The version item of case C01."""
    return ReferenceItem("C01", "C01-001", "version", "", "", "", "", version, None)


def cells(rows: list[dict], *columns: str) -> list[tuple]:
    """These columns of the rows behind the version row."""
    return [tuple(row[column] for column in columns) for row in rows[1:]]


def test_every_row_has_the_columns_of_the_template_and_the_version_row_comes_first():
    """The header is the one of evaluation/templates/raw_output_scores.csv, where the repository holds that file. The version row
    has no field; its note names the version and a fixed word for the method, and the run that counts."""
    template = REPO_ROOT / "evaluation" / "templates" / "raw_output_scores.csv"
    if template.is_file():
        assert template.read_text(encoding="utf-8").strip().split(",") == list(SHEET_COLUMNS)
    rows = sheet_rows("C01", reading(match("price", "1", "1")), [answered(1), item(2, "price", "1", "same")])
    assert all(tuple(row) == SHEET_COLUMNS for row in rows) and len(rows) == 2
    assert rows[0] == dict.fromkeys(SHEET_COLUMNS, "") | {
        "case_id": "C01", "reference_item_id": "C01-001", "reference_label": "version 1", "version_status": "right",
        "note": "aid: version 1 (single version); run 1 of 1"}
    assert rows[1] == dict.fromkeys(SHEET_COLUMNS, "") | {
        "case_id": "C01", "reference_item_id": "C01-002", "line_pair_id": "P1|D1", "field": "price", "reference_label": "same",
        "detection_class": "no difference", "comparable": "yes", "note": "aid: same"}


@pytest.mark.parametrize("choice,word,status", [
    (VersionChoice(version=2, method="reference", reason="quotes version 2", confidence=1.0), "version 2 (quoted version)", "right"),
    (VersionChoice(version=2, method="reference", reason="dated 2026-10-20, the date of version 2", confidence=1.0),
     "version 2 (date)", "right"),
    (VersionChoice(version=1, method="reference", reason="single version", confidence=1.0), "version 1 (single version)",
     "version-selection error"),
    (VersionChoice(version=2, method="correspondence", confidence=1.0,
                   reason="lines with the item code and quantity of a PO line: version 1: 0, version 2: 3"),
     "version 2 (correspondence)", "right"),
    (VersionChoice(version=1, method="model", reason="The supplier Jansen writes about order 4500012345", confidence=0.9),
     "version 1 (model)", "version-selection error"),
    (VersionChoice(version=None, method="none", reason="cannot select", confidence=0.0), "no version (cannot select)",
     "abstention on the version"),
    (VersionChoice(version=None, method="none", reason="no stored version", confidence=0.0), "no version (no stored version)",
     "no stored version"),
    (VersionChoice(version=None, method="none", reason="quotes version 7, which is not stored", confidence=0.0),
     "no version (quoted version not stored)", "no stored version"),
])
def test_the_version_row_names_the_method_by_a_fixed_word_and_never_the_reason(choice, word, status):
    """The reason of a choice holds a date of the document, or the free text of a model: none of it reaches the sheet."""
    found = Reading(choice=choice, lacks=NO_VERSION) if choice.version is None else reading(choice=choice)
    [row] = sheet_rows("C01", found, [answered(2)])
    assert row["note"].startswith(f"aid: {word}") and row["version_status"] == status and row["reference_label"] == "version 2"
    for told in ("2026-10-20", "Jansen", "4500012345", "version 7", "version 1: 0"):
        assert told not in row["note"]


def test_an_unresolved_version_reference_is_said_and_takes_no_side():
    [row] = sheet_rows("C01", reading(), [answered(None)])
    assert (row["reference_label"], row["version_status"]) == ("unresolved", "reference unresolved")


def test_a_label_looked_up_gives_the_class_and_a_judgement_is_left_blank_with_a_proposal():
    """The lookup rows of scoring manual section 4 are filled. A flag on a comparable difference is the rater's to judge: its
    class stays blank and the note holds the proposal."""
    found = reading(flag(1, "price", "1", "1"), flag(2, "unit", "1", "1"), flag(3, "quantity", "2", "2"),
                    match("quantity", "1", "1"), match("delivery_date", "1", "1"), match("price", "2", "2"),
                    match("line_presence", "1", "1"),
                    uncompared("unit", "2", "2", "unknown unit conversion"), uncompared("delivery_date", "2", "2", "no PO date"),
                    uncompared("line_presence", "2", "2", "match uncertain"))
    items = [answered(), item(2, "price", "1", "different", cites=(1, 8)), item(3, "unit", "1", "same"),
             item(4, "quantity", "2", "cannot be settled"),
             item(5, "quantity", "1", "same"), item(6, "delivery_date", "1", "different"), item(7, "price", "2", "unresolved"),
             item(8, "line_presence", "1", "same"), item(9, "unit", "2", "cannot be settled"), item(10, "delivery_date", "2", "different"),
             item(11, "line_presence", "2", "same"), item(12, "required_info", "", "not applicable"),
             item(13, "required_info", "", "missing on the confirmation"), item(14, "required_info", "", "same")]
    assert cells(sheet_rows("C01", found, items), "reference_item_id", "flag_id", "detection_class", "comparable") == [
        ("C01-002", "F1", "", "yes"),                                   # a flag on a comparable difference: to judge
        ("C01-003", "F2", "false flag", "yes"),                         # a flag on an item that is the same: a lookup
        ("C01-004", "F3", "outside the detection counts", "no"),
        ("C01-005", "", "no difference", "yes"),
        ("C01-006", "", "miss", "yes"),                                 # the aid says same where the documents differ
        ("C01-007", "", "match on unsettled item", "no"),
        ("C01-008", "", "no difference", "yes"),
        ("C01-009", "U1", "justified", "no"),
        ("C01-010", "U2", "abstention", "yes"),                         # could not compare on a comparable difference: a miss in the counts
        ("C01-011", "U3", "abstention", "yes"),
        ("C01-012", "", "not scored", ""),
        ("C01-013", "", "miss", "yes"),                                 # no output on a comparable difference
        ("C01-014", "", "no output", "yes")]


def test_the_note_begins_with_what_the_aid_asserts_in_fixed_words():
    found = reading(flag(1, "delivery_date", "1", "1", "later"), match("price", "1", "1"), match("line_presence", "1+2", "1"),
                    uncompared("price", "2", "2", "total only"), pairs=(pair("1+2", "1", "merged"),))
    items = [answered(), item(2, "delivery_date", "1", "same"), item(3, "price", "1", "same"), item(4, "price", "2", "cannot be settled"),
             item(5, "line_presence", "2", "same", "merged"), item(6, "unit", "2", "same")]
    rows = sheet_rows("C01", found, items)
    assert [row["note"].partition(";")[0] for row in rows[1:]] == [
        "aid: later", "aid: same", "aid: could not compare (total only)", "aid: same (merged pair)", "aid: no output for this item"]
    assert all("a value that no row may hold" not in cell for row in rows for cell in row.values())


def test_a_flag_that_agrees_with_the_reference_is_proposed_as_a_true_flag():
    """The detail agrees with the label, the source leads to the value on both sides, and the flag names the line of the document
    that the reference cites."""
    rows = sheet_rows("C01", reading(flag(1, "price", "1", "1")), [answered(), item(2, "price", "1", "different", cites=(1, 8))])
    assert rows[1]["detection_class"] == "" and rows[1]["source_traceable"] == "yes" and rows[1]["line_pair_id"] == "P1|D1"
    assert rows[1]["note"] == ("aid: different; proposal: true flag; to judge: the stated difference; "
                               "source: PO side yes, document side yes")


@pytest.mark.parametrize("label,detail,doc_value,cites,proposal", [
    ("different", "later", said("2026-11-23", 8, "23-11-2026"), (1, 8), "true flag"),
    ("different", "earlier", said("2026-11-23", 8, "23-11-2026"), None, "true flag"),
    ("different", "extra line", said("KM-1", 8), (1, 8), "true flag"),
    ("missing on the confirmation", "missing on the document", None, (1, 8), "true flag"),
    ("missing on the confirmation", "missing line", None, None, "true flag"),
    ("missing on the confirmation", "not in cart", None, None, "true flag"),
    ("different", "missing on the document", None, (1, 8),
     "false flag (the flag says missing on the document where the reference says different)"),
    ("missing on the confirmation", "different", said("14,00", 8), None,
     "false flag (the flag says different where the reference says missing on the confirmation)"),
    ("different", "different", said("8,00", 9), (1, 8), "false flag (the flag cites another line of the document than the reference)"),
    ("different", "different", DocValue(value="14,00", text="14,00"), (1, 8),
     "false flag (a value of the flag has no traceable source and the flag cites another line of the document than the reference)"),
])
def test_the_proposal_for_a_flag_on_a_comparable_difference(label, detail, doc_value, cites, proposal):
    """True flag when the detail agrees with the label, no source fails and the flag names the document line that the reference
    cites, where it cites one and the flag has a document value; else false flag, with the defect named."""
    rows = sheet_rows("C01", reading(flag(1, "price", "1", "1", detail, doc_value=doc_value)),
                      [answered(), item(2, "price", "1", label, cites=cites)])
    assert rows[1]["detection_class"] == "" and f"; proposal: {proposal}; to judge: " in rows[1]["note"]


def test_a_flag_on_the_wrong_line_is_a_false_flag_and_leaves_the_difference_a_miss():
    """Scoring manual, section 4: a flag on the right field but the wrong line is one false flag and one miss."""
    found = reading(flag(1, "price", "2", "2", po_value=said("8,00", 11), doc_value=said("8,00", 9)), match("price", "1", "1"))
    rows = sheet_rows("C01", found, [answered(), item(2, "price", "1", "different", cites=(1, 8)), item(3, "price", "2", "same")])
    assert cells(rows, "reference_item_id", "line_pair_id", "flag_id", "detection_class") == [
        ("C01-002", "P1|D1", "", "miss"), ("C01-003", "P2|D2", "F1", "false flag")]


def test_a_repeated_flag_is_linked_to_the_first_and_has_no_class_of_its_own():
    """The same field, lines, detail, values and sources: a copy, linked through duplicate_of. A flag on the same item with another
    assertion is scored for itself."""
    found = reading(flag(1, "price", "1", "1"), flag(2, "price", "1", "1"),
                    flag(3, "price", "1", "1", "missing on the document", doc_value=None),
                    flag(4, "price", "1", "1"))
    rows = sheet_rows("C01", found, [answered(), item(2, "price", "1", "different")])
    assert cells(rows, "flag_id", "duplicate_of", "detection_class") == [("F1", "", ""), ("F2", "F1", ""), ("F3", "", ""), ("F4", "F1", "")]
    assert "proposal: true flag" in rows[1]["note"] and "proposal" not in rows[2]["note"] and "duplicate of F1" in rows[2]["note"]
    assert "proposal: false flag" in rows[3]["note"]
    same = sheet_rows("C01", reading(flag(1, "unit", "1", "1"), flag(2, "unit", "1", "1")), [answered(), item(2, "unit", "1", "same")])
    assert cells(same, "flag_id", "duplicate_of", "detection_class") == [("F1", "", "false flag"), ("F2", "F1", "")]


def test_one_item_of_the_aid_on_a_merged_line_sits_on_the_reference_items_of_both_po_lines():
    found = reading(uncompared("price", "1+2", "1", "merged line"), match("line_presence", "1+2", "1"), pairs=(pair("1+2", "1", "merged"),))
    items = [answered(), item(2, "price", "1", "cannot be settled", "merged"), item(3, "price", "2", "cannot be settled", "merged"),
             item(4, "line_presence", "1", "same", "merged"), item(5, "line_presence", "2", "same", "merged")]
    rows = sheet_rows("C01", found, items)
    assert cells(rows, "reference_item_id", "line_pair_id", "flag_id", "detection_class", "line_match_status") == [
        ("C01-002", "P1+2|D1", "U1", "justified", ""), ("C01-003", "P1+2|D1", "U1", "justified", ""),
        ("C01-004", "P1+2|D1", "", "no difference", "right"), ("C01-005", "P1+2|D1", "", "no difference", "right")]


def test_an_extra_line_is_keyed_by_the_line_of_the_document_and_a_missing_line_by_the_po_line():
    """The reference names an extra line by the document's line id. The aid's flag for document line 3 lands on it, not on the
    item of PO line 3, which the aid found missing."""
    found = reading(flag(1, "line_presence", "3", "", "missing line", doc_value=None),
                    flag(2, "line_presence", "", "3", "extra line", po_value=None, doc_value=said("KM-2", 9)),
                    unmatched="3", extra="3")
    items = [answered(), item(2, "line_presence", "3", "missing on the confirmation", "missing"),
             item(3, "line_presence", "3", "different", "extra", cites=(1, 9))]
    rows = sheet_rows("C01", found, items)
    assert cells(rows, "reference_item_id", "line_pair_id", "flag_id", "reference_label", "line_match_status") == [
        ("C01-002", "P3|D-", "F1", "missing on the confirmation (missing)", "right"),
        ("C01-003", "P-|D3", "F2", "different (extra)", "right")]
    assert "proposal: true flag" in rows[1]["note"] and "source: PO side yes, no document value" in rows[1]["note"]
    assert "proposal: true flag" in rows[2]["note"] and "source: no PO value, document side yes" in rows[2]["note"]


@pytest.mark.parametrize("line_label,label,placed,status,detection_class", [
    ("matched", "same", dict(pairs=(pair("1", "1"),)), "right", "no difference"),
    ("matched", "same", dict(pairs=(pair("1", "1+2", "split"),)), "other", "no difference"),
    ("split", "same", dict(pairs=(pair("1", "1+2", "split"),)), "right", "no difference"),
    ("split", "same", dict(pairs=(pair("1", "1"),)), "missed split or merge", "no difference"),
    ("merged", "same", dict(pairs=(pair("1", "1"),)), "missed split or merge", "no difference"),
    ("missing", "missing on the confirmation", dict(pairs=(pair("1", "1"),)), "forced match", "miss"),
    ("unresolved", "unresolved", dict(pairs=(pair("1", "1"),)), "forced match", "match on unsettled item"),
    ("unresolved", "unresolved", dict(pairs=(), uncertain=(pair("1", "1"),)), "uncertain", "justified"),
    ("matched", "same", dict(pairs=(), uncertain=(pair("1", "1"),)), "uncertain", "abstention"),
    ("matched", "same", dict(pairs=(), unmatched="1"), "other", "false flag"),
])
def test_the_line_match_status_compares_the_kind_of_pair_with_the_line_label(line_label, label, placed, status, detection_class):
    """Right means that the kind of pair agrees with the line label; which line of the document the aid paired is not checked. A
    forced match is a pair on a line that the reference calls missing or unresolved."""
    if placed.get("uncertain"):
        found = reading(uncompared("line_presence", "1", "1", "match uncertain"), **placed)
    elif placed.get("unmatched"):
        found = reading(flag(1, "line_presence", "1", "", "missing line", doc_value=None), **placed)
    else:
        [paired] = placed["pairs"]
        found = reading(match("line_presence", "1", "+".join(paired.doc_line_ids)), **placed)
    rows = sheet_rows("C01", found, [answered(), item(2, "line_presence", "1", label, line_label)])
    assert cells(rows, "line_match_status", "detection_class") == [(status, detection_class)]


def test_a_line_of_the_document_that_the_reference_calls_extra_and_the_aid_paired_is_a_miss():
    found = reading(match("line_presence", "1", "1+2"), pairs=(pair("1", "1+2", "split"),))
    rows = sheet_rows("C01", found, [answered(), item(2, "line_presence", "1", "same"),
                                     item(3, "line_presence", "2", "different", "extra")])
    assert cells(rows, "reference_item_id", "line_pair_id", "detection_class", "line_match_status") == [
        ("C01-002", "P1|D1+2", "no difference", "other"), ("C01-003", "P1|D1+2", "miss", "other")]


def test_an_item_of_the_aid_that_belongs_to_no_reference_item_cannot_be_mapped():
    """Matches too: an item the references do not hold is reported with its number, never left out and never a false flag by itself."""
    found = reading(flag(1, "price", "2", "2"), match("unit", "2", "2"), uncompared("quantity", "2", "2"), match("price", "1", "1"))
    rows = sheet_rows("C01", found, [answered(), item(2, "price", "1", "same")])
    assert cells(rows, "reference_item_id", "line_pair_id", "field", "flag_id", "detection_class", "reference_label", "comparable") == [
        ("C01-002", "P1|D1", "price", "", "no difference", "same", "yes"),
        ("", "P2|D2", "price", "F1", "cannot be mapped", "", ""), ("", "P2|D2", "quantity", "U1", "cannot be mapped", "", ""),
        ("", "P2|D2", "unit", "", "cannot be mapped", "", "")]


def test_without_references_every_item_of_the_aid_has_a_row_and_no_class():
    found = reading(flag(1, "price", "1", "1"), uncompared("quantity", "1", "1"), match("unit", "1", "1"), match("required_info", "", ""))
    rows = sheet_rows("C01", found)
    assert rows[0]["note"] == "aid: version 1 (single version); run 1 of 1" and rows[0]["version_status"] == ""
    assert cells(rows, "line_pair_id", "field", "flag_id", "source_traceable", "note") == [
        ("P1|D1", "price", "F1", "yes", "aid: different; source: PO side yes, document side yes"),
        ("P1|D1", "quantity", "U1", "", "aid: could not compare (total only)"), ("P1|D1", "unit", "", "", "aid: same"),
        ("", "required_info", "", "", "aid: same")]
    assert all(row["reference_item_id"] == row["reference_label"] == row["detection_class"] == row["comparable"] == "" for row in rows)


# --- the source of a flag ---

@pytest.mark.parametrize("po_value,doc_value,more,cell,said_of_it", [
    (said("12,50", 10), said("14,00", 8), {}, "yes", "source: PO side yes, document side yes"),
    (said("12,50", 10), said("15,00", 8), {}, "no", "source: PO side yes, document side no"),
    (said("12,50", 10), said("14,00", 7), {}, "no", "source: PO side yes, document side no"),
    (DocValue(value="12.50", text="12,50"), said("14,00", 8), {}, "no", "source: PO side no, document side yes"),
    (DocValue(value="12.50"), said("14,00", 8), {"po": Text(None, "export")}, "",
     "source: PO side not checked (export), document side yes"),
    (said("12,50", 10), said("14,00", 8), {"document": Text(None, "saved page")}, "",
     "source: PO side yes, document side not checked (saved page)"),
    (said("12,50", 10), DocValue(value="14.00"), {"document": Text(None, "no text layer")}, "",
     "source: PO side yes, document side not checked (no text layer)"),
    (DocValue(value="12.50"), said("14,00", 8), {"entered": frozenset({("1", "price")})}, "",
     "source: PO side not checked (value entered in Exact), document side yes"),
    (DocValue(value="12.50"), said("15,00", 8), {"po": Text(None, "export")}, "no",
     "source: PO side not checked (export), document side no"),
])
def test_each_side_of_a_flag_is_judged_on_its_own(po_value, doc_value, more, cell, said_of_it):
    """A side without a text layer to check is not checked, whatever its value holds; on a side with one, a value without a source,
    or whose cited line does not hold its text, fails. The flag is no when a side fails, yes when every side was checked and
    cites, and blank otherwise."""
    rows = sheet_rows("C01", reading(flag(1, "price", "1", "1", po_value=po_value, doc_value=doc_value), **more),
                      [answered(), item(2, "price", "1", "different")])
    assert rows[1]["source_traceable"] == cell and rows[1]["note"].endswith(said_of_it)
    if cell == "no":
        assert "proposal: false flag (a value of the flag has no traceable source)" in rows[1]["note"]
    else:
        judged = "proposal: true flag; to judge: the stated difference, the document line" + (", the source" if not cell else "")
        assert judged in rows[1]["note"]


def test_a_value_entered_in_exact_is_said_on_the_rows_of_its_line_and_field_and_never_the_value():
    found = reading(flag(1, "price", "1", "1", po_value=DocValue(value="13.75")), match("quantity", "1", "1"), match("price", "2", "2"),
                    entered=frozenset({("1", "price")}))
    rows = sheet_rows("C01", found, [answered(), item(2, "price", "1", "different"), item(3, "quantity", "1", "same"),
                                     item(4, "price", "2", "same")])
    assert ["aid compared with a value entered in Exact" in row["note"] for row in rows] == [False, True, False, False]
    assert all("13.75" not in cell for row in rows for cell in row.values())


# --- what the lookup cannot do ---

def test_a_reference_line_that_the_reading_does_not_hold_is_said_and_gets_no_class():
    """A line id in another form, or of another version, must not read as no output, and so as a miss."""
    found = reading(match("price", "1", "1"), flag(1, "line_presence", "", "7", "extra line", po_value=None), extra="7")
    items = [answered(), item(2, "price", "1", "same"), item(3, "price", "10", "different"),
             item(4, "line_presence", "9", "different", "extra")]
    rows = sheet_rows("C01", found, items)
    assert cells(rows, "reference_item_id", "line_pair_id", "flag_id", "detection_class", "note") == [
        ("C01-002", "P1|D1", "", "no difference", "aid: same"),
        ("C01-003", "", "", "", "aid: no output for this item; line id not found in the aid's reading"),
        ("C01-004", "", "", "", "aid: no output for this item; line id not found in the aid's reading"),
        ("", "P-|D7", "F1", "cannot be mapped", "aid: extra line; source: no PO value, document side yes")]


def test_the_field_items_of_a_missing_or_an_extra_line_are_not_counted():
    """Scoring manual, section 4: a wholly missing or extra line is one line-presence difference, and no further differences are
    counted for it. A references file made by hand may hold field items for such a line all the same."""
    found = reading(flag(1, "line_presence", "2", "", "missing line", doc_value=None), pairs=(pair("1", "1"),), unmatched="2")
    items = [answered(), item(2, "line_presence", "2", "missing on the confirmation", "missing"),
             item(3, "price", "2", "missing on the confirmation", "missing"), item(4, "quantity", "2", "missing on the confirmation", ""),
             item(5, "price", "5", "different", "extra")]
    rows = sheet_rows("C01", found, items)
    assert cells(rows, "reference_item_id", "detection_class", "comparable", "note")[1:] == [
        ("C01-003", "", "yes", "aid: not looked up; not counted: the line is missing or extra"),
        ("C01-004", "", "yes", "aid: not looked up; not counted: the line is missing or extra"),
        ("C01-005", "", "yes", "aid: not looked up; not counted: the line is missing or extra")]
    assert rows[1]["flag_id"] == "F1" and "proposal: true flag" in rows[1]["note"]


def test_with_a_wrong_version_the_classes_are_left_blank_unless_both_versions_hold_the_same_line_ids():
    """Which lines of the selected version are the lines of the answered version is a judgement, unless they carry the same ids."""
    found = dict(choice=VersionChoice(version=1, method="correspondence", reason="made up", confidence=1.0))
    items = [answered(2), item(2, "price", "1", "different"), item(3, "price", "2", "same"), item(4, "required_info", "", "not applicable")]
    other = sheet_rows("C01", reading(flag(1, "price", "1", "1"), match("price", "2", "2"), match("unit", "2", "2"),
                                      version_lines={1: frozenset({"1", "2"}), 2: frozenset({"1", "2", "3"})}, **found), items)
    assert other[0]["version_status"] == "version-selection error"
    assert cells(other, "reference_item_id", "flag_id", "detection_class") == [
        ("C01-002", "F1", ""), ("C01-003", "", ""), ("C01-004", "", "not scored"), ("", "", "")]
    assert all("wrong version: class left to the rater" in row["note"] and "proposal" not in row["note"] for row in other[1:3] + other[4:])
    same = sheet_rows("C01", reading(flag(1, "price", "1", "1"), match("price", "2", "2"),
                                     version_lines={1: frozenset({"1", "2"}), 2: frozenset({"2", "1"})}, **found), items)
    assert same[0]["version_status"] == "version-selection error"
    assert cells(same, "reference_item_id", "detection_class")[:2] == [("C01-002", ""), ("C01-003", "no difference")]
    assert "proposal: true flag" in same[1]["note"] and "wrong version" not in same[1]["note"]
    unknown = sheet_rows("C01", reading(match("price", "2", "2"), **found), items)             # the answered version is not stored
    assert cells(unknown, "detection_class")[1] == ("",)


# --- a case without an output ---

def test_a_case_that_was_not_run_has_one_row_and_no_class():
    """Not run is not no output: a difference of a case that was never run is no miss."""
    rows = sheet_rows("C01", Reading(lacks=NOT_RUN), [answered(), item(2, "price", "1", "different")])
    assert rows == [dict.fromkeys(SHEET_COLUMNS, "") | {"case_id": "C01", "reference_item_id": "C01-001", "reference_label": "version 1",
                                                        "version_status": "not run", "note": "aid: not run"}]
    assert sheet_rows("C01", Reading(lacks=NOT_RUN))[0]["version_status"] == "not run"


def test_a_run_whose_first_output_is_not_kept_has_one_row_that_says_so():
    kept = Reading(lacks=NOT_KEPT, run="run 1 of 1",
                   remarks=("run again in the same folder (or no audit record): the first run's output is not kept",))
    [row] = sheet_rows("C01", kept, [answered(), item(2, "price", "1", "different")])
    assert row["version_status"] == "first output not kept" and row["detection_class"] == ""
    assert row["note"] == ("aid: first output not kept; run 1 of 1; run again in the same folder (or no audit record): the first run's "
                           "output is not kept")


def test_a_failed_run_has_no_output_and_its_differences_are_misses():
    """The case was attempted: it stays in the counts. An item that is the same, or that the documents do not settle, has no output."""
    items = [answered(), item(2, "price", "1", "different"), item(3, "unit", "1", "same"), item(4, "quantity", "1", "cannot be settled"),
             item(5, "required_info", "", "not applicable"), item(6, "line_presence", "2", "missing on the confirmation", "missing"),
             item(7, "price", "2", "missing on the confirmation", "missing")]
    rows = sheet_rows("C01", Reading(lacks=NO_OUTPUT, run="run 1 of 1"), items)
    assert rows[0]["version_status"] == "no output" and rows[0]["note"] == "aid: no output for this case (failed); run 1 of 1"
    assert cells(rows, "detection_class", "line_pair_id", "line_match_status", "note") == [
        ("miss", "", "", "aid: no output for this case (failed)"), ("no output", "", "", "aid: no output for this case (failed)"),
        ("no output", "", "", "aid: no output for this case (failed)"), ("not scored", "", "", "aid: no output for this case (failed)"),
        ("miss", "", "", "aid: no output for this case (failed)"),
        ("", "", "", "aid: not looked up; not counted: the line is missing or extra")]
    assert sheet_rows("C01", Reading(lacks=NO_OUTPUT))[0]["version_status"] == "no output"


def test_a_run_that_selected_no_version_has_no_item_output_either():
    """Cannot select is the aid's answer on the version, an abstention; the items of the case then have no output."""
    choice = VersionChoice(version=None, method="none", reason="cannot select", confidence=0.0)
    rows = sheet_rows("C01", Reading(lacks=NO_VERSION, choice=choice, run="run 2 of 2", remarks=("run 1 waited for its purchase order",)),
                      [answered(), item(2, "price", "1", "different"), item(3, "unit", "1", "same")])
    assert rows[0]["note"] == "aid: no version (cannot select); run 2 of 2; run 1 waited for its purchase order"
    assert rows[0]["version_status"] == "abstention on the version"
    assert cells(rows, "detection_class", "note") == [("miss", "aid: no output for this case (no version selected)"),
                                                      ("no output", "aid: no output for this case (no version selected)")]


# --- the 22 made-up cases, as the demo runs them ---

@pytest.fixture(scope="module")
def made_up(tmp_path_factory) -> dict[str, Path]:
    """The made-up set run as aid.demo runs it, once with the parser and once with the recorded reader: the demo folder of each."""
    out = {}
    for reader in ("parser", "recorded"):
        out[reader] = tmp_path_factory.mktemp(f"demo_{reader}") / "demo"
        with contextlib.redirect_stdout(io.StringIO()):
            assert demo.main(["--out", str(out[reader]), "--reader", reader]) == 0
    return out


def made_up_rows(out: Path, kind: str) -> list[dict[str, str]]:
    """The rows of the sheet of one control point of a demo folder, against the references of the made-up set."""
    answers = read_references(MADE_UP_REFERENCES.read_bytes(), "references.csv")
    found = folders(out / "cases")
    return [row for case in read_case_list(out / "cases.csv") if case.kind == kind
            for row in sheet_rows(case.case_id, reading_of(runs_of(case, found).runs), answers[case.case_id])]


def proposals(rows: list[dict[str, str]]) -> Counter:
    """How many rows propose each class."""
    return Counter(row["note"].partition("proposal: ")[2].partition(";")[0] for row in rows if "proposal: " in row["note"])


def test_the_words_for_a_run_without_output_are_those_of_the_case_runs():
    assert (NO_OUTPUT, NO_VERSION, NOT_KEPT) == (case_runs.NO_OUTPUT, case_runs.NO_VERSION, case_runs.FIRST_NOT_KEPT)


@pytest.mark.parametrize("reader,kind,differences,true_flags,failed", [
    ("recorded", "confirmation", 13, 13, []), ("parser", "confirmation", 13, 12, ["MU05", "MU16"]),
    ("recorded", "cart", 2, 2, []), ("parser", "cart", 2, 2, [])])
def test_the_proposals_on_the_made_up_set_find_the_declared_differences_and_no_false_flag(made_up, reader, kind, differences,
                                                                                         true_flags, failed):
    """With the recorded reader every declared difference of the confirmations is flagged and proposed as a true flag; with the
    parser the two scans fail, and the one difference of MU16 is a miss. The two differences of the cart are found by both. No flag
    is proposed or looked up as false, nothing is left unmapped, and every reference item has its row."""
    rows = made_up_rows(made_up[reader], kind)
    labels = {row["reference_item_id"]: row["reference_label"].partition(" (")[0] for row in rows if row["field"]}
    assert sum(label in DIFFERENCE_LABELS for label in labels.values()) == differences
    assert proposals(rows) == {"true flag": true_flags}
    classes = Counter(row["detection_class"] for row in rows)
    assert classes["false flag"] == classes["cannot be mapped"] == classes["abstention"] == 0
    assert classes["miss"] == differences - true_flags
    assert sorted(row["case_id"] for row in rows if row["version_status"] == "no output") == failed
    assert Counter(row["version_status"] for row in rows if not row["field"]) == (
        {"right": len({row["case_id"] for row in rows}) - len(failed)} | ({"no output": len(failed)} if failed else {}))
    answers = read_references(MADE_UP_REFERENCES.read_bytes(), "references.csv")
    expected = {item.item_id for case in read_case_list(made_up[reader] / "cases.csv") if case.kind == kind
                for item in answers[case.case_id]}
    assert {row["reference_item_id"] for row in rows} == expected and all(row["note"].startswith("aid: ") for row in rows)


def test_the_rows_of_the_made_up_confirmations_by_reference_label_and_what_the_aid_asserts(made_up):
    """The table of the check before the build: the same items are matches, the differences are flags of the kind the case
    declares, what cannot be settled is a could-not-compare item, and the merged line of MU19 puts four items of the aid on eight
    reference items."""
    rows = [row for row in made_up_rows(made_up["recorded"], "confirmation") if row["field"]]
    said = Counter((row["reference_label"].partition(" (")[0], row["note"].partition(";")[0].removeprefix("aid: "), row["detection_class"])
                   for row in rows)
    assert said == {("same", "same", "no difference"): 294, ("same", "same (matched pair)", "no difference"): 73,
                    ("same", "same (merged pair)", "no difference"): 2,
                    ("different", "different", ""): 6, ("different", "later", ""): 2, ("different", "extra line", ""): 1,
                    ("missing on the confirmation", "missing on the document", ""): 3,
                    ("missing on the confirmation", "missing line", ""): 1,
                    ("cannot be settled", "could not compare (merged line)", "justified"): 8,
                    ("cannot be settled", "could not compare (total only)", "justified"): 3,
                    ("cannot be settled", "could not compare (unknown unit conversion)", "justified"): 4}
    merged = [row for row in rows if row["case_id"] == "MU19" and row["flag_id"]]
    assert len(merged) == 8 and {row["flag_id"] for row in merged} == {"U1", "U2", "U3", "U4"}
    assert {row["line_pair_id"] for row in merged} == {"P1+2|D1"}
    assert Counter(row["line_match_status"] for row in rows if row["field"] == "line_presence") == {"right": 77}
    assert Counter(row["source_traceable"] for row in rows if row["flag_id"].startswith("F")) == {"yes": 13}


def test_the_flags_of_a_cart_are_true_flags_though_their_po_values_come_from_an_export(made_up):
    """The PO side of a cart is read from an export of the ERP: it has no text layer and its values no source. That side is not
    checked, which leaves the source of the flag blank and its proposal a true flag (the fault of the first draft, which made both
    differences of CART02 false flags and misses)."""
    for reader in ("parser", "recorded"):
        flags = [row for row in made_up_rows(made_up[reader], "cart") if row["flag_id"]]
        assert [(row["case_id"], row["flag_id"], row["field"], row["line_pair_id"], row["source_traceable"], row["detection_class"])
                for row in flags] == [("CART02", "F2", "price", "P2|D2", "", ""), ("CART02", "F1", "line_presence", "P3|D-", "", "")]
        assert flags[0]["note"] == ("aid: different; proposal: true flag; to judge: the stated difference, the document line, the source; "
                                    "source: PO side not checked (export), document side not checked (saved page)")
        assert flags[1]["note"] == ("aid: not in cart; proposal: true flag; to judge: the stated difference, the document line, "
                                    "the source; "
                                    "source: PO side not checked (export), no document value")


def test_making_the_rows_leaves_the_cases_and_the_po_store_as_they_were(made_up):
    """Nothing is written and nothing is touched: the names, the sizes, the sha256 and the modification times of everything under
    the cases root and the PO store are the same afterwards."""
    out = made_up["recorded"]
    before = snapshot(out / "cases"), snapshot(out / "po_store")
    made_up_rows(out, "confirmation")
    made_up_rows(out, "cart")
    assert (snapshot(out / "cases"), snapshot(out / "po_store")) == before


def test_no_row_of_the_made_up_sheet_holds_identifying_text(made_up):
    """Anonymised case ids only: no PO number, no folder name, no supplier, no file name and no value of a document."""
    rows = made_up_rows(made_up["recorded"], "confirmation") + made_up_rows(made_up["recorded"], "cart")
    cells = " ".join(cell for row in rows for cell in row.values())
    for spec in load_specs(SPEC_DIR):
        told = [spec.po.po_number, spec.po.supplier, f"{spec.case_id}_conf", f"{spec.case_id}.html", "po.pdf"]
        told += [line.item_code for line in spec.document.lines] + [line.description for line in spec.document.lines]
        assert not [text for text in told if text and text in cells]
    assert not [folder.name for folder in folders(made_up["recorded"] / "cases").read if folder.name in cells] and "/" not in cells


# --- what the sheet takes of a run, from folders changed by hand ---

@pytest.fixture
def copied(made_up, tmp_path) -> Path:
    """A copy of the demo folder of the recorded reader that a test may change: the cases and the case list (the PO store stays
    where the demo filed it, as the audit records name it)."""
    shutil.copytree(made_up["recorded"] / "cases", tmp_path / "cases")
    shutil.copy(made_up["recorded"] / "cases.csv", tmp_path / "cases.csv")
    return tmp_path


def the_reading(root: Path, case_id: str) -> Reading:
    [case] = [case for case in read_case_list(root / "cases.csv") if case.case_id == case_id]
    return reading_of(runs_of(case, folders(root / "cases")).runs)


def change_audit(root: Path, po_number: str, change) -> None:
    """Rewrite the audit record of the one case folder of the purchase order with what `change` makes of its steps by name."""
    [audit] = (root / "cases").glob(f"{po_number}_*/audit.json")
    record = json.loads(audit.read_text(encoding="utf-8"))
    change({step["name"]: step["payload"] for step in record["steps"]}, record)
    audit.write_text(json.dumps(record), encoding="utf-8")


def test_the_reading_of_a_run_holds_its_output_and_the_text_of_both_documents(copied):
    read = the_reading(copied, "MU07")
    assert read.lacks == "" and read.run == "run 1 of 1" and read.remarks == () and read.entered == frozenset()
    assert read.choice.version == 1 and [flag.flag_id for flag in read.findings.flags] == ["F1"] and len(read.pairs.pairs) == 2
    assert read.version_lines == {1: frozenset({"1", "2"})}
    assert read.document.lines[1, 1] and read.po.lines[1, 1] == "Inkooporder 250107"
    assert the_reading(copied, "MU01").document.lines is None                 # no flag: no file is read for its sources


def test_a_case_without_a_run_and_a_case_whose_only_run_waited_are_not_run(copied):
    assert reading_of(()) == Reading(lacks=NOT_RUN)
    shutil.rmtree(next((copied / "cases").glob("250107_*")))
    assert the_reading(copied, "MU07") == Reading(lacks=NOT_RUN)


def test_a_po_file_that_changed_since_the_run_or_is_gone_is_not_checked_and_never_named(copied, tmp_path):
    """The PDF of the PO side is read from the path of step 01 only when its sha256 is the one recorded there."""
    moved = tmp_path / "moved.pdf"

    def point_elsewhere(steps, record):
        version = steps["po_versions"]["versions"][0]
        shutil.copy(version["pdf_path"], moved)
        version["pdf_path"] = str(moved)
    change_audit(copied, "250107", point_elsewhere)
    assert the_reading(copied, "MU07").po.lines is not None
    moved.write_bytes(moved.read_bytes() + b"\n")
    assert the_reading(copied, "MU07").po == Text(None, "file changed since the run")
    moved.unlink()
    read = the_reading(copied, "MU07")
    assert read.po == Text(None, "file not found")
    [flagged] = [row for row in sheet_rows("MU07", read) if row["flag_id"]]
    assert flagged["source_traceable"] == "" and flagged["note"].endswith("source: PO side not checked (file not found), document side yes")
    assert "moved" not in flagged["note"]


def test_a_document_that_changed_in_the_case_folder_is_not_checked(copied):
    [document] = (copied / "cases").glob("250107_*/input/MU07_conf.pdf")
    document.write_bytes(document.read_bytes() + b"\n")
    assert the_reading(copied, "MU07").document == Text(None, "file changed since the run")


def test_a_scan_has_no_text_layer_to_check_a_source_against(copied):
    """MU16 is a scan that the recorded reader reads. Its one flag says that a value is missing on the document, so it has a PO
    value only; given a document value by hand, that side is not checked, whatever source the reader named for it."""
    [flagged] = [row for row in sheet_rows("MU16", the_reading(copied, "MU16")) if row["flag_id"]]
    assert flagged["source_traceable"] == "yes" and flagged["note"].endswith("source: PO side yes, no document value")

    def a_value_on_the_scan(steps, record):
        steps["compare"]["flags"][0]["doc_value"] = {"value": "2026-11-23", "text": "23-11-2026",
                                                     "source": {"page": 1, "line": 9, "text": "x"}}
    change_audit(copied, "250116", a_value_on_the_scan)
    [flagged] = [row for row in sheet_rows("MU16", the_reading(copied, "MU16")) if row["flag_id"]]
    assert flagged["source_traceable"] == "" and flagged["note"].endswith("source: PO side yes, document side not checked (no text layer)")


def test_the_values_entered_in_exact_of_a_run_are_taken_by_line_and_field_only(copied):
    def enter(steps, record):
        record["steps"].insert(2, {"name": "entered", "seconds": 0.0, "payload": {"version": 1, "replaced": [
            {"line_id": "2", "field": "price", "was": "1.00", "entered": "2.00", "marked_at": "2026-11-12T09:00:00", "by": "a buyer"}]}})
    change_audit(copied, "250107", enter)
    read = the_reading(copied, "MU07")
    assert read.entered == frozenset({("2", "price")}) and read.remarks == ()

    def unread(steps, record):
        steps["entered"].update(replaced=[], unread=True)
    change_audit(copied, "250107", unread)
    assert the_reading(copied, "MU07").remarks == ("the values entered in Exact could not be read at the run",)


def test_a_file_of_a_run_that_cannot_be_read_now_is_counted_and_said_without_its_folder(copied, tmp_path, monkeypatch, command):
    """While the aid writes a file, or another program holds it, a read fails. The run then gives no output that counts as kept,
    the version row says that a file could not be read now, and what is printed counts it; no folder is named."""
    [held] = (copied / "cases").glob("250107_*/audit.json")
    real_read = Path.read_bytes

    def read_bytes(self):
        if self == held:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read(self)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    read = the_reading(copied, "MU07")
    assert read.lacks == NOT_KEPT and read.unread == 1
    assert read.remarks == ("1 file of the run could not be read now: run the command again", case_runs.NOT_KEPT)
    printed = command(*sheet_arguments(copied, tmp_path / "records"))
    assert "1 file of the runs could not be read now" in printed and "250107" not in printed
    rows = read_sheet(real_read(tmp_path / "records" / "raw_output_scores.csv"), "raw_output_scores.csv")
    [row] = [row for row in rows if row["case_id"] == "MU07"]
    assert row["version_status"] == "first output not kept" and "1 file of the run could not be read now" in row["note"]
    assert json.loads(real_read(tmp_path / "records" / "sheet_manifest.json"))["files_not_read"] == 1


def test_a_folder_run_again_in_place_gives_no_reading_of_its_first_output(copied):
    [folder] = (copied / "cases").glob("250107_*")
    closes = {"records": [{"action": "close", "at": "2026-11-12T10:00:00"}]}
    (folder / "steps" / "09_close.json").write_text(json.dumps(closes), encoding="utf-8")
    read = the_reading(copied, "MU07")
    assert read.lacks == NOT_KEPT and read.findings is None and read.remarks == (case_runs.NOT_KEPT,) and read.run == "run 1 of 1"


# --- the two files of the sheet ---

def small_sheet() -> list[dict[str, str]]:
    """A sheet of five cases put together by hand, as the sheet command writes it. C01 has a flag on a difference (to judge), a
    flag on an item that is the same, a miss and two copies of a flag; C02 failed; C03 was not run; C04 has no difference and no
    flag; C05 selected the wrong version of two with the same line ids, and has a flag that is outside the counts, an abstention on
    a difference, a merged line that cannot be settled and an item that cannot be mapped."""
    wrong = VersionChoice(version=1, method="correspondence", reason="made up", confidence=1.0)
    c01 = reading(flag(1, "price", "1", "1"), flag(2, "unit", "1", "1", po_value=DocValue(value="pc", text="st")),
                  flag(3, "price", "1", "1"), flag(4, "price", "1", "1"), match("quantity", "1", "1"), match("delivery_date", "1", "1"),
                  match("line_presence", "1", "1"), match("line_presence", "2", "2"), match("required_info", "", ""))
    c04 = reading(match("price", "1", "1"), match("quantity", "1", "1"), match("line_presence", "1", "1"), match("required_info", "", ""))
    c05 = reading(flag(1, "quantity", "3", "3"), uncompared("delivery_date", "3", "3", "no PO date"),
                  uncompared("price", "1+2", "1", "merged line"), match("line_presence", "1+2", "1"), match("line_presence", "3", "3"),
                  match("line_presence", "4", "4"), match("unit", "3", "3"), choice=wrong, po=Text(None, "export"),
                  pairs=(pair("1+2", "1", "merged"), pair("3", "3"), pair("4", "4")),
                  version_lines={1: frozenset("1234"), 2: frozenset("1234")})

    def case(case_id: str, items: list[ReferenceItem]) -> list[ReferenceItem]:
        return [ReferenceItem(case_id, item.item_id.replace("C01", case_id), *list(vars(item).values())[2:]) for item in items]

    return [row for case_id, found, items in [
        ("C01", c01, [answered(), item(2, "price", "1", "different", cites=(1, 8)), item(3, "unit", "1", "same"),
                      item(4, "quantity", "1", "same"),
                      item(5, "delivery_date", "1", "different"), item(6, "line_presence", "1", "same"),
                      item(7, "line_presence", "2", "same", "split"), item(8, "required_info", "", "same")]),
        ("C02", Reading(lacks=NO_OUTPUT, run="run 1 of 1"), [answered(), item(2, "price", "1", "different"), item(3, "unit", "1", "same")]),
        ("C03", Reading(lacks=NOT_RUN), [answered(), item(2, "price", "1", "different")]),
        ("C04", c04, [answered(), item(2, "price", "1", "same"), item(3, "quantity", "1", "same"), item(4, "line_presence", "1", "same"),
                      item(5, "required_info", "", "not applicable")]),
        ("C05", c05, [answered(2), item(2, "quantity", "3", "cannot be settled"), item(3, "delivery_date", "3", "different"),
                      item(4, "price", "1", "cannot be settled", "merged"), item(5, "price", "2", "cannot be settled", "merged"),
                      item(6, "line_presence", "1", "same", "merged"), item(7, "line_presence", "2", "same", "merged"),
                      item(8, "line_presence", "4", "missing on the confirmation", "missing")]),
    ] for row in sheet_rows(case_id, found, case(case_id, items))]


def test_the_csv_of_the_sheet_is_utf8_with_commas_and_plain_line_ends():
    rows = small_sheet()
    data = sheet_csv(rows)
    assert data.startswith(",".join(SHEET_COLUMNS).encode() + b"\nC01,C01-001,,,version 1,") and b"\r" not in data
    assert list(csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""))) == rows


def test_the_workbook_of_the_sheet_holds_the_same_rows_as_text_with_lists_to_choose_from():
    """Every cell is a text cell, so that no spreadsheet makes a time or a number of it; the header row stays in view; and each of
    the five columns with a closed list offers that list."""
    rows = small_sheet()
    book = openpyxl.load_workbook(io.BytesIO(sheet_xlsx(rows)))
    page = book.active
    values = [["" if cell.value is None else cell.value for cell in line] for line in page.iter_rows()]
    assert values[0] == list(SHEET_COLUMNS) and values[1:] == [list(row.values()) for row in rows]
    assert all(cell.number_format == "@" and (cell.value is None or isinstance(cell.value, str))
               for line in page.iter_rows() for cell in line)
    assert page.freeze_panes == "A2"
    lists = {str(validation.sqref).partition(":")[0]: validation.formula1 for validation in page.data_validations.dataValidation}
    assert lists == {"G2": '"' + ",".join(raw_output.CLASSES) + '"', "I2": '"yes,no"', "J2": '"yes,no"',
                     "K2": '"' + ",".join(raw_output.VERSION_STATUSES) + '"', "L2": '"' + ",".join(raw_output.LINE_MATCH_STATUSES) + '"'}
    assert all(len(formula) <= 255 and validation.type == "list" and validation.allow_blank
               for formula, validation in zip(lists.values(), page.data_validations.dataValidation))
    book.close()


# --- reading a sheet that a rater filled ---

def as_saved(rows: list[dict[str, str]], delimiter: str = ";", encoding: str = "utf-8-sig", line_end: str = "\r\n") -> bytes:
    """The sheet as a spreadsheet saves it as CSV: its list separator, its code page and its line ends."""
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=delimiter, lineterminator=line_end)
    writer.writerows([SHEET_COLUMNS, *(row.values() for row in rows)])
    return out.getvalue().encode(encoding)


def changed(rows: list[dict[str, str]], of_case: str, of_flag: str, /, **cells: str) -> list[dict[str, str]]:
    """The rows with these cells typed into the rows of the flag of the case."""
    return [row | cells if (row["case_id"], row["flag_id"]) == (of_case, of_flag) else dict(row) for row in rows]


def test_a_sheet_is_read_back_as_it_was_written_from_csv_and_from_a_workbook():
    rows = small_sheet()
    assert read_sheet(sheet_csv(rows), "raw_output_scores.csv") == rows
    assert read_sheet(sheet_xlsx(rows), "raw_output_scores.xlsx") == rows
    assert read_sheet(sheet_xlsx(rows), "Scores of May.XLSX") == rows


@pytest.mark.parametrize("encoding", ["utf-8-sig", "cp1252"])
def test_a_sheet_saved_by_a_spreadsheet_is_read_with_what_the_rater_typed(encoding):
    """Semicolons, a byte order mark or the Windows code page, a class typed with a capital and a space behind it, a non-breaking
    space, two spaces inside a class, a flag id in small letters, a note with an accented letter, a blank row, a short row and an
    empty column behind the last one."""
    rows = changed(small_sheet(), "C01", "F1", detection_class="True  Flag\u00a0",
                   note="aid: different; gecontroleerd op de orderbevestiging, co\u00f6rdinatie")
    rows = changed(rows, "C01", "F2", flag_id="f2", source_traceable=" NO ")
    saved = as_saved(rows, encoding=encoding).decode(encoding).split("\r\n")
    saved[0] += ";"                                                           # an empty column behind the last one
    saved.insert(3, ";" * len(SHEET_COLUMNS))                                  # a row that is empty in every cell
    saved[1] = saved[1].rstrip(";")                                           # the version row of C01 ends before its empty cells
    read = read_sheet("\r\n".join(saved).encode(encoding), "sheet.csv")
    assert len(read) == len(rows) and read[0] == rows[0]
    assert read[1]["detection_class"] == "true flag" and read[1]["note"].endswith("co\u00f6rdinatie")
    [second] = [row for row in read if row["reference_item_id"] == "C01-003"]
    assert (second["flag_id"], second["source_traceable"], second["detection_class"]) == ("F2", "no", "false flag")


@pytest.mark.parametrize("cells,sentence", [
    (dict(detection_class="treu flag"),
     "row 3: the column detection_class holds 'treu flag': it is empty or one of true flag, false flag, miss,"),
    (dict(detection_class="TRUE"), "row 3: the column detection_class holds 'TRUE', which a spreadsheet made of what was typed"),
    (dict(source_traceable="WAAR"), "row 3: the column source_traceable holds 'WAAR', which a spreadsheet made of what was typed"),
    (dict(note="#NAME?"), "row 3: the column note holds '#NAME?', which a spreadsheet made of what was typed"),
    (dict(source_traceable="ja"), "row 3: the column source_traceable holds 'ja': it is empty or one of yes, no"),
    (dict(comparable="maybe"), "row 3: the column comparable holds 'maybe': it is empty or one of yes, no"),
    (dict(version_status="wrong"), "row 3: the column version_status holds 'wrong': it is empty or one of right, version-selection error,"),
    (dict(line_match_status="forced"), "row 3: the column line_match_status holds 'forced': it is empty or one of right, forced match,"),
    (dict(field="weight"), "row 3: the column field holds 'weight': it is empty or one of price, quantity,"),
    (dict(line_pair_id="1:01"), "row 3: the column line_pair_id holds '1:01': a line pair reads as P1+2|D1"),
    (dict(line_pair_id="01:01:00"), "row 3: the column line_pair_id holds '01:01:00': a line pair reads as P1+2|D1"),
    (dict(flag_id="flag 1"), "row 3: the column flag_id holds 'flag 1': a flag is F and a number, a could-not-compare item U and a number"),
    (dict(duplicate_of="U1"), "row 3: the column duplicate_of holds 'U1': it names a flag, F and a number"),
    (dict(reference_label="unclear"), "row 3: the column reference_label holds 'unclear'"),
    (dict(note="checked"), "row 3: the note does not begin with 'aid:'"),
    (dict(case_id=""), "row 3 has no case id"),
    (dict(flag_id="", detection_class="true flag"), "row 3: the class true flag stands on a row without a flag"),
    (dict(reference_label="same", detection_class="true flag"),
     "row 3: the class true flag stands on a row whose reference item is no difference"),
    (dict(reference_item_id="", reference_label="", comparable="", detection_class="true flag"),
     "row 3: the class true flag stands on a row whose reference item is no difference"),
])
def test_a_cell_outside_its_list_is_refused_with_the_row_the_column_and_the_values(cells, sentence):
    """A class with a typing error would count as nothing and a time in place of a line pair as no line at all, without a word."""
    with pytest.raises(raw_output.Refusal) as refused:
        read_sheet(as_saved(changed(small_sheet(), "C01", "F1", **cells)), "sheet.csv")
    assert str(refused.value).startswith("sheet.csv, ") and sentence in str(refused.value)


def test_a_sheet_whose_rows_do_not_fit_together_is_refused():
    """A row that stands twice would count its flag twice; a case without its version row would fall out of the number of cases;
    and the rows of one reference item must agree on what the reference says."""
    rows = small_sheet()
    with pytest.raises(raw_output.Refusal, match=r"sheet.csv, row 4: the same case, reference item and flag as row 3 \(C01, C01-002, F1\)"):
        read_sheet(as_saved(rows[:2] + [rows[1] | {"note": "aid: different; typed again"}] + rows[2:]), "sheet.csv")
    with pytest.raises(raw_output.Refusal, match="sheet.csv: case C02 has 0 version rows"):
        read_sheet(as_saved([row for row in rows if (row["case_id"], row["field"]) != ("C02", "")]), "sheet.csv")
    with pytest.raises(raw_output.Refusal, match="sheet.csv, row 4: the reference item C01-002 is 'same' here and 'different' in row 3"):
        read_sheet(as_saved(changed(rows, "C01", "F3", reference_label="same")), "sheet.csv")
    with pytest.raises(raw_output.Refusal, match="sheet.csv is not a raw output sheet: it lacks the columns flag_id, note"):
        read_sheet(b"case_id;reference_item_id;line_pair_id;field;reference_label;detection_class;duplicate_of;source_traceable;comparable;"
                   b"version_status;line_match_status\r\n", "sheet.csv")
    with pytest.raises(raw_output.Refusal, match="sheet.xlsx cannot be read as a workbook"):
        read_sheet(sheet_csv(rows), "sheet.xlsx")
    with pytest.raises(raw_output.Refusal, match="sheet.csv: save the file as CSV UTF-8"):
        read_sheet(sheet_xlsx(rows), "sheet.csv")


def test_a_workbook_cell_that_is_no_text_is_read_as_what_it_shows_and_refused_where_it_matters():
    book = openpyxl.load_workbook(io.BytesIO(sheet_xlsx(small_sheet())))
    book.active["I3"] = True                                                 # a truth value in source_traceable
    saved = io.BytesIO()
    book.save(saved)
    book.close()
    with pytest.raises(raw_output.Refusal, match="sheet.xlsx, row 3: the column source_traceable holds 'TRUE', which a spreadsheet made"):
        read_sheet(saved.getvalue(), "sheet.xlsx")


# --- the counts ---

def by_field(counted, name: str) -> dict:
    [row] = [row for row in counted.fields if row["field"] == name]
    return row


def total_of(counted, quantity: str) -> tuple:
    [row] = [row for row in counted.totals if row["quantity"] == quantity]
    return row["value"], row["denominator"]


ADDITIVE = [column for column in COUNT_COLUMNS[3:]
            if column not in ("coverage", "recall", "precision", "cases_with_differences", "cases_with_flags")]


def test_the_counts_of_a_sheet_by_its_mechanical_proposals():
    """Every comparable reference difference counts once, as a detection or a miss; every distinct flag once, as true or false;
    the copies of a flag are folded; and what the documents do not settle is reported outside the detection counts."""
    counted = count(small_sheet(), "made-up", "mechanical")
    assert [tuple(row) for row in counted.fields] == [COUNT_COLUMNS] * 7
    assert [row["field"] for row in counted.fields] == [*raw_output.FIELDS, "total"]
    assert all((row["set"], row["classes"]) == ("made-up", "mechanical")
               for table in (counted.fields, counted.cases, counted.totals) for row in table)
    price = by_field(counted, "price")
    assert {name: price[name] for name in COUNT_COLUMNS[3:]} == {
        "reference_items": 5, "comparable_items": 3, "compared_items": 2, "no_output_items": 1, "coverage": "2 of 3",
        "differences": 2, "detections": 1, "misses": 1, "misses_by_abstention": 0, "recall": "1 of 2",
        "flags": 1, "true_flags": 1, "false_flags": 0, "precision": "1 of 1", "cases_with_differences": 2, "cases_with_flags": 1,
        "flags_outside": 0, "justified_items": 2, "abstention_items": 0, "could_not_compare_items_of_the_aid": 1,
        "matches_on_unsettled_items": 0, "cannot_be_mapped": 0, "flags_with_a_value_without_a_traceable_source": 0,
        "flags_source_not_checked": 0, "duplicate_flags": 2, "unscored_rows": 0}
    unit, quantity, delivery = by_field(counted, "unit"), by_field(counted, "quantity"), by_field(counted, "delivery_date")
    assert (unit["flags"], unit["true_flags"], unit["false_flags"], unit["precision"], unit["recall"]) == (1, 0, 1, "0 of 1", "undefined")
    assert (unit["flags_with_a_value_without_a_traceable_source"], unit["cannot_be_mapped"], unit["no_output_items"]) == (1, 1, 1)
    assert (quantity["flags_outside"], quantity["flags"], quantity["flags_source_not_checked"], quantity["comparable_items"]) == (
        1, 0, 1, 2)
    assert (delivery["differences"], delivery["misses"], delivery["misses_by_abstention"], delivery["abstention_items"],
            delivery["recall"]) == (
        2, 2, 1, 1, "0 of 2")
    assert (delivery["compared_items"], delivery["coverage"]) == (1, "1 of 2")          # the abstention is no comparison
    total = by_field(counted, "total")
    assert (total["differences"], total["detections"], total["misses"], total["recall"], total["flags"], total["precision"]) == (
        5, 1, 4, "1 of 5", 2, "1 of 2")
    assert (total["cases_with_differences"], total["cases_with_flags"]) == (3, 1)        # distinct cases: not the sum of the fields
    for column in ADDITIVE:
        assert total[column] == sum(row[column] for row in counted.fields[:-1]), column


def test_what_is_not_counted_and_what_was_not_found_in_the_counts():
    """The field items of a missing line are reference items and are in no other count. A reference line that the reading does not
    hold waits for the rater: its row is unscored, its difference neither a detection nor a miss, and the case says how many."""
    found = reading(flag(1, "line_presence", "2", "", "missing line", doc_value=None), match("price", "1", "1"), pairs=(pair("1", "1"),),
                    unmatched="2")
    items = [answered(), item(2, "line_presence", "2", "missing on the confirmation", "missing"),
             item(3, "price", "2", "missing on the confirmation", "missing"), item(4, "price", "1", "same"),
             item(5, "price", "10", "different")]
    counted = count(sheet_rows("C01", found, items), "development", "mechanical")
    price, presence = by_field(counted, "price"), by_field(counted, "line_presence")
    assert (price["reference_items"], price["comparable_items"], price["differences"], price["detections"], price["misses"]) == (
        3, 2, 1, 0, 0)
    assert (price["unscored_rows"], price["no_output_items"], price["recall"]) == (1, 1, "withheld: unscored rows")
    assert (presence["differences"], presence["detections"], presence["flags"], presence["unscored_rows"]) == (1, 1, 1, 0)
    [case] = counted.cases
    assert (case["line_ids_not_found"], case["unscored_rows"], case["differences"], case["detections"], case["misses"]) == (1, 1, 2, 1, 0)


def test_a_field_without_items_has_undefined_ratios_and_keeps_its_row():
    empty = count([row for row in small_sheet() if row["field"] != "required_info"], "made-up", "mechanical")
    required = by_field(empty, "required_info")
    assert (required["reference_items"], required["coverage"], required["recall"], required["precision"]) == (
        0, "undefined", "undefined", "undefined")


def test_under_the_rater_a_judgement_row_without_a_class_is_unscored_and_no_recall_is_written():
    """The proposals are not counted for the rater: a flag on a comparable difference that the rater has not judged is neither
    a detection nor a miss, and while one is left no recall and no precision is written, for any field."""
    unscored = count(small_sheet(), "held-out", "rater")
    price, total = by_field(unscored, "price"), by_field(unscored, "total")
    assert (price["unscored_rows"], price["differences"], price["detections"], price["misses"], price["flags"]) == (1, 2, 0, 1, 0)
    assert {row["recall"] for row in unscored.fields} == {row["precision"] for row in unscored.fields} == {"withheld: unscored rows"}
    assert total["unscored_rows"] == 1 and total["coverage"] == "13 of 16" and "Unscored rows: 1" in "\n".join(unscored.printed)
    judged = count(changed(small_sheet(), "C01", "F1", detection_class="false flag"), "held-out", "rater")
    price = by_field(judged, "price")
    assert (price["unscored_rows"], price["detections"], price["misses"], price["flags"], price["false_flags"], price["recall"],
            price["precision"]) == (
        0, 0, 2, 1, 1, "0 of 2", "0 of 1")
    agreed = count(changed(small_sheet(), "C01", "F1", detection_class="true flag"), "held-out", "rater")
    assert [{k: v for k, v in row.items() if k not in ("set", "classes")} for row in agreed.fields] == [
        {k: v for k, v in row.items() if k not in ("set", "classes")} for row in count(small_sheet(), "made-up", "mechanical").fields]


def test_a_class_the_rater_typed_stands_before_the_proposal_under_both_bases():
    rows = changed(small_sheet(), "C01", "F1", detection_class="false flag")
    assert by_field(count(rows, "development", "mechanical"), "price")["detections"] == 0


def test_a_false_flag_without_a_reference_item_counts_in_the_flags():
    """The sheet writes an item of the aid that belongs to no reference item as cannot be mapped; a rater who finds it a false flag
    types that, and the flags and the precision take it in."""
    rows = [row | {"flag_id": "F9", "detection_class": "false flag", "note": "aid: different"}
            if (row["detection_class"], row["field"]) == ("cannot be mapped", "unit") else row for row in small_sheet()]
    unit = by_field(count(rows, "made-up", "mechanical"), "unit")
    assert (unit["cannot_be_mapped"], unit["flags"], unit["false_flags"], unit["precision"]) == (0, 2, 2, "0 of 2")


def test_the_cases_of_the_counts_keep_the_cases_with_zero_counts():
    counted = count(small_sheet(), "made-up", "mechanical")
    assert [tuple(row) for row in counted.cases] == [CASE_COLUMNS] * 5
    assert [list(row.values())[2:] for row in counted.cases] == [
        # case, output, version status, line errors, ids not found, differences, detections, misses, flags, true, false,
        # copies, abstentions, unmapped, unscored
        ["C01", "yes", "right", 1, 0, 2, 1, 1, 2, 1, 1, 2, 0, 0, 0],
        ["C02", "no output", "no output", 0, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0],
        ["C03", "not run", "not run", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        ["C04", "yes", "right", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        ["C05", "yes", "version-selection error", 1, 0, 2, 0, 2, 0, 0, 0, 0, 1, 2, 0]]


def test_the_totals_carry_their_denominators_and_a_case_not_run_is_outside_every_one():
    counted = count(small_sheet(), "made-up", "mechanical")
    assert [tuple(row) for row in counted.totals] == [TOTAL_COLUMNS] * len(counted.totals)
    assert total_of(counted, "cases") == (5, "") and total_of(counted, "cases with output") == (3, 5)
    assert total_of(counted, "cases without output (the run failed)") == (1, 5) and total_of(counted, "cases not run") == (1, 5)
    assert total_of(counted, "cases whose first output is not kept") == (0, 5)
    assert total_of(counted, "version-selection errors") == (1, 3) and total_of(counted, "abstentions on the version") == (0, 3)
    assert total_of(counted, "forced matches") == (1, 1) and total_of(counted, "missed splits or merges") == (1, 3)
    assert total_of(counted, "line-matching errors") == (2, 6) and total_of(counted, "lines left uncertain") == (0, 6)
    assert total_of(counted, "false flags (the mean per case is the value over the denominator)") == (1, 3)
    assert total_of(counted, "false flags per case: median") == (0, 3) and total_of(counted, "false flags per case: lowest") == (0, 3)
    assert total_of(counted, "false flags per case: highest") == (1, 3) and total_of(counted, "cases with a false flag") == (1, 3)
    assert total_of(counted, "duplicate flags (the mean per case is the value over the denominator)") == (2, 3)
    assert total_of(counted, "cases with a version-selection error: differences") == (2, "")
    assert total_of(counted, "cases with a version-selection error: detections") == (0, 2)
    assert total_of(counted, "cases with a version-selection error: misses") == (2, 2)
    assert total_of(counted, "cases with a version-selection error: true flags") == (0, 0)
    assert total_of(counted, "fields counted") == (6, 5)
    [fields] = [row for row in counted.totals if row["quantity"] == "fields counted"]
    assert "quantity and unit as one field" in fields["what_the_denominator_is"]
    assert all(row["what_the_denominator_is"] for row in counted.totals if row["denominator"] != "")


def test_the_median_of_an_even_number_of_cases_is_written_without_a_decimal_point():
    """A decimal point would read as a date or a thousand in a spreadsheet of another country."""
    rows = [row for row in small_sheet() if row["case_id"] in ("C01", "C04")]
    assert total_of(count(rows, "made-up", "mechanical"), "false flags per case: median") == ("between 0 and 1", 2)


def test_no_recall_and_no_precision_for_the_challenge_set():
    counted = count(small_sheet(), "challenge", "mechanical")
    assert {row["recall"] for row in counted.fields} == {row["precision"] for row in counted.fields} == {"not reported: challenge set"}
    assert by_field(counted, "total")["detections"] == 1 and "recall" not in "\n".join(counted.printed[1:])
    assert "constructed functional challenges" in counted.printed[0]


@pytest.mark.parametrize("set_name,classes,words,never", [
    ("made-up", "mechanical", "the aid's behaviour on made-up documents", "claim 1"),
    ("development", "mechanical", "development cases: not part of the results", "claim 1"),
    ("held-out", "rater", "the aid's raw output on the held-out cases (claim 1)", "made-up"),
    ("held-out", "mechanical", "not the scored record", "(claim 1)"),
])
def test_the_first_printed_line_names_the_set_and_the_basis_and_claims_no_more_than_the_set_allows(set_name, classes, words, never):
    counted = count(changed(small_sheet(), "C01", "F1", detection_class="true flag"), set_name, classes)
    assert counted.printed[0].startswith(f"Set {set_name}, classes {classes}: ") and words in counted.printed[0]
    assert never not in "\n".join(counted.printed)
    assert counted.printed[1:] == [
        "Cases: 5; without output: 1; not run: 1; first output not kept: 0.",
        "Version-selection errors: 1 of 3; abstentions on the version: 0 of 3; no stored version: 0 of 3.",
        "Line-matching errors: 2 of 6 lines; lines left uncertain: 0.",
        "Unscored rows: 0.",
        "Total: 5 differences, 1 detected, 4 missed (1 by abstention); 2 flags, 1 true, 1 false; recall 1 of 5; precision 1 of 2; "
        "coverage 13 of 16.",
        "Six fields are counted where the protocol names five: it has quantity and unit as one field."]


def test_the_three_files_of_the_counts_are_csv_with_the_set_and_the_basis_in_every_row():
    files = counts_files(count(small_sheet(), "made-up", "mechanical"))
    assert list(files) == ["raw_output_counts.csv", "raw_output_cases.csv", "raw_output_totals.csv"]
    for data, columns in zip(files.values(), (COUNT_COLUMNS, CASE_COLUMNS, TOTAL_COLUMNS)):
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")))
        assert tuple(rows[0]) == columns and b"\r" not in data
        assert all((row["set"], row["classes"]) == ("made-up", "mechanical") for row in rows)


@pytest.mark.parametrize("reader,kind,found,cases,failed", [
    ("recorded", "confirmation", "13 of 13", 20, 0), ("parser", "confirmation", "12 of 13", 20, 2),
    ("recorded", "cart", "2 of 2", 2, 0), ("parser", "cart", "2 of 2", 2, 0)])
def test_the_counts_of_the_made_up_set_by_the_mechanical_proposals(made_up, reader, kind, found, cases, failed):
    """The figures to check before the build: 13 of the 13 declared differences of the confirmations with the recorded reader, 12
    of 13 with the parser, whose reads of the two scans, MU05 and MU16, fail, so that the one difference of MU16 is the miss, and
    2 of 2 for the carts with both; no false flag anywhere."""
    counted = count(read_sheet(sheet_csv(made_up_rows(made_up[reader], kind)), "raw_output_scores.csv"), "made-up", "mechanical")
    total = by_field(counted, "total")
    assert (total["recall"], total["false_flags"], total["unscored_rows"], total["cannot_be_mapped"]) == (found, 0, 0, 0)
    assert total["precision"] == f"{total['flags']} of {total['flags']}" and total["flags"] == total["detections"] == int(found.split()[0])
    assert total_of(counted, "cases") == (cases, "") and total_of(counted, "cases without output (the run failed)") == (failed, cases)
    assert total_of(counted, "version-selection errors") == (0, cases - failed) and total_of(counted, "line-matching errors")[0] == 0
    assert len(counted.cases) == cases and total["justified_items"] == (15 if kind == "confirmation" else 0)
    if kind == "confirmation":
        assert total["could_not_compare_items_of_the_aid"] == 11           # the merged line of MU19: four items on eight reference items


def test_the_counting_code_loads_nothing_of_the_case_folders():
    """The totals are derived from the item records alone (scoring manual, section 8): reading a sheet and counting it imports
    nothing of the module that reads the case folders."""
    code = ("import sys; from aid.evalscripts import raw_output as r; rows = r.read_sheet(sys.stdin.buffer.read(), 'sheet.csv'); "
            "r.counts_files(r.count(rows, 'made-up', 'mechanical')); print('aid.evalscripts.case_runs' in sys.modules)")
    done = subprocess.run([sys.executable, "-B", "-W", "error", "-c", code], input=sheet_csv(small_sheet()), capture_output=True,
                          cwd=REPO_ROOT)
    assert done.stdout.decode().strip() == "False" and done.returncode == 0, done.stderr.decode()


# --- the two subcommands, through the records command ---

@pytest.fixture
def command(capsys):
    """The records command (aid/evalscripts/records.py), which holds what sheet and counts share with log: run with these
    arguments it gives its exit code and what it printed, or for a refusal the sentence on stderr."""
    records = pytest.importorskip("aid.evalscripts.records")

    def run(*arguments, refused: bool = False) -> str:
        capsys.readouterr()
        if refused:
            with pytest.raises(SystemExit) as stopped:
                records.main([str(argument) for argument in arguments])
            assert stopped.value.code == 2
            return capsys.readouterr().err
        assert records.main([str(argument) for argument in arguments]) == 0
        return capsys.readouterr().out
    return run


def sheet_arguments(root: Path, out: Path, *more, set_name: str = "made-up", references: bool = True) -> list:
    """The arguments of sheet on a demo folder (or the copy of one), against the references of the made-up set."""
    return ["sheet", "--out", out, "--set", set_name, "--case-list", root / "cases.csv", "--cases-root", root / "cases",
            *(["--references", MADE_UP_REFERENCES] if references else []), *more]


def test_sheet_writes_the_two_files_and_a_manifest_and_only_reads_the_cases(made_up, tmp_path, command):
    """The made-up confirmations against the made-up references: the rows of the two files are those of the cases, the manifest
    names every file with its sha256 and holds no path, and everything under the cases root and the PO store is as it was. What
    is printed counts cases and rows and tallies no class."""
    root, out = made_up["recorded"], tmp_path / "records"
    before = snapshot(root / "cases"), snapshot(root / "po_store")
    printed = command(*sheet_arguments(root, out))
    assert (snapshot(root / "cases"), snapshot(root / "po_store")) == before
    assert sorted(path.name for path in out.iterdir()) == ["raw_output_scores.csv", "raw_output_scores.xlsx", "sheet_manifest.json"]
    rows = made_up_rows(root, "confirmation")
    assert read_sheet((out / "raw_output_scores.csv").read_bytes(), "raw_output_scores.csv") == rows
    assert read_sheet((out / "raw_output_scores.xlsx").read_bytes(), "raw_output_scores.xlsx") == rows
    assert f"Sheet of the set made-up, control point confirmation: 20 cases, {len(rows)} rows, against the references." in printed
    assert "a rater confirms or corrects" in printed and "restricted storage" in printed
    for told in ("true flag", "false flag", "miss", str(root / "cases"), "250101"):
        assert told not in printed
    manifest = json.loads((out / "sheet_manifest.json").read_text(encoding="utf-8"))
    assert (manifest["subcommand"], manifest["set"], manifest["kind"], manifest["cases"]) == ("sheet", "made-up", "confirmation", 20)
    assert [entry["name"] for entry in manifest["inputs"]] == ["cases.csv", "references.csv"]
    assert {entry["name"]: entry["sha256"] for entry in manifest["outputs"]} == {
        name: hashlib.sha256((out / name).read_bytes()).hexdigest() for name in ("raw_output_scores.csv", "raw_output_scores.xlsx")}
    assert str(root) not in json.dumps(manifest) and manifest["options"]["cases_root"] == "cases"
    assert any("a rater confirms or corrects" in note for note in manifest["notes"])


def test_one_sheet_holds_one_control_point(made_up, tmp_path, command):
    printed = command(*sheet_arguments(made_up["parser"], tmp_path / "records", "--kind", "cart"))
    assert "control point cart: 2 cases" in printed
    rows = read_sheet((tmp_path / "records" / "raw_output_scores.csv").read_bytes(), "raw_output_scores.csv")
    assert {row["case_id"] for row in rows} == {"CART01", "CART02"} and rows == made_up_rows(made_up["parser"], "cart")


def test_sheet_without_references_lists_the_items_of_the_aid_and_gives_no_class(made_up, tmp_path, command):
    printed = command(*sheet_arguments(made_up["recorded"], tmp_path / "records", references=False))
    rows = read_sheet((tmp_path / "records" / "raw_output_scores.csv").read_bytes(), "raw_output_scores.csv")
    assert "without references" in printed and len({row["case_id"] for row in rows}) == 20
    assert all(not row["detection_class"] and not row["reference_item_id"] for row in rows)
    assert sum(bool(row["flag_id"]) for row in rows) == 24


@pytest.mark.parametrize("more,sentence", [
    (["--set", "held-out"], "--set held-out needs --references and --references-locked YYYY-MM-DD"),
    (["--set", "held-out", "--references", MADE_UP_REFERENCES], "--set held-out needs --references and --references-locked YYYY-MM-DD"),
    (["--set", "challenge"], "--set challenge needs --references"),
    (["--set", "made-up", "--references-locked", "2026-12-01"], "--references-locked needs --references"),
    (["--set", "held-out", "--references", MADE_UP_REFERENCES, "--references-locked", "1 December"],
     "--references-locked is the day the references were locked, as YYYY-MM-DD"),
    (["--set", "made-up", "--kind", "quotation"], "invalid choice: 'quotation'"),
    (["--set", "pilot"], "invalid choice: 'pilot'"),
])
def test_sheet_refuses_an_order_of_work_that_the_protocol_does_not_allow(made_up, tmp_path, command, more, sentence):
    """For held-out cases the sheet is made only against references that are locked, which the person at the keyboard states with
    the day; for the challenge set only against references. Nothing is written."""
    root, out = made_up["recorded"], tmp_path / "records"
    error = command("sheet", "--out", out, "--case-list", root / "cases.csv", "--cases-root", root / "cases", *more, refused=True)
    assert sentence in error and not out.exists()


def test_the_statement_that_the_references_are_locked_goes_into_the_manifest(made_up, tmp_path, command):
    printed = command(*sheet_arguments(made_up["recorded"], tmp_path / "records", "--references-locked", "2026-12-01", set_name="held-out"))
    manifest = json.loads((tmp_path / "records" / "sheet_manifest.json").read_text(encoding="utf-8"))
    assert manifest["statements"] == {"references_locked": "2026-12-01"} and manifest["set"] == "held-out"
    assert "protocol section 7.1" in printed


def test_a_case_without_a_run_is_refused_by_its_id_unless_it_is_allowed(copied, tmp_path, command):
    """A case that was never run must not read as a case without output, whose differences are misses. With --allow-not-run it
    has one row that says so."""
    shutil.rmtree(next((copied / "cases").glob("250107_*")))
    shutil.rmtree(next((copied / "cases").glob("250112_*")))
    before = snapshot(copied)
    error = command(*sheet_arguments(copied, tmp_path / "records"), refused=True)
    assert "no run of the aid found for 2 cases of the case list: MU07, MU12" in error and "--allow-not-run" in error
    assert snapshot(copied) == before and "250107" not in error and not (tmp_path / "records").exists()
    printed = command(*sheet_arguments(copied, tmp_path / "records", "--allow-not-run"))
    rows = read_sheet((tmp_path / "records" / "raw_output_scores.csv").read_bytes(), "raw_output_scores.csv")
    assert [(row["version_status"], row["note"]) for row in rows if row["case_id"] in ("MU07", "MU12")] == [("not run", "aid: not run")] * 2
    assert "Not run: 2 cases" in printed
    assert json.loads((tmp_path / "records" / "sheet_manifest.json").read_text(encoding="utf-8"))["cases_not_run"] == 2


def test_two_documents_for_one_purchase_order_are_refused_by_the_case_id(copied, tmp_path, command):
    """The folders of a purchase order hold two documents and the case list names neither: nothing is guessed."""
    [folder] = (copied / "cases").glob("250107_*")
    second = folder.with_name("250107_20261201T100000")
    shutil.copytree(folder, second)
    meta = json.loads((second / "case.json").read_text(encoding="utf-8"))
    meta["files"][0]["sha256"] = "cd" * 32
    (second / "case.json").write_text(json.dumps(meta), encoding="utf-8")
    cases = (copied / "cases.csv").read_text(encoding="utf-8").replace(str(demo.MADEUP_OUT / "MU07_conf.pdf"), "")
    (copied / "cases.csv").write_text(cases, encoding="utf-8")
    error = command(*sheet_arguments(copied, tmp_path / "records"), refused=True)
    assert "case MU07: 2 documents for this purchase order: name the folder in the case list" in error and "250107" not in error


def test_sheet_is_refused_while_the_aid_runs_on_the_data_folder(copied, tmp_path, command):
    (copied / START_STATE).write_text(json.dumps({"launcher": {"heartbeat": time.time()}}), encoding="utf-8")
    error = command(*sheet_arguments(copied, tmp_path / "records"), refused=True)
    assert "the aid runs on this data folder: stop it first, or give --while-running" in error and not (tmp_path / "records").exists()
    assert "20 cases" in command(*sheet_arguments(copied, tmp_path / "records", "--while-running"))


def test_sheet_refuses_inputs_that_do_not_hold_what_the_sheet_needs(copied, tmp_path, command):
    out = tmp_path / "records"
    labels = tmp_path / "references.csv"
    labels.write_bytes(MADE_UP_REFERENCES.read_bytes().replace(b",different,", b",differs,", 1))
    error = command(*sheet_arguments(copied, out, references=False), "--references", labels, refused=True)
    assert "--references: references.csv: MU07-003 has the label 'differs'" in error
    kept = [line for line in MADE_UP_REFERENCES.read_bytes().splitlines(keepends=True) if not line.startswith(b"MU02,")]
    labels.write_bytes(b"".join(kept))
    error = command(*sheet_arguments(copied, out, references=False), "--references", labels, refused=True)
    assert "--references: references.csv holds no item of 1 case of the case list: MU02" in error
    error = command(*sheet_arguments(copied, out, references=False), "--references", tmp_path / "none*.csv", refused=True)
    assert "--references: no file matches none*.csv" in error
    (copied / "cases.csv").write_text("case_id,po_number,kind\nCART01,250201,cart\n", encoding="utf-8")
    error = command(*sheet_arguments(copied, out), refused=True)
    assert "--case-list: cases.csv holds no case of the kind confirmation" in error
    error = command("sheet", "--out", out, "--set", "made-up", "--case-list", copied / "cases.csv", "--cases-root", copied / "nowhere",
                    refused=True)
    assert "--cases-root is no folder" in error and not out.exists()


def test_sheet_writes_nothing_into_a_folder_that_holds_a_sheet_already(made_up, tmp_path, command):
    """There is no option to write over a sheet: a rater may have filled it."""
    command(*sheet_arguments(made_up["recorded"], tmp_path / "records"))
    filled = (tmp_path / "records" / "raw_output_scores.csv").read_bytes()
    assert "raw_output_scores.csv is already in" in command(*sheet_arguments(made_up["recorded"], tmp_path / "records"), refused=True)
    assert (tmp_path / "records" / "raw_output_scores.csv").read_bytes() == filled


@pytest.mark.parametrize("name", ["raw_output_scores.csv", "raw_output_scores.xlsx", "raw_output_sc*.csv"])
def test_counts_writes_three_files_and_a_manifest_from_the_sheet_alone(made_up, tmp_path, command, name):
    """The sheet as the sheet command wrote it, from the CSV, from the workbook and through a wildcard: the same counts, by the
    mechanical proposals, which the first printed line and the manifest say."""
    command(*sheet_arguments(made_up["parser"], tmp_path / "records"))
    out = tmp_path / "counts"
    printed = command("counts", "--out", out, "--set", "made-up", "--classes", "mechanical", "--sheet", tmp_path / "records" / name)
    assert printed.startswith("Set made-up, classes mechanical: the aid's behaviour on made-up documents")
    assert "recall 12 of 13; precision 12 of 12" in printed and "Cases: 20; without output: 2; not run: 0" in printed
    assert "claim 1" not in printed
    assert sorted(path.name for path in out.iterdir()) == ["counts_manifest.json", "raw_output_cases.csv", "raw_output_counts.csv",
                                                           "raw_output_totals.csv"]
    with open(out / "raw_output_counts.csv", encoding="utf-8", newline="") as file:
        total = list(csv.DictReader(file))[-1]
    assert (total["field"], total["recall"], total["precision"], total["false_flags"]) == ("total", "12 of 13", "12 of 12", "0")
    manifest = json.loads((out / "counts_manifest.json").read_text(encoding="utf-8"))
    assert (manifest["subcommand"], manifest["set"], manifest["classes"], manifest["cases"]) == ("counts", "made-up", "mechanical", 20)
    assert [entry["name"] for entry in manifest["inputs"]] == [name if "*" not in name else "raw_output_scores.csv"]
    read = (tmp_path / "records" / manifest["inputs"][0]["name"]).read_bytes()
    assert manifest["inputs"][0]["sha256"] == hashlib.sha256(read).hexdigest()
    assert any("Six fields are counted" in note for note in manifest["notes"])
    assert "attempts abandoned on a service failure leave no record in a case folder" in manifest["notes"]


def test_counts_refuses_a_sheet_with_a_cell_outside_its_list_and_writes_nothing(tmp_path, command):
    sheet = tmp_path / "filled.csv"
    sheet.write_bytes(as_saved(changed(small_sheet(), "C01", "F1", detection_class="true flag?")))
    error = command("counts", "--out", tmp_path / "counts", "--set", "held-out", "--classes", "rater", "--sheet", sheet, refused=True)
    assert "--sheet: filled.csv, row 3: the column detection_class holds 'true flag?': it is empty or one of true flag, false flag" in error
    assert not (tmp_path / "counts").exists()
    for arguments, sentence in ((["--classes", "rater"], "the following arguments are required: --set"),
                                (["--set", "held-out"], "the following arguments are required: --classes"),
                                (["--set", "held-out", "--classes", "guess"], "invalid choice: 'guess'")):
        assert sentence in command("counts", "--out", tmp_path / "counts", "--sheet", sheet, *arguments, refused=True)
    sheet.write_bytes(sheet_csv([]))
    assert "--sheet: filled.csv holds no row" in command("counts", "--out", tmp_path / "counts", "--set", "held-out", "--classes", "rater",
                                                        "--sheet", sheet, refused=True)


def test_counts_of_a_sheet_that_a_rater_filled_say_claim_1_only_for_the_held_out_set(tmp_path, command):
    sheet = tmp_path / "filled.xlsx"
    sheet.write_bytes(sheet_xlsx(changed(small_sheet(), "C01", "F1", detection_class="true flag")))
    printed = command("counts", "--out", tmp_path / "counts", "--set", "held-out", "--classes", "rater", "--sheet", sheet)
    assert "(claim 1)" in printed.splitlines()[0] and "recall 1 of 5; precision 1 of 2" in printed
    printed = command("counts", "--out", tmp_path / "other", "--set", "development", "--classes", "rater", "--sheet", sheet)
    assert "claim 1" not in printed and "not part of the results" in printed


def test_counts_refuses_a_set_that_differs_from_the_set_of_the_sheet(made_up, tmp_path, command):
    """The sheet's manifest beside it says which set it was made of: a sheet of the made-up set is never counted as held-out, and
    a sheet saved again beside it under another name or with semicolons is still that sheet."""
    command(*sheet_arguments(made_up["recorded"], tmp_path / "records"))
    rows = read_sheet((tmp_path / "records" / "raw_output_scores.csv").read_bytes(), "raw_output_scores.csv")
    rated = tmp_path / "records" / "rated_semi.csv"
    rated.write_bytes(as_saved(rows))
    for sheet in (tmp_path / "records" / "raw_output_scores.csv", rated):
        error = command("counts", "--out", tmp_path / "counts", "--set", "held-out", "--classes", "rater", "--sheet", sheet,
                        refused=True)
        assert ("--set held-out is not the set of the sheet: the sheet_manifest.json beside it says made-up; give --set made-up"
                in error)
        assert not (tmp_path / "counts").exists()
    printed = command("counts", "--out", tmp_path / "counts", "--set", "made-up", "--classes", "mechanical", "--sheet", rated)
    assert "set could not be confirmed" not in printed
    manifest = json.loads((tmp_path / "counts" / "counts_manifest.json").read_text(encoding="utf-8"))
    assert manifest["sheet_manifest"]["set"] == "made-up" and manifest["sheet_manifest"]["kind"] == "confirmation"
    assert manifest["sheet_manifest"]["sha256"] == hashlib.sha256((tmp_path / "records" / "sheet_manifest.json").read_bytes()).hexdigest()


def test_counts_of_a_held_out_sheet_whose_manifest_states_no_locked_references_are_refused(made_up, tmp_path, command):
    command(*sheet_arguments(made_up["recorded"], tmp_path / "records"))
    manifest = tmp_path / "records" / "sheet_manifest.json"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace('"set": "made-up"', '"set": "held-out"'), encoding="utf-8")
    error = command("counts", "--out", tmp_path / "counts", "--set", "held-out", "--classes", "rater", "--sheet",
                    tmp_path / "records" / "raw_output_scores.csv", refused=True)
    assert "the sheet_manifest.json beside it states no day on which the references were locked" in error


def test_counts_of_a_sheet_without_its_manifest_say_that_the_set_could_not_be_confirmed(tmp_path, command):
    sheet = tmp_path / "filled.csv"
    sheet.write_bytes(as_saved(small_sheet()))
    printed = command("counts", "--out", tmp_path / "counts", "--set", "held-out", "--classes", "rater", "--sheet", sheet)
    said = "The set could not be confirmed from the sheet: no sheet_manifest.json stands beside it."
    assert said in printed
    manifest = json.loads((tmp_path / "counts" / "counts_manifest.json").read_text(encoding="utf-8"))
    assert manifest["sheet_manifest"] is None and said in manifest["notes"]


def test_sheet_of_real_cases_writes_nothing_into_a_clone_of_a_repository(made_up, tmp_path, command):
    """The sheet of real cases is a record of restricted storage; the sheet of the made-up set may stand in a clone, and so may
    counts, which may go to the repository."""
    clone = tmp_path / "clone"
    (clone / ".git").mkdir(parents=True)
    root = made_up["recorded"]
    error = command(*sheet_arguments(root, clone / "sheet", set_name="development"), refused=True)
    assert "--out lies inside a clone of a repository (clone holds a .git)" in error and not (clone / "sheet").exists()
    command(*sheet_arguments(root, clone / "made_up_sheet"))
    command("counts", "--out", clone / "counts", "--set", "made-up", "--classes", "mechanical", "--sheet",
            clone / "made_up_sheet" / "raw_output_scores.csv")
    assert (clone / "counts" / "raw_output_counts.csv").exists()


def test_sheet_says_when_it_cannot_check_whether_the_aid_runs(copied, tmp_path, command):
    """The launcher's state file lies next to the settings file: a cases root without a settings file above it gives nothing to
    check, and the command says so."""
    unchecked = "whether the aid runs could not be checked"
    (copied / START_STATE).write_text("{}", encoding="utf-8")            # the state file of a launcher that says nothing
    assert unchecked not in command(*sheet_arguments(copied, tmp_path / "records"))
    (copied / START_STATE).unlink()
    assert unchecked in command(*sheet_arguments(copied, tmp_path / "again"))
