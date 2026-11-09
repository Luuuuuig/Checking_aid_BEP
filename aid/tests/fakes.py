"""Fakes shared by the tests of the checking aid: small made-up documents, a decider that gives one answer, and the write
entries and case results that the digest and the review page show.

The documents are the fakes of aid/tests/test_versioning.py, with a description on a line and a PO of its own added, so that
the tests of every step build their documents the same way. A line's description and delivery date are given by name, so that
no value can go to the wrong field. No fake calls a model.

The Microsoft sign-in and Microsoft Graph have fakes too: FakeApp stands in for msal's PublicClientApplication and FakeSession for
the requests session that Graph is called through, so that no test reaches Microsoft or the network. The tenant and client IDs,
the account and the token are made up. copilot_session is that session as the Copilot Chat API answers the Copilot client
(aid/readers/copilot_client.py), with made-up answers.
"""
import base64
import hashlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import unquote

import msal
from requests.structures import CaseInsensitiveDict

from aid.decider import Decision, DecisionAnswer
from aid.execution.ops import OpKind, WriteOp
from aid.execution.writer import WriteLogEntry
from aid.pipeline import CaseResult, Status, _digest_line   # _digest_line: the line of a case as the pipeline writes it
from aid.po_store import PoVersion
from aid.schema import DocLine, DocValue, Document

PO_NUMBER = "250101"
ENTRY_TIME = "2026-11-18T09:00:00"               # the time of every entry that entry() makes


def _value(text: str | None) -> DocValue:
    return DocValue(value=text, text=text) if text else DocValue()


def _numbered(lines: list[DocLine]) -> list[DocLine]:
    return [line.model_copy(update={"line_id": str(n)}) for n, line in enumerate(lines, 1)]


def line(code: str, qty: str, price: str | None = None, *, desc: str | None = None, date: str | None = None, unit: str | None = None,
         basis: str | None = None, discount: str | None = None, net_price: str | None = None) -> DocLine:
    """A document line with this item code, quantity, unit price, description, delivery date, unit, price basis, discount
    (in percent) and net price, each in normal form; "" or None leaves the value out. The document it goes into numbers it."""
    return DocLine(line_id="", item_code=_value(code), description=_value(desc), quantity=_value(qty), unit=_value(unit),
                   unit_price=_value(price), price_basis=_value(basis), discount_pct=_value(discount), net_price=_value(net_price),
                   delivery_date=_value(date))


def po(lines: list[DocLine], *, currency: str | None = None, incoterm: str | None = None) -> Document:
    """A made-up PO with these lines, numbered 1, 2, 3, as its PDF gives it, in this currency and with this Incoterm, if any."""
    return Document(kind="po", po_reference=_value(PO_NUMBER), currency=_value(currency), incoterm=_value(incoterm), lines=_numbered(lines))


def confirmation(lines: list[DocLine], *, currency: str | None = None, incoterm: str | None = None) -> Document:
    """A supplier's confirmation of these lines, numbered 1, 2, 3, that quotes no version and has no date, in this currency
    and with this Incoterm, if any."""
    return Document(kind="confirmation", currency=_value(currency), incoterm=_value(incoterm), lines=_numbered(lines))


def po_version(n: int, lines: list[DocLine], date: str | None = None) -> PoVersion:
    """Version n of a made-up PO with these lines, dated `date`, as the store gives it (no PDF behind it)."""
    document = Document(kind="po", po_reference=_value(PO_NUMBER), version_hint=_value(str(n)), lines=_numbered(lines))
    return PoVersion(po_number=PO_NUMBER, version=n, version_date=date, pdf_path=Path(f"v{n}.pdf"), document=document, supplier=None,
                     total=None, above_authorization=None)


class FakeDecider:
    """Gives the same answer to every decision and keeps the decisions it was asked."""
    name = "fake"

    def __init__(self, choice: str | None, confidence: float):
        self.choice, self.confidence, self.decisions = choice, confidence, []

    def decide(self, d: Decision) -> DecisionAnswer:
        self.decisions.append(d)
        return DecisionAnswer(choice=self.choice, confidence=self.confidence, rationale="the fake's rationale", source=self.name)


def entry(kind: OpKind, old: str | None, new: str | None, entry_id: str) -> WriteLogEntry:
    """A write made (status "done") by rule R04 at ENTRY_TIME: an operation of this kind on line 1 of the made-up PO, or on the
    article KM-1001 for an article_price, from the old value, which is also the entry's previous value, to the new one. Its
    case_id is empty until case_result puts the entry in a case."""
    where = {"article_code": "KM-1001"} if kind == "article_price" else {"line_id": "1"}
    op = WriteOp(kind=kind, po_number=PO_NUMBER, version=1, old_value=old, new_value=new, **where)
    return WriteLogEntry(entry_id=entry_id, case_id="", op=op, status="done", previous=old, new=new, rule_id="R04", evidence={},
                         timestamp=ENTRY_TIME)


def case_result(case_id: str, status: Status, writes: Sequence[WriteLogEntry] = (), *, supplier: str | None = None,
                language: str = "nl") -> CaseResult:
    """The result of the case with this id and status, holding these write entries, each given the case's id, and the digest line
    that the pipeline writes for them in the language, naming the supplier when one is given. The PO number is the part of the
    id before the first underscore, and none for "unknown". The result has no version, no findings, no actions, no messages and
    no review items: a test adds what it needs with model_copy."""
    po_number = case_id.partition("_")[0]
    entries = [write.model_copy(update={"case_id": case_id}) for write in writes]
    line = _digest_line(None if po_number == "unknown" else po_number, supplier, status, entries, language, "confirmation")
    return CaseResult(case_id=case_id, status=status, version=None, findings=None, actions=[], write_entries=entries, messages=[],
                      review_items=[], digest_line=line, supplier=supplier)


# --- the Microsoft sign-in and Microsoft Graph ---

TENANT_ID = "00000000-0000-0000-0000-000000000001"   # made up
CLIENT_ID = "00000000-0000-0000-0000-000000000002"   # made up
USERNAME = "buyer@example.com"                      # the signed-in account of the fakes
ACCESS_TOKEN = "made-up-access-token-0001"          # the tests check that it shows nowhere
GRAPH = "https://graph.microsoft.com/v1.0"
_UNSET = object()


def account_entry(username: str) -> dict:
    """An account as msal's get_accounts gives it."""
    return {"username": username, "home_account_id": f"{username}.home", "environment": "login.microsoftonline.com"}


def signed_in(username: str = USERNAME) -> dict:
    """The answer of msal to a sign-in that succeeded for the account."""
    return {"access_token": ACCESS_TOKEN, "token_type": "Bearer", "expires_in": 3600, "id_token_claims": {"preferred_username": username}}


def sign_in_event(username: str) -> dict:
    """What msal adds to its token cache after a sign-in of the account (made up; the id token is not signed). Each account has a
    user id of its own, so that the cache keeps two accounts apart."""
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    uid = hashlib.sha256(username.encode()).hexdigest()[:16]
    claims = {"preferred_username": username, "oid": uid, "tid": "t1", "iss": "https://login.microsoftonline.com/t1/v2.0", "aud": "c1", "sub": uid}
    return {"client_id": "c1", "scope": ["User.Read"], "token_endpoint": "https://login.microsoftonline.com/t1/oauth2/v2.0/token",
            "response": {"access_token": "made-up-access", "refresh_token": "made-up-refresh", "expires_in": 3600, "token_type": "Bearer",
                         "client_info": part({"uid": uid, "utid": "t1"}), "id_token": f"{part({'alg': 'none'})}.{part(claims)}."}}


class FakeApp:
    """A stand-in for msal's PublicClientApplication. It holds the accounts it is given, answers a sign-in (the browser or the
    device code) with `result` (a sign-in of USERNAME unless given), after which the account is among its accounts, and a silent
    request (acquire_token_silent_with_error) with `silent`: ACCESS_TOKEN unless given, an error such as {"error": "invalid_grant"}
    when Microsoft refuses the refresh, or None when msal has nothing. Every call is recorded in `calls` as (name, scopes, keyword
    arguments). Bound to a token cache (bind, as the tests' accounts do with their store's), it keeps its accounts in that cache
    too, as msal keeps them in the cache it is given, so that the account's name is read from the cache."""

    def __init__(self, accounts: Sequence[str] = (), result: dict | None = None, silent=_UNSET, flow: dict | None = None):
        self.token_cache = None
        self.accounts = [account_entry(username) for username in accounts]
        self.result = result if result is not None else signed_in()
        self.silent = {"access_token": ACCESS_TOKEN, "token_type": "Bearer"} if silent is _UNSET else silent
        self.flow = flow or {"user_code": "ABCD-1234", "verification_uri": "https://microsoft.com/devicelogin", "expires_in": 900,
                             "message": "To sign in, use a web browser to open the page and enter the code ABCD-1234."}
        self.calls: list[tuple] = []

    def get_accounts(self, username: str | None = None) -> list[dict]:
        self.calls.append(("get_accounts", None, {}))
        return [dict(account) for account in self.accounts if username in (None, account["username"])]

    def acquire_token_interactive(self, scopes, **kwargs) -> dict:
        self.calls.append(("acquire_token_interactive", list(scopes), kwargs))
        return self._sign_in()

    def initiate_device_flow(self, scopes=None, **kwargs) -> dict:
        self.calls.append(("initiate_device_flow", list(scopes or []), kwargs))
        return dict(self.flow)

    def acquire_token_by_device_flow(self, flow, **kwargs) -> dict:
        self.calls.append(("acquire_token_by_device_flow", None, {"flow": flow, **kwargs}))
        return self._sign_in()

    def acquire_token_silent_with_error(self, scopes, account, **kwargs):
        self.calls.append(("acquire_token_silent_with_error", list(scopes), {"account": account, **kwargs}))
        return dict(self.silent) if self.silent is not None else None

    def remove_account(self, account: dict) -> None:
        self.calls.append(("remove_account", None, {"account": account}))
        self.accounts = [kept for kept in self.accounts if kept["home_account_id"] != account["home_account_id"]]
        if self.token_cache is not None:
            for cached in list(self.token_cache.search(msal.TokenCache.CredentialType.ACCOUNT)):
                if cached.get("username") == account["username"]:
                    self.token_cache.remove_account(cached)

    def bind(self, token_cache) -> "FakeApp":
        """Keep the accounts in the token cache too, from now on (see the class docstring)."""
        self.token_cache = token_cache
        for account in self.accounts:
            token_cache.add(sign_in_event(account["username"]))
        return self

    def _sign_in(self) -> dict:
        username = (self.result.get("id_token_claims") or {}).get("preferred_username")
        if "access_token" in self.result and username and username not in [account["username"] for account in self.accounts]:
            self.accounts.append(account_entry(username))
            if self.token_cache is not None:
                self.token_cache.add(sign_in_event(username))
        return dict(self.result)


class FakeResponse:
    """A response of Graph as requests gives it: the status code, the headers (in any case), and the body as JSON or as bytes."""

    def __init__(self, status_code: int = 200, json_data=None, content: bytes = b"", headers: dict | None = None):
        self.status_code, self._json, self.headers = status_code, json_data, CaseInsensitiveDict(headers or {})
        self.content = json.dumps(json_data).encode() if json_data is not None else content

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        if self._json is None:
            raise ValueError("the response holds no JSON")
        return self._json


Answer = FakeResponse | Callable[[dict], FakeResponse]


class FakeSession:
    """A requests-like session for Graph. `answers` maps (method, URL) to the responses for it, given in turn and the last one again
    when they run out; a response may be a function of the request, which it is given as recorded. A URL is compared with its
    %-escapes decoded, so that a test writes it as Graph reads it, except a URL given with a %-escape of its own (a link of Graph's,
    whose state token may hold "%2B"), which must be asked exactly as given. Every request is recorded in `calls` as {"method",
    "url", "headers", "data", "timeout"}. A request without an answer fails the test, so that nothing is asked that the test did not
    foresee."""

    def __init__(self, answers: dict[tuple[str, str], Answer | list[Answer]] | None = None):
        self.answers = {(method, url if "%" in url else unquote(url)): list(given) if isinstance(given, list) else [given]
                        for (method, url), given in (answers or {}).items()}
        self.calls: list[dict] = []

    def get(self, url, headers=None, timeout=None, **kwargs) -> FakeResponse:
        return self._answer("GET", url, headers, None, timeout)

    def post(self, url, headers=None, data=None, timeout=None, **kwargs) -> FakeResponse:
        return self._answer("POST", url, headers, data, timeout)

    def _answer(self, method: str, url: str, headers, data, timeout) -> FakeResponse:
        request = {"method": method, "url": url, "headers": dict(headers or {}), "data": data, "timeout": timeout}
        self.calls.append(request)
        queue = self.answers.get((method, url)) or self.answers.get((method, unquote(url)))
        if queue is None:
            raise AssertionError(f"the fake session has no answer for {method} {unquote(url)}")
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        return answer(request) if callable(answer) else answer


# --- Microsoft 365 Copilot ---

COPILOT = "https://graph.microsoft.com/beta/copilot/conversations"
CONVERSATION = "made-up-conversation-0001"          # the id of the conversation the fake Copilot makes
RESPONSE_MESSAGE = "#microsoft.graph.copilotConversationResponseMessage"


def copilot_answer(text: str | None, sent: str, conversation: str = CONVERSATION, state: str = "active") -> FakeResponse:
    """The answer of the Copilot Chat API to a chat, in the shape of the examples of Microsoft's chat page: the conversation in its
    state, with two messages of the type copilotConversationResponseMessage, the first recapping the message that was sent, the
    second holding the answer's text; without a text (None), the recap alone."""
    messages = [{"@odata.type": RESPONSE_MESSAGE, "id": "made-up-recap-message", "text": sent, "attributions": []}]
    if text is not None:
        messages.append({"@odata.type": RESPONSE_MESSAGE, "id": "made-up-response-message", "text": text, "attributions": []})
    return FakeResponse(200, json_data={"id": conversation, "state": state, "turnCount": 1, "messages": messages})


def sent_text(request: dict) -> str:
    """The text of the message of a chat request as recorded by FakeSession."""
    return json.loads(request["data"])["message"]["text"]


def copilot_session(*answers: str | Answer, created: Answer | list[Answer] | None = None,
                    conversation: str = CONVERSATION) -> FakeSession:
    """Graph for the Copilot client: creating a conversation gives the conversation `conversation` (or the responses `created`, in
    turn), and each chat in it gives the `answers` in turn, the last one again when they run out; a text is the text of the answer
    after the recap of the message sent (copilot_answer), anything else is given as it is."""
    def answering(text: str) -> Callable[[dict], FakeResponse]:
        return lambda request: copilot_answer(text, sent_text(request), conversation)
    made = created if created is not None else FakeResponse(201, json_data={"id": conversation, "state": "active", "turnCount": 0})
    chats = [answering(answer) if isinstance(answer, str) else answer for answer in answers]
    return FakeSession({("POST", COPILOT): made} | ({("POST", f"{COPILOT}/{conversation}/chat"): chats} if chats else {}))
