"""Tests of the LLM client (aid/readers/llm_client.py): the request digest, the recorded replay and the request the Anthropic
client sends. The SDK is replaced by a stub, so no test calls the network."""
import base64
import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from aid.readers.llm_client import AnthropicClient, LlmRequest, LlmResponse, ReaderError, RecordedClient


def test_recorded_client_replays(tmp_path):
    req = LlmRequest(system="s", user_text="u", schema={})
    (tmp_path / f"{req.digest()}.json").write_text(json.dumps({"text": "{}", "model": "m", "input_tokens": 1, "output_tokens": 1, "seconds": 0.1}))
    assert RecordedClient(tmp_path).complete(req).text == "{}"


def test_recorded_client_miss_without_live_raises(tmp_path):
    with pytest.raises(KeyError): RecordedClient(tmp_path).complete(LlmRequest(system="s", user_text="x", schema={}))


def test_anthropic_client_request_shape(monkeypatch):
    """Amended by the ruling of 29 October: the client streams, with max_tokens 32000."""
    captured = {}
    message = SimpleNamespace(content=[SimpleNamespace(type="text", text="{}")], stop_reason="end_turn", usage=SimpleNamespace(input_tokens=5, output_tokens=2), model="model-x", _request_id="r1")
    class Stream:  # stub of the context manager that anthropic.Anthropic().messages.stream(...) returns
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get_final_message(self): return message
    class M:  # stub of anthropic.Anthropic().messages
        def stream(self, **kw): captured.update(kw); return Stream()
    monkeypatch.setattr("aid.readers.llm_client.anthropic.Anthropic", lambda: SimpleNamespace(messages=M()))
    AnthropicClient("model-x").complete(LlmRequest(system="s", user_text="u", schema={"type": "object"}, pdf=b"%PDF"))
    assert captured["output_config"]["format"]["type"] == "json_schema" and captured["max_tokens"] == 32000 and "thinking" not in captured and "temperature" not in captured
    assert captured["messages"][0]["content"][0]["type"] == "document"


# --- the digest ---

def test_the_digest_covers_the_content_of_the_request_and_nothing_else():
    base = LlmRequest(system="s", user_text="u", schema={"a": 1, "b": 2}, pdf=b"%PDF-1")
    same = LlmRequest(system="s", user_text="u", schema={"b": 2, "a": 1}, pdf=b"%PDF-1", max_tokens=99)   # key order and max_tokens do not count
    assert base.digest() == same.digest() and len(base.digest()) == 64
    changed = [replace(base, system="t"), replace(base, user_text="v"), replace(base, schema={"a": 1}), replace(base, pdf=b"%PDF-2"),
               replace(base, pdf=None), replace(base, pdf=None, images=[b"%PDF-1"]), replace(base, images=[b"ab"]),
               replace(base, images=[b"a", b"b"])]
    assert len({base.digest(), *(request.digest() for request in changed)}) == len(changed) + 1


# --- the recorded replay ---

class Live:
    """A live client that counts its calls."""
    name = "live"

    def __init__(self):
        self.calls = 0

    def complete(self, req):
        self.calls += 1
        return LlmResponse(text='{"a": 1}', model="m", input_tokens=10, output_tokens=3, seconds=1.5, cost_eur=Decimal("0.0001"), request_id="req_1")


def test_recorded_client_records_a_miss_once_and_replays_it(tmp_path):
    live, folder = Live(), tmp_path / "recorded"
    req = LlmRequest(system="s", user_text="u", schema={})
    first = RecordedClient(folder, live=live).complete(req)
    again = RecordedClient(folder, live=live).complete(req)
    assert live.calls == 1 and again == first == live.complete(req) and (folder / f"{req.digest()}.json").exists()
    assert RecordedClient(folder).complete(req) == first and RecordedClient(folder).name == "recorded"


def test_record_writes_the_file_the_replay_reads(tmp_path):
    req = LlmRequest(system="s", user_text="u", schema={})
    response = LlmResponse(text="{}", model="m", input_tokens=0, output_tokens=0, seconds=0.0)
    path = RecordedClient(tmp_path).record(req, response)
    assert path == tmp_path / f"{req.digest()}.json" and RecordedClient(tmp_path).complete(req) == response
    assert json.loads(path.read_text(encoding="utf-8"))["cost_eur"] is None


# --- the Anthropic client ---

def stub(monkeypatch, stream_request_id=None, **reply) -> dict:
    """Put a stub in place of the SDK client; it keeps the arguments of messages.stream and streams `reply` over a plain answer.
    The stream itself has `stream_request_id`, as the SDK's stream reads the request id from the response header."""
    captured = {}
    answer = dict(content=[SimpleNamespace(type="text", text="{}")], stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
                  model="model-x", _request_id="req_1") | reply

    class Stream:
        request_id = stream_request_id
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get_final_message(self): return SimpleNamespace(**answer)

    class Messages:
        def stream(self, **kw):
            captured.update(kw)
            return Stream()
    monkeypatch.setattr("aid.readers.llm_client.anthropic.Anthropic", lambda: SimpleNamespace(messages=Messages()))
    return captured


def test_anthropic_client_sends_one_user_message_and_reports_the_answer(monkeypatch):
    captured = stub(monkeypatch, content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text='{"a": 1}')])
    client = AnthropicClient("model-x", effort="low", price_per_mtok=(Decimal("4"), Decimal("20")))
    response = client.complete(LlmRequest(system="the prompt", user_text="read", schema={"type": "object"}, images=[b"one", b"two"], max_tokens=1234))
    assert set(captured) == {"model", "max_tokens", "system", "messages", "output_config"}
    assert (captured["model"], captured["max_tokens"], captured["system"]) == ("model-x", 1234, "the prompt")
    assert captured["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}}, "effort": "low"}
    [message] = captured["messages"]
    assert message["role"] == "user" and [block["type"] for block in message["content"]] == ["image", "image", "text"]
    assert message["content"][1]["source"] == {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(b"two").decode()}
    assert message["content"][2]["text"] == "read"
    assert (response.text, response.model, response.input_tokens, response.output_tokens, response.request_id) == ('{"a": 1}', "model-x", 1000, 200, "req_1")
    assert response.cost_eur == Decimal("0.008") and response.seconds >= 0 and response.attempts == 1 and client.name == "model-x"


def test_anthropic_client_sends_a_pdf_as_one_document_block(monkeypatch):
    captured = stub(monkeypatch)
    response = AnthropicClient("model-x").complete(LlmRequest(system="s", user_text="u", schema={}, pdf=b"%PDF-1.4 " * 100))
    document, text = captured["messages"][0]["content"]
    assert document == {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                        "data": base64.standard_b64encode(b"%PDF-1.4 " * 100).decode()}}
    assert "\n" not in document["source"]["data"] and text == {"type": "text", "text": "u"}
    assert captured["output_config"]["effort"] == "medium" and captured["max_tokens"] == 32000 and response.cost_eur is None


def test_anthropic_client_takes_the_request_id_from_the_stream_when_the_final_message_has_none(monkeypatch):
    """The SDK builds a streamed final message from its events, without the request id; the stream has it from the response header."""
    stub(monkeypatch, stream_request_id="req_from_stream", _request_id=None)
    assert AnthropicClient("model-x").complete(LlmRequest(system="s", user_text="u", schema={}, pdf=b"%PDF")).request_id == "req_from_stream"


@pytest.mark.parametrize(("reply", "message"), [({"stop_reason": "refusal"}, "refused"), ({"stop_reason": "max_tokens"}, "cut off at max_tokens"),
                                                ({"stop_reason": "pause_turn"}, "stopped with 'pause_turn'"), ({"content": []}, "holds no text")])
def test_anthropic_client_refuses_an_answer_it_cannot_use(monkeypatch, reply, message):
    """A refusal, an answer cut off at max_tokens, any other stop before the end of the turn and an answer without text raise ReaderError."""
    stub(monkeypatch, **reply)
    with pytest.raises(ReaderError, match=message) as raised:
        AnthropicClient("model-x", price_per_mtok=(Decimal("4"), Decimal("20"))).complete(LlmRequest(system="s", user_text="u", schema={}, pdf=b"%PDF"))
    used = raised.value.response                                  # what the failed request cost, for the audit record
    assert raised.value.attempts == 1 and (used.input_tokens, used.output_tokens, used.request_id, used.cost_eur) == (1000, 200, "req_1", Decimal("0.008"))


# --- the token count of the Anthropic client (for --estimate-only) ---

def test_the_client_counts_the_tokens_of_the_request_it_would_send(monkeypatch):
    """count_tokens asks the SDK's messages.count_tokens for the same model, system prompt, message and output config as the
    streamed request, and no max_tokens, which the count does not take. A stub stands for the SDK: no network."""
    sent, made = {}, []
    answer = SimpleNamespace(content=[SimpleNamespace(type="text", text="{}")], stop_reason="end_turn", model="model-x", _request_id="req_1",
                             usage=SimpleNamespace(input_tokens=1000, output_tokens=200))

    class Stream:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get_final_message(self): return answer

    class Messages:
        def stream(self, **kw): sent["stream"] = kw; return Stream()
        def count_tokens(self, **kw): sent["count"] = kw; return SimpleNamespace(input_tokens=4321)

    monkeypatch.setattr("aid.readers.llm_client.anthropic.Anthropic", lambda: made.append(1) or SimpleNamespace(messages=Messages()))
    client = AnthropicClient("model-x", effort="low")
    request = LlmRequest(system="the prompt", user_text="read", schema={"type": "object"}, pdf=b"%PDF-1.4", max_tokens=1234)
    assert client.count_tokens(request) == 4321
    client.complete(request)
    assert set(sent["count"]) == {"model", "system", "messages", "output_config"}
    assert sent["count"] == {name: value for name, value in sent["stream"].items() if name != "max_tokens"}
    assert sent["count"]["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}}, "effort": "low"}
    assert [block["type"] for block in sent["count"]["messages"][0]["content"]] == ["document", "text"] and len(made) == 1     # one SDK client
