"""The price-list import of the checking aid: a supplier's price list or dealer-portal download updates the article prices through the
same write path as every other change, so that each change is logged and can be undone. Scraping a webshop is out of scope.

import_price_list() reads the list, decides row by row and has the writer apply one article_price operation (rule P01) for every price
that changed. The entries the writer returns are in ImportResult.updated and the rows that were not imported in ImportResult.skipped;
ImportResult.outcomes has both in the order of the file. As with every writer, appending the entries to the write log is the caller's job
(aid/execution/writer.py); the command line appends them to the write log of a folder of its own (see below), so that the undo can reverse
an import.

The list is a CSV file, comma or semicolon separated (the header row decides which), in UTF-8 or Windows-1252, or the first sheet of an
.xlsx workbook, with the column names in its first row. The aid's columns are item_code, price, basis, unit, min_quantity, valid_from and
valid_to. column_map gives the name the file uses for each of them (default: the same name); names are matched without regard to case
and to spaces around them. item_code and price must be columns of the file, and so must every column the map names; the others may be
missing. A blank row is ignored. A date in a workbook cell is read as the day it shows and a number as its value to 8 decimals, so that a
price a spreadsheet stored as 38.09999999999999 is 38.1. A list that cannot be read at all (not a .csv or .xlsx file, a damaged file,
text that is neither UTF-8 nor Windows-1252, a missing column) raises ValueError before anything is written, and so does a price in
current_prices that is not a number.

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
"""
import argparse
import contextlib
import csv
import io
import re
import sys
import zipfile
import zlib
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from pydantic import BaseModel

from aid.execution.ops import WriteOp
from aid.execution.writer import PreparedWriter, WriteLog, WriteLogEntry, Writer, WriterError
from aid.schema import normalize_basis, normalize_date, normalize_number, normalize_unit
from aid.settings import load_settings

COLUMNS = ("item_code", "price", "basis", "unit", "min_quantity", "valid_from", "valid_to")
REQUIRED = ("item_code", "price")                    # the columns the file must have; the others may be missing
RULE_ID = "P01"
ERROR_PREFIX = "error: "                             # the start of the reason of a row whose write failed
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class ImportResult(BaseModel):
    """What one import did. `skipped` is (item code, reason) for each row not imported, those whose write failed included (see the
    module docstring)."""
    updated: list[WriteLogEntry]                 # one entry per changed price, in the order of the file
    skipped: list[tuple[str, str]]
    expired: bool                                # some row is past its valid_to
    supplier: str = ""                           # the supplier the list is from
    outcomes: list[WriteLogEntry | tuple[str, str]] = []     # the entry or the skip of each row, in the order of the file

    @property
    def errors(self) -> list[tuple[str, str]]:
        """The skipped rows whose write failed, those with a reason that starts with "error: "."""
        return [skip for skip in self.skipped if _failed(skip[1])]


def import_price_list(path: Path, supplier: str, current_prices: dict[str, str], writer: Writer, now: datetime,
                      column_map: dict[str, str] | None = None) -> ImportResult:
    """Import the price list at `path`, which `supplier` sent, against `current_prices` (item code to the current price in the normal
    form of aid/schema.py), and have `writer` apply the changes. `column_map` maps the aid's column names to the file's (see the module
    docstring for the rows, the reasons for skipping one and the write)."""
    path = Path(path)
    rows = _read_list(path, column_map)                       # a list that cannot be read raises here, before any write
    current = {code: _current_price(code, text) for code, text in current_prices.items()}
    today = now.date()
    case_id = f"pricelist_{supplier}_{now:%Y%m%dT%H%M%S}"
    outcomes: list[WriteLogEntry | tuple[str, str]] = []      # the entry or the (item, reason) of each row, in the order of the file
    settled: set[str] = set()                                 # the articles an earlier row of the file settled
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
    return ImportResult(updated=updated, skipped=skipped, expired=expired, supplier=supplier, outcomes=outcomes)


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

def _read_list(path: Path, column_map: dict[str, str] | None) -> list[tuple[int, dict[str, str]]]:
    """The rows of the list below its header row as (row number, trimmed text of each of the aid's columns), blank rows left out. The
    row number is the position in the file, counted from 1 as a spreadsheet counts; a column the file lacks is blank."""
    unknown = sorted(set(column_map or {}) - set(COLUMNS))
    if unknown:
        raise ValueError(f"the column map names {unknown}, which are not columns of the import ({', '.join(COLUMNS)})")
    table = _read_table(path)
    filled = [index for index, cells in enumerate(table) if any(cell.strip() for cell in cells)]      # the rows that are not blank
    if not filled:
        raise ValueError(f"{path.name} is empty")
    where = _columns(path.name, table[filled[0]], column_map or {})
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


def _read_table(path: Path) -> list[list[str]]:
    """Every row of the CSV file or of the first sheet of the workbook as a list of cell texts, blank rows included."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _csv_table(path)
    if suffix == ".xlsx":
        return _xlsx_table(path)
    raise ValueError(f"{path.name} is neither a .csv nor an .xlsx file")


def _csv_table(path: Path) -> list[list[str]]:
    """The records of a CSV file. It is read as UTF-8 (with or without a byte order mark) and, when that fails, as Windows-1252. The
    delimiter is the one, comma or semicolon, that cuts the first non-blank line into more cells; a tie is a comma."""
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
        return list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    except csv.Error as exc:
        raise ValueError(f"{path.name} cannot be read as CSV ({exc})") from exc


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
    """The rows of the first sheet of a workbook, a formula as the value it last had. Each cell is what `cell` makes of the value
    openpyxl gives for it: by default its text (_cell_text), as the price list reads it. With `values_only` False `cell` is given the
    cell itself instead (an openpyxl read-only cell, with its value and its number format, or an empty cell where the row holds
    nothing): the reader of the PO export (aid/readers/po_export.py) needs to know which cells hold a number, a date or a percentage.
    The file is opened here and openpyxl reads the open file, so that the handle is closed whatever fails inside openpyxl. A workbook
    that cannot be read raises ValueError."""
    with open(path, "rb") as source:                          # a file that cannot be opened raises its OSError here
        try:
            with contextlib.closing(load_workbook(source, read_only=True, data_only=True)) as workbook:
                if not workbook.worksheets:
                    return []
                sheet = workbook.worksheets[0]
                sheet.reset_dimensions()                      # a file with a wrong dimension record would give only its first cell
                return [[cell(item) for item in row] for row in sheet.iter_rows(values_only=values_only)]
        except (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, KeyError, SyntaxError, OSError) as exc:
            # Not a zip file; a zip file with bad data, a method or version zipfile does not know (NotImplementedError, a kind of
            # RuntimeError), a member its directory marks as encrypted (RuntimeError: no password is given) or offsets that lead nowhere
            # (the seek is an OSError); one without the parts of a workbook; bad XML.
            raise ValueError(f"{path.name} cannot be read as an .xlsx workbook ({exc})") from exc


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
