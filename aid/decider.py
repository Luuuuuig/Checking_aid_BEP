"""The decider: the answer to a question that the aid's rules leave open, from a language model or from no one.

Three steps of the aid can meet a question that their rules do not settle: which version of a PO a document answers
(aid/versioning.py), which PO line a line of the document answers, and what kind of document an attachment is. The step
then asks a Decider with a Decision: the kind of question, the options and the context the answer rests on. The answer
chooses one option, or none (choice None), with a confidence from 0 to 1, a rationale and its source, the decider's name.
A step acts on a choice only when its confidence is at least MIN_CONFIDENCE; any other answer leaves the question to the
buyer.

- NoDecider chooses nothing. It is the aid without a model, so every open question goes to review.
- LlmDecider asks the model behind an LlmClient, once and without a retry. The system prompt is
  aid/readers/prompts/decide.md, whose first line names its version and is sent with it. The user text gives the kind,
  the options and the context as indented JSON. The answer is structured output in a strict schema: a choice from the
  options or "none", a number for the confidence and a string for the rationale. An unusable answer (not JSON, not in the
  schema, a choice outside the options, a confidence outside 0 to 1) is no choice, with confidence 0 and what was wrong as
  the rationale, and so is a ReaderError from the client (the model refused, or its answer was cut off): an open question
  is a valid outcome, so neither raises. The client's other errors, such as no connection, are not caught, because an
  unreachable service is not an answer. Each answer carries what its call took (tokens, seconds, cost, request id) for
  the audit record and the cost per case, an unusable or refused one too; they are None when no response came.
"""
import json
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ValidationError

from aid.readers.llm_client import LlmClient, LlmRequest, LlmResponse, ReaderError
from aid.schema import make_strict

MIN_CONFIDENCE = 0.8
NONE = "none"                                    # the choice of no option
PROMPT = Path(__file__).resolve().parent / "readers" / "prompts" / "decide.md"
USER_TEXT = "The question: {kind}\nThe options: {options}\n\nThe context:\n{context}"


class Decision(BaseModel):
    kind: Literal["version", "line_match", "document_type"]
    options: list[str]
    context: dict


class DecisionAnswer(BaseModel):
    choice: str | None                           # one of the options; None when no option is chosen
    confidence: float                            # from 0 to 1
    rationale: str
    source: str                                  # the decider's name: "none", or "model:<model id>"
    # What the call to the model took, from its response; None when no model was asked or no response came.
    input_tokens: int | None = None
    output_tokens: int | None = None
    seconds: float | None = None
    cost_eur: Decimal | None = None
    request_id: str | None = None


class Decider(Protocol):
    name: str

    def decide(self, d: Decision) -> DecisionAnswer: ...


class NoDecider:
    """Chooses nothing: every answer is choice None with confidence 0.0."""
    name = "none"

    def decide(self, d: Decision) -> DecisionAnswer:
        return DecisionAnswer(choice=None, confidence=0.0, rationale="no decider", source=self.name)


class Answer(BaseModel):
    """The answer to the question: one of the options or "none", how sure you are, and why."""
    choice: str
    confidence: float
    rationale: str


def decision_request(d: Decision) -> LlmRequest:
    """The request that puts the decision to the model: the prompt, the decision as the user text, and the schema of
    Answer made strict, its choice limited to the options and "none"."""
    schema = Answer.model_json_schema()
    schema["properties"]["choice"]["enum"] = [*d.options, NONE]
    make_strict(schema)
    user_text = USER_TEXT.format(kind=d.kind, options=json.dumps(d.options, ensure_ascii=False),
                                 context=json.dumps(d.context, indent=2, ensure_ascii=False, default=str))
    return LlmRequest(system=PROMPT.read_text(encoding="utf-8").strip(), user_text=user_text, schema=schema)


class LlmDecider:
    """The model behind `client` (see the module docstring). Its name is "model:<client name>"; the source of each answer
    names the model the answer came from."""

    def __init__(self, client: LlmClient):
        self.client = client
        self.name = f"model:{client.name}"

    def decide(self, d: Decision) -> DecisionAnswer:
        try:
            response = self.client.complete(decision_request(d))
        except ReaderError as error:
            return DecisionAnswer(choice=None, confidence=0.0, rationale=f"no usable answer: {error}", **self._call(error.response))
        call = self._call(response)
        answer, problem = _read(response.text, d.options)
        if answer is None:
            return DecisionAnswer(choice=None, confidence=0.0, rationale=problem, **call)
        return DecisionAnswer(choice=None if answer.choice == NONE else answer.choice, confidence=answer.confidence,
                              rationale=answer.rationale, **call)

    def _call(self, response: LlmResponse | None) -> dict:
        """The source of an answer and what its call took: from the response, or the decider's own name when none came."""
        if response is None:
            return {"source": self.name}
        return {"source": f"model:{response.model}", "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
                "seconds": response.seconds, "cost_eur": response.cost_eur, "request_id": response.request_id}


def _read(text: str, options: list[str]) -> tuple[Answer | None, str | None]:
    """The model's answer, or None and what is wrong with it."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return None, f"the answer is not JSON: {error}"
    try:
        answer = Answer.model_validate(data)
    except ValidationError as error:
        first = error.errors(include_url=False)[0]
        return None, f"the answer does not match the schema: {'.'.join(map(str, first['loc'])) or 'the answer'}: {first['msg']}"
    if answer.choice != NONE and answer.choice not in options:
        return None, f"the choice {answer.choice!r} is not one of the options {options} or {NONE!r}"
    if not 0 <= answer.confidence <= 1:
        return None, f"the confidence {answer.confidence} is not between 0 and 1"
    return answer, None
