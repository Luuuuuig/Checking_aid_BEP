"""A one-command demo of the checking aid on the made-up document set: python3 -m aid.demo --out demo

The demo prepares a folder and runs the first pass, so that the aid can be tried out without any credential, any company
document or any connection:

    <out>/settings.json   a copy of aid/settings.example.json with the parser as the reader and the folders below
    <out>/cases.csv       the 22 made-up cases (20 confirmations, 2 saved cart pages) for the batch
    <out>/cases/          one folder per case after the run (steps/, audit.json, write_log.jsonl, dispositions.csv)
    <out>/cases/prepared/prepared_changes.csv   the changes the aid would make in Exact, prepared for the buyer
    <out>/cases/outbox/   the messages written as .eml files instead of sent: the runner's digest, and on a prepared folder
                          (whose settings say send_via "file") every question and request of the runner and the review page
    <out>/po_store/       the PO versions of the made-up PO PDFs and the looks that the two ERP exports gave
    <out>/inbox/          the watched folder of the runner, empty; drop a made-up confirmation in it and start the runner
    <out>/summary.csv     one row per case: the status, the flags, the writes, the messages, the review items
    <out>/voorbeelden/    with --demonstration only: the made-up export of articles and price list of S04 for the tab "Prijslijst"

The documents come from aid/madeup/out, which the repository holds. --reader parser (the default) reads with the template parser
and needs no AI; the two scans then fail, which is expected, because they need the LLM reader (the carts are given as their saved pages, which the cart reader reads). --reader
recorded replays the recorded LLM answers of the test fixtures instead, so every document is read, still without any call.
--reader anthropic reads with the LLM reader over the Anthropic client, the development client of the plan: it needs the key in the
environment variable ANTHROPIC_API_KEY, calls the service for every document and the decider, and is for the made-up documents of
this demo only; company documents go to Microsoft 365 Copilot (aid/readers/copilot_client.py) and to no other service.

--prepare-only writes the folder and files the purchase orders but runs no case: the folder to watch the runner loop work by
itself, because a document a folder has seen once counts as a duplicate afterwards and opens no second case. Its settings say
send_via "file": the runner and the review page write every message of this folder as a file, whatever is connected. The folder
of a run keeps the default, "auto", with nothing connected: its runner and its review page ask no question, since they have no way
to send one, and give it to the buyer as "Bericht niet verzonden" (the page says so after a close); only the runner's digest is
written as a file (give the folder a prepared one's send_via "file" to see the questions as files).

--demonstration, which the demonstration start of the folder version runs (python -m aid.portable --demo, aid/MANUAL.md section
3.16), runs the batch as above and then gives the folder the settings of the page and the runner of the demonstration
(demonstration_settings): the test mode on, with no authorization limit, so that the settings tab offers a try-out and the watched
folder takes one; send_via "file", so that a question asked on the page is written to cases/outbox without any mailbox; a made-up
address on example.com for each made-up supplier, so that a question has an address; the buyer's address and those of the
suppliers as the own addresses of the test mode, so that its guard lets such a question be written; and the tab "Prijslijst" ready
at once (demonstration_price_list): the columns of the export of articles (article_columns, article_decimal_comma), an installation
step otherwise, a made-up export of the articles of S04 and S04's made-up price list in <out>/voorbeelden, the purchase prices of
that export taken in for S04 and the columns of the price list kept, so that choosing S04, giving the price list and pressing
"Bekijken" shows the comparison. A plain demo folder leaves article_columns empty: with it set, the review page counts the settings
as a company's and refuses --share, which the demonstration never uses. The batch's own run keeps the settings above, so that its
cases are those of every other run.

After the run: python3 -m aid.review --settings <out>/settings.json opens the review page on 127.0.0.1:7860, and
python3 -m aid.runner --settings <out>/settings.json --poll-seconds 5 --max-passes 3 runs the loop over <out>/inbox. Nothing is
sent and nothing is written to any system: the writer prepares changes, and no mailer sends anything. The settings hold a made-up
capture token, so python3 -m aid.capture_server --settings <out>/settings.json --testshop serves the pre-check in the browser with
the made-up cart pages as a test shop (aid/extension/README.md).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from aid import batch
from aid.decider import LlmDecider
from aid.madeup.generate import load_specs
from aid.pricelist import keep_columns, store_current
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
DEMO_TOKEN = "made-up-token-for-the-test-shop"    # the capture token of the demo settings, made up; the extension's options hold it too
TESTSHOP_PAGE = "http://127.0.0.1:7870/testshop/CART02.html"     # the made-up cart page CART02 in the capture server's test shop
EXTENSION_README = "aid/extension/README.md"
ANTHROPIC_KEY = "ANTHROPIC_API_KEY"                  # the environment variable the Anthropic SDK reads; never written to a file
# The price list of the demonstration (demonstration_price_list): the folder of its two made-up files, their names, the supplier they
# are of, the columns of the export of articles that the settings name and the columns of the price list kept for the supplier.
SAMPLES = "voorbeelden"
SAMPLE_SUPPLIER = "S04"
SAMPLE_EXPORT = f"Verzonnen export artikelen {SAMPLE_SUPPLIER} uit Exact.xlsx"
SAMPLE_LIST = f"Verzonnen prijslijst {SAMPLE_SUPPLIER}.xlsx"
ARTICLE_COLUMNS = {"item_code": "Artikel", "price": "Inkoopprijs"}
LIST_COLUMNS = {"item_code": "Artikelnummer", "price": "Nettopreis", "basis": "Preiseinheit"}
# (item code, the buyer's description in Exact, the supplier's description, the purchase price in Exact or None for an article the
# export does not hold, the price on the price list, the price unit on the price list or None for a blank cell). The articles are
# lines of the made-up purchase order 250104 of S04 with a price per piece (KM-0423 at 0.82 instead of 0.80), and one new article.
# S04 is a German supplier (its confirmation of 250104 is in German), so its list has German titles and descriptions, those of that
# confirmation. The export of Exact holds its prices as numbers, as such an export does; none ends in a zero, as the tab shows the
# price of a number cell without its trailing zero ("0,8"). The price list holds its prices as texts with the decimal comma, as a
# list that a supplier converts from a PDF often does, so the tab shows them as written ("0,70"). article_decimal_comma stays on, as
# for a Dutch export that writes its prices as texts. In the order of the codes, so that the table of "Bekijken" shows the rows that
# matter without a scroll: an article unknown in Exact, two prices that rise by about three percent, a price that cannot be read,
# one ten times the purchase price (a price per 10 read as a price per piece: the supplier left its price unit blank), which the tab
# keeps off the list, and four prices that stay the same.
SAMPLE_ARTICLES = (("KM-0400", "Zeskantbout M10 x 30", "Sechskantschraube M10 x 30", None, "0,39", 1),
                   ("KM-0405", "Zeskantbout M10 x 40", "Sechskantschraube M10 x 40", 0.42, "0,42", 1),
                   ("KM-0406", "Zeskantbout M12 x 50", "Sechskantschraube M12 x 50", 0.68, "0,70", 1),
                   ("KM-0421", "Draadeind M8 x 1000", "Gewindestange M8 x 1000", 1.95, "auf Anfrage", 1),
                   ("KM-0422", "Draadeind M10 x 1000", "Gewindestange M10 x 1000", 2.85, "28,50", None),
                   ("KM-0423", "Slangklem 12-20 mm", "Schlauchschelle 12-20 mm", 0.82, "0,82", 1),
                   ("KM-0424", "Slangklem 20-32 mm", "Schlauchschelle 20-32 mm", 0.85, "0,88", 1),
                   ("KM-0425", "Slangpilaar 1/2 inch", "Schlauchtülle 1/2 Zoll", 1.45, "1,45", 1),
                   ("KM-0426", "Slangpilaar 3/4 inch", "Schlauchtülle 3/4 Zoll", 1.95, "1,95", 1))
PRICE_FORMAT = "0.00"                            # a price cell's number format: Excel shows 0,42 under a Dutch locale
SAMPLE_AUTHOR = "Verzonnen voorbeeld"            # the author of the two workbooks, instead of the library that writes them


def main(argv: list[str] | None = None) -> int:
    """Prepare --out and run the batch over the made-up set; 0 when the run was made, 2 when the folder was refused."""
    args = _arguments(argv)
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()) and not args.force:
        print(f"{out} exists and is not empty; choose another folder or pass --force to run into it", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    settings_path = write_settings(out, files_only=args.prepare_only)
    cases_path = write_cases(out)
    (out / "inbox").mkdir(exist_ok=True)
    if args.prepare_only:
        batch.file_po_dir(load_settings(settings_path), MADEUP_OUT)
        print(prepared_text(out))
        return 0
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
    if args.demonstration:
        demonstration_settings(settings_path)
        demonstration_price_list(out, load_settings(settings_path).cases_root)
    print(summary_text(out, args.reader))
    if args.demonstration:
        print("Settings of the demonstration: the test mode on, questions written as files to cases/outbox, made-up supplier addresses, "
              f"and the tab Prijslijst set up with the purchase prices of {SAMPLE_SUPPLIER} taken in; its made-up export and price list "
              f"are in {out / SAMPLES}")
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


def write_settings(out: Path, files_only: bool = False) -> Path:
    """<out>/settings.json: the example settings with the parser as the reader, the folders under <out>, the new-supplier rule
    off and a lead time per made-up supplier; no authorization limit, so the re-approval rule stays off until one is set in the
    file. With files_only (the prepared folder, which the runner and the review page then work on) also send_via "file": the
    made-up set sends nothing by its settings, not by the absence of a mailbox, so that a sign-in made on its settings tab sends
    nothing either, and a question asked on its review page is written to cases/outbox. The settings of the batch's own run are
    left as they were, since each case's audit record keeps the settings of its run and the batch's files are compared with those
    of earlier runs. The file is read back through load_settings, so a demo never starts from settings that do not load."""
    data = json.loads(EXAMPLE_SETTINGS.read_text(encoding="utf-8"))
    data.update({"reader": "parser", "cases_root": "cases", "po_store_root": "po_store", "watched_folder": "inbox",
                 "first_cases_review": 0,             # every made-up supplier has one case: the new-supplier rule would send all to review
                 "capture_token": DEMO_TOKEN,         # made up, for the test shop of the capture server; a real token stays out of files
                 "supplier_lead_times": {spec.po.supplier: LEAD_TIME_DAYS for spec in load_specs(SPEC_DIR)}})
    if files_only:
        data["send_via"] = "file"
    path = out / "settings.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    load_settings(path)
    return path


def demonstration_settings(path: Path) -> Path:
    """The settings file of a folder whose batch has run, made those of the demonstration start of the folder version (see the module
    docstring): test_mode true (authorization_limit stays null), send_via "file", supplier_emails with a made-up address on
    example.com for each made-up supplier, own_addresses holding those and buyer_email, and the columns of the export of articles of
    the tab "Prijslijst" (ARTICLE_COLUMNS, with the decimal comma). Read back through load_settings."""
    data = json.loads(path.read_text(encoding="utf-8"))
    suppliers = {spec.po.supplier: f"{spec.po.supplier.lower()}@example.com" for spec in load_specs(SPEC_DIR)}
    data.update({"test_mode": True, "send_via": "file", "supplier_emails": suppliers,
                 "own_addresses": [data["buyer_email"], *sorted(set(suppliers.values()))],
                 "article_columns": ARTICLE_COLUMNS, "article_decimal_comma": True})
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    load_settings(path)
    return path


def demonstration_price_list(out: Path, cases_root: Path) -> None:
    """The tab "Prijslijst" of the demonstration, ready at once (see the module docstring): the two made-up sample files in
    <out>/SAMPLES (write_samples), the purchase prices of SAMPLE_SUPPLIER taken in from the export as the tab's "Inkoopprijzen
    opnemen" takes them in (store_current with the columns of the settings), and the columns of the supplier's price list kept as a
    press of "Op de lijst In Exact invoeren zetten" keeps them (keep_columns), so that its column fields fill themselves."""
    export, _ = write_samples(out)
    store_current(cases_root, export, ARTICLE_COLUMNS, True, datetime.now(), SAMPLE_SUPPLIER)
    keep_columns(cases_root, SAMPLE_SUPPLIER, LIST_COLUMNS)


def write_samples(out: Path) -> tuple[Path, Path]:
    """The two made-up files of the price list in <out>/SAMPLES, as workbooks: (the export of the articles of SAMPLE_SUPPLIER as Exact
    gives one, with a title and a blank row above its column titles; that supplier's price list, its column titles in its first row).
    The articles are lines of the made-up purchase order of SAMPLE_SUPPLIER, at the prices of that order but for KM-0423, whose
    price was raised from 0.80 to 0.82 so that no price of the export ends in a zero, and one new article on the price list only
    (see SAMPLE_ARTICLES)."""
    folder = out / SAMPLES
    folder.mkdir(exist_ok=True)
    export, price_list = folder / SAMPLE_EXPORT, folder / SAMPLE_LIST
    _workbook(export, [[f"Artikelen van leverancier {SAMPLE_SUPPLIER} (verzonnen voorbeeld)"], [],
                       [ARTICLE_COLUMNS["item_code"], "Omschrijving", ARTICLE_COLUMNS["price"]],
                       *[[code, text, price] for code, text, _, price, _, _ in SAMPLE_ARTICLES if price is not None]])
    _workbook(price_list, [[LIST_COLUMNS["item_code"], "Bezeichnung", LIST_COLUMNS["price"], LIST_COLUMNS["basis"]],
                           *[[code, text, listed, unit] for code, _, text, _, listed, unit in SAMPLE_ARTICLES]])
    return export, price_list


def _workbook(path: Path, rows: list[list]) -> None:
    """An .xlsx workbook at `path` with one sheet holding these rows, each number (a float) shown with two decimals."""
    book = Workbook()
    book.properties.creator = SAMPLE_AUTHOR
    sheet = book.active
    sheet.title = "Blad1"
    for row in rows:
        sheet.append(row)
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, float):
                cell.number_format = PRICE_FORMAT
    book.save(path)


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


def capture_text(out: Path) -> list[str]:
    """How to try the pre-check in the browser on the demo folder: the capture server with its test shop and the extension."""
    return [f"Pre-check in the browser: python3 -m aid.capture_server --settings {out / 'settings.json'} --testshop  then load the "
            f"extension as {EXTENSION_README} describes (token {DEMO_TOKEN}) and open {TESTSHOP_PAGE}"]


def prepared_text(out: Path) -> str:
    """What a prepared folder holds and how to see the aid work by itself on it."""
    return "\n".join([f"Demo folder prepared, no case run: {out}",
                       "The PO store holds the made-up purchase orders; the inbox is empty; no confirmation has been seen yet.",
                       f"Start the loop:  python3 -m aid.runner --settings {out / 'settings.json'} --poll-seconds 5",
                       f"Then copy a made-up confirmation from {MADEUP_OUT} (for example MU03_conf.pdf) into {out / 'inbox'}: the loop opens the "
                       "case by itself, runs it and writes the digest to cases/digests/ when it is due "
                       "(to get one after every pass with a case, choose \"Na elke zaak\" under \"Dagoverzicht\" on the settings tab "
                       f"\"Instellingen\" of the review page, python3 -m aid.review --settings {out / 'settings.json'}; or set "
                       "\"digest\": \"per_case\" in settings.json).",
                       f"Stop the loop by creating the file {out / 'STOP'}.", *capture_text(out)])


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
              f"python3 -m aid.runner --settings {out / 'settings.json'} --poll-seconds 5 --max-passes 3  (the digest lands in cases/digests/)",
              *capture_text(out)]
    return "\n".join(lines)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python3 -m aid.demo", description="Prepare a folder and run the checking aid over the made-up document set.")
    parser.add_argument("--out", type=Path, default=Path("demo"), help="the demo folder (default: demo, in the current folder)")
    parser.add_argument("--reader", choices=["parser", "recorded", "anthropic"], default="parser",
                        help="the template parser (default, no AI), the LLM reader over the recorded answers of the tests, or the LLM reader "
                             "over the Anthropic client with the key in ANTHROPIC_API_KEY (made-up documents only)")
    parser.add_argument("--force", action="store_true", help="run into a folder that exists and is not empty")
    parser.add_argument("--prepare-only", action="store_true",
                        help="write the settings and the case list and file the purchase orders, but run no case: the folder for "
                             "watching the loop work by itself (a document the folder has seen once counts as a duplicate afterwards)")
    parser.add_argument("--demonstration", action="store_true",
                        help="after the run, the settings of the demonstration start of the folder version: the test mode on, "
                             "questions written as files, made-up supplier addresses, and the tab Prijslijst set up with a made-up "
                             "export and price list in <out>/voorbeelden")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
