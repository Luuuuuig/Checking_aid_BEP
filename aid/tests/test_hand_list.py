"""Tests of the list 'In Exact invoeren' as the page shows it (aid/review/hand_list.py): its blocks in their order, its rows with the
item code of the version each case was compared with, the values in the two languages, and the hint on an order above the
authorization, which shows no amount. The cases are made-up case folders with step 01 as the pipeline writes it, their changes
prepared by the prepared writer."""
import csv
import io
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from aid.case import open_case
from aid.execution.by_hand import open_changes
from aid.execution.ops import WriteOp
from aid.execution.by_hand import mark
from aid.execution.writer import UNDO_RULE_ID, PreparedWriter
from aid.madeup.generate import export_csv, load_specs
from aid.po_store import PoStore, PoVersion
from aid.review.hand_list import blocks, ids_of, row_text
from aid.review.i18n import STRINGS
from aid.tests.conftest import REPO_ROOT, deps
from aid.tests.fakes import line, po_version
from aid.tests.helpers import EXPORT_COLUMNS

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
