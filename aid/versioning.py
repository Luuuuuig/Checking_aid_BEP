"""Version selection: which stored version of its PO a supplier's document answers.

The rules follow the order of evidence of the scoring manual (section 2) and are tried in this order:

1. Reference. The document quotes a version that is stored, or its date is the date of exactly one version: that
   version. A quoted version that is not stored selects nothing ("quotes version n, which is not stored"): the store has
   not been given that version yet, and the buyer should see so rather than have the aid choose another one. A PO with a
   single stored version, which the document does not contradict by quoting another, gives that version ("single version").
2. Correspondence. For each version, the number of document lines whose item code and quantity are those of one of its
   lines (the quantity as a number, so 10 and 10.00 are one quantity). When one version alone has the highest number, and
   that number is at least 1, it is the version.
3. Model. Otherwise the decider chooses among the versions, from the date and lines of the document and the number,
   date and lines of each version, a line giving its item code, quantity, unit price and delivery date (a revision that
   moved only delivery dates differs there). Its choice counts when its confidence is at least MIN_CONFIDENCE.
4. Otherwise no version is selected ("cannot select"), which is a valid answer: the case goes to review.

Every rule but the third is deterministic. A PO with no stored version gives no version.
"""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from aid.decider import MIN_CONFIDENCE, Decider, Decision
from aid.po_store import PoVersion
from aid.schema import DocLine, Document


class VersionChoice(BaseModel):
    version: int | None
    method: Literal["reference", "correspondence", "model", "none"]
    reason: str
    confidence: float


def select_version(doc: Document, versions: list[PoVersion], decider: Decider) -> VersionChoice:
    """The version of the PO that `doc` answers, among the stored `versions`, by the rules of the module docstring."""
    if not versions:
        return _no_version("no stored version")
    hint = doc.version_hint.value
    if hint is not None:
        number = int(hint) if hint.strip().isdecimal() else None
        quoted = next((v for v in versions if v.version == number), None)
        if quoted is None:
            return _no_version(f"quotes version {hint.strip()}, which is not stored")
        return _chosen(quoted, "reference", f"quotes version {quoted.version}")
    date = doc.document_date.value
    dated = [v for v in versions if date is not None and v.version_date == date]
    if len(dated) == 1:
        return _chosen(dated[0], "reference", f"dated {date}, the date of version {dated[0].version}")
    if len(versions) == 1:
        return _chosen(versions[0], "reference", "single version")
    counts = {v.version: _corresponding(doc, v) for v in versions}
    best = max(counts.values())
    leaders = [v for v in versions if counts[v.version] == best]
    if best >= 1 and len(leaders) == 1:
        tally = ", ".join(f"version {number}: {count}" for number, count in counts.items())
        return _chosen(leaders[0], "correspondence", f"lines with the item code and quantity of a PO line: {tally}")
    options = [str(v.version) for v in versions]
    answer = decider.decide(Decision(kind="version", options=options, context=_context(doc, versions)))
    if answer.choice in options and answer.confidence >= MIN_CONFIDENCE:
        return VersionChoice(version=int(answer.choice), method="model", reason=answer.rationale, confidence=answer.confidence)
    return _no_version("cannot select")


def _chosen(version: PoVersion, method: str, reason: str) -> VersionChoice:
    return VersionChoice(version=version.version, method=method, reason=reason, confidence=1.0)


def _no_version(reason: str) -> VersionChoice:
    return VersionChoice(version=None, method="none", reason=reason, confidence=0.0)


def _key(line: DocLine) -> tuple[str, Decimal] | None:
    """A line's item code and quantity, or None when it lacks one of them."""
    code, quantity = line.item_code.value, line.quantity.value
    return None if code is None or quantity is None else (code, Decimal(quantity))


def _corresponding(doc: Document, version: PoVersion) -> int:
    """The number of lines of `doc` whose item code and quantity are those of a line of the version."""
    po_keys = {_key(line) for line in version.document.lines} - {None}
    return sum(1 for line in doc.lines if _key(line) in po_keys)


def _summary(lines: list[DocLine]) -> list[dict]:
    return [{"line_id": line.line_id, "item_code": line.item_code.value, "quantity": line.quantity.value,
             "unit_price": line.unit_price.value, "delivery_date": line.delivery_date.value} for line in lines]


def _context(doc: Document, versions: list[PoVersion]) -> dict:
    """What the decider sees: the date and lines of the document, and the number, date and lines of each version."""
    return {"document": {"date": doc.document_date.value, "lines": _summary(doc.lines)},
            "versions": [{"version": v.version, "date": v.version_date, "lines": _summary(v.document.lines)} for v in versions]}
