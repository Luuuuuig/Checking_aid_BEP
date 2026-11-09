"""Tests of the settings tab of the review page (aid/review/page.py, its strings in aid/review/i18n.py) and of the six settings it
writes (aid/settings.py). The sign-in is a fake msal application and Microsoft Graph is FakeSession (aid/tests/fakes.py): no test
opens a browser or reaches the network."""
import json
import os
import stat
from datetime import datetime
from pathlib import Path

import pytest
import requests

from aid.connections.microsoft import COPILOT_SCOPES, MAIL_SCOPES, SHARED_SCOPES, MicrosoftAccount
from aid.connections.secrets import SecretStore
from aid.review.__main__ import company_values, refuse_share
from aid.review.i18n import STRINGS
from aid.review.page import build_app, import_gradio
from aid.settings import Settings, SettingsInvalid, load_settings, update_settings_file
from aid.tests.conftest import SETTINGS, ReachedMicrosoft, deps
from aid.tests.fakes import (ACCESS_TOKEN, CLIENT_ID, COPILOT, GRAPH, TENANT_ID, USERNAME, FakeApp, FakeResponse, FakeSession,
                             copilot_answer, copilot_session, sent_text, sign_in_event)

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
    assert renames == [(".settings.json.tmp", "settings.json")] and sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]
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
    assert note == STRINGS["nl"]["settings_saved"] == "De controle neemt dit over bij de volgende ronde"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert {key: saved[key] for key in SIX} == SIX and trigger == STRINGS["nl"]["settings_trigger_pending"]
    assert ACCESS_TOKEN not in path.read_text(encoding="utf-8") and STRINGS["en"]["settings_saved"] == "The check takes this over at the next pass"
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
    assert save(TENANT_ID, CLIENT_ID, "", FOLDER, "auto", listed, "nl")[0] == STRINGS["nl"]["settings_saved"]
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
    path = settings_file(tmp_path)
    built = []

    class Page:
        def launch(self, **options):
            built.append(options)

    monkeypatch.setattr(review, "build_app", lambda d, **options: (built.append(options), Page())[1])
    assert review.main(["--settings", str(path)]) == 0
    assert review.main(["--settings", str(path), "--share"]) == 0               # made-up settings: the link is allowed
    assert built == [{"settings_file": path, "share": False}, {"server_name": "127.0.0.1", "share": False},
                     {"settings_file": path, "share": True}, {"server_name": "127.0.0.1", "share": True}]


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
    assert after == before | {"reader": "llm"} and list(after) == list(before) and note == STRINGS["nl"]["settings_saved"]
    assert choice == "llm" and handler(page, "ai_opened")("en") == "llm"
    choice, note = handler(page, "save_reader")("parser", "en")
    assert json.loads(path.read_text(encoding="utf-8"))["reader"] == "parser" and note == STRINGS["en"]["settings_saved"]


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
    assert note == STRINGS["en"]["settings_not_saved"].format(fields=STRINGS["en"][label]) and value not in note
    assert path.read_bytes() == before
