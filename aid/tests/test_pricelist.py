"""Tests of the price-list import (aid/pricelist.py) on made-up lists: the item codes are the KM numbers of the made-up set and the
supplier is S1. No list, price or supplier comes from a company, and no test calls the network."""
import csv
import os
import re
import struct
import subprocess
import sys
import zipfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from aid.execution.ops import WriteOp
from aid.execution.writer import PreparedWriter, WriteLog, WriterError, undo_case
from aid.pricelist import ImportResult, import_price_list, main

REPO_ROOT = Path(__file__).resolve().parents[2]
HEADER = ("item_code", "price", "basis", "unit", "min_quantity", "valid_from", "valid_to")


def write_xlsx(path: Path, rows: list[tuple], header: tuple = HEADER, blank_rows: int = 0, iso_dates: bool = False) -> Path:
    """A workbook at `path` whose first sheet holds the header row and then these rows, and then `blank_rows` rows with no value in
    them but a number format, which a spreadsheet saves and a reader gets back as blank rows. With `iso_dates` a date is stored as
    an ISO date typed as one (t="d"); otherwise as a number with a date format, as a spreadsheet does."""
    workbook = Workbook(iso_dates=iso_dates)
    sheet = workbook.active
    sheet.append(list(header))
    for row in rows:
        sheet.append(list(row))
    for number in range(len(rows) + 2, len(rows) + 2 + blank_rows):
        for column in range(1, len(header) + 1):
            sheet.cell(row=number, column=column).number_format = "0.00"
    workbook.save(path)
    return path


def test_import_updates_changed_rows(tmp_path):
    xlsx = write_xlsx(tmp_path / "list.xlsx", [("KM-1001", "12.70", "1", "pc", "1", "2026-11-01", "2027-01-31"), ("KM-1002", "8.00", "1", "pc", "1", "2026-11-01", "2027-01-31"), ("KM-1003", "5.00", "1", "pc", "1", "2026-01-01", "2026-06-30")])
    r = import_price_list(xlsx, "S1", {"KM-1001": "12.50", "KM-1002": "8.00"}, PreparedWriter(tmp_path), datetime(2026, 11, 18))
    assert [e.op.article_code for e in r.updated] == ["KM-1001"] and ("KM-1002", "unchanged") in r.skipped and ("KM-1003", "expired") in r.skipped and r.expired


# The tests below cover what the rulings add to the brief's test above.

NOW = datetime(2026, 11, 18, 9, 30)
CURRENT = {"KM-1001": "12.50", "KM-1002": "8.00", "KM-1003": "5.00", "KM-1004": "3.20"}
NORMAL = {"item_code": "KM-1001", "price": "12.70", "basis": "1", "unit": "pc", "min_quantity": "1", "valid_from": "2026-11-01",
          "valid_to": "2027-01-31"}                                        # the normalised row of item() below


def item(code="KM-1001", price="12.70", basis="1", unit="pc", min_quantity="1", valid_from="2026-11-01", valid_to="2027-01-31") -> tuple:
    """One row of a made-up list in the order of HEADER. By default it is valid on NOW and raises KM-1001 from 12.50 to 12.70."""
    return (code, price, basis, unit, min_quantity, valid_from, valid_to)


def write_csv(path: Path, rows: list[tuple], header: tuple = HEADER, delimiter: str = ",", encoding: str = "utf-8") -> Path:
    """A CSV file at `path` with the header row and then these rows."""
    with open(path, "w", encoding=encoding, newline="") as f:
        csv.writer(f, delimiter=delimiter).writerows([header, *rows])
    return path


def rewrite_sheet(path: Path, pattern: bytes, replacement: bytes) -> Path:
    """A copy of the workbook at `path` in which the XML of the first sheet has `pattern` (a regular expression) replaced."""
    copy = path.with_name("edited_" + path.name)
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(copy, "w", zipfile.ZIP_DEFLATED) as target:
        for member in source.infolist():
            data = source.read(member.filename)
            target.writestr(member, re.sub(pattern, replacement, data) if member.filename == "xl/worksheets/sheet1.xml" else data)
    return copy


def flipped(path: Path, member: str, fraction: float) -> Path:
    """A copy of the workbook at `path` in which one byte of the compressed data of `member` is inverted, `fraction` of the way in."""
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(member)
    raw = bytearray(path.read_bytes())
    name_length, extra_length = struct.unpack("<HH", raw[info.header_offset + 26:info.header_offset + 30])      # the local file header
    raw[info.header_offset + 30 + name_length + extra_length + int(info.compress_size * fraction)] ^= 0xFF
    copy = path.with_name("flipped_" + path.name)
    copy.write_bytes(raw)
    return copy


def marked_encrypted(path: Path, member: str) -> Path:
    """A copy of the workbook at `path` whose zip directory marks `member` as encrypted (bit 0 of its general-purpose flag). Only the
    directory changes, which is written when the file is closed, so the data is as it was and no password is given."""
    copy = path.with_name("encrypted_" + path.name)
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(copy, "w", zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        target.getinfo(member).flag_bits |= 0x1
    return copy


def open_files() -> set[Path]:
    """The files this process has open (from /proc/self/fd, so on Linux only)."""
    found = set()
    for descriptor in Path("/proc/self/fd").iterdir():
        try:
            found.add(Path(os.readlink(descriptor)).resolve())
        except OSError:                                                   # a descriptor that closed since the listing
            pass
    return found


def prepared_rows(folder: Path) -> list[dict[str, str]]:
    """The rows of the folder's prepared_changes.csv."""
    with open(folder / "prepared_changes.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def run(tmp_path: Path, rows: list[tuple], current: dict[str, str] = CURRENT, **more) -> ImportResult:
    """Import a made-up CSV list with these rows against `current` on NOW, the changes prepared in tmp_path."""
    return import_price_list(write_csv(tmp_path / "list.csv", rows), "S1", current, PreparedWriter(tmp_path), NOW, **more)


class FlakyWriter:
    """Prepares every change but raises the exception given for an article (`errors` maps article codes to exceptions)."""
    name = "flaky"

    def __init__(self, folder: Path, errors: dict[str, BaseException]):
        self.inner, self.errors = PreparedWriter(folder), errors

    def apply(self, op, case_id, rule_id, evidence, now):
        if op.article_code in self.errors:
            raise self.errors[op.article_code]
        return self.inner.apply(op, case_id, rule_id, evidence, now)


# --- the file: CSV and XLSX ---

def test_a_csv_with_semicolons_and_decimal_commas_is_read(tmp_path):
    path = write_csv(tmp_path / "lijst.csv", [item("KM-1001", "12,70"), item("KM-1002", "1.234,50")], delimiter=";")
    r = import_price_list(path, "S1", {"KM-1001": "12.50", "KM-1002": "1200.00"}, PreparedWriter(tmp_path), NOW)
    assert [(e.op.article_code, e.op.old_value, e.op.new_value) for e in r.updated] == [("KM-1001", "12.50", "12.70"),
                                                                                         ("KM-1002", "1200.00", "1234.50")]


def test_a_comma_separated_csv_may_quote_a_decimal_comma(tmp_path):
    r = run(tmp_path, [item(price="12,70")])
    assert '"12,70"' in (tmp_path / "list.csv").read_text(encoding="utf-8") and [e.op.new_value for e in r.updated] == ["12.70"]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252"])
def test_a_csv_in_utf8_or_windows_1252_is_read(tmp_path, encoding):
    path = write_csv(tmp_path / "list.csv", [item(price="€ 12,70", unit="Stück")], delimiter=";", encoding=encoding)
    if encoding == "cp1252":
        with pytest.raises(UnicodeDecodeError):                            # the test reaches the fallback
            path.read_bytes().decode("utf-8")
    (entry,) = import_price_list(path, "S1", CURRENT, PreparedWriter(tmp_path), NOW).updated
    assert entry.op.new_value == "12.70" and entry.evidence["row"]["unit"] == "pc"


def test_blank_rows_are_ignored_and_a_short_row_has_blank_cells(tmp_path):
    """In a file cut at semicolons. In a file cut at commas a short row refuses the file since ruling Z30 of the price list's third
    review round: a row cut by a decimal comma could not be told from it."""
    path = tmp_path / "list.csv"
    path.write_text("item_code;price\nKM-1001;12.70\n\n;\nKM-1002\n", encoding="utf-8")
    r = import_price_list(path, "S1", CURRENT, PreparedWriter(tmp_path), NOW)
    assert [e.op.article_code for e in r.updated] == ["KM-1001"] and r.skipped == [("KM-1002", "unreadable price")]
    commas = tmp_path / "commas.csv"
    commas.write_text("item_code,price\nKM-1001,12.70\n\n,\nKM-1002\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"^commas\.csv, row 5: another number of cells than the row of column titles$"):
        import_price_list(commas, "S1", CURRENT, PreparedWriter(tmp_path / "commas"), NOW)


def test_a_workbook_is_read_from_the_one_sheet_that_is_seen(tmp_path, capsys):
    """Ruling Z17 of the second review round: the command reads a workbook as every reader of the aid does, from the one visible
    sheet that holds data. A hidden sheet is never read, wherever it stands; a workbook with more than one visible sheet with data
    is refused, the command naming those sheets, and nothing is prepared."""
    workbook = Workbook()
    for sheet, (code, price) in zip([workbook.active, workbook.create_sheet("Now")], [("KM-1002", "9.00"), ("KM-1001", "12.70")]):
        sheet.append(list(HEADER))
        sheet.append(list(item(code, price)))
    workbook.active.sheet_state = "hidden"                     # the first sheet, the old prices, is hidden
    workbook.active = 1
    workbook.save(tmp_path / "list.xlsx")
    r = import_price_list(tmp_path / "list.xlsx", "S1", CURRENT, PreparedWriter(tmp_path / "one"), NOW)
    assert [e.op.article_code for e in r.updated] == ["KM-1001"] and r.skipped == []
    workbook["Sheet"].sheet_state = "visible"
    workbook.save(tmp_path / "two.xlsx")
    with pytest.raises(ValueError):
        import_price_list(tmp_path / "two.xlsx", "S1", CURRENT, PreparedWriter(tmp_path / "two"), NOW)
    assert main(command(tmp_path, tmp_path / "two.xlsx"), now=NOW) == 1
    out, err = capsys.readouterr()
    assert out == "" and err == ("cannot read: two.xlsx has more than one visible sheet with data (Sheet, Now); the aid reads one sheet: save "
                                 "the sheet you mean as a file of its own\n")
    assert not (tmp_path / "two").exists() and not (tmp_path / "prepared").exists()


def test_a_workbook_with_dates_and_numbers_in_its_cells_is_read(tmp_path):
    xlsx = write_xlsx(tmp_path / "list.xlsx", [("KM-1001", 12.7, 1, "pc", 1.5, date(2026, 11, 1), datetime(2027, 1, 31)),
                                               ("KM-1002", 8, 1, "pc", 1, None, None)])
    r = import_price_list(xlsx, "S1", CURRENT, PreparedWriter(tmp_path), NOW)
    assert [e.evidence["row"] for e in r.updated] == [{"item_code": "KM-1001", "price": "12.7", "basis": "1", "unit": "pc",
                                                       "min_quantity": "1.5", "valid_from": "2026-11-01", "valid_to": "2027-01-31"}]
    assert r.skipped == [("KM-1002", "unchanged")]                         # the number 8 is the price 8.00


def test_the_float_noise_of_a_spreadsheet_is_not_a_price_change(tmp_path):
    noisy = [item("KM-1001", 12.7 * 3), item("KM-1002", 4.35 * 100)]                    # 38.099999999999994 and 434.99999999999994
    xlsx = write_xlsx(tmp_path / "list.xlsx", noisy)
    r = import_price_list(xlsx, "S1", {"KM-1001": "38.10", "KM-1002": "435.00"}, PreparedWriter(tmp_path), NOW)
    assert r.updated == [] and r.skipped == [("KM-1001", "unchanged"), ("KM-1002", "unchanged")]


def test_a_workbook_with_dates_typed_as_iso_dates_is_read(tmp_path):
    xlsx = write_xlsx(tmp_path / "list.xlsx", [item(valid_from=date(2026, 11, 1), valid_to=date(2027, 1, 31))], iso_dates=True)
    with zipfile.ZipFile(xlsx) as archive:
        assert b't="d"' in archive.read("xl/worksheets/sheet1.xml")           # the dates are stored as ISO dates and come back as dates
    r = import_price_list(xlsx, "S1", CURRENT, PreparedWriter(tmp_path), NOW)
    assert [e.evidence["row"] for e in r.updated] == [NORMAL]


@pytest.mark.parametrize("column, cell", [("valid_from", time(8, 30)), ("valid_to", timedelta(days=1, hours=2))], ids=["time", "duration"])
def test_a_time_or_a_duration_in_a_date_column_is_an_unreadable_date(tmp_path, column, cell):
    row = list(item())
    row[HEADER.index(column)] = cell
    r = import_price_list(write_xlsx(tmp_path / "list.xlsx", [tuple(row)]), "S1", CURRENT, PreparedWriter(tmp_path), NOW)
    assert (r.updated, r.skipped) == ([], [("KM-1001", f"unreadable {column}")])


def test_a_number_that_a_workbook_stores_with_a_zero_fraction_is_read_without_it(tmp_path):
    xlsx = write_xlsx(tmp_path / "list.xlsx", [item(price=12.7, min_quantity=100)])
    stored = rewrite_sheet(xlsx, rb"<v>100</v>", b"<v>100.0</v>")
    (entry,) = import_price_list(stored, "S1", CURRENT, PreparedWriter(tmp_path), NOW).updated
    assert entry.evidence["row"]["min_quantity"] == "100"                  # not "100.0"


def test_a_workbook_with_a_wrong_dimension_record_is_read_in_full(tmp_path):
    xlsx = write_xlsx(tmp_path / "list.xlsx", [item("KM-1001"), item("KM-1002", "9.00")])
    stored = rewrite_sheet(xlsx, rb'<dimension ref="[^"]*"', b'<dimension ref="A1"')
    r = import_price_list(stored, "S1", CURRENT, PreparedWriter(tmp_path), NOW)
    assert [e.op.article_code for e in r.updated] == ["KM-1001", "KM-1002"]


def test_workbook_column_names_are_matched_without_regard_to_case_or_surrounding_spaces(tmp_path):
    header = (" Item_Code ", "PRICE", "Basis", " UNIT", "Min_Quantity", "VALID_FROM", "Valid_To ")
    r = import_price_list(write_xlsx(tmp_path / "list.xlsx", [item()], header=header), "S1", CURRENT, PreparedWriter(tmp_path), NOW)
    assert [e.evidence["row"] for e in r.updated] == [NORMAL]


def test_a_workbook_is_read_through_a_column_map(tmp_path):
    header = ("Artikel", "Netto prijs", "Per", "Eenheid", "Min. afname", "Geldig vanaf", "Geldig tot")
    xlsx = write_xlsx(tmp_path / "lijst.xlsx", [item()], header=header)
    r = import_price_list(xlsx, "S1", CURRENT, PreparedWriter(tmp_path), NOW, dict(zip(HEADER, header)))
    assert [e.evidence["row"] for e in r.updated] == [NORMAL]
    partial = write_xlsx(tmp_path / "partial.xlsx", [item()[:2]], header=("ARTIKEL ", "netto PRIJS"))        # names given in other case, too
    r = import_price_list(partial, "S1", CURRENT, PreparedWriter(tmp_path), NOW, {"item_code": " artikel", "price": "Netto Prijs"})
    assert [e.op.article_code for e in r.updated] == ["KM-1001"]
    with pytest.raises(ValueError, match="no column 'Prijs' for price"):
        import_price_list(partial, "S1", CURRENT, PreparedWriter(tmp_path), NOW, {"item_code": "Artikel", "price": "Prijs"})


def test_a_blank_item_code_cell_in_a_workbook_skips_the_row_and_names_its_row(tmp_path):
    no_code = (None, "5.00", "1", "pc", "1", "2026-11-01", "2027-01-31")
    xlsx = write_xlsx(tmp_path / "list.xlsx", [item("KM-1001"), no_code, item("KM-1002", "9.00")])
    r = import_price_list(xlsx, "S1", CURRENT, PreparedWriter(tmp_path), NOW)
    assert [e.op.article_code for e in r.updated] == ["KM-1001", "KM-1002"] and r.skipped == [("row 3", "no item code")]


def test_blank_rows_at_the_end_of_a_workbook_are_ignored(tmp_path):
    xlsx = write_xlsx(tmp_path / "list.xlsx", [item("KM-1001"), item("KM-1002", "8.00"), (" ",) * 7], blank_rows=3)
    assert load_workbook(xlsx).active.max_row == 7                        # the workbook does hold the blank rows: 1 header, 3 rows, 3 more
    r = import_price_list(xlsx, "S1", CURRENT, PreparedWriter(tmp_path), NOW)
    assert [e.op.article_code for e in r.updated] == ["KM-1001"] and r.skipped == [("KM-1002", "unchanged")]


def test_the_suffix_decides_between_csv_and_workbook_without_regard_to_case(tmp_path):
    xlsx = write_xlsx(tmp_path / "LIST.XLSX", [item()])
    assert [e.op.article_code for e in import_price_list(xlsx, "S1", CURRENT, PreparedWriter(tmp_path), NOW).updated] == ["KM-1001"]
    csv_file = write_csv(tmp_path / "LIST.CSV", [item("KM-1002", "9.00")])
    assert [e.op.article_code for e in import_price_list(csv_file, "S1", CURRENT, PreparedWriter(tmp_path), NOW).updated] == ["KM-1002"]


def test_a_row_number_is_the_position_in_the_file_as_a_spreadsheet_counts_it(tmp_path):
    path = tmp_path / "list.csv"
    path.write_text("\nitem_code,price\nKM-1001,12.70\n\n,5.00\n", encoding="utf-8")          # the header is in row 2, a blank row 4
    assert import_price_list(path, "S1", CURRENT, PreparedWriter(tmp_path), NOW).skipped == [("row 5", "no item code")]


# --- the columns ---

def test_the_column_map_gives_the_names_the_file_uses(tmp_path):
    header = ("Artikel", "Netto prijs", "Per", "Eenheid", "Min. afname", "Geldig vanaf", "Geldig tot")
    path = write_csv(tmp_path / "lijst.csv", [item()], header=header, delimiter=";")
    r = import_price_list(path, "S1", CURRENT, PreparedWriter(tmp_path), NOW, dict(zip(HEADER, header)))
    assert [e.evidence["row"] for e in r.updated] == [NORMAL]


def test_a_partial_column_map_leaves_the_other_columns_to_their_own_names(tmp_path):
    path = write_csv(tmp_path / "list.csv", [("KM-1001", "12.70", "pc")], header=("item_code", "Net price", "unit"))
    (entry,) = import_price_list(path, "S1", CURRENT, PreparedWriter(tmp_path), NOW, {"price": "Net price"}).updated
    assert entry.evidence["row"] == {"item_code": "KM-1001", "price": "12.70", "basis": "1", "unit": "pc", "min_quantity": None,
                                     "valid_from": None, "valid_to": None}


def test_column_names_are_matched_without_regard_to_case_or_surrounding_spaces(tmp_path):
    header = (" Item_Code ", "PRICE", "Basis", " UNIT", "Min_Quantity", "VALID_FROM", "Valid_To ")
    r = import_price_list(write_csv(tmp_path / "a.csv", [item()], header=header), "S1", CURRENT, PreparedWriter(tmp_path), NOW)
    assert [e.evidence["row"] for e in r.updated] == [NORMAL]
    mapped = write_csv(tmp_path / "b.csv", [item()[:2]], header=("artikel", "net price"))
    r = import_price_list(mapped, "S1", CURRENT, PreparedWriter(tmp_path), NOW, {"item_code": " ARTIKEL", "price": "Net Price "})
    assert [e.op.article_code for e in r.updated] == ["KM-1001"]


def test_a_list_with_only_an_item_code_and_a_price_gets_the_default_basis_and_no_other_value(tmp_path):
    path = write_csv(tmp_path / "list.csv", [("KM-1001", "12.70")], header=("item_code", "price"))
    (entry,) = import_price_list(path, "S1", CURRENT, PreparedWriter(tmp_path), NOW).updated
    assert entry.evidence["row"] == {"item_code": "KM-1001", "price": "12.70", "basis": "1", "unit": None, "min_quantity": None,
                                     "valid_from": None, "valid_to": None}


@pytest.mark.parametrize("header, absent", [(("price", "unit"), "item_code"), (("item_code", "unit"), "price")])
def test_a_list_without_an_item_code_or_a_price_column_is_refused(tmp_path, header, absent):
    path = write_csv(tmp_path / "list.csv", [], header=header)
    with pytest.raises(ValueError, match=f"no column '{absent}'"):
        import_price_list(path, "S1", CURRENT, PreparedWriter(tmp_path), NOW)


def test_a_missing_column_is_named_with_the_columns_the_file_has(tmp_path):
    path = write_csv(tmp_path / "list.csv", [("KM-1001", "12.70")], header=("Artikel", "Prijs"))
    with pytest.raises(ValueError, match=r"list.csv has no column 'item_code' for item_code \(its columns: Artikel, Prijs\)"):
        import_price_list(path, "S1", CURRENT, PreparedWriter(tmp_path), NOW)


def test_a_column_the_map_names_must_be_in_the_file_though_an_optional_column_may_be_missing(tmp_path):
    path = write_csv(tmp_path / "list.csv", [("KM-1001", "12.70")], header=("item_code", "price"))
    assert import_price_list(path, "S1", CURRENT, PreparedWriter(tmp_path), NOW).updated            # no valid_to column: no bound
    with pytest.raises(ValueError, match="no column 'Valid until' for valid_to"):
        import_price_list(path, "S1", CURRENT, PreparedWriter(tmp_path), NOW, {"valid_to": "Valid until"})


def test_a_column_map_that_names_an_unknown_column_is_refused(tmp_path):
    path = write_csv(tmp_path / "list.csv", [item()])
    with pytest.raises(ValueError, match="'prise'"):
        import_price_list(path, "S1", CURRENT, PreparedWriter(tmp_path), NOW, {"prise": "price"})


def test_two_columns_of_one_name_are_refused_instead_of_choosing_one(tmp_path):
    path = write_csv(tmp_path / "list.csv", [("KM-1001", "12.70", "13.00")], header=("item_code", "price", "Price"))
    with pytest.raises(ValueError, match="2 columns named 'price'"):
        import_price_list(path, "S1", CURRENT, PreparedWriter(tmp_path), NOW)


def test_a_file_that_is_missing_raises_the_error_of_the_file_system(tmp_path):
    with pytest.raises(FileNotFoundError):
        import_price_list(tmp_path / "nope.csv", "S1", CURRENT, PreparedWriter(tmp_path), NOW)


def make_zip_without_workbook_parts(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("a.txt", "no workbook")


@pytest.mark.parametrize("name, content", [
    ("empty.csv", b""),
    ("blank.csv", b"\n\n"),
    ("list.txt", b"item_code,price\nKM-1001,12.70\n"),                     # a suffix that is neither .csv nor .xlsx
    ("list.xls", b"\xd0\xcf\x11\xe0"),
    ("binary.csv", b"item_code,price\nKM-1001,12.\x8170\n"),               # 0x81 is neither UTF-8 nor Windows-1252
    ("damaged.xlsx", b"this is not a workbook"),
    ("parts.xlsx", None),                                                   # a zip file that holds no workbook
])
def test_a_list_that_cannot_be_read_raises_before_anything_is_written(tmp_path, name, content):
    path = tmp_path / name
    if content is None:
        make_zip_without_workbook_parts(path)
    else:
        path.write_bytes(content)
    with pytest.raises(ValueError, match=re.escape(name)):
        import_price_list(path, "S1", CURRENT, PreparedWriter(tmp_path), NOW)
    assert not (tmp_path / "prepared_changes.csv").exists()


@pytest.mark.parametrize("member", ["xl/worksheets/sheet1.xml", "[Content_Types].xml"])
@pytest.mark.parametrize("fraction", [0.25, 0.5, 0.75])
def test_a_workbook_with_a_damaged_byte_raises_value_error_whatever_the_damage(tmp_path, member, fraction):
    xlsx = write_xlsx(tmp_path / "list.xlsx", [item("KM-1001"), item("KM-1002", "9.00"), item("KM-1003", "5.50")])
    damaged = flipped(xlsx, member, fraction)                              # the zip file or its data fails: BadZipFile, zlib.error, ...
    with pytest.raises(ValueError, match=f"{damaged.name} cannot be read as an .xlsx workbook"):
        import_price_list(damaged, "S1", CURRENT, PreparedWriter(tmp_path), NOW)
    assert not (tmp_path / "prepared_changes.csv").exists()


def test_a_workbook_whose_directory_offset_is_damaged_raises_value_error(tmp_path):
    xlsx = write_xlsx(tmp_path / "list.xlsx", [item()])
    raw = bytearray(xlsx.read_bytes())
    raw[-5] ^= 0xFF                                                        # in the end record of the directory: its offset leads outside the file
    damaged = tmp_path / "offset.xlsx"
    damaged.write_bytes(raw)
    with pytest.raises(ValueError, match="offset.xlsx cannot be read as an .xlsx workbook"):
        import_price_list(damaged, "S1", CURRENT, PreparedWriter(tmp_path), NOW)


def test_a_workbook_that_names_a_compression_method_zipfile_does_not_have_raises_value_error(tmp_path):
    xlsx = write_xlsx(tmp_path / "list.xlsx", [item()])
    unknown = tmp_path / "method.xlsx"
    with zipfile.ZipFile(xlsx) as source, zipfile.ZipFile(unknown, "w") as target:
        for member in source.namelist():
            target.writestr(member, source.read(member))
        for info in target.filelist:
            info.compress_type = 99                                        # the directory, written when the file is closed, names it
    with pytest.raises(ValueError, match="method.xlsx cannot be read as an .xlsx workbook"):
        import_price_list(unknown, "S1", CURRENT, PreparedWriter(tmp_path), NOW)


@pytest.mark.parametrize("member", ["docProps/core.xml", "xl/workbook.xml", "xl/worksheets/sheet1.xml"])      # members openpyxl reads
def test_a_workbook_with_an_encrypted_member_raises_value_error(tmp_path, member):
    locked = marked_encrypted(write_xlsx(tmp_path / "list.xlsx", [item()]), member)
    with pytest.raises(ValueError, match=re.escape(locked.name) + r" cannot be read as an \.xlsx workbook \(File '.*' is encrypted"):
        import_price_list(locked, "S1", CURRENT, PreparedWriter(tmp_path), NOW)
    assert not (tmp_path / "prepared_changes.csv").exists()


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="the open files are listed in /proc/self/fd")
@pytest.mark.parametrize("damage", ["no workbook parts", "a damaged byte", "an encrypted member"])
def test_a_workbook_that_cannot_be_read_is_not_left_open(tmp_path, damage):
    path = tmp_path / "list.xlsx"
    if damage == "no workbook parts":
        make_zip_without_workbook_parts(path)
    elif damage == "a damaged byte":
        path = flipped(write_xlsx(path, [item()]), "[Content_Types].xml", 0.25)
    else:
        path = marked_encrypted(write_xlsx(path, [item()]), "docProps/core.xml")
    with pytest.raises(ValueError) as stopped:
        import_price_list(path, "S1", CURRENT, PreparedWriter(tmp_path), NOW)
    assert path.resolve() not in open_files()                              # `stopped` keeps the error, with the frames behind it, alive


def test_a_workbook_without_rows_is_refused(tmp_path):
    Workbook().save(tmp_path / "list.xlsx")
    with pytest.raises(ValueError, match="list.xlsx is empty"):
        import_price_list(tmp_path / "list.xlsx", "S1", CURRENT, PreparedWriter(tmp_path), NOW)


def test_a_current_price_that_is_not_a_number_is_refused_before_anything_is_written(tmp_path):
    path = write_csv(tmp_path / "list.csv", [item("KM-1001"), item("KM-1002", "9.00")])
    with pytest.raises(ValueError, match="KM-1002"):
        import_price_list(path, "S1", {"KM-1001": "12.50", "KM-1002": "n/a"}, PreparedWriter(tmp_path), NOW)
    assert not (tmp_path / "prepared_changes.csv").exists()


# --- the values of a row ---

def test_the_values_of_a_row_are_in_the_normal_forms_of_the_schema(tmp_path):
    r = run(tmp_path, [item("KM-1001", "EUR 12,70", "per stuk", "Stück", "10", "01-11-2026", "31.01.2027")])
    assert [e.evidence["row"] for e in r.updated] == [NORMAL | {"min_quantity": "10"}]


def test_an_item_code_is_trimmed(tmp_path):
    r = run(tmp_path, [item(" KM-1001 ")])
    assert [e.op.article_code for e in r.updated] == ["KM-1001"] and r.updated[0].evidence["row"]["item_code"] == "KM-1001"


def test_a_blank_basis_is_1_and_a_blank_date_is_no_bound(tmp_path):
    r = run(tmp_path, [item(basis="", unit="", min_quantity="", valid_from="", valid_to="")])
    assert [e.evidence["row"] for e in r.updated] == [NORMAL | {"basis": "1", "unit": None, "min_quantity": None, "valid_from": None,
                                                                "valid_to": None}]


def test_a_unit_or_quantity_that_cannot_be_read_is_none_and_the_row_is_still_imported(tmp_path):
    r = run(tmp_path, [item(unit="", min_quantity="n/a")])
    assert [e.evidence["row"] for e in r.updated] == [NORMAL | {"unit": None, "min_quantity": None}] and r.skipped == []


def test_a_row_whose_price_basis_is_not_1_is_skipped_as_unknown_price_basis(tmp_path):
    """Final review, finding 29: the current prices have no basis, so only a price per piece can be compared with them. A row whose
    basis is present and not 1 (per 100, or one that cannot be read) is skipped, after the reasons before it and before
    "unchanged"; it settles nothing, so a later row per piece for the same article is imported."""
    rows = [item("KM-1001", "1250,00", basis="100"), item("KM-1002", "9.00", basis="per 100 st"), item("KM-1003", "5.00", basis="2,5"),
            item("KM-1004", "3.50", basis="per stuk"), item("KM-1001", "12.70", basis=""), item("KM-9999", basis="100"),
            item("KM-1003", "", basis="100")]
    r = run(tmp_path, rows)
    assert r.skipped == [("KM-1001", "unknown price basis"), ("KM-1002", "unknown price basis"), ("KM-1003", "unknown price basis"),
                         ("KM-9999", "unknown article"), ("KM-1003", "unreadable price")]
    assert [(e.op.article_code, e.op.new_value) for e in r.updated] == [("KM-1004", "3.50"), ("KM-1001", "12.70")]


@pytest.mark.parametrize("cell", ["", "n/a", "on request", "-"])
def test_a_price_that_cannot_be_read_skips_the_row_with_a_reason_naming_the_column(tmp_path, cell):
    r = run(tmp_path, [item(price=cell)])
    assert (r.updated, r.skipped, r.expired) == ([], [("KM-1001", "unreadable price")], False)


@pytest.mark.parametrize("column, cell", [
    ("valid_from", "soon"), ("valid_from", "31-02-2026"), ("valid_from", "2026-W47"), ("valid_from", "2026-11-01/2026-12-31"),
    ("valid_to", "later"), ("valid_to", "2027-13-01"), ("valid_to", "KW 5"), ("valid_to", "01-11-2026/31-01-2027"),
])
def test_a_date_that_cannot_be_read_skips_the_row_with_a_reason_naming_the_column(tmp_path, column, cell):
    row = list(item())
    row[HEADER.index(column)] = cell
    r = run(tmp_path, [tuple(row)])
    assert (r.updated, r.skipped, r.expired) == ([], [("KM-1001", f"unreadable {column}")], False)


@pytest.mark.parametrize("valid_from, valid_to, reason", [
    ("2026-11-18", "2026-11-18", None),                                    # valid on that one day
    ("2026-11-19", "", "not yet valid"),
    ("", "2026-11-17", "expired"),
    ("", "", None),
    ("18-11-2026", "18.11.2026", None),
])
def test_a_row_is_valid_from_its_first_day_to_its_last_day(tmp_path, valid_from, valid_to, reason):
    r = run(tmp_path, [item(valid_from=valid_from, valid_to=valid_to)])
    assert r.skipped == ([] if reason is None else [("KM-1001", reason)]) and len(r.updated) == (reason is None)


# --- why a row is skipped ---

def test_every_skip_reason_is_given_with_the_item_or_the_row(tmp_path):
    r = run(tmp_path, [item(""),                                           # row 2
                       item("KM-1009"),                                    # not in current_prices
                       item("KM-1004", valid_from="2026-12-01"),
                       item("KM-1003", valid_to="2026-06-30"),
                       item("KM-1002", price="8"),                         # 8 is 8.00
                       item("KM-1001"),                                    # the one change
                       item("KM-1001", price="12.90"),
                       item("KM-1004", price="n/a")])
    assert [e.op.article_code for e in r.updated] == ["KM-1001"] and r.expired
    assert r.skipped == [("row 2", "no item code"), ("KM-1009", "unknown article"), ("KM-1004", "not yet valid"), ("KM-1003", "expired"),
                         ("KM-1002", "unchanged"), ("KM-1001", "duplicate row"), ("KM-1004", "unreadable price")]


@pytest.mark.parametrize("row, reason", [
    (item("", price="n/a", valid_to="2026-06-30"), "no item code"),                        # first: nothing else is looked at
    (item("KM-9999", valid_from="soon", valid_to="2026-06-30"), "unreadable valid_from"),
    (item("KM-9999", valid_from="2026-12-01", valid_to="2026-06-30"), "not yet valid"),    # valid_from before valid_to
    (item("KM-9999", valid_to="soon"), "unreadable valid_to"),
    (item("KM-9999", valid_to="2026-06-30"), "expired"),                                   # the validity before the article
    (item("KM-9999", price="n/a"), "unknown article"),                                     # the article before the price
    (item("KM-1002", price="8.00", valid_to="2026-06-30"), "expired"),                     # the validity before "unchanged"
    (item("KM-1002", price="n/a", valid_to="2026-06-30"), "expired"),
    (item("KM-1002", price="n/a"), "unreadable price"),
    (item("KM-1002", price="8.0"), "unchanged"),
])
def test_the_first_reason_in_the_order_is_the_one_given(tmp_path, row, reason):
    assert run(tmp_path, [row]).skipped == [(row[0] or "row 2", reason)]


def test_the_expired_flag_is_set_by_an_expired_row_and_by_nothing_else(tmp_path):
    rows = [item("KM-1002", price="8.00"), item("KM-1003", valid_from="2026-12-01"), item("KM-9999"), item("KM-1004", price="n/a"), item("")]
    assert run(tmp_path, rows).expired is False
    assert run(tmp_path, [*rows, item("KM-9999", valid_to="2026-06-30")]).expired is True       # an expired row for an article not known


def test_the_first_row_in_force_settles_an_article_and_a_later_row_is_a_duplicate(tmp_path):
    r = run(tmp_path, [item("KM-1001", "12.70"), item("KM-1001", "12.90"), item("KM-1001", "12.50")])
    assert [e.op.new_value for e in r.updated] == ["12.70"]
    assert r.skipped == [("KM-1001", "duplicate row"), ("KM-1001", "unchanged")]          # "unchanged" comes first in the order


def test_a_row_that_leaves_the_price_unchanged_settles_the_article_too(tmp_path):
    r = run(tmp_path, [item("KM-1002", "8.00", min_quantity="1"), item("KM-1002", "7.00", min_quantity="100")])
    assert r.updated == [] and r.skipped == [("KM-1002", "unchanged"), ("KM-1002", "duplicate row")]


@pytest.mark.parametrize("old", [dict(valid_to="2026-06-30"), dict(valid_from="2026-12-01"), dict(price="n/a")])
def test_a_row_that_is_not_in_force_settles_nothing_so_the_current_row_after_it_is_imported(tmp_path, old):
    r = run(tmp_path, [item("KM-1001", **old), item("KM-1001", "12.90")])
    assert [e.op.new_value for e in r.updated] == ["12.90"] and len(r.skipped) == 1


# --- the write ---

def test_a_change_is_one_article_price_operation_with_its_evidence(tmp_path):
    r = run(tmp_path, [item("KM-1001", "12,70", "1", "Stück", "10", "01-11-2026", "31.01.2027")])
    (entry,) = r.updated
    assert entry.op == WriteOp(kind="article_price", po_number="", version=None, line_id=None, article_code="KM-1001", old_value="12.50",
                               new_value="12.70", document=None)
    assert (entry.case_id, entry.rule_id, entry.status, entry.previous, entry.new, entry.timestamp) == (
        "pricelist_S1_20261118T093000", "P01", "prepared", "12.50", "12.70", "2026-11-18T09:30:00")
    assert entry.evidence == {"supplier": "S1", "file": "list.csv", "row": NORMAL | {"min_quantity": "10"}}
    assert r.supplier == "S1"


def test_the_changes_are_applied_in_the_order_of_the_file_and_prepared_one_row_each(tmp_path):
    r = run(tmp_path, [item("KM-1003", "5.50"), item("KM-1001", "12.70")])
    assert [e.op.article_code for e in r.updated] == ["KM-1003", "KM-1001"]
    assert [(row["entry_id"], row["case_id"], row["po_number"], row["version"], row["line_id"], row["article_code"], row["kind"],
             row["old_value"], row["new_value"], row["document"], row["rule_id"], row["timestamp"]) for row in prepared_rows(tmp_path)] == [
        (e.entry_id, "pricelist_S1_20261118T093000", "", "", "", e.op.article_code, "article_price", e.op.old_value, e.op.new_value, "",
         "P01", "2026-11-18T09:30:00") for e in r.updated]


def test_the_result_needs_only_the_three_fields_of_the_interface():
    result = ImportResult(updated=[], skipped=[("KM-1001", "unchanged")], expired=False)
    assert (result.supplier, result.outcomes, result.errors) == ("", [], [])


def test_a_writer_error_on_one_row_is_recorded_and_the_import_goes_on(tmp_path):
    path = write_csv(tmp_path / "list.csv", [item("KM-1001"), item("KM-1002", "9.00"), item("KM-1003", "5.50")])
    r = import_price_list(path, "S1", CURRENT, FlakyWriter(tmp_path, {"KM-1002": WriterError("503")}), NOW)
    assert [e.op.article_code for e in r.updated] == ["KM-1001", "KM-1003"] and r.skipped == [("KM-1002", "error: 503")]
    assert [row["article_code"] for row in prepared_rows(tmp_path)] == ["KM-1001", "KM-1003"] and r.expired is False


@pytest.mark.parametrize("error, reason", [
    (WriterError("503"), "error: 503"),                                      # the writer's own message stands alone
    (WriterError(""), "error: WriterError"),
    (RuntimeError("the disk is gone"), "error: RuntimeError: the disk is gone"),
    (RuntimeError(), "error: RuntimeError"),
    (ValueError("its evidence is not JSON"), "error: ValueError: its evidence is not JSON"),
])
def test_the_reason_of_a_failed_write_names_the_error(tmp_path, error, reason):
    r = import_price_list(write_csv(tmp_path / "list.csv", [item()]), "S1", CURRENT, FlakyWriter(tmp_path, {"KM-1001": error}), NOW)
    assert r.updated == [] and r.skipped == [("KM-1001", reason)]


def test_a_failed_write_still_settles_the_article(tmp_path):                 # so that the rows decide the same whatever the writer does
    path = write_csv(tmp_path / "list.csv", [item("KM-1002", "9.00"), item("KM-1002", "9.50")])
    r = import_price_list(path, "S1", CURRENT, FlakyWriter(tmp_path, {"KM-1002": WriterError("503")}), NOW)
    assert r.updated == [] and r.skipped == [("KM-1002", "error: 503"), ("KM-1002", "duplicate row")]


def test_any_error_of_a_write_is_a_skipped_row_so_that_the_entries_already_made_are_kept(tmp_path):
    rows = [item("KM-1001", "12.70"), item("KM-1002", "9.00"), item("KM-1003", "5.50"), item("KM-1004", "3.50")]
    writer = FlakyWriter(tmp_path, {"KM-1003": RuntimeError("the disk is gone")})                     # the third of four changes
    r = import_price_list(write_csv(tmp_path / "list.csv", rows), "S1", CURRENT, writer, NOW)
    assert [e.op.article_code for e in r.updated] == ["KM-1001", "KM-1002", "KM-1004"]
    assert r.skipped == [("KM-1003", "error: RuntimeError: the disk is gone")]
    assert [row["article_code"] for row in prepared_rows(tmp_path)] == ["KM-1001", "KM-1002", "KM-1004"]


def test_an_interrupt_is_not_swallowed_as_an_error_of_a_write(tmp_path):
    with pytest.raises(KeyboardInterrupt):
        import_price_list(write_csv(tmp_path / "list.csv", [item()]), "S1", CURRENT, FlakyWriter(tmp_path, {"KM-1001": KeyboardInterrupt()}), NOW)


def test_the_outcomes_are_the_entries_and_the_skips_in_the_order_of_the_file(tmp_path):
    rows = [item("KM-1002", "8.00"), item("KM-1001"), item(""), item("KM-1003", valid_to="2026-06-30"), item("KM-1004", "3.50")]
    r = import_price_list(write_csv(tmp_path / "list.csv", rows), "S1", CURRENT, FlakyWriter(tmp_path, {"KM-1004": WriterError("503")}), NOW)
    assert r.outcomes == [("KM-1002", "unchanged"), r.updated[0], ("row 4", "no item code"), ("KM-1003", "expired"), ("KM-1004", "error: 503")]
    assert r.updated[0].op.article_code == "KM-1001" and len(r.updated) == 1
    assert r.skipped == [outcome for outcome in r.outcomes if isinstance(outcome, tuple)]
    assert r.errors == [("KM-1004", "error: 503")]


def test_the_changes_can_be_logged_and_undone(tmp_path):
    r = run(tmp_path, [item("KM-1001", "12.70"), item("KM-1002", "9.00")])
    log = WriteLog(tmp_path)
    for entry in r.updated:
        log.append(entry)
    undone = undo_case(tmp_path, PreparedWriter(tmp_path), NOW)
    assert [(u.op.article_code, u.op.old_value, u.op.new_value) for u in undone] == [("KM-1002", "9.00", "8.00"), ("KM-1001", "12.70", "12.50")]


# --- the command ---

def command(tmp_path: Path, list_file: Path | None = None, current: Path | None = None, write_log: Path | None = None) -> list[str]:
    """The arguments of a run on tmp_path, which prepares its changes in tmp_path / "prepared" and logs them in write_log (by default
    tmp_path / "log"). By default the list is a workbook with a change, an unchanged price and an expired row, and the current prices are
    CURRENT written with semicolons and decimal commas."""
    if current is None:
        current = tmp_path / "current.csv"
        lines = [f"{code};{price.replace('.', ',')}\n" for code, price in CURRENT.items()]
        current.write_text("item_code;price\n" + "".join(lines), encoding="utf-8")
    if list_file is None:
        list_file = write_xlsx(tmp_path / "list.xlsx", [item("KM-1001"), item("KM-1002", "8.00"), item("KM-1003", valid_to="2026-06-30")])
    return ["--file", str(list_file), "--supplier", "S1", "--current", str(current), "--prepared", str(tmp_path / "prepared"),
            "--write-log", str(write_log or tmp_path / "log")]


def test_the_command_prepares_the_changes_and_prints_a_line_per_row_and_a_summary(tmp_path, capsys):
    assert main(command(tmp_path), now=NOW) == 0
    (row,) = prepared_rows(tmp_path / "prepared")
    assert (row["case_id"], row["article_code"], row["kind"], row["old_value"], row["new_value"], row["rule_id"], row["timestamp"]) == (
        "pricelist_S1_20261118T093000", "KM-1001", "article_price", "12.50", "12.70", "P01", "2026-11-18T09:30:00")
    out, err = capsys.readouterr()
    assert out.splitlines() == ["updated KM-1001 from 12.50 to 12.70 (prepared)", "skipped KM-1002: unchanged", "skipped KM-1003: expired",
                                "S1, list.xlsx: 1 updated, 2 skipped, 0 errors",
                                "Some rows are past their valid_to: ask the supplier for a current list."]
    assert err == ""


def test_the_command_says_nothing_of_expiry_when_no_row_is_expired(tmp_path, capsys):
    list_file = write_csv(tmp_path / "list.csv", [item()])
    assert main(command(tmp_path, list_file=list_file), now=NOW) == 0
    assert capsys.readouterr().out.splitlines() == ["updated KM-1001 from 12.50 to 12.70 (prepared)",
                                                    "S1, list.csv: 1 updated, 0 skipped, 0 errors"]


def test_the_command_prints_the_rows_in_the_order_of_the_file(tmp_path, capsys):
    rows = [item("KM-1002", "8.00"), item("KM-1001"), item("KM-1003", valid_to="2026-06-30"), item("KM-1004", "3.50"), item("")]
    assert main(command(tmp_path, list_file=write_csv(tmp_path / "list.csv", rows)), now=NOW) == 0
    assert capsys.readouterr().out.splitlines() == [
        "skipped KM-1002: unchanged", "updated KM-1001 from 12.50 to 12.70 (prepared)", "skipped KM-1003: expired",
        "updated KM-1004 from 3.20 to 3.50 (prepared)", "skipped row 6: no item code",
        "S1, list.csv: 2 updated, 3 skipped, 0 errors", "Some rows are past their valid_to: ask the supplier for a current list."]


def test_the_command_counts_a_failed_write_as_an_error_not_a_skip_and_returns_1(tmp_path, capsys, monkeypatch):
    errors = {"KM-1003": RuntimeError("the disk is gone"), "KM-1004": WriterError("503")}
    monkeypatch.setattr("aid.pricelist.PreparedWriter", lambda folder: FlakyWriter(folder, errors))
    rows = [item("KM-1001"), item("KM-1002", "8.00"), item("KM-1003", "5.50"), item("KM-1004", "3.50")]
    assert main(command(tmp_path, list_file=write_csv(tmp_path / "list.csv", rows)), now=NOW) == 1
    assert capsys.readouterr().out.splitlines() == [
        "updated KM-1001 from 12.50 to 12.70 (prepared)", "skipped KM-1002: unchanged", "error KM-1003: RuntimeError: the disk is gone",
        "error KM-1004: 503", "S1, list.csv: 1 updated, 1 skipped, 2 errors"]


def test_a_prepared_folder_that_cannot_be_written_makes_an_error_of_every_change(tmp_path, capsys):
    (tmp_path / "prepared").write_text("a file where the folder should be", encoding="utf-8")
    assert main(command(tmp_path, list_file=write_csv(tmp_path / "list.csv", [item()])), now=NOW) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("error KM-1001: cannot write ") and lines[1:] == ["S1, list.csv: 0 updated, 0 skipped, 1 errors"]


def test_the_command_returns_0_when_every_row_is_skipped(tmp_path, capsys):
    assert main(command(tmp_path, list_file=write_csv(tmp_path / "list.csv", [item("KM-9999")])), now=NOW) == 0
    assert "skipped KM-9999: unknown article" in capsys.readouterr().out and not (tmp_path / "prepared").exists()


def test_the_command_uses_the_clock_when_it_is_given_no_time(tmp_path):
    list_file = write_csv(tmp_path / "list.csv", [item(valid_from="2000-01-01", valid_to="2099-12-31")])      # valid whatever the date
    assert main(command(tmp_path, list_file=list_file)) == 0
    assert [row["article_code"] for row in prepared_rows(tmp_path / "prepared")] == ["KM-1001"]


def test_the_command_runs_as_a_module(tmp_path):
    list_file = write_csv(tmp_path / "list.csv", [item(valid_from="2000-01-01", valid_to="2099-12-31")])
    done = subprocess.run([sys.executable, "-B", "-W", "error", "-m", "aid.pricelist", *command(tmp_path, list_file=list_file)],
                          cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8")
    assert (done.returncode, done.stderr) == (0, "") and done.stdout.splitlines()[0] == "updated KM-1001 from 12.50 to 12.70 (prepared)"
    assert [row["article_code"] for row in prepared_rows(tmp_path / "prepared")] == ["KM-1001"]


def test_the_command_returns_1_for_a_price_list_that_cannot_be_read(tmp_path, capsys):
    damaged = tmp_path / "list.xlsx"
    damaged.write_bytes(b"this is not a workbook")
    assert main(command(tmp_path, list_file=damaged), now=NOW) == 1
    out, err = capsys.readouterr()
    assert out == "" and "list.xlsx" in err and not (tmp_path / "prepared").exists()


def test_the_command_returns_1_for_a_workbook_with_a_damaged_byte(tmp_path, capsys):
    damaged = flipped(write_xlsx(tmp_path / "list.xlsx", [item("KM-1001"), item("KM-1002", "9.00"), item("KM-1003", "5.50")]),
                      "xl/worksheets/sheet1.xml", 0.25)
    assert main(command(tmp_path, list_file=damaged), now=NOW) == 1
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("cannot read: flipped_list.xlsx cannot be read as an .xlsx workbook") and not (tmp_path / "prepared").exists()


def test_the_command_returns_1_with_one_line_for_a_workbook_with_an_encrypted_member(tmp_path, capsys):
    locked = marked_encrypted(write_xlsx(tmp_path / "list.xlsx", [item()]), "docProps/core.xml")
    assert main(command(tmp_path, list_file=locked), now=NOW) == 1
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("cannot read: encrypted_list.xlsx cannot be read as an .xlsx workbook (File 'docProps/core.xml' is encrypted")
    assert err.count("\n") == 1 and not (tmp_path / "prepared").exists()                    # one line, not a traceback


def test_the_command_returns_1_for_a_price_list_that_is_missing(tmp_path, capsys):
    assert main(command(tmp_path, list_file=tmp_path / "nope.csv"), now=NOW) == 1
    assert "nope.csv" in capsys.readouterr().err


@pytest.mark.parametrize("content, problem", [
    ("item_code;price\nKM-1001;n/a\n", "KM-1001"),
    ("item_code;price\nKM-1001;1,00\nKM-1001;2,00\n", "listed twice"),
    ("item_code;price\n;1,00\n", "no item code"),
    ("item_code;cost\nKM-1001;1,00\n", "no column 'price'"),
])
def test_the_command_returns_1_for_current_prices_that_cannot_be_read(tmp_path, capsys, content, problem):
    current = tmp_path / "current.csv"
    current.write_text(content, encoding="utf-8")
    assert main(command(tmp_path, current=current), now=NOW) == 1
    err = capsys.readouterr().err
    assert "current.csv" in err and problem in err and not (tmp_path / "prepared").exists()


def test_the_command_returns_1_for_current_prices_that_are_missing(tmp_path, capsys):
    assert main(command(tmp_path, current=tmp_path / "nope.csv"), now=NOW) == 1
    assert "nope.csv" in capsys.readouterr().err


@pytest.mark.parametrize("missing", ["--file", "--supplier", "--current", "--prepared", "--write-log"])
def test_a_command_line_without_a_required_option_exits_with_2(tmp_path, capsys, missing):
    arguments = command(tmp_path)
    at = arguments.index(missing)
    with pytest.raises(SystemExit) as stopped:
        main(arguments[:at] + arguments[at + 2:], now=NOW)
    assert stopped.value.code == 2 and missing in capsys.readouterr().err


def test_the_help_names_the_options(capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["--help"])
    out = capsys.readouterr().out
    assert stopped.value.code == 0 and all(option in out for option in ("--file", "--supplier", "--current", "--prepared", "--write-log",
                                                                        "--settings"))


# --- the write log of the command (final review, finding 30) ---

def test_the_command_logs_every_entry_so_that_the_undo_can_reverse_the_import(tmp_path, capsys):
    rows = [item("KM-1001"), item("KM-1002", "9.00"), item("KM-1003", "5.00")]
    log = tmp_path / "pricelist log"
    assert main(command(tmp_path, list_file=write_csv(tmp_path / "list.csv", rows), write_log=log), now=NOW) == 0
    entries = WriteLog(log).entries()
    assert [(e.op.article_code, e.op.old_value, e.op.new_value, e.status, e.rule_id) for e in entries] == [
        ("KM-1001", "12.50", "12.70", "prepared", "P01"), ("KM-1002", "8.00", "9.00", "prepared", "P01")]
    assert [e.entry_id for e in entries] == [row["entry_id"] for row in prepared_rows(tmp_path / "prepared")]
    undone = undo_case(log, PreparedWriter(tmp_path / "undo"), NOW)
    assert [(u.op.article_code, u.op.old_value, u.op.new_value, u.undo_of) for u in undone] == [
        ("KM-1002", "9.00", "8.00", entries[1].entry_id), ("KM-1001", "12.70", "12.50", entries[0].entry_id)]
    assert [row["new_value"] for row in prepared_rows(tmp_path / "undo")] == ["8.00", "12.50"]
    assert main(command(tmp_path, list_file=write_csv(tmp_path / "list.csv", [item("KM-1004", "3.50")]), write_log=log), now=NOW) == 0
    assert [e.op.article_code for e in WriteLog(log).entries()][-1] == "KM-1004"                   # a second import appends


def test_the_command_logs_nothing_when_no_price_changed(tmp_path):
    assert main(command(tmp_path, list_file=write_csv(tmp_path / "list.csv", [item("KM-1002", "8.00")])), now=NOW) == 0
    assert WriteLog(tmp_path / "log").entries() == []


def test_with_settings_the_write_log_defaults_to_the_day_s_folder_under_the_cases_root(tmp_path):
    example = (REPO_ROOT / "aid" / "settings.example.json").read_text(encoding="utf-8")
    settings = tmp_path / "settings.json"
    settings.write_text(example.replace('"cases_root": "cases"', '"cases_root": "my cases"'), encoding="utf-8")
    arguments = command(tmp_path)
    arguments = arguments[:arguments.index("--write-log")] + ["--settings", str(settings)]
    assert main(arguments, now=NOW) == 0
    assert [e.op.article_code for e in WriteLog(tmp_path / "my cases" / "pricelists" / "2026-11-18").entries()] == ["KM-1001"]
    assert not (tmp_path / "log").exists()
    log = tmp_path / "given"
    assert main(arguments + ["--write-log", str(log)], now=NOW) == 0                                # a folder given wins
    assert [e.op.article_code for e in WriteLog(log).entries()] == ["KM-1001"]


def test_the_command_returns_1_when_the_write_log_cannot_be_written(tmp_path, capsys):
    (tmp_path / "log").write_text("a file where the folder should be", encoding="utf-8")
    assert main(command(tmp_path), now=NOW) == 1
    out, err = capsys.readouterr()
    assert "cannot write the write log" in err and "log" in err
    assert [row["article_code"] for row in prepared_rows(tmp_path / "prepared")] == ["KM-1001"]


# --- the article prices of an Exact export, kept per supplier (Task 42) ---

import json  # noqa: E402

from aid.execution.by_hand import link_put_back, mark, open_changes  # noqa: E402
from aid.execution.writer import undo_entry  # noqa: E402
from aid.pricelist import (ARTICLES_FILE, ARTICLES_FOLDER, SUPPLIER_NAME_MAX, NameRefused, NoArticle, also_held,  # noqa: E402
                           current_prices, is_supplier_name, list_of, read_articles, store_current, stored_prices, supplier_named)
from aid.schema import normalize_number  # noqa: E402
from aid.readers.po_export import ExportRefused  # noqa: E402
from aid.tests.helpers import wrong_dimensions, write_sheet  # noqa: E402

ARTICLE_COLUMNS = {"item_code": "Artikel", "price": "Inkoopprijs"}                    # an export of one supplier's articles
WITH_SUPPLIER = ARTICLE_COLUMNS | {"supplier": "Leverancier"}                         # an export that names the supplier of each row
STORED = datetime(2026, 11, 20, 9, 15)                                                # when the made-up export is given
TITLES = ["Artikel", "Omschrijving", "Inkoopprijs"]


def articles(path: Path, rows: list[list], titles: list[str] = TITLES) -> Path:
    """A made-up export of articles at `path`: a title above the row of column titles, as an overview of the ERP prints one, then the
    rows. A .csv file is cut at semicolons and every cell is a text; in a workbook a str is a text cell and a number a number cell."""
    return write_sheet(path, [["Artikelen van een leverancier (verzonnen voorbeeld)"], [], titles, *rows])


def held(cases_root: Path) -> dict:
    """What the file of the stored article prices holds."""
    return json.loads((cases_root / ARTICLES_FOLDER / ARTICLES_FILE).read_text(encoding="utf-8"))


def test_the_article_export_is_stored_and_a_newer_one_replaces_it(tmp_path):
    """Amendment 1: the article prices are kept per supplier. An export without a supplier column is stored under the name the buyer
    gives; a newer export replaces, whole, the prices of each supplier it holds and leaves every other supplier's as they are."""
    root = tmp_path / "cases"
    first = articles(tmp_path / "artikelen.csv", [["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", "8,00"], ["KM-1003", "Ring", "1.250,00"]])
    assert store_current(root, first, ARTICLE_COLUMNS, True, STORED, "S01") == (["S01"], 3, 0)
    assert held(root) == {"suppliers": {"S01": {"stored_at": "2026-11-20T09:15:00", "file": "artikelen.csv",
                                                "prices": {"KM-1001": "12.50", "KM-1002": "8.00", "KM-1003": "1250.00"}}}}
    assert (root / "exact" / "article_prices.json").is_file() and sorted(path.name for path in (root / "exact").iterdir()) == [ARTICLES_FILE]
    assert current_prices(root, "S01") == ({"KM-1001": "12.50", "KM-1002": "8.00", "KM-1003": "1250.00"}, "2026-11-20T09:15:00")
    assert current_prices(root, " s 01 ") == current_prices(root, "S01")              # a name that differs in capitals or spaces only
    assert current_prices(root, "S02") == ({}, None) and current_prices(root, "") == ({}, None)
    # another supplier's export leaves the first supplier's prices as they are
    second = articles(tmp_path / "s02.xlsx", [["KM-2001", "Bout", 3.2], ["KM-1001", "Slang", 11.9]])
    assert store_current(root, second, ARTICLE_COLUMNS, True, STORED + timedelta(minutes=5), "S02") == (["S02"], 2, 0)
    assert current_prices(root, "S01")[0]["KM-1001"] == "12.50" and list(held(root)["suppliers"]) == ["S01", "S02"]
    assert current_prices(root, "S02") == ({"KM-2001": "3.2", "KM-1001": "11.9"}, "2026-11-20T09:20:00")
    # a newer export of the first supplier replaces its prices whole: an article it no longer holds is gone
    newer = articles(tmp_path / "nieuw.csv", [["KM-1001", "Slang", "12,70"], ["KM-1004", "Dop", "0,45"]])
    assert store_current(root, newer, ARTICLE_COLUMNS, True, STORED + timedelta(days=1), "s01") == (["S01"], 2, 0)      # the stored name
    assert held(root)["suppliers"]["S01"] == {"stored_at": "2026-11-21T09:15:00", "file": "nieuw.csv",
                                             "prices": {"KM-1001": "12.70", "KM-1004": "0.45"}}
    assert held(root)["suppliers"]["S02"]["stored_at"] == "2026-11-20T09:20:00"
    assert stored_prices(root)["S02"].prices == {"KM-2001": "3.2", "KM-1001": "11.9"} and stored_prices(root)["S01"].file == "nieuw.csv"
    # without a supplier column and without a name nothing is stored
    before = (root / "exact" / ARTICLES_FILE).read_bytes()
    for name in (None, "", "  "):
        with pytest.raises(ValueError, match="supplier"):
            store_current(root, newer, ARTICLE_COLUMNS, True, STORED + timedelta(days=2), name)
    assert (root / "exact" / ARTICLES_FILE).read_bytes() == before


def test_an_export_with_a_supplier_column_is_stored_under_the_suppliers_it_names(tmp_path):
    """Amendment 1: with a supplier column the supplier of a row is the one its cell names, whatever the buyer named; a row whose
    supplier cell is empty is left out and counted; the export replaces the prices of the suppliers it holds and no other's."""
    root = tmp_path / "cases"
    store_current(root, articles(tmp_path / "s02.csv", [["KM-2001", "Bout", "3,20"]]), ARTICLE_COLUMNS, True, STORED, "S02")
    store_current(root, articles(tmp_path / "s03.csv", [["KM-3001", "Moer", "0,10"], ["KM-3002", "Pen", "0,20"]]), ARTICLE_COLUMNS, True, STORED, "S03")
    both = articles(tmp_path / "alle.xlsx", [["KM-1001", "S01", 12.5], ["KM-3001", "s03 ", 0.11], ["KM-1002", "S01", 8], ["KM-9001", None, 1],
                                             ["KM-9002", "  ", 2]], titles=["Artikel", "Leverancier", "Inkoopprijs"])
    assert store_current(root, both, WITH_SUPPLIER, False, STORED + timedelta(hours=1), "S02") == (["S01", "S03"], 3, 2)
    stored = held(root)["suppliers"]
    assert list(stored) == ["S02", "S03", "S01"] and stored["S02"]["prices"] == {"KM-2001": "3.20"}        # S02 as it was, though named
    assert stored["S03"] == {"stored_at": "2026-11-20T10:15:00", "file": "alle.xlsx", "prices": {"KM-3001": "0.11"}}      # whole
    assert stored["S01"]["prices"] == {"KM-1001": "12.5", "KM-1002": "8"}
    assert read_articles(both, WITH_SUPPLIER, False).left_out == {"no_supplier": [7, 8]}
    assert [(row.row, row.supplier, row.item_code, row.price) for row in read_articles(both, WITH_SUPPLIER, False).rows] == [
        (4, "S01", "KM-1001", "12.5"), (5, "s03", "KM-3001", "0.11"), (6, "S01", "KM-1002", "8")]
    # an export of this form that holds no row is refused: it stores nothing and names no supplier (ruling Z11 of the review round)
    before = (root / "exact" / ARTICLES_FILE).read_bytes()
    empty = articles(tmp_path / "leeg.xlsx", [], titles=["Artikel", "Leverancier", "Inkoopprijs"])
    with pytest.raises(NoArticle):
        store_current(root, empty, WITH_SUPPLIER, False, STORED + timedelta(hours=2))
    assert (root / "exact" / ARTICLES_FILE).read_bytes() == before


def test_an_article_twice_or_without_a_price_is_left_out_and_counted(tmp_path):
    """Rule 1: an article listed twice for one supplier, or with a price that is no number, is left out and counted, and so is a row
    without an item code. The same article of two suppliers is no article twice. A number cell of a workbook is the number it holds,
    whatever the convention; a text is read with the convention of the settings."""
    root = tmp_path / "cases"
    rows = [["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", "op aanvraag"], ["KM-1003", "Ring", "8,00"], ["KM-1003", "Ring", "8,10"],
            [None, "Totaal", "28,60"], ["KM-1004", "Dop", None], ["KM-1005", "Kap", "1.000"]]
    export = articles(tmp_path / "artikelen.csv", rows)
    read = read_articles(export, ARTICLE_COLUMNS, True)
    assert [(row.row, row.item_code, row.price, row.text) for row in read.rows] == [(4, "KM-1001", "12.50", "12,50"), (10, "KM-1005", "1000", "1.000")]
    assert read.left_out == {"no_item_code": [8], "no_price": [5, 9], "twice": [6, 7]}
    assert store_current(root, export, ARTICLE_COLUMNS, True, STORED, "S01") == (["S01"], 2, 5)
    assert held(root)["suppliers"]["S01"]["prices"] == {"KM-1001": "12.50", "KM-1005": "1000"}
    assert read_articles(export, ARTICLE_COLUMNS, False).rows[-1].price == "1.000"            # the other convention: one, to three decimals
    # per supplier: the same article of two suppliers is kept for both, and twice for one of them is left out for that one
    named = articles(tmp_path / "alle.csv", [["KM-1001", "S01", "12,50"], ["KM-1001", "S02", "11,90"], ["KM-1001", "s 02", "11,95"]],
                     titles=["Artikel", "Leverancier", "Inkoopprijs"])
    assert store_current(root, named, WITH_SUPPLIER, True, STORED, None) == (["S01"], 1, 2)
    assert read_articles(named, WITH_SUPPLIER, True).left_out == {"twice": [5, 6]}
    # a number cell is its number under either convention: one with three decimals, which the convention of the texts would read
    # as twelve thousand with the decimal comma set (ruling Z2 of the review round); a date or an error cell is no price and no
    # item code
    book = articles(tmp_path / "artikelen.xlsx", [["KM-1001", "Slang", 12.345], ["KM-1002", "Klem", "1,25"], ["KM-1003", "Ring", date(2026, 11, 20)],
                                                    [date(2026, 11, 20), "Datum", 2.5], ["KM-1004", "Dop", "#N/A"], ["KM-1005", "Kap", 1.125]])
    for comma in (True, False):
        read = read_articles(book, ARTICLE_COLUMNS, comma)
        assert [(row.item_code, row.price, row.kind) for row in read.rows] == [("KM-1001", "12.345", "number"), ("KM-1002", "1.25", "text"),
                                                                              ("KM-1005", "1.125", "number")]
        assert read.left_out == {"no_item_code": [7], "no_price": [6, 8]}
    assert normalize_number("12.345", decimal_comma=True) == "12345"                          # what a number cell must never be read as
    # the row of column titles again, lower down, is no row and is counted nowhere
    again = articles(tmp_path / "twee.csv", [["KM-1001", "Slang", "12,50"], TITLES, ["KM-1002", "Klem", "8,00"]])
    assert [row.item_code for row in read_articles(again, ARTICLE_COLUMNS, True).rows] == ["KM-1001", "KM-1002"]
    assert read_articles(again, ARTICLE_COLUMNS, True).left_out == {}


def test_an_export_that_cannot_be_read_is_refused_and_nothing_is_stored(tmp_path):
    """A file of another type, a file without the columns set, a title that stands twice and a map without the item code or the
    price are refused as the export reader refuses a file (ExportRefused, with its reason), before anything is written."""
    root = tmp_path / "cases"
    good = articles(tmp_path / "artikelen.csv", [["KM-1001", "Slang", "12,50"]])
    old = tmp_path / "artikelen.xls"
    old.write_bytes(good.read_bytes())
    other = write_sheet(tmp_path / "anders.csv", [["Code", "Prijs"], ["KM-1001", "12,50"]])
    double = write_sheet(tmp_path / "dubbel.csv", [["Artikel", "Inkoopprijs", "Inkoopprijs"], ["KM-1001", "12,50", "12,60"]])
    for path, columns, reason in ((old, ARTICLE_COLUMNS, "file_type"), (other, ARTICLE_COLUMNS, "no_column"), (double, ARTICLE_COLUMNS, "double_column"),
                                  (good, {"item_code": "Artikel"}, "map"), (good, ARTICLE_COLUMNS | {"basis": "Per"}, "map"),
                                  (good, {"item_code": "Artikel", "price": " "}, "map")):
        with pytest.raises(ExportRefused) as refused:
            store_current(root, path, columns, True, STORED, "S01")
        assert refused.value.reason == reason and "KM-1001" not in str(refused.value) and "12,50" not in str(refused.value)
    assert not root.exists()
    with pytest.raises(OSError):
        store_current(root, tmp_path / "weg.csv", ARTICLE_COLUMNS, True, STORED, "S01")


def test_the_stored_prices_are_written_whole_or_not_at_all(tmp_path, monkeypatch):
    """Amendment 1: the file is written through a temporary file and a replace, so a write that fails leaves the stored prices as they
    were and no temporary file; and a stored file that cannot be read is nothing stored, never an error of current_prices."""
    root = tmp_path / "cases"
    export = articles(tmp_path / "artikelen.csv", [["KM-1001", "Slang", "12,50"]])
    store_current(root, export, ARTICLE_COLUMNS, True, STORED, "S01")
    file = root / ARTICLES_FOLDER / ARTICLES_FILE
    before = file.read_bytes()

    def refuse(source, target):
        raise PermissionError("made-up: the file is held")

    with monkeypatch.context() as patched:
        patched.setattr(os, "replace", refuse)
        with pytest.raises(PermissionError):
            store_current(root, export, ARTICLE_COLUMNS, True, STORED + timedelta(days=1), "S02")
    assert file.read_bytes() == before and [path.name for path in file.parent.iterdir()] == [ARTICLES_FILE]
    for damaged in (b"{\"suppliers\": {\"S01\": {\"stored_at\": \"2026-11-20T0", b"[]", b"\xff\xfe", b"{\"suppliers\": {\"S01\": {\"prices\": 7}}}",
                    b"{\"suppliers\": {\"S01\": {\"stored_at\": \"gisteren\", \"file\": \"a.csv\", \"prices\": {}}}}"):
        file.write_bytes(damaged)
        with pytest.raises(ValueError) as unreadable:
            stored_prices(root)
        assert ARTICLES_FILE in str(unreadable.value) and "gisteren" not in str(unreadable.value)
        assert current_prices(root, "S01") == ({}, None)
    # a new export is stored over a file that cannot be read: what it held is gone, as nothing could be read from it
    assert store_current(root, export, ARTICLE_COLUMNS, True, STORED, "S02") == (["S02"], 1, 0) and list(held(root)["suppliers"]) == ["S02"]
    file.unlink()
    assert stored_prices(root) == {} and stored_prices(tmp_path / "nowhere") == {}


def prepared_list(root: Path, tmp_path: Path, supplier: str, rows: list[tuple], now: datetime) -> ImportResult:
    """A made-up price list of the supplier carried out against its current prices, as the page carries one out: prepared in
    <cases_root>/prepared."""
    listed = write_csv(tmp_path / f"lijst_{supplier}_{now:%H%M%S}.csv", rows)
    return import_price_list(listed, supplier, current_prices(root, supplier)[0], PreparedWriter(root / "prepared"), now)


def test_an_entered_article_price_counts_until_a_newer_export(tmp_path):
    """Amendment 2: the current prices of a supplier are its own stored prices with every article price entered or seen on the list
    after its export was stored laid over them. A change counts for the supplier its case names, one whose case names no supplier for
    every supplier whose stored prices hold the article; a newer export of the supplier is taken as it is."""
    root = tmp_path / "cases"
    prepared = root / "prepared"
    store_current(root, articles(tmp_path / "s01.csv", [["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", "8,00"]]), ARTICLE_COLUMNS, True, STORED, "S01")
    store_current(root, articles(tmp_path / "s02.csv", [["KM-1001", "Slang", "11,90"], ["KM-1002", "Klem", "7,50"]]), ARTICLE_COLUMNS, True, STORED, "S02")
    store_current(root, articles(tmp_path / "s03.csv", [["KM-3001", "Moer", "0,10"]]), ARTICLE_COLUMNS, True, STORED, "S03")
    result = prepared_list(root, tmp_path, "S01", [item("KM-1001", "12.70"), item("KM-1002", "8.40")], STORED + timedelta(minutes=10))
    assert [entry.case_id for entry in result.updated] == ["pricelist_S01_20261120T092500"] * 2
    assert current_prices(root, "S01")[0] == {"KM-1001": "12.50", "KM-1002": "8.00"}          # prepared and not entered: Exact holds the old price
    [first, second] = open_changes(prepared)
    assert mark(prepared, first.ids, "entered", STORED + timedelta(minutes=20)) == 1
    assert current_prices(root, "S01") == ({"KM-1001": "12.70", "KM-1002": "8.00"}, "2026-11-20T09:15:00")
    assert current_prices(root, "S02")[0]["KM-1001"] == "11.90"                               # the change's case names S01, not S02
    assert mark(prepared, second.ids, "dropped", STORED + timedelta(minutes=21)) == 1          # a dropped change was never entered
    assert current_prices(root, "S01")[0]["KM-1002"] == "8.00"
    # seen counts as entered
    seen = prepared_list(root, tmp_path, "S02", [item("KM-1002", "7.80")], STORED + timedelta(minutes=30))
    assert current_prices(root, "S02")[0]["KM-1002"] == "7.50"
    assert mark(prepared, [seen.updated[0].entry_id], "seen", STORED + timedelta(minutes=31), by="export") == 1
    assert current_prices(root, "S02")[0] == {"KM-1001": "11.90", "KM-1002": "7.80"} and current_prices(root, "S01")[0]["KM-1002"] == "8.00"
    # a change whose case names no supplier counts for every supplier whose stored prices hold the article, and for no other
    nameless = PreparedWriter(prepared).apply(WriteOp(kind="article_price", po_number="", version=None, article_code="KM-1001", new_value="13.10"),
                                              "onbekend_20261120T100000", "R04", {}, STORED + timedelta(minutes=45))
    assert mark(prepared, [nameless.entry_id], "entered", STORED + timedelta(minutes=46)) == 1
    assert current_prices(root, "S01")[0]["KM-1001"] == "13.10" and current_prices(root, "S02")[0]["KM-1001"] == "13.10"
    assert current_prices(root, "S03")[0] == {"KM-3001": "0.10"}
    # a newer export of the supplier is taken as it is: what was entered before it was stored no longer counts
    newer = articles(tmp_path / "s01_nieuw.csv", [["KM-1001", "Slang", "13,00"], ["KM-1002", "Klem", "8,00"]])
    store_current(root, newer, ARTICLE_COLUMNS, True, STORED + timedelta(hours=2), "S01")
    assert current_prices(root, "S01") == ({"KM-1001": "13.00", "KM-1002": "8.00"}, "2026-11-20T11:15:00")
    assert current_prices(root, "S02")[0]["KM-1001"] == "13.10"                               # S02's own export is older than the mark


def test_a_mark_in_the_second_of_the_export_does_not_count_and_a_put_back_gives_the_old_price_again(tmp_path):
    """Amendment 2, the one judgement of the marks: the value of an article is the new value of its last change that is entered or
    seen, at the time of that mark. A mark made in the second the export was stored, or before it, does not count: the export is the
    newer look. A put-back that is entered puts the old price back, and one that is still open leaves the entered price."""
    root = tmp_path / "cases"
    prepared = root / "prepared"
    export = articles(tmp_path / "s01.csv", [["KM-1001", "Slang", "12,50"]])
    store_current(root, export, ARTICLE_COLUMNS, True, STORED, "S01")
    result = prepared_list(root, tmp_path, "S01", [item("KM-1001", "12.70")], STORED - timedelta(minutes=5))
    [entry] = result.updated
    assert mark(prepared, [entry.entry_id], "entered", STORED) == 1                             # in the second of the export
    assert current_prices(root, "S01")[0] == {"KM-1001": "12.50"}
    assert mark(prepared, [entry.entry_id], "reopened", STORED + timedelta(seconds=1)) == 1
    assert mark(prepared, [entry.entry_id], "entered", STORED + timedelta(seconds=2)) == 1
    assert current_prices(root, "S01")[0] == {"KM-1001": "12.70"}
    # the put-back, as "Terugdraaien in Exact" prepares it from the price list's own write log
    folder = root / "pricelists" / "2026-11-20"
    folder.mkdir(parents=True)
    WriteLog(folder).append(entry)
    undo = undo_entry(folder, entry.entry_id, PreparedWriter(prepared), STORED + timedelta(minutes=1))
    link_put_back(prepared, undo.entry_id, entry.entry_id, STORED + timedelta(minutes=1))
    assert current_prices(root, "S01")[0] == {"KM-1001": "12.70"}                               # the put-back is still open
    assert mark(prepared, [undo.entry_id], "entered", STORED + timedelta(minutes=2)) == 1
    assert current_prices(root, "S01")[0] == {"KM-1001": "12.50"}


def test_a_put_back_to_a_price_the_aid_never_read_gives_the_export_s_price_again(tmp_path):
    """An article price that a confirmation asked has no price before it that the aid knows. Entered, it is the article's price; its
    undo, entered in turn, puts back a price the aid never read: the article then has the export's price again, and an article that
    the export does not hold has none."""
    root = tmp_path / "cases"
    prepared = root / "prepared"
    store_current(root, articles(tmp_path / "s01.csv", [["KM-1001", "Slang", "12,50"]]), ARTICLE_COLUMNS, True, STORED, "S01")
    writer, case_id = PreparedWriter(prepared), "onbekend_20261120T093000"                 # a case without a folder: it names no supplier
    for minute, code in enumerate(("KM-1001", "KM-7777")):
        asked = writer.apply(WriteOp(kind="article_price", po_number="", version=None, article_code=code, new_value="12.70"), case_id, "R04", {},
                             STORED + timedelta(minutes=10 + minute))
        assert mark(prepared, [asked.entry_id], "entered", STORED + timedelta(minutes=20 + minute)) == 1
    assert current_prices(root, "S01")[0] == {"KM-1001": "12.70"}                           # KM-7777 is no article of this supplier's export
    undo = writer.apply(WriteOp(kind="article_price", po_number="", version=None, article_code="KM-1001", old_value="12.70"), case_id, "undo",
                        {}, STORED + timedelta(minutes=30))
    assert current_prices(root, "S01")[0] == {"KM-1001": "12.70"}                           # the undo is still to enter
    assert mark(prepared, [undo.entry_id], "entered", STORED + timedelta(minutes=31)) == 1
    assert current_prices(root, "S01")[0] == {"KM-1001": "12.50"}
    # a change whose case names the supplier counts for an article that its export does not hold; put back, the article has no price
    own = "pricelist_S01_20261120T100000"
    asked = writer.apply(WriteOp(kind="article_price", po_number="", version=None, article_code="KM-8888", new_value="5.00"), own, "P01", {},
                         STORED + timedelta(minutes=45))
    assert mark(prepared, [asked.entry_id], "entered", STORED + timedelta(minutes=46)) == 1
    assert current_prices(root, "S01")[0] == {"KM-1001": "12.50", "KM-8888": "5.00"}
    back = writer.apply(WriteOp(kind="article_price", po_number="", version=None, article_code="KM-8888", old_value="5.00"), own, "undo", {},
                        STORED + timedelta(minutes=50))
    assert mark(prepared, [back.entry_id], "entered", STORED + timedelta(minutes=51)) == 1
    assert current_prices(root, "S01")[0] == {"KM-1001": "12.50"}


def test_a_list_is_known_by_its_case_id_and_a_name_by_its_stored_spelling():
    assert list_of("pricelist_S01_20261120T092500") == ("S01", datetime(2026, 11, 20, 9, 25))
    assert list_of("pricelist_Voorbeeld B.V._20261120T092500") == ("Voorbeeld B.V.", datetime(2026, 11, 20, 9, 25))
    assert list_of("pricelist_a_b_20261120T092500")[0] == "a_b"
    for other in ("250108_20261120T092500", "pricelist_S01", "pricelist_S01_2026", "unknown_20261120T092500", "pricelist__20261120T092500",
                  "pricelist_S01_20261340T092500"):
        assert list_of(other) is None, other
    assert supplier_named(" s 01 ", ["S02", "S 01"]) == "S 01" and supplier_named("  Nieuw  BV ", ["S01"]) == "Nieuw  BV"
    assert supplier_named("", ["S01"]) == "" and supplier_named(None, ["S01"]) == ""


def test_a_row_that_joins_an_entered_change_is_not_ticked_for_its_own_supplier(tmp_path):
    """Amendment 2: a change counts for the supplier its case names, and the cases that decide are those of the rows that were ticked.
    The list holds one row per article and value, whichever supplier asked: when a second supplier's list asks the value that is
    entered already for the first, its row joins that change, and the price is not the second supplier's by it."""
    root = tmp_path / "cases"
    prepared = root / "prepared"
    for supplier, price in (("S01", "12,50"), ("S02", "11,90")):
        store_current(root, articles(tmp_path / f"{supplier}.csv", [["KM-1001", "Slang", price]]), ARTICLE_COLUMNS, True, STORED, supplier)
    [first] = prepared_list(root, tmp_path, "S01", [item("KM-1001", "12.70")], STORED + timedelta(minutes=10)).updated
    assert mark(prepared, [first.entry_id], "entered", STORED + timedelta(minutes=20)) == 1
    [second] = prepared_list(root, tmp_path, "S02", [item("KM-1001", "12.70")], STORED + timedelta(minutes=30)).updated
    assert open_changes(prepared) == [] and mark(prepared, [second.entry_id], "entered", STORED + timedelta(minutes=31)) == 0
    assert current_prices(root, "S01")[0] == {"KM-1001": "12.70"} and current_prices(root, "S02")[0] == {"KM-1001": "11.90"}
    # another value for the second supplier is a change of its own, and counts for that supplier once it is ticked
    [third] = prepared_list(root, tmp_path, "S02", [item("KM-1001", "12.10")], STORED + timedelta(minutes=40)).updated
    assert mark(prepared, [third.entry_id], "entered", STORED + timedelta(minutes=41)) == 1
    assert current_prices(root, "S02")[0] == {"KM-1001": "12.10"} and current_prices(root, "S01")[0] == {"KM-1001": "12.70"}


# --- the review round of the price list on the page (rulings Z1, Z3, Z7 and Z11) ---

def workbook_of(path: Path, sheets: list[tuple[str, list[list], str]]) -> Path:
    """A made-up workbook with these sheets in this order: (the name of the sheet, its rows, "visible", "hidden" or "veryHidden"),
    each with a wrong dimension record (ruling Z24: the sheet the buyer sees is read in full whatever that record says)."""
    book = Workbook()
    book.remove(book.active)
    for title, rows, state in sheets:
        sheet = book.create_sheet(title)
        sheet.sheet_state = state
        for row in rows:
            sheet.append(row)
    book.save(path)
    return wrong_dimensions(path)


def test_an_export_is_read_from_the_one_sheet_the_buyer_sees(tmp_path):
    """Ruling Z1: of an export of articles a hidden sheet is never read; the one visible sheet that holds data is read, whatever its
    place; a workbook with more than one such sheet is refused with their names, which the text of the error does not hold, and
    nothing is stored."""
    root = tmp_path / "cases"
    hidden_first = workbook_of(tmp_path / "artikelen.xlsx", [("2025 (oud)", [TITLES, ["KM-1001", "Slang", 9.99]], "hidden"), ("Blad2", [], "visible"),
                                                             ("Artikelen", [["Verzonnen"], TITLES, ["KM-1001", "Slang", 12.5]], "visible"),
                                                             ("Diep", [TITLES, ["KM-1001", "Slang", 1.11]], "veryHidden")])
    assert [(row.row, row.item_code, row.price) for row in read_articles(hidden_first, ARTICLE_COLUMNS).rows] == [(3, "KM-1001", "12.5")]
    assert store_current(root, hidden_first, ARTICLE_COLUMNS, False, STORED, "S01") == (["S01"], 1, 0)
    before = (root / ARTICLES_FOLDER / ARTICLES_FILE).read_bytes()
    two = workbook_of(tmp_path / "twee.xlsx", [("Slangen", [TITLES, ["KM-1001", "Slang", 12.7]], "visible"),
                                               ("Klemmen", [TITLES, ["KM-1002", "Klem", 8.4]], "visible")])
    for read in (lambda: read_articles(two, ARTICLE_COLUMNS), lambda: store_current(root, two, ARTICLE_COLUMNS, False, STORED, "S01")):
        with pytest.raises(ExportRefused) as several:           # the export reader's refusal, as for the order lines (ruling Z17)
            read()
        assert (several.value.reason, several.value.names) == ("several_sheets", ["Slangen", "Klemmen"])
        assert "Slangen" not in str(several.value) and "Klemmen" not in str(several.value)
    assert (root / ARTICLES_FOLDER / ARTICLES_FILE).read_bytes() == before
    only_hidden = workbook_of(tmp_path / "verborgen.xlsx", [("oud", [TITLES, ["KM-1001", "Slang", 9.99]], "hidden"), ("leeg", [], "visible")])
    with pytest.raises(ExportRefused) as nothing:                                     # only a hidden sheet holds data (ruling Z33)
        read_articles(only_hidden, ARTICLE_COLUMNS)
    assert nothing.value.reason == "hidden_sheet"


def test_an_export_without_an_article_is_refused_and_leaves_what_was_stored(tmp_path):
    """Ruling Z11: an export of the articles that gives no article stores nothing and leaves what was stored, with a supplier
    column and without one: an export read with the wrong columns never empties a supplier's prices."""
    root = tmp_path / "cases"
    nothing = articles(tmp_path / "niets.csv", [["KM-1001", "Slang", "op aanvraag"], [None, "Totaal", "12,50"]])
    with pytest.raises(NoArticle) as refused:
        store_current(root, nothing, ARTICLE_COLUMNS, True, STORED, "S01")
    assert not root.exists() and "KM-1001" not in str(refused.value) and "op aanvraag" not in str(refused.value)
    store_current(root, articles(tmp_path / "s01.csv", [["KM-1001", "Slang", "12,50"]]), ARTICLE_COLUMNS, True, STORED, "S01")
    before = (root / ARTICLES_FOLDER / ARTICLES_FILE).read_bytes()
    named = articles(tmp_path / "alle.csv", [["KM-1001", "", "12,50"], ["KM-1002", "S02", "geen"]], titles=["Artikel", "Leverancier", "Inkoopprijs"])
    for export, columns, supplier in ((nothing, ARTICLE_COLUMNS, "S01"), (nothing, ARTICLE_COLUMNS, "S02"), (named, WITH_SUPPLIER, None),
                                      (articles(tmp_path / "leeg.csv", []), ARTICLE_COLUMNS, "S01")):
        with pytest.raises(NoArticle):
            store_current(root, export, columns, True, STORED + timedelta(days=1), supplier)
    assert (root / ARTICLES_FOLDER / ARTICLES_FILE).read_bytes() == before and current_prices(root, "S01")[0] == {"KM-1001": "12.50"}


def test_a_suppliers_name_that_is_no_name_stores_nothing(tmp_path):
    """Ruling Z7: a supplier's name longer than a hundred characters, or with a line break or a control character, is no name: an
    export given for it, or that names it in a row, stores nothing, and the error never holds the name."""
    root = tmp_path / "cases"
    export = articles(tmp_path / "artikelen.csv", [["KM-1001", "Slang", "12,50"]])
    assert SUPPLIER_NAME_MAX == 100
    assert all(is_supplier_name(name) for name in ("S01", "Hansen A/S", "Jansen\\Zonen", "../../buiten", "L" * 100, f"  {'L' * 100}  ", "", None))
    for bad in ("L" * 101, "Jansen\nen Zonen", "a\rb", "a\tb", "a\x00b", "a\x7fb", "a\u2028b", "a\u2029b", "a\x85b"):
        assert not is_supplier_name(bad), repr(bad)
        with pytest.raises(NameRefused) as refused:
            store_current(root, export, ARTICLE_COLUMNS, True, STORED, bad)
        assert bad not in str(refused.value)
        named = articles(tmp_path / "alle.csv", [["KM-1001", "S01", "12,50"], ["KM-1002", bad, "8,00"]], titles=["Artikel", "Leverancier", "Inkoopprijs"])
        if "\x00" not in bad:                                                          # a CSV file holds no NUL that the reader takes
            with pytest.raises(NameRefused):
                store_current(root, named, WITH_SUPPLIER, True, STORED)
    assert not root.exists()
    assert store_current(root, export, ARTICLE_COLUMNS, True, STORED, "L" * 100) == (["L" * 100], 1, 0)


def test_the_articles_that_another_supplier_holds_too(tmp_path):
    """Ruling Z3: also_held gives the articles that the stored prices of another supplier hold, by the stored names without regard
    to capitals and spaces; a supplier's own articles are never among them. Ruling Z19 (a): each with the names of those suppliers,
    as they are stored and in the order they were first stored."""
    root = tmp_path / "cases"
    for supplier, rows in (("S01", [["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", "8,00"]]), ("S 02", [["KM-1001", "Slang", "11,90"]]),
                           ("S03", [["KM-3001", "Moer", "0,10"], ["KM-1002", "Klem", "7,90"]])):
        store_current(root, articles(tmp_path / f"{supplier}.csv", rows), ARTICLE_COLUMNS, True, STORED, supplier)
    held = stored_prices(root)
    assert also_held(held, "S01") == {"KM-1001": ["S 02"], "KM-3001": ["S03"], "KM-1002": ["S03"]}
    assert also_held(held, " s02 ") == {"KM-1001": ["S01"], "KM-1002": ["S01", "S03"], "KM-3001": ["S03"]}
    assert also_held(held, "S03") == {"KM-1001": ["S01", "S 02"], "KM-1002": ["S01"]}
    assert also_held(held, "S04") == {"KM-1001": ["S01", "S 02"], "KM-1002": ["S01", "S03"], "KM-3001": ["S03"]}
    assert also_held({"S01": held["S01"]}, "s 01") == {} and also_held({}, "S01") == {}


def test_the_callers_check_holds_a_row_back_with_its_price(tmp_path):
    """The import takes a check of its caller for a price that would change (the tab's, rulings Z3 and Z6): a row it holds back is
    skipped with the check's reason and its list price is kept; the check is asked for no row that is unchanged, a duplicate or not
    in force, and without a check the import decides as before."""
    asked = []

    def check(code, old, new):
        asked.append((code, old, new))
        return "held back" if code == "KM-1002" else None
    rows = [item("KM-1001", "12.70"), item("KM-1002", "8.40"), item("KM-1002", "8.60"), item("KM-1003", "5.00"), item("KM-1004", "3.50", basis="100"),
            item("KM-9999", "1.00")]
    listed = write_csv(tmp_path / "lijst.csv", rows)
    result = import_price_list(listed, "S01", CURRENT, PreparedWriter(tmp_path / "prepared"), NOW, check=check)
    assert asked == [("KM-1001", "12.50", "12.70"), ("KM-1002", "8.00", "8.40")]
    assert result.skipped == [("KM-1002", "held back"), ("KM-1002", "duplicate row"), ("KM-1003", "unchanged"), ("KM-1004", "unknown price basis"),
                              ("KM-9999", "unknown article")]
    assert result.held == {"KM-1002": "8.40"} and [entry.op.article_code for entry in result.updated] == ["KM-1001"]
    assert len(prepared_rows(tmp_path / "prepared")) == 1
    plain = import_price_list(listed, "S01", CURRENT, PreparedWriter(tmp_path / "plain"), NOW)
    assert [entry.op.article_code for entry in plain.updated] == ["KM-1001", "KM-1002"] and plain.held == {}
    # the rows of the file as the caller read them: the file itself is then not read
    table = [["item_code", "price"], ["KM-1001", "12,90"]]
    given = import_price_list(tmp_path / "nergens.csv", "S01", CURRENT, PreparedWriter(tmp_path / "given"), NOW, table=table)
    assert [(entry.new, entry.evidence["file"]) for entry in given.updated] == [("12.90", "nergens.csv")]


# --- the second review round of the price list on the page (rulings Z16, Z17 and Z26) ---

C16 = "KM-1001,Slang,12,70\nKM-1002,Klem,8,40\n"       # the check's case c16: decimal commas without quotation marks in a comma file


def test_a_csv_row_with_a_filled_cell_beyond_the_titles_refuses_the_list_and_the_export(tmp_path, capsys):
    """Ruling Z16, as ruling Z30 amends it for a file cut at commas: a CSV row with more cells than its titles is never read: 12,70
    was read as 12. The file is refused whole, with the row named and no cell of it, by the command and by the export of articles;
    nothing is prepared or stored. A quoted comma and trailing separators on every row are read as before."""
    listed = tmp_path / "c16.csv"
    listed.write_text("item_code,description,price\n" + C16, encoding="utf-8")
    with pytest.raises(ValueError, match=r"^c16\.csv, row 2: another number of cells than the row of column titles$") as refused:
        import_price_list(listed, "S01", CURRENT, PreparedWriter(tmp_path / "prepared"), NOW)
    assert not (tmp_path / "prepared").exists() and "Slang" not in str(refused.value)
    assert main(command(tmp_path, listed), now=NOW) == 1
    out, err = capsys.readouterr()
    assert out == "" and err == "cannot read: c16.csv, row 2: another number of cells than the row of column titles\n"
    assert not (tmp_path / "prepared").exists()
    current = tmp_path / "prijzen.csv"                         # the command's file of current prices is a CSV table too
    current.write_text("item_code,price\nKM-1001,12,50\n", encoding="utf-8")
    assert main(command(tmp_path, write_csv(tmp_path / "goed.csv", [item("KM-1001")]), current), now=NOW) == 1
    assert capsys.readouterr().err == "cannot read: prijzen.csv, row 2: another number of cells than the row of column titles\n"
    quoted = tmp_path / "quoted.csv"
    quoted.write_text('item_code,description,price,,\nKM-1001,"Slang, 2 m","12,70",,\nKM-1002,Klem,"8,00", ,\n', encoding="utf-8")
    result = import_price_list(quoted, "S01", CURRENT, PreparedWriter(tmp_path / "quoted"), NOW)
    assert [(entry.op.article_code, entry.new) for entry in result.updated] == [("KM-1001", "12.70")] and result.skipped == [("KM-1002", "unchanged")]
    # the export of articles: the same rows stored 12 and 8 before
    root = tmp_path / "cases"
    export = tmp_path / "artikelen.csv"
    export.write_text("Artikelen (verzonnen voorbeeld),,,,\n\nArtikel,Omschrijving,Inkoopprijs\n" + C16, encoding="utf-8")
    with pytest.raises(ExportRefused) as surplus:
        read_articles(export, ARTICLE_COLUMNS, True)
    assert (surplus.value.reason, surplus.value.row, surplus.value.column) == ("cell_count", 4, None)
    assert str(surplus.value) == "artikelen.csv, row 4: another number of cells than the row of column titles"
    with pytest.raises(ExportRefused):
        store_current(root, export, ARTICLE_COLUMNS, True, STORED, "S02")
    assert not root.exists()
    fine = tmp_path / "goed.csv"
    fine.write_text('Artikel,Omschrijving,Inkoopprijs,,\nKM-1001,"Slang, 2 m","12,70",,\nKM-1002,Klem,8.40, ,\n', encoding="utf-8")
    assert [(row.item_code, row.price) for row in read_articles(fine, ARTICLE_COLUMNS, True).rows] == [("KM-1001", "12.70"), ("KM-1002", "8.40")]


def test_the_row_with_a_filled_cell_beyond_the_last_title():
    """Ruling Z16, the one helper every CSV table goes through: the number, counted from 1, of the first row below the row of column
    titles that holds a filled cell beyond the last column title; none when only empty cells stand there (trailing separators).
    Ruling Z30: in a table cut at commas, the first filled row that has another number of cells than the row of titles, whatever
    its cells hold; a blank row is no row."""
    from aid.pricelist import SURPLUS, UNEVEN, odd_row
    assert odd_row([["a", "b"], ["1", "2"], ["1", "2", ""], ["1", "2", " ", ""]], 0) is None
    assert odd_row([["a", "b", "", ""], ["1", "2", "", ""], ["1", "2", "x"]], 0) == (3, SURPLUS)        # beyond the last title, not the last cell
    assert odd_row([["Kop", "van", "drie", "cellen"], [], ["a", "b"], ["1", "2"], ["1", "2", "3"], ["1", "2", "3"]], 2) == (5, SURPLUS)
    assert odd_row([["a", "b"]], 0) is None and odd_row([["a", "b"], ["1"], []], 0) is None
    assert odd_row([[("a",), ("b",)], [("1",), ("2",), ("3",)]], 0, text=lambda cell: cell[0]) == (2, SURPLUS)     # cells of another kind
    assert (SURPLUS, UNEVEN) == ("a filled cell beyond the last column title", "another number of cells than the row of column titles")
    # cut at commas: every filled row has the titles' number of cells, empty ones counted
    assert odd_row([["a", "b"], ["1", "2"], ["1", "2", ""], ["1", "2", " ", ""]], 0, commas=True) == (3, UNEVEN)
    assert odd_row([["a", "b", "c", "d"], ["1", "12", "70", "", ""]], 0, commas=True) == (2, UNEVEN)       # the check's c16b: room at the end
    assert odd_row([["a", "b", "c", "d"], ["1", "12", "70"], ["2", "8", "40"]], 0, commas=True) == (2, UNEVEN)     # shorter than the titles
    assert odd_row([["a", "b", "", ""], ["1", "2", "", ""], [], ["", "  "], ["1", "2", "", ""]], 0, commas=True) is None
    assert odd_row([["a", "b", "", ""], ["1", "2", "x", ""]], 0, commas=True) == (2, SURPLUS)             # the same number, a cell without a title
    assert odd_row([["Kop"], [], ["a", "b"], ["1", "2"], ["1"]], 2, commas=True) == (5, UNEVEN)            # the rows above the titles are not read


def test_a_name_that_is_no_name_in_a_row_of_the_export_is_refused_with_its_row(tmp_path):
    """Ruling Z26: with a supplier column, a kept row whose supplier cell holds a name that is no name refuses the export with the
    row of that cell, so that the buyer knows where to look; a name the buyer gave himself has no row. A row that is left out
    anyway (no price) does not refuse the export."""
    root = tmp_path / "cases"
    titles = ["Artikel", "Leverancier", "Inkoopprijs"]
    export = articles(tmp_path / "alle.xlsx", [["KM-1001", "S01", 12.5], ["KM-2001", "S02", 3.2], ["KM-9001", "L" * 101, None],
                                               ["KM-3001", "S03 BV\nPostbus 1", 1.5], ["KM-3002", "x" * 300, 2]], titles=titles)
    with pytest.raises(NameRefused) as refused:
        store_current(root, export, WITH_SUPPLIER, False, STORED)
    assert refused.value.row == 7 and "S03" not in str(refused.value) and "Postbus" not in str(refused.value)
    assert not root.exists()
    with pytest.raises(NameRefused) as typed:
        store_current(root, articles(tmp_path / "s01.csv", [["KM-1001", "Slang", "12,50"]]), ARTICLE_COLUMNS, True, STORED, "a\nb")
    assert typed.value.row is None and not root.exists()



def test_a_suppliers_stored_prices_and_kept_columns_are_removed_whole_or_not_at_all(tmp_path, monkeypatch):
    """Ruling Z19 (b): remove_stored takes away the stored prices of one supplier and the columns kept for its price list, by its
    stored name whatever the capitals and spaces of the name given, and nothing else; a name that is not stored removes nothing; and
    a write that fails in the middle leaves both files as they were."""
    import aid.pricelist as module
    from aid.pricelist import COLUMNS_FILE, keep_columns, kept_columns, remove_stored
    root = tmp_path / "cases"
    rows = [["KM-1001", "Slang", "12,50"], ["KM-1002", "Klem", "8,00"]]
    for supplier in ("S01 B.V.", "S01 BV", "S02"):
        store_current(root, articles(tmp_path / "artikelen.csv", rows), ARTICLE_COLUMNS, True, STORED, supplier)
        keep_columns(root, supplier, {"item_code": "Artikel", "price": f"Prijs {supplier}"})
    files = [root / ARTICLES_FOLDER / ARTICLES_FILE, root / ARTICLES_FOLDER / COLUMNS_FILE]
    before = [file.read_bytes() for file in files]
    assert remove_stored(root, "S03") is None and remove_stored(root, "") is None and [file.read_bytes() for file in files] == before
    for failing in (1, 2):                                  # the first write fails, then the second: nothing is half done
        calls, replace = [], module.os.replace

        def held(source, target, failing=failing, calls=calls, replace=replace):
            calls.append(Path(target).name)
            if len(calls) == failing:
                raise PermissionError(13, "made-up: the file is held")
            return replace(source, target)
        with monkeypatch.context() as patched:
            patched.setattr(module.os, "replace", held)
            with pytest.raises(PermissionError):
                remove_stored(root, "S01 B.V.")
        assert [file.read_bytes() for file in files] == before, failing
        assert sorted(path.name for path in (root / ARTICLES_FOLDER).iterdir()) == sorted([ARTICLES_FILE, COLUMNS_FILE])       # no temporary file left
    assert remove_stored(root, " s01 b.v. ") == "S01 B.V."
    assert list(stored_prices(root)) == ["S01 BV", "S02"] and list(kept_columns(root)) == ["S01 BV", "S02"]
    assert stored_prices(root)["S01 BV"].prices == {"KM-1001": "12.50", "KM-1002": "8.00"}
    assert kept_columns(root)["S02"] == {"item_code": "Artikel", "price": "Prijs S02"}
    assert remove_stored(root, "S01 B.V.") is None                                 # not held any more: nothing to remove
    # a supplier without kept columns: its prices are removed and the columns file is not written
    columns_before = files[1].read_bytes()
    store_current(root, articles(tmp_path / "artikelen.csv", rows), ARTICLE_COLUMNS, True, STORED, "S04")
    assert remove_stored(root, "S04") == "S04" and files[1].read_bytes() == columns_before and list(stored_prices(root)) == ["S01 BV", "S02"]
    # stored prices that cannot be read as such hold no supplier to remove
    files[0].write_text("geen JSON", encoding="utf-8")
    assert remove_stored(root, "S02") is None and files[1].read_bytes() == columns_before


# --- the third review round (rulings Z30, Z32, Z33 and Z34) ---

def test_in_a_comma_file_a_row_is_read_only_with_the_titles_number_of_cells(tmp_path, capsys):
    """Ruling Z30, the check's c16b files: a row cut by a decimal comma without quotation marks was still read as 12 when its last
    cells were empty or the row was shorter than the titles. In a file cut at commas every filled row has exactly the titles'
    number of cells, or the file is refused whole with the row; for the command's list and the export of articles alike. A comma
    file whose rows all have that number, with empty last cells, and a semicolon file with short rows are read as before."""
    root = tmp_path / "cases"
    for name, rows in (("ruimte.csv", "KM-1001,12,70,,\r\nKM-1002,8,40,,\r\n"), ("kort.csv", "KM-1001,12,70\nKM-1002,8,40\n")):
        listed = tmp_path / name
        listed.write_bytes(("item_code,price,min_quantity,valid_to\n" + rows).encode("utf-8"))
        with pytest.raises(ValueError, match=rf"^{name[:-4]}\.csv, row 2: another number of cells than the row of column titles$"):
            import_price_list(listed, "S01", CURRENT, PreparedWriter(tmp_path / "prepared"), NOW)
        assert main(command(tmp_path, listed), now=NOW) == 1
        assert capsys.readouterr().err == f"cannot read: {name}, row 2: another number of cells than the row of column titles\n"
        assert not (tmp_path / "prepared").exists()
    for name, rows in (("ruimte export.csv", "KM-1001,Slang,12,70,\nKM-1002,Klem,8,40,\n"), ("kort export.csv", "KM-1001,Slang,12\nKM-1002,Klem,8,40\n")):
        export = tmp_path / name
        export.write_text("Artikel,Omschrijving,Inkoopprijs,Opmerking\n" + rows, encoding="utf-8")
        with pytest.raises(ExportRefused) as refused:
            read_articles(export, ARTICLE_COLUMNS, True)
        assert (refused.value.reason, refused.value.row) == ("cell_count", 2) and "12" not in str(refused.value)
        with pytest.raises(ExportRefused):
            store_current(root, export, ARTICLE_COLUMNS, True, STORED, "S01")
    assert not root.exists()
    # as before: the titles' number of cells on every row, the last cells empty; and a blank row between
    even = tmp_path / "even.csv"
    even.write_text('item_code,price,min_quantity,valid_to\nKM-1001,"12,70",,\n,,,\n\nKM-1002,8.40,,\n', encoding="utf-8")
    result = import_price_list(even, "S01", CURRENT, PreparedWriter(tmp_path / "even"), NOW)
    assert [(entry.op.article_code, entry.new) for entry in result.updated] == [("KM-1001", "12.70"), ("KM-1002", "8.40")]
    export = tmp_path / "even export.csv"
    export.write_text('Artikel,Omschrijving,Inkoopprijs,Opmerking\nKM-1001,Slang,"12,70",\nKM-1002,Klem,8.40,\n', encoding="utf-8")
    assert [(row.item_code, row.price) for row in read_articles(export, ARTICLE_COLUMNS, True).rows] == [("KM-1001", "12.70"), ("KM-1002", "8.40")]
    # as before: a semicolon file with short rows and with trailing separators on some rows (a decimal comma cannot cut such a row)
    short = tmp_path / "puntkomma.csv"
    short.write_text("item_code;price;min_quantity;valid_to\nKM-1001;12,70\nKM-1002;8,40;;;;\n", encoding="utf-8")
    result = import_price_list(short, "S01", CURRENT, PreparedWriter(tmp_path / "short"), NOW)
    assert [(entry.op.article_code, entry.new) for entry in result.updated] == [("KM-1001", "12.70"), ("KM-1002", "8.40")]
    export = tmp_path / "puntkomma export.csv"
    export.write_text("Artikel;Omschrijving;Inkoopprijs;Opmerking\nKM-1001;Slang;12,70\nKM-1002;Klem;8,40;;;\n", encoding="utf-8")
    assert [(row.item_code, row.price) for row in read_articles(export, ARTICLE_COLUMNS, True).rows] == [("KM-1001", "12.70"), ("KM-1002", "8.40")]


def test_the_command_names_a_missing_column_before_it_looks_at_the_rows(tmp_path, capsys):
    """Ruling Z32: the command looks up its columns first and checks the rows only when the title row holds them, so that a list cut
    at tabs, or one with a heading above its titles, is refused with the missing column named, as before the second round."""
    tabs = tmp_path / "l_tab.csv"
    tabs.write_text("item_code\tprice\tbasis\tunit\nKM-1001\t12,70\t1\tst\n", encoding="utf-8")
    assert main(command(tmp_path, tabs), now=NOW) == 1
    assert capsys.readouterr().err == ("cannot read: l_tab.csv has no column 'item_code' for item_code (its columns: "
                                       "item_code\tprice\tbasis\tunit)\n")
    heading = tmp_path / "l_kop.csv"
    heading.write_text("Overzicht (verzonnen);;;\nitem_code;price;basis;unit\nKM-1001;12,70;1;st\n", encoding="utf-8")
    assert main(command(tmp_path, heading), now=NOW) == 1
    assert capsys.readouterr().err == "cannot read: l_kop.csv has no column 'item_code' for item_code (its columns: Overzicht (verzonnen))\n"
    assert not (tmp_path / "prepared").exists()


def test_a_workbook_with_data_on_a_hidden_sheet_only_says_so(tmp_path, capsys):
    """Ruling Z33: a workbook whose only data stands on a hidden sheet is refused in words that say so, by the export of articles
    (the export reader's reason hidden_sheet) and by the command; it was "no column", or an empty file. A workbook without any
    data stays what it was."""
    only_hidden = workbook_of(tmp_path / "verborgen.xlsx", [("oud", [TITLES, ["KM-1001", "Slang", 9.99]], "hidden"), ("leeg", [], "visible"),
                                                           ("diep", [["x"]], "veryHidden")])
    with pytest.raises(ExportRefused, match=r"^verborgen\.xlsx holds data on a hidden sheet only$") as refused:
        read_articles(only_hidden, ARTICLE_COLUMNS)
    assert refused.value.reason == "hidden_sheet" and "oud" not in str(refused.value)
    with pytest.raises(ExportRefused):
        store_current(tmp_path / "cases", only_hidden, ARTICLE_COLUMNS, False, STORED, "S01")
    assert not (tmp_path / "cases").exists()
    listed = workbook_of(tmp_path / "lijst.xlsx", [("2025", [["item_code", "price"], ["KM-1001", 12.7]], "hidden"), ("Blad2", [[None, "  "]], "visible")])
    assert main(command(tmp_path, listed), now=NOW) == 1
    assert capsys.readouterr().err == "cannot read: lijst.xlsx holds data on a hidden sheet only; the aid reads no hidden sheet\n"
    empty = workbook_of(tmp_path / "leeg.xlsx", [("Blad1", [], "visible"), ("Blad2", [], "hidden")])
    with pytest.raises(ExportRefused) as nothing:
        read_articles(empty, ARTICLE_COLUMNS)
    assert nothing.value.reason == "no_column"


def test_a_workbook_is_read_as_its_one_sheet_whatever_stands_beside_its_table(tmp_path):
    """Ruling Z34, for the export of articles: a note far to the right of the table and a formula in a column without a title are
    no surplus cells (a workbook's cells are never cut wrongly), and a second visible sheet that holds only formatted empty cells
    is no sheet with data: each such workbook is read as its one sheet."""
    from openpyxl.styles import Font
    wide = tmp_path / "breed.xlsx"
    book = Workbook()
    sheet = book.active
    for row in (TITLES, ["KM-1001", "Slang", 12.5], ["KM-1002", "Klem", 8]):
        sheet.append(row)
    sheet["E2"], sheet["M3"] = "=C2*2", "nagekeken"                       # a formula in an untitled column, a note far to the right
    book.save(wide)
    assert [(row.row, row.item_code, row.price) for row in read_articles(wide, ARTICLE_COLUMNS).rows] == [(2, "KM-1001", "12.5"), (3, "KM-1002", "8")]
    beside = tmp_path / "opgemaakt.xlsx"
    book = Workbook()
    for row in (TITLES, ["KM-1001", "Slang", 12.5]):
        book.active.append(row)
    second = book.create_sheet("Opmaak")
    for place in ("A1", "B2", "C3"):
        second[place].font = Font(bold=True)                              # formatted cells that hold nothing
    book.save(beside)
    assert [(row.item_code, row.price) for row in read_articles(wrong_dimensions(beside), ARTICLE_COLUMNS).rows] == [("KM-1001", "12.5")]
