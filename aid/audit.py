"""The audit record of the checking aid: one JSON file per run, <case_root>/audit.json.

An Audit collects what a run did while it goes on, and save() writes it. The record is for the researcher, IT and the thesis
(design specification, section 6); the buyer never sees it, the digest and the review page are what the buyer sees. An Audit
decides nothing and writes nothing but audit.json.

audit.json holds these keys, in this order:

    schema_version   1
    aid_version      the version of the aid that made the record
    case_id          the folder name of the case
    started_at       when the run started, in ISO form
    finished_at      when the run ended, in ISO form: the time of the save, unless save() is given another
    inputs           each input file as {"name", "sha256"}
    model            a list of {"role", "name", "prompt_version", "system_prompt_sha256"}, one per role that named a model
                     ("reader", "decider"), [] when none did
    calls            every call to a model, with its tokens, cost, seconds and request id
    settings         the settings of the run, with the authorization limit as "set" or "unset" and never as the amount
    steps            each step as {"name", "seconds", "payload"}, the payload being the output of the step
    decisions        each rule decision as {"rule_id", "threshold", "inputs", "result"}
    writes           each write-log entry
    messages         each message the aid sent or received
    errors           each error as {"step", "message"}
    timings          the seconds of each step name, added up, then "total", the sum of all steps, and "wall_seconds",
                     finished_at minus started_at (null when either has no time zone); all rounded to six decimals

Everything in the file is a JSON value. The parts the caller makes up (a step payload, the threshold and inputs of a
decision, a message record, a write-log entry and what a call takes from its response) are checked when they are recorded:
one that is not JSON, or not text that can be written as UTF-8 (a lone surrogate), raises ValueError at the call that gave it,
where the cause can be found, and not in save() at the end of the run, when the whole record would be lost with it. A pydantic
model in them is taken as its model_dump(mode="json"). As everywhere in the aid, an amount or a rate is given as a plain
decimal string ("2.0").

The amount of the authorization limit never reaches the file. The settings section shows "set" or "unset", and so does every
other place: wherever a recorded dict has the key authorization_limit, a None becomes "unset", "set" and "unset" stay and
any other value becomes "set". That covers a Settings, or a model that holds one, in any payload, and a dump of the settings
that the caller made.
"""
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel

from aid.case import Case, _write_json, sha256_file       # _write_json: the file appears whole or not at all, as case.json does
from aid.decider import DecisionAnswer
from aid.execution.writer import WriteLogEntry
from aid.readers.llm_client import LlmResponse
from aid.settings import Settings

AUDIT_FILE = "audit.json"
SCHEMA_VERSION = 1
TOTAL = "total"                                  # the keys timings keeps for itself, so no step can have these names
WALL_SECONDS = "wall_seconds"
LIMIT_KEY = "authorization_limit"                # the settings field whose amount is kept out of the record
SECONDS_DECIMALS = 6                             # timings and the summary are rounded to microseconds
Role = Literal["reader", "decider"]              # who asks a model: the reader of documents or the decider of open questions
CallKind = Role                                  # a call is recorded under the role of whoever made it
# What a call keeps of a response, in this order: who answered (an LlmResponse names its model, a DecisionAnswer its source),
# what the call took, and the number of requests where the response counts them. The text of the answer is not kept.
_CALL_FIELDS = ("model", "source", "input_tokens", "output_tokens", "cost_eur", "seconds", "request_id", "attempts")


class Audit:
    """The audit record of one run on one case. Each method records in the order it is called. save() writes the record as it is
    at that moment, and it may be called again after more has been recorded: it overwrites the file with the current state."""

    def __init__(self, case: Case, settings: Settings, started_at: datetime, aid_version: str):
        self.case = case
        self.started_at = started_at
        self.aid_version = aid_version
        self._settings = _settings_dump(settings)        # taken now: the settings the run started with, whatever happens to them later
        self._inputs: list[dict] = []
        self._models: dict[str, dict] = {}              # by role, in the order the roles were first named
        self._calls: list[dict] = []
        self._steps: list[dict] = []
        self._decisions: list[dict] = []
        self._writes: list[dict] = []
        self._messages: list[dict] = []
        self._errors: list[dict] = []

    def input(self, path: Path) -> None:
        """Record an input file by its name and the sha256 of its content. A file that is missing raises FileNotFoundError."""
        path = Path(path)
        self._inputs.append({"name": path.name, "sha256": sha256_file(path)})

    def model(self, name: str, prompt_version: str, system_prompt: str, role: Role = "reader") -> None:
        """Record the model of a role ("reader" for the reading of documents, "decider" for the decision prompt), the version of
        its prompt and the sha256 of its system prompt, not the prompt itself. There is one record per role: a second call for
        the same role replaces that role's record, in its place. A role that does not exist raises ValueError."""
        if role not in get_args(Role):
            raise ValueError(f"unknown role {role!r}, expected one of {get_args(Role)}")
        digest = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
        self._models[role] = {"role": role, "name": name, "prompt_version": prompt_version, "system_prompt_sha256": digest}

    def call(self, kind: CallKind, response: LlmResponse | DecisionAnswer) -> None:
        """Record one call to a model, for the cost per case: `kind` is "reader" or "decider" and `response` the LlmResponse of
        the reader or the DecisionAnswer of the decider. The record holds the kind and then those of model, source,
        input_tokens, output_tokens, cost_eur, seconds, request_id and attempts that the response has: an LlmResponse names its
        model and counts its attempts, a DecisionAnswer names its source. A cost is kept as a plain decimal string and a value
        the response does not know stays None; the text of the answer is not kept. A kind that does not exist raises
        ValueError, and so do seconds that are not finite or are negative, and a value that is not JSON; a response that has none
        of those attributes raises TypeError: a call is not recorded as an empty line."""
        if kind not in get_args(CallKind):
            raise ValueError(f"unknown kind of call {kind!r}, expected one of {get_args(CallKind)}")
        record = {"kind": kind}
        for name in _CALL_FIELDS:
            if hasattr(response, name):
                value = getattr(response, name)
                record[name] = _plain(value) if isinstance(value, Decimal) else value
        if len(record) == 1:
            raise TypeError(f"cannot record a call from {type(response).__name__}: it is neither an LlmResponse nor a DecisionAnswer")
        if record.get("seconds") is not None:
            record["seconds"] = _seconds(record["seconds"], f"the {kind} call")
        self._calls.append(_json_copy(record, f"the {kind} call"))

    def step(self, name: str, payload: BaseModel | dict, seconds: float) -> None:
        """Record a step: its name, the seconds it took and its output. A pydantic model is kept as its
        `model_dump(mode="json")`, a dict as it is; the record holds a copy, so that a later change to the dict does not reach it.
        A step name may repeat, and timings adds its seconds up. ValueError names the step for a payload that is not JSON, for
        seconds that are not finite or are negative, and for the names "total" and "wall_seconds", which timings keeps for itself."""
        if name in (TOTAL, WALL_SECONDS):
            raise ValueError(f"{name!r} cannot be the name of a step: timings keeps that key for itself")
        seconds = _seconds(seconds, f"step {name!r}")
        data = _json_copy(payload, f"the payload of step {name!r}")
        self._steps.append({"name": name, "seconds": seconds, "payload": data})

    def decision(self, rule_id: str, threshold: dict, inputs: dict, result: str) -> None:
        """Record a rule decision: the rule, the threshold it applied, the values it applied it to and the result (AUTO, ASK or
        REVIEW). The threshold and the inputs must be JSON; otherwise ValueError names the rule."""
        self._decisions.append({"rule_id": rule_id, "threshold": _json_copy(threshold, f"the threshold of rule {rule_id!r}"),
                                "inputs": _json_copy(inputs, f"the inputs of rule {rule_id!r}"), "result": result})

    def write(self, entry: WriteLogEntry) -> None:
        """Record a write-log entry as its `model_dump(mode="json")`, whether the change was made or only prepared."""
        self._writes.append(_json_copy(entry, "the write entry"))

    def message(self, record: dict) -> None:
        """Record a message the aid sent or received, as the dict that describes it. A record that is not JSON raises ValueError."""
        self._messages.append(_json_copy(record, "the message record"))

    def error(self, step: str, message: str) -> None:
        """Record an error and the step it came from. This is called from the handlers of other errors, so it does not fail on
        what it is given: an exception passed for the message is recorded as its text, and a character that cannot be written
        as UTF-8 (a lone surrogate in the text of an exception) as "?"."""
        text = str(message).encode("utf-8", errors="replace").decode("utf-8")
        self._errors.append({"step": step, "message": text})

    def save(self, finished_at: datetime | None = None) -> Path:
        """Write <case root>/audit.json and return its path. `finished_at` is the time the run ended and defaults to the UTC clock.
        The file appears whole or not at all, and a file from an earlier save is replaced."""
        if finished_at is None:
            finished_at = datetime.now(timezone.utc)
        record = {"schema_version": SCHEMA_VERSION, "aid_version": self.aid_version, "case_id": self.case.case_id,
                  "started_at": self.started_at.isoformat(), "finished_at": finished_at.isoformat(),
                  "inputs": self._inputs, "model": list(self._models.values()), "calls": self._calls, "settings": self._settings,
                  "steps": self._steps, "decisions": self._decisions, "writes": self._writes, "messages": self._messages,
                  "errors": self._errors, "timings": self._timings(finished_at)}
        path = self.case.root / AUDIT_FILE
        _write_json(path, record)
        return path

    def summary(self) -> dict:
        """The counts the digest needs, from what is recorded so far:

            steps       the number of steps
            decisions   the number of decisions per result, for the results that occur ({"AUTO": 2, "REVIEW": 1})
            writes      the number of writes per status, for the statuses that occur ({"done": 1, "prepared": 2})
            messages    the number of messages
            errors      the number of errors
            calls       {"count", "input_tokens", "output_tokens", "cost_eur", "without_cost"}: the tokens and the cost (a plain
                        decimal string) added up over the calls that know them, and the number of calls that carry no cost.
                        The totals leave those out, so a total is complete only where without_cost is 0.
            seconds     the sum of the seconds of all steps, rounded to six decimals
        """
        costs = [Decimal(str(call["cost_eur"])) for call in self._calls if call.get("cost_eur") is not None]
        return {"steps": len(self._steps),
                "decisions": dict(Counter(decision["result"] for decision in self._decisions)),
                "writes": dict(Counter(write["status"] for write in self._writes)),
                "messages": len(self._messages),
                "errors": len(self._errors),
                "calls": {"count": len(self._calls),
                          "input_tokens": sum(call.get("input_tokens") or 0 for call in self._calls),
                          "output_tokens": sum(call.get("output_tokens") or 0 for call in self._calls),
                          "cost_eur": _plain(sum(costs, Decimal(0))),
                          "without_cost": len(self._calls) - len(costs)},
                "seconds": self._total_seconds()}

    def _total_seconds(self) -> float:
        return round(sum((step["seconds"] for step in self._steps), 0.0), SECONDS_DECIMALS)

    def _timings(self, finished_at: datetime) -> dict:
        """The seconds of each step name, added up in the order the names first appear; then "total", the sum of all steps, and
        "wall_seconds", the time from started_at to finished_at (negative if the clock was set back), which is None when either
        has no time zone, because a naive time cannot be compared with an aware one. All are rounded to six decimals."""
        per_name: dict[str, float] = {}
        for step in self._steps:
            per_name[step["name"]] = per_name.get(step["name"], 0.0) + step["seconds"]
        wall = None
        if _is_aware(self.started_at) and _is_aware(finished_at):
            wall = round((finished_at - self.started_at).total_seconds(), SECONDS_DECIMALS)
        return {name: round(seconds, SECONDS_DECIMALS) for name, seconds in per_name.items()} | \
            {TOTAL: self._total_seconds(), WALL_SECONDS: wall}


def _settings_dump(settings: Settings) -> dict:
    """The settings as JSON values (paths and decimals as strings), with the authorization limit replaced by "set" or "unset":
    the amount is the buyer's and stays out of every record. A Settings met anywhere else in a record gets the same dump."""
    return _without_limit(settings.model_dump(mode="json"))


def _without_limit(value):
    """`value`, parsed JSON, with the amount of the authorization limit taken out: wherever a dict has the key authorization_limit,
    None becomes "unset", "set" and "unset" stay and any other value becomes "set". The value is not changed, a copy is returned."""
    if isinstance(value, dict):
        return {key: _limit_text(item) if key == LIMIT_KEY else _without_limit(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_limit(item) for item in value]
    return value


def _limit_text(limit) -> str:
    """What replaces the value of an authorization_limit key: None is "unset", "set" and "unset" stay, anything else is an amount."""
    if limit is None:
        return "unset"
    return limit if limit in ("set", "unset") else "set"


def _json_copy(value, what: str):
    """A copy of `value` made through JSON, so that the record holds what the file will hold and a later change to the caller's
    object does not reach it. A pydantic model, at any depth, is taken as its `model_dump(mode="json")`, and the amount of the
    authorization limit is taken out of the result (see _without_limit). Raises ValueError, naming `what`, for a value that is
    not JSON (a Decimal or a datetime in a dict, an object of another class, a NaN or an infinity) and for text that cannot be
    written as UTF-8, which json.dumps lets through but the file cannot hold: it is dumped and encoded as the file is written."""
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, default=_json_default)
        text.encode("utf-8")                              # raises for a lone surrogate, which the file cannot hold
        return _without_limit(json.loads(text))
    except (TypeError, ValueError) as exc:               # a PydanticSerializationError and a UnicodeEncodeError are ValueErrors
        raise ValueError(f"{what} cannot be recorded as JSON: {exc}") from exc


def _json_default(obj):
    """What json.dumps does with an object it does not know: a pydantic model is taken as its `model_dump(mode="json")`."""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _seconds(value, what: str) -> float:
    """`value` as a float number of seconds. Raises ValueError, naming `what`, for seconds that are not finite or are negative;
    a value that is not a number raises the TypeError of float()."""
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"{what}: the seconds must be finite and not negative, not {value!r}")
    return seconds


def _is_aware(moment: datetime) -> bool:
    return moment.tzinfo is not None and moment.utcoffset() is not None


def _plain(amount: Decimal) -> str:
    """A decimal as a plain string, never in scientific notation: Decimal("1E-7") is "0.0000001"."""
    return format(amount, "f")
