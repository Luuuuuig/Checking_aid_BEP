# Checking aid

A tool that checks a supplier's order confirmation, or a saved webshop cart page, against the purchase order in the ERP system, flags every difference, routes each finding by a settings table (make the correction in the system, ask the supplier, or hand it to the buyer), and keeps an audit record of every step. It was built for a bachelor end project on operational purchasing; the design specification, the implementation plan, the evaluation protocol and the record of every decision are in the thesis repository, [Luuuuuig/Bachelor-End-Project](https://github.com/Luuuuuig/Bachelor-End-Project) (the `docs/` folder there; docstrings in this code that name `docs/process/...` refer to it). Development continues there; this repository holds a copy of the package `aid/` and the two evaluation templates its tests read, synced from the thesis repository's work branch.

## Install and test

Python 3.13. From the repository root:

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

The first command writes a settings file and a case list for the 22 made-up cases, files their purchase orders, runs every case with the template parser (no AI) and prints the statuses; look at `demo/summary.csv`, a case folder under `demo/cases/` and `demo/cases/prepared/prepared_changes.csv`, the changes the aid would make in the ERP system. The second serves the review page on http://127.0.0.1:7860: the cases for the buyer, with the documents, the findings, the decisions, close and undo, in Dutch with an English switch. The runner loop watches an inbox folder (`python3 -m aid.runner --settings demo/settings.json --poll-seconds 5 --max-passes 3` after copying a made-up confirmation from `aid/madeup/out/` into `demo/inbox/`) and writes the digest under `demo/cases/digests/`. `--reader recorded` replays the recorded AI answers; `--reader anthropic` reads the made-up documents live through the Anthropic client, with the key in the environment variable `ANTHROPIC_API_KEY` (never in a file). The full guide, the settings, the other commands and what the demo does not do are in [aid/README.md](aid/README.md).

## Rules of this repository

- Only made-up documents: no company document, screenshot, export or identifying data is ever committed, and no API key, token, password or authorization amount.
- Company documents are read only on the company side through the company's Microsoft AI service (the client of Task 7b of the plan, not yet in the code) and go to no other service; the Anthropic client is the development client for made-up documents only.
- Writes to the ERP system go through the Exact API writer, which stays behind its fake until the company gives a route to a test-account copy; until then the aid prepares changes in a CSV file and writes its messages as .eml files.
