"""Tests of the LLM reader (aid/readers/supplier_llm.py) and of document_from_spec, the perfect reading of a made-up case in
aid/tests/helpers.py. The answers are fake or recorded; no test calls a model."""
import csv
import json
import re
import shutil
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from aid.evalscripts.score_reading import expected_fields, score_reading
from aid.madeup.generate import load_specs
from aid.readers.llm_client import LlmResponse, RecordedClient
from aid.readers.pdf_text import extract_lines
from aid.readers.supplier_llm import ReaderError, documents_to_record, main, read_supplier_document, reading_schema
from aid.schema import DocLine, DocValue, Document, SourceRef
from aid.tests.helpers import answer_from_spec, document_from_spec

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "aid" / "madeup" / "spec"
SPECS = {spec.case_id: spec for spec in load_specs(SPEC_DIR)}
SET = REPO_ROOT / "aid" / "madeup" / "out"           # the committed documents, from which the recorded answers are made
RECORDED = REPO_ROOT / "aid" / "tests" / "fixtures" / "recorded"
HEADER = [name for name, field in Document.model_fields.items() if field.annotation is DocValue]
FIELDS = [name for name, field in DocLine.model_fields.items() if field.annotation is DocValue]


class FakeClient:
    name = "fake"
    def __init__(self, answers): self.answers = list(answers); self.requests = []
    def complete(self, req): self.requests.append(req); return LlmResponse(text=self.answers.pop(0), model="fake", input_tokens=1, output_tokens=1, seconds=0.0)


def test_retry_once_then_error(madeup_out):
    good = document_from_spec("MU01").model_dump_json()
    doc, resp = read_supplier_document(madeup_out / "MU01_conf.pdf", FakeClient(["not json", good]))
    assert resp.attempts == 2 and doc.lines[0].item_code.value == "KM-1001"
    with pytest.raises(ReaderError): read_supplier_document(madeup_out / "MU01_conf.pdf", FakeClient(["bad", "bad"]))

def test_fabricated_source_becomes_uncertain(madeup_out):
    d = document_from_spec("MU01"); d.lines[0].unit_price.source.text = "99,99 does not exist"
    doc, _ = read_supplier_document(madeup_out / "MU01_conf.pdf", FakeClient([d.model_dump_json()]))
    assert doc.lines[0].unit_price.uncertain is True

def test_png_input_sends_image_blocks(madeup_out):
    c = FakeClient([document_from_spec("CART02").model_dump_json()])
    read_supplier_document(madeup_out / "CART02.png", c, kind="cart")
    assert c.requests[0].pdf is None and len(c.requests[0].images) == 1


# The tests below cover what the rulings of 29 October add to the brief's tests above.

def values(doc: Document) -> list[DocValue]:
    """Every DocValue of the document: the header fields, then the fields of each line."""
    return [getattr(doc, name) for name in HEADER] + [getattr(line, name) for line in doc.lines for name in FIELDS]


def read(folder: Path, case_id: str, answer: str, **kwargs) -> Document:
    """The Document the reader makes of the confirmation of `case_id` from a fake client that gives `answer`."""
    return read_supplier_document(folder / f"{case_id}_conf.pdf", FakeClient([answer]), **kwargs)[0]


# --- the reading schema ---

def test_the_reading_schema_asks_for_the_text_of_each_value_and_not_its_value():
    schema = reading_schema()
    assert all(o["additionalProperties"] is False and o["required"] == list(o["properties"]) for o in [schema, *schema["$defs"].values()])
    assert set(schema["properties"]) == {"language", "lines", *HEADER}                 # no kind, source_file, pages or reader
    assert set(schema["$defs"]["ReadLine"]["properties"]) == {"line_id", *FIELDS}
    assert list(schema["$defs"]["ReadValue"]["properties"]) == ["text", "source", "uncertain"]
    assert list(schema["$defs"]["SourceRef"]["properties"]) == ["page", "line", "text"]
    assert {"type": "null"} in schema["properties"]["language"]["anyOf"] and "default" not in json.dumps(schema)


# --- from the text to the value ---

def test_values_are_normalised_from_their_text_field_by_field(madeup_out):
    """MU10 is German: decimal commas, "Stk", a discount column and dates with full stops."""
    doc = read(madeup_out, "MU10", answer_from_spec("MU10"))
    line = doc.lines[0]
    assert [(getattr(line, f).text, getattr(line, f).value) for f in ("item_code", "quantity", "unit", "unit_price", "price_basis", "discount_pct",
                                                                      "delivery_date")] == [
        ("KM-0101", "KM-0101"), ("6", "6"), ("Stk", "pc"), ("20,00", "20.00"), ("1", "1"), ("5 %", "5"), ("16.11.2026", "2026-11-16")]
    assert [(getattr(doc, f).text, getattr(doc, f).value) for f in ("po_reference", "supplier", "document_date", "currency", "incoterm")] == [
        ("250110", "250110"), ("S10", "S10"), ("22.10.2026", "2026-10-22"), ("EUR", "EUR"), ("CPT Rotterdam", "CPT Rotterdam")]
    assert line.net_price == DocValue() and doc.version_hint == DocValue() and doc.capture_date == DocValue()
    assert (doc.kind, doc.source_file, doc.pages, doc.reader) == ("confirmation", "MU10_conf.pdf", 1, "llm:fake:v1")
    assert line.unit_price.source.text == "1 KM-0101 Rückschlagventil DN20 6 Stk 20,00 1 5 % 16.11.2026" and not any(v.uncertain for v in values(doc))


def test_weeks_ranges_and_a_basis_printed_inside_the_price(madeup_out):
    week, span, basis, english = (read(madeup_out, case, answer_from_spec(case)).lines[0] for case in ("MU14", "MU15", "MU09", "MU02"))
    assert (week.delivery_date.text, week.delivery_date.value) == ("KW 48", "2026-W48")                  # in the year of the document date
    assert (span.delivery_date.text, span.delivery_date.value) == ("30.11.2026 - 04.12.2026", "2026-11-30/2026-12-04")
    assert (basis.unit_price.text, basis.unit_price.value, basis.price_basis.text, basis.price_basis.value) == ("12,50 / 100 st", "12.50", "100 st", "100")
    assert (english.unit_price.text, english.unit_price.value, english.price_basis.text, english.price_basis.value) == ("1.234,50", "1234.50", "each", "1")


@pytest.mark.parametrize(("read_language", "given", "expected"), [
    ("nl", None, "1000"), ("de", None, "1000"), ("en", None, "1.000"), (None, None, "1.000"), ("en", "de", "1000"), ("de", "en", "1.000")])
def test_the_language_settles_an_ambiguous_number(madeup_out, read_language, given, expected):
    """ "1.000" is a thousand in Dutch and German and one in English. The language given to the reader wins over the
    language the model reports; with neither, normalize_number's own rule reads it as one."""
    answer = json.loads(answer_from_spec("MU01")) | {"language": read_language}
    answer["lines"][0]["quantity"]["text"] = "1.000"
    assert read(madeup_out, "MU01", json.dumps(answer), language=given).lines[0].quantity.value == expected


def test_blank_unreadable_and_uncited_values(madeup_out):
    """A blank text is no value. A text no normaliser can read keeps its text and is uncertain, and so is a value without a
    source. The model's own mark is kept."""
    answer = json.loads(answer_from_spec("MU01"))
    first = answer["lines"][0]
    first["unit"]["text"] = "  "
    first["quantity"]["text"] = "tien"
    first["delivery_date"]["source"] = None
    first["description"]["uncertain"] = True
    line = read(madeup_out, "MU01", json.dumps(answer)).lines[0]
    assert line.unit == DocValue()
    assert (line.quantity.text, line.quantity.value, line.quantity.uncertain) == ("tien", None, True)
    assert (line.delivery_date.value, line.delivery_date.source, line.delivery_date.uncertain) == ("2026-11-16", None, True)
    assert line.description.uncertain is True and line.item_code.uncertain is False and line.unit_price.uncertain is False


def test_a_full_document_answer_takes_each_value_from_its_text(madeup_out):
    """A Document as the answer (the fake answers of these tests): each value is worked out again from its text. A value
    given without its text is kept. On a PDF with a text layer it is uncertain, because there is no text to find on its
    line; on a scan it keeps the model's mark, unless it has no source."""
    d = document_from_spec("MU01")
    d.lines[0].quantity.value = "999"                                       # the text "10" decides
    d.lines[1].unit_price.text = None                                       # "12.50" without its text: kept, and nothing to check
    doc = read(madeup_out, "MU01", d.model_dump_json())
    assert doc.lines[0].quantity.value == "10"
    assert (doc.lines[1].unit_price.value, doc.lines[1].unit_price.text, doc.lines[1].unit_price.uncertain) == ("12.50", None, True)
    scan = document_from_spec("MU05")
    scan.lines[0].unit_price.text = None                                                # with its source: the model's mark
    scan.lines[1].unit_price.text, scan.lines[1].unit_price.source = None, None         # without a source: uncertain
    doc = read(madeup_out, "MU05", scan.model_dump_json())
    assert [(line.unit_price.value, line.unit_price.uncertain) for line in doc.lines[:2]] == [("27.90", False), ("9.35", True)]


# --- the sources ---

def test_only_the_fabricated_source_becomes_uncertain(madeup_out):
    d = document_from_spec("MU01")
    d.lines[0].unit_price.source.text = "99,99 does not exist"
    d.supplier.source.page = 2                                              # the PDF has one page
    doc = read(madeup_out, "MU01", d.model_dump_json())
    assert [v for v in values(doc) if v.uncertain] == [doc.supplier, doc.lines[0].unit_price]


def test_a_made_up_value_on_a_real_line_is_uncertain(madeup_out):
    """The source quotes MU01's real line 8, but "99,99" is not printed on it."""
    d = document_from_spec("MU01")
    d.lines[0].unit_price.text = "99,99"
    doc = read(madeup_out, "MU01", d.model_dump_json())
    assert doc.lines[0].unit_price.source.text == "1 KM-1001 Hydraulische slang 10 mm, 2 m 10 st 12,50 1 16-11-2026"
    assert doc.lines[0].unit_price.uncertain is True and [v for v in values(doc) if v.uncertain] == [doc.lines[0].unit_price]


def test_a_source_text_found_anywhere_on_the_page_is_not_enough(madeup_out):
    """A source text "1" is on page 1 of MU01 many times over, but the price "12,50" is not on line 3 or the lines around it."""
    d = document_from_spec("MU01")
    d.lines[0].unit_price.source = SourceRef(page=1, line=3, text="1")
    assert read(madeup_out, "MU01", d.model_dump_json()).lines[0].unit_price.uncertain is True


def nothing_shown() -> dict:
    """A value the document does not show, in the reading schema."""
    return {"text": None, "source": None, "uncertain": False}


def test_a_value_wrapped_onto_the_next_line_stays_certain(tmp_path):
    """A description printed over two lines is found across the line it cites and the next one; cited one line too high,
    with the line after it, it is not."""
    path = tmp_path / "wrapped.pdf"
    canvas = Canvas(str(path), pagesize=A4)
    canvas.setFont("Helvetica", 9)
    for y, text in ((700, "S99"), (686, "1 KM-9901 Hydraulische slang"), (672, "10 mm, 2 m 4 st")):
        canvas.drawString(50, y, text)
    canvas.save()
    def shown(text, line, line_text): return {"text": text, "source": {"page": 1, "line": line, "text": line_text}, "uncertain": False}
    row = {"line_id": "1", **{name: nothing_shown() for name in FIELDS}}
    row |= {"item_code": shown("KM-9901", 2, "1 KM-9901 Hydraulische slang"), "description": shown("Hydraulische slang 10 mm, 2 m", 2, "1 KM-9901 Hydraulische slang"),
            "quantity": shown("4", 3, "10 mm, 2 m 4 st")}
    answer = {"language": "nl", **{name: nothing_shown() for name in HEADER}, "supplier": shown("S99", 1, "S99"), "lines": [row]}
    doc, _ = read_supplier_document(path, FakeClient([json.dumps(answer)]))
    assert [(v.text, v.uncertain) for v in (doc.supplier, doc.lines[0].item_code, doc.lines[0].description, doc.lines[0].quantity)] == [
        ("S99", False), ("KM-9901", False), ("Hydraulische slang 10 mm, 2 m", False), ("4", False)]
    row["description"] = shown("Hydraulische slang 10 mm, 2 m", 1, "S99")
    doc, _ = read_supplier_document(path, FakeClient([json.dumps(answer)]))
    assert doc.lines[0].description.uncertain is True


def test_the_sources_of_a_scan_are_not_checked(madeup_out):
    """A scan has no text layer to check a source against, so every value keeps the model's own mark."""
    d = document_from_spec("MU05")
    d.lines[0].unit_price.source.text = "not on the page"
    d.lines[1].quantity.uncertain = True
    doc = read(madeup_out, "MU05", d.model_dump_json())
    assert doc.lines[0].unit_price.uncertain is False and doc.lines[1].quantity.uncertain is True and doc.pages == 1


# --- the request, the retry and the refusals ---

def test_the_retry_shows_the_error_and_counts_both_requests(madeup_out):
    client = FakeClient(['{"lines": []}', answer_from_spec("MU01")])
    doc, response = read_supplier_document(madeup_out / "MU01_conf.pdf", client)
    first, retry = client.requests
    assert retry.user_text.startswith(first.user_text + "\n\nYour previous answer did not match the schema: ")
    assert retry.user_text.endswith(". Answer again.") and "po_reference" in retry.user_text   # the error names what is missing
    assert (retry.system, retry.schema, retry.pdf) == (first.system, first.schema, first.pdf)
    assert (response.attempts, response.input_tokens, response.output_tokens, len(doc.lines)) == (2, 2, 2, 3)


def test_an_answer_without_lines_is_sent_back_once(madeup_out):
    """A document holds order lines; an answer without any would read downstream as every PO line missing."""
    empty = json.dumps(json.loads(answer_from_spec("MU01")) | {"lines": []})
    client = FakeClient([empty, answer_from_spec("MU01")])
    doc, response = read_supplier_document(madeup_out / "MU01_conf.pdf", client)
    assert client.requests[1].user_text.endswith("did not match the schema: the document holds order lines and none were returned. Answer again.")
    assert (response.attempts, len(doc.lines)) == (2, 3)
    with pytest.raises(ReaderError, match="none were returned") as raised:
        read_supplier_document(madeup_out / "MU01_conf.pdf", FakeClient([empty, empty]))
    assert raised.value.attempts == 2


def test_a_failed_read_carries_the_usage_of_its_requests(madeup_out, tmp_path):
    """ReaderError holds the answer of the failed read (both requests counted after a retry) and the number of requests,
    so that the audit record can log tokens, seconds, cost and request id."""
    with pytest.raises(ReaderError) as raised:
        read_supplier_document(madeup_out / "MU01_conf.pdf", FakeClient(["not json", "still not json"]))
    used = raised.value.response
    assert (raised.value.attempts, used.text, used.input_tokens, used.output_tokens, used.attempts) == (2, "still not json", 2, 2, 2)

    class RefusesTheRetry:                     # the client raises for the retry, with the usage of that request
        name = "refusing"
        def __init__(self): self.calls = 0
        def complete(self, req):
            self.calls += 1
            if self.calls == 1:
                return LlmResponse(text="not json", model="m", input_tokens=5, output_tokens=1, seconds=1.0, request_id="req_1")
            raise ReaderError("the model refused to answer", LlmResponse(text="", model="m", input_tokens=6, output_tokens=2, seconds=0.5,
                                                                          request_id="req_2"), attempts=1)
    with pytest.raises(ReaderError, match="refused") as raised:
        read_supplier_document(madeup_out / "MU01_conf.pdf", RefusesTheRetry())
    used = raised.value.response
    assert (raised.value.attempts, used.input_tokens, used.output_tokens, used.seconds, used.request_id) == (2, 11, 3, 1.5, "req_2")
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8\xff")
    with pytest.raises(ReaderError) as raised:
        read_supplier_document(tmp_path / "photo.jpg", FakeClient([]))
    assert (raised.value.attempts, raised.value.response) == (0, None)                 # refused before any request


def test_an_unknown_prompt_version_is_refused_before_any_request(madeup_out):
    client = FakeClient([])
    with pytest.raises(ValueError, match="v9"):
        read_supplier_document(madeup_out / "MU01_conf.pdf", client, prompt_version="v9")
    assert client.requests == []


def test_a_file_that_is_neither_a_pdf_nor_a_png_is_refused(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8\xff")
    with pytest.raises(ReaderError, match="photo.jpg"):
        read_supplier_document(tmp_path / "photo.jpg", FakeClient([]))


def test_a_cart_screenshot(madeup_out):
    client = FakeClient([answer_from_spec("CART02", "cart")])
    doc, _ = read_supplier_document(madeup_out / "CART02.png", client, kind="cart")
    assert (doc.kind, doc.source_file, doc.pages, doc.reader) == ("cart", "CART02.png", 1, "llm:fake:v1")
    assert [(l.item_code.value, l.quantity.value, l.unit.value, l.unit_price.text, l.unit_price.value) for l in doc.lines] == [
        ("KM-2001", "5", "pc", "€ 12,50", "12.50"), ("KM-2002", "3", "pc", "€ 41,00", "41.00")]
    assert (doc.capture_date.value, doc.document_date) == ("2026-11-04", DocValue()) and "cart" in client.requests[0].user_text
    header = {name: (getattr(doc, name).source.line, getattr(doc, name).source.text) for name in ("supplier", "capture_date", "po_reference", "currency")}
    assert header == {"supplier": (1, "S22"), "capture_date": (3, "Datum: 04-11-2026"), "po_reference": (4, "Uw referentie: 250202"),
                      "currency": (9, "Prijzen in EUR, exclusief btw.")}                 # the lines of the screenshot, from the top
    assert [{getattr(line, f).source.line for f in FIELDS if getattr(line, f).source} for line in doc.lines] == [{6}, {7}]
    assert not any(value.uncertain for value in values(doc))


# --- document_from_spec, the perfect reading ---

def parse_source(text: str) -> tuple[int, int]:
    """(page, line) of a source of references.csv: "<file>, page p, line l"."""
    page, line = re.fullmatch(r".+, page (\d+), line (\d+)", text).groups()
    return int(page), int(line)


def test_document_from_spec_cites_the_rows_the_references_name():
    """All 20 confirmations, the scans included: the lines' values stand on the rows references.csv names, the PO reference
    on the row of the version item and the Incoterm on the row of the required information."""
    with open(REPO_ROOT / "aid" / "madeup" / "references.csv", encoding="utf-8", newline="") as f:
        references = list(csv.DictReader(f))
    confirmations = [case_id for case_id, spec in SPECS.items() if spec.confirmation]
    assert len(confirmations) == 20
    for case_id in confirmations:
        doc, rows = document_from_spec(case_id), [r for r in references if r["case_id"] == case_id]
        named = {parse_source(r["source"]) for r in rows if r["line_id"] and r["source"] != "po"}
        cited = {(v.source.page, v.source.line) for line in doc.lines for v in (getattr(line, f) for f in FIELDS) if v.source}
        assert cited == named, case_id
        version = next(r for r in rows if r["item_type"] == "version")
        assert (doc.po_reference.source.page, doc.po_reference.source.line) == parse_source(version["source"]), case_id
        required = next(r for r in rows if r["item_type"] == "required information")
        if required["source"] != "po":
            assert (doc.incoterm.source.page, doc.incoterm.source.line) == parse_source(required["source"]), case_id
        else:
            assert doc.incoterm == DocValue(), case_id


def test_document_from_spec_quotes_the_text_lines_of_the_pdf(madeup_out):
    """Each source is the line extract_lines gives at its page and line (where the PDF has a text layer), and each value's
    text stands in its source."""
    for case_id in (case_id for case_id, spec in SPECS.items() if spec.confirmation):
        doc = document_from_spec(case_id)
        lines = {(line.page, line.line): line.text for line in extract_lines(madeup_out / f"{case_id}_conf.pdf")}
        assert (doc.kind, doc.source_file, doc.reader) == ("confirmation", f"{case_id}_conf.pdf", "spec")
        for value in values(doc):
            if value.source is not None:
                assert value.text in value.source.text and not value.uncertain, (case_id, value)
                assert not lines or lines[value.source.page, value.source.line] == value.source.text, (case_id, value)


def test_document_from_spec_of_the_cart():
    """CART02 with kind "cart": the screenshot's values, each citing its line of the screenshot (cart_rows), counted from the top."""
    doc = document_from_spec("CART02", kind="cart")
    first = doc.lines[0]
    assert (doc.kind, doc.source_file, [line.line_id for line in doc.lines]) == ("cart", "CART02.png", ["1", "2"])
    assert (first.unit_price.source.page, first.unit_price.source.line, doc.lines[1].item_code.source.line) == (1, 6, 7)
    assert first.unit_price.source.text == "KM-2001 Hydraulische slang 10 mm x 2 m 5 st € 12,50 1 € 62,50"
    assert (first.unit_price.text, first.unit_price.value, first.unit.text, first.unit.value) == ("€ 12,50", "12.50", "st", "pc")
    assert (doc.capture_date.text, doc.capture_date.value, doc.capture_date.source.line) == ("04-11-2026", "2026-11-04", 3)
    assert (doc.po_reference.value, doc.po_reference.source.line, doc.supplier.source.line, doc.currency.source.line) == ("250202", 4, 1, 9)
    assert first.delivery_date == DocValue() and doc.document_date == DocValue()
    assert all(value.text in value.source.text and not value.uncertain for value in values(doc) if value.source)


# --- the --record command ---

def test_the_record_command_records_each_answer_once(tmp_path, monkeypatch, capsys):
    """--record reads every confirmation and cart screenshot of the set through RecordedClient(folder, live=AnthropicClient(...)),
    here with a stand-in for the model, so that a second run replays what the first one recorded."""
    folder = tmp_path / "set"
    folder.mkdir()
    for name in ("MU01_conf.pdf", "CART02.png"):
        shutil.copy(SET / name, folder / name)
    answers, made, calls = {True: answer_from_spec("MU01"), False: answer_from_spec("CART02", "cart")}, [], []

    class StandIn:
        name = "stand-in"
        def __init__(self, model, effort): made.append((model, effort))
        def complete(self, req):
            calls.append(req)
            return LlmResponse(text=answers[req.pdf is not None], model="stand-in", input_tokens=7, output_tokens=3, seconds=0.5)
    monkeypatch.setattr("aid.readers.supplier_llm.AnthropicClient", StandIn)
    argv = ["--record", str(tmp_path / "recorded"), "--set", str(folder), "--model", "model-x"]
    assert main(argv) == 0 and main(argv) == 0
    assert made == [("model-x", "medium")] * 2 and len(calls) == 2 and len(list((tmp_path / "recorded").glob("*.json"))) == 2
    printed = capsys.readouterr().out
    assert "MU01_conf.pdf: 3 lines" in printed and "CART02.png: 2 lines" in printed


# --- the recorded answers ---

def test_recorded_set_reads_all():
    """The recorded answers for the 21 documents of the --record command (the 20 confirmations and CART02.png) replay
    through the reader's whole path: the request and its digest, the recorded file, the parsing, the normalisation and the
    source check. Field accuracy is counted against the specs' values by score_reading."""
    documents = documents_to_record(SET)
    assert [path.name for path, kind in documents if kind == "cart"] == ["CART02.png"] and len(documents) == 21
    client, correct, total = RecordedClient(RECORDED), 0, 0
    for path, kind in documents:
        doc, response = read_supplier_document(path, client, kind=kind)
        assert (doc.kind, doc.source_file, doc.reader) == (kind, path.name, "llm:recorded:v1") and response.attempts <= 2
        if response.model == "synthetic":                       # a perfect reading: the specs mark nothing uncertain
            assert not any(value.uncertain for value in values(doc)), path.name
        expected = expected_fields(SPEC_DIR / f"{path.stem.removesuffix('_conf')}.json")
        score = score_reading(doc, expected, extract_lines(path) if path.suffix == ".pdf" else [])
        correct, total = correct + score.fields_correct, total + score.fields_total
    assert correct / total >= 0.9


def test_the_record_command_prints_the_usage_of_a_failed_read(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "set"
    folder.mkdir()
    shutil.copy(SET / "CART02.png", folder / "CART02.png")

    class StandIn:
        name = "stand-in"
        def __init__(self, model, effort): pass
        def complete(self, req): return LlmResponse(text="not json", model="stand-in", input_tokens=7, output_tokens=3, seconds=0.5, request_id="req_9")
    monkeypatch.setattr("aid.readers.supplier_llm.AnthropicClient", StandIn)
    assert main(["--record", str(tmp_path / "recorded"), "--set", str(folder), "--model", "model-x"]) == 1
    printed = capsys.readouterr().out
    assert "CART02.png: not read after 2 attempt(s)" in printed and "model stand-in, 14 + 6 tokens, 1.0 s, request req_9" in printed


def test_the_recorder_names_no_model(capsys):
    """A model identifier is kept in the settings default and the bake-off only: the module docstring and the --model help show a
    placeholder."""
    import aid.readers.supplier_llm as module
    with pytest.raises(SystemExit):
        main(["--help"])
    help_text = capsys.readouterr().out
    assert "<model id>" in help_text and "<model id>" in module.__doc__
    assert not re.search(r"claude-[a-z]", help_text + module.__doc__)
