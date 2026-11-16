"""Tests of the trigger of the post-check through the work mailbox (aid/sources/microsoft_mail.py) and of its place in the runner's
pass (aid/runner.py): the folder of the mailbox is read through Microsoft Graph, faked here by FakeSession, with a fake sign-in
(aid/tests/fakes.py). No test reaches Microsoft or the network."""
import base64
import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
import requests

import aid.runner
import aid.sources.microsoft_mail as microsoft_mail
from aid.connections.microsoft import SHARED_SCOPES, MicrosoftAccount, MicrosoftUnreachable, SignInNeeded
from aid.messaging import FileMailer
from aid.messaging.microsoft_mailer import AutoMailer, MicrosoftMailer
from aid.connections.secrets import SecretStore
from aid.review.intake import recent
from aid.runner import build_deps, main, run_loop
from aid.settings import Settings, load_settings
from aid.sources.microsoft_mail import MailSync, forget_connection, sync_mailbox
from aid.sources.microsoft_mail import _on_graph as ok_link
from aid.tests.conftest import REPO_ROOT, SETTINGS, deps
from aid.tests.fakes import ACCESS_TOKEN, CLIENT_ID, GRAPH, TENANT_ID, USERNAME, FakeApp, FakeResponse, FakeSession

NOW = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
CONNECTED = "2026-11-12T08:00:00Z"                   # NOW as the state file writes it
FOLDER = "AAMkADMadeUpFolder0001="                   # a made-up id of a mail folder, as Graph gives one
DELTA_1, DELTA_2, DELTA_3 = (f"{GRAPH}/me/mailFolders/{FOLDER}/messages/delta?$deltatoken=made-up-{n}" for n in (1, 2, 3))
NEXT_1 = f"{GRAPH}/me/mailFolders/{FOLDER}/messages/delta?$skiptoken=made-up-next-1"
LAPSED = "Postvak niet verbonden: meld u opnieuw aan op het tabblad Instellingen"


def baseline(owner: str = "me", folder: str = FOLDER) -> str:
    """The delta query that takes the baseline of a new connection, as Graph reads it (FakeSession compares URLs decoded)."""
    return f"{GRAPH}/{owner}/mailFolders/{folder}/messages/delta?changeType=created&$select=id,receivedDateTime"


def value_url(message_id: str, owner: str = "me") -> str:
    return f"{GRAPH}/{owner}/messages/{message_id}/$value"


def page(messages: list[dict], next_link: str | None = None, delta_link: str | None = None) -> FakeResponse:
    links = {"@odata.nextLink": next_link} if next_link else {"@odata.deltaLink": delta_link}
    return FakeResponse(json_data={"value": messages} | links)


def message(message_id: str, received: str) -> dict:
    return {"id": message_id, "receivedDateTime": received}


def mark(message_id: str) -> str:
    """How the state knows a message: the first 12 hex digits of the sha256 of its id."""
    return hashlib.sha256(message_id.encode()).hexdigest()[:12]


def eml_name(message_id: str, received: str) -> str:
    return f"{received[:19].replace('-', '').replace(':', '')}_{mark(message_id)}.eml"


def supplier_mail(subject: str = "Orderbevestiging", sender: str = "s01@example.com", **headers) -> bytes:
    """A made-up e-mail as Graph gives it ($value), from a supplier unless said otherwise."""
    lines = [f"From: {sender}", f"To: {USERNAME}", f"Subject: {subject}", *(f"{name.replace('_', '-')}: {value}" for name, value in headers.items())]
    return ("\r\n".join(lines) + "\r\n\r\nBijgaand.\r\n").encode()


def mail_settings(tmp_path: Path, **values) -> Settings:
    """The tests' settings with the cases and the watched folder under tmp_path and the trigger on: the made-up tenant and client
    and the folder FOLDER of the signed-in account's own mailbox."""
    return SETTINGS.model_copy(update={"cases_root": tmp_path / "cases", "watched_folder": tmp_path / "inbox", "ms_tenant_id": TENANT_ID,
                                       "ms_client_id": CLIENT_ID, "mail_folder": FOLDER, "mail_folder_name": "Orderbevestigingen"} | values)


def signed_in_account(tmp_path: Path, app: FakeApp | None = None) -> MicrosoftAccount:
    store = SecretStore(tmp_path / "secrets")
    return MicrosoftAccount(TENANT_ID, CLIENT_ID, store, app=(app or FakeApp(accounts=[USERNAME])).bind(store.token_cache()))


def connected(tmp_path: Path, delta_link: str | None = DELTA_1, known: tuple[str, ...] = (), **values) -> None:
    """The state file as an earlier sync of the folder left it, and the file of the marks of the messages it knows (by their ids)."""
    state = {"mailbox": None, "folder": FOLDER, "connected_at": CONNECTED, "delta_link": delta_link, "last_sync": CONNECTED, "saved": 0} | values
    (tmp_path / "cases").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cases" / "mailbox_state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "cases" / "mailbox_known.json").write_text(json.dumps([mark(message_id) for message_id in known]), encoding="utf-8")


def state(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "cases" / "mailbox_state.json").read_text(encoding="utf-8"))


def known(tmp_path: Path) -> set[str]:
    """The marks of the messages the connection knows."""
    path = tmp_path / "cases" / "mailbox_known.json"
    return set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()


def sync(tmp_path: Path, session: FakeSession, now: datetime = NOW, account: MicrosoftAccount | None = None, sleep=None, **values) -> MailSync:
    options = {"sleep": sleep} if sleep is not None else {}
    return sync_mailbox(account or signed_in_account(tmp_path), mail_settings(tmp_path, **values), now, session=session, **options)


# --- the trigger ---

@pytest.mark.parametrize("missing", ["mail_folder", "ms_tenant_id", "ms_client_id"])
def test_the_trigger_is_off_without_a_folder_or_the_ids(tmp_path, missing):
    session = FakeSession()
    assert sync(tmp_path, session, **{missing: None}) == MailSync([], 0, None)
    assert session.calls == [] and not (tmp_path / "cases" / "mailbox_state.json").exists()


def test_the_first_sync_takes_a_baseline_of_the_folder_and_saves_none_of_what_was_there(tmp_path):
    """The messages in the folder when it is connected, received before the connection, are known by their ids and never saved;
    no message's e-mail is asked for."""
    session = FakeSession({("GET", baseline()): page([message("OLD", "2026-11-01T07:00:00Z"), message("LAST", "2026-11-12T07:59:59Z")],
                                                     next_link=NEXT_1),
                           ("GET", NEXT_1): page([message("EARLIER", "2026-10-12T08:00:00Z")], delta_link=DELTA_1)})
    assert sync(tmp_path, session) == MailSync([], 3, None)
    assert unquote(session.calls[0]["url"]) == baseline() and session.calls[0]["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert len(session.calls) == 2 and not list((tmp_path / "inbox").iterdir())
    assert state(tmp_path) == {"mailbox": None, "folder": FOLDER, "connected_at": CONNECTED, "delta_link": DELTA_1, "last_sync": CONNECTED,
                               "saved": 0}
    assert known(tmp_path) == {mark(m) for m in ("OLD", "LAST", "EARLIER")}


@pytest.mark.parametrize("first", [FakeResponse(403, json_data={"error": {"code": "ErrorAccessDenied"}}), None],
                         ids=["the folder refused at first", "the sign-in lapsed at first"])
def test_mail_that_arrives_before_the_first_baseline_succeeds_is_saved(tmp_path, first):
    """The connection's time is kept from the first attempt: when the baseline is taken later, a message received since then is
    saved, and what was there before it is known."""
    if first is None:
        with pytest.raises(SignInNeeded):
            sync(tmp_path, FakeSession(), account=signed_in_account(tmp_path, FakeApp()))
    else:
        assert sync(tmp_path, FakeSession({("GET", baseline()): first})).error == "HTTP 403"
    assert (state(tmp_path)["delta_link"], known(tmp_path)) == (None, set())
    later = FakeSession({("GET", baseline()): page([message("OLD", "2026-11-01T07:00:00Z"), message("GAP", "2026-11-12T09:30:00Z")],
                                                   delta_link=DELTA_1),
                         ("GET", value_url("GAP")): FakeResponse(content=supplier_mail())})
    done = sync(tmp_path, later, now=NOW + timedelta(hours=3))
    assert [path.name for path in done.saved] == [eml_name("GAP", "2026-11-12T09:30:00Z")] and done.skipped == 1
    assert state(tmp_path)["connected_at"] == CONNECTED and known(tmp_path) == {mark("OLD"), mark("GAP")}


def test_a_later_sync_saves_each_new_message_whatever_its_receive_time_once_under_a_dot_name_first(tmp_path, monkeypatch):
    """A message dragged or moved by a rule into the folder is new there, also when it was received long before; a message of the
    baseline that Graph gives again (a change of its read state) and a message given again (a replay) are not saved."""
    connected(tmp_path, known=("BASE",))
    dragged, fresh = message("M1", "2026-11-01T09:01:00Z"), message("M2", "2026-11-12T09:02:30Z")
    session = FakeSession({("GET", DELTA_1): page([message("BASE", "2026-10-01T07:00:00Z"), dragged, fresh], delta_link=DELTA_2),
                           ("GET", value_url("M1")): FakeResponse(content=supplier_mail("M1")),
                           ("GET", value_url("M2")): FakeResponse(content=supplier_mail("M2")),
                           ("GET", DELTA_2): page([dragged], delta_link=DELTA_3)})
    renames, rename = [], os.replace
    monkeypatch.setattr(os, "replace", lambda source, target: (renames.append((Path(source).name, Path(target).name)), rename(source, target))[1])
    names = [eml_name("M1", dragged["receivedDateTime"]), eml_name("M2", fresh["receivedDateTime"])]
    assert names[0].startswith("20261101T090100_")
    done = sync(tmp_path, session, now=NOW + timedelta(hours=2))
    inbox = tmp_path / "inbox"
    assert done == MailSync([inbox / name for name in names], 1, None)
    assert [(inbox / name).read_bytes() for name in names] == [supplier_mail("M1"), supplier_mail("M2")]
    written = [(source, target) for source, target in renames if target.endswith(".eml")]
    assert [target for _, target in written] == names and all(source.startswith(".") for source, _ in written)
    assert sorted(path.name for path in inbox.iterdir()) == names                   # no file under a temporary name is left
    assert sync(tmp_path, session, now=NOW + timedelta(hours=3)) == MailSync([], 1, None)
    assert [request["url"] for request in session.calls].count(value_url("M1")) == 1
    assert state(tmp_path) | {"last_sync": None} == {"mailbox": None, "folder": FOLDER, "connected_at": CONNECTED, "delta_link": DELTA_3,
                                                    "last_sync": None, "saved": 2}
    assert state(tmp_path)["last_sync"] == "2026-11-12T11:00:00Z" and known(tmp_path) == {mark(m) for m in ("BASE", "M1", "M2")}


def test_the_marks_are_written_only_when_they_change(tmp_path):
    """The marks may be many (a large folder): a pass that lists nothing new leaves their file as it was."""
    connected(tmp_path, known=("BASE",))
    marks = tmp_path / "cases" / "mailbox_known.json"
    os.utime(marks, ns=(1_000_000_000, 1_000_000_000))
    session = FakeSession({("GET", DELTA_1): page([], delta_link=DELTA_2), ("GET", DELTA_2): page([message("BASE", "2026-10-01T07:00:00Z")],
                                                                                                   delta_link=DELTA_3)})
    sync(tmp_path, session)
    sync(tmp_path, session, now=NOW + timedelta(minutes=1))
    assert marks.stat().st_mtime_ns == 1_000_000_000 and state(tmp_path)["delta_link"] == DELTA_3 and "known" not in state(tmp_path)


def test_a_message_the_scan_has_handled_already_is_not_saved_again(tmp_path):
    connected(tmp_path)
    handled = tmp_path / "inbox" / "processed" / eml_name("M1", "2026-11-12T09:01:00Z")
    handled.parent.mkdir(parents=True)
    handled.write_bytes(b"handled")
    session = FakeSession({("GET", DELTA_1): page([message("M1", "2026-11-12T09:01:00Z")], delta_link=DELTA_2)})
    assert sync(tmp_path, session) == MailSync([], 1, None) and len(session.calls) == 1


def test_the_next_links_are_followed_to_the_delta_link(tmp_path):
    connected(tmp_path)
    session = FakeSession({("GET", DELTA_1): page([message("M1", "2026-11-12T08:00:00Z")], next_link=NEXT_1),
                           ("GET", NEXT_1): page([message("M2", "2026-11-12T08:00:01Z")], delta_link=DELTA_2),
                           ("GET", value_url("M1")): FakeResponse(content=supplier_mail()), ("GET", value_url("M2")): FakeResponse(content=supplier_mail())})
    assert len(sync(tmp_path, session).saved) == 2 and state(tmp_path)["delta_link"] == DELTA_2


def test_a_link_and_an_id_are_asked_as_graph_gives_them(tmp_path):
    """Graph's state tokens and message ids are opaque and may hold escapes: a link is called as Graph wrote it, and an id is
    escaped once in the address of its e-mail."""
    escaped = f"{GRAPH}/me/mailFolders/{FOLDER}/messages/delta?$deltatoken=made%2Bup%2F1"
    session = FakeSession({("GET", baseline()): page([], delta_link=escaped),
                           ("GET", escaped): page([message("AAMk/AB+cd=", "2026-11-12T09:00:00Z")], delta_link=DELTA_2),
                           ("GET", f"{GRAPH}/me/messages/AAMk%2FAB%2Bcd%3D/$value"): FakeResponse(content=supplier_mail())})
    sync(tmp_path, session)
    assert state(tmp_path)["delta_link"] == escaped
    assert len(sync(tmp_path, session, now=NOW + timedelta(minutes=1)).saved) == 1


@pytest.mark.parametrize("status", [429, 503])
def test_throttling_waits_the_retry_after_and_tries_once_more(tmp_path, status):
    connected(tmp_path)
    busy = FakeResponse(status, json_data={"error": {"code": "ApplicationThrottled", "message": "made-up"}}, headers={"retry-after": "7"})
    session = FakeSession({("GET", DELTA_1): [busy, page([message("M1", "2026-11-12T09:01:00Z")], delta_link=DELTA_2)],
                           ("GET", value_url("M1")): [busy, FakeResponse(content=supplier_mail())]})
    waits = []
    done = sync(tmp_path, session, sleep=waits.append)
    assert done.error is None and len(done.saved) == 1 and waits == [7, 7] and state(tmp_path)["delta_link"] == DELTA_2


def test_a_long_wait_is_cut_to_thirty_seconds_and_a_second_refusal_ends_the_sync(tmp_path):
    connected(tmp_path)
    session = FakeSession({("GET", DELTA_1): FakeResponse(429, headers={"Retry-After": "120"})})
    waits = []
    assert sync(tmp_path, session, sleep=waits.append) == MailSync([], 0, "HTTP 429")
    assert waits == [30] and len(session.calls) == 2 and state(tmp_path)["delta_link"] == DELTA_1


@pytest.mark.parametrize("refusals, error", [([FakeResponse(500)], "HTTP 500"),                 # an error of Graph's
                                             ([FakeResponse(503, headers={"Retry-After": "1"})] * 2, "HTTP 503")])   # throttled twice
def test_a_message_whose_e_mail_cannot_be_had_ends_the_sync_and_is_asked_for_again(tmp_path, refusals, error):
    """The files saved before stay and are counted, the delta link stays where it was, and the next sync saves the message."""
    connected(tmp_path)
    first, second = message("M1", "2026-11-12T09:01:00Z"), message("M2", "2026-11-12T09:02:00Z")
    session = FakeSession({("GET", DELTA_1): page([first, second], delta_link=DELTA_2),
                           ("GET", value_url("M1")): FakeResponse(content=supplier_mail()),
                           ("GET", value_url("M2")): [*refusals, FakeResponse(content=supplier_mail())]})
    done = sync(tmp_path, session, sleep=lambda seconds: None)
    assert done == MailSync([tmp_path / "inbox" / eml_name("M1", first["receivedDateTime"])], 0, error)
    assert (state(tmp_path)["delta_link"], state(tmp_path)["saved"]) == (DELTA_1, 1) and mark("M2") not in known(tmp_path)
    again = sync(tmp_path, session, now=NOW + timedelta(minutes=1))
    assert [path.name for path in again.saved] == [eml_name("M2", second["receivedDateTime"])] and again.skipped == 1


def test_a_401_is_sign_in_needed(tmp_path):
    connected(tmp_path)
    session = FakeSession({("GET", DELTA_1): FakeResponse(401, json_data={"error": {"code": "InvalidAuthenticationToken"}})})
    with pytest.raises(SignInNeeded):
        sync(tmp_path, session)


def test_a_lapsed_sign_in_asks_graph_nothing(tmp_path):
    connected(tmp_path)
    session = FakeSession()
    with pytest.raises(SignInNeeded):
        sync(tmp_path, session, account=signed_in_account(tmp_path, FakeApp()))
    assert session.calls == []


def test_a_microsoft_that_cannot_be_reached_is_microsoft_unreachable_without_its_address(tmp_path):
    """For the token or for Graph: the runner then keeps a lapse it noted (it learnt nothing about the sign-in)."""
    class Offline(FakeApp):
        def acquire_token_silent_with_error(self, scopes, account, **options):
            raise requests.ConnectionError(f"made-up failure for https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token")

    class Unreachable(FakeSession):
        def get(self, url, headers=None, timeout=None, **kwargs):
            raise requests.ConnectionError(f"made-up failure for {url}")

    connected(tmp_path)
    session = FakeSession()
    with pytest.raises(MicrosoftUnreachable, match="^ConnectionError$"):
        sync(tmp_path, session, account=signed_in_account(tmp_path, Offline(accounts=[USERNAME])))
    assert session.calls == []
    with pytest.raises(MicrosoftUnreachable, match="^ConnectionError$"):
        sync(tmp_path, Unreachable())
    assert state(tmp_path)["delta_link"] == DELTA_1


def test_a_folder_that_is_gone_is_named(tmp_path):
    session = FakeSession({("GET", baseline()): FakeResponse(404, json_data={"error": {"code": "ErrorItemNotFound"}})})
    assert sync(tmp_path, session) == MailSync([], 0, "mail folder not found")


@pytest.mark.parametrize("refusal", [FakeResponse(410, json_data={"error": {"code": "resyncRequired"}}),
                                     FakeResponse(400, json_data={"error": {"code": "syncStateNotFound", "message": "made-up"}})])
def test_a_delta_link_that_has_expired_takes_a_new_baseline_that_saves_what_arrived_since_the_last_read(tmp_path, refusal):
    """A message received since the last sync that read the folder is saved; an older one is known, also one moved into the folder
    while the link was broken (the limit the manual states)."""
    connected(tmp_path, known=("OLD",), last_sync="2026-11-12T09:00:00Z")
    session = FakeSession({("GET", DELTA_1): refusal,
                           ("GET", baseline()): page([message("OLD", "2026-11-01T07:00:00Z"), message("GAP", "2026-11-12T09:30:00Z"),
                                                      message("MOVED", "2026-10-01T07:00:00Z")], delta_link=DELTA_2),
                           ("GET", value_url("GAP")): FakeResponse(content=supplier_mail())})
    done = sync(tmp_path, session, now=NOW + timedelta(days=1))
    assert done.error is None and [path.name for path in done.saved] == [eml_name("GAP", "2026-11-12T09:30:00Z")]
    assert (state(tmp_path)["connected_at"], state(tmp_path)["delta_link"]) == (CONNECTED, DELTA_2)
    assert known(tmp_path) == {mark("OLD"), mark("GAP"), mark("MOVED")}


def test_a_baseline_that_fails_part_way_stores_nothing_new_and_the_next_one_saves_the_message(tmp_path):
    connected(tmp_path, known=("OLD",), last_sync="2026-11-12T09:00:00Z")
    listed = page([message("OLD", "2026-11-01T07:00:00Z"), message("GAP", "2026-11-12T09:30:00Z"), message("NEW", "2026-11-12T09:40:00Z")],
                  delta_link=DELTA_2)
    session = FakeSession({("GET", DELTA_1): FakeResponse(410), ("GET", baseline()): listed,
                           ("GET", value_url("GAP")): [FakeResponse(500), FakeResponse(content=supplier_mail())],
                           ("GET", value_url("NEW")): FakeResponse(content=supplier_mail())})
    assert sync(tmp_path, session, now=NOW + timedelta(days=1)).error == "HTTP 500"
    assert (state(tmp_path)["delta_link"], known(tmp_path)) == (DELTA_1, {mark("OLD")})
    done = sync(tmp_path, session, now=NOW + timedelta(days=1, minutes=1))
    assert sorted(path.name for path in done.saved) == sorted(eml_name(m, r) for m, r in (("GAP", "2026-11-12T09:30:00Z"),
                                                                                           ("NEW", "2026-11-12T09:40:00Z")))
    assert state(tmp_path)["delta_link"] == DELTA_2 and known(tmp_path) == {mark("OLD"), mark("GAP"), mark("NEW")}


OFF_GRAPH = ["https://attacker.example.org\\@graph.microsoft.com/v1.0/me/mailFolders/x/messages/delta?$deltatoken=t",   # a backslash
             "https://user@graph.microsoft.com/v1.0/me/mailFolders/x/messages/delta?$deltatoken=t",                      # userinfo
             "https://graph.microsoft.com.example.org/v1.0/me/messages/delta",                                          # a look-alike
             "https://graph.example.org/v1.0/me/mailFolders/x/messages/delta?$deltatoken=t",
             "http://graph.microsoft.com/v1.0/me/mailFolders/x/messages/delta?$deltatoken=t",                            # not https
             "https://graph.microsoft.com:8443/v1.0/me/mailFolders/x/messages/delta?$deltatoken=t",                      # a port
             "https://graph.microsoft.com/v1.0/me/mailFolders/x/messages/delta?$deltatoken=t\tx",                        # a tab
             "https://graph.microsoft.com /v1.0/me/messages/delta",                                                     # a space
             "https://[graph.microsoft.com]/v1.0/me/mailFolders/x/messages/delta?$deltatoken=t",                         # brackets, which
             "https://graph.microsoft.com]/v1.0/me/mailFolders/x/messages/delta?$deltatoken=t",                          # urlsplit refuses
             "https://[/v1.0/me/mailFolders/x/messages/delta?$deltatoken=t",
             "https://graph.microsoft.com/v1.0/me/messages?$top=100",                                    # every message of the mailbox
             "https://graph.microsoft.com/v1.0/me/mailFolders/x/messages?$top=100",                      # a folder, but no delta
             "https://graph.microsoft.com/v1.0/me/messages/delta?$deltatoken=t",                         # a delta without a folder
             "https://graph.microsoft.com/v1.0/me/mailFolders/x/../../messages/delta?$deltatoken=t",     # a folder only in name
             "https://graph.microsoft.com/v1.0/me/mailFolders/x/%2E%2E/%2e%2e/messages/delta?$deltatoken=t"]


@pytest.mark.parametrize("link", OFF_GRAPH)
def test_a_link_off_graph_gets_no_token_and_a_new_delta_is_started(tmp_path, link):
    """A delta link in the state file, or a next link, that does not lead to https://graph.microsoft.com (on port 443) beyond
    doubt is not called: the token would go with it. The sync ends with an error and the next one starts a new delta."""
    connected(tmp_path, delta_link=link)
    session = FakeSession()
    assert sync(tmp_path, session) == MailSync([], 0, "unexpected link host") and session.calls == []
    assert state(tmp_path)["delta_link"] is None
    restart = FakeSession({("GET", baseline()): page([], delta_link=DELTA_2)})
    assert sync(tmp_path, restart).error is None and state(tmp_path)["delta_link"] == DELTA_2
    connected(tmp_path)
    paged = FakeSession({("GET", DELTA_1): page([], next_link=link)})
    assert sync(tmp_path, paged).error == "unexpected link host" and len(paged.calls) == 1


@pytest.mark.parametrize("link", [
    f"{GRAPH}/me/mailFolders/AAMkADMade%2BUp%2FFolder%3D/messages/delta?$deltatoken=made-up-4",              # an id %-escaped
    f"{GRAPH}/me/mailFolders('AAMkADMade%2BUp%2FFolder%3D')/messages/delta?$deltatoken=made-up-4",           # Graph's other spelling
    f"{GRAPH}/users/orders@example.com/MailFolders('AAMkADMadeUpFolder0001=')/Messages/Delta?$skiptoken=made-up-4",
    "https://GRAPH.microsoft.com:443/v1.0/me/mailfolders/AAMkADMadeUpFolder0001=/messages/DELTA?$deltatoken=made-up-4"])
def test_a_delta_link_of_a_folder_in_either_spelling_of_graph_is_followed(tmp_path, link):
    """The folder id itself is not compared (Graph's own spelling of it in its links cannot be checked), only that the link is a
    delta query of a mail folder, in any case and after its %-escapes are decoded."""
    assert ok_link(link)
    connected(tmp_path, delta_link=link)
    session = FakeSession({("GET", link): page([], delta_link=DELTA_2)})
    assert sync(tmp_path, session) == MailSync([], 0, None) and state(tmp_path)["delta_link"] == DELTA_2


def test_a_tampered_link_with_a_backslash_sends_the_token_nowhere_through_requests_itself(tmp_path):
    """requests reads "https://attacker.example.org\\@graph.microsoft.com/..." as a request to attacker.example.org, while urlsplit
    names graph.microsoft.com: over a real requests session, with an adapter that records where it would send, nothing is sent."""
    sent = []

    class Recorder(requests.adapters.BaseAdapter):
        def send(self, request, **options):
            sent.append((urlsplit(request.url).hostname, request.headers.get("Authorization")))
            raise requests.ConnectionError("recorded, not sent")

        def close(self):
            pass

    connected(tmp_path, delta_link=OFF_GRAPH[0])
    with requests.Session() as session:
        session.mount("https://", Recorder())
        session.mount("http://", Recorder())
        assert sync(tmp_path, session).error == "unexpected link host"
    assert sent == []
    assert ok_link("https://graph.microsoft.com:443/v1.0/me/mailFolders/x/messages/delta?$deltatoken=t")


def test_the_aids_own_e_mails_are_not_saved_and_the_replies_to_it_are(tmp_path):
    """The digest and the copies of the aid's own questions come back into the mailbox it sends from: they carry Auto-Submitted:
    auto-generated with the digest's subject or the aid's Message-ID (32 hex digits at a domain the aid sends from: the mailbox's,
    the signed-in account's, aid_email's). A supplier's reply, a bounce and a supplier's automatic confirmation whose Message-ID is
    32 hex digits at the supplier's own domain are saved."""
    connected(tmp_path)
    own_id = f"<{'ab' * 16}@example.com>"
    mails = {"DIGEST": supplier_mail("[Inkoopassistent] Dagoverzicht 12-11-2026", USERNAME, Auto_Submitted="auto-generated",
                                     Message_ID="<AM0PR01MB0001@AM0PR01MB0001.prod.example.com>"),
             "COPY": supplier_mail("Vraag over orderbevestiging PO 250111", USERNAME, Auto_Submitted="auto-generated", Message_ID=own_id),
             "REPLY": supplier_mail("Re: Vraag over orderbevestiging PO 250111", "s11@example.com", In_Reply_To=own_id),
             "BOUNCE": supplier_mail("Undeliverable: Vraag over orderbevestiging PO 250111", "postmaster@example.com",
                                     Auto_Submitted="auto-replied"),
             "SYSTEM": supplier_mail("Orderbevestiging 250111", "orders@example.org", Auto_Submitted="auto-generated",
                                     Message_ID=f"<{'cd' * 16}@example.org>")}                # 32 hex digits at the supplier's own domain
    session = FakeSession({("GET", DELTA_1): page([message(name, "2026-11-12T09:00:00Z") for name in mails], delta_link=DELTA_2),
                           **{("GET", value_url(name)): FakeResponse(content=content) for name, content in mails.items()}})
    done = sync(tmp_path, session)
    assert [path.name for path in done.saved] == [eml_name(name, "2026-11-12T09:00:00Z") for name in ("REPLY", "BOUNCE", "SYSTEM")]
    assert done.skipped == 2 and {mark("DIGEST"), mark("COPY")} <= known(tmp_path)


def test_another_folder_starts_a_new_connection(tmp_path):
    connected(tmp_path, folder="AAMkADMadeUpFolderEarlier=", saved=5, known=("M1",))
    later = NOW + timedelta(days=1)
    session = FakeSession({("GET", baseline()): page([message("M2", "2026-11-13T07:00:00Z")], delta_link=DELTA_1)})
    assert sync(tmp_path, session, now=later) == MailSync([], 1, None)
    assert state(tmp_path) | {"last_sync": None} == {"mailbox": None, "folder": FOLDER, "connected_at": "2026-11-13T08:00:00Z",
                                                    "delta_link": DELTA_1, "last_sync": None, "saved": 0}
    assert known(tmp_path) == {mark("M2")}


def test_another_mailbox_is_read_by_its_address_with_the_shared_scopes_and_starts_a_new_connection(tmp_path):
    connected(tmp_path)
    owner, app = "users/orders@example.com", FakeApp(accounts=[USERNAME])
    session = FakeSession({("GET", baseline(owner=owner)): page([], delta_link=DELTA_1),
                           ("GET", DELTA_1): page([message("M1", "2026-11-12T08:00:00Z")], delta_link=DELTA_2),
                           ("GET", value_url("M1", owner=owner)): FakeResponse(content=supplier_mail())})
    sync(tmp_path, session, account=signed_in_account(tmp_path, app), mailbox="orders@example.com")
    assert state(tmp_path)["mailbox"] == "orders@example.com" and state(tmp_path)["delta_link"] == DELTA_1
    assert len(sync(tmp_path, session, account=signed_in_account(tmp_path, app), mailbox="orders@example.com").saved) == 1
    assert {request["method"] for request in session.calls} == {"GET"}              # the mailbox is only read
    scopes = [call[1] for call in app.calls if call[0] == "acquire_token_silent_with_error"]
    assert scopes and all(set(SHARED_SCOPES) <= set(asked) for asked in scopes)


# --- the trigger in the runner's pass ---

def confirmation_email() -> bytes:
    """A made-up supplier's e-mail with the confirmation of the made-up PO 250101 (aid/madeup/out) attached, as Graph gives it ($value)."""
    mail = EmailMessage()
    mail["From"], mail["To"], mail["Subject"] = "s01@example.com", USERNAME, "Orderbevestiging"
    mail["Message-ID"] = "<made-up-0001@example.com>"
    mail.set_content("Bijgaand onze orderbevestiging.")
    mail.add_attachment((REPO_ROOT / "aid" / "madeup" / "out" / "MU01_conf.pdf").read_bytes(), maintype="application", subtype="pdf",
                        filename="MU01_conf.pdf")
    return bytes(mail)


def mail_deps(tmp_path, madeup_out, clock=None):
    """deps() with the trigger on (mail_settings), the watched folder made, and the clock given, NOW unless given."""
    d = deps(tmp_path, madeup_out)
    (tmp_path / "inbox").mkdir(exist_ok=True)
    return d.model_copy(update={"settings": mail_settings(tmp_path), "clock": clock or (lambda: NOW)})


def test_the_pass_reads_the_mailbox_before_the_scan_so_that_a_confirmation_opens_its_case_in_that_pass(tmp_path, madeup_out):
    d = mail_deps(tmp_path, madeup_out)
    received = "2026-11-12T08:00:30Z"
    session = FakeSession({("GET", baseline()): page([], delta_link=DELTA_1),
                           ("GET", DELTA_1): page([message("M1", received)], delta_link=DELTA_2),
                           ("GET", value_url("M1")): FakeResponse(content=confirmation_email())})
    account, stop, root = signed_in_account(tmp_path), tmp_path / "STOP", tmp_path / "cases"
    assert run_loop(d, stop, poll_seconds=0, max_passes=1, account=account, session=session) == 1     # the baseline is taken
    assert not list(root.glob("250101_*"))
    assert run_loop(d, stop, poll_seconds=0, max_passes=1, account=account, session=session) == 1
    [case] = root.glob("250101_*")
    assert (case / "steps" / "08_result.json").exists() and (case / "input" / "MU01_conf.pdf").exists()
    assert (tmp_path / "inbox" / "processed" / eml_name("M1", received)).exists()
    assert not (root / "runner.log").exists()
    # the e-mail the mailbox saved is a file of the watched folder: the tab 'Bestanden' names what came of its attachment (Task 37)
    assert [(line["file"], line["outcome"], line["detail"]) for line in recent(root)] == [
        (f"{eml_name('M1', received)}/MU01_conf.pdf", "case", case.name)]


def mailbox_lines(root: Path) -> list[str]:
    log = root / "runner.log"
    return [line for line in log.read_text(encoding="utf-8").splitlines() if " mailbox: " in line] if log.exists() else []


def test_a_lapsed_sign_in_is_logged_once_an_hour_and_named_in_the_digest_while_the_folder_goes_on(tmp_path, madeup_out):
    now = [datetime(2026, 11, 12, 9, 0)]                     # the runner's clock: local time without a zone
    d = mail_deps(tmp_path, madeup_out, clock=lambda: now[0])
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    lapsed, session, stop, root = signed_in_account(tmp_path, FakeApp()), FakeSession(), tmp_path / "STOP", tmp_path / "cases"
    run_loop(d, stop, poll_seconds=0, max_passes=2, account=lapsed, session=session)
    assert len(mailbox_lines(root)) == 1 and "Settings tab" in mailbox_lines(root)[0] and ACCESS_TOKEN not in mailbox_lines(root)[0]
    [case] = root.glob("250101_*")                           # the watched folder was handled all the same
    assert (case / "steps" / "08_result.json").exists()
    now[0] += timedelta(minutes=59)
    run_loop(d, stop, poll_seconds=0, max_passes=1, account=lapsed, session=session)
    assert len(mailbox_lines(root)) == 1
    now[0] += timedelta(minutes=2)
    run_loop(d, stop, poll_seconds=0, max_passes=1, account=lapsed, session=session)
    assert len(mailbox_lines(root)) == 2 and session.calls == []
    now[0] = datetime(2026, 11, 12, 17, 0)                   # past the digest time
    run_loop(d, stop, poll_seconds=0, max_passes=1, account=lapsed, session=session)
    digest = (root / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")
    assert digest.count(LAPSED) == 1 and LAPSED in (root / "digests" / "2026-11-12.html").read_text(encoding="utf-8")
    back = FakeSession({("GET", baseline()): page([], delta_link=DELTA_1)})      # the baseline the lapse kept from being taken
    run_loop(d, stop, poll_seconds=0, max_passes=1, account=signed_in_account(tmp_path), session=back)
    assert "mailbox_lapsed" not in json.loads((root / "digest_state.json").read_text(encoding="utf-8"))
    times = [line.split(" ", 1)[0] for line in mailbox_lines(root)]      # the pass at 17:00 was more than an hour after the last line
    assert times == ["2026-11-12T09:00:00", "2026-11-12T10:01:00", "2026-11-12T17:00:00", "2026-11-12T17:00:00"]
    assert "signed in again" in mailbox_lines(root)[-1]


def lapsed_mailer(tmp_path: Path, account: MicrosoftAccount) -> AutoMailer:
    """The mailer of send_via "auto" with an account in the cache whose sign-in has lapsed: it chooses the mailbox, which fails."""
    return AutoMailer(MicrosoftMailer(account, None, "", clock=lambda: NOW, session=FakeSession()),
                      FileMailer(tmp_path / "cases" / "outbox", sender="aid@example.com"))


@pytest.mark.parametrize("digest", ["daily", "per_case"])
def test_a_digest_the_lapsed_sign_in_cannot_send_goes_to_the_outbox_once_with_one_line_in_the_log(tmp_path, madeup_out, digest):
    now = [datetime(2026, 11, 12, 17, 0)]                    # past the digest time
    lapsed = signed_in_account(tmp_path, FakeApp(accounts=[USERNAME], silent=None))      # in the cache, but no token to be had
    d = mail_deps(tmp_path, madeup_out, clock=lambda: now[0])
    d = d.model_copy(update={"mailer": lapsed_mailer(tmp_path, lapsed), "settings": d.settings.model_copy(update={"digest": digest})})
    root = tmp_path / "cases"
    for minute in range(3):
        now[0] = datetime(2026, 11, 12, 17, minute)
        run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1, account=lapsed, session=FakeSession())
    [sent] = (root / "outbox").glob("*.eml")
    mail = message_from_bytes(sent.read_bytes(), policy=policy.default)
    assert mail["Subject"] == "[Inkoopassistent] Dagoverzicht 12-11-2026" and LAPSED in mail.get_content()
    assert json.loads((root / "digest_state.json").read_text(encoding="utf-8"))["last_sent"] == "2026-11-12T17:00:00"
    assert len((root / "runner.log").read_text(encoding="utf-8").splitlines()) == 1 and len(mailbox_lines(root)) == 1


def test_the_lapse_is_named_no_more_once_the_trigger_is_off(tmp_path, madeup_out):
    now = [datetime(2026, 11, 12, 9, 0)]
    d = mail_deps(tmp_path, madeup_out, clock=lambda: now[0])
    lapsed, root = signed_in_account(tmp_path, FakeApp()), tmp_path / "cases"
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1, account=lapsed, session=FakeSession())
    assert "mailbox_lapsed" in json.loads((root / "digest_state.json").read_text(encoding="utf-8"))
    off = d.model_copy(update={"settings": d.settings.model_copy(update={"mail_folder": None})})       # "To stop reading the mailbox"
    now[0] = datetime(2026, 11, 12, 17, 0)
    run_loop(off, tmp_path / "STOP", poll_seconds=0, max_passes=1, account=lapsed, session=FakeSession())
    assert "mailbox_lapsed" not in json.loads((root / "digest_state.json").read_text(encoding="utf-8"))
    assert LAPSED not in (root / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")


def test_in_the_per_case_mode_a_lapse_makes_one_digest(tmp_path, madeup_out):
    d = mail_deps(tmp_path, madeup_out, clock=lambda: datetime(2026, 11, 12, 9, 0))
    d = d.model_copy(update={"settings": d.settings.model_copy(update={"digest": "per_case"})})
    lapsed, stop, root = signed_in_account(tmp_path, FakeApp()), tmp_path / "STOP", tmp_path / "cases"
    run_loop(d, stop, poll_seconds=0, max_passes=3, account=lapsed, session=FakeSession())
    [digest] = (root / "digests").glob("*.txt")
    assert LAPSED in digest.read_text(encoding="utf-8")
    assert len(list((root / "digests").glob("*.html"))) == 1


class Flapping(FakeApp):
    """An account in the cache whose sign-in has lapsed, on a network that is down while `offline()` says so."""

    def __init__(self, offline):
        super().__init__(accounts=[USERNAME], silent=None)
        self.offline = offline

    def acquire_token_silent_with_error(self, scopes, account, **options):
        if self.offline():
            raise requests.ConnectionError("made-up failure")
        return super().acquire_token_silent_with_error(scopes, account, **options)


def test_a_microsoft_that_cannot_be_reached_keeps_the_lapse_so_that_it_is_logged_and_named_once(tmp_path, madeup_out, monkeypatch):
    """Lapsed, lapsed, unreachable, lapsed, unreachable, lapsed, a minute apart in one run: an unreachable Microsoft says nothing
    about the sign-in, so the lapse keeps its time. One line about the lapse, one about the network, none that says "signed in
    again", and one digest in the per-case mode."""
    now = [datetime(2026, 11, 12, 9, 0)]
    monkeypatch.setattr(aid.runner.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + timedelta(minutes=1)))
    account = signed_in_account(tmp_path, Flapping(lambda: now[0].minute in (2, 4)))
    d = mail_deps(tmp_path, madeup_out, clock=lambda: now[0])
    d = d.model_copy(update={"mailer": lapsed_mailer(tmp_path, account), "settings": d.settings.model_copy(update={"digest": "per_case"})})
    root = tmp_path / "cases"
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=6, account=account, session=FakeSession()) == 6
    lines = [line.split(" ", 1)[1] for line in mailbox_lines(root)]
    assert len(lines) == 2 and "sign-in has lapsed" in lines[0] and lines[1] == "mailbox: MicrosoftUnreachable: ConnectionError"
    assert len(list((root / "outbox").glob("*.eml"))) == 1
    assert json.loads((root / "digest_state.json").read_text(encoding="utf-8"))["mailbox_lapsed"]["since"] == "2026-11-12T09:00:00"


def test_turning_the_trigger_off_forgets_the_connection_so_that_connecting_again_reads_nothing_of_the_time_between(tmp_path, madeup_out):
    connected(tmp_path, known=("OLD",), connected_at="2026-10-20T08:00:00Z", last_sync="2026-11-01T08:00:00Z")
    d = mail_deps(tmp_path, madeup_out)
    off = d.model_copy(update={"settings": d.settings.model_copy(update={"mail_folder": None})})
    run_loop(off, tmp_path / "STOP", poll_seconds=0, max_passes=1, account=signed_in_account(tmp_path), session=FakeSession())
    root = tmp_path / "cases"
    assert not (root / "mailbox_state.json").exists() and not (root / "mailbox_known.json").exists()
    between = message("BETWEEN", "2026-11-05T09:00:00Z")                     # arrived while the trigger was off
    session = FakeSession({("GET", baseline()): page([message("OLD", "2026-10-01T07:00:00Z"), between], delta_link=DELTA_2)})
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1, account=signed_in_account(tmp_path), session=session)
    assert (state(tmp_path)["connected_at"], state(tmp_path)["delta_link"]) == (CONNECTED, DELTA_2)
    assert not list((tmp_path / "inbox").rglob("*.eml")) and known(tmp_path) == {mark("OLD"), mark("BETWEEN")}


def test_another_error_of_the_mailbox_is_logged_and_the_folder_goes_on(tmp_path, madeup_out):
    d = mail_deps(tmp_path, madeup_out)
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    session = FakeSession({("GET", baseline()): FakeResponse(404, json_data={"error": {"code": "ErrorItemNotFound"}})})
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2, account=signed_in_account(tmp_path), session=session)
    root = tmp_path / "cases"
    assert [line.split(" ", 1)[1] for line in mailbox_lines(root)] == ["mailbox: mail folder not found"]       # once in a run
    assert list(root.glob("250101_*")) and ACCESS_TOKEN not in (root / "runner.log").read_text(encoding="utf-8")   # a token was issued


# --- the settings file, read again when it changes ---

def settings_file(tmp_path: Path, **values) -> Path:
    data = json.loads(SETTINGS.model_dump_json()) | {"cases_root": "cases", "po_store_root": "po_store", "watched_folder": "inbox",
                                                       "reader": "parser"} | values
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def rewrite(path: Path, **values) -> None:
    """Change values in the settings file, its modification time a second later, as a save on the settings tab does."""
    before = path.stat().st_mtime_ns
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")) | values, indent=2), encoding="utf-8")
    os.utime(path, ns=(before + 1_000_000_000, before + 1_000_000_000))


def file_deps(tmp_path, madeup_out, **values):
    path = settings_file(tmp_path, **values)
    return path, deps(tmp_path, madeup_out).model_copy(update={"settings": load_settings(path)})


def test_a_changed_settings_file_is_taken_at_the_start_of_the_next_pass_through_rebuild(tmp_path, madeup_out, monkeypatch):
    path, d = file_deps(tmp_path, madeup_out)
    monkeypatch.setattr(aid.runner.time, "sleep", lambda seconds: rewrite(path, watched_folder="inbox2", send_via="file"))
    rebuilt = []

    def rebuild(settings):
        rebuilt.append(settings)
        return d.model_copy(update={"settings": settings})

    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2, settings_file=path, rebuild=rebuild) == 2
    assert [(s.watched_folder, s.send_via) for s in rebuilt] == [(tmp_path / "inbox2", "file")]
    assert (tmp_path / "inbox2").is_dir() and not (tmp_path / "cases" / "runner.log").exists()


def test_a_changed_settings_file_that_is_not_valid_is_logged_once_and_the_settings_in_use_are_kept(tmp_path, madeup_out, monkeypatch):
    path, d = file_deps(tmp_path, madeup_out)
    changes = [lambda: rewrite(path, price_pct="not-an-amount-xyz", watched_folder="inbox2")]
    monkeypatch.setattr(aid.runner.time, "sleep", lambda seconds: changes and changes.pop()())
    (tmp_path / "inbox").mkdir()
    shutil.copy(madeup_out / "MU01_conf.pdf", tmp_path / "inbox")
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=3, settings_file=path) == 3
    lines = (tmp_path / "cases" / "runner.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and " settings: " in lines[0] and "price_pct" in lines[0] and "not-an-amount-xyz" not in lines[0]
    assert not (tmp_path / "inbox2").exists() and list((tmp_path / "cases").glob("250101_*"))


def test_an_error_of_another_folder_is_logged_although_the_folder_before_failed(tmp_path, madeup_out, monkeypatch):
    path, d = file_deps(tmp_path, madeup_out, ms_tenant_id=TENANT_ID, ms_client_id=CLIENT_ID, mail_folder=FOLDER)
    other = "AAMkADMadeUpFolder0002="
    monkeypatch.setattr(aid.runner.time, "sleep", lambda seconds: rewrite(path, mail_folder=other))
    session = FakeSession({("GET", baseline()): FakeResponse(404), ("GET", baseline(folder=other)): FakeResponse(403)})
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=3, settings_file=path, account=signed_in_account(tmp_path), session=session)
    assert [line.split(" ", 1)[1] for line in mailbox_lines(tmp_path / "cases")] == ["mailbox: mail folder not found", "mailbox: HTTP 403"]


def test_the_command_rebuilds_its_deps_when_its_settings_file_changes(tmp_path, monkeypatch):
    path = settings_file(tmp_path)
    built, build = [], aid.runner.build_deps
    monkeypatch.setattr(aid.runner, "build_deps", lambda settings, environ: (built.append(settings), build(settings, environ))[1])
    monkeypatch.setattr(aid.runner.time, "sleep", lambda seconds: rewrite(path, watched_folder="inbox2"))
    assert main(["--settings", str(path), "--max-passes", "2", "--poll-seconds", "0"]) == 0
    assert [settings.watched_folder for settings in built] == [tmp_path / "inbox", tmp_path / "inbox2"]
    assert (tmp_path / "inbox").is_dir() and (tmp_path / "inbox2").is_dir()


@pytest.mark.parametrize("send_via", ["auto", "microsoft", "smtp", "file"])
def test_a_secrets_store_this_process_cannot_use_is_one_line_of_the_mailbox_whatever_send_via(tmp_path, send_via):
    """A store made with a kind of store this process cannot use (a runner started outside the session whose keyring the page
    used) stops neither the command nor the folder: the mailbox stage names it once in a run, with the store's own message."""
    secrets = aid.runner.default_secrets_dir()
    secrets.mkdir(parents=True)
    (secrets / "backend").write_text("made-up-kind\n", encoding="utf-8")
    path = settings_file(tmp_path, ms_tenant_id=TENANT_ID, ms_client_id=CLIENT_ID, mail_folder=FOLDER, send_via=send_via, digest="per_case")
    assert main(["--settings", str(path), "--max-passes", "2", "--poll-seconds", "0"]) == 0
    [line] = mailbox_lines(tmp_path / "cases")
    assert "mailbox: StoreRefused: " in line and "'made-up-kind'" in line


def test_the_runner_makes_its_own_account_in_the_folder_the_review_page_uses(tmp_path, madeup_out, monkeypatch):
    """Without an account given, the runner makes one of the settings' tenant and client, with the store in default_secrets_dir(),
    the folder of the review page's account too, and makes a new one when the settings name another client."""
    import msal

    from aid.review.page import _account
    made = []

    class Recorder(FakeApp):
        def __init__(self, client_id, authority=None, token_cache=None, **options):
            super().__init__(accounts=[USERNAME])
            made.append((client_id, authority, token_cache))

    monkeypatch.setattr(msal, "PublicClientApplication", Recorder)
    other_client = "00000000-0000-0000-0000-000000000003"
    path, d = file_deps(tmp_path, madeup_out, ms_tenant_id=TENANT_ID, ms_client_id=CLIENT_ID, mail_folder=FOLDER)
    monkeypatch.setattr(aid.runner.time, "sleep", lambda seconds: rewrite(path, ms_client_id=other_client))
    session = FakeSession({("GET", baseline()): page([], delta_link=DELTA_1), ("GET", DELTA_1): page([], delta_link=DELTA_2)})
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2, settings_file=path, session=session)
    store = aid.runner.default_secrets_dir()
    assert [(client, authority) for client, authority, _ in made] == [(CLIENT_ID, f"https://login.microsoftonline.com/{TENANT_ID}"),
                                                                       (other_client, f"https://login.microsoftonline.com/{TENANT_ID}")]
    assert _account(TENANT_ID, CLIENT_ID).store.directory == store == SecretStore(store).directory
    assert build_deps(load_settings(path), {}).mailer.microsoft.account.store.directory == store


def test_a_digest_the_guard_stops_with_a_lapsed_sign_in_leaves_no_outbox_file_and_asks_microsoft_nothing(tmp_path, madeup_out):
    """The test mode with a lapsed sign-in. The guard comes before the token, so a digest to an address that is not one's own is
    stopped before the work mailbox asks Microsoft anything, and it never reaches the outbox that a lapsed sign-in writes to: its text
    goes to cases/digests/. The lapsed account is still on the PC and counts as one's own address, so a digest to that address goes
    the way of a lapsed sign-in, into cases/outbox/, as before."""
    from aid.messaging.microsoft_mailer import mailer_of
    from aid.tests.fakes import FakeSession
    now = [datetime(2026, 11, 12, 17, 0)]                    # past the digest time
    for name, buyer, own in (("stopped", "inkoop@example.org", ["owner@example.org"]), ("own", USERNAME, [])):
        folder = tmp_path / name
        folder.mkdir()
        sending = FakeApp(accounts=[USERNAME], silent=None)                      # in the cache, but no token to be had
        lapsed, reading = signed_in_account(folder, sending), signed_in_account(folder, FakeApp(accounts=[USERNAME], silent=None))
        d = mail_deps(folder, madeup_out, clock=lambda: now[0])
        settings = d.settings.model_copy(update={"test_mode": True, "own_addresses": own, "buyer_email": buyer})
        graph_calls = FakeSession()
        d = d.model_copy(update={"settings": settings, "mailer": mailer_of(settings, {}, lambda: lapsed, session=graph_calls, clock=lambda: now[0])})
        run_loop(d, folder / "STOP", poll_seconds=0, max_passes=1, account=reading, session=FakeSession())
        root = folder / "cases"
        assert graph_calls.calls == [], name
        if name == "stopped":
            assert not (root / "outbox").exists() and (root / "digests" / "2026-11-12.txt").exists()
            assert not [call for call in sending.calls if call[0] == "acquire_token_silent_with_error"]
        else:
            [sent] = (root / "outbox").glob("*.eml")
            mail = message_from_bytes(sent.read_bytes(), policy=policy.default)
            assert mail["Subject"] == "[Inkoopassistent] Dagoverzicht 12-11-2026" and mail["To"] == USERNAME
            assert not (root / "digests" / "2026-11-12.txt").exists()


# --- the folder for orders: a second folder of the mailbox, read for purchase orders only ---

ORDERS = "AAMkADMadeUpSentItems0001="                 # a made-up id of the folder Sent Items, as Graph gives one
ORDERS_1, ORDERS_2, ORDERS_3 = (f"{GRAPH}/me/mailFolders/{ORDERS}/messages/delta?$deltatoken=made-up-sent-{n}" for n in (1, 2, 3))
MARKER = "MadeUpMarker0001"                           # a made-up word inside a document that must never be said anywhere
SUBJECT_MARKER = "MadeUpMarker0002"                   # a made-up word in the subject of a message that must never be kept
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def orders_baseline(owner: str = "me") -> str:
    """The delta query that takes the baseline of the folder for orders, as Graph reads it."""
    return (f"{GRAPH}/{owner}/mailFolders/{ORDERS}/messages/delta?changeType=created"
            "&$select=id,receivedDateTime,sentDateTime,hasAttachments")


def sent(message_id: str, received: str | None = "2026-11-12T09:00:30Z", attached: bool | None = True, **more) -> dict:
    """An entry of a delta query of the folder for orders: its id, its receive time and hasAttachments, each left out for None."""
    entry = {"id": message_id} | ({"receivedDateTime": received} if received is not None else {})
    return entry | ({"hasAttachments": attached} if attached is not None else {}) | more


def code(data: bytes) -> str:
    """The code of an order file: the first 8 hex digits of the sha256 of the PDF."""
    return hashlib.sha256(data).hexdigest()[:8]


def used_mark(message_id: str) -> str:
    """How the used marks know a message: the first 12 hex digits of the sha256 of its Message-ID."""
    return hashlib.sha256(message_id.encode()).hexdigest()[:12]


def order_name(message_id: str, received: str, data: bytes) -> str:
    """The name of the order file of a PDF of a message of the folder for orders."""
    return f"{received[:19].replace('-', '').replace(':', '')}_{mark(message_id)}_{code(data)}.order.pdf"


def order_mail(number: int, *attachments, subject: str = "Inkooporder 250101", message_id: str | None = "", **headers) -> bytes:
    """A made-up e-mail of the buyer as Graph gives it ($value), with a text and these attachments: a pair (name, bytes) of a PDF, a
    triple (name, bytes, content type), or an EmailMessage, attached as an e-mail is forwarded as an attachment. Its Message-ID is
    <made-up-NNNN@example.org> unless given (None: none)."""
    mail = EmailMessage()
    mail["From"], mail["To"], mail["Subject"] = USERNAME, "s01@example.org", subject
    if message_id is not None:
        mail["Message-ID"] = message_id or f"<made-up-{number:04d}@example.org>"
    for name, value in headers.items():
        mail[name.replace("_", "-")] = value
    mail.set_content("Bijgaand.")
    for attachment in attachments:
        if isinstance(attachment, EmailMessage):
            mail.add_attachment(attachment)
            continue
        name, data, *kind = attachment
        maintype, _, subtype = (kind[0] if kind else "application/pdf").partition("/")
        mail.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return bytes(mail)


def general_terms() -> bytes:
    """A made-up PDF of general terms, with no word of a purchase order in its title."""
    from aid.madeup.generate import Row, Text, pdf_bytes
    return pdf_bytes([[Row([Text(0, "Algemene voorwaarden")]), Row([Text(0, "Artikel 1. Toepasselijkheid")])]], "made-up terms")


def orders_settings(tmp_path: Path, **values) -> Settings:
    """mail_settings with the folder for orders ORDERS set as well."""
    return mail_settings(tmp_path, **({"order_folder": ORDERS, "order_folder_name": "Verzonden items"} | values))


def orders_connected(tmp_path: Path, delta_link: str | None = ORDERS_1, known: tuple[str, ...] = (), **values) -> None:
    """The state file of the folder for orders as an earlier sync left it, and the marks of the messages it knows (by their ids)."""
    state = {"mailbox": None, "folder": ORDERS, "connected_at": CONNECTED, "delta_link": delta_link, "last_sync": CONNECTED, "saved": 0} | values
    (tmp_path / "cases").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cases" / "mailbox_orders_state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "cases" / "mailbox_orders_known.json").write_text(json.dumps([mark(m) for m in known]), encoding="utf-8")


def orders_state(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "cases" / "mailbox_orders_state.json").read_text(encoding="utf-8"))


def orders_known(tmp_path: Path) -> set[str]:
    path = tmp_path / "cases" / "mailbox_orders_known.json"
    return set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()


def used(tmp_path: Path) -> dict | None:
    """The used marks, or None without their file."""
    path = tmp_path / "cases" / "mailbox_used.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def orders_sync(tmp_path: Path, session: FakeSession, now: datetime = NOW, account: MicrosoftAccount | None = None, sleep=None, **values):
    options = {"sleep": sleep} if sleep is not None else {}
    return microsoft_mail.sync_orders(account or signed_in_account(tmp_path), orders_settings(tmp_path, **values), now, session=session,
                                      **options)


def disk(tmp_path: Path) -> dict[str, bytes]:
    """Every file under tmp_path with its bytes, but those of the store of the sign-in."""
    return {path.relative_to(tmp_path).as_posix(): path.read_bytes() for path in sorted(tmp_path.rglob("*"))
            if path.is_file() and path.relative_to(tmp_path).parts[0] not in ("secrets", "default-secrets")}


def holding(tmp_path: Path, *words: str) -> list[str]:
    """The files under tmp_path that hold one of the words."""
    return [name for name, data in disk(tmp_path).items() if any(word.encode() in data for word in words)]


def test_without_a_folder_for_orders_the_reading_is_as_before(tmp_path):
    """With order_folder empty, sync_orders asks nothing and writes nothing, and the main folder asks exactly what it asked before
    and writes neither the files of the folder for orders nor the used marks."""
    session = FakeSession()
    assert microsoft_mail.sync_orders(signed_in_account(tmp_path), mail_settings(tmp_path), NOW, session=session) == \
        microsoft_mail.OrderSync([], 0, None, 0, None)
    assert session.calls == [] and not (tmp_path / "cases").exists()
    connected(tmp_path)
    session = FakeSession({("GET", DELTA_1): page([message("M1", "2026-11-12T09:01:00Z")], delta_link=DELTA_2),
                           ("GET", value_url("M1")): FakeResponse(content=order_mail(1, ("Inkooporder 250101.pdf", b"%PDF-1.4 made-up")))})
    assert len(sync(tmp_path, session).saved) == 1
    assert [unquote(call["url"]) for call in session.calls] == [DELTA_1, value_url("M1")]
    assert sorted(path.name for path in (tmp_path / "cases").iterdir()) == ["mailbox_known.json", "mailbox_state.json"]


def test_the_first_sync_of_the_folder_for_orders_takes_a_baseline_and_takes_nothing_that_was_there(tmp_path):
    """The baseline asks for the folder of order_folder, with the fields the folder's steps need. Of what it lists, a message with an
    attachment (or one Graph says nothing of) becomes known by its mark; one without an attachment gets no mark; no e-mail is asked.
    The connection of the main folder is left alone."""
    connected(tmp_path)
    main = {name: (tmp_path / "cases" / name).read_bytes() for name in ("mailbox_state.json", "mailbox_known.json")}
    session = FakeSession({("GET", orders_baseline()): page([sent("OLD1", "2026-11-01T07:00:00Z", True), sent("OLD2", "2026-11-02T07:00:00Z", False),
                                                             sent("OLD3", "2026-11-03T07:00:00Z", None)], delta_link=ORDERS_1)})
    assert orders_sync(tmp_path, session) == microsoft_mail.OrderSync([], 3, None, 0, None)
    assert [unquote(call["url"]) for call in session.calls] == [orders_baseline()] and f"/mailFolders/{ORDERS}/" in unquote(session.calls[0]["url"])
    assert list((tmp_path / "inbox").iterdir()) == [] and orders_known(tmp_path) == {mark("OLD1"), mark("OLD3")}
    assert orders_state(tmp_path) == {"mailbox": None, "folder": ORDERS, "connected_at": CONNECTED, "delta_link": ORDERS_1,
                                      "last_sync": CONNECTED, "saved": 0}
    assert {name: (tmp_path / "cases" / name).read_bytes() for name in main} == main and used(tmp_path) is None


def test_a_new_order_e_mail_gives_the_watched_folder_its_purchase_order_and_nothing_else(tmp_path, madeup_out):
    """Of an e-mail with general terms, a spreadsheet and the purchase order, only the purchase order is written: one bare file named
    <time>_<mark>_<code>.order.pdf with the bytes of the PDF. The e-mail is never written, no work copy is made, and the message's
    Message-ID is recorded by its mark as used by the folder for orders. Two attachments with the same bytes give one file."""
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    content = order_mail(1, ("Algemene voorwaarden.pdf", general_terms()), ("Bestellijst.xlsx", b"PK made-up workbook", XLSX),
                         ("Inkooporder 250101.pdf", po))
    orders_connected(tmp_path)
    session = FakeSession({("GET", ORDERS_1): page([sent("S1")], delta_link=ORDERS_2), ("GET", value_url("S1")): FakeResponse(content=content)})
    (tmp_path / "inbox").mkdir()
    before = disk(tmp_path)
    name = order_name("S1", "2026-11-12T09:00:30Z", po)
    assert orders_sync(tmp_path, session) == microsoft_mail.OrderSync([tmp_path / "inbox" / name], 0, None, 0, None)
    after = disk(tmp_path)
    assert [path for path in after if path not in before and not path.startswith("cases/mailbox_")] == [f"inbox/{name}"]
    assert after[f"inbox/{name}"] == po and not list(tmp_path.rglob("*.eml")) and not (tmp_path / "cases" / "tmp").exists()
    assert used(tmp_path) == {used_mark("<made-up-0001@example.org>"): "orders"} and orders_state(tmp_path)["saved"] == 1
    assert mark("S1") in orders_known(tmp_path) and orders_state(tmp_path)["delta_link"] == ORDERS_2
    twice = FakeSession({("GET", ORDERS_2): page([sent("S2", "2026-11-12T09:05:00Z")], delta_link=ORDERS_3),
                         ("GET", value_url("S2")): FakeResponse(content=order_mail(2, ("a.pdf", po), ("b.pdf", po)))})
    done = orders_sync(tmp_path, twice, now=NOW + timedelta(minutes=10))
    assert [path.name for path in done.saved] == [order_name("S2", "2026-11-12T09:05:00Z", po)] and orders_state(tmp_path)["saved"] == 2


def without_order(case: str, madeup_out: Path) -> tuple[bytes | None, dict]:
    """The e-mail of each case of an e-mail without a purchase order, and the settings it is read with."""
    from aid.tests.helpers import ANDERS_LAYOUT, damaged_encrypt_pdf, damaged_info_pdf, empty_page_pdf
    po, layout = (madeup_out / "MU01_po_v1.pdf").read_bytes(), {"po_layout": ANDERS_LAYOUT}
    forwarded = EmailMessage()
    forwarded["Subject"] = "Inkooporder 250101"
    forwarded.set_content("Bijgaand.")
    forwarded.add_attachment(po, maintype="application", subtype="pdf", filename="Inkooporder 250101.pdf")
    mails = {"confirmation": (("MU01_conf.pdf", (madeup_out / "MU01_conf.pdf").read_bytes()),), "xlsx": (("lijst.xlsx", b"PK made-up", XLSX),),
             "no attachment": None, "a body only": (), "an attached e-mail": (forwarded,), "a layout it does not fit": (("po.pdf", po),),
             "an empty page": (("leeg.pdf", empty_page_pdf()),), "damaged info": (("x.pdf", damaged_info_pdf(MARKER)),),
             "damaged info, layout": (("x.pdf", damaged_info_pdf(MARKER)),), "damaged encrypt": (("x.pdf", damaged_encrypt_pdf(MARKER)),),
             "damaged encrypt, layout": (("x.pdf", damaged_encrypt_pdf(MARKER)),)}
    attachments = mails[case]
    content = None if attachments is None else order_mail(4, *attachments, subject=f"Bijlagen {SUBJECT_MARKER}")
    return content, (layout if "layout" in case else {})


@pytest.mark.parametrize("case", ["confirmation", "xlsx", "no attachment", "a body only", "an attached e-mail", "a layout it does not fit",
                                  "an empty page", "damaged info", "damaged info, layout", "damaged encrypt", "damaged encrypt, layout"])
def test_a_sent_e_mail_without_a_purchase_order_leaves_nothing_on_disk(tmp_path, madeup_out, case):
    """An e-mail of the folder for orders without a PDF that is a purchase order leaves no file and no used mark, and nothing of it is
    written anywhere: not its subject, not a word of a damaged PDF. A message with an attachment becomes known by its mark; one
    without an attachment is passed over by its listing alone, its e-mail never asked and the marks not written."""
    content, values = without_order(case, madeup_out)
    orders_connected(tmp_path)
    marks = tmp_path / "cases" / "mailbox_orders_known.json"
    os.utime(marks, ns=(1_000_000_000, 1_000_000_000))
    entry = sent("S4", attached=content is not None)
    answers = {("GET", ORDERS_1): page([entry], delta_link=ORDERS_2)} | ({("GET", value_url("S4")): FakeResponse(content=content)} if content else {})
    session = FakeSession(answers)
    (tmp_path / "inbox").mkdir()
    before = disk(tmp_path)
    done = orders_sync(tmp_path, session, **values)
    assert (done.saved, done.error, done.unread, done.unread_kind) == ([], None, 0, None)
    after = disk(tmp_path)
    changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    if content is None:
        assert changed == ["cases/mailbox_orders_state.json"] and marks.stat().st_mtime_ns == 1_000_000_000
        assert [unquote(call["url"]) for call in session.calls] == [ORDERS_1]
    else:
        assert changed == ["cases/mailbox_orders_known.json", "cases/mailbox_orders_state.json"] and orders_known(tmp_path) == {mark("S4")}
    assert holding(tmp_path, MARKER, SUBJECT_MARKER) == [] and used(tmp_path) is None


@pytest.mark.parametrize("own", ["question", "question, Exchange's Message-ID", "digest", "reapproval"])
def test_the_aids_own_messages_in_the_folder_for_orders_are_never_taken_whatever_they_carry(tmp_path, madeup_out, monkeypatch, own):
    """The aid's messages come back into Sent Items. Each is passed over by its headers before any attachment is looked at, also
    with a purchase order attached on purpose: the headers X-Aid-Template and X-Aid-Case, the digest's subject, or the
    Auto-Submitted header with the aid's own Message-ID at a domain the aid sends from."""
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    aid_headers = {"X_Aid_Template": "quantity_unit", "X_Aid_Case": "250101_20261112T080000"}
    mails = {"question": dict(subject="Vraag over orderbevestiging PO 250101", message_id=f"<{'ab' * 16}@example.com>",
                              Auto_Submitted="auto-generated", **aid_headers),
             "question, Exchange's Message-ID": dict(subject="Vraag over orderbevestiging PO 250101",
                                                     message_id="<AM0PR01MB0001@AM0PR01MB0001.prod.example.com>", **aid_headers),
             "digest": dict(subject="[Inkoopassistent] Dagoverzicht 12-11-2026", message_id="<made-up-0005@example.org>"),
             "reapproval": dict(subject="Inkooporder PO 250108 opnieuw ter fiattering", message_id=f"<{'cd' * 16}@example.com>",
                                Auto_Submitted="auto-generated", X_Aid_Template="reapproval", X_Aid_Case="250108_20261112T080000")}
    looked = []
    monkeypatch.setattr(microsoft_mail, "is_po_pdf", lambda data, settings: looked.append(1) or True)
    orders_connected(tmp_path)
    session = FakeSession({("GET", ORDERS_1): page([sent("OWN")], delta_link=ORDERS_2),
                           ("GET", value_url("OWN")): FakeResponse(content=order_mail(5, ("Inkooporder 250101.pdf", po), **mails[own]))})
    (tmp_path / "inbox").mkdir()
    done = orders_sync(tmp_path, session)
    assert (done.saved, done.skipped, done.unread) == ([], 1, 0) and looked == [] and list((tmp_path / "inbox").iterdir()) == []
    assert used(tmp_path) is None and mark("OWN") in orders_known(tmp_path)


@pytest.mark.parametrize("headers", [{"In_Reply_To": "<made-up-0900@example.org>"}, {"References": "<made-up-0900@example.org>"},
                                     {"subject": "RE: Inkooporder 250101"}, {"subject": "FW: Inkooporder 250101"},
                                     {"subject": "Doorst: Inkooporder 250101"}, {"subject": "AW: Bestellung 250101"},
                                     {"subject": "[EXTERN] FW: Inkooporder 250101"}, {"subject": "EXTERN: Fwd: Inkooporder 250101"},
                                     {}], ids=["In-Reply-To", "References", "RE", "FW", "Doorst", "AW", "EXTERN tag", "EXTERN word", "control"])
def test_a_reply_or_a_forward_is_never_taken_from_the_folder_for_orders(tmp_path, madeup_out, headers):
    """A reply or a forward, by its In-Reply-To or References header or by the prefix of its subject, is passed over whole, so that a
    supplier's document that the buyer answers or sends on is never filed as an order. The control, a new e-mail, is taken."""
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    orders_connected(tmp_path)
    session = FakeSession({("GET", ORDERS_1): page([sent("S6")], delta_link=ORDERS_2),
                           ("GET", value_url("S6")): FakeResponse(content=order_mail(6, ("Inkooporder 250101.pdf", po), **headers))})
    done = orders_sync(tmp_path, session)
    if headers:
        assert done.saved == [] and list((tmp_path / "inbox").iterdir()) == [] and used(tmp_path) is None
    else:
        assert [path.name for path in done.saved] == [order_name("S6", "2026-11-12T09:00:30Z", po)]


@pytest.mark.parametrize("prefix", ["", "FW: "], ids=["a new e-mail", "a forward"])
def test_a_sent_e_mail_whose_subject_begins_with_many_tags_between_stars_is_read_in_a_moment(tmp_path, madeup_out, prefix):
    """A subject that begins with nine tags between stars ("*** SPOED ***") is told apart from a reply or a forward in a moment, so
    that such an e-mail never holds up the sync, the runner's pass or the main folder: the new e-mail gives its purchase order, and
    the forward is passed over. Read with the stars between two tags split in every way, one such subject took ten seconds and more,
    and every further tag several times as long (test_messaging.py reads forty)."""
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    orders_connected(tmp_path)
    content = order_mail(7, ("Inkooporder 250101.pdf", po), subject=prefix + "*** SPOED *** " * 9 + "Inkooporder 250101")
    session = FakeSession({("GET", ORDERS_1): page([sent("S7")], delta_link=ORDERS_2), ("GET", value_url("S7")): FakeResponse(content=content)})
    started = time.monotonic()
    done = orders_sync(tmp_path, session)
    assert time.monotonic() - started < 2
    assert [path.name for path in done.saved] == ([] if prefix else [order_name("S7", "2026-11-12T09:00:30Z", po)])
    assert (done.skipped, done.error, done.unread) == ((1 if prefix else 0), None, 0) and mark("S7") in orders_known(tmp_path)


def test_a_message_without_a_message_id_is_not_taken(tmp_path, madeup_out):
    """Without a Message-ID the message could not be told apart from its copy in the folder the aid reads: it is passed over."""
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    orders_connected(tmp_path)
    session = FakeSession({("GET", ORDERS_1): page([sent("S7")], delta_link=ORDERS_2),
                           ("GET", value_url("S7")): FakeResponse(content=order_mail(7, ("Inkooporder 250101.pdf", po), message_id=None))})
    done = orders_sync(tmp_path, session)
    assert done.saved == [] and done.skipped == 1 and list((tmp_path / "inbox").iterdir()) == [] and used(tmp_path) is None
    assert mark("S7") in orders_known(tmp_path)


@pytest.mark.parametrize("first", ["orders first", "main first"])
def test_one_message_in_both_folders_is_used_once_whichever_folder_reads_it_first(tmp_path, madeup_out, first):
    """The buyer in copy of an order e-mail: its copy in the folder the aid reads and its sent copy in the folder for orders share
    their Message-ID. Whichever folder reads it first uses it, and the other passes over its copy whole."""
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    content = order_mail(8, ("Inkooporder 250101.pdf", po))
    connected(tmp_path)
    orders_connected(tmp_path)
    main = FakeSession({("GET", DELTA_1): page([message("COPY", "2026-11-12T09:00:30Z")], delta_link=DELTA_2),
                        ("GET", value_url("COPY")): FakeResponse(content=content)})
    orders = FakeSession({("GET", ORDERS_1): page([sent("SENT")], delta_link=ORDERS_2), ("GET", value_url("SENT")): FakeResponse(content=content)})
    if first == "orders first":
        assert len(orders_sync(tmp_path, orders).saved) == 1
        done = sync(tmp_path, main, order_folder=ORDERS)
        assert (done.saved, done.skipped) == ([], 1) and mark("COPY") in known(tmp_path)
        assert used(tmp_path) == {used_mark("<made-up-0008@example.org>"): "orders"}
    else:
        assert len(sync(tmp_path, main, order_folder=ORDERS).saved) == 1
        assert orders_sync(tmp_path, orders).saved == [] and [unquote(call["url"]) for call in orders.calls] == [ORDERS_1, value_url("SENT")]
        assert used(tmp_path) == {used_mark("<made-up-0008@example.org>"): "main"}
    assert len(list((tmp_path / "inbox").iterdir())) == 1


def test_after_a_main_sync_cut_off_the_next_one_records_its_message_from_its_file_and_the_sent_copy_is_passed_over(tmp_path, madeup_out):
    """A process that ended right after the main folder renamed a message's file leaves its marks, its state and the used marks of
    before. The next sync of the main folder lists the message again, finds its file and records the message from it, without asking
    its e-mail; the folder for orders then passes over the sent copy."""
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    content = order_mail(9, ("Inkooporder 250101.pdf", po))
    connected(tmp_path)
    orders_connected(tmp_path)
    root = tmp_path / "cases"
    kept = {name: (root / name).read_bytes() for name in ("mailbox_state.json", "mailbox_known.json")}
    main = FakeSession({("GET", DELTA_1): page([message("COPY", "2026-11-12T09:00:30Z")], delta_link=DELTA_2),
                        ("GET", value_url("COPY")): FakeResponse(content=content)})
    assert len(sync(tmp_path, main, order_folder=ORDERS).saved) == 1
    for name, data in kept.items():
        (root / name).write_bytes(data)
    (root / "mailbox_used.json").unlink()
    again = sync(tmp_path, main, now=NOW + timedelta(minutes=1), order_folder=ORDERS)
    assert (again.saved, again.skipped) == ([], 1) and [unquote(call["url"]) for call in main.calls].count(value_url("COPY")) == 1
    assert used(tmp_path) == {used_mark("<made-up-0009@example.org>"): "main"}
    orders = FakeSession({("GET", ORDERS_1): page([sent("SENT")], delta_link=ORDERS_2), ("GET", value_url("SENT")): FakeResponse(content=content)})
    assert orders_sync(tmp_path, orders).saved == [] and [path.name for path in (tmp_path / "inbox").iterdir()] == [eml_name("COPY", "2026-11-12T09:00:30Z")]


def test_a_new_baseline_of_the_main_folder_reads_no_file_of_a_message_from_before_the_time_it_compares_with(tmp_path):
    """A new baseline of the main folder while a folder for orders is set (the folder chosen again, or its state lost) lists a message
    from before the time it compares with, whose .eml lies in processed/ from an earlier connection: it becomes known, as without a
    folder for orders, and its file is neither looked for nor read, so that it gets no used mark. A message from after that time whose
    .eml is in place, which a sync cut off before its marks were written left, is still recorded from its file."""
    processed = tmp_path / "inbox" / "processed"
    processed.mkdir(parents=True)
    old, cut_off = ("OLD", "2026-11-01T08:00:00Z"), ("CUT", "2026-11-12T08:30:00Z")
    (processed / eml_name(*old)).write_bytes(supplier_mail(Message_ID="<made-up-0101@example.org>"))
    (processed / eml_name(*cut_off)).write_bytes(supplier_mail(Message_ID="<made-up-0102@example.org>"))
    session = FakeSession({("GET", baseline()): page([message(*old), message(*cut_off)], delta_link=DELTA_1)})
    done = sync(tmp_path, session, order_folder=ORDERS, order_folder_name="Verzonden items")
    assert (done.saved, done.skipped) == ([], 2) and [unquote(call["url"]) for call in session.calls] == [baseline()]
    assert known(tmp_path) == {mark("OLD"), mark("CUT")} and used(tmp_path) == {used_mark("<made-up-0102@example.org>"): "main"}


def test_the_used_marks_are_written_right_after_each_message(tmp_path, madeup_out):
    """The used marks are written right after each message's files are placed, not at the end of the sync: when the second message's
    e-mail is asked, the file holds the first message's mark, with the folder that used it, in both folders."""
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    connected(tmp_path)
    orders_connected(tmp_path)
    seen = []

    def second(content: bytes):
        def answer(request: dict) -> FakeResponse:
            seen.append((used(tmp_path), sorted(path.name for path in (tmp_path / "inbox").iterdir())))
            return FakeResponse(content=content)
        return answer
    main = FakeSession({("GET", DELTA_1): page([message("M1", "2026-11-12T09:00:30Z"), message("M2", "2026-11-12T09:01:30Z")], delta_link=DELTA_2),
                        ("GET", value_url("M1")): FakeResponse(content=order_mail(11, ("a.pdf", po))),
                        ("GET", value_url("M2")): second(order_mail(12, ("a.pdf", po)))})
    sync(tmp_path, main, order_folder=ORDERS)
    assert seen[0][0] == {used_mark("<made-up-0011@example.org>"): "main"}
    orders = FakeSession({("GET", ORDERS_1): page([sent("S1"), sent("S2", "2026-11-12T09:01:30Z")], delta_link=ORDERS_2),
                          ("GET", value_url("S1")): FakeResponse(content=order_mail(13, ("a.pdf", po))),
                          ("GET", value_url("S2")): second(order_mail(14, ("a.pdf", po)))})
    orders_sync(tmp_path, orders)
    assert seen[1][0] == {used_mark("<made-up-0011@example.org>"): "main", used_mark("<made-up-0012@example.org>"): "main",
                          used_mark("<made-up-0013@example.org>"): "orders"}
    assert order_name("S1", "2026-11-12T09:00:30Z", po) in seen[1][1]


def test_two_messages_with_the_same_purchase_order_pdf_both_reach_the_watched_folder_as_today(tmp_path, madeup_out):
    """The order sent twice, as two e-mails with two Message-IDs: both reach the watched folder, under two stems, and the store files
    the second as it files the same PDF given again through the folder the aid reads (check A8 of the try list)."""
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    orders_connected(tmp_path)
    session = FakeSession({("GET", ORDERS_1): page([sent("S1"), sent("S2", "2026-11-12T09:10:00Z")], delta_link=ORDERS_2),
                           ("GET", value_url("S1")): FakeResponse(content=order_mail(21, ("a.pdf", po))),
                           ("GET", value_url("S2")): FakeResponse(content=order_mail(22, ("a.pdf", po)))})
    done = orders_sync(tmp_path, session)
    assert [path.name for path in done.saved] == [order_name("S1", "2026-11-12T09:00:30Z", po), order_name("S2", "2026-11-12T09:10:00Z", po)]


def test_a_message_moved_into_the_folder_for_orders_later_is_taken_whatever_its_receive_time(tmp_path, madeup_out):
    """A delta round compares no time: a message moved into the folder is taken, also when it was received long before, and a message
    Graph gives again is not. An entry with a sent time only is named by it, and a baseline compares it."""
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    orders_connected(tmp_path, known=("BASE",))
    moved, only_sent = sent("M1", "2026-11-01T09:01:00Z"), sent("M2", None, sentDateTime="2026-11-12T09:02:30Z")
    session = FakeSession({("GET", ORDERS_1): page([sent("BASE", "2026-10-01T07:00:00Z"), moved, only_sent], delta_link=ORDERS_2),
                           ("GET", value_url("M1")): FakeResponse(content=order_mail(31, ("a.pdf", po))),
                           ("GET", value_url("M2")): FakeResponse(content=order_mail(32, ("a.pdf", po))),
                           ("GET", ORDERS_2): page([moved], delta_link=ORDERS_3)})
    done = orders_sync(tmp_path, session)
    assert [path.name for path in done.saved] == [order_name("M1", "2026-11-01T09:01:00Z", po), order_name("M2", "2026-11-12T09:02:30Z", po)]
    assert orders_sync(tmp_path, session, now=NOW + timedelta(minutes=1)).saved == []
    expired = FakeSession({("GET", ORDERS_3): FakeResponse(410),
                           ("GET", orders_baseline()): page([sent("OLD", None, sentDateTime="2026-11-12T07:00:00Z"),
                                                             sent("GAP", None, sentDateTime="2026-11-12T08:30:00Z")], delta_link=ORDERS_1),
                           ("GET", value_url("GAP")): FakeResponse(content=order_mail(33, ("a.pdf", po)))})
    done = orders_sync(tmp_path, expired, now=NOW + timedelta(hours=1))                # last_sync 08:01: OLD before it, GAP after it
    assert [path.name for path in done.saved] == [order_name("GAP", "2026-11-12T08:30:00Z", po)] and mark("OLD") in orders_known(tmp_path)


@pytest.mark.parametrize("refusal", ["404", "403", "503 twice", "500 on an e-mail"])
def test_an_error_on_the_folder_for_orders_keeps_its_delta_link_and_loses_no_order(tmp_path, madeup_out, refusal):
    """An error of Graph ends the sync with its error and the delta link of before; an order file written before it stays and is
    known, and the next sync takes nothing twice."""
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    orders_connected(tmp_path)
    listed = page([sent("S1"), sent("S2", "2026-11-12T09:01:30Z")], delta_link=ORDERS_2)
    gone, refused, busy = (FakeResponse(404, json_data={"error": {"code": "ErrorItemNotFound"}}), FakeResponse(403),
                           FakeResponse(503, headers={"Retry-After": "1"}))
    delta = {"404": [gone, listed], "403": [refused, listed], "503 twice": [busy, busy, listed], "500 on an e-mail": [listed]}[refusal]
    second = [FakeResponse(500), FakeResponse(content=order_mail(42, ("a.pdf", po)))] if refusal == "500 on an e-mail" else \
        [FakeResponse(content=order_mail(42, ("a.pdf", po)))]
    session = FakeSession({("GET", ORDERS_1): delta, ("GET", value_url("S1")): FakeResponse(content=order_mail(41, ("a.pdf", po))),
                           ("GET", value_url("S2")): second})
    done = orders_sync(tmp_path, session, sleep=lambda seconds: None)
    error = {"404": "mail folder not found", "403": "HTTP 403", "503 twice": "HTTP 503", "500 on an e-mail": "HTTP 500"}[refusal]
    assert done.error == error and orders_state(tmp_path)["delta_link"] == ORDERS_1
    first = order_name("S1", "2026-11-12T09:00:30Z", po)
    if refusal == "500 on an e-mail":
        assert [path.name for path in done.saved] == [first] and mark("S1") in orders_known(tmp_path) and mark("S2") not in orders_known(tmp_path)
    again = orders_sync(tmp_path, session, now=NOW + timedelta(minutes=1), sleep=lambda seconds: None)
    assert again.error is None and sorted(path.name for path in (tmp_path / "inbox").iterdir()) == sorted(
        [first, order_name("S2", "2026-11-12T09:01:30Z", po)])
    assert [unquote(call["url"]) for call in session.calls].count(value_url("S1")) == 1


@pytest.mark.parametrize("failing", ["the order file", "the used marks"])
def test_a_failure_of_the_aids_own_folders_ends_the_sync_and_the_next_one_places_and_records_the_order_once(tmp_path, madeup_out,
                                                                                                           monkeypatch, failing):
    """An OSError while an order file is placed or the used marks are written ends the sync with the class of the error alone, never
    a path: the message gets no mark and no used entry, and the delta link of before is kept. The next sync lists it again and
    records it, with one order file in the watched folder either way: a file that was not placed is placed then, its unfinished copy
    of before written over, and a file placed before counts as taken before and is not written again."""
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    orders_connected(tmp_path)
    session = FakeSession({("GET", ORDERS_1): page([sent("S1")], delta_link=ORDERS_2),
                           ("GET", value_url("S1")): FakeResponse(content=order_mail(51, ("a.pdf", po)))})
    refused = []
    if failing == "the order file":
        rename = os.replace

        def replace(source, target):
            if str(target).endswith(".order.pdf") and not refused:
                refused.append(target)
                raise PermissionError(13, "made-up: the file is held by another program", str(target))
            return rename(source, target)
        monkeypatch.setattr(os, "replace", replace)
    else:
        write = microsoft_mail._write_json

        def write_json(path, data):
            if Path(path).name == "mailbox_used.json" and not refused:
                refused.append(path)
                raise PermissionError(13, "made-up: the file is held by another program", str(path))
            return write(path, data)
        monkeypatch.setattr(microsoft_mail, "_write_json", write_json)
    done = orders_sync(tmp_path, session)
    assert done.error == "PermissionError" and refused and mark("S1") not in orders_known(tmp_path) and used(tmp_path) is None
    assert orders_state(tmp_path)["delta_link"] == ORDERS_1
    again = orders_sync(tmp_path, session, now=NOW + timedelta(minutes=1))
    name = order_name("S1", "2026-11-12T09:00:30Z", po)
    assert again.error is None and [path.name for path in (tmp_path / "inbox").iterdir()] == [name]
    assert used(tmp_path) == {used_mark("<made-up-0051@example.org>"): "orders"} and mark("S1") in orders_known(tmp_path)
    assert [path.name for path in again.saved] == ([name] if failing == "the order file" else [])


def unreadable_mail(po: bytes) -> bytes:
    """A made-up e-mail whose attachment has the malformed name parameter filename*0*, which Python's e-mail parser cannot read."""
    return (b"From: buyer@example.com\r\nTo: s01@example.org\r\nSubject: Bijlage " + SUBJECT_MARKER.encode() + b"\r\n"
            b"Message-ID: <made-up-0061@example.org>\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"b\"\r\n\r\n"
            b"--b\r\nContent-Type: text/plain\r\n\r\nBijgaand.\r\n--b\r\nContent-Type: application/pdf\r\n"
            b"Content-Disposition: attachment; filename*0*\r\nContent-Transfer-Encoding: base64\r\n\r\n"
            + base64.b64encode(po) + b"\r\n--b--\r\n")


@pytest.mark.parametrize("how", ["patched", "a malformed name"])
def test_an_e_mail_that_cannot_be_read_is_passed_over_and_stops_no_later_message(tmp_path, madeup_out, monkeypatch, how):
    """An e-mail whose reading raises counts as one without a purchase order: it is known by its mark, nothing of it is written or
    recorded, and the next message is read. The sync gives the number of such e-mails and the class of the first failure, never its
    text."""
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    if how == "patched":
        named = microsoft_mail._named_attachments

        def attachments(mail):
            if SUBJECT_MARKER in str(mail["Subject"]):
                raise IndexError("made-up: " + SUBJECT_MARKER)
            return named(mail)
        monkeypatch.setattr(microsoft_mail, "_named_attachments", attachments)
        first = order_mail(60, ("a.pdf", po), subject=f"Bijlage {SUBJECT_MARKER}")
    else:
        first = unreadable_mail(po)
    orders_connected(tmp_path)
    session = FakeSession({("GET", ORDERS_1): page([sent("A"), sent("B", "2026-11-12T09:01:30Z")], delta_link=ORDERS_2),
                           ("GET", value_url("A")): FakeResponse(content=first),
                           ("GET", value_url("B")): FakeResponse(content=order_mail(62, ("a.pdf", po))),
                           ("GET", ORDERS_2): page([], delta_link=ORDERS_3)})
    done = orders_sync(tmp_path, session)
    assert [path.name for path in done.saved] == [order_name("B", "2026-11-12T09:01:30Z", po)] and done.error is None
    assert used(tmp_path) == {used_mark("<made-up-0062@example.org>"): "orders"} and holding(tmp_path, SUBJECT_MARKER) == []
    if how == "patched":
        assert (done.unread, done.unread_kind) == (1, "IndexError") and mark("A") in orders_known(tmp_path)
    calls = len(session.calls)
    assert orders_sync(tmp_path, session, now=NOW + timedelta(minutes=1)) == microsoft_mail.OrderSync([], 0, None, 0, None)
    assert [unquote(call["url"]) for call in session.calls[calls:]] == [ORDERS_2]


def test_a_message_from_before_the_connection_that_graph_lists_again_is_not_taken(tmp_path, madeup_out):
    """A delta round compares no time, so the mark is what keeps a message from before the connection out when Graph lists it again;
    a message without an attachment needs none, since it is passed over by its listing."""
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    session = FakeSession({("GET", orders_baseline()): page([sent("OLD1", "2026-11-01T07:00:00Z"), sent("OLD2", "2026-11-02T07:00:00Z", False)],
                                                            delta_link=ORDERS_1),
                           ("GET", ORDERS_1): page([sent("OLD1", "2026-11-01T07:00:00Z"), sent("OLD2", "2026-11-02T07:00:00Z", False)],
                                                   delta_link=ORDERS_2),
                           ("GET", value_url("OLD1")): FakeResponse(content=order_mail(71, ("a.pdf", po)))})
    orders_sync(tmp_path, session)
    assert orders_sync(tmp_path, session, now=NOW + timedelta(minutes=1)).saved == []
    assert [unquote(call["url"]) for call in session.calls] == [orders_baseline(), ORDERS_1]
    assert list((tmp_path / "inbox").iterdir()) == [] and orders_known(tmp_path) == {mark("OLD1")} and used(tmp_path) is None


# --- the folder for orders in the runner's pass ---

def orders_deps(tmp_path: Path, madeup_out: Path, now: list, without: tuple[str, ...] = ("250101",), **values):
    """mail_deps with the folder for orders ORDERS set (orders_settings, with `values`), the clock at now[0], which the test moves
    on, and the PO store without the POs `without` (the store of deps() holds every made-up PO)."""
    d = mail_deps(tmp_path, madeup_out, clock=lambda: now[0])
    for number in without:
        shutil.rmtree(tmp_path / "store" / number)
    return d.model_copy(update={"settings": orders_settings(tmp_path, **values)})


def intake_of(root: Path) -> list[tuple[str, str, str]]:
    """The lines of <cases_root>/intake.jsonl as (file, outcome, detail), in the order they were written."""
    path = root / "intake.jsonl"
    return [(line["file"], line["outcome"], line["detail"]) for line in map(json.loads, path.read_text(encoding="utf-8").splitlines())] \
        if path.exists() else []


def order_lines(root: Path) -> list[str]:
    """The lines of runner.log of the stage of the folder for orders, without their times."""
    log = root / "runner.log"
    return [line.split(" ", 1)[1] for line in log.read_text(encoding="utf-8").splitlines() if " order folder: " in line] if log.exists() else []


def stamp(moment: datetime) -> str:
    """A time of the runner's clock (local time without a zone) as the state files write it."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_a_used_marks_file_that_cannot_be_written_never_changes_the_main_folders_reading(tmp_path, madeup_out, monkeypatch):
    """While the used marks cannot be written, the main folder reads, saves, writes its own marks and state and ends a pass exactly as
    without a folder for orders: no line of its stage. The failure shows when the folder for orders records a message: its sync ends
    with the class of the error, logged once in a run of the loop, and the order file it placed stays to be filed."""
    now = [datetime(2026, 11, 12, 9, 0)]
    d = orders_deps(tmp_path, madeup_out, now, without=())
    write = microsoft_mail._write_json

    def refusing(path, data):
        if Path(path).name == "mailbox_used.json":
            raise PermissionError(13, "made-up: the file is held by another program", str(path))
        return write(path, data)
    monkeypatch.setattr(microsoft_mail, "_write_json", refusing)
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    session = FakeSession({("GET", baseline()): page([], delta_link=DELTA_1), ("GET", orders_baseline()): page([], delta_link=ORDERS_1),
                           ("GET", DELTA_1): page([message("M1", "2026-11-12T08:30:00Z")], delta_link=DELTA_2),
                           ("GET", value_url("M1")): FakeResponse(content=confirmation_email()),
                           ("GET", ORDERS_1): page([sent("NONE", attached=False)], delta_link=ORDERS_2), ("GET", DELTA_2): page([], delta_link=DELTA_2),
                           ("GET", ORDERS_2): page([sent("S1", "2026-11-12T08:40:00Z")], delta_link=ORDERS_3),
                           ("GET", value_url("S1")): FakeResponse(content=order_mail(81, ("Inkooporder 250101.pdf", po)))})
    account, stop, root = signed_in_account(tmp_path), tmp_path / "STOP", tmp_path / "cases"
    run_loop(d, stop, poll_seconds=0, max_passes=1, account=account, session=session)                  # the baselines
    now[0] = datetime(2026, 11, 12, 9, 1)
    run_loop(d, stop, poll_seconds=0, max_passes=1, account=account, session=session)
    [case] = root.glob("250101_*")
    assert (case / "steps" / "08_result.json").exists() and (tmp_path / "inbox" / "processed" / eml_name("M1", "2026-11-12T08:30:00Z")).exists()
    assert state(tmp_path)["delta_link"] == DELTA_2 and mark("M1") in known(tmp_path) and used(tmp_path) is None
    assert mailbox_lines(root) == [] and order_lines(root) == [] and orders_state(tmp_path)["last_sync"] == stamp(now[0])
    now[0] = datetime(2026, 11, 12, 9, 2)
    run_loop(d, stop, poll_seconds=0, max_passes=2, account=account, session=session)
    name = order_name("S1", "2026-11-12T08:40:00Z", po)
    assert (tmp_path / "inbox" / name).exists() or (tmp_path / "inbox" / "processed" / name).exists()
    assert order_lines(root) == ["order folder: PermissionError"] and mailbox_lines(root) == []
    assert str(tmp_path) not in (root / "runner.log").read_text(encoding="utf-8")


def test_the_folder_for_orders_gives_its_purchase_order_to_the_scan_of_the_same_pass(tmp_path, madeup_out):
    """The order file lies in the watched folder before the scan of the same pass, which files it: Bestanden shows its line with the
    mark of the folder, "Laatst opgenomen" names the order, and the digest names nothing of the folder."""
    from aid.review.exact_settings import state_lines
    from aid.review.intake import rows
    now = [datetime(2026, 11, 12, 9, 0)]
    d = orders_deps(tmp_path, madeup_out, now)
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    session = FakeSession({("GET", baseline()): page([], delta_link=DELTA_1), ("GET", orders_baseline()): page([], delta_link=ORDERS_1),
                           ("GET", DELTA_1): page([], delta_link=DELTA_1),
                           ("GET", ORDERS_1): page([sent("S1", "2026-11-12T15:30:00Z")], delta_link=ORDERS_2),
                           ("GET", value_url("S1")): FakeResponse(content=order_mail(91, ("Inkooporder 250101.pdf", po)))})
    account, stop, root = signed_in_account(tmp_path), tmp_path / "STOP", tmp_path / "cases"
    run_loop(d, stop, poll_seconds=0, max_passes=1, account=account, session=session)
    now[0] = datetime(2026, 11, 12, 17, 0)                                # past the digest time: this pass makes the day's digest
    run_loop(d, stop, poll_seconds=0, max_passes=1, account=account, session=session)
    name = order_name("S1", "2026-11-12T15:30:00Z", po)
    assert (tmp_path / "store" / "250101" / "v1" / "po.json").exists() and (tmp_path / "inbox" / "processed" / name).exists()
    assert intake_of(root)[-1] == (name, "po", "250101 v1")
    assert rows(root, "nl")[0][2] == "Inkooporder 250101 opgenomen (versie 1) (uit de map voor inkooporders)"
    assert any("Laatst opgenomen: inkooporder 250101, versie 1" in line for line in state_lines(d.settings, d.store, now[0], "nl"))
    digest = (root / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")
    assert code(po) not in digest and mark("S1") not in digest and not (root / "runner.log").exists()


def test_a_waiting_confirmation_is_checked_again_in_the_same_pass_when_its_order_comes_from_the_folder_for_orders(tmp_path, madeup_out):
    """A confirmation whose purchase order the store does not hold waits for it; when the folder for orders gives that order, the
    waiting case is checked again in the same pass: a new case runs and the old one is finished as replaced."""
    from aid.case import load_case
    from aid.review.close import replaced
    now = [datetime(2026, 11, 12, 9, 0)]
    d = orders_deps(tmp_path, madeup_out, now)
    shutil.copy(madeup_out / "MU01_conf.pdf", d.settings.watched_folder)
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    session = FakeSession({("GET", baseline()): page([], delta_link=DELTA_1), ("GET", orders_baseline()): page([], delta_link=ORDERS_1),
                           ("GET", DELTA_1): page([], delta_link=DELTA_1),
                           ("GET", ORDERS_1): page([sent("S1", "2026-11-12T08:59:00Z")], delta_link=ORDERS_2),
                           ("GET", value_url("S1")): FakeResponse(content=order_mail(92, ("Inkooporder 250101.pdf", po)))})
    root = tmp_path / "cases"
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2, account=signed_in_account(tmp_path), session=session) == 2
    old, new = sorted(root.glob("250101_*"))
    assert (new / "steps" / "08_result.json").exists() and replaced(load_case(root, old.name))
    assert intake_of(root)[-2:] == [(order_name("S1", "2026-11-12T08:59:00Z", po), "po", "250101 v1"), ("MU01_conf.pdf", "recheck", new.name)]


@pytest.mark.parametrize("third", ["the main folder's copy", "another e-mail", "the main folder alone"])
def test_one_message_in_both_folders_is_filed_once_and_its_print_is_given_once(tmp_path, madeup_out, third):
    """The sent copy of an order e-mail is taken from the folder for orders and filed at one pass; its copy in the folder the aid reads,
    the same Message-ID, comes at the next pass and is passed over: one line on Bestanden, no "skipped duplicate", and the store notes
    no print given again. Another e-mail with the same PDF, another Message-ID, reaches the store as today: a line "skipped duplicate"
    and a print given again, exactly as the same two e-mails through the folder the aid reads alone give."""
    from aid.po_store import GIVEN
    now = [datetime(2026, 11, 12, 9, 0)]
    alone = third == "the main folder alone"
    d = orders_deps(tmp_path, madeup_out, now, **({"order_folder": None, "order_folder_name": None} if alone else {}))
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    first, other = order_mail(100, ("Inkooporder 250101.pdf", po)), order_mail(101, ("Inkooporder 250101.pdf", po))
    later = message("COPY" if third == "the main folder's copy" else "OTHER", "2026-11-12T08:40:00Z")
    answers = {("GET", baseline()): page([], delta_link=DELTA_1),
               ("GET", DELTA_1): page([message("FIRST", "2026-11-12T08:30:00Z")] if alone else [], delta_link=DELTA_2),
               ("GET", DELTA_2): page([later], delta_link=DELTA_3), ("GET", value_url("FIRST")): FakeResponse(content=first),
               ("GET", value_url("COPY")): FakeResponse(content=first), ("GET", value_url("OTHER")): FakeResponse(content=other)}
    if not alone:
        answers |= {("GET", orders_baseline()): page([], delta_link=ORDERS_1),
                    ("GET", ORDERS_1): page([sent("SENT", "2026-11-12T08:30:00Z")], delta_link=ORDERS_2),
                    ("GET", value_url("SENT")): FakeResponse(content=first), ("GET", ORDERS_2): page([], delta_link=ORDERS_2)}
    session, account, root = FakeSession(answers), signed_in_account(tmp_path), tmp_path / "cases"
    for minute in range(3):
        now[0] = datetime(2026, 11, 12, 9, minute)
        run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1, account=account, session=session)
    given = (tmp_path / "store" / "250101" / "v1" / GIVEN).exists()
    if third == "the main folder's copy":
        name = order_name("SENT", "2026-11-12T08:30:00Z", po)
        assert intake_of(root) == [(name, "po", "250101 v1")] and not given
        assert sorted(path.name for path in (tmp_path / "inbox" / "processed").iterdir()) == [name]
    else:
        assert [(outcome, detail) for _, outcome, detail in intake_of(root)] == [("po", "250101 v1"), ("skipped", "duplicate")] and given


def test_a_sent_e_mail_without_a_purchase_order_leaves_nothing_in_the_digest_on_bestanden_or_in_a_log(tmp_path, madeup_out, caplog):
    """Six sent e-mails without a purchase order, read at the pass that makes the day's digest: a confirmation, a spreadsheet, no
    attachment, a PDF without a word of a purchase order while no layout is saved, a damaged PDF and a PDF about which the reader of
    PDFs warns. Nothing of them is on Bestanden, in the digest, in runner.log, in a file or in a log record, nothing is in the watched
    folder, and the decider is never asked."""
    import logging

    from aid.review.intake import rows
    from aid.tests.fakes import FakeDecider
    from aid.tests.helpers import ANDERS_PO, damaged_info_pdf, warning_pdf
    caplog.set_level(logging.WARNING)
    now = [datetime(2026, 11, 12, 9, 0)]
    decider = FakeDecider(None, 0.0)
    d = orders_deps(tmp_path, madeup_out, now).model_copy(update={"decider": decider})
    words = [f"MadeUpSubject{n}" for n in range(1, 7)] + [f"MadeUpName{n}" for n in range(1, 7)] + [MARKER]
    mails = {"C1": order_mail(201, ("Bevestiging MadeUpName1.pdf", (madeup_out / "MU01_conf.pdf").read_bytes()), subject="Orderbevestiging MadeUpSubject1"),
             "C2": order_mail(202, ("Lijst MadeUpName2.xlsx", b"PK made-up", XLSX), subject="Prijslijst MadeUpSubject2"),
             "C4": order_mail(204, ("Bestelling MadeUpName4.pdf", ANDERS_PO.read_bytes()), subject="Bestelling MadeUpSubject4"),
             "C5": order_mail(205, ("Voorwaarden MadeUpName5.pdf", damaged_info_pdf(MARKER)), subject="Voorwaarden MadeUpSubject5"),
             "C6": order_mail(206, ("Bijlage MadeUpName6.pdf", warning_pdf(MARKER)), subject="Bijlage MadeUpSubject6")}
    listed = [sent("C1"), sent("C2"), sent("C3", attached=False, subject="MadeUpSubject3"), sent("C4"), sent("C5"), sent("C6")]
    session = FakeSession({("GET", baseline()): page([], delta_link=DELTA_1), ("GET", orders_baseline()): page([], delta_link=ORDERS_1),
                           ("GET", DELTA_1): page([], delta_link=DELTA_1), ("GET", ORDERS_1): page(listed, delta_link=ORDERS_2),
                           **{("GET", value_url(name)): FakeResponse(content=content) for name, content in mails.items()}})
    account, stop, root = signed_in_account(tmp_path), tmp_path / "STOP", tmp_path / "cases"
    run_loop(d, stop, poll_seconds=0, max_passes=1, account=account, session=session)
    now[0] = datetime(2026, 11, 12, 17, 0)
    run_loop(d, stop, poll_seconds=0, max_passes=1, account=account, session=session)
    assert not (root / "intake.jsonl").exists() and rows(root, "nl") == []
    assert json.loads((root / "digest_state.json").read_text(encoding="utf-8"))["skipped"] == []
    for kind in ("txt", "html"):
        digest = (root / "digests" / f"2026-11-12.{kind}").read_text(encoding="utf-8")
        assert not [word for word in words if word in digest] and "Overgeslagen" not in digest
    assert not (root / "runner.log").exists() and holding(tmp_path, *words) == []
    assert [record.getMessage() for record in caplog.records if MARKER in record.getMessage()] == []
    assert [path for path in (tmp_path / "inbox").rglob("*") if path.is_file()] == [] and decider.decisions == []


@pytest.mark.parametrize("failure", ["401", "404", "403", "unreachable", "an error of the aid"])
def test_an_error_or_a_lapse_of_the_folder_for_orders_does_not_stop_the_main_folder_or_the_pass(tmp_path, madeup_out, monkeypatch, failure):
    """The folder for orders is read after the folder the aid reads, and nothing it meets stops that folder, the scan or the pass. An
    error is logged once in a run of the loop under its own stage, by Graph's text or the class of the error, and the first read after
    it says so; an error of the aid's own code by its class only. A lapsed sign-in of the folder alone keeps the mailbox's lapse noted,
    logged once an hour, and never alternates with "signed in again"."""
    now, passes = [datetime(2026, 11, 12, 9, 0)], []
    monkeypatch.setattr(aid.runner.time, "sleep", lambda seconds: passes.append(seconds))
    d = orders_deps(tmp_path, madeup_out, now, without=())
    connected(tmp_path)
    orders_connected(tmp_path)
    refusal = {"401": FakeResponse(401, json_data={"error": {"code": "InvalidAuthenticationToken"}}),
               "404": FakeResponse(404, json_data={"error": {"code": "ErrorItemNotFound"}}), "403": FakeResponse(403)}.get(failure)
    answers = {("GET", DELTA_1): page([message("M1", "2026-11-12T08:30:00Z")], delta_link=DELTA_2), ("GET", DELTA_2): page([], delta_link=DELTA_2),
               ("GET", value_url("M1")): FakeResponse(content=confirmation_email()),
               ("GET", ORDERS_1): ([refusal] * (3 if failure == "401" else 2) + [page([], delta_link=ORDERS_2)]) if refusal else page([], delta_link=ORDERS_2),
               ("GET", ORDERS_2): page([], delta_link=ORDERS_2)}

    class OrdersUnreachable(FakeSession):
        def get(self, url, headers=None, timeout=None, **kwargs):
            if failure == "unreachable" and len(passes) < 2 and ORDERS in unquote(url):
                raise requests.ConnectionError(f"made-up failure for {url}")
            return super().get(url, headers, timeout, **kwargs)
    if failure == "an error of the aid":
        def broken(*args, **kwargs):
            raise RuntimeError(f"made-up: {SUBJECT_MARKER}")
        monkeypatch.setattr(aid.runner, "sync_orders", broken)
    root = tmp_path / "cases"
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=3, account=signed_in_account(tmp_path), session=OrdersUnreachable(answers)) == 3
    [case] = root.glob("250101_*")
    assert (case / "steps" / "08_result.json").exists() and (tmp_path / "inbox" / "processed" / eml_name("M1", "2026-11-12T08:30:00Z")).exists()
    lines = {"404": ["order folder: mail folder not found"], "403": ["order folder: HTTP 403"],
             "unreachable": ["order folder: MicrosoftUnreachable: ConnectionError"], "an error of the aid": ["order folder: RuntimeError"],
             "401": []}[failure]
    recovered = ["order folder: read after an earlier failure"] if failure in ("404", "403", "unreachable") else []
    assert order_lines(root) == lines + recovered and SUBJECT_MARKER not in (root / "runner.log").read_text(encoding="utf-8")
    if failure == "401":
        [lapse] = mailbox_lines(root)
        assert "sign-in has lapsed" in lapse and "mailbox_lapsed" in json.loads((root / "digest_state.json").read_text(encoding="utf-8"))
    else:
        assert mailbox_lines(root) == []


def test_a_sign_in_lapsed_at_the_first_pass_after_the_save_connects_the_folder_for_orders_all_the_same(tmp_path, madeup_out):
    """At the runner's first pass after the folder for orders is saved, the sign-in has lapsed. The folder of the mailbox records its
    connection at that pass, before it asks for the token, and the folder for orders records its own as well, asking Microsoft nothing.
    So an order that Exact sends through Outlook before the sign-in is repaired comes after that connection, and the first pass that
    reads the folder takes it, as the folder of the mailbox saves the confirmation that came meanwhile."""
    now = [datetime(2026, 11, 16, 9, 0)]
    d = orders_deps(tmp_path, madeup_out, now)
    root, stop = tmp_path / "cases", tmp_path / "STOP"
    nothing = FakeSession()
    run_loop(d, stop, poll_seconds=0, max_passes=1, account=signed_in_account(tmp_path, FakeApp()), session=nothing)
    assert nothing.calls == [] and "mailbox_lapsed" in json.loads((root / "digest_state.json").read_text(encoding="utf-8"))
    connection = {"mailbox": None, "connected_at": stamp(now[0]), "delta_link": None, "last_sync": None, "saved": 0}
    assert state(tmp_path) == connection | {"folder": FOLDER} and orders_state(tmp_path) == connection | {"folder": ORDERS}
    assert orders_known(tmp_path) == set() and order_lines(root) == []
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    sent_at, confirmed_at = stamp(now[0] + timedelta(minutes=30)), stamp(now[0] + timedelta(minutes=31))
    session = FakeSession({("GET", baseline()): page([message("C1", confirmed_at)], delta_link=DELTA_1),
                           ("GET", value_url("C1")): FakeResponse(content=confirmation_email()),
                           ("GET", orders_baseline()): page([sent("S1", sent_at)], delta_link=ORDERS_1),
                           ("GET", value_url("S1")): FakeResponse(content=order_mail(1, ("Inkooporder 250101.pdf", po)))})
    now[0] = datetime(2026, 11, 16, 10, 0)
    run_loop(d, stop, poll_seconds=0, max_passes=1, account=signed_in_account(tmp_path), session=session)
    assert [unquote(call["url"]) for call in session.calls] == [baseline(), value_url("C1"), orders_baseline(), value_url("S1")]
    name = order_name("S1", sent_at, po)
    assert (tmp_path / "inbox" / "processed" / name).exists() and (tmp_path / "store" / "250101" / "v1" / "po.json").exists()
    assert orders_state(tmp_path)["saved"] == 1 and any((case / "steps" / "08_result.json").exists() for case in root.glob("250101_*"))


def test_a_state_of_the_folder_for_orders_that_cannot_be_written_at_a_lapsed_pass_is_one_line_of_its_stage(tmp_path, madeup_out,
                                                                                                         monkeypatch):
    """When the connection of the folder for orders cannot be recorded at a pass whose sign-in has lapsed (a file another program
    holds), that is one line of the stage "order folder" in a run of the loop, by the class of the error and never its path; the lapse
    of the mailbox is noted and logged as before, and the folder of the mailbox keeps its connection."""
    now, passes = [datetime(2026, 11, 16, 9, 0)], []
    monkeypatch.setattr(aid.runner.time, "sleep", lambda seconds: passes.append(seconds))
    d = orders_deps(tmp_path, madeup_out, now)
    root = tmp_path / "cases"
    write = microsoft_mail._write_json

    def refused(path, data):
        if Path(path).name.startswith("mailbox_orders_"):
            raise PermissionError(f"made-up: {path}")
        return write(path, data)
    monkeypatch.setattr(microsoft_mail, "_write_json", refused)
    nothing = FakeSession()
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2, account=signed_in_account(tmp_path, FakeApp()), session=nothing) == 2
    assert nothing.calls == [] and order_lines(root) == ["order folder: PermissionError"] and len(mailbox_lines(root)) == 1
    assert state(tmp_path)["connected_at"] == stamp(now[0]) and not (root / "mailbox_orders_state.json").exists()
    assert str(tmp_path) not in (root / "runner.log").read_text(encoding="utf-8")


def test_without_a_folder_for_orders_its_connection_and_the_used_marks_are_forgotten(tmp_path, madeup_out):
    """A pass with the folder for orders set leaves its state, its marks and the used marks; a pass with it emptied removes the three
    and leaves the main folder's connection. Another folder for orders starts a connection of its own and keeps the used marks."""
    now = [datetime(2026, 11, 12, 9, 0)]
    d = orders_deps(tmp_path, madeup_out, now, without=())
    connected(tmp_path)
    orders_connected(tmp_path)
    root = tmp_path / "cases"
    (root / "mailbox_used.json").write_text(json.dumps({"0123456789ab": "orders"}), encoding="utf-8")
    session = FakeSession({("GET", DELTA_1): page([], delta_link=DELTA_1), ("GET", ORDERS_1): page([], delta_link=ORDERS_1)})
    account = signed_in_account(tmp_path)

    def files() -> list[str]:
        return sorted(path.name for path in root.iterdir() if path.name.startswith("mailbox_"))
    run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1, account=account, session=session)
    assert files() == ["mailbox_known.json", "mailbox_orders_known.json", "mailbox_orders_state.json", "mailbox_state.json", "mailbox_used.json"]
    off = d.model_copy(update={"settings": d.settings.model_copy(update={"order_folder": None, "order_folder_name": None})})
    run_loop(off, tmp_path / "STOP", poll_seconds=0, max_passes=1, account=account, session=session)
    assert files() == ["mailbox_known.json", "mailbox_state.json"]
    orders_connected(tmp_path)
    (root / "mailbox_used.json").write_text(json.dumps({"0123456789ab": "orders"}), encoding="utf-8")
    other = "AAMkADMadeUpOrders0002="
    session = FakeSession({("GET", f"{GRAPH}/me/mailFolders/{other}/messages/delta?changeType=created"
                                   "&$select=id,receivedDateTime,sentDateTime,hasAttachments"): page([], delta_link=ORDERS_2)})
    microsoft_mail.sync_orders(account, orders_settings(tmp_path, order_folder=other), NOW + timedelta(days=1), session=session)
    assert (orders_state(tmp_path)["folder"], orders_state(tmp_path)["connected_at"]) == (other, "2026-11-13T08:00:00Z")
    assert used(tmp_path) == {"0123456789ab": "orders"}


def test_the_command_reads_the_folder_for_orders_once_the_settings_file_names_it(tmp_path, madeup_out, monkeypatch):
    """A save on the settings tab that names a folder for orders is taken over at the next pass, as any change of the settings file."""
    path, d = file_deps(tmp_path, madeup_out, ms_tenant_id=TENANT_ID, ms_client_id=CLIENT_ID, mail_folder=FOLDER)
    monkeypatch.setattr(aid.runner.time, "sleep", lambda seconds: rewrite(path, order_folder=ORDERS, order_folder_name="Verzonden items"))
    rebuilt = []

    def rebuild(settings):
        rebuilt.append(settings)
        return d.model_copy(update={"settings": settings})
    session = FakeSession({("GET", baseline()): page([], delta_link=DELTA_1), ("GET", DELTA_1): page([], delta_link=DELTA_2),
                           ("GET", orders_baseline()): page([], delta_link=ORDERS_1)})
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2, settings_file=path, rebuild=rebuild, account=signed_in_account(tmp_path),
                    session=session) == 2
    assert [settings.order_folder for settings in rebuilt] == [ORDERS]
    assert [unquote(call["url"]) for call in session.calls] == [baseline(), DELTA_1, orders_baseline()]
    assert orders_state(tmp_path)["folder"] == ORDERS and not (tmp_path / "cases" / "runner.log").exists()


def test_the_runner_names_an_e_mail_that_cannot_be_read_by_its_number_and_class_only(tmp_path, madeup_out, monkeypatch):
    """An e-mail of the folder for orders that cannot be read is one line of runner.log, with the number of such e-mails and the class
    of the failure, never what the e-mail holds; it is known after it, so a later pass adds no line."""
    named = microsoft_mail._named_attachments

    def attachments(mail):
        if SUBJECT_MARKER in str(mail["Subject"]):
            raise IndexError(f"made-up: {SUBJECT_MARKER}")
        return named(mail)
    monkeypatch.setattr(microsoft_mail, "_named_attachments", attachments)
    now = [datetime(2026, 11, 12, 9, 0)]
    d = orders_deps(tmp_path, madeup_out, now)
    po = (madeup_out / "MU01_po_v1.pdf").read_bytes()
    session = FakeSession({("GET", baseline()): page([], delta_link=DELTA_1), ("GET", orders_baseline()): page([], delta_link=ORDERS_1),
                           ("GET", DELTA_1): page([], delta_link=DELTA_1),
                           ("GET", ORDERS_1): page([sent("A"), sent("B", "2026-11-12T09:01:30Z")], delta_link=ORDERS_2),
                           ("GET", value_url("A")): FakeResponse(content=order_mail(60, ("a.pdf", po), subject=f"Bijlage {SUBJECT_MARKER}")),
                           ("GET", value_url("B")): FakeResponse(content=order_mail(62, ("a.pdf", po))), ("GET", ORDERS_2): page([], delta_link=ORDERS_2)})
    account, root = signed_in_account(tmp_path), tmp_path / "cases"
    for minute in range(3):
        now[0] = datetime(2026, 11, 12, 9, minute)
        run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1, account=account, session=session)
    assert order_lines(root) == ["order folder: 1 new e-mail could not be read and was passed over (IndexError)"]
    assert SUBJECT_MARKER not in (root / "runner.log").read_text(encoding="utf-8") and holding(tmp_path, SUBJECT_MARKER) == []
