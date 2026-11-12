"""A try-out confirmation: a made-up order confirmation drawn from a purchase order that the PO store holds, with at most one change,
so that the owner can try the whole chain on an order of the test account of Exact without a supplier and without the AI service (the
plan's decision D15); and a try-out cart, a made-up cart page of that order for the pre-check in the browser (the last part
below). Nothing made up may reach a supplier, the approver or the live system: the review page offers a try-out only in
the test mode and while no authorization limit is set (the part "Proefdraaien" of aid/review/page.py), every try-out is read back
before it leaves the page (checked_confirmation, below), no change of a try-out is one that the routing (aid/routing.py) asks a
supplier about (no quantity and no missing line), and a case made from a try-out never sends a message (aid/tryout_mark.py).

The document. confirmation_pdf draws the try-out with Row, Text and pdf_bytes of the made-up set (aid/madeup/generate.py), in the
layout that the parser reads as table_nl (aid/readers/templates/table_nl.json), in Dutch as that layout is, whatever the page's
language:

    Proefleverancier (verzonnen document)        the first row: "try-out supplier (made-up document)", which names no supplier,
                                                 so that the case takes the PO's supplier, and by which the aid knows a try-out
    Orderbevestiging
    Uw inkooporder <number>, versie <n>          the PDF version it was made from, so that the version is chosen by reference
    Datum: <the day of making>
    Valuta: <currency>                           only when the PO states a currency
    Leveringsvoorwaarden: <Incoterm>             only when the PO states an Incoterm
    Pos Artikel Omschrijving Aantal Eenheid Prijs Per Leverdatum     the column titles, again at the top of each later page
    <a row for each line of the PO>              LINES_PER_PAGE rows to a page
    Dit is een proefdocument van de inkoopassistent. Het komt niet van een leverancier.
                                                 under the table: "this is a try-out document of the purchasing assistant; it does
                                                 not come from a supplier"

The moment of making, to the second, is the PDF's title, which the parser does not read, so that every try-out is a document of its
own: two try-outs of the same order and change differ in their bytes, and the scan opens a case for each instead of skipping the
second as a duplicate.

made_from gives the version a try-out of a PO is made from: its latest PDF version, which a confirmation is matched against while the
PO has one (PoStore.candidates of aid/po_store.py), also when a look from an export came after it. A PO known from an export only (a
look) has no PDF version to quote: its try-out names the PO alone ("Uw inkooporder <number>"), and its case is compared with the look.
A try-out is made from the order as the aid compares it: checked_confirmation lays the values that the marks of the list "In Exact
invoeren" say Exact holds over that version first, as the case of a try-out gets them (as_compared; as_entered of
aid/execution/by_hand.py, the marks of the try-outs' own changes included, after the list was brought in line with the PO as it
stands, as the pipeline does), and draws and reads back the try-out of that order. So
after the owner entered and ticked the change of an earlier try-out, a try-out with no change prints the entered value and its case
closes without a difference, and one with the price 1 percent up prints one percent above the entered price. The store is not
changed. While the list cannot be read, no try-out is made: the error is raised, and the page says that it did not work.
The parser reads the PO number, and with it the version quoted, of a number of six to eight digits (the template table_nl); the case
of another number finds its PO as the watched folder finds one (aid/sources/mailbox.py), and checked_confirmation makes such a
try-out only when that finds it.

The lines. Every line of the PO is printed as the store holds it, each cell only when the line states the value, so that, read back
with the parser, the try-out gives the PO's item codes, quantities, units, prices, price bases and delivery dates but for the change:
- Pos, the line id, when every line id of the PO is a whole number that fits the column and no two are the same, as the parser takes a
  row for a line by a whole number there; else the place of each line, 1, 2, 3, since the lines are matched by their item codes.
- Artikel, the item code as the store holds it. The column is as wide as the widest item code of the PO needs (its title at least),
  at the cost of the description: the two share SHARED points, so that an item code of up to about 100 points at the document's
  font size prints whole, such as twenty digits or KM-08-00417-ABC-2026. A wider one, such as twenty capitals, would leave the
  description less room than its title and is refused (TryoutRefused "item_code").
- Omschrijving, the description. A line is matched by its item code when the code holds a letter or a digit, as the matcher decides
  it (norm_code of aid/matching.py); its description is cut after its last whole word that fits its column, with " …" after it so
  that the cut shows (a first word too wide is cut inside, with "…"). Any other line, without an item code or with a placeholder
  such as "-", is matched by its description alone, so its description is printed whole.
- Aantal and Prijs in the Dutch form, a point between the thousands and a comma before the decimals, with the decimals as the store
  holds them ("1.234,50", "0,125"). The price is the one the comparison (aid/comparison.py) compares: the line's net price when it
  states one, else its unit price less its discount when it states one, rounded half up to the decimals of its unit price and at
  least the cent, else its unit price.
- Eenheid, "st" for a piece, as the made-up layouts print it, and any other unit as the store holds it.
- Per, the price basis as the store holds it.
- Leverdatum, a day as 16-11-2026, a week with its year as week 48-2026 (so that the parser needs no year of its own), and a range as
  its two days, 23-11-2026 - 27-11-2026.

The change (Change), made on the first line that states the value: price_small is the price 1 percent up and price_large 10 percent
up, each rounded half up to the decimals the PO prints the price with, at least two, and at least one step of those decimals up (12
becomes 12.12, 0.125 becomes 0.126, 12.50 becomes 12.63); date_later is the delivery date 3 days later and date_much_later 60 days
later, a week or a range moved as the range of its days.

What gives no try-out. confirmation_pdf refuses with TryoutRefused, before anything is drawn and in this order: a PO without lines
("no_lines"); a change of the price or of the delivery date of a PO none of whose lines states that value ("no_price", "no_date"); a
text the try-out would print, the header rows and every cell included (a description whole, before any cut), with a character that
the PDF's built-in font cannot print, or a control character ("character", checked first, also before an item code too wide;
Helvetica holds the characters of its encoding, cp1252, and prints any other as another letter, so that "FCA Poznań" would read back
as "FCA Poznan" and the routing would ask the supplier about it); an item code too wide ("item_code"); and a value that does not fit
the try-out ("value"): a quantity, a unit, a price, a price basis or a delivery date too wide for its column, the whole description of
a line not matched by its item code that is too wide for its column, and a line with neither an item code with a letter or a digit nor
a description, which nothing could match. Every refusal's text names the line and the column, or the header row, never a value.
checked_confirmation adds the three reasons of the read-back: "read_back", "not_found" and "version". The page shows each reason by
its sentence (aid/review/i18n.py, "tryout_reason:<reason>").

The read-back (checked_confirmation). Before a try-out is given to the assistant or offered for download, the aid reads it back as the
runner would read it with the parser (read_with_parser of aid/pipeline.py) and compares it with the PO version it was made from with
match_lines and compare (aid/matching.py, aid/comparison.py, read and never changed), in a folder of its own under
<cases_root>/tmp/work that goes again at once. It is refused, with nothing written, unless:
- the parser reads it, the only flag of the comparison is the chosen change on its line, with the new value (for "none", no flag at
  all), and the comparison leaves uncompared what it leaves uncompared when the PO is compared with itself (a PO line without a
  unit, for one, which a confirmation equal to the order leaves uncompared as well); else "read_back";
- the scan of the watched folder, given the try-out alone in a folder of its own, opens one case for its purchase order (by the PO
  number pattern of the settings, by a held number: an order number of at least four characters that the store holds, standing on
  the first page with one of that order's item codes, or by the lines; aid/sources/mailbox.py); else "not_found": a one-line order
  whose number has fewer than four characters and is not one the pattern reads, which neither a held number nor its lines find, or
  such an order whose lines a repeat order shares, for example. That scan opens its case in that folder and writes nothing into the
  store or the cases;
- the version the try-out quotes, or that the aid chooses without a quote, is the one it was made from; else "version": an order of
  another number with two versions of the same lines, for example, whose version the aid would not know.

file_name names the PDF "PROEF orderbevestiging <PO number> <time>.pdf", and known_pos gives the POs the store knows, for the page's
dropdown. Nothing here writes a file outside the folder of the read-back: the page offers the PDF as a download or puts it into the
watched folder (aid/sources/dropin.py), and from there on a try-out is an ordinary case: scanned, run and listed like any other, but
for the guards of aid/tryout_mark.py.

The try-out cart. For the pre-check in the browser the owner tries the chain with a try-out cart: a page of a try-out shop, made of a
PO as it stands in the store (PoStore.as_it_stands, which is what a cart is compared with), with at most one change (CartChange:
"none", or "price_small", the price of the first line that states one 1 percent up, as above). cart_html draws it in Dutch, as the
made-up cart pages are (aid/madeup/generate.py):

    <h1> Proefwinkel (verzonnen pagina)          the first heading, CART_HEADING: "try-out shop (made-up page)", by which the aid
                                                 knows a try-out cart. It names the try-out shop and no supplier: the case takes
                                                 the PO's supplier and never this heading, and has none when the PO names none
                                                 (aid/pipeline.py), as a PO known from an export without a supplier column does
    <h2> Winkelwagen
    Datum: <the day of making>
    Uw referentie: <PO number>                   the cart reader reads a number of six to eight digits there (aid/readers/cart.py), so
                                                 that the order is found by its number, never guessed (aid/sources/capture.py); a
                                                 number of another form, also one that goes on behind six to eight digits, is
                                                 not read, and the cart's items decide
    Valuta: <currency>                           only when the PO states a currency
    Artikelnummer Omschrijving Aantal Eenheid Prijs Per      the cart table, a row for each line of the PO
    Dit is een proefdocument van de inkoopassistent. Het komt niet van een leverancier.

Each cell is printed only when the line states the value, and every value of the order is printed as text (escaped), so that
nothing an order holds is markup of the page: the item code and the description as the store holds them, the quantity
and the price (the one the comparison compares, as above) with a comma before the decimals as they are and no mark between the
thousands, which the cart reader, given no number convention, reads back as it stands; "st" for a piece and any other unit as the
store holds it; and the price basis. A cart has no delivery dates. The day stands on the page and no moment: a try-out cart of the
same order and change made again on the same day is the same page, which the pre-check answers from the case it opened, as it
answers any page it has seen.

write_cart writes the page as <cases_root>/tryout/<PO number>_<change>.html, whole or not at all, and the capture server serves it
as /tryout/<name> while try-outs are offered (aid/capture_server.py; cart_link gives the address on this PC). Before the page
offers that link, checked_cart reads the page back with the cart reader, as checked_confirmation reads a try-out confirmation back,
in the same folder under <cases_root>/tmp/work, and refuses with TryoutRefused unless:
- cart_html makes the page ("no_lines", "no_price"), the cart reader reads it, the aid knows it as a try-out by its heading
  (is_tryout of aid/tryout_mark.py), and, compared as a cart with the PO as the store holds it, which is what the pre-check will
  compare it with, its only flag is the chosen change and it leaves uncompared what the PO compared with itself leaves uncompared;
  else "read_back";
- the pre-check, asked the same rule with the same arguments at the moment of making (cart_order of aid/sources/capture.py, with
  the cases of the settings and that moment), would find the order the cart is made of, and not only guess it, nor find another
  order in its place: by the number on the page, or, for a number that the cart reader does not read, as the one order filed that
  day that holds exactly the cart's items and is not sent; else "not_found". So a cart of such an order is given on the day of its
  export only.
A capture case made from a try-out cart is a try-out for every guard of aid/tryout_mark.py. With "Eerste zaken van een leverancier"
above 0 it comes to the buyer until that many cases of its order's supplier were closed (a try-out is no supplier's case), and
always when its order names no supplier.
"""
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from pathlib import Path
from typing import Literal, get_args
from urllib.parse import quote

from reportlab.pdfbase.pdfmetrics import stringWidth

from aid.case import TEMPORARY, WORK, load_case, work_copy_folder
from aid.comparison import Findings, compare, date_span
from aid.decider import NoDecider
from aid.execution.by_hand import FOLDER as LIST_FOLDER
from aid.execution.by_hand import as_entered, filed_of, reconcile
from aid.madeup.generate import (BODY, BOLD, CONFIRMATION_WORDS, CONTENT_WIDTH, GAP, LINES_PER_PAGE, REGULAR, UNIT_WORDS, Column, Row, Text,
                                 day_text, pdf_bytes)
from aid.matching import match_lines, norm_code
from aid.pipeline import read_with_parser
from aid.po_store import PoStore, PoVersion
from aid.readers.cart import read_cart_html
from aid.readers.llm_client import ReaderError
from aid.schema import DocLine, Document
from aid.settings import Settings
from aid.sources.capture import GUESSES, cart_order
from aid.sources.mailbox import scan_folder
from aid.tryout_mark import CART_HEADING, FIRST_ROW, is_tryout
from aid.versioning import select_version

Change = Literal["none", "price_small", "price_large", "date_later", "date_much_later"]
CHANGES: tuple[str, ...] = get_args(Change)      # in the order the page offers them
CartChange = Literal["none", "price_small"]      # the changes of a try-out cart, each one of CHANGES
CART_CHANGES: tuple[str, ...] = get_args(CartChange)
CART_FOLDER = "tryout"                           # under cases_root: the try-out carts, which the capture server serves as /tryout/<name>
CART_SHOP = "http://127.0.0.1:7870"              # the capture server on this PC, as the launcher starts it (aid/start.py, ADDRESSES)
CART_TITLES = ("Artikelnummer", "Omschrijving", "Aantal", "Eenheid", "Prijs", "Per")      # the column titles of a try-out cart
RAISED = {"price_small": 1, "price_large": 10}   # percent up
LATER = {"date_later": 3, "date_much_later": 60}  # days later
# why a PO gives no try-out (TryoutRefused), in the order they are found
REASONS = ("no_lines", "no_price", "no_date", "character", "item_code", "value", "read_back", "not_found", "version")
SUPPLIER_ROW = FIRST_ROW
NOTE_ROW = "Dit is een proefdocument van de inkoopassistent. Het komt niet van een leverancier."
TITLE = "made-up try-out order confirmation"      # the PDF's title, followed by the moment of making
ROOM = 6                                         # points a cell leaves free: pdfplumber joins characters less than 3 points apart
ENCODING = "cp1252"                              # the encoding of the built-in font: the characters it prints as they are
# The widths of the columns in points. Artikel and Omschrijving share SHARED (see the module docstring); Leverdatum takes the rest
# of the line, 106.28 points: room for a range of two days.
POS, QUANTITY, UNIT, PRICE, BASIS, SHARED = 27, 52, 52, 60, 28, 170
_WHOLE = re.compile(r"[0-9]+")
_FILED = re.compile(r"[ve](?:0|[1-9][0-9]*)")   # the folder of a PDF version or of a look at a PO (aid/po_store.py)
_PRICE, _DATE = "price", "delivery_date"         # the fields of the comparison's flag of a change


class TryoutRefused(ValueError):
    """A PO that gives no try-out, with its reason, one of REASONS, which the page shows by its sentence. The text names no value."""

    def __init__(self, reason: str, text: str):
        super().__init__(text)
        self.reason = reason


def confirmation_pdf(po: PoVersion, change: Change, moment: datetime) -> bytes:
    """The try-out of the PO with the change, made at `moment` (its day the date of the try-out, the moment to the second its title),
    as a PDF (see the module docstring). Raises TryoutRefused for a PO that gives no try-out, by the reasons of the module docstring
    but the two of the read-back, and ValueError for a change that is none of CHANGES."""
    lines = po.document.lines
    if not lines:
        raise TryoutRefused("no_lines", "the PO has no order lines")
    prices, days, _ = _changed(lines, change)
    header = _header(po, moment)
    texts = [_texts(place, line, price, day) for place, line, price, day in zip(_places(lines), lines, prices, days)]
    for row in header:
        _printable(row.texts[0].text, f"the header row of the {row.ref}")
    for place, row_texts in zip(_places(lines), texts):
        for title, field in _TITLES:
            _printable(row_texts[field], f"line {place}, column {title}")
    columns = _layout(lines)
    body = [_row(place, line, row_texts, columns) for place, line, row_texts in zip(_places(lines), lines, texts)]
    titles = Row([Text(column.x, column.title, BOLD) for column in columns], gap=GAP, rule=True)
    chunks = [body[n:n + LINES_PER_PAGE] for n in range(0, len(body), LINES_PER_PAGE)]
    pages = [(header if n == 0 else []) + [titles] + chunk for n, chunk in enumerate(chunks)]
    pages[-1].append(Row([Text(0, NOTE_ROW)], gap=GAP))
    return pdf_bytes(pages, f"{TITLE} {moment:%Y-%m-%d %H:%M:%S}")


def checked_confirmation(po: PoVersion, change: Change, moment: datetime, settings: Settings, store: PoStore) -> bytes:
    """The try-out of confirmation_pdf, made from the version as a case made from a try-out is compared with it (as_compared) and read
    back before it leaves the page (see the module docstring): returned only when the parser reads it as that order with the change
    alone and the scan, with these settings and this store, finds its PO and the version it was made from. Raises TryoutRefused with
    the reason otherwise, and the OSError of a list "In Exact invoeren" that cannot be read; nothing is written but in the folder
    of the read-back under <cases_root>/tmp/work, which goes again at once."""
    po = as_compared(po, settings, store, moment)
    pdf = confirmation_pdf(po, change, moment)
    expected = _changed(po.document.lines, change)[2]
    name = file_name(po, moment)
    with work_copy_folder(Path(settings.cases_root) / TEMPORARY / WORK, "aid_tryout_") as folder:
        path = folder / name
        path.write_bytes(pdf)
        try:
            doc = read_with_parser(path, "confirmation")
        except ReaderError:
            raise TryoutRefused("read_back", "the parser does not read the try-out") from None
        if not _reads_as(doc, po, expected):
            raise TryoutRefused("read_back", "read back, the try-out differs from the PO in more than the change")
        if not _found(pdf, name, po, moment, settings, store, folder):
            raise TryoutRefused("not_found", "the scan would not find the PO of the try-out")
        if not _version_chosen(doc, po, store):
            raise TryoutRefused("version", "the aid would not choose the version of the PO the try-out was made from")
    return pdf


def as_compared(po: PoVersion, settings: Settings, store: PoStore, now: datetime) -> PoVersion:
    """The version of the store as a case made from a try-out is compared with it (aid/pipeline.py): the list "In Exact invoeren"
    brought in line with the PO as it stands first, at now (reconcile of aid/execution/by_hand.py, as the pipeline does before it
    compares: a tick that a newer print contradicts counts no more), then the values that the marks of the list say Exact holds
    laid over the version, those of the try-outs' own changes too (as_entered); the version itself when no mark counts for it, or
    when the store cannot say when it was filed. An OSError of the list's files is raised."""
    filed = filed_of(store, po)
    if filed is None:
        return po
    prepared = Path(settings.cases_root) / LIST_FOLDER
    reconcile(prepared, store, Path(settings.cases_root), now, po.po_number)
    return as_entered(po, prepared, filed, settings.authorization_limit, tryout=True)[0]


def file_name(po: PoVersion, now: datetime) -> str:
    """The name of the try-out's PDF: "PROEF orderbevestiging <PO number> <time>.pdf", the time as 20261112-093005."""
    return f"PROEF orderbevestiging {po.po_number} {now:%Y%m%d-%H%M%S}.pdf"


def known_pos(store: PoStore, limit: int = 50) -> list[tuple[str, str]]:
    """The POs the store knows, as the page's dropdown offers them: (label, PO number) of each, the one filed last first, by the newest
    po.json of its PDF versions and its looks, at most `limit` of them. The label is the PO number, with the supplier of the PO as it
    stands (PoStore.as_it_stands) in brackets when it names one. A folder that holds no PO, or whose files cannot be read, is passed
    over."""
    if not store.root.is_dir():
        return []
    found = []
    for folder in store.root.iterdir():
        try:
            standing = store.as_it_stands(folder.name)
            filed = [path.stat().st_mtime_ns for path in folder.glob("*/po.json") if _FILED.fullmatch(path.parent.name)]
        except (OSError, ValueError):            # taken away meanwhile, or a po.json that cannot be read
            continue
        if standing is not None and filed:
            found.append((max(filed), folder.name, standing))
    found.sort(key=lambda item: (-item[0], item[1]))
    return [(f"{number} ({standing.supplier})" if standing.supplier else number, number) for _, number, standing in found[:limit]]


def made_from(store: PoStore, po_number: str) -> PoVersion | None:
    """The version of the PO that a try-out is made from (see the module docstring): its latest PDF version, else, for a PO known from
    an export only, the PO as it stands, its latest look; None for a PO the store does not know."""
    versions = store.versions(po_number)
    return versions[-1] if versions else store.as_it_stands(po_number)


# --- the try-out cart ---

def cart_html(po: PoVersion, change: CartChange, today: date) -> str:
    """The try-out cart of the PO with the change, made on the day `today`, as a page of the try-out shop (see the module
    docstring). Raises TryoutRefused for a PO without lines ("no_lines") and, for "price_small", for a PO none of whose lines states
    a price ("no_price"), and ValueError for a change that is none of CART_CHANGES."""
    if change not in CART_CHANGES:
        raise ValueError(f"{change!r} is no change of a try-out cart")
    lines = po.document.lines
    if not lines:
        raise TryoutRefused("no_lines", "the PO has no order lines")
    prices = _changed(lines, change)[0]
    head = "".join(f"<th>{title}</th>" for title in CART_TITLES)
    body = "\n".join("      <tr>" + "".join(f"<td>{escape(text)}</td>" for text in _cart_cells(line, price)) + "</tr>"
                     for line, price in zip(lines, prices))
    currency = po.document.currency.value
    stated = f"  <p>{CONFIRMATION_WORDS['nl']['currency']} {escape(currency)}</p>\n" if currency is not None else ""
    return f"""<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<title>{CART_HEADING}</title>
<style>
  body {{ font-family: Helvetica, Arial, sans-serif; margin: 2em; }}
  table.cart {{ border-collapse: collapse; }}
  table.cart th, table.cart td {{ border-bottom: 1px solid #ccc; padding: 0.4em 1em; text-align: left; }}
</style>
</head>
<body>
  <h1>{CART_HEADING}</h1>
  <h2>Winkelwagen</h2>
  <p>Datum: {day_text(today, 'nl')}</p>
  <p>Uw referentie: {escape(po.po_number)}</p>
{stated}  <table class="cart">
    <thead>
      <tr>{head}</tr>
    </thead>
    <tbody>
{body}
    </tbody>
  </table>
  <p>{NOTE_ROW}</p>
</body>
</html>
"""


def write_cart(cases_root: Path, po: PoVersion, change: CartChange, today: date) -> str:
    """Write the try-out cart of cart_html as <cases_root>/tryout/<PO number>_<change>.html, whole or not at all, and return the
    name of the file, by which the capture server serves it (cart_link gives its address). A cart made again of the same order with
    the same change takes the place of the earlier one. Raises what cart_html raises, and writes nothing then."""
    page = cart_html(po, change, today)
    name = f"{po.po_number}_{change}.html"
    folder = Path(cases_root) / CART_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f".{name}.tmp"
    temporary.write_bytes(page.encode("utf-8"))
    os.replace(temporary, folder / name)
    return name


def checked_cart(po: PoVersion, change: CartChange, now: datetime, settings: Settings, store: PoStore) -> str:
    """The try-out cart of cart_html, made at `now`, whose day stands on the page, from the order as a case made from a try-out is
    compared with it (as_compared: a change of a try-out that was entered and ticked counts in the next try-out's case, so the next
    cart starts from it), and read back before the page offers its link (see the module docstring): returned only when the cart
    reader reads it as the PO as it stands, compared so, with the change alone, the aid knows it as a try-out, and the pre-check,
    asked at `now` with this store and the cases of the settings, would find its PO and not only guess it, and no other order in
    its place. Raises TryoutRefused with the reason otherwise, and the OSError of a list "In Exact invoeren" that cannot be read;
    nothing is written but in the folder of the read-back under <cases_root>/tmp/work, which goes again at once."""
    po = as_compared(po, settings, store, now)
    page = cart_html(po, change, now.date())
    expected = _changed(po.document.lines, change)[2]
    with work_copy_folder(Path(settings.cases_root) / TEMPORARY / WORK, "aid_tryout_") as folder:
        path = folder / "cart.html"
        path.write_bytes(page.encode("utf-8"))
        try:
            cart = read_cart_html(path)
        except ReaderError:
            raise TryoutRefused("read_back", "the cart reader does not read the try-out cart") from None
        known = is_tryout(path)
    standing = store.as_it_stands(po.po_number)
    standing = as_compared(standing, settings, store, now) if standing is not None else None
    if not known:
        raise TryoutRefused("read_back", "the aid would not know the try-out cart as a try-out")
    if standing is not None and not _reads_as(cart, standing, expected, "cart"):
        raise TryoutRefused("read_back", "read back, the try-out cart differs from the PO as the store holds it in more than the change")
    number, how = cart_order(cart, store, Path(settings.cases_root), now)
    if standing is None or number != po.po_number or how in GUESSES:
        raise TryoutRefused("not_found", "the pre-check would not find the PO of the try-out cart")
    return page


def cart_link(name: str) -> str:
    """The address of the try-out cart `name` (as write_cart returns it) on the capture server of this PC."""
    return f"{CART_SHOP}/{CART_FOLDER}/{quote(name)}"


def _cart_cells(line: DocLine, price: str | None) -> list[str]:
    """What the row of one line prints under each of CART_TITLES; `price` is the price of the try-out, with the change."""
    unit = line.unit.value
    return [line.item_code.value or "", line.description.value or "", _plain(line.quantity.value),
            UNIT_WORDS["nl"].get(unit, unit) if unit is not None else "", _plain(price), line.price_basis.value or ""]


def _plain(number: str | None) -> str:
    """A number in normal form as a try-out cart prints it: a comma before its decimals as they are and no mark between the
    thousands, so that the cart reader, which is given no number convention, reads it back as it stands; "" for no number."""
    return number.replace(".", ",") if number is not None else ""


# --- the rows ---

_TITLES = (("Pos", "line_id"), ("Artikel", "item_code"), ("Omschrijving", "description"), ("Aantal", "quantity"), ("Eenheid", "unit"),
           ("Prijs", "unit_price"), ("Per", "price_basis"), ("Leverdatum", "delivery_date"))


def _header(po: PoVersion, moment: datetime) -> list[Row]:
    """The rows above the table: made up, the title, the PO and the version quoted (none for a look from an export), the date, and the
    currency and the Incoterm when the PO states them. Each row's ref names it in an error."""
    words, doc = CONFIRMATION_WORDS["nl"], po.document
    reference = f"{words['po']} {po.po_number}" + ("" if doc.reader.startswith("export:") else f", {words['version']} {po.version}")
    rows = [Row([Text(0, SUPPLIER_ROW, BOLD, 14)], ref="made-up supplier"), Row([Text(0, words["title"], BOLD, 12)], ref="title"),
            Row([Text(0, reference)], ref="purchase order"), Row([Text(0, f"{words['date']} {day_text(moment.date(), 'nl')}")], ref="date")]
    if doc.currency.value is not None:
        rows.append(Row([Text(0, f"{words['currency']} {doc.currency.value}")], ref="currency"))
    if doc.incoterm.value is not None:
        rows.append(Row([Text(0, f"{words['incoterm']} {doc.incoterm.value}")], ref="Incoterm"))
    return rows


def _places(lines: list[DocLine]) -> list[str]:
    """What the column Pos prints for each line: its line id when every line id is a whole number that fits the column and no two are
    the same; else 1, 2, 3 in the order of the lines."""
    ids = [line.line_id for line in lines]
    if len(set(ids)) == len(ids) and all(_WHOLE.fullmatch(text) and _fits(text, POS) for text in ids):
        return ids
    return [str(n) for n in range(1, len(lines) + 1)]


def _texts(place: str, line: DocLine, price: str | None, day: str | None) -> dict[str, str]:
    """What the row of one line prints in each column, the description whole; `price` and `day` are those of the try-out, with the
    change."""
    code, unit = line.item_code.value, line.unit.value
    return {"line_id": place, "item_code": code or "", "description": line.description.value or "",
            "quantity": _dutch(line.quantity.value) if line.quantity.value is not None else "",
            "unit": UNIT_WORDS["nl"].get(unit, unit) if unit is not None else "", "unit_price": _dutch(price) if price is not None else "",
            "price_basis": line.price_basis.value or "", "delivery_date": _delivery(day) if day is not None else ""}


def _layout(lines: list[DocLine]) -> list[Column]:
    """The columns of the PO's table: Artikel as wide as its widest item code needs, its title at least, and Omschrijving the rest of
    SHARED (see the module docstring). Refuses an item code that leaves the description less room than its title."""
    widest = max(((_width(line.item_code.value), n) for n, line in enumerate(lines, 1) if line.item_code.value), default=(0.0, 0))
    code = max(_width("Artikel", BOLD), widest[0]) + ROOM
    if SHARED - code < _width("Omschrijving", BOLD) + ROOM:
        raise TryoutRefused("item_code", f"line {_places(lines)[widest[1] - 1]}: the item code is too wide for its column")
    return _columns(("Pos", "line_id", POS), ("Artikel", "item_code", code), ("Omschrijving", "description", SHARED - code),
                    ("Aantal", "quantity", QUANTITY), ("Eenheid", "unit", UNIT), ("Prijs", "unit_price", PRICE), ("Per", "price_basis", BASIS))


def _columns(*specs: tuple[str, str, float]) -> list[Column]:
    """The columns of the table, side by side from the left margin, the last one to the right edge of the text."""
    columns, x = [], 0.0
    for title, field, width in specs:
        columns.append(Column(title, field, x, width))
        x += width
    return columns + [Column("Leverdatum", "delivery_date", x, CONTENT_WIDTH - x)]


def _row(place: str, line: DocLine, texts: dict[str, str], columns: list[Column]) -> Row:
    """The row of one line (see the module docstring): the description cut when the line is matched by its item code; a value that
    does not fit its column, and a line that nothing could match, are refused."""
    coded = norm_code(line.item_code.value) is not None
    if not coded and not line.description.value:
        raise TryoutRefused("value", f"line {place}: neither an item code nor a description, so nothing would match it")
    cells = []
    for column in columns:
        text = texts[column.field]
        if column.field == "description" and coded:
            text = _cut(text, column.width)
        if not _fits(text, column.width):
            raise TryoutRefused("value", f"line {place}: the column {column.title} is too narrow for the value of the line")
        if text:
            cells.append(Text(column.x, text))
    return Row(cells)


def _printable(text: str, where: str) -> None:
    """Refuse a text that holds a character the built-in font cannot print: one outside its encoding, or a control character."""
    try:
        text.encode(ENCODING)
    except UnicodeEncodeError:
        raise TryoutRefused("character", f"{where}: a character that the try-out's font cannot print") from None
    if any(ord(char) < 32 or 127 <= ord(char) < 160 for char in text):
        raise TryoutRefused("character", f"{where}: a control character, which the try-out's font cannot print")


def _width(text: str, font: str = REGULAR) -> float:
    return stringWidth(text, font, BODY)


def _fits(text: str, width: float) -> bool:
    return _width(text) <= width - ROOM


def _cut(text: str, width: float) -> str:
    """The text when it fits the width; else its most first words that fit with " …" after them, so that the cut shows and never
    leaves part of a word that reads as another one ("M10 x 4" of "M10 x 40"); when not even the first word fits, as many of its
    first characters as fit with "…" after them."""
    if _fits(text, width):
        return text
    words = text.split()
    for count in range(len(words) - 1, 0, -1):
        if _fits(cut := " ".join(words[:count]) + " …", width):
            return cut
    cut = words[0] if words else ""
    while cut and not _fits(cut + "…", width):
        cut = cut[:-1]
    return cut + "…"


# --- the values and the change ---

def _changed(lines: list[DocLine], change: Change) -> tuple[list[str | None], list[str | None], list[tuple]]:
    """The price and the delivery date that the try-out prints for each line, with the change made on the first line that states the
    value, and the flag the comparison is to find for it: [(field, the line's id, the new value)], [] for "none"."""
    prices = [_price(line) for line in lines]
    days = [line.delivery_date.value for line in lines]
    if change in RAISED:
        at = next((n for n, price in enumerate(prices) if price is not None), None)
        if at is None:
            raise TryoutRefused("no_price", "no line of the PO states a price")
        prices[at] = _raised(prices[at], RAISED[change])
        return prices, days, [(_PRICE, (lines[at].line_id,), Decimal(prices[at]))]
    if change in LATER:
        at = next((n for n, day in enumerate(days) if day is not None), None)
        if at is None:
            raise TryoutRefused("no_date", "no line of the PO states a delivery date")
        days[at] = _later(days[at], LATER[change])
        return prices, days, [(_DATE, (lines[at].line_id,), days[at])]
    if change != "none":
        raise ValueError(f"{change!r} is no change of a try-out")
    return prices, days, []


def _price(line: DocLine) -> str | None:
    """The price a try-out prints for a line, the one the comparison compares (see the module docstring); None for no price."""
    if line.net_price.value is not None:
        return line.net_price.value
    if line.unit_price.value is None:
        return None
    if line.discount_pct.value is None:
        return line.unit_price.value
    places = max(2, _decimals(line.unit_price.value))
    net = Decimal(line.unit_price.value) * (1 - abs(Decimal(line.discount_pct.value)) / 100)
    return format(net.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), "f")


def _raised(price: str, percent: int) -> str:
    """The price `percent` percent up, rounded half up to the decimals it is printed with, at least two, and at least one step of
    those decimals up."""
    value, step = Decimal(price), Decimal(1).scaleb(-max(2, _decimals(price)))
    raised = (value * (100 + percent) / 100).quantize(step, rounding=ROUND_HALF_UP)
    return format(max(raised, (value + step).quantize(step)), "f")


def _later(value: str, days: int) -> str:
    """A delivery date in normal form `days` days later: a day as a day, a week or a range as the range of its days moved."""
    first, last = (day + timedelta(days=days) for day in date_span(value))
    return first.isoformat() if first == last else f"{first.isoformat()}/{last.isoformat()}"


def _decimals(number: str) -> int:
    """The decimals of a number in normal form: 2 for "12.50", 0 for "12"."""
    return max(-Decimal(number).as_tuple().exponent, 0)


def _dutch(number: str) -> str:
    """A number in normal form as the layout prints it: a point between the thousands, a comma before its decimals as they are."""
    return f"{Decimal(number):,f}".translate(str.maketrans(",.", ".,"))


def _delivery(value: str) -> str:
    """A delivery date in normal form as the try-out prints it (see the module docstring)."""
    if "-W" in value:
        year, week = value.split("-W")
        return f"week {int(week)}-{year}"
    return " - ".join(day_text(part, "nl") for part in value.split("/"))


# --- the read-back ---

def _reads_as(doc: Document, po: PoVersion, expected: list[tuple], kind: Literal["confirmation", "cart"] = "confirmation") -> bool:
    """Whether the try-out as its reader read it is the PO with the change alone, compared as a document of `kind`: the comparison's
    flags are exactly the expected one (none for "none"), and it leaves uncompared what the PO compared with itself leaves
    uncompared. A PO the comparison refuses to compare (it raises ValueError for a match that puts one PO line id in two pairs, as
    two lines with the same id give) is not."""
    try:
        findings = compare(match_lines(doc, po.document, NoDecider()), doc, po.document, kind)
        alone = compare(match_lines(po.document, po.document, NoDecider()), po.document, po.document, kind)
    except ValueError:
        return False
    return [_flag(flag) for flag in findings.flags] == expected and _uncompared(findings) == _uncompared(alone)


def _flag(flag) -> tuple:
    """A flag as the expected change is written: (field, the PO line ids, the document's value), a price as a number."""
    value = flag.doc_value.value if flag.doc_value is not None else None
    if flag.field == _PRICE and value is not None:
        value = Decimal(value)
    return flag.field, tuple(flag.po_line_ids), value


def _uncompared(findings: Findings) -> Counter:
    """What the comparison leaves uncompared, by field, PO lines and reason: the document's own line ids differ from the PO's."""
    return Counter((item.field, tuple(item.po_line_ids), item.reason) for item in findings.uncompared)


def _found(pdf: bytes, name: str, po: PoVersion, moment: datetime, settings: Settings, store: PoStore, folder: Path) -> bool:
    """Whether the scan of the watched folder, given the try-out alone in a folder of its own, opens one case, for the PO."""
    inbox, cases = folder / "inbox", folder / "cases"
    inbox.mkdir()
    (inbox / name).write_bytes(pdf)
    scanned = scan_folder(inbox, store, cases, settings, moment)
    return len(scanned.new_cases) == 1 and load_case(cases, scanned.new_cases[0]).po_number == po.po_number


def _version_chosen(doc: Document, po: PoVersion, store: PoStore) -> bool:
    """Whether the aid chooses the version the try-out was made from among those the store gives a confirmation of the PO."""
    return select_version(doc, store.candidates(po.po_number, "confirmation"), NoDecider()).version == po.version
