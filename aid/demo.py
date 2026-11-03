"""A one-command demo of the checking aid on the made-up document set: python3 -m aid.demo --out demo

The demo prepares a folder and runs the first pass, so that the aid can be tried out without any credential, any company
document or any connection:

    <out>/settings.json   a copy of aid/settings.example.json with the parser as the reader and the folders below
    <out>/cases.csv       the 22 made-up cases (20 confirmations, 2 saved cart pages) for the batch
    <out>/cases/          one folder per case after the run (steps/, audit.json, write_log.jsonl, dispositions.csv)
    <out>/cases/prepared/prepared_changes.csv   the changes the aid would make in Exact, prepared for the buyer
    <out>/cases/outbox/   the questions to the suppliers as .eml files, once a run has a mailer (the review page and the runner)
    <out>/po_store/       the PO versions filed from the made-up PO PDFs and the two ERP exports
    <out>/inbox/          the watched folder of the runner, empty; drop a made-up confirmation in it and start the runner
    <out>/summary.csv     one row per case: the status, the flags, the writes, the messages, the review items

The documents come from aid/madeup/out, which the repository holds. --reader parser (the default) reads with the template parser
and needs no AI; the two scans then fail, which is expected, because they need the LLM reader (the carts are given as their saved pages, which the cart reader reads). --reader
recorded replays the recorded LLM answers of the test fixtures instead, so every document is read, still without any call.
--reader anthropic reads with the LLM reader over the Anthropic client, the development client of the plan: it needs the key in the
environment variable ANTHROPIC_API_KEY, calls the service for every document and the decider, and is for the made-up documents of
this demo only; company documents go to the Microsoft client of Task 7b and to no other service.

After the run: python3 -m aid.review --settings <out>/settings.json opens the review page on 127.0.0.1:7860, and
python3 -m aid.runner --settings <out>/settings.json --poll-seconds 5 --max-passes 3 runs the loop over <out>/inbox. Nothing is
sent and nothing is written to any system: the writer prepares changes and the mailer writes files.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path

from aid import batch
from aid.decider import LlmDecider
from aid.madeup.generate import load_specs
from aid.readers.cart import is_cart_page, read_cart_html
from aid.readers.llm_client import AnthropicClient
from aid.readers.supplier_llm import read_supplier_document
from aid.settings import Settings, load_settings

PACKAGE = Path(__file__).resolve().parent
MADEUP_OUT = PACKAGE / "madeup" / "out"
SPEC_DIR = PACKAGE / "madeup" / "spec"
RECORDED_DIR = PACKAGE / "tests" / "fixtures" / "recorded"
EXAMPLE_SETTINGS = PACKAGE / "settings.example.json"
LEAD_TIME_DAYS = 10                                  # the lead time given to every made-up supplier
ANTHROPIC_KEY = "ANTHROPIC_API_KEY"                  # the environment variable the Anthropic SDK reads; never written to a file


def main(argv: list[str] | None = None) -> int:
    """Prepare --out and run the batch over the made-up set; 0 when the run was made, 2 when the folder was refused."""
    args = _arguments(argv)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()) and not args.force:
        print(f"{out} exists and is not empty; choose another folder or pass --force to run into it", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    settings_path = write_settings(out)
    cases_path = write_cases(out)
    (out / "inbox").mkdir(exist_ok=True)
    batch_argv = ["--cases", str(cases_path), "--po-dir", str(MADEUP_OUT), "--settings", str(settings_path), "--out", str(out / "summary.csv")]
    if args.reader == "anthropic":
        if not os.environ.get(ANTHROPIC_KEY):
            print(f"--reader anthropic needs {ANTHROPIC_KEY} in the environment (never in a file); the demo sends made-up documents only",
                  file=sys.stderr)
            return 2
        reader, decider = live_reader(load_settings(settings_path))
        batch.main(batch_argv + ["--reader", "parser"], reader=reader, decider=decider)
    else:
        batch_argv += ["--reader", args.reader] + (["--recorded-dir", str(RECORDED_DIR)] if args.reader == "recorded" else [])
        batch.main(batch_argv)
    print(summary_text(out, args.reader))
    return 0


def live_reader(settings: Settings):
    """The LLM reader and the decider over the Anthropic client, the development client, for the made-up documents of this demo
    only: the model of the settings, or the package default when the settings hold the example's placeholder. A cart page saved
    as HTML is read by the cart reader, which needs no model."""
    model = settings.model if not settings.model.startswith("<") else Settings.model_fields["model"].default
    client = AnthropicClient(model, settings.effort)

    def reader(path: Path, kind: str):
        if is_cart_page(path, kind):
            return read_cart_html(path)
        return read_supplier_document(path, client, kind)
    return reader, LlmDecider(client)


def write_settings(out: Path) -> Path:
    """<out>/settings.json: the example settings with the parser as the reader, the folders under <out>, the new-supplier rule
    off and a lead time per made-up supplier; no authorization limit, so the re-approval rule stays off until one is set in the
    file. The file is read back through load_settings, so a demo never starts from settings that do not load."""
    data = json.loads(EXAMPLE_SETTINGS.read_text(encoding="utf-8"))
    data.update({"reader": "parser", "cases_root": "cases", "po_store_root": "po_store", "watched_folder": "inbox",
                 "first_cases_review": 0,             # every made-up supplier has one case: the new-supplier rule would send all to review
                 "supplier_lead_times": {spec.po.supplier: LEAD_TIME_DAYS for spec in load_specs(SPEC_DIR)}})
    path = out / "settings.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    load_settings(path)
    return path


def write_cases(out: Path) -> Path:
    """<out>/cases.csv: the made-up confirmations as <case>_conf.pdf and the carts as their saved pages, with the PO number of
    each case's spec, in the batch's columns (case_id, document, po_number, kind)."""
    path = out / "cases.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["case_id", "document", "po_number", "kind"])
        for spec in load_specs(SPEC_DIR):
            document = MADEUP_OUT / (f"{spec.case_id}_conf.pdf" if spec.confirmation else f"{spec.case_id}.html")
            writer.writerow([spec.case_id, str(document), spec.po.po_number, "confirmation" if spec.confirmation else "cart"])
    return path


def summary_text(out: Path, reader: str) -> str:
    """What the run gave and what to do next, from <out>/summary.csv."""
    with open(out / "summary.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    counts = Counter(row["status"] for row in rows)
    failed = [row["case_id"] for row in rows if row["status"] == "failed"]
    lines = [f"Demo folder: {out}",
             f"{len(rows)} made-up cases run with the {reader} reader: " + ", ".join(f"{counts[s]} {s}" for s in ("closed", "review", "waiting", "failed") if counts[s])]
    if failed and reader == "parser":
        lines.append(f"Failed with the parser, as expected: {', '.join(failed)} (a scan has no text layer, so it needs the LLM reader; "
                     f"run again with --reader recorded to replay recorded answers for them)")
    lines += ["Look at: summary.csv, cases/<case>/steps/08_result.json, cases/<case>/dispositions.csv, cases/<case>/write_log.jsonl, "
              "cases/<case>/audit.json, cases/prepared/prepared_changes.csv",
              f"Review page (Dutch, with an English switch): python3 -m aid.review --settings {out / 'settings.json'}  then open http://127.0.0.1:7860",
              f"Runner loop: copy a made-up confirmation from {MADEUP_OUT} into {out / 'inbox'}, then "
              f"python3 -m aid.runner --settings {out / 'settings.json'} --poll-seconds 5 --max-passes 3  (the digest lands in cases/digests/)"]
    return "\n".join(lines)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python3 -m aid.demo", description="Prepare a folder and run the checking aid over the made-up document set.")
    parser.add_argument("--out", type=Path, default=Path("demo"), help="the demo folder (default: demo, in the current folder)")
    parser.add_argument("--reader", choices=["parser", "recorded", "anthropic"], default="parser",
                        help="the template parser (default, no AI), the LLM reader over the recorded answers of the tests, or the LLM reader "
                             "over the Anthropic client with the key in ANTHROPIC_API_KEY (made-up documents only)")
    parser.add_argument("--force", action="store_true", help="run into a folder that exists and is not empty")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
