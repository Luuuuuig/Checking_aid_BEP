"""The list "In Exact invoeren" as the review page shows it, without the page library: the open changes of aid/execution/by_hand.py in
blocks, each change one row in the page's language (aid/review/page.py builds the tab from it, its strings are in aid/review/i18n.py).

blocks gives one block per PO in the order of the PO numbers (numbers as numbers), its changes by line (numbers as numbers) and then
price, date and quantity, and then one block "Artikelprijzen" with the article prices by article code, whichever order or price list
asked them. A block is (its id, its heading, its rows). The id is "po:<PO number>", or "articles" (ARTICLES). The heading of a PO's
block is "Inkooporder <number> (<supplier>)": the supplier as the PO as it stands names it (PoStore.as_it_stands of aid/po_store.py:
its latest PDF version, or a look from an export filed after it, as for the PO of a cart), else as the result of the newest case of
the block that knows one, and without the brackets when neither does. When the PO as it stands is above the authorization, the
heading has a second line, the hint that the order must be approved again after the change; the PO's total and the limit are shown
nowhere. A row is (its text, its value): the text of row_text, and the value the entry ids of the change
joined by "+" (ids_of splits it again).

row_text shows the line, the item code of that line in the version the change's case was compared with (step 01 of the case: of the
newest case that holds the change's version, "onbekend" when none does), the kind of the value, and the old and the new value in the
page's language: a price or a quantity with the decimal comma in Dutch and the point in English, a day as dd-mm-yyyy, a week as
"week 47 (2026)", a range as its two days joined by "/", and "onbekend" for a value the aid does not know, such as the old purchase
price of an article that it never read. An article price shows its article code. The undo of an entered article price whose price
before it the aid never read (its new value unknown) reads "artikel KM-0801: inkoopprijs 12,70 terugzetten naar de prijs van
daarvoor": the buyer puts back the price that Exact held before. The put-back of a write that a program made in Exact into a field
that was empty before it reads "... → leeg": the buyer empties the field (_emptied). No rule code is shown. A change of try-out
documents (Change.tryout: the list gives the rows of cases made from a try-out a place of their own, aid/execution/by_hand.py, so a
change is all from try-outs or not at all) carries "(proefdocument)" behind its row, in the test mode and out of it, so that a
made-up change is never shown as one to enter in the real Exact.

What the controls of the tab show is made here as well: tick_rows, the group of tick boxes (a row of a PO's block after the first line
of its heading, so that each tick box names its order), block_choices, the dropdown of the blocks, price_lists, the price lists
whose article prices are still to enter, for the one press that marks the article prices of one list as entered (each named by its
supplier and the time it was put on the list, "S01 (20-11-2026 09:30)", the newest first), and intro, the text above them: the
intro, then the lines that apply, in the test mode (the setting test_mode) "Proefstand: voer deze wijzigingen alleen in het test-Exact
in.", the hint of each order above the authorization, "Er is niets in te voeren." when nothing is open, and how many lines of the list
could not be read.

A row that puts a change back in Exact (linked by the mark puts_back, aid/execution/by_hand.py) shows "Terugzetten: " before its row,
"Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,50" among the tick boxes. An open row of a PO line whose
line shows, in the print of its order that the aid was given last (the latest PDF version or the latest look from an export),
given after the change was asked, neither its old nor its new value has the note of what Exact shows now after it: "... prijs
12,50 → 12,70. Exact toont nu 12,80." (note_value of by_hand). A row whose tick a print ended (Change.ended of by_hand: the buyer
had ticked it, and a print given after the tick contradicts it) says that in the place of that note, with the value the print
showed, whatever it is: "... U vinkte dit af, maar de inkooporder die daarna binnenkwam toont 12,50. Voer de wijziging in Exact
in, of vink opnieuw af als zij er al in staat."; or, when the line could not be found in that print, that it could not.

Under the list the tab has two sections. done_rows gives the section "Afgehandeld in de laatste 14 dagen": the changes entered, seen or
dropped in that time, the newest first, each as label_of names it, followed by how the aid knows it, "(door u afgevinkt op 18-11-2026)",
"(gezien in versie 2 van de inkooporder)", "(gezien in een export uit Exact)" or "(niet ingevoerd, op 18-11-2026)"; done_text makes the
section's text of them. take_back is its button "Terugdraaien in Exact": on a change entered or seen it prepares the old value as the
value to enter, through the undo of the review page's close (aid/review/close.py, undo) for a change of a case, so that the case's
write log, step 09 and audit record hold it, and through undo_entry on the price list's own write-log folder for a change of a price
list (<cases_root>/pricelists/<day>, where the import's command keeps it); it links the new row to the entry it puts back and says
what happened, never "Ongedaan gemaakt": the change stays among the handled ones as it was, next to its put-back. The put-back
names the old value of the row, the purchase order's as the aid read it; when the aid's own records give another value for what
Exact held before the change (the last change of the place entered or seen before it, else what the order as it stands shows),
the note of a put-back made now ends with one more sentence that says so and asks the buyer to look in Exact which value goes
back (_other_before, "Let op: ..."). An article price
whose old price the aid never read, a change that was not entered and a put-back itself are not put back, and the note says so,
before anything else is looked at. A put-back never replaces a newer change: while a later change of the same line or article that
another document asked is open, entered or seen (_newer), nothing is prepared, and the note names that change and says what the
buyer can do: when it asks this change's old value itself, nothing needs putting back (enter it while it is on the list; once it is
entered or seen, the change was put back already); else drop it while it is on the list, or put it back and tick its put-back off
once it is entered, or, when the aid can never put it back, put the old value back in Exact himself. An entered or seen newer
change counts only while the marks say that Exact holds its value and its own put-back is not entered or seen: once its put-back
is entered or seen, or another row entered after it set another value, it no longer counts. The put-back is worked out first
(by_hand.put_back_state, nothing written): it is made when it would stand open on the list, "Deze wijziging is al teruggedraaid."
is said when the marks say that Exact holds the old value already, whatever newer change there is, and the note to pass on to the
researcher is left for a state the rules cannot give. A put-back that exists already is not made again: open, the note says that it
is on the list; dropped, it is put on the list again; entered or seen, the change was put back already; replaced by a newer change,
or dropped with a newer row after it, it does not return to the list while a row after it is open or a newer change of the change
still counts (as _newer counts it), and is made again, a second undo of the same entry, once nothing stands in its way.
confirmations gives the section "Bevestigingen om bij te voegen", which the tab shows between the list and the handled changes: the
rows that attach a confirmation, without a tick, while the newest row of their case that attaches it or sets the status is at most
7 days old, each with the day of that row, the order, the file and whether the status is to be set to Bevestigd as well (the case's
last row that sets the status sets Bevestigd); confirmation_lines and confirmations_text show them, under the section's heading and a
line that counts them and says what to do.
"""
import re
from datetime import datetime, timedelta
from pathlib import Path, PureWindowsPath

from aid.case import load_case, names_a_folder
from aid.execution.by_hand import (ARTICLE_KIND, EXPORT, FOLDER, KINDS, LINE_KINDS, Change, changes, item_code, link_put_back,
                                   newest_version, note_value, place_of, prepared_rows, put_back_of, put_back_state, reopen, same_value,
                                   shown_now)
from aid.execution.writer import LOG_FILE, UNDO_RULE_ID, WriteLog, WriteLogEntry, read_before, undo_entry
from aid.pipeline import CaseResult, Deps
from aid.po_store import PoStore, PoVersion
from aid.pricelist import list_of
from aid.review.close import undo
from aid.review.i18n import STRINGS
from aid.routing import CONFIRMED

TAB = "invoeren"                                 # the id of the tab on the page, and the value of tab= in an address that selects it
ARTICLES = "articles"                            # the id of the block of the article prices
PO_BLOCK = "po:"                                 # the start of the id of a PO's block
DONE_DAYS, CONFIRMATION_DAYS = 14, 7             # how far back the two sections under the list look
PRICELISTS = "pricelists"                        # under cases_root: the write-log folders of the price-list import (aid/pricelist.py)
_HELD = ("entered", "seen")                      # the states in which the marks say that Exact holds a change's value
_ATTACH, _DETACH, _STATUS = "attach_document", "detach_document", "set_status"
_DECIMAL = {"nl": ",", "en": "."}
_NUMBERS = ("po_line_price", "po_line_quantity", "article_price")     # the kinds whose values are numbers
_DAY = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_WEEK = re.compile(r"(\d{4})-W(\d{2})")
_MARKDOWN = re.compile(r"([\\`*_\[\]<>~])")      # the marks that change how Markdown shows a name in the middle of a line


def blocks(open_: list[Change], store: PoStore, cases_root: Path, language: str) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """The open changes in blocks, as the tab shows them (see the module docstring): (block id, heading, [(row text, value)])."""
    strings = STRINGS[language]
    orders: dict[str, list[Change]] = {}
    articles = []
    for change in open_:
        if block_of(change) == ARTICLES:
            articles.append(change)
        else:
            orders.setdefault(change.po_number, []).append(change)
    shown = []
    for po_number in sorted(orders, key=_natural):
        changes = sorted(orders[po_number], key=lambda change: (_natural(change.line_id or ""), LINE_KINDS.index(change.kind)))
        shown.append((f"{PO_BLOCK}{po_number}", _heading(po_number, changes, store, cases_root, strings),
                      _rows(changes, store, cases_root, language)))
    if articles:
        articles.sort(key=lambda change: change.article_code or "")
        shown.append((ARTICLES, strings["hand_block_articles"], _rows(articles, store, cases_root, language)))
    return shown


def row_text(change: Change, cases_root: Path, language: str) -> str:
    """One change as its row shows it (see the module docstring), with "Terugzetten: " before the row of a put-back and
    "(proefdocument)" behind the row of a change of try-out documents. A put-back without a value to put back shows "leeg" for it
    when the field was empty before the write it puts back (_emptied), and "onbekend" when the aid knows no old value."""
    strings = STRINGS[language]
    old, new = (_value(value, change.kind, language) for value in (change.old_value, change.new_value))
    emptied = _emptied(change, cases_root)
    new = strings["hand_empty"] if emptied else new
    mark = f" {strings['hand_tryout']}" if change.tryout else ""
    if change.kind == ARTICLE_KIND:
        back = change.rule_id == UNDO_RULE_ID and change.new_value is None and not emptied
        text = strings["hand_row:article_price_back" if back else "hand_row:article_price"].format(item=change.article_code, old=old, new=new)
    else:
        item = item_code(change, cases_root) or strings["hand_unknown"]
        text = strings[f"hand_row:{change.kind}"].format(line=change.line_id, item=item, old=old, new=new)
    return (strings["hand_put_back_row"].format(row=text) if change.puts_back else text) + mark


def _emptied(change: Change, cases_root: Path | None) -> bool:
    """Whether the change is a put-back, without a value to put back, of a write that a program made in a system into a field
    that was empty before it (read_before of aid/execution/writer.py, on the entry it puts back in the write log of its case):
    the buyer then empties the field, where a put-back whose old value the aid does not know asks him for that value. A write
    log that is not there, or cannot be read, says nothing."""
    if change.new_value is not None or change.puts_back is None or cases_root is None:
        return False
    try:
        folder = _log_folder(Path(cases_root), change.case_ids[-1], change.puts_back)
        entry = next((entry for entry in WriteLog(folder).entries() if entry.entry_id == change.puts_back), None) if folder is not None else None
    except (OSError, ValueError):
        return False
    return entry is not None and entry.status == "done" and read_before(entry) == "empty"


def block_of(change: Change) -> str:
    """The id of the block a change belongs to: "articles" for an article price, else "po:<PO number>"."""
    return ARTICLES if change.kind == ARTICLE_KIND else f"{PO_BLOCK}{change.po_number}"


def ids_of(value: str) -> list[str]:
    """The entry ids of a row's value: its parts between the "+" signs."""
    return [entry_id for entry_id in (value or "").split("+") if entry_id]


def tick_rows(listed: list[tuple[str, str, list[tuple[str, str]]]]) -> list[tuple[str, str]]:
    """The group of tick boxes: every row of the blocks in their order, as (label, value); a row of a PO's block after the first line
    of the block's heading, "Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,50 → 12,70", an article price as its row."""
    return [(text if block_id == ARTICLES else f"{_title(heading)}, {text}", value) for block_id, heading, rows in listed for text, value in rows]


def block_choices(listed: list[tuple[str, str, list[tuple[str, str]]]]) -> list[tuple[str, str]]:
    """The dropdown of the blocks: the first line of each heading, and the block's id."""
    return [(_title(heading), block_id) for block_id, heading, _ in listed]


def price_lists(open_: list[Change], cases_root: Path, language: str) -> list[tuple[str, str, list[str]]]:
    """The price lists whose article prices are still to enter, the newest first: (the label, the case id of the list, the values of
    its rows on the list, each the entry ids of a change joined by "+"). A price list is a case id that the price-list import gives
    (list_of of aid/pricelist.py) and that is no case folder; a case id that cannot be the name of a folder (a supplier's name with
    a slash, or a very long one) is a price list without a look at the file system. Its label names the supplier and when the list
    was put on the list, "S01 (20-11-2026 09:30)". An article price that a confirmation asked too is a row of the list as well: it
    is one change. A put-back ("Terugzetten") and any other row that undoes a change is no row of a price list, though it carries
    the case id of the change it undoes: the buyer who says that a list's prices are in Exact does not say that an old price is
    back. So a change is a row of the lists whose own rows ask it, never by an undo row, and a list is not given for a put-back
    alone. A list of a supplier that was put on the list again holds the rows of the newer one: of two lists of one supplier with
    the same rows only the newest is given, since either press would mark the same rows."""
    found: dict[str, list[str]] = {}
    asked_by = {row.entry_id: row.case_id for row in prepared_rows(Path(cases_root) / FOLDER, (ARTICLE_KIND,))
                if row.rule_id != UNDO_RULE_ID}          # the case of each row that asks an article price and undoes none
    for change in open_:
        if change.kind == ARTICLE_KIND:
            for case_id in dict.fromkeys(asked_by[entry_id] for entry_id in change.ids if entry_id in asked_by):
                if list_of(case_id) is not None and not _is_case(Path(cases_root), case_id):
                    found.setdefault(case_id, []).append("+".join(change.ids))
    lists = sorted(((*list_of(case_id), case_id) for case_id in found), key=lambda one: (one[0], one[2]))
    lists.sort(key=lambda one: one[1], reverse=True)         # the newest first; lists of one moment by their supplier
    given, shown = set(), []
    for supplier, moment, case_id in lists:
        rows = (supplier, frozenset(found[case_id]))
        if rows not in given:
            given.add(rows)
            shown.append((f"{supplier} ({_day_or_week(moment.date().isoformat(), language)} {moment:%H:%M})", case_id, found[case_id]))
    return shown


def intro(listed: list[tuple[str, str, list[tuple[str, str]]]], passed: int, language: str, test_mode: bool = False) -> str:
    """The text above the list, as Markdown: the intro, in the test mode the line that the changes go into the test Exact only, then a
    paragraph for each block with a hint (its first line in bold, then the hint), "Er is niets in te voeren." when no block is listed,
    and how many lines of the list could not be read."""
    strings = STRINGS[language]
    lines = [strings["hand_intro"], *([strings["hand_test_mode"]] if test_mode else [])]
    for _, heading, _ in listed:
        title, _, hint = heading.partition("\n")
        if hint:
            lines.append(f"**{_markdown(title)}** {_markdown(hint)}")
    if not listed:
        lines.append(strings["hand_nothing"])
    if passed:
        lines.append(strings["hand_unreadable_one"] if passed == 1 else strings["hand_unreadable_many"].format(n=passed))
    return "\n\n".join(lines)


# --- the parts of a block and of a row ---

def _rows(changes: list[Change], store: PoStore, cases_root: Path, language: str) -> list[tuple[str, str]]:
    """The rows of open changes: each row's text, with the note of what Exact shows now when there is one, and its value."""
    return [(_noted(change, store, cases_root, language), "+".join(change.ids)) for change in changes]


def _noted(change: Change, store: PoStore, cases_root: Path, language: str) -> str:
    """An open change's row with a note after it. A row that a print put back on the list (the buyer had ticked it, and the
    purchase order that came in afterwards contradicts the tick: Change.ended, by_hand.contradict) says so, with the value that
    print showed for the line, or that the line could not be found in it. Any other row has the note of what Exact shows now when
    a newer version of its order shows neither its old nor its new value (note_value of aid/execution/by_hand.py)."""
    text, strings = row_text(change, cases_root, language), STRINGS[language]
    if change.ended is not None:
        shown = change.ended[1]
        if shown is None:
            return f"{text}. {strings['hand_tick_ended_unfound']}"
        return f"{text}. {strings['hand_tick_ended'].format(value=_value(shown, change.kind, language))}"
    other = note_value(change, store, cases_root)
    return text if other is None else f"{text}. {strings['hand_exact_shows'].format(value=_value(other, change.kind, language))}"


def _heading(po_number: str, changes: list[Change], store: PoStore, cases_root: Path, strings: dict[str, str]) -> str:
    """The heading of a PO's block: the order and its supplier, and on a second line the hint when the PO as it stands is above
    the authorization."""
    standing = _as_it_stands(store, po_number)
    supplier = (standing.supplier if standing is not None else None) or _supplier(changes, cases_root)
    heading = (strings["hand_block_po"].format(number=po_number, supplier=supplier) if supplier
               else strings["hand_block_po_alone"].format(number=po_number))
    return f"{heading}\n{strings['hand_hint']}" if standing is not None and standing.above_authorization else heading


def _as_it_stands(store: PoStore, po_number: str) -> PoVersion | None:
    """The PO as it stands in the store: its latest PDF version, or its latest look from an export when that came after it
    (PoStore.as_it_stands); None for a PO the store does not know, or when the store cannot be read."""
    try:
        return store.as_it_stands(po_number)
    except (OSError, ValueError):
        return None


def _supplier(changes: list[Change], cases_root: Path) -> str | None:
    """The supplier that the result of a case of the changes names, the newest case first; None when none does."""
    for case_id in reversed([case_id for change in changes for case_id in change.case_ids]):
        try:
            supplier = CaseResult.model_validate(load_case(cases_root, case_id).read_step("result")).supplier
        except (OSError, ValueError):
            continue
        if supplier:
            return supplier
    return None


def _value(value: str | None, kind: str, language: str) -> str:
    """A value of a change in the language (see the module docstring)."""
    if value is None:
        return STRINGS[language]["hand_unknown"]
    if kind in _NUMBERS:
        return value.replace(".", _DECIMAL[language])
    return "/".join(_day_or_week(part, language) for part in value.split("/")) if kind == "po_line_date" else value


def _day_or_week(text: str, language: str) -> str:
    """A day as dd-mm-yyyy, a week as "week 47 (2026)", anything else as it is."""
    if day := _DAY.fullmatch(text):
        return f"{day[3]}-{day[2]}-{day[1]}"
    if week := _WEEK.fullmatch(text):
        return STRINGS[language]["hand_week"].format(week=int(week[2]), year=week[1])
    return text


def _natural(text: str) -> tuple:
    """A number by its value, before any text, which follows by its letters: "2" before "10", "99999" before "250104"."""
    return (0, int(text), text) if text.isdecimal() else (1, 0, text)


def _title(heading: str) -> str:
    """The first line of a heading."""
    return heading.partition("\n")[0]


def _markdown(text: str) -> str:
    """A text with the marks Markdown reads escaped, so that a name shows as it is."""
    return _MARKDOWN.sub(r"\\\1", text)


# --- the two sections under the list ---

def done_rows(all_: list[Change], now: datetime, language: str, days: int = DONE_DAYS, store: PoStore | None = None,
              cases_root: Path | None = None) -> list[tuple[str, str]]:
    """The section "Afgehandeld in de laatste 14 dagen" (see the module docstring): the changes of all_ entered, seen or dropped at most
    `days` before now (a mark after now counts too), the newest mark first, each as (its text, its value). The text is the change as
    label_of names it, followed by how the aid knows it; the value is its entry ids joined by "+", as on the list. Without the store
    and the cases root an order is named without its supplier and a line without its item code."""
    strings = STRINGS[language]
    done = [change for change in all_ if change.state in ("entered", "seen", "dropped") and change.marked_at is not None
            and _within(change.marked_at, now, days)]
    done.sort(key=lambda change: _aware(datetime.fromisoformat(change.marked_at)), reverse=True)     # stable: one time keeps the order
    return [(f"{label_of(change, store, cases_root, language)} {_how(change, strings, language)}", "+".join(change.ids)) for change in done]


def done_text(rows: list[tuple[str, str]], language: str) -> str:
    """The section of the handled changes as Markdown: its heading in bold, then a line per change of done_rows."""
    heading = f"**{STRINGS[language]['hand_done']}**"
    return f"{heading}\n\n" + "\n".join(f"- {_markdown(text)}" for text, _ in rows) if rows else heading


def label_of(change: Change, store: PoStore | None, cases_root: Path | None, language: str) -> str:
    """A change as the tick boxes of the list name it: a change of a PO line after the first line of its order's heading, the supplier
    as the order as it stands names it (newest_version), else as a result of its cases; an article price as its row."""
    text = row_text(change, cases_root, language)
    if block_of(change) == ARTICLES:
        return text
    strings = STRINGS[language]
    version = newest_version(store, change.po_number) if store is not None else None
    supplier = (version.supplier if version is not None else None) or (_supplier([change], cases_root) if cases_root is not None else None)
    order = (strings["hand_block_po"].format(number=change.po_number, supplier=supplier) if supplier
             else strings["hand_block_po_alone"].format(number=change.po_number))
    return f"{order}, {text}"


def take_back(deps: Deps, change: Change, now: datetime, language: str) -> str:
    """"Terugdraaien in Exact" on a handled change (see the module docstring), with the Deps of the page, whose writer prepares into
    <cases_root>/prepared: prepare the old value as the value to enter, link the new row to the entry it puts back, and return the
    note of what happened. The entry put back is the newest row of the change whose mark gives its state and whose old value the aid
    knows; a change of a case is put back through undo() of aid/review/close.py, one of a price list through undo_entry on the
    list's write-log folder under <cases_root>/pricelists. An entry the write log shows undone already (the manual's recipe) is
    linked to that undo instead. In this order, with nothing written: a change that can never be put back says so (an article price
    whose earlier price the aid never read, a put-back itself, an entry whose write log the aid cannot find); a newer change of its
    place refuses it (_newer), with a note that says what the buyer can do (the newer change asks the old value already, is on the
    list, was entered, or was entered and can never be put back), unless the marks say that Exact holds the old value already; and
    the put-back is worked out first (by_hand.put_back_state): one that the marks say Exact holds already gives "Deze wijziging is al
    teruggedraaid.", and the note for the researcher is left for a state the rules cannot give. A WriterError of the writer, or a
    write log that cannot be read, is raised."""
    strings = STRINGS[language]
    cases_root = Path(deps.settings.cases_root)
    prepared = cases_root / FOLDER
    if change.state not in _HELD:
        return strings["hand_put_back_not_entered"]
    linked = next((row for row in (put_back_of(prepared, entry_id) for entry_id in reversed(change.ids)) if row is not None), None)
    if linked is not None:
        return _put_back_note(deps, linked, now, language, fresh=False)
    rows = {row.entry_id: row for row in prepared_rows(prepared, KINDS)}
    if (never := _never(change, rows, cases_root)) is not None:
        return strings[never]
    entry_id = _put_back_entry(change, rows)
    found = changes(prepared)[0]
    if (newer := _newer(change, found)) is not None:
        if put_back_state(prepared, entry_id, now) == "already":
            return strings["hand_put_back_done"]     # the marks say that Exact holds the old value: nothing to put back
        named = label_of(newer, deps.store, cases_root, language)
        if same_value(change.kind, newer.new_value, rows[entry_id].old_value):
            return strings["hand_put_back_not_needed"].format(row=named) if newer.state == "open" else strings["hand_put_back_done"]
        if newer.state == "open":
            return strings["hand_put_back_newer_open"].format(row=named)
        if _never(newer, rows, cases_root) is not None:
            return strings["hand_put_back_newer_never"].format(row=named)
        return strings["hand_put_back_newer_entered"].format(row=named)
    folder = _log_folder(cases_root, rows[entry_id].case_id, entry_id)
    before = next((entry for entry in WriteLog(folder).entries() if entry.undo_of == entry_id and entry.status != "failed"), None)
    if before is not None:                       # the manual's recipe undid it before: link that undo
        link_put_back(prepared, before.entry_id, entry_id, now)
        return _put_back_note(deps, before.entry_id, now, language, fresh=False)
    return _put_back_made(deps, folder, entry_id, now, language)


def _put_back_made(deps: Deps, folder: Path, entry_id: str, now: datetime, language: str, again: bool = False) -> str:
    """Prepare the put-back of the entry, whose write log is in `folder`, link it, and say so, when by_hand.put_back_state says it
    would stand open on the list; "Deze wijziging is al teruggedraaid." when the marks say Exact holds the old value already, and
    nothing written; the note for the researcher for any other state, which the rules cannot give. With again, an entry whose
    earlier put-back newer rows replaced is undone once more (_put_back_note)."""
    strings = STRINGS[language]
    prepared = Path(deps.settings.cases_root) / FOLDER
    state = put_back_state(prepared, entry_id, now)
    if state == "already":
        return strings["hand_put_back_done"]
    if state != "open":
        return strings["hand_put_back_lost"]
    made = _undone(deps, folder, entry_id, now, again)
    if made is None:                             # the write log refuses it: not its entry, or nothing was written
        return strings["hand_put_back_cannot"]
    link_put_back(prepared, made.entry_id, entry_id, now)
    return _put_back_note(deps, made.entry_id, now, language, fresh=True)


def _never(change: Change, rows: dict, cases_root: Path) -> str | None:
    """The key of the note that a change can never be put back, or None when it can be: an article price whose earlier price the aid
    never read ("hand_put_back_unknown"); a change whose entry to put back is a put-back or another undo itself, or whose write log
    the aid cannot find ("hand_put_back_cannot")."""
    entry_id = _put_back_entry(change, rows)
    if entry_id is None:
        return "hand_put_back_unknown" if change.kind == ARTICLE_KIND else "hand_put_back_cannot"
    if rows[entry_id].rule_id == UNDO_RULE_ID or _log_folder(cases_root, rows[entry_id].case_id, entry_id) is None:
        return "hand_put_back_cannot"
    return None


def _put_back_entry(change: Change, rows: dict) -> str | None:
    """The entry a put-back of the change puts back: the newest row of the change whose mark gives its state and whose old value the
    aid knows; None when there is none."""
    known = [entry_id for entry_id in change.marked_ids if entry_id in rows and rows[entry_id].old_value is not None]
    return known[-1] if known else None


def put_back_note(deps: Deps, made: WriteLogEntry, of: str, now: datetime, language: str) -> str:
    """The note of an undo that the page's writer prepared for an entry `of` (the review tab's own undo of a write made in a system):
    the row of a change to enter is linked as a put-back and the note says that it is on the list, not undone in Exact; a row that
    attaches, detaches or sets a status is on no list, and the note says to put the old value back in Exact."""
    if made.op.kind not in KINDS:
        return STRINGS[language]["hand_put_back_cannot"]
    link_put_back(Path(deps.settings.cases_root) / FOLDER, made.entry_id, of, now)
    return _put_back_note(deps, made.entry_id, now, language, fresh=True)


def confirmations(prepared: Path, now: datetime, days: int = CONFIRMATION_DAYS) -> list[dict]:
    """The section "Bevestigingen om bij te voegen": the rows of the folder's prepared_changes.csv that attach a confirmation,
    without a tick, while the newest row of their case that attaches it or sets the status was prepared at most `days` before now,
    so that a status that a close prepares days after the run brings its confirmation back; the newest such row first. Each is
    {"date": the day of that newest row, "po_number", "file": the name of the file, "set_status": whether the status is to be set to
    Bevestigd as well, "case_id"}, and "tryout": True for a row of a case made from a try-out, whose line confirmation_lines marks.
    The status is to be set when the last row of the case that sets the status sets Bevestigd (its undo sets Besteld again); a row
    whose attachment a later row of its case detaches (an undo) is left out."""
    rows = prepared_rows(prepared, (_ATTACH, _DETACH, _STATUS))
    found = []
    for index, row in enumerate(rows):
        if row.kind != _ATTACH or row.rule_id == UNDO_RULE_ID:
            continue
        if any(other.kind == _DETACH and other.case_id == row.case_id and other.document == row.document for other in rows[index + 1:]):
            continue
        own = [other for other in rows if other.case_id == row.case_id and other.kind in (_ATTACH, _STATUS)]
        newest = max(own, key=lambda other: _aware(datetime.fromisoformat(other.timestamp)))
        if not _within(newest.timestamp, now, days):
            continue
        statuses = [other.new_value for other in own if other.kind == _STATUS]
        found.append((_aware(datetime.fromisoformat(newest.timestamp)),
                      {"date": datetime.fromisoformat(newest.timestamp).date().isoformat(), "po_number": row.po_number,
                       "file": PureWindowsPath(row.document or "").name, "set_status": bool(statuses) and statuses[-1] == CONFIRMED,
                       "case_id": row.case_id, **({"tryout": True} if row.tryout else {})}))
    return [item for _, item in sorted(found, key=lambda pair: pair[0], reverse=True)]       # stable: one time keeps the order


def confirmation_lines(found: list[dict], cases_root: Path, language: str) -> list[str]:
    """The confirmations as the section shows them: the day, the order with its supplier as its case's result names it, and the file,
    with "alleen bijvoegen: er staat nog iets open" after it when the status is not to be set yet, and "(proefdocument)" behind the
    confirmation of a try-out, which is made up: nothing of it is to be done in the real Exact."""
    strings = STRINGS[language]
    lines = []
    for item in found:
        day, supplier = _day_or_week(item["date"], language), _case_supplier(item["case_id"], cases_root)
        line = (strings["hand_confirmation"].format(date=day, number=item["po_number"], supplier=supplier, file=item["file"]) if supplier
                else strings["hand_confirmation_alone"].format(date=day, number=item["po_number"], file=item["file"]))
        line = line if item["set_status"] else f"{line} ({strings['hand_attach_only']})"
        lines.append(f"{line} {strings['hand_tryout']}" if item.get("tryout") else line)
    return lines


def confirmations_text(found: list[dict], cases_root: Path, language: str) -> str:
    """The section of the confirmations as Markdown: its heading, a line that counts them and says what to do with them ("2
    bevestigingen van de laatste 7 dagen: voeg ze in Exact bij en zet de status op Bevestigd."), then a line per confirmation; ""
    when there is none."""
    if not found:
        return ""
    strings = STRINGS[language]
    count = strings["hand_confirmations_one"] if len(found) == 1 else strings["hand_confirmations_many"].format(n=len(found))
    lines = confirmation_lines(found, cases_root, language)
    return f"## {strings['hand_confirmations']}\n\n{count}\n\n" + "\n".join(f"- {_markdown(line)}" for line in lines)


def _how(change: Change, strings: dict[str, str], language: str) -> str:
    """How the aid knows that a handled change is handled: the buyer's tick or drop with its day, or the version it was seen in."""
    day = _day_or_week(datetime.fromisoformat(change.marked_at).date().isoformat(), language)
    if change.state == "seen" and change.marked_by == EXPORT:
        return strings["hand_done_seen_export"]
    if change.state == "seen":
        return strings["hand_done_seen"].format(n=(change.marked_by or "").removeprefix("version "))
    return strings["hand_done_dropped" if change.state == "dropped" else "hand_done_ticked"].format(date=day)


def _put_back_note(deps: Deps, row_id: str, now: datetime, language: str, fresh: bool) -> str:
    """The note of a press of "Terugdraaien in Exact" by the state of the put-back row `row_id`: open, the row on the list when it was
    made now (fresh), else that the put-back is on the list already; dropped, put on the list again; entered or seen, or held by Exact
    by the marks, or not on the list at all (a writer that made it in a system), the change was put back already. Replaced by newer
    rows of its place, or dropped with newer rows after it, so that it cannot return to the list: the change was put back already
    when the marks say that Exact holds its old value; while a row after the put-back is open, or a newer change of the change still
    counts (_newer: on the list, or entered or seen and still in Exact), that the put-back does not return to the list (the newer
    change is the decision now); once nothing stands in its way (the rows after it were dropped, are already held, were cancelled or
    put back), the put-back is made again, a new undo of the same entry, so that a put-back can always be made when nothing newer
    stands in its way (_in_the_way)."""
    strings = STRINGS[language]
    cases_root = Path(deps.settings.cases_root)
    prepared = cases_root / FOLDER
    found = changes(prepared)[0]
    put = next((change for change in found if row_id in change.ids), None)
    if put is None or put.state in (*_HELD, "already"):
        return strings["hand_put_back_done"]
    state = put.state
    if state == "dropped":                       # put on the list again, when nothing came after it; else as if replaced
        state = reopen(prepared, put.ids, now)
        if state == "open":
            put, fresh = next(change for change in changes(prepared)[0] if row_id in change.ids), True
        elif state == "already":
            return strings["hand_put_back_done"]
    if state == "replaced" and put.puts_back is not None:
        of = next((row for row in prepared_rows(prepared, KINDS) if row.entry_id == put.puts_back), None)
        owner = next((change for change in found if put.puts_back in change.ids), None)
        if of is not None and put_back_state(prepared, of.entry_id, now) == "already":
            return strings["hand_put_back_done"]
        folder = _log_folder(cases_root, of.case_id, of.entry_id) if of is not None else None
        if folder is not None and owner is not None and not _in_the_way(put, owner, found):
            return _put_back_made(deps, folder, of.entry_id, now, language, again=True)
    if put.state != "open":
        return strings["hand_reopen_not"]
    if not fresh:
        return strings["hand_put_back_on_list"]
    listed = "hand_put_back_listed_empty" if _emptied(put, cases_root) else "hand_put_back_listed"      # empty the field, or enter the old value
    return strings[listed].format(row=label_of(put, deps.store, cases_root, language)) + _other_before(deps, put, found, language)


def _other_before(deps: Deps, put: Change, found: list[Change], language: str) -> str:
    """The sentence after the note of a put-back made now, when the aid's own records give another value for what Exact held before
    the change than the one the put-back names (the old value of the change's row, which is the purchase order's as the aid read
    it): the value of the last change of the place that was entered or seen before the change put back, by the marks, else the
    value that the order as it stands shows (by_hand.shown_now) when that is neither the value entered nor the value the put-back
    names; "" when the records give no other value. The put-back itself names the row's value, as before. The sentence is for the
    put-back of a change of the list alone. The put-back of a write that a program made in a system names the value the program
    read there before it wrote, or none when the field was empty (read_before of aid/execution/writer.py): that is no reading of
    the purchase order that the aid's records could set right, so it has no such sentence, and neither has a put-back that names
    no value."""
    strings = STRINGS[language]
    place = place_of(put)
    at = next((index for index, one in enumerate(found) if put.puts_back in one.ids), None)
    if at is None or put.new_value is None:      # no row of the list is put back, or no value is named
        return ""
    held = [one.new_value for one in found[:at] if place_of(one) == place and one.state in _HELD]
    known, source = (held[-1], "marks") if held else (shown_now(put, deps.store, Path(deps.settings.cases_root)), "look")
    if known is None or same_value(put.kind, known, put.new_value) or (source == "look" and same_value(put.kind, known, put.old_value)):
        return ""
    return " " + strings["hand_put_back_other"].format(old=_value(put.new_value, put.kind, language), value=_value(known, put.kind, language),
                                                       source=strings[f"hand_put_back_source:{source}"])


def _newer(change: Change, found: list[Change]) -> Change | None:
    """The newest change of the change's place asked after it that is open, entered or seen and is no undo: a change that another
    document asked, which a put-back of `change` must not replace (the put-back would become the last change of the place); None
    when there is none. An entered or seen newer change counts only while the marks say that Exact holds its value (the last entered
    or seen change of the place has it) and its own put-back is not entered or seen: once its put-back is entered or seen, or another
    row entered after it set another value, Exact no longer holds it. So the refusal can always be lifted: by dropping the newer
    change, by putting it back and ticking its put-back off (a put-back that newer rows replaced is made again once nothing after it
    stands in its way, _in_the_way), or, for one that can never be put back, by the buyer putting the old value back in Exact himself."""
    place = place_of(change)
    at = next((index for index, one in enumerate(found) if change.ids[0] in one.ids), None)
    put_back = {one.puts_back for one in found if one.puts_back is not None and one.state in _HELD}
    held = [one.new_value for one in found if place_of(one) == place and one.state in _HELD]       # the last: what Exact holds
    later = [one for one in found[at + 1:] if place_of(one) == place and one.rule_id != UNDO_RULE_ID and not put_back & set(one.ids)
             and (one.state == "open" or (one.state in _HELD and same_value(one.kind, one.new_value, held[-1])))] if at is not None else []
    return later[-1] if later else None


def _in_the_way(put: Change, owner: Change, found: list[Change]) -> bool:
    """Whether something after the put-back `put` of a change of `owner` stands in the way of a new put-back: a row of its place
    after it that is open (on the list), or a newer change of `owner` that still counts (_newer)."""
    place = place_of(put)
    at = next((index for index, one in enumerate(found) if put.ids[0] in one.ids), None)
    return (at is None or any(place_of(one) == place and one.state == "open" for one in found[at + 1:])
            or _newer(owner, found) is not None)


def _log_folder(cases_root: Path, case_id: str, entry_id: str) -> Path | None:
    """The folder whose write log holds the entry: the folder of its case, or the price list's write-log folder under
    <cases_root>/pricelists that holds it (the newest first); None when there is none. A write log that cannot be read is passed by.
    A case id that cannot be the name of a folder is no case's, without a look at the file system."""
    if _is_case(cases_root, case_id):
        return cases_root / case_id
    for log in sorted((cases_root / PRICELISTS).glob(f"*/{LOG_FILE}"), reverse=True):
        try:
            if any(entry.entry_id == entry_id for entry in WriteLog(log.parent).entries()):
                return log.parent
        except ValueError:
            continue
    return None


def _is_case(cases_root: Path, case_id: str) -> bool:
    """Whether the case id is that of a case with a folder: an id that cannot be the name of a folder (names_a_folder of
    aid/case.py) is none, and the file system is not asked."""
    return names_a_folder(case_id) and (cases_root / case_id / "case.json").is_file()


def _undone(deps: Deps, folder: Path, entry_id: str, now: datetime, again: bool = False) -> WriteLogEntry | None:
    """The undo of the entry, prepared through the page's writer: undo() of the review page's close for an entry of a case, so that
    the case's write log, step 09 and audit record hold it, and undo_entry on the folder for an entry of a price list; with again,
    also for an entry undone already. None when the entry cannot be undone (undone already, or an undo itself)."""
    if (folder / "case.json").is_file():
        return undo(load_case(folder.parent, folder.name), entry_id, deps, again=again)
    return undo_entry(folder, entry_id, deps.writer, now, again=again)


def _case_supplier(case_id: str, cases_root: Path) -> str | None:
    """The supplier that the result of the case names; None without one."""
    try:
        return CaseResult.model_validate(load_case(cases_root, case_id).read_step("result")).supplier
    except (OSError, ValueError):
        return None


def _within(text: str, now: datetime, days: int) -> bool:
    """Whether the time in the text lies at most `days` days before now; a time after now counts as within."""
    return _aware(now) - _aware(datetime.fromisoformat(text)) <= timedelta(days=days)


def _aware(moment: datetime) -> datetime:
    """A time with its zone; a time without one is local time, so that one with a zone and one without compare."""
    return moment if moment.utcoffset() is not None else moment.astimezone()
