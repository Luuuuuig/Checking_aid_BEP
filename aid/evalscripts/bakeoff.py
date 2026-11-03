"""The model bake-off: each candidate model reads the made-up set, every reading is scored against the specs, and the cheapest model
whose accuracy is close enough to the best is chosen.

    python3 -m aid.evalscripts.bakeoff --models M1 M2 ... [--set aid/madeup/out] [--spec aid/madeup/spec] [--out FILE] [--price M=IN,OUT ...]
                                       [--estimate-only] [--record DIR]

For each model, in the order given, the command reads each *_conf.pdf of --set as a confirmation and each *.png (CART02.png) as a cart
page with read_supplier_document, scores the reading with score_reading against the spec of the case in --spec (aid/madeup/spec/<case>.json)
and the text lines of the PDF (a screenshot has none, so its sources are not scored), and writes one CSV row per model and document to --out
(default aid/evalscripts/results/bakeoff_<date>.csv, the date of the run). A read that raises ReaderError is a row with the error and no
field right, not the end of the run. The columns, and how the result is read, are in aid/evalscripts/results/README.md.

The summary that follows has, per model, the mean field accuracy over all documents, the mean source accuracy over the documents where it
can be measured, the mean cost per case and the total cost, and then the decision of choose: the cheapest model whose field accuracy and
source accuracy are both within 2 percentage points of the best. Without a price for every model there is no decision, because there is no
cost to compare.

--price MODEL=IN,OUT gives a model's price in euro per million input tokens and per million output tokens. The prices are read from the
provider's price page on the day of the run and typed here; the command holds none. A price for a model that is not in --models is a
command error: it is nearly always a misspelt name, which would leave the model that does run without a price. Every answer's cost is the
client's own figure or, where the answer carries none (a replay of answers recorded without a price), worked out from its tokens at the
price given now.

--estimate-only counts the input tokens of one request per model (the first document of the set, through the client's count_tokens), prints
them and stops before any answer is asked for, so the cost can be approved first.

--record DIR wraps each model's client in RecordedClient(DIR/<model>, live=client): every answer is kept, and a second run with the same DIR
replays them without a call. Recorded answers are answers on made-up documents.

main takes the client factory as its second argument (model, price -> client), so that the tests run it with fake clients; the default
builds AnthropicClient(model, price_per_mtok=price), which reads its key from ANTHROPIC_API_KEY.
"""
import argparse
import csv
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import fmean
from typing import Protocol

from aid.evalscripts.score_reading import ReadingScore, choose, expected_fields, score_reading, score_unread
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

  python3 -m aid.evalscripts.bakeoff --models claude-opus-5-5 claude-sonnet-5-5 claude-haiku-5-5 \\
      --price claude-opus-5-5=IN,OUT --price claude-sonnet-5-5=IN,OUT --price claude-haiku-5-5=IN,OUT \\
      --record aid/evalscripts/results/recorded
"""


class BakeoffClient(LlmClient, Protocol):
    """A client the bake-off can run: it answers requests and counts the tokens of one."""
    def count_tokens(self, req: LlmRequest) -> int: ...


ClientFactory = Callable[[str, Price | None], BakeoffClient]


def anthropic_client(model: str, price: Price | None) -> AnthropicClient:
    """The default client factory: the model behind the Anthropic SDK, priced at the euro prices of a million tokens given with --price."""
    return AnthropicClient(model, price_per_mtok=price)


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
    """One model's reading of one document: the line of the result file."""
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


def main(argv: list[str] | None = None, client_factory: ClientFactory | None = None) -> int:
    """Run the bake-off (see the module docstring). Returns 0 when the run is done, whatever the decision and however many documents could
    not be read; a command line that cannot be run (no documents, no spec for one, a price that is not MODEL=IN,OUT or is for a model
    that is not in --models) exits with 2."""
    parser = argparse.ArgumentParser(
        prog="python3 -m aid.evalscripts.bakeoff", epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Run the candidate models on the made-up set, score them and choose the cheapest that is close enough to the best.")
    parser.add_argument("--models", nargs="+", required=True, help="the model ids to run, in this order")
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
    factory, prices = client_factory or anthropic_client, dict(args.price)
    unknown = [model for model in prices if model not in args.models]
    if unknown:                                    # a misspelt name would leave the model that does run without a price
        parser.error(f"--price names {', '.join(unknown)}, not among the --models ({' '.join(args.models)}): check the spelling")
    documents = documents_to_record(args.set)
    if not documents:
        parser.error(f"no documents in {args.set}: it holds no *_conf.pdf and no *.png")
    if args.estimate_only:
        return _estimate(args.models, prices, factory, documents)
    targets = _targets(documents, args.spec, parser)
    out = args.out or RESULTS / f"bakeoff_{today().isoformat()}.csv"
    rows = _run(args.models, prices, factory, args.record, targets, out)
    summaries = [_summarise(model, [row for row in rows if row.model == model]) for model in args.models]
    print(f"\nSummary over {len(targets)} documents per model:")
    for summary in summaries:
        print(_describe(summary))
    print(_decide(summaries))
    print(f"Results written to {out}")
    return 0


def _estimate(models: list[str], prices: dict[str, Price], factory: ClientFactory, documents: list[tuple[Path, Kind]]) -> int:
    """Print the input tokens of the first document's request for each model, which is the size of every request of the full run."""
    path, kind = documents[0]
    request = reading_request(path, kind)
    for model in models:
        tokens = factory(model, prices.get(model)).count_tokens(request)
        print(f"{model}: {tokens} input tokens for one request ({path.name}); the full run reads {len(documents)} documents per model")
    print(f"The answers add output tokens, up to {request.max_tokens} per request.")
    return 0


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
         out: Path) -> list[ResultRow]:
    """Read every document with every model and write each row to `out` as it is made, so that a run that stops half way leaves its rows."""
    rows = []
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        for model in models:
            client = factory(model, prices.get(model))
            if record is not None:
                client = RecordedClient(record / model, live=client)
            for target in targets:
                row = _read(model, client, target, prices.get(model))
                rows.append(row)
                writer.writerow(row.cells())
                handle.flush()
                print(row.progress(), flush=True)
    return rows


def _read(model: str, client: LlmClient, target: Target, price: Price | None) -> ResultRow:
    """One model's reading of one document, scored. A ReaderError is the row of a document that scores no field right, with what
    the failed requests used."""
    try:
        doc, response = read_supplier_document(target.path, client, kind=target.kind)
        score, error, attempts = score_reading(doc, target.expected, target.lines), "", response.attempts
    except ReaderError as failure:
        response, score, error, attempts = failure.response, score_unread(target.expected), str(failure), failure.attempts
    used = response if response is not None else LlmResponse(text="", model=model, input_tokens=0, output_tokens=0, seconds=0.0)   # no answer: no use
    cost = used.cost_eur
    if cost is None and price is not None:           # answers replayed from a recording made without a price
        cost = (used.input_tokens * price[0] + used.output_tokens * price[1]) / 1_000_000
    return ResultRow(model, target.case, target.kind, score, used.input_tokens, used.output_tokens, cost, used.seconds, attempts, error)


def _summarise(model: str, rows: list[ResultRow]) -> Summary:
    sources = [row.score.source_accuracy for row in rows if row.score.source_accuracy is not None]
    costs = [row.cost for row in rows]
    priced = None not in costs
    return Summary(model, len(rows), fmean(row.score.field_accuracy for row in rows), fmean(sources) if sources else None, len(sources),
                   sum(costs) / len(costs) if priced else None, sum(costs) if priced else None, sum(bool(row.error) for row in rows))


def _describe(summary: Summary) -> str:
    source = ("not measurable" if summary.source_accuracy is None
              else f"{summary.source_accuracy:.1%} ({summary.measured} of {summary.documents} documents)")
    cost = ("unknown (no price given)" if summary.cost_per_case is None
            else f"EUR {summary.cost_per_case:.4f}, total EUR {summary.total_cost:.4f}")
    unread = f", {summary.unread} document{'s' if summary.unread != 1 else ''} not read" if summary.unread else ""
    return f"{summary.model}: field accuracy {summary.field_accuracy:.1%}, source accuracy {source}, cost per case {cost}{unread}"


def _decide(summaries: list[Summary]) -> str:
    """The decision by choose, or why there is none. A model with no source accuracy to measure counts as 0 there: nothing it read was
    shown to be sourced."""
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
