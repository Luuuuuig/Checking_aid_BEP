# The model bake-off: results

This folder holds the result files of the model bake-off, one CSV per run (`bakeoff_<date>.csv`), and the recorded answers of a run made with `--record`. The bake-off has each candidate model read the made-up document set (`aid/madeup/out`: the 20 order confirmations and the cart screenshot `CART02.png`), scores every reading against the case specs (`aid/madeup/spec`) and chooses the model on price and performance. The candidates are the models that the company's AI platform offers (see [Running it](#running-it)). The command is `aid/evalscripts/bakeoff.py`, the scorer is `aid/evalscripts/score_reading.py`.

No company document is read, scored or recorded here. The set is made up, and so are the answers recorded on it.

## What is scored

For each model and document, the reading is compared with the spec of the case, field by field:

- **Fields.** The header has seven fields (`po_reference`, `version_hint`, `supplier`, `document_date`, `currency`, `incoterm`, `capture_date`) and each line nine (`item_code`, `description`, `quantity`, `unit`, `unit_price`, `price_basis`, `discount_pct`, `net_price`, `delivery_date`). A field is right when the model's normalised value equals the spec's. A value the document does not print is right when the model leaves it missing and wrong when the model makes one up. A line the model leaves out makes all its fields wrong, and a line the document does not have is one error.
- **Sources.** Every value is read with the page and line it stands on. Where the document has a text layer (a PDF that is not a scan), a source is right when the cited line exists and holds the text the model gives for the value. Only the values the document prints and the model got right are checked. A scan and the screenshot have no text lines to check against, so their sources are not scored.

A document the model could not read at all (it refused, was cut off, or answered unusably twice) has a row with the error and no field right.

## The columns

| Column | Meaning |
|---|---|
| `model` | The model id given with `--models`. |
| `case` | The case of the document: `MU01` to `MU20`, `CART02`. |
| `kind` | `confirmation` for a `*_conf.pdf`, `cart` for the screenshot. |
| `fields_total` | The fields scored: the header and line fields the spec lists, and one more for each extra line the model made. |
| `fields_correct` | How many of them are right. |
| `field_accuracy` | `fields_correct` divided by `fields_total`, from 0 to 1. |
| `sources_total` | The right values whose source is checked. 0 for a scan, for the screenshot and for a document that could not be read. |
| `sources_correct` | How many of those cite a line that holds the value's text. |
| `source_accuracy` | `sources_correct` divided by `sources_total`. Blank when `sources_total` is 0. |
| `input_tokens`, `output_tokens` | The tokens the document's request(s) used. After a retry, both requests together. |
| `cost` | The cost in euro of those tokens at the price given with `--price`. Blank for a model without a price. |
| `seconds` | The time the model took to answer (both requests after a retry), from sending the request to the end of the answer. Reading the file, building the request and starting the SDK client are not in it. |
| `attempts` | The requests sent: 1, or 2 when the first answer was unusable and the model was asked again. |
| `error` | Why the document could not be read; blank when it was. |

## The decision

The command prints a summary per model and then the decision:

- **Field accuracy** of a model is the mean over all its documents, an unread document counting as 0.
- **Source accuracy** is the mean over the documents where it can be measured. It says how well a model cites what it read right; a model that fails to read a document loses field accuracy, not source accuracy.
- **Cost per case** is the mean cost of a document, and the total cost is the sum.

The chosen model is **the cheapest model whose field accuracy and source accuracy are both within 2 percentage points (0.02) of the best value of each**. Exactly 2 points below the best is still within. Of two models at the same cost, the one named first on the command line wins. The best field accuracy and the best source accuracy may belong to different models.

There is no decision when a model has no price (there is no cost to compare), and none when no model is within 2 points of both bests; the command says which and leaves the choice to the person reading the summary.

## Running it

Change of 2 November 2026: the AI service of the aid is a Microsoft service of the Copilot family in the company's tenant (Johan's stipulation, recorded on 30 October in the [feedback record](../../../docs/meetings/Johan_Feedback_Recorded_2026-10-30.md), and Yijie's decision of 2 November). The bake-off therefore runs on the models that the company's platform offers. Remy, the IT manager, lists the models, names the exact API route (for a Python tool usually the Azure OpenAI service in the company's tenant) and provides the credential, which is used where Remy allows. The credential is never put in a file, on the command line or in a recording. The measures and the decision rule below do not change.

The run needs the Microsoft client of Task 7b of the [implementation plan](../../../docs/superpowers/plans/2026-10-26-checking-aid-implementation.md). Until Task 7b lands, the command builds only the Anthropic client, which is the development client for made-up documents only (the SDK reads its key from `ANTHROPIC_API_KEY` in the environment, in Colab from Colab Secrets). A run with it only tests the script on the made-up set: it is not the bake-off of the plan and it does not choose the model of the aid. Give such a run `--out` and `--record` in a scratch folder outside the repository, and do not commit it. Task 7b adds the options `--client anthropic|microsoft` (default `microsoft` once that client exists; `anthropic` is refused for a set outside `aid/madeup/`) and `--endpoint`, which the commands below use. The documents are the made-up set. No company document, no content taken from one and no identifying metadata goes to any other AI service (the Anthropic client included) or to Google (Colab, Drive).

**1. The estimate.** It asks the service to count the input tokens of one request per model (the first document of the set) and stops. No model reads the document and no answer is asked for. With the Microsoft client the count is the service's own where it offers one, otherwise a local estimate, printed as such. Replace `ENDPOINT` with the endpoint that Remy names, and `MODEL-A` and `MODEL-B` with the models of the platform that Remy lists, one name for each model that is run.

```bash
python3 -m aid.evalscripts.bakeoff --client microsoft --endpoint ENDPOINT --models MODEL-A MODEL-B --estimate-only
```

The full run reads the 21 documents of the set with each model, one request each, and a second request for an answer that has to be asked again. The answers add output tokens, up to the token limit of each request (32000 today; the estimate prints the limit it uses). So the input cost of the run is about 21 times the counted tokens times the price of an input token, and the answers come on top. Yijie approves the estimate before the full run.

**2. The full run.** Give one `--price` for each model in `--models`, and replace `IN` and `OUT` with the model's price in euro per million input tokens and per million output tokens.

```bash
python3 -m aid.evalscripts.bakeoff --client microsoft --endpoint ENDPOINT --models MODEL-A MODEL-B \
    --price MODEL-A=IN,OUT --price MODEL-B=IN,OUT \
    --record aid/evalscripts/results/recorded
```

**The prices come from the price page of the company's AI platform on the day of the run, and are typed on the command line. They are never taken from memory, and the command holds none.** Note the day and the page the prices came from with the result. A price in dollars is converted at the rate of that day, and the rate is noted too. A `--price` for a model that is not in `--models` is refused before anything is read: it is nearly always a misspelt name, which would leave the model that does run without a price.

The result goes to `aid/evalscripts/results/bakeoff_<date>.csv` (the date of the run) unless `--out` says otherwise. A second run on the same day overwrites the file of the first, so move it or give `--out`. The run writes each row as it is made, so a run that stops leaves the rows it had.

`--set` and `--spec` default to `aid/madeup/out` and `aid/madeup/spec`. Point them at made-up documents only: a company document is not read with this command.

## Recorded answers

`--record DIR` keeps the answers of each model in `DIR/<model>/`, one JSON file per request (the same files `RecordedClient` replays in the tests). A second run with the same `DIR` replays them and asks the model nothing, so a run can be repeated, or finished after a stop, without paying twice. If the run stops on a service error (no connection, a rate limit), run the same command again: the answers already recorded are replayed and only the rest is asked. A document whose answer was refused or cut off is not recorded (the client raises before the recording is made), so the rerun asks the model again for it, and a cut-off answer can cost up to the token limit of the request each time.

- A recording holds the cost it was made with. Answers recorded without `--price` get the cost of the price given on the replay, worked out from their tokens.
- An unusable answer is recorded too and replays as the same failure. To ask again, delete the files of that model.
- The recorded answers are answers on made-up documents only. They may be committed with the result.

# The AI-necessity check: results

This folder also holds the result files of the AI-necessity check, one CSV per run (`necessity_<date>.csv`). The check answers whether the aid needs the LLM reader at all, or whether the parser baseline (the layout templates of `aid/readers/supplier_parser.py`) reads the confirmations well enough. The command is `aid/evalscripts/necessity.py`; it scores with the same scorer as the bake-off.

```bash
python3 -m aid.evalscripts.necessity --cases CASES --spec SPECS --recorded RECORDED [--out FILE]
```

For every `*_conf.pdf` of `--cases`, both readers read the confirmation: the parser, and the LLM reader through `RecordedClient(--recorded)`, which replays the recorded answers. Each reading is scored against the spec of the case (`<--spec>/<case>.json`) as above. A confirmation that a reader cannot read has every field wrong and the error in its row, and the run goes on: for the parser, a PDF without a readable text layer, no template that fits or a template that does not fit after all; for the LLM reader, a `ReaderError` (its text), a request with no recorded answer (`no recorded answer`) or any other failure (`<class>: <message>`). A case without a spec, with a spec that does not parse or with the spec of a cart stops the run with a message naming the file, before any row is written. The result goes to `aid/evalscripts/results/necessity_<date>.csv` (the date of the run) unless `--out` says otherwise. The rows are written to `<name>.partial` beside it as they are made, and the file gets its name only when the run is complete.

| Column | Meaning |
|---|---|
| `case` | The case: the file name without `_conf.pdf`. |
| `layout` | The layout of the confirmation, from the spec: `table_nl`, `table_de`, `text_en`, `merged`, `total_only` or `scan`. |
| `reader` | `parser` or `llm`. One row per case and reader. |
| `fields_total`, `fields_correct`, `field_accuracy` | As in the bake-off. |
| `errors` | `fields_total` minus `fields_correct`: the field errors of the reading. |
| `error` | Why the reader could not read the confirmation; blank when it could. |

The summary gives, per layout, the field errors of the parser and of the LLM reader, the totals over all cases and the decision: **`keep_llm` when the LLM reader makes fewer than half the parser's field errors, `parser_only` otherwise** (also when neither makes an error). The decision goes into the settings (`reader: "llm"` or `"parser"`) and the status guide; with `parser_only` the thesis reports that AI was not needed for reading.

**The check runs on the development cases.** They are company documents, so the LLM reader reads them only through the Microsoft client of Task 7b, on the company side (Task 21), and never through the Anthropic client or in Colab; their result files stay on the company side. A run on the made-up set (`--cases aid/madeup/out --spec aid/madeup/spec --recorded aid/tests/fixtures/recorded`) only tests the command: it is development evidence, not the check, and the summary says so when every case's spec is the made-up spec of that case. On the made-up set with the synthetic recorded answers, the parser fails on the two scans and the LLM reader reads every case, so the decision there is `keep_llm`.
