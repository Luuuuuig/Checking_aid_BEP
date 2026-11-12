"""Tests of the PO store (aid/po_store.py) on the made-up PO PDFs and the made-up export, and on variants of them that the
generator draws. The amounts below are totals of made-up POs; the only authorization limit is the made-up one of conftest.py."""
import csv
import io
import json
import os
import re
import shutil
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from aid.case import sha256_file
from aid.madeup.generate import EXPORT_COLUMNS, PO_COLUMNS, Row, Text, export_csv, load_specs, pdf_bytes, po_pages
from aid.po_store import PoStore, PoVersion
from aid.readers.po_export import is_folder_name
from aid.readers.po_pdf import read_po_pdf
from aid.schema import DocValue
from aid.tests.conftest import SETTINGS
from aid.tests.helpers import ANDERS_LAYOUT, ANDERS_PO, MADEUP_LAYOUT, document_from_spec, given_on

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


def test_as_they_stand_gives_each_po_as_it_stands_in_the_order_of_the_numbers(madeup_out, tmp_path):
    """as_they_stand gives each PO of the store once, as as_it_stands gives it (its latest PDF version, or its latest look when that
    was filed after it), the shorter number first and then by its text. A folder that holds no version and no look is no PO, and a
    store without a folder has none."""
    store = PoStore(tmp_path / "store")
    for name in ("MU03_po_v1.pdf", "MU03_po_v2.pdf", "MU01_po_v1.pdf"):
        store.add(madeup_out / name)
    store.add(drawn_po(tmp_path / "1000000.pdf", header={"po_reference": "Inkooporder 1000000"}))
    store.from_export(madeup_out / "CART01_export.csv", COLUMNS)
    (store.root / "250000").mkdir()                                                # a folder without a version or a look
    standing = list(store.as_they_stand())
    assert [(po.po_number, po.version, po.pdf_path.suffix) for po in standing] == [
        ("250101", 1, ".pdf"), ("250103", 2, ".pdf"), ("250201", 1, ".csv"), ("1000000", 1, ".pdf")]
    assert standing == [store.as_it_stands(number) for number in ("250101", "250103", "250201", "1000000")]
    assert list(PoStore(tmp_path / "nothing").as_they_stand()) == []


# --- filing the POs of an export ---

def test_an_export_files_its_po_as_the_next_look(madeup_out, tmp_path):
    """Rewritten by Task 40, by design: an export files a look at its PO beside the PDF versions, and no version. The look reads as the
    PO it shows: version 1 while the PO has no PDF version, then the number of its latest PDF version. The same export again files
    nothing; other rows are the next look."""
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
    assert store.from_export(export, COLUMNS) == [version] and store.snapshots("250202") == [version]    # the same export again: nothing
    assert store.versions("250202") == []
    pdf = store.add(drawn_po(tmp_path / "po.pdf", header={"po_reference": "Inkooporder 250202", "version": None}))
    assert pdf.version == 1                                                       # the PDFs keep a sequence of their own
    rows = export_rows(export)
    rows[0]["quantity"] = "6"
    [changed] = store.from_export(written_export(tmp_path / "changed.csv", rows), COLUMNS)
    assert store.versions("250202") == [pdf] and [look.document.lines[0].quantity.value for look in store.snapshots("250202")] == ["5", "6"]
    assert (changed.version, changed.supplier, changed.document.lines[0].quantity.value) == (1, "S03", "6")    # the PDF's number and supplier


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
    monkeypatch.setattr("aid.po_store.read_po", lambda pdf, layout: doc.model_copy(update={"po_reference": DocValue(value=po_number,
                                                                                                                        text=po_number)}))
    with pytest.raises(ValueError, match=r"^MU03_po_v1\.pdf: the PO number cannot be the name of a folder$") as refused:
        PoStore(tmp_path / "store").add(madeup_out / "MU03_po_v1.pdf")
    assert po_number.strip() not in str(refused.value)
    assert stored_files(tmp_path / "store") == [] and PoStore(tmp_path / "store").versions(po_number) == []
    assert all(is_folder_name(spec.po.po_number) for spec in SPECS.values())


def test_from_export_on_a_workbook_equals_the_csv(madeup_out, tmp_path):
    """The made-up export saved as a workbook, its numbers as number cells and its dates as date cells, is filed as the CSV file is:
    the same PO, version and total, and every line with the same values. A price cell shows 12.5 where the CSV file writes 12.50,
    which is the same number. The look names the workbook as its file, and the same workbook again files nothing."""
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
    assert store.from_export(xlsx, COLUMNS) == [version] and store.snapshots("250202") == [version] and store.versions("250202") == []
    # the same workbook again files nothing; the store notes in given.txt that it was given a print of the order once more (W16)
    assert stored_files(tmp_path / "store" / "250202") == [tmp_path / "store" / "250202" / "e1", tmp_path / "store" / "250202" / "e1" / "given.txt",
                                                           tmp_path / "store" / "250202" / "e1" / "po.json"]
    assert store.from_export(madeup_out / "CART02_export.csv", COLUMNS) == [version]      # the same rows in the CSV file: nothing new


def test_a_supplier_column_names_the_supplier_of_the_look(madeup_out, tmp_path):
    """An export with a supplier column gives the look its supplier, which a PO from an export did not have before."""
    rows = [row | {"supplier": "S22" if row["line_id"] == "2" else ""} for row in export_rows(madeup_out / "CART02_export.csv")]
    with open(tmp_path / "export.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[*EXPORT_COLUMNS, "supplier"])
        writer.writeheader()
        writer.writerows(rows)
    [version] = PoStore(tmp_path / "store").from_export(tmp_path / "export.csv", COLUMNS | {"supplier": "supplier"})
    assert (version.supplier, version.document.supplier.text, version.version_date) == ("S22", "S22", None)
    assert PoStore(tmp_path / "store").snapshots("250202")[0].supplier == "S22"


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
    written = [json.loads(path.read_text(encoding="utf-8"))["pdf_path"] for path in sorted(here.glob("store/*/*/po.json"))]
    assert written == [str(copy), str(export)]                     # 250103/v1, the PDF version, and 250202/e1, the look of the export
    monkeypatch.chdir(madeup_out)                                                 # read from another working directory
    assert PoStore(here / "store").versions("250103")[0].pdf_path.read_bytes() == (madeup_out / "MU03_po_v1.pdf").read_bytes()
    assert PoStore(here / "store").snapshots("250202")[0].pdf_path == export


# --- adding PO PDFs with a layout from the settings ---

def test_add_reads_with_the_layout(madeup_out, tmp_path):
    """With a layout the store reads the PDF with it: the second made-up PO, which prints no version, becomes version 1 of its PO, and
    a made-up PO read with the made-up layout is stored as without one, but for the reader's name."""
    store = PoStore(tmp_path / "store", SETTINGS.authorization_limit)
    anders = store.add(ANDERS_PO, ANDERS_LAYOUT)
    assert (anders.po_number, anders.version, anders.version_date, anders.supplier) == (
        "IB-2600417", 1, "2026-11-03", "Proefleverancier S23")
    assert (anders.document.reader, anders.document.source_file, len(anders.document.lines)) == ("po_pdf:layout", "ANDERS_po_v1.pdf", 12)
    folder = tmp_path / "store" / "IB-2600417" / "v1"
    assert anders.pdf_path == folder / "po.pdf" and anders.pdf_path.read_bytes() == ANDERS_PO.read_bytes()
    assert anders.sha256 == sha256_file(ANDERS_PO)
    assert PoVersion.model_validate_json((folder / "po.json").read_text(encoding="utf-8")) == anders
    assert store.add(ANDERS_PO, ANDERS_LAYOUT) == anders and store.versions("IB-2600417") == [anders]   # the same file again is stored once
    by_layout = store.add(madeup_out / "MU03_po_v2.pdf", MADEUP_LAYOUT)
    by_template = PoStore(tmp_path / "other", SETTINGS.authorization_limit).add(madeup_out / "MU03_po_v2.pdf")
    assert (by_layout.document.reader, by_template.document.reader) == ("po_pdf:layout", "po_pdf:po_madeup")
    but_for = {"pdf_path": True, "document": {"reader"}}                    # the store's own copy of the PDF, and the name of the reader
    assert by_layout.model_dump(exclude=but_for) == by_template.model_dump(exclude=but_for)
    assert (by_layout.version, by_layout.total, by_layout.above_authorization) == (2, Decimal("471.60"), False)
    assert store.add(madeup_out / "MU03_po_v1.pdf", MADEUP_LAYOUT).version == 1                             # the number the PDF prints
    assert [v.version for v in store.versions("250103")] == [1, 2] and store.add(madeup_out / "MU03_po_v1.pdf", None).version == 1


def test_prices_per_piece_count_for_the_total_only(madeup_out, tmp_path):
    """The second made-up PO prints no price basis. With prices_per_piece its lines keep an empty price basis and its total counts each
    line as per one unit; without it the total is unknown. A line that prints its basis is counted with that basis either way."""
    store = PoStore(tmp_path / "store", SETTINGS.authorization_limit)
    per_piece = store.add(ANDERS_PO, ANDERS_LAYOUT)
    assert ANDERS_LAYOUT.prices_per_piece and [line.price_basis for line in per_piece.document.lines] == [DocValue()] * 12
    assert (per_piece.total, per_piece.above_authorization) == (Decimal("4417.85"), False)       # the total the PDF prints on its last row
    assert PoStore(tmp_path / "store", Decimal("4000")).versions("IB-2600417")[0].above_authorization is True
    unticked = ANDERS_LAYOUT.model_copy(update={"prices_per_piece": False})
    unknown = PoStore(tmp_path / "unknown", SETTINGS.authorization_limit).add(ANDERS_PO, unticked)
    assert (unknown.total, unknown.above_authorization) == (None, None) and unknown.document == per_piece.document
    ticked = MADEUP_LAYOUT.model_copy(update={"prices_per_piece": True})
    assert store.add(madeup_out / "MU04_po_v1.pdf", ticked).total == Decimal("680.50")       # 18 of its 32 lines print "100" as their basis
    without_one = store.add(drawn_po(tmp_path / "po.pdf", "MU07", cells={("1", "Per"): ""}), ticked)
    assert without_one.document.lines[0].price_basis == DocValue() and without_one.document.lines[1].price_basis.value == "1"
    assert (without_one.total, without_one.above_authorization) == (Decimal("5537.50"), True)       # 3 x 12,50 per one + 100 x 55,00
    unread = PoStore(tmp_path / "unread", SETTINGS.authorization_limit).add(drawn_po(tmp_path / "unread.pdf", "MU07",
                                                                                     cells={("1", "Per"): "n.v.t."}), ticked)
    basis = unread.document.lines[0].price_basis
    assert (basis.text, basis.value, basis.uncertain) == ("n.v.t.", None, True)          # a basis is printed, and it cannot be read
    assert (unread.total, unread.above_authorization) == (None, None)                    # so the line is not counted as per one


def test_a_refused_pdf_leaves_the_store_as_it_was(madeup_out, tmp_path):
    """With a layout the store refuses a PDF that does not fit it, also one that the template of the made-up layout reads, and a PDF
    that fits and gives no PO number or no order line. Nothing is stored."""
    store = PoStore(tmp_path / "store")
    first = store.add(madeup_out / "MU03_po_v1.pdf", MADEUP_LAYOUT)
    before = stored_files(tmp_path / "store")
    no_number = drawn_po(tmp_path / "no_number.pdf", header={"po_reference": "Inkooporder nr. 250103"})   # fits: "nr." holds no digit
    no_line = drawn_po(tmp_path / "no_line.pdf", cells={(line, "Regel"): "" for line in ("1", "2", "3")})
    pages = po_pages(SPECS["MU03"], SPECS["MU03"].po.versions[0])
    pages.append([Row([Text(0, "Inkooporder 250199")]), *pages[0][6:]])          # a second PO printed behind the first
    two_pos = tmp_path / "two_pos.pdf"
    two_pos.write_bytes(pdf_bytes(pages, two_pos.name))
    for pdf, layout, reason in ((madeup_out / "MU03_conf.pdf", MADEUP_LAYOUT, "does not fit the purchase order layout"),
                                (madeup_out / "MU05_conf.pdf", MADEUP_LAYOUT, "does not fit the purchase order layout"),       # a scan
                                (ANDERS_PO, MADEUP_LAYOUT, "does not fit the purchase order layout"),
                                (madeup_out / "MU03_po_v2.pdf", ANDERS_LAYOUT, "does not fit the purchase order layout"),   # a made-up PO
                                (no_number, MADEUP_LAYOUT, "no PO number found"), (no_line, MADEUP_LAYOUT, "no order line found"),
                                (two_pos, MADEUP_LAYOUT, "more than one PO")):
        with pytest.raises(ValueError, match=rf"^{re.escape(pdf.name)}: .*{reason}"):
            store.add(pdf, layout)
        assert stored_files(tmp_path / "store") == before and store.versions("250103") == [first], pdf.name
    assert not (tmp_path / "store" / "IB-2600417").exists()


@pytest.mark.parametrize("printed", ["PO/2026/0417", "COM1"])
def test_a_po_number_that_cannot_be_a_folder_name_is_refused_by_the_setting(tmp_path, printed):
    """A PO number read after the PO label may hold a slash, or be a name that Windows keeps for a device. The store refuses it by the
    folder rule of the export reader, and the refusal names the setting and not the number, so that the skip reason, the digest and
    runner.log never hold a value read from the PDF."""
    pdf = drawn_po(tmp_path / "slash.pdf", header={"po_reference": f"Inkooporder {printed}"})
    with pytest.raises(ValueError, match=r"^slash\.pdf: the PO number .*po_layout\.po_label.* cannot be the name of a folder") as refused:
        PoStore(tmp_path / "store").add(pdf, MADEUP_LAYOUT)
    assert printed not in str(refused.value) and not any(char.isdigit() for char in str(refused.value))
    assert stored_files(tmp_path / "store") == []


def test_a_version_stored_from_another_pdf_is_refused_by_the_setting_with_a_layout(madeup_out, tmp_path):
    """With a layout the refusal of a version number that the store holds from another file names the settings and the file, and
    neither the version number nor the PO number, which are read from the company's PDF. Without a layout, for the made-up PDFs, it
    names them as before."""
    store = PoStore(tmp_path / "store")
    first = store.add(madeup_out / "MU03_po_v1.pdf", MADEUP_LAYOUT)
    other = drawn_po(tmp_path / "other.pdf", version=2, header={"version": "Versie 1"})     # version 2's lines, printed as version 1
    with pytest.raises(ValueError, match=r"^other\.pdf: .*po_layout\.version_label.*from another file") as refused:
        store.add(other, MADEUP_LAYOUT)
    assert "250103" not in str(refused.value) and "1" not in str(refused.value).replace("other.pdf", "")
    assert store.versions("250103") == [first]
    made_up = PoStore(tmp_path / "made_up")
    made_up.add(madeup_out / "MU03_po_v1.pdf")
    with pytest.raises(ValueError, match=r"other\.pdf is version 1 of PO 250103, which is stored from another file"):
        made_up.add(other)


def test_a_version_held_from_another_file_is_refused_with_a_class_of_its_own(madeup_out, tmp_path):
    """Ruling O6: the refusal of a version number that the store holds from another file is a VersionHeld, a ValueError of its own, in
    both its wordings (with a layout and without one), so that the scan tells it apart by its class and never by its text. Other
    refusals of the store stay plain ValueErrors."""
    from aid.po_store import VersionHeld
    assert issubclass(VersionHeld, ValueError)
    other = drawn_po(tmp_path / "other.pdf", version=2, header={"version": "Versie 1"})     # version 2's lines, printed as version 1
    for name, layout in (("layout", MADEUP_LAYOUT), ("made_up", None)):
        store = PoStore(tmp_path / name)
        store.add(madeup_out / "MU03_po_v1.pdf", layout)
        with pytest.raises(VersionHeld, match="from another file"):
            store.add(other, layout)
    for name in ("MU05_conf.pdf", "MU01_conf.pdf"):
        with pytest.raises(ValueError) as refused:
            PoStore(tmp_path / "plain").add(madeup_out / name)
        assert type(refused.value) is ValueError, name


# --- an export is a look at the PO, kept beside its PDF versions (Task 40) ---

FILED = datetime(2026, 11, 16, 8, 0).timestamp()          # the moment the tests below file from, in local time


def filed(store: PoStore, po_number: str, look: str, minute: int) -> None:
    """Set when the version or the look `look` ("v2", "e1") of the PO was filed: the modification time of its po.json, `minute`
    minutes after FILED. The store orders the looks at a PO by these times, so a test that needs an order sets it."""
    moment = FILED + 60 * minute
    os.utime(store.root / po_number / look / "po.json", (moment, moment))


def rows_of(case_id: str) -> list[dict[str, str]]:
    """The rows of the ERP export of the made-up PO of `case_id`: the lines of its latest version (export_csv of the generator)."""
    return list(csv.DictReader(io.StringIO(export_csv(SPECS[case_id]))))


def test_an_export_files_a_look_and_no_version(madeup_out, tmp_path):
    """An export makes no v folder: each of its POs gets the folder e1 with its po.json, a PoVersion written whole, that keeps the
    export's path and the file's sha256 and has no po.pdf beside it. versions() gives no look, snapshots() gives the looks, and a
    new store on the same root reads them back."""
    export = madeup_out / "CART02_export.csv"
    store = PoStore(tmp_path / "store", SETTINGS.authorization_limit)
    [look] = store.from_export(export, COLUMNS)
    folder = tmp_path / "store" / "250202" / "e1"
    assert stored_files(tmp_path / "store") == [tmp_path / "store" / "250202", folder, folder / "po.json"]
    assert PoVersion.model_validate_json((folder / "po.json").read_text(encoding="utf-8")) == look
    assert (look.pdf_path, look.sha256, look.version, look.document.reader) == (export, sha256_file(export), 1, "export:CART02_export.csv")
    again = PoStore(tmp_path / "store", SETTINGS.authorization_limit)
    assert again.versions("250202") == [] and again.snapshots("250202") == [look] and again.as_it_stands("250202") == look
    assert again.snapshots("250199") == [] and again.snapshots("..") == [] and PoStore(tmp_path / "nothing").snapshots("250202") == []
    pdf = store.add(madeup_out / "MU03_po_v1.pdf")
    assert store.versions("250103") == [pdf] and store.snapshots("250103") == []        # a PDF makes no look


def test_the_same_rows_again_file_nothing_and_other_rows_the_next_look(madeup_out, tmp_path):
    """Each PO of an export is held against its latest look, value by value in normal form, numbers as numbers: the same rows file
    nothing, also when the file writes them otherwise; other rows are the next look, e1, e2 and so on, also when a new file holds the
    rows of an earlier look, and so is a supplier the export now names. The file of an earlier look files nothing more (ruling E1,
    below). A file of several POs files only those whose rows changed. `filed` gets the PO number of each look filed."""
    store = PoStore(tmp_path / "store")
    news: list[str] = []
    [first] = store.from_export(madeup_out / "CART02_export.csv", COLUMNS, filed=news)
    assert news == ["250202"]
    same: list[str] = []
    assert store.from_export(madeup_out / "CART02_export.csv", COLUMNS, filed=same) == [first] and same == []
    rows = export_rows(madeup_out / "CART02_export.csv")
    shorter = {"12.50": "12.5", "40.00": "40", "3.20": "3.2"}
    written_otherwise = [row | {"quantity": f"{row['quantity']}.0", "unit_price": shorter[row["unit_price"]], "unit": "stuks",
                                "delivery_date": "12-11-2026"} for row in rows]           # 5.0, 12.5, stuks for st, the day first
    assert store.from_export(written_export(tmp_path / "otherwise.csv", written_otherwise), COLUMNS, filed=same) == [first] and same == []
    changed = [rows[0] | {"unit_price": "12.60"}, *rows[1:]]
    [second] = store.from_export(written_export(tmp_path / "changed.csv", changed), COLUMNS, filed=news)
    assert news == ["250202", "250202"] and second.document.lines[0].unit_price.value == "12.60"
    assert [look.document.reader for look in store.snapshots("250202")] == ["export:CART02_export.csv", "export:changed.csv"]
    assert sorted(path.name for path in (tmp_path / "store" / "250202").iterdir()) == ["e1", "e2"]
    [third] = store.from_export(written_export(tmp_path / "back.csv", rows), COLUMNS, filed=news)     # the first rows again, in a new
    assert third.document.lines == first.document.lines and len(store.snapshots("250202")) == 3 and news[-1] == "250202"   # file
    assert store.from_export(madeup_out / "CART02_export.csv", COLUMNS, filed=same) == [first] and same == []    # the first file: E1
    both = written_export(tmp_path / "both.csv", rows + export_rows(madeup_out / "CART01_export.csv"))
    several: list[str] = []
    looks = store.from_export(both, COLUMNS, filed=several)
    assert several == ["250201"] and [look.po_number for look in looks] == ["250202", "250201"] and looks[0] == third
    with open(tmp_path / "named.csv", "w", encoding="utf-8", newline="") as f:          # the same lines, and now a supplier
        writer = csv.DictWriter(f, fieldnames=[*EXPORT_COLUMNS, "supplier"])
        writer.writeheader()
        writer.writerows(row | {"supplier": "S21"} for row in export_rows(madeup_out / "CART01_export.csv"))
    [named] = store.from_export(tmp_path / "named.csv", COLUMNS | {"supplier": "supplier"}, filed=several)
    assert several == ["250201", "250201"] and named.supplier == "S21" and named.document.lines == looks[1].document.lines


def test_an_export_file_is_filed_once(madeup_out, tmp_path):
    """Ruling E1: an export file that is the source of a look of a PO files nothing more for that PO, whatever looks came after it,
    and neither does a file that filed nothing for the PO because its rows were those of the latest look (the look keeps its sha256
    in e<n>/same_rows.txt). So a file handled again at every pass, or an older export given again, never comes back on top of a
    newer look, and each gives the look it was. Another file with the rows of an earlier look is still the next look (rule 2)."""
    store = PoStore(tmp_path / "store")
    older = madeup_out / "CART01_export.csv"
    rows = export_rows(older)
    same = written_export(tmp_path / "same.csv", rows)                                   # the older rows, in another file
    newer = written_export(tmp_path / "newer.csv", [rows[0] | {"unit_price": "0.99"}, *rows[1:]])
    [first] = store.from_export(older, COLUMNS)
    assert store.from_export(same, COLUMNS) == [first]                                    # the same rows: nothing filed
    assert (tmp_path / "store" / "250201" / "e1" / "same_rows.txt").read_text(encoding="utf-8") == sha256_file(same) + "\n"
    [second] = store.from_export(newer, COLUMNS)
    for path, look in ((older, first), (same, first), (newer, second), (older, first), (same, first)):
        filed: list[str] = []
        assert store.from_export(path, COLUMNS, filed=filed) == [look] and filed == [], path.name
    assert [look.document.lines[0].unit_price.value for look in store.snapshots("250201")] == ["0.95", "0.99"]
    assert store.as_it_stands("250201") == second
    assert (tmp_path / "store" / "250201" / "e1" / "same_rows.txt").read_text(encoding="utf-8") == sha256_file(same) + "\n"
    cut = tmp_path / "store" / "250201" / "e2" / "same_rows.txt"
    cut.write_text("0123", encoding="utf-8")                                              # a line a crash cut off
    copy = written_export(tmp_path / "copy.csv", [rows[0] | {"unit_price": "0.990"}, *rows[1:]])   # the newer rows, written otherwise
    assert store.from_export(copy, COLUMNS) == [second] and cut.read_text(encoding="utf-8") == f"0123\n{sha256_file(copy)}\n"
    assert store.from_export(copy, COLUMNS) == [second] and cut.read_text(encoding="utf-8") == f"0123\n{sha256_file(copy)}\n"   # kept once
    back = written_export(tmp_path / "back.csv", rows + export_rows(madeup_out / "CART02_export.csv"))     # a new file of two POs
    filed = []
    third, other = store.from_export(back, COLUMNS, filed=filed)
    assert filed == ["250201", "250202"] and third.document.lines == first.document.lines and store.as_it_stands("250201") == third


@pytest.mark.parametrize("name,change", [
    ("delivery_date", lambda rows: [rows[0] | {"delivery_date": "2026-11-13"}, *rows[1:]]),
    ("unit", lambda rows: [rows[0] | {"unit": "m"}, *rows[1:]]),
    ("price_basis", lambda rows: [rows[0] | {"price_basis": "10"}, *rows[1:]]),
    ("description", lambda rows: [rows[0] | {"description": "Hydraulische slang 10 mm, 3 m"}, *rows[1:]]),
    ("line_id", lambda rows: [rows[0] | {"line_id": "10"}, *rows[1:]]),
])
def test_each_value_of_the_rows_counts(madeup_out, tmp_path, name, change):
    """Rule 2, value by value (ruling E7): an export that changes only one value of the latest look's rows, the delivery date, the
    unit, the price basis, the description or a line id, files the next look, which holds the change."""
    store = PoStore(tmp_path / "store")
    rows = export_rows(madeup_out / "CART02_export.csv")
    [first] = store.from_export(madeup_out / "CART02_export.csv", COLUMNS)
    filed: list[str] = []
    [look] = store.from_export(written_export(tmp_path / f"{name}.csv", change(rows)), COLUMNS, filed=filed)
    assert filed == ["250202"] and store.as_it_stands("250202") == look and len(store.snapshots("250202")) == 2
    differs = [(old, new) for old, new in zip(first.document.lines, look.document.lines) if old != new]
    assert len(differs) == 1 and differs[0][0].line_id == "1"


def test_the_po_as_it_stands_in_the_four_orders_of_filing(madeup_out, tmp_path):
    """The PO as it stands is its latest PDF version, or its latest look when that was filed after the latest PDF version or the PO
    has no PDF version; when the two were filed at the same time the PDF counts. The times are those of the po.json files, set
    here, and filed_at gives them."""
    store = PoStore(tmp_path / "store")
    v1, v2 = (store.add(madeup_out / f"MU03_po_v{n}.pdf") for n in (1, 2))         # PDFs only: the latest version
    filed(store, "250103", "v1", 10)
    filed(store, "250103", "v2", 0)                                                 # filed before version 1, and still the latest
    assert store.as_it_stands("250103") == v2
    rows = export_rows(madeup_out / "CART02_export.csv")                           # exports only: the latest look
    store.from_export(written_export(tmp_path / "first.csv", rows), COLUMNS)
    [latest] = store.from_export(written_export(tmp_path / "second.csv", [rows[0] | {"quantity": "6"}, *rows[1:]]), COLUMNS)
    filed(store, "250202", "e1", 20)
    filed(store, "250202", "e2", 30)
    assert store.as_it_stands("250202") == latest
    [look] = store.from_export(written_export(tmp_path / "250101.csv", rows_of("MU01")), COLUMNS)     # an export, then a PDF: the PDF
    pdf = store.add(madeup_out / "MU01_po_v1.pdf")
    filed(store, "250101", "e1", 0)
    filed(store, "250101", "v1", 5)
    assert store.as_it_stands("250101") == pdf
    filed(store, "250101", "e1", 6)                                                 # a PDF, then an export: the look
    assert store.as_it_stands("250101") == look.model_copy(update={"supplier": "S01"})        # the look reads with the PDF's supplier
    filed(store, "250101", "e1", 5)                                                 # filed at the same time: the PDF
    assert store.as_it_stands("250101") == pdf
    assert store.filed_at("250101", "v1") == store.filed_at("250101", "e1") == datetime.fromtimestamp(FILED + 300).astimezone()
    assert store.filed_at("250202", "e2") > store.filed_at("250202", "e1")
    assert store.as_it_stands("250199") is None and store.as_it_stands("..") is None
    with pytest.raises(FileNotFoundError):
        store.filed_at("250101", "e2")
    with pytest.raises(ValueError):
        store.filed_at("250101", "../250103/v1")


def test_when_the_po_as_it_stands_was_filed(madeup_out, tmp_path):
    """filed_last gives when the PO as it stands was filed: its latest PDF version, or its latest look when that was filed after it,
    by the times of their po.json files; an earlier version or look does not count, and a PO the store does not know has none."""
    store = PoStore(tmp_path / "store")
    for n in (1, 2):
        store.add(madeup_out / f"MU03_po_v{n}.pdf")
    filed(store, "250103", "v1", 40)
    filed(store, "250103", "v2", 10)                                                # the latest version, whenever the first was filed
    assert store.filed_last("250103") == store.filed_at("250103", "v2") == datetime.fromtimestamp(FILED + 600).astimezone()
    rows = export_rows(madeup_out / "CART02_export.csv")
    store.from_export(written_export(tmp_path / "first.csv", rows), COLUMNS)
    store.from_export(written_export(tmp_path / "second.csv", [rows[0] | {"quantity": "6"}, *rows[1:]]), COLUMNS)
    filed(store, "250202", "e1", 30)
    filed(store, "250202", "e2", 20)                                                # the latest look, whenever the first was filed
    assert store.filed_last("250202") == store.filed_at("250202", "e2")
    store.from_export(written_export(tmp_path / "250101.csv", rows_of("MU01")), COLUMNS)
    store.add(madeup_out / "MU01_po_v1.pdf")
    filed(store, "250101", "e1", 0)
    filed(store, "250101", "v1", 5)                                                 # an export, then a PDF: the PDF stands
    assert store.filed_last("250101") == store.filed_at("250101", "v1")
    filed(store, "250101", "e1", 6)                                                 # a PDF, then an export: the look stands
    assert store.filed_last("250101") == store.filed_at("250101", "e1")
    assert store.filed_last("250199") is None and store.filed_last("..") is None


def test_a_look_carries_the_number_and_the_supplier_of_the_latest_pdf_version(madeup_out, tmp_path):
    """A look is given with the number of the latest PDF version of its PO, 1 without one, and with that version's supplier when its
    export names none; a supplier the export names stays. Its po.json keeps what the export gave, so the look follows a later PDF."""
    store = PoStore(tmp_path / "store")
    [alone] = store.from_export(written_export(tmp_path / "250103.csv", rows_of("MU03")), COLUMNS)
    assert (alone.version, alone.supplier) == (1, None)
    store.add(madeup_out / "MU03_po_v1.pdf")
    v2 = store.add(madeup_out / "MU03_po_v2.pdf")
    filed(store, "250103", "e1", 10)
    filed(store, "250103", "v1", 0)
    filed(store, "250103", "v2", 5)
    stands = store.as_it_stands("250103")
    assert (stands.version, stands.supplier, stands.document) == (2, "S03", alone.document) and v2.supplier == "S03"
    assert json.loads((tmp_path / "store" / "250103" / "e1" / "po.json").read_text(encoding="utf-8"))["supplier"] is None
    v3 = store.add(drawn_po(tmp_path / "v3.pdf", version=2, header={"version": "Versie 3", "supplier": "Leverancier: S23"}))
    assert (v3.version, v3.supplier) == (3, "S23")
    assert [(look.version, look.supplier) for look in store.snapshots("250103")] == [(3, "S23")]
    named = [row | {"supplier": "S21"} for row in export_rows(madeup_out / "CART02_export.csv")]
    with open(tmp_path / "named.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[*EXPORT_COLUMNS, "supplier"])
        writer.writeheader()
        writer.writerows(named)
    [own] = store.from_export(tmp_path / "named.csv", COLUMNS | {"supplier": "supplier"})
    store.add(drawn_po(tmp_path / "250202.pdf", header={"po_reference": "Inkooporder 250202", "version": "Versie 4"}))
    filed(store, "250202", "v4", 0)
    filed(store, "250202", "e1", 1)
    assert (own.version, own.supplier) == (1, "S21")
    assert (store.as_it_stands("250202").version, store.as_it_stands("250202").supplier) == (4, "S21")


def test_candidates_for_a_confirmation_and_for_a_cart(madeup_out, tmp_path):
    """What a document is matched against. For a store of PO PDFs it is versions(), for a cart as for a confirmation, also with a
    look filed before the latest PDF version. A look filed after it is the PO as it stands: a cart gets it alone, and a
    confirmation still gets the PDF versions, never an export while its PO has a PDF. A PO known from an export only gives its
    look to both kinds, and an unknown PO nothing."""
    store = PoStore(tmp_path / "store")
    pdfs = [store.add(madeup_out / f"MU03_po_v{n}.pdf") for n in (1, 2)]
    assert store.candidates("250103", "confirmation") == store.candidates("250103", "cart") == store.versions("250103") == pdfs
    [look] = store.from_export(written_export(tmp_path / "250103.csv", rows_of("MU03")), COLUMNS)
    filed(store, "250103", "v1", 0)
    filed(store, "250103", "e1", 5)
    filed(store, "250103", "v2", 10)
    assert store.candidates("250103", "confirmation") == store.candidates("250103", "cart") == pdfs
    filed(store, "250103", "e1", 15)
    assert (look.version, look.supplier, look.document.reader) == (2, "S03", "export:250103.csv")
    assert store.candidates("250103", "cart") == [look] == [store.as_it_stands("250103")]
    assert store.candidates("250103", "confirmation") == pdfs
    [only] = store.from_export(madeup_out / "CART02_export.csv", COLUMNS)
    assert store.candidates("250202", "confirmation") == store.candidates("250202", "cart") == [only]
    assert store.candidates("250199", "confirmation") == store.candidates("250199", "cart") == [] == store.candidates("..", "cart")


def test_find_by_lines_finds_a_po_known_from_an_export(madeup_out, tmp_path):
    """find_by_lines looks at each PO as it stands: a PO known from an export only is found by the item codes of its look, and a PO
    whose newer look holds other items is found by those, until a later PDF version stands again."""
    store = PoStore(tmp_path / "store")
    store.from_export(madeup_out / "CART01_export.csv", COLUMNS)
    assert store.find_by_lines(document_from_spec("CART01", kind="cart")) == ["250201"]
    for n in (1, 2):
        store.add(madeup_out / f"MU03_po_v{n}.pdf")
    other_items = [row | {"item_code": {"1": "KM-0391", "2": "KM-0392"}.get(row["line_id"], row["item_code"])} for row in rows_of("MU03")]
    store.from_export(written_export(tmp_path / "250103.csv", other_items), COLUMNS)
    filed(store, "250103", "v1", 0)
    filed(store, "250103", "v2", 5)
    filed(store, "250103", "e1", 10)
    assert store.find_by_lines(document_from_spec("MU03")) == []                  # 250103 as it stands keeps only KM-0303
    filed(store, "250103", "e1", 5)                                                # filed with version 2: the PDF stands
    assert store.find_by_lines(document_from_spec("MU03")) == ["250103"]


def test_a_pdf_that_prints_version_1_is_filed_after_an_export(madeup_out, tmp_path):
    """The PDF versions and the looks no longer share a sequence: a PO PDF that prints its version number is filed as that version
    whatever looks the PO has (before Task 40 an export was version 1, and the PDF that printed "Versie 1" was refused), and a PDF
    that prints none is the next PDF version."""
    store = PoStore(tmp_path / "store")
    [look] = store.from_export(written_export(tmp_path / "250101.csv", rows_of("MU01")), COLUMNS)
    pdf = store.add(madeup_out / "MU01_po_v1.pdf")
    assert (pdf.version, pdf.document.version_hint.value) == (1, "1")
    assert store.versions("250101") == [pdf] and store.snapshots("250101") == [look.model_copy(update={"supplier": "S01"})]
    unnumbered = store.add(drawn_po(tmp_path / "unnumbered.pdf", "MU01", header={"version": None}))
    assert unnumbered.version == 2 and [v.version for v in store.versions("250101")] == [1, 2]


def test_add_and_from_export_say_what_they_filed_now_and_what_was_filed_before(madeup_out, tmp_path):
    """Task 37: so that the scan can tell a file it filed before, add appends the PO number to `filed` only when it files the PDF now,
    not when the store holds it already, and from_export appends to `taken` the POs that had taken the file before (ruling E1: it files
    nothing more for them), not a PO whose rows a new file only repeats, which keeps the file in its latest look's same_rows.txt."""
    store = PoStore(tmp_path / "store")
    first: list[str] = []
    assert store.add(madeup_out / "MU01_po_v1.pdf", filed=first).version == 1 and first == ["250101"]
    again: list[str] = []
    assert store.add(madeup_out / "MU01_po_v1.pdf", None, again).version == 1 and again == []
    assert store.add(madeup_out / "MU01_po_v1.pdf").version == 1                          # without the list, as before
    filed, taken = [], []
    store.from_export(madeup_out / "CART02_export.csv", COLUMNS, filed=filed, taken=taken)
    assert (filed, taken) == (["250202"], [])
    filed, taken = [], []
    store.from_export(madeup_out / "CART02_export.csv", COLUMNS, filed=filed, taken=taken)
    assert (filed, taken) == ([], ["250202"])
    filed, taken = [], []
    store.from_export(written_export(tmp_path / "same.csv", export_rows(madeup_out / "CART02_export.csv")), COLUMNS, filed=filed, taken=taken)
    assert (filed, taken) == ([], [])                                                      # the same rows in a new file
    filed, taken = [], []
    store.from_export(tmp_path / "same.csv", COLUMNS, filed=filed, taken=taken)
    assert (filed, taken) == ([], ["250202"])                                             # that file again: kept in same_rows.txt


# --- a print that shows nothing new is a look at the order at the time it was given (Task 43, the second review round, W16) ---

LONG_AGO = datetime(2026, 9, 1, 8, 0)                    # before any day these tests can run on


def long_ago(store: PoStore, po_number: str, look: str, minutes: int = 0) -> datetime:
    """Set the time of the version or the look, when it was filed and when a print of it was last given, to `minutes` after
    LONG_AGO, so that what the store notes next is later whatever the day of the run: that time."""
    moment = LONG_AGO.timestamp() + 60 * minutes
    for name in ("po.json", "given.txt"):
        if (store.root / po_number / look / name).exists():
            os.utime(store.root / po_number / look / name, (moment, moment))
    return datetime.fromtimestamp(moment).astimezone()


def test_a_print_given_again_is_a_look_at_the_order_at_the_time_it_was_given(madeup_out, tmp_path):
    """W16 (the check's findings 1 and 6): an export whose rows for the PO are those of its latest look files nothing, and the store
    notes the moment: given_at is then later than filed_at, which stays. So do the same rows in another file, the look's own file
    given again, and a PDF that is the latest version the store holds. An older look's file and an older PDF version note
    nothing: they are not the PO as it stands. A version or look never given again has given_at equal to filed_at."""
    store = PoStore(tmp_path / "store")
    rows = export_rows(madeup_out / "CART02_export.csv")
    [first] = store.from_export(madeup_out / "CART02_export.csv", COLUMNS)
    folder = tmp_path / "store" / "250202" / "e1"
    filed_first = long_ago(store, "250202", "e1")
    assert not (folder / "given.txt").exists() and store.given_at("250202", "e1") == store.filed_at("250202", "e1") == filed_first
    assert store.from_export(written_export(tmp_path / "same.csv", rows), COLUMNS) == [first]          # the same rows, another file
    assert (folder / "given.txt").read_bytes() == b"" and store.filed_at("250202", "e1") == filed_first
    assert store.given_at("250202", "e1") > filed_first and len(store.snapshots("250202")) == 1
    noted = long_ago(store, "250202", "e1", 5)
    assert (store.filed_at("250202", "e1"), store.given_at("250202", "e1")) == (noted, noted)
    for again in (madeup_out / "CART02_export.csv", tmp_path / "same.csv"):       # the look's own file, and the one it kept
        noted = long_ago(store, "250202", "e1", 5)
        assert store.from_export(again, COLUMNS) == [first] and store.given_at("250202", "e1") > noted, again.name
    [second] = store.from_export(written_export(tmp_path / "changed.csv", [rows[0] | {"unit_price": "12.60"}, *rows[1:]]), COLUMNS)
    noted = long_ago(store, "250202", "e1", 5)
    assert store.from_export(madeup_out / "CART02_export.csv", COLUMNS) == [first]     # the file of an older look: not the PO as it stands
    assert store.given_at("250202", "e1") == noted and not (tmp_path / "store" / "250202" / "e2" / "given.txt").exists()
    with pytest.raises(FileNotFoundError):
        store.given_at("250202", "e3")
    with pytest.raises(ValueError):
        store.given_at("250202", "../250202/e1")
    v1, v2 = (store.add(madeup_out / f"MU03_po_v{n}.pdf") for n in (1, 2))
    filed_v1, filed_v2 = long_ago(store, "250103", "v1"), long_ago(store, "250103", "v2", 1)
    assert store.add(madeup_out / "MU03_po_v2.pdf") == v2 and store.given_at("250103", "v2") > filed_v2 == store.filed_at("250103", "v2")
    assert store.add(madeup_out / "MU03_po_v1.pdf") == v1 and store.given_at("250103", "v1") == filed_v1      # an older version
    assert sorted(path.name for path in (tmp_path / "store" / "250103" / "v2").iterdir()) == ["given.txt", "po.json", "po.pdf"]
    assert sorted(path.name for path in (tmp_path / "store" / "250103" / "v1").iterdir()) == ["po.json", "po.pdf"]


def test_the_print_given_last_and_the_po_as_it_stands(madeup_out, tmp_path):
    """W16: what a document is compared with keeps to the filing times: a look filed before the latest PDF version does not stand
    again when an export with its rows comes after that PDF. The print given last (given_last) does follow it: the list judges a
    tick against the export that came after the PDF, and against the PDF once that is given after the export. A PO with PDFs
    only, or with looks only, has its latest one as both."""
    store = PoStore(tmp_path / "store")
    [look] = store.from_export(written_export(tmp_path / "250101.csv", rows_of("MU01")), COLUMNS)
    pdf = store.add(madeup_out / "MU01_po_v1.pdf")
    long_ago(store, "250101", "e1")
    long_ago(store, "250101", "v1", 5)
    assert store.as_it_stands("250101") == pdf and store.given_last("250101") == (pdf, "v1")
    store.from_export(written_export(tmp_path / "next day.csv", rows_of("MU01")), COLUMNS)      # the same rows, given after the PDF
    given = look.model_copy(update={"supplier": "S01"})
    assert store.given_last("250101") == (given, "e1")
    assert store.as_it_stands("250101") == pdf and store.candidates("250101", "cart") == store.candidates("250101", "confirmation") == [pdf]
    given_on(store, "250101", "e1", LONG_AGO + timedelta(minutes=10))                # that export came ten minutes after LONG_AGO
    store.add(madeup_out / "MU01_po_v1.pdf")                                         # the PDF, given after that
    assert store.given_last("250101") == (pdf, "v1") and store.as_it_stands("250101") == pdf
    given_on(store, "250101", "v1", LONG_AGO + timedelta(minutes=10))                # given at the same time: the PDF
    assert store.given_last("250101") == (pdf, "v1")
    given_on(store, "250101", "v1", LONG_AGO + timedelta(minutes=9))
    assert store.given_last("250101") == (given, "e1") and store.as_it_stands("250101") == pdf
    [alone] = store.from_export(madeup_out / "CART02_export.csv", COLUMNS)            # looks only, and PDFs only
    v1, v2 = (store.add(madeup_out / f"MU03_po_v{n}.pdf") for n in (1, 2))
    assert store.given_last("250202") == (alone, "e1") and store.given_last("250103") == (v2, "v2")
    assert store.given_last("250199") is None and store.given_last("..") is None


def test_a_file_that_lay_there_already_is_no_new_giving(madeup_out, tmp_path):
    """W16 and ruling E1 together: the scan handles a file that stays in the watched folder again at every pass. The store is told
    when the file came to the folder (`arrived`), and a file that lay there already when the store last noted a print that shows
    the PO so notes nothing: it is given once. A file that came later is given again. Without `arrived`, every call is a giving."""
    store = PoStore(tmp_path / "store")
    [look] = store.from_export(madeup_out / "CART02_export.csv", COLUMNS, arrived=LONG_AGO)
    pdf = store.add(madeup_out / "MU03_po_v1.pdf", arrived=LONG_AGO)
    for po_number, name in (("250202", "e1"), ("250103", "v1")):
        filed = long_ago(store, po_number, name, 10)                               # filed ten minutes after the file came
        for _ in range(2):                                                         # the same file at the passes that follow
            assert store.from_export(madeup_out / "CART02_export.csv", COLUMNS, arrived=LONG_AGO) == [look]
            assert store.add(madeup_out / "MU03_po_v1.pdf", arrived=LONG_AGO) == pdf
            assert store.given_at(po_number, name) == filed and not (store.root / po_number / name / "given.txt").exists()
    same = written_export(tmp_path / "same.csv", export_rows(madeup_out / "CART02_export.csv"))
    assert store.from_export(same, COLUMNS, arrived=LONG_AGO) == [look]             # the same rows, in a file that lay there too
    assert store.given_at("250202", "e1") == store.filed_at("250202", "e1")
    later = datetime.fromtimestamp(LONG_AGO.timestamp() + 3600)                     # an hour after: put there anew
    assert store.from_export(same, COLUMNS, arrived=later) == [look] and store.given_at("250202", "e1") > store.filed_at("250202", "e1")
    assert store.add(madeup_out / "MU03_po_v1.pdf", arrived=later) == pdf and store.given_at("250103", "v1") > store.filed_at("250103", "v1")
    noted = long_ago(store, "250202", "e1", 120)                                    # noted two hours after: that file lies there still
    assert store.from_export(same, COLUMNS, arrived=later) == [look] and store.given_at("250202", "e1") == noted
    assert store.from_export(same, COLUMNS) == [look] and store.given_at("250202", "e1") > noted          # no time told: a giving
