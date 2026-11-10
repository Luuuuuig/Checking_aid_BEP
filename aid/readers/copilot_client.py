"""Microsoft 365 Copilot behind LlmClient (aid/readers/llm_client.py): the AI service of the aid, used with the work sign-in of
aid/connections/microsoft.py through the Copilot Chat API of Microsoft Graph.

What Microsoft's documentation says of the Chat API (read on 8 November 2026: the overview of the Chat API, the pages on creating a
conversation and on a chat, and the Graph permissions reference): it is a preview in Graph's beta endpoint, and Microsoft does not
support the use of these APIs in production applications; it needs a Microsoft 365 Copilot licence on the signed-in account and the
seven delegated permissions of COPILOT_SCOPES together, four of which need an administrator's consent once; it takes text (the
message and additional context) or links to files in OneDrive and SharePoint, and answers with text only; web grounding can be
switched off per message, enterprise grounding cannot; it names no model and states no JSON mode, no limits and no throttling.

The request. complete reads the text layer of the request's PDF with extract_lines (aid/readers/pdf_text.py), from a copy in the
client's work folder that is removed again (the runner's <cases_root>/tmp/work; from memory without one, never from the system's
temporary folder), and sends it as additional context: one entry per page, each line written "p<page> r<line>: <text>" with the
page and the line as the reader's sources count them, so that the reader's check of every value against the page
(aid/readers/supplier_llm.py, "Checking the sources") works as for any model, and a value Copilot takes from elsewhere is uncertain. A
request with images, or with a PDF without text lines (a scan), raises ReaderError "Copilot reads the text layer only: this document
has no text layer" before any call, and a PDF with a page that holds an image and no text lines (a scanned page) raises "Copilot reads
the text layer only: page <n> has no text layer", since a document is never read in part; a page with neither text nor an image (a
blank page) is left out of the context. The case fails as unreadable, as a case does whose document the reader cannot read, and the
digest lists it for the buyer. A request without a PDF and without images, the decider's, sends the message only. The message is the
system prompt, a blank line, the user text, a blank line, ANSWER_AS_JSON and, on the next line, the request's schema as compact JSON.
Every chat has web grounding switched off and the client's time zone as its location hint. The request's max_tokens is not sent: the
API takes no such limit.

The calls. Each request creates a conversation (POST .../beta/copilot/conversations with {}, answered 201 with the conversation's
id) and sends one chat in it (POST .../conversations/<id>/chat, answered 200 with the conversation and its messages), waiting at
most GRAPH_TIMEOUT seconds for the conversation and CHAT_TIMEOUT for the chat, which answers with the whole text at once. The
token is account.token(COPILOT_SCOPES), sent as "Authorization: Bearer" only: it is never logged, kept or raised. Microsoft's
examples answer a chat with two messages whose @odata.type is RESPONSE_MESSAGE, the first recapping the message sent and the second
the response. The answer is the text of the last such message that is not the message sent (but for white space), with a code
fence around it removed (```json ... ``` or ``` ... ```); nothing else of it is changed. A chat without such a message, or whose
message holds no text, raises ReaderError "Copilot gave no answer". A conversation whose state is not "active" (Copilot ended it,
for example "disengagedForRai") is CopilotError "Copilot ended the conversation: <state>", whatever text came with it. The
LlmResponse names the client (name) as its model, counts 0 tokens and no cost (the licence covers the use), and its request id is
the conversation's id, by which the audit record names the call.

The errors. A sign-in that is needed again (SignInNeeded of the account) and a 401 on either call raise SignInNeeded: sign in for
Copilot again on the settings tab. Any other failure to get the token, Microsoft that cannot be reached to renew it or to make the
sign-in's application (MicrosoftUnreachable) or an answer msal cannot use, is CopilotError "Copilot could not be reached: <the class
of the error>", before anything is sent. A 429, 503 or 504 is sent again once, after the seconds of its Retry-After header (at most
MAX_WAIT, and DEFAULT_WAIT when the header gives no number of seconds), waited through `sleep`. A second such answer, and any other
status, is CopilotError "Copilot refused: <status> <Graph's error code or 'no code'>", and a request that fails (no connection, a
timeout) is CopilotError "Copilot could not be reached: <the class of the error>". Neither the message nor the body of Graph's answer
is ever part of an error. A CopilotError is a failure of the service, not an unreadable document, so it is no ReaderError: the runner
(aid/runner.py) leaves the case without a result, so that a later pass runs it again. An ended conversation, and a 400 or a 413 of the
chat, refuse the document and not Copilot as a whole (case_only), so the runner goes on with its other cases; a 400 or 413 of the call
that creates the conversation, whose body is {}, concerns no document. Every failure of the chat is marked so (chat): only such a
failure can be the document's own, while a failure to get the token or to create the conversation is Copilot's as a whole.

ask sends one message as it is, without context and without the instruction to answer in JSON: the test of Copilot on the settings
tab of the review page uses it.
"""
import io
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

import pdfplumber
import requests

from aid.case import work_copy_folder
from aid.connections.microsoft import COPILOT_SCOPES, GRAPH_TIMEOUT, MicrosoftAccount, MicrosoftUnreachable, SignInNeeded
from aid.readers.llm_client import LlmRequest, LlmResponse, ReaderError
from aid.readers.pdf_text import TextLine, extract_lines

CONVERSATIONS = "https://graph.microsoft.com/beta/copilot/conversations"
RESPONSE_MESSAGE = "#microsoft.graph.copilotConversationResponseMessage"
ANSWER_AS_JSON = "Answer with one JSON object only, with no other text and no code fence, that follows this JSON schema:"
PAGE = "Document page {page}; each line starts with its page and line number as the sources cite them"
NO_TEXT_LAYER = "Copilot reads the text layer only: this document has no text layer"
NO_TEXT_ON_PAGE = "Copilot reads the text layer only: page {page} has no text layer"
UNREACHABLE = "Copilot could not be reached: {error}"
ENDED = "Copilot ended the conversation: {state}"
ACTIVE = "active"                                 # the state of a conversation Copilot goes on with
NO_ANSWER = "Copilot gave no answer"
NOT_ACCEPTED = "Copilot did not accept the sign-in: sign in for Copilot on the Settings tab of the review page"
NO_CODE = "no code"
RETRIED = (429, 503, 504)                         # the statuses that are sent again once
OF_THE_REQUEST = (400, 413)                       # the statuses of a chat that refuse the document, not the service
MAX_WAIT = 30                                     # seconds: the longest wait before a request is sent again
DEFAULT_WAIT = 5                                  # seconds: the wait when the answer gives no Retry-After in seconds
CHAT_TIMEOUT = 300                                # seconds per chat: Copilot answers with the whole text at once
_FENCE = re.compile(r"\s*```(?:json\b)?\s*(.*?)\s*```\s*", re.DOTALL | re.IGNORECASE)
_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}")       # an error code of Graph, such as "Forbidden"


class CopilotError(Exception):
    """Copilot refused a request or could not be reached; its text is the status and Graph's error code, the class of the error, or
    the state of an ended conversation, only. `chat` is True for a failure of the chat that carries the document (its refusals, its
    other statuses, a connection that failed during it, an ended conversation): only such a failure can be the document's own, while
    a failure to get the token or to create the conversation never is. `case_only` is True when the refusal concerns this request's
    document and not Copilot as a whole: a conversation Copilot ended, or a 400 or 413 (OF_THE_REQUEST) of the chat; the runner then
    goes on with its other cases."""

    def __init__(self, message: str, case_only: bool = False, chat: bool = False):
        super().__init__(message)
        self.case_only, self.chat = case_only, chat or case_only


class CopilotClient:
    """Microsoft 365 Copilot through the Chat API with the account's sign-in (see the module docstring), an LlmClient. `session` is
    the requests session that Graph is called through (None: requests itself), and `sleep` waits the seconds before a request is
    sent again. `work` is the folder for the copy of a PDF while its text layer is read: the runner gives <cases_root>/tmp/work, so
    that the copy lies under the cases and never in the system's temporary folder; without one the PDF is read from memory."""
    name = "microsoft-365-copilot"

    def __init__(self, account: MicrosoftAccount, time_zone: str = "Europe/Amsterdam", session=None,
                 sleep: Callable[[float], None] = time.sleep, work: Path | None = None):
        self.account, self.time_zone, self.session, self.sleep, self.work = account, time_zone, session, sleep, work

    def complete(self, req: LlmRequest) -> LlmResponse:
        """Send the request as one chat in a new conversation, its PDF's text layer as the context, and return the answer. Raises
        ReaderError for a request with images or a PDF without a text layer (before any call) and for a chat without an answer,
        SignInNeeded and CopilotError as the module docstring says."""
        context = _page_context(req, self.work)
        return self.ask(_message(req), context)

    def ask(self, text: str, context: list[dict] | None = None) -> LlmResponse:
        """Send the text as the message of one chat in a new conversation, with the additional context when one is given, and return
        the answer (see the module docstring)."""
        authorization = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
        started = time.monotonic()
        conversation = _json(self._post(CONVERSATIONS, authorization, {}, GRAPH_TIMEOUT, chat=False)).get("id")
        if not isinstance(conversation, str) or not conversation:
            raise CopilotError("Copilot answered without a conversation id")
        chat = {"message": {"text": text}} | ({"additionalContext": context} if context else {}) | {
            "locationHint": {"timeZone": self.time_zone}, "contextualResources": {"webContext": {"isWebEnabled": False}}}
        answered = _json(self._post(f"{CONVERSATIONS}/{quote(conversation, safe='')}/chat", authorization, chat, CHAT_TIMEOUT, chat=True))
        state = answered.get("state", ACTIVE)
        if state != ACTIVE:
            shown = state if isinstance(state, str) and _CODE.fullmatch(state) else "unknown state"
            raise CopilotError(ENDED.format(state=shown), case_only=True, chat=True)
        answer = _answer(answered, text)
        response = LlmResponse(text="" if answer is None else _unfenced(answer), model=self.name, input_tokens=0, output_tokens=0,
                               seconds=time.monotonic() - started, cost_eur=None, request_id=conversation)
        if answer is None:
            raise ReaderError(NO_ANSWER, response, attempts=1)
        return response

    def _token(self) -> str:
        """The account's token for COPILOT_SCOPES. A sign-in that is needed again stays SignInNeeded; any other failure to get the
        token (Microsoft cannot be reached to renew it or to make the sign-in's application, or msal cannot use its answer) is
        CopilotError with the class of the error, so that the case is run again later."""
        try:
            return self.account.token(COPILOT_SCOPES)
        except SignInNeeded:
            raise
        except MicrosoftUnreachable as error:            # its text is the class of the error of the network
            raise CopilotError(UNREACHABLE.format(error=error)) from None
        except Exception as error:
            raise CopilotError(UNREACHABLE.format(error=type(error).__name__)) from None

    def _post(self, url: str, headers: dict[str, str], body: dict, timeout: float, chat: bool):
        """POST the body as JSON and return Graph's answer when it is a success; a 429, 503 or 504 is sent again once. Only a refusal
        of the chat (`chat`), which carries the document, can be one of the document's own (case_only); the call that creates the
        conversation sends {}."""
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        for attempt in (1, 2):
            try:
                response = (self.session or requests).post(url, headers=headers, data=data, timeout=timeout)
            except requests.RequestException as error:
                raise CopilotError(UNREACHABLE.format(error=type(error).__name__), chat=chat) from None
            status = response.status_code
            if 200 <= status < 300:
                return response
            if status == 401:
                raise SignInNeeded(NOT_ACCEPTED)
            if status not in RETRIED or attempt == 2:
                raise CopilotError(f"Copilot refused: {status} {_code(response)}", case_only=chat and status in OF_THE_REQUEST, chat=chat)
            self.sleep(_wait(response))


def _page_context(req: LlmRequest, work: Path | None = None) -> list[dict]:
    """The additional context of a request: an entry per page of its PDF's text layer, [] without a PDF. ReaderError for images, for
    a PDF without text lines, and for a PDF with a page that holds an image and no text lines (a scanned page), so that no document
    is read in part; a page with neither (a blank page) is left out."""
    if req.images:
        raise ReaderError(NO_TEXT_LAYER)
    if req.pdf is None:
        return []
    lines, with_images = _text_lines(req.pdf, work)
    if not lines:
        raise ReaderError(NO_TEXT_LAYER)
    pages = sorted({line.page for line in lines})
    if (scanned := next((page for page, image in enumerate(with_images, 1) if image and page not in pages), None)) is not None:
        raise ReaderError(NO_TEXT_ON_PAGE.format(page=scanned))
    return [{"text": "\n".join(f"p{line.page} r{line.line}: {line.text}" for line in lines if line.page == page),
             "description": PAGE.format(page=page)} for page in pages]


def _text_lines(pdf: bytes, work: Path | None = None) -> tuple[list[TextLine], list[bool]]:
    """The text lines of the PDF's bytes and, for each page in order, whether it holds an image: with a work folder read from a copy
    in a folder of its own there (work_copy_folder of aid/case.py), which is removed again; without one read from memory. No copy
    ever lies in the system's temporary folder."""
    if work is None:
        with pdfplumber.open(io.BytesIO(pdf)) as document:
            with_images = [bool(page.images) for page in document.pages]
        return extract_lines(io.BytesIO(pdf)), with_images
    with work_copy_folder(work, "aid-copilot-") as folder:
        path = folder / "document.pdf"
        path.write_bytes(pdf)
        with pdfplumber.open(path) as document:
            with_images = [bool(page.images) for page in document.pages]
        return extract_lines(path), with_images


def _message(req: LlmRequest) -> str:
    """The message of a request: the system prompt, the user text and the instruction to answer in its schema, with the schema."""
    schema = json.dumps(req.schema, ensure_ascii=False, separators=(",", ":"))
    return f"{req.system}\n\n{req.user_text}\n\n{ANSWER_AS_JSON}\n{schema}"


def _json(response) -> dict:
    """The JSON object of an answer of Graph; {} when it holds none."""
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _answer(conversation: dict, sent: str) -> str | None:
    """The text of the last response message of the conversation that is not the recap of the message sent (Microsoft's examples
    answer with a response message that recaps the prompt and then the response), or None when there is none or that message holds
    no text."""
    messages = conversation.get("messages")
    answers = [message for message in messages if isinstance(message, dict) and message.get("@odata.type") == RESPONSE_MESSAGE
               and not _recaps(message.get("text"), sent)] if isinstance(messages, list) else []
    text = answers[-1].get("text") if answers else None
    return text if isinstance(text, str) and text.strip() else None


def _recaps(text, sent: str) -> bool:
    """Whether a message's text is the message sent, but for white space."""
    return isinstance(text, str) and text.split() == sent.split()


def _unfenced(text: str) -> str:
    """The text without a code fence around it; a text that is not fenced as a whole is returned as it is."""
    fenced = _FENCE.fullmatch(text)
    return fenced.group(1) if fenced else text


def _wait(response) -> int:
    """The seconds to wait before the request is sent again: those of the answer's Retry-After, from 0 to MAX_WAIT, else DEFAULT_WAIT."""
    try:
        seconds = int(str(response.headers.get("Retry-After", "")).strip())
    except ValueError:
        return DEFAULT_WAIT
    return min(max(seconds, 0), MAX_WAIT)


def _code(response) -> str:
    """Graph's error code of an answer, when it looks like one; never its message."""
    error = _json(response).get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and _CODE.fullmatch(code) else NO_CODE
