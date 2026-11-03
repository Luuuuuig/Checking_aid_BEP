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
    path = tmp_path / "list.csv"
    path.write_text("item_code,price\nKM-1001,12.70\n\n,\nKM-1002\n", encoding="utf-8")
    r = import_price_list(path, "S1", CURRENT, PreparedWriter(tmp_path), NOW)
    assert [e.op.article_code for e in r.updated] == ["KM-1001"] and r.skipped == [("KM-1002", "unreadable price")]


def test_a_workbook_is_read_from_its_first_sheet_only(tmp_path):
    workbook = Workbook()
    for sheet, (code, price) in zip([workbook.active, workbook.create_sheet("Old")], [("KM-1001", "12.70"), ("KM-1002", "9.00")]):
        sheet.append(list(HEADER))
        sheet.append(list(item(code, price)))
    workbook.save(tmp_path / "list.xlsx")
    r = import_price_list(tmp_path / "list.xlsx", "S1", CURRENT, PreparedWriter(tmp_path), NOW)
    assert [e.op.article_code for e in r.updated] == ["KM-1001"] and r.skipped == []


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
