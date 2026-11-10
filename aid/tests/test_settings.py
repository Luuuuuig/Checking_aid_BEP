import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from aid.settings import Settings, load_settings


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
