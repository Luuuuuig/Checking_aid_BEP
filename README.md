# Checking aid

A tool that checks a supplier's order confirmation, or the webshop cart before ordering, against the purchase order in the ERP system, flags every difference, routes each finding by a settings table (make the correction in the system, ask the supplier, or hand it to the buyer), and keeps an audit record of every step. It was built for a bachelor end project on operational purchasing; the design specification, the implementation plan, the evaluation protocol and the record of every decision are in the thesis repository, [Luuuuuig/Bachelor-End-Project](https://github.com/Luuuuuig/Bachelor-End-Project) (the `docs/` folder there; docstrings in this code that name `docs/process/...` refer to it). Development continues there; this repository holds a copy of the package `aid/` and the two evaluation templates its tests read, synced from the thesis repository's work branch.

## Install and test

Python 3.11 or newer (developed on 3.13). From the repository root:

```bash
python3 -m pip install -r aid/requirements.txt -r aid/requirements-dev.txt
python3 -m pytest aid/tests -q
```

The test suite runs offline: no test makes a network call or calls an AI service. The documents the tests use are a made-up set under `aid/madeup/`; a test that needs an AI answer replays a recorded one, made on those documents.

## Try it out

```bash
python3 -m aid.demo --out demo
python3 -m aid.review --settings demo/settings.json
```

The first command writes a settings file and a case list for the 22 made-up cases, files their purchase orders, runs every case with the template parser (no AI) and prints the statuses; look at `demo/summary.csv`, a case folder under `demo/cases/` and `demo/cases/prepared/prepared_changes.csv`, the changes the aid would make in the ERP system. The second serves the review page on http://127.0.0.1:7860: the cases for the buyer, with the documents, the findings, the decisions, close and undo, in Dutch with an English switch. The runner loop watches an inbox folder (`python3 -m aid.runner --settings demo/settings.json --poll-seconds 5 --max-passes 3` after copying a made-up confirmation from `aid/madeup/out/` into `demo/inbox/`) and writes the digest under `demo/cases/digests/`. `--reader recorded` replays the recorded AI answers; `--reader anthropic` reads the made-up documents live through the Anthropic client, with the key in the environment variable `ANTHROPIC_API_KEY` (never in a file). The full guide, the settings, the other commands and what the demo does not do are in [aid/README.md](aid/README.md). How to use the aid day to day, run it on the company side, maintain it and solve problems is in [aid/MANUAL.md](aid/MANUAL.md), with a one-page Dutch guide for the buyer at its end.

## Start it once, then it runs by itself

`python3 -m aid.start --settings live/settings.json --testshop` starts the runner, the review page and the capture server together, starts a part again when it stops, and writes their output under `live/logs/`. On Windows, `--install-autostart` puts a shortcut in your own Startup folder (no administrator rights), so the three parts start without a window each time you log on; `--status` says how they are, `--stop` stops them and keeps them off, and `--resume --background` starts them again without a window. On a PC where the word `python` does nothing, call Python by its full path (for example `& "C:\Users\<you>\AppData\Local\Programs\Python\Python311\python.exe" -m aid.start ...` in PowerShell). The details are in [aid/MANUAL.md](aid/MANUAL.md), section 3.4.1.

The buyer changes his own rules on the tab "Instellingen" of the review page: the price and date margins, the wait for a supplier's answer, the digest, the language and the suppliers ([manual](aid/MANUAL.md), section 2.7). The connections (the work mailbox and the AI service) are folded away under "Koppelingen" at the bottom of that tab.

**Without installing anything.** On a PC where nothing may be installed (a work laptop), the aid runs from one folder: `python -m aid.portable --build <folder>` on a Windows PC with python.org's 64-bit Python builds that folder with its own Python inside; a double click on "Inkoopassistent starten" there opens the page and makes the aid start at every logon, and every document stays in the folder's `data` ([manual](aid/MANUAL.md), section 3.16).

## The connection to Exact

The aid reads what Exact itself delivers and changes nothing in Exact ([manual](aid/MANUAL.md), section 3.17):

- **Purchase orders in.** The PDF that Exact e-mails is read with a layout that the owner sets once on the settings tab, from one sample PDF, with a trial read that shows every value before anything is saved. For the check before ordering, when Exact has e-mailed nothing yet, an export of the order lines (Excel or CSV) is read with the columns set the same way, and kept beside the PDF versions.
- **Changes out.** Every change the aid would make is a row on the tab "In Exact invoeren": the buyer types it into Exact and ticks it, and when a newer purchase order PDF from Exact shows the change, the aid ticks it off itself. A change can be put back with an honest row for the old value. The confirmations to attach and the status to set are listed for the buyer to do in Exact.
- **Confirmations.** A confirmation finds its order among the numbers the aid knows, and one that came before its order is checked again by itself when the order arrives.

## The four parts

- **The post-check, passive.** After a sign-in with a Microsoft work account on the settings tab of the review page, the runner reads one folder of the mailbox and opens a case for each confirmation that arrives ([manual](aid/MANUAL.md), section 3.14); a folder filled by a mailbox rule works as before.
- **The pre-check, before ordering.** A browser extension (`aid/extension/`) sends the webshop cart page to the capture server (`python3 -m aid.capture_server`), which checks it against the purchase order at once and shows the result in a banner on the page (section 3.13). Try it on the made-up test shop: `python3 -m aid.capture_server --settings demo/settings.json --testshop`, then load the extension ([aid/extension/README.md](aid/extension/README.md)).
- **The price list, on request.** `python3 -m aid.pricelist` updates the article prices from a supplier's price list, with a write log and an undo.
- **Purchase orders from an ERP export.** An export of purchase order lines, as an Excel workbook or a CSV file with the ERP's own column titles and number form, is read with the column map of the settings (`export_columns`, `export_decimal_comma`, `export_fill_down`; [manual](aid/MANUAL.md), section 6); a file it cannot read safely is refused by its column or row.
- **The AI service through the work sign-in.** Microsoft 365 Copilot reads the documents after a sign-in on the same tab (section 3.15); without it the template parser reads them.

## Rules of this repository

- Only made-up documents: no company document, screenshot, export or identifying data is ever committed, and no API key, token, password or authorization amount.
- Company documents are read only on the company side through the company's Microsoft AI service (Microsoft 365 Copilot with the work sign-in, `aid/readers/copilot_client.py`) and go to no other service; the Anthropic client is the development client for made-up documents only.
- Writes to the ERP system go through the Exact API writer, which stays behind its fake until the company gives a route to a test-account copy; until then the aid prepares changes in a CSV file and writes its messages as .eml files.
