"""Comparison: the values of a supplier's document against the PO version it answers, by the rules of the scoring manual.

compare takes the line pairs of match_lines (aid/matching.py) and gives the findings of a case: a Flag for each difference the
documents show, a Match for each value they agree on, and an Uncompared item with its reason for each value that cannot be
compared. The rules are those of the scoring manual (docs/process/Scoring_Manual_Draft_2026-10-21.md, sections 2 and 3), by which
aid/madeup/references.csv labels the made-up set. No model is asked anything, so the same documents always give the same
findings, and a value is a Match only when both documents state it and the rules compared it.

What is compared. On a confirmation: the price, quantity, unit and delivery date of each pair, the presence of each line, and the
required information. On a cart: the price, quantity and unit of each pair and the presence of each line, no dates and no
required information.

Line presence. A PO line that no document line answers (unmatched_po) is a Flag "missing line", on a cart "not in cart", with the
PO line's item code (its description when it has no code) as po_value. A document line that answers no PO line (extra_doc) is a
Flag "extra line", with the document line's code (or description) as doc_value. A pair is a Match whose value is its kind:
matched, split or merged.

Pairs whose values are not compared. An uncertain pair (match.uncertain) gives an Uncompared "match uncertain" for each field
above, its line presence included; a PO line that is a candidate of several document lines is in the uncertain pair of each,
and each gives its own items. A merged pair gives an Uncompared "merged line" for each field above, because a value printed
once for several PO lines cannot be divided among them; its line presence is a Match. A match that puts a PO line in two pairs,
or in a pair and an uncertain pair, is not one that match_lines makes: compare raises ValueError naming the line, because
comparing that line twice is a bug, not a finding.

The fields of a matched pair, each compared on its own, so that a flagged field leaves the others compared. Each rule takes the
first of its steps that applies, and its first step checks the values it reads: one marked uncertain, on either side, makes the
field Uncompared "unreadable or unverified source". The price reads the unit price, net price, discount, price basis and unit of
both lines and the currency of both documents; the quantity reads the quantities and units; the unit the units; the delivery
date the delivery dates.
- Price. The PO line states no price (no unit price and no net price): "no PO price". The document line states none: "total only"
  when it prints an amount of money that is not its quantity or discount, in the text of one of its values or on the text line
  one of them stands on (a line total; how a price given only as a total is compared is still to be fixed in the manual), else
  a Flag "missing on the document". Both documents state a currency and they differ: "different currency". The PO states
  a currency and the document none: "currency missing", because a currency the document does not state cannot be shown to be
  the PO's. The units
  differ (see Unit): "unknown unit conversion". One line states a price basis and the other none: "unknown price basis". Else
  the document line's net price, converted to the PO line's basis, and the PO line's own net price are compared at the
  precision of the price the PO line prints, at least the cent: both are rounded half up to that many decimals, so that 0.0425
  a piece against 0.04 is a Flag "different" and 12.35 against 12.345 is a Match. This is the researcher's rule for the
  manual's item on decimal rounding (section 3). When neither line states a basis, both net prices are compared as they stand.
  The values of a price flag are these two rounded net prices, each with the text and source of the price printed on its line,
  so that one reads against the other.
- Quantity. The PO line states none: "no PO quantity". The document line states none: a Flag "missing on the document". The
  units differ: "unknown unit conversion". Else the quantities as numbers (5 and 5.00 are one quantity): a Match or a Flag
  "different".
- Unit. The PO line states none: "no PO unit". The document line states none: a Flag "missing on the document"; another unit: a
  Flag "different"; else a Match. The schema has no place for a unit conversion that a document states, so none is made: the
  price and the quantity are compared only in the same unit, and two lines that both state no unit count as in the same unit.
- Delivery date. The PO line states none: "no PO date". The document line states none: a Flag "missing on the document". Else
  date_matches decides: a Match, or a Flag "later" when the document's date begins after the PO's, else "earlier".

A split pair (several document lines answering one PO line) is compared as a matched pair, but its quantity is the sum of the
quantities of its document lines (one that states no quantity is a Flag "missing on the document" for that line), and its price,
unit and delivery date are a Match only when every document line agrees with the PO line, else each document line that does not
gives its own Flag or Uncompared (doc_line_ids that line).

Required information, on a confirmation only: the Incoterm. When the PO states none, and none marked uncertain either, nothing is
required and nothing is compared. A value marked uncertain on either side: "unreadable or unverified source". None on the
confirmation: a Flag "missing on the document". Else the two texts, without case and extra spaces: a Match, or a Flag "different".

A Match's value is the value in the PO's normal form: the PO line's net price per its own basis at the precision of its printed
price (at least two decimals), the quantity, the unit, the delivery date, the Incoterm, or the kind of a pair. The items come in
this order, and the flags are numbered F1, F2, ... in it: the missing lines in PO order and the extra lines in document order,
then each pair, uncertain pairs included, in the order of its first PO line with its price, quantity, unit, delivery date and
line presence, then the required information. Findings.version is the version the PO prints, if any. The values are read in
the normal forms of aid/schema.py, which the readers give: a number or a delivery date in another form raises, because it is a
reader's error.
"""
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel

from aid.matching import MatchResult, Pair
from aid.schema import DocLine, DocValue, Document, normalize_number

Field = Literal["price", "quantity", "unit", "delivery_date", "line_presence", "required_info"]
Detail = Literal["different", "missing on the document", "missing line", "extra line", "not in cart", "later", "earlier"]
Reason = Literal["different currency", "currency missing", "unknown price basis", "unknown unit conversion", "total only", "match uncertain",
                 "unreadable or unverified source", "no PO date", "merged line", "no PO price", "no PO quantity", "no PO unit"]

_PAIR_FIELDS: dict[str, tuple[Field, ...]] = {"confirmation": ("price", "quantity", "unit", "delivery_date"),
                                               "cart": ("price", "quantity", "unit")}
_AMOUNT = re.compile(r"(?<![\d.,])\d+(?:[.,']\d{3})*[.,]\d{2}(?![\d.,])")     # an amount of money, with two decimals: "192,00", "1.234,50"
_DAY = r"\d{4}-\d{2}-\d{2}"
_DELIVERY = re.compile(rf"(?P<year>\d{{4}})-W(?P<week>\d{{2}})|(?P<first>{_DAY})(?:/(?P<last>{_DAY}))?")    # a week, a day or a range


class Flag(BaseModel):
    flag_id: str                                 # "F1", "F2", ... in the order of the module docstring
    field: Field
    po_line_ids: list[str]
    doc_line_ids: list[str]
    po_value: DocValue | None                    # None when the PO has no such line (an extra line)
    doc_value: DocValue | None                   # None when the document has no such line or states no such value
    detail: Detail


class Match(BaseModel):
    field: Field
    po_line_ids: list[str]
    doc_line_ids: list[str]
    value: str                                   # in the PO's normal form (see the module docstring)


class Uncompared(BaseModel):
    field: Field
    po_line_ids: list[str]
    doc_line_ids: list[str]
    reason: Reason


class Findings(BaseModel):
    version: int | None                          # the version the PO prints; the pipeline may set the version it selected
    flags: list[Flag]
    matches: list[Match]
    uncompared: list[Uncompared]


def compare(match: MatchResult, doc: Document, po: Document, kind: Literal["confirmation", "cart"]) -> Findings:
    """The findings of `doc` against `po`, whose lines `match` pairs, by the rules of the module docstring. Raises ValueError
    for a match that puts a PO line in two pairs, or in a pair and an uncertain pair."""
    _check_pairs(match)
    comparison = _Comparison(doc, po, kind)
    comparison.line_presence(match)
    comparison.pairs(match)
    if kind == "confirmation":
        comparison.required_info()
    return comparison.findings


def net_unit_price(line: DocLine, basis: str | None = None, places: int = 2) -> Decimal | None:
    """The line's net price per `basis` units, a price basis in normal form ("1", "100"; by default the line's own basis): its
    net price if it states one, else its unit price less its discount (discount_pct percent, without its sign), converted from
    the line's price basis to `basis` and rounded half up to `places` decimals, the cent by default. None when the line states
    neither a unit price nor a net price, or no price basis. The quantity plays no part."""
    net, own = _net(line), line.price_basis.value
    if net is None or own is None:
        return None
    return _rounded(net * Decimal(basis or own) / Decimal(own), places)


def date_matches(po_value: str, doc_value: str) -> bool:
    """Whether the document's delivery date is the PO's: the days the PO's value covers lie within those the document's value
    covers. A day covers itself, a week its Monday to Friday (ISO week) and a range its days from the first to the last, so a
    PO day inside the document's week or range counts as the same. Raises ValueError for a value in none of these forms."""
    po_first, po_last = _span(po_value)
    doc_first, doc_last = _span(doc_value)
    return doc_first <= po_first and po_last <= doc_last


def date_span(value: str) -> tuple[date, date]:
    """The first and the last day a delivery date in normal form covers: a day itself, Monday to Friday of an ISO week, the days
    of a range. Raises ValueError for any other text, a day or a week that does not exist, and a range that ends before it
    begins."""
    return _span(value)


# --- one run of the rules ---

@dataclass(frozen=True)
class _Same:
    value: str


@dataclass(frozen=True)
class _Differs:
    detail: Detail
    po_value: DocValue | None
    doc_value: DocValue | None


@dataclass(frozen=True)
class _Cannot:
    reason: Reason


_Outcome = _Same | _Differs | _Cannot


class _Comparison:
    """One run of the rules on a document and its PO, which collects the findings in the order of the module docstring."""

    def __init__(self, doc: Document, po: Document, kind: str):
        self.doc, self.po, self.kind, self.fields = doc, po, kind, _PAIR_FIELDS[kind]
        self.doc_lines = {line.line_id: line for line in doc.lines}
        self.po_lines = {line.line_id: line for line in po.lines}
        self.findings = Findings(version=_printed_version(po), flags=[], matches=[], uncompared=[])

    def add(self, field: Field, po_ids: list[str], doc_ids: list[str], outcome: _Outcome) -> None:
        """Record an outcome as a Match, a Flag numbered after the flags before it, or an Uncompared item."""
        ids = {"field": field, "po_line_ids": list(po_ids), "doc_line_ids": list(doc_ids)}
        if isinstance(outcome, _Same):
            self.findings.matches.append(Match(value=outcome.value, **ids))
        elif isinstance(outcome, _Differs):
            self.findings.flags.append(Flag(flag_id=f"F{len(self.findings.flags) + 1}", po_value=outcome.po_value,
                                            doc_value=outcome.doc_value, detail=outcome.detail, **ids))
        else:
            self.findings.uncompared.append(Uncompared(reason=outcome.reason, **ids))

    def line_presence(self, match: MatchResult) -> None:
        """The flags of the missing and the extra lines."""
        missing: Detail = "not in cart" if self.kind == "cart" else "missing line"
        for line_id in match.unmatched_po:
            self.add("line_presence", [line_id], [], _Differs(missing, _known_by(self.po_lines[line_id]), None))
        for line_id in match.extra_doc:
            self.add("line_presence", [], [line_id], _Differs("extra line", None, _known_by(self.doc_lines[line_id])))

    def pairs(self, match: MatchResult) -> None:
        """The pairs and the uncertain pairs, in the order of their first PO line (then of their first document line)."""
        po_order = {line.line_id: n for n, line in enumerate(self.po.lines)}
        doc_order = {line.line_id: n for n, line in enumerate(self.doc.lines)}
        pairs = [(pair, False) for pair in match.pairs] + [(pair, True) for pair in match.uncertain]
        pairs.sort(key=lambda item: (min(po_order[i] for i in item[0].po_line_ids), min(doc_order[j] for j in item[0].doc_line_ids)))
        for pair, uncertain in pairs:
            self.pair(pair, uncertain)

    def pair(self, pair: Pair, uncertain: bool) -> None:
        """The items of one pair: its fields, then its line presence."""
        if uncertain or pair.kind == "merged":
            reason: Reason = "match uncertain" if uncertain else "merged line"
            for field in self.fields:
                self.add(field, pair.po_line_ids, pair.doc_line_ids, _Cannot(reason))
            self.add("line_presence", pair.po_line_ids, pair.doc_line_ids, _Cannot(reason) if uncertain else _Same(pair.kind))
            return
        [po_id] = pair.po_line_ids                                     # a matched or split pair answers one PO line
        po_line, parts = self.po_lines[po_id], [self.doc_lines[j] for j in pair.doc_line_ids]
        for field in self.fields:
            for doc_ids, outcome in self.outcomes(field, po_line, parts):
                self.add(field, [po_id], doc_ids, outcome)
        self.add("line_presence", [po_id], pair.doc_line_ids, _Same(pair.kind))

    def outcomes(self, field: Field, po_line: DocLine, parts: list[DocLine]) -> list[tuple[list[str], _Outcome]]:
        """The outcomes of one field of a matched or split pair, each with the document lines it is about: the quantity of the
        lines together, any other field a Match when every line agrees, else the outcome of each line that does not."""
        if field == "quantity":
            return _quantity(po_line, parts)
        outcomes = [self.rule(field, po_line, part) for part in parts]
        if all(isinstance(outcome, _Same) for outcome in outcomes):
            return [([part.line_id for part in parts], outcomes[0])]
        return [([part.line_id], outcome) for part, outcome in zip(parts, outcomes) if not isinstance(outcome, _Same)]

    def rule(self, field: Field, po_line: DocLine, line: DocLine) -> _Outcome:
        """The outcome of the price, unit or delivery date of one document line against its PO line."""
        if field == "price":
            return _price(po_line, line, self.po.currency, self.doc.currency)
        return _unit(po_line, line) if field == "unit" else _delivery_date(po_line, line)

    def required_info(self) -> None:
        """The item of the required information, if the PO requires any."""
        outcome = _required_info(self.po.incoterm, self.doc.incoterm)
        if outcome is not None:
            self.add("required_info", [], [], outcome)


# --- the rules of the fields ---

def _price(po_line: DocLine, line: DocLine, po_currency: DocValue, doc_currency: DocValue) -> _Outcome:
    """The price rule of the module docstring, for a PO line and one document line."""
    if _uncertain(po_currency, doc_currency, *_price_values(po_line), *_price_values(line)):
        return _Cannot("unreadable or unverified source")
    po_net, doc_net = _net(po_line), _net(line)
    if po_net is None:
        return _Cannot("no PO price")
    places = max(2, _decimals(_printed_price(po_line).value))         # the precision of the price the PO prints, at least the cent
    po_price = _rounded(po_net, places)                                # the PO line's net price per its own basis
    if doc_net is None:
        return _Cannot("total only") if _prints_an_amount(line) else _Differs("missing on the document", _priced(po_line, po_price), None)
    if po_currency.value is not None and doc_currency.value is not None and po_currency.value != doc_currency.value:
        return _Cannot("different currency")
    if po_currency.value is not None and doc_currency.value is None:
        return _Cannot("currency missing")
    if line.unit.value != po_line.unit.value:
        return _Cannot("unknown unit conversion")
    basis = po_line.price_basis.value
    if (basis is None) != (line.price_basis.value is None):
        return _Cannot("unknown price basis")
    doc_price = _rounded(doc_net, places) if basis is None else net_unit_price(line, basis, places)
    if doc_price == po_price:
        return _Same(_plain(po_price))
    return _Differs("different", _priced(po_line, po_price), _priced(line, doc_price))


def _quantity(po_line: DocLine, parts: list[DocLine]) -> list[tuple[list[str], _Outcome]]:
    """The quantity rule of the module docstring, for a PO line and the document lines of its pair, whose quantities are summed."""
    ids = [part.line_id for part in parts]
    if _uncertain(po_line.quantity, po_line.unit, *(value for part in parts for value in (part.quantity, part.unit))):
        return [(ids, _Cannot("unreadable or unverified source"))]
    if po_line.quantity.value is None:
        return [(ids, _Cannot("no PO quantity"))]
    if missing := [part.line_id for part in parts if part.quantity.value is None]:
        return [([line_id], _Differs("missing on the document", po_line.quantity, None)) for line_id in missing]
    if any(part.unit.value != po_line.unit.value for part in parts):
        return [(ids, _Cannot("unknown unit conversion"))]
    total = sum(Decimal(part.quantity.value) for part in parts)
    if total == Decimal(po_line.quantity.value):
        return [(ids, _Same(po_line.quantity.value))]
    if len(parts) == 1:
        return [(ids, _Differs("different", po_line.quantity, parts[0].quantity))]
    confirmed = DocValue(value=_plain(total), text=" + ".join(part.quantity.text or part.quantity.value for part in parts))
    return [(ids, _Differs("different", po_line.quantity, confirmed))]


def _unit(po_line: DocLine, line: DocLine) -> _Outcome:
    """The unit rule of the module docstring, for a PO line and one document line."""
    if _uncertain(po_line.unit, line.unit):
        return _Cannot("unreadable or unverified source")
    if po_line.unit.value is None:
        return _Cannot("no PO unit")
    if line.unit.value is None:
        return _Differs("missing on the document", po_line.unit, None)
    if line.unit.value != po_line.unit.value:
        return _Differs("different", po_line.unit, line.unit)
    return _Same(po_line.unit.value)


def _delivery_date(po_line: DocLine, line: DocLine) -> _Outcome:
    """The delivery date rule of the module docstring, for a PO line and one document line."""
    if _uncertain(po_line.delivery_date, line.delivery_date):
        return _Cannot("unreadable or unverified source")
    po_date, doc_date = po_line.delivery_date.value, line.delivery_date.value
    if po_date is None:
        return _Cannot("no PO date")
    if doc_date is None:
        return _Differs("missing on the document", po_line.delivery_date, None)
    if date_matches(po_date, doc_date):
        return _Same(po_date)
    return _Differs("later" if _span(doc_date)[0] > _span(po_date)[0] else "earlier", po_line.delivery_date, line.delivery_date)


def _required_info(po: DocValue, doc: DocValue) -> _Outcome | None:
    """The rule of the required information in the module docstring, for the Incoterm of the PO and of the confirmation; None
    when the PO states none."""
    if po.value is None and not po.uncertain:
        return None
    if _uncertain(po, doc):
        return _Cannot("unreadable or unverified source")
    if doc.value is None:
        return _Differs("missing on the document", po, None)
    if _words(doc.value) != _words(po.value):
        return _Differs("different", po, doc)
    return _Same(po.value)


# --- helpers ---

def _check_pairs(match: MatchResult) -> None:
    """Raise ValueError, naming the PO line, when `match` puts a PO line in two pairs or in a pair and an uncertain pair, which
    match_lines never does. A PO line in several uncertain pairs, as a candidate of several document lines, is valid."""
    paired = Counter(line_id for pair in match.pairs for line_id in pair.po_line_ids)
    waiting = {line_id for pair in match.uncertain for line_id in pair.po_line_ids}
    for line_id, count in paired.items():
        if count > 1:
            raise ValueError(f"the match puts PO line {line_id} in {count} pairs")
        if line_id in waiting:
            raise ValueError(f"the match puts PO line {line_id} in a pair and in an uncertain pair")


def _uncertain(*values: DocValue) -> bool:
    return any(value.uncertain for value in values)


def _price_values(line: DocLine) -> tuple[DocValue, ...]:
    """The values of a line that its price rests on."""
    return line.unit_price, line.net_price, line.discount_pct, line.price_basis, line.unit


def _net(line: DocLine) -> Decimal | None:
    """The line's net price per its own price basis, not rounded: its net price, else its unit price less its discount (taken
    without its sign, so that "-5" is 5 percent off as well); None when it states neither price."""
    if line.net_price.value is not None:
        return Decimal(line.net_price.value)
    if line.unit_price.value is None:
        return None
    discount = abs(Decimal(line.discount_pct.value)) if line.discount_pct.value is not None else Decimal(0)
    return Decimal(line.unit_price.value) * (1 - discount / 100)


def _rounded(amount: Decimal, places: int) -> Decimal:
    """The amount rounded half up to `places` decimals."""
    return amount.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _decimals(number: str) -> int:
    """The number of decimals of a number in normal form: 2 for "12.50", 0 for "12"."""
    return max(-Decimal(number).as_tuple().exponent, 0)


def _plain(number: Decimal) -> str:
    """A number as a plain decimal string, the normal form of aid/schema.py."""
    return format(number, "f")


def _printed_price(line: DocLine) -> DocValue:
    """The price a line prints: its net price if it states one, else its unit price."""
    return line.net_price if line.net_price.value is not None else line.unit_price


def _priced(line: DocLine, price: Decimal) -> DocValue:
    """The price the line prints with `price` as its value; its text and source stay those of the printed price."""
    return _printed_price(line).model_copy(update={"value": _plain(price)})


def _prints_an_amount(line: DocLine) -> bool:
    """Whether the document line prints an amount of money, with two decimals, that is not its quantity or its discount: in the
    text of one of its values or on the text line one of them stands on."""
    values = [value for _, value in line if isinstance(value, DocValue)]
    texts = {text for value in values for text in (value.text, value.source.text if value.source else None) if text}
    own = {Decimal(value.value) for value in (line.quantity, line.discount_pct) if value.value is not None}
    for text in texts:
        for found in _AMOUNT.finditer(text):
            amount = normalize_number(found.group())
            if amount is not None and Decimal(amount) not in own:
                return True
    return False


def _span(value: str) -> tuple[date, date]:
    """The first and the last day a delivery date in normal form covers: a day, Monday to Friday of an ISO week, or a range.
    Raises ValueError for any other text, a day or a week that does not exist, and a range that ends before it begins."""
    found = _DELIVERY.fullmatch(value)
    if found is None:
        raise ValueError(f"{value!r} is not a delivery date in normal form")
    if found["week"]:
        monday = date.fromisocalendar(int(found["year"]), int(found["week"]), 1)
        return monday, monday + timedelta(days=4)
    first = date.fromisoformat(found["first"])
    last = date.fromisoformat(found["last"]) if found["last"] else first
    if last < first:
        raise ValueError(f"the range {value!r} ends before it begins")
    return first, last


def _words(text: str) -> str:
    """A text without case and extra spaces, so that formatting is not compared."""
    return " ".join(text.split()).casefold()


def _known_by(line: DocLine) -> DocValue | None:
    """What a line-presence flag shows of a line: its item code, else its description; None when it has neither."""
    return next((value for value in (line.item_code, line.description) if value.value is not None or value.text), None)


def _printed_version(po: Document) -> int | None:
    """The version number the PO prints, or None."""
    hint = (po.version_hint.value or "").strip()
    return int(hint) if hint.isdecimal() else None
