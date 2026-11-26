"""The client of Azure OpenAI over its Responses API, for the exploration readers: one request with a document in, one answer out.

It is an LlmClient like AnthropicClient, built over `requests` and not over a vendor library. The route is the v1 route of the
resource, <endpoint>/openai/v1/responses, which takes no api-version. The key goes in the api-key header, and only there: it is
never logged and never in the text of an exception, which holds the status and the service's error code only, not its message
(a message can quote the request). A request follows no redirect, so that the key and the document go to the endpoint only: a
redirect is a failure of the service with its status.

- The PDF goes as a data URL in an input_file item with the file name document.pdf, and each PNG as an input_image item with
  detail high, before the user's text.
- `store` is false, so that the Responses API does not keep the response (a stored response is kept for 30 days). Azure's store
  for abuse monitoring is apart from this. The client is for made-up documents only, and a stored response is still a copy.
- The schema is sent strict. A strict schema can be refused with a 400 that names the schema (no page states its error code, so
  the client looks for the word "schema" in the code or the message). The request is then sent once more with strict false,
  and never more than twice in all.
- No route is documented that counts the input tokens before an answer, so count_tokens raises ReaderError.
- A failure of the service is an AzureServiceError of a class named by its cause (a refused key, a deployment or route not found,
  a limit, another refusal, a failure of the service, no answer in time, no connection), since the guard that wraps the client
  shows the class of an error and never its text. The answer is not streamed, so the client waits 10 seconds for the connection
  and 600 seconds for the answer: a long reading can take minutes.
"""
import time
from decimal import Decimal

import requests

from aid.readers.llm_client import _STOPPED, LlmRequest, LlmResponse, ReaderError, _base64

NO_COUNT = "Azure OpenAI gives no count of the tokens before an answer"


class AzureServiceError(Exception):
    """The service did not answer with an answer: an HTTP failure or no connection. The text holds the status and the service's
    error code, or the class of the connection error, and nothing else. A failure with a known cause has a subclass named by it."""

    def __init__(self, message: str, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class AzureKeyRefused(AzureServiceError):
    """The service refused the key: status 401 or 403."""


class AzureNotFound(AzureServiceError):
    """Status 404: the endpoint has no such deployment or route."""


class AzureLimit(AzureServiceError):
    """Status 429: a limit of the service."""


class AzureRequestRefused(AzureServiceError):
    """Any other status from 400 to 499, a schema refused twice included: the service refused the request."""


class AzureServerError(AzureServiceError):
    """A status from 500 to 599: the service failed."""


class AzureTimeout(AzureServiceError):
    """No answer in time (requests.Timeout)."""


class AzureNoConnection(AzureServiceError):
    """No connection to the endpoint (any other requests.RequestException)."""


BY_STATUS = {401: AzureKeyRefused, 403: AzureKeyRefused, 404: AzureNotFound, 429: AzureLimit}


def failure_class(status: int) -> type[AzureServiceError]:
    """The class of a reply with the status, which is not 200: by its cause from 400 to 599, AzureServiceError for any other."""
    if status in BY_STATUS:
        return BY_STATUS[status]
    if 400 <= status <= 499:
        return AzureRequestRefused
    return AzureServerError if 500 <= status <= 599 else AzureServiceError


def responses_url(endpoint: str) -> str:
    """The Responses route of the resource, for an endpoint with or without a trailing slash or the /openai/v1 path (in any case)."""
    base = endpoint.strip().rstrip("/")
    if base.lower().endswith("/openai/v1"):
        base = base[: -len("/openai/v1")]
    return f"{base}/openai/v1/responses"


def responses_body(req: LlmRequest, deployment: str, strict: bool = True) -> dict:
    """The JSON body of the request: the system prompt, then one user message with the PDF or each image and then the text."""
    content = []
    if req.pdf is not None:
        content.append({"type": "input_file", "filename": "document.pdf", "file_data": f"data:application/pdf;base64,{_base64(req.pdf)}"})
    content += [{"type": "input_image", "image_url": f"data:image/png;base64,{_base64(image)}", "detail": "high"} for image in req.images]
    content.append({"type": "input_text", "text": req.user_text})
    return {"model": deployment, "store": False, "max_output_tokens": req.max_tokens,
            "text": {"format": {"type": "json_schema", "name": "reading", "schema": req.schema, "strict": strict}},
            "input": [{"role": "system", "content": [{"type": "input_text", "text": req.system}]},
                      {"role": "user", "content": content}]}


class AzureOpenAIClient:
    """A deployment of Azure OpenAI with structured output in the request's schema. `name` is azure_openai:<deployment>.
    `price_per_mtok` is the price in euro of a million input tokens and of a million output tokens; without it the cost of an
    answer is None. The key comes from the caller. `timeout` is the seconds to wait for the connection and for the answer. `strict_used`
    says, after a call, which request the service answered: True the one with the schema strict, False the one sent again without
    strict after the schema was refused, None when no answer came back."""

    def __init__(self, endpoint: str, api_key: str, deployment: str, price_per_mtok: tuple[Decimal, Decimal] | None = None,
                 session=None, timeout: tuple[float, float] = (10, 600)):
        self.url = responses_url(endpoint)
        self.deployment = deployment
        self.name = f"azure_openai:{deployment}"
        self.price_per_mtok = price_per_mtok
        self.timeout = timeout
        self._api_key = api_key
        self._session = session if session is not None else requests.Session()
        self.strict_used: bool | None = None

    def complete(self, req: LlmRequest) -> LlmResponse:
        """Send the request and return the answer. Raises AzureServiceError, of the class of its cause, when the service fails or
        cannot be reached, and ReaderError, carrying what the request cost, when the model refuses, the answer is cut off at
        max_tokens or it holds no text."""
        started = time.monotonic()
        self.strict_used = None
        strict = True
        reply = self._post(responses_body(req, self.deployment))
        if reply.status_code == 400 and _is_schema_refusal(*_error_of(reply)):
            strict = False
            reply = self._post(responses_body(req, self.deployment, strict=False))      # one more request, never a third
        seconds = time.monotonic() - started
        if reply.status_code != 200:
            code, _ = _error_of(reply)
            text = f"Azure OpenAI answered with status {reply.status_code}" + (f", error code {code}" if code else "")
            raise failure_class(reply.status_code)(text, reply.status_code, code)
        self.strict_used = strict
        return self._response(reply, seconds)

    def count_tokens(self, req: LlmRequest) -> int:
        raise ReaderError(NO_COUNT)

    def _post(self, body: dict):
        headers = {"api-key": self._api_key, "Content-Type": "application/json"}
        try:
            return self._session.post(self.url, headers=headers, json=body, timeout=self.timeout, allow_redirects=False)
        except requests.Timeout as exc:              # before RequestException: a ConnectTimeout is a ConnectionError as well
            waited = self.timeout[0] if isinstance(exc, requests.ConnectTimeout) else self.timeout[1]
            raise AzureTimeout(f"Azure OpenAI did not answer within {waited:g} seconds") from None
        except requests.RequestException as exc:
            raise AzureNoConnection(f"Azure OpenAI could not be reached: {type(exc).__name__}") from None

    def _response(self, reply, seconds: float) -> LlmResponse:
        try:
            data = reply.json()
        except ValueError:
            raise AzureServiceError("Azure OpenAI answered with a body that is not JSON", reply.status_code) from None
        if not isinstance(data, dict):
            raise AzureServiceError("Azure OpenAI answered with a body that is not an object", reply.status_code)
        if (status := data.get("status")) is not None and status not in ("completed", "incomplete"):    # it failed at the service
            code = data["error"].get("code") if isinstance(data.get("error"), dict) else None
            raise AzureServiceError(f"Azure OpenAI answered with the response status {status!r}" + (f", error code {code}" if code else ""),
                                    reply.status_code, code)
        usage = data.get("usage") or {}
        input_tokens, output_tokens = usage.get("input_tokens") or 0, usage.get("output_tokens") or 0       # a count of null is 0
        items = [part for item in data.get("output") or [] if isinstance(item, dict) and item.get("type") == "message"
                 for part in item.get("content") or [] if isinstance(part, dict)]          # an item or a part that is no object is skipped
        text = next((part.get("text") for part in items if part.get("type") == "output_text"), None)
        request_id = reply.headers.get("apim-request-id") or reply.headers.get("x-request-id")
        response = LlmResponse(text=text or "", model=self.name, input_tokens=input_tokens, output_tokens=output_tokens, seconds=seconds,
                               cost_eur=self._cost(input_tokens, output_tokens), request_id=request_id)
        if data.get("status") == "incomplete":
            reason = (data.get("incomplete_details") or {}).get("reason")
            stopped = {"max_output_tokens": _STOPPED["max_tokens"], "content_filter": _STOPPED["refusal"]}
            raise ReaderError(stopped.get(reason, f"the answer stopped with {reason!r}"), response, attempts=1)
        if any(part.get("type") == "refusal" for part in items):
            raise ReaderError(_STOPPED["refusal"], response, attempts=1)
        if text is None:
            raise ReaderError("the answer holds no text", response, attempts=1)
        return response

    def _cost(self, input_tokens: int, output_tokens: int) -> Decimal | None:
        if self.price_per_mtok is None:
            return None
        price_in, price_out = (Decimal(price) for price in self.price_per_mtok)
        return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def _error_of(reply) -> tuple[str | None, str]:
    """The error code and the message of a failed reply, from {"error": {...}} or from the body itself; None and "" when it has none."""
    try:
        data = reply.json()
    except ValueError:
        return None, ""
    if not isinstance(data, dict):
        return None, ""
    error = data.get("error") if isinstance(data.get("error"), dict) else data
    return error.get("code"), str(error.get("message") or "")


def _is_schema_refusal(code: str | None, message: str) -> bool:
    return "schema" in f"{code or ''} {message}".lower()
