"""Tests of the sending through the work mailbox (aid/messaging/microsoft_mailer.py) and of the runner's choice of its mailer
(build_deps of aid/runner.py). Microsoft Graph is FakeSession and the sign-in a fake msal application (aid/tests/fakes.py)."""
import base64
import json
from datetime import datetime
from email import message_from_bytes, policy
from urllib.parse import unquote

import pytest
import requests

import aid.runner
from aid.case import open_case
from aid.connections.microsoft import SHARED_SCOPES, MicrosoftAccount
from aid.connections.secrets import LazySecretStore, SecretStore
from aid.messaging import FileMailer, HandOverUnconfirmed, MailerError, Message, SmtpMailer
from aid.messaging.microsoft_mailer import AutoMailer, MicrosoftMailer, SignInLapsed, mailer_of
from aid.pipeline import run_case
from aid.runner import build_deps
from aid.settings import load_settings
from aid.sources.mailbox import scan_folder
from aid.tests.conftest import SETTINGS, deps
from aid.tests.fakes import ACCESS_TOKEN, CLIENT_ID, GRAPH, TENANT_ID, USERNAME, FakeApp, FakeResponse, FakeSession, sign_in_event, signed_in

NOW = datetime(2026, 11, 12, 9, 0)
DRAFT = "AAMkADMadeUpDraft0001="                     # a made-up id of a draft, as Graph gives one
EXCHANGE_ID = "<AM0PR01MB0001ABCD@AM0PR01MB0001.eurprd01.prod.example.com>"       # made up, in the form of Exchange Online's ids
MESSAGE = Message(to=["s11@example.com"], cc=["buyer@example.com"], subject="Vraag over orderbevestiging PO 250111",
                  body="Klopt het aantal van regel 1?", template="quantity_unit", case_id="250111_20261112T090000")


def account(tmp_path, app: FakeApp | None = None) -> MicrosoftAccount:
    store = SecretStore(tmp_path / "secrets")
    return MicrosoftAccount(TENANT_ID, CLIENT_ID, store, app=(app or FakeApp(accounts=[USERNAME])).bind(store.token_cache()))


def graph(owner: str = "me", internet_id: str | None = None, created: int = 201, sent: int = 202) -> FakeSession:
    """Graph as the mailer meets it: the draft made from the MIME it is given has that MIME's Message-ID as its internetMessageId,
    or internet_id when one is given (an id of Exchange's own), and sending the draft is accepted."""
    def create(request):
        mime = message_from_bytes(base64.b64decode(request["data"]), policy=policy.default)
        return FakeResponse(created, json_data={"id": DRAFT, "isDraft": True, "internetMessageId": internet_id or mime["Message-ID"]})
    return FakeSession({("POST", f"{GRAPH}/{owner}/messages"): create, ("POST", f"{GRAPH}/{owner}/messages/{DRAFT}/send"): FakeResponse(sent)})


def mailer(tmp_path, session, mailbox: str | None = None, sender: str = "", app: FakeApp | None = None) -> MicrosoftMailer:
    """The mailer of the runner: no sender given, so that the From is the mailbox, or the account signed in at each send."""
    return MicrosoftMailer(account(tmp_path, app), mailbox, sender, clock=lambda: NOW, session=session)


def test_a_message_goes_out_as_the_aids_own_mime_through_a_draft_that_is_then_sent(tmp_path):
    session = graph()
    record = mailer(tmp_path, session).send(MESSAGE)
    create, send = session.calls
    assert (create["method"], create["url"]) == ("POST", f"{GRAPH}/me/messages")
    assert create["headers"]["Content-Type"] == "text/plain" and create["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    mime = base64.b64decode(create["data"])
    mail = message_from_bytes(mime, policy=policy.default)
    assert mail["Message-ID"] == f"<{record.message_id}@example.com>" and len(record.message_id) == 32
    assert (mail["From"], mail["To"], mail["Cc"], mail["Subject"]) == (USERNAME, "s11@example.com", "buyer@example.com", MESSAGE.subject)
    assert (mail["X-Aid-Case"], mail["X-Aid-Template"], mail["Auto-Submitted"]) == (MESSAGE.case_id, "quantity_unit", "auto-generated")
    assert mail.get_content().strip() == MESSAGE.body and b"\r\n" in mime
    assert (send["method"], unquote(send["url"])) == ("POST", f"{GRAPH}/me/messages/{DRAFT}/send") and not send["data"]
    assert send["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert record.internet_message_id == mail["Message-ID"]
    assert (record.to, record.subject, record.sent_at, record.template) == (MESSAGE.to, MESSAGE.subject, "2026-11-12T09:00:00", "quantity_unit")


@pytest.mark.parametrize("internet_id", [None, EXCHANGE_ID], ids=["the aid's Message-ID kept", "an id of Exchange's own"])
def test_a_reply_to_a_question_sent_through_microsoft_is_filed_into_its_case(tmp_path, madeup_out, internet_id):
    d = deps(tmp_path, madeup_out)
    d = d.model_copy(update={"mailer": mailer(tmp_path, graph(internet_id=internet_id)),
                             "settings": d.settings.model_copy(update={"supplier_emails": {"S11": "s11@example.com"}})})
    case = open_case(d.settings.cases_root, "confirmation", [madeup_out / "MU11_conf.pdf"], "250111", d.clock())
    assert run_case(case, d).status == "waiting"
    [line] = (case.root / "messages" / "sent.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    named = record["internet_message_id"]
    assert named == (internet_id or f"<{record['message_id']}@example.com>")
    if internet_id:                                          # the id the supplier's reply names is the one recorded
        assert record["message_id"] == "AM0PR01MB0001ABCD"
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    reply = (f"From: s11@example.com\nTo: {USERNAME}\nSubject: Re: {MESSAGE.subject}\nIn-Reply-To: {named}\nReferences: {named}\n\n"
             "Het aantal klopt.\n").encode()
    (inbox / "antwoord.eml").write_bytes(reply)
    scan = scan_folder(inbox, d.store, d.settings.cases_root, d.settings, NOW)
    assert scan.replies == [("antwoord.eml", case.case_id)] and scan.answered == [case.case_id] and scan.new_cases == []
    assert (case.root / "messages" / "replies" / "antwoord.eml").read_bytes() == reply
    assert case.read_step("result")["status"] == "review"


def test_a_message_of_another_mailbox_goes_out_from_that_mailbox(tmp_path):
    session = graph(owner="users/orders@example.com")
    app = FakeApp(accounts=[USERNAME])
    record = mailer(tmp_path, session, mailbox="orders@example.com", app=app).send(MESSAGE)
    assert [unquote(call["url"]) for call in session.calls] == [f"{GRAPH}/users/orders@example.com/messages",
                                                                 f"{GRAPH}/users/orders@example.com/messages/{DRAFT}/send"]
    mail = message_from_bytes(base64.b64decode(session.calls[0]["data"]), policy=policy.default)
    assert mail["From"] == "orders@example.com" and record.internet_message_id == f"<{record.message_id}@example.com>"
    [(_, scopes, _)] = [call for call in app.calls if call[0] == "acquire_token_silent_with_error"]
    assert set(SHARED_SCOPES) <= set(scopes)                  # another mailbox needs the shared scopes


def test_the_from_is_the_account_signed_in_when_the_message_is_sent(tmp_path):
    """The runner gives no sender: a sign-in made after the runner started, as another account, sends From that account."""
    app = FakeApp(accounts=[USERNAME], result=signed_in("buyer@example.org"))
    sending = mailer(tmp_path, graph(), app=app)
    sending.send(MESSAGE)
    sending.account.sign_in_browser(["Mail.Send"])
    sending.send(MESSAGE)
    froms = [message_from_bytes(base64.b64decode(call["data"]), policy=policy.default)["From"] for call in sending.session.calls
             if call["url"].endswith("/me/messages")]
    assert froms == [USERNAME, "buyer@example.org"]


class Unreachable(FakeSession):
    """Graph that cannot be reached: requests raises with the address in its text."""

    def post(self, url, headers=None, data=None, timeout=None, **kwargs):
        raise requests.ConnectionError(f"made-up failure for {url}")


def draft_only(status: int) -> FakeSession:
    """Graph that answers the making of the draft with this status and nothing else."""
    return FakeSession({("POST", f"{GRAPH}/me/messages"): FakeResponse(status, json_data={"error": {"code": "ErrorAccessDenied", "message": "made-up detail"}})})


@pytest.mark.parametrize("session, shown, kind", [
    (draft_only(403), "HTTP 403", "refused"),
    (draft_only(429), "HTTP 429", "unreachable"),                                    # asked too often: another try may pass
    (draft_only(503), "HTTP 503", "unreachable"),
    (graph(sent=500), "HTTP 500", "unconfirmed"),
    (graph(sent=403), "HTTP 403", "refused"),                                        # Graph says no to the send: the draft did not leave
    (graph(sent=429), "HTTP 429", "unreachable"),
    (FakeSession({("POST", f"{GRAPH}/me/messages"): FakeResponse(201, json_data={"isDraft": True})}), "no id", "refused"),
    (Unreachable(), "ConnectionError", "unreachable"),
])
def test_an_error_is_a_mailer_error_with_the_status_code_or_the_class_only(tmp_path, session, shown, kind):
    """Its kind is what the review page tells the buyer to do by: "unreachable" for what another try may get past, "refused" for
    what it will not, and "unconfirmed" for a failure at the send of the draft (HandOverUnconfirmed)."""
    with pytest.raises(MailerError) as raised:
        mailer(tmp_path, session).send(MESSAGE)
    text = str(raised.value)
    assert shown in text and "made-up" not in text and ACCESS_TOKEN not in text and GRAPH not in text and "s11@example.com" not in text
    assert raised.value.__cause__ is None and (raised.value.__context__ is None or raised.value.__suppress_context__)
    assert raised.value.kind == kind and isinstance(raised.value, HandOverUnconfirmed) == (kind == "unconfirmed")


@pytest.mark.parametrize("internet_id", [None, EXCHANGE_ID], ids=["the aid's Message-ID kept", "an id of Exchange's own"])
@pytest.mark.parametrize("failure", [requests.ReadTimeout("made-up: no answer in time"), requests.ConnectionError("made-up: reset"), 500, 504])
def test_a_failure_at_the_send_of_the_draft_leaves_the_hand_over_unconfirmed(tmp_path, failure, internet_id):
    """Ruling S5 of the review round of Task 44. The draft is made, and its send fails without an answer, or with an answer of a
    server that failed: the message may have left. That is HandOverUnconfirmed, with the record of the message under the id the
    supplier would have received (the draft's internetMessageId), marked as not confirmed; a failure at the making of the draft is
    a message not sent, as before."""
    session = graph(internet_id=internet_id)

    def fails(request):
        if isinstance(failure, int):
            return FakeResponse(failure, json_data={"error": {"code": "made-up", "message": "made-up detail"}})
        raise failure
    session.answers[("POST", f"{GRAPH}/me/messages/{DRAFT}/send")] = [fails]
    with pytest.raises(HandOverUnconfirmed) as raised:
        mailer(tmp_path, session).send(MESSAGE)
    record, text = raised.value.record, str(raised.value)
    assert text.endswith(": no confirmation that the draft was sent") and "made-up" not in text and ACCESS_TOKEN not in text
    assert raised.value.kind == "unconfirmed" and isinstance(raised.value, MailerError) and not isinstance(raised.value, SignInLapsed)
    mime = message_from_bytes(base64.b64decode(session.calls[0]["data"]), policy=policy.default)
    expected = internet_id or mime["Message-ID"]
    assert (record.internet_message_id, record.message_id) == (expected, expected.strip("<>").partition("@")[0])
    assert (record.unconfirmed, record.to, record.template) == (True, MESSAGE.to, "quantity_unit")


def test_a_lapsed_sign_in_has_the_kind_of_a_sign_in(tmp_path):
    with pytest.raises(SignInLapsed) as lapsed:
        mailer(tmp_path, graph(), app=FakeApp()).send(MESSAGE)
    assert lapsed.value.kind == "sign_in"
    with pytest.raises(SignInLapsed) as lapsed:
        mailer(tmp_path, graph(sent=401)).send(MESSAGE)                              # a 401 at the send: the token was not taken
    assert lapsed.value.kind == "sign_in" and not isinstance(lapsed.value, HandOverUnconfirmed)


def test_a_lapsed_sign_in_is_a_mailer_error_of_its_own_and_asks_graph_nothing(tmp_path):
    session = graph()
    with pytest.raises(SignInLapsed, match="Settings tab") as lapsed:
        mailer(tmp_path, session, app=FakeApp()).send(MESSAGE)
    assert isinstance(lapsed.value, MailerError) and session.calls == []
    refused = FakeSession({("POST", f"{GRAPH}/me/messages"): FakeResponse(401, json_data={"error": {"code": "InvalidAuthenticationToken"}})})
    with pytest.raises(SignInLapsed, match="HTTP 401"):
        mailer(tmp_path, refused).send(MESSAGE)


def test_a_microsoft_that_cannot_be_reached_for_the_token_is_a_mailer_error_with_the_class_only(tmp_path):
    class Offline(FakeApp):
        def acquire_token_silent_with_error(self, scopes, account, **options):
            raise requests.ConnectionError(f"made-up failure for https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token")

    session = graph()
    with pytest.raises(MailerError) as raised:
        mailer(tmp_path, session, app=Offline(accounts=[USERNAME])).send(MESSAGE)
    assert str(raised.value).startswith("ConnectionError") and TENANT_ID not in str(raised.value) and "login" not in str(raised.value)
    assert not isinstance(raised.value, SignInLapsed) and session.calls == []


def test_auto_sends_through_the_mailbox_while_an_account_is_signed_in_and_else_the_other_way(tmp_path):
    """send_via "auto" is decided at each message, from the token cache: before the sign-in and after a sign-out the messages are
    written as files, and once an account is signed in they go through the mailbox, without a restart of the runner."""
    app = FakeApp()
    session = graph()
    auto = AutoMailer(mailer(tmp_path, session, app=app), FileMailer(tmp_path / "outbox", sender="aid@example.com"))
    auto.send(MESSAGE)
    assert len(list((tmp_path / "outbox").glob("*.eml"))) == 1 and session.calls == []
    auto.microsoft.account.sign_in_browser(["Mail.Send"])
    assert auto.current() is auto.microsoft and auto.send(MESSAGE).internet_message_id and len(session.calls) == 2
    auto.microsoft.account.sign_out()
    auto.send(MESSAGE)
    assert len(list((tmp_path / "outbox").glob("*.eml"))) == 2 and len(session.calls) == 2


def test_while_the_sign_in_has_lapsed_auto_sends_nothing_and_tries_no_other_way(tmp_path):
    """An account in the cache whose sign-in has lapsed: "auto" keeps to the mailbox, whose message is not sent (SignInLapsed, which
    the pipeline records as not sent, for the buyer to send), and writes no file: a reply to another sender would never reach the
    folder that is read, and so never its case."""
    session = graph()
    auto = AutoMailer(mailer(tmp_path, session, app=FakeApp(accounts=[USERNAME], silent=None)),
                      FileMailer(tmp_path / "outbox", sender="aid@example.com"))
    with pytest.raises(SignInLapsed):
        auto.send(MESSAGE)
    assert not list((tmp_path / "outbox").glob("*.eml")) and session.calls == []


def test_a_store_this_process_cannot_use_is_a_lapse_of_the_mailer_that_names_neither_folder_nor_kind(tmp_path):
    """The store is made at the first send, so that the runner starts all the same; its refusal stops the mailbox as a lapsed
    sign-in does (the digest then goes to the outbox), by a fixed text, since the pipeline keeps the text in the audit."""
    store = tmp_path / "secrets"
    store.mkdir()
    (store / "backend").write_text("made-up-kind\n", encoding="utf-8")
    session = graph()
    refused = MicrosoftMailer(MicrosoftAccount(TENANT_ID, CLIENT_ID, LazySecretStore(store)), None, "", clock=lambda: NOW, session=session)
    with pytest.raises(SignInLapsed, match="^StoreRefused: ") as raised:
        refused.send(MESSAGE)
    assert str(store) not in str(raised.value) and "made-up-kind" not in str(raised.value) and session.calls == []


def test_a_draft_without_an_internet_message_id_keeps_the_aids_own_id(tmp_path):
    session = FakeSession({("POST", f"{GRAPH}/me/messages"): FakeResponse(201, json_data={"id": DRAFT}),
                           ("POST", f"{GRAPH}/me/messages/{DRAFT}/send"): FakeResponse(202)})
    record = mailer(tmp_path, session).send(MESSAGE)
    assert len(record.message_id) == 32 and record.internet_message_id is None


# --- the runner's choice of its mailer ---

def settings_file(tmp_path, **values):
    data = json.loads(SETTINGS.model_dump_json()) | {"cases_root": "cases", "po_store_root": "po_store", "watched_folder": "inbox",
                                                       "reader": "parser"} | values
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return load_settings(path)


MAIL = {"ms_tenant_id": TENANT_ID, "ms_client_id": CLIENT_ID, "mail_folder": "AAMkADMadeUpFolder0001="}
SMTP = {"smtp_host": "smtp.example.com", "smtp_user": "aid@example.com"}
PASSWORD = {"AID_MAIL_PASSWORD": "made-up-password"}


@pytest.mark.parametrize("values, environ, chosen, other", [
    (MAIL, {}, AutoMailer, FileMailer),                                         # auto with a folder: decided at each message
    (MAIL | SMTP, PASSWORD, AutoMailer, SmtpMailer),
    (MAIL | {"mail_folder": None}, {}, FileMailer, None),                       # no folder: the trigger is off
    (MAIL | {"send_via": "file"}, {}, FileMailer, None),
    (MAIL | SMTP | {"send_via": "smtp"}, PASSWORD, SmtpMailer, None),
    (MAIL | {"send_via": "smtp"}, {}, FileMailer, None),                        # SMTP not set up: files, as before
    (MAIL | {"mail_folder": None, "send_via": "microsoft"}, {}, MicrosoftMailer, None),
    (MAIL | {"send_via": "microsoft"}, {}, MicrosoftMailer, None),
    ({}, {}, FileMailer, None),                                                 # the settings of before: as before
])
def test_the_runner_chooses_its_mailer_by_send_via(tmp_path, values, environ, chosen, other):
    signed = account(tmp_path)
    mailer_built = build_deps(settings_file(tmp_path, **values), environ, account=signed).mailer
    assert type(mailer_built) is chosen
    if chosen is AutoMailer:
        assert type(mailer_built.other) is other and mailer_built.microsoft.account is signed
    microsoft = mailer_built.microsoft if chosen is AutoMailer else mailer_built
    if isinstance(microsoft, MicrosoftMailer):
        assert (microsoft.mailbox, microsoft.sender) == (values.get("mailbox"), "")          # the From is asked at each send


@pytest.mark.parametrize("values, environ, unrouted", [
    ({"send_via": "file"}, {}, None),                                           # the owner's choice: nothing may leave
    (MAIL | SMTP | {"send_via": "file"}, PASSWORD, None),
    ({}, {}, "no_mailbox"),                                                     # auto with nothing connected
    (MAIL | {"mail_folder": None}, {}, "no_mailbox"),                           # auto with a sign-in and no folder chosen
    (MAIL, {}, "no_mailbox"),                                                   # auto with a folder: the other way of the AutoMailer
    ({"send_via": "smtp"}, {}, "no_mailbox"),                                   # SMTP chosen and no server set
    (SMTP, {}, "no_password"), (SMTP | {"send_via": "smtp"}, {}, "no_password"), (MAIL | SMTP, {}, "no_password"),
])
def test_the_file_mailer_given_for_want_of_a_route_is_marked(tmp_path, values, environ, unrouted):
    """Ruling S4 of the review round of Task 44: mailer_of marks the file mailer it gives when the settings name a way to send
    that is not there (no mailbox connected, or the mail server's password not set), and leaves the one of send_via "file"
    unmarked: written as a file by choice. The runner and the page get the same mailer, mark included."""
    settings = settings_file(tmp_path, **values)
    built = mailer_of(settings, environ, lambda: account(tmp_path))
    files = built.other if isinstance(built, AutoMailer) else built
    assert type(files) is FileMailer and files.unrouted == unrouted and files.out_dir == settings.cases_root / "outbox"
    runner = build_deps(settings, environ, account=account(tmp_path)).mailer
    assert (runner.other if isinstance(runner, AutoMailer) else runner).unrouted == unrouted


def test_the_choice_of_auto_needs_no_network_and_follows_the_token_cache(tmp_path):
    """The runner may start before the network is up: making the Deps asks Microsoft nothing (the conftest makes msal's
    application raise), and an account in the store's token cache makes "auto" send through the mailbox."""
    d = build_deps(settings_file(tmp_path, **MAIL), {})
    assert type(d.mailer) is AutoMailer and d.mailer.current() is d.mailer.other
    assert d.mailer.microsoft.account.store.directory == aid.runner.default_secrets_dir()
    d.mailer.microsoft.account.store.token_cache().add(sign_in_event(USERNAME))
    assert d.mailer.current() is d.mailer.microsoft


def test_send_via_microsoft_needs_the_tenant_and_the_client(tmp_path):
    with pytest.raises(ValueError, match="ms_client_id"):
        settings_file(tmp_path, send_via="microsoft", ms_tenant_id=TENANT_ID)
