"""The AI-necessity check: is the LLM reader needed, or does the parser baseline read the confirmations well enough?

    python3 -m aid.evalscripts.necessity --cases DIR --spec DIR --recorded DIR [--out FILE]

For every *_conf.pdf of --cases, both readers read the confirmation: the parser (detect_template, then read_with_template; a PDF no
template fits, or that its template does not fit after all, is a read failure) and the LLM reader (read_supplier_document through the
client, kind "confirmation"). Each reading is scored with score_reading against the spec of the case (<--spec>/<case>.json) and the
text lines of the PDF. A read failure scores every field wrong and its error is recorded; it never ends the run. The parser's failure
is any exception of the text layer or the template; the LLM reader's is a ReaderError (its text), a recorded answer that is missing
("no recorded answer") or any other exception ("<class>: <message>"). A case without a spec, with a spec that does not parse or with
the spec of a cart ends the run with a message naming the file, before any row is written.

The result file (default aid/evalscripts/results/necessity_<date of the run>.csv) has one row per case and reader with the columns
of COLUMNS; `errors` is fields_total minus fields_correct. The rows go to a temporary file beside it (<name>.partial), flushed row by
row, which is renamed to the result file when the run is complete, so a run that stops leaves no file that looks finished. The summary printed gives, per layout, the error totals of the parser and of
the LLM reader, the grand totals and the decision of necessity_decision. The columns and how the result is read are in
aid/evalscripts/results/README.md.

The client of the LLM reader is RecordedClient(--recorded), which replays recorded answers, unless main is given another (a test
passes a fake). The development cases are company documents: they are read only through the Microsoft client on the company side
(Task 21). A run on the made-up set is development evidence, not the check, and the summary says so.
"""
import argparse
import csv
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from aid.evalscripts.score_reading import ReadingScore, expected_fields, score_reading, score_unread
from aid.madeup.generate import MadeUpSpec
from aid.readers.llm_client import LlmClient, ReaderError, RecordedClient
from aid.readers.pdf_text import TextLine, extract_lines
from aid.readers.supplier_llm import read_supplier_document
from aid.readers.supplier_parser import LayoutTemplate, detect_template, load_templates, read_with_template

Decision = Literal["keep_llm", "parser_only"]
Reader = Literal["parser", "llm"]
READERS: tuple[Reader, ...] = ("parser", "llm")
RESULTS = Path("aid") / "evalscripts" / "results"
MADE_UP_SPECS = Path(__file__).resolve().parents[1] / "madeup" / "spec"
COLUMNS = ["case", "layout", "reader", "fields_total", "fields_correct", "field_accuracy", "errors", "error"]


def necessity_decision(parser_errors: int, llm_errors: int) -> Decision:
    """keep_llm when the LLM reader makes fewer than half the parser's field errors, parser_only otherwise (no errors at all too)."""
    return "keep_llm" if llm_errors < 0.5 * parser_errors else "parser_only"


def today() -> date:
    """The date of the run, from the clock; a function of its own so that a test can fix it."""
    return date.today()


@dataclass(frozen=True)
class Case:
    """A confirmation of the set and what its readings are scored against."""
    pdf: Path
    name: str
    layout: str
    expected: dict[tuple[str, str], str | None]
    made_up: bool                                  # its spec is the made-up spec of the same case


@dataclass(frozen=True)
class Row:
    """One reader's reading of one case: the line of the result file."""
    case: str
    layout: str
    reader: Reader
    score: ReadingScore
    error: str

    @property
    def errors(self) -> int:
        return self.score.fields_total - self.score.fields_correct

    def cells(self) -> list[str | int]:
        return [self.case, self.layout, self.reader, self.score.fields_total, self.score.fields_correct,
                f"{self.score.field_accuracy:.4f}", self.errors, self.error]


def main(argv: list[str] | None = None, client: LlmClient | None = None) -> int:
    """Run the check (see the module docstring). Returns 0 when the run is done, whatever the decision; a command line that cannot be
    run (no confirmation in --cases, a case without a spec) exits with 2."""
    parser = argparse.ArgumentParser(prog="python3 -m aid.evalscripts.necessity",
                                     description="Read every confirmation with the parser and the LLM reader, score both and decide "
                                                 "whether the LLM reader is needed.")
    parser.add_argument("--cases", type=Path, required=True, help="folder of the confirmations (*_conf.pdf)")
    parser.add_argument("--spec", type=Path, required=True, help="folder of the case specs (<case>.json)")
    parser.add_argument("--recorded", type=Path, required=True, help="folder of the recorded answers the LLM reader replays")
    parser.add_argument("--out", type=Path, help="the result file (default aid/evalscripts/results/necessity_<date of the run>.csv)")
    args = parser.parse_args(argv)
    pdfs = sorted(args.cases.glob("*_conf.pdf"))
    if not pdfs:
        parser.error(f"no confirmations in {args.cases}: it holds no *_conf.pdf")
    cases = [_case(pdf, args.spec, parser) for pdf in pdfs]
    client = client or RecordedClient(args.recorded)
    out = args.out or RESULTS / f"necessity_{today().isoformat()}.csv"
    rows = _run(cases, client, out)
    print(_summary(cases, rows))
    print(f"Results written to {out}")
    return 0


def _case(pdf: Path, spec_dir: Path, parser: argparse.ArgumentParser) -> Case:
    name = pdf.name.removesuffix("_conf.pdf")
    spec = spec_dir / f"{name}.json"
    if not spec.is_file():
        parser.error(f"{pdf.name} has no spec: {spec} does not exist")
    try:
        text = spec.read_text(encoding="utf-8")
        parsed = MadeUpSpec.model_validate_json(text)
        expected = expected_fields(spec)
    except (OSError, ValueError) as error:          # a ValidationError and a UnicodeDecodeError are ValueErrors
        parser.error(f"the spec {spec} of {pdf.name} cannot be read: {error}")
    if parsed.confirmation is None:
        parser.error(f"the spec {spec} of {pdf.name} is the spec of a cart, not of a confirmation")
    made_up = MADE_UP_SPECS / spec.name
    return Case(pdf, name, parsed.confirmation.layout, expected, made_up.is_file() and made_up.read_text(encoding="utf-8") == text)


def _run(cases: list[Case], client: LlmClient, out: Path) -> list[Row]:
    """Read every case with both readers and write each row to the temporary file as it is made, so that a run that stops half way
    leaves its rows there; the complete file is renamed to `out`."""
    templates, rows = load_templates(), []
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".partial")
    with open(partial, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        for case in cases:
            try:
                lines, problem = extract_lines(case.pdf), ""
            except Exception as error:               # a PDF without a readable text layer: the parser cannot read it
                lines, problem = [], _described(error)
            for row in (_parser_row(case, templates, lines, problem), _llm_row(case, client, lines)):
                rows.append(row)
                writer.writerow(row.cells())
                handle.flush()
                print(f"{row.case} {row.reader}: {row.score.fields_correct} of {row.score.fields_total} fields right"
                      + (f", not read: {row.error}" if row.error else ""), flush=True)
    os.replace(partial, out)
    return rows


def _parser_row(case: Case, templates: list[LayoutTemplate], lines: list[TextLine], problem: str) -> Row:
    """The parser's reading of the case; `problem` is the failure of the text layer, which leaves the parser nothing to read."""
    if problem:
        return _unread(case, "parser", problem)
    try:
        template = detect_template(case.pdf, templates)
    except Exception as error:
        return _unread(case, "parser", _described(error))
    if template is None:
        return _unread(case, "parser", f"no template for {case.pdf.name}")
    try:
        doc = read_with_template(case.pdf, template)
    except ValueError as error:                     # the template does not fit after all
        return _unread(case, "parser", f"no template for {case.pdf.name}: {error}")
    except Exception as error:
        return _unread(case, "parser", _described(error))
    return Row(case.name, case.layout, "parser", score_reading(doc, case.expected, lines), "")


def _llm_row(case: Case, client: LlmClient, lines: list[TextLine]) -> Row:
    try:
        doc, _ = read_supplier_document(case.pdf, client, kind="confirmation")
    except ReaderError as error:
        return _unread(case, "llm", str(error))
    except KeyError:                                # RecordedClient has no answer recorded for the request
        return _unread(case, "llm", "no recorded answer")
    except Exception as error:
        return _unread(case, "llm", _described(error))
    return Row(case.name, case.layout, "llm", score_reading(doc, case.expected, lines), "")


def _described(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def _unread(case: Case, reader: Reader, error: str) -> Row:
    return Row(case.name, case.layout, reader, score_unread(case.expected), error)


def _summary(cases: list[Case], rows: list[Row]) -> str:
    """The error totals per layout and over all cases, and the decision."""
    def errors(reader: Reader, layout: str | None = None) -> int:
        return sum(row.errors for row in rows if row.reader == reader and layout in (None, row.layout))

    lines = [f"\nField errors over {len(cases)} cases:"]
    for layout in sorted({case.layout for case in cases}):
        lines.append(f"  {layout}: parser {errors('parser', layout)} errors, LLM reader {errors('llm', layout)} errors")
    parser_errors, llm_errors = errors("parser"), errors("llm")
    lines.append(f"All cases: parser {parser_errors} errors, LLM reader {llm_errors} errors")
    lines.append(f"Decision: {necessity_decision(parser_errors, llm_errors)} (keep_llm when the LLM reader makes fewer than half the "
                 "parser's errors)")
    if all(case.made_up for case in cases):
        lines.append("These are the cases of the made-up set: the run is development evidence, not the necessity check.")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
