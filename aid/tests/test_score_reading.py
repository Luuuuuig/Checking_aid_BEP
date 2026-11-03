"""Tests of the reading scorer (aid/evalscripts/score_reading.py): expected_fields, score_reading and choose. The readings scored are
the made-up perfect readings of the specs, altered by hand, and the parser baseline's; no test calls an AI service or the network."""
from pathlib import Path

import pytest

from aid.evalscripts.score_reading import (HEADER_FIELDS, LINE_FIELDS, ReadingScore, choose, expected_fields, score_reading,
                                        score_unread)
from aid.madeup.generate import load_specs
from aid.readers.pdf_text import TextLine, extract_lines
from aid.readers.supplier_parser import detect_template, load_templates, read_with_template
from aid.schema import DocLine, DocValue, Document, SourceRef
from aid.tests.helpers import document_from_spec

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "aid" / "madeup" / "spec"
CASES = [(spec.case_id, "confirmation" if spec.confirmation else "cart") for spec in load_specs(SPEC_DIR)]    # MU01 to MU20, CART01, CART02


# --- the tests of the brief ---

def test_score_reading_counts(madeup_out):
    exp = expected_fields(Path("aid/madeup/spec/MU01.json"))
    doc = document_from_spec("MU01")                                   # perfect answer
    s = score_reading(doc, exp, extract_lines(madeup_out / "MU01_conf.pdf"))
    assert s.fields_correct == s.fields_total and s.field_accuracy == 1.0
    doc.lines[0].unit_price.value = "99.00"
    s2 = score_reading(doc, exp, extract_lines(madeup_out / "MU01_conf.pdf"))
    assert s2.fields_correct == s.fields_total - 1 and s2.errors[0]["field"] == "unit_price"


def test_decision_rule():
    rows = [("A", 0.95, 0.95, 0.40), ("B", 0.94, 0.94, 0.05), ("C", 0.80, 0.90, 0.01)]   # model, field acc, source acc, cost per case
    assert choose(rows) == "B"


# --- expected_fields ---

def header_of(expected: dict) -> dict:
    """The header entries of expected_fields: the seven header fields, which stand under the line id "doc"."""
    return {name: value for (line_id, name), value in expected.items() if line_id == "doc"}


def test_every_field_of_the_schema_has_an_entry():
    assert HEADER_FIELDS == ("po_reference", "version_hint", "supplier", "document_date", "currency", "incoterm", "capture_date")
    assert LINE_FIELDS == ("item_code", "description", "quantity", "unit", "unit_price", "price_basis", "discount_pct", "net_price", "delivery_date")
    expected = expected_fields(SPEC_DIR / "MU01.json")
    assert len(expected) == len(HEADER_FIELDS) + 3 * len(LINE_FIELDS) and {line_id for line_id, _ in expected} == {"doc", "1", "2", "3"}


def test_a_confirmation_expects_its_header_and_the_values_of_each_line():
    expected = expected_fields(SPEC_DIR / "MU01.json")
    assert header_of(expected) == {"po_reference": "250101", "version_hint": None, "supplier": "S01", "document_date": "2026-10-21",
                                   "currency": "EUR", "incoterm": "DAP Rotterdam", "capture_date": None}     # a confirmation has no capture date
    assert {name: expected["2", name] for name in LINE_FIELDS} == {
        "item_code": "KM-1002", "description": "Klephuis DN25", "quantity": "4", "unit": "pc", "unit_price": "12.50", "price_basis": "1",
        "discount_pct": None, "net_price": None, "delivery_date": "2026-11-16"}


def test_a_value_the_confirmation_does_not_print_is_none():
    assert header_of(expected_fields(SPEC_DIR / "MU03.json"))["version_hint"] == "1"             # MU03 quotes version 1 of its PO
    assert header_of(expected_fields(SPEC_DIR / "MU20.json"))["incoterm"] is None                # MU20 prints no Incoterm
    totals_only = expected_fields(SPEC_DIR / "MU06.json")                                        # MU06 prints line totals and no prices
    assert [totals_only[line_id, name] for line_id in "123" for name in ("unit_price", "price_basis", "net_price")] == [None] * 9
    discount = expected_fields(SPEC_DIR / "MU10.json")
    assert (discount["1", "discount_pct"], discount["2", "discount_pct"]) == ("5", None)
    assert expected_fields(SPEC_DIR / "MU14.json")["1", "delivery_date"] == "2026-W48"
    assert expected_fields(SPEC_DIR / "MU15.json")["1", "delivery_date"] == "2026-11-30/2026-12-04"


def test_a_cart_expects_its_capture_date_and_no_confirmation_header():
    expected = expected_fields(SPEC_DIR / "CART02.json")
    assert header_of(expected) == {"po_reference": "250202", "version_hint": None, "supplier": "S22", "document_date": None,
                                   "currency": "EUR", "incoterm": None, "capture_date": "2026-11-04"}
    assert {line_id for line_id, _ in expected} == {"doc", "1", "2"}
    assert [expected["1", name] for name in ("item_code", "quantity", "unit", "unit_price", "price_basis", "delivery_date")] == [
        "KM-2001", "5", "pc", "12.50", "1", None]                                                # a cart prints no delivery date


@pytest.mark.parametrize(("case", "kind"), CASES, ids=[case for case, _ in CASES])
def test_the_perfect_reading_of_every_case_scores_one(madeup_out, case, kind):
    """The 20 confirmations and the 2 carts: document_from_spec and expected_fields agree on every field, the header and each line."""
    pdf = madeup_out / f"{case}_conf.pdf"
    lines = extract_lines(pdf) if pdf.exists() else []                                   # a cart is an image or an HTML page: no text lines
    expected = expected_fields(SPEC_DIR / f"{case}.json")
    score = score_reading(document_from_spec(case, kind), expected, lines)
    assert (score.fields_total, score.fields_correct, score.field_accuracy, score.errors) == (len(expected), len(expected), 1.0, [])
    assert score.missing_marked_correctly == sum(value is None for value in expected.values())
    if lines:       # a text layer: every value that is printed and read is a source to check, and the perfect reading cites them all right
        assert score.sources_total == sum(value is not None for value in expected.values()) > 0
        assert (score.sources_correct, score.source_accuracy) == (score.sources_total, 1.0)
    else:           # a scan, an image or a web page has no text lines to check a citation against
        assert (score.sources_total, score.sources_correct, score.source_accuracy) == (0, 0, None)


# --- score_reading: the fields ---

@pytest.fixture
def mu01():
    """The perfect reading of MU01 (lines 1 to 3) and the values it should have."""
    return document_from_spec("MU01"), expected_fields(SPEC_DIR / "MU01.json")


def test_a_wrong_value_is_one_error_that_names_the_field_and_both_values(mu01):
    doc, expected = mu01
    doc.lines[1].quantity.value = "5"
    score = score_reading(doc, expected, [])
    assert score.errors == [{"line_id": "2", "field": "quantity", "expected": "4", "got": "5"}]
    assert (score.fields_total, score.fields_correct) == (len(expected), len(expected) - 1)


def test_a_value_the_reading_leaves_out_is_wrong(mu01):
    doc, expected = mu01
    doc.lines[0].unit = DocValue()
    assert score_reading(doc, expected, []).errors == [{"line_id": "1", "field": "unit", "expected": "pc", "got": None}]


def test_a_value_the_document_does_not_print_is_right_as_missing_and_wrong_as_a_value(mu01):
    doc, expected = mu01
    perfect = score_reading(doc, expected, [])
    assert perfect.missing_marked_correctly == 8       # version_hint and capture_date, and discount_pct and net_price on each of the 3 lines
    doc.capture_date = DocValue(value="2026-10-21", text="21-10-2026")                   # a date the confirmation does not show
    score = score_reading(doc, expected, [])
    assert score.errors == [{"line_id": "doc", "field": "capture_date", "expected": None, "got": "2026-10-21"}]
    assert (score.fields_correct, score.missing_marked_correctly) == (perfect.fields_correct - 1, perfect.missing_marked_correctly - 1)


@pytest.mark.parametrize(("value", "right"), [(None, True), ("", False), ("0", False)])
def test_where_the_document_prints_nothing_only_a_missing_value_is_right(mu01, value, right):
    """An empty string and "0" are values, not a missing value (DocValue.missing is "value is None"): to give either where the
    document shows none is wrong."""
    doc, expected = mu01
    perfect = score_reading(doc, expected, [])
    doc.capture_date = DocValue(value=value, text=value)                                   # a confirmation has no capture date
    score = score_reading(doc, expected, [])
    assert score.errors == ([] if right else [{"line_id": "doc", "field": "capture_date", "expected": None, "got": value}])
    assert (score.fields_correct, score.missing_marked_correctly) == ((perfect.fields_correct, perfect.missing_marked_correctly) if right
                                                                      else (perfect.fields_correct - 1, perfect.missing_marked_correctly - 1))


def test_the_lines_are_matched_by_their_number_and_not_by_their_place(mu01):
    doc, expected = mu01
    doc.lines.reverse()
    assert score_reading(doc, expected, []).field_accuracy == 1.0


def test_a_line_the_reading_lacks_makes_all_its_fields_wrong(mu01):
    doc, expected = mu01
    del doc.lines[1]
    score = score_reading(doc, expected, [])
    assert [(e["line_id"], e["field"]) for e in score.errors] == [("2", name) for name in LINE_FIELDS]   # discount_pct and net_price too
    assert score.errors[4] == {"line_id": "2", "field": "unit_price", "expected": "12.50", "got": None, "note": "line missing"}
    assert all(e["got"] is None and e["note"] == "line missing" for e in score.errors)
    assert (score.fields_total, score.fields_correct) == (len(expected), len(expected) - len(LINE_FIELDS))
    assert score.missing_marked_correctly == 8 - 2           # the two fields of line 2 that the document leaves out are not "marked missing"


def test_a_reading_without_lines_has_the_header_and_nothing_else(mu01):
    doc, expected = mu01
    doc.lines = []
    score = score_reading(doc, expected, [])
    assert score.fields_correct == len(HEADER_FIELDS) and len(score.errors) == 3 * len(LINE_FIELDS)


def test_a_line_the_document_does_not_have_is_one_error(mu01):
    doc, expected = mu01
    doc.lines.append(DocLine(line_id="4", item_code=DocValue(value="KM-1004", text="KM-1004")))
    score = score_reading(doc, expected, [])
    assert score.errors == [{"line_id": "4", "field": "extra_line", "expected": None, "got": "KM-1004"}]
    assert (score.fields_total, score.fields_correct) == (len(expected) + 1, len(expected))


def test_a_line_numbered_wrongly_is_a_missing_line_and_an_extra_line(mu01):
    doc, expected = mu01
    doc.lines[0].line_id = "7"
    score = score_reading(doc, expected, [])
    assert [(e["line_id"], e["field"]) for e in score.errors] == [("1", name) for name in LINE_FIELDS] + [("7", "extra_line")]
    assert score.fields_total == len(expected) + 1


def test_a_line_repeated_under_its_number_counts_once_and_the_copy_is_extra(mu01):
    doc, expected = mu01
    doc.lines.append(doc.lines[0].model_copy(deep=True))
    score = score_reading(doc, expected, [])
    assert [(e["line_id"], e["field"]) for e in score.errors] == [("1", "extra_line")]
    assert (score.fields_total, score.fields_correct) == (len(expected) + 1, len(expected))


def test_a_document_that_could_not_be_read_has_no_field_right(mu01):
    """Not an empty reading: that would get the fields the document leaves out right. The document's own fields are all unread."""
    _, expected = mu01
    score = score_unread(expected)
    assert (score.fields_total, score.fields_correct, score.field_accuracy) == (len(expected), 0, 0.0)
    assert (score.sources_total, score.sources_correct, score.source_accuracy, score.missing_marked_correctly) == (0, 0, None, 0)
    assert len(score.errors) == len(expected)
    assert score.errors[0] == {"line_id": "doc", "field": "po_reference", "expected": "250101", "got": None, "note": "document not read"}


# --- score_reading: the sources ---

def text_line(page: int, line: int, text: str) -> TextLine:
    return TextLine(page=page, line=line, text=text, x0=0.0, top=0.0)


LAYER = [text_line(1, 1, "S01"), text_line(1, 2, "1 KM-1001   Slang  4 st\t12,50 Stück"), text_line(2, 1, "Seite 2 van 2")]


def quantity_cited(text: str | None, source: SourceRef | None) -> tuple[Document, dict]:
    """A reading of line 1 whose right quantity has the given text and source, and the quantity it should read as."""
    return (Document(kind="confirmation", lines=[DocLine(line_id="1", quantity=DocValue(value="4", text=text, source=source))]),
            {("1", "quantity"): "4"})


@pytest.mark.parametrize(("text", "source", "right"), [
    pytest.param("4", SourceRef(page=1, line=2, text="not the line"), True, id="the text the source quotes is not looked at"),
    pytest.param("4 st", SourceRef(page=1, line=2, text=""), True, id="a text of two words"),
    pytest.param("KM-1001 Slang", SourceRef(page=1, line=2, text=""), True, id="runs of spaces in the line are one space"),
    pytest.param("st 12,50", SourceRef(page=1, line=2, text=""), True, id="a tab in the line is a space"),
    pytest.param("12,50  Stück", SourceRef(page=1, line=2, text=""), True, id="two spaces in the text are one space"),
    pytest.param("Stu\u0308ck", SourceRef(page=1, line=2, text=""), True, id="an accented letter composed in the other way"),
    pytest.param("km-1001", SourceRef(page=1, line=2, text=""), False, id="the case counts"),
    pytest.param("4", SourceRef(page=1, line=1, text="1 KM-1001   Slang  4 st"), False, id="another line of the page holds no 4"),
    pytest.param("4", SourceRef(page=2, line=2, text=""), False, id="a line the text layer does not have"),
    pytest.param("4", SourceRef(page=3, line=2, text=""), False, id="a page the text layer does not have"),
    pytest.param("2", SourceRef(page=2, line=1, text=""), True, id="the second page"),
    pytest.param("4", None, False, id="no citation"),
    pytest.param(None, SourceRef(page=1, line=2, text=""), False, id="no text to find on the line"),
    pytest.param("  ", SourceRef(page=1, line=2, text=""), False, id="a blank text is on every line and proves nothing"),
])
def test_a_source_is_right_when_the_cited_line_holds_the_text(text, source, right):
    doc, expected = quantity_cited(text, source)
    score = score_reading(doc, expected, LAYER)
    assert (score.fields_correct, score.sources_total, score.sources_correct) == (1, 1, int(right))


def test_only_a_value_that_is_printed_and_right_has_a_source_to_check():
    cited = SourceRef(page=1, line=2, text="")
    line = DocLine(line_id="1", quantity=DocValue(value="4", text="4", source=cited), unit=DocValue(value="m", text="st", source=cited),
                   discount_pct=DocValue())
    expected = {("1", "quantity"): "4", ("1", "unit"): "pc", ("1", "discount_pct"): None, ("1", "net_price"): None}
    score = score_reading(Document(kind="confirmation", lines=[line]), expected, LAYER)
    assert (score.fields_correct, score.sources_total, score.sources_correct) == (3, 1, 1)      # the wrong unit and the two missing values have none


def test_a_wrong_citation_costs_a_source_and_not_a_field(madeup_out, mu01):
    doc, expected = mu01
    lines = extract_lines(madeup_out / "MU01_conf.pdf")
    doc.lines[0].item_code.source.line += 1                      # the row below, which holds KM-1002
    score = score_reading(doc, expected, lines)
    assert score.errors == [] and score.fields_correct == score.fields_total
    assert score.sources_correct == score.sources_total - 1 and score.source_accuracy < 1.0
    doc.supplier.source.page = 2                                  # MU01 has one page
    assert score_reading(doc, expected, lines).sources_correct == score.sources_correct - 1


def test_without_a_text_layer_no_source_is_scored(mu01):
    doc, expected = mu01
    score = score_reading(doc, expected, [])
    assert (score.fields_correct, score.sources_total, score.sources_correct, score.source_accuracy) == (len(expected), 0, 0, None)


def test_the_accuracies_are_shares_and_a_score_without_fields_has_none():
    score = ReadingScore(fields_total=8, fields_correct=6, sources_total=4, sources_correct=3)
    assert (score.field_accuracy, score.source_accuracy) == (0.75, 0.75)
    assert (ReadingScore().field_accuracy, ReadingScore().source_accuracy) == (0.0, None)         # no division by zero


def test_the_parser_baselines_reading_is_scored_by_the_same_function(madeup_out):
    """Nothing in the scorer assumes the LLM reader: the template parser's Document, whose values cite the rows they were cut from,
    scores 1.0 on fields and on sources on each of the 18 confirmations with a text layer (a scan has no template to read it with)."""
    scored = 0
    for case, kind in CASES:
        pdf = madeup_out / f"{case}_conf.pdf"
        template = detect_template(pdf, load_templates()) if pdf.exists() else None
        if template is None:
            continue
        score = score_reading(read_with_template(pdf, template), expected_fields(SPEC_DIR / f"{case}.json"), extract_lines(pdf))
        assert (score.field_accuracy, score.source_accuracy, score.errors) == (1.0, 1.0, []), case
        scored += 1
    assert scored == 18


# --- choose ---

def test_a_model_is_out_when_either_accuracy_is_more_than_two_points_below_the_best():
    rows = [("best", 0.97, 0.97, 0.50), ("weak fields", 0.90, 0.97, 0.01), ("weak sources", 0.97, 0.90, 0.02), ("good", 0.96, 0.96, 0.10)]
    assert choose(rows) == "good"


def test_two_points_below_the_best_is_still_within_and_a_hair_more_is_not():
    """0.97 - 0.95 is 0.020000000000000018 in floating point, which a plain comparison with 0.02 would refuse."""
    assert choose([("best", 0.97, 0.97, 0.50), ("edge", 0.95, 0.95, 0.10)]) == "edge"
    assert choose([("best", 0.97, 0.97, 0.50), ("over", 0.949, 0.97, 0.10)]) == "best"
    assert choose([("best", 0.97, 0.97, 0.50), ("over", 0.97, 0.949, 0.10)]) == "best"


def test_the_best_value_is_taken_for_each_accuracy_on_its_own():
    """A is the best on field accuracy and B on source accuracy; only C is within two points of both."""
    assert choose([("A", 0.97, 0.90, 0.05), ("B", 0.90, 0.97, 0.01), ("C", 0.96, 0.96, 0.10)]) == "C"
    assert choose([("A", 0.99, 0.80, 0.05), ("B", 0.90, 0.90, 0.01), ("C", 0.98, 0.89, 0.10)]) == "C"           # the two bests differ in size


def test_models_of_the_same_cost_keep_the_order_given():
    rows = [("first", 0.95, 0.95, 0.10), ("second", 0.95, 0.95, 0.10)]
    assert (choose(rows), choose(rows[::-1])) == ("first", "second")
    assert choose([("only", 0.50, 0.50, 1.0)]) == "only"


def test_no_choice_is_made_where_no_model_is_close_to_the_best_on_both():
    with pytest.raises(ValueError, match="within 2 percentage points"):
        choose([("X", 0.99, 0.50, 0.1), ("Y", 0.50, 0.99, 0.1)])
    with pytest.raises(ValueError, match="no model"):
        choose([])
