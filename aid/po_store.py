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
    <root>/<PO number>/<v or e><n>/given.txt  empty; its time is when a print that shows the PO so was last given again, when one was

versions() gives the PDF versions only, so that a store of PO PDFs gives version selection what it gave before looks existed, and
snapshots() gives the looks. The PO as it stands (as_it_stands) is its latest PDF version, or its latest look when that look was
filed after the latest PDF version or the PO has no PDF version; when the two were filed at the same time the PDF counts. When a
version or a look was filed is the modification time of its po.json (filed_at), so the order of the looks at a PO rests on the
times of the store's files; filed_last gives when the PO as it stands was filed, by which the pre-check tells whether an order
was filed on the day of a cart and which of several orders was filed last, and as_they_stand gives every PO as it stands, among
which it looks for the order of a cart (aid/sources/capture.py). A print that shows nothing new is a look at the order all the same: when the store is given an export
whose rows for the PO are those of its latest look, that look's own export file again, or the latest PDF version it holds, it
files nothing and notes the moment in the folder of that look or version (the time of given.txt), and given_at is the later of
the two times: when the store was last given a print that shows the PO so. What a document is compared with does not follow that
moment: as_it_stands and candidates rest on the filing times alone, as before it was kept. The list of changes to enter in Exact
does (aid/execution/by_hand.py): it judges a tick against the print given last (given_last: the latest PDF version or the latest
look, by given_at), so that an export given after a tick ends it when it still shows the old value, whether its rows are new or
not. Only the latest look and the latest PDF version note the moment. A file that stays in the watched folder and is handled
again at every pass is given once: the scan says when each file came to the folder (`arrived`), and a file that lay there
already when the store last noted a print that shows the PO so notes nothing.
A copy of the store that does not keep those times gives each po.json the time it was copied, in the
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
from collections.abc import Iterator
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
GIVEN = "given.txt"                                     # in a version's or a look's folder: its time is when the store was last given a
                                                        # print that shows the PO as that version or look holds it (given_at)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LINE_VALUES = tuple(name for name in DocLine.model_fields if name != "line_id")      # the values of a line that a look compares
_NUMBERS = ("quantity", "unit_price", "price_basis", "discount_pct", "net_price")     # those compared as numbers


class VersionHeld(ValueError):
    """add's refusal of a PO PDF whose version number the store holds from another file: the same order printed again with other
    bytes, or a changed order that the ERP printed under the same version number. A class of its own, in both its wordings (with a
    layout and without one), so that the scan tells it apart by its class and never by its text (aid/sources/mailbox.py)."""


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

    def add(self, pdf: Path, layout: PoLayout | None = None, filed: list[str] | None = None,
            arrived: datetime | None = None) -> PoVersion:
        """File the PO PDF as a version of its PO and return that version.

        The PDF is read with read_po: without a layout with the template of the made-up layout, and with `layout`, the layout of
        the settings, with that layout alone. The reader's ValueError is not caught: a scan or a PDF the template does not fit,
        and with a layout a PDF that does not fit it or that fits and gives no PO number, more than one PO number or no order line.
        A reading without a PO number raises ValueError as well, and so does a PO number that cannot be the name of a folder, which
        with a layout is named by the setting and not by itself. The version number is the one the PDF prints, else the number of
        PDF versions stored for the PO plus one; the looks from exports count for neither. A PDF already stored returns its stored
        version. A version number that is stored from another PDF raises VersionHeld, a ValueError. A PDF that is refused leaves the
        store as it was. A layout that says prices_per_piece makes the total count each line that prints no price basis as per one
        unit (see the module docstring). When a list `filed` is given, the PO number is appended to it when the PDF was filed now,
        and not when the store held it already, so that the scan can tell a PDF filed before (aid/sources/mailbox.py). The latest
        version given again notes the moment (given_at), unless `arrived`, when the file came to the place it is given from, is
        not after the moment the store last noted: the same file handled again (see the module docstring)."""
        pdf = Path(pdf).resolve()
        doc = read_po(pdf, layout)
        if doc.po_reference.value is None:
            raise ValueError(f"{pdf.name}: no PO number found, so the PDF cannot be filed")
        if layout is not None:
            _check_folder_name(doc.po_reference.value, pdf, by_layout=True)
        per_piece = layout is not None and layout.prices_per_piece
        return self._file(doc, pdf, sha256_file(pdf), copy=True, default_basis="1" if per_piece else None, by_layout=layout is not None,
                          filed=filed, arrived=arrived)

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

    def given_at(self, po_number: str, look: str) -> datetime:
        """When the store was last given a print that shows the PO as the version ("v2") or the look ("e1") holds it: when it was
        filed (filed_at), or later, when the same PDF, an export with the look's rows, or the look's own export file came again
        (the time of GIVEN in its folder; see the module docstring). A print that shows nothing new is still a look at the order
        at the time it was given. Raises as filed_at does."""
        filed = self.filed_at(po_number, look)
        try:
            again = datetime.fromtimestamp((self.root / po_number / look / GIVEN).stat().st_mtime).astimezone()
        except FileNotFoundError:
            return filed
        return max(filed, again)

    def given_last(self, po_number: str) -> tuple[PoVersion, str] | None:
        """The print of the PO that the store was given last, with the name of its folder ("v2", "e1"): its latest PDF version or
        its latest look, the one with the later given_at, the PDF version when the two are equal; None for a PO the store does
        not know. Unlike as_it_stands it follows a print that came again: an export with the rows of the latest look, given after
        a newer PDF version was filed, is the last look at the order, while a document is still compared with that PDF version."""
        if not _folder_name(po_number):
            return None
        versions, looks = self.versions(po_number), self._looks(po_number)
        if not looks:
            return (versions[-1], f"v{versions[-1].version}") if versions else None
        number, path = looks[-1]
        if versions and self.given_at(po_number, f"e{number}") <= self.given_at(po_number, f"v{versions[-1].version}"):
            return versions[-1], f"v{versions[-1].version}"
        return self._given(self._read(path), versions[-1:]), f"e{number}"

    def filed_last(self, po_number: str) -> datetime | None:
        """When the PO as it stands was filed (see as_it_stands): filed_at of its latest PDF version, or of its latest look when
        that was filed after it or the PO has no PDF version; None for a PO the store does not know."""
        if not _folder_name(po_number):
            return None
        versions, looks = self.versions(po_number), self._looks(po_number)
        filed = [self.filed_at(po_number, f"v{versions[-1].version}")] if versions else []
        filed += [self.filed_at(po_number, f"e{looks[-1][0]}")] if looks else []
        return max(filed, default=None)

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

    def as_they_stand(self) -> Iterator[PoVersion]:
        """Each PO of the store as it stands (as_it_stands), in the order of the numbers (the shorter number first, then by its
        text); none for a store without a folder."""
        if not self.root.is_dir():
            return
        for folder in sorted(self.root.iterdir(), key=lambda path: (len(path.name), path.name)):
            standing = self.as_it_stands(folder.name)
            if standing is not None:
                yield standing

    def find_by_lines(self, doc: Document) -> list[str]:
        """The PO numbers whose PO as it stands (a PDF version or a look from an export) has at least two different item codes
        that are also on the lines of `doc`, in the order of the numbers (the shorter number first, then by its text). The codes
        are compared as they stand."""
        codes = _item_codes(doc)
        return [standing.po_number for standing in self.as_they_stand() if len(codes & _item_codes(standing.document)) >= 2]

    def from_export(self, path: Path, columns: dict[str, str], decimal_comma: bool = False, fill_down: bool = False,
                    filed: list[str] | None = None, taken: list[str] | None = None,
                    arrived: datetime | None = None) -> list[PoVersion]:
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
        PO, if it has a supplier column, and its PO number is appended to `filed` when a list is given. A PO that had taken the file
        before has its number appended to `taken`, when a list is given, so that the scan can tell an export file filed before
        (aid/sources/mailbox.py). A file that files nothing because the PO's latest look took it before, or has its rows, notes
        the moment for that look (given_at), unless `arrived`, when the file came to the place it is given from, is not after the
        moment the store last noted: the same file handled again (see the module docstring). No v folder is made. Each look is
        returned as the module docstring says it is given. A file or a map
        that the reader refuses raises its ValueError (ExportRefused) before anything is stored: the error names the column or the row,
        never what a cell holds."""
        path = Path(path).resolve()
        reading = read_export(path, columns, decimal_comma, fill_down)      # every row of the file, before anything is written
        digest = sha256_file(path)
        looks = []
        for doc in reading.documents:
            look, how = self._look(doc, path, digest, arrived)
            looks.append(look)
            if how == "filed" and filed is not None:
                filed.append(look.po_number)
            if how == "taken" and taken is not None:
                taken.append(look.po_number)
        return looks

    def _look(self, doc: Document, source: Path, digest: str, arrived: datetime | None = None) -> tuple[PoVersion, str]:
        """The look `doc`, read from the export `source` whose sha256 is `digest`, gives its PO (see from_export), as it is given,
        and how: "taken" when the PO has taken the file before, which files nothing (the look whose source it is, or whose
        same_rows.txt keeps it, is given); "same" when its rows are those of the latest look, which files nothing either, the latest
        look keeping the file in its same_rows.txt; "filed" when the rows are the next look, filed now."""
        po_number = doc.po_reference.value
        _check_folder_name(po_number, source)
        looks = [(number, path, self._read(path)) for number, path in self._looks(po_number)]
        latest = self.versions(po_number)[-1:]
        for _, path, look in reversed(looks):
            if look.sha256 == digest or digest in _same_rows(path.parent):
                if path == looks[-1][1]:                     # the latest look's own file again: the order is seen so once more
                    self._note_given(po_number, path.parent.name, arrived)
                return self._given(look, latest), "taken"    # filed once: this file never comes back on top of a later look
        if looks and _rows(looks[-1][2].document) == _rows(doc):
            _keep_same_rows(looks[-1][1].parent, digest)
            self._note_given(po_number, looks[-1][1].parent.name, arrived)
            return self._given(looks[-1][2], latest), "same"
        total = _total(doc)
        look = PoVersion(po_number=po_number, version=1, version_date=doc.document_date.value, pdf_path=source, document=doc,
                         supplier=doc.supplier.value, total=total, above_authorization=self._above(total), sha256=digest)
        self._write(self.root / po_number / f"e{looks[-1][0] + 1 if looks else 1}", look)
        return self._given(look, latest), "filed"

    def _note_given(self, po_number: str, look: str, arrived: datetime | None) -> None:
        """Note in the folder of the version or the look that the store is given, now, a print that shows the PO as it holds it
        there: GIVEN is made or its time renewed (given_at). The po.json is not touched, so the time it was filed stays. Nothing
        is noted for a file that came to its place (`arrived`) no later than the moment the store last noted for that version or
        look: it lay there already, and is the same giving handled again."""
        if arrived is not None and arrived.timestamp() <= self.given_at(po_number, look).timestamp():
            return
        path = self.root / po_number / look / GIVEN
        path.touch()
        os.utime(path, None)

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
              by_layout: bool = False, filed: list[str] | None = None, arrived: datetime | None = None) -> PoVersion:
        """Store `doc`, read from `source`, as a version of its PO (see add); `copy` keeps a copy of the source. `default_basis` is
        the price basis the total counts for a line that states none (None: the total of such a PO is unknown). With `by_layout`,
        `doc` was read from the company's PDF with the layout of the settings, and a refusal names the settings, never its values.
        The PO number is appended to `filed`, when a list is given, once the version is written, and not for a PDF stored already."""
        po_number = doc.po_reference.value
        _check_folder_name(po_number, source)
        stored = self.versions(po_number)
        same = next((v for v in stored if v.sha256 == digest), None)
        if same is not None:
            if same == stored[-1]:                   # the latest version, given again: a look at the order now
                self._note_given(po_number, f"v{same.version}", arrived)
            return same
        hint = doc.version_hint.value
        number = len(stored) + 1 if hint is None else int(hint)       # the number the PO prints, else the next one
        if any(v.version == number for v in stored):
            if by_layout:
                raise VersionHeld(f"{source.name}: a version with its version number (po_layout.version_label) is stored for its PO "
                                  "(po_layout.po_label) from another file, so it cannot be filed")
            raise VersionHeld(f"{source.name} is version {number} of PO {po_number}, which is stored from another file")
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
        if filed is not None:
            filed.append(po_number)
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
