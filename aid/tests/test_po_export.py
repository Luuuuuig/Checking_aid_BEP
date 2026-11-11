"""Tests of the export reader (aid/readers/po_export.py): on CSV files and workbooks the tests write themselves, on the made-up exports
of the set (aid/madeup/out) and on the made-up Dutch export of the try-out folder (aid/madeup/tryout/EXPORT_nl.csv). Every PO number,
supplier, item code and price below is made up."""
import csv
import io
import re
import shutil
import warnings
import zipfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from openpyxl import Workbook

from aid.madeup.generate import EXPORT_COLUMNS, TRYOUT_EXPORT_COLUMNS, UNIT_WORDS, generate_tryout, load_specs
from aid.madeup.generate import main as generate_main
from aid.po_store import PoStore
from aid.pricelist import _xlsx_table
from aid.readers.po_export import (EXPORT_FIELDS, EXPORT_SUFFIXES, REFUSALS, ExportReading, ExportRefused, is_export, is_folder_name,
                                   read_export, read_export_table)
from aid.schema import DocLine, DocValue, Document
from aid.tests.conftest import REPO_ROOT

SPECS = {spec.case_id: spec for spec in load_specs(REPO_ROOT / "aid" / "madeup" / "spec")}
TRYOUT = REPO_ROOT / "aid" / "madeup" / "tryout"
# A made-up export with Dutch column titles: the map, the titles in the order of the columns, and three order lines of two POs.
NL = {"po_number": "Inkooporder", "line_id": "Regel", "item_code": "Artikel", "description": "Omschrijving", "quantity": "Aantal",
      "unit": "Eenheid", "unit_price": "Prijs", "price_basis": "Per", "delivery_date": "Leverdatum"}
TITLES = list(NL.values())
ROWS = [["250301", "1", "KM-3101", "Slangklem 16-27 mm", "1000", "st", "12.50", "100", "2026-11-24"],
        ["250301", "2", "KM-3102", "Hydrauliekslang 12 mm", "25", "m", "4.80", "1", "2026-11-24"],
        ["250302", "1", "KM-3201", "O-ring 30 x 3 mm", "200", "st", "0.125", "1", "2026-11-26"]]
THIRD = ["250301", "3", "KM-3103", "Kogelkraan DN20", "4", "st", "38.75", "1", "2026-12-01"]      # a third line of the first PO


def write_csv(path: Path, rows: list[list], delimiter: str = ",", encoding: str = "utf-8") -> Path:
    """A CSV file at `path` with these rows, each cell as its text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding=encoding, newline="") as f:
        csv.writer(f, delimiter=delimiter, lineterminator="\n").writerows(rows)
    return path


def write_semicolons(path: Path, rows: list[list]) -> Path:
    """The rows as a semicolon-separated file in which every row has the cells of the widest one, as a spreadsheet saves a sheet."""
    width = max(len(row) for row in rows)
    return write_csv(path, [[*row, *[""] * (width - len(row))] for row in rows], delimiter=";")


def write_xlsx(path: Path, rows: list[list], iso_dates: bool = False, formats: dict[str, str] | None = None) -> Path:
    """A workbook at `path` whose first sheet holds these rows: a str is a text cell, an int or a float a number cell, a date or a
    datetime a date cell, a time a time cell, a timedelta a duration cell and None an empty cell. With `iso_dates` a date is stored as
    an ISO date typed as one; otherwise as a number with a date format, as a spreadsheet does. `formats` gives a cell a number format
    by its coordinate ({"J2": "0%"})."""
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook(iso_dates=iso_dates)
    for row in rows:
        workbook.active.append(list(row))
    for coordinate, number_format in (formats or {}).items():
        workbook.active[coordinate].number_format = number_format
    workbook.save(path)
    return path


def patched_xlsx(path: Path, rows: list[list], replace: list[tuple[bytes, bytes]]) -> Path:
    """The workbook of write_xlsx with each `old` of `replace` in the XML of its sheet replaced by `new`, as a program that writes the
    XML of a workbook itself may write it, wrongly."""
    write_xlsx(path, rows)
    data = io.BytesIO()
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                for old, new in replace:
                    assert old in content, old
                    content = content.replace(old, new)
            target.writestr(item, content)
    path.write_bytes(data.getvalue())
    return path


# A file name -> how its rows are written: the three kinds of file a test reads the same rows from.
WRITERS = {"comma.csv": write_csv, "semicolon.csv": write_semicolons, "book.xlsx": write_xlsx}


def without(*fields: str) -> dict[str, str]:
    """The map NL without these fields."""
    return {field: title for field, title in NL.items() if field not in fields}


def orders(reading: ExportReading, *fields: str) -> list[tuple]:
    """Each PO of the reading as (PO number, [(line id, the value of each of `fields`) of each line])."""
    return [(doc.po_reference.value, [(line.line_id, *(getattr(line, field).value for field in fields)) for line in doc.lines])
            for doc in reading.documents]


def read(text: str, value: str | None = None) -> DocValue:
    """A cell that was read: its text and its value, which is the text itself unless given."""
    return DocValue(value=text if value is None else value, text=text)


def unread(text: str) -> DocValue:
    """A cell whose text is no value: the text is kept, there is no value and the value is uncertain."""
    return DocValue(text=text, uncertain=True)


def test_the_fields_and_the_file_types_of_an_export():
    assert EXPORT_SUFFIXES == (".xlsx", ".csv")
    assert EXPORT_FIELDS == ("po_number", "line_id", "item_code", "description", "quantity", "unit", "unit_price", "price_basis",
                             "discount_pct", "net_price", "delivery_date", "supplier")


# --- the file: CSV and workbook ---

def test_a_comma_a_semicolon_and_a_windows_1252_file(tmp_path):
    """The same rows as a comma-separated file, as a semicolon-separated file with a byte order mark and as a semicolon-separated file
    in Windows-1252 give the same reading. One description holds a comma and a letter that the two encodings write differently."""
    rows = [TITLES, ROWS[0], [*ROWS[1][:3], "Slang ø 12 mm, 2 m", *ROWS[1][4:]], ROWS[2]]
    comma = write_csv(tmp_path / "comma" / "export.csv", rows)
    marked = write_csv(tmp_path / "marked" / "export.csv", rows, delimiter=";", encoding="utf-8-sig")
    western = write_csv(tmp_path / "western" / "export.csv", rows, delimiter=";", encoding="cp1252")
    assert marked.read_bytes().startswith(b"\xef\xbb\xbf")
    with pytest.raises(UnicodeDecodeError):                                # the test reaches the fallback
        western.read_bytes().decode("utf-8")
    reading = read_export(comma, NL)
    assert read_export(marked, NL) == reading and read_export(western, NL) == reading
    first, second = reading.documents
    assert (first.kind, first.reader, first.source_file, first.po_reference) == ("po", "export:export.csv", "export.csv", read("250301"))
    assert orders(reading, "item_code", "quantity", "unit", "unit_price", "price_basis", "delivery_date") == [
        ("250301", [("1", "KM-3101", "1000", "pc", "12.50", "100", "2026-11-24"), ("2", "KM-3102", "25", "m", "4.80", "1", "2026-11-24")]),
        ("250302", [("1", "KM-3201", "200", "pc", "0.125", "1", "2026-11-26")])]
    assert first.lines[1].description == read("Slang ø 12 mm, 2 m")
    assert first.lines[0].unit == read("st", "pc") and first.lines[0].unit_price.source is None
    assert (reading.rows, reading.passed_over, reading.unread) == (3, 0, {})


@pytest.mark.parametrize("name", WRITERS)
def test_a_text_keeps_its_spaces_and_its_value_has_none(tmp_path, name):
    """The text of a value is the cell as the file writes it; the PO number, the line id and the value of an item code, a description
    or a supplier are the text without the spaces around it."""
    cells = [" 250301 ", " 1 ", " KM-3101 ", "  Slangklem 16-27 mm ", " 1000 ", " st ", " 12.50 ", " 100 ", " 2026-11-24 ", " S21 "]
    [doc] = read_export(WRITERS[name](tmp_path / name, [[*TITLES, "Leverancier"], cells]), NL | {"supplier": "Leverancier"}).documents
    [line] = doc.lines
    assert (doc.po_reference, line.line_id, doc.supplier) == (read("250301"), "1", read(" S21 ", "S21"))
    assert (line.item_code, line.description) == (read(" KM-3101 ", "KM-3101"), read("  Slangklem 16-27 mm ", "Slangklem 16-27 mm"))
    assert (line.quantity, line.unit, line.unit_price) == (read(" 1000 ", "1000"), read(" st ", "pc"), read(" 12.50 ", "12.50"))
    assert (line.price_basis, line.delivery_date) == (read(" 100 ", "100"), read(" 2026-11-24 ", "2026-11-24"))


def csv_text(rows: list[list[str]], mark: str) -> str:
    """The rows as the text of a CSV file cut at `mark`, a row of one cell holding no mark."""
    return "".join(mark.join(row) + "\n" for row in rows)


def test_a_csv_file_is_cut_at_the_mark_under_which_its_header_row_is_found(tmp_path):
    """A CSV file is cut at semicolons, at commas or at tabs: the first of the three under which a row holds every title of the map.
    So a title line above the header row may hold no mark or another one, and a file in UTF-16 with its byte order mark, as some
    programs save CSV, is read as well. Every file below gives the reading of the plain comma-separated file."""
    rows = [TITLES, *ROWS]
    plain = read_export(write_csv(tmp_path / "plain" / "export.csv", rows), NL)
    files = {"a semicolon file under a title line without a semicolon": csv_text([["Openstaande inkooporderregels"], *rows], ";"),
             "a semicolon file under a title line with commas": csv_text([["Overzicht, leverancier S21, week 47"], *rows], ";"),
             "a tab-separated file": csv_text(rows, "\t")}
    for number, (case, text) in enumerate(files.items()):
        path = tmp_path / str(number) / "export.csv"
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8")
        assert read_export(path, NL).documents == plain.documents and is_export(path, NL), case
    for number, (mark, encoding) in enumerate([("\t", "utf-16-le"), (";", "utf-16-le"), (",", "utf-16-be")]):
        path = tmp_path / f"utf16-{number}" / "export.csv"
        path.parent.mkdir()
        path.write_bytes(("\ufeff" + csv_text(rows, mark)).encode(encoding))               # the text behind its byte order mark
        assert path.read_bytes()[:2] == {"utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff"}[encoding]
        assert read_export(path, NL) == plain, (mark, encoding)
    default = tmp_path / "default" / "export.csv"                                          # UTF-16 and the empty map, the default
    default.parent.mkdir()
    default.write_bytes(csv_text([["po_number", "item_code", "quantity"], ["250301", "KM-3101", "1000"]], "\t").encode("utf-16"))
    assert orders(read_export(default, {}), "item_code", "quantity") == [("250301", [("1", "KM-3101", "1000")])]


def test_when_two_marks_find_a_header_row_the_semicolon_comes_first(tmp_path):
    """A title line that holds every title cut at commas stands above a header row cut at semicolons: both marks find a header row,
    and the first of semicolon, comma and tab that finds one is taken. Cut at commas, the file would give no order line at all."""
    path = tmp_path / "export.csv"
    path.write_text(",".join(TITLES) + "\n" + csv_text([TITLES, *ROWS], ";"), encoding="utf-8")
    reading = read_export(path, NL)
    assert orders(reading, "item_code") == [("250301", [("1", "KM-3101"), ("2", "KM-3102")]), ("250302", [("1", "KM-3201")])]
    assert (reading.rows, reading.passed_over) == (3, 0)


def test_a_workbook_is_its_first_sheet(tmp_path):
    """The first sheet is read, also when the workbook was saved with another sheet in view."""
    workbook = Workbook()
    for sheet, row in zip([workbook.active, workbook.create_sheet("Oud")], [ROWS[0], ROWS[2]]):
        sheet.append(TITLES)
        sheet.append(row)
    workbook.active = 1                                                                     # saved with the second sheet in view
    workbook.save(tmp_path / "export.xlsx")
    reading = read_export(tmp_path / "export.xlsx", NL)
    assert orders(reading, "item_code") == [("250301", [("1", "KM-3101")])] and reading.rows == 1
    assert (reading.documents[0].reader, reading.documents[0].source_file) == ("export:export.xlsx", "export.xlsx")


# --- the cells: numbers, texts and dates ---

@pytest.mark.parametrize("decimal_comma", [False, True])
def test_a_number_cell_is_read_as_its_number_under_either_convention(tmp_path, decimal_comma):
    """A number cell is the number it holds. Read as a text, 12.125 would be 12125 under the decimal comma, and a price basis of
    1.234 would be 1234 under any convention; 38.099999999999994 is the 38.1 a spreadsheet shows. A price basis is a whole number
    above zero: 2.5, 1.234, 0 and -1 are no basis."""
    rows = [TITLES,
            [250301, 1, "KM-3101", "Slangklem 16-27 mm", 1000, "st", 12.125, 100, "2026-11-24"],
            [250301, 2, "KM-3102", "Hydrauliekslang 12 mm", 2.5, "m", 12.7 * 3, 1, "2026-11-24"],
            [250301, 3, "KM-3103", "Kogelkraan DN20", 4, "st", 1.234, 2.5, "2026-11-24"],
            [250301, 4, "KM-3104", "Vetnippel M8", 50, "st", 0.4, 1.234, "2026-11-24"],
            [250301, 5, "KM-3105", "Spie 6 x 6", 10, "st", 0.2, 0, "2026-11-24"],
            [250301, 6, "KM-3106", "Borgring 20 mm", 10, "st", 0.3, -1, "2026-11-24"]]
    reading = read_export(write_xlsx(tmp_path / "export.xlsx", rows), NL, decimal_comma=decimal_comma)
    [doc] = reading.documents
    assert doc.po_reference == read("250301")                              # a PO number and a line id kept as numbers
    assert orders(reading, "quantity", "unit_price", "price_basis") == [
        ("250301", [("1", "1000", "12.125", "100"), ("2", "2.5", "38.1", "1"), ("3", "4", "1.234", None), ("4", "50", "0.4", None),
                    ("5", "10", "0.2", None), ("6", "10", "0.3", None)])]
    assert (doc.lines[0].unit_price, doc.lines[0].quantity) == (read("12.125"), read("1000"))
    assert (doc.lines[2].price_basis, doc.lines[3].price_basis) == (unread("2.5"), unread("1.234"))       # no whole number: no basis
    assert (doc.lines[4].price_basis, doc.lines[5].price_basis) == (unread("0"), unread("-1"))            # none above zero: no basis
    assert reading.unread == {"price_basis": [4, 5, 6, 7]}


@pytest.mark.parametrize("name", WRITERS)
def test_a_text_number_follows_the_convention(tmp_path, name):
    """A number written as text, in a CSV file or in a text cell of a workbook, is read with the convention: under the decimal comma
    "1.000" is a thousand and "1,125" one and an eighth, under the decimal point the other way round. A text with two decimals or with
    both marks reads the same under both."""
    rows = [TITLES,
            ["250301", "1", "KM-3101", "Slangklem 16-27 mm", "1.000", "st", "12,50", "100", "2026-11-24"],
            ["250301", "2", "KM-3102", "Hydrauliekslang 12 mm", "1,125", "m", "1.234,50", "1", "2026-11-24"]]
    path = WRITERS[name](tmp_path / name, rows)
    comma, point, default = (read_export(path, NL, **convention) for convention in ({"decimal_comma": True}, {"decimal_comma": False}, {}))
    assert orders(comma, "quantity", "unit_price") == [("250301", [("1", "1000", "12.50"), ("2", "1.125", "1234.50")])]
    assert orders(point, "quantity", "unit_price") == [("250301", [("1", "1.000", "12.50"), ("2", "1125", "1234.50")])]
    assert default == point                                                # the decimal point is the default
    assert comma.documents[0].lines[0].quantity == read("1.000", "1000")   # the text stays as the file writes it
    assert comma.unread == point.unread == {}


def test_a_date_cell_and_a_date_text_with_a_time(tmp_path):
    """A date cell is its day, whatever time of day it holds. A date written as text is read by the schema's normaliser, and a time of
    day behind it is left out; its text stays as written."""
    cells = [datetime(2026, 11, 24), datetime(2026, 11, 24, 14, 30), date(2026, 11, 25), "24-11-2026", "24-11-2026 00:00:00",
             "2026-11-24T14:30:00", "24.11.2026 8:05", " 2026-11-24 00:00:00.000 ", "week 48 2026", "24-11-2026 - 28-11-2026"]
    values = ["2026-11-24", "2026-11-24", "2026-11-25", "2026-11-24", "2026-11-24", "2026-11-24", "2026-11-24", "2026-11-24", "2026-W48",
              "2026-11-24/2026-11-28"]
    start = [250301, 1, "KM-3101", "Slangklem 16-27 mm", 1, "st", 12.5, 1]                                # a row up to its delivery date
    reading = read_export(write_xlsx(tmp_path / "export.xlsx", [TITLES, *([*start, cell] for cell in cells)]), without("line_id"))
    days = [line.delivery_date for line in reading.documents[0].lines]
    assert [day.value for day in days] == values and reading.unread == {}
    assert [day.text for day in days[:3]] == ["2026-11-24", "2026-11-24", "2026-11-25"]                   # a date cell: its day
    assert [day.text for day in days[3:]] == cells[3:]                                                      # a text: as written
    stored = [TITLES, [*start, date(2026, 11, 25)], [*start, datetime(2026, 11, 24, 14, 30)]]             # dates stored as ISO dates
    typed = read_export(write_xlsx(tmp_path / "typed.xlsx", stored, iso_dates=True), without("line_id"))
    assert [line.delivery_date for line in typed.documents[0].lines] == [read("2026-11-25"), read("2026-11-24")]
    written = [TITLES, [*ROWS[0][:8], "24-11-2026 00:00"], [*ROWS[1][:8], "2026-11-24 14:30:00"]]
    texts = read_export(write_csv(tmp_path / "export.csv", written), NL)
    assert [line.delivery_date for line in texts.documents[0].lines] == [read("24-11-2026 00:00", "2026-11-24"),
                                                                           read("2026-11-24 14:30:00", "2026-11-24")]


def test_a_number_cell_formatted_as_a_percentage_is_the_percentage_it_shows(tmp_path):
    """A number cell whose number format shows a percentage holds 0.05 where it shows 5%, and is read as the 5 it shows, in a
    discount column as in any other. A % that the format only writes behind the number, quoted or after a backslash, shows the
    number itself. The price list reads the same workbook as before: the value each cell holds."""
    columns = NL | {"discount_pct": "Korting"}
    cells = [(0.05, "0%", "5"), (0.125, "0.00%", "12.5"), (1, "0%", "100"), (0.0525, "0.0%", "5.25"), (5, '0" %"', "5"),
             (2.5, "0.0\\%", "2.5"), (0.4, "General", "0.4"), (0, "0%", "0")]
    rows = [[*TITLES, "Korting"], *([250301, line, f"KM-31{line:02}", "Slangklem", 10, "st", 1.5, 1, "2026-11-24", discount]
                                    for line, (discount, _, _) in enumerate(cells, 1))]
    formats = {f"J{row}": number_format for row, (_, number_format, _) in enumerate(cells, 2)}
    path = write_xlsx(tmp_path / "export.xlsx", rows, formats=formats)
    reading = read_export(path, columns)
    assert [line.discount_pct for line in reading.documents[0].lines] == [read(shown) for *_, shown in cells]
    assert reading.unread == {}
    assert [row[9] for row in _xlsx_table(path)[1:]] == ["0.05", "0.125", "1", "0.0525", "5", "2.5", "0.4", "0"]


def test_a_time_or_a_duration_cell_is_no_value_in_any_field(tmp_path):
    """A time cell (08:00) and a duration cell (one day) of a workbook hold no value of any field: each keeps its text, has no value
    and is listed as unread, as a date cell is in a number field. Read as a text, the time in the price column would be a price of 8
    and the duration in the quantity column a quantity of 1."""
    columns = NL | {"discount_pct": "Korting", "net_price": "Netto", "supplier": "Leverancier"}
    titles = [*TITLES, "Korting", "Netto", "Leverancier"]
    rows = [titles,
            [250301, 1, "KM-3101", time(8, 0), timedelta(days=1), time(0, 0), time(8, 0), timedelta(days=1), time(8, 30),
             time(8, 0), timedelta(hours=2), time(12, 0)],
            [250301, 2, "KM-3102", "Hydrauliekslang 12 mm", 25, "m", 4.8, 1, datetime(2026, 11, 24), 5, 4.56, "S21"]]
    reading = read_export(write_xlsx(tmp_path / "export.xlsx", rows), columns)
    first, second = reading.documents[0].lines
    fields = ("description", "quantity", "unit", "unit_price", "price_basis", "delivery_date", "discount_pct", "net_price")
    assert [getattr(first, field) for field in fields] == [unread("08:00:00"), unread("1 day, 0:00:00"), unread("00:00:00"),
                                                           unread("08:00:00"), unread("1 day, 0:00:00"), unread("08:30:00"),
                                                           unread("08:00:00"), unread("2:00:00")]
    assert reading.unread == {field: [2] for field in EXPORT_FIELDS if field in (*fields, "supplier")}
    assert reading.documents[0].supplier == unread("12:00:00")              # the first filled supplier cell of the PO, unread
    assert [getattr(second, field).value for field in fields] == ["Hydrauliekslang 12 mm", "25", "m", "4.8", "1", "2026-11-24", "5",
                                                                  "4.56"]


ERRORS = ["#N/A", "#VALUE!", "#REF!", "#DIV/0!", "#NAME?", "#NUM!", "#NULL!"]          # the error cells of a spreadsheet


def test_an_error_cell_is_no_value_in_any_field(tmp_path):
    """An error cell of a workbook (#N/A, #VALUE!, #REF!, #DIV/0!, #NAME?, #NUM!, #NULL!) is no value in any field, whether the
    workbook holds it as an error or as the text of one: unread in a value column, and in the item code column its row is passed
    over and counted and listed as unread, as for a date cell. (In the PO number and line id columns it refuses the file: see the
    refusals.)"""
    columns = NL | {"discount_pct": "Korting", "net_price": "Netto", "supplier": "Leverancier"}
    fields = ("description", "quantity", "unit", "unit_price", "price_basis", "delivery_date", "discount_pct", "net_price")
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([*TITLES, "Korting", "Netto", "Leverancier"])
    sheet.append(["250301", "1", "KM-3101", *ERRORS, "#N/A", "#REF!"])                       # row 2: errors in every value column
    sheet.append(["250301", "2", "KM-3102", *ERRORS, "#N/A", "#REF!"])                       # row 3: the same as texts
    sheet.append(["250301", "3", "#N/A", "Kogelkraan DN20", "4", "st", "38.75", "1", "2026-12-01", "", "", "S21"])     # row 4
    sheet.append(["250301", "4", " #ref! ", "Vetnippel M8", "50", "st", "0.40", "1", "2026-12-01", "", "", "S21"])     # row 5
    for cell in sheet[3]:
        cell.data_type = "s"                                                                  # row 3 holds the codes as text
    assert [cell.data_type for cell in sheet[2]][3:] == ["e"] * 9 and sheet["C5"].data_type == "s"
    workbook.save(tmp_path / "export.xlsx")
    reading = read_export(tmp_path / "export.xlsx", columns)
    [doc] = reading.documents
    assert [line.line_id for line in doc.lines] == ["1", "2"] and doc.supplier == unread("#REF!")
    for line in doc.lines:
        assert [getattr(line, field) for field in fields] == [unread(error) for error in ERRORS] + [unread("#N/A")]
    assert (reading.rows, reading.passed_over) == (2, 2)
    assert reading.unread == {"item_code": [4, 5], **{field: [2, 3] for field in fields}, "supplier": [2, 3]}


def test_a_date_serial_openpyxl_cannot_convert_is_an_error_cell_and_warns_no_one(tmp_path):
    """openpyxl gives a date cell whose serial lies beyond the dates as the error #VALUE!, with a warning that holds the cell and its
    serial. The reader keeps that warning to itself, so that nothing of the cell reaches the console or a log, and reads the cell
    as an error cell: unread in the date column, and in the item code column its row is passed over."""
    later = [250301, 2, datetime(2026, 11, 25), "Hydrauliekslang 12 mm", 25, "m", 4.8, 1, datetime(2026, 11, 24)]
    path = patched_xlsx(tmp_path / "export.xlsx", [TITLES, GOOD_ROW, later], [
        (b'<c r="I2" s="1" t="n"><v>46350</v>', b'<c r="I2" s="1" t="n"><v>1e20</v>'),
        (b'<c r="C3" s="1" t="n"><v>46351</v>', b'<c r="C3" s="1" t="n"><v>9e99</v>')])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        reading = read_export(path, NL)
    assert caught == []
    [line] = reading.documents[0].lines
    assert line.delivery_date == unread("#VALUE!") and (reading.rows, reading.passed_over) == (1, 1)
    assert reading.unread == {"item_code": [3], "delivery_date": [2]}


def test_identifier_columns_read_a_whole_number_as_its_digits_and_a_date_as_no_value(tmp_path):
    """In the columns of the PO number, the line id, the item code and the supplier a number cell that holds a whole number is that
    number's digits (250301.0 is 250301), whatever format shows it: a number cell holds no zeros in front, so these columns should be
    text cells. A date, a time or a duration cell is no identifier: an item code so kept is listed as unread and its row passed over
    and counted, a supplier so kept is listed as unread, and a PO number or a line id so kept refuses the file (see the refusals)."""
    columns = NL | {"supplier": "Leverancier"}
    rows = [[*TITLES, "Leverancier"],
            [250301.0, 1.0, 3101, "Slangklem 16-27 mm", 1000, "st", 12.5, 100, "2026-11-24", 21.0],
            [250301, 2, datetime(2026, 10, 12), "Hydrauliekslang 12 mm", 25, "m", 4.8, 1, "2026-11-24", "S21"],
            [250301, 3, time(12, 10), "Kogelkraan DN20", 4, "st", 38.75, 1, "2026-12-01", "S21"],
            [250301, 4, "KM-3104", "Vetnippel M8", 50, "st", 0.4, 1, "2026-12-01", date(2026, 11, 2)],
            [250302, 1, "0123", "O-ring 30 x 3 mm", 200, "st", 0.125, 1, "2026-11-26", timedelta(days=21)]]
    formats = {"A2": "0000000", "B2": "0.00", "C2": "000000", "C3": "dd-mm"}                     # what a spreadsheet shows: 0250301, 1.00
    reading = read_export(write_xlsx(tmp_path / "export.xlsx", rows, formats=formats), columns)
    assert orders(reading, "item_code") == [("250301", [("1", "3101"), ("4", "KM-3104")]), ("250302", [("1", "0123")])]
    first, second = reading.documents
    assert (first.po_reference, first.supplier, second.supplier) == (read("250301"), read("21"), unread("21 days, 0:00:00"))
    assert (reading.rows, reading.passed_over) == (3, 2)
    assert reading.unread == {"item_code": [3, 4], "supplier": [5, 6]}


# --- the header row and the rows ---

@pytest.mark.parametrize("name", WRITERS)
def test_title_rows_above_the_header_are_passed_over(tmp_path, name):
    """The header row is the first row that holds every column title of the map, whatever the case of a title and the spaces around
    it. The rows above it, one of which holds two of the titles, are passed over and not counted, and every row keeps the number it
    has in the file: the cell that is no value stands on row 7."""
    header = [" INKOOPORDER", "regel ", "ARTIKEL", "omschrijving", " Aantal ", "eenheid", "PRIJS", "per", "LeverDatum"]
    rows = [["Openstaande inkooporderregels"], [], ["Artikel", "Aantal"], header, ROWS[0], ROWS[1], [*ROWS[2][:4], "tien", *ROWS[2][5:]]]
    reading = read_export(WRITERS[name](tmp_path / name, rows), NL)
    assert orders(reading, "item_code", "quantity") == [("250301", [("1", "KM-3101", "1000"), ("2", "KM-3102", "25")]),
                                                        ("250302", [("1", "KM-3201", None)])]
    assert (reading.rows, reading.passed_over, reading.unread) == (3, 0, {"quantity": [7]})


@pytest.mark.parametrize("name", WRITERS)
def test_a_total_row_is_passed_over_and_counted(tmp_path, name):
    """A filled row without an item code is no order line: a total with a text in the PO column, a group row, a row with nothing but an
    amount in a column outside the map, a row whose item code cell holds only spaces. Each is passed over and counted; a blank row is
    not, nor a row of spaces."""
    rows = [[*TITLES, "Bedrag"],
            [*ROWS[0], "125.00"],
            [*ROWS[1], "120.00"],
            ["Totaal 250301", "", "", "", "", "", "", "", "", "245.00"],
            ["", "", "", "", "", "", "", "", "", ""],
            ["", "", "", "Leverancier S22", "", "", "", "", "", ""],
            ["  ", "   ", "   ", "  ", " ", "  ", " ", " ", "  ", "   "],
            [*ROWS[2], "25.00"],
            ["250302", "2", "   ", "Vrachtkosten", "1", "st", "7.50", "1", "", "7.50"],
            ["", "", "", "", "", "", "", "", "", "277.50"]]
    reading = read_export(WRITERS[name](tmp_path / name, rows), NL)
    assert orders(reading, "item_code") == [("250301", [("1", "KM-3101"), ("2", "KM-3102")]), ("250302", [("1", "KM-3201")])]
    assert (reading.rows, reading.passed_over, reading.unread) == (3, 4, {})


@pytest.mark.parametrize("name", WRITERS)
def test_a_row_with_an_item_code_and_no_quantity_is_passed_over_and_counted(tmp_path, name):
    """A total or a group row may print its word in the item code column. When the map names a quantity column, a row whose item code
    cell is filled and whose quantity cell is blank is passed over and counted, whether or not it has a PO number. A quantity that is
    a text but no number keeps its row an order line, with the quantity unread. A map without a quantity column reads such a row as
    an order line."""
    rows = [TITLES,
            ROWS[0],
            ROWS[1],
            ["250301", "", "Totaal", "", "", "", "245.00", "", ""],                                 # row 4: a subtotal of its PO
            ["250302", "1", "KM-3201", "O-ring 30 x 3 mm", "tien", "st", "0.125", "1", "2026-11-26"],     # row 5: an unread quantity
            ["", "", "Totaal", "", " ", "", "270.00", "", ""]]                                      # row 6: the grand total
    path = WRITERS[name](tmp_path / name, rows)
    for fill_down in (False, True):
        reading = read_export(path, NL, fill_down=fill_down)
        assert orders(reading, "item_code", "quantity") == [("250301", [("1", "KM-3101", "1000"), ("2", "KM-3102", "25")]),
                                                            ("250302", [("1", "KM-3201", None)])]
        assert (reading.rows, reading.passed_over, reading.unread) == (3, 2, {"quantity": [5]})
    numbered = [row[:1] + row[2:] for row in rows]                                                 # no line id column either
    narrow = read_export(WRITERS[name](tmp_path / "narrow" / name, numbered), without("line_id", "quantity"), fill_down=True)
    assert orders(narrow, "item_code") == [("250301", [("1", "KM-3101"), ("2", "KM-3102"), ("3", "Totaal")]),
                                           ("250302", [("1", "KM-3201"), ("2", "Totaal")])]


def test_a_row_shorter_than_the_header_row_has_blank_cells(tmp_path):
    """A row that ends before the last column, as a CSV file may write it, lacks the values of the columns it does not reach; one that
    ends before the item code is a row without an item code, and one that ends before the quantity a row without a quantity."""
    path = tmp_path / "export.csv"
    path.write_text(",".join(TITLES) + "\n250301,1,KM-3101,Slangklem 16-27 mm,1000\n250301,2,KM-3102,,25\n250301,3,KM-3103\nTotaal\n",
                    encoding="utf-8")
    reading = read_export(path, NL)
    first, second = reading.documents[0].lines
    assert (first.quantity, first.unit, first.delivery_date) == (read("1000"), DocValue(), DocValue())
    assert (second.item_code, second.description, second.quantity, second.unit) == (read("KM-3102"), DocValue(), read("25"), DocValue())
    assert (reading.rows, reading.passed_over, reading.unread) == (2, 2, {})


def test_fill_down_and_the_refusal_without_it(tmp_path):
    """With fill_down a row with an item code and no PO number takes the PO number of the nearest row above it whose PO cell is filled,
    an order line or a row that is passed over. A PO number holds a digit, so a total's word in the PO column is no PO number: a total
    that no line takes refuses nothing, but a line below it that would take its word refuses the file by the row of the word (an
    overview prints the number of the next PO on its first line, so only a file that leaves it out does this). Without fill_down the
    first row without a PO number refuses the whole file, and so does, with it, a first order line without a PO number."""
    rows = [TITLES,
            ROWS[0],                                                                               # row 2: PO 250301
            ["", *ROWS[1][1:]],                                                                    # row 3: no PO number: 250301
            ["Totaal", "", "", "", "", "", "", "", ""],                                            # row 4: passed over
            ROWS[2],                                                                               # row 5: PO 250302
            ["  ", "2", "KM-3202", "Tandwielpomp 11 cc", "2", "st", "1234.50", "1", "2026-11-26"]]        # row 6: spaces are no PO number
    path = write_csv(tmp_path / "export.csv", rows)
    filled = read_export(path, NL, fill_down=True)
    assert orders(filled, "item_code") == [("250301", [("1", "KM-3101"), ("2", "KM-3102")]),
                                           ("250302", [("1", "KM-3201"), ("2", "KM-3202")])]
    assert (filled.rows, filled.passed_over) == (4, 1)
    taken = write_csv(tmp_path / "taken" / "export.csv", [*rows[:4], ["", *THIRD[1:]], *rows[4:]])      # row 5 would take the word
    with pytest.raises(ExportRefused, match=r"^export\.csv, row 4: the PO number holds no digit$") as refused:
        read_export(taken, NL, fill_down=True)
    assert (refused.value.reason, refused.value.row, refused.value.column) == ("po_number_no_digit", 4, None)
    with pytest.raises(ExportRefused, match=r"^export\.csv, row 3: an item code without a PO number$") as refused:
        read_export(path, NL)
    assert (refused.value.reason, refused.value.row, refused.value.column) == ("no_po_number", 3, None)
    headless = write_csv(tmp_path / "headless" / "export.csv", [TITLES, rows[2], rows[1]])       # the first order line has no PO number
    with pytest.raises(ExportRefused, match=r"^export\.csv, row 2: an item code without a PO number$"):
        read_export(headless, NL, fill_down=True)


# An overview that prints the PO number once for each PO: (1) on its first row, which for the second PO is a text line without an
# item code; (2) on a group row of its own above the lines of each PO. Without line ids: the map is without("line_id").
TEXT_LINE = [TITLES,
             ROWS[0],                                                                              # row 2: PO 250301
             ["", *ROWS[1][1:]],                                                                   # row 3
             ["250302", "", "", "Leveren aan magazijn 2", "", "", "", "", ""],                     # row 4: a text line of PO 250302
             ["", *ROWS[2][1:]],                                                                   # row 5
             ["", "2", "KM-3202", "Tandwielpomp 11 cc", "2", "st", "1234.50", "1", "2026-11-26"]]  # row 6
GROUPED = [TITLES,
           ["250301", "", "", "", "", "", "", "", ""],                                             # row 2: the group of PO 250301
           ["", *ROWS[0][1:]], ["", *ROWS[1][1:]],                                                 # rows 3 and 4
           ["250302", "", "", "S22", "", "", "", "", ""],                                          # row 5: the group of PO 250302
           ["", *ROWS[2][1:]]]                                                                     # row 6


@pytest.mark.parametrize("name", WRITERS)
def test_fill_down_takes_the_po_number_of_a_text_line_or_a_group_row(tmp_path, name):
    """A PO whose number stands on a row without an item code, a text line or a group row, keeps its own lines: with fill_down they
    take the number of that row, and do not join the PO above it. Without fill_down the first line without a PO number refuses the
    file."""
    expected = [("250301", [("1", "KM-3101"), ("2", "KM-3102")]), ("250302", [("1", "KM-3201"), ("2", "KM-3202")])]
    text_line = WRITERS[name](tmp_path / "text" / name, TEXT_LINE)
    reading = read_export(text_line, without("line_id"), fill_down=True)
    assert orders(reading, "item_code") == expected and (reading.rows, reading.passed_over) == (4, 1)
    grouped = read_export(WRITERS[name](tmp_path / "grouped" / name, GROUPED), without("line_id"), fill_down=True)
    assert orders(grouped, "item_code") == [*expected[:1], ("250302", [("1", "KM-3201")])] and (grouped.rows, grouped.passed_over) == (3, 2)
    with pytest.raises(ExportRefused, match=rf"^{re.escape(name)}, row 3: an item code without a PO number$"):
        read_export(text_line, without("line_id"))


def test_with_fill_down_the_row_a_po_number_is_taken_from_gives_the_po_its_supplier(tmp_path):
    """The supplier of a PO is the first filled supplier cell of its rows, counting the row its PO number is taken from with
    fill_down: a text line that carries the PO number and the supplier (the layout of finding 1 of the review) gives both to the
    lines below it. A row passed over whose number no line takes gives no supplier, and a supplier cell that is no value on the row
    a number is taken from is listed as unread by that row and is the PO's supplier, without a value."""
    columns = without("line_id") | {"supplier": "Leverancier"}
    rows = [[*TITLES, "Leverancier"],
            [*ROWS[0], "S21"],                                                                    # row 2: PO 250301 of S21
            ["", *ROWS[1][1:], ""],                                                               # row 3
            ["250302", "", "", "Leveren aan magazijn 2", "", "", "", "", "", "S22"],             # row 4: the text line of PO 250302
            ["", *ROWS[2][1:], "S98"],                                                            # row 5: a supplier below the text line
            ["", "2", "KM-3202", "Tandwielpomp 11 cc", "2", "st", "1234.50", "1", "2026-11-26", "S99"],     # row 6: and another one
            ["250303", "", "", "Leveren aan magazijn 3", "", "", "", "", "", "S23"],             # row 7: a text line no line takes
            ["250304", "1", "KM-3401", "Vetnippel M8", "50", "st", "0.40", "1", "2026-11-30", ""]]     # row 8: no supplier
    reading = read_export(write_csv(tmp_path / "export.csv", rows), columns, fill_down=True)
    assert [(doc.po_reference.value, doc.supplier) for doc in reading.documents] == [
        ("250301", read("S21")), ("250302", read("S22")), ("250304", DocValue())]
    assert orders(reading, "item_code") == [("250301", [("1", "KM-3101"), ("2", "KM-3102")]),
                                           ("250302", [("1", "KM-3201"), ("2", "KM-3202")]), ("250304", [("1", "KM-3401")])]
    assert (reading.rows, reading.passed_over, reading.unread) == (5, 2, {})
    dated = [row[:] for row in rows[:6]]
    dated[3][9] = date(2026, 11, 2)                                                               # the supplier of the text line is a date
    book = read_export(write_xlsx(tmp_path / "export.xlsx", dated), columns, fill_down=True)
    assert [doc.supplier for doc in book.documents] == [read("S21"), unread("2026-11-02")] and book.unread == {"supplier": [4]}


def test_a_po_number_that_a_line_takes_from_a_row_above_is_checked_on_that_row(tmp_path):
    """The PO number a line takes from a row above it is held to the rules of a PO number on that row: a group row whose PO cell
    cannot be the name of a folder, holds no digit, or holds a date or an error, refuses the file by its own row once a line below
    takes it. A row passed over whose PO cell no line takes refuses nothing."""
    for number, (cell, message, reason) in enumerate([("PO: 250302", "the PO number cannot be the name of a folder", "po_number_no_folder"),
                                                      ("Diversen", "the PO number holds no digit", "po_number_no_digit")]):
        group = [row[:] for row in GROUPED]
        group[4][0] = cell                                                                     # row 5: the group row of the second PO
        with pytest.raises(ExportRefused, match=rf"^export\.csv, row 5: {message}$") as refused:
            read_export(write_csv(tmp_path / str(number) / "export.csv", group), without("line_id"), fill_down=True)
        assert (refused.value.reason, refused.value.row) == (reason, 5)
    for number, cell in enumerate([datetime(2026, 3, 2), "#N/A"]):                             # a date cell and an error cell
        group = [row[:] for row in GROUPED]
        group[4][0] = cell
        with pytest.raises(ExportRefused, match=r"^export\.xlsx, row 5: the PO number is a date, a time or an error cell$") as refused:
            read_export(write_xlsx(tmp_path / str(number) / "export.xlsx", group), without("line_id"), fill_down=True)
        assert (refused.value.reason, refused.value.row) == ("po_number_date", 5)
    total = [TITLES, ROWS[0], ["Totaal: 2 regels", "", "", "", "", "", "", "", ""], ROWS[2]]  # a total no line takes the number of
    reading = read_export(write_csv(tmp_path / "total.csv", total), NL, fill_down=True)
    assert [doc.po_reference.value for doc in reading.documents] == ["250301", "250302"] and reading.passed_over == 1


@pytest.mark.parametrize("name", WRITERS)
def test_a_header_row_repeated_lower_in_the_file_is_passed_over_and_counted(tmp_path, name):
    """A report saved page by page, or two exports pasted into one file, repeat the row of column titles lower down. Such a row holds
    every title of the map again: it is passed over and counted, and gives the rows below it no PO number, so with fill_down a line
    below it keeps the PO above it."""
    rows = [TITLES, ROWS[0], ["", *ROWS[1][1:]], [title.upper() for title in TITLES], ["", *THIRD[1:]], ROWS[2]]
    filled = read_export(WRITERS[name](tmp_path / "filled" / name, rows), NL, fill_down=True)
    assert orders(filled, "item_code") == [("250301", [("1", "KM-3101"), ("2", "KM-3102"), ("3", "KM-3103")]),
                                           ("250302", [("1", "KM-3201")])]
    assert (filled.rows, filled.passed_over, filled.unread) == (4, 1, {})
    pasted = read_export(WRITERS[name](tmp_path / "pasted" / name, [TITLES, ROWS[0], TITLES, ROWS[2]]), NL)
    assert orders(pasted, "item_code") == [("250301", [("1", "KM-3101")]), ("250302", [("1", "KM-3201")])]
    assert (pasted.rows, pasted.passed_over, pasted.unread) == (2, 1, {})
    made_up = [EXPORT_COLUMNS, ["250201", "1", "KM-2101", "a", "25", "st", "0.95", "1", "2026-11-10"], EXPORT_COLUMNS]
    assert read_export(WRITERS[name](tmp_path / "empty" / name, made_up), {}).passed_over == 1           # the empty map


def test_without_a_line_id_column_the_lines_are_numbered(tmp_path):
    """A map without line_id numbers the lines of each PO 1, 2, 3 in the order of the file, also when the rows of two POs alternate;
    a map with it keeps the line ids of the file."""
    rows = [TITLES, ["250301", "10", *ROWS[0][2:]], ["250301", "20", *ROWS[1][2:]], ["250302", "10", *ROWS[2][2:]],
            ["250301", "30", *THIRD[2:]]]
    path = write_csv(tmp_path / "export.csv", rows)
    numbered, kept = read_export(path, without("line_id")), read_export(path, NL)
    assert orders(numbered, "item_code") == [("250301", [("1", "KM-3101"), ("2", "KM-3102"), ("3", "KM-3103")]),
                                             ("250302", [("1", "KM-3201")])]
    assert orders(kept, "item_code") == [("250301", [("10", "KM-3101"), ("20", "KM-3102"), ("30", "KM-3103")]),
                                         ("250302", [("10", "KM-3201")])]


def test_a_supplier_a_discount_and_a_net_price_column(tmp_path):
    """The supplier of a PO is the first filled supplier cell of its rows, and a PO without one has no supplier. The discount and the
    net price of a line are read as numbers with the convention."""
    columns = NL | {"supplier": "Leverancier", "discount_pct": "Korting", "net_price": "Netto"}
    rows = [[*TITLES, "Leverancier", "Korting", "Netto"],
            [*ROWS[0], "", "5 %", "11,875"],
            [*ROWS[1], " S21 ", "", ""],
            [*THIRD, "S99", "2,5", "37,78"],
            [*ROWS[2], "", "", ""]]
    reading = read_export(write_semicolons(tmp_path / "export.csv", rows), columns, decimal_comma=True)
    first, second = reading.documents
    assert first.supplier == read(" S21 ", "S21") and second.supplier == DocValue()
    assert orders(reading, "discount_pct", "net_price") == [("250301", [("1", "5", "11.875"), ("2", None, None), ("3", "2.5", "37.78")]),
                                                           ("250302", [("1", None, None)])]
    assert first.lines[0].discount_pct == read("5 %", "5") and first.lines[1].discount_pct == DocValue()
    assert first.lines[1].net_price == DocValue() and reading.unread == {}


def test_no_unit_and_no_price_basis_is_written_into_a_line_that_states_none(tmp_path):
    """A line whose unit or price basis cell is blank, or whose file or map has no such column, has no unit and no price basis: none
    is filled in, where the price-list import takes a blank basis as one."""
    rows = [TITLES, [*ROWS[0][:5], "", ROWS[0][6], "", ROWS[0][8]], ROWS[1]]
    path = write_csv(tmp_path / "export.csv", rows)
    blank, stated = read_export(path, NL).documents[0].lines
    assert (blank.unit, blank.price_basis) == (DocValue(), DocValue())
    assert (stated.unit, stated.price_basis) == (read("m"), read("1"))
    kept = [[cell for place, cell in enumerate(row) if place not in (5, 7)] for row in [TITLES, *ROWS]]       # no unit and no basis column
    narrow = write_csv(tmp_path / "narrow.csv", kept)
    for file in (path, narrow):                               # a map without the two fields, on a file with and without their columns
        lines = [line for doc in read_export(file, without("unit", "price_basis")).documents for line in doc.lines]
        assert lines and all((line.unit, line.price_basis) == (DocValue(), DocValue()) for line in lines)


@pytest.mark.parametrize("case_id", ["CART01", "CART02"])
def test_an_empty_map_reads_the_made_up_exports_as_before(madeup_out, case_id):
    """The empty map means every field whose name is a column title of the file. The made-up exports name their nine columns after
    the fields, so they give the Documents they gave when the batch named each of those columns in its map: each value with its text
    as the file writes it and its normal form, which the spec holds."""
    path, spec = madeup_out / f"{case_id}_export.csv", SPECS[case_id]
    expected = Document(kind="po", po_reference=read(spec.po.po_number), source_file=path.name, reader=f"export:{path.name}", lines=[
        DocLine(line_id=line.line_id, item_code=read(line.item_code), description=read(line.description), quantity=read(line.quantity),
                unit=read(UNIT_WORDS["nl"][line.unit], line.unit), unit_price=read(line.unit_price), price_basis=read(line.price_basis),
                delivery_date=read(line.delivery_date)) for line in spec.po.versions[-1].lines])
    reading = read_export(path, {})
    assert reading == ExportReading(documents=[expected], rows=3, passed_over=0, unread={},
                                    last_line_po=spec.po.po_number)                # the PO its last line went to (ruling M15)
    assert read_export(path, {field: field for field in EXPORT_COLUMNS}) == reading


def test_an_empty_map_reads_every_field_the_file_names(tmp_path):
    """With the empty map a column named after any of the twelve fields is read, a column with another title is not, and a file
    without the columns po_number and item_code is refused."""
    rows = [["supplier", "PO_NUMBER", "item_code", "quantity", "net_price", "Opmerking"],
            ["S21", "250301", "KM-3101", "1000", "0.125", "spoed"],
            ["", "250301", "KM-3102", "25", "4.80", ""]]
    reading = read_export(write_csv(tmp_path / "export.csv", rows), {})
    [doc] = reading.documents
    assert doc.supplier.value == "S21" and orders(reading, "item_code", "quantity", "net_price", "unit_price", "description") == [
        ("250301", [("1", "KM-3101", "1000", "0.125", None, None), ("2", "KM-3102", "25", "4.80", None, None)])]
    for lacking, named in ((1, "po_number"), (2, "item_code")):
        path = write_csv(tmp_path / named / "export.csv", [[cell for place, cell in enumerate(row) if place != lacking] for row in rows])
        with pytest.raises(ExportRefused, match=rf"^export\.csv has no column '{named}'$") as refused:
            read_export(path, {})
        assert (refused.value.reason, refused.value.column, refused.value.row) == ("no_column", named, None)


def test_unread_cells_are_counted_by_field(tmp_path):
    """A cell whose text is no value keeps its text, has no value, is uncertain and is listed under its field with the row it stands
    on; the fields come in the order of EXPORT_FIELDS. A blank cell is missing and listed nowhere."""
    rows = [[*TITLES, "Korting"],
            ["250301", "1", "KM-3101", "Slangklem 16-27 mm", "tien", "st", "12.50", "100", "volgt", "geen"],
            ["250301", "2", "KM-3102", "Hydrauliekslang 12 mm", "25", "", "n.v.t.", "per 2,5", "", ""],
            ["250301", "3", "KM-3103", "Kogelkraan DN20", "4", ".", "38.75", "1", "week 48", "5"]]
    reading = read_export(write_csv(tmp_path / "export.csv", rows), NL | {"discount_pct": "Korting"})
    assert reading.unread == {"quantity": [2], "unit": [4], "unit_price": [3], "price_basis": [3], "discount_pct": [2],
                              "delivery_date": [2, 4]}
    assert list(reading.unread) == [field for field in EXPORT_FIELDS if field in reading.unread]
    first, second, third = reading.documents[0].lines
    assert (first.quantity, first.delivery_date, first.discount_pct) == (unread("tien"), unread("volgt"), unread("geen"))
    assert (second.unit_price, second.price_basis) == (unread("n.v.t."), unread("per 2,5"))
    assert (second.quantity, second.unit, second.delivery_date, second.discount_pct) == (read("25"), DocValue(), DocValue(), DocValue())
    assert (third.unit, third.delivery_date) == (unread("."), unread("week 48"))               # a week without a year names no week
    assert third.quantity == read("4") and reading.rows == 3
    # In a workbook a date cell is no number and no price basis, a truth value is no number, a number cell is no date and a time cell
    # no date either; a price basis cell below one is none. A date stored as an ISO date, which openpyxl gives as a day without a
    # time, is no number either.
    cells = [TITLES, [250301, 1, "KM-3101", "Slangklem 16-27 mm", datetime(2026, 11, 24), "st", True, datetime(2026, 11, 24), 46350],
             [250301, 2, "KM-3102", "Hydrauliekslang 12 mm", 25, "m", 4.8, -1, time(8, 30)]]
    book = read_export(write_xlsx(tmp_path / "export.xlsx", cells), NL)
    line, other = book.documents[0].lines
    assert book.unread == {"quantity": [2], "unit_price": [2], "price_basis": [2, 3], "delivery_date": [2, 3]}
    assert (line.quantity, line.unit_price) == (unread("2026-11-24"), unread("True"))
    assert (line.price_basis, line.delivery_date) == (unread("2026-11-24"), unread("46350"))
    assert (other.price_basis, other.delivery_date) == (unread("-1"), unread("08:30:00"))
    typed = [TITLES, [250301, 1, "KM-3101", "Slangklem 16-27 mm", date(2026, 11, 25), "st", date(2026, 11, 25), 1, date(2026, 11, 25)]]
    iso = read_export(write_xlsx(tmp_path / "typed.xlsx", typed, iso_dates=True), NL)
    assert [(field, getattr(iso.documents[0].lines[0], field)) for field in ("quantity", "unit_price", "delivery_date")] == [
        ("quantity", unread("2026-11-25")), ("unit_price", unread("2026-11-25")), ("delivery_date", read("2026-11-25"))]
    assert iso.unread == {"quantity": [2], "unit_price": [2]}


# --- the refusals ---

def changed(change: dict[tuple[int, int], str] | None = None, header: list[str] | None = None) -> list[list[str]]:
    """The header row (TITLES unless given) and ROWS, with the cell at (row of the file, column from 0) given another text."""
    rows = [list(header or TITLES), *(list(row) for row in ROWS)]
    for (row, column), text in (change or {}).items():
        rows[row - 1][column] = text
    return rows


NO_FOLDER = "the PO number cannot be the name of a folder"
NO_IDENTIFIER = "the %s is a date, a time or an error cell"
# What is refused -> the file name, its rows, the map, the options, the text of the error, and its reason, column and row.
REFUSED = {
    "a map without po_number": ("export.csv", changed(), without("po_number"), {},
                                "the export columns name no column for po_number", ("map", None, None)),
    "a map without item_code": ("export.csv", changed(), without("item_code"), {},
                                "the export columns name no column for item_code", ("map", None, None)),
    "a map that names a field and no column": ("export.csv", changed(), NL | {"unit": " "}, {},
                                               "the export columns name no column for unit", ("map", None, None)),
    "a map that names unknown fields": ("export.csv", changed(), NL | {"price": "Prijs", "amount": "Bedrag"}, {},
                                        "the export columns name ['amount', 'price'], which are not fields of a PO line "
                                        f"({', '.join(EXPORT_FIELDS)})", ("map", None, None)),
    "a mapped column that no row holds": ("export.csv", changed(), NL | {"unit": "Maat"}, {},
                                          "export.csv has no column 'Maat'", ("no_column", "Maat", None)),
    "a file without rows": ("export.csv", [], NL, {}, "export.csv has no column 'Inkooporder'", ("no_column", "Inkooporder", None)),
    "an item code without a PO number": ("export.csv", changed({(3, 0): ""}), NL, {},
                                         "export.csv, row 3: an item code without a PO number", ("no_po_number", None, 3)),
    "a first order line without a PO number": ("export.csv", changed({(2, 0): " "}), NL, {"fill_down": True},
                                               "export.csv, row 2: an item code without a PO number", ("no_po_number", None, 2)),
    "a mapped line id that is blank": ("export.csv", changed({(3, 1): "  "}), NL, {},
                                       "export.csv, row 3: an item code without a line id", ("no_line_id", None, 3)),
    "a line id twice in one PO": ("export.csv", changed({(3, 1): "1"}), NL, {},
                                  "export.csv, row 3: the line id stands on an earlier line of the same PO", ("double_line_id", None, 3)),
    "a line id twice in one PO, its lines apart": ("export.csv", [*changed(), ["250301", "2", *THIRD[2:]]], NL, {},
                                                   "export.csv, row 5: the line id stands on an earlier line of the same PO",
                                                   ("double_line_id", None, 5)),
    "a PO number kept as a date": ("export.xlsx", changed({(3, 0): datetime(2026, 3, 1)}), NL, {},
                                   f"export.xlsx, row 3: {NO_IDENTIFIER % 'PO number'}", ("po_number_date", None, 3)),
    "a PO number kept as a duration": ("export.xlsx", changed({(2, 0): timedelta(days=3)}), NL, {"fill_down": True},
                                       f"export.xlsx, row 2: {NO_IDENTIFIER % 'PO number'}", ("po_number_date", None, 2)),
    "a PO number kept as an error": ("export.xlsx", changed({(4, 0): "#N/A"}), NL, {},
                                     f"export.xlsx, row 4: {NO_IDENTIFIER % 'PO number'}", ("po_number_date", None, 4)),
    "a PO number without a digit": ("export.csv", changed({(4, 0): "Totaal"}), NL, {},
                                    "export.csv, row 4: the PO number holds no digit", ("po_number_no_digit", None, 4)),
    "a line id kept as a date": ("export.xlsx", changed({(4, 1): datetime(2026, 1, 1)}), NL, {},
                                 f"export.xlsx, row 4: {NO_IDENTIFIER % 'line id'}", ("line_id_date", None, 4)),
    "a line id kept as a time": ("export.xlsx", changed({(2, 1): time(1, 0)}), NL, {},
                                 f"export.xlsx, row 2: {NO_IDENTIFIER % 'line id'}", ("line_id_date", None, 2)),
    "a line id kept as an error": ("export.xlsx", changed({(3, 1): "#REF!"}), NL, {},
                                   f"export.xlsx, row 3: {NO_IDENTIFIER % 'line id'}", ("line_id_date", None, 3)),
    "a PO number that leads out of the store": ("export.csv", changed({(4, 0): "../250302"}), NL, {},
                                                f"export.csv, row 4: {NO_FOLDER}", ("po_number_no_folder", None, 4)),
    "a PO number with a path separator": ("export.xlsx", changed({(2, 0): "2503/01"}), NL, {},
                                          f"export.xlsx, row 2: {NO_FOLDER}", ("po_number_no_folder", None, 2)),
    "a PO number that is the folder above, without a digit": ("export.csv", changed({(4, 0): ".."}), NL, {},
                                                              "export.csv, row 4: the PO number holds no digit",
                                                              ("po_number_no_digit", None, 4)),
    "a PO number with a colon": ("export.csv", changed({(4, 0): "250302:1"}), NL, {},
                                 f"export.csv, row 4: {NO_FOLDER}", ("po_number_no_folder", None, 4)),
    "a PO number of 300 characters": ("export.csv", changed({(4, 0): "250302" * 50}), NL, {},
                                      f"export.csv, row 4: {NO_FOLDER}", ("po_number_no_folder", None, 4)),
    "a PO number that is a device of Windows": ("export.xlsx", changed({(3, 0): "con.250301"}), NL, {},
                                                f"export.xlsx, row 3: {NO_FOLDER}", ("po_number_no_folder", None, 3)),
    "a title that stands twice": ("export.csv", changed(header=[*TITLES[:5], "Aantal", *TITLES[6:]]), without("unit"), {},
                                  "export.csv, row 1: the column 'Aantal' stands twice", ("double_column", "Aantal", 1)),
    "an old workbook": ("export.xls", changed(), NL, {}, "export.xls is neither a .csv nor an .xlsx file", ("file_type", None, None)),
    "a text file": ("export.txt", changed(), NL, {}, "export.txt is neither a .csv nor an .xlsx file", ("file_type", None, None)),
}


def texts(rows: list[list]) -> list[str]:
    """The texts of the cells of these rows that a test looks for in an error, those of a few letters left out."""
    return [str(cell) for cells in rows for cell in cells if cell is not None and len(str(cell).strip()) > 2]


@pytest.mark.parametrize("case", REFUSED)
def test_each_refusal_names_the_column_or_the_row_and_no_content(tmp_path, case):
    """Each refusal is a ValueError whose text names the file with the column or the row, or the field of the map, and holds the
    text of no cell of an order row; the column and the row are also attributes of the error."""
    name, rows, columns, options, message, (reason, column, row) = REFUSED[case]
    path = (write_xlsx if name.endswith(".xlsx") else write_csv)(tmp_path / name, rows)
    with pytest.raises(ExportRefused) as refused:
        read_export(path, columns, **options)
    error = refused.value
    assert isinstance(error, ValueError) and str(error) == message
    assert (error.reason, error.column, error.row) == (reason, column, row)
    assert not [text for text in texts(rows[1:]) if text in str(error)]


GOOD_ROW = [250301, 1, "KM-3101", "Slangklem 16-27 mm", 1000, "st", 12.5, 100, datetime(2026, 11, 24)]   # as cells of a workbook
BROKEN = "export.xlsx cannot be read as an .xlsx workbook: the file is damaged, or a cell holds what its type does not allow"
NOT_READ = ("export.csv uses a delimiter or an encoding the aid does not read: the aid reads CSV cut at semicolons, commas or tabs, in "
            "UTF-8, Windows-1252 or UTF-16 with its byte order mark")


def broken_xlsx(*replace: tuple[bytes, bytes]):
    """A writer of the workbook of TITLES and GOOD_ROW whose sheet has these replacements (see patched_xlsx)."""
    return lambda path: patched_xlsx(path, [TITLES, GOOD_ROW], list(replace))


def written(content: bytes):
    """A writer of a file that holds these bytes."""
    return lambda path: path.write_bytes(content)


# A file the aid cannot read -> its name, how it is written, the text of the error, its reason and row, and texts the error must not
# hold: the content of a cell, and the text of the error that openpyxl, the decoder or the csv module raised.
REFUSED_FILES = {
    "a file that is no workbook": ("export.xlsx", written(b"no workbook"), BROKEN, ("unreadable", None), ["zip", "File is not"]),
    "a number cell that holds a text": ("export.xlsx", broken_xlsx((b"<v>12.5</v>", b"<v>12,50</v>")), BROKEN, ("unreadable", None),
                                        ["12,50", "invalid literal"]),
    "a date cell that holds a text": ("export.xlsx", broken_xlsx((b'<c r="I2" s="1" t="n"><v>46350</v>',
                                                                   b'<c r="I2" t="d"><v>24-11-2026</v>')),
                                      BROKEN, ("unreadable", None), ["24-11-2026", "datetime"]),
    "a cell whose shared text is missing": ("export.xlsx", broken_xlsx((b'<c r="C2" t="inlineStr"><is><t>KM-3101</t></is></c>',
                                                                         b'<c r="C2" t="s"><v>99</v></c>')),
                                            BROKEN, ("unreadable", None), ["index", "99"]),
    "a number cell whose style is missing": ("export.xlsx", broken_xlsx((b'<c r="G2" t="n">', b'<c r="G2" s="77" t="n">')),
                                             "export.xlsx, row 2: a cell of the workbook cannot be read", ("unreadable", 2),
                                             ["index", "12.5"]),
    "a CSV file with a cell longer than the csv module takes": ("export.csv", written(csv_text([TITLES, ["x" * 200_000]], ";").encode()),
                                                                "export.csv cannot be read as CSV", ("unreadable", None),
                                                                ["field", "131072"]),
    "a CSV file cut at a mark the aid does not read": ("export.csv", written(csv_text([TITLES, *ROWS], "|").encode()), NOT_READ,
                                                       ("delimiter_or_encoding", None), []),
    "a CSV file in UTF-16 without its byte order mark": ("export.csv", written(csv_text([TITLES, *ROWS], ";").encode("utf-16-le")),
                                                         NOT_READ, ("delimiter_or_encoding", None), []),
    "a CSV file in no encoding the aid reads": ("export.csv", written(csv_text([TITLES, *ROWS], ";").encode() + b"\x81\x8d\x8f\x90\x9d\n"),
                                                NOT_READ, ("delimiter_or_encoding", None), ["decode", "0x81"]),
}


@pytest.mark.parametrize("case", REFUSED_FILES)
def test_a_file_the_aid_cannot_read_is_refused_without_the_text_of_the_error(tmp_path, case):
    """A workbook that openpyxl cannot read, or with a cell it cannot convert, and a CSV file cut at another mark or in another
    encoding, are refused as a whole. The text names the file, and the row where it is known, and neither the content of a cell
    nor the text of the error underneath, which a traceback does not show either. The store files nothing of such a file."""
    name, write, message, (reason, row), hidden = REFUSED_FILES[case]
    path = tmp_path / name
    write(path)
    with pytest.raises(ExportRefused) as refused:
        read_export(path, NL)
    error = refused.value
    assert (str(error), error.reason, error.column, error.row) == (message, reason, None, row)
    assert not [text for text in [*hidden, *texts(ROWS)] if text in str(error)]
    assert error.__cause__ is None and (error.__context__ is None or error.__suppress_context__)    # no error underneath is shown
    assert not is_export(path, NL)
    with pytest.raises(ExportRefused, match=f"^{re.escape(message)}$"):
        PoStore(tmp_path / "store").from_export(path, NL)
    assert not (tmp_path / "store").exists()


def test_the_refusals_above_cover_every_reason():
    reasons = {reason for *_, (reason, _, _) in REFUSED.values()} | {reason for *_, (reason, _), _ in REFUSED_FILES.values()}
    assert reasons == set(REFUSALS)


def test_a_csv_file_without_the_titles_of_the_map_names_the_first_one_it_lacks(tmp_path):
    """A CSV file that some mark the aid reads cuts into cells, and whose rows lack a title of the map, is refused for the first title
    of the map, in the order of the fields, that the row holding most of them lacks: the delimiter is not to blame there. A file that
    cannot be opened raises its OSError."""
    rows = [[title for title in TITLES if title not in ("Artikel", "Per")], *ROWS]
    with pytest.raises(ExportRefused, match=r"^export\.csv has no column 'Artikel'$") as refused:
        read_export(write_csv(tmp_path / "export.csv", rows, delimiter="\t"), NL)
    assert (refused.value.reason, refused.value.column) == ("no_column", "Artikel")
    with pytest.raises(FileNotFoundError):
        read_export(tmp_path / "missing.csv", NL)
    with pytest.raises(FileNotFoundError):
        read_export(tmp_path / "missing.xlsx", NL)


@pytest.mark.parametrize("name", ["250301", "PO-250301", "0250301", "PO 25 03", "250301.2", "Totaal", "CONSOLE", "COM10", "x.con",
                                  "Inkooporder ä", "P" * 100])
def test_a_po_number_that_is_a_folder_name_on_every_system(name):
    assert is_folder_name(name)


@pytest.mark.parametrize("name", [None, "", ".", "..", "P" * 101, "2503/01", "2503\\01", "250301:1", "PO*1", "PO?1", 'PO"1', "PO<1", "PO>1",
                                  "PO|1", "PO\t1", "PO\n1", "PO\x001", "PO\x7f1", " 250301", "250301 ", "250301.", "CON", "con", "Prn",
                                  "aux", "NUL", "nul.250301", "COM1", "com9.txt", "LPT1", "Lpt9", "CON .txt"])
def test_a_po_number_that_cannot_be_a_folder_name_on_windows(name):
    """A folder of Windows cannot have these names, so a PO number with one of them is refused on every system: a path, one of the
    characters : * ? " < > |, a control character, a space at either end or a dot at the end, more than 100 characters, and the
    devices CON, PRN, AUX, NUL, COM1 to COM9 and LPT1 to LPT9, in any case and with an extension."""
    assert not is_folder_name(name)


# --- telling an export from another spreadsheet ---

@pytest.mark.parametrize("name", WRITERS)
def test_is_export_tells_an_export_from_a_price_list(tmp_path, name):
    """A .csv or .xlsx file is an export when one of its rows holds every column title of the map, compared as the reader compares
    them. A price list holds some of the titles and never all, and an empty map tells nothing."""
    rows = [["Openstaande inkooporderregels"], [title.upper() + " " for title in TITLES], *ROWS]
    prices = [["Artikel", "Omschrijving", "Prijs", "Per", "Eenheid", "Geldig vanaf"],
              ["KM-3101", "Slangklem 16-27 mm", "12.50", "100", "st", "2026-11-01"]]
    export, price_list = WRITERS[name](tmp_path / "export" / name, rows), WRITERS[name](tmp_path / "list" / name, prices)
    assert is_export(export, NL) and not is_export(price_list, NL)
    assert not is_export(export, {}) and not is_export(export, {"po_number": " "})
    assert is_export(price_list, {"item_code": "Artikel", "unit_price": "prijs"})          # whatever the map says of the fields


def test_is_export_is_false_for_another_file_type_and_for_a_file_that_is_no_table(tmp_path, madeup_out):
    export = write_csv(tmp_path / "export.csv", [TITLES, *ROWS])
    upper = write_csv(tmp_path / "upper" / "EXPORT.CSV", [TITLES, *ROWS])                  # a folder of its own: one name on Windows
    shutil.copy(export, tmp_path / "export.txt")
    (tmp_path / "damaged.xlsx").write_bytes(b"no workbook")
    assert is_export(export, NL) and is_export(upper, NL)                                  # the suffix in any case
    assert not is_export(tmp_path / "export.txt", NL) and not is_export(tmp_path / "damaged.xlsx", NL)
    assert not is_export(madeup_out / "MU01_conf.pdf", NL) and not is_export(madeup_out / "CART02_export.csv", NL)
    assert is_export(madeup_out / "CART02_export.csv", {field: field for field in EXPORT_COLUMNS})
    assert is_export(TRYOUT / "EXPORT_nl.csv", TRYOUT_EXPORT_COLUMNS)
    with pytest.raises(FileNotFoundError):
        is_export(tmp_path / "missing.csv", NL)


# --- the made-up Dutch export of the try-out folder ---

def test_the_made_up_dutch_export_is_read():
    """EXPORT_nl.csv has a title row and a blank row above its header, Dutch column titles, semicolons, decimal commas, the PO number
    and the supplier on the first row of each order only, no line numbers, and a total row at its end. Read with its map, the decimal
    comma and fill_down, it gives two POs with their lines numbered."""
    path = TRYOUT / "EXPORT_nl.csv"
    reading = read_export(path, TRYOUT_EXPORT_COLUMNS, decimal_comma=True, fill_down=True)
    assert [(doc.po_reference.value, doc.supplier.value, doc.reader) for doc in reading.documents] == [
        ("250301", "S21", "export:EXPORT_nl.csv"), ("250302", "S22", "export:EXPORT_nl.csv")]
    assert orders(reading, "item_code", "description", "quantity", "unit", "unit_price", "price_basis", "delivery_date") == [
        ("250301", [("1", "KM-3101", "Slangklem 16-27 mm", "1000", "pc", "12.50", "100", "2026-11-24"),
                    ("2", "KM-3102", "Hydrauliekslang 12 mm", "25", "m", "4.80", "1", "2026-11-24"),
                    ("3", "KM-3103", "Kogelkraan DN20", "4", "pc", "38.75", "1", "2026-12-01")]),
        ("250302", [("1", "KM-3201", "O-ring 30 x 3 mm", "200", "pc", "0.125", "1", "2026-11-26"),
                    ("2", "KM-3202", "Tandwielpomp 11 cc", "2", "pc", "1234.50", "1", "2026-11-26")])]
    assert (reading.rows, reading.passed_over, reading.unread) == (5, 1, {})
    assert reading.documents[0].lines[0].quantity == read("1.000", "1000")
    with pytest.raises(ExportRefused, match=r"^EXPORT_nl\.csv, row 5: an item code without a PO number$"):
        read_export(path, TRYOUT_EXPORT_COLUMNS, decimal_comma=True)                       # the PO number stands on the first row only
    point = read_export(path, TRYOUT_EXPORT_COLUMNS, fill_down=True)
    assert point.documents[0].lines[0].quantity.value == "1.000"                           # with the decimal point, one piece
    lines = path.read_text(encoding="utf-8").splitlines()
    assert "verzonnen" in lines[0] and not lines[1].strip(";") and lines[2].split(";")[:3] == ["Inkooporder", "Leverancier", "Artikel"]
    assert lines[-1].startswith("Totaal;") and re.fullmatch(r";;KM-3102;.*;4,80;1;24-11-2026;120,00", lines[4])


def test_the_try_out_export_is_written_by_the_generator(tmp_path, capsys):
    """generate_tryout writes the file the repository holds (its text: a checkout may change the line ends). The command line writes
    the try-out folder with --tryout and then nothing else: no document of the set and no references."""
    held = (TRYOUT / "EXPORT_nl.csv").read_text(encoding="utf-8")
    written = generate_tryout(tmp_path / "direct")
    assert tmp_path / "direct" / "EXPORT_nl.csv" in written
    assert (tmp_path / "direct" / "EXPORT_nl.csv").read_text(encoding="utf-8") == held
    assert b"\r" not in (tmp_path / "direct" / "EXPORT_nl.csv").read_bytes()                # the generator writes plain line feeds
    out, references = tmp_path / "out", tmp_path / "references.csv"
    assert generate_main(["--tryout", str(tmp_path / "command"), "--out", str(out), "--references", str(references)]) == 0
    assert (tmp_path / "command" / "EXPORT_nl.csv").read_text(encoding="utf-8") == held
    assert not out.exists() and not references.exists()
    assert f"try-out files in {tmp_path / 'command'}" in capsys.readouterr().out


# --- what the trial read on the settings page needs (Task 39's review round, rulings M1 and M11) ---

def test_the_rows_passed_over_are_listed_by_their_kind(tmp_path):
    """The reader passes over the same rows as before, and ExportReading lists them by their kind, so that the trial read on the
    settings page can say why: the rows whose item code cell is blank, the rows with an item code and a blank quantity, the rows whose
    item code is a date, a time or an error cell (in unread["item_code"], as before), and the rows that repeat the row of column titles.
    passed_over counts them all, as before."""
    rows = [TITLES, ROWS[0],
            ["250301", "2", "KM-3102", "Hydrauliekslang 12 mm", None, "m", "4.80", "1", "2026-11-24"],    # row 3: no quantity
            [None, None, None, "Subtotaal"],                                                              # row 4: no item code
            TITLES,                                                                                       # row 5: the titles again
            ["250302", "1", datetime(2026, 11, 3), "Pakking", "5", "st", "1.00", "1", "2026-11-26"],     # row 6: an item code that is a date
            ROWS[2], ["Totaal"]]                                                                          # row 8: no item code
    reading = read_export(write_xlsx(tmp_path / "kinds.xlsx", rows), NL)
    assert (reading.without_item_code, reading.without_quantity, reading.titles_again, reading.unread) == ([4, 8], [3], [5], {"item_code": [6]})
    assert (reading.rows, reading.passed_over) == (2, 5)
    plain = read_export(write_csv(tmp_path / "plain.csv", [TITLES, *ROWS]), NL)                  # nothing passed over: empty lists
    assert (plain.without_item_code, plain.without_quantity, plain.titles_again, plain.passed_over) == ([], [], [], 0)


def test_the_reader_records_the_po_its_last_line_went_to(tmp_path):
    """The reader records the PO number of the last row it read as an order line (Task 39's second review round, ruling M15), so that
    the trial read on the settings page shows that PO: with fill_down a total row at the end of the file joins the PO of the row above
    it, which in an export sorted by delivery date is not the PO the file names last. What it reads does not change."""
    from aid.tests.helpers import SORTED_BY_DATE, SORTED_BY_DATE_COLUMNS, SORTED_BY_DATE_LONG
    reading = read_export(SORTED_BY_DATE, SORTED_BY_DATE_COLUMNS, decimal_comma=True, fill_down=True)
    assert [doc.po_reference.value for doc in reading.documents] == ["250301", "250302", "250303"]       # in the order first named
    assert [line.item_code.value for line in reading.documents[1].lines] == ["KM-3201", "KM-3202", "Totaal"]
    assert reading.last_line_po == "250302" and (reading.rows, reading.passed_over) == (5, 0)
    long = read_export(SORTED_BY_DATE_LONG, SORTED_BY_DATE_COLUMNS, decimal_comma=True, fill_down=True)
    assert [doc.po_reference.value for doc in long.documents] == ["250301", "250304", "250306", "250302", "250303", "250305"]
    assert long.last_line_po == "250306" and long.documents[2].lines[-1].item_code.value == "Totaal" and long.rows == 31
    with pytest.raises(ExportRefused) as refused:                                 # without the tick the total row refuses the file
        read_export(SORTED_BY_DATE, SORTED_BY_DATE_COLUMNS, decimal_comma=True)
    assert (refused.value.reason, refused.value.row) == ("no_po_number", 6)
    assert read_export(write_csv(tmp_path / "plain.csv", [TITLES, *ROWS]), NL).last_line_po == "250302"
    assert read_export(write_csv(tmp_path / "back.csv", [TITLES, ROWS[2], ROWS[0]]), NL).last_line_po == "250301"
    assert read_export(write_csv(tmp_path / "none.csv", [TITLES, ["Totaal"]]), NL).last_line_po is None   # no order line


def test_read_export_table_reads_the_file_once_and_gives_its_table(tmp_path, monkeypatch):
    """read_export_table is read_export with the table it read and the index of its header row, for a caller that needs the cells
    themselves: the block of the settings tab tells a number cell from a text with them and reads the sample once."""
    import aid.readers.po_export as po_export
    path = write_semicolons(tmp_path / "export.csv", [["Openstaande inkooporderregels"], TITLES, *ROWS])
    reads, real = [], po_export.csv_text
    monkeypatch.setattr(po_export, "csv_text", lambda file: (reads.append(Path(file).name), real(file))[1])
    reading, table, header = read_export_table(path, NL)
    assert reads == ["export.csv"] and header == 1 and [cell.text for cell in table[header]] == TITLES
    assert [cell.kind for cell in table[header + 1]] == ["text"] * len(TITLES) and reading == read_export(path, NL)
