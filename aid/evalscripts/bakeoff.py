"""The model bake-off: each candidate model reads the made-up set, every reading is scored against the specs, and the cheapest model
whose accuracy is close enough to the best is chosen.

    python3 -m aid.evalscripts.bakeoff --models M1 M2 ... [--set aid/madeup/out] [--spec aid/madeup/spec] [--out FILE] [--price M=IN,OUT ...]
                                       [--estimate-only] [--record DIR]

A model is an Anthropic model id, or azure_openai:<deployment> or foundry:<deployment>, the exploration readers of aid/readers/exploration.py
(made-up documents only, their settings in environment variables; no route of Azure OpenAI that counts tokens before an answer is documented,
so --estimate-only says unknown for it). A name with a colon is never an Anthropic model id: one that names no service is a command error.

For each model, in the order given, the command reads each *_conf.pdf of --set as a confirmation and each *.png (CART02.png) as a cart
page with read_supplier_document, scores the reading with score_reading against the spec of the case in --spec (aid/madeup/spec/<case>.json)
and the text lines of the PDF (a screenshot has none, so its sources are not scored), and writes one CSV row per model and document to --out
(default aid/evalscripts/results/bakeoff_<date>.csv, the date of the run). A read that raises ReaderError is a row with the error and no
field right, not the end of the run. The columns, and how the result is read, are in aid/evalscripts/results/README.md.

The summary that follows has, per model, the mean field accuracy over all documents, the mean source accuracy over the documents where it
can be measured, the mean cost per case and the total cost, and then the decision of choose: the cheapest model whose field accuracy and
source accuracy are both within 2 percentage points of the best. Without a price for every model there is no decision, because there is no
cost to compare, and none while a document of an exploration reader was not read (its service failed, or the guard refused it), because its
row would count as a reading of 0.

--price MODEL=IN,OUT gives a model's price in euro per million input tokens and per million output tokens. The prices are read from the
provider's price page on the day of the run and typed here; the command holds none. A price for a model that is not in --models is a
command error: it is nearly always a misspelt name, which would leave the model that does run without a price. Every answer's cost is the
client's own figure or, where the answer carries none (a replay of answers recorded without a price), worked out from its tokens at the
price given now.

--estimate-only counts the input tokens of one request per model (the first document of the set, through the client's count_tokens), prints
them and stops before any answer is asked for, so the cost can be approved first. A count that fails (at the service, or for an
Anthropic model id with a ReaderError such as the library missing) is a line that says so, and the command then ends with 1; "unknown"
is for an exploration service that offers no count.

--record DIR wraps each model's client in RecordedClient(DIR/<model>, with a colon of a service name made an underscore, live=client): every
answer is kept, and a second run with the same DIR replays them without a call. Recorded answers are answers on made-up documents.

main takes the client factory as its second argument (model, price -> client), so that the tests run it with fake clients; the default,
client_for_model, builds AnthropicClient(model, price_per_mtok=price), which reads its key from ANTHROPIC_API_KEY, or for a service name its
guarded exploration reader. A document the service cannot be sent, or a failure of the service, is a row with the error, as a ReaderError is.
"""
import argparse
import csv
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import fmean
from typing import Protocol

from aid.evalscripts.score_reading import ReadingScore, choose, expected_fields, score_reading, score_unread
from aid.readers import exploration
from aid.readers.copilot_client import CopilotError
from aid.readers.llm_client import AnthropicClient, LlmClient, LlmRequest, LlmResponse, ReaderError, RecordedClient
from aid.readers.pdf_text import TextLine, extract_lines
from aid.readers.supplier_llm import Kind, documents_to_record, read_supplier_document, reading_request

Price = tuple[Decimal, Decimal]                    # euro per million input tokens and per million output tokens
RESULTS = Path("aid") / "evalscripts" / "results"
COLUMNS = ["model", "case", "kind", "fields_total", "fields_correct", "field_accuracy", "sources_total", "sources_correct", "source_accuracy",
           "input_tokens", "output_tokens", "cost", "seconds", "attempts", "error"]
EPILOG = """\
Examples (the prices come from the provider's price page on the day of the run, euro per million tokens):

  python3 -m aid.evalscripts.bakeoff --models claude-opus-5-5 claude-sonnet-5-5 claude-haiku-5-5 --estimate-only

  python3 -m aid.evalscripts.bakeoff --models MODEL azure_openai:DEPLOYMENT foundry:DEPLOYMENT --estimate-only

  python3 -m aid.evalscripts.bakeoff --models claude-opus-5-5 claude-sonnet-5-5 claude-haiku-5-5 \\
      --price claude-opus-5-5=IN,OUT --price claude-sonnet-5-5=IN,OUT --price claude-haiku-5-5=IN,OUT \\
      --record aid/evalscripts/results/recorded
"""


class BakeoffClient(LlmClient, Protocol):
    """A client the bake-off can run: it answers requests and counts the tokens of one."""
    def count_tokens(self, req: LlmRequest) -> int: ...


ClientFactory = Callable[[str, Price | None], BakeoffClient]


def service_of(model: str) -> tuple[str, str] | None:
    """The service and the deployment of a name like azure_openai:<deployment> or foundry:<deployment>; None for any other name,
    an Anthropic model id."""
    service, colon, deployment = model.partition(":")
    return (service, deployment.strip()) if colon and service in exploration.SERVICES else None


def client_for_model(model: str, price: Price | None, environ: Mapping[str, str] | None = None) -> BakeoffClient:
    """The default client factory. azure_openai:<deployment> and foundry:<deployment> give the exploration reader of that service
    for that deployment, behind the guard that sends the made-up documents of the package only; its settings come from the
    environment variables (environ, by default os.environ), never from a file. Any other name is a model of the Anthropic SDK,
    priced at the euro prices of a million tokens given with --price. Raises ValueError for a service name with no deployment and
    for a setting the service lacks (its text names the variable, never a value)."""
    named = service_of(model)
    if named is None:
        return AnthropicClient(model, price_per_mtok=price)
    service, deployment = named
    if not deployment:
        raise ValueError(f"{model!r} needs a deployment after the colon, as in {service}:<deployment>")
    client = exploration.exploration_client(service, os.environ if environ is None else environ, deployment=deployment, price=price)
    return exploration.guarded(service, client)


def today() -> date:
    """The date of the run, from the clock; a function of its own so that a test can fix it."""
    return date.today()


def parse_price(text: str) -> tuple[str, Price]:
    """A --price value, MODEL=IN,OUT: the model and its euro price per million input tokens and per million output tokens, each zero or more."""
    model, _, prices = text.partition("=")
    price_in, _, price_out = prices.partition(",")
    try:
        price = (Decimal(price_in.strip()), Decimal(price_out.strip()))
    except InvalidOperation:
        price = None
    if not model.strip() or price is None or not all(number.is_finite() and number >= 0 for number in price):
        raise argparse.ArgumentTypeError(f"{text!r} is not MODEL=IN,OUT, a model and its euro price per million input and output tokens")
    return model.strip(), price


@dataclass(frozen=True)
class Target:
    """A document of the set and what its reading is scored against."""
    path: Path
    kind: Kind
    case: str
    expected: dict[tuple[str, str], str | None]
    lines: list[TextLine]                          # the text lines of a PDF; none for a screenshot or a scan


@dataclass
class ResultRow:
    """One model's reading of one document: the line of the result file. `not_read` marks a document that an exploration reader did
    not read at all (its service failed, or the guard refused it), which is no column of the file; no row of an Anthropic model id
    has it."""
    model: str
    case: str
    kind: str
    score: ReadingScore
    input_tokens: int
    output_tokens: int
    cost: Decimal | None
    seconds: float
    attempts: int
    error: str
    not_read: bool = False

    def cells(self) -> list[str | int]:
        source = self.score.source_accuracy
        return [self.model, self.case, self.kind, self.score.fields_total, self.score.fields_correct, f"{self.score.field_accuracy:.4f}",
                self.score.sources_total, self.score.sources_correct, "" if source is None else f"{source:.4f}", self.input_tokens,
                self.output_tokens, "" if self.cost is None else format(self.cost, "f"), f"{self.seconds:.2f}", self.attempts, self.error]

    def progress(self) -> str:
        line = (f"{self.model} {self.case}: {self.score.fields_correct} of {self.score.fields_total} fields right, "
                f"{self.input_tokens} + {self.output_tokens} tokens, {self.seconds:.1f} s")
        return f"{line}, not read: {self.error}" if self.error else line


@dataclass
class Summary:
    """One model over all the documents."""
    model: str
    documents: int
    field_accuracy: float                          # the mean over all documents
    source_accuracy: float | None                  # the mean over the documents where it can be measured
    measured: int                                  # how many documents those are
    cost_per_case: Decimal | None                  # None unless every answer has a cost
    total_cost: Decimal | None
    unread: int
    not_read: int = 0                              # the rows marked not_read


def main(argv: list[str] | None = None, client_factory: ClientFactory | None = None) -> int:
    """Run the bake-off (see the module docstring). Returns 0 when the run is done, whatever the decision and however many documents could
    not be read, and 1 for an estimate whose count failed at a service; a command line that cannot be run (no documents, no spec for one, a
    price that is not MODEL=IN,OUT or is for a model that is not in --models, a service name with a blank deployment, a name with a colon
    that names no service, a variable the service lacks) exits with 2."""
    parser = argparse.ArgumentParser(
        prog="python3 -m aid.evalscripts.bakeoff", epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Run the candidate models on the made-up set, score them and choose the cheapest that is close enough to the best.")
    parser.add_argument("--models", nargs="+", required=True,
                        help="the model ids to run, in this order; azure_openai:<deployment> or foundry:<deployment> for an exploration reader")
    parser.add_argument("--set", type=Path, default=Path("aid/madeup/out"), help="folder of the made-up documents (default aid/madeup/out)")
    parser.add_argument("--spec", type=Path, default=Path("aid/madeup/spec"), help="folder of the case specs (default aid/madeup/spec)")
    parser.add_argument("--out", type=Path, help="the result file (default aid/evalscripts/results/bakeoff_<date of the run>.csv)")
    parser.add_argument("--price", action="append", type=parse_price, default=[], metavar="MODEL=IN,OUT",
                        help="euro per million input tokens and per million output tokens of a model in --models; give it once per model, "
                             "typed from the provider's price page of the day of the run")
    parser.add_argument("--estimate-only", action="store_true", help="print the token count of one request per model and stop")
    parser.add_argument("--record", type=Path, metavar="DIR",
                        help="keep every answer in DIR/<model>, and replay what is there instead of asking again")
    args = parser.parse_args(argv)
    factory, prices = client_factory or client_for_model, dict(args.price)
    unknown = [model for model in prices if model not in args.models]
    if unknown:                                    # a misspelt name would leave the model that does run without a price
        parser.error(f"--price names {', '.join(unknown)}, not among the --models ({' '.join(args.models)}): check the spelling")
    services = " or ".join(f"{service}:<deployment>" for service in exploration.SERVICES)
    for model in args.models:                      # before any client is built
        named = service_of(model)
        if named is not None and not named[1]:
            parser.error(f"{model!r} needs a deployment after the colon, as in {named[0]}:<deployment>")
        if named is None and ":" in model:         # a misspelt service name would leave the guard: an Anthropic model id holds no colon
            parser.error(f"{model!r} names no service: use {services}; an Anthropic model id holds no colon")
    documents = documents_to_record(args.set)
    if not documents:
        parser.error(f"no documents in {args.set}: it holds no *_conf.pdf and no *.png")
    if args.estimate_only:
        return _estimate(args.models, prices, factory, documents, parser)
    targets = _targets(documents, args.spec, parser)
    out = args.out or RESULTS / f"bakeoff_{today().isoformat()}.csv"
    rows = _run(args.models, prices, factory, args.record, targets, out, parser)
    summaries = [_summarise(model, [row for row in rows if row.model == model]) for model in args.models]
    print(f"\nSummary over {len(targets)} documents per model:")
    for summary in summaries:
        print(_describe(summary))
    print(_decide(summaries))
    print(f"Results written to {out}")
    return 0


def _estimate(models: list[str], prices: dict[str, Price], factory: ClientFactory, documents: list[tuple[Path, Kind]],
              parser: argparse.ArgumentParser) -> int:
    """Print the input tokens of the first document's request for each model, which is the size of every request of the full run.
    For an exploration service, a count the client cannot give (no counting route is documented for Azure OpenAI) or that the guard
    refuses is "unknown" with the reason. A count that fails at the service, and a ReaderError of an Anthropic model id (such as the
    anthropic library missing), is "the count failed" with the error, and the command returns 1 once every model was tried. The
    next model follows either way."""
    path, kind = documents[0]
    request = reading_request(path, kind)
    failed = False
    for model in models:
        try:
            tokens = _build(factory, model, prices.get(model), parser).count_tokens(request)
        except ReaderError as error:
            if service_of(model) is None:            # an Anthropic model id: a count was possible, and it failed
                print(f"{model}: the count failed ({error})")
                failed = True
            else:                                    # an exploration service that offers no count, or a document its guard refuses
                print(f"{model}: unknown ({error})")
            continue
        except CopilotError as error:                # the service failed: a key, an address or a deployment, a limit, no connection
            print(f"{model}: the count failed ({error}); correct the variables before the full run")
            failed = True
            continue
        print(f"{model}: {tokens} input tokens for one request ({path.name}); the full run reads {len(documents)} documents per model")
    print(f"The answers add output tokens, up to {request.max_tokens} per request.")
    return 1 if failed else 0


def _build(factory: ClientFactory, model: str, price: Price | None, parser: argparse.ArgumentParser) -> BakeoffClient:
    """The model's client; a setting a service lacks is a command error, as the exploration readers' text names it."""
    try:
        return factory(model, price)
    except ValueError as error:
        parser.error(str(error))


def _targets(documents: list[tuple[Path, Kind]], spec_dir: Path, parser: argparse.ArgumentParser) -> list[Target]:
    """The documents with their expected values and text lines; a document without a spec stops the run before any model is asked."""
    targets = []
    for path, kind in documents:
        case = path.stem.removesuffix("_conf")
        spec = spec_dir / f"{case}.json"
        if not spec.is_file():
            parser.error(f"{path.name} has no spec: {spec} does not exist")
        targets.append(Target(path, kind, case, expected_fields(spec), extract_lines(path) if path.suffix.lower() == ".pdf" else []))
    return targets


def _run(models: list[str], prices: dict[str, Price], factory: ClientFactory, record: Path | None, targets: list[Target],
         out: Path, parser: argparse.ArgumentParser) -> list[ResultRow]:
    """Read every document with every model and write each row to `out` as it is made, so that a run that stops half way leaves its rows."""
    rows = []
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        for model in models:
            client = _build(factory, model, prices.get(model), parser)
            if record is not None:
                client = RecordedClient(record / model.replace(":", "_"), live=client)
            for target in targets:
                row = _read(model, client, target, prices.get(model))
                rows.append(row)
                writer.writerow(row.cells())
                handle.flush()
                print(row.progress(), flush=True)
    return rows


def _read(model: str, client: LlmClient, target: Target, price: Price | None) -> ResultRow:
    """One model's reading of one document, scored. A ReaderError is the row of a document that scores no field right, with what
    the failed requests used; so is a CopilotError, the failure of a service, which used nothing we know of. For an exploration
    reader, a CopilotError and a ReaderError without an answer (the guard's refusal) mark the row not_read."""
    try:
        doc, response = read_supplier_document(target.path, client, kind=target.kind)
        score, error, attempts, unread = score_reading(doc, target.expected, target.lines), "", response.attempts, False
    except ReaderError as failure:
        response, score, error, attempts = failure.response, score_unread(target.expected), str(failure), failure.attempts
        unread = failure.response is None
    except CopilotError as failure:
        response, score, error, attempts, unread = None, score_unread(target.expected), f"service failed: {failure}", 0, True
    used = response if response is not None else LlmResponse(text="", model=model, input_tokens=0, output_tokens=0, seconds=0.0)   # no answer: no use
    cost = used.cost_eur
    if cost is None and price is not None:           # answers replayed from a recording made without a price
        cost = (used.input_tokens * price[0] + used.output_tokens * price[1]) / 1_000_000
    return ResultRow(model, target.case, target.kind, score, used.input_tokens, used.output_tokens, cost, used.seconds, attempts, error,
                     not_read=unread and service_of(model) is not None)


def _summarise(model: str, rows: list[ResultRow]) -> Summary:
    sources = [row.score.source_accuracy for row in rows if row.score.source_accuracy is not None]
    costs = [row.cost for row in rows]
    priced = None not in costs
    return Summary(model, len(rows), fmean(row.score.field_accuracy for row in rows), fmean(sources) if sources else None, len(sources),
                   sum(costs) / len(costs) if priced else None, sum(costs) if priced else None, sum(bool(row.error) for row in rows),
                   sum(row.not_read for row in rows))


def _describe(summary: Summary) -> str:
    source = ("not measurable" if summary.source_accuracy is None
              else f"{summary.source_accuracy:.1%} ({summary.measured} of {summary.documents} documents)")
    cost = ("unknown (no price given)" if summary.cost_per_case is None
            else f"EUR {summary.cost_per_case:.4f}, total EUR {summary.total_cost:.4f}")
    unread = f", {summary.unread} document{'s' if summary.unread != 1 else ''} not read" if summary.unread else ""
    return f"{summary.model}: field accuracy {summary.field_accuracy:.1%}, source accuracy {source}, cost per case {cost}{unread}"


def _decide(summaries: list[Summary]) -> str:
    """The decision by choose, or why there is none: a document of an exploration reader that was not read (its row would count as a
    reading of 0), a model without a price, or no model close enough to the best. A model with no source accuracy to measure counts as
    0 there: nothing it read was shown to be sourced."""
    if count := sum(summary.not_read for summary in summaries):
        models, many = ", ".join(summary.model for summary in summaries if summary.not_read), count != 1
        return (f"No decision: {count} document{'s' if many else ''} of {models} {'were' if many else 'was'} not read (the service failed, "
                f"or the document was refused); run the same command again with the same --record to ask {'them' if many else 'it'} again.")
    unpriced = [summary.model for summary in summaries if summary.cost_per_case is None]
    if unpriced:
        return f"No decision: the cost per case is unknown for {', '.join(unpriced)}; give --price MODEL=IN,OUT for every model."
    rows = [(summary.model, summary.field_accuracy, summary.source_accuracy or 0.0, float(summary.cost_per_case)) for summary in summaries]
    try:
        chosen = choose(rows)
    except ValueError as error:
        return f"No decision: {error}."
    return f"Decision: {chosen}, the cheapest model within 2 percentage points of the best field accuracy and of the best source accuracy."


if __name__ == "__main__":
    raise SystemExit(main())
