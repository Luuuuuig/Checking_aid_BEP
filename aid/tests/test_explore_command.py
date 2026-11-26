"""Tests of the one-document check (aid/readers/explore.py): a made-up document read through a fake client, shown field by field.
No test calls a service or the network."""
import os
from pathlib import Path

import pytest

from aid.readers import explore
from aid.readers.exploration import guarded
from aid.readers.llm_client import LlmResponse, ReaderError
from aid.tests.helpers import answer_from_spec

MADE_UP = Path(__file__).resolve().parents[1] / "madeup" / "out"
SECRET = "sk-not-for-the-output"


class FakeClient:
    """A recording client: it answers MU05 as its spec says, with 1,200 + 300 tokens in 2.5 s, at the price it was given."""

    def __init__(self, name="azure_openai:dep", price=None, error=None):
        self.name, self.price, self.error, self.calls = name, price, error, []

    def complete(self, req):
        self.calls.append(req)
        if self.error:
            raise self.error
        cost = None if self.price is None else (1200 * self.price[0] + 300 * self.price[1]) / 1_000_000
        return LlmResponse(text=answer_from_spec("MU05"), model=self.name, input_tokens=1200, output_tokens=300, seconds=2.5, cost_eur=cost)


def run(capsys, *args, client=None, environ=None):
    client = client or FakeClient()
    seen = {}

    def factory(value, env, price):
        seen["price"] = price
        client.price = price
        return guarded(value, client)

    code = explore.main(list(args), environ if environ is not None else {}, client_factory=factory)
    return code, capsys.readouterr().out, client, seen


def test_a_made_up_confirmation_is_read_and_shown(capsys):
    code, out, client, _ = run(capsys, "--service", "azure_openai", "--file", str(MADE_UP / "MU05_conf.pdf"))
    lines = out.splitlines()
    assert code == 0 and len(client.calls) == 1
    assert lines[0].startswith("azure_openai:dep read MU05_conf.pdf: ")
    assert "fields, 1200 + 300 tokens, 2.5 s" in lines[0]
    assert any(line.startswith("po_reference: ") and "page 1, line" in line for line in lines[1:])
    assert any(line.startswith("line 1 quantity: ") for line in lines[1:])


def test_a_document_that_is_not_made_up_is_refused_without_a_call(capsys, tmp_path):
    other = tmp_path / "real.pdf"
    other.write_bytes((MADE_UP / "MU05_conf.pdf").read_bytes() + b"\n% changed")
    code, out, client, _ = run(capsys, "--service", "foundry", "--file", str(other))
    assert code == 2 and "Copilot could not read this document:" in out and client.calls == []


@pytest.mark.parametrize("service, environ, missing", [
    ("azure_openai", {"AZURE_OPENAI_ENDPOINT": "https://x.openai.azure.com", "AZURE_OPENAI_API_KEY": SECRET}, "AZURE_OPENAI_DEPLOYMENT"),
    ("foundry", {"ANTHROPIC_FOUNDRY_API_KEY": SECRET, "ANTHROPIC_FOUNDRY_RESOURCE": "made-up-resource"}, "AID_FOUNDRY_DEPLOYMENT")])
def test_a_missing_variable_ends_with_2_and_names_it_without_its_value(capsys, service, environ, missing):
    code = explore.main(["--service", service, "--file", str(MADE_UP / "MU05_conf.pdf")], environ)
    out = capsys.readouterr().out
    assert code == 2 and missing in out and SECRET not in out


def test_a_service_failure_ends_with_1_and_its_class(capsys):
    code, out, _, _ = run(capsys, "--service", "azure_openai", "--file", str(MADE_UP / "MU05_conf.pdf"),
                          client=FakeClient(error=TimeoutError(SECRET)))
    assert code == 1 and "TimeoutError" in out and SECRET not in out


def test_the_price_gives_the_cost(capsys):
    code, out, _, seen = run(capsys, "--service", "azure_openai", "--file", str(MADE_UP / "MU05_conf.pdf"), "--price", "2,8")
    assert code == 0 and "€ 0.0048" in out.splitlines()[0] and [str(p) for p in seen["price"]] == ["2", "8"]


def test_a_cart_screenshot_defaults_to_the_cart_kind(capsys):
    code, out, client, _ = run(capsys, "--service", "azure_openai", "--file", str(MADE_UP / "CART02.png"))
    assert len(client.calls) == 1 and client.calls[0].images and not client.calls[0].pdf
    assert code == 0 and out.startswith("azure_openai:dep read CART02.png: ")


def test_a_price_that_is_not_two_numbers_is_refused(capsys):
    with pytest.raises(SystemExit) as stop:
        explore.main(["--service", "foundry", "--file", str(MADE_UP / "MU05_conf.pdf"), "--price", "cheap"], {})
    assert stop.value.code == 2


STRICT_TOOK = "Schema: sent strict, and the deployment took it."
STRICT_REFUSED = ("Schema: the deployment refused the strict schema, so it was sent once more without strict; "
                  "the aid's own checks of every value still apply.")
MU05 = str(MADE_UP / "MU05_conf.pdf")


class AzureLikeClient(FakeClient):
    def __init__(self, strict_used, **kwargs):
        super().__init__(**kwargs)
        self.strict_used = strict_used


class FoundryLikeClient(FakeClient):
    def __init__(self, answered_model, **kwargs):
        super().__init__(name="foundry:dep", **kwargs)
        self.answered_model = answered_model


@pytest.mark.parametrize(("strict_used", "line", "other"), [(True, STRICT_TOOK, STRICT_REFUSED), (False, STRICT_REFUSED, STRICT_TOOK)])
def test_the_check_shows_whether_the_deployment_took_the_strict_schema(capsys, strict_used, line, other):
    code, out, _, _ = run(capsys, "--service", "azure_openai", "--file", MU05, client=AzureLikeClient(strict_used))
    assert code == 0 and out.splitlines()[-1] == line and other not in out


def test_the_check_shows_the_model_the_answer_named_and_the_name_the_aid_records(capsys):
    code, out, _, _ = run(capsys, "--service", "foundry", "--file", MU05, client=FoundryLikeClient("claude-x"))
    assert code == 0 and out.splitlines()[-1] == 'The answer named the model "claude-x"; the aid records it as "foundry:dep".'


def test_a_client_without_the_facts_or_with_none_prints_neither_line(capsys):
    for client in (FakeClient(), AzureLikeClient(None), FoundryLikeClient(None)):
        code, out, _, _ = run(capsys, "--service", "azure_openai", "--file", MU05, client=client)
        assert code == 0 and "Schema:" not in out and "The answer named" not in out


def test_a_refusal_and_a_service_failure_print_neither_line(capsys, tmp_path):
    other = tmp_path / "real.pdf"
    other.write_bytes((MADE_UP / "MU05_conf.pdf").read_bytes() + b"\n% changed")
    refused = run(capsys, "--service", "azure_openai", "--file", str(other), client=AzureLikeClient(True))
    failed = run(capsys, "--service", "foundry", "--file", MU05, client=FoundryLikeClient("claude-x", error=TimeoutError("t")))
    assert (refused[0], failed[0]) == (2, 1)
    assert all("Schema:" not in out and "The answer named" not in out for out in (refused[1], failed[1]))


@pytest.mark.parametrize("where", ["missing.pdf", "folder.pdf"])
def test_a_file_that_cannot_be_read_is_a_refusal_before_any_client_is_built(capsys, tmp_path, where):
    path = tmp_path / where                           # a path that does not exist, then a folder named like a PDF
    if where == "folder.pdf":
        path.mkdir()
    built = []
    code = explore.main(["--service", "azure_openai", "--file", str(path)], {}, client_factory=lambda *a: built.append(a))
    error = FileNotFoundError if where == "missing.pdf" else (PermissionError if os.name == "nt" else IsADirectoryError)
    assert code == 2 and built == []
    assert capsys.readouterr().out.strip() == f"the file {path} cannot be read ({error.__name__}), so nothing was sent"


@pytest.mark.parametrize("status, kind", [(404, "AzureNotFound"), (401, "AzureKeyRefused")])
def test_an_azure_openai_failure_ends_with_1_and_the_class_that_names_its_cause(capsys, status, kind):
    """The real Azure OpenAI client behind the real guard, over a fake session that answers with the status: the line names the
    class, which says what failed, and never the service's message or the key."""
    from aid.readers.azure_client import AzureOpenAIClient
    from aid.tests.test_azure_client import FakeResponse, FakeSession
    session = FakeSession(FakeResponse(status, {"error": {"code": "made-up-code", "message": SECRET}}))
    client = AzureOpenAIClient("https://made-up.openai.azure.com", SECRET, "dep", session=session)
    code = explore.main(["--service", "azure_openai", "--file", MU05], {}, client_factory=lambda value, environ, price: guarded(value, client))
    assert (code, capsys.readouterr().out) == (1, f"the exploration reader for Azure OpenAI failed at its service: {kind}\n")
    assert len(session.calls) == 1


@pytest.mark.parametrize("service", ["azure_openai", "foundry"])
def test_the_command_s_own_factory_refuses_a_document_that_is_not_made_up_before_any_call(capsys, tmp_path, monkeypatch, service):
    """explore.main with its own client factory, every variable set to a made-up value: the guard of that factory refuses a made-up
    PDF with one byte more, and neither client sends anything or builds the SDK's client."""
    from aid.readers.azure_client import AzureOpenAIClient
    from aid.readers.exploration import NOT_MADE_UP, SERVICES
    from aid.readers.foundry_client import FoundryClient
    calls = []

    def recorded(*args, **kwargs):
        calls.append(args)
        raise AssertionError("a request was about to go out")

    monkeypatch.setattr(AzureOpenAIClient, "_post", recorded)
    monkeypatch.setattr(FoundryClient, "_client", recorded)
    other = tmp_path / "MU05_conf.pdf"
    other.write_bytes((MADE_UP / "MU05_conf.pdf").read_bytes() + b"\n")
    environ = {"AZURE_OPENAI_ENDPOINT": "https://made-up.openai.azure.com", "AZURE_OPENAI_API_KEY": SECRET, "AZURE_OPENAI_DEPLOYMENT": "dep",
               "ANTHROPIC_FOUNDRY_API_KEY": SECRET, "ANTHROPIC_FOUNDRY_RESOURCE": "made-up-resource", "AID_FOUNDRY_DEPLOYMENT": "dep"}
    code = explore.main(["--service", service, "--file", str(other)], environ)
    assert (code, capsys.readouterr().out) == (2, NOT_MADE_UP.format(service=SERVICES[service]) + "\n") and calls == []


class Unusable(FakeClient):
    """A client whose every answer is not JSON: the reader asks once more, and then gives up with both requests counted."""

    def complete(self, req):
        self.calls.append(req)
        return LlmResponse(text="not json", model=self.name, input_tokens=1200, output_tokens=300, seconds=2.5)


@pytest.mark.parametrize("client, lines", [
    (FakeClient(error=ReaderError("the answer was cut off at max_tokens", LlmResponse(text="", model="azure_openai:dep", input_tokens=5000,
                                                                                    output_tokens=32000, seconds=61.0), attempts=1)),
     ["the answer was cut off at max_tokens", "1 request, 5000 + 32000 tokens, 61.0 s, € 0.266"]),
    (Unusable(), ["MU05_conf.pdf: no usable answer in two attempts:", "2 requests, 2400 + 600 tokens, 5.0 s, € 0.0096"])])
def test_an_answer_that_cannot_be_used_ends_with_2_and_says_what_its_requests_used(capsys, client, lines):
    """An answer that was paid for and cannot be used: its sentence, then the requests, the tokens, the seconds and the cost."""
    code, out, _, _ = run(capsys, "--service", "azure_openai", "--file", MU05, "--price", "2,8", client=client)
    printed = out.splitlines()
    assert code == 2 and len(printed) == 2 and printed[0].startswith(lines[0]) and printed[1] == lines[1]


def test_the_help_says_what_the_check_does_its_exit_codes_and_where_its_settings_come_from(capsys):
    with pytest.raises(SystemExit) as stop:
        explore.main(["--help"], {})
    text = " ".join(capsys.readouterr().out.split())
    assert stop.value.code == 0 and text.count("python -m aid.readers.explore") == 1          # the usage line, not again as the description
    assert "Read one made-up document of the package through Azure OpenAI or Anthropic's models in Microsoft Foundry" in text
    assert "environment variables" in text and "Exit codes: 0 for a reading; 2 for a refusal" in text and "1 for a failure of the service" in text
