"""Tests of the Microsoft sign-in (aid/connections/microsoft.py). A fake msal application (aid/tests/fakes.py) answers in place of
Microsoft: no test opens a browser or reaches the network."""
import msal
import pytest
import requests

from aid.connections.microsoft import (MAIL_SCOPES, SHARED_SCOPES, MicrosoftAccount, MicrosoftUnreachable, SignInFailed, SignInNeeded,
                                       mail_scopes)
from aid.connections.secrets import SecretStore
from aid.tests.fakes import ACCESS_TOKEN, CLIENT_ID, TENANT_ID, USERNAME, FakeApp, sign_in_event, signed_in


def account(tmp_path, app: FakeApp) -> MicrosoftAccount:
    store = SecretStore(tmp_path / "secrets")
    return MicrosoftAccount(TENANT_ID, CLIENT_ID, store, app=app.bind(store.token_cache()))


def calls(app: FakeApp, name: str) -> list[tuple]:
    return [call for call in app.calls if call[0] == name]


def test_the_scopes_read_and_send_mail_and_read_the_profile():
    assert {"Mail.Read", "Mail.Send", "User.Read"} <= set(MAIL_SCOPES)
    assert "Mail.ReadWrite" in MAIL_SCOPES                   # the draft that a message is sent from (aid/messaging/microsoft_mailer.py)
    assert not {"offline_access", "openid", "profile"} & set(MAIL_SCOPES)        # msal adds these itself
    assert mail_scopes(None) == MAIL_SCOPES
    assert mail_scopes("orders@example.com") == MAIL_SCOPES + SHARED_SCOPES          # another mailbox, by its address
    assert SHARED_SCOPES == ["Mail.Read.Shared", "Mail.ReadWrite.Shared", "Mail.Send.Shared"]


def test_the_browser_sign_in_returns_the_account(tmp_path):
    app = FakeApp()
    signing = account(tmp_path, app)
    assert signing.account() is None
    assert signing.sign_in_browser(MAIL_SCOPES) == USERNAME and signing.account() == USERNAME
    [(_, scopes, options)] = calls(app, "acquire_token_interactive")
    assert scopes == MAIL_SCOPES and options["prompt"] == "select_account" and options["timeout"] > 0


def test_a_sign_in_that_fails_is_named_by_its_error_code_only(tmp_path):
    app = FakeApp(result={"error": "access_denied", "error_description": "AADSTS65004: made-up description of the refusal"})
    with pytest.raises(SignInFailed) as raised:
        account(tmp_path, app).sign_in_browser(MAIL_SCOPES)
    assert str(raised.value) == "access_denied"
    odd = FakeApp(result={"error": "made up <b>code</b> with words", "error_description": "made-up"})
    with pytest.raises(SignInFailed) as raised:
        account(tmp_path, odd).sign_in_browser(MAIL_SCOPES)
    assert "<" not in str(raised.value) and " " not in str(raised.value) and "made-up" not in str(raised.value)


def test_the_device_sign_in_gives_the_code_and_the_address_and_then_the_account(tmp_path):
    app = FakeApp()
    signing = account(tmp_path, app)
    flow = signing.start_device_sign_in(MAIL_SCOPES)
    assert (flow["user_code"], flow["verification_uri"]) == ("ABCD-1234", "https://microsoft.com/devicelogin")
    assert calls(app, "initiate_device_flow")[0][1] == MAIL_SCOPES
    assert signing.finish_device_sign_in(flow) == USERNAME and signing.account() == USERNAME
    assert calls(app, "acquire_token_by_device_flow")[0][2]["flow"] == flow
    refused = FakeApp(flow={"error": "invalid_client", "error_description": "AADSTS7000218: made-up"})
    with pytest.raises(SignInFailed, match="^invalid_client$"):
        account(tmp_path, refused).start_device_sign_in(MAIL_SCOPES)


def test_the_token_comes_silently_from_the_account_in_the_cache(tmp_path):
    app = FakeApp(accounts=[USERNAME])
    assert account(tmp_path, app).token(MAIL_SCOPES) == ACCESS_TOKEN
    [(_, scopes, options)] = calls(app, "acquire_token_silent_with_error")
    assert scopes == MAIL_SCOPES and options["account"]["username"] == USERNAME
    assert not calls(app, "acquire_token_interactive")      # never a window from the runner


@pytest.mark.parametrize("accounts, silent, code", [
    ((), None, ""),                                                                      # nobody signed in
    ((USERNAME,), None, ""),                                                             # msal has no token for the account
    ((USERNAME,), {"error": "invalid_grant", "error_description": "AADSTS70043: made-up, the refresh token has expired"},
     "(invalid_grant)"),                                                                 # Microsoft refused the refresh
])
def test_without_a_usable_sign_in_the_token_is_sign_in_needed(tmp_path, accounts, silent, code):
    with pytest.raises(SignInNeeded) as raised:
        account(tmp_path, FakeApp(accounts=accounts, silent=silent)).token(MAIL_SCOPES)
    assert "Settings tab" in str(raised.value) and "made-up" not in str(raised.value) and "AADSTS" not in str(raised.value)
    assert code in str(raised.value)


def test_a_microsoft_that_cannot_be_reached_is_named_by_the_class_of_the_error_only(tmp_path):
    class Offline(FakeApp):
        def acquire_token_silent_with_error(self, scopes, account, **options):
            raise requests.ConnectionError(f"made-up failure for https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token")

    with pytest.raises(MicrosoftUnreachable) as raised:
        account(tmp_path, Offline(accounts=[USERNAME])).token(MAIL_SCOPES)
    assert str(raised.value) == "ConnectionError" and raised.value.__cause__ is None and raised.value.__suppress_context__


def test_the_signed_in_account_is_read_from_the_cache_without_the_network(tmp_path):
    """Whether an account is signed in is read from the store's token cache, which needs no network (msal's application, which
    the conftest makes raise, would ask Microsoft about the tenant)."""
    store = SecretStore(tmp_path / "secrets")
    signing = MicrosoftAccount(TENANT_ID, CLIENT_ID, store)
    assert signing.account() is None
    store.token_cache().add(sign_in_event(USERNAME))
    assert signing.account() == USERNAME


def test_the_account_is_read_from_the_token_cache_also_once_the_application_is_made(tmp_path):
    """msal's get_accounts asks Microsoft about the authority (instance discovery) when it finds no account, as after a sign-out:
    who is signed in is read from the store's token cache instead, also once the application is made."""
    app = FakeApp()
    signing = account(tmp_path, app)
    assert signing.sign_in_browser(MAIL_SCOPES) == USERNAME
    before = len(calls(app, "get_accounts"))
    assert signing.account() == USERNAME and len(calls(app, "get_accounts")) == before
    signing.sign_out()
    before = len(calls(app, "get_accounts"))
    assert signing.account() is None and len(calls(app, "get_accounts")) == before


def test_sign_out_removes_the_account(tmp_path):
    app = FakeApp(accounts=[USERNAME])
    signing = account(tmp_path, app)
    signing.sign_out()
    assert signing.account() is None and app.accounts == []
    with pytest.raises(SignInNeeded):
        signing.token(MAIL_SCOPES)


def test_a_sign_in_as_another_account_keeps_that_account_alone(tmp_path):
    app = FakeApp(accounts=["earlier@example.com"], result=signed_in("buyer@example.org"))
    signing = account(tmp_path, app)
    assert signing.sign_in_browser(MAIL_SCOPES) == "buyer@example.org"
    assert [entry["username"] for entry in app.accounts] == ["buyer@example.org"] and signing.account() == "buyer@example.org"


def test_the_application_is_made_on_first_use_with_the_tenant_and_the_cache_of_the_store(tmp_path, monkeypatch):
    """msal asks Microsoft about the tenant when its application is made, so an account that is only built, as build_deps and the
    review page build one, reaches nothing."""
    made, timeouts = [], []

    class Recorder(FakeApp):
        def __init__(self, client_id, authority=None, token_cache=None, **options):
            super().__init__()
            made.append((client_id, authority, token_cache))
            timeouts.append(options.get("timeout"))

    monkeypatch.setattr(msal, "PublicClientApplication", Recorder)
    store = SecretStore(tmp_path / "secrets")
    signing = MicrosoftAccount(TENANT_ID, CLIENT_ID, store)
    assert signing.account() is None and made == []           # read from the cache
    with pytest.raises(SignInNeeded):
        signing.token(MAIL_SCOPES)
    with pytest.raises(SignInNeeded):
        signing.token(MAIL_SCOPES)
    assert made == [(CLIENT_ID, f"https://login.microsoftonline.com/{TENANT_ID}", store.token_cache())]
    assert timeouts == [30]                                  # msal's own requests wait no longer than Graph's (GRAPH_TIMEOUT)

    def offline(*args, **options):
        raise requests.ConnectionError(f"made-up failure for https://login.microsoftonline.com/{TENANT_ID}")

    monkeypatch.setattr(msal, "PublicClientApplication", offline)
    with pytest.raises(MicrosoftUnreachable, match="^ConnectionError$"):
        MicrosoftAccount(TENANT_ID, CLIENT_ID, store).token(MAIL_SCOPES)
