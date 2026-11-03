"""The LLM reader: a supplier's order confirmation, or a captured cart page, read by a language model into the Document of
aid.schema.

The model sees the document itself: the PDF (a scan as well as a PDF with a text layer) or a PNG screenshot. It answers in
the reading schema (reading_schema()): for each header field and each field of each line the text as printed, its source
(page, line and the text of that line) and whether it was hard to read, and the language of the document. It does not
normalise. This reader works out each value from its text with the normalisers of aid.schema, so that a value means the
same whichever reader produced it:

    quantity, unit_price, net_price, discount_pct   normalize_number, with the decimal comma of nl and de or the decimal point of en
    price_basis                                     normalize_basis, on the text of the basis alone
    unit                                            normalize_unit
    delivery_date, document_date, capture_date      normalize_date, a week in the year of the document's date
    currency                                        normalize_currency
    item_code, description, po_reference,           the text without the spaces around it
    version_hint, supplier, incoterm

The language is the one given to read_supplier_document, or else the one the model reports. A blank text is no value. A
value is uncertain when the model marks it, when its text cannot be read as a value, or when it has no source.

Checking the sources. On a PDF with a text layer, a value is also uncertain unless both of these hold:
- the text its source quotes is on the page the source names (find_source: case, runs of white space and the way an accented
  letter is composed do not matter);
- the value's own text, with runs of white space as one space and its case kept, stands in the line the source names, or
  in that line joined by spaces with the line before and the line after it (a value wrapped over two lines).
So a certain value on such a PDF is printed, as the model gives it, on the line it cites or on the line before or after it.
The check cannot tell whether the text was put in the right field (a quantity given as the price of the same line passes),
nor find a value the model left out. A value given without its text (a whole Document as the answer) has nothing to find and
is uncertain there. A scan or an image has no text layer, so nothing is checked: its values are uncertain only by the rules
of the paragraph above.

An answer that does not match the schema, or that holds no lines (every PO line would then read as missing), is sent back
once with what is wrong; a second unusable answer raises ReaderError (defined beside the client, which raises it for a
refused or cut-off answer), and the case goes to review as unreadable. A ReaderError carries the last answer, with the
tokens, seconds, cost and request id of every request of the read, and the number of requests. The reader also takes a
whole Document as the answer, as the tests' fake answers are: each value is then worked out again from its text, and a
value given without a text is kept.

The system prompt is aid/readers/prompts/read_document.md, whose first line names its version. The user text below belongs
to the prompt as well. The Document's reader is "llm:<client name>:<prompt version>".

Recording the answers of a model on the made-up set, once per document (each answer is kept under the digest of its request):

    python3 -m aid.readers.supplier_llm --record aid/tests/fixtures/recorded --set aid/madeup/out --model "<model id>"
"""
import argparse
import re
import unicodedata
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from pydantic.json_schema import SkipJsonSchema

from aid.readers.llm_client import AnthropicClient, LlmClient, LlmRequest, LlmResponse, ReaderError, RecordedClient
from aid.readers.pdf_text import TextLine, extract_lines, find_source, page_count
from aid.schema import (DocLine, DocValue, Document, SourceRef, make_strict, normalize_basis, normalize_currency, normalize_date,
                        normalize_number, normalize_unit)

Kind = Literal["confirmation", "cart"]
Language = Literal["nl", "de", "en"]

PROMPT = Path(__file__).resolve().parent / "prompts" / "read_document.md"
USER_TEXT = {
    "confirmation": "Read this order confirmation from a supplier into the schema.",
    "cart": "Read this page captured from a supplier's webshop cart into the schema. The date the page shows is its capture_date.",
}
RETRY = "Your previous answer did not match the schema: {error}. Answer again."
NO_LINES = "the document holds order lines and none were returned"
DECIMAL_COMMA = {"nl": True, "de": True, "en": False}


# --- the reading schema ---
# The docstrings of these models are the descriptions the model sees in the schema.

class ReadValue(BaseModel):
    """One value: its text as printed, where it stands, and whether it is hard to read."""
    text: str | None                  # as printed; null when the document does not show the value
    source: SourceRef | None          # the page and line it stands on, with the text of that line
    uncertain: bool                   # printed but hard to read
    value: SkipJsonSchema[str | None] = Field(default=None, exclude=True)    # only in a whole Document given as the answer


class ReadLine(BaseModel):
    line_id: str
    item_code: ReadValue
    description: ReadValue
    quantity: ReadValue
    unit: ReadValue
    unit_price: ReadValue
    price_basis: ReadValue
    discount_pct: ReadValue
    net_price: ReadValue
    delivery_date: ReadValue


class Reading(BaseModel):          # keys it does not know, such as those of a whole Document, are ignored
    """A supplier's document as printed: its language, its header values and its lines."""
    language: Language | None = None
    po_reference: ReadValue
    version_hint: ReadValue
    supplier: ReadValue
    document_date: ReadValue
    currency: ReadValue
    incoterm: ReadValue
    capture_date: ReadValue
    lines: list[ReadLine]


def reading_schema() -> dict:
    """The JSON schema of the model's answer: Reading made strict, as aid.schema.document_schema makes Document strict
    (every object closed, every property required, a value that is not printed written as null). It has no `value`, no
    `kind`, `source_file`, `pages` or `reader`. A new dict is returned at each call."""
    schema = Reading.model_json_schema()
    make_strict(schema)
    return schema


# --- the request ---

def reading_request(path: Path, kind: Kind = "confirmation", prompt_version: str = "v1") -> LlmRequest:
    """The request that asks the model to read the document at `path`: the prompt, the user text for `kind`, the reading
    schema and the document, a PDF or a PNG image. Raises ValueError for an unknown kind or prompt version and ReaderError
    for a file that is neither a PDF nor a PNG."""
    path = Path(path)
    if kind not in USER_TEXT:
        raise ValueError(f"the LLM reader reads a confirmation or a cart page, not {kind!r}")
    suffix = path.suffix.lower()
    if suffix not in (".pdf", ".png"):
        raise ReaderError(f"{path.name}: the LLM reader takes a PDF or a PNG image")
    document = {"pdf": path.read_bytes()} if suffix == ".pdf" else {"images": [path.read_bytes()]}
    return LlmRequest(system=_prompt(prompt_version), user_text=USER_TEXT[kind], schema=reading_schema(), **document)


def _prompt(version: str) -> str:
    """The system prompt of `version`, without the first line that names the version."""
    first, _, prompt = PROMPT.read_text(encoding="utf-8").partition("\n")
    found = re.fullmatch(r"<!-- read_document (v\d+) -->", first.strip())
    if found is None or found[1] != version:
        raise ValueError(f"{PROMPT.name} holds prompt version {found[1] if found else 'unknown'}, not {version}")
    return prompt.strip()


# --- the reader ---

def read_supplier_document(path: Path, client: LlmClient, kind: Kind = "confirmation", prompt_version: str = "v1",
                           language: Language | None = None) -> tuple[Document, LlmResponse]:
    """Read the document at `path` with the model behind `client` into a Document of `kind`. Returns the Document and the
    answer; after a retry the answer counts the tokens, seconds and cost of both requests and has attempts=2. Raises
    ReaderError when the model refuses, when its answer is cut off, or when the answer is unusable twice (it does not
    match the schema, or it holds no lines). `language` sets the number convention; without it the language the model
    reports does."""
    path = Path(path)
    request = reading_request(path, kind, prompt_version)
    response = client.complete(request)
    reading, problem = _answer(response.text)
    if problem is not None:
        retry = replace(request, user_text=f"{request.user_text}\n\n{RETRY.format(error=problem)}")
        try:
            second = client.complete(retry)
        except ReaderError as error:                # the retry refused or cut off: count both requests
            raise ReaderError(str(error), _together(response, error.response) if error.response else response, attempts=2) from error
        response = _together(response, second)
        reading, problem = _answer(second.text)
        if problem is not None:
            raise ReaderError(f"{path.name}: no usable answer in two attempts: {problem}", response, attempts=2)
    pdf = path.suffix.lower() == ".pdf"
    document = _document(reading, kind, DECIMAL_COMMA.get(language or reading.language), extract_lines(path) if pdf else [],
                         source_file=path.name, pages=page_count(path) if pdf else 1, reader=f"llm:{client.name}:{prompt_version}")
    return document, response


def _answer(text: str) -> tuple[Reading | None, str | None]:
    """The answer as a Reading, or None and what is wrong with it: it does not match the schema, or it holds no lines."""
    try:
        reading = Reading.model_validate_json(text)
    except ValidationError as error:
        return None, _describe(error)
    return (reading, None) if reading.lines else (None, NO_LINES)


def _describe(error: ValidationError, most: int = 10) -> str:
    """The errors of a validation in one line: where each one is and what is wrong, the first `most` of them."""
    found = [f"{'.'.join(map(str, e['loc'])) or 'the answer'}: {e['msg']}" for e in error.errors(include_url=False)]
    return "; ".join(found[:most]) + (f"; and {len(found) - most} more" if len(found) > most else "")


def _together(first: LlmResponse, second: LlmResponse) -> LlmResponse:
    """The answer of a retry, with the tokens, seconds and cost of both requests."""
    cost = None if first.cost_eur is None or second.cost_eur is None else first.cost_eur + second.cost_eur
    return replace(second, input_tokens=first.input_tokens + second.input_tokens, output_tokens=first.output_tokens + second.output_tokens,
                   seconds=first.seconds + second.seconds, cost_eur=cost, attempts=2)


def _document(reading: Reading, kind: Kind, decimal_comma: bool | None, lines: list[TextLine], **facts) -> Document:
    """The Document of the answer: each value worked out from its text and checked against the text lines of the PDF (none for
    a scan or an image). `facts` are the source_file, pages and reader of the Document."""
    header = {"document_date": _value("document_date", reading.document_date, decimal_comma, None, lines)}
    document_date = _as_date(header["document_date"].value)
    header |= {name: _value(name, getattr(reading, name), decimal_comma, document_date, lines)
               for name in ("po_reference", "version_hint", "supplier", "currency", "incoterm", "capture_date")}
    doc_lines = [DocLine(line_id=line.line_id.strip() or str(number),            # as the prompt numbers lines the document does not number
                         **{name: _value(name, getattr(line, name), decimal_comma, document_date, lines)
                            for name in ReadLine.model_fields if name != "line_id"})
                 for number, line in enumerate(reading.lines, 1)]
    return Document(kind=kind, lines=doc_lines, **header, **facts)


def _value(field: str, read: ReadValue, decimal_comma: bool | None, document_date: date | None, lines: list[TextLine]) -> DocValue:
    """The DocValue of one field (see the module docstring for when a value is uncertain)."""
    if read.text is not None and read.text.strip():
        text, value = read.text, _normalise(field, read.text, decimal_comma, document_date)
    elif read.text is None and read.value is not None:          # a whole Document that gives the value alone
        text, value = None, read.value
    else:
        return DocValue()                                        # not on the document; a blank text is no value either
    source = read.source
    unproven = source is None or bool(lines) and not _stands_where_cited(text, source, lines)
    return DocValue(value=value, text=text, source=source, uncertain=read.uncertain or value is None or unproven)


def _stands_where_cited(text: str | None, source: SourceRef, lines: list[TextLine]) -> bool:
    """Whether a value stands where its source says, on a PDF with text lines (see "Checking the sources" above)."""
    if text is None or find_source(lines, source.page, source.text) is None:
        return False
    page = {line.line: line.text for line in lines if line.page == source.page}
    if source.line not in page:
        return False
    window = " ".join(page[number] for number in (source.line - 1, source.line, source.line + 1) if number in page)
    return _collapsed(text) in _collapsed(window)


def _collapsed(text: str) -> str:
    """The text with runs of white space as one space and accented letters in one composed form; the case is kept."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def _normalise(field: str, text: str, decimal_comma: bool | None, document_date: date | None) -> str | None:
    if field in ("quantity", "unit_price", "net_price", "discount_pct"):
        return normalize_number(text, decimal_comma=decimal_comma)
    if field == "price_basis":
        return normalize_basis(text)
    if field == "unit":
        return normalize_unit(text)
    if field in ("delivery_date", "document_date", "capture_date"):
        return normalize_date(text, document_date)
    if field == "currency":
        return normalize_currency(text)
    return text.strip()                                          # item_code, description, po_reference, version_hint, supplier, incoterm


def _as_date(value: str | None) -> date | None:
    """The document's own date, against which a delivery week without a year is read."""
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:                                           # a range where a date was expected
        return None


# --- recording a model's answers ---

def documents_to_record(folder: Path) -> list[tuple[Path, Kind]]:
    """The documents the --record command reads in a folder laid out as aid/madeup/out: each *_conf.pdf as a confirmation and
    each *.png as a cart page."""
    folder = Path(folder)
    return [(path, "confirmation") for path in sorted(folder.glob("*_conf.pdf"))] + [(path, "cart") for path in sorted(folder.glob("*.png"))]


def main(argv: list[str] | None = None) -> int:
    """python3 -m aid.readers.supplier_llm --record <folder> --set <folder> --model <model> [--effort low|medium|high]: read each
    document of the set once through RecordedClient(folder, live=AnthropicClient(model)). An answer already recorded is
    replayed, not asked again. Prints one line per document, never its content."""
    parser = argparse.ArgumentParser(description="Record a model's answers on the made-up set, once per document.")
    parser.add_argument("--record", type=Path, required=True, help="folder of the recorded answers, e.g. aid/tests/fixtures/recorded")
    parser.add_argument("--set", type=Path, default=Path("aid/madeup/out"), help="folder of the made-up documents")
    parser.add_argument("--model", required=True, metavar="<model id>", help="the id of the model, as the AI service names it")
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high"], help="the model's effort (default medium)")
    args = parser.parse_args(argv)
    client = RecordedClient(args.record, live=AnthropicClient(args.model, effort=args.effort))
    failed = 0
    for path, kind in documents_to_record(args.set):
        try:
            doc, response = read_supplier_document(path, client, kind=kind)
        except ReaderError as error:
            failed += 1
            used = error.response
            print(f"{path.name}: not read after {error.attempts} attempt(s): {error}" + (
                f" (model {used.model}, {used.input_tokens} + {used.output_tokens} tokens, {used.seconds:.1f} s, request {used.request_id})"
                if used else ""))
            continue
        print(f"{path.name}: {len(doc.lines)} lines, {response.attempts} attempt(s), model {response.model}, "
              f"{response.input_tokens} + {response.output_tokens} tokens, {response.seconds:.1f} s")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
