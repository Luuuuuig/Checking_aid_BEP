# The checking aid in Google Colab: the made-up set only

[Run_Aid_Colab.ipynb](Run_Aid_Colab.ipynb) runs the checking aid in Google Colab on the made-up documents (`aid/madeup/out`). It is for development and demonstration. It is connected to no company system: the aid prepares its changes in a CSV file and writes its messages and its digest as `.eml` files. Nothing is sent and nothing is written to any system. Company documents are never run here. They are run on the company side (see "Running on the company side" in [aid/README.md](../README.md)).

## The Drive folder

The notebook works in one folder of Google Drive:

```
MyDrive/BEP-aid/
  settings.json   the made-up settings
  inbox/          the watched folder: put made-up documents here
  cases/          one folder per case, the digest state and runner.log
  po_store/       the PO versions the aid has filed
  prepared/       prepared_changes.csv and the .eml files of the messages and the digest
  STOP            create this file to stop the loop
```

`inbox/`, `cases/`, `po_store/` and `prepared/` are created when they are missing. Relative folders in `settings.json` resolve against the folder that holds it, so `"cases_root": "cases"`, `"po_store_root": "po_store"` and `"watched_folder": "inbox"` give the layout above.

## settings.json

Copy [aid/settings.example.json](../settings.example.json) to `MyDrive/BEP-aid/settings.json` and change only this:

- the e-mail addresses stay `example.com` addresses, as in the example;
- `supplier_lead_times` names the made-up suppliers `S01` to `S22`, each with a lead time in days, for example `{"S01": 10, "S02": 10, ...}`;
- `authorization_limit` and `exact_base_url` stay `null`, and `smtp_host` and `smtp_user` stay `null`;
- `reader` is `"llm"` to read with the model of `model`, or `"parser"` to run without a model.

Never put a real e-mail address, a real supplier name, an authorization amount or an Exact address in this file or anywhere else in Drive.

## Colab Secrets

Colab Secrets hold two values and no more:

- `GITHUB_TOKEN`, a token that can read the repository, for the clone of cell 2;
- `ANTHROPIC_API_KEY`, for the development client that reads the made-up documents.

No Exact token and no mailbox password go into Colab Secrets.

## Running it

1. Put the made-up documents into `inbox/`: the PO PDFs (`*_po_v*.pdf`) and the confirmations (`*_conf.pdf`) of `aid/madeup/out`. The aid files the PO PDFs into `po_store/` and opens a case for each confirmation.
2. Delete `STOP` if it is there from an earlier run, or the loop makes no pass.
3. Run the five cells in order: mount Drive, clone and install, build the dependencies, the loop, and the review page.

Cell 4 makes a pass every 60 seconds: it handles `inbox/` (the files it handled move to `inbox/processed/`), runs every new case, ends the wait of questions that had no answer in time and makes the digest when it is due. Errors are written to `cases/runner.log`, and the loop goes on. Colab's clock runs in UTC, so `digest_time` is compared in UTC there: "16:30" is 17:30 in the Netherlands in winter and 18:30 in summer.

To stop the loop, create an empty file `STOP` in `MyDrive/BEP-aid/`; the loop ends before its next pass. Interrupting the cell also stops it. Cell 5 then starts the review page with a public share link. The link is for the made-up set only: before it opens the link, the cell asks `refuse_share` of `aid/review/__main__.py` and stops with the reason when `settings.json` holds a company value (an Exact address, endpoints or fields, an authorization limit, an SMTP server or login, or an e-mail address outside `example.com` and `example.org`).

## What the aid leaves in prepared/

- `prepared_changes.csv`: one row per change the aid would make in the ERP.
- One `.eml` file per message: the questions to the made-up suppliers, and the digest, once a day from `digest_time` on (subject "[Inkoopassistent] Dagoverzicht DD-MM-YYYY"). Open a file in a mail program, or as text, to read it.

The `.eml` files are written, not sent. The settings hold only `example.com` addresses, so even a copy of these files sent by hand would reach no one.
