"""The Microsoft sign-in of the aid: the user's own work account, through Microsoft's own library MSAL, with the app registration's
tenant ID and client ID from the settings (ms_tenant_id, ms_client_id). The manual's section 3.14 gives the steps of the registration.

MicrosoftAccount wraps an msal PublicClientApplication with the authority https://login.microsoftonline.com/<tenant id> and the
token cache of the SecretStore (aid/connections/secrets.py; the runner's is a LazySecretStore), which the review page and the
runner share. msal asks Microsoft about the tenant when the application is made, so the account makes it on first use, for a
sign-in or a token: an account that is only built, or only asked who is signed in, reaches nothing. The application waits at most
GRAPH_TIMEOUT seconds for each of its own requests to Microsoft (msal's default is no limit), so that a network that takes a
request and never answers cannot hold the runner or the page. The tests give the account a fake application.

- sign_in_browser opens Microsoft's sign-in page in the browser of the PC where the aid runs (acquire_token_interactive, with the
  redirect to http://localhost of the registration's platform "Mobile and desktop applications") and waits for it, at most
  BROWSER_SECONDS. It is the default way to sign in.
- start_device_sign_in and finish_device_sign_in are the device sign-in, for a PC without a browser: the first gives the code and the
  address (Microsoft's flow, with "user_code" and "verification_uri"), the second waits until the code has been entered elsewhere.
- token gives an access token silently, from the account in the cache (acquire_token_silent_with_error, which renews it with the
  refresh token when it has expired). Without an account, or when Microsoft no longer accepts the sign-in, it raises SignInNeeded,
  with msal's error code when Microsoft gave one ("invalid_grant"). When Microsoft cannot be reached, making the application or
  renewing the token raises MicrosoftUnreachable, whose text is the class of the error only: requests' own text names the address,
  with the tenant ID.
- account gives the signed-in account's name (its e-mail address), or None, read from the store's token cache without the network,
  also once the application is made: msal's own get_accounts asks Microsoft about the authority when it finds no account, as
  after a sign-out. sign_out removes the account from the cache.

After a sign-in the cache holds that account only, so that the runner and the page always use the account last signed in. The
scopes are mail_scopes(mailbox): MAIL_SCOPES for the account's own mailbox, and the SHARED_SCOPES besides for another mailbox given
by its address, as Microsoft requires for a shared or delegated mailbox
(https://learn.microsoft.com/en-us/graph/outlook-share-messages-folders); the sign-in for Microsoft 365 Copilot, the AI service
(aid/readers/copilot_client.py), asks for COPILOT_SCOPES besides; msal adds offline_access, openid and profile itself. A token or an
error description of msal is never shown, logged or raised: a sign-in that fails is SignInFailed with msal's error code only (for
example "invalid_grant").

GRAPH is the address of Microsoft Graph that the trigger (aid/sources/microsoft_mail.py) and the mailer
(aid/messaging/microsoft_mailer.py) call with the account's token, and owner_path the part of an address that names the mailbox.
"""
import re
from collections.abc import Sequence
from urllib.parse import quote

import msal
import requests

from aid.connections.secrets import LazySecretStore, SecretStore

AUTHORITY = "https://login.microsoftonline.com/"
GRAPH = "https://graph.microsoft.com/v1.0"
GRAPH_TIMEOUT = 30                                # seconds per request to Graph
# Mail.Read reads the folder of the trigger (aid/sources/microsoft_mail.py), User.Read the account's own profile, Mail.Send sends a
# message, and Mail.ReadWrite creates the draft it is sent from (aid/messaging/microsoft_mailer.py says why a draft).
MAIL_SCOPES = ["Mail.Read", "Mail.ReadWrite", "Mail.Send", "User.Read"]
SHARED_SCOPES = ["Mail.Read.Shared", "Mail.ReadWrite.Shared", "Mail.Send.Shared"]     # the same for another mailbox
# The seven delegated permissions that the Copilot Chat API of Microsoft 365 Copilot (aid/readers/copilot_client.py) needs together;
# People.Read.All, OnlineMeetingTranscript.Read.All, ChannelMessage.Read.All and ExternalItem.Read.All need an administrator's consent.
COPILOT_SCOPES = ["Sites.Read.All", "Mail.Read", "People.Read.All", "OnlineMeetingTranscript.Read.All", "Chat.Read",
                  "ChannelMessage.Read.All", "ExternalItem.Read.All"]
BROWSER_SECONDS = 300                             # how long the browser sign-in waits for the person
SIGN_IN_AGAIN = "sign in again on the Settings tab of the review page"
_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}")        # an error code of msal, such as "invalid_grant"


class SignInNeeded(Exception):
    """No account is signed in, or the sign-in has lapsed: the message says to sign in again on the settings tab."""


class SignInFailed(Exception):
    """A sign-in that did not succeed; its text is msal's error code only."""


class MicrosoftUnreachable(Exception):
    """Microsoft could not be reached; its text is the class of the error only."""


def mail_scopes(mailbox: str | None) -> list[str]:
    """The scopes for the mailbox: MAIL_SCOPES for the signed-in account's own (None), and the SHARED_SCOPES besides for another."""
    return MAIL_SCOPES + SHARED_SCOPES if mailbox else list(MAIL_SCOPES)


class MicrosoftAccount:
    """The signed-in work account (see the module docstring)."""

    def __init__(self, tenant_id: str, client_id: str, store: SecretStore | LazySecretStore, app=None):
        self.tenant_id, self.client_id, self.store, self._app = tenant_id, client_id, store, app

    def application(self):
        """The msal application, made on first use."""
        if self._app is None:
            try:
                self._app = msal.PublicClientApplication(self.client_id, authority=AUTHORITY + self.tenant_id,
                                                         token_cache=self.store.token_cache(), timeout=GRAPH_TIMEOUT)
            except requests.RequestException as error:
                raise MicrosoftUnreachable(type(error).__name__) from None
        return self._app

    def sign_in_browser(self, scopes: Sequence[str]) -> str:
        """Sign in in this PC's browser and return the account."""
        result = self.application().acquire_token_interactive(list(scopes), prompt="select_account", timeout=BROWSER_SECONDS)
        return self._signed_in(result)

    def start_device_sign_in(self, scopes: Sequence[str]) -> dict:
        """Start the device sign-in: the flow, whose "user_code" is entered at its "verification_uri"."""
        flow = self.application().initiate_device_flow(scopes=list(scopes))
        if "user_code" not in flow:
            raise SignInFailed(_code(flow))
        return flow

    def finish_device_sign_in(self, flow: dict) -> str:
        """Wait until the code of the flow has been entered, and return the account."""
        return self._signed_in(self.application().acquire_token_by_device_flow(flow))

    def token(self, scopes: Sequence[str]) -> str:
        """An access token for the scopes, silently from the account in the cache; SignInNeeded when there is none to be had."""
        app = self.application()
        accounts = app.get_accounts()
        if not accounts:
            raise SignInNeeded(f"no Microsoft account is signed in: {SIGN_IN_AGAIN}")
        try:
            result = app.acquire_token_silent_with_error(list(scopes), account=accounts[0])
        except requests.RequestException as error:
            raise MicrosoftUnreachable(type(error).__name__) from None
        if not result or "access_token" not in result:
            code = f" ({_code(result)})" if result else ""
            raise SignInNeeded(f"the Microsoft sign-in has lapsed{code}: {SIGN_IN_AGAIN}")
        return result["access_token"]

    def account(self) -> str | None:
        """The signed-in account's name, its e-mail address, or None, from the store's token cache, which the application keeps its
        accounts in and which needs no network, whether the application is made or not (see the module docstring)."""
        accounts = list(self.store.token_cache().search(msal.TokenCache.CredentialType.ACCOUNT))
        return accounts[0].get("username") if accounts else None

    def sign_out(self) -> None:
        """Remove every account from the cache, with its tokens."""
        app = self.application()
        for account in app.get_accounts():
            app.remove_account(account)

    def _signed_in(self, result: dict) -> str:
        """The account of a sign-in that succeeded, the only one left in the cache; SignInFailed with the error code otherwise."""
        if "access_token" not in result:
            raise SignInFailed(_code(result))
        app = self.application()
        username = (result.get("id_token_claims") or {}).get("preferred_username")
        accounts = app.get_accounts()
        username = username or (accounts[0].get("username") if accounts else None)
        for other in accounts:
            if other.get("username") != username:
                app.remove_account(other)
        return username or ""


def owner_path(mailbox: str | None) -> str:
    """The part of a Graph address that names the mailbox: "me" for the signed-in account's own, else "users/<address>"."""
    return f"users/{quote(mailbox, safe='@')}" if mailbox else "me"


def _code(result: dict) -> str:
    """msal's error code of a result, when it looks like one; never its description."""
    code = str(result.get("error") or "")
    return code if _CODE.fullmatch(code) else "unknown_error"
