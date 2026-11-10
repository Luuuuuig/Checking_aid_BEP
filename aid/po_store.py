"""The PO store: every version of every purchase order the aid has been given, by PO number and version number.

add() files a PO PDF, read with the PO PDF reader (aid/readers/po_pdf.py), as a version of its PO. A cart pre-check has no
PO PDF yet, so from_export() files the POs of an ERP export instead, an Excel or CSV file read with the export reader
(aid/readers/po_export.py). Each version has a folder of its own:

    <root>/<PO number>/v<n>/po.pdf     the store's copy of the PDF (none for a version from an export)
    <root>/<PO number>/v<n>/po.json    the PoVersion, written last: a folder without it holds no version

The store reads its folders each time it is asked, so a new PoStore on the same root sees every version stored before it.
A file already stored for a PO (the same sha256) is not stored again: filing it again returns its stored version. The root
and every file given to the store are made absolute first, so po.json holds absolute paths, which read the same from any
working directory.

The total of a version is the sum over its lines of quantity times unit price divided by the price basis, worked out in
decimals from the normalised values. It is None when a line lacks one of the three, because the total of the other lines
is not the PO's total. above_authorization says whether the total is greater than the store's authorization limit, the
setting kept outside the repository; it is None when the total or the limit is unknown. It is worked out against the
store's limit each time a version is read, so that a changed setting holds for the versions stored before it.
"""
import os
import shutil
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel

from aid.case import sha256_file
from aid.readers.po_export import EXPORT_FIELDS, read_export  # noqa: F401 (EXPORT_FIELDS is importable from here too)
from aid.readers.po_export import is_folder_name as _folder_name      # one rule for a PO number as a folder, for the store and the reader
from aid.readers.po_pdf import read_po_pdf
from aid.schema import Document


class PoVersion(BaseModel):
    """One version of a PO as the store keeps it."""
    po_number: str
    version: int
    version_date: str | None                     # the PO's own date (its document_date); None when the PO shows none
    pdf_path: Path                               # the store's copy of the PDF; for a version from an export, the export file
    document: Document
    supplier: str | None
    total: Decimal | None                        # see the module docstring
    above_authorization: bool | None
    sha256: str | None = None                    # of the file the version was read from


class PoStore:
    """The versions under `root` (see the module docstring); their totals are compared with `authorization_limit`."""

    def __init__(self, root: Path, authorization_limit: Decimal | None = None):
        self.root = Path(root).resolve()
        self.authorization_limit = authorization_limit

    def add(self, pdf: Path) -> PoVersion:
        """File the PO PDF as a version of its PO and return that version.

        The PDF is read with read_po_pdf, whose ValueError (a scan, a PDF the template does not fit) is not caught; a reading
        without a PO number raises ValueError as well. The version number is the one the PDF prints, else the number of
        versions stored for the PO plus one. A PDF already stored returns its stored version. A version number that is
        stored from another file raises ValueError. A PDF that is refused leaves the store as it was."""
        pdf = Path(pdf).resolve()
        doc = read_po_pdf(pdf)
        if doc.po_reference.value is None:
            raise ValueError(f"{pdf.name}: no PO number found, so the PDF cannot be filed")
        return self._file(doc, pdf, sha256_file(pdf), copy=True)

    def versions(self, po_number: str) -> list[PoVersion]:
        """The stored versions of the PO, by ascending version number; [] for a PO the store does not hold."""
        if not _folder_name(po_number):
            return []
        return sorted((self._read(path) for path in (self.root / po_number).glob("v*/po.json")), key=lambda v: v.version)

    def find_by_lines(self, doc: Document) -> list[str]:
        """The PO numbers whose latest stored version has at least two different item codes that are also on the lines of
        `doc`, in the order of the numbers (the shorter number first, then by its text). The codes are compared as they
        stand."""
        if not self.root.is_dir():
            return []
        codes = _item_codes(doc)
        found = []
        for folder in sorted(self.root.iterdir(), key=lambda path: (len(path.name), path.name)):
            versions = self.versions(folder.name)
            if versions and len(codes & _item_codes(versions[-1].document)) >= 2:
                found.append(versions[-1].po_number)
        return found

    def from_export(self, path: Path, columns: dict[str, str], decimal_comma: bool = False, fill_down: bool = False) -> list[PoVersion]:
        """File the POs of an ERP export, an .xlsx or .csv file with one row per PO line, and return their versions in the
        order of the file.

        The file is read by read_export (aid/readers/po_export.py), whose docstring gives the rules. `columns` maps the
        aid's field names (EXPORT_FIELDS) to the column titles of the file; it must name po_number and item_code, a field
        it leaves out stays missing, and an empty map means every field whose name is a column title of the file.
        `decimal_comma` is the convention of the numbers the file writes as text, and with `fill_down` a row without a PO
        number takes the one that stands nearest above it. Each PO number gives one Document of kind "po", read by
        "export:<file name>": each value has its text as in the file and its normal form, and no source. Each Document
        is stored as the next version of its PO, with the export as its pdf_path, no date, and the supplier the export
        names for the PO, if it has a supplier column, unless the PO is stored from this same file already. A file or a
        map that the reader refuses raises its ValueError (ExportRefused) before anything is stored: the error names the
        column or the row, never what a cell holds."""
        path = Path(path).resolve()
        reading = read_export(path, columns, decimal_comma, fill_down)      # every row of the file, before anything is written
        digest = sha256_file(path)
        return [self._file(doc, path, digest, copy=False) for doc in reading.documents]

    def _file(self, doc: Document, source: Path, digest: str, copy: bool) -> PoVersion:
        """Store `doc`, read from `source`, as a version of its PO (see add); `copy` keeps a copy of the source."""
        po_number = doc.po_reference.value
        _check_folder_name(po_number, source)
        stored = self.versions(po_number)
        same = next((v for v in stored if v.sha256 == digest), None)
        if same is not None:
            return same
        hint = doc.version_hint.value
        number = len(stored) + 1 if hint is None else int(hint)       # the number the PO prints, else the next one
        if any(v.version == number for v in stored):
            raise ValueError(f"{source.name} is version {number} of PO {po_number}, which is stored from another file")
        folder = self.root / po_number / f"v{number}"
        folder.mkdir(parents=True, exist_ok=True)
        path = source
        if copy:
            path = folder / "po.pdf"
            shutil.copy2(source, path)
        total = _total(doc)
        version = PoVersion(po_number=po_number, version=number, version_date=doc.document_date.value, pdf_path=path, document=doc,
                            supplier=doc.supplier.value, total=total, above_authorization=self._above(total), sha256=digest)
        tmp = folder / "po.json.tmp"                 # po.json appears whole or not at all
        tmp.write_text(version.model_dump_json(indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, folder / "po.json")
        return version

    def _read(self, path: Path) -> PoVersion:
        version = PoVersion.model_validate_json(path.read_text(encoding="utf-8"))
        version.above_authorization = self._above(version.total)
        return version

    def _above(self, total: Decimal | None) -> bool | None:
        return None if total is None or self.authorization_limit is None else total > self.authorization_limit


def _check_folder_name(po_number: str | None, source: Path) -> None:
    """Refuse a PO number that cannot be the name of a folder on every system the aid runs on (is_folder_name of the export reader),
    from a PDF as from an export, before anything is filed. The error names the file and not the number, a value read from it."""
    if not _folder_name(po_number):
        raise ValueError(f"{source.name}: the PO number cannot be the name of a folder")


def _total(doc: Document) -> Decimal | None:
    """The total of the PO (see the module docstring)."""
    total = Decimal(0)
    for line in doc.lines:
        quantity, price, basis = line.quantity.value, line.unit_price.value, line.price_basis.value
        if quantity is None or price is None or basis is None:
            return None
        total += Decimal(quantity) * Decimal(price) / Decimal(basis)
    return total


def _item_codes(doc: Document) -> set[str]:
    return {line.item_code.value for line in doc.lines if line.item_code.value is not None}
