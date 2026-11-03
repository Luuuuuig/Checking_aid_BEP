"""Tests of version selection (aid/versioning.py): the rules on the made-up MU03 and on small made-up POs, and the decider's
part with a fake decider and with the LLM decider over a fake client. No test calls a model."""
import json
from pathlib import Path

from aid.decider import MIN_CONFIDENCE, Decision, DecisionAnswer, LlmDecider, NoDecider
from aid.po_store import PoStore, PoVersion
from aid.readers.llm_client import LlmRequest, LlmResponse
from aid.schema import DocLine, DocValue, Document
from aid.tests.helpers import document_from_spec
from aid.versioning import select_version


# --- fakes: small made-up documents, and a decider that gives one answer ---

def _value(text: str | None) -> DocValue:
    return DocValue(value=text, text=text) if text else DocValue()


def _numbered(lines: list[DocLine]) -> list[DocLine]:
    return [line.model_copy(update={"line_id": str(n)}) for n, line in enumerate(lines, 1)]


def line(code: str, qty: str, price: str | None = None, date: str | None = None) -> DocLine:
    """A document line with this item code, quantity, unit price and delivery date. The document it goes into numbers it."""
    return DocLine(line_id="", item_code=_value(code), quantity=_value(qty), unit_price=_value(price), delivery_date=_value(date))


def confirmation(lines: list[DocLine]) -> Document:
    """A supplier's confirmation of these lines, numbered 1, 2, 3, that quotes no version and has no date."""
    return Document(kind="confirmation", lines=_numbered(lines))


def po_version(n: int, lines: list[DocLine], date: str | None = None) -> PoVersion:
    """Version n of a made-up PO with these lines, dated `date`, as the store gives it (no PDF behind it)."""
    po = Document(kind="po", po_reference=_value("250101"), version_hint=_value(str(n)), lines=_numbered(lines))
    return PoVersion(po_number="250101", version=n, version_date=date, pdf_path=Path(f"v{n}.pdf"), document=po, supplier=None, total=None,
                     above_authorization=None)


class FakeClient:
    """A model that gives one answer, and keeps the request."""
    name = "fake-client"

    def __init__(self, text: str):
        self.text, self.requests = text, []

    def complete(self, req: LlmRequest) -> LlmResponse:
        self.requests.append(req)
        return LlmResponse(text=self.text, model="fake-model", input_tokens=1, output_tokens=1, seconds=0.0)


class FakeDecider:
    """Gives the same answer to every decision and keeps the decisions it was asked."""
    name = "fake"

    def __init__(self, choice: str | None, confidence: float):
        self.choice, self.confidence, self.decisions = choice, confidence, []

    def decide(self, d: Decision) -> DecisionAnswer:
        self.decisions.append(d)
        return DecisionAnswer(choice=self.choice, confidence=self.confidence, rationale="the fake's rationale", source=self.name)


# --- the brief's tests ---

def test_reference_wins(madeup_out, tmp_path):
    s = PoStore(tmp_path); [s.add(p) for p in sorted(madeup_out.glob("MU03_po_v*.pdf"))]
    doc = document_from_spec("MU03")                                   # quotes version 1
    c = select_version(doc, s.versions("250103"), NoDecider()); assert (c.version, c.method) == (1, "reference")

def test_correspondence(madeup_out, tmp_path):
    s = PoStore(tmp_path); [s.add(p) for p in sorted(madeup_out.glob("MU03_po_v*.pdf"))]
    doc = document_from_spec("MU03"); doc.version_hint = DocValue()
    c = select_version(doc, s.versions("250103"), NoDecider()); assert (c.version, c.method) == (1, "correspondence")

def test_model_decides_only_with_confidence():
    vs = [po_version(1, [line("KM-1", "5")]), po_version(2, [line("KM-1", "5")])]
    doc = confirmation([line("KM-1", "5")])
    assert select_version(doc, vs, NoDecider()).method == "none"
    assert select_version(doc, vs, FakeDecider("2", 0.9)).version == 2
    assert select_version(doc, vs, FakeDecider("2", 0.5)).method == "none"


# The tests below cover what the rulings of 29 October add to the brief's tests above.

TWO = [po_version(1, [line("KM-1", "5")], date="2026-10-12"), po_version(2, [line("KM-1", "6")], date="2026-10-26")]   # differ in the quantity


def quoting(version: str, lines: list[DocLine]) -> Document:
    doc = confirmation(lines)
    doc.version_hint = _value(version)
    return doc


# --- (1) the reference: a quoted version or the date of a version ---

def test_a_quoted_version_wins_over_the_lines():
    c = select_version(quoting("2", [line("KM-1", "5")]), TWO, NoDecider())       # the lines answer version 1
    assert (c.version, c.method, c.confidence) == (2, "reference", 1.0) and "version 2" in c.reason


def test_a_quoted_version_that_is_not_stored_is_not_guessed():
    decider = FakeDecider("1", 0.99)
    c = select_version(quoting("3", [line("KM-1", "5")]), TWO, decider)
    assert (c.version, c.method, c.reason, c.confidence) == (None, "none", "quotes version 3, which is not stored", 0.0)
    single = select_version(quoting("2", [line("KM-1", "5")]), TWO[:1], decider)   # one stored version, but the document quotes another
    assert (single.version, single.method, single.reason) == (None, "none", "quotes version 2, which is not stored")
    named = select_version(quoting("v2", [line("KM-1", "5")]), TWO, decider)        # a quote that is no version number
    assert (named.version, named.method, named.reason) == (None, "none", "quotes version v2, which is not stored")
    assert decider.decisions == []


def test_the_date_of_exactly_one_version_is_a_reference():
    doc = confirmation([line("KM-1", "5")])                                      # the lines answer version 1
    doc.document_date = _value("2026-10-26")
    c = select_version(doc, TWO, NoDecider())
    assert (c.version, c.method, c.confidence) == (2, "reference", 1.0) and "2026-10-26" in c.reason
    doc.document_date = _value("2026-10-14")                                     # the date of neither version: the lines decide
    assert select_version(doc, TWO, NoDecider()).method == "correspondence"
    same_day = [v.model_copy(update={"version_date": "2026-10-26"}) for v in TWO]
    doc.document_date = _value("2026-10-26")                                     # the date of both versions settles nothing
    c = select_version(doc, same_day, NoDecider())
    assert (c.version, c.method) == (1, "correspondence")


def test_a_single_version_needs_no_reference():
    c = select_version(confirmation([line("KM-9", "1")]), TWO[:1], FakeDecider("1", 0.99))    # its lines answer nothing
    assert (c.version, c.method, c.reason, c.confidence) == (1, "reference", "single version", 1.0)
    assert select_version(quoting("1", [line("KM-9", "1")]), TWO[:1], NoDecider()).method == "reference"


def test_no_stored_version_is_no_choice():
    decider = FakeDecider("1", 0.99)
    c = select_version(confirmation([line("KM-1", "5")]), [], decider)
    assert (c.version, c.method, c.confidence) == (None, "none", 0.0) and decider.decisions == []


# --- (2) the correspondence of the lines ---

def test_correspondence_counts_the_lines_with_the_item_code_and_quantity_of_a_po_line():
    vs = [po_version(1, [line("KM-1", "4"), line("KM-2", "5")]), po_version(2, [line("KM-1", "6"), line("KM-3", "5")])]
    c = select_version(confirmation([line("KM-1", "4.00"), line("KM-2", "5")]), vs, NoDecider())    # 4.00 is the quantity 4
    assert (c.version, c.method, c.confidence) == (1, "correspondence", 1.0) and "version 1: 2" in c.reason and "version 2: 0" in c.reason
    unmatched = confirmation([line("KM-2", "9"), line("KM-9", "4")])                  # a code alone or a quantity alone is no match
    assert select_version(unmatched, vs, NoDecider()).method == "none"


# --- (3) the decider, and (4) no choice ---

def moved_delivery() -> tuple[list[PoVersion], Document]:
    """Two versions that differ only in the delivery date, so that their lines tie, and a confirmation of version 2's date,
    itself dated on neither version's day."""
    versions = [po_version(1, [line("KM-1", "5", "12.50", date="2026-11-16")], date="2026-10-12"),
                po_version(2, [line("KM-1", "5", "12.50", date="2026-11-23")], date="2026-10-26")]
    doc = confirmation([line("KM-1", "5", "12.50", date="2026-11-23")])
    doc.document_date = _value("2026-10-28")
    return versions, doc


def test_the_decider_sees_the_dates_and_the_line_summaries():
    versions, doc = moved_delivery()
    decider = FakeDecider("2", 0.9)
    c = select_version(doc, versions, decider)
    assert (c.version, c.method, c.reason, c.confidence) == (2, "model", "the fake's rationale", 0.9)
    [decision] = decider.decisions
    assert (decision.kind, decision.options) == ("version", ["1", "2"])
    summary = {"line_id": "1", "item_code": "KM-1", "quantity": "5", "unit_price": "12.50"}
    assert decision.context == {
        "document": {"date": "2026-10-28", "lines": [summary | {"delivery_date": "2026-11-23"}]},
        "versions": [{"version": 1, "date": "2026-10-12", "lines": [summary | {"delivery_date": "2026-11-16"}]},
                     {"version": 2, "date": "2026-10-26", "lines": [summary | {"delivery_date": "2026-11-23"}]}]}


def test_the_llm_decider_reaches_the_model_branch():
    versions, doc = moved_delivery()
    client = FakeClient(json.dumps({"choice": "2", "confidence": 0.9, "rationale": 'The confirmation delivers on "2026-11-23", as version 2.'}))
    c = select_version(doc, versions, LlmDecider(client))
    assert (c.version, c.method, c.reason, c.confidence) == (2, "model", 'The confirmation delivers on "2026-11-23", as version 2.', 0.9)
    [request] = client.requests
    assert request.schema["properties"]["choice"]["enum"] == ["1", "2", "none"] and '"delivery_date": "2026-11-16"' in request.user_text


def test_the_model_needs_one_of_the_options_and_enough_confidence():
    vs = [po_version(1, [line("KM-1", "5")]), po_version(2, [line("KM-1", "5")])]
    doc = confirmation([line("KM-1", "5")])
    assert select_version(doc, vs, FakeDecider("2", MIN_CONFIDENCE)).method == "model"         # the threshold itself is enough
    assert select_version(doc, vs, FakeDecider("3", 0.99)).method == "none"                    # not one of the options
    c = select_version(doc, vs, FakeDecider(None, 0.99))                                        # the decider chose none
    assert (c.version, c.method, c.reason, c.confidence) == (None, "none", "cannot select", 0.0)
