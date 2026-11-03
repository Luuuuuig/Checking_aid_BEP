import hashlib
import json
import os
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from aid.audit import AUDIT_FILE, Audit
from aid.case import open_case, sha256_file
from aid.decider import Decision, DecisionAnswer, NoDecider
from aid.execution.ops import WriteOp
from aid.execution.writer import PreparedWriter, WriteLogEntry
from aid.readers.llm_client import LlmResponse
from aid.settings import Settings
from aid.tests.conftest import SETTINGS


def test_audit_record_shape(tmp_path):
    c = open_case(tmp_path, "confirmation", [], "250101", datetime(2026, 11, 10, 9, 0))
    a = Audit(c, SETTINGS.model_copy(update={"authorization_limit": Decimal("1")}), datetime(2026, 11, 10, 9, 0), "0.1")
    a.step("read_po", {"lines": 3}, 0.2); a.decision("R04", {"price_pct": "2.0", "price_abs": "25.00"}, {"diff_pct": "1.6"}, "AUTO")
    p = a.save(); d = json.loads(p.read_text())
    assert list(d)[:4] == ["schema_version", "aid_version", "case_id", "started_at"] and d["decisions"][0]["rule_id"] == "R04"
    assert d["settings"]["authorization_limit"] == "set" and "1" not in json.dumps(d["settings"]["authorization_limit"])


# The tests below cover what the brief's rulings add to the test above.

NOW = datetime(2026, 11, 10, 9, 0)
FINISHED = datetime(2026, 11, 10, 9, 30)
SECTIONS = ["schema_version", "aid_version", "case_id", "started_at", "finished_at", "inputs", "model", "calls", "settings", "steps",
            "decisions", "writes", "messages", "errors", "timings"]
CALL_KEYS = ["kind", "model", "input_tokens", "output_tokens", "cost_eur", "seconds", "request_id", "attempts"]
UTC = timezone.utc
# A made-up limit that nothing else in a record can equal. It is derived from the made-up limit of the tests' settings, so that
# no new amount is written down here.
LIMIT = SETTINGS.authorization_limit * 1000 + 17


def put(folder: Path, name: str, content: bytes) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def make_audit(tmp_path, settings=SETTINGS, files=()) -> Audit:
    """An Audit for a new case in tmp_path/cases, opened at NOW with the given input files."""
    case = open_case(tmp_path / "cases", "confirmation", list(files), "250101", NOW)
    return Audit(case, settings, NOW, "0.1")


def saved(audit: Audit, finished_at=FINISHED) -> dict:
    """Save the audit and read audit.json back."""
    return json.loads(audit.save(finished_at).read_text(encoding="utf-8"))


def llm_response(**more) -> LlmResponse:
    fields = dict(text="the whole text of the answer", model="model-a", input_tokens=1200, output_tokens=300, seconds=4.5,
                  cost_eur=Decimal("0.0123"), request_id="req_1", attempts=2)
    return LlmResponse(**(fields | more))


def decision_answer(**more) -> DecisionAnswer:
    fields = dict(choice="2", confidence=0.9, rationale="the document quotes version 2", source="model:model-a", input_tokens=400,
                  output_tokens=60, seconds=1.25, cost_eur=Decimal("0.0004"), request_id="req_2")
    return DecisionAnswer(**(fields | more))


def make_entry(status="prepared", **more) -> WriteLogEntry:
    op = WriteOp(kind="po_line_price", po_number="250101", version=1, line_id="1", old_value="12.50", new_value="12.70")
    fields = dict(entry_id="e1", case_id="c1", op=op, status=status, previous="12.50", new="12.70", rule_id="R04",
                  evidence={"flag_id": "f1"}, timestamp="2026-11-10T10:00:00")
    return WriteLogEntry(**(fields | more))


def limit_settings(limit=LIMIT) -> Settings:
    """The tests' settings with a distinctive made-up limit and relative paths, so that nothing else in a dump can look like it."""
    return SETTINGS.model_copy(update={"authorization_limit": limit, "cases_root": Path("cases"), "po_store_root": Path("store"),
                                       "watched_folder": Path("inbox")})


def limits_in(node):
    """Every value of an authorization_limit key anywhere in parsed JSON."""
    if isinstance(node, dict):
        for key, item in node.items():
            if key == "authorization_limit":
                yield item
            else:
                yield from limits_in(item)
    elif isinstance(node, list):
        for item in node:
            yield from limits_in(item)


class Sample(BaseModel):
    price: Decimal
    when: datetime
    where: Path
    note: str


class Holder(BaseModel):
    thing: Any


class WithSettings(BaseModel):
    settings: Settings
    others: list[Settings] = []


class NoOffset(tzinfo):
    """A time zone that knows no offset: a datetime that has it is naive, by the definition of the datetime module."""

    def utcoffset(self, moment):
        return None

    def dst(self, moment):
        return None

    def tzname(self, moment):
        return None


def test_a_new_record_has_every_section_in_order(tmp_path):
    a = make_audit(tmp_path)
    path = a.save(FINISHED)
    d = json.loads(path.read_text(encoding="utf-8"))
    assert path == a.case.root / AUDIT_FILE == a.case.root / "audit.json"
    assert list(d) == SECTIONS
    assert (d["schema_version"], d["aid_version"], d["case_id"]) == (1, "0.1", "250101_20261110T090000")
    assert (d["started_at"], d["finished_at"]) == ("2026-11-10T09:00:00", "2026-11-10T09:30:00")
    assert (d["inputs"], d["model"], d["calls"], d["steps"], d["decisions"], d["writes"], d["messages"], d["errors"]) == \
        ([], [], [], [], [], [], [], [])
    assert d["timings"] == {"total": 0.0, "wall_seconds": None}


def test_datetimes_are_iso_and_finished_at_defaults_to_the_utc_clock(tmp_path):
    case = open_case(tmp_path / "cases", "confirmation", [], "250101", NOW)
    started = datetime(2026, 11, 10, 9, 0, tzinfo=timezone(timedelta(hours=1)))
    a = Audit(case, SETTINGS, started, "0.1")
    before = datetime.now(timezone.utc)
    d = json.loads(a.save().read_text(encoding="utf-8"))
    after = datetime.now(timezone.utc)
    finished = datetime.fromisoformat(d["finished_at"])
    assert d["started_at"] == "2026-11-10T09:00:00+01:00"
    assert finished.utcoffset() == timedelta(0) and before <= finished <= after


def test_input_records_the_name_and_the_sha256_of_each_file(tmp_path):
    first = put(tmp_path / "docs", "conf.pdf", b"%PDF-1.4 made up")
    second = put(tmp_path / "docs", "po.pdf", b"%PDF-1.4 another made up")
    a = make_audit(tmp_path, files=[first, second])
    for path in a.case.input_files:
        a.input(path)
    assert saved(a)["inputs"] == [{"name": "conf.pdf", "sha256": sha256_file(first)}, {"name": "po.pdf", "sha256": sha256_file(second)}]
    assert sha256_file(first) != sha256_file(second)


def test_a_missing_input_raises_and_is_not_recorded(tmp_path):
    a = make_audit(tmp_path)
    with pytest.raises(FileNotFoundError):
        a.input(tmp_path / "missing.pdf")
    assert saved(a)["inputs"] == []


def test_model_keeps_the_hash_of_the_prompt_and_not_its_text(tmp_path):
    prompt = "Lees de bevestiging van de leverancier: prijs per stuk, één regel per artikel."
    a = make_audit(tmp_path)
    a.model("model-a", "v1", prompt)                                   # the reader, unless another role is given
    path = a.save(FINISHED)
    assert json.loads(path.read_text(encoding="utf-8"))["model"] == [{
        "role": "reader", "name": "model-a", "prompt_version": "v1", "system_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}]
    assert "Lees de bevestiging" not in path.read_text(encoding="utf-8")


def test_model_keeps_one_record_per_role_and_a_second_call_replaces_that_role_in_place(tmp_path):
    a = make_audit(tmp_path)
    a.model("model-a", "v1", "the reading prompt")
    a.model("model-b", "d1", "the decision prompt", role="decider")
    first = saved(a)["model"]
    assert [(m["role"], m["name"], m["prompt_version"]) for m in first] == [("reader", "model-a", "v1"), ("decider", "model-b", "d1")]
    assert list(first[1]) == ["role", "name", "prompt_version", "system_prompt_sha256"]
    assert first[1]["system_prompt_sha256"] == hashlib.sha256(b"the decision prompt").hexdigest()
    assert first[0]["system_prompt_sha256"] == hashlib.sha256(b"the reading prompt").hexdigest()
    a.model("model-c", "v2", "a newer reading prompt")                 # the reader again: its record is replaced, where it was
    second = saved(a)["model"]
    assert [(m["role"], m["name"], m["prompt_version"]) for m in second] == [("reader", "model-c", "v2"), ("decider", "model-b", "d1")]
    assert second[0]["system_prompt_sha256"] == hashlib.sha256(b"a newer reading prompt").hexdigest()
    b = make_audit(tmp_path / "other")                                 # the records follow the order in which the roles were first named
    b.model("model-b", "d1", "the decision prompt", role="decider")
    b.model("model-a", "v1", "the reading prompt")
    assert [m["role"] for m in saved(b)["model"]] == ["decider", "reader"]


def test_model_refuses_a_role_that_does_not_exist(tmp_path):
    a = make_audit(tmp_path)
    with pytest.raises(ValueError, match="writer"):
        a.model("model-a", "v1", "a prompt", role="writer")
    assert saved(a)["model"] == []


def test_call_records_the_usage_of_a_reader_response(tmp_path):
    a = make_audit(tmp_path)
    a.call("reader", llm_response())
    path = a.save(FINISHED)
    record = json.loads(path.read_text(encoding="utf-8"))["calls"][0]
    assert record == {"kind": "reader", "model": "model-a", "input_tokens": 1200, "output_tokens": 300, "cost_eur": "0.0123",
                      "seconds": 4.5, "request_id": "req_1", "attempts": 2}
    assert list(record) == CALL_KEYS
    assert "whole text of the answer" not in path.read_text(encoding="utf-8")        # the usage is kept, not the answer


def test_call_records_the_usage_of_a_decider_answer(tmp_path):
    a = make_audit(tmp_path)
    a.call("decider", decision_answer())
    path = a.save(FINISHED)
    record = json.loads(path.read_text(encoding="utf-8"))["calls"][0]
    assert record == {"kind": "decider", "source": "model:model-a", "input_tokens": 400, "output_tokens": 60, "cost_eur": "0.0004",
                      "seconds": 1.25, "request_id": "req_2"}
    assert list(record) == ["kind", "source", "input_tokens", "output_tokens", "cost_eur", "seconds", "request_id"]      # no attempts
    assert "the document quotes version 2" not in path.read_text(encoding="utf-8")


def test_call_keeps_what_the_response_does_not_know_as_null(tmp_path):
    none_asked = NoDecider().decide(Decision(kind="version", options=["1", "2"], context={}))       # no model was asked
    a = make_audit(tmp_path)
    a.call("decider", none_asked)
    a.call("reader", llm_response(cost_eur=None, request_id=None))                                   # a client without a price table
    first, second = saved(a)["calls"]
    assert first == {"kind": "decider", "source": "none", "input_tokens": None, "output_tokens": None, "cost_eur": None,
                     "seconds": None, "request_id": None}
    assert (second["cost_eur"], second["request_id"], second["input_tokens"]) == (None, None, 1200)


def test_call_writes_a_cost_as_a_plain_decimal_string(tmp_path):
    a = make_audit(tmp_path)
    a.call("reader", llm_response(cost_eur=Decimal("1E-7")))             # str() of this is scientific notation
    a.call("reader", llm_response(cost_eur=Decimal("0.10")))             # trailing zeros are kept
    a.call("reader", llm_response(cost_eur=Decimal("12")))
    assert [call["cost_eur"] for call in saved(a)["calls"]] == ["0.0000001", "0.10", "12"]


def test_call_refuses_an_unknown_kind_and_an_object_that_is_no_response(tmp_path):
    a = make_audit(tmp_path)
    with pytest.raises(ValueError, match="writer"):
        a.call("writer", llm_response())
    with pytest.raises(TypeError, match="NoneType"):
        a.call("reader", None)
    with pytest.raises(TypeError, match="object"):
        a.call("decider", object())
    assert saved(a)["calls"] == []


def test_step_keeps_a_model_as_json_and_a_dict_as_given(tmp_path):
    a = make_audit(tmp_path)
    a.step("read_po", Sample(price=Decimal("12.50"), when=datetime(2026, 11, 10, 9, 5), where=Path("docs") / "conf.pdf", note="ok"), 0.25)
    payload = {"lines": 3, "nested": {"flags": ["price", "date"], "ok": True, "gap": None}, "ratio": 0.5}
    a.step("compare", payload, 0.5)
    d = saved(a)
    assert d["steps"] == [{"name": "read_po", "seconds": 0.25, "payload": {"price": "12.50", "when": "2026-11-10T09:05:00",
                                                                             "where": str(Path("docs") / "conf.pdf"), "note": "ok"}},
                          {"name": "compare", "seconds": 0.5, "payload": payload}]
    assert list(d["steps"][0]) == ["name", "seconds", "payload"]


@pytest.mark.parametrize("payload", [
    {"price": Decimal("12.50")},
    {"when": datetime(2026, 11, 10)},
    {"gap": float("nan")},
    {(1, 2): "a key that is not a string"},
    Holder(thing=object()),
    {"note": "price \ud800 text"},                       # a lone surrogate: json.dumps lets it through, the file cannot hold it
], ids=["decimal", "datetime", "nan", "tuple key", "model with an object", "lone surrogate"])
def test_a_payload_that_is_not_json_raises_with_the_step_name(tmp_path, payload):
    a = make_audit(tmp_path)
    with pytest.raises(ValueError, match="read_po"):
        a.step("read_po", payload, 0.1)
    d = saved(a)
    assert d["steps"] == [] and d["timings"] == {"total": 0.0, "wall_seconds": None}      # a refused step leaves no trace, not even its seconds


def test_a_step_with_no_seconds_raises_at_the_call_and_is_not_recorded(tmp_path):
    a = make_audit(tmp_path)
    with pytest.raises(TypeError):
        a.step("read_po", {"lines": 3}, None)             # not left to fail in save(), at the end of the run
    assert saved(a)["steps"] == []


def test_step_keeps_a_copy_of_a_dict(tmp_path):
    a = make_audit(tmp_path)
    payload = {"lines": [1, 2]}
    a.step("read_po", payload, 0.1)
    payload["lines"].append(3)
    payload["extra"] = True
    assert saved(a)["steps"][0]["payload"] == {"lines": [1, 2]}


def test_timings_add_up_the_seconds_of_a_repeated_step_name(tmp_path):
    a = make_audit(tmp_path)
    a.step("read", {}, 0.25)
    a.step("compare", {}, 0.5)
    a.step("read", {}, 0.5)
    d = saved(a)
    assert d["timings"] == {"read": 0.75, "compare": 0.5, "total": 1.25, "wall_seconds": None}
    assert list(d["timings"]) == ["read", "compare", "total", "wall_seconds"]
    assert [(s["name"], s["seconds"]) for s in d["steps"]] == [("read", 0.25), ("compare", 0.5), ("read", 0.5)]      # the steps stay apart


@pytest.mark.parametrize("name", ["total", "wall_seconds"])
def test_a_step_cannot_have_a_name_that_timings_keeps_for_itself(tmp_path, name):
    a = make_audit(tmp_path)
    with pytest.raises(ValueError, match=name):
        a.step(name, {}, 1.0)
    assert saved(a)["steps"] == []


BAD_SECONDS = pytest.mark.parametrize("seconds", [-0.5, float("nan"), float("inf"), float("-inf")], ids=["negative", "nan", "inf", "-inf"])


@BAD_SECONDS
def test_a_step_with_seconds_that_are_not_finite_or_are_negative_raises_with_the_step_name(tmp_path, seconds):
    a = make_audit(tmp_path)
    with pytest.raises(ValueError, match="read_po"):
        a.step("read_po", {"lines": 3}, seconds)
    assert saved(a)["steps"] == []


@BAD_SECONDS
def test_a_call_with_seconds_that_are_not_finite_or_are_negative_raises_with_the_call(tmp_path, seconds):
    a = make_audit(tmp_path)
    with pytest.raises(ValueError, match="reader"):
        a.call("reader", llm_response(seconds=seconds))
    with pytest.raises(ValueError, match="decider"):
        a.call("decider", decision_answer(seconds=seconds))
    assert saved(a)["calls"] == []


def test_zero_seconds_and_a_call_without_seconds_are_accepted(tmp_path):
    a = make_audit(tmp_path)
    a.step("read_po", {}, 0)
    a.call("reader", llm_response(seconds=0.0))
    a.call("decider", decision_answer(seconds=None))
    d = saved(a)
    assert (d["steps"][0]["seconds"], d["calls"][0]["seconds"], d["calls"][1]["seconds"]) == (0, 0, None)


def test_timings_and_the_summary_are_rounded_to_six_decimals(tmp_path):
    a = make_audit(tmp_path)
    a.step("read", {}, 0.1)
    a.step("read", {}, 0.2)                                # 0.1 + 0.2 is 0.30000000000000004 in floating point
    a.step("compare", {}, 1.23456789)
    d = saved(a)
    assert d["timings"] == {"read": 0.3, "compare": 1.234568, "total": 1.534568, "wall_seconds": None}
    assert [s["seconds"] for s in d["steps"]] == [0.1, 0.2, 1.23456789]       # the steps keep the seconds as they were given
    assert a.summary()["seconds"] == 1.534568


def test_wall_seconds_is_finished_minus_started_when_both_have_a_time_zone(tmp_path):
    started = datetime(2026, 11, 10, 9, 0, tzinfo=timezone(timedelta(hours=1)))                     # 08:00 UTC
    a = Audit(open_case(tmp_path / "cases", "confirmation", [], "250101", NOW), SETTINGS, started, "0.1")
    a.step("read", {}, 0.25)
    d = saved(a, datetime(2026, 11, 10, 8, 30, 15, 500000, tzinfo=UTC))                            # another offset: the moments are compared
    assert d["timings"] == {"read": 0.25, "total": 0.25, "wall_seconds": 1815.5}                   # and total stays the sum of the steps
    assert list(d["timings"]) == ["read", "total", "wall_seconds"]
    assert saved(a, started - timedelta(seconds=10))["timings"]["wall_seconds"] == -10.0           # as it is, if the clock was set back


@pytest.mark.parametrize("started, finished", [
    (datetime(2026, 11, 10, 9, 0), datetime(2026, 11, 10, 9, 30)),
    (datetime(2026, 11, 10, 9, 0), datetime(2026, 11, 10, 9, 30, tzinfo=UTC)),
    (datetime(2026, 11, 10, 9, 0, tzinfo=UTC), datetime(2026, 11, 10, 9, 30)),
    (datetime(2026, 11, 10, 9, 0), None),
    (datetime(2026, 11, 10, 9, 0, tzinfo=NoOffset()), datetime(2026, 11, 10, 9, 30, tzinfo=UTC)),
], ids=["both naive", "only finished has a zone", "only started has a zone", "naive start and the utc clock", "a zone without an offset"])
def test_wall_seconds_is_null_when_a_time_has_no_time_zone(tmp_path, started, finished):
    a = Audit(open_case(tmp_path / "cases", "confirmation", [], "250101", NOW), SETTINGS, started, "0.1")
    a.step("read", {}, 0.25)
    assert saved(a, finished)["timings"] == {"read": 0.25, "total": 0.25, "wall_seconds": None}


def test_wall_seconds_uses_the_utc_clock_when_finished_at_is_not_given(tmp_path):
    started = datetime.now(UTC) - timedelta(seconds=30)
    a = Audit(open_case(tmp_path / "cases", "confirmation", [], "250101", NOW), SETTINGS, started, "0.1")
    assert 30 <= saved(a, None)["timings"]["wall_seconds"] < 90


def test_the_limit_is_written_as_set_or_unset(tmp_path):
    for limit, expected in ((SETTINGS.authorization_limit, "set"), (Decimal("0"), "set"), (None, "unset")):      # zero is a limit that is set
        a = make_audit(tmp_path / expected / str(limit), SETTINGS.model_copy(update={"authorization_limit": limit}))
        assert saved(a)["settings"]["authorization_limit"] == expected


def test_the_settings_are_dumped_as_json_with_paths_as_strings(tmp_path):
    settings = SETTINGS.model_copy(update={"cases_root": tmp_path / "cases", "po_store_root": tmp_path / "store",
                                           "watched_folder": tmp_path / "inbox"})
    dumped = saved(make_audit(tmp_path, settings))["settings"]
    assert [dumped[name] for name in ("cases_root", "po_store_root", "watched_folder")] == [str(tmp_path / n) for n in ("cases", "store", "inbox")]
    assert dumped == settings.model_dump(mode="json") | {"authorization_limit": "set"}          # nothing else is changed
    assert list(dumped) == list(Settings.model_fields)
    assert (dumped["price_pct"], dumped["price_abs"]) == ("2.0", "25.00")                      # decimals as strings
    assert dumped["model"] == SETTINGS.model                                                    # kept as it is


def test_the_settings_are_those_the_run_started_with(tmp_path):
    settings = SETTINGS.model_copy()
    a = make_audit(tmp_path, settings)
    settings.price_pct = Decimal("9.0")                    # changed after the run started
    assert saved(a)["settings"]["price_pct"] == "2.0"


def test_the_authorization_amount_appears_nowhere_in_the_file(tmp_path):
    amount = str(SETTINGS.authorization_limit)                          # the made-up limit of the tests' settings
    settings = SETTINGS.model_copy(update={"cases_root": Path("cases"), "po_store_root": Path("store"), "watched_folder": Path("inbox")})
    assert amount in json.dumps(settings.model_dump(mode="json"))      # the plain dump shows it, so a leak would fail this test
    a = make_audit(tmp_path, settings)
    a.model("model-a", "v1", "a prompt")
    a.call("reader", llm_response())
    a.step("read_po", {"po_above_authorization": True}, 0.5)
    a.decision("R10", {"po_above_authorization": True}, {}, "AUTO")
    a.write(make_entry())
    a.message({"template": "reapproval"})
    a.error("route", "boom")
    assert amount not in a.save(FINISHED).read_text(encoding="utf-8")


PLACES = {
    "step payload": lambda a, s: a.step("read_po", s, 0.1),
    "step dict": lambda a, s: a.step("read_po", {"settings": s, "more": [s, {"deeper": s}]}, 0.1),
    "step model holding settings": lambda a, s: a.step("read_po", WithSettings(settings=s, others=[s]), 0.1),
    "step dict holding a model holding settings": lambda a, s: a.step("read_po", {"holder": WithSettings(settings=s)}, 0.1),
    "step model with an untyped field": lambda a, s: a.step("read_po", Holder(thing={"deeper": [s]}), 0.1),
    "decision threshold": lambda a, s: a.decision("R04", {"settings": s}, {}, "AUTO"),
    "decision inputs": lambda a, s: a.decision("R04", {}, {"settings": s}, "AUTO"),
    "message": lambda a, s: a.message({"settings": s}),
    "write evidence": lambda a, s: a.write(make_entry(evidence={"settings": s})),
}


@pytest.mark.parametrize("place", PLACES)
def test_a_settings_anywhere_in_a_record_is_written_without_its_limit(tmp_path, place):
    settings = limit_settings()
    assert str(LIMIT) in json.dumps(settings.model_dump(mode="json"))       # the plain dump shows the amount, so a leak fails below
    a = make_audit(tmp_path)
    PLACES[place](a, settings)
    raw = a.save(FINISHED).read_text(encoding="utf-8")
    assert str(LIMIT) not in raw
    found = list(limits_in(json.loads(raw)))
    assert set(found) == {"set"} and len(found) >= 2                       # the settings section, and the settings met in the record


def test_a_settings_in_a_step_is_dumped_as_the_settings_section_is(tmp_path):
    settings = limit_settings()
    a = make_audit(tmp_path, settings)
    a.step("settings", settings, 0.1)
    d = saved(a)
    assert d["steps"][0]["payload"] == d["settings"] == settings.model_dump(mode="json") | {"authorization_limit": "set"}
    b = make_audit(tmp_path / "unset", limit_settings(None))
    b.step("settings", limit_settings(None), 0.1)
    assert saved(b)["steps"][0]["payload"]["authorization_limit"] == "unset"


def test_a_dump_of_the_settings_made_by_the_caller_is_covered_too(tmp_path):
    a = make_audit(tmp_path)
    a.step("settings", limit_settings().model_dump(mode="json"), 0.1)
    assert str(LIMIT) not in a.save(FINISHED).read_text(encoding="utf-8")


@pytest.mark.parametrize("given, written", [(None, "unset"), ("set", "set"), ("unset", "unset"), (str(LIMIT), "set")],
                         ids=["none", "set", "unset", "an amount"])
def test_a_limit_key_in_a_record_is_written_as_set_or_unset(tmp_path, given, written):
    a = make_audit(tmp_path)
    a.message({"authorization_limit": given, "nested": [{"authorization_limit": given}]})
    assert saved(a)["messages"] == [{"authorization_limit": written, "nested": [{"authorization_limit": written}]}]


def test_saving_twice_overwrites_with_the_current_state(tmp_path):
    a = make_audit(tmp_path)
    a.step("read_po", {"lines": 3}, 0.25)
    first = a.save(FINISHED)
    before = json.loads(first.read_text(encoding="utf-8"))
    a.step("compare", {"flags": 0}, 0.5)
    a.error("compare", "boom")
    second = a.save(FINISHED + timedelta(minutes=5))
    after = json.loads(second.read_text(encoding="utf-8"))
    assert first == second == a.case.root / "audit.json"
    assert ([s["name"] for s in before["steps"]], before["errors"]) == (["read_po"], [])
    assert ([s["name"] for s in after["steps"]], [e["step"] for e in after["errors"]]) == (["read_po", "compare"], ["compare"])
    assert (before["finished_at"], after["finished_at"]) == ("2026-11-10T09:30:00", "2026-11-10T09:35:00")
    assert after["timings"] == {"read_po": 0.25, "compare": 0.5, "total": 0.75, "wall_seconds": None}
    assert sorted(p.name for p in a.case.root.iterdir()) == ["audit.json", "case.json", "input", "messages", "steps"]      # no temporary file left


def test_a_save_that_fails_in_the_replace_leaves_the_earlier_file_intact(tmp_path, monkeypatch):
    a = make_audit(tmp_path)
    a.step("read_po", {"lines": 3}, 0.25)
    path = a.save(FINISHED)
    earlier = path.read_bytes()
    a.step("compare", {"flags": 0}, 0.5)
    replace = os.replace

    def failing(source, target, *args, **kwargs):
        if Path(target) == path:
            raise OSError("the disk is full")
        return replace(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing)
    with pytest.raises(OSError, match="disk is full"):
        a.save(FINISHED + timedelta(minutes=5))
    assert path.read_bytes() == earlier                                    # the file of the first save, whole
    monkeypatch.undo()
    assert [s["name"] for s in saved(a)["steps"]] == ["read_po", "compare"]         # and the next save goes through


def test_a_save_that_fails_in_the_serialisation_leaves_the_earlier_file_intact(tmp_path):
    a = make_audit(tmp_path)
    a.step("read_po", {"lines": 3}, 0.25)
    path = a.save(FINISHED)
    earlier = path.read_bytes()
    a.decision("R04\ud800", {}, {}, "AUTO")        # a rule id is not checked at the call, and a lone surrogate cannot be written as UTF-8
    with pytest.raises(ValueError):                # a UnicodeEncodeError
        a.save(FINISHED + timedelta(minutes=5))
    assert path.read_bytes() == earlier


def test_write_records_the_entry_in_json_mode(tmp_path):
    a = make_audit(tmp_path)
    entry = make_entry(evidence={"price": Decimal("12.70"), "seen": datetime(2026, 11, 10, 9, 5)})
    a.write(entry)
    assert saved(a)["writes"] == [entry.model_dump(mode="json")]
    assert saved(a)["writes"][0]["evidence"] == {"price": "12.70", "seen": "2026-11-10T09:05:00"}


def test_the_records_keep_the_order_of_the_calls(tmp_path):
    a = make_audit(tmp_path)
    for rule, result in (("R02", "AUTO"), ("R04", "AUTO"), ("R05", "REVIEW")):
        a.decision(rule, {"rule": rule}, {"seen": [rule]}, result)
        a.message({"template": rule})
        a.error(f"step_{rule}", f"error {rule}")
    d = saved(a)
    assert d["decisions"][1] == {"rule_id": "R04", "threshold": {"rule": "R04"}, "inputs": {"seen": ["R04"]}, "result": "AUTO"}
    assert list(d["decisions"][1]) == ["rule_id", "threshold", "inputs", "result"]
    assert [x["rule_id"] for x in d["decisions"]] == ["R02", "R04", "R05"]
    assert [x["template"] for x in d["messages"]] == ["R02", "R04", "R05"]
    assert d["errors"] == [{"step": f"step_{r}", "message": f"error {r}"} for r in ("R02", "R04", "R05")]


def test_error_takes_an_exception_for_its_message(tmp_path):
    a = make_audit(tmp_path)
    a.error("read_po", ValueError("boom"))              # called from an except block: it must not fail in its turn
    assert saved(a)["errors"] == [{"step": "read_po", "message": "boom"}]


def test_error_replaces_text_that_cannot_be_written_as_utf8(tmp_path):
    a = make_audit(tmp_path)
    a.error("read_po", ValueError("bad \ud800 text, een prijs van € 12,50"))           # exception text can hold a lone surrogate
    path = a.save(FINISHED)
    assert json.loads(path.read_text(encoding="utf-8"))["errors"] == [{"step": "read_po", "message": "bad ? text, een prijs van € 12,50"}]


def test_text_that_cannot_be_written_as_utf8_is_refused_at_the_call(tmp_path):
    bad = "price \ud800 text"                       # a lone surrogate: json.dumps lets it through, the file cannot hold it
    a = make_audit(tmp_path)
    with pytest.raises(ValueError, match="read_po"):
        a.step("read_po", {"note": bad}, 0.1)
    with pytest.raises(ValueError, match="R04"):
        a.decision("R04", {"note": bad}, {}, "AUTO")
    with pytest.raises(ValueError, match="message"):
        a.message({"note": bad})
    with pytest.raises(ValueError, match="write"):
        a.write(make_entry(evidence={"note": bad}))
    with pytest.raises(ValueError, match="reader"):
        a.call("reader", llm_response(request_id=bad))
    d = saved(a)                                    # nothing was recorded, and the record can still be saved
    assert (d["steps"], d["decisions"], d["messages"], d["writes"], d["calls"]) == ([], [], [], [], [])


def test_non_ascii_text_is_written_as_it_is(tmp_path):
    a = make_audit(tmp_path)
    a.step("read_po", {"note": "prijs in € voor één regel"}, 0.1)
    a.error("compare", "geen vergelijking mogelijk: bedrag in €")
    data = a.save(FINISHED).read_bytes()
    assert "prijs in € voor één regel".encode("utf-8") in data and "bedrag in €".encode("utf-8") in data      # the characters, as UTF-8 bytes
    assert b"\\u20ac" not in data and b"\\u00e9" not in data                                                    # and not as escapes


def test_a_decision_or_a_message_that_is_not_json_raises_and_is_not_recorded(tmp_path):
    a = make_audit(tmp_path)
    with pytest.raises(ValueError, match="R04"):
        a.decision("R04", {"price_pct": Decimal("2.0")}, {}, "AUTO")
    with pytest.raises(ValueError, match="R04"):
        a.decision("R04", {}, {"diff_pct": Decimal("1.6")}, "AUTO")
    with pytest.raises(ValueError, match="message"):
        a.message({"sent": datetime(2026, 11, 10, 9, 10)})
    d = saved(a)
    assert (d["decisions"], d["messages"]) == ([], [])


def test_summary_counts_what_the_digest_needs(tmp_path):
    a = make_audit(tmp_path)
    for name, seconds in (("read", 0.25), ("compare", 0.5), ("read", 0.5)):
        a.step(name, {}, seconds)
    for rule, result in (("R04", "AUTO"), ("R02", "AUTO"), ("R06", "ASK"), ("R05", "REVIEW")):
        a.decision(rule, {}, {}, result)
    a.write(make_entry("prepared", entry_id="e1"))
    a.write(make_entry("prepared", entry_id="e2"))
    a.write(make_entry("done", entry_id="e3"))
    a.message({"template": "quantity_unit"})
    a.message({"template": "missing_info"})
    a.error("compare", "boom")
    a.call("reader", llm_response())
    a.call("decider", decision_answer())
    a.call("decider", decision_answer(input_tokens=None, output_tokens=None, seconds=None, cost_eur=None, request_id=None))   # no response came
    assert a.summary() == {"steps": 3, "decisions": {"AUTO": 2, "ASK": 1, "REVIEW": 1}, "writes": {"prepared": 2, "done": 1},
                           "messages": 2, "errors": 1,
                           "calls": {"count": 3, "input_tokens": 1600, "output_tokens": 360, "cost_eur": "0.0127", "without_cost": 1},
                           "seconds": 1.25}
    assert a.summary()["seconds"] == saved(a)["timings"]["total"]


def test_summary_of_an_audit_without_records_is_zero(tmp_path):
    assert make_audit(tmp_path).summary() == {"steps": 0, "decisions": {}, "writes": {}, "messages": 0, "errors": 0,
                                              "calls": {"count": 0, "input_tokens": 0, "output_tokens": 0, "cost_eur": "0",
                                                        "without_cost": 0},
                                              "seconds": 0.0}


def test_summary_says_when_the_cost_of_a_call_is_unknown(tmp_path):
    a = make_audit(tmp_path)
    a.call("reader", llm_response(cost_eur=None))
    a.call("reader", llm_response(cost_eur=None))
    calls = a.summary()["calls"]
    assert (calls["count"], calls["input_tokens"], calls["cost_eur"], calls["without_cost"]) == (2, 2400, "0", 2)


def test_a_full_record_reads_back_with_every_section(tmp_path):
    conf = put(tmp_path / "docs", "conf.pdf", b"%PDF-1.4 made up")
    a = make_audit(tmp_path, files=[conf])
    a.input(a.case.input_files[0])
    a.model("model-a", "v1", "a system prompt")
    a.model("model-b", "d1", "a decision prompt", role="decider")
    a.call("reader", llm_response())
    a.call("decider", decision_answer())
    a.step("read_document", {"lines": 3}, 0.25)
    a.decision("R04", {"price_pct": "2.0", "price_abs": "25.00"}, {"diff_pct": "1.6"}, "AUTO")
    entry = PreparedWriter(tmp_path / "prepared").apply(
        WriteOp(kind="po_line_price", po_number="250101", version=1, line_id="1", old_value="12.50", new_value="12.70"),
        a.case.case_id, "R04", {"flag_id": "f1"}, NOW)
    a.write(entry)
    a.message({"template": "quantity_unit", "to": "supplier@example.com"})
    a.error("compare", "kon de prijs niet vergelijken: alleen een totaalprijs, geen prijs per stuk (één regel, €)")
    path = a.save(FINISHED)
    data = path.read_bytes()
    d = json.loads(data.decode("utf-8"))
    assert list(d) == SECTIONS and all(d[section] for section in SECTIONS)                        # every section is there and filled
    assert (d["inputs"][0]["name"], [(m["role"], m["name"]) for m in d["model"]], [c["kind"] for c in d["calls"]]) == \
        ("conf.pdf", [("reader", "model-a"), ("decider", "model-b")], ["reader", "decider"])
    assert d["steps"] == [{"name": "read_document", "seconds": 0.25, "payload": {"lines": 3}}]
    assert d["decisions"][0]["threshold"] == {"price_pct": "2.0", "price_abs": "25.00"}
    assert WriteLogEntry.model_validate(d["writes"][0]) == entry                                    # the write reads back as the entry
    assert d["messages"] == [{"template": "quantity_unit", "to": "supplier@example.com"}]
    assert d["errors"] == [{"step": "compare", "message": "kon de prijs niet vergelijken: alleen een totaalprijs, geen prijs per stuk (één regel, €)"}]
    assert d["timings"] == {"read_document": 0.25, "total": 0.25, "wall_seconds": None}
    assert data.endswith(b"\n") and "één regel, €".encode("utf-8") in data                  # the characters are in the file as UTF-8, not as escapes
