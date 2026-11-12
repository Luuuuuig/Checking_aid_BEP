"""The reader of the ERP export: a spreadsheet file with one row per purchase order line, as the buyer saves it from an overview of the
ERP. A cart is checked before its order is released and sent, so no PO PDF exists at that moment, and the export gives the cart its PO
side. read_export() reads the file into one Document of kind "po" per PO number; PoStore.from_export (aid/po_store.py) files them.

The file. A .csv file is read as UTF-16 when it begins with a UTF-16 byte order mark, and otherwise as UTF-8, with or without a byte
order mark, or else as Windows-1252. It is cut at semicolons, at commas or at tabs (DELIMITERS): the first of these marks, in that
order, under which a row of the file holds every column title of the map (see the header row below), so a title line above the column
titles may hold another mark or none. An .xlsx file is the one visible sheet of the workbook that holds data, never a hidden sheet
(a workbook with more than one such sheet is refused with their names), a formula as the value it last had. Any other
file is refused; an old .xls workbook is saved as .xlsx first. The rows are numbered as a spreadsheet numbers them: the first row of
the file is row 1, and a blank row counts.

The map. `columns` maps the aid's field names (EXPORT_FIELDS) to the column titles of the file. It must name po_number and item_code;
a field it leaves out stays missing on every line. Titles are compared without regard to case and to spaces around them. An empty map
means every field whose name is a column title of the file, so the made-up exports, which name each column after its field, need no
map.

The header row is the first row of the file that holds every column title of the map (with an empty map: the titles po_number and
item_code). The rows above it, a title or a line with the date of the overview, are passed over and not counted. A title of the map
that stands twice in the header row is refused, because it is not clear which column is meant.

The rows below the header row. A blank row is ignored. These filled rows are no order line, and each is passed over and counted in
ExportReading.passed_over: a row that holds every column title of the map again (the header row repeated lower down, as in a report
saved page by page or in two exports pasted into one file); a row without an item code, such as a total, a group row or a text line; a
row whose item code is a date, a time, a duration or an error cell (see the values); and, when a quantity column is read, a row with an
item code and a blank quantity, such as a total or a group row that prints its word in the item code column. ExportReading lists them
by their kind as well: without_item_code, without_quantity and titles_again, and an item code that is no value under
unread["item_code"]. Every other row is one order line of the PO whose number it holds. A row with an item code and no PO number takes,
when `fill_down` is set, the PO number of the nearest row above it whose PO cell is filled, whether that row is an order line or a row
that was passed over, a repeated header row excepted. So an overview that prints the number once for each PO, on its first line or on a
group row or a text line above its lines, gives each line its own PO. Without `fill_down`, or with no such row above it, a row with an
item code and no PO number refuses the whole file. The POs come in the order in which the file first names them, each with its lines in
the order of the file, and ExportReading.last_line_po names the PO that the last row read as an order line went to: with `fill_down`,
the PO a total row at the end of the file joins, which in an export not sorted by PO number is not the PO the file names last. A line
has the line id of its row when the map names line_id; a line id that is blank, or that an earlier line of the same PO has, refuses the
file. Without a line_id column the lines of each PO are numbered 1, 2, 3. When the map names supplier, the PO's supplier is the first
filled supplier cell of its rows: its order lines and, with `fill_down`, the rows they take its number from (a text line or a group row
that names the supplier gives it to the PO of the lines below it).

The PO number of a line is the text of its PO cell, or of the cell it takes with `fill_down`, without the spaces around it. A date,
a time, a duration or an error cell of a workbook is no PO number; a PO number holds at least one digit, so the word of a total in the
PO column ("Totaal") is none; and it names the PO's folder in the PO store, so it must be a folder name on every system the aid runs
on (is_folder_name). A cell that fails one of these refuses the file, naming the row of that cell. The PO cell of a row that was
passed over is held to this only when a line below it takes its number.

The values. A blank cell is missing: DocValue(). Every other cell keeps its text and gets the normal form of aid/schema.py:

    quantity, unit_price, discount_pct, net_price    a number cell of a workbook is the number it holds, whatever `decimal_comma`
                                                     says (12.125 stays 12.125); a text is read by normalize_number with
                                                     `decimal_comma` as the convention of the file
    price_basis                                      a number cell that holds a whole number above zero is that number; a text is
                                                     read by normalize_basis
    unit                                             normalize_unit
    delivery_date                                    a date cell of a workbook is its day; a text is read by normalize_date after a
                                                     time of day at its end is left out ("24-11-2026 00:00:00" is 2026-11-24)
    item_code, supplier                              the text without the spaces around it; a date cell is no value
    description                                      the text without the spaces around it

Every cell of a .csv file is a text. The text of a number or a date cell of a workbook is the one the price list reads: a number to
8 decimals without a fraction of zero, so that a whole number is its digits (250301.0 is "250301"), and a date as "2026-11-24". A
number cell whose number format shows a percentage (a % that is not quoted or escaped in the format) is the number of percent it
shows: 0.05 shown as 5% is "5", so a discount column formatted as a percentage is read right. A date cell is no number and no price
basis, a number cell is no date, and a time or a duration cell is no value in any field. An error cell (#N/A, #VALUE!, #REF!,
#DIV/0!, #NAME?, #NUM!, #NULL!), whether the workbook holds it as an error or as the text of one, is no value in any field either;
openpyxl gives a date cell whose serial lies beyond the dates as #VALUE!, and the warning it gives with the cell's serial is kept
from the console. A number cell holds no zeros in front, so the columns of the PO number, the line id, the item code and the supplier
should be text cells. A cell whose text is no value keeps its text, has no value and is uncertain, so the comparison leaves it alone;
ExportReading.unread lists, under its field, the row of each such cell of an order line, of each supplier cell of a row a PO number
is taken from, and of each item code cell that is a date, a time, a duration or an error. No unit and no price basis is ever
written into a line that states none: the comparison leaves a price or a quantity alone when only one side states the price basis or
the unit, so a constant here would switch that check off for every document that prints none.

The refusals. read_export raises ExportRefused, a ValueError, before it returns anything, so that the store files nothing of a refused
file: for a map that names an unknown field, that names a field without a column title or that lacks po_number or item_code; for a
file that is neither .csv nor .xlsx; for a workbook that openpyxl cannot read or that holds a cell it cannot convert, and for a CSV
file that the csv module cannot cut; for a CSV file that no mark the aid reads cuts, so that every row of it is one cell, whose text
holds NUL characters or that is in no encoding the aid reads (the error then names the marks and the encodings the aid reads); for a
column of the map that no row holds; for a title of the map that stands twice in the header row; for a row with an item code and no
PO number (see above); for a PO number that is a date, a time or an error cell, that holds no digit or that cannot be a folder name;
and for a row with an item code whose mapped line id is blank, is a date, a time or an error cell, or stands on an earlier line of the
same PO. The text of the error names the
file with the column or the row (where it is known), or the field of the map, and never what a cell holds, nor the text of an error
underneath it, which a traceback does not show either; ExportRefused.reason, .column and .row give the same to a caller that words
the refusal itself. A file that cannot be opened raises its OSError.

The table. export_table gives every row of a file as its cells with the header row the map finds, csv_text the text of a CSV file,
csv_rows that text cut at one mark, and workbook_rows the rows of a workbook, each cell with its text and its kind, as this reader reads
them: the block "Koppeling met Exact" of the settings tab (aid/review/exact_settings.py) shows a sample with them and tells a number
cell from a text. read_export_table is read_export with the table it read, so that the block reads a sample once for a trial.
"""
import codecs
import csv
import io
import re
import unicodedata
import warnings
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple

from pydantic import BaseModel

from aid.pricelist import SURPLUS, OnlyHidden, SeveralSheets, _cell_text, _xlsx_table, odd_row
from aid.schema import DocLine, DocValue, Document, normalize_basis, normalize_date, normalize_number, normalize_unit

EXPORT_SUFFIXES = (".xlsx", ".csv")
EXPORT_FIELDS = ("po_number", "line_id", "item_code", "description", "quantity", "unit", "unit_price", "price_basis", "discount_pct",
                 "net_price", "delivery_date", "supplier")
REQUIRED = ("po_number", "item_code")                # the fields a map must name
DELIMITERS = (";", ",", "\t")                        # the marks a CSV file is cut at, in the order in which they are tried
FOLDER_NAME_MAX = 100                                # the most characters a PO number has, as it names a folder of the PO store
# The reasons of ExportRefused: a wrong map; a file of another type; a workbook or a CSV file that cannot be read; a CSV file with a
# delimiter or an encoding the aid does not read; a column of the map that no row holds; a title that stands twice in the header row;
# an item code without a PO number; a PO number that is a date, a time or an error cell; a PO number without a digit; a PO number
# that is no folder name; an item code without a line id; a line id that is a date, a time or an error cell; a line id that an
# earlier line of the same PO has; a row of a CSV file with a filled cell beyond the last column title; a workbook with more than
# one visible sheet with data.
REFUSALS = ("map", "file_type", "unreadable", "delimiter_or_encoding", "no_column", "double_column", "no_po_number", "po_number_date",
            "po_number_no_digit", "po_number_no_folder", "no_line_id", "line_id_date", "double_line_id", "surplus_cells",
            "cell_count", "several_sheets", "hidden_sheet")
_NUMBERS = ("quantity", "unit_price", "discount_pct", "net_price")                                   # the fields read as a number
_LINE_VALUES = tuple(field for field in EXPORT_FIELDS if field not in ("po_number", "line_id", "supplier"))      # the values of a DocLine
_NO_IDENTIFIER = ("date", "time", "error")           # the kinds of cell that hold no PO number, line id, item code or supplier
_NO_VALUE = ("time", "error")                        # the kinds of cell that hold no value of any field
_ERRORS = frozenset({"#N/A", "#VALUE!", "#REF!", "#DIV/0!", "#NAME?", "#NUM!", "#NULL!"})        # the error cells of a spreadsheet
_DIGIT = re.compile(r"[0-9]")
_TIME_OF_DAY = re.compile(r"(?:\s+|T)[0-9]{1,2}:[0-9]{2}(?::[0-9]{2}(?:[.,][0-9]+)?)?$")             # " 00:00", "T14:30:00", " 8:05:00.000"
# The parts of a number format that only write characters, so that a % in them shows no percentage: a quoted text, an escaped
# character, a part in brackets ([Red], [$EUR-413]), and the character behind _ (a space as wide as it) or * (it fills the cell).
_LITERAL = re.compile(r'"[^"]*"|\\.|\[[^\]]*\]|[_*].')
_NOT_IN_A_NAME = frozenset('/\\:*?"<>|')             # a path, and the characters Windows refuses in the name of a folder
_DEVICES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))})
_NOT_READ = "the aid reads CSV cut at semicolons, commas or tabs, in UTF-8, Windows-1252 or UTF-16 with its byte order mark"
_UNREADABLE = "cannot be read as an .xlsx workbook: the file is damaged, or a cell holds what its type does not allow"


class ExportReading(BaseModel):
    """What read_export read from one export file (see the module docstring)."""
    documents: list[Document]             # one of kind "po" per PO number, in the order of the file, read by "export:<file name>"
    rows: int                             # the rows read as order lines
    passed_over: int                      # the filled rows below the header row that are no order line (see the module docstring)
    unread: dict[str, list[int]]          # field -> the rows whose cell holds no value of it; the fields in the order of EXPORT_FIELDS
    # The rows passed over by their kind (passed_over counts them all, with the rows of unread["item_code"], whose item code is a date,
    # a time or an error cell): an item code cell that is blank; an item code and a blank quantity; the row of column titles again.
    without_item_code: list[int] = []
    without_quantity: list[int] = []
    titles_again: list[int] = []
    last_line_po: str | None = None       # the PO number that the last row read as an order line went to; None without an order line


class ExportRefused(ValueError):
    """An export that read_export refuses. The text names the file with the column or the row, or the field of the map, and never
    what a cell holds. `reason` is one of REFUSALS, `column` the column title the refusal is about and `row` the row of the file,
    counted from 1; each of the two is None when the refusal names none. `names` are the names of the sheets of a workbook that is
    refused for its sheets (several_sheets), which the text does not hold, and `tables` their rows, or the rows of the hidden sheets
    of a workbook whose only data stands on a hidden sheet (hidden_sheet), by which is_export tells whether such a workbook is an
    export at all."""

    def __init__(self, text: str, reason: str, column: str | None = None, row: int | None = None, names: list[str] | None = None,
                 tables: list | None = None):
        super().__init__(text)
        self.reason, self.column, self.row, self.names, self.tables = reason, column, row, names or [], tables or []


class _Cell(NamedTuple):
    """One cell of the file: its text, and for a cell of a workbook whether it holds a number, a date, a time (a time of day or a
    duration) or an error (#N/A and the like)."""
    text: str
    kind: str = "text"                    # "text", "number", "date", "time" or "error"


_BLANK = _Cell("")                        # the cell of a row that is shorter than the header row


class _PoCell(NamedTuple):
    """The filled PO cell of a row, with the row's number and its supplier cell, for the rows below it that take its PO number."""
    cell: _Cell
    row: int
    supplier: _Cell


def read_export(path: Path, columns: dict[str, str], decimal_comma: bool = False, fill_down: bool = False) -> ExportReading:
    """Read the export at `path` with the column map `columns`: one Document of kind "po" per PO number, with how many rows were read
    as order lines, how many filled rows below the header row were passed over (and which, by their kind), and the rows of the cells
    that are no value. `decimal_comma` is the convention of the numbers the file writes as text, and `fill_down` lets a row without a
    PO number take the one that stands nearest above it. See the module docstring for the file, the map, the rows, the values and the
    refusals."""
    return read_export_table(path, columns, decimal_comma, fill_down)[0]


def read_export_table(path: Path, columns: dict[str, str], decimal_comma: bool = False,
                      fill_down: bool = False) -> tuple[ExportReading, list[list[_Cell]], int]:
    """read_export, with the table it read (export_table: every row of the file as its cells) and the index of the header row in it, for
    a caller that needs the cells themselves and reads the file once: the block of the settings tab tells a number cell from a text."""
    path = Path(path)
    name = path.name
    _check_map(columns)
    titles = {field: title.strip() for field, title in columns.items()} or {field: field for field in REQUIRED}
    table, header = export_table(path, titles)
    where = _columns(name, table[header], header + 1, titles if columns else None)
    wanted = {_folded(title) for title in titles.values()}          # a row below the header row that holds them all repeats it
    lines: dict[str, list[DocLine]] = {}             # PO number -> its lines; the POs and the lines in the order of the file
    ids: dict[str, set[str]] = {}                    # PO number -> the line ids of its lines so far
    suppliers: dict[str, DocValue] = {}              # PO number -> the first filled supplier cell of its rows
    read_rows: set[int] = set()                      # the rows whose supplier cell has been read
    unread: dict[str, list[int]] = {}
    without_item_code: list[int] = []                # the rows passed over by their kind (ExportReading)
    without_quantity: list[int] = []
    titles_again: list[int] = []
    passed_over, above = 0, None                     # `above`: the _PoCell of the nearest row above whose PO cell is filled
    last_line_po: str | None = None                  # the PO number of the last row read as an order line
    for number, row in enumerate(table[header + 1:], header + 2):
        if not any(cell.text.strip() for cell in row):
            continue                                 # a blank row
        if wanted <= {_folded(cell.text) for cell in row}:
            passed_over += 1                         # the header row again, which gives the rows below it no PO number
            titles_again.append(number)
            continue
        cells = {field: row[at] if at < len(row) else _BLANK for field, at in where.items()}
        own = _PoCell(cells["po_number"], number, cells.get("supplier", _BLANK)) if cells["po_number"].text.strip() else None
        item_code = _value("item_code", cells["item_code"], decimal_comma)
        if item_code.uncertain:
            unread.setdefault("item_code", []).append(number)
        if item_code.missing or ("quantity" in where and not cells["quantity"].text.strip()):
            passed_over += 1                         # a total, a group row, a text line, an item code that is no value
            if not item_code.uncertain:              # an item code that is a date, a time or an error cell is in unread already
                (without_item_code if item_code.missing else without_quantity).append(number)
            above = own or above
            continue
        if own is None and (not fill_down or above is None):
            raise ExportRefused(f"{name}, row {number}: an item code without a PO number", "no_po_number", row=number)
        above = own or above
        po_number = _po_number(name, above.cell, above.row)
        po_lines = lines.setdefault(po_number, [])
        if "line_id" in where:
            line_id = _line_id(name, number, cells["line_id"], ids.setdefault(po_number, set()))
            ids[po_number].add(line_id)
        else:
            line_id = str(len(po_lines) + 1)
        values = {field: _value(field, cells[field], decimal_comma) for field in _LINE_VALUES if field in where}
        for field, value in values.items():
            if value.uncertain:
                unread.setdefault(field, []).append(number)
        po_lines.append(DocLine(line_id=line_id, **values))
        last_line_po = po_number
        if "supplier" in where:
            # The PO's supplier is the first filled supplier cell of its rows: the row its PO number is taken from with fill_down (a
            # text line or a group row, read once), then this order line.
            taken_from = [(above.row, above.supplier)] if above.row != number and above.row not in read_rows else []
            for row_number, cell in [*taken_from, (number, cells["supplier"])]:
                read_rows.add(row_number)
                supplier = _value("supplier", cell, decimal_comma)
                if supplier.uncertain:
                    unread.setdefault("supplier", []).append(row_number)
                if supplier.text is not None:
                    suppliers.setdefault(po_number, supplier)
    documents = [Document(kind="po", po_reference=DocValue(value=po_number, text=po_number), supplier=suppliers.get(po_number, DocValue()),
                          lines=po_lines, source_file=name, reader=f"export:{name}") for po_number, po_lines in lines.items()]
    reading = ExportReading(documents=documents, rows=sum(len(po_lines) for po_lines in lines.values()), passed_over=passed_over,
                            unread={field: unread[field] for field in EXPORT_FIELDS if field in unread}, without_item_code=without_item_code,
                            without_quantity=without_quantity, titles_again=titles_again, last_line_po=last_line_po)
    return reading, table, header


def is_export(path: Path, columns: dict[str, str]) -> bool:
    """Whether the file is an export that the map `columns` describes: a .csv or .xlsx file one of whose rows holds every column
    title of the map, compared as read_export compares them (a CSV file cut at the mark that finds such a row). It tells an export
    from another spreadsheet, a price list for example, and says nothing of the fields the map names. False for an empty map, which
    names no title, for a file of another type and for a file that cannot be read as a table; a file that cannot be opened raises
    its OSError. A CSV file whose row of titles was found and that is refused for a row below it (a filled cell beyond the last
    title, or in a comma file another number of cells) is an export all the same, and so is a workbook refused for its sheets
    (more than one visible sheet with data, or data on a hidden sheet only) when one of those sheets holds the titles, so that
    the reader's refusal is what the scan shows, and not a skip."""
    titles = {field: title.strip() for field, title in columns.items() if title.strip()}
    if not titles:
        return False
    try:
        export_table(Path(path), titles)
    except ExportRefused as refused:                 # a file of another type, one that cannot be read, one without the titles
        wanted = {_folded(title) for title in titles.values()}
        return refused.reason in ("surplus_cells", "cell_count") or (refused.reason in ("several_sheets", "hidden_sheet") and any(
            wanted <= {_folded(cell.text) for cell in row} for table in refused.tables for row in table))
    except ValueError:
        return False
    return True


def is_folder_name(name: str | None) -> bool:
    """Whether a PO number can be the name of one folder of the PO store (aid/po_store.py keeps each PO in a folder of its own) on
    every system the aid runs on, Windows included, whatever system reads it: not empty and at most FOLDER_NAME_MAX characters; no
    path ("/", "\\", "." or ".."), so that no PO number points outside the store; none of the characters : * ? " < > | and no control
    character; no space at either end and no dot at the end; and no device name of Windows (CON, PRN, AUX, NUL, COM1 to COM9 and LPT1
    to LPT9), in any case and also with an extension ("con.250301")."""
    if not name or len(name) > FOLDER_NAME_MAX or name in (".", ".."):
        return False
    if any(char in _NOT_IN_A_NAME or unicodedata.category(char) == "Cc" for char in name):
        return False
    if name[0] == " " or name[-1] in " .":
        return False
    return name.split(".", 1)[0].rstrip(" ").upper() not in _DEVICES


# --- the map and the table ---

def _check_map(columns: dict[str, str]) -> None:
    """Refuse a map that names an unknown field, and a map with fields that lacks po_number or item_code or names a field without a
    column title. An empty map is not checked: its fields come from the file."""
    unknown = sorted(set(columns) - set(EXPORT_FIELDS))
    if unknown:
        raise ExportRefused(f"the export columns name {unknown}, which are not fields of a PO line ({', '.join(EXPORT_FIELDS)})", "map")
    if not columns:
        return
    for field in (*REQUIRED, *columns):
        if not columns.get(field, "").strip():
            raise ExportRefused(f"the export columns name no column for {field}", "map")


def export_table(path: Path, titles: dict[str, str]) -> tuple[list[list[_Cell]], int]:
    """Every row of the file as its cells, blank rows included, and the index in it of the header row: the first row that holds every
    title of `titles` (field -> column title). A CSV file is cut at the first of DELIMITERS under which such a row is found. Refuses a
    file of another type, a file that cannot be read, and a file in no row of which the titles stand: a CSV file for its delimiter or
    its encoding when no mark cuts a row of it into two cells or its text holds NUL characters, any file else for the first title of
    the map it lacks. A CSV file with a row below the header row that the aid does not read is refused with that row (odd_row of
    aid/pricelist.py): a row with a filled cell beyond the last column title (surplus_cells) and, in a file cut at commas, a filled
    row with another number of cells than the header row (cell_count). Such a row is cut otherwise than its titles, and no cell
    of it can be trusted.
    Each cell has its text and its kind ("text" for every cell of a CSV file; see _workbook_cell)."""
    name, suffix = path.name, path.suffix.lower()
    if suffix not in EXPORT_SUFFIXES:
        raise ExportRefused(f"{name} is neither a .csv nor an .xlsx file", "file_type")
    wanted = {_folded(title) for title in titles.values()}
    text = csv_text(path) if suffix == ".csv" else None
    tables = []
    for mark, table in ((mark, csv_rows(text, mark)) for mark in DELIMITERS) if text is not None else [(None, workbook_rows(path))]:
        if table is None:
            continue                                 # the csv module cannot cut the text at this mark
        header = next((index for index, row in enumerate(table) if wanted <= {_folded(cell.text) for cell in row}), None)
        if header is not None:
            if mark is not None and (odd := odd_row(table, header, mark == ",", lambda cell: cell.text)) is not None:
                raise ExportRefused(f"{name}, row {odd[0]}: {odd[1]}", "surplus_cells" if odd[1] == SURPLUS else "cell_count", row=odd[0])
            return table, header
        tables.append(table)
    if text is not None:
        if not tables:
            raise ExportRefused(f"{name} cannot be read as CSV", "unreadable")
        rows = [row for table in tables for row in table]
        if "\x00" in text or (any(cell.text.strip() for row in rows for cell in row) and all(len(row) <= 1 for row in rows)):
            raise ExportRefused(f"{name} uses a delimiter or an encoding the aid does not read: {_NOT_READ}", "delimiter_or_encoding")
    most: set[str] = set()                           # the titles of the map in the row that holds most of them
    for row in (row for table in tables for row in table):
        if len(found := wanted & {_folded(cell.text) for cell in row}) > len(most):
            most = found
    order = sorted(titles, key=lambda field: EXPORT_FIELDS.index(field) if field in EXPORT_FIELDS else len(EXPORT_FIELDS))
    missing = next(titles[field] for field in order if _folded(titles[field]) not in most)
    raise ExportRefused(f"{name} has no column {missing!r}", "no_column", column=missing)


def csv_text(path: Path) -> str:
    """The text of a CSV file: UTF-16 behind a UTF-16 byte order mark, else UTF-8 (a UTF-8 byte order mark left out) or else
    Windows-1252. A file in none of these encodings is refused for its encoding."""
    data = path.read_bytes()
    try:
        if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            return data.decode("utf-16")
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return data.decode("cp1252")
    except UnicodeDecodeError:
        raise ExportRefused(f"{path.name} uses a delimiter or an encoding the aid does not read: {_NOT_READ}",
                            "delimiter_or_encoding") from None


def csv_rows(text: str, mark: str) -> list[list[_Cell]] | None:
    """The rows of a CSV text cut at `mark`, blank rows included; None when the csv module cannot cut it so (a cell longer than it
    takes)."""
    try:
        return [[_Cell(cell) for cell in row] for row in csv.reader(io.StringIO(text, newline=""), delimiter=mark)]
    except csv.Error:
        return None


def workbook_rows(path: Path) -> list[list[_Cell]]:
    """The rows of the one sheet of a workbook that the aid reads, read as the price list reads one (_xlsx_table: the one visible
    sheet that holds data, never a hidden one) but cell by cell, so that each cell keeps its kind. A workbook with more than one
    visible sheet with data is refused (several_sheets), the names of those sheets and their rows kept on the error and out of its
    text, and so is one whose only data stands on a hidden sheet (hidden_sheet). A workbook that openpyxl cannot read, or that holds
    a cell it cannot convert, is refused: the error names the file,
    and the row when it is known, and neither a cell nor the text of the error underneath, which holds the cell's content. openpyxl
    gives a date cell whose serial lies beyond the dates as the error #VALUE!, with a warning that holds the cell and its serial: the
    warning is kept here, so that nothing of the cell reaches the console or a log, and the cell is read as an error cell."""
    name = path.name
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=r"Cell .* is marked as a date", category=UserWarning)
        try:
            return _xlsx_table(path, cell=lambda cell: _workbook_cell(cell, name), values_only=False)
        except ExportRefused:
            raise
        except SeveralSheets as several:
            raise ExportRefused(f"{name} has more than one visible sheet with data", "several_sheets", names=several.names,
                                tables=several.tables) from None
        except OnlyHidden as hidden:
            raise ExportRefused(f"{name} holds data on a hidden sheet only", "hidden_sheet", tables=hidden.tables) from None
        except (ValueError, TypeError, OverflowError, IndexError, KeyError):
            # The price list's ValueError for a workbook openpyxl cannot open, and what openpyxl raises, before it gives the row, for a
            # cell it cannot convert: a number or an ISO date that is a text (ValueError), a shared text that is not there (IndexError).
            raise ExportRefused(f"{name} {_UNREADABLE}", "unreadable") from None


# The names by which these helpers were known before the block "Koppeling met Exact" of the settings tab read a sample with them.
_table, _csv_text, _csv_rows, _workbook_rows = export_table, csv_text, csv_rows, workbook_rows


def _workbook_cell(cell: Any, name: str) -> _Cell:
    """One cell of a workbook from openpyxl's read-only cell: its text as the price list reads it (_cell_text), a percentage as the
    number of percent it shows, and its kind: an error (an error cell, or a text that is the code of one, such as #N/A), a number, a
    date, a time (a time of day or a duration), or else a text, a truth value included."""
    value = cell.value
    if cell.data_type == "e" or (isinstance(value, str) and value.strip().upper() in _ERRORS):
        return _Cell(_cell_text(value), "error")
    if isinstance(value, (time, timedelta)):
        return _Cell(_cell_text(value), "time")
    if isinstance(value, date):                                   # a datetime too, which is a date
        return _Cell(_cell_text(value), "date")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            percentage = "%" in _LITERAL.sub("", cell.number_format or "")
        except (IndexError, KeyError, TypeError):                 # a style or a number format that the workbook does not hold
            raise ExportRefused(f"{name}, row {cell.row}: a cell of the workbook cannot be read", "unreadable", row=cell.row) from None
        return _Cell(_percent(value) if percentage else _cell_text(value), "number")
    return _Cell(_cell_text(value))


def _percent(value: int | float) -> str:
    """The number of percent a number cell shows as a percentage, as a text in normal form: 0.05 is "5", 0.125 is "12.5"."""
    percent = (Decimal(_cell_text(value)) * 100).normalize()
    return format(percent, "f") if percent else "0"


def _folded(text: str) -> str:
    """A column title as it is compared: without the spaces around it and without regard to case."""
    return text.strip().casefold()


def _columns(name: str, row: list[_Cell], number: int, titles: dict[str, str] | None) -> dict[str, int]:
    """The position in the header row `row`, row `number` of the file `name`, of the column of each field that is read: the fields of
    `titles` (field -> column title), or for the empty map (None) every field whose name is a title of the header row."""
    if titles is None:
        found = {_folded(cell.text) for cell in row}
        titles = {field: field for field in EXPORT_FIELDS if field in found}
    where = {}
    for field, title in titles.items():
        places = [place for place, cell in enumerate(row) if _folded(cell.text) == _folded(title)]
        if len(places) > 1:
            raise ExportRefused(f"{name}, row {number}: the column {title!r} stands twice", "double_column", column=title, row=number)
        where[field] = places[0]
    return where


# --- one row ---

def _po_number(name: str, cell: _Cell, row: int) -> str:
    """The PO number in `cell`, the PO cell of row `row` of the file `name`, without the spaces around it. A date, a time or an error
    cell is refused, and so is a PO number without a digit (a word such as a total's) or one that cannot be a folder name
    (is_folder_name), each by the row of the cell."""
    if cell.kind in _NO_IDENTIFIER:
        raise ExportRefused(f"{name}, row {row}: the PO number is a date, a time or an error cell", "po_number_date", row=row)
    po_number = cell.text.strip()
    if not _DIGIT.search(po_number):
        raise ExportRefused(f"{name}, row {row}: the PO number holds no digit", "po_number_no_digit", row=row)
    if not is_folder_name(po_number):
        raise ExportRefused(f"{name}, row {row}: the PO number cannot be the name of a folder", "po_number_no_folder", row=row)
    return po_number


def _line_id(name: str, row: int, cell: _Cell, taken: set[str]) -> str:
    """The line id in `cell`, the line_id cell of the order line on row `row`, without the spaces around it. A blank cell, a date, a
    time or an error cell and a line id of `taken`, the line ids of the earlier lines of the same PO, are refused by the row."""
    line_id = cell.text.strip()
    if not line_id:
        raise ExportRefused(f"{name}, row {row}: an item code without a line id", "no_line_id", row=row)
    if cell.kind in _NO_IDENTIFIER:
        raise ExportRefused(f"{name}, row {row}: the line id is a date, a time or an error cell", "line_id_date", row=row)
    if line_id in taken:
        raise ExportRefused(f"{name}, row {row}: the line id stands on an earlier line of the same PO", "double_line_id", row=row)
    return line_id


def _value(field: str, cell: _Cell, decimal_comma: bool) -> DocValue:
    """The DocValue of one cell of an order line: its text and its normal form (see the module docstring). A blank cell is
    DocValue(); a cell whose text is no value has no value and is uncertain."""
    text, kind = cell
    if not text.strip():
        return DocValue()
    # For the kinds of value below: a text goes through the schema's normaliser; a number or a date cell of a workbook is what it holds
    # when the field takes that kind of cell, and no value otherwise; a time, a duration or an error cell is no value of any field.
    if kind in _NO_VALUE:
        value = None
    elif field in _NUMBERS:
        value = normalize_number(text, decimal_comma=decimal_comma) if kind == "text" else text if kind == "number" else None
    elif field == "price_basis":
        value = normalize_basis(text) if kind == "text" else text if kind == "number" and text.isdigit() and int(text) > 0 else None
    elif field == "delivery_date":
        value = normalize_date(_TIME_OF_DAY.sub("", text.strip()), None) if kind == "text" else text if kind == "date" else None
    elif field == "unit":
        value = normalize_unit(text)
    elif field in ("item_code", "supplier") and kind in _NO_IDENTIFIER:
        value = None                                                 # an item code or a supplier is no date
    else:
        value = text.strip()                                         # the item code, the description and the supplier stay as written
    return DocValue(value=value, text=text, uncertain=value is None)
