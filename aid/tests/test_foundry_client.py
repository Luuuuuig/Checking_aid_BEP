"""The Foundry client (aid/readers/foundry_client.py): the Anthropic client with the SDK's Foundry client behind it. The SDK is
stubbed, or driven over a local transport: no test calls a service."""
import json
import logging
import sys
from types import SimpleNamespace

import pytest

from aid.readers import llm_client
from aid.readers.foundry_client import NO_FOUNDRY_LIBRARY, FoundryClient
from aid.readers.llm_client import AnthropicClient, LlmRequest, ReaderError

REQUEST = LlmRequest(system="made-up system", user_text="made-up text", schema={"type": "object"}, pdf=b"%PDF made-up", max_tokens=1234)
SDK_LOG = "anthropic._base_client"                 # where anthropic 2.3.0 logs the options of each request at DEBUG, headers included


class FakeStream:
    def __init__(self, calls, kwargs):
        calls.append(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"a": 1}')], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5), model="claude-x")


class FakeFoundry:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs, self.stream_calls, self.count_calls = kwargs, [], []
        self.messages = SimpleNamespace(stream=lambda **kw: FakeStream(self.stream_calls, kw), count_tokens=self._count)
        FakeFoundry.instances.append(self)

    def _count(self, **kw):
        self.count_calls.append(kw)
        return SimpleNamespace(input_tokens=42)

    def copy(self, *a, **k):
        raise AssertionError("copy must not be called")

    with_options = with_middleware = copy


class FakeHttpClient:
    """The SDK's DefaultHttpxClient in a stub: it keeps the options it is made with."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture(autouse=True)
def fake_sdk(monkeypatch):
    FakeFoundry.instances = []
    monkeypatch.setattr(llm_client, "anthropic", SimpleNamespace(AnthropicFoundry=FakeFoundry, DefaultHttpxClient=FakeHttpClient))


@pytest.fixture(autouse=True)
def sdk_log():
    """The SDK's request log without the filters that earlier clients left on it, and with its filters put back after the test,
    so that the filter of one test's key does not rewrite the log of another."""
    log = logging.getLogger(SDK_LOG)
    before = list(log.filters)
    log.filters[:] = []
    yield log
    log.filters[:] = before


def test_the_sdk_client_gets_the_key_the_resource_and_an_http_client_that_follows_no_redirect():
    FoundryClient("dep", "k", resource="res").complete(REQUEST)
    kwargs = dict(FakeFoundry.instances[-1].kwargs)
    assert kwargs.pop("http_client").kwargs == {"follow_redirects": False} and kwargs == {"api_key": "k", "resource": "res"}
    url = "https://res.services.ai.azure.com/anthropic"
    FoundryClient("dep", "k", base_url=url).complete(REQUEST)
    kwargs = dict(FakeFoundry.instances[-1].kwargs)
    assert kwargs.pop("http_client").kwargs == {"follow_redirects": False} and kwargs == {"api_key": "k", "base_url": url}


def test_resource_and_base_url_are_one_or_the_other():
    with pytest.raises(ValueError):
        FoundryClient("dep", "k", resource="res", base_url="https://x")
    with pytest.raises(ValueError):
        FoundryClient("dep", "k")


def test_the_request_is_the_stand_ins_with_the_deployment_as_model():
    FoundryClient("dep", "k", resource="res").complete(REQUEST)
    assert FakeFoundry.instances[-1].stream_calls == [{**AnthropicClient("dep")._arguments(REQUEST), "max_tokens": REQUEST.max_tokens}]


def test_the_request_takes_the_effort_given():
    FoundryClient("dep", "k", resource="res", effort="high").complete(REQUEST)
    [call] = FakeFoundry.instances[-1].stream_calls
    assert call == {**AnthropicClient("dep", "high")._arguments(REQUEST), "max_tokens": REQUEST.max_tokens}
    assert call["output_config"]["effort"] == "high"


def test_the_answer_names_the_deployment_not_the_sdks_model():
    client = FoundryClient("dep", "k", resource="res")
    answer = client.complete(REQUEST)
    assert answer.model == "foundry:dep" and client.name == "foundry:dep" and client.model == "dep"
    assert (answer.text, answer.input_tokens, answer.output_tokens) == ('{"a": 1}', 10, 5)


def test_count_tokens_goes_through_the_foundry_client():
    assert FoundryClient("dep", "k", resource="res").count_tokens(REQUEST) == 42


@pytest.mark.parametrize("sdk", [None, SimpleNamespace()])
def test_without_a_foundry_client_in_the_sdk_it_is_a_reader_error(monkeypatch, sdk):
    monkeypatch.setattr(llm_client, "anthropic", sdk)
    with pytest.raises(ReaderError, match="no client for Foundry") as caught:
        FoundryClient("dep", "k", resource="res").complete(REQUEST)
    assert str(caught.value) == NO_FOUNDRY_LIBRARY


def stopped_client(stop_reason, model="claude-x"):
    """A Foundry client whose stub stream ends with `stop_reason` and names `model`."""
    message = SimpleNamespace(content=[SimpleNamespace(type="text", text='{"a": 1}')], stop_reason=stop_reason,
                              usage=SimpleNamespace(input_tokens=10, output_tokens=5), model=model)

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return message

    client = FoundryClient("dep", "k", resource="res")
    client._sdk = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: Stream()))
    return client


def test_answered_model_is_none_until_an_answer_has_come_back():
    assert FoundryClient("dep", "k", resource="res").answered_model is None


def test_answered_model_is_the_model_the_answer_named_while_the_response_names_the_deployment():
    client = FoundryClient("dep", "k", resource="res")
    response = client.complete(REQUEST)
    assert client.answered_model == "claude-x" and response.model == "foundry:dep"


def test_a_cut_off_answer_keeps_the_answered_model_and_names_the_deployment_in_its_response():
    client = stopped_client("max_tokens", model="claude-y")
    with pytest.raises(ReaderError, match="cut off at max_tokens") as error:
        client.complete(REQUEST)
    assert error.value.response.model == "foundry:dep" and client.answered_model == "claude-y"


def test_answered_model_starts_again_with_each_call():
    client = FoundryClient("dep", "k", resource="res")
    client.complete(REQUEST)
    assert client.answered_model == "claude-x"

    def fails(**kwargs):
        raise TimeoutError("made-up")

    client._sdk.messages.stream = fails
    with pytest.raises(TimeoutError):
        client.complete(REQUEST)
    assert client.answered_model is None


KEY = "made-up-foundry-key-for-the-log-test"       # a dummy key, never a real one


def events(*answer):
    """A streamed answer as the service sends it: one server-sent event per (name, data)."""
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in answer).encode()


MADE_UP_ANSWER = events(
    ("message_start", {"type": "message_start", "message": {"id": "msg_made_up", "type": "message", "role": "assistant", "model": "claude-x",
                                                             "content": [], "stop_reason": None, "stop_sequence": None,
                                                             "usage": {"input_tokens": 10, "output_tokens": 1}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": '{"a": 1}'}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 5}}),
    ("message_stop", {"type": "message_stop"}))


def test_the_key_is_in_no_record_and_no_output_of_the_sdks_debug_log(monkeypatch, caplog, capsys):
    """anthropic 2.3.0 logs the options of every request at DEBUG (ANTHROPIC_LOG=debug, or the root logger at DEBUG), and those of
    Foundry hold the key in two headers. The installed AnthropicFoundry is driven here over a local transport that answers a
    made-up message, so nothing leaves the PC; the key reaches the request and no line of the log."""
    import anthropic
    import httpx2                                     # the HTTP package of the SDK, whose client it takes
    for name in ("ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_RESOURCE", "ANTHROPIC_FOUNDRY_BASE_URL", "ANTHROPIC_LOG"):
        monkeypatch.delenv(name, raising=False)      # the SDK reads them from the environment
    sent = []

    def answer(request):
        sent.append(request)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=MADE_UP_ANSWER)

    def local(http_client, **kwargs):              # the client's own http client, in a local one that follows no redirect either
        assert http_client.follow_redirects is False
        return anthropic.AnthropicFoundry(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(answer)))

    monkeypatch.setattr(llm_client, "anthropic", SimpleNamespace(AnthropicFoundry=local, DefaultHttpxClient=anthropic.DefaultHttpxClient))
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="anthropic")
    shown = logging.StreamHandler(sys.stderr)        # the console's lines, as ANTHROPIC_LOG=debug prints them
    logging.getLogger("anthropic").addHandler(shown)
    try:
        response = FoundryClient("dep", KEY, base_url="https://made-up-resource.invalid/anthropic").complete(REQUEST)
    finally:
        logging.getLogger("anthropic").removeHandler(shown)
    assert response.text == '{"a": 1}' and len(sent) == 1 and sent[0].headers["x-api-key"] == KEY
    options = [record.getMessage() for record in caplog.records if record.getMessage().startswith("Request options")]
    assert options and all("<key>" in message for message in options)
    assert all(KEY not in record.getMessage() for record in caplog.records) and KEY not in caplog.text
    printed = capsys.readouterr()
    assert "Request options" in printed.err and KEY not in printed.out + printed.err


def test_the_key_is_hidden_by_one_filter_per_key(sdk_log):
    for key in ("made-up-key-1", "made-up-key-1", "made-up-key-2", ""):
        FoundryClient("dep", key, resource="res")._client()
    keys = [getattr(log_filter, "key", None) for log_filter in sdk_log.filters]
    assert sorted(keys) == ["made-up-key-1", "made-up-key-2"]            # a second client with the same key adds none, no key none



CONFIGURED = "made-up-resource.services.ai.azure.com"       # the host of the made-up resource, never reached: the transport is patched


@pytest.mark.parametrize("call", ["complete", "count_tokens"])
def test_a_redirect_is_not_followed_and_is_a_failure_of_the_service(monkeypatch, call):
    """A service that answers 307 with a Location on another host over plain http gets no second request: the key headers and the
    document go to the configured host only. The redirect is the same kind of error as another failure of the service (here 400):
    the SDK's APIStatusError, which the exploration reader turns into a CopilotError that names the service and the class only;
    neither text holds the key. The installed AnthropicFoundry runs over its own http client, whose transport is patched, so
    nothing leaves the PC."""
    import anthropic
    import httpx2
    from aid.readers import exploration
    from aid.readers.copilot_client import CopilotError
    from aid.tests.test_stand_in import MADE_UP_PDF, request
    for name in ("ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_RESOURCE", "ANTHROPIC_FOUNDRY_BASE_URL", "ANTHROPIC_LOG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm_client, "anthropic", anthropic)
    hosts, status = [], {"code": 307}

    def answer(transport, sent):
        hosts.append(sent.url.host)
        if status["code"] == 307:
            return httpx2.Response(307, headers={"location": "http://elsewhere.invalid/made-up"}, request=sent)
        return httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "made-up"}},
                               request=sent)

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", answer)
    made_up = request(pdf=MADE_UP_PDF.read_bytes())
    failures = {}
    for code in (307, 400):
        status["code"], hosts[:] = code, []
        with pytest.raises(anthropic.APIStatusError) as failed:
            getattr(FoundryClient("dep", KEY, resource="made-up-resource"), call)(REQUEST)
        assert hosts == [CONFIGURED]                 # the configured host got the request, and no other host got any
        assert failed.value.status_code == code and KEY not in str(failed.value)
        reader = exploration.guarded("foundry", FoundryClient("dep", KEY, resource="made-up-resource"))
        with pytest.raises(CopilotError) as said:
            getattr(reader, call)(made_up)
        assert hosts == [CONFIGURED, CONFIGURED] and KEY not in str(said.value)
        failures[code] = str(said.value)
    assert failures[307].startswith("the exploration reader for Anthropic's models in Microsoft Foundry failed at its service: ")
    assert failures[400].startswith("the exploration reader for Anthropic's models in Microsoft Foundry failed at its service: ")
