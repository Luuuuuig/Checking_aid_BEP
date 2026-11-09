"""Tests of the Copilot client (aid/readers/copilot_client.py): Microsoft 365 Copilot behind LlmClient, through the Copilot Chat API
and the work sign-in. The sign-in is a fake msal application and Graph is FakeSession (aid/tests/fakes.py); the answers are made
up, or the perfect reading of a made-up case (aid/tests/helpers.py): no test reaches Microsoft or the network."""
import json
import tempfile

import msal
import pytest
import requests
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from aid.connections.microsoft import COPILOT_SCOPES, GRAPH_TIMEOUT, MAIL_SCOPES, MicrosoftAccount, SignInNeeded
from aid.connections.secrets import SecretStore
from aid.decider import Decision, LlmDecider, decision_request
from aid.readers.copilot_client import CHAT_TIMEOUT, CopilotClient, CopilotError
from aid.readers.llm_client import LlmRequest, LlmResponse, ReaderError
from aid.readers.pdf_text import extract_lines
from aid.readers.supplier_llm import read_supplier_document, reading_request
from aid.schema import DocLine, DocValue, Document
from aid.tests.fakes import (ACCESS_TOKEN, CLIENT_ID, COPILOT, CONVERSATION, RESPONSE_MESSAGE, TENANT_ID, USERNAME, FakeApp, FakeResponse,
                             copilot_answer, copilot_session, sent_text)
from aid.tests.helpers import answer_from_spec

CHAT = f"{COPILOT}/{CONVERSATION}/chat"
ANSWER_AS_JSON = "Answer with one JSON object only, with no other text and no code fence, that follows this JSON schema:"
HEADER = [name for name, field in Document.model_fields.items() if field.annotation is DocValue]
FIELDS = [name for name, field in DocLine.model_fields.items() if field.annotation is DocValue]


def account(tmp_path, app: FakeApp | None = None) -> MicrosoftAccount:
    return MicrosoftAccount(TENANT_ID, CLIENT_ID, SecretStore(tmp_path / "secrets"), app=app or FakeApp(accounts=[USERNAME]))


def client(tmp_path, session, app: FakeApp | None = None, **options) -> tuple[CopilotClient, list[float]]:
    """A Copilot client over the fake sign-in and the session, with the seconds it was told to wait."""
    waits: list[float] = []
    return CopilotClient(account(tmp_path, app), session=session, sleep=waits.append, **options), waits


def body(call: dict) -> dict:
    return json.loads(call["data"])


def fenced(text: str) -> str:
    return f"```json\n{text}\n```"


def refused(status: int, code: str | None = None, headers: dict | None = None) -> FakeResponse:
    """An error answer of Graph, with its code and a message that may never show anywhere."""
    error = {"error": {"code": code, "message": "made-up message of the refusal, with a detail of the tenant"}} if code else None
    return FakeResponse(status, json_data=error, headers=headers)


def values(doc: Document) -> list[DocValue]:
    return [getattr(doc, name) for name in HEADER] + [getattr(line, name) for line in doc.lines for name in FIELDS]


class Unreachable(FakeApp):
    """A sign-in whose renewal of the token cannot reach Microsoft, as msal raises it when no cached token is left."""

    def acquire_token_silent_with_error(self, scopes, account, **kwargs):
        raise requests.ConnectionError("made-up detail: login.microsoftonline.com unreachable")


def chat_answer(messages: list[dict], state: str = "active") -> FakeResponse:
    return FakeResponse(200, json_data={"id": CONVERSATION, "state": state, "messages": messages})


def recap(text: str) -> dict:
    """The response message that recaps the message sent, as Microsoft's examples begin."""
    return {"@odata.type": RESPONSE_MESSAGE, "text": text}


class FakeClient:
    """A client that gives one answer, to read the same answer once without Copilot."""
    name = "fake"

    def __init__(self, text: str):
        self.text = text

    def complete(self, req: LlmRequest) -> LlmResponse:
        return LlmResponse(text=self.text, model="fake", input_tokens=0, output_tokens=0, seconds=0.0)


def test_the_scopes_are_the_seven_delegated_permissions_of_the_chat_api():
    assert COPILOT_SCOPES == ["Sites.Read.All", "Mail.Read", "People.Read.All", "OnlineMeetingTranscript.Read.All", "Chat.Read",
                              "ChannelMessage.Read.All", "ExternalItem.Read.All"]
    assert not {"offline_access", "openid", "profile"} & set(COPILOT_SCOPES) and "Mail.Read" in MAIL_SCOPES


# --- the request ---

def test_a_request_creates_a_conversation_and_sends_one_chat_with_each_page_as_context(tmp_path, madeup_out, monkeypatch):
    """MU04 has two pages: each is one entry of the context, its lines numbered as the reader's sources cite them. The copy of the
    PDF that the text layer is read from is removed again."""
    pdf = madeup_out / "MU04_conf.pdf"
    request = reading_request(pdf)
    session = copilot_session('{"made-up": "answer"}')
    copilot, waits = client(tmp_path, session)
    (temp := tmp_path / "temp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    response = copilot.complete(request)
    assert list(temp.iterdir()) == []
    create, chat = session.calls
    assert (create["method"], create["url"], json.loads(create["data"])) == ("POST", COPILOT, {})
    assert (chat["method"], chat["url"]) == ("POST", CHAT)
    assert (create["timeout"], chat["timeout"]) == (GRAPH_TIMEOUT, CHAT_TIMEOUT)
    for call in (create, chat):
        assert call["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}" and call["headers"]["Content-Type"] == "application/json"
    sent = body(chat)
    schema = json.dumps(request.schema, ensure_ascii=False, separators=(",", ":"))
    assert sent["message"] == {"text": f"{request.system}\n\n{request.user_text}\n\n{ANSWER_AS_JSON}\n{schema}"}
    assert sent["locationHint"] == {"timeZone": "Europe/Amsterdam"}
    assert sent["contextualResources"] == {"webContext": {"isWebEnabled": False}}            # web grounding off
    lines = extract_lines(pdf)
    assert [entry["description"] for entry in sent["additionalContext"]] == [
        f"Document page {page}; each line starts with its page and line number as the sources cite them" for page in (1, 2)]
    for page, entry in enumerate(sent["additionalContext"], 1):
        assert entry["text"].split("\n") == [f"p{line.page} r{line.line}: {line.text}" for line in lines if line.page == page]
    assert entry["text"].startswith("p2 r1: ") and set(entry) == {"text", "description"}
    assert response == LlmResponse(text='{"made-up": "answer"}', model="microsoft-365-copilot", input_tokens=0, output_tokens=0,
                                   seconds=response.seconds, cost_eur=None, request_id=CONVERSATION, attempts=1)
    assert response.seconds >= 0 and waits == [] and CopilotClient.name == copilot.name == "microsoft-365-copilot"


def test_the_token_is_asked_for_the_copilot_scopes_and_shows_nowhere(tmp_path, madeup_out):
    app = FakeApp(accounts=[USERNAME])
    copilot, _ = client(tmp_path, copilot_session("{}"), app=app, time_zone="Europe/Berlin")
    response = copilot.complete(reading_request(madeup_out / "MU01_conf.pdf"))
    assert [call[1] for call in app.calls if call[0] == "acquire_token_silent_with_error"] == [COPILOT_SCOPES]
    assert ACCESS_TOKEN not in repr(response) and ACCESS_TOKEN not in repr(copilot) and ACCESS_TOKEN not in repr(vars(copilot))
    assert body(copilot.session.calls[1])["locationHint"] == {"timeZone": "Europe/Berlin"}


@pytest.mark.parametrize("pdf", ["MU05_conf.pdf", "MU16_conf.pdf"])        # the two scans of the made-up set
def test_a_pdf_without_a_text_layer_raises_reader_error_before_any_call(tmp_path, madeup_out, pdf, monkeypatch):
    session = copilot_session("{}")
    (temp := tmp_path / "temp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    with pytest.raises(ReaderError) as raised:
        client(tmp_path, session)[0].complete(reading_request(madeup_out / pdf))
    assert str(raised.value) == "Copilot reads the text layer only: this document has no text layer"
    assert (raised.value.attempts, raised.value.response, session.calls) == (0, None, []) and list(temp.iterdir()) == []


def two_pages(tmp_path, second: str) -> bytes:
    """A PDF whose page 1 has a line of text and whose page 2 is blank ("blank") or holds only an image, as a scanned page does
    ("scan")."""
    from PIL import Image
    pdf, png = tmp_path / f"{second}.pdf", tmp_path / "scan.png"
    Image.new("RGB", (60, 40), "white").save(png)
    canvas = Canvas(str(pdf), pagesize=A4)
    canvas.drawString(72, 800, "Orderbevestiging 250101")
    canvas.showPage()
    if second == "scan":
        canvas.drawImage(str(png), 72, 400, width=300, height=200)
    canvas.showPage()
    canvas.save()
    return pdf.read_bytes()


def test_a_pdf_with_a_scanned_page_raises_reader_error_before_any_call(tmp_path):
    """Page 1 has text and page 2 is an image without text: the document is not read in part."""
    session = copilot_session("{}")
    with pytest.raises(ReaderError) as raised:
        client(tmp_path, session)[0].complete(LlmRequest(system="s", user_text="u", schema={}, pdf=two_pages(tmp_path, "scan")))
    assert str(raised.value) == "Copilot reads the text layer only: page 2 has no text layer"
    assert (raised.value.attempts, session.calls) == (0, [])


def test_a_blank_page_is_left_out_of_the_context(tmp_path):
    """Page 2 holds neither text nor an image, as a blank trailing page: the document is read from page 1."""
    session = copilot_session("{}")
    client(tmp_path, session)[0].complete(LlmRequest(system="s", user_text="u", schema={}, pdf=two_pages(tmp_path, "blank")))
    assert [entry["description"].split(";")[0] for entry in body(session.calls[1])["additionalContext"]] == ["Document page 1"]


def test_an_images_only_request_raises_reader_error_before_any_call_and_a_cart_screenshot_is_unreadable(tmp_path, madeup_out):
    session = copilot_session("{}")
    copilot, _ = client(tmp_path, session)
    with pytest.raises(ReaderError, match="^Copilot reads the text layer only") as raised:
        copilot.complete(LlmRequest(system="s", user_text="u", schema={}, images=[(madeup_out / "CART02.png").read_bytes()]))
    assert raised.value.attempts == 0 and session.calls == []
    with pytest.raises(ReaderError, match="^Copilot reads the text layer only"):
        read_supplier_document(madeup_out / "CART02.png", copilot, kind="cart")
    assert session.calls == []


def test_the_deciders_request_carries_the_message_only(tmp_path):
    decision = Decision(kind="version", options=["1", "2"], context={"document": {"date": "2026-10-14"}})
    session = copilot_session(fenced('{"choice": "2", "confidence": 0.9, "rationale": "the made-up rationale"}'))
    answer = LlmDecider(client(tmp_path, session)[0]).decide(decision)
    assert (answer.choice, answer.confidence, answer.source, answer.request_id) == ("2", 0.9, "model:microsoft-365-copilot", CONVERSATION)
    assert (answer.input_tokens, answer.output_tokens, answer.cost_eur) == (0, 0, None)
    sent = body(session.calls[1])
    request = decision_request(decision)
    schema = json.dumps(request.schema, ensure_ascii=False, separators=(",", ":"))
    assert sent == {"message": {"text": f"{request.system}\n\n{request.user_text}\n\n{ANSWER_AS_JSON}\n{schema}"},
                    "locationHint": {"timeZone": "Europe/Amsterdam"}, "contextualResources": {"webContext": {"isWebEnabled": False}}}


# --- the answer ---

@pytest.mark.parametrize("text, expected", [
    ('{"a": 1}', '{"a": 1}'),
    ('```json\n{"a": 1}\n```', '{"a": 1}'),
    ('```\n{"a": 1}\n```', '{"a": 1}'),
    ('  ```JSON\n{"a": 1}\n```  \n', '{"a": 1}'),
    ('```\nOK\n```', "OK"),
    (' {"a": 1} \n', ' {"a": 1} \n'),                                  # nothing else is changed
    ('Here it is: ```json\n{"a": 1}\n```', 'Here it is: ```json\n{"a": 1}\n```'),
])
def test_a_code_fence_around_the_answer_is_removed_and_nothing_else_is_changed(tmp_path, text, expected):
    assert client(tmp_path, copilot_session(text))[0].ask("a made-up question").text == expected


def test_the_answer_is_the_text_of_the_last_response_message_after_the_recap(tmp_path):
    """Microsoft's examples answer a chat with two response messages: the first recaps the message sent, the second is the answer."""
    def answered(request):
        return chat_answer([recap(sent_text(request)), {"@odata.type": RESPONSE_MESSAGE, "text": "an earlier answer"},
                            {"@odata.type": RESPONSE_MESSAGE, "text": fenced('{"last": true}')}])
    session = copilot_session(answered)
    response = client(tmp_path, session)[0].ask("a made-up question")
    assert response.text == '{"last": true}' and body(session.calls[1]) == {
        "message": {"text": "a made-up question"}, "locationHint": {"timeZone": "Europe/Amsterdam"},
        "contextualResources": {"webContext": {"isWebEnabled": False}}}
    assert client(tmp_path, copilot_session("OK"))[0].ask(COPILOT_TEST).text == "OK"


COPILOT_TEST = "Answer with the word OK only."


@pytest.mark.parametrize("answer", [
    lambda request: copilot_answer(None, sent_text(request)),                       # the recap alone: the prompt is no answer
    lambda request: chat_answer([recap(sent_text(request)), recap(" " + sent_text(request) + "\n")]),
    lambda request: chat_answer([recap(sent_text(request)), {"@odata.type": RESPONSE_MESSAGE, "text": ""}]),
    lambda request: chat_answer([recap(sent_text(request)), {"@odata.type": RESPONSE_MESSAGE, "text": "  \n"}]),
    lambda request: chat_answer([recap(sent_text(request)), {"@odata.type": RESPONSE_MESSAGE, "text": None}]),
    lambda request: chat_answer([]),
    lambda request: FakeResponse(200, json_data={"id": CONVERSATION, "state": "active"}),
    lambda request: FakeResponse(200, content=b"not JSON"),
])
def test_without_an_answer_after_the_recap_copilot_gave_no_answer(tmp_path, madeup_out, answer):
    with pytest.raises(ReaderError) as raised:
        client(tmp_path, copilot_session(answer))[0].complete(reading_request(madeup_out / "MU01_conf.pdf"))
    assert str(raised.value) == "Copilot gave no answer" and raised.value.attempts == 1
    assert raised.value.response.request_id == CONVERSATION and raised.value.response.text == ""


def test_the_recap_of_the_test_message_is_no_answer_on_the_tab(tmp_path):
    session = copilot_session(lambda request: copilot_answer(None, sent_text(request)))
    with pytest.raises(ReaderError, match="^Copilot gave no answer$"):
        client(tmp_path, session)[0].ask(COPILOT_TEST)


@pytest.mark.parametrize("state, expected", [("disengagedForRai", "disengagedForRai"), ("made up <b>state</b>", "unknown state")])
def test_a_conversation_that_copilot_ended_is_copilot_error(tmp_path, state, expected):
    session = copilot_session(lambda request: copilot_answer("Sorry, I can't chat about this.", sent_text(request), state=state))
    with pytest.raises(CopilotError) as raised:
        client(tmp_path, session)[0].ask("a made-up question")
    assert str(raised.value) == f"Copilot ended the conversation: {expected}" and raised.value.case_only is True
    assert "Sorry" not in str(raised.value) and len(session.calls) == 2


# --- the errors ---

@pytest.mark.parametrize("on_create", [True, False])
def test_a_401_on_either_call_is_sign_in_needed(tmp_path, on_create):
    session = copilot_session(created=refused(401, "InvalidAuthenticationToken")) if on_create else copilot_session(refused(401))
    with pytest.raises(SignInNeeded) as raised:
        client(tmp_path, session)[0].ask("a made-up question")
    assert "Settings tab" in str(raised.value) and "made-up message" not in str(raised.value) and ACCESS_TOKEN not in str(raised.value)
    assert len(session.calls) == (1 if on_create else 2)


def test_a_network_failure_while_the_token_is_renewed_is_copilot_error_and_nothing_is_sent(tmp_path):
    session = copilot_session("OK")
    with pytest.raises(CopilotError) as raised:
        client(tmp_path, session, app=Unreachable(accounts=[USERNAME]))[0].ask("a made-up question")
    assert str(raised.value) == "Copilot could not be reached: ConnectionError" and raised.value.case_only is False
    assert "login.microsoftonline.com" not in str(raised.value) and session.calls == []


@pytest.mark.parametrize("failure, name", [(requests.ConnectionError("made-up detail of the tenant"), "ConnectionError"),
                                           (ValueError("made-up detail of the authority"), "ValueError")])
def test_a_failure_to_make_the_sign_in_is_copilot_error(tmp_path, monkeypatch, failure, name):
    """The msal application is made on first use and asks Microsoft about the tenant: that can fail without the network, or with an
    answer msal cannot use."""
    def refused(*args, **kwargs):
        raise failure
    monkeypatch.setattr(msal, "PublicClientApplication", refused)
    session = copilot_session("OK")
    copilot = CopilotClient(MicrosoftAccount(TENANT_ID, CLIENT_ID, SecretStore(tmp_path / "secrets")), session=session, sleep=lambda s: None)
    with pytest.raises(CopilotError) as raised:
        copilot.ask("a made-up question")
    assert str(raised.value) == f"Copilot could not be reached: {name}" and "made-up" not in str(raised.value) and session.calls == []


def test_without_a_sign_in_nothing_is_sent(tmp_path):
    session = copilot_session("OK")
    with pytest.raises(SignInNeeded):
        client(tmp_path, session, app=FakeApp())[0].ask("a made-up question")
    assert session.calls == []


def test_a_403_is_copilot_error_with_the_status_and_the_code_and_never_the_message(tmp_path):
    session = copilot_session(refused(403, "Forbidden"))
    with pytest.raises(CopilotError) as raised:
        client(tmp_path, session)[0].ask("a made-up question")
    assert str(raised.value) == "Copilot refused: 403 Forbidden" and not isinstance(raised.value, ReaderError)
    assert len(session.calls) == 2                                          # a refusal is not asked again


@pytest.mark.parametrize("answer, expected", [
    (refused(400, "BadRequest"), "Copilot refused: 400 BadRequest"),
    (FakeResponse(403, content=b"<html>made-up message of a proxy</html>"), "Copilot refused: 403 no code"),
    (FakeResponse(404, json_data={"error": {"code": "made-up message with spaces in it", "message": "made-up"}}), "Copilot refused: 404 no code"),
    (FakeResponse(413), "Copilot refused: 413 no code"),
    (FakeResponse(500, json_data={"error": "made-up message"}), "Copilot refused: 500 no code"),
    (FakeResponse(502), "Copilot refused: 502 no code"),
])
def test_another_status_names_the_status_and_a_code_only_and_is_not_asked_again(tmp_path, answer, expected):
    """On the call that creates the conversation, whose body is {}, no refusal concerns the document: none is case_only."""
    session = copilot_session(created=answer)
    copilot, waits = client(tmp_path, session)
    with pytest.raises(CopilotError) as raised:
        copilot.ask("a made-up question")
    assert str(raised.value) == expected and raised.value.case_only is False and raised.value.chat is False
    assert len(session.calls) == 1 and waits == []


@pytest.mark.parametrize("answer, case_only", [(refused(400, "BadRequest"), True), (FakeResponse(413), True), (FakeResponse(500), False),
                                               (refused(403, "Forbidden"), False)])
def test_only_a_400_or_413_of_the_chat_call_concerns_the_document(tmp_path, answer, case_only):
    with pytest.raises(CopilotError) as raised:
        client(tmp_path, copilot_session(answer))[0].ask("a made-up question")
    assert raised.value.case_only is case_only and raised.value.chat is True


def test_a_429_is_asked_again_after_its_retry_after(tmp_path):
    session = copilot_session("OK", created=[refused(429, "TooManyRequests", {"Retry-After": "7"}),
                                             FakeResponse(201, json_data={"id": CONVERSATION})])
    copilot, waits = client(tmp_path, session)
    assert copilot.ask("a made-up question").text == "OK"
    assert waits == [7] and [call["url"] for call in session.calls] == [COPILOT, COPILOT, CHAT]


@pytest.mark.parametrize("headers, wait", [({"Retry-After": "120"}, 30), ({"Retry-After": "0"}, 0), ({}, 5), ({"Retry-After": "soon"}, 5)])
def test_the_wait_is_at_most_30_seconds(tmp_path, headers, wait):
    session = copilot_session(FakeResponse(503, headers=headers), "OK")
    copilot, waits = client(tmp_path, session)
    assert copilot.ask("a made-up question").text == "OK" and waits == [wait]


def test_a_504_twice_is_copilot_error(tmp_path):
    session = copilot_session(FakeResponse(504, headers={"Retry-After": "2"}))
    copilot, waits = client(tmp_path, session)
    with pytest.raises(CopilotError) as raised:
        copilot.ask("a made-up question")
    assert str(raised.value) == "Copilot refused: 504 no code" and waits == [2]
    assert [call["url"] for call in session.calls] == [COPILOT, CHAT, CHAT]


def test_a_connection_error_is_copilot_error_with_its_class(tmp_path):
    def unreachable(request):
        raise requests.ConnectionError(f"made-up detail of {request['url']}")
    with pytest.raises(CopilotError) as raised:
        client(tmp_path, copilot_session(created=unreachable))[0].ask("a made-up question")
    assert str(raised.value) == "Copilot could not be reached: ConnectionError" and raised.value.__cause__ is None
    with pytest.raises(CopilotError, match="^Copilot answered without a conversation id$"):
        client(tmp_path, copilot_session(created=FakeResponse(201, json_data={"turnCount": 0})))[0].ask("a made-up question")


# --- the reader over Copilot ---

def test_the_reader_reads_mu01_from_a_copilot_answer_and_its_values_pass_the_source_check(tmp_path, madeup_out):
    """The answer is the perfect reading of MU01's made-up confirmation, in a code fence as Copilot may give it. Every value cites
    a line of the context that was sent, and every one passes the reader's check against the page's text."""
    pdf = madeup_out / "MU01_conf.pdf"
    session = copilot_session(fenced(answer_from_spec("MU01")))
    doc, response = read_supplier_document(pdf, client(tmp_path, session)[0])
    assert (doc.reader, doc.source_file, doc.pages, len(doc.lines)) == ("llm:microsoft-365-copilot:v1", "MU01_conf.pdf", 1, 3)
    cited = [value for value in values(doc) if value.value is not None]
    assert cited and not any(value.uncertain for value in values(doc))
    context = body(session.calls[1])["additionalContext"][0]["text"].split("\n")
    assert all(f"p{value.source.page} r{value.source.line}: {value.source.text}" in context for value in cited)
    same, _ = read_supplier_document(pdf, FakeClient(answer_from_spec("MU01")))
    assert doc == same.model_copy(update={"reader": "llm:microsoft-365-copilot:v1"})
    assert (response.model, response.request_id, response.attempts, response.cost_eur) == ("microsoft-365-copilot", CONVERSATION, 1, None)


def test_a_value_copilot_takes_from_elsewhere_is_uncertain(tmp_path, madeup_out):
    answer = json.loads(answer_from_spec("MU01"))
    answer["lines"][0]["unit_price"]["text"] = "99,99"                     # not printed on the line it cites
    doc, _ = read_supplier_document(madeup_out / "MU01_conf.pdf", client(tmp_path, copilot_session(json.dumps(answer)))[0])
    assert doc.lines[0].unit_price.uncertain is True and doc.lines[0].quantity.uncertain is False


def test_a_value_cited_on_a_line_that_does_not_print_it_is_uncertain(tmp_path, madeup_out):
    """Line 1's price cited on line 6 of the page (no product row), with that line's text: the source is found, the price is not."""
    answer = json.loads(answer_from_spec("MU01"))
    sixth = next(line for line in extract_lines(madeup_out / "MU01_conf.pdf") if line.line == 6)
    answer["lines"][0]["unit_price"]["source"] = {"page": 1, "line": 6, "text": sixth.text}
    doc, _ = read_supplier_document(madeup_out / "MU01_conf.pdf", client(tmp_path, copilot_session(json.dumps(answer)))[0])
    assert "12,50" not in sixth.text and doc.lines[0].unit_price.uncertain is True and doc.lines[1].unit_price.uncertain is False


def test_an_answer_that_does_not_match_the_schema_is_sent_again_in_a_new_conversation(tmp_path, madeup_out):
    session = copilot_session("I cannot give JSON.", fenced(answer_from_spec("MU01")))
    doc, response = read_supplier_document(madeup_out / "MU01_conf.pdf", client(tmp_path, session)[0])
    assert response.attempts == 2 and len(doc.lines) == 3
    assert [call["url"] for call in session.calls] == [COPILOT, CHAT, COPILOT, CHAT]
    assert "Your previous answer did not match the schema" in body(session.calls[3])["message"]["text"]


def test_copilot_answer_follows_the_shape_of_microsofts_examples():
    messages = copilot_answer("OK", "the message sent").json()["messages"]
    assert [(message["@odata.type"], message["text"]) for message in messages] == [(RESPONSE_MESSAGE, "the message sent"),
                                                                                  (RESPONSE_MESSAGE, "OK")]
    assert copilot_answer("OK", "the message sent").json()["state"] == "active"
