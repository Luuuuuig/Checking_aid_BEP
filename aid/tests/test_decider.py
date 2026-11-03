"""Tests of the decider (aid/decider.py). The model is a fake client; no test calls a model."""
import json
import re
from decimal import Decimal

import pytest

from aid.decider import PROMPT, Decision, LlmDecider, NoDecider
from aid.readers.llm_client import LlmResponse, ReaderError

LINE = {"line_id": "2", "item_code": "KM-0302", "quantity": "4", "unit_price": "22.10", "delivery_date": "2026-11-16"}
DECISION = Decision(kind="version", options=["1", "2"], context={
    "document": {"date": "2026-10-14", "lines": [LINE]},
    "versions": [{"version": 1, "date": "2026-10-12", "lines": [LINE]}, {"version": 2, "date": "2026-10-26", "lines": [LINE | {"quantity": "6"}]}]})
KIND = Decision(kind="document_type", options=["confirmation", "invoice"], context={"first_page": "S03 Orderbevestiging Uw inkooporder 250103"})


USAGE = {"input_tokens": 1200, "output_tokens": 85, "seconds": 2.5, "cost_eur": Decimal("0.0123"), "request_id": "req_fake"}


def response(text: str) -> LlmResponse:
    """The fake model's answer, with the tokens, seconds, cost and request id of the call."""
    return LlmResponse(text=text, model="fake-model", **USAGE)


def usage(result) -> dict:
    return {name: getattr(result, name) for name in USAGE}


class FakeClient:
    """Gives the answers it holds, one per request, and keeps the requests. An exception in place of an answer is raised."""
    name = "fake-client"
    def __init__(self, *answers): self.answers = list(answers); self.requests = []
    def complete(self, req):
        self.requests.append(req)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return response(answer)


def answer(choice, confidence, rationale='Line 2 confirms "4 st", the quantity of version 1.') -> str:
    return json.dumps({"choice": choice, "confidence": confidence, "rationale": rationale})


def test_no_decider_never_chooses():
    result = NoDecider().decide(DECISION)
    assert (result.choice, result.confidence, result.source) == (None, 0.0, "none") and set(usage(result).values()) == {None}


@pytest.mark.parametrize("decision", [DECISION, KIND], ids=["version", "document_type"])
def test_one_request_holds_the_prompt_the_decision_and_a_strict_schema(decision):
    client = FakeClient(answer(decision.options[0], 0.9))
    LlmDecider(client).decide(decision)
    [request] = client.requests
    marker = PROMPT.read_text(encoding="utf-8").partition("\n")[0]
    assert re.fullmatch(r"<!-- decide v\d+ -->", marker) and request.system.startswith(marker)
    schema = request.schema
    assert schema["properties"]["choice"]["enum"] == [*decision.options, "none"]
    assert (schema["properties"]["confidence"]["type"], schema["properties"]["rationale"]["type"]) == ("number", "string")
    assert schema["additionalProperties"] is False and schema["required"] == ["choice", "confidence", "rationale"]
    assert decision.kind in request.user_text and json.dumps(decision.options) in request.user_text
    assert json.dumps(decision.context, indent=2) in request.user_text and (request.pdf, request.images) == (None, [])


def test_a_usable_answer_is_the_models_choice():
    decider = LlmDecider(FakeClient(answer("1", 0.9)))
    result = decider.decide(DECISION)
    assert (result.choice, result.confidence, result.rationale) == ("1", 0.9, 'Line 2 confirms "4 st", the quantity of version 1.')
    assert (decider.name, result.source) == ("model:fake-client", "model:fake-model")       # the source names the model that answered
    assert usage(result) == USAGE                                                           # what the call took, for the audit record


def test_none_is_no_choice_with_the_models_confidence():
    result = LlmDecider(FakeClient(answer("none", 0.7, "Both versions order 4 of KM-0302."))).decide(DECISION)
    assert (result.choice, result.confidence, result.rationale, result.source) == (None, 0.7, "Both versions order 4 of KM-0302.", "model:fake-model")


@pytest.mark.parametrize("text,problem", [
    ("not json", "JSON"),
    (answer("3", 0.9), "'3'"),                                                     # a choice outside the options
    (answer("1", 1.5), "1.5"),                                                     # a confidence outside 0 to 1
    (answer("1", -0.1), "-0.1"),
    (json.dumps({"choice": "1", "confidence": 0.9}), "rationale"),                 # a field left out
])
def test_an_unusable_answer_is_no_choice_and_is_not_asked_again(text, problem):
    client = FakeClient(text)
    result = LlmDecider(client).decide(DECISION)
    assert (result.choice, result.confidence, result.source) == (None, 0.0, "model:fake-model") and problem in result.rationale
    assert len(client.requests) == 1 and usage(result) == USAGE                             # the call was made and is counted


def test_a_refused_answer_is_no_choice():
    refused = ReaderError("the model refused to answer", response(""), attempts=1)
    result = LlmDecider(FakeClient(refused)).decide(DECISION)
    assert (result.choice, result.confidence, result.source) == (None, 0.0, "model:fake-model") and "refused" in result.rationale
    assert usage(result) == USAGE                                                           # the refused call is counted as well


def test_an_answer_refused_before_any_response_names_the_decider():
    result = LlmDecider(FakeClient(ReaderError("no answer came"))).decide(DECISION)
    assert (result.choice, result.confidence, result.source) == (None, 0.0, "model:fake-client") and "no answer came" in result.rationale
    assert set(usage(result).values()) == {None}
