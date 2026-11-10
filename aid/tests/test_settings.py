import json
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from aid.readers.po_layout import PoLayout
from aid.settings import Settings, SettingsInvalid, load_settings, update_settings_file


def test_example_settings_load(tmp_path):
    s = load_settings(Path("aid/settings.example.json"))
    assert s.price_pct == Decimal("2.0") and s.price_abs == Decimal("25.00")
    assert s.ask_wait_working_days == 3 and s.first_cases_review == 5
    assert s.authorization_limit is None and s.language == "nl"


def test_missing_buyer_email_fails(tmp_path):
    p = tmp_path / "s.json"; p.write_text('{"aid_email": "a@example.com", "approver_email": "b@example.com", "cases_root": ".", "po_store_root": ".", "watched_folder": "."}')
    with pytest.raises(ValueError, match="settings"): load_settings(p)


# The tests below cover what the brief's rulings add to the two above.

ADDRESSES_AND_FOLDERS = {"buyer_email", "aid_email", "approver_email", "cases_root", "po_store_root", "watched_folder"}
PLACEHOLDERS = {"model"}                              # the example names no model: the one default is in settings.py


def test_example_settings_hold_the_defaults():
    """The example file shows every default, so it must not drift from the model."""
    example = load_settings(Path("aid/settings.example.json"))
    defaults = Settings(buyer_email="b@example.com", aid_email="a@example.com", approver_email="p@example.com",
                        cases_root=".", po_store_root=".", watched_folder=".")
    for name in Settings.model_fields.keys() - ADDRESSES_AND_FOLDERS - PLACEHOLDERS:
        assert getattr(example, name) == getattr(defaults, name), name
    assert example.model == "<model id>"


def test_the_example_holds_the_export_settings_with_their_defaults():
    """The two settings of the export's form stand in the example beside export_columns, each with its default: the decimal point, and
    a PO number on every row."""
    values = json.loads(Path("aid/settings.example.json").read_text(encoding="utf-8"))
    names = list(values)
    assert names[names.index("export_columns"):][:3] == ["export_columns", "export_decimal_comma", "export_fill_down"]
    assert (values["export_columns"], values["export_decimal_comma"], values["export_fill_down"]) == ({}, False, False)
    example, fields = load_settings(Path("aid/settings.example.json")), Settings.model_fields
    assert (example.export_decimal_comma, example.export_fill_down) == (False, False)
    assert (fields["export_decimal_comma"].default, fields["export_fill_down"].default) == (False, False)


def test_relative_paths_resolve_against_the_settings_file(tmp_path, monkeypatch):
    drive = tmp_path / "drive"; drive.mkdir()
    elsewhere = tmp_path / "elsewhere"; elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)                      # the working directory must not matter
    inbox = tmp_path / "absolute_inbox"
    p = drive / "settings.json"
    p.write_text(json.dumps({"buyer_email": "b@example.com", "aid_email": "a@example.com", "approver_email": "p@example.com",
                             "cases_root": "cases", "po_store_root": "../store", "watched_folder": str(inbox)}))
    s = load_settings(p)
    assert s.cases_root == drive / "cases"
    assert s.po_store_root == tmp_path / "store"
    assert s.watched_folder == inbox                  # an absolute path stays as it is


def test_a_misspelled_setting_is_rejected(tmp_path):
    values = json.loads(Path("aid/settings.example.json").read_text(encoding="utf-8"))
    values["authorisation_limit"] = "1"               # British spelling: must not be ignored silently
    p = tmp_path / "s.json"; p.write_text(json.dumps(values))
    with pytest.raises(ValueError, match="settings"): load_settings(p)


def test_the_example_names_no_model():
    """A model identifier is kept in one place, the default of settings.py; the example shows a placeholder."""
    assert not re.search(r"claude-[a-z]", Path("aid/settings.example.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("change, value", [({"authorization_limit": "12,345.67"}, "12,345.67"), ({"authorisation_limit": 777777}, "777777")])
def test_a_settings_file_that_fails_names_the_field_and_never_the_value(tmp_path, change, value):
    """A wrong amount or a misspelt key stops the aid with the field and the problem, never the value, which may be the buyer's
    authorization amount: the error is a plain ValueError, without pydantic's error (and its input) as its context."""
    values = json.loads(Path("aid/settings.example.json").read_text(encoding="utf-8")) | change
    p = tmp_path / "s.json"; p.write_text(json.dumps(values))
    with pytest.raises(ValueError) as info:
        load_settings(p)
    error = info.value
    assert type(error) is ValueError and error.__cause__ is None and error.__suppress_context__
    assert next(iter(change)) in str(error) and value not in str(error) and "input_value" not in str(error)


# --- the layout of the PO PDF and the senders of the PO e-mails ---

LAYOUT = {"po_label": "Bestelnummer", "date_label": "Besteldatum", "supplier_line": 3,       # a made-up layout, as the settings hold one
          "title_row": ["Hoeveelheid", "Artikelcode", "Stuksprijs"], "columns": ["quantity", "item_code", "unit_price"],
          "prices_per_piece": True}


def test_the_example_holds_no_layout_and_no_senders_and_a_settings_file_may_hold_both(tmp_path):
    example = json.loads(Path("aid/settings.example.json").read_text(encoding="utf-8"))
    assert example["po_layout"] is None and example["po_senders"] == []
    settings = load_settings(Path("aid/settings.example.json"))
    assert settings.po_layout is None and settings.po_senders == []
    p = tmp_path / "s.json"; p.write_text(json.dumps(example | {"po_layout": LAYOUT, "po_senders": ["inkoop@example.org", "example.com"]}))
    settings = load_settings(p)
    assert settings.po_layout == PoLayout.model_validate(LAYOUT)
    assert settings.po_layout.table_end is None and settings.po_layout.decimal_comma
    assert settings.po_senders == ["inkoop@example.org", "example.com"]


@pytest.mark.parametrize("change, field", [
    ({"title_row": ["Artikelcode"], "columns": ["item_code"]}, "po_layout.title_row"),         # fewer than two words
    ({"columns": ["quantity", "item_code"]}, "po_layout.columns"),                              # one meaning too few
    ({"columns": ["quantity", "description", "unit_price"]}, "po_layout.columns"),              # no item code
    ({"po_label": ""}, "po_layout.po_label"),
    ({"supplier_label": "Crediteur"}, "po_layout.supplier_line"),                               # a label and a line for the supplier
    ({"kolommen": ["quantity"]}, "po_layout.kolommen"),                                         # a key the layout does not know
    ({"title_row": "Hoeveelheid Artikelcode Stuksprijs"}, "po_layout.title_row"),               # a text where a list is asked
    ({"title_row": ["Hoeveelheid", "Artikel code", "Stuksprijs"]}, "po_layout.title_row"),      # two printed words in one entry
])
def test_a_layout_that_does_not_validate_is_named_by_its_field(tmp_path, change, field):
    """A layout at fault stops the aid as any setting does: the error names the field of the layout and its problem, and never a
    word of the layout, which is the company's. A save of such a layout is refused in the same way and leaves the file as it was."""
    example = json.loads(Path("aid/settings.example.json").read_text(encoding="utf-8"))
    p = tmp_path / "s.json"; p.write_text(json.dumps(example | {"po_layout": LAYOUT | change}))
    with pytest.raises(ValueError) as info:
        load_settings(p)
    error = str(info.value)
    assert type(info.value) is ValueError and info.value.__cause__ is None and field in error and "input_value" not in error
    for word in ("Bestelnummer", "Besteldatum", "Hoeveelheid", "Artikelcode", "Artikel code", "Stuksprijs", "Crediteur"):
        assert word not in error, word
    p.write_text(json.dumps(example, indent=2))
    before = p.read_text()
    with pytest.raises(SettingsInvalid) as refused:
        update_settings_file(p, {"po_layout": LAYOUT | change})
    assert field in refused.value.fields and p.read_text() == before
    assert update_settings_file(p, {"po_layout": LAYOUT, "po_senders": ["example.org"]}).po_layout == PoLayout.model_validate(LAYOUT)
    saved = json.loads(p.read_text())
    assert (saved["po_layout"], saved["po_senders"]) == (LAYOUT, ["example.org"]) and list(saved) == list(example)   # each key in its place



@pytest.mark.parametrize("entry", ["", " ", "Inkoop <inkoop@example.org>", "inkoop@example.org,", "inkoop@example.org;erp@example.org",
                                   "inkoop@example.org erp@example.org", "inkoop", "inkoop@", "@", "example", "inkoop@example"])
def test_a_sender_that_is_no_address_or_domain_is_refused_by_its_place(tmp_path, entry):
    """Each entry of po_senders is, after its outer spaces, an e-mail address or a domain (the part after the at sign, with or without
    the at sign). Anything else is refused by its place in the list, po_senders.<n>, and never by what it holds: a blank entry, the
    form a mail program shows (a name with the address in angle brackets), two addresses in one entry, a trailing comma. A save of
    such a list is refused in the same way and leaves the file as it was."""
    example = json.loads(Path("aid/settings.example.json").read_text(encoding="utf-8"))
    p = tmp_path / "s.json"
    p.write_text(json.dumps(example | {"po_senders": ["erp@example.org", entry]}))
    with pytest.raises(ValueError) as info:
        load_settings(p)
    error = str(info.value)
    assert "po_senders.1" in error and "po_senders.0" not in error and "input_value" not in error
    assert not entry.strip() or entry.strip() not in error.split("s.json is not valid: ", 1)[1], error
    p.write_text(json.dumps(example, indent=2))
    before = p.read_text()
    with pytest.raises(SettingsInvalid) as refused:
        update_settings_file(p, {"po_senders": [entry]})
    assert refused.value.fields == ["po_senders.0"] and p.read_text() == before


def test_a_sender_list_of_addresses_and_domains_loads(tmp_path):
    """Addresses and domains load, with their outer spaces taken off; an empty list is any sender."""
    example = json.loads(Path("aid/settings.example.json").read_text(encoding="utf-8"))
    senders = ["Inkoop@Example.ORG", " example.org ", "@example.com", "mail.example.org", "a.b-c@x.example.org"]
    p = tmp_path / "s.json"
    p.write_text(json.dumps(example | {"po_senders": senders}))
    assert load_settings(p).po_senders == ["Inkoop@Example.ORG", "example.org", "@example.com", "mail.example.org", "a.b-c@x.example.org"]


def test_the_settings_load_without_the_pdf_libraries():
    """The layout's model is pydantic only: a process that reads the settings, as the review page's command does first, does not load
    the PDF libraries for it."""
    code = "import sys, aid.settings; print(sorted(name for name in ('pdfplumber', 'pypdfium2', 'reportlab') if name in sys.modules))"
    root = Path(__file__).resolve().parents[2]
    done = subprocess.run([sys.executable, "-B", "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip() == "[]", done.stderr
