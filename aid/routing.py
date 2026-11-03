"""Routing: the routing table of the settings, applied to the findings of a case.

route takes the findings of the comparison (aid/comparison.py) and the context of the case and gives the actions: AUTO (the aid
makes the change itself, through the write operations the action carries, see aid/execution/ops.py), ASK (the aid asks the
supplier, with the message the action carries, one of the three standard templates) or REVIEW (the buyer decides on the review
page). Each action names the rule of the table that decided it, which the audit record and the disposition log cite, and gives
its reason in words. The thresholds and the supplier lists come from the settings, never from the code. The routing writes
nothing, sends nothing and asks no model anything: the same findings, context and settings always give the same actions, and
nothing it is given is changed.

The rules (design specification, section 5), in the order they are tried:
- R11. The supplier is new (fewer cases seen than first_cases_review) or marked by the buyer (review_suppliers): one REVIEW action
  for the whole case, reason "new supplier" or else "supplier marked for review", listing every finding. No other rule fires.
- R09, no version at all. When neither the findings nor the context hold a version, nothing is written or asked: one REVIEW
  action, "no version selected", lists every finding (the flags, then the uncompared items), and the version item follows it.
- R02 and R03, a delivery date flag. A date is compared on one day, the last it covers (aid.comparison.date_span): a day on
  itself, a week on its Friday and a range on its last day, on the PO's side as on the document's (a PO normally states a day).
  The PO date is that of the line in the context, else the flag's PO value. A split line (see below) is R03 REVIEW, "split line:
  parts differ". A date missing on the document is R03 REVIEW, "no delivery date on the document". A document date before the
  PO date is R02 AUTO, "delivery date earlier than the PO date", whatever the lead time, because R02 is tried before R03.
  Otherwise an unknown lead time is R03 REVIEW, "no lead time known for the supplier"; a document date at most the supplier's
  lead time plus lead_time_tolerance_days after the PO date is R02 AUTO, "delivery date within the lead time plus the
  tolerance"; a later one is R03 REVIEW, "delivery date beyond the lead time plus the tolerance". R02 writes the document's date
  over the PO line's (po_line_date).
- R04 and R05, a price flag, whose two values are net prices at the PO line's price basis (po_basis in the context; "1" for a
  line not in it). A split line is R05 REVIEW, "split line: parts differ", and a price missing on the document R05 REVIEW, "no
  price on the document". The quantity is the value of the line's quantity Match, else the PO value of its quantity Flag; a line
  with neither is R05 REVIEW, "quantity unknown for the price rule". R04 AUTO, "price within threshold", when the difference is
  at most price_pct percent of the PO price and the difference per piece (divided by the basis) times the quantity is at most
  price_abs. It writes the document's price over the PO line's (po_line_price) and over the article's (article_price, for the
  line's item code in po_item_code; with no item code there is no article_price, and the reason says so). Otherwise R05 REVIEW,
  "price above threshold".
- R06, a quantity or unit flag: ASK with the template quantity_unit. R07, a missing or an extra line: ASK with the template
  line_missing_extra; a line not in the cart is R07 REVIEW, "could not be placed". R08, a flag on the required information: ASK
  with the template missing_info. On a cart each of these questions is REVIEW instead, "cart: for the buyer" (the buyer corrects
  the cart or the PO before ordering), and for a supplier in messages_off_suppliers REVIEW, "messages off for this supplier".
  The questions of one template are one ASK action per case, listing all their items and finding ids, so that the supplier gets
  one e-mail per template.
- R09. The uncompared items: one REVIEW action per reason, with that reason, listing the items that have it. No version in the
  findings: one REVIEW action, "no version selected", the version item. When the context holds a version, the flags still route
  by their rules and their operations carry the context's version.
- R10. On a PO above the buyer's authorization (po_above_authorization; None, when no limit is set, counts as not above), every
  AUTO action that writes to a PO line keeps its operations and becomes R10, so that execution asks the approver to approve the
  PO again. Its reason is "re-approval requested; " and the rule and reason it had: "re-approval requested; R04 price within
  threshold", and its base_rule_id is the rule it had (R02 or R04). When the settings set an authorization limit and
  po_above_authorization is None, the PO's total is unknown, so the PO may be above the authorization: every such AUTO action
  becomes REVIEW under R10 instead, without its operations, with the reason "PO total unknown" and the rule it had as its
  base_rule_id, so that nothing is written on such a PO without the buyer.
- R01, the operations of the case. On a confirmation with an AUTO action, or with no other action at all, a last AUTO action R01,
  "attach and confirm", attaches the document (attach_document, the context's document_path) and, when no action is ASK or
  REVIEW, sets the status from Besteld to Bevestigd (set_status). It comes after every other action, so that execution makes the
  writes of the findings first, and R10 never applies to it. A confirmation with no other action (no finding, and a version in
  the findings) is this one action. A cart gets neither operation: a cart with no other action is one AUTO action R01, "no
  difference", without operations.
- R00, the fallback. A flag that no rule covers (a field and detail the comparison does not give, or a price or date flag without
  exactly one PO line or without a PO value): REVIEW, "not covered by a rule".

Split lines. A PO line that several document lines answer (its line presence Match is "split") and that has a price or date flag
cannot take the value of one part, so the flag is REVIEW whatever its values. A PO line in more than one finding of the flag's
field (several flags, or a flag and a match or an uncompared item for other parts) counts as split too.

The operations. Every value operation carries what the undo restores (aid/execution/writer.py): old_value the value before the
change and new_value the value after it, in normal form. article_price is the exception: the routing does not know the article's
current price, so its old_value is None; the live writer reads it before it writes and logs, and a prepared article change shows
an empty old value. The version of every operation is that of the findings, else that of the context.

The order of the actions is that of the findings they cover: the flags in the order of their ids (F1, F2, ..., F10), an ASK
action at its first flag, then the uncompared items, numbered U1, U2, ... in the order of findings.uncompared (an action at the
first item of its reason), then the version item, then R01's action. finding_ids holds the ids of the items an action covers;
it is empty for the version item and for R01, and findings_by_id gives the finding each id names. The item of a question holds
plain strings: po_line_ids and doc_line_ids (the ids joined by ", "), field, po_value and doc_value (a value in normal form, else
its text as printed, else "").

RULE_SETTINGS names the settings each rule reads, so that a record of a decision can cite them with it.

The values are read in the normal forms of aid/schema.py, which the comparison gives: a date or a number in another form raises,
because it is a reader's error.
"""
import re
from collections import Counter
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from aid.comparison import Findings, Flag, Uncompared, date_span
from aid.execution.ops import OpKind, WriteOp
from aid.schema import DocValue
from aid.settings import Settings

Route = Literal["AUTO", "ASK", "REVIEW"]
Template = Literal["quantity_unit", "line_missing_extra", "missing_info"]

FALLBACK_RULE = "R00"                            # the rule id of a finding no rule of the table covers
# The settings each rule reads, by name. R02 and R03 read the supplier's entry of supplier_lead_times and R10 the authorization
# limit through the context, which holds what the pipeline worked out from them (supplier_lead_time_days, po_above_authorization);
# every other name is read from the settings themselves.
RULE_SETTINGS: dict[str, tuple[str, ...]] = {
    "R01": (), "R02": ("lead_time_tolerance_days", "supplier_lead_times"), "R03": ("lead_time_tolerance_days", "supplier_lead_times"),
    "R04": ("price_pct", "price_abs"), "R05": ("price_pct", "price_abs"), "R06": ("messages_off_suppliers",),
    "R07": ("messages_off_suppliers",), "R08": ("messages_off_suppliers",), "R09": (), "R10": ("authorization_limit",),
    "R11": ("first_cases_review", "review_suppliers"), FALLBACK_RULE: ()}
ORDERED, CONFIRMED = "Besteld", "Bevestigd"      # the PO's status before and after set_status
NO_VERSION = "no version selected"
TOTAL_UNKNOWN = "PO total unknown"
SPLIT_LINE = "split line: parts differ"

_PO_LINE_KINDS = ("po_line_price", "po_line_date", "po_line_quantity")
_FLAG_ID = re.compile(r"F(\d+)")


class CaseContext(BaseModel):
    supplier: str
    po_number: str
    version: int | None                          # the PO version the pipeline selected
    po_date: dict[str, str]                      # line_id -> the PO line's delivery date
    po_basis: dict[str, str] = {}                # line_id -> the PO line's price basis; a line not in it has basis "1"
    po_item_code: dict[str, str] = {}            # line_id -> the PO line's item code, the article of an article_price operation
    po_above_authorization: bool | None          # None when no limit is set (not above) or the PO's total is unknown (see R10)
    supplier_lead_time_days: int | None          # None when the ERP gives no lead time for the supplier
    cases_seen_for_supplier: int
    document_path: str                           # the supplier's document, which attach_document attaches
    kind: Literal["confirmation", "cart"]


class MessageSpec(BaseModel):
    template: Template
    items: list[dict]                            # per item: po_line_ids, doc_line_ids, field, po_value, doc_value, plain strings


class Action(BaseModel):
    route: Route
    rule_id: str                                 # "R01" to "R11", or FALLBACK_RULE
    finding_ids: list[str]                       # the flag ids and the uncompared ids ("U1", ...) the action covers
    ops: list[WriteOp] = []                      # the writes of an AUTO action
    message: MessageSpec | None = None           # the question of an ASK action
    reason: str
    base_rule_id: str | None = None              # the rule an R10 action took its operations or its findings from (R02 or R04); None for any other


def route(findings: Findings, ctx: CaseContext, settings: Settings) -> list[Action]:
    """The actions for the findings of a case, by the rules and in the order of the module docstring."""
    flags = sorted(findings.flags, key=_id_order)
    uncompared_ids = _uncompared_ids(findings)
    finding_ids = [f.flag_id for f in flags] + uncompared_ids
    if (reason := _supplier_review(ctx, settings)) is not None:
        return [Action(route="REVIEW", rule_id="R11", finding_ids=finding_ids, reason=reason)]
    version_item = [Action(route="REVIEW", rule_id="R09", finding_ids=[], reason=NO_VERSION)] if findings.version is None else []
    if findings.version is None and ctx.version is None:
        covered = [Action(route="REVIEW", rule_id="R09", finding_ids=finding_ids, reason=NO_VERSION)] if finding_ids else []
        return covered + version_item
    rules = _Rules(findings, ctx, settings)
    actions = _one_question_per_template([rules.flag(f) for f in flags]) + _uncompared(findings.uncompared, uncompared_ids)
    actions += version_item
    if ctx.po_above_authorization:
        actions = [_reapproval(action) if _writes_a_po_line(action) else action for action in actions]
    elif ctx.po_above_authorization is None and settings.authorization_limit is not None:
        actions = [_total_unknown(action) if _writes_a_po_line(action) else action for action in actions]
    return actions + rules.case_operations(actions)


class _Rules:
    """The rules for the flags of one case, and the operations of the case."""

    def __init__(self, findings: Findings, ctx: CaseContext, settings: Settings):
        self.findings, self.ctx, self.settings = findings, ctx, settings
        self.version = findings.version if findings.version is not None else ctx.version
        items = [*findings.flags, *findings.matches, *findings.uncompared]
        self.per_line = Counter((item.field, line_id) for item in items for line_id in item.po_line_ids)
        self.split_lines = {line_id for match in findings.matches if match.field == "line_presence" and match.value == "split"
                            for line_id in match.po_line_ids}

    def flag(self, flag: Flag) -> Action:
        """The action for one flag: the rule for its field and detail, the fallback when no rule has them."""
        rule = _FLAG_RULES.get((flag.field, flag.detail))
        return _uncovered(flag) if rule is None else rule(self, flag)

    def split(self, flag: Flag, line_id: str) -> bool:
        """Whether the flag's PO line is a split line whose parts differ in the flag's field (see the module docstring)."""
        return line_id in self.split_lines or self.per_line[(flag.field, line_id)] > 1

    def delivery_date(self, flag: Flag) -> Action:
        """R02 or R03 for a delivery date flag."""
        line_id = _one(flag.po_line_ids)
        po_date = None if line_id is None else (self.ctx.po_date.get(line_id) or _value(flag.po_value))
        if po_date is None:
            return _uncovered(flag)
        if self.split(flag, line_id):
            return _review("R03", flag, SPLIT_LINE)
        doc_date = _value(flag.doc_value)
        if doc_date is None:
            return _review("R03", flag, "no delivery date on the document")
        po_day, doc_day, lead_time = _compared_day(po_date), _compared_day(doc_date), self.ctx.supplier_lead_time_days
        if doc_day < po_day:
            reason = "delivery date earlier than the PO date"
        elif lead_time is None:
            return _review("R03", flag, "no lead time known for the supplier")
        elif doc_day <= po_day + timedelta(days=lead_time + self.settings.lead_time_tolerance_days):
            reason = "delivery date within the lead time plus the tolerance"
        else:
            return _review("R03", flag, "delivery date beyond the lead time plus the tolerance")
        op = self.op("po_line_date", line_id=line_id, old_value=po_date, new_value=doc_date)
        return Action(route="AUTO", rule_id="R02", finding_ids=[flag.flag_id], ops=[op], reason=reason)

    def price(self, flag: Flag) -> Action:
        """R04 or R05 for a price flag."""
        line_id, po_price, doc_price = _one(flag.po_line_ids), _value(flag.po_value), _value(flag.doc_value)
        if line_id is None or po_price is None:
            return _uncovered(flag)
        if self.split(flag, line_id):
            return _review("R05", flag, SPLIT_LINE)
        if doc_price is None:
            return _review("R05", flag, "no price on the document")
        quantity = self.quantity(line_id)
        if quantity is None:
            return _review("R05", flag, "quantity unknown for the price rule")
        if not self.within_threshold(Decimal(po_price), Decimal(doc_price), quantity, Decimal(self.ctx.po_basis.get(line_id, "1"))):
            return _review("R05", flag, "price above threshold")
        ops, reason = [self.op("po_line_price", line_id=line_id, old_value=po_price, new_value=doc_price)], "price within threshold"
        if item_code := self.ctx.po_item_code.get(line_id):
            ops.append(self.op("article_price", article_code=item_code, new_value=doc_price))     # the old article price is the writer's to read
        else:
            reason += "; article price not updated: item code unknown"
        return Action(route="AUTO", rule_id="R04", finding_ids=[flag.flag_id], ops=ops, reason=reason)

    def within_threshold(self, po_price: Decimal, doc_price: Decimal, quantity: Decimal, basis: Decimal) -> bool:
        """Whether a price difference is within both thresholds: at most price_pct percent of the PO price, and per piece times
        the quantity (taken without its sign) at most price_abs. Each test is multiplied out (by 100, by the basis), so that no
        division rounds at a boundary."""
        difference = abs(doc_price - po_price)
        return (difference * 100 <= po_price * self.settings.price_pct
                and difference * abs(quantity) <= self.settings.price_abs * basis)

    def quantity(self, line_id: str) -> Decimal | None:
        """The quantity of a PO line by the findings: the value of its quantity Match, else the PO value of its quantity Flag;
        None when they hold neither."""
        for match in self.findings.matches:
            if match.field == "quantity" and match.po_line_ids == [line_id]:
                return Decimal(match.value)
        for flag in self.findings.flags:
            if flag.field == "quantity" and flag.po_line_ids == [line_id] and _value(flag.po_value) is not None:
                return Decimal(flag.po_value.value)
        return None

    def quantity_or_unit(self, flag: Flag) -> Action:
        return self.ask(flag, "R06", "quantity_unit", "ask the supplier about the quantity or unit")

    def line(self, flag: Flag) -> Action:
        return self.ask(flag, "R07", "line_missing_extra", "ask the supplier about the missing or extra line")

    def not_in_cart(self, flag: Flag) -> Action:
        return _review("R07", flag, "could not be placed")

    def required_info(self, flag: Flag) -> Action:
        return self.ask(flag, "R08", "missing_info", "ask the supplier about the required information")

    def ask(self, flag: Flag, rule_id: str, template: Template, reason: str) -> Action:
        """An ASK action with the question of the template about the flag; REVIEW on a cart, and when the buyer switched the
        supplier's messages off."""
        if self.ctx.kind == "cart":
            return _review(rule_id, flag, "cart: for the buyer")
        if self.ctx.supplier in self.settings.messages_off_suppliers:
            return _review(rule_id, flag, "messages off for this supplier")
        return Action(route="ASK", rule_id=rule_id, finding_ids=[flag.flag_id], message=MessageSpec(template=template, items=[_item(flag)]),
                      reason=reason)

    def op(self, kind: OpKind, **fields: str) -> WriteOp:
        """An operation on the case's PO, with the version of the findings, else of the context."""
        return WriteOp(kind=kind, po_number=self.ctx.po_number, version=self.version, **fields)

    def case_operations(self, actions: list[Action]) -> list[Action]:
        """R01's action, which comes after the others: on a confirmation with an AUTO action or with no action, the document
        attached and, when no action is ASK or REVIEW, the status set; on a cart with no action, no operation; else nothing."""
        if self.ctx.kind == "cart":
            return [] if actions else [Action(route="AUTO", rule_id="R01", finding_ids=[], reason="no difference")]
        if actions and not any(action.route == "AUTO" for action in actions):
            return []
        ops = [self.op("attach_document", document=self.ctx.document_path)]
        if all(action.route == "AUTO" for action in actions):
            ops.append(self.op("set_status", old_value=ORDERED, new_value=CONFIRMED))
        return [Action(route="AUTO", rule_id="R01", finding_ids=[], ops=ops, reason="attach and confirm")]


_FLAG_RULES: dict[tuple[str, str], Callable[[_Rules, Flag], Action]] = {
    ("delivery_date", "later"): _Rules.delivery_date,
    ("delivery_date", "earlier"): _Rules.delivery_date,
    ("delivery_date", "missing on the document"): _Rules.delivery_date,
    ("price", "different"): _Rules.price,
    ("price", "missing on the document"): _Rules.price,
    ("quantity", "different"): _Rules.quantity_or_unit,
    ("quantity", "missing on the document"): _Rules.quantity_or_unit,
    ("unit", "different"): _Rules.quantity_or_unit,
    ("unit", "missing on the document"): _Rules.quantity_or_unit,
    ("line_presence", "missing line"): _Rules.line,
    ("line_presence", "extra line"): _Rules.line,
    ("line_presence", "not in cart"): _Rules.not_in_cart,
    ("required_info", "missing on the document"): _Rules.required_info,
    ("required_info", "different"): _Rules.required_info,
}


def _supplier_review(ctx: CaseContext, settings: Settings) -> str | None:
    """R11's reason when every case of the supplier goes to review, whatever its findings; None when the findings decide."""
    if ctx.cases_seen_for_supplier < settings.first_cases_review:
        return "new supplier"
    if ctx.supplier in settings.review_suppliers:
        return "supplier marked for review"
    return None


def _one_question_per_template(actions: list[Action]) -> list[Action]:
    """The actions with the ASK actions of each template joined into one at the place of the first: every finding id and item
    of the template, in the order of the flags."""
    joined: list[Action] = []
    first: dict[str, int] = {}
    for action in actions:
        if action.message is None:
            joined.append(action)
        elif action.message.template not in first:
            first[action.message.template] = len(joined)
            joined.append(action)
        else:
            n = first[action.message.template]
            ask = joined[n]
            message = MessageSpec(template=ask.message.template, items=ask.message.items + action.message.items)
            joined[n] = ask.model_copy(update={"finding_ids": ask.finding_ids + action.finding_ids, "message": message})
    return joined


def _uncompared(items: list[Uncompared], ids: list[str]) -> list[Action]:
    """R09 for the uncompared items: one REVIEW action per reason, listing the ids of the items with that reason, in the order of
    the first item of each reason."""
    by_reason: dict[str, list[str]] = {}
    for item, item_id in zip(items, ids):
        by_reason.setdefault(item.reason, []).append(item_id)
    return [Action(route="REVIEW", rule_id="R09", finding_ids=found, reason=reason) for reason, found in by_reason.items()]


def _writes_a_po_line(action: Action) -> bool:
    return action.route == "AUTO" and any(op.kind in _PO_LINE_KINDS for op in action.ops)


def findings_by_id(findings: Findings) -> dict[str, Flag | Uncompared]:
    """The findings by the ids the actions give them in finding_ids: a flag by its flag_id, an uncompared item by its id U1, U2, ..."""
    return {flag.flag_id: flag for flag in findings.flags} | dict(zip(_uncompared_ids(findings), findings.uncompared))


def _uncompared_ids(findings: Findings) -> list[str]:
    """The ids of the uncompared items: U1, U2, ... in the order of findings.uncompared."""
    return [f"U{n}" for n in range(1, len(findings.uncompared) + 1)]


def _reapproval(action: Action) -> Action:
    """R10: the action with its operations, under rule R10, its reason naming the rule and the reason it had, its base_rule_id the
    rule."""
    return action.model_copy(update={"rule_id": "R10", "reason": f"re-approval requested; {action.rule_id} {action.reason}",
                                     "base_rule_id": action.rule_id})


def _total_unknown(action: Action) -> Action:
    """R10 on a PO of unknown total with a limit set: the action as REVIEW, without its operations, its reason TOTAL_UNKNOWN, its
    base_rule_id the rule it had."""
    return action.model_copy(update={"route": "REVIEW", "rule_id": "R10", "ops": [], "reason": TOTAL_UNKNOWN, "base_rule_id": action.rule_id})


def _review(rule_id: str, flag: Flag, reason: str) -> Action:
    return Action(route="REVIEW", rule_id=rule_id, finding_ids=[flag.flag_id], reason=reason)


def _uncovered(flag: Flag) -> Action:
    return _review(FALLBACK_RULE, flag, "not covered by a rule")


def _one(ids: list[str]) -> str | None:
    """The one id of a list of one, else None."""
    return ids[0] if len(ids) == 1 else None


def _value(value: DocValue | None) -> str | None:
    """A value in normal form; None when there is none."""
    return None if value is None else value.value


def _shown(value: DocValue | None) -> str:
    """A value as a question shows it: in normal form, else its text as printed, else ""."""
    if value is None:
        return ""
    return value.value if value.value is not None else value.text or ""


def _item(flag: Flag) -> dict[str, str]:
    """The item of a question about a flag, in plain strings."""
    return {"po_line_ids": ", ".join(flag.po_line_ids), "doc_line_ids": ", ".join(flag.doc_line_ids), "field": flag.field,
            "po_value": _shown(flag.po_value), "doc_value": _shown(flag.doc_value)}


def _id_order(flag: Flag) -> tuple[int, int]:
    """The sort key of a flag: the number of an id F1, F2, ...; an id of another form comes after them."""
    found = _FLAG_ID.fullmatch(flag.flag_id)
    return (0, int(found[1])) if found else (1, 0)


def _compared_day(value: str) -> date:
    """The day a delivery date in normal form is compared on: the last day it covers (a day itself, the Friday of a week, the
    last day of a range). Raises ValueError for any other text."""
    return date_span(value)[1]
