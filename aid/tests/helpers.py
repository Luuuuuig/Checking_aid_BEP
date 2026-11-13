"""Test helpers shared by the tests of the checking aid. Each task adds the helpers its own tests name."""
import codecs
import csv
import os
import re
import socket
import threading
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal

from openpyxl import Workbook

from aid.madeup import generate
from aid.madeup.generate import (CART_COLUMNS, PO_COLUMNS, TRYOUT_PO_LAYOUT, TRYOUT_PO_NAME, MadeUpSpec, Row, Text, basis_words, cart_rows,
                                 cart_table, cell_text, confirmation_pages, day_text, row_positions)
from aid.readers.po_layout import PoLayout
from aid.readers.po_pdf import layout_first_page
from aid.readers.supplier_llm import Reading
from aid.schema import DocLine, DocValue, Document, SourceRef

SPEC_DIR = Path(__file__).resolve().parents[1] / "madeup" / "spec"
_LINE_FIELDS = [name for name, field in DocLine.model_fields.items() if field.annotation is DocValue]
# The column map of the made-up ERP export (aid/madeup/generate.py, export_csv), for PoStore.from_export: it names the column of
# each of the nine fields the export has after the field. (The three other export fields, the discount, the net price and the
# supplier, have no column in the made-up export, and a map may only name columns that the file holds.)
EXPORT_COLUMNS = {field: field for field in generate.EXPORT_COLUMNS}
# The fixed layout po_madeup written as a layout of the settings (the setting po_layout): the labels of its six header rows, and the
# words of its title row with the field of each. With it the layout reader reads the made-up PO PDFs as the template does.
MADEUP_LAYOUT = PoLayout(po_label="Inkooporder", version_label="Versie", date_label="Datum", supplier_label="Leverancier",
                         incoterm_label="Levering", currency_label="Valuta", title_row=[column.title for column in PO_COLUMNS],
                         columns=[column.field for column in PO_COLUMNS])
# The made-up PO in the second made-up layout, as the repository holds it, and the layout that reads it (aid/madeup/generate.py).
ANDERS_PO = Path(__file__).resolve().parents[1] / "madeup" / "tryout" / TRYOUT_PO_NAME
ANDERS_LAYOUT = PoLayout.model_validate(TRYOUT_PO_LAYOUT)
# That layout as the form of the block "Koppeling met Exact" gives it (aid/review/exact_settings.py): the seven fields above the title
# row, the end text, the number convention and the tick, with the title row that "Kopregel zoeken" finds, and the meaning chosen in
# the dropdown of each word of that row.
ANDERS_FORM = {"po_label": "Bestelnummer", "date_label": "Besteldatum", "supplier_label": "", "supplier_line": "3", "version_label": "",
               "currency_label": "Valuta", "incoterm_label": "Leveringsconditie", "table_end": "", "numbers": "comma", "per_piece": True,
               "title_row": ["Hoeveelheid", "Eenh.", "Artikelcode", "Benaming", "Gewenste", "levering", "Stuksprijs", "Bedrag"]}
ANDERS_MEANINGS = ["quantity", "unit", "item_code", "description", "delivery_date", "same", "unit_price", "skip"]
# Three made-up POs of the review of that block (aid/tests/fixtures/samples): one report of an ERP with line numbers, a price basis and
# a line amount, its numbers printed with a decimal comma (c01, quantities 1.000, 2.500, 1.000), with a decimal point (c02, 1,000,
# 2,500, 1,000) and with three decimals (c03, 10,000 and 2,500, a decimal comma); and the form that sets its layout.
SAMPLES = Path(__file__).resolve().parent / "fixtures" / "samples"
ERP_FORM = {"po_label": "Ordernummer", "date_label": "Orderdatum", "supplier_label": "Leverancier", "supplier_line": "", "version_label": "",
            "currency_label": "Valuta", "incoterm_label": "Leveringsvoorwaarden", "table_end": "", "numbers": "comma", "per_piece": False,
            "title_row": ["Regel", "Artikelcode", "Omschrijving", "Aantal", "Eenh.", "Prijs", "Per", "Bedrag", "Leverdatum"]}
ERP_MEANINGS = ["line_id", "item_code", "description", "quantity", "unit", "unit_price", "price_basis", "skip", "delivery_date"]
# The made-up Dutch export of the try-out folder (aid/madeup/generate.py, tryout_export_csv), and the form of part 2 of the block
# "Koppeling met Exact" that reads it (aid/review/exact_settings.py): the column title chosen in the dropdown of each field ("(none)",
# the choice "(staat niet in het bestand)", for a field the export has no column for), the decimal comma and the tick that the PO
# number stands on the first line of each PO only.
EXPORT_NL = Path(__file__).resolve().parents[1] / "madeup" / "tryout" / generate.TRYOUT_EXPORT
EXPORT_NL_FORM = {"po_number": "Inkooporder", "line_id": "(none)", "item_code": "Artikel", "description": "Omschrijving", "quantity": "Aantal",
                  "unit": "Eenheid", "unit_price": "Prijs", "price_basis": "Per", "discount_pct": "(none)", "net_price": "(none)",
                  "delivery_date": "Leverdatum", "supplier": "Leverancier", "numbers": "comma", "fill_down": True}
# Its row of column titles (row 3), the map that the form gives (the fields in the order of the form), and the lines of its first
# order as the trial read shows them: each value as the aid files it, with the cell's text in brackets when the two differ.
EXPORT_NL_TITLES = ["Inkooporder", "Leverancier", "Artikel", "Omschrijving", "Aantal", "Eenheid", "Prijs", "Per", "Leverdatum", "Bedrag"]
EXPORT_NL_COLUMNS = {"po_number": "Inkooporder", "item_code": "Artikel", "description": "Omschrijving", "quantity": "Aantal", "unit": "Eenheid",
                     "unit_price": "Prijs", "price_basis": "Per", "delivery_date": "Leverdatum", "supplier": "Leverancier"}
EXPORT_NL_FIRST_ORDER = [
    ["250301", "1", "KM-3101", "Slangklem 16-27 mm", "1000 (1.000)", "pc (st)", "12.5 (12,50)", "100", "2026-11-24 (24-11-2026)", "S21"],
    ["250301", "2", "KM-3102", "Hydrauliekslang 12 mm", "25", "m", "4.8 (4,80)", "1", "2026-11-24 (24-11-2026)", "S21"],
    ["250301", "3", "KM-3103", "Kogelkraan DN20", "4", "pc (st)", "38.75 (38,75)", "1", "2026-12-01 (01-12-2026)", "S21"]]
# The trial read shows the first and the last order of a sample with two orders or more (Task 39's review round, ruling M2), and what
# it says of EXPORT_nl.csv in both languages, a count of one in the singular (ruling M9).
EXPORT_NL_ORDERS = EXPORT_NL_FIRST_ORDER + [
    ["250302", "1", "KM-3201", "O-ring 30 x 3 mm", "200", "pc (st)", "0.125 (0,125)", "1", "2026-11-26 (26-11-2026)", "S22"],
    ["250302", "2", "KM-3202", "Tandwielpomp 11 cc", "2", "pc (st)", "1234.5 (1.234,50)", "1", "2026-11-26 (26-11-2026)", "S22"]]
EXPORT_NL_READ = ("Gelezen: 2 inkooporders met samen 5 regels; 1 rij zonder artikelcode overgeslagen. Hieronder de eerste en de laatste "
                  "inkooporder: vergelijk ze met Exact.")
EXPORT_NL_READ_EN = ("Read: 2 purchase orders with 5 lines in all; 1 row without an item code passed over. Below are the first and the last "
                     "purchase order: compare them with Exact.")


def write_sheet(path: Path, rows: list[list]) -> Path:
    """A made-up export at `path` with these rows: a .csv file cut at semicolons, every cell its text (None an empty cell), or for
    any other name the first sheet of a workbook, where a str is a text cell, an int or a float a number cell, a date or a datetime a
    date cell and None an empty cell."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        with open(path, "w", encoding="utf-8", newline="") as f:
            csv.writer(f, delimiter=";", lineterminator="\n").writerows([["" if cell is None else str(cell) for cell in row] for row in rows])
        return path
    workbook = Workbook()
    for row in rows:
        workbook.active.append(list(row))
    workbook.save(path)
    return path


def wrong_dimensions(path: Path) -> Path:
    """The workbook at `path` written again with a wrong dimension record in every sheet, <dimension ref="A1">, as a real export
    may hold one: read by that record, each sheet would give its first cell only."""
    with zipfile.ZipFile(path) as source:
        members = [(member, source.read(member.filename)) for member in source.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for member, data in members:
            sheet = member.filename.startswith("xl/worksheets/")
            target.writestr(member, re.sub(rb'<dimension ref="[^"]*"', b'<dimension ref="A1"', data) if sheet else data)
    return path


# The three samples of Task 39's review round that the sample of part 2 was cut wrongly for (ruling M5), byte for byte: a tab-separated
# export with decimal commas, one in UTF-16 with its byte order mark, and a comma-separated one with semicolons in a description.
TABS_TITLES = ["Inkooporder", "Artikel", "Omschrijving", "Aantal", "Prijs", "Bedrag"]
WIDE_TITLES = ["Inkooporder", "Artikel", "Omschrijving", "Aantal", "Eenheid", "Prijs", "Per", "Leverdatum", "Bedrag"]
COMMAS_TITLES = ["PO Number", "Item", "Description", "Qty", "Price"]


def write_cut_samples(folder: Path) -> tuple[Path, Path, Path]:
    """tabs_dutch_numbers.csv, unicode_text_nl.csv and comma_semicolons_in_text.csv in `folder`, as the review round held them."""
    folder.mkdir(parents=True, exist_ok=True)
    tabs, wide, commas = folder / "tabs_dutch_numbers.csv", folder / "unicode_text_nl.csv", folder / "comma_semicolons_in_text.csv"
    tabs.write_bytes("".join("\t".join(cells) + "\n" for cells in [
        TABS_TITLES, ["250301", "KM-3101", "Slangklem", "1000", "12,50", "125,00"],
        ["250301", "KM-3102", "Slang", "25", "4,80", "120,00"]]).encode("utf-8"))
    wide.write_bytes(codecs.BOM_UTF16_LE + "".join("\t".join(cells) + "\r\n" for cells in [
        WIDE_TITLES, ["250301", "KM-3101", "Slangklem 16-27 mm", "1.000", "st", "12,50", "100", "24-11-2026", "125,00"],
        ["250301", "KM-3102", "Hydrauliekslang 12 mm", "25", "m", "4,80", "1", "24-11-2026", "120,00"],
        ["250302", "KM-3201", "O-ring 30 x 3 mm", "200", "st", "0,13", "1", "26-11-2026", "26,00"]]).encode("utf-16-le"))
    commas.write_bytes(b"PO Number,Item,Description,Qty,Price\r\n250301,KM-3101,\"Hose clamp 16-27 mm, zinc\",1000,12.50\r\n"
                       b"250301,KM-3102,Hose 12 mm; EPDM; 10 bar,25,4.80\r\n250302,KM-3201,O-ring 30 x 3 mm,200,0.13\r\n")
    return tabs, wide, commas


# The samples of the check of Task 39's fix round, byte for byte. Two exports sorted by delivery date, with the PO number on every line
# and a total row at the end that the tick gives the PO number of the row above it (ruling M15; in fixtures/samples, the second of 32
# rows). And the samples that the cut of ruling M5 got wrong (ruling M16): EXPORT_nl.csv with a title line that holds two commas, with
# and without a footer that holds them too; a tab export with such a title, also in UTF-16 with its byte order mark; and a sample whose
# last column title holds two commas, so that the semicolons and the commas give as many rows of three cells.
SORTED_BY_DATE, SORTED_BY_DATE_LONG = SAMPLES / "sorted_by_date.csv", SAMPLES / "sorted_by_date_long.csv"
SORTED_BY_DATE_COLUMNS = {"po_number": "Inkooporder", "supplier": "Leverancier", "item_code": "Artikel", "description": "Omschrijving",
                          "quantity": "Aantal", "unit": "Eenheid", "unit_price": "Prijs", "delivery_date": "Leverdatum"}
TITLE_COMMAS = ["Inkooporder", "Artikel", "Omschrijving", "Aantal", "Prijs", "Bedrag"]
TIE_TITLES = ["Inkooporder", "Artikel", "Prijs", "Bedrag, excl. btw, in EUR"]


def write_title_samples(folder: Path) -> tuple[Path, Path, Path, Path, Path]:
    """export_nl_title_footer.csv, export_nl_title_only.csv, tabs_title_commas.csv, tabs_title_commas_utf16.csv and tie.csv in
    `folder`, as the check of the fix round held them."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = [folder / name for name in ("export_nl_title_footer.csv", "export_nl_title_only.csv", "tabs_title_commas.csv",
                                        "tabs_title_commas_utf16.csv", "tie.csv")]
    exported = EXPORT_NL.read_bytes()
    title_only = b"Openstaande inkooporderregels, alle leveranciers, week 47" + exported[exported.index(b";"):]
    paths[0].write_bytes(title_only + b"Afgedrukt op 10-11-2026, 09:30, pagina 1 van 1;;;;;;;;;\n")
    paths[1].write_bytes(title_only)
    tabs = "".join("\t".join(cells) + "\r\n" for cells in [
        ["Openstaande inkooporderregels, week 47, alle leveranciers"], [], TITLE_COMMAS,
        ["250301", "KM-3101", "Slangklem", "1000", "12,50", "12500,00"], ["250301", "KM-3102", "Slang", "25", "4,80", "120,00"],
        ["250302", "KM-3201", "O-ring", "200", "0,13", "26,00"]])
    paths[2].write_bytes(tabs.encode("cp1252"))
    paths[3].write_bytes(codecs.BOM_UTF16_LE + tabs.encode("utf-16-le"))
    paths[4].write_bytes(b"Inkooporder;Artikel;Prijs;Bedrag, excl. btw, in EUR\n250301;KM-3101;12,50;125,00\n250301;KM-3102;4,80;120,00\n")
    return tuple(paths)


def first_page(pdf: Path) -> str:
    """The text of the first page of a PDF as the watched folder holds it against a PO layout (layout_first_page of
    aid/readers/po_pdf.py): a line to each text line, with cells printed close together cut apart; "" for a scan."""
    return layout_first_page(pdf)


def document_from_spec(case_id: str, kind: Literal["confirmation", "cart"] = "confirmation", html: bool = False) -> Document:
    """The perfect reading of the made-up case `case_id` (aid/madeup/spec/<case_id>.json).

    Every value the document prints has its text as printed (the generator's formats and any text_override), its value in
    normal form (the spec's own value, which the spec stores in that form) and its source: the page and line of the row it
    stands on in the generator's layout, with the text of that row. A value the document does not print is DocValue().
    Nothing is uncertain.

    A confirmation case gives its confirmation, the scans (MU05, MU16) included; its rows are the ones references.csv names.
    A cart case gives the cart as its document shows it, each value citing its line counted from the top: the supplier on
    line 1, the date on the "Datum:" line, the PO number on the "Uw referentie:" line, the articles below the column titles
    and the currency on the "Prijzen in EUR" line. The document of a case of layout html (CART01) is its saved page, whose
    lines are numbered as the cart reader (aid/readers/cart.py) numbers them, an article row being its cells joined by " | ".
    The document of a case of layout png (CART02) is its screenshot (cart_rows), whose lines the prompt asks the model to
    count; html=True reads such a case from its saved page instead. The page and the screenshot show the same lines in the
    same order, so a value stands on the same line number in both. (references.csv names the r-th article row of the saved
    page instead.) `kind` is the kind of the Document; the content is whatever the case holds.
    """
    spec = MadeUpSpec.model_validate_json((SPEC_DIR / f"{case_id}.json").read_text(encoding="utf-8"))
    return _confirmation(spec, kind) if spec.confirmation else _cart(spec, kind, html)


def po_number_of(case_id: str) -> str:
    """The PO number of the made-up case `case_id`, as its spec (aid/madeup/spec/<case_id>.json) gives it."""
    return MadeUpSpec.model_validate_json((SPEC_DIR / f"{case_id}.json").read_text(encoding="utf-8")).po.po_number


def answer_from_spec(case_id: str, kind: Literal["confirmation", "cart"] = "confirmation") -> str:
    """The answer a perfect model gives for the made-up case: document_from_spec in the reading schema of the LLM reader
    (each value's text, source and mark, without the value) with the language of the document."""
    spec = MadeUpSpec.model_validate_json((SPEC_DIR / f"{case_id}.json").read_text(encoding="utf-8"))
    language = spec.confirmation.language if spec.confirmation else "nl"      # the made-up cart pages are Dutch
    return Reading.model_validate(document_from_spec(case_id, kind).model_dump() | {"language": language}).model_dump_json()


def _citing(pages: list[list[Row]]) -> Callable[[str, str, str | None], DocValue]:
    """A function that gives a value printed on the row tagged `ref` of the pages, citing that row's page, line and text
    (joined as extract_lines joins a line); a value not printed is DocValue() and needs no row (MU20's Incoterm)."""
    where = row_positions(pages)
    texts = {row.ref: " ".join(text.text for text in row.texts) for page in pages for row in page if row.ref}

    def cited(ref: str, text: str, value: str | None) -> DocValue:
        if not text:
            return DocValue()
        page, line = where[ref]
        return DocValue(value=value, text=text, source=SourceRef(page=page, line=line, text=texts[ref]))
    return cited


def _confirmation(spec: MadeUpSpec, kind: str) -> Document:
    conf, po, language = spec.confirmation, spec.po, spec.confirmation.language
    pages = confirmation_pages(spec)
    cited = _citing(pages)
    version = "" if conf.version_quoted is None else str(conf.version_quoted)
    header = {"supplier": cited("supplier", po.supplier, po.supplier),
              "po_reference": cited("po_reference", po.po_number, po.po_number),
              "version_hint": cited("po_reference", version, version),          # "Uw inkooporder 250103, versie 1"
              "document_date": cited("document_date", day_text(conf.document_date, language), conf.document_date.isoformat()),
              "currency": cited("currency", po.currency, po.currency),
              "incoterm": cited("incoterm", conf.incoterm or "", conf.incoterm)}
    lines = []
    for line in conf.lines:
        texts = {field: cell_text(line, field, language) for field in _LINE_FIELDS}
        if conf.layout == "text_en":
            texts["price_basis"] = basis_words(line)                              # the prose says "each" or "per 100"
        elif not texts["price_basis"] and line.price_basis is not None:
            texts["price_basis"] = texts["unit_price"].partition("/")[2].strip()   # printed in the price: "100 st" of "12,50 / 100 st"
        lines.append(DocLine(line_id=line.line_id, **{field: cited(f"line {line.line_id}", texts[field], getattr(line, field, None))
                                                      for field in _LINE_FIELDS}))
    return Document(kind=kind, lines=lines, source_file=f"{spec.case_id}_conf.pdf", pages=len(pages), reader="spec", **header)


def _cart(spec: MadeUpSpec, kind: str, html: bool) -> Document:
    cart, po = spec.cart, spec.po
    html = html or cart.layout == "html"
    table, _ = cart_table(spec)                                   # the article rows as the shop prints them, in CART_COLUMNS order
    cited = _citing([_page_rows(spec, table) if html else cart_rows(spec)])
    lines = []
    for line, cells in zip(cart.lines, table):
        printed = dict(zip((column.field for column in CART_COLUMNS), cells))
        lines.append(DocLine(line_id=line.line_id, **{field: cited(f"line {line.line_id}", printed.get(field, ""), getattr(line, field, None))
                                                      for field in _LINE_FIELDS}))
    header = {"supplier": cited("supplier", po.supplier, po.supplier),
              "po_reference": cited("po_reference", po.po_number, po.po_number),
              "capture_date": cited("capture_date", day_text(cart.capture_date, "nl"), cart.capture_date.isoformat()),
              "currency": cited("currency", po.currency, po.currency)}
    source_file = f"{spec.case_id}.html" if html else f"{spec.case_id}.png"
    return Document(kind=kind, lines=lines, source_file=source_file, pages=1, reader="spec", **header)


def _page_rows(spec: MadeUpSpec, table: list[list[str]]) -> list[Row]:
    """The lines of the saved cart page that a value can cite: the rows of the screenshot (cart_rows), which show the blocks of
    the page in the same order, with the text of each article row as the page's table row gives it, its cells joined by " | "."""
    joined = {f"line {line.line_id}": " | ".join(cells) for line, cells in zip(spec.cart.lines, table)}
    return [Row([Text(0, joined[row.ref])], ref=row.ref) if row.ref in joined else row for row in cart_rows(spec)]


def newer_po(path: Path, case_id: str, number: int, prices: dict[str, str], codes: dict[str, str] | None = None) -> Path:
    """A newer print of the made-up PO of `case_id`, drawn by the generator into `path` as Exact prints an order again after a change:
    its first version as version `number`, with the unit price of each line that `prices` names (line id -> price, two decimals)
    and the item code of each line that `codes` names (line id -> item code)."""
    spec = MadeUpSpec.model_validate_json((SPEC_DIR / f"{case_id}.json").read_text(encoding="utf-8"))
    first = spec.po.versions[0]
    lines = [line.model_copy(update={"unit_price": prices.get(line.line_id, line.unit_price),
                                     "item_code": (codes or {}).get(line.line_id, line.item_code)}) for line in first.lines]
    version = first.model_copy(update={"version": number, "lines": lines})
    path.write_bytes(generate.pdf_bytes(generate.po_pages(spec, version), path.name))
    return path


def filed_on(store, po_number: str, look: str, when) -> None:
    """Set when the version ("v2") or the look ("e1") of the PO was filed, as PoStore.filed_at reads it: the time of its po.json."""
    stamp = when.timestamp()
    os.utime(store.root / po_number / look / "po.json", (stamp, stamp))


def given_on(store, po_number: str, look: str, when) -> None:
    """Set when the store was last given a print that shows the PO as the version ("v2") or the look ("e1") holds it, as
    PoStore.given_at reads it: the time of the given.txt that the store left in that folder when such a print came again. The
    store must have left one: a test that sets the time shows by it that the print was noted."""
    path, stamp = store.root / po_number / look / "given.txt", when.timestamp()
    assert path.exists(), f"the store noted no print of {look} given again"
    os.utime(path, (stamp, stamp))


def confirmation_again(path: Path, case_id: str, prices: dict[str, str] | None = None, quoted: int | None = None) -> Path:
    """Another confirmation of the made-up PO of `case_id`, drawn by the generator into `path` as its supplier would send one again:
    the case's own confirmation with the unit price of each line that `prices` names (line id -> price, two decimals), quoting the
    version `quoted` of the PO when one is given. Its title is the name of its file, so that no two of them hold the same bytes."""
    spec = MadeUpSpec.model_validate_json((SPEC_DIR / f"{case_id}.json").read_text(encoding="utf-8"))
    lines = [line.model_copy(update={"unit_price": (prices or {}).get(line.line_id, line.unit_price)}) for line in spec.confirmation.lines]
    again = spec.confirmation.model_copy(update={"lines": lines} | ({"version_quoted": quoted} if quoted is not None else {}))
    path.write_bytes(generate.pdf_bytes(generate.confirmation_pages(spec.model_copy(update={"confirmation": again})), path.name))
    return path


# --- a local page and a proxy that cannot be reached (Task 30: no proxy stands between the aid and its own pages) ---

@contextmanager
def local_page() -> Iterator[str]:
    """The address of a page that answers 200 on 127.0.0.1, on a free port, for the time of the block."""
    class Answer(BaseHTTPRequestHandler):
        def do_GET(self):                         # noqa: N802 (the name http.server calls)
            body = b"made-up page"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_HEAD = do_GET

        def log_message(self, *args):            # no lines on stderr
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Answer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def unreachable_proxy() -> str:
    """The address of a proxy on this PC that nothing listens on, as a proxy of the PC that cannot reach this PC's own 127.0.0.1
    looks to a request that goes through it: every request through it fails."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def without_proxies(monkeypatch) -> None:
    """Take every proxy variable out of the environment for the test (the PC that runs the suite may have its own); monkeypatch
    puts them back afterwards."""
    for name in [name for name in os.environ if name.lower().endswith("_proxy")]:
        monkeypatch.delenv(name)


def cases_for_the_count(cases_root: Path, madeup_out: Path, full) -> list[str]:
    """Seven confirmations under cases_root for the count of the cases that wait for their purchase order (ruling N10), and the ids of
    the two that wait: MU01 and MU02 run while the store does not hold their POs. Besides them: a closed confirmation (MU01 against its
    PO), a replaced one and a decided one (each waited for its PO first), MU07 in review for another reason, and MU01 opened without a
    PO number, whose result waits by waits_for_po all the same. `full` are the Deps of a store that holds every made-up PO."""
    from datetime import datetime

    from aid.case import open_case
    from aid.pipeline import run_case
    from aid.po_store import PoStore
    from aid.review.app import decide
    from aid.review.close import finish_replaced

    empty = full.model_copy(update={"store": PoStore(Path(cases_root).parent / "count_empty_store")})

    def ran(name: str, po_number: str | None, minute: int, deps):
        case = open_case(cases_root, "confirmation", [Path(madeup_out) / f"{name}_conf.pdf"], po_number, datetime(2026, 11, 12, 9, minute))
        result = run_case(case, deps)
        return case, result

    waiting = [ran("MU01", "250101", 0, empty)[0], ran("MU02", "250102", 1, empty)[0]]
    _, closed = ran("MU01", "250101", 2, full)
    replaced, _ = ran("MU01", "250101", 3, empty)
    finish_replaced(replaced, "250101_20261112T091000", datetime(2026, 11, 12, 9, 10), "nl")
    decided, _ = ran("MU02", "250102", 4, empty)
    decide(decided, "RI1", "unresolved")
    _, other = ran("MU07", "250107", 5, full)
    numberless, _ = ran("MU01", None, 6, empty)
    assert closed.status == "closed" and other.status == "review" and other.version.version is not None and numberless.po_number is None
    return [case.case_id for case in waiting]


def shown_headings(block) -> list[str]:
    """The headings a block of the review page shows, top to bottom, each as its line of Markdown ("## Laatst verwerkt"): the lines
    that begin with one to six # and a space, of every Markdown inside the block, and none of a Markdown that is hidden or that
    stands inside a hidden block."""
    if getattr(block, "visible", True) in (False, "hidden"):
        return []
    own = str(block.value or "").splitlines() if type(block).__name__ == "Markdown" else []
    return ([line for line in own if re.match(r"#{1,6} ", line)]
            + [found for child in getattr(block, "children", []) for found in shown_headings(child)])


def skipped_headings(headings: list[str]) -> list[str]:
    """Those of these headings (as shown_headings gives them) that skip a level, the page's one h1 standing before the first: an h3
    right after the h1, an h4 right after an h2, and so on; and a second h1."""
    skipped, level = [], 1
    for heading in headings:
        depth = len(heading) - len(heading.lstrip("#"))
        if depth == 1 or depth > level + 1:
            skipped.append(heading)
        level = depth
    return skipped
