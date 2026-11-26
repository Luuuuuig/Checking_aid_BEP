"""The one-document check: python -m aid.readers.explore --service {azure_openai,foundry} --file PATH [--kind {confirmation,cart}]
[--price IN,OUT]

Reads one made-up document of the package through Azure OpenAI or Anthropic's models in Microsoft Foundry and shows the reading field
by field: the value of each field and the page and line it was found on. The first line gives the number of fields read, the tokens,
the seconds and, with --price (euros per million input tokens and per million output tokens), the cost.

The key and the other settings come from environment variables only (see aid/readers/exploration.py), and the key is never printed.
The document always goes through guarded(): a file that is not a made-up document of the package is refused before anything is sent,
and so is a document of the unseen cases of the evaluation (made_up_extra.sha256), which the aid alone sends, for the evaluation run:
this check prints what it read, and nobody may see a case of the evaluation before the run.

The kind is "cart" for a .png and "confirmation" otherwise, unless --kind says so. The exit code is 0 for a reading, 2 for a refusal
(a document that is not made up, a file that cannot be read, a missing variable, an answer that cannot be used) with its
sentence, and 1 for a failure of the service, with the class of the error. An answer that was paid for and cannot be used has a
second line: its requests, tokens, seconds and, with --price, cost.

After the reading, one line says whether Azure OpenAI took the schema strict, and one says which model a Foundry answer named; a
client without these facts shows neither.
"""
import argparse
import os
from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path

from aid.readers.copilot_client import CopilotError
from aid.readers.exploration import SERVICES, exploration_client, guarded
from aid.readers.llm_client import LlmResponse, ReaderError
from aid.readers.supplier_llm import read_supplier_document
from aid.schema import DocValue

Price = tuple[Decimal, Decimal]
STRICT_TOOK = "Schema: sent strict, and the deployment took it."
STRICT_REFUSED = ("Schema: the deployment refused the strict schema, so it was sent once more without strict; "
                  "the aid's own checks of every value still apply.")
DESCRIPTION = ("Read one made-up document of the package through Azure OpenAI or Anthropic's models in Microsoft Foundry and show the "
               "reading field by field.")
EPILOG = ("The settings of the service, its key among them, come from environment variables only, never from a file (aid/MANUAL.md, "
          "section 3.15). Exit codes: 0 for a reading; 2 for a refusal, with its sentence (a document that is not made up, a file that "
          "cannot be read, a missing variable, an answer that cannot be used); 1 for a failure of the service, with the class of the error.")
ClientFactory = Callable[[str, Mapping[str, str], Price | None], object]


def default_factory(value: str, environ: Mapping[str, str], price: Price | None):
    """The guarded client of the service, with its settings from `environ`."""
    return guarded(value, exploration_client(value, environ, price=price))


def parse_price(text: str) -> Price:
    """A --price value, IN,OUT: two numbers, each zero or more."""
    try:
        price_in, price_out = (Decimal(part.strip()) for part in text.split(","))
    except (ValueError, InvalidOperation):
        raise argparse.ArgumentTypeError(f"{text!r} is not IN,OUT: two numbers, euros per million input and output tokens") from None
    if not (price_in.is_finite() and price_out.is_finite() and price_in >= 0 and price_out >= 0):
        raise argparse.ArgumentTypeError(f"{text!r} is not IN,OUT: two numbers of zero or more")
    return price_in, price_out


def _used(answer: LlmResponse, price: Price | None) -> str:
    """What an answer used: its tokens, its seconds and its cost, the client's own or worked out at the price given (none without)."""
    cost = answer.cost_eur
    if cost is None and price is not None:
        cost = (answer.input_tokens * price[0] + answer.output_tokens * price[1]) / 1_000_000
    return (f"{answer.input_tokens} + {answer.output_tokens} tokens, {answer.seconds:.1f} s"
            + ("" if cost is None else f", € {Decimal(str(cost)):f}"))


def _shown(name: str, value: DocValue) -> str:
    """One line for a field that was read: its value and where it was found."""
    text = f"{name}: {value.value if value.value is not None else value.text}"
    if value.source is not None:
        text += f" (page {value.source.page}, line {value.source.line}: {value.source.text!r})"
    return text + (" [uncertain]" if value.uncertain else "")


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None, client_factory: ClientFactory | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aid.readers.explore", description=DESCRIPTION, epilog=EPILOG)
    parser.add_argument("--service", required=True, choices=sorted(SERVICES))
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--kind", choices=("confirmation", "cart"))
    parser.add_argument("--price", type=parse_price, metavar="IN,OUT", help="euros per million input and output tokens")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    kind = args.kind or ("cart" if args.file.suffix.lower() == ".png" else "confirmation")
    try:
        args.file.read_bytes()                         # a file that cannot be read is refused before a client is built
    except OSError as error:
        print(f"the file {args.file} cannot be read ({type(error).__name__}), so nothing was sent")
        return 2
    try:
        client = (client_factory or default_factory)(args.service, environ, args.price)
        document, answer = read_supplier_document(args.file, client, kind)
    except (ValueError, ReaderError) as error:         # a missing variable, a document that is not made up, an unusable answer
        print(error)
        if isinstance(error, ReaderError) and error.response is not None:      # an answer that was paid for and cannot be used
            print(f"{error.attempts} request{'s' if error.attempts != 1 else ''}, {_used(error.response, args.price)}")
        return 2
    except CopilotError as error:                      # the service failed; its text names the class of the error only
        print(error)
        return 1
    fields = [(name, getattr(document, name)) for name in
              ("po_reference", "version_hint", "supplier", "document_date", "currency", "incoterm", "capture_date")]
    for line in document.lines:
        fields += [(f"line {line.line_id} {name}", value) for name, value in line if isinstance(value, DocValue)]
    fields = [(name, value) for name, value in fields if not value.missing or value.text is not None]
    print(f"{client.name} read {args.file.name}: {len(fields)} fields, {_used(answer, args.price)}")
    for name, value in fields:
        print(_shown(name, value))
    service_client = getattr(client, "client", client)         # inside the guard
    strict_used = getattr(service_client, "strict_used", None)
    if strict_used is not None:
        print(STRICT_TOOK if strict_used else STRICT_REFUSED)
    answered_model = getattr(service_client, "answered_model", None)
    if answered_model is not None:
        print(f'The answer named the model "{answered_model}"; the aid records it as "{client.name}".')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
