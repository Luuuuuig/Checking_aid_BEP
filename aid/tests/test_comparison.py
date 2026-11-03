"""Tests of the comparison (aid/comparison.py): the rules on small made-up documents, and the whole made-up set against
its references. No test calls a model."""
import csv
from decimal import Decimal
from pathlib import Path

import pytest

from aid.comparison import Findings, compare, date_matches, net_unit_price
from aid.decider import NoDecider
from aid.madeup.generate import EXPORT_COLUMNS, load_specs
from aid.matching import MatchResult, Pair, match_lines
from aid.po_store import PoStore
from aid.readers.po_pdf import read_po_pdf
from aid.schema import DocValue, SourceRef
from aid.tests.fakes import FakeDecider, confirmation, line, po, po_version
from aid.tests.helpers import document_from_spec

MADEUP = Path(__file__).resolve().parents[1] / "madeup"
with open(MADEUP / "references.csv", encoding="utf-8", newline="") as _f:
    REFERENCES = list(csv.DictReader(_f))
CASES = list(dict.fromkeys(row["case_id"] for row in REFERENCES))       # MU01 to MU20, then the carts
SPECS = {spec.case_id: spec for spec in load_specs(MADEUP / "spec")}
EXPORT_MAP = {field: field for field in EXPORT_COLUMNS}                  # the made-up export names its columns after the aid's fields


# --- the brief's tests ---

def test_price_basis_and_decimal_comma():
    po_doc = po([line("KM-1", "3", "12.35", basis="1")]); doc = confirmation([line("KM-1", "3", "1234.50", basis="100")])
    f = compare(match_lines(doc, po_doc, NoDecider()), doc, po_doc, "confirmation")
    assert any(m.field == "price" for m in f.matches) and not [x for x in f.flags if x.field == "price"]

def test_price_difference_flag():
    po_doc = po([line("KM-1", "3", "12.50")]); doc = confirmation([line("KM-1", "3", "14.00")])
    f = compare(match_lines(doc, po_doc, NoDecider()), doc, po_doc, "confirmation")
    assert f.flags[0].field == "price" and f.flags[0].doc_value.value == "14.00" and f.flags[0].detail == "different"

def test_week_and_range_dates():
    assert date_matches("2026-11-18", "2026-W47") and date_matches("2026-11-18", "2026-11-16/2026-11-20")
    assert not date_matches("2026-11-23", "2026-W47") and date_matches("2027-01-05", "2027-W01")

def test_missing_and_extra_lines():
    po_doc = po([line("KM-1", "1"), line("KM-2", "1")]); doc = confirmation([line("KM-1", "1"), line("KM-7", "1")])
    f = compare(match_lines(doc, po_doc, NoDecider()), doc, po_doc, "confirmation")
    assert {(x.field, x.detail) for x in f.flags} == {("line_presence", "missing line"), ("line_presence", "extra line")}

def test_uncertain_value_is_never_a_match():
    po_doc = po([line("KM-1", "3", "12.50")]); doc = confirmation([line("KM-1", "3", "12.50")])
    doc.lines[0].unit_price.uncertain = True
    f = compare(match_lines(doc, po_doc, NoDecider()), doc, po_doc, "confirmation")
    assert any(u.field == "price" and u.reason == "unreadable or unverified source" for u in f.uncompared) and not any(m.field == "price" for m in f.matches)

def test_currency_and_total_only():
    po_doc = po([line("KM-1", "3", "12.50")], currency="EUR")
    doc = confirmation([line("KM-1", "3", "12.50")], currency="USD")
    assert compare(match_lines(doc, po_doc, NoDecider()), doc, po_doc, "confirmation").uncompared[0].reason == "different currency"
    doc2 = confirmation([line("KM-1", "3", None)]); doc2.lines[0].description.text = "3 x KM-1 totaal 37,50"
    assert compare(match_lines(doc2, po_doc, NoDecider()), doc2, po_doc, "confirmation").uncompared[0].reason == "total only"

def test_incoterm_missing():
    po_doc = po([line("KM-1", "1")], incoterm="DAP Rotterdam"); doc = confirmation([line("KM-1", "1")], incoterm=None)
    f = compare(match_lines(doc, po_doc, NoDecider()), doc, po_doc, "confirmation"); assert any(x.field == "required_info" for x in f.flags)


# The tests below cover the rulings given with the task, on top of the brief's tests above.

def compared(doc, po_doc, kind="confirmation") -> Findings:
    """The findings of `doc` against `po_doc`, their lines matched without a decider."""
    return compare(match_lines(doc, po_doc, NoDecider()), doc, po_doc, kind)


def items(f: Findings, field: str) -> list[tuple[str, str]]:
    """The items of one field: ("match", value), ("flag", detail) or ("uncompared", reason), the matches first."""
    return ([("match", m.value) for m in f.matches if m.field == field] + [("flag", x.detail) for x in f.flags if x.field == field]
            + [("uncompared", u.reason) for u in f.uncompared if u.field == field])


def only(f: Findings, field: str) -> tuple[str, str]:
    """The one item of a field, in the findings of a single pair."""
    [item] = items(f, field)
    return item


def priced(qty: str, price: str | None, basis: str | None = "1", unit: str | None = "pc", **values) -> list:
    """One line of item KM-1, in a list for po() or confirmation()."""
    return [line("KM-1", qty, price, basis=basis, unit=unit, **values)]


# --- net_unit_price and the price (ruling 1) ---

def test_net_unit_price_on_the_line_s_own_basis_and_on_another():
    per_100 = line("KM-1", "200", "12.50", basis="100")
    assert [str(net_unit_price(per_100, basis)) for basis in (None, "1", "1000")] == ["12.50", "0.13", "125.00"]   # 0.125 rounds up
    per_piece = line("KM-1", "200", "0.125", basis="1")
    assert [str(net_unit_price(per_piece, basis)) for basis in (None, "100")] == ["0.13", "12.50"]


def test_net_unit_price_with_a_discount_or_a_net_price():
    assert str(net_unit_price(line("KM-1", "6", "20.00", basis="1", discount="5"))) == "19.00"
    assert str(net_unit_price(line("KM-1", "6", "20.00", basis="1", discount="-5"))) == "19.00"          # printed with a minus
    assert str(net_unit_price(line("KM-1", "6", "20.00", basis="1", discount="2.5"), "100")) == "1950.00"
    assert str(net_unit_price(line("KM-1", "6", "20.00", basis="1", discount="5", net_price="18.75"))) == "18.75"   # printed net
    assert str(net_unit_price(line("KM-1", "6", None, basis="100", net_price="18.75"), "1")) == "0.19"


def test_net_unit_price_at_another_precision():
    per_1000 = line("KM-1", "1000", "42.50", basis="1000")
    assert [str(net_unit_price(per_1000, "1", places)) for places in (2, 4)] == ["0.04", "0.0425"]


def test_net_unit_price_needs_a_price_and_a_basis_but_no_quantity():
    assert net_unit_price(line("KM-1", "3", "12.50")) is None and net_unit_price(line("KM-1", "3", "12.50"), "1") is None   # no basis
    assert net_unit_price(line("KM-1", "3", None, basis="1", discount="5")) is None              # no unit price and no net price
    assert net_unit_price(line("KM-1", "0", "12.50", basis="1")) == net_unit_price(line("KM-1", "", "12.50", basis="1")) == Decimal("12.50")


def test_the_price_is_compared_at_the_po_line_s_basis_both_ways():
    assert only(compared(confirmation(priced("200", "0.125")), po(priced("200", "12.50", "100"))), "price") == ("match", "12.50")
    assert only(compared(confirmation(priced("200", "125.00", "1000")), po(priced("200", "0.13"))), "price") == ("match", "0.13")
    # 0.13 a piece is 13.00 per 100: the flag gives both prices per 100, the PO line's basis, with their texts as printed.
    [flag] = compared(confirmation(priced("200", "0.13")), po(priced("200", "12.50", "100"))).flags
    assert (flag.field, flag.detail) == ("price", "different")
    assert (flag.po_value.value, flag.doc_value.value, flag.doc_value.text) == ("12.50", "13.00", "0.13")


def test_the_price_is_compared_at_the_precision_of_the_po_s_price_and_at_least_the_cent():
    po_doc = po(priced("1000", "0.0425"))
    [flag] = compared(confirmation(priced("1000", "0.04")), po_doc).flags                    # equal to the cent, not to the PO's 4 decimals
    assert (flag.field, flag.detail, flag.po_value.value, flag.doc_value.value) == ("price", "different", "0.0425", "0.0400")
    assert only(compared(confirmation(priced("1000", "0.0425")), po_doc), "price") == ("match", "0.0425")
    assert only(compared(confirmation(priced("1000", "42.50", "1000")), po_doc), "price") == ("match", "0.0425")    # per 1000 to per piece
    assert only(compared(confirmation(priced("3", "12.345")), po(priced("3", "12.35"))), "price") == ("match", "12.35")    # half up
    assert only(compared(confirmation(priced("3", "12.3")), po(priced("3", "12.3"))), "price") == ("match", "12.30")      # at least the cent
    assert only(compared(confirmation(priced("1000", "0.0404")), po(priced("1000", "0.0425", discount="5"))), "price") == ("match", "0.0404")


def test_a_discount_or_a_net_price_on_either_side():
    po_doc = po(priced("6", "20.00"))
    [flag] = compared(confirmation(priced("6", "20.00", discount="5")), po_doc).flags                                   # MU10
    assert (flag.field, flag.detail) == ("price", "different")
    assert (flag.po_value.value, flag.doc_value.value, flag.doc_value.text) == ("20.00", "19.00", "20.00")
    assert only(compared(confirmation(priced("6", "21.00", net_price="20.00")), po_doc), "price") == ("match", "20.00")
    [flag] = compared(confirmation(priced("6", None, net_price="19.50")), po_doc).flags                  # the net price is the one printed
    assert (flag.detail, flag.doc_value.value, flag.doc_value.text) == ("different", "19.50", "19.50")
    assert only(compared(confirmation(priced("6", "19.00")), po(priced("6", "20.00", discount="5"))), "price") == ("match", "19.00")


def test_a_price_basis_on_one_side_only_is_unknown():
    per_piece, unstated = priced("3", "12.50"), priced("3", "12.50", basis=None)
    assert only(compared(confirmation(unstated), po(per_piece)), "price") == ("uncompared", "unknown price basis")
    assert only(compared(confirmation(per_piece), po(unstated)), "price") == ("uncompared", "unknown price basis")
    assert only(compared(confirmation(unstated), po(unstated)), "price") == ("match", "12.50")          # neither states one: as they stand


def test_a_currency_the_po_states_and_the_document_does_not_is_never_a_match():
    """Final review, finding 31: a currency the document does not state cannot be shown to be the PO's, so its price is not
    compared, whether the numbers agree or not. A PO that states none, or both stating the same, compares as before."""
    for price in ("12.50", "12.70"):
        f = compared(confirmation(priced("3", price)), po(priced("3", "12.50"), currency="EUR"))
        assert only(f, "price") == ("uncompared", "currency missing") and only(f, "quantity") == ("match", "3")
    assert only(compared(confirmation(priced("3", "12.50"), currency="EUR"), po(priced("3", "12.50"), currency="EUR")), "price") == ("match", "12.50")
    assert only(compared(confirmation(priced("3", "12.50"), currency="EUR"), po(priced("3", "12.50"))), "price") == ("match", "12.50")
    assert only(compared(confirmation(priced("3", None)), po(priced("3", "12.50"), currency="EUR")), "price") == ("flag", "missing on the document")


def test_a_price_missing_on_the_document_or_given_as_a_total_only():
    po_doc = po(priced("3", "12.50"))
    [flag] = compared(confirmation(priced("3", None, basis=None)), po_doc).flags
    assert (flag.field, flag.detail, flag.po_value.value, flag.doc_value) == ("price", "missing on the document", "12.50", None)
    # As in MU06, the total stands on the text line that the line's values come from.
    total = confirmation(priced("3", None, basis=None))
    for value in (total.lines[0].item_code, total.lines[0].quantity, total.lines[0].unit):
        value.source = SourceRef(page=1, line=8, text="1 KM-1 Magneetventiel 24 V 3 st 37,50 16-11-2026")
    assert only(compared(total, po_doc), "price") == ("uncompared", "total only")
    # A quantity with two decimals is no total.
    metres = confirmation(priced("2.50", None, basis=None, unit="m"))
    metres.lines[0].quantity.text = "2,50"
    assert only(compared(metres, po(priced("2.50", "4.00", unit="m"))), "price") == ("flag", "missing on the document")


def test_a_po_line_without_a_price_or_a_quantity_cannot_be_compared():
    f = compared(confirmation(priced("3", "12.50")), po(priced("", None, basis=None)))
    assert only(f, "price") == ("uncompared", "no PO price") and only(f, "quantity") == ("uncompared", "no PO quantity")


def test_a_quantity_missing_on_the_document():
    [flag] = compared(confirmation(priced("", "12.50")), po(priced("3", "12.50"))).flags
    assert (flag.field, flag.detail, flag.po_value.value, flag.doc_value) == ("quantity", "missing on the document", "3", None)


def test_quantities_are_compared_as_numbers():
    assert only(compared(confirmation(priced("5.00", None)), po(priced("5", None))), "quantity") == ("match", "5")
    [flag] = compared(confirmation(priced("12", None)), po(priced("10", None))).flags                                   # MU11
    assert (flag.field, flag.detail, flag.po_value.value, flag.doc_value.value) == ("quantity", "different", "10", "12")


# --- units (ruling 2) ---

def test_a_unit_missing_on_the_document():                                                                         # MU13
    f = compared(confirmation(priced("4", "12.50", unit=None)), po(priced("4", "12.50")))
    [flag] = f.flags
    assert (flag.field, flag.detail, flag.po_value.value, flag.doc_value) == ("unit", "missing on the document", "pc", None)
    assert only(f, "price") == only(f, "quantity") == ("uncompared", "unknown unit conversion")


def test_another_unit_on_the_document():                                                                           # MU12
    f = compared(confirmation(priced("50", "1.20")), po(priced("50", "1.20", unit="m")))
    [flag] = f.flags
    assert (flag.field, flag.detail, flag.po_value.value, flag.doc_value.value) == ("unit", "different", "m", "pc")
    assert only(f, "price") == only(f, "quantity") == ("uncompared", "unknown unit conversion")


def test_the_same_unit_and_a_po_line_without_a_unit():
    def fields(doc_unit, po_unit):
        f = compared(confirmation(priced("4", "12.50", unit=doc_unit)), po(priced("4", "12.50", unit=po_unit)))
        return [only(f, field) for field in ("price", "quantity", "unit")]
    assert fields("pc", "pc") == [("match", "12.50"), ("match", "4"), ("match", "pc")]
    assert fields("pc", None) == [("uncompared", "unknown unit conversion")] * 2 + [("uncompared", "no PO unit")]
    assert fields(None, None) == [("match", "12.50"), ("match", "4"), ("uncompared", "no PO unit")]     # neither states a unit


# --- merged and split pairs (rulings 3 and 4) ---

def test_a_merged_pair_is_not_compared():                                                                          # MU19
    po_doc = po([line("KM-1", "4", "24.00", basis="1", unit="pc", date="2026-11-16"),
                 line("KM-2", "4", "7.50", basis="1", unit="pc", date="2026-11-16")])
    doc = confirmation([line("KM-1 / KM-2", "4", "31.50", basis="1", unit="pc", date="2026-11-16")])
    f = compared(doc, po_doc)
    assert f.flags == [] and [(m.field, m.po_line_ids, m.doc_line_ids, m.value) for m in f.matches] == [
        ("line_presence", ["1", "2"], ["1"], "merged")]
    assert [(u.field, u.po_line_ids, u.doc_line_ids, u.reason) for u in f.uncompared] == [
        (field, ["1", "2"], ["1"], "merged line") for field in ("price", "quantity", "unit", "delivery_date")]


def test_a_split_pair_sums_its_quantities():
    po_doc = po(priced("10", "12.50", date="2026-11-16"))
    doc = confirmation(priced("4", "12.50", date="2026-11-16") + priced("6", "1250.00", "100", date="2026-W47"))
    f = compared(doc, po_doc)
    assert f.flags == [] and f.uncompared == []
    assert [(m.field, m.po_line_ids, m.doc_line_ids, m.value) for m in f.matches] == [
        ("price", ["1"], ["1", "2"], "12.50"), ("quantity", ["1"], ["1", "2"], "10"), ("unit", ["1"], ["1", "2"], "pc"),
        ("delivery_date", ["1"], ["1", "2"], "2026-11-16"), ("line_presence", ["1"], ["1", "2"], "split")]


def test_a_split_pair_with_one_differing_part():
    po_doc = po(priced("10", "12.50", date="2026-11-16"))
    doc = confirmation(priced("4", "12.50", date="2026-11-16") + priced("5", "13.00", date="2026-W48"))
    f = compared(doc, po_doc)
    assert [(x.flag_id, x.field, x.po_line_ids, x.doc_line_ids, x.detail) for x in f.flags] == [
        ("F1", "price", ["1"], ["2"], "different"), ("F2", "quantity", ["1"], ["1", "2"], "different"),
        ("F3", "delivery_date", ["1"], ["2"], "later")]
    price, quantity, _ = f.flags
    assert (price.po_value.value, price.doc_value.value) == ("12.50", "13.00")
    assert (quantity.po_value.value, quantity.doc_value.value, quantity.doc_value.text) == ("10", "9", "4 + 5")       # the parts summed
    assert [(m.field, m.doc_line_ids) for m in f.matches] == [("unit", ["1", "2"]), ("line_presence", ["1", "2"])]


def test_a_split_pair_with_a_part_that_states_no_unit_or_no_quantity():
    po_doc = po(priced("10", "12.50", date="2026-11-16"))
    f = compared(confirmation(priced("4", "12.50", date="2026-11-16") + priced("6", "12.50", unit=None, date="2026-11-16")), po_doc)
    assert [(x.field, x.doc_line_ids, x.detail) for x in f.flags] == [("unit", ["2"], "missing on the document")]
    assert [(u.field, u.doc_line_ids, u.reason) for u in f.uncompared] == [("price", ["2"], "unknown unit conversion"),
                                                                           ("quantity", ["1", "2"], "unknown unit conversion")]
    f = compared(confirmation(priced("4", "12.50", date="2026-11-16") + priced("", "12.50", date="2026-11-16")), po_doc)
    assert [(x.field, x.doc_line_ids, x.detail) for x in f.flags] == [("quantity", ["2"], "missing on the document")]


# --- required information (ruling 5) ---

def test_the_incoterm_of_the_confirmation():
    def required(po_term, doc_term, uncertain=False):
        po_doc, doc = po([line("KM-1", "1")], incoterm=po_term), confirmation([line("KM-1", "1")], incoterm=doc_term)
        doc.incoterm.uncertain = uncertain
        return items(compared(doc, po_doc), "required_info")
    assert required("DAP Rotterdam", "DAP Rotterdam") == required("DAP Rotterdam", " dap  rotterdam") == [("match", "DAP Rotterdam")]
    assert required("DAP Rotterdam", "FCA Venlo") == [("flag", "different")]
    assert required("DAP Rotterdam", None) == [("flag", "missing on the document")]
    assert required(None, "DAP Rotterdam") == required(None, None) == []                       # the PO requires none
    assert required("DAP Rotterdam", "DAP Rotterdam", uncertain=True) == [("uncompared", "unreadable or unverified source")]
    [flag] = compared(confirmation([line("KM-1", "1")], incoterm="FCA Venlo"), po([line("KM-1", "1")], incoterm="DAP Rotterdam")).flags
    assert (flag.po_line_ids, flag.doc_line_ids, flag.po_value.value, flag.doc_value.value) == ([], [], "DAP Rotterdam", "FCA Venlo")


# --- delivery dates (ruling 6) ---

def test_the_details_of_a_delivery_date():
    def delivery(doc_date, po_date="2026-11-16"):
        return only(compared(confirmation([line("KM-1", "1", date=doc_date)]), po([line("KM-1", "1", date=po_date)])), "delivery_date")
    assert delivery("2026-11-16") == delivery("2026-W47") == delivery("2026-11-13/2026-11-16") == ("match", "2026-11-16")
    assert delivery("2026-11-17") == delivery("2026-W48") == delivery("2026-11-17/2026-11-20") == ("flag", "later")
    assert delivery("2026-11-13") == delivery("2026-W46") == delivery("2026-11-09/2026-11-13") == ("flag", "earlier")
    assert delivery(None) == ("flag", "missing on the document")
    assert delivery("2026-11-16", po_date=None) == delivery(None, po_date=None) == ("uncompared", "no PO date")
    [flag] = compared(confirmation([line("KM-1", "1", date="2026-W48")]), po([line("KM-1", "1", date="2026-11-16")])).flags   # MU14
    assert (flag.po_value.value, flag.doc_value.value) == ("2026-11-16", "2026-W48")


def test_date_matches_takes_a_week_from_monday_to_friday_and_a_range_with_both_ends():
    assert date_matches("2026-11-16", "2026-W47") and date_matches("2026-11-20", "2026-W47")
    assert not date_matches("2026-11-21", "2026-W47") and not date_matches("2026-11-15", "2026-W47")     # the Saturday after, the Sunday before
    assert date_matches("2026-11-20", "2026-11-16/2026-11-20") and not date_matches("2026-11-21", "2026-11-16/2026-11-20")
    assert date_matches("2026-11-16", "2026-11-16") and not date_matches("2026-11-16", "2026-11-17")
    assert date_matches("2027-01-01", "2026-W53")                                                        # a week across the turn of the year
    # 2027 has no week 53; a day as printed, a range of weeks, a range that ends before it begins and a day without hyphens are
    # no normal forms.
    for po_value, doc_value in (("2026-11-16", "2027-W53"), ("16-11-2026", "2026-W47"), ("2026-11-18", "2026-W47/2026-W48"),
                                ("2026-11-16", "2026-11-20/2026-11-16"), ("20261116", "2026-W47")):
        with pytest.raises(ValueError):
            date_matches(po_value, doc_value)


# --- the version and the order of the findings (rulings 7 and 8) ---

def test_the_version_is_the_one_the_po_prints():
    doc = confirmation([line("KM-1", "1")])
    assert compared(doc, po_version(3, [line("KM-1", "1")]).document).version == 3
    assert compared(doc, po([line("KM-1", "1")])).version is None


def test_flag_ids_and_the_order_of_the_findings():
    po_doc = po([line("KM-1", "5", "10.00", basis="1", unit="pc", date="2026-11-16"),
                 line("KM-2", "1", "3.00", basis="1", unit="pc", date="2026-11-16"),
                 line("KM-3", "2", "4.00", basis="1", unit="pc", date="2026-11-16")], incoterm="DAP Rotterdam")
    doc = confirmation([line("KM-3", "2", "4.50", basis="1", unit="pc", date="2026-11-13"),
                        line("KM-9", "1", "1.00", basis="1", unit="pc", date="2026-11-16"),
                        line("KM-1", "6", "10.00", basis="1", unit="pc")])
    f = compared(doc, po_doc)
    assert [(x.flag_id, x.field, x.po_line_ids, x.doc_line_ids, x.detail) for x in f.flags] == [
        ("F1", "line_presence", ["2"], [], "missing line"), ("F2", "line_presence", [], ["2"], "extra line"),
        ("F3", "quantity", ["1"], ["3"], "different"), ("F4", "delivery_date", ["1"], ["3"], "missing on the document"),
        ("F5", "price", ["3"], ["1"], "different"), ("F6", "delivery_date", ["3"], ["1"], "earlier"),
        ("F7", "required_info", [], [], "missing on the document")]
    assert [(m.field, m.po_line_ids) for m in f.matches] == [("price", ["1"]), ("unit", ["1"]), ("line_presence", ["1"]),
                                                              ("quantity", ["3"]), ("unit", ["3"]), ("line_presence", ["3"])]
    assert Findings.model_validate_json(f.model_dump_json()) == f                    # the findings keep as JSON, as the case's steps do


def test_uncertain_pairs_take_their_place_in_po_order():
    po_doc = po([line(code, "1", unit="pc", date="2026-11-16") for code in ("KM-1", "KM-2", "KM-3")])
    doc = confirmation([line(code, "1", unit="pc", date="2026-11-16") for code in ("KM-3", "KM-2", "KM-1")])
    match = MatchResult(pairs=[Pair(po_line_ids=["3"], doc_line_ids=["1"], basis="code", confidence=1.0, kind="matched"),
                               Pair(po_line_ids=["1"], doc_line_ids=["3"], basis="code", confidence=1.0, kind="matched")],
                        unmatched_po=[], extra_doc=[],
                        uncertain=[Pair(po_line_ids=["2"], doc_line_ids=["2"], basis="description", confidence=0.7, kind="matched")])
    f = compare(match, doc, po_doc, "confirmation")
    uncertain = [(field, ["2"], "match uncertain") for field in ("price", "quantity", "unit", "delivery_date", "line_presence")]
    assert [(u.field, u.po_line_ids, u.reason) for u in f.uncompared] == [
        ("price", ["1"], "no PO price"), *uncertain, ("price", ["3"], "no PO price")]
    assert [m.po_line_ids for m in f.matches] == [["1"]] * 4 + [["3"]] * 4 and f.flags == []


# --- uncertain values, uncertain matches and the cart (ruling 9) ---

def test_an_uncertain_value_on_either_side_is_never_compared():
    po_doc = po(priced("3", "12.50", date="2026-11-16"), currency="EUR")
    doc = confirmation(priced("3", "12.50", date="2026-11-16"), currency="EUR")
    po_doc.lines[0].delivery_date.uncertain = True                                     # a PO value
    f = compared(doc, po_doc)
    assert only(f, "delivery_date") == ("uncompared", "unreadable or unverified source") and only(f, "price") == ("match", "12.50")
    po_doc.lines[0].delivery_date.uncertain = False
    po_doc.lines[0].unit.uncertain = True                                              # the price and the quantity rest on the unit
    f = compared(doc, po_doc)
    assert [only(f, field) for field in ("price", "quantity", "unit")] == [("uncompared", "unreadable or unverified source")] * 3
    po_doc.lines[0].unit.uncertain = False
    doc.currency.uncertain = True                                                      # the price rests on the currency
    f = compared(doc, po_doc)
    assert only(f, "price") == ("uncompared", "unreadable or unverified source") and only(f, "quantity") == ("match", "3")


def test_two_lines_the_decider_puts_on_one_po_line_are_one_split_pair():
    # Before Task 10's fix the two choices were two matched pairs, and each gave a quantity Match of 10.
    po_doc = po([line("", "10", unit="pc", desc="Hydraulic hose 10 mm, 2 m")])
    doc = confirmation([line("", "10", unit="pc", desc="Hose 10 mm, 2 m"), line("", "10", unit="pc", desc="Hose 10mm 2m")])
    match = match_lines(doc, po_doc, FakeDecider("1", 0.95))
    assert [(p.po_line_ids, p.doc_line_ids, p.kind, p.basis) for p in match.pairs] == [(["1"], ["1", "2"], "split", "model")]
    f = compare(match, doc, po_doc, "confirmation")
    [flag] = [x for x in f.flags if x.field == "quantity"]
    assert (flag.detail, flag.po_line_ids, flag.doc_line_ids) == ("different", ["1"], ["1", "2"])
    assert (flag.po_value.value, flag.doc_value.value) == ("10", "20")
    assert "quantity" not in [m.field for m in f.matches]


def test_a_match_that_puts_a_po_line_in_two_pairs_is_refused():
    po_doc = po([line("", "10", unit="pc", desc="Hydraulic hose 10 mm, 2 m")])
    doc = confirmation([line("", "10", unit="pc", desc="Hose 10 mm, 2 m"), line("", "10", unit="pc", desc="Hose 10mm 2m")])
    first, second = (Pair(po_line_ids=["1"], doc_line_ids=[doc_id], basis="model", confidence=0.95, kind="matched") for doc_id in ("1", "2"))
    twice = MatchResult(pairs=[first, second], unmatched_po=[], extra_doc=[], uncertain=[])
    with pytest.raises(ValueError, match="PO line 1 in 2 pairs"):
        compare(twice, doc, po_doc, "confirmation")
    with pytest.raises(ValueError, match="PO line 1 in a pair and in an uncertain pair"):
        compare(MatchResult(pairs=[first], unmatched_po=[], extra_doc=[], uncertain=[second]), doc, po_doc, "confirmation")
    # A candidate of two lines that no decider settles is in the uncertain pair of each, which match_lines makes and compare takes.
    shared = match_lines(doc, po_doc, NoDecider())
    assert [(p.po_line_ids, p.doc_line_ids) for p in shared.uncertain] == [(["1"], ["1"]), (["1"], ["2"])]
    f = compare(shared, doc, po_doc, "confirmation")
    assert f.flags == f.matches == [] and [(u.doc_line_ids, u.reason) for u in f.uncompared] == [
        (doc_id, "match uncertain") for doc_id in (["1"], ["2"]) for _ in range(5)]


def test_every_field_of_an_uncertain_match_is_uncompared():
    po_doc = po([line("", "5", "10.00", basis="1", unit="pc", desc="Hydraulic hose 10 mm, 2 m", date="2026-11-16")])
    doc = confirmation([line("", "5", "10.00", basis="1", unit="pc", desc="Hose 10 mm, 2 m", date="2026-11-16")])
    match = match_lines(doc, po_doc, NoDecider())                                      # a candidate that no decider settles
    assert match.uncertain and not match.pairs
    f = compare(match, doc, po_doc, "confirmation")
    assert f.flags == [] and f.matches == []
    assert [(u.field, u.po_line_ids, u.doc_line_ids, u.reason) for u in f.uncompared] == [
        (field, ["1"], ["1"], "match uncertain") for field in ("price", "quantity", "unit", "delivery_date", "line_presence")]


def test_a_cart_is_compared_on_price_quantity_and_unit_only():
    po_doc = po([line("KM-1", "5", "12.50", basis="1", unit="pc", date="2026-11-12"), line("KM-2", "3", "40.00", basis="1", unit="pc"),
                 line("KM-3", "1", "2.00", basis="1", unit="pc"), line("KM-4", "1", "3.00", basis="1", unit="pc"),
                 line("", "1", "9.00", basis="1", unit="pc", desc="Hydraulic hose 10 mm, 2 m")], incoterm="DAP Rotterdam")
    cart = confirmation([line("KM-1", "5", "12.50", basis="1", unit="pc", date="2026-12-01"),       # a date and no Incoterm: not compared
                         line("KM-3 / KM-4", "1", "5.00", basis="1", unit="pc"),
                         line("", "1", "9.00", basis="1", unit="pc", desc="Hose 10 mm, 2 m")]).model_copy(update={"kind": "cart"})
    f = compared(cart, po_doc, "cart")
    assert [(x.flag_id, x.field, x.po_line_ids, x.detail) for x in f.flags] == [("F1", "line_presence", ["2"], "not in cart")]
    assert [(m.field, m.po_line_ids) for m in f.matches] == [("price", ["1"]), ("quantity", ["1"]), ("unit", ["1"]), ("line_presence", ["1"]),
                                                              ("line_presence", ["3", "4"])]
    assert [(u.field, u.po_line_ids, u.reason) for u in f.uncompared] == [
        *((field, ["3", "4"], "merged line") for field in ("price", "quantity", "unit")),
        *((field, ["5"], "match uncertain") for field in ("price", "quantity", "unit", "line_presence"))]


# --- line presence ---

def test_a_missing_and_an_extra_line_show_the_line_s_code_or_description():
    f = compared(confirmation([line("KM-7", "1", desc="Kogelkraan DN25")]), po([line("KM-1", "1", desc="Klep"), line("", "1", desc="Slang")]))
    assert [(x.detail, x.po_value and x.po_value.value, x.doc_value and x.doc_value.value) for x in f.flags] == [
        ("missing line", "KM-1", None), ("missing line", "Slang", None), ("extra line", None, "KM-7")]


def test_a_flagged_field_leaves_the_other_fields_of_its_line_compared():
    f = compared(confirmation(priced("12", "14.00", date="2026-11-23")), po(priced("10", "12.50", date="2026-11-16")))
    assert [(x.field, x.detail) for x in f.flags] == [("price", "different"), ("quantity", "different"), ("delivery_date", "later")]
    assert [m.field for m in f.matches] == ["unit", "line_presence"] and f.uncompared == []


# --- the made-up set (ruling 10) ---

def made_up_case(case_id: str, madeup_out: Path, tmp_path: Path):
    """The perfect reading of a made-up case, the PO it is compared with (the PDF of the version it answers, or a cart's
    export) and the kind of check."""
    if case_id.startswith("CART"):
        [version] = PoStore(tmp_path).from_export(madeup_out / f"{case_id}_export.csv", EXPORT_MAP)
        return document_from_spec(case_id, "cart"), version.document, "cart"
    answered = next(row["version_answered"] for row in REFERENCES if row["case_id"] == case_id)
    return document_from_spec(case_id), read_po_pdf(madeup_out / f"{case_id}_po_v{answered}.pdf"), "confirmation"


def reference_item(case_id: str, row: dict) -> tuple[str, str]:
    """What the findings must hold for one row of references.csv: ("match", ""), ("uncompared", "") or ("flag", the kind of the
    difference the case declares)."""
    if row["label"] == "same":
        return "match", ""
    if row["label"] == "cannot be settled":
        return "uncompared", ""
    kinds = {(d.line_id, d.field): d.kind for d in SPECS[case_id].document.differences}
    return "flag", kinds[(row["line_id"], row["field"])]                 # different, or missing on the confirmation


def found_items(f: Findings) -> dict[tuple[str, str, bool], list[tuple[str, str]]]:
    """The findings by reference item: (field, line id, whether the line is an extra document line). An item of several PO lines
    (a merged pair) counts for each of them."""
    found: dict[tuple[str, str, bool], list[tuple[str, str]]] = {}
    for kind, item, word in ([("flag", x, x.detail) for x in f.flags] + [("match", m, "") for m in f.matches]
                             + [("uncompared", u, "") for u in f.uncompared]):
        if item.field == "required_info":
            keys = [("required_info", "", False)]
        elif item.po_line_ids:
            keys = [(item.field, line_id, False) for line_id in item.po_line_ids]
        else:
            keys = [(item.field, line_id, True) for line_id in item.doc_line_ids]
        for key in keys:
            found.setdefault(key, []).append((kind, word))
    return found


@pytest.mark.parametrize("case_id", CASES)
def test_every_made_up_case_gives_the_findings_of_its_references(case_id, madeup_out, tmp_path):
    """Each item of references.csv is one finding: same a Match, different and missing on the confirmation a Flag with the kind
    of difference the case declares, cannot be settled an Uncompared; a cart's required information (not applicable) is none.
    No finding is left over."""
    doc, po_doc, kind = made_up_case(case_id, madeup_out, tmp_path)
    f = compare(match_lines(doc, po_doc, NoDecider()), doc, po_doc, kind)
    rows = [row for row in REFERENCES if row["case_id"] == case_id and row["item_type"] != "version" and row["label"] != "not applicable"]
    expected = {(row["field"], row["line_id"], row["line_label"] == "extra"): [reference_item(case_id, row)] for row in rows}
    assert len(expected) == len(rows) and found_items(f) == expected
    assert f.version == (None if kind == "cart" else int(rows[0]["version_answered"]))


def test_the_reasons_of_the_made_up_items_that_cannot_be_settled(madeup_out, tmp_path):
    reasons = {}
    for case_id in ("MU06", "MU12", "MU13", "MU19"):
        doc, po_doc, kind = made_up_case(case_id, madeup_out, tmp_path / case_id)
        f = compare(match_lines(doc, po_doc, NoDecider()), doc, po_doc, kind)
        reasons[case_id] = [(u.field, u.po_line_ids, u.reason) for u in f.uncompared]
    assert reasons == {
        "MU06": [("price", [line_id], "total only") for line_id in ("1", "2", "3")],
        "MU12": [("price", ["1"], "unknown unit conversion"), ("quantity", ["1"], "unknown unit conversion")],
        "MU13": [("price", ["1"], "unknown unit conversion"), ("quantity", ["1"], "unknown unit conversion")],
        "MU19": [(field, ["1", "2"], "merged line") for field in ("price", "quantity", "unit", "delivery_date")]}


def test_the_price_flags_of_the_made_up_set_read_one_net_price_against_the_other(madeup_out, tmp_path):
    for case_id, po_price, doc_price in (("MU09", "12.50", "0.13"), ("MU10", "20.00", "19.00"), ("MU07", "12.50", "14.00")):
        doc, po_doc, kind = made_up_case(case_id, madeup_out, tmp_path / case_id)
        [flag] = compare(match_lines(doc, po_doc, NoDecider()), doc, po_doc, kind).flags
        assert (flag.field, flag.po_value.value, flag.doc_value.value) == ("price", po_price, doc_price)
        printed, po_printed = doc.lines[0].unit_price, po_doc.lines[0].unit_price         # the texts and sources stay those printed
        assert (flag.doc_value.text, flag.doc_value.source, flag.po_value.text, flag.po_value.source) == (
            printed.text, printed.source, po_printed.text, po_printed.source)
