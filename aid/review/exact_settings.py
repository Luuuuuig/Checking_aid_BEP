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
so that the title row is copied off the page word for word and never typed. The page's field takes any file, so that a file whose
name does not end in .pdf raises NotPdf, and a file that cannot be opened as a PDF (cut short, damaged, locked or gone) raises
SampleUnreadable, from every function here that reads the sample, whatever the PDF library raised.

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
named by its own line number ("Regel n"); and a column of numbers of which one is printed with the decimal mark of the other convention
than the one chosen followed by one, two, four or more digits ("12,50" or "0,1250" while the convention is "1,234.50"; a mark followed by
exactly three digits proves no convention), named by its column with the convention that fits. Last, the reader itself is asked on the rows already read (read_po_layout_rows): a refusal that none of these names is a problem
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

The number recognition. Part 1 also sets how the watched folder finds the PO number of a confirmation: the setting po_number_pattern,
written from two fields, the words after which suppliers print the PO number and the fewest and the most digits of a PO number.
pattern_from makes the pattern of the fields: the words as alternatives without regard to case, then spaces, a colon, a hash, a full
stop or a dash, then a number of that many digits that no further digit follows. The words are typed with a comma between them; the
spaces around a word are not kept, and a space inside a word stands for any run of white space. It refuses, with a problem for each
field at fault ("words", "digits") in the page's language: no word, a number of digits that is no whole number from 1 to DIGITS_MAX,
the fewest above the most. pattern_parts reads the words and the digits back from a pattern of that form, or of the form of the
default pattern of the settings (no full stop before the number, and no check after it), and gives None for a pattern of another
form: one written into the settings file by hand, which the page shows as an own pattern and leaves alone while the fields are empty.
A longer number is taken whole or not at all under every pattern, since find_po_numbers of aid/sources/mailbox.py passes over a
number that a digit follows; so the default and the fields' form of its words and digits differ only in that the fields' form also
passes a full stop between the word and the number.

Part 2, the export of order lines. Before ordering no PO PDF exists, so a cart is checked against an export of the order lines that the
owner saves from Exact (aid/readers/po_export.py reads it). Part 2 sets the three settings of that export, export_columns,
export_decimal_comma and export_fill_down, from one sample export of the company, an .xlsx or .csv file that stays on the PC as the
sample of part 1 does. table_head gives the first HEAD_ROWS rows of the sample, each with its row number as a spreadsheet numbers it,
and header_choices the row with the column titles (the one given, else the first row with three filled cells) and its titles, which
fill one dropdown for each field of EXPORT_FORM beside the choice NO_COLUMN, "(staat niet in het bestand)"; sample_head gives both from
one read of the file. They read the file with the export reader's own helpers (_sheet): the first sheet of a workbook, or a CSV file in
the reader's encodings, cut at the mark under which the whole file holds the most filled cells, since under the file's own mark every
row is cut into its columns, while commas or semicolons inside some cells add cells on those rows only; on a tie, at the mark under
which the widest row holds the most filled cells; on a tie again, at the first of the reader's marks. The reader itself, which has the
map's titles, takes the first mark under which a row holds every title of its map; the two agree whenever the map's titles stand in the
row that the sample shows as its row of column titles and in no row cut at an earlier mark. A file that the reader refuses as a whole
raises the reader's ExportRefused. export_from_form makes the three settings of the form, or refuses it with a problem per field: the
PO number or the item code without a column, and a column chosen for more than one field. form_from_export gives the form back for the
settings, and saved_export the three settings as the file holds them, which the page keeps of the moment its form was filled.
export_trial reads the sample once with the three settings (read_export_table) and writes nothing. A refusal of the reader is shown by
a fixed sentence chosen by its reason (refusal_text), with the column or the row and never what a cell holds; otherwise a problem is:
rows whose item code is a date, a time or an error value, which the reader passes over; a column of numbers one of whose text cells
shows the other decimal mark than the convention chosen followed by one, two, four or more digits, named by its title with the choice
that fits (a number cell of a workbook never is: it is read as a number whatever the convention says); and a sample that gives no order
line. export_shown gives what the trial says (export_text), a count of one in the singular: the count line ("Gelezen: ...", with the
rows passed over because their item code cell is blank, a repeated row of column titles counted nowhere); a line for the rows with an
item code and no quantity, which the reader passed over and which refuse nothing; a line for each field but the item code with cells
that were not read, how many and the first row; and the problems. It gives the lines of the orders shown (export_lines): the first
order and, when the sample holds two or more, the last order, which is the order of the file's last order line (with the tick, where a
total row at the end of the file joins), and the PO the file names last when that is yet another, as in an export sorted by delivery
date; each order once, each line with its PO number and its number first and each value as reading_shown shows it, a number in plain
digits with the cell's text in brackets when the two differ. A column is named by its title in quotes.

The state. state_lines are the lines of "Stand van de koppeling": whether a layout is set; how many POs the aid knows, by a PDF version
or a look from an export, with the number and the time of what was filed last and the version of a PDF (known, by the time of its
po.json; a look is named as coming from an export); whether the columns of the export are set; when the last export was filed
(last_export, by the time of the po.json of the newest look); how many files lie in the watched folder longer than two passes of the
runner (unread), which are those the aid could not read, since a file it handled moves to processed/; how many cases wait for their
PO (cases_waiting_for_po of aid/review/close.py), which the runner checks again when the PO comes in; and, last, how many changes are
still to enter in Exact, counted as the tab "In Exact invoeren" counts them (still_to_enter), "?" while that list cannot be read. They
show no line of a document, no supplier and no amount, so that the owner can report them in words. Later tasks add their lines.
"""
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from itertools import groupby
from pathlib import Path
from typing import NamedTuple, get_args

from pydantic import TypeAdapter, ValidationError

from aid.execution.by_hand import FOLDER, open_changes
from aid.po_store import PoStore, po_total
from aid.readers.pdf_text import TextRow, extract_rows
from aid.readers.po_export import (DELIMITERS, EXPORT_FIELDS, EXPORT_SUFFIXES, ExportReading, ExportRefused, csv_rows, csv_text, is_folder_name,
                                   read_export_table, workbook_rows)
from aid.readers.po_layout import NOT_FIELDS, Meaning, PoLayout
from aid.readers.po_pdf import (after_label, folded, header_lines, header_values, label_pattern, layout_labels, read_layout,
                                read_po_layout_rows)
from aid.review.close import cases_waiting_for_po
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
# A number whose mark is followed by one, two, four or more digits and no further digit ("12,50", "0,1250", "4,8"): that mark is its
# decimal mark under either convention, so it proves the convention of the document; a mark followed by exactly three digits proves nothing.
_DECIMAL_MARK = {",": re.compile(r"\d,(?:\d{1,2}|\d{4,})(?!\d)"), ".": re.compile(r"\d\.(?:\d{1,2}|\d{4,})(?!\d)")}
_FILED = re.compile(r"([ve])(0|[1-9][0-9]*)")    # the folder of a PDF version ("v2") or of a look from an export ("e1") in the PO store
DIGITS_MAX = 20                                  # the most digits the fields of the number recognition take
# The forms of a PO number pattern that pattern_parts reads, as (what stands between a word and the number, what follows the number):
# the form the fields write, and the form of the default pattern of the settings.
_PATTERN_FORMS = ((r"[\s:#.-]*", r"(?!\d)"), (r"[\s:#-]*", ""))
# The width of each column of the table of order lines, in parts of the whole, so that its cells wrap inside the block (line_widths).
_WIDTHS = {"line_id": 11, "item_code": 13, "description": 26, "quantity": 9, "unit": 8, "unit_price": 10, "price_basis": 9, "discount_pct": 9,
           "net_price": 10, "delivery_date": 17}
EXPORT_FORM = EXPORT_FIELDS                      # part 2: one dropdown for each field of the export, in the order of the page
NO_COLUMN = "(none)"                             # the value of the choice "(staat niet in het bestand)" of those dropdowns
HEAD_ROWS = 15                                   # the rows of an export sample that its table shows
_REQUIRED = ("po_number", "item_code")           # the fields whose column must be chosen


class SampleUnreadable(Exception):
    """The sample cannot be opened as a PDF: cut short, damaged, locked or gone. The page names it by a sentence of its own."""


class NotPdf(Exception):
    """The sample of part 1 is no PDF by its name, which does not end in .pdf (the runner, too, takes a PDF by its name): the field
    takes any file, so that the page names this by a sentence of its own, which says that the name is the cause."""


class Trial(NamedTuple):
    """The trial read of a sample with a layout (see the module docstring)."""
    document: Document | None                    # what the layout reads, whatever it finds; None for a PDF without text
    problems: list[str]                          # what keeps the sample from being filed with the layout
    warnings: list[str]                          # what the reader passes by, or reads with the columns of an earlier page
    total: bool                                  # whether the total of the PO can be worked out


class ExportTrial(NamedTuple):
    """The trial read of an export sample with the three settings of part 2 (see the module docstring)."""
    reading: ExportReading | None                # what the export reader read; None when it refused the file
    problems: list[str]                          # what keeps the sample from being read with the settings, in the page's language


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


# --- the number recognition ---

def typed_words(text: str | None) -> list[str]:
    """The words of the field of the number recognition: the parts between its commas, each with single spaces and without the spaces
    around it, each once and in their order; an empty part is passed over."""
    return list(dict.fromkeys(" ".join(part.split()) for part in (text or "").split(",") if part.strip()))


def pattern_from(words: str, low: str, high: str, language: str) -> tuple[str | None, dict[str, str]]:
    """The PO number pattern of the fields (see the module docstring), and {}; or None and the problem of each field at fault,
    "words" and "digits", in the page's language. `low` and `high` are the fewest and the most digits as typed."""
    strings, problems = STRINGS[language], {}
    listed, least, most = typed_words(words), _digit_count(low), _digit_count(high)
    if not listed:
        problems["words"] = strings["exact_problem:po_words"]
    if least is None or most is None or least > most:
        problems["digits"] = strings["exact_problem:po_digits"]
    if problems:
        return None, problems
    return _pattern(listed, least, most), {}


def pattern_parts(pattern: str) -> tuple[str, int, int] | None:
    """The words, joined by a comma and a space as the field shows them, and the fewest and the most digits of a pattern of the form
    the fields write or of the form of the default pattern; None for a pattern of another form (see the module docstring). A pattern
    is of the form only when the words and the digits read from it give the same pattern again: so a word that the fields could not
    have written, an empty one or one with a comma or a mark that is not escaped, makes it another form."""
    for between, after in _PATTERN_FORMS:
        found = re.fullmatch(r"\(\?i:(.+)\)" + re.escape(between) + r"\(\\d\{([0-9]+),([0-9]+)\}\)" + re.escape(after), pattern, re.DOTALL)
        if found is None:
            continue
        words, least, most = _alternatives(found[1]), _digit_count(found[2]), _digit_count(found[3])
        if (words is None or least is None or most is None or least > most or any(not word or "," in word for word in words)
                or _pattern(words, least, most, between, after) != pattern):
            return None
        return ", ".join(words), least, most
    return None


def _pattern(words: list[str], least: int, most: int, between: str = _PATTERN_FORMS[0][0], after: str = _PATTERN_FORMS[0][1]) -> str:
    """The pattern of the words and the digits, in the form the fields write unless another is given: each word with its marks escaped
    and a run of white space where it has a space."""
    alternatives = "|".join(r"\s+".join(re.escape(part) for part in word.split()) for word in words)
    return f"(?i:{alternatives}){between}(\\d{{{least},{most}}}){after}"


def _alternatives(text: str) -> list[str] | None:
    """The words of the alternatives of a pattern as _pattern writes them, a space for each run of white space; None when a backslash
    ends the text or stands before an s that no plus follows."""
    words, word, chars = [], [], iter(text)
    for char in chars:
        if char == "\\":
            escaped = next(chars, None)
            if escaped is None or (escaped == "s" and next(chars, None) != "+"):
                return None
            word.append(" " if escaped == "s" else escaped)
        elif char == "|":
            words.append("".join(word))
            word = []
        else:
            word.append(char)
    return [*words, "".join(word)]


def _digit_count(text: str | None) -> int | None:
    """A number of digits as the fields take it: a whole number from 1 to DIGITS_MAX, written in digits; None for anything else."""
    text = (text or "").strip()
    return int(text) if _WHOLE.fullmatch(text) and 1 <= int(text) <= DIGITS_MAX else None


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
    """A problem for each column of numbers of which one is printed with the decimal mark of the other convention than the one chosen
    followed by one, two, four or more digits (_DECIMAL_MARK: "12,50" or "0,1250" while the convention is the point; a mark followed by
    exactly three digits proves nothing): the column, and the convention that fits it."""
    mark, key = (".", "exact_problem:convention_point") if layout.decimal_comma else (",", "exact_problem:convention_comma")
    return [strings[key].format(column=strings[f"exact_meaning:{field}"]) for field in _cells_of(layout)
            if field in _NUMBERS and any(_DECIMAL_MARK[mark].search(getattr(line, field).text or "") for line in doc.lines)]


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


# --- part 2: the export of order lines ---

def table_head(path: Path, rows: int = HEAD_ROWS) -> list[list[str]]:
    """The first `rows` rows of an export sample, each its row number and the texts of its cells, all as wide as the widest of them once
    the empty cells at the end of each row are left out; [] for an empty file. The file is read as _sheet says, and one that the export
    reader refuses as a whole raises its ExportRefused."""
    return _head(_sheet(Path(path)), rows)


def head_headers(head: list[list[str]], language: str) -> list[str]:
    """The column titles of the table of the first rows (table_head): "Rij", then each column by its letter, as a spreadsheet names it."""
    width = max((len(row) - 1 for row in head), default=0)
    return [STRINGS[language]["exact_export_row"], *[_letters(column) for column in range(width)]]


def header_choices(path: Path, row: int | None = None) -> tuple[int, list[str]]:
    """The row with the column titles of an export sample and its titles: the row given (counted from 1), else the first row with three
    filled cells, else the first filled row, else row 1. Its titles are the texts of its cells without the spaces around them, an empty
    cell left out and a title that stands twice taken once, compared as the export reader compares titles (case not counting); none for
    a row beyond the file. The file is read as _sheet says."""
    return _titled(_sheet(Path(path)), row)


def sample_head(path: Path, row: int | None = None, rows: int = HEAD_ROWS) -> tuple[list[list[str]], int, list[str]]:
    """The first rows of an export sample (as table_head gives them), its row with the column titles and their titles (as header_choices
    gives them), from one read of the file."""
    sheet = _sheet(Path(path))
    return (_head(sheet, rows), *_titled(sheet, row))


def export_from_form(form: dict, language: str) -> tuple[dict | None, dict[str, str]]:
    """The three settings of the form of part 2 (see the module docstring): export_columns, the title chosen for each field of EXPORT_FORM
    that has one, without the spaces around it and in the order of EXPORT_FORM; export_decimal_comma, the convention ("point" or else the
    comma); export_fill_down, the tick. Or None and the problem of each field at fault, in the page's language: the PO number or the item
    code without a column, and a column chosen for more than one field, named once by the field that chose it the second time, the
    titles compared as the export reader compares them."""
    strings, problems = STRINGS[language], {}
    chosen = {field: _text(form.get(field)) for field in EXPORT_FORM if _text(form.get(field)) not in ("", NO_COLUMN)}
    for field in _REQUIRED:
        if field not in chosen:
            problems[field] = strings[f"exact_export_problem:{field}"]
    first: dict[str, str | None] = {}            # a title as compared -> the title as first chosen; None once it was named
    for field, title in chosen.items():
        folded_title = title.casefold()
        if first.get(folded_title) is not None:
            problems[field] = strings["exact_export_problem:more_than_once"].format(column=_quoted(first[folded_title]))
            first[folded_title] = None
        first.setdefault(folded_title, title)
    if problems:
        return None, problems
    return {"export_columns": chosen, "export_decimal_comma": form.get("numbers") != "point", "export_fill_down": bool(form.get("fill_down"))}, {}


def form_from_export(settings: Settings) -> dict:
    """The form of part 2 as it shows the settings: the title of each field of EXPORT_FORM, without the spaces around it (NO_COLUMN for a
    field the map leaves out), the number convention ("comma" or "point") and the tick."""
    columns = {field: title.strip() for field, title in settings.export_columns.items()}
    return {field: columns.get(field) or NO_COLUMN for field in EXPORT_FORM} | {
        "numbers": "comma" if settings.export_decimal_comma else "point", "fill_down": settings.export_fill_down}


def saved_export(settings: Settings) -> dict:
    """The three settings of part 2 as the settings hold them, by which the page tells a form filled from the file as it is now from one
    that another browser tab, or a change of the file itself, overtook (as saved_values does for part 1)."""
    return {"export_columns": dict(settings.export_columns), "export_decimal_comma": settings.export_decimal_comma,
            "export_fill_down": settings.export_fill_down}


def field_label(field: str, language: str) -> str:
    """The label of the dropdown of a field of EXPORT_FORM: the PO number and the item code with "(verplicht)", the supplier as the reading
    of part 1 names it, any other field as part 1 names what a column holds."""
    strings = STRINGS[language]
    if field in _REQUIRED:
        return strings[f"exact_export_field:{field}"]
    return strings["exact_header:supplier" if field == "supplier" else f"exact_meaning:{field}"]


def export_trial(path: Path, values: dict, language: str) -> ExportTrial:
    """The trial read of an export sample with the three settings `values` (export_from_form gives them; a convention or a tick left out
    is False), which reads the file once (read_export_table) and writes nothing: what the export reader reads, and the problems that
    keep the sample from being read with the settings. A refusal of the reader is its fixed sentence (refusal_text) and nothing is read;
    else the problems are the rows whose item code is a date, a time or an error value, which the reader passes over (one problem, with
    how many there are and the first row), each column of numbers whose text shows the other convention than the one chosen
    (_conventions_of), and a sample that gives no order line. A file that cannot be opened raises its OSError."""
    strings, path = STRINGS[language], Path(path)
    columns, decimal_comma = dict(values.get("export_columns") or {}), bool(values.get("export_decimal_comma"))
    try:
        reading, table, header = read_export_table(path, columns, decimal_comma, bool(values.get("export_fill_down")))
    except ExportRefused as refused:
        return ExportTrial(None, [refusal_text(refused, language)])
    dated = reading.unread.get("item_code", [])
    problems = [_counted(strings, "exact_export_problem:item_code_date", dated)] if dated else []
    problems += _conventions_of(table, header, columns, decimal_comma, strings)
    if not reading.documents:
        problems.append(strings["exact_export_problem:no_lines"])
    return ExportTrial(reading, problems)


def refusal_text(error: ExportRefused, language: str) -> str:
    """The fixed sentence by which the page shows a refusal of the export reader: chosen by its reason (one of REFUSALS of
    aid/readers/po_export.py), with the column in quotes and the row where the reader gives them, and never what a cell holds. A cell of
    a workbook that cannot be read, whose row the reader gives, has a sentence of its own."""
    key = f"exact_export_refused:{error.reason}" + ("_row" if error.reason == "unreadable" and error.row is not None else "")
    return STRINGS[language][key].format(column=_quoted(error.column or ""), row=error.row)


def export_text(tried: ExportTrial, values: dict, language: str) -> str:
    """What a trial read says, one paragraph each, a count of one in the singular: the count line when the reader read an order (the
    orders and their lines, the rows without an item code passed over, and the order or orders shown below); the rows with an item code
    and no quantity, which the reader passed over (how many, the first row, and the quantity's field as the form names it); for each
    field with cells that were not read, but the item code, how many and the first row, the column by its title; then the problems. A
    repeated row of column titles is counted nowhere."""
    strings, reading, lines = STRINGS[language], tried.reading, []
    if reading is not None:
        if reading.documents:
            lines.append(_read_line(reading, strings))
        if reading.without_quantity:
            lines.append(_counted(strings, "exact_export_no_quantity", reading.without_quantity, field=field_label("quantity", language)))
        if reading.documents:
            titles = values.get("export_columns") or {}
            lines += [_counted(strings, "exact_export_unread", rows, column=_quoted(titles.get(field, field).strip()))
                      for field, rows in reading.unread.items() if field != "item_code"]
    return "\n\n".join(lines + tried.problems)


def export_lines(tried: ExportTrial, values: dict, language: str) -> list[list[str]]:
    """The lines of the orders a trial read shows (_orders_shown: the first order and, when it read two orders or more, the last), as
    the table shows them (export_headers): the PO number, the line number and each field of the settings' map, a value as reading_shown
    shows it (a number in plain digits, the cell's text in brackets when the two differ, "?" for a cell that is no value); [] when no
    order was read."""
    if tried.reading is None or not tried.reading.documents:
        return []
    return [[_line_value(doc, line, field) for field in _export_shown(values)] for doc in _orders_shown(tried.reading) for line in doc.lines]


def _orders_shown(reading: ExportReading) -> list[Document]:
    """The orders of a reading that the trial shows: the first order and, when the reader read two or more, the order of the file's
    last order line (with the tick, where a total row at the end of the file joins: the last order in the file's own sense) and the PO
    the file names last when that is yet another, as in an export sorted by delivery date; each once, in that order."""
    documents = reading.documents
    of_last_line = next((doc for doc in documents if doc.po_reference.value == reading.last_line_po), documents[-1])
    shown: list[Document] = []
    for doc in (documents[0], of_last_line, documents[-1]):
        if all(doc is not other for other in shown):
            shown.append(doc)
    return shown


def export_headers(values: dict | None, language: str) -> list[str]:
    """The column titles of the table of the orders shown (export_lines) for the three settings `values`; for None, every field of
    EXPORT_FORM, as the empty table shows them."""
    strings = STRINGS[language]
    names = {"po_number": "exact_header:po_reference", "supplier": "exact_header:supplier"}
    return [strings[names.get(field, f"exact_meaning:{field}")] for field in _export_shown(values)]


def export_shown(path: Path, values: dict, language: str) -> tuple[str, list[list[str]]]:
    """The trial read of an export sample as the page shows it: what it says (export_text) and the lines of its first and its last order
    (export_lines). A file that cannot be opened raises its OSError."""
    tried = export_trial(path, values, language)
    return export_text(tried, values, language), export_lines(tried, values, language)


def _sheet(path: Path) -> list[list]:
    """Every row of an export sample as its cells, each with its text and its kind, blank rows included, read with the export reader's
    own helpers: the first sheet of a workbook, or a CSV file in the reader's encodings, cut at the mark of the reader's (DELIMITERS)
    under which the whole file holds the most filled cells, since under the file's own mark every row is cut into its columns, while
    commas or semicolons inside some cells add cells on those rows only; on a tie, at the mark under which the widest row holds the
    most filled cells; on a tie again, at the first of DELIMITERS. The reader chooses its mark otherwise, since it has the map's
    titles: the first mark under which a row holds every title of the map. The two agree whenever the map's titles stand in the row
    that the sample shows as its row of column titles and in no row cut at an earlier mark. Raises the reader's ExportRefused for a
    file of another type, a workbook that cannot be read, and a CSV file that the csv module cannot cut, that no mark cuts into two
    cells, whose text holds NUL characters (UTF-16 without its mark) or that is in no encoding the reader reads. A file that cannot be
    opened raises its OSError."""
    suffix = path.suffix.lower()
    if suffix not in EXPORT_SUFFIXES:
        raise ExportRefused(f"{path.name} is neither a .csv nor an .xlsx file", "file_type")
    if suffix == ".xlsx":
        return workbook_rows(path)
    text = csv_text(path)
    tables = [table for table in (csv_rows(text, mark) for mark in DELIMITERS) if table is not None]
    if not tables:
        raise ExportRefused(f"{path.name} cannot be read as CSV", "unreadable")
    not_cut = ExportRefused(f"{path.name} uses a delimiter or an encoding the aid does not read", "delimiter_or_encoding")
    if "\x00" in text:
        raise not_cut
    rows = [row for table in tables for row in table]
    if any(cell.text.strip() for row in rows for cell in row) and all(len(row) <= 1 for row in rows):
        raise not_cut
    # each mark by the filled cells of the whole file, then by the filled cells of its widest row; the first mark of the best
    ranks = [(sum(_filled(row) for row in table), max((_filled(row) for row in table), default=0)) for table in tables]
    return tables[ranks.index(max(ranks))]


def _head(sheet: list[list], rows: int) -> list[list[str]]:
    """The first `rows` rows of a sample read by _sheet, as table_head gives them."""
    shown = [[cell.text for cell in row] for row in sheet[:rows]]
    shown = [row[:_used(row)] for row in shown]
    width = max((len(row) for row in shown), default=0)
    return [[str(number), *row, *[""] * (width - len(row))] for number, row in enumerate(shown, 1)]


def _titled(sheet: list[list], row: int | None) -> tuple[int, list[str]]:
    """The row with the column titles of a sample read by _sheet and its titles, as header_choices gives them."""
    if row is None:
        row = next((n for n, cells in enumerate(sheet, 1) if _filled(cells) >= 3), None) or next(
            (n for n, cells in enumerate(sheet, 1) if _filled(cells)), 1)
    titles: dict[str, str] = {}
    for cell in sheet[row - 1] if 1 <= row <= len(sheet) else []:
        if cell.text.strip():
            titles.setdefault(cell.text.strip().casefold(), cell.text.strip())
    return row, list(titles.values())


def _read_line(reading: ExportReading, strings: dict[str, str]) -> str:
    """The count line of a trial read: the orders and their lines ("samen" only for more than one order), the rows whose item code cell
    is blank, and the order or orders shown below it, a count of one in the singular."""
    orders, lines, passed = len(reading.documents), reading.rows, len(reading.without_item_code)
    counted_lines = strings["exact_export_lines_one"] if lines == 1 else strings["exact_export_lines_many"].format(m=lines)
    counted_orders = (strings["exact_export_orders_one"].format(lines=counted_lines) if orders == 1 else
                      strings["exact_export_orders_many"].format(n=orders, lines=counted_lines))
    counted_passed = strings["exact_export_passed_one"] if passed == 1 else strings["exact_export_passed_many"].format(k=passed)
    below = strings["exact_export_first" if orders == 1 else "exact_export_first_and_last"]
    return strings["exact_export_read"].format(orders=counted_orders, passed=counted_passed, below=below)


def _counted(strings: dict[str, str], key: str, rows: list[int], **values) -> str:
    """The sentence of `key` for these rows: its form for one row ("<key>_one", with that row) or for more (with how many and the first)."""
    if len(rows) == 1:
        return strings[f"{key}_one"].format(row=rows[0], **values)
    return strings[key].format(count=len(rows), row=rows[0], **values)


def _conventions_of(table: list[list], header: int, columns: dict[str, str], decimal_comma: bool, strings: dict[str, str]) -> list[str]:
    """A problem for each column of numbers of the map (quantity, price, price basis, discount, net price) one of whose text cells, on a
    row below the row with the column titles that holds an item code, shows a number with the decimal mark of the other convention than
    the one chosen followed by one, two, four or more digits (_DECIMAL_MARK: "12,50" or "0,1250" while the convention is the point): the
    column by its title, and the choice that fits. A number cell of a workbook is read as a number whatever the convention says, so it
    never counts. The table is the one the export reader read with the map (read_export_table), and `header` the index of its row of
    column titles."""
    titles = {field: title.strip() for field, title in columns.items()}
    if "item_code" not in titles:
        return []
    where = {field: next(at for at, cell in enumerate(table[header]) if cell.text.strip().casefold() == title.casefold())
             for field, title in titles.items()}
    mark, key = (".", "exact_problem:convention_point") if decimal_comma else (",", "exact_problem:convention_comma")
    lines = [row for row in table[header + 1:] if _cell_at(row, where["item_code"])[0].strip()]
    return [strings[key].format(column=_quoted(titles[field])) for field in EXPORT_FORM if field in _NUMBERS and field in where
            and any(kind == "text" and _DECIMAL_MARK[mark].search(text) for text, kind in (_cell_at(row, where[field]) for row in lines))]


def _cell_at(row: list, at: int) -> tuple[str, str]:
    """The text and the kind of the cell of a row at a place; a blank text cell beyond the end of a short row."""
    return (row[at].text, row[at].kind) if at < len(row) else ("", "text")


def _export_shown(values: dict | None) -> list[str]:
    """The fields of the table of the orders shown: the PO number, the line number (numbered without a column), then the fields of the map
    in the order of EXPORT_FORM; every field for no settings."""
    if values is None:
        return list(EXPORT_FORM)
    columns = values.get("export_columns") or {}
    return ["po_number", "line_id", *[field for field in EXPORT_FORM if field in columns and field not in ("po_number", "line_id")]]


def _line_value(doc: Document, line: DocLine, field: str) -> str:
    """One cell of the table of the orders shown: the PO's number and supplier, the line's number, or the value of the line."""
    if field == "line_id":
        return line.line_id
    if field in ("po_number", "supplier"):
        return _shown(doc.po_reference if field == "po_number" else doc.supplier)
    return _shown(getattr(line, field), number=field in _NUMBERS)


def _filled(row: list) -> int:
    return sum(1 for cell in row if cell.text.strip())


def _used(texts: list[str]) -> int:
    """The number of cells of a row up to its last filled one."""
    return max((n for n, text in enumerate(texts, 1) if text.strip()), default=0)


def _letters(column: int) -> str:
    """A column by its letters, as a spreadsheet names it, counted from 0: "A", ..., "Z", "AA"."""
    letters, column = "", column + 1
    while column:
        column, rest = divmod(column - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def _quoted(title: str) -> str:
    return f"\"{title}\""


# --- the state of the connection ---

def known(store: PoStore) -> tuple[int, tuple[str, int | None, datetime] | None]:
    """The number of POs the store holds as they stand, by a PDF version or by a look from an export, and what was filed last, by the
    modification time of its po.json, which the store writes last: its PO number, the version of a PDF version or None for a look from
    an export, and the time. On equal times a PDF version counts before a look, as the PO as it stands takes it (aid/po_store.py), and
    then the highest PO number. None for a store that holds neither."""
    filed = []
    for path in store.root.glob("*/*/po.json") if store.root.is_dir() else []:
        folder = _FILED.fullmatch(path.parent.name)
        if folder is None:
            continue
        try:
            status = path.stat()
        except OSError:                          # taken away meanwhile
            continue
        number = path.parent.parent.name
        filed.append((status.st_mtime_ns, folder[1] == "v", len(number), number, int(folder[2]), status.st_mtime))
    if not filed:
        return 0, None
    _, pdf, _, number, version, moment = max(filed)
    return len({entry[3] for entry in filed}), (number, version if pdf else None, datetime.fromtimestamp(moment, timezone.utc))


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


def last_export(store: PoStore) -> datetime | None:
    """When the newest look at a PO that an export gave was filed: the latest filed_at of the store's looks (the time of each look's
    po.json), in local time; None for a store without a look."""
    filed = []
    for path in store.root.glob("*/e*/po.json") if store.root.is_dir() else []:
        try:
            filed.append(store.filed_at(path.parent.parent.name, path.parent.name))
        except (OSError, ValueError):            # taken away meanwhile, or a folder that is no look
            continue
    return max(filed, default=None)


def state_lines(settings: Settings, store: PoStore, now: datetime, language: str) -> list[str]:
    """The lines of "Stand van de koppeling" (see the module docstring), in the page's language; the time of the PO filed last and of the
    last export in the local time of this PC."""
    strings = STRINGS[language]
    lines = [strings["exact_state_set" if settings.po_layout is not None else "exact_state_unset"]]
    count, last = known(store)
    if last is None:
        lines.append(strings["exact_state_none"])
    else:
        number, version, moment = last
        lines.append(strings["exact_state_known" if version is not None else "exact_state_known_export"].format(
            count=count, number=number, version=version, time=moment.astimezone().strftime("%Y-%m-%d %H:%M")))
    lines.append(strings["exact_state_columns_set" if settings.export_columns else "exact_state_columns_unset"])
    filed = last_export(store)
    lines.append(strings["exact_state_no_export"] if filed is None else
                 strings["exact_state_export"].format(time=filed.astimezone().strftime("%Y-%m-%d %H:%M")))
    lines.append(strings["exact_state_unread"].format(n=unread(settings.watched_folder, now)))
    lines.append(strings["exact_state_waiting"].format(n=len(cases_waiting_for_po(settings.cases_root))))
    lines.append(_to_enter_line(settings, strings))
    return lines


def still_to_enter(cases_root: Path) -> int | None:
    """The number of changes still to enter in Exact, counted as the tab "In Exact invoeren" counts them (open_changes of
    aid/execution/by_hand.py, over <cases_root>/prepared); None while that list cannot be read, which the tab shows as "(?)"."""
    try:
        return len(open_changes(Path(cases_root) / FOLDER))
    except Exception:                            # a file of the list held by another program, or any other error, as the tab says it
        return None


def _to_enter_line(settings: Settings, strings: dict[str, str]) -> str:
    """The state line of the changes still to enter in Exact, "?" for the number while the list cannot be read."""
    count = still_to_enter(settings.cases_root)
    return strings["exact_state_to_enter"].format(n="?" if count is None else count)


# --- helpers ---

def _rows(pdf: Path) -> list[TextRow]:
    """The text rows of the PDF as the layout reader reads them. A file whose name does not end in .pdf (in any case) raises NotPdf; a
    file that cannot be opened as a PDF (cut short, damaged, locked or gone) raises SampleUnreadable, whatever the PDF library raised."""
    if Path(pdf).suffix.lower() != ".pdf":
        raise NotPdf(f"{Path(pdf).name}: its name does not end in .pdf")
    try:
        return extract_rows(Path(pdf), cut_cells=True)
    except Exception as error:
        raise SampleUnreadable(f"{Path(pdf).name} cannot be opened as a PDF ({type(error).__name__})") from error


def _page_one(rows: list[TextRow]) -> list[TextRow]:
    return [row for row in rows if row.line.page == 1]


def _text(value) -> str:
    """A value of a form as text without the space around it; "" for None."""
    return value.strip() if isinstance(value, str) else "" if value is None else str(value).strip()
