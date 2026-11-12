"""The review page of the checking aid: the cases that wait for the buyer, one case per screen, and the buyer's decisions on them
(design specification, section 5, "The review page"). Dutch, with an English switch.

The review page is three modules. This one holds the view: the functions that read the case folder (aid/case.py) for the page, and
decide, which records a decision; aid/review/close.py carries the decisions out (close_case, undo); aid/review/page.py is the gradio
page (build_app, page_rows). close_case, undo, build_app, import_gradio and page_rows are imported here as well, so that the review
page has one module to import from.

    list_open_cases   the cases whose saved result (steps/08_result.json) is in review, in the order they were opened, but a case
                      of the capture server that is marked as replaced (marked_replaced)
    load_case_view    one case as the page shows it (CaseView)
    decide            a decision of the buyer on a review item, appended to <case root>/decisions.jsonl

The view. doc_pages are the pages of the supplier's document (the case's first input file) as PNG images at 100 dpi, rendered with
render_page of aid/readers/pdf_text.py; a PNG cart is its own one page and an HTML cart has none, its rows being in the lists.
po_pages are the pages of the PDF of the selected PO version, none for a look from an export; when step 01 names the store's copy
of a PDF that is no longer there (the folder of the aid was moved), the page looks the version up in the store as it is now. The
three lists are built from the result's findings: a row per flag, per match and per uncompared item; then `flags` gets a row for each
review item that no row carries (an item without a finding, such as "new supplier" on a case without findings, or a second item on
one finding), but a settled one (the item of a message not sent that was sent since: ReviewItem.settled), which the count of a
case's items leaves out as well. A row's
item_id is that of the first review item that names its finding (RI1, RI2, ...), else the finding's id (F1, ...; U1, ... as the
routing numbers the uncompared items; M1, M2, ... for the matches, which have no id), and the page offers the four decisions on
every row whose item_id is a review item ("ask" only where decide takes it). A row shows its values in normal form (else as printed; a match its value on both
sides, an uncompared item none), its sources as "p<page> r<line>: <text>", and its reason: the review item's, else the flag's
detail or the uncompared item's reason. thumbnail is None, kept for a later crop of the cited line. reapproval_banner is true when
the PO is above the buyer's authorization, by the routing context of step 06, or by the selected version of step 01 without it.
entered names the values entered in Exact that the case was compared with in place of the selected version's own, as step
03_entered of the case holds them (aid/pipeline.py), and entered_unread says that the list of them could not be read when the case
ran; the page says either above the lists. A case without that step has neither.
"""
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel

from aid.case import Case
from aid.comparison import Findings, Flag, Match, Uncompared
from aid.messaging import _append_line           # the lines of a file of records, appended as the messaging module appends them
from aid.pipeline import LOOKS, RESULT_FILE, CaseResult, ReviewItem, asked_by_the_run
from aid.po_store import PoStore, PoVersion
from aid.readers.pdf_text import page_count, render_page
from aid.routing import CaseContext, findings_by_id
from aid.routing import _shown                   # a value as the routing's questions show it
from aid.schema import DocValue
from aid.tryout_mark import case_is_tryout, tryout_case

DECISIONS_FILE = "decisions.jsonl"
CAPTURE_FILE = "capture.json"                    # what the capture server keeps of its case, in the case folder (aid/capture_server.py)
DPI = 100
Decision = Literal["confirm", "keep", "ask", "unresolved"]


class CaseSummary(BaseModel):
    """A case in the list of the open cases."""
    case_id: str
    po_number: str | None
    supplier: str | None
    status: str
    opened_at: str                               # from case.json
    items: int                                   # the number of review items, a settled one left out (ReviewItem.settled)
    tryout: bool = False                         # whether the case is made from a try-out (aid/tryout_mark.py)


class FlagRow(BaseModel):
    """One row of a list of the page (see the module docstring)."""
    item_id: str
    field: str
    line: str                                    # the PO line ids joined by ", ", else the document line ids
    po_value: str
    doc_value: str
    po_source: str
    doc_source: str
    thumbnail: bytes | None
    reason: str = ""


class EnteredValue(BaseModel):
    """A value entered in Exact that the case was compared with in place of the selected version's own (step 03_entered)."""
    line: str                                    # the line id in the selected version
    field: str                                   # "price", "delivery_date" or "quantity"
    value: str                                   # the entered value, in normal form


class CaseView(BaseModel, arbitrary_types_allowed=True):
    """One case as the page shows it (see the module docstring)."""
    summary: CaseSummary
    doc_pages: list[bytes]
    po_pages: list[bytes]
    flags: list[FlagRow]
    matches: list[FlagRow]
    uncompared: list[FlagRow]
    decisions: dict[str, str]                    # item_id -> its last decision
    reapproval_banner: bool
    entered: list[EnteredValue] = []             # the values entered in Exact that took the place of the selected version's own
    entered_unread: bool = False                 # the list of them could not be read when the case ran


# --- the open cases and the view ---

def list_open_cases(cases_root: Path) -> list[CaseSummary]:
    """The cases under cases_root whose saved result is in review, in the order they were opened (then by id). A folder without a
    case.json or a result is not listed, and neither is a result that cannot be read, which the pipeline's count of closed cases
    skips as well, nor a case of the capture server that is marked as replaced (marked_replaced)."""
    return _list_cases(cases_root, lambda root, result: result.status == "review")


def marked_replaced(root: Path) -> bool:
    """Whether the case of the folder is a case of the capture server that is marked as replaced by another: its capture.json holds
    replaced_by, which the server writes before it finishes the case as replaced (aid/capture_server.py, _replace). From the mark
    on the page offers the case no more, and a decision, a close and an undo on it are refused, as on a case finished as replaced
    (aid/review/close.py), also while that finish is still missing: a decision saved then would never be carried out. A folder
    without the file, or whose file cannot be read as an object, is not marked."""
    try:
        meta = json.loads((Path(root) / CAPTURE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(meta, dict) and meta.get("replaced_by") is not None


def _list_cases(cases_root: Path, listed: Callable[[Path, CaseResult], bool]) -> list[CaseSummary]:
    """The cases under cases_root for which listed(case root, saved result) is true, as list_open_cases gives them, each with whether
    it is made from a try-out (case_is_tryout of aid/tryout_mark.py), which is asked of these cases only. A capture case marked as
    replaced (marked_replaced) is never among them."""
    summaries = []
    for path in Path(cases_root).glob(f"*/steps/{RESULT_FILE}"):
        root = path.parents[1]
        try:
            meta = json.loads((root / "case.json").read_text(encoding="utf-8"))
            result = CaseResult.model_validate_json(path.read_text(encoding="utf-8"))
            summary = _summary(root.name, meta, result)
        except (OSError, ValueError, KeyError):
            continue
        if listed(root, result) and not marked_replaced(root):
            summaries.append(summary.model_copy(update={"tryout": case_is_tryout(cases_root, root.name)}))
    return sorted(summaries, key=lambda summary: (summary.opened_at, summary.case_id))


def load_case_view(case: Case, store: PoStore | None = None) -> CaseView:
    """The case as the page shows it (see the module docstring). A case without a result raises FileNotFoundError. When the store's
    copy of the selected version's PDF (its po.pdf) is not where step 01 names it (the folder of the aid was moved), the same
    version is looked up in `store`, the store as it is now, which gives the po.pdf of the version's own folder; a look from an
    export names its export, which has no pages and is not looked up."""
    result = _saved_result(case)
    meta = json.loads((case.root / "case.json").read_text(encoding="utf-8"))
    flags, matches, uncompared = _rows(result)
    version = _selected_version(case, result)
    pdf = version.pdf_path if version is not None else None
    if version is not None and store is not None and Path(pdf).name == "po.pdf" and not Path(pdf).is_file():
        pdf = next((stored.pdf_path for stored in store.versions(version.po_number) if stored.version == version.version), pdf)
    summary = _summary(case.case_id, meta, result).model_copy(update={"tryout": tryout_case(case)})
    entered, unread = _entered(case)
    return CaseView(summary=summary, doc_pages=_pages(case.input_files[0] if case.input_files else None),
                    po_pages=_pages(pdf), flags=flags, matches=matches, uncompared=uncompared,
                    decisions={item_id: record["decision"] for item_id, record in _decisions(case).items()},
                    reapproval_banner=bool(_context(case, result).po_above_authorization), entered=entered, entered_unread=unread)


def _entered(case: Case) -> tuple[list[EnteredValue], bool]:
    """The values of the case's step 03_entered, and whether that step says that the list could not be read; none and False for a
    case without the step, or whose step cannot be read."""
    try:
        step = case.read_step("entered")
        values = [EnteredValue(line=one["line_id"], field=one["field"], value=one["entered"]) for one in step["replaced"]]
        return values, step.get("unread") is True
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return [], False


def _summary(case_id: str, meta: dict, result: CaseResult) -> CaseSummary:
    return CaseSummary(case_id=case_id, po_number=meta.get("po_number"), supplier=result.supplier, status=result.status,
                       opened_at=meta["opened_at"], items=sum(not item.settled for item in result.review_items))


def _rows(result: CaseResult) -> tuple[list[FlagRow], list[FlagRow], list[FlagRow]]:
    """The rows of the three lists (see the module docstring)."""
    findings = result.findings or Findings(version=None, flags=[], matches=[], uncompared=[])
    by_id = findings_by_id(findings)
    naming: dict[str, ReviewItem] = {}           # finding id -> the first review item that names it
    for item in result.review_items:
        if item.finding_id is not None:
            naming.setdefault(item.finding_id, item)
    flags, uncompared = [], []
    for finding_id, finding in by_id.items():
        item = naming.get(finding_id)
        (flags if isinstance(finding, Flag) else uncompared).append(_row(item.item_id if item else finding_id, finding, item))
    matches = [_row(f"M{n}", match) for n, match in enumerate(findings.matches, 1)]
    shown = {row.item_id for row in flags + uncompared}
    flags += [_row(item.item_id, by_id.get(item.finding_id), item) for item in result.review_items if item.item_id not in shown and not item.settled]
    return flags, matches, uncompared


def _row(item_id: str, finding: Flag | Match | Uncompared | None, item: ReviewItem | None = None) -> FlagRow:
    """The row of a finding, or of a review item without one; item is the review item the row is decided by, if any."""
    if isinstance(finding, Match):
        return FlagRow(item_id=item_id, field=finding.field, line=_line(finding), po_value=finding.value, doc_value=finding.value,
                       po_source="", doc_source="", thumbnail=None)
    flag = finding if isinstance(finding, Flag) else None
    po_value = flag.po_value if flag else (item.po_value if item else None)
    doc_value = flag.doc_value if flag else (item.doc_value if item else None)
    own = flag.detail if flag else (finding.reason if finding is not None else "")
    field = finding.field if finding is not None else (item.field or "" if item else "")
    return FlagRow(item_id=item_id, field=field, line=_line(finding) if finding is not None else "", po_value=_shown(po_value),
                   doc_value=_shown(doc_value), po_source=_cited(po_value), doc_source=_cited(doc_value), thumbnail=None,
                   reason=item.reason if item is not None else own)


def _line(finding: Flag | Match | Uncompared) -> str:
    return ", ".join(finding.po_line_ids) or ", ".join(finding.doc_line_ids)


def _cited(value: DocValue | None) -> str:
    """Where a value is printed, as "p<page> r<line>: <text>"; "" without a source."""
    if value is None or value.source is None:
        return ""
    return f"p{value.source.page} r{value.source.line}: {value.source.text}"


def _pages(path: Path | None) -> list[bytes]:
    """The pages of a document as PNG images at DPI: those of a PDF, the bytes of a PNG, none of anything else (an HTML cart, an
    export) or of a file that is not there."""
    if path is None or not Path(path).is_file():
        return []
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return [render_page(path, number, dpi=DPI) for number in range(1, page_count(path) + 1)]
    return [Path(path).read_bytes()] if suffix == ".png" else []


def _saved_result(case: Case) -> CaseResult:
    return CaseResult.model_validate_json((case.root / "steps" / RESULT_FILE).read_text(encoding="utf-8"))


def _selected_version(case: Case, result: CaseResult) -> PoVersion | None:
    """The version the run selected, as step 01 holds it; None without a selected version or without step 01."""
    if result.version is None or result.version.version is None:
        return None
    try:
        versions = case.read_step("po_versions")["versions"]
    except FileNotFoundError:
        return None
    return next((PoVersion.model_validate(version) for version in versions if version["version"] == result.version.version), None)


def _context(case: Case, result: CaseResult) -> CaseContext:
    """The routing context of the case as step 06 holds it; without step 06, one made from the selected version of step 01 (its
    item codes and whether it is above the authorization) and the case."""
    try:
        return CaseContext.model_validate(case.read_step("route")["context"])
    except FileNotFoundError:
        pass
    version = _selected_version(case, result)
    lines = version.document.lines if version is not None else []
    return CaseContext(supplier=result.supplier or "", po_number=case.po_number or "",
                       version=result.version.version if result.version is not None else None, po_date={},
                       po_item_code={line.line_id: line.item_code.value for line in lines if line.item_code.value is not None},
                       po_above_authorization=version.above_authorization if version is not None else None, supplier_lead_time_days=None,
                       cases_seen_for_supplier=0, document_path=str(case.input_files[0]) if case.input_files else "", kind=case.kind)


def _decisions(case: Case) -> dict[str, dict]:
    """The last decision of each item in decisions.jsonl, by item id; {} without the file. A line that is not a decision raises
    ValueError naming the file and the line: a damaged record is not read around."""
    return _decision_log(case)[0]


def _decision_log(case: Case) -> tuple[dict[str, dict], dict[str, int], int]:
    """The last decision of each item in decisions.jsonl by item id (_decisions), the number of the line each stands on, and the
    number of the last line that holds a decision (0 without one). The file is only ever added to, so a line number tells a
    decision made after a close from the one that close carried out, also when both were made within one second."""
    path = case.root / DECISIONS_FILE
    if not path.exists():
        return {}, {}, 0
    latest, lines, last = {}, {}, 0
    for number, line in enumerate(path.read_bytes().split(b"\n"), start=1):
        if line.strip():
            try:
                record = json.loads(line)
                latest[record["item_id"]] = record
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError(f"{path}, line {number}: not a decision") from error
            lines[record["item_id"]], last = number, number
    return latest, lines, last


# --- the decisions ---

class NothingAsked(ValueError):
    """"ask" on a case made from a try-out document (aid/tryout_mark.py): the aid sends nothing about it to a supplier."""


def decide(case: Case, item_id: str, decision: Decision, article_update: bool = False) -> None:
    """Record the buyer's decision on a review item: append {"item_id", "decision", "article_update", "at"} to <case
    root>/decisions.jsonl, "at" being the time now to the second. The last decision of an item counts; close_case carries it out.

    "confirm" takes the supplier's value into the system, "keep" keeps the PO's value, "ask" asks the supplier, "unresolved" leaves
    the item open. On an item without a finding, "confirm" and "keep" both acknowledge it. article_update, on a price, also updates
    the article's price. Refused with ValueError, recording nothing: a decision that is not one of the four, an item that is not a
    review item of the case, and "ask" on an item without a finding (there is no value to ask about; but the item of a bounce or
    a silence of a question the run asked itself takes it, and the close asks that question again: asked_by_the_run of
    aid/pipeline.py, ruling C4 of the clean-up of Task 44), on a cart (the buyer
    corrects a cart or its PO before ordering) or on an item that asks him to look in Exact, because a write was made and not
    verified or its outcome is not known (it is no question for the supplier: "confirm" lets the change stand and "keep" has the
    close put the old value back, see close_case). "ask" on any item of a case made from a try-out document is refused with
    NothingAsked, a ValueError of its own, since the aid sends nothing about a try-out (aid/tryout_mark.py). Any decision on a
    capture case that is marked as replaced (marked_replaced) is refused too: another case took its place. The capture server is
    another process and marks without a lock, so the mark is looked at twice: before anything else, and again after the line was
    appended. When the case was marked meanwhile, the line is taken out again (_take_back) and the decision refused in the same
    way, so that no decision is saved in a case that is finished as replaced; the server, for its part, reads a case again after
    its mark and takes the mark back when it then finds a decision (aid/capture_server.py, _replace)."""
    if decision not in get_args(Decision):
        raise ValueError(f"unknown decision {decision!r}: expected one of {', '.join(get_args(Decision))}")
    if marked_replaced(case.root):
        raise ValueError(f"case {case.case_id} was replaced by another case, so no decision is taken on it")
    result = _saved_result(case)
    item = next((item for item in result.review_items if item.item_id == item_id), None)
    if item is None:
        raise ValueError(f"case {case.case_id} has no review item {item_id!r}")
    if decision == "ask" and tryout_case(case):
        raise NothingAsked(f"case {case.case_id} is made from a try-out document, so nothing about it is sent to a supplier")
    if decision == "ask" and item.finding_id is None and asked_by_the_run(result, item) is None:
        raise ValueError(f"{item_id} is about the whole case and names no value, so there is no question to ask the supplier")
    if decision == "ask" and case.kind == "cart":
        raise ValueError("a cart is not asked about: the buyer corrects the cart or the PO before ordering")
    if decision == "ask" and item.reason in LOOKS:
        raise ValueError(f"{item_id} asks for a look in Exact after a write of the aid, which is no question for the supplier")
    record = {"item_id": item_id, "decision": decision, "article_update": bool(article_update), "at": datetime.now().isoformat(timespec="seconds")}
    path, line = case.root / DECISIONS_FILE, json.dumps(record, ensure_ascii=False)
    _append_line(path, line)
    if marked_replaced(case.root):               # marked between the first look and the append: the line is not left in the case
        _take_back(path, line)
        raise ValueError(f"case {case.case_id} was replaced by another case, so no decision is taken on it")


def _take_back(path: Path, line: str) -> None:
    """Take the line that this decision just appended out of the file again, and no other: the file's tail is compared with the
    line, and only when the tail is that line is it cut off. A file that holds nothing after that is removed. When another line
    was appended behind it in the same instant, the file is left as it is. (On a file that did not end with a newline the
    append put one before its line; that newline stays.)"""
    own = line.encode("utf-8") + b"\n"
    with open(path, "rb+") as f:
        data = f.read()
        if not data.endswith(own):
            return
        f.truncate(len(data) - len(own))
    if len(data) == len(own):
        path.unlink(missing_ok=True)


# The close and the page, imported here after the view they build on (see the module docstring).
from aid.review.close import close_case, undo  # noqa: E402,F401
from aid.review.page import build_app, import_gradio, page_rows  # noqa: E402,F401
