# Checking aid

A tool that checks a supplier's order confirmation, or the webshop cart before ordering, against the purchase order in the ERP system, flags every difference, routes each finding by a settings table (prepare the correction for the buyer to enter, ask the supplier, or hand it to the buyer), and keeps an audit record of every step. It was built for a bachelor end project on operational purchasing; the design specification, the implementation plan, the evaluation protocol and the record of every decision are in the thesis repository, [Luuuuuig/Bachelor-End-Project](https://github.com/Luuuuuig/Bachelor-End-Project) (the `docs/` folder there; docstrings in this code that name `docs/process/...` refer to it). Development continues there; this repository holds a copy of the package `aid/` and the two evaluation templates its tests read, synced from the thesis repository's work branch.

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

The first command writes a settings file and a case list for the 22 made-up cases, files their purchase orders, runs every case with the template parser (no AI) and prints the statuses; look at `demo/summary.csv`, a case folder under `demo/cases/` and `demo/cases/prepared/prepared_changes.csv`, the changes the aid prepares for the buyer to enter in the ERP system. The second serves the review page on http://127.0.0.1:7860: the cases for the buyer, with the documents, the findings, the decisions, close and undo, in Dutch with an English switch. Its tab "Beoordelen" opens with a strip that counts the cases by their state and says what is wrong with the aid, keeps itself up to date every 30 seconds, and also lists the cases the aid could not finish and the documents it could not place, for the buyer to handle by hand. A case opens with its decisions first, each in its own row that says what the purchase order and the supplier's document say and where each was read, with "Zaak afsluiten" right under them; the two documents follow side by side, with the lines a difference was read from marked on both. The runner loop watches an inbox folder (`python3 -m aid.runner --settings demo/settings.json --poll-seconds 5 --max-passes 3` after copying a made-up confirmation from `aid/madeup/out/` into `demo/inbox/`) and writes the digest under `demo/cases/digests/`. `python3 -m aid.demo --out demo2 --prepare-only` writes a folder in which no case has run yet and whose settings write every message as a file, to watch the runner work. `--reader recorded` replays the recorded AI answers; `--reader anthropic` reads the made-up documents live through the Anthropic client, with the key in the environment variable `ANTHROPIC_API_KEY` (never in a file). The full guide, the settings, the other commands and what the demo does not do are in [aid/README.md](aid/README.md). How to use the aid day to day, run it on the company side, maintain it and solve problems is in [aid/MANUAL.md](aid/MANUAL.md), with a one-page Dutch guide for the buyer at its end.

## Start it once, then it runs by itself

`python3 -m aid.start --settings live/settings.json --testshop` starts the runner, the review page and the capture server together, starts a part again when it stops, and writes their output under `live/logs/`. On Windows, `--install-autostart` puts a shortcut in your own Startup folder (no administrator rights), so the three parts start without a window each time you log on; `--status` says how they are, `--stop` stops them and keeps them off, and `--resume --background` starts them again without a window. On a PC where the word `python` does nothing, call Python by its full path (for example `& "C:\Users\<you>\AppData\Local\Programs\Python\Python311\python.exe" -m aid.start ...` in PowerShell). The details are in [aid/MANUAL.md](aid/MANUAL.md), section 3.4.1.

The buyer changes his own rules on the tab "Instellingen" of the review page: the price and date margins, the wait for a supplier's answer, the digest, the language and the suppliers ([manual](aid/MANUAL.md), section 2.8). That tab has five sections: "Uw regels", "Leveranciers", "Postvak en AI" (the work mailbox and the AI service), "Koppeling met Exact", and "Stand van de assistent", which shows the runner's last pass, the mailbox, what reads the documents and the last lines of its log. A document that does not come in by itself (a purchase order PDF, a confirmation, an export from Exact) is given on the tab "Bestanden", which also says what became of every file the aid was given (section 2.7).

**Without installing anything.** On a PC where nothing may be installed (a work laptop), the aid runs from one folder: `python -m aid.portable --build <folder>` on a Windows PC with python.org's 64-bit Python builds that folder with its own Python inside; a double click on "Inkoopassistent starten" there opens the page and makes the aid start at every logon, and every document stays in the folder's `data` ([manual](aid/MANUAL.md), section 3.16).

## The connection to Exact

The aid reads what Exact itself delivers and writes nothing in Exact: no command builds a write route, and a settings file in which `exact_base_url` is set is refused at every start ([manual](aid/MANUAL.md), sections 3.10 and 3.17).

- **Purchase orders in.** The PDF that Exact e-mails is read with a layout that the owner sets once on the settings tab, from one sample PDF, with a trial read that shows every value before anything is saved. For the check before ordering, when Exact has e-mailed nothing yet, an export of the order lines (Excel or CSV) is read with the columns set the same way, and kept beside the PDF versions. Of a workbook only the one visible sheet that holds data is read, and a file that cannot be read safely (two sheets with data, a row of a comma-separated file with another number of cells than its titles) is refused with the reason.
- **Changes out.** Every change the aid prepares is a row on the tab "In Exact invoeren": the buyer types it into Exact and ticks it, and when a newer purchase order PDF or a newer export from Exact shows the change, the aid ticks it off itself. A ticked value counts in the next comparison, so a second confirmation is compared with the order as entered; a newer print that shows another value ends the tick and puts the row back on the list, with a sentence that says so. A change can be put back with a row of its own for the old value. The confirmations to attach and the status to set are listed for the buyer to do in Exact.
- **Article prices.** On the tab "Prijslijst" the buyer gives the export of one supplier's articles from Exact and that supplier's price list, sees which purchase prices change, and puts the changes on the list with one press; it asks before it takes a supplier's stored prices away. A price that more than one supplier holds, or that differs more than three times from the price in Exact, is kept off the list with the reason.
- **Confirmations.** A confirmation is linked to its order by the order number after one of the words the owner sets on the settings tab. One that names an order the aid does not know yet waits, and is checked again by itself when the order arrives. When the aid finds the order another way, the link is a guess: the case goes to the buyer first, and nothing is prepared or asked for it.
- **Trying it without a supplier.** In the test mode the settings tab makes a try-out confirmation from a purchase order the aid knows, with one chosen difference. A try-out is marked "(proefdocument)" wherever it shows, sends nothing, and never hides or counts as a supplier's own document.

## The four parts

- **The post-check, passive.** After a sign-in with a Microsoft work account on the settings tab of the review page, the runner reads one folder of the mailbox and opens a case for each confirmation that arrives ([manual](aid/MANUAL.md), section 3.14); a folder filled by a mailbox rule works as before. Questions to a supplier, from the aid's own rules or asked by the buyer on the review page, are sent through that mailbox; a reply belongs to the question it answers, and no order is confirmed while a question is still open.
- **The pre-check, before ordering.** A browser extension (`aid/extension/`) sends the webshop cart page to the capture server (`python3 -m aid.capture_server`), which finds the purchase order the cart belongs to, checks the cart against it at once and shows the result in a banner on the page (section 3.13). The order is found for sure by its number on the page, or as the one order filed that day that holds exactly the cart's articles; otherwise the banner says it is a guess and nothing is prepared. The banner names what a cart check does not compare (delivery time, minimum or pack quantity, delivery costs and terms). Try it on the made-up test shop: `python3 -m aid.capture_server --settings demo/settings.json --testshop`, then load the extension ([aid/extension/README.md](aid/extension/README.md)).
- **The price list, on request.** On the tab "Prijslijst" of the review page (above); `python3 -m aid.pricelist` does the same from the command line.
- **The AI service through the work sign-in.** Microsoft 365 Copilot reads the documents after a sign-in on the same tab (section 3.15); without it the template parser reads them. A scanned document without a text layer is not read; the digest and the tab "Bestanden" say so.

## Rules of this repository

- Only made-up documents: no company document, screenshot, export or identifying data is ever committed, and no API key, token, password or authorization amount.
- Company documents are read only on the company side through the company's Microsoft AI service (Microsoft 365 Copilot with the work sign-in, `aid/readers/copilot_client.py`) and go to no other service; the Anthropic client is the development client for made-up documents only.
- The aid writes nothing in the ERP system: every change is prepared for the buyer to enter, on the tab "In Exact invoeren". Messages go out through the work mailbox once it is connected. With nothing connected the aid asks nothing: the case goes to the buyer with the reason, on the review page and in the digest. A settings file can have every message written as an .eml file instead (`send_via` "file").
