"""Fakes shared by the tests of the checking aid: small made-up documents, a decider that gives one answer, and the write
entries and case results that the digest and the review page show.

The documents are the fakes of aid/tests/test_versioning.py, with a description on a line and a PO of its own added, so that
the tests of every step build their documents the same way. A line's description and delivery date are given by name, so that
no value can go to the wrong field. No fake calls a model.
"""
from collections.abc import Sequence
from pathlib import Path

from aid.decider import Decision, DecisionAnswer
from aid.execution.ops import OpKind, WriteOp
from aid.execution.writer import WriteLogEntry
from aid.pipeline import CaseResult, Status, _digest_line   # _digest_line: the line of a case as the pipeline writes it
from aid.po_store import PoVersion
from aid.schema import DocLine, DocValue, Document

PO_NUMBER = "250101"
ENTRY_TIME = "2026-11-18T09:00:00"               # the time of every entry that entry() makes


def _value(text: str | None) -> DocValue:
    return DocValue(value=text, text=text) if text else DocValue()


def _numbered(lines: list[DocLine]) -> list[DocLine]:
    return [line.model_copy(update={"line_id": str(n)}) for n, line in enumerate(lines, 1)]


def line(code: str, qty: str, price: str | None = None, *, desc: str | None = None, date: str | None = None, unit: str | None = None,
         basis: str | None = None, discount: str | None = None, net_price: str | None = None) -> DocLine:
    """A document line with this item code, quantity, unit price, description, delivery date, unit, price basis, discount
    (in percent) and net price, each in normal form; "" or None leaves the value out. The document it goes into numbers it."""
    return DocLine(line_id="", item_code=_value(code), description=_value(desc), quantity=_value(qty), unit=_value(unit),
                   unit_price=_value(price), price_basis=_value(basis), discount_pct=_value(discount), net_price=_value(net_price),
                   delivery_date=_value(date))


def po(lines: list[DocLine], *, currency: str | None = None, incoterm: str | None = None) -> Document:
    """A made-up PO with these lines, numbered 1, 2, 3, as its PDF gives it, in this currency and with this Incoterm, if any."""
    return Document(kind="po", po_reference=_value(PO_NUMBER), currency=_value(currency), incoterm=_value(incoterm), lines=_numbered(lines))


def confirmation(lines: list[DocLine], *, currency: str | None = None, incoterm: str | None = None) -> Document:
    """A supplier's confirmation of these lines, numbered 1, 2, 3, that quotes no version and has no date, in this currency
    and with this Incoterm, if any."""
    return Document(kind="confirmation", currency=_value(currency), incoterm=_value(incoterm), lines=_numbered(lines))


def po_version(n: int, lines: list[DocLine], date: str | None = None) -> PoVersion:
    """Version n of a made-up PO with these lines, dated `date`, as the store gives it (no PDF behind it)."""
    document = Document(kind="po", po_reference=_value(PO_NUMBER), version_hint=_value(str(n)), lines=_numbered(lines))
    return PoVersion(po_number=PO_NUMBER, version=n, version_date=date, pdf_path=Path(f"v{n}.pdf"), document=document, supplier=None,
                     total=None, above_authorization=None)


class FakeDecider:
    """Gives the same answer to every decision and keeps the decisions it was asked."""
    name = "fake"

    def __init__(self, choice: str | None, confidence: float):
        self.choice, self.confidence, self.decisions = choice, confidence, []

    def decide(self, d: Decision) -> DecisionAnswer:
        self.decisions.append(d)
        return DecisionAnswer(choice=self.choice, confidence=self.confidence, rationale="the fake's rationale", source=self.name)


def entry(kind: OpKind, old: str | None, new: str | None, entry_id: str) -> WriteLogEntry:
    """A write made (status "done") by rule R04 at ENTRY_TIME: an operation of this kind on line 1 of the made-up PO, or on the
    article KM-1001 for an article_price, from the old value, which is also the entry's previous value, to the new one. Its
    case_id is empty until case_result puts the entry in a case."""
    where = {"article_code": "KM-1001"} if kind == "article_price" else {"line_id": "1"}
    op = WriteOp(kind=kind, po_number=PO_NUMBER, version=1, old_value=old, new_value=new, **where)
    return WriteLogEntry(entry_id=entry_id, case_id="", op=op, status="done", previous=old, new=new, rule_id="R04", evidence={},
                         timestamp=ENTRY_TIME)


def case_result(case_id: str, status: Status, writes: Sequence[WriteLogEntry] = (), *, supplier: str | None = None,
                language: str = "nl") -> CaseResult:
    """The result of the case with this id and status, holding these write entries, each given the case's id, and the digest line
    that the pipeline writes for them in the language, naming the supplier when one is given. The PO number is the part of the
    id before the first underscore, and none for "unknown". The result has no version, no findings, no actions, no messages and
    no review items: a test adds what it needs with model_copy."""
    po_number = case_id.partition("_")[0]
    entries = [write.model_copy(update={"case_id": case_id}) for write in writes]
    line = _digest_line(None if po_number == "unknown" else po_number, supplier, status, entries, language, "confirmation")
    return CaseResult(case_id=case_id, status=status, version=None, findings=None, actions=[], write_entries=entries, messages=[],
                      review_items=[], digest_line=line, supplier=supplier)
