"""Tests of the routing (aid/routing.py): the rules R01 to R11 of the settings table, on constructed findings. No test calls a
model."""
from aid.comparison import Findings, Flag, Match, Uncompared, date_span
from aid.execution.ops import WriteOp
from aid.routing import RULE_SETTINGS, CaseContext, MessageSpec, findings_by_id, route
from aid.schema import DocValue
from aid.settings import Settings
from aid.tests.conftest import SETTINGS as SHARED_SETTINGS

# The routing reads first_cases_review from the settings. The shared test settings set it to 0, so that the new-supplier rule
# fires only where a test asks for it; here it is 5, the default of settings.example.json, so that the 9 cases seen of ctx() are
# past it and the 2 of the brief's R11 test are not.
SETTINGS = SHARED_SETTINGS.model_copy(update={"first_cases_review": 5})


# --- the brief's helpers ---

def ctx(**kw):
    # The brief's ctx() with an item code for line 1: no article_price operation is made without one (ruling 5), and the brief's
    # R04 test expects one.
    return CaseContext(**{"supplier": "S1", "po_number": "250101", "version": 1, "po_date": {"1": "2026-11-16"},
                          "po_item_code": {"1": "KM-1001"}, "po_above_authorization": False, "supplier_lead_time_days": 10,
                          "cases_seen_for_supplier": 9, "document_path": "x.pdf", "kind": "confirmation"} | kw)


def findings(*items, version=1) -> Findings:
    """Findings of these items: flags, numbered F1, F2, ... in the order given, matches and uncompared entries. A tuple of items
    (price_flag gives one) counts as its items."""
    flat = [part for item in items for part in (item if isinstance(item, tuple) else (item,))]
    flags = [f.model_copy(update={"flag_id": f"F{n}"}) for n, f in enumerate((x for x in flat if isinstance(x, Flag)), 1)]
    return Findings(version=version, flags=flags, matches=[x for x in flat if isinstance(x, Match)],
                    uncompared=[x for x in flat if isinstance(x, Uncompared)])


def value(text: str | None) -> DocValue | None:
    return None if text is None else DocValue(value=text, text=text)


def flag(field: str, line_id: str | None, po: str | None, doc: str | None, detail: str, doc_line_id: str | None = None) -> Flag:
    """A flag on PO line `line_id` (None: no PO line) and document line `doc_line_id` (by default the PO line's id; "": no
    document line), with these values in normal form. findings() gives it its id."""
    doc_line_id = line_id if doc_line_id is None else doc_line_id
    return Flag(flag_id="F0", field=field, po_line_ids=[line_id] if line_id else [], doc_line_ids=[doc_line_id] if doc_line_id else [],
                po_value=value(po), doc_value=value(doc), detail=detail)


def date_flag(line_id, po, doc):
    """A delivery date flag, "later" or "earlier" as the comparison words it; "missing on the document" when doc is None."""
    detail = "missing on the document" if doc is None else "later" if date_span(doc)[0] > date_span(po)[0] else "earlier"
    return flag("delivery_date", line_id, po, doc, detail)


def price_flag(line_id, po, doc, qty):
    """A price flag (net prices at the PO line's basis) with the quantity Match of its line; the flag alone when qty is None.
    doc None is a price missing on the document."""
    price = flag("price", line_id, po, doc, "different" if doc is not None else "missing on the document")
    return price if qty is None else (price, Match(field="quantity", po_line_ids=[line_id], doc_line_ids=[line_id], value=qty))


def qty_flag(line_id):
    return flag("quantity", line_id, "3", "5", "different")


def missing_line_flag(line_id):
    return flag("line_presence", line_id, f"KM-100{line_id}", None, "missing line", doc_line_id="")


def incoterm_flag():
    return flag("required_info", None, "DAP Rotterdam", None, "missing on the document")


def uncompared(field, reason, line_id="1"):
    return Uncompared(field=field, po_line_ids=[line_id], doc_line_ids=[line_id], reason=reason)


# --- the brief's tests ---

def test_r01_no_difference():
    a = route(Findings(version=1, flags=[], matches=[], uncompared=[]), ctx(), SETTINGS)
    assert a[0].route == "AUTO" and {o.kind for o in a[0].ops} == {"attach_document", "set_status"} and a[0].rule_id == "R01"
def test_r02_r03_dates():
    assert route(findings(date_flag("1", po="2026-11-16", doc="2026-11-30")), ctx(), SETTINGS)[0].rule_id == "R02"      # 14 days ≤ 10 + 7
    assert route(findings(date_flag("1", po="2026-11-16", doc="2026-12-10")), ctx(), SETTINGS)[0].route == "REVIEW"      # 24 days
    assert route(findings(date_flag("1", po="2026-11-16", doc="2026-11-10")), ctx(), SETTINGS)[0].rule_id == "R02"      # earlier
    assert route(findings(date_flag("1", po="2026-11-16", doc="2026-11-30")), ctx(supplier_lead_time_days=None), SETTINGS)[0].route == "REVIEW"
def test_r04_r05_prices():
    a = route(findings(price_flag("1", po="12.50", doc="12.70", qty="3")), ctx(), SETTINGS)[0]
    assert a.rule_id == "R04" and {o.kind for o in a.ops} >= {"po_line_price", "article_price"}
    assert route(findings(price_flag("1", po="12.50", doc="14.00", qty="3")), ctx(), SETTINGS)[0].rule_id == "R05"
    assert route(findings(price_flag("1", po="1000.00", doc="1015.00", qty="2")), ctx(), SETTINGS)[0].rule_id == "R05"   # 1.5% but €30 per line
def test_r06_r08_ask_and_messages_off():
    assert route(findings(qty_flag("1")), ctx(), SETTINGS)[0].message.template == "quantity_unit"
    assert route(findings(missing_line_flag("2")), ctx(), SETTINGS)[0].message.template == "line_missing_extra"
    assert route(findings(incoterm_flag()), ctx(), SETTINGS)[0].message.template == "missing_info"
    assert route(findings(qty_flag("1")), ctx(), SETTINGS.model_copy(update={"messages_off_suppliers": ["S1"]}))[0].route == "REVIEW"
def test_r09_uncompared_and_no_version():
    assert route(Findings(version=None, flags=[], matches=[], uncompared=[]), ctx(version=None), SETTINGS)[0].route == "REVIEW"
    assert route(findings(uncompared("price", "total only")), ctx(), SETTINGS)[0].route == "REVIEW"
def test_r10_above_authorization():
    a = route(findings(price_flag("1", po="12.50", doc="12.70", qty="3")), ctx(po_above_authorization=True), SETTINGS)[0]
    assert a.route == "AUTO" and a.rule_id == "R10"
def test_r11_new_supplier():
    a = route(Findings(version=1, flags=[], matches=[], uncompared=[]), ctx(cases_seen_for_supplier=2), SETTINGS)
    assert len(a) == 1 and a[0].route == "REVIEW" and a[0].rule_id == "R11"


# The tests below cover the rulings given with the task, on top of the brief's tests above.

def routed(*items, version=1, settings=SETTINGS, **context) -> list[tuple[str, str, list[str], str]]:
    """The actions for these findings items as (route, rule_id, finding_ids, reason)."""
    return [(a.route, a.rule_id, a.finding_ids, a.reason) for a in route(findings(*items, version=version), ctx(**context), settings)]


def kinds(actions) -> list[list[str]]:
    """The kinds of the operations of each action."""
    return [[op.kind for op in a.ops] for a in actions]


ATTACH = WriteOp(kind="attach_document", po_number="250101", version=1, document="x.pdf")
STATUS = WriteOp(kind="set_status", po_number="250101", version=1, old_value="Besteld", new_value="Bevestigd")
CASE = ("AUTO", "R01", [], "attach and confirm")                     # the last action of a confirmation, as routed() shows it


# --- R01 and the operations of the case (ruling 6 and fix item d) ---

def test_r01_attaches_the_document_and_sets_the_status_in_one_action():
    [a] = route(Findings(version=1, flags=[], matches=[], uncompared=[]), ctx(), SETTINGS)
    assert (a.route, a.rule_id, a.finding_ids, a.reason, a.message) == ("AUTO", "R01", [], "attach and confirm", None)
    assert a.ops == [ATTACH, STATUS]


def test_matches_alone_are_no_difference():
    m = Match(field="price", po_line_ids=["1"], doc_line_ids=["1"], value="12.50")
    assert routed(m) == [CASE]


def test_the_case_operations_come_last_in_an_action_of_their_own():
    actions = route(findings(date_flag("1", po="2026-11-16", doc="2026-11-30"), price_flag("2", po="12.50", doc="12.70", qty="3")),
                    ctx(po_item_code={"2": "KM-1002"}), SETTINGS)
    assert [(a.route, a.rule_id, a.reason) for a in actions] == [
        ("AUTO", "R02", "delivery date within the lead time plus the tolerance"), ("AUTO", "R04", "price within threshold"),
        ("AUTO", "R01", "attach and confirm")]
    assert kinds(actions) == [["po_line_date"], ["po_line_price", "article_price"], ["attach_document", "set_status"]]
    assert (actions[2].ops, actions[2].finding_ids) == ([ATTACH, STATUS], [])


def test_a_mixed_case_attaches_once_and_sets_no_status():
    actions = route(findings(price_flag("1", po="12.50", doc="12.70", qty="3"), qty_flag("2")), ctx(), SETTINGS)
    assert [(a.route, a.rule_id) for a in actions] == [("AUTO", "R04"), ("ASK", "R06"), ("AUTO", "R01")]
    assert kinds(actions) == [["po_line_price", "article_price"], [], ["attach_document"]]


def test_the_case_operations_come_after_the_reviews_and_the_version_item():
    actions = route(findings(uncompared("price", "total only", "2"), date_flag("1", po="2026-11-16", doc="2026-11-30"), version=None),
                    ctx(), SETTINGS)
    assert [(a.route, a.rule_id) for a in actions] == [("AUTO", "R02"), ("REVIEW", "R09"), ("REVIEW", "R09"), ("AUTO", "R01")]
    assert kinds(actions)[-1] == ["attach_document"]


def test_a_case_without_auto_action_attaches_nothing():
    actions = route(findings(qty_flag("1"), uncompared("price", "total only")), ctx(), SETTINGS)
    assert [a.route for a in actions] == ["ASK", "REVIEW"] and kinds(actions) == [[], []]


def test_a_cart_case_gets_no_attachment_and_no_status():
    actions = route(findings(flag("line_presence", "3", "KM-2003", None, "not in cart", doc_line_id=""),
                             price_flag("2", po="40.00", doc="40.50", qty="2")), ctx(kind="cart", po_item_code={"2": "KM-2002"}), SETTINGS)
    assert [(a.route, a.rule_id, a.reason) for a in actions] == [("REVIEW", "R07", "could not be placed"), ("AUTO", "R04", "price within threshold")]
    assert kinds(actions) == [[], ["po_line_price", "article_price"]] and actions[0].message is None
    [nothing] = route(Findings(version=1, flags=[], matches=[], uncompared=[]), ctx(kind="cart"), SETTINGS)
    assert (nothing.route, nothing.rule_id, nothing.reason, nothing.ops) == ("AUTO", "R01", "no difference", [])


def test_on_a_cart_the_questions_are_for_the_buyer():
    items = (qty_flag("1"), flag("unit", "2", "m", "pc", "different"), flag("line_presence", None, None, "KM-2004", "extra line", doc_line_id="4"),
             incoterm_flag())
    for settings in (SETTINGS, SETTINGS.model_copy(update={"messages_off_suppliers": ["S1"]})):
        actions = route(findings(*items), ctx(kind="cart"), settings)
        assert [(a.route, a.rule_id, a.finding_ids, a.reason, a.message, a.ops) for a in actions] == [
            ("REVIEW", rule, [f"F{n}"], "cart: for the buyer", None, []) for n, rule in enumerate(("R06", "R06", "R07", "R08"), 1)]


def test_a_price_above_threshold_on_a_cart_is_the_review_reason_the_pre_check_shows():
    assert routed(price_flag("2", po="40.00", doc="41.00", qty="2"), kind="cart") == [("REVIEW", "R05", ["F1"], "price above threshold")]


# --- R02 and R03, the delivery date (ruling 4) ---

def test_a_week_is_compared_on_its_friday():
    # The PO's 2026-11-16 plus 10 days of lead time plus 7 of tolerance is 2026-12-03.
    assert routed(date_flag("1", po="2026-11-16", doc="2026-W48"))[0][:2] == ("AUTO", "R02")       # Friday 2026-11-27
    assert routed(date_flag("1", po="2026-11-16", doc="2026-W49"))[0][:2] == ("REVIEW", "R03")     # Monday 2026-11-30, Friday 2026-12-04


def test_a_week_whose_friday_ends_the_lead_time_is_within():
    # 11 days of lead time and 7 of tolerance after 2026-11-16 end on Friday 2026-12-04, the Friday of week 49; 10 days end a
    # day earlier.
    assert routed(date_flag("1", po="2026-11-16", doc="2026-W49"), supplier_lead_time_days=11)[0][:2] == ("AUTO", "R02")
    assert routed(date_flag("1", po="2026-11-16", doc="2026-W49"), supplier_lead_time_days=10)[0][:2] == ("REVIEW", "R03")


def test_a_range_is_compared_on_its_last_day():
    assert routed(date_flag("1", po="2026-11-16", doc="2026-11-26/2026-12-03"))[0][:2] == ("AUTO", "R02")
    assert routed(date_flag("1", po="2026-11-16", doc="2026-11-30/2026-12-04"))[0][:2] == ("REVIEW", "R03")


def test_the_lead_time_boundary_is_within():
    assert routed(date_flag("1", po="2026-11-16", doc="2026-12-03")) == [
        ("AUTO", "R02", ["F1"], "delivery date within the lead time plus the tolerance"), CASE]
    assert routed(date_flag("1", po="2026-11-16", doc="2026-12-04")) == [
        ("REVIEW", "R03", ["F1"], "delivery date beyond the lead time plus the tolerance")]


def test_the_lead_time_and_the_tolerance_come_from_the_context_and_the_settings():
    late = date_flag("1", po="2026-11-16", doc="2026-11-30")                                          # 14 days later
    assert routed(late, supplier_lead_time_days=7)[0][:2] == ("AUTO", "R02")                           # 7 + 7
    assert routed(late, supplier_lead_time_days=6)[0][:2] == ("REVIEW", "R03")                         # 6 + 7
    assert routed(late, settings=SETTINGS.model_copy(update={"lead_time_tolerance_days": 4}))[0][:2] == ("AUTO", "R02")    # 10 + 4
    assert routed(late, settings=SETTINGS.model_copy(update={"lead_time_tolerance_days": 3}))[0][:2] == ("REVIEW", "R03")  # 10 + 3


def test_an_unknown_lead_time_sends_a_later_date_to_review():
    assert routed(date_flag("1", po="2026-11-16", doc="2026-11-17"), supplier_lead_time_days=None) == [
        ("REVIEW", "R03", ["F1"], "no lead time known for the supplier")]


def test_an_earlier_date_needs_no_lead_time():
    # R02 is tried before R03: a date earlier than the PO's is AUTO whatever the lead time.
    for doc in ("2026-11-10", "2026-W46", "2026-11-09/2026-11-13"):                                   # Friday of W46: 2026-11-13
        assert routed(date_flag("1", po="2026-11-16", doc=doc), supplier_lead_time_days=None) == [
            ("AUTO", "R02", ["F1"], "delivery date earlier than the PO date"), CASE]


def test_a_po_date_that_is_a_week_is_compared_on_its_friday_too():
    # A PO normally states a day. Week 47 is compared on Friday 2026-11-20: the document's 2026-11-18 is earlier and needs no
    # lead time, and 2026-12-07 is within 10 + 7 days. The context has no date for the line, so the flag's PO value counts.
    assert routed(flag("delivery_date", "1", "2026-W47", "2026-11-18", "later"), po_date={}, supplier_lead_time_days=None)[0][:2] == (
        "AUTO", "R02")
    assert routed(flag("delivery_date", "1", "2026-W47", "2026-12-07", "later"), po_date={})[0][:2] == ("AUTO", "R02")
    assert routed(flag("delivery_date", "1", "2026-W47", "2026-12-08", "later"), po_date={})[0][:2] == ("REVIEW", "R03")


def test_the_po_day_itself_is_not_earlier():
    # The comparison flags no date on a PO day, but a PO week is compared on its Friday, on which the document may deliver.
    assert routed(flag("delivery_date", "1", "2026-W47", "2026-11-20", "later"), po_date={}, supplier_lead_time_days=None) == [
        ("REVIEW", "R03", ["F1"], "no lead time known for the supplier")]


def test_the_date_operation_writes_the_document_date_over_the_po_date():
    [a, case] = route(findings(date_flag("1", po="2026-11-16", doc="2026-W48")), ctx(), SETTINGS)
    assert a.ops == [WriteOp(kind="po_line_date", po_number="250101", version=1, line_id="1", old_value="2026-11-16", new_value="2026-W48")]
    assert case.ops == [ATTACH, STATUS]


def test_the_po_date_comes_from_the_context_and_else_from_the_flag():
    # PO 2026-11-20 in the context: 2026-12-07 is within 10 + 7 days of it, though not of the flag's 2026-11-16.
    a = route(findings(date_flag("1", po="2026-11-16", doc="2026-12-07")), ctx(po_date={"1": "2026-11-20"}), SETTINGS)[0]
    assert (a.rule_id, a.ops[0].old_value) == ("R02", "2026-11-20")
    b = route(findings(date_flag("2", po="2026-11-16", doc="2026-11-30")), ctx(), SETTINGS)[0]       # line 2 is not in po_date
    assert (b.rule_id, b.ops[0].line_id, b.ops[0].old_value) == ("R02", "2", "2026-11-16")


def test_a_delivery_date_missing_on_the_document_goes_to_review():
    assert routed(date_flag("1", po="2026-11-16", doc=None)) == [("REVIEW", "R03", ["F1"], "no delivery date on the document")]


# --- R04 and R05, the price (rulings 2, 3 and 5) ---

def test_the_percentage_boundary_is_within():
    for doc in ("12.75", "12.25"):                                                                   # 2% of 12.50 is 0.25
        assert routed(price_flag("1", po="12.50", doc=doc, qty="3")) == [("AUTO", "R04", ["F1"], "price within threshold"), CASE]
    for doc in ("12.76", "12.24"):
        assert routed(price_flag("1", po="12.50", doc=doc, qty="3")) == [("REVIEW", "R05", ["F1"], "price above threshold")]


def test_the_euro_boundary_is_within():
    assert routed(price_flag("1", po="100.00", doc="101.25", qty="20"))[0][:2] == ("AUTO", "R04")     # 1.25 x 20 = 25.00
    assert routed(price_flag("1", po="100.00", doc="101.25", qty="21"))[0][:2] == ("REVIEW", "R05")   # 26.25
    assert routed(price_flag("1", po="100.00", doc="101.26", qty="20"))[0][:2] == ("REVIEW", "R05")   # 25.20


def test_the_thresholds_come_from_the_settings():
    wider = SETTINGS.model_copy(update={"price_pct": SETTINGS.price_pct * 2, "price_abs": SETTINGS.price_abs * 2})
    assert routed(price_flag("1", po="12.50", doc="14.00", qty="3"), settings=wider)[0][:2] == ("REVIEW", "R05")    # 12%
    assert routed(price_flag("1", po="12.50", doc="12.95", qty="3"), settings=wider)[0][:2] == ("AUTO", "R04")      # 3.6%
    assert routed(price_flag("1", po="1000.00", doc="1015.00", qty="2"), settings=wider)[0][:2] == ("AUTO", "R04")  # 30.00


def test_the_euro_rule_counts_the_difference_per_piece_at_the_po_basis():
    # 12.50 and 12.70 per 100 pieces: 0.002 a piece, 0.60 on 300 pieces; read per piece it would be 60.00.
    assert routed(price_flag("1", po="12.50", doc="12.70", qty="300"), po_basis={"1": "100"})[0][:2] == ("AUTO", "R04")
    assert routed(price_flag("1", po="12.50", doc="12.70", qty="300"))[0][:2] == ("REVIEW", "R05")
    assert routed(price_flag("1", po="12.50", doc="12.70", qty="12600"), po_basis={"1": "100"})[0][:2] == ("REVIEW", "R05")   # 25.20


def test_the_euro_rule_reads_the_quantity_without_its_sign():
    assert routed(price_flag("1", po="100.00", doc="101.25", qty="-21"))[0][:2] == ("REVIEW", "R05")


def test_the_quantity_comes_from_the_quantity_flag_when_the_quantity_differs():
    # No quantity Match: the PO value of the line's quantity flag counts, 20 or 30 pieces at 1.00 more a piece.
    assert routed(price_flag("1", po="100.00", doc="101.00", qty=None), flag("quantity", "1", "20", "25", "different")) == [
        ("AUTO", "R04", ["F1"], "price within threshold"), ("ASK", "R06", ["F2"], "ask the supplier about the quantity or unit"), CASE]
    assert routed(price_flag("1", po="100.00", doc="101.00", qty=None), flag("quantity", "1", "30", "25", "different"))[0][:2] == (
        "REVIEW", "R05")


def test_the_quantity_is_that_of_the_same_po_line():
    other = Match(field="quantity", po_line_ids=["2"], doc_line_ids=["2"], value="3")
    assert routed(price_flag("1", po="12.50", doc="12.70", qty=None), other) == [
        ("REVIEW", "R05", ["F1"], "quantity unknown for the price rule")]


def test_a_price_flag_without_a_quantity_goes_to_review():
    [a] = route(findings(price_flag("1", po="12.50", doc="12.70", qty=None)), ctx(), SETTINGS)
    assert (a.route, a.rule_id, a.reason, a.ops) == ("REVIEW", "R05", "quantity unknown for the price rule", [])


def test_a_price_missing_on_the_document_goes_to_review():
    assert routed(price_flag("1", po="12.50", doc=None, qty="3")) == [("REVIEW", "R05", ["F1"], "no price on the document")]


def test_the_price_operations_write_the_document_net_price_over_the_po_net_price():
    # The article's current price is not in the findings: its old value is None, for the writer to read (fix item f).
    [a, case] = route(findings(price_flag("1", po="12.50", doc="12.70", qty="3")), ctx(), SETTINGS)
    assert a.ops == [WriteOp(kind="po_line_price", po_number="250101", version=1, line_id="1", old_value="12.50", new_value="12.70"),
                     WriteOp(kind="article_price", po_number="250101", version=1, article_code="KM-1001", old_value=None, new_value="12.70")]
    assert case.ops == [ATTACH, STATUS]


def test_an_unknown_item_code_makes_no_article_price_operation():
    [a, case] = route(findings(price_flag("1", po="12.50", doc="12.70", qty="3")), ctx(po_item_code={}), SETTINGS)
    assert (a.route, a.rule_id) == ("AUTO", "R04") and kinds([a, case]) == [["po_line_price"], ["attach_document", "set_status"]]
    assert a.reason == "price within threshold; article price not updated: item code unknown"


# --- split lines (fix item a) ---

def split_line(line_id="1", parts=("1", "2")) -> Match:
    """The line presence Match of a PO line that several document lines answer."""
    return Match(field="line_presence", po_line_ids=[line_id], doc_line_ids=list(parts), value="split")


SPLIT = "split line: parts differ"


def test_a_split_line_whose_parts_differ_in_price_goes_to_review():
    quantity = Match(field="quantity", po_line_ids=["1"], doc_line_ids=["1", "2"], value="10")

    def part(doc_line, doc):
        return flag("price", "1", "12.50", doc, "different", doc_line_id=doc_line)
    # two parts that differ, a flag each
    assert routed(part("1", "12.70"), part("2", "12.60"), quantity) == [("REVIEW", "R05", ["F1"], SPLIT), ("REVIEW", "R05", ["F2"], SPLIT)]
    # one part agrees, one differs: the comparison flags the one, and the line presence Match tells the line is split
    assert routed(part("2", "12.70"), quantity, split_line()) == [("REVIEW", "R05", ["F1"], SPLIT)]
    # the other part as a price Match or as an uncompared price
    assert routed(part("2", "12.70"), quantity, Match(field="price", po_line_ids=["1"], doc_line_ids=["1"], value="12.50")) == [
        ("REVIEW", "R05", ["F1"], SPLIT)]
    assert routed(part("2", "12.70"), quantity, uncompared("price", "unreadable or unverified source", "1")) == [
        ("REVIEW", "R05", ["F1"], SPLIT), ("REVIEW", "R09", ["U1"], "unreadable or unverified source")]
    # a split part without a price is a split line too
    assert routed(part("2", None), quantity, split_line()) == [("REVIEW", "R05", ["F1"], SPLIT)]
    # a line one document line answers, and a line beside a split one, are not split
    for other in (Match(field="line_presence", po_line_ids=["1"], doc_line_ids=["1"], value="matched"), split_line("2", ("2", "3"))):
        assert routed(part("1", "12.70"), quantity, other)[0][:2] == ("AUTO", "R04")


def test_a_split_line_whose_parts_differ_in_date_goes_to_review():
    def part(doc_line, doc):
        return flag("delivery_date", "1", "2026-11-16", doc, "later", doc_line_id=doc_line)
    assert routed(part("1", "2026-11-20"), part("2", "2026-11-30")) == [("REVIEW", "R03", ["F1"], SPLIT), ("REVIEW", "R03", ["F2"], SPLIT)]
    assert routed(part("2", "2026-11-30"), split_line()) == [("REVIEW", "R03", ["F1"], SPLIT)]
    assert routed(part("2", "2026-11-30"), Match(field="delivery_date", po_line_ids=["1"], doc_line_ids=["1"], value="2026-11-16")) == [
        ("REVIEW", "R03", ["F1"], SPLIT)]
    assert routed(part("2", None), split_line()) == [("REVIEW", "R03", ["F1"], SPLIT)]
    assert routed(part("1", "2026-11-30"), split_line("2", ("2", "3")))[0][:2] == ("AUTO", "R02")


# --- R06 to R08, the questions to the supplier (ruling 10) ---

def test_each_question_carries_its_items_as_plain_strings():
    [quantity] = route(findings(qty_flag("1")), ctx(), SETTINGS)
    assert (quantity.route, quantity.rule_id, quantity.reason, quantity.ops) == ("ASK", "R06", "ask the supplier about the quantity or unit", [])
    assert quantity.message == MessageSpec(template="quantity_unit", items=[
        {"po_line_ids": "1", "doc_line_ids": "1", "field": "quantity", "po_value": "3", "doc_value": "5"}])
    [missing] = route(findings(missing_line_flag("2")), ctx(), SETTINGS)
    assert (missing.rule_id, missing.reason) == ("R07", "ask the supplier about the missing or extra line")
    assert missing.message.items == [{"po_line_ids": "2", "doc_line_ids": "", "field": "line_presence", "po_value": "KM-1002", "doc_value": ""}]
    [info] = route(findings(incoterm_flag()), ctx(), SETTINGS)
    assert (info.rule_id, info.reason) == ("R08", "ask the supplier about the required information")
    assert info.message.items == [{"po_line_ids": "", "doc_line_ids": "", "field": "required_info", "po_value": "DAP Rotterdam", "doc_value": ""}]


def test_every_quantity_unit_line_and_required_info_flag_is_asked():
    for item, rule, template in ((flag("quantity", "1", "3", None, "missing on the document"), "R06", "quantity_unit"),
                                 (flag("unit", "1", "m", "pc", "different"), "R06", "quantity_unit"),
                                 (flag("unit", "1", "pc", None, "missing on the document"), "R06", "quantity_unit"),
                                 (flag("line_presence", None, None, "KM-1803", "extra line", doc_line_id="3"), "R07", "line_missing_extra"),
                                 (flag("required_info", None, "DAP Rotterdam", "EXW Venlo", "different"), "R08", "missing_info")):
        [a] = route(findings(item), ctx(), SETTINGS)
        assert (a.route, a.rule_id, a.message.template) == ("ASK", rule, template)


def test_a_message_item_falls_back_on_the_printed_text():
    item = Flag(flag_id="F1", field="line_presence", po_line_ids=[], doc_line_ids=["3"], po_value=None,
                doc_value=DocValue(text="Kugelhahn DN15"), detail="extra line")
    [a] = route(Findings(version=1, flags=[item], matches=[], uncompared=[]), ctx(), SETTINGS)
    assert a.message.items == [{"po_line_ids": "", "doc_line_ids": "3", "field": "line_presence", "po_value": "", "doc_value": "Kugelhahn DN15"}]


def test_messages_off_turns_every_question_into_review():
    off = SETTINGS.model_copy(update={"messages_off_suppliers": ["S1"]})
    for item, rule in ((qty_flag("1"), "R06"), (missing_line_flag("2"), "R07"), (incoterm_flag(), "R08")):
        [a] = route(findings(item), ctx(), off)
        assert (a.route, a.rule_id, a.finding_ids, a.reason, a.message) == ("REVIEW", rule, ["F1"], "messages off for this supplier", None)
    assert routed(qty_flag("1"), settings=off, supplier="S2")[0][:2] == ("ASK", "R06")


def test_the_questions_of_one_template_are_one_action():
    items = (qty_flag("1"), missing_line_flag("3"), flag("unit", "2", "m", "pc", "different"), incoterm_flag(),
             flag("line_presence", None, None, "KM-1805", "extra line", doc_line_id="5"), flag("quantity", "4", "8", None, "missing on the document"))
    actions = route(findings(*items), ctx(), SETTINGS)
    assert [(a.route, a.rule_id, a.finding_ids, a.reason, a.message.template) for a in actions] == [
        ("ASK", "R06", ["F1", "F3", "F6"], "ask the supplier about the quantity or unit", "quantity_unit"),
        ("ASK", "R07", ["F2", "F5"], "ask the supplier about the missing or extra line", "line_missing_extra"),
        ("ASK", "R08", ["F4"], "ask the supplier about the required information", "missing_info")]
    assert actions[0].message.items == [
        {"po_line_ids": "1", "doc_line_ids": "1", "field": "quantity", "po_value": "3", "doc_value": "5"},
        {"po_line_ids": "2", "doc_line_ids": "2", "field": "unit", "po_value": "m", "doc_value": "pc"},
        {"po_line_ids": "4", "doc_line_ids": "4", "field": "quantity", "po_value": "8", "doc_value": ""}]
    assert actions[1].message.items == [
        {"po_line_ids": "3", "doc_line_ids": "", "field": "line_presence", "po_value": "KM-1003", "doc_value": ""},
        {"po_line_ids": "", "doc_line_ids": "5", "field": "line_presence", "po_value": "", "doc_value": "KM-1805"}]
    off = route(findings(*items), ctx(), SETTINGS.model_copy(update={"messages_off_suppliers": ["S1"]}))
    assert [(a.route, a.finding_ids) for a in off] == [("REVIEW", [f"F{n}"]) for n in range(1, 7)]


def test_a_line_not_in_the_cart_goes_to_review_with_messages_on_or_off():
    not_in_cart = flag("line_presence", "3", "KM-2003", None, "not in cart", doc_line_id="")
    for settings in (SETTINGS, SETTINGS.model_copy(update={"messages_off_suppliers": ["S1"]})):
        assert routed(not_in_cart, kind="cart", settings=settings) == [("REVIEW", "R07", ["F1"], "could not be placed")]


# --- R09, the version and the uncompared items (ruling 8) ---

def test_no_version_in_the_findings_is_one_review_action_after_the_others():
    actions = route(findings(qty_flag("1"), date_flag("1", po="2026-11-16", doc="2026-11-30"), version=None), ctx(version=3), SETTINGS)
    assert [(a.route, a.rule_id, a.finding_ids, a.reason) for a in actions] == [
        ("ASK", "R06", ["F1"], "ask the supplier about the quantity or unit"),
        ("AUTO", "R02", ["F2"], "delivery date within the lead time plus the tolerance"),
        ("REVIEW", "R09", [], "no version selected"), CASE]
    assert [op.version for a in actions for op in a.ops] == [3, 3]           # the date and the attachment, at the context's version
    assert routed(version=None) == [("REVIEW", "R09", [], "no version selected")]                     # and no R01


def test_no_version_anywhere_writes_and_asks_nothing():
    items = (date_flag("1", po="2026-11-16", doc="2026-11-30"), qty_flag("2"), price_flag("3", po="12.50", doc="12.70", qty="3"),
             uncompared("price", "total only", "4"))
    actions = route(findings(*items, version=None), ctx(version=None), SETTINGS)
    assert [(a.route, a.rule_id, a.finding_ids, a.reason, a.ops, a.message) for a in actions] == [
        ("REVIEW", "R09", ["F1", "F2", "F3", "U1"], "no version selected", [], None), ("REVIEW", "R09", [], "no version selected", [], None)]
    nothing = route(Findings(version=None, flags=[], matches=[], uncompared=[]), ctx(version=None), SETTINGS)
    assert [(a.route, a.rule_id, a.finding_ids, a.reason) for a in nothing] == [("REVIEW", "R09", [], "no version selected")]
    # one version, in the findings or in the context, is enough for the flags to route by their rules
    assert [a.rule_id for a in route(findings(*items, version=None), ctx(version=2), SETTINGS)] == ["R02", "R06", "R04", "R09", "R09", "R01"]
    assert [a.rule_id for a in route(findings(*items, version=2), ctx(version=None), SETTINGS)] == ["R02", "R06", "R04", "R09", "R01"]


def test_the_operations_carry_the_version_of_the_findings():
    [a, case] = route(findings(date_flag("1", po="2026-11-16", doc="2026-11-30"), version=2), ctx(version=1), SETTINGS)
    assert [op.version for op in a.ops + case.ops] == [2, 2, 2]


def test_each_reason_of_the_uncompared_items_is_one_review_action():
    assert routed(uncompared("price", "total only", "1"), uncompared("quantity", "unknown unit conversion", "2"),
                  uncompared("price", "total only", "3")) == [
        ("REVIEW", "R09", ["U1", "U3"], "total only"), ("REVIEW", "R09", ["U2"], "unknown unit conversion")]


# --- R10, the PO above the buyer's authorization (ruling 7) ---

def test_r10_covers_every_auto_action_on_a_po_line_and_keeps_its_operations():
    items = (price_flag("1", po="12.50", doc="12.70", qty="3"), date_flag("2", po="2026-11-16", doc="2026-11-30"),
             price_flag("3", po="12.50", doc="12.70", qty="3"))
    plain = route(findings(*items), ctx(), SETTINGS)
    above = route(findings(*items), ctx(po_above_authorization=True), SETTINGS)
    assert [(a.route, a.rule_id) for a in above] == [("AUTO", "R10")] * 3 + [("AUTO", "R01")]
    assert [a.ops for a in above] == [a.ops for a in plain] and [a.finding_ids for a in above] == [["F1"], ["F2"], ["F3"], []]
    assert [a.reason for a in above] == ["re-approval requested; R04 price within threshold",
                                         "re-approval requested; R02 delivery date within the lead time plus the tolerance",
                                         "re-approval requested; R04 price within threshold; article price not updated: item code unknown",
                                         "attach and confirm"]


def test_r10_needs_a_po_known_to_be_above_the_authorization():
    # None with no authorization limit set: the store cannot tell, and nothing changes (Task 15 passes the PO version's value on).
    no_limit = SETTINGS.model_copy(update={"authorization_limit": None})
    assert routed(price_flag("1", po="12.50", doc="12.70", qty="3"), settings=no_limit, po_above_authorization=None)[0][:2] == ("AUTO", "R04")


def test_r10_sends_the_writes_on_a_po_of_unknown_total_to_review_when_a_limit_is_set():
    """Final review, finding 32: with an authorization limit set, None means the PO's total is unknown, so the PO may be above
    the authorization: every AUTO action that writes to a PO line becomes REVIEW under R10, "PO total unknown", without its
    operations and with the rule it had as base_rule_id. The other actions are left alone, and with no AUTO action left R01 attaches
    nothing."""
    items = (price_flag("1", po="12.50", doc="12.70", qty="3"), date_flag("2", po="2026-11-16", doc="2026-11-20"), qty_flag("3"))
    actions = route(findings(*items), ctx(po_above_authorization=None), SETTINGS)
    assert [(a.route, a.rule_id, a.finding_ids, a.reason, a.base_rule_id, a.ops) for a in actions] == [
        ("REVIEW", "R10", ["F1"], "PO total unknown", "R04", []), ("REVIEW", "R10", ["F2"], "PO total unknown", "R02", []),
        ("ASK", "R06", ["F3"], "ask the supplier about the quantity or unit", None, [])]
    assert routed(po_above_authorization=None) == [CASE]                              # no write to a PO line: attach and confirm
    assert routed(qty_flag("1"), po_above_authorization=None) == routed(qty_flag("1"))
    assert "authorization_limit" in RULE_SETTINGS["R10"]


def test_r10_leaves_the_other_actions_alone():
    items = (qty_flag("1"), price_flag("2", po="12.50", doc="14.00", qty="3"), uncompared("price", "total only", "3"))
    assert routed(*items, po_above_authorization=True) == routed(*items)
    assert routed(po_above_authorization=True) == [CASE]


# --- R11, the supplier the buyer reviews (ruling 8) ---

def test_r11_lists_every_finding_and_nothing_else_fires():
    items = (price_flag("1", po="12.50", doc="12.70", qty="3"), qty_flag("2"), uncompared("price", "total only", "3"),
             uncompared("price", "match uncertain", "4"))
    for version in (1, None):
        [a] = route(findings(*items, version=version), ctx(cases_seen_for_supplier=2, version=version), SETTINGS)
        assert (a.route, a.rule_id, a.finding_ids, a.reason, a.ops, a.message) == (
            "REVIEW", "R11", ["F1", "F2", "U1", "U2"], "new supplier", [], None)


def test_r11_for_a_supplier_marked_for_review():
    marked = SETTINGS.model_copy(update={"review_suppliers": ["S1"]})
    assert routed(price_flag("1", po="12.50", doc="12.70", qty="3"), settings=marked) == [
        ("REVIEW", "R11", ["F1"], "supplier marked for review")]
    assert routed(settings=marked) == [("REVIEW", "R11", [], "supplier marked for review")]
    assert routed(settings=marked, supplier="S2") == [CASE]


def test_r11_reads_the_number_of_first_cases_from_the_settings():
    two = SETTINGS.model_copy(update={"first_cases_review": 2})
    assert routed(settings=two, cases_seen_for_supplier=1) == [("REVIEW", "R11", [], "new supplier")]
    assert routed(settings=two, cases_seen_for_supplier=2) == [CASE]


# --- the order of the actions and the fallback (rulings 8 and 9) ---

def test_the_actions_come_in_the_order_of_their_findings():
    items = (qty_flag("1"), uncompared("price", "total only", "4"), price_flag("2", po="12.50", doc="12.70", qty="3"),
             missing_line_flag("3"), uncompared("quantity", "unknown unit conversion", "5"), uncompared("price", "total only", "6"),
             flag("unit", "7", "m", "pc", "different"))
    assert [(rule, ids) for _, rule, ids, _ in routed(*items, version=None)] == [
        ("R06", ["F1", "F4"]), ("R04", ["F2"]), ("R07", ["F3"]), ("R09", ["U1", "U3"]), ("R09", ["U2"]), ("R09", []), ("R01", [])]


def test_the_flags_are_taken_in_the_order_of_their_ids():
    flags = [item.model_copy(update={"flag_id": flag_id}) for item, flag_id in (
        (qty_flag("1"), "F10"), (date_flag("2", po="2026-11-16", doc="2026-11-30"), "F2"), (missing_line_flag("3"), "F1"))]
    actions = route(Findings(version=1, flags=flags, matches=[], uncompared=[]), ctx(), SETTINGS)
    assert [a.finding_ids for a in actions] == [["F1"], ["F2"], ["F10"], []]
    questions = [qty_flag(line_id).model_copy(update={"flag_id": flag_id}) for line_id, flag_id in (("1", "F10"), ("2", "F2"))]
    assert [a.finding_ids for a in route(Findings(version=1, flags=questions, matches=[], uncompared=[]), ctx(), SETTINGS)] == [["F2", "F10"]]


def test_a_finding_no_rule_covers_goes_to_review():
    future = Flag.model_construct(flag_id="F1", field="price", po_line_ids=["1"], doc_line_ids=["1"], po_value=value("12.50"),
                                  doc_value=value("12.70"), detail="surcharge")
    two_lines = {"po_line_ids": ["1", "2"]}                                                          # a merged pair is Uncompared
    for item in (future, flag("unit", "1", "pc", "pc", "later"), flag("price", None, "12.50", "12.70", "different"),
                 flag("price", "1", "12.50", "12.70", "different").model_copy(update=two_lines),
                 flag("delivery_date", None, "2026-11-16", "2026-11-30", "later"),
                 flag("delivery_date", "1", "2026-11-16", "2026-11-30", "later").model_copy(update=two_lines),
                 flag("price", "1", None, "12.70", "different"), flag("delivery_date", "2", None, "2026-11-30", "later")):
        [a] = route(Findings(version=1, flags=[item.model_copy(update={"flag_id": "F1"})], matches=[], uncompared=[]), ctx(), SETTINGS)
        assert (a.route, a.rule_id, a.finding_ids, a.reason, a.ops, a.message) == ("REVIEW", "R00", ["F1"], "not covered by a rule", [], None)


def test_the_routing_changes_nothing_it_is_given():
    f, c = findings(price_flag("1", po="12.50", doc="12.70", qty="3"), qty_flag("2")), ctx(po_above_authorization=True)
    before = (f.model_copy(deep=True), c.model_copy(deep=True), SETTINGS.model_copy(deep=True))
    first, second = route(f, c, SETTINGS), route(f, c, SETTINGS)
    assert first == second and (f, c, SETTINGS) == before


# --- what the pipeline reads of the routing for the audit record (review round 1 of Task 15) ---

def test_r10_names_the_rule_it_took_its_writes_from():
    items = (price_flag("1", po="12.50", doc="12.70", qty="3"), date_flag("2", po="2026-11-16", doc="2026-11-30"))
    above = route(findings(*items), ctx(po_above_authorization=True), SETTINGS)
    assert [(a.rule_id, a.base_rule_id) for a in above] == [("R10", "R04"), ("R10", "R02"), ("R01", None)]
    assert [a.base_rule_id for a in route(findings(*items), ctx(), SETTINGS)] == [None, None, None]


def test_rule_settings_name_settings_for_every_rule():
    assert set(RULE_SETTINGS) == {"R00", *(f"R{n:02d}" for n in range(1, 12))}
    assert {name for names in RULE_SETTINGS.values() for name in names} <= set(Settings.model_fields)


def test_findings_by_id_gives_the_finding_each_action_names():
    f = findings(qty_flag("1"), uncompared("price", "total only", "2"), uncompared("unit", "match uncertain", "3"),
                 uncompared("quantity", "total only", "4"))
    by_id = findings_by_id(f)
    assert sorted(i for a in route(f, ctx(), SETTINGS) for i in a.finding_ids) == sorted(by_id) == ["F1", "U1", "U2", "U3"]
    assert (by_id["F1"], by_id["U1"], by_id["U2"], by_id["U3"]) == (f.flags[0], *f.uncompared)
