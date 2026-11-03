"""The capture source: a case from a webshop cart page that the buyer captured before ordering, for the pre-check (design
specification, sections 2 and 4). The page is saved from the browser as HTML or taken as a screenshot, and comes with the number of
the PO the buyer built the cart for."""
from datetime import datetime
from pathlib import Path

from aid.case import Case, open_case


def open_capture_case(cases_root: Path, file: Path, po_number: str, now: datetime) -> Case:
    """Open a case of kind "cart" for the captured page `file`, which the pipeline reads, as open_upload_case (aid/sources/upload.py)
    opens an upload. A PO number typed with spaces around it is taken without them, and a blank one is unknown, which opens the case
    as "unknown_<time>". Everything else is open_case's (aid/case.py): the file is copied into the case folder with its modification
    time, and nothing is created when it refuses the call, with FileNotFoundError for a missing file and FileExistsError for a case
    of the same PO opened in the same second."""
    return open_case(cases_root, "cart", [file], (po_number or "").strip() or None, now)
