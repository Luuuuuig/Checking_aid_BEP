"""The price-list import of the checking aid: a supplier's price list or dealer-portal download updates the article prices through the
same write path as every other change, so that each change is logged and can be undone. Scraping a webshop is out of scope.

import_price_list() reads the list, decides row by row and has the writer apply one article_price operation (rule P01) for every price
that changed. The entries the writer returns are in ImportResult.updated and the rows that were not imported in ImportResult.skipped;
ImportResult.outcomes has both in the order of the file. As with every writer, appending the entries to the write log is the caller's job
(aid/execution/writer.py); the command line appends them to the write log of a folder of its own (see below), so that the undo can reverse
an import.

The list is a CSV file, comma or semicolon separated (the header row decides which), in UTF-8 or Windows-1252, or an .xlsx workbook, of
which the one visible sheet that holds data is read (a hidden sheet never; more than one such sheet is refused with their names), with
the column names in its first row. The aid's columns are item_code, price, basis, unit, min_quantity, valid_from and
valid_to. column_map gives the name the file uses for each of them (default: the same name); names are matched without regard to case
and to spaces around them. item_code and price must be columns of the file, and so must every column the map names; the others may be
missing. A blank row is ignored. A date in a workbook cell is read as the day it shows and a number as its value to 8 decimals, so that a
price a spreadsheet stored as 38.09999999999999 is 38.1. A list that cannot be read at all (not a .csv or .xlsx file, a damaged file,
text that is neither UTF-8 nor Windows-1252, a missing column) raises ValueError before anything is written, and so does a price in
current_prices that is not a number. A CSV file with a row that the aid does not read is refused whole in the same way, with the
row named (odd_row): a row with a filled cell beyond the last column title, and, in a file cut at commas, a filled row with another
number of cells than the row of titles. A comma in a number or a description without quotation marks cuts such a row into more
cells than it has, and read by position 12,70 would be 12. The command looks up its columns first, so that a file whose first
filled row does not hold them is refused with the missing column named. A workbook whose only data stands on a hidden sheet is
refused in words that say so (OnlyHidden).

Each cell is read with the normalisers of aid/schema.py, whose normal forms its docstring gives: price and min_quantity with
normalize_number, which detects the decimal comma as it does for a document ("12,70", "1.234,50" and "EUR 12,70" are read); basis with
normalize_basis, a blank basis meaning "1"; unit with normalize_unit; valid_from and valid_to with normalize_date against the date of
`now`, where only one day counts as a date (a week or a range is not the day a price starts or ends). A blank cell, and for basis, unit and
min_quantity a cell that cannot be read, is None in the normalised row; a blank date means no bound.

Why a row is skipped. ImportResult.skipped holds (item code, reason) for each row that is not imported, in the order of the file. For a
row without an item code the first element is "row <n>", the first row of the file being row 1 as in a spreadsheet. The reasons are
checked in this order and the first that applies is given:

    no item code
    unreadable valid_from, then "not yet valid" (valid_from is after the date of `now`)
    unreadable valid_to, then "expired" (valid_to is before the date of `now`); ImportResult.expired is True when any row is expired
    unknown article (the item code is not in current_prices)
    unreadable price (blank, or not a number)
    unknown price basis (the row states a basis that is not 1: another number, or one that cannot be read)
    unchanged (the price equals the current one as a number, so "8" and "8.00" are the same)
    duplicate row
    error: <message> for a WriterError, error: <type>: <message> for any other exception (the writer could not make the change; the
    import goes on with the next row)

The validity of a row is checked before its article, so that `expired` says something about the list and not only about the articles the
aid knows. A duplicate row is a later row for an item code that an earlier row of the file settled: the first row that is in force (valid
today, for a known article, with a readable price per piece) settles the article, whether it changes the price or leaves it unchanged. A row that
is not in force settles nothing, so a list that keeps an expired row above the current one still imports the current one.

The current prices have no basis column, so they are taken as prices per piece and only a row per piece (a basis of 1, or none) is
compared with them: a row per 100 is skipped as "unknown price basis" rather than written as a price per piece a hundred times too high.
The list's basis and unit, and its minimum quantity, are recorded in the evidence of every change; the unit plays no part in the decision.
This is a first rule, to be revisited with real lists.

Every change is one WriteOp of kind article_price: po_number "" and version None because it is not a PO write, article_code the item
code, old_value the current price and new_value the list price, both in normal form. It is applied with the case id
"pricelist_<supplier>_<now:%Y%m%dT%H%M%S>", the rule id "P01" and the evidence {"supplier": the supplier, "file": the name of the list,
"row": the normalised row}. A write that raises is a skipped row whatever the exception, so that ImportResult.updated holds every entry
that was made and none is lost for the write log.

    python3 -m aid.pricelist --file list.xlsx --supplier S1 --current current.csv --prepared <dir> --write-log <folder>
    python3 -m aid.pricelist --file list.xlsx --supplier S1 --current current.csv --prepared <dir> --settings settings.json

reads the current prices from a CSV file with the columns item_code and price, prepares each change in <dir>/prepared_changes.csv
(PreparedWriter), appends every entry the import made to the write log of <folder> (<folder>/write_log.jsonl, WriteLog of
aid/execution/writer.py; undo_case on that folder reverses the import), prints one line per row in the order of the file (updated,
skipped or error) and a summary line "<n> updated, <n> skipped, <n> errors", and returns 0. Without --write-log the folder is
<cases_root>/pricelists/<YYYY-MM-DD> of the settings that --settings names, the day being that of the run; one of the two must be given.
The folder is made when the first entry is logged. It returns 1 when a file cannot be read, when any write failed and when the write
log cannot be written.

The article prices of an Exact export. On the review page the buyer has no terminal: the current prices come from an export of a
supplier's articles that he saves from Exact and gives on the tab "Prijslijst" (aid/review/pricelist_page.py, aid/review/page.py).
read_articles reads such an export with the columns of the settings (article_columns: item_code and price, and supplier when the
export names the supplier of each row) and the convention of its numbers written as text (article_decimal_comma). The file is read as
the export of order lines is (aid/readers/po_export.py): an .xlsx or .csv file, the row of column titles the first row that holds
every title of the map, a number cell of a workbook the number it holds whatever the convention says, a text read with
normalize_number. Of a workbook the sheet read is the one the buyer sees (_xlsx_table): a hidden sheet is never read, the one
visible sheet that holds data is read whatever its place, and a workbook with more than one such sheet is refused with their
names (the export reader's ExportRefused, reason several_sheets). A blank row, and the row of column titles again, are no row. A row
is left out and counted, under the first of
these reasons that applies (LEFT_OUT): no item code (a blank cell, a date, a time or an error cell); with a supplier column, no
supplier; a price that is no number; and an article that two rows list for one supplier, where every such row is left out, since
it is not clear which price holds. A file that cannot be read as a whole raises the export reader's ExportRefused, a ValueError,
with its reason: another type than .xlsx and .csv, a damaged file, a map that names another field or lacks the item code or the
price, a column of the map that no row holds, or a title that stands twice.

store_current keeps the prices of such an export in <cases_root>/exact/article_prices.json, per supplier:

    {"suppliers": {"S01": {"stored_at": "2026-11-20T09:15:00", "file": "artikelen.xlsx", "prices": {"KM-1001": "12.50"}}}}

The supplier of a row is the one its supplier cell names; without a supplier column, the one the caller names. A newer export
replaces, whole, the stored prices of each supplier it holds and leaves every other supplier's as they are, so that the export of one
supplier's articles never takes away the prices of the others. A supplier is known by its name without regard to capitals and
spaces (supplier_named): "s 01" is the stored "S01". The file is written whole or not at all, through a temporary file beside it and
a replace, and one export is stored at a time in this process, each on the file as the one before left it. stored_prices reads
it; a file that cannot be read as one raises ValueError naming the file and never a value, and current_prices takes such a file as
nothing stored. An export that gives no article stores nothing and leaves what was stored (NoArticle), so that an export read with
the wrong columns never empties a supplier's prices; and a supplier's name that is no name stores nothing either (NameRefused;
is_supplier_name: at most SUPPLIER_NAME_MAX characters, no line break and no control character), since the name becomes part of
the case id of the supplier's price lists and is shown on the page. also_held gives the articles that the stored prices of another
supplier hold too, and others_asking those of which the list "In Exact invoeren" holds an open change that another supplier's price
list or case asked, each with the names of those suppliers: the tab keeps a row of a price list for such an article off the list
and names the supplier. remove_stored takes the stored prices of one supplier away, with the columns kept for its price list,
whole or not at all: the way out when one supplier was given under two spellings, which then share every article.

The columns of a supplier's price list, as the buyer last pointed them out on the tab, are data like the stored prices and no
setting: keep_columns keeps them per supplier in <cases_root>/exact/pricelist_columns.json, written whole or not at all,

    {"suppliers": {"S01": {"item_code": "Artikelnummer", "price": "Nettoprijs"}}}

and kept_columns reads them, a file that does not hold this form being nothing kept. So a press of the tab never changes the
settings file.

The tab's own reading and check. The command reads a list as the first paragraphs say. The tab "Prijslijst" reads a workbook
the same way and a CSV file otherwise (as the export is read: more encodings and separators), and hands import_price_list the rows
it read (table); and it gives the import a check of its own for each price that would change (check): a row the check holds back
is skipped with the check's reason, and ImportResult.held keeps its list price for the page to show
(aid/review/pricelist_page.py).

current_prices gives what Exact holds for a supplier as far as the aid knows: that supplier's stored prices, with every article
price that was entered or seen on the list "In Exact invoeren" after the export was stored laid over them. The marks are judged as
the list judges them (changes of aid/execution/by_hand.py): of the changes of an article that count for the supplier, the last one
that is entered or seen gives the price, at the time of the mark that gives it that state. So an open, dropped, replaced or undone
change never counts, a put-back that is entered puts the old price back, and one that is still open leaves the entered price. A
mark counts when it was made after the supplier's export was stored; an export stored in the second of the mark or after it is the
newer look and is taken as it is. A change counts for the supplier its case names: the supplier of a price list (list_of reads it
from the case id that import_price_list gives, "pricelist_<supplier>_<time>") or the supplier in the result of a case; a change whose
case names no supplier counts for every supplier whose stored prices hold the article. The cases that decide are those of the rows
of the change whose marks give it its state, the rows that were ticked or seen: the list holds one row per article and value,
whichever supplier asked, so a row that another supplier's list adds to a change that is entered already was never ticked for that
supplier, and does not make the price that supplier's. The changes that cases made from a try-out asked never count: a price list
is no try-out. An entered put-back to a price the aid never read (the undo of an article price that a confirmation asked, whose
price before it is unknown) gives the article the export's price again, and no price when the export does not hold the article.
While the two files of the list cannot be read, current_prices raises their error, so that no price is ever compared as if nothing
had been entered.
"""
import argparse
import contextlib
import csv
import io
import json
import os
import re
import sys
import tempfile
import threading
import unicodedata
import zipfile
import zlib
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple

from openpyxl import load_workbook
from pydantic import BaseModel

from aid.case import load_case, names_a_folder
from aid.execution.ops import WriteOp
from aid.execution.writer import PreparedWriter, WriteLog, WriteLogEntry, Writer, WriterError
from aid.schema import normalize_basis, normalize_date, normalize_number, normalize_unit
from aid.settings import load_settings

COLUMNS = ("item_code", "price", "basis", "unit", "min_quantity", "valid_from", "valid_to")
REQUIRED = ("item_code", "price")                    # the columns the file must have; the others may be missing
RULE_ID = "P01"
ERROR_PREFIX = "error: "                             # the start of the reason of a row whose write failed
CASE_PREFIX = "pricelist_"                           # the start of the case id of an import: pricelist_<supplier>_<YYYYmmddTHHMMSS>
ARTICLES_FOLDER = "exact"                            # under cases_root: what the aid keeps of Exact's own exports
ARTICLES_FILE = "article_prices.json"                # <cases_root>/exact/article_prices.json: the article prices, per supplier
COLUMNS_FILE = "pricelist_columns.json"              # <cases_root>/exact/pricelist_columns.json: the columns of each supplier's price list
SUPPLIER_NAME_MAX = 100                              # the most characters of a supplier's name that the tab "Prijslijst" takes
ARTICLE_FIELDS = ("item_code", "price", "supplier")  # the fields of an export of articles; the map must name the first two
LEFT_OUT = ("no_item_code", "no_supplier", "no_price", "twice")      # why a row of such an export is left out, in the order checked
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_LIST_CASE = re.compile(re.escape(CASE_PREFIX) + r"(.+)_([0-9]{8}T[0-9]{6})")
_NO_CODE = ("date", "time", "error")                 # the kinds of cell of a workbook that hold no item code, supplier or price
_NO_NAME = ("Cc", "Zl", "Zp")                        # the kinds of character no supplier's name holds: a control character, a line break
SURPLUS = "a filled cell beyond the last column title"       # what the error of a CSV row says that the aid does not read (odd_row):
UNEVEN = "another number of cells than the row of column titles"     # of any CSV file; and of a file cut at commas
_STORING = threading.Lock()                          # one export is stored at a time in this process (store_current)


class ImportResult(BaseModel):
    """What one import did. `skipped` is (item code, reason) for each row not imported, those whose write failed included (see the
    module docstring)."""
    updated: list[WriteLogEntry]                 # one entry per changed price, in the order of the file
    skipped: list[tuple[str, str]]
    expired: bool                                # some row is past its valid_to
    supplier: str = ""                           # the supplier the list is from
    outcomes: list[WriteLogEntry | tuple[str, str]] = []     # the entry or the skip of each row, in the order of the file
    held: dict[str, str] = {}                    # item code -> the list price of the row that the caller's check held back

    @property
    def errors(self) -> list[tuple[str, str]]:
        """The skipped rows whose write failed, those with a reason that starts with "error: "."""
        return [skip for skip in self.skipped if _failed(skip[1])]


class SeveralSheets(ValueError):
    """A workbook with more than one visible sheet with data, of which the aid reads one (_xlsx_table). `names` are the names of
    those sheets, in the order of the workbook, and `tables` their rows as _xlsx_table gives the rows of a sheet; the text of the
    error holds no name."""

    def __init__(self, names: list[str], tables: list[list[list]] | None = None):
        super().__init__(f"the workbook has {len(names)} visible sheets with data, and one sheet is read")
        self.names, self.tables = names, tables or []


class OnlyHidden(ValueError):
    """A workbook whose only data stands on a hidden sheet: the aid reads no hidden sheet (_xlsx_table), and says so, where an empty
    file or a missing column would send the buyer the wrong way. `tables` are the rows of the hidden sheets with data, as
    _xlsx_table gives the rows of a sheet, by which the export reader tells whether the file is an export; they are never read as
    data. The text of the error names the file and no sheet."""

    def __init__(self, name: str, tables: list[list[list]]):
        super().__init__(f"{name} holds data on a hidden sheet only; the aid reads no hidden sheet")
        self.tables = tables


class NoArticle(ValueError):
    """An export of articles that gives no article: no row with an item code and a price that can be read (store_current)."""


class NameRefused(ValueError):
    """A supplier's name that is no name (is_supplier_name): the text of the error never holds the name. `row` is the row of the
    export, counted from 1, whose supplier cell holds the name; None for a name the buyer gave himself."""

    def __init__(self, text: str, row: int | None = None):
        super().__init__(text)
        self.row = row


def import_price_list(path: Path, supplier: str, current_prices: dict[str, str], writer: Writer, now: datetime,
                      column_map: dict[str, str] | None = None, table: list[list[str]] | None = None,
                      check: Callable[[str, str, str], str | None] | None = None) -> ImportResult:
    """Import the price list at `path`, which `supplier` sent, against `current_prices` (item code to the current price in the normal
    form of aid/schema.py), and have `writer` apply the changes. `column_map` maps the aid's column names to the file's (see the module
    docstring for the rows, the reasons for skipping one and the write). `table` are the rows of the file, each a list of cell
    texts, when the caller read them itself (the tab "Prijslijst" reads a list otherwise than the command does); `check` is the
    caller's own check of a price that would change, (the item code, the current price, the list price) -> the reason for which
    the row is passed over, or None: a row it holds back is skipped with that reason, its list price kept in ImportResult.held."""
    path = Path(path)
    rows = _read_list(path, column_map, table)                # a list that cannot be read raises here, before any write
    current = {code: _current_price(code, text) for code, text in current_prices.items()}
    today = now.date()
    case_id = f"pricelist_{supplier}_{now:%Y%m%dT%H%M%S}"
    outcomes: list[WriteLogEntry | tuple[str, str]] = []      # the entry or the (item, reason) of each row, in the order of the file
    settled: set[str] = set()                                 # the articles an earlier row of the file settled
    held: dict[str, str] = {}                                 # the list price of each row that the caller's check held back
    for number, raw in rows:
        code = raw["item_code"]
        if not code:
            outcomes.append((f"row {number}", "no item code"))
            continue
        row = _normalise(raw, today)
        reason = _unusable(raw, row, today, current)
        if reason is None:                                    # the row is in force: it settles the article, whatever follows
            earlier = code in settled
            settled.add(code)
            if Decimal(row["price"]) == Decimal(current[code]):
                reason = "unchanged"
            elif earlier:
                reason = "duplicate row"
            elif check is not None and (reason := check(code, current[code], row["price"])) is not None:
                held[code] = row["price"]
        if reason is not None:
            outcomes.append((code, reason))
            continue
        op = WriteOp(kind="article_price", po_number="", version=None, article_code=code, old_value=current[code], new_value=row["price"])
        evidence = {"supplier": supplier, "file": path.name, "row": row}
        try:
            outcomes.append(writer.apply(op, case_id=case_id, rule_id=RULE_ID, evidence=evidence, now=now))
        except Exception as exc:                              # not only WriterError: an exception must not lose the entries already made
            outcomes.append((code, _error_reason(exc)))
    updated = [outcome for outcome in outcomes if isinstance(outcome, WriteLogEntry)]
    skipped = [outcome for outcome in outcomes if isinstance(outcome, tuple)]
    expired = any(reason == "expired" for _, reason in skipped)
    return ImportResult(updated=updated, skipped=skipped, expired=expired, supplier=supplier, outcomes=outcomes, held=held)


def _error_reason(exc: Exception) -> str:
    """The reason of a row whose write raised `exc`. A WriterError is the writer's own way to say it could not make the change, so its
    message stands alone; any other exception is named by its type as well. With no message the type is all there is."""
    message = str(exc)
    if isinstance(exc, WriterError):
        return ERROR_PREFIX + (message or type(exc).__name__)
    return ERROR_PREFIX + (f"{type(exc).__name__}: {message}" if message else type(exc).__name__)


def _failed(reason: str) -> bool:
    """Whether a skip reason is the one of a write that failed."""
    return reason.startswith(ERROR_PREFIX)


def _current_price(code: str, text: str) -> str:
    """The current price of an article in normal form. A text that is not a number raises ValueError; the import checks every current
    price first, so that it never stops half way through the writes."""
    price = normalize_number(text)
    if price is None:
        raise ValueError(f"the current price of {code} is {text!r}, which is not a number")
    return price


# --- one row ---

def _normalise(raw: dict[str, str], today: date) -> dict[str, str | None]:
    """The row in the normal forms of aid/schema.py, keyed by the aid's column names (see the module docstring)."""
    return {"item_code": raw["item_code"],
            "price": normalize_number(raw["price"]),
            "basis": normalize_basis(raw["basis"]) if raw["basis"] else "1",
            "unit": normalize_unit(raw["unit"]),
            "min_quantity": normalize_number(raw["min_quantity"]),
            "valid_from": _day(raw["valid_from"], today),
            "valid_to": _day(raw["valid_to"], today)}


def _day(text: str, today: date) -> str | None:
    """`text` as a day, "2026-11-16", or None when it is blank, cannot be read or is a week or a range, which normalize_date reads too."""
    value = normalize_date(text, today) if text else None
    return value if value is not None and _DAY.fullmatch(value) else None


def _unusable(raw: dict[str, str], row: dict[str, str | None], today: date, current: dict[str, str]) -> str | None:
    """Why the row does not give a price in force for a known article, the first reason in the order of the module docstring; None when it
    does. `raw` tells a blank date (no bound) from one that cannot be read."""
    start, end = row["valid_from"], row["valid_to"]
    if start is None and raw["valid_from"]:
        return "unreadable valid_from"
    if start is not None and date.fromisoformat(start) > today:
        return "not yet valid"
    if end is None and raw["valid_to"]:
        return "unreadable valid_to"
    if end is not None and date.fromisoformat(end) < today:
        return "expired"
    if raw["item_code"] not in current:
        return "unknown article"
    if row["price"] is None:
        return "unreadable price"
    if row["basis"] != "1":
        return "unknown price basis"
    return None


# --- reading the list ---

def _read_list(path: Path, column_map: dict[str, str] | None, table: list[list[str]] | None = None) -> list[tuple[int, dict[str, str]]]:
    """The rows of the list below its header row as (row number, trimmed text of each of the aid's columns), blank rows left out. The
    row number is the position in the file, counted from 1 as a spreadsheet counts; a column the file lacks is blank. `table` are
    the rows of the file when the caller read them, and checked them; else the file is read here (_read_table). The columns are
    looked up first, so that a file whose first filled row does not hold them (cut at tabs, or with a heading above its titles) is
    refused with the missing column named; only then are the rows of a CSV file checked (odd_row), and the file refused with the
    first row the aid does not read."""
    unknown = sorted(set(column_map or {}) - set(COLUMNS))
    if unknown:
        raise ValueError(f"the column map names {unknown}, which are not columns of the import ({', '.join(COLUMNS)})")
    table, mark = (_read_table(path) if table is None else (table, None))
    filled = [index for index, cells in enumerate(table) if any(cell.strip() for cell in cells)]      # the rows that are not blank
    if not filled:
        raise ValueError(f"{path.name} is empty")
    where = _columns(path.name, table[filled[0]], column_map or {})
    if mark is not None and (odd := odd_row(table, filled[0], commas=mark == ",")) is not None:     # once the title row holds the columns
        raise ValueError(f"{path.name}, row {odd[0]}: {odd[1]}")
    return [(index + 1, {column: _cell(table[index], at) for column, at in where.items()}) for index in filled[1:]]


def _cell(cells: list[str], at: int | None) -> str:
    """The trimmed text of the cell at position `at` of a row; blank where the file lacks the column or the row is shorter."""
    return cells[at].strip() if at is not None and at < len(cells) else ""


def _columns(name: str, header: list[str], column_map: dict[str, str]) -> dict[str, int | None]:
    """The position in the header row of the file `name` of each of the aid's columns; None for one the file lacks and need not have."""
    titles = [cell.strip().casefold() for cell in header]
    present = ", ".join(cell.strip() for cell in header if cell.strip())          # for the message of a column that is not there
    where: dict[str, int | None] = {}
    for column in COLUMNS:
        title = column_map.get(column, column)
        found = [index for index, other in enumerate(titles) if other == title.strip().casefold()]
        if len(found) > 1:
            raise ValueError(f"{name} has {len(found)} columns named {title!r}, so the {column} column is not clear")
        if not found and (column in REQUIRED or column in column_map):
            raise ValueError(f"{name} has no column {title!r} for {column} (its columns: {present})")
        where[column] = found[0] if found else None
    return where


def _read_table(path: Path) -> tuple[list[list[str]], str | None]:
    """Every row of the CSV file, or of the one visible sheet of the workbook that holds data (_xlsx_table), as a list of cell
    texts, blank rows included; and the mark at which a CSV file was cut, None for a workbook."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _csv_table(path)
    if suffix == ".xlsx":
        return _xlsx_table(path), None
    raise ValueError(f"{path.name} is neither a .csv nor an .xlsx file")


def _csv_table(path: Path) -> tuple[list[list[str]], str]:
    """The records of a CSV file, and the mark at which it was cut. It is read as UTF-8 (with or without a byte order mark) and,
    when that fails, as Windows-1252. The delimiter is the one, comma or semicolon, that cuts the first non-blank line into more
    cells; a tie is a comma."""
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{path.name} is neither UTF-8 nor Windows-1252 text") from exc
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max(",;", key=lambda mark: len(next(csv.reader([first], delimiter=mark), [])))
    try:
        table = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    except csv.Error as exc:
        raise ValueError(f"{path.name} cannot be read as CSV ({exc})") from exc
    return table, delimiter


def odd_row(table: list[list], header: int, commas: bool = False, text: Callable[[Any], str] = str) -> tuple[int, str] | None:
    """The first row of a CSV table below its row of column titles (the row at index `header`) that the aid does not read, as (its
    number, counted from 1; why): SURPLUS, it holds a filled cell beyond the last column title; or, in a table cut at commas
    (`commas`), UNEVEN, it is a filled row with another number of cells than the row of titles, the empty ones counted. None when
    every row can be read. Every CSV table the aid reads goes through this one check (the price list of the command and of the
    tab, the export of articles, the export of order lines): a comma in a number or a description without quotation marks cuts a
    row into more cells than it has, and read by position it would give 12 for 12,70 without a word. In a file cut at semicolons
    or tabs a decimal comma cannot cut a row, so there only a filled cell without a title refuses the file, and empty cells beyond
    the titles (trailing separators) and short rows are read. In a file cut at commas the cut cells may fall into empty cells at
    the end of the row or into the room of a short row, so every filled row must have exactly the titles' number of cells. What
    cannot be told is a comma file whose writer leaves cells out so that a cut row has, by chance, the titles' number of cells.
    The file is refused whole, with this row named. `text` gives the text of a cell (the cells themselves by default)."""
    width = max((place + 1 for place, cell in enumerate(table[header]) if text(cell).strip()), default=0)
    for number, cells in enumerate(table[header + 1:], header + 2):
        if commas and len(cells) != len(table[header]) and any(text(cell).strip() for cell in cells):
            return number, UNEVEN
        if any(text(cell).strip() for cell in cells[width:]):
            return number, SURPLUS
    return None


def _cell_text(value: object) -> str:
    """The text of one cell value of a workbook: a datetime as its day, "2026-11-16"; a number to 8 decimals (a spreadsheet keeps 17
    digits, so a price of 38.10 can be stored as 38.09999999999999) and without a fraction of zero ("8.0" is "8"); nothing as "";
    any other value as str() gives it."""
    if value is None:
        return ""
    if isinstance(value, datetime):                           # a number with a date format, which openpyxl turns into a datetime
        return value.date().isoformat()                       # only the day is kept
    if isinstance(value, float):
        value = round(value, 8)
        return str(int(value)) if value.is_integer() else format(Decimal(repr(value)), "f")
    # Text, an integer, a boolean. A cell typed as an ISO date comes back from openpyxl as a plain date, which str() writes in the same
    # form, "2026-11-16". A time or a duration cell comes back as a time or a timedelta, whose text no date reads.
    return str(value)


def _xlsx_table(path: Path, cell: Callable[[Any], Any] = _cell_text, values_only: bool = True) -> list[list]:
    """The rows of the one sheet of a workbook that the aid reads, a formula as the value it last had. Each cell is what `cell`
    makes of the value openpyxl gives for it: by default its text (_cell_text), as the price list reads it. With `values_only`
    False `cell` is given the cell itself instead (an openpyxl read-only cell, with its value and its number format, or an empty
    cell where the row holds nothing): the reader of the PO export (aid/readers/po_export.py) needs to know which cells hold a
    number, a date or a percentage. The file is opened here and openpyxl reads the open file, so that the handle is closed whatever
    fails inside openpyxl. A workbook that cannot be read raises ValueError.

    The sheet read is the one the buyer sees, for every Excel file the aid reads (the price list of the tab and of the command, the
    export of articles, the export of order lines, the samples): a hidden sheet is never read; the one visible sheet that holds
    data (a cell that is not blank) is read, whatever its place and whichever sheet the workbook was saved with in view; more than
    one such sheet raises SeveralSheets with their names and their rows, so that no sheet is read or left out without a word; a
    workbook without such a sheet has no rows, unless a hidden sheet holds data, which raises OnlyHidden, so that the refusal
    can say where the data stands."""
    with open(path, "rb") as source:                          # a file that cannot be opened raises its OSError here
        try:
            with contextlib.closing(load_workbook(source, read_only=True, data_only=True)) as workbook:
                filled = []
                for sheet in (sheet for sheet in workbook.worksheets if sheet.sheet_state == "visible"):
                    sheet.reset_dimensions()                  # a file with a wrong dimension record would give only its first cell
                    rows = [list(row) for row in sheet.iter_rows(values_only=values_only)]
                    if any(not _blank(item if values_only else item.value) for row in rows for item in row):
                        filled.append((sheet.title, rows))
                if len(filled) > 1:
                    raise SeveralSheets([title for title, _ in filled], [[[cell(item) for item in row] for row in rows] for _, rows in filled])
                if filled:
                    return [[cell(item) for item in row] for row in filled[0][1]]
                unseen = []                                   # no sheet the buyer sees holds data: does one he does not see?
                for sheet in (sheet for sheet in workbook.worksheets if sheet.sheet_state != "visible"):
                    sheet.reset_dimensions()
                    rows = [list(row) for row in sheet.iter_rows(values_only=values_only)]
                    if any(not _blank(item if values_only else item.value) for row in rows for item in row):
                        unseen.append([[cell(item) for item in row] for row in rows])
                if unseen:
                    raise OnlyHidden(path.name, unseen)
                return []
        except (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, KeyError, SyntaxError, OSError) as exc:
            # Not a zip file; a zip file with bad data, a method or version zipfile does not know (NotImplementedError, a kind of
            # RuntimeError), a member its directory marks as encrypted (RuntimeError: no password is given) or offsets that lead nowhere
            # (the seek is an OSError); one without the parts of a workbook; bad XML.
            raise ValueError(f"{path.name} cannot be read as an .xlsx workbook ({exc})") from exc


def _blank(value: object) -> bool:
    """Whether a cell value of a workbook holds nothing: no value, or a text of spaces only."""
    return value is None or (isinstance(value, str) and not value.strip())


# --- the article prices of an Exact export, kept per supplier ---

class ArticleRow(NamedTuple):
    """One article of an export of articles, as read_articles keeps it."""
    row: int                                     # its row in the file, counted from 1 as a spreadsheet counts
    supplier: str | None                         # as its supplier cell names it; None for an export without a supplier column
    item_code: str
    price: str                                   # the purchase price per piece, in normal form
    text: str                                    # the text of its price cell
    kind: str = "text"                           # "number" for a number cell of a workbook, which no convention reads; else "text"


class Articles(NamedTuple):
    """What read_articles read: the articles kept, in the order of the file, and the rows left out by their reason (LEFT_OUT)."""
    rows: list[ArticleRow]
    left_out: dict[str, list[int]]               # reason -> the rows, counted from 1; a reason without a row is not in it


class StoredPrices(NamedTuple):
    """The stored article prices of one supplier (stored_prices)."""
    stored_at: str                               # when its export was stored: "2026-11-20T09:15:00"
    file: str                                    # the name of that export
    prices: dict[str, str]                       # item code -> the purchase price per piece, in normal form


def read_articles(export: Path, columns: dict[str, str], decimal_comma: bool = False) -> Articles:
    """Read the export of articles at `export` with the column map `columns` (the aid's field -> the column title of the file:
    item_code and price, and supplier for an export that names the supplier of each row); `decimal_comma` is the convention of the
    numbers the file writes as text. See the module docstring for the file, the rows left out and the refusals (ExportRefused)."""
    # Imported here: the export reader imports this module's workbook helpers, so neither can import the other while it loads.
    from aid.readers.po_export import ExportRefused, export_table

    export = Path(export)
    unknown = sorted(set(columns) - set(ARTICLE_FIELDS))
    if unknown:
        raise ExportRefused(f"the article columns name {unknown}, which are not fields of an article ({', '.join(ARTICLE_FIELDS)})", "map")
    titles = {field: title.strip() for field, title in columns.items()}
    for field in ("item_code", "price", *titles):
        if not titles.get(field):
            raise ExportRefused(f"the article columns name no column for {field}", "map")
    table, header = export_table(export, titles)
    where = {}
    for field, title in titles.items():
        places = [place for place, cell in enumerate(table[header]) if _folded(cell.text) == _folded(title)]
        if len(places) > 1:
            raise ExportRefused(f"{export.name}, row {header + 1}: the column {title!r} stands twice", "double_column", column=title,
                                row=header + 1)
        where[field] = places[0]
    wanted = {_folded(title) for title in titles.values()}
    read: list[ArticleRow] = []
    left_out: dict[str, list[int]] = {}
    for number, row in enumerate(table[header + 1:], header + 2):
        if not any(cell.text.strip() for cell in row) or wanted <= {_folded(cell.text) for cell in row}:
            continue                                 # a blank row, or the row of column titles again
        code, price = (row[where[field]] if where[field] < len(row) else None for field in ("item_code", "price"))
        named = row[where["supplier"]] if "supplier" in where and where["supplier"] < len(row) else None
        if code is None or not code.text.strip() or code.kind in _NO_CODE:
            left_out.setdefault("no_item_code", []).append(number)
        elif "supplier" in where and (named is None or not named.text.strip() or named.kind in _NO_CODE):
            left_out.setdefault("no_supplier", []).append(number)
        elif (value := _article_price(price, decimal_comma)) is None:
            left_out.setdefault("no_price", []).append(number)
        else:
            read.append(ArticleRow(number, named.text.strip() if named is not None else None, code.text.strip(), value, price.text.strip(),
                                   price.kind))
    listed: dict[tuple, list[int]] = {}              # (the supplier as compared, the item code) -> the rows that list the article
    for article in read:
        listed.setdefault((_folded_name(article.supplier or ""), article.item_code), []).append(article.row)
    twice = sorted(number for rows in listed.values() if len(rows) > 1 for number in rows)
    if twice:
        left_out["twice"] = twice
    return Articles([article for article in read if article.row not in set(twice)],
                    {reason: left_out[reason] for reason in LEFT_OUT if reason in left_out})


def store_current(cases_root: Path, export: Path, columns: dict[str, str], decimal_comma: bool, now: datetime,
                  supplier: str | None = None) -> tuple[list[str], int, int]:
    """Store the article prices of the export at `export` (read_articles) in <cases_root>/exact/article_prices.json, at `now` to the
    second, and return (the suppliers whose prices were stored, in the order of the file; the articles kept; the rows left out).
    With a supplier column in `columns` the supplier of a row is the one its cell names and `supplier` is not looked at: each
    supplier with an article kept gets the prices of this export in the place of those stored for it. Without one, the articles are
    those of `supplier`, the name the buyer gave, which must not be empty (ValueError): its stored prices become those of this
    export. Every other supplier's prices stay as they are. A supplier whose name differs from a stored one in capitals or spaces
    only is that stored supplier. The export is read before anything is written, so a refused export stores nothing and leaves
    what was stored: one the reader refuses (ExportRefused: a workbook with more than one visible sheet with data among them),
    a supplier's name, given or in a kept row, that is no name (NameRefused, with the row of the export for a name in a row; see
    is_supplier_name) and an export that gives no article (NoArticle), so that an export read with the wrong columns never
    empties a supplier's prices. A stored file that cannot be
    read as one holds nothing that could be kept, and one that cannot be opened raises its OSError, as does a write that fails,
    which leaves the file as it was."""
    read = read_articles(export, columns, decimal_comma)
    named = "supplier" in columns
    own = (supplier or "").strip()
    if not named and not own:
        raise ValueError("the export has no supplier column, so the supplier of its articles must be named")
    unnamed = next((article for article in read.rows if not is_supplier_name(article.supplier)), None) if named else None
    if unnamed is not None or not (named or is_supplier_name(own)):
        raise NameRefused("a supplier's name is too long, or holds a line break or a control character", unnamed.row if unnamed else None)
    if not read.rows:
        raise NoArticle(f"{Path(export).name} gives no article: no row has an item code and a price that can be read")
    with _STORING:                                   # the file is read and written in one go, so that two exports stored at
        try:                                         # the same moment both land
            held = stored_prices(cases_root)
        except ValueError:                           # no file that can be read as one: nothing of it can be kept
            held = {}
        fresh: dict[str, dict[str, str]] = {} if named else {supplier_named(own, held): {}}
        for article in read.rows:
            name = supplier_named(article.supplier, [*held, *fresh]) if named else next(iter(fresh))
            fresh.setdefault(name, {})[article.item_code] = article.price
        if fresh:
            at, file = now.isoformat(timespec="seconds"), Path(export).name
            held.update({name: StoredPrices(at, file, prices) for name, prices in fresh.items()})
            _write_whole(_articles_file(cases_root), {"suppliers": {name: {"stored_at": one.stored_at, "file": one.file, "prices": one.prices}
                                                                    for name, one in held.items()}})
    return list(fresh), len(read.rows), sum(len(rows) for rows in read.left_out.values())


def stored_prices(cases_root: Path) -> dict[str, StoredPrices]:
    """The stored article prices, supplier -> (when its export was stored, the name of that export, its prices), in the order they
    were first stored; {} without the file. A file that does not hold what store_current writes raises ValueError, which names the
    file and never a value of it; a file that cannot be opened raises its OSError."""
    path = _articles_file(cases_root)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {}
    try:
        suppliers = json.loads(data.decode("utf-8"))["suppliers"]
        found = {}
        for name, one in suppliers.items():
            at, file, prices = one["stored_at"], one["file"], one["prices"]
            datetime.fromisoformat(at)
            if not (isinstance(file, str) and all(isinstance(value, str) for pair in prices.items() for value in pair)):
                raise ValueError("no stored prices")
            found[name] = StoredPrices(at, file, dict(prices))
    except (ValueError, TypeError, KeyError, AttributeError):         # no JSON, not UTF-8, or not the form store_current writes
        raise ValueError(f"{path.name} does not hold the stored article prices") from None
    return found


def current_prices(cases_root: Path, supplier: str) -> tuple[dict[str, str], str | None]:
    """The current prices of the supplier (item code -> the purchase price per piece, in normal form) and when its export was
    stored: its stored prices with every article price entered or seen on the list after that moment laid over them (see the module
    docstring). ({}, None) for a supplier without stored prices, and while the stored file cannot be read. An error of the list's
    two files is raised."""
    # Imported here: the list of changes reads the PO store, whose export reader imports this module's workbook helpers.
    from aid.execution.by_hand import ARTICLE_KIND, FOLDER, changes, prepared_rows

    cases_root = Path(cases_root)
    try:
        held = stored_prices(cases_root)
    except (OSError, ValueError):
        return {}, None
    name = supplier_named(supplier, held)
    if name not in held:
        return {}, None
    stored, stored_at = held[name].prices, held[name].stored_at
    prices, since, named = dict(stored), _moment(stored_at), {}
    asked_by = {row.entry_id: row.case_id for row in prepared_rows(cases_root / FOLDER, (ARTICLE_KIND,))}
    for change in changes(cases_root / FOLDER)[0]:   # the changes of an article in the order they were asked: the last one decides
        if change.kind == ARTICLE_KIND and not change.tryout and change.state in ("entered", "seen") and _moment(change.marked_at) > since:
            cases = dict.fromkeys(asked_by[entry_id] for entry_id in change.marked_ids if entry_id in asked_by)
            if not _counts_for(cases, change.article_code, name, stored, cases_root, named):
                continue
            if change.new_value is not None:
                prices[change.article_code] = change.new_value
            elif change.article_code in stored:      # put back to a price the aid never read: the export's price is what it knows
                prices[change.article_code] = stored[change.article_code]
            else:
                prices.pop(change.article_code, None)
    return prices, stored_at


def also_held(held: dict[str, StoredPrices], supplier: str) -> dict[str, list[str]]:
    """The articles that the stored prices of another supplier than `supplier` hold, each with the names of those suppliers as they
    are stored, in the order they were first stored: a row of this supplier's price list for one of them is not put on the list
    "In Exact invoeren", which keeps one price per article, and the tab names the other supplier (aid/review/pricelist_page.py)."""
    found: dict[str, list[str]] = {}
    for name, one in held.items():
        if _folded_name(name) != _folded_name(supplier):
            for code in one.prices:
                found.setdefault(code, []).append(name)
    return found


def others_asking(asked: dict, supplier: str, cases_root: Path) -> dict[str, list[str]]:
    """The articles of which the list "In Exact invoeren" holds an open change that another supplier than `supplier` asked, each
    with the names of those suppliers: `asked` is the open change of each article (ArticleLook.open of aid/execution/by_hand.py),
    and a change is another supplier's when one of the cases of its rows names another supplier, a price list by its case id and
    a case with a folder by its result (_case_supplier). A case that names no supplier is no other supplier. A row of this
    supplier's price list for such an article would take that change off the list, or join it, so the tab keeps it off the list
    as it does for an article that another supplier's stored prices hold (also_held), also when no export of that supplier is
    stored."""
    found: dict[str, list[str]] = {}
    named: dict[str, str | None] = {}
    for code, change in asked.items():
        for case_id in change.case_ids:
            if case_id not in named:
                named[case_id] = _case_supplier(Path(cases_root), case_id)
            name = named[case_id]
            if name is not None and not same_supplier(name, supplier) and not any(same_supplier(name, other) for other in found.get(code, [])):
                found.setdefault(code, []).append(name)
    return found


def with_others(first: dict[str, list[str]], second: dict[str, list[str]]) -> dict[str, list[str]]:
    """Two maps of an article to other suppliers' names as one: the names of `first`, then those of `second` that it does not hold
    already, compared as suppliers' names are."""
    found = {code: list(names) for code, names in first.items()}
    for code, names in second.items():
        held = found.setdefault(code, [])
        held += [name for name in names if not any(same_supplier(name, other) for other in held)]
    return found


def same_supplier(one: str, other: str) -> bool:
    """Whether two names are one supplier's: they differ in capitals and in spaces only."""
    return _folded_name(one) == _folded_name(other)


def is_supplier_name(name: str | None) -> bool:
    """Whether the tab takes this as a supplier's name: at most SUPPLIER_NAME_MAX characters, and no line break or control
    character. The name becomes part of the case id of each price list of the supplier, and is shown on a line of the page."""
    name = (name or "").strip()
    return len(name) <= SUPPLIER_NAME_MAX and not any(unicodedata.category(char) in _NO_NAME for char in name)


def kept_columns(cases_root: Path) -> dict[str, dict[str, str]]:
    """The columns kept for each supplier's price list, supplier -> (the aid's column name -> the column title of the list), from
    <cases_root>/exact/pricelist_columns.json; {} without the file, and for a file that does not hold what keep_columns writes:
    the columns are a help at the next list, and nothing else depends on them. A file that cannot be opened raises its OSError."""
    try:
        data = (Path(cases_root) / ARTICLES_FOLDER / COLUMNS_FILE).read_bytes()
    except FileNotFoundError:
        return {}
    try:
        suppliers = json.loads(data.decode("utf-8"))["suppliers"]
        if not all(isinstance(value, str) for columns in suppliers.values() for pair in columns.items() for value in pair):
            return {}
        return {name: dict(columns) for name, columns in suppliers.items()}
    except (ValueError, TypeError, KeyError, AttributeError):
        return {}


def keep_columns(cases_root: Path, supplier: str, columns: dict[str, str]) -> None:
    """Keep `columns` as the columns of the supplier's price list, in <cases_root>/exact/pricelist_columns.json, beside the stored
    article prices and written as they are: whole or not at all, one write at a time. The supplier is the one kept already whose
    name differs in capitals or spaces only; the columns kept for every other supplier stay. Nothing is written when the same
    columns are kept already. An error of the file system is raised, and the file is then as it was."""
    with _STORING:
        held = kept_columns(cases_root)
        name = supplier_named(supplier, held)
        if held.get(name) != dict(columns):
            held[name] = dict(columns)
            _write_whole(Path(cases_root) / ARTICLES_FOLDER / COLUMNS_FILE, {"suppliers": held})


def remove_stored(cases_root: Path, supplier: str) -> str | None:
    """Take away what the aid keeps of one supplier for the tab "Prijslijst": its stored article prices and the columns kept for
    its price list, and nothing else; no prepared change, mark or log is touched. The supplier is the stored one whose name differs
    from `supplier` in capitals or spaces only; its stored name is returned, or None when no stored prices are held under the name
    (also while the stored file cannot be read as one), and then nothing is written. Whole or not at all: the kept columns, which
    are a help only, are written first and put back as they were when the stored prices cannot be written after them; the error of
    the file system is raised. A supplier given twice under two spellings shares every article with itself, and this is the way
    out (MANUAL 4.3)."""
    with _STORING:
        try:
            held = stored_prices(cases_root)
        except ValueError:
            return None
        name = supplier_named(supplier, held)
        if name not in held:
            return None
        columns_file = Path(cases_root) / ARTICLES_FOLDER / COLUMNS_FILE
        columns = kept_columns(cases_root)
        kept_as = supplier_named(name, columns)
        columns_before = columns_file.read_bytes() if kept_as in columns else None
        if columns_before is not None:
            _write_whole(columns_file, {"suppliers": {other: one for other, one in columns.items() if other != kept_as}})
        try:
            _write_whole(_articles_file(cases_root), {"suppliers": {other: {"stored_at": one.stored_at, "file": one.file, "prices": one.prices}
                                                                    for other, one in held.items() if other != name}})
        except BaseException:
            if columns_before is not None:
                _write_whole(columns_file, columns_before)
            raise
    return name


def supplier_named(name: str | None, known) -> str:
    """The supplier a name stands for: the one of `known` (names, in their order) that differs from it in capitals and spaces only,
    else the name itself without the spaces around it; "" for no name."""
    name = (name or "").strip()
    return next((other for other in known if name and _folded_name(other) == _folded_name(name)), name)


def list_of(case_id: str) -> tuple[str, datetime] | None:
    """The supplier and the time of the price list that a case id names, as import_price_list makes it
    ("pricelist_<supplier>_<YYYYmmddTHHMMSS>"); None for any other case id."""
    found = _LIST_CASE.fullmatch(case_id)
    try:
        return (found[1], datetime.strptime(found[2], "%Y%m%dT%H%M%S")) if found else None
    except ValueError:                               # digits that are no time
        return None


def _counts_for(cases, article_code: str, supplier: str, stored: dict[str, str], cases_root: Path, named: dict[str, str | None]) -> bool:
    """Whether an article-price change counts for the supplier (see the module docstring): one of `cases`, the cases of its marked
    rows, names the supplier, or one of them names none and the supplier's stored prices hold the article. `named` keeps the supplier
    of each case looked up."""
    for case_id in cases:
        if case_id not in named:
            named[case_id] = _case_supplier(cases_root, case_id)
        if named[case_id] is None:
            if article_code in stored:
                return True
        elif _folded_name(named[case_id]) == _folded_name(supplier):
            return True
    return False


def _case_supplier(cases_root: Path, case_id: str) -> str | None:
    """The supplier that the case of an article-price change names: for a case with a folder, the supplier of its result; for a
    price list, the supplier of its case id; None when it names none, or its result cannot be read. A case id that cannot be the
    name of a folder (names_a_folder of aid/case.py) is no case's, and the file system is not asked."""
    try:
        if names_a_folder(case_id) and (cases_root / case_id / "case.json").is_file():
            supplier = load_case(cases_root, case_id).read_step("result").get("supplier")
            return supplier.strip() or None if isinstance(supplier, str) else None
    except (OSError, ValueError, AttributeError):
        return None
    listed = list_of(case_id)
    return listed[0].strip() or None if listed is not None else None


def _article_price(cell, decimal_comma: bool) -> str | None:
    """The price in one cell of an export of articles, in normal form: a number cell of a workbook is the number it holds, a text is
    read with the convention; None for a blank cell, a date, a time or an error cell, and a text that is no number."""
    if cell is None or not cell.text.strip() or cell.kind in _NO_CODE:
        return None
    return cell.text.strip() if cell.kind == "number" else normalize_number(cell.text, decimal_comma=decimal_comma)


def _folded(text: str) -> str:
    """A column title as it is compared: without the spaces around it and without regard to case, as the export reader compares one."""
    return text.strip().casefold()


def _folded_name(name: str) -> str:
    """A supplier's name as it is compared: without regard to capitals and to spaces, wherever they stand."""
    return "".join(name.split()).casefold()


def _moment(text: str) -> float:
    """A time of the stored prices or of a mark as a moment; a time without a zone is the PC's own."""
    return datetime.fromisoformat(text).timestamp()


def _articles_file(cases_root: Path) -> Path:
    return Path(cases_root) / ARTICLES_FOLDER / ARTICLES_FILE


def _write_whole(path: Path, data: dict | bytes) -> None:
    """Write data to `path`, as JSON or, for bytes, as they are (a file put back as it was), whole or not at all: to a temporary
    file with a name of its own beside it, which then replaces the file. The folder is made when it is missing; a write that fails
    removes the temporary file and leaves the file as it was."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        if isinstance(data, bytes):
            with os.fdopen(handle, "wb") as f:
                f.write(data)
        else:
            with os.fdopen(handle, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


# --- the command line ---

def _read_current(path: Path) -> dict[str, str]:
    """The current prices from a CSV file with the columns item_code and price (the price read as in a list); a row without an item code,
    a price that is not a number or an item code twice raises ValueError."""
    current: dict[str, str] = {}
    for number, raw in _read_list(path, None):
        code, price = raw["item_code"], normalize_number(raw["price"])
        if not code:
            raise ValueError(f"{path.name}, row {number}: no item code")
        if price is None:
            raise ValueError(f"{path.name}, row {number}: the price of {code} is {raw['price']!r}, which is not a number")
        if code in current:
            raise ValueError(f"{path.name}, row {number}: {code} is listed twice")
        current[code] = price
    return current


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    """Run the command of the module docstring; `now` is the time of the run (default: the clock). Returns 0 when the import ran,
    whatever it skipped, and 1 when the list or the current prices cannot be read or any write failed; a command line that cannot be run
    exits with 2."""
    parser = argparse.ArgumentParser(
        prog="python3 -m aid.pricelist", description="Import a supplier's price list: prepare each changed article price for the buyer.")
    parser.add_argument("--file", type=Path, required=True, help="the price list, a .csv or .xlsx file")
    parser.add_argument("--supplier", required=True, help="the supplier the list is from, e.g. S1; it is recorded with every change")
    parser.add_argument("--current", type=Path, required=True, help="the current prices, a CSV file with the columns item_code and price")
    parser.add_argument("--prepared", type=Path, required=True, help="the folder where prepared_changes.csv is written")
    parser.add_argument("--write-log", type=Path, help="the folder whose write_log.jsonl gets every change, for the undo "
                                                       "(default: <cases_root>/pricelists/<YYYY-MM-DD> of --settings)")
    parser.add_argument("--settings", type=Path, help="the settings file whose cases_root holds the default write-log folder")
    args = parser.parse_args(argv)
    if args.write_log is None and args.settings is None:
        parser.error("the argument --write-log is required without --settings")
    now = now or datetime.now()
    try:
        log_folder = args.write_log or load_settings(args.settings).cases_root / "pricelists" / f"{now:%Y-%m-%d}"
        current = _read_current(args.current)
        result = import_price_list(args.file, args.supplier, current, PreparedWriter(args.prepared), now)
    except SeveralSheets as several:                     # the names of the sheets are what the user of the command acts on
        print(f"cannot read: {args.file.name} has more than one visible sheet with data ({', '.join(several.names)}); the aid reads one "
              "sheet: save the sheet you mean as a file of its own", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"cannot read: {exc}", file=sys.stderr)
        return 1
    try:
        _log(log_folder, result.updated)
    except OSError as exc:
        print(f"cannot write the write log in {log_folder}: {exc}", file=sys.stderr)
        log_failed = True
    else:
        log_failed = False
    for outcome in result.outcomes:
        if isinstance(outcome, WriteLogEntry):
            print(f"updated {outcome.op.article_code} from {outcome.previous} to {outcome.new} ({outcome.status})")
            continue
        item, reason = outcome
        print(f"error {item}: {reason.removeprefix(ERROR_PREFIX)}" if _failed(reason) else f"skipped {item}: {reason}")
    failed = len(result.errors)
    print(f"{args.supplier}, {args.file.name}: {len(result.updated)} updated, {len(result.skipped) - failed} skipped, {failed} errors")
    if result.expired:
        print("Some rows are past their valid_to: ask the supplier for a current list.")
    return 1 if failed or log_failed else 0


def _log(folder: Path, entries: list[WriteLogEntry]) -> None:
    """Append the entries to the write log of `folder`, which is made when there is an entry to log."""
    if entries:
        Path(folder).mkdir(parents=True, exist_ok=True)
        log = WriteLog(folder)
        for entry in entries:
            log.append(entry)


if __name__ == "__main__":
    raise SystemExit(main())
