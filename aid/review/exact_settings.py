"""What the block "Koppeling met Exact (bij de installatie)" of the settings tab shows and writes, without gradio, so that it is tested
without a page (aid/review/page.py builds the block, as aid/review/buyer_settings.py is built for the buyer's part).

The block connects the aid to what Exact itself delivers; it changes nothing in Exact. Part 1 sets the layout of the purchase order
PDF that Exact e-mails, the setting po_layout (PoLayout of aid/readers/po_layout.py), and the senders of those e-mails, po_senders, from
one sample PDF of the company's own purchase order. The sample stays on the PC where the page runs: it is read where the page library
put it when it was chosen, it is copied nowhere and nothing is filed from it.

The first page. page_lines gives the lines of page 1 of the sample with their numbers, as the layout reader reads them (extract_rows
with cut_cells of aid/readers/pdf_text.py), numbered from 1 from the top as extract_lines numbers them: the number of a line is the
number that supplier_line takes and that a problem names as "Regel n van de eerste pagina". title_words gives the words of the first
line of page 1 that holds a word, or several words, as a word or phrase of its own, case not counting (as the reader looks for a label),
so that the title row is copied off the page word for word and never typed. A file that cannot be opened as a PDF (cut short,
damaged, locked or gone) raises SampleUnreadable from every function here that reads the sample, whatever the PDF library raised.

The form. FORM holds the fields of the form besides the title row, in the order of the page: the text before the PO number, before
the date, before the supplier (or the number of the line that holds the supplier alone), before the version, the currency and the
delivery terms; the end text; the number convention ("comma" for 1.234,50, "point" for 1,234.50); and the tick that every price is per
one unit. The title row is the list of words that title_words found, and each word has a dropdown with one of MEANINGS. layout_from_form
makes the layout of the form and of those meanings, or refuses it with a problem for each field at fault ("po_label", "supplier_line",
"title_row", "columns"), each a sentence in the page's language that names no value, by the rules PoLayout refuses a layout by (Task
31, rule 2): an empty PO label, a supplier text and a supplier line together or a line that is no whole number from 1, no title row,
one of one word or of more than SLOTS words, a word without a meaning, a first word that belongs to the column on its left, a meaning
of two columns, no item code, and neither a line number nor a quantity. form_from_layout gives the form back for a layout, so that the
block opens with the saved layout, and as_setting gives a layout as the settings file holds it; saved_values gives the two settings as
the file holds them, which the page keeps of the moment its form was filled, so that a save from a form that another browser tab
overtook is refused. senders_from reads the senders, one address or one domain (with or without the at sign before it) to each entry,
separated by commas, by the rule of the setting po_senders itself; an entry that is neither is named in the problem, since it is what
the owner typed.

The trial read. trial reads the sample with a layout, from one extraction of the text of the PDF, and gives what it read (the reader's
own reading, whatever it finds), the problems that keep the sample from being filed with the layout, the warnings of what the reader
passes by, and whether the total of the PO can be worked out; check gives the problems alone. A problem is: a PDF without text; a first
page without the title row; no PO label with a number after it; different numbers after the PO label, which the reader takes for two
POs in one PDF, named by the lines of the first page's table and the later pages that hold them, with the advice that a longer text
before the number reads the one line that holds the PO number; a PO number that cannot be the name of a folder; a line above the title
row that holds a word of an order confirmation, so that the watched folder would take the PDF for a confirmation (titled_confirmation
of aid/sources/mailbox.py), named by its line of the first page; no order line; a line without an item code or without a quantity,
named by its own line number ("Regel n"); and a column of numbers of which one is printed with exactly two digits after the decimal mark
of the other convention than the one chosen ("12,50" while the convention is "1,234.50"), named by its column with the convention that
fits. Last, the reader itself is asked on the rows already read (read_po_layout_rows): a refusal that none of these names is a problem
as well. A warning is: a line of page 1 that holds the PO label with no word with a digit after it, which the reader passes by; and a
page after the first title row that does not show the title row, whose rows the reader reads with the columns of the title row before
it, or does not read at all once the end text has ended the table. Problems and warnings name the setting, the column, the line or the
page, never a value of the PDF. reading_shown gives the reading as the page's two tables: the header values the layout asks for,
[what, value read], and a row for each order line, its line number and the columns of the layout in their order (line_headers, with
line_widths for their widths). A value is shown in its normal form, a number in plain digits without trailing zeros and with a point
for its decimals, so that a number never reads as a printed text that means another one ("1 (1.000)" is one, "1000 (1.000)" a
thousand), with the printed text in brackets when the two differ; a value printed and not readable is "?" with its text in brackets; a
header value that the layout asks for and that was not read is "?", and an empty cell is empty. The reading never shows the total of
the PO, the authorization limit or whether the total is above it.

The state. state_lines are the lines of "Stand van de koppeling": whether a layout is set; how many POs the aid knows, with the number,
the version and the time of the one filed last (known, by the time of its po.json); and how many files lie in the watched folder longer
than two passes of the runner (unread), which are those the aid could not read, since a file it handled moves to processed/. They show
no line of a document, no supplier and no amount, so that the owner can report them in words. Later tasks add their lines.
"""
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from itertools import groupby
from pathlib import Path
from typing import NamedTuple, get_args

from pydantic import TypeAdapter, ValidationError

from aid.po_store import PoStore, po_total
from aid.readers.pdf_text import TextRow, extract_rows
from aid.readers.po_export import is_folder_name
from aid.readers.po_layout import NOT_FIELDS, Meaning, PoLayout
from aid.readers.po_pdf import (after_label, folded, header_lines, header_values, label_pattern, layout_labels, read_layout,
                                read_po_layout_rows)
from aid.review.i18n import STRINGS
from aid.schema import DocLine, DocValue, Document
from aid.settings import Settings
from aid.sources.mailbox import titled_confirmation

SLOTS = 20                                       # the most words of a title row the form takes: one dropdown for each
MEANINGS: tuple[str, ...] = get_args(Meaning)    # what the column of a word of the title row holds, in the order of the dropdowns
FORM = ("po_label", "date_label", "supplier_label", "supplier_line", "version_label", "currency_label", "incoterm_label", "table_end", "numbers",
        "per_piece")                             # the fields of the form besides the title row, in the order of the page
LABELS = ("po_label", "date_label", "supplier_label", "version_label", "currency_label", "incoterm_label")      # the labels of a layout
HEADER = ("po_reference", "version_hint", "document_date", "supplier", "currency", "incoterm")                # the header values read
PAGE_LINES = 60                                  # the lines of page 1 the table shows
PASSES = 120                                     # seconds: two passes of the runner, which makes one every 60 (aid/runner.py)
_ASKED = dict(zip(HEADER, ("po_label", "version_label", "date_label", "supplier_label", "currency_label", "incoterm_label")))   # their labels
_CELLS = tuple(field for field in DocLine.model_fields if field != "line_id")          # the cells of an order line, in their order
_NUMBERS = ("quantity", "unit_price", "price_basis", "discount_pct", "net_price")       # the cells that hold a number
_SENDERS = TypeAdapter(Settings.model_fields["po_senders"].annotation)                 # the setting's own rule for its entries
_WHOLE = re.compile(r"[0-9]+")
_TWO_DECIMALS = {",": re.compile(r"\d,\d{2}(?!\d)"), ".": re.compile(r"\d\.\d{2}(?!\d)")}   # a number printed with two decimals after the mark
# The width of each column of the table of order lines, in parts of the whole, so that its cells wrap inside the block (line_widths).
_WIDTHS = {"line_id": 11, "item_code": 13, "description": 26, "quantity": 9, "unit": 8, "unit_price": 10, "price_basis": 9, "discount_pct": 9,
           "net_price": 10, "delivery_date": 17}


class SampleUnreadable(Exception):
    """The sample cannot be opened as a PDF: cut short, damaged, locked or gone. The page names it by a sentence of its own."""


class Trial(NamedTuple):
    """The trial read of a sample with a layout (see the module docstring)."""
    document: Document | None                    # what the layout reads, whatever it finds; None for a PDF without text
    problems: list[str]                          # what keeps the sample from being filed with the layout
    warnings: list[str]                          # what the reader passes by, or reads with the columns of an earlier page
    total: bool                                  # whether the total of the PO can be worked out


# --- the first page and the title row ---

def page_lines(pdf: Path, limit: int = PAGE_LINES) -> list[list[str]]:
    """[line number, text] of each of the first `limit` lines of page 1 of the PDF, as the layout reader reads them and numbered as
    extract_lines numbers them; [] for a PDF without text (a scan)."""
    return [[str(row.line.line), row.line.text] for row in _page_one(_rows(pdf))][:limit]


def title_words(pdf: Path, word: str) -> list[str] | None:
    """The words of the first line of page 1 that holds `word` as a word of its own (several words as a phrase), case and outer spaces
    not counting, as the layout reader cuts them; None when no line of page 1 holds it, for a PDF without text and for no word."""
    word = " ".join((word or "").split())
    if not word:
        return None
    pattern = label_pattern(word)
    return next((row.line.text.split() for row in _page_one(_rows(pdf)) if pattern.search(row.line.text)), None)


# --- the form ---

def layout_from_form(form: dict, meanings: list, language: str) -> tuple[PoLayout | None, dict[str, str]]:
    """The layout of the form (see the module docstring): its fields (FORM) and its title row (form["title_row"]), with the meaning
    chosen for each word of the title row; dropdowns beyond the title row are not read. Or None and the problem of each field at fault,
    in the page's language, several problems of a field in one text."""
    strings, problems = STRINGS[language], {}

    def refuse(field: str, text: str) -> None:
        problems[field] = f"{problems[field]} {text}" if field in problems else text

    title_row = [str(word) for word in form.get("title_row") or []]
    chosen = list(meanings or [])[:len(title_row)]
    chosen += [None] * (len(title_row) - len(chosen))
    if not _text(form.get("po_label")):
        refuse("po_label", strings["exact_problem:po_label"])
    line = _text(form.get("supplier_line"))
    if line and not (_WHOLE.fullmatch(line) and int(line) >= 1):
        refuse("supplier_line", strings["exact_problem:supplier_line"])
    elif line and _text(form.get("supplier_label")):
        refuse("supplier_line", strings["exact_problem:supplier"])
    if not title_row:
        refuse("title_row", strings["exact_problem:no_title"])
    elif len(title_row) > SLOTS:
        refuse("title_row", strings["exact_title_long"])
    elif len(title_row) < 2:
        refuse("title_row", strings["exact_problem:short_title"])
    elif any(meaning not in MEANINGS for meaning in chosen):
        refuse("columns", strings["exact_problem:meanings"])
    else:
        if chosen[0] == "same":
            refuse("columns", strings["exact_problem:first_same"])
        fields = [meaning for meaning in chosen if meaning not in NOT_FIELDS]
        for meaning in dict.fromkeys(meaning for meaning in fields if fields.count(meaning) > 1):
            refuse("columns", strings["exact_problem:twice"].format(meaning=strings[f"exact_meaning:{meaning}"]))
        if "item_code" not in chosen:
            refuse("columns", strings["exact_problem:item_code"])
        if "line_id" not in chosen and "quantity" not in chosen:
            refuse("columns", strings["exact_problem:quantity"])
    if problems:
        return None, problems
    try:
        layout = PoLayout(**{label: _text(form.get(label)) or None for label in LABELS}, supplier_line=int(line) if line else None,
                          title_row=title_row, columns=chosen, table_end=_text(form.get("table_end")) or None,
                          decimal_comma=form.get("numbers") != "point", prices_per_piece=bool(form.get("per_piece")))
    except ValidationError as error:             # not met after the checks above; named by the field, never by a value
        field = str(error.errors(include_input=False)[0]["loc"][0])
        key = {"po_label": "po_label", "supplier_line": "supplier_line", "title_row": "no_title"}.get(field, "meanings")
        return None, {field if field in ("po_label", "supplier_line", "title_row") else "columns": strings[f"exact_problem:{key}"]}
    return layout, {}


def form_from_layout(layout: PoLayout | None) -> dict:
    """The form as it shows a layout: the texts of FORM ("" for a label the layout leaves out), the number convention, the tick, the
    word of the title row that its search is filled with (the title of the item code), the title row and the meaning of each of its
    words. For no layout the empty form, numbers with a decimal comma."""
    if layout is None:
        return dict.fromkeys(FORM, "") | {"numbers": "comma", "per_piece": False, "title_word": "", "title_row": [], "columns": []}
    texts = {field: getattr(layout, field) or "" for field in (*LABELS, "table_end")}
    texts |= {"supplier_line": "" if layout.supplier_line is None else str(layout.supplier_line),
              "numbers": "comma" if layout.decimal_comma else "point", "per_piece": layout.prices_per_piece}
    return {field: texts[field] for field in FORM} | {"title_word": layout.title_row[layout.columns.index("item_code")],
                                                      "title_row": list(layout.title_row), "columns": list(layout.columns)}


def as_setting(layout: PoLayout) -> dict:
    """The layout as the settings file holds it: the keys it sets, in the order of PoLayout, without a label it leaves out."""
    return layout.model_dump(mode="json", exclude_none=True)


def saved_values(settings: Settings) -> dict:
    """The two settings of the block as the settings hold them, by which the page tells a form filled from the file as it is now from
    one that another browser tab overtook: {"po_layout": the layout as the file holds it (as_setting), or None; "po_senders": the list}."""
    return {"po_layout": as_setting(settings.po_layout) if settings.po_layout is not None else None, "po_senders": list(settings.po_senders)}


def senders_from(text: str | None, language: str) -> tuple[list[str] | None, str | None]:
    """The senders typed, separated by commas, each without its outer spaces (an empty entry is passed over): (the list, None), or
    (None, the problem), which names each entry that is neither an address nor a domain by the rule of the setting po_senders."""
    entries = [entry for entry in ((text or "").split(",")) if entry.strip()]
    entries = [entry.strip() for entry in entries]
    refused = [entry for entry in entries if not _is_sender(entry)]
    if refused:
        return None, " ".join(STRINGS[language]["exact_problem:sender"].format(text=entry) for entry in refused)
    return entries, None


def _is_sender(entry: str) -> bool:
    try:
        _SENDERS.validate_python([entry])
    except ValidationError:
        return False
    return True


# --- the trial read ---

def check(pdf: Path, layout: PoLayout, language: str) -> list[str]:
    """What keeps the sample from being filed with the layout, each in the page's language (see the module docstring); [] for none."""
    return trial(pdf, layout, language).problems


def trial(pdf: Path, layout: PoLayout, language: str) -> Trial:
    """The trial read of the sample with the layout (see the module docstring), from one extraction of the text of the PDF. A PDF that
    cannot be opened raises SampleUnreadable."""
    strings, pdf = STRINGS[language], Path(pdf)
    rows = _rows(pdf)
    if not rows:
        return Trial(None, [strings["exact_problem:scan"]], [], False)
    page = "\n".join(row.line.text for row in rows if row.line.page == 1)     # the first page as the watched folder holds it against the layout
    labels = layout_labels(layout)
    po_label, every = labels["po_reference"], list(labels.values())
    header = header_lines(page, layout)
    titled = len(header) < len(page.splitlines())                             # the title row stands on page 1
    labelled = any(text for line in page.splitlines() for text in after_label(line, po_label, every))
    numbers = list(header_values(rows, "po_reference", po_label, every))
    doc = read_layout(pdf, rows, layout)
    problems = [] if titled else [strings["exact_problem:title_differs"]]
    if not labelled or not numbers:
        problems.append(strings["exact_problem:no_number"])
    elif len({value.value for value in numbers}) > 1:
        problems.append(strings["exact_problem:two_pos"].format(places=_places(numbers, strings)))
    elif not is_folder_name(doc.po_reference.value):
        problems.append(strings["exact_problem:folder"])
    if titled:
        problems += [strings["exact_problem:confirmation"].format(line=n) for n, line in enumerate(header, 1) if titled_confirmation(line, layout)]
        if not doc.lines:
            problems.append(strings["exact_problem:no_lines"])
    for line in doc.lines:
        if line.item_code.value is None:
            problems.append(strings["exact_problem:no_item_code"].format(line=line.line_id))
        if line.quantity.value is None:
            problems.append(strings["exact_problem:no_quantity"].format(line=line.line_id))
    problems += _conventions(doc, layout, strings)
    if not problems:
        try:
            read_po_layout_rows(pdf, rows, layout, doc)      # the reader itself, for a refusal that the rules above do not know
        except ValueError:
            problems.append(strings["exact_problem:refused"])
    warnings = _passed(rows, layout, strings) + _pages(rows, layout, doc, strings)
    total = bool(doc.lines) and po_total(doc, "1" if layout.prices_per_piece else None) is not None
    return Trial(doc, problems, warnings, total)


def _places(numbers: list[DocValue], strings: dict[str, str]) -> str:
    """Where the PO label is followed by a number: the lines of the first page's table and the later pages, in words."""
    found = {(value.source.page, value.source.line) for value in numbers if value.source is not None}
    lines, pages = sorted(line for page, line in found if page == 1), sorted({page for page, _ in found if page > 1})
    parts = [strings["exact_place:line" if len(lines) == 1 else "exact_place:lines"].format(lines=_listed(lines, strings))] if lines else []
    if pages:
        parts.append(strings["exact_place:page" if len(pages) == 1 else "exact_place:pages"].format(pages=_listed(pages, strings)))
    return f" {strings['exact_and']} ".join(parts)


def _listed(numbers: list[int], strings: dict[str, str]) -> str:
    """Numbers in words: "2", "2 en 6", "2, 6 en 9"."""
    texts = [str(number) for number in numbers]
    return texts[0] if len(texts) == 1 else f"{', '.join(texts[:-1])} {strings['exact_and']} {texts[-1]}"


def _conventions(doc: Document, layout: PoLayout, strings: dict[str, str]) -> list[str]:
    """A problem for each column of numbers of which one is printed with exactly two digits after the decimal mark of the other
    convention than the one chosen ("12,50" while the convention is the point): the column, and the convention that fits it."""
    mark, key = (".", "exact_problem:convention_point") if layout.decimal_comma else (",", "exact_problem:convention_comma")
    return [strings[key].format(column=strings[f"exact_meaning:{field}"]) for field in _cells_of(layout)
            if field in _NUMBERS and any(_TWO_DECIMALS[mark].search(getattr(line, field).text or "") for line in doc.lines)]


def _passed(rows: list[TextRow], layout: PoLayout, strings: dict[str, str]) -> list[str]:
    """A warning for each line of page 1 that holds the PO label with no word with a digit after it at any of its places: the reader
    passes it by and takes the number from another line."""
    labels = layout_labels(layout)
    po_label, every = labels["po_reference"], list(labels.values())
    passed = []
    for row in _page_one(rows):
        places = after_label(row.line.text, po_label, every)
        words = [text.split()[0].rstrip(",;:.") if text else "" for text in places]
        if places and not any(any(char.isdigit() for char in word) for word in words):
            passed.append(strings["exact_warning:passed"].format(line=row.line.line))
    return passed


def _pages(rows: list[TextRow], layout: PoLayout, doc: Document, strings: dict[str, str]) -> list[str]:
    """A warning for each page after the first title row that does not show the title row: the reader reads its rows with the columns
    of the title row before it, or not at all when the end text ended the table before it. The table ends where the reader ends it: at
    the first row below a title row that is no order line and holds the end text."""
    title = folded(layout.title_row)
    end = label_pattern(layout.table_end) if layout.table_end else None
    lines = {(value.source.page, value.source.line) for line in doc.lines for value in (getattr(line, cell) for cell in _CELLS) if value.source}
    warnings, seen, ended = [], False, False
    for page, group in groupby(rows, key=lambda row: row.line.page):
        page_rows = list(group)
        at = next((n for n, row in enumerate(page_rows) if folded(word.text for word in row.words) == title), None)
        if at is not None:
            seen, ended, page_rows = True, False, page_rows[at + 1:]
        elif not seen:
            continue
        else:
            warnings.append(strings["exact_warning:ended" if ended else "exact_warning:continued"].format(page=page))
        if end is not None and not ended:
            ended = any((row.line.page, row.line.line) not in lines and end.search(row.line.text) for row in page_rows)
    return warnings


def reading_shown(doc: Document, language: str, layout: PoLayout | None = None) -> tuple[list[list[str]], list[list[str]]]:
    """The reading as the page's two tables (see the module docstring): the header rows [what, value read] and a row for each order
    line. With a layout, only the header values it has a label for and its columns; without one, every header value and every cell."""
    strings = STRINGS[language]
    header = [[strings[f"exact_header:{field}"], _shown(getattr(doc, field), asked=True)] for field in HEADER
              if layout is None or _asks(layout, field)]
    cells = _cells_of(layout)
    return header, [[line.line_id, *[_shown(getattr(line, cell), number=cell in _NUMBERS) for cell in cells]] for line in doc.lines]


def line_headers(language: str, layout: PoLayout | None = None) -> list[str]:
    """The column titles of the table of order lines that reading_shown gives: the line number, then the layout's columns."""
    strings = STRINGS[language]
    return [strings[f"exact_meaning:{field}"] for field in ("line_id", *_cells_of(layout))]


def line_widths(layout: PoLayout | None = None) -> list[str]:
    """The width of each column of the table of order lines (line_headers), in whole percents that make 100 together, so that the table
    fits the block and its cells wrap inside their columns; what the whole percents leave goes to the description, else the widest."""
    fields = ["line_id", *_cells_of(layout)]
    weights = [_WIDTHS[field] for field in fields]
    widths = [weight * 100 // sum(weights) for weight in weights]
    widest = fields.index("description") if "description" in fields else weights.index(max(weights))
    widths[widest] += 100 - sum(widths)
    return [f"{width}%" for width in widths]


def _cells_of(layout: PoLayout | None) -> list[str]:
    """The cells of a line that the table shows: those of the layout's columns, in their order; every cell without a layout."""
    if layout is None:
        return list(_CELLS)
    return [meaning for meaning in layout.columns if meaning not in (*NOT_FIELDS, "line_id")]


def _asks(layout: PoLayout, field: str) -> bool:
    """Whether the layout reads the header value: it has a label for it, or for the supplier the number of its line."""
    if field == "supplier" and layout.supplier_line is not None:
        return True
    return bool((getattr(layout, _ASKED[field]) or "").strip())


def _shown(value: DocValue, asked: bool = False, number: bool = False) -> str:
    """A value as the reading shows it (see the module docstring); `asked`: a header value the layout asks for; `number`: a cell that
    holds a number, shown in plain digits (_plain)."""
    if value.value is not None:
        shown = _plain(value.value) if number else value.value
        return f"{shown} ({value.text})" if value.text and value.text != shown else shown
    if value.text:
        return f"? ({value.text})"
    return "?" if asked else ""


def _plain(number: str) -> str:
    """A number as it will be filed, in plain digits without trailing zeros and with a point for its decimals: "1000", "12.5", "1"."""
    try:
        return format(Decimal(number).normalize(), "f")
    except InvalidOperation:
        return number


# --- the state of the connection ---

def known(store: PoStore) -> tuple[int, tuple[str, int, datetime] | None]:
    """The number of POs the store holds a version of, and the PO number, the version and the time of the version filed last, by the
    modification time of its po.json, which the store writes last (None for a store without a version)."""
    filed = []
    for path in store.root.glob("*/v*/po.json") if store.root.is_dir() else []:
        try:
            status = path.stat()
        except OSError:                          # taken away meanwhile
            continue
        if _WHOLE.fullmatch(path.parent.name[1:]):
            filed.append((status.st_mtime_ns, path.parent.parent.name, int(path.parent.name[1:]), status.st_mtime))
    if not filed:
        return 0, None
    _, number, version, moment = max(filed)
    return len({entry[1] for entry in filed}), (number, version, datetime.fromtimestamp(moment, timezone.utc))


def unread(watched_folder: Path, now: datetime, older_seconds: int = PASSES) -> int:
    """The files in the watched folder (not in a folder below it, and not a file whose name starts with a dot, which the scan leaves
    alone) that came into it more than older_seconds before `now`: those the aid could not read, since the scan moves every file it
    handled. A file came in at the later of its modification time and the time of its entry in the folder (on Windows its creation,
    elsewhere the last change of its entry), which a copy and a move set. A time without a zone is the PC's own. 0 without the folder."""
    try:
        files = [path for path in Path(watched_folder).iterdir() if not path.name.startswith(".") and path.is_file()]
    except OSError:
        return 0
    limit = now.timestamp() - older_seconds
    return sum(1 for path in files if (arrived := _arrived(path)) is not None and arrived < limit)


def _arrived(path: Path) -> float | None:
    try:
        status = path.stat()
    except OSError:
        return None
    return max(status.st_mtime, status.st_ctime)


def state_lines(settings: Settings, store: PoStore, now: datetime, language: str) -> list[str]:
    """The lines of "Stand van de koppeling" (see the module docstring), in the page's language; the time of the PO filed last in the
    local time of this PC."""
    strings = STRINGS[language]
    lines = [strings["exact_state_set" if settings.po_layout is not None else "exact_state_unset"]]
    count, last = known(store)
    if last is None:
        lines.append(strings["exact_state_none"])
    else:
        number, version, moment = last
        lines.append(strings["exact_state_known"].format(count=count, number=number, version=version,
                                                         time=moment.astimezone().strftime("%Y-%m-%d %H:%M")))
    lines.append(strings["exact_state_unread"].format(n=unread(settings.watched_folder, now)))
    return lines


# --- helpers ---

def _rows(pdf: Path) -> list[TextRow]:
    """The text rows of the PDF as the layout reader reads them. A file that cannot be opened as a PDF (cut short, damaged, locked or
    gone) raises SampleUnreadable, whatever the PDF library raised."""
    try:
        return extract_rows(Path(pdf), cut_cells=True)
    except Exception as error:
        raise SampleUnreadable(f"{Path(pdf).name} cannot be opened as a PDF ({type(error).__name__})") from error


def _page_one(rows: list[TextRow]) -> list[TextRow]:
    return [row for row in rows if row.line.page == 1]


def _text(value) -> str:
    """A value of a form as text without the space around it; "" for None."""
    return value.strip() if isinstance(value, str) else "" if value is None else str(value).strip()
