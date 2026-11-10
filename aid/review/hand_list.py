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
daarvoor": the buyer puts back the price that Exact held before. No rule code is shown.

What the controls of the tab show is made here as well: tick_rows, the group of tick boxes (a row of a PO's block after the first line
of its heading, so that each tick box names its order), block_choices, the dropdown of the blocks, and intro, the text above them: the
intro, then the lines that apply, the hint of each order above the authorization, "Er is niets in te voeren." when nothing is open, and
how many lines of the list could not be read.
"""
import re
from pathlib import Path

from aid.case import load_case
from aid.execution.by_hand import ARTICLE_KIND, LINE_KINDS, Change
from aid.execution.writer import UNDO_RULE_ID
from aid.pipeline import CaseResult
from aid.po_store import PoStore, PoVersion
from aid.review.i18n import STRINGS

TAB = "invoeren"                                 # the id of the tab on the page, and the value of tab= in an address that selects it
ARTICLES = "articles"                            # the id of the block of the article prices
PO_BLOCK = "po:"                                 # the start of the id of a PO's block
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
        shown.append((f"{PO_BLOCK}{po_number}", _heading(po_number, changes, store, cases_root, strings), _rows(changes, cases_root, language)))
    if articles:
        articles.sort(key=lambda change: change.article_code or "")
        shown.append((ARTICLES, strings["hand_block_articles"], _rows(articles, cases_root, language)))
    return shown


def row_text(change: Change, cases_root: Path, language: str) -> str:
    """One change as its row shows it (see the module docstring)."""
    strings = STRINGS[language]
    old, new = (_value(value, change.kind, language) for value in (change.old_value, change.new_value))
    if change.kind == ARTICLE_KIND:
        back = change.rule_id == UNDO_RULE_ID and change.new_value is None
        return strings["hand_row:article_price_back" if back else "hand_row:article_price"].format(item=change.article_code, old=old, new=new)
    item = _item_code(change, cases_root) or strings["hand_unknown"]
    return strings[f"hand_row:{change.kind}"].format(line=change.line_id, item=item, old=old, new=new)


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


def intro(listed: list[tuple[str, str, list[tuple[str, str]]]], passed: int, language: str) -> str:
    """The text above the list, as Markdown: the intro, then a paragraph for each block with a hint (its first line in bold, then the
    hint), "Er is niets in te voeren." when no block is listed, and how many lines of the list could not be read."""
    strings = STRINGS[language]
    lines = [strings["hand_intro"]]
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

def _rows(changes: list[Change], cases_root: Path, language: str) -> list[tuple[str, str]]:
    return [(row_text(change, cases_root, language), "+".join(change.ids)) for change in changes]


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


def _item_code(change: Change, cases_root: Path) -> str | None:
    """The item code of the change's line in the version its case was compared with: step 01 of the newest case of the change that
    holds that version; None when no case does, or the line has no item code there."""
    for case_id in reversed(change.case_ids):
        try:
            versions = [PoVersion.model_validate(version) for version in load_case(cases_root, case_id).read_step("po_versions")["versions"]]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        version = next((version for version in versions if version.version == change.version), None)
        if version is not None:
            line = next((line for line in version.document.lines if line.line_id == change.line_id), None)
            return line.item_code.value if line is not None else None
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
