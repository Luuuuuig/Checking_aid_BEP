"""Tests of the model bake-off command (aid/evalscripts/bakeoff.py): the result file, the summary and the decision, --price,
--estimate-only and --record. The models are fakes, or the SDK is a stub: no test calls an AI service or the network."""
import csv
import json
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest

from aid.evalscripts.bakeoff import client_for_model, main
from aid.evalscripts.score_reading import HEADER_FIELDS, LINE_FIELDS
from aid.readers import exploration, llm_client
from aid.readers.copilot_client import CopilotError
from aid.readers.llm_client import LlmResponse, ReaderError
from aid.readers.supplier_llm import reading_request
from aid.tests.helpers import answer_from_spec

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "aid" / "madeup" / "spec"


# --- the bake-off command ---

COLUMNS = ["model", "case", "kind", "fields_total", "fields_correct", "field_accuracy", "sources_total", "sources_correct", "source_accuracy",
           "input_tokens", "output_tokens", "cost", "seconds", "attempts", "error"]
ANSWERS = {True: answer_from_spec("MU01"), False: answer_from_spec("CART02", "cart")}      # by "the request holds a PDF": a confirmation or a cart
PRICES = ["--price", "model-a=10,50", "--price", "model-b=2,10", "--price", "model-c=1,5"]     # made up; per request 0.02, 0.004, 0.002 EUR
PRICES_A_B = PRICES[:4]


class FakeClient:
    """A model for the bake-off. It reads MU01 and CART02 as their specs say, except that `mistakes` spoils that many quantities of
    MU01, `cites` False leaves out every source, `refuses_carts` has it refuse the cart with a ReaderError, and `stumbles` makes its first
    answer to each document unusable. Each request takes 1000 input tokens, 200 output tokens and 2 s, and costs what the price
    given to the factory says (nothing is known without a price)."""

    def __init__(self, name, price, mistakes=0, cites=True, refuses_carts=False, stumbles=False, counts=5000):
        self.name, self.price, self.counts = name, price, counts
        self.mistakes, self.cites, self.refuses_carts, self.stumbles = mistakes, cites, refuses_carts, stumbles
        self.calls, self.counted, self.seen = [], [], set()

    def cost(self, input_tokens, output_tokens):
        return None if self.price is None else (input_tokens * self.price[0] + output_tokens * self.price[1]) / 1_000_000

    def complete(self, req):
        self.calls.append(req)
        confirmation, document = req.pdf is not None, (req.pdf, tuple(req.images))
        first_time = document not in self.seen
        self.seen.add(document)
        if self.refuses_carts and not confirmation:
            raise ReaderError("the model refused to answer", LlmResponse(text="", model=self.name, input_tokens=1000, output_tokens=0,
                                                                        seconds=1.0, cost_eur=self.cost(1000, 0)), attempts=1)
        text = "not json" if self.stumbles and first_time else self.answer(confirmation)
        return LlmResponse(text=text, model=self.name, input_tokens=1000, output_tokens=200, seconds=2.0, cost_eur=self.cost(1000, 200))

    def answer(self, confirmation):
        reading = json.loads(ANSWERS[confirmation])
        if confirmation:
            for line in reading["lines"][:self.mistakes]:
                line["quantity"]["text"] = "999"
        if not self.cites:
            for value in [reading[name] for name in HEADER_FIELDS] + [line[name] for line in reading["lines"] for name in LINE_FIELDS]:
                value["source"] = None
        return json.dumps(reading)

    def count_tokens(self, req):
        self.counted.append(req)
        return self.counts


class Models:
    """The client factory of the tests: one FakeClient per model, with the options given for that model; it remembers the clients
    it built and the prices it was given."""

    def __init__(self, **options):
        self.options, self.clients, self.prices = options, {}, {}

    def __call__(self, model, price):
        self.prices[model] = price
        self.clients[model] = FakeClient(model, price, **self.options.get(model, {}))
        return self.clients[model]

    def calls(self):
        return {model: len(client.calls) for model, client in self.clients.items()}


@pytest.fixture
def two_documents(madeup_out, tmp_path):
    """A set of two documents: the confirmation MU01 (a PDF with a text layer) and the cart screenshot CART02."""
    folder = tmp_path / "set"
    folder.mkdir()
    for name in ("MU01_conf.pdf", "CART02.png"):
        shutil.copy(madeup_out / name, folder / name)
    return folder


def run(folder, out, factory, *args, models=("model-a",)):
    return main(["--models", *models, "--set", str(folder), "--spec", str(SPEC_DIR), "--out", str(out), *args], client_factory=factory)


def read_rows(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_a_run_scores_each_model_on_each_document_and_chooses_the_cheapest_within_two_points(two_documents, tmp_path, capsys):
    """model-a reads everything right and costs most, model-b misses one field of 59 and costs a fifth, model-c misses three and is
    cheapest: the rule picks model-b, as the brief's example does."""
    models, out = Models(**{"model-b": {"mistakes": 1}, "model-c": {"mistakes": 3}}), tmp_path / "result.csv"
    assert run(two_documents, out, models, *PRICES, models=("model-a", "model-b", "model-c")) == 0
    rows = read_rows(out)
    assert list(rows[0]) == COLUMNS
    assert [(r["model"], r["case"], r["kind"]) for r in rows] == [(model, case, kind) for model in ("model-a", "model-b", "model-c")
                                                                   for case, kind in (("MU01", "confirmation"), ("CART02", "cart"))]
    assert dict(rows[0]) == {"model": "model-a", "case": "MU01", "kind": "confirmation", "fields_total": "34", "fields_correct": "34",
                             "field_accuracy": "1.0000", "sources_total": "26", "sources_correct": "26", "source_accuracy": "1.0000",
                             "input_tokens": "1000", "output_tokens": "200", "cost": "0.02", "seconds": "2.00", "attempts": "1", "error": ""}
    assert dict(rows[1]) == {"model": "model-a", "case": "CART02", "kind": "cart", "fields_total": "25", "fields_correct": "25",
                             "field_accuracy": "1.0000", "sources_total": "0", "sources_correct": "0", "source_accuracy": "",       # no text lines
                             "input_tokens": "1000", "output_tokens": "200", "cost": "0.02", "seconds": "2.00", "attempts": "1", "error": ""}
    assert [(r["fields_correct"], r["field_accuracy"], r["sources_total"], r["cost"]) for r in rows[2:]] == [
        ("33", "0.9706", "25", "0.004"), ("25", "1.0000", "0", "0.004"), ("31", "0.9118", "23", "0.002"), ("25", "1.0000", "0", "0.002")]
    printed = capsys.readouterr().out
    assert "model-a: field accuracy 100.0%, source accuracy 100.0% (1 of 2 documents), cost per case EUR 0.0200, total EUR 0.0400" in printed
    assert "model-b: field accuracy 98.5%, source accuracy 100.0% (1 of 2 documents), cost per case EUR 0.0040, total EUR 0.0080" in printed
    assert "model-c: field accuracy 95.6%" in printed and "Decision: model-b" in printed and "within 2 percentage points" in printed


def test_a_model_that_cites_nothing_is_out_on_its_source_accuracy_alone(two_documents, tmp_path, capsys):
    """model-b reads every field right and is the cheapest, but its sources are all missing: 0 of the 26 on MU01."""
    models, out = Models(**{"model-b": {"cites": False}}), tmp_path / "result.csv"
    run(two_documents, out, models, *PRICES_A_B, models=("model-a", "model-b"))
    rows = read_rows(out)
    assert [(r["field_accuracy"], r["sources_correct"], r["source_accuracy"]) for r in rows[2:]] == [("1.0000", "0", "0.0000"), ("1.0000", "0", "")]
    printed = capsys.readouterr().out
    assert "model-b: field accuracy 100.0%, source accuracy 0.0%" in printed and "Decision: model-a" in printed


def test_a_tiny_cost_is_written_in_plain_digits(two_documents, tmp_path):
    out = tmp_path / "result.csv"
    run(two_documents, out, Models(), "--price", "model-a=0.0001,0.0001")             # 1.2E-7 euro for 1000 + 200 tokens
    assert {r["cost"] for r in read_rows(out)} == {"0.00000012"}


def test_the_progress_and_the_result_file_are_printed(two_documents, tmp_path, capsys):
    run(two_documents, tmp_path / "result.csv", Models(), "--price", "model-a=10,50")
    printed = capsys.readouterr().out
    assert "model-a MU01: 34 of 34 fields right, 1000 + 200 tokens, 2.0 s" in printed and "model-a CART02: 25 of 25 fields right" in printed
    assert "Decision: model-a" in printed and f"Results written to {tmp_path / 'result.csv'}" in printed


def test_a_retried_read_reports_both_requests(two_documents, tmp_path):
    """The first answer to each document is unusable, so the reader asks again: two attempts, and the tokens and the cost of both."""
    out = tmp_path / "result.csv"
    run(two_documents, out, Models(**{"model-a": {"stumbles": True}}), "--price", "model-a=10,50")
    assert [(r["fields_correct"], r["input_tokens"], r["output_tokens"], r["cost"], r["seconds"], r["attempts"]) for r in read_rows(out)] == [
        ("34", "2000", "400", "0.04", "4.00", "2"), ("25", "2000", "400", "0.04", "4.00", "2")]


def test_a_document_that_cannot_be_read_is_a_row_with_its_error_and_the_run_goes_on(two_documents, tmp_path, capsys):
    """The refused cart scores no field right; what the refused request cost still counts; the next model is asked all the same."""
    models, out = Models(**{"model-a": {"refuses_carts": True}}), tmp_path / "result.csv"
    assert run(two_documents, out, models, "--price", "model-a=10,50", "--price", "model-b=2,10", models=("model-a", "model-b")) == 0
    rows = read_rows(out)
    assert rows[0]["error"] == "" and rows[0]["fields_correct"] == "34"
    assert dict(rows[1]) == {"model": "model-a", "case": "CART02", "kind": "cart", "fields_total": "25", "fields_correct": "0",
                             "field_accuracy": "0.0000", "sources_total": "0", "sources_correct": "0", "source_accuracy": "",
                             "input_tokens": "1000", "output_tokens": "0", "cost": "0.01", "seconds": "1.00", "attempts": "1",
                             "error": "the model refused to answer"}
    assert [(r["model"], r["error"]) for r in rows[2:]] == [("model-b", ""), ("model-b", "")] and models.calls() == {"model-a": 2, "model-b": 2}
    printed = capsys.readouterr().out
    assert "model-a CART02: 0 of 25 fields right, 1000 + 0 tokens, 1.0 s, not read: the model refused to answer" in printed
    assert ("model-a: field accuracy 50.0%, source accuracy 100.0% (1 of 2 documents), cost per case EUR 0.0150, total EUR 0.0300, "
            "1 document not read") in printed


def test_a_failed_read_that_got_no_answer_has_no_usage(two_documents, tmp_path):
    class NoAnswer(FakeClient):
        def complete(self, req):
            raise ReaderError("no usable answer")
    out = tmp_path / "result.csv"
    run(two_documents, out, lambda model, price: NoAnswer(model, price), "--price", "model-a=10,50")
    assert [(r["fields_correct"], r["input_tokens"], r["output_tokens"], r["cost"], r["seconds"], r["attempts"], r["error"])
            for r in read_rows(out)] == [("0", "0", "0", "0", "0.00", "0", "no usable answer")] * 2


def test_a_model_without_a_price_has_no_cost_and_the_summary_says_so(two_documents, tmp_path, capsys):
    models, out = Models(), tmp_path / "result.csv"
    run(two_documents, out, models, "--price", "model-a=10,50", models=("model-a", "model-b"))
    assert models.prices == {"model-a": (Decimal("10"), Decimal("50")), "model-b": None}           # the factory is told which model has no price
    assert [(r["model"], r["cost"]) for r in read_rows(out)] == [("model-a", "0.02"), ("model-a", "0.02"), ("model-b", ""), ("model-b", "")]
    printed = capsys.readouterr().out
    assert "model-b: field accuracy 100.0%, source accuracy 100.0% (1 of 2 documents), cost per case unknown (no price given)" in printed
    assert "No decision" in printed and "model-b" in printed.split("No decision")[1] and "Decision:" not in printed


def test_no_decision_is_made_where_no_model_is_close_to_the_best_on_both_accuracies(two_documents, tmp_path, capsys):
    """model-a reads all the fields and cites nothing; model-b cites well and misses three fields."""
    models = Models(**{"model-a": {"cites": False}, "model-b": {"mistakes": 3}})
    assert run(two_documents, tmp_path / "result.csv", models, *PRICES_A_B, models=("model-a", "model-b")) == 0
    printed = capsys.readouterr().out
    assert "No decision: no model is within 2 percentage points of the best field accuracy and of the best source accuracy" in printed


@pytest.mark.parametrize(("args", "prices"), [
    (["--price", "model-a=10,50"], {"model-a": (Decimal("10"), Decimal("50"))}),
    (["--price", "model-a=0.8, 4.25", "--price", "model-b=1e1,2.50"],
     {"model-a": (Decimal("0.8"), Decimal("4.25")), "model-b": (Decimal("1E+1"), Decimal("2.50"))}),
    (["--price", "model-a=3,15", "--price", "model-a=4,20"], {"model-a": (Decimal("4"), Decimal("20"))}),                 # the last one counts
])
def test_a_price_is_euro_per_million_input_and_output_tokens(two_documents, tmp_path, args, prices):
    models = Models()
    run(two_documents, tmp_path / "result.csv", models, *args, models=tuple(prices))
    assert models.prices == prices


@pytest.mark.parametrize("price", ["model-a", "model-a=", "model-a=10", "model-a=10,", "model-a=,50", "=10,50", "model-a=ten,50", "model-a=10,-1",
                                   "model-a=nan,1", "model-a=10,inf", "model-a=1,2,3"])
def test_a_price_that_is_not_model_equals_in_comma_out_is_refused_before_anything_is_read(two_documents, tmp_path, capsys, price):
    models = Models()
    with pytest.raises(SystemExit) as raised:
        run(two_documents, tmp_path / "result.csv", models, "--price", price)
    assert raised.value.code == 2 and models.clients == {} and not (tmp_path / "result.csv").exists()
    assert "--price" in capsys.readouterr().err


@pytest.mark.parametrize("extra", [[], ["--estimate-only"]], ids=["a run", "an estimate"])
def test_a_price_for_a_model_that_is_not_run_is_refused_before_anything_is_read(two_documents, tmp_path, capsys, extra):
    """A misspelt model in --price would leave the model that does run without a price, and only the end of a paid run would say so."""
    models = Models()
    with pytest.raises(SystemExit) as raised:
        run(two_documents, tmp_path / "result.csv", models, "--price", "model-a=10,50", "--price", "model-aa=1,2", "--price", "model-c=3,4", *extra,
            models=("model-a", "model-b"))
    assert raised.value.code == 2 and models.clients == {} and not (tmp_path / "result.csv").exists()
    error = capsys.readouterr().err
    assert "--price names model-aa, model-c," in error and "--models (model-a model-b)" in error        # only the names that are not run


def test_estimate_only_prints_the_token_count_of_one_request_per_model_and_stops(two_documents, tmp_path, capsys):
    models, out = Models(**{"model-a": {"counts": 12345}, "model-b": {"counts": 6789}}), tmp_path / "result.csv"
    assert run(two_documents, out, models, "--estimate-only", models=("model-a", "model-b")) == 0
    printed = capsys.readouterr().out
    assert "model-a: 12345 input tokens for one request (MU01_conf.pdf); the full run reads 2 documents per model" in printed
    request = reading_request(two_documents / "MU01_conf.pdf", "confirmation")                   # the first document, as the run would send it
    assert "model-b: 6789 input tokens for one request (MU01_conf.pdf)" in printed and f"up to {request.max_tokens} per request" in printed
    assert [client.counted for client in models.clients.values()] == [[request], [request]]
    assert models.calls() == {"model-a": 0, "model-b": 0} and not out.exists()                    # no answer was asked for, no file written


def test_a_run_recorded_with_record_replays_without_calling_the_model(two_documents, tmp_path):
    record, out = tmp_path / "recorded", tmp_path / "result.csv"
    first = Models()
    run(two_documents, out, first, *PRICES_A_B, "--record", str(record), models=("model-a", "model-b"))
    answers = read_rows(out)
    assert first.calls() == {"model-a": 2, "model-b": 2}
    assert sorted(path.name for path in record.iterdir()) == ["model-a", "model-b"]                # one folder per model
    assert all(len(list((record / model).glob("*.json"))) == 2 for model in ("model-a", "model-b"))
    second = Models()
    run(two_documents, out, second, *PRICES_A_B, "--record", str(record), models=("model-a", "model-b"))
    assert second.calls() == {"model-a": 0, "model-b": 0} and read_rows(out) == answers          # the same rows, tokens, seconds and cost


@pytest.mark.parametrize(("stop_reason", "message"), [("refusal", "the model refused to answer"),
                                                      ("max_tokens", "the answer was cut off at max_tokens")])
def test_a_refused_or_cut_off_answer_is_not_recorded_so_a_rerun_asks_the_model_again(two_documents, tmp_path, monkeypatch, stop_reason, message):
    """The client raises before RecordedClient can record the answer, so a rerun replays the documents that were read and asks
    again for the one that was refused or cut off, which costs up to the token limit of the request each time."""
    streamed, limits, record = [], [], tmp_path / "recorded"
    reply = {"document": ANSWERS[True], "image": ANSWERS[False]}

    class Stream:
        def __init__(self, kind, limit): self.kind, self.limit = kind, limit
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get_final_message(self):
            stopped = self.kind == "image"                        # the cart is the document that is refused or cut off
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="" if stopped else reply[self.kind])], model="model-x",
                                   stop_reason=stop_reason if stopped else "end_turn", _request_id="req_1",
                                   usage=SimpleNamespace(input_tokens=1000, output_tokens=self.limit if stopped else 200))

    class Messages:
        def stream(self, **kw):
            streamed.append(kw["messages"][0]["content"][0]["type"])
            limits.append(kw["max_tokens"])
            return Stream(streamed[-1], kw["max_tokens"])

    monkeypatch.setattr("aid.readers.llm_client.anthropic.Anthropic", lambda: SimpleNamespace(messages=Messages()))
    out = tmp_path / "result.csv"
    argv = ["--models", "model-x", "--set", str(two_documents), "--spec", str(SPEC_DIR), "--out", str(out), "--record", str(record)]
    assert main(argv) == 0
    assert [(r["case"], r["error"], r["output_tokens"]) for r in read_rows(out)] == [("MU01", "", "200"), ("CART02", message, str(limits[1]))]
    assert streamed == ["document", "image"] and len(list((record / "model-x").glob("*.json"))) == 1       # only the confirmation is recorded
    assert main(argv) == 0
    assert streamed == ["document", "image", "image"]                       # the confirmation was replayed, the cart was asked again
    assert [(r["case"], r["error"], r["attempts"]) for r in read_rows(out)] == [("MU01", "", "1"), ("CART02", message, "1")]     # the same request


def test_a_service_error_ends_the_run_with_the_rows_so_far_and_a_rerun_finishes_it(two_documents, tmp_path, monkeypatch):
    """An SDK error (here a rate limit on the cart) is not an unreadable document, so it ends the run. The row made before it is in
    the result file, and with --record the rerun replays that document and asks only for the rest."""
    import httpx2                                            # the HTTP package of the SDK, which builds its errors
    streamed, state = [], {"limited": True}
    reply = {"document": ANSWERS[True], "image": ANSWERS[False]}
    refused = httpx2.Response(429, request=httpx2.Request("POST", "https://offline.invalid/v1/messages"))

    class Stream:
        def __init__(self, text): self.text = text
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get_final_message(self):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)], stop_reason="end_turn", model="model-x",
                                   _request_id="req_1", usage=SimpleNamespace(input_tokens=1000, output_tokens=200))

    class Messages:
        def stream(self, **kw):
            kind = kw["messages"][0]["content"][0]["type"]
            streamed.append(kind)
            if kind == "image" and state["limited"]:
                raise anthropic.RateLimitError("rate limited", response=refused, body=None)
            return Stream(reply[kind])

    monkeypatch.setattr("aid.readers.llm_client.anthropic.Anthropic", lambda: SimpleNamespace(messages=Messages()))
    out = tmp_path / "result.csv"
    argv = ["--models", "model-x", "--set", str(two_documents), "--spec", str(SPEC_DIR), "--out", str(out), "--record", str(tmp_path / "recorded")]
    with pytest.raises(anthropic.RateLimitError):
        main(argv)
    assert [(r["case"], r["fields_correct"]) for r in read_rows(out)] == [("MU01", "34")]
    state["limited"] = False                                   # the limit is over: the same command again
    assert main(argv) == 0
    assert streamed == ["document", "image", "image"]          # MU01 was replayed, only the cart was asked again
    assert [(r["case"], r["fields_correct"]) for r in read_rows(out)] == [("MU01", "34"), ("CART02", "25")]


def test_answers_recorded_without_a_price_get_the_cost_of_the_price_given_later(two_documents, tmp_path):
    """The recorded answers keep no cost when no price was given, so a replay works the cost out from the tokens at today's price."""
    record, out = tmp_path / "recorded", tmp_path / "result.csv"
    run(two_documents, out, Models(), "--record", str(record))
    assert [r["cost"] for r in read_rows(out)] == ["", ""]
    again = Models()
    run(two_documents, out, again, "--price", "model-a=10,50", "--record", str(record))
    assert again.calls() == {"model-a": 0} and [r["cost"] for r in read_rows(out)] == ["0.02", "0.02"]


def test_the_result_file_is_named_by_the_date_of_the_run_unless_out_says_otherwise(two_documents, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                                                                   # the default folder is relative
    monkeypatch.setattr("aid.evalscripts.bakeoff.today", lambda: date(2026, 11, 2))
    assert main(["--models", "model-a", "--set", str(two_documents), "--spec", str(SPEC_DIR)], client_factory=Models()) == 0
    assert [r["case"] for r in read_rows(tmp_path / "aid" / "evalscripts" / "results" / "bakeoff_2026-11-02.csv")] == ["MU01", "CART02"]


def test_a_document_without_a_spec_stops_the_run_before_any_model_is_asked(two_documents, tmp_path, capsys):
    specs = tmp_path / "specs"
    specs.mkdir()
    shutil.copy(SPEC_DIR / "MU01.json", specs / "MU01.json")                                      # CART02's spec is not there
    models = Models()
    with pytest.raises(SystemExit) as raised:
        main(["--models", "model-a", "--set", str(two_documents), "--spec", str(specs), "--out", str(tmp_path / "result.csv")],
             client_factory=models)
    assert raised.value.code == 2 and models.clients == {} and not (tmp_path / "result.csv").exists()
    assert "CART02" in capsys.readouterr().err


def test_a_set_without_documents_is_refused(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    with pytest.raises(SystemExit) as raised:
        main(["--models", "model-a", "--set", str(tmp_path / "empty"), "--estimate-only"], client_factory=Models())
    assert raised.value.code == 2 and "no documents" in capsys.readouterr().err


def test_the_help_names_the_options_and_the_candidate_models(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    help_text = capsys.readouterr().out
    options = ("--models", "--set", "--spec", "--out", "--price", "--estimate-only", "--record")
    assert raised.value.code == 0 and all(option in help_text for option in options)
    assert "--models claude-opus-5-5 claude-sonnet-5-5 claude-haiku-5-5 --estimate-only" in help_text      # the example commands are not re-wrapped


def test_the_default_client_is_the_anthropic_client_priced_by_the_command_line(two_documents, tmp_path, monkeypatch, capsys):
    """Without a client factory the run asks the SDK (here a stub, so no network): the price reaches the cost of every answer and
    --estimate-only counts through messages.count_tokens."""
    streamed, counted = [], []
    reply = {"document": ANSWERS[True], "image": ANSWERS[False]}

    class Stream:
        def __init__(self, text): self.text = text
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get_final_message(self):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)], stop_reason="end_turn", model="model-x",
                                   _request_id="req_1", usage=SimpleNamespace(input_tokens=1000, output_tokens=200))

    class Messages:
        def stream(self, **kw): streamed.append(kw); return Stream(reply[kw["messages"][0]["content"][0]["type"]])
        def count_tokens(self, **kw): counted.append(kw); return SimpleNamespace(input_tokens=4321)

    monkeypatch.setattr("aid.readers.llm_client.anthropic.Anthropic", lambda: SimpleNamespace(messages=Messages()))
    out = tmp_path / "result.csv"
    argv = ["--models", "model-x", "--set", str(two_documents), "--spec", str(SPEC_DIR), "--out", str(out)]
    assert main([*argv, "--price", "model-x=5,25"]) == 0
    assert [(r["fields_correct"], r["cost"]) for r in read_rows(out)] == [("34", "0.01"), ("25", "0.01")] and len(streamed) == 2
    assert main([*argv, "--estimate-only"]) == 0
    assert "model-x: 4321 input tokens" in capsys.readouterr().out and len(counted) == 1 and counted[0]["model"] == "model-x"


def test_the_seconds_of_an_answer_leave_out_building_the_sdk_client_and_the_request(two_documents, tmp_path, monkeypatch):
    """The first request of a client builds the SDK client and every request encodes its document: neither is the model's time. The
    clock here is a fake that moves only when something takes time: 5 s to build the SDK client, 3 s to encode a document and 2 s
    for the model to answer."""
    now, encode = [0.0], llm_client._base64
    reply = {"document": ANSWERS[True], "image": ANSWERS[False]}

    class Stream:
        def __init__(self, text): self.text = text
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get_final_message(self):
            now[0] += 2.0
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)], stop_reason="end_turn", model="model-x",
                                   _request_id="req_1", usage=SimpleNamespace(input_tokens=1000, output_tokens=200))

    class Messages:
        def stream(self, **kw): return Stream(reply[kw["messages"][0]["content"][0]["type"]])

    def build_sdk_client():
        now[0] += 5.0
        return SimpleNamespace(messages=Messages())

    def encode_slowly(data):
        now[0] += 3.0
        return encode(data)

    monkeypatch.setattr("aid.readers.llm_client.time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr("aid.readers.llm_client.anthropic.Anthropic", build_sdk_client)
    monkeypatch.setattr("aid.readers.llm_client._base64", encode_slowly)
    out = tmp_path / "result.csv"
    assert main(["--models", "model-x", "--set", str(two_documents), "--spec", str(SPEC_DIR), "--out", str(out)]) == 0
    assert [r["seconds"] for r in read_rows(out)] == ["2.00", "2.00"]


def test_a_set_with_no_text_layer_is_decided_on_field_accuracy_and_cost(two_documents, tmp_path, capsys):
    """Only the screenshot: no source accuracy exists for any model, so the decision rests on the other two measures."""
    (two_documents / "MU01_conf.pdf").unlink()
    run(two_documents, tmp_path / "result.csv", Models(), *PRICES_A_B, models=("model-a", "model-b"))
    printed = capsys.readouterr().out
    assert "source accuracy not measurable" in printed and "Decision: model-b" in printed          # the same readings, and model-b is cheaper


# --- the services of the exploration readers: azure_openai:<deployment> and foundry:<deployment> ---

class ServiceClient(FakeClient):
    """A client a service name stands for. `fails_on` is the number of the document (1 is the first) at which it raises a
    CopilotError that names the class only; `counts` None makes count_tokens raise ReaderError, as Azure OpenAI's does."""

    def __init__(self, name, price, fails_on=None, **options):
        super().__init__(name, price, **options)
        self.fails_on = fails_on

    def complete(self, req):
        if self.fails_on is not None and len(self.calls) + 1 == self.fails_on:
            self.calls.append(req)
            raise CopilotError("the exploration reader for Azure OpenAI failed at its service: ConnectionError")
        return super().complete(req)

    def count_tokens(self, req):
        if self.counts is None:
            raise ReaderError("this service has no way to count tokens")
        return super().count_tokens(req)


class Built(list):
    """What the replaced exploration_client was asked for, one dict per call; `options` are the ServiceClient options by service."""
    options: dict = {}


@pytest.fixture
def built(monkeypatch):
    """Replaces exploration_client by one that records its arguments and returns a ServiceClient."""
    seen = Built()
    seen.options = {}

    def fake(value, environ, deployment=None, effort="medium", price=None, session=None):
        seen.append({"value": value, "deployment": deployment, "price": price, "environ": environ})
        return ServiceClient(f"{value}:{deployment}", price, **seen.options.get(value, {}))

    monkeypatch.setattr(exploration, "exploration_client", fake)
    return seen


def test_service_names_reach_their_clients_with_the_deployment(built):
    azure, foundry = client_for_model("azure_openai:dep1", None), client_for_model("foundry:dep2", (Decimal(3), Decimal(15)))
    assert [(b["value"], b["deployment"], b["price"]) for b in built] == [("azure_openai", "dep1", None), ("foundry", "dep2", (3, 15))]
    assert azure.name == "azure_openai:dep1" and foundry.name == "foundry:dep2"
    assert type(azure).__name__ == "StandIn" and type(foundry).__name__ == "StandIn"           # both are behind the guard
    plain = client_for_model("model-x", None)
    assert type(plain).__name__ == "AnthropicClient" and len(built) == 2


def test_a_name_with_a_blank_deployment_is_a_command_error_before_any_client_is_built(two_documents, tmp_path, capsys, built):
    for name in ("azure_openai:", "foundry:", "foundry:   "):
        with pytest.raises(SystemExit) as raised:
            main(["--models", "model-b", name, "--set", str(two_documents), "--spec", str(SPEC_DIR), "--out", str(tmp_path / "r.csv")])
        assert raised.value.code == 2 and "needs a deployment" in capsys.readouterr().err
    with pytest.raises(ValueError):
        client_for_model("azure_openai:", None)
    assert built == []


def test_a_missing_setting_is_a_command_error_naming_the_variable(two_documents, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    with pytest.raises(SystemExit) as raised:
        main(["--models", "azure_openai:dep1", "--set", str(two_documents), "--spec", str(SPEC_DIR), "--out", str(tmp_path / "r.csv")])
    assert raised.value.code == 2 and "AZURE_OPENAI_ENDPOINT" in capsys.readouterr().err


def test_prices_take_the_service_names(two_documents, tmp_path, built):
    out = tmp_path / "result.csv"
    argv = ["--models", "azure_openai:dep1", "--set", str(two_documents), "--spec", str(SPEC_DIR), "--out", str(out)]
    assert main([*argv, "--price", "azure_openai:dep1=2,8"]) == 0
    assert built[0]["price"] == (2, 8) and [r["cost"] for r in read_rows(out)] == ["0.0036", "0.0036"]    # 1000 * 2 + 200 * 8 per million


def test_estimate_only_says_unknown_for_azure_and_counts_foundry(two_documents, tmp_path, capsys, built):
    built.options = {"azure_openai": {"counts": None}, "foundry": {"counts": 42}}
    assert main(["--models", "azure_openai:dep1", "foundry:dep2", "--set", str(two_documents), "--spec", str(SPEC_DIR), "--estimate-only"]) == 0
    printed = capsys.readouterr().out
    assert "azure_openai:dep1: unknown (" in printed and "foundry:dep2: 42 input tokens" in printed


def test_estimate_goes_on_after_a_copilot_error_from_the_count(two_documents, capsys):
    class Counting(FakeClient):
        def count_tokens(self, req):
            if self.name == "model-a":
                raise CopilotError("the exploration reader for Foundry failed at its service: ConnectionError")
            return super().count_tokens(req)

    assert main(["--models", "model-a", "model-b", "--set", str(two_documents), "--spec", str(SPEC_DIR), "--estimate-only"],
                client_factory=lambda model, price: Counting(model, price)) == 1          # every model was tried, and one count failed
    printed = capsys.readouterr().out
    assert ("model-a: the count failed (the exploration reader for Foundry failed at its service: ConnectionError); correct the "
            "variables before the full run") in printed
    assert "model-b: 5000 input tokens" in printed


def test_a_service_failure_is_an_error_row_and_the_run_goes_on(two_documents, tmp_path, capsys):
    out = tmp_path / "result.csv"
    factory = lambda model, price: ServiceClient(model, price, fails_on=2)
    assert run(two_documents, out, factory, models=("azure_openai:dep1", "foundry:dep2", "model-b")) == 0
    rows = read_rows(out)
    assert [r["error"] for r in rows[:2]] == ["", "service failed: the exploration reader for Azure OpenAI failed at its service: ConnectionError"]
    assert [r["model"] for r in rows] == ["azure_openai:dep1"] * 2 + ["foundry:dep2"] * 2 + ["model-b"] * 2 and rows[1]["fields_correct"] == "0"
    assert "Summary over 2 documents per model" in capsys.readouterr().out


def test_the_record_folder_of_a_service_name_has_no_colon(two_documents, tmp_path):
    record = tmp_path / "recorded"
    assert run(two_documents, tmp_path / "result.csv", Models(), "--record", str(record), models=("azure_openai:dep1",)) == 0
    assert [p.name for p in record.iterdir()] == ["azure_openai_dep1"] and list((record / "azure_openai_dep1").glob("*.json"))


def test_a_document_outside_the_made_up_folders_is_an_error_row_without_a_call(madeup_out, tmp_path, monkeypatch):
    folder = tmp_path / "set"
    folder.mkdir()
    (folder / "MU05_conf.pdf").write_bytes((madeup_out / "MU05_conf.pdf").read_bytes() + b"\n")      # one byte more: no made-up file
    inner = []

    def fake(value, environ, deployment=None, effort="medium", price=None, session=None):
        inner.append(ServiceClient(f"{value}:{deployment}", price))
        return inner[-1]

    monkeypatch.setattr(exploration, "exploration_client", fake)
    out = tmp_path / "result.csv"
    assert main(["--models", "azure_openai:dep1", "--set", str(folder), "--spec", str(SPEC_DIR), "--out", str(out)]) == 0
    [row] = read_rows(out)
    assert row["error"].startswith("Copilot could not read this document:") and row["fields_correct"] == "0"
    assert inner[0].calls == []


@pytest.mark.parametrize("status, kind", [(404, "AzureNotFound"), (401, "AzureKeyRefused")])
def test_an_azure_openai_failure_is_a_row_with_the_class_that_names_its_cause(tmp_path, monkeypatch, status, kind):
    """The real Azure OpenAI client behind the real guard, over a fake session that answers every request with the status."""
    from aid.readers import stand_in
    from aid.readers.azure_client import AzureOpenAIClient
    from aid.tests.test_azure_client import FakeResponse, FakeSession
    folder = tmp_path / "set"
    folder.mkdir()
    for name in ("MU01_conf.pdf", "CART02.png"):                     # the package's own made-up files, which the guard lets through
        shutil.copy(stand_in.MADE_UP_FOLDERS[0] / name, folder / name)
    sessions = []

    def azure(value, environ, deployment=None, effort="medium", price=None, session=None):
        sessions.append(FakeSession(FakeResponse(status, {"error": {"code": "made-up-code", "message": "made-up message"}})))
        return AzureOpenAIClient("https://made-up.openai.azure.com", "made-up-key", deployment, price_per_mtok=price, session=sessions[-1])

    monkeypatch.setattr(exploration, "exploration_client", azure)
    out = tmp_path / "result.csv"
    assert main(["--models", "azure_openai:dep1", "--set", str(folder), "--spec", str(SPEC_DIR), "--out", str(out)]) == 0
    assert [r["error"] for r in read_rows(out)] == [f"service failed: the exploration reader for Azure OpenAI failed at its service: {kind}"] * 2
    assert len(sessions[0].calls) == 2                               # each document was sent, and refused


class Failing(ServiceClient):
    """A client a service name stands for whose service fails at every document."""

    def complete(self, req):
        self.calls.append(req)
        raise CopilotError("the exploration reader for Azure OpenAI failed at its service: AzureLimit")


def test_no_decision_is_made_over_documents_that_a_service_did_not_read(two_documents, tmp_path, capsys):
    """A document whose service failed scores 0 and costs nothing: a decision over it would rest on the failure, not on a reading."""
    prices = ["--price", "azure_openai:dep1=2,8", "--price", "foundry:dep2=3,15"]
    assert run(two_documents, tmp_path / "result.csv", lambda model, price: Failing(model, price), *prices,
               models=("azure_openai:dep1", "foundry:dep2")) == 0
    printed = capsys.readouterr().out
    assert ("No decision: 4 documents of azure_openai:dep1, foundry:dep2 were not read (the service failed, or the document was "
            "refused); run the same command again with the same --record to ask them again.") in printed
    assert "Decision:" not in printed


def test_no_decision_is_made_while_one_document_was_refused_by_the_guard(madeup_out, tmp_path, capsys, built):
    """The guard's refusal is a document that was not read as well: here the cart, changed by one byte, beside the confirmation."""
    folder = tmp_path / "set"
    folder.mkdir()
    shutil.copy(madeup_out / "MU01_conf.pdf", folder / "MU01_conf.pdf")
    (folder / "CART02.png").write_bytes((madeup_out / "CART02.png").read_bytes() + b"\n")
    assert run(folder, tmp_path / "result.csv", None, "--price", "azure_openai:dep1=2,8", models=("azure_openai:dep1",)) == 0
    printed = capsys.readouterr().out
    assert ("No decision: 1 document of azure_openai:dep1 was not read (the service failed, or the document was refused); run the "
            "same command again with the same --record to ask it again.") in printed and "Decision:" not in printed


@pytest.mark.parametrize("failure", [ReaderError("no usable answer"), CopilotError("made-up failure")])
def test_an_unread_document_of_an_anthropic_model_id_still_counts_as_0_and_the_run_decides(two_documents, tmp_path, capsys, failure):
    """The rule of results/README.md for the Anthropic models: an unread document counts as 0, and the decision is made."""
    class Unread(FakeClient):
        def complete(self, req):
            if req.pdf is None:                           # the cart
                raise failure
            return super().complete(req)

    factory = lambda model, price: (Unread if model == "model-a" else FakeClient)(model, price)
    assert run(two_documents, tmp_path / "result.csv", factory, *PRICES_A_B, models=("model-a", "model-b")) == 0
    printed = capsys.readouterr().out
    assert "model-a: field accuracy 50.0%" in printed and "Decision: model-b" in printed and "No decision" not in printed


@pytest.mark.parametrize("name", ["Foundry:dep", "azure-openai:dep"])
@pytest.mark.parametrize("extra", [[], ["--estimate-only"]], ids=["a run", "an estimate"])
def test_a_misspelt_service_name_is_a_command_error_before_any_client_is_built(two_documents, tmp_path, capsys, name, extra):
    """A name with a colon is no Anthropic model id: one whose service is misspelt would otherwise leave the guard."""
    models = Models()
    with pytest.raises(SystemExit) as raised:
        run(two_documents, tmp_path / "result.csv", models, *extra, models=("model-a", name))
    assert raised.value.code == 2 and models.clients == {} and not (tmp_path / "result.csv").exists()
    assert (f"{name!r} names no service: use azure_openai:<deployment> or foundry:<deployment>; an Anthropic model id holds no "
            "colon") in capsys.readouterr().err


def test_estimate_says_unknown_for_azure_openai_without_sending_anything(two_documents, capsys, monkeypatch):
    """The real Azure OpenAI client behind the real guard: its count is unknown, and its session is never used."""
    from aid.readers.azure_client import NO_COUNT, AzureOpenAIClient

    class Unused:
        def post(self, *args, **kwargs):
            raise AssertionError("the estimate sent a request to Azure OpenAI")

    def azure(value, environ, deployment=None, effort="medium", price=None, session=None):
        return AzureOpenAIClient("https://made-up.openai.azure.com", "made-up-key", deployment, session=Unused())

    monkeypatch.setattr(exploration, "exploration_client", azure)
    assert main(["--models", "azure_openai:dep1", "--set", str(two_documents), "--spec", str(SPEC_DIR), "--estimate-only"]) == 0
    assert f"azure_openai:dep1: unknown ({NO_COUNT})" in capsys.readouterr().out


def test_estimate_on_a_set_outside_the_made_up_folders_counts_nothing(madeup_out, tmp_path, capsys, monkeypatch):
    """A count sends the document as an answer does, so the guard refuses it first, for both services."""
    folder = tmp_path / "set"
    folder.mkdir()
    (folder / "MU05_conf.pdf").write_bytes((madeup_out / "MU05_conf.pdf").read_bytes() + b"\n")       # one byte more: no made-up file
    inner = []

    def fake(value, environ, deployment=None, effort="medium", price=None, session=None):
        inner.append(ServiceClient(f"{value}:{deployment}", price))
        return inner[-1]

    monkeypatch.setattr(exploration, "exploration_client", fake)
    assert main(["--models", "azure_openai:dep1", "foundry:dep2", "--set", str(folder), "--spec", str(SPEC_DIR), "--estimate-only"]) == 0
    printed = capsys.readouterr().out
    for model, service in (("azure_openai:dep1", "Azure OpenAI"), ("foundry:dep2", "Anthropic's models in Microsoft Foundry")):
        assert f"{model}: unknown (Copilot could not read this document: the exploration reader for {service} reads" in printed
    assert len(inner) == 2 and all(client.counted == [] and client.calls == [] for client in inner)
