# The checking aid

`aid` is the Python package of the checking aid of the Bachelor End Project. It reads a supplier's order confirmation, or a captured webshop cart page, compares it with the purchase order by fixed rules, carries out the routine corrections, asks the supplier the standard questions, and hands the buyer only the cases its rules cannot settle. What it does and why is in the [design specification](../docs/superpowers/specs/2026-10-26-checking-aid-design.md); the build, task by task, is in the [implementation plan](../docs/superpowers/plans/2026-10-26-checking-aid-implementation.md).

How to use the aid day to day, run it on the company side, maintain it and solve problems: [MANUAL.md](MANUAL.md) (with a one-page Dutch guide for the buyer at its end).

## Install and test

Python 3.11 or newer (developed on 3.13; the suite also passes on 3.11). From the repository root:

```bash
python3 -m pip install -r aid/requirements.txt -r aid/requirements-dev.txt
python3 -m pytest aid/tests -q
```

The test suite runs offline: no test makes a network call or calls an AI service. A test that needs an AI answer replays a recorded one, made on made-up documents only.

## Try it out on the made-up set

Two commands run the whole aid on the made-up document set, with no AI service, no credential, no company document and no connection: the writer prepares changes in a CSV file and the mailer writes .eml files, so nothing is sent and nothing is written to any system.

```bash
python3 -m aid.demo --out demo
python3 -m aid.review --settings demo/settings.json
```

The first command writes `demo/settings.json` (the example settings with the parser as the reader and the folders under `demo/`) and `demo/cases.csv` (the 20 confirmations and the 2 saved cart pages with their PO numbers), files the made-up PO PDFs and the two ERP exports into `demo/po_store/`, runs every case and prints the statuses. With the parser the two scans fail, as expected: a scan has no text layer, so only the LLM reader can read it; `python3 -m aid.demo --out demo2 --reader recorded` replays the recorded LLM answers of the test fixtures instead, so every document is read, still without any call. Look at `demo/summary.csv` (one row per case), a case folder `demo/cases/<PO number>_<time>/` (`steps/01_po_versions.json` to `08_result.json`, `audit.json`, `write_log.jsonl`, `dispositions.csv`) and `demo/cases/prepared/prepared_changes.csv`, the changes the aid would make in Exact.

The second command serves the review page on http://127.0.0.1:7860 (the host only; `--share` is refused with company settings): the cases in review, each with its two documents, the three lists, the four decisions per item and the article tick, the close button and the undo, in Dutch with a switch to English. Closing a case writes its prepared changes and its questions (`demo/cases/outbox/*.eml`) and its dispositions; the undo reverses a written change.

The runner is the loop both hosts run, and the way the aid works by itself: a confirmation that arrives is the trigger. To watch it, prepare a second folder without running any case (a document a folder has seen once counts as a duplicate afterwards, so the `demo` folder above would skip the made-up confirmations), start the loop, and drop a confirmation into its inbox from another terminal:

```bash
python3 -m aid.demo --out live --prepare-only
python3 -m aid.runner --settings live/settings.json --poll-seconds 5
cp aid/madeup/out/MU03_conf.pdf aid/madeup/out/MU07_conf.pdf live/inbox/      # in a second terminal
```

Within one pass the two files move to `live/inbox/processed/`, two case folders appear under `live/cases/` (PO 250103 closed with its prepared changes, PO 250107 for review), the questions whose wait is over are escalated, and when the digest is due (`digest_time` in the settings; set `"digest": "per_case"` to get one after every pass with a case) the digest is written to `live/cases/digests/` and, as an .eml, to `live/cases/outbox/`. Create the file `live/STOP` to end the loop. On the company side the mailbox rule does the copying. The other commands take made-up inputs the same way: the price-list import (`python3 -m aid.pricelist --file <list.csv> --supplier S01 --current <current_prices.csv> --prepared demo/cases/prepared --settings demo/settings.json`), the necessity check (`python3 -m aid.evalscripts.necessity --cases aid/madeup/out --spec aid/madeup/spec --recorded aid/tests/fixtures/recorded --out demo/necessity.csv`) and the freeze record (`python3 -m aid.freeze --settings demo/settings.json --out demo/freeze.json`).

The pre-check in the browser works the same way on a prepared folder. The demo's settings hold a made-up capture token, so the capture server starts on them, and `--testshop` makes it serve the two made-up cart pages as a test shop:

```bash
python3 -m aid.capture_server --settings live/settings.json --testshop
```

Load the browser extension of `aid/extension/` unpacked in Edge or Chrome, put the token of `live/settings.json` (`capture_token`) in its options and open http://127.0.0.1:7870/testshop/CART02.html: within about a second an amber banner at the top of the page names the PO line that is not in the cart and the price difference, with a link to the case on the review page (stop the review page of `demo/` and start `python3 -m aid.review --settings live/settings.json`, so that the link finds the case), and CART01 gives a green banner. A reload answers from the same case. The extension sends the cart page to the server on this computer only; [extension/README.md](extension/README.md) describes the options and the check by hand.

With your own Anthropic key, the demo can read the made-up documents live through the development client: put the key in the environment (`export ANTHROPIC_API_KEY=...` on macOS or Linux, `$env:ANTHROPIC_API_KEY="..."` in PowerShell; never in a file of the repository or the demo folder) and run `python3 -m aid.demo --out demo3 --reader anthropic`. Every confirmation and the cart screenshot then go to the service, the decider is asked where the rules call for it, and the audit of each case names the model; a run over the 22 cases costs a small amount. This option is for made-up documents only: a company document goes to Microsoft 365 Copilot through the work sign-in and to no other service, so the runner (the company-side command) has no such option.

What the demo does not do: write to Exact (the Exact writer stays behind its fake until Remy gives a route to the test-account copy) or send mail (the SMTP mailer needs the aid's mailbox). Setting `authorization_limit` in `demo/settings.json` turns the re-approval rule on for the made-up POs above it. For the AI service on company documents, the runner uses Microsoft 365 Copilot through the user's own work sign-in ([The AI service](#the-ai-service)); it needs a Copilot licence on the account, seven permissions on the app registration and an administrator's consent for four of them, so the demo does not use it.

## Layout

One module per step of a case, in the order a case goes through them: `sources/` (mailbox.py: the watched folder; microsoft_mail.py: the work mailbox, read through Microsoft Graph into the watched folder; upload.py: a case from files; capture.py: a case from a saved cart page), `readers/` (po_pdf.py: the PO PDF; supplier_parser.py: the template parser; supplier_llm.py with llm_client.py: the LLM reader; copilot_client.py: Microsoft 365 Copilot, the AI service, behind the LLM reader's client interface; cart.py: a saved cart page; pdf_text.py: the text layer), po_store.py (the PO versions), versioning.py (which version the document answers), matching.py (document lines to PO lines), comparison.py (the scoring manual's rules), routing.py (the settings table, R01 to R11), execution/ (ops.py: the write operations; writer.py: the prepared writer, the retrying writer, the write log and the undo; exact_api.py: the Exact writer), messaging/ (the supplier questions, the re-approval request, the mailers, the reply wait; microsoft_mailer.py: sending through the work mailbox), audit.py (the audit record), pipeline.py (run_case, the dispositions, the escalation), batch.py (headless runs over a case list), notify.py (the digest), review/ (the review page), capture_server.py with extension/ (the pre-check of a webshop cart in the browser), runner.py (the loop both hosts run), pricelist.py (the price-list import), evalscripts/ (the reading scorer, the bake-off, the necessity check), freeze.py (the freeze record), madeup/ (the made-up document set) and colab/ (the notebook for the made-up set). Beside them stand connections/ (secrets.py: the protected store of the sign-in's tokens on the PC; microsoft.py: the Microsoft sign-in), decider.py (the open questions the rules leave, such as which PO version a document answers), schema.py (the document model and its normalisers), case.py (the case folder: what each module writes into it is listed in its docstring) and settings.py (the settings model). The tests live in tests/, with the recorded LLM answers under tests/fixtures/recorded.

The commands, from the repository root, with their required options:

```bash
python3 -m aid.batch --cases <cases.csv> --po-dir <folder> --settings <file> --reader parser|recorded [--recorded-dir <folder>] --out <summary.csv>
python3 -m aid.runner --settings <file> [--poll-seconds N] [--max-passes N]
python3 -m aid.review --settings <file> [--share]
python3 -m aid.capture_server --settings <file> [--host 127.0.0.1] [--port 7870] [--testshop]
python3 -m aid.pricelist --file <price list .csv or .xlsx> --supplier <supplier> --current <prices.csv> --prepared <folder>
python3 -m aid.evalscripts.necessity --cases <folder> --spec <folder> --recorded <folder> [--out <file>]
python3 -m aid.freeze --settings <file> [--out <file>]
python3 -m aid.evalscripts.bakeoff --models <model id> [<model id> ...] [--estimate-only]
```

`--reader recorded` of the batch needs `--recorded-dir`. Each command's `--help` lists its other options.

## Settings

Everything the buyer controls is a setting: the price thresholds, the supplier lists, the wait for a supplier's answer, the digest, the language and the folders. [settings.example.json](settings.example.json) lists every setting with its default, and `settings.py` holds the model with the allowed values. A key the model does not know is refused, so a misspelled setting stops the aid instead of being ignored.

Copy the example to a `settings.json` outside the repository and fill it in. These values live only there:

- the real e-mail addresses of the buyer, the aid's mailbox and the approver (the example holds `example.com` addresses), and the buyer's name that signs the aid's messages (`buyer_name`, empty in the example);
- the buyer's authorization limit, the PO total above which a changed PO goes back to the approver (the example holds `null`);
- the supplier lists, the lead times and the suppliers' e-mail addresses to which the aid sends its questions (`supplier_emails`), which name real suppliers;
- the capture token (`capture_token`, `null` in the example), which the browser extension sends to the capture server (`python3 -m aid.capture_server`) and without which the server does not start; the extension's options hold the same value.

`ms_tenant_id`, `ms_client_id`, `mailbox`, `mail_folder`, `mail_folder_name` and `send_via` connect the work mailbox through the user's own Microsoft sign-in (`aid/connections/`, `aid/sources/microsoft_mail.py`, `aid/messaging/microsoft_mailer.py`). In the example they are `null`, and `send_via` is `"auto"`: the aid then works as before, from the watched folder. They are filled in on the settings tab of the review page: the Directory (tenant) ID and the Application (client) ID of an app registration in Microsoft Entra ID (identifiers, no secrets), the address of the mailbox to read and send from (`null`: the signed-in account's own; another mailbox needs the shared permissions of the registration and access in Exchange), and the id and the display name of the folder that the runner reads at each pass (`null`: no mailbox is read). `send_via` says how messages go out: `"microsoft"` through that mailbox, `"smtp"`, `"file"`, or `"auto"`, which, with a folder set, is the mailbox whenever an account is signed in at the time of the message, else SMTP when it is set up, else files. While the sign-in has lapsed, `"auto"` and `"microsoft"` send no question (it goes to the buyer as "message not sent") and the digest is written into `cases/outbox/`. The sign-in's tokens are kept in a protected store on the PC, never in the settings file or a repository. While a tenant or a folder is set, the review page's `--share` is refused. The manual's section 3.14 gives the steps of the app registration and of the connection.

`reader` says how the documents are read: `"llm"`, by Microsoft 365 Copilot through the same Microsoft sign-in, which needs `ms_tenant_id` and `ms_client_id` (the runner refuses `"llm"` without them), or `"parser"`, by the template parser, without AI. The settings tab sets it ("Documenten lezen met"); the manual's section 3.15 gives what Copilot needs.

`exact_base_url`, `exact_endpoints` and `exact_fields` give the Exact API that the aid writes its changes to (`aid/execution/exact_api.py`): its address (`null`: the aid only prepares its changes), the path of each endpoint, and the Exact field name of each kind of change whose field differs from the writer's default. `smtp_host`, `smtp_port` and `smtp_user` give the mail server and the login of the aid's mailbox (`null`: the messages are written as `.eml` files instead of sent). The Exact token and the mailbox password are no settings: the runner reads them from the environment variables `AID_EXACT_TOKEN` and `AID_MAIL_PASSWORD`.

Relative paths in the file resolve against the folder that holds the settings file, not against the working directory. The credential of an AI service is not a setting: a client reads it from the environment or from the platform's secret store, never from the settings file or from code (see [The AI service](#the-ai-service)).

`precheck_suppliers` is reserved and has no effect yet: no module reads it. The cart pre-check runs through the batch (`python3 -m aid.batch`, a row of kind `cart` in its cases file), which files the ERP exports that give a cart its PO side, or through the capture server for the browser extension.

`export_columns` maps the aid's field names of a PO line to the column names of the ERP export, which gives a cart pre-check its PO side because no PO PDF exists before ordering. Each key is one of `po_number`, `line_id`, `item_code`, `description`, `quantity`, `unit`, `unit_price`, `price_basis` and `delivery_date`, and its value is the name of the export's column that holds that field. The map must name `po_number` and `line_id`; a field it leaves out is missing on the PO. The batch runner files every `*_export.csv` of its `--po-dir` with this map, and an empty map, the default, means that the export names each column after its field, as the made-up exports do (`aid/madeup/out/CART02_export.csv`).

## The AI service

The AI service of the aid is a Microsoft service of the Copilot family in the company's tenant. The company supervisor stipulated that the AI platform must be Copilot and that the data are for internal use only ([recorded feedback of 30 October 2026](../docs/meetings/Johan_Feedback_Recorded_2026-10-30.md)). Yijie decided on 2 November 2026 that any AI service of Microsoft's Copilot family is acceptable, and on 8 November 2026 the company's AI service was named as Microsoft 365 Copilot. The model sits behind one interface, `LlmClient`, so the service can change without changing the modules that use it.

- Company documents are processed only through Microsoft 365 Copilot (`CopilotClient`, `aid/readers/copilot_client.py`), used with the user's own work sign-in through the Copilot Chat API of Microsoft Graph. The client sends the text layer of a PDF with its lines numbered, and the reader checks every value against the page, so a value Copilot takes from elsewhere is marked uncertain; web grounding is switched off. A scan has no text layer and is not read, nor is a PDF with a scanned page (a blank page is left out): its case is listed as failed in the digest. The Chat API is a preview of Microsoft, in Graph's beta endpoint, which Microsoft does not support for production use. No credential of the AI service is a setting: the sign-in's token is kept in the protected store on the PC. The manual's section 3.15 gives what Copilot needs and how to connect it.
- Made-up documents contain no company data and may be processed anywhere, in Google Colab or locally. The Anthropic client (`AnthropicClient`) is the development client for made-up documents only: the SDK reads `ANTHROPIC_API_KEY` from the environment (in Google Colab, from Colab Secrets).

No company document, no content taken from one and no identifying metadata goes to any other AI service (the Anthropic client included) or to Google (Colab, Drive). Runs on company documents are hosted on the company side, in the company's Microsoft environment or on a company PC that the company designates. Google Colab with a Drive folder is kept for the made-up set only. The runner for the company side is described in [Running on the company side](#running-on-the-company-side); its host and folder follow the company's answer.

## Running on the company side

The runner (`aid/runner.py`) runs the aid as a loop: every 60 seconds it handles the watched folder, runs each new case, ends the wait of the questions that bounced or had no answer in time and makes the digest when it is due, until a file `STOP` exists next to the settings file. In the watched folder an e-mail that answers a question of the aid (its In-Reply-To or References header names the Message-ID of a question the aid sent, not of a re-approval request) is filed into that case under `messages/replies/`, `messages/bounces/` or `messages/auto/`, and opens no case: a reply puts the case in review with the item "supplier answered: read the reply" and reports it in the next digest, so that the buyer reads it and decides, and a bounce ends the wait at once. Instead of the mailbox rule, the runner can read a folder of the work mailbox itself after a Microsoft sign-in on the settings tab of the review page, and saves each new e-mail into the watched folder at the start of a pass (MANUAL.md, section 3.14); it reads the settings file again when the tab saves it. A case whose run was cut off after a write or a message is not run again, since that would repeat them: it goes to review with the item "run cut off after writes: check the write log". Errors go to `runner.log` under `cases_root`, each once in a run of the loop, and the loop goes on; a settings file that does not validate stops the command with exit code 2 and the fields at fault, never their values. Its command is

```bash
python3 -m aid.runner --settings <root>/settings.json [--poll-seconds N] [--max-passes N]
```

for the folder layout `<root>/{settings.json, inbox/, cases/, po_store/, STOP}`; prepared changes go to `cases/prepared/`, and messages written instead of sent to `cases/outbox/`. With `"reader": "llm"` it reads through Microsoft 365 Copilot with the work sign-in, and it refuses `"llm"` with exit code 2 while the settings lack the tenant and the client; with `"reader": "parser"` it reads with the template parser. A refusal or an outage of Copilot while a case runs leaves the case without a result, logged once, for a later pass. Only a failure of Copilot as a whole makes the other cases of that pass wait too, and the digest then says so in one line with the number of cases waiting; a refusal of one document holds back no other case, and a document Copilot keeps failing is finished as failed after three failures. It writes through the Exact API when `exact_base_url` is set, with the token from `AID_EXACT_TOKEN`, and else prepares the changes; it sends through the aid's mailbox when `smtp_host` and `smtp_user` are set and `AID_MAIL_PASSWORD` is in the environment, and else writes the messages as files. The settings file with the real values lives in `<root>` only.

What waits on the company's answer (Remy): the host (a company PC or the company's Microsoft environment), how the runner is started and kept running there, the folder `<root>`, where the Exact token and the mailbox password are kept, and the mailbox rule that puts the suppliers' e-mails for Yijie's company mailbox into `inbox/`. The first run on documents of the test-account copy also needs Johan's written OK. The review page is started on its own, on the host, with `python3 -m aid.review --settings <file> [--share]`, and is served on 127.0.0.1 of the host only, so it is opened in a browser on that machine. `--share` opens a public share link for the made-up set only: it is refused with company settings (exit code 2). Google Colab runs the made-up set only ([aid/colab](colab/README.md)).

## Teams via Power Automate

The aid e-mails its digest from its own mailbox (`aid_email`), or from the work mailbox when it sends through it (MANUAL.md, section 3.14), to the buyer (`buyer_email`): once a day from `digest_time` on, or per case when `digest` is `per_case` (`aid/notify.py`). The digest lists the cases the aid handled, with an undo link next to each write it made, and a link to the review page (`review_url`) for each case that waits for the buyer. A Power Automate flow outside the code relays the e-mail to the buyer in Teams, so that no AI service is connected to Teams. The flow has a trigger and an action:

- Trigger: a new e-mail whose subject starts with `[Inkoopassistent]`, from the sender the digest has (`aid_email`, or the work mailbox's address when the aid sends through it), in the mailbox that receives the digest ("When a new email arrives" of the Office 365 Outlook connector, with the sender and the subject filter set). The subject is "[Inkoopassistent] Dagoverzicht DD-MM-YYYY", or "[Inkoopassistent] Daily digest DD-MM-YYYY" in English. The prefix is the same in both languages, so the trigger works whatever the language setting.
- Action: post a message to the buyer in Teams with the e-mail's text ("Post message in a chat or channel" of the Microsoft Teams connector, posted by the Flow bot in a chat with the buyer), or an adaptive card built from the HTML version of the digest ("Post card in a chat or channel"). `send_digest` sends only the text, as the e-mail's body. The HTML version, with the same content as headings, lists and links (`Digest.html` of `aid/notify.py`), is not sent: the runner writes it to `cases/digests/<day>.html`, with or without a mailer, for a flow to pick up and build the card from. The runner sets up no flow.

If the flow is not allowed, the e-mail itself is the notification.
