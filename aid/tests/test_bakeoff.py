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

from aid.evalscripts.bakeoff import main
from aid.evalscripts.score_reading import HEADER_FIELDS, LINE_FIELDS
from aid.readers import llm_client
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
