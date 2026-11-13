"""Tests of the tab 'Prijslijst' of the review page: what it shows and writes without the page library (aid/review/pricelist_page.py),
and the tab itself through its handlers by name as the page holds them (aid/review/page.py): no browser. Every list, export, price and
supplier is made up; no test calls the network."""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from aid.execution.by_hand import mark, open_changes
from aid.execution.writer import WriteLog
from aid.pricelist import current_prices, kept_columns, store_current, stored_prices
from aid.readers.po_export import ExportRefused
from aid.review.i18n import STRINGS
from aid.review.pricelist_page import (LIST_COLUMNS, ListRefused, carried_line, carry_out, checked_columns, checked_export, column_map,
                                       columns_for, count_line, filed_line, headers, list_titles, on_list, plan, preview, refusal_text,
                                       shown, state_line)
from aid.tests.helpers import wrong_dimensions, write_sheet
from aid.tests.test_pricelist import ARTICLE_COLUMNS, HEADER, articles, item, prepared_rows, write_csv, write_xlsx

NL, EN = STRINGS["nl"], STRINGS["en"]
NOW = datetime(2026, 11, 20, 9, 30)                       # the made-up moment a list is viewed and carried out
STORED = datetime(2026, 11, 20, 9, 15)                    # when the made-up export was given
CURRENT = {"KM-1001": "12.50", "KM-1002": "8.00", "KM-1003": "5.00", "KM-1004": "3.20"}
MAP = {column: column for column in ("item_code", "price", "basis", "valid_from", "valid_to")}     # a list with the aid's own titles
# A made-up list with a row for every outcome: a price that changes, one that stays the same, an unknown article, a price that cannot
# be read, a price per hundred, a row not valid yet, an expired row, a second row of an article, a row without an item code, and two
# rows whose dates cannot be read.
EVERY_OUTCOME = [item("KM-1001", "12.70"), item("KM-1002", "8.00"), item("KM-9999", "4.10"), item("KM-1003", "op aanvraag"),
                 item("KM-1003", "450.00", basis="100"), item("KM-1004", "3.50", valid_from="2027-01-01"),
                 item("KM-1004", "3.10", valid_to="2026-06-30"), item("KM-1004", "3.40"), item("KM-1004", "3.45"), item("", "9.99"),
                 item("KM-1003", "5.10", valid_from="binnenkort"), item("KM-1003", "5.10", valid_to="tot nader order")]


def files_of(*folders: Path) -> dict:
    """Every file under the folders, with its bytes."""
    return {path: path.read_bytes() for folder in folders if folder.exists() for path in folder.rglob("*") if path.is_file()}


def test_a_view_writes_nothing(tmp_path):
    """Rule 3: 'Bekijken' writes nothing anywhere: the import runs with a writer that records only, so the result names each price
    that changes while no file is made or changed under the cases root or beside the list."""
    root = tmp_path / "cases"
    store_current(root, articles(tmp_path / "artikelen.csv", [["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", "8,00"]]), ARTICLE_COLUMNS,
                  True, STORED, "S01")
    listed = write_csv(tmp_path / "lijst.csv", [item("KM-1001", "12.70"), item("KM-1002", "8.00"), item("KM-9999", "4.10")])
    before = files_of(tmp_path)
    current, _ = current_prices(root, "S01")
    result = preview(listed, "S01", current, MAP, NOW)
    assert [(entry.op.article_code, entry.previous, entry.new, entry.status) for entry in result.updated] == [("KM-1001", "12.50", "12.70", "prepared")]
    assert result.skipped == [("KM-1002", "unchanged"), ("KM-9999", "unknown article")] and result.supplier == "S01"
    assert plan(result) == [["KM-1001", "12.50", "12.70"], ["KM-1002", "unchanged"], ["KM-9999", "unknown article"]]
    assert files_of(tmp_path) == before and not (root / "prepared").exists() and not (root / "pricelists").exists()
    assert plan(preview(listed, "S01", current, {}, NOW)) == plan(result)                  # no map: the aid's own titles
    with pytest.raises(ValueError):                                                         # a list that cannot be read, and still no file
        preview(listed, "S01", current, {"price": "Nettoprijs"}, NOW)
    assert files_of(tmp_path) == before


def test_carrying_out_prepares_what_the_view_showed_and_logs_it(tmp_path):
    """Rule 4: the press prepares exactly the changes the view showed, through the prepared writer into <cases_root>/prepared, the write
    path of every change, and logs them in the price list's own write log as the import's command does."""
    root = tmp_path / "cases"
    listed = write_xlsx(tmp_path / "lijst.xlsx", [("KM-1001", 12.7, 1, "pc", 1, None, None), ("KM-1002", 8, 1, "pc", 1, None, None),
                                                   ("KM-1004", "3,45", None, None, None, None, None)])
    viewed = preview(listed, "S01", CURRENT, MAP, NOW)
    assert not root.exists()
    result = carry_out(listed, "S01", CURRENT, MAP, root, NOW)
    assert plan(result) == plan(viewed) == [["KM-1001", "12.50", "12.7"], ["KM-1002", "unchanged"], ["KM-1004", "3.20", "3.45"]]
    rows = prepared_rows(root / "prepared")
    assert [(row["case_id"], row["article_code"], row["kind"], row["old_value"], row["new_value"], row["rule_id"], row["timestamp"]) for row in rows] == [
        ("pricelist_S01_20261120T093000", "KM-1001", "article_price", "12.50", "12.7", "P01", "2026-11-20T09:30:00"),
        ("pricelist_S01_20261120T093000", "KM-1004", "article_price", "3.20", "3.45", "P01", "2026-11-20T09:30:00")]
    logged = WriteLog(root / "pricelists" / "2026-11-20").entries()
    assert logged == result.updated and [entry.entry_id for entry in logged] == [row["entry_id"] for row in rows]
    assert [(entry.status, entry.evidence["supplier"], entry.evidence["file"]) for entry in logged] == [("prepared", "S01", "lijst.xlsx")] * 2
    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()) == [
        "prepared/prepared_changes.csv", "pricelists/2026-11-20/write_log.jsonl"]
    assert [change.article_code for change in open_changes(root / "prepared")] == ["KM-1001", "KM-1004"] and on_list(result, root) == 2
    # a list in which no price changes prepares nothing and makes no write log
    same = carry_out(write_csv(tmp_path / "gelijk.csv", [item("KM-1002", "8.00")]), "S01", CURRENT, MAP, tmp_path / "other", NOW)
    assert same.updated == [] and not (tmp_path / "other").exists() and on_list(same, tmp_path / "other") == 0


def test_the_same_list_twice_adds_no_row(tmp_path):
    """Rule 5: the same list carried out twice prepares its rows twice, and the list In Exact invoeren shows one row per article and
    value. A change the buyer dropped for that value stays off the list, and the press says so by its count."""
    root = tmp_path / "cases"
    listed = write_csv(tmp_path / "lijst.csv", [item("KM-1001", "12.70"), item("KM-1004", "3.45")])
    first = carry_out(listed, "S01", CURRENT, MAP, root, NOW)
    again = carry_out(listed, "S01", CURRENT, MAP, root, NOW + timedelta(minutes=5))
    assert len(prepared_rows(root / "prepared")) == 4 and len(WriteLog(root / "pricelists" / "2026-11-20").entries()) == 4
    listed_now = open_changes(root / "prepared")
    assert [(change.article_code, change.new_value, len(change.ids)) for change in listed_now] == [("KM-1001", "12.70", 2), ("KM-1004", "3.45", 2)]
    assert on_list(first, root) == on_list(again, root) == 2
    assert mark(root / "prepared", listed_now[0].ids, "dropped", NOW + timedelta(minutes=6)) == 2
    third = carry_out(listed, "S01", CURRENT, MAP, root, NOW + timedelta(minutes=10))
    assert len(third.updated) == 2 and on_list(third, root) == 1                           # the dropped change is not asked again
    assert carried_line(1, "nl") == "1 prijswijziging staat op de lijst In Exact invoeren."
    assert carried_line(2, "nl") == "2 prijswijzigingen staan op de lijst In Exact invoeren."
    assert (carried_line(1, "en"), carried_line(0, "en")) == ("1 price change is on the list Enter in Exact.",
                                                              "0 price changes are on the list Enter in Exact.")


def test_the_price_lists_to_tick_are_named_by_supplier_and_time_and_given_once(tmp_path):
    """Rule 10, what the press of the tab In Exact invoeren offers: the price lists with article prices still to enter, the newest
    first, each named by its supplier and the time it was put on the list; the same list put on the list again holds the same rows
    and is given once, by the newest; a list all of whose rows are handled is no longer given."""
    from aid.review.hand_list import price_lists
    root = tmp_path / "cases"
    listed = write_csv(tmp_path / "lijst.csv", [item("KM-1001", "12.70"), item("KM-1004", "3.45")])
    carry_out(listed, "S01", CURRENT, MAP, root, NOW)
    carry_out(listed, "S01", CURRENT, MAP, root, NOW + timedelta(minutes=5))             # the same list again: the same rows
    carry_out(write_csv(tmp_path / "s02.csv", [item("KM-1002", "8.10")]), "Voorbeeld B.V.", CURRENT, MAP, root, NOW + timedelta(days=1))
    open_now = open_changes(root / "prepared")
    rows = ["+".join(change.ids) for change in open_now]
    assert price_lists(open_now, root, "nl") == [
        ("Voorbeeld B.V. (21-11-2026 09:30)", "pricelist_Voorbeeld B.V._20261121T093000", [rows[2]]),
        ("S01 (20-11-2026 09:35)", "pricelist_S01_20261120T093500", rows[:2])]
    assert [label for label, _, _ in price_lists(open_now, root, "en")] == ["Voorbeeld B.V. (21-11-2026 09:30)", "S01 (20-11-2026 09:35)"]
    assert mark(root / "prepared", open_now[0].ids, "entered", NOW + timedelta(days=2)) == 2
    assert price_lists(open_changes(root / "prepared"), root, "nl")[1] == ("S01 (20-11-2026 09:35)", "pricelist_S01_20261120T093500", [rows[1]])
    for change in open_changes(root / "prepared"):
        mark(root / "prepared", change.ids, "dropped", NOW + timedelta(days=2))
    assert price_lists(open_changes(root / "prepared"), root, "nl") == []
    # a case folder whose name looks like a price list's is a case, and no price list: here the folder of the one list that no
    # other list doubles, so that the look at case.json decides (ruling Z2 of the review round)
    assert [case_id for _, case_id, _ in price_lists(open_now, root, "nl")] == ["pricelist_Voorbeeld B.V._20261121T093000",
                                                                                "pricelist_S01_20261120T093500"]
    (root / "pricelist_Voorbeeld B.V._20261121T093000").mkdir()
    (root / "pricelist_Voorbeeld B.V._20261121T093000" / "case.json").write_text("{}", encoding="utf-8")
    assert [case_id for _, case_id, _ in price_lists(open_now, root, "nl")] == ["pricelist_S01_20261120T093500"]


def test_the_outcomes_have_their_labels(tmp_path):
    """Rule 8: every reason for passing a row over is shown by its label in the page's language, never by the import's own reason or a
    rule code; the count line counts what changes, what stays and what was passed over, and a list with expired rows gets the advice."""
    listed = write_csv(tmp_path / "lijst.csv", EVERY_OUTCOME)
    result = preview(listed, "S01", CURRENT, MAP, NOW)
    assert headers("nl") == ["Artikel", "Inkoopprijs in Exact", "Prijs op de lijst", "Uitkomst"]
    assert headers("en") == ["Article", "Purchase price in Exact", "Price on the list", "Outcome"]
    assert shown(result, CURRENT, "nl") == [
        ["KM-1001", "12,50", "12,70", "prijs verandert"], ["KM-1002", "8,00", "8,00", "ongewijzigd"],
        ["KM-9999", "", "", "artikel niet bekend in Exact"], ["KM-1003", "5,00", "", "prijs niet te lezen"],
        ["KM-1003", "5,00", "", "prijs per ander aantal: niet vergeleken"], ["KM-1004", "3,20", "", "nog niet geldig"],
        ["KM-1004", "3,20", "", "verlopen"], ["KM-1004", "3,20", "3,40", "prijs verandert"], ["KM-1004", "3,20", "", "dubbele rij"],
        ["rij 11", "", "", "geen artikelcode"], ["KM-1003", "5,00", "", "geldig vanaf niet te lezen"],
        ["KM-1003", "5,00", "", "geldig tot niet te lezen"]]
    english = shown(result, CURRENT, "en")
    assert [row[3] for row in english] == [
        "price changes", "unchanged", "article not known in Exact", "price cannot be read", "price per another number: not compared",
        "not valid yet", "expired", "price changes", "duplicate row", "no item code", "valid from cannot be read", "valid to cannot be read"]
    assert english[0][:3] == ["KM-1001", "12.50", "12.70"] and english[9][0] == "row 11"
    assert count_line(result, "nl") == ("2 prijzen veranderen, 1 blijft gelijk, 9 rijen overgeslagen. Sommige rijen zijn verlopen: vraag de "
                                        "leverancier om een actuele lijst.")
    assert count_line(result, "en") == "2 prices change, 1 stays the same, 9 rows passed over. Some rows are expired: ask the supplier for a current list."
    one = preview(write_csv(tmp_path / "een.csv", [item("KM-1001", "12.70"), item("KM-1002", "8.00"), item("KM-9999", "4.10"),
                                                   item("KM-1004", "3.20")]), "S01", CURRENT, MAP, NOW)
    assert count_line(one, "nl") == "1 prijs verandert, 2 blijven gelijk, 1 rij overgeslagen."
    assert count_line(one, "en") == "1 price changes, 2 stay the same, 1 row passed over."
    for language in ("nl", "en"):
        text = json.dumps([shown(result, CURRENT, language), count_line(result, language)], ensure_ascii=False)
        assert not re.search(r"\bP01\b|\bR\d{2}\b|unknown article|unreadable|not yet valid|price basis|—|–", text), language
    # a row whose write failed is named by a label, never by the error's text
    failed = result.model_copy(update={"outcomes": [("KM-1001", "error: cannot write /made-up/prepared_changes.csv: [Errno 13]")]})
    assert shown(failed, CURRENT, "nl") == [["KM-1001", "12,50", "", "niet gelukt"]] and shown(failed, CURRENT, "en")[0][3] == "failed"


def test_the_columns_come_from_the_map_kept_for_the_supplier(tmp_path):
    """Rule 9: the columns of the list are filled from the map kept for the supplier, else from the file's titles that equal the aid's.
    The titles are those of the list's first filled row; a field of a kept map whose title the file lacks stays empty."""
    own = write_csv(tmp_path / "eigen.csv", [item()])
    assert list_titles(own) == list(HEADER)
    assert columns_for(list_titles(own), None) == {"item_code": "item_code", "price": "price", "basis": "basis", "valid_from": "valid_from",
                                                  "valid_to": "valid_to"}
    assert columns_for(list_titles(own), {}) == columns_for(list_titles(own), None)
    dutch = write_sheet(tmp_path / "lijst.xlsx", [[], ["Artikelnummer", " Nettoprijs ", "Per", "Ingangsdatum", "ARTIKELNUMMER", None],
                                                  ["KM-1001", 12.7, 1, "2026-11-01", "x"]])
    assert list_titles(dutch) == ["Artikelnummer", "Nettoprijs", "Per", "Ingangsdatum"]      # trimmed, an empty cell out, each title once
    assert columns_for(list_titles(dutch), None) == dict.fromkeys(LIST_COLUMNS, "")           # no title equals the aid's
    kept = {"item_code": "artikelnummer", "price": "Nettoprijs", "valid_to": "Einddatum"}
    assert columns_for(list_titles(dutch), kept) == {"item_code": "Artikelnummer", "price": "Nettoprijs", "basis": "", "valid_from": "",
                                                     "valid_to": ""}                           # as the file spells it; no title of its own
    assert columns_for(list_titles(own), kept) == dict.fromkeys(LIST_COLUMNS, "")             # the kept map decides, not the file's titles
    # the map of the form: the fields left empty are left out, and what is missing is named in the page's language
    assert column_map({"item_code": " Artikelnummer ", "price": "Nettoprijs", "basis": "", "valid_from": None, "valid_to": "Ingangsdatum"}, "nl") == (
        {"item_code": "Artikelnummer", "price": "Nettoprijs", "valid_to": "Ingangsdatum"}, [])
    assert column_map({}, "nl") == (None, ["Kies de kolom met de artikelcode.", "Kies de kolom met de prijs."])
    assert column_map({"item_code": "Code"}, "en") == (None, ["Choose the column with the price."])
    assert column_map({"item_code": "Code", "price": "code", "basis": "CODE", "valid_from": "Van", "valid_to": "Van"}, "nl") == (
        None, ["De kolom \"Code\" is meer dan een keer gekozen.", "De kolom \"Van\" is meer dan een keer gekozen."])
    # a chosen title that stands twice in the row of titles is refused with its row; the list is read with the map of the form
    with pytest.raises(ListRefused) as twice:
        checked_columns(dutch, {"item_code": "Artikelnummer", "price": "Nettoprijs"})
    assert (twice.value.reason, twice.value.column, twice.value.row) == ("double_column", "Artikelnummer", 2)
    assert refusal_text(twice.value, "lijst.xlsx", "nl") == NL["exact_export_refused:double_column"].format(column="\"Artikelnummer\"", row=2)
    checked_columns(dutch, {"item_code": "Per", "price": "Nettoprijs"})
    result = preview(dutch, "S01", CURRENT, {"item_code": "Per", "price": "Nettoprijs"}, NOW)
    assert plan(result) == [["1", "unknown article"]]


def test_a_file_the_tab_refuses_is_named_by_a_sentence(tmp_path):
    """Amendment 4: each refusal of an export of articles, and of a price list, is a sentence in the page's language that names the file
    as it is given and never what a cell holds."""
    good = articles(tmp_path / "artikelen.csv", [["KM-1001", "Slang", "12,50"]])
    for name, data, reason in (("artikelen.xls", good.read_bytes(), "type"), ("artikelen.pdf", b"%PDF-1.4", "type"), ("leeg.xlsx", b"", "empty"),
                               ("leeg.csv", b"", "empty")):
        path = tmp_path / name
        path.write_bytes(data)
        for check in (checked_export, list_titles):
            with pytest.raises(ListRefused) as refused:
                check(path)
            assert refused.value.reason == reason and name not in str(refused.value), name
    checked_export(good)
    type_error, empty = ListRefused("type"), ListRefused("empty")
    assert refusal_text(type_error, "artikelen.xls", "nl") == "artikelen.xls is niet aangenomen: de assistent leest hier Excel (.xlsx) en csv."
    assert refusal_text(type_error, "artikelen.xls", "en") == "artikelen.xls was not accepted: the assistant reads Excel (.xlsx) and CSV here."
    assert refusal_text(empty, "leeg.csv", "nl") == NL["files_refused:empty"].format(name="leeg.csv") == "leeg.csv is niet aangenomen: het bestand is leeg."
    assert refusal_text(empty, "leeg.csv", "en") == "leeg.csv was not accepted: the file is empty."
    differ = "De kolommen van dit bestand zijn anders dan ingesteld: controleer Instellingen, Koppeling met Exact, deel 3."
    for reason in ("map", "no_column", "double_column"):
        assert refusal_text(ExportRefused("made-up", reason, column="Prijs", row=3), "a.csv", "nl") == differ, reason
    assert refusal_text(ExportRefused("made-up", "no_column"), "a.csv", "en") == (
        "The columns of this file differ from those set: check Settings, Connection to Exact, part 3.")
    assert refusal_text(ExportRefused("made-up", "file_type"), "a.xls", "nl") == refusal_text(type_error, "a.xls", "nl")
    assert refusal_text(ExportRefused("made-up", "unreadable"), "a.xlsx", "nl") == NL["exact_export_refused:unreadable"]
    assert refusal_text(ExportRefused("made-up", "delimiter_or_encoding"), "a.csv", "en") == EN["exact_export_refused:delimiter_or_encoding"]
    # a price list that cannot be read, and one whose rows are all blank
    damaged = tmp_path / "kapot.xlsx"
    damaged.write_bytes(b"no workbook")
    with pytest.raises(ListRefused) as unreadable:
        list_titles(damaged)
    assert unreadable.value.reason == "unreadable" and refusal_text(unreadable.value, "kapot.xlsx", "nl") == NL["exact_export_refused:unreadable"]
    blank = tmp_path / "blanco.csv"
    blank.write_text(";;\n;;\n", encoding="utf-8")
    with pytest.raises(ListRefused) as nothing:
        list_titles(blank)
    assert nothing.value.reason == "empty"
    with pytest.raises(OSError):
        list_titles(tmp_path / "weg.csv")


def test_the_lines_of_the_stored_prices_count_one_in_the_singular(tmp_path):
    """Amendment 4 (d) and (e): what the tab says of the supplier chosen and of an export just stored, a count of one in the singular."""
    root = tmp_path / "cases"
    store_current(root, articles(tmp_path / "artikelen.xlsx", [["KM-1001", "Slang", 12.5], ["KM-1002", "Klem", 8]]), ARTICLE_COLUMNS, False,
                  STORED, "S01")
    stored = stored_prices(root)["S01"]
    assert state_line("S01", stored, "nl") == "Inkoopprijzen van S01 opgenomen op 2026-11-20 09:15 uit artikelen.xlsx: 2 artikelen."
    assert state_line("S01", stored, "en") == "Purchase prices of S01 filed on 2026-11-20 09:15 from artikelen.xlsx: 2 articles."
    assert state_line("S01", stored._replace(prices={"KM-1001": "12.5"}), "nl").endswith(": 1 artikel.")
    assert state_line("S02", None, "nl") == ("Van S02 zijn nog geen inkoopprijzen opgenomen. Geef eerst de export van de artikelen van deze "
                                             "leverancier uit Exact.")
    assert state_line("S02", None, "en") == "No purchase prices of S02 are filed yet. First give the export of this supplier's articles from Exact."
    assert (state_line("", None, "nl"), state_line("", stored, "en")) == ("Kies eerst een leverancier.", "Choose a supplier first.")
    assert filed_line(["S01"], 12, 3, "nl") == "Inkoopprijzen van S01 opgenomen: 12 artikelen; 3 rijen overgeslagen."
    assert filed_line(["S01"], 1, 1, "nl") == "Inkoopprijzen van S01 opgenomen: 1 artikel; 1 rij overgeslagen."
    assert filed_line(["S01"], 12, 0, "en") == "Purchase prices of S01 filed: 12 articles; 0 rows passed over."
    assert filed_line(["S01", "S03"], 12, 3, "nl") == "Inkoopprijzen opgenomen van 2 leveranciers: 12 artikelen; 3 rijen overgeslagen."
    assert filed_line(["S01", "S03"], 1, 1, "en") == "Purchase prices filed of 2 suppliers: 1 article; 1 row passed over."
    assert filed_line([], 0, 2, "nl") == "Inkoopprijzen opgenomen van 0 leveranciers: 0 artikelen; 2 rijen overgeslagen."


# --- the tab on the page, through its handlers by name ---

import os  # noqa: E402
import shutil  # noqa: E402

from gradio.data_classes import FileData  # noqa: E402

from aid.review.page import import_gradio  # noqa: E402
from aid.settings import load_settings  # noqa: E402
from aid.tests import test_hand_tab as hand  # noqa: E402
from aid.tests.test_hand_tab import (ARTICLE_COLUMNS as SAVED_COLUMNS, CARRIED_OUT, COLUMN_FIELDS, LIST_CHOSEN, OWN, PRICELIST_HANDLERS,  # noqa: E402
                                     PRICELIST_OPENED, PRICELIST_RELABEL, PRICELIST_SHOWN, PRICES_FILED, PRICES_REMOVED, SAME, SUPPLIER_CHOSEN,
                                     articles_export, called, handler, inside, price_list, pricelist_page, pricelist_tab, tab_page, uploaded)

assert SAVED_COLUMNS == ARTICLE_COLUMNS
INACTIVE, ACTIVE = {"__type__": "update", "interactive": False}, {"__type__": "update", "interactive": True}
SHOWN_PART, HIDDEN_PART = {"__type__": "update", "visible": True}, {"__type__": "update", "visible": False}
CHANGED = "De gegevens zijn veranderd sinds u de lijst bekeek. Bekijk de lijst opnieuw."
S01_ROWS = [["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", "8,00"]]
LISTED = [("KM-1001", "12,70", "", "", ""), ("KM-1002", "8,40", "", "", "")]


def filed(page, tmp_path: Path, supplier: str | None, name: str = "artikelen.csv", rows: list[list] = S01_ROWS, language: str = "nl",
          titles: tuple = ("Artikel", "Omschrijving", "Inkoopprijs")) -> dict:
    """A made-up export of articles given on the tab for the supplier: what "Inkoopprijzen opnemen" gives."""
    return called(page, "prices_filed", PRICES_FILED, articles_export(tmp_path, name, rows, titles), supplier, language, None)


def viewed(page, listed: str, supplier: str | None, columns: list | None = None, language: str = "nl") -> dict:
    return called(page, "pricelist_shown", PRICELIST_SHOWN, listed, supplier, *(OWN if columns is None else columns), language)


def carried(page, listed: str, supplier: str | None, view: dict | None, columns: list | None = None, language: str = "nl") -> dict:
    return called(page, "pricelist_carried_out", CARRIED_OUT, listed, supplier, *(OWN if columns is None else columns), view, language)


def test_two_suppliers_exports_one_after_the_other_keep_each_others_prices(tmp_path, madeup_out):
    """Amendment 1 on the page: the export of a second supplier leaves the first supplier's prices as they are, the dropdown offers both
    suppliers, and a newer export of the first replaces its own prices only."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    assert filed(page, tmp_path, "S01")["export_note"] == "Inkoopprijzen van S01 opgenomen: 2 artikelen; 0 rijen overgeslagen."
    clock.later(5)
    second = filed(page, tmp_path, "S02", "s02.xlsx", [["KM-2001", "Bout", 3.2], ["KM-2002", "Moer", "0,10"], ["KM-2003", "Pen", None]])
    assert second["export_note"] == "Inkoopprijzen van S02 opgenomen: 2 artikelen; 1 rij overgeslagen."
    assert (second["supplier"]["choices"], second["supplier"]["value"]) == (["S01", "S02"], "S02")
    assert second["state"] == "Inkoopprijzen van S02 opgenomen op 2026-11-20 09:20 uit s02\\.xlsx: 2 artikelen."
    assert current_prices(root, "S01") == ({"KM-1001": "12.50", "KM-1002": "8.00"}, "2026-11-20T09:15:00")       # as they were
    assert current_prices(root, "S02") == ({"KM-2001": "3.2", "KM-2002": "0.10"}, "2026-11-20T09:20:00")
    chosen = called(page, "pricelist_supplier_chosen", SUPPLIER_CHOSEN, "s01", None, "nl")
    assert chosen["state"] == "Inkoopprijzen van S01 opgenomen op 2026-11-20 09:15 uit artikelen\\.csv: 2 artikelen." and chosen["list_part"] == SHOWN_PART
    opened = called(page, "pricelist_opened", PRICELIST_OPENED, " s 01", "en")
    assert (opened["supplier"]["choices"], opened["supplier"]["value"]) == (["S01", "S02"], "S01")                # the stored name
    assert opened["state"] == "Purchase prices of S01 filed on 2026-11-20 09:15 from artikelen\\.csv: 2 articles."
    clock.later(5)
    newer = filed(page, tmp_path, " s01 ", "nieuw.csv", [["KM-1001", "Slang", "12,90"]], language="en")
    assert newer["export_note"] == "Purchase prices of S01 filed: 1 article; 0 rows passed over." and newer["supplier"]["value"] == "S01"
    assert current_prices(root, "S01") == ({"KM-1001": "12.90"}, "2026-11-20T09:25:00") and current_prices(root, "S02")[1] == "2026-11-20T09:20:00"
    assert list(stored_prices(root)) == ["S01", "S02"]


def test_each_refusal_of_the_export_is_a_sentence_and_stores_nothing(tmp_path, madeup_out):
    """Amendments 4 (e) and 6: the field takes any file, so that the page library refuses none in its own words, and the handler refuses
    in the page's language, the file's name shown as text and never as markup; nothing is stored. One press stores one export."""
    gr = import_gradio()
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    [field] = [block for block in inside(pricelist_tab(page)) if isinstance(block, gr.File) and block.elem_id == "pricelist-export"]
    assert field.file_types is None and field.type == "filepath"
    good = Path(articles_export(tmp_path, "artikelen.csv", S01_ROWS)).read_bytes()
    for name, data, dutch, english in (
            ("artikelen.xls", good, "artikelen\\.xls is niet aangenomen: de assistent leest hier Excel (.xlsx) en csv.",
             "artikelen\\.xls was not accepted: the assistant reads Excel (.xlsx) and CSV here."),
            ("# kop *x* <b>.pdf", b"%PDF-1.4", "\\# kop \\*x\\* \\<b\\>\\.pdf is niet aangenomen: de assistent leest hier Excel (.xlsx) en csv.",
             "\\# kop \\*x\\* \\<b\\>\\.pdf was not accepted: the assistant reads Excel (.xlsx) and CSV here."),
            ("leeg.xlsx", b"", "leeg\\.xlsx is niet aangenomen: het bestand is leeg.", "leeg\\.xlsx was not accepted: the file is empty."),
            ("prijzen.csv", b"Code;Prijs\nKM-1001;12,50\n",
             "De kolommen van dit bestand zijn anders dan ingesteld: controleer Instellingen, Koppeling met Exact, deel 3.",
             "The columns of this file differ from those set: check Settings, Connection to Exact, part 3."),
            ("kapot.xlsx", b"no workbook", NL["exact_export_refused:unreadable"], EN["exact_export_refused:unreadable"])):
        upload = uploaded(tmp_path, name, data)
        assert field.preprocess(FileData(path=upload)) == upload, name                      # the page library lets it through
        for language, sentence in (("nl", dutch), ("en", english)):
            refused = called(page, "prices_filed", PRICES_FILED, upload, "S01", language, None)
            assert refused["export_note"] == sentence and refused["export"] == {"__type__": "update", "value": None}, name
            assert refused["supplier"]["choices"] == [] and refused["list_part"] == HIDDEN_PART, name
    # without a supplier column the supplier must be chosen; without a file a file
    for supplier in (None, "", "   "):
        upload = uploaded(tmp_path, "artikelen.csv", good)
        assert called(page, "prices_filed", PRICES_FILED, upload, supplier, "nl", None)["export_note"] == (
            "Kies eerst de leverancier van wie deze artikelen zijn.")
        assert called(page, "prices_filed", PRICES_FILED, upload, supplier, "en", None)["export_note"] == (
            "First choose the supplier whose articles these are.")
    assert called(page, "prices_filed", PRICES_FILED, None, "S01", "nl", None)["export_note"] == "Kies eerst een bestand."
    assert called(page, "prices_filed", PRICES_FILED, None, "S01", "en", None)["export_note"] == "Choose a file first."
    assert not (root / "exact").exists()
    # the button is active while a file is chosen, and one press stores one export
    chosen = handler(page, "pricelist_export_chosen")
    assert (chosen(None), chosen(""), chosen(upload)) == (INACTIVE, INACTIVE, ACTIVE)
    first = called(page, "prices_filed", PRICES_FILED, upload, "S01", "nl", None)
    assert first["export_note"].startswith("Inkoopprijzen van S01 opgenomen: 2 artikelen") and first["given"] is not None
    stored = (root / "exact" / "article_prices.json").read_bytes()
    clock.later(3)
    nothing = dict.fromkeys(PRICES_FILED, SAME)
    assert called(page, "prices_filed", PRICES_FILED, upload, "S01", "nl", first["given"]) == nothing       # a double click
    assert called(page, "prices_filed", PRICES_FILED, None, "S01", "nl", first["given"]) == nothing         # the field emptied
    assert (root / "exact" / "article_prices.json").read_bytes() == stored
    status = Path(upload).stat()
    os.utime(upload, ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000_000))           # chosen again: a new upload
    assert called(page, "prices_filed", PRICES_FILED, upload, "S01", "nl", first["given"])["state"].startswith(
        "Inkoopprijzen van S01 opgenomen op 2026-11-20 09:18 uit")
    # a stored file that cannot be opened: the press says so by the class of the error, and nothing is written
    (root / "exact" / "article_prices.json").unlink()
    (root / "exact" / "article_prices.json").mkdir()
    failed = called(page, "prices_filed", PRICES_FILED, uploaded(tmp_path, "artikelen.csv", good), "S01", "nl", None)
    assert failed["export_note"] in [NL["settings_failed"].format(code=code) for code in ("IsADirectoryError", "PermissionError")]
    assert failed["notice"] == {"__type__": "update", "value": NL["pricelist_stored_unreadable"], "visible": True}


def test_an_export_with_a_supplier_column_needs_no_supplier_chosen(tmp_path, madeup_out):
    """Amendment 4 (e): with a supplier column no supplier needs to be chosen for the export, and the dropdown offers the suppliers it
    named afterwards; the note counts the suppliers, and names the supplier of an export of one."""
    page, d, clock = pricelist_page(tmp_path, madeup_out, article_columns=ARTICLE_COLUMNS | {"supplier": "Leverancier"})
    titles = ("Artikel", "Leverancier", "Inkoopprijs")
    several = filed(page, tmp_path, None, "alle.csv", [["KM-1001", "S01", "12,50"], ["KM-3001", "S03", "0,10"], ["KM-1002", "S01", "8,00"],
                                                       ["KM-9001", "", "1,00"]], titles=titles)
    assert several["export_note"] == "Inkoopprijzen opgenomen van 2 leveranciers: 3 artikelen; 1 rij overgeslagen."
    assert (several["supplier"]["choices"], several["supplier"]["value"]) == (["S01", "S03"], None)
    assert several["state"] == "Kies eerst een leverancier." and several["list_part"] == HIDDEN_PART
    clock.later(5)
    english = filed(page, tmp_path, "S03", "alle.csv", [["KM-1001", "S01", "12,50"], ["KM-3001", "S03", "0,11"]], language="en", titles=titles)
    assert english["export_note"] == "Purchase prices filed of 2 suppliers: 2 articles; 0 rows passed over."
    assert english["supplier"]["value"] == "S03" and english["list_part"] == SHOWN_PART         # the supplier chosen stays chosen
    clock.later(5)
    one = filed(page, tmp_path, "S03", "s04.csv", [["KM-4001", "S04", "7,00"]], titles=titles)   # an export of one supplier's articles
    assert one["export_note"] == "Inkoopprijzen van S04 opgenomen: 1 artikel; 0 rijen overgeslagen."
    assert (one["supplier"]["choices"], one["supplier"]["value"]) == (["S01", "S03", "S04"], "S04")
    assert one["state"] == "Inkoopprijzen van S04 opgenomen op 2026-11-20 09:25 uit s04\\.csv: 1 artikel."
    assert stored_prices(d.settings.cases_root)["S01"].prices == {"KM-1001": "12.50"}
    before = (d.settings.cases_root / "exact" / "article_prices.json").read_bytes()
    assert filed(page, tmp_path, None, "leeg.csv", [], titles=titles)["export_note"] == NL["pricelist_refused:no_article"]     # ruling Z11
    assert (d.settings.cases_root / "exact" / "article_prices.json").read_bytes() == before


def test_the_press_carries_out_the_view_or_nothing(tmp_path, madeup_out):
    """Rule 4 as amendment 7 makes it exact: the press carries out the view the page showed. When the list file, the supplier, the
    columns or the supplier's current prices changed since that view, it carries nothing out and says so; a view is carried out once."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    filed(page, tmp_path, "S01")
    filed(page, tmp_path, "S02", "s02.csv", [["KM-2001", "Bout", "11,90"], ["KM-2002", "Moer", "8,00"]])
    listed = price_list(tmp_path, "prijslijst.csv", LISTED)
    nothing_written = hand.files_of(root)

    def refused(view: dict, *, file: str = listed, supplier: str = "S01", columns: list | None = None, language: str = "nl") -> None:
        result = carried(page, file, supplier, view["viewed"], columns, language)
        assert result == {"table": SAME, "note": CHANGED if language == "nl" else "The data changed since you viewed the list. View the list again.",
                          "carry": INACTIVE, "viewed": None}
        assert hand.files_of(root) == nothing_written

    clock.later(10)
    view = viewed(page, listed, "S01")
    assert view["note"] == "2 prijzen veranderen, 0 blijven gelijk, 0 rijen overgeslagen." and view["carry"] == ACTIVE
    refused(view, supplier="S02")                                                           # another supplier
    refused(view, supplier="", language="en")
    refused(view, columns=["item_code", "price", "", "valid_from", "valid_to"])             # other columns
    refused(view, columns=["item_code", "", "", "", ""])
    other = price_list(tmp_path, "prijslijst.csv", LISTED)                                  # another upload of the same list
    refused(view, file=other)
    status = Path(listed).stat()
    os.utime(listed, ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000_000))           # the file written anew
    refused(view)
    os.utime(listed, ns=(status.st_atime_ns, status.st_mtime_ns))
    refused(view, file=None)
    # a newer export of the supplier, with the same prices and with others: the view is no longer what would be carried out
    clock.later(1)
    filed(page, tmp_path, "S01", rows=[["KM-1001", "Slang", "12,60"], ["KM-1002", "Klem", "8,00"]])
    nothing_written = hand.files_of(root)
    refused(view)
    view = viewed(page, listed, "S01")
    assert view["table"]["data"][0] == ["KM-1001", "12,60", "12,70", "prijs verandert"]
    # a mark on the list since the view: an article price entered for this supplier
    clock.later(1)
    first = carried(page, listed, "S01", view["viewed"])
    assert first == {"table": view["table"], "note": "2 prijswijzigingen staan op de lijst In Exact invoeren.", "carry": INACTIVE, "viewed": None}
    view = viewed(page, listed, "S01")                                                      # not entered yet: both change still
    clock.later(1)
    [change] = [change for change in open_changes(root / "prepared") if change.article_code == "KM-1002"]
    assert mark(root / "prepared", change.ids, "entered", clock()) == 1
    nothing_written = hand.files_of(root)
    refused(view)
    # a view is carried out once: the press gives back no view, and a press without one does nothing, its note included
    view = viewed(page, listed, "S01")
    assert view["table"]["data"] == [["KM-1001", "12,60", "12,70", "prijs verandert"], ["KM-1002", "8,40", "8,40", "ongewijzigd"]]
    done = carried(page, listed, "S01", view["viewed"])
    assert done == {"table": view["table"], "note": "1 prijswijziging staat op de lijst In Exact invoeren.", "carry": INACTIVE, "viewed": None}
    rows = len(hand.prepared_lines(root))
    assert carried(page, listed, "S01", done["viewed"]) == {"table": SAME, "note": SAME, "carry": INACTIVE, "viewed": None}
    assert len(hand.prepared_lines(root)) == rows == 3
    # a change of the list's file, a new choice of the supplier and a column chosen by the buyer each end the view on the page
    assert handler(page, "pricelist_column_chosen")() == (INACTIVE, None)
    chosen = called(page, "pricelist_file_chosen", LIST_CHOSEN, listed, "S01", "nl")
    assert (chosen["carry"], chosen["viewed"], chosen["note"], chosen["table"]["data"]) == (INACTIVE, None, "", [])
    again = called(page, "pricelist_supplier_chosen", SUPPLIER_CHOSEN, "S02", listed, "nl")
    assert (again["carry"], again["viewed"], again["table"]["data"]) == (INACTIVE, None, [])


def test_a_price_list_the_tab_cannot_view_is_named_by_a_sentence(tmp_path, madeup_out):
    """The price list's field takes any file; a list of another type, an empty list, a list that cannot be read, a column that is missing
    or chosen twice and a title that stands twice are each named by a sentence, the file's name shown as text; nothing is written."""
    gr = import_gradio()
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    filed(page, tmp_path, "S01")
    before = hand.files_of(root)
    [field] = [block for block in inside(pricelist_tab(page)) if isinstance(block, gr.File) and block.elem_id == "pricelist-list"]
    assert field.file_types is None
    emptied = [{"__type__": "update", "choices": [("(staat niet in het bestand)", "")], "value": ""}] * 5
    for name, data, dutch, english in (
            ("lijst.pdf", b"%PDF-1.4", "lijst\\.pdf is niet aangenomen: de assistent leest hier Excel (.xlsx) en csv.",
             "lijst\\.pdf was not accepted: the assistant reads Excel (.xlsx) and CSV here."),
            ("[x](y) lijst.xls", b"a;b", "\\[x\\]\\(y\\) lijst\\.xls is niet aangenomen: de assistent leest hier Excel (.xlsx) en csv.",
             "\\[x\\]\\(y\\) lijst\\.xls was not accepted: the assistant reads Excel (.xlsx) and CSV here."),
            ("leeg.csv", b"", "leeg\\.csv is niet aangenomen: het bestand is leeg.", "leeg\\.csv was not accepted: the file is empty."),
            ("kapot.xlsx", b"no workbook", NL["exact_export_refused:unreadable"], EN["exact_export_refused:unreadable"])):
        upload = uploaded(tmp_path, name, data)
        assert field.preprocess(FileData(path=upload)) == upload, name
        for language, sentence in (("nl", dutch), ("en", english)):
            chosen = called(page, "pricelist_file_chosen", LIST_CHOSEN, upload, "S01", language)
            assert chosen["note"] == sentence and chosen["carry"] == INACTIVE and chosen["viewed"] is None, name
            if language == "nl":
                assert [chosen[name] for name in COLUMN_FIELDS] == emptied
            view = viewed(page, upload, "S01", language=language)
            assert (view["note"], view["table"]["data"], view["carry"], view["viewed"]) == (sentence, [], INACTIVE, None), name
    gone = called(page, "pricelist_file_chosen", LIST_CHOSEN, None, "S01", "nl")
    assert [gone[name] for name in COLUMN_FIELDS] == emptied and gone["note"] == ""
    assert (viewed(page, None, "S01")["note"], viewed(page, None, "S01", language="en")["note"]) == ("Kies eerst een bestand.", "Choose a file first.")
    listed = price_list(tmp_path, "lijst.csv", [("KM-1001", "12,70", "12,80")], titles=("Code", "Prijs", "Prijs"))
    fields = called(page, "pricelist_file_chosen", LIST_CHOSEN, listed, "S01", "nl")
    assert [fields[name] for name in COLUMN_FIELDS] == [
        {"__type__": "update", "choices": [("(staat niet in het bestand)", ""), ("Code", "Code"), ("Prijs", "Prijs")], "value": ""}] * 5
    assert viewed(page, listed, "S01", ["", "", "", "", ""])["note"] == "Kies de kolom met de artikelcode. Kies de kolom met de prijs."
    assert viewed(page, listed, "S01", ["Code", None, "", "", ""], "en")["note"] == "Choose the column with the price."
    assert viewed(page, listed, "S01", ["Code", "Code", "", "", ""])["note"] == "De kolom \"Code\" is meer dan een keer gekozen."
    assert viewed(page, listed, "S01", ["Code", "Prijs", "", "", ""])["note"] == (
        NL["exact_export_refused:double_column"].format(column="\"Prijs\"", row=1))
    assert viewed(page, listed, "S01", ["Code", "Netto", "", "", ""])["note"] == NL["settings_failed"].format(code="ValueError")
    marked = price_list(tmp_path, "sterren.csv", [("KM-1001", "12,70", "12,80")], titles=("Code", "*Netto*", "*Netto*"))
    assert viewed(page, marked, "S01", ["Code", "*Netto*", "*Netto*", "", ""])["note"] == (
        "De kolom \"\\*Netto\\*\" is meer dan een keer gekozen.")                             # a title is shown as text, never as markup
    assert viewed(page, marked, "S01", ["Code", "*Netto*", "", "", ""])["note"] == (
        NL["exact_export_refused:double_column"].format(column="\"\\*Netto\\*\"", row=1))
    assert viewed(page, str(tmp_path / "weg.csv"), "S01")["note"] == NL["settings_failed"].format(code="FileNotFoundError")
    assert hand.files_of(root) == before
    # while the list In Exact invoeren cannot be read, no price is compared as if nothing had been entered
    good = price_list(tmp_path, "goed.csv", LISTED)
    (root / "prepared").mkdir()
    (root / "prepared" / "marks.jsonl").mkdir()
    blocked = viewed(page, good, "S01")
    assert blocked["note"] in [NL["settings_failed"].format(code=code) for code in ("IsADirectoryError", "PermissionError")]
    assert (blocked["table"]["data"], blocked["viewed"]) == ([], None)


def test_the_columns_of_a_list_are_kept_for_the_supplier_and_filled_again(tmp_path, madeup_out):
    """Rule 9 on the page, as ruling Z9 of the review round keeps it: carrying a list out keeps its columns for the supplier beside
    the stored article prices, and the settings file is never written; the next list of that supplier opens with them chosen;
    another supplier's list is filled from the titles that equal the aid's."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    path, root = tmp_path / "settings.json", d.settings.cases_root
    filed(page, tmp_path, "S01")
    filed(page, tmp_path, "S02", "s02.csv", [["KM-2001", "Bout", "3,20"]])
    titles = ("Artikelnummer", "Omschrijving", "Nettoprijs", "Per", "Ingangsdatum")
    listed = price_list(tmp_path, "lijst S01.xlsx", [("KM-1001", "Slang", 12.7, 1, "2026-11-01"), ("KM-1002", "Klem", 840, 100, None)], titles=titles)
    fields = called(page, "pricelist_file_chosen", LIST_CHOSEN, listed, "S01", "nl")
    offered = [("(staat niet in het bestand)", ""), *[(title, title) for title in titles]]
    assert [fields[name] for name in COLUMN_FIELDS] == [{"__type__": "update", "choices": offered, "value": ""}] * 5     # no title is the aid's
    columns = ["Artikelnummer", "Nettoprijs", "Per", "Ingangsdatum", ""]
    clock.later(10)
    view = viewed(page, listed, "S01", columns)
    assert view["table"]["data"] == [["KM-1001", "12,50", "12,7", "prijs verandert"],
                                     ["KM-1002", "8,00", "", "prijs per ander aantal: niet vergeleken"]]            # rule 6: per one only
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    assert carried(page, listed, "S01", view["viewed"], columns)["note"] == "1 prijswijziging staat op de lijst In Exact invoeren."
    kept = {"item_code": "Artikelnummer", "price": "Nettoprijs", "basis": "Per", "valid_from": "Ingangsdatum"}
    assert kept_columns(root) == {"S01": kept}
    assert json.loads((root / "exact" / "pricelist_columns.json").read_text(encoding="utf-8")) == {"suppliers": {"S01": kept}}
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before                           # the settings file: not a byte, not its time
    assert "pricelist_columns" not in path.read_text(encoding="utf-8") and not hasattr(load_settings(path), "pricelist_columns")
    # the next list of the supplier, whose titles differ in capitals: the fields are filled from the kept map, as the file spells them
    newer = price_list(tmp_path, "lijst december.csv", [("KM-1001", "12,90", "1", "")], titles=("ARTIKELNUMMER", "nettoprijs", "Per", "Einddatum"))
    again = called(page, "pricelist_file_chosen", LIST_CHOSEN, newer, " s01 ", "nl")
    assert [again[name]["value"] for name in COLUMN_FIELDS] == ["ARTIKELNUMMER", "nettoprijs", "Per", "", ""]
    assert called(page, "pricelist_supplier_chosen", SUPPLIER_CHOSEN, "S01", newer, "nl")["column:price"]["value"] == "nettoprijs"
    # another supplier has no kept map: its fields are filled from the titles that equal the aid's own
    other = called(page, "pricelist_supplier_chosen", SUPPLIER_CHOSEN, "S02", newer, "nl")
    assert [other[name]["value"] for name in COLUMN_FIELDS] == [""] * 5
    own = called(page, "pricelist_file_chosen", LIST_CHOSEN, price_list(tmp_path, "eigen.csv", LISTED), "S02", "nl")
    assert [own[name]["value"] for name in COLUMN_FIELDS] == OWN
    assert [own[name]["value"] for name in COLUMN_FIELDS] != [called(page, "pricelist_file_chosen", LIST_CHOSEN, price_list(
        tmp_path, "eigen.csv", LISTED), "S01", "nl")[name]["value"] for name in COLUMN_FIELDS]                     # S01's kept map decides


def test_an_article_price_of_a_case_counts_for_the_supplier_of_that_case(tmp_path, madeup_out):
    """Amendment 2 on the page: the article price that the confirmation of PO 250108 asked counts, once it is ticked as entered, for
    the supplier of that case (S08) and for no other supplier that holds the article; in the test mode a price list is handled as any
    other and its rows are no try-out rows (amendment 9). Ruling Z3 of the review round: once a second supplier's stored prices hold
    the article too, no row of a price list for it is put on the list, for either supplier, and the tick of the case stays S08's."""
    page, d, clock = pricelist_page(tmp_path, madeup_out, cases=("MU08",), test_mode=True)
    root = d.settings.cases_root
    filed(page, tmp_path, "S08", "s08.csv", [["KM-0801", "Koppeling", "12,50"]])
    listed = price_list(tmp_path, "lijst.csv", [("KM-0801", "12,70", "", "", "")])
    assert viewed(page, listed, "S08")["table"]["data"] == [["KM-0801", "12,50", "12,70", "prijs verandert"]]
    filed(page, tmp_path, "S14", "s14.csv", [["KM-0801", "Koppeling", "12,50"], ["KM-1401", "Flens", "3,00"]])
    assert viewed(page, listed, "S08")["table"]["data"] == [["KM-0801", "12,50", "12,70", shared_nl("S14")]]
    clock.later(5)
    [article] = [change for change in open_changes(root / "prepared") if change.kind == "article_price"]
    assert article.old_value is None and mark(root / "prepared", article.ids, "entered", clock()) == 1
    assert viewed(page, listed, "S08")["table"]["data"] == [["KM-0801", "12,70", "12,70", "ongewijzigd"]]
    assert current_prices(root, "S14")[0]["KM-0801"] == "12.50"                                  # the case names S08, not S14
    both = price_list(tmp_path, "lijst s14.csv", [("KM-0801", "12,70", "", "", ""), ("KM-1401", "3,10", "", "", "")])
    view = viewed(page, both, "S14")
    assert view["table"]["data"] == [["KM-0801", "12,50", "12,70", shared_nl("S08")], ["KM-1401", "3,00", "3,10", "prijs verandert"]]
    assert view["note"] == "1 prijs verandert, 0 blijven gelijk, 1 rij overgeslagen."
    rows = len(hand.prepared_lines(root))
    done = carried(page, both, "S14", view["viewed"])
    assert done["note"] == "1 prijswijziging staat op de lijst In Exact invoeren." and done["table"] == view["table"]
    assert len(hand.prepared_lines(root)) == rows + 1                                           # the shared article is not prepared
    texts = [text for text, _ in hand.shown(page, "list_opened", "nl")["rows"]["choices"] if text.startswith("artikel")]
    assert texts == ["artikel KM-1401: inkoopprijs 3,00 → 3,10"]                                 # and a price list is no try-out
    assert current_prices(root, "S08")[0] == {"KM-0801": "12.70"} and current_prices(root, "S14")[0]["KM-0801"] == "12.50"


def test_the_tab_offers_nothing_on_a_share_link(tmp_path, madeup_out):
    """Rule 11: on a public share link and without a settings file the tab says only that it is not available, as the settings tab
    does, and none of its handlers is on the page."""
    for share, file, key in ((True, True, "settings_share"), (False, False, "settings_no_file")):
        page, prepared, d = tab_page(tmp_path / key, madeup_out, cases=(), share=share, file=file, article_columns=ARTICLE_COLUMNS)
        tab = pricelist_tab(page)
        assert [type(child).__name__ for child in tab.children] == ["Markdown"] and tab.children[0].value == NL[key]
        assert not {getattr(f.fn, "__name__", "") for f in page.fns.values()} & (PRICELIST_HANDLERS | {"pricelist_entered", "pricelists_opened"})
        [relabel] = [f.fn for f in page.fns.values() if tab in f.outputs]
        assert relabel("en") == (EN[key], {"__type__": "update", "label": "Price list"}), key
    own = tab_page(tmp_path / "own", madeup_out, cases=())[0]                               # the page of this PC has them all
    assert PRICELIST_HANDLERS <= {getattr(f.fn, "__name__", "") for f in own.fns.values()}


def test_the_tab_is_wired_as_the_page_shows_it(tmp_path, madeup_out):
    """The tab stands between 'In Exact invoeren' and 'Bestanden' (amendment 4), with fixed controls only, top to bottom: the notice;
    the introduction, the supplier with the press that takes its stored prices away beside it (ruling Z19), its state, the export
    with its button and note; and the price list with its five column fields,
    'Bekijken', the table, the note and the press. Each handler is one of the page's functions with its trigger, inputs and outputs,
    and the language switch relabels everything."""
    gr = import_gradio()
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    tab = pricelist_tab(page)
    language = [block for block in page.blocks.values() if isinstance(block, gr.Radio) and block.label == NL["language"]][0]
    assert [(child.label, child.id) for child in tab.parent.children] == [
        ("Beoordelen", "beoordelen"), ("In Exact invoeren", "invoeren"), ("Prijslijst", "prijslijst"), ("Bestanden", "bestanden"),
        ("Instellingen", "instellingen")]
    given, kept = [block for block in inside(tab) if isinstance(block, gr.State)]
    by_id = {block.elem_id: block for block in inside(tab) if getattr(block, "elem_id", None)}

    def name(component) -> str:
        if component is tab:
            return "tab"
        if component is language:
            return "language"
        if component is given:
            return "given"
        if component is kept:
            return "viewed"
        return component.elem_id

    def bound(fn_name: str) -> tuple:
        [f] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == fn_name]
        return (sorted((name(page.blocks[block]) if block != page._id else "page", event) for block, event in f.targets),
                [name(component) for component in f.inputs], [name(component) for component in f.outputs])

    columns = [f"pricelist-column-{column}" for column in LIST_COLUMNS]
    order = [name(block) for block in inside(tab) if isinstance(block, (gr.Markdown, gr.Button, gr.File, gr.Dataframe, gr.Dropdown))]
    assert order == ["pricelist-notice", "pricelist-intro", "pricelist-supplier", "pricelist-remove", "pricelist-state", "pricelist-export", "pricelist-file-prices",
                     "pricelist-export-note", "pricelist-list", *columns, "pricelist-columns-hint", "pricelist-view", "pricelist-table",
                     "pricelist-note", "pricelist-carry-out"]
    assert bound("pricelist_opened") == ([("page", "load"), ("tab", "select")], ["pricelist-supplier", "language"],
                                         ["pricelist-notice", "pricelist-body", "pricelist-supplier", "pricelist-state", "pricelist-list-part"])
    after_view = ["pricelist-table", "pricelist-note", "pricelist-carry-out", "viewed"]
    assert bound("pricelist_supplier_chosen") == ([("pricelist-supplier", "change")], ["pricelist-supplier", "pricelist-list", "language"],
                                                  ["pricelist-state", "pricelist-list-part", *columns, *after_view])
    assert bound("pricelist_export_chosen") == ([("pricelist-export", "change")], ["pricelist-export"], ["pricelist-file-prices"])
    assert bound("prices_filed") == ([("pricelist-file-prices", "click")], ["pricelist-export", "pricelist-supplier", "language", "given"],
                                     ["pricelist-export-note", "pricelist-export", "pricelist-supplier", "pricelist-state", "pricelist-list-part",
                                      "given", "pricelist-notice"])
    assert bound("prices_removed") == ([("pricelist-remove", "click")], ["pricelist-supplier", "language"],
                                       ["pricelist-export-note", "pricelist-supplier", "pricelist-state", "pricelist-list-part", *after_view,
                                        "pricelist-notice"])
    beside = by_id["pricelist-remove"].parent                                         # one row holds the supplier's field and the press
    assert isinstance(beside, gr.Row) and by_id["pricelist-supplier"] in inside(beside) and by_id["pricelist-state"] not in inside(beside)
    assert bound("pricelist_file_chosen") == ([("pricelist-list", "change")], ["pricelist-list", "pricelist-supplier", "language"],
                                              [*columns, *after_view])
    assert bound("pricelist_column_chosen") == (sorted((column, "input") for column in columns), [], ["pricelist-carry-out", "viewed"])
    assert bound("pricelist_shown") == ([("pricelist-view", "click")], ["pricelist-list", "pricelist-supplier", *columns, "language"], after_view)
    assert bound("pricelist_carried_out") == ([("pricelist-carry-out", "click")],
                                              ["pricelist-list", "pricelist-supplier", *columns, "viewed", "language"], after_view)
    assert bound("pricelist_relabel") == ([("language", "change")], ["language", "pricelist-supplier", "pricelist-list"],
                                          ["tab", "pricelist-notice", "pricelist-intro", "pricelist-supplier", "pricelist-remove", "pricelist-state", "pricelist-export",
                                           "pricelist-file-prices", "pricelist-export-note", "pricelist-list", *columns, "pricelist-columns-hint",
                                           "pricelist-view", "pricelist-table", "pricelist-note", "pricelist-carry-out", "viewed"])
    # as the page is built: nothing shown but the notice's place, the two presses inactive, and a name typed is taken
    assert (by_id["pricelist-body"].visible, by_id["pricelist-list-part"].visible, by_id["pricelist-notice"].visible) == (False, False, False)
    assert by_id["pricelist-file-prices"].interactive is False and by_id["pricelist-carry-out"].interactive is False
    supplier = by_id["pricelist-supplier"]
    assert (supplier.label, supplier.info, supplier.allow_custom_value, supplier.choices) == (
        "Leverancier", "Kies de leverancier, of typ de naam bij een nieuwe leverancier.", True, [])
    assert (by_id["pricelist-export"].label, by_id["pricelist-list"].label) == ("Export van de artikelen uit Exact (Excel of csv)",
                                                                                "Prijslijst (Excel of csv)")
    assert (by_id["pricelist-file-prices"].value, by_id["pricelist-view"].value, by_id["pricelist-carry-out"].value) == (
        "Inkoopprijzen opnemen", "Bekijken", "Op de lijst In Exact invoeren zetten")
    assert by_id["pricelist-remove"].value == "Inkoopprijzen van deze leverancier weghalen"
    assert [by_id[column].label for column in columns] == ["Kolom met de artikelcode", "Kolom met de prijs", "Kolom met de prijs per (mag leeg)",
                                                          "Geldig vanaf (mag leeg)", "Geldig tot (mag leeg)"]
    assert by_id["pricelist-table"].interactive is False and by_id["pricelist-intro"].value == NL["pricelist_intro"]
    assert by_id["pricelist-columns-hint"].value == NL["pricelist_columns_hint"] and by_id["pricelist-columns-hint"].parent is by_id["pricelist-list-part"]
    assert all(by_id[key].latex_delimiters == [] for key in ("pricelist-state", "pricelist-export-note", "pricelist-note"))
    assert (given.value, kept.value) == (None, None)
    # the tab in English, with the supplier and the list kept
    filed(page, tmp_path, "S01")
    listed = price_list(tmp_path, "prijslijst.csv", LISTED)
    english = called(page, "pricelist_relabel", PRICELIST_RELABEL, "en", "S01", listed)
    assert english["tab"] == {"__type__": "update", "label": "Price list"} and english["notice"] == {"__type__": "update", "value": "", "visible": False}
    assert english["intro"] == EN["pricelist_intro"] and english["state"].startswith("Purchase prices of S01 filed on 2026-11-20 09:15 from")
    assert english["supplier"] == {"__type__": "update", "label": "Supplier", "info": "Choose the supplier, or type the name for a new supplier."}
    assert (english["export"]["label"], english["list"]["label"]) == ("Export of the articles from Exact (Excel or CSV)", "Price list (Excel or CSV)")
    assert (english["file_button"]["value"], english["view"]["value"]) == ("File purchase prices", "View")
    assert english["remove"] == {"__type__": "update", "value": "Remove this supplier's purchase prices"}
    assert english["carry"] == {"__type__": "update", "value": "Put on the list Enter in Exact", "interactive": False}
    assert [english[name]["label"] for name in COLUMN_FIELDS] == ["Column with the item code", "Column with the price",
                                                                 "Column with the price per (may be empty)", "Valid from (may be empty)",
                                                                 "Valid to (may be empty)"]
    assert english["column:price"]["choices"] == [("(not in the file)", ""), *[(title, title) for title in OWN]] and "value" not in english["column:price"]
    assert (english["export_note"], english["note"], english["viewed"]) == ("", "", None)
    assert english["hint"] == ("If the list has a column for what a price is per (per 100, per box), point it out: otherwise the assistant "
                               "reads every price as a price per piece.")
    assert english["table"] == {"headers": ["Article", "Purchase price in Exact", "Price on the list", "Outcome"], "data": []}
    unset = tab_page(tmp_path / "unset", madeup_out, cases=())[0]
    assert called(unset, "pricelist_relabel", PRICELIST_RELABEL, "en", None, None)["notice"] == {
        "__type__": "update", "value": EN["pricelist_unset"], "visible": True}


# --- the review round of the tab (rulings Z1 to Z12) ---

from openpyxl import Workbook  # noqa: E402

from aid.execution.by_hand import MARKS_FILE, changes  # noqa: E402
from aid.execution.ops import WriteOp  # noqa: E402
from aid.execution.writer import PreparedWriter, WriterError  # noqa: E402
from aid.pricelist import also_held, import_price_list, keep_columns  # noqa: E402
from aid.review.page import _literal  # noqa: E402
from aid.review.pricelist_page import BY, DIFFERS, FACTOR, SHARED, ask_again, held_back, list_table, not_prepared_line  # noqa: E402

SHARED_NL = "ook bij leverancier {name}: voer deze prijs zelf in Exact in"          # ruling Z19 (a): the outcome names the other supplier
DIFFERS_NL = "wijkt sterk af: controleer de kolom \"prijs per\""
DIFFERS_LINE = ("Een prijs die meer dan drie keer zo hoog of zo laag is, zet de assistent niet op de lijst: controleer de lijst, en voer een "
                "echte wijziging zelf in Exact in.")
NOTHING_LISTED = "0 prijswijzigingen staan op de lijst In Exact invoeren."
ONE_LISTED = "1 prijswijziging staat op de lijst In Exact invoeren."
TITLES = ["Artikel", "Omschrijving", "Inkoopprijs"]


def shared_nl(names: str) -> str:
    """The outcome of a row kept off the list because another supplier holds its article too, with the names as the tab gives them."""
    return SHARED_NL.format(name=names)


def sheets_file(tmp_path: Path, name: str, sheets: list[tuple[str, list[list], str]]) -> str:
    """A made-up workbook as the page library hands an upload over, with these sheets in this order: (the name of the sheet, its
    rows, and whether it is "visible", "hidden" or "veryHidden"), each with a wrong dimension record (ruling Z24: the sheet the
    buyer sees is read in full whatever that record says)."""
    book = Workbook()
    book.remove(book.active)
    for title, rows, state in sheets:
        sheet = book.create_sheet(title)
        sheet.sheet_state = state
        for row in rows:
            sheet.append(row)
    made = tmp_path / "made" / name
    made.parent.mkdir(parents=True, exist_ok=True)
    book.save(made)
    return uploaded(tmp_path, name, wrong_dimensions(made).read_bytes())


def rows_of(page) -> list[str]:
    """The rows of the list In Exact invoeren as the tab shows them."""
    return [text for text, _ in hand.shown(page, "list_opened", "nl")["rows"]["choices"]]


def lists_offered(page) -> dict:
    """What the row of the price lists on the tab In Exact invoeren offers."""
    opened = [f.fn for f in page.fns.values() if getattr(f.fn, "__name__", "") == "pricelists_opened"][0]
    return dict(zip(("row", "choice", "button", "kept"), opened("nl", None), strict=True))


def tick_all(page, clock) -> str:
    """Every row of the list ticked as entered, a minute later: the note of the press."""
    clock.later(1)
    return hand.shown(page, "changes_entered", [value for _, value in hand.shown(page, "list_opened", "nl")["rows"]["choices"]], "nl")["note"]


def test_only_the_sheet_the_buyer_sees_is_read(tmp_path, madeup_out):
    """Ruling Z1, for the export of the articles and for the price list: a hidden sheet is never read; the one visible sheet that
    holds data is read, whatever its place; a workbook with more than one such sheet is refused by the names of those sheets, shown as
    text, and nothing is stored or compared."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    export = sheets_file(tmp_path, "artikelen.xlsx", [("2025 (oud)", [TITLES, ["KM-1001", "Slang", 9.99]], "hidden"), ("Leeg", [], "visible"),
                                                      ("Artikelen", [TITLES, ["KM-1001", "Slang", 12.5], ["KM-1002", "Klem", 8]], "visible"),
                                                      ("Diep", [TITLES, ["KM-1001", "Slang", 1.11]], "veryHidden")])
    assert called(page, "prices_filed", PRICES_FILED, export, "S01", "nl", None)["export_note"] == (
        "Inkoopprijzen van S01 opgenomen: 2 artikelen; 0 rijen overgeslagen.")
    assert stored_prices(root)["S01"].prices == {"KM-1001": "12.5", "KM-1002": "8"}
    stored = hand.files_of(root)
    two = sheets_file(tmp_path, "twee.xlsx", [("Slangen", [TITLES, ["KM-1001", "Slang", 12.7]], "visible"), ("oud", [TITLES], "hidden"),
                                              ("_Klemmen_ <i>", [TITLES, ["KM-1002", "Klem", 8.4]], "visible")])
    marked = _literal("_Klemmen_ <i>")                        # a sheet's name is shown as text, never as markup
    assert marked == "\\_Klemmen\\_ \\<i\\>"
    for language, sentence in (
            ("nl", f"Dit Excel-bestand heeft meer dan één werkblad met gegevens (Slangen, {marked}). De assistent leest één werkblad: sla het "
                   "werkblad dat u bedoelt op als een eigen bestand en geef dat."),
            ("en", f"This Excel file has more than one sheet with data (Slangen, {marked}). The assistant reads one sheet: save the sheet you "
                   "mean as a file of its own and give that.")):
        for supplier in ("S01", "S02"):
            assert called(page, "prices_filed", PRICES_FILED, two, supplier, language, None)["export_note"] == sentence
    assert hand.files_of(root) == stored
    # the price list: the hidden first sheet is not compared in the place of the sheet the buyer sees
    head = ["item_code", "price"]
    listed = sheets_file(tmp_path, "lijst.xlsx", [("2025 (oud)", [head, ["KM-1001", 11.11], ["KM-1002", 7.77]], "hidden"),
                                                  ("2027", [head, ["KM-1001", 12.7]], "visible")])
    fields = called(page, "pricelist_file_chosen", LIST_CHOSEN, listed, "S01", "nl")
    assert fields["note"] == "" and [fields[name]["value"] for name in COLUMN_FIELDS] == ["item_code", "price", "", "", ""]
    view = viewed(page, listed, "S01", ["item_code", "price", "", "", ""])
    assert view["table"]["data"] == [["KM-1001", "12,5", "12,7", "prijs verandert"]]
    assert view["note"] == "1 prijs verandert, 0 blijven gelijk, 0 rijen overgeslagen."
    # two sheets with rows: no row of either is compared, at the choice of the file and at the view, and the press has nothing
    both = sheets_file(tmp_path, "lijsten.xlsx", [("Slangen", [head, ["KM-1001", 12.7]], "visible"),
                                                  ("_Klemmen_ <i>", [head, ["KM-1002", 8.4]], "visible")])
    chosen = called(page, "pricelist_file_chosen", LIST_CHOSEN, both, "S01", "nl")
    assert chosen["note"] == NL["exact_export_refused:several_sheets"].format(names=f"Slangen, {marked}") and chosen["column:price"]["value"] == ""
    english = viewed(page, both, "S01", ["item_code", "price", "", "", ""], "en")
    assert (english["note"], english["table"]["data"], english["carry"], english["viewed"]) == (
        EN["exact_export_refused:several_sheets"].format(names=f"Slangen, {marked}"), [], INACTIVE, None)
    assert hand.files_of(root) == stored


def test_the_command_reads_the_sheet_the_buyer_sees_as_the_tab_does(tmp_path):
    """Rulings Z1 and Z17: the tab and, since the second review round, the command of Task 23 read the one visible sheet of a
    workbook that holds data, never a hidden one. list_table refuses a workbook with more than one visible sheet with data by their
    names, and a workbook without a visible sheet with data has no row."""
    head = ["item_code", "price"]
    listed = Path(sheets_file(tmp_path, "lijst.xlsx", [("oud", [head, ["KM-1001", 11.11]], "hidden"), ("nieuw", [head, ["KM-1001", 12.7]], "visible")]))
    command = import_price_list(listed, "S01", CURRENT, PreparedWriter(tmp_path / "command"), NOW)
    assert [(entry.previous, entry.new) for entry in command.updated] == [("12.50", "12.7")]
    assert plan(preview(listed, "S01", CURRENT, {}, NOW)) == [["KM-1001", "12.50", "12.7"]]
    assert list_table(listed) == [["item_code", "price"], ["KM-1001", "12.7"]]
    two = Path(sheets_file(tmp_path, "twee.xlsx", [("A", [head], "visible"), ("B", [["  "], [None, "x"]], "visible"), ("C", [[None], ["  "]], "visible")]))
    with pytest.raises(ListRefused) as several:
        list_table(two)
    assert (several.value.reason, several.value.names) == ("sheets", ["A", "B"]) and "A" not in str(several.value)
    with pytest.raises(ListRefused):
        list_titles(two)
    with pytest.raises(ValueError):
        preview(two, "S01", CURRENT, {}, NOW)
    none = Path(sheets_file(tmp_path, "geen.xlsx", [("verborgen", [head, ["KM-1001", 12.7]], "hidden"), ("leeg", [[None, " "]], "visible")]))
    for read in (list_table, list_titles):                    # ruling Z33: only a hidden sheet holds data, and the refusal says so
        with pytest.raises(ListRefused) as nothing:
            read(none)
        assert nothing.value.reason == "hidden"
    bare = Path(sheets_file(tmp_path, "kaal.xlsx", [("verborgen", [], "hidden"), ("leeg", [[None, " "]], "visible")]))
    assert list_table(bare) == []
    with pytest.raises(ListRefused) as nothing:
        list_titles(bare)
    assert nothing.value.reason == "empty"


def test_an_article_that_two_suppliers_share_is_not_put_on_the_list(tmp_path, madeup_out):
    """Ruling Z3, the seven runs of the review's probe: a row of a price list whose article the stored prices of another supplier
    hold too is shown with an outcome of its own and its price, counts among the rows passed over, and is left out by the press. So
    no change of one supplier takes another's off the list, and no tick counts for a supplier that did not ask it."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    runs = ([("A", "12,70", False), ("B", "12,10", False)], [("B", "12,10", False), ("A", "12,70", False)],
            [("A", "12,70", True), ("B", "12,10", True)], [("B", "12,10", True), ("A", "12,70", True)],
            [("A", "12,70", True), ("B", "12,70", True)], [("A", "12,70", False), ("B", "12,70", True)],
            [("A", "12,70", False), ("B", "12,10", False), ("A", "12,70", False)])
    held = {"A": "12,50", "B": "11,90"}
    for number, steps in enumerate(runs, 1):
        shutil.rmtree(root, ignore_errors=True)
        filed(page, tmp_path, "A", "a.csv", [["KM-1001", "Slang", "12,50"], ["KM-1009", "Eigen", "1,00"]])
        filed(page, tmp_path, "B", "b.csv", [["KM-1001", "Slang", "11,90"]])
        for supplier, price, tick in steps:
            clock.later(10)
            listed = price_list(tmp_path, f"lijst {supplier}.csv", [("KM-1001", price, "", "", "")])
            view = viewed(page, listed, supplier)
            assert view["table"]["data"] == [["KM-1001", held[supplier], price, shared_nl("B" if supplier == "A" else "A")]], number
            assert view["note"] == "0 prijzen veranderen, 0 blijven gelijk, 1 rij overgeslagen." and view["carry"] == ACTIVE
            clock.later(1)
            done = carried(page, listed, supplier, view["viewed"])
            assert (done["note"], done["table"]) == (NOTHING_LISTED, view["table"]), number
            assert rows_of(page) == [] and not (root / "prepared").exists(), number
            if tick:
                assert tick_all(page, clock) == NL["hand_tick_first"]                              # nothing to tick: nothing is noted
        assert current_prices(root, "A")[0]["KM-1001"] == "12.50" and current_prices(root, "B")[0] == {"KM-1001": "11.90"}, number
        assert lists_offered(page)["choice"]["choices"] == [] and changes(root / "prepared")[0] == []
    english = viewed(page, listed, "A", language="en")
    assert english["table"]["data"] == [["KM-1001", "12.50", "12.70", "also with supplier B: enter this price in Exact yourself"]]
    assert english["note"] == "0 prices change, 0 stay the same, 1 row passed over."
    # an article of one supplier only goes on the list as before, beside a shared one that does not
    own = price_list(tmp_path, "eigen.csv", [("KM-1009", "1,10", "", "", ""), ("KM-1001", "12,50", "", "", ""), ("KM-1001", "12,70", "", "", "")])
    view = viewed(page, own, "A")
    assert view["table"]["data"] == [["KM-1009", "1,00", "1,10", "prijs verandert"], ["KM-1001", "12,50", "12,50", "ongewijzigd"],
                                     ["KM-1001", "12,50", "", "dubbele rij"]]          # an unchanged price is unchanged, whoever shares it
    assert carried(page, own, "A", view["viewed"])["note"] == ONE_LISTED and rows_of(page) == ["artikel KM-1009: inkoopprijs 1,00 → 1,10"]


def test_a_change_on_the_list_stays_when_another_supplier_comes_to_hold_its_article(tmp_path, madeup_out):
    """Ruling Z3: a change that stood on the list before a second supplier's export held its article stays there, and is its own
    supplier's alone: the second supplier's list cannot replace it or join it, a tick counts for the supplier that asked it, and a
    newer export of the other supplier between a view and a press ends the view."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    filed(page, tmp_path, "A", "a.csv", [["KM-1001", "Slang", "12,50"]])
    clock.later(10)
    first = price_list(tmp_path, "lijst A.csv", [("KM-1001", "12,70", "", "", "")])
    view = viewed(page, first, "A")
    assert view["table"]["data"] == [["KM-1001", "12,50", "12,70", "prijs verandert"]]
    clock.later(1)
    filed(page, tmp_path, "B", "b.csv", [["KM-1001", "Slang", "11,90"]])                      # between the view and the press
    assert carried(page, first, "A", view["viewed"])["note"] == CHANGED and not (root / "prepared").exists()
    shutil.rmtree(root)
    filed(page, tmp_path, "A", "a.csv", [["KM-1001", "Slang", "12,50"]])
    clock.later(10)
    view = viewed(page, first, "A")
    pressed = clock()
    assert carried(page, first, "A", view["viewed"])["note"] == ONE_LISTED
    clock.later(5)
    filed(page, tmp_path, "B", "b.csv", [["KM-1001", "Slang", "11,90"]])
    for price in ("12,10", "12,70"):                                                          # another value, and the same value
        clock.later(5)
        second = price_list(tmp_path, "lijst B.csv", [("KM-1001", price, "", "", "")])
        view = viewed(page, second, "B")
        assert view["table"]["data"] == [["KM-1001", "11,90", price, shared_nl("A")]]
        assert carried(page, second, "B", view["viewed"])["note"] == NOTHING_LISTED
        assert rows_of(page) == ["artikel KM-1001: inkoopprijs 12,50 → 12,70"]                # A's change, as it stood
    [change] = open_changes(root / "prepared")
    assert change.case_ids == [f"pricelist_A_{pressed:%Y%m%dT%H%M%S}"] and len(change.ids) == 1
    assert tick_all(page, clock) == "1 wijziging genoteerd als ingevoerd in Exact."
    assert current_prices(root, "A")[0] == {"KM-1001": "12.70"} and current_prices(root, "B")[0] == {"KM-1001": "11.90"}


def test_a_put_back_is_no_row_of_a_price_list(tmp_path, madeup_out):
    """Ruling Z4, the review's probe: the one press for a list's article prices takes the list's own open changes and never a row
    "Terugzetten", and a list is not offered for a put-back alone."""
    from aid.review.hand_list import price_lists
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    filed(page, tmp_path, "S01")
    clock.later(10)
    listed = price_list(tmp_path, "lijst.csv", LISTED)
    assert carried(page, listed, "S01", viewed(page, listed, "S01")["viewed"])["note"] == "2 prijswijzigingen staan op de lijst In Exact invoeren."
    clock.later(5)
    first = [value for text, value in hand.shown(page, "list_opened", "nl")["rows"]["choices"] if "KM-1001" in text]
    assert hand.shown(page, "changes_entered", first, "nl")["note"] == "1 wijziging genoteerd als ingevoerd in Exact."
    clock.later(5)
    under = hand.done_of(page, "done_opened", "nl", None)
    back = hand.done_of(page, "change_put_back", hand.chosen_of(under, "KM-1001"), "nl")
    assert "Terugzetten: artikel KM-1001: inkoopprijs 12,70 → 12,50" in back["done_note"]
    assert rows_of(page) == ["Terugzetten: artikel KM-1001: inkoopprijs 12,70 → 12,50", "artikel KM-1002: inkoopprijs 8,00 → 8,40"]
    offered = lists_offered(page)
    [(label, case_id)] = offered["choice"]["choices"]
    assert label == "S01 (20-11-2026 09:25)" and len(offered["kept"][case_id]) == 1               # the list's own open change only
    clock.later(5)
    assert hand.shown(page, "pricelist_entered", case_id, offered["kept"], "nl")["note"] == "1 wijziging genoteerd als ingevoerd in Exact."
    assert rows_of(page) == ["Terugzetten: artikel KM-1001: inkoopprijs 12,70 → 12,50"]          # the put-back is still to do
    assert current_prices(root, "S01")[0] == {"KM-1001": "12.70", "KM-1002": "8.40"}             # Exact holds the entered price until then
    after = lists_offered(page)
    assert after["row"] == {"__type__": "update", "visible": False} and after["choice"]["choices"] == [] and after["kept"] == {}
    # an undo row without a link (the manual's recipe) is no row of the list either
    other = tmp_path / "other"
    done = carry_out(write_csv(tmp_path / "een.csv", [item("KM-1001", "12.70")]), "S01", CURRENT, MAP, other, NOW)
    assert mark(other / "prepared", [done.updated[0].entry_id], "entered", NOW + timedelta(minutes=1)) == 1
    from aid.execution.writer import undo_entry
    undo_entry(other / "pricelists" / "2026-11-20", done.updated[0].entry_id, PreparedWriter(other / "prepared"), NOW + timedelta(minutes=2))
    [undo] = open_changes(other / "prepared")
    assert undo.rule_id == "undo" and undo.puts_back is None and undo.case_ids == ["pricelist_S01_20261120T093000"]
    assert price_lists([undo], other, "nl") == []


def test_what_the_view_shows_as_changing_can_be_put_on_the_list(tmp_path, madeup_out):
    """Ruling Z5, the review's two probes: when the list holds a value as entered while the supplier's newer export shows another
    price, the press puts that change back on the list as open, as 'Weer op de lijst zetten' does, noted as done by the assistant,
    and counts it with the others: also after more than 14 days, when the handled section no longer offers the change."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    filed(page, tmp_path, "S01")
    clock.later(10)
    november = price_list(tmp_path, "lijst nov.csv", LISTED)
    assert carried(page, november, "S01", viewed(page, november, "S01")["viewed"])["note"] == "2 prijswijzigingen staan op de lijst In Exact invoeren."
    assert tick_all(page, clock) == "2 wijzigingen genoteerd als ingevoerd in Exact."
    clock.later(20 * 24 * 60)                                                                 # 20 days: Exact was changed outside the aid
    filed(page, tmp_path, "S01", "artikelen dec.csv", [["KM-1001", "Slang", "12,60"], ["KM-1002", "Klem", "8,40"]])
    clock.later(10)
    december = price_list(tmp_path, "lijst dec.csv", LISTED)
    view = viewed(page, december, "S01")
    assert view["table"]["data"] == [["KM-1001", "12,60", "12,70", "prijs verandert"], ["KM-1002", "8,40", "8,40", "ongewijzigd"]]
    assert hand.done_of(page, "done_opened", "nl", None)["done_choice"]["choices"] == []      # nothing to put back by hand any more
    marks_before = len((root / "prepared" / MARKS_FILE).read_text(encoding="utf-8").splitlines())
    done = carried(page, december, "S01", view["viewed"])
    assert done["note"] == ONE_LISTED and done["table"] == view["table"]
    assert rows_of(page) == ["artikel KM-1001: inkoopprijs 12,60 → 12,70"]
    lines = [json.loads(line) for line in (root / "prepared" / MARKS_FILE).read_text(encoding="utf-8").splitlines()][marks_before:]
    [change] = open_changes(root / "prepared")
    assert [(line["entry_id"], line["mark"], line["by"]) for line in lines] == [(entry_id, "reopened", "assistant") for entry_id in change.ids]
    assert len(change.ids) == 2 and BY == "assistant"
    assert [label for label, _ in lists_offered(page)["choice"]["choices"]] == ["S01 (10-12-2026 09:36)"]
    # the same view and press again: the price is still asked, once
    clock.later(5)
    view = viewed(page, december, "S01")
    assert view["table"]["data"][0] == ["KM-1001", "12,60", "12,70", "prijs verandert"]
    assert carried(page, december, "S01", view["viewed"])["note"] == ONE_LISTED and len(rows_of(page)) == 1
    assert tick_all(page, clock) == "1 wijziging genoteerd als ingevoerd in Exact."
    clock.later(5)
    assert viewed(page, december, "S01")["table"]["data"][0] == ["KM-1001", "12,70", "12,70", "ongewijzigd"]
    # the tick was early: a newer export that still holds the old price asks the price again as well
    clock.later(5)
    filed(page, tmp_path, "S01", "artikelen opnieuw.csv", [["KM-1001", "Slang", "12,60"], ["KM-1002", "Klem", "8,40"]])
    clock.later(5)
    view = viewed(page, december, "S01")
    assert view["table"]["data"][0] == ["KM-1001", "12,60", "12,70", "prijs verandert"]
    assert carried(page, december, "S01", view["viewed"])["note"] == ONE_LISTED
    assert rows_of(page) == ["artikel KM-1001: inkoopprijs 12,60 → 12,70"]


def test_a_price_is_asked_again_only_when_the_export_is_the_newer_look(tmp_path):
    """Ruling Z5, its bounds (ask_again): a mark made after the supplier's export was stored is the newer look and stays, so the row
    that joins it stays off the list; a dropped change stays dropped; a change that the list takes as in Exact already, because an
    earlier change of the article holds its value as entered from before the export, is asked again too; and the marks of another
    article are left as they are."""
    root, prepared = tmp_path / "cases", tmp_path / "cases" / "prepared"
    listed = write_csv(tmp_path / "lijst.csv", [item("KM-1001", "12.70"), item("KM-1004", "3.45")])
    first = carry_out(listed, "S01", CURRENT, MAP, root, NOW)
    assert mark(prepared, [first.updated[0].entry_id], "entered", NOW + timedelta(minutes=5)) == 1
    assert mark(prepared, [first.updated[1].entry_id], "dropped", NOW + timedelta(minutes=5)) == 1
    stored_before, stored_after = (NOW + timedelta(minutes=1)).isoformat(), (NOW + timedelta(minutes=9)).isoformat()
    # the export older than the mark: Exact holds the value as far as the aid knows
    again = carry_out(listed, "S01", CURRENT, MAP, root, NOW + timedelta(minutes=10), stored_at=stored_before)
    assert on_list(again, root) == 0 and [change.state for change in changes(prepared)[0]] == ["entered", "dropped"]
    same_second = carry_out(listed, "S01", CURRENT, MAP, root, NOW + timedelta(minutes=11), stored_at=(NOW + timedelta(minutes=5)).isoformat())
    assert on_list(same_second, root) == 1                         # an export stored in the second of the mark is the newer look
    assert [change.state for change in changes(prepared)[0]] == ["open", "dropped"]
    assert mark(prepared, changes(prepared)[0][0].ids, "entered", NOW + timedelta(minutes=12)) == 3
    without = carry_out(listed, "S01", CURRENT, MAP, root, NOW + timedelta(minutes=13))            # no time of an export given: nothing is asked again
    assert on_list(without, root) == 0
    # the export newer than the mark: the entered change is asked again, the dropped one stays dropped
    newer = carry_out(listed, "S01", CURRENT, MAP, root, NOW + timedelta(minutes=20), stored_at=(NOW + timedelta(minutes=15)).isoformat())
    assert on_list(newer, root) == 1
    assert [(change.article_code, change.state) for change in changes(prepared)[0]] == [("KM-1001", "open"), ("KM-1004", "dropped")]
    # a change the list takes as in Exact already: entered, then another value dropped, then the first value again after a newer export
    other = tmp_path / "other"
    one = carry_out(write_csv(tmp_path / "een.csv", [item("KM-1001", "12.70"), item("KM-1002", "8.40")]), "S01", CURRENT, MAP, other, NOW)
    assert mark(other / "prepared", [entry.entry_id for entry in one.updated], "entered", NOW + timedelta(minutes=1)) == 2
    two = carry_out(write_csv(tmp_path / "twee.csv", [item("KM-1001", "12.90")]), "S01", CURRENT, MAP, other, NOW + timedelta(minutes=2))
    assert mark(other / "prepared", [two.updated[0].entry_id], "dropped", NOW + timedelta(minutes=3)) == 1
    three = carry_out(write_csv(tmp_path / "drie.csv", [item("KM-1001", "12.70")]), "S01", CURRENT, MAP, other, NOW + timedelta(minutes=4))
    assert [change.state for change in changes(other / "prepared")[0] if change.article_code == "KM-1001"] == ["entered", "dropped", "already"]
    assert on_list(three, other) == 0
    ask_again(three, other, (NOW + timedelta(minutes=3, seconds=30)).isoformat(), NOW + timedelta(minutes=5))
    assert [(change.article_code, change.state) for change in changes(other / "prepared")[0]] == [
        ("KM-1001", "replaced"), ("KM-1002", "entered"), ("KM-1001", "dropped"), ("KM-1001", "open")]
    assert on_list(three, other) == 1


def test_a_price_per_another_number_is_never_taken_per_piece_without_a_word(tmp_path, madeup_out):
    """Ruling Z6, the review's probe: a first list with a price per 100 whose column for what a price is per is not pointed out. The
    row is not put on the list: its price is more than three times the purchase price in Exact. The view shows it with an outcome of
    its own, it counts among the rows passed over, and the count line says why. The factor is one constant, and three times exactly,
    or a third exactly, is no strong difference."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    filed(page, tmp_path, "S01", "a.csv", [["KM-1001", "Slang", "12,50"], ["KM-1002", "Bout", "0,08"], ["KM-1003", "Doos", "25,00"]])
    clock.later(10)
    listed = price_list(tmp_path, "lijst.csv", [("KM-1001", "12,50", "1"), ("KM-1002", "8,40", "100"), ("KM-1003", "0,25", "")],
                        titles=("Artikelnummer", "Nettoprijs", "Per"))
    chosen = called(page, "pricelist_file_chosen", LIST_CHOSEN, listed, "S01", "nl")
    assert [chosen[name]["value"] for name in COLUMN_FIELDS] == [""] * 5                       # a first list: no field is filled
    columns = ["Artikelnummer", "Nettoprijs", "", "", ""]
    view = viewed(page, listed, "S01", columns)
    assert view["table"]["data"] == [["KM-1001", "12,50", "12,50", "ongewijzigd"], ["KM-1002", "0,08", "8,40", DIFFERS_NL],
                                     ["KM-1003", "25,00", "0,25", DIFFERS_NL]]
    assert view["note"] == f"0 prijzen veranderen, 1 blijft gelijk, 2 rijen overgeslagen. {DIFFERS_LINE}"
    done = carried(page, listed, "S01", view["viewed"], columns)
    assert done["note"] == NOTHING_LISTED and rows_of(page) == [] and not (root / "prepared").exists()
    english = viewed(page, listed, "S01", columns, "en")
    assert english["table"]["data"][1] == ["KM-1002", "0.08", "8.40", "differs strongly: check the column \"price per\""]
    assert english["note"] == ("0 prices change, 1 stays the same, 2 rows passed over. A price more than three times as high or as low is not "
                               "put on the list by the assistant: check the list, and enter a real change in Exact yourself.")
    # with the column pointed out the row per 100 is passed over as before, and nothing differs strongly
    per = viewed(page, listed, "S01", ["Artikelnummer", "Nettoprijs", "Per", "", ""])
    assert [row[3] for row in per["table"]["data"]] == ["ongewijzigd", "prijs per ander aantal: niet vergeleken", DIFFERS_NL]
    # the bounds: three times, and a third, are let through; a price above nothing differs strongly; a strong difference goes
    # before another supplier's hold
    check = held_back({"KM-1001"})
    assert FACTOR == 3 and (DIFFERS, SHARED) == ("differs strongly", "with another supplier")
    assert [check("KM-9", "12.50", new) for new in ("37.50", "37.51", "4.17", "4.16", "12.70")] == [None, DIFFERS, None, DIFFERS, None]
    assert (check("KM-9", "0", "0.01"), check("KM-9", "0.00", "5"), check("KM-9", "0.09", "0.03")) == (DIFFERS, DIFFERS, None)
    assert (check("KM-1001", "12.50", "12.70"), check("KM-1001", "12.50", "1250")) == (SHARED, DIFFERS)
    assert held_back()("KM-1001", "12.50", "12.70") is None


def test_a_suppliers_name_is_a_name_never_a_path_and_never_a_cost(tmp_path, madeup_out, monkeypatch):
    """Ruling Z7, the review's probe: a supplier whose name holds a slash costs no wait at any read of the list, after 1 and after 20
    price lists (the wait of a case that cannot be read is never begun: no case can have such an id); a name longer than 100
    characters, or with a line break or a control character, is refused by the tab with a sentence, and nothing is stored."""
    import aid.tryout_mark as tryout_mark

    def no_wait(seconds):
        raise AssertionError("a read of the list waited for a case id that is no folder name")
    monkeypatch.setattr(tryout_mark, "sleep", no_wait)
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    name = "Hansen A/S"
    filed(page, tmp_path, name, "artikelen.csv", [[f"KM-{1000 + n}", "Slang", "12,50"] for n in range(1, 21)])
    for n in range(1, 21):
        clock.later(60)
        listed = price_list(tmp_path, "lijst.csv", [(f"KM-{1000 + n}", "12,70", "", "", "")])
        assert carried(page, listed, name, viewed(page, listed, name)["viewed"])["note"] == ONE_LISTED
        if n in (1, 20):
            assert len(rows_of(page)) == (1 if n == 1 else 19) and len(lists_offered(page)["choice"]["choices"]) == (1 if n == 1 else 19)
            hand.done_of(page, "done_opened", "nl", None)
            first = hand.shown(page, "list_opened", "nl")["rows"]["choices"][0][1]
            assert hand.shown(page, "changes_entered", [first], "nl")["note"] == "1 wijziging genoteerd als ingevoerd in Exact."
    assert current_prices(root, name)[0]["KM-1001"] == "12.70"
    assert not [path for path in tmp_path.rglob("*") if "pricelist_Hansen" in path.name]       # and the name made no folder anywhere
    # a name that is no name: refused wherever the tab takes one, and nothing stored
    stored = hand.files_of(root)
    export = articles_export(tmp_path, "artikelen.csv", S01_ROWS)
    listed = price_list(tmp_path, "lijst.csv", LISTED)
    for bad in ("L" * 101, "Jansen\nen Zonen", "Jan\tsen", "S01\x00", "S  01"):
        for language, sentence in (("nl", "De naam van de leverancier is te lang of bevat tekens die niet in een naam horen."),
                                   ("en", "The supplier's name is too long or holds characters that do not belong in a name.")):
            refused = called(page, "prices_filed", PRICES_FILED, export, bad, language, None)
            assert refused["export_note"] == sentence and refused["supplier"]["value"] is None, repr(bad)
            assert called(page, "pricelist_supplier_chosen", SUPPLIER_CHOSEN, bad, None, language)["state"] == sentence
            view = viewed(page, listed, bad, language=language)
            assert (view["note"], view["viewed"], view["carry"]) == (sentence, None, INACTIVE)
    assert hand.files_of(root) == stored
    longest = "L" * 100                                                                         # a hundred characters are a name
    taken = called(page, "prices_filed", PRICES_FILED, export, f"  {longest} ", "nl", None)
    assert taken["export_note"].startswith(f"Inkoopprijzen van {longest} opgenomen")


def test_a_case_id_that_is_no_folder_name_is_never_looked_up(tmp_path, madeup_out, monkeypatch):
    """Ruling Z7, findings 6, 13 and 15: a case id that cannot be the name of a folder is no try-out's at once, and a price list
    with such an id is a price list without asking the file system: the one press offers it and 'Terugdraaien in Exact' finds its
    write log, also for a name longer than a file name may be."""
    import aid.tryout_mark as tryout_mark
    from aid.case import names_a_folder
    from aid.review.hand_list import price_lists
    from aid.tryout_mark import case_is_tryout

    assert [names_a_folder(case_id) for case_id in ("250108_20261112T090000", "pricelist_S01_20261120T093000", "a b.c")] == [True] * 3
    assert [names_a_folder(case_id) for case_id in ("", ".", "..", "a/b", "/etc/naam", "../x", "x\x00y", "X" * 256, "é" * 128)] == [False] * 9
    assert names_a_folder("X" * 255) and names_a_folder("a:b?", windows=False) and not names_a_folder("a:b", windows=True)
    assert not names_a_folder("a*b", windows=True) and names_a_folder("Voorbeeld B.V.", windows=True)
    monkeypatch.setattr(tryout_mark, "sleep", lambda seconds: (_ for _ in ()).throw(AssertionError("waited")))
    looked = []
    monkeypatch.setattr(tryout_mark, "load_case", lambda root, case_id: looked.append(case_id) or (_ for _ in ()).throw(FileNotFoundError()))
    for case_id in ("pricelist_Hansen A/S_20261120T093000", "pricelist_" + "X" * 300 + "_20261120T093000", "..", ""):
        assert case_is_tryout(tmp_path, case_id) is False
    assert looked == [] and case_is_tryout(tmp_path, "pricelist_S01_20261120T093000") is False and looked == ["pricelist_S01_20261120T093000"]
    monkeypatch.undo()
    # a supplier's name of 300 characters, as the command or an older list may have left one: offered, and put back
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root, long = d.settings.cases_root, "X" * 300
    done = carry_out(write_csv(tmp_path / "lang.csv", [item("KM-1001", "12.70")]), long, CURRENT, MAP, root, clock())
    [(label, case_id, values)] = price_lists(open_changes(root / "prepared"), root, "nl")
    assert label == f"{long} (20-11-2026 09:15)" and values == [done.updated[0].entry_id]
    assert [value for _, value in lists_offered(page)["choice"]["choices"]] == [case_id]
    assert tick_all(page, clock) == "1 wijziging genoteerd als ingevoerd in Exact."
    clock.later(1)
    back = hand.done_of(page, "change_put_back", hand.chosen_of(hand.done_of(page, "done_opened", "nl", None), "KM-1001"), "nl")
    assert "Terugzetten: artikel KM-1001: inkoopprijs 12,70 → 12,50" in back["done_note"]
    # a name of a hundred characters that is longer than a file name in bytes: its list's tick is its own supplier's, and no other's
    wide = "€" * 100
    assert not names_a_folder(f"pricelist_{wide}_20261120T093000")
    filed(page, tmp_path, wide, "breed.csv", [["KM-2001", "Bout", "3,20"]])
    clock.later(5)
    listed = price_list(tmp_path, "breed lijst.csv", [("KM-2001", "3,30", "", "", "")])
    assert carried(page, listed, wide, viewed(page, listed, wide)["viewed"])["note"] == ONE_LISTED
    filed(page, tmp_path, "S02", "s02.csv", [["KM-2001", "Bout", "3,10"]])                    # a second supplier comes to hold the article
    [value] = [value for text, value in hand.shown(page, "list_opened", "nl")["rows"]["choices"] if "KM-2001" in text]
    clock.later(5)
    assert hand.shown(page, "changes_entered", [value], "nl")["note"] == "1 wijziging genoteerd als ingevoerd in Exact."
    assert current_prices(root, wide)[0] == {"KM-2001": "3.30"} and current_prices(root, "S02")[0] == {"KM-2001": "3.10"}


def test_a_csv_price_list_is_read_as_the_export_is(tmp_path, madeup_out):
    """Ruling Z8: the tab reads a CSV price list in the encodings and at the separators of the export of the articles (UTF-8, UTF-16
    with its mark, Windows-1252; comma, semicolon, tab), and refuses what it cannot read with a sentence; the command's reader
    stays as it was."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    filed(page, tmp_path, "S01")
    clock.later(10)
    titles = ["Artikelnummer", "Omschrijving", "Prijs (€)"]
    text = "Artikelnummer\tOmschrijving\tPrijs (€)\r\nKM-1001\tSlang, 2 m; zwart\t12,70\r\nKM-1002\tKlem\t8,00\r\n"
    commas = "Artikelnummer,Omschrijving,Prijs (€)\nKM-1001,\"Slang, 2 m; zwart\",\"12,70\"\nKM-1002,Klem,\"8,00\"\n"
    semicolons = text.replace("\t", ";").replace("Slang, 2 m; zwart", "Slang 2 m zwart")
    offered = [("(staat niet in het bestand)", ""), *[(title, title) for title in titles]]
    for name, data in (("utf16 tabs.csv", b"\xff\xfe" + text.encode("utf-16-le")), ("utf16 be.csv", b"\xfe\xff" + text.encode("utf-16-be")),
                       ("utf8 tabs.csv", text.encode("utf-8")), ("bom tabs.csv", b"\xef\xbb\xbf" + text.encode("utf-8")),
                       ("cp1252 tabs.csv", text.encode("cp1252")), ("komma.csv", commas.encode("utf-8")),
                       ("puntkomma.csv", semicolons.encode("cp1252"))):
        upload = uploaded(tmp_path, name, data)
        chosen = called(page, "pricelist_file_chosen", LIST_CHOSEN, upload, "S01", "nl")
        assert chosen["note"] == "" and chosen["column:price"]["choices"] == offered, name
        view = viewed(page, upload, "S01", ["Artikelnummer", "Prijs (€)", "", "", ""])
        assert view["table"]["data"] == [["KM-1001", "12,50", "12,70", "prijs verandert"], ["KM-1002", "8,00", "8,00", "ongewijzigd"]], name
    assert carried(page, upload, "S01", view["viewed"], ["Artikelnummer", "Prijs (€)", "", "", ""])["note"] == ONE_LISTED
    written = hand.files_of(root)
    # what cannot be read is refused with a sentence, at the choice of the file and at the view
    for name, data in (("zonder merk.csv", text.encode("utf-16-le")), ("plaatje.csv", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"),
                       ("document.csv", b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"), ("een kolom.csv", b"Artikelnummer\nKM-1001\n")):
        upload = uploaded(tmp_path, name, data)
        for language in ("nl", "en"):
            sentence = STRINGS[language]["exact_export_refused:delimiter_or_encoding"]
            chosen = called(page, "pricelist_file_chosen", LIST_CHOSEN, upload, "S01", language)
            assert chosen["note"] == sentence and chosen["column:item_code"]["choices"] == [(STRINGS[language]["exact_export_no_column"], "")], name
            view = viewed(page, upload, "S01", ["Artikelnummer", "Prijs (€)", "", "", ""], language)
            assert (view["note"], view["table"]["data"], view["viewed"]) == (sentence, [], None), name
    assert hand.files_of(root) == written
    # the mark is the one that cuts the row of titles into the most cells: commas in the descriptions further down never decide
    wordy = "Artikelnummer;Omschrijving;Prijs\n" + "".join(f"KM-100{n};Slang, 2 m, zwart, glad, per stuk;12,7{n}\n" for n in range(1, 5))
    assert list_table(Path(uploaded(tmp_path, "woorden.csv", wordy.encode("utf-8"))))[:2] == [
        ["Artikelnummer", "Omschrijving", "Prijs"], ["KM-1001", "Slang, 2 m, zwart, glad, per stuk", "12,71"]]
    tie = Path(uploaded(tmp_path, "gelijk.csv", b"a;b,c\n1;2,3\n"))                             # a tie: the first of the reader's marks
    assert list_table(tie) == [["a", "b,c"], ["1", "2,3"]]
    below = Path(uploaded(tmp_path, "lager.csv", b"\n,,\nitem_code,price\nKM-1001,12.70\n"))   # the row of titles below blank rows
    assert list_table(below)[2:] == [["item_code", "price"], ["KM-1001", "12.70"]] and list_titles(below) == ["item_code", "price"]
    # the command's reader stays: it reads no tabs, and the tab's reading of the same file does
    tabs = Path(uploaded(tmp_path, "tabs.csv", b"item_code\tprice\nKM-1001\t12,70\n"))
    with pytest.raises(ValueError):
        import_price_list(tabs, "S01", CURRENT, PreparedWriter(tmp_path / "command"), NOW)
    assert plan(preview(tabs, "S01", CURRENT, {}, NOW)) == [["KM-1001", "12.50", "12.70"]]
    assert list_table(tabs) == [["item_code", "price"], ["KM-1001", "12,70"]]


def test_the_kept_columns_are_data_beside_the_stored_prices(tmp_path, madeup_out):
    """Rulings Z9 and Z12: the columns kept for a supplier's list are kept under <cases_root>/exact, written whole or not at all,
    each supplier its own: a second supplier's list keeps its columns and leaves the first supplier's. No press of the tab changes
    the settings file."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    path, root = tmp_path / "settings.json", d.settings.cases_root
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    filed(page, tmp_path, "S01")
    filed(page, tmp_path, "S02", "s02.csv", [["KM-2001", "Bout", "3,20"]])
    clock.later(10)
    first = price_list(tmp_path, "s01.csv", [("KM-1001", "12,70")], titles=("Artikelnummer", "Nettoprijs"))
    second = price_list(tmp_path, "s02.csv", [("KM-2001", "3,30", "1")], titles=("Code", "Prijs", "Per"))
    for listed, supplier, columns in ((first, "S01", ["Artikelnummer", "Nettoprijs", "", "", ""]), (second, "S02", ["Code", "Prijs", "Per", "", ""])):
        assert carried(page, listed, supplier, viewed(page, listed, supplier, columns)["viewed"], columns)["note"] == ONE_LISTED
    kept = {"S01": {"item_code": "Artikelnummer", "price": "Nettoprijs"}, "S02": {"item_code": "Code", "price": "Prijs", "basis": "Per"}}
    assert kept_columns(root) == kept and sorted(file.name for file in (root / "exact").iterdir()) == ["article_prices.json", "pricelist_columns.json"]
    assert called(page, "pricelist_file_chosen", LIST_CHOSEN, first, " s01", "nl")["column:price"]["value"] == "Nettoprijs"
    assert called(page, "pricelist_file_chosen", LIST_CHOSEN, second, "S02", "nl")["column:basis"]["value"] == "Per"
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before                              # two exports and two presses later
    # the same columns again write nothing; other columns replace the supplier's own
    file = root / "exact" / "pricelist_columns.json"
    written = file.stat().st_mtime_ns
    keep_columns(root, " s 01 ", kept["S01"])
    assert file.stat().st_mtime_ns == written and kept_columns(root) == kept
    keep_columns(root, "s01", {"item_code": "Artikel", "price": "Netto"})
    assert kept_columns(root) == kept | {"S01": {"item_code": "Artikel", "price": "Netto"}}
    # whole or not at all: a write that fails leaves the file as it was and no temporary file
    held = file.read_bytes()

    def refuse(source, target):
        raise PermissionError("made-up: the file is held")
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(os, "replace", refuse)
        with pytest.raises(PermissionError):
            keep_columns(root, "S03", {"item_code": "A", "price": "B"})
    assert file.read_bytes() == held and sorted(one.name for one in file.parent.iterdir()) == ["article_prices.json", "pricelist_columns.json"]
    # a file that does not hold kept columns is nothing kept, and the next press writes it anew
    for damaged in (b"{\"suppliers\": {\"S01\": {\"item_code\": 7}}}", b"[]", b"\xff\xfe", b"{\"suppliers\": {\"S01\": {\"it"):
        file.write_bytes(damaged)
        assert kept_columns(root) == {}
        assert [called(page, "pricelist_file_chosen", LIST_CHOSEN, first, "S01", "nl")[name]["value"] for name in COLUMN_FIELDS] == [""] * 5
    keep_columns(root, "S02", kept["S02"])
    assert kept_columns(root) == {"S02": kept["S02"]} and kept_columns(tmp_path / "nowhere") == {}


def test_the_table_says_what_became_of_each_row(tmp_path, madeup_out, monkeypatch):
    """Ruling Z10: after the press the table is shown again with the outcome of each row, 'niet gelukt' for a change that could not
    be prepared, and the note names the articles of those rows. When only the columns could not be kept, the changes stand on the
    list and the note says so in a sentence of its own."""
    import aid.review.page as page_module
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    filed(page, tmp_path, "S01", rows=[["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", "8,00"], ["<b>KM</b>", "Ring", "5,00"]])
    clock.later(10)
    listed = price_list(tmp_path, "lijst.csv", [("KM-1001", "12,70", "", "", ""), ("KM-1002", "8,40", "", "", ""), ("<b>KM</b>", "5,10", "", "", ""),
                                               ("KM-9999", "1,00", "", "", "")])
    view = viewed(page, listed, "S01")
    apply = PreparedWriter.apply

    def failing(self, op, case_id, rule_id, evidence, now):
        if op.article_code != "KM-1001":
            raise WriterError("made-up: the file of the prepared changes is held")
        return apply(self, op, case_id, rule_id, evidence, now)
    with monkeypatch.context() as patched:
        patched.setattr(PreparedWriter, "apply", failing)
        done = carried(page, listed, "S01", view["viewed"])
    assert done["table"]["data"] == [["KM-1001", "12,50", "12,70", "prijs verandert"], ["KM-1002", "8,00", "", "niet gelukt"],
                                     ["<b>KM</b>", "5,00", "", "niet gelukt"], ["KM-9999", "", "", "artikel niet bekend in Exact"]]
    marked = f"{_literal('KM-1002')}, {_literal('<b>KM</b>')}"                               # an item code is shown as text
    assert "<b>" not in marked.replace("\\<b\\>", "") and done["note"] == ("1 prijswijziging staat op de lijst In Exact invoeren. Niet op de lijst gezet: "
                                                    f"{marked}. Bekijk de lijst opnieuw om het nog eens te proberen.")      # ruling Z25: no class
    assert rows_of(page) == ["artikel KM-1001: inkoopprijs 12,50 → 12,70"] and "made-up" not in json.dumps(done, ensure_ascii=False)
    assert not_prepared_line(preview(listed, "S01", CURRENT, MAP, NOW), "nl") == ""
    # viewed again, the rows that failed still change, and the press prepares them
    clock.later(5)
    view = viewed(page, listed, "S01", language="en")
    assert [row[3] for row in view["table"]["data"]] == ["price changes"] * 3 + ["article not known in Exact"]
    with monkeypatch.context() as patched:
        patched.setattr(PreparedWriter, "apply", failing)
        english = carried(page, listed, "S01", view["viewed"], language="en")
    assert english["note"] == (f"1 price change is on the list Enter in Exact. Not put on the list: {marked}. "
                               "View the list again to try once more.") and english["table"]["data"][1][3] == "failed"
    # only the columns could not be kept: the changes are on the list, and a sentence of its own says so
    view = viewed(page, listed, "S01")

    def refuse(cases_root, supplier, columns):
        raise PermissionError("made-up: the file is held")
    with monkeypatch.context() as patched:
        patched.setattr(page_module, "keep_columns", refuse)
        done = carried(page, listed, "S01", view["viewed"])
    assert done["note"] == ("3 prijswijzigingen staan op de lijst In Exact invoeren. De kolommen van deze lijst konden niet worden bewaard: "
                            "wijs ze de volgende keer opnieuw aan.")                         # ruling Z25: the count line says the rest
    assert [row[3] for row in done["table"]["data"]] == ["prijs verandert"] * 3 + ["artikel niet bekend in Exact"] and len(rows_of(page)) == 3
    assert EN["pricelist_columns_lost"] == "The columns of this list could not be kept: point them out again next time."
    assert "PermissionError" not in done["note"]
    # with a row that failed and the columns not kept, no sentence says that the changes stand on the list
    clock.later(5)
    view = viewed(page, listed, "S01")
    with monkeypatch.context() as patched:
        patched.setattr(PreparedWriter, "apply", failing)
        patched.setattr(page_module, "keep_columns", refuse)
        both = carried(page, listed, "S01", view["viewed"])
    assert both["note"] == (f"1 prijswijziging staat op de lijst In Exact invoeren. Niet op de lijst gezet: {marked}. Bekijk de lijst opnieuw om "
                            "het nog eens te proberen. De kolommen van deze lijst konden niet worden bewaard: wijs ze de volgende keer opnieuw aan.")


def test_an_export_without_an_article_is_refused_on_the_tab(tmp_path, madeup_out):
    """Ruling Z11: an export of the articles that gives no article stores nothing and leaves what was stored, with a sentence that
    says so; without a supplier column too, where it used to empty the supplier's prices."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    filed(page, tmp_path, "S01")
    stored = hand.files_of(root)
    clock.later(5)
    for rows in ([], [["KM-1001", "Slang", "op aanvraag"], [None, "Totaal", "20,50"], ["KM-1002", "Klem", None]]):
        for language, sentence in (("nl", "Deze export gaf geen artikel: geen rij heeft een artikelcode en een prijs die te lezen is. Er is niets "
                                          "opgenomen."),
                                   ("en", "This export gave no article: no row has an item code and a price that can be read. Nothing was filed.")):
            for supplier in ("S01", "S02"):
                refused = filed(page, tmp_path, supplier, "leeg.csv", rows, language)
                assert refused["export_note"] == sentence and refused["supplier"]["choices"] == ["S01"], supplier
    assert hand.files_of(root) == stored and current_prices(root, "S01")[0] == {"KM-1001": "12.50", "KM-1002": "8.00"}


def test_a_try_outs_article_price_ticked_in_the_test_mode_never_counts(tmp_path, madeup_out):
    """Ruling Z12 (finding 10): the article price that a case made from a try-out asked, ticked as entered in the test mode, never
    counts as the price in Exact: the tab compares a price list with the supplier's stored price as before."""
    from aid.tryout import confirmation_pdf, made_from
    page, d, clock = pricelist_page(tmp_path, madeup_out, test_mode=True)
    root = d.settings.cases_root
    filed(page, tmp_path, "S01", rows=[["KM-1001", "Slang", "12,50"]])
    made = tmp_path / "proef.pdf"
    made.write_bytes(confirmation_pdf(made_from(d.store, "250101"), "price_small", datetime(2026, 11, 12, 9, 30)))
    case = hand.open_upload_case(root, [made], "250101", "confirmation", datetime(2026, 11, 12, 9, 30))
    hand.record_dispositions(case, hand.run_case(case, d))
    [article] = [change for change in open_changes(root / "prepared") if change.kind == "article_price"]
    assert article.tryout and article.article_code == "KM-1001" and article.new_value not in (None, "12.50")
    clock.later(10)
    assert mark(root / "prepared", article.ids, "entered", clock()) == 1                        # after the export was stored
    assert current_prices(root, "S01")[0] == {"KM-1001": "12.50"}
    asked = article.new_value.replace(".", ",")
    listed = price_list(tmp_path, "lijst.csv", [("KM-1001", asked, "", "", "")])
    assert viewed(page, listed, "S01")["table"]["data"] == [["KM-1001", "12,50", asked, "prijs verandert"]]


def test_a_view_and_a_press_on_two_days(tmp_path, madeup_out):
    """Ruling Z12 (finding 10): the press carries out the view or nothing. A row that is not valid on the day of the view and is
    valid on the day of the press is not carried out without a new view."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    filed(page, tmp_path, "S01")
    clock.later(14 * 60 + 30)                                                                 # 20-11-2026 23:45
    listed = price_list(tmp_path, "lijst.csv", [("KM-1001", "12,70", "", "", ""), ("KM-1002", "8,40", "", "2026-11-21", "")])
    view = viewed(page, listed, "S01")
    assert view["table"]["data"] == [["KM-1001", "12,50", "12,70", "prijs verandert"], ["KM-1002", "8,00", "", "nog niet geldig"]]
    clock.later(30)                                                                           # 21-11-2026 00:15
    late = carried(page, listed, "S01", view["viewed"])
    assert (late["note"], late["table"], late["viewed"]) == (CHANGED, SAME, None) and not (root / "prepared").exists()
    view = viewed(page, listed, "S01")
    assert [row[3] for row in view["table"]["data"]] == ["prijs verandert", "prijs verandert"]
    assert carried(page, listed, "S01", view["viewed"])["note"] == "2 prijswijzigingen staan op de lijst In Exact invoeren."


def test_a_suppliers_name_with_marks_is_shown_as_text(tmp_path, madeup_out):
    """Ruling Z12 (finding 10): a supplier's name is shown as it is in the state line and in the line after an export: no mark of it
    is read as markup."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    name = "*S01* <b>x</b> [a](b) `c`"
    shown_name = _literal(name)
    assert shown_name != name and "\\*S01\\*" in shown_name and "\\<b\\>" in shown_name
    stored = filed(page, tmp_path, name)
    assert stored["export_note"] == f"Inkoopprijzen van {shown_name} opgenomen: 2 artikelen; 0 rijen overgeslagen."
    assert stored["state"] == f"Inkoopprijzen van {shown_name} opgenomen op 2026-11-20 09:15 uit artikelen\\.csv: 2 artikelen."
    assert stored["supplier"]["value"] == name                                                  # the dropdown holds the name itself
    for language in ("nl", "en"):
        opened = called(page, "pricelist_opened", PRICELIST_OPENED, name, language)
        chosen = called(page, "pricelist_supplier_chosen", SUPPLIER_CHOSEN, name, None, language)
        assert opened["state"] == chosen["state"] and shown_name in opened["state"] and name not in opened["state"]
    other = "_nieuw_ & <i>co</i>"
    none = called(page, "pricelist_supplier_chosen", SUPPLIER_CHOSEN, other, None, "nl")["state"]
    assert none == NL["pricelist_state_none"].format(supplier=_literal(other)) and other not in none
    assert viewed(page, price_list(tmp_path, "lijst.csv", LISTED), other)["note"] == none
    english = filed(page, tmp_path, name, language="en")
    assert english["export_note"] == f"Purchase prices of {shown_name} filed: 2 articles; 0 rows passed over."


# --- the second review round of the tab (rulings Z16 to Z27) ---

SURPLUS_NL = ("Rij {n} van dit bestand heeft meer cellen dan de rij met kolomtitels. Meestal staat er dan een komma in een getal of in een "
              "omschrijving zonder aanhalingstekens. Sla het bestand op als Excel-bestand (.xlsx) of met puntkomma's als scheidingsteken, en "
              "geef het opnieuw.")
SURPLUS_EN = ("Row {n} of this file has more cells than the row of column titles. Usually a number or a description then holds a comma "
              "without quotation marks. Save the file as an Excel file (.xlsx) or with semicolons as the separator, and give it again.")


UNEVEN_NL = ("Rij {n} van dit bestand heeft een ander aantal cellen dan de rij met kolomtitels. In een bestand met komma's als "
             "scheidingsteken staat er dan meestal een komma in een getal of in een omschrijving zonder aanhalingstekens. Sla het bestand op "
             "als Excel-bestand (.xlsx) of met puntkomma's als scheidingsteken, en geef het opnieuw.")
UNEVEN_EN = ("Row {n} of this file has another number of cells than the row of column titles. In a file with commas as the separator a "
             "number or a description then usually holds a comma without quotation marks. Save the file as an Excel file (.xlsx) or with "
             "semicolons as the separator, and give it again.")
HIDDEN_NL = ("Dit Excel-bestand heeft alleen gegevens op een verborgen werkblad. De assistent leest geen verborgen werkbladen: maak het "
             "werkblad zichtbaar, of sla het op als een eigen bestand, en geef het opnieuw.")
HIDDEN_EN = ("This Excel file holds data on a hidden sheet only. The assistant reads no hidden sheets: unhide the sheet, or save it as a "
             "file of its own, and give it again.")


def test_a_csv_row_with_more_cells_than_the_titles_is_never_read_on_the_tab(tmp_path, madeup_out):
    """Ruling Z16, the check's case c16: a comma file whose decimal commas stand without quotation marks. The tab read 12,70 as 12
    ("prijs verandert") and 8,40 as 8 ("ongewijzigd"), and stored 12 and 8 from the same rows given as the export. Such a file is
    refused whole with the row named, as the list and as the export; nothing is compared or stored. Trailing separators and a
    quoted comma are read as before."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    filed(page, tmp_path, "S01")
    stored = hand.files_of(root)
    clock.later(10)
    rows = b"KM-1001,Slang,12,70\r\nKM-1002,Klem,8,40\r\n"
    listed = uploaded(tmp_path, "c16.csv", b"Artikelnummer,Omschrijving,Nettoprijs\r\n" + rows)
    columns = ["Artikelnummer", "Nettoprijs", "", "", ""]
    for language, sentence in (("nl", UNEVEN_NL.format(n=2)), ("en", UNEVEN_EN.format(n=2))):      # a comma file: ruling Z30's sentence
        chosen = called(page, "pricelist_file_chosen", LIST_CHOSEN, listed, "S01", language)
        assert chosen["note"] == sentence and chosen["column:price"]["choices"] == [(STRINGS[language]["exact_export_no_column"], "")]
        view = viewed(page, listed, "S01", columns, language)
        assert (view["note"], view["table"]["data"], view["carry"], view["viewed"]) == (sentence, [], INACTIVE, None)
        export = uploaded(tmp_path, "c16 export.csv", b"Artikel,Omschrijving,Inkoopprijs\r\n" + rows)
        for supplier in ("S01", "S02"):
            assert called(page, "prices_filed", PRICES_FILED, export, supplier, language, None)["export_note"] == sentence
    assert hand.files_of(root) == stored and current_prices(root, "S01")[0] == {"KM-1001": "12.50", "KM-1002": "8.00"}
    fine = uploaded(tmp_path, "goed.csv", b'Artikelnummer,Omschrijving,Nettoprijs,,\r\nKM-1001,"Slang, 2 m","12,70",,\r\nKM-1002,Klem,"8,40", ,\r\n')
    assert viewed(page, fine, "S01", columns)["table"]["data"] == [["KM-1001", "12,50", "12,70", "prijs verandert"],
                                                                  ["KM-1002", "8,00", "8,40", "prijs verandert"]]
    lower = uploaded(tmp_path, "lager.csv", b"Artikelnummer;Nettoprijs\r\nKM-1001;12,70\r\n\r\nKM-1002;8,40;x\r\n")
    assert viewed(page, lower, "S01", columns)["note"] == SURPLUS_NL.format(n=4)


def test_a_name_that_is_no_name_in_the_export_names_its_row(tmp_path, madeup_out):
    """Ruling Z26 (the check's probe row_name.py): with a supplier column, a supplier cell of a kept row that is no name refuses the
    export with a sentence of its own that names the row, and not with the sentence for a name the buyer typed; nothing is stored."""
    page, d, clock = pricelist_page(tmp_path, madeup_out, article_columns=ARTICLE_COLUMNS | {"supplier": "Leverancier"})
    root = d.settings.cases_root
    titles = ("Artikel", "Leverancier", "Inkoopprijs")
    assert filed(page, tmp_path, None, "goed.xlsx", [["KM-1001", "S01", 12.5], ["KM-2001", "S02", 3.2]], titles=titles)["export_note"] == (
        "Inkoopprijzen opgenomen van 2 leveranciers: 2 artikelen; 0 rijen overgeslagen.")
    stored = hand.files_of(root)
    clock.later(5)
    rows = [["KM-1001", "S01", 12.7], ["KM-2001", "S02", 3.3], ["KM-3001", "S03 BV\nPostbus 1", 1.5]]
    for language, sentence in (
            ("nl", "In rij 6 van de export staat bij de leverancier een naam die te lang is of tekens bevat die niet in een naam horen. Er is "
                   "niets opgenomen."),
            ("en", "Row 6 of the export holds a supplier's name that is too long or holds characters that do not belong in a name. Nothing was "
                   "filed.")):
        assert filed(page, tmp_path, None, "namen.xlsx", rows, language, titles)["export_note"] == sentence
    assert hand.files_of(root) == stored and stored_prices(root)["S01"].prices == {"KM-1001": "12.5"}


def test_a_heading_above_the_column_titles_is_told_so(tmp_path, madeup_out):
    """Ruling Z27 (the check's probe csv_lists.py): a price list whose first filled row holds one filled cell while a later row
    holds more has a heading above its column titles. It is refused with a sentence that says so, as a CSV file and as an Excel
    file, in place of the sentence about the separator and of one odd title in the fields. A file of one column all the way down
    is no such file."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    filed(page, tmp_path, "S01")
    body = "Artikelnummer;Nettoprijs\nKM-1001;12,70\nKM-1002;8,40\n"
    book = sheets_file(tmp_path, "kop.xlsx", [("Lijst", [["Prijslijst 2027"], [], ["Artikelnummer", "Nettoprijs"], ["KM-1001", 12.7]], "visible")])
    wide = sheets_file(tmp_path, "kop breed.xlsx", [("Lijst", [[None, "Prijslijst 2027", None], ["Artikelnummer", "Nettoprijs", "Per"]], "visible")])
    files = [uploaded(tmp_path, "kop.csv", ("Prijslijst 2027\n\n" + body).encode()),
             uploaded(tmp_path, "kop met scheidingstekens.csv", ("Prijslijst 2027;;\n;;\n" + body).encode()),
             uploaded(tmp_path, "kop komma.csv", ("Prijslijst 2027\n" + body.replace(";", ",").replace("12,70", "12.70").replace("8,40", "8.40")).encode()),
             book, wide]
    for language, sentence in (("nl", "De kolomtitels moeten in de eerste rij van het bestand staan: haal de regels erboven weg en geef het "
                                      "bestand opnieuw."),
                               ("en", "The column titles must stand in the first row of the file: remove the lines above them and give the file "
                                      "again.")):
        for upload in files:
            chosen = called(page, "pricelist_file_chosen", LIST_CHOSEN, upload, "S01", language)
            assert chosen["note"] == sentence, Path(upload).name
            assert chosen["column:item_code"]["choices"] == [(STRINGS[language]["exact_export_no_column"], "")], Path(upload).name
            view = viewed(page, upload, "S01", ["Artikelnummer", "Nettoprijs", "", "", ""], language)
            assert (view["note"], view["table"]["data"], view["viewed"]) == (sentence, [], None), Path(upload).name
    # one column all the way down: the separator's sentence for a CSV file, one title for a workbook, as before
    column = uploaded(tmp_path, "een kolom.csv", b"Artikelnummer\nKM-1001\nKM-1002\n")
    assert called(page, "pricelist_file_chosen", LIST_CHOSEN, column, "S01", "nl")["note"] == NL["exact_export_refused:delimiter_or_encoding"]
    single = sheets_file(tmp_path, "een kolom.xlsx", [("Lijst", [["Artikelnummer"], ["KM-1001"]], "visible")])
    one = called(page, "pricelist_file_chosen", LIST_CHOSEN, single, "S01", "nl")
    assert one["note"] == "" and one["column:item_code"]["choices"][1:] == [("Artikelnummer", "Artikelnummer")]
    # the titles in the first row, also below blank rows, are read as before
    below = uploaded(tmp_path, "lager.csv", ("\n;;\n" + body).encode())
    assert viewed(page, below, "S01", ["Artikelnummer", "Nettoprijs", "", "", ""])["note"] == "2 prijzen veranderen, 0 blijven gelijk, 0 rijen overgeslagen."
    with pytest.raises(ListRefused) as heading:
        list_table(Path(book))
    assert heading.value.reason == "heading"


def test_a_press_whose_own_log_cannot_be_written_says_what_is_true(tmp_path, madeup_out, monkeypatch):
    """Ruling Z23 (the check's probe z10.py, run F3): when the price list's own write log cannot be written, the changes stand on
    the list all the same. The press shows the table, keeps the columns, and its note is the count line followed by the sentence
    that the aid cannot put these changes back itself later; it said "Dit is niet gelukt" and kept nothing."""
    import aid.review.pricelist_page as tab_module
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    filed(page, tmp_path, "S01", rows=[["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", "8,00"], ["KM-1003", "Ring", "5,00"]])
    clock.later(10)
    listed = price_list(tmp_path, "lijst.csv", [("KM-1001", "12,70", "", "", ""), ("KM-1002", "8,40", "", "", ""), ("KM-1003", "5,10", "", "", "")])

    def no_log(folder, entries):
        raise PermissionError(13, "made-up: write_log.jsonl is held")
    for language, sentence in (
            ("nl", "3 prijswijzigingen staan op de lijst In Exact invoeren. De assistent kon deze wijzigingen niet in zijn eigen logboek van de "
                   "prijslijst schrijven en kan ze daardoor later niet zelf terugzetten: geef dit door aan de onderzoeker."),
            ("en", "3 price changes are on the list Enter in Exact. The assistant could not write these changes into its own log of the price "
                   "list, so it cannot put them back itself later: pass this on to the researcher.")):
        view = viewed(page, listed, "S01", language=language)
        with monkeypatch.context() as patched:
            patched.setattr(tab_module, "_log", no_log)
            done = carried(page, listed, "S01", view["viewed"], language=language)
        assert done["note"] == sentence and done["table"] == view["table"] and done["carry"] == INACTIVE and done["viewed"] is None
        assert "PermissionError" not in done["note"] and "made-up" not in done["note"]
        clock.later(5)
    assert rows_of(page) == ["artikel KM-1001: inkoopprijs 12,50 → 12,70", "artikel KM-1002: inkoopprijs 8,00 → 8,40",
                             "artikel KM-1003: inkoopprijs 5,00 → 5,10"]
    assert kept_columns(root) == {"S01": dict(zip(LIST_COLUMNS, OWN))} and not list(root.rglob("write_log.jsonl"))


def test_a_press_reads_the_list_a_fixed_number_of_times(tmp_path, madeup_out, monkeypatch):
    """Ruling Z18: the press does not read the list "In Exact invoeren" once for every change it prepares. ask_again reads the
    changes once before its loop, and again only after it wrote a mark; a press of 50 changes opens the file of the list as often
    as a press of 2."""
    import aid.execution.by_hand as list_module
    import aid.review.pricelist_page as tab_module
    counts = {"changes": 0, "file": 0}
    read_changes, read_file = tab_module.changes, list_module._rows

    def counted_changes(prepared):
        counts["changes"] += 1
        return read_changes(prepared)

    def counted_file(path, *kinds):
        counts["file"] += 1
        return read_file(path, *kinds)
    monkeypatch.setattr(tab_module, "changes", counted_changes)
    monkeypatch.setattr(list_module, "_rows", counted_file)

    def pressed(folder: Path, count: int) -> dict:
        """The reads of one press of `count` changes on a page of its own, with nothing on the list before."""
        page, _, clock = pricelist_page(folder, madeup_out)
        codes = [f"KM-{2000 + number}" for number in range(count)]
        filed(page, folder, "S01", rows=[[code, "Slang", "12,50"] for code in codes])
        clock.later(10)
        listed = price_list(folder, "lijst.csv", [(code, "12,70", "", "", "") for code in codes])
        view = viewed(page, listed, "S01")
        counts.update(changes=0, file=0)
        done = carried(page, listed, "S01", view["viewed"])
        assert done["note"] == f"{count} prijswijzigingen staan op de lijst In Exact invoeren."
        return dict(counts)
    few, many = pressed(tmp_path / "few", 2), pressed(tmp_path / "many", 50)
    assert many["changes"] == 1 and many == few
    # asked again: one read before the loop and one after each change whose marks were taken away
    root, prepared = tmp_path / "cases", tmp_path / "cases" / "prepared"
    codes = [f"KM-{3000 + number}" for number in range(50)]
    current = {code: "12.50" for code in codes}
    listed = write_csv(tmp_path / "lijst.csv", [item(code, "12.70") for code in codes])
    first = carry_out(listed, "S01", current, MAP, root, NOW)
    assert mark(prepared, [entry.entry_id for entry in first.updated[:3]], "entered", NOW + timedelta(minutes=1)) == 3
    counts.update(changes=0, file=0)
    again = carry_out(listed, "S01", current, MAP, root, NOW + timedelta(minutes=10), stored_at=(NOW + timedelta(minutes=5)).isoformat())
    assert counts["changes"] == 1 + 3 and on_list(again, root) == 50


def test_a_shared_row_names_the_other_supplier(tmp_path, madeup_out):
    """Ruling Z19 (a): the outcome of a row kept off the list as shared names the other supplier, as it is stored and as text; with
    several the first two, in the order they were first stored, and how many more."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    marked = "S02 <b>BV</b> & _Zn_"
    filed(page, tmp_path, "S01", "a.csv", [["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", "8,00"], ["KM-1003", "Ring", "5,00"]])
    filed(page, tmp_path, marked, "b.csv", [["KM-1001", "Slang", "11,90"]])
    clock.later(10)
    listed = price_list(tmp_path, "lijst.csv", [("KM-1001", "12,70", "", "", ""), ("KM-1002", "8,40", "", "", ""), ("KM-1003", "5,10", "", "", "")])
    view = viewed(page, listed, "S01")
    assert view["table"]["data"] == [["KM-1001", "12,50", "12,70", f"ook bij leverancier {marked}: voer deze prijs zelf in Exact in"],
                                     ["KM-1002", "8,00", "8,40", "prijs verandert"], ["KM-1003", "5,00", "5,10", "prijs verandert"]]
    filed(page, tmp_path, "S03", "c.csv", [["KM-1001", "Slang", "11,80"], ["KM-1002", "Klem", "7,90"]])
    view = viewed(page, listed, "S01")
    assert [row[3] for row in view["table"]["data"]] == [f"ook bij leverancier {marked}, S03: voer deze prijs zelf in Exact in",
                                                         "ook bij leverancier S03: voer deze prijs zelf in Exact in", "prijs verandert"]
    for supplier in ("S04", "S05"):
        filed(page, tmp_path, supplier, "d.csv", [["KM-1001", "Slang", "11,70"]])
    view = viewed(page, listed, "S01")
    assert view["table"]["data"][0] == ["KM-1001", "12,50", "12,70", f"ook bij leverancier {marked}, S03 en nog 2: voer deze prijs zelf in Exact in"]
    assert view["note"] == "1 prijs verandert, 0 blijven gelijk, 2 rijen overgeslagen."
    english = viewed(page, listed, "S01", language="en")
    assert [row[3] for row in english["table"]["data"]] == [f"also with supplier {marked}, S03 and 2 more: enter this price in Exact yourself",
                                                            "also with supplier S03: enter this price in Exact yourself", "price changes"]
    done = carried(page, listed, "S01", view["viewed"])
    assert (done["note"], done["table"]) == (ONE_LISTED, view["table"])
    # the names are part of what the view showed: one supplier more, and the press asks to view the list again
    clock.later(5)
    view = viewed(page, listed, "S01")
    filed(page, tmp_path, "S06", "e.csv", [["KM-1002", "Klem", "7,80"]])
    assert carried(page, listed, "S01", view["viewed"])["note"] == CHANGED


def removed(page, supplier: str | None, language: str = "nl") -> dict:
    """What "Inkoopprijzen van deze leverancier weghalen" gives for the supplier chosen or typed."""
    return called(page, "prices_removed", PRICES_REMOVED, supplier, language)


def test_a_supplier_filed_under_another_spelling_can_be_taken_away(tmp_path, madeup_out, monkeypatch):
    """Ruling Z19 (b), the check's probe typo.py: a supplier given twice under two spellings shares every article with itself, so
    no row of its price list goes on the list. The press beside the supplier's field takes the stored prices and the kept columns
    of one name away, whole or not at all, and nothing else: no row of the list In Exact invoeren changes. After that the rows of
    the list read "prijs verandert" and the press puts them on the list."""
    import aid.pricelist as module
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    rows = [["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", "8,00"], ["KM-1003", "Ring", "5,00"]]
    filed(page, tmp_path, "S01 B.V.", "a.csv", [*rows, ["KM-1009", "Eigen", "1,00"]])                # the name typed with dots once
    clock.later(10)
    own = price_list(tmp_path, "eigen.csv", [("KM-1009", "1,10", "", "", "")])
    assert carried(page, own, "S01 B.V.", viewed(page, own, "S01 B.V.")["viewed"])["note"] == ONE_LISTED
    clock.later(10)
    filed(page, tmp_path, "S01 BV", "a.csv", rows)                                                    # and as it is typed from then on
    clock.later(1)
    listed = price_list(tmp_path, "lijst.csv", [("KM-1001", "12,70", "", "", ""), ("KM-1002", "8,40", "", "", ""), ("KM-1003", "5,00", "", "", "")])
    view = viewed(page, listed, "S01 BV")
    assert view["table"]["data"] == [["KM-1001", "12,50", "12,70", shared_nl("S01 B.V.")], ["KM-1002", "8,00", "8,40", shared_nl("S01 B.V.")],
                                     ["KM-1003", "5,00", "5,00", "ongewijzigd"]]
    assert carried(page, listed, "S01 BV", view["viewed"])["note"] == NOTHING_LISTED
    columns = dict(zip(LIST_COLUMNS, OWN))
    assert kept_columns(root) == {"S01 B.V.": columns, "S01 BV": columns}
    before, listed_before = files_of(root / "exact"), files_of(root / "prepared", root / "pricelists")
    assert len(before) == 2 and rows_of(page) == ["artikel KM-1009: inkoopprijs 1,00 → 1,10"]
    # a write that fails in the middle leaves both files as they were, and the note says that it failed
    calls, replace = [], module.os.replace

    def held(source, target):
        calls.append(Path(target).name)
        if len(calls) == 2:
            raise PermissionError(13, "made-up: the file is held")
        return replace(source, target)
    with monkeypatch.context() as patched:
        patched.setattr(module.os, "replace", held)
        failed = removed(page, "S01 B.V.")
    assert failed["export_note"] == "Dit is niet gelukt (PermissionError)." and len(calls) >= 2
    assert all(failed[name] == hand.SAME for name in PRICES_REMOVED if name != "export_note") and files_of(root / "exact") == before
    # a name the aid does not hold, no name, and a name that is none: the tab's own sentences, and nothing is written
    assert removed(page, "S09")["export_note"] == ("Van S09 zijn nog geen inkoopprijzen opgenomen. Geef eerst de export van de artikelen van "
                                                   "deze leverancier uit Exact.")
    assert removed(page, " ")["export_note"] == "Kies eerst een leverancier." and removed(page, None)["export_note"] == "Kies eerst een leverancier."
    assert removed(page, "L" * 101)["export_note"] == "De naam van de leverancier is te lang of bevat tekens die niet in een naam horen."
    assert files_of(root / "exact") == before
    # the removal, by the stored name whatever the capitals and spaces typed
    out = removed(page, " s01 b.v. ")
    assert out["export_note"] == f"De opgenomen inkoopprijzen van {_literal('S01 B.V.')} zijn weggehaald. Wat al op de lijst In Exact invoeren staat, blijft staan."
    assert out["supplier"] == {"__type__": "update", "choices": ["S01 BV"], "value": None} and out["state"] == "Kies eerst een leverancier."
    assert out["list_part"] == HIDDEN_PART and out["table"]["data"] == [] and (out["note"], out["carry"], out["viewed"]) == ("", INACTIVE, None)
    assert out["notice"] == {"__type__": "update", "value": "", "visible": False}
    assert list(stored_prices(root)) == ["S01 BV"] and kept_columns(root) == {"S01 BV": columns}
    assert files_of(root / "prepared", root / "pricelists") == listed_before                      # no row of the list changed
    assert rows_of(page) == ["artikel KM-1009: inkoopprijs 1,00 → 1,10"]
    assert removed(page, "S01 B.V.")["export_note"].startswith(f"Van {_literal('S01 B.V.')} zijn nog geen inkoopprijzen opgenomen.")
    # after the removal the rows of the list change, and the press puts them on the list
    clock.later(5)
    view = viewed(page, listed, "S01 BV")
    assert view["table"]["data"] == [["KM-1001", "12,50", "12,70", "prijs verandert"], ["KM-1002", "8,00", "8,40", "prijs verandert"],
                                     ["KM-1003", "5,00", "5,00", "ongewijzigd"]]
    assert carried(page, listed, "S01 BV", view["viewed"])["note"] == "2 prijswijzigingen staan op de lijst In Exact invoeren."
    assert rows_of(page) == ["artikel KM-1001: inkoopprijs 12,50 → 12,70", "artikel KM-1002: inkoopprijs 8,00 → 8,40",
                             "artikel KM-1009: inkoopprijs 1,00 → 1,10"]
    # in English, with the name shown as text
    marked = "S07 <b>x</b>"
    filed(page, tmp_path, marked, "g.csv", [["KM-7001", "Bout", "0,10"]])
    assert removed(page, marked, "en")["export_note"] == (f"The stored purchase prices of {_literal(marked)} were removed. What already stands "
                                                          "on the list Enter in Exact stays.")
    assert list(stored_prices(root)) == ["S01 BV"]


OLDER_NL = ("Let op: voor {articles} staat op de lijst In Exact invoeren nog de prijs van een eerdere prijslijst van deze leverancier. Deze "
            "lijst vraagt een andere prijs, die de assistent niet op de lijst zet: haal die regel daar van de lijst en voer de goede prijs "
            "zelf in Exact in.")
OLDER_EN = ("Note: for {articles} the list Enter in Exact still holds the price of an earlier price list of this supplier. This list asks "
            "another price, which the assistant does not put on the list: take that row off the list there and enter the right price in "
            "Exact yourself.")
TICKED_NL, TICKED_EN = "al afgevinkt op de lijst In Exact invoeren", "already ticked on the list Enter in Exact"
DROPPED_NL = "van de lijst gehaald: zet de regel daar terug als de prijs toch moet worden ingevoerd"
DROPPED_EN = "taken off the list: put the row back there if the price must be entered after all"


def test_an_older_change_that_a_held_back_row_leaves_on_the_list_is_named(tmp_path, madeup_out):
    """Ruling Z20, the check's probe z3.py run P1: when a row is kept off the list (shared, or differs strongly) while the list
    still holds an open change of that article that an earlier price list of this supplier asked, with another price, the count
    line of the view and the note after the press name the article: the buyer would otherwise enter the older price."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    filed(page, tmp_path, "A", "a.csv", [["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", "8,00"], ["KM-1003", "Ring", "5,00"]])
    clock.later(1)
    first = price_list(tmp_path, "lijst A.csv", [("KM-1001", "12,70", "", "", ""), ("KM-1002", "8,40", "", "", "")])
    view = viewed(page, first, "A")
    assert view["note"] == "2 prijzen veranderen, 0 blijven gelijk, 0 rijen overgeslagen."
    assert carried(page, first, "A", view["viewed"])["note"] == "2 prijswijzigingen staan op de lijst In Exact invoeren."
    clock.later(1)
    filed(page, tmp_path, "B", "b.csv", [["KM-1001", "Slang", "11,90"]])                      # B's export came; A's row stays on the list
    clock.later(1)
    newer = price_list(tmp_path, "lijst A nieuw.csv", [("KM-1001", "12,90", "", "", ""), ("KM-1002", "80,00", "", "", ""), ("KM-1003", "5,10", "", "", "")])
    view = viewed(page, newer, "A")
    assert view["table"]["data"] == [["KM-1001", "12,50", "12,90", shared_nl("B")], ["KM-1002", "8,00", "80,00", DIFFERS_NL],
                                     ["KM-1003", "5,00", "5,10", "prijs verandert"]]
    both = f"{_literal('KM-1001')}, {_literal('KM-1002')}"
    assert view["note"] == f"1 prijs verandert, 0 blijven gelijk, 2 rijen overgeslagen. {DIFFERS_LINE} {OLDER_NL.format(articles=both)}"
    english = viewed(page, newer, "A", language="en")
    assert english["note"].startswith("1 price changes, 0 stay the same, 2 rows passed over. ") and english["note"].endswith(OLDER_EN.format(articles=both))
    for language, counted, sentence in (("nl", ONE_LISTED, OLDER_NL), ("en", "1 price change is on the list Enter in Exact.", OLDER_EN)):
        clock.later(1)
        view = viewed(page, newer, "A", language=language)
        done = carried(page, newer, "A", view["viewed"], language=language)
        assert done["note"] == f"{counted} {sentence.format(articles=both)}" and done["table"] == view["table"]
    assert rows_of(page) == ["artikel KM-1001: inkoopprijs 12,50 → 12,70", "artikel KM-1002: inkoopprijs 8,00 → 8,40",
                             "artikel KM-1003: inkoopprijs 5,00 → 5,10"]                        # the older prices are still asked
    # a row that asks the price the list holds already leaves nothing wrong on the list
    same = price_list(tmp_path, "lijst A zelfde.csv", [("KM-1001", "12,70", "", "", ""), ("KM-1003", "5,20", "", "", "")])
    view = viewed(page, same, "A")
    assert view["table"]["data"] == [["KM-1001", "12,50", "12,70", shared_nl("B")], ["KM-1003", "5,00", "5,20", "prijs verandert"]]
    assert view["note"] == "1 prijs verandert, 0 blijven gelijk, 1 rij overgeslagen."        # and a row that goes on the list replaces its older one
    # once the older row is ticked, nothing of it is left on the list to name
    clock.later(1)
    older = [value for text, value in hand.shown(page, "list_opened", "nl")["rows"]["choices"] if "KM-1001" in text]
    assert hand.shown(page, "changes_entered", older, "nl")["note"] == "1 wijziging genoteerd als ingevoerd in Exact."
    clock.later(1)
    view = viewed(page, newer, "A")
    assert view["table"]["data"][0] == ["KM-1001", "12,70", "12,90", shared_nl("B")]
    assert view["note"] == (f"1 prijs verandert, 0 blijven gelijk, 2 rijen overgeslagen. {DIFFERS_LINE} "
                            f"{OLDER_NL.format(articles=_literal('KM-1002'))}")


def test_no_price_list_takes_another_suppliers_open_change_off_the_list(tmp_path, madeup_out):
    """Ruling Z21 (the check's probes z3b.py run R1 and z3.py runs P2 and P4): the tab knows another supplier by an export given on
    the tab, or by an open change on the list. A row that would change is kept off the list as shared, with that supplier named,
    when the list holds an open change of its article that a case of another supplier or another supplier's price list asked, also
    when no export of that supplier is stored: at another price the row would take that change off the list, and at the same price
    it would join it, so that one tick counted for two suppliers."""
    # a confirmation's case of S08 asked the article price, open on the list; no export of S08 is stored
    page, d, clock = pricelist_page(tmp_path, madeup_out, cases=("MU08",))
    root = d.settings.cases_root
    [asked] = [change for change in open_changes(root / "prepared") if change.kind == "article_price"]
    assert (asked.article_code, asked.new_value) == ("KM-0801", "12.70")
    filed(page, tmp_path, "S01", "s01.csv", [["KM-0801", "Koppeling", "12,50"]])
    assert list(stored_prices(root)) == ["S01"]
    before, rows_before = files_of(root / "prepared"), rows_of(page)
    for price in ("12,90", "12,70"):                                                          # another price, and the same price
        clock.later(5)
        listed = price_list(tmp_path, "lijst.csv", [("KM-0801", price, "", "", "")])
        view = viewed(page, listed, "S01")
        assert view["table"]["data"] == [["KM-0801", "12,50", price, shared_nl("S08")]], price
        assert view["note"] == "0 prijzen veranderen, 0 blijven gelijk, 1 rij overgeslagen."
        done = carried(page, listed, "S01", view["viewed"])
        assert (done["note"], done["table"]) == (NOTHING_LISTED, view["table"])
        assert files_of(root / "prepared") == before and rows_of(page) == rows_before         # the row of S08's case as it stood
    assert viewed(page, listed, "S01", language="en")["table"]["data"] == [
        ["KM-0801", "12.50", "12.70", "also with supplier S08: enter this price in Exact yourself"]]
    # a supplier known by its export is named before one known by the list alone
    filed(page, tmp_path, "S14", "s14.csv", [["KM-0801", "Koppeling", "12,40"]])
    assert viewed(page, listed, "S01")["table"]["data"] == [["KM-0801", "12,50", "12,70", shared_nl("S14, S08")]]
    # ruling Z34: the supplier's own list asked first and the case of another supplier joined that change: the newer list is kept off
    joined, d, clock = pricelist_page(tmp_path / "joined", madeup_out, cases=("MU08",))
    root = d.settings.cases_root
    [case_id] = [folder.name for folder in root.iterdir() if folder.name.startswith("250108_")]
    filed(joined, tmp_path, "S01", "s01.csv", [["KM-1001", "Slang", "12,50"]])
    clock.later(1)
    own = price_list(tmp_path, "eigen.csv", [("KM-1001", "12,70", "", "", "")])
    assert carried(joined, own, "S01", viewed(joined, own, "S01")["viewed"])["note"] == ONE_LISTED
    clock.later(1)
    PreparedWriter(root / "prepared").apply(WriteOp(kind="article_price", po_number="", version=None, article_code="KM-1001", new_value="12.70"),
                                            case_id, "R04", {}, clock())
    [both] = [change for change in open_changes(root / "prepared") if change.article_code == "KM-1001"]
    assert len(both.ids) == 2 and both.case_ids[1] == case_id and both.case_ids[0].startswith("pricelist_S01_")
    before = files_of(root / "prepared")
    clock.later(1)
    newer = price_list(tmp_path, "nieuwer.csv", [("KM-1001", "12,90", "", "", "")])
    view = viewed(joined, newer, "S01")
    assert view["table"]["data"] == [["KM-1001", "12,50", "12,90", shared_nl("S08")]]
    assert view["note"] == f"0 prijzen veranderen, 0 blijven gelijk, 1 rij overgeslagen. {OLDER_NL.format(articles=_literal('KM-1001'))}"
    assert carried(joined, newer, "S01", view["viewed"])["note"].startswith(NOTHING_LISTED) and files_of(root / "prepared") == before
    # ruling Z34: the supplier of a case is that supplier, whatever the capitals of the name on the tab
    capitals, d, clock = pricelist_page(tmp_path / "capitals", madeup_out, cases=("MU08",))
    filed(capitals, tmp_path, "s08", "s08.csv", [["KM-0801", "Koppeling", "12,50"]])
    assert list(stored_prices(d.settings.cases_root)) == ["s08"]
    clock.later(1)
    higher = price_list(tmp_path, "hoger.csv", [("KM-0801", "12,90", "", "", "")])
    assert viewed(capitals, higher, "s08")["table"]["data"] == [["KM-0801", "12,50", "12,90", "prijs verandert"]]
    # a change of A's list on the list, A's newer export without the article, then B's list
    other, d, clock = pricelist_page(tmp_path / "other", madeup_out)
    root = d.settings.cases_root
    filed(other, tmp_path, "A", "a.csv", [["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", "8,00"]])
    clock.later(1)
    first = price_list(tmp_path, "lijst A.csv", [("KM-1001", "12,70", "", "", "")])
    pressed = clock()
    assert carried(other, first, "A", viewed(other, first, "A")["viewed"])["note"] == ONE_LISTED
    clock.later(1)
    filed(other, tmp_path, "A", "a nieuw.csv", [["KM-1002", "Klem", "8,00"]])                 # A's newer export no longer holds the article
    clock.later(1)
    filed(other, tmp_path, "B", "b.csv", [["KM-1001", "Slang", "11,90"]])
    assert also_held(stored_prices(root), "B") == {"KM-1002": ["A"]}                          # by the exports, B alone holds the article
    before = files_of(root / "prepared")
    for price in ("12,10", "12,70"):
        clock.later(5)
        second = price_list(tmp_path, "lijst B.csv", [("KM-1001", price, "", "", "")])
        view = viewed(other, second, "B")
        assert view["table"]["data"] == [["KM-1001", "11,90", price, shared_nl("A")]], price
        assert carried(other, second, "B", view["viewed"])["note"] == NOTHING_LISTED
        assert files_of(root / "prepared") == before and rows_of(other) == ["artikel KM-1001: inkoopprijs 12,50 → 12,70"]
    [change] = open_changes(root / "prepared")
    assert change.case_ids == [f"pricelist_A_{pressed:%Y%m%dT%H%M%S}"] and len(change.ids) == 1
    # once A's change is ticked the list holds no open change of another supplier, and B's own price can go on the list
    assert tick_all(other, clock) == "1 wijziging genoteerd als ingevoerd in Exact."
    clock.later(5)
    second = price_list(tmp_path, "lijst B.csv", [("KM-1001", "12,10", "", "", "")])
    view = viewed(other, second, "B")
    assert view["table"]["data"] == [["KM-1001", "11,90", "12,10", "prijs verandert"]]
    assert carried(other, second, "B", view["viewed"])["note"] == ONE_LISTED
    assert [(one.article_code, one.new_value, one.state) for one in changes(root / "prepared")[0]] == [
        ("KM-1001", "12.70", "entered"), ("KM-1001", "12.10", "open")]                        # A's ticked change is not replaced
    # a supplier's own earlier list and a case that names no supplier are no other supplier
    clock.later(5)
    again = price_list(tmp_path, "lijst B weer.csv", [("KM-1001", "12,20", "", "", "")])
    assert viewed(other, again, "B")["table"]["data"] == [["KM-1001", "11,90", "12,20", "prijs verandert"]]


def test_a_price_no_press_can_put_on_the_list_is_not_shown_as_changing(tmp_path, madeup_out):
    """Ruling Z22, the check's probe z5.py runs Q7 and Q8: a row whose change the list holds as ticked after the supplier's export,
    or as taken off the list, has an outcome of its own in the view and in the table after the press, counts among the rows passed
    over, and is not prepared: the tab said "prijs verandert" and the press "0 prijswijzigingen"."""
    # Q7: the case of another supplier asked the price and it was ticked after this supplier's export
    page, d, clock = pricelist_page(tmp_path, madeup_out, cases=("MU08",))
    root = d.settings.cases_root
    filed(page, tmp_path, "S01", "s01.csv", [["KM-0801", "Koppeling", "12,50"], ["KM-1001", "Slang", "12,50"]])
    clock.later(1)
    [asked] = [change for change in open_changes(root / "prepared") if change.kind == "article_price"]
    assert mark(root / "prepared", asked.ids, "entered", clock()) == 1
    clock.later(1)
    listed = price_list(tmp_path, "lijst.csv", [("KM-0801", "12,70", "", "", ""), ("KM-1001", "12,70", "", "", "")])
    for language, ticked, counts, counted in (
            ("nl", TICKED_NL, "1 prijs verandert, 0 blijven gelijk, 1 rij overgeslagen.", ONE_LISTED),
            ("en", TICKED_EN, "1 price changes, 0 stay the same, 1 row passed over.", "1 price change is on the list Enter in Exact.")):
        view = viewed(page, listed, "S01", language=language)
        price = "12,70" if language == "nl" else "12.70"
        assert view["table"]["data"][0] == ["KM-0801", price.replace("70", "50"), price, ticked] and view["note"] == counts
        before = len(hand.prepared_lines(root))
        done = carried(page, listed, "S01", view["viewed"], language=language)
        assert (done["note"], done["table"]) == (counted, view["table"]) and len(hand.prepared_lines(root)) == before + 1
        clock.later(1)
    assert current_prices(root, "S01")[0]["KM-0801"] == "12.50"                               # the tick is S08's, as before
    # the value ticked for the other supplier holds also after another value was asked and taken off the list in between
    between = price_list(tmp_path, "tussen.csv", [("KM-0801", "12,90", "", "", "")])
    assert carried(page, between, "S01", viewed(page, between, "S01")["viewed"])["note"] == ONE_LISTED
    clock.later(1)
    taken = [value for text, value in hand.shown(page, "list_opened", "nl")["rows"]["choices"] if text.startswith("artikel KM-0801")]
    assert hand.shown(page, "changes_dropped", taken, "nl")["note"] == NL["hand_dropped_one"]
    clock.later(1)
    only = price_list(tmp_path, "alleen.csv", [("KM-0801", "12,70", "", "", "")])
    view = viewed(page, only, "S01")
    assert view["table"]["data"] == [["KM-0801", "12,50", "12,70", TICKED_NL]]
    assert viewed(page, between, "S01")["table"]["data"] == [["KM-0801", "12,50", "12,90", DROPPED_NL]]
    before = files_of(root / "prepared")
    assert carried(page, only, "S01", view["viewed"])["note"] == NOTHING_LISTED and files_of(root / "prepared") == before
    # Q8: the buyer took the change off the list; the same price again is not asked until the row is put back there
    other, d, clock = pricelist_page(tmp_path / "other", madeup_out)
    root = d.settings.cases_root
    filed(other, tmp_path, "A", "a.csv", [["KM-1001", "Slang", "12,50"]])
    clock.later(1)
    first = price_list(tmp_path, "lijst A.csv", [("KM-1001", "12,70", "", "", "")])
    assert carried(other, first, "A", viewed(other, first, "A")["viewed"])["note"] == ONE_LISTED
    clock.later(1)
    rows = [value for _, value in hand.shown(other, "list_opened", "nl")["rows"]["choices"]]
    assert hand.shown(other, "changes_dropped", rows, "nl")["note"] == NL["hand_dropped_one"]
    clock.later(1)
    view = viewed(other, first, "A")
    assert view["table"]["data"] == [["KM-1001", "12,50", "12,70", DROPPED_NL]]
    assert view["note"] == "0 prijzen veranderen, 0 blijven gelijk, 1 rij overgeslagen." and view["carry"] == ACTIVE
    before = files_of(root / "prepared")
    done = carried(other, first, "A", view["viewed"])
    assert (done["note"], done["table"]) == (NOTHING_LISTED, view["table"]) and files_of(root / "prepared") == before
    assert viewed(other, first, "A", language="en")["table"]["data"] == [["KM-1001", "12.50", "12.70", DROPPED_EN]]
    # another price is a new question, and goes on the list
    higher = price_list(tmp_path, "lijst A hoger.csv", [("KM-1001", "12,90", "", "", "")])
    assert viewed(other, higher, "A")["table"]["data"] == [["KM-1001", "12,50", "12,90", "prijs verandert"]]
    # the row put back on the list there: the price is asked again, and a list that asks it joins that row
    under = hand.done_of(other, "done_opened", "nl", None)
    hand.done_of(other, "change_reopened", hand.chosen_of(under, "KM-1001"), "nl")
    assert rows_of(other) == ["artikel KM-1001: inkoopprijs 12,50 → 12,70"]
    clock.later(1)
    view = viewed(other, first, "A")
    assert view["table"]["data"] == [["KM-1001", "12,50", "12,70", "prijs verandert"]]
    assert carried(other, first, "A", view["viewed"])["note"] == ONE_LISTED and len(open_changes(root / "prepared")) == 1
    # a view made before a row was taken off the list is not carried out after it: the outcome changed
    clock.later(1)
    view = viewed(other, first, "A")
    rows = [value for _, value in hand.shown(other, "list_opened", "nl")["rows"]["choices"]]
    assert hand.shown(other, "changes_dropped", rows, "nl")["note"] == NL["hand_dropped_one"]
    assert carried(other, first, "A", view["viewed"])["note"] == CHANGED


def test_the_lines_that_no_test_held(tmp_path, madeup_out):
    """Ruling Z24, three lines of the page that no test held (the check's X43 and the two survivors M33 and M62 that are not
    equivalent). Kept columns that cannot be opened do not stop the choice of a list: the fields come as for a first list. A newer
    export that changes only a price the list does not hold ends the view all the same: the press carries out the view or nothing.
    And the kept columns are found by the kept supplier's name when the stored prices no longer know it."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    filed(page, tmp_path, "S01")
    clock.later(10)
    titles = ("Artikelnummer", "Nettoprijs", "Per", "Van", "Tot")
    columns = ["Artikelnummer", "Nettoprijs", "", "", ""]
    listed = price_list(tmp_path, "lijst.csv", [("KM-1001", "12,70", "", "", "")], titles=titles)
    # the current prices are part of the view: a price of an article that the list does not hold changed (KM-1002)
    view = viewed(page, listed, "S01", columns)
    assert view["table"]["data"] == [["KM-1001", "12,50", "12,70", "prijs verandert"]]
    clock.later(1)
    filed(page, tmp_path, "S01", "artikelen nieuw.csv", [["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", "8,10"]])
    clock.later(1)
    done = carried(page, listed, "S01", view["viewed"], columns)
    assert (done["note"], done["carry"], done["viewed"]) == (CHANGED, INACTIVE, None) and not (root / "prepared").exists()
    # the columns kept under the kept supplier's name are found when the stored prices no longer hold that name
    clock.later(1)
    assert carried(page, listed, "S01", viewed(page, listed, "S01", columns)["viewed"], columns)["note"] == ONE_LISTED
    assert kept_columns(root) == {"S01": {"item_code": "Artikelnummer", "price": "Nettoprijs"}}
    (root / "exact" / "article_prices.json").write_text("{", encoding="utf-8")                # the stored prices can no longer be read
    clock.later(10)
    assert filed(page, tmp_path, "s01", "artikelen weer.csv")["export_note"] == "Inkoopprijzen van s01 opgenomen: 2 artikelen; 0 rijen overgeslagen."
    assert list(stored_prices(root)) == ["s01"]
    chosen = called(page, "pricelist_file_chosen", LIST_CHOSEN, listed, "s01", "nl")
    assert [chosen[name]["value"] for name in COLUMN_FIELDS] == columns and chosen["note"] == ""
    # kept columns that cannot be opened: the fields come as for a first list, and nothing is said
    kept_file = root / "exact" / "pricelist_columns.json"
    kept_file.unlink()
    kept_file.mkdir()                                                                         # a folder in the place of the file
    with pytest.raises(OSError):
        kept_columns(root)
    chosen = called(page, "pricelist_file_chosen", LIST_CHOSEN, listed, "s01", "nl")
    assert [chosen[name]["value"] for name in COLUMN_FIELDS] == [""] * 5 and chosen["note"] == ""
    assert chosen["column:price"]["choices"] == [("(staat niet in het bestand)", ""), *[(title, title) for title in titles]]
    own = called(page, "pricelist_file_chosen", LIST_CHOSEN, price_list(tmp_path, "eigen.csv", LISTED), "s01", "nl")
    assert [own[name]["value"] for name in COLUMN_FIELDS] == OWN


# --- the third review round (rulings Z30, Z31 and Z33) ---

def test_in_a_comma_file_the_tab_reads_a_row_only_with_the_titles_number_of_cells(tmp_path, madeup_out):
    """Ruling Z30, the check's c16b files on the tab: "KM-1001,12,70,," under four titles was read as 12 ("prijs verandert") and
    8,40 as 8 ("ongewijzigd"), and so were rows shorter than the titles; the export of articles stored 12 and 8. In a file cut at
    commas a row with another number of cells than the row of titles refuses the file, with the row. A comma file whose rows all
    have the titles' number of cells and a semicolon file with short rows are read as before."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    filed(page, tmp_path, "S01")
    stored = hand.files_of(root)
    clock.later(10)
    columns = ["Artikelnummer", "Nettoprijs", "", "", ""]
    room = uploaded(tmp_path, "ruimte.csv", b"Artikelnummer,Nettoprijs,Staffel,Geldig tot\r\nKM-1001,12,70,,\r\nKM-1002,8,40,,\r\n")
    short = uploaded(tmp_path, "kort.csv", b"Artikelnummer,Nettoprijs,Staffel,Geldig tot\nKM-1001,12.70,,\nKM-1002,8,40\n")
    for listed, row in ((room, 2), (short, 3)):
        for language, sentence in (("nl", UNEVEN_NL.format(n=row)), ("en", UNEVEN_EN.format(n=row))):
            chosen = called(page, "pricelist_file_chosen", LIST_CHOSEN, listed, "S01", language)
            assert chosen["note"] == sentence and chosen["column:price"]["choices"] == [(STRINGS[language]["exact_export_no_column"], "")]
            view = viewed(page, listed, "S01", columns, language)
            assert (view["note"], view["table"]["data"], view["carry"], view["viewed"]) == (sentence, [], INACTIVE, None)
    export = uploaded(tmp_path, "export.csv", b"Artikel,Omschrijving,Inkoopprijs,Opmerking\nKM-1001,Slang,12,70,\nKM-1002,Klem,8,40,\n")
    for language, sentence in (("nl", UNEVEN_NL.format(n=2)), ("en", UNEVEN_EN.format(n=2))):
        assert called(page, "prices_filed", PRICES_FILED, export, "S01", language, None)["export_note"] == sentence
    assert hand.files_of(root) == stored and current_prices(root, "S01")[0] == {"KM-1001": "12.50", "KM-1002": "8.00"}
    even = uploaded(tmp_path, "even.csv", b'Artikelnummer,Nettoprijs,Staffel,Geldig tot\r\nKM-1001,"12,70",,\r\n,,,\r\nKM-1002,8.40,,\r\n')
    changing = [["KM-1001", "12,50", "12,70", "prijs verandert"], ["KM-1002", "8,00", "8,40", "prijs verandert"]]
    assert viewed(page, even, "S01", columns)["table"]["data"] == changing
    semicolons = uploaded(tmp_path, "puntkomma.csv", b"Artikelnummer;Nettoprijs;Staffel;Geldig tot\r\nKM-1001;12,70\r\nKM-1002;8,40;;;;\r\n")
    assert viewed(page, semicolons, "S01", columns)["table"]["data"] == changing


def test_a_workbook_with_data_on_a_hidden_sheet_only_is_told_so_on_the_tab(tmp_path, madeup_out):
    """Ruling Z33: a workbook whose only data stands on a hidden sheet was "het bestand is leeg" as a price list and a file with
    other columns as an export. Both fields say that the data stands on a hidden sheet, and what to do; nothing is stored."""
    page, d, clock = pricelist_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    filed(page, tmp_path, "S01")
    stored = hand.files_of(root)
    head = ["item_code", "price"]
    listed = sheets_file(tmp_path, "lijst.xlsx", [("2025", [head, ["KM-1001", 12.7]], "hidden"), ("Blad2", [], "visible")])
    export = sheets_file(tmp_path, "artikelen.xlsx", [("Artikelen", [TITLES, ["KM-1001", "Slang", 12.7]], "veryHidden"), ("Blad2", [[" "]], "visible")])
    for language, sentence in (("nl", HIDDEN_NL), ("en", HIDDEN_EN)):
        chosen = called(page, "pricelist_file_chosen", LIST_CHOSEN, listed, "S01", language)
        assert chosen["note"] == sentence and chosen["column:price"]["value"] == ""
        view = viewed(page, listed, "S01", ["item_code", "price", "", "", ""], language)
        assert (view["note"], view["table"]["data"], view["carry"], view["viewed"]) == (sentence, [], INACTIVE, None)
        assert called(page, "prices_filed", PRICES_FILED, export, "S01", language, None)["export_note"] == sentence
    assert hand.files_of(root) == stored


# --- the texts and the documents ---

# Every text that the brief and its amendments give for the tab, for part 3 of the block and for the press on the tab In Exact
# invoeren, Dutch and English, word for word.
TEXTS = {
    "exact_part_articles": ("3. Inkoopprijzen van artikelen als export uit Exact (voor de prijslijst)",
                            "3. Purchase prices of articles as an export from Exact (for the price list)"),
    "exact_articles_intro": ("Kies een voorbeeld van de export van artikelen uit Exact en wijs de kolommen aan. Heeft de export geen kolom met de "
                             "leverancier, dan kiest u de leverancier wanneer u de export op het tabblad Prijslijst geeft.",
                             "Choose a sample of the export of articles from Exact and point out the columns. If the export has no column with "
                             "the supplier, you choose the supplier when you give the export on the tab Price list."),
    "exact_articles_field:item_code": ("Artikelcode (verplicht)", "Item code (required)"),
    "exact_articles_field:price": ("Inkoopprijs (verplicht)", "Purchase price (required)"),
    "exact_articles_field:supplier": ("Leverancier", "Supplier"),
    "exact_test": ("Testen", "Test"), "exact_articles_save": ("Artikelkolommen opslaan", "Save the article columns"),
    "exact_articles_left:no_item_code": ("zonder artikelcode", "no item code"),
    "exact_articles_left:no_price": ("prijs niet te lezen", "price cannot be read"),
    "exact_articles_left:twice": ("artikel dubbel", "article twice"),
    "exact_articles_left:no_supplier": ("zonder leverancier", "no supplier"),
    "pricelist_tab": ("Prijslijst", "Price list"),
    "pricelist_unset": ("De kolommen van de export van de artikelen zijn nog niet ingesteld. Dat gebeurt bij de installatie: Instellingen, "
                        "Koppeling met Exact, deel 3.",
                        "The columns of the export of the articles are not set yet. That is done at the installation: Settings, Connection to "
                        "Exact, part 3."),
    "pricelist_intro": ("Een prijslijst van een leverancier vergelijkt u hier met de inkoopprijzen in Exact. De assistent heeft daarvoor de "
                        "export van de artikelen van die leverancier uit Exact nodig. Wat verandert, zet u met een druk op de knop op de lijst "
                        "In Exact invoeren; in Exact verandert de assistent niets.",
                        "Here you compare a supplier's price list with the purchase prices in Exact. For that the assistant needs the export of "
                        "that supplier's articles from Exact. What changes, you put on the list Enter in Exact with one press; the assistant "
                        "changes nothing in Exact."),
    "pricelist_supplier": ("Leverancier", "Supplier"),
    "pricelist_supplier_help": ("Kies de leverancier, of typ de naam bij een nieuwe leverancier.",
                                "Choose the supplier, or type the name for a new supplier."),
    "pricelist_state_none": ("Van {supplier} zijn nog geen inkoopprijzen opgenomen. Geef eerst de export van de artikelen van deze leverancier "
                             "uit Exact.",
                             "No purchase prices of {supplier} are filed yet. First give the export of this supplier's articles from Exact."),
    "pricelist_choose_supplier": ("Kies eerst een leverancier.", "Choose a supplier first."),
    "pricelist_export": ("Export van de artikelen uit Exact (Excel of csv)", "Export of the articles from Exact (Excel or CSV)"),
    "pricelist_file_prices": ("Inkoopprijzen opnemen", "File purchase prices"),
    "pricelist_refused:type": ("{name} is niet aangenomen: de assistent leest hier Excel (.xlsx) en csv.",
                               "{name} was not accepted: the assistant reads Excel (.xlsx) and CSV here."),
    "files_refused:empty": ("{name} is niet aangenomen: het bestand is leeg.", "{name} was not accepted: the file is empty."),
    "pricelist_refused:columns": ("De kolommen van dit bestand zijn anders dan ingesteld: controleer Instellingen, Koppeling met Exact, deel 3.",
                                  "The columns of this file differ from those set: check Settings, Connection to Exact, part 3."),
    "pricelist_refused:supplier": ("Kies eerst de leverancier van wie deze artikelen zijn.", "First choose the supplier whose articles these are."),
    "pricelist_list": ("Prijslijst (Excel of csv)", "Price list (Excel or CSV)"),
    "pricelist_column:item_code": ("Kolom met de artikelcode", "Column with the item code"),
    "pricelist_column:price": ("Kolom met de prijs", "Column with the price"),
    "pricelist_column:basis": ("Kolom met de prijs per (mag leeg)", "Column with the price per (may be empty)"),
    "pricelist_column:valid_from": ("Geldig vanaf (mag leeg)", "Valid from (may be empty)"),
    "pricelist_column:valid_to": ("Geldig tot (mag leeg)", "Valid to (may be empty)"),
    "pricelist_view": ("Bekijken", "View"),
    "pricelist_carry_out": ("Op de lijst In Exact invoeren zetten", "Put on the list Enter in Exact"),
    "pricelist_header:article": ("Artikel", "Article"), "pricelist_header:current": ("Inkoopprijs in Exact", "Purchase price in Exact"),
    "pricelist_header:list": ("Prijs op de lijst", "Price on the list"), "pricelist_header:outcome": ("Uitkomst", "Outcome"),
    "pricelist_outcome:changes": ("prijs verandert", "price changes"), "pricelist_outcome:unchanged": ("ongewijzigd", "unchanged"),
    "pricelist_outcome:unknown_article": ("artikel niet bekend in Exact", "article not known in Exact"),
    "pricelist_outcome:unreadable_price": ("prijs niet te lezen", "price cannot be read"),
    "pricelist_outcome:other_basis": ("prijs per ander aantal: niet vergeleken", "price per another number: not compared"),
    "pricelist_outcome:not_yet_valid": ("nog niet geldig", "not valid yet"), "pricelist_outcome:expired": ("verlopen", "expired"),
    "pricelist_outcome:duplicate": ("dubbele rij", "duplicate row"), "pricelist_outcome:no_item_code": ("geen artikelcode", "no item code"),
    "pricelist_carried_many": ("{n} prijswijzigingen staan op de lijst In Exact invoeren.", "{n} price changes are on the list Enter in Exact."),
    "pricelist_expired": ("Sommige rijen zijn verlopen: vraag de leverancier om een actuele lijst.",
                          "Some rows are expired: ask the supplier for a current list."),
    "pricelist_changed": ("De gegevens zijn veranderd sinds u de lijst bekeek. Bekijk de lijst opnieuw.",
                          "The data changed since you viewed the list. View the list again."),
    "hand_pricelist_entered": ("Alle artikelprijzen van deze prijslijst zijn ingevoerd", "All article prices of this price list have been entered"),
}
# The texts that the brief leaves open and the tab needs: a count of one in the singular, the sample's label, two sentences for a column
# that is missing, three outcomes that the import has and the brief does not name, a row without an item code, the stored file that
# cannot be read, the label of the setting, and the label of the dropdown of the press.
BEYOND = {
    "exact_articles_sample": ("Voorbeeld: een export van artikelen uit Exact", "Sample: an export of articles from Exact"),
    "exact_articles_columns": ("Kolommen van de export van de artikelen", "Columns of the export of the articles"),
    "exact_articles_price": ("Inkoopprijs", "Purchase price"),
    "exact_articles_problem:price": ("Kies de kolom met de inkoopprijs.", "Choose the column with the purchase price."),
    "exact_articles_problem:none": ("Het voorbeeld gaf geen artikel: geen rij onder de kolomnamen heeft een artikelcode en een prijs die te lezen "
                                    "is.", "The sample gave no article: no row below the column titles has an item code and a price that can "
                                    "be read."),
    "exact_articles_read": ("{articles} gelezen; {passed}.", "{articles} read; {passed}."),
    "exact_articles_read_reasons": ("{articles} gelezen; {passed} ({reasons}).", "{articles} read; {passed} ({reasons})."),
    "pricelist_stored_unreadable": ("De opgenomen inkoopprijzen kunnen niet worden gelezen. Geef de export van de artikelen opnieuw.",
                                    "The filed purchase prices cannot be read. Give the export of the articles again."),
    "pricelist_state": ("Inkoopprijzen van {supplier} opgenomen op {time} uit {file}: {articles}.",
                        "Purchase prices of {supplier} filed on {time} from {file}: {articles}."),
    "pricelist_filed": ("Inkoopprijzen van {supplier} opgenomen: {articles}; {passed}.", "Purchase prices of {supplier} filed: {articles}; {passed}."),
    "pricelist_filed_suppliers": ("Inkoopprijzen opgenomen van {m} leveranciers: {articles}; {passed}.",
                                  "Purchase prices filed of {m} suppliers: {articles}; {passed}."),
    "pricelist_articles_one": ("1 artikel", "1 article"), "pricelist_articles_many": ("{n} artikelen", "{n} articles"),
    "pricelist_passed_one": ("1 rij overgeslagen", "1 row passed over"), "pricelist_passed_many": ("{k} rijen overgeslagen", "{k} rows passed over"),
    "pricelist_problem:price": ("Kies de kolom met de prijs.", "Choose the column with the price."),
    "pricelist_outcome:unreadable_valid_from": ("geldig vanaf niet te lezen", "valid from cannot be read"),
    "pricelist_outcome:unreadable_valid_to": ("geldig tot niet te lezen", "valid to cannot be read"),
    "pricelist_outcome:failed": ("niet gelukt", "failed"),
    "pricelist_row": ("rij {n}", "row {n}"),
    "pricelist_counts": ("{changes}, {same}, {passed}.", "{changes}, {same}, {passed}."),
    "pricelist_changes_one": ("1 prijs verandert", "1 price changes"), "pricelist_changes_many": ("{a} prijzen veranderen", "{a} prices change"),
    "pricelist_same_one": ("1 blijft gelijk", "1 stays the same"), "pricelist_same_many": ("{b} blijven gelijk", "{b} stay the same"),
    "pricelist_carried_one": ("1 prijswijziging staat op de lijst In Exact invoeren.", "1 price change is on the list Enter in Exact."),
    "hand_pricelist": ("Prijslijst", "Price list"),
}


# The texts that the rulings of the review round give, word for word (the names of the sheets stand at {names}), and the one
# sentence the rulings leave open: the note that names the articles whose change could not be prepared.
RULED = {
    "exact_export_refused:several_sheets": ("Dit Excel-bestand heeft meer dan één werkblad met gegevens ({names}). De assistent leest één werkblad: sla "
                                 "het werkblad dat u bedoelt op als een eigen bestand en geef dat.",
                                 "This Excel file has more than one sheet with data ({names}). The assistant reads one sheet: save the sheet "
                                 "you mean as a file of its own and give that."),
    "pricelist_columns_hint": ("Heeft de lijst een kolom voor waar de prijs per geldt (per 100, per doos), wijs die dan aan: anders leest de "
                               "assistent elke prijs als een prijs per stuk.",
                               "If the list has a column for what a price is per (per 100, per box), point it out: otherwise the assistant "
                               "reads every price as a price per piece."),
    "pricelist_outcome:differs": ("wijkt sterk af: controleer de kolom \"prijs per\"", "differs strongly: check the column \"price per\""),
    "pricelist_differs": ("Een prijs die meer dan drie keer zo hoog of zo laag is, zet de assistent niet op de lijst: controleer de lijst, en "
                          "voer een echte wijziging zelf in Exact in.",
                          "A price more than three times as high or as low is not put on the list by the assistant: check the list, and "
                          "enter a real change in Exact yourself."),
    "pricelist_refused:name": ("De naam van de leverancier is te lang of bevat tekens die niet in een naam horen.",
                               "The supplier's name is too long or holds characters that do not belong in a name."),
    "pricelist_refused:no_article": ("Deze export gaf geen artikel: geen rij heeft een artikelcode en een prijs die te lezen is. Er is niets "
                                     "opgenomen.",
                                     "This export gave no article: no row has an item code and a price that can be read. Nothing was filed."),
}

# The texts of the rulings of the second review round (Z16 to Z27), word for word; a place for a value has the name the strings give
# it ({row} for a row's number, {name} and {supplier} for a supplier's name).
RULED_AGAIN = {
    "exact_export_refused:surplus_cells": (
        "Rij {row} van dit bestand heeft meer cellen dan de rij met kolomtitels. Meestal staat er dan een komma in een getal of in een "
        "omschrijving zonder aanhalingstekens. Sla het bestand op als Excel-bestand (.xlsx) of met puntkomma's als scheidingsteken, en geef "
        "het opnieuw.",
        "Row {row} of this file has more cells than the row of column titles. Usually a number or a description then holds a comma without "
        "quotation marks. Save the file as an Excel file (.xlsx) or with semicolons as the separator, and give it again."),
    "pricelist_outcome:shared": ("ook bij leverancier {name}: voer deze prijs zelf in Exact in",
                                 "also with supplier {name}: enter this price in Exact yourself"),
    "pricelist_shared_more": ("{names} en nog {n}", "{names} and {n} more"),
    "pricelist_remove": ("Inkoopprijzen van deze leverancier weghalen", "Remove this supplier's purchase prices"),
    "pricelist_removed": ("De opgenomen inkoopprijzen van {supplier} zijn weggehaald. Wat al op de lijst In Exact invoeren staat, blijft staan.",
                          "The stored purchase prices of {supplier} were removed. What already stands on the list Enter in Exact stays."),
    "pricelist_older": (OLDER_NL, OLDER_EN),
    "pricelist_outcome:ticked": (TICKED_NL, TICKED_EN),
    "pricelist_outcome:dropped": (DROPPED_NL, DROPPED_EN),
    "pricelist_log_lost": ("De assistent kon deze wijzigingen niet in zijn eigen logboek van de prijslijst schrijven en kan ze daardoor later "
                           "niet zelf terugzetten: geef dit door aan de onderzoeker.",
                           "The assistant could not write these changes into its own log of the price list, so it cannot put them back itself "
                           "later: pass this on to the researcher."),
    "pricelist_columns_lost": ("De kolommen van deze lijst konden niet worden bewaard: wijs ze de volgende keer opnieuw aan.",
                               "The columns of this list could not be kept: point them out again next time."),
    "pricelist_not_prepared": ("Niet op de lijst gezet: {articles}. Bekijk de lijst opnieuw om het nog eens te proberen.",
                               "Not put on the list: {articles}. View the list again to try once more."),
    "pricelist_refused:name_row": ("In rij {row} van de export staat bij de leverancier een naam die te lang is of tekens bevat die niet in een "
                                   "naam horen. Er is niets opgenomen.",
                                   "Row {row} of the export holds a supplier's name that is too long or holds characters that do not belong in "
                                   "a name. Nothing was filed."),
    "pricelist_refused:heading": ("De kolomtitels moeten in de eerste rij van het bestand staan: haal de regels erboven weg en geef het bestand "
                                  "opnieuw.",
                                  "The column titles must stand in the first row of the file: remove the lines above them and give the file "
                                  "again."),
    # the third round (rulings Z30, Z31 and Z33)
    "exact_export_refused:cell_count": (UNEVEN_NL.replace("{n}", "{row}"), UNEVEN_EN.replace("{n}", "{row}")),
    "exact_export_refused:hidden_sheet": (HIDDEN_NL, HIDDEN_EN),
    "files_error:cells": ("Export uit Exact niet opgenomen: een rij heeft een ander aantal cellen dan de rij met kolomtitels. Sla het bestand "
                          "op als Excel-bestand (.xlsx) en geef het opnieuw.",
                          "Export from Exact not filed: a row has another number of cells than the row of column titles. Save the file as an "
                          "Excel file (.xlsx) and give it again."),
    "files_error:sheets": ("Export uit Exact niet opgenomen: het Excel-bestand heeft meer dan één werkblad met gegevens. Sla het werkblad met "
                           "de orderregels op als een eigen bestand en geef dat.",
                           "Export from Exact not filed: the Excel file has more than one sheet with data. Save the sheet with the order "
                           "lines as a file of its own and give that."),
    "files_error:hidden_sheet": ("Export uit Exact niet opgenomen: het Excel-bestand heeft alleen gegevens op een verborgen werkblad. Maak het "
                                 "werkblad zichtbaar en geef het bestand opnieuw.",
                                 "Export from Exact not filed: the Excel file holds data on a hidden sheet only. Unhide the sheet and give "
                                 "the file again."),
}


def test_every_text_of_the_brief_and_the_amendments_stands_in_the_strings():
    """Amendment 6: every Dutch and English text of the brief and of its amendments stands in the page's strings word for word; a
    sentence with counts is put together from parts that give the brief's sentence for any count but one; what the amendments took out
    of the brief is not on the page. The texts of the rulings of the two review rounds stand there the same way."""
    assert {key: (NL[key], EN[key]) for key in TEXTS} == TEXTS
    assert {key: (NL[key], EN[key]) for key in BEYOND} == BEYOND
    assert {key: (NL[key], EN[key]) for key in RULED} == RULED
    assert {key: (NL[key], EN[key]) for key in RULED_AGAIN} == RULED_AGAIN
    mine = {key for key in NL if key.startswith(("pricelist", "exact_articles", "exact_part_articles", "hand_pricelist"))}
    assert mine == {key for key in (*TEXTS, *BEYOND, *RULED, *RULED_AGAIN)
                    if key.startswith(("pricelist", "exact_articles", "exact_part_articles", "hand_pricelist"))}
    for gone in ("ook bij een andere leverancier", "also with another supplier", "De wijzigingen staan op de lijst.", "Niet op de lijst gezet ("):
        assert not any(gone in text for text in (*NL.values(), *EN.values())), gone              # what the second round replaced
    # the sentences with counts, as the brief and the amendments give them
    for strings, read, state, filed_one, filed_many, counts in (
            (NL, "{n} artikelen gelezen; {k} rijen overgeslagen.", "Inkoopprijzen van {supplier} opgenomen op {time} uit {file}: {n} artikelen.",
             "Inkoopprijzen van {supplier} opgenomen: {n} artikelen; {k} rijen overgeslagen.",
             "Inkoopprijzen opgenomen van {m} leveranciers: {n} artikelen; {k} rijen overgeslagen.",
             "{a} prijzen veranderen, {b} blijven gelijk, {k} rijen overgeslagen."),
            (EN, "{n} articles read; {k} rows passed over.", "Purchase prices of {supplier} filed on {time} from {file}: {n} articles.",
             "Purchase prices of {supplier} filed: {n} articles; {k} rows passed over.",
             "Purchase prices filed of {m} suppliers: {n} articles; {k} rows passed over.",
             "{a} prices change, {b} stay the same, {k} rows passed over.")):
        articles, passed = strings["pricelist_articles_many"], strings["pricelist_passed_many"]
        assert strings["exact_articles_read"].format(articles=articles, passed=passed) == read
        assert strings["pricelist_state"].format(supplier="{supplier}", time="{time}", file="{file}", articles=articles) == state
        assert strings["pricelist_filed"].format(supplier="{supplier}", articles=articles, passed=passed) == filed_one
        assert strings["pricelist_filed_suppliers"].format(m="{m}", articles=articles, passed=passed) == filed_many
        assert strings["pricelist_counts"].format(changes=strings["pricelist_changes_many"], same=strings["pricelist_same_many"], passed=passed) == counts
    # what the amendments took out of the brief stands nowhere on the page
    every = "\n".join([*NL.values(), *EN.values()])
    for gone in ("Opslaan en opnemen", "Save and file", "Nog geen inkoopprijzen opgenomen.", "No purchase prices filed yet.",
                 "Neem eerst de inkoopprijzen uit Exact op", "File the purchase prices from Exact first", "Inkoopprijzen opgenomen op {"):
        assert gone not in every, gone
    for key in mine:
        assert re.findall(r"\{(\w+)\}", NL[key]) == re.findall(r"\{(\w+)\}", EN[key]), key          # the same places for the same values
        assert not re.search(r"—|–|\bR\d{2}\b|\bP01\b|_", NL[key] + EN[key]), key                      # no dash, no rule code, no setting's name
        assert not re.search(r"\b(je|jij|jou|jouw)\b", NL[key]), key                                # the formal address


def code_blocks(text: str) -> list[str]:
    """The fenced blocks of a part of the manual, each as its text."""
    return [block.strip("\n") + "\n" for block in re.findall(r"```\n(.*?)```", text, flags=re.DOTALL)]


def test_the_documents_say_what_the_price_list_tab_does(tmp_path):
    """Amendment 5: the manual describes the tab in section 4.3 beside the command, with a pointer from section 2, whose sections keep
    their numbers; 3.17 has part 3 and the owner's walk with two made-up files written out, which give exactly the lines the walk
    quotes; 4.9 says that an article price is ticked by hand; the Dutch guide has the brief's sentence. The manual quotes the page's
    texts whole where it quotes them. The rulings of the review round stand in 4.3, 2.6, 3.1, 3.2, 3.7, 3.17, 4.8 and the guide: one
    sheet, an article that two suppliers share, a price that differs strongly, a price asked again, the kept columns as data. So do
    those of the second round: the one sheet everywhere, a CSV row with more cells than its titles, a heading above the titles, the
    other supplier named and known by the list too, the removal of a supplier, the two outcomes of a price no press can put on the
    list, and the notes of a press."""
    manual, whole = hand.manual_sections(), Path("aid/MANUAL.md").read_text(encoding="utf-8")
    tab, part, press = manual["4.3"], manual["3.17"], manual["2.6"]
    assert tab.startswith("### 4.3 The price-list import and its undo\n") and "python3 -m aid.pricelist --file" in tab
    assert tab.index("**The tab \"Prijslijst\".**") < tab.index("**The command.**")
    assert [number for number in manual if number.startswith("2.")] == [f"2.{n}" for n in range(1, 9)]       # section 2 is not renumbered
    assert manual["2.7"].startswith("### 2.7 The tab Bestanden\n") and manual["2.8"].startswith("### 2.8 Your settings on the page\n")
    assert ("The page has five tabs: \"Beoordelen\" (this section), \"In Exact invoeren\" (section 2.6), \"Prijslijst\" (section 4.3), "
            "\"Bestanden\" (section 2.7) and \"Instellingen\" (section 2.8).") in manual["2.2"]
    assert "The page has five tabs:" in manual["3.7"] and "\"Prijslijst\", a supplier's price list compared with" in manual["3.7"]
    assert "the tabs \"In Exact invoeren\", \"Prijslijst\" and \"Bestanden\" and the settings tab say only" in manual["3.7"]
    assert "four tabs" not in whole
    # 4.3 quotes the tab word for word
    for key in ("pricelist_tab", "pricelist_unset", "pricelist_intro", "pricelist_supplier", "pricelist_supplier_help", "pricelist_choose_supplier",
                "pricelist_export", "pricelist_file_prices", "pricelist_list", "pricelist_view", "pricelist_carry_out", "pricelist_expired",
                "pricelist_changed", "pricelist_refused:columns", "pricelist_refused:supplier", "pricelist_stored_unreadable",
                "pricelist_problem:price", "exact_export_problem:item_code", "files_choose_first", "exact_export_no_column",
                "hand_pricelist_entered", *[f"pricelist_column:{column}" for column in LIST_COLUMNS],
                *[f"pricelist_header:{column}" for column in ("article", "current", "list", "outcome")]):
        assert f"\"{NL[key]}\"" in tab, key
    for quote in (state_line("S01", None, "nl"), filed_line(["S01"], 12, 3, "nl"), filed_line(["S01", "S02"], 12, 3, "nl"),
                  "Inkoopprijzen van S01 opgenomen op 2026-11-20 09:15 uit artikelen S01.csv: 12 artikelen.",
                  "2 prijzen veranderen, 1 blijft gelijk, 3 rijen overgeslagen.", carried_line(2, "nl"), carried_line(0, "nl"),
                  NL["pricelist_refused:type"].format(name="artikelen.xls"), NL["files_refused:empty"].format(name="artikelen.csv"),
                  NL["pricelist_row"].format(n=7), NL["settings_failed"].format(code="PermissionError")):
        assert f"\"{quote}\"" in tab, quote
    # the table of the outcomes: every label of the tab is quoted there, and every label quoted first in a row is a label of the tab
    labels = {NL[key].format(name="S02") for key in NL if key.startswith("pricelist_outcome:")}          # the shared row names a supplier
    rows = [line for line in tab.splitlines() if line.startswith("| \"")]
    assert all(any(line.startswith(f"| \"{label}\" |") for label in labels) for line in rows) and len(rows) == 15
    assert all(any(f"\"{label}\"" in line for line in rows) for label in labels)
    for words in ("cases/exact/article_prices.json", "cases/pricelists/<day>/write_log.jsonl", "`cases/exact/pricelist_columns.json`",
                  "writes nothing anywhere", "leaves every other supplier's prices as they are", "counts for the supplier of its case",
                  "**Two suppliers of one article.**", "--share", "In the test mode (section 3.17) a price list is handled as any other"):
        assert words in tab, words
    # the rulings of the review round: 4.3 quotes each of their texts whole, and says what the buyer can do
    for key in ("pricelist_outcome:differs", "pricelist_columns_hint", "pricelist_differs", "pricelist_refused:name",
                "pricelist_columns_lost", "pricelist_refused:no_article"):
        assert f"\"{NL[key]}\"" in tab, key
    sheets = NL["exact_export_refused:several_sheets"].format(names="Slangen, Klemmen")
    assert f"\"{sheets}\"" in tab and f"\"{sheets}\"" in part
    # the rulings of the second round: each text quoted whole, with made-up values in its places
    for quote in (NL["pricelist_not_prepared"].format(articles="KM-1002"), NL["pricelist_outcome:shared"].format(name="S02"),
                  NL["pricelist_outcome:shared"].format(name=NL["pricelist_shared_more"].format(names="S02, S03", n=2)),
                  NL["pricelist_outcome:shared"].format(name="S01 B.V."), NL["pricelist_remove"], NL["pricelist_removed"].format(supplier="S01 B.V."),
                  NL["pricelist_older"].format(articles="KM-1001"), NL["pricelist_outcome:ticked"], NL["pricelist_outcome:dropped"],
                  NL["pricelist_log_lost"], NL["pricelist_refused:heading"], NL["pricelist_refused:name_row"].format(row=6),
                  NL["exact_export_refused:surplus_cells"].format(row=3), NL["hand_dropped"], NL["hand_reopen"]):
        assert f"\"{quote}\"" in tab, quote
    assert f"\"{NL['exact_export_refused:surplus_cells'].format(row=4)}\"" in part
    # the third round (rulings Z30, Z31, Z33 and Z35): the comma file, the hidden sheet, the lines of the tab Bestanden, the README
    for section, row in ((tab, 3), (part, 4)):
        assert f"\"{NL['exact_export_refused:cell_count'].format(row=row)}\"" in section and f"\"{NL['exact_export_refused:hidden_sheet']}\"" in section
        assert "In a CSV file cut at commas every filled row below the column titles must have exactly their number of cells" in section
    assert ("What cannot be told is a comma file whose writer leaves out cells so that a cut row has, by chance, the titles' number of "
            "cells") in tab
    assert "It looks up its columns first, so that a file whose first row does not hold them is refused with the missing column named" in tab
    for key in ("files_error:cells", "files_error:sheets", "files_error:hidden_sheet"):
        assert f"\"{NL[key]}\"" in manual["2.7"], key
    assert "in a file cut at commas, a filled row below the column titles has another number of cells than they have" in manual["6"]
    assert "when the only data of a workbook stands on a hidden sheet" in manual["6"]
    assert "a CSV row with more cells than its row of column titles refuses the file" not in Path("aid/README.md").read_text(encoding="utf-8")
    assert "in a file cut at commas, a row with another number of cells than its row of column titles" in Path("aid/README.md").read_text(encoding="utf-8")
    for words in ("by an export given on the tab", "or by an open change on the list", "also when no export of that supplier was given",
                  "**An older price that stays on the list.**", "**A supplier given under two spellings.**", "both or neither, and nothing else",
                  "Rows and columns hidden in Excel (a filter, a hidden column) are read as the others are.",
                  "A file with a heading above its column titles is not taken", "Of an Excel file the command reads one sheet, the one you see",
                  "What the table shows as \"prijs verandert\", the press puts on the list.", "the same holds for the file of `--current`"):
        assert words in tab, words
    assert "Of an Excel file the block reads one sheet, the one you see" in part
    assert "a workbook with more than one visible sheet with data (the sentence quoted under step 1)" in part
    assert "its first sheet is read" not in whole and "the first sheet of a" not in whole and "one sheet is read, the one you see" in manual["6"]
    assert "when a workbook has more than one visible sheet with data; when a row of a CSV file has a filled cell beyond" in manual["6"]
    for words in ("**A price that Exact does not hold after all.**", "**A price that differs strongly.**", "a hidden sheet is never read",
                  "more than three times the purchase price in Exact, or less than a third of it", "noted in `marks.jsonl` as done by the assistant",
                  "no press of the tab changes the settings file", "Enter that price in Exact yourself, for the supplier whose list it is",
                  "in UTF-8, UTF-16 with its byte order mark or Windows-1252, cut at semicolons, commas or tabs",
                  "While the columns of the export of articles are set (`article_columns`, part 3), `--share` itself is refused (section 3.7).",
                  "also when the tick is older than the 14 days of the handled changes"):
        assert words in tab, words
    assert "`pricelist_columns`" not in whole and "kept for the supplier in the settings file" not in whole
    assert "While the columns are set, the review page's `--share` is refused, as while the columns of part 2 are set" in part
    assert "Of an Excel file part 3 reads one sheet, the one you see" in part
    assert "`article_columns`, its part 3" in manual["3.7"] and "The tab \"Prijslijst\" changes none of it" in manual["4.8"]
    # 3.17: part 3, quoted whole, and no line of the two state lines that the amendments took out
    for key in ("exact_part_articles", "exact_articles_intro", "exact_articles_sample", "exact_articles_field:item_code", "exact_articles_field:price",
                "exact_articles_field:supplier", "exact_test", "exact_articles_save", "exact_articles_problem:price", "exact_articles_problem:none",
                "exact_articles_price", *[f"exact_articles_left:{reason}" for reason in ("no_item_code", "no_supplier", "no_price", "twice")]):
        assert f"\"{NL[key]}\"" in part, key
    assert "\"12 artikelen gelezen; 3 rijen overgeslagen (zonder artikelcode: 2, prijs niet te lezen: 1).\"" in part
    assert "It stores no price" in part and "`article_columns` and `article_decimal_comma`" in part
    assert "part 3 sets the columns of the export of a supplier's articles" in part
    # the walk: the two made-up files as the manual writes them out give exactly the lines it quotes
    walk = part[part.index("**A test walk of the price list with two made-up files.**"):part.index("**A test walk with a try-out.**")]
    export_text, list_text = code_blocks(walk)
    export, listed = tmp_path / "artikelen S01.csv", tmp_path / "prijslijst S01.csv"
    export.write_text(export_text, encoding="utf-8")
    listed.write_text(list_text, encoding="utf-8")
    from aid.review.exact_settings import articles_text, articles_trial, header_choices
    saved = {"article_columns": {"item_code": "Artikel", "price": "Inkoopprijs"}, "article_decimal_comma": True}
    assert header_choices(export) == (3, ["Artikel", "Omschrijving", "Inkoopprijs"]) and "\"Rij met de kolomnamen\" shows 3" in walk
    read = articles_text(articles_trial(export, saved, "nl"), "nl")
    assert read == "3 artikelen gelezen; 1 rij overgeslagen (prijs niet te lezen: 1)." and f"\"{read}\"" in walk
    root = tmp_path / "cases"
    stored = store_current(root, export, saved["article_columns"], True, STORED, "S01")
    assert f"\"{filed_line(*stored, 'nl')}\"" in walk and f"\"{state_line('S01', None, 'nl')}\"" in walk
    columns = {"item_code": "Artikelnummer", "price": "Nettoprijs", "basis": "Per", "valid_to": "Geldig tot"}
    assert list_titles(listed) == ["Artikelnummer", "Omschrijving", "Nettoprijs", "Per", "Geldig tot"]
    view = preview(listed, "S01", current_prices(root, "S01")[0], columns, NOW)
    assert [row[3] for row in shown(view, current_prices(root, "S01")[0], "nl")] == [
        "prijs verandert", "ongewijzigd", "prijs verandert", "prijs per ander aantal: niet vergeleken", "artikel niet bekend in Exact", "verlopen"]
    assert f"\"{count_line(view, 'nl')}\"" in walk and "The table shows six rows" in walk
    done = carry_out(listed, "S01", current_prices(root, "S01")[0], columns, root, NOW)
    assert f"\"{carried_line(on_list(done, root), 'nl')}\"" in walk
    [first, second] = open_changes(root / "prepared")
    for change in (first, second):
        row = hand.NL["hand_row:article_price"].format(item=change.article_code, old=change.old_value.replace(".", ","),
                                                        new=change.new_value.replace(".", ","))
        assert f"\"{row}\"" in walk, row
    assert mark(root / "prepared", first.ids, "entered", NOW + timedelta(minutes=5)) == 1
    again = preview(listed, "S01", current_prices(root, "S01")[0], columns, NOW + timedelta(minutes=6))
    assert shown(again, current_prices(root, "S01")[0], "nl")[0] == ["KM-1001", "12,70", "12,70", "ongewijzigd"]
    assert f"\"{count_line(again, 'nl').split(' Sommige')[0]}\"" in walk
    for key in ("pricelist_export", "pricelist_file_prices", "pricelist_list", "pricelist_view", "pricelist_carry_out", "hand_pricelist_entered",
                "hand_entered", "exact_numbers:comma", "exact_export_no_column"):
        assert f"\"{NL[key]}\"" in walk, key
    assert "checks A15 and A16" in walk and "python3 -m aid.demo --out live --prepare-only" in walk
    # 2.6: the press; 4.9: an article price is ticked by hand, and what is not built; 3.1 and 3.2: the file and the settings
    assert f"\"{NL['hand_pricelist_entered']}\"" in press and "\"S01 (20-11-2026 09:30)\"" in press and "under \"Blok\"" in press
    assert "A row \"Terugzetten:\" that puts a price of that list back is no row of the list" in press
    assert "says to enter that price in Exact yourself (section 4.3)" in press
    limits = manual["4.9"]
    assert "**An article price is ticked by hand.**" in limits and "does not tick the open article prices of that supplier off" in limits
    assert "check A17" in limits and "nothing is built for it" in limits
    assert "exact/               article_prices.json" in manual["3.1"] and "from the tab \"Prijslijst\" or by the" in manual["3.1"]
    assert "and pricelist_columns.json, the columns of each" in manual["3.1"]
    for setting in ("article_columns", "article_decimal_comma"):
        assert f"| `{setting}` |" in manual["3.2"], setting
    assert "`article_columns` and `article_decimal_comma` (its part 3)" in manual["3.2"]
    assert "The tab \"Prijslijst\" writes no setting" in manual["3.2"] and "`cases/exact/pricelist_columns.json`" in manual["3.2"]
    # the Dutch guide: the brief's sentence, and every quote of its paragraph is a text of the page
    guide = manual["7"]
    assert "Een prijslijst vergelijkt u op het tabblad Prijslijst." in guide
    [paragraph] = [line for line in guide.splitlines() if line.startswith("**Prijslijst.**")]
    quotes = re.findall(r'"([^"]+)"', paragraph)
    shared = NL["pricelist_outcome:shared"].format(name="S02")
    assert len(quotes) >= 15 and shared in quotes and NL["pricelist_remove"] in quotes
    assert NL["pricelist_outcome:ticked"] in quotes and NL["pricelist_outcome:dropped"] in quotes
    for quote in quotes:
        assert quote in (*NL.values(), shared), quote
    assert not re.search(r"\b(je|jij|jou|jouw)\b|—|–", paragraph)
    for words in ("Van een Excel-bestand leest de assistent één werkblad, het werkblad dat u ziet",
                  "Heeft de lijst een kolom voor waar de prijs per geldt (per 100, per doos), wijs die dan ook aan.",
                  "Een prijs die meer dan drie keer zo hoog of zo laag is als de inkoopprijs in Exact, zet de assistent ook niet op de lijst",
                  "voer de prijs zelf in Exact in en geef daarna een nieuwe export van de artikelen van deze leverancier",
                  "Staat daar dezelfde leverancier in een andere schrijfwijze (S01 B.V. naast S01 BV)",
                  "Wat al op de lijst In Exact invoeren staat, blijft staan.",
                  "de assistent vraagt dan opnieuw om de prijzen die verschillen"):
        assert words in paragraph, words
    readme = Path("aid/README.md").read_text(encoding="utf-8")
    assert "Its tab \"Prijslijst\" compares a supplier's price list" in readme and "`article_columns` and `article_decimal_comma` describe" in readme
    assert "cases/exact/article_prices.json" in readme and "in `cases/exact/pricelist_columns.json`" in readme
    assert "`pricelist_columns`" not in readme and "no press of the tab changes the settings file" in readme
