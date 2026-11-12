"""Tests of the trigger of the post-check through the work mailbox (aid/sources/microsoft_mail.py) and of its place in the runner's
pass (aid/runner.py): the folder of the mailbox is read through Microsoft Graph, faked here by FakeSession, with a fake sign-in
(aid/tests/fakes.py). No test reaches Microsoft or the network."""
import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
import requests

import aid.runner
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
