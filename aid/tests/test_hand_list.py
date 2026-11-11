"""Tests of the list 'In Exact invoeren' as the page shows it (aid/review/hand_list.py): its blocks in their order, its rows with the
item code of the version each case was compared with, the values in the two languages, and the hint on an order above the
authorization, which shows no amount. The cases are made-up case folders with step 01 as the pipeline writes it, their changes
prepared by the prepared writer."""
import csv
import io
import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from aid.case import load_case, open_case
from aid.execution.by_hand import changes, open_changes, put_back_of, reconcile, reopen
from aid.execution.ops import WriteOp
from aid.execution.by_hand import mark
from aid.execution.writer import UNDO_RULE_ID, PreparedWriter, WriteLog, undo_case
from aid.madeup.generate import MadeUpSpec, confirmation_pdf, export_csv, load_specs
from aid.pipeline import record_dispositions, run_case
from aid.po_store import PoStore, PoVersion
from aid.review.hand_list import blocks, confirmation_lines, confirmations, done_rows, ids_of, row_text, take_back, tick_rows
from aid.review.i18n import STRINGS
from aid.sources.upload import open_upload_case
from aid.tests.conftest import REPO_ROOT, deps
from aid.tests.fakes import case_result, line, po_version
from aid.tests.helpers import EXPORT_COLUMNS, SPEC_DIR, filed_on, newer_po, po_number_of

NOW = datetime(2026, 11, 18, 9, 0)


class Asking:
    """Cases of made-up POs that prepare changes, as a run leaves them: a case folder under tmp_path/cases with step 01 (the versions
    of its PO) and the rows of the prepared writer in tmp_path/prepared."""

    def __init__(self, tmp_path: Path, store: PoStore):
        self.root, self.store, self.prepared = tmp_path / "cases", store, tmp_path / "prepared"
        self.writer, self.opened = PreparedWriter(self.prepared), 0
        self.document = tmp_path / "conf.pdf"
        self.document.write_bytes(b"%PDF-1.4 made up")

    def case(self, po_number: str, versions: list[PoVersion] | None = None) -> str:
        """A new case of the PO, whose step 01 holds these versions (by default those the store holds): its id."""
        self.opened += 1
        made = open_case(self.root, "confirmation", [self.document], po_number, datetime(2026, 11, 18, 9, self.opened))
        held = self.store.versions(po_number) if versions is None else versions
        made.write_step(1, "po_versions", {"po_number": po_number, "versions": [version.model_dump(mode="json") for version in held]})
        return made.case_id

    def ask(self, case_id: str, *ops: WriteOp) -> list[str]:
        """The operations prepared for the case: their entry ids."""
        return [self.writer.apply(op, case_id, "R04", {}, NOW).entry_id for op in ops]

    def open(self):
        return open_changes(self.prepared)


def on_line(kind: str, po: str, line_id: str, old: str, new: str, version: int = 1) -> WriteOp:
    return WriteOp(kind=kind, po_number=po, version=version, line_id=line_id, old_value=old, new_value=new)


def article(code: str, new: str, old: str | None = None, po: str = "250108") -> WriteOp:
    return WriteOp(kind="article_price", po_number=po, version=1, article_code=code, old_value=old, new_value=new)


def test_the_blocks_are_by_order_and_line_then_the_article_prices(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    asking = Asking(tmp_path, d.store)
    # asked in an order unlike the list's: PO 250114 first, the lines of PO 250104 mixed, an article price among them
    date14 = asking.ask(asking.case("250114"), on_line("po_line_date", "250114", "1", "2026-11-16", "2026-W48"))
    line10, quantity2, article02, price2, date2 = asking.ask(
        asking.case("250104"), on_line("po_line_price", "250104", "10", "1.00", "1.10"), on_line("po_line_quantity", "250104", "2", "5", "6"),
        article("KM-0402", "2.10", po="250104"), on_line("po_line_price", "250104", "2", "2.00", "2.10"),
        on_line("po_line_date", "250104", "2", "2026-11-16", "2026-11-20"))
    unknown = asking.ask(asking.case("99999", versions=[]), on_line("po_line_quantity", "99999", "1", "1", "2"))     # a PO the store lacks
    price08, article01 = asking.ask(asking.case("250108"), on_line("po_line_price", "250108", "1", "12.50", "12.70"), article("KM-0801", "12.70"))
    shown = blocks(asking.open(), d.store, asking.root, "nl")
    assert [(block_id, heading) for block_id, heading, _ in shown] == [
        ("po:99999", "Inkooporder 99999"), ("po:250104", "Inkooporder 250104 (S04)"), ("po:250108", "Inkooporder 250108 (S08)"),
        ("po:250114", "Inkooporder 250114 (S14)"), ("articles", "Artikelprijzen")]                 # the PO numbers as numbers
    assert [[ids_of(value) for _, value in rows] for _, _, rows in shown] == [
        [unknown], [[price2], [date2], [quantity2], [line10]], [[price08]], [date14], [[article02], [article01]]]
    assert [text for text, _ in shown[1][2]] == ["regel 2 (KM-0402): prijs 2,00 → 2,10", "regel 2 (KM-0402): leverdatum 16-11-2026 → 20-11-2026",
                                                 "regel 2 (KM-0402): aantal 5 → 6", "regel 10 (KM-0410): prijs 1,00 → 1,10"]
    assert shown[0][2] == [("regel 1 (onbekend): aantal 1 → 2", unknown[0])]
    assert [text for text, _ in shown[4][2]] == ["artikel KM-0402: inkoopprijs onbekend → 2,10", "artikel KM-0801: inkoopprijs onbekend → 12,70"]
    english = blocks(asking.open(), d.store, asking.root, "en")
    assert [heading for _, heading, _ in english] == ["Purchase order 99999", "Purchase order 250104 (S04)", "Purchase order 250108 (S08)",
                                                      "Purchase order 250114 (S14)", "Article prices"]
    # a change asked by two cases is one row, whose value holds the entry ids of both
    again = asking.ask(asking.case("250108"), on_line("po_line_price", "250108", "1", "12.50", "12.70"))
    [(text, value)] = [rows for block_id, _, rows in blocks(asking.open(), d.store, asking.root, "nl") if block_id == "po:250108"][0]
    assert text == "regel 1 (KM-0801): prijs 12,50 → 12,70" and value == f"{price08}+{again[0]}" and ids_of(value) == [price08, again[0]]
    assert blocks([], d.store, asking.root, "nl") == []


def test_a_row_shows_the_item_code_of_the_version_its_case_used(tmp_path, madeup_out):
    """The item code of a line is the one of the version that the change's case was compared with (step 01 of the case): a line whose
    article differs between two versions shows the article of each change's own version. No rule code shows."""
    d = deps(tmp_path, madeup_out)
    asking = Asking(tmp_path, d.store)
    versions = [po_version(1, [line("KM-1001", "10", "2.50"), line("KM-1002", "5", "4.00")]),
                po_version(2, [line("KM-1009", "10", "2.50"), line("KM-1002", "5", "4.00")])]      # line 1 holds another article in v2
    first, second = asking.case("250101", versions[:1]), asking.case("250101", versions)            # compared with version 1, with 2
    asking.ask(first, on_line("po_line_price", "250101", "1", "2.50", "2.60", version=1))
    asking.ask(second, on_line("po_line_quantity", "250101", "1", "10", "12", version=2))
    by_kind = {change.kind: change for change in asking.open()}
    assert row_text(by_kind["po_line_price"], asking.root, "nl") == "regel 1 (KM-1001): prijs 2,50 → 2,60"
    assert row_text(by_kind["po_line_quantity"], asking.root, "nl") == "regel 1 (KM-1009): aantal 10 → 12"
    # the newest case that holds the change's version decides; a case without step 01, or whose folder is gone, does not know it
    both = by_kind["po_line_quantity"].model_copy(update={"case_ids": [second, first]})
    assert row_text(both, asking.root, "en") == "line 1 (KM-1009): quantity 10 → 12"
    gone = by_kind["po_line_price"].model_copy(update={"case_ids": ["250101_20261101T090000"]})
    assert row_text(gone, asking.root, "en") == "line 1 (unknown): price 2.50 → 2.60"
    assert not [change for change in asking.open() if change.rule_id in row_text(change, asking.root, "nl")]       # "R04" nowhere


def test_values_in_dutch_and_in_english(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    asking = Asking(tmp_path, d.store)
    asking.ask(asking.case("250101"), on_line("po_line_price", "250101", "1", "1234.50", "1240.00"), on_line("po_line_quantity", "250101", "1", "2.5", "3"),
               on_line("po_line_date", "250101", "1", "2026-11-16", "2026-W47"), on_line("po_line_date", "250101", "2", "2026-11-16", "2026-11-16/2026-11-20"),
               on_line("po_line_date", "250101", "3", "2026-W07", "2026-02-13"),
               article("KM-1001", "12.70", po="250101"), article("KM-1002", "12.7", old="12.50", po="250101"))
    texts = {language: [row_text(change, asking.root, language) for change in asking.open()] for language in ("nl", "en")}
    assert texts["nl"] == ["regel 1 (KM-1001): prijs 1234,50 → 1240,00", "regel 1 (KM-1001): aantal 2,5 → 3",
                           "regel 1 (KM-1001): leverdatum 16-11-2026 → week 47 (2026)",
                           "regel 2 (KM-1002): leverdatum 16-11-2026 → 16-11-2026/20-11-2026",
                           "regel 3 (KM-1003): leverdatum week 7 (2026) → 13-02-2026",
                           "artikel KM-1001: inkoopprijs onbekend → 12,70", "artikel KM-1002: inkoopprijs 12,50 → 12,7"]
    assert texts["en"] == ["line 1 (KM-1001): price 1234.50 → 1240.00", "line 1 (KM-1001): quantity 2.5 → 3",
                           "line 1 (KM-1001): delivery date 16-11-2026 → week 47 (2026)",
                           "line 2 (KM-1002): delivery date 16-11-2026 → 16-11-2026/20-11-2026",
                           "line 3 (KM-1003): delivery date week 7 (2026) → 13-02-2026",
                           "article KM-1001: purchase price unknown → 12.70", "article KM-1002: purchase price 12.50 → 12.7"]
    assert (STRINGS["nl"]["hand_unknown"], STRINGS["en"]["hand_unknown"]) == ("onbekend", "unknown")
    assert ids_of("e1+e2") == ["e1", "e2"] and ids_of("e1") == ["e1"] and ids_of("") == []


def test_the_hint_on_an_order_above_the_authorization_shows_no_amount(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    asking = Asking(tmp_path, d.store)
    asking.ask(asking.case("250108"), on_line("po_line_price", "250108", "1", "12.50", "12.70"), article("KM-0801", "12.70"))   # total 58.50
    asking.ask(asking.case("250114"), on_line("po_line_date", "250114", "1", "2026-11-16", "2026-W48"))                       # total 272.40
    limited = PoStore(d.store.root, Decimal("100.13"))             # a made-up limit between the two totals
    for language, hint in (("nl", "Let op: na deze wijziging moet de inkooporder opnieuw gefiatteerd worden."),
                           ("en", "Note: after this change the purchase order must be approved again.")):
        shown = blocks(asking.open(), limited, asking.root, language)
        headings = {block_id: heading for block_id, heading, _ in shown}
        assert STRINGS[language]["hand_hint"] == hint
        assert headings["po:250114"] == f"{STRINGS[language]['hand_block_po'].format(number='250114', supplier='S14')}\n{hint}"
        assert hint not in headings["po:250108"] and hint not in headings["articles"]
        flat = repr(shown)
        assert not [amount for amount in ("100.13", "100,13", "100.1", "272.4", "272,4", "58.5", "58,5") if amount in flat], language
    for store in (d.store, PoStore(d.store.root, None)):          # the tests' made-up limit (5000), and no limit: no hint
        assert not [heading for _, heading, _ in blocks(asking.open(), store, asking.root, "nl") if "\n" in heading]


def test_a_block_takes_its_supplier_and_its_hint_from_the_po_as_it_stands(tmp_path, madeup_out):
    """With the looks of Task 40 the heading of a PO's block reads the PO as it stands (PoStore.as_it_stands), not only its PDF
    versions. PO 250202, known from an export alone as a cart's PO is, names the supplier of its look and shows the hint, its look's
    total (214.50) being above the made-up limit. PO 250108 has its PDF (58.50) and, filed after it, a look with 300 pieces on line 1
    (3771.00): the look stands, so the hint shows, and the look reads with the PDF's supplier."""
    d = deps(tmp_path, madeup_out)
    rows = list(csv.DictReader(io.StringIO((madeup_out / "CART02_export.csv").read_text(encoding="utf-8"))))
    with open(tmp_path / "250202.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[*EXPORT_COLUMNS, "supplier"])
        writer.writeheader()
        writer.writerows(row | {"supplier": "S22"} for row in rows)
    [cart_po] = d.store.from_export(tmp_path / "250202.csv", EXPORT_COLUMNS | {"supplier": "supplier"})
    spec = next(spec for spec in load_specs(REPO_ROOT / "aid" / "madeup" / "spec") if spec.case_id == "MU08")
    lines = list(csv.reader(io.StringIO(export_csv(spec))))
    lines[1][lines[0].index("quantity")] = "300"                                     # 300 pieces on line 1
    with open(tmp_path / "250108.csv", "w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(lines)
    [later] = d.store.from_export(tmp_path / "250108.csv", EXPORT_COLUMNS)
    pdf = d.store.root / "250108" / "v1" / "po.json"
    os.utime(d.store.root / "250108" / "e1" / "po.json", (pdf.stat().st_mtime + 60, pdf.stat().st_mtime + 60))   # filed after the PDF
    asking = Asking(tmp_path, d.store)
    asking.ask(asking.case("250202", [cart_po]), on_line("po_line_price", "250202", "2", "40.00", "41.00"))
    asking.ask(asking.case("250108"), on_line("po_line_price", "250108", "1", "12.50", "12.70"))
    limited = PoStore(d.store.root, Decimal("100.13"))
    assert (cart_po.total, later.total, d.store.versions("250108")[0].total) == (Decimal("214.50"), Decimal("3771.00"), Decimal("58.50"))
    hint = STRINGS["nl"]["hand_hint"]
    headings = {block_id: heading for block_id, heading, _ in blocks(asking.open(), limited, asking.root, "nl")}
    assert headings["po:250202"] == f"{STRINGS['nl']['hand_block_po'].format(number='250202', supplier='S22')}\n{hint}"
    assert headings["po:250108"] == f"{STRINGS['nl']['hand_block_po'].format(number='250108', supplier='S08')}\n{hint}"


def test_an_undo_to_enter_puts_the_value_from_before_back(tmp_path, madeup_out):
    """The undo of an entered change is a row as the others are, the old value back; an article price whose price before it the aid
    never knew says to put that price back (review round, Z2)."""
    d = deps(tmp_path, madeup_out)
    asking = Asking(tmp_path, d.store)
    one = asking.case("250108")
    asked = asking.ask(one, on_line("po_line_price", "250108", "1", "12.50", "12.70"), article("KM-0801", "12.70"))
    mark(asking.prepared, asked, "entered", NOW)
    back = [asking.writer.apply(op, one, UNDO_RULE_ID, {"undo_of": "made-up"}, NOW).entry_id
            for op in (WriteOp(kind="article_price", po_number="250108", version=1, article_code="KM-0801", old_value="12.70", new_value=None),
                       on_line("po_line_price", "250108", "1", "12.70", "12.50"))]
    for language, rows in (("nl", ["regel 1 (KM-0801): prijs 12,70 → 12,50", "artikel KM-0801: inkoopprijs 12,70 terugzetten naar de prijs van daarvoor"]),
                           ("en", ["line 1 (KM-0801): price 12.70 → 12.50", "article KM-0801: purchase price 12.70 back to the price before it"])):
        shown = blocks(asking.open(), d.store, asking.root, language)
        assert [[text for text, _ in block_rows] for _, _, block_rows in shown] == [rows[:1], rows[1:]], language
        assert [[ids_of(value) for _, value in block_rows] for _, _, block_rows in shown] == [[back[1:]], [back[:1]]]


# --- Task 35: the note of what Exact shows now, the done rows, the put-back and the confirmations ---

LATER = datetime(2026, 11, 20, 9, 0)                 # a look at the page some days after the cases ran


def ran(tmp_path: Path, madeup_out: Path, *names: str) -> tuple:
    """The made-up cases given run as the runner runs them, their changes prepared into <cases_root>/prepared, where the page and the
    runner read the list: (the Deps, that folder, {name: case id})."""
    prepared = tmp_path / "cases" / "prepared"
    d = deps(tmp_path, madeup_out, writer=PreparedWriter(prepared))
    opened = {}
    for minute, name in enumerate(names):
        case = open_upload_case(d.settings.cases_root, [madeup_out / f"{name}_conf.pdf"], po_number_of(name), "confirmation",
                                datetime(2026, 11, 12, 9, minute))
        record_dispositions(case, run_case(case, d))
        opened[name] = case.case_id
    return d, prepared, opened


def one(prepared: Path, kind: str):
    """The change of the kind among the changes of the folder, whatever its state; the last one when there are several."""
    return [change for change in changes(prepared)[0] if change.kind == kind][-1]


def listed_rows(d, language: str = "nl") -> list[str]:
    """The rows of the list as the tab's tick boxes show them."""
    prepared = d.settings.cases_root / "prepared"
    return [label for label, _ in tick_rows(blocks(open_changes(prepared), d.store, d.settings.cases_root, language))]


def test_an_open_row_notes_what_exact_shows_now(tmp_path, madeup_out):
    """A newer version of the order that shows neither the old nor the new value of an open row gives the row the note of what Exact
    shows now; one that shows the old value gives none (rule 4), and neither does one filed before the change was asked (review
    round, K1)."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")                                               # asked on 12 November, 9:00
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.50"}))                    # the old price printed again
    filed_on(d.store, "250108", "v2", datetime(2026, 11, 13, 9, 0))
    assert listed_rows(d)[0] == "Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,50 → 12,70"
    d.store.add(newer_po(tmp_path / "MU08_po_v3.pdf", "MU08", 3, {"1": "12.80"}))
    filed_on(d.store, "250108", "v3", datetime(2026, 11, 11, 9, 0))                                # filed before the change was asked
    assert listed_rows(d)[0] == "Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,50 → 12,70"
    filed_on(d.store, "250108", "v3", datetime(2026, 11, 13, 10, 0))
    assert listed_rows(d)[0] == "Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,50 → 12,70. Exact toont nu 12,80."
    assert listed_rows(d, "en")[0] == "Purchase order 250108 (S08), line 1 (KM-0801): price 12.50 → 12.70. Exact now shows 12.80."
    assert listed_rows(d)[1] == "artikel KM-0801: inkoopprijs onbekend → 12,70"                    # an article price has no such note


def test_the_done_rows_say_how_the_aid_knows(tmp_path, madeup_out):
    """The changes entered, seen or dropped in the last 14 days, the newest first, each with how the aid knows it; an open, replaced or
    older change is not listed, and the value of a row is its entry ids as on the list."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08", "MU14")
    price, article, date = (one(prepared, kind) for kind in ("po_line_price", "article_price", "po_line_date"))
    mark(prepared, price.ids, "entered", datetime(2026, 11, 18, 10, 0))
    mark(prepared, article.ids, "dropped", datetime(2026, 11, 18, 11, 0))
    mark(prepared, date.ids, "seen", datetime(2026, 11, 19, 8, 0), by="version 2")
    old = PreparedWriter(prepared).apply(on_line("po_line_quantity", "250108", "2", "50", "60"), price.case_ids[0], "B01", {}, NOW).entry_id
    mark(prepared, [old], "entered", datetime(2026, 11, 5, 9, 0))                                    # more than 14 days before LATER
    PreparedWriter(prepared).apply(on_line("po_line_quantity", "250114", "1", "5", "6"), date.case_ids[0], "B01", {}, NOW)      # open
    # seen in an export about 10 days before LATER: still in the 14 days (review round, K7 and K9)
    exported = PreparedWriter(prepared).apply(on_line("po_line_date", "250108", "2", "2026-11-16", "2026-11-23"), price.case_ids[0], "B01", {},
                                              datetime(2026, 11, 9, 9, 0)).entry_id
    mark(prepared, [exported], "seen", datetime(2026, 11, 10, 10, 0), by="export")
    rows = done_rows(changes(prepared)[0], LATER, "nl", store=d.store, cases_root=d.settings.cases_root)
    assert rows == [("Inkooporder 250114 (S14), regel 1 (KM-1401): leverdatum 16-11-2026 → week 48 (2026) (gezien in versie 2 van de inkooporder)",
                     "+".join(date.ids)),
                    ("artikel KM-0801: inkoopprijs onbekend → 12,70 (niet ingevoerd, op 18-11-2026)", "+".join(article.ids)),
                    ("Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,50 → 12,70 (door u afgevinkt op 18-11-2026)", "+".join(price.ids)),
                    ("Inkooporder 250108 (S08), regel 2 (KM-0802): leverdatum 16-11-2026 → 23-11-2026 (gezien in een export uit Exact)", exported)]
    english = done_rows(changes(prepared)[0], LATER, "en", store=d.store, cases_root=d.settings.cases_root)
    assert [text for text, _ in english] == [
        "Purchase order 250114 (S14), line 1 (KM-1401): delivery date 16-11-2026 → week 48 (2026) (seen in version 2 of the purchase order)",
        "article KM-0801: purchase price unknown → 12.70 (not entered, on 18-11-2026)",
        "Purchase order 250108 (S08), line 1 (KM-0801): price 12.50 → 12.70 (ticked by you on 18-11-2026)",
        "Purchase order 250108 (S08), line 2 (KM-0802): delivery date 16-11-2026 → 23-11-2026 (seen in an export from Exact)"]
    assert [text for text, _ in done_rows(changes(prepared)[0], datetime(2026, 11, 20, 9, 0), "nl", days=1)] == []   # one day back: none
    # without the store and the cases the order is named without its supplier and the line without its article
    assert done_rows(changes(prepared)[0], LATER, "nl")[2][0] == "Inkooporder 250108, regel 1 (onbekend): prijs 12,50 → 12,70 (door u afgevinkt op 18-11-2026)"


def test_a_put_back_of_a_case_change_is_logged_recorded_and_listed(tmp_path, madeup_out):
    """'Terugdraaien in Exact' on an entered price prepares the old value through the undo of the review page's close: the case's write
    log holds the undo, step 09 and the audit record its record; the new row is linked by the mark puts_back and stands on the list
    with 'Terugzetten:' before it, and the price stays entered (rule 8)."""
    d, prepared, cases = ran(tmp_path, madeup_out, "MU08")
    price = one(prepared, "po_line_price")
    mark(prepared, price.ids, "entered", NOW)
    case = load_case(d.settings.cases_root, cases["MU08"])
    before = len(WriteLog(case.root).entries())
    note = take_back(d, one(prepared, "po_line_price"), LATER, "nl")
    assert note == ("Nog niet teruggedraaid in Exact. Op de lijst staat nu: Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs "
                    "12,70 → 12,50. Voer de oude waarde in Exact in en vink de regel af.")
    entries = WriteLog(case.root).entries()
    [undo] = entries[before:]
    assert (undo.undo_of, undo.status, undo.rule_id, undo.op.kind, undo.op.old_value, undo.op.new_value) == (
        price.ids[0], "prepared", UNDO_RULE_ID, "po_line_price", "12.70", "12.50")
    record = case.read_step("close")["records"][-1]
    assert (record["action"], record["entry_id"], record["undo"]["entry_id"]) == ("undo", price.ids[0], undo.entry_id)
    assert json.loads((case.root / "audit.json").read_text(encoding="utf-8"))["close"][-1] == record
    assert put_back_of(prepared, price.ids[0]) == undo.entry_id
    assert (one(prepared, "po_line_price").ids, one(prepared, "po_line_price").state) == ([undo.entry_id], "open")
    assert [change.state for change in changes(prepared)[0] if change.ids == price.ids] == ["entered"]
    assert listed_rows(d)[0] == "Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,50"
    assert listed_rows(d, "en")[0] == "Purchase order 250108 (S08), Put back: line 1 (KM-0801): price 12.70 → 12.50"
    assert "Ongedaan" not in note and "ongedaan" not in note


def test_a_put_back_of_a_price_list_change(tmp_path, madeup_out):
    """A price list's article price, entered, is put back through undo_entry on the list's own write-log folder
    (<cases_root>/pricelists/<day>, as the import's command keeps it), with its old price as the value to enter."""
    from aid.pricelist import main as import_list

    prepared = tmp_path / "cases" / "prepared"
    d = deps(tmp_path, madeup_out, writer=PreparedWriter(prepared))
    listing, current = tmp_path / "list.csv", tmp_path / "current.csv"
    listing.write_text("item_code,price\nKM-0801,12.70\n", encoding="utf-8")
    current.write_text("item_code,price\nKM-0801,12.50\n", encoding="utf-8")
    folder = d.settings.cases_root / "pricelists" / "2026-11-18"
    assert import_list(["--file", str(listing), "--supplier", "S08", "--current", str(current), "--prepared", str(prepared),
                        "--write-log", str(folder)], now=datetime(2026, 11, 18, 12, 0)) == 0
    [change] = open_changes(prepared)
    assert (change.case_ids, change.old_value, change.new_value) == (["pricelist_S08_20261118T120000"], "12.50", "12.70")
    mark(prepared, change.ids, "entered", NOW)
    note = take_back(d, one(prepared, "article_price"), LATER, "en")
    assert note == ("Not put back in Exact yet. The list now holds: Put back: article KM-0801: purchase price 12.70 → 12.50. Enter the "
                    "old value in Exact and tick the row.")
    [entry, undo] = WriteLog(folder).entries()
    assert (undo.undo_of, undo.status, undo.op.old_value, undo.op.new_value, undo.case_id) == (
        entry.entry_id, "prepared", "12.70", "12.50", "pricelist_S08_20261118T120000")
    assert put_back_of(prepared, entry.entry_id) == undo.entry_id and listed_rows(d) == ["Terugzetten: artikel KM-0801: inkoopprijs 12,70 → 12,50"]


def test_a_second_put_back_says_it_is_on_the_list(tmp_path, madeup_out):
    d, prepared, cases = ran(tmp_path, madeup_out, "MU08")
    price = one(prepared, "po_line_price")
    mark(prepared, price.ids, "entered", NOW)
    take_back(d, one(prepared, "po_line_price"), LATER, "nl")
    kept = {path: path.read_bytes() for path in d.settings.cases_root.rglob("*") if path.is_file()}
    entered = next(change for change in changes(prepared)[0] if change.ids == price.ids)
    for language, said in (("nl", "Het terugzetten staat al op de lijst."), ("en", "The put-back is on the list already.")):
        assert take_back(d, entered, LATER, language) == said
    assert {path: path.read_bytes() for path in d.settings.cases_root.rglob("*") if path.is_file()} == kept      # nothing written


def test_a_dropped_put_back_is_reopened(tmp_path, madeup_out):
    """A put-back the buyer dropped is put on the list again by a second press; one he entered, or that the aid saw, means that the
    change was put back already. Until then the first change stays entered."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    price = one(prepared, "po_line_price")
    mark(prepared, price.ids, "entered", NOW)
    take_back(d, one(prepared, "po_line_price"), LATER, "nl")
    put = one(prepared, "po_line_price")
    mark(prepared, put.ids, "dropped", NOW)
    entered = next(change for change in changes(prepared)[0] if change.ids == price.ids)
    assert entered.state == "entered" and open_changes(prepared) == [one(prepared, "article_price")]
    assert take_back(d, entered, LATER, "nl") == ("Nog niet teruggedraaid in Exact. Op de lijst staat nu: Inkooporder 250108 (S08), "
                                                   "Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,50. Voer de oude waarde in Exact in "
                                                   "en vink de regel af.")
    assert (one(prepared, "po_line_price").ids, one(prepared, "po_line_price").state) == (put.ids, "open")
    log = WriteLog(load_case(d.settings.cases_root, price.case_ids[0]).root)
    assert [entry.undo_of for entry in log.entries()].count(price.ids[0]) == 1                     # reopened: no second undo
    for what, by in (("entered", "buyer"), ("seen", "version 2")):
        if what == "seen":
            mark(prepared, put.ids, "reopened", NOW)
        mark(prepared, put.ids, what, NOW, by=by)
        assert take_back(d, entered, LATER, "nl") == "Deze wijziging is al teruggedraaid."
        assert take_back(d, entered, LATER, "en") == "This change was put back already."
        assert next(change for change in changes(prepared)[0] if change.ids == price.ids).state == "entered"


def test_an_article_price_without_an_old_value_is_not_put_back(tmp_path, madeup_out):
    """MU08's article price comes from a case, which never read the article's price before it: the aid cannot put it back, says so, and
    writes nothing."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "article_price").ids, "entered", NOW)
    kept = {path: path.read_bytes() for path in d.settings.cases_root.rglob("*") if path.is_file()}
    assert take_back(d, one(prepared, "article_price"), LATER, "nl") == ("Van dit artikel kent de assistent de oude prijs niet; zet die "
                                                                          "zelf terug in Exact.")
    assert take_back(d, one(prepared, "article_price"), LATER, "en") == ("The assistant does not know the old price of this article; put "
                                                                          "it back in Exact yourself.")
    assert {path: path.read_bytes() for path in d.settings.cases_root.rglob("*") if path.is_file()} == kept


def test_a_change_not_entered_or_a_put_back_itself_is_not_put_back(tmp_path, madeup_out):
    """A dropped change was never entered, so there is nothing to put back; a put-back that was entered is an undo, which the undo
    never reverses. Either way nothing is written and the note says why."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08", "MU14")
    mark(prepared, one(prepared, "po_line_date").ids, "dropped", NOW)
    mark(prepared, one(prepared, "po_line_price").ids, "entered", NOW)
    take_back(d, one(prepared, "po_line_price"), LATER, "nl")
    put = one(prepared, "po_line_price")
    assert (put.puts_back, put.state) == (one(prepared, "po_line_price").puts_back, "open") and put.puts_back is not None
    mark(prepared, put.ids, "entered", NOW)
    kept = {path: path.read_bytes() for path in d.settings.cases_root.rglob("*") if path.is_file()}
    assert take_back(d, one(prepared, "po_line_date"), LATER, "nl") == "Deze wijziging is niet ingevoerd; in Exact is er niets terug te draaien."
    assert take_back(d, one(prepared, "po_line_date"), LATER, "en") == "This change was not entered; there is nothing to put back in Exact."
    assert take_back(d, one(prepared, "po_line_price"), LATER, "nl") == ("De assistent kan deze wijziging niet terugdraaien; zet de oude "
                                                                          "waarde zelf terug in Exact.")
    assert {path: path.read_bytes() for path in d.settings.cases_root.rglob("*") if path.is_file()} == kept


def test_the_confirmations_of_seven_days(tmp_path):
    """The rows that attach a confirmation, without a tick: the day, the order, the file, and whether the status is to be set as well
    (the case's last row that sets it sets Bevestigd). The 7 days count from the newest row of the case that attaches it or sets the
    status, whose day the line shows, and the newest such row comes first (review round, K5): a status that a close prepares days after
    the run brings its confirmation back, also when the attachment is more than 7 days old. An attachment undone is left out."""
    prepared = tmp_path / "cases" / "prepared"
    writer = PreparedWriter(prepared)

    def attached(case_id: str, at: datetime, status: bool = False, name: str = "MU08_conf.pdf") -> str:
        po = case_id.partition("_")[0]
        document = str(tmp_path / "cases" / case_id / "input" / name)
        entry = writer.apply(WriteOp(kind="attach_document", po_number=po, version=1, document=document), case_id, "R01", {}, at)
        if status:
            writer.apply(WriteOp(kind="set_status", po_number=po, version=1, old_value="Besteld", new_value="Bevestigd"), case_id, "R01", {}, at)
        return entry.entry_id

    attached("250101_20261105T090000", datetime(2026, 11, 5, 9, 0), status=True, name="MU01_conf.pdf")            # too old
    attached("250108_20261118T090000", datetime(2026, 11, 18, 9, 0), status=True)
    attached("250111_20261119T090000", datetime(2026, 11, 19, 9, 0), name="MU11_conf.pdf")                         # attach only
    undone = attached("250114_20261119T100000", datetime(2026, 11, 19, 10, 0), status=True, name="MU14_conf.pdf")
    writer.apply(WriteOp(kind="detach_document", po_number="250114", version=1,
                         document=str(tmp_path / "cases" / "250114_20261119T100000" / "input" / "MU14_conf.pdf")),
                 "250114_20261119T100000", UNDO_RULE_ID, {"undo_of": undone}, datetime(2026, 11, 19, 11, 0))
    attached("250117_20261119T120000", datetime(2026, 11, 19, 12, 0), status=True, name="MU17_conf.pdf")
    writer.apply(WriteOp(kind="set_status", po_number="250117", version=1, old_value="Bevestigd", new_value="Besteld"),
                 "250117_20261119T120000", UNDO_RULE_ID, {}, datetime(2026, 11, 19, 13, 0))                       # the status undone
    writer.apply(on_line("po_line_price", "250108", "1", "12.50", "12.70"), "250108_20261118T090000", "R04", {}, datetime(2026, 11, 18, 9, 0))
    # the reviewers' late close: the run attached the confirmation only, on 5 November; the close prepares the status on 19 November
    attached("250111_20261105T090000", datetime(2026, 11, 5, 9, 0), name="MU11_late.pdf")
    assert [item["case_id"] for item in confirmations(prepared, LATER)] == ["250117_20261119T120000", "250111_20261119T090000",
                                                                            "250108_20261118T090000"]
    writer.apply(WriteOp(kind="set_status", po_number="250111", version=1, old_value="Besteld", new_value="Bevestigd"), "250111_20261105T090000",
                 "B01", {"decision": "close"}, datetime(2026, 11, 19, 14, 0))
    found = confirmations(prepared, LATER)
    assert found == [{"date": "2026-11-19", "po_number": "250111", "file": "MU11_late.pdf", "set_status": True, "case_id": "250111_20261105T090000"},
                     {"date": "2026-11-19", "po_number": "250117", "file": "MU17_conf.pdf", "set_status": False, "case_id": "250117_20261119T120000"},
                     {"date": "2026-11-19", "po_number": "250111", "file": "MU11_conf.pdf", "set_status": False, "case_id": "250111_20261119T090000"},
                     {"date": "2026-11-18", "po_number": "250108", "file": "MU08_conf.pdf", "set_status": True, "case_id": "250108_20261118T090000"}]
    assert confirmations(prepared, LATER, days=1) == found[:3] and confirmations(tmp_path / "nothing", LATER) == []
    # the supplier as the case's result names it; without a result the order is named alone
    made = open_case(tmp_path / "cases", "confirmation", [], "250108", datetime(2026, 11, 18, 9, 0))
    made.write_step(8, "result", case_result(made.case_id, "closed", supplier="S08"))
    assert confirmation_lines(found, tmp_path / "cases", "nl") == [
        "19-11-2026: inkooporder 250111, bestand MU11_late.pdf",
        "19-11-2026: inkooporder 250117, bestand MU17_conf.pdf (alleen bijvoegen: er staat nog iets open)",
        "19-11-2026: inkooporder 250111, bestand MU11_conf.pdf (alleen bijvoegen: er staat nog iets open)",
        "18-11-2026: inkooporder 250108 (S08), bestand MU08_conf.pdf"]
    assert confirmation_lines(found[3:], tmp_path / "cases", "en") == ["18-11-2026: purchase order 250108 (S08), file MU08_conf.pdf"]
    assert confirmation_lines(found[1:2], tmp_path / "cases", "en") == [
        "19-11-2026: purchase order 250117, file MU17_conf.pdf (attach only: something is still open)"]


# --- the review round: K1 (a document counts only when it came after the change) and K2 (a put-back never replaces a newer change) ---

def test_a_print_filed_before_the_put_back_never_ticks_it_off(tmp_path, madeup_out):
    """The reviewers' sequence (K1): Exact printed the order again as version 2 after the case and before the buyer entered the price,
    still with the old price. The buyer ticks the price and puts it back. Version 2 was filed before the put-back, so the pass leaves
    the put-back on the list; a version 3 filed after it that shows the price the buyer entered changes nothing and adds no note; a
    version 4 that shows the old price ticks the put-back off, seen in version 4."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")                                               # asked on 12 November, 9:00
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {}))                                # line 1 still at 12.50
    filed_on(d.store, "250108", "v2", datetime(2026, 11, 12, 10, 0))
    assert reconcile(prepared, d.store, d.settings.cases_root, datetime(2026, 11, 12, 10, 5)) == 0
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 10, 30))
    later = d.model_copy(update={"clock": lambda: datetime(2026, 11, 12, 11, 0)})
    take_back(later, one(prepared, "po_line_price"), datetime(2026, 11, 12, 11, 0), "nl")
    put = one(prepared, "po_line_price")
    assert (put.state, put.asked_at, put.puts_back is not None) == ("open", "2026-11-12T11:00:00", True)
    assert reconcile(prepared, d.store, d.settings.cases_root, datetime(2026, 11, 12, 11, 15)) == 0
    row = "Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,50"
    assert one(prepared, "po_line_price").state == "open" and listed_rows(d)[0] == row
    d.store.add(newer_po(tmp_path / "MU08_po_v3.pdf", "MU08", 3, {"1": "12.70"}))                    # Exact holds what the buyer entered
    filed_on(d.store, "250108", "v3", datetime(2026, 11, 12, 12, 0))
    assert reconcile(prepared, d.store, d.settings.cases_root, datetime(2026, 11, 12, 12, 5)) == 0 and listed_rows(d)[0] == row
    d.store.add(newer_po(tmp_path / "MU08_po_v4.pdf", "MU08", 4, {"1": "12.50"}))                    # and then the old price again
    filed_on(d.store, "250108", "v4", datetime(2026, 11, 12, 13, 0))
    assert reconcile(prepared, d.store, d.settings.cases_root, datetime(2026, 11, 12, 13, 5)) == 1
    seen = one(prepared, "po_line_price")
    assert (seen.ids, seen.state, seen.marked_by) == (put.ids, "seen", "version 4") and row not in listed_rows(d)


def newer_confirmation(path: Path, case_id: str, version: int, prices: dict[str, str]) -> Path:
    """A newer made-up confirmation of the case's supplier that quotes version `version` of its order, with the unit price of each line
    that `prices` names (line id -> price), drawn by the generator into `path`."""
    spec = MadeUpSpec.model_validate_json((SPEC_DIR / f"{case_id}.json").read_text(encoding="utf-8"))
    lines = [line.model_copy(update={"unit_price": prices.get(line.line_id, line.unit_price)}) for line in spec.confirmation.lines]
    spec = spec.model_copy(update={"confirmation": spec.confirmation.model_copy(update={"version_quoted": version, "lines": lines})})
    path.write_bytes(confirmation_pdf(spec))
    return path


def test_a_put_back_never_replaces_a_newer_change(tmp_path, madeup_out):
    """The reviewers' sequence (K2): the buyer ticks MU08's price, Exact sends version 2 with it, and a newer confirmation of S08 that
    quotes version 2 asks 12,90 for the line. "Terugdraaien in Exact" on the first price is refused, with nothing written and the newer
    change named, while that change is open or entered, and the note says how to lift the refusal (second review round, K13); once
    the buyer dropped it, the put-back is made."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 10, 0))
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.70"}))
    filed_on(d.store, "250108", "v2", datetime(2026, 11, 12, 11, 0))
    second = open_upload_case(d.settings.cases_root, [newer_confirmation(tmp_path / "MU08_conf_2.pdf", "MU08", 2, {"1": "12.90"})],
                              "250108", "confirmation", datetime(2026, 11, 12, 12, 0))
    record_dispositions(second, run_case(second, d))
    newer = one(prepared, "po_line_price")
    assert (newer.old_value, newer.new_value, newer.state, newer.case_ids) == ("12.70", "12.90", "open", [second.case_id])
    first = next(change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.new_value == "12.70")
    kept = {path: path.read_bytes() for path in d.settings.cases_root.rglob("*") if path.is_file()}
    said = {"nl": ("Terugdraaien kan nu niet: er staat een nieuwere wijziging op de lijst, Inkooporder 250108 (S08), regel 1 (KM-0801): "
                   "prijs 12,70 → 12,90. Laat die eerst vallen; daarna kunt u deze wijziging terugdraaien."),
            "en": ("Cannot put back now: a newer change is on the list, Purchase order 250108 (S08), line 1 (KM-0801): price 12.70 → 12.90. "
                   "Drop that one first; then you can put this change back.")}
    for language in ("nl", "en"):
        assert take_back(d, first, LATER, language) == said[language]
    assert {path: path.read_bytes() for path in d.settings.cases_root.rglob("*") if path.is_file()} == kept      # nothing written
    mark(prepared, newer.ids, "entered", datetime(2026, 11, 12, 13, 0))                             # entered: still refused
    entered = {path: path.read_bytes() for path in d.settings.cases_root.rglob("*") if path.is_file()}
    said = {"nl": ("Terugdraaien kan nu niet: er is een nieuwere wijziging ingevoerd, Inkooporder 250108 (S08), regel 1 (KM-0801): "
                   "prijs 12,70 → 12,90. Draai die eerst terug en vink het terugzetten af; daarna kunt u deze wijziging terugdraaien."),
            "en": ("Cannot put back now: a newer change was entered, Purchase order 250108 (S08), line 1 (KM-0801): price 12.70 → 12.90. "
                   "Put that one back first and tick its put-back off; then you can put this change back.")}
    for language in ("nl", "en"):
        assert take_back(d, first, LATER, language) == said[language]
    assert {path: path.read_bytes() for path in d.settings.cases_root.rglob("*") if path.is_file()} == entered
    mark(prepared, newer.ids, "reopened", datetime(2026, 11, 12, 14, 0))
    mark(prepared, newer.ids, "dropped", datetime(2026, 11, 12, 14, 0))                              # the buyer does not want 12,90
    assert take_back(d, first, LATER, "nl").startswith("Nog niet teruggedraaid in Exact. Op de lijst staat nu: Inkooporder 250108 (S08), "
                                                       "Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,50.")
    assert [(change.new_value, change.state) for change in changes(prepared)[0] if change.kind == "po_line_price"] == [
        ("12.70", "entered"), ("12.90", "dropped"), ("12.50", "open")]


def test_a_put_back_that_a_newer_change_replaced_does_not_return(tmp_path, madeup_out):
    """The put-back came first: the buyer ticks MU08's price and puts it back, and then a newer confirmation asks 12,90 for the line,
    which replaces the put-back on the list, as a newer value replaces any older change (the cost of runs, accepted in the review
    round). While that newer change is open or entered, "Terugdraaien in Exact" on the first price says that the change does not
    return to the list and writes nothing, so the note of K2, which asks to handle the newer change first, is not given here. Once the
    buyer dropped the newer change, nothing stands in the put-back's way any more, and the press makes it again (third review round,
    K22)."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 10, 0))
    first = one(prepared, "po_line_price")
    assert take_back(d, first, datetime(2026, 11, 12, 10, 30), "nl").startswith("Nog niet teruggedraaid in Exact.")
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.70"}))
    filed_on(d.store, "250108", "v2", datetime(2026, 11, 12, 11, 0))
    second = open_upload_case(d.settings.cases_root, [newer_confirmation(tmp_path / "MU08_conf_2.pdf", "MU08", 2, {"1": "12.90"})],
                              "250108", "confirmation", datetime(2026, 11, 12, 12, 0))
    record_dispositions(second, run_case(second, d))
    newer = one(prepared, "po_line_price")
    assert [(change.new_value, change.state) for change in changes(prepared)[0] if change.kind == "po_line_price"] == [
        ("12.70", "entered"), ("12.50", "replaced"), ("12.90", "open")]
    for marks in ([], ["entered"]):                                                 # the newer change open, then entered
        for what in marks:
            mark(prepared, newer.ids, what, datetime(2026, 11, 12, 13, 0))
        kept = {path: path.read_bytes() for path in d.settings.cases_root.rglob("*") if path.is_file()}
        for language in ("nl", "en"):
            assert take_back(d, first, LATER, language) == STRINGS[language]["hand_reopen_not"], (marks, language)
        assert {path: path.read_bytes() for path in d.settings.cases_root.rglob("*") if path.is_file()} == kept, marks
    mark(prepared, newer.ids, "reopened", datetime(2026, 11, 12, 13, 0))
    mark(prepared, newer.ids, "dropped", datetime(2026, 11, 12, 13, 0))            # dropped: the put-back is made again
    assert take_back(d.model_copy(update={"clock": lambda: LATER}), first, LATER, "nl").startswith("Nog niet teruggedraaid in Exact.")
    assert [(change.new_value, change.rule_id, change.state) for change in changes(prepared)[0] if change.kind == "po_line_price"] == [
        ("12.70", "R04", "entered"), ("12.50", UNDO_RULE_ID, "replaced"), ("12.90", "R04", "dropped"), ("12.50", UNDO_RULE_ID, "open")]


# --- the second review round: a put-back stands on the list (K10), K4 for a change that two cases asked (K11), K2's way out (K13) ---

def files_of(d) -> dict:
    """Every file under the cases root of the Deps, by its path: what a refusal must leave as it was."""
    return {path: path.read_bytes() for path in d.settings.cases_root.rglob("*") if path.is_file()}


def test_a_put_back_stands_when_the_change_was_asked_again(tmp_path, madeup_out):
    """The checkers' sequence (second review round, K10), every row from a case run: MU08's price ticked; Exact sends version 2 with it;
    a newer confirmation that quotes version 2 asks 12,90, which the buyer drops; then MU08's first confirmation comes in again, quoting
    version 1, and asks 12,50 → 12,70 once more, which the marks say Exact holds ("already"). "Terugdraaien in Exact" on the ticked
    price puts it back: the put-back stands open on the list, the later run is replaced by it, and the page says so truly."""
    d, prepared, opened = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.70"}))
    filed_on(d.store, "250108", "v2", datetime(2026, 11, 12, 9, 45))
    for name, version, prices, at in (("MU08_conf_2.pdf", 2, {"1": "12.90"}, datetime(2026, 11, 12, 10, 0)),
                                      ("MU08_conf_again.pdf", 1, {}, datetime(2026, 11, 12, 11, 0))):
        again = open_upload_case(d.settings.cases_root, [newer_confirmation(tmp_path / name, "MU08", version, prices)], "250108",
                                 "confirmation", at)
        record_dispositions(again, run_case(again, d))
        if version == 2:
            mark(prepared, one(prepared, "po_line_price").ids, "dropped", datetime(2026, 11, 12, 10, 30))
    prices = [change for change in changes(prepared)[0] if change.kind == "po_line_price"]
    assert [(change.new_value, change.state) for change in prices] == [("12.70", "entered"), ("12.90", "dropped"), ("12.70", "already")]
    said = take_back(d, prices[0], datetime(2026, 11, 12, 12, 0), "nl")
    row = "Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,50"
    assert said == f"Nog niet teruggedraaid in Exact. Op de lijst staat nu: {row}. Voer de oude waarde in Exact in en vink de regel af."
    assert [(change.new_value, change.rule_id, change.state) for change in changes(prepared)[0] if change.kind == "po_line_price"] == [
        ("12.70", "R04", "entered"), ("12.90", "R04", "dropped"), ("12.70", "R04", "replaced"), ("12.50", UNDO_RULE_ID, "open")]
    assert listed_rows(d)[0] == row
    assert take_back(d, prices[0], datetime(2026, 11, 12, 12, 5), "nl") == "Het terugzetten staat al op de lijst."
    [entry] = [entry for entry in WriteLog(load_case(d.settings.cases_root, opened["MU08"]).root).entries() if entry.undo_of]
    assert (entry.op.old_value, entry.op.new_value, entry.status) == ("12.70", "12.50", "prepared")


def test_a_put_back_that_exact_holds_already_is_not_made_and_the_researcher_note_is_left_for_the_unforeseen(tmp_path, madeup_out,
                                                                                                            monkeypatch):
    """"Terugdraaien in Exact" first works out, without writing, the state the put-back would have (K10, and the third review round's
    K20): here another case's put-back of the line (an undo row, which never blocks as a newer change does) was entered with the old
    price, so the marks say that Exact holds 12,50 already: the press writes nothing and says "Deze wijziging is al teruggedraaid.".
    The note for the researcher is left for a state the rules cannot give, here one that put_back_state is made to give."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    other = PreparedWriter(prepared).apply(on_line("po_line_price", "250108", "1", "12.70", "12.50"), "250108_20261112T100000", UNDO_RULE_ID,
                                           {"undo_of": "elsewhere"}, datetime(2026, 11, 12, 10, 0)).entry_id
    mark(prepared, [other], "entered", datetime(2026, 11, 12, 10, 30))
    first = next(change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.rule_id == "R04")
    kept = files_of(d)
    for language in ("nl", "en"):
        assert take_back(d, first, datetime(2026, 11, 12, 11, 0), language) == STRINGS[language]["hand_put_back_done"]
    assert files_of(d) == kept
    from aid.review import hand_list
    monkeypatch.setattr(hand_list, "put_back_state", lambda *args, **kwargs: "dropped")
    for language, said in (("nl", "Terugdraaien kan nu niet: het terugzetten zou niet op de lijst komen. Er is niets geschreven; geef dit "
                                  "door aan de onderzoeker."),
                           ("en", "Cannot put back now: the put-back would not reach the list. Nothing was written; pass this on to the "
                                  "researcher.")):
        assert take_back(d, first, datetime(2026, 11, 12, 11, 0), language) == said
    assert files_of(d) == kept


def test_a_tick_of_a_change_two_cases_asked_is_not_taken_back_once_it_was_put_back(tmp_path, madeup_out):
    """The checkers' sequence (second review round, K11): MU08's confirmation came in twice, so its price is one change of two cases.
    The buyer ticks it and puts it back; the put-back undoes the newest entry, the second case's. Taking the tick back then writes
    nothing and gives K4's state "undone", with the put-back open, dropped and entered, and the list keeps the put-back as it was."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    again = open_upload_case(d.settings.cases_root, [newer_confirmation(tmp_path / "MU08_conf_again.pdf", "MU08", 1, {})], "250108",
                             "confirmation", datetime(2026, 11, 12, 9, 5))
    record_dispositions(again, run_case(again, d))
    price = one(prepared, "po_line_price")
    assert len(price.ids) == 2 and len(price.case_ids) == 2
    mark(prepared, price.ids, "entered", datetime(2026, 11, 12, 9, 30))
    take_back(d, one(prepared, "po_line_price"), datetime(2026, 11, 12, 10, 0), "nl")
    row = "Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,50"
    for what, state in ((None, "open"), ("dropped", "dropped"), ("reopened", "open"), ("entered", "entered")):
        put = one(prepared, "po_line_price")
        if what is not None:
            mark(prepared, put.ids, what, datetime(2026, 11, 12, 10, 30))
        kept = files_of(d)
        assert reopen(prepared, price.ids, datetime(2026, 11, 12, 11, 0)) == "undone", what
        assert files_of(d) == kept
        assert [(change.new_value, change.state) for change in changes(prepared)[0] if change.kind == "po_line_price"] == [
            ("12.70", "entered"), ("12.50", state)], what
        assert (row in listed_rows(d)) == (state == "open"), what


def test_the_refusal_for_a_newer_change_lifts_once_that_change_is_put_back(tmp_path, madeup_out):
    """The checkers' sequence (second review round, K13): MU08's price entered; Exact sends version 2; a newer confirmation asks 12,90,
    entered too. "Terugdraaien in Exact" on 12,70 is refused, and says how to go on: put the newer change back and tick its put-back
    off. While that put-back waits, the refusal stays; once it is entered, Exact holds 12,70 again, the newer change no longer blocks,
    and the put-back of 12,70 is made and stands open on the list."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.70"}))
    filed_on(d.store, "250108", "v2", datetime(2026, 11, 12, 9, 45))
    second = open_upload_case(d.settings.cases_root, [newer_confirmation(tmp_path / "MU08_conf_2.pdf", "MU08", 2, {"1": "12.90"})],
                              "250108", "confirmation", datetime(2026, 11, 12, 10, 0))
    record_dispositions(second, run_case(second, d))
    newer = one(prepared, "po_line_price")
    mark(prepared, newer.ids, "entered", datetime(2026, 11, 12, 10, 30))
    first = next(change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.new_value == "12.70")
    refused = ("Terugdraaien kan nu niet: er is een nieuwere wijziging ingevoerd, Inkooporder 250108 (S08), regel 1 (KM-0801): prijs "
               "12,70 → 12,90. Draai die eerst terug en vink het terugzetten af; daarna kunt u deze wijziging terugdraaien.")
    kept = files_of(d)
    assert take_back(d, first, datetime(2026, 11, 12, 11, 0), "nl") == refused and files_of(d) == kept
    later = d.model_copy(update={"clock": lambda: datetime(2026, 11, 12, 11, 10)})
    assert take_back(later, one(prepared, "po_line_price"), datetime(2026, 11, 12, 11, 10), "nl").startswith("Nog niet teruggedraaid")
    kept = files_of(d)
    assert take_back(d, first, datetime(2026, 11, 12, 11, 15), "nl") == refused and files_of(d) == kept     # its put-back waits
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 11, 20))     # entered: Exact holds 12,70
    said = take_back(d.model_copy(update={"clock": lambda: datetime(2026, 11, 12, 11, 30)}), first, datetime(2026, 11, 12, 11, 30), "nl")
    row = "Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,50"
    assert said == f"Nog niet teruggedraaid in Exact. Op de lijst staat nu: {row}. Voer de oude waarde in Exact in en vink de regel af."
    assert [(change.old_value, change.new_value, change.state) for change in changes(prepared)[0] if change.kind == "po_line_price"] == [
        ("12.50", "12.70", "entered"), ("12.70", "12.90", "entered"), ("12.90", "12.70", "entered"), ("12.70", "12.50", "open")]
    assert listed_rows(d)[0] == row


def test_a_change_that_can_never_be_put_back_says_so_before_a_newer_change(tmp_path, madeup_out):
    """The checkers' sequence (second review round, K13): the notes that say a change can never be put back come before the refusal
    for a newer change, so that the buyer is never sent to handle a newer change for nothing: an article price whose earlier price the
    aid never read, and a put-back itself, each with a newer change of its place open."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "article_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    PreparedWriter(prepared).apply(article("KM-0801", "12.90", po="250108"), "250108_20261112T100000", "R04", {}, datetime(2026, 11, 12, 10, 0))
    item = next(change for change in changes(prepared)[0] if change.kind == "article_price" and change.state == "entered")
    kept = files_of(d)
    assert take_back(d, item, datetime(2026, 11, 12, 11, 0), "nl") == STRINGS["nl"]["hand_put_back_unknown"] and files_of(d) == kept
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 11, 20))
    take_back(d.model_copy(update={"clock": lambda: datetime(2026, 11, 12, 11, 30)}), one(prepared, "po_line_price"),
              datetime(2026, 11, 12, 11, 30), "nl")
    put = one(prepared, "po_line_price")
    mark(prepared, put.ids, "entered", datetime(2026, 11, 12, 11, 40))
    PreparedWriter(prepared).apply(on_line("po_line_price", "250108", "1", "12.50", "12.60"), "250108_20261112T115000", "R04", {},
                                   datetime(2026, 11, 12, 11, 50))
    put = next(change for change in changes(prepared)[0] if change.ids == put.ids)
    assert (put.rule_id, put.state, one(prepared, "po_line_price").state) == (UNDO_RULE_ID, "entered", "open")
    kept = files_of(d)
    assert take_back(d, put, datetime(2026, 11, 12, 12, 0), "nl") == STRINGS["nl"]["hand_put_back_cannot"] and files_of(d) == kept


# --- the third review round: a put-back stands alone (K20), the newer change that asks the old value (K21), a replaced put-back made
# again (K22), a newer change that can never be put back (K23), and the seen cases of K13 (K25) ---

def confirmed(d, folder: Path, name: str, version: int, prices: dict[str, str], at: datetime):
    """A newer made-up confirmation of S08 that quotes version `version` of 250108, with the unit prices given, run as a case at `at`."""
    case = open_upload_case(d.settings.cases_root, [newer_confirmation(folder / name, "MU08", version, prices)], "250108", "confirmation", at)
    record_dispositions(case, run_case(case, d))
    return case


def line_prices(prepared: Path) -> list[tuple]:
    """The changes of 250108's line 1 price: (old value, new value, rule id, state), in their order."""
    return [(change.old_value, change.new_value, change.rule_id, change.state) for change in changes(prepared)[0]
            if change.kind == "po_line_price" and change.po_number == "250108"]


def line_change(prepared: Path, old: str, new: str, rule_id: str = "R04"):
    """The newest change of 250108's line 1 price with these values and rule."""
    return [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250108"
            and (change.old_value, change.new_value, change.rule_id) == (old, new, rule_id)][-1]


def press(d, change, hour: int, minute: int, language: str = "nl") -> str:
    """"Terugdraaien in Exact" on the change at that time of 12 November, with the page's clock at that time too: the note."""
    moment = datetime(2026, 11, 12, hour, minute)
    return take_back(d.model_copy(update={"clock": lambda: moment}), change, moment, language)


def listed_note(row: str) -> str:
    return f"Nog niet teruggedraaid in Exact. Op de lijst staat nu: {row}. Voer de oude waarde in Exact in en vink de regel af."


PUT_BACK_ROW = "Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs {old} → {new}"


def test_a_newer_change_that_asks_the_old_value_needs_no_put_back(tmp_path, madeup_out):
    """The checkers' f2 and at7 (third review round, K20 and K21): MU08's 12,50 -> 12,70 ticked; Exact sends version 2; a newer
    confirmation that quotes it asks 12,50 again (12,70 -> 12,50). "Terugdraaien in Exact" writes nothing and says that the list
    already asks the old value. Once the buyer dropped that change, the press makes the put-back, which stands open on the list: it no
    longer joins the dropped row of its own value. With the newer change entered instead (at7's variant), Exact holds the old value,
    and the press says "Deze wijziging is al teruggedraaid." and writes nothing."""
    for entered in (False, True):
        folder = tmp_path / ("entered" if entered else "dropped")
        d, prepared, _ = ran(folder, madeup_out, "MU08")
        mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
        d.store.add(newer_po(folder / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.70"}))
        filed_on(d.store, "250108", "v2", datetime(2026, 11, 12, 9, 45))
        confirmed(d, folder, "MU08_conf_2.pdf", 2, {"1": "12.50"}, datetime(2026, 11, 12, 10, 0))
        assert line_prices(prepared) == [("12.50", "12.70", "R04", "entered"), ("12.70", "12.50", "R04", "open")]
        first, newer = line_change(prepared, "12.50", "12.70"), line_change(prepared, "12.70", "12.50")
        if entered:
            mark(prepared, newer.ids, "entered", datetime(2026, 11, 12, 11, 0))
            kept = files_of(d)
            for language in ("nl", "en"):
                assert press(d, first, 11, 10, language) == STRINGS[language]["hand_put_back_done"]
            assert files_of(d) == kept
            continue
        kept = files_of(d)
        said = {"nl": ("Terugdraaien is niet nodig: op de lijst staat al een wijziging naar de oude waarde, Inkooporder 250108 (S08), regel 1 "
                       "(KM-0801): prijs 12,70 → 12,50. Voer die in Exact in en vink die af."),
                "en": ("No need to put back: the list already holds a change to the old value, Purchase order 250108 (S08), line 1 (KM-0801): "
                       "price 12.70 → 12.50. Enter it in Exact and tick it off.")}
        for language in ("nl", "en"):
            assert press(d, first, 11, 0, language) == said[language]
        assert files_of(d) == kept
        mark(prepared, newer.ids, "dropped", datetime(2026, 11, 12, 11, 5))
        row = PUT_BACK_ROW.format(old="12,70", new="12,50")
        assert press(d, first, 11, 10) == listed_note(row)
        assert line_prices(prepared) == [("12.50", "12.70", "R04", "entered"), ("12.70", "12.50", "R04", "dropped"),
                                         ("12.70", "12.50", UNDO_RULE_ID, "open")]
        assert listed_rows(d)[0] == row


def test_a_put_back_that_exact_holds_already_by_the_marks_is_not_made(tmp_path, madeup_out):
    """The checkers' f1 (third review round, K20): MU08's 12,50 -> 12,70 ticked; a second confirmation that quotes version 1 asks
    12,50 -> 12,60, ticked too. "Terugdraaien in Exact" on 12,70 is refused, naming 12,60; the buyer puts 12,60 back and ticks that
    put-back off, so Exact holds 12,50, the old value of 12,70. The next press on 12,70 writes nothing and says "Deze wijziging is al
    teruggedraaid.", where it sent the buyer to the researcher before."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    confirmed(d, tmp_path, "MU08_conf_2.pdf", 1, {"1": "12.60"}, datetime(2026, 11, 12, 10, 0))
    mark(prepared, line_change(prepared, "12.50", "12.60").ids, "entered", datetime(2026, 11, 12, 10, 30))
    first, second = line_change(prepared, "12.50", "12.70"), line_change(prepared, "12.50", "12.60")
    refused = ("Terugdraaien kan nu niet: er is een nieuwere wijziging ingevoerd, Inkooporder 250108 (S08), regel 1 (KM-0801): prijs "
               "12,50 → 12,60. Draai die eerst terug en vink het terugzetten af; daarna kunt u deze wijziging terugdraaien.")
    assert press(d, first, 11, 0) == refused
    assert press(d, second, 11, 10) == listed_note(PUT_BACK_ROW.format(old="12,60", new="12,50"))
    mark(prepared, line_change(prepared, "12.60", "12.50", UNDO_RULE_ID).ids, "entered", datetime(2026, 11, 12, 11, 20))
    kept = files_of(d)
    for language in ("nl", "en"):
        assert press(d, first, 11, 30, language) == STRINGS[language]["hand_put_back_done"]
    assert files_of(d) == kept


def test_a_put_back_that_newer_rows_replaced_is_made_again_once_they_are_handled(tmp_path, madeup_out):
    """The checkers' f3 (third review round, K22): MU08's 12,50 -> 12,70 ticked; a second confirmation asks 12,50 -> 12,60, ticked;
    its put-back is made, and then a third confirmation asks 12,50 -> 12,55, which replaces that put-back, and the buyer drops it. The
    refusal on 12,70 asks to put 12,60 back and tick its put-back off; the press on 12,60 now makes a new put-back, since no row after
    the replaced one is open, entered or seen; once that is entered, Exact holds 12,50 again and the press on 12,70 says so."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    confirmed(d, tmp_path, "MU08_conf_2.pdf", 1, {"1": "12.60"}, datetime(2026, 11, 12, 10, 0))
    mark(prepared, line_change(prepared, "12.50", "12.60").ids, "entered", datetime(2026, 11, 12, 10, 30))
    second = line_change(prepared, "12.50", "12.60")
    press(d, second, 10, 40)
    confirmed(d, tmp_path, "MU08_conf_3.pdf", 1, {"1": "12.55"}, datetime(2026, 11, 12, 11, 0))
    mark(prepared, line_change(prepared, "12.50", "12.55").ids, "dropped", datetime(2026, 11, 12, 11, 5))
    assert line_prices(prepared) == [("12.50", "12.70", "R04", "entered"), ("12.50", "12.60", "R04", "entered"),
                                     ("12.60", "12.50", UNDO_RULE_ID, "replaced"), ("12.50", "12.55", "R04", "dropped")]
    first = line_change(prepared, "12.50", "12.70")
    refused = ("Terugdraaien kan nu niet: er is een nieuwere wijziging ingevoerd, Inkooporder 250108 (S08), regel 1 (KM-0801): prijs "
               "12,50 → 12,60. Draai die eerst terug en vink het terugzetten af; daarna kunt u deze wijziging terugdraaien.")
    assert press(d, first, 11, 10) == refused
    row = PUT_BACK_ROW.format(old="12,60", new="12,50")
    assert press(d, second, 11, 20) == listed_note(row)
    assert line_prices(prepared)[-1] == ("12.60", "12.50", UNDO_RULE_ID, "open") and listed_rows(d)[0] == row
    log = [entry for entry in WriteLog(load_case(d.settings.cases_root, second.case_ids[0]).root).entries() if entry.undo_of]
    assert [(entry.undo_of, entry.status) for entry in log] == [(second.ids[0], "prepared")] * 2       # the second undo of the entry
    mark(prepared, line_change(prepared, "12.60", "12.50", UNDO_RULE_ID).ids, "entered", datetime(2026, 11, 12, 11, 30))
    assert press(d, first, 11, 40) == STRINGS["nl"]["hand_put_back_done"]
    # while a row after the replaced put-back is open, the press says that the put-back does not return, as before
    other = tmp_path / "other"
    d, prepared, _ = ran(other, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    press(d, one(prepared, "po_line_price"), 9, 40)
    confirmed(d, other, "MU08_conf_3.pdf", 1, {"1": "12.55"}, datetime(2026, 11, 12, 11, 0))
    kept = files_of(d)
    assert press(d, line_change(prepared, "12.50", "12.70"), 11, 10) == STRINGS["nl"]["hand_reopen_not"]
    assert files_of(d) == kept


def test_the_refusal_lifts_when_the_newer_change_was_asked_again_after_its_put_back(tmp_path, madeup_out):
    """The checkers' at4 (b) (third review round, K22): 12,50 -> 12,70 entered; a newer confirmation that quotes version 2 asks 12,90,
    entered; its put-back is made, and then the same 12,90 confirmation comes in once more, which replaces the put-back and is
    "already". The press on 12,70 asks to put 12,90 back; the press on 12,90 now makes a new put-back, and once it is entered the
    put-back of 12,70 is made and stands open."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.70"}))
    filed_on(d.store, "250108", "v2", datetime(2026, 11, 12, 9, 45))
    confirmed(d, tmp_path, "MU08_conf_2.pdf", 2, {"1": "12.90"}, datetime(2026, 11, 12, 10, 0))
    newer = line_change(prepared, "12.70", "12.90")
    mark(prepared, newer.ids, "entered", datetime(2026, 11, 12, 10, 30))
    press(d, line_change(prepared, "12.70", "12.90"), 11, 0)
    confirmed(d, tmp_path, "MU08_conf_2_again.pdf", 2, {"1": "12.90"}, datetime(2026, 11, 12, 11, 30))
    assert line_prices(prepared)[2:] == [("12.90", "12.70", UNDO_RULE_ID, "replaced"), ("12.70", "12.90", "R04", "already")]
    first = line_change(prepared, "12.50", "12.70")
    refused = ("Terugdraaien kan nu niet: er is een nieuwere wijziging ingevoerd, Inkooporder 250108 (S08), regel 1 (KM-0801): prijs "
               "12,70 → 12,90. Draai die eerst terug en vink het terugzetten af; daarna kunt u deze wijziging terugdraaien.")
    assert press(d, first, 11, 40) == refused
    row = PUT_BACK_ROW.format(old="12,90", new="12,70")
    assert press(d, next(change for change in changes(prepared)[0] if change.ids == newer.ids), 11, 50) == listed_note(row)
    again = [change for change in changes(prepared)[0] if change.puts_back is not None][-1]
    assert (again.state, again.new_value) == ("open", "12.70")
    mark(prepared, again.ids, "entered", datetime(2026, 11, 12, 12, 0))
    row = PUT_BACK_ROW.format(old="12,70", new="12,50")
    assert press(d, first, 12, 10) == listed_note(row)
    assert line_prices(prepared)[-1] == ("12.70", "12.50", UNDO_RULE_ID, "open") and listed_rows(d)[0] == row


def test_a_newer_change_that_can_never_be_put_back_says_so(tmp_path, madeup_out):
    """The checkers' at4 (a) (third review round, K23): a price list's 12,50 -> 12,60 for KM-0801, entered; then a confirmation's
    article price for KM-0801, whose earlier price the aid never read, entered too. The press on the price list's change names that
    newer change, says that the assistant cannot put it back, and asks the buyer to put the old value back in Exact himself."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    pricelists = d.settings.cases_root / "pricelists" / "2026-11-12"
    pricelists.mkdir(parents=True)
    entry = PreparedWriter(prepared).apply(article("KM-0801", "12.60", old="12.50", po=""), "pricelist_S08_20261112T080000", "P01", {},
                                           datetime(2026, 11, 12, 8, 0))
    WriteLog(pricelists).append(entry)
    mark(prepared, [entry.entry_id], "entered", datetime(2026, 11, 12, 8, 30))
    PreparedWriter(prepared).apply(article("KM-0801", "12.90"), "250108_20261112T100000", "R04", {}, datetime(2026, 11, 12, 10, 0))
    newer = next(change for change in changes(prepared)[0] if change.kind == "article_price" and change.new_value == "12.90")
    mark(prepared, newer.ids, "entered", datetime(2026, 11, 12, 10, 30))
    listed = next(change for change in changes(prepared)[0] if change.ids == [entry.entry_id])
    kept = files_of(d)
    said = {"nl": ("Terugdraaien kan nu niet: er is een nieuwere wijziging ingevoerd, artikel KM-0801: inkoopprijs onbekend → 12,90, die de "
                   "assistent niet kan terugdraaien. Zet de oude waarde zelf terug in Exact."),
            "en": ("Cannot put back now: a newer change was entered, article KM-0801: purchase price unknown → 12.90, which the assistant "
                   "cannot put back. Put the old value back in Exact yourself.")}
    for language in ("nl", "en"):
        assert press(d, listed, 11, 0, language) == said[language]
    assert files_of(d) == kept


def test_a_seen_newer_change_refuses_and_its_seen_put_back_lifts_the_refusal(tmp_path, madeup_out):
    """K13 (b) for "seen" (third review round, K25): a newer change that the aid saw in Exact refuses as an entered one does; and a
    newer change whose own put-back the aid saw in a version filed after it no longer blocks, so the put-back of the first change is
    made and stands open."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.70"}))
    filed_on(d.store, "250108", "v2", datetime(2026, 11, 12, 9, 45))
    confirmed(d, tmp_path, "MU08_conf_2.pdf", 2, {"1": "12.90"}, datetime(2026, 11, 12, 10, 0))
    d.store.add(newer_po(tmp_path / "MU08_po_v3.pdf", "MU08", 3, {"1": "12.90"}))
    filed_on(d.store, "250108", "v3", datetime(2026, 11, 12, 10, 30))
    assert reconcile(prepared, d.store, d.settings.cases_root, datetime(2026, 11, 12, 10, 35)) == 1
    newer = line_change(prepared, "12.70", "12.90")
    assert (newer.state, newer.marked_by) == ("seen", "version 3")
    first = line_change(prepared, "12.50", "12.70")
    refused = ("Terugdraaien kan nu niet: er is een nieuwere wijziging ingevoerd, Inkooporder 250108 (S08), regel 1 (KM-0801): prijs "
               "12,70 → 12,90. Draai die eerst terug en vink het terugzetten af; daarna kunt u deze wijziging terugdraaien.")
    kept = files_of(d)
    assert press(d, first, 11, 0) == refused and files_of(d) == kept
    assert press(d, newer, 11, 10) == listed_note(PUT_BACK_ROW.format(old="12,90", new="12,70"))
    d.store.add(newer_po(tmp_path / "MU08_po_v4.pdf", "MU08", 4, {"1": "12.70"}))
    filed_on(d.store, "250108", "v4", datetime(2026, 11, 12, 11, 20))
    assert reconcile(prepared, d.store, d.settings.cases_root, datetime(2026, 11, 12, 11, 25)) == 1
    assert line_prices(prepared)[-1] == ("12.90", "12.70", UNDO_RULE_ID, "seen")
    row = PUT_BACK_ROW.format(old="12,70", new="12,50")
    assert press(d, first, 11, 30) == listed_note(row)
    assert line_prices(prepared)[-1] == ("12.70", "12.50", UNDO_RULE_ID, "open") and listed_rows(d)[0] == row


def test_a_newer_change_that_no_longer_stands_in_exact_does_not_refuse(tmp_path, madeup_out):
    """The model lens (third review round, K20 (b)): MU08's 12,50 -> 12,70 ticked; two newer confirmations that quote version 1 ask
    12,60 and then 12,55, both ticked, and the buyer puts 12,55 back and ticks that off, so the marks say that Exact holds 12,50
    again. The presses on 12,70 and on 12,60 say "Deze wijziging is al teruggedraaid." and write nothing: before, the press on 12,70
    named 12,60, whose own press said that, and the advice could not be followed."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    for minute, value in ((0, "12.60"), (20, "12.55")):
        confirmed(d, tmp_path, f"MU08_conf_{value}.pdf", 1, {"1": value}, datetime(2026, 11, 12, 10, minute))
        mark(prepared, line_change(prepared, "12.50", value).ids, "entered", datetime(2026, 11, 12, 10, minute + 10))
    assert press(d, line_change(prepared, "12.50", "12.55"), 10, 40) == listed_note(PUT_BACK_ROW.format(old="12,55", new="12,50"))
    mark(prepared, line_change(prepared, "12.55", "12.50", UNDO_RULE_ID).ids, "entered", datetime(2026, 11, 12, 10, 50))
    kept = files_of(d)
    for value in ("12.70", "12.60"):
        for language in ("nl", "en"):
            assert press(d, line_change(prepared, "12.50", value), 11, 0, language) == STRINGS[language]["hand_put_back_done"]
    assert files_of(d) == kept


def test_a_newer_change_whose_put_back_exact_holds_already_does_not_refuse(tmp_path, madeup_out):
    """The model lens (third review round, K13 and K20): MU08's 12,50 -> 12,70 ticked; version 2 shows it; newer confirmations that
    quote version 2 ask 12,90 and 12,80, both ticked; 12,80 is put back and that is ticked off, so the marks say that Exact holds 12,70.
    The operator then undoes the 12,90 case with MANUAL 3.10's recipe, and a press on 12,90 links that undo, whose state is "already".
    12,90 no longer stands in Exact, so the press on 12,70 makes its put-back, where it named 12,90 before, whose put-back cannot be
    ticked off."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.70"}))
    filed_on(d.store, "250108", "v2", datetime(2026, 11, 12, 9, 45))
    newer = confirmed(d, tmp_path, "MU08_conf_12.90.pdf", 2, {"1": "12.90"}, datetime(2026, 11, 12, 10, 0))
    mark(prepared, line_change(prepared, "12.70", "12.90").ids, "entered", datetime(2026, 11, 12, 10, 10))
    confirmed(d, tmp_path, "MU08_conf_12.80.pdf", 2, {"1": "12.80"}, datetime(2026, 11, 12, 10, 20))
    mark(prepared, line_change(prepared, "12.70", "12.80").ids, "entered", datetime(2026, 11, 12, 10, 30))
    assert press(d, line_change(prepared, "12.70", "12.80"), 10, 40) == listed_note(PUT_BACK_ROW.format(old="12,80", new="12,70"))
    mark(prepared, line_change(prepared, "12.80", "12.70", UNDO_RULE_ID).ids, "entered", datetime(2026, 11, 12, 10, 50))
    undo_case(newer.root, PreparedWriter(prepared), datetime(2026, 11, 12, 11, 0))
    assert press(d, line_change(prepared, "12.70", "12.90"), 11, 10) == STRINGS["nl"]["hand_put_back_done"]
    assert line_prices(prepared)[-1] == ("12.90", "12.70", UNDO_RULE_ID, "already")
    assert line_change(prepared, "12.90", "12.70", UNDO_RULE_ID).puts_back == line_change(prepared, "12.70", "12.90").ids[0]
    row = PUT_BACK_ROW.format(old="12,70", new="12,50")
    assert press(d, line_change(prepared, "12.50", "12.70"), 11, 20) == listed_note(row)
    assert line_prices(prepared)[-1] == ("12.70", "12.50", UNDO_RULE_ID, "open") and listed_rows(d)[0] == row


def test_a_newer_change_whose_value_exact_no_longer_holds_does_not_refuse(tmp_path, madeup_out):
    """The model lens (third review round, K13 (b) and K21): MU08's 12,50 -> 12,70 ticked; version 2 shows it; a confirmation that
    quotes version 2 asks 12,50 again, ticked; another that quotes version 2 as well asks 12,90, ticked, and is put back to the 12,70
    of that version, which is ticked off too. The marks now say that Exact holds 12,70 again, the value of the first change: the press
    on it makes its put-back, where it said "Deze wijziging is al teruggedraaid." because a newer change had asked 12,50, a value that
    Exact no longer holds."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.70"}))
    filed_on(d.store, "250108", "v2", datetime(2026, 11, 12, 9, 45))
    for minute, value in ((0, "12.50"), (20, "12.90")):
        confirmed(d, tmp_path, f"MU08_conf_{value}.pdf", 2, {"1": value}, datetime(2026, 11, 12, 10, minute))
        mark(prepared, line_change(prepared, "12.70", value).ids, "entered", datetime(2026, 11, 12, 10, minute + 10))
    assert press(d, line_change(prepared, "12.70", "12.90"), 10, 40) == listed_note(PUT_BACK_ROW.format(old="12,90", new="12,70"))
    mark(prepared, line_change(prepared, "12.90", "12.70", UNDO_RULE_ID).ids, "entered", datetime(2026, 11, 12, 10, 50))
    row = PUT_BACK_ROW.format(old="12,70", new="12,50")
    assert press(d, line_change(prepared, "12.50", "12.70"), 11, 0) == listed_note(row)
    assert line_prices(prepared)[-1] == ("12.70", "12.50", UNDO_RULE_ID, "open") and listed_rows(d)[0] == row


def test_a_change_whose_old_value_exact_holds_was_put_back_whatever_newer_change_is_open(tmp_path, madeup_out):
    """K20 (b), third review round: the put-back is worked out before a newer change refuses it. MU08's 12,50 -> 12,70 ticked; version
    2 shows it; a confirmation that quotes version 2 asks 12,50 again, ticked, so the marks say that Exact holds 12,50; another that
    quotes version 2 as well asks 12,90, which stands open. The press on 12,70 says "Deze wijziging is al teruggedraaid." and writes
    nothing, where it asked to drop 12,90 first, after which it would have said the same."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.70"}))
    filed_on(d.store, "250108", "v2", datetime(2026, 11, 12, 9, 45))
    confirmed(d, tmp_path, "MU08_conf_12.50.pdf", 2, {"1": "12.50"}, datetime(2026, 11, 12, 10, 0))
    mark(prepared, line_change(prepared, "12.70", "12.50").ids, "entered", datetime(2026, 11, 12, 10, 10))
    confirmed(d, tmp_path, "MU08_conf_12.90.pdf", 2, {"1": "12.90"}, datetime(2026, 11, 12, 10, 20))
    assert line_prices(prepared)[-1] == ("12.70", "12.90", "R04", "open")
    kept = files_of(d)
    for language in ("nl", "en"):
        assert press(d, line_change(prepared, "12.50", "12.70"), 10, 30, language) == STRINGS[language]["hand_put_back_done"]
    assert files_of(d) == kept


def test_a_dropped_put_back_that_a_dropped_newer_row_follows_is_made_again(tmp_path, madeup_out):
    """K22 for a put-back the buyer dropped (third review round): MU08's 12,50 -> 12,70 ticked and put back; the buyer drops the
    put-back; a newer confirmation that quotes version 1 asks 12,60, which he drops too. The put-back cannot return to the list (a row
    came after it), and nothing after it is open, entered or seen: the press makes a new put-back, where it said that another change
    had come in, which the buyer could do nothing about. While that newer row is open, the press still says so."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    first = line_change(prepared, "12.50", "12.70")
    row = PUT_BACK_ROW.format(old="12,70", new="12,50")
    assert press(d, first, 9, 40) == listed_note(row)
    mark(prepared, line_change(prepared, "12.70", "12.50", UNDO_RULE_ID).ids, "dropped", datetime(2026, 11, 12, 9, 50))
    confirmed(d, tmp_path, "MU08_conf_12.60.pdf", 1, {"1": "12.60"}, datetime(2026, 11, 12, 10, 0))
    kept = files_of(d)
    assert press(d, first, 10, 10) == STRINGS["nl"]["hand_reopen_not"] and files_of(d) == kept
    mark(prepared, line_change(prepared, "12.50", "12.60").ids, "dropped", datetime(2026, 11, 12, 10, 20))
    assert press(d, first, 10, 30) == listed_note(row)
    assert line_prices(prepared)[1:] == [("12.70", "12.50", UNDO_RULE_ID, "dropped"), ("12.50", "12.60", "R04", "dropped"),
                                         ("12.70", "12.50", UNDO_RULE_ID, "open")]
    assert listed_rows(d)[0] == row


def test_a_replaced_put_back_is_made_again_once_the_rows_after_it_no_longer_stand(tmp_path, madeup_out):
    """K22 with the model lens (third review round): MU08's 12,50 -> 12,70 ticked; a confirmation that quotes version 1 asks 12,60,
    ticked, and its put-back is made; version 2 shows 12,60 and a confirmation that quotes it asks 12,50, ticked, which replaces that
    put-back; that change is put back in turn and its put-back ticked, so Exact holds 12,60 again. The press on 12,70 names 12,60; the
    press on 12,60 makes a new put-back, as nothing after the first one still stands (the newer 12,50 was put back), where it said
    that another change had come in, so that the advice could not be followed. Once that is ticked, 12,70 was put back already."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    confirmed(d, tmp_path, "MU08_conf_12.60.pdf", 1, {"1": "12.60"}, datetime(2026, 11, 12, 10, 0))
    mark(prepared, line_change(prepared, "12.50", "12.60").ids, "entered", datetime(2026, 11, 12, 10, 10))
    newer = line_change(prepared, "12.50", "12.60")
    assert press(d, newer, 10, 20) == listed_note(PUT_BACK_ROW.format(old="12,60", new="12,50"))
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.60"}))
    filed_on(d.store, "250108", "v2", datetime(2026, 11, 12, 10, 25))
    confirmed(d, tmp_path, "MU08_conf_12.50.pdf", 2, {"1": "12.50"}, datetime(2026, 11, 12, 10, 30))
    mark(prepared, line_change(prepared, "12.60", "12.50").ids, "entered", datetime(2026, 11, 12, 10, 40))
    assert press(d, line_change(prepared, "12.60", "12.50"), 10, 50) == listed_note(PUT_BACK_ROW.format(old="12,50", new="12,60"))
    mark(prepared, line_change(prepared, "12.50", "12.60", UNDO_RULE_ID).ids, "entered", datetime(2026, 11, 12, 11, 0))
    first = line_change(prepared, "12.50", "12.70")
    refused = ("Terugdraaien kan nu niet: er is een nieuwere wijziging ingevoerd, Inkooporder 250108 (S08), regel 1 (KM-0801): prijs "
               "12,50 → 12,60. Draai die eerst terug en vink het terugzetten af; daarna kunt u deze wijziging terugdraaien.")
    assert press(d, first, 11, 10) == refused
    row = PUT_BACK_ROW.format(old="12,60", new="12,50")
    assert press(d, next(change for change in changes(prepared)[0] if change.ids == newer.ids), 11, 20) == listed_note(row)
    assert line_prices(prepared)[-1] == ("12.60", "12.50", UNDO_RULE_ID, "open") and listed_rows(d)[0] == row
    mark(prepared, line_prices_ids(prepared)[-1], "entered", datetime(2026, 11, 12, 11, 30))
    assert press(d, first, 11, 40) == STRINGS["nl"]["hand_put_back_done"]


def line_prices_ids(prepared: Path) -> list[list[str]]:
    """The entry ids of the changes of 250108's line 1 price, in their order."""
    return [change.ids for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250108"]


def test_a_newer_change_whose_put_back_was_seen_does_not_refuse_when_exact_holds_its_value_again(tmp_path, madeup_out):
    """K13 (b) for "seen", apart from the value the marks say Exact holds (third review round, K25): MU08's 12,50 -> 12,70 ticked;
    version 2 shows it; a confirmation that quotes version 2 asks 12,90, ticked; version 3 shows 12,90; its put-back is made and seen
    in version 4. A confirmation that quotes version 3 asks 12,80, ticked, and is put back to the 12,90 of that version, which is
    ticked off: the marks say that Exact holds 12,90 again. The 12,90 change still does not block, as its own put-back was seen: the
    press on 12,70 makes its put-back."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.70"}))
    filed_on(d.store, "250108", "v2", datetime(2026, 11, 12, 9, 45))
    confirmed(d, tmp_path, "MU08_conf_12.90.pdf", 2, {"1": "12.90"}, datetime(2026, 11, 12, 10, 0))
    mark(prepared, line_change(prepared, "12.70", "12.90").ids, "entered", datetime(2026, 11, 12, 10, 10))
    d.store.add(newer_po(tmp_path / "MU08_po_v3.pdf", "MU08", 3, {"1": "12.90"}))
    filed_on(d.store, "250108", "v3", datetime(2026, 11, 12, 10, 15))
    assert press(d, line_change(prepared, "12.70", "12.90"), 10, 20) == listed_note(PUT_BACK_ROW.format(old="12,90", new="12,70"))
    d.store.add(newer_po(tmp_path / "MU08_po_v4.pdf", "MU08", 4, {"1": "12.70"}))
    filed_on(d.store, "250108", "v4", datetime(2026, 11, 12, 10, 30))
    assert reconcile(prepared, d.store, d.settings.cases_root, datetime(2026, 11, 12, 10, 35)) == 1
    assert line_change(prepared, "12.90", "12.70", UNDO_RULE_ID).state == "seen"
    confirmed(d, tmp_path, "MU08_conf_12.80.pdf", 3, {"1": "12.80"}, datetime(2026, 11, 12, 10, 40))
    mark(prepared, line_change(prepared, "12.90", "12.80").ids, "entered", datetime(2026, 11, 12, 10, 45))
    assert press(d, line_change(prepared, "12.90", "12.80"), 10, 50) == listed_note(PUT_BACK_ROW.format(old="12,80", new="12,90"))
    mark(prepared, line_change(prepared, "12.80", "12.90", UNDO_RULE_ID).ids, "entered", datetime(2026, 11, 12, 10, 55))
    row = PUT_BACK_ROW.format(old="12,70", new="12,50")
    assert press(d, line_change(prepared, "12.50", "12.70"), 11, 0) == listed_note(row)
    assert line_prices(prepared)[-1] == ("12.70", "12.50", UNDO_RULE_ID, "open") and listed_rows(d)[0] == row


def test_a_replaced_put_back_says_put_back_already_when_a_newer_change_entered_the_old_value(tmp_path, madeup_out):
    """K20 (b) on a put-back that newer rows replaced (third review round): MU08's 12,50 -> 12,70 ticked; version 2 shows it; a
    confirmation that quotes version 2 asks 12,90, ticked, and its put-back is made; version 3 shows 12,90 and a confirmation that
    quotes it asks 12,70, which replaces that put-back, and is ticked. The marks say that Exact holds 12,70, the old value of 12,90:
    the press on 12,90 says "Deze wijziging is al teruggedraaid." and writes nothing, where it said that another change had come in."""
    d, prepared, _ = ran(tmp_path, madeup_out, "MU08")
    mark(prepared, one(prepared, "po_line_price").ids, "entered", datetime(2026, 11, 12, 9, 30))
    d.store.add(newer_po(tmp_path / "MU08_po_v2.pdf", "MU08", 2, {"1": "12.70"}))
    filed_on(d.store, "250108", "v2", datetime(2026, 11, 12, 9, 45))
    confirmed(d, tmp_path, "MU08_conf_12.90.pdf", 2, {"1": "12.90"}, datetime(2026, 11, 12, 10, 0))
    mark(prepared, line_change(prepared, "12.70", "12.90").ids, "entered", datetime(2026, 11, 12, 10, 10))
    newer = line_change(prepared, "12.70", "12.90")
    assert press(d, newer, 10, 20) == listed_note(PUT_BACK_ROW.format(old="12,90", new="12,70"))
    d.store.add(newer_po(tmp_path / "MU08_po_v3.pdf", "MU08", 3, {"1": "12.90"}))
    filed_on(d.store, "250108", "v3", datetime(2026, 11, 12, 10, 25))
    confirmed(d, tmp_path, "MU08_conf_12.70.pdf", 3, {"1": "12.70"}, datetime(2026, 11, 12, 10, 30))
    mark(prepared, line_change(prepared, "12.90", "12.70").ids, "entered", datetime(2026, 11, 12, 10, 40))
    assert line_prices(prepared)[2:] == [("12.90", "12.70", UNDO_RULE_ID, "replaced"), ("12.90", "12.70", "R04", "entered")]
    kept = files_of(d)
    for language in ("nl", "en"):
        assert press(d, next(change for change in changes(prepared)[0] if change.ids == newer.ids), 10, 50, language) == (
            STRINGS[language]["hand_put_back_done"])
    assert files_of(d) == kept
