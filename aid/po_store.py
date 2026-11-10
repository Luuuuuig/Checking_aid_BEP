"""The PO store: what the aid knows of every purchase order, by PO number: the PDF versions of the PO, and the looks at it that
exports of the ERP gave.

add() files a PO PDF, read with the PO PDF reader (aid/readers/po_pdf.py), as a version of its PO: with the template of the
made-up layout, or, when it is given the layout of the settings (the setting po_layout), with that layout and with nothing else. A
cart pre-check has no PO PDF yet, so from_export() files the POs of an ERP export, an Excel or CSV file read with the export reader
(aid/readers/po_export.py), as looks: a look is the PO as an export showed it when it was filed. The versions and the looks of a
PO do not share a sequence, so a PDF that prints its version number is filed as that version whatever looks the PO has. Each has a
folder of its own:

    <root>/<PO number>/v<n>/po.pdf          the store's copy of the PDF of version n
    <root>/<PO number>/v<n>/po.json         the PoVersion, written last: a folder without it holds no version
    <root>/<PO number>/e<n>/po.json         the n-th look from an export, a PoVersion written whole or not at all, which keeps the
                                            export's path as its pdf_path and the export's sha256; no copy of the export
    <root>/<PO number>/e<n>/same_rows.txt   the sha256 of each later export file whose rows for the PO were this look's, one to a
                                            line, when there was one

versions() gives the PDF versions only, so that a store of PO PDFs gives version selection what it gave before looks existed, and
snapshots() gives the looks. The PO as it stands (as_it_stands) is its latest PDF version, or its latest look when that look was
filed after the latest PDF version or the PO has no PDF version; when the two were filed at the same time the PDF counts. When a
version or a look was filed is the modification time of its po.json (filed_at), so the order of the looks at a PO rests on the
times of the store's files. A copy of the store that does not keep those times gives each po.json the time it was copied, in the
order of the copy, and that can make an older look or an older PDF the PO as it stands: the store is never copied. candidates() is
what a document of a kind is matched against: the PO as it stands alone when that is a look and either the document is a cart or
the PO has no PDF version; otherwise the PDF versions, so that a confirmation is never matched against an export while its PO has a
PDF. A look is given (by snapshots, as_it_stands, candidates and from_export) with the number of the latest PDF version of its PO, 1
without one, and, when its export names no supplier, with the supplier of that version, so that it reads as the PO it shows; its
po.json keeps what the export gave: version 1, and the supplier the export names, if any.

An export files a look for each of its POs whose rows differ from those of the PO's latest look, each value of the Documents the
export reader gives compared in normal form (a number as a number, so 12.5 and 12.50 are one price); a PO whose rows are the same
files nothing, and the look keeps the file's sha256 in same_rows.txt. An export file is filed once: a file whose sha256 is the source
of a look of a PO, or is kept in a look's same_rows.txt, files nothing more for that PO, whatever looks came after it. So a file that
stays in the watched folder and is handled again at every pass, or an older export given again, never comes back on top of a newer
look. The store reads its folders each time it is asked, so a new PoStore on the same root sees every version and every look stored
before it. A PDF already stored for a PO (the same sha256) is not stored again: filing it again returns its stored version. The root
and every file given to the store are made absolute first, so po.json holds absolute paths, which read the same from any working
directory. When a PDF version is read, its pdf_path is the po.pdf in its own folder under the root as it is now, whatever po.json
holds, so that a store that was moved (moved, not copied: see above) keeps its PDFs; in place this is the path stored. A look keeps
the path of its export as po.json holds it.

The total of a version is the sum over its lines of quantity times unit price divided by the price basis, worked out in
decimals from the normalised values. It is None when a line lacks one of the three, because the total of the other lines
is not the PO's total. One exception: a PDF filed with a layout that says prices_per_piece (the PDF prints no price basis and
every price is per one unit) has each line that prints no price basis counted as per one, so that its total is known; a basis that
is printed and cannot be read still leaves the total unknown. The lines themselves keep their empty price basis: a basis written
into them would be compared with the supplier's document, which prints none either.

above_authorization says whether the total is greater than the store's authorization limit, the setting kept outside the
repository; it is None when the total or the limit is unknown. It is worked out against the store's limit each time a version
is read, so that a changed setting holds for the versions stored before it.
"""
import os
import re
import shutil
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import BaseModel

from aid.case import sha256_file
from aid.readers.po_export import EXPORT_FIELDS, read_export  # noqa: F401 (EXPORT_FIELDS is importable from here too)
from aid.readers.po_export import is_folder_name as _folder_name      # one rule for a PO number as a folder, for the store and the reader
from aid.readers.po_layout import PoLayout
from aid.readers.po_pdf import read_po
from aid.schema import DocLine, Document

_FOLDER = re.compile(r"[ve](?:0|[1-9][0-9]*)")        # the folder of a version ("v2", "v0" for a PDF that prints 0) or of a look ("e1")
SAME_ROWS = "same_rows.txt"                             # in a look's folder: the later export files with its rows (module docstring)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LINE_VALUES = tuple(name for name in DocLine.model_fields if name != "line_id")      # the values of a line that a look compares
_NUMBERS = ("quantity", "unit_price", "price_basis", "discount_pct", "net_price")     # those compared as numbers


class PoVersion(BaseModel):
    """One version of a PO as the store keeps it, or one look at a PO from an export (see the module docstring)."""
    po_number: str
    version: int                                 # a look is given the number of the latest PDF version of its PO, 1 without one
    version_date: str | None                     # the PO's own date (its document_date); None when the PO shows none
    pdf_path: Path                               # the store's copy of the PDF; for a look from an export, the export file
    document: Document
    supplier: str | None
    total: Decimal | None                        # see the module docstring
    above_authorization: bool | None
    sha256: str | None = None                    # of the file the version was read from


class PoStore:
    """The versions and the looks under `root` (see the module docstring); their totals are compared with `authorization_limit`."""

    def __init__(self, root: Path, authorization_limit: Decimal | None = None):
        self.root = Path(root).resolve()
        self.authorization_limit = authorization_limit

    def add(self, pdf: Path, layout: PoLayout | None = None) -> PoVersion:
        """File the PO PDF as a version of its PO and return that version.

        The PDF is read with read_po: without a layout with the template of the made-up layout, and with `layout`, the layout of
        the settings, with that layout alone. The reader's ValueError is not caught: a scan or a PDF the template does not fit,
        and with a layout a PDF that does not fit it or that fits and gives no PO number, more than one PO number or no order line.
        A reading without a PO number raises ValueError as well, and so does a PO number that cannot be the name of a folder, which
        with a layout is named by the setting and not by itself. The version number is the one the PDF prints, else the number of
        PDF versions stored for the PO plus one; the looks from exports count for neither. A PDF already stored returns its stored
        version. A version number that is stored from another PDF raises ValueError. A PDF that is refused leaves the store as it
        was. A layout that says prices_per_piece makes the total count each line that prints no price basis as per one unit (see
        the module docstring)."""
        pdf = Path(pdf).resolve()
        doc = read_po(pdf, layout)
        if doc.po_reference.value is None:
            raise ValueError(f"{pdf.name}: no PO number found, so the PDF cannot be filed")
        if layout is not None:
            _check_folder_name(doc.po_reference.value, pdf, by_layout=True)
        per_piece = layout is not None and layout.prices_per_piece
        return self._file(doc, pdf, sha256_file(pdf), copy=True, default_basis="1" if per_piece else None, by_layout=layout is not None)

    def versions(self, po_number: str) -> list[PoVersion]:
        """The stored PDF versions of the PO, by ascending version number, and never a look from an export; [] for a PO the store
        has no PDF of."""
        if not _folder_name(po_number):
            return []
        return sorted((self._read(path) for path in (self.root / po_number).glob("v*/po.json")), key=lambda v: v.version)

    def snapshots(self, po_number: str) -> list[PoVersion]:
        """The looks at the PO that exports gave, in the order they were filed (e1, e2, ...), each given as the module docstring
        says; [] for a PO no export named. They are never part of versions()."""
        if not _folder_name(po_number):
            return []
        latest = self.versions(po_number)[-1:]
        return [self._given(self._read(path), latest) for _, path in self._looks(po_number)]

    def filed_at(self, po_number: str, look: str) -> datetime:
        """When the version ("v2") or the look ("e1") of the PO was filed: the modification time of its po.json, in local time.
        ValueError for a name that is neither, FileNotFoundError for one the store does not hold."""
        if not _folder_name(po_number) or not _FOLDER.fullmatch(look):
            raise ValueError("filed_at takes a PO number that can be a folder name, and a version as v<n> or a look as e<n>")
        return datetime.fromtimestamp((self.root / po_number / look / "po.json").stat().st_mtime).astimezone()

    def as_it_stands(self, po_number: str) -> PoVersion | None:
        """The newest look at the PO: its latest PDF version, or its latest look when that was filed after the latest PDF version
        or the PO has no PDF version, the PDF counting when the two were filed at the same time (see the module docstring); None
        for a PO the store does not know."""
        versions, look = self._stands(po_number)
        return look if look is not None else (versions[-1] if versions else None)

    def candidates(self, po_number: str, kind: str) -> list[PoVersion]:
        """What a document of the kind ("confirmation" or "cart") is matched against: the PO as it stands alone when that is a look
        and either the document is a cart or the PO has no PDF version; otherwise the PDF versions (versions()). So a store of PO
        PDFs gives every document the list it gave before looks existed, and a confirmation is never matched against an export
        while its PO has a PDF. [] for a PO the store does not know."""
        versions, look = self._stands(po_number)
        return [look] if look is not None and (kind == "cart" or not versions) else versions

    def find_by_lines(self, doc: Document) -> list[str]:
        """The PO numbers whose PO as it stands (a PDF version or a look from an export) has at least two different item codes
        that are also on the lines of `doc`, in the order of the numbers (the shorter number first, then by its text). The codes
        are compared as they stand."""
        if not self.root.is_dir():
            return []
        codes = _item_codes(doc)
        found = []
        for folder in sorted(self.root.iterdir(), key=lambda path: (len(path.name), path.name)):
            standing = self.as_it_stands(folder.name)
            if standing is not None and len(codes & _item_codes(standing.document)) >= 2:
                found.append(standing.po_number)
        return found

    def from_export(self, path: Path, columns: dict[str, str], decimal_comma: bool = False, fill_down: bool = False,
                    filed: list[str] | None = None) -> list[PoVersion]:
        """File the POs of an ERP export, an .xlsx or .csv file with one row per PO line, as looks, and return the look of each PO
        of the file, filed or found, in the order of the file.

        The file is read by read_export (aid/readers/po_export.py), whose docstring gives the rules. `columns` maps the
        aid's field names (EXPORT_FIELDS) to the column titles of the file; it must name po_number and item_code, a field
        it leaves out stays missing, and an empty map means every field whose name is a column title of the file.
        `decimal_comma` is the convention of the numbers the file writes as text, and with `fill_down` a row without a PO
        number takes the one that stands nearest above it. Each PO number gives one Document of kind "po", read by
        "export:<file name>": each value has its text as in the file and its normal form, and no source. A file that the PO has
        taken before (the source of one of its looks, or kept in one's same_rows.txt) files nothing for it, whatever looks came
        after, and that look is returned. A Document whose rows are those of the PO's latest look, each value compared in normal
        form, files nothing either: that look is returned and keeps the file's sha256 in its same_rows.txt. Any other is filed as
        the next look of its PO (e1, e2, ...), with the export as its pdf_path, no date, and the supplier the export names for the
        PO, if it has a supplier column, and its PO number is appended to `filed` when a list is given. No v folder is made. Each
        look is returned as the module docstring says it is given. A file or a map that the reader refuses raises its ValueError
        (ExportRefused) before anything is stored: the error names the column or the row, never what a cell holds."""
        path = Path(path).resolve()
        reading = read_export(path, columns, decimal_comma, fill_down)      # every row of the file, before anything is written
        digest = sha256_file(path)
        looks = []
        for doc in reading.documents:
            look, new = self._look(doc, path, digest)
            looks.append(look)
            if new and filed is not None:
                filed.append(look.po_number)
        return looks

    def _look(self, doc: Document, source: Path, digest: str) -> tuple[PoVersion, bool]:
        """The look `doc`, read from the export `source` whose sha256 is `digest`, gives its PO (see from_export), as it is given,
        and whether it was filed now. A file the PO has taken before files nothing: the look whose source it is, or whose
        same_rows.txt keeps it. Rows the same as those of the latest look file nothing either, and the latest look keeps the file in
        its same_rows.txt. Any other rows are the next look, filed."""
        po_number = doc.po_reference.value
        _check_folder_name(po_number, source)
        looks = [(number, path, self._read(path)) for number, path in self._looks(po_number)]
        latest = self.versions(po_number)[-1:]
        for _, path, look in reversed(looks):
            if look.sha256 == digest or digest in _same_rows(path.parent):
                return self._given(look, latest), False      # filed once: this file never comes back on top of a later look
        if looks and _rows(looks[-1][2].document) == _rows(doc):
            _keep_same_rows(looks[-1][1].parent, digest)
            return self._given(looks[-1][2], latest), False
        total = _total(doc)
        look = PoVersion(po_number=po_number, version=1, version_date=doc.document_date.value, pdf_path=source, document=doc,
                         supplier=doc.supplier.value, total=total, above_authorization=self._above(total), sha256=digest)
        self._write(self.root / po_number / f"e{looks[-1][0] + 1 if looks else 1}", look)
        return self._given(look, latest), True

    def _looks(self, po_number: str) -> list[tuple[int, Path]]:
        """The number and the po.json of each look at the PO, by ascending number; a folder without po.json holds no look."""
        found = [(int(path.parent.name[1:]), path) for path in (self.root / po_number).glob("e*/po.json")
                 if _FOLDER.fullmatch(path.parent.name)]
        return sorted(found)

    def _stands(self, po_number: str) -> tuple[list[PoVersion], PoVersion | None]:
        """The PDF versions of the PO, and its latest look, as it is given, when that look is the PO as it stands (see
        as_it_stands); else None in its place."""
        if not _folder_name(po_number):
            return [], None
        versions, looks = self.versions(po_number), self._looks(po_number)
        if not looks:
            return versions, None
        number, path = looks[-1]
        if versions and self.filed_at(po_number, f"e{number}") <= self.filed_at(po_number, f"v{versions[-1].version}"):
            return versions, None                    # filed before the latest PDF version, or at the same time
        return versions, self._given(self._read(path), versions[-1:])

    def _given(self, look: PoVersion, latest: list[PoVersion]) -> PoVersion:
        """The look as the store gives it: with the number of the latest PDF version `latest` (a list of it, or empty), 1 without
        one, and with that version's supplier when its export named none."""
        if not latest:
            return look.model_copy(update={"version": 1})
        supplier = look.supplier if look.supplier is not None else latest[0].supplier
        return look.model_copy(update={"version": latest[0].version, "supplier": supplier})

    def _file(self, doc: Document, source: Path, digest: str, copy: bool, default_basis: str | None = None,
              by_layout: bool = False) -> PoVersion:
        """Store `doc`, read from `source`, as a version of its PO (see add); `copy` keeps a copy of the source. `default_basis` is
        the price basis the total counts for a line that states none (None: the total of such a PO is unknown). With `by_layout`,
        `doc` was read from the company's PDF with the layout of the settings, and a refusal names the settings, never its values."""
        po_number = doc.po_reference.value
        _check_folder_name(po_number, source)
        stored = self.versions(po_number)
        same = next((v for v in stored if v.sha256 == digest), None)
        if same is not None:
            return same
        hint = doc.version_hint.value
        number = len(stored) + 1 if hint is None else int(hint)       # the number the PO prints, else the next one
        if any(v.version == number for v in stored):
            if by_layout:
                raise ValueError(f"{source.name}: a version with its version number (po_layout.version_label) is stored for its PO "
                                 "(po_layout.po_label) from another file, so it cannot be filed")
            raise ValueError(f"{source.name} is version {number} of PO {po_number}, which is stored from another file")
        folder = self.root / po_number / f"v{number}"
        folder.mkdir(parents=True, exist_ok=True)
        path = source
        if copy:
            path = folder / "po.pdf"
            shutil.copy2(source, path)
        total = _total(doc, default_basis)
        version = PoVersion(po_number=po_number, version=number, version_date=doc.document_date.value, pdf_path=path, document=doc,
                            supplier=doc.supplier.value, total=total, above_authorization=self._above(total), sha256=digest)
        self._write(folder, version)
        return version

    def _write(self, folder: Path, version: PoVersion) -> None:
        """Write the PoVersion as the po.json of the folder, made when it is not there: through a temporary file and a rename, so
        that po.json appears whole or not at all."""
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "po.json.tmp"
        tmp.write_text(version.model_dump_json(indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, folder / "po.json")

    def _read(self, path: Path) -> PoVersion:
        """The version or the look that po.json at path holds. A PDF version (a folder v<n>) has the store's copy as its pdf_path,
        the po.pdf in its own folder: it is given as that file under the store's root as it is now, whatever path po.json holds, so
        that a store that was moved (the aid from a folder, aid/portable.py) still has its PDFs; in place the path is the one
        stored. A look (a folder e<n>) keeps the path of its export."""
        version = PoVersion.model_validate_json(path.read_text(encoding="utf-8"))
        version.above_authorization = self._above(version.total)
        if path.parent.name.startswith("v") and version.pdf_path.name == "po.pdf":
            version.pdf_path = path.parent / "po.pdf"
        return version

    def _above(self, total: Decimal | None) -> bool | None:
        return None if total is None or self.authorization_limit is None else total > self.authorization_limit


def _check_folder_name(po_number: str | None, source: Path, by_layout: bool = False) -> None:
    """Refuse a PO number that cannot be the name of a folder on every system the aid runs on (is_folder_name of the export reader),
    from a PDF as from an export, before anything is filed. The error names the file, and for a PDF read with the layout of the
    settings the setting the number was read after (po_layout.po_label); never the number, a value read from the file."""
    if not _folder_name(po_number):
        if by_layout:
            raise ValueError(f"{source.name}: the PO number after the PO label of the layout (po_layout.po_label) cannot be the name of "
                             "a folder")
        raise ValueError(f"{source.name}: the PO number cannot be the name of a folder")


def po_total(doc: Document, default_basis: str | None = None) -> Decimal | None:
    """The total of the PO (see the module docstring); a line that prints no price basis is counted per `default_basis`, if one is
    given. A basis that is printed and cannot be read is no basis of one: the total is then unknown."""
    total = Decimal(0)
    for line in doc.lines:
        printed = line.price_basis
        quantity, price = line.quantity.value, line.unit_price.value
        basis = printed.value or (default_basis if printed.text is None else None)
        if quantity is None or price is None or basis is None:
            return None
        total += Decimal(quantity) * Decimal(price) / Decimal(basis)
    return total


_total = po_total                                # the name the store's own methods and the tests knew it by


def _item_codes(doc: Document) -> set[str]:
    return {line.item_code.value for line in doc.lines if line.item_code.value is not None}


def _same_rows(folder: Path) -> set[str]:
    """The sha256 of the export files that a look's same_rows.txt keeps; none when it has no such file. A line that is no sha256 (a
    line a crash cut off) is passed over."""
    try:
        lines = (folder / SAME_ROWS).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return set()
    return {line.strip() for line in lines if _SHA256.fullmatch(line.strip())}


def _keep_same_rows(folder: Path, digest: str) -> None:
    """Add the sha256 to the look's same_rows.txt, made when it is not there, as a line of its own also after a line a crash cut
    off. The look's po.json is not touched, so the time it was filed stays."""
    path = folder / SAME_ROWS
    with open(path, "a+", encoding="utf-8", newline="\n") as f:
        f.seek(0)
        text = f.read()
        f.write(("\n" if text and not text.endswith("\n") else "") + digest + "\n")


def _rows(doc: Document) -> tuple:
    """The rows of a PO read from an export as two looks are compared (see from_export): the supplier the export names, and each
    line in its order with its line id and the normal form of each of its values, a number as a number."""
    return doc.supplier.value, [(line.line_id, *(_normal(name, getattr(line, name).value) for name in _LINE_VALUES))
                                for line in doc.lines]


def _normal(name: str, value: str | None) -> str | Decimal | None:
    """A value in normal form as two looks compare it: a number as a Decimal, so that 12.5 and 12.50 are one price; any other value,
    and a number that does not read as one, as its text."""
    if value is not None and name in _NUMBERS:
        try:
            return Decimal(value)
        except InvalidOperation:
            return value
    return value
