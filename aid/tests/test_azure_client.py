"""Tests of the Azure OpenAI client: a fake session stands in for the service, so that no test makes a call."""
import logging
from decimal import Decimal
from types import SimpleNamespace

import pytest
import requests

from aid.readers import azure_client
from aid.readers.azure_client import NO_COUNT, AzureOpenAIClient, AzureServiceError, responses_body, responses_url
from aid.readers.llm_client import LlmRequest, ReaderError

ENDPOINT = "https://res.openai.azure.com"
GOOD = {"status": "completed",
        "output": [{"type": "reasoning"}, {"type": "message", "content": [{"type": "output_text", "text": "{\"a\": 1}"}]}],
        "usage": {"input_tokens": 1200, "output_tokens": 300}}


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None, allow_redirects=True):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout, "allow_redirects": allow_redirects})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def request(**kwargs):
    return LlmRequest(system="sys", user_text="read this", schema={"type": "object"}, **kwargs)


def client(*answers, key="k", price=None):
    session = FakeSession(*answers)
    return AzureOpenAIClient(ENDPOINT, key, "dep", price_per_mtok=price, session=session), session


def test_the_url_is_the_v1_responses_route_of_the_endpoint():
    for e in ("https://res.openai.azure.com", "https://res.openai.azure.com/", " https://res.openai.azure.com/openai/v1 \n",
              "https://res.openai.azure.com/OpenAI/V1/"):
        url = responses_url(e)
        assert url == "https://res.openai.azure.com/openai/v1/responses"
        assert "api-version" not in url


def test_the_body_sends_the_pdf_as_a_data_url_with_its_file_name():
    req = request(pdf=b"%PDF-1.7 x")
    body = responses_body(req, "dep")
    assert body["model"] == "dep" and body["store"] is False and body["max_output_tokens"] == req.max_tokens
    assert body["text"]["format"] == {"type": "json_schema", "name": "reading", "schema": req.schema, "strict": True}
    assert body["input"][0] == {"role": "system", "content": [{"type": "input_text", "text": req.system}]}
    content = body["input"][1]["content"]
    assert content[0]["type"] == "input_file" and content[0]["filename"] == "document.pdf"
    assert content[0]["file_data"].startswith("data:application/pdf;base64,")
    assert content[-1] == {"type": "input_text", "text": req.user_text}
    assert responses_body(req, "dep", strict=False)["text"]["format"]["strict"] is False


def test_images_go_as_input_image_with_detail_high():
    content = responses_body(request(images=[b"one", b"two"]), "dep")["input"][1]["content"]
    assert [item["type"] for item in content] == ["input_image", "input_image", "input_text"]
    assert content[0] == {"type": "input_image", "image_url": "data:image/png;base64,b25l", "detail": "high"}
    assert content[1]["image_url"] == "data:image/png;base64,dHdv"


def test_the_key_goes_in_the_api_key_header_only():
    req = request(pdf=b"%PDF-1.7 made-up")
    c, session = client(FakeResponse(200, GOOD))
    c.complete(req)
    assert session.calls[0]["url"] == "https://res.openai.azure.com/openai/v1/responses" and session.calls[0]["json"] == responses_body(req, "dep")
    assert session.calls[0]["headers"] == {"api-key": "k", "Content-Type": "application/json"}
    assert session.calls[0]["allow_redirects"] is False                # the key never follows a redirect to another host
    assert session.calls[0]["timeout"] == (10, 600)            # the connection, then the answer, which is not streamed


def test_the_key_is_in_no_log_and_no_exception_text(caplog):
    caplog.set_level(logging.DEBUG)
    for answer in (FakeResponse(401, {"error": {"code": "x", "message": "k-secret"}}),
                   FakeResponse(400, {"error": {"code": "y", "message": "k-secret"}}),
                   requests.Timeout("k-secret")):
        c, _ = client(answer, key="k-secret")
        with pytest.raises(AzureServiceError) as error:
            c.complete(request())
        assert "k-secret" not in str(error.value)
    assert all("k-secret" not in record.getMessage() for record in caplog.records)


def test_a_completed_answer_gives_its_text_tokens_request_id_and_cost(monkeypatch):
    clock = iter([10.0, 12.5])                                         # the request is sent at 10.0 s and answered at 12.5 s
    monkeypatch.setattr(azure_client, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    c, _ = client(FakeResponse(200, GOOD, {"apim-request-id": "r-1"}), price=(Decimal("2"), Decimal("8")))
    answer = c.complete(request())
    assert answer.text == "{\"a\": 1}"
    assert (answer.input_tokens, answer.output_tokens) == (1200, 300)
    assert answer.request_id == "r-1" and answer.model == "azure_openai:dep" == c.name
    assert answer.cost_eur == Decimal("0.0048")
    assert answer.seconds == 2.5


@pytest.mark.parametrize("output", [[], [{"type": "reasoning"}]])
def test_an_answer_without_text_is_a_reader_error(output):
    c, _ = client(FakeResponse(200, GOOD | {"output": output}))
    with pytest.raises(ReaderError, match="the answer holds no text"):
        c.complete(request())


def test_an_incomplete_answer_at_the_output_limit_is_the_same_error_as_max_tokens():
    body = GOOD | {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
    c, _ = client(FakeResponse(200, body))
    with pytest.raises(ReaderError, match="cut off at max_tokens") as error:
        c.complete(request())
    assert error.value.response.output_tokens == 300


def test_a_refusal_is_the_same_error_as_a_refusal():
    body = GOOD | {"output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]}
    c, _ = client(FakeResponse(200, body))
    with pytest.raises(ReaderError, match="the model refused to answer"):
        c.complete(request())


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
@pytest.mark.parametrize("body", [{"error": {"code": "x", "message": "MSG-TEXT"}}, {"code": "x", "message": "MSG-TEXT"}])
def test_http_failures_are_service_errors_with_their_status(status, body):
    c, _ = client(FakeResponse(status, body))
    with pytest.raises(AzureServiceError) as error:
        c.complete(request())
    assert error.value.status_code == status and error.value.code == "x"
    assert str(status) in str(error.value) and "x" in str(error.value) and "MSG-TEXT" not in str(error.value)


@pytest.mark.parametrize("status, body, kind", [
    (401, {"error": {"code": "x"}}, "AzureKeyRefused"), (403, {"error": {"code": "x"}}, "AzureKeyRefused"),
    (404, {"error": {"code": "x"}}, "AzureNotFound"), (429, {"error": {"code": "x"}}, "AzureLimit"),
    (400, {"error": {"code": "x", "message": "m"}}, "AzureRequestRefused"), (409, {"error": {"code": "x"}}, "AzureRequestRefused"),
    (413, {"error": {"code": "x"}}, "AzureRequestRefused"),
    (400, {"error": {"code": "invalid_json_schema", "message": "bad"}}, "AzureRequestRefused"),            # the schema refused twice
    (500, {"error": {"code": "x"}}, "AzureServerError"), (502, {"error": {"code": "x"}}, "AzureServerError"),
    (503, {"error": {"code": "x"}}, "AzureServerError")])
def test_each_failed_status_is_a_service_error_named_by_its_cause(status, body, kind):
    """The guard shows the class of a failure only, so the class says what failed; it keeps the status and the code."""
    c, _ = client(FakeResponse(status, body))
    with pytest.raises(AzureServiceError) as error:
        c.complete(request())
    assert type(error.value).__name__ == kind and (error.value.status_code, error.value.code) == (status, body["error"]["code"])


@pytest.mark.parametrize("failure, kind, text", [
    (requests.ConnectionError("c"), "AzureNoConnection", "Azure OpenAI could not be reached: ConnectionError"),
    (requests.exceptions.SSLError("s"), "AzureNoConnection", "Azure OpenAI could not be reached: SSLError"),
    (requests.exceptions.InvalidURL("u"), "AzureNoConnection", "Azure OpenAI could not be reached: InvalidURL"),
    (requests.ReadTimeout("r"), "AzureTimeout", "Azure OpenAI did not answer within 600 seconds"),
    (requests.Timeout("t"), "AzureTimeout", "Azure OpenAI did not answer within 600 seconds"),
    (requests.ConnectTimeout("c"), "AzureTimeout", "Azure OpenAI did not answer within 10 seconds")])     # a ConnectionError too
def test_a_connection_failure_or_timeout_is_a_service_error_without_status_named_by_its_cause(failure, kind, text):
    c, _ = client(failure)
    with pytest.raises(AzureServiceError) as error:
        c.complete(request())
    assert type(error.value).__name__ == kind and str(error.value) == text
    assert error.value.status_code is None and error.value.code is None


def test_the_text_of_a_failure_leaves_out_an_error_code_the_service_did_not_give():
    for body in ({"error": {"code": None, "message": "m"}}, {"error": {"message": "m"}}, None):
        c, _ = client(FakeResponse(404, body))
        with pytest.raises(AzureServiceError) as error:
            c.complete(request())
        assert str(error.value) == "Azure OpenAI answered with status 404" and error.value.code is None
    c, _ = client(FakeResponse(404, {"error": {"code": "DeploymentNotFound", "message": "m"}}))
    with pytest.raises(AzureServiceError) as error:
        c.complete(request())
    assert str(error.value) == "Azure OpenAI answered with status 404, error code DeploymentNotFound"


@pytest.mark.parametrize("error", [{"error": {"code": "invalid_json_schema", "message": "bad"}},
                                   {"error": {"code": "x", "message": "The Schema is not supported"}}])
def test_a_refused_schema_is_sent_once_more_without_strict(error):
    c, session = client(FakeResponse(400, error), FakeResponse(200, GOOD))
    assert c.complete(request()).text == "{\"a\": 1}"
    assert [call["json"]["text"]["format"]["strict"] for call in session.calls] == [True, False]
    assert session.calls[0]["json"] | {"text": None} == session.calls[1]["json"] | {"text": None}


def test_a_refused_schema_twice_is_a_service_error_after_two_calls():
    c, session = client(FakeResponse(400, {"error": {"code": "invalid_json_schema", "message": "m"}}))
    with pytest.raises(AzureServiceError) as error:
        c.complete(request())
    assert error.value.status_code == 400 and len(session.calls) == 2
    assert c.strict_used is None


def test_strict_used_stays_none_when_the_request_sent_again_without_strict_fails():
    c, session = client(FakeResponse(400, SCHEMA_REFUSED), FakeResponse(500, {"error": {"code": "x"}}))
    with pytest.raises(AzureServiceError):
        c.complete(request())
    assert len(session.calls) == 2 and c.strict_used is None


def test_a_400_about_something_else_is_not_retried():
    c, session = client(FakeResponse(400, {"error": {"code": "invalid_image", "message": "m"}}))
    with pytest.raises(AzureServiceError):
        c.complete(request())
    assert len(session.calls) == 1


def test_count_tokens_says_the_service_gives_no_count():
    c, session = client(FakeResponse(200, GOOD))
    with pytest.raises(ReaderError, match=NO_COUNT):
        c.count_tokens(request())
    assert session.calls == []


SCHEMA_REFUSED = {"error": {"code": "invalid_json_schema", "message": "bad"}}


def test_strict_used_is_none_until_an_answer_has_come_back():
    c, _ = client(FakeResponse(200, GOOD))
    assert c.strict_used is None
    c.complete(request())
    assert c.strict_used is True


def test_strict_used_is_false_after_the_answer_to_the_request_sent_again_without_strict():
    c, _ = client(FakeResponse(400, SCHEMA_REFUSED), FakeResponse(200, GOOD))
    c.complete(request())
    assert c.strict_used is False


def test_strict_used_is_false_when_the_answer_to_the_second_request_is_cut_off():
    body = GOOD | {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
    c, _ = client(FakeResponse(400, SCHEMA_REFUSED), FakeResponse(200, body))
    with pytest.raises(ReaderError, match="cut off at max_tokens"):
        c.complete(request())
    assert c.strict_used is False


def test_strict_used_stays_none_after_a_service_failure_and_starts_again_with_each_call():
    c, _ = client(FakeResponse(200, GOOD), FakeResponse(500, {"error": {"code": "x"}}))
    c.complete(request())
    assert c.strict_used is True
    with pytest.raises(AzureServiceError):
        c.complete(request())
    assert c.strict_used is None


def test_the_request_id_falls_back_to_x_request_id():
    c, _ = client(FakeResponse(200, GOOD, {"x-request-id": "x-1"}))
    assert c.complete(request()).request_id == "x-1"


@pytest.mark.parametrize("reason, text", [("content_filter", "the model refused to answer"),
                                          ("made_up_reason", "the answer stopped with 'made_up_reason'")])
def test_an_answer_incomplete_for_another_reason_is_a_reader_error_that_names_it(reason, text):
    c, _ = client(FakeResponse(200, GOOD | {"status": "incomplete", "incomplete_details": {"reason": reason}}))
    with pytest.raises(ReaderError) as error:
        c.complete(request())
    assert str(error.value) == text and error.value.response.output_tokens == 300


def test_only_a_400_is_sent_again_without_strict():
    c, session = client(FakeResponse(500, {"error": {"code": "x", "message": "the schema store failed"}}))
    with pytest.raises(AzureServiceError):
        c.complete(request())
    assert len(session.calls) == 1


@pytest.mark.parametrize("status, body, kind", [(200, None, "AzureServiceError"), (502, None, "AzureServerError"),
                                                (500, [1], "AzureServerError")])
def test_a_body_that_is_not_a_json_object_is_a_service_error_with_its_status_and_no_code(status, body, kind):
    """A gateway's page of HTML, for example, after one request."""
    c, session = client(FakeResponse(status, body))
    with pytest.raises(AzureServiceError) as error:
        c.complete(request())
    assert type(error.value).__name__ == kind and (error.value.status_code, error.value.code) == (status, None) and len(session.calls) == 1


def test_a_redirect_is_not_followed_and_is_a_service_error_with_its_status():
    c, session = client(FakeResponse(307, None, {"Location": "https://other.example/collect"}))
    with pytest.raises(AzureServiceError) as error:
        c.complete(request())
    assert type(error.value) is AzureServiceError and error.value.status_code == 307
    assert len(session.calls) == 1 and session.calls[0]["allow_redirects"] is False


@pytest.mark.parametrize("body", [[1], "made-up text", 0])
def test_a_200_whose_json_is_not_an_object_is_a_service_error(body):
    c, _ = client(FakeResponse(200, body))
    with pytest.raises(AzureServiceError) as error:
        c.complete(request())
    assert type(error.value) is AzureServiceError
    assert (str(error.value), error.value.status_code) == ("Azure OpenAI answered with a body that is not an object", 200)


@pytest.mark.parametrize("status, error, code", [("failed", {"code": "server_error", "message": "MSG-TEXT"}, "server_error"),
                                                 ("cancelled", None, None)])
def test_a_200_whose_response_did_not_complete_is_a_service_error_with_the_code_of_its_error(status, error, code):
    """A response that failed at the service waits as a failure of the service: it is not counted against the document."""
    c, _ = client(FakeResponse(200, GOOD | {"status": status, "error": error}))
    with pytest.raises(AzureServiceError) as failed:
        c.complete(request())
    assert type(failed.value) is AzureServiceError and (failed.value.status_code, failed.value.code) == (200, code)
    assert "MSG-TEXT" not in str(failed.value)


def test_output_items_and_content_parts_that_are_not_objects_are_skipped():
    message = {"type": "message", "content": ["made-up", None, {"type": "output_text", "text": "{\"a\": 1}"}]}
    c, _ = client(FakeResponse(200, GOOD | {"output": ["made-up", 3, None, message]}))
    assert c.complete(request()).text == "{\"a\": 1}"


def test_token_counts_that_are_null_count_as_0():
    c, _ = client(FakeResponse(200, GOOD | {"usage": {"input_tokens": None, "output_tokens": None}}), price=(Decimal("2"), Decimal("8")))
    answer = c.complete(request())
    assert (answer.input_tokens, answer.output_tokens, answer.cost_eur) == (0, 0, 0)
