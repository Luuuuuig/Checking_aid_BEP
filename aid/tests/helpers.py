"""Test helpers shared by the tests of the checking aid. Each task adds the helpers its own tests name."""
import os
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal

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
