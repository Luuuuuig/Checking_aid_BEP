"""The upload source: a case from files the buyer uploads (design specification, section 2), for the evaluation, a demonstration or
as the fallback when the watched folder cannot be used. The batch runner (aid/batch.py) opens its cases this way; the review page
offers no upload yet."""
from datetime import datetime
from pathlib import Path

from aid.case import Case, open_case


def open_upload_case(cases_root: Path, files: list[Path], po_number: str | None, kind: str, now: datetime) -> Case:
    """Open a case for the files the buyer uploaded: kind "confirmation" or "cart", and the PO number when the buyer knows it.

    The first file is the supplier's document, which the pipeline reads. A PO number typed with spaces around it is taken without
    them, and a blank one is unknown, which opens the case as "unknown_<time>". Everything else is open_case's (aid/case.py): the files
    are copied into the case folder, and nothing is created when it refuses the call, with ValueError for a kind that does not exist,
    FileNotFoundError for a missing file and FileExistsError for a case of the same PO opened in the same second."""
    return open_case(cases_root, kind, files, (po_number or "").strip() or None, now)
