"""Tests of what the settings tab shows and writes for the buyer (aid/review/buyer_settings.py), without a page: the eleven settings
as the form shows them and as a save reads them, which of them the buyer changed, and the suppliers across the four settings that
name them."""
from decimal import Decimal

import pytest

from aid.review.buyer_settings import (BUYER_FIELDS, SUPPLIER_FIELDS, SUPPLIER_PARTS, changed_fields, parse, shown, supplier_changes,
                                       supplier_rows, supplier_shown, supplier_update, with_supplier, without_supplier)
from aid.review.i18n import STRINGS
from aid.settings import Settings
from aid.tests.conftest import SETTINGS

FORM = {"price_pct": "2", "price_abs": "25,00", "lead_time_tolerance_days": "7", "first_cases_review": "5", "ask_wait_working_days": "3",
        "buyer_name": "", "digest": "daily", "digest_time": "16:30", "buyer_email": "buyer@example.com", "language": "nl",
        "approver_email": "approver@example.com"}


def settings(**values) -> Settings:
    return SETTINGS.model_copy(update=values)


def problem(language: str, kind: str) -> str:
    return STRINGS[language][f"settings_problem:{kind}"]


# --- shown ---

def test_the_form_shows_the_eleven_settings_as_text_with_a_decimal_comma_in_dutch():
    s = settings(price_pct=Decimal("2.0"), price_abs=Decimal("25.00"), buyer_name="A. Buyer", digest="per_case", digest_time="07:05")
    nl = shown(s, "nl")
    assert list(nl) == list(BUYER_FIELDS) and len(BUYER_FIELDS) == 11
    assert nl == {"price_pct": "2", "price_abs": "25,00", "lead_time_tolerance_days": "7", "first_cases_review": "0",
                  "ask_wait_working_days": "3", "buyer_name": "A. Buyer", "digest": "per_case", "digest_time": "07:05",
                  "buyer_email": "buyer@example.com", "language": "nl", "approver_email": "approver@example.com"}
    en = shown(s, "en")
    assert (en["price_pct"], en["price_abs"]) == ("2", "25.00") and en["language"] == "nl"      # the setting, not the page's language


@pytest.mark.parametrize("pct, amount, nl, en", [
    ("2.5", "25", ("2,5", "25,00"), ("2.5", "25.00")),
    ("2.50", "12.5", ("2,5", "12,50"), ("2.5", "12.50")),
    ("0", "0", ("0", "0,00"), ("0", "0.00")),
    ("12.125", "7.125", ("12,125", "7,125"), ("12.125", "7.125")),
    ("100", "1000.500", ("100", "1000,50"), ("100", "1000.50")),
])
def test_the_two_price_values_lose_their_trailing_zeros_beyond_two_decimals(pct, amount, nl, en):
    s = settings(price_pct=Decimal(pct), price_abs=Decimal(amount))
    assert (shown(s, "nl")["price_pct"], shown(s, "nl")["price_abs"]) == nl
    assert (shown(s, "en")["price_pct"], shown(s, "en")["price_abs"]) == en


# --- parse ---

def test_a_form_as_shown_parses_to_the_values_to_write():
    values, problems = parse(shown(SETTINGS, "nl"), "nl")
    assert problems == {} and list(values) == list(BUYER_FIELDS)
    assert values == {"price_pct": "2", "price_abs": "25.00", "lead_time_tolerance_days": 7, "first_cases_review": 0,
                      "ask_wait_working_days": 3, "buyer_name": "", "digest": "daily", "digest_time": "16:30",
                      "buyer_email": "buyer@example.com", "language": "nl", "approver_email": "approver@example.com"}
    assert Settings.model_validate(SETTINGS.model_dump() | values).price_pct == Decimal("2")


@pytest.mark.parametrize("text, value", [("2,5", "2.5"), ("2.5", "2.5"), (" 3 ", "3"), ("0", "0"), ("100", "100"), ("99,99", "99.99"),
                                         ("0,5", "0.5")])
def test_a_percentage_takes_a_comma_or_a_point(text, value):
    for language in ("nl", "en"):
        values, problems = parse(FORM | {"price_pct": text}, language)
        assert problems == {} and values["price_pct"] == value


@pytest.mark.parametrize("text", ["100,01", "-1", "", "abc", "2,5,0", "1.234,5", "2 %", "1e2", "NaN", "Infinity", ",5"])
def test_a_percentage_outside_0_to_100_or_not_a_number_is_refused(text):
    for language in ("nl", "en"):
        values, problems = parse(FORM | {"price_pct": text}, language)
        assert problems == {"price_pct": problem(language, "percent")}
    assert problem("nl", "percent") == "een getal van 0 tot 100" and problem("en", "percent") == "a number from 0 to 100"


@pytest.mark.parametrize("text, value", [("25,00", "25.00"), ("25.00", "25.00"), ("0", "0"), ("1000000", "1000000"), ("12,5", "12.5")])
def test_an_amount_is_0_or_more_with_a_comma_or_a_point(text, value):
    values, problems = parse(FORM | {"price_abs": text}, "nl")
    assert problems == {} and values["price_abs"] == value
    values, problems = parse(FORM | {"price_abs": "-0,01"}, "en")
    assert problems == {"price_abs": problem("en", "amount")}


@pytest.mark.parametrize("text", ["1.000", "10.000", "1,000", "2,500", "0.125", "25,000", "100.000"])
def test_a_percentage_and_an_amount_take_at_most_two_decimals_so_that_a_thousand_is_not_read_as_one(text):
    """"1.000" is how a Dutch buyer writes a thousand: read with the point as the decimal mark it would be saved as 1."""
    for language in ("nl", "en"):
        values, problems = parse(FORM | {"price_pct": text, "price_abs": text}, language)
        assert problems == {"price_pct": problem(language, "decimals"), "price_abs": problem(language, "decimals")}
        assert "price_pct" not in values and "price_abs" not in values
    assert problem("nl", "decimals") == "hoogstens twee decimalen (schrijf duizend als 1000, zonder punt of komma)"
    assert problem("en", "decimals") == "at most two decimals (write a thousand as 1000, without a point or a comma)"
    values, problems = parse(FORM | {"price_pct": "2,50", "price_abs": "1000"}, "nl")
    assert problems == {} and (values["price_pct"], values["price_abs"]) == ("2.50", "1000")


def test_only_the_fields_named_are_read():
    """A save reads the fields the buyer changed and no other: a field it does not name is neither a value nor a problem."""
    form = FORM | {"price_pct": "3,5", "digest_time": "25:00", "buyer_email": "no-address"}
    assert parse(form, "nl", only=["price_pct"]) == ({"price_pct": "3.5"}, {})
    assert parse(form, "nl", only=["digest_time", "price_pct"]) == ({"price_pct": "3.5"}, {"digest_time": problem("nl", "time")})
    assert parse(form, "nl", only=[]) == ({}, {})
    assert list(parse(FORM, "nl", only=["approver_email", "price_abs"])[0]) == ["price_abs", "approver_email"]      # in the order of the form


@pytest.mark.parametrize("field, low, high, kind", [("lead_time_tolerance_days", 0, 365, "days"), ("first_cases_review", 0, 1000, "cases"),
                                                    ("ask_wait_working_days", 1, 60, "wait")])
def test_the_whole_numbers_keep_to_their_ranges(field, low, high, kind):
    for text in (str(low), str(high), f" {low + 1} "):
        values, problems = parse(FORM | {field: text}, "nl")
        assert problems == {} and values[field] == int(text)
    for text in (str(low - 1), str(high + 1), "2,5", "2.0", "", "zeven"):
        values, problems = parse(FORM | {field: text}, "nl")
        assert problems == {field: problem("nl", kind)}
    assert problem("nl", "days") == "een heel getal van 0 tot 365" and problem("en", "wait") == "a whole number from 1 to 60"


def test_the_digest_is_daily_or_per_case_and_its_time_hh_mm():
    assert parse(FORM | {"digest": "per_case"}, "nl")[0]["digest"] == "per_case"
    assert parse(FORM | {"digest": "weekly"}, "nl")[1] == {"digest": problem("nl", "digest")}
    for text, value in (("9:05", "09:05"), ("00:00", "00:00"), ("23:59", "23:59"), (" 16:30 ", "16:30")):
        assert parse(FORM | {"digest_time": text}, "nl")[0]["digest_time"] == value
    for text in ("24:00", "12:60", "1630", "16.30", "", "16:3", "-1:00", "16:30:00"):
        assert parse(FORM | {"digest_time": text}, "en")[1] == {"digest_time": problem("en", "time")}


def test_the_two_addresses_follow_the_address_rule():
    values, problems = parse(FORM | {"buyer_email": " orders@example.org ", "approver_email": "boss@sub.example.com"}, "nl")
    assert problems == {} and (values["buyer_email"], values["approver_email"]) == ("orders@example.org", "boss@sub.example.com")
    for text in ("", "buyer", "buyer@example", "a b@example.com", "a@b@example.com"):
        values, problems = parse(FORM | {"buyer_email": text, "approver_email": text}, "nl")
        assert problems == {"buyer_email": problem("nl", "address"), "approver_email": problem("nl", "address")}


def test_the_name_is_one_line_of_at_most_80_characters_and_may_be_empty():
    assert parse(FORM | {"buyer_name": " A. Buyer "}, "nl")[0]["buyer_name"] == "A. Buyer"
    assert parse(FORM | {"buyer_name": "  "}, "nl")[0]["buyer_name"] == ""
    assert parse(FORM | {"buyer_name": "x" * 80}, "nl")[1] == {}
    for text in ("x" * 81, "A.\nBuyer", "A.\rBuyer"):
        assert parse(FORM | {"buyer_name": text}, "nl")[1] == {"buyer_name": problem("nl", "name")}


def test_the_language_is_dutch_or_english():
    assert parse(FORM | {"language": "en"}, "nl")[0]["language"] == "en"
    assert parse(FORM | {"language": "de"}, "nl")[1] == {"language": problem("nl", "language")}


def test_every_refused_field_has_its_problem_and_a_missing_field_counts_as_empty():
    form = FORM | {"price_pct": "x", "digest_time": "25:00"}
    del form["buyer_email"]
    values, problems = parse(form, "en")
    assert problems == {"price_pct": problem("en", "percent"), "digest_time": problem("en", "time"), "buyer_email": problem("en", "address")}
    assert parse({key: None for key in BUYER_FIELDS} | {"buyer_name": None}, "nl")[1].keys() == set(BUYER_FIELDS) - {"buyer_name"}


def test_the_changed_fields_are_those_whose_text_differs_from_what_the_form_was_filled_with():
    filled = shown(SETTINGS, "nl")
    assert changed_fields(dict(filled), filled) == []
    form = filled | {"price_pct": " 2 ", "buyer_name": "A. Buyer", "digest": "per_case", "approver_email": "boss@example.com"}
    assert changed_fields(form, filled) == ["buyer_name", "digest", "approver_email"]          # a space around a text is no change
    assert changed_fields(filled | {"price_abs": "25"}, filled) == ["price_abs"]                # "25" for "25,00": the text differs
    assert changed_fields(filled | {"digest": None}, filled) == ["digest"] and changed_fields({}, filled) == list(BUYER_FIELDS)[:5] + list(BUYER_FIELDS)[6:]
    assert changed_fields({"buyer_name": "x"}, {}) == ["buyer_name"]                             # a field never filled counts as empty


def test_every_problem_is_in_both_languages():
    kinds = {key for key in STRINGS["nl"] if key.startswith("settings_problem:")}
    assert kinds == {key for key in STRINGS["en"] if key.startswith("settings_problem:")} and len(kinds) >= 10


# --- the suppliers ---

SUPPLIERS = settings(supplier_emails={"S02": "sales@s02.example.com", "acme": "orders@acme.example.org", "Gamma": "info@gamma.example.com"},
                     supplier_lead_times={"S02": 10, "Zeta": 0}, review_suppliers=["Beta", "S02"], messages_off_suppliers=["acme", "Delta"])


def test_the_suppliers_are_every_name_of_the_four_settings_sorted_without_regard_to_case():
    assert SUPPLIER_FIELDS == ("supplier_emails", "supplier_lead_times", "review_suppliers", "messages_off_suppliers")
    assert supplier_rows(SUPPLIERS) == [["acme", "orders@acme.example.org", "", False, True], ["Beta", "", "", True, False],
                                        ["Delta", "", "", False, True], ["Gamma", "info@gamma.example.com", "", False, False],
                                        ["S02", "sales@s02.example.com", 10, True, False], ["Zeta", "", 0, False, False]]
    for field in SUPPLIER_FIELDS:                              # a supplier named in one setting only is listed, whichever setting it is
        empty = {other: type(getattr(SUPPLIERS, other))() for other in SUPPLIER_FIELDS}
        only = {"supplier_emails": {"Only": "only@example.com"}, "supplier_lead_times": {"Only": 0}}.get(field, ["Only"])
        assert [row[0] for row in supplier_rows(settings(**(empty | {field: only})))] == ["Only"], field
    assert supplier_rows(settings(supplier_emails={}, supplier_lead_times={}, review_suppliers=[], messages_off_suppliers=[])) == []


def test_a_new_supplier_is_added_to_the_settings_its_values_name():
    values, problems = with_supplier(SUPPLIERS, " New Supplier BV ", " info@new.example.com ", "14", True, False)
    assert problems == {} and list(values) == list(SUPPLIER_FIELDS)
    assert values["supplier_emails"] == SUPPLIERS.supplier_emails | {"New Supplier BV": "info@new.example.com"}
    assert list(values["supplier_emails"])[-1] == "New Supplier BV"                            # a new one at the end
    assert values["supplier_lead_times"] == {"S02": 10, "Zeta": 0, "New Supplier BV": 14}
    assert values["review_suppliers"] == ["Beta", "S02", "New Supplier BV"] and values["messages_off_suppliers"] == ["acme", "Delta"]
    assert list(SUPPLIERS.supplier_emails) == ["S02", "acme", "Gamma"] and SUPPLIERS.review_suppliers == ["Beta", "S02"]      # nothing changed in place


def test_a_supplier_is_changed_in_its_place_and_an_empty_value_or_a_tick_off_takes_it_out():
    values, problems = with_supplier(SUPPLIERS, "S02", "", "", False, True)
    assert problems == {}
    assert values == {"supplier_emails": {"acme": "orders@acme.example.org", "Gamma": "info@gamma.example.com"}, "supplier_lead_times": {"Zeta": 0},
                      "review_suppliers": ["Beta"], "messages_off_suppliers": ["acme", "Delta", "S02"]}
    values, problems = with_supplier(SUPPLIERS, "acme", "new@acme.example.org", 3, False, True)
    assert list(values["supplier_emails"].items()) == [("S02", "sales@s02.example.com"), ("acme", "new@acme.example.org"),
                                                       ("Gamma", "info@gamma.example.com")]
    assert values["supplier_lead_times"]["acme"] == 3 and values["messages_off_suppliers"] == ["acme", "Delta"]
    values, problems = with_supplier(SUPPLIERS, "acme", "orders@acme.example.org", "", False, False)     # the tick "no e-mails" taken off
    assert problems == {} and values["messages_off_suppliers"] == ["Delta"] and values["supplier_emails"] == SUPPLIERS.supplier_emails
    values, problems = with_supplier(SUPPLIERS, "S02", "sales@s02.example.com", "10", False, False)       # the tick "always to you" taken off
    assert problems == {} and values["review_suppliers"] == ["Beta"]


def test_a_lead_time_of_0_days_and_a_name_of_80_characters_are_taken():
    values, problems = with_supplier(SUPPLIERS, "S09", "", "0", False, False)
    assert problems == {} and values["supplier_lead_times"]["S09"] == 0
    assert ["S09", "", 0, False, False] in supplier_rows(SUPPLIERS.model_copy(update=values))            # listed, its lead time 0 and not empty
    assert supplier_shown(SUPPLIERS.model_copy(update=values), "S09")["lead_time"] == "0"
    name = "N" * 80
    values, problems = with_supplier(SUPPLIERS, f" {name} ", "", "1", False, False)
    assert problems == {} and values["supplier_lead_times"][name] == 1
    assert with_supplier(SUPPLIERS, "N" * 81, "", "1", False, False)[1] == {"supplier_name": problem("nl", "supplier_name")}


def test_a_supplier_left_without_any_value_is_in_none_of_the_four():
    values, problems = with_supplier(SUPPLIERS, "Beta", "", None, False, False)
    assert problems == {} and all("Beta" not in values[field] for field in SUPPLIER_FIELDS)
    assert "Beta" not in [row[0] for row in supplier_rows(SUPPLIERS.model_copy(update=values))]


def test_names_are_compared_exactly_as_written():
    values, problems = with_supplier(SUPPLIERS, "s02", "", "", False, False)
    assert problems == {} and values["supplier_emails"]["S02"] == "sales@s02.example.com" and "s02" not in values["supplier_lead_times"]
    assert values["review_suppliers"] == ["Beta", "S02"]                                       # "s02" with its tick off leaves "S02" marked
    values, problems = with_supplier(SUPPLIERS, "s02", "other@s02.example.org", "", True, True)
    assert problems == {} and values["supplier_emails"]["S02"] == "sales@s02.example.com" and values["supplier_emails"]["s02"] == "other@s02.example.org"
    assert values["review_suppliers"] == ["Beta", "S02", "s02"] and values["messages_off_suppliers"] == ["acme", "Delta", "s02"]


def test_a_listed_name_is_used_as_the_settings_have_it_and_only_a_typed_name_is_trimmed():
    """A name kept by hand with a space or a tab around it is that supplier's name: the dropdown gives it as the file has it."""
    odd = settings(supplier_emails={}, supplier_lead_times={" Spaced ": 5, "Tab\t": 3}, review_suppliers=[" Spaced "], messages_off_suppliers=[])
    values, problems = with_supplier(odd, " Spaced ", "a@spaced.example.com", "6", False, False)
    assert problems == {} and values == {"supplier_emails": {" Spaced ": "a@spaced.example.com"}, "supplier_lead_times": {" Spaced ": 6, "Tab\t": 3},
                                         "review_suppliers": [], "messages_off_suppliers": []}
    assert without_supplier(odd, " Spaced ") == {"supplier_emails": {}, "supplier_lead_times": {"Tab\t": 3}, "review_suppliers": [],
                                                 "messages_off_suppliers": []}
    assert without_supplier(odd, "Tab\t")["supplier_lead_times"] == {" Spaced ": 5}
    values, problems = with_supplier(odd, " Typed BV ", "", "2", False, False)                 # not listed: a typed name, trimmed
    assert problems == {} and list(values["supplier_lead_times"]) == [" Spaced ", "Tab\t", "Typed BV"]
    values, problems = with_supplier(odd, "Spaced", "", "2", False, False)                     # another supplier than " Spaced "
    assert problems == {} and values["supplier_lead_times"] == {" Spaced ": 5, "Tab\t": 3, "Spaced": 2}
    long_listed = settings(supplier_emails={}, supplier_lead_times={"L" * 90: 5}, review_suppliers=[], messages_off_suppliers=[])
    assert with_supplier(long_listed, "L" * 90, "", "6", False, False) == ({"supplier_emails": {}, "supplier_lead_times": {"L" * 90: 6},
                                                                            "review_suppliers": [], "messages_off_suppliers": []}, {})


def test_the_supplier_form_shows_a_supplier_and_a_save_changes_the_parts_the_buyer_changed():
    assert SUPPLIER_PARTS == ("email", "lead_time", "review", "off")
    assert supplier_shown(SUPPLIERS, "S02") == {"email": "sales@s02.example.com", "lead_time": "10", "review": True, "off": False}
    assert supplier_shown(SUPPLIERS, "Delta") == {"email": "", "lead_time": "", "review": False, "off": True}
    assert supplier_shown(SUPPLIERS, "not listed") == {"email": "", "lead_time": "", "review": False, "off": False}
    filled = supplier_shown(SUPPLIERS, "S02")
    assert supplier_changes(dict(filled), filled) == {}
    assert supplier_changes(filled | {"email": " sales@s02.example.com ", "lead_time": "12", "off": True}, filled) == {"lead_time": "12", "off": True}
    assert supplier_changes({"email": "", "lead_time": None, "review": False, "off": False}, supplier_shown(SUPPLIERS, "not listed")) == {}
    # the file changed meanwhile: S02 has another address and another lead time; the buyer only set the tick "no e-mails"
    meanwhile = SUPPLIERS.model_copy(update={"supplier_emails": SUPPLIERS.supplier_emails | {"S02": "new@s02.example.org"},
                                             "supplier_lead_times": {"S02": 99, "Zeta": 0}})
    values, problems = supplier_update(meanwhile, "S02", {"off": True})
    assert problems == {} and values["supplier_emails"]["S02"] == "new@s02.example.org" and values["supplier_lead_times"]["S02"] == 99
    assert values["review_suppliers"] == ["Beta", "S02"] and values["messages_off_suppliers"] == ["acme", "Delta", "S02"]
    # a part the buyer did not change is not read: an address the file holds stays, whatever it looks like
    kept = SUPPLIERS.model_copy(update={"supplier_emails": {"S02": "no address"}})
    assert supplier_update(kept, "S02", {"lead_time": "11"}) == ({"supplier_emails": {"S02": "no address"}, "supplier_lead_times": {"S02": 11, "Zeta": 0},
                                                                 "review_suppliers": ["Beta", "S02"], "messages_off_suppliers": ["acme", "Delta"]}, {})
    assert supplier_update(kept, "S02", {"email": "still no address", "lead_time": "x"}, "en")[1] == {
        "supplier_email": problem("en", "address"), "supplier_lead_time": problem("en", "days")}
    assert supplier_update(SUPPLIERS, "S02", {}) == ({field: getattr(SUPPLIERS, field) for field in SUPPLIER_FIELDS}, {})


@pytest.mark.parametrize("name, email, lead, refused", [
    ("", "a@example.com", "1", {"supplier_name": "supplier_name"}),
    ("  ", "", "", {"supplier_name": "supplier_name"}),
    ("x" * 81, "", "", {"supplier_name": "supplier_name"}),
    ("S09", "not-an-address", "", {"supplier_email": "address"}),
    ("S09", "", "366", {"supplier_lead_time": "days"}),
    ("S09", "", "-1", {"supplier_lead_time": "days"}),
    ("S09", "", "2,5", {"supplier_lead_time": "days"}),
    ("S09", "x@", "zeven", {"supplier_email": "address", "supplier_lead_time": "days"}),
])
def test_a_supplier_value_that_is_not_valid_is_refused_by_its_field(name, email, lead, refused):
    values, problems = with_supplier(SUPPLIERS, name, email, lead, False, False)
    assert problems == {field: problem("nl", kind) for field, kind in refused.items()}
    assert parse(FORM, "en")[1] == {} and with_supplier(SUPPLIERS, "S09", "", "365", False, False)[1] == {}


def test_the_problems_of_a_supplier_follow_the_language():
    assert with_supplier(SUPPLIERS, "", "", "", False, False, language="en")[1] == {"supplier_name": problem("en", "supplier_name")}


def test_a_supplier_is_removed_from_all_four():
    values = without_supplier(SUPPLIERS, " S02 ")
    assert values == {"supplier_emails": {"acme": "orders@acme.example.org", "Gamma": "info@gamma.example.com"}, "supplier_lead_times": {"Zeta": 0},
                      "review_suppliers": ["Beta"], "messages_off_suppliers": ["acme", "Delta"]}
    assert without_supplier(SUPPLIERS, "Delta")["messages_off_suppliers"] == ["acme"]
    assert without_supplier(SUPPLIERS, "unknown") == {field: getattr(SUPPLIERS, field) for field in SUPPLIER_FIELDS}
