"""Tests of the PO store (aid/po_store.py) on the made-up PO PDFs and the made-up export, and on variants of them that the
generator draws. The amounts below are totals of made-up POs; the only authorization limit is the made-up one of conftest.py."""
import csv
import json
import re
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from aid.case import sha256_file
from aid.madeup.generate import EXPORT_COLUMNS, PO_COLUMNS, load_specs, pdf_bytes, po_pages
from aid.po_store import PoStore, PoVersion
from aid.readers.po_export import is_folder_name
from aid.readers.po_pdf import read_po_pdf
from aid.schema import DocValue
from aid.tests.conftest import SETTINGS
from aid.tests.helpers import document_from_spec

SPECS = {spec.case_id: spec for spec in load_specs(Path(__file__).resolve().parents[1] / "madeup" / "spec")}
COLUMNS = {field: field for field in EXPORT_COLUMNS}          # the made-up export names its columns after the aid's fields


def test_store_adds_versions(madeup_out, tmp_path):
    s = PoStore(tmp_path); s.add(madeup_out / "MU03_po_v1.pdf"); s.add(madeup_out / "MU03_po_v2.pdf")
    assert [v.version for v in s.versions("250103")] == [1, 2] and s.versions("250103")[1].document.lines[1].quantity.value == "6"


# The tests below cover what the rulings of 29 October add to the brief's test above.

def drawn_po(path: Path, case_id: str = "MU03", version: int = 1, header: dict | None = None, cells: dict | None = None) -> Path:
    """Version `version` of the made-up PO of `case_id`, drawn by the generator into `path` with some of its texts changed.
    `header` gives a header row a new text by its tag ({"version": "Versie 10"}), and None leaves the row out. `cells` gives
    a table cell a new text by its line and its column title ({("1", "Prijs"): "12,50"}), and "" leaves the cell empty."""
    spec = SPECS[case_id]
    pages = po_pages(spec, next(v for v in spec.po.versions if v.version == version))
    rows = {row.ref: row for page in pages for row in page if row.ref}
    for ref, text in (header or {}).items():
        if text is None:
            pages[0].remove(rows[ref])
        else:
            rows[ref].texts[0].text = text
    for (line_id, title), text in (cells or {}).items():
        row = rows[f"line {line_id}"]
        cell = next(t for t in row.texts if t.x == next(c.x for c in PO_COLUMNS if c.title == title))
        if text:
            cell.text = text
        else:
            row.texts.remove(cell)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes(pages, path.name))
    return path


def written_export(path: Path, rows: list[dict[str, str]], header: list[str] = EXPORT_COLUMNS) -> Path:
    """An export file at `path` with these rows (keyed by the made-up export's column names) under `header`."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows([row[column] for column in EXPORT_COLUMNS] for row in rows)
    return path


def export_rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def stored_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*"))


# --- adding PO PDFs ---

def test_a_version_is_kept_on_disk_and_a_new_store_reads_it_back(madeup_out, tmp_path):
    store = PoStore(tmp_path / "store")
    v2 = store.add(madeup_out / "MU03_po_v2.pdf")                       # stored first, listed last
    v1 = store.add(madeup_out / "MU03_po_v1.pdf")
    folder = tmp_path / "store" / "250103" / "v1"
    assert (v1.po_number, v1.version, v1.version_date, v1.supplier) == ("250103", 1, "2026-10-12", "S03")
    assert v1.pdf_path == folder / "po.pdf" and v1.pdf_path.read_bytes() == (madeup_out / "MU03_po_v1.pdf").read_bytes()
    assert v1.sha256 == sha256_file(madeup_out / "MU03_po_v1.pdf") and v1.document.source_file == "MU03_po_v1.pdf"
    assert PoVersion.model_validate_json((folder / "po.json").read_text(encoding="utf-8")) == v1
    assert PoStore(tmp_path / "store").versions("250103") == [v1, v2]
    assert PoStore(tmp_path / "store").versions("250199") == [] and PoStore(tmp_path / "nothing").versions("250103") == []


def test_versions_are_listed_by_their_number(madeup_out, tmp_path):
    store = PoStore(tmp_path / "store")
    store.add(drawn_po(tmp_path / "v10.pdf", header={"version": "Versie 10"}))
    store.add(madeup_out / "MU03_po_v2.pdf")
    assert [v.version for v in store.versions("250103")] == [2, 10]               # by number, although "v10" sorts before "v2"


def test_a_pdf_without_a_version_number_is_the_next_version(tmp_path):
    store = PoStore(tmp_path / "store")
    first = store.add(drawn_po(tmp_path / "a.pdf", header={"version": None}))
    second = store.add(drawn_po(tmp_path / "b.pdf", version=2, header={"version": None}))
    assert (first.version, second.version, first.document.version_hint) == (1, 2, DocValue())
    assert second.document.lines[1].quantity.value == "6" and second.pdf_path == tmp_path / "store" / "250103" / "v2" / "po.pdf"


def test_a_pdf_already_stored_is_not_stored_again(madeup_out, tmp_path):
    store = PoStore(tmp_path / "store")
    first = store.add(madeup_out / "MU03_po_v1.pdf")
    copy = tmp_path / "inbox" / "Inkooporder 250103.pdf"
    copy.parent.mkdir()
    shutil.copy(madeup_out / "MU03_po_v1.pdf", copy)
    assert store.add(copy) == first and store.add(first.pdf_path) == first       # the same bytes under another name; the store's own copy
    unnumbered = drawn_po(tmp_path / "unnumbered.pdf", header={"version": None})
    assert store.add(unnumbered).version == 2 and store.add(unnumbered).version == 2    # without a version number it is still stored once
    assert [v.version for v in store.versions("250103")] == [1, 2]


def test_a_version_number_stored_from_another_pdf_is_refused(madeup_out, tmp_path):
    store = PoStore(tmp_path / "store")
    first = store.add(madeup_out / "MU03_po_v1.pdf")
    before = stored_files(tmp_path / "store")
    other = drawn_po(tmp_path / "other.pdf", version=2, header={"version": "Versie 1"})     # version 2's lines, printed as version 1
    with pytest.raises(ValueError, match=r"other\.pdf.*version 1"):
        store.add(other)
    assert stored_files(tmp_path / "store") == before and store.versions("250103") == [first]
    assert first.pdf_path.read_bytes() == (madeup_out / "MU03_po_v1.pdf").read_bytes()


@pytest.mark.parametrize("name,reason", [("MU05_conf.pdf", "no row contains 'Artikel'"),     # a scan: the reader's own refusal
                                         ("MU01_conf.pdf", "no PO number")])                 # read as a PO, it names no PO number
def test_a_pdf_that_cannot_be_filed_is_refused(madeup_out, tmp_path, name, reason):
    with pytest.raises(ValueError, match=rf"{re.escape(name)}.*{reason}"):
        PoStore(tmp_path / "store").add(madeup_out / name)
    assert stored_files(tmp_path / "store") == []


# --- the total and the authorization flag ---

def test_the_total_and_whether_it_is_above_the_authorization_limit(madeup_out, tmp_path):
    store = PoStore(tmp_path / "store", SETTINGS.authorization_limit)
    v1, v2 = (store.add(madeup_out / f"MU03_po_v{n}.pdf") for n in (1, 2))
    assert (v1.total, v2.total) == (Decimal("427.40"), Decimal("471.60"))      # 10 x 18,40 + 4 x 22,10 + 5 x 31,00; version 2 orders 6 of line 2
    assert store.add(madeup_out / "MU04_po_v1.pdf").total == Decimal("680.50")   # 18 of its 32 lines are priced per 100
    for spec in (s for s in SPECS.values() if s.confirmation):                    # above the made-up limit where the spec says so: MU07
        assert store.add(madeup_out / f"{spec.case_id}_po_v1.pdf").above_authorization is spec.po.buyer_authorization_exceeded, spec.case_id
    at_the_total = PoStore(tmp_path / "store", authorization_limit=v1.total).versions("250103")    # the flag follows the store's own limit
    assert [v.above_authorization for v in at_the_total] == [False, True]         # a total equal to the limit is not above it


@pytest.mark.parametrize("title", ["Aantal", "Prijs", "Per"])
def test_a_line_without_quantity_price_or_basis_leaves_the_total_unknown(tmp_path, title):
    version = PoStore(tmp_path / "store", SETTINGS.authorization_limit).add(drawn_po(tmp_path / "po.pdf", "MU07", cells={("1", title): ""}))
    assert (version.total, version.above_authorization) == (None, None)          # MU07 is above the limit, but without a total there is no flag


def test_without_a_limit_there_is_no_flag(madeup_out, tmp_path):
    version = PoStore(tmp_path / "store").add(madeup_out / "MU07_po_v1.pdf")
    assert version.total is not None and version.above_authorization is None


# --- finding a PO by the item codes of a document ---

def test_find_by_lines_names_the_pos_whose_latest_version_has_two_of_the_codes(madeup_out, tmp_path):
    store = PoStore(tmp_path / "store")
    for name in ("MU03_po_v1.pdf", "MU03_po_v2.pdf", "MU01_po_v1.pdf"):
        store.add(madeup_out / name)
    for number in ("1000000", "250099"):                                          # two other POs with MU03's lines
        store.add(drawn_po(tmp_path / f"{number}.pdf", header={"po_reference": f"Inkooporder {number}"}))
    doc = document_from_spec("MU03")
    assert store.find_by_lines(doc) == ["250099", "250103", "1000000"]            # in the order of the numbers, not of the texts
    doc.lines = doc.lines[:2]
    assert store.find_by_lines(doc) == ["250099", "250103", "1000000"]            # two codes are enough
    doc.lines = [doc.lines[0], doc.lines[0].model_copy(update={"line_id": "2"})]
    assert store.find_by_lines(doc) == []                                          # one code on two lines is one code
    store.add(drawn_po(tmp_path / "v3.pdf", header={"version": "Versie 3"}, cells={("1", "Artikel"): "KM-0391", ("2", "Artikel"): "KM-0392"}))
    assert store.find_by_lines(document_from_spec("MU03")) == ["250099", "1000000"]    # 250103's latest version keeps only KM-0303
    assert PoStore(tmp_path / "nothing").find_by_lines(document_from_spec("MU03")) == []


# --- filing the POs of an export ---

def test_an_export_files_its_po_as_the_next_version(madeup_out, tmp_path):
    export = madeup_out / "CART02_export.csv"
    store = PoStore(tmp_path / "store", SETTINGS.authorization_limit)
    [version] = store.from_export(export, COLUMNS)
    assert (version.po_number, version.version, version.version_date, version.supplier, version.pdf_path) == ("250202", 1, None, None, export)
    doc = version.document
    assert (doc.kind, doc.reader, doc.source_file, doc.po_reference.value) == ("po", "export:CART02_export.csv", "CART02_export.csv", "250202")
    assert [(l.line_id, l.item_code.value, l.description.value, l.quantity.value, l.unit.value, l.unit_price.value, l.price_basis.value,
             l.delivery_date.value) for l in doc.lines] == [
        (p.line_id, p.item_code, p.description, p.quantity, p.unit, p.unit_price, p.price_basis, p.delivery_date) for p in SPECS["CART02"].po.versions[0].lines]
    unit, price = doc.lines[0].unit, doc.lines[0].unit_price
    assert (unit.text, unit.value, price.text, price.value, price.source, price.uncertain) == ("st", "pc", "12.50", "12.50", None, False)
    assert (version.total, version.above_authorization) == (Decimal("214.50"), False)       # 5 x 12,50 + 3 x 40,00 + 10 x 3,20
    assert store.from_export(export, COLUMNS) == [version] and store.versions("250202") == [version]    # the same export again is stored once
    store.add(drawn_po(tmp_path / "po.pdf", header={"po_reference": "Inkooporder 250202", "version": None}))
    rows = export_rows(export)
    rows[0]["quantity"] = "6"
    [changed] = store.from_export(written_export(tmp_path / "changed.csv", rows), COLUMNS)
    assert [v.version for v in store.versions("250202")] == [1, 2, 3] and changed.version == 3 and changed.document.lines[0].quantity.value == "6"


def test_an_export_of_several_pos_files_each_in_the_order_of_the_file(madeup_out, tmp_path):
    rows = export_rows(madeup_out / "CART02_export.csv") + export_rows(madeup_out / "CART01_export.csv")
    versions = PoStore(tmp_path / "store").from_export(written_export(tmp_path / "export.csv", rows), COLUMNS)
    assert [(v.po_number, v.version, [l.item_code.value for l in v.document.lines]) for v in versions] == [
        ("250202", 1, ["KM-2001", "KM-2002", "KM-2003"]), ("250201", 1, ["KM-2101", "KM-2102", "KM-2103"])]


def test_an_export_with_its_own_column_names_and_number_convention(madeup_out, tmp_path):
    names = ["Inkooporder", "Regel", "Artikel", "Omschrijving", "Aantal", "Eenheid", "Prijs", "Per", "Leverdatum"]
    rows = export_rows(madeup_out / "CART02_export.csv")
    rows[0]["quantity"] = "1.000"
    export = written_export(tmp_path / "export.csv", rows, header=names)
    columns = dict(zip(EXPORT_COLUMNS, names))
    point, comma = (PoStore(tmp_path / name).from_export(export, columns, **kwargs)[0] for name, kwargs in [("a", {}), ("b", {"decimal_comma": True})])
    assert [l.item_code.value for l in point.document.lines] == ["KM-2001", "KM-2002", "KM-2003"] and point.po_number == "250202"
    assert (point.document.lines[0].quantity.value, comma.document.lines[0].quantity.value) == ("1.000", "1000")    # one, or a thousand


def test_a_field_left_out_of_the_column_map_stays_missing(madeup_out, tmp_path):
    columns = {field: column for field, column in COLUMNS.items() if field not in ("description", "delivery_date")}
    [version] = PoStore(tmp_path / "store").from_export(madeup_out / "CART02_export.csv", columns)
    assert [(l.item_code.value, l.description, l.delivery_date) for l in version.document.lines] == [
        ("KM-2001", DocValue(), DocValue()), ("KM-2002", DocValue(), DocValue()), ("KM-2003", DocValue(), DocValue())]


def test_a_blank_or_unreadable_export_cell_has_no_value(madeup_out, tmp_path):
    rows = export_rows(madeup_out / "CART02_export.csv")
    rows[1]["unit_price"], rows[2]["quantity"] = "", "tien"
    [version] = PoStore(tmp_path / "store").from_export(written_export(tmp_path / "export.csv", rows), COLUMNS)
    second, third = version.document.lines[1:]
    assert second.unit_price == DocValue() and (third.quantity.text, third.quantity.value, third.quantity.uncertain) == ("tien", None, True)
    assert version.total is None


@pytest.mark.parametrize("columns,named", [
    (COLUMNS | {"unit": "Eenheid"}, "Eenheid"),                                    # a column the file does not have
    ({field: column for field, column in COLUMNS.items() if field != "po_number"}, "po_number"),      # no column for the PO number
    ({field: column for field, column in COLUMNS.items() if field != "item_code"}, "item_code"),      # no column for the item code
    (COLUMNS | {"price": "unit_price"}, "price"),                                  # a field the aid does not have
])
def test_a_column_map_that_does_not_fit_the_export_is_refused(madeup_out, tmp_path, columns, named):
    with pytest.raises(ValueError, match=named):
        PoStore(tmp_path / "store").from_export(madeup_out / "CART02_export.csv", columns)
    assert stored_files(tmp_path / "store") == []


def test_a_column_map_without_line_id_numbers_the_lines_of_each_po(madeup_out, tmp_path):
    """A map need not name line_id: the lines of each PO are then numbered in the order of the file, whatever the file's own ids."""
    rows = export_rows(madeup_out / "CART02_export.csv") + export_rows(madeup_out / "CART01_export.csv")
    for row in rows:
        row["line_id"] = str(10 * int(row["line_id"]))
    export = written_export(tmp_path / "export.csv", rows)
    columns = {field: column for field, column in COLUMNS.items() if field != "line_id"}
    numbered, kept = (PoStore(tmp_path / name).from_export(export, used) for name, used in (("a", columns), ("b", COLUMNS)))
    assert [[(l.line_id, l.item_code.value) for l in v.document.lines] for v in numbered] == [
        [("1", "KM-2001"), ("2", "KM-2002"), ("3", "KM-2003")], [("1", "KM-2101"), ("2", "KM-2102"), ("3", "KM-2103")]]
    assert [[l.line_id for l in v.document.lines] for v in kept] == [["10", "20", "30"], ["10", "20", "30"]]


@pytest.mark.parametrize("field,text,reason", [
    ("po_number", "", "export.csv, row 7: an item code without a PO number"),
    ("line_id", " ", "export.csv, row 7: an item code without a line id"),
    ("line_id", "2", "export.csv, row 7: the line id stands on an earlier line of the same PO"),    # row 6 is line 2 of PO 250201
    ("po_number", "../250201", "export.csv, row 7: the PO number cannot be the name of a folder"),     # one folder inside the store
    ("po_number", "250201" * 50, "export.csv, row 7: the PO number cannot be the name of a folder"),   # longer than a file system takes
    ("po_number", "Totaal", "export.csv, row 7: the PO number holds no digit"),                        # a word, no PO number
    ("po_number", "250201?", "export.csv, row 7: the PO number cannot be the name of a folder"),       # a character Windows refuses
])
def test_an_export_row_that_cannot_be_filed_is_refused(madeup_out, tmp_path, field, text, reason):
    """The fault is on the last row, of the second PO: the export is refused whole, the first PO (250202) included. The error names
    the row and never what a cell holds."""
    rows = export_rows(madeup_out / "CART02_export.csv") + export_rows(madeup_out / "CART01_export.csv")
    rows[-1][field] = text
    with pytest.raises(ValueError, match=f"^{re.escape(reason)}$") as refused:
        PoStore(tmp_path / "store").from_export(written_export(tmp_path / "export.csv", rows), COLUMNS)
    assert not [cell for cell in rows[-1].values() if len(cell.strip()) > 2 and cell in str(refused.value)]
    assert stored_files(tmp_path / "store") == [] and not (tmp_path / "250201").exists()


def test_a_po_number_on_the_first_row_only_is_filed_with_fill_down(madeup_out, tmp_path):
    """The PO number on the first row of each PO only: with fill_down the export is filed as the full one is, without it nothing is."""
    full = export_rows(madeup_out / "CART02_export.csv") + export_rows(madeup_out / "CART01_export.csv")
    first_only = [row | {"po_number": row["po_number"] if row["line_id"] == "1" else ""} for row in full]
    export = written_export(tmp_path / "export.csv", first_only)
    with pytest.raises(ValueError, match=r"^export\.csv, row 3: an item code without a PO number$"):
        PoStore(tmp_path / "store").from_export(export, COLUMNS)
    assert stored_files(tmp_path / "store") == []
    filled = PoStore(tmp_path / "store").from_export(export, COLUMNS, fill_down=True)
    whole = PoStore(tmp_path / "whole").from_export(written_export(tmp_path / "whole.csv", full), COLUMNS)
    assert [v.po_number for v in filled] == [v.po_number for v in whole] == ["250202", "250201"]
    assert [(v.version, v.total, v.document.lines) for v in filled] == [(v.version, v.total, v.document.lines) for v in whole]


def test_with_fill_down_a_po_whose_number_stands_on_a_text_line_keeps_its_lines(madeup_out, tmp_path):
    """The second PO of the export prints its number on a text line without an item code, and its lines print none. With fill_down
    both POs are filed, each with its own lines and total, so a cart of the second PO finds that PO by its item codes, not the
    first one."""
    full = export_rows(madeup_out / "CART02_export.csv") + export_rows(madeup_out / "CART01_export.csv")
    text_line = {column: "" for column in EXPORT_COLUMNS} | {"po_number": "250201", "description": "Leveren aan magazijn 2"}
    rows = [row | {"po_number": row["po_number"] if row is full[0] else ""} for row in full]
    export = written_export(tmp_path / "export.csv", [*rows[:3], text_line, *rows[3:]])
    columns = {field: column for field, column in COLUMNS.items() if field != "line_id"}
    store = PoStore(tmp_path / "store", SETTINGS.authorization_limit)
    filed = store.from_export(export, columns, fill_down=True)
    assert [(v.po_number, v.total, [l.item_code.value for l in v.document.lines]) for v in filed] == [
        ("250202", Decimal("214.50"), ["KM-2001", "KM-2002", "KM-2003"]), ("250201", Decimal("101.95"), ["KM-2101", "KM-2102", "KM-2103"])]
    assert store.find_by_lines(document_from_spec("CART01", kind="cart")) == ["250201"]


def test_a_price_basis_cell_of_zero_or_below_is_unread_and_the_file_is_filed_whole(madeup_out, tmp_path):
    """A price basis is a whole number above zero. A basis cell of 0 or -1 in a workbook is unread, the total of its PO unknown, and
    the file is filed whole: the store never divides by such a basis."""
    workbook = Workbook()
    workbook.active.append(EXPORT_COLUMNS)
    rows = export_rows(madeup_out / "CART02_export.csv") + export_rows(madeup_out / "CART01_export.csv")
    for place, row in enumerate(rows):
        basis = {4: 0, 5: -1}.get(place, int(row["price_basis"]))                              # two lines of PO 250201
        workbook.active.append([row["po_number"], row["line_id"], row["item_code"], row["description"], int(row["quantity"]), row["unit"],
                                float(row["unit_price"]), basis, row["delivery_date"]])
    workbook.save(tmp_path / "export.xlsx")
    first, second = PoStore(tmp_path / "store", SETTINGS.authorization_limit).from_export(tmp_path / "export.xlsx", COLUMNS)
    assert (first.po_number, first.total, second.po_number, second.total, second.above_authorization) == (
        "250202", Decimal("214.50"), "250201", None, None)
    assert [l.price_basis for l in second.document.lines] == [DocValue(value="1", text="1"), DocValue(text="0", uncertain=True),
                                                             DocValue(text="-1", uncertain=True)]


@pytest.mark.parametrize("po_number", ["CON", "Nul.250103", "250103:1", "250103.", "250103 ", "P" * 101])
def test_the_store_holds_the_po_number_of_a_pdf_to_the_same_folder_rule(madeup_out, tmp_path, monkeypatch, po_number):
    """The store keeps a PO in a folder named after its number, so it refuses a PO number that cannot be a folder name on Windows,
    from a PDF as from an export, before it files anything; the error names the file and not the number. Such a number has no
    versions. Every made-up PO number is a folder name."""
    doc = read_po_pdf(madeup_out / "MU03_po_v1.pdf")
    monkeypatch.setattr("aid.po_store.read_po_pdf", lambda pdf: doc.model_copy(update={"po_reference": DocValue(value=po_number,
                                                                                                                text=po_number)}))
    with pytest.raises(ValueError, match=r"^MU03_po_v1\.pdf: the PO number cannot be the name of a folder$") as refused:
        PoStore(tmp_path / "store").add(madeup_out / "MU03_po_v1.pdf")
    assert po_number.strip() not in str(refused.value)
    assert stored_files(tmp_path / "store") == [] and PoStore(tmp_path / "store").versions(po_number) == []
    assert all(is_folder_name(spec.po.po_number) for spec in SPECS.values())


def test_from_export_on_a_workbook_equals_the_csv(madeup_out, tmp_path):
    """The made-up export saved as a workbook, its numbers as number cells and its dates as date cells, is filed as the CSV file is:
    the same PO, version and total, and every line with the same values. A price cell shows 12.5 where the CSV file writes 12.50,
    which is the same number. The version names the workbook as its file, and the same workbook again is stored once."""
    workbook = Workbook()
    workbook.active.append(EXPORT_COLUMNS)
    for row in export_rows(madeup_out / "CART02_export.csv"):
        workbook.active.append([int(row["po_number"]), int(row["line_id"]), row["item_code"], row["description"], int(row["quantity"]),
                                row["unit"], float(row["unit_price"]), int(row["price_basis"]), date.fromisoformat(row["delivery_date"])])
    xlsx = tmp_path / "export.xlsx"
    workbook.save(xlsx)
    [from_csv] = PoStore(tmp_path / "csv", SETTINGS.authorization_limit).from_export(madeup_out / "CART02_export.csv", COLUMNS)
    store = PoStore(tmp_path / "store", SETTINGS.authorization_limit)
    [version] = store.from_export(xlsx, COLUMNS)
    assert (version.po_number, version.version, version.version_date, version.supplier, version.pdf_path) == ("250202", 1, None, None, xlsx)
    document, other_document = version.document, from_csv.document
    assert (version.sha256, document.reader, document.source_file) == (sha256_file(xlsx), "export:export.xlsx", "export.xlsx")
    assert (version.total, version.above_authorization) == (from_csv.total, from_csv.above_authorization) == (Decimal("214.50"), False)
    assert document.po_reference == other_document.po_reference and len(document.lines) == len(other_document.lines) == 3
    for line, other in zip(document.lines, other_document.lines):
        assert (line.line_id, line.item_code, line.description, line.unit, line.price_basis, line.delivery_date) == (
            other.line_id, other.item_code, other.description, other.unit, other.price_basis, other.delivery_date)
        assert Decimal(line.quantity.value) == Decimal(other.quantity.value) and not line.quantity.uncertain
        assert Decimal(line.unit_price.value) == Decimal(other.unit_price.value) and not line.unit_price.uncertain
    assert (version.document.lines[0].unit_price.text, from_csv.document.lines[0].unit_price.text) == ("12.5", "12.50")
    assert store.from_export(xlsx, COLUMNS) == [version] and store.versions("250202") == [version]
    assert not (tmp_path / "store" / "250202" / "v1" / "po.pdf").exists()                   # no copy of an export is kept


def test_a_supplier_column_names_the_supplier_of_the_version(madeup_out, tmp_path):
    """An export with a supplier column gives the version its supplier, which a version from an export did not have before."""
    rows = [row | {"supplier": "S22" if row["line_id"] == "2" else ""} for row in export_rows(madeup_out / "CART02_export.csv")]
    with open(tmp_path / "export.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[*EXPORT_COLUMNS, "supplier"])
        writer.writeheader()
        writer.writerows(rows)
    [version] = PoStore(tmp_path / "store").from_export(tmp_path / "export.csv", COLUMNS | {"supplier": "supplier"})
    assert (version.supplier, version.document.supplier.text, version.version_date) == ("S22", "S22", None)
    assert PoStore(tmp_path / "store").versions("250202")[0].supplier == "S22"


def test_a_po_number_names_one_folder_inside_the_store(madeup_out, tmp_path):
    PoStore(tmp_path / "store").add(madeup_out / "MU03_po_v1.pdf")
    assert PoStore(tmp_path / "store" / "250103" / "v1").versions("..") == []     # ".." would read the folder above the root


def test_the_store_holds_absolute_paths_when_given_relative_ones(madeup_out, tmp_path, monkeypatch):
    for name in ("MU03_po_v1.pdf", "CART02_export.csv"):
        shutil.copy(madeup_out / name, tmp_path / name)
    monkeypatch.chdir(tmp_path)
    store = PoStore(Path("store"))
    from_pdf = store.add(Path("MU03_po_v1.pdf"))
    [from_export] = store.from_export(Path("CART02_export.csv"), COLUMNS)
    here = tmp_path.resolve()
    copy, export = here / "store" / "250103" / "v1" / "po.pdf", here / "CART02_export.csv"
    assert (store.root, from_pdf.pdf_path, from_export.pdf_path) == (here / "store", copy, export)
    written = [json.loads(path.read_text(encoding="utf-8"))["pdf_path"] for path in sorted(here.glob("store/*/v*/po.json"))]
    assert written == [str(copy), str(export)]
    monkeypatch.chdir(madeup_out)                                                 # read from another working directory
    assert PoStore(here / "store").versions("250103")[0].pdf_path.read_bytes() == (madeup_out / "MU03_po_v1.pdf").read_bytes()
