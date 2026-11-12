"""The tab "Prijslijst" of the review page, without the page library, so that it is tested without a page (aid/review/page.py builds the
tab from it, its strings are in aid/review/i18n.py): a supplier's price list compared with the purchase prices that Exact holds, and
its changes put on the list "In Exact invoeren". The aid changes nothing in Exact.

What Exact holds. The buyer gives the export of a supplier's articles from Exact on the tab; aid/pricelist.py keeps its prices per
supplier (store_current) and gives the current prices of a supplier (current_prices): the stored prices with every article price
entered or seen on the list since. checked_export refuses an export before it is read: a file that is no .xlsx or .csv file, and an
empty file. refusal_text gives the sentence of each refusal, of this module (ListRefused) and of the export reader (ExportRefused of
aid/readers/po_export.py): another type, an empty file, columns that differ from those set in part 3 of the block "Koppeling met
Exact", and for a file that cannot be read the sentence that part 2 of that block has for one. state_line and filed_line are what
the tab says of the stored prices of a supplier and of an export just stored; each count has its form for one. An export in a
workbook with more than one visible sheet with data, an export that gave no article and a supplier's name that is none are refused
by the stored prices themselves (aid/pricelist.py), each with its sentence here.

The price list. list_table reads a list as the tab reads one, and the import is given its rows: of a workbook the one sheet the
buyer sees (a hidden sheet never, the one visible sheet that holds data whatever its place, and a workbook with more than one
such sheet refused with their names), of a CSV file the text in the encodings and at the separators of the export of articles,
cut at the mark that cuts its first filled row, the row of its titles, into the most cells.
list_titles gives the column titles of a price list, the cells of its first filled row, which is where the import looks for
them; it refuses a file of another type, an empty file and a file that cannot be read. columns_for fills the tab's five column
fields (LIST_COLUMNS): from the map kept for the supplier (kept_columns of aid/pricelist.py: kept beside the stored prices when a
list is carried out, never in the settings file), each field with its kept title when the file holds it; without a kept map,
each field with the file's title that equals the aid's own name of the column. column_map makes the map of the fields, or names
what is missing: no column for the item code or the price, or one column chosen for two fields; checked_columns refuses a chosen title
that stands twice in the list's row of titles. A
sentence that names a file or a column title takes the name as the page makes it showable, so that no mark of it is read as markup.
A column of the file that has the aid's own name is read under that name also when its field is left empty, as the import reads a
list.

The view and the press. preview runs the import (import_price_list of aid/pricelist.py) with a writer that records and writes
nothing: no file is made or changed anywhere. carry_out runs the same import through the prepared writer into
<cases_root>/prepared, the write path of every change, and appends its entries to the price list's own write log,
<cases_root>/pricelists/<day>/write_log.jsonl, as the import's command does, so that a change of a list can be put back from the
tab "In Exact invoeren". The same list carried out twice prepares its rows twice, and the list shows one row per article and
value (aid/execution/by_hand.py).

What the tab keeps off the list. Both runs give the import the tab's own check of a price that would change (held_back). A
price more than FACTOR times the purchase price in Exact, or less than one part in FACTOR of it, is not put on the list (DIFFERS):
that is what a price per 100 or per box looks like when the list's column for what a price is per was not pointed out. A row
whose article another supplier holds too is not put on the list (SHARED): the list keeps one price per article, whichever
supplier asked, so two suppliers' prices of one article cannot both stand on it, and the buyer enters such a price in Exact
himself. The tab knows another supplier by its stored prices (also_held of aid/pricelist.py) or by an open change of the article
on the list that its price list or its case asked (others_asking), and the outcome of the row names it (others_named). And a
row whose price no press can put on the list is not shown as changing: the list holds that value as ticked after the
supplier's export (TICKED), or the buyer took it off the list (DROPPED); after_press works that out from the list as it was
read once (listed, ArticleLook of aid/execution/by_hand.py), with nothing written. All four are rows passed over, each with an
outcome of its own and its price on the list. older_asked gives the articles of which a row kept off the list as shared or as
differing strongly leaves an older price on the list: an open change that an earlier price list of the same supplier asked,
with another price; older_line is the sentence that the count line of a view and the note of a press add for them.

What the view shows as changing can be put on the list. When the list holds a value as entered or seen from before the
supplier's newer export, and the export shows another price, the view says that the price changes; the press then asks it again
(ask_again): the marks of the change that holds the value are taken away, as "Weer op de lijst zetten" does, by BY. A mark made
after the export stays, and a dropped change stays dropped; ask_again reads the list once, and again only after it wrote a
mark. plan gives what a view showed as plain values, by which the page tells whether a press would carry out the view it showed. shown
gives the rows of the tab's table (headers): the article, the purchase price in Exact, the
price on the list and the outcome, each outcome by its label (OUTCOMES) and never by the import's own reason or a rule code; a
row without an item code is named by its row, and a price reads with the decimal comma in Dutch. The price on the list is
shown for a price that changes and for one that stays the same; the import keeps no price of a row it passes over. count_line
counts the prices that change, those that stay the same and the rows passed over, with the advice to ask for a current list when
a row is expired, and with the sentence that no price which differs strongly is put on the list when such a row occurs;
carried_line says how many price changes stand on the list after a press, which on_list counts: the entries of the press that an
open change of the list holds. After a press shown gives the table again from the press's own result, where a change that could
not be prepared has the label "failed", and not_prepared_line names the articles of those rows for the note. A write log that
cannot be written does not stop a press: carry_out does the rest and raises LogNotWritten with the result at its end, for the
page to say that the aid cannot put these changes back itself.
"""
import uuid
from collections.abc import Callable, Collection, Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

from aid.execution.by_hand import ARTICLE_KIND, ASKED, FOLDER, ArticleLook, changes, mark, open_changes, place_of, same_value
from aid.execution.ops import WriteOp
from aid.execution.writer import UNDO_RULE_ID, PreparedWriter, WriteLogEntry
from aid.pricelist import _log        # an import's entries appended to its write log, as the import's command appends them
from aid.pricelist import _xlsx_table     # the rows of a workbook, each cell as the import reads it
from aid.pricelist import (SURPLUS, ImportResult, NameRefused, NoArticle, OnlyHidden, SeveralSheets, StoredPrices, import_price_list, list_of,
                           odd_row, same_supplier)
from aid.readers.po_export import DELIMITERS, EXPORT_SUFFIXES, ExportRefused, csv_rows, csv_text
from aid.review.exact_settings import refusal_text as export_refusal_text
from aid.review.exact_settings import sheets_text
from aid.review.hand_list import PRICELISTS
from aid.review.i18n import STRINGS

TAB = "prijslijst"                               # the id of the tab on the page
LIST_COLUMNS = ("item_code", "price", "basis", "valid_from", "valid_to")      # the columns of a price list that the tab has a field for
REQUIRED = ("item_code", "price")                # the columns that must be chosen
FACTOR = 3                                       # a list price more than this many times the purchase price in Exact, or less than
#                                                  one part in this many of it, is not put on the list (see held_back)
SHARED, DIFFERS = "with another supplier", "differs strongly"         # the reasons of the tab's own check of a price that would change
TICKED, DROPPED = "ticked on the list", "taken off the list"          # (held_back): a row with one of them is kept off the list
BY = "assistant"                                 # who a mark "reopened" of the press is by, in marks.jsonl (ask_again)
# The label of each reason for which a row is passed over, by the part of its key after "pricelist_outcome:": the import's reasons
# and the four of the tab's own check; a row whose write failed has the label "failed", and a price that changes the label "changes".
OUTCOMES = {"unchanged": "unchanged", "unknown article": "unknown_article", "unreadable price": "unreadable_price",
            "unknown price basis": "other_basis", "not yet valid": "not_yet_valid", "expired": "expired", "duplicate row": "duplicate",
            "no item code": "no_item_code", "unreadable valid_from": "unreadable_valid_from", "unreadable valid_to": "unreadable_valid_to",
            SHARED: "shared", DIFFERS: "differs", TICKED: "ticked", DROPPED: "dropped"}
_KEPT_OFF = (SHARED, DIFFERS, TICKED, DROPPED)   # the reasons of the tab's own check: the import keeps the list price of such a row
_DECIMAL = {"nl": ",", "en": "."}
_HELD = ("entered", "seen")                      # the states of a change by which the list says that Exact holds its value
_SEPARATORS = " \t\r\n;,\ufeff"                  # what a CSV file without a filled cell holds: its marks, spaces and line ends
_COLUMNS_DIFFER = ("map", "no_column", "double_column")       # the export reader's reasons that mean: not the columns set in part 3


class ListRefused(ValueError):
    """A file that the tab refuses before it is compared or stored. `reason` is "type" (no .xlsx or .csv file), "empty", "unreadable",
    "sheets" (a workbook with more than one visible sheet with data: `names` their names), "hidden" (a workbook whose only data
    stands on a hidden sheet), "heading" (a price list with a heading above its column titles) or "double_column" (a chosen title
    stands twice in the row of titles: `column` the title and `row`
    the row). The text names the reason and never the file or a value."""

    def __init__(self, reason: str, column: str | None = None, row: int | None = None, names: list[str] | None = None):
        super().__init__(f"the file is refused: {reason}")
        self.reason, self.column, self.row, self.names = reason, column, row, names or []


class LogNotWritten(OSError):
    """The price list's own write log could not be written after the changes of a press were prepared (carry_out). `result` is the
    import's result: its changes stand on the list "In Exact invoeren", and the aid cannot put them back itself later."""

    def __init__(self, result: ImportResult):
        super().__init__("the write log of the price list could not be written")
        self.result = result


class Listed(NamedTuple):
    """What a view or a press of the tab reads of the list "In Exact invoeren", in one read (listed)."""
    asked: dict                                  # the open change of each article that has one (ArticleLook.open)
    after: Callable[[str, str], str]             # the state a row of the price list would end with on the list (after_press)


def listed(cases_root: Path, stored_at: str | None) -> Listed:
    """The list "In Exact invoeren" as a view or a press of the tab looks at it, its two files read once: the open change of each
    article, and the look ahead of a press for a supplier whose export was stored at `stored_at`."""
    look = ArticleLook(Path(cases_root) / FOLDER)
    return Listed(look.open(), after_press(look, stored_at))


class _Recorder:
    """A writer that records and writes nothing: the entry a prepared change would get, in no file."""
    name = "preview"

    def apply(self, op: WriteOp, case_id: str, rule_id: str, evidence: dict, now: datetime) -> WriteLogEntry:
        return WriteLogEntry(entry_id=uuid.uuid4().hex, case_id=case_id, op=op, status="prepared", previous=op.old_value, new=op.new_value,
                             rule_id=rule_id, evidence=evidence, timestamp=now.isoformat())


# --- the view and the press ---

def preview(list_file: Path, supplier: str, current: dict[str, str], column_map: dict[str, str], now: datetime,
            shared: Collection[str] = frozenset(), after: Callable[[str, str], str] | None = None) -> ImportResult:
    """What carrying the price list out would do, with nothing written anywhere: the import's result with the entry of each price that
    changes, made by a writer that records only. The list is read as the tab reads one (list_table), and a price that would change
    passes the tab's own check (held_back, with `shared`, the articles that another supplier holds too, and `after`, the state a
    row would end with on the list). A list that cannot be read raises a ValueError: the import's, or the refusal of list_table."""
    return import_price_list(Path(list_file), supplier, current, _Recorder(), now, column_map or None, table=list_table(Path(list_file)),
                             check=held_back(shared, after))


def carry_out(list_file: Path, supplier: str, current: dict[str, str], column_map: dict[str, str], cases_root: Path, now: datetime,
              shared: Collection[str] = frozenset(), stored_at: str | None = None,
              after: Callable[[str, str], str] | None = None) -> ImportResult:
    """Carry the price list out: each price that changes and that the tab's check lets through (held_back, with `shared` and `after`) prepared
    in <cases_root>/prepared, the write path of every change, and its entry appended to the price list's write log,
    <cases_root>/pricelists/<the day of now>/write_log.jsonl, as the import's command logs an import. With `stored_at`, when the
    supplier's export was stored, a change that the list holds as entered from before that export is asked again (ask_again). A
    list that cannot be read raises a ValueError before anything is written. A write log that cannot be written does not stop the
    press: the changes were prepared and stand on the list, so the rest is done as well, and LogNotWritten is raised at the end
    with the result, for the page to say that the aid cannot put these changes back itself."""
    result = import_price_list(Path(list_file), supplier, current, PreparedWriter(Path(cases_root) / FOLDER), now, column_map or None,
                               table=list_table(Path(list_file)), check=held_back(shared, after))
    try:
        _log(Path(cases_root) / PRICELISTS / f"{now:%Y-%m-%d}", result.updated)
        logged = True
    except OSError:
        logged = False
    if stored_at is not None:
        ask_again(result, cases_root, stored_at, now)
    if not logged:
        raise LogNotWritten(result)
    return result


def held_back(shared: Collection[str] = frozenset(), after: Callable[[str, str], str] | None = None) -> Callable[[str, str, str], str | None]:
    """The tab's own check of a price that would change, as import_price_list takes one: (the item code, the purchase price in Exact,
    the list price) -> the reason for which the row is not put on the list, or None. DIFFERS: the list price is more than FACTOR
    times the purchase price or less than one part in FACTOR of it, which is what a price per 100 or per box read as a price per
    piece looks like; no purchase price of zero is compared so, a list price above it differs strongly. SHARED: another supplier
    holds the article too (`shared`: its stored prices hold it, or the list holds an open change of the article that it asked),
    and the list "In Exact invoeren" keeps one price per article, so two suppliers' prices of one article cannot both stand on it.
    A price that differs strongly is named so whoever else holds the article, since a price a hundred times too high is not one to
    enter by hand either. TICKED and DROPPED: no press can put the price on the list, by the state its row would end with there
    (`after`, see after_press): the list holds the value as ticked after the supplier's export, or the buyer took it off the list."""
    def check(code: str, old: str, new: str) -> str | None:
        was, asked = Decimal(old), Decimal(new)
        if asked > FACTOR * was or asked * FACTOR < was:
            return DIFFERS
        if code in shared:
            return SHARED
        state = after(code, new) if after is not None else "open"
        return DROPPED if state == "dropped" else TICKED if state in (*_HELD, "already") else None
    return check


def after_press(look: ArticleLook, stored_at: str | None) -> Callable[[str, str], str]:
    """The state that a row of a price list would end with on the list "In Exact invoeren", worked out with nothing written:
    (the item code, the list price) -> the state of the change that would hold a new row asking that price (`look`, the list as it
    was read once), after the press asked again what it asks again (ask_again, with `stored_at`, when the supplier's export was
    stored): the marks of the change that holds the value taken away, turn by turn, as long as that change was marked at or
    before that moment. "open" means the press puts the price on the list; "entered", "seen" and "already" that the list holds it
    as ticked after the export; "dropped" that the buyer took it off the list."""
    since = datetime.fromisoformat(stored_at).timestamp() if stored_at is not None else None

    def state(code: str, new: str) -> str:
        without: set[str] = set()
        while True:
            found = look.asked(code, new, without)
            at = next(index for index, change in enumerate(found) if ASKED in change.ids)
            held = _holder(found, at)
            if not _asked_again(held, since) or set(held.ids) <= without:
                return found[at].state
            without |= set(held.ids)
    return state


def _holder(found: list, at: int):
    """The change by which the list says that Exact holds the value of the change at `at` of `found` (changes in the order of the
    file): that change itself when it is entered or seen; for one the list takes as in Exact already, the last entered or seen
    change of its place before it; else None."""
    if found[at].state in _HELD:
        return found[at]
    if found[at].state == "already":
        return next((one for one in reversed(found[:at]) if place_of(one) == place_of(found[at]) and one.state in _HELD), None)
    return None


def _asked_again(held, since: float | None) -> bool:
    """Whether the press takes the marks of `held` away (see ask_again): it holds a value from at or before the moment the
    supplier's export was stored (`since`); a mark made after it is the newer look and stays."""
    return held is not None and since is not None and held.marked_at is not None and datetime.fromisoformat(held.marked_at).timestamp() <= since


def ask_again(result: ImportResult, cases_root: Path, stored_at: str, now: datetime) -> None:
    """After a press: put back on the list, as open, each change of the press that the list would not show as open because it
    holds that value as entered or seen from before the supplier's export was stored (`stored_at`). The view compared the list
    with that newer export, which shows another price than the one ticked: Exact was changed outside the aid, a price was typed
    wrong in Exact and ticked, or the tick came before the price was entered. So the price is asked again. The marks of the change
    that holds the value (the change the new row joined, or, for a row that the list takes as in Exact already, the last entered
    or seen change of the article before it) are taken away with the mark "reopened", as "Weer op de lijst zetten" writes it, by
    BY instead of the buyer. A mark made after the export was stored is the newer look and stays: Exact holds that value as far
    as the aid knows. A dropped change stays dropped. The list is read once, and again only after a mark was written: a press of
    thousands of changes with nothing to ask again reads it once."""
    prepared = Path(cases_root) / FOLDER
    since = datetime.fromisoformat(stored_at).timestamp()

    def read() -> tuple[list, dict[str, int]]:
        found = changes(prepared)[0]
        return found, {entry_id: index for index, change in enumerate(found) for entry_id in change.ids}
    found, places = read()
    for entry in result.updated:
        for _ in range(len(result.updated) + 8):         # each turn takes the marks of one change away; a place has few
            at = places.get(entry.entry_id)
            held = _holder(found, at) if at is not None else None
            if not _asked_again(held, since) or not mark(prepared, held.ids, "reopened", now, by=BY):
                break
            found, places = read()                       # the marks changed, and with them the states of the changes


def plan(result: ImportResult) -> list[list[str | None]]:
    """What a view showed, as plain values in the order of the file: [the article, the old price, the new price] for a price that
    changes and [the article or the row, the reason] for a row passed over. Two results with the same plan carry out the same."""
    return [[outcome.op.article_code, outcome.previous, outcome.new] if isinstance(outcome, WriteLogEntry) else list(outcome)
            for outcome in result.outcomes]


def on_list(result: ImportResult, cases_root: Path) -> int:
    """How many of the changes a press prepared stand on the list "In Exact invoeren": the entries that an open change holds, those
    that the press asked again (ask_again) with the others. A change the buyer dropped before for the same value is prepared again
    and stays off the list."""
    listed = {entry_id for change in open_changes(Path(cases_root) / FOLDER) for entry_id in change.ids}
    return sum(1 for entry in result.updated if entry.entry_id in listed)


# --- what the table and its lines show ---

def headers(language: str) -> list[str]:
    """The column titles of the tab's table."""
    return [STRINGS[language][f"pricelist_header:{column}"] for column in ("article", "current", "list", "outcome")]


def shown(result: ImportResult, current: dict[str, str], language: str, shared: Mapping[str, list[str]] | None = None) -> list[list[str]]:
    """The rows of the tab's table, one for each row of the list in the order of the file: the article (a row without an item code by
    its row), the purchase price in Exact, the price on the list (for a price that changes or stays the same, and for one that the
    tab's own check held back, which the buyer may have to enter himself) and the outcome by its label (see the module docstring).
    The outcome of a row kept off the list as shared names the other suppliers (`shared`, the article -> their names; others_named).
    For the result of a press it says what became of each row: a change that could not be prepared has the label "failed". The
    table shows a cell as text, so a name and an item code stand in it as they are."""
    strings, rows = STRINGS[language], []
    for outcome in result.outcomes:
        if isinstance(outcome, WriteLogEntry):
            rows.append([outcome.op.article_code or "", _price(outcome.previous, language), _price(outcome.new, language),
                         strings["pricelist_outcome:changes"]])
            continue
        item, reason = outcome
        held = _price(current.get(item), language)
        asked = held if reason == "unchanged" else _price(result.held.get(item), language) if reason in _KEPT_OFF else ""
        article = strings["pricelist_row"].format(n=item.removeprefix("row ")) if reason == "no item code" else item
        outcome = strings[f"pricelist_outcome:{OUTCOMES.get(reason, 'failed')}"]
        if reason == SHARED:
            outcome = outcome.format(name=others_named((shared or {}).get(item, []), language))
        rows.append([article, held, asked, outcome])
    return rows


def others_named(names: list[str], language: str) -> str:
    """The other suppliers of a shared article as the outcome of its row names them: the first two, then how many more."""
    first = ", ".join(names[:2])
    return first if len(names) <= 2 else STRINGS[language]["pricelist_shared_more"].format(names=first, n=len(names) - 2)


def count_line(result: ImportResult, language: str) -> str:
    """The line under the table: how many prices change, how many stay the same and how many rows were passed over, each count of one in
    the singular; the advice to ask the supplier for a current list when a row is expired; and, when the tab's check held a row back
    because its price differs strongly (DIFFERS), that the aid puts no such price on the list."""
    strings = STRINGS[language]
    same = sum(1 for _, reason in result.skipped if reason == "unchanged")
    lines = [strings["pricelist_counts"].format(changes=counted("pricelist_changes", len(result.updated), language),
                                                same=counted("pricelist_same", same, language),
                                                passed=counted("pricelist_passed", len(result.skipped) - same, language))]
    lines += [strings["pricelist_expired"]] if result.expired else []
    lines += [strings["pricelist_differs"]] if any(reason == DIFFERS for _, reason in result.skipped) else []
    return " ".join(lines)


def older_asked(result: ImportResult, supplier: str, asked: Mapping) -> list[str]:
    """The articles, in the order of the file, of the rows that the tab's check kept off the list as shared or as differing
    strongly while the list holds an open change of the article (`asked`, the open change of each article: ArticleLook.open) that
    an earlier price list of this supplier asked, with another price than this list asks. That older price is then still asked of
    the buyer, and this list does not replace it. A row "Terugzetten" is no price of a price list."""
    found = []
    for item, reason in result.skipped:
        change = asked.get(item) if reason in (SHARED, DIFFERS) else None
        if (change is not None and change.rule_id != UNDO_RULE_ID and not same_value(ARTICLE_KIND, change.new_value, result.held.get(item))
                and any(listed is not None and same_supplier(listed[0], supplier) for listed in map(list_of, change.case_ids))):
            found.append(item)
    return list(dict.fromkeys(found))


def older_line(articles: list[str], language: str, literal: Callable[[str], str] = str) -> str:
    """What the count line of a view and the note of a press add for the articles of older_asked, each as `literal` makes an item
    code showable; "" for none."""
    return STRINGS[language]["pricelist_older"].format(articles=", ".join(literal(item) for item in articles)) if articles else ""


def not_prepared_line(result: ImportResult, language: str, literal: Callable[[str], str] = str) -> str:
    """What the note of a press says of the changes that could not be prepared: their articles, each once in the order of the file
    and as `literal` makes an item code showable; "" when every change was prepared."""
    articles = list(dict.fromkeys(item for item, _ in result.errors))
    if not articles:
        return ""
    return STRINGS[language]["pricelist_not_prepared"].format(articles=", ".join(literal(item) for item in articles))


def carried_line(listed: int, language: str) -> str:
    """What the tab says after a press: how many price changes stand on the list "In Exact invoeren"."""
    strings = STRINGS[language]
    return strings["pricelist_carried_one"] if listed == 1 else strings["pricelist_carried_many"].format(n=listed)


def counted(key: str, count: int, language: str) -> str:
    """A count in words, by the two forms of `key`: "<key>_one" for one, "<key>_many" for any other count."""
    strings = STRINGS[language]
    return strings[f"{key}_one"] if count == 1 else strings[f"{key}_many"].format(n=count, k=count, a=count, b=count)


def _price(value: str | None, language: str) -> str:
    """A price in normal form as the page shows it: with the decimal comma in Dutch; "" for none."""
    return (value or "").replace(".", _DECIMAL[language])


# --- the columns of a price list ---

def list_titles(list_file: Path) -> list[str]:
    """The column titles of a price list: the cells of its first row that is not blank, without the spaces around them, an empty cell
    left out and a title that stands twice taken once (compared without regard to case, as the import compares titles). Refuses a
    file that is no .xlsx or .csv file, an empty file and a file that cannot be read (ListRefused); a file that cannot be opened
    raises its OSError."""
    titles: dict[str, str] = {}
    for cell in _title_row(Path(list_file))[1]:
        if cell.strip():
            titles.setdefault(cell.strip().casefold(), cell.strip())
    return list(titles.values())


def columns_for(titles: list[str], kept: dict[str, str] | None) -> dict[str, str]:
    """What the five column fields are filled with for a list with these titles (see the module docstring): the title of the file, as
    the file spells it, for each field of LIST_COLUMNS, "" for a field that stays empty. `kept` is the map kept for the supplier, None
    or empty when none is kept."""
    offered = {title.casefold(): title for title in titles}
    source = kept or {column: column for column in LIST_COLUMNS}
    return {column: offered.get((source.get(column) or "").strip().casefold(), "") for column in LIST_COLUMNS}


def column_map(form: dict, language: str, literal: Callable[[str], str] = str) -> tuple[dict[str, str] | None, list[str]]:
    """The column map of the five fields (the aid's column -> the title chosen, a field left empty left out), or None and what is
    missing, each a sentence in the page's language: no column for the item code or for the price, and a column chosen for more than
    one field, named once by its title as `literal` makes it showable (the page gives its own, so that a title shows as text)."""
    strings, problems = STRINGS[language], []
    chosen = {column: (form.get(column) or "").strip() for column in LIST_COLUMNS if (form.get(column) or "").strip()}
    if "item_code" not in chosen:
        problems.append(strings["exact_export_problem:item_code"])
    if "price" not in chosen:
        problems.append(strings["pricelist_problem:price"])
    first: dict[str, str | None] = {}            # a title as compared -> the title as first chosen; None once it was named
    for title in chosen.values():
        folded = title.casefold()
        if first.get(folded) is not None:
            problems.append(strings["exact_export_problem:more_than_once"].format(column=f"\"{literal(first[folded])}\""))
            first[folded] = None
        first.setdefault(folded, title)
    return (None, problems) if problems else (chosen, [])


def checked_columns(list_file: Path, chosen: dict[str, str]) -> None:
    """Refuse a list in whose row of titles a chosen title stands twice (ListRefused, with the title and the row): the import would
    not know which column is meant."""
    number, cells = _title_row(Path(list_file))
    folded = [cell.strip().casefold() for cell in cells]
    for title in chosen.values():
        if folded.count(title.strip().casefold()) > 1:
            raise ListRefused("double_column", column=title.strip(), row=number)


def list_table(list_file: Path) -> list[list[str]]:
    """Every row of a price list as the tab reads it, each a list of cell texts, blank rows included; the import is given these
    rows (import_price_list's table). A workbook is read from the sheet the buyer sees: a hidden sheet is never read, the one
    visible sheet that holds data is read whatever its place, each cell as the import reads a cell, and a workbook with more than
    one such sheet is refused with their names (ListRefused, "sheets"), so that no sheet is compared or left out without a word. A
    CSV file is read as the export of articles is (aid/readers/po_export.py): in UTF-8, UTF-16 with its byte order mark or
    Windows-1252, cut at semicolons, commas or tabs. The export reader finds its mark by the titles of its map; a price list has
    no map yet, and its titles stand in its first filled row, so the mark is the one that cuts that row into the most cells (the
    first of the reader's marks on a tie), as the import's own reader chooses between its two. A mark inside a description
    further down then never decides. A CSV file that cannot be read so raises the export reader's ExportRefused: an encoding the
    reader does not read, a text with NUL characters (UTF-16 without its mark), a file that the csv module cannot cut, a file
    whose first filled row no mark cuts into two cells, since a price list has at least an item code and a price, and a file with
    a row that the aid does not read, with that row (odd_row of aid/pricelist.py: a filled cell beyond the last column title, or,
    in a file cut at commas, another number of cells than the row of titles). A file of another type and a workbook that cannot
    be read are refused (ListRefused), so is a workbook whose only data stands on a hidden sheet ("hidden"), and so is a list, CSV
    or workbook, with a heading
    above its column titles ("heading": its first filled row holds one filled cell and a later row more), which is told so and not
    taken for a file with another separator or with one odd title; a file that holds nothing but its marks has no row with a
    cell."""
    path = Path(list_file)
    suffix = path.suffix.lower()
    if suffix not in EXPORT_SUFFIXES:
        raise ListRefused("type")
    if suffix == ".xlsx":
        try:
            table = _xlsx_table(path)
        except SeveralSheets as several:
            raise ListRefused("sheets", names=several.names) from None
        except OnlyHidden:                       # only a sheet the buyer does not see holds data
            raise ListRefused("hidden") from None
        except ValueError:                       # a workbook that cannot be read
            raise ListRefused("unreadable") from None
        if _heading(table):
            raise ListRefused("heading")
        return table
    text = csv_text(path)
    if not text.strip(_SEPARATORS):
        return []
    not_cut = ExportRefused(f"{path.name} uses a delimiter or an encoding the aid does not read", "delimiter_or_encoding")
    if "\x00" in text:
        raise not_cut
    cut = [(mark, table) for mark, table in ((mark, csv_rows(text, mark)) for mark in DELIMITERS) if table is not None]
    if not cut:
        raise ExportRefused(f"{path.name} cannot be read as CSV", "unreadable")
    marks = [mark for mark, _ in cut]
    tables = [[[cell.text for cell in row] for row in table] for _, table in cut]
    widths = [len(next((row for row in table if any(cell.strip() for cell in row)), [])) for table in tables]
    if max(widths) < 2:                          # no mark cuts the first filled row: a heading above the titles, or no table at all
        raise ListRefused("heading") if any(_heading(table) for table in tables) else not_cut
    mark, table = marks[widths.index(max(widths))], tables[widths.index(max(widths))]
    if _heading(table):
        raise ListRefused("heading")
    header = next(index for index, cells in enumerate(table) if any(cell.strip() for cell in cells))
    if (odd := odd_row(table, header, commas=mark == ",")) is not None:       # a row cut otherwise than the titles: no cell of it is read
        raise ExportRefused(f"{path.name}, row {odd[0]}: {odd[1]}", "surplus_cells" if odd[1] == SURPLUS else "cell_count", row=odd[0])
    return table


def _heading(table: list[list[str]]) -> bool:
    """Whether a price list has a heading above its column titles: its first filled row holds one filled cell and a later row
    holds more. The import takes the first filled row for the titles, so such a list would offer its heading as its one title."""
    filled = [sum(1 for cell in cells if cell.strip()) for cells in table]
    first = next((index for index, count in enumerate(filled) if count), None)
    return first is not None and filled[first] == 1 and any(count > 1 for count in filled[first + 1:])


def _title_row(path: Path) -> tuple[int, list[str]]:
    """The first row of a price list that is not blank, with its number counted from 1, as the import finds the list's titles."""
    _checked(path)
    for number, cells in enumerate(list_table(path), 1):
        if any(cell.strip() for cell in cells):
            return number, cells
    raise ListRefused("empty")


# --- the export of articles, and what the tab says of the stored prices ---

def checked_export(export: Path) -> None:
    """Refuse an export of articles before it is read: a file that is no .xlsx or .csv file, and an empty file (ListRefused). A file
    that cannot be looked at raises its OSError."""
    _checked(Path(export))


def _checked(path: Path) -> None:
    if path.suffix.lower() not in EXPORT_SUFFIXES:
        raise ListRefused("type")
    if path.stat().st_size == 0:
        raise ListRefused("empty")


def refusal_text(error: ValueError, name: str, language: str, literal: Callable[[str], str] = str) -> str:
    """The sentence of a refusal, in the page's language: of this module (ListRefused), of the export reader (ExportRefused) or of
    the stored article prices (SeveralSheets, NoArticle and NameRefused of aid/pricelist.py), with `name`, the file as the page
    shows a name. A file of another type and an empty file are named; a workbook with more than one visible sheet with data names
    those sheets, each as `literal` makes a name showable; an export whose columns are not those set says to check part 3; an export
    that gave no article and a supplier's name that is none have their sentences; a title that stands twice is named with its row,
    as `literal` makes a title showable; a file that cannot be read has the sentence of part 2 of the block "Koppeling met Exact"
    for one. Never what a cell holds."""
    strings = STRINGS[language]
    if isinstance(error, SeveralSheets) or (isinstance(error, ExportRefused) and error.reason == "several_sheets"):
        return sheets_text(error, language, literal)
    if isinstance(error, NoArticle):
        return strings["pricelist_refused:no_article"]
    if isinstance(error, NameRefused):                    # a name in a row of the export has its own sentence, with the row
        return strings["pricelist_refused:name"] if error.row is None else strings["pricelist_refused:name_row"].format(row=error.row)
    if isinstance(error, ListRefused):
        if error.reason == "type":
            return strings["pricelist_refused:type"].format(name=name)
        if error.reason == "empty":
            return strings["files_refused:empty"].format(name=name)
        if error.reason == "sheets":
            return sheets_text(error, language, literal)
        if error.reason == "heading":
            return strings["pricelist_refused:heading"]
        if error.reason == "hidden":
            return strings["exact_export_refused:hidden_sheet"]
        if error.reason == "double_column":
            return strings["exact_export_refused:double_column"].format(column=f"\"{literal(error.column or '')}\"", row=error.row)
        return strings["exact_export_refused:unreadable"]
    if error.reason == "file_type":
        return strings["pricelist_refused:type"].format(name=name)
    if error.reason in _COLUMNS_DIFFER:
        return strings["pricelist_refused:columns"]
    return export_refusal_text(error, language)


def when(stored_at: str) -> str:
    """The time of a stored export as the page shows a time: "2026-11-20 09:15"."""
    return datetime.fromisoformat(stored_at).strftime("%Y-%m-%d %H:%M")


def state_line(supplier: str, stored: StoredPrices | None, language: str) -> str:
    """What the tab says of the supplier chosen: to choose one first, that no purchase prices of it are filed yet, or when its prices
    were filed, from which file, and how many articles. `supplier` and the file of `stored` are shown as they are given."""
    strings = STRINGS[language]
    if not supplier:
        return strings["pricelist_choose_supplier"]
    if stored is None:
        return strings["pricelist_state_none"].format(supplier=supplier)
    return strings["pricelist_state"].format(supplier=supplier, time=when(stored.stored_at), file=stored.file,
                                             articles=counted("pricelist_articles", len(stored.prices), language))


def filed_line(suppliers: list[str], kept: int, left_out: int, language: str) -> str:
    """What the tab says after an export was stored: for one supplier, its name with the articles kept and the rows left out; for an
    export that named none or several, how many suppliers. The names are shown as they are given."""
    strings = STRINGS[language]
    articles, passed = counted("pricelist_articles", kept, language), counted("pricelist_passed", left_out, language)
    if len(suppliers) == 1:
        return strings["pricelist_filed"].format(supplier=suppliers[0], articles=articles, passed=passed)
    return strings["pricelist_filed_suppliers"].format(m=len(suppliers), articles=articles, passed=passed)
