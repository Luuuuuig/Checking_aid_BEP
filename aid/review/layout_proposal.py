"""What the first page of a sample purchase order shows of its layout: the detection behind the proposal of the settings page
(proposal of aid/review/exact_settings.py), without the page library and without the page's texts.

The owner sets the layout of the company's purchase order PDF (PoLayout of aid/readers/po_layout.py) from one sample, by copying the
texts of the sample into a form. detect looks on the first page of the sample for what can be filled in beforehand. What it gives is
a guess: the builder of the aid never saw a purchase order of the company, so the word lists below are guesses as well. Nothing here
is saved, and the owner checks every field against the trial read of the form. So the rule throughout is that a wrong guess is worse
than an open field: what is not known by a listed word, or by the page itself beyond doubt, is left to the owner.

The word lists stand first, as plain data, so that the owner can add the words of the company's own PDF. A word or a title is compared
without regard to case and without a full stop or a colon at its end, so every entry is written in small letters and without them.

The title row. A line of page 1 can be the title row when it has from two words to as many as the form takes, none of them with a
digit or a colon and two of them further apart than a space, as the titles of two columns are and the words of a name or a sentence
are not, and when the line right under it holds a word with a digit, as an order line does. Lines with two or more words that are
titles of TITLES go before the others. Of those that are left the line is taken that has the most of its words above a word of the
line right under it, since the first order line fills its columns; among equals the one with the most such words over all lines under
it, then the upper one. With titles that no list knows and a first order line that leaves most columns empty this can be another
table than the order lines; the owner sees the line number, and the search of the title row by one of its words stays for that case.
No title row is proposed when the line so taken is not also the first by the words above the lines under it alone, as with a small
table of listed titles above order lines under titles that no list knows, or when the line right above it has the words of a title
row as well, two of them over its words, as a title row printed over two lines has ("Unit" over "price"): the owner chooses then.

The columns. The order lines are the lines under the title row that have a word under at least half of its words and hold a digit;
they are cut into cells as the reader cuts them (title_spans and cells_under of aid/readers/po_pdf.py, numbers read with a decimal
comma where two words are one number). A word of the title row belongs to the title on its left ("same") when the gap between the
two is no wider than an average character of theirs and fewer than half of the order lines have a cell under each. The meaning of a
column is then, in this order:
- the meaning that TITLES gives its whole title ("Prijs per eenheid", never one word of it); a field that two titles would get is
  given to neither;
- by its cells, for a title that no list knows: the delivery date when every cell reads as a date or a week, the unit when every cell
  is a unit of UNITS, the item code when every cell is one word with a letter and a digit, the description when every cell holds a
  letter and one of them several words; and only when one such column alone is open and no title has the field yet. Cells that are
  each a letter and a number in one word and read as a week ("W48") may be weeks and may be codes, so they are neither;
- by arithmetic: when exactly one choice of three columns of numbers has the first times the second equal to the third on every
  order line, and there are two lines or more, the third is a line amount, which is not read ("skip"); the factor printed whole on
  every line is then the quantity and the one printed with two or more decimals the unit price, when one is each;
- else None: the owner chooses.

The labels. They are looked for on the lines above the title row, on the whole page when there is none. A label is the text before a
value in a part of a line (_pairs), and holds no digit. The PO label comes from the known words only (PO_WORDS, with NUMBER_WORDS
beside them), before a word with a digit that is no date: with the header values printed under their labels, the one word of the
page that stands before a word with a digit is the name of the supplier, and a layout with that name as its PO label would read
without a problem. The version and the supplier are proposed behind a listed label only. The date is the one date above the title
row, or of several the one behind a label of DATE_LABELS, and never one behind a title of the delivery date. The currency is a
currency code that stands alone after its label, and the delivery terms begin with an Incoterm in capitals. A field for which the
lines show different labels before different values gets none of them; of the labels of one value, and of the lines of one label,
the first is taken.

Never proposed: the line of a supplier who stands without a label, the end text of the table, that every price is per one unit, and
the senders. Nothing on the page tells them apart from what merely looks like them; the row under the last order line of the second
made-up PO, for one, would end its table a page early.

The decimal mark is not judged here: detect gives the cells of the columns that hold numbers only, with their lines, but for a
column known as the line number, the item code, the description, the unit or the delivery date, and proposal looks in them for the
proof that the trial read asks for.
"""
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from itertools import combinations
from typing import NamedTuple

from aid.readers.pdf_text import TextRow, TextWord
from aid.readers.po_layout import NOT_FIELDS
from aid.readers.po_pdf import cells_under, title_spans
from aid.schema import normalize_currency, normalize_date, normalize_number

# --- the word lists (Dutch, English and German), for the owner to extend ---

# The words a label of the PO number is made of: "Inkooporder", "Bestelnummer", "Purchase order". A text before a number is proposed
# as the PO label only when each of its words stands here or in NUMBER_WORDS and at least one of them here.
PO_WORDS = ("inkooporder", "inkoopordernummer", "inkoopordernr", "bestelnummer", "bestelnr", "bestelling", "bestelbon", "ordernummer",
            "ordernr", "order", "po", "po-nummer", "purchase", "bestellnummer", "bestellnr", "bestell-nr", "bestellung")
# The words that may stand beside a word of PO_WORDS in that label: "Inkooporder nr.", "Ons ordernummer", "Purchase order no.".
NUMBER_WORDS = ("nr", "nummer", "no", "number", "ons", "onze", "our", "unsere")
# The labels of the date of the PO. They are looked at only when the lines above the title row hold more than one date: the date
# behind one of these is then proposed, and no date when none of them stands there.
DATE_LABELS = ("datum", "besteldatum", "orderdatum", "inkoopdatum", "date", "order date", "po date", "bestelldatum")
# The labels of the version number of the PO, which is proposed only behind one of these and before a whole number.
VERSION_LABELS = ("versie", "versienummer", "versienr", "revisie", "revisienummer", "rev", "version", "revision")
# The labels of the supplier, who is proposed only behind one of these.
SUPPLIER_LABELS = ("leverancier", "crediteur", "supplier", "vendor", "lieferant", "kreditor")
# The Incoterms, in capitals as a PDF prints them: a label before one of these is the label of the delivery terms.
INCOTERMS = ("EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP", "FAS", "FOB", "CFR", "CIF", "DAT", "DAF", "DES", "DEQ", "DDU")
# The titles of the columns of the lines table, under the meaning of the column (a Meaning of aid/readers/po_layout.py; "skip" is
# a column that is not read, such as a line amount). A title of several words is listed as a whole, with one space between its words.
TITLES = {
    "line_id": ("regel", "regelnr", "regelnummer", "rgl", "pos", "positie", "posnr", "position", "line"),
    "item_code": ("artikel", "artikelcode", "artikelnr", "artikelnummer", "artikel-nr", "art.nr", "art nr", "itemcode", "item code",
                  "item number", "part number", "article"),
    "description": ("omschrijving", "artikelomschrijving", "artikel omschrijving", "benaming", "description", "bezeichnung",
                    "beschreibung"),
    "quantity": ("aantal", "hoeveelheid", "besteld", "aantal besteld", "qty", "quantity", "menge", "anzahl"),
    "unit": ("eenheid", "eenh", "eh", "unit", "uom", "einheit", "me"),
    "unit_price": ("prijs", "stuksprijs", "eenheidsprijs", "inkoopprijs", "prijs per", "prijs per eenheid", "prijs per stuk", "price",
                   "unit price", "preis", "einzelpreis", "stückpreis"),
    "price_basis": ("per", "pe", "prijseenheid", "price unit", "preiseinheit"),
    "discount_pct": ("korting", "korting %", "kortingspercentage", "krt", "discount", "discount %", "rabatt", "rabatt %"),
    "net_price": ("netto", "nettoprijs", "netto prijs", "net price", "nettopreis"),
    "delivery_date": ("leverdatum", "levering", "leverweek", "afleverdatum", "gewenste leverdatum", "delivery", "delivery date",
                      "liefertermin", "lieferdatum"),
    "skip": ("bedrag", "regelbedrag", "totaal", "totaalbedrag", "regeltotaal", "btw", "btw %", "amount", "total", "line total", "vat",
             "betrag", "gesamt", "gesamtpreis", "mwst"),
}
# The units of measure by which a column under a title that no list knows is known as the unit: every cell of it is one of these.
UNITS = ("st", "stk", "stuk", "stuks", "pc", "pcs", "piece", "pieces", "ea", "each", "stück", "m", "mtr", "meter", "m2", "m3", "cm",
         "mm", "kg", "g", "gr", "l", "ltr", "liter", "ml", "doos", "ds", "pak", "set", "rol", "paar", "zak", "fles", "blik", "box",
         "pack", "pair", "roll", "karton")

LABEL_GAP = 15.0             # points: a gap wider than this between two words of a line parts two labels with their values
_TITLE_OF = {title: meaning for meaning, titles in TITLES.items() for title in titles}
# The field of a column that is known by its cells, by what those cells all are (_kind).
_BY_CELLS = {"date": "delivery_date", "unit": "unit", "code": "item_code", "text": "description"}
_NO_NUMBERS = ("line_id", "item_code", "description", "unit", "delivery_date")     # the fields whose cells prove no decimal mark
_ANY_YEAR = date(2026, 1, 1)         # a week without a year is a date here whatever its year; this year has all 53 weeks
_CENT = Decimal("0.01")              # a line amount is the product of its factors to the cent


class Detected(NamedTuple):
    """What the first page of a sample shows of its layout (detect)."""
    labels: dict[str, tuple[str, int]]           # the label found for a header field: (its text, its line of page 1)
    title_line: int | None                       # the line of page 1 that holds the title row; None when none was found
    title_row: list[str]                         # the words of the title row
    columns: list[str | None]                    # the meaning of each of those words; None for a word left open
    numbers: list[tuple[int, str]]               # (line of page 1, text) of each cell of a column of numbers


def detect(page: list[TextRow], most_words: int) -> Detected:
    """What the lines of the first page of a sample show of its layout (see the module docstring); `most_words` is the most words
    of a title row that the form takes."""
    at = _title_at(page, most_words)
    if at is None:
        return Detected(_labels(page), None, [], [], [])
    columns, numbers = _columns(page[at].words, page[at + 1:])
    return Detected(_labels(page[:at]), page[at].line.line, [word.text for word in page[at].words], columns, numbers)


def _fold(text: str) -> str:
    """A word or a title as the lists hold it: without regard to case, each word without a full stop or colon at its end."""
    return " ".join(word.casefold().rstrip(".:") for word in text.split())


def _has_digit(text: str) -> bool:
    return any(char.isdigit() for char in text)


# --- the labels above the title row ---

def _labels(rows: list[TextRow]) -> dict[str, tuple[str, int]]:
    """The label of each header field that these lines show, with its first line (see the module docstring). A field for which
    the lines show different labels before different values gets none of them."""
    places: dict[str, list[tuple[str, int, str]]] = {}                 # for each field (a label, its line, the first word after it)
    for label, value, line in _pairs(rows):
        words, first = set(_fold(label).split()), value[0].rstrip(",;:.")
        dated = normalize_date(value[0], None) is not None
        fits = {"po_label": bool(words & set(PO_WORDS)) and words <= {*PO_WORDS, *NUMBER_WORDS} and _has_digit(first) and not dated,
                "date_label": dated and _TITLE_OF.get(_fold(label)) != "delivery_date",
                "supplier_label": _fold(label) in SUPPLIER_LABELS,
                "version_label": _fold(label) in VERSION_LABELS and first.isdecimal(),
                "currency_label": len(value) == 1 and normalize_currency(value[0]) is not None,
                "incoterm_label": value[0] in INCOTERMS}
        for field in (field for field, fit in fits.items() if fit):
            places.setdefault(field, []).append((label, line, first.casefold()))
    known = [place for place in places.get("date_label", []) if _fold(place[0]) in DATE_LABELS]
    if known:
        places["date_label"] = known
    return {field: found[0][:2] for field, found in places.items()
            if len({_fold(label) for label, _, _ in found}) == 1 or len({value for _, _, value in found}) == 1}


def _pairs(rows: list[TextRow]) -> Iterator[tuple[str, list[str], int]]:
    """(a label, the words of the value after it, their line) for each part of each line that holds a label before a value. A line
    is parted where a gap wider than LABEL_GAP stands between two words, since two labels may share a line. In a part the label ends
    with its first word that ends in a colon; without a colon it ends before the first later word that looks like a value: one with
    a digit, a currency code or an Incoterm. A part without a label, with a digit in its label or with nothing after it gives nothing."""
    for row in rows:
        parts = [[row.words[0]]]
        for before, after in zip(row.words, row.words[1:]):
            if after.x0 - before.x1 > LABEL_GAP:
                parts.append([])
            parts[-1].append(after)
        for part in parts:
            texts = [word.text for word in part]
            end = next((n + 1 for n, text in enumerate(texts) if text.endswith(":")), None)
            if end is None:
                valued = (n for n, text in enumerate(texts) if n and (_has_digit(text) or normalize_currency(text) or text in INCOTERMS))
                end = next(valued, 0)
            label = " ".join(texts[:end]).rstrip(": ")
            if label and not _has_digit(label) and texts[end:]:
                yield label, texts[end:], row.line.line


# --- the title row ---

def _title_at(page: list[TextRow], most_words: int) -> int | None:
    """Where the title row stands among the lines of page 1, counted from 0; None when no line can be it (see the module docstring)."""
    lines = []
    for n, row in enumerate(page[:-1]):
        if _titles_like(row, most_words) and any(_has_digit(word.text) for word in page[n + 1].words):
            under = [_above(row.words, below) for below in page[n + 1:]]
            split = n > 0 and _titles_like(page[n - 1], most_words) and _above(page[n - 1].words, row) >= 2
            lines.append((sum(_fold(word.text) in _TITLE_OF for word in row.words) >= 2, under[0], sum(under), -n, split))
    if not lines:
        return None
    best = max(lines, key=lambda line: line[:4])
    if best[4] or best[1:4] != max(line[1:4] for line in lines):
        return None                                          # the lower half of a split title row, or two lines that can be it
    return -best[3]


def _titles_like(row: TextRow, most_words: int) -> bool:
    """Whether a line has the words of a title row: from two to `most_words`, none with a digit or a colon, two of them further
    apart than a space."""
    texts = [word.text for word in row.words]
    return (2 <= len(texts) <= most_words and not any(_has_digit(text) or ":" in text for text in texts)
            and not all(_one_title(left, right) for left, right in zip(row.words, row.words[1:])))


def _above(titles: list[TextWord], row: TextRow) -> int:
    """How many of the words of a title row stand above a word of the row."""
    return sum(any(min(word.x1, title.x1) > max(word.x0, title.x0) for word in row.words) for title in titles)


# --- the columns ---

def _columns(words: list[TextWord], below: list[TextRow]) -> tuple[list[str | None], list[tuple[int, str]]]:
    """(the meaning of each word of the title row, None for one left open; the cells of the columns of numbers with their lines),
    from the lines under the title row (see the module docstring)."""
    lines = [row for row in below if _above(words, row) * 2 >= len(words) and any(_has_digit(word.text) for word in row.words)]
    apart = [cells_under(row.words, title_spans(words, [None] * len(words)), True) for row in lines]
    same = [n > 0 and _one_title(words[n - 1], word) and sum(1 for cells in apart if cells[n - 1] and cells[n]) * 2 < len(lines)
            for n, word in enumerate(words)]
    spans = title_spans(words, ["same" if joined else None for joined in same])
    rows = [cells_under(row.words, spans, True) for row in lines]
    titles: list[str] = []
    for word, joined in zip(words, same):
        titles.append(f"{titles.pop()} {_fold(word.text)}" if joined else _fold(word.text))
    listed = [_TITLE_OF.get(title) for title in titles]
    meanings = [None if meaning not in NOT_FIELDS and listed.count(meaning) > 1 else meaning for meaning in listed]
    kinds = [_kind([cells[column] for cells in rows if cells[column]]) for column in range(len(titles))]
    for kind, field in _BY_CELLS.items():
        unknown = [column for column, meaning in enumerate(meanings) if meaning is None and kinds[column] == kind]
        if len(unknown) == 1 and field not in listed:
            meanings[unknown[0]] = field
    _by_product(rows, [column for column, kind in enumerate(kinds) if kind == "number"], meanings)
    numbers = [(row.line.line, cells[column]) for row, cells in zip(lines, rows) for column, kind in enumerate(kinds)
               if kind == "number" and meanings[column] not in _NO_NUMBERS and cells[column]]
    of_column = iter(meanings)
    return ["same" if joined else next(of_column) for joined in same], numbers


def _one_title(left: TextWord, right: TextWord) -> bool:
    """Whether two words of a title row stand one space apart: the gap between them is no wider than an average character of theirs."""
    return right.x0 - left.x1 <= (left.x1 - left.x0 + right.x1 - right.x0) / (len(left.text) + len(right.text))


def _kind(cells: list[str]) -> str | None:
    """What the filled cells of a column all are: "date", "unit", "number", "code" or "text"; None for no cell, for a mixture, and
    for cells that are each a letter and a number in one word and read as a week ("W48"), which may be weeks and may be codes."""
    if not cells:
        return None
    codes = all(len(cell.split()) == 1 and _has_digit(cell) and any(char.isalpha() for char in cell) for cell in cells)
    if all(normalize_date(cell, _ANY_YEAR) is not None for cell in cells):
        return None if codes else "date"
    if all(_fold(cell) in UNITS for cell in cells):
        return "unit"
    if all(_is_number(cell) for cell in cells):
        return "number"
    if codes:
        return "code"
    if all(any(char.isalpha() for char in cell) for cell in cells) and any(len(cell.split()) > 1 for cell in cells):
        return "text"
    return None


def _is_number(cell: str) -> bool:
    """Whether a cell is a number and nothing else: digits with their marks, with or without a sign or a currency sign around them."""
    return _has_digit(cell) and all(char.isdigit() or char in " .,'" for char in cell.strip("+-€$£ "))


def _by_product(rows: list[list[str]], numeric: list[int], meanings: list[str | None]) -> None:
    """Give the columns that arithmetic knows their meaning, in `meanings` (see the module docstring): the one choice of three
    columns of numbers of which the third is the product of the other two on every order line, under either reading of the marks."""
    products = {}
    for comma in (True, False):
        table = [{column: _decimal(cells[column], comma) for column in numeric} for cells in rows]
        for (a, b), c in ((factors, c) for factors in combinations(numeric, 2) for c in numeric if c not in factors):
            if len(table) >= 2 and all(None not in (row[a], row[b], row[c]) and abs(row[a] * row[b] - row[c]) <= _CENT for row in table):
                products.setdefault((a, b, c), table)
    if len(products) != 1:
        return
    ((a, b, amount), table), = products.items()
    if meanings[amount] not in (None, "skip"):
        return
    meanings[amount] = "skip"
    whole = [column for column in (a, b) if all(row[column].as_tuple().exponent >= 0 for row in table)]
    priced = [column for column in (a, b) if all(row[column].as_tuple().exponent <= -2 for row in table)]
    if len(whole) == len(priced) == 1:
        if meanings[whole[0]] is None and "quantity" not in meanings:
            meanings[whole[0]] = "quantity"
        if meanings[priced[0]] is None and "unit_price" not in meanings and meanings[whole[0]] == "quantity":
            meanings[priced[0]] = "unit_price"


def _decimal(cell: str, decimal_comma: bool) -> Decimal | None:
    """The number of a cell under a reading of its marks; None for an empty cell."""
    value = normalize_number(cell, decimal_comma=decimal_comma) if cell else None
    return None if value is None else Decimal(value)
