import json
from datetime import date

import pytest
from pydantic import ValidationError

from aid.schema import (DocLine, DocValue, Document, SourceRef, document_schema, normalize_basis,
                        normalize_currency, normalize_date, normalize_number, normalize_unit)


@pytest.mark.parametrize("text,expected", [("1.234,50", "1234.50"), ("12,5", "12.5"), ("1,234.50", "1234.50"), ("€ 12,50", "12.50"), ("12.50", "12.50"), ("abc", None)])
def test_normalize_number(text, expected): assert normalize_number(text) == expected

@pytest.mark.parametrize("text,expected", [("per 100", "100"), ("/100 st", "100"), ("per stuk", "1"), ("pro Stück", "1"), ("per 1000", "1000")])
def test_normalize_basis(text, expected): assert normalize_basis(text) == expected

def test_week_number_at_year_boundary():
    assert normalize_date("KW 1", date(2026, 12, 28)) == "2027-W01"
    assert normalize_date("week 47", date(2026, 11, 3)) == "2026-W47"
    assert normalize_date("16.11.2026 - 20.11.2026", None) == "2026-11-16/2026-11-20"
    assert normalize_date("16-11-2026", None) == "2026-11-16"

def test_missing_is_not_zero():
    v = DocValue(); assert v.missing and v.value is None

def test_schema_is_strict():
    s = document_schema(); assert s["additionalProperties"] is False and "lines" in s["properties"]


# The tests below cover what the brief's rulings add to the five above.

# --- the document model ---

def test_a_value_is_missing_when_it_has_no_normalised_value():
    assert DocValue(value=None, text="about 12", uncertain=True).missing     # found on the document, but not readable as a value
    assert not DocValue(value="0", text="0").missing                         # zero is a value
    assert "missing" not in DocValue().model_dump()                         # a property, not a stored field


def test_default_values_are_not_shared_between_instances():
    a, b = DocLine(line_id="1"), DocLine(line_id="2")
    a.quantity.value = "5"
    assert a.quantity is not b.quantity and b.quantity.missing
    d, e = Document(kind="po"), Document(kind="po")
    d.currency.value = "EUR"
    d.lines.append(a)
    assert e.currency.missing and e.lines == []


def test_document_defaults_and_kinds():
    d = Document(kind="cart")
    assert (d.lines, d.source_file, d.pages, d.reader) == ([], "", 0, "")
    header = ("po_reference", "version_hint", "supplier", "document_date", "currency", "incoterm", "capture_date")
    assert all(getattr(d, name).missing for name in header)
    line = ("item_code", "description", "quantity", "unit", "unit_price", "price_basis", "discount_pct", "net_price", "delivery_date")
    assert all(getattr(DocLine(line_id="1"), name).missing for name in line)
    for kind in ("po", "confirmation", "cart"):
        assert Document(kind=kind).kind == kind
    with pytest.raises(ValidationError):
        Document(kind="invoice")


def test_document_round_trips_through_json():
    src = SourceRef(page=1, line=7, text="KM-1001  3 st  12,50")
    doc = Document(kind="confirmation", source_file="MU01_conf.pdf", pages=1, reader="parser:table_nl",
                   po_reference=DocValue(value="250101", text="250101", source=src),
                   lines=[DocLine(line_id="1", item_code=DocValue(value="KM-1001", text="KM-1001", source=src),
                                  unit_price=DocValue(value="12.50", text="12,50", source=src, uncertain=True))])
    assert Document.model_validate_json(doc.model_dump_json()) == doc


# --- numbers ---

@pytest.mark.parametrize("text,expected", [
    ("1.234.567,89", "1234567.89"), ("1,234,567.89", "1234567.89"),          # either mark can be the thousands mark
    ("1.234,5", "1234.5"), ("1.234,567", "1234.567"), ("1,234.567", "1234.567"),
    ("€ 1.234,50", "1234.50"), ("12,50 €", "12.50"), ("EUR 12.50", "12.50"), ("$12.5", "12.5"), ("£ 1,234.50", "1234.50"),
    ("1234,5", "1234.5"), ("1234.5678", "1234.5678"), ("0,5", "0.5"), ("0.125", "0.125"),
    ("12", "12"), ("0", "0"), ("0,00", "0.00"),                                # zero is a value, and the printed decimals stay
    ("  12,50  ", "12.50"), ("Price: 12,50 each", "12.50"),
    ("1 234,50", "1234.50"), ("1\u00a0234,50", "1234.50"), ("1'234.50", "1234.50"), ("12 345", "12345"),   # a space or apostrophe is a thousands mark
    ("-12,50", "-12.50"), ("€ -12,50", "-12.50"), ("-€ 12,50", "-12.50"), ("-5 %", "-5"),
    ("12-14", "12"), ("KM-12", "12"),                                          # a hyphen after a digit or a letter is no minus sign
    ("€ 12,50 / 100 st", "12.50"), ("12,50 per 100", "12.50"), ("12,50\n/ 100 st", "12.50"),   # the first number is the one read
])
def test_normalize_number_formats(text, expected): assert normalize_number(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("1.234", "1.234"), ("1,234", "1.234"), ("12.345", "12.345"),              # one mark and three digits, no sign of money: a decimal mark
    ("€ 1.234", "1234"), ("1,234 EUR", "1234"), ("$1,234", "1234"), ("EUR 12.345", "12345"),   # a currency mark: a thousands mark
    ("1.234.567", "1234567"), ("1,234,567", "1234567"),                        # a second group: a thousands mark
    ("0.123", "0.123"), ("1.2345", "1.2345"), ("12.34", "12.34"),              # not a group of three: a decimal mark
])
def test_normalize_number_one_mark_followed_by_three_digits(text, expected): assert normalize_number(text) == expected


@pytest.mark.parametrize("text,decimal_comma,expected", [
    ("1.000", True, "1000"), ("1,000", False, "1000"),                         # the convention names the thousands mark ...
    ("1.000", False, "1.000"), ("1,000", True, "1.000"),                       # ... and the decimal mark
    ("1.000,50", True, "1000.50"), ("1,000.50", False, "1000.50"),
    ("12,5", True, "12.5"), ("12.5", False, "12.5"),
    ("12,50", False, "12.50"), ("12.50", True, "12.50"),                       # a mark and two digits at the end is always a decimal mark
    ("12.345", True, "12345"), ("12,345", False, "12345"), ("12.345", False, "12.345"), ("12,345", True, "12.345"),
    ("1.234.567", True, "1234567"), ("1,234,567", False, "1234567"),
    ("1 000", True, "1000"), ("1 000,50", True, "1000.50"), ("1'000.50", False, "1000.50"),   # a space or apostrophe never was ambiguous
    ("€ 1.000", True, "1000"), ("€ 1.000", False, "1.000"),                   # given the convention, a currency sign decides nothing
    ("1.000 st", True, "1000"),
    ("-1.000,50", True, "-1000.50"), ("€ 12,50 / 1.000 st", True, "12.50"),
])
def test_normalize_number_with_the_convention_of_the_document(text, decimal_comma, expected):
    assert normalize_number(text, decimal_comma=decimal_comma) == expected


@pytest.mark.parametrize("text,decimal_comma,expected", [
    ("1,234.50", True, "1234.50"), ("1.234,50", False, "1234.50"),             # both marks: the last one is the decimal mark
    ("1.234.567", False, "1234567"), ("1,234,567", True, "1234567"),           # a mark that repeats is a thousands mark
    ("12,5", False, "12.5"), ("12.5", True, "12.5"), ("1.2345", True, "1.2345"), ("1,2345", False, "1.2345"),   # no grouping explains these
    ("0.123", True, "0.123"), ("0,123", False, "0.123"),
])
def test_a_text_that_only_one_reading_explains_keeps_it_whatever_the_convention(text, decimal_comma, expected):
    assert normalize_number(text, decimal_comma=decimal_comma) == expected


@pytest.mark.parametrize("text,expected", [("1.234", "1.234"), ("€ 1.234", "1234"), ("1,234 EUR", "1234"), ("1.234.567", "1234567"),
                                           ("12,5", "12.5"), ("1.234,50", "1234.50"), ("abc", None)])
def test_decimal_comma_none_is_the_rule_without_a_convention(text, expected):
    assert normalize_number(text, decimal_comma=None) == expected == normalize_number(text)


@pytest.mark.parametrize("text", ["abc", "", "   ", "€", "-", "1.234,56,7", "12.34.56", "1,234,56", "1.234,567.89", "1.23.456"])
def test_normalize_number_refuses_what_it_cannot_read(text): assert normalize_number(text) is None


@pytest.mark.parametrize("decimal_comma", [True, False])
@pytest.mark.parametrize("text", ["abc", "", "   ", "€", "-", "1.234,56,7", "12.34.56", "1,234,56", "1.234,567.89", "1.23.456"])
def test_normalize_number_refuses_the_same_texts_with_a_convention(text, decimal_comma):
    assert normalize_number(text, decimal_comma=decimal_comma) is None


# --- price basis and unit ---

@pytest.mark.parametrize("text,expected", [
    ("pro 100", "100"), ("100", "100"), ("100 st", "100"), ("per 100 Stück", "100"), ("per 10", "10"),
    ("€ 12,50 / 100 st", "100"), ("12,50 per 100", "100"), ("1.234,50 / 100", "100"), ("12,50\n/ 100 st", "100"),   # the price in front is not the basis
    ("€ 13 / 100 st", "100"), ("13 per 1000", "1000"),                          # not even a price that is a whole number
    ("per 1.000", "1000"), ("per 1,000 st", "1000"), ("per 1 000", "1000"),
    ("each", "1"), ("per piece", "1"), ("pro Einheit", "1"), ("per eenheid", "1"), ("€ 12,50 / stuk", "1"), ("per m", "1"), ("per kg", "1"),
])
def test_normalize_basis_other_forms(text, expected): assert normalize_basis(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "per 0", "per 2,5", "12,50", "netto"])
def test_normalize_basis_refuses_what_it_cannot_read(text): assert normalize_basis(text) is None


@pytest.mark.parametrize("text,expected", [
    # A whole number that is not the basis (a price, a minimum, the 2 of m2) must not become the basis:
    # a wrong basis is not None, so it would reach the price comparison as if it had been read.
    ("€ 13 / stuk", "1"), ("40 EUR / pc", "1"), ("EUR 40 each", "1"), ("1,250 / stuk", "1"), ("2,345 EUR per st", "1"),
    ("per m2", "1"), ("€ 25,00 per m2", "1"), ("per 100 m2", "100"), ("per m3", "1"), ("pro Stück ab 5 St.", "1"),
])
def test_normalize_basis_takes_no_number_that_is_not_the_basis(text, expected): assert normalize_basis(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("/100st", "100"), ("12,50/100stk", "100"), ("per 100m", "100"), ("per 100m2", "100"),     # the unit may be glued to the basis
    ("je 100 Stück", "100"), ("à 100", "100"), ("p. 100 st", "100"), ("p.100", "100"), ("PER 100", "100"),   # more ways to say "per"
    ("per 100 / per 1000", "1000"),                                                         # of several, the last counts
    ("m2", "1"), ("100.", "100"), ("100 St.", "100"),
])
def test_normalize_basis_after_a_word_for_per_or_alone(text, expected): assert normalize_basis(text) == expected


@pytest.mark.parametrize("text,expected", [
    # A basis introduced by a preposition is still a basis: without these words the unit word behind the number
    # would turn it into "1", a wrong value that is not None and could hide a real basis difference.
    ("für 100 Stück", "100"), ("Preis für 100 Stück", "100"), ("for 100 pcs", "100"),
    ("voor 100 stuks", "100"), ("zu 100 Stück", "100"), ("bei 100 Stück", "100"),
    ("FÜR 100 STÜCK", "100"), ("Price for 1.000 pcs", "1000"), ("12,50 voor 1000 st", "1000"), ("€ 12,50 zu 100", "100"),
])
def test_normalize_basis_after_a_preposition(text, expected): assert normalize_basis(text) == expected


def test_a_basis_difference_between_the_po_and_the_confirmation_is_not_hidden():
    # both sides used to read "1": the confirmation's "für 100 St." was cut down to its unit word
    assert normalize_basis("12,50 per stuk") == "1"
    assert normalize_basis("12,50 für 100 St.") == "100"


@pytest.mark.parametrize("text", ["super 100", "stop. 100", "dafür 100", "ervoor 100", "dazu 100", "dabei 100", "therefor 100", "pk 100"])
def test_a_word_for_per_counts_only_as_a_whole_word(text):
    # each of these ends in a word for "per" or looks like "p." ("pk"), but is another word
    assert normalize_basis(text) is None


@pytest.mark.parametrize("text", ["40 EUR", "€ 13", "2,5 m", "per 2,5 m", "per 100,00", "x5"])
def test_normalize_basis_refuses_a_number_that_cannot_be_the_basis(text): assert normalize_basis(text) is None


def test_normalize_basis_reads_a_decomposed_umlaut():
    # "Stück" with a combining diaeresis, as a PDF can give it
    assert normalize_basis("pro Stu\u0308ck") == "1"
    assert normalize_basis("100 Stu\u0308ck") == "100"


@pytest.mark.parametrize("text,expected", [
    ("st", "pc"), ("stk", "pc"), ("stuk", "pc"), ("stuks", "pc"), ("pcs", "pc"), ("pc", "pc"), ("piece", "pc"), ("Stück", "pc"),
    ("St.", "pc"), ("PCS", "pc"), ("Stu\u0308ck", "pc"),                      # the last one is "Stück" with a combining diaeresis, as a PDF can give it
    ("m", "m"), ("mtr", "m"), ("meter", "m"), ("M", "m"),
    ("kg", "kg"), ("KG", "kg"), ("l", "l"), ("L", "l"), ("Liter", "l"),
    ("Doos", "doos"), ("  Set ", "set"), ("m2", "m2"), ("100  Stk", "100 stk"),   # anything else: the lower-cased text
])
def test_normalize_unit(text, expected): assert normalize_unit(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "."])
def test_normalize_unit_of_nothing_is_none(text): assert normalize_unit(text) is None


# --- dates ---

@pytest.mark.parametrize("text,expected", [
    ("16-11-2026", "2026-11-16"), ("16.11.2026", "2026-11-16"), ("2026-11-16", "2026-11-16"), ("16/11/2026", "2026-11-16"),
    ("6-1-2026", "2026-01-06"), ("  16.11.2026 ", "2026-11-16"), ("29.02.2028", "2028-02-29"), ("2026/11/16", "2026-11-16"),
    ("03/04/2026", "2026-04-03"),                                              # day first: the third of April
])
def test_normalize_date_numeric_formats(text, expected): assert normalize_date(text, None) == expected


@pytest.mark.parametrize("text", [
    "", "abc", "31.02.2026", "29.02.2027", "13-13-2026", "11/16/2026", "2026-13-01",       # not a date: day first, so 11/16 has no month 16
    "16-11-26", "16.11.", "16-11.2026", "16.11.2026 10:30", "week",                       # two-digit year, no year, mixed marks, a time, no number
    "week 100", "KW 47/26", "2026-W47-2025", "week 47 and more", "47",                    # three-digit week, two-digit year, two years, trailing words, no week word
])
def test_normalize_date_refuses_what_it_cannot_read(text): assert normalize_date(text, date(2026, 11, 3)) is None


@pytest.mark.parametrize("text", ["week 47", "Week 47", "wk 47", "wk47", "WK. 47", "KW 47", "kw47", "KW-47", "CW 47", "cw47", "W47", "w 47"])
def test_week_words(text): assert normalize_date(text, date(2026, 11, 3)) == "2026-W47"


def test_week_numbers_have_two_digits():
    assert normalize_date("week 1", date(2026, 1, 15)) == "2026-W01"
    assert normalize_date("KW 01", date(2026, 1, 15)) == "2026-W01"
    assert normalize_date("kw 9", date(2026, 2, 10)) == "2026-W09"


def test_a_week_more_than_182_days_before_the_document_date_is_next_year():
    # The Monday of week 19 of 2026 is 4 May: exactly 182 days before Monday 2 November, 183 days before Tuesday 3 November.
    assert normalize_date("week 19", date(2026, 11, 2)) == "2026-W19"
    assert normalize_date("week 19", date(2026, 11, 3)) == "2027-W19"
    assert normalize_date("week 18", date(2026, 11, 2)) == "2027-W18"
    assert normalize_date("week 20", date(2026, 11, 3)) == "2026-W20"


def test_a_week_number_needs_a_document_date_or_a_year():
    assert normalize_date("week 47", None) is None
    assert normalize_date("KW 47/2026", None) == "2026-W47"
    assert normalize_date("week 47 2026", None) == "2026-W47"
    assert normalize_date("W47-2026", None) == "2026-W47"
    assert normalize_date("2026-W47", None) == "2026-W47"
    assert normalize_date("2026W47", None) == "2026-W47"
    assert normalize_date("KW 47/2027", date(2026, 11, 3)) == "2027-W47"        # a given year is used as it stands


@pytest.mark.parametrize("text,document_date", [
    ("week 0", date(2026, 11, 3)), ("week 54", date(2026, 11, 3)),
    ("week 53", date(2027, 6, 1)), ("2027-W53", None),                  # 2027 has 52 weeks
])
def test_a_week_that_does_not_exist_is_none(text, document_date): assert normalize_date(text, document_date) is None


def test_week_53_exists_in_a_year_with_53_weeks():
    assert normalize_date("week 53", date(2026, 12, 14)) == "2026-W53"
    assert normalize_date("2026-W53", None) == "2026-W53"


@pytest.mark.parametrize("text,expected", [
    ("16.11.2026 - 20.11.2026", "2026-11-16/2026-11-20"), ("16.11.2026-20.11.2026", "2026-11-16/2026-11-20"),
    ("16.11.2026 \u2013 20.11.2026", "2026-11-16/2026-11-20"), ("16.11.2026 / 20.11.2026", "2026-11-16/2026-11-20"),   # the first one on this line has an en dash
    ("16-11-2026 - 20-11-2026", "2026-11-16/2026-11-20"), ("16-11-2026-20-11-2026", "2026-11-16/2026-11-20"),
    ("2026-11-16 - 2026-11-20", "2026-11-16/2026-11-20"), ("2026-11-16/2026-11-20", "2026-11-16/2026-11-20"),
    ("30.11.2026 - 04.12.2026", "2026-11-30/2026-12-04"), ("16.11.2026 - 16.11.2026", "2026-11-16/2026-11-16"),
])
def test_date_ranges(text, expected): assert normalize_date(text, None) == expected


@pytest.mark.parametrize("text", ["20.11.2026 - 16.11.2026", "16.11.2026 - 31.02.2026", "16.11.2026 -", "16.11.2026 - 20.11", "- 20.11.2026"])
def test_a_range_that_is_not_two_dates_in_order_is_none(text): assert normalize_date(text, None) is None


def test_normal_forms_are_unchanged_by_a_second_pass():
    for text in ("2026-11-16", "2026-W47", "2026-11-16/2026-11-20"):
        assert normalize_date(text, None) == text


# --- currency ---

@pytest.mark.parametrize("text,expected", [
    ("€", "EUR"), ("EUR", "EUR"), ("euro", "EUR"), ("Euro", "EUR"), (" eur ", "EUR"),
    ("$", "USD"), ("USD", "USD"), ("£", "GBP"), ("GBP", "GBP"), ("gbp.", "GBP"),
])
def test_normalize_currency(text, expected): assert normalize_currency(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "12,50", "EUR USD"])
def test_normalize_currency_refuses_what_it_does_not_know(text): assert normalize_currency(text) is None


# --- any text ---

@pytest.mark.parametrize("text", ["\x00", "\n", "\U0001F600", "9" * 5000, "1." * 3000, "kw " * 100, "\u0663\u0662", "-" * 5000])
def test_odd_text_gives_a_value_or_none_never_an_error(text):
    results = [normalize_number(text), normalize_basis(text), normalize_unit(text), normalize_currency(text),
               normalize_date(text, date(2026, 11, 3)), normalize_date(text, None)]
    assert all(result is None or isinstance(result, str) for result in results)


# --- the schema for the AI ---

def objects(node):
    """Every object schema inside a JSON schema, however deep."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for child in node.values():
            yield from objects(child)
    elif isinstance(node, list):
        for child in node:
            yield from objects(child)


def test_every_object_in_the_schema_is_strict():
    schema = document_schema()
    assert set(schema["$defs"]) == {"DocLine", "DocValue", "SourceRef"}
    found = list(objects(schema))
    assert len(found) == 4                                      # Document, DocLine, DocValue, SourceRef
    for obj in found:
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])       # every property is required, in the model's order
    assert schema["required"] == list(Document.model_fields)
    assert schema["$defs"]["DocLine"]["required"] == list(DocLine.model_fields)


def test_the_schema_asks_for_null_not_for_absence():
    schema = document_schema()
    assert '"default"' not in json.dumps(schema)                # every field is required, so a default has no meaning
    value = schema["$defs"]["DocValue"]["properties"]["value"]
    assert {"type": "null"} in value["anyOf"] and {"type": "string"} in value["anyOf"]
    assert {"type": "null"} in schema["$defs"]["DocValue"]["properties"]["source"]["anyOf"]
    assert schema["properties"]["kind"]["enum"] == ["po", "confirmation", "cart"]


def test_the_schema_is_plain_json_and_new_each_time():
    first = document_schema()
    assert json.loads(json.dumps(first)) == first
    first["required"].clear()
    assert document_schema()["required"]
