"""The client of the language model: one request with a document in, one answer out.

A request holds a system prompt, a user text, the JSON schema the answer must follow and the document, as the bytes of a
PDF or of PNG images. A client sends it and returns the text of the answer with its token counts, time and cost. The client
knows nothing of what the document is or what the schema means: aid/readers/supplier_llm.py builds the request and reads
the answer.

- AnthropicClient asks the model through the Anthropic SDK, which reads its key from ANTHROPIC_API_KEY in the environment
  (in Colab, from Colab Secrets). It streams the answer, because the model's thinking counts toward max_tokens and a long
  answer then needs a limit the SDK allows only on a stream. It never logs or keeps the document. It also counts the input tokens
  of a request without running the model (count_tokens), which is how the bake-off estimates its cost before it spends any.
  The SDK is the development client's own library: the aid from a folder (aid/portable.py) is built without it, so this module
  imports without it, with the name anthropic None, and the client raises ReaderError when it is used.
- RecordedClient replays recorded answers, one JSON file per request named by the request's digest, so that no test calls
  the model. Given a live client, it sends a request it has no answer for and records the answer.
"""
import base64
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Protocol

try:
    import anthropic
except ImportError:                              # the aid from a folder has no development client; the name stays for the tests
    anthropic = None


@dataclass
class LlmRequest:
    system: str
    user_text: str
    schema: dict
    pdf: bytes | None = None
    images: list[bytes] = field(default_factory=list)      # PNG images, one block each
    max_tokens: int = 32000                                 # thinking included: a 32-line answer with its sources comes near 16000

    def digest(self) -> str:
        """The sha256, in hex, of the system prompt, the user text, the schema, the PDF and the images. Two requests have the
        same digest when they send the same content; the order of the schema's keys and max_tokens do not count. Each part is
        hashed with its name and length, so that no two different requests run together into the same bytes."""
        schema = json.dumps(self.schema, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        parts = [(b"system", self.system.encode()), (b"user_text", self.user_text.encode()), (b"schema", schema.encode())]
        if self.pdf is not None:
            parts.append((b"pdf", self.pdf))
        parts += [(b"image", image) for image in self.images]
        sha = hashlib.sha256()
        for name, data in parts:
            sha.update(name + b"\0" + len(data).to_bytes(8, "big") + data)
        return sha.hexdigest()


@dataclass
class LlmResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    seconds: float
    cost_eur: Decimal | None = None
    request_id: str | None = None
    attempts: int = 1


class ReaderError(Exception):
    """An answer that cannot be used: the model refused, its answer was cut off at max_tokens or held no text, or (raised by
    aid/readers/supplier_llm.py, which also exports it) the answer was unusable twice.

    `response` is the last answer received, with the tokens, seconds, cost and request id an audit record logs (after a
    retry, of both requests together), or None when no answer came. `attempts` is the number of requests sent."""

    def __init__(self, message: str, response: LlmResponse | None = None, attempts: int = 0):
        super().__init__(message)
        self.response = response
        self.attempts = attempts


class LlmClient(Protocol):
    name: str

    def complete(self, req: LlmRequest) -> LlmResponse: ...


class RecordedClient:
    """Replays the answers recorded in `dir`: the answer to a request is the file <digest>.json, which holds the fields of
    LlmResponse (cost_eur as a string). Without an answer, the request raises KeyError, or, when a live client is given,
    goes to the live client and its answer is recorded."""
    name: str = "recorded"

    def __init__(self, dir: Path, live: LlmClient | None = None):
        self.dir = Path(dir)
        self.live = live

    def complete(self, req: LlmRequest) -> LlmResponse:
        path = self.dir / f"{req.digest()}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            cost = data.pop("cost_eur", None)
            return LlmResponse(**data, cost_eur=None if cost is None else Decimal(cost))
        if self.live is None:
            raise KeyError(f"no recorded answer {path.name} in {self.dir}")
        response = self.live.complete(req)
        self.record(req, response)
        return response

    def record(self, req: LlmRequest, response: LlmResponse) -> Path:
        """Write `response` as the answer to `req` and return its file."""
        data = asdict(response) | {"cost_eur": None if response.cost_eur is None else str(response.cost_eur)}
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{req.digest()}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path


_STOPPED = {"refusal": "the model refused to answer", "max_tokens": "the answer was cut off at max_tokens"}


class AnthropicClient:
    """The model behind the Anthropic SDK, with structured output in the request's schema. `name` is the model id, so that a
    Document read with this client names its model. `price_per_mtok` is the price in euro of a million input tokens and of
    a million output tokens; without it the cost of an answer is None. The SDK's own errors (no connection, a rate limit)
    are not caught here: an unreachable service is not an unreadable document."""

    def __init__(self, model: str, effort: str = "medium", price_per_mtok: tuple[Decimal, Decimal] | None = None):
        self.name = model
        self.model = model
        self.effort = effort
        self.price_per_mtok = price_per_mtok
        self._sdk = None

    def complete(self, req: LlmRequest) -> LlmResponse:
        """Send the request as one user message, the PDF as a document block or each image as an image block and then the
        text, and wait for the whole streamed answer. The seconds of the answer run from sending the request to the end of the
        stream, not from building the SDK client and the request. Raises ReaderError, carrying what the request cost, when the
        model refuses, when the answer is cut off at max_tokens, or when it holds no text."""
        client, arguments = self._client(), self._arguments(req)           # building the SDK client and the request is not the model's time
        started = time.monotonic()
        with client.messages.stream(max_tokens=req.max_tokens, **arguments) as stream:
            message = stream.get_final_message()
            # The SDK builds the final message of a stream from its events, without the request id; the stream has it.
            request_id = getattr(message, "_request_id", None) or getattr(stream, "request_id", None)
        seconds = time.monotonic() - started
        text = next((block.text for block in message.content if block.type == "text"), None)
        usage = message.usage
        response = LlmResponse(text=text or "", model=message.model, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                               seconds=seconds, cost_eur=self._cost(usage.input_tokens, usage.output_tokens), request_id=request_id)
        if message.stop_reason != "end_turn":
            raise ReaderError(_STOPPED.get(message.stop_reason, f"the answer stopped with {message.stop_reason!r}"), response, attempts=1)
        if text is None:
            raise ReaderError("the answer holds no text", response, attempts=1)
        return response

    def count_tokens(self, req: LlmRequest) -> int:
        """The number of input tokens the service counts for the request, without running the model: the same model, system prompt,
        message and output config as complete sends, so that the estimate is of the request that will be paid for. The answer's
        tokens are not part of it."""
        return self._client().messages.count_tokens(**self._arguments(req)).input_tokens

    def _client(self):
        if self._sdk is None:
            if anthropic is None:
                raise ReaderError("the anthropic package is not installed: this Python cannot use the development client")
            self._sdk = anthropic.Anthropic()
        return self._sdk

    def _arguments(self, req: LlmRequest) -> dict:
        """What messages.stream and messages.count_tokens both take of the request: the model, the system prompt, the one user
        message (the PDF or each image, then the text) and the output config with the schema and the effort."""
        content = [*_document_blocks(req), {"type": "text", "text": req.user_text}]
        return dict(model=self.model, system=req.system, messages=[{"role": "user", "content": content}],
                    output_config={"format": {"type": "json_schema", "schema": req.schema}, "effort": self.effort})

    def _cost(self, input_tokens: int, output_tokens: int) -> Decimal | None:
        if self.price_per_mtok is None:
            return None
        price_in, price_out = (Decimal(price) for price in self.price_per_mtok)
        return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def _document_blocks(req: LlmRequest) -> list[dict]:
    """The document of the request as content blocks: the PDF, then the images, each in base64 without line breaks."""
    blocks = []
    if req.pdf is not None:
        blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": _base64(req.pdf)}})
    blocks += [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _base64(image)}} for image in req.images]
    return blocks


def _base64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")
