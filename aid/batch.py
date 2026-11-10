"""The batch runner: a list of cases run headless through the pipeline (aid/pipeline.py), for the pilot, the parser comparison, the
challenge set and the held-out run.

    python3 -m aid.batch --cases cases.csv --po-dir <folder of PO PDFs and exports> --settings settings.json --reader parser|recorded
                         [--recorded-dir <folder of recorded answers>] --out summary.csv

cases.csv has the columns case_id, document, po_number and kind: the evaluation's id of the case, the supplier's document (a path
relative to the folder of cases.csv, or absolute), the PO number (blank when it is unknown) and "confirmation" or "cart". A line
without a case id, a document or a kind is a failed case. The store
is the one under po_store_root of the settings, with the authorization limit of the settings. Every *_po_v*.pdf of --po-dir is filed
into it first, and then every *_export.csv, the ERP export that gives a cart its PO side (PoStore.from_export), with the column map
of settings.export_columns, or with each column named after its field when that setting is empty, and with the number convention
and the fill-down of settings.export_decimal_comma and settings.export_fill_down; a file the store refuses
stops the batch before any case runs, and what the store holds from before stays. The reader is the parser baseline
(--reader parser) or the LLM reader over the answers recorded in --recorded-dir (--reader recorded), which calls no model; under
either, a cart page saved as HTML (is_cart_page of aid/readers/cart.py) is read by the cart reader, which needs no model. There is no
decider, the writer prepares each change in <cases_root>/prepared/prepared_changes.csv, no message is sent and the clock is the real
one. After its cases the batch ends, at that clock, the wait of the questions under cases_root that other runs sent (escalate_pending
of aid/pipeline.py), and prints a line for each case it sends to review and for each case whose files it cannot read.

The cases run in the order of the file, each opened as an upload (aid/sources/upload.py) under cases_root and run with run_case. A
case opened in the same second as a case of the same PO (or of no PO) would have that case's id, so it is opened on the first second
after it that gives a free id, as the watched folder does. The aid's dispositions of each case go to its disposition log, under the
case id of cases.csv. --out gets one row per case as the case ends, so that a batch cut short keeps the rows of the cases it ran.
The columns are SUMMARY_COLUMNS: the case id of cases.csv, the status,
the selected version, the number of flags and of uncompared items (blank when nothing was compared), the rule and route of each
action ("R04 AUTO; R01 AUTO"), the number of writes, of messages and of review items, and the error of a failed case. A case that
cannot even be opened (a missing file) is a failed row. The command prints a line per case and returns 0, or 1 when a case failed.
"""
import argparse
import csv
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from aid.case import Case
from aid.decider import Decider, NoDecider
from aid.execution.writer import PreparedWriter
from aid.pipeline import UNREADABLE, CaseResult, Deps, escalate_pending, read_with_parser, record_dispositions, run_case
from aid.po_store import PoStore
from aid.readers.cart import is_cart_page, read_cart_html
from aid.readers.llm_client import LlmResponse, RecordedClient
from aid.readers.supplier_llm import read_supplier_document
from aid.schema import Document
from aid.settings import Settings, load_settings
from aid.sources.upload import open_upload_case

SUMMARY_COLUMNS = ("case_id", "status", "version", "flags", "uncompared", "actions", "writes", "messages", "review_items", "error")
REQUIRED = ("case_id", "document", "kind")       # the columns of cases.csv a case cannot do without; po_number may be blank
CASE_ID_TRIES = 1000                             # how many seconds after the clock's time a free case id is looked for


def main(argv: list[str] | None = None, reader: Callable | None = None, decider: Decider | None = None) -> int:
    """Run the cases of --cases and write the summary to --out (see the module docstring); 0, or 1 when a case failed. A caller may
    pass its own reader and decider in place of --reader's (the demo does, for a live client over made-up documents)."""
    args = _arguments(argv)
    settings = load_settings(args.settings)
    store = file_po_dir(settings, args.po_dir)
    deps = Deps(settings=settings, store=store, reader=reader or _reader(args), decider=decider or NoDecider(),
                writer=PreparedWriter(settings.cases_root / "prepared"), mailer=None, clock=_now)
    cases, counts = _cases(args.cases), Counter()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        summary = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS, lineterminator="\n")
        summary.writeheader()
        for line, case in cases:
            row = _run(case, f"line {line} of {args.cases.name}", args.cases.parent, deps)
            summary.writerow(row)
            f.flush()
            counts[row["status"]] += 1
    for case_id, reason in escalate_pending(settings.cases_root, deps.clock(), settings):
        print(f"{case_id}: {reason}" if reason.startswith(f"{UNREADABLE}:") else f"{case_id}: escalated to review: {reason}")
    print(f"{len(cases)} cases, " + ", ".join(f"{counts[status]} {status}" for status in ("closed", "review", "waiting", "failed"))
          + f"; summary in {args.out}")
    return 1 if counts["failed"] else 0


def file_po_dir(settings: Settings, po_dir: Path) -> PoStore:
    """The PO store of the settings with every *_po_v*.pdf of po_dir filed into it, then every *_export.csv, read with the three
    export settings (the column map, which when empty means that each column is named after its field, the number convention and
    the fill-down of the PO number); a file the store holds already is not filed twice."""
    store = PoStore(settings.po_store_root, settings.authorization_limit)
    for pdf in sorted(Path(po_dir).glob("*_po_v*.pdf")):
        store.add(pdf)
    for export in sorted(Path(po_dir).glob("*_export.csv")):
        store.from_export(export, settings.export_columns, settings.export_decimal_comma, settings.export_fill_down)
    return store


def _now() -> datetime:
    """The real clock, in local time with its offset."""
    return datetime.now().astimezone()


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a list of cases through the checking aid, headless.")
    parser.add_argument("--cases", type=Path, required=True, help="CSV file with the columns case_id, document, po_number, kind")
    parser.add_argument("--po-dir", type=Path, required=True,
                        help="folder whose *_po_v*.pdf files and then *_export.csv files are filed into the PO store first")
    parser.add_argument("--settings", type=Path, required=True, help="settings.json")
    parser.add_argument("--reader", choices=["parser", "recorded"], required=True, help="the parser baseline, or the LLM reader over recorded answers")
    parser.add_argument("--recorded-dir", type=Path, help="folder of the recorded answers, for --reader recorded")
    parser.add_argument("--out", type=Path, required=True, help="the summary CSV file to write")
    args = parser.parse_args(argv)
    if args.reader == "recorded" and args.recorded_dir is None:
        parser.error("--reader recorded needs --recorded-dir")
    if not args.po_dir.is_dir():
        parser.error(f"--po-dir {args.po_dir} is not a folder")
    return args


def _reader(args: argparse.Namespace) -> Callable[[Path, str], Document | tuple[Document, LlmResponse]]:
    """The reader of --reader: the parser baseline, or the LLM reader over the answers recorded in --recorded-dir. Under either, a cart
    page saved as HTML is read by the cart reader, which returns the Document alone."""
    if args.reader == "parser":
        return read_with_parser
    client = RecordedClient(args.recorded_dir)

    def read_recorded(path: Path, kind: str) -> Document | tuple[Document, LlmResponse]:
        if is_cart_page(path, kind):
            return read_cart_html(path)
        return read_supplier_document(path, client, kind)
    return read_recorded


def _cases(path: Path) -> list[tuple[int, dict]]:
    """The rows of cases.csv, each with the line of the file it ends on: the reader skips blank lines, and a quoted value may hold a
    line break, so the number of a row is not its line."""
    with open(path, encoding="utf-8-sig", newline="") as f:      # also reads a file saved with a BOM
        reader = csv.DictReader(f)
        return [(reader.line_num, row) for row in reader]


def _run(case: dict, where: str, folder: Path, deps: Deps) -> dict:
    """Open and run one case of cases.csv (`where` names its line), record its dispositions and return its row of the summary."""
    name = (case.get("case_id") or "").strip()
    try:
        if missing := [column for column in REQUIRED if not (case.get(column) or "").strip()]:
            raise ValueError(f"{where} has no {' and no '.join(missing)}")
        opened = _open(deps, folder / case["document"].strip(), case.get("po_number"), case["kind"].strip())
    except Exception as exc:                         # the case cannot be opened: its row says why
        error = f"{type(exc).__name__}: {exc}"
        print(f"{name or where}: failed, not opened: {error}")
        return dict.fromkeys(SUMMARY_COLUMNS, "") | {"case_id": name, "status": "failed", "writes": 0, "messages": 0, "review_items": 0,
                                                    "error": error}
    result = run_case(opened, deps)
    record_dispositions(opened, result, case_id=name)
    print(f"{name} ({result.case_id}): {result.digest_line}")
    return _row(name, result)


def _open(deps: Deps, document: Path, po_number: str | None, kind: str) -> Case:
    """open_upload_case at the clock's time, or at the first second after it that gives the case an id no case has."""
    now = deps.clock()
    for seconds in range(CASE_ID_TRIES):
        try:
            return open_upload_case(deps.settings.cases_root, [document], po_number, kind, now + timedelta(seconds=seconds))
        except FileExistsError as error:
            taken = error
    raise taken


def _row(name: str, result: CaseResult) -> dict:
    findings = result.findings
    return {"case_id": name, "status": result.status,
            "version": "" if result.version is None or result.version.version is None else result.version.version,
            "flags": "" if findings is None else len(findings.flags), "uncompared": "" if findings is None else len(findings.uncompared),
            "actions": "; ".join(f"{action.rule_id} {action.route}" for action in result.actions), "writes": len(result.write_entries),
            "messages": len(result.messages), "review_items": len(result.review_items), "error": result.error or ""}


if __name__ == "__main__":
    raise SystemExit(main())
