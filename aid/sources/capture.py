"""The capture source: a case from a webshop cart page that the buyer captured before ordering, for the pre-check (design
specification, sections 2 and 4). The page is saved from the browser as HTML or taken as a screenshot, and comes with the number of
the PO the buyer built the cart for.

Which PO that is (cart_order), for a cart read from a page. The buyer makes the order in Exact, gives its export, and fills the cart
with every item of that order: the aid is sure of an order only on evidence that fits that. Three words first.
- An order is sent when the store holds a PDF version of it, or when a supplier's confirmation of it came in: a case that is no
  cart's and no try-out's (aid/tryout_mark.py) holds its number, found by the number recognition (no po_found_by in its
  case.json), and its result is not failed. Such a case is looked for by the order's number in the name of its folder
  ("<PO number>_<time>"), so that only the cases of the orders in question are read. An order without a PDF version is not thereby
  unsent, since sending the order from Exact is optional; it counts as not sent until its PDF or a confirmation of it comes in.
- An order was filed today when the PO as it stands was filed (PoStore.filed_last) on the calendar day of the capture, by the clock
  of the capture.
- An order holds exactly the cart's items when the set of the item codes of the PO as it stands equals the set of the cart's item
  codes and neither has a line without an item code.
The rule:
- (a) The number the page states, when the store knows that PO, as it stands from a PDF or from an export: found, whatever items it
  shares with the cart. A number the page states that the store does not know decides nothing (a webshop prints numbers of its
  own): the items decide.
- (b) Otherwise the aid is sure by the items when exactly one stored order that was filed today holds exactly the cart's items, and
  that order is not sent: found, BY_ITEMS. One item code is enough here: an order of one line, filed today, is found for a cart of
  that one item.
- (c) Otherwise the choice is a guess, when at least one stored order fits. The candidates are the orders that share at least two
  item codes with the cart and the orders that hold exactly the cart's items, each as it stands. The order the cart is compared with
  is chosen among them in this order: first those filed today, when there are any; then those that hold exactly the cart's items;
  then those that are not sent; then those of the page's supplier; then the one whose PO as it stands was filed last, the later in
  the order of the numbers among orders filed at the same time. The guess is ALREADY_SENT when that order is sent and NOT_SURE when
  it is not; the capture case keeps which in its case.json, so that nothing is prepared and the buyer decides (the pipeline's rule
  G01, aid/pipeline.py).
- (d) No candidate: the cart has no order, (None, None).
The supplier is no evidence for being sure, only for the choice of a guess: a webshop's heading seldom equals the supplier's name in
Exact."""
import json
import os
from datetime import datetime
from pathlib import Path

from aid.case import Case, load_case, open_case
from aid.pipeline import RESULT_FILE
from aid.po_store import PoStore
from aid.schema import Document
from aid.tryout_mark import tryout_case

BY_ITEMS = "cart items"                                  # found: the one order filed today that holds exactly the cart's items, not sent
NOT_SURE = "cart items, not sure"                        # a guess: the order the cart is compared with is not sent
ALREADY_SENT = "cart items of an order already sent"     # a guess: the order the cart is compared with is sent
GUESSES = (NOT_SURE, ALREADY_SENT)                       # how the order of a cart was guessed (case.json's po_found_by)


def cart_order(cart: Document, store: PoStore, cases_root: Path, now: datetime) -> tuple[str | None, str | None]:
    """The PO the cart is compared with, and how it was found when not as the number the page states: BY_ITEMS, or one of GUESSES
    (see the module docstring); (None, None) for a cart of no stored PO. `cases_root` holds the cases, by which an order with a
    supplier's confirmation counts as sent, and `now` is the time of the capture, whose calendar day is today."""
    stated = cart.po_reference.value
    if stated and store.as_it_stands(stated) is not None:
        return stated, None
    codes, whole = _item_codes(cart), _whole(cart)
    standing, exact = {}, set()
    for po in store.as_they_stand():                     # one pass over the store: the candidates, and which hold exactly the cart's items
        theirs = _item_codes(po.document)
        same = whole and theirs == codes and _whole(po.document)
        if same or len(codes & theirs) >= 2:
            standing[po.po_number] = po
            exact |= {po.po_number} if same else set()
    if not standing:
        return None, None
    today = {number for number in standing if _on_the_day(store.filed_last(number), now)}
    sent = _Sent(store, cases_root)
    sure = today & exact
    if len(sure) == 1 and not sent(*sure):
        return sure.pop(), BY_ITEMS
    supplier = cart.supplier.value
    among = list(standing)                               # in the order of the numbers
    among = [number for number in among if number in today] or among
    among = [number for number in among if number in exact] or among
    among = [number for number in among if not sent(number)] or among
    among = [number for number in among if supplier and standing[number].supplier == supplier] or among
    # the one filed last; max keeps the first among equals, so the numbers go in from the last to the first
    chosen = max(reversed(among), key=store.filed_last)
    return chosen, ALREADY_SENT if sent(chosen) else NOT_SURE


class _Sent:
    """Whether an order is sent (see the module docstring), asked of one store and one cases root, each order once. The folders of
    the cases root are listed once, when the first order without a PDF version is asked about, and only the cases whose folder
    names the order are read."""

    def __init__(self, store: PoStore, cases_root: Path):
        self.store, self.cases_root = store, Path(cases_root)
        self.known: dict[str, bool] = {}
        self.folders: dict[str, list[str]] | None = None     # the order's number -> the folders of the cases root that name it

    def __call__(self, number: str) -> bool:
        if number not in self.known:
            self.known[number] = bool(self.store.versions(number)) or self.confirmed(number)
        return self.known[number]

    def confirmed(self, number: str) -> bool:
        """Whether a supplier's confirmation of the order came in: a case of the order that is no cart's and no try-out's, whose
        number was found by the number recognition, and whose result is not failed. A case that has no result yet, or whose result
        cannot be read, is not known to have failed and counts; a folder whose case.json cannot be read is no case."""
        if self.folders is None:
            self.folders = {}
            try:
                names = os.listdir(self.cases_root)
            except OSError:
                names = []
            for name in names:
                self.folders.setdefault(name.rpartition("_")[0], []).append(name)
        for name in sorted(self.folders.get(number, ())):
            try:
                case = load_case(self.cases_root, name)
            except (OSError, ValueError):
                continue
            if case.kind == "cart" or case.po_number != number or case.po_found_by is not None or _failed(case):
                continue
            if not tryout_case(case):                    # asked last: it reads the first page of the case's document
                return True
        return False


def _failed(case: Case) -> bool:
    """Whether the saved result of the case is failed."""
    try:
        result = json.loads((case.root / "steps" / RESULT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(result, dict) and result.get("status") == "failed"


def _on_the_day(filed: datetime | None, now: datetime) -> bool:
    """Whether the moment the store gives, in local time, falls on the calendar day of `now`: in the zone of `now` when it has one,
    else in local time, as a clock without a zone tells it."""
    if filed is None:
        return False
    return (filed.astimezone(now.tzinfo) if now.tzinfo is not None else filed.astimezone()).date() == now.date()


def open_capture_case(cases_root: Path, file: Path, po_number: str, now: datetime, found_by: str | None = None) -> Case:
    """Open a case of kind "cart" for the captured page `file`, which the pipeline reads, as open_upload_case (aid/sources/upload.py)
    opens an upload. A PO number typed with spaces around it is taken without them, and a blank one is unknown, which opens the case
    as "unknown_<time>". found_by says how the order was guessed, one of GUESSES, when it was: case.json then holds it as
    po_found_by, and only then. Everything else is open_case's (aid/case.py): the file is copied into the case folder with its
    modification time, and nothing is created when it refuses the call, with FileNotFoundError for a missing file and
    FileExistsError for a case of the same PO opened in the same second."""
    return open_case(cases_root, "cart", [file], (po_number or "").strip() or None, now, found_by)


def _item_codes(doc: Document) -> set[str]:
    return {line.item_code.value for line in doc.lines if line.item_code.value is not None}


def _whole(doc: Document) -> bool:
    """Whether the document has lines and each of them states an item code."""
    return bool(doc.lines) and all(line.item_code.value is not None for line in doc.lines)
