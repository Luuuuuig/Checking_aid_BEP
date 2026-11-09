# Recorded answers of the LLM reader

Each JSON file here is one answer to one request of the LLM reader (`aid/readers/supplier_llm.py`). The file is named by the request's digest (`LlmRequest.digest()`), the sha256 of the system prompt, the user text, the reading schema and the bytes of the document. `RecordedClient` replays the files, so the tests read documents through the LLM reader without calling any AI service. A file holds the fields of `LlmResponse`: the answer's `text` and its `model`, token counts, seconds, cost, request id and attempts.

## These answers are synthetic

No credential for an AI service was available when the reader was built (29 October 2026). The answers were therefore written by `aid/tests/fixtures/make_synthetic_recorded.py`, not by a model:

- each is the perfect reading of its document, built from the spec by `answer_from_spec` in `aid/tests/helpers.py`;
- each is in the reading schema the model answers in;
- each is filed under the digest of the request the reader sends for that document.

Replaying them tests the reader's own path: the request and its digest, the recorded file, the parsing, the normalisation and the source check. It does not measure a model. Their `model` is `synthetic`, and their token counts and seconds are 0.

The documents are the made-up set only (`aid/madeup/out`): the 20 confirmations (`*_conf.pdf`) and the cart screenshot `CART02.png`. No company document is ever recorded here.

## Which AI service records them

Change of 2 November 2026: the AI service of the aid is a Microsoft service of the Copilot family in the company's tenant (Johan's stipulation, recorded on 30 October in `docs/meetings/Johan_Feedback_Recorded_2026-10-30.md`, and Yijie's decision of 2 November). No company document, no content taken from one and no identifying metadata goes to any other AI service or to Google (Colab, Drive). Made-up documents contain no company data and may be processed anywhere. The model stays behind the `LlmClient` interface; the Anthropic client already in the code is the development client for made-up documents only. Since 8 November 2026 the company's AI service is Microsoft 365 Copilot, and its client is `CopilotClient` (`aid/readers/copilot_client.py`). No recording option for it exists yet, so the synthetic answers above stay the fixtures: no answer of another service is committed here, and a recording made with the development client for a local experiment stays outside the repository.

## Replacing them with the real recording

Once the record command can use the Copilot client, the synthetic answers are replaced with Copilot's answers on the made-up documents, recorded with the packages of `aid/requirements.txt` and the Copilot sign-in of the settings tab. Copilot reads the text layer only, so it cannot record the cart screenshot `CART02.png` or the two scans: a Copilot recording holds at most the answers of the 18 confirmations with a text layer, and the replay test then has to expect those. The digest covers the schema that pydantic generates, so another pydantic version can change the digests.

1. Delete the synthetic answers: `rm aid/tests/fixtures/recorded/*.json`. Any answer left in the folder is replayed and not asked again, because the digest names neither the client nor the model.
2. Record, on the made-up set only, once the record command has an option for the Copilot client (not built yet: today `python3 -m aid.readers.supplier_llm --record` uses the development client only). The command prints one line per document with the model, the tokens and the seconds.
3. Run the tests. `test_recorded_set_reads_all` replays the 21 synthetic answers and asserts a field accuracy of at least 0.9; for a Copilot recording it has to expect the answers Copilot can give.
4. Commit the new files, and replace the section "These answers are synthetic" above with the date, the client, the model and, where the client has one, the effort of the recording; keep the section "Which AI service records them".

## When the digests change

A change to the prompt (`aid/readers/prompts/read_document.md`), the user text, the reading schema or a document changes the digests. The replay then fails with a `KeyError` that names the missing file, until the answers are made again. While they are synthetic, run `python3 -m aid.tests.fixtures.make_synthetic_recorded`, which also deletes the synthetic answers it no longer writes. Once Copilot has made them, record them again as above.
