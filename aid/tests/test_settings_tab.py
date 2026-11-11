"""Tests of the settings tab of the review page (aid/review/page.py, its strings in aid/review/i18n.py) and of the six settings it
writes (aid/settings.py). The sign-in is a fake msal application and Microsoft Graph is FakeSession (aid/tests/fakes.py): no test
opens a browser or reaches the network."""
import hashlib
import json
import os
import re
import shutil
import stat
from datetime import datetime
from decimal import Decimal
from email import message_from_bytes, policy
from pathlib import Path

import pytest
import requests

from aid.connections.microsoft import COPILOT_SCOPES, MAIL_SCOPES, SHARED_SCOPES, MicrosoftAccount
from aid.connections.secrets import SecretStore
from aid.execution.by_hand import mark, open_changes
from aid.madeup.generate import TRYOUT_PO_LAYOUT, Row, Text, pdf_bytes, tryout_po_pages
from aid.po_store import PoStore
from aid.readers.po_export import read_export
from aid.review.__main__ import company_values, refuse_share
from aid.review.buyer_settings import BUYER_FIELDS, SUPPLIER_FIELDS, supplier_rows
from aid.review.exact_settings import FORM as EXACT_FORM
from aid.review.exact_settings import (EXPORT_FORM, MEANINGS, NO_COLUMN, SLOTS, export_headers, field_label, line_headers, page_lines,
                                       table_head)
from aid.review.i18n import STRINGS
from aid.review.page import NEW_SUPPLIER, build_app, import_gradio
from aid.settings import Settings, SettingsInvalid, is_address, load_settings, read_settings, update_settings_file
from aid.sources.mailbox import NOT_AN_EXPORT, scan_folder
from aid.tests.conftest import SETTINGS, ReachedMicrosoft, deps
from aid.tests.fakes import (ACCESS_TOKEN, CLIENT_ID, COPILOT, GRAPH, TENANT_ID, USERNAME, FakeApp, FakeResponse, FakeSession,
                             copilot_answer, copilot_session, sent_text, sign_in_event)
from aid.tests.helpers import (ANDERS_FORM, ANDERS_LAYOUT, ANDERS_MEANINGS, ANDERS_PO, COMMAS_TITLES, ERP_FORM, ERP_MEANINGS, EXPORT_COLUMNS,
                               EXPORT_NL, EXPORT_NL_COLUMNS, EXPORT_NL_FORM, EXPORT_NL_ORDERS, EXPORT_NL_READ, EXPORT_NL_READ_EN,
                               EXPORT_NL_TITLES, MADEUP_LAYOUT, SAMPLES, SORTED_BY_DATE, SORTED_BY_DATE_COLUMNS, SORTED_BY_DATE_LONG, TABS_TITLES, TIE_TITLES,
                               TITLE_COMMAS, WIDE_TITLES, write_cut_samples, write_sheet, write_title_samples)
from aid.tryout import CHANGES, confirmation_pdf

FOLDER = "AAMkADMadeUpFolder0001="
SIX = {"ms_tenant_id": TENANT_ID, "ms_client_id": CLIENT_ID, "mailbox": None, "mail_folder": FOLDER, "mail_folder_name": "Orderbevestigingen",
       "send_via": "auto"}
FOLDERS = FakeResponse(json_data={"value": [{"id": "AAMkADInbox=", "displayName": "Postvak IN", "totalItemCount": 40},
                                            {"id": FOLDER, "displayName": "Orderbevestigingen", "totalItemCount": 12}]})


def settings_file(tmp_path: Path, **values) -> Path:
    """A made-up settings file as an operator keeps one: its own order of the keys, with relative folders."""
    data = {"language": "nl", "buyer_email": "buyer@example.com", "aid_email": "aid@example.com", "approver_email": "approver@example.com",
            "cases_root": "cases", "po_store_root": "po_store", "watched_folder": "inbox", "reader": "parser", "price_pct": "2.0",
            "supplier_lead_times": {"S01": 10}} | values
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    return path


def local(utc: str) -> str:
    """A time of Graph or of the state as the tab shows it: in the local time of the PC."""
    return datetime.fromisoformat(utc).astimezone().strftime("%Y-%m-%d %H:%M")


# --- the settings and their file ---

def test_saving_writes_the_six_settings_and_keeps_every_other_key_in_its_place(tmp_path, monkeypatch):
    path = settings_file(tmp_path)
    before = json.loads(path.read_text(encoding="utf-8"))
    renames, rename = [], os.replace
    monkeypatch.setattr(os, "replace", lambda source, target: (renames.append((Path(source).name, Path(target).name)), rename(source, target))[1])
    saved = update_settings_file(path, SIX)
    after = json.loads(path.read_text(encoding="utf-8"))
    assert list(after) == list(before) + list(SIX) and {key: after[key] for key in before} == before
    assert {key: after[key] for key in SIX} == SIX and saved == load_settings(path)
    [(temporary, target)] = renames                           # one rename, from a temporary file with a name of its own beside the file
    assert target == "settings.json" and temporary.startswith(".settings.json.") and temporary.endswith(".tmp")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]
    assert path.read_text(encoding="utf-8").startswith('{\n    "language"') and path.read_text(encoding="utf-8").endswith("}")
    changed = update_settings_file(path, SIX | {"mailbox": "orders@example.com", "send_via": "file"})
    again = json.loads(path.read_text(encoding="utf-8"))
    assert list(again) == list(after) and (again["mailbox"], again["send_via"]) == ("orders@example.com", "file")
    assert (changed.mailbox, changed.cases_root) == ("orders@example.com", tmp_path / "cases")


def test_the_example_settings_hold_the_six_empty_and_saving_keeps_their_places(tmp_path):
    example = json.loads(Path("aid/settings.example.json").read_text(encoding="utf-8"))
    assert {key: example[key] for key in SIX} == {key: None for key in SIX if key != "send_via"} | {"send_via": "auto"}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(example | {"model": "made-up-model"}, indent=2), encoding="utf-8")
    update_settings_file(path, SIX)
    assert list(json.loads(path.read_text(encoding="utf-8"))) == list(example)


@pytest.mark.skipif(os.name == "nt", reason="Windows keeps a file's access in its ACL, which a rename does not widen")
def test_saving_keeps_a_settings_file_that_only_its_owner_can_read_so(tmp_path):
    path = settings_file(tmp_path)
    path.chmod(0o600)
    update_settings_file(path, SIX)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    compact = tmp_path / "compact.json"
    compact.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8"))) + "\n", encoding="utf-8")
    update_settings_file(compact, SIX | {"send_via": "file"})
    assert len(compact.read_text(encoding="utf-8").splitlines()) == 1             # a file on one line stays on one line


@pytest.mark.parametrize("field, value", [("ms_client_id", "not-a-guid-xyz"), ("ms_tenant_id", "not a domain xyz"),
                                          ("mailbox", "no-address-xyz"), ("send_via", "pigeon-xyz")])
def test_a_value_that_is_not_valid_is_refused_by_its_field_and_the_file_is_left_as_it_was(tmp_path, field, value):
    path = settings_file(tmp_path)
    before = path.read_bytes()
    with pytest.raises(SettingsInvalid) as raised:
        update_settings_file(path, SIX | {field: value})
    assert field in str(raised.value) and value not in str(raised.value) and raised.value.fields == [field]
    assert path.read_bytes() == before and sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]


HAND_KEPT = ('{\r\n\t"language": "nl",\r\n\t"buyer_email": "buyer@example.com", "aid_email": "aid@example.com",\r\n'
             '\t"approver_email": "approver@example.com",\r\n\t"authorization_limit": 5000.00,\r\n'
             '\t"cases_root": "cases", "po_store_root": "po_store", "watched_folder": "inbox",\r\n\t"reader": "parser",\r\n'
             '\t"price_pct": "2.0",\r\n\t"review_suppliers": ["Caf\\u00e9 BV", "S02"],\r\n\t"smtp_port":587,\r\n'
             '\t"supplier_lead_times": {"S01": 10,   "S02": 12}\r\n}\r\n')


def test_a_save_replaces_the_text_of_the_changed_keys_only_and_every_other_key_keeps_its_bytes(tmp_path):
    """A file kept by hand: tabs, CRLF, several keys on a line, a number with trailing zeros, an escaped letter, no space after a
    colon. A save changes the value of its own keys where they stand and nothing else, byte for byte."""
    path = tmp_path / "settings.json"
    path.write_bytes(HAND_KEPT.encode("utf-8"))
    saved = update_settings_file(path, {"price_pct": "3.5", "aid_email": "aid@example.org"})
    expected = HAND_KEPT.replace('"price_pct": "2.0"', '"price_pct": "3.5"').replace('"aid@example.com"', '"aid@example.org"')
    assert path.read_bytes() == expected.encode("utf-8")
    assert (saved.price_pct, saved.aid_email, saved.authorization_limit) == (Decimal("3.5"), "aid@example.org", Decimal("5000"))
    update_settings_file(path, {"supplier_lead_times": {"S01": 10, "S02": 12, "S03": 3}, "buyer_name": "A. Buyer", "digest": "per_case"})
    again = path.read_bytes().decode("utf-8")
    assert again == expected.replace('{"S01": 10,   "S02": 12}', '{\r\n\t\t"S01": 10,\r\n\t\t"S02": 12,\r\n\t\t"S03": 3\r\n\t}').replace(
        '\r\n}\r\n', ',\r\n\t"buyer_name": "A. Buyer",\r\n\t"digest": "per_case"\r\n}\r\n')           # a new key at the end, the file's own indentation
    assert json.loads(again)["review_suppliers"] == ["Café BV", "S02"] and '"Caf\\u00e9 BV"' in again and '"smtp_port":587' in again


def test_a_save_keeps_a_byte_order_mark_and_a_file_on_one_line(tmp_path):
    path = settings_file(tmp_path)
    one_line = json.dumps(json.loads(path.read_text(encoding="utf-8")))
    path.write_bytes(b"\xef\xbb\xbf" + one_line.encode("utf-8"))
    update_settings_file(path, {"price_pct": "4", "digest_time": "08:00"})
    assert path.read_bytes() == b"\xef\xbb\xbf" + (one_line.replace('"price_pct": "2.0"', '"price_pct": "4"')[:-1]
                                                   + ', "digest_time": "08:00"}').encode("utf-8")
    assert load_settings(path).digest_time == "08:00"


def test_a_save_whose_values_the_file_already_holds_writes_nothing(tmp_path, monkeypatch):
    path = settings_file(tmp_path, **SIX)
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    before, renames = path.read_bytes(), []
    monkeypatch.setattr(os, "replace", lambda source, target: renames.append(target))
    saved = update_settings_file(path, SIX | {"price_pct": "2.0", "supplier_lead_times": {"S01": 10}})
    assert saved == load_settings(path) and renames == []
    assert path.read_bytes() == before and path.stat().st_mtime_ns == 1_700_000_000_000_000_000
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]
    with pytest.raises(SettingsInvalid):                       # the file itself is still validated
        path.write_text(json.dumps(json.loads(before) | {"send_via": "pigeon-xyz"}), encoding="utf-8")
        update_settings_file(path, {})


def test_the_values_of_a_save_can_be_built_from_the_file_as_it_is_read(tmp_path):
    path = settings_file(tmp_path)
    seen = []

    def one_more(settings: Settings) -> dict:
        seen.append(settings)
        return {"supplier_lead_times": settings.supplier_lead_times | {"S02": 12}}

    saved = update_settings_file(path, one_more)
    assert len(seen) == 1 and seen[0].supplier_lead_times == {"S01": 10} and seen[0].cases_root == tmp_path / "cases"
    assert saved.supplier_lead_times == {"S01": 10, "S02": 12} == json.loads(path.read_text(encoding="utf-8"))["supplier_lead_times"]
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")) | {"smtp_port": "no-port-xyz"}, indent=4), encoding="utf-8")
    before, called = path.read_bytes(), []
    with pytest.raises(SettingsInvalid) as raised:             # a file that does not validate as it is: the values are not built
        update_settings_file(path, lambda settings: called.append(settings) or {"price_pct": "3"})
    assert raised.value.fields == ["smtp_port"] and "no-port-xyz" not in str(raised.value) and called == []
    assert path.read_bytes() == before


def test_two_saves_at_the_same_moment_both_land_and_the_file_stays_json(tmp_path):
    """Each save builds its values from the file as it is read inside the lock of update_settings_file, so neither is lost."""
    import threading
    path = settings_file(tmp_path)
    errors = []

    def add(name: str, gate: threading.Barrier) -> None:
        try:
            gate.wait()
            update_settings_file(path, lambda settings: {"supplier_lead_times": settings.supplier_lead_times | {name: 5}})
        except Exception as error:                              # a failed save fails the test below
            errors.append(error)

    for round_ in range(40):
        gate = threading.Barrier(4)
        threads = [threading.Thread(target=add, args=(f"R{round_}-{n}", gate)) for n in range(4)]
        [thread.start() for thread in threads]
        [thread.join() for thread in threads]
        held = json.loads(path.read_text(encoding="utf-8"))["supplier_lead_times"]
        assert errors == [] and {f"R{round_}-{n}" for n in range(4)} <= set(held), round_
    assert len(load_settings(path).supplier_lead_times) == 161 and sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]


def test_a_write_that_fails_leaves_the_file_as_it_was_and_no_temporary_file(tmp_path, monkeypatch):
    path = settings_file(tmp_path)
    before, names, rename = path.read_bytes(), [], os.replace

    def refused(source, target):
        names.append(Path(source).name)
        raise PermissionError("made-up: the file is open in another program")

    monkeypatch.setattr(os, "replace", refused)
    for _ in range(2):
        with pytest.raises(PermissionError):
            update_settings_file(path, {"price_pct": "3"})
    assert path.read_bytes() == before and sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]
    monkeypatch.setattr(os, "replace", lambda source, target: (names.append(Path(source).name), rename(source, target))[1])
    update_settings_file(path, {"price_pct": "3"})
    assert len(set(names)) == 3 and all(name.startswith(".settings.json.") and name.endswith(".tmp") for name in names)


def test_read_settings_names_the_fields_of_a_file_that_does_not_validate(tmp_path):
    path = settings_file(tmp_path)
    assert read_settings(path) == load_settings(path)
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")) | {"price_pct": "secret-xyz", "smtp_port": "x"}), encoding="utf-8")
    with pytest.raises(SettingsInvalid) as raised:
        read_settings(path)
    assert raised.value.fields == ["price_pct", "smtp_port"] and "secret-xyz" not in str(raised.value)
    with pytest.raises(ValueError) as plain:                    # load_settings gives the same text as a plain ValueError
        load_settings(path)
    assert type(plain.value) is ValueError and str(plain.value) == str(raised.value)
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(SettingsInvalid) as raised:
        read_settings(path)
    assert raised.value.fields == ["the file"]
    path.write_text("{ no json", encoding="utf-8")
    with pytest.raises(ValueError):
        read_settings(path)
    with pytest.raises(OSError):
        read_settings(tmp_path / "missing.json")


def test_the_new_settings_are_checked_and_an_empty_one_is_none():
    def valid(**values) -> Settings:
        return Settings.model_validate(json.loads(SETTINGS.model_dump_json()) | values)
    assert valid(ms_tenant_id="contoso.onmicrosoft.com").ms_tenant_id == "contoso.onmicrosoft.com"     # a tenant by its domain
    assert valid(ms_tenant_id=TENANT_ID.upper()).ms_tenant_id == TENANT_ID.upper()
    blank = valid(ms_tenant_id="", ms_client_id=" ", mailbox="", mail_folder="", mail_folder_name="")
    assert (blank.ms_tenant_id, blank.ms_client_id, blank.mailbox, blank.mail_folder, blank.mail_folder_name) == (None,) * 5
    assert SETTINGS.send_via == "auto" and SETTINGS.mail_folder is None
    with pytest.raises(ValueError):
        valid(ms_client_id="contoso.onmicrosoft.com")                       # a client is a GUID only
    with pytest.raises(ValueError, match="send_via"):
        valid(send_via="microsoft")


# --- the tab ---

def tab(tmp_path, madeup_out, app: FakeApp | None = None, session: FakeSession | None = None, share: bool = False, **values):
    """The review page with its settings tab on a made-up settings file, a fake sign-in and a fake Graph: (the settings file, the
    page, the (tenant, client) pairs the page made an account for)."""
    path = settings_file(tmp_path, **values)
    d = deps(tmp_path, madeup_out).model_copy(update={"settings": load_settings(path)})
    store = SecretStore(tmp_path / "secrets")
    signing = MicrosoftAccount(TENANT_ID, CLIENT_ID, store, app=(app or FakeApp()).bind(store.token_cache()))
    made = []

    def account_for(tenant: str, client: str) -> MicrosoftAccount:
        made.append((tenant, client))
        return signing

    page = build_app(d, settings_file=path, share=share, account_factory=account_for, session=session or FakeSession())
    return path, page, made


def handler(page, name: str):
    [found] = [f.fn for f in page.fns.values() if getattr(f.fn, "__name__", "") == name]
    return found


def test_the_tab_opens_with_the_saved_settings_and_the_signed_in_account(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, app=FakeApp(accounts=[USERNAME]), **SIX)
    tenant, client, mailbox, folder, send_via, account, trigger, names = handler(page, "settings_opened")("nl")
    assert (tenant, client, mailbox, send_via) == (TENANT_ID, CLIENT_ID, "", "auto")
    assert folder["choices"] == [("Orderbevestigingen", FOLDER)] and folder["value"] == FOLDER
    assert names == {"mailbox": "", "folders": {FOLDER: "Orderbevestigingen"}}            # the folders listed, and of which mailbox
    assert account == STRINGS["nl"]["settings_signed_in"].format(account=USERNAME) and made == [(TENANT_ID, CLIENT_ID)]
    assert trigger == STRINGS["nl"]["settings_trigger_pending"]


def test_opening_the_tab_reads_the_token_cache_only(tmp_path, madeup_out):
    """Opening the tab, or switching its language, asks Microsoft nothing: the account is read from the token cache, without
    msal's application (the conftest makes it raise ReachedMicrosoft) and without renewing a token."""
    path = settings_file(tmp_path, **SIX)
    d = deps(tmp_path, madeup_out).model_copy(update={"settings": load_settings(path)})
    store = SecretStore(tmp_path / "secrets")
    store.token_cache().add(sign_in_event(USERNAME))
    page = build_app(d, settings_file=path, account_factory=lambda tenant, client: MicrosoftAccount(tenant, client, store),
                     session=FakeSession())
    assert handler(page, "settings_opened")("nl")[5] == STRINGS["nl"]["settings_signed_in"].format(account=USERNAME)
    assert handler(page, "settings_relabel")("en", TENANT_ID, CLIENT_ID, "", "auto")[7] == \
        STRINGS["en"]["settings_signed_in"].format(account=USERNAME)


def test_a_sign_in_that_has_lapsed_shows_when_the_connection_is_tested_or_the_folders_are_loaded(tmp_path, madeup_out):
    app = FakeApp(accounts=[USERNAME], silent=None)
    path, page, made = tab(tmp_path, madeup_out, app=app, **SIX)
    assert handler(page, "settings_opened")("nl")[5] == STRINGS["nl"]["settings_signed_in"].format(account=USERNAME)
    assert not [call for call in app.calls if call[0] == "acquire_token_silent_with_error"]          # no token asked to open the tab
    lapsed = STRINGS["nl"]["settings_lapsed"].format(account=USERNAME)
    assert handler(page, "check_connection")(TENANT_ID, CLIENT_ID, "", FOLDER, "nl") == lapsed
    assert handler(page, "load_folders")(TENANT_ID, CLIENT_ID, "", "nl")[2] == lapsed
    path, page, made = tab(tmp_path / "none", madeup_out, app=FakeApp(), **SIX)
    assert handler(page, "check_connection")(TENANT_ID, CLIENT_ID, "", FOLDER, "nl") == STRINGS["nl"]["settings_not_signed_in"]


def test_a_microsoft_that_cannot_be_reached_is_named_by_the_class_of_the_error(tmp_path, madeup_out):
    class Unreachable(FakeSession):
        def get(self, url, headers=None, timeout=None, **kwargs):
            raise requests.ConnectionError(f"made-up failure for {url}")

    path, page, made = tab(tmp_path, madeup_out, app=FakeApp(accounts=[USERNAME]), session=Unreachable(), **SIX)
    note = handler(page, "check_connection")(TENANT_ID, CLIENT_ID, "", FOLDER, "nl")
    assert note == STRINGS["nl"]["settings_graph_failed"].format(code="ConnectionError") and "made-up" not in note
    assert handler(page, "load_folders")(TENANT_ID, CLIENT_ID, "", "nl")[2] == note


def test_a_store_the_pc_refuses_is_named_on_the_tab_without_its_folder(tmp_path, madeup_out):
    path = settings_file(tmp_path, **SIX)
    d = deps(tmp_path, madeup_out).model_copy(update={"settings": load_settings(path)})
    work_tree = tmp_path / "work-tree"                       # a folder inside a git work tree, which the store refuses
    (work_tree / ".git").mkdir(parents=True)

    def refused(tenant, client):
        return MicrosoftAccount(tenant, client, SecretStore(work_tree / "secrets"))

    page = build_app(d, settings_file=path, account_factory=refused, session=FakeSession())
    assert handler(page, "settings_opened")("nl")[5] == STRINGS["nl"]["settings_not_signed_in"]           # the tab opens all the same
    notes = [list(handler(page, "sign_in_browser")(TENANT_ID, CLIENT_ID, "", "nl"))[-1][3],
             handler(page, "sign_out")(TENANT_ID, CLIENT_ID, "nl")[3], handler(page, "load_folders")(TENANT_ID, CLIENT_ID, "", "nl")[2],
             handler(page, "check_connection")(TENANT_ID, CLIENT_ID, "", FOLDER, "nl")]
    assert notes == [STRINGS["nl"]["settings_store_refused"]] * 4 and "secrets" not in STRINGS["nl"]["settings_store_refused"]
    ai = [list(handler(page, "sign_in_copilot")(TENANT_ID, CLIENT_ID, "", "nl"))[-1][1], handler(page, "test_copilot")(TENANT_ID, CLIENT_ID, "nl")]
    assert ai == [STRINGS["nl"]["settings_store_refused"]] * 2
    assert not (work_tree / "secrets").exists()


def test_the_browser_sign_in_shows_the_account_and_lists_the_folders_and_never_the_token(tmp_path, madeup_out):
    session = FakeSession({("GET", f"{GRAPH}/me/mailFolders?$top=100"): FOLDERS})
    path, page, made = tab(tmp_path, madeup_out, session=session)
    waiting, shown = list(handler(page, "sign_in_browser")(TENANT_ID, CLIENT_ID, "", "nl"))
    assert waiting[3] == STRINGS["nl"]["settings_waiting"]               # said before the browser window opens
    assert STRINGS["nl"]["settings_waiting"].startswith("Er opent nu een venster van Microsoft")
    account, folders, names, note = shown
    assert account == STRINGS["nl"]["settings_signed_in"].format(account=USERNAME)
    assert folders["choices"] == [("Postvak IN", "AAMkADInbox="), ("Orderbevestigingen", FOLDER)]
    assert names == {"mailbox": "", "folders": {"AAMkADInbox=": "Postvak IN", FOLDER: "Orderbevestigingen"}}
    assert ACCESS_TOKEN not in repr([waiting, shown]) and session.calls[0]["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert made == [(TENANT_ID, CLIENT_ID)]


def test_a_browser_sign_in_that_times_out_says_so_by_a_fixed_label(tmp_path, madeup_out):
    from msal.oauth2cli.oauth2 import BrowserInteractionTimeoutError

    class Unanswered(FakeApp):
        def acquire_token_interactive(self, scopes, **kwargs):
            raise BrowserInteractionTimeoutError("made-up: no answer within the time")

    path, page, made = tab(tmp_path, madeup_out, app=Unanswered())
    waiting, shown = list(handler(page, "sign_in_browser")(TENANT_ID, CLIENT_ID, "", "en"))
    assert shown[3] == STRINGS["en"]["settings_browser_timeout"] and "made-up" not in shown[3]


def test_a_sign_in_for_another_mailbox_asks_for_the_shared_scopes(tmp_path, madeup_out):
    app = FakeApp()
    session = FakeSession({("GET", f"{GRAPH}/users/orders@example.com/mailFolders?$top=100"): FOLDERS})
    path, page, made = tab(tmp_path, madeup_out, app=app, session=session)
    list(handler(page, "sign_in_browser")(TENANT_ID, CLIENT_ID, " orders@example.com ", "nl"))
    [(_, scopes, _)] = [call for call in app.calls if call[0] == "acquire_token_interactive"]
    assert set(SHARED_SCOPES) <= set(scopes)


def test_a_sign_in_needs_valid_ids_and_a_failed_one_shows_its_error_code_only(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, app=FakeApp(result={"error": "access_denied", "error_description": "made-up detail"}))
    sign_in = handler(page, "sign_in_browser")
    assert list(sign_in("not-a-guid", CLIENT_ID, "", "nl"))[-1][3] == STRINGS["nl"]["settings_ids_needed"] and made == []
    note = list(sign_in(TENANT_ID, CLIENT_ID, "", "en"))[-1][3]
    assert note == STRINGS["en"]["settings_sign_in_failed"].format(code="access_denied") and "made-up" not in note


def test_the_sign_in_with_a_code_shows_the_code_and_the_address_and_then_waits_for_the_account(tmp_path, madeup_out):
    app = FakeApp()
    session = FakeSession({("GET", f"{GRAPH}/users/orders@example.com/mailFolders?$top=100"): FOLDERS})
    path, page, made = tab(tmp_path, madeup_out, app=app, session=session)
    steps = handler(page, "sign_in_code")(TENANT_ID, CLIENT_ID, "orders@example.com", "nl")
    first = next(steps)                                       # the code is shown before the page waits for it to be entered
    assert first[3] == STRINGS["nl"]["settings_code"].format(address="https://microsoft.com/devicelogin", code="ABCD-1234")
    assert not [call for call in app.calls if call[0] == "acquire_token_by_device_flow"]
    last = next(steps)
    assert [call for call in app.calls if call[0] == "acquire_token_by_device_flow"]
    assert last[0] == STRINGS["nl"]["settings_signed_in"].format(account=USERNAME) and len(last[1]["choices"]) == 2
    assert ACCESS_TOKEN not in repr([first, last]) and next(steps, None) is None


def test_the_connection_test_shows_the_number_of_messages_and_the_newest_time_and_nothing_of_a_message(tmp_path, madeup_out):
    newest = FakeResponse(json_data={"value": [{"receivedDateTime": "2026-11-12T08:59:00Z", "subject": "Geheim onderwerp",
                                                "from": {"emailAddress": {"address": "s01@example.com"}}}]})
    session = FakeSession({("GET", f"{GRAPH}/me/mailFolders/{FOLDER}"): FakeResponse(json_data={"id": FOLDER, "totalItemCount": 12}),
                           ("GET", f"{GRAPH}/me/mailFolders/{FOLDER}/messages?$top=1&$orderby=receivedDateTime desc&$select=receivedDateTime"): newest})
    path, page, made = tab(tmp_path, madeup_out, app=FakeApp(accounts=[USERNAME]), session=session)
    note = handler(page, "check_connection")(TENANT_ID, CLIENT_ID, "", FOLDER, "nl")
    assert note == STRINGS["nl"]["settings_tested"].format(count=12, newest=local("2026-11-12T08:59:00+00:00"))
    assert "Geheim" not in note and "s01" not in note
    empty = FakeSession({("GET", f"{GRAPH}/me/mailFolders/{FOLDER}"): FakeResponse(json_data={"id": FOLDER, "totalItemCount": 0}),
                         ("GET", f"{GRAPH}/me/mailFolders/{FOLDER}/messages?$top=1&$orderby=receivedDateTime desc&$select=receivedDateTime"):
                             FakeResponse(json_data={"value": []})})
    path, page, made = tab(tmp_path / "empty", madeup_out, app=FakeApp(accounts=[USERNAME]), session=empty)
    assert handler(page, "check_connection")(TENANT_ID, CLIENT_ID, "", FOLDER, "nl") == STRINGS["nl"]["settings_tested_empty"]
    gone = FakeSession({("GET", f"{GRAPH}/me/mailFolders/{FOLDER}"): FakeResponse(404, json_data={"error": {"code": "ErrorItemNotFound", "message": "made-up"}})})
    path, page, made = tab(tmp_path / "gone", madeup_out, app=FakeApp(accounts=[USERNAME]), session=gone)
    assert handler(page, "check_connection")(TENANT_ID, CLIENT_ID, "", FOLDER, "en") == STRINGS["en"]["settings_graph_failed"].format(code="HTTP 404")


def test_saving_on_the_tab_writes_the_settings_and_says_that_the_check_takes_them_over(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, app=FakeApp(accounts=[USERNAME]))
    save = handler(page, "save_settings")
    listed = {"mailbox": "", "folders": {FOLDER: "Orderbevestigingen"}}
    note, trigger = save(TENANT_ID, CLIENT_ID, "", FOLDER, "auto", listed, "nl")
    assert note == STRINGS["nl"]["settings_buyer_saved"] == "Opgeslagen. De assistent gebruikt dit vanaf de volgende ronde, binnen een minuut."
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert {key: saved[key] for key in SIX} == SIX and trigger == STRINGS["nl"]["settings_trigger_pending"]
    assert ACCESS_TOKEN not in path.read_text(encoding="utf-8") and STRINGS["en"]["settings_buyer_saved"] == "Saved. The aid uses this from its next pass, within a minute."
    assert "settings_saved" not in STRINGS["nl"] and "settings_saved" not in STRINGS["en"]      # one note for a save the runner takes over
    refused, _ = save(TENANT_ID, "not-a-guid-xyz", "", FOLDER, "auto", listed, "nl")
    assert refused == STRINGS["nl"]["settings_not_saved"].format(fields=STRINGS["nl"]["settings_client"]) and "not-a-guid-xyz" not in refused
    assert json.loads(path.read_text(encoding="utf-8")) == saved
    other, _ = save(TENANT_ID, CLIENT_ID, "orders@example.com", FOLDER, "auto", listed, "nl")      # the folder is of the own mailbox
    assert other == STRINGS["nl"]["settings_folders_first"] and json.loads(path.read_text(encoding="utf-8")) == saved


def test_a_save_that_turns_the_trigger_off_or_names_another_folder_forgets_the_connection(tmp_path, madeup_out):
    """So that connecting a folder again later starts from that moment, also while the runner is not running."""
    path, page, made = tab(tmp_path, madeup_out, app=FakeApp(accounts=[USERNAME]), **SIX)
    root = tmp_path / "cases"

    def connect():
        root.mkdir(exist_ok=True)
        state = {"mailbox": None, "folder": FOLDER, "connected_at": "2026-11-12T08:00:00Z", "delta_link": f"{GRAPH}/made-up",
                 "last_sync": "2026-11-12T09:15:00Z", "saved": 3}
        (root / "mailbox_state.json").write_text(json.dumps(state), encoding="utf-8")
        (root / "mailbox_known.json").write_text("[]", encoding="utf-8")

    def stored() -> list[str]:
        return sorted(p.name for p in root.iterdir() if p.name.startswith("mailbox_"))

    save = handler(page, "save_settings")
    listed = {"mailbox": "", "folders": {FOLDER: "Orderbevestigingen", "AAMkADInbox=": "Postvak IN"}}
    connect()
    assert save(TENANT_ID, CLIENT_ID, "", FOLDER, "auto", listed, "nl")[0] == STRINGS["nl"]["settings_buyer_saved"]
    assert stored() == ["mailbox_known.json", "mailbox_state.json"]                     # the same folder: the connection stays
    save(TENANT_ID, CLIENT_ID, "", None, "auto", listed, "nl")                         # no folder: the trigger is off
    assert stored() == []
    connect()
    save(TENANT_ID, CLIENT_ID, "", "AAMkADInbox=", "auto", listed, "nl")               # another folder
    assert stored() == []


def test_the_state_of_the_trigger_comes_from_the_state_file(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **SIX)
    (tmp_path / "cases").mkdir(exist_ok=True)
    state = {"mailbox": None, "folder": FOLDER, "connected_at": "2026-11-12T08:00:00Z", "delta_link": "made-up",
             "last_sync": "2026-11-12T09:15:00Z", "saved": 3, "known": []}
    (tmp_path / "cases" / "mailbox_state.json").write_text(json.dumps(state), encoding="utf-8")
    trigger = handler(page, "settings_opened")("nl")[6]
    assert trigger == STRINGS["nl"]["settings_trigger_on"].format(since=local("2026-11-12T08:00:00+00:00"),
                                                                  last=local("2026-11-12T09:15:00+00:00"), saved=3)
    assert "made-up" not in trigger
    unread = state | {"delta_link": None, "last_sync": None}                # connected, but the baseline is not taken yet
    (tmp_path / "cases" / "mailbox_state.json").write_text(json.dumps(unread), encoding="utf-8")
    assert handler(page, "settings_opened")("nl")[6] == STRINGS["nl"]["settings_trigger_unread"].format(since=local("2026-11-12T08:00:00+00:00"))
    off_path, off_page, _ = tab(tmp_path / "off", madeup_out)
    assert handler(off_page, "settings_opened")("en")[6] == STRINGS["en"]["settings_trigger_off"]


def test_sign_out_removes_the_account_and_empties_the_folders(tmp_path, madeup_out):
    app = FakeApp(accounts=[USERNAME])
    path, page, made = tab(tmp_path, madeup_out, app=app)
    account, folders, names, note = handler(page, "sign_out")(TENANT_ID, CLIENT_ID, "nl")
    assert account == STRINGS["nl"]["settings_not_signed_in"] and folders["choices"] == [] and app.accounts == []
    assert names == {"mailbox": None, "folders": {}}


def test_after_a_sign_out_opening_the_tab_asks_microsoft_nothing(tmp_path, madeup_out):
    """Once msal's application is made, its get_accounts asks Microsoft about the authority when it finds no account, with the
    application's timeout: after "Afmelden" the tab still reads the account from the token cache only."""
    class NoDiscovery(FakeApp):
        signed_out = False

        def get_accounts(self, username=None):
            if self.signed_out:
                raise ReachedMicrosoft("msal's get_accounts after the sign-out would ask Microsoft about the authority")
            return super().get_accounts(username)

    app = NoDiscovery(accounts=[USERNAME])
    path, page, made = tab(tmp_path, madeup_out, app=app, **SIX)
    assert handler(page, "sign_out")(TENANT_ID, CLIENT_ID, "nl")[0] == STRINGS["nl"]["settings_not_signed_in"]
    app.signed_out = True
    assert handler(page, "settings_opened")("nl")[5] == STRINGS["nl"]["settings_not_signed_in"]
    assert handler(page, "settings_relabel")("en", TENANT_ID, CLIENT_ID, "", "auto")[7] == STRINGS["en"]["settings_not_signed_in"]


def test_on_a_public_link_the_tab_offers_nothing(tmp_path, madeup_out):
    gr = import_gradio()
    path, page, made = tab(tmp_path, madeup_out, share=True, **SIX)
    [settings] = [block for block in page.blocks.values() if isinstance(block, gr.Tab) and block.label == STRINGS["nl"]["settings_tab"]]
    assert [type(child).__name__ for child in settings.children] == ["Markdown"]
    assert settings.children[0].value == STRINGS["nl"]["settings_share"] == "Niet beschikbaar op een openbare link"
    names = {getattr(f.fn, "__name__", "") for f in page.fns.values()}
    assert not names & {"settings_opened", "sign_in_browser", "sign_in_code", "sign_out", "load_folders", "check_connection", "save_settings"}
    assert made == [] and TENANT_ID not in json.dumps(page.config, default=str)


def test_a_page_without_its_settings_file_offers_no_settings_either(tmp_path, madeup_out):
    gr = import_gradio()
    page = build_app(deps(tmp_path, madeup_out))
    [settings] = [block for block in page.blocks.values() if isinstance(block, gr.Tab) and block.label == STRINGS["nl"]["settings_tab"]]
    assert [child.value for child in settings.children] == [STRINGS["nl"]["settings_no_file"]]


def test_the_review_command_gives_the_page_its_settings_file_and_its_share_and_stays_on_the_loopback(tmp_path, monkeypatch):
    import aid.review.__main__ as review
    monkeypatch.setenv("GRADIO_TEMP_DIR", "")                   # recorded, so that the folder the command names is undone after the test
    monkeypatch.delenv("GRADIO_TEMP_DIR")
    path = settings_file(tmp_path)
    built = []

    class Page:
        def launch(self, **options):
            built.append(options)

    monkeypatch.setattr(review, "build_app", lambda d, **options: (built.append(options), Page())[1])
    assert review.main(["--settings", str(path)]) == 0
    assert review.main(["--settings", str(path), "--share"]) == 0               # made-up settings: the link is allowed
    guards = [options.pop("app_kwargs") for options in built if "app_kwargs" in options]
    assert built == [{"settings_file": path, "share": False}, {"server_name": "127.0.0.1", "share": False, "ssr_mode": False},
                     {"settings_file": path, "share": True}, {"server_name": "127.0.0.1", "share": True, "ssr_mode": False}]
    for guard in guards:                                      # each launch with the check of the Host header, and nothing else
        assert list(guard) == ["middleware"] and [middleware.cls for middleware in guard["middleware"]] == [review.OwnHost]
    assert len(guards) == 2
    assert os.environ["GRADIO_TEMP_DIR"] == str(tmp_path / "cases" / "tmp")           # the page library's temporary files


def test_the_extension_code_is_shown_on_the_local_page_only(tmp_path, madeup_out):
    """The code the browser extension sends (capture_token) is one line inside the folded connections that cannot be edited, with
    a button to copy it, filled from the settings file when the page loads: on the page of this PC only, not on a public link and
    not on a page without its settings file."""
    gr = import_gradio()
    nl, en = STRINGS["nl"], STRINGS["en"]
    assert (nl["settings_extension_code"], en["settings_extension_code"]) == ("Code voor de uitbreiding in de browser", "Code for the browser extension")
    assert nl["settings_extension_code_help"] == "Plak deze code in de opties van de uitbreiding Inkoopassistent in Edge of Chrome."
    assert en["settings_extension_code_help"] == "Paste this code into the options of the Inkoopassistent extension in Edge or Chrome."
    token = "made-up-code-of-the-extension-0123456789"
    path, page, made = tab(tmp_path, madeup_out, capture_token=token)
    [settings] = [block for block in page.blocks.values() if isinstance(block, gr.Tab) and block.label == nl["settings_tab"]]
    [folded] = [block for block in settings.children if isinstance(block, gr.Accordion) and block.label == nl["settings_connections"]]

    def inside(block) -> list:
        return [block, *[found for child in getattr(block, "children", []) for found in inside(child)]]
    [code] = [block for block in page.blocks.values() if getattr(block, "label", None) == nl["settings_extension_code"]]
    assert code in inside(folded) and isinstance(code, gr.Textbox) and code.interactive is False and code.buttons == ["copy"]
    assert code.info == nl["settings_extension_code_help"] and code.max_lines == 1
    [fill] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "extension_code_opened"]
    assert fill.outputs == [code] and fill.fn() == token
    assert token not in json.dumps(page.config, default=str)                     # read when the page loads, not built into it
    assert handler(page, "extension_code_relabel")("en") == {"__type__": "update", "label": en["settings_extension_code"],
                                                            "info": en["settings_extension_code_help"]}
    path.write_text(path.read_text(encoding="utf-8").replace(token, "made-up-code-after-a-change-of-the-file"), encoding="utf-8")
    assert fill.fn() == "made-up-code-after-a-change-of-the-file"                  # the file as it is when the page loads
    # nothing of it on a public link, and nothing on a page without its settings file
    shared = tab(tmp_path / "shared", madeup_out, share=True, capture_token=token)[1]
    without = build_app(deps(tmp_path / "without", madeup_out).model_copy(update={"settings": load_settings(path)}))
    for other in (shared, without):
        assert nl["settings_extension_code"] not in {getattr(block, "label", None) for block in other.blocks.values()}
        assert not {"extension_code_opened", "extension_code_relabel"} & {getattr(f.fn, "__name__", "") for f in other.fns.values()}
        assert token not in json.dumps(other.config, default=str) and "made-up-code-after" not in json.dumps(other.config, default=str)


def test_switching_the_language_relabels_the_tab_and_clears_its_note(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, app=FakeApp(accounts=[USERNAME]), **SIX)
    shown = handler(page, "settings_relabel")("en", TENANT_ID, CLIENT_ID, "", "auto")
    assert shown[0] == STRINGS["en"]["settings_intro"] and shown[1]["label"] == "Directory (tenant) ID" and shown[-1] == ""


def test_a_public_link_is_refused_while_the_settings_name_a_tenant_or_a_folder():
    for name, value in (("ms_tenant_id", TENANT_ID), ("mail_folder", FOLDER)):
        settings = SETTINGS.model_copy(update={"authorization_limit": None, name: value})
        assert company_values(settings) == [f"{name} is set"] and value not in refuse_share(settings)
    assert company_values(SETTINGS.model_copy(update={"authorization_limit": None})) == []


def test_the_two_languages_have_the_same_strings_for_the_tab():
    assert {key for key in STRINGS["nl"] if key.startswith("settings")} == {key for key in STRINGS["en"] if key.startswith("settings")}
    assert STRINGS["nl"]["settings_tab"] == "Instellingen" and STRINGS["en"]["settings_tab"] == "Settings"
    assert STRINGS["nl"]["settings_sign_in"] == "Aanmelden bij Microsoft" and STRINGS["nl"]["settings_sign_in_code"] == "Aanmelden met een code"
    assert STRINGS["nl"]["settings_test"] == "Verbinding testen" and STRINGS["nl"]["settings_save"] == "Opslaan"
    assert STRINGS["nl"]["settings_sign_out"] == "Afmelden"
    assert (STRINGS["nl"]["settings_tenant"], STRINGS["nl"]["settings_client"]) == ("Map-id (tenant)", "Toepassings-id (client)")
    assert STRINGS["nl"]["settings_ids_needed"] == "Vul eerst een geldige map-id (tenant) en toepassings-id (client) in."


# --- the AI section: Microsoft 365 Copilot (Task 27) ---

def test_the_copilot_sign_in_asks_for_the_mail_and_the_copilot_permissions_in_the_browser(tmp_path, madeup_out):
    app = FakeApp()
    path, page, made = tab(tmp_path, madeup_out, app=app)
    waiting, (account, note) = list(handler(page, "sign_in_copilot")(TENANT_ID, CLIENT_ID, "", "nl"))
    assert waiting[1] == STRINGS["nl"]["settings_waiting"]                # said while the browser window is open
    [(_, scopes, options)] = [call for call in app.calls if call[0] == "acquire_token_interactive"]
    assert scopes == MAIL_SCOPES + COPILOT_SCOPES and options["prompt"] == "select_account"
    assert account == STRINGS["nl"]["settings_signed_in"].format(account=USERNAME) and note == STRINGS["nl"]["settings_ai_signed_in"]
    assert list(handler(page, "sign_in_copilot")("not-a-guid", CLIENT_ID, "", "en"))[-1][1] == STRINGS["en"]["settings_ids_needed"]
    failed = tab(tmp_path / "failed", madeup_out, app=FakeApp(result={"error": "consent_required", "error_description": "made-up"}))[1]
    note = list(handler(failed, "sign_in_copilot")(TENANT_ID, CLIENT_ID, "", "nl"))[-1][1]
    assert note == STRINGS["nl"]["settings_sign_in_failed"].format(code="consent_required") and "made-up" not in note


def test_the_copilot_sign_in_for_another_mailbox_keeps_the_shared_scopes(tmp_path, madeup_out):
    app = FakeApp()
    path, page, made = tab(tmp_path, madeup_out, app=app)
    list(handler(page, "sign_in_copilot")(TENANT_ID, CLIENT_ID, " orders@example.com ", "nl"))
    [(_, scopes, _)] = [call for call in app.calls if call[0] == "acquire_token_interactive"]
    assert scopes == MAIL_SCOPES + SHARED_SCOPES + COPILOT_SCOPES


def test_the_copilot_test_sends_one_chat_and_shows_the_first_20_characters_of_the_answer(tmp_path, madeup_out):
    session = copilot_session("OK, this answer is longer than twenty characters.")
    path, page, made = tab(tmp_path, madeup_out, app=FakeApp(accounts=[USERNAME]), session=session)
    note = handler(page, "test_copilot")(TENANT_ID, CLIENT_ID, "nl")
    assert note == STRINGS["nl"]["settings_ai_answer"].format(answer="OK, this answer is l") and note.startswith("Copilot antwoordt")
    create, chat = session.calls
    assert create["url"] == COPILOT and json.loads(chat["data"])["message"] == {"text": "Answer with the word OK only."}
    assert json.loads(chat["data"])["contextualResources"] == {"webContext": {"isWebEnabled": False}}
    assert ACCESS_TOKEN not in note and handler(page, "test_copilot")("", CLIENT_ID, "nl") == STRINGS["nl"]["settings_ids_needed"]


@pytest.mark.parametrize("app, session, error", [
    (FakeApp(accounts=[USERNAME]), copilot_session(created=FakeResponse(403, json_data={"error": {"code": "Forbidden", "message": "made-up"}})),
     "Copilot refused: 403 Forbidden"),
    (FakeApp(accounts=[USERNAME]), copilot_session(lambda request: copilot_answer(None, sent_text(request))),      # the recap alone
     "Copilot gave no answer"),
    (FakeApp(accounts=[USERNAME]), copilot_session(lambda request: copilot_answer("Sorry.", sent_text(request), state="disengagedForRai")),
     "Copilot ended the conversation: disengagedForRai"),
    (FakeApp(), copilot_session("OK"), "no Microsoft account is signed in: sign in again on the Settings tab of the review page"),
])
def test_the_copilot_test_shows_the_error_text_and_never_a_message_of_microsoft(tmp_path, madeup_out, app, session, error):
    path, page, made = tab(tmp_path, madeup_out, app=app, session=session)
    note = handler(page, "test_copilot")(TENANT_ID, CLIENT_ID, "en")
    assert note == STRINGS["en"]["settings_ai_failed"].format(error=error) and "made-up" not in note


def test_the_choice_of_the_reader_is_written_to_the_settings_file_and_shown_when_the_tab_opens(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **SIX)
    before = json.loads(path.read_text(encoding="utf-8"))
    assert handler(page, "ai_opened")("nl") == "parser"
    choice, note = handler(page, "save_reader")("llm", "nl")
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after == before | {"reader": "llm"} and list(after) == list(before) and note == STRINGS["nl"]["settings_buyer_saved"]
    assert choice == "llm" and handler(page, "ai_opened")("en") == "llm"
    choice, note = handler(page, "save_reader")("parser", "en")
    assert json.loads(path.read_text(encoding="utf-8"))["reader"] == "parser" and note == STRINGS["en"]["settings_buyer_saved"]


def test_copilot_is_not_chosen_before_the_tenant_and_the_client_are_saved(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out)
    before = path.read_bytes()
    choice, note = handler(page, "save_reader")("llm", "nl")
    assert (choice, note) == ("parser", STRINGS["nl"]["settings_ai_ids_needed"]) and path.read_bytes() == before


def test_the_ai_section_says_what_copilot_needs_in_both_languages():
    for language in ("nl", "en"):
        strings = STRINGS[language]
        assert all(scope in strings["settings_ai_needs"] for scope in COPILOT_SCOPES) and "Microsoft 365 Copilot" in strings["settings_ai_needs"]
    nl = STRINGS["nl"]
    assert (nl["settings_ai_sign_in"], nl["settings_ai_test"], nl["settings_ai_reader"]) == (
        "Aanmelden voor Copilot", "Copilot testen", "Documenten lezen met")
    assert (nl["settings_reader:llm"], nl["settings_reader:parser"]) == ("Copilot", "zonder AI (parser)")
    assert nl["settings_ai_answer"].startswith("Copilot antwoordt") and "beheerder" in nl["settings_ai_needs"]
    assert "licence" in STRINGS["en"]["settings_ai_needs"] and "administrator" in STRINGS["en"]["settings_ai_needs"]


@pytest.mark.parametrize("field, value, label", [("send_via", "pigeon-xyz", "settings_send_via"), ("mailbox", "no-address-xyz", "settings_mailbox"),
                                                 ("ms_client_id", "not-a-guid-xyz", "settings_client")])
def test_the_reader_is_not_saved_into_a_file_that_does_not_validate_and_the_field_is_named_by_its_label(tmp_path, madeup_out, field, value,
                                                                                                        label):
    path, page, made = tab(tmp_path, madeup_out, **SIX)
    data = json.loads(path.read_text(encoding="utf-8")) | {field: value}
    path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    before = path.read_bytes()
    choice, note = handler(page, "save_reader")("parser", "en")
    en = STRINGS["en"]                                         # the line of a settings file that cannot be read, with the field's label
    assert note == en["settings_file_unreadable"].format(fields=en["settings_file_fields"].format(fields=en[label])) and value not in note
    assert en[label] in note and choice == "parser" and path.read_bytes() == before
    listed = {"mailbox": "", "folders": {FOLDER: "Orderbevestigingen"}}
    assert handler(page, "save_settings")(TENANT_ID, CLIENT_ID, "", FOLDER, "auto", listed, "en")[0] == note      # the mailbox's save alike
    assert path.read_bytes() == before


# --- the buyer's settings (Task 29) ---

BUYER = {"price_pct": "2.0", "price_abs": "25.00", "lead_time_tolerance_days": 7, "first_cases_review": 0, "ask_wait_working_days": 3,
         "buyer_name": "", "digest": "daily", "digest_time": "16:30", "language": "nl",
         "supplier_emails": {"S07": "sales@s07.example.com"}, "supplier_lead_times": {"S07": 10, "S01": 10},
         "review_suppliers": ["Acme"], "messages_off_suppliers": []}
SHOWN = ["2", "25,00", "7", "0", "3", "", "daily", "16:30", "buyer@example.com", "nl", "approver@example.com"]    # BUYER as the Dutch form shows it
CODE = re.compile(r"\b[RB]\d\d\b|_")                 # a rule code, or a setting's name such as price_pct
SUPPLIER_FORM = ("name", "email", "lead_time", "review", "off")
OPENED = ("line", *BUYER_FIELDS, "filled", "table", "choice", *SUPPLIER_FORM, "supplier_filled")       # what the fill gives, in its order
SAVED = ("note", "line", *BUYER_FIELDS, "filled")                                                       # what the main save gives
SUPPLIER_SAVED = ("note", "line", "table", "choice", *SUPPLIER_FORM, "supplier_filled")               # what a supplier save or removal gives
NEW_FORM = {"name": NEW_SUPPLIER, "email": "", "lead_time": "", "review": False, "off": False}        # the supplier form as "Leverancier toevoegen" fills it
SAME = {"__type__": "update"}                         # gr.update(): the component is left as it is


def named(names: tuple, outputs: tuple) -> dict:
    assert len(outputs) == len(names), (len(outputs), len(names))
    return dict(zip(names, outputs))


def opened(page, language="nl") -> dict:
    """The fill of the buyer's part (the page loaded, or the settings tab selected), by name."""
    return named(OPENED, handler(page, "buyer_opened")(language))


def form(page, language="nl") -> list:
    """The eleven fields of the form as the fill gives them, in the order of BUYER_FIELDS."""
    shown = opened(page, language)
    return [shown[field] for field in BUYER_FIELDS]


def save(page, values: list, filled, language="nl") -> dict:
    """The main save with the eleven texts and what the form was filled with, by name."""
    return named(SAVED, handler(page, "save_buyer")(*values, filled, language))


def changed(page, page_language="nl", **texts) -> dict:
    """The form filled, the texts typed into it (by the names of BUYER_FIELDS) and the main save pressed."""
    shown = opened(page, page_language)
    return save(page, [texts.get(field, shown[field]) for field in BUYER_FIELDS], shown["filled"], page_language)


def choose(page, choice: str, language="nl") -> dict:
    """The buyer's choice in the dropdown of suppliers: the form it fills, by name."""
    return named((*SUPPLIER_FORM, "supplier_filled"), handler(page, "supplier_chosen")(choice, language))


def save_supplier(page, choice: str, language="nl", filled=None, **typed) -> dict:
    """A supplier chosen (or "Leverancier toevoegen"), the values typed over its form, and "Leverancier opslaan" pressed."""
    chosen = choose(page, choice, language)
    shown = chosen["supplier_filled"] | {"name": "" if choice == NEW_SUPPLIER else choice}
    given = shown | typed
    return named(SUPPLIER_SAVED, handler(page, "save_supplier")(choice, given["name"], given["email"], given["lead_time"], given["review"],
                                                                 given["off"], chosen["supplier_filled"] if filled is None else filled, language))


def remove_supplier(page, choice: str, language="nl") -> dict:
    return named(SUPPLIER_SAVED, handler(page, "remove_supplier")(choice, language))


def held(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def by_hand(path: Path, **values) -> None:
    """A change made in the settings file itself, with a modification time that differs from the one before."""
    data, before = held(path) | values, path.stat().st_mtime_ns
    path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    os.utime(path, ns=(before + 1_000_000_000, before + 1_000_000_000))


def unreadable(language: str, *labels: str) -> str:
    """The line of a settings file that cannot be read, naming the fields by their labels."""
    strings = STRINGS[language]
    return strings["settings_file_unreadable"].format(fields=strings["settings_file_fields"].format(fields=", ".join(labels)) if labels else "")


def test_the_form_shows_the_file_when_the_page_loads_and_each_time_the_settings_tab_is_selected(tmp_path, madeup_out):
    gr = import_gradio()
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    [fill] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "buyer_opened"]
    [settings] = [block for block in page.blocks.values() if isinstance(block, gr.Tab) and block.label == STRINGS["nl"]["settings_tab"]]
    assert sorted(fill.targets) == sorted([(page._id, "load"), (settings._id, "select")])
    first = opened(page)
    assert first["line"] == "" and [first[field] for field in BUYER_FIELDS] == SHOWN
    assert first["filled"] == dict(zip(BUYER_FIELDS, SHOWN))                                # what a save compares the form with
    assert first["table"]["headers"] == [STRINGS["nl"][f"settings_supplier_column:{n}"] for n in ("name", "email", "lead_time", "review", "messages_off")]
    assert first["table"]["data"] == [["Acme", "", "", STRINGS["nl"]["settings_tick"], ""], ["S01", "", "10", "", ""],
                                      ["S07", "sales@s07.example.com", "10", "", ""]]
    assert first["choice"]["choices"] == [(STRINGS["nl"]["settings_supplier_new"], NEW_SUPPLIER), ("Acme", "Acme"), ("S01", "S01"), ("S07", "S07")]
    assert first["choice"]["value"] == NEW_SUPPLIER and first["supplier_filled"] == NEW_FORM
    assert (first["name"]["value"], first["name"]["interactive"], first["email"], first["lead_time"], first["review"], first["off"]) == (
        "", True, "", "", False, False)
    by_hand(path, price_pct="3.5", buyer_name="A. Buyer", supplier_lead_times={"S07": 10, "S01": 99, "S30": 4})
    again = opened(page, "en")                                                              # the tab selected again
    assert (again["price_pct"], again["price_abs"], again["buyer_name"]) == ("3.5", "25.00", "A. Buyer") and again["filled"]["price_pct"] == "3.5"
    assert ["S01", "", "99", "", ""] in again["table"]["data"] and ("S30", "S30") in again["choice"]["choices"]
    assert form(page)[0] == "3,5"


def test_a_save_writes_each_of_the_eleven_fields_where_it_belongs_and_keeps_every_other_key_and_the_order(tmp_path, madeup_out):
    """All eleven changed, each to a value of its own, on a file that lacks six of them."""
    path, page, made = tab(tmp_path, madeup_out, price_abs="25.00", supplier_emails={"S07": "sales@s07.example.com"})
    before = held(path)
    lacking = ["lead_time_tolerance_days", "first_cases_review", "ask_wait_working_days", "buyer_name", "digest", "digest_time"]
    assert [key for key in BUYER_FIELDS if key not in before] == lacking
    typed = {"price_pct": "3,5", "price_abs": "40", "lead_time_tolerance_days": "9", "first_cases_review": "4", "ask_wait_working_days": "6",
             "buyer_name": " A. Buyer ", "digest": "per_case", "digest_time": "7:05", "buyer_email": "new.buyer@example.com", "language": "en",
             "approver_email": "new.approver@example.com"}
    result = changed(page, **typed)
    assert result["note"] == STRINGS["nl"]["settings_buyer_saved"] == "Opgeslagen. De assistent gebruikt dit vanaf de volgende ronde, binnen een minuut."
    assert STRINGS["en"]["settings_buyer_saved"] == "Saved. The aid uses this from its next pass, within a minute."
    after = held(path)
    assert {key: after[key] for key in BUYER_FIELDS} == {
        "price_pct": "3.5", "price_abs": "40", "lead_time_tolerance_days": 9, "first_cases_review": 4, "ask_wait_working_days": 6,
        "buyer_name": "A. Buyer", "digest": "per_case", "digest_time": "07:05", "buyer_email": "new.buyer@example.com", "language": "en",
        "approver_email": "new.approver@example.com"}
    assert list(after) == list(before) + lacking                                             # a key the file lacked at its end, in the form's order
    assert {key: after[key] for key in after if key not in BUYER_FIELDS} == {key: before[key] for key in before if key not in BUYER_FIELDS}
    assert path.read_text(encoding="utf-8").startswith('{\n    "language"')
    # the form shows the values as the file now holds them, and they are what the next save compares with
    as_held = ["3,5", "40,00", "9", "4", "6", "A. Buyer", "per_case", "07:05", "new.buyer@example.com", "en", "new.approver@example.com"]
    assert [result[field] for field in BUYER_FIELDS] == as_held and result["filled"] == dict(zip(BUYER_FIELDS, as_held)) and result["line"] == ""
    assert load_settings(path).price_pct == Decimal("3.5") and form(page) == as_held


def test_a_save_writes_only_the_fields_the_buyer_changed_and_nothing_when_none_changed(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    before = path.read_bytes()
    untouched = changed(page)                                                               # "Opslaan" on the form as it was filled
    assert untouched["note"] == STRINGS["nl"]["settings_buyer_unchanged"] == "Er is niets gewijzigd; er is niets opgeslagen."
    assert STRINGS["en"]["settings_buyer_unchanged"] == "Nothing was changed; nothing was saved."
    assert path.read_bytes() == before and path.stat().st_mtime_ns == 1_700_000_000_000_000_000
    assert [untouched[field] for field in BUYER_FIELDS] == SHOWN and untouched["filled"] == dict(zip(BUYER_FIELDS, SHOWN))
    assert changed(page, price_pct=" 2 ")["note"] == STRINGS["nl"]["settings_buyer_unchanged"] and path.read_bytes() == before
    stale = opened(page)                                                                    # with nothing typed the form shows the file as it is now
    by_hand(path, first_cases_review=3)
    shown_again = save(page, [stale[field] for field in BUYER_FIELDS], stale["filled"])
    assert (shown_again["note"], shown_again["first_cases_review"], shown_again["filled"]["first_cases_review"]) == (
        STRINGS["nl"]["settings_buyer_unchanged"], "3", "3") and held(path)["first_cases_review"] == 3
    by_hand(path, first_cases_review=0)
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    before = path.read_bytes()
    result = changed(page, buyer_name="A. Buyer")                                           # one field: the rest of the file keeps its bytes
    assert result["note"] == STRINGS["nl"]["settings_buyer_saved"]
    assert path.read_bytes() == before.replace(b'"buyer_name": ""', b'"buyer_name": "A. Buyer"') != before
    assert b'"price_pct": "2.0"' in path.read_bytes() and result["price_pct"] == "2"         # shown as 2, never written as "2"
    # the file changes while a form stays open: a field the buyer changed wins, a field he did not touch is never written
    stale = opened(page)
    by_hand(path, price_pct="4.5", digest_time="09:00", first_cases_review=5)
    values = [stale[field] for field in BUYER_FIELDS]
    values[BUYER_FIELDS.index("digest_time")] = "08:00"
    result = save(page, values, stale["filled"])
    after = held(path)
    assert result["note"] == STRINGS["nl"]["settings_buyer_saved"]
    assert (after["price_pct"], after["first_cases_review"], after["digest_time"], after["buyer_name"]) == ("4.5", 5, "08:00", "A. Buyer")
    assert (result["price_pct"], result["first_cases_review"], result["digest_time"]) == ("4,5", "5", "08:00")      # the file as it now is


def test_a_refused_field_writes_nothing_also_not_a_valid_field_changed_with_it_and_is_named_by_its_label(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    before = path.read_bytes()
    result = changed(page, "en", price_pct="150-xyz", digest_time="25:00", buyer_name="A. Buyer", approver_email="boss@example.com")
    en = STRINGS["en"]
    assert result["note"] == en["settings_buyer_refused"].format(fields="; ".join(
        [f"{en['settings_field:price_pct']}: {en['settings_problem:percent']}", f"{en['settings_field:digest_time']}: {en['settings_problem:time']}"]))
    assert path.read_bytes() == before and "150-xyz" not in result["note"]
    assert [result[field] for field in BUYER_FIELDS] == [SAME] * 11 and result["filled"] == SAME      # what he typed stays in the form
    # a field that is not valid but that he did not change is not read and holds nothing back
    by_hand(path, digest_time="half past four")
    assert changed(page, buyer_name="A. Buyer")["note"] == STRINGS["nl"]["settings_buyer_saved"]
    assert (held(path)["digest_time"], held(path)["buyer_name"]) == ("half past four", "A. Buyer")


def test_a_thousand_written_with_a_point_or_a_comma_is_refused_and_a_saved_number_shows_as_it_was_read(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    before = path.read_bytes()
    nl = STRINGS["nl"]
    for text in ("1.000", "10.000", "1,000"):
        result = changed(page, price_abs=text)
        assert result["note"] == nl["settings_buyer_refused"].format(fields=f"{nl['settings_field:price_abs']}: {nl['settings_problem:decimals']}")
        assert "1000" in result["note"] and path.read_bytes() == before
    result = changed(page, price_abs="1000", price_pct="2,50", digest_time="9:05")
    assert result["note"] == nl["settings_buyer_saved"] and (held(path)["price_abs"], held(path)["price_pct"], held(path)["digest_time"]) == (
        "1000", "2.50", "09:05")
    assert (result["price_abs"], result["price_pct"], result["digest_time"]) == ("1000,00", "2,5", "09:05")


def test_a_form_whose_fill_the_page_no_longer_knows_is_filled_again_and_nothing_is_written(tmp_path, madeup_out):
    """After the page was started again a browser tab left open still shows its old form: the page cannot tell what the buyer
    changed, so it writes nothing, shows the file and says so."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    stale = form(page)
    by_hand(path, price_pct="4.5")
    before = path.read_bytes()
    stale[BUYER_FIELDS.index("buyer_name")] = "A. Buyer"
    result = save(page, stale, None)
    assert result["note"] == STRINGS["nl"]["settings_buyer_refilled"] and path.read_bytes() == before
    assert result["price_pct"] == "4,5" and result["buyer_name"] == "" and result["filled"]["price_pct"] == "4,5"
    chosen = choose(page, "S07")
    result = named(SUPPLIER_SAVED, handler(page, "save_supplier")("S07", "S07", "sales@s07.example.com", "99", False, False, None, "nl"))
    assert result["note"] == STRINGS["nl"]["settings_buyer_refilled"] and path.read_bytes() == before
    assert (result["lead_time"], result["supplier_filled"]) == ("10", chosen["supplier_filled"])
    other = named(SUPPLIER_SAVED, handler(page, "save_supplier")("S07", "S07", "", "99", True, True, choose(page, "S01")["supplier_filled"], "nl"))
    assert other["note"] == STRINGS["nl"]["settings_buyer_refilled"] and path.read_bytes() == before      # filled for another supplier


def test_a_file_that_cannot_be_read_shows_one_line_and_the_settings_in_use_and_every_save_refuses(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl = STRINGS["nl"]
    assert changed(page, price_pct="5", buyer_name="A. Buyer")["note"] == nl["settings_buyer_saved"]      # the settings in use, saved on the page
    assert save_supplier(page, NEW_SUPPLIER, name="A BV", email="a@a.example.org", lead_time="5", review=True)["note"] == nl["settings_buyer_saved"]
    good = held(path)
    broken = {"a value the settings refuse": lambda: by_hand(path, price_abs="30,00-secret-xyz", smtp_port="x"),
              "a supplier value the settings refuse": lambda: by_hand(path, supplier_lead_times=good["supplier_lead_times"] | {"S01": "tien"}),
              "no JSON": lambda: path.write_text('{"language": "nl", ', encoding="utf-8"),
              "no object": lambda: path.write_text("[]", encoding="utf-8"),
              "missing": lambda: path.unlink()}
    lines = {"a value the settings refuse": unreadable("nl", nl["settings_field:price_abs"], "smtp_port"),
             "a supplier value the settings refuse": unreadable("nl", nl["settings_suppliers"])}
    listed = {"mailbox": "", "folders": {}}
    for how, damage in broken.items():
        path.write_text(json.dumps(good, indent=4), encoding="utf-8")
        by_hand(path, first_cases_review=4)                    # a change by hand that the tab shows before the file breaks
        assert (opened(page)["line"], opened(page)["first_cases_review"]) == ("", "4"), how
        damage()
        before = path.read_bytes() if path.exists() else None
        line = lines.get(how, unreadable("nl"))
        shown = opened(page)
        assert shown["line"] == f"**{line}**" and "secret-xyz" not in line and "tien" not in line, how
        assert (shown["price_pct"], shown["buyer_name"]) == ("5", "A. Buyer"), how                # the settings in use, not those of the start
        assert shown["first_cases_review"] == "4", how                                             # also a value the tab only read, never saved
        assert ["A BV", "a@a.example.org", "5", nl["settings_tick"], ""] in shown["table"]["data"], how
        values = [shown[field] for field in BUYER_FIELDS]
        values[BUYER_FIELDS.index("digest_time")] = "08:00"
        refused = save(page, values, shown["filled"])
        assert (refused["note"], refused["line"]) == (line, f"**{line}**") and refused["digest_time"] == SAME, how
        assert save(page, [shown[field] for field in BUYER_FIELDS], shown["filled"])["note"] == line, how       # also with nothing changed
        refused = save_supplier(page, NEW_SUPPLIER, name="B BV", lead_time="6")
        assert (refused["note"], refused["line"], refused["table"]) == (line, f"**{line}**", SAME), how
        assert save_supplier(page, "S07", lead_time="12")["note"] == line and remove_supplier(page, "S07")["note"] == line, how
        assert handler(page, "save_settings")("", "", "", None, "file", listed, "nl")[0] == line, how
        assert handler(page, "save_reader")("parser", "nl")[1] == line, how
        assert handler(page, "save_reader")("llm", "nl")[1] == line, how                      # the line, before the need of the IDs
        assert (path.read_bytes() if path.exists() else None) == before, how
    assert unreadable("nl") == ("Het instellingenbestand kan niet worden gelezen. Hieronder staan de instellingen die de assistent nu gebruikt. "
                                "Opslaan kan pas weer als het bestand is hersteld.")
    assert unreadable("en", "Digest") == ("The settings file cannot be read: check Digest. Below are the settings the aid uses now. "
                                          "Saving works again once the file is repaired.")
    path.write_text(json.dumps(good, indent=4), encoding="utf-8")                              # repaired: the line goes and a save works
    assert opened(page, "en")["line"] == "" and changed(page, digest_time="08:00")["note"] == nl["settings_buyer_saved"]


def test_a_file_that_is_locked_refuses_a_save_with_the_same_line(tmp_path, madeup_out, monkeypatch):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    shown, before, read = opened(page), path.read_bytes(), Path.read_bytes

    def locked(self):
        if self.name == "settings.json":
            raise PermissionError("made-up: the file is open in another program")
        return read(self)

    values = [shown[field] for field in BUYER_FIELDS]
    values[BUYER_FIELDS.index("buyer_name")] = "A. Buyer"
    with monkeypatch.context() as while_locked:
        while_locked.setattr(Path, "read_bytes", locked)
        assert save(page, values, shown["filled"])["note"] == unreadable("nl") and opened(page)["line"] == f"**{unreadable('nl')}**"
        assert save_supplier(page, NEW_SUPPLIER, name="B BV", lead_time="6")["note"] == unreadable("nl")
    assert path.read_bytes() == before and "made-up" not in unreadable("nl")
    assert save(page, values, shown["filled"])["note"] == STRINGS["nl"]["settings_buyer_saved"]      # the file free again: the same save lands


def test_a_save_that_cannot_write_the_file_says_so_by_the_class_of_the_error(tmp_path, madeup_out, monkeypatch):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    before = path.read_bytes()

    def refused(source, target):
        raise PermissionError("made-up: the file is open in another program")

    monkeypatch.setattr(os, "replace", refused)
    failed = STRINGS["nl"]["settings_failed"].format(code="PermissionError")
    assert changed(page, buyer_name="A. Buyer")["note"] == failed and save_supplier(page, "S07", lead_time="12")["note"] == failed
    assert remove_supplier(page, "S07")["note"] == failed and "made-up" not in failed
    assert path.read_bytes() == before and sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".")) == []


def test_a_field_of_the_file_that_is_not_valid_is_named_by_its_label_on_the_tab(tmp_path, madeup_out):
    """The labels of the eleven fields and of the four supplier settings (FIELD_LABELS): never a setting's name for a field of the tab."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl = STRINGS["nl"]
    by_hand(path, lead_time_tolerance_days="week-xyz", review_suppliers="Acme-xyz", supplier_emails={"S07": 7}, authorization_limit="x")
    line = opened(page)["line"]
    assert line == f"**{unreadable('nl', nl['settings_field:lead_time_tolerance_days'], nl['settings_suppliers'], 'authorization_limit')}**"
    assert "xyz" not in line and "supplier_emails" not in line and "review_suppliers" not in line and "S07" not in line


def test_a_supplier_is_added_changed_and_removed_across_the_four_settings(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl = STRINGS["nl"]
    added = save_supplier(page, NEW_SUPPLIER, name=" New BV ", email="info@new.example.org", lead_time="14", review=True, off=True)
    assert (added["note"], added["line"]) == (nl["settings_buyer_saved"], "")
    data = held(path)
    assert (data["supplier_emails"]["New BV"], data["supplier_lead_times"]["New BV"]) == ("info@new.example.org", 14)
    assert "New BV" in data["review_suppliers"] and data["messages_off_suppliers"] == ["New BV"]
    assert ["New BV", "info@new.example.org", "14", nl["settings_tick"], nl["settings_tick"]] in added["table"]["data"]
    assert ("New BV", "New BV") in added["choice"]["choices"] and added["choice"]["value"] == "New BV"
    assert (added["name"]["value"], added["name"]["interactive"], added["email"], added["lead_time"], added["review"], added["off"]) == (
        "New BV", False, "info@new.example.org", "14", True, True)                           # the form shows the supplier as the file holds it
    assert added["supplier_filled"] == {"name": "New BV", "email": "info@new.example.org", "lead_time": "14", "review": True, "off": True}
    changed_ = save_supplier(page, "S07", name="ignored", email="", lead_time="021")         # an existing one keeps its name
    data = held(path)
    assert changed_["note"] == nl["settings_buyer_saved"] and "S07" not in data["supplier_emails"] and data["supplier_lead_times"]["S07"] == 21
    assert "ignored" not in json.dumps(data) and list(data["supplier_lead_times"]) == ["S07", "S01", "New BV"]      # changed in its place
    assert (changed_["lead_time"], changed_["email"], changed_["choice"]["value"]) == ("21", "", "S07")
    off = save_supplier(page, "New BV", off=False)                                           # the tick "no e-mails" taken off again
    assert off["note"] == nl["settings_buyer_saved"] and held(path)["messages_off_suppliers"] == [] and "New BV" in held(path)["review_suppliers"]
    zero = save_supplier(page, "S01", lead_time="0")                                         # a lead time of 0 days is a lead time
    assert held(path)["supplier_lead_times"]["S01"] == 0 and ["S01", "", "0", "", ""] in zero["table"]["data"] and zero["lead_time"] == "0"
    assert choose(page, "S01")["lead_time"] == "0"
    data = held(path)
    refused = save_supplier(page, NEW_SUPPLIER, "en", name="", email="no-address-xyz", lead_time="400")
    en = STRINGS["en"]
    assert refused["note"] == en["settings_buyer_refused"].format(fields="; ".join([
        f"{en['settings_supplier_name']}: {en['settings_problem:supplier_name']}", f"{en['settings_supplier_email']}: {en['settings_problem:address']}",
        f"{en['settings_supplier_lead_time']}: {en['settings_problem:days']}"])) and "no-address-xyz" not in refused["note"]
    assert held(path) == data and refused["table"] == SAME and refused["email"] == SAME
    name = "N" * 80                                                                          # a name of 80 characters is taken, one of 81 is not
    assert save_supplier(page, NEW_SUPPLIER, name=name, lead_time="1")["note"] == nl["settings_buyer_saved"] and name in held(path)["supplier_lead_times"]
    no_name = nl["settings_buyer_refused"].format(fields=f"{nl['settings_supplier_name']}: {nl['settings_problem:supplier_name']}")
    data = held(path)
    assert save_supplier(page, NEW_SUPPLIER, name="N" * 81, lead_time="1")["note"] == no_name
    assert save_supplier(page, NEW_SUPPLIER, name=NEW_SUPPLIER, lead_time="1")["note"] == no_name and held(path) == data      # the dropdown's own value
    removed = remove_supplier(page, "New BV")
    data = held(path)
    assert removed["note"] == nl["settings_buyer_saved"] and all("New BV" not in data[field] for field in SUPPLIER_FIELDS)
    assert "New BV" not in [row[0] for row in removed["table"]["data"]] and removed["choice"]["value"] == NEW_SUPPLIER
    assert ("New BV", "New BV") not in removed["choice"]["choices"] and ("S07", "S07") in removed["choice"]["choices"]
    assert (removed["name"]["value"], removed["name"]["interactive"], removed["email"], removed["lead_time"], removed["review"], removed["off"]) == (
        "", True, "", "", False, False) and removed["supplier_filled"] == NEW_FORM          # the form emptied for a new supplier
    assert remove_supplier(page, NEW_SUPPLIER)["note"] == nl["settings_supplier_choose"]
    emptied = save_supplier(page, "S07", email="", lead_time="")                             # left without any value: in none of the four
    assert emptied["note"] == nl["settings_buyer_saved"] and "S07" not in [row[0] for row in emptied["table"]["data"]]
    assert emptied["choice"]["value"] == NEW_SUPPLIER and emptied["supplier_filled"] == NEW_FORM


def test_a_supplier_save_changes_the_parts_the_buyer_changed_and_a_listed_one_untouched_writes_nothing(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl = STRINGS["nl"]
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    before = path.read_bytes()
    untouched = save_supplier(page, "S07")
    assert untouched["note"] == nl["settings_buyer_unchanged"] and path.read_bytes() == before
    assert path.stat().st_mtime_ns == 1_700_000_000_000_000_000 and untouched["table"] == SAME
    chosen = choose(page, "S07")                                                             # the form of S07 stays open while the file changes
    by_hand(path, supplier_lead_times={"S07": 99, "S01": 10}, supplier_emails={"S07": "hand@s07.example.org", "S09": "s09@example.org"})
    result = named(SUPPLIER_SAVED, handler(page, "save_supplier")("S07", "S07", chosen["email"], chosen["lead_time"], True, False,
                                                                  chosen["supplier_filled"], "nl"))           # only the tick is set
    data = held(path)
    assert result["note"] == nl["settings_buyer_saved"] and data["review_suppliers"] == ["Acme", "S07"]
    assert data["supplier_lead_times"] == {"S07": 99, "S01": 10} and data["supplier_emails"] == {"S07": "hand@s07.example.org", "S09": "s09@example.org"}
    assert (result["email"], result["lead_time"], result["review"]) == ("hand@s07.example.org", "99", True)       # the form shows the file
    assert ["S09", "s09@example.org", "", "", ""] in result["table"]["data"]
    text = path.read_text(encoding="utf-8")                                                  # only the one setting was written
    assert text.count('"S07"') == 3 and '"supplier_emails": {\n        "S07": "hand@s07.example.org",\n        "S09": "s09@example.org"\n    }' in text


def test_a_name_that_is_already_listed_is_not_saved_as_a_new_supplier(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **(BUYER | {"review_suppliers": ["Acme", "S07"], "messages_off_suppliers": ["S07"]}))
    before = path.read_bytes()
    for typed in ("S07", "  S07 "):
        result = save_supplier(page, NEW_SUPPLIER, name=typed, email="new@s07.example.org")
        assert result["note"] == STRINGS["nl"]["settings_supplier_listed"] and path.read_bytes() == before
        assert result["choice"]["value"] == NEW_SUPPLIER and ("S07", "S07") in result["choice"]["choices"]
    assert STRINGS["nl"]["settings_supplier_listed"] == ("Niet opgeslagen: er staat al een leverancier met deze naam in de lijst. Kies die "
                                                         "leverancier bij \"Leverancier\" om hem te wijzigen.")
    assert STRINGS["en"]["settings_supplier_listed"] == ("Not saved: a supplier with this name is already listed. Choose that supplier under "
                                                         "\"Supplier\" to change it.")
    by_hand(path, supplier_lead_times={"S07": 10, "S01": 10, "Later BV": 3})                 # listed in the file after the tab was filled
    before = path.read_bytes()
    result = save_supplier(page, NEW_SUPPLIER, name="Later BV", email="later@example.org")
    assert result["note"] == STRINGS["nl"]["settings_supplier_listed"] and path.read_bytes() == before
    assert ("Later BV", "Later BV") in result["choice"]["choices"] and ["Later BV", "", "3", "", ""] in result["table"]["data"]
    assert save_supplier(page, NEW_SUPPLIER, name="s07", lead_time="3")["note"] == STRINGS["nl"]["settings_buyer_saved"]      # another name


def test_a_new_supplier_with_a_name_and_nothing_else_is_not_saved(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    before = path.read_bytes()
    result = save_supplier(page, NEW_SUPPLIER, name="Alleen Naam BV", email="  ", lead_time="")
    assert result["note"] == STRINGS["nl"]["settings_supplier_empty"] == (
        "Niet opgeslagen: een leverancier heeft een e-mailadres, een levertijd of een vinkje nodig.")
    assert STRINGS["en"]["settings_supplier_empty"] == "Not saved: a supplier needs an e-mail address, a lead time or a tick."
    assert path.read_bytes() == before and path.stat().st_mtime_ns == 1_700_000_000_000_000_000 and result["table"] == SAME
    for one in ({"email": "a@example.org"}, {"lead_time": "0"}, {"review": True}, {"off": True}):      # one of them is enough
        assert save_supplier(page, NEW_SUPPLIER, name=f"Met {next(iter(one))} BV", **one)["note"] == STRINGS["nl"]["settings_buyer_saved"]
    assert len(supplier_rows(load_settings(path))) == 3 + 4


def test_a_listed_supplier_whose_name_has_a_space_around_it_is_changed_and_removed_as_the_file_has_it(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **(BUYER | {"supplier_lead_times": {" Spaced ": 5, "S01": 10}, "supplier_emails": {}}))
    assert (" Spaced ", " Spaced ") in opened(page)["choice"]["choices"]
    result = save_supplier(page, " Spaced ", email="a@spaced.example.com", lead_time="6")
    data = held(path)
    assert result["note"] == STRINGS["nl"]["settings_buyer_saved"] and data["supplier_emails"] == {" Spaced ": "a@spaced.example.com"}
    assert data["supplier_lead_times"] == {" Spaced ": 6, "S01": 10} and result["choice"]["value"] == " Spaced "
    removed = remove_supplier(page, " Spaced ")
    data = held(path)
    assert removed["note"] == STRINGS["nl"]["settings_buyer_saved"] and data["supplier_lead_times"] == {"S01": 10} and data["supplier_emails"] == {}
    assert [row[0] for row in removed["table"]["data"]] == ["Acme", "S01"]
    assert remove_supplier(page, "Gone BV")["note"] == STRINGS["nl"]["settings_buyer_unchanged"]      # no longer in the file: nothing to take out


def test_a_supplier_chosen_in_the_dropdown_keeps_its_name_as_the_file_had_it_when_the_file_no_longer_holds_it(tmp_path, madeup_out):
    """The dropdown gives a supplier's name as the file had it. When another browser tab took the supplier out of the file meanwhile,
    a save of what the buyer changed (his change wins) adds it again under that same name, never a trimmed one."""
    path, page, made = tab(tmp_path, madeup_out, **(BUYER | {"supplier_lead_times": {" Spaced ": 5, "S01": 10}, "supplier_emails": {}}))
    chosen = choose(page, " Spaced ")
    assert remove_supplier(page, " Spaced ")["note"] == STRINGS["nl"]["settings_buyer_saved"]                 # in the other browser tab
    result = named(SUPPLIER_SAVED, handler(page, "save_supplier")(" Spaced ", " Spaced ", "", "6", False, False, chosen["supplier_filled"], "nl"))
    assert result["note"] == STRINGS["nl"]["settings_buyer_saved"] and held(path)["supplier_lead_times"] == {"S01": 10, " Spaced ": 6}
    assert result["choice"]["value"] == " Spaced " and result["lead_time"] == "6"


def test_the_note_of_a_supplier_save_is_cleared_by_the_buyers_own_choice_only(tmp_path, madeup_out):
    """The dropdown's change, which a save and a removal cause themselves, fills the form and leaves the note alone; the note is
    cleared by the dropdown's input event, the buyer's own choice."""
    gr = import_gradio()
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    [dropdown] = [block for block in page.blocks.values() if isinstance(block, gr.Dropdown) and block.label == STRINGS["nl"]["settings_supplier_choice"]
                  and not (block.elem_id or "").startswith("export-field-")]        # not the supplier's column of the export (Task 39)
    [note] = [block for block in page.blocks.values() if getattr(block, "elem_id", None) == "settings-supplier-note"]
    bound = {(getattr(f.fn, "__name__", ""), event): f for f in page.fns.values() for block, event in f.targets if block == dropdown._id}
    assert sorted(bound) == [("supplier_chosen", "change"), ("supplier_picked", "input")]
    assert note not in bound[("supplier_chosen", "change")].outputs and bound[("supplier_picked", "input")].outputs == [note]
    assert handler(page, "supplier_picked")() == "" and bound[("supplier_picked", "input")].inputs == []
    for name in ("save_supplier", "remove_supplier"):
        [f] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == name]
        assert f.outputs[0] is note and dropdown in f.outputs
    # nothing else writes the note: not the fill of the tab, not the main save, not the switch of the page's language
    assert sorted(getattr(f.fn, "__name__", "") for f in page.fns.values() if note in f.outputs) == ["remove_supplier", "save_supplier",
                                                                                                    "supplier_picked"]
    # and in the handlers themselves: the dropdown's change gives the form and its fill, nothing for a note
    assert len(handler(page, "supplier_chosen")("S07", "nl")) == len(bound[("supplier_chosen", "change")].outputs) == 6


def test_choosing_a_supplier_fills_the_form_with_its_values(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    chosen = choose(page, "S07")
    assert (chosen["name"]["value"], chosen["name"]["interactive"], chosen["email"], chosen["lead_time"], chosen["review"], chosen["off"]) == (
        "S07", False, "sales@s07.example.com", "10", False, False)
    assert chosen["supplier_filled"] == {"name": "S07", "email": "sales@s07.example.com", "lead_time": "10", "review": False, "off": False}
    acme = choose(page, "Acme")
    assert (acme["email"], acme["lead_time"], acme["review"], acme["off"]) == ("", "", True, False)
    new = choose(page, NEW_SUPPLIER)
    assert (new["name"]["value"], new["name"]["interactive"], new["email"], new["lead_time"], new["review"], new["off"]) == ("", True, "", "", False, False)
    assert new["supplier_filled"] == NEW_FORM
    by_hand(path, supplier_lead_times={"S07": 31, "S01": 10})                               # the form is filled from the file as it is now
    assert choose(page, "S07")["lead_time"] == "31"


def test_two_saves_pressed_at_the_same_moment_both_land(tmp_path, madeup_out):
    import threading
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    saved = STRINGS["nl"]["settings_buyer_saved"]
    for round_ in range(25):
        shown, notes, gate = opened(page), {}, threading.Barrier(3)
        values = [shown[field] for field in BUYER_FIELDS]
        values[BUYER_FIELDS.index("buyer_name")] = f"Buyer {round_}"

        def main_save():
            gate.wait()
            notes["main"] = save(page, values, shown["filled"])["note"]

        def supplier_save():
            gate.wait()
            notes["supplier"] = handler(page, "save_supplier")(NEW_SUPPLIER, f"R{round_} BV", "", "4", False, False, None, "nl")[0]

        def supplier_removal():
            gate.wait()
            notes["removal"] = handler(page, "remove_supplier")(f"R{round_ - 1} BV", "nl")[0]

        threads = [threading.Thread(target=target) for target in (main_save, supplier_save, supplier_removal)]
        [thread.start() for thread in threads]
        [thread.join() for thread in threads]
        data = held(path)                                                                    # the file is JSON after every round
        assert notes["main"] == notes["supplier"] == saved and notes["removal"] in (saved, STRINGS["nl"]["settings_buyer_unchanged"]), (round_, notes)
        assert data["buyer_name"] == f"Buyer {round_}" and data["supplier_lead_times"].get(f"R{round_} BV") == 4, round_
        assert f"R{round_ - 1} BV" not in data["supplier_lead_times"], round_
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".")) == []


def test_a_save_changes_what_the_pages_own_close_uses(tmp_path, madeup_out):
    """The supplier's address and the buyer's name saved on the tab are those of the question the page's close sends, at once: the
    save hands them to the page, which does not wait for its own look at the file (the file's time is put back to show it)."""
    from aid.messaging import FileMailer
    from aid.pipeline import run_case
    from aid.review.app import decide
    from aid.sources.upload import open_upload_case
    out = tmp_path / "outbox"
    path = settings_file(tmp_path, **BUYER)
    d = deps(tmp_path, madeup_out).model_copy(update={"settings": load_settings(path),
                                                      "mailer": FileMailer(out, sender="aid@example.com", clock=lambda: datetime(2026, 11, 12, 9))})
    c = open_upload_case(d.settings.cases_root, [madeup_out / "MU07_conf.pdf"], "250107", "confirmation", d.clock())
    run_case(c, d)
    decide(c, "RI1", "ask")
    page = build_app(d, settings_file=path, session=FakeSession())
    for save_it in (lambda: save_supplier(page, "S07", email="orders@s07.example.org"), lambda: changed(page, buyer_name="A. Buyer")):
        opened(page)                                                                        # the page has looked at the file as it is
        times = (path.stat().st_atime_ns, path.stat().st_mtime_ns)
        assert save_it()["note"] == STRINGS["nl"]["settings_buyer_saved"]
        os.utime(path, ns=times)                                                            # the file looks as old as at that look
    assert d.settings.supplier_emails == {"S07": "sales@s07.example.com"}                  # the caller's Deps are left alone
    handler(page, "close")(c.case_id, "nl")
    [eml] = out.glob("*.eml")
    mail = message_from_bytes(eml.read_bytes(), policy=policy.default)
    assert mail["To"] == "orders@s07.example.org" and "A. Buyer" in mail.get_content()


def test_a_change_of_the_file_by_hand_reaches_the_pages_close_and_an_invalid_one_keeps_the_settings(tmp_path, madeup_out):
    from aid.messaging import FileMailer
    from aid.pipeline import run_case
    from aid.review.app import decide
    from aid.sources.upload import open_upload_case
    out = tmp_path / "outbox"
    path = settings_file(tmp_path, **BUYER)
    d = deps(tmp_path, madeup_out).model_copy(update={"settings": load_settings(path),
                                                      "mailer": FileMailer(out, sender="aid@example.com", clock=lambda: datetime(2026, 11, 12, 9))})
    c = open_upload_case(d.settings.cases_root, [madeup_out / "MU07_conf.pdf"], "250107", "confirmation", d.clock())
    run_case(c, d)
    decide(c, "RI1", "ask")
    page = build_app(d, settings_file=path, session=FakeSession())
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(data | {"buyer_name": "B. Hand", "supplier_emails": {"S07": "hand@s07.example.org"}}, indent=4), encoding="utf-8")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))
    handler(page, "opened")("nl", None)                                                     # the review tab opens: the file is read again
    path.write_text(json.dumps(data | {"price_pct": "not-a-number"}, indent=4), encoding="utf-8")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 2_000_000_000))
    shown = opened(page)                                                                    # the settings tab shows the settings in use
    assert shown["buyer_name"] == "B. Hand" and shown["line"] == f"**{unreadable('nl', STRINGS['nl']['settings_field:price_pct'])}**"
    handler(page, "close")(c.case_id, "nl")
    [eml] = out.glob("*.eml")
    mail = message_from_bytes(eml.read_bytes(), policy=policy.default)
    assert mail["To"] == "hand@s07.example.org" and "B. Hand" in mail.get_content()


def test_the_pages_look_at_the_file_when_a_case_is_shown_gives_the_settings_in_use(tmp_path, madeup_out):
    """The review tab looks at the file when it shows a case: what it then read are the settings in use, which the settings tab
    shows once the file can no longer be read."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    by_hand(path, buyer_name="B. Hand", price_pct="4.5")
    handler(page, "show")(None, "nl")                                                       # a case is shown (here none is chosen)
    by_hand(path, price_abs="thirty-xyz")
    shown = opened(page)
    assert (shown["buyer_name"], shown["price_pct"], shown["price_abs"]) == ("B. Hand", "4,5", "25,00")
    assert shown["line"] == f"**{unreadable('nl', STRINGS['nl']['settings_field:price_abs'])}**"


def test_a_file_locked_when_the_page_looks_at_it_is_read_at_the_next_look(tmp_path, madeup_out, monkeypatch):
    """A file that cannot be opened for a moment (another program holds it) is looked at again: the settings the tab then reads are
    the page's own from then on, and they are the settings in use that the tab shows once the file no longer validates."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    by_hand(path, buyer_name="B. Hand", price_pct="4.5")
    read = Path.read_bytes

    def locked(self):
        if self.name == "settings.json":
            raise PermissionError("made-up: the file is open in another program")
        return read(self)

    with monkeypatch.context() as while_locked:
        while_locked.setattr(Path, "read_bytes", locked)
        handler(page, "show")(None, "nl")                                                   # the review tab looks while the file is held
    assert (opened(page)["buyer_name"], opened(page)["line"]) == ("B. Hand", "")             # the tab reads it, free again
    by_hand(path, price_abs="thirty-xyz")
    shown = opened(page)
    assert (shown["buyer_name"], shown["price_pct"]) == ("B. Hand", "4,5") and shown["line"] != ""


def test_a_question_the_buyer_chooses_on_the_review_tab_goes_to_a_supplier_the_aid_asks_nothing_by_itself(tmp_path, madeup_out):
    """What the tick, the column and the help of "no questions by itself" say: the aid's own questions come to the buyer, and a
    question the buyer chooses on the review tab is sent to the supplier (the review tab's behaviour, which this pins)."""
    from aid.messaging import FileMailer
    from aid.pipeline import run_case
    from aid.review.app import decide
    from aid.sources.upload import open_upload_case
    out = tmp_path / "outbox"
    path = settings_file(tmp_path, **(BUYER | {"messages_off_suppliers": ["S11"], "supplier_emails": {"S11": "orders@s11.example.org"},
                                               "supplier_lead_times": {"S11": 10}}))
    d = deps(tmp_path, madeup_out).model_copy(update={"settings": load_settings(path),
                                                      "mailer": FileMailer(out, sender="aid@example.com", clock=lambda: datetime(2026, 11, 12, 9))})
    c = open_upload_case(d.settings.cases_root, [madeup_out / "MU11_conf.pdf"], "250111", "confirmation", d.clock())
    result = run_case(c, d)
    assert result.status == "review" and [item.reason for item in result.review_items] == ["messages off for this supplier"]
    assert not list(out.glob("*.eml"))                                                      # the aid's own question came to the buyer
    decide(c, "RI1", "ask")
    page = build_app(d, settings_file=path, session=FakeSession())
    handler(page, "close")(c.case_id, "nl")
    [eml] = out.glob("*.eml")
    assert message_from_bytes(eml.read_bytes(), policy=policy.default)["To"] == "orders@s11.example.org"
    for language, itself, chosen in (("nl", "uit zichzelf", "zelf op het tabblad \"Beoordelen\" kiest"), ("en", "by itself", "choose yourself on the tab \"Review\"")):
        strings = STRINGS[language]
        assert itself in strings["settings_supplier_messages_off"] and itself in strings["settings_supplier_column:messages_off"]
        assert itself in strings["settings_suppliers_help"] and chosen in strings["settings_suppliers_help"]
        assert "nooit" not in strings["settings_suppliers_help"] and "never" not in strings["settings_suppliers_help"]


def test_the_helps_say_what_the_rules_do(tmp_path):
    """The help of the delivery-date days, of the price and of the first cases against the routing and the pipeline themselves."""
    from aid.comparison import Findings, Flag, Match
    from aid.pipeline import _closed_cases
    from aid.routing import CaseContext, route
    from aid.schema import DocValue

    def value(text):
        return DocValue(text=text, value=text, source=None)

    def context(lead_time):
        return CaseContext(supplier="S14", po_number="250114", version=1, po_date={"1": "2026-11-16"}, po_item_code={"1": "KM-1401"},
                           po_above_authorization=False, supplier_lead_time_days=lead_time, cases_seen_for_supplier=9, document_path="x.pdf",
                           kind="confirmation")

    def date(doc: str, lead_time, days: int) -> str:
        flag = Flag(flag_id="F1", field="delivery_date", po_line_ids=["1"], doc_line_ids=["1"], po_value=value("2026-11-16"), doc_value=value(doc),
                    detail="later" if doc > "2026-11-16" else "earlier")
        settings = SETTINGS.model_copy(update={"lead_time_tolerance_days": days, "authorization_limit": None})
        return route(Findings(version=1, flags=[flag], matches=[], uncompared=[]), context(lead_time), settings)[0].route

    # counted from the delivery date of the PO's line (16 November), up to and including the lead time (10) plus the days (7): 3 December
    assert (date("2026-12-03", 10, 7), date("2026-12-04", 10, 7)) == ("AUTO", "REVIEW")
    assert (date("2026-11-17", None, 7), date("2026-01-05", None, 7), date("2026-01-05", 10, 0)) == ("REVIEW", "AUTO", "AUTO")
    nl, en = STRINGS["nl"]["settings_help:lead_time_tolerance_days"], STRINGS["en"]["settings_help:lead_time_tolerance_days"]
    assert "geteld vanaf de leverdatum van de regel op de inkooporder" in nl and "counted from the delivery date of the purchase order's line" in en
    assert "Een eerdere leverdatum neemt de assistent over, ook zonder bekende levertijd" in nl and "de marge" in nl.lower()
    assert "An earlier delivery date is taken over, also without a known lead time" in en and "the margin" in en.lower()
    assert "na de datum op de inkooporder" not in nl and "after the purchase order's date" not in en
    price = Flag(flag_id="F1", field="price", po_line_ids=["1"], doc_line_ids=["1"], po_value=value("10.00"), doc_value=value("10.15"), detail="different")
    quantity = Match(field="quantity", po_line_ids=["1"], doc_line_ids=["1"], value="100")
    action = route(Findings(version=1, flags=[price], matches=[quantity], uncompared=[]), context(10), SETTINGS.model_copy(update={"authorization_limit": None}))[0]
    assert [op.kind for op in action.ops] == ["po_line_price", "article_price"]             # the article's price is changed too
    assert "prijs van het artikel" in STRINGS["nl"]["settings_help:price_pct"] and "the article's price" in STRINGS["en"]["settings_help:price_pct"]
    assert "drempel" in STRINGS["nl"]["settings_help:price_pct"] and "drempel" in STRINGS["nl"]["settings_help:price_abs"]
    assert "threshold" in STRINGS["en"]["settings_help:price_pct"] and "threshold" in STRINGS["en"]["settings_help:price_abs"]
    for name, status in (("a", "closed"), ("b", "closed"), ("c", "review"), ("d", "waiting"), ("e", "failed")):
        (tmp_path / name / "steps").mkdir(parents=True)
        (tmp_path / name / "steps" / "08_result.json").write_text(json.dumps({"status": status, "supplier": "S01"}), encoding="utf-8")
    assert _closed_cases(tmp_path, "S01", "this case") == 2                                 # the closed cases count, no other
    assert STRINGS["nl"]["settings_help:first_cases_review"].startswith("Tot zoveel zaken van een leverancier zijn afgesloten, komt elke zaak van die "
                                                                        "leverancier naar u") and "0 zet dit uit" in STRINGS["nl"]["settings_help:first_cases_review"]
    assert STRINGS["en"]["settings_help:first_cases_review"].startswith("Until this many cases of a supplier are closed, every case of that supplier "
                                                                        "comes to you") and "0 turns this off" in STRINGS["en"]["settings_help:first_cases_review"]


def test_the_helps_of_the_name_the_address_and_the_language_say_what_the_messages_do():
    """The language of the aid, the name under "Inkoop" or "Purchasing" and the copy to the buyer hold for the request to the approver
    as for the questions to suppliers, and the signature follows the language of the aid, whatever the page's language."""
    from aid.execution.ops import WriteOp
    from aid.messaging import render_reapproval
    from aid.routing import CaseContext
    context = CaseContext(supplier="S08", po_number="250108", version=1, po_date={}, po_above_authorization=True, supplier_lead_time_days=10,
                          cases_seen_for_supplier=9, document_path="x.pdf", kind="confirmation")
    change = [WriteOp(kind="po_line_price", po_number="250108", version=1, line_id="1", old_value="12.50", new_value="12.70")]
    for language, under in (("nl", "Inkoop"), ("en", "Purchasing")):
        for name, end in (("A. Buyer", f"\n{under}\nA. Buyer\n"), ("", f"\n{under}\n")):
            request = render_reapproval(change, context, SETTINGS.model_copy(update={"language": language, "buyer_name": name}))
            assert (request.to, request.cc) == ([SETTINGS.approver_email], [SETTINGS.buyer_email]) and request.body.endswith(end), language
    nl, en = STRINGS["nl"], STRINGS["en"]
    # both signatures, on the page of either language, each with the language of the aid that gives it
    assert nl["settings_help:buyer_name"].startswith("Deze naam staat onder \"Inkoop\", of onder \"Purchasing\" als de taal van de assistent English is,")
    assert en["settings_help:buyer_name"].startswith("This name stands under \"Purchasing\", or under \"Inkoop\" when the language of the aid is Nederlands,")
    assert nl["settings_help:buyer_name"].endswith("leeg: alleen \"Inkoop\" of \"Purchasing\".")
    assert en["settings_help:buyer_name"].endswith("empty: \"Purchasing\" or \"Inkoop\" alone.")
    assert "fiatteur" in nl["settings_help:buyer_email"] and "approver" in en["settings_help:buyer_email"]
    assert "fiatteur" in nl["settings_help:language"] and "approver" in en["settings_help:language"]


def test_one_name_per_thing_on_the_tab():
    nl, en = STRINGS["nl"], STRINGS["en"]
    assert (nl["settings_supplier_new"], en["settings_supplier_new"]) == ("Leverancier toevoegen", "Add a supplier")
    assert nl["reason:new supplier"] == "Nieuwe leverancier" and "\"Nieuwe leverancier\"" in nl["settings_help:first_cases_review"]
    assert "\"New supplier\"" in en["settings_help:first_cases_review"]
    mine = {key: text for key, text in nl.items() if key.startswith(("settings_group", "settings_field", "settings_help", "settings_supplier",
                                                                     "settings_buyer", "settings_problem", "settings_limit", "settings_file"))}
    assert not [key for key, text in mine.items() if "bij u" in text or "naar mij" in text or "webwinkel" in text or "Engels" in text]
    assert "naar u" in nl["settings_supplier_review"] and nl["settings_supplier_column:review"] == "Altijd naar u"
    assert "webshop" in nl["settings_help:language"] and "webshop" in en["settings_help:language"]
    for strings in (nl, en):                                   # the choices of the aid's language read as the page's own switch does
        assert (strings["settings_language:nl"], strings["settings_language:en"]) == ("Nederlands", "English")
    assert nl["settings_connections"] == "Koppelingen: het Microsoft-postvak en de AI-dienst"
    assert en["settings_connections"] == "Connections: the Microsoft mailbox and the AI service"


def test_the_buyers_part_is_wired_as_the_page_shows_it(tmp_path, madeup_out):
    """The built page itself: each handler's trigger, inputs and outputs, each field's help, the headings in their order and the
    table that cannot be edited."""
    gr = import_gradio()
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl = STRINGS["nl"]
    [settings] = [block for block in page.blocks.values() if isinstance(block, gr.Tab) and block.label == nl["settings_tab"]]
    [folded] = [block for block in settings.children if isinstance(block, gr.Accordion) and block.label == nl["settings_connections"]]
    [exact] = [block for block in settings.children if isinstance(block, gr.Accordion) and block.label == nl["exact_title"]]

    def inside(block) -> list:
        return [block, *[found for child in getattr(block, "children", []) for found in inside(child)]]
    buyers = [block for block in inside(settings) if block not in inside(folded) and block not in inside(exact)]
    [fill] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "buyer_opened"]
    states = {id(fill.outputs[OPENED.index("filled")]): "filled", id(fill.outputs[-1]): "supplier filled"}
    assert all(isinstance(fill.outputs[OPENED.index(name)], gr.State) for name in ("filled", "supplier_filled"))

    def name(component) -> str:
        """A component as the buyer sees it: its label, a button's text, the id of a note, the headers of the table."""
        if id(component) in states:
            return states[id(component)]
        if isinstance(component, gr.Button):
            return f"button {component.value}"
        if isinstance(component, gr.Dataframe):
            return "table"
        return getattr(component, "label", None) or getattr(component, "elem_id", None) or type(component).__name__

    def bound(fn_name: str) -> tuple:
        [f] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == fn_name]
        return (sorted((name(page.blocks[block]) if block != page._id else "page", event) for block, event in f.targets),
                [name(component) for component in f.inputs], [name(component) for component in f.outputs])

    fields = [nl[f"settings_field:{field}"] for field in BUYER_FIELDS]
    supplier_form = [nl["settings_supplier_name"], nl["settings_supplier_email"], nl["settings_supplier_lead_time"], nl["settings_supplier_review"],
                     nl["settings_supplier_messages_off"]]
    page_language, choice = STRINGS["nl"]["language"], nl["settings_supplier_choice"]
    line, note, supplier_note = "settings-file-line", "settings-buyer-note", "settings-supplier-note"
    assert bound("buyer_opened") == ([(nl["settings_tab"], "select"), ("page", "load")], [page_language],
                                     [line, *fields, "filled", "table", choice, *supplier_form, "supplier filled"])
    assert bound("save_buyer") == ([(f"button {nl['settings_buyer_save']}", "click")], [*fields, "filled", page_language], [note, line, *fields, "filled"])
    assert bound("supplier_chosen") == ([(choice, "change")], [choice, page_language], [*supplier_form, "supplier filled"])
    assert bound("supplier_picked") == ([(choice, "input")], [], [supplier_note])
    assert bound("save_supplier") == ([(f"button {nl['settings_supplier_save']}", "click")], [choice, *supplier_form, "supplier filled", page_language],
                                      [supplier_note, line, "table", choice, *supplier_form, "supplier filled"])
    assert bound("remove_supplier") == ([(f"button {nl['settings_supplier_remove']}", "click")], [choice, page_language],
                                        [supplier_note, line, "table", choice, *supplier_form, "supplier filled"])
    relabel = bound("buyer_relabel")
    assert relabel[0] == [(page_language, "change")] and line in relabel[2] and set(fields) <= set(relabel[2])
    # what the page shows when it is built: each field with its help, the headings in their order, the table read-only
    shown = {block.label: block for block in buyers if getattr(block, "label", None)}
    assert [shown[label].info for label in fields] == [nl[f"settings_help:{field}"] for field in BUYER_FIELDS]
    assert [type(shown[label]).__name__ for label in fields] == ["Textbox"] * 6 + ["Radio", "Textbox", "Textbox", "Radio", "Textbox"]
    assert shown[nl["settings_field:digest"]].choices == [(nl["settings_digest:daily"], "daily"), (nl["settings_digest:per_case"], "per_case")]
    assert shown[nl["settings_field:language"]].choices == [("Nederlands", "nl"), ("English", "en")]
    headings = [block.value for block in buyers if isinstance(block, gr.Markdown) and str(block.value).startswith("### ")]
    assert headings == ["### Wat de assistent zelf mag afhandelen", "### Vragen aan leveranciers", "### Dagoverzicht en taal", "### Fiattering",
                        "### Leveranciers"]
    order = [block for block in buyers if isinstance(block, (gr.Markdown, gr.Textbox, gr.Radio, gr.Button, gr.Dataframe, gr.Dropdown, gr.Checkbox))]
    labels = [name(block) if not isinstance(block, gr.Markdown) or block.elem_id else block.value for block in order]
    assert labels == [line, headings[0], *fields[:4], headings[1], *fields[4:6], headings[2], *fields[6:10], headings[3], fields[10], nl["settings_limit"],
                      f"button {nl['settings_buyer_save']}", note, headings[4], nl["settings_suppliers_help"], "table", choice, *supplier_form,
                      f"button {nl['settings_supplier_save']}", f"button {nl['settings_supplier_remove']}", supplier_note]
    [table] = [block for block in buyers if isinstance(block, gr.Dataframe)]
    assert table.interactive is False and settings.children[-1] is folded and settings.children[0].elem_id == line


def test_switching_the_language_relabels_the_buyers_part_and_keeps_what_is_chosen(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    shown_en = handler(page, "buyer_relabel")("en", "per_case", "nl", "S07")
    flat = json.dumps(shown_en, default=str)
    assert STRINGS["en"]["settings_field:price_pct"] in flat and STRINGS["en"]["settings_help:price_pct"] in flat
    assert STRINGS["en"]["settings_group:handled"] in flat and STRINGS["en"]["settings_supplier_save"] in flat
    assert STRINGS["nl"]["settings_field:price_pct"] not in flat
    updates = [update for update in shown_en if isinstance(update, dict)]
    [digest] = [update for update in updates if update.get("label") == STRINGS["en"]["settings_field:digest"]]
    assert digest["value"] == "per_case" and digest["choices"] == [(STRINGS["en"]["settings_digest:daily"], "daily"),
                                                                    (STRINGS["en"]["settings_digest:per_case"], "per_case")]
    [choice] = [update for update in updates if update.get("label") == STRINGS["en"]["settings_supplier_choice"]]
    assert choice["value"] == "S07" and choice["choices"][0] == (STRINGS["en"]["settings_supplier_new"], NEW_SUPPLIER)
    assert shown_en[0] == ""                                                                 # the line of a file that cannot be read: none
    by_hand(path, price_pct="x")
    assert handler(page, "buyer_relabel")("en", "daily", "nl", NEW_SUPPLIER)[0] == f"**{unreadable('en', STRINGS['en']['settings_field:price_pct'])}**"


def test_the_connections_are_folded_away_under_one_heading_and_still_work(tmp_path, madeup_out):
    gr = import_gradio()
    path, page, made = tab(tmp_path, madeup_out, app=FakeApp(accounts=[USERNAME]), **SIX)
    [settings] = [block for block in page.blocks.values() if isinstance(block, gr.Tab) and block.label == STRINGS["nl"]["settings_tab"]]
    [folded] = [block for block in settings.children if isinstance(block, gr.Accordion) and block.label == STRINGS["nl"]["settings_connections"]]
    assert folded.label == STRINGS["nl"]["settings_connections"] == "Koppelingen: het Microsoft-postvak en de AI-dienst"
    assert folded.open is False and type(settings.children[-1]).__name__ == "Accordion"

    def inside(block) -> list:
        return [block, *[found for child in getattr(block, "children", []) for found in inside(child)]]
    labels = {getattr(block, "label", None) for block in inside(folded)}
    assert {STRINGS["nl"]["settings_tenant"], STRINGS["nl"]["settings_folder"], STRINGS["nl"]["settings_ai_reader"]} <= labels
    buyer_labels = {getattr(block, "label", None) for block in inside(settings) if block not in inside(folded)}
    assert {STRINGS["nl"][f"settings_field:{field}"] for field in BUYER_FIELDS} <= buyer_labels
    assert handler(page, "settings_opened")("nl")[0] == TENANT_ID and handler(page, "ai_opened")("nl") == "parser"


def test_the_buyers_part_shows_no_rule_code_and_no_setting_name_and_says_the_limit_is_set_at_the_installation(tmp_path, madeup_out):
    gr = import_gradio()
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    texts = [str(block.value) for block in page.blocks.values() if isinstance(block, gr.Markdown)]
    for language in ("nl", "en"):
        strings = STRINGS[language]
        mine = [value for key, value in strings.items() if key.startswith(("settings_group", "settings_field", "settings_help", "settings_supplier",
                                                                            "settings_buyer", "settings_problem", "settings_limit", "settings_file"))]
        assert mine and not [text for text in mine if CODE.search(text)], language
        assert all(strings[f"settings_help:{field}"] for field in BUYER_FIELDS)
        assert not [text for text in mine if "—" in text or "–" in text], language          # no dash as punctuation
    assert STRINGS["nl"]["settings_limit"] in texts and "installatie" in STRINGS["nl"]["settings_limit"]
    assert [STRINGS["nl"][f"settings_group:{group}"] for group in ("handled", "questions", "digest", "approval")] == [
        "Wat de assistent zelf mag afhandelen", "Vragen aan leveranciers", "Dagoverzicht en taal", "Fiattering"]
    assert f"### {STRINGS['nl']['settings_suppliers']}" in texts and STRINGS["nl"]["settings_suppliers"] == "Leveranciers"
    assert (STRINGS["nl"]["settings_buyer_save"], STRINGS["nl"]["settings_supplier_save"], STRINGS["nl"]["settings_supplier_remove"]) == (
        "Opslaan", "Leverancier opslaan", "Leverancier verwijderen")


def test_on_a_public_link_and_without_a_file_the_buyers_part_is_not_there_either(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, share=True, **BUYER)
    names = {getattr(f.fn, "__name__", "") for f in page.fns.values()}
    mine = {"buyer_opened", "save_buyer", "supplier_chosen", "supplier_picked", "save_supplier", "remove_supplier", "buyer_relabel"}
    assert not names & mine
    labels = {getattr(block, "label", None) for block in page.blocks.values()}
    assert not labels & {STRINGS["nl"][f"settings_field:{field}"] for field in BUYER_FIELDS} and STRINGS["nl"]["settings_supplier_choice"] not in labels
    names = {getattr(f.fn, "__name__", "") for f in build_app(deps(tmp_path / "nofile", madeup_out)).fns.values()}
    assert not names & mine
    own = tab(tmp_path / "own", madeup_out, **BUYER)[1]                                     # the page of this PC has them all
    assert mine <= {getattr(f.fn, "__name__", "") for f in own.fns.values()}


def test_an_address_is_one_address_and_nothing_after_it():
    assert is_address("buyer@example.com") and is_address("a.b+c@sub.example.org")
    assert not [text for text in ("buyer@example.com more", "buyer@example.com\nnext", " buyer@example.com", "a@b", "buyer@@example.com", "")
                if is_address(text)]


# --- the page answers its own host only ---

def answers(page, share: bool, requests: list[tuple], port: int = 7861) -> list[tuple]:
    """What the application gradio makes of the page with the review command's launch options answers to each request (a Host
    header or None, a method, a path, "http" or "websocket", and optionally more headers), handed over as a server listening on
    127.0.0.1 and `port` does: (the status, the body) of each, for a websocket (the type of the first message, its code). The
    application is made and asked inside one event loop, which is closed (gradio leaves a loop it made itself open; see
    aid/review/page.py)."""
    import asyncio

    import aid.review.__main__ as review
    from gradio.routes import App

    async def ask() -> list[tuple]:
        application = App.create_app(page, app_kwargs=review.launch_options(page, share)["app_kwargs"])
        given = []
        for host, method, path, kind, *more in requests:
            sent = []
            headers = ([(b"host", host.encode("latin-1"))] if host is not None else []) + [
                (name.lower().encode(), value.encode()) for name, value in (more[0] if more else {}).items()]

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False} if kind == "http" else {"type": "websocket.connect"}

            async def send(message):
                sent.append(message)

            scope = {"type": kind, "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http" if kind == "http" else "ws",
                     "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "", "client": ("127.0.0.1", 50000),
                     "server": ("127.0.0.1", port), "headers": headers, "subprotocols": []}
            await application(scope, receive, send)
            given.append((sent[0]["type"], sent[0].get("code")) if kind == "websocket" else
                         (sent[0]["status"], b"".join(message.get("body", b"") for message in sent[1:])))
        return given

    return asyncio.run(ask())


def test_a_request_that_names_another_host_is_refused(tmp_path, madeup_out):
    """The Host check as the review command hands it to the page's launch, in the application gradio makes with it."""
    import aid.review.__main__ as review
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    before = path.read_bytes()
    options = review.launch_options(page, share=False)
    assert (options["server_name"], options["share"], options["ssr_mode"]) == ("127.0.0.1", False, False)
    own = ["127.0.0.1:7861", "127.0.0.1", "localhost:7861", "localhost", "LOCALHOST:7861"]
    for (status, body), host in zip(answers(page, False, [(host, "GET", "/config", "http") for host in own]), own):
        assert status == 200 and b"Instellingen" in body, host                              # the page's own host: its config
    other = ["example.org", "example.org:7861", "evil.example", "127.0.0.1.evil.example", "127.0.0.1:1", "localhost:7862", "[::1]:7861",
             "127.0.0.1:7861@evil.example", "0.0.0.0:7861", "", None]
    asked = [(host, method, target, "http") for host in other
             for method, target in (("GET", "/config"), ("GET", "/"), ("POST", "/gradio_api/call/save_buyer"), ("POST", "/gradio_api/queue/join"),
                                    ("OPTIONS", "/gradio_api/queue/join"))]
    for answer, request in zip(answers(page, False, asked), asked):
        assert answer == (400, b"Invalid host header"), request
    assert answers(page, False, [("example.org", "GET", "/queue/join", "websocket")]) == [("websocket.close", 1008)]
    # gradio's own CORS layer stands before the check and answers a browser's preflight itself, running nothing of the page; the
    # request that the preflight announces is refused like any other
    cross = {"Origin": "http://example.org", "Access-Control-Request-Method": "POST", "Content-Type": "application/json"}
    preflight, announced = answers(page, False, [("example.org", "OPTIONS", "/gradio_api/call/save_buyer", "http", cross),
                                                 ("example.org", "POST", "/gradio_api/call/save_buyer", "http", cross)])
    assert preflight == (200, b"OK") and announced == (400, b"Invalid host header")
    assert path.read_bytes() == before                                                      # nothing was written


def test_on_a_public_link_the_links_host_passes_too(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, share=True, **BUYER)

    def statuses(page, share, *hosts) -> list[int]:
        return [status for status, body in answers(page, share, [(host, "GET", "/config", "http") for host in hosts])]

    assert statuses(page, True, "abc123.gradio.live", "127.0.0.1:7861") == [400, 200]      # no link yet
    page.share_url = "https://abc123.gradio.live"
    assert statuses(page, True, "abc123.gradio.live", "ABC123.gradio.live:443", "other.gradio.live", "127.0.0.1:7861") == [200, 200, 400, 200]
    private = tab(tmp_path / "private", madeup_out, **BUYER)[1]                             # without --share a link's host is refused
    private.share_url = "https://abc123.gradio.live"
    assert statuses(private, False, "abc123.gradio.live", "localhost:7861") == [400, 200]


def test_the_page_tells_the_browser_to_keep_none_of_its_files(tmp_path, madeup_out):
    """The page images of a document and an uploaded file are served on the page library's file route: every answer there carries
    Cache-Control: no-store, so that the browser keeps no copy of them in its cache, which lies outside the aid's folders. The
    middleware that checks the Host does it; the page's other answers are left as the library makes them."""
    import asyncio

    import aid.review.__main__ as review
    from gradio.routes import App

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"image/webp"),
                                                                             (b"cache-control", b"max-age=3600")]})
        await send({"type": "http.response.body", "body": b"made-up image"})

    async def headers_of(application, path: str) -> dict:
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
                 "raw_path": path.encode(), "query_string": b"", "root_path": "", "client": ("127.0.0.1", 50000),
                 "server": ("127.0.0.1", 7861), "headers": [(b"host", b"127.0.0.1:7861")]}
        await application(scope, receive, send)
        return {"status": sent[0]["status"], **{name.decode().lower(): value.decode() for name, value in sent[0]["headers"]}}

    async def middleware() -> list[dict]:
        guard = review.OwnHost(inner)
        return [await headers_of(guard, path) for path in ("/gradio_api/file=/made/up/cases/tmp/0a1b/image.webp",
                                                           "/gradio_api/file/made/up/image.webp", "/config")]

    image, deprecated, config = asyncio.run(middleware())
    assert image["cache-control"] == "no-store" and image["content-type"] == "image/webp"
    assert deprecated["cache-control"] == "no-store"
    assert config["cache-control"] == "max-age=3600"                                          # not a file of the page: as it was
    path, page, made = tab(tmp_path, madeup_out, **BUYER)

    async def page_answers() -> list[dict]:
        application = App.create_app(page, app_kwargs=review.launch_options(page, False)["app_kwargs"])
        return [await headers_of(application, path) for path in (f"/gradio_api/file={tmp_path / 'not-a-file-of-the-page.webp'}", "/config")]

    answered, config = asyncio.run(page_answers())
    assert answered["status"] in (403, 404) and answered["cache-control"] == "no-store"
    assert config["status"] == 200 and config.get("cache-control") != "no-store"


# --- the documents say what the tab does ---

def manual_sections() -> dict[str, str]:
    """The manual's sections by their number ("2.7", "3.14", "5", "7"), each with its own text only."""
    sections, number = {}, None
    for line in Path("aid/MANUAL.md").read_text(encoding="utf-8").splitlines():
        if (heading := re.match(r"#{2,3} (\d+(?:\.\d+)?)\.? ", line)) is not None:
            number = heading[1]
        if number is not None:
            sections[number] = sections.get(number, "") + line + "\n"
    return sections


def test_the_manual_names_the_tab_as_the_page_does():
    nl, manual = STRINGS["nl"], manual_sections()
    buyers = manual["2.7"]
    for key in ([f"settings_group:{group}" for group in ("handled", "questions", "digest", "approval")]
                + [f"settings_field:{field}" for field in BUYER_FIELDS]
                + ["settings_digest:daily", "settings_digest:per_case", "settings_buyer_save", "settings_buyer_saved", "settings_buyer_unchanged",
                   "settings_suppliers", "settings_supplier_column:review", "settings_supplier_column:messages_off", "settings_supplier_choice",
                   "settings_supplier_new", "settings_supplier_save", "settings_supplier_remove", "settings_supplier_review",
                   "settings_supplier_messages_off", "settings_supplier_listed", "settings_supplier_empty", "settings_connections"]):
        assert f"\"{nl[key]}\"" in buyers, key                                              # quoted word for word
    assert nl["settings_file_unreadable"].format(fields="") in buyers and nl["settings_problem:decimals"] in buyers
    whole = Path("aid/MANUAL.md").read_text(encoding="utf-8")
    # the connections' heading names no installation; the block "Koppeling met Exact (bij de installatie)" is the one that does
    assert "(bij de installatie)" not in whole.replace(f"\"{nl['exact_title']}\"", "")
    assert "Geen e-mails" not in whole and "geen e-mails" not in whole
    assert "never e-mails" not in whole and "after the PO's date" not in whole and "This many first cases" not in whole
    assert "\"Leverancier toevoegen\"" in manual["4.1"] and "Nieuwe leverancier" not in manual["4.1"]
    assert buyers.count("Nieuwe leverancier") == 1 and manual["7"].count("Nieuwe leverancier") == 1      # the reason of the first cases only
    guide = manual["7"]
    for key in ("settings_supplier_new", "settings_buyer_save", "settings_supplier_save", "settings_supplier_remove", "settings_connections"):
        assert f"\"{nl[key]}\"" in guide, key
    for number in ("3.14", "3.15", "5", "7"):                 # where the manual sends the reader to a button of the folded part, it names the part
        assert "\"Koppelingen" in manual[number], number
    for button in ("Aanmelden bij Microsoft", "Aanmelden voor Copilot"):
        for number in ("3.14", "3.15", "5", "7"):
            section = manual[number]
            if button in section:                             # the first mention of the button comes after the part is named
                assert section.index("Koppelingen") < section.index(f"\"{button}\""), (number, button)


def test_the_manual_and_the_readme_say_what_a_save_does_and_does_not_do():
    manual, guide = manual_sections()["2.7"], manual_sections()["7"]
    # what was checked before a save keeps its outcome, and a cart is checked anew only when the cart itself changes: on the same day,
    # since the capture server answers the same cart from its case for the rest of that day only (the same page byte for byte on any day)
    assert "keeps its outcome" in manual and "on the same day a cart is checked anew, with the new values, only when the cart itself changes" in manual
    assert "the same page on any day, the same cart on the same day" in manual
    assert "houdt zijn uitkomst" in guide and "op dezelfde dag pas opnieuw gecontroleerd als de winkelwagen zelf verandert" in guide
    # the name, the address and the language hold for the request to the approver too, and the name stands under "Purchasing" in English
    assert "\"Purchasing\"" in manual and "copy of every question and of the request to the approver" in manual
    assert "of the messages to suppliers and to the approver" in manual
    # the days are counted from the delivery date of the purchase order's line, with one worked example
    assert "counted from the delivery date of the purchase order's line" in manual and "3 December" in manual and "4 December" in manual
    assert "geteld vanaf de leverdatum van de regel op de inkooporder" in guide
    # no questions by itself, and a question the buyer chooses on the review tab is sent
    assert "by itself" in manual and "is still sent to the supplier" in manual and "uit zichzelf" in guide and "gaat wel naar de leverancier" in guide
    # a save writes only what the buyer changed
    assert "writes only the fields you changed" in manual and "alleen wat u hebt gewijzigd" in guide
    # the line of a file that cannot be read names a field of the tab by its label and a setting of the installation by its name
    assert "by its label on the tab, or by the setting's name for a setting the installation sets" in manual
    assert "(a field of the tab by its label, any other setting by its name)" in manual_sections()["3.2"]
    readme = Path("aid/README.md").read_text(encoding="utf-8")
    choice = readme.index("\"Na elke zaak\"")                 # the walk-through's digest after every case: the page of live/, not of demo/
    assert "python3 -m aid.review --settings live/settings.json" in readme[choice - 260:choice]
    for text in (Path("aid/MANUAL.md").read_text(encoding="utf-8"), readme):
        assert "—" not in text                           # no em-dash


# --- the connection to Exact, part 1: the layout of the purchase order PDF set from a sample (Task 32) ---

SLOT_NAMES = tuple(f"slot{n}" for n in range(1, SLOTS + 1))
EXACT_OPENED = ("state", *EXACT_FORM, "title_word", "title_row", *SLOT_NAMES, "senders", "filled", "title_note")   # the fill, in its order
SAMPLE_SHOWN = ("page", "sample_note", "summary", "header", "lines", "problems", "note")       # what choosing a sample gives
TITLE_FOUND = ("title_row", *SLOT_NAMES, "title_note")                                          # what "Kopregel zoeken" gives
TESTED = ("summary", "header", "lines", "problems", "note")                                    # what "Testen" gives
EXACT_SAVED = ("note", "state", "filled", "summary", "header", "lines", "problems")            # what "Opslaan" gives
SENDERS = "inkoop@example.org, example.org"
NOTHING_SAVED = {"po_layout": None, "po_senders": []}          # what the block's form is filled with from a file without the two settings
MINE = {"exact_opened", "exact_sample_shown", "exact_title_found", "exact_tested", "exact_saved", "exact_state_shown", "exact_relabel"}


def exact_args(sample, form: dict | None = None, meanings: list | None = None, senders: str = SENDERS, language: str = "nl",
               filled: dict | None = NOTHING_SAVED) -> list:
    """The inputs of "Testen" and "Opslaan": the sample as the page library hands it over (a path), the fields of the form, the title
    row found, the SLOTS dropdowns (those beyond the title row empty), the senders, what the form was filled with (the two settings as
    the file held them), then the page's language."""
    form = ANDERS_FORM if form is None else form
    meanings = list(ANDERS_MEANINGS if meanings is None else meanings)
    return [None if sample is None else str(sample), *[form[field] for field in EXACT_FORM], form["title_row"],
            *(meanings + [None] * (SLOTS - len(meanings))), senders, filled, language]


def edited_anders(tmp_path: Path, edit, name: str) -> Path:
    """The second made-up PO after `edit` has changed the rows of its pages (tryout_po_pages of the generator)."""
    pages = tryout_po_pages()
    edit(pages)
    path = tmp_path / name
    path.write_bytes(pdf_bytes(pages, "edited try-out PO"))
    return path


def test_the_sample_shows_its_lines_and_the_title_row_its_dropdowns(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl, en = STRINGS["nl"], STRINGS["en"]
    shown = named(SAMPLE_SHOWN, handler(page, "exact_sample_shown")(str(ANDERS_PO), "nl"))
    assert shown["page"]["headers"] == ["Regel", "Tekst"] and shown["page"]["data"] == page_lines(ANDERS_PO)
    assert shown["page"]["data"][2] == ["3", "Proefleverancier S23"] and shown["sample_note"] == ""
    assert (shown["summary"], shown["problems"], shown["note"], shown["lines"]["value"]["data"]) == ("", "", "", [])     # an earlier reading goes
    found = named(TITLE_FOUND, handler(page, "exact_title_found")(str(ANDERS_PO), "Artikelcode", [], *[None] * SLOTS, "nl"))
    assert found["title_row"] == ANDERS_LAYOUT.title_row and found["title_note"] == nl["exact_title_found"]
    slots = [found[name] for name in SLOT_NAMES]
    assert [slot["label"] for slot in slots[:8]] == ANDERS_LAYOUT.title_row and [slot["visible"] for slot in slots] == [True] * 8 + [False] * 12
    assert slots[0]["choices"] == [(nl[f"exact_meaning:{meaning}"], meaning) for meaning in MEANINGS] and [s["value"] for s in slots] == [None] * 20
    # the same title row found again keeps what was chosen; another title row starts empty
    chosen = [*ANDERS_MEANINGS, *[None] * 12]
    again = named(TITLE_FOUND, handler(page, "exact_title_found")(str(ANDERS_PO), "Benaming", ANDERS_LAYOUT.title_row, *chosen, "nl"))
    assert [again[name]["value"] for name in SLOT_NAMES[:8]] == ANDERS_MEANINGS
    other = named(TITLE_FOUND, handler(page, "exact_title_found")(str(madeup_out / "MU01_po_v1.pdf"), "Artikel", ANDERS_LAYOUT.title_row, *chosen, "nl"))
    assert other["title_row"] == MADEUP_LAYOUT.title_row and [other[name]["value"] for name in SLOT_NAMES[:8]] == [None] * 8
    # a word on no line of page 1, a scan, no sample, and a title row of more than 20 words leave the title row and the dropdowns
    long = tmp_path / "long.pdf"
    long.write_bytes(pdf_bytes([[Row([Text(0, "Bestelnummer: X-1")]), Row([Text(0, " ".join(f"K{n}" for n in range(1, 22)))])]], "long"))
    for sample, word, note in ((ANDERS_PO, "Artikel", nl["exact_title_missing"]), (madeup_out / "MU05_conf.pdf", "Artikel", nl["exact_problem:scan"]),
                               (None, "Artikel", nl["exact_problem:no_sample"]), (long, "K3", nl["exact_title_long"])):
        kept = named(TITLE_FOUND, handler(page, "exact_title_found")(None if sample is None else str(sample), word, ANDERS_LAYOUT.title_row,
                                                                     *chosen, "nl"))
        assert kept["title_note"] == note and kept["title_row"] == ANDERS_LAYOUT.title_row and [kept[n] for n in SLOT_NAMES] == [SAME] * 20, note
    assert nl["exact_title_found"] == "Kopregel gevonden. Kies bij elk woord wat er in die kolom staat."
    assert nl["exact_title_missing"] == "Op de eerste pagina staat geen regel met dit woord."
    scan = named(SAMPLE_SHOWN, handler(page, "exact_sample_shown")(str(madeup_out / "MU05_conf.pdf"), "en"))
    assert (scan["page"]["headers"], scan["page"]["data"], scan["sample_note"]) == (["Line", "Text"], [], en["exact_problem:scan"])
    gone = named(SAMPLE_SHOWN, handler(page, "exact_sample_shown")(None, "nl"))          # the sample taken away again
    assert (gone["page"]["data"], gone["sample_note"]) == ([], "")


def test_testing_writes_nothing(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, authorization_limit="777.77", **BUYER)
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    before = path.read_bytes()
    nl, en = STRINGS["nl"], STRINGS["en"]
    result = named(TESTED, handler(page, "exact_tested")(*exact_args(ANDERS_PO)))
    assert result["summary"] == nl["exact_read"].format(n=12, answer=nl["exact_yes"]) and result["note"] == ""
    assert result["summary"].startswith("Het voorbeeld is gelezen: 12 orderregels; het totaal is te berekenen: ja.")
    assert result["header"]["headers"] == [nl["exact_read_what"], nl["exact_read_value"]]
    assert result["header"]["data"][:2] == [["Inkoopordernummer", "IB-2600417"], ["Datum", "2026-11-03 (03-11-2026)"]]
    assert result["lines"]["value"]["headers"] == line_headers("nl", ANDERS_LAYOUT) and len(result["lines"]["value"]["data"]) == 12
    assert result["lines"]["value"]["data"][0] == ["1", "10", "pc (st)", "KM-2301", "Kogelkraan DN25 messing", "2026-11-16 (16-11-2026)", "12.5 (12,50)"]
    assert result["problems"] == f"- {nl['exact_warning:continued'].format(page=2)}"     # page 2 has no title row
    # never the authorization limit, nor whether the total is above it (the total is 4.417,85, above the limit of the file)
    flat = json.dumps(result, ensure_ascii=False)
    assert not any(text in flat for text in ("777", "4417", "4.417", "fiat", "Fiat", "grens", "limiet", "boven de"))
    unpriced = named(TESTED, handler(page, "exact_tested")(*exact_args(ANDERS_PO, ANDERS_FORM | {"per_piece": False}, language="en")))
    assert unpriced["summary"] == en["exact_read"].format(n=12, answer=en["exact_no"]) and unpriced["summary"].endswith("press Save.")
    # a form that gives no layout reads nothing and says why, in the note
    refused = named(TESTED, handler(page, "exact_tested")(*exact_args(ANDERS_PO, ANDERS_FORM | {"po_label": ""}, senders="Inkoop")))
    assert refused["note"] == " ".join([nl["exact_problem:po_label"], nl["exact_problem:sender"].format(text="Inkoop")])
    assert (refused["summary"], refused["lines"]["value"]["data"], refused["problems"]) == ("", [], "")
    assert path.read_bytes() == before and path.stat().st_mtime_ns == 1_700_000_000_000_000_000
    assert not (tmp_path / "po_store").exists() and not (tmp_path / "inbox").exists()


def test_saving_writes_the_layout_and_the_senders_and_keeps_every_other_key(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **(BUYER | {"po_layout": None, "po_senders": [], "review_url": "http://localhost:7860"}))
    text, before = path.read_text(encoding="utf-8"), held(path)
    nl = STRINGS["nl"]
    result = named(EXACT_SAVED, handler(page, "exact_saved")(*exact_args(ANDERS_PO)))
    assert result["note"] == nl["exact_saved"] and nl["exact_state_set"] in result["state"]
    assert result["summary"] == nl["exact_read"].format(n=12, answer=nl["exact_yes"])          # the reading the save checked
    assert result["filled"] == {"po_layout": TRYOUT_PO_LAYOUT, "po_senders": ["inkoop@example.org", "example.org"]}   # what the form now holds
    after = held(path)
    assert after["po_layout"] == TRYOUT_PO_LAYOUT and list(after["po_layout"]) == list(TRYOUT_PO_LAYOUT)
    assert after["po_senders"] == ["inkoop@example.org", "example.org"] and list(after) == list(before)
    # the two values are replaced where they stand, in the file's indentation; every other character of the file is kept
    def written(value) -> str:
        return json.dumps(value, indent=4).replace("\n", "\n    ")
    assert path.read_text(encoding="utf-8") == text.replace('"po_layout": null', f'"po_layout": {written(TRYOUT_PO_LAYOUT)}').replace(
        '"po_senders": []', f'"po_senders": {written(["inkoop@example.org", "example.org"])}')
    saved = load_settings(path)
    assert saved.po_layout == ANDERS_LAYOUT and company_values(saved) == ["po_layout is set", "po_senders is not empty"]    # no share link now
    # the same save again, from the form as the first save left it, writes nothing
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    assert named(EXACT_SAVED, handler(page, "exact_saved")(*exact_args(ANDERS_PO, filled=result["filled"])))["note"] == nl["exact_saved"]
    assert path.stat().st_mtime_ns == 1_700_000_000_000_000_000
    # a file without the two keys gets them at its end, in this order; empty senders are an empty list
    path, page, made = tab(tmp_path / "lacking", madeup_out, **BUYER)
    before = held(path)
    assert named(EXACT_SAVED, handler(page, "exact_saved")(*exact_args(ANDERS_PO, senders=" ")))["note"] == nl["exact_saved"]
    after = held(path)
    assert list(after) == [*before, "po_layout", "po_senders"] and after["po_senders"] == [] and {key: after[key] for key in before} == before


def test_after_a_save_the_next_pass_files_the_purchase_orders_that_stayed_in_the_watched_folder(tmp_path, madeup_out):
    """A PO PDF that the aid cannot read without the layout stays in the watched folder as an error (its title "Inkooporder" makes it
    a PO); once the layout is saved, the next pass, which reads the settings file again, files it."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    shutil.copy(edited_anders(tmp_path, lambda pages: setattr(pages[0][1].texts[0], "text", "Inkooporder"), "exact_po.pdf"), inbox / "exact_po.pdf")
    store = PoStore(tmp_path / "po_store")
    first = scan_folder(inbox, store, tmp_path / "cases", load_settings(path), datetime(2026, 11, 12, 9, 0))
    assert first.stored_pos == [] and first.skipped[0][0] == "exact_po.pdf" and first.skipped[0][1].startswith("error:")
    assert (inbox / "exact_po.pdf").exists()
    assert named(EXACT_SAVED, handler(page, "exact_saved")(*exact_args(ANDERS_PO)))["note"] == STRINGS["nl"]["exact_saved"]
    second = scan_folder(inbox, store, tmp_path / "cases", load_settings(path), datetime(2026, 11, 12, 9, 1))
    assert second.stored_pos == ["IB-2600417"] and not (inbox / "exact_po.pdf").exists()
    assert store.versions("IB-2600417")[0].document.reader == "po_pdf:layout"
    state = handler(page, "exact_state_shown")("nl")
    assert STRINGS["nl"]["exact_state_known"].split("{count}")[0] + "1." in state and "IB-2600417" in state


def test_a_refused_save_writes_nothing_and_names_the_problem(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    before = path.read_bytes()
    nl, en = STRINGS["nl"], STRINGS["en"]
    titled = edited_anders(tmp_path, lambda pages: setattr(pages[0][1].texts[0], "text", "Orderbevestiging"), "titled.pdf")
    two = edited_anders(tmp_path, lambda pages: setattr(pages[1][0].texts[0], "text", "Bestelnummer: IB-2600999"), "two.pdf")
    two_pos = nl["exact_problem:two_pos"].format(places="regel 6 van de eerste pagina en pagina 2")
    c01 = SAMPLES / "c01_dutch_numbers.pdf"
    cases = [(exact_args(None), [nl["exact_problem:no_sample"]]),
             (exact_args(ANDERS_PO, senders="Inkoop <inkoop@example.org>"), [nl["exact_problem:sender"].format(text="Inkoop <inkoop@example.org>")]),
             (exact_args(ANDERS_PO, meanings=ANDERS_MEANINGS[:7]), [nl["exact_problem:meanings"]]),
             (exact_args(ANDERS_PO, ANDERS_FORM | {"po_label": " "}), [nl["exact_problem:po_label"]]),
             (exact_args(madeup_out / "MU01_po_v1.pdf"), [nl["exact_problem:title_differs"], nl["exact_problem:no_number"]]),
             (exact_args(madeup_out / "MU05_conf.pdf"), [nl["exact_problem:scan"]]),
             (exact_args(titled), [nl["exact_problem:confirmation"].format(line=2)]),
             (exact_args(two), [two_pos]),
             (exact_args(c01, ERP_FORM | {"numbers": "point"}, ERP_MEANINGS), [nl["exact_problem:convention_comma"].format(column="Prijs")])]
    for args, problems in cases:
        result = named(EXACT_SAVED, handler(page, "exact_saved")(*args))
        assert result["note"] == nl["exact_not_saved"].format(problems=" ".join(problems)), problems
        assert result["state"] == SAME and result["filled"] == NOTHING_SAVED and path.read_bytes() == before, problems
    shown = named(EXACT_SAVED, handler(page, "exact_saved")(*exact_args(two)))                # the reading shows the refusal below the tables
    assert shown["problems"].splitlines() == [f"- {two_pos}", f"- {nl['exact_warning:continued'].format(page=2)}"]
    assert "IB-2600999" not in shown["problems"] + shown["note"]
    english = named(EXACT_SAVED, handler(page, "exact_saved")(*exact_args(None, language="en")))
    assert english["note"] == en["exact_not_saved"].format(problems=en["exact_problem:no_sample"]) == "Not saved: Choose a sample first."
    assert nl["exact_not_saved"] == "Niet opgeslagen: {problems}" and nl["exact_problem:no_sample"] == "Kies eerst een voorbeeld."
    by_hand(path, price_pct="x")                                                           # a settings file that cannot be read
    broken = path.read_bytes()
    result = named(EXACT_SAVED, handler(page, "exact_saved")(*exact_args(ANDERS_PO)))
    assert result["note"] == unreadable("nl", nl["settings_field:price_pct"]) and path.read_bytes() == broken


def test_the_block_reopens_with_the_saved_layout(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl, en = STRINGS["nl"], STRINGS["en"]
    empty = named(EXACT_OPENED, handler(page, "exact_opened")("nl"))
    assert {field: empty[field] for field in EXACT_FORM} == {field: "" for field in EXACT_FORM} | {"numbers": "comma", "per_piece": False}
    assert (empty["title_word"], empty["title_row"], empty["senders"]) == ("", [], "") and nl["exact_state_unset"] in empty["state"]
    assert [empty[name]["visible"] for name in SLOT_NAMES] == [False] * 20 and (empty["filled"], empty["title_note"]) == (NOTHING_SAVED, "")
    handler(page, "exact_saved")(*exact_args(ANDERS_PO))
    again = build_app(deps(tmp_path, madeup_out).model_copy(update={"settings": load_settings(path)}), settings_file=path, session=FakeSession())
    shown = named(EXACT_OPENED, handler(again, "exact_opened")("en"))
    assert {field: shown[field] for field in EXACT_FORM} == {field: ANDERS_FORM[field] for field in EXACT_FORM}
    assert (shown["title_word"], shown["title_row"], shown["senders"]) == ("Artikelcode", ANDERS_LAYOUT.title_row, SENDERS)
    assert [shown[name]["label"] for name in SLOT_NAMES[:8]] == ANDERS_LAYOUT.title_row
    assert [shown[name]["value"] for name in SLOT_NAMES] == [*ANDERS_MEANINGS, *[None] * 12]
    assert [shown[name]["visible"] for name in SLOT_NAMES] == [True] * 8 + [False] * 12
    assert shown["slot1"]["choices"] == [(en[f"exact_meaning:{meaning}"], meaning) for meaning in MEANINGS] and en["exact_state_set"] in shown["state"]
    assert shown["filled"] == {"po_layout": TRYOUT_PO_LAYOUT, "po_senders": ["inkoop@example.org", "example.org"]} and shown["title_note"] == ""
    # the reopened form is tested as it is: no new search of the title row is needed
    form = {field: shown[field] for field in EXACT_FORM} | {"title_row": shown["title_row"]}
    tested = named(TESTED, handler(again, "exact_tested")(*exact_args(ANDERS_PO, form, [shown[name]["value"] for name in SLOT_NAMES], shown["senders"],
                                                                      filled=shown["filled"])))
    assert tested["summary"] == nl["exact_read"].format(n=12, answer=nl["exact_yes"])
    # a layout written into the file by hand opens the same way
    path, page, made = tab(tmp_path / "by_hand", madeup_out, **(BUYER | {"po_layout": TRYOUT_PO_LAYOUT, "po_senders": ["example.org"]}))
    by_file = named(EXACT_OPENED, handler(page, "exact_opened")("nl"))
    assert by_file["po_label"] == "Bestelnummer" and by_file["supplier_line"] == "3" and by_file["senders"] == "example.org"


def test_the_block_offers_nothing_on_a_share_link_or_without_a_settings_file(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, share=True, **BUYER)
    assert not {getattr(f.fn, "__name__", "") for f in page.fns.values()} & MINE
    labels = {getattr(block, "label", None) for block in page.blocks.values()}
    assert not labels & {STRINGS["nl"]["exact_title"], STRINGS["nl"]["exact_sample"], STRINGS["nl"]["exact_field:po_label"]}
    assert not {getattr(f.fn, "__name__", "") for f in build_app(deps(tmp_path / "nofile", madeup_out)).fns.values()} & MINE
    own = tab(tmp_path / "own", madeup_out, **BUYER)[1]                                     # the page of this PC has them all
    assert MINE <= {getattr(f.fn, "__name__", "") for f in own.fns.values()}


def test_both_languages_hold_the_same_keys():
    nl, en = STRINGS["nl"], STRINGS["en"]
    mine = [key for key in nl if key.startswith("exact_")]
    assert set(nl) == set(en) and mine == [key for key in en if key.startswith("exact_")] and len(mine) > 60
    for key in mine:
        assert nl[key] and en[key], key
        assert re.findall(r"\{(\w+)\}", nl[key]) == re.findall(r"\{(\w+)\}", en[key]), key       # the same places for the same values
        assert not re.search(r"[—–_]", nl[key] + en[key]), key                                  # no dash, and no setting's name
    assert (nl["exact_title"], en["exact_title"]) == ("Koppeling met Exact (bij de installatie)", "Connection to Exact (at the installation)")
    assert nl["exact_intro"] == ("De assistent leest wat Exact zelf levert en verandert zelf niets in Exact. Hier stelt u in hoe de documenten uit "
                                 "Exact eruitzien. Uw voorbeeldbestanden blijven op deze pc.")
    assert en["exact_intro"] == ("The assistant reads what Exact itself delivers and changes nothing in Exact. Here you set what the documents "
                                 "from Exact look like. Your sample files stay on this PC.")
    assert (nl["exact_part_po"], en["exact_part_po"]) == ("1. Inkooporders die Exact per e-mail stuurt (pdf)",
                                                          "1. Purchase orders that Exact sends by e-mail (PDF)")
    assert [nl[f"exact_meaning:{meaning}"] for meaning in MEANINGS] == [
        "Regelnummer", "Artikelcode", "Omschrijving", "Aantal", "Eenheid", "Prijs", "Prijs per (aantal)", "Korting in procenten", "Nettoprijs",
        "Leverdatum", "Hoort bij de kolom links ervan", "Niet nodig"]
    assert [en[f"exact_meaning:{meaning}"] for meaning in MEANINGS] == [
        "Line number", "Item code", "Description", "Quantity", "Unit", "Price", "Price per (number)", "Discount in percent", "Net price",
        "Delivery date", "Belongs to the column on its left", "Not needed"]
    assert [nl[f"exact_field:{field}"] for field in ("po_label", "date_label", "supplier_label", "supplier_line", "version_label", "currency_label",
                                                      "incoterm_label")] == [
        "Tekst voor het inkoopordernummer", "Tekst voor de datum (mag leeg)", "Tekst voor de leverancier",
        "Of: het nummer van de regel waarop alleen de leverancier staat",
        "Tekst voor het versienummer (leeg: de pdf noemt geen versie; de assistent nummert dan in de volgorde van binnenkomst)",
        "Tekst voor de valuta (mag leeg)", "Tekst voor de leveringsconditie (mag leeg)"]
    assert (nl["exact_sample"], nl["exact_page"], nl["exact_page_line"], nl["exact_page_text"]) == (
        "Voorbeeld: een inkooporder uit Exact als pdf", "De eerste pagina van het voorbeeld, regel voor regel", "Regel", "Tekst")
    assert (nl["exact_field:title_word"], nl["exact_find"], en["exact_find"]) == (
        "Een woord uit de kopregel boven de orderregels, bijvoorbeeld Artikel", "Kopregel zoeken", "Find title row")
    assert (nl["exact_field:table_end"], nl["exact_field:numbers"], nl["exact_numbers:comma"], nl["exact_numbers:point"]) == (
        "De tabel houdt op bij de regel met deze tekst (leeg: tot het eind)", "Getallen op de pdf", "1.234,50 (komma voor de decimalen)",
        "1,234.50 (punt voor de decimalen)")
    assert nl["exact_field:per_piece"] == "De pdf heeft geen kolom prijs per: elke prijs is per 1 stuk"
    assert (nl["exact_test"], nl["exact_save"], en["exact_test"], en["exact_save"]) == ("Testen", "Opslaan", "Test", "Save")
    assert (nl["exact_reading"], nl["exact_state_title"], nl["exact_refresh"]) == ("Zo leest de assistent het voorbeeld", "Stand van de koppeling",
                                                                                  "Stand vernieuwen")
    assert nl["exact_read"] == ("Het voorbeeld is gelezen: {n} orderregels; het totaal is te berekenen: {answer}. Vergelijk de tabel met de "
                                "inkooporder in Exact. Klopt alles, druk dan op Opslaan.")
    assert nl["exact_saved"] == ("Opgeslagen. De assistent leest inkooporders in deze opmaak vanaf de volgende ronde, binnen een minuut. "
                                 "Inkooporders die nog in de bewaakte map liggen omdat de assistent ze niet kon lezen, worden dan opgenomen; "
                                 "andere geeft u opnieuw.")
    assert en["exact_saved"] == ("Saved. The assistant reads purchase orders in this layout from its next pass, within a minute. Purchase orders "
                                 "that still lie in the watched folder because it could not read them are then filed; give any others again.")
    # the texts of the review's rulings, word for word
    assert nl["exact_title_saved_long"] == ("De opgeslagen kopregel heeft meer dan 20 woorden; dat kan dit formulier niet tonen. De opmaak blijft "
                                            "gelden tot u een nieuwe opslaat.")
    assert en["exact_title_saved_long"] == ("The saved title row has more than 20 words, which this form cannot show. The layout stays in force "
                                            "until you save a new one.")
    assert (nl["exact_page_more"], en["exact_page_more"]) == ("De tabel toont de eerste {n} regels van de eerste pagina.",
                                                              "The table shows the first {n} lines of the first page.")
    assert nl["exact_changed"] == ("Niet opgeslagen: de koppeling is intussen op een andere plek gewijzigd. Open het tabblad Instellingen "
                                   "opnieuw en kijk de opmaak na.")
    assert en["exact_changed"] == "Not saved: the connection was changed elsewhere meanwhile. Open the tab Settings again and check the layout."
    assert nl["exact_problem:unopenable"] == "Dit pdf-bestand kan niet worden geopend. Sla het opnieuw op uit de e-mail en kies het nog eens."


def test_the_exact_block_is_wired_as_the_page_shows_it(tmp_path, madeup_out):
    """The built page itself: the block folded away just above the connections, its controls in their order, each field's help, and
    each handler's trigger, inputs and outputs."""
    gr = import_gradio()
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl = STRINGS["nl"]
    [settings] = [block for block in page.blocks.values() if isinstance(block, gr.Tab) and block.label == nl["settings_tab"]]
    accordions = [block for block in settings.children if isinstance(block, gr.Accordion)]
    assert [block.label for block in accordions] == [nl["exact_title"], nl["settings_connections"]]
    exact = accordions[0]
    assert exact.open is False and settings.children[-2:] == accordions                  # above the Microsoft connections, at the bottom

    def inside(block) -> list:
        return [block, *[found for child in getattr(block, "children", []) for found in inside(child)]]
    [fill] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "exact_opened"]
    states = {id(fill.outputs[EXACT_OPENED.index("title_row")]): "title row", id(fill.outputs[EXACT_OPENED.index("filled")]): "filled"}
    assert all(isinstance(fill.outputs[EXACT_OPENED.index(state)], gr.State) for state in ("title_row", "filled"))

    def name(component) -> str:
        if id(component) in states:
            return states[id(component)]
        if isinstance(component, gr.Button):
            return f"button {component.value}"
        if isinstance(component, (gr.Dataframe, gr.Markdown)) and component.elem_id:
            return component.elem_id
        return getattr(component, "label", None) or str(getattr(component, "value", ""))

    def bound(fn_name: str) -> tuple:
        [f] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == fn_name]
        return (sorted((name(page.blocks[block]) if block != page._id else "page", event) for block, event in f.targets),
                [name(component) for component in f.inputs], [name(component) for component in f.outputs])

    fields = [nl[f"exact_field:{field}"] for field in ("po_label", "date_label", "supplier_label", "supplier_line", "version_label", "currency_label",
                                                        "incoterm_label")]
    form = [*fields, nl["exact_field:table_end"], nl["exact_field:numbers"], nl["exact_field:per_piece"]]       # in the order of FORM
    slots = [str(n) for n in range(1, SLOTS + 1)]
    reading = ["exact-summary", "exact-header", "exact-lines", "exact-problems"]
    sample, word, senders, page_language = nl["exact_sample"], nl["exact_field:title_word"], nl["exact_field:senders"], nl["language"]
    order = [block for block in inside(exact) if isinstance(block, (gr.Markdown, gr.Textbox, gr.Radio, gr.Button, gr.Dataframe, gr.Dropdown,
                                                                     gr.Checkbox, gr.File))]
    names = [name(block) for block in order]
    assert names[:names.index(f"### {nl['exact_part_export']}")] == [                # part 1, up to part 2 (Task 39)
        nl["exact_intro"], f"### {nl['exact_state_title']}", "exact-state", f"button {nl['exact_refresh']}", f"### {nl['exact_part_po']}", sample,
        "exact-page", "exact-sample-note", *fields, word, f"button {nl['exact_find']}", "exact-title-note", *slots, nl["exact_field:table_end"],
        nl["exact_field:numbers"], nl["exact_field:per_piece"], senders, f"button {nl['exact_test']}", f"button {nl['exact_save']}", "exact-note",
        f"### {nl['exact_reading']}", *reading, f"### {nl['exact_numbers_title']}", nl["exact_field:po_words"], nl["exact_field:po_digits_least"],
        nl["exact_field:po_digits_most"], f"button {nl['exact_po_number_save']}", "exact-number-note"]
    # the fill at the load and at each select of the settings tab, as the buyer's part (ruling L6)
    assert bound("exact_opened") == ([(nl["settings_tab"], "select"), ("page", "load")], [page_language],
                                     ["exact-state", *form, word, "title row", *slots, senders, "filled", "exact-title-note"])
    assert bound("exact_sample_shown") == ([(sample, "change")], [sample, page_language], ["exact-page", "exact-sample-note", *reading, "exact-note"])
    assert bound("exact_title_found") == ([(f"button {nl['exact_find']}", "click")], [sample, word, "title row", *slots, page_language],
                                          ["title row", *slots, "exact-title-note"])
    inputs = [sample, *form, "title row", *slots, senders, "filled", page_language]
    assert bound("exact_tested") == ([(f"button {nl['exact_test']}", "click")], inputs, [*reading, "exact-note"])
    assert bound("exact_saved") == ([(f"button {nl['exact_save']}", "click")], inputs, ["exact-note", "exact-state", "filled", *reading])
    assert bound("exact_state_shown") == ([(f"button {nl['exact_refresh']}", "click")], [page_language], ["exact-state"])
    assert bound("exact_relabel")[0] == [(page_language, "change")]
    [lines] = [block for block in inside(exact) if getattr(block, "elem_id", None) == "exact-lines"]          # ruling L10: a width for each column
    assert len(lines.column_widths) == len(line_headers("nl")) and sum(float(width.rstrip("%")) for width in lines.column_widths) == 100
    shown = {block.label: block for block in inside(exact) if getattr(block, "label", None)}
    assert shown[nl["exact_field:po_label"]].info == nl["exact_help:po_label"] and shown[nl["exact_field:supplier_line"]].info is None
    assert [shown[label].info for label in fields[1:3] + fields[4:]] == [nl["exact_help:label"]] * 5
    assert (shown[nl["exact_field:table_end"]].info, shown[senders].info) == (nl["exact_help:table_end"], nl["exact_help:senders"])
    assert shown[nl["exact_field:numbers"]].choices == [(nl["exact_numbers:comma"], "comma"), (nl["exact_numbers:point"], "point")]
    assert shown[sample].file_types is None and shown[nl["exact_page"]].elem_id == "exact-page"      # any file reaches the handler (M3)
    assert all(block.interactive is False for block in inside(exact) if isinstance(block, gr.Dataframe))
    assert [block.visible for block in inside(exact) if isinstance(block, gr.Dropdown)][:SLOTS] == [False] * SLOTS     # until a title row is found


RELABELLED = ("block", "intro", "state_title", "state", "refresh", "part", "sample", "page", "sample_note", "po_label", "date_label",
              "supplier_label", "supplier_line", "version_label", "currency_label", "incoterm_label", "title_word", "find", "title_note", *SLOT_NAMES,
              "table_end", "numbers", "per_piece", "senders", "test", "save", "note", "reading", "summary", "header", "lines", "problems")


def test_switching_the_language_relabels_the_exact_block_and_keeps_what_is_chosen(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl, en = STRINGS["nl"], STRINGS["en"]
    shown = named(RELABELLED, handler(page, "exact_relabel")("en", str(ANDERS_PO), "point", ANDERS_LAYOUT.title_row, *ANDERS_MEANINGS, *[None] * 12))
    flat = json.dumps(shown, default=str, ensure_ascii=False)
    for key in ("exact_title", "exact_intro", "exact_field:po_label", "exact_help:senders", "exact_test", "exact_state_unset", "exact_reading"):
        assert en[key] in flat, key
    assert nl["exact_field:po_label"] not in flat and nl["exact_help:label"] not in flat
    assert (shown["block"]["label"], shown["po_label"]["label"], shown["po_label"]["info"]) == (en["exact_title"], en["exact_field:po_label"],
                                                                                                en["exact_help:po_label"])
    assert shown["numbers"]["value"] == "point" and shown["numbers"]["choices"] == [(en["exact_numbers:comma"], "comma"),
                                                                                    (en["exact_numbers:point"], "point")]
    choices = [(en[f"exact_meaning:{meaning}"], meaning) for meaning in MEANINGS]
    assert [shown[name]["choices"] for name in SLOT_NAMES] == [choices] * SLOTS
    assert [shown[name]["value"] for name in SLOT_NAMES] == [*ANDERS_MEANINGS, *[None] * 12]       # what is chosen stays
    assert shown["page"]["label"] == en["exact_page"] and nl["exact_page"] not in flat                   # ruling L8: the table's label too
    assert shown["page"]["value"] == {"headers": ["Line", "Text"], "data": page_lines(ANDERS_PO)} and shown["sample_note"] == ""
    assert (shown["note"], shown["summary"], shown["problems"], shown["lines"]["value"]["data"]) == ("", "", "", [])     # tested again in English
    assert shown["lines"]["value"]["headers"] == line_headers("en")


def test_the_sample_is_read_where_the_page_library_put_it_and_copied_nowhere(tmp_path, madeup_out):
    """The page library hands the sample over as a file of its own temporary folder (gradio's GRADIO_TEMP_DIR, which the review
    command sets to <cases_root>/tmp, aid/review/__main__.py). Choosing, testing and saving read it there: no copy of it is made
    anywhere, nothing is filed and no case is opened."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    upload = tmp_path / "gradio" / "0a1b2c3d"
    upload.mkdir(parents=True)
    sample = upload / "ANDERS_po_v1.pdf"
    shutil.copy(ANDERS_PO, sample)
    digest = hashlib.sha256(sample.read_bytes()).hexdigest()
    handler(page, "exact_sample_shown")(str(sample), "nl")
    handler(page, "exact_title_found")(str(sample), "Artikelcode", [], *[None] * SLOTS, "nl")
    handler(page, "exact_tested")(*exact_args(sample))
    assert named(EXACT_SAVED, handler(page, "exact_saved")(*exact_args(sample)))["note"] == STRINGS["nl"]["exact_saved"]
    copies = [p for p in tmp_path.rglob("*") if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == digest]
    assert copies == [sample] and sorted(p.name for p in upload.iterdir()) == ["ANDERS_po_v1.pdf"]
    assert not (tmp_path / "po_store").exists() and not (tmp_path / "inbox").exists() and not list(tmp_path.glob("cases/IB-*"))


def test_the_manual_and_the_readme_say_how_the_layout_is_set_on_the_page():
    nl, manual = STRINGS["nl"], manual_sections()
    exact = manual["3.17"]
    for key in ("exact_title", "exact_part_po", "exact_sample", "exact_page", "exact_field:po_label", "exact_field:supplier_line",
                "exact_field:title_word", "exact_find", "exact_meaning:same", "exact_meaning:skip", "exact_field:table_end", "exact_field:numbers",
                "exact_field:per_piece", "exact_field:senders", "exact_test", "exact_save", "exact_reading", "exact_state_title", "exact_refresh"):
        assert f"\"{nl[key]}\"" in exact, key                                               # quoted word for word
    assert "the layout first, the mailbox second" in exact and "not on a shared screen" in exact and "ANDERS_po_v1.pdf" in exact
    assert "test walk" in exact and "B2" in exact and "\"Bestelnummer\"" in exact and "\"Artikelcode\"" in exact
    assert "3.17" in manual["3.12"] and "Until the trial read on the page exists" not in manual["3.12"]
    assert "3.17" in manual["2.7"] and "3.17" in manual["3.2"] and "`po_layout`, `po_senders`, `review_url`" not in manual["3.2"]
    readme = Path("aid/README.md").read_text(encoding="utf-8")
    assert nl["exact_title"] in readme and "section 3.17" in readme


# --- the review round of the block (rulings L2, L3, L6, L10, L11 and L12) ---

def test_a_saved_title_row_longer_than_the_form_opens_without_its_dropdowns(tmp_path, madeup_out):
    """Ruling L2: a layout written into the file with a title row of more than 20 words, which the reader takes, opens the block without
    the title row and its dropdowns, with the line that says so and every other field as saved; the fill gives one value per output, so
    gradio can send it, and the layout stays in force: a save of that form has no title row and is refused."""
    import asyncio
    nl = STRINGS["nl"]
    layout = {"po_label": "Bestelnummer", "date_label": "Besteldatum", "title_row": [f"K{n}" for n in range(1, 23)],
              "columns": ["item_code", "quantity"] + ["skip"] * 20, "decimal_comma": False}
    path, page, made = tab(tmp_path, madeup_out, **(BUYER | {"po_layout": layout, "po_senders": ["inkoop@example.org"]}))
    [fill] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "exact_opened"]
    out = handler(page, "exact_opened")("nl")
    assert len(out) == len(fill.outputs)
    asyncio.run(page.postprocess_data(fill, list(out), None))                       # no TypeError: each value fits its output
    shown = named(EXACT_OPENED, out)
    assert shown["title_row"] == [] and [shown[name]["visible"] for name in SLOT_NAMES] == [False] * SLOTS
    assert shown["title_note"] == nl["exact_title_saved_long"] and nl["exact_state_set"] in shown["state"]
    assert (shown["po_label"], shown["date_label"], shown["numbers"], shown["senders"]) == ("Bestelnummer", "Besteldatum", "point", "inkoop@example.org")
    before = path.read_bytes()
    form = {field: shown[field] for field in EXACT_FORM} | {"title_row": shown["title_row"]}
    note = handler(page, "exact_saved")(*exact_args(ANDERS_PO, form, [], shown["senders"], filled=shown["filled"]))[0]
    assert note == nl["exact_not_saved"].format(problems=nl["exact_problem:no_title"]) and path.read_bytes() == before


def test_the_table_says_when_it_shows_only_the_first_lines_of_the_first_page(tmp_path, madeup_out):
    """Ruling L3: a first page of more lines than the table shows gets the line under the table, in the page's language."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    many, sixty = tmp_path / "many.pdf", tmp_path / "sixty.pdf"
    for pdf, count in ((many, 65), (sixty, 60)):
        pdf.write_bytes(pdf_bytes([[Row([Text(0, f"Regel {n}", "Helvetica", 5)]) for n in range(1, count + 1)]], "made-up page"))
    shown = named(SAMPLE_SHOWN, handler(page, "exact_sample_shown")(str(many), "nl"))
    assert len(shown["page"]["data"]) == 60 and shown["page"]["data"][-1] == ["60", "Regel 60"]
    assert shown["sample_note"] == STRINGS["nl"]["exact_page_more"].format(n=60) == "De tabel toont de eerste 60 regels van de eerste pagina."
    assert named(SAMPLE_SHOWN, handler(page, "exact_sample_shown")(str(sixty), "nl"))["sample_note"] == ""         # all 60 shown
    relabelled = named(RELABELLED, handler(page, "exact_relabel")("en", str(many), "comma", [], *[None] * SLOTS))
    assert relabelled["sample_note"] == "The table shows the first 60 lines of the first page."


def test_a_save_from_a_form_the_file_no_longer_holds_is_refused_and_selecting_the_tab_fills_it_again(tmp_path, madeup_out):
    """Ruling L6: the block keeps what its form was filled with (at the load, at each select of the settings tab and after each save).
    A save from a form that another browser tab overtook writes nothing and says so; the tab selected again shows the file and saves."""
    nl = STRINGS["nl"]
    path, page, made = tab(tmp_path, madeup_out, **(BUYER | {"po_layout": TRYOUT_PO_LAYOUT, "po_senders": ["inkoop@example.org"]}))
    first, second = (named(EXACT_OPENED, handler(page, "exact_opened")("nl")) for _ in range(2))      # two browser tabs
    assert first["filled"] == second["filled"] == {"po_layout": TRYOUT_PO_LAYOUT, "po_senders": ["inkoop@example.org"]}
    saved = named(EXACT_SAVED, handler(page, "exact_saved")(*exact_args(ANDERS_PO, senders="inkoop@example.org, orders.example.org",
                                                                         filled=first["filled"])))
    assert saved["note"] == nl["exact_saved"] and saved["filled"]["po_senders"] == ["inkoop@example.org", "orders.example.org"]
    before = path.read_bytes()
    stale = named(EXACT_SAVED, handler(page, "exact_saved")(*exact_args(ANDERS_PO, senders="inkoop@example.org", filled=second["filled"])))
    assert (stale["note"], stale["state"], stale["filled"]) == (nl["exact_changed"], SAME, second["filled"]) and path.read_bytes() == before
    again = named(EXACT_OPENED, handler(page, "exact_opened")("nl"))                     # the settings tab selected again in tab 2
    assert again["senders"] == "inkoop@example.org, orders.example.org" and again["filled"] == saved["filled"]
    landed = named(EXACT_SAVED, handler(page, "exact_saved")(*exact_args(ANDERS_PO, senders="inkoop@example.org", filled=again["filled"])))
    assert landed["note"] == nl["exact_saved"] and held(path)["po_senders"] == ["inkoop@example.org"]
    by_hand(path, po_layout=TRYOUT_PO_LAYOUT | {"table_end": "Totaal exclusief btw"})        # the layout counts too: changed in the file
    refused = named(EXACT_SAVED, handler(page, "exact_saved")(*exact_args(ANDERS_PO, filled=landed["filled"])))
    assert refused["note"] == nl["exact_changed"] and held(path)["po_layout"]["table_end"] == "Totaal exclusief btw"
    assert handler(page, "exact_saved")(*exact_args(ANDERS_PO, filled=None))[0] == nl["exact_changed"]     # a fill the page no longer knows


def test_the_table_of_order_lines_gives_each_column_a_width_inside_the_block(tmp_path, madeup_out):
    """Ruling L10: the table of order lines carries a width for each of its columns, together the whole width of the block, so that
    its cells wrap inside it; the widest column is the description's."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    for args in (exact_args(ANDERS_PO), exact_args(SAMPLES / "c01_dutch_numbers.pdf", ERP_FORM, ERP_MEANINGS), exact_args(None)):
        lines = named(TESTED, handler(page, "exact_tested")(*args))["lines"]
        widths = [int(width.rstrip("%")) for width in lines["column_widths"]]
        assert len(widths) == len(lines["value"]["headers"]) and sum(widths) == 100, lines["value"]["headers"]
    lines = named(TESTED, handler(page, "exact_tested")(*exact_args(ANDERS_PO)))["lines"]
    widths = dict(zip(lines["value"]["headers"], [int(width.rstrip("%")) for width in lines["column_widths"]]))
    assert max(widths, key=widths.get) == "Omschrijving" and len(widths) == 7


def test_a_pdf_that_cannot_be_opened_is_named_in_every_handler_that_opens_it(tmp_path, madeup_out):
    """Ruling L11: a sample cut short gets the sentence that says what to do, never a class of the PDF library."""
    nl, en = STRINGS["nl"], STRINGS["en"]
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    cut = tmp_path / "cut.pdf"
    cut.write_bytes(ANDERS_PO.read_bytes()[:1500])
    shown = named(SAMPLE_SHOWN, handler(page, "exact_sample_shown")(str(cut), "nl"))
    assert (shown["sample_note"], shown["page"]["data"]) == (nl["exact_problem:unopenable"], [])
    found = named(TITLE_FOUND, handler(page, "exact_title_found")(str(cut), "Artikelcode", [], *[None] * SLOTS, "nl"))
    assert found["title_note"] == nl["exact_problem:unopenable"]
    assert named(TESTED, handler(page, "exact_tested")(*exact_args(cut)))["note"] == nl["exact_problem:unopenable"]
    before = path.read_bytes()
    saved = named(EXACT_SAVED, handler(page, "exact_saved")(*exact_args(cut)))
    assert saved["note"] == nl["exact_not_saved"].format(problems=nl["exact_problem:unopenable"]) and path.read_bytes() == before
    relabelled = named(RELABELLED, handler(page, "exact_relabel")("en", str(cut), "comma", [], *[None] * SLOTS))
    assert relabelled["sample_note"] == en["exact_problem:unopenable"]
    assert "Pdfminer" not in json.dumps([shown, found, saved, relabelled], default=str)


def test_the_summary_is_shown_only_when_the_trial_has_no_problem(tmp_path, madeup_out):
    """Ruling L12: "Klopt alles, druk dan op Opslaan" stands only above a reading without problems; a warning alone keeps it."""
    nl = STRINGS["nl"]
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    wrong = named(TESTED, handler(page, "exact_tested")(*exact_args(madeup_out / "MU01_po_v1.pdf")))
    assert wrong["summary"] == "" and wrong["problems"].splitlines() == [f"- {nl['exact_problem:title_differs']}",
                                                                        f"- {nl['exact_problem:no_number']}"]
    right = named(TESTED, handler(page, "exact_tested")(*exact_args(ANDERS_PO)))
    assert right["summary"] == nl["exact_read"].format(n=12, answer=nl["exact_yes"]) and right["problems"] != ""        # the page-2 warning
    refused = named(EXACT_SAVED, handler(page, "exact_saved")(*exact_args(madeup_out / "MU01_po_v1.pdf")))
    assert refused["summary"] == "" and refused["note"].startswith("Niet opgeslagen: ")


def test_the_manual_says_how_the_review_round_reads_a_sample():
    exact = manual_sections()["3.17"]
    assert "the first 60 lines" in exact and "\"1000 (1.000)\"" in exact and "\"1 (1.000)\"" in exact
    assert "\"Inkooporder:\"" in exact and "which is two purchase orders in one PDF" not in exact
    assert "another browser tab" in exact and "\"Klopt alles, druk dan op Opslaan\"" in exact


# --- the number recognition of part 1, and the count of the cases that wait for their purchase order (Task 36) ---

from aid.tests.helpers import cases_for_the_count  # noqa: E402

NUMBERS_OPENED = ("words", "least", "most", "filled")                        # the fill of the two fields, in its order
NUMBERS_SAVED = ("note", "words", "least", "most", "filled")                 # what "Nummerherkenning opslaan" gives
DEFAULT_PATTERN = Settings.model_fields["po_number_pattern"].default
DEFAULT_WORDS = "PO, Inkooporder, Bestelling, Bestellung, Order"
NUMBER_HANDLERS = {"exact_numbers_opened", "exact_numbers_saved", "exact_numbers_relabel"}


def words_shown(value: str, placeholder: str = "") -> dict:
    """The field of the words as a fill or a save gives it: its text, and the line that stands in it while it is empty."""
    return {"__type__": "update", "value": value, "placeholder": placeholder}


def test_the_two_fields_write_the_po_number_pattern_only(tmp_path, madeup_out):
    """Rule 11 and amendment 3: the two fields are filled from the file at the load and at each select of the settings tab, here with
    the five words and the six to eight digits of the default, which the file does not hold. "Nummerherkenning opslaan" writes
    po_number_pattern and nothing else, through update_settings_file: every other key keeps its bytes, the key the file lacks is added
    at its end, and the fields then show what the file holds. Fields as they were filled write nothing. A refused field writes nothing
    and is named by its label with its problem. A save from fields filled before the file's pattern changed is refused with the block's
    note, and the tab selected again fills them with the file as it is."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl, en = STRINGS["nl"], STRINGS["en"]
    fill = named(NUMBERS_OPENED, handler(page, "exact_numbers_opened")("nl"))
    assert fill == {"words": words_shown(DEFAULT_WORDS), "least": "6", "most": "8", "filled": DEFAULT_PATTERN}
    text, before = path.read_text(encoding="utf-8"), held(path)
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    same = named(NUMBERS_SAVED, handler(page, "exact_numbers_saved")(f" {DEFAULT_WORDS},", "6", " 8", DEFAULT_PATTERN, "nl"))
    assert same == {"note": nl["settings_buyer_unchanged"], "words": SAME, "least": SAME, "most": SAME, "filled": DEFAULT_PATTERN}
    assert path.stat().st_mtime_ns == 1_700_000_000_000_000_000
    pattern = r"(?i:Inkooporder|Uw\s+referentie)[\s:#.-]*(\d{6,10})(?!\d)"
    saved = named(NUMBERS_SAVED, handler(page, "exact_numbers_saved")("Inkooporder,Uw referentie", "6", "10", DEFAULT_PATTERN, "nl"))
    assert saved == {"note": nl["settings_buyer_saved"], "words": words_shown("Inkooporder, Uw referentie"), "least": "6", "most": "10",
                     "filled": pattern}
    after = held(path)
    assert list(after) == [*before, "po_number_pattern"] and {key: after[key] for key in before} == before and after["po_number_pattern"] == pattern
    assert path.read_text(encoding="utf-8").startswith(text[:text.rindex("\n}")]) and load_settings(path).po_number_pattern == pattern
    # refused: each problem by the label of its field, nothing written
    words, digits = nl["exact_field:po_words"], f"{nl['exact_field:po_digits_least']} {nl['exact_field:po_digits_most']}"
    written = path.read_bytes()
    for typed, problems in ((("", "6", "8"), [(words, nl["exact_problem:po_words"])]), (("Inkooporder", "0", "8"), [(digits, nl["exact_problem:po_digits"])]),
                            (("Inkooporder", "9", "8"), [(digits, nl["exact_problem:po_digits"])]),
                            (("", "", ""), [(words, nl["exact_problem:po_words"]), (digits, nl["exact_problem:po_digits"])])):
        refused = named(NUMBERS_SAVED, handler(page, "exact_numbers_saved")(*typed, pattern, "nl"))
        assert refused["note"] == nl["settings_buyer_refused"].format(fields="; ".join(f"{label}: {problem}" for label, problem in problems)), typed
        assert (refused["words"], refused["least"], refused["most"], refused["filled"]) == (SAME, SAME, SAME, pattern) and path.read_bytes() == written
    assert nl["settings_buyer_refused"].format(fields=f"{words}: {nl['exact_problem:po_words']}") == (
        "Niet opgeslagen: Woorden waar leveranciers uw inkoopordernummer achter zetten: vul minstens een woord in.")
    # the file's pattern changed after the fill (another browser tab, or the file itself): refused with the fields' own note (ruling N12)
    by_hand(path, po_number_pattern=r"(?i:Order)[\s:#.-]*(\d{6,8})(?!\d)")
    changed = path.read_bytes()
    stale = named(NUMBERS_SAVED, handler(page, "exact_numbers_saved")("Inkooporder", "6", "8", pattern, "nl"))
    assert (stale["note"], stale["filled"]) == (nl["exact_number_changed"], pattern) and path.read_bytes() == changed
    assert named(NUMBERS_SAVED, handler(page, "exact_numbers_saved")("Inkooporder", "6", "8", pattern, "en"))["note"] == en["exact_number_changed"]
    again = named(NUMBERS_OPENED, handler(page, "exact_numbers_opened")("en"))
    assert again == {"words": words_shown("Order"), "least": "6", "most": "8", "filled": r"(?i:Order)[\s:#.-]*(\d{6,8})(?!\d)"}
    english = named(NUMBERS_SAVED, handler(page, "exact_numbers_saved")("Order, PO", "6", "8", again["filled"], "en"))
    assert english["note"] == en["settings_buyer_saved"] and held(path)["po_number_pattern"] == r"(?i:Order|PO)[\s:#.-]*(\d{6,8})(?!\d)"
    by_hand(path, price_pct="x")                                                            # a settings file that cannot be read
    broken = path.read_bytes()
    result = named(NUMBERS_SAVED, handler(page, "exact_numbers_saved")("Inkooporder", "6", "8", english["filled"], "nl"))
    assert result["note"] == unreadable("nl", nl["settings_field:price_pct"]) and path.read_bytes() == broken


def test_a_pattern_of_another_form_is_shown_as_an_own_pattern_and_left_alone_while_the_fields_are_empty(tmp_path, madeup_out):
    """Rule 11: a pattern the fields cannot show is not taken apart: the fields are empty, the field of the words says "eigen patroon (in
    het instellingenbestand)", and a save with the fields empty writes nothing. Filled in, the fields replace it with a pattern of
    their form."""
    own = r"REF-(\d{6})"
    path, page, made = tab(tmp_path, madeup_out, **(BUYER | {"po_number_pattern": own}))
    nl, en = STRINGS["nl"], STRINGS["en"]
    fill = named(NUMBERS_OPENED, handler(page, "exact_numbers_opened")("nl"))
    assert fill == {"words": words_shown("", nl["exact_po_number_own"]), "least": "", "most": "", "filled": own}
    before = path.read_bytes()
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    left = named(NUMBERS_SAVED, handler(page, "exact_numbers_saved")(" ", "", "", own, "nl"))
    assert left["note"] == nl["settings_buyer_unchanged"] and path.read_bytes() == before and path.stat().st_mtime_ns == 1_700_000_000_000_000_000
    half = named(NUMBERS_SAVED, handler(page, "exact_numbers_saved")("Uw referentie", "", "", own, "nl"))
    assert half["note"] == nl["settings_buyer_refused"].format(
        fields=f"{nl['exact_field:po_digits_least']} {nl['exact_field:po_digits_most']}: {nl['exact_problem:po_digits']}") and path.read_bytes() == before
    relabelled = handler(page, "exact_numbers_relabel")("en", own)
    assert relabelled[0] == f"### {en['exact_numbers_title']}"                                   # the heading of the fields (ruling N11)
    assert relabelled[1] == {"__type__": "update", "label": en["exact_field:po_words"], "info": en["exact_help:po_words"],
                             "placeholder": en["exact_po_number_own"]}
    assert [update["label"] for update in relabelled[2:4]] == [en["exact_field:po_digits_least"], en["exact_field:po_digits_most"]]
    assert relabelled[4] == {"__type__": "update", "value": en["exact_po_number_save"]} and relabelled[5] == ""
    assert handler(page, "exact_numbers_relabel")("nl", DEFAULT_PATTERN)[1]["placeholder"] == ""
    saved = named(NUMBERS_SAVED, handler(page, "exact_numbers_saved")("Uw referentie", "6", "8", own, "nl"))
    assert saved["note"] == nl["settings_buyer_saved"] and held(path)["po_number_pattern"] == r"(?i:Uw\s+referentie)[\s:#.-]*(\d{6,8})(?!\d)"
    assert {key: value for key, value in held(path).items() if key != "po_number_pattern"} == settings_file_values(tmp_path / "again", BUYER)


def settings_file_values(folder: Path, values: dict) -> dict:
    """What settings_file writes for these values, as JSON."""
    return held(settings_file(folder, **values))


def test_the_texts_of_the_number_recognition_and_the_count_of_the_waiting_cases(tmp_path, madeup_out):
    """The texts of the two fields, of their heading (ruling N11) and of their note when the pattern was changed elsewhere (ruling N12)
    word for word, and the state line that counts the cases that wait for their purchase order (rule 10): of seven confirmations only
    the two run while the store does not hold their POs, not a closed, a replaced or a decided one, one in review for another reason,
    nor one opened without a PO number (ruling N10)."""
    nl, en = STRINGS["nl"], STRINGS["en"]
    assert (nl["exact_numbers_title"], en["exact_numbers_title"]) == (
        "Hoe de assistent het inkoopordernummer op een orderbevestiging vindt", "How the assistant finds the purchase order number on an order confirmation")
    assert (nl["exact_number_changed"], en["exact_number_changed"]) == (
        "Niet opgeslagen: de nummerherkenning is intussen op een andere plek gewijzigd. Open het tabblad Instellingen opnieuw en kijk de woorden "
        "en de cijfers na.",
        "Not saved: the number recognition was changed elsewhere meanwhile. Open the tab Settings again and check the words and the digits.")
    assert (nl["exact_field:po_words"], en["exact_field:po_words"]) == ("Woorden waar leveranciers uw inkoopordernummer achter zetten",
                                                                      "Words after which suppliers print your purchase order number")
    assert (nl["exact_help:po_words"], en["exact_help:po_words"]) == ("Met een komma ertussen, bijvoorbeeld: Inkooporder, Uw referentie, Uw ordernummer.",
                                                                    "With a comma between them, for example: Inkooporder, Uw referentie, Uw ordernummer.")
    assert (nl["exact_field:po_digits_least"], nl["exact_field:po_digits_most"]) == ("Een inkoopordernummer heeft minstens zoveel cijfers",
                                                                                    "en hoogstens zoveel cijfers")
    assert (en["exact_field:po_digits_least"], en["exact_field:po_digits_most"]) == ("A purchase order number has at least this many digits",
                                                                                    "and at most this many digits")
    assert (nl["exact_po_number_save"], en["exact_po_number_save"]) == ("Nummerherkenning opslaan", "Save number recognition")
    assert (nl["exact_problem:po_words"], en["exact_problem:po_words"]) == ("vul minstens een woord in", "fill in at least one word")
    assert (nl["exact_problem:po_digits"], en["exact_problem:po_digits"]) == (
        "een heel getal van 1 tot 20, en het kleinste niet groter dan het grootste", "a whole number from 1 to 20, the smallest not above the largest")
    assert (nl["exact_state_waiting"], en["exact_state_waiting"]) == ("Zaken die op hun inkooporder wachten: {n}.",
                                                                    "Cases that wait for their purchase order: {n}.")
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    assert nl["exact_state_waiting"].format(n=0) in handler(page, "exact_state_shown")("nl")
    assert load_settings(path).cases_root == tmp_path / "cases"
    cases_for_the_count(tmp_path / "cases", madeup_out, deps(tmp_path / "run", madeup_out))
    state = handler(page, "exact_state_shown")("en")
    assert en["exact_state_waiting"].format(n=2) in state and "250101" not in state and "S01" not in state
    assert nl["exact_state_waiting"].format(n=2) in named(EXACT_OPENED, handler(page, "exact_opened")("nl"))["state"]


def test_the_number_recognition_is_wired_in_part_1_of_the_block(tmp_path, madeup_out):
    """The two fields and their button stand at the end of part 1 of the block (the order of the whole block is held in
    test_the_exact_block_is_wired_as_the_page_shows_it); the fill runs at the load and at each select of the settings tab, the save at
    its button, and the language switch relabels them. On a share link and without a settings file they are not there."""
    gr = import_gradio()
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl = STRINGS["nl"]

    def bound(fn_name: str) -> tuple:
        [f] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == fn_name]
        names = [getattr(page.blocks[block], "label", None) or getattr(page.blocks[block], "value", None) if block != page._id else "page"
                 for block, _ in f.targets]
        return sorted(zip(names, [event for _, event in f.targets])), f
    targets, fill = bound("exact_numbers_opened")
    assert targets == [(nl["settings_tab"], "select"), ("page", "load")]
    assert [getattr(block, "label", None) for block in fill.outputs[:3]] == [nl["exact_field:po_words"], nl["exact_field:po_digits_least"],
                                                                         nl["exact_field:po_digits_most"]]
    assert isinstance(fill.outputs[3], gr.State) and fill.inputs[0].label == nl["language"]
    targets, save = bound("exact_numbers_saved")
    assert targets == [(nl["exact_po_number_save"], "click")]
    assert [*save.inputs[:3], save.inputs[3]] == [*fill.outputs[:3], fill.outputs[3]] and save.inputs[4].label == nl["language"]
    assert save.outputs[0].elem_id == "exact-number-note" and [*save.outputs[1:]] == [*fill.outputs]
    targets, relabel = bound("exact_numbers_relabel")
    assert targets == [(nl["language"], "change")] and relabel.inputs[1] is fill.outputs[3]
    assert relabel.outputs[0].value == f"### {nl['exact_numbers_title']}" and [*relabel.outputs[1:4]] == [*fill.outputs[:3]]
    words = fill.outputs[0]
    assert words.info == nl["exact_help:po_words"] and isinstance(words, gr.Textbox)
    for share, file in ((True, True), (False, False)):
        other = tab(tmp_path / f"{share}", madeup_out, share=share, **BUYER)[1] if file else build_app(deps(tmp_path / "nofile", madeup_out))
        assert not {getattr(f.fn, "__name__", "") for f in other.fns.values()} & NUMBER_HANDLERS


def test_the_manual_says_when_a_case_is_checked_again():
    """The manual after the review round of Task 36: the two hints word for word (2.2) and the guide's word that a decision ends the
    check again (7, ruling N8); the heading and the note of the fields, and what the default and the fields' form take (3.17, rulings
    N7, N11 and N12); when a case is checked again, and that a changed number recognition counts for documents that come in after it,
    with no promise that a restart or an order given again links a case by a new recognition (2.2, 4.4 and 5, ruling N9); that a case
    without a number waits for nothing (3.17, ruling N2); that a replaced case keeps its files and is passed over by a count (3.1,
    ruling N14); and the stage of a failing check again in the log (3.6, ruling N13). The second review round: what a restart does
    (2.2, 3.17, 4.4 and 5, ruling N24), the reason of a guessed link (2.4, 3.17 and 5, ruling N18), and the guard of 3.2's list of what
    only the installation sets (ruling N25). The third: a decision saved while the runner checks a case again, and the new case that
    has not run removed then (3.17, rulings N28 and N35), and a guessed case without a version (2.2, 2.4, 3.17 and 7, ruling N29)."""
    nl, manual = STRINGS["nl"], manual_sections()
    whole = Path("aid/MANUAL.md").read_text(encoding="utf-8")
    assert f"\"{nl['waiting_hint:po'].format(number='250101')}\"" in manual["2.2"]
    assert f"\"{nl['waiting_hint:version'].format(number='250103', version='1')}\"" in manual["2.2"]
    assert "Inkooporder 250101 kent de assistent nog niet." in manual["7"] and "niet meer vanzelf opnieuw" in manual["7"]
    exact = manual["3.17"]
    for key in ("exact_numbers_title", "exact_number_changed", "exact_po_number_save", "exact_field:po_words", "exact_state_waiting"):
        assert f"\"{nl[key].split('{')[0].rstrip(': ')}" in exact, key
    assert "a longer number is taken whole or not at all, in the default as in the fields' form" in exact
    assert "the two differ only in that the fields' form also passes a full stop between the word and the number" in exact
    assert "waits for nothing" in exact and "never for another" in exact
    assert "is not joined to letters or digits by a dash, a full stop, a slash or a comma:" in exact and "(\"Uw referentie:250101\")" in exact
    for number in ("2.2", "3.17", "4.4", "5"):                  # ruling N24: what a restart does
        assert "every order of the store counts as filed" in manual[number], number
    for number in ("4.4", "5"):
        assert "counts for documents that come in after" in manual[number], number
    for promise in ("is checked again at the first pass after the runner starts, or when the order's e-mail is given again",
                    "set the number recognition (section 3.17) and restart the runner", "De inkooporder van deze zaak kent de assistent",
                    "and at the first pass after the runner starts: it checks the confirmation", "and at the first pass after the runner "
                    "starts, a new case is opened", "and at the first pass after the runner starts, the runner checks"):
        assert promise not in whole, promise
    # ruling N18: the reason of a guessed link, its row in 2.4, the advice in 3.17 and the troubleshooting in 5
    from aid.pipeline import GUESSED_PO
    en = STRINGS["en"]
    label, english = nl[f"reason:{GUESSED_PO}"], en[f"reason:{GUESSED_PO}"]
    assert f"| {GUESSED_PO} | {label} | {english} |" in manual["2.4"]
    assert f"\"{label}\"" in exact and "keep coming with" in exact and f"\"{label}\"" in manual["5"] and f"\"{label}\"" in manual["7"]
    # ruling N25 (the guard of N15b): the settings tab writes po_number_pattern, so 3.2 does not list it as the installation's alone
    installation = manual["3.2"].split("Only the installation sets the rest:")[1].split("Set these in the file")[0]
    assert "`po_number_pattern`" not in installation and "`export_columns`" not in installation and "`capture_token`" in installation
    assert "keeps all its files" in manual["3.1"] and "passes over a case whose `steps/09_close.json` holds the record \"replaced\"" in manual["3.1"]
    assert "`recheck <case>`" in manual["3.6"]
    # the third review round: a decision saved during the check again wins (3.17, ruling N28), and a guessed case without a version
    # says so (2.2, 2.4, 3.17 and 7, ruling N29)
    assert "also when you decide on it while the runner checks it again" not in whole
    assert ("A decision you save while the runner checks the case again stops that check, unless you save it in the very moment the "
            f"runner closes the old case as replaced; the close then says so: \"{nl['not_closed']}\" When the check stops, the new case "
            "it opened for the confirmation is removed, and so is a new case that an earlier check opened when it has not run yet (it "
            "has no result and wrote or sent nothing); a new case that has run stays.") in exact                  # ruling N35
    for number in ("2.2", "2.4", "3.17"):
        assert "\"Geen versie van de inkooporder gekozen\"" in manual[number] and "once that version is filed" in manual[number], number
        assert "no hint" in manual[number] and "not counted" in manual[number] and "order pane" in manual[number], number
    assert ("Staat er ook \"Geen versie van de inkooporder gekozen\", dan kent de assistent de versie niet die de bevestiging noemt"
            in manual["7"]) and "zodra die versie binnenkomt" in manual["7"]
    # the merge with Task 33: the try-out's read-back finds the order as the scan does, by a held number as well
    read_back = exact.split("**The read-back.**")[1].split("\n")[0]
    assert "or else by a held number, the number of an order the aid knows, of at least four characters" in read_back
    assert "one line whose number has fewer than four characters and is not one the pattern reads" in read_back


# --- the connection to Exact, part 2: the columns of an export of order lines set from a sample (Task 39) ---

CHOSEN = tuple(f"field:{field}" for field in EXPORT_FORM)                                   # the twelve dropdowns, by the field of each
EXPORT_OPENED = (*CHOSEN, "numbers", "fill_down", "offered", "export_filled")                # the fill of part 2, in its order
EXPORT_SAMPLE_SHOWN = ("head", "sample_note", "row", *CHOSEN, "offered", "reading", "lines", "note")   # what choosing a sample gives
EXPORT_HEADER_SHOWN = (*CHOSEN, "offered", "sample_note")                                   # what another row with the column titles gives
EXPORT_TESTED = ("reading", "lines", "note")                                                 # what "Testen" gives
EXPORT_SAVED = ("note", "state", "export_filled", "reading", "lines")                        # what "Opslaan" gives
RELABELLED_EXPORT = ("part", "intro", "sample", "head", "sample_note", "row", *CHOSEN, "numbers", "fill_down", "test", "save", "note", "reading",
                     "lines")
NOTHING_EXPORTED = {"export_columns": {}, "export_decimal_comma": False, "export_fill_down": False}    # a file without the three settings
SAVED_EXPORT = {"export_columns": EXPORT_NL_COLUMNS, "export_decimal_comma": True, "export_fill_down": True}    # what EXPORT_NL_FORM saves
EXPORT_MINE = {"export_opened", "export_sample_shown", "export_header_shown", "export_tested", "export_saved", "export_relabel"}
NONE_CHOSEN = [NO_COLUMN] * len(EXPORT_FORM)


def export_args(sample, form: dict | None = None, filled: dict | None = NOTHING_EXPORTED, language: str = "nl") -> list:
    """The inputs of "Testen" and "Opslaan" of part 2: the sample as the page library hands it over (a path), the column chosen in each
    dropdown, the number convention, the tick, what the part was filled with (the three settings as the file held them), then the
    page's language."""
    form = EXPORT_NL_FORM if form is None else form
    return [None if sample is None else str(sample), *[form[field] for field in EXPORT_FORM], form["numbers"], form["fill_down"], filled,
            language]


def offered(titles: list[str], language: str = "nl") -> list[tuple[str, str]]:
    """The choices of a dropdown of part 2 while these titles are offered: the choice for no column first."""
    return [(STRINGS[language]["exact_export_no_column"], NO_COLUMN), *[(title, title) for title in titles]]


def test_the_export_sample_fills_the_dropdowns(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl, en = STRINGS["nl"], STRINGS["en"]
    shown = named(EXPORT_SAMPLE_SHOWN, handler(page, "export_sample_shown")(str(EXPORT_NL), *NONE_CHOSEN, "nl"))
    assert shown["head"] == {"headers": ["Rij", *"ABCDEFGHIJ"], "data": table_head(EXPORT_NL)} and shown["sample_note"] == ""
    assert shown["row"] == 3 and shown["offered"] == EXPORT_NL_TITLES                      # the row with the column titles, found
    assert [shown[name]["choices"] for name in CHOSEN] == [offered(EXPORT_NL_TITLES)] * 12
    assert [shown[name]["value"] for name in CHOSEN] == NONE_CHOSEN
    assert (shown["reading"], shown["note"], shown["lines"]["value"]["data"]) == ("", "", [])      # an earlier reading goes
    # a column chosen before stays chosen where the sample has it (compared as the reader compares titles), and stays offered where
    # the sample lacks it, so that "Testen" names it
    chosen = [EXPORT_NL_FORM[field] for field in EXPORT_FORM]
    chosen[EXPORT_FORM.index("item_code")], chosen[EXPORT_FORM.index("net_price")] = "ARTIKEL", "Netto"
    again = named(EXPORT_SAMPLE_SHOWN, handler(page, "export_sample_shown")(str(EXPORT_NL), *chosen, "nl"))
    assert again["offered"] == [*EXPORT_NL_TITLES, "Netto"] and again["field:net_price"]["choices"] == offered([*EXPORT_NL_TITLES, "Netto"])
    assert [again[name]["value"] for name in CHOSEN] == [*chosen[:2], "Artikel", *chosen[3:]]
    # another row with the column titles offers its titles; back on row 3 the columns chosen are taken again
    first = named(EXPORT_HEADER_SHOWN, handler(page, "export_header_shown")(str(EXPORT_NL), 1, *NONE_CHOSEN, "nl"))
    assert first["offered"] == ["Openstaande inkooporderregels (verzonnen voorbeeld)"] and first["sample_note"] == ""
    assert first["field:supplier"]["choices"] == offered(["Openstaande inkooporderregels (verzonnen voorbeeld)"])
    back = named(EXPORT_HEADER_SHOWN, handler(page, "export_header_shown")(str(EXPORT_NL), 3, *chosen, "nl"))
    assert [back[name]["value"] for name in CHOSEN][:3] == ["Inkooporder", NO_COLUMN, "Artikel"] and back["offered"][-1] == "Netto"
    assert named(EXPORT_HEADER_SHOWN, handler(page, "export_header_shown")(None, 3, *chosen, "nl")) == dict.fromkeys(EXPORT_HEADER_SHOWN, SAME)
    # a file of another type is named by its sentence, and the dropdowns stay as they are; so does a sample taken away
    old = tmp_path / "export.xls"
    old.write_bytes(EXPORT_NL.read_bytes())
    refused = named(EXPORT_SAMPLE_SHOWN, handler(page, "export_sample_shown")(str(old), *chosen, "en"))
    assert (refused["sample_note"], refused["head"]["data"], refused["row"]) == (en["exact_export_refused:file_type"], [], None)
    assert refused["head"]["headers"] == ["Row"] and [refused[name] for name in (*CHOSEN, "offered")] == [SAME] * 13
    gone = named(EXPORT_SAMPLE_SHOWN, handler(page, "export_sample_shown")(None, *chosen, "nl"))
    assert (gone["head"]["data"], gone["sample_note"], gone["row"]) == ([], "", None) and [gone[name] for name in (*CHOSEN, "offered")] == [SAME] * 13
    assert (nl["exact_export_sample"], nl["exact_export_head"], nl["exact_export_header_row"], nl["exact_export_no_column"]) == (
        "Voorbeeld: een export van orderregels uit Exact", "De eerste rijen van het voorbeeld", "Rij met de kolomnamen", "(staat niet in het bestand)")
    assert (en["exact_export_sample"], en["exact_export_head"], en["exact_export_header_row"], en["exact_export_no_column"]) == (
        "Sample: an export of order lines from Exact", "The first rows of the sample", "Row with the column titles", "(not in the file)")


def test_testing_the_export_writes_nothing(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, authorization_limit="777.77", **BUYER)
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    before = path.read_bytes()
    nl, en = STRINGS["nl"], STRINGS["en"]
    result = named(EXPORT_TESTED, handler(page, "export_tested")(*export_args(EXPORT_NL)))
    assert result["reading"] == EXPORT_NL_READ and result["note"] == ""
    assert result["lines"]["value"]["headers"] == export_headers(SAVED_EXPORT, "nl") and result["lines"]["value"]["data"] == EXPORT_NL_ORDERS
    english = named(EXPORT_TESTED, handler(page, "export_tested")(*export_args(EXPORT_NL, language="en")))
    assert english["reading"] == EXPORT_NL_READ_EN and english["lines"]["value"]["headers"][:2] == ["Purchase order number", "Line number"]
    # a form that gives no map reads nothing and says why in the note, and so does a missing sample
    refused = named(EXPORT_TESTED, handler(page, "export_tested")(*export_args(EXPORT_NL, EXPORT_NL_FORM | {"po_number": NO_COLUMN,
                                                                                                              "net_price": "Prijs"})))
    assert refused["note"] == " ".join([nl["exact_export_problem:po_number"], nl["exact_export_problem:more_than_once"].format(column="\"Prijs\"")])
    assert (refused["reading"], refused["lines"]["value"]["data"]) == ("", [])
    assert named(EXPORT_TESTED, handler(page, "export_tested")(*export_args(None)))["note"] == nl["exact_problem:no_sample"]
    # a refusal of the reader stands where the reading stands, by its sentence
    unticked = named(EXPORT_TESTED, handler(page, "export_tested")(*export_args(EXPORT_NL, EXPORT_NL_FORM | {"fill_down": False})))
    assert (unticked["reading"], unticked["lines"]["value"]["data"], unticked["note"]) == (nl["exact_export_refused:no_po_number"].format(row=5),
                                                                                         [], "")
    assert "777" not in json.dumps([result, english, refused, unticked], ensure_ascii=False)      # never the authorization limit
    assert path.read_bytes() == before and path.stat().st_mtime_ns == 1_700_000_000_000_000_000
    assert not (tmp_path / "po_store").exists() and not (tmp_path / "inbox").exists()


def test_saving_writes_the_three_settings_and_keeps_every_other_key(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **(BUYER | NOTHING_EXPORTED | {"review_url": "http://localhost:7860"}))
    text, before = path.read_text(encoding="utf-8"), held(path)
    nl = STRINGS["nl"]
    result = named(EXPORT_SAVED, handler(page, "export_saved")(*export_args(EXPORT_NL)))
    assert result["note"] == nl["exact_export_saved"] == "Opgeslagen." and STRINGS["en"]["exact_export_saved"] == "Saved."
    assert nl["exact_state_columns_set"] in result["state"] and nl["exact_state_columns_unset"] not in result["state"]
    assert result["export_filled"] == SAVED_EXPORT                                            # what the part now holds
    assert result["reading"] == EXPORT_NL_READ and result["lines"]["value"]["data"] == EXPORT_NL_ORDERS
    after = held(path)
    assert {key: after[key] for key in SAVED_EXPORT} == SAVED_EXPORT and list(after) == list(before)
    assert list(after["export_columns"]) == list(EXPORT_NL_COLUMNS)

    def written(value) -> str:
        return json.dumps(value, indent=4).replace("\n", "\n    ")
    assert path.read_text(encoding="utf-8") == text.replace('"export_columns": {}', f'"export_columns": {written(EXPORT_NL_COLUMNS)}').replace(
        '"export_decimal_comma": false', '"export_decimal_comma": true').replace('"export_fill_down": false', '"export_fill_down": true')
    assert load_settings(path).export_columns == EXPORT_NL_COLUMNS and company_values(load_settings(path)) == ["export_columns is not empty"]
    # the same save again, from the part as the first save left it, writes nothing
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    again = named(EXPORT_SAVED, handler(page, "export_saved")(*export_args(EXPORT_NL, filled=result["export_filled"])))
    assert again["note"] == nl["exact_export_saved"] and path.stat().st_mtime_ns == 1_700_000_000_000_000_000
    # a file without the three keys gets them at its end, in this order
    path, page, made = tab(tmp_path / "lacking", madeup_out, **BUYER)
    before = held(path)
    assert named(EXPORT_SAVED, handler(page, "export_saved")(*export_args(EXPORT_NL)))["note"] == nl["exact_export_saved"]
    after = held(path)
    assert list(after) == [*before, "export_columns", "export_decimal_comma", "export_fill_down"] and {key: after[key] for key in before} == before


def test_a_save_of_the_columns_is_refused_while_the_sample_cannot_be_read_with_them(tmp_path, madeup_out):
    """Rules 1 and 2, with amendment 2: each problem is named in the page's language and nothing is written: no sample, a field that
    must be chosen, a column chosen twice, a refusal of the reader, a text column of numbers in the other convention, a file of another
    type, and a sample that gives no order line; and a settings file that cannot be read."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    before = path.read_bytes()
    nl = STRINGS["nl"]
    old = tmp_path / "export.xls"
    old.write_bytes(EXPORT_NL.read_bytes())
    empty = write_sheet(tmp_path / "empty.csv", [["Inkooporder", "Artikel"], ["250301", ""]])
    least = dict.fromkeys(EXPORT_FORM, NO_COLUMN) | {"po_number": "Inkooporder", "item_code": "Artikel", "numbers": "comma", "fill_down": False}
    convention = nl["exact_problem:convention_comma"].format(column="\"Prijs\"")
    cases = [(export_args(None), [nl["exact_problem:no_sample"]]),
             (export_args(EXPORT_NL, EXPORT_NL_FORM | {"item_code": NO_COLUMN}), [nl["exact_export_problem:item_code"]]),
             (export_args(EXPORT_NL, EXPORT_NL_FORM | {"unit": "Aantal"}), [nl["exact_export_problem:more_than_once"].format(column="\"Aantal\"")]),
             (export_args(EXPORT_NL, EXPORT_NL_FORM | {"fill_down": False}), [nl["exact_export_refused:no_po_number"].format(row=5)]),
             (export_args(EXPORT_NL, EXPORT_NL_FORM | {"numbers": "point"}), [convention]),
             (export_args(EXPORT_NL, EXPORT_NL_FORM | {"net_price": "Netto"}), [nl["exact_export_refused:no_column"].format(column="\"Netto\"")]),
             (export_args(old), [nl["exact_export_refused:file_type"]]),
             (export_args(empty, least), [nl["exact_export_problem:no_lines"]])]
    for args, problems in cases:
        result = named(EXPORT_SAVED, handler(page, "export_saved")(*args))
        assert result["note"] == nl["exact_not_saved"].format(problems=" ".join(problems)), problems
        assert result["state"] == SAME and result["export_filled"] == NOTHING_EXPORTED and path.read_bytes() == before, problems
    point = named(EXPORT_SAVED, handler(page, "export_saved")(*export_args(EXPORT_NL, EXPORT_NL_FORM | {"numbers": "point"})))
    assert point["lines"]["value"]["data"][0][4] == "1 (1.000)"                     # the reading shows what the wrong choice would file
    assert point["reading"].split("\n\n") == [EXPORT_NL_READ, convention]
    english = named(EXPORT_SAVED, handler(page, "export_saved")(*export_args(None, language="en")))
    assert english["note"] == "Not saved: Choose a sample first."
    by_hand(path, price_pct="x")                                                          # a settings file that cannot be read
    broken = path.read_bytes()
    result = named(EXPORT_SAVED, handler(page, "export_saved")(*export_args(EXPORT_NL)))
    assert result["note"] == unreadable("nl", nl["settings_field:price_pct"]) and path.read_bytes() == broken


def test_the_part_reopens_with_the_saved_map(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl = STRINGS["nl"]
    empty = named(EXPORT_OPENED, handler(page, "export_opened")(None, None, "nl"))
    assert [empty[name]["value"] for name in CHOSEN] == NONE_CHOSEN and [empty[name]["choices"] for name in CHOSEN] == [offered([])] * 12
    assert (empty["numbers"], empty["fill_down"], empty["offered"], empty["export_filled"]) == ("point", False, [], NOTHING_EXPORTED)
    handler(page, "export_saved")(*export_args(EXPORT_NL))
    again = build_app(deps(tmp_path, madeup_out).model_copy(update={"settings": load_settings(path)}), settings_file=path, session=FakeSession())
    shown = named(EXPORT_OPENED, handler(again, "export_opened")(None, None, "en"))      # no sample: the dropdowns hold the saved titles
    saved_titles = list(EXPORT_NL_COLUMNS.values())
    assert shown["offered"] == saved_titles and [shown[name]["choices"] for name in CHOSEN] == [offered(saved_titles, "en")] * 12
    assert [shown[name]["value"] for name in CHOSEN] == [EXPORT_NL_FORM[field] for field in EXPORT_FORM]
    assert (shown["numbers"], shown["fill_down"], shown["export_filled"]) == ("comma", True, SAVED_EXPORT)
    # the reopened part is tested as it is: no column needs to be chosen again
    form = {field: shown[f"field:{field}"]["value"] for field in EXPORT_FORM} | {"numbers": shown["numbers"], "fill_down": shown["fill_down"]}
    tested = named(EXPORT_TESTED, handler(again, "export_tested")(*export_args(EXPORT_NL, form, shown["export_filled"])))
    assert tested["reading"] == EXPORT_NL_READ and tested["lines"]["value"]["data"] == EXPORT_NL_ORDERS
    # the tab selected again while a sample is chosen: the sample's titles are offered and the saved map is chosen
    with_sample = named(EXPORT_OPENED, handler(again, "export_opened")(str(EXPORT_NL), 3, "nl"))
    assert with_sample["offered"] == EXPORT_NL_TITLES and [with_sample[name]["value"] for name in CHOSEN] == [EXPORT_NL_FORM[field]
                                                                                                             for field in EXPORT_FORM]
    # a map written into the file by hand opens the same way
    path, page, made = tab(tmp_path / "by_hand", madeup_out, **(BUYER | {"export_columns": {"item_code": "Artikel", "po_number": "Order"},
                                                                       "export_decimal_comma": True}))
    by_file = named(EXPORT_OPENED, handler(page, "export_opened")(None, None, "nl"))
    assert by_file["offered"] == ["Order", "Artikel"] and (by_file["field:po_number"]["value"], by_file["field:item_code"]["value"]) == ("Order",
                                                                                                                                       "Artikel")
    assert (by_file["numbers"], by_file["fill_down"], by_file["field:supplier"]["value"]) == ("comma", False, NO_COLUMN)


def test_the_part_offers_nothing_on_a_share_link(tmp_path, madeup_out):
    columns = {"po_number": "Bestelnummer-voorbeeld", "item_code": "Artikelnummer-voorbeeld"}
    path, page, made = tab(tmp_path, madeup_out, share=True, **(BUYER | {"export_columns": columns}))
    assert not {getattr(f.fn, "__name__", "") for f in page.fns.values()} & EXPORT_MINE
    flat = json.dumps(page.config, default=str, ensure_ascii=False)
    assert not any(text in flat for text in (STRINGS["nl"]["exact_part_export"], STRINGS["nl"]["exact_export_sample"], *columns.values()))
    assert not {getattr(f.fn, "__name__", "") for f in build_app(deps(tmp_path / "nofile", madeup_out)).fns.values()} & EXPORT_MINE
    own = tab(tmp_path / "own", madeup_out, **BUYER)[1]                                      # the page of this PC has them all
    assert EXPORT_MINE <= {getattr(f.fn, "__name__", "") for f in own.fns.values()}


def test_a_save_of_the_columns_from_a_form_the_file_no_longer_holds_is_refused_and_selecting_the_tab_fills_it_again(tmp_path, madeup_out):
    """Amendment 2 (ruling L6 of part 1): part 2 keeps what its form was filled with, the three export settings as the file held them
    (at the load, at each select of the settings tab and after each save). A save from a form that another browser tab overtook, or
    after the three settings changed in the file itself, writes nothing and says so; a change of part 1's settings does not count."""
    nl = STRINGS["nl"]
    path, page, made = tab(tmp_path, madeup_out, **(BUYER | {"export_columns": {"po_number": "Inkooporder", "item_code": "Artikel"},
                                                           "export_fill_down": True}))
    first, second = (named(EXPORT_OPENED, handler(page, "export_opened")(None, None, "nl")) for _ in range(2))     # two browser tabs
    assert first["export_filled"] == second["export_filled"] == {"export_columns": {"po_number": "Inkooporder", "item_code": "Artikel"},
                                                                 "export_decimal_comma": False, "export_fill_down": True}
    saved = named(EXPORT_SAVED, handler(page, "export_saved")(*export_args(EXPORT_NL, filled=first["export_filled"])))
    assert saved["note"] == nl["exact_export_saved"] and saved["export_filled"] == SAVED_EXPORT
    before = path.read_bytes()
    stale = named(EXPORT_SAVED, handler(page, "export_saved")(*export_args(EXPORT_NL, EXPORT_NL_FORM | {"description": NO_COLUMN},
                                                                           second["export_filled"])))
    assert (stale["note"], stale["state"], stale["export_filled"]) == (nl["exact_export_changed"], SAME, second["export_filled"])
    assert path.read_bytes() == before
    again = named(EXPORT_OPENED, handler(page, "export_opened")(None, None, "nl"))       # the settings tab selected again in tab 2
    assert again["export_filled"] == SAVED_EXPORT and again["field:description"]["value"] == "Omschrijving"
    landed = named(EXPORT_SAVED, handler(page, "export_saved")(*export_args(EXPORT_NL, EXPORT_NL_FORM | {"description": NO_COLUMN},
                                                                            again["export_filled"])))
    assert landed["note"] == nl["exact_export_saved"] and "description" not in held(path)["export_columns"]
    by_hand(path, export_decimal_comma=False)                                              # changed in the file itself
    refused = named(EXPORT_SAVED, handler(page, "export_saved")(*export_args(EXPORT_NL, filled=landed["export_filled"])))
    assert refused["note"] == nl["exact_export_changed"] and held(path)["export_decimal_comma"] is False
    assert handler(page, "export_saved")(*export_args(EXPORT_NL, filled=None))[0] == nl["exact_export_changed"]   # a fill the page lost
    fresh = named(EXPORT_OPENED, handler(page, "export_opened")(None, None, "nl"))
    by_hand(path, po_senders=["inkoop@example.org"])                                       # part 1's settings, changed meanwhile
    assert named(EXPORT_SAVED, handler(page, "export_saved")(*export_args(EXPORT_NL, filled=fresh["export_filled"])))["note"] == nl[
        "exact_export_saved"]
    assert nl["exact_export_changed"] == ("Niet opgeslagen: de koppeling is intussen op een andere plek gewijzigd. Open het tabblad Instellingen "
                                          "opnieuw en kijk de kolommen na.")
    assert STRINGS["en"]["exact_export_changed"] == ("Not saved: the connection was changed elsewhere meanwhile. Open the tab Settings again "
                                                     "and check the columns.")


def test_the_export_part_is_wired_as_the_page_shows_it(tmp_path, madeup_out):
    """The built page itself: part 2 below part 1 in the folded block, its controls in their order, and each handler's trigger, inputs
    and outputs; the fill at the load and at each select of the settings tab, as part 1's (amendment 2)."""
    gr = import_gradio()
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl = STRINGS["nl"]
    [settings] = [block for block in page.blocks.values() if isinstance(block, gr.Tab) and block.label == nl["settings_tab"]]
    [exact] = [block for block in settings.children if isinstance(block, gr.Accordion) and block.label == nl["exact_title"]]

    def inside(block) -> list:
        return [block, *[found for child in getattr(block, "children", []) for found in inside(child)]]
    [fill] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "export_opened"]
    states = {id(fill.outputs[EXPORT_OPENED.index("offered")]): "offered", id(fill.outputs[EXPORT_OPENED.index("export_filled")]): "export filled"}
    assert all(isinstance(fill.outputs[EXPORT_OPENED.index(state)], gr.State) for state in ("offered", "export_filled"))

    def name(component) -> str:
        if id(component) in states:
            return states[id(component)]
        if isinstance(component, gr.Button):
            return f"button {component.value}"
        if isinstance(component, (gr.Dataframe, gr.Markdown)) and component.elem_id:
            return component.elem_id
        return getattr(component, "label", None) or str(getattr(component, "value", ""))

    def bound(fn_name: str) -> tuple:
        [f] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == fn_name]
        return (sorted((name(page.blocks[block]) if block != page._id else "page", event) for block, event in f.targets),
                [name(component) for component in f.inputs], [name(component) for component in f.outputs])

    order = [block for block in inside(exact) if isinstance(block, (gr.Markdown, gr.Textbox, gr.Radio, gr.Button, gr.Dataframe, gr.Dropdown,
                                                                     gr.Checkbox, gr.File, gr.Number))]
    names = [name(block) for block in order]
    part = names[names.index(f"### {nl['exact_part_export']}"):names.index(f"### {nl['tryout_title']}")]    # Task 33's part follows, last
    labels = [field_label(field, "nl") for field in EXPORT_FORM]
    sample, row, numbers, tick, page_language = (nl["exact_export_sample"], nl["exact_export_header_row"], nl["exact_export_field:numbers"],
                                                 nl["exact_export_field:fill_down"], nl["language"])
    assert part == [f"### {nl['exact_part_export']}", nl["exact_export_intro"], sample, "export-head", "export-sample-note", row, *labels, numbers,
                    tick, f"button {nl['exact_test']}", f"button {nl['exact_save']}", "export-note", "export-read", "export-lines"]
    assert bound("export_opened") == ([(nl["settings_tab"], "select"), ("page", "load")], [sample, row, page_language],
                                      [*labels, numbers, tick, "offered", "export filled"])
    assert bound("export_sample_shown") == ([(sample, "change")], [sample, *labels, page_language],
                                            ["export-head", "export-sample-note", row, *labels, "offered", "export-read", "export-lines", "export-note"])
    assert bound("export_header_shown") == ([(row, "input")], [sample, row, *labels, page_language], [*labels, "offered", "export-sample-note"])
    inputs = [sample, *labels, numbers, tick, "export filled", page_language]
    assert bound("export_tested") == ([(f"button {nl['exact_test']}", "click")], inputs, ["export-read", "export-lines", "export-note"])
    assert bound("export_saved") == ([(f"button {nl['exact_save']}", "click")], inputs,
                                     ["export-note", "exact-state", "export filled", "export-read", "export-lines"])
    assert bound("export_relabel")[0] == [(page_language, "change")]
    shown = {name(block): block for block in order[names.index(f"### {nl['exact_part_export']}"):]}
    assert shown[sample].file_types is None and (shown[row].precision, shown[row].minimum, shown[row].value) == (0, None, None)   # M3, M4
    assert shown[nl["exact_meaning:line_id"]].info == nl["exact_export_help:line_id"]
    assert [shown[label].info for label in labels if label != nl["exact_meaning:line_id"]] == [None] * 11
    assert [(shown[label].choices, shown[label].value) for label in labels] == [([(nl["exact_export_no_column"], NO_COLUMN)], NO_COLUMN)] * 12
    assert shown[numbers].choices == [(nl["exact_numbers:comma"], "comma"), (nl["exact_numbers:point"], "point")]
    assert all(shown[table].interactive is False for table in ("export-head", "export-lines")) and shown["export-head"].label == nl["exact_export_head"]


def test_switching_the_language_relabels_the_export_part_and_keeps_what_is_chosen(tmp_path, madeup_out):
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl, en = STRINGS["nl"], STRINGS["en"]
    chosen = [EXPORT_NL_FORM[field] for field in EXPORT_FORM]
    shown = named(RELABELLED_EXPORT, handler(page, "export_relabel")("en", str(EXPORT_NL), 3, *chosen, EXPORT_NL_TITLES, "comma"))
    assert (shown["part"], shown["intro"]) == (f"### {en['exact_part_export']}", en["exact_export_intro"])
    assert (shown["sample"]["label"], shown["row"]["label"], shown["head"]["label"]) == (en["exact_export_sample"], en["exact_export_header_row"],
                                                                                         en["exact_export_head"])
    assert shown["head"]["value"] == {"headers": ["Row", *"ABCDEFGHIJ"], "data": table_head(EXPORT_NL)} and shown["sample_note"] == ""
    assert [shown[name]["label"] for name in CHOSEN] == [field_label(field, "en") for field in EXPORT_FORM]
    assert [shown[name]["choices"] for name in CHOSEN] == [offered(EXPORT_NL_TITLES, "en")] * 12
    assert [shown[name]["value"] for name in CHOSEN] == chosen and shown["field:line_id"]["info"] == en["exact_export_help:line_id"]
    assert (shown["numbers"]["label"], shown["numbers"]["value"]) == (en["exact_export_field:numbers"], "comma")
    assert shown["numbers"]["choices"] == [(en["exact_numbers:comma"], "comma"), (en["exact_numbers:point"], "point")]
    assert (shown["fill_down"]["label"], shown["test"]["value"], shown["save"]["value"]) == (en["exact_export_field:fill_down"], "Test", "Save")
    assert (shown["note"], shown["reading"], shown["lines"]["value"]) == ("", "", {"headers": export_headers(None, "en"), "data": []})
    flat = json.dumps(shown, default=str, ensure_ascii=False)
    assert nl["exact_export_sample"] not in flat and nl["exact_export_no_column"] not in flat and nl["exact_export_help:line_id"] not in flat
    assert (nl["exact_part_export"], en["exact_part_export"]) == ("2. Inkooporders als export uit Exact (voor de controle voor het bestellen)",
                                                                  "2. Purchase orders as an export from Exact (for the check before ordering)")
    assert nl["exact_export_intro"] == ("Voor het bestellen bestaat de pdf van de inkooporder nog niet. Geef de assistent dan een export van de "
                                        "orderregels uit Exact (Excel of csv). Hier stelt u een keer in welke kolom wat bevat.")
    assert en["exact_export_intro"] == ("Before ordering the PDF of the purchase order does not exist yet. Then give the assistant an export of "
                                        "the order lines from Exact (Excel or CSV). Here you set once which column holds what.")
    assert (nl["exact_export_field:numbers"], en["exact_export_field:numbers"]) == ("Getallen die als tekst in het bestand staan",
                                                                                    "Numbers that stand in the file as text")
    assert (nl["exact_export_field:fill_down"], en["exact_export_field:fill_down"]) == (
        "Het inkoopordernummer staat alleen op de eerste regel van elke inkooporder",
        "The purchase order number stands on the first line of each purchase order only")
    assert (nl["exact_export_help:line_id"], en["exact_export_help:line_id"]) == (
        "Zonder kolom telt de assistent de regels van elke inkooporder in de volgorde van het bestand.",
        "Without a column the assistant counts the lines of each purchase order in the order of the file.")
    refused = named(RELABELLED_EXPORT, handler(page, "export_relabel")("en", str(tmp_path / "gone.xls"), None, *NONE_CHOSEN, [], "point"))
    assert refused["sample_note"] == en["exact_export_refused:file_type"] and refused["head"]["value"]["data"] == []


def test_the_export_sample_is_read_where_the_page_library_put_it_and_copied_nowhere(tmp_path, madeup_out):
    """Rule 6: the page library hands the sample over as a file of its own temporary folder (the folder version of Task 30 puts that
    folder inside the aid's folder). Choosing, testing and saving read it there: no copy of it is made, nothing is filed in the PO store
    and no case is opened."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    upload = tmp_path / "gradio" / "1b2c3d4e"
    upload.mkdir(parents=True)
    sample = upload / "EXPORT_nl.csv"
    shutil.copy(EXPORT_NL, sample)
    digest = hashlib.sha256(sample.read_bytes()).hexdigest()
    handler(page, "export_sample_shown")(str(sample), *NONE_CHOSEN, "nl")
    handler(page, "export_header_shown")(str(sample), 3, *NONE_CHOSEN, "nl")
    handler(page, "export_tested")(*export_args(sample))
    assert named(EXPORT_SAVED, handler(page, "export_saved")(*export_args(sample)))["note"] == STRINGS["nl"]["exact_export_saved"]
    copies = [p for p in tmp_path.rglob("*") if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == digest]
    assert copies == [sample] and sorted(p.name for p in upload.iterdir()) == ["EXPORT_nl.csv"]
    assert not (tmp_path / "po_store").exists() and not (tmp_path / "inbox").exists() and not list(tmp_path.glob("cases/2503*"))


def test_after_a_save_the_runner_files_the_export_and_the_state_says_when(tmp_path, madeup_out):
    """With the columns saved, an export in the watched folder is filed as looks at its POs at the next pass (Task 40), and the state
    lines of the block say that the columns are set and when the last export was filed (amendment 3)."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl = STRINGS["nl"]
    assert named(EXPORT_SAVED, handler(page, "export_saved")(*export_args(EXPORT_NL)))["note"] == nl["exact_export_saved"]
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    shutil.copy(EXPORT_NL, inbox / "EXPORT_nl.csv")
    store = PoStore(tmp_path / "po_store")
    scanned = scan_folder(inbox, store, tmp_path / "cases", load_settings(path), datetime(2026, 11, 12, 9, 0))
    assert [look.po_number for look in store.snapshots("250301")] == ["250301"] and store.versions("250301") == []
    assert not (inbox / "EXPORT_nl.csv").exists() and scanned.skipped == []
    state = handler(page, "exact_state_shown")("nl")
    moment = store.filed_at("250301", "e1").strftime("%Y-%m-%d %H:%M")
    assert nl["exact_state_columns_set"] in state and nl["exact_state_export"].format(time=moment) in state
    # an export whose column titles differ from those saved is no export to the runner: the digest lists it with this reason
    write_sheet(inbox / "other.csv", [["Order", "Artikel"], ["250301", "KM-3101"]])
    other = scan_folder(inbox, store, tmp_path / "cases", load_settings(path), datetime(2026, 11, 12, 9, 1))
    assert other.skipped == [("other.csv", NOT_AN_EXPORT)]


def test_the_manual_says_how_the_columns_are_set_and_what_the_digest_says_of_another_spreadsheet():
    """Step 6 with amendment 4: MANUAL 3.17 has part 2 (quoting the page word for word, the worked example on EXPORT_nl.csv, the advice
    to compare three lines with Exact, the test walk B4), says that an export whose titles differ from those saved is listed in the
    digest under "Overgeslagen" as a spreadsheet without the export's column titles, and 3.2 says that the block writes the three
    settings. The review round (rulings M1 to M3, M7, M8 and M13): part 2's own advice is asserted within part 2's text; step 5 tells the
    rows passed over apart; the first and the last order are shown, and a "Totaal" line in the last one means a total row; any file
    reaches the block's sentences; each refusal names the column or the row where the reader gives one and says what to do; a purchase
    order known from an export counts among those the assistant knows; and part 1's test walk reports the lines without a count. The
    second review round (rulings M15 to M18): the last order shown is the order of the file's last order line, also the PO the file
    names last when that is another, and a "Totaal" line in it means a total row to which the tick gives the number of the order
    above it; a CSV file is cut at the mark that gives the whole file the most filled cells (ruling M21); the sentence for a file that
    is no PDF says that its name is the cause; and part 2's test walk reports every line the block shows above the table."""
    nl, manual = STRINGS["nl"], manual_sections()
    exact = manual["3.17"]
    part2 = exact[exact.index(f"**Part 2, \"{nl['exact_part_export']}\".**"):]
    for key in ("exact_part_export", "exact_export_sample", "exact_export_head", "exact_export_header_row", "exact_export_field:po_number",
                "exact_export_field:item_code", "exact_export_no_column", "exact_export_help:line_id", "exact_export_field:numbers",
                "exact_numbers:comma", "exact_export_field:fill_down", "exact_test", "exact_save", "exact_export_saved", "exact_state_columns_set",
                "exact_state_columns_unset", "exact_state_no_export", "exact_export_first_and_last"):
        assert f"\"{nl[key]}\"" in exact, key                                                # quoted word for word
    assert EXPORT_NL_READ in part2 and "Laatste export opgenomen" in exact
    assert "EXPORT_nl.csv" in part2 and "B4" in part2 and "later parts are to follow" not in exact
    assert "Compare three lines of that order at least with the same order in Exact" in part2                  # M13: part 2's own advice
    assert "Compare three lines of the first and the last order with Exact's screen" in part2
    assert "report the lines under \"Stand van de koppeling\" in words" in exact and "report the three lines" not in exact
    assert f"\"{NOT_AN_EXPORT}\"" in part2 and "\"Overgeslagen\"" in part2                   # amendment 4
    # M1: step 5 tells the rows passed over apart
    assert "the rows without an item code" in part2 and "rows with an item code but no quantity" in part2
    assert "a date, a time or an error value" in part2 and "Excel can turn a code into a date" in part2
    # M2: the first and the last order, and what a "Totaal" line in the last one means
    assert "the first and the last order" in part2 and "make the export without totals" in part2
    # M3: any file reaches the block, which answers by its own sentences
    assert f"\"{nl['exact_export_refused:file_type']}\"" in part2 and f"\"{nl['exact_problem:not_pdf']}\"" in exact
    assert "Invalid file type" not in exact
    # M7: each refusal names the column or the row where the reader gives one, and says what to do
    assert "names the column or the row where the reader gives one, and says what to do" in part2
    assert nl["exact_export_refused:no_column"].format(column="\"Netto\"") in part2
    # M8: a purchase order known from an export counts among those the assistant knows
    assert "not counted there" not in exact and "\"uit een export\"" in exact
    # M15: the order of the file's last order line is shown, also when the file names another PO last
    assert "the order of the file's last order line" in part2 and "the order the file names last when that is another one" in part2
    assert "a total row to which the tick gives the number of the order above it" in part2 and "a total row which the tick" not in part2
    # M16 and M21: the mark that gives the whole file the most filled cells, then the mark of the widest row
    assert "at the mark under which the whole file holds the most filled cells" in part2 and "most rows of three cells" not in part2
    assert "on a tie, at the mark under which the widest row holds the most filled cells" in part2
    # M17: part 1's sentence for a file that is no PDF names the cause, its name
    assert "a file whose name does not end in .pdf" in exact
    # M18: part 2's test walk reports every line above the table
    assert ("report in words every line the block shows above the table: the line \"Gelezen: ...\", the line on rows with an item code "
            "but no quantity, each line \"Niet gelezen\" and each problem (check B4)") in part2
    settings = manual["3.2"]
    assert "`export_columns`, `export_decimal_comma` and `export_fill_down`" in settings
    rest = settings[settings.index("Only the installation sets the rest"):settings.index("Set these in the file")]
    assert "export_" not in rest


# --- the review round of part 2 (rulings M3, M4, M5 and M11) ---

def test_every_file_reaches_the_sentences_of_the_block(tmp_path, madeup_out):
    """Ruling M3 (findings 2 and 9): the sample fields of part 1 and of part 2 take any file, so that the page library refuses none in
    English before a handler runs, and the block answers in the page's language: part 2 with the brief's sentence for a file that is
    neither .xlsx nor .csv, part 1 with its own sentence for a file whose name does not end in .pdf, which says so (ruling M17: it is
    true for a PDF saved without its suffix too, which the runner passes by as well)."""
    from gradio.data_classes import FileData
    gr = import_gradio()
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl, en = STRINGS["nl"], STRINGS["en"]
    [part1] = [block for block in page.blocks.values() if isinstance(block, gr.File) and block.label == nl["exact_sample"]]
    [part2] = [block for block in page.blocks.values() if isinstance(block, gr.File) and block.elem_id == "export-sample"]
    before = path.read_bytes()
    for name in ("export.xls", "notes.txt", "order.docx"):
        other = tmp_path / name
        other.write_bytes(b"made-up")
        for field in (part1, part2):                                      # the page library lets the file through to the handler
            assert field.file_types is None and field.preprocess(FileData(path=str(other))) == str(other), name
        shown = named(EXPORT_SAMPLE_SHOWN, handler(page, "export_sample_shown")(str(other), *NONE_CHOSEN, "nl"))
        assert shown["sample_note"] == nl["exact_export_refused:file_type"], name
        assert named(EXPORT_TESTED, handler(page, "export_tested")(*export_args(other)))["reading"] == nl["exact_export_refused:file_type"]
        assert named(SAMPLE_SHOWN, handler(page, "exact_sample_shown")(str(other), "en"))["sample_note"] == en["exact_problem:not_pdf"]
        found = named(TITLE_FOUND, handler(page, "exact_title_found")(str(other), "Artikelcode", [], *[None] * SLOTS, "nl"))
        assert found["title_note"] == nl["exact_problem:not_pdf"]
        assert named(TESTED, handler(page, "exact_tested")(*exact_args(other)))["note"] == nl["exact_problem:not_pdf"]
        saved = named(EXACT_SAVED, handler(page, "exact_saved")(*exact_args(other)))
        assert saved["note"] == nl["exact_not_saved"].format(problems=nl["exact_problem:not_pdf"]), name
        relabelled = named(RELABELLED, handler(page, "exact_relabel")("en", str(other), "comma", [], *[None] * SLOTS))
        assert relabelled["sample_note"] == en["exact_problem:not_pdf"]
    assert path.read_bytes() == before
    cut = tmp_path / "cut.pdf"                                            # a .pdf that cannot be opened keeps ruling L11's sentence
    cut.write_bytes(ANDERS_PO.read_bytes()[:1500])
    assert named(SAMPLE_SHOWN, handler(page, "exact_sample_shown")(str(cut), "nl"))["sample_note"] == nl["exact_problem:unopenable"]
    unnamed = tmp_path / "inkooporder"                                    # a PDF saved without its suffix: refused by its name
    unnamed.write_bytes(ANDERS_PO.read_bytes())
    assert named(SAMPLE_SHOWN, handler(page, "exact_sample_shown")(str(unnamed), "nl"))["sample_note"] == nl["exact_problem:not_pdf"]
    assert nl["exact_problem:not_pdf"] == ("Dit bestand is geen pdf-bestand: zijn naam eindigt niet op .pdf. Kies de pdf van een inkooporder "
                                           "zoals Exact die verstuurt.")
    assert en["exact_problem:not_pdf"] == ("This file is no PDF: its name does not end in .pdf. Choose the PDF of a purchase order as Exact "
                                           "sends it.")


def test_the_row_field_takes_no_value_or_0_and_part_2_is_filled_and_relabelled(tmp_path, madeup_out):
    """Ruling M4 (finding 8): the row field has no minimum, and an empty value, 0 or a negative number counts as no row typed, so that
    the row is found by itself. The events that take the row (each select of the settings tab, the language switch and the row's own
    change) run through the page library's own preprocessing with these values, and part 2 is filled again and relabelled."""
    import asyncio

    from gradio.state_holder import SessionState
    path, page, made = tab(tmp_path, madeup_out, **(BUYER | {"export_columns": {"po_number": "Inkooporder", "item_code": "Artikel"}}))
    en = STRINGS["en"]
    [row] = [block for block in page.blocks.values() if getattr(block, "elem_id", None) == "export-row"]
    assert row.minimum is None and [row.preprocess(value) for value in (None, 0, -2)] == [None, 0, -2]

    def run(name: str, inputs: list) -> list:
        [fn] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == name]
        return asyncio.run(page.process_api(block_fn=fn, inputs=inputs, state=SessionState(page)))["data"]

    for typed in (None, 0, -1):
        opened = dict(zip(EXPORT_OPENED, run("export_opened", [None, typed, "nl"])))          # a select of the settings tab
        assert (opened["field:po_number"]["value"], opened["field:item_code"]["value"]) == ("Inkooporder", "Artikel"), typed
        assert ["Inkooporder", "Inkooporder"] in opened["field:po_number"]["choices"] and opened["numbers"] == "point"
        relabelled = dict(zip(RELABELLED_EXPORT, run("export_relabel", ["en", None, typed, *NONE_CHOSEN, None, "point"])))
        assert relabelled["part"] == f"### {en['exact_part_export']}" and relabelled["row"]["label"] == en["exact_export_header_row"], typed
        assert relabelled["field:line_id"]["info"] == en["exact_export_help:line_id"]
        changed = dict(zip(EXPORT_HEADER_SHOWN, run("export_header_shown", [None, typed, *NONE_CHOSEN, "nl"])))
        assert changed["field:po_number"] == {"__type__": "update"}, typed                     # no sample: nothing to offer, no error
    # with a sample, an empty row and 0 both find the row with the column titles by themselves
    sample = tmp_path / "EXPORT_nl.csv"
    shutil.copy(EXPORT_NL, sample)
    for typed in (None, 0, -1):
        offered = named(EXPORT_HEADER_SHOWN, handler(page, "export_header_shown")(str(sample), typed, *NONE_CHOSEN, "nl"))["offered"]
        assert offered == EXPORT_NL_TITLES, typed


def test_a_sample_cut_at_tabs_or_commas_offers_its_titles_on_row_1(tmp_path, madeup_out):
    """Ruling M5 (findings 3 and 11): a tab-separated export with decimal commas, one in UTF-16 with its byte order mark, and a
    comma-separated one with semicolons in a description: the table of the first rows shows each title in a cell of its own, the row
    field shows 1, the dropdowns offer the titles, and "Testen" reads what the export reader reads."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    tabs, wide, commas = write_cut_samples(tmp_path / "samples")      # the reviewers' three samples, as they hold them
    for sample, shown_titles, form in ((tabs, TABS_TITLES, ("Inkooporder", "Artikel", "Aantal", "comma")),
                                       (wide, WIDE_TITLES, ("Inkooporder", "Artikel", "Aantal", "comma")),
                                       (commas, COMMAS_TITLES, ("PO Number", "Item", "Qty", "point"))):
        shown = named(EXPORT_SAMPLE_SHOWN, handler(page, "export_sample_shown")(str(sample), *NONE_CHOSEN, "nl"))
        assert shown["head"]["data"][0] == ["1", *shown_titles] and shown["row"] == 1 and shown["offered"] == shown_titles, sample.name
        assert shown["field:item_code"]["choices"] == offered(shown_titles)
        po_number, item_code, quantity, numbers = form
        chosen = dict.fromkeys(EXPORT_FORM, NO_COLUMN) | {"po_number": po_number, "item_code": item_code, "quantity": quantity,
                                                          "numbers": numbers, "fill_down": False}
        tested = named(EXPORT_TESTED, handler(page, "export_tested")(*export_args(sample, chosen)))
        reading = read_export(sample, {"po_number": po_number, "item_code": item_code, "quantity": quantity}, numbers == "comma")
        assert [line[:3] for line in tested["lines"]["value"]["data"]] == [
            [doc.po_reference.value, line.line_id, line.item_code.value] for doc in reading.documents[:1] + reading.documents[1:][-1:]
            for line in doc.lines], sample.name
        assert [line[3].split(" (")[0] for line in tested["lines"]["value"]["data"]] == [
            str(line.quantity.value) for doc in reading.documents[:1] + reading.documents[1:][-1:] for line in doc.lines], sample.name
        assert tested["note"] == "" and tested["reading"].startswith("Gelezen: "), sample.name


# --- the second review round of part 2 (rulings M15 to M18) ---

def test_testing_an_export_sorted_by_delivery_date_shows_the_order_that_holds_its_total(tmp_path, madeup_out):
    """Ruling M15 (finding 1 of the check of the fix round): "Testen" on an export sorted by delivery date that ends in a total row, with
    the tick, shows the order that the total row joins, which is neither the first order nor the PO the file names last, beside those
    two; on a longer one too, whose total row stands far below the first rows the block shows."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    chosen = dict.fromkeys(EXPORT_FORM, NO_COLUMN) | SORTED_BY_DATE_COLUMNS | {"numbers": "comma", "fill_down": True}
    for sample, orders, total in ((SORTED_BY_DATE, ["250301", "250302", "250303"], "250302"),
                                  (SORTED_BY_DATE_LONG, ["250301", "250306", "250305"], "250306")):
        tested = named(EXPORT_TESTED, handler(page, "export_tested")(*export_args(sample, chosen)))
        table = tested["lines"]["value"]["data"]
        assert list(dict.fromkeys(row[0] for row in table)) == orders and tested["note"] == "", sample.name
        assert [row[0] for row in table if row[2] == "Totaal"] == [total], sample.name
        assert tested["reading"].endswith(STRINGS["nl"]["exact_export_first_and_last"]), sample.name


def test_a_sample_with_commas_in_its_title_line_or_footer_offers_its_titles(tmp_path, madeup_out):
    """Rulings M16 and M21 (findings 2 and 5 of the check of the fix round): a semicolon export with a title line and a footer that hold
    commas, one with the title line only, a tab export with such a title (also in UTF-16) and a sample whose last title holds two
    commas: the table of the first rows shows each title in a cell of its own, the row field shows the row of the titles, the dropdowns
    offer them, and "Testen" reads what the export reader reads."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    footer, title, tabs, wide, tie = write_title_samples(tmp_path / "samples")      # the checkers' five samples, as they hold them
    for sample, row, titles, fill_down in ((footer, 3, EXPORT_NL_TITLES, True), (title, 3, EXPORT_NL_TITLES, True), (tabs, 3, TITLE_COMMAS, False),
                                           (wide, 3, TITLE_COMMAS, False), (tie, 1, TIE_TITLES, False)):
        shown = named(EXPORT_SAMPLE_SHOWN, handler(page, "export_sample_shown")(str(sample), *NONE_CHOSEN, "nl"))
        assert shown["head"]["data"][row - 1][:len(titles) + 1] == [str(row), *titles], sample.name
        assert shown["row"] == row and shown["offered"] == titles and shown["field:item_code"]["choices"] == offered(titles), sample.name
        chosen = dict.fromkeys(EXPORT_FORM, NO_COLUMN) | {"po_number": "Inkooporder", "item_code": "Artikel", "unit_price": "Prijs",
                                                          "numbers": "comma", "fill_down": fill_down}
        tested = named(EXPORT_TESTED, handler(page, "export_tested")(*export_args(sample, chosen)))
        reading = read_export(sample, {"po_number": "Inkooporder", "item_code": "Artikel", "unit_price": "Prijs"}, True, fill_down)
        assert [row[:3] for row in tested["lines"]["value"]["data"]] == [
            [doc.po_reference.value, line.line_id, line.item_code.value] for doc in reading.documents[:1] + reading.documents[1:][-1:]
            for line in doc.lines] and tested["note"] == "", sample.name


def test_a_select_of_the_tab_or_a_language_switch_with_the_same_sample_reads_it_no_more(tmp_path, madeup_out, monkeypatch):
    """Ruling M11 (finding 5): choosing a sample reads it once, for its first rows and its titles; a select of the settings tab and a
    language switch with the same sample (the same path, size and modification time) use what was read; "Testen" reads it once; a sample
    changed on disk is read again."""
    import aid.readers.po_export as po_export
    import aid.review.exact_settings as exact
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    sample = tmp_path / "EXPORT_nl.csv"
    shutil.copy(EXPORT_NL, sample)
    reads, real = [], po_export.csv_text

    def counted(file):
        reads.append(Path(file).name)
        return real(file)

    for module in (po_export, exact):
        monkeypatch.setattr(module, "csv_text", counted)
    shown = named(EXPORT_SAMPLE_SHOWN, handler(page, "export_sample_shown")(str(sample), *NONE_CHOSEN, "nl"))
    assert reads == ["EXPORT_nl.csv"] and shown["row"] == 3
    opened = named(EXPORT_OPENED, handler(page, "export_opened")(str(sample), shown["row"], "nl"))        # a select of the settings tab
    relabelled = named(RELABELLED_EXPORT, handler(page, "export_relabel")("en", str(sample), shown["row"], *NONE_CHOSEN, shown["offered"],
                                                                          "point"))
    named(EXPORT_OPENED, handler(page, "export_opened")(str(sample), None, "nl"))                        # the row field emptied
    assert reads == ["EXPORT_nl.csv"] and opened["offered"] == EXPORT_NL_TITLES
    assert relabelled["head"]["value"]["data"] == table_head(EXPORT_NL)
    reads.clear()
    handler(page, "export_tested")(*export_args(sample))
    assert reads == ["EXPORT_nl.csv"]                                                                     # one read for a trial
    stamp = sample.stat().st_mtime_ns + 2_000_000_000
    os.utime(sample, ns=(stamp, stamp))                                                                   # the same file saved again
    reads.clear()
    named(EXPORT_OPENED, handler(page, "export_opened")(str(sample), 3, "nl"))
    assert reads == ["EXPORT_nl.csv"]


def test_the_tables_of_part_2_keep_their_titles_whole_and_scroll_inside_the_block(tmp_path, madeup_out):
    """Ruling M12 (as Task 32's ruling L10 for part 1): at 1366 by 768 a column title of part 2's two tables breaks between its words
    only, never inside one ("Rij" stays whole), and a table wider than the block scrolls sideways inside it: a style rule of the page for
    the two tables, which the browser check of the review round measured (no title broken inside a word, both tables scrolling)."""
    gr = import_gradio()
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    heads = [block.head for block in page.blocks.values() if isinstance(block, gr.HTML) and block.head and "#export-head" in block.head]
    assert heads == ["<style>#export-head th, #export-head th *, #export-lines th, #export-lines th * { word-break: normal; overflow-wrap: "
                     "normal; }</style>"]
    without = tab(tmp_path / "share", madeup_out, share=True, **BUYER)[1]          # nothing of part 2 on a share link, its style neither
    assert not [block for block in without.blocks.values() if isinstance(block, gr.HTML) and block.head and "#export-head" in block.head]


# --- the part "Proefdraaien" at the end of the block, and the switch of the test mode (Task 33) ---

TRYOUT_OPENED = ("switch", "filled", "why", "controls", "po", "download", "note")    # the fill of the part, in the order of its outputs
TRYOUT_MADE = ("po", "download", "note")                    # what "Proefbevestiging maken (pdf)" and "... direct aan de assistent geven" give
SWITCHED = ("switch", "filled", "why", "controls", "po", "note")                      # what the switch gives
TRYOUT_RELABELLED = ("title", "intro", "switch", "why", "po", "change", "make", "give", "note")
TRYOUT_HANDLERS = {"tryout_opened", "tryout_refreshed", "tryout_made", "tryout_given", "tryout_chosen", "test_mode_saved", "tryout_relabel"}
NOW = datetime(2026, 11, 12, 9, 0)                    # the page's clock in these tests (deps())
SHOWN_PART, HIDDEN_PART = {"__type__": "update", "visible": True}, {"__type__": "update", "visible": False}
NO_DOWNLOAD = {"__type__": "update", "value": None, "visible": False}


def files(folder: Path) -> dict[Path, bytes]:
    """Every file under the folder, with its bytes."""
    return {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}


def test_the_try_out_is_offered_in_the_test_mode_without_a_limit_only(tmp_path, madeup_out, monkeypatch):
    """Rule 1: the part "Proefdraaien" offers a try-out only while test_mode is true and no authorization limit is set; otherwise it
    shows one line that says why, and its two buttons say the same and write nothing. Offered, its dropdown holds the POs the store
    knows; "Proefbevestiging maken (pdf)" makes the PDF for the download in the page library's temporary folder, and "Proefbevestiging
    direct aan de assistent geven" puts it into the watched folder: no other file is written. A PO that gives no try-out is refused
    with its sentence, and nothing is written."""
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path / "gradio"))              # the page library's temporary folder of this test
    nl, en = STRINGS["nl"], STRINGS["en"]
    for n, values in enumerate(({}, {"test_mode": False}, {"test_mode": True, "authorization_limit": "5000.00"})):
        folder = tmp_path / f"not_offered_{n}"
        path, page, made = tab(folder, madeup_out, **(BUYER | values))
        PoStore(folder / "po_store").add(madeup_out / "MU08_po_v1.pdf")
        shown = named(TRYOUT_OPENED, handler(page, "tryout_opened")("nl"))
        assert (shown["why"], shown["controls"], shown["note"]) == (nl["tryout_not_offered"], HIDDEN_PART, ""), values
        before = files(tmp_path)
        assert named(TRYOUT_MADE, handler(page, "tryout_made")("250108", "price_small", "nl")) == {
            "po": SAME, "download": NO_DOWNLOAD, "note": nl["tryout_not_offered"]}
        assert named(TRYOUT_MADE, handler(page, "tryout_given")("250108", "price_small", "en"))["note"] == en["tryout_not_offered"]
        assert files(tmp_path) == before, values
    folder = tmp_path / "offered"
    path, page, made = tab(folder, madeup_out, **(BUYER | {"test_mode": True}))
    shown = named(TRYOUT_OPENED, handler(page, "tryout_opened")("nl"))
    assert (shown["switch"], shown["filled"], shown["why"], shown["controls"]) == (True, True, "", SHOWN_PART)
    assert shown["po"] == {"__type__": "update", "choices": [], "value": None} and shown["note"] == nl["tryout_none_known"]
    assert named(TRYOUT_MADE, handler(page, "tryout_given")(None, "none", "nl")) == {
        "po": {"__type__": "update", "choices": [], "value": None}, "download": NO_DOWNLOAD, "note": nl["tryout_none_known"]}
    po = PoStore(folder / "po_store").add(madeup_out / "MU08_po_v1.pdf")
    shown = named(TRYOUT_OPENED, handler(page, "tryout_opened")("nl"))
    assert shown["po"] == {"__type__": "update", "choices": [("250108 (S08)", "250108")], "value": "250108"} and shown["note"] == ""
    assert shown["download"] == NO_DOWNLOAD
    before = files(tmp_path)
    made_now = named(TRYOUT_MADE, handler(page, "tryout_made")("250108", "price_small", "nl"))
    download = Path(made_now["download"]["value"])
    assert made_now["note"] == nl["tryout_made"] and made_now["download"]["visible"] is True
    assert download.name == "PROEF orderbevestiging 250108 20261112-090000.pdf" and download.parent.parent == tmp_path / "gradio"
    assert download.read_bytes() == confirmation_pdf(po, "price_small", NOW) and made_now["po"] == SAME     # made at the page's clock
    assert set(files(tmp_path)) - set(before) == {download}                     # the read-back's own folder is gone again
    assert named(TRYOUT_MADE, handler(page, "tryout_given")("250108", "price_small", "nl")) == {
        "po": SAME, "download": NO_DOWNLOAD, "note": nl["tryout_given"]}          # the download of the choice before goes (ruling Y14)
    inbox = folder / "inbox"
    assert [p.name for p in inbox.iterdir()] == [download.name] and (inbox / download.name).read_bytes() == download.read_bytes()
    assert set(files(tmp_path)) - set(before) == {download, inbox / download.name}               # the work copy is gone again
    # a PO whose item code is too long for the try-out's column, known from an export: refused with its sentence, nothing written
    export = folder / "long_code.csv"
    export.write_text("po_number,line_id,item_code,description,quantity,unit,unit_price,price_basis,delivery_date\n"
                      "250150,1,KM-0801-WWWWWWWWWWWWWWWWWWWWWWWWWWWWWW,Manometer 0-10 bar,3,st,12.50,1,2026-11-16\n", encoding="utf-8")
    PoStore(folder / "po_store").from_export(export, EXPORT_COLUMNS)
    before = files(tmp_path)
    assert named(TRYOUT_MADE, handler(page, "tryout_made")("250150", "none", "nl")) == {
        "po": SAME, "download": NO_DOWNLOAD, "note": nl["tryout_refused"].format(reason=nl["tryout_reason:item_code"])}
    assert named(TRYOUT_MADE, handler(page, "tryout_given")("250150", "none", "en"))["note"] == en["tryout_refused"].format(
        reason=en["tryout_reason:item_code"])
    assert files(tmp_path) == before
    assert nl["tryout_not_offered"] == "Proefdraaien kan alleen in de proefstand en zolang er geen fiatteringsgrens is ingesteld."
    assert en["tryout_not_offered"] == "Trying out is possible only in the test mode and while no authorization limit is set."
    assert nl["tryout_made"] == ("De proefbevestiging is gemaakt. Stuur haar als bijlage naar uw eigen postvak, naar de map die de assistent "
                                 "leest, of geef haar direct aan de assistent.")
    assert en["tryout_made"] == ("The try-out confirmation was made. Send it as an attachment to your own mailbox, to the folder the assistant "
                                 "reads, or give it to the assistant directly.")
    assert nl["tryout_given"] == ("De proefbevestiging ligt in de bewaakte map. De assistent opent de zaak bij de volgende ronde, binnen een "
                                  "minuut.")
    assert en["tryout_given"] == "The try-out confirmation lies in the watched folder. The assistant opens the case at its next pass, within a minute."
    assert nl["tryout_none_known"] == "De assistent kent nog geen inkooporders. Laat eerst een inkooporder uit Exact binnenkomen."
    assert en["tryout_none_known"] == "The assistant knows no purchase orders yet. Let a purchase order from Exact come in first."
    assert (nl["tryout_refused"], en["tryout_refused"]) == ("Van deze inkooporder kan geen proefdocument worden gemaakt: {reason}.",
                                                            "No try-out document can be made of this purchase order: {reason}.")


def test_the_switch_writes_the_test_mode_only(tmp_path, madeup_out):
    """Rule 10: the switch "Proefstand: de assistent werkt met het test-Exact" writes test_mode into the settings file and nothing else:
    the key is added at the end of a file that lacks it and replaced where it stands after that, every other key keeping its place and
    its bytes. The part follows the switch: offered while the test mode is on (no limit is set here), the line that says why when off."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl, en = STRINGS["nl"], STRINGS["en"]
    before = path.read_text(encoding="utf-8")
    shown = named(TRYOUT_OPENED, handler(page, "tryout_opened")("nl"))
    assert (shown["switch"], shown["filled"], shown["why"], shown["controls"]) == (False, False, nl["tryout_not_offered"], HIDDEN_PART)
    on = named(SWITCHED, handler(page, "test_mode_saved")(True, shown["filled"], "nl"))
    assert (on["switch"], on["filled"], on["why"], on["controls"], on["note"]) == (True, True, "", SHOWN_PART, nl["tryout_saved"])
    after = path.read_text(encoding="utf-8")
    assert after == before[:-2] + ',\n    "test_mode": true\n}' and held(path) == json.loads(before) | {"test_mode": True}
    assert load_settings(path).test_mode is True
    off = named(SWITCHED, handler(page, "test_mode_saved")(False, on["filled"], "en"))
    assert (off["switch"], off["filled"], off["why"], off["controls"], off["note"]) == (False, False, en["tryout_not_offered"], HIDDEN_PART,
                                                                                        en["tryout_saved"])
    assert path.read_text(encoding="utf-8") == after.replace('"test_mode": true', '"test_mode": false')
    assert (nl["tryout_switch"], nl["tryout_saved"]) == ("Proefstand: de assistent werkt met het test-Exact", "Opgeslagen.")
    assert (en["tryout_switch"], en["tryout_saved"]) == ("Test mode: the assistant works with the test Exact", "Saved.")


def test_the_switch_refuses_a_save_when_the_file_changed_meanwhile(tmp_path, madeup_out):
    """Amendment 3: the switch keeps the value it was filled with, as Task 32's block keeps its form (ruling L6). When the file no
    longer holds that value (another browser tab, or the file itself, changed it meanwhile), a toggle writes nothing and the switch is
    filled again from the file, which the note says in the switch's own sentence (ruling Y6); so is a switch whose fill the page no
    longer knows (the page was started again), and a file that cannot be read refuses with its line and writes nothing."""
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl = STRINGS["nl"]
    shown = named(TRYOUT_OPENED, handler(page, "tryout_opened")("nl"))
    by_hand(path, test_mode=True)
    before = path.read_bytes()
    refused = named(SWITCHED, handler(page, "test_mode_saved")(True, shown["filled"], "nl"))
    assert (refused["switch"], refused["filled"], refused["note"]) == (True, True, nl["tryout_switch_refilled"]) and path.read_bytes() == before
    assert (refused["why"], refused["controls"]) == ("", SHOWN_PART)                       # the part as the file now gives it
    again = named(SWITCHED, handler(page, "test_mode_saved")(False, None, "nl"))
    assert (again["switch"], again["filled"], again["note"]) == (True, True, nl["tryout_switch_refilled"]) and path.read_bytes() == before
    english = named(SWITCHED, handler(page, "test_mode_saved")(True, False, "en"))
    assert (english["switch"], english["note"]) == (True, STRINGS["en"]["tryout_switch_refilled"]) and path.read_bytes() == before
    # ruling Y6 (finding 8): the switch's own sentence, which asks the buyer to redo nothing
    assert nl["tryout_switch_refilled"] == ("De proefstand is intussen op een andere plek gewijzigd; de schakelaar toont nu wat het "
                                            "instellingenbestand bevat.")
    assert STRINGS["en"]["tryout_switch_refilled"] == "The test mode was changed elsewhere meanwhile; the switch now shows what the settings file holds."
    path.write_text("{ not json", encoding="utf-8")
    broken = named(SWITCHED, handler(page, "test_mode_saved")(False, True, "nl"))
    assert (broken["switch"], broken["note"]) == (True, unreadable("nl")) and path.read_text(encoding="utf-8") == "{ not json"


def test_the_try_out_part_stands_at_the_end_of_the_exact_block(tmp_path, madeup_out):
    """Amendment 5: the part is the last of the block "Koppeling met Exact", built by functions of its own, each handler bound as the
    page shows it, with strings of its own in one block after the block's own strings in each language, so that the parts other tasks
    add to the block merge beside it. The language switch relabels it and keeps what is chosen. On a share link and without a settings
    file it is not there."""
    gr = import_gradio()
    path, page, made = tab(tmp_path, madeup_out, **BUYER)
    nl, en = STRINGS["nl"], STRINGS["en"]
    [settings] = [block for block in page.blocks.values() if isinstance(block, gr.Tab) and block.label == nl["settings_tab"]]
    [exact, _] = [block for block in settings.children if isinstance(block, gr.Accordion)]

    def inside(block) -> list:
        return [block, *[found for child in getattr(block, "children", []) for found in inside(child)]]
    [fill] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "tryout_opened"]
    filled = fill.outputs[1]
    assert isinstance(filled, gr.State)

    def name(component) -> str:
        if component is filled:
            return "filled"
        if isinstance(component, gr.Button):
            return f"button {component.value}"
        if getattr(component, "elem_id", None):
            return component.elem_id
        return getattr(component, "label", None) or str(getattr(component, "value", ""))

    def bound(fn_name: str) -> tuple:
        [f] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == fn_name]
        return (sorted((name(page.blocks[block]) if block != page._id else "page", event) for block, event in f.targets),
                [name(component) for component in f.inputs], [name(component) for component in f.outputs])

    shown = [block for block in inside(exact) if isinstance(block, (gr.Markdown, gr.Checkbox, gr.Dropdown, gr.Radio, gr.Button, gr.File))]
    part = [name(block) for block in shown][[name(block) for block in shown].index(f"### {nl['tryout_title']}"):]
    assert part == [f"### {nl['tryout_title']}", nl["tryout_intro"], nl["tryout_switch"], "tryout-why", nl["tryout_po"], nl["tryout_change"],
                    f"button {nl['tryout_make']}", f"button {nl['tryout_give']}", "tryout-download", "tryout-note"]
    [controls] = [block for block in inside(exact) if getattr(block, "elem_id", None) == "tryout-controls"]
    assert controls.visible is False and [name(block) for block in inside(controls) if block in shown] == [
        nl["tryout_po"], nl["tryout_change"], f"button {nl['tryout_make']}", f"button {nl['tryout_give']}", "tryout-download"]
    [change] = [block for block in inside(exact) if getattr(block, "label", None) == nl["tryout_change"]]
    assert change.choices == [(nl[f"tryout_change:{value}"], value) for value in CHANGES] and change.value == "none"
    language = nl["language"]
    part_outputs = [nl["tryout_switch"], "filled", "tryout-why", "tryout-controls", nl["tryout_po"]]
    assert bound("tryout_opened") == ([(nl["settings_tab"], "select"), ("page", "load")], [language], [*part_outputs, "tryout-download", "tryout-note"])
    assert bound("tryout_made") == ([(f"button {nl['tryout_make']}", "click")], [nl["tryout_po"], nl["tryout_change"], language],
                                    [nl["tryout_po"], "tryout-download", "tryout-note"])
    assert bound("tryout_given") == ([(f"button {nl['tryout_give']}", "click")], [nl["tryout_po"], nl["tryout_change"], language],
                                     [nl["tryout_po"], "tryout-download", "tryout-note"])
    assert bound("tryout_refreshed") == ([(f"button {nl['exact_refresh']}", "click")], [language, nl["tryout_po"]],
                                         [*part_outputs, "tryout-download", "tryout-note"])           # ruling Y7: "Stand vernieuwen"
    assert bound("tryout_chosen") == ([(nl["tryout_po"], "input"), (nl["tryout_change"], "input")], [], ["tryout-download"])   # ruling Y14
    assert bound("test_mode_saved") == ([(nl["tryout_switch"], "input")], [nl["tryout_switch"], "filled", language], [*part_outputs, "tryout-note"])
    assert bound("tryout_relabel")[0] == [(language, "change")]
    assert bound("tryout_relabel")[1] == [language, nl["tryout_change"]]
    relabelled = named(TRYOUT_RELABELLED, handler(page, "tryout_relabel")("en", "price_small"))
    assert (relabelled["title"], relabelled["intro"], relabelled["why"], relabelled["note"]) == (
        f"### {en['tryout_title']}", en["tryout_intro"], en["tryout_not_offered"], "")
    assert (relabelled["switch"]["label"], relabelled["po"]["label"], relabelled["make"]["value"], relabelled["give"]["value"]) == (
        en["tryout_switch"], en["tryout_po"], en["tryout_make"], en["tryout_give"])
    assert relabelled["change"] == {"__type__": "update", "label": en["tryout_change"], "value": "price_small",
                                    "choices": [(en[f"tryout_change:{value}"], value) for value in CHANGES]}
    # the strings: one block of their own right after the block's own strings, in the same order in both languages
    for strings in (nl, en):
        keys = list(strings)
        mine = ["exact_state_to_enter", "hand_test_mode", "hand_tryout", *[key for key in keys if key.startswith("tryout_")]]
        start = keys.index(mine[0])                  # after the block's own strings, those of its parts (part 2 of Task 39) included
        assert keys[start:start + len(mine)] == mine and keys[start - 1].startswith("exact_")
        assert not [key for key in keys[start + len(mine):] if key.startswith("exact_")]
    assert [key for key in nl if key.startswith("tryout_")] == [key for key in en if key.startswith("tryout_")]
    assert [nl[f"tryout_change:{value}"] for value in CHANGES] == [
        "Niets: alles komt overeen", "De prijs van de eerste regel is 1 procent hoger", "De prijs van de eerste regel is 10 procent hoger",
        "De leverdatum van de eerste regel is drie dagen later", "De leverdatum van de eerste regel is zestig dagen later"]
    assert [en[f"tryout_change:{value}"] for value in CHANGES] == [
        "Nothing: everything agrees", "The price of the first line is 1 percent higher", "The price of the first line is 10 percent higher",
        "The delivery date of the first line is three days later", "The delivery date of the first line is sixty days later"]
    assert (nl["tryout_title"], en["tryout_title"]) == ("Proefdraaien met een inkooporder uit Exact", "Trying out with a purchase order from Exact")
    assert nl["tryout_intro"] == ("Zonder leverancier kunt u de assistent toch proberen: bij een inkooporder die de assistent kent, maakt de "
                                  "assistent een verzonnen orderbevestiging. U kiest wat er afwijkt. Het proefdocument blijft op deze pc.")
    assert en["tryout_intro"] == ("Without a supplier you can still try the assistant: for a purchase order it knows, the assistant makes a "
                                  "made-up order confirmation. You choose what differs. The try-out document stays on this PC.")
    assert (nl["tryout_po"], en["tryout_po"], nl["tryout_change"], en["tryout_change"]) == ("Inkooporder", "Purchase order", "Wat wijkt af?",
                                                                                            "What differs?")
    assert (nl["tryout_make"], en["tryout_make"]) == ("Proefbevestiging maken (pdf)", "Make try-out confirmation (PDF)")
    assert (nl["tryout_give"], en["tryout_give"]) == ("Proefbevestiging direct aan de assistent geven",
                                                      "Give the try-out confirmation to the assistant directly")
    for key in [key for key in nl if key.startswith("tryout_")]:
        assert re.findall(r"\{(\w+)\}", nl[key]) == re.findall(r"\{(\w+)\}", en[key]) and not re.search(r"[—–_]", nl[key] + en[key]), key
    # not there on a share link, nor without a settings file
    assert not {getattr(f.fn, "__name__", "") for f in tab(tmp_path / "share", madeup_out, share=True, **BUYER)[1].fns.values()} & TRYOUT_HANDLERS
    assert not {getattr(f.fn, "__name__", "") for f in build_app(deps(tmp_path / "nofile", madeup_out)).fns.values()} & TRYOUT_HANDLERS
    assert TRYOUT_HANDLERS <= {getattr(f.fn, "__name__", "") for f in page.fns.values()}


def offered_page(tmp_path: Path, madeup_out: Path, **values):
    """The page on a made-up settings file in the test mode without a limit, its PO store empty: (the folder, the page)."""
    path, page, made = tab(tmp_path, madeup_out, **(BUYER | {"test_mode": True} | values))
    return path.parent, page


def export_of(folder: Path, rows: str) -> None:
    """File the rows of an export (with the title row of the made-up exports) in the folder's PO store, as looks."""
    export = folder / "rows.csv"
    export.write_text("po_number,line_id,item_code,description,quantity,unit,unit_price,price_basis,delivery_date\n" + rows, encoding="utf-8")
    PoStore(folder / "po_store").from_export(export, EXPORT_COLUMNS)


def test_the_part_follows_the_store(tmp_path, madeup_out, monkeypatch):
    """Ruling Y7, finding 9: "Stand vernieuwen" fills the part again as the store now stands, the dropdown and its note, keeping the
    order chosen while the store knows it; and a button pressed with no order chosen fills the dropdown from the store before it
    answers, so that it never says that the assistant knows no purchase order while the store holds one."""
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path / "gradio"))
    folder, page = offered_page(tmp_path, madeup_out)
    nl = STRINGS["nl"]
    shown = named(TRYOUT_OPENED, handler(page, "tryout_opened")("nl"))
    assert (shown["po"]["choices"], shown["note"]) == ([], nl["tryout_none_known"])
    store = PoStore(folder / "po_store")
    store.add(madeup_out / "MU08_po_v1.pdf")                # the next pass of the runner files a PO
    refreshed = named(TRYOUT_OPENED, handler(page, "tryout_refreshed")("nl", None))
    assert refreshed["po"] == {"__type__": "update", "choices": [("250108 (S08)", "250108")], "value": "250108"} and refreshed["note"] == ""
    assert (refreshed["switch"], refreshed["filled"], refreshed["why"], refreshed["controls"], refreshed["download"]) == (
        True, True, "", SHOWN_PART, NO_DOWNLOAD)
    os.utime(store.root / "250108" / "v1" / "po.json", (1_700_000_000, 1_700_000_000))
    store.add(madeup_out / "MU01_po_v1.pdf")                # filed after 250108: the one filed last, but 250108 stays chosen
    kept = named(TRYOUT_OPENED, handler(page, "tryout_refreshed")("nl", "250108"))
    assert kept["po"]["value"] == "250108" and [number for _, number in kept["po"]["choices"]] == ["250101", "250108"]
    assert kept["download"] == SAME                         # the choice shown is the same, so is the download
    # a press with no order chosen: the dropdown filled from the store (the one filed last chosen) and the try-out of that order
    made = named(TRYOUT_MADE, handler(page, "tryout_made")(None, "none", "nl"))
    assert made["po"] == {"__type__": "update", "choices": [("250101 (S01)", "250101"), ("250108 (S08)", "250108")], "value": "250101"}
    assert made["note"] == nl["tryout_made"] and Path(made["download"]["value"]).name == "PROEF orderbevestiging 250101 20261112-090000.pdf"
    given = named(TRYOUT_MADE, handler(page, "tryout_given")(None, "none", "en"))
    assert given["po"]["value"] == "250101" and given["note"] == STRINGS["en"]["tryout_given"]


def test_the_language_switch_keeps_the_note_that_no_order_is_known(tmp_path, madeup_out):
    """Ruling Y13, finding 14: switching the page's language gives the note that the assistant knows no purchase order in the new
    language, while the part is offered and the store knows none; once the store knows one, the note is emptied as before."""
    folder, page = offered_page(tmp_path, madeup_out)
    for language in ("en", "nl"):
        assert named(TRYOUT_RELABELLED, handler(page, "tryout_relabel")(language, "none"))["note"] == STRINGS[language]["tryout_none_known"]
    PoStore(folder / "po_store").add(madeup_out / "MU08_po_v1.pdf")
    assert named(TRYOUT_RELABELLED, handler(page, "tryout_relabel")("en", "none"))["note"] == ""
    path, closed, made = tab(tmp_path / "off", madeup_out, **BUYER)                   # not offered: the line why, and no note
    relabelled = named(TRYOUT_RELABELLED, handler(closed, "tryout_relabel")("en", "none"))
    assert (relabelled["why"], relabelled["note"]) == (STRINGS["en"]["tryout_not_offered"], "")


def test_the_download_follows_the_choice(tmp_path, madeup_out, monkeypatch):
    """Ruling Y14: a new choice of the order or of the change, by the buyer, and a press of "Proefbevestiging direct aan de assistent
    geven" clear the download shown, so that the file offered is always the one of the choice shown."""
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path / "gradio"))
    folder, page = offered_page(tmp_path, madeup_out)
    PoStore(folder / "po_store").add(madeup_out / "MU08_po_v1.pdf")
    made = named(TRYOUT_MADE, handler(page, "tryout_made")("250108", "price_small", "nl"))
    assert made["download"]["visible"] is True
    assert handler(page, "tryout_chosen")() == NO_DOWNLOAD
    assert named(TRYOUT_MADE, handler(page, "tryout_given")("250108", "price_small", "nl"))["download"] == NO_DOWNLOAD


def test_the_order_filed_last_is_chosen(tmp_path, madeup_out):
    """Ruling Y10, mutant M12: with two purchase orders in the store, the dropdown offers both, the one filed last first and chosen."""
    folder, page = offered_page(tmp_path, madeup_out)
    store = PoStore(folder / "po_store")
    store.add(madeup_out / "MU08_po_v1.pdf")
    store.add(madeup_out / "MU01_po_v1.pdf")
    os.utime(store.root / "250108" / "v1" / "po.json", (1_700_000_000, 1_700_000_000))
    os.utime(store.root / "250101" / "v1" / "po.json", (1_700_000_060, 1_700_000_060))
    shown = named(TRYOUT_OPENED, handler(page, "tryout_opened")("nl"))
    assert shown["po"] == {"__type__": "update", "choices": [("250101 (S01)", "250101"), ("250108 (S08)", "250108")], "value": "250101"}
    os.utime(store.root / "250108" / "v1" / "po.json", (1_700_000_120, 1_700_000_120))
    assert named(TRYOUT_OPENED, handler(page, "tryout_opened")("nl"))["po"]["value"] == "250108"


def test_a_try_out_the_read_back_refuses_says_why_and_writes_nothing(tmp_path, madeup_out, monkeypatch):
    """Rulings Y1 and Y12 on the page, findings 1, 12 and 15: a try-out is read back before it leaves the page; one refused says why
    by the sentence of its reason, on both buttons and in both languages, and writes nothing: no download, nothing in the watched
    folder, and nothing left of the read-back. Never does the page say "Dit is niet gelukt (ValueError)." of a try-out the code
    knows."""
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path / "gradio"))
    folder, page = offered_page(tmp_path, madeup_out)
    export_of(folder, "260001,1,KM-0801,Manometer,3,st,12.50,1,2026-11-16\n"                     # a unit the font cannot print
                      "260001,2,KM-0802,Bout,3,\u03bcm,0.50,1,2026-11-16\n"
                      "260006,1,KM-0801,Manometer,3,verpakkingseenheid,12.50,1,2026-11-16\n"     # finding 15: a unit too wide
                      "260007,1,KM-0802,Bout,3,st,12.50,10000,2026-11-16\n"                      # and a price basis too wide
                      "IB5,1,KM-5001,Kogelkraan DN25,10,st,12.50,1,2026-11-16\n")                # finding 12: one line, a short number
    reasons = {"260001": "character", "260006": "value", "260007": "value", "IB5": "not_found"}
    before = files(tmp_path)
    for po_number, reason in reasons.items():
        for language in ("nl", "en"):
            strings = STRINGS[language]
            sentence = strings["tryout_refused"].format(reason=strings[f"tryout_reason:{reason}"])
            assert named(TRYOUT_MADE, handler(page, "tryout_made")(po_number, "price_small", language)) == {
                "po": SAME, "download": NO_DOWNLOAD, "note": sentence}, (po_number, language)
            assert named(TRYOUT_MADE, handler(page, "tryout_given")(po_number, "none", language))["note"] == sentence
    assert files(tmp_path) == before and not (folder / "inbox").exists() and not (folder / "cases").exists()


def test_switching_the_test_mode_off_names_the_changes_of_try_outs_still_open(tmp_path, madeup_out):
    """Ruling Y4(d), finding 5 (the reviewers' s3_switch_off.py): when the switch turns the test mode off while the list "In Exact
    invoeren" still holds changes that only try-out documents asked, its note says how many and that they are to be dropped, not
    entered in the real Exact; it still writes test_mode and nothing else (rule 10). It counts the try-out's changes only (ruling Y21):
    a supplier's own confirmation of the same order (MU08's) stands on the list beside them and is not counted. One change is named in
    the singular (ruling Y24). Without such changes the note is "Opgeslagen."."""
    from aid.decider import NoDecider
    from aid.execution.writer import PreparedWriter
    from aid.pipeline import Deps, read_with_parser, run_case
    from aid.sources.upload import open_upload_case
    from aid.tryout import made_from

    folder, page = offered_page(tmp_path, madeup_out)
    path = folder / "settings.json"
    settings = load_settings(path)
    store = PoStore(settings.po_store_root, settings.authorization_limit)
    store.add(madeup_out / "MU08_po_v1.pdf")
    made = folder / "made.pdf"
    made.write_bytes(confirmation_pdf(made_from(store, "250108"), "price_small", NOW))
    d = Deps(settings=settings, store=store, reader=read_with_parser, decider=NoDecider(), writer=PreparedWriter(settings.cases_root / "prepared"),
             mailer=None, clock=lambda: NOW)
    run_case(open_upload_case(settings.cases_root, [madeup_out / "MU08_conf.pdf"], "250108", "confirmation", NOW), d)    # the supplier's own
    run_case(open_upload_case(settings.cases_root, [made], "250108", "confirmation", NOW.replace(minute=1)), d)
    assert len(open_changes(settings.cases_root / "prepared")) == 4                # two of the supplier's, two of the try-out's
    nl, en = STRINGS["nl"], STRINGS["en"]
    shown = named(TRYOUT_OPENED, handler(page, "tryout_opened")("nl"))
    before = held(path)
    off = named(SWITCHED, handler(page, "test_mode_saved")(False, shown["filled"], "nl"))
    assert off["note"] == nl["tryout_saved_open"].format(n=2) and held(path) == before | {"test_mode": False}
    assert off["note"] == ("Opgeslagen. Op de lijst In Exact invoeren staan nog 2 wijzigingen uit proefdocumenten. Voer die niet in het echte "
                           "Exact in; laat ze vallen.")
    on = named(SWITCHED, handler(page, "test_mode_saved")(True, off["filled"], "en"))
    assert on["note"] == en["tryout_saved"]                                     # switched on: nothing to say
    off = named(SWITCHED, handler(page, "test_mode_saved")(False, on["filled"], "en"))
    assert off["note"] == ("Saved. The list Enter in Exact still holds 2 changes from try-out documents. Do not enter them in the real Exact; "
                           "drop them.")
    tryout_ids = [change.ids for change in open_changes(settings.cases_root / "prepared") if change.tryout]
    mark(settings.cases_root / "prepared", tryout_ids[0], "dropped", NOW)        # one of the try-out's two left: the singular
    for language, sentence in (("nl", "Opgeslagen. Op de lijst In Exact invoeren staat nog 1 wijziging uit een proefdocument. Voer die niet in "
                                      "het echte Exact in; laat die vallen."),
                               ("en", "Saved. The list Enter in Exact still holds 1 change from a try-out document. Do not enter it in the real "
                                      "Exact; drop it.")):
        on = named(SWITCHED, handler(page, "test_mode_saved")(True, off["filled"], language))
        off = named(SWITCHED, handler(page, "test_mode_saved")(False, on["filled"], language))
        assert off["note"] == sentence == STRINGS[language]["tryout_saved_open_one"]
    mark(settings.cases_root / "prepared", tryout_ids[1], "dropped", NOW)
    on = named(SWITCHED, handler(page, "test_mode_saved")(True, off["filled"], "nl"))
    assert named(SWITCHED, handler(page, "test_mode_saved")(False, on["filled"], "nl"))["note"] == nl["tryout_saved"]
    assert len(open_changes(settings.cases_root / "prepared")) == 2               # the supplier's own changes stay, to enter


def test_the_documents_say_what_the_second_review_round_of_the_try_out_settled():
    """The rulings of the second review round of Task 33 in the documents: MANUAL 2.1 says how the digest marks a try-out (Y17);
    tryout_mark's docstring and the README say that a try-out writes through the prepared writer only (Y18); 3.17 says how wide an
    item code may be (Y20), names the reason of a version the aid could not choose (Y22), says that the demo folder knows the made-up
    orders already (Y23), and begins the hand-over's step 1 by opening the block, which step 2 no longer does (Y25)."""
    nl, manual = STRINGS["nl"], manual_sections()
    digest, exact = manual["2.1"], manual["3.17"]
    assert '"(proefdocument)"' in digest and '"(zelf in Exact doen)"' in digest and "leaves its changes out" in digest
    mark_doc = " ".join(Path("aid/tryout_mark.py").read_text(encoding="utf-8").split())
    readme = Path("aid/README.md").read_text(encoding="utf-8")
    for text in (mark_doc, readme):
        assert "the prepared writer only, never through the Exact writer" in text
    assert "about 100 points" in exact and '"KM-08-00417-ABC-2026"' in exact and "such as twenty capitals" in exact
    assert "an item code of up to 20 characters prints whole" not in exact
    assert f'"{nl["tryout_reason:version"]}"' in exact
    assert "The demo folder knows the made-up orders already" in exact and 'such as 250108 under "Inkooporder"' in exact
    assert '1. Open "Koppeling met Exact (bij de installatie)" and switch the test mode off' in exact
    assert '2. Open "Koppeling met Exact' not in exact


def test_the_manual_walks_the_owners_test_and_the_hand_over_starts_empty_with_the_test_mode_off():
    """Step 5 and amendment 4: section 3.17 walks the owner's test (a purchase order from the test Exact, a try-out, the result) and
    names the part's controls as the page does; the hand-over lists of sections 3.16 and 3.17 say that the hand-over starts with an
    empty data folder and the test mode off; section 3.2 describes the setting."""
    nl, manual = STRINGS["nl"], manual_sections()
    exact, folder = manual["3.17"], manual["3.16"]
    for key in ("tryout_title", "tryout_switch", "tryout_po", "tryout_change", "tryout_change:price_small", "tryout_make", "tryout_give",
                "tryout_not_offered", "tryout_given", "hand_test_mode"):
        assert f"\"{nl[key]}\"" in exact, key                                               # quoted word for word
    assert "B3" in exact and "test Exact" in exact and "nothing made up" in exact
    for section in (exact, folder):
        assert "The hand-over starts with an empty data folder and the test mode off" in section
    assert "`test_mode`" in manual["3.2"] and "`test_mode`" in manual["3.16"]


def test_the_documents_say_what_the_review_round_of_the_try_out_settled():
    """The rulings of the review round of Task 33, in the manual, the README and settings.py: the try-out's reasons and notes quoted
    word for word in 3.17 (rulings Y1, Y4, Y6, Y12), the sentence on another number as it is after the read-back (Y5), when the
    dropdown is filled (Y7), the own-PC walk with the launcher and the first cases of a supplier (Y8), the tab's line of the test mode
    whatever the limit, ended by the switch alone (Y11), and switching the test mode off as a step of its own of the hand-over in 3.16
    and 3.17 (Y11)."""
    nl, manual = STRINGS["nl"], manual_sections()
    exact, folder, settings_section, tab_section = manual["3.17"], manual["3.16"], manual["3.2"], manual["2.6"]
    sentence = nl["tryout_refused"].format(reason=nl["tryout_reason:no_lines"])
    assert f"\"{sentence}\"" in exact
    for reason in ("no_price", "no_date", "character", "item_code", "value", "read_back", "not_found"):
        assert f"\"{nl[f'tryout_reason:{reason}']}\"" in exact, reason
    for key in ("tryout_switch_refilled", "tryout_nothing_sent", "tryout_none_known"):
        assert f"\"{nl[key]}\"" in exact, key
    assert f"\"{nl['tryout_saved_open'].format(n=2)}\"" in exact and "\"try-out document outside the test mode\"" in exact
    assert "\"not sent (try-out document: nothing is sent)\"" in exact and "\"(proefdocument)\"" in exact and "(proefdocument)" in tab_section
    for gone in ("Dit is niet gelukt (ValueError).", nl["settings_buyer_refilled"], "another number finds its order by the lines",
                 "so that no request to approve an order again can come of it", "prints wrong, which only the owner's own order shows"):
        assert gone not in exact, gone
    assert "a try-out is made only when the assistant would find its order" in exact and "refused with its reason" in exact     # Y5
    assert "with \"Stand vernieuwen\", so press \"Stand vernieuwen\" once the state names the order" in exact                      # Y7
    assert "python3 -m aid.start --settings live/settings.json" in exact and "section 3.4.1" in exact                               # Y8
    assert f"\"{nl['settings_field:first_cases_review']}\" to 5" in exact and "with 0 the aid takes the price over by itself" in exact
    assert "only its rows on the tab \"In Exact invoeren\" show the change" in exact
    assert "whatever `authorization_limit` holds" in settings_section and "only the switch ends it" in settings_section            # Y11
    assert "whatever the authorization limit" in tab_section
    readme = Path("aid/README.md").read_text(encoding="utf-8")
    assert "whatever the authorization limit" in readme and "only the switch on the settings tab ends it" in readme
    assert "only the switch on the settings tab ends it" in " ".join(Path("aid/settings.py").read_text(encoding="utf-8").split())
    assert "2. Switch the test mode off on the settings tab" in folder
    assert '1. Open "Koppeling met Exact (bij de installatie)" and switch the test mode off' in exact          # ruling Y25 of the second round
    for section in (exact, folder, settings_section, tab_section):
        assert not re.search(r"[—–]", section)
